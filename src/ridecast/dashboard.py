"""Read-only, JSON-safe aggregates for a visual operations dashboard.

These functions operate on immutable dataset artifacts and have no dependency on
the API or resource store. Time filters use [start, end). Driver availability is
a contemporaneous snapshot; it is never added across successive time buckets.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import shape

COUNT_FIELDS = ("demand", "passengers", "completed", "cancelled")
WEATHER_FIELDS = ("temperature", "precipitation", "wind_speed", "traffic_index")
MAX_LIMIT = 10_000


def _safe(value: Any) -> Any:
    """Convert pandas/numpy values without allowing nonstandard JSON NaN/Inf."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return stamp.tz_convert("UTC").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load(dataset_dir: str | Path, *, orders: bool = False) -> tuple:
    directory = Path(dataset_dir)
    manifest = _json(directory / "manifest.json")
    world = _json(directory / "world.json")
    panel = pd.read_parquet(directory / "panel.parquet")
    panel["timestamp"] = pd.to_datetime(panel["timestamp"], utc=True)
    panel["zone_id"] = panel["zone_id"].astype(str)
    panel = panel.sort_values(["timestamp", "zone_id"]).reset_index(drop=True)
    if panel.empty:
        raise ValueError("Dataset contains no time buckets")
    if not orders:
        return manifest, world, panel
    trips = pd.read_parquet(directory / "orders.parquet")
    trips["requested_at"] = pd.to_datetime(trips["requested_at"], utc=True)
    trips = trips.sort_values(["requested_at", "order_id"]).reset_index(drop=True)
    return manifest, world, panel, trips


def _stamp(value: Any, name: str = "timestamp") -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return stamp.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name}; use an ISO 8601 date/time") from error


def _bounds(start: Any, end: Any) -> tuple:
    lower = _stamp(start, "start") if start is not None else None
    upper = _stamp(end, "end") if end is not None else None
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("start must be before end (end is exclusive)")
    return lower, upper


def _filter_time(frame: pd.DataFrame, field: str, start: Any, end: Any) -> pd.DataFrame:
    lower, upper = _bounds(start, end)
    if lower is not None:
        frame = frame.loc[frame[field] >= lower]
    if upper is not None:
        frame = frame.loc[frame[field] < upper]
    return frame


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    return value


def _zones(world: dict) -> dict[str, dict]:
    return {str(feature["properties"]["zone_id"]): feature for feature in world["zones"]["features"]}


def _zone_filter(frame: pd.DataFrame, world: dict, zone_id: str | None, field: str) -> pd.DataFrame:
    if zone_id is not None:
        if str(zone_id) not in _zones(world):
            raise ValueError(f"Unknown zone_id: {zone_id}")
        return frame.loc[frame[field].astype(str) == str(zone_id)]
    return frame


def _provenance(manifest: dict) -> dict:
    quality = manifest.get("quality") or {}
    synthetic = manifest.get("source") == "synthetic"
    context_columns = set(quality.get("context_provided_columns") or [])
    complete_context = quality.get("context_default_rows") == 0
    supply_observed = synthetic or (complete_context and "available_drivers" in context_columns)
    return {
        "dataset_id": manifest.get("dataset_id"),
        "world_id": manifest.get("world_id"),
        "source": manifest.get("source"),
        "target_semantics": manifest.get("target_semantics"),
        "bucket_minutes": manifest["bucket_minutes"],
        "supply_observed": supply_observed,
        "supply_source": "simulation" if synthetic else (
            "complete_historical_context" if supply_observed else "placeholder_or_partial_context"),
        "context_default_rows": quality.get("context_default_rows"),
        "outcomes_observed": manifest.get("target_semantics") == "requests",
    }


def _snapshot(panel: pd.DataFrame, manifest: dict, timestamp: Any = None) -> tuple:
    minutes = int(manifest["bucket_minutes"])
    cursor = panel["timestamp"].max() if timestamp is None else _stamp(timestamp)
    # Match the actual bucket starts instead of assuming midnight alignment.
    starts = panel["timestamp"].drop_duplicates().sort_values()
    eligible = starts.loc[starts <= cursor]
    if eligible.empty:
        raise ValueError("timestamp is outside the dataset observation window")
    bucket = eligible.iloc[-1]
    bucket_end = bucket + pd.Timedelta(minutes=minutes)
    if cursor >= bucket_end:
        raise ValueError("timestamp is outside the dataset observation window")
    return panel.loc[panel["timestamp"] == bucket].copy(), bucket, bucket_end, cursor


def _aggregate(frame: pd.DataFrame, by: str | list[str]) -> pd.DataFrame:
    aggregations = {field: "sum" for field in COUNT_FIELDS}
    aggregations["available_drivers"] = "sum"  # Sum across zones, within one timestamp only.
    aggregations.update({field: "mean" for field in WEATHER_FIELDS})
    aggregations.update({"holiday": "max", "event_intensity": "max"})
    return frame.groupby(by, sort=True, as_index=False).agg(aggregations)


def overview(dataset_dir: str | Path) -> dict:
    """Totals, observed range, latest supply/weather and chart-ready distributions."""
    manifest, world, panel = _load(dataset_dir)
    zone_map = _zones(world)
    latest, bucket, bucket_end, _ = _snapshot(panel, manifest)
    city_series = _aggregate(panel, "timestamp")
    totals = {field: int(panel[field].sum()) for field in COUNT_FIELDS}
    provenance = _provenance(manifest)
    totals.update({
        "completion_rate": totals["completed"] / totals["demand"]
        if totals["demand"] and provenance["outcomes_observed"] else None,
        "cancellation_rate": totals["cancelled"] / totals["demand"]
        if totals["demand"] and provenance["outcomes_observed"] else None,
        "latest_available_drivers": int(latest["available_drivers"].sum())
        if provenance["supply_observed"] else None,
        "mean_available_drivers_per_bucket": float(city_series["available_drivers"].mean())
        if provenance["supply_observed"] else None,
        "peak_bucket_demand": int(city_series["demand"].max()),
        "mean_bucket_demand": float(city_series["demand"].mean()),
    })
    summaries = []
    latest_index = latest.set_index("zone_id")
    for zone_id, group in panel.groupby("zone_id", sort=True):
        props = zone_map[zone_id]["properties"]
        current = latest_index.loc[zone_id]
        summaries.append({
            "zone_id": zone_id, "name": props.get("name", zone_id), "kind": props.get("kind"),
            **{field: int(group[field].sum()) for field in COUNT_FIELDS},
            "mean_demand_per_bucket": float(group["demand"].mean()),
            "peak_demand_per_bucket": int(group["demand"].max()),
            "latest_demand": int(current["demand"]),
            "latest_available_drivers": int(current["available_drivers"])
            if provenance["supply_observed"] else None,
            "latest_supply_gap": int(current["demand"] - current["available_drivers"])
            if provenance["supply_observed"] and provenance["outcomes_observed"] else None,
        })
    local = panel["timestamp"].dt.tz_convert(world.get("timezone", "UTC"))
    chart_panel = panel.assign(hour=local.dt.hour, weekday=local.dt.dayofweek)
    distributions = {}
    for field in ("hour", "weekday"):
        rows = []
        for key, group in chart_panel.groupby(field, sort=True):
            bucket_totals = group.groupby("timestamp")["demand"].sum()
            rows.append({field: int(key), "demand": int(group["demand"].sum()),
                         "mean_city_demand_per_bucket": float(bucket_totals.mean()),
                         "bucket_count": len(bucket_totals)})
        distributions[field] = rows
    weather = {
        "timestamp": bucket,
        **{field: float(latest[field].mean()) for field in WEATHER_FIELDS},
        "raining_zones": int((latest["precipitation"] > 0).sum()),
        "event_zones": int((latest["event_intensity"] > 0).sum()),
        "holiday": bool(latest["holiday"].max()),
        "aggregation": "unweighted mean across zones; counts are contemporaneous",
    }
    return _safe({
        **provenance, "timezone": world.get("timezone", "UTC"),
        "range": {"start": manifest["observation_start"], "end": manifest["observation_end"],
                  "end_exclusive": True, "bucket_count": int(panel["timestamp"].nunique()),
                  "zone_count": int(panel["zone_id"].nunique()), "panel_rows": len(panel),
                  "order_count": manifest.get("order_count")},
        "kpis": totals, "zones": summaries, "quality": manifest.get("quality", {}),
        "weather": weather, "distributions": distributions,
        "status_distribution": [{"status": key, "count": totals[key]}
                                for key in ("completed", "cancelled")],
        "latest_bucket": {"start": bucket, "end": bucket_end},
        "supply_semantics": "available_drivers is a snapshot; never a count of distinct drivers over time",
    })


def timeseries(dataset_dir: str | Path, zone_id: str | None = None,
               start: Any = None, end: Any = None, limit: int = 1000) -> dict:
    """Chronological rows, aggregating zones only within each timestamp."""
    _limit(limit)
    manifest, world, panel = _load(dataset_dir)
    panel = _zone_filter(panel, world, zone_id, "zone_id")
    panel = _filter_time(panel, "timestamp", start, end)
    rows = _aggregate(panel, "timestamp")
    total_count = len(rows)
    rows = rows.head(limit)
    provenance = _provenance(manifest)
    rows["supply_gap"] = (rows["demand"] - rows["available_drivers"]
                          if provenance["supply_observed"] and provenance["outcomes_observed"] else None)
    return _safe({
        **_provenance(manifest), "zone_id": zone_id,
        "total_count": total_count, "count": len(rows), "truncated": total_count > limit,
        "rows": rows.to_dict("records"), "end_exclusive": True,
        "aggregation": {"counts": "sum across selected zones in each bucket",
                        "weather": "unweighted zone mean", "event_intensity": "zone maximum"},
    })


def _heatmap_rows(world: dict, snapshot: pd.DataFrame, manifest: dict) -> tuple[list, dict]:
    zone_map = _zones(world)
    provenance = _provenance(manifest)
    rows, features = [], []
    for row in snapshot.to_dict("records"):
        feature = zone_map[row["zone_id"]]
        properties = dict(feature["properties"])
        properties.update({key: value for key, value in row.items() if key != "timestamp"})
        properties["supply_observed"] = provenance["supply_observed"]
        properties["supply_gap"] = (row["demand"] - row["available_drivers"]
                                    if provenance["supply_observed"] and provenance["outcomes_observed"] else None)
        properties["unserved_requests"] = row["cancelled"] if provenance["outcomes_observed"] else None
        properties["completion_rate"] = (row["completed"] / row["demand"]
                                         if row["demand"] and provenance["outcomes_observed"] else None)
        rows.append(properties)
        features.append({"type": "Feature", "geometry": feature["geometry"], "properties": properties})
    return rows, {"type": "FeatureCollection", "features": features}


def heatmap(dataset_dir: str | Path, timestamp: Any = None) -> dict:
    """One bucket, with enriched GeoJSON regions suitable for a choropleth map."""
    manifest, world, panel = _load(dataset_dir)
    snapshot, bucket, bucket_end, _ = _snapshot(panel, manifest, timestamp)
    rows, geojson = _heatmap_rows(world, snapshot, manifest)
    return _safe({
        **_provenance(manifest), "timestamp": bucket, "bucket_end": bucket_end,
        "rows": rows, "geojson": geojson,
        "supply_semantics": "per-zone availability snapshot in this bucket",
    })


def _centres(world: dict) -> dict[str, list[float]]:
    return {zone_id: [float(point.x), float(point.y)]
            for zone_id, feature in _zones(world).items()
            for point in [shape(feature["geometry"]).centroid]}


def flows(dataset_dir: str | Path, start: Any = None, end: Any = None, limit: int = 100) -> dict:
    """Observed origin/destination request counts; cancelled requests remain visible."""
    _limit(limit)
    manifest, world, _, trips = _load(dataset_dir, orders=True)
    trips = _filter_time(trips, "requested_at", start, end)
    centres = _centres(world)
    rows = []
    for (origin, destination), group in trips.groupby(["pickup_zone_id", "dropoff_zone_id"], dropna=False):
        origin = None if pd.isna(origin) else str(origin)
        destination = None if pd.isna(destination) else str(destination)
        rows.append({
            "pickup_zone_id": origin, "dropoff_zone_id": destination, "orders": len(group),
            "passengers": int(group["passenger_count"].sum(min_count=1))
            if group["passenger_count"].notna().any() else None,
            "completed": int((group["status"] == "completed").sum()),
            "cancelled": int((group["status"] == "cancelled").sum()),
            "origin": centres.get(origin), "destination": centres.get(destination),
        })
    rows.sort(key=lambda row: (-row["orders"], row["pickup_zone_id"] or "", row["dropoff_zone_id"] or ""))
    total_count = len(rows)
    selected = rows[:limit]
    features = [{"type": "Feature", "geometry": {"type": "LineString",
                 "coordinates": [row["origin"], row["destination"]]}, "properties": row}
                for row in selected if row["origin"] is not None and row["destination"] is not None]
    return _safe({
        **_provenance(manifest), "rows": selected, "total_count": total_count,
        "count": len(selected), "truncated": total_count > limit, "order_count": len(trips),
        "geojson": {"type": "FeatureCollection", "features": features},
        "geometry_semantics": "zone-centroid links; not actual road trajectories",
        "end_exclusive": True,
    })


def list_orders(dataset_dir: str | Path, zone_id: str | None = None,
                status: str | None = None, offset: int = 0, limit: int = 100) -> dict:
    """Stable chronological order pagination; zone_id means pickup region."""
    _limit(limit)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if status is not None and status not in {"completed", "cancelled"}:
        raise ValueError("status must be completed or cancelled")
    manifest, world, _, trips = _load(dataset_dir, orders=True)
    trips = _zone_filter(trips, world, zone_id, "pickup_zone_id")
    if status is not None:
        trips = trips.loc[trips["status"] == status]
    total_count = len(trips)
    selected = trips.iloc[offset:offset + limit]
    next_offset = offset + len(selected) if offset + len(selected) < total_count else None
    return _safe({
        **_provenance(manifest), "zone_id": zone_id, "status": status,
        "offset": offset, "limit": limit, "total_count": total_count,
        "count": len(selected), "next_offset": next_offset,
        "rows": selected.to_dict("records"),
    })


def _events(world: dict, manifest: dict, cursor: pd.Timestamp) -> list[dict]:
    events = list(world.get("events", []))
    scenario = manifest.get("scenario") or {}
    events.extend(scenario.get("events", []))
    active, seen = [], set()
    for event in events:
        if _stamp(event["start"]) <= cursor < _stamp(event["end"]):
            identity = json.dumps(event, sort_keys=True, ensure_ascii=False)
            if identity not in seen:
                active.append(event)
                seen.add(identity)
    return active


def replay(dataset_dir: str | Path, timestamp: Any = None, limit: int = 100) -> dict:
    """Historical bucket and approximate in-flight coordinates, never live telemetry.

    Vehicles are inferred only where driver ID, coordinates and duration exist.
    Position follows a straight line, not a sampled or map-matched road route.
    Orders from earlier buckets can remain in flight at the selected instant.
    """
    _limit(limit)
    manifest, world, panel, trips = _load(dataset_dir, orders=True)
    snapshot, bucket, bucket_end, cursor = _snapshot(panel, manifest, timestamp)
    if timestamp is None:
        cursor = bucket + pd.Timedelta(minutes=int(manifest["bucket_minutes"]) / 2)
    bucket_trips = trips.loc[(trips["requested_at"] >= bucket) & (trips["requested_at"] < bucket_end)]
    vehicles = []
    required = {"driver_id", "travel_minutes", "pickup_lon", "pickup_lat", "dropoff_lon", "dropoff_lat"}
    if required.issubset(trips.columns):
        candidates = trips.loc[(trips["requested_at"] <= cursor) & (trips["status"] == "completed")].copy()
        candidates = candidates.dropna(subset=sorted(required))
        duration = pd.to_numeric(candidates["travel_minutes"], errors="coerce")
        candidates = candidates.loc[np.isfinite(duration) & (duration > 0)].copy()
        candidates["ends_at"] = candidates["requested_at"] + pd.to_timedelta(
            candidates["travel_minutes"], unit="min")
        candidates = candidates.loc[candidates["ends_at"] > cursor]
        # Incomplete imported data can contain driver collisions. Show one marker
        # per known ID, taking the latest request, and expose the approximation.
        candidates = candidates.sort_values("requested_at").drop_duplicates("driver_id", keep="last")
        for trip in candidates.to_dict("records"):
            coordinates = [trip[key] for key in ("pickup_lon", "pickup_lat", "dropoff_lon", "dropoff_lat")]
            if not all(math.isfinite(float(coordinate)) for coordinate in coordinates):
                continue
            progress = (cursor - trip["requested_at"]).total_seconds() / (float(trip["travel_minutes"]) * 60)
            progress = min(1.0, max(0.0, progress))
            vehicles.append({
                "driver_id": trip["driver_id"], "order_id": trip["order_id"], "state": "occupied",
                "lon": float(trip["pickup_lon"]) + progress * (float(trip["dropoff_lon"]) - float(trip["pickup_lon"])),
                "lat": float(trip["pickup_lat"]) + progress * (float(trip["dropoff_lat"]) - float(trip["pickup_lat"])),
                "progress": progress, "requested_at": trip["requested_at"], "ends_at": trip["ends_at"],
            })
    rows, geojson = _heatmap_rows(world, snapshot, manifest)
    return _safe({
        **_provenance(manifest), "mode": "historical_replay", "is_live": False,
        "timestamp": cursor, "bucket_start": bucket, "bucket_end": bucket_end,
        "orders": bucket_trips.head(limit).to_dict("records"), "order_count": len(bucket_trips),
        "orders_truncated": len(bucket_trips) > limit,
        "vehicles": vehicles[:limit], "vehicle_count": len(vehicles), "vehicles_truncated": len(vehicles) > limit,
        "zones": rows, "geojson": geojson, "events": _events(world, manifest, cursor),
        "position_source": "derived_linear_interpolation",
        "position_assumption": "travel starts at requested_at; interpolation between pickup/dropoff; no road path or idle-vehicle positions",
        "status_semantics": "order status is its final dataset outcome, not its state at replay timestamp",
        "order_scope": "all requests in the selected bucket, including requests later than the replay cursor",
        "supply_semantics": "zone snapshot; inferred occupied markers do not enumerate the full fleet",
    })


def _distance_km(left: list[float], right: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [*left, *right])
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


def dispatch(forecast_result: dict, world: dict, supply: dict | None = None) -> dict:
    """Conservative, one-step greedy repositioning suggestions; no dispatch is executed.

    `supply` is a complete {zone_id: nonnegative_integer} snapshot. A missing
    snapshot produces no transfers instead of inventing vehicle availability.
    Distances are great-circle distances between zone centroids, not travel times.
    """
    zone_map = _zones(world)
    forecast_rows = forecast_result.get("rows") or []
    if not forecast_rows:
        raise ValueError("forecast_result must contain prediction rows")
    frame = pd.DataFrame(forecast_rows)
    if not {"timestamp", "zone_id", "predicted_demand"}.issubset(frame.columns):
        raise ValueError("Forecast rows require timestamp, zone_id and predicted_demand")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if frame["timestamp"].isna().any():
        raise ValueError("Forecast timestamps must be valid")
    frame["zone_id"] = frame["zone_id"].astype(str)
    earliest = frame["timestamp"].min()
    first = frame.loc[frame["timestamp"] == earliest]
    if first["zone_id"].duplicated().any() or set(first["zone_id"]) != set(zone_map):
        raise ValueError("First forecast bucket must contain each world zone exactly once")
    predictions = pd.to_numeric(first["predicted_demand"], errors="coerce")
    if not np.isfinite(predictions).all() or (predictions < 0).any():
        raise ValueError("Predicted demand must be finite and nonnegative")
    demands = dict(zip(first["zone_id"], predictions.astype(float), strict=True))
    required = {zone_id: math.ceil(value) for zone_id, value in demands.items()}
    common = {
        "model_id": forecast_result.get("model_id"), "dataset_id": forecast_result.get("dataset_id"),
        "forecast_origin": forecast_result.get("forecast_origin"), "timestamp": earliest,
        "bucket_minutes": forecast_result.get("bucket_minutes", world.get("bucket_minutes")),
        "method": "greedy_nearest_surplus", "executed": False,
        "horizon_policy": "first_forecast_bucket_only",
        "capacity_assumption": "one available driver serves one request in the next bucket; required_drivers=ceil(predicted_demand)",
        "distance_method": "great_circle_between_zone_centroids; does not model road access or travel time",
        "limitations": "heuristic advice; supply age, trip duration, road closures and relocation arrival times must be checked before execution",
    }
    if supply is None:
        return _safe({
            **common, "supply_known": False, "recommendations": [], "conservation": None,
            "zones": [{"zone_id": zone_id, "predicted_demand": demands[zone_id],
                       "required_drivers": required[zone_id], "available_before": None, "available_after": None}
                      for zone_id in sorted(zone_map)],
        })
    if set(supply) != set(zone_map):
        raise ValueError("supply must provide exactly one availability value for every world zone")
    available = {}
    for zone_id, value in supply.items():
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0:
            raise ValueError("Supply counts must be nonnegative integers")
        available[zone_id] = int(value)
    before = available.copy()
    surplus = {zone_id: max(0, available[zone_id] - required[zone_id]) for zone_id in zone_map}
    deficit = {zone_id: max(0, required[zone_id] - available[zone_id]) for zone_id in zone_map}
    centres = _centres(world)
    pairs = sorted((_distance_km(centres[origin], centres[destination]), origin, destination)
                   for origin, count in surplus.items() if count
                   for destination, shortage in deficit.items() if shortage and origin != destination)
    recommendations = []
    for distance, origin, destination in pairs:
        count = min(surplus[origin], deficit[destination])
        if count:
            surplus[origin] -= count
            deficit[destination] -= count
            available[origin] -= count
            available[destination] += count
            recommendations.append({
                "from_zone_id": origin, "to_zone_id": destination, "drivers": count,
                "distance_km": round(distance, 4), "driver_km": round(count * distance, 4),
                "geometry": {"type": "LineString", "coordinates": [centres[origin], centres[destination]]},
            })
    zones = [{
        "zone_id": zone_id, "predicted_demand": demands[zone_id], "required_drivers": required[zone_id],
        "available_before": before[zone_id], "available_after": available[zone_id],
        "shortage_before": max(0, required[zone_id] - before[zone_id]),
        "shortage_after": max(0, required[zone_id] - available[zone_id]),
    } for zone_id in sorted(zone_map)]
    return _safe({
        **common, "supply_known": True, "recommendations": recommendations, "zones": zones,
        "total_relocated_drivers": sum(row["drivers"] for row in recommendations),
        "total_driver_km": round(sum(row["driver_km"] for row in recommendations), 4),
        "remaining_shortage": sum(row["shortage_after"] for row in zones),
        "conservation": {"before": sum(before.values()), "after": sum(available.values()),
                         "preserved": sum(before.values()) == sum(available.values())},
    })
