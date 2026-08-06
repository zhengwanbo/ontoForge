"""删除指定 Oracle Schema 中 ONTO_* 表与 VW_* 视图。

默认只输出待删除对象。传入 --apply 后才会执行删除。
密码从 ORACLE_PASSWORD 环境变量读取，避免写入脚本或命令历史。
"""

import argparse
import os
import re
import sys

import oracledb


SAFE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清理 Oracle 本体节点和边对象")
    parser.add_argument("--host", default="163.192.218.148")
    parser.add_argument("--port", type=int, default=1521)
    parser.add_argument("--service", default="orclpdb1")
    parser.add_argument("--username", default="oonbuild")
    parser.add_argument("--apply", action="store_true", help="实际执行删除；缺省时仅预览")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    password = os.getenv("ORACLE_PASSWORD")
    if not password:
        print("请先设置 ORACLE_PASSWORD 环境变量。", file=sys.stderr)
        return 2

    dsn = oracledb.makedsn(args.host, args.port, service_name=args.service)
    with oracledb.connect(user=args.username, password=password, dsn=dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT object_type, object_name
                FROM user_objects
                WHERE (object_type = 'TABLE' AND object_name LIKE 'ONTO\\_%' ESCAPE '\\')
                   OR (object_type = 'VIEW' AND object_name LIKE 'VW\\_%' ESCAPE '\\')
                ORDER BY CASE object_type WHEN 'VIEW' THEN 1 ELSE 2 END, object_name
                """
            )
            targets = cursor.fetchall()

            print(f"匹配对象：{len(targets)}")
            for object_type, object_name in targets:
                print(f"- {object_type}: {object_name}")

            if not args.apply:
                print("预览完成。确认后请添加 --apply 执行删除。")
                return 0

            for object_type, object_name in targets:
                if not SAFE_IDENTIFIER.fullmatch(object_name):
                    raise ValueError(f"非法对象名，已中止：{object_name}")
                statement = (
                    f"DROP VIEW {object_name}"
                    if object_type == "VIEW"
                    else f"DROP TABLE {object_name} CASCADE CONSTRAINTS PURGE"
                )
                cursor.execute(statement)
                print(f"已删除 {object_type}: {object_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
