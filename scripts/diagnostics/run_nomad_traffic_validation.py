#!/usr/bin/env python3
"""NOMAD car traffic-model validation: audits the existing car baseline and
runs a demand-scaled stress-test sweep to check whether NOMAD's own
QueueTrafficModel can produce a genuine congested (descending) branch of the
flow-density fundamental diagram once demand actually saturates some edges.

Follows directly from the diagnosis already done in
notebooks/paper_figures/02_figure2_multimodal_thermal_framework.ipynb (Panel
B fundamental diagram) and the corresponding fix in
src/green_mobility/nomad_wrapper/car_snapshots.py (flow_veh_h_uncapped,
capacity_veh_h columns). Nothing here invents a NOMAD config field, CLI flag,
or dataframe column that doesn't already exist in this repo -- every fact
below was verified against source before being used; see "Source references".

CLI, not a notebook: safe to run headless (matplotlib Agg backend forced
below), resumable (skips scenarios whose output already exists and is valid
unless --force), fails fast only for unrecoverable setup errors (unknown
city, NOMAD not built), otherwise records a per-scenario failure and moves
on to the next scenario.

Source references (file:line, verified this session before writing this
script):
  - demand_scale is native NOMAD downsampling, (0,1] ONLY, binomial thinning:
    src/green_mobility/config.py:82-83 (Python validation),
    external/nomad/src/demand/od_matrix.cpp:84-90 (C++:
    std::binomial_distribution requires p in [0,1]; demand_scale>=1.0 skips
    thinning entirely -- it cannot amplify demand above the raw OD count).
    Factors >1.0 in this script therefore use a SEPARATE, honest mechanism:
    a scaled copy of the OD CSV's own `count` column (see
    write_scaled_od_csv), not demand_scale.
  - od_csv demand is seeded with a HARDCODED 42, not configurable per-run:
    external/nomad/tools/nomad-cli/main.cpp:197-199
    (`OdMatrixDemand::from_csv(*cfg.demand.od_csv, 42, ...)`). Only
    `demand.synthetic.seed` (gravity/radiation demand) is configurable --
    unused by every scenario in this project (all use od_csv). This script
    does not (and could not) pass a NOMAD-side seed for the car stress
    scenarios; determinism already holds for any re-run of the same OD
    file/demand_scale without further action.
  - flow_veh_h is NOT measured throughput -- a capacity-capped Little's-Law
    estimate: external/nomad/src/output/geojson_writer.cpp:96-97. This
    script uses flow_veh_h_uncapped/capacity_veh_h
    (src/green_mobility/nomad_wrapper/car_snapshots.py) instead, never
    flow_veh_h, for anything framed as "flow".
  - No per-edge, per-time-bin n_enter/n_exit are recorded anywhere in NOMAD
    (only instantaneous occupancy snapshots) -- confirmed absent from
    external/nomad/src/output/geojson_writer.cpp and
    external/nomad/src/traffic/queue_model.cpp. See
    scripts/diagnostics/proposed_patch_enter_exit_counters.md for the
    minimal addition that would provide them. NOT applied by this script.
  - Road-class capacity/lanes table: loaded directly from
    external/nomad/data/schemas/osm_tag_config.yaml (not hand-copied, to
    avoid drift if that file changes).
  - kJamDensity = 1/7.5 veh/m:
    external/nomad/include/nomad/traffic/traffic_model.hpp:15.
  - storage_cap = max(50, length_m) * max(1, capacity/1600) * kJamDensity;
    BPR travel time tt=ff*(1+0.15*vc^4), vc=min(occ/storage_cap, 2):
    external/nomad/src/traffic/queue_model.cpp (ctor, on_enter).
  - OD CSV schema (origin_node,dest_node,count,mode,depart_mean_s,
    depart_std_s): external/nomad/src/demand/od_matrix.cpp:43-54, confirmed
    against data/palma_de_mallorca/od_palma_de_mallorca_weekday.csv.
  - ScenarioSpec/ScenarioRun/write_manifest/parse_nomad_cli_stats: reused
    as-is from src/green_mobility/{config,manifest}.py and
    src/green_mobility/nomad_wrapper/{runner,car_snapshots}.py -- no
    reimplementation of any of NOMAD's own config/output handling.
  - runlog invocation pattern and required environment (GM_NOMAD_PYTHON,
    LD_PRELOAD, PATH, PROJ_LIB, PYTHONPATH=src):
    scripts/launch_simulations.sh (validated end-to-end earlier this
    session; `run_nomad_cli` itself only needs the built `nomad_cli`
    binary, but this env has been the working recipe for the whole `gm`
    CLI process in this project and is reused unchanged for consistency).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # headless -- no display/notebook required

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from green_mobility.config import (  # noqa: E402
    CityConfig,
    ConfigError,
    NOMAD_DIR,
    ScenarioSpec,
    load_all_cities,
)
from green_mobility.manifest import write_manifest  # noqa: E402

# ---------------------------------------------------------------------------
# Constants verified against source (see module docstring for citations)
# ---------------------------------------------------------------------------
ROAD_CLASS_LABELS: dict[int, str] = {
    0: "Motorway", 1: "MotorwayLink", 2: "Trunk", 3: "TrunkLink",
    4: "Primary", 5: "PrimaryLink", 6: "Secondary", 7: "SecondaryLink",
    8: "Tertiary", 9: "TertiaryLink", 10: "Residential", 11: "LivingStreet",
    12: "Service", 13: "Unclassified",
}
# external/nomad/src/network/osm_loader.cpp maps highway= tags to RoadClass
# via OsmTagConfig::highway_map; this is that same tag->code mapping.
HIGHWAY_TAG_TO_ROAD_CLASS: dict[str, int] = {
    "motorway": 0, "motorway_link": 1, "trunk": 2, "trunk_link": 3,
    "primary": 4, "primary_link": 5, "secondary": 6, "secondary_link": 7,
    "tertiary": 8, "tertiary_link": 9, "residential": 10,
    "living_street": 11, "service": 12, "unclassified": 13,
}
K_JAM_DENSITY = 1.0 / 7.5  # veh/m; traffic_model.hpp:15
NOMAD_ODCSV_SEED = 42  # hardcoded in nomad-cli/main.cpp:198, not configurable
OSM_TAG_CONFIG_PATH = NOMAD_DIR / "data" / "schemas" / "osm_tag_config.yaml"
DEFAULT_FACTORS = (0.5, 1.0, 2.0, 4.0)
BASE_CAR_SCENARIO_KEY = "weekday_full_car"
BPR_ACTIVE_VC_THRESHOLD = 0.8  # queue_model.cpp on_enter: BPR only engages above this v/c
BPR_VC_CAP = 2.0
CONGESTED_TT_RATIO = 1.5  # same threshold used in car_snapshots.hourly_ratio_shares


# ---------------------------------------------------------------------------
# Road-class capacity/storage table (loaded from NOMAD's own YAML, not
# hand-copied)
# ---------------------------------------------------------------------------
def load_road_class_defaults() -> pd.DataFrame:
    if not OSM_TAG_CONFIG_PATH.exists():
        raise SystemExit(f"osm_tag_config.yaml not found at {OSM_TAG_CONFIG_PATH}")
    raw = yaml.safe_load(OSM_TAG_CONFIG_PATH.read_text())
    rows = []
    for tag, vals in raw["highway_defaults"].items():
        if tag not in HIGHWAY_TAG_TO_ROAD_CLASS:
            continue  # non-car classes (track/cycleway/footway/path/steps)
        speed_kmh, lanes, cap_per_lane = vals
        rc = HIGHWAY_TAG_TO_ROAD_CLASS[tag]
        rows.append({
            "road_class": rc, "road_class_label": ROAD_CLASS_LABELS[rc],
            "default_speed_kmh": float(speed_kmh), "default_lanes": int(lanes),
            "capacity_per_lane_veh_h": float(cap_per_lane),
            "default_capacity_veh_h": float(lanes) * float(cap_per_lane),
        })
    return pd.DataFrame(rows).sort_values("road_class").reset_index(drop=True)


def estimated_lanes_for_capacity(capacity_veh_h: float) -> float:
    """queue_model.cpp ctor: estimated_lanes = max(1, capacity/1600)."""
    return max(1.0, capacity_veh_h / 1600.0)


def storage_cap_for_edge(length_m: float, capacity_veh_h: float) -> float:
    """queue_model.cpp ctor: storage_cap = max(50, length_m) * estimated_lanes * kJamDensity."""
    eff_len = max(50.0, length_m)
    return eff_len * estimated_lanes_for_capacity(capacity_veh_h) * K_JAM_DENSITY


# ---------------------------------------------------------------------------
# Step 1: audit the existing baseline (read-only, no simulation)
# ---------------------------------------------------------------------------
def audit_od_demand(city: CityConfig, scenario: ScenarioSpec) -> dict[str, Any]:
    """Hourly requested demand (from the OD CSV's own count/depart_mean_s/
    depart_std_s -- no re-simulation) for the modes this scenario allows."""
    od_path = city.od_csv(scenario.od_label)
    if not od_path.exists():
        return {"status": "MISSING", "od_csv": str(od_path)}
    od = pd.read_csv(od_path)
    sub = od[od["mode"].isin(scenario.modes)]
    hourly = (
        sub.assign(hour=(sub["depart_mean_s"] // 3600).clip(lower=0).astype(int))
        .groupby("hour")["count"].sum()
        .reindex(range(24), fill_value=0)
        .reset_index()
        .rename(columns={"index": "hour", "count": "requested_agents"})
    )
    return {
        "status": "OK",
        "od_csv": str(od_path),
        "n_od_rows": int(len(sub)),
        "requested_agents_total": int(sub["count"].sum()),
        "depart_std_s_p50": float(sub["depart_std_s"].median()),
        "depart_std_s_p95": float(sub["depart_std_s"].quantile(0.95)),
        "hourly_requested": hourly,
    }


def audit_run_stats(manifest_path: Path) -> dict[str, Any]:
    """requested/created/routed/departed/arrived/failed/teleported +
    agent-conservation identity, read from an already-written manifest
    (same run_stats fields cli.py's `gm run` already parses via
    parse_nomad_cli_stats -- reused here, not reparsed differently)."""
    if not manifest_path.exists():
        return {"status": "MISSING", "manifest": str(manifest_path)}
    manifest = json.loads(manifest_path.read_text())
    stats = manifest.get("params", {}).get("run_stats", {})
    if not stats:
        return {"status": "NO_RUN_STATS", "manifest": str(manifest_path)}
    depart = stats.get("depart")
    states_sum = None
    conserved = None
    if depart is not None:
        states_sum = sum(
            stats.get(k, 0) for k in
            ("final_waiting", "final_on_link", "final_at_activity", "final_arrived")
        )
        conserved = (states_sum == depart)
    return {
        "status": "OK",
        "agents_generated": stats.get("agents_generated"),
        "routed": stats.get("routed"),
        "routing_failed": stats.get("routing_failed"),
        "depart": depart,
        "enter": stats.get("enter"),
        "exit": stats.get("exit"),
        "arrive": stats.get("arrive"),
        "teleported": stats.get("teleported"),
        "final_waiting": stats.get("final_waiting"),
        "final_on_link": stats.get("final_on_link"),
        "final_at_activity": stats.get("final_at_activity"),
        "final_arrived": stats.get("final_arrived"),
        "arrival_rate": stats.get("arrival_rate"),
        "natural_arrival_rate": stats.get("natural_arrival_rate"),
        "teleported_rate": stats.get("teleported_rate"),
        "peak_rss_mb": stats.get("peak_rss_mb"),
        "total_time_s": stats.get("total_time_s"),
        "simulation_time_s": stats.get("simulation_time_s"),
        "pre_routing_time_s": stats.get("pre_routing_time_s"),
        "mass_conservation_states_sum": states_sum,
        "mass_conservation_ok": conserved,
    }


def audit_road_class_capacity(city: CityConfig) -> pd.DataFrame:
    """Capacity, default lanes, storage capacity and short-edge share per
    road class, using the city's real edges.parquet lengths (not assumed)."""
    defaults = load_road_class_defaults()
    edges = pd.read_parquet(city.edges_parquet)
    rows = []
    for _, d in defaults.iterrows():
        rc = int(d["road_class"])
        sub = edges[edges["road_class"] == rc]
        if len(sub) == 0:
            continue
        storage_caps = sub["length_m"].apply(
            lambda L: storage_cap_for_edge(float(L), float(d["default_capacity_veh_h"]))
        )
        # A "short edge" here means eff_len is floored to 50m (i.e. the raw
        # length is below NOMAD's own effective-length floor) -- queue_model.cpp
        # ctor comment: this floor exists specifically to stop micro-segments
        # triggering BPR with 1-2 agents.
        n_short = int((sub["length_m"] < 50.0).sum())
        rows.append({
            "road_class": rc, "road_class_label": d["road_class_label"],
            "n_edges": int(len(sub)), "n_short_edges_lt_50m": n_short,
            "frac_short_edges": n_short / len(sub),
            "default_lanes": int(d["default_lanes"]),
            "capacity_per_lane_veh_h": float(d["capacity_per_lane_veh_h"]),
            "default_capacity_veh_h": float(d["default_capacity_veh_h"]),
            "length_m_median": float(sub["length_m"].median()),
            "storage_cap_veh_median": float(storage_caps.median()),
            "storage_cap_veh_p10": float(storage_caps.quantile(0.10)),
        })
    return pd.DataFrame(rows).sort_values("road_class").reset_index(drop=True)


def audit_baseline(city: CityConfig, scenario: ScenarioSpec, output_dir: Path) -> dict[str, Any]:
    """Everything askable from data already on disk -- no simulation."""
    from green_mobility.nomad_wrapper.car_snapshots import load_car_bins

    od_audit = audit_od_demand(city, scenario)
    road_class_table = audit_road_class_capacity(city)
    road_class_table.to_parquet(output_dir / "baseline_road_class_capacity.parquet", index=False)

    car_bins_path = city.green_mobility_dir / "exposure" / "weekday_full_car_edge_bins.parquet"
    car_bins_audit: dict[str, Any] = {"status": "MISSING", "path": str(car_bins_path)}
    if car_bins_path.exists():
        bins = pd.read_parquet(car_bins_path)
        n_bins_total = bins["bin_start_s"].nunique()
        per_bin_occ = bins.groupby("bin_start_s")["occupancy_veh"].sum()
        car_bins_audit = {
            "status": "OK",
            "n_rows": int(len(bins)),
            "n_edges": int(bins["edge_id"].nunique()),
            "n_bins_total": int(n_bins_total),
            "median_bins_present_per_edge": float(bins.groupby("edge_id").size().median()),
            "travel_time_ratio_p50": float(bins["travel_time_ratio"].quantile(0.50)),
            "travel_time_ratio_p95": float(bins["travel_time_ratio"].quantile(0.95)),
            "travel_time_ratio_p99": float(bins["travel_time_ratio"].quantile(0.99)),
            "frac_congested_gt_1_5": float((bins["travel_time_ratio"] > CONGESTED_TT_RATIO).mean()),
            "frac_any_congestion": float((bins["congestion"] > 0).mean()),
            "total_occupancy_min_bin": float(per_bin_occ.min()),
            "total_occupancy_max_bin": float(per_bin_occ.max()),
        }

    audit = {
        "city": city.slug, "scenario": scenario.key,
        "od_demand": od_audit,
        "existing_car_edge_bins": car_bins_audit,
        "road_class_capacity_table": str(output_dir / "baseline_road_class_capacity.parquet"),
    }
    (output_dir / "baseline_audit.json").write_text(
        json.dumps(audit, indent=2, default=lambda o: o.to_dict("records") if isinstance(o, pd.DataFrame) else str(o))
    )
    return audit


# ---------------------------------------------------------------------------
# Step 2: stress scenarios (demand factors 0.5/1.0/2.0/4.0 by default)
# ---------------------------------------------------------------------------
def write_scaled_od_csv(base_od_path: Path, modes: tuple[str, ...], factor: float, out_path: Path) -> dict[str, Any]:
    """factor>1.0 cannot use demand_scale (native NOMAD downsampling is
    (0,1] only, see module docstring) -- this multiplies the OD CSV's own
    `count` column for the scenario's modes instead, an honest, purely
    deterministic arithmetic rescale (no randomness, so no seed needed for
    this step). Rows for OTHER modes are dropped (irrelevant to a car-only
    scenario, kept out to avoid a misleadingly large file implying they
    matter)."""
    od = pd.read_csv(base_od_path)
    sub = od[od["mode"].isin(modes)].copy()
    sub["count"] = np.maximum(1, np.round(sub["count"] * factor)).astype(int)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sub.to_csv(out_path, index=False)
    return {"base_od_csv": str(base_od_path), "modes": list(modes), "factor": factor,
            "n_rows": int(len(sub)), "count_sum": int(sub["count"].sum())}


def factor_od_label(base_od_label: str, factor: float) -> str:
    return f"{base_od_label}_stress_{factor:g}x"


def build_stress_scenario(city: CityConfig, base: ScenarioSpec, factor: float,
                           materialize: bool = True) -> ScenarioSpec:
    """factor<=1.0: native demand_scale (NOMAD's own binomial-thinning
    mechanism, already deterministic under the hardcoded od_csv seed=42).
    factor>1.0: a scaled OD file (see write_scaled_od_csv), demand_scale=1.0
    to avoid double-scaling. materialize=False (used for --dry-run) never
    writes the scaled OD file to disk -- it only reports the path/label that
    WOULD be used, so a dry run has zero filesystem side effects."""
    key = f"{base.key}_stress_{factor:g}x"
    if factor <= 1.0:
        return dataclasses.replace(base, key=key, demand_scale=factor)
    od_label = factor_od_label(base.od_label, factor)
    scaled_path = city.od_csv(od_label)
    if materialize and not scaled_path.exists():
        write_scaled_od_csv(city.od_csv(base.od_label), base.modes, factor, scaled_path)
    return dataclasses.replace(base, key=key, od_label=od_label, demand_scale=1.0)


# ---------------------------------------------------------------------------
# Step 3/4: execution + resumability
# ---------------------------------------------------------------------------
def scenario_output_ready(run) -> bool:
    """Valid, complete prior output: manifest present with a successful
    run_stats block AND at least one network_state snapshot on disk."""
    manifest_path = run.output_dir.with_name(run.output_dir.name + ".manifest.json")
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return False
    if not manifest.get("params", {}).get("run_stats"):
        return False
    return run.output_dir.exists() and any(run.output_dir.glob("network_state_*.geojson"))


def run_scenario(city: CityConfig, scenario: ScenarioSpec, force: bool, dry_run: bool) -> dict[str, Any]:
    from green_mobility.nomad_wrapper.paths import require_nomad_cli
    from green_mobility.nomad_wrapper.runner import ScenarioRun, parse_nomad_cli_stats

    run = ScenarioRun(city, scenario)
    result: dict[str, Any] = {"scenario_key": scenario.key, "config_path": str(run.config_path),
                               "output_dir": str(run.output_dir)}

    if not force and scenario_output_ready(run):
        result["status"] = "SKIPPED_EXISTING"
        return result

    if dry_run:
        result["status"] = "DRY_RUN"
        result["would_run_config"] = run.build_config_dict()
        return result

    require_nomad_cli()  # raises NomadNotBuiltError with the build hint if missing -- unrecoverable, let it propagate
    config_path = run.write_config()
    t0 = time.monotonic()
    try:
        proc_result = run.run()
    except subprocess.CalledProcessError as exc:
        result["status"] = "FAILED"
        result["error"] = str(exc)
        result["stdout_tail"] = (exc.stdout or "")[-4000:]
        result["stderr_tail"] = (exc.stderr or "")[-4000:]
        result["wall_time_s"] = time.monotonic() - t0
        return result
    except Exception as exc:  # genuinely unexpected -- still recorded, not raised, per spec (continue to next scenario)
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        result["wall_time_s"] = time.monotonic() - t0
        return result

    run_stats = parse_nomad_cli_stats(proc_result.stdout)
    if getattr(proc_result, "peak_rss_kb", None):
        run_stats["peak_rss_mb"] = round(proc_result.peak_rss_kb / 1024, 1)
    write_manifest(run.output_dir, "diagnostics-stress-run",
                    {"city": city.slug, "scenario": scenario.key, "config": str(config_path),
                     "run_stats": run_stats})
    result["status"] = "OK"
    result["wall_time_s"] = time.monotonic() - t0
    result["run_stats"] = run_stats
    return result


# ---------------------------------------------------------------------------
# Step 4/6: per-scenario metrics + cross-scenario aggregation
# ---------------------------------------------------------------------------
def extract_scenario_metrics(city: CityConfig, scenario: ScenarioSpec, factor: float,
                              road_class_defaults: pd.DataFrame) -> dict[str, pd.DataFrame | dict]:
    from green_mobility.nomad_wrapper.car_snapshots import load_car_bins
    from green_mobility.nomad_wrapper.runner import ScenarioRun

    run = ScenarioRun(city, scenario)
    manifest_path = run.output_dir.with_name(run.output_dir.name + ".manifest.json")
    run_audit = audit_run_stats(manifest_path)

    bins = load_car_bins(run.output_dir, city.demand_occupancy_factor)
    n_bins_total = bins["bin_start_s"].nunique()

    # timebin_metrics: native 900s bins (not hourly) -- mass-conservation
    # PER BIN would need n_enter/n_exit, which NOMAD doesn't record (see
    # module docstring); this is the best available per-bin proxy: total
    # occupancy and travel-time-ratio distribution per bin.
    timebin = bins.groupby("bin_start_s").agg(
        total_occupancy=("occupancy_veh", "sum"),
        n_active_edges=("edge_id", "nunique"),
        travel_time_ratio_mean=("travel_time_ratio", "mean"),
        travel_time_ratio_p95=("travel_time_ratio", lambda s: s.quantile(0.95)),
        frac_congested_gt_1_5=("travel_time_ratio", lambda s: (s > CONGESTED_TT_RATIO).mean()),
        congestion_mean=("congestion", "mean"),
        flow_uncapped_sum=("flow_veh_h_uncapped", "sum"),
    ).reset_index()
    timebin["occupancy_delta_vs_prev_bin"] = timebin["total_occupancy"].diff()
    timebin.insert(0, "scenario_key", scenario.key)
    timebin.insert(1, "demand_factor", factor)

    # road_class_metrics
    merged = bins.merge(city_edges_lengths(city), on="edge_id", how="left")
    rc_rows = []
    for rc, g in merged.groupby("road_class"):
        was_capped = g["flow_veh_h_uncapped"] > g["flow_veh_h"] + 1e-6
        rc_rows.append({
            "road_class": int(rc), "road_class_label": ROAD_CLASS_LABELS.get(int(rc), str(rc)),
            "n_rows": int(len(g)), "n_edges": int(g["edge_id"].nunique()),
            "occupancy_p50": float(g["occupancy_veh"].quantile(0.50)),
            "occupancy_p95": float(g["occupancy_veh"].quantile(0.95)),
            "occupancy_max": float(g["occupancy_veh"].max()),
            "flow_uncapped_p50": float(g["flow_veh_h_uncapped"].quantile(0.50)),
            "flow_uncapped_p95": float(g["flow_veh_h_uncapped"].quantile(0.95)),
            "frac_capacity_capped": float(was_capped.mean()),
            "travel_time_ratio_p50": float(g["travel_time_ratio"].quantile(0.50)),
            "travel_time_ratio_p95": float(g["travel_time_ratio"].quantile(0.95)),
            "travel_time_ratio_p99": float(g["travel_time_ratio"].quantile(0.99)),
            "frac_congested_gt_1_5": float((g["travel_time_ratio"] > CONGESTED_TT_RATIO).mean()),
        })
    road_class_metrics = pd.DataFrame(rc_rows).sort_values("road_class").reset_index(drop=True)
    road_class_metrics.insert(0, "scenario_key", scenario.key)
    road_class_metrics.insert(1, "demand_factor", factor)

    # BPR-active share: occ/storage_cap >= threshold, using each row's own edge length + class capacity
    defaults_by_rc = road_class_defaults.set_index("road_class")["default_capacity_veh_h"].to_dict()
    merged["capacity_veh_h_class_default"] = merged["road_class"].map(defaults_by_rc)
    merged["storage_cap_veh"] = merged.apply(
        lambda r: storage_cap_for_edge(float(r["length_m"]), float(r["capacity_veh_h_class_default"]))
        if pd.notna(r["length_m"]) and pd.notna(r["capacity_veh_h_class_default"]) else np.nan, axis=1)
    merged["vc_ratio"] = merged["occupancy_veh"] / merged["storage_cap_veh"]
    frac_bpr_active = float((merged["vc_ratio"] >= BPR_ACTIVE_VC_THRESHOLD).mean())

    # mass_conservation: whole-run identity (per-bin needs n_enter/n_exit, not available)
    mass_conservation = {
        "scenario_key": scenario.key, "demand_factor": factor,
        "depart": run_audit.get("depart"),
        "final_waiting": run_audit.get("final_waiting"),
        "final_on_link": run_audit.get("final_on_link"),
        "final_at_activity": run_audit.get("final_at_activity"),
        "final_arrived": run_audit.get("final_arrived"),
        "states_sum": run_audit.get("mass_conservation_states_sum"),
        "conserved": run_audit.get("mass_conservation_ok"),
    }

    scenario_row = {
        "scenario_key": scenario.key, "demand_factor": factor,
        "status": run_audit.get("status"),
        "agents_generated": run_audit.get("agents_generated"),
        "routed": run_audit.get("routed"), "routing_failed": run_audit.get("routing_failed"),
        "depart": run_audit.get("depart"), "arrive": run_audit.get("arrive"),
        "teleported": run_audit.get("teleported"),
        "arrival_rate": run_audit.get("arrival_rate"),
        "natural_arrival_rate": run_audit.get("natural_arrival_rate"),
        "teleported_rate": run_audit.get("teleported_rate"),
        "mass_conservation_ok": run_audit.get("mass_conservation_ok"),
        "total_time_s": run_audit.get("total_time_s"),
        "peak_rss_mb": run_audit.get("peak_rss_mb"),
        "n_edge_bins": int(len(bins)),
        "travel_time_ratio_p50": float(bins["travel_time_ratio"].quantile(0.50)),
        "travel_time_ratio_p95": float(bins["travel_time_ratio"].quantile(0.95)),
        "travel_time_ratio_p99": float(bins["travel_time_ratio"].quantile(0.99)),
        "frac_congested_gt_1_5": float((bins["travel_time_ratio"] > CONGESTED_TT_RATIO).mean()),
        "frac_any_congestion": float((bins["congestion"] > 0).mean()),
        "frac_bpr_active_vc_ge_0_8": frac_bpr_active,
        "frac_capacity_capped": float((bins["flow_veh_h_uncapped"] > bins["flow_veh_h"] + 1e-6).mean()),
    }

    return {"timebin_metrics": timebin, "road_class_metrics": road_class_metrics,
            "mass_conservation": mass_conservation, "scenario_summary_row": scenario_row}


_EDGE_LENGTH_CACHE: dict[str, pd.DataFrame] = {}


def city_edges_lengths(city: CityConfig) -> pd.DataFrame:
    if city.slug not in _EDGE_LENGTH_CACHE:
        _EDGE_LENGTH_CACHE[city.slug] = pd.read_parquet(city.edges_parquet)[["edge_id", "length_m", "road_class"]].drop(
            columns=["road_class"])
    return _EDGE_LENGTH_CACHE[city.slug]


def classify_outcome(summary: pd.DataFrame) -> str:
    """Rule-based, explicit thresholds (heuristic, not a validated model) --
    see module docstring for the underlying diagnosis this follows from.
    Order matters: checked top to bottom, first match wins."""
    if "status" not in summary.columns or (summary["status"] == "OK").sum() == 0:
        return "possibile bug: nessuno scenario completato con successo -- vedi failures"
    ok = summary[summary["status"] == "OK"].sort_values("demand_factor")
    if not ok["mass_conservation_ok"].fillna(False).all():
        return "possibile bug: identita' di conservazione agenti violata in almeno uno scenario (depart != somma stati finali)"
    max_factor_row = ok.iloc[-1]
    teleport_range = ok["teleported_rate"].max() - ok["teleported_rate"].min()
    max_congestion = ok["frac_congested_gt_1_5"].max()
    # Checked BEFORE "baseline sottocaricata": a teleport rate that climbs
    # substantially with demand is itself evidence that something IS
    # escalating (agents being discarded), even if congestion/BPR-active
    # metrics never move -- distinct from a truly flat, nothing-happening
    # baseline.
    if teleport_range > 0.05 and max_congestion < 0.01:
        return ("crescita solo teleport: all'aumentare della domanda il teleport rate cresce "
                f"(range {teleport_range:.1%}) ma la quota di bin congestionati (travel_time_ratio>1.5) "
                "resta sotto l'1% -- gli agenti vengono scartati (teleport) invece di mostrare "
                "code/congestione reale nella relazione flusso-densita'")
    if max_factor_row["frac_congested_gt_1_5"] < 0.01 and max_factor_row["frac_bpr_active_vc_ge_0_8"] < 0.01:
        return ("baseline sottocaricata: anche al fattore di domanda piu' alto testato "
                f"({max_factor_row['demand_factor']:g}x) meno dell'1% dei bin arco-tempo raggiunge "
                "travel_time_ratio>1.5 o v/c>=0.8 -- la domanda non si avvicina alla capacita' da nessuna parte")
    if max_factor_row["frac_bpr_active_vc_ge_0_8"] > 0.05 and max_factor_row["frac_congested_gt_1_5"] > 0.02:
        return ("limite QueueTrafficModel: la domanda raggiunge davvero la soglia critica (v/c>=0.8) su una "
                "quota non trascurabile di archi e la congestione cresce con la domanda, ma senza un "
                "meccanismo di discharge/spillback (flow_cap_per_s=0, vedi queue_model.cpp) NOMAD non puo' "
                "propagare la coda a monte -- limite noto e gia' identificato del modello di default, non un bug")
    return ("capacita' eccessive: la domanda cresce e genera qualche congestione locale ma non abbastanza da "
            "classificarsi nettamente in una delle categorie sopra -- ispezionare road_class_metrics.parquet "
            "per le classi/archi specifici coinvolti")


# ---------------------------------------------------------------------------
# Step 6: report + figures
# ---------------------------------------------------------------------------
def make_figures(summary: pd.DataFrame, road_class_metrics: pd.DataFrame, out_dir: Path) -> None:
    ok = summary[summary["status"] == "OK"].sort_values("demand_factor")
    if len(ok) == 0:
        return

    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.5))
    axes[0].plot(ok["demand_factor"], ok["frac_congested_gt_1_5"], marker="o", color="#D55E00")
    axes[0].set_xlabel("demand factor"); axes[0].set_ylabel("frac. edge-bins travel_time_ratio>1.5")
    axes[0].set_title("congestion vs demand factor")
    axes[1].plot(ok["demand_factor"], ok["teleported_rate"], marker="o", color="#0072B2")
    axes[1].set_xlabel("demand factor"); axes[1].set_ylabel("teleported_rate")
    axes[1].set_title("teleport vs demand factor")
    fig.tight_layout()
    fig.savefig(out_dir / "stress_summary.png", dpi=200)
    fig.savefig(out_dir / "stress_summary.pdf")
    plt.close(fig)

    if len(road_class_metrics) > 0:
        fig2, ax2 = plt.subplots(figsize=(5.0, 4.0))
        cmap = plt.get_cmap("plasma")
        for rc, g in road_class_metrics.groupby("road_class"):
            g = g.sort_values("demand_factor")
            ax2.plot(g["demand_factor"], g["frac_congested_gt_1_5"],
                     color=cmap(int(rc) / 13), label=ROAD_CLASS_LABELS.get(int(rc), str(rc)), linewidth=1.1)
        ax2.set_xlabel("demand factor"); ax2.set_ylabel("frac. edge-bins travel_time_ratio>1.5")
        ax2.legend(fontsize=6, loc="center left", bbox_to_anchor=(1.02, 0.5))
        ax2.set_title("congestion by road class")
        fig2.tight_layout()
        fig2.savefig(out_dir / "stress_by_road_class.png", dpi=200, bbox_inches="tight")
        fig2.savefig(out_dir / "stress_by_road_class.pdf", bbox_inches="tight")
        plt.close(fig2)


def _df_to_markdown(df: pd.DataFrame) -> str:
    """Dependency-free replacement for DataFrame.to_markdown() (which needs
    the optional `tabulate` package, not installed in every env this script
    runs in -- e.g. the cluster's `nomad` conda env)."""
    if len(df) == 0:
        return ""
    cols = [str(c) for c in df.columns]
    rows = [[("" if pd.isna(v) else str(v)) for v in row] for row in df.itertuples(index=False)]
    widths = [max(len(cols[i]), *(len(r[i]) for r in rows)) if rows else len(cols[i])
              for i in range(len(cols))]
    def fmt_row(vals: list[str]) -> str:
        return "| " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(vals)) + " |"
    lines = [fmt_row(cols), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines += [fmt_row(r) for r in rows]
    return "\n".join(lines)


def write_diagnostic_report(city: CityConfig, baseline_audit: dict, summary: pd.DataFrame,
                              mass_conservation: pd.DataFrame, conclusion: str, failures: list[dict],
                              out_path: Path) -> None:
    ok = summary[summary["status"] == "OK"].sort_values("demand_factor")
    lines = [
        f"# NOMAD car traffic-model validation -- {city.slug}",
        "",
        f"Generated by `scripts/diagnostics/run_nomad_traffic_validation.py`.",
        "",
        "## Baseline audit (no simulation)",
        "```json",
        json.dumps(baseline_audit.get("od_demand", {}), indent=2, default=str)[:2000],
        "```",
        "",
        "## Stress-test scenario summary",
        "",
        _df_to_markdown(ok) if len(ok) else "*(no scenario completed successfully)*",
        "",
        "## Mass conservation (depart == final_waiting+final_on_link+final_at_activity+final_arrived)",
        "",
        _df_to_markdown(mass_conservation) if len(mass_conservation) else "*(none)*",
        "",
        "## Failures",
        "",
    ]
    if failures:
        for f in failures:
            lines.append(f"- **{f.get('scenario_key')}**: {f.get('error')}")
    else:
        lines.append("*(none)*")
    lines += [
        "",
        "## Automatic conclusion",
        "",
        f"> {conclusion}",
        "",
        "This classification is rule-based (explicit thresholds in `classify_outcome()`), "
        "not a statistical/validated model -- inspect `scenario_summary.parquet`, "
        "`road_class_metrics.parquet` and the figures before treating it as final.",
    ]
    out_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Step 7: optional single-link/corridor test
# ---------------------------------------------------------------------------
def find_dead_end_edge(city: CityConfig) -> dict[str, Any] | None:
    """A genuine dead-end: a node with total car-network degree 1 (in+out
    edges combined). Its single incoming edge has NO alternative route for
    traffic terminating there -- real, computed from edges.parquet, not
    assumed. Only that LAST edge is guaranteed alternative-free; the rest of
    the path from the chosen origin may have alternatives (documented, not
    overclaimed)."""
    edges = pd.read_parquet(city.edges_parquet)
    nodes = pd.read_parquet(city.nodes_parquet)
    degree = pd.concat([edges["from_node"], edges["to_node"]]).value_counts()
    dead_ends = degree[degree == 1].index
    if len(dead_ends) == 0:
        return None
    # Prefer a dead-end whose single edge is Residential/Service/Unclassified
    # (car_exclude-safe, low-capacity -- easiest to saturate) and reasonably
    # long (avoid the < 50m effective-length floor swallowing the test).
    candidates = edges[(edges["to_node"].isin(dead_ends)) & (edges["length_m"] >= 50.0) &
                        (edges["road_class"].isin([10, 12, 13]))]
    if len(candidates) == 0:
        candidates = edges[edges["to_node"].isin(dead_ends)]
        if len(candidates) == 0:
            return None
    chosen = candidates.sort_values("length_m", ascending=False).iloc[0]
    dest_node = int(chosen["to_node"])
    # Origin: the farthest node from the dead-end among nodes with degree>1
    # on a Primary/Secondary class (a plausible, real approach corridor),
    # picked by straight-line distance only (no routing call here -- NOMAD's
    # own router decides the actual path when the scenario runs).
    dest_row = nodes[nodes["node_id"] == dest_node].iloc[0]
    approach_edges = edges[edges["road_class"].isin([4, 6])]
    approach_nodes = pd.concat([approach_edges["from_node"], approach_edges["to_node"]]).unique()
    cand_nodes = nodes[nodes["node_id"].isin(approach_nodes)]
    if len(cand_nodes) == 0:
        return None
    dist2 = (cand_nodes["lon"] - dest_row["lon"]) ** 2 + (cand_nodes["lat"] - dest_row["lat"]) ** 2
    origin_node = int(cand_nodes.loc[dist2.idxmax(), "node_id"])
    return {"dead_end_edge_id": int(chosen["edge_id"]), "dest_node": dest_node, "origin_node": origin_node,
            "edge_length_m": float(chosen["length_m"]), "edge_road_class": int(chosen["road_class"]),
            "road_class_label": ROAD_CLASS_LABELS.get(int(chosen["road_class"]))}


def build_single_link_od(city: CityConfig, edge_info: dict, inflow_levels: tuple[int, ...],
                          out_dir: Path) -> list[dict]:
    """One synthetic OD CSV per inflow level -- real OD schema
    (origin_node,dest_node,count,mode,depart_mean_s,depart_std_s), all
    demand concentrated at t=0 with depart_std_s=0 for a genuine step-inflow
    test, not NOMAD's usual smoothed daily profile."""
    written = []
    for count in inflow_levels:
        path = out_dir / f"single_link_od_{city.slug}_{edge_info['dead_end_edge_id']}_{count}.csv"
        pd.DataFrame([{
            "origin_node": edge_info["origin_node"], "dest_node": edge_info["dest_node"],
            "count": int(count), "mode": "car", "depart_mean_s": 0.0, "depart_std_s": 0.0,
        }]).to_csv(path, index=False)
        written.append({"count": count, "od_csv": str(path)})
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--city", default="palma_de_mallorca")
    p.add_argument("--scenario", default=BASE_CAR_SCENARIO_KEY,
                   help="base car scenario key to stress-test (default: %(default)s)")
    p.add_argument("--factors", default=",".join(str(f) for f in DEFAULT_FACTORS),
                   help="comma-separated demand factors, e.g. 0.5,1.0,2.0,4.0 (default: %(default)s)")
    p.add_argument("--output-dir", default=None,
                   help="default: reports/diagnostics/nomad_traffic_validation/<city>/")
    p.add_argument("--workers", type=int, default=1, help="parallel scenario runs (default: 1)")
    p.add_argument("--force", action="store_true", help="re-run scenarios even if valid output exists")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would run (configs, OD scaling, output paths) without launching anything")
    p.add_argument("--seed", type=int, default=NOMAD_ODCSV_SEED,
                   help="recorded for provenance only -- NOMAD's od_csv demand path hardcodes "
                        "seed=42 (nomad-cli/main.cpp:198) and does not accept an override; "
                        "see module docstring")
    p.add_argument("--single-link-test", action="store_true",
                   help="also build (but, per --dry-run, not necessarily run) an escalating-inflow "
                        "single-corridor test on an automatically-detected dead-end edge")
    p.add_argument("--single-link-inflow", default="10,50,100,300,600",
                   help="comma-separated agent counts for --single-link-test (default: %(default)s)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        cities = load_all_cities()
    except ConfigError as exc:
        raise SystemExit(f"config error loading cities: {exc}") from exc
    if args.city not in cities:
        raise SystemExit(f"unknown city '{args.city}', configured: {sorted(cities)}")
    city = cities[args.city]
    if args.scenario not in city.scenarios:
        raise SystemExit(f"unknown scenario '{args.scenario}' for {city.slug}, "
                          f"configured: {sorted(city.scenarios)}")
    base_scenario = city.scenarios[args.scenario]

    try:
        factors = tuple(float(x) for x in args.factors.split(","))
    except ValueError as exc:
        raise SystemExit(f"--factors must be comma-separated numbers, got '{args.factors}': {exc}") from exc
    if any(f <= 0 for f in factors):
        raise SystemExit(f"--factors must all be > 0, got {factors}")

    out_dir = Path(args.output_dir) if args.output_dir else (
        REPO_ROOT / "reports" / "diagnostics" / "nomad_traffic_validation" / city.slug
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{city.slug}] auditing existing baseline ({base_scenario.key})...")
    baseline_audit = audit_baseline(city, base_scenario, out_dir)
    print(json.dumps({k: v for k, v in baseline_audit.items() if k != "road_class_capacity_table"},
                      indent=2, default=str)[:3000])

    road_class_defaults = load_road_class_defaults()

    scenarios = [(f, build_stress_scenario(city, base_scenario, f, materialize=not args.dry_run)) for f in factors]

    if args.dry_run:
        print(f"\n[dry-run] would process {len(scenarios)} scenario(s):")
        for factor, sc in scenarios:
            from green_mobility.nomad_wrapper.runner import ScenarioRun
            run = ScenarioRun(city, sc)
            print(f"  factor={factor:g}x key={sc.key} od_label={sc.od_label} "
                  f"demand_scale={sc.demand_scale} -> {run.output_dir}")
        if args.single_link_test:
            edge_info = find_dead_end_edge(city)
            print(f"\n[dry-run] single-link test would use: {edge_info}")
        print("\n[dry-run] no simulation launched; no scenario config or scaled-OD input files written. "
              "(The read-only baseline audit under reports/diagnostics/ above IS written in --dry-run too "
              "-- it only summarizes data already on disk, nothing simulation-related.)")
        return 0

    run_results, failures = [], []
    if args.workers > 1:
        print(f"[{city.slug}] running {len(scenarios)} scenario(s) with {args.workers} parallel worker(s)...")
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            future_to_scenario = {
                pool.submit(run_scenario, city, sc, args.force, False): (factor, sc)
                for factor, sc in scenarios
            }
            for fut in as_completed(future_to_scenario):
                factor, sc = future_to_scenario[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # worker process crashed outright -- still recorded, not raised
                    res = {"scenario_key": sc.key, "status": "FAILED", "error": f"worker crashed: {exc}"}
                run_results.append((factor, sc, res))
                if res["status"] == "FAILED":
                    failures.append({"scenario_key": sc.key, "error": res.get("error")})
                    print(f"  [{sc.key}] FAILED: {res.get('error')}")
                else:
                    print(f"  [{sc.key}] {res['status']}")
    else:
        for factor, sc in scenarios:
            print(f"[{city.slug}] scenario factor={factor:g}x ({sc.key})...")
            res = run_scenario(city, sc, args.force, dry_run=False)
            run_results.append((factor, sc, res))
            if res["status"] == "FAILED":
                failures.append({"scenario_key": sc.key, "error": res.get("error")})
                print(f"  FAILED: {res.get('error')}")
            else:
                print(f"  {res['status']}")

    timebin_frames, road_class_frames, mass_rows, summary_rows = [], [], [], []
    for factor, sc, res in run_results:
        if res["status"] not in ("OK", "SKIPPED_EXISTING"):
            continue
        try:
            metrics = extract_scenario_metrics(city, sc, factor, road_class_defaults)
        except Exception as exc:
            failures.append({"scenario_key": sc.key, "error": f"metrics extraction failed: {exc}"})
            continue
        timebin_frames.append(metrics["timebin_metrics"])
        road_class_frames.append(metrics["road_class_metrics"])
        mass_rows.append(metrics["mass_conservation"])
        row = metrics["scenario_summary_row"]
        row["status"] = "OK"
        summary_rows.append(row)
    for f in failures:
        if not any(r.get("scenario_key") == f["scenario_key"] for r in summary_rows):
            summary_rows.append({"scenario_key": f["scenario_key"], "status": "FAILED"})

    summary = pd.DataFrame(summary_rows)
    timebin_metrics = pd.concat(timebin_frames, ignore_index=True) if timebin_frames else pd.DataFrame()
    road_class_metrics = pd.concat(road_class_frames, ignore_index=True) if road_class_frames else pd.DataFrame()
    mass_conservation = pd.DataFrame(mass_rows)

    summary.to_parquet(out_dir / "scenario_summary.parquet", index=False)
    summary.to_csv(out_dir / "scenario_summary.csv", index=False)
    timebin_metrics.to_parquet(out_dir / "timebin_metrics.parquet", index=False)
    road_class_metrics.to_parquet(out_dir / "road_class_metrics.parquet", index=False)
    mass_conservation.to_parquet(out_dir / "mass_conservation.parquet", index=False)

    conclusion = classify_outcome(summary) if "status" in summary.columns and (summary["status"] == "OK").any() else \
        "possibile bug: nessuno scenario completato con successo -- vedi failures"
    make_figures(summary, road_class_metrics, out_dir)
    write_diagnostic_report(city, baseline_audit, summary, mass_conservation, conclusion, failures,
                             out_dir / "diagnostic_report.md")

    if args.single_link_test:
        edge_info = find_dead_end_edge(city)
        if edge_info is None:
            print("[single-link-test] no dead-end edge found for this city's car network")
        else:
            inflow_levels = tuple(int(x) for x in args.single_link_inflow.split(","))
            written = build_single_link_od(city, edge_info, inflow_levels, out_dir / "single_link_test")
            (out_dir / "single_link_test" / "edge_info.json").write_text(json.dumps(edge_info, indent=2))
            print(f"[single-link-test] built {len(written)} synthetic OD files for edge_id="
                  f"{edge_info['dead_end_edge_id']} ({edge_info['road_class_label']}, "
                  f"{edge_info['edge_length_m']:.0f}m) -- NOT run (see --dry-run scope); "
                  "to actually run these, point a ScenarioSpec at each OD file the same way "
                  "build_stress_scenario does, then call run_scenario().")

    print(f"\n[{city.slug}] conclusion: {conclusion}")
    print(f"[{city.slug}] report: {out_dir / 'diagnostic_report.md'}")
    if failures:
        print(f"[{city.slug}] {len(failures)} scenario(s) failed -- see diagnostic_report.md")
    return 1 if failures and not any(r.get("status") == "OK" for r in summary_rows) else 0


if __name__ == "__main__":
    sys.exit(main())
