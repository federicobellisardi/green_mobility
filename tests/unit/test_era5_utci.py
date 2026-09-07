import datetime as dt

import pandas as pd
import pytest

from green_mobility.thermal.era5_utci import (
    DEFAULT_UTCI_REDUCTION_FULL_SHADE_C,
    daylight_fraction_by_hour,
    edge_hour_utci_from_era5,
    solar_elevation_deg,
)

PALMA_LAT, PALMA_LON = 39.5696, 2.6502


def test_solar_elevation_negative_at_local_midnight():
    # Local solar midnight in Palma (UTC+~10min offset from longitude) is
    # close to UTC hour 0 -- sun must be well below the horizon.
    elev = solar_elevation_deg(PALMA_LAT, PALMA_LON, dt.date(2022, 2, 8), 0.0)
    assert elev < 0


def test_solar_elevation_positive_near_local_solar_noon():
    # Local solar noon for lon=2.65E is close to UTC 12:00 - (2.65/15)h ~= 11:50 UTC.
    elev = solar_elevation_deg(PALMA_LAT, PALMA_LON, dt.date(2022, 2, 8), 12.0)
    assert elev > 0


def test_summer_days_are_longer_than_winter_days():
    winter = daylight_fraction_by_hour(PALMA_LAT, PALMA_LON, dt.date(2022, 2, 8))
    summer = daylight_fraction_by_hour(PALMA_LAT, PALMA_LON, dt.date(2022, 7, 5))
    winter_hours = (winter["daylight_fraction"] > 0).sum()
    summer_hours = (summer["daylight_fraction"] > 0).sum()
    assert summer_hours > winter_hours


def test_daylight_fraction_bounded_zero_to_one():
    df = daylight_fraction_by_hour(PALMA_LAT, PALMA_LON, dt.date(2022, 7, 5))
    assert (df["daylight_fraction"] >= 0).all()
    assert (df["daylight_fraction"] <= 1).all()


def test_edge_hour_utci_shade_reduces_daytime_not_night(monkeypatch):
    import green_mobility.thermal.era5_utci as era5_utci_mod

    # Stub the real-data loader: flat 30C ambient UTCI at every hour, so any
    # variation we see comes purely from the daylight-gated shade term.
    def fake_ambient(nc_path, lat, lon, on_date=None):
        return pd.DataFrame({"hour": range(24), "utci_ambient_c": [30.0] * 24})

    monkeypatch.setattr(era5_utci_mod, "load_ambient_utci", fake_ambient)

    edge_shade = pd.DataFrame({"edge_id": [1, 2], "shade_fraction": [0.0, 1.0]})
    result = edge_hour_utci_from_era5(
        "unused.nc", PALMA_LAT, PALMA_LON, dt.date(2022, 7, 5), edge_shade
    )

    # Edge 1 (no shade) must equal ambient at every hour.
    edge1 = result[result["edge_id"] == 1].set_index("hour")
    assert (edge1["utci_c"] - 30.0).abs().max() < 1e-6

    # Edge 2 (full shade) must equal ambient at night (hour 0) ...
    edge2 = result[result["edge_id"] == 2].set_index("hour")
    assert edge2.loc[0, "utci_c"] == pytest.approx(30.0)
    # ...but be reduced at midday (some daylight fraction > 0).
    assert edge2.loc[12, "utci_c"] < 30.0
    assert edge2.loc[12, "utci_c"] >= 30.0 - DEFAULT_UTCI_REDUCTION_FULL_SHADE_C - 1e-6


def test_edge_hour_utci_from_era5_against_real_downloaded_data():
    pytest.importorskip("xarray")
    from pathlib import Path

    nc_path = Path("data/climate_raw/era5/utci_palma_de_mallorca_2022-02-08.nc")
    if not nc_path.exists():
        pytest.skip("real UTCI data not downloaded in this environment")

    edge_shade = pd.DataFrame({"edge_id": [1, 2], "shade_fraction": [0.0, 0.8]})
    result = edge_hour_utci_from_era5(
        nc_path, PALMA_LAT, PALMA_LON, dt.date(2022, 2, 8), edge_shade
    )
    assert len(result) == 2 * 24
    assert result["utci_ambient_c"].between(-20, 45).all()  # sane physical range
    # shaded edge must never exceed the ambient value (shade only ever cools)
    assert (result["utci_c"] <= result["utci_ambient_c"] + 1e-9).all()
