"""Optional research models for static trip shares; no temporal forecasts.

Every fitted transform sees training regions only. Optional sklearn imports are
kept here so the regular application does not need the research extra.
"""

from __future__ import annotations

import numpy as np

from .spatial_calibration import (
    fit_stable_rule,
    fit_student,
    generate_regions,
    predict_rule,
    predict_student,
)


def candidate_configs():
    configs = [{"name": "uniform", "family": "uniform", "size": 3},
               {"name": "employment_density", "family": "employment", "size": 3}]
    for size in [3, 6, 15]:
        for alpha in [.01, .1, 1.]:
            configs.append({"name": f"poisson_{size}_a{alpha}", "family": "poisson", "size": size, "alpha": alpha})
    for size in [6, 15]:
        for alpha in [.1, 1.]:
            configs.append({"name": f"spline_{size}_a{alpha}", "family": "spline", "size": size, "alpha": alpha})
    for loss in ["poisson", "log_squared"]:
        for leaves in [15, 30]:
            configs.append({"name": f"monotone_boost_{loss}_leaf{leaves}", "family": "boost", "size": 15,
                            "loss": loss, "min_samples_leaf": leaves})
    for alpha in [.1, 1.]:
        configs.append({"name": f"stable_power_a{alpha}", "family": "power", "size": 3, "alpha": alpha})
    for fraction in [None, 1., .2, .5]:
        label = "real_quadratic" if fraction is None else f"synthetic_fraction{fraction}"
        configs.append({"name": label, "family": "student", "size": 3, "synthetic_fraction": fraction})
    return configs


def fit_candidate(config, raw, counts, blocks, names):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import PoissonRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import SplineTransformer, StandardScaler

    raw = np.asarray(raw, dtype=float)[:, :config["size"]]
    counts = np.asarray(counts, dtype=float)
    if not np.isfinite(raw).all() or (raw < 0).any() or not np.isfinite(counts).all() or (counts <= 0).any():
        raise ValueError("Expected finite nonnegative attributes and positive training counts")
    family = config["family"]
    fitted = {"config": config, "mean_count": float(counts.mean()),
              "feature_names": list(names[:config["size"]]),
              "target_semantics": "aggregate_completed_trips_assumed", "model_version": "spatial-candidates-v1"}
    if family in {"uniform", "employment"}:
        return fitted
    if family in {"power", "student"}:
        rule = fit_stable_rule(raw, counts, blocks, names[:config["size"]], alpha=config.get("alpha", 1.), bootstraps=40)
        fitted["rule"] = rule
        if family == "student":
            fraction = config["synthetic_fraction"]
            if fraction is None:
                x, y, weights = raw, counts, None
            else:
                virtual = generate_regions(rule, range(16), seed=42, zones=64)
                vx, vy = virtual[rule["features"]].to_numpy(), virtual.completed_trips.to_numpy()
                x, y = np.concatenate([raw, vx]), np.concatenate([counts, vy])
                weights = np.r_[np.full(len(raw), (1-fraction)/len(raw)), np.full(len(vx), fraction/len(vx))]
            fitted["student"] = fit_student(rule, x, y, alpha=.01, weights=weights)
        return fitted
    reference = np.array([np.median(c[c > 0]) if (c > 0).any() else 1. for c in raw.T])
    fitted["reference"] = reference
    x = np.log1p(raw / reference)
    y = counts / fitted["mean_count"]
    if family == "poisson":
        estimator = make_pipeline(StandardScaler(), PoissonRegressor(alpha=config["alpha"], max_iter=1000, tol=1e-7))
    elif family == "spline":
        estimator = make_pipeline(SplineTransformer(n_knots=4, degree=3, knots="quantile",
                                                     extrapolation="linear", include_bias=False),
                                  StandardScaler(), PoissonRegressor(alpha=config["alpha"], max_iter=1000, tol=1e-7))
    elif family == "boost":
        log_loss = config["loss"] == "log_squared"
        if log_loss:
            y = np.log(counts)
        estimator = HistGradientBoostingRegressor(loss="squared_error" if log_loss else "poisson",
                                                 monotonic_cst=[1, 1] + [0]*(raw.shape[1]-2),
                                                 learning_rate=.05, max_iter=160, max_leaf_nodes=7,
                                                 min_samples_leaf=config["min_samples_leaf"],
                                                 l2_regularization=1., early_stopping=False,
                                                 interaction_cst="pairwise", random_state=42)
    else:
        raise ValueError("Unknown research model family")
    estimator.fit(x, y)
    fitted["estimator"] = estimator
    return fitted


def predict_candidate(fitted, raw):
    config = fitted["config"]
    raw = np.asarray(raw, dtype=float)[:, :config["size"]]
    family = config["family"]
    if family == "uniform":
        values = np.ones(len(raw))
    elif family == "employment":
        values = raw[:, 1]
    elif family == "power":
        values = predict_rule(fitted["rule"], raw)
    elif family == "student":
        values = predict_student(fitted["rule"], fitted["student"], raw)
    else:
        transformed = np.log1p(raw/fitted["reference"])
        if family in {"poisson", "spline"}:
            pipeline = fitted["estimator"]
            design = pipeline[:-1].transform(transformed)
            glm = pipeline[-1]
            # Cross-checked against sklearn; avoid macOS matmul status warnings.
            values = np.exp(np.sum(design*glm.coef_, axis=1)+glm.intercept_)*fitted["mean_count"]
        else:
            values = fitted["estimator"].predict(transformed)
            values = np.exp(values) if config["loss"] == "log_squared" else values*fitted["mean_count"]
    if not np.isfinite(values).all() or (values < 0).any() or not (values > 0).any():
        raise ValueError("Invalid predictions; do not silently repair model output")
    return values


def joint_support_diagnostic(training, groups, new):
    """Unweighted joint-neighbor diagnostic inspired by AOA, not its full method.

    Threshold is learned from training spatial folds, without new targets.
    """
    from sklearn.neighbors import NearestNeighbors
    from sklearn.preprocessing import StandardScaler

    training, groups, new = np.asarray(training), np.asarray(groups), np.asarray(new)

    def distance(source, query):
        reference = np.array([np.median(c[c > 0]) if (c > 0).any() else 1. for c in source.T])
        scaler = StandardScaler().fit(np.log1p(source/reference))
        sx, qx = scaler.transform(np.log1p(source/reference)), scaler.transform(np.log1p(query/reference))
        return NearestNeighbors(n_neighbors=1).fit(sx).kneighbors(qx)[0][:, 0] / np.sqrt(source.shape[1])

    oof = np.zeros(len(training))
    for group in np.unique(groups):
        held_out = groups == group
        oof[held_out] = distance(training[~held_out], training[held_out])
    threshold = float(np.quantile(oof, .95))
    new_distance = distance(training, new)
    return {"threshold_training_fold_q95": threshold, "outside_count": int((new_distance > threshold).sum()),
            "rows": len(new), "distance_median": float(np.median(new_distance)),
            "distance_max": float(new_distance.max()),
            "meaning": "unweighted standardized nearest-neighbor screen, not the full weighted AOA algorithm or a guarantee"}
