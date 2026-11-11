"""Small durable registry and atomic single-host job queue (SQLite WAL)."""

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from ridecast.settings import Settings

TERMINAL = {"succeeded", "failed", "cancelled"}
KINDS = {"world": "worlds", "dataset": "datasets", "model": "models"}


def now():
    return datetime.now(UTC).isoformat()


def identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def valid_id(value):
    if not re.fullmatch(r"[wdmj]_[a-f0-9]{16}", value):
        raise ValueError("Invalid resource identifier")
    return value


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.initialize()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS resource_kind ON resources(kind, created_at);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'queued',
                    message TEXT NOT NULL DEFAULT '', params TEXT NOT NULL, result TEXT,
                    error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    heartbeat_at TEXT, worker_id TEXT
                );
                CREATE INDEX IF NOT EXISTS job_state ON jobs(state, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_job ON events(job_id, id);
                CREATE TABLE IF NOT EXISTS workers (
                    id TEXT PRIMARY KEY, heartbeat_at TEXT NOT NULL, current_job_id TEXT
                );
                CREATE TABLE IF NOT EXISTS active_models (
                    world_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, activated_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.settings.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def path(self, kind, resource_id):
        return self.settings.home / KINDS[kind] / valid_id(resource_id)

    def add_resource(self, kind, resource_id, name, payload, status="ready"):
        with self.connection() as db:
            db.execute("INSERT INTO resources VALUES (?, ?, ?, ?, ?, ?)",
                       (valid_id(resource_id), kind, name, status, dump(payload), now()))
        return self.resource(kind, resource_id)

    @staticmethod
    def _resource(row):
        result = dict(row)
        result["metadata"] = json.loads(result.pop("payload"))
        return result

    def resource(self, kind, resource_id):
        valid_id(resource_id)
        with self.connection() as db:
            row = db.execute("SELECT * FROM resources WHERE kind=? AND id=?", (kind, resource_id)).fetchone()
        if row is None:
            raise KeyError(f"{kind} {resource_id} not found")
        return self._resource(row)

    def resources(self, kind, limit=100, offset=0):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM resources WHERE kind=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              (kind, limit, offset)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM resources WHERE kind=?", (kind,)).fetchone()[0]
        return {"items": [self._resource(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def _event(self, db, job_id, event_type, payload):
        db.execute("INSERT INTO events(job_id,event_type,payload,created_at) VALUES (?,?,?,?)",
                   (job_id, event_type, dump(payload), now()))

    def enqueue(self, kind, params):
        job_id = identifier("j")
        with self.connection() as db:
            db.execute("INSERT INTO jobs(id,kind,state,params,created_at) VALUES (?,?,'queued',?,?)",
                       (job_id, kind, dump(params), now()))
            self._event(db, job_id, "state", {"state": "queued", "progress": 0, "stage": "queued"})
        return self.job(job_id)

    @staticmethod
    def _job(row):
        result = dict(row)
        for key in ("params", "result", "error"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def job(self, job_id):
        valid_id(job_id)
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return self._job(row)

    def jobs(self, state=None, limit=100, offset=0):
        where, args = ("WHERE state=?", [state]) if state else ("", [])
        with self.connection() as db:
            total = db.execute(f"SELECT COUNT(*) FROM jobs {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              [*args, limit, offset]).fetchall()
        return {"items": [self._job(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def claim(self, worker_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            job_id = row[0]
            db.execute("UPDATE jobs SET state='running',stage='starting',started_at=?,heartbeat_at=?,worker_id=? "
                       "WHERE id=?", (now(), now(), worker_id, job_id))
            self._event(db, job_id, "state", {"state": "running", "stage": "starting", "progress": 0})
        return self.job(job_id)

    def heartbeat(self, job_id):
        with self.connection() as db:
            db.execute("UPDATE jobs SET heartbeat_at=? WHERE id=? AND state IN ('running','cancelling')",
                       (now(), job_id))

    def progress(self, job_id, stage, fraction, message):
        fraction = max(0, min(0.999, float(fraction)))
        with self.connection() as db:
            row = db.execute("SELECT state,progress FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row[0] not in {"running", "cancelling"}:
                return
            fraction = max(fraction, row[1])
            db.execute("UPDATE jobs SET progress=?,stage=?,message=?,heartbeat_at=? WHERE id=?",
                       (fraction, stage, message, now(), job_id))
            self._event(db, job_id, "progress", {"stage": stage, "progress": fraction, "message": message})

    def finish(self, job_id, state, result=None, error=None):
        if state not in TERMINAL:
            raise ValueError("Invalid terminal state")
        with self.connection() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row[0] in TERMINAL:
                return
            db.execute("UPDATE jobs SET state=?,stage=?,progress=CASE WHEN ?='succeeded' THEN 1 ELSE progress END,"
                       "result=?,error=?,finished_at=?,heartbeat_at=? WHERE id=?",
                       (state, state, state, dump(result) if result is not None else None,
                        dump(error) if error is not None else None, now(), now(), job_id))
            self._event(db, job_id, "state", {"state": state, "result": result, "error": error})

    def publish(self, job, kind, resource_id, name, payload, staging):
        """Fence a lost worker and atomically register its output + successful job.

        Filesystem rename occurs while the SQLite write transaction is held. A
        crash can leave an unregistered directory, but never a ready partial resource.
        """
        destination = self.path(kind, resource_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,worker_id FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if row is None or row["state"] != "running" or row["worker_id"] != job["worker_id"]:
                return False
            if destination.exists():
                raise ValueError("Immutable resource destination already exists")
            os.replace(staging, destination)
            try:
                db.execute("INSERT INTO resources VALUES(?,?,?,?,?,?)",
                           (resource_id, kind, name, "ready", dump(payload), now()))
                result = {"resource_type": kind, "resource_id": resource_id}
                db.execute("UPDATE jobs SET state='succeeded',stage='succeeded',progress=1,result=?,"
                           "finished_at=?,heartbeat_at=? WHERE id=?", (dump(result), now(), now(), job["id"]))
                self._event(db, job["id"], "state", {"state": "succeeded", "result": result, "error": None})
            except BaseException:
                # Put the unpublished bundle back for ordinary job cleanup.
                os.replace(destination, staging)
                raise
        return True

    def cancel(self, job_id):
        self.job(job_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            if state in TERMINAL:
                return self.job(job_id)
            target = "cancelled" if state == "queued" else "cancelling"
            db.execute("UPDATE jobs SET state=?,stage=?,finished_at=? WHERE id=?",
                       (target, target, now() if target == "cancelled" else None, job_id))
            self._event(db, job_id, "state", {"state": target})
        return self.job(job_id)

    def recover_stale(self):
        cutoff = (datetime.now(UTC) - timedelta(seconds=self.settings.stale_seconds)).isoformat()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM jobs WHERE state IN ('running','cancelling') "
                              "AND heartbeat_at < ?", (cutoff,)).fetchall()
            for row in rows:
                error = {"code": "worker_lost", "message": "Worker heartbeat expired; submit a new job."}
                db.execute("UPDATE jobs SET state='failed',stage='failed',error=?,finished_at=? WHERE id=?",
                           (dump(error), now(), row[0]))
                self._event(db, row[0], "state", {"state": "failed", "error": error})
        return len(rows)

    def events(self, job_id, after=0, limit=200):
        self.job(job_id)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                              (job_id, after, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def status(self):
        with self.connection() as db:
            counts = dict(db.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state").fetchall())
            latest = db.execute("SELECT MAX(heartbeat_at) FROM jobs").fetchone()[0]
            workers = [dict(r) for r in db.execute("SELECT * FROM workers ORDER BY heartbeat_at DESC LIMIT 20")]
        for worker in workers:
            age = (datetime.now(UTC) - datetime.fromisoformat(worker["heartbeat_at"])).total_seconds()
            worker["online"] = age < self.settings.stale_seconds
        return {"job_counts": counts, "last_job_heartbeat_at": latest, "database": "ok", "workers": workers,
                "worker_online": any(w["online"] for w in workers)}

    def worker_heartbeat(self, worker_id, job_id=None):
        with self.connection() as db:
            db.execute("INSERT INTO workers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
                       "heartbeat_at=excluded.heartbeat_at,current_job_id=excluded.current_job_id",
                       (worker_id, now(), job_id))

    def activate(self, model_id):
        model = self.resource("model", model_id)
        world_id = model["metadata"]["world_id"]
        with self.connection() as db:
            db.execute("INSERT INTO active_models VALUES(?,?,?) ON CONFLICT(world_id) DO UPDATE SET "
                       "model_id=excluded.model_id,activated_at=excluded.activated_at", (world_id, model_id, now()))
        return {"world_id": world_id, "model_id": model_id, "status": "active"}

    def active(self, world_id):
        self.resource("world", world_id)
        with self.connection() as db:
            row = db.execute("SELECT * FROM active_models WHERE world_id=?", (world_id,)).fetchone()
        return dict(row) if row else {"world_id": world_id, "model_id": None}
