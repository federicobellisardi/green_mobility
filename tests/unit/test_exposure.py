import pandas as pd

from green_mobility.exposure.combine import compute_exposure


def test_compute_exposure_arithmetic():
    flows = pd.DataFrame(
        {
            "edge_id": [1, 1, 2],
            "hour": [8, 9, 8],
            "mode": ["walk", "car", "bike"],
            "count": [10, 100, 5],
        }
    )
    utci = pd.DataFrame(
        {
            "edge_id": [1, 1, 2],
            "hour": [8, 9, 8],
            "utci_c": [30.0, 35.0, 20.0],  # edge 2 below the 26C comfort threshold
        }
    )

    result = compute_exposure(flows, utci).set_index("edge_id")

    # edge 1: only the walk row (car excluded) — count=10 * excess(30-26=4) = 40
    assert result.loc[1, "flow_active"] == 10
    assert result.loc[1, "flow_total"] == 110  # walk + car
    assert result.loc[1, "exposure_score"] == 40.0  # regression: unchanged by the cold-side refactor
    assert result.loc[1, "cold_exposure_score"] == 0.0  # 30/35C, nowhere near the 9C cold threshold

    # edge 2: bike row, utci below threshold -> zero excess -> zero exposure
    assert result.loc[2, "flow_active"] == 5
    assert result.loc[2, "exposure_score"] == 0.0
    assert result.loc[2, "cold_exposure_score"] == 0.0  # 20C, still above the 9C cold threshold


def test_compute_exposure_cold_side():
    flows = pd.DataFrame({"edge_id": [1], "hour": [7], "mode": ["walk"], "count": [10]})
    utci = pd.DataFrame({"edge_id": [1], "hour": [7], "utci_c": [3.0]})  # below the 9C cold threshold

    result = compute_exposure(flows, utci).set_index("edge_id")
    assert result.loc[1, "exposure_score"] == 0.0
    assert result.loc[1, "cold_exposure_score"] == 10 * (9.0 - 3.0)  # count * deficit


def test_compute_exposure_missing_columns_raise():
    import pytest

    bad_flows = pd.DataFrame({"edge_id": [1], "hour": [8], "mode": ["walk"]})
    utci = pd.DataFrame({"edge_id": [1], "hour": [8], "utci_c": [30.0]})
    with pytest.raises(ValueError):
        compute_exposure(bad_flows, utci)
