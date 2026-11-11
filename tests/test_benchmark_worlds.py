import copy
import importlib.util
import math
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_worlds.py"
SPEC = importlib.util.spec_from_file_location("benchmark_worlds", MODULE_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_parser_defaults_to_world_statistics_and_accepts_training_options(tmp_path):
    defaults = benchmark.build_parser().parse_args([])
    assert defaults.train is False
    assert len(set(defaults.seeds)) == len(defaults.seeds) == 3
    args = benchmark.build_parser().parse_args([
        "--seeds", "11", "29", "--days", "14", "--epochs", "2", "--train", "--output", str(tmp_path),
    ])
    assert args.seeds == [11, 29]
    assert args.days == 14
    assert args.epochs == 2
    assert args.train is True
    assert args.output == tmp_path
    with pytest.raises(SystemExit):
        benchmark.build_parser().parse_args(["--days", "0"])


def test_summary_weights_worlds_equally_and_does_not_count_failed_runs():
    worlds = [{
        "seed": 7, "status": "succeeded", "world_hash": "world7", "statistics": {"demand_total": 100},
        "metrics": {"model": {"mae": 2.0, "rmse": 3.0, "wape": 25.0, "smape": 30.0},
                    "baseline": {"mae": 4.0, "rmse": 5.0, "wape": 50.0, "smape": 60.0},
                    "model_mae_improvement_percent": 50},
    }]
    other = copy.deepcopy(worlds[0])
    other.update(seed=42, world_hash="world42", statistics={"demand_total": 1000})
    other["metrics"]["model"]["mae"] = 6
    other["metrics"]["model_mae_improvement_percent"] = -50
    worlds += [other, {"seed": 99, "status": "failed", "error": "error"}]
    summary = benchmark.summarize(worlds)
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    assert summary["distinct_world_hashes"] == 2
    assert summary["model"]["mae"] == {"count": 2, "mean": 4, "std": 2, "min": 2, "max": 6}
    assert summary["demand_total"]["mean"] == 550
    assert summary["worlds_with_lower_model_mae"] == 1
    report = {"created_at": "2026-11-11T00:00:00Z", "worlds": worlds, "summary": summary,
              "settings": {"train": True, "seeds": [7, 42, 99], "days": 14, "rows": 2, "cols": 2,
                           "training_seed": 42, "epochs": 2}}
    markdown = benchmark.render_markdown(report)
    assert "零样本泛化" in markdown
    assert "25.00%" in markdown
    assert "2500.00%" not in markdown


def test_summary_handles_statistics_only_and_undefined_percentage_metrics():
    summary = benchmark.summarize([{
        "seed": 1, "status": "succeeded", "world_hash": "world1", "statistics": {"demand_total": 0},
        "metrics": None,
    }])
    assert summary["trained_worlds"] == 0
    assert summary["model"]["mae"] is None
    assert benchmark.distribution([None, math.nan, math.inf]) is None
    assert benchmark.distribution([3.0])["std"] == 0
