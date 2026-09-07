#!/usr/bin/env python3
"""Compute building_shadow_fraction per edge-hour for one city and one date,
from the Urban Atlas Building Block Height raster (thermal.building_shadow).
Writes data/<city>/green_mobility/thermal/building_shadow/date=<date>/
edge_hour_building_shadow.parquet, per-date partitioned as specced.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from green_mobility.config import load_all_cities
from green_mobility.manifest import write_manifest
from green_mobility.thermal.building_shadow import HeightGrid, edge_hour_building_shadow_fraction

BBH_DIR = Path("data/climate_raw/urban_atlas/bbh")
UA_FUA_CODE = {
    "palma_de_mallorca": "ES010L2_PALMA_DE_MALLORCA", "zaragoza": "ES005L2_ZARAGOZA",
    "murcia": "ES007L2_MURCIA", "valladolid": "ES009L2_VALLADOLID", "madrid": "ES001L3_MADRID",
    "valencia": "ES003L3_VALENCIA", "sevilla": "ES004L3_SEVILLA", "cordoba": "ES020L2_CORDOBA",
    "granada": "ES501L3_GRANADA", "bilbao": "ES019L3_BILBAO", "a_coruna": "ES026L2_CORUNA_A",
}


def _find_bbh_file(city_slug: str) -> Path:
    fua_code = UA_FUA_CODE[city_slug]
    matches = list(BBH_DIR.glob(f"*{fua_code}*.tif"))
    if not matches:
        raise SystemExit(f"no BBH raster found for {city_slug} (FUA code {fua_code}) under {BBH_DIR}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--date", required=True, help="ISO date")
    parser.add_argument("--sample-spacing-m", type=float, default=10.0)
    parser.add_argument("--max-ray-distance-m", type=float, default=250.0)
    parser.add_argument("--edge-sample-spacing-m", type=float, default=20.0)
    args = parser.parse_args()

    cities = load_all_cities()
    if args.city not in cities:
        raise SystemExit(f"unknown city {args.city}, configured: {sorted(cities)}")
    city = cities[args.city]
    d = date_cls.fromisoformat(args.date)

    out_dir = city.green_mobility_dir / "thermal" / "building_shadow" / f"date={d.isoformat()}"
    out_path = out_dir / "edge_hour_building_shadow.parquet"
    if out_path.exists():
        print(f"[{city.slug}] SKIP: {out_path} already exists")
        return 0

    bbh_path = _find_bbh_file(city.slug)
    grid = HeightGrid.from_file(bbh_path)
    bbh_crs = None
    import rasterio
    with rasterio.open(bbh_path) as src:
        bbh_crs = src.crs

    nodes = pd.read_parquet(city.nodes_parquet)
    edges = pd.read_parquet(city.edges_parquet)
    node_lon = nodes.set_index("node_id")["lon"]
    node_lat = nodes.set_index("node_id")["lat"]
    geoms = [
        LineString([(node_lon[fr], node_lat[fr]), (node_lon[to], node_lat[to])])
        for fr, to in zip(edges["from_node"], edges["to_node"])
    ]
    edges_gdf = gpd.GeoDataFrame({"edge_id": edges["edge_id"]}, geometry=geoms, crs="EPSG:4326")
    edges_proj = edges_gdf.to_crs(bbh_crs)

    print(f"[{city.slug}] {len(edges_proj)} edges, BBH={bbh_path.name}, date={d}, CRS={bbh_crs}")
    result = edge_hour_building_shadow_fraction(
        edges_proj, grid, lat_deg=city.lat, lon_deg=city.lon, d=d, hours=list(range(24)),
        sample_spacing_m=args.sample_spacing_m, max_ray_distance_m=args.max_ray_distance_m,
        edge_sample_spacing_m=args.edge_sample_spacing_m,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    write_manifest(
        out_path, "thermal-building-shadow",
        {
            "city": city.slug, "date": d.isoformat(), "bbh_source": bbh_path.name,
            "sample_spacing_m": args.sample_spacing_m, "max_ray_distance_m": args.max_ray_distance_m,
            "edge_sample_spacing_m": args.edge_sample_spacing_m,
            "n_edges": int(len(edges_proj)), "n_rows": int(len(result)),
        },
    )
    print(f"[{city.slug}] wrote {out_path} ({len(result)} rows)")
    daytime = result[result["daylight"]]
    print(f"mean building_shadow_fraction (daylight hours): {daytime['building_shadow_fraction'].mean():.4f}")
    print(f"max building_shadow_fraction: {result['building_shadow_fraction'].max():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
