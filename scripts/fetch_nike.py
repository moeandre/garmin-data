#!/usr/bin/env python3
"""
Busca corridas direto da API do Nike Run Club — mesmos endpoints usados pelo
nrc-exporter (https://github.com/yasoob/nrc-exporter) — e atualiza a pasta
nike-data/activities/ com o JSON completo de cada corrida nova, no mesmo
formato que analyze_nike.py ja sabe consolidar no report.json.

Por que isso existe:
    O export manual da Nike (privacy.nike.com, "Solicitar seus dados") pode
    demorar dias e sair desatualizado assim que chega. Esse script busca
    direto da API, com um access_token colado a mao — a Nike bloqueia login
    automatizado por email/senha via Akamai Bot Manager, entao aqui so tem
    o fluxo de token (igual ao "-t" do nrc-exporter).

Como conseguir o access_token:
    1. Abra https://www.nike.com/ num navegador e faca login.
    2. Abra o DevTools (F12) -> aba Console -> cole e rode:
       JSON.parse(window.localStorage.getItem('oidc.user:https://accounts.nike.com:4fd2d5e7db76e0f85a6bb56721bd51df')).access_token
    3. Cole o valor impresso quando o script pedir (ou exporte
       NIKE_ACCESS_TOKEN antes de rodar, pra nao digitar toda vez). O token
       costuma expirar em ~1h — se o script reclamar de token invalido no
       meio da execucao, gere um novo e rode de novo (ele retoma de onde
       parou: corridas ja salvas em disco nao sao rebaixadas, exceto com
       --full).

    O token nunca e salvo em disco por este script.

Uso:
    pip install -r requirements.txt

    # baixa so as corridas novas pra nike-data/activities/
    python scripts/fetch_nike.py

    # baixa e ja consolida + regera a pagina, tudo numa tacada
    python scripts/fetch_nike.py --out report.json --html marcos-de-corrida.html

    # rebaixa o detalhe de TODAS as corridas da conta, nao so as novas
    python scripts/fetch_nike.py --full
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time

import requests

ACTIVITY_LIST_URL = (
    "https://api.nike.com/plus/v3/activities/before_id/v3/*"
    "?limit=30&types=run%2Cjogging&include_deleted=false"
)
ACTIVITY_LIST_PAGINATION = (
    "https://api.nike.com/plus/v3/activities/before_id/v3/{before_id}"
    "?limit=30&types=run%2Cjogging&include_deleted=false"
)
ACTIVITY_DETAILS_URL = "https://api.nike.com/sport/v3/me/activity/{activity_id}?metrics=ALL"

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def get_access_token() -> str:
    token = os.getenv("NIKE_ACCESS_TOKEN")
    if token:
        return token.strip()
    if not sys.stdin.isatty():
        print(
            "Terminal nao interativo e NIKE_ACCESS_TOKEN nao foi definido. "
            "Veja as instrucoes no topo deste script para extrair o token.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "Cole o access_token do Nike Run Club (instrucoes no topo do "
        "script — DevTools -> Console no nike.com). Nao fica salvo em disco:"
    )
    token = getpass.getpass("Access token: ").strip()
    if len(token) < 5:
        print("Token vazio/invalido.", file=sys.stderr)
        sys.exit(1)
    return token


def _check_response(data: dict, context: str) -> None:
    if isinstance(data, dict) and "error_id" in data:
        print(
            f"Erro da API do Nike ao {context}: {data.get('message') or data['error_id']}\n"
            "Token invalido ou expirado? Gere um novo (instrucoes no topo do script).",
            file=sys.stderr,
        )
        sys.exit(1)


def list_run_ids(token: str, max_pages: int) -> list[str]:
    """Percorre a listagem de atividades (mais recente primeiro) e devolve
    os ids de todas as corridas ('type' == 'run') da conta."""
    headers = {"Authorization": f"Bearer {token}"}
    ids: list[str] = []
    next_url = ACTIVITY_LIST_URL
    for page_num in range(1, max_pages + 1):
        resp = requests.get(next_url, headers=headers, timeout=30)
        data = resp.json()
        _check_response(data, "listar atividades")
        activities = data.get("activities", [])
        ids.extend(a["id"] for a in activities if a.get("type") == "run")
        print(f"  pagina {page_num}: {len(activities)} atividades ({len(ids)} corridas ate agora)")

        before_id = (data.get("paging") or {}).get("before_id")
        if not before_id or not activities:
            break
        next_url = ACTIVITY_LIST_PAGINATION.format(before_id=before_id)
        time.sleep(0.3)
    return ids


def fetch_activity_detail(activity_id: str, token: str) -> dict | None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(ACTIVITY_DETAILS_URL.format(activity_id=activity_id), headers=headers, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        print(f"  falha ao ler detalhe de {activity_id}: resposta nao-JSON (status {resp.status_code})", file=sys.stderr)
        return None
    _check_response(data, f"buscar detalhe de {activity_id}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--activities-dir", default=os.path.join("nike-data", "activities"),
        help="Pasta onde salvar o JSON de cada corrida (padrao: nike-data/activities)",
    )
    parser.add_argument("--out", default=None, help="Se informado, roda analyze_nike.py e consolida no report.json depois de baixar")
    parser.add_argument("--html", default=None, help="Se informado (junto com --out), tambem regera a pagina HTML via build_page.py")
    parser.add_argument("--tolerance", type=float, default=0.03, help="Margem de tolerancia por marco (0.03 = 3%%), repassada pro analyze_nike.py")
    parser.add_argument("--full", action="store_true", help="Rebaixa o detalhe de TODAS as corridas da lista, nao so as que ainda nao temos salvas em disco")
    parser.add_argument("--max-pages", type=int, default=200, help="Trava de seguranca para paginas da listagem")
    parser.add_argument("--sleep", type=float, default=0.3, help="Pausa (s) entre chamadas de detalhe, pra nao apanhar rate limit")
    args = parser.parse_args()

    os.makedirs(args.activities_dir, exist_ok=True)
    known_ids = {os.path.splitext(f)[0] for f in os.listdir(args.activities_dir) if f.endswith(".json")}
    print(f"{len(known_ids)} corridas ja salvas em {args.activities_dir}")

    token = get_access_token()

    print("Listando corridas na API do Nike Run Club...")
    run_ids = list_run_ids(token, args.max_pages)
    print(f"{len(run_ids)} corridas do tipo 'run' na conta (API)")

    to_fetch = run_ids if args.full else [i for i in run_ids if i not in known_ids]
    already = len(run_ids) - len(to_fetch)
    print(f"{len(to_fetch)} corridas para baixar o detalhe completo ({already} ja em cache local)")

    fetched = 0
    for i, activity_id in enumerate(to_fetch, 1):
        print(f"  [{i}/{len(to_fetch)}] {activity_id}")
        detail = fetch_activity_detail(activity_id, token)
        if detail is not None:
            path = os.path.join(args.activities_dir, f"{activity_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(detail, f, ensure_ascii=False)
            fetched += 1
        time.sleep(args.sleep)

    removed_locally = known_ids - set(run_ids)
    print(f"\n{fetched} corridas baixadas/atualizadas em {args.activities_dir}")
    if removed_locally:
        print(
            f"Aviso: {len(removed_locally)} corrida(s) salva(s) localmente nao aparecem mais na "
            "listagem da API (podem ter sido apagadas no app) — nao foram removidas do disco automaticamente."
        )

    if args.out:
        nike_dir = os.path.dirname(os.path.normpath(args.activities_dir)) or "."
        print(f"\nConsolidando {nike_dir} em {args.out} via analyze_nike.py...")
        subprocess.run(
            [
                sys.executable, os.path.join(SCRIPTS_DIR, "analyze_nike.py"),
                "--nike-dir", nike_dir,
                "--out", args.out,
                "--tolerance", str(args.tolerance),
            ],
            check=True,
        )
        if args.html:
            print(f"\nGerando {args.html} via build_page.py...")
            subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "build_page.py"), args.out, args.html],
                check=True,
            )


if __name__ == "__main__":
    main()
