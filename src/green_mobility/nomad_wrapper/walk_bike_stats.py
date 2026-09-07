"""Per-trip and derived summary statistics for walk/bike, built on top of
nomad_wrapper.walk_bike_routes' deterministic routing (see that module's
docstring for why no Simulation.run() is needed for these modes).

Two things this module explicitly checks that must never be violated for a
correct multimodal export (see the audit report):
  - pedestrians/cyclists never use Motorway/MotorwayLink (bike also never
    uses Steps) — road_class_accessible() in include/nomad/core/types.hpp.
  - person_seconds/flow shares stay within plausible physical bounds.
"""
from __future__ import annotations

import pandas as pd

from green_mobility.config import CityConfig, ScenarioSpec
from green_mobility.nomad_wrapper.paths import require_nomad_python
from green_mobility.nomad_wrapper.walk_bike_routes import DAY_END_S, MAX_PLAUSIBLE_DURATION_S, MAX_PLAUSIBLE_SPEED_MS

# RoadClass values a car/walk/bike-forbidden edge would carry — mirrors
# road_class_accessible() in include/nomad/core/types.hpp exactly:
#   Walk excluded: Motorway(0), MotorwayLink(1)
#   Bike excluded: Motorway(0), MotorwayLink(1), Steps(18)
FORBIDDEN_ROAD_CLASSES = {
    "walk": {0, 1},
    "bike": {0, 1, 18},
}


def extract_trip_summary(city: CityConfig, scenario: ScenarioSpec, mode: str) -> tuple[pd.DataFrame, dict]:
    """One row per routed OD pair: origin_node, dest_node, count,
    distance_m, duration_s, speed_ms, depart_start, depart_end,
    ends_after_midnight_share (fraction of this row's agents, given their
    uniformly-sampled departure time, whose arrival time is >= 86400)."""
    if mode not in ("walk", "bike"):
        raise ValueError("mode must be 'walk' or 'bike'")

    require_nomad_python()
    import nomad._nomad_core as core

    graph = core.Graph.load(str(city.graph_bin))
    router = core.AStarRouter(graph)
    agent_mode = core.AgentMode.Walk if mode == "walk" else core.AgentMode.Bike

    od = pd.read_csv(city.od_csv(scenario.od_label))
    od = od[od["mode"] == mode]

    route_cache: dict[tuple[int, int], object] = {}
    rows = []
    unreachable_agents = 0

    for orig, dest, count, depart_start, depart_end in od[
        ["origin_node", "dest_node", "count", "depart_mean_s", "depart_std_s"]
    ].itertuples(index=False):
        key = (int(orig), int(dest))
        if key not in route_cache:
            req = core.RoutingRequest(int(orig), int(dest), 0.0, agent_mode)
            route_cache[key] = router.route(req)
        route = route_cache[key]
        if not route.is_valid:
            unreachable_agents += int(count)
            continue

        duration_s = route.estimated_time_s
        distance_m = route.estimated_dist_m
        speed_ms = distance_m / duration_s if duration_s > 0 else 0.0

        # Share of this row's agents (uniform departure in [depart_start,
        # depart_end)) whose arrival (depart + duration) falls on/after
        # DAY_END_S: exact overlap of the shifted window with [DAY_END_S, inf).
        width = max(depart_end - depart_start, 1e-9)
        arrival_lo, arrival_hi = depart_start + duration_s, depart_end + duration_s
        after_midnight_overlap = max(0.0, arrival_hi - max(arrival_lo, DAY_END_S))
        ends_after_midnight_share = min(1.0, after_midnight_overlap / width)

        rows.append(
            {
                "origin_node": int(orig),
                "dest_node": int(dest),
                "count": float(count),
                "distance_m": distance_m,
                "duration_s": duration_s,
                "speed_ms": speed_ms,
                "depart_start": depart_start,
                "depart_end": depart_end,
                "ends_after_midnight_share": ends_after_midnight_share,
            }
        )

    trips = pd.DataFrame(rows)
    diagnostics = {
        "od_rows": int(len(od)),
        "od_agents": int(od["count"].sum()),
        "unreachable_agents": unreachable_agents,
        "unique_od_pairs_routed": len(route_cache),
    }
    return trips, diagnostics


def _weighted_percentile(values: pd.Series, weights: pd.Series, q: float) -> float:
    order = values.sort_values().index
    v = values.loc[order].to_numpy()
    w = weights.loc[order].to_numpy()
    cw = w.cumsum()
    target = q * w.sum()
    idx = int((cw >= target).argmax())
    return float(v[idx])


def trip_percentiles(trips: pd.DataFrame) -> dict:
    """p50/p90/p95/p99 for distance_m, duration_s, speed_ms — weighted by
    `count` (each row represents `count` agents sharing the same route)."""
    out = {}
    for col in ("distance_m", "duration_s", "speed_ms"):
        out[col] = {
            f"p{int(q*100)}": _weighted_percentile(trips[col], trips["count"], q)
            for q in (0.50, 0.90, 0.95, 0.99)
        }
    return out


def plausibility_shares(trips: pd.DataFrame, mode: str) -> dict:
    """Share of agents (weighted by count) exceeding documented plausibility
    thresholds — see MAX_PLAUSIBLE_SPEED_MS / MAX_PLAUSIBLE_DURATION_S in
    walk_bike_routes.py. A nonzero share here does not necessarily mean an
    error — long-but-real routes exist — but a LARGE share would indicate a
    routing or network-data problem worth investigating."""
    total = trips["count"].sum()
    over_speed = trips.loc[trips["speed_ms"] > MAX_PLAUSIBLE_SPEED_MS[mode], "count"].sum()
    over_duration = trips.loc[trips["duration_s"] > MAX_PLAUSIBLE_DURATION_S, "count"].sum()
    ends_after_midnight = (trips["count"] * trips["ends_after_midnight_share"]).sum()
    return {
        "share_over_speed_threshold": float(over_speed / total) if total else 0.0,
        "share_over_duration_threshold": float(over_duration / total) if total else 0.0,
        "share_ending_after_midnight": float(ends_after_midnight / total) if total else 0.0,
        "speed_threshold_ms": MAX_PLAUSIBLE_SPEED_MS[mode],
        "duration_threshold_s": MAX_PLAUSIBLE_DURATION_S,
    }


def hourly_flow_and_person_seconds(bins: pd.DataFrame) -> pd.DataFrame:
    df = bins.copy()
    df["hour"] = (df["bin_start_s"] // 3600).astype(int)
    return df.groupby("hour", as_index=False).agg(flow=("flow", "sum"), person_seconds=("person_seconds", "sum"))


def check_mode_edge_compatibility(bins: pd.DataFrame, edges: pd.DataFrame, mode: str) -> dict:
    """Explicit verification (not an assumption) that no walk/bike flow or
    person_seconds lands on an edge whose road_class is inaccessible for
    that mode per road_class_accessible() in types.hpp."""
    forbidden = FORBIDDEN_ROAD_CLASSES[mode]
    merged = bins.merge(edges[["edge_id", "road_class"]], on="edge_id", how="left")
    bad = merged[merged["road_class"].isin(forbidden)]
    return {
        "forbidden_road_classes": sorted(forbidden),
        "n_edge_bin_rows_on_forbidden_class": int(len(bad)),
        "flow_on_forbidden_class": float(bad["flow"].sum()),
        "person_seconds_on_forbidden_class": float(bad["person_seconds"].sum()),
        "distinct_forbidden_edges_used": sorted(bad["edge_id"].unique().tolist()),
    }


def top_exposure_edges(bins: pd.DataFrame, top_pct: float = 0.01) -> pd.DataFrame:
    """Top `top_pct` share of edges ranked by total person_seconds summed
    across all bins — the highest-exposure segments for this mode."""
    per_edge = bins.groupby("edge_id", as_index=False).agg(
        total_person_seconds=("person_seconds", "sum"),
        total_flow=("flow", "sum"),
        n_bins_active=("bin_start_s", "count"),
    )
    n_top = max(1, int(round(len(per_edge) * top_pct)))
    return per_edge.sort_values("total_person_seconds", ascending=False).head(n_top)
