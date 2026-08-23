#!/usr/bin/env python3
"""
Analisa o export do Garmin Connect (pasta data/) e conta quantas vezes
cada distancia "marco" (5, 10, 15, 21 e 42 km) foi atingida ou superada
em corridas, independente da distancia total da corrida.

Uma corrida de 21 km, por exemplo, conta como estatistica para os marcos
5, 10, 15 e 21 km.

Uso:
    python scripts/analyze_runs.py [--data-dir data] [--tolerance 0.03] [--out out.json]

O JSON de saida e pensado para ser consumido por uma pagina web (artifact)
que exibe os resultados.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone

from garmin_common import build_hr_zones, summarize


def find_summarized_activities_files(data_dir: str) -> list[str]:
    pattern = os.path.join(data_dir, "DI_CONNECT", "DI-Connect-Fitness", "*summarizedActivities*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"Nenhum arquivo summarizedActivities encontrado em: {pattern}")
    return files


def load_running_activities(data_dir: str) -> list[dict]:
    activities: list[dict] = []
    for path in find_summarized_activities_files(data_dir):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for block in raw:
            for act in block.get("summarizedActivitiesExport", []):
                if act.get("sportType") == "RUNNING" and act.get("distance") is not None:
                    activities.append(act)
    # Evita duplicatas caso existam varios arquivos exportados com sobreposicao
    dedup = {a["activityId"]: a for a in activities}
    return list(dedup.values())


def to_km(distance_cm_like: float) -> float:
    # O export do Garmin guarda "distance" em centimetros.
    return distance_cm_like / 100_000.0


def build_report(activities: list[dict], tolerance: float) -> dict:
    runs = []
    for a in activities:
        ts_ms = a.get("startTimeLocal") or a.get("beginTimestamp")
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        km = to_km(a["distance"])
        avg_hr = a.get("avgHr")
        # "avgRunCadence" no export e a cadencia de uma perna so (~metade do
        # que se costuma chamar de "cadencia" ao correr); "avgDoubleCadence"
        # ja e o total de passos/min e e o que a pagina mostra.
        avg_cadence = a.get("avgDoubleCadence")
        if avg_cadence is None and a.get("avgRunCadence") is not None:
            avg_cadence = a["avgRunCadence"] * 2

        hr_zones = None
        if avg_hr is not None:
            seconds_by_zone = {
                i: a[f"hrTimeInZone_{i}"] / 1000
                for i in range(7)
                if f"hrTimeInZone_{i}" in a
            }
            hr_zones = build_hr_zones(seconds_by_zone)

        runs.append(
            {
                "id": a["activityId"],
                "name": a.get("name") or "Corrida",
                "type": a.get("activityType"),
                "date": dt.date().isoformat(),
                "year": dt.year,
                "km": round(km, 3),
                "duration_s": round(a["duration"] / 1000, 1) if a.get("duration") else None,
                "source": "garmin",
                "avg_hr": avg_hr,
                "avg_cadence": round(avg_cadence, 1) if avg_cadence is not None else None,
                "hr_zones": hr_zones,
            }
        )
    return summarize(runs, tolerance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", help="Pasta raiz do export do Garmin Connect")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.03,
        help="Margem de tolerancia para baixo em cada marco (0.03 = 3%%, ex: 5km aceita >=4.85km)",
    )
    parser.add_argument("--out", default=None, help="Caminho do arquivo JSON de saida")
    args = parser.parse_args()

    activities = load_running_activities(args.data_dir)
    report = build_report(activities, args.tolerance)

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Relatorio salvo em {args.out}")
    else:
        print(output)

    print("\nResumo:")
    print(f"  Total de corridas: {report['total_runs']}")
    print(f"  Distancia total: {report['total_km']} km")
    for m in report["milestones"]:
        print(f"  >= {m['label']:16s}: {m['count']:4d} vezes")


if __name__ == "__main__":
    main()
