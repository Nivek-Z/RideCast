"""Shared, origin-aware feature construction for training and inference.

No target-period observations enter these features. Weather, traffic and supply
for the horizon use the last observation available at each forecast origin.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

FEATURE_VERSION = "origin-aware-v1"
HISTORY_FEATURES = [
    "log_demand", "log_neighbor_demand", "log_passengers", "temperature",
    "log_precipitation", "wind_speed", "traffic_index", "log_available_drivers",
]
FUTURE_FEATURES = [
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "holiday",
    "working_day", "scheduled_event", "temperature_persistence", "log_precipitation_persistence",
    "wind_speed_persistence", "traffic_persistence", "log_supply_persistence",
]
STATIC_FEATURES = ["longitude", "latitude", "log_population", "log_jobs", "attractiveness"]
REQUIRED_PANEL = [
    "timestamp", "zone_id", "demand", "passengers", "temperature", "precipitation",
    "wind_speed", "holiday", "event_intensity", "traffic_index", "available_drivers",
]
SCENARIO_KEYS = {
    "temperature", "precipitation", "wind_speed", "traffic_index", "available_drivers",
    "rain_multiplier", "traffic_multiplier", "supply_multiplier", "demand_multiplier",
    "holiday_dates", "working_dates", "events",
}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def world_fingerprint(world: dict) -> str:
    """Identify the physical feature space; published schedules may evolve."""
    physical = {k: world.get(k) for k in (
        "schema_version", "world_id", "timezone", "bucket_minutes", "zones", "roads", "seed"
    )}
    physical["procedural_events"] = world.get("metadata", {}).get("procedural_events", False)
    physical["evolution_version"] = world.get("metadata", {}).get("evolution", {}).get("version")
    return canonical_hash(physical)


@dataclass
class PanelData:
    frame: pd.DataFrame
    world: dict
    manifest: dict
    times: pd.DatetimeIndex
    zone_ids: list[str]
    values: dict[str, np.ndarray]
    history: np.ndarray
    static: np.ndarray
    step: pd.Timedelta


def load_panel(dataset_dir: Path) -> PanelData:
    dataset_dir = Path(dataset_dir)
    world = json.loads((dataset_dir / "world.json").read_text())
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    if manifest.get("schema_version") != "1.0" or world.get("schema_version") != "1.0":
        raise ValueError("Unsupported dataset/world schema_version; expected 1.0")
    if manifest.get("world_id") != world.get("world_id"):
        raise ValueError("Dataset manifest world_id differs from its world snapshot")
    zone_ids = [str(f["properties"]["zone_id"]) for f in world["zones"]["features"]]
    if len(set(zone_ids)) != len(zone_ids) or not zone_ids:
        raise ValueError("World must contain unique nonempty zones")
    frame = pd.read_parquet(dataset_dir / "panel.parquet")
    missing = set(REQUIRED_PANEL) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing panel columns: {sorted(missing)}")
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    frame["zone_id"] = frame["zone_id"].astype(str)
    if set(frame.zone_id) != set(zone_ids) or frame.duplicated(["timestamp", "zone_id"]).any():
        raise ValueError("Panel zone set mismatch or duplicate zone-time rows")
    times = pd.DatetimeIndex(sorted(frame.timestamp.unique()))
    step = pd.Timedelta(minutes=int(world["bucket_minutes"]))
    if int(manifest["bucket_minutes"]) != int(world["bucket_minutes"]):
        raise ValueError("Dataset and world bucket_minutes differ")
    if len(times) < 2 or len(frame) != len(times) * len(zone_ids):
        raise ValueError("Panel must be a complete rectangular time-zone grid")
    if not ((times[1:] - times[:-1]) == step).all():
        raise ValueError("Panel timestamps must be regularly spaced")
    if times[0] != pd.Timestamp(manifest["observation_start"]) or times[-1] + step != pd.Timestamp(manifest["observation_end"]):
        raise ValueError("Panel range differs from manifest observation boundaries")
    frame = frame.set_index(["timestamp", "zone_id"]).reindex(pd.MultiIndex.from_product([times, zone_ids], names=["timestamp", "zone_id"])).reset_index()
    values = {}
    for key in REQUIRED_PANEL[2:]:
        arr = frame[key].to_numpy(dtype=np.float32).reshape(len(times), len(zone_ids))
        if not np.isfinite(arr).all():
            raise ValueError(f"Panel contains non-finite {key}")
        if key != "temperature" and (arr < 0).any():
            raise ValueError(f"Panel contains negative {key}")
        values[key] = arr
    if not np.equal(values["demand"], np.floor(values["demand"])).all():
        raise ValueError("Demand must contain integer counts")
    adjacency = neighbor_weights(world)
    # An explicit contraction avoids Apple Accelerate matmul floating-point flag
    # warnings observed on ARM64 for otherwise finite small demand matrices.
    neighbor_demand = np.einsum("tz,nz->tn", values["demand"], adjacency, optimize=False)
    if not np.isfinite(neighbor_demand).all():
        raise ValueError("Neighbor aggregation produced non-finite demand")
    history = np.stack([
        np.log1p(values["demand"]), np.log1p(neighbor_demand),
        np.log1p(values["passengers"]), values["temperature"], np.log1p(values["precipitation"]),
        values["wind_speed"], values["traffic_index"], np.log1p(values["available_drivers"]),
    ], axis=-1).astype(np.float32)
    from shapely.geometry import shape
    static = []
    for i, feature in enumerate(world["zones"]["features"]):
        props = feature["properties"]
        center = shape(feature["geometry"]).centroid
        row = [center.x, center.y, np.log1p(float(props.get("population", 0))),
               np.log1p(float(props.get("jobs", 0))), float(props.get("attractiveness", 1))]
        static.append(row + [float(i == j) for j in range(len(zone_ids))])
    return PanelData(frame, world, manifest, times, zone_ids, values, history, np.asarray(static, np.float32), step)


def neighbor_weights(world: dict) -> np.ndarray:
    """Touching zones share history; isolated regions retain their own history."""
    from shapely.geometry import shape
    geometries = [shape(f["geometry"]) for f in world["zones"]["features"]]
    n = len(geometries)
    weights = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j and geometries[i].distance(geometries[j]) < 1e-9:
                weights[i, j] = 1
        if weights[i].sum() == 0:
            weights[i, i] = 1
    return weights / weights.sum(axis=1, keepdims=True)


def _validate_event_structure(event: Any, *, allow_extras: bool = False) -> dict:
    if not isinstance(event, dict):
        raise ValueError("Every event must be an object")
    required = {"zone_id", "start", "end"}
    if required - set(event) or (not allow_extras and set(event) - required - {"name", "intensity"}):
        raise ValueError("Event requires zone_id/start/end and accepts only name/intensity extras")
    if not isinstance(event["zone_id"], str) or not 1 <= len(event["zone_id"]) <= 64:
        raise ValueError("Event zone_id must be a nonempty string of at most 64 characters")
    if "name" in event and (not isinstance(event["name"], str) or not 1 <= len(event["name"]) <= 100):
        raise ValueError("Event name must contain 1 to 100 characters")
    intensity = event.get("intensity", 1)
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)) or not np.isfinite(intensity) or not 0 <= intensity <= 10:
        raise ValueError("Event intensity must be a finite number in [0, 10]")
    stamps = []
    for key in ("start", "end"):
        value = event[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"Event {key} must be an ISO date/time string")
        try:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"Event {key} must be a valid ISO date/time") from None
        stamps.append(stamp)
    # Mixed local/offset timestamps are compared after applying the world timezone.
    if (stamps[0].tzinfo is None) == (stamps[1].tzinfo is None) and stamps[1] <= stamps[0]:
        raise ValueError("Event start must be before end")
    return {**event, "intensity": float(intensity)}


def validate_scenario(scenario: dict | None) -> dict:
    if scenario is None:
        return {}
    if not isinstance(scenario, dict):
        raise ValueError("Forecast scenario must be an object")
    scenario = dict(scenario)
    unknown = set(scenario) - SCENARIO_KEYS
    if unknown:
        raise ValueError(f"Unknown forecast scenario keys: {sorted(unknown)}")
    for key, value in scenario.items():
        if key in {"events", "holiday_dates", "working_dates"}:
            if not isinstance(value, list):
                raise ValueError(f"{key} must be a list")
            limit = 100 if key == "events" else 366
            if len(value) > limit:
                raise ValueError(f"{key} exceeds {limit}-item scenario limit")
            if key == "events":
                scenario[key] = [_validate_event_structure(event) for event in value]
            else:
                for date in value:
                    try:
                        if not isinstance(date, str):
                            raise ValueError
                        parsed = pd.Timestamp(date)
                        if pd.isna(parsed) or parsed.date().isoformat() != date:
                            raise ValueError
                    except (ValueError, TypeError, OverflowError):
                        raise ValueError(f"{key} must contain YYYY-MM-DD dates") from None
                scenario[key] = sorted(set(value))
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        elif (key != "temperature" and value < 0) or abs(value) > 10000:
            raise ValueError(f"{key} outside allowed range")
    if set(scenario.get("holiday_dates", [])) & set(scenario.get("working_dates", [])):
        raise ValueError("The same date cannot be both holiday_dates and working_dates")
    return scenario


def future_context(data: PanelData, origin: int, horizon: int, scenario: dict | None = None) -> np.ndarray:
    """Return [zone, horizon, feature] using only history before origin.

    The same function is used for backtesting and production. Values stored in
    the target interval are deliberately inaccessible to this function.
    """
    scenario = validate_scenario(scenario)
    if not 1 <= origin <= len(data.times):
        raise ValueError("Forecast origin must follow at least one observed bucket")
    timestamp = data.times[0] + origin * data.step
    target_times = pd.date_range(timestamp, periods=horizon, freq=data.step)
    local_times = target_times.tz_convert(data.world["timezone"])
    original_scenario = data.manifest.get("scenario") or {}
    calendar = data.world.get("calendar") or {}
    # Match simulator semantics: explicitly supplied schedules replace that list.
    holidays = set(scenario.get("holiday_dates", original_scenario.get("holiday_dates", calendar.get("holiday_dates", []))))
    working_dates = set(scenario.get("working_dates", original_scenario.get("working_dates", calendar.get("working_dates", []))))
    if holidays & working_dates:
        raise ValueError("Effective calendar marks the same date as holiday and working day")
    events = list(scenario.get("events", original_scenario.get("events", data.world.get("events", []))))
    normalized_events = []
    for event in events:
        # World imports may preserve descriptive event metadata; public forecast
        # overrides were already checked strictly by validate_scenario above.
        event = _validate_event_structure(event, allow_extras=True)
        start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
        try:
            if start.tzinfo is None:
                start = start.tz_localize(data.world["timezone"], ambiguous="raise", nonexistent="raise")
            if end.tzinfo is None:
                end = end.tz_localize(data.world["timezone"], ambiguous="raise", nonexistent="raise")
        except Exception as exc:
            raise ValueError("Event local time is invalid or ambiguous in world.timezone") from exc
        intensity = event["intensity"]
        if end <= start:
            raise ValueError("Event start must be before end in world.timezone")
        if event["zone_id"] not in data.zone_ids:
            raise ValueError("Event refers to an unknown zone")
        normalized_events.append({**event, "start": start.isoformat(),
                                  "end": end.isoformat(), "intensity": intensity})
    from ridecast.simulation import calendar_features, event_features
    effective_world = {**data.world, "calendar": {"holiday_dates": sorted(holidays), "working_dates": sorted(working_dates)},
                       "events": normalized_events}
    result = np.empty((len(data.zone_ids), horizon, len(FUTURE_FEATURES)), dtype=np.float32)
    for z, zone_id in enumerate(data.zone_ids):
        last = {k: float(v[origin - 1, z]) for k, v in data.values.items()}
        temperature = scenario.get("temperature", last["temperature"])
        rain = scenario.get("precipitation", last["precipitation"] * scenario.get("rain_multiplier", 1))
        wind = scenario.get("wind_speed", last["wind_speed"])
        traffic = scenario.get("traffic_index", last["traffic_index"] * scenario.get("traffic_multiplier", 1))
        supply = scenario.get("available_drivers", last["available_drivers"] * scenario.get("supply_multiplier", 1))
        for h, (ts, local) in enumerate(zip(target_times, local_times)):
            holiday = float(calendar_features(ts, effective_world))
            working = float(not holiday)
            event_value = float(event_features(ts, zone_id, effective_world))
            hour = local.hour + local.minute / 60
            result[z, h] = [
                np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24),
                np.sin(2 * np.pi * local.dayofweek / 7), np.cos(2 * np.pi * local.dayofweek / 7),
                holiday, working, event_value, temperature, np.log1p(rain), wind, traffic, np.log1p(supply),
            ]
    return result


@dataclass
class WindowSet:
    inputs: dict[str, np.ndarray]
    targets: np.ndarray
    origins: np.ndarray
    zone_indices: np.ndarray


def chronological_ranges(data: PanelData, lookback: int, horizon: int) -> dict:
    n = len(data.times)
    train_end, validation_end = int(n * 0.7), int(n * 0.85)
    if train_end - lookback < horizon or validation_end - train_end < horizon or n - validation_end < horizon:
        raise ValueError("Insufficient history for lookback/horizon and chronological 70/15/15 split")
    return {"train": (lookback, train_end), "validation": (train_end, validation_end), "test": (validation_end, n)}


def make_windows(data: PanelData, start: int, end: int, lookback: int, horizon: int) -> WindowSet:
    origins = np.arange(start, end - horizon + 1, dtype=np.int32)
    count = len(origins) * len(data.zone_ids)
    if count <= 0:
        raise ValueError("No complete forecast windows in partition")
    if count * lookback * len(HISTORY_FEATURES) > 80_000_000:
        raise ValueError("Feature windows exceed single-host memory budget; shorten history or reduce zone count")
    history, future, static, targets = [], [], [], []
    for t in origins:
        history.append(data.history[t - lookback:t].transpose(1, 0, 2))
        future.append(future_context(data, int(t), horizon))
        static.append(data.static)
        targets.append(data.values["demand"][t:t + horizon].T)
    return WindowSet(
        {"history": np.concatenate(history), "future": np.concatenate(future), "static": np.concatenate(static)},
        np.concatenate(targets), np.repeat(origins, len(data.zone_ids)),
        np.tile(np.arange(len(data.zone_ids)), len(origins)),
    )


def fit_preprocessor(inputs: dict[str, np.ndarray]) -> dict:
    result = {}
    for name, values in inputs.items():
        axis = tuple(range(values.ndim - 1))
        mean = values.mean(axis=axis, dtype=np.float64)
        std = values.std(axis=axis, dtype=np.float64)
        # Longitude values need float64 statistics to avoid unstable cancellation.
        std = np.where(std < 1e-5, 1, std)
        result[name] = {"mean": mean.tolist(), "std": std.tolist()}
    return result


def preprocess(inputs: dict[str, np.ndarray], state: dict) -> dict[str, np.ndarray]:
    result = {}
    for name, values in inputs.items():
        mean = np.asarray(state[name]["mean"], dtype=np.float32)
        std = np.asarray(state[name]["std"], dtype=np.float32)
        if values.shape[-1] != len(mean):
            raise ValueError("Feature shape incompatible with saved preprocessor")
        result[name] = ((values - mean) / std).astype(np.float32)
        if not np.isfinite(result[name]).all():
            raise ValueError("Non-finite preprocessed features")
    return result


def seasonal_predictions(data: PanelData, windows: WindowSet, period: int) -> np.ndarray:
    """For every target pick an observation strictly before its own origin."""
    horizon = windows.targets.shape[1]
    result = np.empty_like(windows.targets)
    for i, (origin, zone) in enumerate(zip(windows.origins, windows.zone_indices)):
        for h in range(horizon):
            if period == 0:
                index = origin - 1
            else:
                index = origin + h - period
                while index >= origin:
                    index -= period
                if index < 0:
                    index = origin - 1
            result[i, h] = data.values["demand"][index, zone]
    return result


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    error = predicted - actual
    denominator = np.abs(actual) + np.abs(predicted)
    smape = np.divide(2 * np.abs(error), denominator, out=np.zeros_like(error), where=denominator > 0)
    total = float(np.abs(actual).sum())
    return {
        "mae": float(np.abs(error).mean()), "rmse": float(np.sqrt(np.square(error).mean())),
        "wape": float(np.abs(error).sum() / total * 100) if total else None,
        "smape": float(smape.mean() * 100), "sample_count": int(actual.size),
        "units": {"mae": "orders", "rmse": "orders", "wape": "percent", "smape": "percent"},
    }
