#!/usr/bin/env python3
"""Spatial-concentration diagnosis: does simulated car traffic concentrate on
real arterials (Motorway/Trunk/Primary) the way real-world traffic does, or
does it spread more evenly across the network than reality?

Uses ONLY already-existing full-day car-only results (no new simulation).
Method: compare each road class's share of total network length/capacity
against its share of simulated vehicle-km-traveled (VKT). In real cities,
arterials carry a share of VKT far larger than their share of network
length (a small fraction of road-km carries a large fraction of traffic) --
if the simulation shows arterials carrying a share of VKT roughly
proportional to (or smaller than) their length share, that is direct
evidence that the simulated network under-concentrates traffic onto real
arterials relative to reality, independent of total demand magnitude.

This follows directly from the Level 1 finding
(scripts/diagnostics/calibrate_od_level1.py): Madrid and Sevilla's implied
car-share already matches real surveys (~1 point off), yet Madrid shows an
8.3x real/simulated gap on the M-30 -- so if a magnitude-only explanation
were right, this VKT-concentration pattern should look "normal"; if a
spatial/routing explanation is right, arterials should look
under-utilized here relative to what their capacity share would predict.

Usage:
  python scripts/diagnostics/spatial_concentration_diagnosis.py --city madrid --date 2022-07-20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from green_mobility.nomad_wrapper.car_snapshots import load_car_bins  # noqa: E402

ROAD_CLASS_LABELS = {
    0: "Motorway", 1: "MotorwayLink", 2: "Trunk", 3: "TrunkLink",
    4: "Primary", 5: "PrimaryLink", 6: "Secondary", 7: "SecondaryLink",
    8: "Tertiary", 9: "TertiaryLink", 10: "Residential", 11: "LivingStreet",
    12: "Service", 13: "Unclassified", 14: "Track",
}
ARTERIAL_CLASSES = {0, 1, 2, 3, 4, 5}  # Motorway..PrimaryLink


def find_output_dir(city: str, date: str) -> Path:
    base = REPO_ROOT / f"results/{city}/weekday_full_car_{date}"
    candidates = sorted(base.glob(f"{date}_*"))
    if not candidates:
        raise FileNotFoundError(f"nessun output trovato sotto {base}")
    return candidates[0]


def diagnose(city: str, date: str) -> pd.DataFrame:
    out_dir = find_output_dir(city, date)
    bins = load_car_bins(out_dir, occupancy_factor=1.20)

    edges = pd.read_parquet(REPO_ROOT / f"data/{city}/edges.parquet")[
        ["edge_id", "road_class", "length_m"]
    ]
    # Restrict to the CAR-accessible network only (road_class<=13, mirrors
    # CAR_MAX_ROAD_CLASS in external/nomad/python/preprocessing/build_od.py)
    # -- classes 14+ (Track, Steps, Cycleway, etc.) are foot/bike-only and
    # were never candidates for car routing, so including them in the length
    # denominator would massively understate arterials' true share of the
    # network cars can actually choose from.
    edges = edges[edges["road_class"] <= 13].copy()

    # network length/capacity share per road class (car-accessible network only)
    net_by_class = edges.groupby("road_class").agg(
        n_edges=("edge_id", "count"), length_m=("length_m", "sum"),
    )
    net_by_class["length_share"] = net_by_class["length_m"] / net_by_class["length_m"].sum()

    # simulated VKT per road class: occupancy_veh * length_m summed over all
    # snapshot bins is proportional to vehicle-time-on-edge; combined with
    # flow_veh_h_uncapped * bin_hours * length_m gives vehicle-km actually
    # traversed (Little's-Law based, valid in free-flow -- true for the vast
    # majority of edge-bins per the fundamental-diagram work already done).
    merged = bins.merge(edges[["edge_id", "road_class", "length_m"]], on="edge_id", how="left",
                         suffixes=("", "_net"))
    merged["bin_hours"] = (merged["bin_end_s"] - merged["bin_start_s"]) / 3600.0
    merged["vkt"] = merged["flow_veh_h_uncapped"] * merged["bin_hours"] * merged["length_m"] / 1000.0

    vkt_by_class = merged.groupby("road_class")["vkt"].sum()
    vkt_share = vkt_by_class / vkt_by_class.sum()

    report = net_by_class.join(vkt_by_class.rename("simulated_vkt_km"), how="outer")
    report["vkt_share"] = vkt_share
    report["vkt_share_over_length_share"] = report["vkt_share"] / report["length_share"]
    report["label"] = [ROAD_CLASS_LABELS.get(int(i), str(i)) for i in report.index]
    report = report.sort_index()
    return report


def summarize(report: pd.DataFrame, city: str) -> None:
    arterial = report[report.index.isin(ARTERIAL_CLASSES)]
    local = report[~report.index.isin(ARTERIAL_CLASSES)]
    art_length_share = arterial["length_share"].sum()
    art_vkt_share = arterial["vkt_share"].sum()
    print(f"\n=== {city}: arterie (Motorway..PrimaryLink) vs rete locale ===")
    print(f"quota lunghezza rete (arterie): {100*art_length_share:.1f}%")
    print(f"quota VKT simulato (arterie):   {100*art_vkt_share:.1f}%")
    print(f"rapporto quota-VKT/quota-lunghezza: {art_vkt_share/art_length_share:.2f}x")
    print(
        "(in una rete reale le arterie portano MOLTO più del loro peso in "
        "lunghezza -- un rapporto vicino a 1.0x o sotto indica che il "
        "traffico simulato NON si concentra sulle arterie come nella realtà)"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--city", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--out", type=Path, default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = diagnose(args.city, args.date)
    out = args.out or (REPO_ROOT / f"reports/diagnostics/spatial_concentration/{args.city}_{args.date}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out)
    print(report[["label", "n_edges", "length_share", "vkt_share", "vkt_share_over_length_share"]]
          .to_string(float_format=lambda x: f"{x:.4f}"))
    summarize(report, args.city)
    print(f"\n[scritto] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
