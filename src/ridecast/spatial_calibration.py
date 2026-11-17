"""Exploratory static regional rules; never a substitute for temporal observations.

Calibrate on real TRAINING regions only. This module does not change the
absolute-time-v2 simulator, infer cancellations, or create observed time series.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

VERSION = "calibrated-spatial-world-v1"


def _finite_matrix(values):
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Regional attributes must be a finite nonnegative matrix")
    return values


def transform_features(rule, raw):
    raw = _finite_matrix(raw)
    reference = np.asarray(rule["reference"])
    if raw.shape[1] != len(reference):
        raise ValueError("Feature schema mismatch")
    return np.log1p(raw / reference)


def _fit_log_rule(x, y, alpha, active=None):
    """Huber IRLS with average-loss ridge, and bounded smooth log slopes."""
    active = np.ones(x.shape[1], dtype=bool) if active is None else np.asarray(active, dtype=bool)
    slopes = np.zeros(x.shape[1])
    if not active.any():
        return float(np.median(y)), slopes
    xa = x[:, active]
    centre = np.median(xa, axis=0)
    scale = np.maximum((np.quantile(xa, .75, axis=0) - np.quantile(xa, .25, axis=0)) / 1.349, .1)
    matrix = np.column_stack([np.ones(len(x)), (xa - centre) / scale])
    penalty = np.diag([0.] + [alpha] * xa.shape[1])
    weights = np.ones(len(y))
    coef = np.zeros(matrix.shape[1])
    for _ in range(30):
        gram = np.einsum("ni,nj,n->ij", matrix, matrix, weights) / len(y) + penalty
        rhs = np.einsum("ni,n,n->i", matrix, y, weights) / len(y)
        updated = np.linalg.solve(gram, rhs)
        residual = y - np.sum(matrix * updated, axis=1)
        sigma = max(float(1.4826 * np.median(np.abs(residual - np.median(residual)))), .05)
        weights = np.minimum(1., 1.35 * sigma / np.maximum(np.abs(residual), 1e-12))
        if np.max(np.abs(updated - coef)) < 1e-7:
            coef = updated
            break
        coef = updated
    slopes[active] = np.clip(coef[1:] / scale, -2., 2.)
    # Re-estimate the intercept after the slope bound; preserve target-scale equivariance.
    intercept = float(np.average(y - np.sum(x * slopes, axis=1), weights=weights))
    return intercept, slopes


def fit_stable_rule(raw, counts, groups, names, *, alpha=.05, seed=42, bootstraps=40):
    raw = _finite_matrix(raw)
    counts, groups = np.asarray(counts, dtype=float), np.asarray(groups)
    if (len(raw) != len(counts) or len(raw) != len(groups) or len(names) != raw.shape[1]
            or not np.isfinite(counts).all() or (counts <= 0).any() or alpha <= 0 or bootstraps < 4):
        raise ValueError("Invalid training data or calibration configuration")
    reference = np.array([np.median(column[column > 0]) if (column > 0).any() else 1.
                          for column in raw.T])
    rule = {"version": VERSION, "features": list(names), "reference": reference.tolist(), "alpha": alpha}
    x, y = transform_features(rule, raw), np.log(counts)
    blocks, rng, sampled = np.unique(groups), np.random.default_rng(seed), []
    if len(blocks) < 3:
        raise ValueError("Stability fitting requires at least three training spatial blocks")
    for _ in range(bootstraps):
        draw = rng.choice(blocks, size=len(blocks), replace=True)
        indices = np.concatenate([np.flatnonzero(groups == block) for block in draw])
        sampled.append(_fit_log_rule(x[indices], y[indices], alpha)[1])
    sampled = np.asarray(sampled)
    sign_support = np.maximum((sampled > 0).mean(axis=0), (sampled < 0).mean(axis=0))
    median = np.median(sampled, axis=0)
    dispersion = (np.quantile(sampled, .75, axis=0) - np.quantile(sampled, .25, axis=0)) / (np.abs(median) + .05)
    active = (sign_support >= .9) & (dispersion <= 1.) & (np.abs(median) >= .05)
    intercept, slopes = _fit_log_rule(x, y, alpha, active)
    log_prediction = intercept + np.sum(x * slopes, axis=1)
    residual = y - log_prediction
    covariance = np.atleast_2d(np.cov(x, rowvar=False))
    covariance = .75 * covariance + .25 * np.diag(np.diag(covariance))
    rule.update(intercept=intercept, slopes=slopes.tolist(), active=active.tolist(),
                training_rows=len(raw), training_blocks=len(blocks), bootstraps=bootstraps,
                sign_support=sign_support.tolist(), bootstrap_slope_median=median.tolist(),
                bootstrap_slope_q10=np.quantile(sampled, .1, axis=0).tolist(),
                bootstrap_slope_q90=np.quantile(sampled, .9, axis=0).tolist(),
                relative_coefficient_iqr=dispersion.tolist(),
                log_feature_mean=x.mean(axis=0).tolist(), log_feature_covariance=covariance.tolist(),
                log_feature_centre=np.median(x, axis=0).tolist(),
                log_feature_scale=np.maximum(x.std(axis=0), .1).tolist(),
                support_lower=x.min(axis=0).tolist(), support_upper=x.max(axis=0).tolist(),
                residual_log_sigma=float(np.clip(1.4826 * np.median(np.abs(residual-np.median(residual))), .05, 1.5)))
    rule["fingerprint"] = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()
    return rule


def predict_rule(rule, raw):
    x = transform_features(rule, raw)
    log_prediction = rule["intercept"] + np.sum(x * rule["slopes"], axis=1)
    if not np.isfinite(log_prediction).all() or (np.abs(log_prediction) > 40).any():
        raise ValueError("Prediction outside supported numeric range; calibration needs review")
    return np.exp(log_prediction)


def support_diagnostics(rule, raw):
    x = transform_features(rule, raw)
    outside = (x < np.asarray(rule["support_lower"])-1e-12) | (x > np.asarray(rule["support_upper"])+1e-12)
    return {"outside_any_training_range": int(outside.any(axis=1).sum()), "rows": len(raw),
            "outside_by_feature": dict(zip(rule["features"], outside.sum(axis=0).tolist())),
            "meaning": "range screening only; being inside ranges does not establish joint support or future accuracy"}


def _rng(seed, world, zone, domain):
    digest = hashlib.sha256(f"{VERSION}|{seed}|{world}|{zone}|{domain}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little"))


def generate_regions(rule, world_indices, *, seed=42, zones=64, mean_predictor=None):
    """Immutable, partition-repeatable STATIC virtual regions, not time buckets.

    Covariates come from a shrunk joint log distribution fitted on real training
    regions. Label noise is a residual-based assumption, not a recovered process.
    """
    world_indices = list(world_indices)
    if zones < 1 or len(set(world_indices)) != len(world_indices):
        raise ValueError("Expected positive zone count and unique virtual world indices")
    eigenvalues, eigenvectors = np.linalg.eigh(rule["log_feature_covariance"])
    root = eigenvectors * np.sqrt(np.maximum(eigenvalues, 0))
    records = []
    for world in world_indices:
        shift = np.sum(root * _rng(seed, world, -1, "world_features").normal(size=len(root)), axis=1) * np.sqrt(.15)
        for zone in range(zones):
            rng = _rng(seed, world, zone, "region")
            latent = np.sum(root * rng.normal(size=len(root)), axis=1) * np.sqrt(.85)
            x = np.clip(np.asarray(rule["log_feature_mean"]) + shift + latent,
                        rule["support_lower"], rule["support_upper"])
            raw = np.asarray(rule["reference"]) * np.expm1(x)
            expected = float(predict_rule(rule, raw[None, :])[0]) if mean_predictor is None else 1.
            sigma = rule["residual_log_sigma"]
            rate = expected * np.exp(rng.normal(0, sigma) - .5*sigma**2)
            count = int(rng.poisson(rate))
            records.append({"world_index": world, "zone_index": zone, "seed": seed,
                            **dict(zip(rule["features"], raw.tolist())),
                            "completed_trips": count, "expected_completed_trips": expected})
    result = pd.DataFrame(records)
    if mean_predictor is not None:
        expected = np.asarray(mean_predictor(result[rule["features"]].to_numpy()), dtype=float)
        if expected.shape != (len(result),) or not np.isfinite(expected).all() or (expected <= 0).any():
            raise ValueError("Invalid external calibrated mean predictions")
        counts = []
        for world, zone, mean in zip(result.world_index, result.zone_index, expected):
            rng = _rng(seed, world, zone, "external_calibrated_label")
            sigma = rule["residual_log_sigma"]
            counts.append(int(rng.poisson(mean*np.exp(rng.normal(0, sigma)-.5*sigma**2))))
        result["expected_completed_trips"] = expected
        result["completed_trips"] = counts
    return result


def _student_basis(rule, raw):
    z = (transform_features(rule, raw) - rule["log_feature_centre"]) / rule["log_feature_scale"]
    columns = [np.ones(len(z)), *z.T]
    columns += [z[:, i] * z[:, j] for i in range(z.shape[1]) for j in range(i, z.shape[1])]
    return np.column_stack(columns)


def fit_student(rule, raw, counts, *, alpha=.01, weights=None):
    matrix = _student_basis(rule, raw)
    counts = np.asarray(counts, dtype=float)
    if len(counts) != len(matrix) or not np.isfinite(counts).all() or (counts < 0).any():
        raise ValueError("Invalid student training labels")
    weights = np.ones(len(matrix)) if weights is None else np.asarray(weights, dtype=float)
    if len(weights) != len(matrix) or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("Invalid student sample weights")
    weights = weights / weights.sum()
    y = np.log(np.maximum(counts, 1))
    penalty = np.diag([0.] + [alpha] * (matrix.shape[1]-1))
    gram = np.einsum("ni,nj,n->ij", matrix, matrix, weights) + penalty
    rhs = np.einsum("ni,n,n->i", matrix, y, weights)
    coefficients = np.linalg.solve(gram, rhs)
    return {"alpha": alpha, "coefficients": coefficients.tolist(), "training_rows": len(matrix),
            "transform_rule_fingerprint": rule["fingerprint"]}


def predict_student(rule, student, raw):
    if student["transform_rule_fingerprint"] != rule["fingerprint"]:
        raise ValueError("Student/rule transform mismatch")
    values = np.sum(_student_basis(rule, raw) * student["coefficients"], axis=1)
    if not np.isfinite(values).all() or (np.abs(values) > 40).any():
        raise ValueError("Student prediction outside supported numeric range")
    return np.exp(values)
