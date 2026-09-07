import pandas as pd
import pytest

from green_mobility.interventions.strategies import (
    allocate_budget,
    rank_active_mobility,
    rank_edges,
    rank_equity,
    rank_flow,
    rank_optimized,
    rank_random,
    rank_thermal_hotspot,
)


@pytest.fixture
def edges():
    # 5 edges, deliberately uncorrelated rankings so each strategy picks a
    # different top edge.
    return pd.DataFrame(
        {
            "edge_id": [1, 2, 3, 4, 5],
            "length_m": [1000, 1000, 1000, 1000, 1000],  # 5000 total
            "flow_total": [10, 50, 20, 5, 100],
            "flow_active": [5, 5, 40, 5, 5],
            "exposure_score": [1.0, 1.0, 1.0, 90.0, 1.0],
            "cold_exposure_score": [2.0, 0.0, 5.0, 3.0, 1.0],
            "vulnerability_index": [0.1, 0.1, 0.1, 0.1, 0.9],
            "utci_peak_c": [30.0, 45.0, 30.0, 30.0, 30.0],
        }
    )


def test_rank_thermal_hotspot_picks_highest_utci(edges):
    assert rank_thermal_hotspot(edges).iloc[0] == 2


def test_rank_flow_picks_highest_total_flow(edges):
    assert rank_flow(edges).iloc[0] == 5


def test_rank_active_mobility_picks_highest_active_flow(edges):
    assert rank_active_mobility(edges).iloc[0] == 3


def test_rank_optimized_picks_highest_net_benefit(edges):
    # edge 4's exposure_score (90) dwarfs its cold_exposure_score (3), so it
    # still wins net of the cold term at the default lambda.
    assert rank_optimized(edges).iloc[0] == 4


def test_rank_optimized_falls_back_to_cold_term_when_exposure_is_uniformly_zero():
    # Reproduces a real bug found this session: a cool-climate city/date
    # where UTCI never crosses the heat threshold makes exposure_score
    # uniformly zero everywhere, which used to silently degrade `optimized`
    # to whatever order the caller happened to iterate strategies in. With
    # a real cold_exposure_score term, it should rank by minimizing added
    # cold stress instead.
    edges = pd.DataFrame(
        {
            "edge_id": [1, 2, 3],
            "length_m": [1000, 1000, 1000],
            "flow_total": [10, 10, 10],
            "flow_active": [10, 10, 10],
            "exposure_score": [0.0, 0.0, 0.0],
            "cold_exposure_score": [5.0, 1.0, 3.0],
            "vulnerability_index": [0.5, 0.5, 0.5],
            "utci_peak_c": [20.0, 20.0, 20.0],
        }
    )
    assert rank_optimized(edges).tolist() == [2, 3, 1]


def test_rank_equity_favours_vulnerable_edge_over_pure_exposure(edges):
    # edge 5 has middling exposure but the highest vulnerability; with
    # equity_weight=1.0 it must win outright.
    ranked = rank_equity(edges, equity_weight=1.0)
    assert ranked.iloc[0] == 5


def test_rank_random_is_a_permutation_and_seed_reproducible(edges):
    a = rank_random(edges, seed=1)
    b = rank_random(edges, seed=1)
    c = rank_random(edges, seed=2)
    assert sorted(a.tolist()) == sorted(edges["edge_id"].tolist())
    assert a.tolist() == b.tolist()
    assert a.tolist() != c.tolist() or len(edges) < 2  # extremely unlikely to collide


def test_rank_edges_dispatch_matches_direct_call(edges):
    assert rank_edges("flow", edges).tolist() == rank_flow(edges).tolist()
    with pytest.raises(ValueError):
        rank_edges("not_a_strategy", edges)


def test_allocate_budget_respects_length_share(edges):
    ranked = rank_flow(edges)  # order: 5, 2, 3, 1, 4 (by flow_total desc)
    selected = allocate_budget(ranked, edges, q=0.4)  # 40% of 5000m = 2000m = 2 edges
    assert selected.sum() == 2
    assert set(edges.loc[selected, "edge_id"]) == {5, 2}


def test_allocate_budget_always_selects_at_least_one_edge(edges):
    ranked = rank_flow(edges)
    selected = allocate_budget(ranked, edges, q=0.01)  # 1% of 5000m < one 1000m edge
    assert selected.sum() == 1


def test_allocate_budget_full_network_at_q_1(edges):
    ranked = rank_flow(edges)
    selected = allocate_budget(ranked, edges, q=1.0)
    assert selected.sum() == len(edges)


@pytest.mark.parametrize("bad_q", [0.0, -0.1, 1.1])
def test_allocate_budget_rejects_out_of_range_q(edges, bad_q):
    ranked = rank_flow(edges)
    with pytest.raises(ValueError):
        allocate_budget(ranked, edges, q=bad_q)
