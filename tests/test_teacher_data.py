import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluate_teacher_data", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_teacher_data.py"
)
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


def test_spatial_partition_is_target_independent_and_keeps_blocks_together():
    features = []
    for y in range(8):
        for x in range(8):
            features.append({"properties": {"TripCount": (x + y) * 100}, "geometry": {
                "type": "Polygon", "coordinates": [[[x, y], [x + .5, y], [x + .5, y + .5],
                                                       [x, y + .5], [x, y]]]},
            })
    validation, blocks = experiment.spatial_split(features)
    changed = copy.deepcopy(features)
    for feature in changed:
        feature["properties"]["TripCount"] = 10**12
    other_validation, other_blocks = experiment.spatial_split(changed)
    np.testing.assert_array_equal(validation, other_validation)
    np.testing.assert_array_equal(blocks, other_blocks)
    assert not set(blocks[validation]) & set(blocks[~validation])
    assert validation.sum() == (~validation).sum() == 32


def test_share_metrics_ignore_unknown_exposure_and_have_known_extremes():
    perfect = experiment.share_metrics([1, 3], [100, 300])
    assert perfect["total_variation_percent"] == 0
    uniform = experiment.share_metrics([1, 3], [1, 1])
    assert uniform["total_variation_percent"] == pytest.approx(25)
    assert uniform["relative_volume_mae"] == pytest.approx(.5)
    assert uniform["spearman"] is None
    with pytest.raises(ValueError):
        experiment.share_metrics([0, 0], [1, 1])


def test_feature_units_cancel_without_using_trip_counts_or_target_city_fitted_scalers():
    x = experiment.design([100, 200, 300], [2, 4, 8], [50, 200, 600])
    same = experiment.design([100, 200, 300], [2000, 4000, 8000], [500, 2000, 6000])
    np.testing.assert_allclose(x, same)
    assert set(experiment.PROTOCOL["excluded_real_inputs"]) >= {"TripCount", "FALSE", "TRUE", "pct_share"}
