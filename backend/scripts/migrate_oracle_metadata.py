import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import oracledb


TABLE_INSERT_ORDER: List[str] = [
    "SYS_DOMAIN",
    "SYS_USER",
    "SYS_LLM_CONFIG",
    "SYS_PROCESS_DEF",
    "SYS_ONTOLOGY_ENTITY",
    "SYS_ONTOLOGY_PROPERTY",
    "SYS_ONTOLOGY_RELATION",
    "SYS_ENTITY_MAPPING",
    "SYS_PROPERTY_MAPPING",
    "SYS_RELATION_MAPPING",
    "SYS_DATA_SOURCE",
    "SYS_BUSINESS_ACTIVITY",
    "SYS_BUSINESS_RULE",
    "SYS_DDL_LOG",
    "SYS_OPERATION_LOG",
    "SYS_AGENT_SKILL",
]

TABLE_DELETE_ORDER: List[str] = list(reversed(TABLE_INSERT_ORDER))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate project metadata tables between Oracle databases.")
    parser.add_argument("--source-host", required=True)
    parser.add_argument("--source-port", type=int, default=1521)
    parser.add_argument("--source-service", required=True)
    parser.add_argument("--source-user", required=True)
    parser.add_argument("--source-password", required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, default=1521)
    parser.add_argument("--target-service", required=True)
    parser.add_argument("--target-user", required=True)
    parser.add_argument("--target-password", required=True)
    parser.add_argument("--backup-dir", default=str(Path("/private/tmp") / f"oon_build_metadata_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
    return parser.parse_args()


def connect(host: str, port: int, service: str, user: str, password: str):
    dsn = f"{host}:{port}/{service}"
    return oracledb.connect(user=user, password=password, dsn=dsn)


def get_columns(connection, table_name: str) -> List[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = :table_name
            ORDER BY COLUMN_ID
            """,
            {"table_name": table_name},
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()


def fetch_all_rows(connection, table_name: str, columns: Sequence[str]) -> List[Dict]:
    cursor = connection.cursor()
    try:
        quoted = ", ".join([f'"{column}"' for column in columns])
        cursor.execute(f'SELECT {quoted} FROM "{table_name}"')
        rows = cursor.fetchall()
        return [
            {
                column: normalize_bind_value(value)
                for column, value in zip(columns, row)
            }
            for row in rows
        ]
    finally:
        cursor.close()


def backup_target_tables(connection, backup_dir: Path):
    backup_dir.mkdir(parents=True, exist_ok=True)
    for table_name in TABLE_INSERT_ORDER:
        columns = get_columns(connection, table_name)
        if not columns:
            continue
        rows = fetch_all_rows(connection, table_name, columns)
        with (backup_dir / f"{table_name}.json").open("w", encoding="utf-8") as fp:
            json.dump(rows, fp, ensure_ascii=False, default=_json_default, indent=2)


def delete_target_rows(connection):
    cursor = connection.cursor()
    try:
        for table_name in TABLE_DELETE_ORDER:
            if not get_columns(connection, table_name):
                continue
            cursor.execute(f'DELETE FROM "{table_name}"')
        connection.commit()
    finally:
        cursor.close()


def insert_rows(source_conn, target_conn) -> List[Tuple[str, int, List[str]]]:
    results: List[Tuple[str, int, List[str]]] = []
    target_cursor = target_conn.cursor()
    try:
        for table_name in TABLE_INSERT_ORDER:
            print(f"Migrating {table_name}...", flush=True)
            source_columns = get_columns(source_conn, table_name)
            target_columns = get_columns(target_conn, table_name)
            if not source_columns or not target_columns:
                results.append((table_name, 0, []))
                continue

            common_columns = [column for column in target_columns if column in source_columns]
            if not common_columns:
                results.append((table_name, 0, []))
                continue

            rows = fetch_all_rows(source_conn, table_name, common_columns)
            if rows:
                bind_names = ", ".join([f":{idx + 1}" for idx in range(len(common_columns))])
                column_names = ", ".join([f'"{column}"' for column in common_columns])
                sql = f'INSERT INTO "{table_name}" ({column_names}) VALUES ({bind_names})'
                values = [tuple(row[column] for column in common_columns) for row in rows]
                target_cursor.executemany(sql, values)
                target_conn.commit()
            results.append((table_name, len(rows), common_columns))
        return results
    finally:
        target_cursor.close()


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def normalize_bind_value(value):
    if hasattr(value, "read"):
        return value.read()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    return value


def main():
    args = parse_args()
    backup_dir = Path(args.backup_dir)
    source_conn = connect(args.source_host, args.source_port, args.source_service, args.source_user, args.source_password)
    target_conn = connect(args.target_host, args.target_port, args.target_service, args.target_user, args.target_password)
    try:
        backup_target_tables(target_conn, backup_dir)
        delete_target_rows(target_conn)
        results = insert_rows(source_conn, target_conn)
    finally:
        source_conn.close()
        target_conn.close()

    print(f"Local backup saved to: {backup_dir}")
    for table_name, row_count, columns in results:
        print(f"{table_name}: {row_count} rows migrated using columns: {', '.join(columns) if columns else '(none)'}")


if __name__ == "__main__":
    main()
