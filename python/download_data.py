#!/usr/bin/env python3
"""Download environmental inputs for the Spanish green-mobility study.

Automated sources:
  * ERA5-Land hourly fields through the Copernicus Climate Data Store API
  * ERA5-derived UTCI time series through the CDS API
  * AEMET daily observations through AEMET OpenData
  * ESA WorldCover 2021 tiles from the public COG archive

Portal-selected sources (Urban Atlas, CNIG/PNOA) can be downloaded
reproducibly with ``download-urls`` after their official URLs have been copied
to a text file (one URL per line). No credentials are stored in this script.
INE census-section boundaries, despite living behind a portal UI for manual
table queries, are ALSO available live and unauthenticated via INE's own OGC
API Features service (confirmed working, see ``ine-sections``) -- no manual
URL collection needed for that one.

Examples:
  python download_green_data.py init
  python download_green_data.py worldcover
  python download_green_data.py era5-land --dates 2022-02-08 2022-07-05 2022-07-19
  python download_green_data.py utci --dates 2022-02-08 2022-07-05 2022-07-19
  AEMET_API_KEY=... python download_green_data.py aemet-daily \
      --stations B278 9434 7178I 2422 --start 2022-02-08 --end 2022-07-19
  python download_green_data.py download-urls \
      --source urban_atlas --url-file urban_atlas_urls.txt

Install the only non-standard dependency needed for CDS downloads with:
  python -m pip install cdsapi

Configure CDS credentials as documented at https://cds.climate.copernicus.eu/
and request an AEMET key at https://opendata.aemet.es/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable


DEFAULT_DATES = ("2022-02-08", "2022-07-05", "2022-07-19")
# Real usage so far has always been --root data/climate_raw (see the audit
# that added this comment -- "data/raw" was never actually populated, only
# data/climate_raw was). Default now matches actual practice instead of
# silently disagreeing with every real invocation on record.
DEFAULT_ROOT = Path("data/climate_raw")


@dataclass(frozen=True)
class City:
    slug: str
    lat: float
    lon: float
    # Compact FUA-oriented bounding boxes: north, west, south, east.
    bbox: tuple[float, float, float, float]


CITIES = {
    "palma_de_mallorca": City("palma_de_mallorca", 39.5696, 2.6502, (39.95, 2.25, 39.25, 3.25)),
    "zaragoza": City("zaragoza", 41.6488, -0.8891, (42.05, -1.45, 41.25, -0.25)),
    "murcia": City("murcia", 37.9922, -1.1307, (38.35, -1.65, 37.65, -0.55)),
    "valladolid": City("valladolid", 41.6523, -4.7245, (42.00, -5.20, 41.30, -4.20)),
    # 8 cities added for the 12-city Nature Cities scale-up. lat/lon = FUA
    # centroid, bbox = FUA envelope + 0.15 deg padding -- both computed
    # directly from data/fua/boundaries.gpkg (JRC/OECD GHSL FUAs layer,
    # already covers all 12 cities, verified via `ogrinfo -sql` in the audit
    # that produced this change), not guessed.
    # lat/lon below are the true city-center landmark point (Puerta del
    # Sol / Placa de Catalunya / etc.), NOT the FUA polygon centroid --
    # confirmed those two differ meaningfully for irregularly-shaped FUAs
    # (Zaragoza's FUA centroid falls in a different MTN50 sheet than the
    # real city; Barcelona's FUA centroid picked a materially worse nearest
    # AEMET station, 16km off vs 1.45km). bbox stays FUA-envelope-based
    # (unaffected -- that's a real coverage requirement, not a point lookup).
    "madrid": City("madrid", 40.4168, -3.7038, (41.039, -4.729, 39.722, -3.044)),
    "barcelona": City("barcelona", 41.3874, 2.1686, (41.910, 1.407, 41.036, 2.821)),
    "valencia": City("valencia", 39.4699, -0.3763, (39.932, -1.217, 39.091, -0.109)),
    "sevilla": City("sevilla", 37.3891, -5.9845, (37.952, -6.481, 36.744, -5.248)),
    # NOTE: boundaries.gpkg has two FUAs matching "Cordoba" -- the accented
    # "Córdoba" (lon ~-5..-4, lat ~37.6..38.2, i.e. Spain) and an unaccented
    # "Cordoba" (lon ~-97, lat ~19, i.e. Córdoba, Veracruz, Mexico). This is
    # the Spanish one; the slug/name below must keep the accent so
    # network_prep.boundaries.match_city's exact-match branch picks it and
    # never falls through to the Mexican duplicate.
    "cordoba": City("cordoba", 37.8789, -4.7794, (38.309, -5.254, 37.502, -4.202)),
    "granada": City("granada", 37.1773, -3.5986, (37.551, -4.078, 36.808, -3.100)),
    "bilbao": City("bilbao", 43.2630, -2.9350, (43.607, -3.585, 42.871, -2.516)),
    "a_coruna": City("a_coruna", 43.3623, -8.4115, (43.555, -8.815, 42.969, -7.901)),
}


ERA5_LAND_VARIABLES = (
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_pressure",
    "surface_solar_radiation_downwards",
    "surface_thermal_radiation_downwards",
)


def log(message: str) -> None:
    print(message, flush=True)


def parse_dates(values: Iterable[str]) -> list[date]:
    parsed = sorted({date.fromisoformat(value) for value in values})
    if not parsed:
        raise ValueError("At least one ISO date is required")
    return parsed


def selected_cities(names: Iterable[str]) -> list[City]:
    result = []
    for name in names:
        try:
            result.append(CITIES[name])
        except KeyError as exc:
            raise ValueError(f"Unknown city: {name}") from exc
    return result


def ensure_layout(root: Path) -> None:
    for source in ("urban_atlas", "era5", "aemet", "worldcover", "cnig", "ine"):
        (root / source).mkdir(parents=True, exist_ok=True)


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path: Path, payload: dict) -> None:
    manifest = path.with_name(path.name + ".manifest.json")
    payload = {**payload, "file": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _looks_like_masked_zip(path: Path) -> bool:
    import zipfile

    return path.suffix.lower() != ".zip" and zipfile.is_zipfile(path)


def _unwrap_single_member_zip(path: Path) -> None:
    """Some portals (confirmed for CDS's derived-utci-historical-timeseries,
    see the download_utci()-specific _unwrap_if_zipped) silently deliver a
    ZIP even when the requested/saved filename has a different extension.
    If that happens and the archive has exactly one member, replace the file
    with that member's bytes so every caller always gets the format its
    filename promises. A multi-member zip is left untouched with a warning
    instead of guessing which member is wanted (dataset-specific — see the
    ".nc"-suffix selection logic in _unwrap_if_zipped for UTCI specifically).
    """
    import zipfile

    if not _looks_like_masked_zip(path):
        return
    with zipfile.ZipFile(path) as zf:
        members = zf.namelist()
        if len(members) != 1:
            log(
                f"WARN {path} is a ZIP masquerading as '{path.suffix or '(no extension)'}' "
                f"with {len(members)} members {members} -- not auto-unwrapped, inspect manually"
            )
            return
        with zf.open(members[0]) as inner:
            data = inner.read()
    path.write_bytes(data)
    log(f"      unwrapped masked ZIP: {path.name} had a single member, extracted in place")


def download(
    url: str,
    destination: Path,
    *,
    headers: dict[str, str] | None = None,
    retries: int = 5,
) -> Path:
    """Idempotent, resumable, retried download with generic masked-ZIP
    detection. Skips outright if `destination` already exists (re-run the
    same command to fill in only what's missing); resumes a `.part` file via
    HTTP Range when the server honours it (falls back to a clean restart
    otherwise, mirroring data/climate_raw/cnig/python/download.py's proven
    download_one() logic, generalized here so every source in this script
    gets it, not just CNIG)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size:
        log(f"SKIP {destination} (already exists)")
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    base_headers = {"User-Agent": "green-mobility-data/1.0", **(headers or {})}

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        request_headers = dict(base_headers)
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=request_headers)
        log(f"GET  {url}" + (f" (resume at {offset} bytes)" if offset else ""))
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                resumed = bool(offset) and response.status == 206
                if offset and not resumed:
                    offset = 0  # server ignored Range -- restart this file cleanly
                with partial.open("ab" if resumed else "wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            partial.replace(destination)
            _unwrap_single_member_zip(destination)
            return destination
        except Exception as exc:  # noqa: BLE001 -- retried below, re-raised after exhausting attempts
            last_exc = exc
            if attempt == retries:
                break
            log(f"      retry {attempt}/{retries} after error: {exc}")
            time.sleep(min(2**attempt, 30))
    partial.unlink(missing_ok=True)
    assert last_exc is not None
    raise last_exc


def combined_area(cities: list[City], padding: float = 0.1) -> list[float]:
    north = max(c.bbox[0] for c in cities) + padding
    west = min(c.bbox[1] for c in cities) - padding
    south = min(c.bbox[2] for c in cities) - padding
    east = max(c.bbox[3] for c in cities) + padding
    return [round(north, 3), round(west, 3), round(south, 3), round(east, 3)]


def require_cdsapi():
    try:
        import cdsapi  # type: ignore
    except ImportError as exc:
        raise SystemExit("Install cdsapi first: python -m pip install cdsapi") from exc
    return cdsapi


def _unwrap_if_zipped(path: Path) -> None:
    """The derived-utci-historical-timeseries CDS dataset ignores
    download_format=unarchived and always delivers a ZIP even when saved
    with a .nc extension (confirmed by inspection: `file` reports "Zip
    archive data" despite the .nc name) — unlike reanalysis-era5-land, which
    honours the flag and returns a real HDF5/netCDF file directly. Detect
    and transparently unwrap so callers always get a real netCDF at `path`,
    regardless of which behaviour this specific CDS dataset has today.
    """
    import zipfile

    if not zipfile.is_zipfile(path):
        return
    with zipfile.ZipFile(path) as zf:
        members = [n for n in zf.namelist() if n.endswith(".nc")]
        if not members:
            raise RuntimeError(f"{path} is a zip but contains no .nc member: {zf.namelist()}")
        with zf.open(members[0]) as inner:
            data = inner.read()
    path.write_bytes(data)


def _city_set_tag(cities: list[City]) -> str:
    """Short, deterministic identifier for a set of cities, used to keep
    per-date-only filenames (below) from silently colliding across two
    different --cities selections. Bug found while extending to 12 cities:
    the file name used to be era5_land_hourly_{date}.nc with no reference to
    which cities' combined bbox it actually covers -- requesting the same
    date for a different --cities subset would hit the "already exists" skip
    and keep the OLD (smaller) bbox file, silently NOT covering the new
    cities. Tagging the filename by the exact city set makes two different
    selections always produce two different files."""
    slugs = sorted(c.slug for c in cities)
    joined = ",".join(slugs)
    if len(joined) <= 40:
        return "-".join(slugs)
    return hashlib.sha256(joined.encode()).hexdigest()[:10]


def download_era5_land(args: argparse.Namespace) -> None:
    """One CDS request per exact date (mirrors download_utci's per-city loop).

    Bug fixed here: year/month/day used to each be passed as the SET of
    values across all requested dates (e.g. year={2022}, month={02,07},
    day={05,08,19} for dates 2022-02-08/2022-07-05/2022-07-19). CDS treats
    those three lists as independent axes and returns their CARTESIAN
    PRODUCT, not just the 3 intended dates — silently fetching 6 dates
    (including 2022-02-05, 2022-02-19, 2022-07-08, never requested) instead
    of 3. Looping per exact date, one CDS request each, makes this
    impossible by construction.
    """
    cdsapi = require_cdsapi() if not getattr(args, "dry_run", False) else None
    dates = parse_dates(args.dates)
    cities = selected_cities(args.cities)
    area = combined_area(cities)
    tag = _city_set_tag(cities)
    for d in dates:
        destination = args.root / "era5" / f"era5_land_hourly_{d.isoformat()}_{tag}.nc"
        if destination.exists() and not args.force:
            log(f"SKIP {destination} (already exists; use --force to replace)")
            continue
        if getattr(args, "dry_run", False):
            log(f"[dry-run] CDS reanalysis-era5-land -> {destination} (area={area})")
            continue
        destination.unlink(missing_ok=True)
        request = {
            "variable": list(ERA5_LAND_VARIABLES),
            "year": [f"{d.year:04d}"],
            "month": [f"{d.month:02d}"],
            "day": [f"{d.day:02d}"],
            "time": [f"{hour:02d}:00" for hour in range(24)],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": area,
        }
        log(f"CDS  reanalysis-era5-land -> {destination}")
        cdsapi.Client().retrieve("reanalysis-era5-land", request, str(destination))
        write_manifest(destination, {
            "source": "Copernicus CDS", "dataset": "reanalysis-era5-land",
            "request": request, "cities": sorted(c.slug for c in cities),
        })


def download_utci_year(args: argparse.Namespace) -> None:
    """Request the WHOLE YEAR of UTCI for each city in a single CDS request.

    Real finding while building the 12-city, full-2022 date-selection
    pipeline: downloading one city-day at a time (the existing
    download_utci() below) would mean 12 cities x 365 days = 4,380
    individual CDS requests -- at the ~15-30s/request turnaround observed
    empirically, that is on the order of a full day of serial wall-clock
    time for a dataset that ends up totalling well under 100 MB. Verified
    directly against the live API that "date" accepts a genuine range
    string ("YYYY-01-01/YYYY-12-31"), not just a single day repeated twice
    -- one test request for Murcia returned a real 8,760-timestep (365 x 24)
    file in ~30s. This cuts the whole-year download to 12 requests total.
    """
    cdsapi = require_cdsapi() if not getattr(args, "dry_run", False) else None
    year = int(args.year)
    date_range = f"{year}-01-01/{year}-12-31"
    for city in selected_cities(args.cities):
        destination = args.root / "era5" / f"utci_{city.slug}_{year}.nc"
        if destination.exists() and not args.force:
            log(f"SKIP {destination} (already exists; use --force to replace)")
            continue
        if getattr(args, "dry_run", False):
            log(f"[dry-run] CDS derived-utci-historical-timeseries -> {destination} "
                f"(area={city.bbox}, date={date_range}, ~8760 hourly values)")
            continue
        destination.unlink(missing_ok=True)
        request = {
            "variable": ["universal_thermal_climate_index"],
            "date": [date_range],
            "time": [f"{hour:02d}:00" for hour in range(24)],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": list(city.bbox),
        }
        log(f"CDS  derived-utci-historical-timeseries (full year {year}) -> {destination}")
        try:
            cdsapi.Client().retrieve("derived-utci-historical-timeseries", request, str(destination))
            _unwrap_if_zipped(destination)
        except Exception as exc:
            raise SystemExit(
                "The CDS UTCI catalogue rejected the full-year request. Open its Download "
                "tab, accept the licence, and compare the generated API request with this "
                f"script. Original error: {exc}"
            ) from exc
        write_manifest(destination, {
            "source": "Copernicus CDS", "dataset": "derived-utci-historical-timeseries",
            "request": request, "year": year,
        })


def download_utci(args: argparse.Namespace) -> None:
    """Request UTCI for each city and date separately.

    Bug fixed here: derived-utci-historical-timeseries does NOT use the
    year/month/day list schema reanalysis-era5-land does. CDS rejected that
    request outright with an explicit, unambiguous error identifying both
    the wrong parameter and the expected format:
        "Please specify a single date range using the format
         yyyy-mm-dd/yyyy-mm-dd or [yyyy-mm-dd, yyyy-mm-dd]."
    Fixed by requesting a single-day range (date=["D","D"]) per exact date,
    same one-request-per-date principle as the ERA5-Land fix — CDS occasionally
    changes option labels for derived products, so keeping requests isolated
    per city/date makes any future catalogue change explicit instead of
    silently fetching the wrong window.

    For fetching a WHOLE YEAR across all cities, use download_utci_year (the
    "utci-year" subcommand) instead -- one combined-range request per city
    rather than 365 individual ones (see its docstring for the measurement
    behind that choice). This function stays as-is for targeted single-day
    fetches (e.g. adding one more reference day later).
    """
    cdsapi = require_cdsapi() if not getattr(args, "dry_run", False) else None
    dates = parse_dates(args.dates)
    for city in selected_cities(args.cities):
        for d in dates:
            destination = args.root / "era5" / f"utci_{city.slug}_{d.isoformat()}.nc"
            if destination.exists() and not args.force:
                log(f"SKIP {destination} (already exists; use --force to replace)")
                continue
            if getattr(args, "dry_run", False):
                log(f"[dry-run] CDS derived-utci-historical-timeseries -> {destination} (area={city.bbox})")
                continue
            destination.unlink(missing_ok=True)
            request = {
                "variable": ["universal_thermal_climate_index"],
                "date": [d.isoformat(), d.isoformat()],
                "time": [f"{hour:02d}:00" for hour in range(24)],
                "data_format": "netcdf",
                "download_format": "unarchived",
                "area": list(city.bbox),
            }
            log(f"CDS  derived-utci-historical-timeseries -> {destination}")
            try:
                cdsapi.Client().retrieve("derived-utci-historical-timeseries", request, str(destination))
                _unwrap_if_zipped(destination)
            except Exception as exc:
                raise SystemExit(
                    "The CDS UTCI catalogue rejected the request. Open its Download tab, "
                    "accept the licence, and compare the generated API request with this script. "
                    f"Original error: {exc}"
                ) from exc
            write_manifest(destination, {"source": "Copernicus CDS", "dataset": "derived-utci-historical-timeseries", "request": request})


def worldcover_tile(lat: float, lon: float) -> str:
    south = math.floor(lat / 3.0) * 3
    west = math.floor(lon / 3.0) * 3
    ns = f"N{south:02d}" if south >= 0 else f"S{abs(south):02d}"
    ew = f"E{west:03d}" if west >= 0 else f"W{abs(west):03d}"
    return f"{ns}{ew}"


def download_worldcover(args: argparse.Namespace) -> None:
    tiles: dict[str, list[str]] = {}
    for city in selected_cities(args.cities):
        # Include every 3-degree tile touched by the city bbox.
        north, west, south, east = city.bbox
        lat_values = range(math.floor(south / 3) * 3, math.floor((north - 1e-9) / 3) * 3 + 1, 3)
        lon_values = range(math.floor(west / 3) * 3, math.floor((east - 1e-9) / 3) * 3 + 1, 3)
        for lat in lat_values:
            for lon in lon_values:
                tile = worldcover_tile(lat + 0.1, lon + 0.1)
                tiles.setdefault(tile, []).append(city.slug)
    for tile, city_names in sorted(tiles.items()):
        filename = f"ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
        url = f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{filename}"
        path = args.root / "worldcover" / filename
        if getattr(args, "dry_run", False):
            status = "already exists" if path.exists() and path.stat().st_size else "would download"
            log(f"[dry-run] {status}: {path} <- {url} (cities: {sorted(set(city_names))})")
            continue
        try:
            download(url, path)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"WorldCover tile not found ({tile}): {exc}") from exc
        write_manifest(path, {"source": "ESA WorldCover 2021 v200", "url": url, "cities": sorted(set(city_names))})


MITMA_ROOT = "https://movilidad-opendata.mitma.es/"
MITMA_ZONE_FILES = [
    "zonificacion/zonificacion_distritos/zonificacion_distritos.shp",
    "zonificacion/zonificacion_distritos/zonificacion_distritos.shx",
    "zonificacion/zonificacion_distritos/zonificacion_distritos.dbf",
    "zonificacion/zonificacion_distritos/zonificacion_distritos.prj",
    "zonificacion/zonificacion_distritos/zonificacion_distritos.cpg",
    "zonificacion/zonificacion_distritos/nombres_distritos.csv",
    "zonificacion/zonificacion_distritos/poblacion_distritos.csv",
]


def download_mitma_population(args: argparse.Namespace) -> None:
    """Official MITMA (Ministerio de Transportes) resident-population-per-
    zone reference, at the SAME "distrito" zonification already used for
    this project's OD matrices (external/nomad/python/preprocessing/
    build_od.py) -- not a separate operation, the same portal
    (movilidad-opendata.mitma.es) already cited for mobility demand.

    Fetches:
      - zonificacion_distritos.{shp,shx,dbf,prj,cpg} (zone geometries,
        EPSG:3042) + nombres_distritos.csv (zone names) + poblacion_distritos.csv
        (reference-year population per zone) -- used to spatially assign
        zones to each city's FUA (mirrors build_od.py's own FUA-intersection
        method) and sum population per city.
      - ONE day's "personas" file (age/sex-stratified estimated resident
        counts per zone; --date, default 2022-02-08 -- the same reference
        date most cities' weekday_full scenario already uses) for the
        age >=65 share needed by the vulnerability index. This is a modelled
        daily estimate (mobile-network-derived), not a census headcount --
        documented as such, never presented as an INE census figure.
    """
    out_dir = args.root / "mitma"
    for rel in MITMA_ZONE_FILES:
        filename = Path(rel).name
        path = out_dir / "zonificacion_distritos" / filename
        if getattr(args, "dry_run", False):
            log(f"[dry-run] would GET {MITMA_ROOT}{rel} -> {path}")
            continue
        url = MITMA_ROOT + rel
        download(url, path)
        write_manifest(path, {"source": "MITMA zonificacion_distritos", "url": url})

    date_str = args.date.replace("-", "")
    year_month = args.date[:7]
    personas_rel = (
        f"estudios_basicos/por-distritos/personas/ficheros-diarios/"
        f"{year_month}/{date_str}_Personas_dia_distritos.csv.gz"
    )
    personas_path = out_dir / "personas" / f"{date_str}_Personas_dia_distritos.csv.gz"
    if getattr(args, "dry_run", False):
        log(f"[dry-run] would GET {MITMA_ROOT}{personas_rel} -> {personas_path}")
        return
    url = MITMA_ROOT + personas_rel
    download(url, personas_path)
    write_manifest(personas_path, {"source": "MITMA personas (daily, age/sex-stratified)", "url": url, "date": args.date})


def _read_json_lenient(response) -> object:
    """AEMET's actual data payload (the URL returned in the "datos" field,
    hosted on opendata's CDN, as opposed to the small metadata response from
    the initial api call) is served as ISO-8859-1/Latin-1 despite the
    request asking for JSON -- confirmed by inspection: decoding a real
    response as UTF-8 fails on station names with accented characters (e.g.
    "VALÈNCIA": byte 0xC8 for 'È' is valid Latin-1 but not a valid UTF-8
    continuation byte). Try UTF-8 first (the metadata response and most
    stations' plain-ASCII names decode fine either way) and only fall back
    to Latin-1 on a real decode failure, rather than assuming one encoding
    and silently mangling -- or crashing on -- the other."""
    raw = response.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return json.loads(text)


def aemet_json(url: str, api_key: str) -> object:
    separator = "&" if "?" in url else "?"
    metadata_url = f"{url}{separator}{urllib.parse.urlencode({'api_key': api_key})}"
    with urllib.request.urlopen(metadata_url, timeout=60) as response:
        metadata = _read_json_lenient(response)
    if not isinstance(metadata, dict) or "datos" not in metadata:
        raise RuntimeError(f"Unexpected AEMET response: {metadata}")
    with urllib.request.urlopen(metadata["datos"], timeout=60) as response:
        return _read_json_lenient(response)


def download_aemet_daily(args: argparse.Namespace) -> None:
    api_key = os.environ.get("AEMET_API_KEY")
    if not api_key:
        raise SystemExit("Set AEMET_API_KEY in the environment (do not put it in the script).")
    start = datetime.fromisoformat(args.start).strftime("%Y-%m-%dT00:00:00UTC")
    end = datetime.fromisoformat(args.end).strftime("%Y-%m-%dT23:59:59UTC")
    for station in args.stations:
        encoded_start = urllib.parse.quote(start, safe="")
        encoded_end = urllib.parse.quote(end, safe="")
        url = (
            "https://opendata.aemet.es/opendata/api/valores/climatologicos/diarios/datos/"
            f"fechaini/{encoded_start}/fechafin/{encoded_end}/estacion/{urllib.parse.quote(station)}/"
        )
        destination = args.root / "aemet" / f"daily_{station}_{args.start}_{args.end}.json"
        if destination.exists() and not args.force:
            log(f"SKIP {destination} (already exists; use --force to replace)")
            continue
        data = aemet_json(url, api_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_manifest(destination, {"source": "AEMET OpenData", "station": station, "start": args.start, "end": args.end})
        time.sleep(0.25)


INE_SECTIONS_API = (
    "https://www.ine.es/geoserver/ogc/features/v1/collections/"
    "WMS_INE_SECCIONES_G01:SU.VectorStatisticalUnit/items"
)

# City name as it appears in INE's own NMUN attribute (confirmed live against
# the API below, not guessed) -> our internal city slug.
INE_CITY_NAMES = {
    "palma_de_mallorca": "Palma",
    "zaragoza": "Zaragoza",
    "murcia": "Murcia",
    "valladolid": "Valladolid",
    "madrid": "Madrid",
    "barcelona": "Barcelona",
    # INE stores Valencia under its co-official Valencian name -- confirmed
    # live: NMUN='Valencia' returns 0 features, NMUN='València' returns 1.
    "valencia": "València",
    "sevilla": "Sevilla",
    "cordoba": "Córdoba",
    "granada": "Granada",
    "bilbao": "Bilbao",
    # Same pattern as Valencia: INE writes municipalities with an article
    # in "name, article" order. Confirmed live: NMUN='A Coruña' returns 0
    # features, NMUN='Coruña, A' returns 1.
    "a_coruna": "Coruña, A",
}


def download_ine_census_sections(args: argparse.Namespace) -> None:
    """Census-section boundaries for the vulnerability index, fetched live
    from INE's own OGC API Features service -- this is NOT a portal-gated
    manual-selection source like Urban Atlas/CNIG (see module docstring);
    it is a real, queryable, unauthenticated public API. One GeoJSON file
    per city, filtered server-side by municipality name (CQL filter), so
    only the ~250-900 sections for that one city are ever transferred, not
    all ~36,000 sections in Spain.
    """
    import json
    import urllib.parse

    page_size = 2000
    for slug in args.cities:
        ine_name = INE_CITY_NAMES[slug]
        destination = args.root / "ine" / f"census_sections_{slug}.geojson"
        if destination.exists() and not args.force:
            log(f"SKIP {destination} (already exists; use --force to replace)")
            continue
        cql = f"NMUN='{ine_name}'"
        base_url = f"{INE_SECTIONS_API}?f=application/json&limit={page_size}&filter={urllib.parse.quote(cql)}"
        if getattr(args, "dry_run", False):
            log(f"[dry-run] would GET {base_url} (paginated) -> {destination}")
            continue

        # Bug found & fixed while extending to 12 cities: a flat single
        # request with limit=2000 silently TRUNCATED Madrid (2483 real
        # sections, per the API's own "totalFeatures" field -- only 2000
        # came back, 483 missing, no error). Follow OGC API Features
        # pagination (startIndex) until numberMatched == numberReturned so
        # this can never again silently under-cover a large municipality.
        features: list = []
        start_index = 0
        total_matched: int | None = None
        while True:
            page_url = base_url if start_index == 0 else f"{base_url}&startIndex={start_index}"
            log(f"GET  {page_url}")
            with urllib.request.urlopen(page_url, timeout=120) as response:
                page = json.load(response)
            page_features = page.get("features", [])
            features.extend(page_features)
            total_matched = page.get("numberMatched", len(features))
            if not page_features or len(features) >= total_matched:
                break
            start_index += len(page_features)

        if not features:
            raise SystemExit(f"No census sections returned for NMUN='{ine_name}' -- check the name")
        if total_matched is not None and len(features) != total_matched:
            raise SystemExit(
                f"NMUN='{ine_name}': fetched {len(features)} sections but the API reports "
                f"numberMatched={total_matched} -- pagination did not converge, aborting "
                "rather than saving an incomplete file."
            )
        data = {"type": "FeatureCollection", "features": features}
        n = len(features)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        write_manifest(destination, {
            "source": "INE OGC API Features (WMS_INE_SECCIONES_G01:SU.VectorStatisticalUnit)",
            "url": base_url, "city": slug, "n_sections": n,
        })
        log(f"  {n} sections -> {destination}")


def download_urls(args: argparse.Namespace) -> None:
    if args.source not in {"urban_atlas", "cnig"}:
        raise SystemExit("--source must be urban_atlas or cnig")
    lines = args.url_file.read_text(encoding="utf-8").splitlines()
    urls = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not urls:
        raise SystemExit(f"No URLs found in {args.url_file}")
    output_dir = args.root / args.source
    for index, url in enumerate(urls, start=1):
        parsed = urllib.parse.urlparse(url)
        filename = Path(urllib.parse.unquote(parsed.path)).name or f"download_{index:03d}"
        destination = output_dir / filename
        path = download(url, destination)
        write_manifest(path, {"source": args.source, "url": url})


UA_CATALOGUE_CSV = {
    "lclu": (
        "https://s3.waw3-1.cloudferro.com/swift/v1/CatalogueCSV/"
        "land_cover_use_in_priority_areas/urban_atlas/"
        "clms_ua_land-cover-land-use_europe_V025ha_3yearly_v1/"
        "clms_ua_land-cover-land-use_europe_V025ha_3yearly_v1_flatgeobuf.csv"
    ),
    "stl": (
        "https://s3.waw3-1.cloudferro.com/swift/v1/CatalogueCSV/"
        "land_cover_use_in_priority_areas/urban_atlas/"
        "clms_ua_street-tree-layer_europe_V005ha_3yearly_v1/"
        "clms_ua_street-tree-layer_europe_V005ha_3yearly_v1_flatgeobuf.csv"
    ),
    "bbh": (
        "https://s3.waw3-1.cloudferro.com/swift/v1/CatalogueCSV/"
        "land_cover_use_in_priority_areas/urban_atlas/"
        "clms_ua_building-height_europe_10m_3yearly_v1/"
        "clms_ua_building-height_europe_10m_3yearly_v1_cog.csv"
    ),
}

# City slug -> Urban Atlas FUA product code, confirmed live against each
# catalogue CSV above (grep on city name), not guessed.
UA_FUA_CODE = {
    "palma_de_mallorca": "ES010L2_PALMA_DE_MALLORCA",
    "zaragoza": "ES005L2_ZARAGOZA",
    "murcia": "ES007L2_MURCIA",
    "valladolid": "ES009L2_VALLADOLID",
    "madrid": "ES001L3_MADRID",
    "barcelona": "ES002L2_BARCELONA",
    "valencia": "ES003L3_VALENCIA",
    "sevilla": "ES004L3_SEVILLA",
    "cordoba": "ES020L2_CORDOBA",
    "granada": "ES501L3_GRANADA",
    "bilbao": "ES019L3_BILBAO",
    "a_coruna": "ES026L2_CORUNA_A",
}

CDSE_S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
# The catalogue's own s3_path field writes "s3://EODATA/..." (uppercase),
# but the actual bucket, confirmed via a live list_buckets() call, is
# lowercase "eodata" -- using the uppercase name gets a real NoSuchBucket
# error from CDSE, not a permissions issue.
CDSE_S3_BUCKET = "eodata"


def require_boto3():
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise SystemExit("Install boto3 first: python -m pip install boto3") from exc
    return boto3


def _cdse_s3_client(boto3_mod):
    access_key = os.environ.get("CDSE_ACCESS_KEY")
    secret_key = os.environ.get("CDSE_SECRET_KEY")
    if not access_key or not secret_key:
        raise SystemExit(
            "Set CDSE_ACCESS_KEY and CDSE_SECRET_KEY in the environment (do not put "
            "them in the script). Generate them for free at "
            "https://dataspace.copernicus.eu/ -> account settings -> S3 credentials."
        )
    return boto3_mod.client(
        "s3", endpoint_url=CDSE_S3_ENDPOINT,
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )


def _fetch_ua_catalogue(product: str) -> list[dict]:
    url = UA_CATALOGUE_CSV[product]
    with urllib.request.urlopen(url, timeout=120) as response:
        text = response.read().decode("utf-8")
    lines = text.splitlines()
    header = lines[0].split(";")
    return [dict(zip(header, line.split(";"))) for line in lines[1:] if line.strip()]


def download_urban_atlas(args: argparse.Namespace) -> None:
    """Urban Atlas (Land Cover/Land Use, Street Tree Layer, Building Block
    Height) via the Copernicus Data Space Ecosystem (CDSE) EODATA S3 bucket.

    Unlike the manual "download-urls" path this repo used before, CDSE
    publishes a small, public, unauthenticated CSV catalogue per product
    (UA_CATALOGUE_CSV) listing every FUA's exact current filename/s3_path --
    fetched fresh on every run instead of hardcoding filenames, since the
    catalogue's own version/date suffixes (e.g. "_V01_R00_20241115") get
    reprocessed over time. Only the actual product bytes need credentials
    (CDSE_ACCESS_KEY/CDSE_SECRET_KEY -- free registration, see
    _cdse_s3_client), matching the AEMET_API_KEY / cdsapi pattern already
    used elsewhere in this script.

    Real finding from building this: Building Block Height, previously
    documented as "unavailable" in this project (the one manually-downloaded
    national file only covered the Canary Islands), IS available per-FUA
    here for all 12 cities -- that file was simply the wrong/mis-scoped
    product, not a genuine coverage gap.
    """
    if args.product not in UA_CATALOGUE_CSV:
        raise SystemExit(f"--product must be one of {sorted(UA_CATALOGUE_CSV)}")
    catalogue = _fetch_ua_catalogue(args.product)

    boto3 = None if getattr(args, "dry_run", False) else require_boto3()
    client = None if getattr(args, "dry_run", False) else _cdse_s3_client(boto3)

    for slug in args.cities:
        fua_code = UA_FUA_CODE[slug]
        candidates = [row for row in catalogue if fua_code in row["name"]]
        if args.year:
            candidates = [row for row in candidates if f"_S{args.year}_" in row["name"]]
        if not candidates:
            raise SystemExit(
                f"No {args.product} entry found for {slug} ({fua_code})"
                + (f" year {args.year}" if args.year else "")
                + " in the live CDSE catalogue -- check UA_CATALOGUE_CSV/UA_FUA_CODE."
            )
        if len(candidates) > 1:
            years = sorted({row["name"].split("_S")[1][:4] for row in candidates})
            raise SystemExit(
                f"{slug}/{args.product}: {len(candidates)} candidates found (years {years}) "
                "-- pass --year to disambiguate, rather than silently picking one."
            )
        row = candidates[0]
        s3_path = row["s3_path"]
        assert s3_path.startswith("s3://EODATA/")
        # BUG FOUND while wiring this up: the catalogue's s3_path is NOT a
        # single-file key -- it's a PRODUCT FOLDER (confirmed via a live
        # list_objects_v2 call: the "object" at that exact key has size 0,
        # and the real payload lives at "<key>/<filename>.<ext>" alongside a
        # ".xml" metadata sidecar and a Legend/ styling subfolder). Fetching
        # the key directly gives a 404 HeadObject error. List the folder and
        # pick out the main data file (matching the catalogue's own
        # `filename`) instead of GETting the folder key.
        prefix = s3_path[len("s3://EODATA/"):] + "/"
        filename = row["name"]
        extension = ".tif" if args.product == "bbh" else ".fgb"
        main_key = f"{prefix}{filename}{extension}"
        xml_key = f"{prefix}{filename}.xml"
        destination = args.root / "urban_atlas" / args.product / f"{filename}{extension}"
        if destination.exists() and not args.force:
            log(f"SKIP {destination} (already exists; use --force to replace)")
            continue
        if getattr(args, "dry_run", False):
            log(f"[dry-run] would GET s3://{CDSE_S3_BUCKET}/{main_key} (+ .xml sidecar) "
                f"-> {destination} (folder total {int(row['content_length']):,} bytes)")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        log(f"S3   {main_key} -> {destination}")
        client.download_file(CDSE_S3_BUCKET, main_key, str(destination))
        # NOTE: the catalogue's checksum_value/content_length are for the
        # WHOLE product folder (main file + .xml + Legend/ styling files),
        # confirmed empirically -- the real md5 of the main file alone never
        # matches it. Not used as a pass/fail gate (would reject every
        # correct download); real integrity check is our own sha256 in the
        # manifest (write_manifest below), same as every other source here.
        xml_destination = destination.with_suffix(".xml")
        try:
            client.download_file(CDSE_S3_BUCKET, xml_key, str(xml_destination))
        except Exception as exc:
            log(f"WARN could not fetch metadata sidecar {xml_key}: {exc}")
        write_manifest(destination, {
            "source": "Copernicus Data Space Ecosystem (CDSE) EODATA",
            "product": args.product, "city": slug, "s3_key": main_key,
            "folder_s3_path": s3_path, "folder_content_length": row.get("content_length"),
        })


def run_all(args: argparse.Namespace) -> None:
    """Orchestrates the fully-automated datasets (worldcover, era5-land, utci,
    ine-sections) behind one --cities/--datasets/--dates-or-range/--dry-run
    surface, without duplicating any download logic -- it just builds a
    shared `dates` list and calls the same functions the dedicated
    subcommands call. aemet-daily (needs a manually chosen station list) and
    download-urls (needs a manually collected Urban Atlas/CNIG URL list) are
    deliberately excluded: neither can be driven by --cities alone yet."""
    if args.years and not (args.dates or (args.start_date and args.end_date)):
        raise SystemExit(
            "--years alone does not select which day(s) of each year to fetch. "
            "Pass --dates or --start-date/--end-date explicitly -- automatic "
            "ordinary/hot-day/heatwave sampling is a pending methodological "
            "decision (see the project plan), not something this script guesses."
        )
    if args.start_date and args.end_date:
        start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
        if end < start:
            raise SystemExit(f"--end-date {end} is before --start-date {start}")
        dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    elif args.dates:
        dates = list(args.dates)
    else:
        dates = list(DEFAULT_DATES)

    sub_args = argparse.Namespace(**vars(args))
    sub_args.dates = dates

    for name, fn in (
        ("worldcover", download_worldcover),
        ("era5-land", download_era5_land),
        ("utci", download_utci),
        ("ine-sections", download_ine_census_sections),
    ):
        if name in args.datasets:
            log(f"=== {name} ===")
            fn(sub_args)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"Raw-data root (default: {DEFAULT_ROOT})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the raw-data directory layout")
    add_common(init)
    init.set_defaults(func=lambda args: ensure_layout(args.root))

    wc = sub.add_parser("worldcover", help="Download ESA WorldCover tiles intersecting the selected cities")
    add_common(wc)
    wc.add_argument("--cities", nargs="+", choices=sorted(CITIES), default=sorted(CITIES))
    wc.add_argument("--dry-run", action="store_true")
    wc.set_defaults(func=download_worldcover)

    era = sub.add_parser("era5-land", help="Download selected hourly ERA5-Land variables")
    add_common(era)
    era.add_argument("--cities", nargs="+", choices=sorted(CITIES), default=sorted(CITIES))
    era.add_argument("--dates", nargs="+", default=list(DEFAULT_DATES))
    era.add_argument("--force", action="store_true")
    era.add_argument("--dry-run", action="store_true")
    era.set_defaults(func=download_era5_land)

    utci = sub.add_parser("utci", help="Download ERA5-derived UTCI for each city")
    add_common(utci)
    utci.add_argument("--cities", nargs="+", choices=sorted(CITIES), default=sorted(CITIES))
    utci.add_argument("--dates", nargs="+", default=list(DEFAULT_DATES))
    utci.add_argument("--force", action="store_true")
    utci.add_argument("--dry-run", action="store_true")
    utci.set_defaults(func=download_utci)

    utci_year = sub.add_parser(
        "utci-year",
        help="Download the FULL YEAR of hourly UTCI per city in one request each "
             "(12 requests total instead of 365/city) -- for date-selection analysis",
    )
    add_common(utci_year)
    utci_year.add_argument("--cities", nargs="+", choices=sorted(CITIES), default=sorted(CITIES))
    utci_year.add_argument("--year", required=True, type=int)
    utci_year.add_argument("--force", action="store_true")
    utci_year.add_argument("--dry-run", action="store_true")
    utci_year.set_defaults(func=download_utci_year)

    aemet = sub.add_parser("aemet-daily", help="Download AEMET daily station observations")
    add_common(aemet)
    aemet.add_argument("--stations", nargs="+", required=True, help="Official AEMET station IDs")
    aemet.add_argument("--start", required=True, help="ISO start date")
    aemet.add_argument("--end", required=True, help="ISO end date")
    aemet.add_argument("--force", action="store_true")
    aemet.set_defaults(func=download_aemet_daily)

    urls = sub.add_parser("download-urls", help="Download official portal-selected URLs with manifests")
    add_common(urls)
    urls.add_argument("--source", required=True, choices=("urban_atlas", "cnig"))
    urls.add_argument("--url-file", required=True, type=Path)
    urls.set_defaults(func=download_urls)

    ine = sub.add_parser("ine-sections", help="Download census-section boundaries from INE's live OGC API")
    add_common(ine)
    ine.add_argument("--cities", nargs="+", choices=sorted(CITIES), default=sorted(CITIES))
    ine.add_argument("--force", action="store_true")
    ine.add_argument("--dry-run", action="store_true")
    ine.set_defaults(func=download_ine_census_sections)

    ua = sub.add_parser(
        "urban-atlas",
        help="Download Urban Atlas products (lclu/stl/bbh) per city via the "
             "CDSE EODATA S3 bucket (needs CDSE_ACCESS_KEY/CDSE_SECRET_KEY)",
    )
    add_common(ua)
    ua.add_argument("--product", required=True, choices=("lclu", "stl", "bbh"))
    ua.add_argument("--cities", nargs="+", choices=sorted(UA_FUA_CODE), default=sorted(UA_FUA_CODE))
    ua.add_argument("--year", help="Disambiguate when a product has multiple vintages (e.g. lclu: 2018 or 2021)")
    ua.add_argument("--force", action="store_true")
    ua.add_argument("--dry-run", action="store_true")
    ua.set_defaults(func=download_urban_atlas)

    mitma_pop = sub.add_parser(
        "mitma-population",
        help="Download MITMA's official per-district resident population "
             "(zonificacion_distritos + poblacion_distritos.csv) and one "
             "day's age-stratified 'personas' file, same portal/zonification "
             "already used for OD demand",
    )
    add_common(mitma_pop)
    mitma_pop.add_argument("--date", default="2022-02-08", help="ISO date for the personas (age-stratified) file")
    mitma_pop.add_argument("--dry-run", action="store_true")
    mitma_pop.set_defaults(func=download_mitma_population)

    all_cmd = sub.add_parser(
        "all",
        help="Unified orchestrator: run several fully-automated datasets for the "
             "selected cities in one idempotent, resumable command",
    )
    add_common(all_cmd)
    all_cmd.add_argument("--cities", nargs="+", choices=sorted(CITIES), default=sorted(CITIES))
    all_cmd.add_argument(
        "--datasets", nargs="+",
        choices=("worldcover", "era5-land", "utci", "ine-sections"),
        default=("worldcover", "era5-land", "utci", "ine-sections"),
        help="aemet-daily and download-urls (Urban Atlas/CNIG) require a manually "
             "selected station/URL list and are intentionally not part of this "
             "orchestrator -- run them as their own subcommands.",
    )
    all_cmd.add_argument("--dates", nargs="+", help="Explicit ISO dates for era5-land/utci")
    all_cmd.add_argument("--start-date", help="Inclusive ISO start date (with --end-date, builds --dates)")
    all_cmd.add_argument("--end-date", help="Inclusive ISO end date")
    all_cmd.add_argument(
        "--years", nargs="+", type=int,
        help="Accepted for forward compatibility with a future ordinary/hot-day/"
             "heatwave sampling policy (still an open methodological decision -- "
             "see the project plan). Has NO effect by itself: combine with --dates "
             "or --start-date/--end-date, or this command refuses to guess which "
             "day(s) of each year to fetch.",
    )
    all_cmd.add_argument("--force", action="store_true")
    all_cmd.add_argument("--dry-run", action="store_true")
    all_cmd.set_defaults(func=run_all)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_layout(args.root)
    try:
        args.func(args)
    except (ValueError, RuntimeError, urllib.error.URLError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
