"""V2 causal seasonal features; v1 construction stays frozen for old artifacts."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ridecast import legacy_features as v1
from ridecast.legacy_features import (  # noqa: F401 -- public compatibility surface
    PanelData,
    WindowSet,
    canonical_hash,
    chronological_ranges,
    neighbor_weights,
    seasonal_predictions,
    validate_scenario,
    world_fingerprint,
)
from ridecast.seasonal import SEASONAL_FEATURES, seasonal_context

FEATURE_VERSION = "origin-aware-profile-v3"
KINDS = ["residential", "business", "commercial", "transport", "leisure", "mixed"]
HARMONICS = [f"hour_{kind}_{order}" for order in (2, 3, 4, 5, 6) for kind in ("sin", "cos")]
HISTORY_FEATURES = v1.HISTORY_FEATURES + ["hour_sin", "hour_cos", *HARMONICS, "holiday", "log_event"]
FUTURE_FEATURES = v1.FUTURE_FEATURES + HARMONICS + ["log_scheduled_event", "lead_days"] + SEASONAL_FEATURES
STATIC_FEATURES = v1.STATIC_FEATURES + [f"kind_{kind}" for kind in KINDS]
REQUIRED_PANEL = v1.REQUIRED_PANEL
SCENARIO_KEYS = v1.SCENARIO_KEYS
PROFILE_FEATURES = ["hour_sin", "hour_cos", *HARMONICS, "weekday_sin", "weekday_cos",
                    "holiday", "log_scheduled_event", "log_precipitation_persistence",
                    "log_recent_mean_24h", "log_recent_mean_168h", "recent_level_ratio", "lead_days"]


def _cycles(times):
    hour = np.asarray(times.hour + times.minute / 60, dtype=float)
    return np.column_stack([fn(2 * np.pi * order * hour / 24)
                            for order in (1, 2, 3, 4, 5, 6) for fn in (np.sin, np.cos)]).astype(np.float32)


def load_panel(dataset_dir):
    data = v1.load_panel(dataset_dir)
    clocks = _cycles(data.times.tz_convert(data.world["timezone"]))
    clocks = np.repeat(clocks[:, None, :], len(data.zone_ids), axis=1)
    data.history = np.concatenate([data.history, clocks, data.values["holiday"][..., None],
                                   np.log1p(data.values["event_intensity"])[..., None]], axis=-1)
    kinds = [feature["properties"].get("kind", "mixed") for feature in data.world["zones"]["features"]]
    kinds = [kind if isinstance(kind, str) and kind in KINDS else "mixed" for kind in kinds]
    kind_matrix = np.array([[float(zone_kind == kind) for kind in KINDS]
                            for zone_kind in kinds], np.float32)
    data.static = np.concatenate([data.static[:, :len(v1.STATIC_FEATURES)], kind_matrix,
                                   data.static[:, len(v1.STATIC_FEATURES):]], axis=-1)
    return data


def future_context(data, origin, horizon, scenario=None):
    base = v1.future_context(data, origin, horizon, scenario)
    times = pd.date_range(data.times[0] + origin * data.step, periods=horizon,
                          freq=data.step).tz_convert(data.world["timezone"])
    harmonic = np.repeat(_cycles(times)[None, :, 2:], len(data.zone_ids), axis=0)
    lead = np.broadcast_to(np.arange(1, horizon + 1, dtype=np.float32)[None, :, None]
                           * float(data.step / pd.Timedelta(days=1)), (len(data.zone_ids), horizon, 1))
    return np.concatenate([base, harmonic, np.log1p(base[:, :, 6:7]), lead,
                            seasonal_context(data, origin, horizon, target_holidays=base[0, :, 4])], axis=-1).astype(np.float32)


def model_inputs(data, origin, lookback, horizon, scenario=None):
    future = future_context(data, origin, horizon, scenario)
    anchor_index = FUTURE_FEATURES.index("log_seasonal_anchor")
    anchor = np.maximum(0.25, np.expm1(future[:, :, anchor_index:anchor_index + 1]))
    return {"history": data.history[origin - lookback:origin].transpose(1, 0, 2),
            "future": future, "static": data.static, "anchor": anchor.astype(np.float32),
            "profile": future[..., [FUTURE_FEATURES.index(name) for name in PROFILE_FEATURES]],
            "region": np.eye(len(data.zone_ids), dtype=np.float32)}


def make_windows(data, start, end, lookback, horizon):
    origins = np.arange(start, end - horizon + 1, dtype=np.int32)
    count = len(origins) * len(data.zone_ids)
    if count <= 0:
        raise ValueError("No complete forecast windows in partition")
    estimate = count * (lookback * len(HISTORY_FEATURES) + horizon * len(FUTURE_FEATURES) + data.static.shape[-1])
    if estimate > 100_000_000:
        raise ValueError("Feature windows exceed single-host memory budget; shorten history or reduce zone count")
    rows = [model_inputs(data, int(t), lookback, horizon) for t in origins]
    targets = np.concatenate([data.values["demand"][t:t + horizon].T for t in origins])
    return WindowSet({key: np.concatenate([row[key] for row in rows]) for key in rows[0]},
                     targets, np.repeat(origins, len(data.zone_ids)),
                     np.tile(np.arange(len(data.zone_ids)), len(origins)))


def fit_preprocessor(inputs):
    result = v1.fit_preprocessor({k: v for k, v in inputs.items() if k not in {"anchor", "region"}})
    for name in ("anchor", "region"):
        if name in inputs:
            width = inputs[name].shape[-1]
            result[name] = {"mean": [0.0] * width, "std": [1.0] * width, "identity": True}
    return result


def preprocess(inputs, state):
    return v1.preprocess(inputs, state)


def metrics(actual, predicted):
    result = v1.metrics(actual, predicted)
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    total = float(actual.sum())
    result.update({"bias": float((predicted - actual).mean()),
                   "volume_bias_percent": float((predicted.sum() - total) / total * 100) if total else None})
    return result
