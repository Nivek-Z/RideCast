import json

import pandas as pd
import pytest

from ridecast.data import import_orders, write_dataset
from ridecast.simulation import create_world, simulate


def options(**kwargs):
    return {
        "observation_start": "2026-01-01T00:00:00+08:00",
        "observation_end": "2026-01-01T03:00:00+08:00",
        "coverage_attested": True,
        "target_semantics": "requests",
        **kwargs,
    }


def csv(tmp_path, rows):
    path = tmp_path / "orders.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def order(**kwargs):
    return {
        "order_id": "a",
        "requested_at": "2026-01-01T00:00:00+08:00",
        "pickup_zone_id": "z_000",
        "passenger_count": 2,
        "status": "completed",
        **kwargs,
    }


def test_csv_deduplicates_and_uses_full_coverage(tmp_path):
    world = create_world(rows=1, cols=2)
    path = csv(tmp_path, [order(), order(), order(order_id="b", status="cancelled", passenger_count=1)])
    manifest = import_orders(path, world, tmp_path / "dataset", "d_test", options())
    panel = pd.read_parquet(tmp_path / "dataset/panel.parquet")
    assert len(panel) == 6
    assert panel.demand.sum() == 2
    assert panel.passengers.sum() == 3
    assert panel.completed.sum() == panel.cancelled.sum() == 1
    assert manifest["quality"]["duplicate_rows"] == 1
    assert manifest["quality"]["zero_demand_buckets"] == 5
    assert json.loads((tmp_path / "dataset/manifest.json").read_text())["content_sha256"]


def test_coverage_attestation_mandatory(tmp_path):
    path = csv(tmp_path, [order()])
    with pytest.raises(ValueError, match="coverage_attested"):
        import_orders(
            path,
            create_world(rows=1, cols=1),
            tmp_path / "dataset",
            "d_test",
            options(coverage_attested=False),
        )


def test_conflicting_ids_reject_or_drop_all(tmp_path):
    path = csv(tmp_path, [order(), order(passenger_count=3), order(order_id="b")])
    world = create_world(rows=1, cols=1)
    with pytest.raises(ValueError, match="冲突"):
        import_orders(path, world, tmp_path / "bad", "d_test", options())
    manifest = import_orders(path, world, tmp_path / "good", "d_test", options(invalid_policy="drop"))
    assert manifest["order_count"] == 1
    assert manifest["quality"]["conflicting_id_rows"] == 2


def test_invalid_rows_reject_or_report_drop(tmp_path):
    path = csv(
        tmp_path,
        [
            order(),
            order(order_id="b", requested_at="2026-01-01T03:00:00+08:00"),
            order(order_id="c", passenger_count=0),
            order(order_id="d", pickup_zone_id="unknown"),
        ],
    )
    world = create_world(rows=1, cols=1)
    with pytest.raises(ValueError, match="超出观测"):
        import_orders(path, world, tmp_path / "bad", "d_test", options())
    manifest = import_orders(path, world, tmp_path / "good", "d_test", options(invalid_policy="drop"))
    assert manifest["order_count"] == 1
    assert manifest["quality"]["rejected_rows"] == 3
    assert len(manifest["quality"]["invalid_examples"]) == 3


def test_naive_timestamp_and_mapped_columns(tmp_path):
    path = csv(tmp_path, [{"id": "a", "time": "2026-01-01 01:10", "zone": "z_000", "people": 2}])
    manifest = import_orders(
        path,
        create_world(rows=1, cols=1),
        tmp_path / "dataset",
        "d_test",
        options(
            target_semantics="completed_trips",
            column_map={
                "order_id": "id",
                "requested_at": "time",
                "pickup_zone_id": "zone",
                "passenger_count": "people",
            },
        ),
    )
    panel = pd.read_parquet(tmp_path / "dataset/panel.parquet")
    assert panel.loc[panel.demand == 1, "timestamp"].iloc[0] == pd.Timestamp("2025-12-31T17:00:00Z")
    assert manifest["target_semantics"] == "completed_trips"


def test_coordinate_assignment_and_mismatch(tmp_path):
    world = create_world(rows=1, cols=2)
    row = order(pickup_lon=120.112, pickup_lat=30.206)
    del row["pickup_zone_id"]
    path = csv(tmp_path, [row])
    import_orders(path, world, tmp_path / "dataset", "d_test", options())
    orders = pd.read_parquet(tmp_path / "dataset/orders.parquet")
    assert orders.pickup_zone_id.iloc[0] == "z_000"  # deterministic shared edge policy
    row["pickup_zone_id"] = "z_001"
    row["pickup_lon"] = 120.104
    path = csv(tmp_path, [row])
    with pytest.raises(ValueError, match="不匹配"):
        import_orders(path, world, tmp_path / "bad", "d_test", options())


def test_context_validates_and_fills_only_missing_rows(tmp_path):
    world = create_world(rows=1, cols=1)
    path = csv(tmp_path, [order()])
    context = tmp_path / "context.csv"
    pd.DataFrame(
        [{"timestamp": "2026-01-01 00:00", "zone_id": "z_000", "precipitation": 7, "available_drivers": 4}]
    ).to_csv(context, index=False)
    manifest = import_orders(path, world, tmp_path / "dataset", "d_test", options(), context)
    panel = pd.read_parquet(tmp_path / "dataset/panel.parquet")
    assert panel.precipitation.tolist() == [7, 0, 0]
    assert manifest["quality"]["context_default_rows"] == 2
    pd.DataFrame([{"timestamp": "2026-01-01 00:10", "zone_id": "z_000", "precipitation": 7}]).to_csv(
        context, index=False
    )
    with pytest.raises(ValueError, match="对齐"):
        import_orders(path, world, tmp_path / "bad", "d_test", options(), context)


def test_bundle_cannot_overwrite_and_validates_counts(tmp_path):
    world = create_world(rows=1, cols=1)
    panel, orders, metadata = simulate(world, "2026-01-01", 1, 1)
    write_dataset(tmp_path / "dataset", world, panel, orders, metadata, "d_test")
    with pytest.raises(ValueError, match="覆盖"):
        write_dataset(tmp_path / "dataset", world, panel, orders, metadata, "d_test")
    broken = panel.copy()
    broken.loc[0, "demand"] += 1
    with pytest.raises(ValueError, match="completed"):
        write_dataset(tmp_path / "broken", world, broken, orders, metadata, "d_other")


def test_bundle_rejects_bucket_labels_that_only_match_global_total(tmp_path):
    world = create_world(rows=1, cols=1)
    panel, orders, metadata = simulate(world, "2026-01-01", 1, 1)
    low, high = panel.demand.idxmin(), panel.demand.idxmax()
    panel.loc[[low, high], ["demand", "passengers", "completed", "cancelled"]] = panel.loc[
        [high, low], ["demand", "passengers", "completed", "cancelled"]
    ].to_numpy()
    with pytest.raises(ValueError, match="逐区域逐桶"):
        write_dataset(tmp_path / "broken", world, panel, orders, metadata, "d_other")
