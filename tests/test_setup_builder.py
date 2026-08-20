import textwrap

import pytest
import yaml

from db_agents.config import AppConfig
from db_agents.setup_builder import (
    SetupBuilderError,
    build_app_config,
    config_to_yaml,
    env_skeleton,
    find_properties_file,
    normalise_dialect,
    parse_properties,
    parse_tables_csv,
)


@pytest.fixture()
def inputs(tmp_path):
    """A minimal but multi-dialect set of input files."""
    csv_path = tmp_path / "tables.csv"
    csv_path.write_text(
        textwrap.dedent(
            """\
            # tables that should get agents
            db_type,host,schema,table
            postgresql,pg.example.com,public,orders
            postgresql,pg.example.com,public,customers
            postgresql,pg.example.com,sales,quotes

            SQLServer,crm.example.com,dbo,Customers
            """
        ),
        encoding="utf-8",
    )

    connections = tmp_path / "connections"
    connections.mkdir()
    (connections / "pg.example.com.properties").write_text(
        textwrap.dedent(
            """\
            # postgres
            port=5432
            database=sales
            username=reader
            password_env=PG_PASSWORD
            driver.sslmode=require
            purview_source_host=pg.internal
            """
        ),
        encoding="utf-8",
    )
    (connections / "crm.example.com.properties").write_text(
        "database=CRM\nusername=crm\npassword_env=CRM_PASSWORD\n",
        encoding="utf-8",
    )
    return csv_path, connections


def test_normalise_dialect_accepts_aliases():
    assert normalise_dialect("SQL Server") == "mssql"
    assert normalise_dialect("Postgres") == "postgresql"
    assert normalise_dialect("IBM DB2") == "db2"
    assert normalise_dialect("oracle") == "oracle"


def test_normalise_dialect_rejects_unknown():
    with pytest.raises(SetupBuilderError, match="Unsupported database technology"):
        normalise_dialect("mongodb")


def test_parse_tables_csv_skips_comments_and_blanks(inputs):
    csv_path, _ = inputs
    rows = parse_tables_csv(csv_path)
    assert len(rows) == 4
    assert rows[0].dialect == "postgresql"
    assert rows[0].schema == "public"
    assert rows[3].dialect == "mssql"


def test_parse_tables_csv_deduplicates(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text(
        "db_type,host,schema,table\npostgresql,h,public,orders\npostgresql,h,public,orders\n",
        encoding="utf-8",
    )
    assert len(parse_tables_csv(csv_path)) == 1


def test_parse_tables_csv_accepts_column_aliases(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text(
        "Database Type,Hostname,Schema Name,Table Name\npostgresql,h,public,orders\n",
        encoding="utf-8",
    )
    rows = parse_tables_csv(csv_path)
    assert rows[0].table == "orders"


def test_parse_tables_csv_missing_column(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("db_type,host,table\npostgresql,h,orders\n", encoding="utf-8")
    with pytest.raises(SetupBuilderError, match="missing required column"):
        parse_tables_csv(csv_path)


def test_parse_tables_csv_missing_value(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("db_type,host,schema,table\npostgresql,,public,orders\n", encoding="utf-8")
    with pytest.raises(SetupBuilderError, match="line 2"):
        parse_tables_csv(csv_path)


def test_parse_properties_handles_comments_and_quotes(tmp_path):
    path = tmp_path / "h.properties"
    path.write_text('# c\n! also c\n\nport = 5432 \nname="my db"\n', encoding="utf-8")
    assert parse_properties(path) == {"port": "5432", "name": "my db"}


def test_parse_properties_rejects_malformed_line(tmp_path):
    path = tmp_path / "h.properties"
    path.write_text("port 5432\n", encoding="utf-8")
    with pytest.raises(SetupBuilderError, match="expected 'key=value'"):
        parse_properties(path)


def test_find_properties_file_falls_back_to_short_host(tmp_path):
    connections = tmp_path / "c"
    connections.mkdir()
    (connections / "pg.properties").write_text("database=x\n", encoding="utf-8")
    assert find_properties_file(connections, "pg.example.com").name == "pg.properties"


def test_find_properties_file_missing(tmp_path):
    connections = tmp_path / "c"
    connections.mkdir()
    with pytest.raises(SetupBuilderError, match="No connection properties file"):
        find_properties_file(connections, "nope.example.com")


def test_build_app_config_groups_rows_by_host(inputs):
    csv_path, connections = inputs
    result = build_app_config(csv_path, connections, llm_deployment="gpt-4o")

    assert result.table_count == 4
    assert result.dialects == {"postgresql", "mssql"}
    assert result.password_env_vars == ["CRM_PASSWORD", "PG_PASSWORD"]
    assert len(result.config.databases) == 2

    pg = next(c for c in result.config.databases if c.dialect == "postgresql")
    assert pg.database == "sales"
    assert pg.port == 5432
    assert pg.schemas == ["public", "sales"]
    assert pg.include_tables == ["public.customers", "public.orders", "sales.quotes"]
    assert pg.driver_options == {"sslmode": "require"}
    assert pg.purview_source_host == "pg.internal"

    mssql = next(c for c in result.config.databases if c.dialect == "mssql")
    # Port not given in the properties file, so the dialect default applies.
    assert mssql.port == 1433
    assert result.config.llm.deployment == "gpt-4o"


def test_build_app_config_requires_database_name(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("db_type,host,schema,table\npostgresql,h,public,orders\n", encoding="utf-8")
    connections = tmp_path / "c"
    connections.mkdir()
    (connections / "h.properties").write_text("username=x\n", encoding="utf-8")
    with pytest.raises(SetupBuilderError, match="No database name"):
        build_app_config(csv_path, connections)


def test_build_app_config_splits_multiple_databases_on_one_host(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text(
        "db_type,host,schema,table,database\n"
        "postgresql,h,public,orders,sales\n"
        "postgresql,h,public,tickets,support\n",
        encoding="utf-8",
    )
    connections = tmp_path / "c"
    connections.mkdir()
    (connections / "h.properties").write_text("database=sales\npassword_env=P\n", encoding="utf-8")

    result = build_app_config(csv_path, connections)
    assert len(result.config.databases) == 2
    names = sorted(c.name for c in result.config.databases)
    assert names == ["h_postgresql_sales", "h_postgresql_support"]


def test_build_app_config_invalid_port(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("db_type,host,schema,table\npostgresql,h,public,orders\n", encoding="utf-8")
    connections = tmp_path / "c"
    connections.mkdir()
    (connections / "h.properties").write_text("database=d\nport=abc\n", encoding="utf-8")
    with pytest.raises(SetupBuilderError, match="Invalid port"):
        build_app_config(csv_path, connections)


def test_generated_yaml_round_trips_into_app_config(inputs):
    csv_path, connections = inputs
    result = build_app_config(csv_path, connections, llm_deployment="gpt-4o")
    text = config_to_yaml(result.config)

    # No secrets should ever end up in the generated config.
    assert "password:" not in text

    reloaded = AppConfig.model_validate(yaml.safe_load(text))
    assert len(reloaded.databases) == 2
    assert reloaded.llm.deployment == "gpt-4o"


def test_env_skeleton_lists_every_referenced_variable(inputs):
    csv_path, connections = inputs
    result = build_app_config(csv_path, connections, llm_deployment="gpt-4o")
    text = env_skeleton(result, purview_enabled=True, llm_configured=True)

    for expected in (
        "PG_PASSWORD=",
        "CRM_PASSWORD=",
        "AZURE_OPENAI_API_KEY=",
        "PURVIEW_CLIENT_SECRET=",
        "DB_AGENTS_CONFIG=config.yaml",
    ):
        assert expected in text


def test_env_skeleton_omits_disabled_sections(inputs):
    csv_path, connections = inputs
    result = build_app_config(csv_path, connections)
    text = env_skeleton(result, purview_enabled=False, llm_configured=False)
    assert "PURVIEW_CLIENT_SECRET" not in text
    assert "AZURE_OPENAI_API_KEY" not in text
