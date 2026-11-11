"""Origin-safe seasonal summaries for count forecasting.

Only demand observations strictly before the forecast origin are read. Calendar
matching uses the city's published local calendar, never panel holiday/weather
columns or simulator latent state. Constants are fixed modeling choices, not
parameters selected from a held-out test set.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ridecast.features import PanelData

SEASONAL_FEATURES = [
    "log_lag_day1", "lag_day1_available",
    "log_lag_day2", "lag_day2_available",
    "log_lag_day3", "lag_day3_available",
    "log_lag_week1", "lag_week1_available",
    "log_recent_mean_6h", "log_recent_mean_24h", "log_recent_mean_168h",
    "log_same_time_mean_7d", "log_same_time_std_7d",
    "same_time_coverage_7d", "same_time_matching_fraction_7d",
    "log_same_time_mean_14d", "log_same_time_std_14d",
    "same_time_coverage_14d", "same_time_matching_fraction_14d",
    "recent_level_ratio", "recent_level_coverage",
    "log_seasonal_anchor", "seasonal_anchor_available",
]
ANCHOR_INDEX = SEASONAL_FEATURES.index("log_seasonal_anchor")
MIN_ANCHOR = 0.25


def _holiday(local, holidays, working_dates):
    date = local.date().isoformat()
    return date in holidays or (local.dayofweek >= 5 and date not in working_dates)


def _clock(data):
    """Cache only time/calendar indices; never cache or scan demand observations."""
    timezone = data.world.get("timezone", "UTC")
    calendar = data.world.get("calendar") or {}
    # Dataset snapshots normally contain the effective schedule. Honor the same
    # explicit list-replacement semantics when reading an older manifest too.
    scenario = data.manifest.get("scenario") or {}
    holidays = tuple(sorted(scenario.get("holiday_dates", calendar.get("holiday_dates", []))))
    working_dates = tuple(sorted(scenario.get("working_dates", calendar.get("working_dates", []))))
    signature = (id(data.times), len(data.times), data.times[0].value,
                 data.step.value, timezone, holidays, working_dates)
    cached = getattr(data, "_seasonal_clock_cache", None)
    if cached is None or cached[0] != signature:
        local = data.times.tz_convert(timezone)
        holiday_set, working_set = set(holidays), set(working_dates)
        flags = np.asarray([_holiday(t, holiday_set, working_set) for t in local], dtype=bool)
        cached = (signature, {"timezone": timezone, "holiday_dates": holiday_set,
                              "working_dates": working_set, "flags": flags, "lags": {}})
        data._seasonal_clock_cache = cached
    return cached[1]


def _local_lags(data, timestamp, clock):
    """Index the same local clock time on preceding calendar dates.

    UTC offsets can differ across DST. Nonexistent historical wall times have
    no lag; ambiguous times choose the target's DST side deterministically.
    """
    key = timestamp.value
    if key in clock["lags"]:
        return clock["lags"][key]
    local = timestamp.tz_convert(clock["timezone"])
    naive = local.tz_localize(None)
    indices = np.full(14, -1, dtype=np.int64)
    for day in range(1, 15):
        previous = (naive - pd.Timedelta(days=day)).tz_localize(
            clock["timezone"], ambiguous=bool(local.dst()), nonexistent="NaT"
        )
        if pd.isna(previous):
            continue
        delta = previous.tz_convert("UTC").value - data.times[0].value
        index, remainder = divmod(delta, data.step.value)
        if remainder == 0 and 0 <= index < len(data.times):
            indices[day - 1] = index
    clock["lags"][key] = indices
    return indices


def _profile(demand, indices, origin, target_holiday, clock, days, fallback):
    indices = indices[:days]
    available = (indices >= 0) & (indices < origin)
    eligible = indices[available]
    if not len(eligible):
        return fallback.copy(), np.zeros_like(fallback), 0.0, 0.0
    matching = clock["flags"][eligible] == target_holiday
    # Prefer comparable working/holiday dates. A first unseen day type falls
    # back to all available dates and exposes matching_fraction=0 to the model.
    selected = matching if matching.any() else np.ones(len(eligible), dtype=bool)
    ages = np.arange(1, days + 1)[available][selected]
    weights = np.exp(-ages.astype(np.float64) / 7.0)
    observations = np.asarray(demand[eligible[selected]], dtype=np.float64)
    weights /= weights.sum()
    mean = np.sum(observations * weights[:, None], axis=0)
    variance = np.sum(np.square(observations - mean) * weights[:, None], axis=0)
    return mean, np.sqrt(np.maximum(variance, 0)), float(selected.sum() / days), float(matching.mean())


def seasonal_context(data: PanelData, origin: int, horizon: int,
                     *, target_holidays: Sequence[bool] | None = None) -> np.ndarray:
    """Return [zone, horizon, feature] seasonal features at one forecast origin.

    Day/week lags use local clock time. Recent means use elapsed 6/24/168 hours.
    Same-time profiles exponentially weight up to 7/14 previous dates, preferring
    the target's working/holiday type. The anchor combines those profiles 75/25
    and applies a conservative recent-level correction derived from matching
    prior-day observations. Missing profiles fall back to recent history.
    target_holidays can replace the known future day type for a what-if forecast;
    it never relabels historical dates or changes the cached history calendar.
    """
    if isinstance(origin, bool) or not isinstance(origin, (int, np.integer)) or not 1 <= origin <= len(data.times):
        raise ValueError("Seasonal origin must follow observed history and not exceed the dataset")
    if isinstance(horizon, bool) or not isinstance(horizon, (int, np.integer)) or horizon < 1:
        raise ValueError("Seasonal horizon must be a positive integer")
    if target_holidays is not None:
        override = np.asarray(target_holidays)
        if (override.shape != (horizon,) or override.dtype.kind not in "bifu"
                or not np.isin(override, [0, 1]).all()):
            raise ValueError("target_holidays must contain one boolean/0/1 value per forecast step")
        target_holidays = override.astype(bool)
    if data.step not in (pd.Timedelta(minutes=15), pd.Timedelta(minutes=30), pd.Timedelta(hours=1)):
        raise ValueError("Seasonal context supports 15/30/60 minute buckets")
    demand = data.values["demand"]
    zones = len(data.zone_ids)
    if demand.shape != (len(data.times), zones):
        raise ValueError("Demand shape must match the time-zone grid")
    clock = _clock(data)
    step6 = int(pd.Timedelta(hours=6) / data.step)
    recent = []
    for hours in (6, 24, 168):
        width = int(pd.Timedelta(hours=hours) / data.step)
        recent.append(np.asarray(demand[max(0, origin - width):origin], dtype=np.float64).mean(axis=0))
    # Match previous-day clocks in the recent window. Do not compare an ordinary
    # working afternoon with a weekend/holiday afternoon as a level shift.
    current_indices, prior_indices = [], []
    for index in range(max(0, origin - step6), origin):
        prior = _local_lags(data, data.times[index], clock)[0]
        if 0 <= prior < origin and clock["flags"][index] == clock["flags"][prior]:
            current_indices.append(index)
            prior_indices.append(prior)
    coverage = len(current_indices) / step6
    if current_indices:
        current = np.asarray(demand[current_indices], dtype=np.float64).sum(axis=0)
        previous = np.asarray(demand[prior_indices], dtype=np.float64).sum(axis=0)
        smoothing = 3.0 * len(current_indices)
        ratio = np.clip((current + smoothing) / (previous + smoothing), 0.5, 2.0)
    else:
        ratio = np.ones(zones, dtype=np.float64)
    level_adjustment = np.exp(0.5 * coverage * np.log(ratio))
    result = np.empty((zones, horizon, len(SEASONAL_FEATURES)), dtype=np.float32)
    for h in range(horizon):
        timestamp = data.times[0] + (origin + h) * data.step
        indices = _local_lags(data, timestamp, clock)
        local = timestamp.tz_convert(clock["timezone"])
        holiday = (_holiday(local, clock["holiday_dates"], clock["working_dates"])
                   if target_holidays is None else bool(target_holidays[h]))
        lag_features = []
        for day in (1, 2, 3, 7):
            index = indices[day - 1]
            available = 0 <= index < origin
            observation = np.asarray(demand[index], dtype=np.float64) if available else recent[0]
            lag_features.extend([np.log1p(observation), np.full(zones, float(available))])
        mean7, std7, coverage7, matching7 = _profile(demand, indices, origin, holiday, clock, 7, recent[0])
        mean14, std14, coverage14, matching14 = _profile(demand, indices, origin, holiday, clock, 14, recent[0])
        has_profile = coverage14 > 0
        anchor = (0.75 * mean7 + 0.25 * mean14) * level_adjustment if has_profile else recent[0]
        anchor = np.maximum(anchor, MIN_ANCHOR)
        result[:, h] = np.stack([
            *lag_features, *[np.log1p(mean) for mean in recent],
            np.log1p(mean7), np.log1p(std7), np.full(zones, coverage7), np.full(zones, matching7),
            np.log1p(mean14), np.log1p(std14), np.full(zones, coverage14), np.full(zones, matching14),
            ratio, np.full(zones, coverage), np.log1p(anchor), np.full(zones, float(has_profile)),
        ], axis=-1)
    if not np.isfinite(result).all():
        raise ValueError("Seasonal context contains non-finite historical demand")
    return result


def seasonal_anchor(data: PanelData, origin: int, horizon: int,
                    *, target_holidays: Sequence[bool] | None = None) -> np.ndarray:
    """Return the exact positive count anchor encoded in seasonal_context."""
    context = seasonal_context(data, origin, horizon, target_holidays=target_holidays)
    return np.maximum(np.expm1(context[..., ANCHOR_INDEX]), MIN_ANCHOR).astype(np.float32)
