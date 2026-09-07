#!/usr/bin/env python3
"""Compute canopy_fraction_v2.parquet for one city: WorldCover-only,
Street-Tree-Layer-only, and fused (pixel-level boolean OR, see
thermal.canopy_fusion) canopy fraction per edge, at 3 buffer widths
(10/15/20 m). Reuses the same per-city UTM-warped WorldCover VRT already
built/validated by compute_canopy.py (same tiles, same reprojection fix for
the Cordoba CRS-mismatch bug) -- this script only adds the STL/fusion layer
on top, it does not re-derive the WorldCover step.

v1 (data/<city>/green_mobility/thermal/canopy_fraction.parquet, WorldCover
only) is left untouched -- this writes a SEPARATE v2 file alongside it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from compute_canopy import WORLDCOVER_DIR, tiles_for_bbox, utm_epsg_for_lon  # noqa: E402 (sys.path set above)
from green_mobility.config import load_all_cities
from green_mobility.manifest import write_manifest
from green_mobility.thermal.canopy_fusion import (
    build_fused_tree_raster,
    combine_canopy_sources,
    rasterize_stl_to_grid,
)

STL_DIR = Path("data/climate_raw/urban_atlas/stl")
UA_FUA_CODE = {
    "palma_de_mallorca": "ES010L2_PALMA_DE_MALLORCA", "zaragoza": "ES005L2_ZARAGOZA",
    "murcia": "ES007L2_MURCIA", "valladolid": "ES009L2_VALLADOLID", "madrid": "ES001L3_MADRID",
    "valencia": "ES003L3_VALENCIA", "sevilla": "ES004L3_SEVILLA", "cordoba": "ES020L2_CORDOBA",
    "granada": "ES501L3_GRANADA", "bilbao": "ES019L3_BILBAO", "a_coruna": "ES026L2_CORUNA_A",
}
BUFFER_WIDTHS_M = (10.0, 15.0, 20.0)


def _find_stl_file(city_slug: str) -> Path:
    fua_code = UA_FUA_CODE[city_slug]
    matches = list(STL_DIR.glob(f"*{fua_code}*.fgb"))
    if not matches:
        raise SystemExit(f"no STL file found for {city_slug} (FUA code {fua_code}) under {STL_DIR}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    args = parser.parse_args()

    cities = load_all_cities()
    if args.city not in cities:
        raise SystemExit(f"unknown city {args.city}, configured: {sorted(cities)}")
    city = cities[args.city]

    out_path = city.green_mobility_dir / "thermal" / "canopy_fraction_v2.parquet"
    if out_path.exists():
        print(f"[{city.slug}] SKIP: {out_path} already exists")
        return 0

    nodes = pd.read_parquet(city.nodes_parquet)
    north, south = nodes["lat"].max(), nodes["lat"].min()
    west, east = nodes["lon"].min(), nodes["lon"].max()
    tiles = tiles_for_bbox(north + 0.05, west - 0.05, south - 0.05, east + 0.05)
    tile_paths = [WORLDCOVER_DIR / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif" for t in tiles]
    missing = [p for p in tile_paths if not p.exists()]
    if missing:
        raise SystemExit(f"[{city.slug}] missing WorldCover tiles: {missing}")

    utm_epsg = utm_epsg_for_lon((west + east) / 2)
    thermal_dir = city.green_mobility_dir / "thermal"
    thermal_dir.mkdir(parents=True, exist_ok=True)

    vrt_path = thermal_dir / f"worldcover_{city.slug}.vrt"
    subprocess.run(
        ["gdalbuildvrt", "-overwrite", str(vrt_path), *(str(p) for p in tile_paths)],
        check=True, capture_output=True,
    )
    warped_vrt_path = thermal_dir / f"worldcover_{city.slug}_utm{utm_epsg}.vrt"
    subprocess.run(
        ["gdalwarp", "-overwrite", "-of", "VRT", "-t_srs", f"EPSG:{utm_epsg}", str(vrt_path), str(warped_vrt_path)],
        check=True, capture_output=True,
    )
    # BUG FOUND during Fase-1 validation: a warped VRT resamples on every
    # read, and a single full-array read (as build_fused_tree_raster does)
    # can give SLIGHTLY different pixel values at certain boundary cells
    # than the many small per-edge windowed/cropped reads
    # edge_boolean_fraction does -- confirmed by direct inspection on a real
    # edge (Palma edge_id=14: worldcover_canopy_fraction=10/18 computed via
    # windowed reads, but the fused raster -- built from ONE full-array read
    # of the same VRT -- disagreed on 1 of those 18 pixels, producing
    # fused < worldcover, violating the union invariant). Materializing the
    # warp into a real GeoTIFF ONCE (plain block I/O on every subsequent
    # read, no further resampling) makes full-array and windowed reads
    # byte-identical, eliminating the discrepancy (verified: 134/3000 test
    # violations -> 0/3000 after this fix).
    warped_tif_path = thermal_dir / f"worldcover_{city.slug}_utm{utm_epsg}.tif"
    subprocess.run(
        ["gdal_translate", "-of", "GTiff", str(warped_vrt_path), str(warped_tif_path)],
        check=True, capture_output=True,
    )

    stl_path = _find_stl_file(city.slug)
    stl = gpd.read_file(stl_path)
    stl_utm = stl.to_crs(epsg=utm_epsg)

    stl_raster_path = thermal_dir / f"stl_{city.slug}_utm{utm_epsg}.tif"
    rasterize_stl_to_grid(stl_utm, warped_tif_path, stl_raster_path)

    fused_raster_path = thermal_dir / f"fused_tree_{city.slug}_utm{utm_epsg}.tif"
    build_fused_tree_raster(warped_tif_path, stl_raster_path, fused_raster_path)

    edges = pd.read_parquet(city.edges_parquet)
    node_lon = nodes.set_index("node_id")["lon"]
    node_lat = nodes.set_index("node_id")["lat"]
    geoms = [
        LineString([(node_lon[fr], node_lat[fr]), (node_lon[to], node_lat[to])])
        for fr, to in zip(edges["from_node"], edges["to_node"])
    ]
    edges_gdf = gpd.GeoDataFrame({"edge_id": edges["edge_id"]}, geometry=geoms, crs="EPSG:4326")
    edges_utm = edges_gdf.to_crs(epsg=utm_epsg)

    print(f"[{city.slug}] {len(edges_utm)} edges, STL={stl_path.name}, UTM=EPSG:{utm_epsg}")
    frames = []
    for buffer_m in BUFFER_WIDTHS_M:
        print(f"[{city.slug}] buffer_m={buffer_m}...")
        frames.append(combine_canopy_sources(edges_utm, warped_tif_path, stl_raster_path, fused_raster_path, buffer_m))
    result = pd.concat(frames, ignore_index=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)

    source_counts = result[result["buffer_m"] == 15.0]["source_flags"].value_counts().to_dict()
    write_manifest(
        out_path, "thermal-canopy-fraction-v2",
        {
            "city": city.slug,
            "worldcover_source": f"ESA WorldCover 2021 v200 (tiles {', '.join(tiles)})",
            "stl_source": stl_path.name,
            "buffer_widths_m": list(BUFFER_WIDTHS_M),
            "utm_epsg": utm_epsg,
            "n_edges": int(len(edges_utm)),
            "source_flags_at_15m": {str(k): int(v) for k, v in source_counts.items()},
            "method": "pixel-level boolean OR fusion (thermal.canopy_fusion), not the "
                      "probabilistic independence formula -- see module docstring",
        },
    )
    print(f"[{city.slug}] wrote {out_path}")
    print(result.groupby("buffer_m")[["worldcover_canopy_fraction", "street_tree_fraction", "fused_canopy_fraction"]].mean())
    print(result[result['buffer_m']==15.0]["source_flags"].value_counts())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
