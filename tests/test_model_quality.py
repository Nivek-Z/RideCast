"""V2 information boundaries and v1 artifact compatibility without retraining."""

import json
from types import SimpleNamespace

import numpy as np
from test_features import make_dataset

from ridecast import features, legacy_features, legacy_ml, ml


def test_complete_v2_inputs_do_not_read_future_observations(tmp_path):
    data = features.load_panel(make_dataset(tmp_path / "data"))
    origin, lookback, horizon = 180, 24, 3
    before = {key: value.copy() for key, value in features.model_inputs(data, origin, lookback, horizon).items()}
    for values in data.values.values():
        values[origin:] = np.nan
    data.history[origin:] = np.nan
    after = features.model_inputs(data, origin, lookback, horizon)
    assert before.keys() == after.keys() == {"history", "future", "static", "anchor"}
    for key in before:
        np.testing.assert_array_equal(before[key], after[key], err_msg=key)
        assert np.isfinite(after[key]).all()


def test_anchor_stays_in_count_units_through_training_preprocessing(tmp_path):
    data = features.load_panel(make_dataset(tmp_path / "data"))
    windows = features.make_windows(data, 100, 110, lookback=24, horizon=3)
    state = features.fit_preprocessor(windows.inputs)
    normalized = features.preprocess(windows.inputs, state)
    expected = np.maximum(0.25, np.expm1(
        windows.inputs["future"][..., features.FUTURE_FEATURES.index("log_seasonal_anchor")]
    ))
    np.testing.assert_array_equal(windows.inputs["anchor"][..., 0], expected)
    np.testing.assert_array_equal(normalized["anchor"], windows.inputs["anchor"])
    assert state["anchor"]["identity"] is True
    assert (normalized["anchor"] >= 0.25).all()


def test_v2_training_statistics_are_unchanged_by_validation_and_test_counts(tmp_path):
    data = features.load_panel(make_dataset(tmp_path / "data"))
    ranges = features.chronological_ranges(data, 24, 3)
    train_start, train_end = ranges["train"]
    before = features.make_windows(data, train_start, train_end, 24, 3)
    expected = features.fit_preprocessor(before.inputs)
    for values in data.values.values():
        values[train_end:] = np.nan
    data.history[train_end:] = np.nan
    after = features.make_windows(data, train_start, train_end, 24, 3)
    assert expected == features.fit_preprocessor(after.inputs)
    for key in before.inputs:
        np.testing.assert_array_equal(before.inputs[key], after.inputs[key])


def test_v2_prediction_decoder_preserves_counts_across_batches():
    class CountModel:
        def __call__(self, inputs, training):
            assert training is False
            return SimpleNamespace(numpy=lambda: inputs["counts"])

    counts = np.array([[0, 1], [2, 3], [4, 7], [10, 20], [30, 50]], dtype=np.float32)
    result = ml._predict(CountModel(), {"counts": counts}, batch_size=2)
    np.testing.assert_array_equal(result, counts)
    assert result.dtype == np.float32


def test_v1_forecast_is_routed_before_v2_features_and_not_silently_redecoded(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "metadata.json").write_text(json.dumps({"feature_schema": {"version": "origin-aware-v1"}}))
    expected = {"rows": [{"predicted_demand": 3.5}], "legacy_marker": True}
    called = []

    def old_forecast(model, dataset, horizon, scenario):
        called.append((model, dataset, horizon, scenario))
        return expected

    def must_not_load_v2(*args, **kwargs):
        raise AssertionError("v1 artifacts must use their frozen feature construction")

    monkeypatch.setattr(legacy_ml, "forecast", old_forecast)
    monkeypatch.setattr(ml, "load_panel", must_not_load_v2)
    scenario = {"demand_multiplier": 2}
    actual = ml.forecast(model_dir, tmp_path / "dataset", 1, scenario)
    assert actual is expected
    assert called == [(model_dir, tmp_path / "dataset", 1, scenario)]


def test_v2_feature_lists_do_not_mutate_frozen_v1_schema():
    assert legacy_features.FEATURE_VERSION == "origin-aware-v1"
    assert features.FEATURE_VERSION != legacy_features.FEATURE_VERSION
    assert len(legacy_features.HISTORY_FEATURES) == 8
    assert len(legacy_features.FUTURE_FEATURES) == 12
    assert "log_seasonal_anchor" not in legacy_features.FUTURE_FEATURES
    assert "log_seasonal_anchor" in features.FUTURE_FEATURES
