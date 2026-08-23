#!/usr/bin/env python3
"""Gera a pagina web (artifact) a partir do report.json produzido por analyze_runs.py.

Uso:
    python scripts/analyze_runs.py --out /tmp/report.json
    python scripts/build_page.py /tmp/report.json out.html
"""
from __future__ import annotations

import html
import json
import sys

RAMP_LIGHT = ["#7CB98F", "#5FA475", "#478A5D", "#2C6F46", "#154F30"]  # 5k -> 42k
RAMP_DARK = ["#2E7D4F", "#3E9761", "#57B378", "#7FCB93", "#ADE0B8"]  # 5k -> 42k

TYPE_LABELS = {
    "running": "Rua",
    "treadmill_running": "Esteira",
    "trail_running": "Trilha",
    "street_running": "Rua",
}


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def fmt_km(km: float) -> str:
    return f"{km:.2f}".replace(".", ",")


def fmt_pace(km: float, duration_s: float | None) -> str:
    if not duration_s or km <= 0:
        return "—"
    pace_min_per_km = (duration_s / 60) / km
    m = int(pace_min_per_km)
    s = int(round((pace_min_per_km - m) * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}/km"


def fmt_duration(duration_s: float | None) -> str:
    if not duration_s:
        return "—"
    total = int(round(duration_s))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}min"


def fmt_date_br(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def mini_chart_svg(by_year: dict, years: list[int], color: str) -> str:
    w, h = 216, 56
    n = len(years)
    gap = 3
    bar_w = (w - gap * (n - 1)) / n
    vals = [by_year.get(str(y), 0) for y in years]
    vmax = max(vals) or 1
    bars = []
    for i, (y, v) in enumerate(zip(years, vals)):
        x = i * (bar_w + gap)
        bh = 4 if v == 0 else max(4, (v / vmax) * (h - 4))
        y0 = h - bh
        radius = min(3, bar_w / 2)
        opacity = "0.22" if v == 0 else "1"
        bars.append(
            f'<rect class="bar" x="{x:.2f}" y="{y0:.2f}" width="{bar_w:.2f}" height="{bh:.2f}" '
            f'rx="{radius:.1f}" fill="{color}" opacity="{opacity}" '
            f'data-year="{y}" data-count="{v}"></rect>'
        )
    return f'<svg class="mini-chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">{"".join(bars)}</svg>'


def milestone_card(m: dict, color: str, years: list[int]) -> str:
    chart = mini_chart_svg(m["by_year"], years, color)
    span = ""
    if m["first_date"] and m["last_date"]:
        span = f'{fmt_date_br(m["first_date"])} — {fmt_date_br(m["last_date"])}'
    return f'''
<article class="milestone" style="--mc: {color}">
  <div class="milestone-top">
    <span class="milestone-dist">{esc(m["label"])}</span>
    <span class="milestone-threshold">a partir de {fmt_km(m["threshold_km"])} km</span>
  </div>
  <div class="milestone-count">{m["count"]}</div>
  <div class="milestone-sub">{"vez" if m["count"] == 1 else "vezes"}</div>
  {chart}
  <div class="milestone-span">{esc(span) if span else "ainda sem registros"}</div>
</article>'''


def build(report: dict) -> str:
    years = [int(y) for y in report["years"]]
    milestones = report["milestones"]
    colors_light = dict(zip([m["key"] for m in milestones], RAMP_LIGHT))
    colors_dark = dict(zip([m["key"] for m in milestones], RAMP_DARK))

    # cards render with light-ramp colors inline; dark variant swapped via CSS var per key using data attr + CSS
    cards_html = "\n".join(
        milestone_card(m, colors_light[m["key"]], years) for m in milestones
    )

    # per-card CSS custom property overrides for dark mode, keyed by nth-child
    dark_overrides = "\n".join(
        f'  :root:not([data-theme="light"]) .milestone:nth-of-type({i+1}) {{ --mc: {colors_dark[m["key"]]}; }}\n'
        f'  :root[data-theme="dark"] .milestone:nth-of-type({i+1}) {{ --mc: {colors_dark[m["key"]]}; }}'
        for i, m in enumerate(milestones)
    )

    total_runs = report["total_runs"]
    total_km = report["total_km"]
    year_min, year_max = years[0], years[-1]

    # Highlights
    runs = report["runs"]
    longest = max(runs, key=lambda r: r["km"])
    marathons = [r for r in runs if r["km"] >= milestones[-1]["threshold_km"]]
    by_year_totals: dict[int, int] = {}
    for r in runs:
        by_year_totals[r["year"]] = by_year_totals.get(r["year"], 0) + 1
    best_year = max(by_year_totals.items(), key=lambda kv: kv[1])

    marathon_badges = "\n".join(
        f'<li class="badge-item"><span class="badge-dot"></span>'
        f'<span class="badge-date">{fmt_date_br(r["date"])}</span>'
        f'<span class="badge-name">{esc(r["name"])}</span>'
        f'<span class="badge-km">{fmt_km(r["km"])} km</span></li>'
        for r in sorted(marathons, key=lambda r: r["date"])
    ) or '<li class="badge-item badge-empty">Nenhuma maratona completa ainda — a próxima entra pra lista.</li>'

    # Table rows + embedded JSON for JS filtering/sorting
    runs_for_js = [
        {
            "id": r["id"],
            "name": r["name"],
            "type": TYPE_LABELS.get(r["type"], r["type"] or "Corrida"),
            "date": r["date"],
            "km": r["km"],
            "duration_s": r.get("duration_s"),
        }
        for r in sorted(runs, key=lambda r: r["date"], reverse=True)
    ]
    runs_json = json.dumps(runs_for_js, ensure_ascii=False).replace("</", "<\\/")
    milestones_json = json.dumps(
        [{"key": m["key"], "label": m["label"], "threshold_km": m["threshold_km"]} for m in milestones],
        ensure_ascii=False,
    ).replace("</", "<\\/")

    year_options = "\n".join(f'<option value="{y}">{y}</option>' for y in years)

    return TEMPLATE.format(
        cards_html=cards_html,
        dark_overrides=dark_overrides,
        total_runs=total_runs,
        total_km=f"{total_km:,.0f}".replace(",", "."),
        year_min=year_min,
        year_max=year_max,
        longest_km=fmt_km(longest["km"]),
        longest_name=esc(longest["name"]),
        longest_date=fmt_date_br(longest["date"]),
        marathon_badges=marathon_badges,
        marathon_count=len(marathons),
        best_year=best_year[0],
        best_year_count=best_year[1],
        year_options=year_options,
        runs_json=runs_json,
        milestones_json=milestones_json,
        row_count=len(runs_for_js),
    )


TEMPLATE = r'''<meta charset="utf-8">
<title>Marcos de Corrida</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Work+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #F1F3EC;
    --surface: #FCFCFB;
    --surface-2: #E7EBDF;
    --ink: #16201A;
    --ink-muted: #57614F;
    --line: #DCE2D2;
    --accent: #2C6F46;
    --accent-ink: #FFFFFF;
    --gold: #A9782A;
    --gold-soft: #F1E4C9;
    --shadow: 0 1px 2px rgba(22,32,26,0.06), 0 8px 24px -12px rgba(22,32,26,0.18);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #12160D;
      --surface: #191F14;
      --surface-2: #212B19;
      --ink: #E7EDE1;
      --ink-muted: #A6B29B;
      --line: #2A3423;
      --accent: #7FCB93;
      --accent-ink: #0E1A11;
      --gold: #D6A34C;
      --gold-soft: #3A3018;
      --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 12px 32px -16px rgba(0,0,0,0.6);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #12160D;
    --surface: #191F14;
    --surface-2: #212B19;
    --ink: #E7EDE1;
    --ink-muted: #A6B29B;
    --line: #2A3423;
    --accent: #7FCB93;
    --accent-ink: #0E1A11;
    --gold: #D6A34C;
    --gold-soft: #3A3018;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 12px 32px -16px rgba(0,0,0,0.6);
  }}
{dark_overrides}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "Work Sans", ui-sans-serif, system-ui, sans-serif;
    font-size: 15px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 48px 24px 80px; }}
  a {{ color: var(--accent); }}

  .eyebrow {{
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--ink-muted);
    margin: 0 0 10px;
  }}
  h1 {{
    font-family: "Bebas Neue", "Arial Narrow", sans-serif;
    font-weight: 400;
    font-size: clamp(40px, 7vw, 64px);
    letter-spacing: 0.01em;
    margin: 0 0 6px;
    text-wrap: balance;
  }}
  .lede {{
    max-width: 62ch;
    color: var(--ink-muted);
    font-size: 16px;
    margin: 0 0 28px;
  }}
  .lede strong {{ color: var(--ink); font-weight: 600; }}

  .stat-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 28px;
    padding: 20px 0 8px;
    border-top: 1px solid var(--line);
  }}
  .stat {{ min-width: 120px; }}
  .stat-num {{
    font-family: "JetBrains Mono", monospace;
    font-variant-numeric: tabular-nums;
    font-size: 26px;
    font-weight: 600;
  }}
  .stat-label {{
    font-size: 12.5px;
    color: var(--ink-muted);
  }}

  section {{ margin-top: 56px; }}
  h2 {{
    font-family: "Bebas Neue", "Arial Narrow", sans-serif;
    font-weight: 400;
    letter-spacing: 0.01em;
    font-size: 26px;
    margin: 0 0 4px;
  }}
  .section-sub {{
    color: var(--ink-muted);
    font-size: 14px;
    margin: 0 0 20px;
    max-width: 68ch;
  }}

  .milestones {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 14px;
  }}
  @media (max-width: 920px) {{ .milestones {{ grid-template-columns: repeat(2, 1fr); }} }}
  @media (max-width: 520px) {{ .milestones {{ grid-template-columns: 1fr; }} }}

  .milestone {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 18px 18px 16px;
    box-shadow: var(--shadow);
    position: relative;
    overflow: hidden;
  }}
  .milestone::before {{
    content: "";
    position: absolute;
    inset: 0 0 auto 0;
    height: 4px;
    background: var(--mc);
  }}
  .milestone-top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 2px;
  }}
  .milestone-dist {{
    font-weight: 600;
    font-size: 14px;
  }}
  .milestone-threshold {{
    font-family: "JetBrains Mono", monospace;
    font-size: 10.5px;
    color: var(--ink-muted);
    white-space: nowrap;
  }}
  .milestone-count {{
    font-family: "Bebas Neue", sans-serif;
    font-size: 56px;
    line-height: 1;
    color: var(--mc);
    margin-top: 6px;
  }}
  .milestone-sub {{
    font-size: 12px;
    color: var(--ink-muted);
    margin-bottom: 10px;
  }}
  .mini-chart {{
    display: block;
    width: 100%;
    height: 44px;
  }}
  .mini-chart .bar {{ cursor: default; transition: opacity .15s; }}
  .mini-chart .bar:hover {{ opacity: 0.75 !important; }}
  .milestone-span {{
    font-family: "JetBrains Mono", monospace;
    font-size: 10.5px;
    color: var(--ink-muted);
    margin-top: 8px;
  }}

  .highlights {{
    display: grid;
    grid-template-columns: 1.1fr 1.4fr;
    gap: 14px;
  }}
  @media (max-width: 760px) {{ .highlights {{ grid-template-columns: 1fr; }} }}
  .h-card {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 20px;
    box-shadow: var(--shadow);
  }}
  .h-card h3 {{
    font-size: 12.5px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--ink-muted);
    margin: 0 0 12px;
    font-weight: 600;
  }}
  .h-longest {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }}
  .h-longest .num {{
    font-family: "Bebas Neue", sans-serif;
    font-size: 48px;
    color: var(--gold);
    line-height: 1;
  }}
  .h-longest .meta {{ color: var(--ink-muted); font-size: 13.5px; }}
  .h-longest .meta strong {{ color: var(--ink); font-weight: 600; }}
  .h-extra {{ margin-top: 14px; font-size: 13px; color: var(--ink-muted); }}
  .h-extra strong {{ color: var(--ink); font-weight: 600; font-family: "JetBrains Mono", monospace; }}

  .badge-list {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }}
  .badge-item {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 12px;
    background: var(--gold-soft);
    border-radius: 9px;
    font-size: 13.5px;
  }}
  .badge-dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--gold); flex: none;
  }}
  .badge-date {{ font-family: "JetBrains Mono", monospace; font-size: 12.5px; color: var(--ink-muted); }}
  .badge-name {{ flex: 1; font-weight: 500; }}
  .badge-km {{ font-family: "JetBrains Mono", monospace; font-weight: 600; color: var(--gold); }}
  .badge-empty {{ background: var(--surface-2); color: var(--ink-muted); }}

  .controls {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 14px;
  }}
  .chip-group {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .chip {{
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    padding: 6px 12px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--ink-muted);
    cursor: pointer;
  }}
  .chip:hover {{ border-color: var(--accent); color: var(--ink); }}
  .chip[aria-pressed="true"] {{
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-ink);
    font-weight: 600;
  }}
  select {{
    font-family: "JetBrains Mono", monospace;
    font-size: 12.5px;
    padding: 6px 10px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--ink);
  }}
  .row-count {{
    margin-left: auto;
    font-size: 12.5px;
    color: var(--ink-muted);
    font-family: "JetBrains Mono", monospace;
  }}

  .table-scroll {{
    max-height: 560px;
    overflow: auto;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
    box-shadow: var(--shadow);
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
  thead th {{
    position: sticky; top: 0;
    background: var(--surface-2);
    text-align: left;
    padding: 10px 14px;
    font-size: 11.5px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--ink-muted);
    font-weight: 600;
    cursor: pointer;
    user-select: none;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
  }}
  thead th.sorted {{ color: var(--accent); }}
  thead th .arrow {{ display: inline-block; width: 10px; opacity: 0.7; }}
  tbody td {{
    padding: 8px 14px;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
  }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover {{ background: var(--surface-2); }}
  td.num, th.num {{ font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums; text-align: right; }}
  td.name {{ white-space: normal; max-width: 320px; }}
  .type-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 10.5px;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--ink-muted);
  }}

  .tooltip {{
    position: fixed;
    pointer-events: none;
    background: var(--ink);
    color: var(--bg);
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    padding: 5px 9px;
    border-radius: 6px;
    transform: translate(-50%, -120%);
    opacity: 0;
    transition: opacity .1s;
    z-index: 50;
    white-space: nowrap;
  }}
  .tooltip.show {{ opacity: 1; }}

  footer {{
    margin-top: 64px;
    padding-top: 20px;
    border-top: 1px solid var(--line);
    color: var(--ink-muted);
    font-size: 12.5px;
    font-family: "JetBrains Mono", monospace;
  }}

  @media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; }}
  }}
</style>

<div class="wrap">
  <p class="eyebrow">Garmin Connect · Histórico de corridas</p>
  <h1>Marcos de Corrida</h1>
  <p class="lede">
    Toda corrida <strong>de {year_min} a {year_max}</strong> passa pelos marcos de 5, 10, 15, 21 e 42&nbsp;km —
    uma corrida de 21&nbsp;km conta ponto pra estatística de 5, 10, 15 <strong>e</strong> 21&nbsp;km.
    Os limiares abaixo têm 3% de folga pra cobrir imprecisão de GPS.
  </p>
  <div class="stat-row">
    <div class="stat"><div class="stat-num">{total_runs}</div><div class="stat-label">corridas registradas</div></div>
    <div class="stat"><div class="stat-num">{total_km} km</div><div class="stat-label">distância total</div></div>
    <div class="stat"><div class="stat-num">{year_min}–{year_max}</div><div class="stat-label">período</div></div>
  </div>

  <section>
    <h2>Marcos atingidos</h2>
    <p class="section-sub">Quantidade de corridas que alcançaram (ou superaram) cada distância, com a evolução ano a ano.</p>
    <div class="milestones">
      {cards_html}
    </div>
  </section>

  <section>
    <h2>Destaques</h2>
    <div class="highlights">
      <div class="h-card">
        <h3>Corrida mais longa</h3>
        <div class="h-longest">
          <div class="num">{longest_km}</div>
          <div class="meta">km · <strong>{longest_name}</strong><br>{longest_date}</div>
        </div>
        <div class="h-extra">Ano com mais corridas: <strong>{best_year}</strong> ({best_year_count} corridas)</div>
      </div>
      <div class="h-card">
        <h3>Maratonas completas ({marathon_count})</h3>
        <ul class="badge-list">
          {marathon_badges}
        </ul>
      </div>
    </div>
  </section>

  <section>
    <h2>Todas as corridas</h2>
    <p class="section-sub">Filtre por ano ou por marco mínimo de distância. Clique nos cabeçalhos pra ordenar.</p>
    <div class="controls">
      <div class="chip-group" id="milestoneChips">
        <button class="chip" data-min="0" aria-pressed="true">Todas</button>
        <button class="chip" data-min="4.85">≥ 5 km</button>
        <button class="chip" data-min="9.7">≥ 10 km</button>
        <button class="chip" data-min="14.55">≥ 15 km</button>
        <button class="chip" data-min="20.465">≥ 21 km</button>
        <button class="chip" data-min="40.929">≥ 42 km</button>
      </div>
      <select id="yearFilter">
        <option value="">Todos os anos</option>
        {year_options}
      </select>
      <span class="row-count" id="rowCount"></span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th data-key="date" class="sorted">Data <span class="arrow">↓</span></th>
            <th data-key="name">Corrida</th>
            <th data-key="type">Tipo</th>
            <th data-key="km" class="num">Distância <span class="arrow"></span></th>
            <th data-key="pace" class="num">Ritmo <span class="arrow"></span></th>
            <th data-key="duration" class="num">Duração <span class="arrow"></span></th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </section>

  <footer>Gerado a partir do export do Garmin Connect · {row_count} corridas de rua no total</footer>
</div>

<div class="tooltip" id="tooltip"></div>

<script id="runsData" type="application/json">{runs_json}</script>
<script>
(function() {{
  const runs = JSON.parse(document.getElementById('runsData').textContent);
  const tbody = document.getElementById('tbody');
  const rowCount = document.getElementById('rowCount');
  const tooltip = document.getElementById('tooltip');
  const yearFilter = document.getElementById('yearFilter');
  const chips = document.querySelectorAll('#milestoneChips .chip');
  const ths = document.querySelectorAll('thead th[data-key]');

  let minKm = 0;
  let year = '';
  let sortKey = 'date';
  let sortDir = -1;

  function pace(km, s) {{
    if (!s || km <= 0) return null;
    return (s / 60) / km;
  }}
  function fmtPace(p) {{
    if (p == null) return '—';
    let m = Math.floor(p), s = Math.round((p - m) * 60);
    if (s === 60) {{ m += 1; s = 0; }}
    return m + ':' + String(s).padStart(2, '0') + '/km';
  }}
  function fmtDuration(s) {{
    if (!s) return '—';
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? (h + 'h' + String(m).padStart(2, '0') + 'm') : (m + 'min');
  }}
  function fmtDateBR(iso) {{
    const [y, m, d] = iso.split('-');
    return d + '/' + m + '/' + y;
  }}
  function fmtKm(km) {{
    return km.toFixed(2).replace('.', ',');
  }}

  function render() {{
    let rows = runs.filter(r => r.km >= minKm && (!year || r.date.slice(0,4) === year));
    rows = rows.slice().sort((a, b) => {{
      let av, bv;
      if (sortKey === 'pace') {{ av = pace(a.km, a.duration_s) ?? Infinity; bv = pace(b.km, b.duration_s) ?? Infinity; }}
      else if (sortKey === 'duration') {{ av = a.duration_s ?? -1; bv = b.duration_s ?? -1; }}
      else {{ av = a[sortKey]; bv = b[sortKey]; }}
      if (av < bv) return -1 * sortDir;
      if (av > bv) return 1 * sortDir;
      return 0;
    }});
    rowCount.textContent = rows.length + (rows.length === 1 ? ' corrida' : ' corridas');
    tbody.innerHTML = rows.map(r => {{
      const p = pace(r.km, r.duration_s);
      return '<tr>' +
        '<td>' + fmtDateBR(r.date) + '</td>' +
        '<td class="name">' + r.name.replace(/</g,'&lt;') + '</td>' +
        '<td><span class="type-pill">' + r.type + '</span></td>' +
        '<td class="num">' + fmtKm(r.km) + ' km</td>' +
        '<td class="num">' + fmtPace(p) + '</td>' +
        '<td class="num">' + fmtDuration(r.duration_s) + '</td>' +
      '</tr>';
    }}).join('');
  }}

  chips.forEach(chip => {{
    chip.addEventListener('click', () => {{
      chips.forEach(c => c.setAttribute('aria-pressed', 'false'));
      chip.setAttribute('aria-pressed', 'true');
      minKm = parseFloat(chip.dataset.min);
      render();
    }});
  }});
  yearFilter.addEventListener('change', () => {{ year = yearFilter.value; render(); }});

  ths.forEach(th => {{
    th.addEventListener('click', () => {{
      const key = th.dataset.key;
      if (sortKey === key) {{ sortDir *= -1; }}
      else {{ sortKey = key; sortDir = key === 'date' ? -1 : -1; }}
      ths.forEach(t => {{ t.classList.remove('sorted'); t.querySelector('.arrow').textContent = ''; }});
      th.classList.add('sorted');
      th.querySelector('.arrow').textContent = sortDir === -1 ? '↓' : '↑';
      render();
    }});
  }});

  // Shared tooltip for mini-chart bars
  document.querySelectorAll('.mini-chart .bar').forEach(bar => {{
    bar.addEventListener('mouseenter', (e) => {{
      const yr = bar.dataset.year, ct = bar.dataset.count;
      tooltip.textContent = yr + ' · ' + ct + (ct === '1' ? ' corrida' : ' corridas');
      tooltip.classList.add('show');
    }});
    bar.addEventListener('mousemove', (e) => {{
      tooltip.style.left = e.clientX + 'px';
      tooltip.style.top = e.clientY + 'px';
    }});
    bar.addEventListener('mouseleave', () => tooltip.classList.remove('show'));
  }});

  render();
}})();
</script>
'''


def main():
    if len(sys.argv) != 3:
        print("uso: build_page.py <report.json> <saida.html>", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        report = json.load(f)
    html_out = build(report)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"Pagina gerada em {sys.argv[2]}")


if __name__ == "__main__":
    main()
