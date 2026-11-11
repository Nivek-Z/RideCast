from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from ridecast.settings import Settings
from ridecast.store import Store


def test_atomic_claim_no_duplicate_and_progress_monotonic(tmp_path):
    store = Store(Settings(home=tmp_path))
    ids = [store.enqueue("simulate", {"i": i})["id"] for i in range(12)]

    def consume(worker):
        claimed = []
        while job := store.claim(worker):
            claimed.append(job["id"])
        return claimed

    with ThreadPoolExecutor(max_workers=3) as pool:
        claimed = sum(pool.map(consume, ["a", "b", "c"]), [])
    assert sorted(claimed) == sorted(ids)
    store.progress(ids[0], "a", 0.8, "first")
    store.progress(ids[0], "b", 0.2, "later")
    assert store.job(ids[0])["progress"] == 0.8
    store.finish(ids[0], "succeeded", {"resource_id": "x"})
    assert store.job(ids[0])["progress"] == 1
    assert store.events(ids[0])[-1]["payload"]["state"] == "succeeded"


def test_cancel_and_expired_worker_recovery(tmp_path):
    store = Store(Settings(home=tmp_path))
    queued = store.enqueue("simulate", {})
    assert store.cancel(queued["id"])["state"] == "cancelled"
    assert store.claim("worker") is None
    running = store.enqueue("train", {})
    store.claim("worker")
    assert store.cancel(running["id"])["state"] == "cancelling"
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with store.connection() as db:
        db.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (stale, running["id"]))
    assert store.recover_stale() == 1
    assert store.job(running["id"])["error"]["code"] == "worker_lost"
    # A fresh Store sees the same durable results, without resetting state.
    reopened = Store(Settings(home=tmp_path))
    assert reopened.job(queued["id"])["state"] == "cancelled"
    assert reopened.job(running["id"])["state"] == "failed"


def test_resource_identifier_cannot_escape_storage(tmp_path):
    import pytest
    store = Store(Settings(home=tmp_path))
    with pytest.raises(ValueError):
        store.path("world", "../../secrets")


def test_lost_worker_cannot_publish_and_success_is_transactional(tmp_path):
    store = Store(Settings(home=tmp_path))
    job = store.enqueue("simulate", {})
    claimed = store.claim("worker-a")
    staging = tmp_path / ".staging" / job["id"]
    staging.mkdir()
    (staging / "manifest.json").write_text("{}")
    store.finish(job["id"], "failed", error={"code": "worker_lost"})
    assert store.publish(claimed, "dataset", "d_aaaaaaaaaaaaaaaa", "x", {}, staging) is False
    assert store.resources("dataset")["total"] == 0
    assert staging.exists()
    next_job = store.enqueue("simulate", {})
    next_claimed = store.claim("worker-b")
    assert store.publish(next_claimed, "dataset", "d_bbbbbbbbbbbbbbbb", "x", {}, staging) is True
    assert store.resource("dataset", "d_bbbbbbbbbbbbbbbb")["status"] == "ready"
    assert store.job(next_job["id"])["state"] == "succeeded"
    assert store.events(next_job["id"])[-1]["payload"]["result"]["resource_id"] == "d_bbbbbbbbbbbbbbbb"
