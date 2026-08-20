"""Configuration models and loaders for database connections and LLM settings."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

Dialect = Literal["mssql", "oracle", "postgresql", "db2"]


class DatabaseConnectionConfig(BaseModel):
    """Connection details for a single database.

    Passwords/secrets should be supplied via environment variables
    (referenced here through ``password_env``) rather than stored in plain
    YAML, so this config can be committed safely.
    """

    name: str = Field(..., description="Unique logical name for this connection, e.g. 'sales_mssql'")
    dialect: Dialect
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password_env: Optional[str] = Field(
        default=None, description="Name of the environment variable holding the password"
    )
    # Escape hatch: a fully-formed SQLAlchemy URL overrides host/port/etc.
    sqlalchemy_url: Optional[str] = None
    # Extra driver-specific query params, e.g. {"driver": "ODBC Driver 18 for SQL Server"}
    driver_options: dict = Field(default_factory=dict)
    # Restrict introspection to specific schemas; None means "all schemas visible to the user"
    schemas: Optional[list[str]] = None
    # Table name patterns to exclude from introspection (SQL LIKE-style, simple glob supported)
    exclude_tables: list[str] = Field(default_factory=list)

    @property
    def password(self) -> Optional[str]:
        if self.password_env:
            return os.environ.get(self.password_env)
        return None

    def build_url(self) -> str:
        """Build (or pass through) the SQLAlchemy connection URL."""
        if self.sqlalchemy_url:
            return self.sqlalchemy_url

        drivername_map = {
            "mssql": "mssql+pyodbc",
            "oracle": "oracle+oracledb",
            "postgresql": "postgresql+psycopg",
            "db2": "ibm_db_sa",
        }
        drivername = drivername_map[self.dialect]

        from sqlalchemy.engine import URL

        query = dict(self.driver_options)
        if self.dialect == "mssql" and "driver" not in query:
            query["driver"] = "ODBC Driver 18 for SQL Server"

        url = URL.create(
            drivername=drivername,
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
            query=query,
        )
        return url.render_as_string(hide_password=False)


class LLMConfig(BaseModel):
    provider: Literal["azure_openai"] = "azure_openai"
    azure_endpoint_env: str = "AZURE_OPENAI_ENDPOINT"
    api_key_env: str = "AZURE_OPENAI_API_KEY"
    api_version: str = "2024-10-21"
    deployment: str = Field(..., description="Azure OpenAI deployment/model name to use")

    @property
    def endpoint(self) -> Optional[str]:
        return os.environ.get(self.azure_endpoint_env)

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env)


class CacheConfig(BaseModel):
    path: str = ".db_agents_cache.sqlite3"
    ttl_seconds: int = 24 * 3600


class AppConfig(BaseModel):
    databases: list[DatabaseConnectionConfig]
    llm: Optional[LLMConfig] = None
    cache: CacheConfig = Field(default_factory=CacheConfig)

    @model_validator(mode="after")
    def _unique_names(self) -> "AppConfig":
        names = [d.name for d in self.databases]
        if len(names) != len(set(names)):
            raise ValueError("database connection 'name' values must be unique")
        return self

    def get_database(self, name: str) -> DatabaseConnectionConfig:
        for d in self.databases:
            if d.name == name:
                return d
        raise KeyError(f"No database connection named '{name}' in config")


def load_config(path: str | Path) -> AppConfig:
    """Load an AppConfig from a YAML file, expanding ${ENV_VAR} references."""
    path = Path(path)
    raw = path.read_text()
    raw = os.path.expandvars(raw)
    data = yaml.safe_load(raw) or {}
    return AppConfig.model_validate(data)
