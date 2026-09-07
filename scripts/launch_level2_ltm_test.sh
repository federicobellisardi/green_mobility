#!/usr/bin/env bash
# Test LTM+fix (Fase 1-4: capacità corretta + discharge cap + segnale
# gate_wait_s) sulle 8 città con OD ricalibrato (Livello 2, quota transit
# città-specifica). Stesso ambiente validato di scripts/launch_simulations.sh
# -- lanciare da una shell interattiva propria (dove `runlog` funziona), non
# da questa sessione.
#
# --ltm-discharge-cap aggiunto dopo aver confermato che senza (solo Fase 1-2)
# il teleport risultava drammaticamente e irrealisticamente alto (~48-52% su
# quasi tutte le città) -- con Fase 3-4 attiva, verificato su Palma a scala
# 0.4: teleport crollato da quel livello a 0.01%.
#
# Madrid ESCLUSA da questo lancio: a scala piena ha superato il limite di 72h
# (probabilmente proprio per l'assenza del discharge cap, che genera un
# volume enorme di retry su partenze rifiutate) -- va rilanciata a parte,
# vedi scripts/launch_madrid_ltm_dcap.sh.
set -euo pipefail
cd /home/fbellisardi/code/green_mobility

# --- ambiente richiesto da `gm run` (stessa ricetta di launch_simulations.sh) ---
export GM_NOMAD_PYTHON=/home/fbellisardi/.conda/envs/nomad/bin/python3
export LD_PRELOAD=/home/fbellisardi/.conda/envs/nomad/lib/libstdc++.so.6
export PATH="/home/fbellisardi/.conda/envs/nomad/bin:$PATH"
export PROJ_LIB=/home/fbellisardi/.conda/envs/nomad/share/proj
export PYTHONPATH=src

CITIES=(barcelona valencia bilbao palma_de_mallorca zaragoza valladolid a_coruna)

for city in "${CITIES[@]}"; do
  runlog -t 72:00 -m 64 -j "nomad_${city}_ltm_dcap" -K "nomad_run_ltm" \
    python3 -m green_mobility.cli run \
      --city "$city" --scenario weekday_full_car --date 2022-02-08 --traffic-model ltm \
      --ltm-discharge-cap --ltm-discharge-burst-s 10
done
