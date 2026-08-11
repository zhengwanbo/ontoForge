"""Synchronize ontology-build, mapping, and DDL metadata between Oracle schemas.

The target receives a replacement of the scoped metadata tables in one transaction.
System users, LLM configuration, datasource credentials, rules, processes, and agent
records are intentionally outside this synchronization scope.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

import oracledb


TABLE_INSERT_ORDER = [
    "SYS_DOMAIN",
    "SYS_ONTOLOGY_ENTITY",
    "SYS_ONTOLOGY_PROPERTY",
    "SYS_ONTOLOGY_RELATION",
    "SYS_ENTITY_MAPPING",
    "SYS_PROPERTY_MAPPING",
    "SYS_RELATION_MAPPING",
    "SYS_ONTOLOGY_BLUEPRINT",
    "SYS_MAPPING_TASK",
    "SYS_DDL_LOG",
    "SYS_DDL_STATEMENT_LOG",
]

TABLE_DELETE_ORDER = [
    "SYS_DDL_STATEMENT_LOG",
    "SYS_DDL_LOG",
    "SYS_MAPPING_TASK",
    "SYS_ONTOLOGY_BLUEPRINT",
    "SYS_PROPERTY_MAPPING",
    "SYS_ENTITY_MAPPING",
    "SYS_RELATION_MAPPING",
    "SYS_ONTOLOGY_PROPERTY",
    "SYS_ONTOLOGY_RELATION",
    "SYS_ONTOLOGY_ENTITY",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dsn", required=True)
    parser.add_argument("--source-user", required=True)
    parser.add_argument("--target-dsn", required=True)
    parser.add_argument("--target-user", required=True)
    parser.add_argument("--apply", action="store_true", help="Write data to target. Omit for read-only validation.")
    parser.add_argument(
        "--migrate-target-schema",
        action="store_true",
        help="When used with --apply, add source columns that are missing from target tables before synchronization.",
    )
    parser.add_argument("--verify", action="store_true", help="Compare source and target row content for every scoped table.")
    return parser.parse_args()


def connect(dsn: str, user: str, password_env_name: str):
    password = os.environ.get(password_env_name)
    if not password:
        raise RuntimeError(f"Missing required environment variable: {password_env_name}")
    return oracledb.connect(user=user, password=password, dsn=dsn)


def get_columns(connection, table_name: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = :table_name
            ORDER BY COLUMN_ID
            """,
            {"table_name": table_name},
        )
        return [row[0] for row in cursor]


def get_column_specs(connection, table_name: str) -> dict[str, tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, CHAR_LENGTH, CHAR_USED,
                   DATA_PRECISION, DATA_SCALE
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = :table_name
            ORDER BY COLUMN_ID
            """,
            {"table_name": table_name},
        )
        return {row[0]: tuple(row[1:]) for row in cursor}


def oracle_type(spec: tuple[Any, ...]) -> str:
    data_type, data_length, char_length, char_used, precision, scale = spec
    if data_type in {"VARCHAR2", "VARCHAR", "CHAR", "NCHAR", "NVARCHAR2", "RAW"}:
        length = char_length if char_length is not None else data_length
        semantics = " CHAR" if char_used == "C" else " BYTE"
        return f"{data_type}({length}{semantics})"
    if data_type == "NUMBER" and precision is not None:
        return f"NUMBER({precision}{',' + str(scale) if scale is not None else ''})"
    if data_type.startswith("TIMESTAMP") and scale is not None:
        return f"{data_type}({scale})"
    return data_type


def schema_differences(source_connection, target_connection) -> tuple[dict[str, list[tuple[str, tuple[Any, ...]]]], list[str]]:
    missing: dict[str, list[tuple[str, tuple[Any, ...]]]] = {}
    incompatible: list[str] = []
    for table_name in TABLE_INSERT_ORDER:
        source_specs = get_column_specs(source_connection, table_name)
        target_specs = get_column_specs(target_connection, table_name)
        if not source_specs:
            raise RuntimeError(f"Source table is missing: {table_name}")
        if not target_specs:
            raise RuntimeError(f"Target table is missing: {table_name}")
        missing_columns = [(name, spec) for name, spec in source_specs.items() if name not in target_specs]
        if missing_columns:
            missing[table_name] = missing_columns
        for name, source_spec in source_specs.items():
            if name in target_specs and oracle_type(source_spec) != oracle_type(target_specs[name]):
                incompatible.append(
                    f"{table_name}.{name}: source={oracle_type(source_spec)} target={oracle_type(target_specs[name])}"
                )
    return missing, incompatible


def add_missing_target_columns(target_connection, missing: dict[str, list[tuple[str, tuple[Any, ...]]]]) -> None:
    cursor = target_connection.cursor()
    try:
        for table_name, columns in missing.items():
            for column_name, spec in columns:
                # New columns intentionally remain nullable: existing target-only
                # rows (such as domains referenced by other modules) must remain valid.
                cursor.execute(f'ALTER TABLE "{table_name}" ADD ("{column_name}" {oracle_type(spec)})')
                print(f"Added target column {table_name}.{column_name} {oracle_type(spec)}")
        target_connection.commit()
    except Exception:
        target_connection.rollback()
        raise
    finally:
        cursor.close()


def get_row_count(connection, table_name: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        return int(cursor.fetchone()[0])


def normalize_value(value: Any) -> Any:
    return value.read() if hasattr(value, "read") else value


def value_for_digest(value: Any) -> Any:
    value = normalize_value(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def fetch_rows(connection, table_name: str, columns: Iterable[str]) -> list[tuple[Any, ...]]:
    column_list = list(columns)
    quoted_columns = ", ".join(f'"{column}"' for column in column_list)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {quoted_columns} FROM "{table_name}"')
        return [tuple(normalize_value(value) for value in row) for row in cursor]


def table_digest(connection, table_name: str, columns: Iterable[str]) -> str:
    rows = fetch_rows(connection, table_name, columns)
    serialized_rows = [json.dumps([value_for_digest(value) for value in row], ensure_ascii=False, separators=(",", ":")) for row in rows]
    return hashlib.sha256("\n".join(sorted(serialized_rows)).encode("utf-8")).hexdigest()


def table_plan(source_connection, target_connection) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for table_name in TABLE_INSERT_ORDER:
        source_columns = get_columns(source_connection, table_name)
        target_columns = get_columns(target_connection, table_name)
        if not source_columns:
            raise RuntimeError(f"Source table is missing: {table_name}")
        if not target_columns:
            raise RuntimeError(f"Target table is missing: {table_name}")
        common_columns = [column for column in source_columns if column in target_columns]
        plan.append({
            "table_name": table_name,
            "columns": common_columns,
            "source_count": get_row_count(source_connection, table_name),
            "target_count": get_row_count(target_connection, table_name),
        })
    return plan


def insert_rows(target_cursor, table_name: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    placeholders = ", ".join(f":{index}" for index in range(1, len(columns) + 1))
    column_names = ", ".join(f'"{column}"' for column in columns)
    target_cursor.executemany(f'INSERT INTO "{table_name}" ({column_names}) VALUES ({placeholders})', rows)


def upsert_domains(target_cursor, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    if "DOMAIN_ID" not in columns:
        raise RuntimeError("SYS_DOMAIN must include DOMAIN_ID for synchronization")
    select_columns = ", ".join(f":{index} AS \"{column}\"" for index, column in enumerate(columns, start=1))
    non_key_columns = [column for column in columns if column != "DOMAIN_ID"]
    update_clause = ", ".join(f't."{column}" = s."{column}"' for column in non_key_columns)
    insert_columns = ", ".join(f'"{column}"' for column in columns)
    insert_values = ", ".join(f's."{column}"' for column in columns)
    merge_sql = f'''MERGE INTO "SYS_DOMAIN" t
                    USING (SELECT {select_columns} FROM DUAL) s
                    ON (t."DOMAIN_ID" = s."DOMAIN_ID")
                    WHEN MATCHED THEN UPDATE SET {update_clause}
                    WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})'''
    target_cursor.executemany(merge_sql, rows)


def sync(source_connection, target_connection, plan: list[dict[str, Any]]) -> None:
    source_rows = {
        item["table_name"]: fetch_rows(source_connection, item["table_name"], item["columns"])
        for item in plan
    }
    cursor = target_connection.cursor()
    try:
        # SYS_DOMAIN is merged rather than cleared because other target-only system
        # features may still reference domain rows.
        domain = next(item for item in plan if item["table_name"] == "SYS_DOMAIN")
        for table_name in TABLE_DELETE_ORDER:
            cursor.execute(f'DELETE FROM "{table_name}"')
        for item in plan:
            if item["table_name"] == "SYS_DOMAIN":
                upsert_domains(cursor, domain["columns"], source_rows["SYS_DOMAIN"])
            else:
                insert_rows(cursor, item["table_name"], item["columns"], source_rows[item["table_name"]])
        target_connection.commit()
    except Exception:
        target_connection.rollback()
        raise
    finally:
        cursor.close()


def main() -> int:
    args = parse_args()
    source_connection = connect(args.source_dsn, args.source_user, "OON_SYNC_SOURCE_PASSWORD")
    target_connection = connect(args.target_dsn, args.target_user, "OON_SYNC_TARGET_PASSWORD")
    try:
        plan = table_plan(source_connection, target_connection)
        missing_columns, incompatible_columns = schema_differences(source_connection, target_connection)
        if incompatible_columns:
            raise RuntimeError("Incompatible source/target column types:\n" + "\n".join(incompatible_columns))
        if missing_columns:
            for table_name, columns in missing_columns.items():
                print(f"Target missing columns in {table_name}: " + ", ".join(name for name, _ in columns))
            if not args.apply or not args.migrate_target_schema:
                print("Schema migration is required before synchronization; no target data was changed.")
                return 2
            add_missing_target_columns(target_connection, missing_columns)
            plan = table_plan(source_connection, target_connection)
        for item in plan:
            print(
                f"{item['table_name']}: source={item['source_count']} target_before={item['target_count']} "
                f"columns={len(item['columns'])}"
            )
        if args.verify:
            mismatches = []
            for item in plan:
                source_digest = table_digest(source_connection, item["table_name"], item["columns"])
                target_digest = table_digest(target_connection, item["table_name"], item["columns"])
                matched = source_digest == target_digest
                print(f"Content check {item['table_name']}: {'matched' if matched else 'mismatch'}")
                if not matched:
                    mismatches.append(item["table_name"])
            if mismatches:
                raise RuntimeError("Content verification failed: " + ", ".join(mismatches))
        if not args.apply:
            print("Dry run completed; no target data was changed.")
            return 0
        sync(source_connection, target_connection, plan)
        for item in plan:
            target_count = get_row_count(target_connection, item["table_name"])
            if target_count != item["source_count"]:
                raise RuntimeError(
                    f"Post-sync count mismatch for {item['table_name']}: "
                    f"source={item['source_count']} target={target_count}"
                )
            source_digest = table_digest(source_connection, item["table_name"], item["columns"])
            target_digest = table_digest(target_connection, item["table_name"], item["columns"])
            if source_digest != target_digest:
                raise RuntimeError(f"Post-sync data mismatch for {item['table_name']}")
            print(f"Verified {item['table_name']}: {target_count} rows, content matched")
        print("Ontology metadata synchronization completed successfully.")
        return 0
    finally:
        source_connection.close()
        target_connection.close()


if __name__ == "__main__":
    sys.exit(main())
