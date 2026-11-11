import json

import pytest
from fastapi.testclient import TestClient

from ridecast.api import create_app
from ridecast.settings import Settings
from ridecast.worker import Worker


@pytest.fixture
def environment(tmp_path):
    app = create_app(Settings(home=tmp_path, api_key="test-secret"))
    client = TestClient(app)
    client.headers["X-API-Key"] = "test-secret"
    return client, app.state.store


def make_world(client):
    response = client.post("/api/v1/worlds", json={"rows": 2, "cols": 2, "seed": 7})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_auth_errors_world_reproducibility_and_openapi(environment):
    client, store = environment
    client.headers.pop("X-API-Key")
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/system").status_code == 401
    client.headers["X-API-Key"] = "test-secret"
    first = make_world(client)
    assert make_world(client) == first
    assert store.resources("world")["total"] == 1
    assert client.get(f"/api/v1/worlds/{first}/map").json()["is_virtual"] is True
    snapshot = client.get(f"/api/v1/worlds/{first}/state", params={"timestamp": "2026-01-01T00:00:00+08:00"})
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["status"] == "synthetic_observation"
    bad = client.post("/api/v1/worlds", json={"timezone": "Invalid/Timezone"})
    assert bad.status_code == 422 and bad.json()["request_id"]
    unknown = client.get("/api/v1/worlds/w_aaaaaaaaaaaaaaaa")
    assert unknown.status_code == 404
    assert "X-Request-ID" in unknown.headers
    assert client.get("/openapi.json").json()["info"]["version"] == "0.2.0"


def test_real_simulation_queue_dashboard_and_terminal_sse(environment):
    client, store = environment
    world_id = make_world(client)
    queued = client.post("/api/v1/simulations", json={"world_id": world_id, "days": 2, "seed": 7})
    assert queued.status_code == 202 and queued.headers["Location"]
    job_id = queued.json()["id"]
    assert store.resources("dataset")["total"] == 0
    Worker(store).run(once=True)
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["state"] == "succeeded", job
    dataset_id = job["result"]["resource_id"]
    prefix = f"/api/v1/datasets/{dataset_id}"
    for suffix in ("overview", "timeseries", "heatmap", "flows", "replay", "orders"):
        response = client.get(f"{prefix}/{suffix}")
        assert response.status_code == 200, response.text
        assert "NaN" not in response.text
    assert client.get(f"{prefix}/orders?limit=2").json()["limit"] == 2
    assert client.get(f"{prefix}/heatmap?timestamp=2001-01-01T00:00:00Z").status_code == 422
    assert client.get(f"{prefix}/timeseries?zone_id=absent").status_code == 422
    events = client.get(f"/api/v1/jobs/{job_id}/events")
    assert events.status_code == 200
    assert "event: done" in events.text and "succeeded" in events.text
    # Only genuinely new events are replayed after Last-Event-ID.
    last_id = store.events(job_id)[-1]["id"]
    resumed = client.get(f"/api/v1/jobs/{job_id}/events", headers={"Last-Event-ID": str(last_id)})
    assert "event: done" in resumed.text and "event: progress" not in resumed.text


def test_import_upload_job_quality_and_cleanup(environment):
    client, store = environment
    world_id = make_world(client)
    options = {"observation_start": "2026-01-01T00:00:00+08:00",
               "observation_end": "2026-01-03T00:00:00+08:00", "coverage_attested": True,
               "target_semantics": "completed_trips"}
    csv = "order_id,requested_at,pickup_zone_id,passenger_count\na,2026-01-01 00:01:00,z_000,2\n"
    response = client.post("/api/v1/datasets/import", data={"world_id": world_id, "options": json.dumps(options)},
                           files={"orders": ("orders.csv", csv, "text/csv")})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    Worker(store).run(once=True)
    job = store.job(job_id)
    assert job["state"] == "succeeded", job
    dataset_id = job["result"]["resource_id"]
    overview = client.get(f"/api/v1/datasets/{dataset_id}/overview").json()
    assert overview["supply_observed"] is False
    assert not list((store.settings.home / "uploads").iterdir())
    options["coverage_attested"] = False
    invalid = client.post("/api/v1/datasets/import", data={"world_id": world_id, "options": json.dumps(options)},
                          files={"orders": ("orders.csv", csv)})
    assert invalid.status_code == 422


def test_failed_job_does_not_publish_resource_and_cancel_is_durable(environment):
    client, store = environment
    world_id = make_world(client)
    pending = client.post("/api/v1/simulations", json={"world_id": world_id, "days": 2}).json()
    assert client.delete(f"/api/v1/jobs/{pending['id']}").json()["state"] == "cancelled"
    invalid = store.enqueue("unsupported", {"resource_id": "d_aaaaaaaaaaaaaaaa"})
    Worker(store).run(once=True)
    assert store.job(invalid["id"])["state"] == "failed"
    assert store.resources("dataset")["total"] == 0
    assert not list((store.settings.home / ".staging").iterdir())


def test_default_scenario_preserves_world_plan_and_cancelled_import_cleans_upload(environment):
    client, store = environment
    world_id = make_world(client)
    job = client.post("/api/v1/simulations", json={"world_id": world_id, "days": 2}).json()
    assert job["params"]["scenario"] == {}
    options = {"observation_start": "2026-01-01T00:00:00+08:00",
               "observation_end": "2026-01-03T00:00:00+08:00", "coverage_attested": True,
               "target_semantics": "completed_trips"}
    queued = client.post("/api/v1/datasets/import", data={"world_id": world_id, "options": json.dumps(options)},
                         files={"orders": ("a.csv", "order_id,requested_at,pickup_zone_id,passenger_count\n")}).json()
    upload_dir = store.settings.home / "uploads" / queued["params"]["upload_id"]
    assert upload_dir.exists()
    assert client.delete(f"/api/v1/jobs/{queued['id']}").json()["state"] == "cancelled"
    assert not upload_dir.exists()
