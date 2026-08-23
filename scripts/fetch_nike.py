#!/usr/bin/env python3
"""
Busca corridas da conta do Nike Run Club (NRC) e atualiza o mesmo
report.json usado pelo fetch_garmin.py / analyze_runs.py — as corridas de
Garmin e Nike ficam somadas no mesmo relatorio de marcos de distancia.

*** AVISO ***
A Nike nao publica uma API oficial para uso de terceiros. Este script fala
com os mesmos endpoints internos que o app/site do NRC usam (descobertos por
engenharia reversa, como fazem varios projetos open source de exportacao de
dados do NRC). A Nike pode mudar esses endpoints a qualquer momento sem
aviso — se o login ou a busca de atividades parar de funcionar, rode com
--dump-raw pra inspecionar a resposta e ajustar o normalize_activity().

Autenticacao (refresh token):
    O login interativo da Nike (com captcha/checagens do navegador) nao da
    pra automatizar de forma confiavel, entao este script usa um
    "refresh_token" que voce mesmo extrai UMA VEZ do seu proprio navegador,
    ja logado em nike.com:

    1. Abra https://www.nike.com e faca login na sua conta.
    2. Abra o DevTools do navegador (F12) -> aba "Rede/Network".
    3. Navegue por alguma pagina autenticada (ex: "Meus pedidos") pra gerar
       trafego, ou va em Application/Armazenamento -> Local Storage ->
       https://www.nike.com e procure uma chave que contenha um JSON com
       "access_token" e "refresh_token" (geralmente sob um nome como
       "nike_unite" ou parecido).
    4. Copie o valor de "refresh_token" (uma string longa) e exporte:

         # PowerShell
         $env:NIKE_REFRESH_TOKEN = "..."
         # Bash
         export NIKE_REFRESH_TOKEN="..."

    O script troca esse refresh_token por um access_token de curta duracao
    a cada execucao e guarda o refresh_token mais recente em --token-dir
    (padrao "~/.garmin_tokens/nike_token.json") pra voce nao precisar repetir
    o processo do navegador toda vez — a Nike costuma rotacionar o refresh
    token a cada uso.

Cache local e atualizacao incremental:
    Igual ao fetch_garmin.py: se --out ja existir, deriva um --since
    automaticamente a partir da corrida do Nike mais recente ja conhecida
    (corridas do Garmin no mesmo arquivo nao entram nessa conta). Use --full
    pra rebuscar tudo.

Uso:
    pip install -r requirements.txt

    python scripts/fetch_nike.py --out report.json
    python scripts/fetch_nike.py --out report.json --html marcos-de-corrida.html
    python scripts/fetch_nike.py --out report.json --full --dump-raw nike_raw.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

from garmin_common import derive_since, load_cached_runs, runs_by_source, summarize

# Client id publico usado pelo site/app do NRC para autenticar contra a API
# da Nike — nao e um segredo (fica embutido no JS publico do nike.com),
# apenas identifica "este e um cliente NRC falando com a API". Se a Nike
# trocar esse valor, sobrescreva com --client-id.
DEFAULT_NIKE_CLIENT_ID = "VmzsCTFwvI5WhIn8FYUpN8oYQnJmVsSt"
TOKEN_REFRESH_URL = "https://unite.nike.com/tokenrefresh"
ACTIVITIES_URL = "https://api.nike.com/sport/v3/me/activities"

DEFAULT_TOKEN_DIR = os.path.join(os.path.expanduser("~"), ".garmin_tokens")


def token_cache_path(token_dir: str) -> str:
    return os.path.join(token_dir, "nike_token.json")


def refresh_access_token(refresh_token: str, client_id: str, debug: bool) -> dict:
    resp = requests.post(
        TOKEN_REFRESH_URL,
        params={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "response_type": "token",
        },
        timeout=20,
    )
    if not resp.ok:
        if debug:
            print(f"[debug] refresh falhou: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
        raise RuntimeError(
            f"Nao foi possivel renovar o token do Nike Run Club (HTTP {resp.status_code}). "
            "O refresh_token pode ter expirado — pegue um novo no navegador (veja o cabecalho "
            "deste script) ou confira se --client-id ainda esta correto."
        )
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Resposta inesperada da Nike ao renovar o token: {json.dumps(data)[:300]}")
    return data


def login(token_dir: str, client_id: str, debug: bool) -> str:
    """Devolve um access_token valido, renovando via refresh_token e
    persistindo o refresh_token mais recente em token_dir."""
    cache_path = token_cache_path(token_dir)
    refresh_token = os.getenv("NIKE_REFRESH_TOKEN")

    if not refresh_token and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            refresh_token = json.load(f).get("refresh_token")

    if not refresh_token:
        if not sys.stdin.isatty():
            print(
                "Sem refresh_token do Nike Run Club. Defina a variavel de ambiente "
                "NIKE_REFRESH_TOKEN (veja instrucoes no topo de fetch_nike.py) e rode de novo.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("Cole abaixo o refresh_token extraido do navegador (veja instrucoes no topo do script).")
        refresh_token = input("NIKE_REFRESH_TOKEN: ").strip()

    tokens = refresh_access_token(refresh_token, client_id, debug)

    os.makedirs(token_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"refresh_token": tokens.get("refresh_token", refresh_token)}, f)

    return tokens["access_token"]


def fetch_page(access_token: str, after_time_ms: int, after_id: str | None, limit: int, debug: bool) -> dict:
    url = f"{ACTIVITIES_URL}/after_id/{after_id}/after_time/{after_time_ms}" if after_id else f"{ACTIVITIES_URL}/after_time/{after_time_ms}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"types": "run", "limit": limit},
        timeout=20,
    )
    if not resp.ok:
        if debug:
            print(f"[debug] busca de atividades falhou: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
        raise RuntimeError(f"Nike Run Club recusou a busca de atividades (HTTP {resp.status_code}).")
    return resp.json()


def _find_metric(summaries: list[dict], metric: str) -> float | None:
    for s in summaries or []:
        if s.get("metric") == metric:
            for key in ("summary", "value"):
                if key in s and s[key] is not None:
                    try:
                        return float(s[key])
                    except (TypeError, ValueError):
                        pass
    return None


def normalize_activity(a: dict) -> dict | None:
    if a.get("type") not in (None, "run") and "run" not in str(a.get("type", "")).lower():
        return None
    if a.get("status") not in (None, "COMPLETE"):
        return None

    start_ms = a.get("start_epoch_ms")
    if start_ms is None:
        return None
    dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)

    km = _find_metric(a.get("summaries") or a.get("metric_summaries"), "distance")
    if km is None:
        km = a.get("distance")
    if km is None:
        return None  # sem distancia, nao da pra classificar em marco nenhum

    duration_ms = a.get("active_duration_ms") or a.get("duration_ms")
    duration_s = round(duration_ms / 1000, 1) if duration_ms else None

    name = ((a.get("tags") or {}).get("com.nike.name")) or a.get("name") or "Corrida (Nike Run Club)"

    return {
        "id": a["id"],
        "name": name,
        "type": "nike_run",
        "date": dt.date().isoformat(),
        "year": dt.year,
        "km": round(float(km), 3),
        "duration_s": duration_s,
        "source": "nike",
    }


def fetch_running_activities(
    access_token: str,
    known_ids: set,
    since: str | None,
    page_size: int,
    max_pages: int,
    full: bool,
    dump_raw: str | None,
    debug: bool,
) -> list[dict]:
    fetched: list[dict] = []
    after_id = None
    after_time_ms = 0

    for page_num in range(max_pages):
        try:
            page = fetch_page(access_token, after_time_ms, after_id, page_size, debug)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            break

        if dump_raw and page_num == 0:
            with open(dump_raw, "w", encoding="utf-8") as f:
                json.dump(page, f, ensure_ascii=False, indent=2)
            print(f"  (raw da 1a pagina salvo em {dump_raw})")

        activities = page.get("activities") or []
        if not activities:
            break

        saw_known = False
        stop = False
        for raw in activities:
            run = normalize_activity(raw)
            if run is None:
                continue
            if since and run["date"] < since:
                stop = True
                continue
            fetched.append(run)
            if run["id"] in known_ids:
                saw_known = True

        print(f"  pagina {page_num + 1}: {len(activities)} atividades ({len(fetched)} corridas ate agora)")

        paging = page.get("paging") or {}
        after_id = paging.get("after_id")
        after_time_ms = paging.get("after_time", after_time_ms)
        if not after_id:
            break
        if stop:
            break
        if saw_known and not full:
            break

        time.sleep(0.3)

    return fetched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="report.json", help="Arquivo JSON de saida (compartilhado com fetch_garmin.py / analyze_runs.py)")
    parser.add_argument("--html", default=None, help="Se informado, tambem gera a pagina HTML nesse caminho")
    parser.add_argument("--tolerance", type=float, default=0.03, help="Margem de tolerancia por marco (0.03 = 3%%)")
    parser.add_argument("--token-dir", default=DEFAULT_TOKEN_DIR, help="Onde guardar o refresh_token (padrao: ~/.garmin_tokens)")
    parser.add_argument("--client-id", default=DEFAULT_NIKE_CLIENT_ID, help="Client id da API da Nike, caso o padrao pare de funcionar")
    parser.add_argument("--since", default=None, help="So busca corridas a partir dessa data (YYYY-MM-DD). Sem isso, deriva do cache automaticamente")
    parser.add_argument("--overlap-days", type=int, default=3, help="Margem de seguranca (dias) ao derivar --since do cache (padrao 3)")
    parser.add_argument("--full", action="store_true", help="Ignora o cache existente e rebusca todo o historico")
    parser.add_argument("--page-size", type=int, default=25, help="Atividades por pagina (padrao 25)")
    parser.add_argument("--max-pages", type=int, default=200, help="Trava de seguranca para o numero de paginas")
    parser.add_argument("--dump-raw", default=None, help="Salva o JSON bruto da 1a pagina nesse caminho (util pra depurar mudancas na API da Nike)")
    parser.add_argument("--debug", action="store_true", help="Mostra detalhes de erros HTTP")
    args = parser.parse_args()

    # all_cached e sempre carregado por inteiro (mesmo com --full) para nunca
    # apagar corridas do Garmin no merge final — --full so forca refazer a
    # busca do Nike do zero (ver comentario equivalente em fetch_garmin.py).
    all_cached = load_cached_runs(args.out)
    nike_cached = {} if args.full else runs_by_source(all_cached, "nike")
    if all_cached:
        print(f"Cache local encontrado: {len(all_cached)} corridas em {args.out} ({len(nike_cached)} do Nike Run Club)")

    since = args.since
    if since is None and nike_cached and not args.full:
        since = derive_since(nike_cached, args.overlap_days)
        print(f"Modo incremental: buscando corridas do Nike a partir de {since} (folga de {args.overlap_days}d)")
    elif args.full:
        print("Carga completa (--full): ignorando cache e rebuscando todo o historico")
    elif not nike_cached:
        print("Sem cache local do Nike Run Club: fazendo carga inicial completa")

    print("Autenticando no Nike Run Club...")
    access_token = login(args.token_dir, args.client_id, args.debug)
    print("Autenticado. Buscando atividades...")

    fetched = fetch_running_activities(
        access_token,
        known_ids=set(nike_cached),
        since=since,
        page_size=args.page_size,
        max_pages=args.max_pages,
        full=args.full,
        dump_raw=args.dump_raw,
        debug=args.debug,
    )

    merged = dict(all_cached)
    for run in fetched:
        merged[run["id"]] = run

    report = summarize(list(merged.values()), args.tolerance)
    report["fetched_at"] = datetime.now(timezone.utc).isoformat()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n{len(fetched)} corridas do Nike novas/atualizadas. Total no relatorio (todas as fontes): {report['total_runs']}")
    print(f"Relatorio salvo em {args.out}")

    if args.html:
        from build_page import build

        with open(args.html, "w", encoding="utf-8") as f:
            f.write(build(report))
        print(f"Pagina gerada em {args.html}")

    print("\nResumo (todas as fontes):")
    for m in report["milestones"]:
        print(f"  >= {m['label']:16s}: {m['count']:4d} vezes")


if __name__ == "__main__":
    main()
