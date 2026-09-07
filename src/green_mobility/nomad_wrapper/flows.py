"""Per-edge, per-hour, per-mode flow extraction.

Why this exists: NOMAD's built-in instrumentation (GeoJsonWriter / on_snapshot,
Simulation.link_states()) is car-only — `QueueTrafficModel::on_enter/on_exit`
is only invoked when `hot_.mode[a] == AgentMode::Car`
(external/nomad/src/core/simulation.cpp:263,291), so walk/bike agents never
update occupancy/inflow/outflow, full stop. But `AgentEnterLink` *events*
(payload = edge id) fire for every agent regardless of mode
(src/core/simulation.cpp:241,306) and are exposed to Python via
`Simulation.register_hook(...)` (bindings/pynomad.cpp:290-297) — counting
those per (edge, hour) for a mode-isolated run (`OdMatrixDemand.from_csv(...,
modes=[mode])`) is a correct, non-invasive way to get real per-mode flow.

STATUS: unblocked on external/nomad's `feature/python-multimodal-bindings`
branch (not yet merged to `main`). Running a simulation from Python also
requires `Simulation.set_router(...)` (walk/bike need `AStarRouter`;
`CHRouter` ignores mode entirely per the NOMAD README) and
`set_traffic_model(...)` — both are now bound in bindings/pynomad.cpp, along
with `AStarRouter`/`CHRouter`/`QueueTrafficModel`/`LtmTrafficModel`
themselves and explicit `has_router`/`has_traffic_model` checks that make
`run()`/`run_until()`/`step()` raise immediately instead of silently no-op'ing
on a null router (`simulation.cpp`: `if (!router_ || n == 0) return;`). See
external/nomad's tests/python/test_multimodal_bindings.py for the bindings'
own verification.
`assert_flow_extraction_available()` below still runs its `hasattr` checks
before every extraction — if `external/nomad`'s submodule pointer here is
ever moved back to a pre-fix commit (e.g. `main` before this branch merges),
it will go back to failing loudly instead of silently returning empty/zero
flow, exactly as before.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from green_mobility.config import CityConfig, ScenarioSpec
from green_mobility.nomad_wrapper.paths import require_nomad_python

HOURS_PER_DAY = 25  # 0..23 plus an overflow bin for events at/after 24:00:00


class NomadCapabilityError(RuntimeError):
    pass


def assert_flow_extraction_available() -> None:
    """Raise NomadCapabilityError immediately if the currently-built NOMAD
    Python module can't actually route agents, instead of letting callers
    silently get an empty/zero flow table back."""
    require_nomad_python()
    import nomad._nomad_core as core

    missing_methods = [
        name
        for name in ("set_router", "set_traffic_model")
        if not hasattr(core.Simulation, name)
    ]
    missing_classes = [
        name
        for name in ("AStarRouter", "CHRouter", "QueueTrafficModel", "LtmTrafficModel")
        if not hasattr(core, name)
    ]
    if missing_methods or missing_classes:
        raise NomadCapabilityError(
            "Per-mode flow extraction needs a router and a traffic model "
            "attached to Simulation from Python. external/nomad's current "
            "pybind11 bindings (bindings/pynomad.cpp) expose neither the "
            f"setters ({missing_methods or 'ok'}) nor the router/traffic-model "
            f"classes themselves ({missing_classes or 'ok'}) — only "
            "set_graph/set_demand/run/register_hook/link_states/"
            "add_geojson_writer and Graph/OsmLoader/OdMatrixDemand/"
            "GravityDemand are bound. Without a router, NOMAD's own "
            "pre-routing silently no-ops (simulation.cpp: "
            "`if (!router_ || n == 0) return;`) rather than erroring, so "
            "this check exists to fail loudly instead of returning a "
            "fabricated all-zero flow table. This is a real NOMAD capability "
            "gap, not a bug in this repo — see README.md 'Known NOMAD gaps' "
            "for the minimal patch that would unblock it. NOMAD is "
            "intentionally not modified by this repo."
        )


def _hour_bin(t_seconds: float) -> int:
    h = int(t_seconds // 3600)
    return min(max(h, 0), HOURS_PER_DAY - 1)


def extract_mode_flows(city: CityConfig, scenario: ScenarioSpec, mode: str) -> pd.DataFrame:
    """Run one mode-isolated simulation and return a per-edge-per-hour flow
    count for `mode`, via the AgentEnterLink hook described in the module
    docstring. Raises NomadCapabilityError if the external/nomad submodule
    checked out here predates feature/python-multimodal-bindings (see
    assert_flow_extraction_available).
    """
    assert_flow_extraction_available()
    import nomad._nomad_core as core

    graph = (
        core.Graph.load(str(city.graph_bin))
        if city.graph_bin.exists()
        else core.OsmLoader().load_and_clean(str(city.fua_clipped_osm_path), True)
    )

    sim_cfg = core.SimulationConfig()
    sim_cfg.start_time = _time_to_seconds(scenario.start_time)
    sim_cfg.end_time = _time_to_seconds(scenario.end_time)
    sim_cfg.traffic_model = scenario.traffic_model
    sim_cfg.router = "astar"  # required for walk/bike; also valid (if slower) for car

    sim = core.Simulation(sim_cfg)
    sim.set_graph(graph)
    # sim_cfg.router/.traffic_model (set above) are informational only — the
    # Simulation class never reads those string fields itself (only NOMAD's
    # CLI main.cpp does, to decide what to construct); the actual behavior
    # comes entirely from the objects attached here, which
    # assert_flow_extraction_available() already guaranteed exist.
    sim.set_traffic_model(_traffic_model(core, scenario, graph))
    sim.set_router(_router(core, graph))

    demand = core.OdMatrixDemand.from_csv(
        str(city.od_csv(scenario.od_label)),
        42,
        sim_cfg.start_time,
        sim_cfg.end_time,
        [mode],
    )
    sim.set_demand(demand)

    counts = np.zeros((graph.num_edges, HOURS_PER_DAY), dtype=np.int64)

    def on_enter_link(time_s: float, agent_id: int, edge_id: int) -> None:
        if edge_id < counts.shape[0]:
            counts[edge_id, _hour_bin(time_s)] += 1

    sim.register_hook("AgentEnterLink", on_enter_link)
    sim.run()

    edge_ids, hours = np.nonzero(counts)
    return pd.DataFrame(
        {
            "edge_id": edge_ids,
            "hour": hours,
            "mode": mode,
            "count": counts[edge_ids, hours],
        }
    )


def _time_to_seconds(t) -> float:
    return float(t.hour * 3600 + t.minute * 60 + t.second)


def _traffic_model(core, scenario: ScenarioSpec, graph):
    if scenario.traffic_model == "ltm":
        return core.LtmTrafficModel(graph)
    return core.QueueTrafficModel(graph)


def _router(core, graph):
    return core.AStarRouter(graph)


def extract_all_mode_flows(
    city: CityConfig, scenario: ScenarioSpec, modes: tuple[str, ...] | None = None
) -> pd.DataFrame:
    modes = modes or scenario.modes
    frames = [extract_mode_flows(city, scenario, m) for m in modes]
    out_dir = city.green_mobility_dir / "flows"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["edge_id", "hour", "mode", "count"]
    )
    result.to_parquet(out_dir / f"{scenario.key}_flows.parquet", index=False)
    return result
