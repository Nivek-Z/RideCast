"""Regularized count-rate profiles fitted from explicitly supplied training rows.

This small NumPy Poisson GLM is a deterministic initializer/comparator, not a
simulator oracle. Callers are responsible for passing the training partition
only. No calendar, world seed, validation data or test data is read here.
"""

from __future__ import annotations

import math

import numpy as np

MAX_ITERATIONS = 30
MIN_LOG_RATE = -20.0
MAX_LOG_RATE = 20.0
ZERO_RATE = 1e-6


def _profile_array(profile):
    values = np.asarray(profile, dtype=np.float64)
    if values.ndim != 3 or not values.shape[0] or not values.shape[1]:
        raise ValueError("profile must have nonempty [sample,horizon,feature] shape")
    if not np.isfinite(values).all():
        raise ValueError("profile features must be finite")
    return values


def _zones(zone_indices, count, zone_count):
    indices = np.asarray(zone_indices)
    if (indices.shape != (count,) or indices.dtype.kind not in "ifu"
            or not np.isfinite(indices).all()
            or not np.equal(indices, np.floor(indices)).all()
            or (indices < 0).any() or (indices >= zone_count).any()):
        raise ValueError("zone_indices must contain one valid integer zone per sample")
    return indices.astype(np.int64)


def _linear(design, coefficients):
    # Explicit contractions avoid small-matrix Accelerate floating-point flag
    # warnings seen elsewhere in this project's Apple Silicon runtime.
    projected = np.einsum("np,p->n", design, coefficients, optimize=False)
    if not np.isfinite(projected).all():
        raise ValueError("Rate profile produced a non-finite linear predictor")
    return np.clip(projected, MIN_LOG_RATE, MAX_LOG_RATE)


def _objective(design, target, coefficients, ridge):
    eta = _linear(design, coefficients)
    rate = np.exp(eta)
    loss = np.mean(rate - target * eta) + 0.5 * ridge * np.square(coefficients[1:]).sum()
    return float(loss), rate


def _fit_zone(features, target, ridge):
    count, width = features.shape
    initial_mean = max(float(target.mean()), 0.1)
    coefficients = np.zeros(width + 1, dtype=np.float64)
    coefficients[0] = math.log(initial_mean)
    design = np.column_stack([np.ones(count), features])
    initial_loss, rate = _objective(design, target, coefficients, ridge)
    loss = initial_loss
    if not target.any():
        coefficients[0] = math.log(ZERO_RATE)
        return coefficients, {"status": "zero_counts", "converged": True, "iterations": 0,
                              "initial_loss": initial_loss, "final_loss": ZERO_RATE,
                              "gradient_max": ZERO_RATE}
    penalty = np.r_[0.0, np.full(width, ridge)]
    status, converged, iterations, gradient_max = "max_iterations", False, 0, None
    for iteration in range(MAX_ITERATIONS):
        gradient = np.einsum("np,n->p", design, rate - target, optimize=False) / count
        gradient += penalty * coefficients
        gradient_max = float(np.abs(gradient).max())
        if gradient_max < 1e-6:
            status, converged = "converged", True
            break
        weighted = design * np.sqrt(rate)[:, None]
        hessian = np.einsum("ni,nj->ij", weighted, weighted, optimize=False) / count
        hessian.flat[::len(coefficients) + 1] += penalty + 1e-8
        if not np.isfinite(hessian).all():
            status = "numerical_guard"
            break
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=1e-10)[0]
        if not np.isfinite(step).all():
            status = "numerical_guard"
            break
        # Preserve the Newton direction while limiting every observation's log
        # rate change, instead of clipping coefficients independently.
        movement = np.einsum("np,p->n", design, step, optimize=False)
        largest = float(np.abs(movement).max())
        if largest > 2:
            step *= 2 / largest
        slope = float(np.dot(gradient, step))
        if slope <= 0 or not math.isfinite(slope):
            status = "numerical_guard"
            break
        accepted = False
        for backtrack in range(16):
            amount = 0.5**backtrack
            candidate = coefficients - amount * step
            candidate_loss, candidate_rate = _objective(design, target, candidate, ridge)
            if math.isfinite(candidate_loss) and candidate_loss <= loss - 1e-4 * amount * slope:
                accepted = True
                break
        if not accepted:
            status = "line_search_stalled"
            break
        improvement = loss - candidate_loss
        coefficients, loss, rate = candidate, candidate_loss, candidate_rate
        iterations = iteration + 1
        if improvement <= 1e-9 * (1 + abs(loss)):
            status, converged = "converged", True
            break
    if not np.isfinite(coefficients).all() or not math.isfinite(loss):
        raise ValueError("Rate-profile fitting produced non-finite parameters")
    final_gradient = np.einsum("np,n->p", design, rate - target, optimize=False) / count
    final_gradient += penalty * coefficients
    return coefficients, {"status": status, "converged": converged, "iterations": iterations,
                          "initial_loss": initial_loss, "final_loss": loss,
                          "gradient_max": float(np.abs(final_gradient).max())}


def _shared_indices(indices, feature_count):
    values = np.asarray(indices)
    if values.ndim != 1 or (values.size and values.dtype.kind not in "iu"):
        raise ValueError("shared_feature_indices must be a sequence of unique integer feature indices")
    if values.size and ((values < 0).any() or (values >= feature_count).any()
                        or len(np.unique(values)) != len(values)):
        raise ValueError("shared_feature_indices are duplicated or outside the feature dimension")
    return [int(value) for value in values]


def _borrow_shared_features(values, target, zones, kernel, bias, indices):
    """Borrow identifiable slopes without changing constant training log rates."""
    threshold = 1e-8
    summaries = []
    for feature in indices:
        means, variances, counts = [], [], []
        sources, constant_zones, missing_zones = [], [], []
        for zone in range(len(bias)):
            selected = zones == zone
            observations = int(selected.sum() * values.shape[1])
            counts.append(observations)
            if not observations:
                means.append(0.0)
                variances.append(0.0)
                missing_zones.append(zone)
                continue
            feature_values = values[selected, :, feature].reshape(-1)
            mean, variance = float(feature_values.mean()), float(feature_values.var())
            # The fitted intercept makes fitted mean rate equal to mean count
            # up to solver tolerance; empirical training mean avoids granting
            # artificial information to all-zero count zones' positive floor.
            mean_rate = float(target[selected].mean())
            means.append(mean)
            variances.append(variance)
            if variance > threshold:
                information = observations * variance * mean_rate
                if not math.isfinite(information):
                    raise ValueError("Shared-feature information weight is non-finite")
                if information > 0:
                    sources.append({"zone_index": zone, "observations": observations,
                                    "training_variance": variance, "training_mean_rate": mean_rate,
                                    "information_weight": information,
                                    "coefficient": float(kernel[feature, zone])})
            else:
                constant_zones.append(zone)
        information = sum(source["information_weight"] for source in sources)
        common = (sum(source["information_weight"] * source["coefficient"] for source in sources) / information
                  if information > 0 else None)
        borrowed = []
        if common is not None:
            for zone in constant_zones:
                previous = float(kernel[feature, zone])
                adjustment = -(common - previous) * means[zone]
                kernel[feature, zone] = common
                bias[zone] += adjustment
                borrowed.append({"zone_index": zone, "observations": counts[zone],
                                 "training_mean": means[zone], "training_variance": variances[zone],
                                 "previous_coefficient": previous, "coefficient": float(common),
                                 "bias_adjustment": float(adjustment)})
        summaries.append({"feature_index": feature,
                          "status": "borrowed" if borrowed else "no_constant_zones" if sources else "no_information",
                          "coefficient": float(common) if common is not None else None,
                          "variance_threshold": threshold,
                          "source_zone_indices": [source["zone_index"] for source in sources],
                          "borrowed_zone_indices": [entry["zone_index"] for entry in borrowed],
                          "missing_training_zone_indices": missing_zones,
                          "sources": sources, "borrowed": borrowed,
                          "information_weight": "observations * training_feature_variance * training_mean_count",
                          "training_preservation": "bias offsets the changed coefficient at the training feature mean; exact for constant inputs"})
    return summaries


def fit_rate_profile(profile, targets, zone_indices, zone_count: int, ridge: float = 0.02,
                     *, shared_feature_indices=()) -> dict:
    """Fit per-zone log rates on supplied [N,H,P] training features/counts.

    Minimized objective per zone is mean(mu - y*log(mu)) + ridge/2*||kernel||².
    Bias is not penalized. Zero-count zones have a finite positive rate floor;
    zones absent from the supplied rows use a documented global-mean fallback.
    Optional shared features borrow an information-weighted coefficient only
    for zones lacking feature variation, offsetting bias to preserve their
    constant-input training predictions. No evidence means no borrowed effect.
    Returned kernel [P,Z], bias [Z] and diagnostics contain JSON-native values.
    """
    values = _profile_array(profile)
    if isinstance(zone_count, bool) or not isinstance(zone_count, (int, np.integer)) or zone_count < 1:
        raise ValueError("zone_count must be a positive integer")
    if isinstance(ridge, bool) or not isinstance(ridge, (int, float)) or not math.isfinite(ridge) or ridge < 0:
        raise ValueError("ridge must be finite and nonnegative")
    target = np.asarray(targets, dtype=np.float64)
    if target.shape != values.shape[:2] or not np.isfinite(target).all() or (target < 0).any():
        raise ValueError("targets must be finite nonnegative [sample,horizon] counts")
    zones = _zones(zone_indices, len(values), zone_count)
    shared_indices = _shared_indices(shared_feature_indices, values.shape[-1])
    kernel = np.zeros((values.shape[-1], zone_count), dtype=np.float64)
    fallback_mean = max(float(target.mean()), 0.1)
    bias = np.full(zone_count, math.log(fallback_mean), dtype=np.float64)
    diagnostics = []
    for zone in range(zone_count):
        selected = zones == zone
        observations = int(selected.sum() * values.shape[1])
        if not observations:
            diagnostics.append({"zone_index": zone, "observations": 0, "status": "no_training_rows",
                                "converged": False, "iterations": 0, "fallback_mean": fallback_mean})
            continue
        features = values[selected].reshape(observations, values.shape[-1])
        counts = target[selected].reshape(-1)
        coefficients, zone_diagnostics = _fit_zone(features, counts, float(ridge))
        bias[zone], kernel[:, zone] = coefficients[0], coefficients[1:]
        diagnostics.append({"zone_index": zone, "observations": observations,
                            "target_mean": float(counts.mean()), **zone_diagnostics})
    shared = _borrow_shared_features(values, target, zones, kernel, bias, shared_indices)
    result = {"kernel": kernel.tolist(), "bias": bias.tolist(),
            "diagnostics": {"solver": "regularized_poisson_newton_backtracking", "ridge": float(ridge),
                            "max_iterations": MAX_ITERATIONS, "training_samples": len(values),
                            "training_observations": int(target.size), "feature_count": values.shape[-1],
                            "log_rate_clip": [MIN_LOG_RATE, MAX_LOG_RATE], "zero_rate_floor": ZERO_RATE,
                            "objective": "mean Poisson loss up to target-only constant plus L2 kernel penalty",
                            "zones": diagnostics}}
    if shared_indices:
        result["diagnostics"]["shared_features"] = shared
        result["diagnostics"]["objective_stage"] = "per_zone_fit_before_shared_borrowing"
    return result


def predict_rate_profile(profile, kernel, bias, zone_indices) -> np.ndarray:
    """Predict finite positive counts [N,H] with a saved per-zone profile."""
    values = _profile_array(profile)
    coefficients, intercept = np.asarray(kernel, dtype=np.float64), np.asarray(bias, dtype=np.float64)
    if values.shape[-1] == 0 and coefficients.size == 0 and intercept.ndim == 1:
        # JSON has no second dimension to preserve for an empty [0,Z] kernel.
        coefficients = coefficients.reshape(0, len(intercept))
    if (intercept.ndim != 1 or not len(intercept) or coefficients.shape != (values.shape[-1], len(intercept))
            or not np.isfinite(coefficients).all() or not np.isfinite(intercept).all()):
        raise ValueError("kernel/bias must be finite and match profile feature and zone dimensions")
    zones = _zones(zone_indices, len(values), len(intercept))
    eta = np.einsum("nhp,np->nh", values, coefficients[:, zones].T, optimize=False) + intercept[zones, None]
    if not np.isfinite(eta).all():
        raise ValueError("Rate profile produced a non-finite prediction")
    return np.exp(np.clip(eta, MIN_LOG_RATE, MAX_LOG_RATE)).astype(np.float32)
