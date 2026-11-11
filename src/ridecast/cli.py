"""Local management commands with the same registry and worker as HTTP."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from ridecast.settings import Settings
from ridecast.store import Store, dump, identifier


def parser():
    cli = argparse.ArgumentParser(description="RideCast backend")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize local storage")
    serve = commands.add_parser("serve", help="Serve the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("worker", help="Run independent persistent job worker")
    worker.add_argument("--once", action="store_true")
    demo = commands.add_parser("demo", help="Build evolving city, simulate, train and export reviewable sample payloads")
    demo.add_argument("--days", type=int, default=45)
    demo.add_argument("--epochs", type=int, default=12)
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--rows", type=int, default=3)
    demo.add_argument("--cols", type=int, default=3)
    demo.add_argument("--tune", action="store_true")
    demo.add_argument("--output", type=Path, default=Path("reports/demo.json"))
    commands.add_parser("status", help="Report registry and worker status")
    export = commands.add_parser("export-openapi", help="Write OpenAPI JSON")
    export.add_argument("--output", type=Path, default=Path("docs/openapi.json"))
    return cli


def demo(args, store):
    from ridecast.api import create_app
    from ridecast.dashboard import heatmap, overview
    from ridecast.ml import forecast
    from ridecast.schemas import SimulationCreate, TrainingCreate
    from ridecast.simulation import create_world
    from ridecast.worker import Worker

    world = create_world(rows=args.rows, cols=args.cols, seed=args.seed)
    try:
        store.resource("world", world["world_id"])
    except KeyError:
        path = store.path("world", world["world_id"])
        path.mkdir(exist_ok=True)
        (path / "world.json").write_text(dump(world), encoding="utf-8")
        store.add_resource("world", world["world_id"], world["name"], world)
    request = SimulationCreate(world_id=world["world_id"], days=args.days, seed=args.seed)
    simulation = store.enqueue("simulate", {**request.model_dump(), "scenario": {}, "resource_id": identifier("d")})
    worker = Worker(store)

    def finish_job(job):
        # Process queued work in FIFO order; another queued job is never mistaken for this one.
        while store.job(job["id"])["state"] not in {"succeeded", "failed", "cancelled"}:
            worker.run(once=True)
        finished = store.job(job["id"])
        if finished["state"] != "succeeded":
            raise RuntimeError(dump(finished["error"] or finished["state"]))
        return finished["result"]["resource_id"]

    dataset_id = finish_job(simulation)
    training_request = TrainingCreate(dataset_id=dataset_id,
                                     config={"epochs": args.epochs, "seed": args.seed, "tune": args.tune})
    training = store.enqueue("train", {**training_request.model_dump(), "resource_id": identifier("m")})
    model_id = finish_job(training)
    dataset_dir = store.path("dataset", dataset_id)
    report = {"world_id": world["world_id"], "dataset_id": dataset_id, "model_id": model_id,
              "simulation_job_id": simulation["id"], "training_job_id": training["id"],
              "overview": overview(dataset_dir), "heatmap": heatmap(dataset_dir),
              "model": store.resource("model", model_id)["metadata"],
              "forecast": forecast(store.path("model", model_id), dataset_dir)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dump(report) + "\n", encoding="utf-8")
    # Machine-readable schema for frontend generation, without binding a network port.
    openapi = Path("docs/openapi.json")
    openapi.parent.mkdir(parents=True, exist_ok=True)
    openapi.write_text(json.dumps(create_app(store.settings).openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report": str(args.output.resolve()), "world_id": world["world_id"], "dataset_id": dataset_id,
            "model_id": model_id, "metrics": report["model"]["metrics"]["model"]}


def main():
    args = parser().parse_args()
    if args.command == "serve":
        import uvicorn
        uvicorn.run("ridecast.api:app_factory", factory=True, host=args.host, port=args.port)
        return
    store = Store(Settings())
    if args.command == "worker":
        # A dedicated invocation makes worker ownership and process isolation explicit.
        command = [sys.executable, "-m", "ridecast.worker"] + (["--once"] if args.once else [])
        raise SystemExit(subprocess.call(command, env=os.environ.copy()))
    if args.command == "demo":
        result = demo(args, store)
    elif args.command == "status":
        result = {**store.status(), "resources": {kind: store.resources(kind, 1)["total"]
                                                 for kind in ("world", "dataset", "model")}}
    elif args.command == "export-openapi":
        from ridecast.api import create_app
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(create_app(store.settings).openapi(), ensure_ascii=False, indent=2),
                               encoding="utf-8")
        result = {"output": str(args.output.resolve())}
    else:
        result = {"status": "initialized", "home": str(store.settings.home)}
    print(dump(result))


if __name__ == "__main__":
    main()
