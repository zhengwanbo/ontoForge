#!/usr/bin/env python3
"""Validate and import the sample0708 CSV archive into matching Oracle tables.

Credentials are deliberately supplied through environment variables, never stored
in this script.  Run without --load first to perform a read-only preflight.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import zipfile
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation

import oracledb


TABLE_RE = re.compile(r"^PDX25_TAMS_[A-Z0-9_]+$")
NUMBER_TYPES = {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE", "INTEGER"}
DATE_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITH LOCAL TIME ZONE"}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", default="sample0708.zip", help="CSV archive path")
    parser.add_argument("--user", default=os.getenv("ORACLE_SAMPLE_USER", "oonbuild"))
    parser.add_argument("--dsn", default=os.getenv("ORACLE_SAMPLE_DSN", "163.192.218.148:1521/orclpdb1"))
    parser.add_argument("--load", action="store_true", help="perform inserts after preflight")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--tables", nargs="+", help="optional explicit table subset for a safe resume")
    return parser.parse_args()


def csv_entries(archive: zipfile.ZipFile) -> list[str]:
    entries = [name for name in archive.namelist() if name.startswith("data/") and name.endswith(".csv")]
    names = [os.path.basename(entry)[:-4] for entry in entries]
    if not entries or any(not TABLE_RE.fullmatch(name) for name in names) or len(names) != len(set(names)):
        raise ValueError("archive does not contain a valid unique PDX25_TAMS CSV table set")
    return sorted(entries)


def parse_value(value: str, type_name: str):
    value = value.strip()
    if value == "":
        return None
    base_type = type_name.split("(", 1)[0]
    if base_type in NUMBER_TYPES:
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"invalid {type_name} value {value!r}") from exc
    if base_type in DATE_TYPES:
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid {type_name} value {value!r}") from exc
    return value


@contextmanager
def open_csv(archive: zipfile.ZipFile, entry: str):
    with archive.open(entry) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
        reader = csv.DictReader(text)
        yield reader.fieldnames or [], reader


def main() -> int:
    args = arguments()
    password = os.getenv("ORACLE_SAMPLE_PASSWORD")
    if not password:
        print("ORACLE_SAMPLE_PASSWORD is required", file=sys.stderr)
        return 2
    if args.batch_size < 1:
        print("--batch-size must be positive", file=sys.stderr)
        return 2

    with zipfile.ZipFile(args.zip) as archive, oracledb.connect(user=args.user, password=password, dsn=args.dsn) as connection:
        cursor = connection.cursor()
        prepared: list[tuple[str, list[str], list[str], int, str]] = []
        requested = set(args.tables or [])
        if requested and any(not TABLE_RE.fullmatch(table) for table in requested):
            raise ValueError("--tables accepts only PDX25_TAMS table names")
        entries = [entry for entry in csv_entries(archive) if not requested or os.path.basename(entry)[:-4] in requested]
        if requested != {os.path.basename(entry)[:-4] for entry in entries}:
            raise ValueError("one or more --tables names are absent from the archive")
        for entry in entries:
            table = os.path.basename(entry)[:-4]
            with open_csv(archive, entry) as (raw_headers, rows):
                headers = [header.strip().upper() for header in raw_headers]
                row_count = sum(1 for _ in rows)
            cursor.execute(
                "select column_name, data_type from user_tab_columns where table_name = :table_name order by column_id",
                table_name=table,
            )
            columns = cursor.fetchall()
            if not columns:
                raise RuntimeError(f"{table}: target table does not exist in current schema")
            target_headers = [column[0] for column in columns]
            if headers != target_headers:
                raise RuntimeError(f"{table}: CSV columns do not exactly match target columns")
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            existing = cursor.fetchone()[0]
            print(f"{table}: CSV {row_count:,} rows; target {existing:,} existing rows; {len(headers)} columns")
            bind_columns = ", ".join(f'"{column}"' for column in headers)
            binds = ", ".join(f":{index}" for index in range(1, len(headers) + 1))
            prepared.append((entry, headers, [column[1] for column in columns], existing, f'INSERT INTO "{table}" ({bind_columns}) VALUES ({binds})'))

        if not args.load:
            print("Preflight succeeded. No data was written.")
            return 0
        occupied = [os.path.basename(entry)[:-4] for entry, _, _, existing, _ in prepared if existing]
        if occupied:
            raise RuntimeError("refusing to append to non-empty tables: " + ", ".join(occupied))

        for entry, headers, types, _, statement in prepared:
            table = os.path.basename(entry)[:-4]
            inserted = 0
            with open_csv(archive, entry) as (_, rows):
                batch = []
                for line_number, row in enumerate(rows, start=2):
                    try:
                        batch.append(tuple(parse_value(row[header] or "", type_name) for header, type_name in zip(headers, types)))
                    except (KeyError, ValueError) as exc:
                        raise RuntimeError(f"{table}: CSV line {line_number}: {exc}") from exc
                    if len(batch) >= args.batch_size:
                        cursor.executemany(statement, batch)
                        inserted += len(batch)
                        batch.clear()
                if batch:
                    cursor.executemany(statement, batch)
                    inserted += len(batch)
            connection.commit()
            print(f"{table}: inserted {inserted:,} rows")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Import stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
