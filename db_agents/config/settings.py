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
    # Restrict introspection to an explicit set of tables. Entries may be a
    # bare table name ("orders") or schema-qualified ("public.orders"), and
    # simple globs are supported. None/empty means "all tables in the
    # selected schemas". Used by the CSV-driven setup generator, which lists
    # exactly which tables should get an agent.
    include_tables: Optional[list[str]] = None
    # Table name patterns to exclude from introspection (SQL LIKE-style, simple glob supported)
    exclude_tables: list[str] = Field(default_factory=list)

    # -- Purview overrides -------------------------------------------------
    # Whether to attempt a Purview lookup for tables in this connection.
    # Defaults to the global purview.enabled setting when not set here.
    purview_enabled: Optional[bool] = None
    # The host/server component used when this source was registered in
    # Purview (e.g. the MSSQL server FQDN, or the Oracle/Postgres/DB2 host).
    # Defaults to `host` if not given -- override when the registered source
    # uses a different hostname/alias than the one used to connect here.
    purview_source_host: Optional[str] = None
    # The database name as registered in Purview, if different from `database`.
    purview_database: Optional[str] = None
    # Purview entity typeName for tables/views registered from this source,
    # e.g. "azure_sql_table", "oracle_table", "postgresql_table", "db2_table".
    # If not set, a sensible default is derived from `dialect`.
    purview_table_entity_type: Optional[str] = None
    purview_column_entity_type: Optional[str] = None
    # Optional custom qualifiedName template, e.g.
    # "mssql://{host}/{database}/{schema}/{table}". Overrides the built-in
    # per-dialect default when your Purview account uses a non-standard
    # scan/registration convention. Supports {host}, {port}, {database},
    # {schema}, {table} placeholders.
    purview_qualified_name_template: Optional[str] = None

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


class PurviewConfig(BaseModel):
    """Connection + auth settings for Microsoft Purview Data Governance lookups.

    Secrets are read from environment variables only, following the same
    pattern as database passwords, so this config can be committed safely.
    """

    enabled: bool = False
    # e.g. "https://<account-name>.purview.azure.com" (classic Data Map/Atlas
    # endpoint) or your account's unified catalog endpoint.
    account_endpoint: str = Field(
        default="", description="Purview account endpoint, e.g. https://<account-name>.purview.azure.com"
    )
    tenant_id_env: str = "PURVIEW_TENANT_ID"
    client_id_env: str = "PURVIEW_CLIENT_ID"
    client_secret_env: str = "PURVIEW_CLIENT_SECRET"
    api_version: str = "2023-09-01"
    # Default Atlas entity typeNames per dialect for tables/columns; can be
    # overridden per-connection via DatabaseConnectionConfig.purview_*_entity_type.
    default_table_entity_types: dict[str, str] = Field(
        default_factory=lambda: {
            "mssql": "azure_sql_table",
            "oracle": "oracle_table",
            "postgresql": "postgresql_table",
            "db2": "db2_table",
        }
    )
    default_column_entity_types: dict[str, str] = Field(
        default_factory=lambda: {
            "mssql": "azure_sql_column",
            "oracle": "oracle_column",
            "postgresql": "postgresql_column",
            "db2": "db2_column",
        }
    )
    # Network/lookup resilience: don't let a slow/unreachable Purview
    # instance block table-agent creation.
    request_timeout_seconds: float = 10.0
    fail_silently: bool = True

    @property
    def tenant_id(self) -> Optional[str]:
        return os.environ.get(self.tenant_id_env)

    @property
    def client_id(self) -> Optional[str]:
        return os.environ.get(self.client_id_env)

    @property
    def client_secret(self) -> Optional[str]:
        return os.environ.get(self.client_secret_env)

    def table_entity_type(self, dialect: str) -> str:
        return self.default_table_entity_types.get(dialect, "table")

    def column_entity_type(self, dialect: str) -> str:
        return self.default_column_entity_types.get(dialect, "column")


class AppConfig(BaseModel):
    databases: list[DatabaseConnectionConfig]
    llm: Optional[LLMConfig] = None
    cache: CacheConfig = Field(default_factory=CacheConfig)
    purview: Optional[PurviewConfig] = None

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
