#!/usr/bin/env python3
"""Learn stable static rules on real training regions, then train on virtual regions."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from evaluate_teacher_data import load_geojson, save, share_metrics, spatial_split, teacher_smoke
from pyproj import Geod, Transformer
from shapely.geometry import shape
from shapely.ops import transform

from ridecast.spatial_calibration import (
    VERSION,
    fit_stable_rule,
    fit_student,
    generate_regions,
    predict_rule,
    predict_student,
    support_diagnostics,
)

FEATURES = ["population", "employment_density", "area_m2", "network_density", "income", "no_vehicle_share"]
PROTOCOL = {
    "version": VERSION,
    "evidence_status": "exploratory; same file and previously viewed test, not new independent evidence",
    "task": "static relative regional completed-trip volume, NOT hourly demand",
    "label_use_authorization": "user now requests rules learned from real TRAINING data",
    "split": "keep previous 431 test tracts; split previous validation blocks 6 train / 2 validation",
    "split_seed": 20261117,
    "generator_feature_sets": ["common3", "context6"],
    "generator_alphas": [.01, .1, 1.],
    "block_bootstraps": 40,
    "stable_rule_criteria": {"sign_support_min": .9, "relative_coefficient_iqr_max": 1.,
                             "absolute_median_slope_min": .05, "absolute_slope_max": 2.},
    "selection_metric": "total_variation_percent on real validation only",
    "synthetic_seeds": [7, 42, 99],
    "worlds_per_seed": 64, "regions_per_world": 64,
    "synthetic_noise": "training-residual MAD lognormal/Poisson; assumed, not identified real dynamics",
    "student": "same quadratic log-feature ridge for real-only, synthetic-only and mixed controls",
    "student_alphas": [.01, .1, 1.],
    "mixed_total_weights": {"real": .5, "synthetic": .5},
    "growth_fractions": [.4, .7, 1.], "stability_seeds": [7, 42, 99, 2026, 17],
    "target_scale_stress": [2., 10.], "outlier_stress": "multiply fixed random 1% of training labels by 100",
    "no_test_based_reselection": True,
    "excluded": ["TripCount as input", "FALSE", "TRUE", "pct_share", "trip means", "tract IDs", "coordinates as predictors"],
}


def metric(actual, scores):
    # Baselines output relative weights in different arbitrary units. Only
    # scale-free metrics are comparable; do not report raw-count log errors.
    return share_metrics(actual, scores)


def split_regions(features):
    previous_validation, blocks = spatial_split(features)
    candidates = np.unique(blocks[previous_validation])
    shuffled = np.random.default_rng(PROTOCOL["split_seed"]).permutation(candidates)
    train, validation, test = np.isin(blocks, shuffled[:6]), np.isin(blocks, shuffled[6:]), ~previous_validation
    if not np.all(train.astype(int) + validation.astype(int) + test.astype(int) == 1):
        raise ValueError("Partitions must be disjoint and exhaustive")
    return train, validation, test, blocks


def stability_checks(rule, raw, actual, groups):
    names, alpha = rule["features"], rule["alpha"]
    reference = predict_rule(rule, raw)
    scaled = []
    for factor in PROTOCOL["target_scale_stress"]:
        changed = fit_stable_rule(raw, actual*factor, groups, names, alpha=alpha,
                                  bootstraps=PROTOCOL["block_bootstraps"])
        prediction = predict_rule(changed, raw)
        scaled.append({"factor": factor, "prediction_share_tv_percent": share_metrics(reference, prediction)["total_variation_percent"],
                       "max_slope_change": float(np.max(np.abs(np.asarray(rule["slopes"])-changed["slopes"]))),
                       "median_prediction_multiplier": float(np.median(prediction/reference))})
    growth = []
    for fraction in PROTOCOL["growth_fractions"]:
        rows = []
        for seed in PROTOCOL["stability_seeds"]:
            rng, chosen = np.random.default_rng(seed), []
            for group in np.unique(groups):
                members = np.flatnonzero(groups == group)
                chosen.extend(rng.permutation(members)[:max(2, int(len(members)*fraction))])
            chosen = np.array(chosen)
            partial = fit_stable_rule(raw[chosen], actual[chosen], groups[chosen], names, alpha=alpha,
                                     bootstraps=PROTOCOL["block_bootstraps"])
            rows.append({"seed": seed, "rows": len(chosen),
                         "tv_vs_full_training_rule_percent": share_metrics(reference, predict_rule(partial, raw))["total_variation_percent"],
                         "active_features": [n for n, active in zip(names, partial["active"]) if active],
                         "slopes": partial["slopes"]})
        growth.append({"fraction": fraction, "runs": rows,
                       "mean_tv_vs_full_percent": float(np.mean([r["tv_vs_full_training_rule_percent"] for r in rows])),
                       "max_tv_vs_full_percent": max(r["tv_vs_full_training_rule_percent"] for r in rows)})
    corrupted = actual.copy()
    indices = np.random.default_rng(42).choice(len(actual), max(1, int(.01*len(actual))), replace=False)
    corrupted[indices] *= 100
    resistant = fit_stable_rule(raw, corrupted, groups, names, alpha=alpha,
                               bootstraps=PROTOCOL["block_bootstraps"])
    outlier = {"corrupted_training_rows": len(indices), "multiplier": 100,
               "prediction_share_tv_percent": share_metrics(reference, predict_rule(resistant, raw))["total_variation_percent"],
               "active_features": [n for n, active in zip(names, resistant["active"]) if active]}
    return {"target_scale": scaled, "training_sample_growth": growth, "outlier": outlier,
            "meaning": "sensitivity checks only; not future/time-drift validation; no outcome tuned using these checks"}


def run(args):
    output = args.output.resolve()
    if "var" not in output.relative_to(Path.cwd().resolve()).parts:
        raise ValueError("All private experiment artifacts must stay under ignored var/")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Preserve previous runs; use a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    save(output / "protocol.json", PROTOCOL)
    features, frame, audit = load_geojson(args.archive)
    previous_summary = json.loads((args.previous / "summary.json").read_text())
    if audit["file_sha256"] != previous_summary["source"]["file_sha256"]:
        raise ValueError("Previous comparison came from a different input file")
    train, validation, test, blocks = split_regions(features)
    old = pd.read_csv(args.previous / "private-predictions.csv")
    if not np.array_equal(old.tract_id.to_numpy(), frame.GEO_ID.to_numpy()):
        raise ValueError("Tract ordering mismatch")
    if not np.array_equal(old.partition.to_numpy() == "test", test):
        raise ValueError("Previous test areas must stay unchanged")
    partitions = pd.DataFrame({"tract_id": frame.GEO_ID.astype("int64"), "block": blocks,
                               "partition": np.select([train, validation], ["train", "validation"], default="test")})
    partitions.to_csv(output / "real-partitions.csv", index=False)
    transformer, geod = Transformer.from_crs(args.source_crs, "EPSG:4326", always_xy=True), Geod(ellps="WGS84")
    geometries = [transform(transformer.transform, shape(f["geometry"])) for f in features]
    bounds = [min(g.bounds[0] for g in geometries), min(g.bounds[1] for g in geometries),
              max(g.bounds[2] for g in geometries), max(g.bounds[3] for g in geometries)]
    if not (-88.5 < bounds[0] < bounds[2] < -87 and 41 < bounds[1] < bounds[3] < 43):
        raise ValueError("Assumed CRS does not produce plausible Chicago coordinates")
    area = np.array([abs(geod.geometry_area_perimeter(g)[0]) for g in geometries])
    raw = np.column_stack([frame.total_population, frame.empoloyment_den, area, frame.network_den,
                           frame.median_household_income, frame.pct_no_vehicle])
    actual = frame.TripCount.to_numpy(dtype=float)
    rules, calibration_validation = {}, {}
    for feature_set, size in [("common3", 3), ("context6", 6)]:
        for alpha in PROTOCOL["generator_alphas"]:
            name = f"{feature_set}_alpha{alpha}"
            rule = fit_stable_rule(raw[train, :size], actual[train], blocks[train], FEATURES[:size],
                                   alpha=alpha, bootstraps=PROTOCOL["block_bootstraps"])
            rules[name] = rule
            calibration_validation[name] = metric(actual[validation], predict_rule(rule, raw[validation, :size]))
    selected_rule_name = min(rules, key=lambda n: calibration_validation[n]["total_variation_percent"])
    rule = rules[selected_rule_name]
    save(output / "calibration-selection.json", {"selected": selected_rule_name, "validation": calibration_validation})
    save(output / "calibration-candidates.json", rules)
    save(output / "calibration-rule.json", rule)
    size = len(rule["features"])
    working = raw[:, :size]
    print(json.dumps({"stage": "training_only_rule_fit", "selected_using_validation": selected_rule_name,
                      "retained_features": [n for n, keep in zip(rule["features"], rule["active"]) if keep]}), flush=True)
    virtual = pd.concat([generate_regions(rule, range(PROTOCOL["worlds_per_seed"]), seed=seed,
                                          zones=PROTOCOL["regions_per_world"])
                         for seed in PROTOCOL["synthetic_seeds"]], ignore_index=True)
    virtual.to_parquet(output / "synthetic-regions.parquet", index=False)
    virtual_x = virtual[rule["features"]].to_numpy()
    virtual_y = virtual.completed_trips.to_numpy()
    # The same basis and training-only transform for all three learner controls.
    learners, learner_validation = {}, {}
    for source in ["real_only", "calibrated_synthetic_only", "real_plus_calibrated_synthetic"]:
        for alpha in PROTOCOL["student_alphas"]:
            if source == "real_only":
                x, y, weights = working[train], actual[train], None
            elif source == "calibrated_synthetic_only":
                x, y, weights = virtual_x, virtual_y, None
            else:
                x, y = np.concatenate([working[train], virtual_x]), np.concatenate([actual[train], virtual_y])
                weights = np.r_[np.full(train.sum(), .5/train.sum()), np.full(len(virtual), .5/len(virtual))]
            name = f"{source}_alpha{alpha}"
            model = fit_student(rule, x, y, alpha=alpha, weights=weights)
            learners[name] = model
            learner_validation[name] = metric(actual[validation], predict_student(rule, model, working[validation]))
    selected_learners = {source: min([n for n in learners if n.startswith(source+"_alpha")],
                                    key=lambda n: learner_validation[n]["total_variation_percent"])
                         for source in ["real_only", "calibrated_synthetic_only", "real_plus_calibrated_synthetic"]}
    validation_scores = {"uniform": metric(actual[validation], np.ones(validation.sum())),
                         "employment_density": metric(actual[validation], raw[validation, 1]),
                         "original_simulator_transfer": metric(actual[validation], old.synthetic_ridge.to_numpy()[validation]),
                         "calibrated_rule_direct": calibration_validation[selected_rule_name]}
    validation_scores.update({source: learner_validation[name] for source, name in selected_learners.items()})
    final_choice = min(validation_scores, key=lambda n: validation_scores[n]["total_variation_percent"])
    same3 = min([n for n in rules if n.startswith("common3")],
                key=lambda n: calibration_validation[n]["total_variation_percent"])
    decision = {"generator": selected_rule_name, "learners": selected_learners, "final_choice": final_choice,
                "same_three_features_generator": same3,
                "validation": validation_scores, "all_student_validation": learner_validation}
    save(output / "selection-before-test.json", decision)
    save(output / "students.json", learners)
    sensitivity = stability_checks(rule, working[train], actual[train], blocks[train])
    save(output / "training-only-stability.json", sensitivity)
    # All selection is now over. No test-based changes follow this point.
    scores = {"uniform": np.ones(len(frame)), "employment_density": raw[:, 1],
              "original_simulator_transfer": old.synthetic_ridge.to_numpy(),
              "calibrated_rule_direct": predict_rule(rule, working)}
    scores.update({source: predict_student(rule, learners[name], working) for source, name in selected_learners.items()})
    test_scores = {name: metric(actual[test], value[test]) for name, value in scores.items()}
    same3_score = metric(actual[test], predict_rule(rules[same3], raw[test, :3]))
    predictions = partitions.assign(actual_tripcount=actual)
    for name, values in scores.items():
        predictions[name] = values
    predictions.to_csv(output / "private-predictions.csv", index=False)
    # Exercise this task's actual selected rule in the unchanged macro simulator.
    smoke = teacher_smoke(features, frame, scores["calibrated_rule_direct"], output, transformer,
                          {"generator_version": VERSION, "calibration_fingerprint": rule["fingerprint"],
                           "rates_fit_real_training_labels": True})
    replay = generate_regions(rule, [5], seed=42, zones=PROTOCOL["regions_per_world"])
    original = virtual[(virtual.seed == 42) & (virtual.world_index == 5)].reset_index(drop=True)
    pd.testing.assert_frame_equal(original, replay)
    return {"protocol": PROTOCOL, "source": audit, "projection": {"assumed_crs": args.source_crs,
            "confirmed": False, "wgs84_bounds": bounds},
            "split": {"train_regions": int(train.sum()), "validation_regions": int(validation.sum()),
                      "test_regions": int(test.sum()), "test_is_previous_test": True,
                      "train_blocks": sorted(np.unique(blocks[train]).tolist()),
                      "validation_blocks": sorted(np.unique(blocks[validation]).tolist()),
                      "test_blocks": sorted(np.unique(blocks[test]).tolist()),
                      "sha256": hashlib.sha256((output/"real-partitions.csv").read_bytes()).hexdigest()},
            "rule": rule, "selection": decision, "test": test_scores,
            "same_three_features_control": {"selected_using_validation": same3,
                                            "validation": calibration_validation[same3], "test": same3_score},
            "synthetic": {"version": VERSION, "virtual_worlds": PROTOCOL["worlds_per_seed"]*len(PROTOCOL["synthetic_seeds"]),
                          "regional_samples": len(virtual), "is_time_series": False,
                          "partition_repeatable": True, "no_new_independent_real_observations": True},
            "stability": sensitivity, "test_covariate_support": support_diagnostics(rule, working[test]),
            "simulation_smoke": smoke,
            "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
            "limitations": [
                "This is a follow-up on previously viewed data/test metrics; exploratory, not a fresh independent confirmation.",
                "Unlike the first experiment, real TRAINING labels now fit the generator and all controls; this is not zero-shot transfer.",
                "No timestamps/exposure period; static completed-trip shares only, not hourly GRU, latent requests, supply or cancellation prediction.",
                "Six training spatial blocks and two validation blocks; sampling and spatial dependence limit stability evidence.",
                "Stable bootstrap directions and bounded smooth responses are associations, not causal or time-invariant laws.",
                "Feature clipping in virtual sampling covers only marginal training ranges; joint feature constraints are approximate.",
                "Residual noise/distribution and virtual world dependence are modeling assumptions, not observed dynamics.",
                "Synthetic regional labels distill the fitted rule and add no independent real-world information; compare direct rule and matched real-only learner.",
                "Counts rescaling invariance tests share stability, not future growth/behavior change or accurate absolute total demand.",
                "Covariate range screening cannot detect every joint-distribution or concept shift; new regimes require new calibration/evaluation.",
                "EPSG:3435, observation period and original trip-count semantics still need provider confirmation.",
                "The existing temporal simulator keeps absolute-time-v2; its arbitrary hourly scale/shape, weather, supply and OD are not calibrated by this static file.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-crs", required=True)
    parser.add_argument("--previous", type=Path, default=Path("var/teacher-data/transfer-v1"))
    parser.add_argument("--output", type=Path, default=Path("var/teacher-data/calibrated-v1"))
    args = parser.parse_args()
    started = time.monotonic()
    result = run(args)
    result.update(created_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic()-started, 3))
    result["source_fingerprints"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                                     [Path(__file__), Path("src/ridecast/spatial_calibration.py"),
                                      Path("src/ridecast/simulation.py"), Path("scripts/evaluate_teacher_data.py")]}
    save(args.output / "summary.json", result)
    print(json.dumps({"split": result["split"], "selected": result["selection"]["final_choice"],
                      "validation": result["selection"]["validation"], "test": result["test"]}), flush=True)


if __name__ == "__main__":
    main()
