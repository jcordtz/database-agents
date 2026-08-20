"""Data models representing introspected table/column metadata."""
from __future__ import annotations

from pydantic import BaseModel, Field

from db_agents.purview.models import PurviewAssetInfo, PurviewColumnInfo


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False
    comment: str | None = None
    # Governance information from Microsoft Purview, if configured/found.
    purview: PurviewColumnInfo | None = None


class ForeignKeyMetadata(BaseModel):
    """A reference from the owning table to another table."""

    constrained_columns: list[str]
    referred_schema: str | None = None
    referred_table: str
    referred_columns: list[str]


class TableMetadata(BaseModel):
    connection_name: str
    dialect: str
    schema_name: str | None = None
    table_name: str
    table_comment: str | None = None
    columns: list[ColumnMetadata] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyMetadata] = Field(default_factory=list)
    # Tables that reference *this* table (reverse FKs), filled in by the registry
    # once all tables in a connection have been introspected.
    referenced_by: list[str] = Field(default_factory=list)
    # Governance information from Microsoft Purview (table-level), if configured/found.
    purview: PurviewAssetInfo | None = None

    @property
    def qualified_name(self) -> str:
        parts = [p for p in (self.schema_name, self.table_name) if p]
        return ".".join(parts)

    @property
    def full_id(self) -> str:
        """Globally unique identifier across all configured connections."""
        return f"{self.connection_name}:{self.qualified_name}"
