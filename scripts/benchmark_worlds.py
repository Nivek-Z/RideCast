#!/usr/bin/env python3
"""Compare seeded worlds and, optionally, independently trained holdout models.

Example:
    python scripts/benchmark_worlds.py --seeds 7 42 99 --days 28 --output var/world-statistics
    python scripts/benchmark_worlds.py --seeds 7 42 99 --days 28 --epochs 5 --train --output var/world-models

Each world gets its own training/validation/test split and a freshly trained
model with the same configuration. This is repeated modeling robustness under
the simulator, NOT zero-shot transfer from one city to an unseen city.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ridecast.data import world_hash, write_dataset
from ridecast.simulation import create_world, simulate, utc_timestamp

EXPERIMENT = "independent_world_holdout_retraining"
METRICS = ("mae", "rmse", "wape", "smape")


def _integer(low: int, high: int):
    def convert(value: str) -> int:
        try:
            result = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("must be an integer") from error
        if not low <= result <= high:
            raise argparse.ArgumentTypeError(f"must be in [{low}, {high}]")
        return result
    return convert


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", nargs="+", type=_integer(0, 2**31 - 1), default=[7, 42, 99],
                        help="distinct world/evolution seeds, separated by spaces (default: 7 42 99)")
    parser.add_argument("--days", type=_integer(1, 365), default=28, help="observed days per world")
    parser.add_argument("--epochs", type=_integer(1, 100), default=5, help="maximum epochs per independently trained model")
    parser.add_argument("--output", type=Path, default=Path("var/benchmarks/worlds"), help="new or empty output directory")
    parser.add_argument("--train", action="store_true", help="train real TensorFlow models; omitted means world statistics only")
    parser.add_argument("--rows", type=_integer(1, 10), default=3, help="zone-grid rows")
    parser.add_argument("--cols", type=_integer(1, 10), default=3, help="zone-grid columns")
    parser.add_argument("--start", default="2026-01-01T00:00:00+08:00", help="absolute observation start with timezone")
    parser.add_argument("--training-seed", type=_integer(0, 2**31 - 1), default=42,
                        help="same model initialization seed in every world")
    return parser


def distribution(values: list[float | None]) -> dict | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return None
    return {"count": len(clean), "mean": statistics.fmean(clean), "std": statistics.pstdev(clean),
            "min": min(clean), "max": max(clean)}


def summarize(worlds: list[dict]) -> dict:
    """Equal-weight world-level summary; never pool overlapping horizon labels."""
    successful = [row for row in worlds if row.get("status") == "succeeded"]
    trained = [row for row in successful if row.get("metrics") is not None]
    return {
        "world_count": len(worlds), "succeeded": len(successful), "failed": len(worlds) - len(successful),
        "distinct_world_hashes": len({row["world_hash"] for row in successful}),
        "distinct_map_geometries": len({row["map_geometry_sha256"] for row in successful
                                        if row.get("map_geometry_sha256")}),
        "demand_total": distribution([row["statistics"]["demand_total"] for row in successful]),
        "trained_worlds": len(trained),
        "model": {metric: distribution([row["metrics"]["model"].get(metric) for row in trained])
                  for metric in METRICS},
        "selected_baseline": {metric: distribution([row["metrics"]["baseline"].get(metric) for row in trained])
                              for metric in METRICS},
        "worlds_with_lower_model_mae": sum(row["metrics"]["model"]["mae"] < row["metrics"]["baseline"]["mae"]
                                           for row in trained),
        "model_mae_improvement_percent": distribution([
            row["metrics"].get("model_mae_improvement_percent") for row in trained]),
        "aggregation": "equal weight per world; population standard deviation; no cross-world model transfer",
    }


def _statistics(world: dict, panel: pd.DataFrame, orders: pd.DataFrame) -> dict:
    city = panel.groupby("timestamp")[["demand", "available_drivers"]].sum()
    kinds = [feature["properties"]["kind"] for feature in world["zones"]["features"]]
    return {
        "zone_count": len(world["zones"]["features"]), "road_count": len(world["roads"]["features"]),
        "land_use_counts": {kind: kinds.count(kind) for kind in sorted(set(kinds))},
        "bucket_count": int(panel["timestamp"].nunique()), "panel_rows": len(panel), "orders": len(orders),
        "demand_total": int(panel["demand"].sum()), "passenger_total": int(panel["passengers"].sum()),
        "completed_total": int(panel["completed"].sum()), "cancelled_total": int(panel["cancelled"].sum()),
        "cancellation_rate": float(panel["cancelled"].sum() / len(orders)) if len(orders) else None,
        "mean_demand_per_zone_bucket": float(panel["demand"].mean()),
        "std_demand_per_zone_bucket": float(panel["demand"].std(ddof=0)),
        "peak_city_bucket_demand": int(city["demand"].max()),
        "mean_city_available_drivers_per_bucket": float(city["available_drivers"].mean()),
        "temperature_min": float(panel["temperature"].min()), "temperature_max": float(panel["temperature"].max()),
        "rainy_zone_bucket_fraction": float((panel["precipitation"] > 0).mean()),
        "event_zone_bucket_fraction": float((panel["event_intensity"] > 0).mean()),
        "supply_semantics": "mean of contemporaneous city snapshots, not unique drivers summed over time",
    }


def _source_fingerprints() -> dict[str, str]:
    source = Path(sys.modules["ridecast.simulation"].__file__).resolve().parent
    files = [source / name for name in ("simulation.py", "data.py", "features.py", "ml.py")]
    files.append(Path(__file__).resolve())
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.is_file()}


def _world_description(world: dict) -> dict:
    geometry = [feature["geometry"] for feature in world["zones"]["features"]]
    return {"world_id": world["world_id"], "world_hash": world_hash(world),
            "world_generator_version": world.get("metadata", {}).get("generator_version"),
            "map_geometry_sha256": hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()}


def _consistency(world: dict, panel: pd.DataFrame, orders: pd.DataFrame,
                 args: argparse.Namespace, seed: int) -> dict:
    repeated = create_world(name="基准虚拟城市", rows=args.rows, cols=args.cols, seed=seed)
    repeated_world = world_hash(world) == world_hash(repeated)
    # A one-day overlapping extraction must reproduce the original world's last
    # day, including order IDs and dynamics, without restarting its random stream.
    overlap_start = utc_timestamp(args.start) + pd.Timedelta(days=args.days - 1)
    overlap_panel, overlap_orders, _ = simulate(world, overlap_start.isoformat(), 1, seed)
    original_panel = panel.loc[panel["timestamp"] >= overlap_start].reset_index(drop=True)
    original_orders = orders.loc[orders["requested_at"] >= overlap_start].reset_index(drop=True)

    def same(left: pd.DataFrame, right: pd.DataFrame, columns: list[str]) -> bool:
        left = left.sort_values(columns).reset_index(drop=True)
        right = right.sort_values(columns).reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(left, right, check_exact=True)
            return True
        except AssertionError:
            return False

    return {"same_seed_world_repeatable": repeated_world,
            "overlap_panel_consistent": same(original_panel, overlap_panel, ["timestamp", "zone_id"]),
            "overlap_orders_consistent": same(original_orders, overlap_orders, ["requested_at", "order_id"]),
            "overlap_start": overlap_start.isoformat(), "overlap_days": 1}


def render_markdown(report: dict) -> str:
    config = report["settings"]
    created_local = pd.Timestamp(report["created_at"]).tz_convert("Asia/Shanghai").isoformat()
    lines = [
        "# 随机虚拟世界重复建模基准", "",
        f"生成时间：{created_local}（Asia/Shanghai）。模式：{'真实 TensorFlow 训练' if config['train'] else '仅世界统计'}。", "",
        "实验语义：每个种子创建独立世界，在该世界自己的历史时间序列上划分训练/验证/测试，"
        "使用相同超参数重新训练。结果衡量仿真生成器下重复建模的稳定性。"
        "**这不是一个模型从城市 A 迁移到未知城市 B 的零样本泛化实验，也不能证明真实城市精度。**", "",
        f"世界种子：`{config['seeds']}`；每世界 {config['days']} 天；区域 {config['rows']}×{config['cols']}；"
        f"固定训练种子 {config['training_seed']}；最大训练轮数 {config['epochs']}。", "",
        "每世界使用时间顺序 70/15/15 划分，验证集选择基线/早停，测试仅最终报告；"
        "未来天气使用预测起点可获得的持续假设。训练指标按 forecast-origin×zone×horizon 评价。", "",
        "| 世界种子 | 区域数 | 发起请求总数 | 取消比例 | 模型 MAE | 基线 MAE | 模型 WAPE | 状态 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    def fmt(value, percentage=False):
        if value is None:
            return "—"
        return f"{value * 100:.2f}%" if percentage else f"{value:.4f}"

    for row in report["worlds"]:
        stats = row.get("statistics", {})
        metrics = row.get("metrics") or {}
        wape = metrics.get("model", {}).get("wape")
        wape_display = "—" if wape is None else f"{wape:.2f}%"
        lines.append(f"| {row['seed']} | {stats.get('zone_count', '—')} | {stats.get('demand_total', '—')} | "
                     f"{fmt(stats.get('cancellation_rate'), True)} | {fmt(metrics.get('model', {}).get('mae'))} | "
                     f"{fmt(metrics.get('baseline', {}).get('mae'))} | "
                     f"{wape_display} | {row['status']} |")
    summary = report["summary"]
    lines += ["", f"成功 {summary['succeeded']}/{summary['world_count']} 个世界；"
              f"不同世界摘要 {summary['distinct_world_hashes']} 个；"
              f"不同地图几何 {summary.get('distinct_map_geometries', 0)} 个。"]
    if summary["trained_worlds"]:
        mae = summary["model"]["mae"]
        lines += ["", f"各世界模型 MAE 的等权均值 {mae['mean']:.4f}，总体标准差 {mae['std']:.4f}；"
                  f"{summary['worlds_with_lower_model_mae']}/{summary['trained_worlds']} 个世界模型 MAE 低于验证集选定基线。"
                  "较少种子只能提供有限的稳定性证据。"]
    lines += ["", "世界确定性检查：同种子重新构建世界；提取最后一天的重叠窗口，逐值比较面板及订单。"
              "检查失败会将该世界标为 failed，脚本返回非零退出码。",
              "", "供给统计是每个时点区域求和后的快照均值，不能将不同小时的司机数相加作为独立司机总量。",
              "", "完整指标、时序边界、生成器版本、世界摘要和错误详情见同目录 `results.json`；"
              "各世界的数据与模型位于 `worlds/seed_*/`。"]
    for row in report["worlds"]:
        if row.get("error"):
            lines += ["", f"种子 {row['seed']} 失败：`{row['error']}`"]
    changed = report.get("implementation", {}).get("source_files_changed_during_run", [])
    if changed:
        lines += ["", f"源码审计：运行期间磁盘源码摘要发生变化：`{', '.join(changed)}`。"
                  "起止 SHA-256 记录在 JSON 中；复现实验应核对变更内容。"]
    if report.get("implementation_review"):
        lines += ["", report["implementation_review"]["assessment"]]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict:
    if len(set(args.seeds)) != len(args.seeds):
        raise ValueError("seeds must be distinct; duplicates do not add independent worlds")
    if len(args.seeds) > 20:
        raise ValueError("At most 20 worlds per run; split larger experiments into separate runs")
    if args.train and args.days < 2:
        raise ValueError("Training requires at least 2 days for the fixed lookback/horizon and temporal split")
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory; existing artifacts are not overwritten")
    output.mkdir(parents=True, exist_ok=True)
    training_config = {"lookback": 24, "horizon": 3, "units": 16, "batch_size": 128,
                       "learning_rate": 0.001, "epochs": args.epochs, "seed": args.training_seed, "tune": False}
    report = {
        "schema_version": "1.0", "experiment": EXPERIMENT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": {"seeds": args.seeds, "days": args.days, "epochs": args.epochs, "train": args.train,
                     "rows": args.rows, "cols": args.cols, "start": args.start,
                     "training_seed": args.training_seed, "training_config": training_config},
        "generalization_claim": "within-world temporal holdout after independent retraining; not zero-shot cross-city transfer",
        "implementation": {"python": sys.version.split()[0], "source_sha256_at_start": _source_fingerprints()},
        "worlds": [],
    }
    for seed in args.seeds:
        started = time.perf_counter()
        result = {"seed": seed, "status": "running", "metrics": None}
        print(f"[seed={seed}] creating world and observations", file=sys.stderr, flush=True)
        try:
            world = create_world(name="基准虚拟城市", rows=args.rows, cols=args.cols, seed=seed)
            panel, orders, metadata = simulate(world, args.start, args.days, seed)
            result.update(_world_description(world))
            result["statistics"] = _statistics(world, panel, orders)
            result["consistency_checks"] = _consistency(world, panel, orders, args, seed)
            checks = result["consistency_checks"]
            if not all(checks[key] for key in ("same_seed_world_repeatable", "overlap_panel_consistent", "overlap_orders_consistent")):
                raise ValueError("World reproducibility/overlap consistency check failed")
            root = output / "worlds" / f"seed_{seed}"
            dataset_dir, model_dir = root / "dataset", root / "model"
            identity = hashlib.sha256(json.dumps([seed, args.days, args.start, args.rows, args.cols]).encode()).hexdigest()[:16]
            manifest = write_dataset(dataset_dir, world, panel, orders, metadata, f"d_{identity}")
            result["dataset_id"] = manifest["dataset_id"]
            result["dataset_path"] = str(dataset_dir.relative_to(output))
            result["generator_provenance"] = manifest.get("provenance", {})
            if args.train:
                from ridecast.ml import train_model

                def progress(stage, fraction, message):
                    if stage in {"training", "complete"}:
                        print(f"[seed={seed}] {stage} {fraction:.0%}: {message}", file=sys.stderr, flush=True)

                model = train_model(dataset_dir, model_dir, f"m_{identity}", dict(training_config), progress)
                result["metrics"] = model["metrics"]
                result["split"] = model["split"]
                result["model_path"] = str(model_dir.relative_to(output))
                result["dependencies"] = model["dependencies"]
            result["status"] = "succeeded"
        except Exception as error:
            result["status"] = "failed"
            result["error"] = f"{type(error).__name__}: {error}"
            print(f"[seed={seed}] failed: {result['error']}", file=sys.stderr, flush=True)
        result["duration_seconds"] = time.perf_counter() - started
        report["worlds"].append(result)
    report["summary"] = summarize(report["worlds"])
    final_fingerprints = _source_fingerprints()
    report["implementation"]["source_sha256_at_end"] = final_fingerprints
    report["implementation"]["source_files_changed_during_run"] = [
        name for name, digest in report["implementation"]["source_sha256_at_start"].items()
        if final_fingerprints.get(name) != digest]
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Saved {output / 'results.json'} and {output / 'summary.md'}", file=sys.stderr)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
