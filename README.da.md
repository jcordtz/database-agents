# db-agents

![Tiger header](images/tiger.jpg)

[![English](images/lang-en-red.svg)](README.md) [![Dansk](images/lang-da--dk-green.svg)](README.da.md) [![Deutsch](images/lang-de-yellow.svg)](README.de.md)

> **Ansvarsfraskrivelse:** Dette projekt leveres **som det er** ("as-is"),
> uden nogen form for garantier eller betingelser, og er tiltænkt brug under
> **MIT-licensen**.

Forvandl hver tabel i dine MSSQL-, Oracle-, PostgreSQL- og DB2-databaser til en
selvbeskrivende "agent", og eksponér dem gennem én samlet **MCP**-server, så en
slutbruger kan stille naturlige spørgsmål på tværs af flere tabeller (og flere
databaser).

## Sådan virker det

```
                      ┌─────────────────────────────┐
 config.yaml  ──────► │  DatabaseConnectionConfig[]  │
                      └──────────────┬───────────────┘
                                     │ SQLAlchemy engine per connection
                                     ▼
                      ┌─────────────────────────────┐
                      │   Metadata Introspector      │  reflection (columns,
                      │   (db_agents/metadata)        │  types, PK/FK) +
                      └──────────────┬───────────────┘  dialect comment SQL
                                     ▼
                      ┌─────────────────────────────┐
                      │  Description Generator       │  structured summary,
                      │  (db_agents/agents/description)│ optionally rewritten
                      └──────────────┬───────────────┘  by the LLM
                                     ▼
                      ┌─────────────────────────────┐
                      │  TableAgent + AgentRegistry   │  one agent per table,
                      │  (cached in SQLite)           │  catalog of all agents
                      └──────────────┬───────────────┘
                                     ▼
                      ┌─────────────────────────────┐
                      │      Orchestrator             │  1) select tables
                      │  (db_agents/orchestrator.py)  │  2) generate SQL
                      │                                │  3) execute (read-only)
                      │                                │  4) synthesize answer
                      └──────────────┬───────────────┘
                                     ▼
                      ┌─────────────────────────────┐
                      │       MCP Server              │  list_tables,
                      │  (db_agents/mcp_server)        │  describe_table,
                      │                                │  refresh_metadata,
                      │                                │  ask_question
                      └─────────────────────────────┘
```

1. **Metadata-introspektion** (`db_agents/metadata`) bruger SQLAlchemy's
   `Inspector` til struktur-reflection (kolonner, datatyper, primærnøgler,
   fremmednøgler), som er ensartet på tværs af dialekter. Tabel-/kolonne-
   *kommentarer* udstilles ikke ens af SQLAlchemy, så hver dialekt har en lille,
   eksplicit SQL-forespørgsel (`comment_fetchers.py`):
   - PostgreSQL: `obj_description` / `col_description`
   - MSSQL: `sys.extended_properties` (`MS_Description`)
   - Oracle: `ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS`
   - DB2: `SYSCAT.TABLES` / `SYSCAT.COLUMNS` (`REMARKS`)

2. **Purview-berigelse** (`db_agents/purview` + `db_agents/agents/purview_enrichment.py`,
   valgfri). Hvis konfigureret, slås hver introspekteret tabel/kolonne op i
   Microsoft Purview Data Map (Atlas) API via et **qualified name** bygget fra
   forbindelsens host/database/schema/table-oplysninger (se
   `db_agents/purview/qualified_name.py`) efter Purviews standardkonventioner
   pr. kildetype, fx `mssql://<server>/<db>/<schema>/<table>` og
   `postgresql://<host>/<db>/<schema>/<table>`. Kolonner får `#<column>` tilføjet.
   Eventuel forretningsbeskrivelse, dataklassifikationer (PII, confidential, ...),
   glossary-termer og kontakter kobles til metadata sammen med databasekommentarer.
   Opslag fejler stiltiende som standard (`purview.fail_silently: true`), så en
   langsom/utilgængelig Purview-konto ikke blokerer agentoprettelse.

3. **Beskrivelsesgenerering** (`db_agents/agents/description.py`) bygger altid et
   deterministisk, struktureret sammendrag fra rå fakta (kommentar, kolonner +
   datatyper + kommentarer, PK, FK, reverse references og evt. Purview-data).
   Hvis en LLM er konfigureret, omskrives sammendraget til naturligt sprog.
   LLM'en må kun omformulere – aldrig opfinde fakta.

4. **Per-tabel-agenter** (`db_agents/agents/table_agent.py`) pakker metadata +
   beskrivelse for én tabel og eksponerer en kompakt katalogpost (inkl. Purview-data)
   til brug i prompts.

5. **AgentRegistry** (`db_agents/agents/registry.py`) opdager alle tabeller på
   tværs af forbindelser, udfører valgfri Purview-opslag, bygger agenterne og
   cacher beskrivelser i lokal SQLite (`.db_agents_cache.sqlite3` som standard).

6. **Orchestrator** (`db_agents/orchestrator.py`) besvarer spørgsmål på tværs af
   tabeller i fire trin:
   - Bed LLM'en vælge relevante tabeller fra kataloget.
   - Bed LLM'en skrive én read-only SQL `SELECT` (med joins) pr. databaseforbindelse.
   - SQL valideres: kun `SELECT`/`WITH` er tilladt (ingen `INSERT/UPDATE/...`).
   - Kør statements pr. forbindelse og få LLM'en til at syntetisere et svar.

7. **MCP-server** (`db_agents/mcp_server`) eksponerer fire værktøjer:
   - `list_tables` — alle opdagede tabeller med kort opsummering
   - `describe_table(table_id)` — fuld beskrivelse + schema (+ Purview-info)
   - `refresh_metadata(force=False)` — gen-introspektion + nye beskrivelser
   - `ask_question(question, row_limit=1000)` — slutbrugerens indgangspunkt

## Opsætning

Der er to måder at få en fungerende opsætning:

- **[Generér fra en CSV](#generering-af-opsætning-fra-csv-anbefalet)** —
  anbefalet når du har en liste af tabeller, der skal have agenter.
- **[Manuel konfiguration](#manuel-opsætning)** — til små eller engangsopsætninger.

## Generering af opsætning fra CSV (anbefalet)

`scripts/create-agent-setup.sh` bygger en komplet, køreklar opsætning
(virtualenv, `config.yaml`, `.env`-skabelon og launcher) fra to input.

### 1. Tabel-CSV

Én række pr. tabel, der skal have sin egen agent:

```csv
db_type,host,schema,table
postgresql,pg.example.com,public,orders
postgresql,pg.example.com,public,customers
mssql,crm-db.example.com,dbo,Customers
oracle,fin-db.example.com,FINANCE,GL_ACCOUNTS
db2,legacy-db.example.com,LEGACY,ORDERS
```

- Kolonnenavne er case-insensitive, og almindelige aliaser virker
  (`database_type`/`dialect` for `db_type`, `table_name` for `table`, ...).
- `db_type` accepterer almindelige navne: `SQL Server`, `Postgres`, `IBM DB2`, ...
- Tomme linjer og `#`-kommentarlinjer ignoreres; dubletter fjernes.
- En valgfri `database`-kolonne lader samme host betjene flere databaser.

Se [`examples/tables.example.csv`](examples/tables.example.csv).

### 2. Connections-mappe

Én `<host>.properties` pr. host i CSV'en med de oplysninger, CSV'en ikke indeholder:

```properties
# pg.example.com.properties
port=5432
database=sales
username=sales_reader
password_env=PG_SALES_PASSWORD

# optional: any "driver.<name>" entry becomes a SQLAlchemy driver option
driver.sslmode=require

# optional Purview overrides for this source
purview_source_host=pg.example.com
purview_database=sales
```

Nøgler der understøttes: `port`, `database`, `username`, `password_env`, `host`,
`sqlalchemy_url`, `driver.*` og `purview_*`-overrides. **Ingen passwords i disse
filer** — `password_env` angiver navnet på en miljøvariabel i stedet.

Se [`examples/connections/`](examples/connections).

### 3. Kør scriptet

```bash
./scripts/create-agent-setup.sh
```

Scriptet spørger om manglende værdier (inkl. **hvor opsætningen skal oprettes**).
Til automation kan du angive alt på forhånd:

```bash
./scripts/create-agent-setup.sh \
    --tables-csv examples/tables.example.csv \
    --connections-dir examples/connections \
    --target-dir ~/db-agents-prod \
    --llm-deployment gpt-4o \
    --no-purview \
    --non-interactive
```

Du kan også lægge værdierne i en options-fil og bruge `--options-file`:

```properties
tables_csv=examples/tables.example.csv
connections_dir=examples/connections
target_dir=/opt/db-agents
llm_deployment=gpt-4o
```

Nyttige flags: `--purview-endpoint URL`, `--force`, `--skip-venv`, `--python PATH`,
`--non-interactive`. Kør med `--help` for fuld liste.

### Hvad du får

```
<target-dir>/
├── config.yaml          # forbindelser, schemas og præcis tabel-liste
├── .env                 # skabelon med variabler der skal udfyldes
├── run-mcp-server.sh    # loader .env og starter MCP-serveren
├── README.md            # setupspecifikke noter og regenerering
├── inputs/              # kopi af CSV + properties til regenerering
└── .venv/               # db_agents + kun nødvendige database-drivere
```

Udfyld derefter `<target-dir>/.env` og start serveren:

```bash
<target-dir>/run-mcp-server.sh
```

Da `config.yaml` sætter `include_tables` fra CSV'en, introspekteres kun de
tabeller der står i filen.

## Manuel opsætning

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# install the driver(s) for the databases you actually need, e.g.:
pip install -e ".[postgresql]"      # PostgreSQL only
pip install -e ".[all-db]"          # every dialect
```

Kopiér eksempelkonfiguration og env-fil:

```bash
cp examples/config.example.yaml config.yaml
cp .env.example .env
```

Redigér `config.yaml` med dine rigtige forbindelsesoplysninger
(host/port/database/username/schemas) — **læg aldrig passwords direkte i YAML**.
Brug i stedet `password_env` og sæt den faktiske værdi i `.env`.

## Kørsel

Indlæs `.env` og start MCP-serveren:

```bash
set -a; source .env; set +a
db-agents-mcp
# or: python -m db_agents.mcp_server.server
```

## Aktivering af Microsoft Purview (valgfrit)

1. Opret/genbrug en service principal i Azure AD med Data Reader-rettigheder.
2. Sæt `purview.enabled: true` og `purview.account_endpoint` i `config.yaml`.
3. Læg `PURVIEW_TENANT_ID`, `PURVIEW_CLIENT_ID`, `PURVIEW_CLIENT_SECRET` i `.env`.
4. Qualified names genereres automatisk pr. tabel/kolonne ud fra
   `host`/`database`/`schema`/`table`.
5. Genstart serveren (eller kør `refresh_metadata`).

## Tilføjelse af ny database

1. Tilføj en ny post under `databases:` i `config.yaml` med `dialect` som
   `mssql`, `oracle`, `postgresql` eller `db2`.
2. Installer matchende ekstra: `pip install -e ".[<dialect>]"`.
3. Genstart serveren (eller kør `refresh_metadata`).

Hvis opsætningen er genereret fra CSV, tilføj blot nye rækker og en
`<host>.properties` og kør scriptet igen med `--force`.

## Test

```bash
pytest -q
```

## Sikkerhedsnoter

- Genereret SQL begrænses til read-only `SELECT`/`WITH`.
- Brug read-only databasebrugere; SQL-filteret er et sikkerhedsnet.
- Passwords og Purview-secret læses kun fra miljøvariabler.
- Purview service principal bør kun have read-only adgang.

---

[![English](images/lang-en-red.svg)](README.md) [![Dansk](images/lang-da--dk-green.svg)](README.da.md) [![Deutsch](images/lang-de-yellow.svg)](README.de.md)
