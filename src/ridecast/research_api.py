"""Local defense API. All experimental work is owned by the worker."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ridecast.research_schema import RESEARCH_MODES, ArenaBatch
from ridecast.responses import Job
from ridecast.settings import Settings
from ridecast.store import Store, identifier


def create_app(settings=None, web_root=None):
    settings = settings or Settings()
    store = Store(settings)
    app = FastAPI(title="RideCast · 网约车需求研究", version="2.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    def require_archive():
        if not settings.teacher_archive.is_file():
            raise HTTPException(409, "请先在本机配置老师提供的 ZIP 数据")

    def find_run(run_id):
        try:
            job = store.job(run_id)
            if job["kind"] == "research" and job["params"].get("mode") in RESEARCH_MODES:
                return job
        except (ValueError, KeyError):
            pass
        raise HTTPException(404, "未找到此实验")

    def summary(job):
        if job["result"]:
            resource = store.resource("research", job["result"]["resource_id"])
            return {**job, "summary": resource["metadata"]}
        return {**job, "summary": None}

    @app.get("/api/research/status")
    def status():
        return {"archive_ready": settings.teacher_archive.is_file(), "worker_online": store.status()["worker_online"]}

    @app.get("/api/research/runs")
    def runs(limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)):
        page = store.jobs(kind="research", modes=RESEARCH_MODES, limit=limit, offset=offset)
        return {**page, "items": [summary(job) for job in page["items"]]}

    @app.post("/api/research/reproduce", status_code=202, response_model=Job)
    def reproduce():
        require_archive()
        return store.enqueue("research", {"mode": "reproduction", "resource_id": identifier("r")})

    @app.post("/api/research/arena", status_code=202, response_model=list[Job])
    def submit_arena(body: ArenaBatch):
        require_archive()
        batch_id = identifier("r")
        return store.enqueue_many("research", [
            {"mode": "arena", "config": config.model_dump(), "batch_id": batch_id, "resource_id": identifier("r")}
            for config in body.configurations
        ])

    @app.get("/api/research/runs/{run_id}")
    def run(run_id: str):
        return summary(find_run(run_id))

    @app.get("/api/research/runs/{run_id}/result", response_class=FileResponse, response_model=None,
             responses={200: {"content": {"application/json": {}}, "description": "Published spatial map and diagnostics"}})
    def result(run_id: str):
        job = find_run(run_id)
        if not job["result"]:
            raise HTTPException(409, "实验产物尚未发布")
        resource_id = job["result"]["resource_id"]
        store.resource("research", resource_id)
        return FileResponse(store.path("research", resource_id) / "result.json", media_type="application/json")

    @app.delete("/api/research/runs/{run_id}", response_model=Job)
    def cancel(run_id: str):
        find_run(run_id)
        return store.cancel(run_id)

    root = web_root or Path("web/dist")
    if (root / "index.html").is_file():
        app.mount("/", StaticFiles(directory=root, html=True), name="presentation")
    return app
