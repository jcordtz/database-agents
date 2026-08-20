"""Registry that discovers all tables across configured connections, builds a
TableAgent per table, and caches generated descriptions (avoiding repeated
introspection + LLM calls) in a small local SQLite cache.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from db_agents.agents.description import generate_description
from db_agents.agents.purview_enrichment import enrich_table_with_purview, is_purview_enabled_for
from db_agents.agents.table_agent import TableAgent
from db_agents.config import AppConfig
from db_agents.connectors import EngineRegistry
from db_agents.llm import LLMClient
from db_agents.metadata import TableMetadata, introspect_connection
from db_agents.purview.client import PurviewClient


class AgentRegistry:
    def __init__(self, config: AppConfig, llm: LLMClient | None = None):
        self._config = config
        self._llm = llm
        self._engines = EngineRegistry(config.databases)
        self._agents: dict[str, TableAgent] = {}
        self._cache_path = Path(config.cache.path)
        self._init_cache()
        self._purview_client: PurviewClient | None = None

    def _get_purview_client(self) -> PurviewClient | None:
        if self._config.purview is None or not self._config.purview.enabled:
            return None
        if self._purview_client is None:
            self._purview_client = PurviewClient(self._config.purview)
        return self._purview_client

    # -- cache -----------------------------------------------------------
    def _init_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._cache_path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS table_cache (
                    full_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    description TEXT NOT NULL,
                    cached_at REAL NOT NULL
                )
                """
            )

    def _cache_get(self, full_id: str) -> tuple[TableMetadata, str] | None:
        ttl = self._config.cache.ttl_seconds
        with sqlite3.connect(self._cache_path) as db:
            row = db.execute(
                "SELECT metadata_json, description, cached_at FROM table_cache WHERE full_id = ?",
                (full_id,),
            ).fetchone()
        if row is None:
            return None
        metadata_json, description, cached_at = row
        if ttl > 0 and (time.time() - cached_at) > ttl:
            return None
        return TableMetadata.model_validate_json(metadata_json), description

    def _cache_put(self, full_id: str, metadata: TableMetadata, description: str) -> None:
        with sqlite3.connect(self._cache_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO table_cache (full_id, metadata_json, description, cached_at) "
                "VALUES (?, ?, ?, ?)",
                (full_id, metadata.model_dump_json(), description, time.time()),
            )

    # -- discovery ---------------------------------------------------------
    def refresh(self, force: bool = False) -> None:
        """(Re)discover all tables across all configured connections and
        (re)build TableAgents, using the cache unless force=True."""
        self._agents.clear()
        purview_client = self._get_purview_client()
        for conn_config in self._config.databases:
            engine = self._engines.get(conn_config.name)
            tables = introspect_connection(engine, conn_config)
            for table in tables:
                cached = None if force else self._cache_get(table.full_id)
                if cached is not None:
                    metadata, description = cached
                else:
                    if purview_client is not None and is_purview_enabled_for(conn_config, self._config.purview):
                        table = enrich_table_with_purview(table, conn_config, self._config.purview, purview_client)
                    metadata = table
                    description = generate_description(metadata, llm=self._llm)
                    self._cache_put(metadata.full_id, metadata, description)
                self._agents[metadata.full_id] = TableAgent(metadata=metadata, description=description)

    def ensure_loaded(self) -> None:
        if not self._agents:
            self.refresh()

    # -- accessors -----------------------------------------------------------
    def all_agents(self) -> list[TableAgent]:
        self.ensure_loaded()
        return list(self._agents.values())

    def get(self, full_id: str) -> TableAgent:
        self.ensure_loaded()
        return self._agents[full_id]

    def catalog(self) -> list[dict]:
        """Compact catalog of every table agent, suitable for passing to an LLM."""
        return [agent.to_catalog_entry() for agent in self.all_agents()]

    def engines(self) -> EngineRegistry:
        return self._engines

    def close(self) -> None:
        self._engines.dispose_all()
        if self._purview_client is not None:
            self._purview_client.close()
