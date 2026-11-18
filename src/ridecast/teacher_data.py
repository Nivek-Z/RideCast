"""Read the original teacher archive and preserve the geometry-only research split."""

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import shape

SPATIAL_GRID = 4
SPLIT_SEED = 42
FEATURES = ["population", "employment_density", "area_m2", "network_density", "income", "no_vehicle_share",
            "education_share", "young_adult_share", "household_size", "job_entropy", "transit_distance",
            "employment_near_transit", "transit_frequency", "unemployment_percent", "commute_minutes"]


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
    grid = SPATIAL_GRID
    cells = np.zeros(len(features), dtype=int)
    for axis in range(2):
        edges = np.quantile(centres[:, axis], np.arange(1, grid) / grid)
        cells += np.searchsorted(edges, centres[:, axis]) * grid**axis
    blocks = np.unique(cells)
    blocks = np.random.default_rng(SPLIT_SEED).permutation(blocks)
    validation = np.isin(cells, blocks[:len(blocks) // 2])
    if min(validation.sum(), (~validation).sum()) < 10:
        raise ValueError("Too few tracts for spatial validation/test")
    return validation, cells


def relative(values):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or values.mean() <= 0:
        raise ValueError("Expected finite nonnegative weights with positive mean")
    return values / values.mean()


def share_metrics(actual, weights):
    actual, weights = relative(actual), relative(weights)
    p, q = actual / actual.sum(), weights / weights.sum()
    return {"total_variation_percent": float(50 * np.abs(p - q).sum()),
            "relative_volume_mae": float(np.abs(actual - weights).mean()),
            "spearman": float(pd.Series(actual).rank().corr(pd.Series(weights).rank()))
            if np.std(weights) > 1e-12 and np.std(actual) > 1e-12 else None}


def spatial_features(features, frame, source_crs):
    from pyproj import Geod, Transformer
    from shapely.ops import transform

    transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    geod = Geod(ellps="WGS84")
    area = np.array([
        abs(geod.geometry_area_perimeter(transform(transformer.transform, shape(f["geometry"])))[0])
        for f in features
    ])
    return np.column_stack([
        frame.total_population, frame.empoloyment_den, area, frame.network_den,
        frame.median_household_income, frame.pct_no_vehicle, frame.pct_bachelorORhigher,
        frame.pct_18to34, frame.household_size_mean, frame.job_entropy, frame.dist_to_tran,
        frame.employed_near_tran, frame.freq_tran, frame.pct_unemployed_16, frame.work_commute_mean,
    ])
