import datetime as dt

import pytest

from green_mobility.config import (
    CONFIGS_DIR,
    CityConfig,
    ConfigError,
    InterventionConfig,
    ScenarioSpec,
    load_all_cities,
    load_intervention_config,
    slugify,
)


def test_slugify_matches_nomad_algorithm():
    # Exact behaviour of the slugify() duplicated in
    # external/nomad/python/preprocessing/{simplify_osm,build_od}.py —
    # this must never drift, since it determines data/{slug}/ paths.
    assert slugify("Palma de Mallorca") == "palma_de_mallorca"
    assert slugify("Zaragoza") == "zaragoza"
    assert slugify("Castilla y León") == "castilla_y_leon"


def test_all_twelve_city_configs_load():
    cities = load_all_cities(CONFIGS_DIR / "cities")
    assert set(cities) == {
        "palma_de_mallorca", "zaragoza", "murcia", "valladolid",
        "madrid", "barcelona", "valencia", "sevilla", "cordoba",
        "granada", "bilbao", "a_coruna",
    }
    for slug, city in cities.items():
        assert city.slug == slug
        assert city.scenarios  # every city must define at least one scenario


def test_city_slug_mismatch_rejected():
    with pytest.raises(ConfigError):
        CityConfig(
            name="Palma de Mallorca",
            slug="wrong_slug",
            country="ES",
            lat=39.5696,
            lon=2.6502,
            geofabrik_url="https://example.com/x.osm.pbf",
            geofabrik_raw_filename="x.osm.pbf",
            mitma_months=("2022-02",),
            demand_occupancy_factor=1.2,
            demand_noise_sigma=0.08,
            scenarios={
                "s": ScenarioSpec(
                    key="s",
                    date=dt.date(2022, 2, 8),
                    start_time=dt.time(0, 0, 0),
                    end_time=dt.time(23, 59, 59),
                    modes=("car",),
                )
            },
        )


def test_walk_bike_require_astar_router():
    with pytest.raises(ConfigError, match="astar"):
        ScenarioSpec(
            key="bad",
            date=dt.date(2022, 2, 8),
            start_time=dt.time(6, 0, 0),
            end_time=dt.time(9, 0, 0),
            modes=("car", "walk"),
            router="CH",
        )


def test_scenario_rejects_unknown_mode():
    with pytest.raises(ConfigError):
        ScenarioSpec(
            key="bad",
            date=dt.date(2022, 2, 8),
            start_time=dt.time(6, 0, 0),
            end_time=dt.time(9, 0, 0),
            modes=("transit",),
        )


def test_intervention_config_loads_and_validates():
    cfg = load_intervention_config()
    assert cfg.q_values == (0.01, 0.05, 0.10, 0.20)
    assert set(cfg.strategies) == {
        "random", "thermal_hotspot", "flow", "active_mobility", "optimized", "equity",
    }


def test_intervention_config_rejects_bad_q():
    with pytest.raises(ConfigError):
        InterventionConfig(q_values=(1.5,), strategies=("random",))


def test_intervention_config_rejects_unknown_strategy():
    with pytest.raises(ConfigError):
        InterventionConfig(q_values=(0.1,), strategies=("not_a_strategy",))
