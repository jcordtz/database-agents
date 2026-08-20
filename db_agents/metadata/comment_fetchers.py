"""Dialect-specific raw-SQL fetchers for table/column comments.

SQLAlchemy's generic Inspector does not uniformly expose comments across all
four target dialects (notably MSSQL and DB2), so each dialect gets a small,
explicit query here. Every function returns:
    table_comments: dict[(schema, table) -> comment]
    column_comments: dict[(schema, table, column) -> comment]
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def fetch_postgresql_comments(conn: Connection, schemas: list[str] | None):
    table_comments: dict[tuple[str, str], str] = {}
    column_comments: dict[tuple[str, str, str], str] = {}

    schema_filter = ""
    params: dict = {}
    if schemas:
        schema_filter = "AND n.nspname = ANY(:schemas)"
        params["schemas"] = schemas
    else:
        schema_filter = "AND n.nspname NOT IN ('pg_catalog', 'information_schema')"

    table_sql = f"""
        SELECT n.nspname AS schema_name, c.relname AS table_name,
               obj_description(c.oid) AS comment
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'v', 'm') {schema_filter}
    """
    for row in conn.execute(text(table_sql), params):
        if row.comment:
            table_comments[(row.schema_name, row.table_name)] = row.comment

    column_sql = f"""
        SELECT n.nspname AS schema_name, c.relname AS table_name,
               a.attname AS column_name,
               col_description(c.oid, a.attnum) AS comment
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE c.relkind IN ('r', 'v', 'm') {schema_filter}
    """
    for row in conn.execute(text(column_sql), params):
        if row.comment:
            column_comments[(row.schema_name, row.table_name, row.column_name)] = row.comment

    return table_comments, column_comments


def fetch_mssql_comments(conn: Connection, schemas: list[str] | None):
    table_comments: dict[tuple[str, str], str] = {}
    column_comments: dict[tuple[str, str, str], str] = {}

    schema_filter = ""
    params: dict = {}
    if schemas:
        schema_filter = "AND s.name IN :schemas"
        # MSSQL/pyodbc doesn't support tuple binding for IN directly via SQLAlchemy
        # text(); use expanding bind parameter instead.
        from sqlalchemy import bindparam

        stmt_table = text(
            """
            SELECT s.name AS schema_name, t.name AS table_name, ep.value AS comment
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE s.name IN :schemas
            """
        ).bindparams(bindparam("schemas", expanding=True))
        stmt_col = text(
            """
            SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, ep.value AS comment
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.columns c ON c.object_id = t.object_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = t.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
            WHERE s.name IN :schemas
            """
        ).bindparams(bindparam("schemas", expanding=True))
        params["schemas"] = schemas
    else:
        stmt_table = text(
            """
            SELECT s.name AS schema_name, t.name AS table_name, ep.value AS comment
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            """
        )
        stmt_col = text(
            """
            SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, ep.value AS comment
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.columns c ON c.object_id = t.object_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = t.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
            """
        )

    for row in conn.execute(stmt_table, params):
        if row.comment:
            table_comments[(row.schema_name, row.table_name)] = str(row.comment)
    for row in conn.execute(stmt_col, params):
        if row.comment:
            column_comments[(row.schema_name, row.table_name, row.column_name)] = str(row.comment)

    return table_comments, column_comments


def fetch_oracle_comments(conn: Connection, schemas: list[str] | None):
    table_comments: dict[tuple[str, str], str] = {}
    column_comments: dict[tuple[str, str, str], str] = {}

    from sqlalchemy import bindparam

    if schemas:
        owner_filter = "AND OWNER IN :schemas"
        table_stmt = text(
            f"SELECT OWNER, TABLE_NAME, COMMENTS FROM ALL_TAB_COMMENTS WHERE COMMENTS IS NOT NULL {owner_filter}"
        ).bindparams(bindparam("schemas", expanding=True))
        col_stmt = text(
            f"SELECT OWNER, TABLE_NAME, COLUMN_NAME, COMMENTS FROM ALL_COL_COMMENTS WHERE COMMENTS IS NOT NULL {owner_filter}"
        ).bindparams(bindparam("schemas", expanding=True))
        params = {"schemas": schemas}
    else:
        table_stmt = text(
            "SELECT OWNER, TABLE_NAME, COMMENTS FROM USER_TAB_COMMENTS WHERE COMMENTS IS NOT NULL"
        )
        col_stmt = text(
            "SELECT USER AS OWNER, TABLE_NAME, COLUMN_NAME, COMMENTS FROM USER_COL_COMMENTS WHERE COMMENTS IS NOT NULL"
        )
        params = {}

    for row in conn.execute(table_stmt, params):
        table_comments[(row.OWNER, row.TABLE_NAME)] = row.COMMENTS
    for row in conn.execute(col_stmt, params):
        column_comments[(row.OWNER, row.TABLE_NAME, row.COLUMN_NAME)] = row.COMMENTS

    return table_comments, column_comments


def fetch_db2_comments(conn: Connection, schemas: list[str] | None):
    table_comments: dict[tuple[str, str], str] = {}
    column_comments: dict[tuple[str, str, str], str] = {}

    from sqlalchemy import bindparam

    if schemas:
        schema_filter = "AND TABSCHEMA IN :schemas"
        table_stmt = text(
            f"SELECT TABSCHEMA, TABNAME, REMARKS FROM SYSCAT.TABLES WHERE REMARKS IS NOT NULL {schema_filter}"
        ).bindparams(bindparam("schemas", expanding=True))
        col_stmt = text(
            f"SELECT TABSCHEMA, TABNAME, COLNAME, REMARKS FROM SYSCAT.COLUMNS WHERE REMARKS IS NOT NULL {schema_filter}"
        ).bindparams(bindparam("schemas", expanding=True))
        params = {"schemas": schemas}
    else:
        table_stmt = text("SELECT TABSCHEMA, TABNAME, REMARKS FROM SYSCAT.TABLES WHERE REMARKS IS NOT NULL")
        col_stmt = text("SELECT TABSCHEMA, TABNAME, COLNAME, REMARKS FROM SYSCAT.COLUMNS WHERE REMARKS IS NOT NULL")
        params = {}

    for row in conn.execute(table_stmt, params):
        table_comments[(row.TABSCHEMA.strip(), row.TABNAME.strip())] = row.REMARKS
    for row in conn.execute(col_stmt, params):
        column_comments[(row.TABSCHEMA.strip(), row.TABNAME.strip(), row.COLNAME.strip())] = row.REMARKS

    return table_comments, column_comments


COMMENT_FETCHERS = {
    "postgresql": fetch_postgresql_comments,
    "mssql": fetch_mssql_comments,
    "oracle": fetch_oracle_comments,
    "db2": fetch_db2_comments,
}
