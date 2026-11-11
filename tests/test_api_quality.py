"""Regression checks for scenario-consistent forecast and dispatch contracts.

Only forecast inference is replaced to make HTTP argument propagation observable;
world geometry, dataset aggregation and dispatch allocation use real domain code.
Real model training/serialization is exercised separately by test_ml.py.
"""

import copy

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ridecast.api import create_app
from ridecast.dashboard import heatmap
from ridecast.data import write_dataset
from ridecast.settings import Settings
from ridecast.simulation import simulate


@pytest.fixture
def quality_api(tmp_path, monkeypatch):
    app = create_app(Settings(home=tmp_path, api_key="test-secret"))
    client = TestClient(app, headers={"X-API-Key": "test-secret"})
    store = app.state.store
    created = client.post("/api/v1/worlds", json={"rows": 1, "cols": 2, "seed": 7})
    assert created.status_code == 201, created.text
    world = created.json()["metadata"]
    dataset_id, model_id = "d_0000000000000001", "m_0000000000000001"
    directory = store.path("dataset", dataset_id)
    panel, orders, metadata = simulate(world, "2026-01-01T00:00:00+08:00", 1, 7)
    manifest = write_dataset(directory, world, panel, orders, metadata, dataset_id)
    store.add_resource("dataset", dataset_id, "scenario contract data", manifest)
    # Registration is enough: the HTTP test double does not read model files.
    store.add_resource("model", model_id, "inference argument probe", {
        "world_id": world["world_id"], "dataset_id": dataset_id,
    })
    zone_ids = [f["properties"]["zone_id"] for f in world["zones"]["features"]]
    calls = []

    def inference_probe(model_dir, dataset_dir, horizon=None, scenario=None):
        calls.append({"model_dir": model_dir, "dataset_dir": dataset_dir,
                      "horizon": horizon, "scenario": copy.deepcopy(scenario)})
        scenario = copy.deepcopy(scenario or {})
        horizon = horizon or 2
        multiplier = scenario.get("demand_multiplier", 1)
        origin = pd.Timestamp(manifest["observation_end"])
        rows = []
        totals = []
        for h in range(horizon):
            timestamp = (origin + pd.Timedelta(hours=h)).isoformat()
            for index, zone_id in enumerate(zone_ids):
                predicted = (2 + 28 * index + h) * multiplier
                rows.append({"timestamp": timestamp, "zone_id": zone_id, "horizon": h + 1,
                             "predicted_demand": predicted, "lower": max(0, predicted - 1),
                             "upper": predicted + 1})
            totals.append({"timestamp": timestamp, "horizon": h + 1,
                           "predicted_demand": sum(r["predicted_demand"] for r in rows if r["horizon"] == h + 1)})
        return {"schema_version": "1.0", "model_id": model_id, "dataset_id": dataset_id,
                "world_id": world["world_id"], "source": "synthetic", "target_semantics": "requests",
                "forecast_origin": origin.isoformat(), "bucket_minutes": 60,
                "timezone": world["timezone"], "horizon": horizon, "rows": rows, "totals": totals,
                "interval": {}, "exogenous_assumption": "HTTP inference-argument fixture",
                "scenario": scenario}

    monkeypatch.setattr("ridecast.ml.forecast", inference_probe)
    return client, store, dataset_id, model_id, zone_ids, calls


@pytest.mark.parametrize("custom_supply", [False, True], ids=["latest-observed-supply", "explicit-integer-supply"])
def test_dispatch_uses_same_scenario_and_first_bucket_as_displayed_forecast(quality_api, custom_supply):
    client, store, dataset_id, model_id, zone_ids, calls = quality_api
    scenario = {"demand_multiplier": 2.5, "rain_multiplier": 1.2, "events": []}
    selection = {"dataset_id": dataset_id, "model_id": model_id}
    displayed = client.post("/api/v1/forecasts", json={**selection, "horizon": 2, "scenario": scenario})
    assert displayed.status_code == 200, displayed.text
    first_bucket = {r["zone_id"]: r["predicted_demand"] for r in displayed.json()["rows"] if r["horizon"] == 1}
    body = {**selection, "scenario": displayed.json()["scenario"]}
    if custom_supply:
        body["supply"] = {zone_ids[0]: 100, zone_ids[1]: 0}
    response = client.post("/api/v1/dispatch-suggestions", json=body)
    assert response.status_code == 200, response.text
    advice = response.json()
    assert advice["scenario"] == scenario
    assert {z["zone_id"]: z["predicted_demand"] for z in advice["zones"]} == first_bucket
    assert [c["scenario"] for c in calls] == [scenario, scenario]
    assert [c["horizon"] for c in calls] == [2, 1]
    assert all(c["model_dir"] == store.path("model", model_id) for c in calls)
    assert all(c["dataset_dir"] == store.path("dataset", dataset_id) for c in calls)
    assert advice["executed"] is False
    assert advice["supply_known"] is True
    assert advice["conservation"]["preserved"] is True
    if custom_supply:
        expected_supply = body["supply"]
        assert advice["supply_source"] == "caller"
        assert advice["supply_snapshot_timestamp"] is None
        assert advice["recommendations"]  # Surplus in one region must reach the other region's shortage.
    else:
        snapshot = heatmap(store.path("dataset", dataset_id))
        expected_supply = {row["zone_id"]: row["available_drivers"] for row in snapshot["rows"]}
        assert advice["supply_source"] == "last_observed_bucket"
        assert advice["supply_snapshot_timestamp"] == snapshot["timestamp"]
    assert {z["zone_id"]: z["available_before"] for z in advice["zones"]} == expected_supply


def test_dispatch_without_scenario_keeps_older_client_compatibility(quality_api):
    client, _, dataset_id, model_id, _, calls = quality_api
    response = client.post("/api/v1/dispatch-suggestions", json={"dataset_id": dataset_id, "model_id": model_id})
    assert response.status_code == 200, response.text
    assert calls[-1]["scenario"] is None
    assert response.json()["scenario"] == {}


@pytest.mark.parametrize("invalid", [1.5, "3", True, -1])
def test_dispatch_rejects_non_integer_or_negative_supply_before_inference(quality_api, invalid):
    client, _, dataset_id, model_id, zone_ids, calls = quality_api
    response = client.post("/api/v1/dispatch-suggestions", json={
        "dataset_id": dataset_id, "model_id": model_id,
        "supply": {zone_ids[0]: invalid, zone_ids[1]: 0},
        "scenario": {"demand_multiplier": 2},
    })
    assert response.status_code == 422, response.text
    assert calls == []
