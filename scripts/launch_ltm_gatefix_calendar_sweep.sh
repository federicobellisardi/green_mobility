#!/usr/bin/env bash
# Full LTM+fix stack (capacity derating + discharge cap + pretrip-reroute +
# exit-gate wait signal for spillback) across all 12 working cities and the
# 5-date main-text calendar (see scripts/launch_simulations.sh for the same
# MAIN_TEXT_DATES set, used there with the default QueueTrafficModel).
#
# Validated on Palma (2022-02-08, full demand scale) before this sweep:
# teleport rate dropped from 39.5% (LTM+dcap+pretrip, no gate-wait fix) to
# 24.1% (same stack + gate-wait fix) over a full day, with 4/5 previously
# permanently-gridlocked Via de Cintura segments now genuinely clearing by
# end of day. See external/nomad feature/ltm-exit-gate-signal for the fix.
#
# Barcelona RE-INCLUDED (previously excluded here for a NetworkCleaner hang
# on its street graph): the fix already existed on the unmerged
# feature/logit-mode-choice branch (af9952b, "prevent infinite
# re-contraction of already-absorbed nodes") and landed on main via today's
# merge -- verified end-to-end just now (`gm prep-network --city barcelona`
# completes in ~5s: 2,091,861 -> 359,046 nodes), no longer a blocker.
#
# First 55-job attempt at this sweep (11 cities, no Barcelona) mostly
# failed with SIGILL -- NOT a nomad logic bug: the build's
# `-march=native` baked in this dev node's (slurm24) specific CPU
# extensions, and 51/55 jobs landed on cluster nodes lacking one of them.
# Fixed in external/nomad by pinning to the portable `-march=x86-64-v2`
# (see cmake/CompilerFlags.cmake); verified by srun'ing the rebuilt binary
# directly on two of the previously-failing nodes (slurm05, slurm12). The
# rebuilt binary is what this script's `python3 -m green_mobility.cli run`
# now dispatches to.
#
# Madrid at full demand scale previously exceeded the 72h SLURM limit
# WITHOUT the discharge cap (see launch_madrid_ltm_dcap.sh, which fell back
# to demand_scale=0.4). This sweep tries Madrid at full scale like every
# other city, since the discharge cap + pretrip-reroute + gate-wait fix
# together directly target the DepartRejected retry storm that caused that
# timeout -- monitor it specifically; fall back to --demand-scale 0.4 for
# Madrid alone if it's still running close to the time limit.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

CITIES=(palma_de_mallorca zaragoza murcia valladolid madrid valencia sevilla cordoba granada bilbao a_coruna barcelona)

MAIN_TEXT_DATES=(2022-01-15 2022-04-10 2022-06-05 2022-07-20 2022-10-28)

for city in "${CITIES[@]}"; do
  for d in "${MAIN_TEXT_DATES[@]}"; do
    runlog -t 128:00 -m 64 -j "nomad_${city}_${d}_ltm_gatefix" -K "nomad_run_ltm_gatefix" \
      python3 -m green_mobility.cli run \
        --city "$city" --scenario weekday_full_car --date "$d" --traffic-model ltm \
        --ltm-discharge-cap --ltm-discharge-burst-s 10 --pretrip-reroute
  done
done
