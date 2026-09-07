import datetime as dt

from green_mobility.config import CityConfig, ScenarioSpec
from green_mobility.nomad_wrapper.runner import ScenarioRun, expected_output_subdir


def make_city():
    return CityConfig(
        name="Palma de Mallorca",
        slug="palma_de_mallorca",
        country="ES",
        lat=39.5696,
        lon=2.6502,
        geofabrik_url="https://example.com/x.osm.pbf",
        geofabrik_raw_filename="x.osm.pbf",
        mitma_months=("2022-02",),
        demand_occupancy_factor=1.2,
        demand_noise_sigma=0.08,
        scenarios={
            "weekday_full": ScenarioSpec(
                key="weekday_full",
                date=dt.date(2022, 2, 8),
                start_time=dt.time(0, 0, 0),
                end_time=dt.time(23, 59, 59),
                modes=("car", "walk", "bike"),
                router="astar",
            )
        },
    )


def test_expected_output_subdir_matches_nomad_cli_fmt_hm():
    # Mirrors external/nomad/tools/nomad-cli/main.cpp's fmt_hm()/subdir logic
    # exactly: "{sim_date}_{HH-MM}_{HH-MM}".
    scenario = make_city().scenarios["weekday_full"]
    assert expected_output_subdir(scenario) == "2022-02-08_00-00_23-59"


def test_scenario_run_config_dict_has_required_nomad_fields():
    city = make_city()
    run = ScenarioRun(city, city.scenarios["weekday_full"])
    cfg = run.build_config_dict()

    assert cfg["simulation"]["router"] == "astar"
    assert cfg["simulation"]["start_time"] == "2022-02-08 00:00:00"
    assert cfg["simulation"]["end_time"] == "2022-02-08 23:59:59"
    assert cfg["demand"]["source"] == "od_csv"
    assert cfg["demand"]["modes"] == ["car", "walk", "bike"]
    # walk/bike scenarios use astar -> CH preprocessing/cache must be absent
    assert cfg["routing"]["algorithm"] == "astar"
    assert cfg["routing"]["preprocess_ch"] is False
    assert "ch_cache" not in cfg["routing"]


def test_scenario_run_paths_are_independent_per_scenario():
    city = make_city()
    run = ScenarioRun(city, city.scenarios["weekday_full"])
    assert run.config_path.name == "weekday_full.json"
    assert city.slug in str(run.base_output_dir)
