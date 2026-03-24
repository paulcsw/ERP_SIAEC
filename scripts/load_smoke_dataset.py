#!/usr/bin/env python3
"""Load a synthetic smoke/demo dataset into the local development MSSQL database.

This loader is intentionally local-dev only. It refuses to run unless the
target database looks like the Docker-based development database.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlsplit

import pyodbc


LOAD_ORDER = [
    "roles",
    "users",
    "user_roles",
    "shops",
    "user_shop_access",
    "system_config",
    "aircraft",
    "work_packages",
    "shop_streams",
    "task_items",
    "task_snapshots",
    "ot_requests",
    "ot_approvals",
    "audit_logs",
    "shift_templates",
    "shift_assignments",
    "attendance_events",
    "daily_assignments",
    "worklog_blocks",
    "time_ledger_daily",
    "ledger_allocations_daily",
]

LOCAL_HOSTS = {"db", "localhost", "127.0.0.1"}
LOCAL_DATABASES = {"cis_erp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a synthetic ERP smoke dataset into the local dev DB."
    )
    parser.add_argument(
        "--dataset",
        default="01_smoke_ui_clean",
        help="Dataset folder name under erp_sample_dataset_bundle/.",
    )
    parser.add_argument(
        "--dataset-root",
        help="Override the erp_sample_dataset_bundle root path.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Target DATABASE_URL. Defaults to env DATABASE_URL.",
    )
    parser.add_argument(
        "--replace-local-dev",
        action="store_true",
        help="Explicitly delete and replace the existing local dev data.",
    )
    return parser.parse_args()


def find_dataset_root(override: str | None) -> Path:
    if override:
        root = Path(override).expanduser().resolve()
        if not root.exists():
            raise SystemExit(f"Dataset root does not exist: {root}")
        return root

    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "erp_sample_dataset_bundle",
        Path.cwd() / "erp_sample_dataset_bundle",
        Path("/app/erp_sample_dataset_bundle"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise SystemExit("Could not find erp_sample_dataset_bundle in the workspace.")


def validate_target_database(database_url: str) -> None:
    if not database_url:
        raise SystemExit("DATABASE_URL is required.")

    parsed = urlsplit(database_url)
    host = parsed.hostname or ""
    database_name = parsed.path.lstrip("/")
    if host not in LOCAL_HOSTS or database_name not in LOCAL_DATABASES:
        raise SystemExit(
            "Refusing to load dataset: target does not look like the local dev DB "
            f"(host={host!r}, database={database_name!r})."
        )


def build_pyodbc_connection_string(database_url: str) -> str:
    parsed = urlsplit(database_url)
    query = parse_qs(parsed.query)
    driver = unquote(query.get("driver", ["ODBC Driver 18 for SQL Server"])[0])
    trust_server_certificate = query.get("TrustServerCertificate", ["yes"])[0]

    host = parsed.hostname or "localhost"
    port = parsed.port or 1433
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database_name = parsed.path.lstrip("/")

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={host},{port};"
        f"DATABASE={database_name};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate={trust_server_certificate};"
    )


def dataset_csv_dir(dataset_root: Path, dataset_name: str) -> Path:
    path = dataset_root / dataset_name / "db_csv"
    if not path.exists():
        raise SystemExit(f"Dataset CSV directory does not exist: {path}")
    return path


def read_csv_rows(csv_path: Path) -> tuple[list[str], list[list[str | None]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return [], []
        rows: list[list[str | None]] = []
        for raw_row in reader:
            rows.append(
                [
                    None if value is None or value == "" else value
                    for value in (raw_row.get(name) for name in reader.fieldnames)
                ]
            )
        return list(reader.fieldnames), rows


def table_has_identity(cursor: pyodbc.Cursor, table_name: str) -> bool:
    row = cursor.execute(
        """
        SELECT COUNT(*)
        FROM sys.identity_columns ic
        INNER JOIN sys.tables t ON ic.object_id = t.object_id
        WHERE t.name = ?
        """,
        table_name,
    ).fetchone()
    return bool(row and row[0])


def delete_existing_rows(cursor: pyodbc.Cursor, tables: Iterable[str]) -> None:
    for table_name in tables:
        cursor.execute(f"DELETE FROM [dbo].[{table_name}]")


def insert_table(
    cursor: pyodbc.Cursor,
    table_name: str,
    columns: list[str],
    rows: list[list[str | None]],
) -> int:
    if not columns or not rows:
        return 0

    quoted_columns = ", ".join(f"[{column}]" for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO [dbo].[{table_name}] ({quoted_columns}) VALUES ({placeholders})"
    identity_on = table_has_identity(cursor, table_name)

    try:
        if identity_on:
            cursor.execute(f"SET IDENTITY_INSERT [dbo].[{table_name}] ON")
        cursor.executemany(sql, rows)
    finally:
        if identity_on:
            cursor.execute(f"SET IDENTITY_INSERT [dbo].[{table_name}] OFF")

    if identity_on and rows and "id" in columns:
        id_index = columns.index("id")
        max_id = max(int(row[id_index]) for row in rows if row[id_index] is not None)
        cursor.execute(f"DBCC CHECKIDENT ('dbo.{table_name}', RESEED, {max_id})")
    return len(rows)


def fetch_table_count(cursor: pyodbc.Cursor, table_name: str) -> int:
    row = cursor.execute(f"SELECT COUNT(*) FROM [dbo].[{table_name}]").fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    args = parse_args()
    if not args.replace_local_dev:
        raise SystemExit(
            "This loader requires --replace-local-dev so destructive replacement is explicit."
        )

    validate_target_database(args.database_url)
    root = find_dataset_root(args.dataset_root)
    csv_dir = dataset_csv_dir(root, args.dataset)
    conn_str = build_pyodbc_connection_string(args.database_url)

    print(f"Dataset root: {root}")
    print(f"Dataset: {args.dataset}")
    print(f"CSV dir: {csv_dir}")

    inserted_counts: dict[str, int] = {}

    conn = pyodbc.connect(conn_str, autocommit=False)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DB_NAME()")
        db_name = cursor.fetchone()[0]
        print(f"Connected database: {db_name}")

        delete_existing_rows(cursor, reversed(LOAD_ORDER))

        for table_name in LOAD_ORDER:
            csv_path = csv_dir / f"{table_name}.csv"
            if not csv_path.exists():
                raise SystemExit(f"Missing dataset file: {csv_path}")
            columns, rows = read_csv_rows(csv_path)
            inserted_counts[table_name] = insert_table(cursor, table_name, columns, rows)
            print(f"Loaded {table_name}: {inserted_counts[table_name]} rows")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verify_conn = pyodbc.connect(conn_str, autocommit=True)
    try:
        cursor = verify_conn.cursor()
        print("\nFinal row counts:")
        for table_name in LOAD_ORDER:
            count = fetch_table_count(cursor, table_name)
            print(f"  {table_name}: {count}")
    finally:
        verify_conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
