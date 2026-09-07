#!/usr/bin/env bash
# Thermal-pipeline v2 work (canopy fusion, building shadow, LCLU plantability,
# weekend walk/bike flows) via runlog, on the SLURM cluster instead of this
# shared interactive box (load average was 23-25 on 4 cores from OTHER
# users' jobs -- confirmed the bottleneck was contention, not this code:
# the LCLU timing test's wall-clock time went UP after vectorizing the
# aggregation while its actual CPU-seconds (user time) stayed flat).
#
# Run this FROM YOUR OWN interactive shell (where `run`/runlog work) -- not
# from this session. Logs land under ./logs/<job>/ as usual for runlog.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

# --- 1. Weekend walk/bike flows: 9 cities still pending (palma, zaragoza
#        already done locally before the switch to runlog) ---
WEEKEND_PENDING=(murcia valladolid a_coruna cordoba granada bilbao sevilla valencia madrid)
for city in "${WEEKEND_PENDING[@]}"; do
  runlog -t 2:00 -m 16 -j "weekend_flows_${city}" -K "weekend_flows" \
    python3 scripts/compute_walk_bike_flows.py --city "$city" --scenario weekend_full
done

# --- 2. Canopy fusion v2 (WorldCover + Street Tree Layer), Palma pilot only ---
runlog -t 4:00 -m 16 -j "canopy_v2_palma_de_mallorca" -K "canopy_v2" \
  python3 scripts/compute_canopy_v2.py --city palma_de_mallorca

# --- 3. Building shadow (ray-casting), Palma pilot: 4 dates, one job each
#        (measured ~58 min/date locally under heavy contention -- expect
#        faster on a dedicated cluster core, but request generous time) ---
PILOT_DATES=(2022-02-08 2022-07-14 2022-07-02 2022-12-25)
for d in "${PILOT_DATES[@]}"; do
  runlog -t 3:00 -m 8 -j "building_shadow_palma_${d}" -K "building_shadow" \
    python3 scripts/compute_building_shadow.py --city palma_de_mallorca --date "$d"
done

# --- 4. LCLU plantability, Palma pilot only (full network, buffer=15m,
#        conservative road-margin scenario -- the default) ---
runlog -t 6:00 -m 16 -j "lclu_plantability_palma" -K "lclu_plantability" \
  python3 scripts/compute_lclu_plantability.py --city palma_de_mallorca
