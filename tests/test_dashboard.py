import json

import pandas as pd
import pytest

from ridecast.dashboard import dispatch, flows, heatmap, list_orders, overview, replay, timeseries


@pytest.fixture
def dataset(tmp_path):
    zones = []
    for index, zone_id in enumerate(("home", "office")):
        x = 120.0 + 0.02 * index
        zones.append({
            "type": "Feature",
            "properties": {"zone_id": zone_id, "name": zone_id, "kind": zone_id,
                           "population": 1000, "jobs": 500, "base_demand": 2, "attractiveness": 1},
            "geometry": {"type": "Polygon", "coordinates": [[[x, 30.0], [x + 0.01, 30.0],
                         [x + 0.01, 30.01], [x, 30.01], [x, 30.0]]]},
        })
    world = {"world_id": "w_test", "timezone": "Asia/Shanghai", "bucket_minutes": 60,
             "zones": {"type": "FeatureCollection", "features": zones},
             "roads": {"type": "FeatureCollection", "features": []},
             "events": [{"name": "show", "zone_id": "office", "start": "2026-01-01T01:00:00Z",
                         "end": "2026-01-01T02:00:00Z", "intensity": 2}]}
    stamps = pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="h")
    rows = []
    for index, stamp in enumerate(stamps):
        for zone_index, zone_id in enumerate(("home", "office")):
            demand = ((2, 1), (1, 1), (0, 1))[index][zone_index]
            cancelled = int(index == 0 and zone_id == "home")
            rows.append({
                "timestamp": stamp, "zone_id": zone_id, "demand": demand,
                "passengers": demand * 2, "completed": demand - cancelled, "cancelled": cancelled,
                "temperature": 20 + zone_index, "precipitation": float(index == 1), "wind_speed": 2.0,
                "holiday": 1, "event_intensity": float(index == 1 and zone_id == "office"),
                "traffic_index": 1.1, "available_drivers": (4, 5, 6)[index] + zone_index,
            })
    panel = pd.DataFrame(rows)
    orders = []
    schedule = [
        ("a", "00:10", "home", "office", "completed", "driver-1", 120),
        ("b", "00:20", "home", "office", "cancelled", None, None),
        ("c", "00:30", "office", "home", "completed", "driver-2", 15),
        ("d", "01:10", "home", "office", "completed", "driver-3", 20),
        ("e", "01:50", "office", "home", "completed", "driver-4", 20),
        ("f", "02:20", "office", "home", "completed", "driver-5", 20),
    ]
    for order_id, minute, origin, destination, status, driver_id, duration in schedule:
        orders.append({
            "order_id": order_id, "requested_at": pd.Timestamp(f"2026-01-01T{minute}:00Z"),
            "pickup_zone_id": origin, "dropoff_zone_id": destination, "passenger_count": 2,
            "status": status, "driver_id": driver_id, "travel_minutes": duration,
            "pickup_lon": 120.0, "pickup_lat": 30.0, "dropoff_lon": 120.02, "dropoff_lat": 30.01,
        })
    trips = pd.DataFrame(orders)
    manifest = {"schema_version": "1.0", "dataset_id": "d_test", "world_id": "w_test",
                "source": "synthetic", "target_semantics": "requests", "bucket_minutes": 60,
                "observation_start": stamps[0].isoformat(),
                "observation_end": (stamps[-1] + pd.Timedelta(hours=1)).isoformat(),
                "row_count": len(panel), "order_count": len(trips), "zone_count": 2,
                "quality": {"invalid_rows": 0}, "scenario": {}}
    panel.to_parquet(tmp_path / "panel.parquet", index=False)
    trips.to_parquet(tmp_path / "orders.parquet", index=False)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "world.json").write_text(json.dumps(world))
    return tmp_path, world


def test_overview_preserves_demand_units_and_supply_snapshots(dataset):
    path, _ = dataset
    result = overview(path)
    assert result["kpis"]["demand"] == 6
    assert result["kpis"]["passengers"] == 12
    assert result["kpis"]["completed"] == 5
    assert result["kpis"]["cancelled"] == 1
    assert result["kpis"]["completion_rate"] == pytest.approx(5 / 6)
    # City availability is 9,11,13 at each instant. 33 would falsely add drivers over time.
    assert result["kpis"]["latest_available_drivers"] == 13
    assert result["kpis"]["mean_available_drivers_per_bucket"] == 11
    assert result["range"]["bucket_count"] == 3
    assert result["weather"]["temperature"] == 20.5
    # UTC midnight is local hour 08:00 in the world's timezone.
    assert [row["hour"] for row in result["distributions"]["hour"]] == [8, 9, 10]


def test_time_filters_are_half_open_and_supply_sum_is_only_spatial(dataset):
    path, _ = dataset
    result = timeseries(path, start="2026-01-01T01:00:00Z", end="2026-01-01T02:00:00Z")
    assert result["total_count"] == 1
    assert result["rows"][0]["demand"] == 2
    assert result["rows"][0]["available_drivers"] == 11
    assert result["rows"][0]["temperature"] == 20.5
    assert result["rows"][0]["supply_gap"] == -9
    single = timeseries(path, zone_id="home", limit=2)
    assert single["truncated"] is True
    assert single["rows"][0]["available_drivers"] == 4
    assert len(single["rows"]) == 2


def test_heatmap_selects_containing_bucket_and_has_geometry(dataset):
    path, _ = dataset
    result = heatmap(path, timestamp="2026-01-01T01:35:00Z")
    assert result["timestamp"] == "2026-01-01T01:00:00+00:00"
    assert len(result["geojson"]["features"]) == 2
    office = next(row for row in result["rows"] if row["zone_id"] == "office")
    assert office["event_intensity"] == 1
    assert office["precipitation"] == 1
    assert heatmap(path)["rows"][0]["completion_rate"] is None  # zero home demand
    with pytest.raises(ValueError, match="outside"):
        heatmap(path, timestamp="2026-01-01T03:00:00Z")


def test_flows_include_cancelled_requests_and_restrict_time(dataset):
    path, _ = dataset
    result = flows(path, start="2026-01-01T00:00:00Z", end="2026-01-01T01:00:00Z")
    assert result["order_count"] == 3
    home_to_office = result["rows"][0]
    assert home_to_office["pickup_zone_id"] == "home"
    assert home_to_office["orders"] == 2
    assert home_to_office["completed"] == 1
    assert home_to_office["cancelled"] == 1
    assert home_to_office["passengers"] == 4
    assert len(result["geojson"]["features"]) == 2


def test_replay_keeps_cross_bucket_active_trip_and_excludes_future_vehicle(dataset):
    path, _ = dataset
    result = replay(path, timestamp="2026-01-01T01:20:00Z")
    assert result["is_live"] is False
    assert {row["order_id"] for row in result["orders"]} == {"d", "e"}
    assert {row["order_id"] for row in result["vehicles"]} == {"a", "d"}
    assert result["events"][0]["name"] == "show"
    carried_trip = next(row for row in result["vehicles"] if row["order_id"] == "a")
    assert carried_trip["progress"] == pytest.approx(70 / 120)
    assert carried_trip["lon"] == pytest.approx(120 + 0.02 * 70 / 120)
    assert "final dataset outcome" in result["status_semantics"]


def test_order_pagination_and_nullable_import_fields_are_json_safe(dataset):
    path, _ = dataset
    cancelled = list_orders(path, zone_id="home", status="cancelled")
    assert cancelled["total_count"] == 1
    assert cancelled["rows"][0]["driver_id"] is None
    assert cancelled["rows"][0]["travel_minutes"] is None
    first = list_orders(path, limit=2)
    second = list_orders(path, limit=2, offset=first["next_offset"])
    assert [row["order_id"] for row in first["rows"] + second["rows"]] == ["a", "b", "c", "d"]
    assert list_orders(path, offset=100)["rows"] == []
    for function in (overview, timeseries, heatmap, flows, replay, list_orders):
        json.dumps(function(path), allow_nan=False)


def test_import_placeholders_and_completed_only_data_do_not_imply_operational_rates(dataset):
    path, _ = dataset
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(source="imported", target_semantics="completed_trips")
    manifest["quality"] = {"context_default_rows": 6, "context_provided_columns": []}
    manifest_path.write_text(json.dumps(manifest))
    summary = overview(path)
    assert summary["supply_observed"] is False
    assert summary["kpis"]["completion_rate"] is None
    assert summary["kpis"]["latest_available_drivers"] is None
    map_result = heatmap(path)
    assert all(row["supply_gap"] is None and row["unserved_requests"] is None for row in map_result["rows"])
    assert timeseries(path)["rows"][0]["supply_gap"] is None
    trips = pd.read_parquet(path / "orders.parquet")
    trips.loc[0, "dropoff_zone_id"] = None
    trips.to_parquet(path / "orders.parquet", index=False)
    missing_destination = next(row for row in flows(path)["rows"] if row["dropoff_zone_id"] is None)
    assert missing_destination["destination"] is None


@pytest.mark.parametrize("kwargs", [{"zone_id": "missing"}, {"limit": 0},
                                   {"start": "2026-01-02", "end": "2026-01-01"}])
def test_chart_filter_validation(dataset, kwargs):
    path, _ = dataset
    with pytest.raises(ValueError):
        timeseries(path, **kwargs)


def test_dispatch_conserves_supply_and_uses_only_first_horizon(dataset):
    _, world = dataset
    forecast = {"model_id": "m_test", "dataset_id": "d_test", "bucket_minutes": 60, "rows": [
        {"timestamp": "2026-01-01T03:00:00Z", "zone_id": "home", "predicted_demand": 2.1},
        {"timestamp": "2026-01-01T03:00:00Z", "zone_id": "office", "predicted_demand": 7.0},
        {"timestamp": "2026-01-01T04:00:00Z", "zone_id": "home", "predicted_demand": 100.0},
        {"timestamp": "2026-01-01T04:00:00Z", "zone_id": "office", "predicted_demand": 100.0},
    ]}
    result = dispatch(forecast, world, supply={"home": 8, "office": 2})
    assert result["executed"] is False
    assert result["total_relocated_drivers"] == 5
    assert result["recommendations"][0]["from_zone_id"] == "home"
    assert result["recommendations"][0]["distance_km"] > 0
    assert result["remaining_shortage"] == 0
    assert result["conservation"] == {"before": 10, "after": 10, "preserved": True}
    assert {row["zone_id"]: row["available_after"] for row in result["zones"]} == {"home": 3, "office": 7}
    assert sum(row["required_drivers"] for row in result["zones"]) == 10
    json.dumps(result, allow_nan=False)
    absent = dispatch(forecast, world)
    assert absent["supply_known"] is False
    assert absent["recommendations"] == []
    with pytest.raises(ValueError, match="every world zone"):
        dispatch(forecast, world, supply={"home": 2})
    with pytest.raises(ValueError, match="nonnegative integers"):
        dispatch(forecast, world, supply={"home": 8, "office": -2})


def test_dispatch_insufficient_global_supply_leaves_explicit_shortage(dataset):
    _, world = dataset
    result = dispatch({"rows": [
        {"timestamp": "2026-01-01T03:00:00Z", "zone_id": "home", "predicted_demand": 4},
        {"timestamp": "2026-01-01T03:00:00Z", "zone_id": "office", "predicted_demand": 7},
    ]}, world, {"home": 5, "office": 2})
    assert result["total_relocated_drivers"] == 1
    assert result["remaining_shortage"] == 4
    assert result["conservation"]["preserved"] is True
    assert all(row["available_after"] >= 0 for row in result["zones"])
