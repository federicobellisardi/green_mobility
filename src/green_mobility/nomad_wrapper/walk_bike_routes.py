"""Per-edge, per-15-min-bin walk/bike flow, vehicle/person-seconds and travel
time — computed WITHOUT running Simulation.run() at all.

Why this is correct (not a shortcut that loses accuracy): walk/bike agents
never reroute — AStarRouter/CHRouter's dynamic reroute logic only fires on
congestion thresholds, and QueueTrafficModel (the only thing that ever
updates congestion state) is gated to AgentMode::Car
(external/nomad/src/core/simulation.cpp:263,291). So a walk/bike agent's
pre-computed route (RoutingRequest -> AStarRouter.route(), bound in
bindings/pynomad.cpp on feature/python-multimodal-bindings) IS its entire
real trip, and each edge's travel time is the deterministic
Graph.mode_free_flow_time (also bound there) — not an estimate, since there
is no congestion model for these modes to estimate around.

Two DISTINCT quantities are computed per (edge, bin), and must not be
confused:
  flow            — expected number of agents ENTERING the edge during the
                    bin (throughput). Computed from the entry-time window's
                    overlap with each bin.
  person_seconds  — expected accumulated dwell-time (occupancy) contributed
                    to the bin. An edge traversal that starts near a bin
                    boundary can spend part of its dwell time in one bin and
                    part in the next — that split is exact (closed-form
                    integral of the entry-time-window x dwell-duration
                    overlap with each bin; see _dwell_bin_overlap_integral),
                    not attributed wholesale to the entry bin.
Both are analytically exact expectations over each OD row's uniform
departure-time sampling (DepartureSampler::uniform, od_matrix.cpp) — not
Monte Carlo samples, so re-running this module is deterministic.

Day-boundary handling: NOMAD's own OD rows only ever schedule DEPARTURES
within one day (build_od.py: depart_mean_s/depart_std_s are hour-bucket
seconds-since-midnight, always < 86400). But a walk/bike trip's ARRIVAL at
a downstream edge can fall on the *next* day if departure is late and the
route is long (walking speed makes this common for late-evening departures
with long routes). Those out-of-window contributions are EXCLUDED from the
returned bin tables (not folded into the last bin — see the bug this fixed,
documented in the audit report) and reported separately as
'spillover_next_day_*' in the diagnostics, so nothing is silently lost or
misattributed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from green_mobility.config import CityConfig, ScenarioSpec
from green_mobility.nomad_wrapper.paths import require_nomad_python

BIN_WIDTH_S = 900.0
DAY_BINS = 96  # 86400 / 900
DAY_END_S = DAY_BINS * BIN_WIDTH_S

# Plausibility thresholds for per-trip diagnostics (documented assumptions,
# not measured facts) — see extract_trip_summary.
MAX_PLAUSIBLE_SPEED_MS = {"walk": 2.5, "bike": 9.0}  # 9 km/h walk, 32.4 km/h bike
MAX_PLAUSIBLE_DURATION_S = 4 * 3600.0  # 4 hours


def _distribute_into_bins(start: float, end: float, count: float) -> dict[int, float]:
    """count agents/persons depart uniformly in [start, end) -> {bin_index:
    expected_count}, for bins fully within the simulated day [0, DAY_END_S).
    Does NOT clamp/fold out-of-range portions into bin 0 or the last bin —
    sum(result.values()) may be < count if [start, end) extends outside
    [0, DAY_END_S); the caller is responsible for tracking that shortfall
    explicitly (see extract_walk_bike_bins's spillover diagnostics) rather
    than silently dropping or misattributing it to a boundary bin."""
    if end <= start:
        b = int(start // BIN_WIDTH_S)
        return {b: count} if 0 <= b < DAY_BINS else {}
    width = end - start
    lo, hi = max(start, 0.0), min(end, DAY_END_S)
    out: dict[int, float] = {}
    if hi > lo:
        b0 = int(lo // BIN_WIDTH_S)
        b1 = min(DAY_BINS - 1, int((hi - 1e-9) // BIN_WIDTH_S))
        for b in range(b0, b1 + 1):
            bin_start = b * BIN_WIDTH_S
            bin_end = bin_start + BIN_WIDTH_S
            overlap = max(0.0, min(hi, bin_end) - max(lo, bin_start))
            if overlap > 0:
                out[b] = out.get(b, 0.0) + count * overlap / width
    return out


def _dwell_bin_overlap_integral(a: float, b: float, dt: float, bin_start: float) -> float:
    """Exact closed-form (via exact trapezoidal quadrature — see note below)
    of:  count/W * integral_{tau=a}^{b} overlap([tau, tau+dt), [bin_start, bin_start+BIN_WIDTH_S)) d(tau)
    with count/W folded in by the caller (this returns the "per unit count/W" integral).

    h(tau) := overlap([tau, tau+dt), bin) is piecewise-LINEAR in tau with
    breakpoints at {bin_start-dt, bin_start, bin_end-dt, bin_end}. Trapezoidal
    quadrature between two consecutive points of a piecewise-linear function
    is mathematically EXACT (not an approximation) as long as every
    breakpoint in [a, b] is included as a quadrature node — which is what
    this does. This avoids hand-deriving (and risking an error in) the
    piecewise quadratic antiderivative directly.
    """
    bin_end = bin_start + BIN_WIDTH_S

    def h(tau: float) -> float:
        return max(0.0, min(tau + dt, bin_end) - max(tau, bin_start))

    nodes = sorted({a, b, bin_start - dt, bin_start, bin_end - dt, bin_end})
    pts = [p for p in nodes if a <= p <= b]
    if not pts or pts[0] > a:
        pts.insert(0, a)
    if pts[-1] < b:
        pts.append(b)

    total = 0.0
    for i in range(len(pts) - 1):
        x0, x1 = pts[i], pts[i + 1]
        if x1 <= x0:
            continue
        total += (h(x0) + h(x1)) / 2.0 * (x1 - x0)
    return total


def _dwell_into_bins(start: float, end: float, count: float, dt: float) -> dict[int, float]:
    """Person/vehicle-seconds contributed to each in-day bin by `count`
    agents entering uniformly in [start, end) and dwelling dt seconds each.
    Like _distribute_into_bins, does not fold out-of-range contributions —
    sum(result.values()) may be < count*dt; the shortfall is the caller's
    'next day' spillover to track explicitly."""
    if end <= start:
        # Degenerate window: every agent enters at exactly `start`.
        b_lo, b_hi = int(start // BIN_WIDTH_S), int((start + dt - 1e-9) // BIN_WIDTH_S)
        out: dict[int, float] = {}
        for b in range(max(0, b_lo), min(DAY_BINS - 1, b_hi) + 1):
            bs = b * BIN_WIDTH_S
            overlap = max(0.0, min(start + dt, bs + BIN_WIDTH_S) - max(start, bs))
            if overlap > 0:
                out[b] = overlap * count
        return out

    width = end - start
    rate = count / width
    # Only bins that can possibly overlap [start, start+dt) .. [end, end+dt)
    b_lo = int(start // BIN_WIDTH_S) - 1
    b_hi = int((end + dt) // BIN_WIDTH_S) + 1
    out = {}
    for b in range(max(0, b_lo), min(DAY_BINS - 1, b_hi) + 1):
        val = rate * _dwell_bin_overlap_integral(start, end, dt, b * BIN_WIDTH_S)
        if val > 0:
            out[b] = val
    return out


RAW_ROW_FLUSH_THRESHOLD = 5_000_000  # accumulated (edge, bin) raw rows before an incremental aggregation pass


def _aggregate_raw_batch(
    col_edge_id: list[int], col_bin: list[int], col_flow: list[float],
    col_person_seconds: list[float], col_travel_time: list[float],
) -> pd.DataFrame:
    raw = pd.DataFrame(
        {"edge_id": col_edge_id, "bin": col_bin, "flow": col_flow,
         "person_seconds": col_person_seconds, "travel_time_s": col_travel_time}
    )
    return raw.groupby(["edge_id", "bin"], as_index=False).agg(
        flow=("flow", "sum"), person_seconds=("person_seconds", "sum"),
        travel_time_s=("travel_time_s", "max"),
    )


def _merge_aggregate(acc: pd.DataFrame | None, batch: pd.DataFrame) -> pd.DataFrame:
    if acc is None:
        return batch
    combined = pd.concat([acc, batch], ignore_index=True)
    return combined.groupby(["edge_id", "bin"], as_index=False).agg(
        flow=("flow", "sum"), person_seconds=("person_seconds", "sum"),
        travel_time_s=("travel_time_s", "max"),
    )


def extract_walk_bike_bins(city: CityConfig, scenario: ScenarioSpec, mode: str) -> tuple[pd.DataFrame, dict]:
    """Returns (bins_df, diagnostics). bins_df columns: mode, edge_id,
    bin_start_s, bin_end_s, flow, person_seconds, travel_time_s
    ('person_seconds' here means the walk/bike agent-seconds directly — no
    occupancy-factor conversion applies, unlike car; see build_od.py, which
    never divides walk/bike counts by occupancy_factor).

    diagnostics includes:
      od_rows, od_agents, routed_rows, routed_agents,
      unreachable_rows, unreachable_agents,
      first_edge_flow_total (must equal routed_agents exactly),
      total_person_seconds_in_window (sum over the returned table),
      total_person_seconds_expected (= sum(count * route_total_time) over
        routed rows — the target for the in-window sum plus spillover),
      spillover_person_seconds_next_day (portion excluded from the table
        because it falls at/after the simulated day's end — see module
        docstring "Day-boundary handling"),
      spillover_rows_affected, spillover_agents_affected.
    """
    if mode not in ("walk", "bike"):
        raise ValueError("mode must be 'walk' or 'bike'")

    require_nomad_python()
    import nomad._nomad_core as core

    graph = core.Graph.load(str(city.graph_bin))
    router = core.AStarRouter(graph)
    agent_mode = core.AgentMode.Walk if mode == "walk" else core.AgentMode.Bike
    walk_speed_ms, bike_speed_ms = 1.39, 4.17

    od = pd.read_csv(city.od_csv(scenario.od_label))
    od = od[od["mode"] == mode]

    route_cache: dict[tuple[int, int], object] = {}
    time_cache: dict[int, float] = {}

    def edge_time(edge_id: int) -> float:
        if edge_id not in time_cache:
            time_cache[edge_id] = graph.mode_free_flow_time(
                edge_id, agent_mode, walk_speed_ms, bike_speed_ms
            )
        return time_cache[edge_id]

    col_edge_id: list[int] = []
    col_bin: list[int] = []
    col_flow: list[float] = []
    col_person_seconds: list[float] = []
    col_travel_time: list[float] = []
    # Periodically reduced into agg_acc (below) rather than kept in full for the
    # whole OD table: for a large city (Barcelona: ~917k edges) the raw
    # per-(edge, bin) contributions across every route can reach hundreds of
    # millions of rows before any aggregation, which grew unbounded past
    # available memory. Sum/max are associative, so flushing in bounded
    # batches produces byte-identical results to aggregating once at the end.
    agg_acc: pd.DataFrame | None = None

    od_agents = int(od["count"].sum())
    unreachable_rows = 0
    unreachable_agents = 0
    routed_rows = 0
    first_edge_flow_total = 0.0
    total_person_seconds_expected = 0.0
    spillover_person_seconds = 0.0
    spillover_rows = 0
    spillover_agents = 0.0

    for orig, dest, count, depart_start, depart_end in od[
        ["origin_node", "dest_node", "count", "depart_mean_s", "depart_std_s"]
    ].itertuples(index=False):
        key = (int(orig), int(dest))
        if key not in route_cache:
            req = core.RoutingRequest(int(orig), int(dest), 0.0, agent_mode)
            route_cache[key] = router.route(req)
        route = route_cache[key]
        if not route.is_valid:
            unreachable_rows += 1
            unreachable_agents += int(count)
            continue
        routed_rows += 1
        count_f = float(count)
        total_person_seconds_expected += count_f * route.estimated_time_s

        row_had_spillover = False
        cumulative = 0.0
        for i, edge_id in enumerate(route.edges):
            dt = edge_time(edge_id)
            entry_start, entry_end = depart_start + cumulative, depart_end + cumulative

            flow_bins = _distribute_into_bins(entry_start, entry_end, count_f)
            if i == 0:
                first_edge_flow_total += sum(flow_bins.values())
            for b, frac_count in flow_bins.items():
                col_edge_id.append(int(edge_id))
                col_bin.append(b)
                col_flow.append(frac_count)
                col_person_seconds.append(0.0)  # filled in below, separate pass
                col_travel_time.append(dt)

            dwell_bins = _dwell_into_bins(entry_start, entry_end, count_f, dt)
            assigned_ps = sum(dwell_bins.values())
            expected_ps = count_f * dt
            shortfall = expected_ps - assigned_ps
            if shortfall > 1e-6:
                row_had_spillover = True
                spillover_person_seconds += shortfall
            for b, ps in dwell_bins.items():
                col_edge_id.append(int(edge_id))
                col_bin.append(b)
                col_flow.append(0.0)
                col_person_seconds.append(ps)
                col_travel_time.append(dt)

            cumulative += dt

        if row_had_spillover:
            spillover_rows += 1
            spillover_agents += count_f

        if len(col_edge_id) >= RAW_ROW_FLUSH_THRESHOLD:
            batch = _aggregate_raw_batch(col_edge_id, col_bin, col_flow, col_person_seconds, col_travel_time)
            agg_acc = _merge_aggregate(agg_acc, batch)
            col_edge_id, col_bin, col_flow, col_person_seconds, col_travel_time = [], [], [], [], []

    if col_edge_id:
        batch = _aggregate_raw_batch(col_edge_id, col_bin, col_flow, col_person_seconds, col_travel_time)
        agg_acc = _merge_aggregate(agg_acc, batch)

    if agg_acc is None:
        bins_df = pd.DataFrame(
            columns=["mode", "edge_id", "bin_start_s", "bin_end_s", "flow", "person_seconds", "travel_time_s"]
        )
    else:
        bins_df = agg_acc
        bins_df.insert(0, "mode", mode)
        bins_df["bin_start_s"] = bins_df.pop("bin") * BIN_WIDTH_S
        bins_df["bin_end_s"] = bins_df["bin_start_s"] + BIN_WIDTH_S

    diagnostics = {
        "od_rows": int(len(od)),
        "od_agents": od_agents,
        "routed_rows": routed_rows,
        "routed_agents": od_agents - unreachable_agents,
        "unreachable_rows": unreachable_rows,
        "unreachable_agents": unreachable_agents,
        "unique_od_pairs_routed": len(route_cache),
        "first_edge_flow_total": first_edge_flow_total,
        "total_person_seconds_in_window": float(bins_df["person_seconds"].sum()) if len(bins_df) else 0.0,
        "total_person_seconds_expected": total_person_seconds_expected,
        "spillover_person_seconds_next_day": spillover_person_seconds,
        "spillover_rows_affected": spillover_rows,
        "spillover_agents_affected": spillover_agents,
    }
    return bins_df, diagnostics


class ConservationError(RuntimeError):
    pass


def assert_conservation(diagnostics: dict, tol: float = 1.0) -> None:
    """Fail loudly if the routed/flow/person-seconds bookkeeping doesn't add
    up -- per "non inventare flussi", a silently-wrong aggregation is worse
    than a crash."""
    routed_plus_unreachable = diagnostics["routed_agents"] + diagnostics["unreachable_agents"]
    if abs(routed_plus_unreachable - diagnostics["od_agents"]) > tol:
        raise ConservationError(
            f"routed_agents ({diagnostics['routed_agents']}) + unreachable_agents "
            f"({diagnostics['unreachable_agents']}) != od_agents ({diagnostics['od_agents']})"
        )
    if abs(diagnostics["first_edge_flow_total"] - diagnostics["routed_agents"]) > tol:
        raise ConservationError(
            f"first_edge_flow_total ({diagnostics['first_edge_flow_total']}) != "
            f"routed_agents ({diagnostics['routed_agents']}) -- flow is not conserved "
            "at trip origin."
        )
    ps_accounted = (
        diagnostics["total_person_seconds_in_window"] + diagnostics["spillover_person_seconds_next_day"]
    )
    ps_expected = diagnostics["total_person_seconds_expected"]
    if ps_expected > 0 and abs(ps_accounted - ps_expected) / ps_expected > 1e-6:
        raise ConservationError(
            f"in-window person-seconds ({diagnostics['total_person_seconds_in_window']}) + "
            f"next-day spillover ({diagnostics['spillover_person_seconds_next_day']}) != "
            f"expected total ({ps_expected}) -- person-seconds are not conserved."
        )
