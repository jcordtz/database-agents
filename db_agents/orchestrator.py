"""Orchestrates cross-table question answering:

1. Ask the LLM to select which table agents (and only those) are relevant to
   the user's question, given the catalog of table descriptions.
2. Group the selected tables by database connection. For tables that share a
   connection, ask the LLM to generate a single joined SQL SELECT statement
   for that connection. Tables from different connections cannot be joined
   in SQL, so each connection gets its own generated query.
3. Execute each generated SQL statement (read-only) against its connection.
4. If more than one connection was involved, combine the resulting datasets
   in-memory (pandas) using the foreign-key relationships as the join hints.
5. Ask the LLM to synthesize a final natural-language answer from the
   combined data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import text

from db_agents.agents.registry import AgentRegistry
from db_agents.llm import LLMClient

_SELECT_SYSTEM_PROMPT = (
    "You are a data analyst assistant. You are given a catalog of database "
    "tables (each with a description, columns, and foreign key references) "
    "and a user's question. Identify the minimal set of tables required to "
    "answer the question. Respond ONLY with a JSON object of the form "
    '{"table_ids": ["<id>", ...], "reasoning": "<short reasoning>"} '
    "where each <id> is exactly one of the 'id' values from the catalog. "
    "Never invent a table id that is not in the catalog."
)

_SQL_SYSTEM_PROMPT = (
    "You are an expert {dialect} SQL developer. You are given the schema "
    "(tables, columns, types, primary keys and foreign keys) for a subset of "
    "tables in a single database, plus a user's question. Write a single "
    "read-only SELECT statement (using JOINs as needed based on the given "
    "foreign keys) that retrieves the data needed to answer the question. "
    "Only use tables and columns that are given to you. Respond ONLY with a "
    'JSON object: {{"sql": "<the SELECT statement>"}}. Do not include DDL or '
    "DML statements, and never modify data."
)

_ANSWER_SYSTEM_PROMPT = (
    "You are a helpful data analyst. You are given the user's original "
    "question and the data retrieved from one or more database queries "
    "(as JSON records, possibly one dataset per source table set). Write a "
    "clear, concise natural-language answer to the question, using only the "
    "provided data. If the data is insufficient to fully answer, say so."
)

_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|merge|grant|revoke|create)\b",
    re.IGNORECASE,
)


@dataclass
class QueryResult:
    connection_name: str
    sql: str
    columns: list[str]
    rows: list[dict] = field(default_factory=list)


@dataclass
class OrchestratorResult:
    question: str
    selected_table_ids: list[str]
    selection_reasoning: str
    queries: list[QueryResult]
    answer: str


class Orchestrator:
    def __init__(self, registry: AgentRegistry, llm: LLMClient):
        self._registry = registry
        self._llm = llm

    # -- step 1: table selection ------------------------------------------------
    def select_tables(self, question: str) -> tuple[list[str], str]:
        catalog = self._registry.catalog()
        user_prompt = (
            f"Catalog of available tables (JSON):\n{json.dumps(catalog, indent=2)}\n\n"
            f"User question: {question}"
        )
        result = self._llm.complete_json(_SELECT_SYSTEM_PROMPT, user_prompt)
        table_ids = result.get("table_ids", [])
        valid_ids = {a.id for a in self._registry.all_agents()}
        table_ids = [t for t in table_ids if t in valid_ids]
        return table_ids, result.get("reasoning", "")

    # -- step 2: per-connection SQL generation -----------------------------------
    def _generate_sql_for_connection(self, connection_name: str, dialect: str, table_ids: list[str], question: str) -> str:
        agents = [self._registry.get(tid) for tid in table_ids]
        schema_payload = [a.to_catalog_entry() for a in agents]
        system = _SQL_SYSTEM_PROMPT.format(dialect=dialect)
        user_prompt = (
            f"Tables available in this database (JSON):\n{json.dumps(schema_payload, indent=2)}\n\n"
            f"User question: {question}"
        )
        result = self._llm.complete_json(system, user_prompt)
        sql = result.get("sql", "").strip().rstrip(";")
        if not sql:
            raise ValueError(f"LLM did not return SQL for connection '{connection_name}'")
        if _FORBIDDEN_SQL.search(sql):
            raise ValueError(f"Generated SQL for '{connection_name}' contains a forbidden statement: {sql}")
        if not sql.lower().startswith(("select", "with")):
            raise ValueError(f"Generated SQL for '{connection_name}' is not a SELECT statement: {sql}")
        return sql

    # -- step 3: execution -----------------------------------------------------
    def _execute(self, connection_name: str, sql: str, row_limit: int = 1000) -> QueryResult:
        engine = self._registry.engines().get(connection_name)
        with engine.connect() as conn:
            cursor_result = conn.execute(text(sql))
            columns = list(cursor_result.keys())
            rows = [dict(zip(columns, row)) for row in cursor_result.fetchmany(row_limit)]
        return QueryResult(connection_name=connection_name, sql=sql, columns=columns, rows=rows)

    # -- step 4: combine (only meaningful for multi-connection questions) -------
    @staticmethod
    def _combine(query_results: list[QueryResult]) -> list[dict]:
        """Best-effort combination of results from multiple connections.

        Since a true relational join across separate DB engines is out of
        scope, we return each dataset labeled by its connection so the LLM
        can reason over them jointly when synthesizing the final answer.
        """
        combined = []
        for qr in query_results:
            combined.append({"connection": qr.connection_name, "sql": qr.sql, "rows": qr.rows})
        return combined

    # -- step 5: synthesis -------------------------------------------------------
    def _synthesize_answer(self, question: str, query_results: list[QueryResult]) -> str:
        combined = self._combine(query_results)
        user_prompt = (
            f"Question: {question}\n\n"
            f"Retrieved data (JSON):\n{json.dumps(combined, indent=2, default=str)}"
        )
        return self._llm.complete(_ANSWER_SYSTEM_PROMPT, user_prompt)

    # -- public entry point ------------------------------------------------------
    def ask(self, question: str, row_limit: int = 1000) -> OrchestratorResult:
        table_ids, reasoning = self.select_tables(question)
        if not table_ids:
            return OrchestratorResult(
                question=question,
                selected_table_ids=[],
                selection_reasoning=reasoning,
                queries=[],
                answer="I could not identify any relevant tables to answer this question.",
            )

        agents = [self._registry.get(tid) for tid in table_ids]
        by_connection: dict[str, list[str]] = {}
        dialect_by_connection: dict[str, str] = {}
        for agent in agents:
            by_connection.setdefault(agent.metadata.connection_name, []).append(agent.id)
            dialect_by_connection[agent.metadata.connection_name] = agent.metadata.dialect

        query_results: list[QueryResult] = []
        for connection_name, ids in by_connection.items():
            sql = self._generate_sql_for_connection(
                connection_name, dialect_by_connection[connection_name], ids, question
            )
            query_results.append(self._execute(connection_name, sql, row_limit=row_limit))

        answer = self._synthesize_answer(question, query_results)

        return OrchestratorResult(
            question=question,
            selected_table_ids=table_ids,
            selection_reasoning=reasoning,
            queries=query_results,
            answer=answer,
        )
