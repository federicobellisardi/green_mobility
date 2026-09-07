import geopandas as gpd
from shapely.geometry import LineString, box

from green_mobility.thermal.lclu_plantability import (
    build_class_reference_table,
    edge_plantability,
)

CRS = "EPSG:32630"


def test_class_reference_table_covers_all_classes_with_valid_status():
    table = build_class_reference_table()
    assert set(table["plantability_status"]) <= {"plantable", "non_plantable", "conditional"}
    assert not table["reason"].isna().any()


def test_edge_plantability_half_plantable_half_building():
    # Buffer covers a 10x20 box: left half (0-10,0-20) = building (11100,
    # non_plantable), right half (10-20,0-20) = green urban public (14110, plantable).
    lclu = gpd.GeoDataFrame(
        {"code_2021": ["11100", "14110"]},
        geometry=[box(0, 0, 10, 20), box(10, 0, 20, 20)],
        crs=CRS,
    )
    edges = gpd.GeoDataFrame({"edge_id": [1]}, geometry=[LineString([(10, 10), (10, 10)])], crs=CRS)
    result = edge_plantability(edges, lclu, buffer_m=10.0)  # buffer radius 10 -> ~ covers both halves
    row = result.iloc[0]
    assert row["candidate_plantable_fraction"] > 0
    assert row["excluded_fraction"] > 0
    assert any("11100" in r for r in row["exclusion_reasons"])


def test_road_margin_scenario_conservative_vs_assume_margin():
    lclu = gpd.GeoDataFrame({"code_2021": ["12220"]}, geometry=[box(0, 0, 20, 20)], crs=CRS)
    edges = gpd.GeoDataFrame({"edge_id": [1]}, geometry=[LineString([(10, 10), (10, 10)])], crs=CRS)

    conservative = edge_plantability(edges, lclu, buffer_m=10.0, road_margin_scenario="conservative").iloc[0]
    assume_margin = edge_plantability(edges, lclu, buffer_m=10.0, road_margin_scenario="assume-margin").iloc[0]

    assert conservative["candidate_plantable_fraction"] == 0.0
    assert assume_margin["candidate_plantable_fraction"] > 0.0
    assert assume_margin["candidate_plantable_fraction"] < 1.0


def test_forests_class_reported_as_currently_treed_not_plantable():
    lclu = gpd.GeoDataFrame({"code_2021": ["31000"]}, geometry=[box(0, 0, 20, 20)], crs=CRS)
    edges = gpd.GeoDataFrame({"edge_id": [1]}, geometry=[LineString([(10, 10), (10, 10)])], crs=CRS)
    result = edge_plantability(edges, lclu, buffer_m=10.0).iloc[0]
    assert result["currently_treed_fraction"] > 0.9
    assert result["candidate_plantable_fraction"] == 0.0


def test_invalid_road_margin_scenario_raises():
    lclu = gpd.GeoDataFrame({"code_2021": ["12220"]}, geometry=[box(0, 0, 20, 20)], crs=CRS)
    edges = gpd.GeoDataFrame({"edge_id": [1]}, geometry=[LineString([(10, 10), (10, 10)])], crs=CRS)
    try:
        edge_plantability(edges, lclu, buffer_m=10.0, road_margin_scenario="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass
