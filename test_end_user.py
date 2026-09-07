import json
import os
from pathlib import Path

import oracledb


ENV_CANDIDATES = [
    Path("backend/.env"),
    Path(".env"),
    Path("../oracleDeepDataSec/.env"),
]


def load_env() -> list[Path]:
    loaded_files: list[Path] = []
    for env_path in ENV_CANDIDATES:
        resolved = env_path.resolve()
        if not resolved.exists():
            continue
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
        loaded_files.append(resolved)
    return loaded_files


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def resolve_path(raw_path: str | None, env_files: list[Path]) -> str | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return str(path)
    for env_file in reversed(env_files):
        candidate = (env_file.parent / path).resolve()
        if candidate.exists():
            return str(candidate)
    return str(path.resolve())


def connect_shared_user(env_files: list[Path]):
    kwargs = {
        "user": required("DB_USER"),
        "password": required("DB_PASSWORD"),
        "dsn": required("DB_CONNECT_STRING"),
    }
    config_dir = resolve_path(os.environ.get("DB_CONFIG_DIR"), env_files)
    wallet_location = resolve_path(os.environ.get("DB_WALLET_LOCATION"), env_files)
    wallet_password = os.environ.get("DB_WALLET_PASSWORD")
    if config_dir:
        kwargs["config_dir"] = config_dir
    if wallet_location:
        kwargs["wallet_location"] = wallet_location
    if wallet_password:
        kwargs["wallet_password"] = wallet_password
    return oracledb.connect(**kwargs)


def connect_local_end_user(env_files: list[Path]):
    username = os.environ.get("LOCAL_END_USER_USERNAME")
    password = os.environ.get("LOCAL_END_USER_PASSWORD")
    if not username or not password:
        raise RuntimeError("缺少 LOCAL_END_USER_USERNAME 或 LOCAL_END_USER_PASSWORD，无法测试本地 END USER 直连。")
    if not all(char.isalnum() or char in "._@-" for char in username):
        raise RuntimeError("LOCAL_END_USER_USERNAME 包含不支持的字符。")

    kwargs = {
        "user": f'"{username}"',
        "password": password,
        "dsn": required("DB_CONNECT_STRING"),
    }
    config_dir = resolve_path(os.environ.get("DB_CONFIG_DIR"), env_files)
    wallet_location = resolve_path(os.environ.get("DB_WALLET_LOCATION"), env_files)
    wallet_password = os.environ.get("DB_WALLET_PASSWORD")
    if config_dir:
        kwargs["config_dir"] = config_dir
    if wallet_location:
        kwargs["wallet_location"] = wallet_location
    if wallet_password:
        kwargs["wallet_password"] = wallet_password
    return oracledb.connect(**kwargs)


def build_end_user_context():
    database_access_token = required("DB_ACCESS_TOKEN")
    end_user_token = os.environ.get("END_USER_TOKEN")
    end_user_identity = os.environ.get("END_USER_IDENTITY")
    end_user_key = os.environ.get("END_USER_KEY")

    if end_user_token:
        identity = end_user_token
        identity_desc = "Microsoft Entra / OCI IAM token"
    elif end_user_identity and end_user_key:
        identity = (end_user_identity, end_user_key)
        identity_desc = "database managed end user"
    else:
        raise RuntimeError(
            "缺少终端用户身份。请提供 END_USER_TOKEN，或同时提供 END_USER_IDENTITY 与 END_USER_KEY。"
        )

    context_attributes = {
        "APP_CTX.CLIENT_KIND": "python-oracledb",
        "APP_CTX.TEST_NAME": "test_end_user.py",
    }
    custom_attr_json = os.environ.get("END_USER_ATTRIBUTES_JSON")
    if custom_attr_json:
        context_attributes["APP_CTX.CUSTOM"] = json.loads(custom_attr_json)

    context = oracledb.create_end_user_security_context(
        end_user_identity=identity,
        database_access_token=database_access_token,
        # data_roles=_split_csv(os.environ.get("END_USER_DATA_ROLES")),
        # attributes=context_attributes,
    )
    return context, identity_desc


def _split_csv(raw_value: str | None):
    if not raw_value:
        return None
    parts = [item.strip() for item in raw_value.split(",")]
    values = [item for item in parts if item]
    return values or None


def query_session_context(cursor):
    cursor.execute(
        """
SELECT
    SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user,
    SYS_CONTEXT('USERENV', 'NETWORK_PROTOCOL') AS network_protocol,
    SYS_CONTEXT('USERENV', 'AUTHENTICATED_IDENTITY') AS authenticated_identity,
    SYS_CONTEXT('USERENV', 'ENTERPRISE_IDENTITY') AS enterprise_identity,
    TO_CLOB(ORA_END_USER_CONTEXT) AS end_user_raw
FROM dual
        """
    )
    session_user, network_protocol, authenticated_identity, enterprise_identity, ctx_clob = cursor.fetchone()
    if ctx_clob is None:
        ctx_text = None
    elif hasattr(ctx_clob, "read"):
        ctx_text = ctx_clob.read()
    else:
        ctx_text = str(ctx_clob)

    print(f"SESSION_USER           : {session_user}")
    print(f"NETWORK_PROTOCOL       : {network_protocol}")
    print(f"AUTHENTICATED_IDENTITY : {authenticated_identity}")
    print(f"ENTERPRISE_IDENTITY    : {enterprise_identity}")
    print(f"ORA_END_USER_CONTEXT   : {ctx_text}")

    if ctx_text:
        print("\n解析后的 ORA_END_USER_CONTEXT:")
        print(json.dumps(json.loads(ctx_text), ensure_ascii=False, indent=2))


def run_shared_user_flow(env_files: list[Path]):
    print("=== 共享数据库用户 TCPS 连接 ===")
    connection = connect_shared_user(env_files)
    try:
        with connection.cursor() as cursor:
            query_session_context(cursor)

        if not os.environ.get("DB_ACCESS_TOKEN"):
            print("\n未提供 DB_ACCESS_TOKEN，当前仅验证 TCPS + wallet 共享账号连通性。")
            print("如需调用 set_end_user_security_context()，还需提供 DB_ACCESS_TOKEN 以及 END_USER_TOKEN，")
            print("或同时提供 END_USER_IDENTITY 与 END_USER_KEY。")
            return

        context, identity_desc = build_end_user_context()
        connection.set_end_user_security_context(context)
        try:
            print(f"\n已设置 end-user security context ({identity_desc})，再次查询会话上下文:")
            with connection.cursor() as cursor:
                query_session_context(cursor)
        finally:
            connection.clear_end_user_security_context()
            print("\n已清理 end-user security context。")
    finally:
        connection.close()
        print("共享连接已关闭。")


def run_local_end_user_probe(env_files: list[Path]):
    username = os.environ.get("LOCAL_END_USER_USERNAME")
    if not username:
        print("\n未设置 LOCAL_END_USER_USERNAME，跳过本地 END USER 直连测试。")
        return

    print("\n=== 本地 END USER TCPS 直连 ===")
    connection = connect_local_end_user(env_files)
    try:
        with connection.cursor() as cursor:
            query_session_context(cursor)
    finally:
        connection.close()
        print("本地 END USER 连接已关闭。")


def main():
    env_files = load_env()
    if env_files:
        print("已加载环境文件:")
        for env_file in env_files:
            print(f"  - {env_file}")
    else:
        print("未找到环境文件，将仅使用当前进程环境变量。")
    oracledb.defaults.fetch_lobs = False

    try:
        run_shared_user_flow(env_files)
        run_local_end_user_probe(env_files)
    except RuntimeError as exc:
        print(f"配置异常: {exc}")
    except oracledb.DatabaseError as exc:
        err_obj = exc.args[0]
        print(f"数据库异常: {err_obj.code} : {err_obj.message}")


if __name__ == "__main__":
    main()
