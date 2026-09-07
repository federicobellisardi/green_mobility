import numpy as np
import pandas as pd
import pytest

from green_mobility.thermal import utci as utci_mod


def test_utci_category_thresholds():
    values = [-50, -30, -20, -5, 5, 15, 28, 35, 40, 50]
    expected = [
        "extreme_cold", "very_strong_cold", "strong_cold", "moderate_cold",
        "slight_cold", "no_stress", "moderate_heat", "strong_heat",
        "very_strong_heat", "extreme_heat",
    ]
    assert utci_mod.utci_category(values).tolist() == expected


def test_apply_shade_full_shade_subtracts_full_reduction():
    result = utci_mod.apply_shade(tr_ambient_c=40.0, shade_fraction=1.0, mrt_reduction_full_shade_c=12.0)
    assert result == pytest.approx(28.0)


def test_apply_shade_no_shade_leaves_ambient_unchanged():
    result = utci_mod.apply_shade(tr_ambient_c=40.0, shade_fraction=0.0, mrt_reduction_full_shade_c=12.0)
    assert result == pytest.approx(40.0)


def test_edge_hour_utci_uses_shade_adjusted_tr(monkeypatch):
    # Stub out the pythermalcomfort dependency: return tr_c unchanged as
    # "utci" so we can assert the shade adjustment reached it, without
    # requiring pythermalcomfort to be installed for this unit test.
    monkeypatch.setattr(utci_mod, "utci_from_weather", lambda tdb_c, tr_c, v_ms, rh_pct: tr_c)

    weather = pd.DataFrame(
        {"hour": [12], "ta_c": [30.0], "rh_pct": [40.0], "wind_ms": [2.0], "tr_ambient_c": [50.0]}
    )
    shade = pd.DataFrame({"edge_id": [1, 2], "shade_fraction": [0.0, 1.0]})

    result = utci_mod.edge_hour_utci(weather, shade, mrt_reduction_full_shade_c=12.0).set_index(
        "edge_id"
    )
    assert result.loc[1, "utci_c"] == pytest.approx(50.0)   # no shade
    assert result.loc[2, "utci_c"] == pytest.approx(38.0)   # full shade: 50 - 12


def test_edge_hour_utci_missing_weather_column_raises():
    weather = pd.DataFrame({"hour": [12], "ta_c": [30.0]})
    shade = pd.DataFrame({"edge_id": [1], "shade_fraction": [0.5]})
    with pytest.raises(ValueError):
        utci_mod.edge_hour_utci(weather, shade)
