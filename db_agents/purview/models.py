"""Data models for information retrieved from Microsoft Purview."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PurviewGlossaryTerm(BaseModel):
    name: str
    guid: str | None = None


class PurviewContact(BaseModel):
    role: str
    identifier: str


class PurviewColumnInfo(BaseModel):
    qualified_name: str
    guid: str | None = None
    description: str | None = None
    classifications: list[str] = Field(default_factory=list)
    glossary_terms: list[PurviewGlossaryTerm] = Field(default_factory=list)


class PurviewAssetInfo(BaseModel):
    """Governance information about a single table (and optionally its
    columns) retrieved from Purview, to be merged with database-native
    metadata (comments, datatypes, FKs)."""

    qualified_name: str
    guid: str | None = None
    entity_type: str | None = None
    description: str | None = None
    classifications: list[str] = Field(default_factory=list)
    glossary_terms: list[PurviewGlossaryTerm] = Field(default_factory=list)
    contacts: list[PurviewContact] = Field(default_factory=list)
    columns: dict[str, PurviewColumnInfo] = Field(default_factory=dict)
    source_url: str | None = None
