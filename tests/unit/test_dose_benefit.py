import pandas as pd
import pytest

from green_mobility.thermal.dose_benefit import (
    DEFAULT_THETA_COLD_C,
    DEFAULT_THETA_HEAT_C,
    cold_dose,
    compute_dose_benefit,
    edge_heat_cold_dose,
    heat_dose,
    net_benefit_sweep,
)


def test_heat_dose_zero_below_threshold():
    df = pd.DataFrame({"n": [10, 20], "utci_c": [20.0, 25.0]})
    assert heat_dose(df, theta_heat_c=26.0) == 0.0


def test_heat_dose_accumulates_excess_weighted_by_n():
    df = pd.DataFrame({"n": [10, 20], "utci_c": [30.0, 26.0]})
    # edge 1: 10 * (30-26) * 1h = 40; edge 2: 20 * (26-26) * 1h = 0
    assert heat_dose(df, theta_heat_c=26.0) == pytest.approx(40.0)


def test_cold_dose_accumulates_deficit_weighted_by_n():
    df = pd.DataFrame({"n": [5, 8], "utci_c": [-1.0, 9.0]})
    # edge 1: 5 * (9-(-1)) * 1h = 50; edge 2: 8 * (9-9) = 0
    assert cold_dose(df, theta_cold_c=9.0) == pytest.approx(50.0)


def test_dt_hours_scales_dose_linearly():
    df = pd.DataFrame({"n": [10], "utci_c": [30.0]})
    dose_1h = heat_dose(df, theta_heat_c=26.0, dt_hours=1.0)
    dose_15min = heat_dose(df, theta_heat_c=26.0, dt_hours=0.25)
    assert dose_15min == pytest.approx(dose_1h * 0.25)


def test_compute_dose_benefit_shade_only_helps_heat_not_cold():
    # Shade cools daytime (ambient 32 -> shaded 26, right at heat threshold)
    # but does nothing at night (ambient == shaded, both below cold threshold).
    df = pd.DataFrame({
        "n": [100, 100],
        "utci_ambient_c": [32.0, 5.0],
        "utci_c": [26.0, 5.0],
    })
    result = compute_dose_benefit(df, theta_heat_c=26.0, theta_cold_c=9.0)
    assert result.heat_dose_ambient == pytest.approx(100 * (32 - 26))
    assert result.heat_dose_shaded == pytest.approx(0.0)
    assert result.heat_benefit == pytest.approx(600.0)
    # cold dose identical ambient vs shaded at night -> zero cost
    assert result.cold_dose_ambient == pytest.approx(100 * (9 - 5))
    assert result.cold_dose_shaded == pytest.approx(100 * (9 - 5))
    assert result.cold_cost == pytest.approx(0.0)


def test_compute_dose_benefit_shade_can_cost_something_in_cold_conditions():
    # A single hour where shade pushes an already-cold edge further below
    # the cold threshold (e.g. a leaf-on evergreen canopy blocking winter sun).
    df = pd.DataFrame({
        "n": [50],
        "utci_ambient_c": [7.0],
        "utci_c": [4.0],
    })
    result = compute_dose_benefit(df, theta_heat_c=26.0, theta_cold_c=9.0)
    assert result.heat_benefit == pytest.approx(0.0)
    assert result.cold_cost == pytest.approx(50 * (9 - 4) - 50 * (9 - 7))  # = 150


def test_net_benefit_sweep_reports_range_not_single_value():
    df = pd.DataFrame({
        "n": [100, 50],
        "utci_ambient_c": [32.0, 7.0],
        "utci_c": [26.0, 4.0],
    })
    result = compute_dose_benefit(df)
    sweep = net_benefit_sweep(result, lambdas=(0.0, 1.0, 2.0))
    assert len(sweep) == 3
    assert (sweep["heat_benefit"] == result.heat_benefit).all()
    assert (sweep["cold_cost"] == result.cold_cost).all()
    # net_benefit must strictly decrease as lambda grows when cold_cost > 0
    assert sweep["net_benefit"].is_monotonic_decreasing
    assert sweep.loc[sweep["lambda"] == 0.0, "net_benefit"].iloc[0] == pytest.approx(result.heat_benefit)


def test_edge_heat_cold_dose_matches_ungrouped_heat_dose_cold_dose():
    # Per-edge values, summed, must equal what the whole-frame heat_dose()/
    # cold_dose() produce on the same rows -- cross-checks the vectorized
    # groupby path against the existing, already-tested scalar functions.
    df = pd.DataFrame({
        "edge_id": [1, 1, 2, 2],
        "n": [10, 20, 5, 8],
        "utci_c": [30.0, 26.0, -1.0, 9.0],
    })
    per_edge = edge_heat_cold_dose(df, theta_heat_c=26.0, theta_cold_c=9.0)
    assert per_edge["heat_dose"].sum() == pytest.approx(heat_dose(df, theta_heat_c=26.0))
    assert per_edge["cold_dose"].sum() == pytest.approx(cold_dose(df, theta_cold_c=9.0))
    # edge 1 is all-heat (30, 26 both >= threshold-adjacent), edge 2 is all-cold
    row1 = per_edge.set_index("edge_id").loc[1]
    row2 = per_edge.set_index("edge_id").loc[2]
    assert row1["heat_dose"] == pytest.approx(40.0)
    assert row1["cold_dose"] == pytest.approx(0.0)
    assert row2["heat_dose"] == pytest.approx(0.0)
    assert row2["cold_dose"] == pytest.approx(50.0)


def test_missing_column_raises_clear_error():
    df = pd.DataFrame({"n": [1], "utci_c": [10.0]})  # missing utci_ambient_c
    with pytest.raises(ValueError):
        compute_dose_benefit(df)
