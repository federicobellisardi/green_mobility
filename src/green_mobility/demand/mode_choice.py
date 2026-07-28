"""Multinomial-logit mode-choice model (car/walk/bike) for MITMA OD trips.

Replaces the flat, national, distance-banded `DISTANCE_MODE_FRACTIONS`
lookup (Movilia 2006 proxy, external/nomad/python/preprocessing/build_od.py)
with a probabilistic model where mode probability depends on trip distance
plus a per-city calibration term, instead of imposing a fixed percentage
split per city.

Does not include "transit": every scenario in this project already
restricts NOMAD's simulated `modes` to car/walk/bike (transit lacks a real
timetable/headway model in NOMAD), so this model is deliberately fit
conditional on "not transit, not other" (moto/taxi/otro) -- it answers
"given this trip uses one of the three modes we actually simulate, which
one", not the full unconditional mode split.

Two real, distinct evidence sources, never fabricated:

1. **Distance-decay shape (BASE_COEFS)**: multinomial logit fit via maximum
   likelihood on real disaggregate trip records (distance in km, observed
   mode) from IECA's "Encuesta Social 2011: Movilidad en las Regiones
   Urbanas de Andalucia" (ESOC11), 5,555 trips across Cordoba/Granada/
   Sevilla after excluding transit/other. Pseudo-R2 (McFadden) = 0.342,
   all coefficients significant at p<0.001. Source file used:
   reference/modal_split/esoc11_extract/ESOC11_BLOQUE2_DESPLA_MICRO.dat,
   record layout verified against the official IECA "diseno de registro"
   (juntadeandalucia.es/institutodeestadisticaycartografia/dega).

2. **Per-city calibration (CITY_SHIFT)**: for Cordoba/Granada/Sevilla, the
   SAME ESOC11 fit's own city dummy coefficients (real, directly estimated,
   not re-derived). For the other 8 cities with real aggregate mode-share
   data, a single scalar shift (applied equally to walk and bike utility)
   calibrated so that applying this model to the city's REAL MITMA
   distance-band distribution (computed from the same raw viajes files
   used to build that city's OD, weekday averages, Feb 2022) reproduces
   the real observed car-share from the Metropolitan Mobility Observatory
   report ("Observatorio de la Movilidad Metropolitana", Summary Report
   2023 and Advance 2024, reference/modal_split/2025-Resumen-2023-24-ENG.pdf,
   page 5, metropolitan-area chart). Murcia has neither source -- uses the
   pooled/base coefficients with NO city shift, flagged low-confidence.

This module only predicts probabilities; it does not touch
external/nomad/python/preprocessing/build_od.py itself -- wiring it in
requires editing a file inside the NOMAD submodule, which per this
project's protocol needs a dedicated branch and explicit review before
merge (see scripts/diagnostics/proposed_patch_enter_exit_counters.md for
the same pattern used previously).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MODES = ("car", "walk", "bike")

# Coefficienti pooled (Cordoba come riferimento implicito, auto = 0),
# stimati via sm.MNLogit su 5555 spostamenti reali ESOC11 (car/walk/bike,
# esclusi transit/other). Vedi docstring del modulo per la fonte esatta.
BASE_COEFS = {
    "walk": {"const": -0.291701, "log_dist": -1.548948},
    "bike": {"const": -3.072171, "log_dist": -0.486203},
}

# Shift di citta' (relativi ad auto=0): walk_shift, bike_shift, fonte,
# quota auto riprodotta dal modello e quota target (NaN se stima diretta,
# per cui non esiste un singolo "target" separato dal fit stesso).
#
# Le 8 citta' "OMM ricalibrato" sono state RIDERIVATE (non solo ritoccate)
# rispetto ai valori originali -- vedi
# scripts/diagnostics/calibrate_transit_share_level2.py e
# reports/diagnostics/od_calibration_level2/transit_share_recalibration.csv.
# Motivo: i valori originali erano calibrati SENZA tener conto che
# build_od.py applica una quota transit FISSA nazionale (~27-31% per ogni
# citta', vedi CITY_TRANSIT_SHARE sotto), non quella reale della citta' --
# quindi, applicati alla pipeline vera, non riproducevano il proprio target
# auto (Madrid: shift vecchio dava 30.5% auto, target reale 39.0%). I nuovi
# shift sono risolti numericamente (root-finding su
# P(auto|non-transit,shift) * (1-CITY_TRANSIT_SHARE[citta']) = target),
# sulla distribuzione di distanza REALE MITMA della citta', in modo che i
# due meccanismi (shift + quota transit reale) siano coerenti tra loro,
# entrambi dalla stessa riga della stessa fonte OMM.
CITY_SHIFT: dict[str, dict] = {
    "cordoba":          {"walk": 0.0,     "bike": 0.0,     "source": "ESOC11 MLE diretto"},
    "granada":          {"walk": 0.0060,  "bike": -0.2618, "source": "ESOC11 MLE diretto"},
    "sevilla":          {"walk": -0.6459, "bike": -0.3784, "source": "ESOC11 MLE diretto"},
    "madrid":           {"walk": 1.887281, "bike": 1.887281, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "barcelona":        {"walk": 3.352909, "bike": 3.352909, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "valencia":         {"walk": 1.748135, "bike": 1.748135, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "bilbao":           {"walk": 2.176471, "bike": 2.176471, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "palma_de_mallorca": {"walk": 1.671145, "bike": 1.671145, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "zaragoza":         {"walk": 1.558722, "bike": 1.558722, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "valladolid":       {"walk": 2.428888, "bike": 2.428888, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "a_coruna":         {"walk": 1.263486, "bike": 1.263486, "source": "OMM ricalibrato (scalare, coerente con CITY_TRANSIT_SHARE)"},
    "murcia":           {"walk": 0.0, "bike": 0.0,
                          "source": "NESSUN bersaglio reale disponibile -- modello pooled, bassa confidenza"},
}


# Quota reale di trasporto pubblico per citta' (Osservatorio della Mobilita'
# Metropolitana, reference/modal_split/2025-Resumen-2023-24-ENG.pdf, pag. 5,
# "Modal share for all trip purposes" -- STESSA riga/fonte gia' usata sopra
# per il target auto delle citta' "OMM calibrato (scalare)"). Usata da
# external/nomad/python/preprocessing/build_od.py (branch
# feature/logit-mode-choice) come transit_frac_override, al posto della
# quota transit fissa nazionale per fascia di distanza (DISTANCE_MODE_FRACTIONS)
# -- quella tabella non e' mai stata calibrata per citta' e produce ~27-31%
# transit ovunque, indipendentemente dalla vera infrastruttura di trasporto
# pubblico locale (confermato: Madrid mostra transit=26.8% simulato vs
# 24.3% reale, ma con effetto piu' marcato su citta' con reti molto diverse
# dal valore nazionale medio, es. Barcelona 23.05% reale).
#
# cordoba/granada/sevilla/murcia intenzionalmente assenti: nessun dato reale
# di quota transit disponibile in questo repo per queste 4 citta' (ESOC11 ha
# probabilmente il dato grezzo ma manca il codebook per leggerlo in modo
# affidabile; murcia non ha alcuna fonte). Restano sulla tabella nazionale
# fissa -- un gap noto, non silenziosamente ignorato, vedi il piano.
CITY_TRANSIT_SHARE: dict[str, float] = {
    "madrid": 0.2430,
    "barcelona": 0.2305,
    "valencia": 0.1360,
    "bilbao": 0.2020,
    "palma_de_mallorca": 0.0720,
    "zaragoza": 0.1530,
    "valladolid": 0.1310,
    "a_coruna": 0.1215,
}


# Ore di punta usate dal ciclo di equilibrio mode-choice/congestione
# (pilota, vedi scripts/diagnostics/mode_choice_equilibrium_pilot.py):
# derivate dal profilo orario REALE di ritardo (tempo simulato/free-flow)
# osservato su Palma (unica citta' con dati abbastanza dettagliati finora,
# vedi reports/diagnostics/mode_choice_calibration/), non un'assunzione
# arbitraria -- riusate per le altre citta' come prima approssimazione,
# non ancora validate citta' per citta'.
PEAK_HOURS = frozenset({7, 8, 9, 13, 14, 15, 16, 17, 18, 19})

# Bonus di congestione in ora di punta (aggiunto a walk_shift/bike_shift
# SOLO per period in PEAK_HOURS), popolato dal pilota di equilibrio.
# Vuoto = nessuna correzione per nessuna citta' (comportamento di default,
# identico a prima che questo meccanismo esistesse).
CITY_PEAK_BONUS: dict[str, float] = {}


@dataclass(frozen=True)
class ModeChoiceModel:
    """Predice P(car), P(walk), P(bike) per distanza, citta' e (opzionale) ora."""

    city_slug: str

    def __post_init__(self) -> None:
        if self.city_slug not in CITY_SHIFT:
            raise KeyError(
                f"citta' '{self.city_slug}' non calibrata in CITY_SHIFT -- "
                "aggiungere uno shift (da ESOC11 o OMM) o usare shift=0 esplicito "
                "prima di usare questo modello, non assumere silenziosamente."
            )

    def predict_proba(self, distance_km: float | np.ndarray,
                       period: int | None = None) -> dict[str, np.ndarray]:
        """Ritorna {mode: probabilita'} per una o piu' distanze (km).
        distance_km deve essere > 0 (log-distanza non definita a 0).
        period: ora MITMA (0-23) opzionale -- se in PEAK_HOURS, applica
        CITY_PEAK_BONUS[city_slug] (default 0.0) in aggiunta allo shift
        di calibrazione. None (default) = nessun bonus, comportamento
        identico a prima che questo parametro esistesse."""
        d = np.asarray(distance_km, dtype=float)
        if np.any(d <= 0):
            raise ValueError("distance_km deve essere > 0 (usato log(distance_km) nel modello)")
        shift = CITY_SHIFT[self.city_slug]
        peak_bonus = CITY_PEAK_BONUS.get(self.city_slug, 0.0) if (
            period is not None and int(period) in PEAK_HOURS) else 0.0
        ld = np.log(d)
        u_walk = BASE_COEFS["walk"]["const"] + BASE_COEFS["walk"]["log_dist"] * ld + shift["walk"] + peak_bonus
        u_bike = BASE_COEFS["bike"]["const"] + BASE_COEFS["bike"]["log_dist"] * ld + shift["bike"] + peak_bonus
        u_car = np.zeros_like(ld)
        u = np.stack([u_car, u_walk, u_bike])
        e = np.exp(u - u.max(axis=0, keepdims=True))
        p = e / e.sum(axis=0, keepdims=True)
        return {"car": p[0], "walk": p[1], "bike": p[2]}

    @property
    def source(self) -> str:
        return CITY_SHIFT[self.city_slug]["source"]
