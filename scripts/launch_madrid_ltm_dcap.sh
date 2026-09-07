#!/usr/bin/env bash
# Madrid, LTM+fix completo (Fase 1-4), a scala ridotta (0.4). A scala piena
# senza discharge cap ha superato il limite di 72h -- il discharge cap
# dovrebbe ridurre drasticamente il volume di retry su partenze rifiutate
# (verificato su Palma: da un volume enorme a 650), ma testiamo prima a
# scala ridotta prima di rischiare un altro slot di 72h a scala piena.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

runlog -t 72:00 -m 64 -j "nomad_madrid_ltm_dcap_scale0.4" -K "nomad_run_ltm" \
  python3 -m green_mobility.cli run \
    --city madrid --scenario weekday_full_car --date 2022-02-08 --traffic-model ltm \
    --demand-scale 0.4 --ltm-discharge-cap --ltm-discharge-burst-s 10
