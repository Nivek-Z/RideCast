import numpy as np
import pytest

pytest.importorskip("sklearn")

from ridecast.spatial_models import (
    candidate_configs,
    fit_candidate,
    joint_support_diagnostic,
    predict_candidate,
)


def data():
    rng = np.random.default_rng(7)
    x = np.exp(rng.normal(size=(120, 15)))
    y = np.exp(5 + np.log1p(x[:, 0]) + np.log1p(x[:, 1]))
    return x, y, np.tile(np.arange(6), 20), [str(i) for i in range(15)]


def test_poisson_normalization_preserves_scale_and_appending_does_not_change_predictions():
    x, y, groups, names = data()
    config = next(c for c in candidate_configs() if c["family"] == "poisson")
    fitted = fit_candidate(config, x, y, groups, names)
    scaled = fit_candidate(config, x, y*10, groups, names)
    p = predict_candidate(fitted, x)
    np.testing.assert_allclose(predict_candidate(scaled, x), p*10, rtol=1e-6)
    np.testing.assert_allclose(predict_candidate(fitted, np.r_[x, x*100])[:len(x)], p, rtol=1e-12)


def test_constrained_boost_cannot_decrease_under_single_population_or_job_increase():
    x, y, groups, names = data()
    config = next(c for c in candidate_configs() if c["family"] == "boost")
    fitted = fit_candidate(config, x, y, groups, names)
    before = predict_candidate(fitted, x)
    for column in [0, 1]:
        changed = x.copy()
        changed[:, column] *= 1.5
        assert (predict_candidate(fitted, changed) >= before-1e-8).all()


def test_invalid_covariates_are_rejected_and_research_configs_are_unique():
    x, y, groups, names = data()
    configs = candidate_configs()
    assert len({c["name"] for c in configs}) == len(configs)
    x[0, 0] = np.nan
    with pytest.raises(ValueError):
        fit_candidate(configs[0], x, y, groups, names)


def test_joint_support_flags_remote_covariates_without_accessing_targets():
    x, _, groups, _ = data()
    diagnostic = joint_support_diagnostic(x, groups, x*1e6)
    assert diagnostic["outside_count"] == len(x)
    assert diagnostic["threshold_training_fold_q95"] > 0
