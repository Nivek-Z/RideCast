"""Immutable dataset storage and explicit, coverage-aware CSV adapters."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape

from .simulation import (
    MAX_ORDERS,
    MAX_PANEL_ROWS,
    ORDER_COLUMNS,
    PANEL_COLUMNS,
    calendar_features,
    event_features,
    utc_timestamp,
    validate_world,
)

DEFAULT_CONTEXT = {
    "temperature": 20.0,
    "precipitation": 0.0,
    "wind_speed": 2.0,
    "traffic_index": 1.0,
    "available_drivers": 0,
}


def _emit(progress, stage, fraction, message):
    if progress:
        progress(stage, float(fraction), message)


def world_hash(world):
    """A reproducible digest of the complete city snapshot, independent of file spacing."""
    return hashlib.sha256(
        json.dumps(world, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json_write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def write_dataset(output_dir: Path, world: dict, panel, orders, metadata: dict, dataset_id: str) -> dict:
    """Publish a validated immutable bundle; manifest is the final commit marker."""
    output_dir = Path(output_dir)
    if (output_dir / "manifest.json").exists():
        raise ValueError("数据集已存在，不允许覆盖")
    world = validate_world(metadata.get("effective_world", world))
    panel, orders = panel.copy(), orders.copy()
    if set(PANEL_COLUMNS) - set(panel):
        raise ValueError("panel 缺少规范字段")
    if set(ORDER_COLUMNS) - set(orders):
        raise ValueError("orders 缺少规范字段")
    panel["timestamp"] = pd.to_datetime(panel["timestamp"], utc=True)
    orders["requested_at"] = pd.to_datetime(orders["requested_at"], utc=True)
    if panel[PANEL_COLUMNS].isna().any().any():
        raise ValueError("完整 panel 不能含缺失值")
    panel = panel.sort_values(["timestamp", "zone_id"]).reset_index(drop=True)
    orders = orders.sort_values(["requested_at", "order_id"]).reset_index(drop=True)
    start, end = utc_timestamp(metadata["observation_start"]), utc_timestamp(metadata["observation_end"])
    minutes = world["bucket_minutes"]
    if start >= end or start != start.floor(f"{minutes}min") or end != end.floor(f"{minutes}min"):
        raise ValueError("观测区间必须为对齐时间桶的非空左闭右开区间")
    ids = [feature["properties"]["zone_id"] for feature in world["zones"]["features"]]
    expected = pd.MultiIndex.from_product(
        [pd.date_range(start, end, freq=f"{minutes}min", inclusive="left"), ids],
        names=["timestamp", "zone_id"],
    )
    if len(expected) > MAX_PANEL_ROWS or len(orders) > MAX_ORDERS:
        raise ValueError("数据规模超过单机上限")
    actual = pd.MultiIndex.from_frame(panel[["timestamp", "zone_id"]])
    if not actual.is_unique or len(actual) != len(expected) or len(expected.difference(actual)):
        raise ValueError("panel 必须是时间×全部区域的完整唯一面板")
    numeric = panel[[column for column in PANEL_COLUMNS if column not in ("timestamp", "zone_id")]].to_numpy(
        dtype=float
    )
    if not np.isfinite(numeric).all():
        raise ValueError("panel 数值必须有限")
    for column in ("demand", "passengers", "completed", "cancelled", "available_drivers"):
        values = panel[column].to_numpy(dtype=float)
        if (values < 0).any() or not np.equal(values, np.floor(values)).all():
            raise ValueError(f"{column} 必须为非负整数")
        panel[column] = panel[column].astype("int64")
    if not np.array_equal(panel["completed"] + panel["cancelled"], panel["demand"]):
        raise ValueError("demand 必须等于 completed+cancelled")
    if panel["demand"].sum() != len(orders) or panel["passengers"].sum() != orders["passenger_count"].sum():
        raise ValueError("订单与面板总数不一致")
    if orders["order_id"].duplicated().any() or orders["order_id"].isna().any():
        raise ValueError("order_id 必须唯一非空")
    if not orders["status"].isin(["completed", "cancelled"]).all():
        raise ValueError("未知订单状态")
    if not orders["pickup_zone_id"].isin(ids).all():
        raise ValueError("订单含未知pickup区域")
    if not orders.loc[orders["dropoff_zone_id"].notna(), "dropoff_zone_id"].isin(ids).all():
        raise ValueError("订单含未知dropoff区域")
    passenger_values = pd.to_numeric(orders["passenger_count"], errors="raise").to_numpy(dtype=float)
    if not (
        np.isfinite(passenger_values)
        & (passenger_values >= 1)
        & (passenger_values <= 8)
        & (passenger_values == np.floor(passenger_values))
    ).all():
        raise ValueError("订单passenger_count必须为1到8的整数")
    if len(orders) and (orders["requested_at"].min() < start or orders["requested_at"].max() >= end):
        raise ValueError("订单时间超出观测区间")
    if len(orders):
        aggregate = (
            orders.assign(
                timestamp=orders["requested_at"].dt.floor(f"{minutes}min"),
                zone_id=orders["pickup_zone_id"],
                demand=1,
                passengers=orders["passenger_count"],
                completed=(orders["status"] == "completed").astype(int),
                cancelled=(orders["status"] == "cancelled").astype(int),
            )
            .groupby(["timestamp", "zone_id"])[["demand", "passengers", "completed", "cancelled"]]
            .sum()
        )
        actual_counts = aggregate.reindex(expected, fill_value=0).to_numpy(dtype="int64")
        if not np.array_equal(
            panel[["demand", "passengers", "completed", "cancelled"]].to_numpy(), actual_counts
        ):
            raise ValueError("订单与面板逐区域逐桶聚合不一致")
    quality = dict(metadata.get("quality", {}))
    quality.setdefault("warnings", [])
    quality["missing_panel_values"] = 0
    quality["zero_demand_buckets"] = int((panel["demand"] == 0).sum())
    quality["completed_orders"] = int(panel["completed"].sum())
    quality["cancelled_orders"] = int(panel["cancelled"].sum())
    quality["cancellation_rate"] = float(panel["cancelled"].sum() / max(1, len(orders)))
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("panel", panel[PANEL_COLUMNS]), ("orders", orders[ORDER_COLUMNS])):
        temp = output_dir / f"{name}.parquet.tmp"
        frame.to_parquet(temp, index=False)
        temp.replace(output_dir / f"{name}.parquet")
    _json_write(output_dir / "world.json", world)
    manifest = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "world_id": world["world_id"],
        "world_hash": world_hash(world),
        "source": metadata.get("source", "imported"),
        "target_semantics": metadata.get("target_semantics", "requests"),
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "bucket_minutes": minutes,
        "timezone": world["timezone"],
        "row_count": len(panel),
        "order_count": len(orders),
        "zone_count": len(ids),
        "zone_ids": ids,
        "seed": metadata.get("seed"),
        "scenario": metadata.get("scenario", {}),
        "quality": quality,
        "provenance": metadata.get("provenance", {}),
        "files": {name: f"{name}.parquet" for name in ("panel", "orders")},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["files"]["world"] = "world.json"
    manifest["content_sha256"] = {
        name: hashlib.sha256((output_dir / filename).read_bytes()).hexdigest()
        for name, filename in manifest["files"].items()
    }
    _json_write(output_dir / "manifest.json", manifest)
    return manifest


def _safe_number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _is_empty(value):
    return value is None or pd.isna(value) or (isinstance(value, str) and not value.strip())


def _zone_for_point(lon, lat, geometries):
    # Shared-boundary points select the lexicographically smallest zone_id.
    point = Point(lon, lat)
    return next((zone_id for zone_id, geometry in geometries.items() if geometry.covers(point)), None)


def import_orders(
    csv_path: Path,
    world: dict,
    output_dir: Path,
    dataset_id: str,
    options: dict,
    context_path: Path | None = None,
    progress=None,
) -> dict:
    """Import one CSV with explicit observation coverage and target semantics.

    column_map maps canonical names to source CSV columns. Exact duplicate IDs
    are deduplicated; conflicting duplicate IDs reject or drop all conflicts.
    """
    world = validate_world(world)
    if options.get("coverage_attested") is not True:
        raise ValueError("必须明确 coverage_attested=true，确认观测区间内所有区域均完整覆盖，才可补零")
    semantics = options.get("target_semantics")
    if semantics not in ("requests", "completed_trips"):
        raise ValueError("target_semantics 必须明确为 requests 或 completed_trips")
    policy = options.get("invalid_policy", "reject")
    if policy not in ("reject", "drop"):
        raise ValueError("invalid_policy 必须为 reject/drop")
    timezone_name = options.get("timezone", world["timezone"])
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("timezone 必须为有效 IANA 时区") from exc
    try:
        start = utc_timestamp(options["observation_start"], timezone_name)
        end = utc_timestamp(options["observation_end"], timezone_name)
    except KeyError as exc:
        raise ValueError("必须指定 observation_start 和 observation_end") from exc
    minutes = world["bucket_minutes"]
    if start >= end or start != start.floor(f"{minutes}min") or end != end.floor(f"{minutes}min"):
        raise ValueError("观测区间必须对齐桶边界且 start < end")
    zone_ids = [feature["properties"]["zone_id"] for feature in world["zones"]["features"]]
    periods = int((end - start).total_seconds() // (minutes * 60))
    if periods * len(zone_ids) > MAX_PANEL_ROWS:
        raise ValueError(f"面板上限 {MAX_PANEL_ROWS} 行")
    geometries = {
        feature["properties"]["zone_id"]: shape(feature["geometry"]) for feature in world["zones"]["features"]
    }
    csv_path = Path(csv_path)
    if csv_path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("CSV 上限256MiB")
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False, nrows=MAX_ORDERS + 1)
    if len(raw) > MAX_ORDERS:
        raise ValueError(f"导入订单上限 {MAX_ORDERS}")
    mapping = options.get("column_map", {})
    if not isinstance(mapping, dict) or len(set(mapping.values())) != len(mapping):
        raise ValueError("column_map 必须是无重复源字段的 canonical->source 字典")
    if set(mapping) - set(ORDER_COLUMNS):
        raise ValueError("column_map 存在未知规范字段")
    if set(mapping.values()) - set(raw):
        raise ValueError("column_map 引用不存在的CSV列")
    raw = raw.rename(columns={source: canonical for canonical, source in mapping.items()})
    if raw.columns.duplicated().any():
        raise ValueError("字段映射导致重复规范列")
    required = {"order_id", "requested_at", "passenger_count"}
    if required - set(raw):
        raise ValueError(f"订单缺少字段: {sorted(required - set(raw))}")
    if "pickup_zone_id" not in raw and not {"pickup_lon", "pickup_lat"}.issubset(raw):
        raise ValueError("至少提供 pickup_zone_id 或 pickup_lon/pickup_lat")
    if semantics == "requests" and "status" not in raw:
        raise ValueError("requests 口径必须提供 status(completed/cancelled)")
    input_rows = len(raw)
    # Deduplicate only columns that influence the canonical record.
    canonical_present = [column for column in ORDER_COLUMNS if column in raw]
    duplicate_rows = int(raw.duplicated(subset=canonical_present).sum())
    raw = raw.drop_duplicates(subset=canonical_present).copy()
    conflicts = raw["order_id"].duplicated(keep=False)
    conflict_rows = int(conflicts.sum())
    if conflict_rows and policy == "reject":
        raise ValueError(f"存在 {conflict_rows} 条内容冲突的重复 order_id")
    raw = raw.loc[~conflicts]
    accepted, errors = [], []
    _emit(progress, "import", 0.04, "验证CSV订单、时区和区域")
    for index, (_, record) in enumerate(raw.iterrows()):
        row = record.to_dict()
        try:
            order_id = str(row["order_id"]).strip()
            if not order_id or len(order_id) > 128:
                raise ValueError("order_id 为空或过长")
            timestamp = utc_timestamp(row["requested_at"], timezone_name)
            if not start <= timestamp < end:
                raise ValueError("订单时间超出观测区间")
            passengers = _safe_number(row["passenger_count"])
            if passengers is None or not passengers.is_integer() or not 1 <= passengers <= 8:
                raise ValueError("passenger_count 须为1到8的整数")
            status = row.get("status", "completed")
            if status not in ("completed", "cancelled"):
                raise ValueError("未知status")
            if semantics == "completed_trips" and status != "completed":
                raise ValueError("completed_trips 不能含cancelled订单")
            coordinates, resolved = {}, {}
            for location in ("pickup", "dropoff"):
                lon_raw, lat_raw = row.get(f"{location}_lon"), row.get(f"{location}_lat")
                zone = row.get(f"{location}_zone_id")
                zone = None if _is_empty(zone) else str(zone)
                if zone is not None and zone not in geometries:
                    raise ValueError(f"未知{location}_zone_id")
                lon_empty, lat_empty = _is_empty(lon_raw), _is_empty(lat_raw)
                if lon_empty != lat_empty:
                    raise ValueError(f"{location}坐标必须成对出现")
                if not lon_empty:
                    lon, lat = _safe_number(lon_raw), _safe_number(lat_raw)
                    if lon is None or lat is None or not (-180 <= lon <= 180 and -85 <= lat <= 85):
                        raise ValueError(f"{location}坐标越界或非有限数")
                    point_zone = _zone_for_point(lon, lat, geometries)
                    if point_zone is None:
                        raise ValueError(f"{location}坐标不在地图覆盖范围")
                    if zone and not geometries[zone].covers(Point(lon, lat)):
                        raise ValueError(f"{location}坐标与zone_id不匹配")
                    zone = zone or point_zone
                else:
                    lon = lat = None
                if location == "pickup" and zone is None:
                    raise ValueError("无法确定pickup区域")
                coordinates[location] = (lon, lat)
                resolved[location] = zone
            travel_raw = row.get("travel_minutes")
            travel = None if _is_empty(travel_raw) else _safe_number(travel_raw)
            if not _is_empty(travel_raw) and (travel is None or not 0 < travel <= 1440):
                raise ValueError("travel_minutes须为(0,1440]的有限数")
            driver = row.get("driver_id")
            driver = None if _is_empty(driver) or status == "cancelled" else str(driver)
            accepted.append(
                [
                    order_id,
                    timestamp,
                    resolved["pickup"],
                    resolved["dropoff"],
                    int(passengers),
                    status,
                    *coordinates["pickup"],
                    *coordinates["dropoff"],
                    driver,
                    travel if status == "completed" else None,
                ]
            )
        except Exception as exc:
            if policy == "reject":
                raise ValueError(f"CSV数据行 {index + 2}: {exc}") from exc
            if len(errors) < 30:
                errors.append({"row": index + 2, "reason": str(exc)})
        if index % max(1, len(raw) // 30) == 0:
            _emit(
                progress,
                "import",
                0.05 + 0.65 * (index + 1) / max(1, len(raw)),
                f"已校验 {index + 1}/{len(raw)} 行",
            )
    orders = pd.DataFrame(accepted, columns=ORDER_COLUMNS)
    orders["requested_at"] = pd.to_datetime(orders["requested_at"], utc=True)
    if input_rows and not len(orders):
        raise ValueError("所有订单均无效，未生成数据集")
    full_index = pd.MultiIndex.from_product(
        [pd.date_range(start, end, freq=f"{minutes}min", inclusive="left"), zone_ids],
        names=["timestamp", "zone_id"],
    )
    panel = full_index.to_frame(index=False)
    if len(orders):
        aggregate = (
            orders.assign(
                timestamp=orders["requested_at"].dt.floor(f"{minutes}min"),
                zone_id=orders["pickup_zone_id"],
                demand=1,
                passengers=orders["passenger_count"],
                completed=(orders["status"] == "completed").astype(int),
                cancelled=(orders["status"] == "cancelled").astype(int),
            )
            .groupby(["timestamp", "zone_id"])[["demand", "passengers", "completed", "cancelled"]]
            .sum()
        )
        panel = panel.merge(aggregate, on=["timestamp", "zone_id"], how="left")
    else:
        for column in ("demand", "passengers", "completed", "cancelled"):
            panel[column] = 0
    for column in ("demand", "passengers", "completed", "cancelled"):
        panel[column] = panel[column].fillna(0).astype("int64")
    for column, default in DEFAULT_CONTEXT.items():
        panel[column] = default
    panel["holiday"] = [calendar_features(timestamp, world) for timestamp in panel["timestamp"]]
    panel["event_intensity"] = [
        event_features(timestamp, zone_id, world)
        for timestamp, zone_id in zip(panel["timestamp"], panel["zone_id"])
    ]
    context_rows, context_defaults = 0, len(panel)
    if context_path is not None:
        context = pd.read_csv(context_path, dtype={"zone_id": str})
        if {"timestamp", "zone_id"} - set(context):
            raise ValueError("context CSV 必须包含 timestamp、zone_id")
        allowed_context = set(DEFAULT_CONTEXT) | {"holiday", "event_intensity"}
        if set(context) - allowed_context - {"timestamp", "zone_id"}:
            raise ValueError("context包含未知字段")
        if len(context) > MAX_PANEL_ROWS:
            raise ValueError("context规模超过上限")
        context["timestamp"] = [utc_timestamp(value, timezone_name) for value in context["timestamp"]]
        if context[["timestamp", "zone_id"]].duplicated().any():
            raise ValueError("context中有重复时间区域")
        if not context["zone_id"].isin(zone_ids).all():
            raise ValueError("context引用未知区域")
        if len(context) and (
            (context["timestamp"] < start).any()
            or (context["timestamp"] >= end).any()
            or not (context["timestamp"] == context["timestamp"].dt.floor(f"{minutes}min")).all()
        ):
            raise ValueError("context时间须对齐且在观测区间内")
        for column in context.columns.difference(["timestamp", "zone_id"]):
            context[column] = pd.to_numeric(context[column], errors="raise")
            values = context[column].to_numpy(dtype=float)
            if not np.isfinite(values).all():
                raise ValueError("context数值必须有限且无缺失")
            if column == "temperature":
                valid = (values >= -80) & (values <= 65)
            elif column == "holiday":
                valid = np.isin(values, [0, 1])
            else:
                valid = (values >= 0) & (values <= (100000 if column == "available_drivers" else 1000))
                if column == "available_drivers":
                    valid &= values == np.floor(values)
            if not valid.all():
                raise ValueError(f"context.{column}超出有效范围")
        context_rows = len(context)
        context_defaults = len(panel) - context_rows
        indexed = panel.set_index(["timestamp", "zone_id"])
        indexed.update(context.set_index(["timestamp", "zone_id"]))
        panel = indexed.reset_index()
    warnings = ["稀疏订单按明确声明的完整观测区间补零；实际采集缺口不可视为零需求。"]
    missing_context_columns = sorted(
        set(DEFAULT_CONTEXT) - (set(context.columns) if context_path is not None else set())
    )
    if context_rows < len(panel) or missing_context_columns:
        warnings.append(
            "部分或全部外因使用占位默认值；available_drivers=0 表示未提供供给，不能解释为真实无车。"
        )
    if semantics == "completed_trips":
        warnings.append("目标为已完成行程量，不包含取消、拒单或未满足的潜在请求。")
    if errors or conflict_rows:
        warnings.append("invalid_policy=drop 已舍弃无效/冲突记录，可能改变需求分布。")
    metadata = {
        "source": "imported",
        "target_semantics": semantics,
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "seed": None,
        "scenario": {},
        "quality": {
            "input_rows": input_rows,
            "accepted_rows": len(orders),
            "duplicate_rows": duplicate_rows,
            "rejected_rows": input_rows - duplicate_rows - len(orders),
            "conflicting_id_rows": conflict_rows,
            "coverage_attested": True,
            "invalid_policy": policy,
            "invalid_examples": errors,
            "context_rows": context_rows,
            "context_default_rows": context_defaults,
            "context_missing_columns": missing_context_columns,
            "context_provided_columns": list(context.columns) if context_path is not None else [],
            "missing_pickup_coordinates": int(orders["pickup_lon"].isna().sum()),
            "warnings": warnings,
        },
        "provenance": {
            "adapter": "ridecast.csv.v1",
            "source_filename": csv_path.name,
            "source_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            "column_map": mapping,
            "declared_timezone": timezone_name,
            "boundary_policy": "shared edges choose lexicographically smallest zone_id when no explicit ID",
            "coverage_attestation": "user-declared complete coverage of every zone throughout observation interval",
            "context_role": "historical observations; future measured weather is not available at forecast origin",
        },
    }
    _emit(progress, "import", 0.85, "构建完整需求面板与质量报告")
    manifest = write_dataset(output_dir, world, panel[PANEL_COLUMNS], orders, metadata, dataset_id)
    _emit(progress, "import", 0.95, "数据集与质量报告已保存")
    return manifest
