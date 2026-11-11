"""Seasonal features must describe available history, including local DST clocks."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ridecast.seasonal import (
    ANCHOR_INDEX,
    MIN_ANCHOR,
    SEASONAL_FEATURES,
    seasonal_anchor,
    seasonal_context,
)


def panel(minutes=60, days=24, start="2026-01-01T00:00:00Z", timezone="Asia/Shanghai", demand=None):
    count = days * 1440 // minutes
    step = pd.Timedelta(minutes=minutes)
    times = pd.date_range(pd.Timestamp(start).tz_convert("UTC"), periods=count, freq=step)
    if demand is None:
        values = np.arange(count, dtype=np.float64) + 1
        demand = np.stack([values, values * 2], axis=1)
    return SimpleNamespace(times=times, step=step, zone_ids=["A", "B"],
                           world={"timezone": timezone, "calendar": {"holiday_dates": [], "working_dates": []}},
                           manifest={"scenario": {}}, values={"demand": np.asarray(demand, dtype=np.float64)})


def feature(context, name):
    return context[..., SEASONAL_FEATURES.index(name)]


def test_poisoning_all_future_counts_and_observed_context_cannot_change_features():
    data = panel()
    origin = 13 * 24 + 8
    baseline = seasonal_context(data, origin, 24)
    data.values["demand"][origin:] = np.nan
    data.values["holiday"] = np.ones_like(data.values["demand"]) * 1000
    data.values["temperature"] = np.ones_like(data.values["demand"]) * 1000
    np.testing.assert_array_equal(baseline, seasonal_context(data, origin, 24))


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_lags_align_with_each_target_bucket_at_all_supported_frequencies(minutes):
    data = panel(minutes)
    origin = 10 * 1440 // minutes + 7
    context = seasonal_context(data, origin, 3)
    assert context.shape == (2, 3, len(SEASONAL_FEATURES))
    for day, name in ((1, "day1"), (2, "day2"), (3, "day3"), (7, "week1")):
        indices = np.arange(origin, origin + 3) - day * 1440 // minutes
        expected = data.values["demand"][indices].T
        np.testing.assert_allclose(np.expm1(feature(context, f"log_lag_{name}")), expected, rtol=1e-6)
        assert (feature(context, f"lag_{name}_available") == 1).all()


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_recent_mean_windows_are_elapsed_hours_and_do_not_include_origin(minutes):
    data = panel(minutes)
    origin = 10 * 1440 // minutes
    context = seasonal_context(data, origin, 2)
    for hours in (6, 24, 168):
        width = hours * 60 // minutes
        expected = data.values["demand"][origin - width:origin].mean(axis=0)
        np.testing.assert_allclose(np.expm1(feature(context, f"log_recent_mean_{hours}h")),
                                   np.repeat(expected[:, None], 2, axis=1), rtol=1e-6)


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_one_bucket_history_and_zero_demand_have_explicit_safe_fallback(minutes):
    data = panel(minutes)
    data.values["demand"][0] = [4, 0]
    data.values["demand"][1:] = np.nan
    context = seasonal_context(data, 1, 3)
    assert np.isfinite(context).all()
    assert not feature(context, "lag_day1_available").any()
    assert not feature(context, "seasonal_anchor_available").any()
    np.testing.assert_allclose(seasonal_anchor(data, 1, 3), [[4, 4, 4], [MIN_ANCHOR] * 3], rtol=1e-6)


def test_lag_inside_forecast_horizon_is_marked_unavailable_instead_of_reading_it():
    data = panel()
    data.values["demand"][1:] = np.nan
    context = seasonal_context(data, 1, 30)
    # Target index24 can refer to observed index0; target25 cannot read index1.
    assert (feature(context, "lag_day1_available")[:, 23] == 1).all()
    assert (feature(context, "lag_day1_available")[:, 24] == 0).all()
    assert np.isfinite(context).all()


@pytest.mark.parametrize("target,lag_hours", [
    ("2026-03-08T08:00:00-04:00", 23),
    ("2026-11-01T08:00:00-05:00", 25),
])
def test_previous_local_day_uses_correct_dst_offset(target, lag_hours):
    start = (pd.Timestamp(target).tz_convert("UTC") - pd.Timedelta(days=12)).isoformat()
    data = panel(start=start, timezone="America/New_York")
    origin = int(data.times.get_loc(pd.Timestamp(target).tz_convert("UTC")))
    context = seasonal_context(data, origin, 1)
    expected = data.values["demand"][origin - lag_hours]
    np.testing.assert_allclose(np.expm1(feature(context, "log_lag_day1")[:, 0]), expected, rtol=1e-6)


def test_nonexistent_prior_dst_clock_has_no_lag_and_ambiguous_clock_is_deterministic():
    spring = panel(30, start="2026-03-01T00:00:00Z", timezone="America/New_York")
    origin = int(spring.times.get_loc(pd.Timestamp("2026-03-09T02:30:00-04:00").tz_convert("UTC")))
    context = seasonal_context(spring, origin, 1)
    assert (feature(context, "lag_day1_available") == 0).all()
    assert (feature(context, "lag_day2_available") == 1).all()
    autumn = panel(30, start="2026-10-20T00:00:00Z", timezone="America/New_York")
    origin = int(autumn.times.get_loc(pd.Timestamp("2026-11-02T01:30:00-05:00").tz_convert("UTC")))
    expected_index = int(autumn.times.get_loc(pd.Timestamp("2026-11-01T01:30:00-05:00").tz_convert("UTC")))
    context = seasonal_context(autumn, origin, 1)
    np.testing.assert_allclose(np.expm1(feature(context, "log_lag_day1")[:, 0]),
                               autumn.values["demand"][expected_index], rtol=1e-6)


def test_same_time_profiles_prefer_calendar_type_and_respect_known_working_day_override():
    data = panel(days=30)
    local = data.times.tz_convert("Asia/Shanghai")
    data.values["demand"][:] = np.where(local.dayofweek.to_numpy()[:, None] >= 5, 100, 10)
    target = pd.Timestamp("2026-01-24T09:00:00+08:00")  # Saturday
    origin = int(data.times.get_loc(target.tz_convert("UTC")))
    holiday_context = seasonal_context(data, origin, 1)
    np.testing.assert_allclose(np.expm1(feature(holiday_context, "log_same_time_mean_14d")), 100, rtol=1e-6)
    data.world["calendar"]["working_dates"] = ["2026-01-24"]
    working_context = seasonal_context(data, origin, 1)
    np.testing.assert_allclose(np.expm1(feature(working_context, "log_same_time_mean_14d")), 10, rtol=1e-6)


def test_recent_level_correction_is_tempered_and_uses_history_only():
    data = panel()
    data.values["demand"][:] = 20
    origin = int(data.times.get_loc(pd.Timestamp("2026-01-15T12:00:00Z")))
    np.testing.assert_allclose(seasonal_anchor(data, origin, 3), 20, rtol=1e-6)
    data.values["demand"][origin - 6:origin] = 40
    anchor = seasonal_anchor(data, origin, 3)
    assert ((anchor > 20) & (anchor < 40)).all()
    # Cache contains clocks only: changing historical counts changes the result.
    data.values["demand"][origin - 6:origin] = 1
    assert (seasonal_anchor(data, origin, 3) < 20).all()


def test_public_anchor_matches_encoded_feature_and_ignores_simulator_hidden_metadata():
    data = panel()
    context = seasonal_context(data, 300, 4)
    np.testing.assert_array_equal(seasonal_anchor(data, 300, 4),
                                  np.maximum(np.expm1(context[..., ANCHOR_INDEX]), MIN_ANCHOR))
    data.world["metadata"] = {"latent_regime": 9999, "oracle": 9999}
    data.world["seed"] = 9999
    data.world["base_demand"] = 9999
    np.testing.assert_array_equal(context, seasonal_context(data, 300, 4))


def test_future_calendar_override_changes_target_matching_without_relabeling_history():
    data = panel(days=30)
    local = data.times.tz_convert("Asia/Shanghai")
    data.values["demand"][:] = np.where(local.dayofweek.to_numpy()[:, None] >= 5, 100, 10)
    origin = int(data.times.get_loc(pd.Timestamp("2026-01-24T09:00:00+08:00").tz_convert("UTC")))
    default = seasonal_context(data, origin, 2)
    history_flags = data._seasonal_clock_cache[1]["flags"].copy()
    changed = seasonal_context(data, origin, 2, target_holidays=[False, True])
    np.testing.assert_allclose(np.expm1(feature(changed, "log_same_time_mean_14d")),
                               [[10, 100], [10, 100]], rtol=1e-6)
    np.testing.assert_array_equal(feature(changed, "recent_level_ratio"), feature(default, "recent_level_ratio"))
    np.testing.assert_array_equal(data._seasonal_clock_cache[1]["flags"], history_flags)
    np.testing.assert_array_equal(seasonal_context(data, origin, 2), default)
    np.testing.assert_array_equal(seasonal_anchor(data, origin, 2, target_holidays=[False, True]),
                                  np.maximum(np.expm1(changed[..., ANCHOR_INDEX]), MIN_ANCHOR))


@pytest.mark.parametrize("override", [[True], [True, False, True], [[True, False]], [0, 2],
                                      [0, np.nan], ["False", "True"], True])
def test_future_calendar_override_rejects_invalid_shapes_or_nonbinary_values(override):
    with pytest.raises(ValueError, match="target_holidays"):
        seasonal_context(panel(), 300, 2, target_holidays=override)


@pytest.mark.parametrize("origin,horizon", [(0, 1), (True, 1), (1.5, 1), (10000, 1), (1, 0), (1, False), (1, 1.5)])
def test_invalid_origin_or_horizon_rejected(origin, horizon):
    with pytest.raises(ValueError):
        seasonal_context(panel(), origin, horizon)
