"""Unified metadata introspection across MSSQL, Oracle, PostgreSQL and DB2.

Uses SQLAlchemy's Inspector for structural reflection (columns, types, PK,
FK) since that API is uniform across dialects, and falls back to small
dialect-specific SQL queries (comment_fetchers) for table/column comments,
which SQLAlchemy does not expose consistently.
"""
from __future__ import annotations

import fnmatch

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from db_agents.config import DatabaseConnectionConfig
from db_agents.metadata.comment_fetchers import COMMENT_FETCHERS
from db_agents.metadata.models import ColumnMetadata, ForeignKeyMetadata, TableMetadata


def _is_excluded(table_name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(table_name, pattern) for pattern in patterns)


def introspect_connection(engine: Engine, conn_config: DatabaseConnectionConfig) -> list[TableMetadata]:
    """Introspect all tables (optionally scoped to conn_config.schemas) for one connection."""
    inspector = inspect(engine)
    schemas = conn_config.schemas or [inspector.default_schema_name]

    fetcher = COMMENT_FETCHERS.get(conn_config.dialect)
    with engine.connect() as connection:
        if fetcher is not None:
            table_comments, column_comments = fetcher(connection, conn_config.schemas)
        else:
            table_comments, column_comments = {}, {}

    tables: list[TableMetadata] = []
    for schema in schemas:
        for table_name in inspector.get_table_names(schema=schema):
            if _is_excluded(table_name, conn_config.exclude_tables):
                continue

            pk_constraint = inspector.get_pk_constraint(table_name, schema=schema)
            pk_columns = pk_constraint.get("constrained_columns") or []

            columns: list[ColumnMetadata] = []
            for col in inspector.get_columns(table_name, schema=schema):
                comment = col.get("comment") or column_comments.get((schema, table_name, col["name"]))
                columns.append(
                    ColumnMetadata(
                        name=col["name"],
                        data_type=str(col["type"]),
                        nullable=col.get("nullable", True),
                        default=str(col["default"]) if col.get("default") is not None else None,
                        is_primary_key=col["name"] in pk_columns,
                        comment=comment,
                    )
                )

            foreign_keys: list[ForeignKeyMetadata] = []
            for fk in inspector.get_foreign_keys(table_name, schema=schema):
                if not fk.get("referred_table"):
                    continue
                foreign_keys.append(
                    ForeignKeyMetadata(
                        constrained_columns=fk.get("constrained_columns", []),
                        referred_schema=fk.get("referred_schema"),
                        referred_table=fk["referred_table"],
                        referred_columns=fk.get("referred_columns", []),
                    )
                )

            table_comment = table_comments.get((schema, table_name))

            tables.append(
                TableMetadata(
                    connection_name=conn_config.name,
                    dialect=conn_config.dialect,
                    schema_name=schema,
                    table_name=table_name,
                    table_comment=table_comment,
                    columns=columns,
                    primary_key=pk_columns,
                    foreign_keys=foreign_keys,
                )
            )

    _populate_reverse_references(tables)
    return tables


def _populate_reverse_references(tables: list[TableMetadata]) -> None:
    """Fill in `referenced_by` on each table based on the other tables' foreign keys."""
    by_name = {t.table_name: t for t in tables}
    for table in tables:
        for fk in table.foreign_keys:
            target = by_name.get(fk.referred_table)
            if target is not None and table.table_name not in target.referenced_by:
                target.referenced_by.append(table.table_name)
