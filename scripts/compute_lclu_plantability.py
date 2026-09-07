#!/usr/bin/env python3
"""Compute edge_plantability.parquet for one city from Urban Atlas LCLU
(thermal.lclu_plantability). Prepares/validates the planting-constraint
layer only -- does NOT run any greening optimization.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from green_mobility.config import load_all_cities
from green_mobility.manifest import write_manifest
from green_mobility.thermal.lclu_plantability import edge_plantability

LCLU_DIR = Path("data/climate_raw/urban_atlas/lclu")
UA_FUA_CODE = {
    "palma_de_mallorca": "ES010L2_PALMA_DE_MALLORCA", "zaragoza": "ES005L2_ZARAGOZA",
    "murcia": "ES007L2_MURCIA", "valladolid": "ES009L2_VALLADOLID", "madrid": "ES001L3_MADRID",
    "valencia": "ES003L3_VALENCIA", "sevilla": "ES004L3_SEVILLA", "cordoba": "ES020L2_CORDOBA",
    "granada": "ES501L3_GRANADA", "bilbao": "ES019L3_BILBAO", "a_coruna": "ES026L2_CORUNA_A",
}


def _find_lclu_file(city_slug: str) -> Path:
    fua_code = UA_FUA_CODE[city_slug]
    matches = list(LCLU_DIR.glob(f"*{fua_code}*.fgb"))
    if not matches:
        raise SystemExit(f"no LCLU file found for {city_slug} (FUA code {fua_code}) under {LCLU_DIR}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--buffer-m", type=float, default=15.0)
    parser.add_argument("--road-margin-scenario", choices=("conservative", "assume-margin"), default="conservative")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N edges (perf testing)")
    args = parser.parse_args()

    cities = load_all_cities()
    if args.city not in cities:
        raise SystemExit(f"unknown city {args.city}, configured: {sorted(cities)}")
    city = cities[args.city]

    out_path = city.green_mobility_dir / "thermal" / "edge_plantability.parquet"
    if out_path.exists() and args.limit is None:
        print(f"[{city.slug}] SKIP: {out_path} already exists")
        return 0

    lclu_path = _find_lclu_file(city.slug)
    lclu = gpd.read_file(lclu_path)

    nodes = pd.read_parquet(city.nodes_parquet)
    edges = pd.read_parquet(city.edges_parquet)
    if args.limit:
        edges = edges.head(args.limit)
    node_lon = nodes.set_index("node_id")["lon"]
    node_lat = nodes.set_index("node_id")["lat"]
    geoms = [
        LineString([(node_lon[fr], node_lat[fr]), (node_lon[to], node_lat[to])])
        for fr, to in zip(edges["from_node"], edges["to_node"])
    ]
    edges_gdf = gpd.GeoDataFrame({"edge_id": edges["edge_id"]}, geometry=geoms, crs="EPSG:4326")
    edges_proj = edges_gdf.to_crs(lclu.crs)

    print(f"[{city.slug}] {len(edges_proj)} edges, LCLU={lclu_path.name}, buffer={args.buffer_m}m, scenario={args.road_margin_scenario}")
    result = edge_plantability(edges_proj, lclu, buffer_m=args.buffer_m, road_margin_scenario=args.road_margin_scenario)

    if args.limit:
        print(result[["edge_id", "currently_treed_fraction", "candidate_plantable_fraction", "excluded_fraction"]].describe())
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    write_manifest(
        out_path, "thermal-lclu-plantability",
        {"city": city.slug, "lclu_source": lclu_path.name, "buffer_m": args.buffer_m,
         "road_margin_scenario": args.road_margin_scenario, "n_edges": int(len(edges_proj))},
    )
    print(f"[{city.slug}] wrote {out_path}")
    print(result[["currently_treed_fraction", "candidate_plantable_fraction", "excluded_fraction"]].mean())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
