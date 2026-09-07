#!/usr/bin/env python3
"""Build the combined per-edge exposure table gm intervene needs:
{city}/green_mobility/exposure/{scenario}_edges.parquet, via
exposure.edges_table.build_scenario_edges_table.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from green_mobility.config import load_all_cities
from green_mobility.exposure.edges_table import SCENARIO_DATE_GROUPS, build_scenario_edges_table
from green_mobility.manifest import write_manifest

CAR_REPRESENTATIVE_DATE = {"weekday_full": "2022-01-15", "weekend_full": "2022-07-20"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIO_DATE_GROUPS))
    parser.add_argument("--with-car-flow", action="store_true",
                         help="include this week's LTM-gatefix car flow in flow_total (needs an already-completed car sweep for this city)")
    args = parser.parse_args()

    cities = load_all_cities()
    city = cities[args.city]

    car_edge_bins_path = None
    if args.with_car_flow:
        d = CAR_REPRESENTATIVE_DATE[args.scenario]
        # the car sweep's CLI --scenario is always "weekday_full_car" regardless of
        # which of the 5 calendar dates is being run -- confirmed via `ls`, the date
        # (not the scenario name) is what actually varies by weekday/weekend OD.
        car_edge_bins_path = (
            city.green_mobility_dir / "exposure"
            / f"weekday_full_car_{d}_ltm_gatefix_car_edge_bins.parquet"
        )
        if not car_edge_bins_path.exists():
            raise SystemExit(f"--with-car-flow given but {car_edge_bins_path} does not exist")

    edges, stats = build_scenario_edges_table(city, args.scenario, car_edge_bins_path=car_edge_bins_path)

    print(f"[{city.slug}/{args.scenario}] n_edges={stats['n_edges']} "
          f"excluded={stats['n_edges_excluded_zero_flow_or_no_canopy']} dates={stats['dates']} "
          f"with_car_flow={stats['with_car_flow']}")
    print(edges[["flow_total", "flow_active", "exposure_score", "utci_peak_c", "vulnerability_index"]].describe())

    out_path = city.green_mobility_dir / "exposure" / f"{args.scenario}_edges.parquet"
    edges.to_parquet(out_path, index=False)
    write_manifest(
        out_path, "exposure-edges",
        {"city": city.slug, "scenario": args.scenario, **stats},
    )
    print(f"[{city.slug}/{args.scenario}] wrote {out_path} ({len(edges)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
