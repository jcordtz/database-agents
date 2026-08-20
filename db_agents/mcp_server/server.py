"""MCP server exposing the database agents as tools.

Tools exposed:
  - list_tables: list all discovered table agents (id, connection, table name, short description)
  - describe_table: full human-readable description + schema for one table
  - refresh_metadata: re-introspect all configured databases and regenerate descriptions
  - ask_question: ask a natural-language question spanning one or more tables;
      the orchestrator selects tables, generates SQL, executes it, and returns
      a synthesized answer plus the underlying query results for transparency.
"""
from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from db_agents.agents import AgentRegistry
from db_agents.config import load_config
from db_agents.llm import LLMClient
from db_agents.orchestrator import Orchestrator

mcp = FastMCP("db-agents")

_state: dict = {}


def _get_registry() -> AgentRegistry:
    if "registry" not in _state:
        config_path = os.environ.get("DB_AGENTS_CONFIG", "config.yaml")
        config = load_config(config_path)
        llm = LLMClient(config.llm) if config.llm else None
        _state["registry"] = AgentRegistry(config, llm=llm)
        _state["llm"] = llm
    return _state["registry"]


def _get_orchestrator() -> Orchestrator:
    if "orchestrator" not in _state:
        registry = _get_registry()
        llm = _state.get("llm")
        if llm is None:
            raise RuntimeError(
                "No LLM configured. Set 'llm' in config.yaml to enable ask_question."
            )
        _state["orchestrator"] = Orchestrator(registry, llm)
    return _state["orchestrator"]


@mcp.tool()
def list_tables() -> list[dict]:
    """List every discovered table agent with a short summary of what it contains."""
    registry = _get_registry()
    return [
        {
            "id": agent.id,
            "connection": agent.metadata.connection_name,
            "dialect": agent.metadata.dialect,
            "table": agent.quoted_name(),
            "summary": agent.description.strip().splitlines()[0] if agent.description else "",
        }
        for agent in registry.all_agents()
    ]


@mcp.tool()
def describe_table(table_id: str) -> dict:
    """Return the full human-readable description and schema for one table.

    table_id is the value returned by list_tables, e.g. 'my_connection:public.orders'.
    """
    registry = _get_registry()
    agent = registry.get(table_id)
    return agent.to_catalog_entry()


@mcp.tool()
def refresh_metadata(force: bool = False) -> dict:
    """Re-introspect all configured databases and regenerate table descriptions.

    Set force=True to bypass the cache and regenerate everything, including
    calling the LLM again for each table's description.
    """
    registry = _get_registry()
    registry.refresh(force=force)
    return {"tables_discovered": len(registry.all_agents())}


@mcp.tool()
def ask_question(question: str, row_limit: int = 1000) -> dict:
    """Ask a natural-language question that may span multiple tables.

    Selects the relevant table(s), generates and executes read-only SQL
    against the appropriate database connection(s), and returns a synthesized
    answer along with the SQL and row data used to derive it.
    """
    orchestrator = _get_orchestrator()
    result = orchestrator.ask(question, row_limit=row_limit)
    return {
        "question": result.question,
        "selected_tables": result.selected_table_ids,
        "selection_reasoning": result.selection_reasoning,
        "queries": [
            {
                "connection": q.connection_name,
                "sql": q.sql,
                "columns": q.columns,
                "row_count": len(q.rows),
                "rows": q.rows,
            }
            for q in result.queries
        ],
        "answer": result.answer,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
