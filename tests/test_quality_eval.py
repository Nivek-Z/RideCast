import copy
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_quality.py"
SPEC = importlib.util.spec_from_file_location("evaluate_quality", MODULE_PATH)
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


def predictions():
    origin = pd.Timestamp("2026-03-01T00:00:00Z")
    return pd.DataFrame([
        {"forecast_origin": origin, "timestamp": origin, "zone_id": zone, "horizon": 1,
         "actual_demand": actual, "predicted_demand": estimate, "lower": 5, "upper": 25}
        for zone, actual, estimate in (("A", 10, 12), ("B", 20, 18))
    ])


def test_preregistered_suite_has_new_seeds_and_fixed_equal_budget():
    cases = quality.final_cases()
    assert len(cases) == len({c["case_id"] for c in cases}) == 10
    assert {c["seed"] for c in cases}.isdisjoint({7, 42, 99, 11, 23})
    assert all(c["days"] == 45 and c["start"] == quality.START for c in cases)
    pressure = [c for c in cases if c["scenario_name"] == "pressure"]
    assert all(c["scenario"]["supply_multiplier"] == .8 for c in pressure)
    assert all(c["scenario"]["events"][0]["start"].startswith("2026-04-12") for c in pressure)
    assert quality.CONFIG["epochs"] == 30 and quality.CONFIG["tune"] is False
    assert quality.build_parser().parse_args([]).run is False
    # A caller cannot accidentally mutate the next run's protocol.
    cases[0]["seed"] = 7
    assert quality.final_cases()[0]["seed"] == 101
    assert quality.protocol_hash() == quality.protocol_hash()
    payload = quality.protocol_payload()
    payload["v2_overrides"]["loss"] = "huber"
    assert quality.protocol_payload()["v2_overrides"]["loss"] == "poisson"


def test_round_parameter_preserves_first_protocol_and_does_not_access_final_data(monkeypatch, capsys):
    assert quality.protocol_hash("round1") == "3608b9095abeb813c06051b8ffa711e6cfae06bba88a010a4d80f7398e795ee1"
    first = quality.final_cases("round1")
    second = quality.final_cases("round2")
    assert len(second) == 10
    assert {case["seed"] for case in second} == {606, 707, 808, 909, 1001}
    assert {case["seed"] for case in first}.isdisjoint({case["seed"] for case in second})
    assert all(case["start"] == "2026-06-01T00:00:00+08:00" for case in second)
    assert all(case["scenario"]["events"][0]["start"].startswith("2026-07-13")
               for case in second if case["scenario_name"] == "pressure")
    assert quality.protocol_payload("round1")["quality_targets"] == quality.protocol_payload("round2")["quality_targets"]
    assert quality.protocol_hash("round2") != quality.protocol_hash("round1")

    def forbidden(*args, **kwargs):
        raise AssertionError("Listing a protocol must not run training or access held-out data")

    monkeypatch.setattr(quality, "run", forbidden)
    assert quality.main(["--protocol", "round2", "--list-cases"]) == 0
    assert '"quality-round2"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        quality.build_parser().parse_args(["--run", "--list-cases"])


def test_metrics_are_count_scale_and_zero_denominators_remain_unknown():
    values = quality.metric_values([0, 10], [0, 8])
    assert values["mae"] == 1
    assert values["wape"] == 20
    assert values["bias"] == -1
    assert values["bias_percent"] == -20
    empty_actual = quality.metric_values([0, 0], [0, 1])
    assert empty_actual["wape"] is None and empty_actual["bias_percent"] is None
    with pytest.raises(ValueError):
        quality.metric_values([], [])
    with pytest.raises(ValueError):
        quality.metric_values([1], [float("nan")])


def test_comparison_never_intersects_away_difficult_or_changed_truth_rows():
    old = predictions()
    new = old.iloc[::-1].copy()
    left, right = quality.align_predictions(old, new)
    assert left[quality.KEYS].equals(right[quality.KEYS])
    with pytest.raises(ValueError, match="sets differ"):
        quality.align_predictions(old, new.iloc[:1])
    new.loc[new.index[0], "actual_demand"] += 1
    with pytest.raises(ValueError, match="truth differs"):
        quality.align_predictions(old, new)
    invalid = old.copy()
    invalid.loc[0, "upper"] = 1
    with pytest.raises(ValueError, match="contain"):
        quality.validate_predictions(invalid)
    extra = old.copy()
    extra["neural_demand"] = [11.0, 19.0]
    assert "neural_demand" in quality.validate_predictions(extra)
    extra.loc[0, "neural_demand"] = np.inf
    with pytest.raises(ValueError, match="neural_demand"):
        quality.validate_predictions(extra)


def test_fixed_partition_and_calibration_cut_are_audited_without_training():
    start = pd.Timestamp(quality.START)
    times = pd.date_range(start, periods=1080, freq="h")
    rows = pd.DataFrame([
        {"forecast_origin": times[origin], "timestamp": times[origin + h - 1], "zone_id": zone,
         "horizon": h, "actual_demand": 1, "predicted_demand": 1, "lower": 0, "upper": 2}
        for origin in range(918, 1078) for zone in ("A", "B") for h in (1, 2, 3)
    ])
    split = {name: {"target_start": times[left].isoformat(),
                    "target_end_exclusive": (times[right - 1] + pd.Timedelta(hours=1)).isoformat()}
             for name, (left, right) in {"train": (24, 756), "validation": (756, 918), "test": (918, 1080)}.items()}
    old = {"config": dict(quality.CONFIG), "split": split}
    new = {"config": {**quality.CONFIG, "loss": "poisson"}, "split": copy.deepcopy(split),
           "validation_subsplit": {"independent_calibration": True, "calibration_start": times[861].isoformat(),
                                   "selection_windows": 206, "calibration_windows": 110},
           "blending": {"neural_weight": 0}}
    manifest = {"observation_start": start.isoformat(),
                "observation_end": (times[-1] + pd.Timedelta(hours=1)).isoformat(),
                "bucket_minutes": 60, "zone_count": 2}
    checks = quality.validate_run_contract(old, new, rows, manifest)
    assert checks["test_unit_count"] == 960
    new["validation_subsplit"]["calibration_windows"] += 2
    with pytest.raises(ValueError, match="calibration"):
        quality.validate_run_contract(old, new, rows, manifest)


def test_baselines_do_not_see_target_data_even_when_horizon_exceeds_daily_period():
    times = pd.date_range("2026-03-01T00:00:00Z", periods=144, freq="h")
    panel = pd.DataFrame({"timestamp": times, "zone_id": "A", "demand": np.arange(len(times))})
    origin_index = 74
    rows = pd.DataFrame([
        {"forecast_origin": times[origin_index], "timestamp": times[origin_index + horizon - 1],
         "zone_id": "A", "horizon": horizon}
        for horizon in (1, 3, 30)
    ])
    before = quality.naive_predictions(rows, panel, 60)
    changed = panel.copy()
    changed.loc[changed.timestamp >= times[origin_index], "demand"] = 99999
    after = quality.naive_predictions(rows, changed, 60)
    for name in quality.BASELINES:
        np.testing.assert_array_equal(before[name], after[name])
        assert (before[name] < origin_index).all()
    assert before["previous_day"][-1] == 55
    assert (before["last_value"] == 73).all()


def test_city_metrics_aggregate_regions_at_the_same_origin_and_horizon():
    rows = predictions()
    result = quality.evaluation_groups(rows, rows["predicted_demand"], "Asia/Shanghai")
    assert result["overall"]["mae"] == 2
    assert result["city_total"]["mae"] == 0
    assert result["city_total"]["sample_count"] == 1
    assert result["peak"]["sample_count"] == 2
    assert result["off_peak"] is None
    interval = quality.interval_metrics(rows)
    assert interval["coverage"] == 1
    assert interval["mean_width"] == 20


def test_acceptance_gate_preserves_failed_and_regressed_cases():
    evaluation = {name: {"overall": {"mae": mae, "rmse": mae, "wape": 20,
                                      "smape": 20, "bias": 0, "bias_percent": 0}}
                  for name, mae in (("v1", 4), ("v2", 3), ("selected_baseline", 3.5),
                                    ("last_value", 6), ("previous_day", 3.5), ("previous_week", 5))}
    cases = [{"case_id": f"case_{i}", "status": "succeeded", "source_unchanged": True,
              "evaluation": copy.deepcopy(evaluation), "mae_change_vs_v1_percent": -25} for i in range(10)]
    summary = quality.summarize_cases(cases)
    assert summary["quality_target_passed"] is True
    assert summary["mean_mae_improvement_vs_v1_percent"] == 25
    cases[0]["mae_change_vs_v1_percent"] = 6
    assert quality.summarize_cases(cases)["quality_target_passed"] is False
    cases[0] = {"case_id": "failed", "status": "failed", "error": "training failed", "source_unchanged": True}
    failed = quality.summarize_cases(cases)
    assert failed["failed"] == 1 and failed["quality_target_passed"] is False
    assert failed["gates"]["all_cases_successful"] is False
