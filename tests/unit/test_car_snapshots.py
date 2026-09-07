import json

import pytest

from green_mobility.nomad_wrapper.car_snapshots import (
    bottleneck_persistence,
    hourly_ratio_shares,
    load_car_bins,
    top_delay_edges,
)


def _write_snapshot(path, t, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def _feature(edge_id, road_class, count, tt, ff, congestion):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "properties": {
            "edge_id": edge_id,
            "road_class": road_class,
            "count": count,
            "congestion": congestion,
            "travel_time_s": tt,
            "free_flow_time_s": ff,
            "length_m": 100.0,
            "speed_ms": 10.0,
            "speed_kmh": 36.0,
            "capacity_veh_h": 600.0,
            "flow_veh_h": count * 3600.0 / tt if tt > 0 else 0.0,
            "density_veh_km": 1.0,
        },
    }


@pytest.fixture
def snapshot_dir(tmp_path):
    # bin 1 (900-1800s): edge 1 free-flowing, edge 2 congested (ratio 2.0)
    _write_snapshot(
        tmp_path / "network_state_001800.geojson", 1800,
        [_feature(1, 10, 2, 10.0, 10.0, 0.0), _feature(2, 4, 5, 20.0, 10.0, 0.5)],
    )
    # bin 2 (1800-2700s): edge 2 severely congested (ratio 3.0), edge 1 gone quiet
    _write_snapshot(
        tmp_path / "network_state_002700.geojson", 2700,
        [_feature(2, 4, 8, 30.0, 10.0, 1.0)],
    )
    return tmp_path


def test_load_car_bins_parses_snapshots_and_computes_derived_fields(snapshot_dir):
    bins = load_car_bins(snapshot_dir, occupancy_factor=1.2)
    assert len(bins) == 3

    edge1 = bins[(bins["edge_id"] == 1) & (bins["bin_end_s"] == 1800)].iloc[0]
    assert edge1["occupancy_veh"] == 2
    assert edge1["travel_time_ratio"] == pytest.approx(1.0)
    assert edge1["vehicle_hours_lost"] == pytest.approx(0.0)
    assert edge1["vehicle_seconds"] == pytest.approx(2 * 900.0)
    assert edge1["person_seconds_est"] == pytest.approx(2 * 900.0 * 1.2)

    edge2_bin1 = bins[(bins["edge_id"] == 2) & (bins["bin_end_s"] == 1800)].iloc[0]
    assert edge2_bin1["travel_time_ratio"] == pytest.approx(2.0)
    # vehicle_hours_lost = occ * (tt-ff)/tt * (bin_duration_s/3600)
    expected_vhl = 5 * (20.0 - 10.0) / 20.0 * (900.0 / 3600.0)
    assert edge2_bin1["vehicle_hours_lost"] == pytest.approx(expected_vhl)


def test_load_car_bins_raises_on_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_car_bins(tmp_path / "does_not_exist", occupancy_factor=1.2)


def test_hourly_ratio_shares_thresholds(snapshot_dir):
    bins = load_car_bins(snapshot_dir, occupancy_factor=1.2)
    hourly = hourly_ratio_shares(bins).set_index("hour")
    hour0 = hourly.loc[0]
    # 3 edge-bins total this hour: ratios 1.0, 2.0, 3.0 -> 2/3 exceed 1.1 and 1.5, 1/3 exceeds 2.0
    assert hour0["n_active_edge_bins"] == 3
    assert hour0["share_ratio_gt_1_1"] == pytest.approx(2 / 3)
    assert hour0["share_ratio_gt_1_5"] == pytest.approx(2 / 3)
    assert hour0["share_ratio_gt_2_0"] == pytest.approx(1 / 3)


def test_top_delay_edges_ranks_by_total_vehicle_hours_lost(snapshot_dir):
    bins = load_car_bins(snapshot_dir, occupancy_factor=1.2)
    top = top_delay_edges(bins, top_pct=1.0)  # all edges
    assert top.iloc[0]["edge_id"] == 2  # edge 2 has all the delay
    assert top.iloc[0]["total_vehicle_hours_lost"] > 0
    edge1_row = top[top["edge_id"] == 1].iloc[0]
    assert edge1_row["total_vehicle_hours_lost"] == pytest.approx(0.0)


def test_bottleneck_persistence_identifies_chronic_vs_transient(snapshot_dir):
    bins = load_car_bins(snapshot_dir, occupancy_factor=1.2)
    persistence = bottleneck_persistence(bins, ratio_threshold=1.5)
    # edge 2 is active in both bins and exceeds 1.5 in both -> persistence 1.0
    edge2 = persistence[persistence["edge_id"] == 2].iloc[0]
    assert edge2["n_bins_active"] == 2
    assert edge2["n_bins_congested"] == 2
    assert edge2["persistence_share"] == pytest.approx(1.0)
    # edge 1 never exceeds the threshold -> absent from the (congested-only) table
    assert 1 not in persistence["edge_id"].values
