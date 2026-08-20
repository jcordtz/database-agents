from db_agents.agents.description import build_structured_summary
from db_agents.agents.purview_enrichment import enrich_table_with_purview, is_purview_enabled_for
from db_agents.agents.table_agent import TableAgent
from db_agents.config import DatabaseConnectionConfig, PurviewConfig
from db_agents.purview.models import PurviewAssetInfo, PurviewColumnInfo, PurviewContact, PurviewGlossaryTerm


def test_is_purview_enabled_for_respects_global_and_per_connection_flags():
    conn = DatabaseConnectionConfig(name="sales_postgres", dialect="postgresql")

    assert is_purview_enabled_for(conn, None) is False
    assert is_purview_enabled_for(conn, PurviewConfig(enabled=False)) is False
    assert is_purview_enabled_for(conn, PurviewConfig(enabled=True, account_endpoint="https://x.purview.azure.com")) is True

    conn_disabled = DatabaseConnectionConfig(name="sales_postgres", dialect="postgresql", purview_enabled=False)
    assert is_purview_enabled_for(conn_disabled, PurviewConfig(enabled=True, account_endpoint="https://x")) is False


def test_enrich_table_with_purview_attaches_table_and_column_info(orders_table, mocker):
    conn = DatabaseConnectionConfig(
        name="sales_postgres", dialect="postgresql", host="pg.example.com", database="sales"
    )
    purview_config = PurviewConfig(enabled=True, account_endpoint="https://acct.purview.azure.com")

    table_asset = PurviewAssetInfo(
        qualified_name="postgresql://pg.example.com/sales/public/orders",
        guid="guid-1",
        description="Governed business description of orders.",
        classifications=["MICROSOFT.PERSONAL.NAME"],
        glossary_terms=[PurviewGlossaryTerm(name="Customer Order")],
        contacts=[PurviewContact(role="Owner", identifier="jcordtz@contoso.com")],
    )
    column_info = PurviewColumnInfo(
        qualified_name="postgresql://pg.example.com/sales/public/orders#customer_id",
        description="Foreign key to the customer.",
        classifications=["MICROSOFT.PERSONAL.ID"],
    )

    fake_client = mocker.Mock()
    fake_client.lookup_table.return_value = table_asset
    fake_client.lookup_column.side_effect = lambda qn, entity_type: (
        column_info if qn.endswith("#customer_id") else None
    )

    enriched = enrich_table_with_purview(orders_table, conn, purview_config, fake_client)

    assert enriched.purview is table_asset
    customer_id_col = next(c for c in enriched.columns if c.name == "customer_id")
    assert customer_id_col.purview is column_info
    other_col = next(c for c in enriched.columns if c.name == "total_amount")
    assert other_col.purview is None

    fake_client.lookup_table.assert_called_once()
    called_qn, called_entity_type = fake_client.lookup_table.call_args[0]
    assert called_qn == "postgresql://pg.example.com/sales/public/orders"
    assert called_entity_type == "postgresql_table"


def test_enrich_table_returns_unchanged_when_table_not_found_in_purview(orders_table, mocker):
    conn = DatabaseConnectionConfig(name="sales_postgres", dialect="postgresql", host="pg.example.com", database="sales")
    purview_config = PurviewConfig(enabled=True, account_endpoint="https://acct.purview.azure.com")

    fake_client = mocker.Mock()
    fake_client.lookup_table.return_value = None

    enriched = enrich_table_with_purview(orders_table, conn, purview_config, fake_client)
    assert enriched.purview is None
    fake_client.lookup_column.assert_not_called()


def test_structured_summary_includes_purview_classifications_and_glossary_terms(orders_table):
    orders_table.purview = PurviewAssetInfo(
        qualified_name="postgresql://pg.example.com/sales/public/orders",
        description="Governed description.",
        classifications=["MICROSOFT.PERSONAL.NAME", "Confidential"],
        glossary_terms=[PurviewGlossaryTerm(name="Customer Order")],
        contacts=[PurviewContact(role="Owner", identifier="jcordtz@contoso.com")],
    )
    summary = build_structured_summary(orders_table)
    assert "Governed description." in summary
    assert "Confidential" in summary
    assert "Customer Order" in summary
    assert "Owner: jcordtz@contoso.com" in summary


def test_catalog_entry_includes_purview_section_when_present(orders_table):
    orders_table.purview = PurviewAssetInfo(
        qualified_name="postgresql://pg.example.com/sales/public/orders",
        description="Governed description.",
        classifications=["Confidential"],
        glossary_terms=[PurviewGlossaryTerm(name="Customer Order")],
    )
    agent = TableAgent(metadata=orders_table, description="desc")
    entry = agent.to_catalog_entry()
    assert entry["purview"]["description"] == "Governed description."
    assert entry["purview"]["classifications"] == ["Confidential"]
    assert entry["purview"]["glossary_terms"] == ["Customer Order"]


def test_catalog_entry_omits_purview_section_when_absent(orders_table):
    agent = TableAgent(metadata=orders_table, description="desc")
    entry = agent.to_catalog_entry()
    assert "purview" not in entry
