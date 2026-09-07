import pytest

from green_mobility.nomad_wrapper.walk_bike_routes import BIN_WIDTH_S, DAY_BINS, DAY_END_S, _distribute_into_bins


def test_distribute_conserves_total_count_within_one_bin():
    out = _distribute_into_bins(100.0, 200.0, 42.0)
    assert sum(out.values()) == pytest.approx(42.0)
    assert set(out.keys()) == {0}


def test_distribute_conserves_total_count_across_multiple_bins():
    # window spans bins 0,1,2 (0-900, 900-1800, 1800-2700)
    out = _distribute_into_bins(500.0, 2000.0, 30.0)
    assert sum(out.values()) == pytest.approx(30.0)
    assert set(out.keys()) == {0, 1, 2}


def test_distribute_splits_proportionally_to_overlap():
    # window [0, 1800) spans exactly bins 0 and 1 with equal overlap -> 50/50
    out = _distribute_into_bins(0.0, 1800.0, 100.0)
    assert out[0] == pytest.approx(50.0)
    assert out[1] == pytest.approx(50.0)


def test_distribute_zero_width_window_goes_entirely_to_one_bin():
    out = _distribute_into_bins(1000.0, 1000.0, 7.0)
    assert sum(out.values()) == pytest.approx(7.0)
    assert len(out) == 1


def test_distribute_does_not_fold_past_end_of_day_into_last_bin():
    # Previously (bug, see audit report) any portion of a window past
    # DAY_END_S was concentrated into the last bin, producing an artificial
    # ~4x flow spike at 23:45-24:00 in real Palma walk/bike data. Fixed: the
    # in-range portion is returned, the rest is NOT returned at all — the
    # caller (extract_walk_bike_bins) is responsible for tracking it as
    # 'next day' spillover explicitly, never silently dropped or
    # misattributed to a boundary bin.
    last_bin_start = (DAY_BINS - 1) * BIN_WIDTH_S
    start, end, count = last_bin_start + 500.0, last_bin_start + 2000.0, 10.0
    out = _distribute_into_bins(start, end, count)
    assert set(out.keys()) == {DAY_BINS - 1}
    in_range_width = DAY_END_S - start
    total_width = end - start
    expected_in_range = count * in_range_width / total_width
    assert out[DAY_BINS - 1] == pytest.approx(expected_in_range)
    assert sum(out.values()) < count  # confirms the shortfall exists (not silently made whole)


def test_distribute_window_entirely_past_day_end_returns_empty():
    out = _distribute_into_bins(DAY_END_S + 100.0, DAY_END_S + 500.0, 5.0)
    assert out == {}


@pytest.mark.parametrize(
    "start,end,count",
    [(0.0, 3600.0, 55.0), (12345.0, 12400.0, 3.0)],
)
def test_distribute_conserves_total_when_fully_within_day(start, end, count):
    out = _distribute_into_bins(start, end, count)
    assert sum(out.values()) == pytest.approx(count)


from green_mobility.nomad_wrapper.walk_bike_routes import (
    ConservationError,
    _dwell_into_bins,
    assert_conservation,
)


def test_dwell_into_bins_conserves_total_when_fully_in_day():
    # 50 agents enter uniformly over a 600s window, each dwelling 1000s
    # (spanning multiple bins) -- total person-seconds must be conserved.
    out = _dwell_into_bins(10_000.0, 10_600.0, 50.0, 1000.0)
    assert sum(out.values()) == pytest.approx(50.0 * 1000.0, rel=1e-6)


def test_dwell_into_bins_degenerate_departure_spans_bins():
    # everyone enters at exactly t=800, dwells 1000s -> occupies bin 0
    # ([0,900)) for 100s and bin 1 ([900,1800)) for 900s.
    out = _dwell_into_bins(800.0, 800.0, 10.0, 1000.0)
    assert out[0] == pytest.approx(10.0 * 100.0)
    assert out[1] == pytest.approx(10.0 * 900.0)
    assert sum(out.values()) == pytest.approx(10.0 * 1000.0)


def test_dwell_into_bins_shortfall_when_overflowing_past_day_end():
    dt = 1000.0
    out = _dwell_into_bins(DAY_END_S - 500.0, DAY_END_S - 500.0, 20.0, dt)
    total = sum(out.values())
    assert total < 20.0 * dt  # part of the dwell spills past DAY_END_S
    assert total == pytest.approx(20.0 * 500.0, rel=1e-6)  # only the in-day portion


def test_assert_conservation_passes_on_consistent_diagnostics():
    diag = {
        "od_agents": 100, "routed_agents": 95, "unreachable_agents": 5,
        "first_edge_flow_total": 95.0,
        "total_person_seconds_in_window": 900.0,
        "total_person_seconds_expected": 1000.0,
        "spillover_person_seconds_next_day": 100.0,
    }
    assert_conservation(diag)  # must not raise


@pytest.mark.parametrize(
    "broken_field,broken_value",
    [
        ("routed_agents", 50),
        ("first_edge_flow_total", 10.0),
        ("total_person_seconds_in_window", 0.0),
    ],
)
def test_assert_conservation_raises_on_broken_diagnostics(broken_field, broken_value):
    diag = {
        "od_agents": 100, "routed_agents": 95, "unreachable_agents": 5,
        "first_edge_flow_total": 95.0,
        "total_person_seconds_in_window": 900.0,
        "total_person_seconds_expected": 1000.0,
        "spillover_person_seconds_next_day": 100.0,
    }
    diag[broken_field] = broken_value
    with pytest.raises(ConservationError):
        assert_conservation(diag)
