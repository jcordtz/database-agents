import pytest

from db_agents.agents.table_agent import TableAgent
from db_agents.orchestrator import Orchestrator, QueryResult


class FakeEngines:
    def __init__(self, engines):
        self._engines = engines

    def get(self, name):
        return self._engines[name]


class FakeRegistry:
    def __init__(self, agents):
        self._agents = {a.id: a for a in agents}

    def all_agents(self):
        return list(self._agents.values())

    def catalog(self):
        return [a.to_catalog_entry() for a in self._agents.values()]

    def get(self, table_id):
        return self._agents[table_id]

    def engines(self):
        return self._engines_registry

    def set_engines(self, engines_registry):
        self._engines_registry = engines_registry


@pytest.fixture
def registry(orders_table, customers_table):
    orders_agent = TableAgent(metadata=orders_table, description="Orders desc")
    customers_agent = TableAgent(metadata=customers_table, description="Customers desc")
    return FakeRegistry([orders_agent, customers_agent])


def test_select_tables_filters_invalid_ids(registry, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete_json.return_value = {
        "table_ids": ["sales_postgres:public.orders", "does_not_exist:public.bogus"],
        "reasoning": "orders has the totals needed",
    }
    orchestrator = Orchestrator(registry, fake_llm)
    ids, reasoning = orchestrator.select_tables("What is the total order amount per customer?")
    assert ids == ["sales_postgres:public.orders"]
    assert reasoning == "orders has the totals needed"


def test_generate_sql_rejects_non_select(registry, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete_json.return_value = {"sql": "DELETE FROM orders"}
    orchestrator = Orchestrator(registry, fake_llm)
    with pytest.raises(ValueError, match="forbidden"):
        orchestrator._generate_sql_for_connection(
            "sales_postgres", "postgresql", ["sales_postgres:public.orders"], "how many orders?"
        )


def test_generate_sql_rejects_non_select_statement(registry, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete_json.return_value = {"sql": "EXEC sp_do_something"}
    orchestrator = Orchestrator(registry, fake_llm)
    with pytest.raises(ValueError, match="not a SELECT"):
        orchestrator._generate_sql_for_connection(
            "sales_postgres", "postgresql", ["sales_postgres:public.orders"], "how many orders?"
        )


def test_generate_sql_accepts_valid_select(registry, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete_json.return_value = {"sql": "SELECT id, total_amount FROM public.orders"}
    orchestrator = Orchestrator(registry, fake_llm)
    sql = orchestrator._generate_sql_for_connection(
        "sales_postgres", "postgresql", ["sales_postgres:public.orders"], "list order totals"
    )
    assert sql == "SELECT id, total_amount FROM public.orders"


def test_ask_returns_no_table_message_when_selection_empty(registry, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete_json.return_value = {"table_ids": [], "reasoning": "nothing relevant"}
    orchestrator = Orchestrator(registry, fake_llm)
    result = orchestrator.ask("What is the weather today?")
    assert result.selected_table_ids == []
    assert "could not identify" in result.answer.lower()


def test_ask_end_to_end_single_connection(registry, mocker):
    fake_llm = mocker.Mock()
    fake_llm.complete_json.side_effect = [
        {"table_ids": ["sales_postgres:public.orders"], "reasoning": "orders has totals"},
        {"sql": "SELECT customer_id, SUM(total_amount) AS total FROM public.orders GROUP BY customer_id"},
    ]
    fake_llm.complete.return_value = "Customer 1 spent $150 in total."

    orchestrator = Orchestrator(registry, fake_llm)
    mocker.patch.object(
        orchestrator,
        "_execute",
        return_value=QueryResult(
            connection_name="sales_postgres",
            sql="SELECT customer_id, SUM(total_amount) AS total FROM public.orders GROUP BY customer_id",
            columns=["customer_id", "total"],
            rows=[{"customer_id": 1, "total": 150.0}],
        ),
    )

    result = orchestrator.ask("What is the total spend per customer?")
    assert result.selected_table_ids == ["sales_postgres:public.orders"]
    assert len(result.queries) == 1
    assert result.queries[0].rows == [{"customer_id": 1, "total": 150.0}]
    assert result.answer == "Customer 1 spent $150 in total."
