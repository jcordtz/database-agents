"""SQLAlchemy engine factory for the supported dialects."""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from db_agents.config import DatabaseConnectionConfig

# Required driver package per dialect, surfaced in error messages so users know
# what to `pip install` for the database they intend to connect to.
_REQUIRED_PACKAGES = {
    "mssql": "pyodbc (pip install 'db-agents[mssql]')",
    "oracle": "oracledb (pip install 'db-agents[oracle]')",
    "postgresql": "psycopg (pip install 'db-agents[postgresql]')",
    "db2": "ibm_db + ibm_db_sa (pip install 'db-agents[db2]')",
}


def build_engine(conn: DatabaseConnectionConfig, **engine_kwargs) -> Engine:
    """Create a SQLAlchemy engine for the given connection config.

    Raises a clear ImportError-derived message if the dialect driver isn't
    installed, rather than a cryptic SQLAlchemy NoSuchModuleError.
    """
    url = conn.build_url()
    try:
        return create_engine(url, **engine_kwargs)
    except ModuleNotFoundError as exc:
        pkg = _REQUIRED_PACKAGES.get(conn.dialect, "the appropriate driver package")
        raise ModuleNotFoundError(
            f"Missing driver for dialect '{conn.dialect}'. Install {pkg}."
        ) from exc


class EngineRegistry:
    """Lazily creates and caches one engine per named connection."""

    def __init__(self, connections: list[DatabaseConnectionConfig]):
        self._connections = {c.name: c for c in connections}
        self._engines: dict[str, Engine] = {}

    def get(self, name: str) -> Engine:
        if name not in self._engines:
            conn = self._connections[name]
            self._engines[name] = build_engine(conn)
        return self._engines[name]

    def connection_config(self, name: str) -> DatabaseConnectionConfig:
        return self._connections[name]

    def names(self) -> list[str]:
        return list(self._connections.keys())

    def dispose_all(self) -> None:
        for engine in self._engines.values():
            engine.dispose()
        self._engines.clear()
