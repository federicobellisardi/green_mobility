"""Materialize a NOMAD JSON scenario config from a (CityConfig, ScenarioSpec)
pair and run it through the nomad_cli binary.

Field layout mirrors external/nomad/data/schemas/scenario_palma_multimodal.json
exactly (audited field-by-field against ScenarioConfig in
include/nomad/config/scenario_config.hpp) — this module only fills in our own
per-city paths, it does not invent new NOMAD config fields.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from green_mobility.config import CityConfig, ScenarioSpec
from green_mobility.nomad_wrapper.paths import run_nomad_cli

# Parses nomad_cli's own spdlog output (tools/nomad-cli/main.cpp +
# src/core/simulation.cpp log lines) — not a NOMAD change, just reading its
# existing stdout so run stats land in our manifest instead of only a
# terminal scrollback.
_STAT_PATTERNS: dict[str, re.Pattern] = {
    "agents_generated": re.compile(r"OdMatrixDemand: generated (\d+) agents"),
    "routed": re.compile(r"Pre-routing done: (\d+)/(\d+) routed, (\d+) failed"),
    "pre_routing_time_s": re.compile(r"Pre-routing done:.*in ([\d.]+)s \((\d+) routes/s\)"),
    "simulation_time_s": re.compile(r"Simulation complete in ([\d.]+)s\.$"),
    "total_time_s_and_events": re.compile(r"Simulation complete in ([\d.]+)s\. Events processed: (\d+)"),
    "event_counts": re.compile(
        r"Event types: Depart=(\d+) Enter=(\d+) Exit=(\d+) Arrive=(\d+) Teleported=(\d+)"
        r"(?: DepartRejected=(\d+))?(?: PretripRerouted=(\d+))?"
    ),
    "final_states": re.compile(
        r"Final states: Waiting=(\d+) OnLink=(\d+) AtActivity=(\d+) Arrived=(\d+)"
    ),
}


def parse_nomad_cli_stats(stdout: str) -> dict:
    """Extract the run-quality numbers nomad_cli already logs (pre-routing
    rate, event counts, arrival/teleport counts) so they can be recorded in
    the manifest instead of only living in a terminal scrollback."""
    stats: dict = {}

    if m := _STAT_PATTERNS["agents_generated"].search(stdout):
        stats["agents_generated"] = int(m.group(1))

    if m := _STAT_PATTERNS["routed"].search(stdout):
        stats["routed"] = int(m.group(1))
        stats["routing_failed"] = int(m.group(3))

    if m := _STAT_PATTERNS["pre_routing_time_s"].search(stdout):
        stats["pre_routing_time_s"] = float(m.group(1))
        stats["pre_routing_routes_per_s"] = int(m.group(2))

    # Two distinct "Simulation complete in Xs" lines are logged: the inner
    # one (event-loop only) and the outer one (pre-routing + event loop,
    # with total event count) — see simulation.cpp / main.cpp.
    complete_lines = re.findall(r"Simulation complete in ([\d.]+)s\.(?: Events processed: (\d+))?", stdout)
    if len(complete_lines) >= 1:
        stats["simulation_time_s"] = float(complete_lines[0][0])
    if len(complete_lines) >= 2:
        stats["total_time_s"] = float(complete_lines[1][0])
        if complete_lines[1][1]:
            stats["events_processed"] = int(complete_lines[1][1])

    if m := _STAT_PATTERNS["event_counts"].search(stdout):
        stats["depart"], stats["enter"], stats["exit"], stats["arrive"], stats["teleported"] = (
            int(x) for x in m.groups()[:5]
        )
        # DepartRejected/PretripRerouted are optional groups (older nomad_cli
        # builds don't log them, and PretripRerouted only appears once the
        # pre-trip-reroute feature has landed) -- only recorded when present,
        # never silently defaulted to 0 (which would look like a real zero
        # count from a run that actually just predates the field).
        if m.group(6):
            stats["depart_rejected"] = int(m.group(6))
        if m.group(7):
            stats["pretrip_rerouted"] = int(m.group(7))
    if m := _STAT_PATTERNS["final_states"].search(stdout):
        stats["final_waiting"], stats["final_on_link"], stats["final_at_activity"], stats["final_arrived"] = (
            int(x) for x in m.groups()
        )

    if "final_arrived" in stats and "depart" in stats and stats["depart"] > 0:
        stats["arrival_rate"] = round(stats["final_arrived"] / stats["depart"], 4)
    if "arrive" in stats and "depart" in stats and stats["depart"] > 0:
        stats["natural_arrival_rate"] = round(stats["arrive"] / stats["depart"], 4)
    if "teleported" in stats and "depart" in stats and stats["depart"] > 0:
        stats["teleported_rate"] = round(stats["teleported"] / stats["depart"], 4)

    return stats


def _fmt_hm(seconds_since_midnight: float) -> str:
    """Mirrors fmt_hm() in external/nomad/tools/nomad-cli/main.cpp — needed to
    predict the timestamped output subdirectory nomad_cli creates
    (output_dir/{sim_date}_{HH-MM}_{HH-MM}/) without parsing its stdout."""
    total_min = int(seconds_since_midnight) // 60
    return f"{total_min // 60:02d}-{total_min % 60:02d}"


def _time_to_seconds(t) -> float:
    return t.hour * 3600 + t.minute * 60 + t.second


def expected_output_subdir(scenario: ScenarioSpec) -> str:
    start_s = _time_to_seconds(scenario.start_time)
    end_s = _time_to_seconds(scenario.end_time)
    return f"{scenario.date.isoformat()}_{_fmt_hm(start_s)}_{_fmt_hm(end_s)}"


@dataclass(frozen=True)
class ScenarioRun:
    """Everything needed to locate a scenario's materialized config and output."""

    city: CityConfig
    scenario: ScenarioSpec

    @property
    def config_dir(self) -> Path:
        return self.city.green_mobility_dir / "scenario_configs"

    @property
    def config_path(self) -> Path:
        return self.config_dir / f"{self.scenario.key}.json"

    @property
    def base_output_dir(self) -> Path:
        return self.city.results_dir / self.scenario.key

    @property
    def output_dir(self) -> Path:
        return self.base_output_dir / expected_output_subdir(self.scenario)

    def build_config_dict(self) -> dict:
        city, sc = self.city, self.scenario
        return {
            "name": f"{city.slug}_{sc.key}",
            "version": "0.1",
            "simulation": {
                "start_time": sc.start_datetime_str,
                "end_time": sc.end_datetime_str,
                "sync_window_s": 30.0,
                "reroute_thresh": 0.20,
                "reroute_interval_s": 300.0,
                # BUG FOUND while extending to 12 cities: the previous
                # defaults (20.0 / 4.0) were implicitly tuned against Palma
                # de Mallorca (the only city validated before this project's
                # 12-city extension) and did not generalize -- Zaragoza's
                # otherwise-identical car-only baseline showed a 23.14%
                # teleport rate at these defaults, vs 1.28% for Palma, with
                # BOTH available NOMAD traffic models (QueueTrafficModel AND
                # LtmTrafficModel, which share the same underlying BPR delay
                # formula) producing near-identical results -- ruling out
                # the traffic model itself. A direct sensitivity test on the
                # same Zaragoza scenario confirmed this is a threshold
                # miscalibration, not a routing/network defect: relaxing to
                # stuck_threshold_ratio=200/stuck_max_hours=24 eliminated
                # teleportation entirely (0 of 599,230 agents), and this
                # more moderate 50/8 pair (2.5x/2x more lenient) already
                # brings it to 15 agents (0.0025%) -- i.e. those trips were
                # genuinely completable, just needed more elapsed time than
                # the tight default allowed before being cut short. No NOMAD
                # source change was needed or made; this is a config-only
                # fix in this wrapper.
                "stuck_threshold_ratio": 50.0,
                "teleport_interval_s": 300.0,
                "max_reroutes": 10,
                "stuck_max_hours": 8.0,
                "enable_pretrip_reroute": sc.enable_pretrip_reroute,
                "num_threads": 0,
                "store_traces": False,
                "traffic_model": sc.traffic_model,
                "router": sc.router,
                "snapshot_interval_s": 900,
            },
            "network": {
                "osm_pbf": str(city.fua_clipped_osm_path),
                "graph_bin": str(city.graph_bin),
                "simplify_topology": True,
                "extract_transit": False,
            },
            "routing": {
                "algorithm": sc.router,
                "preprocess_ch": sc.router.upper() == "CH",
                **(
                    {"ch_cache": str(self.city.city_dir / "ch.bin")}
                    if sc.router.upper() == "CH"
                    else {}
                ),
                "cache_entries": 500_000,
                "use_traffic_costs": True,
                "randomization_sigma": 0.08,
            },
            "traffic": {
                "model": sc.traffic_model,
                "ltm_discharge_cap": sc.ltm_discharge_cap,
                "ltm_discharge_burst_s": sc.ltm_discharge_burst_s,
            },
            "demand": {
                "source": "od_csv",
                "od_csv": str(city.od_csv(sc.od_label)),
                "modes": list(sc.modes),
                "demand_scale": sc.demand_scale,
            },
            "output": {
                "output_dir": str(self.base_output_dir),
                "writers": ["geojson"],
                "snapshot_interval_s": 900,
                "store_trajectories": False,
                "store_link_stats": True,
            },
        }

    def write_config(self) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.build_config_dict(), indent=2))
        return self.config_path

    def run(self):
        """Write the scenario JSON and invoke nomad_cli. Produces the
        car-only-valid congestion/travel-time GeoJSON baseline (see README
        "Known NOMAD gaps" for why this output is car-only regardless of
        `modes` — use nomad_wrapper.flows for walk/bike flow counts)."""
        config_path = self.write_config()
        result = run_nomad_cli(config_path)
        return result
