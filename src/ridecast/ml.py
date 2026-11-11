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

from ridecast.adaptation import causal_level_factors
from ridecast.features import (
    FEATURE_VERSION,
    FUTURE_FEATURES,
    HISTORY_FEATURES,
    PROFILE_FEATURES,
    STATIC_FEATURES,
    WindowSet,
    chronological_ranges,
    fit_preprocessor,
    load_panel,
    make_windows,
    metrics,
    model_inputs,
    preprocess,
    seasonal_predictions,
    validate_scenario,
    world_fingerprint,
)
from ridecast.rate_profile import fit_rate_profile, predict_rate_profile

DEFAULT_CONFIG = {
    "lookback": 24, "horizon": 3, "epochs": 30, "batch_size": 128,
    "units": 32, "learning_rate": 0.001, "seed": 42, "tune": False, "loss": "poisson",
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
    if not isinstance(result["loss"], str) or result["loss"] not in {"poisson", "huber"}:
        raise ValueError("loss must be poisson or huber")
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
    dataset = tf.data.Dataset.from_tensor_slices((inputs, targets.astype(np.float32)))
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
        batches.append(np.maximum(0, raw))
    result = np.concatenate(batches).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Model produced non-finite predictions")
    return result


def _build_model(keras, config: dict, inputs: dict, rate_profile: dict):
    history = keras.Input(shape=inputs["history"].shape[1:], name="history")
    future = keras.Input(shape=inputs["future"].shape[1:], name="future")
    static = keras.Input(shape=inputs["static"].shape[1:], name="static")
    anchor = keras.Input(shape=inputs["anchor"].shape[1:], name="anchor")
    profile = keras.Input(shape=inputs["profile"].shape[1:], name="profile")
    region_id = keras.Input(shape=inputs["region"].shape[1:], name="region")
    sequence = keras.layers.GRU(config["units"], name="regional_gru")(history)
    region = keras.layers.Dense(config["units"], activation="swish", name="region_encoder")(static)
    state = keras.layers.RepeatVector(config["horizon"])(keras.layers.Concatenate()([sequence, region]))
    known = keras.layers.Dense(config["units"] * 2, activation="swish", name="per_step_future")(future)
    joined = keras.layers.Concatenate()([state, known, keras.layers.Rescaling(0.01)(anchor)])
    hidden = keras.layers.Dense(config["units"] * 2, activation="swish")(joined)
    hidden = keras.layers.Dropout(0.05)(hidden)
    hidden = keras.layers.Dense(config["units"], activation="swish")(hidden)
    correction = keras.layers.Dense(1, kernel_initializer="zeros", bias_initializer="zeros",
                                    name="seasonal_log_correction")(hidden)
    # Fit a regularized calendar/rate model using training labels only. It is a
    # frozen parameter layer, not a target-derived feature or simulator oracle.
    all_region_rates = keras.layers.Dense(
        inputs["region"].shape[-1], trainable=False, name="fitted_calendar_log_rates",
        kernel_initializer=keras.initializers.Constant(rate_profile["kernel"]),
        bias_initializer=keras.initializers.Constant(rate_profile["bias"]),
    )(profile)
    chosen_rate = keras.layers.Multiply()([all_region_rates,
                                           keras.layers.RepeatVector(config["horizon"])(region_id)])
    log_rate = keras.layers.Dense(1, use_bias=False, trainable=False,
                                  kernel_initializer="ones", name="select_region_rate")(chosen_rate)
    log_rate = keras.layers.Rescaling(1.0, offset=-20.0)(keras.layers.ReLU(max_value=40.0)(
        keras.layers.Rescaling(1.0, offset=20.0)(log_rate)))
    conditional_rate = keras.layers.Activation("exponential")(log_rate)
    # The recurrent model learns deviations from a smooth fitted conditional
    # rate, rather than being forced to multiply a stale 14-day demand level.
    factor = keras.layers.Activation("exponential")(
        keras.layers.Rescaling(2.0)(keras.layers.Activation("tanh")(correction)))
    output = keras.layers.Reshape((config["horizon"],), name="demand_counts")(
        keras.layers.Multiply()([conditional_rate, factor]))
    model = keras.Model({"history": history, "future": future, "static": static, "anchor": anchor,
                          "profile": profile, "region": region_id},
                         output, name="ridecast_conditional_rate_residual_gru")
    loss = keras.losses.Poisson() if config["loss"] == "poisson" else keras.losses.Huber(delta=3.0)
    model.compile(optimizer=keras.optimizers.Adam(config["learning_rate"], clipnorm=1.0),
                  loss=loss, metrics=[keras.metrics.MeanAbsoluteError(name="mae")])
    return model


def _loss_unit(config):
    return "Poisson loss on demand counts (NLL up to a target-only constant)" if config["loss"] == "poisson" else "Huber loss on demand counts (delta=3)"


def _baseline(inputs, baseline_name, data, windows, periods, rate_profile=None):
    if baseline_name == "seasonal_profile":
        return inputs["anchor"][..., 0]
    if baseline_name == "calendar_rate":
        return predict_rate_profile(inputs["profile"], rate_profile["kernel"], rate_profile["bias"],
                                     np.argmax(inputs["region"], axis=-1))
    return seasonal_predictions(data, windows, periods[baseline_name])


def _blend(neural, baseline, weight):
    return (float(weight) * neural + (1 - float(weight)) * baseline).astype(np.float32)


def _hindcasts(data, model, preprocessor, lookback, horizon, start, end, baseline_name, periods, rate_profile):
    """Current frozen model backcasts h1 using only each past origin's inputs."""
    origins = np.arange(start, end, dtype=np.int32)
    raw = [model_inputs(data, int(t), lookback, horizon) for t in origins]
    inputs = preprocess({key: np.concatenate([row[key] for row in raw]) for key in raw[0]}, preprocessor)
    neural = _predict(model, inputs)
    windows = WindowSet({}, np.zeros_like(neural), np.repeat(origins, len(data.zone_ids)),
                        np.tile(np.arange(len(data.zone_ids)), len(origins)))
    baseline = _baseline(inputs, baseline_name, data, windows, periods, rate_profile)
    shape = (len(origins), len(data.zone_ids))
    return neural[:, 0].reshape(shape), baseline[:, 0].reshape(shape)


def _level_factors(data, observed, expected, origins, zone_indices, start, policy):
    if not policy["strength"]:
        return np.ones(len(origins), dtype=np.float32)
    width = max(1, int(pd.Timedelta(hours=policy["window_hours"]) / data.step))
    matrix = causal_level_factors(observed, expected, np.asarray(origins) - start,
                                  window_steps=width, strength=policy["strength"],
                                  prior_count=policy["prior_count"])
    return matrix[np.arange(len(origins)), zone_indices]


def _breakdown(actual, predicted, baseline, windows, zone_ids):
    by_horizon = [{"horizon": h + 1, "model": metrics(actual[:, h], predicted[:, h]),
                   "baseline": metrics(actual[:, h], baseline[:, h])} for h in range(actual.shape[1])]
    by_zone = []
    for z, zone_id in enumerate(zone_ids):
        mask = windows.zone_indices == z
        by_zone.append({"zone_id": zone_id, "model": metrics(actual[mask], predicted[mask]),
                        "baseline": metrics(actual[mask], baseline[mask])})
    return by_horizon, by_zone


def _operational_metrics(data, windows, actual, predicted, baseline, training_target):
    """Aggregate only contemporaneous zones; peak definitions do not use test scores."""
    zones, horizon = len(data.zone_ids), actual.shape[1]
    city_actual = actual.reshape(-1, zones, horizon).sum(axis=1)
    city_predicted = predicted.reshape(-1, zones, horizon).sum(axis=1)
    city_baseline = baseline.reshape(-1, zones, horizon).sum(axis=1)
    target_indices = windows.origins[:, None] + np.arange(horizon)[None, :]
    local = data.times.take(target_indices.ravel()).tz_convert(data.world["timezone"])
    hours = np.asarray(local.hour).reshape(actual.shape)
    peak = ((hours >= 7) & (hours < 10)) | ((hours >= 17) & (hours < 21))
    threshold = float(np.quantile(training_target, 0.8))
    result = {"city": {"model": metrics(city_actual, city_predicted),
                       "baseline": metrics(city_actual, city_baseline)},
              "high_demand_threshold_from_train": threshold,
              "peak_hours_local": "[07:00,10:00) and [17:00,21:00)"}
    for name, mask in (("peak", peak), ("off_peak", ~peak), ("high_demand", actual >= threshold)):
        result[name] = {"model": metrics(actual[mask], predicted[mask]),
                        "baseline": metrics(actual[mask], baseline[mask])} if mask.any() else None
    return result


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
        "epochs": [], "candidates": [], "loss_unit": _loss_unit(config),
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
    rate_profile = fit_rate_profile(normalized["train"]["profile"], partitions["train"].targets,
                                     partitions["train"].zone_indices, len(data.zone_ids),
                                     shared_feature_indices=[PROFILE_FEATURES.index("log_scheduled_event")])
    validation = partitions["validation"]
    val_start, val_end = ranges["validation"]
    calibration_start = val_start + int((val_end - val_start) * 0.65)
    selection_mask = validation.origins + config["horizon"] <= calibration_start
    calibration_mask = validation.origins >= calibration_start
    independent_calibration = bool(selection_mask.any() and calibration_mask.any())
    if not independent_calibration:
        # Tiny educational datasets cannot support another temporal split.
        selection_mask = calibration_mask = np.ones(len(validation.targets), dtype=bool)
    selection_inputs = {k: v[selection_mask] for k, v in normalized["validation"].items()}
    selection_target = validation.targets[selection_mask]
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
    val_baselines["seasonal_profile"] = validation.inputs["anchor"][..., 0]
    val_baselines["calendar_rate"] = predict_rate_profile(
        normalized["validation"]["profile"], rate_profile["kernel"], rate_profile["bias"], validation.zone_indices)
    baseline_name = min(val_baselines, key=lambda name: metrics(selection_target, val_baselines[name][selection_mask])["mae"])
    candidates = [dict(config)]
    if config["tune"]:
        candidates.append({**config, "loss": "huber" if config["loss"] == "poisson" else "poisson"})
    histories, candidate_scores, paths = [], [], []
    best_index, best_score = 0, float("inf")
    for index, candidate in enumerate(candidates):
        keras.backend.clear_session()
        tf.keras.utils.set_random_seed(config["seed"] + index)
        model = _build_model(keras, candidate, normalized["train"], rate_profile)
        current_curve = {"candidate": index, "config": candidate, "epochs": [], "loss_unit": _loss_unit(candidate)}
        live_history.update({"status": "training", "candidate": index, "epoch": 0,
                             "config": candidate, "epochs": current_curve["epochs"],
                             "candidates": [*histories, current_curve], "loss_unit": _loss_unit(candidate)})
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
            validation_data=_dataset(tf, selection_inputs, selection_target, candidate["batch_size"], candidate["seed"], False),
            epochs=candidate["epochs"], verbose=0,
            callbacks=[training_progress, keras.callbacks.EarlyStopping(
                monitor="val_mae", patience=7, restore_best_weights=True, min_delta=0.001
            ), keras.callbacks.ReduceLROnPlateau(monitor="val_mae", factor=0.5, patience=3, min_lr=1e-5)],
        )
        validation_pred = _predict(model, selection_inputs)
        score = metrics(selection_target, validation_pred)
        candidate_path = output_dir / f"candidate_{index}.keras"
        model.save(candidate_path)
        paths.append(candidate_path)
        candidate_scores.append({"candidate": index, "config": candidate, "validation": score,
                                 "initialization_seed": config["seed"] + index,
                                 "epochs_run": len(history.history["loss"])})
        histories.append({"candidate": index, "config": candidate, "loss_unit": _loss_unit(candidate),
                          "initialization_seed": config["seed"] + index,
                          "epochs": [{"epoch": epoch + 1, "duration_seconds": training_progress.epoch_seconds[epoch],
                                      **{k: float(v[epoch]) for k, v in history.history.items()}}
                                     for epoch in range(len(history.history["loss"]))]})
        if score["mae"] < best_score:
            best_index, best_score = index, score["mae"]
    _progress(progress, "evaluation", 0.80, "Candidate selected by validation MAE; evaluating held-out test once")
    shutil.copyfile(paths[best_index], output_dir / "model.keras")
    keras.backend.clear_session()
    model = keras.models.load_model(output_dir / "model.keras", safe_mode=True)
    validation_neural = _predict(model, normalized["validation"])
    # All choices below use only the earlier validation segment, never calibration/test labels.
    weights = [0.0, 0.25, 0.5, 0.75, 1.0]
    policies = [{"window_hours": 0, "strength": 0.0, "prior_count": 20.0}] + [
        {"window_hours": hours, "strength": strength, "prior_count": 20.0}
        for hours in (12, 24) for strength in (0.5, 1.0)]
    hindcast_start = max(config["lookback"], val_start - int(pd.Timedelta(hours=24) / data.step))
    hindcast_neural, hindcast_baseline = _hindcasts(
        data, model, preprocessor, config["lookback"], config["horizon"], hindcast_start,
        len(data.times), baseline_name, periods, rate_profile)
    observed = data.values["demand"][hindcast_start:]
    blend_scores = []
    for weight in weights:
        historical_expected = _blend(hindcast_neural, hindcast_baseline, weight)
        raw_selection = _blend(validation_neural[selection_mask], val_baselines[baseline_name][selection_mask], weight)
        for policy in policies:
            factors = _level_factors(data, observed, historical_expected, validation.origins[selection_mask],
                                     validation.zone_indices[selection_mask], hindcast_start, policy)
            blend_scores.append({"neural_weight": weight, "adaptation": policy,
                                 **metrics(selection_target, raw_selection * factors[:, None])})
    chosen = min(blend_scores, key=lambda score: (score["mae"], -score["neural_weight"],
                                                  score["adaptation"]["strength"]))
    neural_weight, adaptation = chosen["neural_weight"], chosen["adaptation"]
    historical_expected = _blend(hindcast_neural, hindcast_baseline, neural_weight)
    validation_factors = _level_factors(data, observed, historical_expected, validation.origins,
                                       validation.zone_indices, hindcast_start, adaptation)
    validation_pred = _blend(validation_neural, val_baselines[baseline_name], neural_weight) * validation_factors[:, None]
    test_neural = _predict(model, normalized["test"])
    target = partitions["test"].targets
    baseline_preds = {name: seasonal_predictions(data, partitions["test"], period) for name, period in periods.items()}
    baseline_preds["seasonal_profile"] = partitions["test"].inputs["anchor"][..., 0]
    baseline_preds["calendar_rate"] = predict_rate_profile(
        normalized["test"]["profile"], rate_profile["kernel"], rate_profile["bias"], partitions["test"].zone_indices)
    baseline = baseline_preds[baseline_name]
    raw_test = _blend(test_neural, baseline, neural_weight)
    test_factors = _level_factors(data, observed, historical_expected, partitions["test"].origins,
                                 partitions["test"].zone_indices, hindcast_start, adaptation)
    test_pred = raw_test * test_factors[:, None]
    # Later validation targets calibrate intervals, isolated from tuning when history permits.
    residual_q = np.quantile(np.abs(validation.targets[calibration_mask] - validation_pred[calibration_mask]), 0.9, axis=0)
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
                         "neural_demand": float(test_neural[i, h]),
                         "uncalibrated_demand": float(raw_test[i, h]), "adaptation_factor": float(test_factors[i]),
                         "lower": float(lower[i, h]), "upper": float(upper[i, h])})
    pd.DataFrame(rows).to_parquet(output_dir / "test_predictions.parquet", index=False)
    import keras as standalone_keras
    metadata = {
        "schema_version": "1.0", "model_id": model_id, "dataset_id": data.manifest["dataset_id"],
        "world_id": data.world["world_id"], "status": "ready", "architecture": "conditional_poisson_rate_residual_gru",
        "source": data.manifest["source"], "target_semantics": data.manifest["target_semantics"],
        "bucket_minutes": int(data.world["bucket_minutes"]), "timezone": data.world["timezone"],
        "created_at": datetime.now(timezone.utc).isoformat(), "config": candidates[best_index],
        "duration_seconds": time.perf_counter() - started,
        "requested_config": config, "selected_candidate": best_index, "candidates": candidate_scores,
        "training_observation_start": data.manifest["observation_start"], "training_observation_end": data.manifest["observation_end"],
        "training_panel_sha256": _sha256(dataset_dir / "panel.parquet"), "world_fingerprint": world_fingerprint(data.world),
        "feature_schema": {"version": FEATURE_VERSION, "history": HISTORY_FEATURES, "future": FUTURE_FEATURES,
                           "profile": PROFILE_FEATURES,
                           "static": STATIC_FEATURES + [f"zone_{z}" for z in data.zone_ids], "zone_ids": data.zone_ids},
        "preprocessor": preprocessor, "split": split,
        "rate_profile": {**rate_profile, "fit_partition": "train_only", "ridge": 0.02},
        "adaptation": {**adaptation, "ratio_clip": [0.5, 2.0],
                       "selection": "earlier_validation_MAE_only",
                       "source": "past observed demand versus frozen-model h1 hindcasts, strictly before each origin"},
        "output_transform": "identity_counts",
        "blending": {"neural_weight": neural_weight, "baseline_name": baseline_name,
                     "validation_grid": blend_scores, "selection": "earlier_validation_MAE_only",
                     "deployment": "seasonal_fallback" if neural_weight == 0 else "neural" if neural_weight == 1 else "neural_seasonal_blend"},
        "validation_subsplit": {"independent_calibration": independent_calibration,
                                 "calibration_start": data.times[calibration_start].isoformat(),
                                 "selection_windows": int(selection_mask.sum()),
                                 "calibration_windows": int(calibration_mask.sum())},
        "metrics": {
            "model": model_metrics, "baseline": {"name": baseline_name, **baseline_metrics},
            "neural": metrics(target, test_neural),
            "uncalibrated_model": metrics(target, raw_test),
            "operational": _operational_metrics(data, partitions["test"], target, test_pred, baseline,
                                                  partitions["train"].targets),
            "baselines": {name: metrics(target, values) for name, values in baseline_preds.items()},
            "validation": {"model": metrics(selection_target, validation_pred[selection_mask]),
                           "neural": candidate_scores[best_index]["validation"],
                           "baselines": {name: metrics(selection_target, values[selection_mask]) for name, values in val_baselines.items()}},
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
        "validation_selection": "Earlier validation MAE chooses epoch, count-loss candidate, seasonal baseline and blend weight; later validation calibrates bands when feasible; test only after choices are frozen.",
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
                     "epochs": histories[best_index]["epochs"], "loss_unit": _loss_unit(candidates[best_index])}
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
    if metadata.get("feature_schema", {}).get("version") == "origin-aware-v1":
        from ridecast.legacy_ml import forecast as legacy_forecast
        return legacy_forecast(model_dir, dataset_dir, horizon, scenario)
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
    raw_inputs = model_inputs(data, len(data.times), lookback, trained_horizon, scenario)
    inputs = preprocess(raw_inputs, metadata["preprocessor"])
    with _MODEL_LOAD_LOCK:
        model = _cached_model(str((model_dir / "model.keras").resolve()), metadata["model_sha256"])
    neural = _predict(model, inputs)
    period_day = max(1, int(pd.Timedelta(days=1) / data.step))
    periods = {"last_value": 0, "previous_day": period_day, "previous_week": period_day * 7}
    windows = WindowSet({}, np.zeros_like(neural), np.full(len(data.zone_ids), len(data.times)),
                        np.arange(len(data.zone_ids)))
    blending = metadata["blending"]
    baseline = _baseline(inputs, blending["baseline_name"], data, windows, periods, metadata["rate_profile"])
    predicted = _blend(neural, baseline, blending["neural_weight"])[:, :horizon]
    adaptation = metadata["adaptation"]
    adaptation_factors = np.ones(len(data.zone_ids), dtype=np.float32)
    if adaptation["strength"]:
        width = max(1, int(pd.Timedelta(hours=adaptation["window_hours"]) / data.step))
        start = max(lookback, len(data.times) - width)
        if start < len(data.times):
            past_neural, past_baseline = _hindcasts(data, model, metadata["preprocessor"], lookback,
                trained_horizon, start, len(data.times), blending["baseline_name"], periods, metadata["rate_profile"])
            past_expected = _blend(past_neural, past_baseline, blending["neural_weight"])
            adaptation_factors = causal_level_factors(data.values["demand"][start:], past_expected,
                np.array([len(data.times) - start]), window_steps=width, strength=adaptation["strength"],
                prior_count=adaptation["prior_count"])[0]
            predicted *= adaptation_factors[:, None]
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
        "architecture": metadata["architecture"], "blending": blending,
        "adaptation": {**adaptation, "current_factors": dict(zip(data.zone_ids, adaptation_factors.astype(float).tolist()))},
        "scenario_note": "Overrides are caller-known assumptions; demand_multiplier is an explicit post-model scaling assumption, not an estimated causal effect.",
    }
