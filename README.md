# Marcos de Corrida

Projeto pessoal que junta o histórico de corridas do [Garmin Connect](https://connect.garmin.com)
e do [Nike Run Club](https://www.nike.com/nrc-app) e mostra quantas vezes você já
correu 5, 10, 15, 21 e 42&nbsp;km — **independente do tamanho da corrida**: uma
corrida de 21&nbsp;km conta ponto pra estatística de 5, 10, 15 **e** 21&nbsp;km.

O resultado é uma página HTML autocontida com os marcos atingidos, evolução ano a
ano, destaques (corrida mais longa, maratonas, ano mais ativo) e uma tabela
completa e filtrável de todas as corridas — com a origem (Garmin ou Nike) de
cada uma.

## Como funciona

```
                 ┌─────────────────────────┐
  export GDPR    │  analyze_runs.py         │
  (pasta data/) ─▶  carga inicial offline   │──┐
                 └─────────────────────────┘  │
                 ┌─────────────────────────┐  │        ┌───────────────┐      ┌─────────────────┐
  Garmin Connect │  fetch_garmin.py         │  ├───────▶│  report.json  │─────▶│  build_page.py   │──▶ marcos-de-corrida.html
  (API, login)  ─▶  carga inicial ou        │──┤        │  (cache local, │      └─────────────────┘
                 │  atualização incremental │  │        │  todas as fontes)
                 └─────────────────────────┘  │        └───────────────┘
                 ┌─────────────────────────┐  │
  Nike Run Club  │  fetch_nike.py           │  │
  (API, token)  ─▶  carga inicial ou        │──┘
                 │  atualização incremental │
                 └─────────────────────────┘
```

Três formas de alimentar o mesmo `report.json`, que serve tanto de relatório
quanto de **cache local** — cada corrida carrega um campo `source` (`garmin`
ou `nike`) para que as duas integrações consigam atualizar de forma
independente sem pisar uma na outra:

| Script | Fonte | Quando usar |
|---|---|---|
| [`scripts/analyze_runs.py`](scripts/analyze_runs.py) | Export manual do Garmin Connect (pasta `data/`) | Carga inicial instantânea, sem depender de login/rate limit |
| [`scripts/fetch_garmin.py`](scripts/fetch_garmin.py) | API do Garmin Connect (login) | Atualizações de rotina — busca só as corridas novas |
| [`scripts/fetch_nike.py`](scripts/fetch_nike.py) | API do Nike Run Club (token) | Idem, pro histórico do NRC |
| [`scripts/build_page.py`](scripts/build_page.py) | `report.json` | Gera a página HTML a partir do relatório |
| [`scripts/garmin_common.py`](scripts/garmin_common.py) | — | Marcos de distância + cache/merge compartilhados pelos três scripts de carga |

## Instalação

Requer Python 3.10+.

```bash
pip install -r requirements.txt
```

(`analyze_runs.py` e `build_page.py` não têm dependências externas; `fetch_garmin.py`
usa a biblioteca `garminconnect` e `fetch_nike.py` usa `requests`.)

## Carga inicial

Rode o que fizer sentido pra você — os dois podem coexistir no mesmo `report.json`.

### Garmin — Opção A: a partir do export do Garmin Connect (recomendado)

1. Peça seu export em [connect.garmin.com](https://connect.garmin.com) → *Configurações da conta → Seus dados → Exportar seus dados* (a Garmin manda um e-mail com um `.zip` em até 24h–48h).
2. Descompacte o conteúdo dentro da pasta [`data/`](data) deste projeto, mantendo a estrutura original (`data/DI_CONNECT/DI-Connect-Fitness/...`).
3. Rode:

   ```bash
   python scripts/analyze_runs.py --out report.json
   python scripts/build_page.py report.json marcos-de-corrida.html
   ```

   (esses dois comandos também estão em [`run.bat`](run.bat), pra quem prefere um clique).

### Garmin — Opção B: direto pela API, sem export nenhum

```bash
python scripts/fetch_garmin.py --out report.json --html marcos-de-corrida.html --full
```

Pede email/senha na primeira vez (veja [Autenticação — Garmin](#autenticação--garmin)
abaixo). Como pagina o histórico inteiro pela API, pode demorar mais e esbarrar em
limite de requisições dependendo de quantos anos de corrida você tem — prefira a
Opção A se já tiver o export em mãos.

### Nike Run Club

Não existe export em massa como o do Garmin — a carga inicial já é pela API:

```bash
python scripts/fetch_nike.py --out report.json --html marcos-de-corrida.html --full
```

Pede um `refresh_token` na primeira vez (veja [Autenticação — Nike Run Club](#autenticação--nike-run-club)
abaixo — é um pouco mais manual que o Garmin porque a Nike não expõe login
simples por API).

## Atualização incremental

Depois da carga inicial, rode periodicamente (a ordem entre os dois não importa):

```bash
python scripts/fetch_garmin.py --out report.json --html marcos-de-corrida.html
python scripts/fetch_nike.py --out report.json --html marcos-de-corrida.html
```

O `report.json` existente funciona como **cache**: cada script olha só as
corridas que ele mesmo importou antes, deriva um corte de data automaticamente
a partir da mais recente conhecida (com alguns dias de folga —
`--overlap-days`, padrão 3) e busca só o que é novo naquela fonte, sem
repaginar o histórico inteiro nem mexer nas corridas da outra fonte. Corridas
já conhecidas são sobrescritas com os dados mais novos (ex.: se você renomeou
a atividade no app), e tudo é salvo de volta no mesmo `report.json`.

Flags comuns aos dois scripts:

| Flag | Efeito |
|---|---|
| `--full` | ignora o cache **daquela fonte** e rebusca o histórico inteiro dela (as corridas da outra fonte no `report.json` não são mexidas) |
| `--since 2026-01-01` | força um corte de data manual em vez do automático |
| `--overlap-days N` | folga de segurança (dias) usada ao derivar o corte do cache |
| `--tolerance 0.03` | margem de tolerância por marco (padrão 3%, cobre imprecisão de GPS) |
| `--token-dir PATH` | onde guardar a sessão/token (padrão `~/.garmin_tokens` pros dois) |

Rodar os dois comandos acima de tempos em tempos (manualmente, ou num
agendador de tarefas) já mantém tudo em dia.

## Autenticação — Garmin

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

## Autenticação — Nike Run Club

> ⚠️ A Nike não publica uma API oficial pra terceiros. `fetch_nike.py` fala com
> os mesmos endpoints internos que o site/app do NRC usam — descobertos por
> engenharia reversa, como fazem vários projetos open source de exportação de
> dados do NRC. Pode quebrar se a Nike mudar algo; se isso acontecer, rode com
> `--dump-raw arquivo.json --debug` pra inspecionar a resposta bruta e ajustar
> o `normalize_activity()`.

Diferente do Garmin, o login da Nike tem proteções que não dá pra automatizar
de forma confiável, então o script usa um **refresh_token** que você extrai
uma vez do seu próprio navegador (você já logado na sua conta, nada é
compartilhado com ninguém):

1. Acesse [nike.com](https://www.nike.com) e faça login na sua conta.
2. Abra o DevTools do navegador (`F12`) → aba **Application/Armazenamento** → **Local Storage** → `https://www.nike.com`.
3. Procure uma entrada cujo valor seja um JSON contendo `"access_token"` e `"refresh_token"` (o nome da chave varia, algo como `nike_unite` ou similar). Se não achar nada, navegue para alguma página autenticada (ex.: "Meus pedidos") pra forçar a renovação do token e olhe de novo.
4. Copie o valor de `refresh_token` e exporte como variável de ambiente:

   PowerShell:

   ```powershell
   $env:NIKE_REFRESH_TOKEN = "cole-aqui-o-refresh-token"
   python scripts/fetch_nike.py --out report.json
   ```

   Bash:

   ```bash
   export NIKE_REFRESH_TOKEN="cole-aqui-o-refresh-token"
   python scripts/fetch_nike.py --out report.json
   ```

O script troca esse token por um `access_token` de curta duração a cada
execução e guarda o `refresh_token` mais atualizado em `~/.garmin_tokens/nike_token.json`
(a Nike costuma rotacionar o token a cada uso) — nas próximas vezes não
precisa repetir o processo do navegador, só quando esse token também expirar.

## Estrutura do projeto

```
.
├── data/                    # export do Garmin Connect (GDPR) — não versionado
├── scripts/
│   ├── analyze_runs.py      # carga inicial do Garmin a partir de data/
│   ├── fetch_garmin.py      # carga inicial ou incremental do Garmin via API
│   ├── fetch_nike.py        # carga inicial ou incremental do Nike Run Club via API
│   ├── build_page.py        # report.json -> HTML
│   └── garmin_common.py     # marcos de distância + cache/merge compartilhados
├── report.json              # relatório/cache (todas as fontes) — não versionado
├── marcos-de-corrida.html   # página gerada — não versionado
├── run.bat                  # atalho pra carga do Garmin via export, no Windows
├── requirements.txt
└── README.md
```

`data/`, `report.json`, `marcos-de-corrida.html` e `~/.garmin_tokens` (sessão
do Garmin + token do Nike) ficam de fora do git (veja [`.gitignore`](.gitignore))
porque carregam dados pessoais de saúde/localização ou credenciais de sessão —
nada disso deveria ir para um repositório, nem privado.

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
scripts) pra não descartar uma corrida por causa de imprecisão de GPS. Os
marcos somam corridas de **todas as fontes** juntas — uma corrida de 21&nbsp;km
registrada no Nike conta do mesmo jeito que uma registrada no Garmin.
