import datetime as dt

import pandas as pd
import pytest

from green_mobility.thermal.exposure_calendar import _load_edge_person_hours, dose_benefit_row


def test_load_edge_person_hours_aggregates_walk_and_bike_to_hourly(tmp_path):
    bins = pd.DataFrame({
        "mode": ["walk", "walk", "bike", "car"],
        "edge_id": [1, 1, 1, 1],
        "flow": [1.0, 1.0, 1.0, 1.0],
        "person_seconds": [900.0, 900.0, 1800.0, 999999.0],  # car must be excluded
        "travel_time_s": [10.0] * 4,
        "bin_start_s": [0, 900, 0, 0],       # first two bins are within hour 0 (0-3599s)
        "bin_end_s": [900, 1800, 900, 900],
    })
    path = tmp_path / "bins.parquet"
    bins.to_parquet(path)

    result = _load_edge_person_hours(path)
    assert set(result.columns) == {"edge_id", "hour", "n"}
    row = result[(result["edge_id"] == 1) & (result["hour"] == 0)]
    assert len(row) == 1
    # walk 900+900 + bike 1800 = 3600 person-seconds -> n = 1.0 person-hour
    assert row["n"].iloc[0] == pytest.approx(1.0)


def test_dose_benefit_row_joins_on_edge_and_hour_not_just_hour():
    # Two edges, only edge 1 has any active-mobility flow at hour 0.
    edge_hour = pd.DataFrame({
        "edge_id": [1, 2],
        "hour": [0, 0],
        "utci_ambient_c": [30.0, 30.0],
        "utci_shaded_c": [26.0, 26.0],
    })
    person_hours = pd.DataFrame({"edge_id": [1], "hour": [0], "n": [10.0]})

    row = dose_benefit_row("test_city", dt.date(2022, 7, 1), "evergreen", edge_hour, person_hours)
    # Only edge 1 contributes (inner join): heat_dose_ambient = 10*(30-26) = 40
    assert row["heat_dose_ambient"] == pytest.approx(40.0)
    assert row["heat_dose_shaded"] == pytest.approx(0.0)
    assert row["heat_benefit"] == pytest.approx(40.0)
    assert row["city"] == "test_city"
    assert row["canopy_type"] == "evergreen"
    assert "net_benefit_lambda_1.0" in row
