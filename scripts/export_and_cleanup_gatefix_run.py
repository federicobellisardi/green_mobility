#!/usr/bin/env python3
"""Export a finished LTM-gatefix run's raw NOMAD snapshots to the compact
per-edge parquet format the paper-figure notebooks actually read, then
delete the raw results/ tree to reclaim disk space.

Why this exists: the raw network_state_*.geojson snapshots (one GeoJSON
per 15-min bin, one feature per network edge) scale with city network
size, not demand -- Madrid/Barcelona/Valencia/Sevilla run ~30-35MB/snapshot
(~3GB/date, ~15-17GB for a full 5-date city) vs Palma's ~1.7MB/snapshot
(~1GB for all 5 dates). The exported parquet is the same data losslessly
re-aggregated into one row per (edge, 15-min bin) and is ~4x smaller
(verified: Palma 2022-01-15 went from 159MB raw to 38MB parquet). This is
also the ONLY thing any of this project's paper_figures notebooks read
(see notebooks/paper_figures/02_figure2_multimodal_thermal_framework.ipynb,
CARSHARE_SCENARIOS dict) -- the raw snapshots are a disposable intermediate,
not a second copy of the data.

Only touches runs whose log (logs/nomad_<city>_<date>_ltm_gatefix/*.o)
already shows a completed "Event types:" line -- never a raw directory
that a still-running SLURM job might still be writing to.

Usage:
  python scripts/export_and_cleanup_gatefix_run.py [--dry-run] [--keep-raw]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from green_mobility.config import CONFIGS_DIR, load_all_cities  # noqa: E402
from green_mobility.nomad_wrapper.car_snapshots import load_car_bins  # noqa: E402

RUN_DIR_RE = re.compile(r"^weekday_full_car_(\d{4}-\d{2}-\d{2})_ltm_dcap10_pretrip$")


def log_shows_completed(city_slug: str, date: str) -> bool:
    log_dir = REPO_ROOT / "logs" / f"nomad_{city_slug}_{date}_ltm_gatefix"
    if not log_dir.exists():
        return False
    for f in log_dir.glob("*.o"):
        if "Event types:" in f.read_text(errors="ignore"):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would be done, change nothing")
    ap.add_argument("--keep-raw", action="store_true", help="export but do not delete the raw results/ tree")
    args = ap.parse_args()

    cities = load_all_cities(CONFIGS_DIR / "cities")
    results_root = REPO_ROOT / "results"

    total_freed = 0
    n_exported = 0
    for city_slug, city in sorted(cities.items()):
        city_results = results_root / city_slug
        if not city_results.exists():
            continue
        for run_dir in sorted(city_results.iterdir()):
            m = RUN_DIR_RE.match(run_dir.name)
            if not m or not run_dir.is_dir():
                continue
            date = m.group(1)
            snap_dir = run_dir / f"{date}_00-00_23-59"
            if not snap_dir.exists() or not any(snap_dir.glob("network_state_*.geojson")):
                continue

            out_path = (city.green_mobility_dir / "exposure" /
                        f"weekday_full_car_{date}_ltm_gatefix_car_edge_bins.parquet")

            if out_path.exists():
                print(f"[{city_slug} {date}] parquet already exported, "
                      f"{'raw still present -- cleaning up' if not args.keep_raw else 'leaving raw as-is'}")
                if not args.keep_raw and not args.dry_run:
                    size = sum(f.stat().st_size for f in run_dir.rglob('*') if f.is_file())
                    shutil.rmtree(run_dir)
                    total_freed += size
                continue

            if not log_shows_completed(city_slug, date):
                print(f"[{city_slug} {date}] run not confirmed complete in logs/, skipping (still running?)")
                continue

            print(f"[{city_slug} {date}] exporting...")
            if args.dry_run:
                n_exported += 1
                continue

            bins = load_car_bins(snap_dir, city.demand_occupancy_factor)
            if len(bins) == 0:
                print(f"[{city_slug} {date}] WARNING: 0 rows extracted, NOT deleting raw, skipping export")
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            bins.to_parquet(out_path)
            n_exported += 1
            print(f"[{city_slug} {date}] wrote {out_path} ({len(bins):,} rows)")

            if not args.keep_raw:
                size = sum(f.stat().st_size for f in run_dir.rglob('*') if f.is_file())
                shutil.rmtree(run_dir)
                total_freed += size
                print(f"[{city_slug} {date}] removed raw {run_dir} ({size/1e9:.2f} GB freed)")

    print(f"\n{n_exported} run(s) exported, {total_freed/1e9:.2f} GB freed"
          f"{' (dry run, nothing actually changed)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
