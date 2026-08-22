"""Configuration schema for cities, scenarios and intervention comparisons.

Every city is configured independently (one YAML file, no shared mutable
state) as required by the project brief. This module only defines schema +
validation; it does not know how to run anything.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import time as time_cls
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
NOMAD_DIR = REPO_ROOT / "external" / "nomad"

VALID_MODES = {"car", "walk", "bike"}  # "transit" excluded: NOMAD has no transit graph yet
VALID_STRATEGIES = {
    "random",
    "thermal_hotspot",
    "flow",
    "active_mobility",
    "optimized",
    "equity",
}


def slugify(name: str) -> str:
    """Must stay byte-for-byte identical to the slugify() duplicated in
    external/nomad/python/preprocessing/simplify_osm.py and build_od.py —
    it determines the data/{slug}/ directory NOMAD's own scripts write to.
    Tested in tests/unit/test_config.py against that exact NOMAD behaviour.
    """
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode(),
    ).strip("_")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ScenarioSpec:
    """One simulation window for a city (e.g. a full weekday or a peak hour)."""

    key: str
    date: date_cls
    start_time: time_cls
    end_time: time_cls
    modes: tuple[str, ...]
    od_label: str = "weekday"          # selects data/{slug}/od_{slug}_{od_label}.csv
    router: str = "astar"              # "CH" only valid for car-only scenarios
    traffic_model: str = "queue"       # "queue" | "ltm"
    demand_scale: float = 1.0
    # LtmTrafficModel discharge-rate cap (token bucket, see
    # external/nomad/include/nomad/traffic/ltm_model.hpp) -- opt-in, default
    # off, no effect when traffic_model="queue". Without it, the BPR
    # congestion signal schedule_reroutes() reads is capped near +15% ff
    # once occupancy is correctly bounded (Phases 1-2), which is too weak to
    # trigger meaningful rerouting -- confirmed this makes a large
    # difference at partial demand scales in same-day testing.
    ltm_discharge_cap: bool = False
    ltm_discharge_burst_s: float = 10.0
    # Give every Waiting car agent a fresh, congestion-aware route shortly
    # before its own scheduled departure (see
    # external/nomad SimulationConfig::enable_pretrip_reroute /
    # Simulation::schedule_pretrip_reroutes()) -- opt-in, default off.
    # Without it, every agent departs on the route computed once at
    # pre-routing time (free-flow costs only, no traffic exists yet) and can
    # walk straight into congestion that formed hours earlier with zero
    # foreknowledge -- confirmed a major contributor to the 40-52% teleport
    # rates seen in full-scale production runs.
    enable_pretrip_reroute: bool = False

    def __post_init__(self) -> None:
        if not self.modes:
            raise ConfigError(f"scenario '{self.key}': modes must be non-empty")
        unknown = set(self.modes) - VALID_MODES
        if unknown:
            raise ConfigError(f"scenario '{self.key}': unknown modes {sorted(unknown)}")
        if set(self.modes) - {"car"} and self.router.lower() != "astar":
            # NOMAD README: "CH is car-only ... set router: astar for any scenario using
            # non-car modes" (CHRouter::ch_query ignores AgentMode at query time).
            raise ConfigError(
                f"scenario '{self.key}': modes={self.modes} require router='astar' "
                f"(CH silently ignores non-car modes in NOMAD), got '{self.router}'"
            )
        if self.traffic_model not in {"queue", "ltm"}:
            raise ConfigError(f"scenario '{self.key}': traffic_model must be 'queue' or 'ltm'")
        if not (0.0 < self.demand_scale <= 1.0):
            raise ConfigError(f"scenario '{self.key}': demand_scale must be in (0, 1]")
        if self.ltm_discharge_burst_s <= 0.0:
            raise ConfigError(f"scenario '{self.key}': ltm_discharge_burst_s must be > 0")
        if self.end_time <= self.start_time:
            raise ConfigError(f"scenario '{self.key}': end_time must be after start_time")

    @property
    def start_datetime_str(self) -> str:
        return f"{self.date.isoformat()} {self.start_time.isoformat()}"

    @property
    def end_datetime_str(self) -> str:
        return f"{self.date.isoformat()} {self.end_time.isoformat()}"


@dataclass(frozen=True)
class CityConfig:
    """Independent per-city configuration.

    `name` must match (case-insensitively, substring-ok) the `fuaname` field
    of the JRC/OECD Functional Urban Areas polygon used by NOMAD's own
    find_fua() in simplify_osm.py / build_od.py.
    """

    name: str
    slug: str
    country: str
    lat: float
    lon: float
    geofabrik_url: str
    geofabrik_raw_filename: str
    mitma_months: tuple[str, ...]
    demand_occupancy_factor: float
    demand_noise_sigma: float
    scenarios: dict[str, ScenarioSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected_slug = slugify(self.name)
        if self.slug != expected_slug:
            raise ConfigError(
                f"city '{self.name}': slug '{self.slug}' does not match "
                f"slugify(name)='{expected_slug}' — data/{{slug}}/ paths "
                "written by NOMAD's own scripts would disagree with this config."
            )
        if not self.mitma_months:
            raise ConfigError(f"city '{self.name}': mitma_months must be non-empty")
        for m in self.mitma_months:
            if not re.fullmatch(r"\d{4}-\d{2}", m):
                raise ConfigError(f"city '{self.name}': mitma month '{m}' must be YYYY-MM")
        if not self.scenarios:
            raise ConfigError(f"city '{self.name}': must define at least one scenario")

    # ── Derived paths ────────────────────────────────────────────────────────
    # We deliberately reuse NOMAD's OWN --data-dir layout (data/fua/,
    # data/osm/, data/{slug}/, data/od_raw/) instead of inventing our own —
    # simplify_osm.py and build_od.py are called with --data-dir DATA_DIR and
    # write exactly there; this keeps the wrapper a thin pass-through with no
    # path-translation logic to drift out of sync. See nomad_wrapper/build.py.
    @property
    def city_dir(self) -> Path:
        """NOMAD-script-owned: nodes.parquet, edges.parquet, graph.bin, od_*.csv."""
        return DATA_DIR / self.slug

    @property
    def green_mobility_dir(self) -> Path:
        """Our own artifacts for this city (scenario configs, flows, thermal,
        vulnerability tables) — kept alongside but never overwritten by NOMAD's
        scripts, which only ever touch fua/, osm/, od_raw/ and city_dir's
        NOMAD-native filenames."""
        return self.city_dir / "green_mobility"

    @property
    def results_dir(self) -> Path:
        return RESULTS_DIR / self.slug

    @property
    def raw_osm_path(self) -> Path:
        return DATA_DIR / "osm" / self.geofabrik_raw_filename

    @property
    def fua_clipped_osm_path(self) -> Path:
        return DATA_DIR / "osm" / f"{self.slug}_fua.osm.pbf"

    def od_csv(self, od_label: str) -> Path:
        return self.city_dir / f"od_{self.slug}_{od_label}.csv"

    @property
    def nodes_parquet(self) -> Path:
        return self.city_dir / "nodes.parquet"

    @property
    def edges_parquet(self) -> Path:
        return self.city_dir / "edges.parquet"

    @property
    def graph_bin(self) -> Path:
        return self.city_dir / "graph.bin"


@dataclass(frozen=True)
class InterventionConfig:
    """Shared comparison-grid config (not per-city: it defines the experiment
    design, applied identically to whichever city/scenario it's run against)."""

    q_values: tuple[float, ...]
    strategies: tuple[str, ...]
    random_seed: int = 42

    def __post_init__(self) -> None:
        if not self.q_values:
            raise ConfigError("intervention config: q_values must be non-empty")
        for q in self.q_values:
            if not (0.0 < q <= 1.0):
                raise ConfigError(f"intervention config: q={q} must be in (0, 1]")
        unknown = set(self.strategies) - VALID_STRATEGIES
        if unknown:
            raise ConfigError(f"intervention config: unknown strategies {sorted(unknown)}")
        if not self.strategies:
            raise ConfigError("intervention config: strategies must be non-empty")


def _parse_scenario(key: str, raw: dict[str, Any]) -> ScenarioSpec:
    try:
        return ScenarioSpec(
            key=key,
            date=date_cls.fromisoformat(str(raw["date"])),
            start_time=time_cls.fromisoformat(str(raw["start_time"])),
            end_time=time_cls.fromisoformat(str(raw["end_time"])),
            modes=tuple(raw["modes"]),
            od_label=raw.get("od_label", "weekday"),
            router=raw.get("router", "astar"),
            traffic_model=raw.get("traffic_model", "queue"),
            demand_scale=float(raw.get("demand_scale", 1.0)),
            ltm_discharge_cap=bool(raw.get("ltm_discharge_cap", False)),
            ltm_discharge_burst_s=float(raw.get("ltm_discharge_burst_s", 10.0)),
            enable_pretrip_reroute=bool(raw.get("enable_pretrip_reroute", False)),
        )
    except KeyError as exc:
        raise ConfigError(f"scenario '{key}': missing required field {exc}") from exc


def load_city_config(path: Path) -> CityConfig:
    raw = yaml.safe_load(path.read_text())
    try:
        scenarios = {
            k: _parse_scenario(k, v) for k, v in raw.get("scenarios", {}).items()
        }
        return CityConfig(
            name=raw["name"],
            slug=raw["slug"],
            country=raw.get("country", "ES"),
            lat=float(raw["lat"]),
            lon=float(raw["lon"]),
            geofabrik_url=raw["geofabrik"]["region_url"],
            geofabrik_raw_filename=raw["geofabrik"]["raw_filename"],
            mitma_months=tuple(raw["mitma"]["months"]),
            demand_occupancy_factor=float(raw.get("demand", {}).get("occupancy_factor", 1.20)),
            demand_noise_sigma=float(raw.get("demand", {}).get("noise_sigma", 0.08)),
            scenarios=scenarios,
        )
    except KeyError as exc:
        raise ConfigError(f"{path}: missing required field {exc}") from exc


def load_all_cities(configs_dir: Path = CONFIGS_DIR / "cities") -> dict[str, CityConfig]:
    cities = {}
    for path in sorted(configs_dir.glob("*.yaml")):
        cfg = load_city_config(path)
        if cfg.slug in cities:
            raise ConfigError(f"duplicate city slug '{cfg.slug}' from {path}")
        cities[cfg.slug] = cfg
    return cities


def load_intervention_config(
    path: Path = CONFIGS_DIR / "interventions" / "strategies.yaml",
) -> InterventionConfig:
    raw = yaml.safe_load(path.read_text())
    return InterventionConfig(
        q_values=tuple(float(q) for q in raw["q_values"]),
        strategies=tuple(raw["strategies"]),
        random_seed=int(raw.get("random_seed", 42)),
    )
