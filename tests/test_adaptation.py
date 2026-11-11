import numpy as np
import pytest

from ridecast.adaptation import causal_level_factors


def test_requested_origins_use_only_their_own_strictly_past_prefix():
    observed = np.array([[2, 5], [4, 8], [3, 6], [10, 20], [5, 9]], dtype=float)
    expected = np.array([[3, 4], [5, 7], [4, 5], [20, 10], [7, 8]], dtype=float)
    original = causal_level_factors(observed, expected, [1, 3], 2, 1)
    changed_observed, changed_expected = observed.copy(), expected.copy()
    changed_observed[3:] = [np.nan, -1]
    changed_expected[3:] = [np.inf, -100]
    np.testing.assert_array_equal(original, causal_level_factors(changed_observed, changed_expected, [1, 3], 2, 1))
    # Altering values at the first origin affects the later request but never
    # leaks backwards into the first origin's already available factor.
    changed_observed = observed.copy()
    changed_observed[1:3] *= 100
    changed = causal_level_factors(changed_observed, expected, [1, 3], 2, 1)
    np.testing.assert_array_equal(original[0], changed[0])
    assert not np.array_equal(original[1], changed[1])


def test_regularized_window_ratio_matches_hand_calculation_and_origin_zero():
    observed = np.array([[2, 0], [4, 2], [6, 4]], dtype=float)
    expected = np.array([[4, 0], [8, 1], [12, 2]], dtype=float)
    result = causal_level_factors(observed, expected, [3, 0, 1, 3], 2, .5)
    hand = np.array([[(10 + 20) / (20 + 20), (6 + 20) / (3 + 20)],
                     [1, 1], [(2 + 20) / (4 + 20), 1],
                     [(10 + 20) / (20 + 20), (6 + 20) / (3 + 20)]]) ** .5
    np.testing.assert_allclose(result, hand, rtol=1e-7)
    assert result.dtype == np.float32 and result.shape == (4, 2)


def test_systematic_overprediction_is_corrected_without_unbounded_factor():
    observed = np.full((48, 2), 10.0)
    expected = np.full((48, 2), 20.0)
    factors = causal_level_factors(observed, expected, [24, 48], 24, 1)
    np.testing.assert_allclose(factors, (240 + 20) / (480 + 20))
    assert (np.abs(expected[-1] * factors[-1] - 10) < np.abs(expected[-1] - 10)).all()
    clipped = causal_level_factors(np.zeros((5, 1)), np.full((5, 1), 1000), [5], 5, 1)
    assert clipped[0, 0] == .5
    increased = causal_level_factors(np.full((5, 1), 1000), np.zeros((5, 1)), [5], 5, 1)
    assert increased[0, 0] == 2


def test_unbiased_zero_demand_short_history_and_disabled_adaptation():
    zeros = np.zeros((2, 3))
    np.testing.assert_array_equal(causal_level_factors(zeros, zeros, [0, 1, 2], 100, 1), np.ones((3, 3)))
    observed = np.array([[0, 2], [10, 8]])
    np.testing.assert_array_equal(causal_level_factors(observed, observed, [0, 1, 2], 10, 1), np.ones((3, 2)))
    np.testing.assert_array_equal(causal_level_factors(observed, observed * 2, [1, 2], 10, 0), np.ones((2, 2)))
    assert causal_level_factors(zeros, zeros, [], 1, .5).shape == (0, 3)
    assert causal_level_factors(np.empty((0, 2)), np.empty((0, 2)), [0], 1, 1).shape == (1, 2)
    np.testing.assert_array_equal(causal_level_factors(observed, observed, [1, 2], 10**100, 1), np.ones((2, 2)))


def test_future_objects_are_not_converted_and_only_required_prefix_is_validated():
    observed = np.array([[2], [4], ["unneeded future"]], dtype=object)
    expected = np.array([[3], [5], [object()]], dtype=object)
    value = causal_level_factors(observed, expected, [2], 2, 1)
    np.testing.assert_allclose(value, [[26 / 28]])
    with pytest.raises(ValueError, match="required prefix"):
        causal_level_factors(observed, expected, [3], 2, 1)
    poison = np.full((4, 1), np.nan)
    np.testing.assert_array_equal(causal_level_factors(poison, poison, [0], 2, 1), [[1]])


@pytest.mark.parametrize("kwargs", [
    {"origins": [1.0]}, {"origins": [True]}, {"origins": [[1]]}, {"origins": [-1]},
    {"origins": [4]}, {"window_steps": 0}, {"window_steps": True}, {"window_steps": 1.5},
    {"strength": -0.1}, {"strength": 1.1}, {"strength": np.nan}, {"strength": True},
    {"prior_count": 0}, {"prior_count": -1}, {"prior_count": np.inf}, {"prior_count": True},
    {"strength": 10**500}, {"prior_count": 10**500},
])
def test_invalid_domains_are_rejected(kwargs):
    arguments = {"observed": np.ones((3, 2)), "expected": np.ones((3, 2)), "origins": [2],
                 "window_steps": 2, "strength": .5}
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        causal_level_factors(**arguments)


def test_invalid_required_history_and_shape_are_rejected():
    with pytest.raises(ValueError, match="shape"):
        causal_level_factors(np.ones((3, 2)), np.ones((3, 1)), [2], 1, 1)
    with pytest.raises(ValueError, match="required prefix"):
        causal_level_factors(np.array([[1], [-1], [2]]), np.ones((3, 1)), [2], 1, 1)
    with pytest.raises(ValueError, match="real"):
        causal_level_factors(np.array([[1j]]), np.ones((1, 1)), [1], 1, 1)
