# Figure plan — green_mobility paper

Sequence told across the six figures: **data → model → exposure → shade → optimization → generalization**.

Status legend: READY (real data, computed here), PARTIAL (real data, but with a stated
limitation — a missing mode, an un-normalized metric, single-city only, etc.), BLOCKED
(no fabricated data; the panel names exactly what is missing).

---

## Figure 1 — Data Landscape
**Claim**: the study integrates climate, urban morphology, vegetation, population and
mobility across 12 climatically diverse Spanish cities.

| Panel | Content | Status | Note |
|---|---|---|---|
| A | 12-city map, climate region, size=population | READY | Climate region = reference geographic classification of Spain (5 categories the study itself defines), not a downloaded/computed dataset. Population = MITMA district-level estimate. |
| B | Palma network + Building Block Height | READY | |
| C | Canopy source categorical map (WorldCover/STL/union) | READY | Uses the Phase-1-validated spatial union (`source_flags`), not the probabilistic formula. |
| D | Population + vulnerability | PARTIAL | Population READY; vulnerability index BLOCKED (never computed — income/poverty only 4/11 cities, no composite index built). |

**Caption (provisional)**: *Twelve Spanish cities spanning five climate regimes provide the
basis for this study. (A) Geographic distribution and modelled population. (B) Palma de
Mallorca's street network overlaid on real building heights (Urban Atlas Building Block
Height). (C) Tree canopy detected by ESA WorldCover, the Urban Atlas Street Tree Layer, and
their validated spatial union. (D) Modelled resident population (MITMA); a composite
vulnerability index has not yet been constructed.*

---

## Figure 2 — Multimodal Thermal Framework
**Claim**: the pipeline transforms observed multimodal demand and weather into dynamic
thermal exposure on street edges.

| Panel | Content | Status | Note |
|---|---|---|---|
| A | Pipeline schema | READY | Conceptual diagram, matplotlib. |
| B | Palma car/walk/bike person-hours | PARTIAL | Walk/bike READY; car is a proxy (`person_seconds_est`) from a run whose teleport-rate status against the corrected thresholds has not been re-verified here. |
| C | Shade geometry schema (0/B/T/BT) | READY | Conceptual. |
| D | Hourly example, ambient/building/tree/combined UTCI | READY | Palma, 2022-07-14, evergreen; light re-aggregation of existing building-shadow + canopy_v2, no ray-casting recomputation. |

**Caption (provisional)**: *(A) MITMA origin-destination demand is routed by mode, converted
to edge-hour person-hours, and combined with observed UTCI and modelled tree/building shade
into heat/cold dose. (B) Palma's car (proxy), walk and bike person-hours share the same
network. (C) Shade is decomposed into four states — ambient, building-only, tree-only,
combined — via a spatial/probabilistic union, never a direct sum. (D) A representative edge
shows how building and tree shade separately and jointly shift UTCI through a summer day.*

---

## Figure 3 — Dynamic Thermal Exposure
**Claim**: exposure varies across modes, seasons, hours and cities; national heatwave
declarations do not always coincide with local extremes.

| Panel | Content | Status | Note |
|---|---|---|---|
| A | Seasonal cycle, walk+bike | PARTIAL | v1 (tree-only, no buildings); car BLOCKED; shown as absolute dose (v1 lacks a persisted person-hours column for /1000 normalization). Monthly medoids only, weighted by days-in-month, no event-date double counting. |
| B | Hourly ambient-UTCI profiles | PARTIAL | Ambient climate context only; mode-specific dose overlay only available for Palma (Fig. 2D). |
| C | Hotspot maps | PARTIAL | Walk/bike READY (top-1% exposure edges); car BLOCKED for thermal dose — only a congestion-delay hotspot file exists, a different metric, not conflated here. |
| D | AEMET vs local-extreme mismatch | READY | 11 cities × 26 dates (Barcelona excluded, still blocked upstream), real denominator reported. |

**Caption (provisional)**: *(A) Absolute walk+bike heat benefit and cold cost across the 12
monthly medoids (tree-only, v1). (B) Ambient UTCI diurnal cycles differ by city and by
whether the date falls inside a declared heatwave. (C) Palma's top-1% exposure edges for
walk and bike (car dose not yet computed). (D) National heatwave declarations and locally
extreme UTCI percentiles frequently disagree, city by city.*

---

## Figure 4 — Shade, Buildings and Phenology
**Claim**: the tree contribution must be isolated from the building baseline; phenology
changes the seasonal balance.

| Panel | Content | Status | Note |
|---|---|---|---|
| A | Four-state decomposition | READY | Palma, walk+bike combined; car BLOCKED. |
| B | Marginal tree benefit given buildings, by mode | READY | Walk and bike computed separately (light re-aggregation); car BLOCKED. |
| C | Evergreen vs deciduous annual cycle | READY | v1, walk+bike, tree-only. Phenology ramp is a **prescribed model input**, shown as modelled, not observationally validated. |
| D | Building-coefficient sensitivity | PARTIAL | Palma only; cross-city extension pending Phase 2 (Bilbao/Sevilla, in progress). |

**Caption (provisional)**: *(A) Isolating the building baseline (B) from the tree's own
marginal contribution given existing buildings (T given B) prevents attributing urban-form
effects to vegetation. (B) The same decomposition holds separately for walk and bike. (C)
Deciduous canopy trades some summer benefit for reduced winter cost under a modelled,
non-validated phenology schedule. (D) The tree's marginal benefit is comparatively stable
across a wide range of assumed building-shade coefficients — no coefficient is asserted as
correct.*

---

## Figure 5 — Where to Plant Under a Budget — **BLOCKED**
**Claim (intended)**: the same new-canopy budget avoids more dose under an exposure-aware
strategy than under static strategies.

All four panels BLOCKED. No optimization/strategy-comparison pipeline has ever been
executed for any city — the combined per-edge table (`exposure_score`, `vulnerability_index`,
`utci_peak_c`) required by `interventions/strategies.py` has never been built, and
`gm intervene` fails immediately with a "file not found" for every city checked.

**Missing to unblock**: (1) build the combined edges table (needs vulnerability_index,
itself BLOCKED, plus a defined `exposure_score`/`utci_peak_c` from the v2 pipeline); (2) run
`allocate_budget` for q=1/5/10/20% across the 6 named strategies including a
multi-realization random baseline with registered seeds; (3) car dose-benefit (BLOCKED
throughout) for panel C's multimodal comparison.

---

## Figure 6 — Cross-City Generalization — **BLOCKED**
**Claim (intended)**: climate, urban form and mobility structure explain when dynamic
optimization offers the greatest advantage.

All four panels BLOCKED. Depends directly on Figure 5's optimization output (never executed)
and on v2 thermal-pipeline completeness for enough cities to support cross-city statistics
(only Palma is fully validated at the time these notebooks were built; Bilbao/Sevilla Phase 2
is in progress).

**Missing to unblock**: Figure 5's outputs, plus canopy_v2 + building_shadow + four-state
decomposition completed for at least 6-8 cities (currently 1), plus a designed
leave-one-city-out transferability procedure (not yet specified anywhere in the codebase).
