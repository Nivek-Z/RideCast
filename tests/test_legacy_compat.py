"""Old saved models remain usable through the current forecast entry point."""

import json
import shutil

import numpy as np
import pytest
from test_features import make_dataset

from ridecast.legacy_ml import forecast as legacy_forecast
from ridecast.legacy_ml import train_model as train_legacy_model
from ridecast.ml import forecast

pytestmark = pytest.mark.ml


@pytest.fixture(scope="module")
def legacy_artifact(tmp_path_factory):
    root = tmp_path_factory.mktemp("legacy_compatibility")
    dataset = make_dataset(root / "dataset", days=5)
    model = root / "model"
    metadata = train_legacy_model(dataset, model, "m_1234567890abcdef", {
        "epochs": 1, "lookback": 12, "horizon": 2, "units": 4,
        "batch_size": 32, "seed": 42, "tune": False,
    })
    assert metadata["feature_schema"]["version"] == "origin-aware-v1"
    history = json.loads((model / "history.json").read_text())
    assert len(history["candidates"]) == 1
    assert len(history["epochs"]) == 1
    return dataset, model, metadata


def prediction_values(result):
    return np.asarray([[row[key] for key in ("predicted_demand", "lower", "upper")]
                       for row in result["rows"]], dtype=float)


def test_current_entry_point_preserves_legacy_predictions_and_bands(legacy_artifact):
    dataset, model, metadata = legacy_artifact
    expected = legacy_forecast(model, dataset)
    actual = forecast(model, dataset)
    assert actual["model_id"] == metadata["model_id"]
    assert actual["forecast_origin"] == metadata["training_observation_end"]
    assert actual["horizon"] == 2 and len(actual["rows"]) == 4
    assert [(r["timestamp"], r["zone_id"], r["horizon"]) for r in actual["rows"]] == [
        (r["timestamp"], r["zone_id"], r["horizon"]) for r in expected["rows"]]
    np.testing.assert_array_equal(prediction_values(actual), prediction_values(expected))
    assert actual["totals"] == expected["totals"]
    assert actual["interval"] == expected["interval"]
    assert np.isfinite(prediction_values(actual)).all()


def test_legacy_scenario_and_horizon_semantics_survive_routing(legacy_artifact):
    dataset, model, _ = legacy_artifact
    plain = forecast(model, dataset, horizon=1)
    multiplier = 1.7
    scaled = forecast(model, dataset, horizon=1, scenario={"demand_multiplier": multiplier})
    np.testing.assert_allclose(prediction_values(scaled), prediction_values(plain) * multiplier,
                               rtol=1e-6, atol=1e-6)
    scenario = {"demand_multiplier": multiplier, "rain_multiplier": 1.2}
    actual = forecast(model, dataset, horizon=1, scenario=scenario)
    expected = legacy_forecast(model, dataset, horizon=1, scenario=scenario)
    assert actual["scenario"] == scenario
    assert actual["horizon"] == 1 and len(actual["rows"]) == 2
    np.testing.assert_array_equal(prediction_values(actual), prediction_values(expected))
    with pytest.raises(ValueError, match="horizon"):
        forecast(model, dataset, horizon=3)
    with pytest.raises(ValueError, match="Unknown forecast"):
        forecast(model, dataset, scenario={"unobserved_future": 1})


@pytest.mark.parametrize(("change", "message"), [
    ("model_schema", "Unsupported model/feature schema"),
    ("feature_schema", "Unsupported model/feature schema"),
    ("dataset_schema", "Unsupported dataset/world schema"),
    ("zone_order", "zone order"),
    ("checksum", "checksum mismatch"),
])
def test_legacy_routing_preserves_schema_and_artifact_rejection(legacy_artifact, tmp_path, change, message):
    dataset, model, _ = legacy_artifact
    copied_dataset, copied_model = tmp_path / "dataset", tmp_path / "model"
    shutil.copytree(dataset, copied_dataset)
    shutil.copytree(model, copied_model)
    metadata_path = copied_model / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    if change == "model_schema":
        metadata["schema_version"] = "unsupported"
    elif change == "feature_schema":
        metadata["feature_schema"]["version"] = "unsupported"
    elif change == "dataset_schema":
        manifest_path = copied_dataset / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = "unsupported"
        manifest_path.write_text(json.dumps(manifest))
    elif change == "zone_order":
        metadata["feature_schema"]["zone_ids"].reverse()
    else:
        metadata["model_sha256"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match=message):
        forecast(copied_model, copied_dataset)
