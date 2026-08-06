from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user
from app.schemas.schemas import (
    ApiResponse,
    DataObjectCommentGenerateRequest, DataObjectCommentSaveRequest, GraphQueryRequest
)
from app.models.models import SysDataSource
from app.services.source_data_service import SourceDataService

router = APIRouter(prefix="/source", tags=["源数据浏览管理"])


@router.post("/graph-query", response_model=ApiResponse)
async def execute_graph_query(
    req: GraphQueryRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """在选择的业务 Oracle 数据源上执行只读 Graph SQL。"""
    source = db.query(SysDataSource).filter(
        SysDataSource.source_id == req.source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not source:
        raise HTTPException(status_code=400, detail="数据源不存在或未启用")
    if (source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="图数据查询仅支持 Oracle 数据源")
    if source.business_domain_id and source.business_domain_id != req.domain_id:
        raise HTTPException(status_code=400, detail="数据源不属于当前业务分析域")
    service = SourceDataService(db)
    try:
        data = service.execute_remote_graph_query(
            source_id=req.source_id,
            graph_sql=req.graph_sql,
            schema=req.schema,
            row_limit=req.row_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"执行 Oracle Graph SQL 失败: {str(exc)}")
    return ApiResponse(data=data)


@router.get("/datasources", response_model=ApiResponse)
async def list_browse_data_sources(
    domain_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """获取可用于源数据浏览的数据源列表"""
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    return ApiResponse(data=service.get_available_data_sources(domain_id=domain_id))


@router.get("/datasources/{source_id}/schemas", response_model=ApiResponse)
async def list_source_schemas(
    source_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """获取数据源 schema 列表"""
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    try:
        data = service.get_source_schemas(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取 schema 列表失败: {str(exc)}")
    return ApiResponse(data=data)


@router.get("/datasources/{source_id}/tables", response_model=ApiResponse)
async def list_remote_source_tables(
    source_id: str,
    schema: Optional[str] = None,
    prefix: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """按数据源和 schema 浏览表"""
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    try:
        data = service.get_remote_tables(source_id, schema=schema, prefix=prefix, search=search)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取表列表失败: {str(exc)}")
    return ApiResponse(data=data)


@router.get("/datasources/{source_id}/tables/{table_name}/detail", response_model=ApiResponse)
async def get_remote_table_detail(
    source_id: str,
    table_name: str,
    schema: Optional[str] = None,
    sample_limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """获取表字段详情和样例数据"""
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    try:
        data = service.get_remote_table_detail(
            source_id,
            table_name,
            schema=schema,
            sample_limit=sample_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取表详情失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/datasources/{source_id}/tables/{table_name}/annotation/generate", response_model=ApiResponse)
async def generate_data_object_comments(
    source_id: str,
    table_name: str,
    req: DataObjectCommentGenerateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """为表和字段生成 comments 建议"""
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    try:
        data = await service.generate_remote_table_comment_suggestions(
            source_id=source_id,
            table_name=table_name,
            schema=req.schema,
            sample_limit=req.sample_limit,
            primary_model_config_id=req.primary_model_config_id,
            verifier_model_config_id=req.verifier_model_config_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成描述建议失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/datasources/{source_id}/tables/{table_name}/annotation/save", response_model=ApiResponse)
async def save_data_object_comments(
    source_id: str,
    table_name: str,
    req: DataObjectCommentSaveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """保存表和字段 comments 到外部数据源"""
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    try:
        data = service.save_remote_table_comments(
            source_id=source_id,
            table_name=table_name,
            schema=req.schema,
            table_comment=req.table_comment,
            column_comments=[item.model_dump() for item in req.column_comments],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存 comments 失败: {str(exc)}")
    return ApiResponse(data=data, message="comments 已保存")
