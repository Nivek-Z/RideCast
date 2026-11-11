"""Tests of the information boundary, not copies of neural-network internals."""

import json

import numpy as np
import pandas as pd
import pytest

from ridecast.features import (
    chronological_ranges,
    fit_preprocessor,
    future_context,
    load_panel,
    make_windows,
    metrics,
    seasonal_predictions,
    validate_scenario,
)


def make_dataset(path, days=14):
    path.mkdir(parents=True, exist_ok=True)
    zones = []
    for i in range(2):
        x = 120 + i * .01
        zones.append({"type": "Feature", "properties": {"zone_id": f"z{i}", "population": 1000 + i * 500,
                      "jobs": 500 + i * 1000, "attractiveness": 1 + i, "name": f"Zone {i}", "kind": "residential", "base_demand": 15},
                      "geometry": {"type": "Polygon", "coordinates": [[[x, 30], [x + .01, 30], [x + .01, 30.01], [x, 30.01], [x, 30]]]}})
    world = {"schema_version": "1.0", "world_id": "w_1234567890abcdef", "timezone": "Asia/Shanghai",
             "bucket_minutes": 60, "zones": {"type": "FeatureCollection", "features": zones},
             "roads": {"type": "FeatureCollection", "features": []},
             "calendar": {"holiday_dates": ["2026-01-05"], "working_dates": ["2026-01-10"]}, "events": []}
    times = pd.date_range("2026-01-01T00:00:00Z", periods=24 * days, freq="h")
    rows = []
    for t, ts in enumerate(times):
        for z in range(2):
            demand = max(0, int(15 + 8 * np.sin(t * np.pi / 12) + z * 5))
            rows.append({"timestamp": ts, "zone_id": f"z{z}", "demand": demand, "passengers": demand * 2,
                         "temperature": 20 + np.sin(t / 10), "precipitation": (t % 3) * .2,
                         "wind_speed": 2, "holiday": 0, "event_intensity": 0,
                         "traffic_index": 1.1, "available_drivers": 10})
    pd.DataFrame(rows).to_parquet(path / "panel.parquet", index=False)
    manifest = {"schema_version": "1.0", "dataset_id": "d_1234567890abcdef", "world_id": world["world_id"],
                "bucket_minutes": 60, "observation_start": times[0].isoformat(),
                "observation_end": (times[-1] + pd.Timedelta(hours=1)).isoformat(), "source": "synthetic",
                "target_semantics": "requests", "scenario": {}}
    (path / "world.json").write_text(json.dumps(world))
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


def test_future_observations_do_not_enter_context(tmp_path):
    data = load_panel(make_dataset(tmp_path / "dataset"))
    before = future_context(data, 48, 3)
    for key in ("temperature", "precipitation", "traffic_index", "available_drivers", "passengers", "demand"):
        data.values[key][48:] = 9999
    np.testing.assert_array_equal(before, future_context(data, 48, 3))
    # An explicitly supplied forecast assumption does change the context.
    assert not np.array_equal(before, future_context(data, 48, 3, {"temperature": 40}))


def test_targets_cannot_cross_partition_and_scaler_uses_training_only(tmp_path):
    data = load_panel(make_dataset(tmp_path / "dataset"))
    ranges = chronological_ranges(data, 24, 3)
    windows = {name: make_windows(data, start, end, 24, 3) for name, (start, end) in ranges.items()}
    assert windows["train"].origins.max() + 3 <= ranges["validation"][0]
    assert windows["validation"].origins.max() + 3 <= ranges["test"][0]
    state = fit_preprocessor(windows["train"].inputs)
    data.history[ranges["validation"][0]:] = 99999
    again = make_windows(data, *ranges["train"], 24, 3)
    assert state == fit_preprocessor(again.inputs)


def test_baseline_never_reads_at_or_after_origin(tmp_path):
    data = load_panel(make_dataset(tmp_path / "dataset"))
    windows = make_windows(data, 30, 33, 12, 3)
    # A period smaller than horizon still steps backwards to available history.
    predicted = seasonal_predictions(data, windows, period=1)
    for i, (origin, z) in enumerate(zip(windows.origins, windows.zone_indices)):
        assert (predicted[i] == data.values["demand"][origin - 1, z]).all()


def test_timezones_and_known_calendar(tmp_path):
    data = load_panel(make_dataset(tmp_path / "dataset"))
    # UTC 16:00 is local midnight Jan 5, an explicitly known holiday.
    ctx = future_context(data, 4 * 24 - 8, 1)
    assert (ctx[:, 0, 4] == 1).all()
    assert (ctx[:, 0, 5] == 0).all()
    assert np.allclose(ctx[:, 0, 0], 0, atol=1e-7)


def test_missing_bucket_is_not_silently_imputed(tmp_path):
    path = make_dataset(tmp_path / "dataset")
    frame = pd.read_parquet(path / "panel.parquet").iloc[1:]
    frame.to_parquet(path / "panel.parquet", index=False)
    with pytest.raises(ValueError, match="rectangular"):
        load_panel(path)


def test_zero_metrics_have_explicit_wape_semantics():
    result = metrics(np.zeros(3), np.zeros(3))
    assert result["wape"] is None
    assert result["smape"] == 0


@pytest.mark.parametrize("scenario", [
    [], 1, {"temperature": True}, {"demand_multiplier": False}, {"events": [None]},
    {"events": [1]}, {"events": [[]]}, {"events": [{}]},
    {"holiday_dates": [None]}, {"holiday_dates": [{}]}, {"holiday_dates": ["2026-1-1"]},
    {"holiday_dates": ["2026-02-30"]}, {"holiday_dates": ["NaT"]},
    {"holiday_dates": ["2026-01-01"] * 367}, {"events": [{}] * 101},
    {"holiday_dates": ["2026-01-01"], "working_dates": ["2026-01-01"]},
])
def test_invalid_scenario_structure_is_a_value_error(scenario):
    with pytest.raises(ValueError):
        validate_scenario(scenario)


@pytest.mark.parametrize("replacement", [
    {"zone_id": 1}, {"name": None}, {"intensity": True}, {"intensity": float("nan")},
    {"intensity": 11}, {"start": None}, {"start": "NaT"}, {"end": "not-a-date"},
    {"end": "2026-01-02T00:00:00Z"}, {"end": "2026-01-01T00:00:00Z"},
    {"unknown": "field"},
])
def test_invalid_forecast_event_is_a_value_error(replacement):
    event = {"zone_id": "z0", "start": "2026-01-02T00:00:00Z", "end": "2026-01-02T02:00:00Z", **replacement}
    with pytest.raises(ValueError):
        validate_scenario({"events": [event]})


def test_event_local_timezone_order_and_zone_membership(tmp_path):
    data = load_panel(make_dataset(tmp_path / "dataset"))
    # This pair mixes local and offset timestamps and reverses after UTC conversion.
    invalid = {"zone_id": "z0", "start": "2026-01-02 10:00:00", "end": "2026-01-02T01:00:00Z"}
    with pytest.raises(ValueError, match="start must be before end"):
        future_context(data, 24, 3, {"events": [invalid]})
    event = {"zone_id": "absent", "start": "2026-01-02 08:00:00", "end": "2026-01-02T03:00:00Z"}
    with pytest.raises(ValueError, match="unknown zone"):
        future_context(data, 24, 3, {"events": [event]})
    event["zone_id"] = "z0"
    context = future_context(data, 24, 3, {"events": [event]})
    assert (context[0, :, 6] == 1).all()
    assert (context[1, :, 6] == 0).all()


def test_published_world_event_can_retain_descriptive_metadata(tmp_path):
    data = load_panel(make_dataset(tmp_path / "dataset"))
    data.world["events"] = [{"zone_id": "z0", "start": "2026-01-02T00:00:00Z",
                             "end": "2026-01-02T03:00:00Z", "description": "Published event metadata"}]
    assert (future_context(data, 24, 3)[0, :, 6] == 1).all()
