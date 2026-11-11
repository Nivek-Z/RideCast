#!/usr/bin/env python3
"""Verify a running API and independent worker over real HTTP.

Start ``ridecast serve`` and ``ridecast worker`` separately before running this
script. It never starts a server, worker or in-process TestClient. The small
queued experiment verifies integration only; its metrics are not a model-quality
benchmark. API credentials are read from RIDECAST_API_KEY and never written.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def strict_json(text: str):
    def invalid(value):
        raise ValueError(f"Nonstandard JSON numeric token: {value}")
    return json.loads(text, parse_constant=invalid)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def shape(value):
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    result = {"keys": sorted(value)}
    for field in ("rows", "items", "epochs", "candidates", "zones", "orders", "vehicles", "recommendations"):
        if isinstance(value.get(field), list):
            result[field + "_count"] = len(value[field])
    for field in ("geojson", "zones", "roads"):
        if isinstance(value.get(field), dict) and isinstance(value[field].get("features"), list):
            result[field + "_feature_count"] = len(value[field]["features"])
    return result


class VerificationFailure(RuntimeError):
    pass


class Verifier:
    def __init__(self, base_url: str, api_key: str, deadline: float, report: dict):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.deadline = deadline
        self.report = report

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationFailure("90-second total HTTP verification deadline exceeded")
        return remaining

    def check(self, name: str, condition: bool, details=None):
        row = {"name": name, "status": "passed" if condition else "failed"}
        if details is not None:
            row["details"] = details
        self.report["checks"].append(row)
        if not condition:
            raise VerificationFailure(f"{name}: {details}")

    def request(self, name, path, *, method="GET", data=None, expected=200, params=None,
                raw=False, record=True, content_type="application/json"):
        if params:
            path += "?" + urlencode(params)
        headers = {"Accept": "text/event-stream" if raw else "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        body = data if isinstance(data, bytes) else (
            json.dumps(data, allow_nan=False).encode() if data is not None else None
        )
        if body is not None:
            headers["Content-Type"] = content_type
        started = time.monotonic()
        row = {"name": name, "method": method, "path": path, "expected_status": expected}
        try:
            request = Request(self.base_url + path, data=body, method=method, headers=headers)
            try:
                response = urlopen(request, timeout=min(30, self.remaining()))
            except HTTPError as error:
                response = error
            with response:
                status = response.status
                text = response.read().decode("utf-8")
                row.update({"http_status": status, "request_id": response.headers.get("X-Request-ID"),
                            "content_type": response.headers.get("Content-Type")})
            value = text if raw else strict_json(text)
            if status != expected:
                raise VerificationFailure(f"Expected HTTP {expected}, received {status}: {str(value)[:1200]}")
            row.update({"status": "passed", "duration_ms": round((time.monotonic() - started) * 1000, 2),
                        "shape": {"characters": len(text)} if raw else shape(value)})
            if record:
                self.report["checks"].append(row)
            return value
        except Exception as error:
            row.update({"status": "failed", "reason": str(error),
                        "duration_ms": round((time.monotonic() - started) * 1000, 2)})
            self.report["checks"].append(row)
            raise VerificationFailure(f"{name}: {error}") from error

    def wait_job(self, job_id: str, name: str, *, learning_curve=False):
        started = time.monotonic()
        states, stages, polls, last_progress = [], [], 0, 0.0
        live_epochs_seen = 0
        while True:
            self.remaining()
            job = self.request(name + "_poll", f"/api/v1/jobs/{job_id}", record=False)
            polls += 1
            if job["state"] not in states:
                states.append(job["state"])
            if job["stage"] not in stages:
                stages.append(job["stage"])
            progress = float(job["progress"])
            if progress < last_progress:
                self.check(name + "_monotonic_progress", False, {"before": last_progress, "after": progress})
            last_progress = progress
            if learning_curve:
                curve = self.request(name + "_curve_poll", f"/api/v1/jobs/{job_id}/history", record=False)
                live_epochs_seen = max(live_epochs_seen, len(curve.get("epochs", [])))
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                self.check(name, job["state"] == "succeeded" and progress == 1 and bool(job["worker_id"]), {
                    "job_id": job_id, "state": job["state"], "worker_id": job["worker_id"],
                    "observed_states": states, "observed_stages": stages, "poll_count": polls,
                    "live_epochs_seen": live_epochs_seen, "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "error": job["error"], "result": job["result"],
                })
                return job
            time.sleep(min(.2, self.remaining()))

    def terminal_sse(self, job_id: str, name: str):
        text = self.request(name, f"/api/v1/jobs/{job_id}/events", raw=True)
        parsed = []
        for block in text.replace("\r\n", "\n").split("\n\n"):
            event = None
            data = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if data:
                parsed.append({"event": event, "data": strict_json("\n".join(data))})
        done = any(e["event"] == "done" and e["data"].get("state") == "succeeded" for e in parsed)
        terminal = any(e["event"] == "state" and e["data"].get("payload", {}).get("state") == "succeeded" for e in parsed)
        self.check(name + "_terminal_payload", done and terminal, {"event_count": len(parsed)})


def multipart(fields: dict[str, str], filename: str, csv: str):
    boundary = "RideCastSmoke" + uuid.uuid4().hex
    pieces = []
    for name, value in fields.items():
        pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n')
    pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="orders"; filename="{filename}"\r\n'
                  f'Content-Type: text/csv\r\n\r\n{csv}\r\n--{boundary}--\r\n')
    return "".join(pieces).encode(), "multipart/form-data; boundary=" + boundary


def run(verifier: Verifier, demo: dict, examples: dict):
    v = verifier
    world_id, dataset_id, model_id = (demo[k] for k in ("world_id", "dataset_id", "model_id"))
    reference = {"world_id": world_id, "dataset_id": dataset_id, "model_id": model_id}
    v.report["reference"] = reference
    examples["reference"] = reference

    health = v.request("health", "/health")
    system = v.request("system", "/api/v1/system")
    v.report["versions"] = {"api": system["version"], "api_version": system["api_version"]}
    v.check("healthy_api_and_ml_dependency", health["status"] == "ok" and system["tensorflow_installed"])
    schema = v.request("openapi", "/openapi.json")
    v.check("openapi_version_matches_server", schema["info"]["version"] == system["version"])

    worlds = v.request("world_list", "/api/v1/worlds", params={"limit": 1000})
    v.check("reference_world_registered", any(row["id"] == world_id for row in worlds["items"]))
    world = v.request("world_detail", f"/api/v1/worlds/{world_id}")
    zones = [feature["properties"]["zone_id"] for feature in world["zones"]["features"]]
    world_map = v.request("world_map", f"/api/v1/worlds/{world_id}/map")
    v.check("map_zone_shape", len(world_map["zones"]["features"]) == len(zones) > 0)
    active_before = v.request("reference_active_model_before", f"/api/v1/worlds/{world_id}/active-model")
    rng = random.Random(20261111)
    for index in range(3):
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=rng.randrange(45 * 24), minutes=rng.randrange(60))
        state = v.request(f"world_random_state_{index}", f"/api/v1/worlds/{world_id}/state",
                          params={"timestamp": timestamp.isoformat(), "seed": 42})
        v.check(f"world_state_{index}_shape", len(state["zones"]) == len(zones) and state["status"] == "synthetic_observation")

    datasets = v.request("dataset_list", "/api/v1/datasets", params={"limit": 1000})
    v.check("reference_dataset_registered", any(row["id"] == dataset_id for row in datasets["items"]))
    dataset = v.request("dataset_detail", f"/api/v1/datasets/{dataset_id}")
    prefix = f"/api/v1/datasets/{dataset_id}"
    dashboard = {}
    for name, params in (("overview", None), ("timeseries", {"limit": 8}), ("heatmap", None),
                         ("flows", {"limit": 5}), ("replay", {"limit": 5}), ("orders", {"limit": 5, "offset": 0})):
        dashboard[name] = v.request("dashboard_" + name, prefix + "/" + name, params=params)
    v.request("zone_timeseries", prefix + "/timeseries", params={"zone_id": zones[0], "limit": 3})
    v.check("heatmap_zone_shape", len(dashboard["heatmap"]["rows"]) == len(zones))
    v.check("replay_is_labelled_historical", dashboard["replay"]["is_live"] is False)

    models = v.request("model_list", "/api/v1/models", params={"limit": 1000})
    v.check("reference_model_registered", any(row["id"] == model_id for row in models["items"]))
    model = v.request("model_detail", f"/api/v1/models/{model_id}")
    v.report["versions"]["model_dependencies"] = model["metadata"]["dependencies"]
    history = v.request("model_learning_history", f"/api/v1/models/{model_id}/history")
    evaluation = v.request("model_heldout_evaluation", f"/api/v1/models/{model_id}/evaluation", params={"limit": 5})
    v.check("trained_curves_and_evaluation_exist", bool(history["epochs"]) and bool(evaluation["rows"]))
    forecast_request = {"model_id": model_id, "dataset_id": dataset_id}
    forecast = v.request("reference_forecast", "/api/v1/forecasts", method="POST", data=forecast_request)
    v.check("forecast_dimensions_and_finite_counts", len(forecast["rows"]) == len(zones) * forecast["horizon"]
            and all(math.isfinite(row["predicted_demand"]) and 0 <= row["lower"] <= row["predicted_demand"] <= row["upper"]
                    for row in forecast["rows"]))
    dispatch = v.request("dispatch_default_supply", "/api/v1/dispatch-suggestions", method="POST", data=forecast_request)
    supplied = v.request("dispatch_explicit_integer_supply", "/api/v1/dispatch-suggestions", method="POST",
                         data={**forecast_request, "supply": {zone_id: 30 for zone_id in zones}})
    v.check("dispatch_is_advice_and_preserves_supply", dispatch["executed"] is False
            and supplied["supply_source"] == "caller" and supplied["conservation"]["preserved"] is True)
    invalid = v.request("malformed_forecast_event_rejected", "/api/v1/forecasts", method="POST",
                        data={**forecast_request, "scenario": {"events": [None]}}, expected=422)
    v.check("validation_error_envelope", bool(invalid.get("error", {}).get("code")) and bool(invalid.get("request_id")))

    v.request("job_list", "/api/v1/jobs", params={"limit": 1000})
    reference_job = v.request("reference_training_job", f"/api/v1/jobs/{demo['training_job_id']}")
    v.request("reference_job_learning_history", f"/api/v1/jobs/{demo['training_job_id']}/history")
    v.terminal_sse(demo["training_job_id"], "reference_training_sse")

    for name in ("overview", "heatmap", "flows", "replay"):
        params = {"limit": 5} if name in {"flows", "replay"} else None
        examples["examples"][name] = {"method": "GET", "path": prefix + "/" + name,
                                       "query": params or {}, "response": dashboard[name]}
    examples["examples"].update({
        "forecast": {"method": "POST", "path": "/api/v1/forecasts", "request": forecast_request, "response": forecast},
        "dispatch": {"method": "POST", "path": "/api/v1/dispatch-suggestions", "request": forecast_request, "response": dispatch},
        "dispatch_with_supply": {"method": "POST", "path": "/api/v1/dispatch-suggestions",
                                 "request": {**forecast_request, "supply": {zone_id: 30 for zone_id in zones}}, "response": supplied},
        "job": {"method": "GET", "path": f"/api/v1/jobs/{demo['training_job_id']}", "response": reference_job},
        "history": {"method": "GET", "path": f"/api/v1/models/{model_id}/history", "response": history},
        "world_map": {"method": "GET", "path": f"/api/v1/worlds/{world_id}/map", "response": world_map},
    })
    v.report["reference_shapes"] = {"zones": len(zones), "panel_rows": dataset["metadata"]["row_count"],
                                     "forecast_rows": len(forecast["rows"]), "horizon": forecast["horizon"],
                                     "learning_epochs": len(history["epochs"])}

    # This experiment is deliberately isolated from the 45-day demonstration.
    smoke_world = v.request("create_http_smoke_world", "/api/v1/worlds", method="POST", expected=201,
                            data={"name": "HTTP联调城" + uuid.uuid4().hex[:8], "rows": 2, "cols": 2, "seed": 20261111})
    smoke_world_id = smoke_world["id"]
    v.check("smoke_world_is_separate", smoke_world_id != world_id)
    simulation = v.request("enqueue_http_simulation", "/api/v1/simulations", method="POST", expected=202,
                           data={"world_id": smoke_world_id, "start": "2026-02-01T00:00:00+08:00", "days": 2, "seed": 20261111})
    simulated_job = v.wait_job(simulation["id"], "independent_worker_simulation")
    smoke_dataset_id = simulated_job["result"]["resource_id"]
    trained = v.request("enqueue_http_training", "/api/v1/training-jobs", method="POST", expected=202,
                        data={"dataset_id": smoke_dataset_id, "config": {"epochs": 1, "lookback": 12,
                              "horizon": 2, "units": 8, "batch_size": 32, "seed": 42}})
    trained_job = v.wait_job(trained["id"], "independent_worker_real_training", learning_curve=True)
    smoke_model_id = trained_job["result"]["resource_id"]
    small_forecast = v.request("worker_trained_model_forecast", "/api/v1/forecasts", method="POST",
                              data={"model_id": smoke_model_id, "dataset_id": smoke_dataset_id})
    small_history = v.request("worker_training_final_curve", f"/api/v1/jobs/{trained['id']}/history")
    v.check("worker_model_output_shape", len(small_forecast["rows"]) == 4 * 2 and len(small_history["epochs"]) == 1)
    v.terminal_sse(trained["id"], "worker_training_sse")
    v.request("activate_isolated_smoke_model", f"/api/v1/models/{smoke_model_id}/activate", method="POST")
    active_smoke = v.request("isolated_world_active_model", f"/api/v1/worlds/{smoke_world_id}/active-model")
    v.check("isolated_model_activation", active_smoke["model_id"] == smoke_model_id)
    active_after = v.request("reference_active_model_after", f"/api/v1/worlds/{world_id}/active-model")
    v.check("reference_activation_unchanged", active_after == active_before)
    v.report["queued_http_experiment"] = {
        "world_id": smoke_world_id, "dataset_id": smoke_dataset_id, "model_id": smoke_model_id,
        "simulation_job_id": simulation["id"], "training_job_id": trained["id"],
        "simulation_worker_id": simulated_job["worker_id"], "training_worker_id": trained_job["worker_id"],
        "forecast_rows": len(small_forecast["rows"]), "learning_epochs": len(small_history["epochs"]),
        "purpose": "integration only; no small-experiment accuracy or generalization claim",
    }

    # Exercise real map and CSV adapters as HTTP, not as local function calls.
    imported_world = v.request("world_geojson_import", "/api/v1/worlds/import", method="POST", expected=201,
                              data={**world, "name": "HTTP真实数据适配器联调"})
    options = {"observation_start": "2026-01-01T00:00:00+08:00", "observation_end": "2026-01-03T00:00:00+08:00",
               "coverage_attested": True, "target_semantics": "completed_trips"}
    csv = f"order_id,requested_at,pickup_zone_id,passenger_count\nhttp_smoke_1,2026-01-01 08:01:00,{zones[0]},2\n"
    payload, content_type = multipart({"world_id": imported_world["id"], "options": json.dumps(options)}, "orders.csv", csv)
    imported = v.request("enqueue_csv_import_over_http", "/api/v1/datasets/import", method="POST", expected=202,
                         data=payload, content_type=content_type)
    imported_job = v.wait_job(imported["id"], "independent_worker_csv_import")
    imported_dataset_id = imported_job["result"]["resource_id"]
    imported_overview = v.request("http_import_quality_and_semantics", f"/api/v1/datasets/{imported_dataset_id}/overview")
    v.check("import_target_and_missing_supply_are_labelled", imported_overview["target_semantics"] == "completed_trips"
            and imported_overview["supply_observed"] is False)
    v.report["http_adapter_experiment"] = {"world_id": imported_world["id"], "dataset_id": imported_dataset_id,
                                           "job_id": imported["id"], "purpose": "single-row adapter integration fixture"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--demo", type=Path, default=Path("reports/demo.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/http-smoke.json"))
    args = parser.parse_args()
    started = time.monotonic()
    report = {"schema_version": "1.0", "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "base_url": args.base_url, "demo_file": str(args.demo), "transport": "real TCP HTTP",
              "client_pid": os.getpid(), "in_process_testclient": False, "total_timeout_seconds": 90, "checks": []}
    examples = {"schema_version": "1.0", "generated_at": report["started_at"], "base_url": args.base_url,
                "source": "responses from the 45-day reference demonstration over real HTTP", "examples": {}}
    examples_path = args.output.parent / "api-examples.json"
    exit_code = 0
    try:
        demo = strict_json(args.demo.read_text(encoding="utf-8"))
        verifier = Verifier(args.base_url, os.getenv("RIDECAST_API_KEY", ""), started + 90, report)
        run(verifier, demo, examples)
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        exit_code = 1
    report.update({"finished_at": datetime.now(timezone.utc).isoformat(),
                   "duration_seconds": round(time.monotonic() - started, 3),
                   "checks_passed": sum(row["status"] == "passed" for row in report["checks"]),
                   "checks_total": len(report["checks"]), "examples_file": str(examples_path)})
    write_json(args.output, report)
    write_json(examples_path, examples)
    print(json.dumps({"status": report["status"], "checks_passed": report["checks_passed"],
                      "checks_total": report["checks_total"], "duration_seconds": report["duration_seconds"],
                      "report": str(args.output.resolve()), "examples": str(examples_path.resolve()),
                      **({"failure": report["failure"]} if "failure" in report else {})}, ensure_ascii=False, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
