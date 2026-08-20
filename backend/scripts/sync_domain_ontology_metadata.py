"""Synchronize one domain's ontology, mapping, and DDL metadata between Oracle schemas.

The script deliberately scopes every delete to one DOMAIN_ID.  It is intended for
moving a single business domain without disturbing metadata belonging to other
domains in the target schema.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import oracledb


TABLES = [
    "SYS_DOMAIN",
    "SYS_PROCESS_DEF",
    "SYS_ONTOLOGY_ENTITY",
    "SYS_ONTOLOGY_PROPERTY",
    "SYS_ONTOLOGY_RELATION",
    "SYS_ONTOLOGY_BLUEPRINT",
    "SYS_MAPPING_TASK",
    "SYS_ENTITY_MAPPING",
    "SYS_PROPERTY_MAPPING",
    "SYS_RELATION_MAPPING",
    "SYS_DDL_LOG",
    "SYS_DDL_STATEMENT_LOG",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dsn", required=True)
    parser.add_argument("--source-user", required=True)
    parser.add_argument("--target-dsn", required=True)
    parser.add_argument("--target-user", required=True)
    parser.add_argument("--domain-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Write to target; omit for a read-only dry run.")
    parser.add_argument("--backup-dir", help="Directory for a JSON backup of the target domain before replacement.")
    return parser.parse_args()


def connect(dsn: str, user: str, password_name: str):
    password = os.environ.get(password_name)
    if not password:
        raise RuntimeError(f"Missing environment variable {password_name}")
    return oracledb.connect(user=user, password=password, dsn=dsn)


def columns(connection, table_name: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :name ORDER BY COLUMN_ID",
            {"name": table_name},
        )
        result = [row[0] for row in cursor]
    if not result:
        raise RuntimeError(f"Missing table {table_name}")
    return result


def scalar_ids(cursor, sql: str, domain_id: str) -> list[str]:
    cursor.execute(sql, {"domain_id": domain_id})
    return [row[0] for row in cursor]


def scope_ids(connection, domain_id: str) -> dict[str, list[str]]:
    with connection.cursor() as cursor:
        entity_ids = scalar_ids(cursor, "SELECT ENTITY_ID FROM SYS_ONTOLOGY_ENTITY WHERE DOMAIN_ID = :domain_id", domain_id)
        relation_ids = scalar_ids(cursor, "SELECT RELATION_ID FROM SYS_ONTOLOGY_RELATION WHERE DOMAIN_ID = :domain_id", domain_id)
        log_ids = scalar_ids(cursor, "SELECT LOG_ID FROM SYS_DDL_LOG WHERE DOMAIN_ID = :domain_id", domain_id)
    return {"entities": entity_ids, "relations": relation_ids, "logs": log_ids}


def select_rows(connection, table_name: str, table_columns: list[str], domain_id: str) -> list[tuple[Any, ...]]:
    quoted = ", ".join(f'"{column}"' for column in table_columns)
    filters = {
        "SYS_DOMAIN": ("DOMAIN_ID = :domain_id", {}),
        "SYS_PROCESS_DEF": ("DOMAIN_ID = :domain_id", {}),
        "SYS_ONTOLOGY_ENTITY": ("DOMAIN_ID = :domain_id", {}),
        "SYS_ONTOLOGY_PROPERTY": ("ENTITY_ID IN (SELECT ENTITY_ID FROM SYS_ONTOLOGY_ENTITY WHERE DOMAIN_ID = :domain_id)", {}),
        "SYS_ONTOLOGY_RELATION": ("DOMAIN_ID = :domain_id", {}),
        "SYS_ONTOLOGY_BLUEPRINT": ("DOMAIN_ID = :domain_id", {}),
        "SYS_MAPPING_TASK": ("DOMAIN_ID = :domain_id", {}),
        "SYS_ENTITY_MAPPING": ("ENTITY_ID IN (SELECT ENTITY_ID FROM SYS_ONTOLOGY_ENTITY WHERE DOMAIN_ID = :domain_id)", {}),
        "SYS_PROPERTY_MAPPING": ("PROPERTY_ID IN (SELECT p.PROPERTY_ID FROM SYS_ONTOLOGY_PROPERTY p JOIN SYS_ONTOLOGY_ENTITY e ON e.ENTITY_ID = p.ENTITY_ID WHERE e.DOMAIN_ID = :domain_id)", {}),
        "SYS_RELATION_MAPPING": ("RELATION_ID IN (SELECT RELATION_ID FROM SYS_ONTOLOGY_RELATION WHERE DOMAIN_ID = :domain_id)", {}),
        "SYS_DDL_LOG": ("DOMAIN_ID = :domain_id", {}),
        "SYS_DDL_STATEMENT_LOG": ("LOG_ID IN (SELECT LOG_ID FROM SYS_DDL_LOG WHERE DOMAIN_ID = :domain_id)", {}),
    }
    where, binds = filters[table_name]
    binds["domain_id"] = domain_id
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {quoted} FROM "{table_name}" WHERE {where}', binds)
        return [tuple(value.read() if hasattr(value, "read") else value for value in row) for row in cursor]


def display_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def digest(rows: list[tuple[Any, ...]]) -> str:
    payload = [json.dumps([display_value(value) for value in row], ensure_ascii=False, separators=(",", ":")) for row in rows]
    return hashlib.sha256("\n".join(sorted(payload)).encode()).hexdigest()


def backup(path: Path, scoped_rows: dict[str, list[tuple[Any, ...]]], all_columns: dict[str, list[str]]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for table_name, rows in scoped_rows.items():
        data = [dict(zip(all_columns[table_name], (display_value(value) for value in row))) for row in rows]
        (path / f"{table_name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_target(cursor, domain_id: str) -> None:
    deletes = [
        "DELETE FROM SYS_PROCESS_DEF WHERE DOMAIN_ID = :domain_id",
        "DELETE FROM SYS_DDL_STATEMENT_LOG WHERE LOG_ID IN (SELECT LOG_ID FROM SYS_DDL_LOG WHERE DOMAIN_ID = :domain_id)",
        "DELETE FROM SYS_DDL_LOG WHERE DOMAIN_ID = :domain_id",
        "DELETE FROM SYS_PROPERTY_MAPPING WHERE PROPERTY_ID IN (SELECT p.PROPERTY_ID FROM SYS_ONTOLOGY_PROPERTY p JOIN SYS_ONTOLOGY_ENTITY e ON e.ENTITY_ID = p.ENTITY_ID WHERE e.DOMAIN_ID = :domain_id)",
        "DELETE FROM SYS_ENTITY_MAPPING WHERE ENTITY_ID IN (SELECT ENTITY_ID FROM SYS_ONTOLOGY_ENTITY WHERE DOMAIN_ID = :domain_id)",
        "DELETE FROM SYS_RELATION_MAPPING WHERE RELATION_ID IN (SELECT RELATION_ID FROM SYS_ONTOLOGY_RELATION WHERE DOMAIN_ID = :domain_id)",
        "DELETE FROM SYS_MAPPING_TASK WHERE DOMAIN_ID = :domain_id",
        "DELETE FROM SYS_ONTOLOGY_BLUEPRINT WHERE DOMAIN_ID = :domain_id",
        "DELETE FROM SYS_ONTOLOGY_PROPERTY WHERE ENTITY_ID IN (SELECT ENTITY_ID FROM SYS_ONTOLOGY_ENTITY WHERE DOMAIN_ID = :domain_id)",
        "DELETE FROM SYS_ONTOLOGY_RELATION WHERE DOMAIN_ID = :domain_id",
        "DELETE FROM SYS_ONTOLOGY_ENTITY WHERE DOMAIN_ID = :domain_id",
    ]
    for sql in deletes:
        cursor.execute(sql, {"domain_id": domain_id})


def insert_rows(cursor, table_name: str, table_columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    names = ", ".join(f'"{column}"' for column in table_columns)
    binds = ", ".join(f":{index}" for index in range(1, len(table_columns) + 1))
    cursor.executemany(f'INSERT INTO "{table_name}" ({names}) VALUES ({binds})', rows)


def merge_domain(cursor, table_columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    """Upsert the domain without deleting unrelated records that reference it."""
    if not rows:
        return
    key = "DOMAIN_ID"
    select_list = ", ".join(f":{index} AS \"{column}\"" for index, column in enumerate(table_columns, start=1))
    non_key = [column for column in table_columns if column != key]
    updates = ", ".join(f't.\"{column}\" = s.\"{column}\"' for column in non_key)
    insert_columns = ", ".join(f'\"{column}\"' for column in table_columns)
    insert_values = ", ".join(f's.\"{column}\"' for column in table_columns)
    cursor.executemany(
        f'''MERGE INTO SYS_DOMAIN t USING (SELECT {select_list} FROM DUAL) s
            ON (t.DOMAIN_ID = s.DOMAIN_ID)
            WHEN MATCHED THEN UPDATE SET {updates}
            WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})''',
        rows,
    )


def main() -> int:
    args = parse_args()
    source = connect(args.source_dsn, args.source_user, "OON_DOMAIN_SYNC_SOURCE_PASSWORD")
    target = connect(args.target_dsn, args.target_user, "OON_DOMAIN_SYNC_TARGET_PASSWORD")
    try:
        source_columns = {table: columns(source, table) for table in TABLES}
        target_columns = {table: columns(target, table) for table in TABLES}
        # Oracle schemas can carry the same columns in a different physical order
        # after an ALTER TABLE.  Use the target order for reads/inserts instead of
        # treating that harmless difference as an incompatible schema.
        mismatched = [table for table in TABLES if set(source_columns[table]) != set(target_columns[table])]
        if mismatched:
            raise RuntimeError("Source/target column mismatch: " + ", ".join(mismatched))
        source_rows = {table: select_rows(source, table, target_columns[table], args.domain_id) for table in TABLES}
        target_rows = {table: select_rows(target, table, target_columns[table], args.domain_id) for table in TABLES}
        if len(source_rows["SYS_DOMAIN"]) != 1:
            raise RuntimeError(f"Source domain {args.domain_id} was not found exactly once")
        for table in TABLES:
            print(f"{table}: source={len(source_rows[table])} target_before={len(target_rows[table])}")
        if not args.apply:
            print("Dry run completed; target was not changed.")
            return 0
        if not args.backup_dir:
            raise RuntimeError("--backup-dir is required with --apply")
        backup(Path(args.backup_dir), target_rows, target_columns)
        cursor = target.cursor()
        try:
            delete_target(cursor, args.domain_id)
            merge_domain(cursor, target_columns["SYS_DOMAIN"], source_rows["SYS_DOMAIN"])
            for table in TABLES[1:]:
                insert_rows(cursor, table, target_columns[table], source_rows[table])
            target.commit()
        except Exception:
            target.rollback()
            raise
        finally:
            cursor.close()
        for table in TABLES:
            actual = select_rows(target, table, target_columns[table], args.domain_id)
            if len(actual) != len(source_rows[table]) or digest(actual) != digest(source_rows[table]):
                raise RuntimeError(f"Post-sync verification failed for {table}")
            print(f"Verified {table}: {len(actual)} rows, content matched")
        print("Domain ontology metadata synchronization completed successfully.")
        return 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    sys.exit(main())
