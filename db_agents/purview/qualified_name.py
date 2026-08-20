"""Builds Microsoft Purview-style qualified names for tables and columns.

Purview's Atlas-based qualifiedName format is source-type specific and must
match exactly how the data source was scanned/registered in your Purview
account. The templates below reflect the documented conventions for each
dialect; override them per connection (see
`DatabaseConnectionConfig.purview_qualified_name_template`) if your Purview
account uses a different convention (e.g. a custom scan or non-standard
hostname).

Reference templates (host/port/database/schema/table/column placeholders):
  - mssql:      mssql://{host}/{database}/{schema}/{table}
  - oracle:     oracle://{host}:{port}/{database}/{schema}/{table}
  - postgresql: postgresql://{host}/{database}/{schema}/{table}
  - db2:        db2://{host}:{port}/{database}/{schema}/{table}

Column qualified names simply append "#{column}" to the table's qualified
name, which matches Purview's convention across these source types.
"""
from __future__ import annotations

from db_agents.config import DatabaseConnectionConfig

_DEFAULT_TABLE_TEMPLATES: dict[str, str] = {
    "mssql": "mssql://{host}/{database}/{schema}/{table}",
    "oracle": "oracle://{host}:{port}/{database}/{schema}/{table}",
    "postgresql": "postgresql://{host}/{database}/{schema}/{table}",
    "db2": "db2://{host}:{port}/{database}/{schema}/{table}",
}

_DEFAULT_PORTS: dict[str, int] = {
    "mssql": 1433,
    "oracle": 1521,
    "postgresql": 5432,
    "db2": 50000,
}


def build_table_qualified_name(conn: DatabaseConnectionConfig, schema: str | None, table: str) -> str:
    """Build the Purview qualified name for a table, using this connection's
    overrides (purview_source_host / purview_database / a custom template)
    where provided, falling back to sensible per-dialect defaults."""
    template = conn.purview_qualified_name_template or _DEFAULT_TABLE_TEMPLATES.get(conn.dialect)
    if template is None:
        raise ValueError(f"No Purview qualified name template known for dialect '{conn.dialect}'")

    host = conn.purview_source_host or conn.host or ""
    database = conn.purview_database or conn.database or ""
    port = conn.port or _DEFAULT_PORTS.get(conn.dialect, "")

    return template.format(host=host, port=port, database=database, schema=schema or "", table=table)


def build_column_qualified_name(table_qualified_name: str, column: str) -> str:
    """Purview convention: column qualified name = table qualified name + '#' + column name."""
    return f"{table_qualified_name}#{column}"
