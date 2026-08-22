"""`gm` — command-line entrypoint for the green_mobility pipeline.

Each subcommand is independently re-runnable; none silently reuses stale
data — missing prerequisites raise a clear error naming the command that
produces them. Mirrors the CLI style of NOMAD's own preprocessing scripts
(argparse, one subcommand per pipeline stage) rather than introducing a new
CLI framework dependency.
"""
from __future__ import annotations

import argparse
import sys

from green_mobility.config import (
    CONFIGS_DIR,
    ConfigError,
    load_all_cities,
    load_intervention_config,
)
from green_mobility.manifest import write_manifest


def _resolve_city(cities: dict, slug: str):
    if slug not in cities:
        raise SystemExit(f"unknown city '{slug}', configured cities: {sorted(cities)}")
    return cities[slug]


def _resolve_scenario(city, key: str):
    if key not in city.scenarios:
        raise SystemExit(
            f"unknown scenario '{key}' for {city.slug}, "
            f"configured scenarios: {sorted(city.scenarios)}"
        )
    return city.scenarios[key]


def cmd_prep_boundaries(args, cities) -> int:
    from green_mobility.network_prep.boundaries import validate_cities

    targets = [cities[s] for s in (args.city or sorted(cities))]
    matches = validate_cities(targets)
    for m in matches:
        print(f"{m.city_slug}: '{m.query_name}' -> '{m.matched_fuaname}'")
    return 0


def cmd_prep_network(args, cities) -> int:
    from green_mobility.network_prep.osm_download import download_region_osm
    from green_mobility.nomad_wrapper.build import run_simplify_osm

    city = _resolve_city(cities, args.city)
    print(f"[{city.slug}] downloading regional OSM extract...")
    path = download_region_osm(city, force=args.force)
    print(f"[{city.slug}] OSM extract at {path}")
    print(f"[{city.slug}] running NOMAD's simplify_osm.py...")
    result = run_simplify_osm(city, force_clip=args.force_clip)
    print(result.stdout)
    write_manifest(
        city.graph_bin, "prep-network",
        {"city": city.slug, "force": args.force, "force_clip": args.force_clip,
         "geofabrik_url": city.geofabrik_url},
    )
    return 0


def cmd_prep_demand(args, cities) -> int:
    from green_mobility.nomad_wrapper.build import download_mitma, run_build_od

    city = _resolve_city(cities, args.city)
    print(f"[{city.slug}] downloading MITMA data ({', '.join(city.mitma_months)})...")
    download_mitma(city)
    print(f"[{city.slug}] running NOMAD's build_od.py...")
    run_build_od(city, scale=args.scale)
    from green_mobility.demand.mode_choice import ModeChoiceModel

    write_manifest(
        city.od_csv("weekday"), "prep-demand",
        {"city": city.slug, "months": list(city.mitma_months), "scale": args.scale,
         "occupancy_factor": city.demand_occupancy_factor, "noise_sigma": city.demand_noise_sigma,
         "mode_choice_source": ModeChoiceModel(city.slug).source},
    )
    return 0


def cmd_run(args, cities) -> int:
    import dataclasses
    import datetime as dt

    from green_mobility.nomad_wrapper.runner import ScenarioRun, parse_nomad_cli_stats

    city = _resolve_city(cities, args.city)
    scenario = _resolve_scenario(city, args.scenario)
    if args.date:
        # Override the scenario's calendar date without needing one YAML
        # entry per date (e.g. for the 26-date thermal-calendar sweep) --
        # a distinct `key` keeps its config/output paths from colliding
        # with the base scenario or with other overridden dates.
        override_date = dt.date.fromisoformat(args.date)
        od_label = scenario.od_label
        if od_label == "weekday":
            # Auto-switch to the matching weekend/holiday OD when the
            # override date actually falls on one -- NOMAD's own
            # build_od.py already builds both (is_weekend(): Sat, Sun, or a
            # country holiday via the `holidays` package), so re-using that
            # same definition here keeps the OD choice consistent with how
            # the two files were actually built, not a separately invented
            # weekday/weekend rule.
            try:
                import holidays as holidays_lib
                is_holiday = override_date in holidays_lib.country_holidays(
                    city.country, years={override_date.year}
                )
            except ImportError:
                is_holiday = False
            if override_date.weekday() >= 5 or is_holiday:
                od_label = "weekend"
        scenario = dataclasses.replace(
            scenario, date=override_date, od_label=od_label,
            key=f"{scenario.key}_{override_date.isoformat()}",
        )
    if args.traffic_model:
        # Override the traffic model without a per-model YAML scenario --
        # NOMAD already implements both "queue" (QueueTrafficModel, the
        # default used by every scenario config so far) and "ltm"
        # (LtmTrafficModel, a tested, fully-wired Cell Transmission Model
        # with real spillback) -- see external/nomad/src/traffic/{queue,ltm}
        # _model.{hpp,cpp} and tools/nomad-cli/main.cpp:96-99. A distinct
        # key keeps this run's config/output from colliding with the
        # default-traffic-model run of the same scenario/date.
        scenario = dataclasses.replace(
            scenario, traffic_model=args.traffic_model,
            key=f"{scenario.key}_{args.traffic_model}",
        )
    if args.demand_scale is not None:
        # Native NOMAD downsampling (binomial thinning of the already-built
        # OD file's counts, seed hardcoded in nomad-cli/main.cpp -- see
        # od_matrix.cpp) -- for a quick reduced-demand test without
        # rebuilding any OD file. A distinct key keeps this run's
        # config/output from colliding with the full-demand run.
        scenario = dataclasses.replace(
            scenario, demand_scale=args.demand_scale,
            key=f"{scenario.key}_scale{args.demand_scale:g}",
        )
    if args.ltm_discharge_cap:
        scenario = dataclasses.replace(
            scenario, ltm_discharge_cap=True, ltm_discharge_burst_s=args.ltm_discharge_burst_s,
            key=f"{scenario.key}_dcap{args.ltm_discharge_burst_s:g}",
        )
    if args.pretrip_reroute:
        scenario = dataclasses.replace(
            scenario, enable_pretrip_reroute=True,
            key=f"{scenario.key}_pretrip",
        )
    if args.od_label:
        # Point at an already-materialized OD file (e.g. a stress-test
        # scaled OD from scripts/diagnostics/run_nomad_traffic_validation.py)
        # without needing a per-factor YAML scenario entry.
        scenario = dataclasses.replace(
            scenario, od_label=args.od_label,
            key=f"{scenario.key}_{args.od_label}",
        )
    if args.start_time or args.end_time:
        # Short peak-window test instead of a full day -- demand within the
        # window is whatever the OD file's depart_mean_s/depart_std_s puts
        # there already, no separate windowed OD file needed.
        def _parse_time(s: str) -> dt.time:
            return dt.time.fromisoformat(s if s.count(":") >= 2 else f"{s}:00")

        start_time = _parse_time(args.start_time) if args.start_time else scenario.start_time
        end_time = _parse_time(args.end_time) if args.end_time else scenario.end_time
        window_label = f"{start_time.strftime('%H%M')}_{end_time.strftime('%H%M')}"
        scenario = dataclasses.replace(
            scenario, start_time=start_time, end_time=end_time,
            key=f"{scenario.key}_{window_label}",
        )
    run = ScenarioRun(city, scenario)
    print(f"[{city.slug}/{scenario.key}] writing config to {run.config_path}")
    result = run.run()
    print(result.stdout)
    run_stats = parse_nomad_cli_stats(result.stdout)
    if getattr(result, "peak_rss_kb", None):
        run_stats["peak_rss_mb"] = round(result.peak_rss_kb / 1024, 1)
    write_manifest(
        run.output_dir, "run",
        {"city": city.slug, "scenario": scenario.key, "config": str(run.config_path),
         "run_stats": run_stats},
    )
    print(f"[{city.slug}/{scenario.key}] output written under {run.output_dir}")
    return 0


def cmd_flows(args, cities) -> int:
    from green_mobility.nomad_wrapper.flows import extract_all_mode_flows

    city = _resolve_city(cities, args.city)
    scenario = _resolve_scenario(city, args.scenario)
    modes = tuple(args.modes.split(",")) if args.modes else None
    df = extract_all_mode_flows(city, scenario, modes)
    out_path = city.green_mobility_dir / "flows" / f"{scenario.key}_flows.parquet"
    write_manifest(
        out_path, "flows",
        {"city": city.slug, "scenario": scenario.key, "modes": list(modes or scenario.modes)},
    )
    print(df.groupby("mode")["count"].sum())
    return 0


def cmd_intervene(args, cities) -> int:
    import pandas as pd

    from green_mobility.interventions.compare import compare_strategies

    city = _resolve_city(cities, args.city)
    scenario = _resolve_scenario(city, args.scenario)
    intervention_cfg = load_intervention_config()

    gm_dir = city.green_mobility_dir
    edges_table_path = gm_dir / "exposure" / f"{scenario.key}_edges.parquet"
    if not edges_table_path.exists():
        raise SystemExit(
            f"{edges_table_path} not found. Build it first: run `gm flows`, the "
            "thermal module (thermal.utci / thermal.shade / thermal.canopy), and "
            "exposure.combine.compute_exposure, then join onto edges.parquet and "
            "vulnerability.index output to produce this file (see README "
            "'Pipeline stages') — this repo does not auto-chain those steps "
            "because each depends on real external data (weather, canopy raster, "
            "census indicators) that must be supplied first."
        )
    edges = pd.read_parquet(edges_table_path)

    strategies = tuple(args.strategies.split(",")) if args.strategies else intervention_cfg.strategies
    q_values = tuple(float(q) for q in args.q.split(",")) if args.q else intervention_cfg.q_values

    result = compare_strategies(edges, strategies, q_values, seed=intervention_cfg.random_seed)
    out_path = city.results_dir / scenario.key / "intervention_comparison.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    write_manifest(
        out_path, "intervene",
        {"city": city.slug, "scenario": scenario.key, "strategies": list(strategies),
         "q_values": list(q_values), "seed": intervention_cfg.random_seed,
         "edges_table": str(edges_table_path)},
    )
    print(result.to_string(index=False))
    print(f"\nwritten to {out_path}")
    return 0


def cmd_select_dates(args, cities) -> int:
    from datetime import date as date_cls
    from pathlib import Path

    from green_mobility.thermal.date_selection import build_calendar, load_year_daily_stats

    slugs = args.cities or sorted(cities)
    unknown = set(slugs) - set(cities)
    if unknown:
        raise SystemExit(f"unknown city slug(s): {sorted(unknown)}, configured: {sorted(cities)}")

    era5_dir = Path("data/climate_raw/era5")
    missing = [
        slug for slug in slugs
        if not (era5_dir / f"utci_{slug}_{args.year}.nc").exists()
    ]
    if missing:
        raise SystemExit(
            f"Missing full-year UTCI file for {len(missing)} cities: {missing}. Run:\n"
            f"  python python/download_data.py utci-year --cities {' '.join(missing)} --year {args.year}"
        )

    manual_dates = [date_cls.fromisoformat(d) for d in (args.dates or [])]
    if not args.select_monthly_medoids and not args.include_heatwaves and not manual_dates:
        raise SystemExit(
            "Nothing to select: pass --select-monthly-medoids and/or --include-heatwaves "
            "and/or --dates <ISO dates...> -- this command does not guess a default set."
        )

    year_stats = {
        slug: load_year_daily_stats(era5_dir / f"utci_{slug}_{args.year}.nc", cities[slug].lat, cities[slug].lon)
        for slug in slugs
    }

    final_dates, classification = build_calendar(
        year_stats,
        include_monthly=args.select_monthly_medoids,
        medoid_mode=args.medoid_mode,
        include_heatwaves=args.include_heatwaves,
        manual_dates=manual_dates,
    )

    print(f"Cities: {slugs}")
    print(f"Final calendar ({len(final_dates)} dates): {[d.isoformat() for d in final_dates]}")
    print()
    print(classification.groupby("date").agg(
        n_extreme_heat=("extreme_heat", "sum"),
        n_official_heatwave=("official_heatwave", "sum"),
    ).to_string())

    if args.dry_run:
        print("\n[dry-run] not writing calendar/classification files")
        return 0

    out_dir = Path("data/calendar")
    out_dir.mkdir(parents=True, exist_ok=True)
    calendar_path = out_dir / f"{args.year}_calendar.json"
    classification_path = out_dir / f"{args.year}_classification.parquet"
    calendar_path.write_text(
        __import__("json").dumps([d.isoformat() for d in final_dates], indent=2) + "\n"
    )
    classification.to_parquet(classification_path, index=False)
    write_manifest(
        classification_path, "select-dates",
        {"cities": slugs, "year": args.year, "medoid_mode": args.medoid_mode,
         "select_monthly_medoids": args.select_monthly_medoids,
         "include_heatwaves": args.include_heatwaves,
         "manual_dates": [d.isoformat() for d in manual_dates],
         "n_dates": len(final_dates)},
    )
    print(f"\nwritten: {calendar_path}\nwritten: {classification_path}")
    return 0


def cmd_dose_benefit(args, cities) -> int:
    import json
    from datetime import date as date_cls
    from pathlib import Path

    import pandas as pd

    from green_mobility.thermal.exposure_calendar import (
        _load_edge_person_hours,
        compute_city_date_dose_benefit,
        dose_benefit_row,
    )

    slugs = args.cities or sorted(cities)
    unknown = set(slugs) - set(cities)
    if unknown:
        raise SystemExit(f"unknown city slug(s): {sorted(unknown)}, configured: {sorted(cities)}")

    if args.dates:
        dates = [date_cls.fromisoformat(d) for d in args.dates]
    else:
        calendar_path = Path(args.calendar) if args.calendar else Path("data/calendar") / f"{args.year}_calendar.json"
        if not calendar_path.exists():
            raise SystemExit(
                f"{calendar_path} not found -- pass --dates explicitly or run "
                "`gm select-dates` first to build the reproducible calendar"
            )
        dates = [date_cls.fromisoformat(d) for d in json.loads(calendar_path.read_text())]

    canopy_types = args.canopy_types.split(",") if args.canopy_types else ["evergreen", "deciduous"]
    era5_dir = Path(args.era5_dir)

    rows = []
    skipped = []
    for slug in slugs:
        city = cities[slug]
        scenario = _resolve_scenario(city, args.scenario)
        flows_path = city.green_mobility_dir / "exposure" / f"{scenario.key}_walk_bike_edge_bins.parquet"
        canopy_path = city.green_mobility_dir / "thermal" / "canopy_fraction.parquet"
        utci_path = era5_dir / f"utci_{slug}_{args.year}.nc"
        missing = [p for p in (flows_path, canopy_path, utci_path) if not p.exists()]
        if missing:
            skipped.append((slug, [str(p) for p in missing]))
            continue

        person_hours = _load_edge_person_hours(flows_path)
        for d in dates:
            for ctype in canopy_types:
                edge_hour, canopy_type = compute_city_date_dose_benefit(
                    city, d, year=args.year, era5_dir=era5_dir, canopy_type=ctype,
                )
                rows.append(dose_benefit_row(slug, d, canopy_type, edge_hour, person_hours))

    if not rows:
        raise SystemExit(f"no dose-benefit rows computed -- every city skipped: {skipped}")

    result = pd.DataFrame(rows)
    out_path = Path(args.out) if args.out else Path("data/thermal") / f"dose_benefit_{args.year}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    write_manifest(
        out_path, "dose-benefit",
        {"cities": slugs, "dates": [d.isoformat() for d in dates], "canopy_types": canopy_types,
         "year": args.year, "scenario": args.scenario, "skipped": skipped, "n_rows": len(result)},
    )
    print(result.to_string(index=False))
    if skipped:
        print(f"\nskipped {len(skipped)} city(ies) (missing prerequisite files): {skipped}", file=sys.stderr)
    print(f"\nwritten: {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prep-boundaries", help="validate FUA boundaries.gpkg against configured cities")
    p.add_argument("--city", action="append", help="city slug (repeatable); default: all configured cities")
    p.set_defaults(func=cmd_prep_boundaries)

    p = sub.add_parser("prep-network", help="download OSM + build the routing graph for a city")
    p.add_argument("--city", required=True)
    p.add_argument("--force", action="store_true", help="re-download the regional OSM extract")
    p.add_argument("--force-clip", action="store_true", help="re-clip to the city FUA even if cached")
    p.set_defaults(func=cmd_prep_network)

    p = sub.add_parser("prep-demand", help="download MITMA + build the OD matrix for a city")
    p.add_argument("--city", required=True)
    p.add_argument("--scale", type=float, default=1.0)
    p.set_defaults(func=cmd_prep_demand)

    p = sub.add_parser("run", help="run a scenario through nomad_cli (car-only congestion baseline)")
    p.add_argument("--city", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--date", help="override the scenario's ISO date (e.g. for a date-calendar sweep)")
    p.add_argument("--traffic-model", choices=("queue", "ltm"), help="override the scenario's traffic model")
    p.add_argument("--start-time", help="override the scenario's start time (HH:MM or HH:MM:SS), "
                                        "e.g. for a short peak-window test instead of a full day")
    p.add_argument("--end-time", help="override the scenario's end time (HH:MM or HH:MM:SS)")
    p.add_argument("--od-label", help="override the scenario's od_label (selects "
                                       "data/{slug}/od_{slug}_{od_label}.csv), e.g. to reuse an "
                                       "already-materialized scaled OD file")
    p.add_argument("--demand-scale", type=float, help="override the scenario's demand_scale, "
                                       "(0,1] -- NOMAD's own binomial-thinning downsample (see "
                                       "external/nomad/src/demand/od_matrix.cpp), NOT the OD-file "
                                       "--scale used by `prep-demand` (that rescales the raw OD "
                                       "counts on disk; this thins agents at simulation time, no "
                                       "OD file changes needed)")
    p.add_argument("--ltm-discharge-cap", action="store_true",
                   help="enable LtmTrafficModel's discharge-rate token bucket (no effect if "
                        "--traffic-model isn't 'ltm'). Without this, once occupancy is correctly "
                        "bounded the BPR congestion signal schedule_reroutes() reads is capped "
                        "near +15%% free-flow -- too weak to trigger meaningful rerouting.")
    p.add_argument("--ltm-discharge-burst-s", type=float, default=10.0,
                   help="discharge-cap burst window in seconds (default: %(default)s, only used "
                        "when --ltm-discharge-cap is set)")
    p.add_argument("--pretrip-reroute", action="store_true",
                   help="give Waiting agents a fresh traffic-aware route shortly before their "
                        "scheduled departure (uses the same reroute_router_/reroute_interval_s "
                        "cadence as mid-trip rerouting). Mitigates agents departing on a stale "
                        "free-flow-only pre-route straight into already-known congestion -- run "
                        "alongside --ltm-discharge-cap, which is what actually produces the "
                        "congestion-aware cost signal this depends on.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("flows", help="extract per-edge-per-hour-per-mode flow (car/walk/bike)")
    p.add_argument("--city", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--modes", help="comma-separated, default: scenario's configured modes")
    p.set_defaults(func=cmd_flows)

    p = sub.add_parser("intervene", help="compare canopy/shade intervention strategies at q=1/5/10/20%%")
    p.add_argument("--city", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--strategies", help="comma-separated, default: configs/interventions/strategies.yaml")
    p.add_argument("--q", help="comma-separated fractions, default: configs/interventions/strategies.yaml")
    p.set_defaults(func=cmd_intervene)

    p = sub.add_parser(
        "select-dates",
        help="build the reproducible calendar of representative days from the full-year UTCI series",
    )
    p.add_argument("--cities", nargs="+", help="city slugs; default: all configured cities")
    p.add_argument("--year", type=int, required=True)
    p.add_argument(
        "--select-monthly-medoids", action="store_true",
        help="include one representative day per month (see --medoid-mode)",
    )
    p.add_argument(
        "--medoid-mode", choices=("common", "per_city"), default="common",
        help="'common' (Option B, recommended): one date/month shared by all cities. "
             "'per_city' (Option A, robustness check): a different date/month per city.",
    )
    p.add_argument(
        "--include-heatwaves", action="store_true",
        help="include the verified 2022 summer-normal + heatwave candidate dates",
    )
    p.add_argument("--dates", nargs="+", help="manual ISO date overrides, added to the final set")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_select_dates)

    p = sub.add_parser(
        "dose-benefit",
        help="compute heat-dose/cold-cost/net-benefit rows from real canopy+UTCI+walk/bike-flow data",
    )
    p.add_argument("--cities", nargs="+", help="city slugs; default: all configured cities")
    p.add_argument("--dates", nargs="+", help="ISO dates; default: read data/calendar/<year>_calendar.json")
    p.add_argument("--calendar", default=None, help="calendar JSON path, default data/calendar/<year>_calendar.json")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--scenario", default="weekday_full", help="scenario key for the walk/bike flow table")
    p.add_argument("--canopy-types", help="comma-separated, default: evergreen,deciduous")
    p.add_argument("--era5-dir", default="data/climate_raw/era5")
    p.add_argument("--out", help="output parquet path, default data/thermal/dose_benefit_<year>.parquet")
    p.set_defaults(func=cmd_dose_benefit)

    return parser


def _expected_errors() -> tuple[type[Exception], ...]:
    from green_mobility.network_prep.boundaries import BoundariesError
    from green_mobility.nomad_wrapper.build import NomadScriptError
    from green_mobility.nomad_wrapper.flows import NomadCapabilityError
    from green_mobility.nomad_wrapper.paths import NomadNotBuiltError

    return (
        ConfigError,
        BoundariesError,
        NomadScriptError,
        NomadCapabilityError,
        NomadNotBuiltError,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cities = load_all_cities(CONFIGS_DIR / "cities")
        return args.func(args, cities)
    except _expected_errors() as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
