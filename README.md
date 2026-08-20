# db-agents

Turn every table in your MSSQL, Oracle, PostgreSQL, and DB2 databases into a
self-describing "agent," and expose them all through a single **MCP** server
so an end user can ask natural-language questions that span multiple tables
(and multiple databases).

## How it works

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

1. **Metadata introspection** (`db_agents/metadata`) uses SQLAlchemy's
   `Inspector` for structural reflection (columns, datatypes, primary keys,
   foreign keys), which is uniform across dialects. Table/column *comments*
   are not exposed uniformly by SQLAlchemy, so each dialect has a small,
   explicit SQL query (`comment_fetchers.py`):
   - PostgreSQL: `obj_description` / `col_description`
   - MSSQL: `sys.extended_properties` (`MS_Description`)
   - Oracle: `ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS`
   - DB2: `SYSCAT.TABLES` / `SYSCAT.COLUMNS` (`REMARKS`)

2. **Purview enrichment** (`db_agents/purview` + `db_agents/agents/purview_enrichment.py`,
   optional). If configured, each introspected table/column is looked up in
   Microsoft Purview's Data Map (Atlas) API by a **qualified name** built
   from the connection's host/database/schema/table information (see
   `db_agents/purview/qualified_name.py`), following Purview's standard
   per-source conventions, e.g. `mssql://<server>/<db>/<schema>/<table>` for
   MSSQL, `postgresql://<host>/<db>/<schema>/<table>` for PostgreSQL, etc.
   Column qualified names append `#<column>` to the table's qualified name.
   Any business description, data classifications (PII, confidential, ...),
   glossary terms, and contacts found are attached to the table/column
   metadata alongside the database-native comments. Lookups fail silently by
   default (`purview.fail_silently: true`) so a slow/unreachable Purview
   account never blocks table-agent creation — Purview data is strictly an
   optional enrichment layered on top of the database-native metadata.

3. **Description generation** (`db_agents/agents/description.py`) always
   builds a deterministic, structured summary from the raw facts (comment,
   columns + datatypes + comments, PK, FKs, reverse references, and — when
   present — the merged Purview classifications/glossary terms/business
   description). If an LLM is configured, that structured summary is
   rewritten into a polished natural-language paragraph — the LLM is never
   allowed to invent facts, only reword the given ones.

4. **Per-table agents** (`db_agents/agents/table_agent.py`) wrap the
   metadata + description for one table and expose a compact "catalog
   entry" (including any Purview data) used when prompting the LLM.

5. **AgentRegistry** (`db_agents/agents/registry.py`) discovers every table
   across all configured connections, performs the optional Purview lookup,
   builds the agents, and caches descriptions in a local SQLite file
   (`.db_agents_cache.sqlite3` by default) so repeated startups don't re-run
   introspection, Purview lookups, or LLM calls.

6. **Orchestrator** (`db_agents/orchestrator.py`) answers a cross-table
   question in four steps:
   - Ask the LLM which table(s), from the full catalog, are relevant.
   - For each database *connection* involved, ask the LLM to write a single
     read-only SQL `SELECT` (with joins) using only the given tables/columns.
     Generated SQL is validated to reject anything other than a `SELECT`/`WITH`
     statement (no `INSERT`/`UPDATE`/`DELETE`/`DROP`/etc.).
   - Execute each statement against its connection (a single connection can
     only produce one relational join; separate connections cannot be joined
     in SQL, so each gets its own query).
   - Ask the LLM to synthesize a final natural-language answer from the
     combined result sets.

7. **MCP server** (`db_agents/mcp_server`) exposes four tools:
   - `list_tables` — every discovered table with a short summary
   - `describe_table(table_id)` — full description + schema (+ Purview info) for one table
   - `refresh_metadata(force=False)` — re-introspect + regenerate descriptions
   - `ask_question(question, row_limit=1000)` — the end-user entry point

## Setup

There are two ways to get a working setup:

- **[Generate one from a CSV](#generating-a-setup-from-a-csv-recommended)** —
  recommended when you have a list of tables you want agents for.
- **[Configure it by hand](#manual-setup)** — for small or one-off setups.

## Generating a setup from a CSV (recommended)

`scripts/create-agent-setup.sh` builds a complete, ready-to-run setup
(virtualenv, `config.yaml`, `.env` skeleton and a launcher) from two inputs.

### 1. The tables CSV

One row per table that should get its own agent:

```csv
db_type,host,schema,table
postgresql,pg.example.com,public,orders
postgresql,pg.example.com,public,customers
mssql,crm-db.example.com,dbo,Customers
oracle,fin-db.example.com,FINANCE,GL_ACCOUNTS
db2,legacy-db.example.com,LEGACY,ORDERS
```

- Header names are case-insensitive and common aliases work
  (`database_type`/`dialect` for `db_type`, `table_name` for `table`, …).
- `db_type` accepts friendly spellings: `SQL Server`, `Postgres`, `IBM DB2`, …
- Blank lines and `#` comment lines are ignored; duplicate rows are de-duplicated.
- An optional `database` column lets one host serve several databases; each
  database then becomes its own connection.

See [`examples/tables.example.csv`](examples/tables.example.csv).

### 2. The connections directory

One `<host>.properties` file per host in the CSV, holding everything the CSV
doesn't carry:

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

Recognised keys: `port`, `database`, `username`, `password_env`, `host`,
`sqlalchemy_url`, `driver.*`, and the `purview_*` overrides. **No passwords
go in these files** — `password_env` names an environment variable instead.
If `port` is omitted, the dialect default is used (1433/1521/5432/50000).

See [`examples/connections/`](examples/connections).

### 3. Run the script

```bash
./scripts/create-agent-setup.sh
```

It prompts for anything it needs (including **where to create the setup**).
For automation, pass everything up front:

```bash
./scripts/create-agent-setup.sh \
    --tables-csv examples/tables.example.csv \
    --connections-dir examples/connections \
    --target-dir ~/db-agents-prod \
    --llm-deployment gpt-4o \
    --no-purview \
    --non-interactive
```

Or put the same values in an options file and pass `--options-file`:

```properties
tables_csv=examples/tables.example.csv
connections_dir=examples/connections
target_dir=/opt/db-agents
llm_deployment=gpt-4o
```

Useful flags: `--purview-endpoint URL` (enables Purview enrichment),
`--force` (overwrite an existing config), `--skip-venv`, `--python PATH`,
`--non-interactive`. Run with `--help` for the full list.

### What you get

```
<target-dir>/
├── config.yaml          # connections, schemas and the exact table list
├── .env                 # skeleton listing every variable you must fill in
├── run-mcp-server.sh    # sources .env and starts the MCP server
├── README.md            # setup-specific notes and how to regenerate
├── inputs/              # copy of the CSV + properties used, for regeneration
└── .venv/               # db_agents + only the DB drivers your CSV needs
```

The script inspects the CSV to decide which driver extras to install, so a
PostgreSQL-only CSV won't pull in the Oracle or DB2 clients.

Then fill in the blanks in `<target-dir>/.env` and start the server:

```bash
<target-dir>/run-mcp-server.sh
```

Because `config.yaml` sets `include_tables` from the CSV, only the listed
tables are introspected and get agents.

## Manual setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# install the driver(s) for the databases you actually need, e.g.:
pip install -e ".[postgresql]"      # PostgreSQL only
pip install -e ".[all-db]"          # every dialect
```

Copy the example config and env file:

```bash
cp examples/config.example.yaml config.yaml
cp .env.example .env
```

Edit `config.yaml` with your real connection details (host/port/database/
username/schemas) — **never put passwords directly in the YAML**. Instead,
set `password_env` to the name of an environment variable, and put the
actual password in `.env` (which is git-ignored).

Set your Azure OpenAI endpoint/key/deployment in `.env` as well.

## Running

Load the `.env` file, then run the MCP server (stdio transport by default,
suitable for MCP clients like Claude Desktop / VS Code / other IDEs):

```bash
set -a; source .env; set +a
db-agents-mcp
# or: python -m db_agents.mcp_server.server
```

Point your MCP client's config at this command. On first `ask_question` or
`list_tables` call, the registry introspects all configured databases and
generates descriptions (cached afterward). Call `refresh_metadata` after
schema changes.

## Enabling Microsoft Purview enrichment (optional)

1. In Azure AD, register (or reuse) a service principal and grant it a
   **Data Reader** role (or equivalent) on your Purview account/collection.
2. Set `purview.enabled: true` and `purview.account_endpoint` in
   `config.yaml`, e.g. `https://<your-account>.purview.azure.com`.
3. Put the service principal's tenant/client id/secret in `.env`:
   `PURVIEW_TENANT_ID`, `PURVIEW_CLIENT_ID`, `PURVIEW_CLIENT_SECRET`.
4. Qualified names are generated automatically per table/column from each
   connection's `host`/`database`/`schema`/`table` using Purview's standard
   per-dialect conventions. If a source was registered in Purview under a
   different hostname/database name than you connect with here (common when
   connecting through a private endpoint, proxy, or read replica), set
   `purview_source_host` and/or `purview_database` on that connection in
   `config.yaml`. For a fully custom scan convention, set
   `purview_qualified_name_template` instead (supports `{host}`, `{port}`,
   `{database}`, `{schema}`, `{table}` placeholders).
5. Restart the server (or call `refresh_metadata`). Any business
   description, classifications, glossary terms, and contacts found in
   Purview for a table/column are merged with the database-native comment
   and included in that table's description and its MCP catalog entry — so
   the orchestrator's table selection, SQL generation, and answer synthesis
   can all take governance context (e.g. "this column is PII") into account.

Purview lookups are best-effort: by default (`purview.fail_silently: true`)
a slow or unreachable Purview account is logged and skipped rather than
blocking table-agent creation.

## Adding a new database

1. Add a new entry to `databases:` in `config.yaml` with `dialect` set to
   one of `mssql`, `oracle`, `postgresql`, `db2`.
2. Install the matching extra: `pip install -e ".[<dialect>]"`.
3. Restart the server (or call `refresh_metadata`).

Or, if you generated the setup from a CSV, just add the new rows to the CSV,
drop a `<host>.properties` file next to the others, and re-run
`create-agent-setup.sh` with `--force`.

Everything else — introspection, comment extraction, description
generation, per-table agents, and cross-table orchestration — works
identically regardless of which dialects are involved, including mixing
several dialects/connections in the same question.

## Testing

```bash
pytest -q
```

Tests use fully mocked `TableMetadata` fixtures and a mocked LLM/Purview
client, so they run without any real database, LLM, or Purview connection.

## Security notes

- Generated SQL is restricted to read-only `SELECT`/`WITH` statements; any
  `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/MERGE/GRANT/REVOKE/CREATE` is
  rejected before execution.
- Use a database role with **read-only** grants for the credentials
  configured here — the SQL-keyword filter is a safety net, not a substitute
  for least-privilege database accounts.
- Passwords, and the Purview service principal's client secret, are only
  ever read from environment variables (`password_env`, `PURVIEW_CLIENT_SECRET`,
  etc.), never stored in the YAML config, so `config.yaml` can be safely
  checked into version control.
- Grant the Purview service principal read-only ("Data Reader") access —
  the integration only ever performs `GET` lookups against the Data Map API.
