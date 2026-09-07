#!/usr/bin/env bash
# LTM+fix (Fase 1-4 completo: capacità + discharge cap + gate_wait_s) per le
# 4 città non toccate dal Livello 2 (Cordoba/Granada/Sevilla calibrate via
# ESOC11, Murcia senza evidenza reale -- nessuna ha CITY_TRANSIT_SHARE,
# restano sulla tabella transit nazionale fissa). Stesso ambiente validato
# di launch_simulations.sh.
#
# --ltm-discharge-cap aggiunto dopo aver confermato che senza (solo Fase 1-2)
# il teleport risultava drammaticamente e irrealisticamente alto -- vedi
# scripts/launch_level2_ltm_test.sh per i dettagli della verifica su Palma.
#
# Sevilla a scala ridotta (0.4): 2.4M viaggi auto, paragonabile a Barcelona
# (che a scala piena SENZA discharge cap ha impiegato ~39h) -- le altre 3
# sono nell'ordine di Bilbao (finita in ~2h a scala piena), quindi vanno a
# scala piena.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

for city in cordoba granada murcia; do
  runlog -t 72:00 -m 64 -j "nomad_${city}_ltm_dcap" -K "nomad_run_ltm" \
    python3 -m green_mobility.cli run \
      --city "$city" --scenario weekday_full_car --date 2022-02-08 --traffic-model ltm \
      --ltm-discharge-cap --ltm-discharge-burst-s 10
done

runlog -t 72:00 -m 64 -j "nomad_sevilla_ltm_dcap_scale0.4" -K "nomad_run_ltm" \
  python3 -m green_mobility.cli run \
    --city sevilla --scenario weekday_full_car --date 2022-02-08 --traffic-model ltm \
    --demand-scale 0.4 --ltm-discharge-cap --ltm-discharge-burst-s 10
