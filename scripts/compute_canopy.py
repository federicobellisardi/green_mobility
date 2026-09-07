#!/usr/bin/env python3
"""Compute per-edge canopy_fraction for one city (thermal.canopy), mirroring
exactly the method already validated and saved for Palma de Mallorca:
  - build a small VRT mosaic of the ESA WorldCover tiles covering this city
    (data/climate_raw/worldcover/*.tif, already downloaded for all 12 cities)
  - reproject edge centerlines to the appropriate UTM zone for accurate
    metric buffering (15 m), then rasterstats.zonal_stats against class 10
    (tree cover)
  - save {city}/green_mobility/thermal/canopy_fraction.parquet + manifest,
    same schema/params as palma_de_mallorca's existing file.
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from green_mobility.config import load_all_cities
from green_mobility.manifest import write_manifest
from green_mobility.thermal.canopy import edge_canopy_fraction

WORLDCOVER_DIR = Path("data/climate_raw/worldcover")


def worldcover_tile(lat: float, lon: float) -> str:
    south = math.floor(lat / 3.0) * 3
    west = math.floor(lon / 3.0) * 3
    ns = f"N{south:02d}" if south >= 0 else f"S{abs(south):02d}"
    ew = f"E{west:03d}" if west >= 0 else f"W{abs(west):03d}"
    return f"{ns}{ew}"


def tiles_for_bbox(north, west, south, east) -> list[str]:
    lat_values = range(math.floor(south / 3) * 3, math.floor((north - 1e-9) / 3) * 3 + 1, 3)
    lon_values = range(math.floor(west / 3) * 3, math.floor((east - 1e-9) / 3) * 3 + 1, 3)
    return sorted({worldcover_tile(lat + 0.1, lon + 0.1) for lat in lat_values for lon in lon_values})


def utm_epsg_for_lon(lon: float) -> int:
    """Spain spans UTM zones 29N/30N/31N (28N/27N only for the Canaries,
    not relevant here). Standard UTM zone boundaries every 6 degrees."""
    zone = int((lon + 180) // 6) + 1
    return 32600 + zone  # WGS84 / UTM zone N


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--buffer-m", type=float, default=15.0)
    args = parser.parse_args()

    cities = load_all_cities()
    if args.city not in cities:
        raise SystemExit(f"unknown city {args.city}, configured: {sorted(cities)}")
    city = cities[args.city]

    out_path = city.green_mobility_dir / "thermal" / "canopy_fraction.parquet"
    if out_path.exists():
        print(f"[{city.slug}] SKIP: {out_path} already exists")
        return 0

    # bbox from FUA-envelope city extent -- read from nodes.parquet bounds
    # rather than re-deriving the padded CITIES bbox from download_data.py
    # (this way it's exactly the network's real extent, no dependency on
    # that standalone script's module).
    nodes = pd.read_parquet(city.nodes_parquet)
    north, south = nodes["lat"].max(), nodes["lat"].min()
    west, east = nodes["lon"].min(), nodes["lon"].max()
    tiles = tiles_for_bbox(north + 0.05, west - 0.05, south - 0.05, east + 0.05)
    tile_paths = [WORLDCOVER_DIR / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif" for t in tiles]
    missing = [p for p in tile_paths if not p.exists()]
    if missing:
        raise SystemExit(f"[{city.slug}] missing WorldCover tiles: {missing}")

    utm_epsg = utm_epsg_for_lon((west + east) / 2)

    vrt_path = city.green_mobility_dir / "thermal" / f"worldcover_{city.slug}.vrt"
    vrt_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gdalbuildvrt", "-overwrite", str(vrt_path), *(str(p) for p in tile_paths)],
        check=True, capture_output=True,
    )

    # BUG found while running this for the first non-Palma city: the mosaic
    # VRT above stays in EPSG:4326 (geographic), but edge_canopy_fraction
    # buffers geometries in whatever CRS they're already in and calls
    # rasterio.mask.mask directly (no reprojection) -- passing UTM-metre
    # geometries against a lat/lon raster means every single mask silently
    # misses (0 valid pixels -> NaN for all edges, exactly what happened on
    # the first attempt for Cordoba: 67,120/67,120 NaN). Warp the raster
    # itself into the SAME UTM zone the edges will be buffered in, so both
    # sides of edge_canopy_fraction's rasterio.mask.mask call agree.
    warped_vrt_path = city.green_mobility_dir / "thermal" / f"worldcover_{city.slug}_utm{utm_epsg}.vrt"
    subprocess.run(
        ["gdalwarp", "-overwrite", "-of", "VRT", "-t_srs", f"EPSG:{utm_epsg}", str(vrt_path), str(warped_vrt_path)],
        check=True, capture_output=True,
    )

    edges = pd.read_parquet(city.edges_parquet)
    node_lon = nodes.set_index("node_id")["lon"]
    node_lat = nodes.set_index("node_id")["lat"]
    geoms = [
        LineString([
            (node_lon[fr], node_lat[fr]),
            (node_lon[to], node_lat[to]),
        ])
        for fr, to in zip(edges["from_node"], edges["to_node"])
    ]
    edges_gdf = gpd.GeoDataFrame({"edge_id": edges["edge_id"]}, geometry=geoms, crs="EPSG:4326")
    edges_utm = edges_gdf.to_crs(epsg=utm_epsg)

    print(f"[{city.slug}] {len(edges_utm)} edges, tiles={tiles}, UTM=EPSG:{utm_epsg}")
    result = edge_canopy_fraction(edges_utm, warped_vrt_path, buffer_m=args.buffer_m)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    write_manifest(
        out_path, "thermal-canopy-fraction",
        {
            "city": city.slug,
            "source": f"ESA WorldCover 2021 v200 (tiles {', '.join(tiles)})",
            "buffer_m": args.buffer_m,
            "tree_class_codes": [10],
            "n_edges": int(len(result)),
            "method": f"rasterstats.zonal_stats categorical, straight-line edge geometry (node-to-node), buffered in EPSG:{utm_epsg}",
        },
    )
    print(f"[{city.slug}] wrote {out_path}")
    print(result["canopy_fraction"].describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
