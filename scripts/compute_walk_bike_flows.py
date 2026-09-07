#!/usr/bin/env python3
"""Extract per-edge-per-15-min-bin walk+bike flow for one city, via
nomad_wrapper.walk_bike_routes.extract_walk_bike_bins (offline routing, no
DES run needed -- see that module's docstring). Mirrors exactly the output
already produced for Palma de Mallorca:
  {city}/green_mobility/exposure/{scenario.key}_walk_bike_edge_bins.parquet

Must run under NOMAD's own Python build (the "nomad" conda env), with
LD_PRELOAD set to that env's libstdc++ -- see nomad_wrapper.paths for why.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from green_mobility.config import load_all_cities
from green_mobility.manifest import write_manifest
from green_mobility.nomad_wrapper.walk_bike_routes import assert_conservation, extract_walk_bike_bins


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--scenario", default="weekday_full")
    args = parser.parse_args()

    cities = load_all_cities()
    if args.city not in cities:
        raise SystemExit(f"unknown city {args.city}, configured: {sorted(cities)}")
    city = cities[args.city]
    scenario = city.scenarios[args.scenario]

    out_path = city.green_mobility_dir / "exposure" / f"{scenario.key}_walk_bike_edge_bins.parquet"
    if out_path.exists():
        print(f"[{city.slug}] SKIP: {out_path} already exists")
        return 0

    frames = []
    diagnostics_by_mode = {}
    for mode in ("walk", "bike"):
        print(f"[{city.slug}] extracting {mode}...")
        bins_df, diag = extract_walk_bike_bins(city, scenario, mode)
        frames.append(bins_df)
        diagnostics_by_mode[mode] = diag
        assert_conservation(diag)
        print(f"[{city.slug}] {mode}: od_agents={diag['od_agents']} routed_agents={diag['routed_agents']} "
              f"unreachable_agents={diag['unreachable_agents']} (conservation OK)")

    result = pd.concat(frames, ignore_index=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    write_manifest(
        out_path, "walk-bike-flows",
        {"city": city.slug, "scenario": scenario.key,
         "diagnostics": diagnostics_by_mode, "n_rows": int(len(result))},
    )
    print(f"[{city.slug}] wrote {out_path} ({len(result)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
