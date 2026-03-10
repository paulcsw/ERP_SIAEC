"""Ensure the SQL Server database in DATABASE_URL exists."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def _quote_identifier(name: str) -> str:
    return f"[{name.replace(']', ']]')}]"


def _escape_tsql_string(value: str) -> str:
    return value.replace("'", "''")


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    url = make_url(database_url)
    if not url.database:
        raise SystemExit("DATABASE_URL must include a database name")

    master_url = url.set(drivername="mssql+pyodbc", database="master")
    engine = create_engine(
        master_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )

    db_name = url.database
    db_name_escaped = _escape_tsql_string(db_name)
    db_name_quoted = _quote_identifier(db_name)

    sql = (
        f"IF DB_ID(N'{db_name_escaped}') IS NULL "
        f"CREATE DATABASE {db_name_quoted};"
    )

    with engine.connect() as conn:
        conn.exec_driver_sql(sql)

    print(f"Database ready: {db_name}")


if __name__ == "__main__":
    main()
