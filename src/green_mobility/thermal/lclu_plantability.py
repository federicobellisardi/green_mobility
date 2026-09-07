"""Urban Atlas 2021 land-cover/land-use (LCLU) class -> tree-planting
constraint table, for the FUTURE greening-optimization layer (this module
only builds/validates the constraint layer -- it does NOT run any
optimization, per the project brief).

Classification is NOT "all green = plantable, all grey = not": each class is
judged on what it physically represents (buildings, water, rail corridors,
etc. cannot host street trees; arable land could, but planting street trees
in private farmland is a different intervention than street greening this
study is about). "conditional" classes need a documented, explicit
assumption to resolve into a number -- see PLANTABLE_FRACTION_CONDITIONAL
sensitivity option, never silently defaulted.

Real, honest limitation: Urban Atlas' road classes (12210/12220) do not
distinguish carriageway from verge/median within the polygon -- there is no
sub-class split in this data. Two documented options are exposed
(--road-margin-scenario): "conservative" (roads entirely non-plantable, the
default -- no assumption invented) and "assume-margin" (a fixed, clearly
labelled ASSUMED margin fraction of the road polygon area treated as
conditionally plantable -- a sensitivity variant, not the primary claim).
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

# code_2021 -> (status, reason). status in {"plantable", "non_plantable", "conditional"}.
# Reasons are physical/functional, not a green/grey heuristic.
CLASS_PLANTABILITY: dict[str, tuple[str, str]] = {
    "11100": ("non_plantable", "Continuous urban fabric -- building footprint dominates, negligible open ground"),
    "11210": ("conditional", "Discontinuous dense urban fabric -- courtyards/setbacks may exist between buildings"),
    "11220": ("conditional", "Discontinuous medium density urban fabric -- more open ground between buildings"),
    "11230": ("conditional", "Discontinuous low density urban fabric -- substantial open ground likely present"),
    "11240": ("conditional", "Discontinuous very low density urban fabric -- mostly open ground, sparse buildings"),
    "11300": ("conditional", "Isolated structures -- surrounding open ground, exact footprint not resolved at this scale"),
    "12100": ("conditional", "Industrial/commercial/public/military/private units -- yards and setbacks vary widely"),
    "12210": ("non_plantable", "Fast transit roads -- carriageway/safety clearance, no street-tree planting"),
    "12220": ("conditional", "Other roads -- carriageway vs. verge not distinguished in this data; see road-margin-scenario"),
    "12230": ("non_plantable", "Railways -- track corridor, vegetation control zone, not plantable"),
    "12300": ("non_plantable", "Port areas -- operational hardstanding"),
    "12400": ("non_plantable", "Airports -- operational surfaces, height/wildlife-strike restrictions"),
    "13100": ("non_plantable", "Mineral extraction and dump sites -- active/degraded ground, not a street-greening target"),
    "13300": ("non_plantable", "Construction sites -- transient by definition, not a stable planting target"),
    "13400": ("conditional", "Land without current use -- vacant, physically plantable but tenure/purpose undetermined"),
    "14110": ("plantable", "Green urban areas, public access -- parks etc., directly plantable"),
    "14120": ("conditional", "Green urban areas, private access -- physically plantable but not public street greening"),
    "14130": ("conditional", "Green urban areas, unknown access -- access conditions not resolved in this data"),
    "14200": ("conditional", "Sports and leisure facilities -- some plantable margins, playing surfaces are not"),
    "21000": ("conditional", "Arable land -- physically plantable but active agricultural use/tenure, not street greening"),
    "22000": ("non_plantable", "Permanent crops (vineyards, orchards, olive groves) -- already-planted agricultural use"),
    "23000": ("conditional", "Pastures -- physically plantable, active grazing/agricultural use"),
    "24000": ("conditional", "Complex/mixed cultivation patterns -- mixed agricultural use, tenure undetermined"),
    "31000": ("non_plantable", "Forests -- already forested, not a NEW-planting target"),
    "32000": ("conditional", "Herbaceous vegetation (natural grassland, moors) -- physically plantable, may be protected habitat"),
    "33000": ("non_plantable", "Open spaces with little/no vegetation (beaches, dunes, bare rock, glaciers) -- unsuitable substrate or protected"),
    "40000": ("non_plantable", "Wetlands -- unsuitable substrate, likely protected habitat"),
    "50000": ("non_plantable", "Water -- not land"),
}

ASSUMED_ROAD_MARGIN_FRACTION = 0.15  # ASSUMPTION, sensitivity variant only -- see module docstring


def build_class_reference_table() -> pd.DataFrame:
    """One row per LCLU class: code, description is looked up by the
    caller's own LCLU class_2021 column (not duplicated here to avoid two
    sources of truth for the description text), status, reason,
    sensitivity_option."""
    rows = []
    for code, (status, reason) in CLASS_PLANTABILITY.items():
        sensitivity = (
            f"assume-margin scenario treats {ASSUMED_ROAD_MARGIN_FRACTION:.0%} of area as conditionally plantable"
            if code == "12220" else None
        )
        rows.append({"code_2021": code, "plantability_status": status, "reason": reason, "sensitivity_option": sensitivity})
    return pd.DataFrame(rows)


def edge_plantability(
    edges: gpd.GeoDataFrame,  # [edge_id, geometry] metric CRS, matching lclu's CRS
    lclu: gpd.GeoDataFrame,   # [code_2021, class_2021, geometry], same CRS
    buffer_m: float = 15.0,
    road_margin_scenario: str = "conservative",
) -> pd.DataFrame:
    """For each edge's buffer, the area-weighted share of each LCLU class,
    the currently-treed fraction (a class WOULD be double-accounting with
    thermal.canopy_fusion's canopy_fraction -- this only reports LCLU's own
    "Green urban areas" class shares, not a re-derivation of canopy cover),
    candidate_plantable_fraction, excluded_fraction, and exclusion_reasons.

    road_margin_scenario: "conservative" (default, class 12220 entirely
    excluded, no assumption) or "assume-margin" (ASSUMED_ROAD_MARGIN_FRACTION
    of class 12220's area counted as conditionally plantable -- a documented
    sensitivity variant, not the primary claim).
    """
    if road_margin_scenario not in {"conservative", "assume-margin"}:
        raise ValueError(f"road_margin_scenario must be 'conservative' or 'assume-margin', got {road_margin_scenario!r}")

    buffered = edges[["edge_id"]].copy()
    buffered["geometry"] = edges.geometry.buffer(buffer_m)
    buffered = gpd.GeoDataFrame(buffered, geometry="geometry", crs=edges.crs)
    buffered["buffer_area"] = buffered.geometry.area

    overlay = gpd.overlay(buffered, lclu[["code_2021", "geometry"]], how="intersection")
    overlay["piece_area"] = overlay.geometry.area

    # Vectorized aggregation (pivot to edge_id x code_2021, one pass) instead
    # of a Python-level per-edge loop -- the loop version measured ~3.5 min
    # for 2000 edges (would be ~5h for a ~186k-edge network), dominated by
    # per-edge dict/set construction in pure Python, not by the geopandas
    # overlay call itself.
    shares_wide = overlay.pivot_table(index="edge_id", columns="code_2021", values="piece_area", aggfunc="sum", fill_value=0.0)
    buffer_area = buffered.set_index("edge_id")["buffer_area"]
    shares_wide = shares_wide.div(buffer_area.reindex(shares_wide.index), axis=0)
    # edges with zero overlay area (buffer didn't intersect anything, or
    # buffer_area==0) get an all-NaN/all-zero row -- reindex to include them.
    shares_wide = shares_wide.reindex(buffered["edge_id"])

    treed_fraction = shares_wide.get("31000", pd.Series(0.0, index=shares_wide.index)).fillna(0.0)

    status_of = {code: CLASS_PLANTABILITY.get(code, ("conditional", "Unmapped LCLU class"))[0] for code in shares_wide.columns}
    plantable_cols = [c for c in shares_wide.columns if status_of[c] == "plantable"]
    other_cols = [c for c in shares_wide.columns if status_of[c] != "plantable" and c != "12220"]

    plantable_area_frac = shares_wide[plantable_cols].sum(axis=1) if plantable_cols else pd.Series(0.0, index=shares_wide.index)
    excluded_frac = shares_wide[other_cols].sum(axis=1) if other_cols else pd.Series(0.0, index=shares_wide.index)

    if "12220" in shares_wide.columns:
        road_share = shares_wide["12220"].fillna(0.0)
        if road_margin_scenario == "assume-margin":
            plantable_area_frac = plantable_area_frac + road_share * ASSUMED_ROAD_MARGIN_FRACTION
            excluded_frac = excluded_frac + road_share * (1 - ASSUMED_ROAD_MARGIN_FRACTION)
        else:
            excluded_frac = excluded_frac + road_share

    # exclusion_reasons: built column-by-column (only ~28 classes) rather
    # than row-by-row (thousands of edges) -- for each non-plantable class,
    # find the (typically few) edges where it has nonzero share and append
    # that class's reason string to just those rows.
    reason_lists: dict = {edge_id: [] for edge_id in shares_wide.index}
    for code in shares_wide.columns:
        if status_of[code] == "plantable":
            continue
        status, reason = CLASS_PLANTABILITY.get(code, ("conditional", "Unmapped LCLU class"))
        label = f"{code}: {reason}" + (" (assume-margin scenario)" if code == "12220" and road_margin_scenario == "assume-margin" else "")
        present = shares_wide.index[shares_wide[code].fillna(0.0) > 0]
        for edge_id in present:
            reason_lists[edge_id].append(label)

    rows = []
    for edge_id in shares_wide.index:
        row_shares = shares_wide.loc[edge_id].dropna()
        rows.append({
            "edge_id": edge_id,
            "lclu_class_shares": row_shares.to_dict(),
            "currently_treed_fraction": treed_fraction.loc[edge_id],
            "candidate_plantable_fraction": plantable_area_frac.loc[edge_id],
            "excluded_fraction": excluded_frac.loc[edge_id],
            "exclusion_reasons": sorted(reason_lists[edge_id]),
            "scenario": road_margin_scenario,
        })
    return pd.DataFrame(rows)
