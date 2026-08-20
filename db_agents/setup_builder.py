"""Builds a ready-to-run db-agents configuration from two simple inputs:

1. A **tables CSV** listing which tables should get an agent, one row per
   table::

       db_type,host,schema,table
       postgresql,pg.example.com,public,orders
       postgresql,pg.example.com,public,customers
       mssql,crm-db.example.com,dbo,Customers
       oracle,fin-db.example.com,FINANCE,GL_ACCOUNTS
       db2,legacy-db.example.com,LEGACY,ORDERS

   Header names are case-insensitive and a few common aliases are accepted
   (e.g. ``database_type``/``dialect`` for ``db_type``, ``table_name`` for
   ``table``). Blank lines and ``#`` comment lines are ignored.

2. A **connections directory** holding one ``<host>.properties`` file per
   host, in ``key=value`` format, carrying everything that isn't in the CSV::

       # pg.example.com.properties
       port=5432
       database=sales
       username=sales_reader
       password_env=PG_EXAMPLE_PASSWORD
       # optional:
       driver.sslmode=require
       purview_source_host=pg.example.com
       purview_database=sales

Rows are grouped by (db_type, host) -- and by ``database`` where a host
serves several databases -- into one connection per group, with the CSV's
schemas and tables becoming that connection's ``schemas`` and
``include_tables`` lists, so exactly the listed tables get agents.

The result is an :class:`~db_agents.config.AppConfig`, which is written out
as the ``config.yaml`` consumed by the MCP server.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from db_agents.config import AppConfig, CacheConfig, DatabaseConnectionConfig, LLMConfig, PurviewConfig

# Accepted spellings for each logical CSV column, normalised to lower case
# with non-alphanumerics stripped.
_COLUMN_ALIASES: dict[str, str] = {
    "dbtype": "db_type",
    "databasetype": "db_type",
    "dialect": "db_type",
    "technology": "db_type",
    "databasetechnology": "db_type",
    "host": "host",
    "hostname": "host",
    "server": "host",
    "schema": "schema",
    "schemaname": "schema",
    "owner": "schema",
    "table": "table",
    "tablename": "table",
    "database": "database",
    "databasename": "database",
    "db": "database",
}

# Accepted spellings of each dialect, normalised to the canonical value used
# by DatabaseConnectionConfig.
_DIALECT_ALIASES: dict[str, str] = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "pg": "postgresql",
    "mssql": "mssql",
    "sqlserver": "mssql",
    "microsoftsqlserver": "mssql",
    "azuresql": "mssql",
    "oracle": "oracle",
    "db2": "db2",
    "ibmdb2": "db2",
}

_DEFAULT_PORTS: dict[str, int] = {
    "mssql": 1433,
    "oracle": 1521,
    "postgresql": 5432,
    "db2": 50000,
}


class SetupBuilderError(Exception):
    """Raised when the CSV/properties input is invalid or inconsistent."""


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def normalise_dialect(value: str) -> str:
    """Map a user-supplied database technology name to a supported dialect."""
    key = _normalise_key(value)
    if key not in _DIALECT_ALIASES:
        supported = sorted(set(_DIALECT_ALIASES.values()))
        raise SetupBuilderError(
            f"Unsupported database technology '{value}'. Supported values map to one of: {', '.join(supported)}"
        )
    return _DIALECT_ALIASES[key]


@dataclass(frozen=True)
class TableRow:
    """One row of the tables CSV."""

    dialect: str
    host: str
    schema: str
    table: str
    database: str | None = None


def parse_tables_csv(path: str | Path) -> list[TableRow]:
    """Parse the tables CSV into TableRow entries, ignoring blanks/comments."""
    path = Path(path)
    if not path.is_file():
        raise SetupBuilderError(f"Tables CSV not found: {path}")

    text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise SetupBuilderError(f"Tables CSV is empty: {path}")

    reader = csv.reader(lines)
    raw_header = next(reader)
    header: list[str] = []
    for column in raw_header:
        key = _normalise_key(column)
        header.append(_COLUMN_ALIASES.get(key, key))

    required = {"db_type", "host", "schema", "table"}
    missing = required - set(header)
    if missing:
        raise SetupBuilderError(
            f"Tables CSV {path} is missing required column(s): {', '.join(sorted(missing))}. "
            f"Found columns: {', '.join(raw_header)}"
        )

    rows: list[TableRow] = []
    seen: set[tuple[str, str, str | None, str, str]] = set()
    for line_number, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        record = {key: (values[i].strip() if i < len(values) else "") for i, key in enumerate(header)}

        for required_column in ("db_type", "host", "schema", "table"):
            if not record.get(required_column):
                raise SetupBuilderError(
                    f"{path} line {line_number}: missing value for required column '{required_column}'"
                )

        row = TableRow(
            dialect=normalise_dialect(record["db_type"]),
            host=record["host"],
            schema=record["schema"],
            table=record["table"],
            database=record.get("database") or None,
        )
        key = (row.dialect, row.host, row.database, row.schema, row.table)
        if key in seen:
            continue  # silently de-duplicate identical rows
        seen.add(key)
        rows.append(row)

    if not rows:
        raise SetupBuilderError(f"Tables CSV {path} contains a header but no data rows")
    return rows


def parse_properties(path: str | Path) -> dict[str, str]:
    """Parse a simple ``key=value`` properties file.

    Supports ``#`` and ``!`` comments, blank lines, surrounding whitespace,
    and optionally quoted values.
    """
    path = Path(path)
    properties: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" not in line:
            raise SetupBuilderError(f"{path} line {line_number}: expected 'key=value', got: {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        properties[key] = value
    return properties


def find_properties_file(connections_dir: str | Path, host: str) -> Path:
    """Locate the connection properties file for a host.

    Tries ``<host>.properties`` first, then a few conventional fallbacks so
    that e.g. ``pg.example.com`` can also be described by ``pg.properties``.
    """
    connections_dir = Path(connections_dir)
    if not connections_dir.is_dir():
        raise SetupBuilderError(f"Connections directory not found: {connections_dir}")

    candidates = [
        f"{host}.properties",
        f"{host}.props",
        f"{host}.conf",
        f"{host.split('.')[0]}.properties",
    ]
    for candidate in candidates:
        candidate_path = connections_dir / candidate
        if candidate_path.is_file():
            return candidate_path

    available = sorted(p.name for p in connections_dir.glob("*.properties"))
    raise SetupBuilderError(
        f"No connection properties file for host '{host}' in {connections_dir} "
        f"(looked for {candidates[0]}). Available files: {', '.join(available) or '(none)'}"
    )


def _connection_name(dialect: str, host: str, database: str | None, needs_database_suffix: bool) -> str:
    """Build a stable, readable, YAML/id-friendly connection name."""
    host_part = re.sub(r"[^a-zA-Z0-9]+", "_", host.split(".")[0]).strip("_").lower()
    parts = [host_part, dialect]
    if needs_database_suffix and database:
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "_", database).strip("_").lower())
    return "_".join(p for p in parts if p)


def _bool_property(properties: dict[str, str], key: str) -> bool | None:
    if key not in properties:
        return None
    return properties[key].strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class BuildResult:
    config: AppConfig
    # Environment variable names referenced by the generated config, so the
    # caller can emit a matching .env skeleton.
    password_env_vars: list[str] = field(default_factory=list)
    dialects: set[str] = field(default_factory=set)
    table_count: int = 0


def build_app_config(
    tables_csv: str | Path,
    connections_dir: str | Path,
    llm_deployment: str | None = None,
    purview: PurviewConfig | None = None,
    cache: CacheConfig | None = None,
) -> BuildResult:
    """Build an AppConfig from the tables CSV plus per-host properties files."""
    rows = parse_tables_csv(tables_csv)

    # Cache each host's properties so we read every file at most once.
    properties_by_host: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.host not in properties_by_host:
            properties_by_host[row.host] = parse_properties(find_properties_file(connections_dir, row.host))

    # Group rows into connections. A host serving several databases yields one
    # connection per database, so the CSV can span multiple databases per host.
    groups: dict[tuple[str, str, str | None], list[TableRow]] = {}
    for row in rows:
        database = row.database or properties_by_host[row.host].get("database") or None
        groups.setdefault((row.dialect, row.host, database), []).append(row)

    databases_per_host: dict[str, set[str | None]] = {}
    for dialect, host, database in groups:
        databases_per_host.setdefault(host, set()).add(database)

    connections: list[DatabaseConnectionConfig] = []
    password_env_vars: list[str] = []
    dialects: set[str] = set()

    for (dialect, host, database), group_rows in groups.items():
        properties = properties_by_host[host]
        needs_suffix = len(databases_per_host[host]) > 1
        name = _connection_name(dialect, host, database, needs_suffix)

        if not database:
            raise SetupBuilderError(
                f"No database name for host '{host}': add a 'database=' entry to its properties file "
                f"or a 'database' column to the tables CSV"
            )

        password_env = properties.get("password_env") or properties.get("passwordEnv")
        if password_env:
            password_env_vars.append(password_env)

        port_value = properties.get("port")
        try:
            port = int(port_value) if port_value else _DEFAULT_PORTS.get(dialect)
        except ValueError as exc:
            raise SetupBuilderError(f"Invalid port '{port_value}' in properties for host '{host}'") from exc

        # Any "driver.<name>=<value>" entries become SQLAlchemy driver options.
        driver_options = {
            key.split(".", 1)[1]: value for key, value in properties.items() if key.startswith("driver.")
        }

        schemas = sorted({row.schema for row in group_rows})
        include_tables = sorted({f"{row.schema}.{row.table}" for row in group_rows})

        connections.append(
            DatabaseConnectionConfig(
                name=name,
                dialect=dialect,
                host=properties.get("host") or host,
                port=port,
                database=database,
                username=properties.get("username") or None,
                password_env=password_env,
                sqlalchemy_url=properties.get("sqlalchemy_url") or None,
                driver_options=driver_options,
                schemas=schemas,
                include_tables=include_tables,
                purview_enabled=_bool_property(properties, "purview_enabled"),
                purview_source_host=properties.get("purview_source_host") or None,
                purview_database=properties.get("purview_database") or None,
                purview_table_entity_type=properties.get("purview_table_entity_type") or None,
                purview_column_entity_type=properties.get("purview_column_entity_type") or None,
                purview_qualified_name_template=properties.get("purview_qualified_name_template") or None,
            )
        )
        dialects.add(dialect)

    connections.sort(key=lambda c: c.name)

    config = AppConfig(
        databases=connections,
        llm=LLMConfig(deployment=llm_deployment) if llm_deployment else None,
        cache=cache or CacheConfig(),
        purview=purview,
    )

    return BuildResult(
        config=config,
        password_env_vars=sorted(set(password_env_vars)),
        dialects=dialects,
        table_count=len(rows),
    )


def config_to_yaml(config: AppConfig) -> str:
    """Serialise an AppConfig to the YAML layout understood by load_config."""
    data = config.model_dump(exclude_none=True, exclude_defaults=False)

    # Drop empty collections to keep the generated file readable.
    for connection in data.get("databases", []):
        for key in ("driver_options", "exclude_tables", "schemas", "include_tables"):
            if key in connection and not connection[key]:
                del connection[key]

    header = (
        "# Generated by scripts/create-agent-setup.sh -- do not edit by hand if you\n"
        "# intend to re-run the generator; edit the tables CSV / properties files instead.\n"
        "#\n"
        "# Secrets are never written here: 'password_env' names an environment\n"
        "# variable, which you should set in the accompanying .env file.\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def env_skeleton(result: BuildResult, purview_enabled: bool, llm_configured: bool) -> str:
    """Build a .env skeleton listing every environment variable the generated
    config expects, so the user only has to fill in the values."""
    lines = [
        "# Environment variables required by this db-agents setup.",
        "# Fill in the values, then: set -a; source .env; set +a",
        "",
        "DB_AGENTS_CONFIG=config.yaml",
        "",
    ]

    if llm_configured:
        lines += [
            "# Azure OpenAI (required for ask_question)",
            "AZURE_OPENAI_ENDPOINT=",
            "AZURE_OPENAI_API_KEY=",
            "",
        ]

    if result.password_env_vars:
        lines.append("# Database passwords (one per connection that declares password_env)")
        lines += [f"{name}=" for name in result.password_env_vars]
        lines.append("")

    if purview_enabled:
        lines += [
            "# Microsoft Purview service principal",
            "PURVIEW_TENANT_ID=",
            "PURVIEW_CLIENT_ID=",
            "PURVIEW_CLIENT_SECRET=",
            "",
        ]

    return "\n".join(lines)
