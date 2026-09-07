"""Turn NOMAD's periodic GeoJSON snapshots (GeoJsonWriter, car-only — see
flows.py module docstring for why) into a per-edge, per-time-bin car
exposure table, without re-running any simulation.

Each snapshot is an INSTANTANEOUS state at t = k * snapshot_interval_s (here
900s = 15 min), not a value integrated over the preceding window. Treating
that instant as representative of the whole bin (steady-state-within-bin
approximation) is the only way to get bin-level numbers out of this output
format without re-simulating with event hooks — it is a real approximation,
not measured truth, and is documented per-column below rather than hidden.

Column semantics — kept EXPLICITLY distinct (observed vs. estimated vs. not
available), per the audit that flagged the original `person_seconds` name as
misleadingly implying a person-level measurement when it was really a
vehicle-count with an occupancy-factor bolted on:
  occupancy_veh       — OBSERVED: directly read (`count` in the GeoJSON),
                        vehicles on the edge at the snapshot instant.
  flow_veh_h          — ESTIMATED: directly read (`flow_veh_h`), NOMAD's own
                        Little's-Law estimate (occ * 3600 / travel_time,
                        capacity-capped) — not a counted throughput. Real
                        event-based flow counts are NOT AVAILABLE for this
                        run (no hooks/trajectory store were used; see
                        nomad_wrapper.flows for the hook-based mechanism
                        that would give real counts on a re-run).
  flow_veh_h_uncapped — ESTIMATED, DERIVED here (not read from NOMAD): the
                        same Little's-Law formula as flow_veh_h but WITHOUT
                        the capacity clip applied in
                        external/nomad/src/output/geojson_writer.cpp:96-97.
                        A capacity-capped value can only ever plateau (a
                        min() has a ceiling, never a hump), so flow_veh_h is
                        not suitable on its own for a flow-density
                        fundamental diagram — use this column instead for
                        that purpose. Confirmed capacity-clipped in ~75% of
                        rows in this project's own Palma car baseline; see
                        notebooks/paper_figures/02_figure2_multimodal_thermal_framework.ipynb.
  capacity_veh_h      — OBSERVED: directly read (`capacity_veh_h`), the
                        per-edge capacity value flow_veh_h is clipped
                        against (= lanes * capacity_per_lane_veh_h, see
                        external/nomad/src/network/osm_loader.cpp and
                        data/schemas/osm_tag_config.yaml). Exposed so a
                        capped row (flow_veh_h_uncapped > flow_veh_h) can be
                        identified explicitly rather than inferred.
  vehicle_seconds     — DERIVED from the observed occupancy only, no
                        assumption applied: occupancy_veh * bin_duration_s.
  person_seconds_est  — ESTIMATED, explicit factor applied and named as
                        such: vehicle_seconds * occupancy_factor (the same
                        factor build_od.py used to go from MITMA person-trips
                        to vehicle-trips). This is an assumption-based
                        estimate, not a measurement — do not treat it as
                        equivalent to vehicle_seconds.
  mean_travel_time_s  — OBSERVED: directly read (`travel_time_s`); "mean"
                        only in the sense that it's the single value
                        standing in for the whole bin, not an average over
                        multiple observations.
  free_flow_time_s    — OBSERVED (static per edge/mode).
  travel_time_ratio   — mean_travel_time_s / free_flow_time_s.
  vehicle_hours_lost  — occupancy_veh * (tt - ff) / tt * (bin_duration_s / 3600),
                        i.e. (excess-time fraction) x (vehicle-hours present in
                        the bin) — algebraically equals flow_veh_h * (tt-ff) *
                        (bin_duration_s/3600)/3600 by Little's Law (occ = flow*tt),
                        so it doesn't compound two independent estimates.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

SNAPSHOT_RE = re.compile(r"network_state_(\d+)\.geojson$")


def load_car_bins(output_dir: Path, occupancy_factor: float) -> pd.DataFrame:
    """output_dir: a scenario run's timestamped output directory (contains
    network_state_NNNNNN.geojson + static_network.geojson)."""
    files = sorted(output_dir.glob("network_state_*.geojson"))
    if not files:
        raise FileNotFoundError(f"no network_state_*.geojson snapshots in {output_dir}")

    rows = []
    prev_t = None
    for fp in files:
        m = SNAPSHOT_RE.search(fp.name)
        if not m:
            continue
        t = int(m.group(1))
        bin_duration_s = 900.0 if prev_t is None else float(t - prev_t)
        prev_t = t
        with open(fp) as f:
            gj = json.load(f)
        for feat in gj["features"]:
            p = feat["properties"]
            occ = p["count"]
            tt = p["travel_time_s"]
            ff = p["free_flow_time_s"]
            ratio = tt / ff if ff > 0 else float("nan")
            veh_hours_lost = (
                occ * (tt - ff) / tt * (bin_duration_s / 3600.0) if tt > 0 else 0.0
            )
            vehicle_seconds = occ * bin_duration_s
            # flow_veh_h (below) is capacity-capped at the source
            # (external/nomad/src/output/geojson_writer.cpp:96-97,
            # std::min(occ*3600/tt, edge.capacity)) -- fine for the delay/
            # congestion accounting this module was built for, but a capped
            # value can only ever plateau, never show a descending
            # (congested) branch, which makes it unsuitable on its own for a
            # flow-density fundamental diagram. flow_veh_h_uncapped is the
            # same Little's-Law estimate WITHOUT that clip (recomputed here
            # from occ/tt, already available -- no new simulation), and
            # capacity_veh_h is the clip value itself (already emitted by
            # GeoJsonWriter, just not read into this table before), so
            # capped rows can be identified explicitly downstream rather
            # than inferred.
            flow_uncapped = occ * 3600.0 / tt if tt > 0 else 0.0
            rows.append(
                {
                    "edge_id": p["edge_id"],
                    "road_class": p["road_class"],
                    "bin_end_s": t,
                    "bin_start_s": max(0.0, t - bin_duration_s),
                    "occupancy_veh": occ,
                    "flow_veh_h": p["flow_veh_h"],
                    "flow_veh_h_uncapped": flow_uncapped,
                    "capacity_veh_h": p["capacity_veh_h"],
                    "vehicle_seconds": vehicle_seconds,
                    "person_seconds_est": vehicle_seconds * occupancy_factor,
                    "mean_travel_time_s": tt,
                    "free_flow_time_s": ff,
                    "travel_time_ratio": ratio,
                    "vehicle_hours_lost": veh_hours_lost,
                    "congestion": p["congestion"],
                }
            )
    return pd.DataFrame(rows)


MIN_RELIABLE_EDGE_LENGTH_M = 20.0
# Below this length, density_veh_km = occupancy_veh / (length_m/1000) is
# dominated by a division artifact, not real congestion: NOMAD's occupancy is
# a point-queue bookkeeping count (agents logically "in" that edge), not a
# spatial count of vehicles physically spaced along its geometry -- dividing
# it by a near-zero length (some OSM-derived junction/connector edges are
# under 1m) produces density readings in the thousands of veh/km, which then
# also blow up flow_veh_h_uncapped via Little's Law (occ*3600/tt), even on
# edges with travel_time_ratio == 1.0 (i.e. reported as perfectly free-flow).
# Confirmed empirically on Palma (weekday_full_car_2022-02-08): restricting
# to length_m >= 20 drops the share of edge-bins with density > 500 veh/km
# from 2.93% to 0.04% of all active edge-bins.


def add_density(bins: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Merge in length_m and density_veh_km. Adds `density_reliable`
    (length_m >= MIN_RELIABLE_EDGE_LENGTH_M) rather than silently dropping
    short edges -- see the module-level note above for why density is not
    meaningful below that threshold. Callers building a flow-density
    fundamental diagram should filter on `density_reliable`."""
    m = bins.merge(edges[["edge_id", "length_m"]], on="edge_id", how="left")
    m["density_veh_km"] = m["occupancy_veh"] / (m["length_m"] / 1000.0)
    m["density_reliable"] = m["length_m"] >= MIN_RELIABLE_EDGE_LENGTH_M
    return m


def hourly_ratio_shares(bins: pd.DataFrame) -> pd.DataFrame:
    """Share of active edge-bins (per hour) exceeding travel_time_ratio
    thresholds 1.1 / 1.5 / 2.0."""
    df = bins.copy()
    df["hour"] = (df["bin_end_s"] // 3600).astype(int)
    out = df.groupby("hour").apply(
        lambda g: pd.Series(
            {
                "n_active_edge_bins": len(g),
                "share_ratio_gt_1_1": (g["travel_time_ratio"] > 1.1).mean(),
                "share_ratio_gt_1_5": (g["travel_time_ratio"] > 1.5).mean(),
                "share_ratio_gt_2_0": (g["travel_time_ratio"] > 2.0).mean(),
                "flow_weighted_congestion": (
                    (g["congestion"] * g["flow_veh_h"]).sum() / g["flow_veh_h"].sum()
                    if g["flow_veh_h"].sum() > 0
                    else 0.0
                ),
            }
        ),
        include_groups=False,
    )
    return out.reset_index()


def top_delay_edges(bins: pd.DataFrame, top_pct: float = 0.01) -> pd.DataFrame:
    """Top `top_pct` share of edges (by count of distinct edge_id present)
    ranked by total vehicle_hours_lost summed across all bins."""
    per_edge = bins.groupby("edge_id", as_index=False).agg(
        total_vehicle_hours_lost=("vehicle_hours_lost", "sum"),
        total_vehicle_seconds=("vehicle_seconds", "sum"),
        max_travel_time_ratio=("travel_time_ratio", "max"),
        n_bins_active=("bin_end_s", "count"),
        road_class=("road_class", "first"),
    )
    n_top = max(1, int(round(len(per_edge) * top_pct)))
    return per_edge.sort_values("total_vehicle_hours_lost", ascending=False).head(n_top)


def bottleneck_persistence(bins: pd.DataFrame, ratio_threshold: float = 1.5) -> pd.DataFrame:
    """Per edge_id: how many/what share of the bins it was ACTIVE in did it
    exceed ratio_threshold — distinguishes chronic from transient bottlenecks."""
    df = bins.copy()
    df["congested"] = df["travel_time_ratio"] > ratio_threshold
    out = df.groupby("edge_id", as_index=False).agg(
        n_bins_active=("bin_end_s", "count"),
        n_bins_congested=("congested", "sum"),
        road_class=("road_class", "first"),
    )
    out["persistence_share"] = out["n_bins_congested"] / out["n_bins_active"]
    return out[out["n_bins_congested"] > 0].sort_values("persistence_share", ascending=False)
