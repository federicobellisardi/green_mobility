"""Unit tests for scripts/diagnostics/run_nomad_traffic_validation.py.

scripts/ is not an installed package (see scripts/compute_canopy_v2.py's own
sys.path-insert pattern for precedent) -- imported the same way here rather
than adding a new packaging mechanism.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "diagnostics"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import run_nomad_traffic_validation as m  # noqa: E402
from green_mobility.config import CityConfig, ScenarioSpec  # noqa: E402


def make_city(slug="test_city"):
    return CityConfig(
        name="Test City",
        slug=slug,
        country="ES",
        lat=39.0,
        lon=2.0,
        geofabrik_url="https://example.com/x.osm.pbf",
        geofabrik_raw_filename="x.osm.pbf",
        mitma_months=("2022-02",),
        demand_occupancy_factor=1.2,
        demand_noise_sigma=0.08,
        scenarios={
            "weekday_full_car": ScenarioSpec(
                key="weekday_full_car",
                date=dt.date(2022, 2, 8),
                start_time=dt.time(0, 0, 0),
                end_time=dt.time(23, 59, 59),
                modes=("car",),
                od_label="weekday",
                router="astar",
                traffic_model="queue",
                demand_scale=1.0,
            )
        },
    )


# ---------------------------------------------------------------------------
# Road-class defaults (loaded from the real YAML -- these must match it)
# ---------------------------------------------------------------------------
def test_load_road_class_defaults_matches_yaml():
    df = m.load_road_class_defaults()
    assert len(df) == 14
    residential = df[df["road_class"] == 10].iloc[0]
    assert residential["road_class_label"] == "Residential"
    assert residential["default_lanes"] == 1
    assert residential["capacity_per_lane_veh_h"] == pytest.approx(600.0)
    assert residential["default_capacity_veh_h"] == pytest.approx(600.0)

    motorway = df[df["road_class"] == 0].iloc[0]
    assert motorway["default_lanes"] == 2
    assert motorway["default_capacity_veh_h"] == pytest.approx(4000.0)


def test_storage_cap_matches_hand_derivation_for_representative_residential_edge():
    # Same numbers verified against queue_model.cpp analytically earlier this
    # session: length=63m, capacity=600 -> estimated_lanes=1, eff_len=63,
    # storage_cap = 63 * 1 * (1/7.5) = 8.4.
    cap = m.storage_cap_for_edge(length_m=63.0, capacity_veh_h=600.0)
    assert cap == pytest.approx(8.4, abs=0.05)


def test_storage_cap_uses_50m_effective_length_floor():
    short_edge = m.storage_cap_for_edge(length_m=10.0, capacity_veh_h=600.0)
    at_floor = m.storage_cap_for_edge(length_m=50.0, capacity_veh_h=600.0)
    assert short_edge == pytest.approx(at_floor)


def test_estimated_lanes_floors_at_one():
    assert m.estimated_lanes_for_capacity(600.0) == pytest.approx(1.0)  # 600/1600 < 1
    assert m.estimated_lanes_for_capacity(3200.0) == pytest.approx(2.0)  # 3200/1600 == 2


# ---------------------------------------------------------------------------
# OD scaling (factor > 1.0 path -- the only path that touches the filesystem)
# ---------------------------------------------------------------------------
def test_write_scaled_od_csv_multiplies_count_and_filters_modes(tmp_path):
    base = tmp_path / "od_base.csv"
    pd.DataFrame([
        {"origin_node": 1, "dest_node": 2, "count": 10, "mode": "car",
         "depart_mean_s": 0.0, "depart_std_s": 3600.0},
        {"origin_node": 3, "dest_node": 4, "count": 5, "mode": "walk",
         "depart_mean_s": 0.0, "depart_std_s": 3600.0},
    ]).to_csv(base, index=False)

    out = tmp_path / "od_scaled.csv"
    info = m.write_scaled_od_csv(base, modes=("car",), factor=2.0, out_path=out)

    scaled = pd.read_csv(out)
    assert len(scaled) == 1  # walk row dropped -- scenario is car-only
    assert scaled.iloc[0]["count"] == 20
    assert info["count_sum"] == 20


def test_write_scaled_od_csv_never_rounds_a_positive_count_to_zero(tmp_path):
    base = tmp_path / "od_base.csv"
    pd.DataFrame([{"origin_node": 1, "dest_node": 2, "count": 1, "mode": "car",
                    "depart_mean_s": 0.0, "depart_std_s": 0.0}]).to_csv(base, index=False)
    out = tmp_path / "od_scaled.csv"
    m.write_scaled_od_csv(base, modes=("car",), factor=0.1, out_path=out)
    # (this factor path isn't used in practice for factor<=1 -- demand_scale
    # handles that -- but the helper itself must stay safe if ever called directly)
    assert pd.read_csv(out).iloc[0]["count"] >= 1


# ---------------------------------------------------------------------------
# Stress-scenario construction: factor<=1 uses demand_scale, factor>1 uses a
# scaled OD file, and materialize=False must never touch the filesystem.
# ---------------------------------------------------------------------------
def test_build_stress_scenario_factor_le_1_uses_demand_scale():
    city = make_city()
    base = city.scenarios["weekday_full_car"]
    sc = m.build_stress_scenario(city, base, 0.5, materialize=False)
    assert sc.demand_scale == pytest.approx(0.5)
    assert sc.od_label == "weekday"  # unchanged base OD
    assert sc.key == "weekday_full_car_stress_0.5x"


def test_build_stress_scenario_factor_gt_1_uses_scaled_od_label():
    city = make_city()
    base = city.scenarios["weekday_full_car"]
    sc = m.build_stress_scenario(city, base, 2.0, materialize=False)
    assert sc.demand_scale == pytest.approx(1.0)  # no double-scaling
    assert sc.od_label == "weekday_stress_2x"
    assert sc.key == "weekday_full_car_stress_2x"


def test_build_stress_scenario_materialize_false_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("green_mobility.config.DATA_DIR", tmp_path)
    city = make_city()
    base = city.scenarios["weekday_full_car"]
    # factor>1 path would call write_scaled_od_csv if materialize=True;
    # with materialize=False it must not, even though the base OD file
    # doesn't exist at all here (would raise if it tried to read it).
    sc = m.build_stress_scenario(city, base, 4.0, materialize=False)
    assert not city.od_csv(sc.od_label).exists()


def test_demand_scale_validation_rejects_factors_above_1_in_scenario_spec():
    # Confirms the documented constraint this script works around: passing
    # factor=2.0 directly as demand_scale must fail ScenarioSpec's own
    # validation (src/green_mobility/config.py) -- i.e. build_stress_scenario
    # choosing the OD-scaling path for factor>1 is not optional.
    from green_mobility.config import ConfigError
    with pytest.raises(ConfigError):
        dataclasses.replace(make_city().scenarios["weekday_full_car"], demand_scale=2.0)


# ---------------------------------------------------------------------------
# Resumability
# ---------------------------------------------------------------------------
def test_scenario_output_ready_false_when_manifest_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("green_mobility.config.DATA_DIR", tmp_path)
    from green_mobility.nomad_wrapper.runner import ScenarioRun
    city = make_city()
    run = ScenarioRun(city, city.scenarios["weekday_full_car"])
    assert m.scenario_output_ready(run) is False


def test_scenario_output_ready_true_with_valid_manifest_and_snapshot(tmp_path, monkeypatch):
    import json
    from green_mobility.nomad_wrapper.runner import ScenarioRun
    city = make_city()
    run = ScenarioRun(city, city.scenarios["weekday_full_car"])
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "network_state_000900.geojson").write_text("{}")
    manifest_path = run.output_dir.with_name(run.output_dir.name + ".manifest.json")
    manifest_path.write_text(json.dumps({"params": {"run_stats": {"depart": 10}}}))
    assert m.scenario_output_ready(run) is True


def test_scenario_output_ready_false_with_manifest_but_no_run_stats(tmp_path):
    import json
    from green_mobility.nomad_wrapper.runner import ScenarioRun
    city = make_city()
    run = ScenarioRun(city, city.scenarios["weekday_full_car"])
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "network_state_000900.geojson").write_text("{}")
    manifest_path = run.output_dir.with_name(run.output_dir.name + ".manifest.json")
    manifest_path.write_text(json.dumps({"params": {}}))
    assert m.scenario_output_ready(run) is False


# ---------------------------------------------------------------------------
# Automatic conclusion classification (rule-based, explicit thresholds)
# ---------------------------------------------------------------------------
def _summary_row(factor, frac_congested, frac_bpr_active, teleported_rate, conserved=True):
    return {
        "scenario_key": f"s_{factor}", "demand_factor": factor, "status": "OK",
        "frac_congested_gt_1_5": frac_congested, "frac_bpr_active_vc_ge_0_8": frac_bpr_active,
        "teleported_rate": teleported_rate, "mass_conservation_ok": conserved,
    }


def test_classify_outcome_underloaded_baseline():
    df = pd.DataFrame([_summary_row(f, 0.0, 0.0, 0.01) for f in (0.5, 1.0, 2.0, 4.0)])
    assert m.classify_outcome(df).startswith("baseline sottocaricata")


def test_classify_outcome_mass_conservation_violation_flagged_as_possible_bug():
    rows = [_summary_row(f, 0.0, 0.0, 0.01) for f in (0.5, 1.0, 2.0, 4.0)]
    rows[-1]["mass_conservation_ok"] = False
    df = pd.DataFrame(rows)
    assert m.classify_outcome(df).startswith("possibile bug")


def test_classify_outcome_teleport_only_growth():
    rows = [
        _summary_row(0.5, 0.0, 0.0, 0.01),
        _summary_row(1.0, 0.0, 0.0, 0.02),
        _summary_row(2.0, 0.0, 0.0, 0.10),
        _summary_row(4.0, 0.0, 0.0, 0.30),
    ]
    df = pd.DataFrame(rows)
    assert m.classify_outcome(df).startswith("crescita solo teleport")


def test_classify_outcome_queue_model_limit():
    rows = [
        _summary_row(0.5, 0.0, 0.0, 0.01),
        _summary_row(1.0, 0.01, 0.02, 0.01),
        _summary_row(2.0, 0.05, 0.10, 0.02),
        _summary_row(4.0, 0.10, 0.20, 0.03),
    ]
    df = pd.DataFrame(rows)
    assert m.classify_outcome(df).startswith("limite QueueTrafficModel")


def test_classify_outcome_no_successful_scenarios():
    df = pd.DataFrame([{"scenario_key": "s", "status": "FAILED"}])
    assert m.classify_outcome(df).startswith("possibile bug")


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------
def test_parse_args_defaults():
    args = m.parse_args([])
    assert args.city == "palma_de_mallorca"
    assert args.scenario == "weekday_full_car"
    assert args.factors == "0.5,1.0,2.0,4.0"
    assert args.workers == 1
    assert args.force is False
    assert args.dry_run is False
    assert args.seed == 42


def test_parse_args_custom_factors_and_workers():
    args = m.parse_args(["--city", "madrid", "--factors", "1.0,3.0", "--workers", "4", "--force"])
    assert args.city == "madrid"
    assert args.factors == "1.0,3.0"
    assert args.workers == 4
    assert args.force is True


# ---------------------------------------------------------------------------
# Dead-end detection (synthetic tiny graph, no real city data needed)
# ---------------------------------------------------------------------------
def test_find_dead_end_edge_on_synthetic_graph(tmp_path, monkeypatch):
    monkeypatch.setattr("green_mobility.config.DATA_DIR", tmp_path)
    city = make_city()
    city.city_dir.mkdir(parents=True, exist_ok=True)

    # A tiny graph: node 99 is a dead end (degree 1, reached only via edge 5,
    # a 200m Service-class edge). Node 1 (Primary) is a plausible approach node.
    nodes = pd.DataFrame({
        "node_id": [1, 2, 3, 99],
        "lon": [2.60, 2.62, 2.64, 2.70],
        "lat": [39.50, 39.52, 39.54, 39.60],
    })
    edges = pd.DataFrame({
        "edge_id": [1, 2, 3, 4, 5],
        "from_node": [1, 2, 1, 2, 3],
        "to_node": [2, 3, 3, 1, 99],
        "length_m": [500.0, 500.0, 500.0, 500.0, 200.0],
        "road_class": [4, 4, 6, 6, 12],  # Primary, Primary, Secondary, Secondary, Service
    })
    nodes.to_parquet(city.nodes_parquet, index=False)
    edges.to_parquet(city.edges_parquet, index=False)

    info = m.find_dead_end_edge(city)
    assert info is not None
    assert info["dead_end_edge_id"] == 5
    assert info["dest_node"] == 99
    assert info["road_class_label"] == "Service"


def test_find_dead_end_edge_returns_none_when_no_dead_end(tmp_path, monkeypatch):
    monkeypatch.setattr("green_mobility.config.DATA_DIR", tmp_path)
    city = make_city()
    city.city_dir.mkdir(parents=True, exist_ok=True)
    nodes = pd.DataFrame({"node_id": [1, 2], "lon": [2.6, 2.62], "lat": [39.5, 39.52]})
    edges = pd.DataFrame({
        "edge_id": [1, 2], "from_node": [1, 2], "to_node": [2, 1],
        "length_m": [500.0, 500.0], "road_class": [10, 10],
    })
    nodes.to_parquet(city.nodes_parquet, index=False)
    edges.to_parquet(city.edges_parquet, index=False)
    assert m.find_dead_end_edge(city) is None
