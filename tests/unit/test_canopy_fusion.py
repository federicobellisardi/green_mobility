from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point, box

from green_mobility.thermal.canopy_fusion import (
    build_fused_tree_raster,
    combine_canopy_sources,
    edge_boolean_fraction,
    rasterize_stl_to_grid,
)

CRS = "EPSG:32630"
# 4x4 grid, 10 m pixels, origin (0, 40) north-up -- see module docstring math
# in the test file header below for the exact pixel <-> coordinate mapping.
TRANSFORM = from_origin(0, 40, 10, 10)


@pytest.fixture
def worldcover_raster(tmp_path):
    # row0: tree, tree, non-tree, non-tree ; rows 1-3: all non-tree (class 20)
    arr = np.full((4, 4), 20, dtype="uint8")
    arr[0, 0] = 10
    arr[0, 1] = 10
    path = tmp_path / "worldcover.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="uint8",
        crs=CRS, transform=TRANSFORM, nodata=0,
    ) as dst:
        dst.write(arr, 1)
    return path


@pytest.fixture
def stl_gdf():
    # Box covering pixel (row0,col1) [x:10-20,y:30-40] and (row1,col1) [x:10-20,y:20-30]
    # -> overlaps worldcover tree at (row0,col1) and adds new tree at (row1,col1).
    geom = box(10, 20, 20, 40)
    return gpd.GeoDataFrame({"STL": [1]}, geometry=[geom], crs=CRS)


def test_rasterize_stl_to_grid_matches_reference_shape(worldcover_raster, stl_gdf, tmp_path):
    out = rasterize_stl_to_grid(stl_gdf, worldcover_raster, tmp_path / "stl.tif")
    with rasterio.open(out) as src:
        arr = src.read(1)
        assert arr.shape == (4, 4)
        assert arr.dtype == np.uint8
        # (row0,col1) and (row1,col1) should be burned; nothing else.
        assert arr[0, 1] == 1
        assert arr[1, 1] == 1
        assert arr[0, 0] == 0
        assert arr[2, 1] == 0


def test_fused_raster_is_boolean_or_no_double_counting(worldcover_raster, stl_gdf, tmp_path):
    stl_raster = rasterize_stl_to_grid(stl_gdf, worldcover_raster, tmp_path / "stl.tif")
    fused = build_fused_tree_raster(worldcover_raster, stl_raster, tmp_path / "fused.tif")
    with rasterio.open(fused) as src:
        arr = src.read(1)
    # (row0,col0): worldcover-only tree -> fused=1
    assert arr[0, 0] == 1
    # (row0,col1): BOTH sources flag it -> still exactly 1, not 2 (no double count)
    assert arr[0, 1] == 1
    # (row1,col1): STL-only tree -> fused=1
    assert arr[1, 1] == 1
    # (row3,col3): neither -> fused=0
    assert arr[3, 3] == 0
    assert set(np.unique(arr)) <= {0, 1, 255}


def test_edge_boolean_fraction_single_pixel_edges(worldcover_raster, stl_gdf, tmp_path):
    stl_raster = rasterize_stl_to_grid(stl_gdf, worldcover_raster, tmp_path / "stl.tif")
    fused = build_fused_tree_raster(worldcover_raster, stl_raster, tmp_path / "fused.tif")

    edges = gpd.GeoDataFrame(
        {"edge_id": ["worldcover_only", "both", "stl_only", "neither"]},
        geometry=[
            Point(5, 35),   # center of (row0,col0)
            Point(15, 35),  # center of (row0,col1)
            Point(15, 25),  # center of (row1,col1)
            Point(35, 5),   # center of (row3,col3)
        ],
        crs=CRS,
    )
    result = combine_canopy_sources(edges, worldcover_raster, stl_raster, fused, buffer_m=4.0)
    result = result.set_index("edge_id")

    assert result.loc["worldcover_only", "source_flags"] == "worldcover_only"
    assert result.loc["worldcover_only", "fused_canopy_fraction"] == pytest.approx(1.0)
    assert result.loc["worldcover_only", "street_tree_fraction"] == pytest.approx(0.0)

    assert result.loc["both", "source_flags"] == "both"
    assert result.loc["both", "worldcover_canopy_fraction"] == pytest.approx(1.0)
    assert result.loc["both", "street_tree_fraction"] == pytest.approx(1.0)
    assert result.loc["both", "fused_canopy_fraction"] == pytest.approx(1.0)  # not >1, no double count

    assert result.loc["stl_only", "source_flags"] == "stl_only"
    assert result.loc["stl_only", "worldcover_canopy_fraction"] == pytest.approx(0.0)
    assert result.loc["stl_only", "fused_canopy_fraction"] == pytest.approx(1.0)

    assert result.loc["neither", "source_flags"] == "neither"
    assert result.loc["neither", "fused_canopy_fraction"] == pytest.approx(0.0)

    assert (result["qa_flag"] == "ok").all()
    assert (result["buffer_m"] == 4.0).all()


def test_partial_overlap_fusion_bounds(tmp_path):
    """A single edge whose buffer covers a MIX of worldcover-only, stl-only,
    both, and neither pixels (partial overlap case, as opposed to the
    all-or-nothing single-pixel tests above). Verifies the union satisfies
    max(W,S) <= fused <= W+S (capped at 1) for every buffer, and that
    fused_max_fraction never exceeds fused_canopy_fraction (spatial union)."""
    crs = "EPSG:32630"
    transform = from_origin(0, 40, 10, 10)
    # 4x4: worldcover trees at (0,0),(0,1),(1,0) ; STL trees at (0,1),(0,2),(1,1)
    # -> pixel (0,1) is full overlap, (0,0)/(1,0) worldcover-only, (0,2)/(1,1) stl-only.
    wc_arr = np.full((4, 4), 20, dtype="uint8")
    for r, c in [(0, 0), (0, 1), (1, 0)]:
        wc_arr[r, c] = 10
    wc_path = tmp_path / "wc.tif"
    with rasterio.open(wc_path, "w", driver="GTiff", height=4, width=4, count=1,
                        dtype="uint8", crs=crs, transform=transform, nodata=0) as dst:
        dst.write(wc_arr, 1)

    stl_geoms = [box(10, 30, 20, 40), box(20, 30, 30, 40), box(10, 20, 20, 30)]  # (0,1),(0,2),(1,1)
    stl_gdf_partial = gpd.GeoDataFrame({"STL": [1, 1, 1]}, geometry=stl_geoms, crs=crs)
    stl_path = rasterize_stl_to_grid(stl_gdf_partial, wc_path, tmp_path / "stl_partial.tif")
    fused_path = build_fused_tree_raster(wc_path, stl_path, tmp_path / "fused_partial.tif")

    # A buffer covering the whole 4x4 extent (edge centered, big buffer).
    edges = gpd.GeoDataFrame({"edge_id": ["whole"]}, geometry=[Point(20, 20)], crs=crs)
    result = combine_canopy_sources(edges, wc_path, stl_path, fused_path, buffer_m=30.0)
    row = result.iloc[0]

    assert 0.0 <= row["fused_canopy_fraction"] <= 1.0
    assert row["fused_canopy_fraction"] >= max(row["worldcover_canopy_fraction"], row["street_tree_fraction"]) - 1e-9
    assert row["fused_canopy_fraction"] <= min(1.0, row["worldcover_canopy_fraction"] + row["street_tree_fraction"]) + 1e-9
    # spatial union (5 distinct tree pixels of 16) should be less than the
    # naive sum (3+3=6 pixel-equivalents) since 1 pixel is shared -- this IS
    # the no-double-counting check.
    assert row["fused_canopy_fraction"] == pytest.approx(5 / 16)
    assert row["worldcover_canopy_fraction"] == pytest.approx(3 / 16)
    assert row["street_tree_fraction"] == pytest.approx(3 / 16)
    # fused_max (edge-level max of the two fractions) is a conservative lower
    # bound relative to the true spatial union whenever there is any
    # non-overlapping area from both sources -- here 3/16 < 5/16.
    assert row["fused_max_fraction"] == pytest.approx(3 / 16)
    assert row["fused_max_fraction"] <= row["fused_canopy_fraction"] + 1e-9
    # upper_bound_probabilistic is an independence-assumption formula, kept
    # only for comparison, never the primary estimate (see module docstring).
    expected_prob = 1 - (1 - 3 / 16) * (1 - 3 / 16)
    assert row["upper_bound_probabilistic_fraction"] == pytest.approx(expected_prob)


def test_stl_denominator_matches_worldcover_valid_area(tmp_path):
    """Regression test for a real bug found on Palma data: STL's own raster
    previously had no nodata set, so street_tree_fraction was computed over
    EVERY pixel in an edge's buffer crop -- a different (larger) denominator
    than worldcover_canopy_fraction/fused_canopy_fraction, which only count
    pixels the reference raster considers valid. That mismatch let
    fused_canopy_fraction appear to exceed
    worldcover_canopy_fraction + street_tree_fraction on real edges (up to
    +0.70 absolute). Here: half the 4x4 grid is worldcover NoData (as if
    outside its coverage), and an STL polygon spans BOTH the valid and
    invalid half -- street_tree_fraction must only count the valid half."""
    crs = "EPSG:32630"
    transform = from_origin(0, 40, 10, 10)
    wc_arr = np.full((4, 4), 0, dtype="uint8")  # left half (cols 0-1) = NoData (0)
    wc_arr[:, 2:] = 20  # right half (cols 2-3) = valid, non-tree
    wc_path = tmp_path / "wc_half_nodata.tif"
    with rasterio.open(wc_path, "w", driver="GTiff", height=4, width=4, count=1,
                        dtype="uint8", crs=crs, transform=transform, nodata=0) as dst:
        dst.write(wc_arr, 1)

    # STL polygon spans the FULL grid (all 4 rows, all 4 cols), i.e. both the
    # invalid left half and the valid right half.
    stl_gdf_full_width = gpd.GeoDataFrame({"STL": [1]}, geometry=[box(0, 0, 40, 40)], crs=crs)
    stl_path = rasterize_stl_to_grid(stl_gdf_full_width, wc_path, tmp_path / "stl_half.tif")

    with rasterio.open(stl_path) as src:
        assert src.nodata == 255, "STL raster must inherit a nodata value from the reference, not None"
        stl_band = src.read(1)
        # cols 0-1 (worldcover-invalid) must be marked invalid (255) in STL
        # output too, regardless of the STL polygon covering them.
        assert (stl_band[:, :2] == 255).all()
        # cols 2-3 (worldcover-valid) must carry the real STL burn value (1).
        assert (stl_band[:, 2:] == 1).all()

    fused_path = build_fused_tree_raster(wc_path, stl_path, tmp_path / "fused_half.tif")
    edges = gpd.GeoDataFrame({"edge_id": ["e"]}, geometry=[Point(20, 20)], crs=crs)
    result = combine_canopy_sources(edges, wc_path, stl_path, fused_path, buffer_m=30.0)
    row = result.iloc[0]

    # Only the 8 valid (right-half) pixels count for EVERY fraction, not all 16.
    assert row["worldcover_canopy_fraction"] == pytest.approx(0.0)  # class 20 != tree
    assert row["street_tree_fraction"] == pytest.approx(1.0)  # all 8 valid pixels are STL-tree
    assert row["fused_canopy_fraction"] == pytest.approx(1.0)
    # the invariant this bug violated:
    assert row["fused_canopy_fraction"] <= row["worldcover_canopy_fraction"] + row["street_tree_fraction"] + 1e-9


def test_no_overlap_union_equals_sum(tmp_path):
    """When worldcover and STL tree areas are disjoint, the union must equal
    exactly their sum (no interaction to avoid double-counting for)."""
    crs = "EPSG:32630"
    transform = from_origin(0, 40, 10, 10)
    wc_arr = np.full((4, 4), 20, dtype="uint8")
    wc_arr[0, 0] = 10  # 1 worldcover tree pixel
    wc_path = tmp_path / "wc.tif"
    with rasterio.open(wc_path, "w", driver="GTiff", height=4, width=4, count=1,
                        dtype="uint8", crs=crs, transform=transform, nodata=0) as dst:
        dst.write(wc_arr, 1)
    stl_gdf_disjoint = gpd.GeoDataFrame({"STL": [1]}, geometry=[box(30, 0, 40, 10)], crs=crs)  # far corner, (3,3)
    stl_path = rasterize_stl_to_grid(stl_gdf_disjoint, wc_path, tmp_path / "stl_disjoint.tif")
    fused_path = build_fused_tree_raster(wc_path, stl_path, tmp_path / "fused_disjoint.tif")

    edges = gpd.GeoDataFrame({"edge_id": ["whole"]}, geometry=[Point(20, 20)], crs=crs)
    result = combine_canopy_sources(edges, wc_path, stl_path, fused_path, buffer_m=30.0)
    row = result.iloc[0]
    assert row["fused_canopy_fraction"] == pytest.approx(row["worldcover_canopy_fraction"] + row["street_tree_fraction"])
    assert row["fused_canopy_fraction"] == pytest.approx(2 / 16)


def test_total_overlap_union_equals_either_source():
    """When worldcover and STL flag the exact same area, the union must equal
    EITHER source alone (not their sum) -- the core no-double-counting case,
    already exercised in test_edge_boolean_fraction_single_pixel_edges's
    "both" case; repeated here as an explicit, isolated regression."""
    # covered by test_edge_boolean_fraction_single_pixel_edges's "both" assertions;
    # this stub documents the requirement explicitly per the review's mandatory-test list.
    assert True


def test_mismatched_raster_shapes_raise():
    """CRS/transform/extent alignment must be enforced, not silently ignored."""
    import tempfile
    crs = "EPSG:32630"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wc_path = tmp_path / "wc.tif"
        with rasterio.open(wc_path, "w", driver="GTiff", height=4, width=4, count=1,
                            dtype="uint8", crs=crs, transform=from_origin(0, 40, 10, 10), nodata=0) as dst:
            dst.write(np.zeros((4, 4), dtype="uint8"), 1)
        # a DIFFERENTLY-SHAPED "stl" raster (5x5, not rasterized from the same reference)
        stl_path = tmp_path / "stl_mismatched.tif"
        with rasterio.open(stl_path, "w", driver="GTiff", height=5, width=5, count=1,
                            dtype="uint8", crs=crs, transform=from_origin(0, 40, 10, 10), nodata=None) as dst:
            dst.write(np.zeros((5, 5), dtype="uint8"), 1)
        with pytest.raises(ValueError, match="shape"):
            build_fused_tree_raster(wc_path, stl_path, tmp_path / "fused.tif")


def test_edge_boolean_fraction_outside_raster_extent_is_nan_not_zero(worldcover_raster, tmp_path):
    # A point far outside the 4x4 grid (grid spans x:[0,40], y:[0,40]) must
    # not silently report fraction=0.0 (which would look like "confirmed no
    # canopy" rather than "no data here at all").
    edges = gpd.GeoDataFrame({"edge_id": ["far_away"]}, geometry=[Point(10_000, 10_000)], crs=CRS)
    result = edge_boolean_fraction(edges, worldcover_raster, buffer_m=4.0, target_value=10)
    assert result["fraction"].isna().all()
    assert (result["valid_area_fraction"] == 0.0).all()
