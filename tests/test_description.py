from db_agents.agents.description import build_structured_summary, generate_description


def test_structured_summary_includes_comment_and_columns(orders_table):
    summary = build_structured_summary(orders_table)
    assert "orders" in summary
    assert "Customer orders placed through the web storefront." in summary
    assert "customer_id: INTEGER" in summary
    assert "FK to customers" in summary
    assert "-> public.customers(id)" in summary
    assert "order_items" in summary  # referenced_by


def test_structured_summary_handles_no_comment_and_no_fks(customers_table):
    customers_table.table_comment = None
    customers_table.referenced_by = []
    summary = build_structured_summary(customers_table)
    assert "(none provided)" in summary
    assert "Referenced by other tables: none known" in summary


def test_generate_description_without_llm_returns_structured_summary(orders_table):
    description = generate_description(orders_table, llm=None)
    assert description == build_structured_summary(orders_table)


def test_generate_description_with_llm_calls_complete(orders_table, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete.return_value = "This table stores customer orders."
    description = generate_description(orders_table, llm=fake_llm)
    assert description == "This table stores customer orders."
    fake_llm.complete.assert_called_once()
    system, user = fake_llm.complete.call_args[0]
    assert "orders" in user
