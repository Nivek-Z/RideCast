"""Real TensorFlow/Keras training, auditable evaluation and compatible inference."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ridecast.legacy_features import (
    FEATURE_VERSION,
    FUTURE_FEATURES,
    HISTORY_FEATURES,
    STATIC_FEATURES,
    chronological_ranges,
    fit_preprocessor,
    future_context,
    load_panel,
    make_windows,
    metrics,
    preprocess,
    seasonal_predictions,
    validate_scenario,
    world_fingerprint,
)

DEFAULT_CONFIG = {
    "lookback": 24, "horizon": 3, "epochs": 12, "batch_size": 128,
    "units": 32, "learning_rate": 0.001, "seed": 42, "tune": False,
}
_MODEL_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _cached_model(model_path: str, checksum: str):
    # Paths represent immutable versions; checksum prevents reuse after replacement.
    _, keras = _tensorflow()
    return keras.models.load_model(model_path, safe_mode=True)


def _tensorflow():
    # API/dashboard-only installations do not import or require TensorFlow.
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("TF_NUM_INTEROP_THREADS", "1")
    os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "2")
    os.environ.setdefault("KERAS_BACKEND", "tensorflow")
    try:
        import tensorflow as tf
        from tensorflow import keras
    except ImportError as exc:
        raise RuntimeError("TensorFlow is required for training/inference. Install ridecast[ml].") from exc
    try:
        tf.config.threading.set_inter_op_parallelism_threads(1)
        tf.config.threading.set_intra_op_parallelism_threads(2)
    except RuntimeError:
        pass  # A model may already have initialized the runtime in this worker.
    return tf, keras


def validate_config(config: dict) -> dict:
    unknown = set(config) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"Unknown training config fields: {sorted(unknown)}")
    result = {**DEFAULT_CONFIG, **config}
    limits = {"lookback": (2, 168), "horizon": (1, 24), "epochs": (1, 100),
              "batch_size": (8, 1024), "units": (4, 256), "seed": (0, 2**31 - 1)}
    for key, (low, high) in limits.items():
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    if not isinstance(result["tune"], bool):
        raise ValueError("tune must be boolean")
    if not isinstance(result["learning_rate"], (int, float)) or not 1e-6 <= result["learning_rate"] <= 0.1:
        raise ValueError("learning_rate must lie in [1e-6, 0.1]")
    return result


def _progress(callback, stage: str, fraction: float, message: str):
    if callback:
        callback(stage, float(fraction), message)


def _write_json(path: Path, value: dict):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _write_json_atomic(path: Path, value: dict):
    """Publish a whole live snapshot; readers never observe a partial JSON file."""
    temporary = path.with_name(path.name + ".tmp")
    _write_json(temporary, value)
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dataset(tf, inputs, targets, batch_size: int, seed: int, shuffle: bool):
    dataset = tf.data.Dataset.from_tensor_slices((inputs, np.log1p(targets).astype(np.float32)))
    if shuffle:
        dataset = dataset.shuffle(min(len(targets), 8192), seed=seed, reshuffle_each_iteration=True)
    options = tf.data.Options()
    options.threading.private_threadpool_size = 1
    options.threading.max_intra_op_parallelism = 1
    return dataset.batch(batch_size).with_options(options).prefetch(1)


def _predict(model, inputs, batch_size: int = 512) -> np.ndarray:
    # Direct calls avoid creating a fresh tf.data threadpool for each dashboard request.
    batches = []
    count = len(next(iter(inputs.values())))
    for start in range(0, count, batch_size):
        raw = model({k: v[start:start + batch_size] for k, v in inputs.items()}, training=False).numpy()
        batches.append(np.expm1(np.clip(raw, 0, 20)))
    result = np.concatenate(batches).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Model produced non-finite predictions")
    return result


def _build_model(keras, config: dict, inputs: dict, target_mean: float):
    history = keras.Input(shape=inputs["history"].shape[1:], name="history")
    future = keras.Input(shape=inputs["future"].shape[1:], name="future")
    static = keras.Input(shape=inputs["static"].shape[1:], name="static")
    sequence = keras.layers.GRU(config["units"], name="regional_gru")(history)
    known = keras.layers.Dense(32, activation="relu", name="known_future_encoder")(
        keras.layers.Flatten()(future)
    )
    joined = keras.layers.Concatenate()([sequence, known, static])
    hidden = keras.layers.Dense(config["units"], activation="relu")(joined)
    hidden = keras.layers.Dropout(0.1)(hidden)
    # Output is log1p(expected demand). Built-in layers permit safe .keras reload.
    output = keras.layers.Dense(
        config["horizon"], activation="softplus", name="log1p_demand",
        bias_initializer=keras.initializers.Constant(max(target_mean, 0.01)),
    )(hidden)
    model = keras.Model({"history": history, "future": future, "static": static}, output, name="ridecast_regional_gru")
    model.compile(optimizer=keras.optimizers.Adam(config["learning_rate"], clipnorm=1.0), loss="mse")
    return model


def _breakdown(actual, predicted, baseline, windows, zone_ids):
    by_horizon = [{"horizon": h + 1, "model": metrics(actual[:, h], predicted[:, h]),
                   "baseline": metrics(actual[:, h], baseline[:, h])} for h in range(actual.shape[1])]
    by_zone = []
    for z, zone_id in enumerate(zone_ids):
        mask = windows.zone_indices == z
        by_zone.append({"zone_id": zone_id, "model": metrics(actual[mask], predicted[mask]),
                        "baseline": metrics(actual[mask], baseline[mask])})
    return by_horizon, by_zone


def train_model(dataset_dir: Path, output_dir: Path, model_id: str, config: dict, progress=None) -> dict:
    """Train candidates on training only, select with validation, then test once.

    A dataset is a collection of forecast-origin/horizon pairs. Test targets can
    appear at more than one forecast origin, but never cross a split boundary.
    """
    started = time.perf_counter()
    config = validate_config(config)
    output_dir, dataset_dir = Path(output_dir), Path(dataset_dir)
    if (output_dir / "metadata.json").exists() or (output_dir / "model.keras").exists():
        raise ValueError("Model output directory already contains a version; choose a new model_id")
    output_dir.mkdir(parents=True, exist_ok=True)
    live_history = {
        "schema_version": "1.0", "model_id": model_id, "status": "starting", "candidate": None,
        "candidate_count": 2 if config["tune"] else 1, "epoch": 0, "config": config,
        "epochs": [], "candidates": [], "loss_unit": "mean squared log1p demand",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json_atomic(output_dir / "live_history.json", live_history)
    _progress(progress, "features", 0.02, "Validating immutable dataset and chronological partitions")
    data = load_panel(dataset_dir)
    ranges = chronological_ranges(data, config["lookback"], config["horizon"])
    partitions = {name: make_windows(data, start, end, config["lookback"], config["horizon"])
                  for name, (start, end) in ranges.items()}
    # No validation or test feature contributes to these statistics.
    preprocessor = fit_preprocessor(partitions["train"].inputs)
    normalized = {name: preprocess(w.inputs, preprocessor) for name, w in partitions.items()}
    _progress(progress, "features", 0.10, "Origin-aware features ready; scaler fitted on training windows only")
    tf, keras = _tensorflow()
    tf.keras.utils.set_random_seed(config["seed"])
    try:
        tf.config.experimental.enable_op_determinism()
    except (AttributeError, RuntimeError):
        pass
    period_day = max(1, int(pd.Timedelta(days=1) / data.step))
    periods = {"last_value": 0, "previous_day": period_day, "previous_week": period_day * 7}
    val_baselines = {name: seasonal_predictions(data, partitions["validation"], period)
                     for name, period in periods.items()}
    baseline_name = min(val_baselines, key=lambda name: metrics(partitions["validation"].targets, val_baselines[name])["mae"])
    candidates = [dict(config)]
    if config["tune"]:
        candidates.append({**config, "units": min(config["units"] * 2, 256), "learning_rate": config["learning_rate"] / 2})
    histories, candidate_scores, paths = [], [], []
    best_index, best_score = 0, float("inf")
    for index, candidate in enumerate(candidates):
        keras.backend.clear_session()
        tf.keras.utils.set_random_seed(config["seed"] + index)
        model = _build_model(keras, candidate, normalized["train"], float(np.log1p(partitions["train"].targets).mean()))
        current_curve = {"candidate": index, "config": candidate, "epochs": []}
        live_history.update({"status": "training", "candidate": index, "epoch": 0,
                             "config": candidate, "epochs": current_curve["epochs"],
                             "candidates": [*histories, current_curve]})
        _write_json_atomic(output_dir / "live_history.json", live_history)

        class TrainingProgress(keras.callbacks.Callback):
            def __init__(self):
                super().__init__()
                self.epoch_seconds = []

            def on_epoch_begin(self, epoch, logs=None):
                self.epoch_started = time.perf_counter()

            def on_epoch_end(self, epoch, logs=None):
                self.epoch_seconds.append(time.perf_counter() - self.epoch_started)
                logs = logs or {}
                current_curve["epochs"].append({
                    "epoch": epoch + 1, "duration_seconds": self.epoch_seconds[-1],
                    **{key: float(value) for key, value in logs.items()},
                })
                live_history["epoch"] = epoch + 1
                _write_json_atomic(output_dir / "live_history.json", live_history)
                fraction = 0.12 + 0.62 * (index + (epoch + 1) / candidate["epochs"]) / len(candidates)
                _progress(progress, "training", fraction,
                          f"candidate={index + 1}/{len(candidates)} epoch={epoch + 1}/{candidate['epochs']} "
                          f"loss={logs.get('loss', 0):.5f} val_loss={logs.get('val_loss', 0):.5f}")

        training_progress = TrainingProgress()
        history = model.fit(
            _dataset(tf, normalized["train"], partitions["train"].targets, candidate["batch_size"], candidate["seed"], True),
            validation_data=_dataset(tf, normalized["validation"], partitions["validation"].targets, candidate["batch_size"], candidate["seed"], False),
            epochs=candidate["epochs"], verbose=0,
            callbacks=[training_progress, keras.callbacks.EarlyStopping(
                monitor="val_loss", patience=4, restore_best_weights=True, min_delta=0.0001
            )],
        )
        validation_pred = _predict(model, normalized["validation"])
        score = metrics(partitions["validation"].targets, validation_pred)
        candidate_path = output_dir / f"candidate_{index}.keras"
        model.save(candidate_path)
        paths.append(candidate_path)
        candidate_scores.append({"candidate": index, "config": candidate, "validation": score,
                                 "epochs_run": len(history.history["loss"])})
        histories.append({"candidate": index, "config": candidate,
                          "epochs": [{"epoch": epoch + 1, "duration_seconds": training_progress.epoch_seconds[epoch],
                                      **{k: float(v[epoch]) for k, v in history.history.items()}}
                                     for epoch in range(len(history.history["loss"]))]})
        if score["mae"] < best_score:
            best_index, best_score = index, score["mae"]
    _progress(progress, "evaluation", 0.80, "Candidate selected by validation MAE; evaluating held-out test once")
    shutil.copyfile(paths[best_index], output_dir / "model.keras")
    keras.backend.clear_session()
    model = keras.models.load_model(output_dir / "model.keras", safe_mode=True)
    validation_pred = _predict(model, normalized["validation"])
    test_pred = _predict(model, normalized["test"])
    target = partitions["test"].targets
    baseline_preds = {name: seasonal_predictions(data, partitions["test"], period) for name, period in periods.items()}
    baseline = baseline_preds[baseline_name]
    # Marginal residual bands are calibrated on validation, never on test.
    residual_q = np.quantile(np.abs(partitions["validation"].targets - validation_pred), 0.9, axis=0)
    lower, upper = np.maximum(0, test_pred - residual_q), test_pred + residual_q
    by_horizon, by_zone = _breakdown(target, test_pred, baseline, partitions["test"], data.zone_ids)
    model_metrics, baseline_metrics = metrics(target, test_pred), metrics(target, baseline)
    split = {}
    for name, (start, end) in ranges.items():
        windows = partitions[name]
        split[name] = {
            "target_start": data.times[start].isoformat(), "target_end_exclusive": (data.times[end - 1] + data.step).isoformat(),
            "first_origin": data.times[int(windows.origins.min())].isoformat(),
            "last_origin": data.times[int(windows.origins.max())].isoformat(),
            "forecast_origin_count": len(np.unique(windows.origins)), "zone_window_count": len(windows.targets),
            "history_policy": "Only observations strictly before each origin; earlier partition history is allowed",
        }
    rows = []
    for i, (origin, z) in enumerate(zip(partitions["test"].origins, partitions["test"].zone_indices)):
        for h in range(config["horizon"]):
            rows.append({"forecast_origin": data.times[origin], "timestamp": data.times[origin + h],
                         "zone_id": data.zone_ids[z], "horizon": h + 1, "actual_demand": float(target[i, h]),
                         "predicted_demand": float(test_pred[i, h]), "baseline_demand": float(baseline[i, h]),
                         "lower": float(lower[i, h]), "upper": float(upper[i, h])})
    pd.DataFrame(rows).to_parquet(output_dir / "test_predictions.parquet", index=False)
    import keras as standalone_keras
    metadata = {
        "schema_version": "1.0", "model_id": model_id, "dataset_id": data.manifest["dataset_id"],
        "world_id": data.world["world_id"], "status": "ready", "architecture": "shared_regional_gru_direct_multihorizon",
        "source": data.manifest["source"], "target_semantics": data.manifest["target_semantics"],
        "bucket_minutes": int(data.world["bucket_minutes"]), "timezone": data.world["timezone"],
        "created_at": datetime.now(timezone.utc).isoformat(), "config": candidates[best_index],
        "duration_seconds": time.perf_counter() - started,
        "requested_config": config, "selected_candidate": best_index, "candidates": candidate_scores,
        "training_observation_start": data.manifest["observation_start"], "training_observation_end": data.manifest["observation_end"],
        "training_panel_sha256": _sha256(dataset_dir / "panel.parquet"), "world_fingerprint": world_fingerprint(data.world),
        "feature_schema": {"version": FEATURE_VERSION, "history": HISTORY_FEATURES, "future": FUTURE_FEATURES,
                           "static": STATIC_FEATURES + [f"zone_{z}" for z in data.zone_ids], "zone_ids": data.zone_ids},
        "preprocessor": preprocessor, "split": split,
        "metrics": {
            "model": model_metrics, "baseline": {"name": baseline_name, **baseline_metrics},
            "baselines": {name: metrics(target, values) for name, values in baseline_preds.items()},
            "validation": {"model": candidate_scores[best_index]["validation"],
                           "baselines": {name: metrics(partitions["validation"].targets, values) for name, values in val_baselines.items()}},
            "by_horizon": by_horizon, "by_zone": by_zone,
            "model_mae_improvement_percent": (baseline_metrics["mae"] - model_metrics["mae"]) / baseline_metrics["mae"] * 100 if baseline_metrics["mae"] else None,
            "interval": {"method": "validation_absolute_residual_quantile", "nominal_marginal_coverage": 0.9,
                         "test_coverage": float(((target >= lower) & (target <= upper)).mean()),
                         "test_mean_width": float((upper - lower).mean()),
                         "per_horizon_radius": residual_q.tolist(),
                         "limitation": "Empirical marginal residual band; temporal dependence and distribution shift can change coverage. Not a confidence guarantee."},
            "evaluation_unit": "forecast-origin × zone × horizon; overlapping targets within a partition are counted at each origin",
        },
        "exogenous_assumption": "Future weather, traffic and supply use the last origin-available observation; calendar and scheduled events are known in advance.",
        "validation_selection": "Validation MAE chooses architecture and baseline; validation loss chooses early-stopping epoch; test is evaluated only after selection.",
        "limitations": ["Synthetic performance does not establish real-world accuracy.",
                        "Imported completed_trips labels represent observed served trips, not total latent requests.",
                        "Single chronological holdout; rolling-origin cross-period validation is recommended before production."],
        "dependencies": {"tensorflow": tf.__version__, "keras": standalone_keras.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "files": {"model": "model.keras", "history": "history.json", "live_history": "live_history.json",
                  "test_predictions": "test_predictions.parquet"},
        "model_sha256": _sha256(output_dir / "model.keras"),
    }
    history_value = {"schema_version": "1.0", "model_id": model_id, "status": "ready",
                     "selected_candidate": best_index, "candidate": best_index, "candidate_count": len(candidates),
                     "config": candidates[best_index], "epoch": len(histories[best_index]["epochs"]),
                     "candidates": histories, "duration_seconds": metadata["duration_seconds"],
                     "epochs": histories[best_index]["epochs"], "loss_unit": "mean squared log1p demand"}
    _write_json(output_dir / "history.json", history_value)
    _write_json_atomic(output_dir / "live_history.json", {**live_history, **history_value})
    _write_json(output_dir / "metadata.json", metadata)
    for path in paths:
        path.unlink()
    _progress(progress, "complete", 1, "Model serialized, reloaded, and held-out metrics saved")
    return metadata


def forecast(model_dir: Path, dataset_dir: Path, horizon: int | None = None, scenario: dict | None = None) -> dict:
    model_dir, dataset_dir = Path(model_dir), Path(dataset_dir)
    metadata = json.loads((model_dir / "metadata.json").read_text())
    data = load_panel(dataset_dir)
    if metadata.get("schema_version") != "1.0" or metadata["feature_schema"].get("version") != FEATURE_VERSION:
        raise ValueError("Unsupported model/feature schema version")
    if metadata["world_id"] != data.world["world_id"] or metadata["world_fingerprint"] != world_fingerprint(data.world):
        raise ValueError("Model world fingerprint differs from dataset world; retrain for the changed world")
    if metadata["feature_schema"]["zone_ids"] != data.zone_ids:
        raise ValueError("Model zone order differs from dataset")
    if metadata["target_semantics"] != data.manifest["target_semantics"]:
        raise ValueError("Model and dataset target semantics differ")
    if metadata["bucket_minutes"] != data.world["bucket_minutes"] or metadata["timezone"] != data.world["timezone"]:
        raise ValueError("Model bucket/timezone differs from dataset")
    origin = data.times[-1] + data.step
    if origin < pd.Timestamp(metadata["training_observation_end"]):
        raise ValueError("Forecast data ends before the model development dataset; use backtest artifacts for historical evaluation")
    trained_horizon, lookback = metadata["config"]["horizon"], metadata["config"]["lookback"]
    horizon = trained_horizon if horizon is None else horizon
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= trained_horizon:
        raise ValueError(f"horizon must be an integer from 1 to trained horizon {trained_horizon}")
    if len(data.times) < lookback:
        raise ValueError(f"Forecast needs at least {lookback} contiguous history buckets")
    scenario = validate_scenario(scenario)
    if _sha256(model_dir / "model.keras") != metadata["model_sha256"]:
        raise ValueError("Model artifact checksum mismatch")
    inputs = {
        "history": data.history[-lookback:].transpose(1, 0, 2),
        "future": future_context(data, len(data.times), trained_horizon, scenario), "static": data.static,
    }
    inputs = preprocess(inputs, metadata["preprocessor"])
    with _MODEL_LOAD_LOCK:
        model = _cached_model(str((model_dir / "model.keras").resolve()), metadata["model_sha256"])
    predicted = _predict(model, inputs)[:, :horizon]
    radius = np.asarray(metadata["metrics"]["interval"]["per_horizon_radius"], dtype=float)[:horizon]
    multiplier = float(scenario.get("demand_multiplier", 1))
    lower, upper = np.maximum(0, predicted - radius) * multiplier, (predicted + radius) * multiplier
    predicted *= multiplier
    rows = []
    for h in range(horizon):
        for z, zone_id in enumerate(data.zone_ids):
            rows.append({"timestamp": (origin + h * data.step).isoformat(), "zone_id": zone_id,
                         "horizon": h + 1, "predicted_demand": float(predicted[z, h]),
                         "lower": float(lower[z, h]), "upper": float(upper[z, h])})
    return {
        "schema_version": "1.0", "model_id": metadata["model_id"], "dataset_id": data.manifest["dataset_id"],
        "world_id": data.world["world_id"], "source": data.manifest["source"],
        "target_semantics": data.manifest["target_semantics"], "forecast_origin": origin.isoformat(),
        "bucket_minutes": metadata["bucket_minutes"], "timezone": metadata["timezone"], "horizon": horizon,
        "rows": rows, "totals": [{"horizon": h + 1, "timestamp": (origin + h * data.step).isoformat(),
                                  "predicted_demand": float(predicted[:, h].sum())} for h in range(horizon)],
        "interval": {"method": "validation_absolute_residual_quantile", "nominal_marginal_coverage": 0.9,
                     "coverage_is_not_guaranteed": True},
        "exogenous_assumption": metadata["exogenous_assumption"], "scenario": scenario,
        "scenario_note": "Overrides are caller-known assumptions; demand_multiplier is an explicit post-model scaling assumption, not an estimated causal effect.",
    }
