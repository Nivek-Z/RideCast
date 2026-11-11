"""Strictly causal recent-level correction for already-observed model errors.

The caller supplies one-step expectations that were constructed without their
own targets. This helper only aggregates errors; it never predicts expectations,
refits model weights, or consults observations at/after a requested origin.
"""

from __future__ import annotations

import math
from numbers import Real

import numpy as np


def causal_level_factors(observed, expected, origins, window_steps: int,
                         strength: float, prior_count: float = 20) -> np.ndarray:
    """Return [origin, zone] factors from [origin-window_steps, origin).

    For each requested origin, the positive pseudo-count regularizes the ratio
    (sum(observed) + prior_count) / (sum(expected) + prior_count). Its clipped
    [0.5, 2] value is raised to strength in [0, 1]. Missing warm-up expectations
    must be resolved explicitly by the caller. Only the prefix strictly before
    the maximum requested origin is converted/validated; unneeded future values
    may contain sentinels and cannot affect historical factors.
    """
    observed_array, expected_array = np.asarray(observed), np.asarray(expected)
    if (observed_array.ndim != 2 or expected_array.ndim != 2 or
            observed_array.shape != expected_array.shape or observed_array.shape[1] < 1):
        raise ValueError("observed and expected must have the same [time, zone] shape with at least one zone")
    origin_array = np.asarray(origins)
    # np.asarray([]) is float64 by default; a genuinely empty sequence is valid.
    if origin_array.ndim != 1 or (origin_array.size and origin_array.dtype.kind not in "iu"):
        raise ValueError("origins must be a one-dimensional integer array")
    if (origin_array < 0).any() or (origin_array > observed_array.shape[0]).any():
        raise ValueError("origins must lie in [0, number of observed time steps]")
    if isinstance(window_steps, (bool, np.bool_)) or not isinstance(window_steps, (int, np.integer)) or window_steps < 1:
        raise ValueError("window_steps must be a positive integer")
    try:
        strength_value = float(strength)
        prior_value = float(prior_count)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("strength and prior_count must be finite real numbers") from error
    if isinstance(strength, (bool, np.bool_)) or not isinstance(strength, Real) or not math.isfinite(strength_value) or not 0 <= strength_value <= 1:
        raise ValueError("strength must be a finite real number in [0, 1]")
    if isinstance(prior_count, (bool, np.bool_)) or not isinstance(prior_count, Real) or not math.isfinite(prior_value) or prior_value <= 0:
        raise ValueError("prior_count must be a finite positive real number")
    ends = origin_array.astype(np.int64, copy=False)
    maximum = int(ends.max()) if ends.size else 0
    prefixes = []
    for name, array in (("observed", observed_array), ("expected", expected_array)):
        prefix = array[:maximum]
        if prefix.size and np.iscomplexobj(prefix):
            raise ValueError(f"{name} required prefix must contain real counts")
        try:
            prefix = np.asarray(prefix, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{name} required prefix must contain finite nonnegative counts") from error
        if not np.isfinite(prefix).all() or (prefix < 0).any():
            raise ValueError(f"{name} required prefix must contain finite nonnegative counts")
        prefixes.append(prefix)
    zones = observed_array.shape[1]
    if not ends.size:
        return np.ones((0, zones), dtype=np.float32)
    if maximum == 0 or strength == 0:
        return np.ones((len(ends), zones), dtype=np.float32)
    # float64 accumulators avoid cancellation from long float32 histories.
    with np.errstate(over="ignore", invalid="ignore"):
        totals = [np.concatenate([np.zeros((1, zones), dtype=np.float64),
                                  np.cumsum(prefix, axis=0, dtype=np.float64)], axis=0)
                  for prefix in prefixes]
    if any(not np.isfinite(total).all() for total in totals):
        raise ValueError("Required prefix cumulative count exceeds finite float64 range")
    starts = np.maximum(0, ends - min(int(window_steps), maximum))
    observed_sum, expected_sum = (total[ends] - total[starts] for total in totals)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        ratio = (observed_sum + prior_value) / (expected_sum + prior_value)
    if not np.isfinite(ratio).all():
        raise ValueError("Recent-level ratio exceeds finite float64 range")
    return np.power(np.clip(ratio, 0.5, 2.0), strength_value).astype(np.float32)
