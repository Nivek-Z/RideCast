import json
import subprocess
import sys

import numpy as np
import pytest
from test_features import make_dataset

from ridecast.ml import forecast, train_model, validate_config


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("real_tensorflow")
    dataset = make_dataset(root / "dataset")
    model_dir = root / "model"
    events = []
    metadata = train_model(dataset, model_dir, "m_test", {
        "epochs": 2, "lookback": 12, "horizon": 3, "units": 8, "batch_size": 32, "tune": True,
    }, lambda stage, fraction, message: events.append((stage, fraction, message)))
    return dataset, model_dir, metadata, events


@pytest.mark.ml
def test_real_training_serialization_and_new_process_forecast(trained):
    dataset, model_dir, metadata, events = trained
    assert (model_dir / "model.keras").stat().st_size > 1000
    assert len(metadata["candidates"]) == 2
    assert metadata["dependencies"]["tensorflow"].startswith("2.")
    assert metadata["split"]["train"]["target_end_exclusive"] == metadata["split"]["validation"]["target_start"]
    assert metadata["split"]["validation"]["target_end_exclusive"] == metadata["split"]["test"]["target_start"]
    assert metadata["metrics"]["model"]["mae"] >= 0
    live_history = json.loads((model_dir / "live_history.json").read_text())
    final_history = json.loads((model_dir / "history.json").read_text())
    assert live_history["status"] == "ready"
    assert live_history["epochs"] == final_history["epochs"]
    assert live_history["candidates"] == final_history["candidates"]
    assert all({"epoch", "loss", "val_loss", "duration_seconds"} <= set(row) for row in live_history["epochs"])
    assert events[-1][1] == 1
    assert all(b[1] >= a[1] for a, b in zip(events, events[1:]))
    result = forecast(model_dir, dataset)
    assert len(result["rows"]) == 6
    assert result["forecast_origin"] == metadata["training_observation_end"]
    assert all(0 <= row["lower"] <= row["predicted_demand"] <= row["upper"] for row in result["rows"])
    script = "from ridecast.ml import forecast; import json; from pathlib import Path; import sys; print(json.dumps(forecast(Path(sys.argv[1]), Path(sys.argv[2]))))"
    process = subprocess.run([sys.executable, "-c", script, str(model_dir), str(dataset)], capture_output=True, text=True, check=True)
    reloaded = json.loads(process.stdout.strip().splitlines()[-1])
    np.testing.assert_allclose([r["predicted_demand"] for r in result["rows"]], [r["predicted_demand"] for r in reloaded["rows"]], rtol=1e-5)


@pytest.mark.ml
def test_forecast_compatibility_and_explicit_scenario(trained):
    dataset, model_dir, metadata, _ = trained
    plain = forecast(model_dir, dataset, horizon=1)
    scaled = forecast(model_dir, dataset, horizon=1, scenario={"demand_multiplier": 2})
    np.testing.assert_allclose([r["predicted_demand"] * 2 for r in plain["rows"]], [r["predicted_demand"] for r in scaled["rows"]])
    with pytest.raises(ValueError, match="horizon"):
        forecast(model_dir, dataset, horizon=4)
    with pytest.raises(ValueError, match="Unknown forecast"):
        forecast(model_dir, dataset, scenario={"unobserved_future": 1})
    world_path = dataset / "world.json"
    original = world_path.read_text()
    changed = json.loads(original)
    changed["zones"]["features"][0]["properties"]["population"] += 1
    world_path.write_text(json.dumps(changed))
    try:
        with pytest.raises(ValueError, match="fingerprint"):
            forecast(model_dir, dataset)
    finally:
        world_path.write_text(original)


def test_training_config_rejects_resource_and_schema_errors():
    for config in ({"lookback": 10000}, {"epochs": True}, {"horizon": 0}, {"learning_rate": float("nan")}, {"hidden_secret": 1}):
        with pytest.raises(ValueError):
            validate_config(config)
