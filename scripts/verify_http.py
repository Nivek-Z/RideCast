#!/usr/bin/env python3
"""Smoke-check a running defense API and its separate worker over real HTTP."""

import argparse
import json
import math
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def verify(base):
    base = base.rstrip("/") + "/api/research"
    checks = []

    def request(path, method="GET", body=None, expected=200):
        payload = json.dumps(body).encode() if body is not None else None
        req = Request(base + path, data=payload, method=method, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise RuntimeError(f"{method} {path}: {response.status} (expected {expected}): {response.read()!r}")
            return json.load(response)

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    def wait(run):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = request(f"/runs/{run['id']}")
            if status["state"] in {"failed", "cancelled"}:
                raise RuntimeError(status["error"] or status["state"])
            if status["state"] == "succeeded":
                return status["summary"], request(f"/runs/{run['id']}/result")
            time.sleep(.24)
        raise TimeoutError(run["id"])

    started = time.monotonic()
    ready = request("/status")
    check("archive and independent worker ready", ready["archive_ready"] and ready["worker_online"])
    request("/arena", "POST", {"configurations": [{"seeds": [7, 7]}]}, 422)
    check("duplicate seed rejected", True)
    request("/arena", "POST", {"configurations": [{"seeds": [7]}, {"seeds": [8]}]}, 422)
    check("different seed cohorts in a single batch rejected", True)
    original = request("/reproduce", "POST", expected=202)
    summary, output = wait(original)
    tv = summary["metrics"]["student"]["total_variation_percent"]
    check("recomputed historical TV", math.isclose(tv, 25.891369193776843, abs_tol=.02))
    check("all five real execution stages persisted", [s["stage"] for s in summary["steps"]] ==
          ["data", "calibration", "generation", "training", "evaluation"])
    check("student fitted on 9216 synthetic labels", summary["metrics"]["train_regions"] == 9216)
    check("formal artifact maps only held-out targets", len(output["map"]["regions"]) == 431 and
          not any(output["map"]["development"][i] for i in output["map"]["regions"]))
    check("map contains 795 geometries", len(output["map"]["paths"]) == 795 and all(output["map"]["paths"]))
    check("observations and predictions each sum to one", all(
        math.isclose(sum(output["map"][key]), 100) for key in ("actual", "predicted", "teacher")))
    configurations = [
        {"worlds_per_seed": 8, "teacher_alpha": .1, "seeds": [42, 7]},
        {"worlds_per_seed": 16, "teacher_alpha": 1., "seeds": [7, 42]},
    ]
    jobs = request("/arena", "POST", {"configurations": configurations}, 202)
    check("single submission queues both configurations", len(jobs) == 2)
    check("both jobs share a batch identifier", jobs[0]["params"]["batch_id"] == jobs[1]["params"]["batch_id"])
    arena = []
    for index, job in enumerate(jobs):
        summary, output = wait(job)
        metrics = summary["metrics"]
        check(f"configuration {index + 1} ranks on development folds",
              len(output["diagnostics"]["folds"]) == 8 and len(output["diagnostics"]["seed_folds"]) == 16)
        check(f"configuration {index + 1} exposes no holdout values",
              len(output["map"]["regions"]) == 364 and
              all(output["map"]["development"][i] for i in output["map"]["regions"]))
        arena.append({"config": summary["config"], "mean_tv": metrics["mean_tv"],
                      "seed_std": metrics["seed_std"], "duration_seconds": summary["duration_seconds"]})
    request("/temporal", expected=404)
    check("retired temporal endpoint unavailable", True)
    page = request("/runs")
    check("research run list contains only spatial studies",
          all(item["params"]["mode"] in {"reproduction", "arena"} for item in page["items"]))
    ids = {item["id"] for item in page["items"] if item["summary"]}
    check("completed results remain discoverable", all(job["id"] in ids for job in [original, *jobs]))
    formal_summary = request(f"/runs/{original['id']}")["summary"]
    return {"checks": checks, "checks_passed": len(checks), "environment": "local API + independent worker",
            "formal_execution_seconds": formal_summary["duration_seconds"], "formal_tv": tv, "arena": arena,
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(verify(args.base_url), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
