"""Per-table agent: wraps one table's metadata + generated description and
knows how to describe itself and produce a SQL SELECT fragment for it."""
from __future__ import annotations

from pydantic import BaseModel

from db_agents.metadata.models import TableMetadata


class TableAgent(BaseModel):
    """A lightweight 'agent' representing a single database table.

    It doesn't call the LLM itself — the orchestrator drives the LLM calls —
    but it packages everything the orchestrator/LLM needs to reason about
    this table: its metadata and human-readable description.
    """

    metadata: TableMetadata
    description: str

    model_config = {"arbitrary_types_allowed": True}

    @property
    def id(self) -> str:
        return self.metadata.full_id

    @property
    def table_name(self) -> str:
        return self.metadata.table_name

    def quoted_name(self, dialect_quote: bool = True) -> str:
        """Fully-qualified table reference suitable for use in generated SQL."""
        schema = self.metadata.schema_name
        table = self.metadata.table_name
        return f"{schema}.{table}" if schema else table

    def column_names(self) -> list[str]:
        return [c.name for c in self.metadata.columns]

    def to_catalog_entry(self) -> dict:
        """Compact representation used when presenting this agent to the LLM
        for table-selection / SQL-generation prompts."""
        entry = {
            "id": self.id,
            "connection": self.metadata.connection_name,
            "dialect": self.metadata.dialect,
            "table": self.quoted_name(),
            "description": self.description,
            "columns": [
                {
                    "name": c.name,
                    "type": c.data_type,
                    "comment": c.comment,
                    **(
                        {
                            "purview_description": c.purview.description,
                            "purview_classifications": c.purview.classifications,
                        }
                        if c.purview is not None
                        else {}
                    ),
                }
                for c in self.metadata.columns
            ],
            "primary_key": self.metadata.primary_key,
            "foreign_keys": [
                {
                    "columns": fk.constrained_columns,
                    "references_table": fk.referred_table,
                    "references_columns": fk.referred_columns,
                }
                for fk in self.metadata.foreign_keys
            ],
            "referenced_by": self.metadata.referenced_by,
        }

        if self.metadata.purview is not None:
            entry["purview"] = {
                "qualified_name": self.metadata.purview.qualified_name,
                "description": self.metadata.purview.description,
                "classifications": self.metadata.purview.classifications,
                "glossary_terms": [t.name for t in self.metadata.purview.glossary_terms if t.name],
                "contacts": [
                    {"role": c.role, "identifier": c.identifier} for c in self.metadata.purview.contacts
                ],
                "source_url": self.metadata.purview.source_url,
            }

        return entry
