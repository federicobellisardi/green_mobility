"""Fuses ESA WorldCover tree-cover (thermal.canopy) with the Urban Atlas
Street Tree Layer (STL) into a single per-edge canopy estimate, keeping all
three views (worldcover-only, STL-only, fused) rather than silently replacing
one with the other.

Why a pixel-level fusion, not the probabilistic-independence formula
(1-(1-a)*(1-b)): both sources CAN be brought onto the same 10 m grid (STL is
rasterized onto WorldCover's own grid, see rasterize_stl_to_grid) so overlap
is checked directly, pixel by pixel -- a boolean OR -- rather than assumed
under an independence approximation. This avoids double-counting by
construction (a pixel flagged by both sources still counts once) and needs no
documented approximation.

Known, real differences between the two sources (not treated as one being
"ground truth" for the other):
  - WorldCover: global 10 m raster, single per-pixel land-cover class,
    continuous coverage, but not tuned for individual street trees in a
    dense urban canyon.
  - Street Tree Layer: Europe-specific vector product, but only maps tree
    canopy PATCHES >= 500 sq m (confirmed from the actual downloaded data:
    "V005ha" = 0.05 ha = 500 sq m minimum mapping unit) -- isolated street
    trees below that patch size are not captured at all, a real, documented
    gap in STL, not a WorldCover shortcoming.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def rasterize_stl_to_grid(stl: gpd.GeoDataFrame, reference_raster_path: Path, out_path: Path) -> Path:
    """Burns STL tree-patch polygons onto a NEW raster sharing the exact
    transform/shape/CRS of reference_raster_path (e.g. the per-city
    UTM-warped WorldCover VRT already built by scripts/compute_canopy.py) --
    a prerequisite for any pixel-level comparison/fusion with WorldCover.

    stl must already be reprojected to the reference raster's CRS by the
    caller (this function does not reproject).
    """
    import rasterio
    from rasterio.features import rasterize

    with rasterio.open(reference_raster_path) as ref:
        transform = ref.transform
        shape = (ref.height, ref.width)
        crs = ref.crs
        profile = ref.profile
        ref_band = ref.read(1)
        ref_nodata = ref.nodata

    if str(stl.crs) != str(crs):
        raise ValueError(
            f"stl CRS ({stl.crs}) does not match reference raster CRS ({crs}) -- "
            "reproject stl to the reference raster's CRS before calling this."
        )

    burned = rasterize(
        [(geom, 1) for geom in stl.geometry if geom is not None and not geom.is_empty],
        out_shape=shape, transform=transform, fill=0, dtype="uint8", all_touched=False,
    )
    # BUG FOUND during Fase-1 validation: leaving this output's nodata unset
    # (as an earlier version did) meant edge_boolean_fraction computed
    # street_tree_fraction over EVERY pixel in an edge's buffer crop,
    # including pixels the reference (WorldCover) raster considers outside
    # its valid extent -- a DIFFERENT, larger denominator than
    # worldcover_canopy_fraction/fused_canopy_fraction use. That mismatch let
    # fused_canopy_fraction appear to exceed
    # worldcover_canopy_fraction + street_tree_fraction on real Palma edges
    # (up to +0.70 absolute) -- not a double-counting bug, a denominator
    # mismatch. Fix: burn the reference raster's OWN invalid-area mask onto
    # this output too (255 = invalid), so every source shares the exact same
    # valid-pixel universe per edge.
    if ref_nodata is not None:
        invalid_mask = ref_band == ref_nodata
        burned = np.where(invalid_mask, 255, burned).astype("uint8")
        nodata_out = 255
    else:
        nodata_out = None

    # reference_raster_path may itself be a VRT (this project's per-city
    # WorldCover mosaic/warp is always a VRT) -- its driver can't write
    # pixels directly, so the OUTPUT is always a real GeoTIFF regardless of
    # what format the reference happened to be.
    profile.update(driver="GTiff", dtype="uint8", count=1, nodata=nodata_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(burned, 1)
    return out_path


def build_fused_tree_raster(
    worldcover_raster_path: Path, stl_raster_path: Path, out_path: Path,
    tree_class_codes: tuple[int, ...] = (10,),
) -> Path:
    """Boolean OR of (WorldCover class in tree_class_codes) and (STL burned
    raster == 1), on the shared grid both already sit on. A pixel flagged by
    either or both sources counts once -- this IS the double-counting fix,
    not the probabilistic formula."""
    import rasterio

    with rasterio.open(worldcover_raster_path) as wc_src:
        wc = wc_src.read(1)
        profile = wc_src.profile
        wc_nodata = wc_src.nodata
    with rasterio.open(stl_raster_path) as stl_src:
        stl = stl_src.read(1)

    if wc.shape != stl.shape:
        raise ValueError(f"worldcover shape {wc.shape} != stl shape {stl.shape} -- grids not aligned")

    wc_is_tree = np.isin(wc, tree_class_codes)
    stl_is_tree = stl == 1
    fused = (wc_is_tree | stl_is_tree).astype("uint8")
    if wc_nodata is not None:
        fused_valid = wc != wc_nodata
    else:
        fused_valid = np.ones_like(wc, dtype=bool)

    profile.update(driver="GTiff", dtype="uint8", count=1, nodata=255)
    out = np.where(fused_valid, fused, 255).astype("uint8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(out, 1)
    return out_path


def edge_boolean_fraction(
    edges: gpd.GeoDataFrame, raster_path: Path, buffer_m: float,
    target_value: int | tuple[int, ...] = 1,
) -> pd.DataFrame:
    """Generic sibling of thermal.canopy.edge_canopy_fraction: fraction of a
    raster's target_value pixel(s) within buffer_m of each edge. target_value
    can be a single value (e.g. 1 for the boolean STL/fused rasters) or a
    tuple of class codes (e.g. (10,) for WorldCover's raw class-coded
    raster) -- both go through the exact same masking logic, so worldcover,
    STL, and fused fractions are never biased by different code paths.

    Returns DataFrame[edge_id, fraction, valid_area_fraction].
    """
    import rasterio
    from rasterio.mask import mask as rio_mask

    if not raster_path.exists():
        raise FileNotFoundError(f"raster not found: {raster_path}")
    target_codes = target_value if isinstance(target_value, tuple) else (target_value,)

    buffered = edges.geometry.buffer(buffer_m)
    rows = []
    with rasterio.open(raster_path) as src:
        for edge_id, geom in zip(edges["edge_id"], buffered):
            try:
                data, _ = rio_mask(src, [geom], crop=True, nodata=src.nodata)
            except ValueError:
                rows.append((edge_id, np.nan, 0.0))
                continue
            band = data[0]
            valid = band != src.nodata if src.nodata is not None else np.ones_like(band, bool)
            total = int(valid.sum())
            if total == 0:
                rows.append((edge_id, np.nan, 0.0))
                continue
            target = int(np.isin(band[valid], target_codes).sum())
            rows.append((edge_id, target / total, 1.0))
    return pd.DataFrame(rows, columns=["edge_id", "fraction", "valid_area_fraction"])


def combine_canopy_sources(
    edges: gpd.GeoDataFrame,
    worldcover_raster_path: Path,
    stl_raster_path: Path,
    fused_raster_path: Path,
    buffer_m: float,
    tree_class_codes: tuple[int, ...] = (10,),
) -> pd.DataFrame:
    """Runs edge_boolean_fraction against all three (worldcover, stl, fused)
    rasters and assembles the v2 schema: worldcover_canopy_fraction,
    street_tree_fraction, fused_canopy_fraction, buffer_m, valid_area_fraction,
    source_flags ("worldcover_only" | "stl_only" | "both" | "neither", a
    presence/absence classification at a >0 threshold, not a magnitude
    comparison), qa_flag ("ok" | "no_valid_pixels").

    worldcover_raster_path carries WorldCover's own raw class codes (matched
    against tree_class_codes); stl_raster_path/fused_raster_path are already
    boolean 0/1 (rasterize_stl_to_grid/build_fused_tree_raster's output),
    matched against 1.
    """
    wc = edge_boolean_fraction(edges, worldcover_raster_path, buffer_m, target_value=tree_class_codes)
    stl = edge_boolean_fraction(edges, stl_raster_path, buffer_m, target_value=1)
    fused = edge_boolean_fraction(edges, fused_raster_path, buffer_m, target_value=1)

    out = wc.rename(columns={"fraction": "worldcover_canopy_fraction"})[
        ["edge_id", "worldcover_canopy_fraction", "valid_area_fraction"]
    ]
    out = out.merge(stl.rename(columns={"fraction": "street_tree_fraction"})[["edge_id", "street_tree_fraction"]], on="edge_id")
    out = out.merge(fused.rename(columns={"fraction": "fused_canopy_fraction"})[["edge_id", "fused_canopy_fraction"]], on="edge_id")
    out["buffer_m"] = buffer_m

    # Two additional, clearly-labelled comparison variants -- neither is the
    # primary estimate (fused_canopy_fraction, the pixel-level spatial union,
    # is), kept for explicit cross-checking:
    #   fused_max_fraction: conservative edge-level fallback if the spatial
    #     union were ever found invalid (max cannot exceed either source, so
    #     it structurally cannot overstate canopy from overlap).
    #   upper_bound_probabilistic_fraction: the independence-assumption
    #     formula 1-(1-W)(1-S) -- explicitly labelled "upper_bound" because it
    #     assumes W and S are independent events, which overstates canopy
    #     wherever they observe the SAME trees (the realistic case for two
    #     canopy products over the same area) -- never used as the primary
    #     estimate, see module docstring.
    out["fused_max_fraction"] = out[["worldcover_canopy_fraction", "street_tree_fraction"]].max(axis=1)
    out["upper_bound_probabilistic_fraction"] = 1 - (1 - out["worldcover_canopy_fraction"]) * (1 - out["street_tree_fraction"])

    has_wc = out["worldcover_canopy_fraction"].fillna(0) > 0
    has_stl = out["street_tree_fraction"].fillna(0) > 0
    out["source_flags"] = np.select(
        [has_wc & has_stl, has_wc & ~has_stl, ~has_wc & has_stl],
        ["both", "worldcover_only", "stl_only"],
        default="neither",
    )
    out["qa_flag"] = np.where(out["valid_area_fraction"] > 0, "ok", "no_valid_pixels")
    return out
