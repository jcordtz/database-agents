"""Enriches introspected TableMetadata with governance information looked up
from Microsoft Purview, keyed by a qualified name built from the
database/schema/table (and column) information.
"""
from __future__ import annotations

import logging

from db_agents.config import DatabaseConnectionConfig, PurviewConfig
from db_agents.metadata.models import TableMetadata
from db_agents.purview.client import PurviewClient
from db_agents.purview.qualified_name import build_column_qualified_name, build_table_qualified_name

logger = logging.getLogger(__name__)


def is_purview_enabled_for(conn: DatabaseConnectionConfig, purview_config: PurviewConfig | None) -> bool:
    if purview_config is None or not purview_config.enabled:
        return False
    if conn.purview_enabled is not None:
        return conn.purview_enabled
    return True


def enrich_table_with_purview(
    table: TableMetadata,
    conn: DatabaseConnectionConfig,
    purview_config: PurviewConfig,
    client: PurviewClient,
) -> TableMetadata:
    """Look up the table (and its columns) in Purview by qualified name, and
    attach the resulting governance info to the metadata. Never raises --
    lookup failures are logged and the table is returned unchanged, since
    Purview data is an optional enrichment on top of database-native metadata."""
    try:
        table_qn = build_table_qualified_name(conn, table.schema_name, table.table_name)
    except Exception:
        logger.warning("Could not build Purview qualified name for %s", table.full_id, exc_info=True)
        return table

    table_entity_type = conn.purview_table_entity_type or purview_config.table_entity_type(conn.dialect)
    asset = client.lookup_table(table_qn, table_entity_type)
    if asset is None:
        return table

    column_entity_type = conn.purview_column_entity_type or purview_config.column_entity_type(conn.dialect)
    for column in table.columns:
        column_qn = build_column_qualified_name(table_qn, column.name)
        column_info = client.lookup_column(column_qn, column_entity_type)
        if column_info is not None:
            column.purview = column_info
            asset.columns[column.name] = column_info

    table.purview = asset
    return table
