#!/usr/bin/env python3
"""Pilot: iterative peak-hour congestion feedback into the mode-choice model
(a scoped, time-of-day-only version of a combined mode-choice/assignment
equilibrium -- see notebooks/diagnostics/mode_choice_calibration.ipynb
sec. "Limiti noti" and the conversation that led here for why a city-wide
average congestion signal was too weak on Palma, and why per-road-class
feedback isn't implementable without knowing which roads an OD pair's route
uses).

Mechanism: CITY_PEAK_BONUS[city] (src/green_mobility/demand/mode_choice.py)
is an additive term applied to walk/bike utility ONLY for OD rows whose
MITMA period falls in PEAK_HOURS (7-9, 13-19 -- Palma's own observed delay
profile). After each simulation, this script reads the realized peak-hour
congestion factor (mean_travel_time_s / free_flow_time_s, weighted by
occupancy_veh, restricted to PEAK_HOURS bins) from the edge bins, and
updates the bonus: bonus_{i+1} = bonus_i + ALPHA * (congestion_factor_i - 1).
Converged when the city-wide effective car_frac (from the real OD csv)
changes by < CONVERGENCE_TOL_PP between iterations, or after MAX_ITER.

This does NOT touch the base calibrated shift (CITY_SHIFT, from ESOC11/OMM)
-- CITY_PEAK_BONUS is a separate, additive, peak-hours-only correction on
top of it, so the underlying static calibration stays intact and reviewable
independently.

Usage:
  python scripts/diagnostics/mode_choice_equilibrium_pilot.py bootstrap \
      --city palma_de_mallorca --results-dir results/palma_de_mallorca/weekday_full_car_2022-02-08/2022-02-08_00-00_23-59
  python scripts/diagnostics/mode_choice_equilibrium_pilot.py iterate --city palma_de_mallorca
  # (user launches the printed runlog command on the cluster, waits for it to finish)
  python scripts/diagnostics/mode_choice_equilibrium_pilot.py record \
      --city palma_de_mallorca --results-dir results/palma_de_mallorca/weekday_full_car_2022-02-08/2022-02-08_00-00_23-59
  # repeat iterate/record until "CONVERGED" or MAX_ITER
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from green_mobility.config import load_all_cities
from green_mobility.demand import mode_choice as mc
from green_mobility.nomad_wrapper.build import run_build_od
from green_mobility.nomad_wrapper.car_snapshots import load_car_bins

ALPHA = 10.0            # passo di aggiornamento del bonus (pilota, non ottimizzato)
MAX_ITER = 4
CONVERGENCE_TOL_PP = 1.0  # punti percentuali di car_frac_effettivo

STATE_DIR = REPO_ROOT / "reports" / "diagnostics" / "mode_choice_calibration"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _state_path(city: str) -> Path:
    return STATE_DIR / f"equilibrium_{city}.json"


def _load_state(city: str) -> dict:
    p = _state_path(city)
    if not p.exists():
        raise SystemExit(f"nessuno stato per '{city}' -- lancia prima 'bootstrap'. ({p})")
    return json.loads(p.read_text())


def _save_state(city: str, state: dict) -> None:
    _state_path(city).write_text(json.dumps(state, indent=2))


def _car_frac_effettivo(city_slug: str, cities: dict) -> float:
    path = cities[city_slug].od_csv("weekday")
    df = pd.read_csv(path, usecols=["count", "mode"])
    tot = df.groupby("mode")["count"].sum()
    denom = tot.get("car", 0) + tot.get("walk", 0)
    return float(tot.get("car", 0) / denom) if denom > 0 else float("nan")


def _peak_congestion_factor(results_dir: Path, city_slug: str) -> float:
    """Fattore di ritardo (tempo reale/free-flow) pesato su occupancy_veh,
    ristretto alle ore PEAK_HOURS -- vedi mode_choice.py per la loro origine
    (profilo orario reale osservato su Palma)."""
    bins = load_car_bins(results_dir, occupancy_factor=1.20)
    m = bins[(bins["occupancy_veh"] > 0) & (bins["travel_time_ratio"] > 0)].copy()
    m["hour"] = (m["bin_start_s"] // 3600).astype(int)
    m = m[m["hour"].isin(mc.PEAK_HOURS)]
    if m.empty:
        raise RuntimeError(f"nessun bin in PEAK_HOURS trovato in {results_dir}")
    w = m["occupancy_veh"]
    return float((m["travel_time_ratio"] * w).sum() / w.sum())


def _read_manifest_rates(results_dir: Path) -> tuple[float | None, float | None]:
    # il manifest e' un file FRATELLO di results_dir, non al suo interno:
    # results/<city>/<scenario>/<data>.manifest.json accanto a .../<data>/
    manifest_path = results_dir.parent / f"{results_dir.name}.manifest.json"
    if not manifest_path.exists():
        return None, None
    d = json.loads(manifest_path.read_text())
    stats = d.get("params", {}).get("run_stats", {})
    return stats.get("arrival_rate"), stats.get("teleported_rate")


def cmd_bootstrap(args: argparse.Namespace) -> None:
    """Iterazione 0: usa una simulazione GIA' fatta con CITY_PEAK_BONUS=0
    (il bonus di default -- nessuna correzione picco applicata) per misurare
    il primo punto della serie, senza dover rilanciare nulla."""
    cities = load_all_cities()
    city = args.city
    results_dir = REPO_ROOT / args.results_dir
    if city not in mc.CITY_SHIFT:
        raise SystemExit(f"'{city}' non in CITY_SHIFT -- calibrare prima il modello base")

    congestion_factor = _peak_congestion_factor(results_dir, city)
    car_frac = _car_frac_effettivo(city, cities)
    arrival, teleported = _read_manifest_rates(results_dir)
    next_bonus = 0.0 + ALPHA * (congestion_factor - 1.0)

    state = {
        "city": city,
        "history": [{
            "iteration": 0,
            "peak_bonus_used": 0.0,
            "peak_congestion_factor": congestion_factor,
            "car_frac_effettivo": car_frac,
            "arrival_rate": arrival,
            "teleported_rate": teleported,
            "results_dir": str(results_dir.relative_to(REPO_ROOT)),
        }],
        "next_bonus": next_bonus,
        "converged": False,
    }
    _save_state(city, state)
    print(f"[{city}] iterazione 0 registrata: fattore congestione picco={congestion_factor:.4f}, "
          f"car_frac_effettivo={car_frac:.4f}, arrival={arrival}, teleport={teleported}")
    print(f"[{city}] prossimo bonus (iterazione 1): {next_bonus:.4f}")


def cmd_iterate(args: argparse.Namespace) -> None:
    """Applica l'ultimo bonus calcolato, rigenera l'OD, stampa il comando
    runlog da lanciare per questa iterazione (non posso lanciare io stesso
    SLURM in questa sandbox -- vedi conversazione)."""
    cities = load_all_cities()
    city = args.city
    state = _load_state(city)
    if state.get("converged"):
        print(f"[{city}] gia' convergente -- nessuna nuova iterazione necessaria "
              "(usa --force nello stato se vuoi comunque continuare)")
        return
    iteration = len(state["history"])
    if iteration > MAX_ITER:
        print(f"[{city}] raggiunto MAX_ITER={MAX_ITER} senza convergenza netta -- "
              "vedi reports/.../equilibrium_{city}.json per la serie completa")
        return
    bonus = state["next_bonus"]
    mc.CITY_PEAK_BONUS[city] = bonus
    run_build_od(cities[city])
    print(f"[{city}] OD rigenerata con CITY_PEAK_BONUS={bonus:.4f} (iterazione {iteration})")
    print("\nLancia questo (io non posso, 'run' non risolvibile nella mia sandbox):\n")
    print(f'  runlog -t 72:00 -m 64 -j "nomad_{city}_eq{iteration}" -K "nomad_run_modechoice" \\')
    print(f'    python3 -m green_mobility.cli run --city {city} --scenario weekday_full_car --date 2022-02-08')
    print(f"\nPoi: python scripts/diagnostics/mode_choice_equilibrium_pilot.py record "
          f"--city {city} --results-dir results/{city}/weekday_full_car_2022-02-08/2022-02-08_00-00_23-59")


def cmd_record(args: argparse.Namespace) -> None:
    cities = load_all_cities()
    city = args.city
    state = _load_state(city)
    results_dir = REPO_ROOT / args.results_dir
    iteration = len(state["history"])
    bonus_used = state["next_bonus"]

    congestion_factor = _peak_congestion_factor(results_dir, city)
    car_frac = _car_frac_effettivo(city, cities)
    arrival, teleported = _read_manifest_rates(results_dir)

    prev_car_frac = state["history"][-1]["car_frac_effettivo"]
    delta_pp = 100 * abs(car_frac - prev_car_frac)
    converged = delta_pp < CONVERGENCE_TOL_PP

    next_bonus = bonus_used + ALPHA * (congestion_factor - 1.0)
    state["history"].append({
        "iteration": iteration,
        "peak_bonus_used": bonus_used,
        "peak_congestion_factor": congestion_factor,
        "car_frac_effettivo": car_frac,
        "arrival_rate": arrival,
        "teleported_rate": teleported,
        "results_dir": str(results_dir.relative_to(REPO_ROOT)),
        "delta_pp_vs_prev": delta_pp,
    })
    state["next_bonus"] = next_bonus
    state["converged"] = converged
    _save_state(city, state)

    status = "CONVERGENTE" if converged else "non ancora convergente"
    print(f"[{city}] iterazione {iteration}: fattore congestione picco={congestion_factor:.4f}, "
          f"car_frac_effettivo={car_frac:.4f} (delta {delta_pp:.2f}pp vs iter precedente) -> {status}")
    print(f"[{city}] arrival={arrival}  teleport={teleported}")
    if not converged:
        print(f"[{city}] prossimo bonus: {next_bonus:.4f} -- lancia 'iterate' per continuare")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap")
    b.add_argument("--city", required=True)
    b.add_argument("--results-dir", required=True)
    b.set_defaults(func=cmd_bootstrap)

    i = sub.add_parser("iterate")
    i.add_argument("--city", required=True)
    i.set_defaults(func=cmd_iterate)

    r = sub.add_parser("record")
    r.add_argument("--city", required=True)
    r.add_argument("--results-dir", required=True)
    r.set_defaults(func=cmd_record)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
