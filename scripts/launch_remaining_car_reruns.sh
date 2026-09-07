#!/usr/bin/env bash
# Remainder of the stuck-agent-threshold re-run (see paper/main/main.tex
# sec:stuck): sevilla's last 2 dates + all of valencia + all of madrid.
# Everything else (7 cities + sevilla's first 3 dates) already completed
# with the corrected stuck_threshold_ratio=50/stuck_max_hours=8 -- confirmed
# via their manifests' teleported_rate (0.00-0.04%).
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

for d in 2022-07-20 2022-10-28; do
  runlog -t 72:00 -m 64 -j "nomad_sevilla_${d}" -K "nomad_run_sevilla" \
    python3 -m green_mobility.cli run --city sevilla --scenario weekday_full_car --date "$d"
done

for d in 2022-01-15 2022-04-10 2022-06-05 2022-07-20 2022-10-28; do
  runlog -t 72:00 -m 64 -j "nomad_valencia_${d}" -K "nomad_run_valencia" \
    python3 -m green_mobility.cli run --city valencia --scenario weekday_full_car --date "$d"
  runlog -t 72:00 -m 64 -j "nomad_madrid_${d}" -K "nomad_run_madrid" \
    python3 -m green_mobility.cli run --city madrid --scenario weekday_full_car --date "$d"
done
