import numpy as np
import pandas as pd
import pytest

from green_mobility.thermal.shade_states import (
    STATES,
    four_state_reductions,
    state_utci_reduction,
    union_shadow_fraction,
)


def _series(values):
    return pd.Series(values, dtype=float)


def test_state_0_is_always_zero_reduction():
    tree = _series([0.0, 0.3, 1.0])
    building = _series([0.0, 0.5, 1.0])
    r = state_utci_reduction(tree, building, "0")
    assert (r == 0.0).all()


def test_state_B_has_no_tree_component():
    tree = _series([0.9])  # even with strong tree shade present in the input...
    building = _series([0.4])
    r_B = state_utci_reduction(tree, building, "B", building_coef=3.0)
    # ...state B must equal building_frac * building_coef only, tree ignored.
    assert r_B.iloc[0] == pytest.approx(0.4 * 3.0)


def test_state_T_has_no_building_component():
    tree = _series([0.4])
    building = _series([0.9])  # strong building shade present, must be ignored for T
    r_T = state_utci_reduction(tree, building, "T", tree_coef=6.0)
    assert r_T.iloc[0] == pytest.approx(0.4 * 6.0)


def test_state_BT_contains_both_and_no_double_counting():
    tree = _series([0.5])
    building = _series([0.5])
    r_BT = state_utci_reduction(tree, building, "BT", tree_coef=6.0, building_coef=6.0)
    # both fully overlapping at coef=6 for both -> equivalent to union*6, not sum*6
    union = union_shadow_fraction(tree, building)
    assert r_BT.iloc[0] == pytest.approx(union.iloc[0] * 6.0)
    # explicitly NOT double counted: tree*6 + building*6 would be 6.0, union-based is less
    assert r_BT.iloc[0] < (0.5 * 6.0 + 0.5 * 6.0)


def test_deciduous_zero_leaf_cover_T_equals_zero():
    # tree_effective_frac already includes the phenology multiplier upstream;
    # leaf-cover=0 means tree_effective_frac=0 regardless of canopy fraction.
    tree_effective = _series([0.0, 0.0])
    building = _series([0.2, 0.6])
    r_T = state_utci_reduction(tree_effective, building, "T", tree_coef=6.0)
    assert (r_T == 0.0).all()


def test_deciduous_zero_leaf_cover_BT_equals_B():
    tree_effective = _series([0.0, 0.0])
    building = _series([0.2, 0.6])
    r_BT = state_utci_reduction(tree_effective, building, "BT", tree_coef=6.0, building_coef=4.0)
    r_B = state_utci_reduction(tree_effective, building, "B", building_coef=4.0)
    assert np.allclose(r_BT.values, r_B.values)


def test_building_coef_required_for_B_and_BT():
    tree = _series([0.5])
    building = _series([0.5])
    with pytest.raises(ValueError):
        state_utci_reduction(tree, building, "B")
    with pytest.raises(ValueError):
        state_utci_reduction(tree, building, "BT")


def test_invalid_state_raises():
    tree = _series([0.5])
    building = _series([0.5])
    with pytest.raises(ValueError):
        state_utci_reduction(tree, building, "X")


def test_union_shadow_fraction_bounds_and_no_double_counting():
    tree = pd.Series(np.random.RandomState(0).uniform(0, 1, 200))
    building = pd.Series(np.random.RandomState(1).uniform(0, 1, 200))
    union = union_shadow_fraction(tree, building)
    assert (union >= tree - 1e-9).all()  # union always >= either component alone
    assert (union >= building - 1e-9).all()
    assert (union <= 1.0 + 1e-9).all()
    assert (union >= 0.0 - 1e-9).all()


def test_four_state_reductions_no_nan_or_inf_and_order_invariant():
    rng = np.random.RandomState(42)
    tree = pd.Series(rng.uniform(0, 1, 500))
    building = pd.Series(rng.uniform(0, 1, 500))
    states = four_state_reductions(tree, building, tree_coef=6.0, building_coef=3.0)
    for s in STATES:
        vals = states[s].values
        assert not np.isnan(vals).any()
        assert not np.isinf(vals).any()

    shuffled_idx = rng.permutation(500)
    states_shuffled = four_state_reductions(tree.iloc[shuffled_idx].reset_index(drop=True),
                                             building.iloc[shuffled_idx].reset_index(drop=True),
                                             tree_coef=6.0, building_coef=3.0)
    # re-sort both by the original values to compare -- order invariance means
    # the SAME (tree,building) pair yields the same reduction regardless of
    # row position.
    orig_bt = states["BT"].iloc[shuffled_idx].reset_index(drop=True)
    assert np.allclose(orig_bt.values, states_shuffled["BT"].values)


def test_bt_reduction_never_exceeds_stronger_coefficient_times_union():
    # sanity ceiling: BT reduction should never exceed union_frac * max(coef)
    rng = np.random.RandomState(7)
    tree = pd.Series(rng.uniform(0, 1, 300))
    building = pd.Series(rng.uniform(0, 1, 300))
    tree_coef, building_coef = 6.0, 2.0
    r_bt = state_utci_reduction(tree, building, "BT", tree_coef=tree_coef, building_coef=building_coef)
    union = union_shadow_fraction(tree, building)
    ceiling = union * max(tree_coef, building_coef)
    assert (r_bt <= ceiling + 1e-9).all()
