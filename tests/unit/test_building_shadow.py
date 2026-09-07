import datetime as dt
import math

import geopandas as gpd
import numpy as np
import pytest
from affine import Affine
from shapely.geometry import LineString

from green_mobility.thermal.building_shadow import (
    HeightGrid,
    edge_hour_building_shadow_fraction,
    is_point_shadowed,
    sample_points_along_edge,
)

CRS = "EPSG:32630"
# 100x100 grid, 1 m pixels, origin (0, 100) north-up: pixel (row,col) covers
# x=[col, col+1), y=[100-row-1, 100-row).
TRANSFORM = Affine.translation(0, 100) * Affine.scale(1, -1)


def _grid_with_building(height: float, at_xy: tuple[float, float], nodata=65535) -> HeightGrid:
    arr = np.full((100, 100), nodata, dtype="uint16")
    x, y = at_xy
    col, row = int(x), int(100 - y)
    arr[row, col] = height
    return HeightGrid(array=arr, transform=TRANSFORM, nodata=nodata)


def test_isolated_building_analytic_shadow_boundary():
    # Building of height 20 m at x=50 (test point at x=0, same y). Distance=50 m.
    grid = _grid_with_building(height=20.0, at_xy=(50.5, 50.5))
    # elevation such that tan(elevation) = 20/50 = 0.4 exactly -> boundary case
    elevation_at_boundary = math.degrees(math.atan(20.0 / 50.0))
    # Just below the boundary elevation (building taller than required) -> shadowed
    assert is_point_shadowed(0.5, 50.5, elevation_at_boundary - 1.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    # Just above the boundary elevation (building shorter than required) -> not shadowed
    assert not is_point_shadowed(0.5, 50.5, elevation_at_boundary + 1.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)


def test_high_sun_vs_low_sun_same_building():
    grid = _grid_with_building(height=20.0, at_xy=(50.5, 50.5))
    high_sun = is_point_shadowed(0.5, 50.5, 45.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    low_sun = is_point_shadowed(0.5, 50.5, 10.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    assert high_sun is False   # tan(45)=1 -> needs height>50 at d=50, building is only 20
    assert low_sun is True     # tan(10)=0.176 -> needs height>~8.8 at d=50, building is 20


def test_azimuth_change_flips_shadow_side():
    # Building only to the EAST of the point.
    grid = _grid_with_building(height=20.0, at_xy=(50.5, 50.5))
    toward_building = is_point_shadowed(0.5, 50.5, 10.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    away_from_building = is_point_shadowed(0.5, 50.5, 10.0, 270.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    assert toward_building is True
    assert away_from_building is False


def test_no_building_never_shadowed():
    grid = HeightGrid(array=np.zeros((100, 100), dtype="uint16"), transform=TRANSFORM, nodata=None)
    for elevation in (1.0, 10.0, 45.0, 89.0):
        assert not is_point_shadowed(0.5, 50.5, elevation, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)


def test_nodata_treated_as_no_building():
    # Entire raster is NoData (65535) -- confirmed real semantics of the BBH
    # product (NoData = "not a building pixel", not "unknown").
    arr = np.full((100, 100), 65535, dtype="uint16")
    grid = HeightGrid(array=arr, transform=TRANSFORM, nodata=65535)
    assert grid.height_at(50.5, 50.5) == 0.0
    assert not is_point_shadowed(0.5, 50.5, 5.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)


def test_night_elevation_never_shadowed_even_with_tall_building():
    grid = _grid_with_building(height=200.0, at_xy=(10.5, 50.5))
    assert not is_point_shadowed(0.5, 50.5, -5.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    assert not is_point_shadowed(0.5, 50.5, 0.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)


def test_fraction_always_in_unit_interval():
    grid = _grid_with_building(height=15.0, at_xy=(30.5, 50.5))
    edges = gpd.GeoDataFrame(
        {"edge_id": [1, 2]},
        geometry=[LineString([(0, 50), (60, 50)]), LineString([(0, 0), (0, 90)])],
        crs=CRS,
    )
    result = edge_hour_building_shadow_fraction(
        edges, grid, lat_deg=39.5, lon_deg=2.6, d=dt.date(2022, 7, 14), hours=list(range(24)),
    )
    assert result["building_shadow_fraction"].between(0.0, 1.0).all()
    assert not result["building_shadow_fraction"].isna().any()


def test_result_invariant_to_edge_order():
    grid = _grid_with_building(height=15.0, at_xy=(30.5, 50.5))
    edges = gpd.GeoDataFrame(
        {"edge_id": [1, 2, 3]},
        geometry=[
            LineString([(0, 50), (60, 50)]),
            LineString([(0, 0), (0, 90)]),
            LineString([(20, 20), (40, 40)]),
        ],
        crs=CRS,
    )
    shuffled = edges.iloc[[2, 0, 1]].reset_index(drop=True)

    kwargs = dict(lat_deg=39.5, lon_deg=2.6, d=dt.date(2022, 7, 14), hours=[12])
    r1 = edge_hour_building_shadow_fraction(edges, grid, **kwargs).set_index("edge_id").sort_index()
    r2 = edge_hour_building_shadow_fraction(shuffled, grid, **kwargs).set_index("edge_id").sort_index()
    pd_testing_cols = ["building_shadow_fraction", "solar_elevation_deg", "solar_azimuth_deg"]
    for col in pd_testing_cols:
        assert (r1[col] == r2[col]).all()


def test_convergence_with_finer_sample_spacing():
    # A 1x1 m building precisely placed; fine ray-marching spacings should
    # agree with each other (both resolve the same obstruction), even though
    # a much coarser spacing could in principle step over a sub-cell-sized
    # obstacle (not asserted here -- only that refinement converges/stabilizes).
    grid = _grid_with_building(height=20.0, at_xy=(50.5, 50.5))
    fine_1m = is_point_shadowed(0.5, 50.5, 10.0, 90.0, grid, sample_spacing_m=1.0, max_ray_distance_m=100.0)
    fine_2m = is_point_shadowed(0.5, 50.5, 10.0, 90.0, grid, sample_spacing_m=2.0, max_ray_distance_m=100.0)
    fine_5m = is_point_shadowed(0.5, 50.5, 10.0, 90.0, grid, sample_spacing_m=5.0, max_ray_distance_m=100.0)
    assert fine_1m == fine_2m == fine_5m == True


def test_sample_points_along_edge_spacing():
    geom = LineString([(0, 0), (100, 0)])
    points = sample_points_along_edge(geom, spacing_m=20.0)
    assert len(points) >= 5
    assert points[0] == pytest.approx((0.0, 0.0))
    assert points[-1] == pytest.approx((100.0, 0.0))
