from db_agents.agents.table_agent import TableAgent


def test_table_agent_catalog_entry(orders_table):
    agent = TableAgent(metadata=orders_table, description="Customer orders.")
    entry = agent.to_catalog_entry()

    assert entry["id"] == "sales_postgres:public.orders"
    assert entry["table"] == "public.orders"
    assert entry["description"] == "Customer orders."
    assert {"name": "customer_id", "type": "INTEGER", "comment": "FK to customers"} in entry["columns"]
    assert entry["primary_key"] == ["id"]
    assert entry["foreign_keys"] == [
        {"columns": ["customer_id"], "references_table": "customers", "references_columns": ["id"]}
    ]
    assert entry["referenced_by"] == ["order_items"]


def test_table_agent_quoted_name_without_schema(orders_table):
    orders_table.schema_name = None
    agent = TableAgent(metadata=orders_table, description="desc")
    assert agent.quoted_name() == "orders"
