#!/usr/bin/env python3
"""Follow a development-selected smooth teacher with synthetic-only student fitting.

Student selection uses generated validation worlds and teacher means, never real
test outcomes. This is exploratory follow-up, not new independent confirmation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from evaluate_teacher_data import load_geojson, save, share_metrics, spatial_split, teacher_smoke
from explore_spatial_models import FEATURES
from pyproj import Geod, Transformer
from shapely.geometry import shape
from shapely.ops import transform
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import PoissonRegressor
from threadpoolctl import threadpool_limits

from ridecast.spatial_calibration import fit_stable_rule, generate_regions
from ridecast.spatial_models import joint_support_diagnostic, predict_candidate

PROTOCOL = {"version": "calibrated-gam-spatial-world-v1",
            "evidence_status": "exploratory follow-up; selected teacher uses development CV, previous test already viewed",
            "teacher": "frozen overall development-selected model from spatial-search-v1, no manual test-based choice",
            "student": "Poisson GLM on frozen teacher spline/scaler features; coefficients fitted on generated labels only",
            "seeds": [7, 42, 99], "worlds_per_seed": 64, "regions_per_world": 64,
            "synthetic_train_worlds": "indices 0..47", "synthetic_validation_worlds": "indices 48..63",
            "alphas": [0., .001, .01], "selection": "TV against teacher means in synthetic validation worlds",
            "real_fit": "364 development regions calibrate teacher/covariate distribution/residual scale; 431 test untouched by fitting",
            "noise": "mean-preserving residual-MAD lognormal plus Poisson; assumption, not observed temporal process",
            "recovered_dynamics": False, "independent_new_real_information": False}


def student_predict(model, transformed, scale):
    return np.exp(np.sum(transformed*model.coef_, axis=1)+model.intercept_)*scale


def check_synthetic_growth(virtual, frozen_transform, reference, selected_alpha, full_model, full_scale):
    """Frozen-alpha sensitivity check on generated validation worlds only."""
    basis = frozen_transform.transform(np.log1p(virtual[FEATURES].to_numpy()/reference))
    validation = virtual.world_index.to_numpy() >= 48
    comparison = student_predict(full_model, basis[validation], full_scale)
    rows = []
    for worlds in [12, 24, 48]:
        training = virtual.world_index.to_numpy() < worlds
        scale = float(virtual.completed_trips.to_numpy()[training].mean())
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            model = PoissonRegressor(alpha=float(selected_alpha), max_iter=2000, tol=1e-7).fit(
                basis[training], virtual.completed_trips.to_numpy()[training]/scale)
        converged = not any(issubclass(w.category, ConvergenceWarning) for w in captured)
        scores = student_predict(model, basis[validation], scale)
        rows.append({"training_regions": int(training.sum()), "converged": converged,
                     "tv_vs_full_student_percent": share_metrics(comparison, scores)["total_variation_percent"],
                     "fidelity_to_teacher_mean_tv_percent": share_metrics(
                         virtual.expected_completed_trips.to_numpy()[validation], scores)["total_variation_percent"]})
    return {"runs": rows, "real_test_targets_used": False,
            "meaning": "generated-sample sensitivity only, not temporal drift or extra real-data accuracy"}


def run(args):
    output = args.output.resolve()
    if "var" not in output.relative_to(Path.cwd().resolve()).parts or (output.exists() and any(output.iterdir())):
        raise ValueError("Use a fresh private directory under ignored var/")
    output.mkdir(parents=True, exist_ok=True)
    save(output/"protocol.json", PROTOCOL)
    search = json.loads((args.search/"summary.json").read_text())
    selected_teacher = search["selection"]["selected"]
    teacher_file = args.search/f"model-{selected_teacher}.joblib"
    teacher = joblib.load(teacher_file)
    if teacher["config"]["family"] != "spline":
        raise ValueError("This frozen distillation protocol requires the development-selected spline family")
    features, frame, audit = load_geojson(args.archive)
    if audit["file_sha256"] != search["source"]["file_sha256"]:
        raise ValueError("Input checksum changed")
    development, blocks = spatial_split(features)
    test = ~development
    transformer, geod = Transformer.from_crs(args.source_crs, "EPSG:4326", always_xy=True), Geod(ellps="WGS84")
    area = np.array([abs(geod.geometry_area_perimeter(transform(transformer.transform, shape(f["geometry"])))[0])
                     for f in features])
    raw = np.column_stack([frame.total_population, frame.empoloyment_den, area, frame.network_den,
                           frame.median_household_income, frame.pct_no_vehicle, frame.pct_bachelorORhigher,
                           frame.pct_18to34, frame.household_size_mean, frame.job_entropy, frame.dist_to_tran,
                           frame.employed_near_tran, frame.freq_tran, frame.pct_unemployed_16, frame.work_commute_mean])
    counts = frame.TripCount.to_numpy(dtype=float)
    rule = fit_stable_rule(raw[development], counts[development], blocks[development], FEATURES, alpha=1., bootstraps=40)
    def mean_predictor(values):
        return predict_candidate(teacher, values)
    residual = np.log(counts[development]/mean_predictor(raw[development]))
    sigma = float(np.clip(1.4826*np.median(np.abs(residual-np.median(residual))), .05, 1.5))
    rule.update(residual_log_sigma=sigma, mean_generator_version=PROTOCOL["version"],
                external_teacher_sha256=hashlib.sha256(teacher_file.read_bytes()).hexdigest())
    rule.pop("fingerprint")
    rule["fingerprint"] = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()
    save(output/"generator.json", rule)
    virtual = pd.concat([generate_regions(rule, range(64), seed=seed, zones=64, mean_predictor=mean_predictor)
                         for seed in PROTOCOL["seeds"]], ignore_index=True)
    virtual.to_parquet(output/"synthetic-regions.parquet", index=False)
    frozen_transform = teacher["estimator"][:-1]
    basis = frozen_transform.transform(np.log1p(virtual[FEATURES].to_numpy()/teacher["reference"]))
    training = virtual.world_index.to_numpy() < 48
    scale = float(virtual.completed_trips.to_numpy()[training].mean())
    candidates, validation, captured_messages = {}, {}, []
    for alpha in PROTOCOL["alphas"]:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            student = PoissonRegressor(alpha=alpha, max_iter=2000, tol=1e-7).fit(
                basis[training], virtual.completed_trips.to_numpy()[training]/scale)
        captured_messages.extend(str(w.message) for w in captured)
        if any(issubclass(w.category, ConvergenceWarning) for w in captured):
            continue
        candidates[str(alpha)] = student
        validation[str(alpha)] = share_metrics(virtual.expected_completed_trips.to_numpy()[~training],
                                               student_predict(student, basis[~training], scale))
    selected = min(candidates, key=lambda a: validation[a]["total_variation_percent"])
    save(output/"selection-before-real-test.json", {"teacher": selected_teacher, "selected_alpha": selected,
                                                    "synthetic_validation_fidelity": validation})
    model = candidates[selected]
    actual_basis = frozen_transform.transform(np.log1p(raw/teacher["reference"]))
    student_scores = student_predict(model, actual_basis, scale)
    teacher_scores = mean_predictor(raw)
    pd.DataFrame({"tract_id": frame.GEO_ID, "partition": np.where(development, "development", "test"),
                  "teacher": teacher_scores, "synthetic_student": student_scores,
                  "actual": counts}).to_parquet(output/"private-predictions.parquet", index=False)
    joblib.dump({"teacher_reference": teacher["reference"], "frozen_transform": frozen_transform,
                 "student": model, "scale": scale, "generator_fingerprint": rule["fingerprint"],
                 "feature_names": FEATURES, "target_semantics": "aggregate_completed_trips_assumed",
                 "training_source": "generated_static_regions", "model_version": PROTOCOL["version"]}, output/"student.joblib")
    repeated = generate_regions(rule, [5], seed=42, zones=64, mean_predictor=mean_predictor)
    pd.testing.assert_frame_equal(virtual[(virtual.seed == 42) & (virtual.world_index == 5)].reset_index(drop=True), repeated)
    smoke = teacher_smoke(features, frame, student_scores, output, transformer,
                          {"generator_version": PROTOCOL["version"], "calibration_fingerprint": rule["fingerprint"],
                           "rates_fit_real_training_labels": True, "student_training_source": "generated_static_regions"})
    return {"protocol": PROTOCOL, "source": audit, "teacher_selected_by_development": selected_teacher,
            "selected_student_alpha": selected, "synthetic_validation_fidelity": validation,
            "synthetic": {"worlds": 192, "regions": len(virtual), "training_regions": int(training.sum()),
                          "validation_regions": int((~training).sum()), "partition_repeatable": True,
                          "residual_log_sigma": sigma, "generator_fingerprint": rule["fingerprint"]},
            "test": {"teacher_direct": share_metrics(counts[test], teacher_scores[test]),
                     "synthetic_only_student": share_metrics(counts[test], student_scores[test]),
                     "student_vs_teacher_fidelity": share_metrics(teacher_scores[test], student_scores[test])},
            "joint_feature_support": joint_support_diagnostic(raw[development], blocks[development], raw[test]),
            "simulation_smoke": smoke, "library_warnings": sorted(set(captured_messages)),
            "synthetic_growth_check": check_synthetic_growth(virtual, frozen_transform, teacher["reference"],
                                                              selected, model, scale),
            "limitations": ["This is model distillation, not independent new real information or proof of augmentation benefit.",
                            "Teacher transforms, knots, references and generator are calibrated on real development data; only student coefficients use synthetic labels exclusively.",
                            "Single-city static aggregate and previously viewed test; no temporal GRU or future-growth validation.",
                            "Residual noise, virtual feature joint distribution and hourly simulator dynamics remain assumptions.",
                            "Marginal support clipping and joint-neighbor diagnostic do not guarantee validity in new regimes.",
                            "The teacher and virtual-data route were chosen after earlier exploratory results; new external validation is required."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-crs", required=True)
    parser.add_argument("--search", type=Path, default=Path("var/teacher-data/spatial-search-v1"))
    parser.add_argument("--output", type=Path, default=Path("var/teacher-data/gam-world-v1"))
    args = parser.parse_args()
    started = time.monotonic()
    with threadpool_limits(limits=1):
        report = run(args)
    report["duration_seconds"] = round(time.monotonic()-started, 3)
    report["source_fingerprints"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                     [Path(__file__), Path("src/ridecast/spatial_models.py"), Path("src/ridecast/spatial_calibration.py")]}
    save(args.output/"summary.json", report)
    print(json.dumps({"test": report["test"], "synthetic": report["synthetic"], "selected_alpha": report["selected_student_alpha"]}), flush=True)


if __name__ == "__main__":
    main()
