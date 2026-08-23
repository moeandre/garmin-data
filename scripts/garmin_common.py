"""Logica compartilhada entre os scripts de analise (analyze_runs.py, fetch_garmin.py).

Define os marcos de distancia e a funcao que transforma uma lista de corridas
ja normalizadas em um relatorio pronto para a pagina web (build_page.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

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
