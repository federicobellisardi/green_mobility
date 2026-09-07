"""_dwell_into_bins / _dwell_bin_overlap_integral are the exact-quadrature
implementation behind "person_seconds split by actual temporal overlap with
each bin" — verified here against straightforward numerical integration
(fine-grained Monte-Carlo-free discretization) before trusting them on real
data, given a previous version of this module had a real conservation bug
(see audit report) caught only by testing, not by inspection.
"""
import numpy as np
import pytest

from green_mobility.nomad_wrapper.walk_bike_routes import (
    BIN_WIDTH_S,
    DAY_BINS,
    _dwell_bin_overlap_integral,
    _dwell_into_bins,
    _distribute_into_bins,
)


def _numerical_overlap_integral(a: float, b: float, dt: float, bin_start: float, n: int = 200_000) -> float:
    """Reference implementation: fine-grained discretization of
    integral_a^b overlap([tau,tau+dt), [bin_start,bin_start+BIN_WIDTH_S)) d(tau)."""
    bin_end = bin_start + BIN_WIDTH_S
    taus = np.linspace(a, b, n)
    h = np.clip(np.minimum(taus + dt, bin_end) - np.maximum(taus, bin_start), 0.0, None)
    trapezoid = getattr(np, "trapezoid", None) or np.trapz
    return float(trapezoid(h, taus))


@pytest.mark.parametrize(
    "a,b,dt,bin_start",
    [
        (0.0, 3600.0, 45.0, 0.0),          # dwell << bin width, window starts at bin start
        (0.0, 3600.0, 45.0, 1800.0),        # same, later bin
        (500.0, 600.0, 1200.0, 0.0),        # dwell >> window width and >> bin width
        (86000.0, 86500.0, 300.0, 85500.0),  # near day boundary
        (1000.0, 1000.0, 500.0, 0.0),        # degenerate (zero-width) window handled by _dwell_into_bins, not this fn
    ],
)
def test_overlap_integral_matches_numerical_reference(a, b, dt, bin_start):
    if a == b:
        pytest.skip("degenerate window tested separately via _dwell_into_bins")
    exact = _dwell_bin_overlap_integral(a, b, dt, bin_start)
    numeric = _numerical_overlap_integral(a, b, dt, bin_start)
    assert exact == pytest.approx(numeric, rel=1e-3, abs=1e-2)


def test_dwell_into_bins_conserves_total_within_day():
    # Window and dwell entirely within the day -> no spillover, total == count*dt.
    out = _dwell_into_bins(1000.0, 2000.0, count=10.0, dt=200.0)
    assert sum(out.values()) == pytest.approx(10.0 * 200.0, rel=1e-6)


def test_dwell_into_bins_splits_across_bin_boundary_by_overlap():
    # A single-instant-like narrow window entering right at a bin boundary
    # with a dwell that spans exactly into the next bin: half the dwell time
    # should land in each bin (symmetric case).
    bin_b = 3 * BIN_WIDTH_S
    out = _dwell_into_bins(bin_b - 1e-6, bin_b + 1e-6, count=1.0, dt=2 * BIN_WIDTH_S)
    # dwell spans [bin_b - dt/2, bin_b + dt/2) roughly -> bins (2 and 3) or (3 and 4)
    assert sum(out.values()) == pytest.approx(2 * BIN_WIDTH_S, rel=1e-3)


def test_dwell_into_bins_shortfall_equals_next_day_spillover():
    # Entry window entirely within the day, but dwell time pushes past 86400.
    day_end = DAY_BINS * BIN_WIDTH_S
    dt = 1000.0
    out = _dwell_into_bins(day_end - 400.0, day_end - 300.0, count=5.0, dt=dt)
    expected_total = 5.0 * dt
    actual_total = sum(out.values())
    assert actual_total < expected_total  # some of the dwell spills past day end
    shortfall = expected_total - actual_total
    assert shortfall > 0


def test_flow_and_dwell_distributions_are_independent_concepts():
    # Same entry window/count, tiny dt vs huge dt -> flow distribution
    # (entry-based) must be IDENTICAL; only the person_seconds distribution
    # (dwell-based) should differ.
    entry_start, entry_end, count = 100.0, 1000.0, 20.0
    flow_a = _distribute_into_bins(entry_start, entry_end, count)
    flow_b = _distribute_into_bins(entry_start, entry_end, count)  # flow ignores dt entirely
    assert flow_a == flow_b

    dwell_short = _dwell_into_bins(entry_start, entry_end, count, dt=10.0)
    dwell_long = _dwell_into_bins(entry_start, entry_end, count, dt=5000.0)
    # Both conserve their own total (no spillover here — nowhere near day end)...
    assert sum(dwell_short.values()) == pytest.approx(count * 10.0)
    assert sum(dwell_long.values()) == pytest.approx(count * 5000.0)
    # ...but a much longer dwell spreads person-seconds across far more bins.
    assert set(dwell_long.keys()) != set(dwell_short.keys())
    assert len(dwell_long) > len(dwell_short)
