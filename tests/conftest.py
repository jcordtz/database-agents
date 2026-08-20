import pytest

from db_agents.metadata.models import ColumnMetadata, ForeignKeyMetadata, TableMetadata


@pytest.fixture
def orders_table() -> TableMetadata:
    return TableMetadata(
        connection_name="sales_postgres",
        dialect="postgresql",
        schema_name="public",
        table_name="orders",
        table_comment="Customer orders placed through the web storefront.",
        columns=[
            ColumnMetadata(name="id", data_type="INTEGER", nullable=False, is_primary_key=True, comment="Order id"),
            ColumnMetadata(name="customer_id", data_type="INTEGER", nullable=False, comment="FK to customers"),
            ColumnMetadata(name="total_amount", data_type="NUMERIC(10,2)", nullable=False, comment="Order total in USD"),
            ColumnMetadata(name="created_at", data_type="TIMESTAMP", nullable=False, comment=None),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKeyMetadata(
                constrained_columns=["customer_id"],
                referred_schema="public",
                referred_table="customers",
                referred_columns=["id"],
            )
        ],
        referenced_by=["order_items"],
    )


@pytest.fixture
def customers_table() -> TableMetadata:
    return TableMetadata(
        connection_name="sales_postgres",
        dialect="postgresql",
        schema_name="public",
        table_name="customers",
        table_comment="Registered customers.",
        columns=[
            ColumnMetadata(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
            ColumnMetadata(name="name", data_type="VARCHAR(200)", nullable=False, comment="Full name"),
        ],
        primary_key=["id"],
        foreign_keys=[],
        referenced_by=["orders"],
    )
