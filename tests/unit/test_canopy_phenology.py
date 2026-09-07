import datetime as dt

import pytest

from green_mobility.thermal.canopy_phenology import (
    canopy_shade_multiplier,
    deciduous_leaf_fraction,
)


def test_deciduous_leaf_fraction_zero_in_deep_winter():
    assert deciduous_leaf_fraction(1) == pytest.approx(0.0)   # Jan 1
    assert deciduous_leaf_fraction(365) == pytest.approx(0.0)  # Dec 31


def test_deciduous_leaf_fraction_full_in_summer():
    assert deciduous_leaf_fraction(200) == pytest.approx(1.0)  # mid-July


def test_deciduous_leaf_fraction_ramps_at_leaf_on():
    # Exactly at leaf_on_doy, fraction should be ~0.5 (midpoint of ramp)
    frac = deciduous_leaf_fraction(100, leaf_on_doy=100, ramp_days=20)
    assert frac == pytest.approx(0.5, abs=0.05)
    # before the ramp starts -> 0
    assert deciduous_leaf_fraction(85, leaf_on_doy=100, ramp_days=20) == pytest.approx(0.0)
    # after the ramp completes -> 1
    assert deciduous_leaf_fraction(115, leaf_on_doy=100, ramp_days=20) == pytest.approx(1.0)


def test_deciduous_leaf_fraction_ramps_at_leaf_off():
    frac = deciduous_leaf_fraction(305, leaf_off_doy=305, ramp_days=20)
    assert frac == pytest.approx(0.5, abs=0.05)


def test_canopy_shade_multiplier_evergreen_always_one():
    for month in (1, 4, 7, 12):
        d = dt.date(2022, month, 15)
        assert canopy_shade_multiplier(d, "evergreen") == 1.0
        assert canopy_shade_multiplier(d, "no_distinction") == 1.0


def test_canopy_shade_multiplier_deciduous_zero_in_winter_full_in_summer():
    assert canopy_shade_multiplier(dt.date(2022, 1, 15), "deciduous") == pytest.approx(0.0)
    assert canopy_shade_multiplier(dt.date(2022, 7, 15), "deciduous") == pytest.approx(1.0)


def test_canopy_shade_multiplier_rejects_unknown_type():
    with pytest.raises(ValueError):
        canopy_shade_multiplier(dt.date(2022, 1, 1), "bogus")
