"""Executable protocols for the defense and spatial validation arena."""

import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import shape
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import PoissonRegressor
from threadpoolctl import threadpool_limits

from ridecast.research_schema import ArenaConfig
from ridecast.spatial_calibration import fit_stable_rule, generate_regions
from ridecast.spatial_models import fit_candidate, predict_candidate
from ridecast.teacher_data import FEATURES, load_geojson, share_metrics, spatial_features, spatial_split

PROTOCOL = "defense-spatial-v1"
SOURCE_CRS = "EPSG:3435"
SOURCE_DIGEST = "1016525c53ea54e608fee0a6d6fc7cd6f0599bdcabc08622688be615bae569e0"
FORMAL = ArenaConfig(worlds_per_seed=64, teacher_alpha=1.0, seeds=[7, 42, 99])
REGIONS_PER_WORLD = 64
STUDENT_ALPHAS = (0., .001, .01)


@dataclass
class Chicago:
    features: list
    ids: list
    raw: np.ndarray
    counts: np.ndarray
    development: np.ndarray
    blocks: np.ndarray
    digest: str

    @classmethod
    def load(cls, archive: Path):
        features, frame, audit = load_geojson(archive)
        if audit["file_sha256"] != SOURCE_DIGEST:
            raise ValueError("归档中的 GeoJSON 和历史实验的源数据校验值不一致")
        development, blocks = spatial_split(features)
        return cls(features, frame.GEO_ID.astype(str).tolist(), spatial_features(features, frame, SOURCE_CRS),
                   frame.TripCount.to_numpy(dtype=float), development, blocks, audit["file_sha256"])


def variation(actual, predicted):
    return float(50 * np.abs(actual / actual.sum() - predicted / predicted.sum()).sum())


def calibrate(raw, counts, blocks, alpha):
    config = {"name": "smooth_teacher", "family": "spline", "size": len(FEATURES), "alpha": alpha}
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        teacher = fit_candidate(config, raw, counts, blocks, FEATURES)
    rule = fit_stable_rule(raw, counts, blocks, FEATURES, alpha=1., bootstraps=40)
    residual = np.log(counts / predict_candidate(teacher, raw))
    rule["residual_log_sigma"] = float(np.clip(1.4826 * np.median(np.abs(residual - np.median(residual))), .05, 1.5))
    # The power-rule fingerprint predates replacement of its mean and dispersion.
    rule.pop("fingerprint")
    return teacher, rule


def generate(teacher, rule, worlds, seeds):
    return pd.concat([
        generate_regions(rule, range(worlds), seed=seed, zones=REGIONS_PER_WORLD,
                         mean_predictor=lambda raw: predict_candidate(teacher, raw))
        for seed in seeds
    ], ignore_index=True)


def student_design(teacher, raw):
    return teacher["estimator"][:-1].transform(np.log1p(raw / teacher["reference"]))


def predict_student(model, design, scale):
    return np.exp(np.sum(design * model.coef_, axis=1) + model.intercept_) * scale


def train_student(teacher, generated, worlds):
    basis = student_design(teacher, generated[FEATURES].to_numpy())
    training = generated.world_index.to_numpy() < 3 * worlds // 4
    labels = generated.completed_trips.to_numpy()[training]
    scale = float(labels.mean())
    validation_mean = generated.expected_completed_trips.to_numpy()[~training]
    candidates, fidelity = {}, {}
    for alpha in STUDENT_ALPHAS:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ConvergenceWarning)
            student = PoissonRegressor(alpha=alpha, max_iter=2000, tol=1e-7).fit(basis[training], labels / scale)
        if any(issubclass(w.category, ConvergenceWarning) for w in captured):
            continue
        prediction = predict_student(student, basis[~training], scale)
        fidelity[str(alpha)] = variation(validation_mean, prediction)
        candidates[str(alpha)] = student
    if not candidates:
        raise ValueError("所有合成训练候选均未收敛")
    alpha = min(candidates, key=fidelity.__getitem__)
    return candidates[alpha], scale, float(alpha), fidelity


def geometry_paths(features):
    geoms = [shape(feature["geometry"]).simplify(100, preserve_topology=True) for feature in features]
    bounds = np.array([geom.bounds for geom in geoms])
    left, bottom = bounds[:, :2].min(axis=0)
    right, top = bounds[:, 2:].max(axis=0)
    scale = min(416 / (right - left), 476 / (top - bottom))
    offset_x = (440 - (right - left) * scale) / 2
    offset_y = (500 - (top - bottom) * scale) / 2

    def ring_path(ring):
        points = "L".join(f"{(x-left)*scale + offset_x:.1f},{(top-y)*scale + offset_y:.1f}" for x, y in ring.coords)
        return f"M{points}Z"

    paths = []
    for geom in geoms:
        polygons = (geom,) if geom.geom_type == "Polygon" else geom.geoms
        paths.append("".join(ring_path(r) for polygon in polygons for r in (polygon.exterior, *polygon.interiors)))
    return paths


def map_series(data, mask, prediction, teacher):
    def shares(values):
        return (values / values.sum() * 100).tolist()

    return {
        "ids": data.ids, "paths": geometry_paths(data.features),
        "development": data.development.tolist(), "blocks": data.blocks.tolist(),
        "regions": np.flatnonzero(mask).tolist(),
        "actual": shares(data.counts[mask]), "predicted": shares(prediction), "teacher": shares(teacher),
    }


def reproduction(data, progress):
    development, test = data.development, ~data.development
    progress("calibration", .18, "在 364 个真实开发区拟合平滑规则、生成分布和噪声尺度")
    teacher, rule = calibrate(data.raw[development], data.counts[development], data.blocks[development], FORMAL.teacher_alpha)
    progress("generation", .40, "用固定种子生成 192 个静态虚拟世界")
    generated = generate(teacher, rule, FORMAL.worlds_per_seed, FORMAL.seeds)
    progress("training", .73, "只用生成标签拟合学生，在生成验证世界选择正则强度")
    model, scale, alpha, fidelity = train_student(teacher, generated, FORMAL.worlds_per_seed)
    progress("evaluation", .90, "在 431 个空间留出区计算 TV 和逐区误差")
    direct = predict_candidate(teacher, data.raw[test])
    student = predict_student(model, student_design(teacher, data.raw[test]), scale)
    return {
        "student": share_metrics(data.counts[test], student),
        "teacher": share_metrics(data.counts[test], direct),
        "employment": share_metrics(data.counts[test], data.raw[test, 1]),
        "train_regions": len(generated) * 3 // 4, "validation_regions": len(generated) // 4,
        "student_alpha": alpha, "synthetic_fidelity_tv": fidelity,
        "residual_log_sigma": rule["residual_log_sigma"],
    }, map_series(data, test, student, direct), {
        "generator": rule, "teacher_reference": teacher["reference"].tolist(),
        "teacher_coefficients": teacher["estimator"][-1].coef_.tolist(),
        "student_coefficients": model.coef_.tolist(), "student_intercept": model.intercept_, "student_scale": scale,
    }


def arena(data, config, progress):
    # Restrict the arrays to development data before entering any training or scoring loop.
    mask = data.development
    raw, labels, blocks = data.raw[mask], data.counts[mask], data.blocks[mask]
    fold_rows, seed_rows = [], []
    unique = np.unique(blocks)
    oof = np.zeros((len(config.seeds), len(raw)))
    teacher_oof = np.zeros(len(raw))
    for index, block in enumerate(unique):
        validation = blocks == block
        training = ~validation
        progress("cross_validation", .12 + .8 * index / len(unique),
                 f"空间块 {index + 1}/{len(unique)} · 留出 {validation.sum()} 区")
        teacher, rule = calibrate(raw[training], labels[training], blocks[training], config.teacher_alpha)
        actual = labels[validation]
        teacher_oof[validation] = predict_candidate(teacher, raw[validation])
        design = student_design(teacher, raw[validation])
        scores = []
        for seed_index, seed in enumerate(config.seeds):
            generated = generate(teacher, rule, config.worlds_per_seed, [seed])
            model, scale, alpha, _ = train_student(teacher, generated, config.worlds_per_seed)
            prediction = predict_student(model, design, scale)
            oof[seed_index, validation] = prediction
            tv = variation(actual, prediction)
            scores.append(tv)
            seed_rows.append({"block": int(block), "seed": seed, "tv": tv, "student_alpha": alpha})
        fold_rows.append({"block": int(block), "regions": int(validation.sum()), "tv": float(np.mean(scores)),
                          "teacher_tv": variation(actual, teacher_oof[validation]),
                          "employment_tv": variation(actual, raw[validation, 1])})
    seed_scores = [float(np.mean([row["tv"] for row in seed_rows if row["seed"] == seed])) for seed in config.seeds]
    scores = [row["tv"] for row in fold_rows]
    progress("evaluation", .95, "汇总空间折、随机种子与区外预测")
    return {
        "mean_tv": float(np.mean(scores)), "fold_std": float(np.std(scores)),
        "seed_std": float(np.std(seed_scores)) if len(seed_scores) > 1 else None,
        "seed_means": [{"seed": seed, "tv": score} for seed, score in zip(config.seeds, seed_scores)],
        "teacher_mean_tv": float(np.mean([row["teacher_tv"] for row in fold_rows])),
        "employment_mean_tv": float(np.mean([row["employment_tv"] for row in fold_rows])),
    }, map_series(data, mask, oof.mean(axis=0), teacher_oof), {"folds": fold_rows, "seed_folds": seed_rows}


def run_research(archive, mode, config, progress):
    started = time.perf_counter()
    steps = []

    def record(stage, fraction, message):
        steps.append({"stage": stage, "elapsed_seconds": round(time.perf_counter() - started, 3)})
        progress(stage, fraction, message)

    record("data", .04, "读取真实归档并按固定空间块划分")
    data = Chicago.load(archive)
    with threadpool_limits(limits=1):
        metrics, map_data, diagnostics = (
            reproduction(data, record) if mode == "reproduction" else arena(data, config, record)
        )
    result = {"mode": mode, "protocol": PROTOCOL, "source_digest": data.digest, "source_crs": SOURCE_CRS,
              "development_regions": int(data.development.sum()), "holdout_regions": int((~data.development).sum()),
              "config": (FORMAL if mode == "reproduction" else config).model_dump(),
              "duration_seconds": round(time.perf_counter() - started, 3), "metrics": metrics, "steps": steps}
    return result, {"map": map_data, "diagnostics": diagnostics}
