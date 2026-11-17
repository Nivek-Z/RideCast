#!/usr/bin/env python3
"""Synthetic-only training, real spatial validation/test; never fabricate timestamps.

Install .[ml,dev,research], then see docs/TEACHER_DATA.md for the protocol.
Private inputs, row-level outputs, worlds and datasets must stay under var/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import mapping, shape
from shapely.ops import transform

from ridecast.data import write_dataset
from ridecast.simulation import create_world, simulate, validate_world

FEATURES = ["relative_population", "relative_employment_density", "relative_area"]
PROTOCOL = {
    "version": "teacher-spatial-transfer-v1",
    "evidence_status": "exploratory: full-file descriptive statistics/correlations inspected before protocol",
    "task": "relative spatial completed-trip volume; NOT temporal demand forecasting",
    "synthetic_seeds": list(range(1000, 1024)),
    "synthetic_validation_seeds": list(range(1018, 1024)),
    "synthetic_days": 3,
    "start": "2026-01-06T00:00:00Z",
    "real_split_seed": 42,
    "real_spatial_grid": 4,
    "alpha_candidates": [0.1, 1.0, 10.0, 100.0],
    "synthetic_selection_metric": "log_relative_volume_mse",
    "real_selection_metric": "total_variation_percent",
    "real_label_use": "validation selects a fixed candidate; test evaluated once after selection",
    "candidates": ["synthetic_ridge", "uniform", "population", "employment_density",
                   "employment_mass_proxy", "population_density"],
    "excluded_real_inputs": ["TripCount", "FALSE", "TRUE", "pct_share", "TripsSeconds_mean",
                             "TripMiles_mean", "Fare_mean", "TripTotal_mean", "GEO_ID",
                             "Pickup Census Tract"],
}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_geojson(archive):
    with zipfile.ZipFile(archive) as bundle:
        members = [i for i in bundle.infolist() if Path(i.filename).name == "chicago_tnp.geojson"]
        if len(members) != 1 or members[0].file_size > 32 * 1024**2:
            raise ValueError("Expected one chicago_tnp.geojson no larger than 32 MiB")
        raw = bundle.read(members[0])  # Read data only; no archive paths are extracted or executed.
    obj = json.loads(raw)
    if obj.get("type") != "FeatureCollection" or not obj.get("features"):
        raise ValueError("Expected a nonempty GeoJSON FeatureCollection")
    frame = pd.DataFrame([f["properties"] for f in obj["features"]])
    required = ["GEO_ID", "Pickup Census Tract", "TripCount", "FALSE", "TRUE",
                "total_population", "empoloyment_den"]  # Preserve source spelling.
    values = frame[required].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Required properties must be finite and nonnegative")
    if frame["GEO_ID"].duplicated().any() or not (frame["GEO_ID"] == frame["Pickup Census Tract"]).all():
        raise ValueError("Tract identifiers must be unique and consistent")
    if not np.equal(values[:, :5], np.floor(values[:, :5])).all():
        raise ValueError("Tract identifiers and trip counts must be integers")
    geometries = [shape(f["geometry"]) for f in obj["features"]]
    if any(not g.is_valid or g.is_empty or g.geom_type not in {"Polygon", "MultiPolygon"}
           for g in geometries):
        raise ValueError("Invalid or unsupported tract geometry")
    order = np.argsort(frame["GEO_ID"].to_numpy())
    features = [obj["features"][i] for i in order]
    frame = frame.iloc[order].reset_index(drop=True)
    return features, frame, {"file_sha256": hashlib.sha256(raw).hexdigest(),
                             "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                             "declared_crs": obj.get("crs"), "feature_count": len(features),
                             "property_count": len(frame.columns),
                             "missing_cells": int(frame.isna().sum().sum()),
                             "trip_total": int(frame["TripCount"].sum()),
                             "false_plus_true_equals_tripcount": bool(
                                 (frame["FALSE"] + frame["TRUE"] == frame["TripCount"]).all()),
                             "observation_period": None, "timestamps_available": False}


def spatial_split(features):
    """Assign entire coordinate blocks using geometry only, before reading targets."""
    centres = np.array([[shape(f["geometry"]).centroid.x, shape(f["geometry"]).centroid.y]
                        for f in features])
    grid = PROTOCOL["real_spatial_grid"]
    cells = np.zeros(len(features), dtype=int)
    for axis in range(2):
        edges = np.quantile(centres[:, axis], np.arange(1, grid) / grid)
        cells += np.searchsorted(edges, centres[:, axis]) * grid**axis
    blocks = np.unique(cells)
    blocks = np.random.default_rng(PROTOCOL["real_split_seed"]).permutation(blocks)
    validation = np.isin(cells, blocks[:len(blocks) // 2])
    if min(validation.sum(), (~validation).sum()) < 10:
        raise ValueError("Too few tracts for spatial validation/test")
    return validation, cells


def relative(values):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or values.mean() <= 0:
        raise ValueError("Expected finite nonnegative weights with positive mean")
    return values / values.mean()


def design(population, employment_density, area):
    return np.column_stack([np.log(np.maximum(relative(v), 0.001))
                            for v in (population, employment_density, area)])


def share_metrics(actual, weights):
    actual, weights = relative(actual), relative(weights)
    p, q = actual / actual.sum(), weights / weights.sum()
    return {"total_variation_percent": float(50 * np.abs(p - q).sum()),
            "relative_volume_mae": float(np.abs(actual - weights).mean()),
            "spearman": float(pd.Series(actual).rank().corr(pd.Series(weights).rank()))
            if np.std(weights) > 1e-12 and np.std(actual) > 1e-12 else None}


def fit_ridge(x, y, alpha):
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1e-6)
    matrix = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.diag([0.0] + [alpha] * x.shape[1])
    coef = np.linalg.solve(matrix.T @ matrix + penalty, matrix.T @ y)
    return {"mean": mean.tolist(), "scale": scale.tolist(), "coef": coef.tolist(), "alpha": alpha}


def predict_log(model, x):
    matrix = np.column_stack([np.ones(len(x)), (x - model["mean"]) / model["scale"]])
    # This tiny dot product avoids spurious macOS Accelerate floating-point
    # status warnings seen with NumPy matmul; independently cross-checked.
    prediction = np.sum(matrix * np.asarray(model["coef"]), axis=1)
    if not np.isfinite(prediction).all():
        raise ValueError("Nonfinite transfer-model prediction")
    return prediction


def train_synthetic(output, geod):
    xs, ys, training, summaries = [], [], [], []
    for seed in PROTOCOL["synthetic_seeds"]:
        world = create_world(rows=3, cols=3, seed=seed, timezone="America/Chicago")
        panel, orders, metadata = simulate(world, PROTOCOL["start"], PROTOCOL["synthetic_days"], seed)
        folder = output / "synthetic" / str(seed)
        write_dataset(folder, world, panel, orders, metadata, f"d_teacher_{seed}")
        props = [f["properties"] for f in world["zones"]["features"]]
        area = np.array([abs(geod.geometry_area_perimeter(shape(f["geometry"]))[0])
                         for f in world["zones"]["features"]])
        population = np.array([p["population"] for p in props])
        employment_density = np.array([p["jobs"] for p in props]) / area
        volume = panel.groupby("zone_id")["completed"].sum().reindex([p["zone_id"] for p in props]).to_numpy()
        xs.append(design(population, employment_density, area))
        ys.append(np.log(np.maximum(relative(volume), 0.001)))
        training.extend([seed not in PROTOCOL["synthetic_validation_seeds"]] * len(props))
        summaries.append({"seed": seed, "zone_count": len(props), "requests": len(orders),
                          "completed": int(volume.sum()), "panel_rows": len(panel)})
        print(json.dumps({"stage": "synthetic_training_data", "seed": seed, "orders": len(orders)}), flush=True)
    x, y, training = np.concatenate(xs), np.concatenate(ys), np.array(training)
    candidates = [fit_ridge(x[training], y[training], alpha) for alpha in PROTOCOL["alpha_candidates"]]
    for model in candidates:
        model["synthetic_validation_mse"] = float(np.mean((predict_log(model, x[~training]) - y[~training])**2))
    selected = min(candidates, key=lambda m: m["synthetic_validation_mse"])
    save(output / "synthetic-model.json", {"features": FEATURES, "selected": selected, "candidates": candidates})
    return selected, {"worlds": len(summaries), "train_zones": int(training.sum()),
                      "validation_zones": int((~training).sum()), "world_summaries": summaries,
                      "selected_alpha": selected["alpha"],
                      "validation_log_relative_mse": selected["synthetic_validation_mse"]}


def teacher_smoke(features, frame, scores, output, transformer, adapter_metadata=None):
    # Selection is by tract ID order, independent of real trip labels.
    indices = np.linspace(0, len(features) - 1, 9, dtype=int)
    rates = relative(scores[indices]) * 10  # Arbitrary demo scale: period/exposure is unknown.
    zones = []
    for i, rate in zip(indices, rates):
        zones.append({"type": "Feature", "geometry": mapping(transform(transformer.transform,
                                                                                shape(features[i]["geometry"]))),
                      "properties": {"zone_id": str(int(frame.iloc[i]["GEO_ID"])), "kind": "mixed",
                                     "population": int(frame.iloc[i]["total_population"]), "jobs": 0,
                                     "base_demand": float(rate), "attractiveness": 1}})
    identity_input = zones if adapter_metadata is None else {"zones": zones, "adapter_metadata": adapter_metadata}
    identity = hashlib.sha256(json.dumps(identity_input, sort_keys=True).encode()).hexdigest()[:16]
    world = validate_world({"name": "Chicago geometry / synthetic dynamics demonstration", "seed": 42,
                            "world_id": f"w_{identity}", "timezone": "America/Chicago", "bucket_minutes": 60,
                            "zones": {"type": "FeatureCollection", "features": zones},
                            "calendar": {"holiday_dates": [], "working_dates": []}, "events": [],
                            "metadata": {"fictional": False, "procedural_events": False,
                                         "generator_version": PROTOCOL["version"],
                                         "evolution": {"version": "absolute-time-v2"},
                                         "source_crs_status": "assumed_not_confirmed",
                                         "description": "Real tract geometry; all dynamics, supply and OD are synthetic.",
                                         "jobs_observed": False, "land_use_observed": False,
                                         "demand_rate_scale": "arbitrary mean 10 per hour; not a real estimate",
                                         **(adapter_metadata or {})}})
    start = PROTOCOL["start"]
    panel, orders, metadata = simulate(world, start, 2, 42)
    write_dataset(output / "teacher-geometry-demo", world, panel, orders, metadata, "d_teacher_geometry_demo")
    overlap = (pd.Timestamp(start) + pd.Timedelta(days=1)).isoformat()
    op, oo, _ = simulate(world, overlap, 1, 42)
    pd.testing.assert_frame_equal(panel[panel.timestamp >= pd.Timestamp(overlap)].reset_index(drop=True), op)
    pd.testing.assert_frame_equal(orders[orders.requested_at >= pd.Timestamp(overlap)].reset_index(drop=True), oo)
    stress, stress_orders, sm = simulate(world, start, 2, 42, {"supply_multiplier": 0.5})
    write_dataset(output / "teacher-supply-stress", world, stress, stress_orders, sm, "d_teacher_supply_stress")
    return {"zones": 9, "panel_rows": len(panel), "synthetic_requests": len(orders),
            "baseline_synthetic_completed": int(panel.completed.sum()),
            "half_supply_synthetic_completed": int(stress.completed.sum()),
            "overlap_panel_exact": True, "overlap_orders_exact": True,
            "real_supply_or_cancellation_validation": False,
            "rates_use_real_tripcount": False,
            "rates_fit_real_training_labels": bool((adapter_metadata or {}).get("rates_fit_real_training_labels", False))}


def run(args):
    from pyproj import Geod, Transformer
    from pyproj import __version__ as pyproj_version

    output = args.output.resolve()
    if "var" not in output.relative_to(Path.cwd().resolve()).parts:
        raise ValueError("Private experiment output must be under this repository's ignored var/")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new or empty output directory; preserve previous experiments")
    output.mkdir(parents=True, exist_ok=True)
    save(output / "protocol.json", PROTOCOL)  # Freeze scoring, not a claim of preregistration before exploration.
    features, frame, audit = load_geojson(args.archive)
    validation, blocks = spatial_split(features)
    split = pd.DataFrame({"tract_id": frame.GEO_ID.astype("int64"), "block": blocks,
                          "partition": np.where(validation, "validation", "test")})
    split.to_csv(output / "real-partitions.csv", index=False)
    transformer = Transformer.from_crs(args.source_crs, "EPSG:4326", always_xy=True)
    geod = Geod(ellps="WGS84")
    geometries = [transform(transformer.transform, shape(f["geometry"])) for f in features]
    bounds = [min(g.bounds[0] for g in geometries), min(g.bounds[1] for g in geometries),
              max(g.bounds[2] for g in geometries), max(g.bounds[3] for g in geometries)]
    if not (-88.5 < bounds[0] < bounds[2] < -87 and 41 < bounds[1] < bounds[3] < 43):
        raise ValueError("Explicit source CRS does not produce plausible Chicago coordinates")
    area = np.array([abs(geod.geometry_area_perimeter(g)[0]) for g in geometries])
    population = frame.total_population.to_numpy(dtype=float)
    employment = frame.empoloyment_den.to_numpy(dtype=float)
    model, synthetic = train_synthetic(output, geod)
    x = design(population, employment, area)
    scores = {"synthetic_ridge": np.exp(np.clip(predict_log(model, x), -12, 12)),
              "uniform": np.ones(len(frame)), "population": population,
              "employment_density": employment, "employment_mass_proxy": employment * area,
              "population_density": population / area}
    actual = frame.TripCount.to_numpy(dtype=float)
    validation_metrics = {name: share_metrics(actual[validation], values[validation])
                          for name, values in scores.items()}
    selected = min(scores, key=lambda name: validation_metrics[name][PROTOCOL["real_selection_metric"]])
    # Persist the decision before accessing real test targets in any metric.
    save(output / "selection.json", {"selected": selected, "validation": validation_metrics})
    test_metrics = {name: share_metrics(actual[~validation], values[~validation]) for name, values in scores.items()}
    predictions = split.assign(actual_tripcount=actual)
    for name, values in scores.items():
        predictions[name] = values
    predictions.to_csv(output / "private-predictions.csv", index=False)
    smoke = teacher_smoke(features, frame, scores[selected], output, transformer)
    return {"protocol": PROTOCOL, "source": audit,
            "projection": {"source_crs": args.source_crs, "status": "assumed_not_confirmed",
                           "transformed_wgs84_bounds": bounds},
            "split": {"validation_tracts": int(validation.sum()), "test_tracts": int((~validation).sum()),
                      "validation_blocks": sorted(np.unique(blocks[validation]).tolist()),
                      "test_blocks": sorted(np.unique(blocks[~validation]).tolist()),
                      "assignment_sha256": hashlib.sha256((output / "real-partitions.csv").read_bytes()).hexdigest()},
            "synthetic_training": synthetic, "validation": validation_metrics,
            "selected_using_validation": selected, "test": test_metrics, "simulation_smoke": smoke,
            "environment": {"python": platform.python_version(), "numpy": np.__version__,
                            "pandas": pd.__version__, "pyproj": pyproj_version},
            "limitations": [
                "Full-file descriptive statistics and target correlations were inspected before this protocol; the spatial test is exploratory, not a pristine blind test.",
                "No timestamps or observation period: cannot validate temporal GRU, hourly counts or real MAE.",
                "TripCount is treated as realized trips, pending source confirmation; requests/cancellations are unobserved.",
                "Synthetic model is a static log-relative-volume ridge, not the application's temporal GRU.",
                "All fitted coefficients and standardization use synthetic training worlds only; real labels select candidates.",
                "Common features: population, dimensionless employment density and area; density definitions may differ.",
                "Per-world covariate normalization uses unlabeled features of the complete target city (transductive).",
                "Synthetic training cities are fictional, narrow in scale and geography; covariate/concept shift is substantial.",
                "EPSG:3435 is an explicit hypothesis; missing source CRS prevents confirmed positional accuracy.",
                "One spatial split in one city; adjacent blocks remain dependent; no temporal or cross-city generalization claim.",
                "Share metrics remove unknown exposure/total volume; they do not evaluate absolute counts.",
                "Alternative proxies use real covariates, no real training labels; validation choice is not zero-shot transfer.",
                "Synthetic supply/OD/weather/passengers/land use are assumptions; stress results are not real causal effects.",
                "Only nine tracts are smoke-tested on real geometry, under the existing 100-zone limit.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-crs", required=True, help="Explicit hypothesis; do not silently infer missing CRS")
    parser.add_argument("--output", type=Path, default=Path("var/teacher-data/transfer-v1"))
    args = parser.parse_args()
    started = time.monotonic()
    report = run(args)
    report.update(created_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic()-started, 3))
    report["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save(args.output / "summary.json", report)
    print(json.dumps({"selected": report["selected_using_validation"], "validation": report["validation"],
                      "test": report["test"], "output": str(args.output.resolve())}), flush=True)


if __name__ == "__main__":
    main()
