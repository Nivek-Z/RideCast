import copy

import pandas as pd
import pytest
from shapely.geometry import Point, shape

from ridecast.simulation import PANEL_COLUMNS, create_world, evolution_snapshot, simulate, validate_world


def test_seeded_city_and_orders_reproducible():
    world = create_world(rows=2, cols=2)
    assert world == create_world(rows=2, cols=2)
    first, orders, metadata = simulate(world, "2026-01-01T00:00:00+08:00", 2, 42)
    second, other_orders, _ = simulate(world, "2026-01-01T00:00:00+08:00", 2, 42)
    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_frame_equal(orders, other_orders)
    assert len(first) == 48 * 4
    assert first.demand.sum() == len(orders)
    assert first.passengers.sum() == orders.passenger_count.sum()
    assert (first.completed + first.cancelled == first.demand).all()
    assert first.timestamp.dt.tz is not None
    assert metadata["target_semantics"] == "requests"
    geometries = {f["properties"]["zone_id"]: shape(f["geometry"]) for f in world["zones"]["features"]}
    for row in orders.itertuples():
        assert geometries[row.pickup_zone_id].covers(Point(row.pickup_lon, row.pickup_lat))
        assert geometries[row.dropoff_zone_id].covers(Point(row.dropoff_lon, row.dropoff_lat))


def test_no_supply_keeps_unmet_demand_and_no_completed_driver():
    panel, orders, _ = simulate(create_world(rows=1, cols=1), "2026-01-02", 1, 7, {"supply_multiplier": 0})
    assert panel.demand.sum() > 0
    assert panel.completed.sum() == 0
    assert panel.cancelled.sum() == len(orders)
    assert orders.driver_id.isna().all()


def test_zero_demand_is_complete_not_missing():
    panel, orders, _ = simulate(
        create_world(rows=1, cols=2, bucket_minutes=30), "2026-01-02", 1, 7, {"demand_multiplier": 0}
    )
    assert len(panel) == 96
    assert not len(orders)
    assert panel.demand.sum() == 0
    assert not panel.isna().any().any()


def test_planned_event_and_holiday_scenario():
    world = create_world(rows=1, cols=1)
    panel, _, metadata = simulate(
        world,
        "2026-01-02",
        1,
        7,
        {
            "holiday_dates": ["2026-01-02"],
            "events": [
                {
                    "zone_id": "z_000",
                    "start": "2026-01-02T18:00:00+08:00",
                    "end": "2026-01-02T20:00:00+08:00",
                    "intensity": 2,
                }
            ],
        },
    )
    assert panel.holiday.eq(1).all()
    assert panel.event_intensity.eq(2).sum() == 2
    assert metadata["effective_world"]["events"]
    assert not world["events"]


def test_overlap_rejected_shared_edges_allowed():
    world = create_world(rows=1, cols=2)
    validate_world(world)
    bad = copy.deepcopy(world)
    bad["zones"]["features"][1]["geometry"] = bad["zones"]["features"][0]["geometry"]
    with pytest.raises(ValueError, match="重叠"):
        validate_world(bad)


@pytest.mark.parametrize(
    "kwargs", [{"rows": 0}, {"cols": 11}, {"bucket_minutes": 7}, {"timezone": "fake/place"}]
)
def test_invalid_city_settings(kwargs):
    with pytest.raises(ValueError):
        create_world(**kwargs)


def test_bad_start_and_scenario_rejected():
    world = create_world(rows=1, cols=1)
    with pytest.raises(ValueError, match="对齐"):
        simulate(world, "2026-01-01T00:01:00+08:00", 1, 42)
    with pytest.raises(ValueError, match="未知场景"):
        simulate(world, "2026-01-01", 1, 42, {"magic": 3})
    with pytest.raises(ValueError):
        simulate(world, "2026-01-01", 1, 42, {"demand_multiplier": float("nan")})


def test_absolute_world_overlapping_and_segmented_windows_match_exactly():
    world = create_world(rows=2, cols=2)
    whole, whole_orders, meta = simulate(world, "2026-01-01", 3, 99)
    beginning, beginning_orders, _ = simulate(world, "2026-01-01", 1, 99)
    continuation, continuation_orders, _ = simulate(world, "2026-01-02", 2, 99)
    pd.testing.assert_frame_equal(whole, pd.concat([beginning, continuation], ignore_index=True))
    pd.testing.assert_frame_equal(
        whole_orders, pd.concat([beginning_orders, continuation_orders], ignore_index=True)
    )
    overlap, overlap_orders, _ = simulate(world, "2026-01-02", 1, 99)
    cutoff = pd.Timestamp("2026-01-03T00:00:00+08:00")
    pd.testing.assert_frame_equal(
        overlap, continuation.loc[continuation.timestamp < cutoff].reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(
        overlap_orders,
        continuation_orders.loc[continuation_orders.requested_at < cutoff].reset_index(drop=True),
    )
    assert meta["provenance"]["window_independent"] is True


def test_snapshot_is_exactly_the_same_bucket_as_simulated_panel():
    world = create_world(rows=2, cols=2, seed=7)
    scenario = {"rain_multiplier": 1.8, "demand_multiplier": 1.4}
    panel, _, _ = simulate(world, "2026-01-02", 1, 123, scenario)
    snapshot = evolution_snapshot(world, "2026-01-02T12:37:00+08:00", 123, scenario)
    expected = panel.loc[panel.timestamp == pd.Timestamp(snapshot["world_time"])].reset_index(drop=True)
    actual = pd.DataFrame(snapshot["zones"])[PANEL_COLUMNS]
    actual["timestamp"] = pd.to_datetime(actual["timestamp"], utc=True)
    pd.testing.assert_frame_equal(expected, actual)
    assert snapshot["totals"]["demand"] == expected.demand.sum()
    assert snapshot["status"] == "synthetic_observation"


def test_different_seeds_vary_map_and_dynamics_and_weather_is_continuous():
    world = create_world(rows=2, cols=2)
    other = create_world(rows=2, cols=2, seed=43)
    assert world["zones"] != other["zones"]
    first, _, _ = simulate(world, "2026-01-01", 2, 42)
    second, _, _ = simulate(world, "2026-01-01", 2, 43)
    assert not first.demand.equals(second.demand)
    weather = first.groupby("timestamp").first()
    assert weather.temperature.diff().abs().max() < 4
    assert weather.precipitation.diff().abs().max() < 5
    assert weather.temperature.std() > 0


@pytest.mark.parametrize(
    "field,value", [("zones", []), ("calendar", []), ("events", [None]), ("roads", []), ("metadata", None)]
)
def test_structural_errors_always_raise_value_error(field, value):
    world = create_world(rows=1, cols=1)
    world[field] = value
    with pytest.raises(ValueError):
        validate_world(world)


def test_properties_geometry_errors_raise_value_error():
    world = create_world(rows=1, cols=1)
    world["zones"]["features"][0]["properties"] = None
    with pytest.raises(ValueError):
        validate_world(world)
    world = create_world(rows=1, cols=1)
    world["zones"]["features"][0]["geometry"] = {"type": "Polygon", "coordinates": "broken"}
    with pytest.raises(ValueError):
        validate_world(world)
