# Marcos de Corrida

Projeto pessoal que analisa o histórico de corridas do [Garmin Connect](https://connect.garmin.com)
e mostra quantas vezes você já correu 5, 10, 15, 21 e 42&nbsp;km — **independente do
tamanho da corrida**: uma corrida de 21&nbsp;km conta ponto pra estatística de 5, 10, 15
**e** 21&nbsp;km.

O resultado é uma página HTML autocontida com os marcos atingidos, evolução ano a
ano, uma seção de **Performance** (ritmo, cadência, frequência cardíaca e zona
de FC, filtrável por período), destaques (corrida mais longa, maratonas, ano
mais ativo) e uma tabela completa e filtrável de todas as corridas.

## Como funciona

```
                 ┌────────────────────────┐
  export GDPR    │  analyze_runs.py        │
  (pasta data/) ─▶  carga inicial offline  │─┐
                 └────────────────────────┘ │
                                             │        ┌──────────────┐      ┌───────────────────┐
                 ┌────────────────────────┐ ├───────▶│  report.json  │─────▶│  build_page.py     │──▶ marcos-de-corrida.html
  Garmin Connect │  fetch_garmin.py        │ │        │  (cache local)│      └───────────────────┘
  (API, login)  ─▶  carga inicial ou       │─┘        └──────────────┘
                 │  atualização incremental│
                 └────────────────────────┘
```

Duas formas de alimentar o mesmo `report.json`, que serve tanto de relatório
quanto de **cache local**:

| Script | Fonte | Quando usar |
|---|---|---|
| [`scripts/analyze_runs.py`](scripts/analyze_runs.py) | Export manual do Garmin Connect (pasta `data/`) | Carga inicial instantânea, sem depender de login/rate limit |
| [`scripts/fetch_garmin.py`](scripts/fetch_garmin.py) | API do Garmin Connect (login) | Atualizações de rotina — busca só as corridas novas |
| [`scripts/build_page.py`](scripts/build_page.py) | `report.json` | Gera a página HTML a partir do relatório |
| [`scripts/garmin_common.py`](scripts/garmin_common.py) | — | Marcos de distância + cache compartilhados pelos dois scripts de carga |

## Instalação

Requer Python 3.10+.

```bash
pip install -r requirements.txt
```

(`analyze_runs.py` e `build_page.py` não têm dependências externas — só
`fetch_garmin.py` precisa da biblioteca `garminconnect`.)

## Carga inicial

Escolha **uma** das duas formas de popular o `report.json` pela primeira vez.

### Opção A — a partir do export do Garmin Connect (recomendado)

1. Peça seu export em [connect.garmin.com](https://connect.garmin.com) → *Configurações da conta → Seus dados → Exportar seus dados* (a Garmin manda um e-mail com um `.zip` em até 24h–48h).
2. Descompacte o conteúdo dentro da pasta [`data/`](data) deste projeto, mantendo a estrutura original (`data/DI_CONNECT/DI-Connect-Fitness/...`).
3. Rode:

   ```bash
   python scripts/analyze_runs.py --out report.json
   python scripts/build_page.py report.json marcos-de-corrida.html
   ```

   (esses dois comandos também estão em [`run.bat`](run.bat), pra quem prefere um clique).

### Opção B — direto pela API, sem export nenhum

```bash
python scripts/fetch_garmin.py --out report.json --html marcos-de-corrida.html --full
```

Pede email/senha na primeira vez (veja [Autenticação](#autenticação)
abaixo). Como pagina o histórico inteiro pela API, pode demorar mais e esbarrar em
limite de requisições dependendo de quantos anos de corrida você tem — prefira a
Opção A se já tiver o export em mãos.

## Atualização incremental

Depois da carga inicial (por qualquer uma das opções acima), rode periodicamente:

```bash
python scripts/fetch_garmin.py --out report.json --html marcos-de-corrida.html
```

O `report.json` existente funciona como **cache**: o script olha a corrida mais
recente que já conhece, deriva um corte de data automaticamente (com alguns
dias de folga — `--overlap-days`, padrão 3) e busca só as atividades novas na
API, sem repaginar o histórico inteiro. Corridas já conhecidas são
sobrescritas com os dados mais novos (ex.: se você renomeou a atividade no
app), e tudo é salvo de volta no mesmo `report.json`.

Flags úteis:

| Flag | Efeito |
|---|---|
| `--full` | ignora o cache e rebusca todo o histórico via API |
| `--since 2026-01-01` | força um corte de data manual em vez do automático |
| `--overlap-days N` | folga de segurança (dias) usada ao derivar o corte do cache |
| `--tolerance 0.03` | margem de tolerância por marco (padrão 3%, cobre imprecisão de GPS) |
| `--token-dir PATH` | onde guardar a sessão logada (padrão `~/.garmin_tokens`) |
| `--skip-hr-zones` | não busca o tempo em cada zona de FC (1 chamada extra por corrida nova) — mais rápido, mas a corrida fica sem dado pra seção Performance |

Rodar `python scripts/fetch_garmin.py --html marcos-de-corrida.html` de tempos
em tempos (manualmente, ou num agendador de tarefas) já mantém tudo em dia.

## Performance

A seção **Performance** da página soma ritmo, cadência, FC e tempo em zona de
todas as corridas dentro do período selecionado (filtro por data, com atalhos
"Este ano" / "Últimos 90 dias" / "Últimos 30 dias" ou datas manuais). O ritmo
médio é a soma da distância dividida pela soma do tempo no período (não a
média simples dos ritmos), que é o jeito correto de agregar.

A disponibilidade de cada dado depende de como a corrida entrou no relatório:

| Dado | Export do Garmin (`analyze_runs.py`) | API do Garmin (`fetch_garmin.py`) |
|---|---|---|
| Ritmo | sempre (via distância/duração) | sempre |
| Cadência | se o dispositivo registrou | se o dispositivo registrou |
| FC média | se usou monitor de FC | se usou monitor de FC |
| Zona de FC | se o export trouxe o detalhe por zona | se usou monitor de FC (1 chamada extra por corrida — `--skip-hr-zones` desliga) |

Corridas sem um dado específico simplesmente não entram naquela média — cada
cartão mostra "N de M corridas com dado" pra deixar isso visível.

## Autenticação

- Defina as variáveis de ambiente `GARMIN_EMAIL` e `GARMIN_PASSWORD` antes de
  rodar, ou deixe em branco para digitar interativamente (a senha não aparece
  no terminal). **O script nunca grava a senha em disco** e nunca é visto por
  terceiros — quem digita é você, direto no seu terminal.
- Se sua conta usa verificação em duas etapas, o código é pedido na hora.
- Depois do primeiro login, a sessão fica em cache em `~/.garmin_tokens`
  (configurável com `--token-dir`) e as próximas execuções não pedem
  email/senha de novo — só quando a sessão expira ou é revogada.

PowerShell:

```powershell
$env:GARMIN_EMAIL = "voce@example.com"
$env:GARMIN_PASSWORD = "sua-senha"
python scripts/fetch_garmin.py --out report.json
```

Bash:

```bash
export GARMIN_EMAIL="voce@example.com"
export GARMIN_PASSWORD="sua-senha"
python scripts/fetch_garmin.py --out report.json
```

## Estrutura do projeto

```
.
├── data/                    # export do Garmin Connect (GDPR) — não versionado
├── scripts/
│   ├── analyze_runs.py      # carga inicial a partir de data/
│   ├── fetch_garmin.py      # carga inicial ou incremental via API
│   ├── build_page.py        # report.json -> HTML
│   └── garmin_common.py     # marcos de distância + lógica compartilhada
├── report.json              # relatório/cache — não versionado
├── marcos-de-corrida.html   # página gerada — não versionado
├── run.bat                  # atalho pra Opção A no Windows
├── requirements.txt
└── README.md
```

`data/`, `report.json`, `marcos-de-corrida.html` e `~/.garmin_tokens` ficam de
fora do git (veja [`.gitignore`](.gitignore)) porque carregam dados pessoais
de saúde/localização ou credenciais de sessão — nada disso deveria ir para um
repositório, nem privado.

## Os marcos de distância

Definidos em [`scripts/garmin_common.py`](scripts/garmin_common.py):

| Marco | Limiar considerado |
|---|---|
| 5 km | 5,00 km × (1 − tolerância) |
| 10 km | 10,00 km × (1 − tolerância) |
| 15 km | 15,00 km × (1 − tolerância) |
| 21 km | 21,0975 km (meia maratona oficial) × (1 − tolerância) |
| 42 km | 42,195 km (maratona oficial) × (1 − tolerância) |

A tolerância padrão é 3% (ajustável com `--tolerance` em qualquer um dos
scripts) pra não descartar uma corrida por causa de imprecisão de GPS.
