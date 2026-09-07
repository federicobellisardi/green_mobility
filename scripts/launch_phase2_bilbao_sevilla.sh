#!/usr/bin/env bash
# Phase 2 of the corrected multimodal v2 thermal analysis: contrast cities
# Bilbao + Sevilla, 3 dates (2022-02-08 winter, 2022-07-14 summer weekday,
# 2022-07-02 summer Saturday -- matching 3 of Palma's own pilot dates for
# direct cross-city comparison). Walk/bike person-hours are date-independent
# (weekday_full/weekend_full already computed for both cities) -- only
# canopy_v2, building_shadow, and car simulations for these specific 3 dates
# are new work.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

CITIES=(bilbao sevilla)
DATES=(2022-02-08 2022-07-14 2022-07-02)

# --- 1. Canopy fusion v2 (needed 32GB last time WorldCover+STL OOM'd at 16GB) ---
for city in "${CITIES[@]}"; do
  runlog -t 4:00 -m 32 -j "canopy_v2_${city}" -K "canopy_v2_phase2" \
    python3 scripts/compute_canopy_v2.py --city "$city"
done

# --- 2. Building shadow ray-casting (measured ~1h/date on Palma) ---
for city in "${CITIES[@]}"; do
  for d in "${DATES[@]}"; do
    runlog -t 3:00 -m 8 -j "building_shadow_${city}_${d}" -K "building_shadow_phase2" \
      python3 scripts/compute_building_shadow.py --city "$city" --date "$d"
  done
done

# --- 3. Car simulations for these 3 specific dates (not in the existing
#        5-date main-text set already validated for both cities) ---
for city in "${CITIES[@]}"; do
  for d in "${DATES[@]}"; do
    runlog -t 72:00 -m 64 -j "nomad_${city}_${d}" -K "nomad_run_phase2" \
      python3 -m green_mobility.cli run --city "$city" --scenario weekday_full_car --date "$d"
  done
done
