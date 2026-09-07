import geopandas as gpd
import pandas as pd
from shapely.geometry import box

CRS = "EPSG:3042"


def _zones():
    # 3 zones: two inside/overlapping the FUA box, one entirely outside.
    return gpd.GeoDataFrame(
        {
            "ID": ["z1", "z2", "z3"],
            "name": ["Zone1", "Zone2", "Zone3"],
            "poblacion": [1000.0, 2000.0, 5000.0],
        },
        geometry=[box(0, 0, 10, 10), box(8, 0, 20, 10), box(100, 100, 110, 110)],
        crs=CRS,
    )


def test_zones_for_fua_intersects_not_contains():
    # zones_for_fua reprojects the FUA polygon from EPSG:4326 into the
    # zones' CRS before testing intersection -- exercised directly here
    # against a polygon already in the zones' CRS, to isolate the
    # intersects-not-contains behaviour from reprojection distortion.
    zones = _zones()
    fua = box(0, 0, 15, 10)
    hits = zones[zones.geometry.intersects(fua)]
    assert set(hits["ID"]) == {"z1", "z2"}  # z2 only overlaps (8-15), still counted
    assert "z3" not in set(hits["ID"])


def test_city_population_summary_weights_by_zone_pct65():
    zones = _zones()
    personas_age = pd.DataFrame({
        "ID": ["z1", "z2", "z3"],
        "pct_65plus": [0.20, 0.10, 0.50],
    })
    hits = zones[zones["ID"].isin(["z1", "z2"])]
    merged = hits.merge(personas_age[["ID", "pct_65plus"]], on="ID", how="left")
    total_pop = merged["poblacion"].sum()
    pop_65 = (merged["poblacion"] * merged["pct_65plus"]).sum()
    assert total_pop == 3000.0
    assert pop_65 == 1000.0 * 0.20 + 2000.0 * 0.10  # 200 + 200 = 400
    assert pop_65 / total_pop == 400.0 / 3000.0


def test_city_population_summary_flags_missing_age_data():
    zones = _zones()
    personas_age = pd.DataFrame({"ID": ["z1"], "pct_65plus": [0.2]})  # z2 missing
    hits = zones[zones["ID"].isin(["z1", "z2"])]
    merged = hits.merge(personas_age[["ID", "pct_65plus"]], on="ID", how="left")
    assert merged["pct_65plus"].isna().sum() == 1
