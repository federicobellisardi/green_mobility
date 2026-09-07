"""Run every strategy x q combination and tabulate comparison metrics.

`exposure_addressed` is the sum of exposure_score (see exposure/combine.py)
over the edges a strategy selects — read it as "share of the day's active-
mobility heat-stress burden carried by the segments this strategy would
shade", an upper bound assuming planted shade fully removes that edge's
excess-UTCI term, not a claim about the actual post-intervention UTCI drop
(that depends on canopy maturity, species, planting density — out of scope
here; see thermal/utci.py DEFAULT_MRT_REDUCTION_FULL_SHADE_C for the one
physical assumption this pipeline does make).

`cold_exposure_addressed`/`net_benefit_addressed` are the same idea applied
to the cold-side term and to the net heat/cold trade-off
(interventions.strategies._net_benefit, same DEFAULT_LAMBDA) that
`optimized`/`equity` actually rank by — reported here so the table stays
self-consistent with what those two strategies actually selected, not just
the heat-only view.

`net_benefit_addressed` deliberately has no `share_of_total_net_benefit`
sibling, unlike the heat/cold columns: exposure_score and
cold_exposure_score are both sums of non-negative dose terms, so "share of
city total" is always well-behaved, but net_benefit = heat - lam*cold can be
negative city-wide (e.g. any city/scenario where cold burden exceeds heat
burden in aggregate — common for cooler cities/seasons). Dividing the best
(least negative or most positive) strategy's net_benefit_addressed by a
negative total flips its sign, making the genuinely best strategy look
worst — confirmed happening for several cities before this was caught.
Read `net_benefit_addressed`'s absolute value only; higher is always
better regardless of its sign or the city-wide total's sign.
"""
from __future__ import annotations

import pandas as pd

from green_mobility.interventions.strategies import DEFAULT_LAMBDA, _net_benefit, allocate_budget, rank_edges


def compare_strategies(
    edges: pd.DataFrame,
    strategies: tuple[str, ...],
    q_values: tuple[float, ...],
    seed: int = 42,
) -> pd.DataFrame:
    total_length_m = edges["length_m"].sum()
    total_exposure = edges["exposure_score"].sum()
    total_cold_exposure = edges["cold_exposure_score"].sum()
    total_active_flow = edges["flow_active"].sum()

    rows = []
    for strategy in strategies:
        ranked = rank_edges(strategy, edges, seed=seed)
        for q in q_values:
            selected_mask = allocate_budget(ranked, edges, q)
            selected = edges.loc[selected_mask]
            length_m = selected["length_m"].sum()
            net_benefit_selected = _net_benefit(selected, DEFAULT_LAMBDA).sum()
            rows.append(
                {
                    "strategy": strategy,
                    "q": q,
                    "n_edges": int(selected_mask.sum()),
                    "length_km": length_m / 1000.0,
                    "share_of_network_length": length_m / total_length_m if total_length_m else 0.0,
                    "exposure_addressed": selected["exposure_score"].sum(),
                    "share_of_total_exposure": (
                        selected["exposure_score"].sum() / total_exposure if total_exposure else 0.0
                    ),
                    "cold_exposure_addressed": selected["cold_exposure_score"].sum(),
                    "share_of_total_cold_exposure": (
                        selected["cold_exposure_score"].sum() / total_cold_exposure
                        if total_cold_exposure
                        else 0.0
                    ),
                    "net_benefit_addressed": net_benefit_selected,
                    "active_flow_covered": selected["flow_active"].sum(),
                    "share_of_active_flow": (
                        selected["flow_active"].sum() / total_active_flow
                        if total_active_flow
                        else 0.0
                    ),
                    "mean_vulnerability_covered": (
                        selected["vulnerability_index"].mean() if len(selected) else 0.0
                    ),
                }
            )
    return pd.DataFrame(rows)
