#!/usr/bin/env bash
# First-ever car-only baseline run for Barcelona, now unblocked after the
# NetworkCleaner fix (external/nomad, branch fix/barcelona-network-cleaner-hang)
# and a successful prep-network + prep-demand. Validation run on a single
# date first (2022-07-20, the same reference date used throughout the
# teleport-rate investigation for the other 11 cities) before committing to
# the full 5-date main-text set.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

runlog -t 72:00 -m 64 -j "nomad_barcelona_2022-07-20" -K "nomad_run_barcelona" \
  python3 -m green_mobility.cli run --city barcelona --scenario weekday_full_car --date 2022-07-20
