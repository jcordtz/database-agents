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

2. **Description generation** (`db_agents/agents/description.py`) always
   builds a deterministic, structured summary from the raw facts (comment,
   columns + datatypes + comments, PK, FKs, reverse references). If an LLM
   is configured, that structured summary is rewritten into a polished
   natural-language paragraph — the LLM is never allowed to invent facts,
   only reword the given ones.

3. **Per-table agents** (`db_agents/agents/table_agent.py`) wrap the
   metadata + description for one table and expose a compact "catalog
   entry" used when prompting the LLM.

4. **AgentRegistry** (`db_agents/agents/registry.py`) discovers every table
   across all configured connections, builds the agents, and caches
   descriptions in a local SQLite file (`.db_agents_cache.sqlite3` by
   default) so repeated startups don't re-run introspection or LLM calls.

5. **Orchestrator** (`db_agents/orchestrator.py`) answers a cross-table
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

6. **MCP server** (`db_agents/mcp_server`) exposes four tools:
   - `list_tables` — every discovered table with a short summary
   - `describe_table(table_id)` — full description + schema for one table
   - `refresh_metadata(force=False)` — re-introspect + regenerate descriptions
   - `ask_question(question, row_limit=1000)` — the end-user entry point

## Setup

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

## Adding a new database

1. Add a new entry to `databases:` in `config.yaml` with `dialect` set to
   one of `mssql`, `oracle`, `postgresql`, `db2`.
2. Install the matching extra: `pip install -e ".[<dialect>]"`.
3. Restart the server (or call `refresh_metadata`).

Everything else — introspection, comment extraction, description
generation, per-table agents, and cross-table orchestration — works
identically regardless of which dialects are involved, including mixing
several dialects/connections in the same question.

## Testing

```bash
pytest -q
```

Tests use fully mocked `TableMetadata` fixtures and a mocked LLM client, so
they run without any real database or LLM connection.

## Security notes

- Generated SQL is restricted to read-only `SELECT`/`WITH` statements; any
  `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/MERGE/GRANT/REVOKE/CREATE` is
  rejected before execution.
- Use a database role with **read-only** grants for the credentials
  configured here — the SQL-keyword filter is a safety net, not a substitute
  for least-privilege database accounts.
- Passwords are only ever read from environment variables (`password_env`),
  never stored in the YAML config, so `config.yaml` can be safely checked
  into version control.
