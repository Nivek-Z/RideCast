"""Versioned HTTP API for the forecasting laboratory and visual dashboards."""

import asyncio
import hmac
import importlib.util
import json
import logging
import os
import uuid
from pathlib import Path
from time import monotonic
from typing import Annotated
from zoneinfo import ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ridecast import __version__
from ridecast.responses import (
    ErrorEnvelope,
    Forecast,
    Job,
    JobList,
    LearningHistory,
    Resource,
    ResourceList,
    WorldMap,
)
from ridecast.schemas import (
    DispatchRequest,
    ForecastRequest,
    ImportOptions,
    SimulationCreate,
    TrainingCreate,
    WorldCreate,
)
from ridecast.settings import Settings
from ridecast.store import TERMINAL, Store, dump, identifier, now, valid_id

log = logging.getLogger("ridecast.api")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def create_app(settings=None):
    settings = settings or Settings()
    store = Store(settings)
    app = FastAPI(title="RideCast · 网约车需求实验室", version=__version__,
                  responses={code: {"model": ErrorEnvelope} for code in (401, 404, 409, 413, 422, 500)},
                  description="虚拟城市、真实订单导入、TensorFlow 预测及仪表盘接口。所有桶为左闭右开。")
    app.state.store = store
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-API-Key",
                       "Last-Event-ID", "X-Request-ID"], expose_headers=["Location", "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        start = monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Response-Time-Ms"] = str(round((monotonic() - start) * 1000, 2))
        return response

    def error(request, code, message, status, details=None):
        return JSONResponse(status_code=status, content=jsonable_encoder({
            "error": {"code": code, "message": message, "details": details},
            "request_id": getattr(request.state, "request_id", None)}))

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return error(request, "not_found", str(exc).strip("'"), 404)

    @app.exception_handler(ValueError)
    @app.exception_handler(ZoneInfoNotFoundError)
    async def bad_input(request, exc):
        return error(request, "invalid_input", str(exc), 422)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid_schema(request, exc):
        # Pydantic may put exception instances in ctx; stringify before JSON encoding.
        details = [{"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return error(request, "validation_error", "Request validation failed", 422, details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error(request, "http_error", str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        log.exception("Unhandled request %s", getattr(request.state, "request_id", None))
        return error(request, "internal_error", "Internal error; use request_id to find server logs", 500)

    def authorize(x_api_key: Annotated[str | None, Header()] = None):
        if settings.api_key and not hmac.compare_digest(x_api_key or "", settings.api_key):
            raise HTTPException(status_code=401, detail="Valid X-API-Key required")

    auth = [Depends(authorize)]

    def resource_path(kind, resource_id):
        store.resource(kind, resource_id)
        return store.path(kind, resource_id)

    def accepted(job):
        return JSONResponse(status_code=202, content=job, headers={"Location": f"/api/v1/jobs/{job['id']}"})

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok", "version": __version__, "server_time": now(), **store.status()}

    @app.get("/api/v1/system", tags=["system"], dependencies=auth)
    def system():
        return {"version": __version__, "server_time": now(), "api_version": "v1", "storage": "sqlite+parquet",
                "api_key_required": bool(settings.api_key),
                "tensorflow_installed": importlib.util.find_spec("tensorflow") is not None,
                "units": {"demand": "orders_per_bucket", "passengers": "people_per_bucket",
                          "temperature": "degC", "precipitation": "mm_per_hour", "wind_speed": "m_per_s",
                          "traffic_index": "dimensionless", "available_drivers": "driver_snapshot",
                          "distance": "km", "travel_minutes": "minutes"},
                "time_convention": "UTC ISO8601; local calendar uses world.timezone; [start,end)",
                "job_states": ["queued", "running", "cancelling", "succeeded", "failed", "cancelled"],
                "limits": {"upload_bytes_per_file": settings.max_upload_bytes, "max_panel_rows": 150000,
                           "max_page_size": 1000},
                "capabilities": ["geojson_layers", "scenario_simulation", "csv_import", "quality_report",
                                 "heatmap", "flows", "replay", "job_sse", "gru_forecast", "model_comparison",
                                 "dispatch_suggestions"], **store.status()}

    @app.post("/api/v1/worlds", status_code=201, response_model=Resource, tags=["worlds"], dependencies=auth)
    def create_world(body: WorldCreate):
        from ridecast.simulation import create_world

        world = create_world(**body.model_dump())
        return save_world(world)

    def save_world(world):
        from ridecast.simulation import validate_world

        world = validate_world(world)
        world_id = world["world_id"]
        path = store.path("world", world_id)
        # Serialize identical deterministic creates across API processes too.
        # An orphan directory from a crash can safely be completed on the next identical request.
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM resources WHERE id=?", (world_id,)).fetchone()
            if row:
                if dump(json.loads(row[0])) != dump(world):
                    raise HTTPException(status_code=409, detail="World identity collision")
            else:
                path.mkdir(parents=True, exist_ok=True)
                temporary = path / f"{uuid.uuid4().hex}.tmp"
                temporary.write_text(dump(world), encoding="utf-8")
                os.replace(temporary, path / "world.json")
                db.execute("INSERT INTO resources VALUES(?, 'world', ?, 'ready', ?, ?)",
                           (world_id, world["name"], dump(world), now()))
        return store.resource("world", world_id)

    @app.post("/api/v1/worlds/import", status_code=201, response_model=Resource, tags=["worlds"], dependencies=auth)
    def import_world(body: dict):
        world = {**body, "world_id": identifier("w")}
        return save_world(world)

    @app.get("/api/v1/worlds", response_model=ResourceList, tags=["worlds"], dependencies=auth)
    def worlds(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.resources("world", limit, offset)

    @app.get("/api/v1/worlds/{world_id}", tags=["worlds"], dependencies=auth)
    def world(world_id: str):
        return store.resource("world", world_id)["metadata"]

    @app.get("/api/v1/worlds/{world_id}/map", response_model=WorldMap, tags=["worlds"], dependencies=auth)
    def world_map(world_id: str):
        data = store.resource("world", world_id)["metadata"]
        return {"world_id": world_id, "crs": "EPSG:4326", "zones": data["zones"], "roads": data["roads"],
                "is_virtual": data.get("metadata", {}).get("fictional", False)}

    @app.get("/api/v1/worlds/{world_id}/active-model", tags=["models"], dependencies=auth)
    def active_model(world_id: str):
        return store.active(world_id)

    @app.get("/api/v1/worlds/{world_id}/state", tags=["dashboard"], dependencies=auth)
    def world_state(world_id: str, timestamp: str, seed: int = Query(42, ge=0, le=2**31 - 1)):
        from ridecast.simulation import evolution_snapshot
        world = store.resource("world", world_id)["metadata"]
        return evolution_snapshot(world, timestamp, seed)

    @app.post("/api/v1/simulations", status_code=202, response_model=Job, tags=["datasets"], dependencies=auth)
    def simulate(body: SimulationCreate):
        import pandas as pd
        world = store.resource("world", body.world_id)["metadata"]
        ts = pd.Timestamp(body.start)
        if ts.tzinfo is None:
            raise ValueError("start must include timezone")
        if ts.value % (world["bucket_minutes"] * 60 * 10**9):
            raise ValueError("start must align to the UTC bucket boundary")
        panel_rows = body.days * (1440 // world["bucket_minutes"]) * len(world["zones"]["features"])
        if panel_rows > 150000:
            raise ValueError("Requested simulation exceeds 150000 panel rows")
        params = {**body.model_dump(), "scenario": body.scenario.model_dump(exclude_unset=True),
                  "resource_id": identifier("d")}
        return accepted(store.enqueue("simulate", params))

    @app.post("/api/v1/datasets/import", status_code=202, response_model=Job, tags=["datasets"], dependencies=auth)
    async def import_dataset(world_id: Annotated[str, Form()], options: Annotated[str, Form()],
                             orders: Annotated[UploadFile, File()], context: Annotated[UploadFile | None, File()] = None):
        import shutil

        store.resource("world", world_id)
        parsed = ImportOptions.model_validate_json(options).model_dump()
        # Files are staged first; the job becomes visible only after both uploads finish.
        upload_id = identifier("j")
        upload_dir = settings.home / "uploads" / upload_id
        upload_dir.mkdir()
        try:
            for upload, name in ((orders, "orders.csv"), (context, "context.csv")):
                if upload is None:
                    continue
                total = 0
                with (upload_dir / name).open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > settings.max_upload_bytes:
                            raise HTTPException(status_code=413, detail="Upload exceeds file size limit")
                        stream.write(chunk)
                await upload.close()
            # Queue creation and upload rename must not race a worker: prepare directory ID in params.
            job = store.enqueue("import", {"world_id": world_id, "options": parsed,
                                          "resource_id": identifier("d"), "upload_id": upload_id})
            return accepted(job)
        except BaseException:
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise

    @app.get("/api/v1/datasets", response_model=ResourceList, tags=["datasets"], dependencies=auth)
    def datasets(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.resources("dataset", limit, offset)

    @app.get("/api/v1/datasets/{dataset_id}", response_model=Resource, tags=["datasets"], dependencies=auth)
    def dataset(dataset_id: str):
        return store.resource("dataset", dataset_id)

    @app.get("/api/v1/datasets/{dataset_id}/overview", tags=["dashboard"], dependencies=auth)
    def overview(dataset_id: str):
        from ridecast.dashboard import overview
        return overview(resource_path("dataset", dataset_id))

    @app.get("/api/v1/datasets/{dataset_id}/timeseries", tags=["dashboard"], dependencies=auth)
    def timeseries(dataset_id: str, zone_id: str | None = None, start: str | None = None,
                   end: str | None = None, limit: int = Query(1000, ge=1, le=10000)):
        from ridecast.dashboard import timeseries
        return timeseries(resource_path("dataset", dataset_id), zone_id, start, end, limit)

    @app.get("/api/v1/datasets/{dataset_id}/heatmap", tags=["dashboard"], dependencies=auth)
    def heatmap(dataset_id: str, timestamp: str | None = None):
        from ridecast.dashboard import heatmap
        return heatmap(resource_path("dataset", dataset_id), timestamp)

    @app.get("/api/v1/datasets/{dataset_id}/flows", tags=["dashboard"], dependencies=auth)
    def flows(dataset_id: str, start: str | None = None, end: str | None = None,
              limit: int = Query(100, ge=1, le=1000)):
        from ridecast.dashboard import flows
        return flows(resource_path("dataset", dataset_id), start, end, limit)

    @app.get("/api/v1/datasets/{dataset_id}/replay", tags=["dashboard"], dependencies=auth)
    def replay(dataset_id: str, timestamp: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        from ridecast.dashboard import replay
        return replay(resource_path("dataset", dataset_id), timestamp, limit)

    @app.get("/api/v1/datasets/{dataset_id}/orders", tags=["dashboard"], dependencies=auth)
    def orders(dataset_id: str, zone_id: str | None = None, status: str | None = None,
               offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        from ridecast.dashboard import list_orders
        return list_orders(resource_path("dataset", dataset_id), zone_id, status, offset, limit)

    @app.post("/api/v1/training-jobs", status_code=202, response_model=Job, tags=["models"], dependencies=auth)
    def training(body: TrainingCreate):
        store.resource("dataset", body.dataset_id)
        return accepted(store.enqueue("train", {**body.model_dump(), "resource_id": identifier("m")}))

    @app.get("/api/v1/models", response_model=ResourceList, tags=["models"], dependencies=auth)
    def models(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.resources("model", limit, offset)

    @app.get("/api/v1/models/{model_id}", response_model=Resource, tags=["models"], dependencies=auth)
    def model(model_id: str):
        return store.resource("model", model_id)

    @app.get("/api/v1/models/{model_id}/history", response_model=LearningHistory, tags=["models"], dependencies=auth)
    def history(model_id: str):
        return read_json(resource_path("model", model_id) / "history.json")

    @app.get("/api/v1/models/{model_id}/evaluation", tags=["models"], dependencies=auth)
    def evaluation(model_id: str, limit: int = Query(1000, ge=1, le=10000), offset: int = Query(0, ge=0)):
        import pandas as pd
        path = resource_path("model", model_id)
        frame = pd.read_parquet(path / "test_predictions.parquet")
        return {"model_id": model_id, "total": len(frame), "offset": offset, "limit": limit,
                "rows": json.loads(frame.iloc[offset:offset + limit].to_json(orient="records", date_format="iso"))}

    @app.post("/api/v1/models/{model_id}/activate", tags=["models"], dependencies=auth)
    def activate(model_id: str):
        return store.activate(model_id)

    @app.post("/api/v1/forecasts", response_model=Forecast, tags=["prediction"], dependencies=auth)
    def forecasts(body: ForecastRequest):
        from ridecast.ml import forecast
        return forecast(resource_path("model", body.model_id), resource_path("dataset", body.dataset_id),
                        body.horizon, body.scenario)

    @app.post("/api/v1/dispatch-suggestions", tags=["prediction"], dependencies=auth)
    def dispatch_suggestions(body: DispatchRequest):
        from ridecast.dashboard import dispatch, heatmap
        from ridecast.ml import forecast

        directory = resource_path("dataset", body.dataset_id)
        result = forecast(resource_path("model", body.model_id), directory, 1)
        world = read_json(directory / "world.json")
        snapshot = heatmap(directory)
        supply = body.supply
        if supply is None and snapshot.get("supply_observed", False):
            supply = {r["zone_id"]: r["available_drivers"] for r in snapshot["rows"]}
        suggestion = dispatch(result, world, supply)
        manifest = read_json(directory / "manifest.json")
        suggestion["source"] = manifest["source"]
        suggestion["target_semantics"] = manifest["target_semantics"]
        suggestion["supply_source"] = ("caller" if body.supply is not None else
                                       "last_observed_bucket" if supply is not None else "unavailable")
        suggestion["supply_snapshot_timestamp"] = (snapshot.get("timestamp")
                                                  if body.supply is None and supply is not None else None)
        return suggestion

    @app.get("/api/v1/jobs", response_model=JobList, tags=["jobs"], dependencies=auth)
    def jobs(state: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        if state and state not in {"queued", "running", "cancelling", *TERMINAL}:
            raise ValueError("Unknown job state")
        return store.jobs(state, limit, offset)

    @app.get("/api/v1/jobs/{job_id}", response_model=Job, tags=["jobs"], dependencies=auth)
    def job(job_id: str):
        return store.job(job_id)

    @app.delete("/api/v1/jobs/{job_id}", response_model=Job, tags=["jobs"], dependencies=auth)
    def cancel(job_id: str):
        import shutil
        result = store.cancel(job_id)
        if result["kind"] == "import" and result["state"] == "cancelled":
            upload_id = result["params"].get("upload_id", job_id)
            shutil.rmtree(settings.home / "uploads" / valid_id(upload_id), ignore_errors=True)
        return result

    @app.get("/api/v1/jobs/{job_id}/history", response_model=LearningHistory, tags=["jobs"], dependencies=auth)
    def live_history(job_id: str):
        job = store.job(job_id)
        if job["kind"] != "train":
            raise ValueError("Learning curves are only available for training jobs")
        if job["state"] == "succeeded":
            return {**read_json(store.path("model", job["result"]["resource_id"]) / "history.json"),
                    "job_id": job_id, "state": job["state"]}
        candidates = [settings.home / ".staging" / job_id / "live_history.json",
                      settings.home / "jobs" / job_id / "history.json"]
        for path in candidates:
            try:
                return {**read_json(path), "job_id": job_id, "state": job["state"]}
            except FileNotFoundError:
                continue
        return {"state": job["state"], "candidates": [], "epochs": []}

    @app.get("/api/v1/jobs/{job_id}/events", tags=["jobs"], dependencies=auth,
             response_class=StreamingResponse,
             responses={200: {"description": "Durable server-sent job events",
                              "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    async def events(job_id: str, request: Request, after: int = Query(0, ge=0),
                     last_event_id: Annotated[str | None, Header()] = None):
        store.job(job_id)
        if last_event_id:
            try:
                after = max(after, int(last_event_id))
            except ValueError:
                raise ValueError("Last-Event-ID must be an integer") from None

        async def stream():
            cursor, last_ping = after, monotonic()
            while not await request.is_disconnected():
                batch = store.events(job_id, cursor)
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {dump(event)}\n\n"
                if store.job(job_id)["state"] in TERMINAL and len(batch) < 200:
                    yield f"event: done\ndata: {dump({'state': store.job(job_id)['state']})}\n\n"
                    return
                if monotonic() - last_ping > 15:
                    yield ": keepalive\n\n"
                    last_ping = monotonic()
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


# Uvicorn --factory avoids creating SQLite files merely by importing modules.
def app_factory():
    return create_app()
