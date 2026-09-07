"""End-to-end time-conservation check for nomad_wrapper.walk_bike_routes,
against a real (tiny, synthetic) NOMAD graph — not just the pure-Python unit
tests of the underlying bin-splitting math (tests/unit/test_walk_bike_*.py).

Requires NOMAD built with Python bindings; skips otherwise (matches
tests/integration/test_nomad_smoke.py's pattern).
"""
from __future__ import annotations

import datetime as dt

import pytest

from green_mobility.config import CityConfig, ScenarioSpec
from green_mobility.nomad_wrapper.paths import NomadNotBuiltError, require_nomad_python


@pytest.fixture
def core():
    # Fixture-based skip (matches test_nomad_smoke.py), not module-level
    # pytest.importorskip: nomad/__init__.py deliberately re-raises
    # ImportError with a custom message (see paths.py docstring), and in
    # this environment that confuses importorskip's collection-time skip
    # detection into a hard collection error instead of a clean skip.
    try:
        require_nomad_python()
    except NomadNotBuiltError as exc:
        pytest.skip(str(exc))
    import nomad._nomad_core as _core

    return _core


@pytest.fixture
def tiny_city(core, tmp_path):
    edges = [
        (0, 1, 300.0, 10.0, 600.0, 10),
        (1, 2, 300.0, 10.0, 600.0, 10),
        (2, 3, 300.0, 10.0, 600.0, 10),
        (0, 2, 500.0, 25.0, 2000.0, 0),
        (3, 0, 900.0, 10.0, 600.0, 10),
    ]
    graph = core.build_test_graph(edges, 4)
    city_dir = tmp_path / "tinytown"
    (city_dir / "green_mobility").mkdir(parents=True)
    graph.save(str(city_dir / "graph.bin"))

    od_csv = city_dir / "od_tinytown_weekday.csv"
    # Each mode gets a normal row plus a deliberately-late (spillover-
    # inducing) row, so the spillover assertions below are meaningful for
    # BOTH modes rather than only one of them.
    od_csv.write_text(
        "origin_node,dest_node,count,mode,depart_mean_s,depart_std_s\n"
        "0,3,10,walk,0,10\n"
        "0,3,5,walk,86390,86400\n"  # departs right at day end -> guaranteed spillover
        "0,3,8,bike,0,10\n"
        "0,3,3,bike,86390,86400\n"  # same, for bike
    )

    city = CityConfig(
        name="Tinytown", slug="tinytown", country="ES",
        geofabrik_url="", geofabrik_raw_filename="", mitma_months=("2022-02",),
        demand_occupancy_factor=1.2, demand_noise_sigma=0.08,
        scenarios={"s": ScenarioSpec(
            key="s", date=dt.date(2022, 2, 8),
            start_time=dt.time(0, 0, 0), end_time=dt.time(23, 59, 59),
            modes=("walk", "bike"), router="astar",
        )},
    )
    import green_mobility.config as cfgmod
    cfgmod.DATA_DIR = tmp_path
    return city


def test_person_seconds_conservation_walk_and_bike(tiny_city):
    from green_mobility.nomad_wrapper.walk_bike_routes import extract_walk_bike_bins

    city = tiny_city
    scenario = city.scenarios["s"]

    for mode in ("walk", "bike"):
        bins_df, diag = extract_walk_bike_bins(city, scenario, mode)

        # Core identity requested by the audit: sum(person_seconds over
        # edges/bins) + spillover (excluded, after-window portion) must
        # equal sum(count x total route time) for every routed row.
        in_window = diag["total_person_seconds_in_window"]
        spillover = diag["spillover_person_seconds_next_day"]
        expected = diag["total_person_seconds_expected"]
        assert in_window + spillover == pytest.approx(expected, rel=1e-6)

        # The row departing at [86390, 86400) must show up as spillover,
        # since even its very first edge's dwell time (300m at capped speed)
        # already pushes well past day end.
        assert diag["spillover_rows_affected"] > 0
        assert diag["spillover_agents_affected"] > 0

        # flow conservation (independent identity, from the earlier fix):
        # every routed agent enters its route's first edge exactly once.
        assert diag["first_edge_flow_total"] == pytest.approx(diag["routed_agents"])
