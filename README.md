# green_mobility

Reproducible pipeline — built on top of the [NOMAD](external/nomad) agent-based
mobility simulator, used as a black-box engine whose simulation logic is
never modified — to simulate car, pedestrian and bike demand in **Palma de
Mallorca, Zaragoza, Murcia and Valladolid**, and to study where tree canopy /
shade would most reduce the thermal exposure of active mobility (walking,
cycling). The one deliberate exception: NOMAD's Python bindings were missing
the ability to actually run a simulation from Python at all (see "Known
NOMAD gaps" #2) — that binding-only gap was fixed, with explicit sign-off,
on NOMAD's own `feature/python-multimodal-bindings` branch; no simulation
algorithm was touched.

## Status

This is the **scaffolding phase**: config, the NOMAD wrapper, the
intervention-comparison math, and all unit tests are complete and passing
with no external data required. NOMAD itself has been built and its Python
bindings fixed and verified (see "Known NOMAD gaps" #2) on a feature branch —
not yet merged to `main`, so re-cloning this repo fresh still checks out the
pre-fix commit until that happens. Producing real per-city results still
requires, in order: merging that branch (or pointing the submodule at it),
obtaining the FUA boundaries file, downloading OSM/MITMA data, and (for the
thermal side) supplying real canopy/weather/vulnerability data — see
"Pipeline stages" below. None of that has been run for real cities yet.

## Architecture

```
green_mobility/
├── external/nomad/            # git submodule — simulation engine, NEVER modified
├── configs/
│   ├── cities/                # one independent YAML per city
│   └── interventions/         # shared strategy/q-grid config (the experiment design)
├── data/                      # gitignored; uses NOMAD's own --data-dir layout:
│   ├── fua/boundaries.gpkg    #   JRC/OECD Functional Urban Areas (you supply this)
│   ├── osm/                   #   regional + FUA-clipped .osm.pbf
│   ├── od_raw/                #   raw MITMA viajes + zonification
│   └── {city_slug}/            #   NOMAD-owned: nodes/edges.parquet, graph.bin, od_*.csv
│       └── green_mobility/    #   OUR OWN artifacts: scenario configs, flows, thermal, vulnerability
├── results/{city_slug}/       # NOMAD's output_dir convention + intervention_comparison.csv
├── src/green_mobility/
│   ├── config.py              # city/scenario/intervention schema + validation
│   ├── nomad_wrapper/         # runner.py (nomad_cli), build.py (calls NOMAD's own scripts),
│   │                          # flows.py (per-mode flow extraction), paths.py
│   ├── network_prep/          # boundaries.py (FUA lookup), osm_download.py (per-region Geofabrik)
│   ├── thermal/                # canopy.py, shade.py, utci.py
│   ├── vulnerability/index.py
│   ├── exposure/combine.py
│   ├── interventions/          # strategies.py, compare.py
│   ├── manifest.py             # provenance JSON written alongside every artifact
│   └── cli.py                  # `gm` entrypoint
└── tests/unit/                 # no external data / NOMAD build required
```

## Setup

```bash
git submodule update --init external/nomad   # if not already checked out

conda env create -f environment.yml
conda activate green_mobility
pip install -e .
pytest tests/unit   # should pass with zero external data
```

### Building NOMAD (required before `gm run` / `gm flows`)

```bash
cd external/nomad
conda env create -f environment.yml && conda activate nomad
/usr/bin/cmake -B build -DCMAKE_BUILD_TYPE=Release -DNOMAD_BUILD_TESTS=ON \
  -DCMAKE_PREFIX_PATH="$HOME/miniconda3/envs/nomad" -Wno-dev
cmake --build build -j$(nproc)
ctest --test-dir build   # 63/63 should pass
```

`green_mobility` never invokes cmake/make itself — `nomad_wrapper.paths`
only locates the result and raises a clear error (with this exact command)
if it isn't there yet.

**Known runtime quirk (`gm flows` / anything importing `nomad._nomad_core`
in the same process as pandas/pyarrow):** in a "nomad" conda env built per
the above, `pandas` loads the *system* `libstdc++.so.6` before NOMAD's own
`libtbb` gets a chance to load the newer one it needs from the conda env —
and once a library's SONAME is loaded, the dynamic linker won't load a
second copy, regardless of `_nomad_core.so`'s own correct RPATH. Symptom:
an `ImportError` mentioning `CXXABI_...` even though the build is fine.
Fix — preload the conda env's own copy first:
```bash
LD_PRELOAD="$CONDA_PREFIX/lib/libstdc++.so.6" python -m gm flows ...
```
`nomad_wrapper.paths.require_nomad_python()` detects this specific error and
prints this exact fix rather than the generic "not built" message.

## Pipeline stages (`gm` CLI)

```
gm prep-boundaries [--city SLUG ...]       # validate data/fua/boundaries.gpkg against configured cities
gm prep-network --city SLUG [--force-clip] # download regional OSM, run NOMAD's simplify_osm.py
gm prep-demand --city SLUG [--scale 1.0]   # download MITMA, run NOMAD's build_od.py
gm run --city SLUG --scenario KEY          # materialize ScenarioConfig JSON, run nomad_cli
gm flows --city SLUG --scenario KEY        # per-edge-per-hour-per-mode flow (see "Known NOMAD gaps")
gm intervene --city SLUG --scenario KEY [--strategies ...] [--q 0.01,0.05,0.10,0.20]
```

Every stage writes a `<output>.manifest.json` (`manifest.py`) recording the
command, parameters, timestamp, and both this repo's and NOMAD's git commit —
an artifact without a manifest next to it should be treated as untrusted.

`gm intervene` expects a combined per-edge table
(`data/{slug}/green_mobility/exposure/{scenario}_edges.parquet` with columns
`edge_id, length_m, flow_total, flow_active, exposure_score,
vulnerability_index, utci_peak_c`) that isn't auto-chained from the stages
above, because building it requires real external data this repo does not
fabricate (canopy raster, hourly weather, census indicators) — join
`edges.parquet` with `nomad_wrapper.flows` output, `thermal.utci.edge_hour_utci`,
`exposure.combine.compute_exposure`, and `vulnerability.index.edge_vulnerability`
yourself once those inputs are in hand (see "Thermal data sources" below).

## Known NOMAD gaps (audited against submodule commit `e5d9778`; gap #2 fixed
by commit `fd2678e` on `feature/python-multimodal-bindings`, not yet on `main`)

1. **Per-mode edge flow is car-only, full stop.** `QueueTrafficModel::on_enter/
   on_exit` — the only thing that ever updates `LinkState.occupancy/inflow/
   outflow` — is called only when `hot_.mode[a] == AgentMode::Car`
   (`src/core/simulation.cpp:263,291`). `GeoJsonWriter` and
   `Simulation.link_states()` therefore never see walk/bike agents at all.
2. **~~The Python bindings can't currently run a real simulation of any
   mode.~~ Fixed on `external/nomad`'s `feature/python-multimodal-bindings`
   branch (not yet merged to `main`).** `Simulation::set_router`/
   `set_traffic_model` existed in C++ (`include/nomad/core/simulation.hpp:
   66-67`) but were not bound in `bindings/pynomad.cpp` — nor were
   `AStarRouter`/`CHRouter`/`QueueTrafficModel`/`LtmTrafficModel` themselves.
   Without a router, `simulation.cpp`'s pre-routing silently no-op'd
   (`if (!router_ || n == 0) return;`) instead of erroring — a bare
   Python-driven `Simulation` "succeeded" with an empty result. That branch
   binds all of the above, plus `has_router`/`has_traffic_model` and an
   explicit `ValueError` from `run()`/`run_until()`/`step()` when either is
   missing, verified in `external/nomad/tests/python/
   test_multimodal_bindings.py`. `nomad_wrapper.flows.
   assert_flow_extraction_available()` still runs its own `hasattr` checks
   before every extraction, so if this repo's submodule pointer ever moves
   back to a pre-fix commit, it goes back to failing loudly instead of
   silently returning a fabricated all-zero flow table.
3. **`ParquetWriter` is an unimplemented stub** (`src/output/parquet_writer.cpp`
   — both hooks are `// TODO Phase 4`); `"writers": ["parquet"]` silently
   produces nothing.
4. **`nomad.Scenario.from_config`** (`python/nomad/scenario.py:85-101`) passes
   `cfg.simulation.router` (a string) as an OSM file path — unused here.
5. **No FUA boundary data ships with NOMAD** — `data/fua/boundaries.gpkg`
   must be supplied yourself (see "Thermal / canopy / vulnerability data
   sources" below; validated by `network_prep.boundaries`).

**Minimal NOMAD patch for #1** (gap #2 is now fixed — see above; this is what
remains): remove the `AgentMode::Car` gate on `traffic_->on_enter/on_exit`,
add small per-mode `LinkState` counters, and finish `ParquetWriter` — making
walk/bike flow visible through the existing CLI/GeoJSON path too, with no
Python-round-trip needed. Not applied here — NOMAD is not modified by this
repo beyond the binding fix already merged onto its feature branch. The
mode-isolated Python-hook approach `flows.py` uses instead (now working) is
scientifically equivalent for each mode's own numbers regardless: walk/bike
travel time only depends on their own mode-capped free-flow speed, and car
congestion only depends on other car agents — cross-mode presence changes
neither.

## Thermal / canopy / vulnerability data sources

None of this data is bundled or fabricated. Every module documents its
expected input and fails with a clear error if the file is missing, rather
than substituting a plausible-looking default.

| Input | Recommended open source | Module |
|---|---|---|
| City boundary | JRC/OECD "Functional Urban Areas" (GHSL / OECD Metropolitan Areas) | `network_prep.boundaries` |
| Tree canopy | ESA WorldCover 10m v200, class 10 "Tree cover" (CC-BY-4.0) | `thermal.canopy` |
| Air temp / humidity / wind | Copernicus ERA5-Land hourly reanalysis (needs a free CDS account) | `thermal.utci` |
| UTCI formula | Bröde et al. (2012), via the `pythermalcomfort` package | `thermal.utci` |
| Vulnerability indicators | INE census-section statistics (age structure, Atlas de Renta) | `vulnerability.index` |

**Explicit modelling assumptions** (both configurable, neither a measured
fact for these specific cities — see each module's docstring):
- Shade = canopy fraction only; real building-shadow casting is out of scope
  for v1 (`thermal.shade`).
- Full-canopy shade reduces mean radiant temperature by
  `DEFAULT_MRT_REDUCTION_FULL_SHADE_C = 12.0°C` at its default, a value drawn
  from the range reported in urban-microclimate field studies, not a
  calibrated figure for Palma/Zaragoza/Murcia/Valladolid specifically
  (`thermal.utci`).

## Intervention comparison

`q` (1/5/10/20%) is the share of **total street-edge length** that receives
new canopy/shade under a given strategy (agreed convention — see
`configs/interventions/strategies.yaml`). Strategies:

| Strategy | Ranks edges by |
|---|---|
| `random` | uniform random order (null baseline) |
| `thermal_hotspot` | peak UTCI, descending |
| `flow` | total flow (all modes), descending |
| `active_mobility` | walk+bike flow, descending |
| `optimized` | `exposure_score` = Σ_hour active_flow × max(0, UTCI − 26°C), descending |
| `equity` | 50/50 normalized blend of `exposure_score` and the vulnerability index |

`gm intervene` reports, per (strategy, q): edges/km selected, share of
network length, `exposure_addressed` (the share of the day's exposure_score
that strategy's selection would cover — an upper bound under full
mitigation, not a claim about actual post-intervention UTCI), and coverage
of active flow / vulnerability.

## Testing

```bash
pytest tests/unit          # config, strategies, UTCI, exposure, vulnerability, runner, manifest — no external data
pytest tests/integration   # requires a built NOMAD; auto-skips otherwise
```

End-to-end validation for a real city run should be checked against the
numbers NOMAD's own README already publishes for Palma (96.5% arrival rate,
713,065 car agents at `demand_scale=1.0` for 2022-02-08) — a real, citable
regression check.
