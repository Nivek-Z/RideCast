import numpy as np
import pandas as pd
import pytest

from ridecast.spatial_calibration import (
    fit_stable_rule,
    fit_student,
    generate_regions,
    predict_rule,
    predict_student,
    support_diagnostics,
    transform_features,
)


def training_data():
    rng = np.random.default_rng(17)
    x = np.exp(rng.normal(size=(240, 3)))
    y = np.exp(7 + 1.2*np.log1p(x[:, 0]) + .7*np.log1p(x[:, 1]) + rng.normal(0, .03, len(x)))
    return x, y, np.tile(np.arange(6), 40)


def test_stable_rules_preserve_volume_growth_and_do_not_learn_null_covariate():
    x, y, groups = training_data()
    rule = fit_stable_rule(x, y, groups, ["population", "jobs", "null"], alpha=.01, bootstraps=20)
    assert rule["active"][:2] == [True, True]
    assert not rule["active"][2]
    larger = fit_stable_rule(x, y*10, groups, rule["features"], alpha=.01, bootstraps=20)
    np.testing.assert_allclose(rule["slopes"], larger["slopes"], atol=2e-6)
    np.testing.assert_allclose(predict_rule(larger, x), predict_rule(rule, x)*10, rtol=2e-6)
    # Future covariates may be inspected for support, never incorporated into the fitted transform.
    before = rule["fingerprint"]
    appended = np.concatenate([x, x*1000])
    np.testing.assert_array_equal(predict_rule(rule, appended)[:len(x)], predict_rule(rule, x))
    diagnostics = support_diagnostics(rule, x*1000)
    assert diagnostics["outside_any_training_range"] == len(x)
    assert rule["fingerprint"] == before


def test_virtual_regions_are_repeatable_under_partition_reordering_and_growth():
    x, y, groups = training_data()
    rule = fit_stable_rule(x, y, groups, ["population", "jobs", "null"], bootstraps=8)
    batch = generate_regions(rule, [3, 4], zones=8)
    overlap = generate_regions(rule, [4], zones=8)
    pd.testing.assert_frame_equal(batch[batch.world_index == 4].reset_index(drop=True), overlap)
    reordered = generate_regions(rule, [4, 3], zones=12)
    selected = reordered[(reordered.world_index == 3) & (reordered.zone_index < 8)].reset_index(drop=True)
    pd.testing.assert_frame_equal(batch[batch.world_index == 3].reset_index(drop=True), selected)
    assert (batch.completed_trips >= 0).all()
    assert (batch[rule["features"]] >= 0).all().all()
    def external(raw):
        return np.full(len(raw), 100.)
    whole = generate_regions(rule, [3, 4], zones=8, mean_predictor=external)
    part = generate_regions(rule, [4], zones=8, mean_predictor=external)
    pd.testing.assert_frame_equal(whole[whole.world_index == 4].reset_index(drop=True), part)
    assert (whole.expected_completed_trips == 100).all()


def test_units_cancel_and_outliers_do_not_dominate_smooth_rule():
    x, y, groups = training_data()
    names = ["population", "jobs", "null"]
    rule = fit_stable_rule(x, y, groups, names, alpha=.01, bootstraps=20)
    converted = fit_stable_rule(x*np.array([1, 1000, 10]), y, groups, names, alpha=.01, bootstraps=20)
    np.testing.assert_allclose(transform_features(rule, x), transform_features(converted, x*[1, 1000, 10]))
    np.testing.assert_allclose(predict_rule(rule, x), predict_rule(converted, x*[1, 1000, 10]), rtol=1e-7)
    corrupted = y.copy()
    corrupted[0] *= 1000
    resistant = fit_stable_rule(x, corrupted, groups, names, alpha=.01, bootstraps=20)
    assert np.median(np.abs(np.log(predict_rule(rule, x)/predict_rule(resistant, x)))) < .05


def test_student_reuses_frozen_training_transform_and_rejects_incompatible_rule():
    x, y, groups = training_data()
    rule = fit_stable_rule(x, y, groups, ["population", "jobs", "null"], bootstraps=8)
    learner = fit_student(rule, x, y, alpha=.01)
    prediction = predict_student(rule, learner, x)
    assert np.mean(np.abs(np.log(prediction/y))) < .1
    assert learner["transform_rule_fingerprint"] == rule["fingerprint"]
    with pytest.raises(ValueError, match="mismatch"):
        predict_student({**rule, "fingerprint": "different"}, learner, x)
    with pytest.raises(ValueError):
        fit_stable_rule(x, y, groups*0, rule["features"], bootstraps=8)
