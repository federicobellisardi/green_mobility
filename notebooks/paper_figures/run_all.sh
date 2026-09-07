#!/usr/bin/env bash
# Executes all 6 paper-figure notebooks in place, in order. Each notebook is
# self-contained (resolves REPO_ROOT itself, no shared kernel state) and only
# uses already-computed outputs / light analysis -- no NOMAD, simulation,
# download, canopy, or building-shadow recomputation.
set -euo pipefail
cd "$(dirname "$0")"

JUPYTER=${JUPYTER:-/home/fbellisardi/.conda/envs/geo_flow/bin/jupyter}

for nb in 01_figure1_data_landscape.ipynb \
          02_figure2_multimodal_thermal_framework.ipynb \
          03_figure3_dynamic_thermal_exposure.ipynb \
          04_figure4_shade_and_phenology.ipynb \
          05_figure5_canopy_optimization.ipynb \
          06_figure6_cross_city_generalization.ipynb; do
  echo "=== executing $nb ==="
  "$JUPYTER" nbconvert --to notebook --execute --inplace "$nb"
done
echo "=== all 6 notebooks executed ==="
