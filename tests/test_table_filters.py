from db_agents.metadata.introspector import _is_excluded, _is_included


def test_no_include_list_means_all_tables():
    assert _is_included("public", "orders", None) is True
    assert _is_included("public", "orders", []) is True


def test_include_matches_bare_and_qualified_names():
    patterns = ["public.orders", "customers"]
    assert _is_included("public", "orders", patterns) is True
    assert _is_included("public", "customers", patterns) is True
    assert _is_included("sales", "customers", patterns) is True
    assert _is_included("public", "invoices", patterns) is False


def test_include_is_case_insensitive_for_oracle_style_names():
    assert _is_included("FINANCE", "GL_ACCOUNTS", ["finance.gl_accounts"]) is True
    assert _is_included("finance", "gl_accounts", ["FINANCE.GL_ACCOUNTS"]) is True


def test_include_supports_globs():
    assert _is_included("public", "order_lines", ["public.order*"]) is True
    assert _is_included("public", "customers", ["public.order*"]) is False


def test_include_does_not_cross_schemas_when_qualified():
    assert _is_included("archive", "orders", ["public.orders"]) is False


def test_exclude_still_applies():
    assert _is_excluded("tmp_orders", ["tmp_*"]) is True
    assert _is_excluded("orders", ["tmp_*"]) is False
