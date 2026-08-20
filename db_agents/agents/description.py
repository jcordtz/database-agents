"""Generates human-readable descriptions of tables from introspected metadata.

Always builds a deterministic, structured description first (so the system
works with zero LLM dependency and is auditable), then optionally asks the
LLM to turn that structured summary into a polished natural-language
paragraph. The structured facts are never invented by the LLM — only
reworded.
"""
from __future__ import annotations

from db_agents.llm import LLMClient
from db_agents.metadata.models import TableMetadata

_SYSTEM_PROMPT = (
    "You write concise, accurate documentation for database tables. "
    "You are given structured facts about a table (its comment, columns, "
    "datatypes, and foreign key references), optionally supplemented with "
    "governance information from a data catalog (Microsoft Purview), such as "
    "a business description, data classifications (e.g. PII, confidential), "
    "and glossary terms. Rewrite these facts as a clear, human-readable "
    "description of what the table contains. "
    "Do not invent facts that are not present in the input. "
    "Keep it factual and concise (a short paragraph plus a bullet list of "
    "notable columns), mention which other tables it references and is "
    "referenced by, and call out any data classifications or glossary terms "
    "if present."
)


def build_structured_summary(table: TableMetadata) -> str:
    """Deterministic, template-based summary — no LLM required."""
    lines: list[str] = []
    lines.append(f"Table: {table.qualified_name} (connection: {table.connection_name}, dialect: {table.dialect})")
    if table.table_comment:
        lines.append(f"Table comment: {table.table_comment}")
    else:
        lines.append("Table comment: (none provided)")

    if table.purview is not None:
        lines.append("Data governance information (from Microsoft Purview):")
        if table.purview.description:
            lines.append(f"  - Business description: {table.purview.description}")
        if table.purview.classifications:
            lines.append(f"  - Classifications: {', '.join(sorted(set(table.purview.classifications)))}")
        if table.purview.glossary_terms:
            terms = ", ".join(t.name for t in table.purview.glossary_terms if t.name)
            if terms:
                lines.append(f"  - Glossary terms: {terms}")
        if table.purview.contacts:
            contacts = ", ".join(f"{c.role}: {c.identifier}" for c in table.purview.contacts)
            lines.append(f"  - Contacts: {contacts}")

    lines.append("Columns:")
    for col in table.columns:
        flags = []
        if col.is_primary_key:
            flags.append("PK")
        if not col.nullable:
            flags.append("NOT NULL")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        comment = f" -- {col.comment}" if col.comment else ""
        purview_bits = []
        if col.purview is not None:
            if col.purview.description:
                purview_bits.append(f"purview: {col.purview.description}")
            if col.purview.classifications:
                purview_bits.append("classifications: " + ", ".join(sorted(set(col.purview.classifications))))
        purview_str = f" ({'; '.join(purview_bits)})" if purview_bits else ""
        lines.append(f"  - {col.name}: {col.data_type}{flag_str}{comment}{purview_str}")

    if table.foreign_keys:
        lines.append("References (this table -> other tables):")
        for fk in table.foreign_keys:
            src = ", ".join(fk.constrained_columns)
            dst = ", ".join(fk.referred_columns)
            target = fk.referred_table if not fk.referred_schema else f"{fk.referred_schema}.{fk.referred_table}"
            lines.append(f"  - ({src}) -> {target}({dst})")
    else:
        lines.append("References (this table -> other tables): none")

    if table.referenced_by:
        lines.append(f"Referenced by other tables: {', '.join(sorted(set(table.referenced_by)))}")
    else:
        lines.append("Referenced by other tables: none known")

    return "\n".join(lines)


def generate_description(table: TableMetadata, llm: LLMClient | None = None) -> str:
    """Return a human-readable description of the table.

    If `llm` is provided, the structured summary is rewritten into prose by
    the model. Otherwise the structured summary itself (already readable) is
    returned as-is.
    """
    structured = build_structured_summary(table)
    if llm is None:
        return structured

    user_prompt = (
        "Here are the structured facts about a database table:\n\n"
        f"{structured}\n\n"
        "Write the human-readable description now."
    )
    return llm.complete(_SYSTEM_PROMPT, user_prompt)
