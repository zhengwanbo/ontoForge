from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.schemas.schemas import ApiResponse, DataSourceCreate, DataSourceUpdate, DataSourceResponse
from sqlalchemy import or_
from app.models.models import SysDataSource, SysDomain, generate_id

router = APIRouter(prefix="/system/datasources", tags=["系统-数据源管理"])
logger = get_logger(__name__)


def _normalize_sql(sql: str) -> str:
    return " ".join((sql or "").split())


def _log_remote_sql_execute(source: SysDataSource, sql: str, params: Optional[dict] = None):
    logger.info(
        "REMOTE SQL execute: source=%s schema=%s sql=%s",
        source.source_name,
        source.schema_name or source.username,
        _normalize_sql(sql),
    )
    logger.debug("REMOTE SQL params: %s", params or {})


def _execute_remote_sql(cursor, source: SysDataSource, sql: str, params: Optional[dict] = None):
    _log_remote_sql_execute(source, sql, params)
    try:
        cursor.execute(sql, params or {})
    except Exception as exc:
        logger.exception(
            "REMOTE SQL failed: source=%s sql=%s error=%s",
            source.source_name,
            _normalize_sql(sql),
            str(exc),
        )
        raise


def _fetchone_logged(cursor, source: SysDataSource, label: str):
    row = cursor.fetchone()
    logger.info("REMOTE SQL result: source=%s label=%s row=%s", source.source_name, label, row)
    return row


def _fetchall_logged(cursor, source: SysDataSource, label: str):
    rows = cursor.fetchall()
    logger.info(
        "REMOTE SQL result: source=%s label=%s row_count=%s preview=%s",
        source.source_name,
        label,
        len(rows),
        rows[:5],
    )
    return rows


@router.get("", response_model=ApiResponse)
async def list_data_sources(
    business_domain_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    query = db.query(SysDataSource, SysDomain.domain_name).outerjoin(
        SysDomain,
        SysDomain.domain_id == SysDataSource.business_domain_id,
    )
    if business_domain_id:
        query = query.filter(
            or_(
                SysDataSource.business_domain_id == business_domain_id,
                SysDataSource.business_domain_id.is_(None),
            )
        )
    sources = query.order_by(SysDataSource.created_at.desc()).all()
    data = [DataSourceResponse(
        source_id=source.source_id,
        source_name=source.source_name,
        source_desc=source.source_desc,
        db_type=source.db_type,
        host=source.host,
        port=source.port,
        service_name=source.service_name,
        sid=source.sid,
        username=source.username,
        schema_name=source.schema_name,
        business_domain_id=source.business_domain_id,
        business_domain_name=domain_name,
        is_active=source.is_active,
        is_default=source.is_default,
        connection_status=source.connection_status,
        last_test_time=source.last_test_time,
        created_by=source.created_by,
        created_at=source.created_at
    ).model_dump() for source, domain_name in sources]
    return ApiResponse(data=data)


@router.post("", response_model=ApiResponse)
async def create_data_source(
    req: DataSourceCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if req.business_domain_id:
        domain = db.query(SysDomain).filter(SysDomain.domain_id == req.business_domain_id).first()
        if not domain:
            raise HTTPException(status_code=400, detail="业务分析域不存在")

    # Simple encryption for password (replace with real encryption in production)
    password_enc = _simple_encrypt(req.password)

    source = SysDataSource(
        source_id=generate_id("ds"),
        source_name=req.source_name,
        source_desc=req.source_desc,
        db_type=req.db_type,
        host=req.host,
        port=req.port,
        service_name=req.service_name,
        sid=req.sid,
        username=req.username,
        password_enc=password_enc,
        schema_name=req.schema_name,
        business_domain_id=req.business_domain_id,
        is_default="Y" if req.is_default else "N",
        created_by=current_user.get("username", "unknown"),
        connection_status="UNKNOWN"
    )

    # Unset other defaults if this is default
    if req.is_default:
        db.query(SysDataSource).update({SysDataSource.is_default: "N"})

    db.add(source)
    db.commit()
    db.refresh(source)
    return ApiResponse(data={"source_id": source.source_id, "source_name": source.source_name})


@router.put("/{source_id}", response_model=ApiResponse)
async def update_data_source(
    source_id: str,
    req: DataSourceUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    source = db.query(SysDataSource).filter(SysDataSource.source_id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    payload = req.model_dump(exclude_unset=True)
    if "business_domain_id" in payload:
        if req.business_domain_id:
            domain = db.query(SysDomain).filter(SysDomain.domain_id == req.business_domain_id).first()
            if not domain:
                raise HTTPException(status_code=400, detail="业务分析域不存在")

    for field, value in payload.items():
        if field == "password" and value:
            source.password_enc = _simple_encrypt(value)
        elif field == "is_default":
            if value:
                db.query(SysDataSource).update({SysDataSource.is_default: "N"})
            source.is_default = "Y" if value else "N"
        elif field == "is_active":
            source.is_active = "Y" if value else "N"
        else:
            setattr(source, field, value)

    source.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="数据源已更新")


@router.delete("/{source_id}", response_model=ApiResponse)
async def delete_data_source(
    source_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    source = db.query(SysDataSource).filter(SysDataSource.source_id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    db.delete(source)
    db.commit()
    return ApiResponse(message="数据源已删除")


@router.post("/{source_id}/test", response_model=ApiResponse)
async def test_connection(
    source_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """测试Oracle数据源连接"""
    source = db.query(SysDataSource).filter(SysDataSource.source_id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    import time
    start_time = time.time()

    try:
        # Decrypt password
        password = _simple_decrypt(source.password_enc)

        # Build connection string
        if source.service_name:
            dsn = f"{source.host}:{source.port}/{source.service_name}"
        elif source.sid:
            dsn = f"{source.host}:{source.port}/{source.sid}"
        else:
            dsn = f"{source.host}:{source.port}"

        # Try to connect
        import oracledb
        connection = oracledb.connect(
            user=source.username,
            password=password,
            dsn=dsn
        )

        # Get basic info
        cursor = connection.cursor()
        _execute_remote_sql(cursor, source, "SELECT BANNER FROM v$version WHERE ROWNUM <= 1")
        version = _fetchone_logged(cursor, source, "db_version")
        cursor.close()
        connection.close()

        duration = round(time.time() - start_time, 2)
        source.connection_status = "CONNECTED"
        source.last_test_time = datetime.utcnow()
        db.commit()

        return ApiResponse(data={
            "success": True,
            "duration": duration,
            "db_version": version[0] if version else "Oracle Database",
            "message": f"连接成功! 耗时{duration}秒"
        })
    except Exception as e:
        duration = round(time.time() - start_time, 2)
        source.connection_status = "DISCONNECTED"
        source.last_test_time = datetime.utcnow()
        db.commit()

        return ApiResponse(data={
            "success": False,
            "duration": duration,
            "error": str(e),
            "message": f"连接失败: {str(e)[:100]}"
        })


@router.get("/{source_id}/tables", response_model=ApiResponse)
async def list_source_tables(
    source_id: str,
    schema: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """从Oracle数据源获取表列表"""
    source = db.query(SysDataSource).filter(SysDataSource.source_id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    password = _simple_decrypt(source.password_enc)
    schema_name = schema or source.schema_name or source.username.upper()

    try:
        import oracledb
        if source.service_name:
            dsn = f"{source.host}:{source.port}/{source.service_name}"
        elif source.sid:
            dsn = f"{source.host}:{source.port}/{source.sid}"
        else:
            dsn = f"{source.host}:{source.port}"

        connection = oracledb.connect(user=source.username, password=password, dsn=dsn)
        cursor = connection.cursor()

        # Get current user info
        _execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
        current_user = _fetchone_logged(cursor, source, "connected_user")[0]

        # Use provided schema, or fall back to current user
        schema_name = schema or source.schema_name or current_user

        # Try multiple queries to find tables. Table comments live in *_TAB_COMMENTS,
        # not *_TABLES, so join them explicitly.
        queries = [
            # 1. ALL_TABLES for specific owner (requires SELECT_CATALOG_ROLE or direct grant)
            ("""
             SELECT t.OWNER, t.TABLE_NAME, c.COMMENTS, t.NUM_ROWS
             FROM ALL_TABLES t
             LEFT JOIN ALL_TAB_COMMENTS c
               ON c.OWNER = t.OWNER
              AND c.TABLE_NAME = t.TABLE_NAME
              AND c.TABLE_TYPE = 'TABLE'
             WHERE t.OWNER = :owner
             ORDER BY t.TABLE_NAME
             """,
             {"owner": schema_name.upper()}),
            # 2. USER_TABLES for connected user (always works)
            ("""
             SELECT USER AS OWNER, t.TABLE_NAME, c.COMMENTS, t.NUM_ROWS
             FROM USER_TABLES t
             LEFT JOIN USER_TAB_COMMENTS c
               ON c.TABLE_NAME = t.TABLE_NAME
             ORDER BY t.TABLE_NAME
             """,
             {}),
        ]

        tables = []
        schema_used = schema_name

        for query, params in queries:
            try:
                _execute_remote_sql(cursor, source, query, params)
                rows = _fetchall_logged(cursor, source, "source_tables")
                if rows:
                    for row in rows:
                        table_entry = {
                            "owner": row[0],
                            "table_name": row[1],
                            "comments": row[2],
                            "num_rows": row[3] or 0
                        }
                        if search and search.upper() not in row[1].upper():
                            continue
                        if not any(t["owner"] == row[0] and t["table_name"] == row[1] for t in tables):
                            tables.append(table_entry)
                    if tables and "ALL_TABLES" in query:
                        schema_used = schema_name.upper()
                    elif tables and "USER_TABLES" in query:
                        schema_used = current_user
                    if tables:
                        break
            except Exception:
                continue

        cursor.close()
        connection.close()

        return ApiResponse(data={"schema": schema_used, "tables": tables, "connected_user": current_user})
    except Exception as e:
        return ApiResponse(code=500, message=f"获取表列表失败: {str(e)}", data={"tables": []})


@router.get("/{source_id}/tables/{table_name}/columns", response_model=ApiResponse)
async def list_source_columns(
    source_id: str,
    table_name: str,
    schema: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """从Oracle数据源获取表结构"""
    source = db.query(SysDataSource).filter(SysDataSource.source_id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    password = _simple_decrypt(source.password_enc)
    requested_schema = schema or source.schema_name

    try:
        import oracledb
        if source.service_name:
            dsn = f"{source.host}:{source.port}/{source.service_name}"
        else:
            dsn = f"{source.host}:{source.port}/{source.sid}" if source.sid else f"{source.host}:{source.port}"

        connection = oracledb.connect(user=source.username, password=password, dsn=dsn)
        cursor = connection.cursor()

        _execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
        connected_user = _fetchone_logged(cursor, source, "connected_user")[0]

        candidate_owners = []
        for owner in [requested_schema, source.username, connected_user]:
            if owner and owner.upper() not in candidate_owners:
                candidate_owners.append(owner.upper())

        columns = []
        schema_used = candidate_owners[0] if candidate_owners else connected_user
        actual_table_name = table_name

        column_sql = """
            SELECT
                c.OWNER,
                c.TABLE_NAME,
                c.COLUMN_NAME,
                c.DATA_TYPE || CASE
                    WHEN c.DATA_TYPE IN ('VARCHAR2','VARCHAR','CHAR','NCHAR','NVARCHAR2','RAW') THEN '(' || c.DATA_LENGTH || ')'
                    WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL AND c.DATA_SCALE IS NOT NULL THEN '(' || c.DATA_PRECISION || ',' || c.DATA_SCALE || ')'
                    WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL THEN '(' || c.DATA_PRECISION || ')'
                    ELSE ''
                END AS DATA_TYPE,
                CASE WHEN c.NULLABLE = 'Y' THEN 'Y' ELSE 'N' END AS NULLABLE,
                c.DATA_DEFAULT,
                c.COLUMN_ID,
                com.COMMENTS
            FROM ALL_TAB_COLUMNS c
            LEFT JOIN ALL_COL_COMMENTS com
                ON com.OWNER = c.OWNER
               AND com.TABLE_NAME = c.TABLE_NAME
               AND com.COLUMN_NAME = c.COLUMN_NAME
            WHERE c.OWNER = :own
              AND (c.TABLE_NAME = :tbl OR c.TABLE_NAME = :tbl_upper)
            ORDER BY c.COLUMN_ID
        """

        for owner in candidate_owners:
            _execute_remote_sql(cursor, source, column_sql, {
                "own": owner,
                "tbl": table_name,
                "tbl_upper": table_name.upper()
            })
            rows = _fetchall_logged(cursor, source, f"source_columns:{owner}.{table_name.upper()}")
            if rows:
                schema_used = rows[0][0]
                actual_table_name = rows[0][1]
                for row in rows:
                    columns.append({
                        "column_name": row[2],
                        "data_type": row[3],
                        "nullable": row[4],
                        "default_value": row[5] if row[5] else None,
                        "column_id": row[6],
                        "comments": row[7] if row[7] else None
                    })
                break

        if not columns:
            _execute_remote_sql(cursor, source, """
                SELECT
                    c.TABLE_NAME,
                    c.COLUMN_NAME,
                    c.DATA_TYPE || CASE
                        WHEN c.DATA_TYPE IN ('VARCHAR2','VARCHAR','CHAR','NCHAR','NVARCHAR2','RAW') THEN '(' || c.DATA_LENGTH || ')'
                        WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL AND c.DATA_SCALE IS NOT NULL THEN '(' || c.DATA_PRECISION || ',' || c.DATA_SCALE || ')'
                        WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL THEN '(' || c.DATA_PRECISION || ')'
                        ELSE ''
                    END AS DATA_TYPE,
                    CASE WHEN c.NULLABLE = 'Y' THEN 'Y' ELSE 'N' END AS NULLABLE,
                    c.DATA_DEFAULT,
                    c.COLUMN_ID,
                    com.COMMENTS
                FROM USER_TAB_COLUMNS c
                LEFT JOIN USER_COL_COMMENTS com
                    ON com.TABLE_NAME = c.TABLE_NAME
                   AND com.COLUMN_NAME = c.COLUMN_NAME
                WHERE c.TABLE_NAME = :tbl OR c.TABLE_NAME = :tbl_upper
                ORDER BY c.COLUMN_ID
            """, {"tbl": table_name, "tbl_upper": table_name.upper()})
            rows = _fetchall_logged(cursor, source, f"user_source_columns:{table_name.upper()}")
            if rows:
                schema_used = connected_user
                actual_table_name = rows[0][0]
                for row in rows:
                    columns.append({
                        "column_name": row[1],
                        "data_type": row[2],
                        "nullable": row[3],
                        "default_value": row[4] if row[4] else None,
                        "column_id": row[5],
                        "comments": row[6] if row[6] else None
                    })

        # Get table comment
        _execute_remote_sql(cursor, source, """
            SELECT COMMENTS FROM ALL_TAB_COMMENTS
            WHERE OWNER = :own AND TABLE_NAME = :tbl AND TABLE_TYPE = 'TABLE'
        """, {"own": schema_used, "tbl": actual_table_name})
        table_comment = _fetchone_logged(cursor, source, f"table_comment:{schema_used}.{actual_table_name}")
        table_comment = table_comment[0] if table_comment else None

        cursor.close()
        connection.close()

        return ApiResponse(data={
            "owner": schema_used,
            "table_name": actual_table_name,
            "table_comment": table_comment,
            "columns": columns
        })
    except Exception as e:
        return ApiResponse(code=500, message=f"获取表结构失败: {str(e)}", data={"columns": []})


def _simple_encrypt(text: str) -> str:
    """简单加密（生产环境建议使用更安全的加密方案）"""
    import base64
    return base64.b64encode(text.encode()).decode()


def _simple_decrypt(encoded: str) -> str:
    """简单解密"""
    import base64
    return base64.b64decode(encoded.encode()).decode()
