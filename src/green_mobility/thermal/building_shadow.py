"""Geometric building-shadow fraction per street edge, from the Urban Atlas
Building Block Height (BBH) raster + already-computed solar position
(thermal.era5_utci.solar_elevation_deg/solar_azimuth_deg).

Deliberately limited scope (per project brief): this computes a purely
geometric building_shadow_fraction (obstructed line-of-sight to the sun,
sampled along each edge) -- NOT irradiance, NOT mean radiant temperature,
NOT a UTCI correction, NOT multiple reflections. It is NOT SOLWEIG/UMEP and
must not be described as such; it is a simple ray-cast against a height
raster, closer to a basic horizon-obstruction check.

Method: for a ground point at (x, y), a building blocks the sun if, marching
outward from (x, y) TOWARD the sun's azimuth (the direction FROM the point
TO the sun -- so a blocking building sits between the point and the sun),
some raster cell at horizontal distance d has

    building_height > d * tan(solar_elevation)

i.e. the building's angular height, as seen from the point, exceeds the
sun's elevation angle. The BBH raster is sparse by construction (~1-2% of
pixels are buildings, the ESA raster's own NoData covers the rest) -- NoData
is therefore treated as height=0 (open ground, no obstruction), not as
"unknown", which is the correct reading of this specific product (confirmed
by inspecting the raster: NoData is not a missing-data flag here, it is the
product's encoding of "not a building pixel").
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as date_cls

import numpy as np
import pandas as pd

from green_mobility.thermal.era5_utci import daylight_fraction_by_hour, solar_azimuth_deg, solar_elevation_deg

DEFAULT_SAMPLE_SPACING_M = 10.0
DEFAULT_MAX_RAY_DISTANCE_M = 250.0
DEFAULT_EDGE_SAMPLE_SPACING_M = 20.0


@dataclass(frozen=True)
class HeightGrid:
    """In-memory building-height raster (loaded once per city, reused
    across every edge/hour/date -- avoids re-opening the file per point,
    the dominant cost at this raster's scale)."""

    array: np.ndarray   # 2D, uint16-like, real height in metres where a building exists
    transform: object    # affine.Affine, raster CRS units (must be metric, e.g. EPSG:3035/UTM)
    nodata: float | int | None

    @classmethod
    def from_file(cls, path) -> "HeightGrid":
        import rasterio
        with rasterio.open(path) as src:
            return cls(array=src.read(1), transform=src.transform, nodata=src.nodata)

    def height_at(self, x: float, y: float) -> float:
        """Height in metres at (x, y); 0.0 if outside the raster extent or
        NoData (see module docstring for why NoData -> 0.0 here)."""
        col, row = ~self.transform * (x, y)
        row, col = int(row), int(col)
        if row < 0 or row >= self.array.shape[0] or col < 0 or col >= self.array.shape[1]:
            return 0.0
        val = self.array[row, col]
        if self.nodata is not None and val == self.nodata:
            return 0.0
        return float(val)


def is_point_shadowed(
    x: float, y: float, elevation_deg: float, azimuth_deg: float, grid: HeightGrid,
    sample_spacing_m: float = DEFAULT_SAMPLE_SPACING_M,
    max_ray_distance_m: float = DEFAULT_MAX_RAY_DISTANCE_M,
) -> bool:
    """True if some building along the ray from (x,y) toward the sun blocks
    it (building_height > horizontal_distance * tan(elevation)). Elevation
    must be > 0 (daytime); callers handle elevation <= 0 (night) themselves
    -- see edge_hour_building_shadow_fraction."""
    if elevation_deg <= 0:
        return False
    az_rad = math.radians(azimuth_deg)
    dx, dy = math.sin(az_rad), math.cos(az_rad)  # compass bearing -> (east, north) unit vector
    tan_elev = math.tan(math.radians(elevation_deg))
    n_steps = max(1, int(max_ray_distance_m / sample_spacing_m))
    for step in range(1, n_steps + 1):
        dist = step * sample_spacing_m
        h = grid.height_at(x + dx * dist, y + dy * dist)
        if h > dist * tan_elev:
            return True
    return False


def sample_points_along_edge(geom, spacing_m: float) -> list[tuple[float, float]]:
    """Evenly spaced (x, y) points along a LineString's length (at least the
    two endpoints), spacing_m apart -- geom must be in a metric CRS."""
    length = geom.length
    if length == 0:
        p = geom.coords[0]
        return [(p[0], p[1])]
    n_points = max(2, int(length / spacing_m) + 1)
    return [
        (pt.x, pt.y)
        for pt in (geom.interpolate(t, normalized=True) for t in np.linspace(0, 1, n_points))
    ]


def edge_hour_building_shadow_fraction(
    edges,  # GeoDataFrame[edge_id, geometry] in the SAME metric CRS as grid
    grid: HeightGrid,
    lat_deg: float, lon_deg: float, d: date_cls, hours: list[int],
    sample_spacing_m: float = DEFAULT_SAMPLE_SPACING_M,
    max_ray_distance_m: float = DEFAULT_MAX_RAY_DISTANCE_M,
    edge_sample_spacing_m: float = DEFAULT_EDGE_SAMPLE_SPACING_M,
) -> pd.DataFrame:
    """Returns DataFrame[edge_id, date, hour, solar_azimuth_deg,
    solar_elevation_deg, daylight, building_shadow_fraction, n_sample_points,
    valid_sample_fraction, qa_flag] -- one row per (edge, hour).

    building_shadow_fraction: fraction of this edge's sample points
    shadowed by a building at that hour. Always in [0, 1]. At night
    (elevation <= 0) building_shadow_fraction is 0.0 and daylight=False (no
    sun to block in the first place -- mirrors thermal.era5_utci's own
    daylight gating for tree shade, for consistency).
    """
    daylight_by_hour = daylight_fraction_by_hour(lat_deg, lon_deg, d).set_index("hour")["daylight_fraction"]

    edge_points: dict = {}
    for edge_id, geom in zip(edges["edge_id"], edges.geometry):
        edge_points[edge_id] = sample_points_along_edge(geom, edge_sample_spacing_m)

    rows = []
    for hour in hours:
        elevation = solar_elevation_deg(lat_deg, lon_deg, d, hour)
        azimuth = solar_azimuth_deg(lat_deg, lon_deg, d, hour)
        is_daylight = elevation > 0
        for edge_id, points in edge_points.items():
            n = len(points)
            if not is_daylight:
                rows.append((edge_id, d.isoformat(), hour, azimuth, elevation, False, 0.0, n, 1.0, "ok"))
                continue
            n_shadowed = sum(
                is_point_shadowed(x, y, elevation, azimuth, grid, sample_spacing_m, max_ray_distance_m)
                for x, y in points
            )
            fraction = n_shadowed / n if n > 0 else np.nan
            qa = "ok" if n > 0 else "no_sample_points"
            rows.append((edge_id, d.isoformat(), hour, azimuth, elevation, True, fraction, n, 1.0 if n > 0 else 0.0, qa))

    return pd.DataFrame(rows, columns=[
        "edge_id", "date", "hour", "solar_azimuth_deg", "solar_elevation_deg",
        "daylight", "building_shadow_fraction", "n_sample_points", "valid_sample_fraction", "qa_flag",
    ])
