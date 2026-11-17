#!/usr/bin/env python3
"""Development-only spatial CV, stability gates, then a frozen exploratory test."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import warnings
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import joblib
import numpy as np
import pandas as pd
import sklearn
from evaluate_teacher_data import load_geojson, save, share_metrics, spatial_split
from pyproj import Geod, Transformer
from shapely.geometry import shape
from shapely.ops import transform
from sklearn.exceptions import ConvergenceWarning
from threadpoolctl import threadpool_limits

from ridecast.spatial_models import candidate_configs, fit_candidate, predict_candidate

FEATURES = ["population", "employment_density", "area_m2", "network_density", "income", "no_vehicle_share",
            "education_share", "young_adult_share", "household_size", "job_entropy", "transit_distance",
            "employment_near_transit", "transit_frequency", "unemployment_percent", "commute_minutes"]
PROTOCOL = {"version": "spatial-model-search-v1", "evidence_status": "exploratory; repeated use of previously viewed file",
            "development": "previous 364 validation regions; eight leave-one-spatial-block-out folds",
            "test": "previous 431 test regions; never used to rank new candidates",
            "features": FEATURES, "candidates": candidate_configs(),
            "rank_score": "mean fold TV + 0.25 * standard deviation of fold TV",
            "stability_pool": "top five by CV plus best per family plus both baselines",
            "stability": {"sample_fraction": .8, "seeds": [7, 42, 99, 2026, 17],
                          "mean_tv_vs_full_max": 5., "max_tv_vs_full_max": 10.},
            "promising_gate": {"relative_mean_fold_gain_vs_employment_min": .1, "wins_out_of_8_min": 6},
            "fallback": "employment-density baseline if no candidate meets all development criteria",
            "test_reporting": "one development-selected configuration per family plus overall selected model",
            "fit_transform": "training-fold positive medians/scalers/knots only; no target city mean normalization",
            "label_semantics": "assumed aggregate completed trips; no observation period or time-series labels"}


def family_key(config):
    return config["name"] if config["family"] == "student" else config["family"]


def fit_checked(config, x, y, blocks):
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        fitted = fit_candidate(config, x, y, blocks, FEATURES)
    convergence = [w for w in captured if issubclass(w.category, ConvergenceWarning)]
    if convergence:
        raise ValueError("Optimizer failed to converge; candidate excluded")
    return fitted, [str(w.message) for w in captured]


def run(args):
    output = args.output.resolve()
    if "var" not in output.relative_to(Path.cwd().resolve()).parts or (output.exists() and any(output.iterdir())):
        raise ValueError("Use a new private output directory under ignored var/")
    output.mkdir(parents=True, exist_ok=True)
    save(output/"protocol.json", PROTOCOL)
    features, frame, audit = load_geojson(args.archive)
    old_summary = json.loads((args.previous/"summary.json").read_text())
    if audit["file_sha256"] != old_summary["source"]["file_sha256"]:
        raise ValueError("Input checksum differs from the original comparison")
    development, groups = spatial_split(features)
    test = ~development
    old_predictions = pd.read_csv(args.previous/"private-predictions.csv")
    if not np.array_equal(test, old_predictions.partition.to_numpy() == "test"):
        raise ValueError("Test partition changed")
    transformer, geod = Transformer.from_crs(args.source_crs, "EPSG:4326", always_xy=True), Geod(ellps="WGS84")
    geometries = [transform(transformer.transform, shape(f["geometry"])) for f in features]
    area = np.array([abs(geod.geometry_area_perimeter(g)[0]) for g in geometries])
    raw = np.column_stack([frame.total_population, frame.empoloyment_den, area, frame.network_den,
                           frame.median_household_income, frame.pct_no_vehicle, frame.pct_bachelorORhigher,
                           frame.pct_18to34, frame.household_size_mean, frame.job_entropy, frame.dist_to_tran,
                           frame.employed_near_tran, frame.freq_tran, frame.pct_unemployed_16, frame.work_commute_mean])
    actual = frame.TripCount.to_numpy(dtype=float)
    x, y, blocks = raw[development], actual[development], groups[development]
    folds = np.unique(blocks)
    configs = {c["name"]: c for c in PROTOCOL["candidates"]}
    cv, failures, predictions, warning_log = {}, {}, {}, []
    for name, config in configs.items():
        oof = np.zeros(len(y))
        fold_scores = []
        try:
            for fold in folds:
                train, validation = blocks != fold, blocks == fold
                fitted, captured = fit_checked(config, x[train], y[train], blocks[train])
                warning_log.extend(captured)
                oof[validation] = predict_candidate(fitted, x[validation])
                fold_scores.append({"block": int(fold), "regions": int(validation.sum()),
                                    **share_metrics(y[validation], oof[validation])})
            tv = np.array([r["total_variation_percent"] for r in fold_scores])
            cv[name] = {"folds": fold_scores, "mean_fold_tv": float(tv.mean()), "std_fold_tv": float(tv.std()),
                        "rank_score": float(tv.mean()+.25*tv.std()), "pooled_oof": share_metrics(y, oof)}
            predictions[name] = oof
            print(json.dumps({"stage": "development_spatial_cv", "candidate": name,
                              "mean_fold_tv": cv[name]["mean_fold_tv"]}), flush=True)
        except (ValueError, FloatingPointError) as error:
            failures[name] = str(error)
    baseline = cv["employment_density"]
    for name, result in cv.items():
        result["relative_mean_gain_vs_employment"] = 1-result["mean_fold_tv"]/baseline["mean_fold_tv"]
        result["fold_wins_vs_employment"] = sum(a["total_variation_percent"] < b["total_variation_percent"]
                                                  for a, b in zip(result["folds"], baseline["folds"]))
    ranking = sorted(cv, key=lambda n: cv[n]["rank_score"])
    family_winners = {}
    for name in ranking:
        family_winners.setdefault(family_key(configs[name]), name)
    pool = set(ranking[:5]) | set(family_winners.values()) | {"uniform", "employment_density"}
    stability, models = {}, {}
    for name in sorted(pool):
        model, captured = fit_checked(configs[name], x, y, blocks)
        models[name] = model
        warning_log.extend(captured)
        reference = predict_candidate(model, x)
        runs = []
        for seed in PROTOCOL["stability"]["seeds"]:
            rng, indices = np.random.default_rng(seed), []
            for block in folds:
                choices = np.flatnonzero(blocks == block)
                indices.extend(rng.permutation(choices)[:max(2, int(.8*len(choices)))])
            indices = np.asarray(indices)
            partial, captured = fit_checked(configs[name], x[indices], y[indices], blocks[indices])
            warning_log.extend(captured)
            runs.append({"seed": seed, "training_regions": len(indices),
                         "tv_vs_full_percent": share_metrics(reference, predict_candidate(partial, x))["total_variation_percent"]})
        mean = float(np.mean([r["tv_vs_full_percent"] for r in runs]))
        maximum = max(r["tv_vs_full_percent"] for r in runs)
        stability[name] = {"runs": runs, "mean_tv_vs_full": mean, "max_tv_vs_full": maximum,
                           "passes": mean <= 5 and maximum <= 10}
    promising = [n for n in ranking if n in stability and stability[n]["passes"]
                 and cv[n]["relative_mean_gain_vs_employment"] >= .1 and cv[n]["fold_wins_vs_employment"] >= 6]
    selected = promising[0] if promising else "employment_density"
    selected_for_test = sorted(set(family_winners.values()) | {selected})
    decision = {"selected": selected, "promising_development_candidates": promising, "family_winners": family_winners,
                "all_development_ranking": ranking, "selected_for_test": selected_for_test,
                "fallback_used": not bool(promising), "stability": stability}
    save(output/"selection-before-test.json", decision)
    save(output/"development-cv.json", cv)
    pd.DataFrame(predictions).to_parquet(output/"private-oof-predictions.parquet", index=False)
    # No more candidate selection follows. Existing test is reused only for exploratory reporting.
    test_scores, test_predictions = {}, {}
    for name in selected_for_test:
        model = models[name]
        values = predict_candidate(model, raw[test])
        test_scores[name] = share_metrics(actual[test], values)
        test_predictions[name] = values
        joblib.dump(model, output/f"model-{name}.joblib")
    test_scores["original_simulator_transfer_reference"] = share_metrics(actual[test], old_predictions.synthetic_ridge.to_numpy()[test])
    pd.DataFrame({"tract_id": frame.GEO_ID.to_numpy()[test], "actual": actual[test],
                  **test_predictions}).to_parquet(output/"private-test-predictions.parquet", index=False)
    final_model = models[selected]
    reference = predict_candidate(final_model, x)
    scale_checks = []
    for factor in [2., 10.]:
        changed, captured = fit_checked(configs[selected], x, y*factor, blocks)
        warning_log.extend(captured)
        scale_checks.append({"factor": factor, "share_tv_percent": share_metrics(reference, predict_candidate(changed, x))["total_variation_percent"]})
    appended = predict_candidate(final_model, np.concatenate([x, x*2]))[:len(x)]
    return {"protocol": PROTOCOL, "source": audit, "projection": {"assumed_crs": args.source_crs, "confirmed": False},
            "split": {"development_regions": int(development.sum()), "development_blocks": folds.tolist(),
                      "test_regions": int(test.sum()), "test_blocks": np.unique(groups[test]).tolist(),
                      "same_previously_viewed_test": True},
            "cv": cv, "failures": failures, "selection": decision, "test": test_scores,
            "scale_checks": scale_checks, "append_old_prediction_max_difference": float(np.max(np.abs(reference-appended))),
            "library_warnings": sorted(set(warning_log)),
            "environment": {"sklearn": sklearn.__version__, "numpy": np.__version__},
            "limitations": [
                "Repeated exploratory analysis on a previously viewed single-city snapshot; no fresh independent test.",
                "No timestamps/statistical period, so no hourly demand, request/cancellation, future growth or GRU validation.",
                "Development CV has only eight spatial groups; adjacent groups remain dependent.",
                "Trying many candidates can overfit development CV; stability thresholds are declared research screens, not production guarantees.",
                "Marginal monotonicity in boosting is a modeling constraint, not a discovered causal law or invariant response under simultaneous feature changes.",
                "Synthetic augmentation distills training-fitted rules and adds no independent observed information.",
                "80% resampling, count-scale and appended-record tests do not establish stability under future concept shift.",
                "Source CRS, aggregate target semantics, feature units and observation coverage still require provider confirmation.",
                "No real-world dispatch or satisfaction outcomes, no temporal simulator dynamics calibrated by these static models.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-crs", required=True)
    parser.add_argument("--previous", type=Path, default=Path("var/teacher-data/transfer-v1"))
    parser.add_argument("--output", type=Path, default=Path("var/teacher-data/spatial-search-v1"))
    args = parser.parse_args()
    started = time.monotonic()
    with threadpool_limits(limits=1):
        result = run(args)
    result["duration_seconds"] = round(time.monotonic()-started, 3)
    result["source_fingerprints"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                     [Path(__file__), Path("src/ridecast/spatial_models.py"), Path("src/ridecast/spatial_calibration.py")]}
    save(args.output/"summary.json", result)
    print(json.dumps({"selected": result["selection"]["selected"], "fallback_used": result["selection"]["fallback_used"],
                      "test": result["test"], "duration_seconds": result["duration_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
