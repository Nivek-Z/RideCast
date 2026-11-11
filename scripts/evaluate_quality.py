#!/usr/bin/env python3
"""Run the preregistered, equal-budget v1/v2 demand-model acceptance study.

The default invocation only prints the protocol. --run deliberately generates
previously unseen final worlds, trains both models and reports held-out tests.
Do not use final results to continue tuning. See docs/MODEL_QUALITY_PROTOCOL.md.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROTOCOL_VERSION = "quality-v1"
FINAL_SEEDS = (101, 202, 303, 404, 505)
START = "2026-03-01T00:00:00+08:00"
DAYS = 45
CONFIG = {"lookback": 24, "horizon": 3, "units": 32, "batch_size": 128,
          "learning_rate": 0.001, "epochs": 30, "seed": 42, "tune": False}
V2_OVERRIDES = {"loss": "poisson"}
QUALITY_TARGETS = {"minimum_mean_mae_improvement_percent": 15,
                   "minimum_cases_beating_naive": 8, "maximum_case_mae_regression_percent": 5}
KEYS = ["forecast_origin", "timestamp", "zone_id", "horizon"]
BASELINES = ("last_value", "previous_day", "previous_week")


def final_cases() -> list[dict]:
    cases = []
    for seed in FINAL_SEEDS:
        for scenario_name in ("normal", "pressure"):
            scenario = {}
            if scenario_name == "pressure":
                scenario = {"rain_multiplier": 2, "traffic_multiplier": 1.3, "supply_multiplier": 0.8,
                            "events": [{"name": "预先公告的质量验收活动", "zone_id": "z_000",
                                        "start": "2026-04-12T18:00:00+08:00",
                                        "end": "2026-04-12T22:00:00+08:00", "intensity": 1.5}]}
            cases.append({"case_id": f"{scenario_name}_seed_{seed}", "seed": seed,
                          "scenario_name": scenario_name, "scenario": scenario,
                          "start": START, "days": DAYS, "rows": 3, "cols": 3, "bucket_minutes": 60})
    return cases


def protocol_payload() -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "config": copy.deepcopy(CONFIG),
            "v2_overrides": dict(V2_OVERRIDES), "quality_targets": dict(QUALITY_TARGETS),
            "v2_validation_policy": {"selection_fraction": 0.65, "calibration_fraction": 0.35,
                                     "neural_weights": [0, 0.25, 0.5, 0.75, 1]},
            "cases": final_cases()}


def protocol_hash() -> str:
    canonical = json.dumps(protocol_payload(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def metric_values(actual, predicted) -> dict:
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if actual.shape != predicted.shape or actual.size == 0:
        raise ValueError("Metric arrays must be nonempty and identically shaped")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all() or (actual < 0).any() or (predicted < 0).any():
        raise ValueError("Demand and predictions must be finite and nonnegative")
    error = predicted - actual
    denominator = np.abs(actual) + np.abs(predicted)
    smape = np.divide(2 * np.abs(error), denominator, out=np.zeros_like(error), where=denominator > 0)
    total = float(actual.sum())
    return {"mae": float(np.abs(error).mean()), "rmse": float(np.sqrt(np.square(error).mean())),
            "wape": float(np.abs(error).sum() / total * 100) if total else None,
            "smape": float(smape.mean() * 100), "bias": float(error.mean()),
            "bias_percent": float(error.sum() / total * 100) if total else None,
            "actual_total": total, "predicted_total": float(predicted.sum()), "sample_count": int(actual.size)}


def validate_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    required = {*KEYS, "actual_demand", "predicted_demand", "lower", "upper"}
    if required - set(frame):
        raise ValueError(f"Predictions missing columns: {sorted(required - set(frame))}")
    frame = frame.copy()
    for column in ("forecast_origin", "timestamp"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
    frame["zone_id"] = frame["zone_id"].astype(str)
    if frame.empty or frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
        raise ValueError("Prediction keys must be nonempty, unique and nonnull")
    numeric = ["actual_demand", "predicted_demand", "lower", "upper", "horizon"]
    numeric += [column for column in ("neural_demand", "baseline_demand") if column in frame]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column]).all() or (frame[column] < 0).any():
            raise ValueError(f"Invalid prediction column {column}")
    if (frame["horizon"] < 1).any() or not np.equal(frame["horizon"], np.floor(frame["horizon"])).all():
        raise ValueError("horizon must be a positive integer")
    if ((frame["lower"] > frame["predicted_demand"]) | (frame["predicted_demand"] > frame["upper"])).any():
        raise ValueError("Prediction interval must contain its point forecast")
    if (frame["timestamp"] < frame["forecast_origin"]).any():
        raise ValueError("Targets must not precede forecast origin")
    return frame.sort_values(KEYS).reset_index(drop=True)


def align_predictions(old: pd.DataFrame, new: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    old, new = validate_predictions(old), validate_predictions(new)
    if not old[KEYS].equals(new[KEYS]):
        raise ValueError("v1/v2 prediction-unit sets differ; never discard difficult unmatched rows")
    if not np.array_equal(old["actual_demand"].to_numpy(), new["actual_demand"].to_numpy()):
        raise ValueError("v1/v2 ground truth differs")
    return old, new


def validate_run_contract(old_meta: dict, new_meta: dict, rows: pd.DataFrame, manifest: dict) -> dict:
    """Audit fixed budget, chronological cuts and independent v2 calibration."""
    start, end = pd.Timestamp(manifest["observation_start"]), pd.Timestamp(manifest["observation_end"])
    step = pd.Timedelta(minutes=int(manifest["bucket_minutes"]))
    count = int((end - start) / step)
    train_end, validation_end = int(count * 0.7), int(count * 0.85)
    calibration_start = train_end + int((validation_end - train_end) * 0.65)
    horizon = CONFIG["horizon"]
    zones = int(manifest["zone_count"])
    expected = {"train": (start + CONFIG["lookback"] * step, start + train_end * step),
                "validation": (start + train_end * step, start + validation_end * step),
                "test": (start + validation_end * step, end)}
    for name, metadata in (("v1", old_meta), ("v2", new_meta)):
        if any(metadata["config"].get(key) != value for key, value in CONFIG.items()):
            raise ValueError(f"{name} trained with a different preregistered budget/config")
        for split, (left, right) in expected.items():
            boundaries = metadata["split"][split]
            if (pd.Timestamp(boundaries["target_start"]) != left or
                    pd.Timestamp(boundaries["target_end_exclusive"]) != right):
                raise ValueError(f"{name} {split} partition differs from preregistered chronology")
    if any(new_meta["config"].get(key) != value for key, value in V2_OVERRIDES.items()):
        raise ValueError("v2 count loss differs from preregistered configuration")
    expected_units = (count - validation_end - horizon + 1) * zones * horizon
    if len(rows) != expected_units or rows["forecast_origin"].min() != expected["test"][0]:
        raise ValueError("Test prediction units do not fully cover the registered test partition")
    if rows["forecast_origin"].max() != end - horizon * step or rows["timestamp"].max() != end - step:
        raise ValueError("Test prediction units end outside registered window")
    subsplit = new_meta["validation_subsplit"]
    selection_origins = max(0, calibration_start - train_end - horizon + 1)
    calibration_origins = max(0, validation_end - calibration_start - horizon + 1)
    if (not subsplit["independent_calibration"] or
            pd.Timestamp(subsplit["calibration_start"]) != start + calibration_start * step or
            subsplit["selection_windows"] != selection_origins * zones or
            subsplit["calibration_windows"] != calibration_origins * zones):
        raise ValueError("v2 calibration overlaps selection, or chronological subpartition counts differ")
    blending = new_meta["blending"]
    if blending["neural_weight"] not in (0, 0.25, 0.5, 0.75, 1):
        raise ValueError("v2 blend weight was not selected from the preregistered validation grid")
    return {"same_core_budget": True, "same_target_cuts": True, "test_unit_count": expected_units,
            "calibration_start": (start + calibration_start * step).isoformat(),
            "selection_windows": selection_origins * zones,
            "calibration_windows": calibration_origins * zones, "independent_calibration": True}


def naive_predictions(rows: pd.DataFrame, panel: pd.DataFrame, bucket_minutes: int) -> dict[str, np.ndarray]:
    """Recompute every comparator using only observations before each origin."""
    panel = panel.copy()
    panel["timestamp"] = pd.to_datetime(panel["timestamp"], utc=True)
    panel["zone_id"] = panel["zone_id"].astype(str)
    matrix = panel.pivot(index="timestamp", columns="zone_id", values="demand").sort_index()
    values = matrix.to_numpy(dtype=float)
    if not np.isfinite(values).all() or matrix.empty:
        raise ValueError("Baselines require a complete finite panel")
    origins = matrix.index.get_indexer(pd.to_datetime(rows["forecast_origin"], utc=True))
    targets = matrix.index.get_indexer(pd.to_datetime(rows["timestamp"], utc=True))
    zones = matrix.columns.get_indexer(rows["zone_id"].astype(str))
    if (origins < 1).any() or (targets < 0).any() or (zones < 0).any():
        raise ValueError("Baseline prediction keys outside the observed panel")
    if not np.array_equal(targets - origins, rows["horizon"].to_numpy() - 1):
        raise ValueError("horizon and target timestamp disagree")
    step = pd.Timedelta(minutes=bucket_minutes)
    if not ((matrix.index[1:] - matrix.index[:-1]) == step).all():
        raise ValueError("Panel is not regularly spaced")
    day = int(pd.Timedelta(days=1) / step)
    result = {}
    for name, period in (("last_value", 0), ("previous_day", day), ("previous_week", day * 7)):
        if period == 0:
            indices = origins - 1
        else:
            indices = targets - period
            # Multi-step horizons shorter than a seasonal period use the usual
            # lag. Larger horizons repeat the last available seasonal cycle.
            shifts = np.maximum(0, np.floor_divide(indices - origins, period) + 1)
            indices -= shifts * period
            indices = np.where(indices < 0, origins - 1, indices)
        if (indices >= origins).any():
            raise ValueError("A seasonal baseline read future observations")
        result[name] = values[indices, zones]
    return result


def evaluation_groups(rows: pd.DataFrame, predicted, timezone: str) -> dict:
    frame = rows[KEYS + ["actual_demand"]].copy()
    frame["prediction"] = np.asarray(predicted, dtype=float)
    result = {"overall": metric_values(frame["actual_demand"], frame["prediction"])}
    for key in ("zone_id", "horizon"):
        result[f"by_{key}"] = [{key: value if key == "zone_id" else int(value),
                                 **metric_values(group["actual_demand"], group["prediction"])}
                                for value, group in frame.groupby(key, sort=True)]
    hours = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone).dt.hour
    peak = ((hours >= 7) & (hours < 10)) | ((hours >= 17) & (hours < 21))
    result["peak"] = metric_values(frame.loc[peak, "actual_demand"], frame.loc[peak, "prediction"]) if peak.any() else None
    result["off_peak"] = metric_values(frame.loc[~peak, "actual_demand"], frame.loc[~peak, "prediction"]) if (~peak).any() else None
    city = frame.groupby(["forecast_origin", "timestamp", "horizon"], sort=True)[["actual_demand", "prediction"]].sum()
    result["city_total"] = metric_values(city["actual_demand"], city["prediction"])
    return result


def interval_metrics(rows: pd.DataFrame) -> dict:
    covered = (rows["actual_demand"] >= rows["lower"]) & (rows["actual_demand"] <= rows["upper"])
    width = rows["upper"] - rows["lower"]
    return {"coverage": float(covered.mean()), "mean_width": float(width.mean()),
            "by_horizon": [{"horizon": int(h), "coverage": float(covered.loc[g.index].mean()),
                            "mean_width": float(width.loc[g.index].mean())}
                           for h, g in rows.groupby("horizon", sort=True)]}


def summarize_cases(cases: list[dict], expected_count: int = 10) -> dict:
    successful = [case for case in cases if case.get("status") == "succeeded"]
    complete = len(cases) == expected_count and len(successful) == expected_count
    means = {}
    for model in ("v1", "v2", "selected_baseline", *BASELINES):
        rows = [case["evaluation"][model]["overall"] for case in successful]
        means[model] = {metric: statistics.fmean(row[metric] for row in rows if row[metric] is not None)
                       if any(row[metric] is not None for row in rows) else None
                       for metric in ("mae", "rmse", "wape", "smape", "bias", "bias_percent")}
    old, new = means["v1"]["mae"], means["v2"]["mae"]
    improvement = (old - new) / old * 100 if old else None
    wins = sum(case["evaluation"]["v2"]["overall"]["mae"] <
               case["evaluation"]["selected_baseline"]["overall"]["mae"] for case in successful)
    worst_regression = max((case["mae_change_vs_v1_percent"] for case in successful), default=None)
    gates = {"all_cases_successful": complete,
             "mean_mae_improvement_at_least_15_percent": improvement is not None and improvement >= QUALITY_TARGETS["minimum_mean_mae_improvement_percent"],
             "at_least_8_of_10_beat_validation_selected_naive": wins >= QUALITY_TARGETS["minimum_cases_beating_naive"],
             "no_case_mae_regression_above_5_percent": worst_regression is not None and worst_regression <= QUALITY_TARGETS["maximum_case_mae_regression_percent"],
             "frozen_sources_unchanged": all(case.get("source_unchanged", False) for case in cases) and complete}
    neural_metrics = [case["evaluation"]["v2_neural"]["overall"] for case in successful
                      if "v2_neural" in case["evaluation"]]
    deployments = [case.get("models", {}).get("v2", {}).get("blending", {}) for case in successful]
    return {"case_count": len(cases), "expected_case_count": expected_count, "succeeded": len(successful),
            "failed": len(cases) - len(successful), "equal_weight_case_means": means,
            "mean_mae_improvement_vs_v1_percent": improvement, "cases_beating_selected_baseline": wins,
            "worst_case_mae_regression_percent": worst_regression, "gates": gates,
            "v2_pure_neural_mean_mae": statistics.fmean(row["mae"] for row in neural_metrics) if neural_metrics else None,
            "deployment_counts": {name: sum(item.get("deployment") == name for item in deployments)
                                  for name in ("neural", "neural_seasonal_blend", "seasonal_fallback")},
            "neural_weights": [{"case_id": case["case_id"],
                                "neural_weight": case.get("models", {}).get("v2", {}).get("blending", {}).get("neural_weight")}
                               for case in successful],
            "quality_target_passed": all(gates.values()), "aggregation": "equal weight per case, not pooled demand"}


def _fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = [root / "src" / "ridecast" / name for name in (
        "simulation.py", "data.py", "features.py", "ml.py", "legacy_features.py", "legacy_ml.py", "seasonal.py")]
    paths += [Path(__file__).resolve(), root / "docs" / "MODEL_QUALITY_PROTOCOL.md"]
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _latency(forecast_function, model_dir, dataset_dir) -> dict:
    elapsed = []
    for _ in range(4):
        started = time.perf_counter()
        forecast_function(model_dir, dataset_dir)
        elapsed.append((time.perf_counter() - started) * 1000)
    return {"cold_ms": elapsed[0], "warm_ms": elapsed[1:], "warm_median_ms": statistics.median(elapsed[1:]),
            "scope": "full forecast call including file read, features and model; sequential local process"}


def render_markdown(report: dict) -> str:
    lines = ["# 模型质量升级：独立预登记验收", "", f"协议 `{PROTOCOL_VERSION}`；运行时间 {report['created_at']}。",
             "", "所有case使用同一原始面板、同预测单元和相等最大30轮预算；每世界分别重训。"
             "结果是合成世界的时间留出评价，不是实际城市精度或零样本迁移保证。", "",
             "v2指实际服务的验证集选定神经/季节融合结果；纯神经网络指标单列，权重0时明确标为季节回退。", "",
             "| case | v1 MAE | v2服务 MAE | v2纯神经 MAE | 神经权重 | 验证选定基线 MAE | v2 WAPE | 状态 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]

    def number(value):
        return "—" if value is None else f"{value:.4f}"

    for case in report["cases"]:
        evaluation = case.get("evaluation", {})
        old = evaluation.get("v1", {}).get("overall", {})
        new = evaluation.get("v2", {}).get("overall", {})
        base = evaluation.get("selected_baseline", {}).get("overall", {})
        neural = evaluation.get("v2_neural", {}).get("overall", {})
        blend = case.get("models", {}).get("v2", {}).get("blending", {})
        lines.append(f"| {case['case_id']} | {number(old.get('mae'))} | {number(new.get('mae'))} | "
                     f"{number(neural.get('mae'))} | {number(blend.get('neural_weight'))} | "
                     f"{number(base.get('mae'))} | {number(new.get('wape'))} | {case['status']} |")
    summary = report["summary"]
    lines += ["", f"相对等预算v1的case等权平均MAE改善：{number(summary['mean_mae_improvement_vs_v1_percent'])}%。",
              f"优于验证集选定朴素基线：{summary['cases_beating_selected_baseline']}/10；"
              f"最差case相对v1误差变化：{number(summary['worst_case_mae_regression_percent'])}%。",
              f"纯神经网络等权平均MAE：{number(summary['v2_pure_neural_mean_mae'])}；"
              f"服务部署形式：{summary['deployment_counts']}。",
              "", "| 预登记目标 | 结果 |", "| --- | --- |"]
    lines += [f"| {name} | {'通过' if passed else '未通过'} |" for name, passed in summary["gates"].items()]
    lines += ["", f"质量目标整体：**{'通过' if summary['quality_target_passed'] else '未通过'}**。"
              "门槛未因测试结果改变。完整区域/步长/高峰/城市/偏差/区间/时延/全部基线见results.json。",
              "", "WAPE和bias%已经乘100；城市误差先在相同预测起点和目标时刻汇总区域；"
              "不把重叠目标当作互相独立的新订单总量。区间为经验区间，不保证覆盖。"]
    for case in report["cases"]:
        if case.get("error"):
            lines += ["", f"{case['case_id']} 失败：`{case['error']}`"]
    return "\n".join(lines) + "\n"


def run(output: Path) -> dict:
    from ridecast import legacy_ml, ml
    from ridecast.data import write_dataset
    from ridecast.simulation import create_world, simulate

    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output must be a new or empty directory; never overwrite acceptance artifacts")
    output.mkdir(parents=True, exist_ok=True)
    fingerprints = _fingerprints()
    report = {"schema_version": "1.0", "protocol_version": PROTOCOL_VERSION,
              "protocol_sha256": protocol_hash(), "protocol": protocol_payload(),
              "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "config": copy.deepcopy(CONFIG),
              "cases": [], "source_sha256_at_start": fingerprints,
              "test_access_policy": "Final suite evaluated after development freeze; do not tune on these results"}
    for specification in final_cases():
        case = {**specification, "status": "running"}
        started = time.perf_counter()
        root = output / specification["case_id"]
        root.mkdir()
        print(f"[{specification['case_id']}] generating immutable observations", file=sys.stderr, flush=True)
        try:
            world = create_world(name="预登记质量验收城市", rows=3, cols=3, seed=specification["seed"])
            panel, orders, data_meta = simulate(world, START, DAYS, specification["seed"], specification["scenario"])
            identity = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()[:16]
            dataset_dir = root / "dataset"
            manifest = write_dataset(dataset_dir, world, panel, orders, data_meta, f"d_{identity}")
            del orders
            metadata = {}
            for version, module in (("v1", legacy_ml), ("v2", ml)):
                print(f"[{specification['case_id']}] training {version}, fixed equal budget", file=sys.stderr, flush=True)
                model_id = "m_" + hashlib.sha256(f"{identity}_{version}".encode()).hexdigest()[:16]
                config = {**CONFIG, **(V2_OVERRIDES if version == "v2" else {})}
                metadata[version] = module.train_model(dataset_dir, root / version, model_id, config)
            old, new = align_predictions(pd.read_parquet(root / "v1" / "test_predictions.parquet"),
                                         pd.read_parquet(root / "v2" / "test_predictions.parquet"))
            contract_checks = validate_run_contract(metadata["v1"], metadata["v2"], old, manifest)
            if "neural_demand" not in new:
                raise ValueError("v2 must expose pure neural predictions separately from served blend")
            weight = metadata["v2"]["blending"]["neural_weight"]
            expected_served = weight * new["neural_demand"] + (1 - weight) * new["baseline_demand"]
            if not np.allclose(new["predicted_demand"], expected_served, rtol=1e-6, atol=1e-6):
                raise ValueError("Served predictions do not match the recorded neural/seasonal blend")
            baselines = naive_predictions(old, panel, world["bucket_minutes"])
            val_scores = metadata["v1"]["metrics"]["validation"]["baselines"]
            selected = min(BASELINES, key=lambda name: val_scores[name]["mae"])
            evaluation = {"v1": evaluation_groups(old, old["predicted_demand"], world["timezone"]),
                          "v2": evaluation_groups(new, new["predicted_demand"], world["timezone"]),
                          "v2_neural": evaluation_groups(new, new["neural_demand"], world["timezone"]),
                          "v2_served_baseline": evaluation_groups(new, new["baseline_demand"], world["timezone"])}
            evaluation.update({name: evaluation_groups(old, values, world["timezone"])
                               for name, values in baselines.items()})
            evaluation["selected_baseline"] = evaluation[selected]
            old_mae, new_mae = evaluation["v1"]["overall"]["mae"], evaluation["v2"]["overall"]["mae"]
            case.update({"status": "succeeded", "world_id": world["world_id"], "dataset_id": manifest["dataset_id"],
                         "dataset_content_sha256": manifest["content_sha256"], "evaluation": evaluation,
                         "contract_checks": contract_checks,
                         "selected_baseline": selected,
                         "baseline_selection": "lowest validation MAE from frozen v1 chronological validation",
                         "test_best_naive_diagnostic_only": min(BASELINES, key=lambda name: evaluation[name]["overall"]["mae"]),
                         "mae_change_vs_v1_percent": (new_mae - old_mae) / old_mae * 100 if old_mae else 0,
                         "intervals": {"v1": interval_metrics(old), "v2": interval_metrics(new)},
                         "latency": {"v1": _latency(legacy_ml.forecast, root / "v1", dataset_dir),
                                     "v2": _latency(ml.forecast, root / "v2", dataset_dir)},
                         "models": {version: {key: value.get(key) for key in (
                             "model_id", "architecture", "feature_schema", "config", "duration_seconds", "split",
                             "selected_candidate", "candidates", "dependencies", "model_sha256", "blending",
                             "validation_subsplit", "validation_selection", "output_transform", "metrics")}
                                    for version, value in metadata.items()}})
        except Exception as error:
            case.update(status="failed", error=f"{type(error).__name__}: {error}")
            print(f"[{specification['case_id']}] failed: {case['error']}", file=sys.stderr, flush=True)
        case["duration_seconds"] = time.perf_counter() - started
        case["source_unchanged"] = _fingerprints() == fingerprints
        report["cases"].append(case)
        _write_json(output / "progress.json", report)
        if not case["source_unchanged"]:
            # Continuing after a code change mixes model definitions across final worlds.
            print("Source changed during acceptance run; stop and report noncompliance", file=sys.stderr)
            break
    report["source_sha256_at_end"] = _fingerprints()
    if report["source_sha256_at_end"] != fingerprints:
        for case in report["cases"]:
            case["source_unchanged"] = False
    report["summary"] = summarize_cases(report["cases"])
    _write_json(output / "results.json", report)
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run final suite only after model development is frozen")
    parser.add_argument("--list-cases", action="store_true", help="Show preregistered cases without accessing their data")
    parser.add_argument("--output", type=Path, default=Path("var/quality/final-v1"))
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.run:
        print(json.dumps({**protocol_payload(), "protocol_sha256": protocol_hash()},
                         ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    report = run(args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, allow_nan=False))
    return 0 if report["summary"]["quality_target_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
