from db_agents.config import DatabaseConnectionConfig
from db_agents.purview.qualified_name import build_column_qualified_name, build_table_qualified_name


def test_postgresql_qualified_name_uses_host_database_schema_table():
    conn = DatabaseConnectionConfig(
        name="sales_postgres", dialect="postgresql", host="pg.example.com", port=5432, database="sales"
    )
    qn = build_table_qualified_name(conn, "public", "orders")
    assert qn == "postgresql://pg.example.com/sales/public/orders"


def test_mssql_qualified_name_has_no_port():
    conn = DatabaseConnectionConfig(name="crm", dialect="mssql", host="crm-db.example.com", database="CRM")
    qn = build_table_qualified_name(conn, "dbo", "Customers")
    assert qn == "mssql://crm-db.example.com/CRM/dbo/Customers"


def test_oracle_qualified_name_includes_port():
    conn = DatabaseConnectionConfig(
        name="finance", dialect="oracle", host="finance-db.example.com", port=1521, database="FINPDB"
    )
    qn = build_table_qualified_name(conn, "FINANCE", "GL_ACCOUNTS")
    assert qn == "oracle://finance-db.example.com:1521/FINPDB/FINANCE/GL_ACCOUNTS"


def test_db2_qualified_name_uses_default_port_when_not_set():
    conn = DatabaseConnectionConfig(name="legacy", dialect="db2", host="legacy-db.example.com", database="LEGACYDB")
    qn = build_table_qualified_name(conn, "LEGACY", "ORDERS")
    assert qn == "db2://legacy-db.example.com:50000/LEGACYDB/LEGACY/ORDERS"


def test_purview_source_host_and_database_override_take_precedence():
    conn = DatabaseConnectionConfig(
        name="sales_postgres",
        dialect="postgresql",
        host="internal-pg.corp.local",
        database="sales_db",
        purview_source_host="pg.example.com",
        purview_database="sales",
    )
    qn = build_table_qualified_name(conn, "public", "orders")
    assert qn == "postgresql://pg.example.com/sales/public/orders"


def test_custom_qualified_name_template_override():
    conn = DatabaseConnectionConfig(
        name="sales_postgres",
        dialect="postgresql",
        host="pg.example.com",
        database="sales",
        purview_qualified_name_template="custom://{host}/{database}.{schema}.{table}",
    )
    qn = build_table_qualified_name(conn, "public", "orders")
    assert qn == "custom://pg.example.com/sales.public.orders"


def test_column_qualified_name_appends_hash_and_column():
    assert build_column_qualified_name("postgresql://pg.example.com/sales/public/orders", "customer_id") == (
        "postgresql://pg.example.com/sales/public/orders#customer_id"
    )
