"""Logica compartilhada entre os scripts de analise (analyze_runs.py,
fetch_garmin.py).

Define os marcos de distancia e a funcao que transforma uma lista de corridas
ja normalizadas em um relatorio pronto para a pagina web (build_page.py). O
report.json resultante funciona tambem como cache local para a atualizacao
incremental do fetch_garmin.py.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

# Marcos de distancia (em km). 21 e 42 usam a distancia oficial de prova
# (meia maratona / maratona) como referencia para a tolerancia.
MILESTONES = [
    {"key": "5k", "label": "5 km", "km": 5.0},
    {"key": "10k", "label": "10 km", "km": 10.0},
    {"key": "15k", "label": "15 km", "km": 15.0},
    {"key": "21k", "label": "21 km (meia)", "km": 21.0975},
    {"key": "42k", "label": "42 km (maratona)", "km": 42.195},
]


def summarize(runs: list[dict], tolerance: float) -> dict:
    """Recebe corridas ja normalizadas (id, name, type, date, year, km, duration_s)
    e devolve o relatorio completo (mesmo formato consumido por build_page.py).
    """
    runs = sorted(runs, key=lambda r: r["date"])

    milestones_out = []
    for m in MILESTONES:
        threshold = m["km"] * (1 - tolerance)
        hits = [r for r in runs if r["km"] >= threshold]
        by_year: dict[int, int] = {}
        for r in hits:
            by_year[r["year"]] = by_year.get(r["year"], 0) + 1
        milestones_out.append(
            {
                "key": m["key"],
                "label": m["label"],
                "km": m["km"],
                "threshold_km": round(threshold, 3),
                "count": len(hits),
                "by_year": dict(sorted(by_year.items())),
                "first_date": hits[0]["date"] if hits else None,
                "last_date": hits[-1]["date"] if hits else None,
                "longest_km": max((r["km"] for r in hits), default=None),
            }
        )

    years = sorted({r["year"] for r in runs})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tolerance": tolerance,
        "total_runs": len(runs),
        "total_km": round(sum(r["km"] for r in runs), 1),
        "years": years,
        "milestones": milestones_out,
        "runs": runs,
    }


def is_running_type(type_key: str | None) -> bool:
    """True para qualquer variante de corrida (running, treadmill_running,
    trail_running, track_running, indoor_running, virtual_run, etc)."""
    if not type_key:
        return False
    return "run" in type_key.lower()


# --- performance (ritmo, cadencia, FC, zonas) ------------------------------
#
# Campos opcionais em cada corrida: "avg_hr" (bpm), "avg_cadence" (passos
# totais/min) e "hr_zones" (lista fixa de 6 posicoes, em segundos:
# [Z0, Z1, Z2, Z3, Z4, "Z5+"], onde Z0 e o tempo abaixo da zona 1 e "Z5+"
# agrupa a zona 5 em diante). Todos None quando a fonte nao tem esse dado
# (ex: corrida sem monitor de FC).

def build_hr_zones(seconds_by_zone: dict[int, float]) -> list[float] | None:
    """Recebe {numero_da_zona: segundos} (zonas 0..N, onde 0 = abaixo da Z1)
    e devolve a lista fixa de 6 posicoes descrita acima, somando zonas acima
    de 5 na ultima posicao. None se o dict for vazio/None."""
    if not seconds_by_zone:
        return None
    zones = [0.0] * 6
    for zone, secs in seconds_by_zone.items():
        try:
            idx = min(max(int(zone), 0), 5)
            zones[idx] += float(secs or 0)
        except (TypeError, ValueError):
            continue
    return [round(z, 1) for z in zones]


# --- cache local usado pelo fetch_garmin.py --------------------------------
#
# O proprio report.json funciona como cache pra atualizacao incremental.

def load_cached_runs(path: str) -> dict[object, dict]:
    """Le um report.json existente (se houver) e devolve {id: run}."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        prev = json.load(f)
    return {r["id"]: r for r in prev.get("runs", [])}


def derive_since(cached_runs: dict[object, dict], overlap_days: int) -> str | None:
    """Deriva uma data de corte (YYYY-MM-DD) a partir da corrida mais recente
    ja em cache, com alguns dias de folga de seguranca — usado para tornar as
    atualizacoes incrementais rapidas sem precisar de --since manual. Devolve
    None se ainda nao ha nada em cache (carga inicial)."""
    if not cached_runs:
        return None
    last_known_date = max(r["date"] for r in cached_runs.values())
    cutoff = date.fromisoformat(last_known_date) - timedelta(days=overlap_days)
    return cutoff.isoformat()
