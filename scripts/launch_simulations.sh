#!/usr/bin/env bash
# NOMAD car-only baseline sweep, 11 cities x thermal calendar, via runlog.
# Run this FROM YOUR OWN interactive shell (where `run`/runlog work) --
# not from this session. Logs land under ./logs/<job>/ as usual for runlog;
# I can tail those from here once they start appearing.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

# --- environment gm run needs (validated end-to-end in this session) ---
export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

# 11 cities ready (network + OD). Barcelona excluded: NOMAD's NetworkCleaner
# hangs reproducibly on its street graph (confirmed via a 300s timeout kill,
# not solved -- would need authorization to touch NOMAD's C++ source).
CITIES=(palma_de_mallorca zaragoza murcia valladolid madrid valencia sevilla cordoba granada bilbao a_coruna)

# Full reproducible 26-date calendar (see data/calendar/2022_calendar.json
# and data/calendar/2022_classification.parquet for the full per-city-day
# evidence table this was built from).
ALL_DATES=(2022-01-15 2022-02-09 2022-03-07 2022-04-10 2022-05-12 2022-06-05
           2022-06-06 2022-07-02 2022-07-05 2022-07-10 2022-07-14 2022-07-18
           2022-07-20 2022-07-22 2022-07-25 2022-07-31 2022-08-03 2022-08-06
           2022-08-10 2022-08-14 2022-08-20 2022-08-27 2022-09-23 2022-10-28
           2022-11-14 2022-12-25)

# Recommended MINIMAL main-text set (5 dates, one per season + the genuine
# non-heatwave summer control): 2022-01-15 (DJF medoid), 2022-04-10 (MAM
# medoid), 2022-06-05 (verified locally non-extreme in all 12 cities --
# the one "summer normal" candidate that survived verification everywhere),
# 2022-07-20 (JJA medoid -- itself inside the official 9-26 July heatwave,
# so it doubles as a genuine heatwave day), 2022-10-28 (SON medoid).
MAIN_TEXT_DATES=(2022-01-15 2022-04-10 2022-06-05 2022-07-20 2022-10-28)

# ── Pick ONE of the two loops below (comment out the other) ──────────────

# --- Option 1: minimal main-text set only (5 dates x 11 cities = 55 jobs) ---
DATES=("${MAIN_TEXT_DATES[@]}")

# --- Option 2: full extended robustness set (26 dates x 11 cities = 286 jobs) ---
# DATES=("${ALL_DATES[@]}")

for city in "${CITIES[@]}"; do
  for d in "${DATES[@]}"; do
    runlog -t 72:00 -m 64  -j "nomad_${city}_${d}" -K "nomad_run_${city}" \
      python3 -m green_mobility.cli run \
        --city "$city" --scenario weekday_full_car --date "$d"
  done
done
