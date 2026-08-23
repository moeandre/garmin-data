#!/usr/bin/env python3
"""
Busca corridas direto da conta do Garmin Connect (sem precisar exportar o
GDPR/"Data Export" manualmente) e atualiza o relatorio de marcos de distancia.

Autenticacao:
    Defina as variaveis de ambiente GARMIN_EMAIL e GARMIN_PASSWORD antes de
    rodar, ou deixe em branco para digitar interativamente (a senha nao fica
    visivel no terminal). O script nunca grava a senha em disco.

    Depois do primeiro login com sucesso, a sessao fica em cache local (pasta
    --token-dir, por padrao "~/.garmin_tokens") e as proximas execucoes nao
    pedem email/senha de novo — apenas quando a sessao expira ou e revogada.
    Se sua conta usa verificacao em duas etapas (MFA), o codigo e pedido na
    hora.

Cache local e atualizacao incremental:
    O proprio --out (report.json) funciona como cache: se ele ja existir, o
    script deriva um --since automaticamente a partir da corrida mais recente
    ja conhecida (com alguns dias de folga, --overlap-days) e busca so o que
    e novo, fazendo merge com o que ja tinha — sem precisar passar --since a
    mao toda vez. Isso deixa as execucoes de rotina rapidas mesmo com anos de
    historico.

    Carga inicial — duas formas:
      1) A partir do export manual do Garmin Connect (pasta data/), que e
         instantaneo: `python scripts/analyze_runs.py --out report.json`.
         As execucoes seguintes de fetch_garmin.py usam esse report.json como
         cache e so completam com o que faltar.
      2) Direto pela API, sem export nenhum: `python scripts/fetch_garmin.py
         --out report.json --full` (paginar anos de historico pode demorar
         um pouco e esbarrar em rate limit — prefira a opcao 1 se ja tiver o
         export em maos).

Uso:
    pip install -r requirements.txt

    python scripts/fetch_garmin.py --out report.json                          # incremental (usa o cache)
    python scripts/fetch_garmin.py --out report.json --html marcos-de-corrida.html
    python scripts/fetch_garmin.py --out report.json --full                   # carga inicial completa via API
    python scripts/fetch_garmin.py --out report.json --since 2026-01-01       # forca uma data de corte
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime, timezone

from garmin_common import build_hr_zones, derive_since, is_running_type, load_cached_runs, summarize

try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    print(
        "Faltou instalar a biblioteca do Garmin Connect. Rode:\n"
        "  pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_TOKEN_DIR = os.path.join(os.path.expanduser("~"), ".garmin_tokens")


def prompt_mfa() -> str:
    return input("Codigo de verificacao em duas etapas (MFA): ").strip()


def login(token_dir: str) -> Garmin:
    """Tenta reaproveitar a sessao salva; se nao der, pede email/senha."""
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        client.login(tokenstore=token_dir)
        return client
    except GarminConnectAuthenticationError:
        pass  # sessao ausente/expirada — cai para o login com credenciais abaixo

    if not email:
        if not sys.stdin.isatty():
            print(
                "Sessao expirada e o terminal nao e interativo. Defina "
                "GARMIN_EMAIL e GARMIN_PASSWORD como variaveis de ambiente "
                "e rode de novo.",
                file=sys.stderr,
            )
            sys.exit(1)
        email = input("Email do Garmin Connect: ").strip()
    if not password:
        if not sys.stdin.isatty():
            print(
                "Sessao expirada e o terminal nao e interativo. Defina "
                "GARMIN_EMAIL e GARMIN_PASSWORD como variaveis de ambiente "
                "e rode de novo.",
                file=sys.stderr,
            )
            sys.exit(1)
        password = getpass.getpass("Senha do Garmin Connect: ")

    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    client.login(tokenstore=token_dir)  # salva a sessao em token_dir para o proximo run
    return client


def parse_activity_datetime(raw: str) -> datetime:
    # A API devolve "YYYY-MM-DD HH:MM:SS" (hora local, sem timezone).
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")


def normalize_activity(a: dict) -> dict | None:
    type_key = (a.get("activityType") or {}).get("typeKey")
    if not is_running_type(type_key):
        return None
    distance_m = a.get("distance")
    if distance_m is None:
        return None
    raw_dt = a.get("startTimeLocal") or a.get("startTimeGMT")
    if not raw_dt:
        return None
    dt = parse_activity_datetime(raw_dt)
    duration_s = a.get("duration")
    return {
        "id": a["activityId"],
        "name": a.get("activityName") or "Corrida",
        "type": type_key,
        "date": dt.date().isoformat(),
        "year": dt.year,
        "km": round(distance_m / 1000, 3),
        "duration_s": round(duration_s, 1) if duration_s is not None else None,
        "avg_hr": a.get("averageHR"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute"),
        "hr_zones": None,  # preenchido a parte (ver fetch_hr_zones), exige 1 chamada por atividade
    }


def fetch_hr_zones(client: Garmin, activity_id, debug: bool) -> list[float] | None:
    """Busca o tempo em cada zona de FC pra 1 atividade (endpoint separado da
    lista). Best-effort: corrida sem monitor de FC, dispositivo antigo, ou
    qualquer erro da API resulta em None sem interromper o fetch."""
    try:
        raw = client.get_activity_hr_in_timezones(activity_id)
    except Exception as e:  # noqa: BLE001 - endpoint nao documentado, formato pode variar
        if debug:
            print(f"[debug] hrTimeInZones falhou pra atividade {activity_id}: {e}", file=sys.stderr)
        return None
    if not raw:
        return None
    seconds_by_zone: dict[int, float] = {}
    for z in raw:
        zone_num = z.get("zoneNumber", z.get("zone"))
        secs = z.get("secsInZone", z.get("secondsInZone", z.get("timeInZone")))
        if zone_num is None or secs is None:
            continue
        try:
            seconds_by_zone[int(zone_num)] = float(secs)
        except (TypeError, ValueError):
            continue
    return build_hr_zones(seconds_by_zone)


def fetch_running_activities(
    client: Garmin,
    known_ids: set,
    since: str | None,
    page_size: int,
    max_pages: int,
    full: bool,
    fetch_zones: bool = True,
    debug: bool = False,
) -> list[dict]:
    fetched = []
    start = 0
    for page_num in range(max_pages):
        try:
            page = client.get_activities(start=start, limit=page_size)
        except GarminConnectTooManyRequestsError:
            print("Muitas requisicoes seguidas — aguardando 60s antes de tentar de novo...", file=sys.stderr)
            time.sleep(60)
            try:
                page = client.get_activities(start=start, limit=page_size)
            except GarminConnectTooManyRequestsError:
                print("Garmin Connect continua limitando as requisicoes. Tente novamente mais tarde.", file=sys.stderr)
                break

        if not page:
            break

        saw_known = False
        stop = False
        for raw in page:
            run = normalize_activity(raw)
            if run is None:
                continue
            if since and run["date"] < since:
                stop = True
                continue
            if fetch_zones and run.get("avg_hr") is not None:
                run["hr_zones"] = fetch_hr_zones(client, run["id"], debug)
            fetched.append(run)
            if run["id"] in known_ids:
                saw_known = True

        print(f"  pagina {page_num + 1}: {len(page)} atividades ({len(fetched)} corridas ate agora)")

        if len(page) < page_size:
            break  # ultima pagina
        if stop:
            break  # passou do --since
        if saw_known and not full:
            break  # ja alcancou o que tinhamos em cache — resto ja e conhecido

        start += page_size
        time.sleep(0.3)  # educado com a API

    return fetched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="report.json", help="Arquivo JSON de saida (tambem usado como cache para update incremental)")
    parser.add_argument("--html", default=None, help="Se informado, tambem gera a pagina HTML nesse caminho")
    parser.add_argument("--tolerance", type=float, default=0.03, help="Margem de tolerancia por marco (0.03 = 3%%)")
    parser.add_argument("--token-dir", default=DEFAULT_TOKEN_DIR, help="Onde guardar a sessao logada (padrao: ~/.garmin_tokens)")
    parser.add_argument("--since", default=None, help="So busca corridas a partir dessa data (YYYY-MM-DD). Sem isso, deriva do cache automaticamente")
    parser.add_argument("--overlap-days", type=int, default=3, help="Margem de seguranca (em dias) ao derivar --since do cache, para nao perder corrida lancada com atraso (padrao 3)")
    parser.add_argument("--full", action="store_true", help="Ignora o cache existente e rebusca todo o historico (carga inicial via API)")
    parser.add_argument("--page-size", type=int, default=100, help="Atividades por pagina (padrao 100)")
    parser.add_argument("--max-pages", type=int, default=200, help="Trava de seguranca para o numero de paginas")
    parser.add_argument("--skip-hr-zones", action="store_true", help="Nao busca o tempo em cada zona de FC (1 chamada extra por corrida nova) — mais rapido, mas sem dado pra secao Performance da pagina")
    parser.add_argument("--debug", action="store_true", help="Mostra detalhes de erros ao buscar zonas de FC")
    args = parser.parse_args()

    cached = {} if args.full else load_cached_runs(args.out)
    if cached:
        print(f"Cache local encontrado: {len(cached)} corridas em {args.out}")

    since = args.since
    if since is None and cached and not args.full:
        since = derive_since(cached, args.overlap_days)
        print(f"Modo incremental: buscando corridas a partir de {since} (folga de {args.overlap_days}d)")
    elif args.full:
        print("Carga completa (--full): ignorando cache e rebuscando todo o historico")
    elif not cached:
        print("Sem cache local: fazendo carga inicial completa via API")

    print("Entrando no Garmin Connect...")
    client = login(args.token_dir)
    print("Login ok. Buscando atividades...")

    fetched = fetch_running_activities(
        client,
        known_ids=set(cached),
        since=since,
        page_size=args.page_size,
        max_pages=args.max_pages,
        full=args.full,
        fetch_zones=not args.skip_hr_zones,
        debug=args.debug,
    )

    merged = dict(cached)
    for run in fetched:
        merged[run["id"]] = run  # dados novos sobrescrevem (ex: corrida renomeada)

    report = summarize(list(merged.values()), args.tolerance)
    report["source"] = "garmin_connect_api"
    report["fetched_at"] = datetime.now(timezone.utc).isoformat()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n{len(fetched)} corridas novas/atualizadas. Total no relatorio: {report['total_runs']}")
    print(f"Relatorio salvo em {args.out}")

    if args.html:
        from build_page import build

        with open(args.html, "w", encoding="utf-8") as f:
            f.write(build(report))
        print(f"Pagina gerada em {args.html}")

    print("\nResumo:")
    for m in report["milestones"]:
        print(f"  >= {m['label']:16s}: {m['count']:4d} vezes")


if __name__ == "__main__":
    main()
