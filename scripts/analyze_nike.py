#!/usr/bin/env python3
"""
Le o export de dados do Nike Run Club (pasta nike-data/activities/*.json,
baixado em "Solicitar seus dados" nas configuracoes de privacidade da Nike)
e consolida no mesmo report.json usado por analyze_runs.py / fetch_garmin.py
— as corridas do Garmin e do Nike ficam somadas no mesmo relatorio de marcos
de distancia.

Por que filtrar por app_id:
    Quem sincroniza o relogio Garmin com o Nike Run Club acaba com a MESMA
    corrida duplicada no export da Nike (uma vinda do relogio, salva com
    app_id "com.garmin.garmin"). Importar isso de novo contaria a mesma
    corrida duas vezes. Este script so importa corridas nativas do app da
    Nike (registradas pelo proprio app, sem vir de um relogio Garmin) —
    tipicamente o historico de antes de você ter um Garmin, ou corridas
    registradas so pelo celular. Nos dados desse projeto isso reduziu 718
    atividades pra 148 corridas realmente exclusivas do Nike.

    Como seguranca extra, qualquer corrida "nativa" do Nike cuja data e
    distancia batam (± 300m) com uma corrida ja presente no --out tambem e
    pulada, pro caso raro de voce ter registrado a mesma corrida nos dois
    apps manualmente.

Uso:
    python scripts/analyze_nike.py --out report.json
    python scripts/build_page.py report.json marcos-de-corrida.html
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timedelta, timezone

from garmin_common import load_cached_runs, summarize

# App ids que indicam que a corrida foi sincronizada de um relogio Garmin
# pro Nike Run Club (e portanto ja deve estar no report.json vindo do
# Garmin). Comeca com "com.garmin" cobre variantes tipo "com.garmin.garmin".
GARMIN_SYNCED_PREFIX = "com.garmin"

# A Nike guarda so o horario em UTC (start_epoch_ms), sem timezone. Assume
# horario de Brasilia (UTC-3) pra decidir em que dia a corrida caiu — ajuste
# aqui se voce corre em outro fuso.
LOCAL_UTC_OFFSET_HOURS = -3

# Terreno registrado no app -> mesmo vocabulario de "type" usado pelo Garmin,
# pra aparecer com o rotulo certo (Esteira/Rua/Trilha) na pagina.
TERRAIN_TO_TYPE = {
    "treadmill": "treadmill_running",
    "trail": "trail_running",
    "road": "running",
    "track": "running",
    "amped": "running",
}


def load_nike_activities(nike_dir: str) -> list[dict]:
    pattern = os.path.join(nike_dir, "activities", "*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"Nenhum arquivo de atividade encontrado em: {pattern}")
    activities = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            activities.append(json.load(f))
    return activities


def find_metric(summaries: list[dict], metric: str, summary: str = "total") -> float | None:
    for s in summaries or []:
        if s.get("metric") == metric and s.get("summary") == summary:
            try:
                return float(s["value"])
            except (TypeError, ValueError, KeyError):
                return None
    return None


def normalize_activity(a: dict, skip_garmin_synced: bool = True) -> dict | None:
    if str(a.get("type", "")).lower() != "run":
        return None
    if skip_garmin_synced and str(a.get("app_id") or "").startswith(GARMIN_SYNCED_PREFIX):
        return None  # ja veio (ou deveria vir) do Garmin — evita contar 2x

    start_ms = a.get("start_epoch_ms")
    if start_ms is None:
        return None
    km = find_metric(a.get("summaries"), "distance", "total")
    if km is None or km <= 0:
        return None

    dt_utc = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    dt_local = dt_utc + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)

    duration_ms = a.get("active_duration_ms")
    avg_hr = find_metric(a.get("summaries"), "heart_rate", "mean")
    terrain = str((a.get("tags") or {}).get("terrain") or "").lower()
    name = (a.get("tags") or {}).get("com.nike.name") or "Corrida (Nike Run Club)"

    return {
        "id": a["id"],
        "name": name,
        "type": TERRAIN_TO_TYPE.get(terrain, "running"),
        "date": dt_local.date().isoformat(),
        "year": dt_local.year,
        "km": round(km, 3),
        "duration_s": round(duration_ms / 1000, 1) if duration_ms else None,
        "avg_hr": round(avg_hr, 1) if avg_hr is not None else None,
        "avg_cadence": None,  # o app da Nike nao registra cadencia
        "hr_zones": None,     # nem tempo em zona de FC
    }


def is_duplicate(run: dict, existing_runs: list[dict], tolerance_km: float = 0.3) -> bool:
    for r in existing_runs:
        if r["date"] == run["date"] and abs(r["km"] - run["km"]) < tolerance_km:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nike-dir", default="nike-data", help="Pasta com o export do Nike Run Club (padrao: nike-data)")
    parser.add_argument("--out", default="report.json", help="Arquivo JSON de saida (mesclado com o que ja existir)")
    parser.add_argument("--tolerance", type=float, default=0.03, help="Margem de tolerancia por marco (0.03 = 3%%)")
    parser.add_argument("--include-garmin-synced", action="store_true", help="Nao filtra corridas sincronizadas do Garmin (cuidado: provavel duplicar dados)")
    args = parser.parse_args()

    existing = load_cached_runs(args.out)
    existing_list = list(existing.values())
    print(f"Cache existente: {len(existing_list)} corridas em {args.out}")

    activities = load_nike_activities(args.nike_dir)
    print(f"{len(activities)} atividades encontradas em {args.nike_dir}/activities")

    added = []
    skipped_garmin_synced = 0
    skipped_duplicate = 0
    skipped_incomplete = 0
    for a in activities:
        is_garmin_synced = str(a.get("app_id") or "").startswith(GARMIN_SYNCED_PREFIX)
        if not args.include_garmin_synced and is_garmin_synced:
            skipped_garmin_synced += 1
            continue
        run = normalize_activity(a, skip_garmin_synced=not args.include_garmin_synced)
        if run is None:
            skipped_incomplete += 1
            continue
        if is_duplicate(run, existing_list) or is_duplicate(run, added):
            skipped_duplicate += 1
            continue
        added.append(run)

    print(f"  {len(added)} corridas novas do Nike Run Club")
    print(f"  {skipped_garmin_synced} ignoradas (ja sincronizadas de um relogio Garmin)")
    print(f"  {skipped_duplicate} ignoradas (batem em data+distancia com uma corrida ja existente)")
    print(f"  {skipped_incomplete} ignoradas (sem tipo/distancia valida)")

    merged = dict(existing)
    for run in added:
        merged[run["id"]] = run

    report = summarize(list(merged.values()), args.tolerance)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nTotal consolidado: {report['total_runs']} corridas")
    print(f"Relatorio salvo em {args.out}")

    print("\nResumo:")
    for m in report["milestones"]:
        print(f"  >= {m['label']:16s}: {m['count']:4d} vezes")


if __name__ == "__main__":
    main()
