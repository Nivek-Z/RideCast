"""Run durable jobs in a process separate from the HTTP server."""

import argparse
import json
import logging
import shutil
import threading
import time
import traceback

from ridecast.settings import Settings
from ridecast.store import Store, identifier

log = logging.getLogger("ridecast.worker")


class JobCancelled(Exception):
    pass


class OwnershipLost(Exception):
    pass


def execute_job(store: Store, job: dict):
    job_id, params = job["id"], job["params"]
    staging = store.settings.home / ".staging" / job_id
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    def check_cancel():
        current = store.job(job_id)
        if current["state"] in {"cancelling", "cancelled"}:
            raise JobCancelled()
        if current["state"] != "running" or current["worker_id"] != job["worker_id"]:
            raise OwnershipLost()

    def progress(stage, fraction, message):
        check_cancel()
        store.progress(job_id, stage, fraction, message)

    try:
        check_cancel()
        kind = job["kind"]
        resource_id = params["resource_id"]
        if kind == "simulate":
            from ridecast.data import write_dataset
            from ridecast.simulation import simulate

            world = store.resource("world", params["world_id"])["metadata"]
            panel, orders, metadata = simulate(world, params["start"], params["days"], params["seed"],
                                                params.get("scenario"), progress)
            progress("saving", 0.95, "Persisting immutable dataset")
            payload = write_dataset(staging, world, panel, orders, metadata, resource_id)
            resource_kind, name = "dataset", f"{world['name']} · synthetic"
        elif kind == "import":
            from ridecast.data import import_orders

            world = store.resource("world", params["world_id"])["metadata"]
            upload = store.settings.home / "uploads" / params.get("upload_id", job_id)
            payload = import_orders(upload / "orders.csv", world, staging, resource_id, params["options"],
                                    (upload / "context.csv") if (upload / "context.csv").exists() else None,
                                    progress)
            resource_kind, name = "dataset", f"{world['name']} · imported"
        elif kind == "train":
            from ridecast.ml import train_model

            store.resource("dataset", params["dataset_id"])
            payload = train_model(store.path("dataset", params["dataset_id"]), staging, resource_id,
                                  params["config"], progress)
            resource_kind, name = "model", f"GRU · {params['dataset_id']}"
        else:
            raise ValueError(f"Unsupported job kind {kind}")
        check_cancel()
        if not store.publish(job, resource_kind, resource_id, name, payload, staging):
            check_cancel()
            raise OwnershipLost()
    except JobCancelled:
        store.finish(job_id, "cancelled")
    except OwnershipLost:
        log.warning("Job %s lost ownership; output was not published", job_id)
    except Exception as exc:
        log.error("Job %s failed\n%s", job_id, traceback.format_exc())
        code = "invalid_input" if isinstance(exc, (ValueError, KeyError)) else "execution_failed"
        store.finish(job_id, "failed", error={"code": code, "type": type(exc).__name__, "message": str(exc)[:2000]})
    finally:
        live = staging / "live_history.json"
        if live.exists():
            archive = store.settings.home / "jobs" / job_id
            archive.mkdir(exist_ok=True)
            shutil.copyfile(live, archive / "history.json.tmp")
            (archive / "history.json.tmp").replace(archive / "history.json")
        shutil.rmtree(staging, ignore_errors=True)
        if job["kind"] == "import":
            shutil.rmtree(store.settings.home / "uploads" / params.get("upload_id", job_id), ignore_errors=True)


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.worker_id = identifier("j")
        self.current = None

    def _heartbeat(self, stop):
        while not stop.wait(5):
            try:
                self.store.worker_heartbeat(self.worker_id, self.current)
                if self.current:
                    self.store.heartbeat(self.current)
            except Exception:
                log.exception("Heartbeat failed")

    def run(self, once=False, poll_seconds=1):
        self.store.recover_stale()
        self.store.worker_heartbeat(self.worker_id)
        stop = threading.Event()
        thread = threading.Thread(target=self._heartbeat, args=(stop,), daemon=True)
        thread.start()
        try:
            while True:
                job = self.store.claim(self.worker_id)
                if job:
                    self.current = job["id"]
                    self.store.worker_heartbeat(self.worker_id, self.current)
                    execute_job(self.store, job)
                    self.current = None
                    self.store.worker_heartbeat(self.worker_id)
                if once:
                    return job is not None
                if not job:
                    self.store.recover_stale()
                    time.sleep(poll_seconds)
        finally:
            stop.set()
            thread.join(timeout=6)
            with self.store.connection() as db:
                db.execute("DELETE FROM workers WHERE id=?", (self.worker_id,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Claim at most one job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker(Store(Settings()))
    if args.once:
        print(json.dumps({"processed": worker.run(once=True)}))
    else:
        worker.run()


if __name__ == "__main__":
    main()
