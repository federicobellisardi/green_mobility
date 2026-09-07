"""Tree canopy cover per street edge, from a real land-cover raster.

Default source: ESA WorldCover 10m v200 (2021), class code 10 = "Tree cover"
(https://esa-worldcover.org — open, CC-BY-4.0, cite Zanaga et al. 2022). Any
raster with a "tree cover" class code works; pass `tree_class_codes` to match
a different product (e.g. a local municipal canopy layer).

This module does not download the raster — that is a real, sizeable
(country-scale, multi-hundred-MB) external fetch the operator should do
deliberately (see README "Thermal data sources"), not something to trigger
silently from a pipeline step.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def edge_canopy_fraction(
    edges: gpd.GeoDataFrame,
    raster_path: Path,
    buffer_m: float = 15.0,
    tree_class_codes: tuple[int, ...] = (10,),
) -> pd.DataFrame:
    """Fraction of tree-cover pixels within `buffer_m` of each edge centerline.

    Parameters
    ----------
    edges : GeoDataFrame with an 'edge_id' column and LineString geometry in
        a projected (metric) CRS — reproject before calling if edges came from
        NOMAD's nodes/edges.parquet (EPSG:4326 lon/lat).
    raster_path : land-cover raster (e.g. ESA WorldCover .tif) covering the
        city's extent.

    Returns
    -------
    DataFrame[edge_id, canopy_fraction] — canopy_fraction in [0, 1].
    """
    import rasterio
    from rasterio.mask import mask as rio_mask

    if not raster_path.exists():
        raise FileNotFoundError(
            f"Canopy raster not found: {raster_path}. See README 'Thermal data "
            "sources' for how to obtain ESA WorldCover (or an equivalent "
            "tree-cover raster) for this city's extent."
        )

    buffered = edges.geometry.buffer(buffer_m)
    rows = []
    with rasterio.open(raster_path) as src:
        for edge_id, geom in zip(edges["edge_id"], buffered):
            try:
                data, _ = rio_mask(src, [geom], crop=True, nodata=src.nodata)
            except ValueError:
                # geometry does not intersect the raster extent at all
                rows.append((edge_id, np.nan))
                continue
            band = data[0]
            valid = band != src.nodata if src.nodata is not None else np.ones_like(band, bool)
            total = int(valid.sum())
            if total == 0:
                rows.append((edge_id, np.nan))
                continue
            tree = int(np.isin(band, tree_class_codes).sum())
            rows.append((edge_id, tree / total))

    return pd.DataFrame(rows, columns=["edge_id", "canopy_fraction"])
