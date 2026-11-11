"""Statistical recovery and edge behavior of the training-only count profile."""

import json

import numpy as np
import pytest

from ridecast.rate_profile import MAX_ITERATIONS, fit_rate_profile, predict_rate_profile


def sample(seed=123, count=1000, horizon=3):
    rng = np.random.default_rng(seed)
    profile = rng.normal(size=(count, horizon, 3))
    zones = np.arange(count) % 2
    kernel = np.array([[0.3, -0.4], [-0.2, 0.1], [0.15, 0.25]])
    bias = np.log([3, 7])
    rate = np.exp(np.einsum("nhp,np->nh", profile, kernel[:, zones].T) + bias[zones, None])
    return profile, rng.poisson(rate), zones, kernel, bias, rate


def test_known_poisson_coefficients_recovered_and_improve_on_intercept_only_rate():
    profile, target, zones, kernel, bias, true_rate = sample()
    fitted = fit_rate_profile(profile, target, zones, 2)
    np.testing.assert_allclose(fitted["kernel"], kernel, atol=0.06)
    np.testing.assert_allclose(fitted["bias"], bias, atol=0.06)
    predicted = predict_rate_profile(profile, fitted["kernel"], fitted["bias"], zones)
    baseline = np.array([target[zones == z].mean() for z in range(2)])[zones, None]
    assert np.abs(predicted - true_rate).mean() < np.abs(baseline - true_rate).mean() * 0.2
    for row in fitted["diagnostics"]["zones"]:
        assert row["converged"]
        assert row["final_loss"] < row["initial_loss"]
        assert 0 < row["iterations"] <= MAX_ITERATIONS
    # Fitted state can be stored directly, without NumPy-specific serializers.
    restored = json.loads(json.dumps(fitted, allow_nan=False))
    np.testing.assert_array_equal(predict_rate_profile(profile, restored["kernel"], restored["bias"], zones), predicted)


def test_fitted_profile_predicts_independent_feature_draws_with_same_known_rates():
    profile, target, zones, kernel, bias, _ = sample()
    fitted = fit_rate_profile(profile, target, zones, 2)
    fresh, _, fresh_zones, _, _, true_rate = sample(seed=718, count=300, horizon=2)
    predicted = predict_rate_profile(fresh, fitted["kernel"], fitted["bias"], fresh_zones)
    assert predicted.shape == (300, 2)
    assert np.abs(predicted - true_rate).mean() / true_rate.mean() < 0.05
    assert not np.allclose(np.asarray(fitted["kernel"])[:, 0], np.asarray(fitted["kernel"])[:, 1])
    assert np.exp(fitted["bias"])[1] > np.exp(fitted["bias"])[0] * 1.8


def test_zero_count_zone_and_unobserved_zone_have_explicit_finite_fallbacks():
    rng = np.random.default_rng(9)
    profile = rng.normal(size=(40, 2, 4))
    zones = np.arange(40) % 2
    target = np.broadcast_to((zones * 5)[:, None], (40, 2)).astype(float)
    fitted = fit_rate_profile(profile, target, zones, 3)
    predicted = predict_rate_profile(profile, fitted["kernel"], fitted["bias"], zones)
    assert np.isfinite(predicted).all() and (predicted > 0).all()
    np.testing.assert_allclose(predicted[zones == 0], 1e-6, rtol=1e-6)
    np.testing.assert_allclose(predicted[zones == 1], 5, rtol=1e-6)
    assert fitted["diagnostics"]["zones"][0]["status"] == "zero_counts"
    assert fitted["diagnostics"]["zones"][2]["status"] == "no_training_rows"
    np.testing.assert_allclose(predict_rate_profile(profile[:1], fitted["kernel"], fitted["bias"], [2]), 2.5)
    json.dumps(fitted, allow_nan=False)


def test_constant_and_collinear_features_are_regularized_without_singular_failure():
    rng = np.random.default_rng(8)
    trend = rng.normal(size=(300, 2))
    profile = np.stack([trend, 2 * trend, np.ones_like(trend), np.zeros_like(trend)], axis=-1)
    target = rng.poisson(np.exp(np.log(4) + 0.3 * trend))
    fitted = fit_rate_profile(profile, target, np.zeros(300, dtype=int), 1)
    row = fitted["diagnostics"]["zones"][0]
    assert np.isfinite(fitted["kernel"]).all()
    assert row["final_loss"] < row["initial_loss"]
    assert row["status"] == "converged"
    assert np.isfinite(predict_rate_profile(profile, fitted["kernel"], fitted["bias"], np.zeros(300))).all()


def test_training_subset_does_not_read_future_rows_and_retains_no_input_views():
    profile, target, zones, _, _, _ = sample(count=200)
    before = fit_rate_profile(profile[:100], target[:100], zones[:100], 2)
    saved = json.dumps(before, sort_keys=True, allow_nan=False)
    profile[100:] = np.nan
    target[100:] = -999
    after = fit_rate_profile(profile[:100], target[:100], zones[:100], 2)
    assert before == after
    profile[:100] = 999
    assert json.dumps(before, sort_keys=True, allow_nan=False) == saved
    assert before["diagnostics"]["training_samples"] == 100
    assert before["diagnostics"]["training_observations"] == 300


def test_bias_only_profile_and_large_log_rates_remain_finite():
    profile = np.empty((12, 2, 0))
    target = np.full((12, 2), 3)
    fitted = fit_rate_profile(profile, target, np.zeros(12, dtype=int), 1)
    np.testing.assert_allclose(predict_rate_profile(profile, fitted["kernel"], fitted["bias"], np.zeros(12)), 3)
    extreme = predict_rate_profile(np.array([[[1000.0], [-1000.0]]]), [[1000.0]], [0.0], [0])
    assert np.isfinite(extreme).all() and (extreme > 0).all()
    np.testing.assert_allclose(extreme, [[np.exp(20), np.exp(-20)]], rtol=1e-6)


def test_shared_event_borrowing_preserves_constant_training_rates_and_changes_future_effect():
    rng = np.random.default_rng(441)
    profile = rng.normal(size=(800, 2, 2))
    zones = np.arange(800) % 2
    profile[zones == 1, :, 0] = -0.4  # Standardized no-event value need not be zero.
    true_log_rate = np.log(4 + zones[:, None] * 2) + 0.7 * profile[..., 0] + 0.2 * profile[..., 1]
    target = rng.poisson(np.exp(true_log_rate))
    original = fit_rate_profile(profile, target, zones, 3)
    shared = fit_rate_profile(profile, target, zones, 3, shared_feature_indices=(0,))
    before = predict_rate_profile(profile, original["kernel"], original["bias"], zones)
    after = predict_rate_profile(profile, shared["kernel"], shared["bias"], zones)
    np.testing.assert_array_equal(after, before)
    detail = shared["diagnostics"]["shared_features"][0]
    assert detail["source_zone_indices"] == [0]
    assert detail["borrowed_zone_indices"] == [1]
    assert detail["missing_training_zone_indices"] == [2]
    assert abs(shared["kernel"][0][1] - original["kernel"][0][0]) < 1e-12
    assert shared["kernel"][0][2] == original["kernel"][0][2] == 0
    assert shared["bias"][2] == original["bias"][2]
    future = profile[zones == 1][:3].copy()
    no_event = predict_rate_profile(future, shared["kernel"], shared["bias"], [1, 1, 1])
    future[..., 0] += 1
    with_event = predict_rate_profile(future, shared["kernel"], shared["bias"], [1, 1, 1])
    np.testing.assert_allclose(with_event / no_event, np.exp(detail["coefficient"]), rtol=1e-6)
    json.dumps(shared, allow_nan=False)


def test_shared_effect_uses_information_weights_and_does_not_force_positive_sign():
    rng = np.random.default_rng(871)
    count = 900
    profile = rng.normal(size=(count, 2, 2))
    zones = np.arange(count) % 3
    profile[zones == 1, :, 0] *= 2
    profile[zones == 2, :, 0] = 0.3
    effects = np.array([-0.25, -0.65, 0])
    rate = np.exp(np.log(3 + zones[:, None]) + effects[zones, None] * profile[..., 0])
    target = rng.poisson(rate)
    original = fit_rate_profile(profile, target, zones, 3)
    shared = fit_rate_profile(profile, target, zones, 3, shared_feature_indices=[0])
    weights = [target[zones == z].size * profile[zones == z, :, 0].var() * target[zones == z].mean()
               for z in (0, 1)]
    expected = np.average([original["kernel"][0][z] for z in (0, 1)], weights=weights)
    detail = shared["diagnostics"]["shared_features"][0]
    assert detail["coefficient"] < 0
    np.testing.assert_allclose(detail["coefficient"], expected, rtol=1e-12)
    np.testing.assert_allclose(shared["kernel"][0][2], expected, rtol=1e-12)
    for zone in (0, 1):
        assert shared["kernel"][0][zone] == original["kernel"][0][zone]
        assert shared["bias"][zone] == original["bias"][zone]


def test_no_varying_feature_evidence_does_not_invent_shared_event_effect():
    rng = np.random.default_rng(65)
    profile = rng.normal(size=(80, 2, 2))
    zones = np.arange(80) % 2
    profile[..., 0] = -0.2
    target = rng.poisson(np.full((80, 2), 4.0))
    original = fit_rate_profile(profile, target, zones, 2)
    shared = fit_rate_profile(profile, target, zones, 2, shared_feature_indices=(0,))
    assert shared["kernel"] == original["kernel"]
    assert shared["bias"] == original["bias"]
    detail = shared["diagnostics"]["shared_features"][0]
    assert detail["status"] == "no_information"
    assert detail["coefficient"] is None
    assert detail["sources"] == detail["borrowed"] == []
    assert fit_rate_profile(profile, target, zones, 2, shared_feature_indices=()) == original


@pytest.mark.parametrize("indices", [None, [True], [0.0], [-1], [2], [0, 0], [[0]], [np.nan], "0"])
def test_invalid_shared_feature_indices_rejected(indices):
    with pytest.raises(ValueError, match="shared_feature_indices"):
        fit_rate_profile(np.ones((3, 2, 2)), np.ones((3, 2)), [0, 0, 0], 1,
                         shared_feature_indices=indices)


@pytest.mark.parametrize("replacement", [
    {"profile": np.zeros((2, 3))}, {"profile": np.ones((0, 2, 1))},
    {"profile": np.full((2, 2, 1), np.nan)}, {"targets": np.full((2, 2), -1)},
    {"targets": np.full((2, 2), np.inf)}, {"targets": np.ones((3, 2))},
    {"zone_indices": [0, 2]}, {"zone_indices": [0, 0.5]}, {"zone_indices": [True, False]},
    {"zone_count": True}, {"zone_count": 0}, {"ridge": -1}, {"ridge": float("nan")},
])
def test_invalid_training_arrays_or_options_rejected(replacement):
    inputs = {"profile": np.ones((2, 2, 1)), "targets": np.ones((2, 2)),
              "zone_indices": [0, 1], "zone_count": 2, **replacement}
    with pytest.raises(ValueError):
        fit_rate_profile(**inputs)
