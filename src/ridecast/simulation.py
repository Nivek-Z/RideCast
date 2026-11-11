"""Fictional city geometry and seeded, bucket-level ride request simulation.

This is an explanatory simulator, not a calibrated traffic digital twin.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from shapely.geometry import Point, mapping, shape

PANEL_COLUMNS = [
    "timestamp",
    "zone_id",
    "demand",
    "passengers",
    "completed",
    "cancelled",
    "temperature",
    "precipitation",
    "wind_speed",
    "holiday",
    "event_intensity",
    "traffic_index",
    "available_drivers",
]
ORDER_COLUMNS = [
    "order_id",
    "requested_at",
    "pickup_zone_id",
    "dropoff_zone_id",
    "passenger_count",
    "status",
    "pickup_lon",
    "pickup_lat",
    "dropoff_lon",
    "dropoff_lat",
    "driver_id",
    "travel_minutes",
]
MAX_PANEL_ROWS = 150_000
MAX_ORDERS = 500_000


def utc_timestamp(value, timezone_name="Asia/Shanghai") -> pd.Timestamp:
    """Interpret a naive timestamp in the declared city timezone; reject DST ambiguity."""
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("时间不能为空")
    if result.tzinfo is None:
        result = result.tz_localize(timezone_name, ambiguous="raise", nonexistent="raise")
    return result.tz_convert("UTC")


def _emit(progress, stage, fraction, message):
    if progress:
        progress(stage, float(fraction), message)


def _nonnegative(value, name, maximum=1e9):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须为有限非负数") from exc
    if not math.isfinite(number) or number < 0 or number > maximum:
        raise ValueError(f"{name} 必须位于 0 到 {maximum} 之间")
    return number


def create_world(
    name="霁川市", rows=4, cols=4, seed=42, bucket_minutes=60, timezone="Asia/Shanghai", **kwargs
) -> dict:
    """Build a fictional WGS84 city; seeded geometry can be exported as GeoJSON."""
    if not isinstance(rows, int) or not isinstance(cols, int) or not 1 <= rows <= 10 or not 1 <= cols <= 10:
        raise ValueError("rows/cols 必须是 1 到 10 的整数")
    rng = np.random.default_rng(seed)
    west, south = float(kwargs.get("longitude", 120.10)), float(kwargs.get("latitude", 30.20))
    size = float(kwargs.get("cell_degrees", 0.012))
    if not 0.001 <= size <= 0.1:
        raise ValueError("cell_degrees 必须位于 0.001 到 0.1 之间")
    kinds = ["residential", "business", "commercial", "transport", "leisure"]
    kind_names = {
        "residential": "居住区",
        "business": "商务区",
        "commercial": "商贸区",
        "transport": "枢纽区",
        "leisure": "文娱区",
    }
    widths = rng.uniform(0.72, 1.28, cols)
    heights = rng.uniform(0.72, 1.28, rows)
    x_edges = np.r_[west, west + np.cumsum(widths / widths.sum() * cols * size)]
    y_edges = np.r_[south, south + np.cumsum(heights / heights.sum() * rows * size)]
    kind_schedule = rng.choice(kinds, size=rows * cols, p=[0.32, 0.22, 0.20, 0.12, 0.14])
    if rows * cols >= len(kinds):
        # Educational demo cities contain each functional land use; locations
        # and the remaining mix are still seeded rather than fixed by index.
        for position, kind in zip(rng.choice(rows * cols, size=len(kinds), replace=False), kinds):
            kind_schedule[position] = kind
    features = []
    for row in range(rows):
        for col in range(cols):
            index = row * cols + col
            kind = str(kind_schedule[index])
            x, y, right, top = map(float, (x_edges[col], y_edges[row], x_edges[col + 1], y_edges[row + 1]))
            geometry = {
                "type": "Polygon",
                "coordinates": [[[x, y], [right, y], [right, top], [x, top], [x, y]]],
            }
            properties = {
                "zone_id": f"z_{index:03d}",
                "name": f"{kind_names[kind]}{index + 1:02d}",
                "kind": kind,
                "base_demand": round(float(rng.uniform(5, 15)), 3),
                "population": int(rng.integers(8000, 28000) * (1.8 if kind == "residential" else 0.7)),
                "jobs": int(rng.integers(3000, 15000) * (2 if kind == "business" else 0.8)),
                "attractiveness": round(float(rng.uniform(0.7, 1.5)), 3),
                "center": [(x + right) / 2, (y + top) / 2],
            }
            features.append({"type": "Feature", "geometry": geometry, "properties": properties})
    roads = []
    for index in range(rows + 1):
        y = float(y_edges[index])
        roads.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[west, y], [west + cols * size, y]]},
                "properties": {"road_id": f"h_{index}", "name": f"纬路{index}", "speed_kph": 35},
            }
        )
    for index in range(cols + 1):
        x = float(x_edges[index])
        roads.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[x, south], [x, south + rows * size]]},
                "properties": {"road_id": f"v_{index}", "name": f"经路{index}", "speed_kph": 40},
            }
        )
    identity = hashlib.sha256(
        json.dumps(
            ["seeded-city-v2", name, rows, cols, seed, bucket_minutes, timezone, west, south, size]
        ).encode()
    ).hexdigest()[:16]
    world = {
        "schema_version": "1.0",
        "world_id": kwargs.get("world_id", f"w_{identity}"),
        "name": name,
        "timezone": timezone,
        "bucket_minutes": bucket_minutes,
        "seed": seed,
        "zones": {"type": "FeatureCollection", "features": features},
        "roads": {"type": "FeatureCollection", "features": roads},
        "calendar": kwargs.get("calendar", {"holiday_dates": ["2026-01-01"], "working_dates": []}),
        "events": kwargs.get("events", []),
        "metadata": {
            "fictional": True,
            "generator_version": "seeded-city-v2",
            "coordinate_system": "EPSG:4326",
            "rows": rows,
            "cols": cols,
            "procedural_events": True,
            "evolution": {
                "version": "absolute-time-v2",
                "anchor": "1970-01-01T00:00:00Z",
                "seed_policy": "world.seed + simulation seed + absolute UTC bucket + zone_id",
                "geometry": "seeded nonuniform rectangular tessellation with shared edges",
                "weather": "smooth absolute-time synoptic fronts with seasonal and diurnal cycles",
                "demand": "spatially shared and local smooth latent regimes plus calendar/activity effects",
                "feedback": "supply and congestion respond to previous bucket realized demand",
                "scheduled_events": "seeded weekly leisure/commercial programme; announced in advance",
                "scope": "macro bucket simulator; no continuous per-driver trajectory or road routing",
            },
            "description": "完全虚构的霁川市地图；经纬度仅用于通用GIS兼容，不代表真实道路或行政区。",
        },
    }
    return validate_world(world)


def validate_world(world: dict) -> dict:
    """Validate polygons, stable IDs, planned context and spatial bounds."""
    try:
        if not isinstance(world, dict):
            raise ValueError("world 必须为 JSON 对象")
        return _validate_world(world)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"world 结构或几何无效: {exc}") from exc


def _validate_world(world: dict) -> dict:
    result = copy.deepcopy(world)
    if result.get("schema_version", "1.0") != "1.0":
        raise ValueError("不支持的 world schema_version")
    result["schema_version"] = "1.0"
    result.setdefault("seed", 42)
    if (
        isinstance(result["seed"], bool)
        or not isinstance(result["seed"], int)
        or not 0 <= result["seed"] < 2**32
    ):
        raise ValueError("world.seed 必须为 [0,2^32) 的整数")
    if not isinstance(result.get("name"), str) or not 1 <= len(result["name"]) <= 100:
        raise ValueError("world.name 必须为 1 到 100 字符")
    try:
        ZoneInfo(result.get("timezone", "Asia/Shanghai"))
    except Exception as exc:
        raise ValueError("timezone 必须为有效 IANA 时区") from exc
    result.setdefault("timezone", "Asia/Shanghai")
    if result.get("bucket_minutes", 60) not in (15, 30, 60):
        raise ValueError("bucket_minutes 仅支持 15/30/60")
    result.setdefault("bucket_minutes", 60)
    zones = result.get("zones", {})
    if zones.get("type") != "FeatureCollection" or not 1 <= len(zones.get("features", [])) <= 100:
        raise ValueError("zones 必须为含 1 到 100 区域的 GeoJSON FeatureCollection")
    ids, geometries = set(), []
    defaults = {"kind": "mixed", "base_demand": 10, "population": 10000, "jobs": 5000, "attractiveness": 1}
    for feature in zones["features"]:
        if feature.get("type") != "Feature":
            raise ValueError("区域必须为 GeoJSON Feature")
        props = feature.setdefault("properties", {})
        zone_id = props.get("zone_id")
        if not isinstance(zone_id, str) or not zone_id or len(zone_id) > 64 or zone_id in ids:
            raise ValueError("zone_id 必须为唯一非空字符串（不超过64字符）")
        ids.add(zone_id)
        for key, default in defaults.items():
            props.setdefault(key, default)
        props.setdefault("name", zone_id)
        for key in ("base_demand", "population", "jobs", "attractiveness"):
            props[key] = _nonnegative(props[key], key, 1e8)
        try:
            geometry = shape(feature["geometry"])
        except Exception as exc:
            raise ValueError("无效区域 geometry") from exc
        if (
            geometry.geom_type not in ("Polygon", "MultiPolygon")
            or not geometry.is_valid
            or geometry.is_empty
            or geometry.area <= 0
        ):
            raise ValueError("区域须为有效非空 Polygon/MultiPolygon")
        x1, y1, x2, y2 = geometry.bounds
        if not (-180 <= x1 <= x2 <= 180 and -85 <= y1 <= y2 <= 85):
            raise ValueError("地图坐标必须为 WGS84 经度[-180,180]、纬度[-85,85]")
        for other in geometries:
            if geometry.intersection(other).area > 1e-12:
                raise ValueError("区域不能面积重叠（允许共享边界）")
        geometries.append(geometry)
        props["center"] = list(geometry.representative_point().coords[0])
        feature["geometry"] = mapping(geometry)
    zones["features"].sort(key=lambda feature: feature["properties"]["zone_id"])
    roads = result.setdefault("roads", {"type": "FeatureCollection", "features": []})
    if roads.get("type") != "FeatureCollection" or len(roads.get("features", [])) > 5000:
        raise ValueError("roads 必须为至多5000条道路的 FeatureCollection")
    for road in roads.get("features", []):
        geometry = shape(road["geometry"])
        if (
            geometry.geom_type not in ("LineString", "MultiLineString")
            or not geometry.is_valid
            or geometry.is_empty
        ):
            raise ValueError("道路须为有效 LineString/MultiLineString")
        x1, y1, x2, y2 = geometry.bounds
        if not (-180 <= x1 <= x2 <= 180 and -85 <= y1 <= y2 <= 85):
            raise ValueError("道路坐标越界")
    calendar = result.setdefault("calendar", {})
    for key in ("holiday_dates", "working_dates"):
        values = calendar.setdefault(key, [])
        if not isinstance(values, list) or len(values) > 5000:
            raise ValueError("calendar 日期须为数组且不超过5000项")
        for value in values:
            if not isinstance(value, str) or str(pd.Timestamp(value).date()) != value:
                raise ValueError("calendar 日期须为 YYYY-MM-DD")
        calendar[key] = sorted(set(values))
    if set(calendar["holiday_dates"]) & set(calendar["working_dates"]):
        raise ValueError("同一日期不能同时为 holiday_date 与 working_date")
    _validate_events(result.setdefault("events", []), ids, result["timezone"])
    result.setdefault("metadata", {})
    result["metadata"]["coordinate_system"] = "EPSG:4326"
    return result


def _validate_events(events, zone_ids, timezone_name):
    if not isinstance(events, list) or len(events) > 5000:
        raise ValueError("events 必须为不超过5000项的数组")
    for event in events:
        if event.get("zone_id") not in zone_ids:
            raise ValueError("活动引用未知 zone_id")
        start, end = (
            utc_timestamp(event.get("start"), timezone_name),
            utc_timestamp(event.get("end"), timezone_name),
        )
        if start >= end:
            raise ValueError("活动 start 必须早于 end")
        event["start"], event["end"] = start.isoformat(), end.isoformat()
        event["intensity"] = _nonnegative(event.get("intensity", 1), "event.intensity", 10)
        event.setdefault("name", "虚拟活动")


def calendar_features(timestamp, world):
    local = utc_timestamp(timestamp).tz_convert(world["timezone"])
    date = str(local.date())
    calendar = world.get("calendar", {})
    is_working = date in calendar.get("working_dates", [])
    holiday = int(date in calendar.get("holiday_dates", []) or (local.dayofweek >= 5 and not is_working))
    return holiday


def event_features(timestamp, zone_id, world):
    timestamp = utc_timestamp(timestamp)
    intensity = sum(
        float(event["intensity"])
        for event in world.get("events", [])
        if event["zone_id"] == zone_id
        and utc_timestamp(event["start"]) <= timestamp < utc_timestamp(event["end"])
    )
    if world.get("metadata", {}).get("procedural_events", False):
        local = timestamp.tz_convert(world["timezone"])
        props = next(
            (
                feature["properties"]
                for feature in world["zones"]["features"]
                if feature["properties"]["zone_id"] == zone_id
            ),
            {},
        )
        # Recurring programmes are a known calendar, never inferred from future demand.
        schedule_seed = _stable_seed(world.get("seed", 42), zone_id, "programme")
        if props.get("kind") == "leisure" and local.dayofweek in (4, 5) and 18 <= local.hour < 22:
            intensity += 0.6 + (schedule_seed % 7) / 10
        if props.get("kind") == "commercial" and local.dayofweek == 5 and 11 <= local.hour < 17:
            intensity += 0.3 + (schedule_seed % 5) / 10
    return float(intensity)


def _sample_point(geometry, rng):
    x1, y1, x2, y2 = geometry.bounds
    for _ in range(2000):
        x, y = float(rng.uniform(x1, x2)), float(rng.uniform(y1, y2))
        if geometry.covers(Point(x, y)):
            return x, y
    return tuple(geometry.representative_point().coords[0])


def _distance_km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def _stable_seed(*parts):
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "little")


def _normal_noise(key, domain, index):
    # Explicit hash-to-normal transform avoids a stateful process startup or window warm-up.
    u = (_stable_seed(key, domain, int(index), "u") + 0.5) / 2**64
    v = (_stable_seed(key, domain, int(index), "v") + 0.5) / 2**64
    return math.sqrt(-2 * math.log(max(u, 1e-15))) * math.cos(2 * math.pi * v)


def _smooth_noise(key, domain, absolute_hour, scale):
    position = absolute_hour / scale
    index = math.floor(position)
    weight = position - index
    weight = weight * weight * (3 - 2 * weight)
    return (1 - weight) * _normal_noise(key, domain, index) + weight * _normal_noise(key, domain, index + 1)


def _weather(timestamp, world, key, rain_multiplier):
    local = timestamp.tz_convert(world["timezone"])
    hour = local.hour + local.minute / 60
    absolute_hour = timestamp.value / 3_600_000_000_000
    front = _smooth_noise(key, "weather_front", absolute_hour, 24)
    showers = _smooth_noise(key, "weather_showers", absolute_hour, 6)
    precipitation = float(max(0, front + 0.45 * showers - 0.15) * 4.2 * rain_multiplier)
    temperature = float(
        18
        + 8 * math.sin(2 * math.pi * (local.dayofyear - 90) / 365)
        + 4 * math.sin(2 * math.pi * (hour - 9) / 24)
        - 1.2 * front
        + 0.5 * _smooth_noise(key, "temperature", absolute_hour, 8)
    )
    wind = float(max(0, 2.7 + 0.9 * _smooth_noise(key, "wind", absolute_hour, 12) + precipitation * 0.12))
    return temperature, precipitation, wind


def _peak_profile(hour, kind):
    morning = math.exp(-0.5 * ((hour - 8) / 1.5) ** 2)
    evening = math.exp(-0.5 * ((hour - 18) / 1.8) ** 2)
    daytime = math.exp(-0.5 * ((hour - 14) / 4) ** 2)
    night = math.exp(-0.5 * ((hour - 21) / 2.3) ** 2)
    peak = {
        "residential": 1.8 * morning + 0.6 * evening + 0.3 * daytime,
        "business": 0.7 * morning + 1.7 * evening + 0.5 * daytime,
        "commercial": 0.3 * morning + 1.0 * evening + 0.9 * daytime,
        "transport": morning + evening + 0.8 * daytime,
        "leisure": 0.2 * daytime + 1.7 * night,
    }.get(kind, morning + evening + daytime)
    return peak, morning, evening, daytime


def _demand_at(timestamp, props, world, key, multipliers):
    local = timestamp.tz_convert(world["timezone"])
    hour = local.hour + local.minute / 60
    absolute_hour = timestamp.value / 3_600_000_000_000
    absolute_bucket = timestamp.value // (world["bucket_minutes"] * 60_000_000_000)
    zone_id, kind = props["zone_id"], props["kind"]
    peak, _, _, _ = _peak_profile(hour, kind)
    holiday = calendar_features(timestamp, world)
    holiday_factor = (
        (1.35 if kind in ("commercial", "leisure") else 0.72 if kind == "business" else 1.05)
        if holiday
        else 1
    )
    _, precipitation, _ = _weather(timestamp, world, key, multipliers["rain_multiplier"])
    centre = props["center"]
    cluster = f"{math.floor(centre[0] / 0.025)}:{math.floor(centre[1] / 0.025)}"
    regime = math.exp(
        0.16 * _smooth_noise(key, "city_economy", absolute_hour, 168)
        + 0.10 * _smooth_noise(key, f"district:{cluster}", absolute_hour, 24)
        + 0.07 * _smooth_noise(key, f"local:{zone_id}", absolute_hour, 6)
    )
    events = event_features(timestamp, zone_id, world)
    lam = (
        props["base_demand"]
        * (0.18 + peak)
        * holiday_factor
        * (1 + min(precipitation, 15) * 0.045)
        * (1 + events)
        * regime
        * multipliers["demand_multiplier"]
        * world["bucket_minutes"]
        / 60
    )
    if lam > 5000:
        raise ValueError("场景单桶需求过大，请降低 base_demand/倍数")
    demand = int(np.random.default_rng(_stable_seed(key, "demand", absolute_bucket, zone_id)).poisson(lam))
    return demand, events, regime


def _prepare_scenario(world, scenario):
    world = validate_world(world)
    scenario = copy.deepcopy({} if scenario is None else scenario)
    if not isinstance(scenario, dict):
        raise ValueError("scenario 必须为对象")
    allowed = {
        "demand_multiplier",
        "rain_multiplier",
        "traffic_multiplier",
        "supply_multiplier",
        "holiday_dates",
        "working_dates",
        "events",
    }
    if set(scenario) - allowed:
        raise ValueError(f"未知场景参数: {sorted(set(scenario) - allowed)}")
    multipliers = {
        key: _nonnegative(scenario.get(key, 1), key, 5)
        for key in ("demand_multiplier", "rain_multiplier", "traffic_multiplier", "supply_multiplier")
    }
    for field in ("holiday_dates", "working_dates"):
        if field in scenario:
            world["calendar"][field] = scenario[field]
    if "events" in scenario:
        world["events"] = scenario["events"]
    return validate_world(world), scenario, multipliers


def _zone_state(timestamp, props, world, key, multipliers):
    minutes = world["bucket_minutes"]
    absolute_bucket = int(timestamp.value // (minutes * 60_000_000_000))
    local = timestamp.tz_convert(world["timezone"])
    _, morning, evening, daytime = _peak_profile(local.hour + local.minute / 60, props["kind"])
    zone_id = props["zone_id"]
    temperature, precipitation, wind = _weather(timestamp, world, key, multipliers["rain_multiplier"])
    demand, events, regime = _demand_at(timestamp, props, world, key, multipliers)
    previous_demand, _, _ = _demand_at(
        timestamp - pd.Timedelta(minutes=minutes), props, world, key, multipliers
    )
    base_capacity = max(1, props["base_demand"] * minutes / 25)
    demand_pressure = previous_demand / base_capacity
    traffic = float(
        np.clip(
            (
                0.82
                + 0.4 * (morning + evening)
                + precipitation * 0.035
                + events * 0.12
                + min(0.55, max(0, demand_pressure - 0.7) * 0.20)
            )
            * multipliers["traffic_multiplier"],
            0.05,
            5,
        )
    )
    supply_rate = max(
        0,
        (props["base_demand"] * (0.8 + 0.4 * daytime) + 0.30 * previous_demand * 60 / minutes)
        * multipliers["supply_multiplier"],
    )
    drivers = int(
        np.random.default_rng(_stable_seed(key, "supply", absolute_bucket, zone_id)).poisson(supply_rate)
    )
    completed = min(demand, max(0, int(drivers * minutes / (25 * traffic))))
    passenger_counts = np.random.default_rng(_stable_seed(key, "orders", absolute_bucket, zone_id)).choice(
        [1, 2, 3, 4], size=demand, p=[0.66, 0.24, 0.08, 0.02]
    )
    return {
        "timestamp": timestamp,
        "zone_id": zone_id,
        "demand": demand,
        "passengers": int(passenger_counts.sum()),
        "completed": completed,
        "cancelled": demand - completed,
        "temperature": temperature,
        "precipitation": precipitation,
        "wind_speed": wind,
        "holiday": calendar_features(timestamp, world),
        "event_intensity": events,
        "traffic_index": traffic,
        "available_drivers": drivers,
        "latent_regime": float(regime),
        "previous_bucket_demand": previous_demand,
    }


def evolution_snapshot(world, timestamp, seed=42, scenario=None) -> dict:
    """Read one deterministic synthetic bucket without generating detailed orders.

    This is simulator state (including latent diagnostics), never a future-weather
    observation available to the demand predictor. Arbitrary query instants floor
    to the UTC bucket, exactly as the dataset time axis does.
    """
    world, scenario, multipliers = _prepare_scenario(world, scenario)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed 必须为 [0,2^32) 的整数")
    requested = utc_timestamp(timestamp, world["timezone"])
    timestamp = requested.floor(f"{world['bucket_minutes']}min")
    key = _stable_seed(world.get("seed", 42), seed, "absolute-time-v2")
    zones = [
        _zone_state(timestamp, feature["properties"], world, key, multipliers)
        for feature in world["zones"]["features"]
    ]
    for zone in zones:
        zone["timestamp"] = timestamp.isoformat()
        zone["state"] = "undersupplied" if zone["cancelled"] else "balanced"
    first = zones[0]
    return {
        "world_id": world["world_id"],
        "world_time": timestamp.isoformat(),
        "timestamp": timestamp.isoformat(),
        "requested_time": requested.isoformat(),
        "local_time": timestamp.tz_convert(world["timezone"]).isoformat(),
        "bucket_end": (timestamp + pd.Timedelta(minutes=world["bucket_minutes"])).isoformat(),
        "bucket_minutes": world["bucket_minutes"],
        "seed": seed,
        "world_seed": world.get("seed", 42),
        "status": "synthetic_observation",
        "zones": zones,
        "weather": {key: first[key] for key in ("temperature", "precipitation", "wind_speed")},
        "totals": {
            key: sum(zone[key] for zone in zones)
            for key in ("demand", "passengers", "completed", "cancelled", "available_drivers")
        },
        "active_events": [
            {"zone_id": zone["zone_id"], "intensity": zone["event_intensity"]}
            for zone in zones
            if zone["event_intensity"] > 0
        ],
        "evolution": world.get("metadata", {}).get("evolution", {"version": "absolute-time-v2"}),
        "scenario": scenario,
        "assumptions": [
            "绝对时间锚定，窗口无关；静态地图由world.seed决定，运行seed决定环境/需求实现。",
            "这是合成世界观测状态；未来天气/潜在状态不能作为模型默认可知特征。",
            "供给与拥堵为桶级宏观近似，不包含逐车连续轨迹或路由。",
        ],
    }


def simulate(world, start, days, seed=42, scenario=None, progress=None):
    """Generate requested orders and a full panel with weather/calendar/supply factors."""
    world, scenario, multipliers = _prepare_scenario(world, scenario)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
        raise ValueError("days 必须为 1 到 366 的整数")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed 必须为 [0,2^32) 的整数")
    start = utc_timestamp(start, world["timezone"])
    minutes = world["bucket_minutes"]
    if start != start.floor(f"{minutes}min"):
        raise ValueError("start 必须对齐时间桶")
    count = days * 24 * 60 // minutes
    zones = world["zones"]["features"]
    if count * len(zones) > MAX_PANEL_ROWS:
        raise ValueError(f"面板最多 {MAX_PANEL_ROWS} 行，请减少天数或区域数")
    times = pd.date_range(start, periods=count, freq=f"{minutes}min")
    geometries = [shape(feature["geometry"]) for feature in zones]
    properties = [feature["properties"] for feature in zones]
    zone_ids = [props["zone_id"] for props in properties]
    # Reject obviously oversized requests before allocating hundreds of thousands
    # of per-order Python objects. This is a planning estimate, not a hard bound;
    # a second hard guard below handles high Poisson draws and concentrated events.
    average_profile = {
        "residential": 0.70,
        "business": 0.82,
        "commercial": 0.97,
        "transport": 0.86,
        "leisure": 0.67,
    }
    estimated_orders = (
        sum(props["base_demand"] * average_profile.get(props["kind"], 0.9) for props in properties)
        * count
        * minutes
        / 60
        * multipliers["demand_multiplier"]
        * 1.3
    )
    if estimated_orders > MAX_ORDERS:
        raise ValueError(
            f"预计订单约 {int(estimated_orders)}，超过单次 {MAX_ORDERS} 上限，请减少天数/区域/需求倍数"
        )
    panels, orders = [], []
    key = _stable_seed(world.get("seed", 42), seed, "absolute-time-v2")
    _emit(progress, "simulate", 0.02, "读取绝对时间锚定的演化世界并生成订单")
    for time_index, timestamp in enumerate(times):
        local = timestamp.tz_convert(world["timezone"])
        hour = local.hour + local.minute / 60
        absolute_bucket = int(timestamp.value // (minutes * 60_000_000_000))
        _, morning, evening, _ = _peak_profile(hour, "mixed")
        weights = np.array(
            [
                1
                + p["attractiveness"]
                + (2 if p["kind"] == "business" and morning > 0.5 else 0)
                + (2 if p["kind"] == "residential" and evening > 0.5 else 0)
                for p in properties
            ]
        )
        destination_probabilities = weights / weights.sum()
        for zone_index, props in enumerate(properties):
            zone_id = props["zone_id"]
            state = _zone_state(timestamp, props, world, key, multipliers)
            demand, completed, cancelled = state["demand"], state["completed"], state["cancelled"]
            traffic, drivers = state["traffic_index"], state["available_drivers"]
            rng = np.random.default_rng(_stable_seed(key, "orders", absolute_bucket, zone_id))
            passenger_counts = rng.choice([1, 2, 3, 4], size=demand, p=[0.66, 0.24, 0.08, 0.02])
            panels.append([state[column] for column in PANEL_COLUMNS])
            if len(orders) + demand > MAX_ORDERS:
                raise ValueError(f"订单上限 {MAX_ORDERS}，请减少模拟规模")
            completed_flags = np.array([True] * completed + [False] * cancelled)
            rng.shuffle(completed_flags)
            for order_index in range(demand):
                destination = int(rng.choice(len(zones), p=destination_probabilities))
                pickup, dropoff = (
                    _sample_point(geometries[zone_index], rng),
                    _sample_point(geometries[destination], rng),
                )
                served = bool(completed_flags[order_index])
                travel = round(max(3.0, _distance_km(pickup, dropoff) * 1.3 / 28 * 60 * traffic + 3), 2)
                orders.append(
                    [
                        f"s{world.get('seed', 42)}_{seed}_{absolute_bucket}_{zone_id}_{order_index}",
                        timestamp + pd.Timedelta(seconds=int(rng.integers(0, minutes * 60))),
                        zone_id,
                        zone_ids[destination],
                        int(passenger_counts[order_index]),
                        "completed" if served else "cancelled",
                        *pickup,
                        *dropoff,
                        f"driver_{zone_index}_{order_index % max(1, drivers)}" if served else None,
                        travel if served else None,
                    ]
                )
        if time_index % max(1, count // 50) == 0:
            _emit(
                progress,
                "simulate",
                0.03 + 0.9 * (time_index + 1) / count,
                f"已生成 {time_index + 1}/{count} 个时间桶",
            )
    panel = pd.DataFrame(panels, columns=PANEL_COLUMNS)
    order_frame = pd.DataFrame(orders, columns=ORDER_COLUMNS)
    order_frame["requested_at"] = pd.to_datetime(order_frame["requested_at"], utc=True)
    order_frame["passenger_count"] = order_frame["passenger_count"].astype("int64")
    for column in ("pickup_lon", "pickup_lat", "dropoff_lon", "dropoff_lat", "travel_minutes"):
        order_frame[column] = pd.to_numeric(order_frame[column]).astype("float64")
    metadata = {
        "source": "synthetic",
        "target_semantics": "requests",
        "observation_start": start.isoformat(),
        "observation_end": (times[-1] + pd.Timedelta(minutes=minutes)).isoformat(),
        "seed": seed,
        "scenario": scenario,
        "effective_world": world,
        "quality": {
            "input_rows": len(orders),
            "accepted_rows": len(orders),
            "rejected_rows": 0,
            "duplicate_rows": 0,
            "zero_demand_buckets": int((panel["demand"] == 0).sum()),
            "coverage_attested": True,
            "planned_order_estimate": int(estimated_orders),
            "warnings": ["合成城市与需求生成机制未经真实城市校准；司机容量按桶近似，非逐车交通仿真。"],
        },
        "provenance": {
            "generator": "ridecast.simulation.v2",
            "evolution": world.get("metadata", {}).get("evolution", {"version": "absolute-time-v2"}),
            "window_independent": True,
            "world_seed": world.get("seed", 42),
            "seed": seed,
            "fictional": True,
            "future_weather_policy": "训练和评估只能使用预测起点及之前的观测天气；场景覆盖属条件假设。",
        },
    }
    _emit(progress, "simulate", 0.95, "虚拟订单生成完成")
    return panel, order_frame, metadata
