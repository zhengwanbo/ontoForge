from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user
from app.schemas.schemas import ApiResponse, CommentsUpdateRequest, SourceDataQueryRequest, SourceDataResponse
from app.models.models import SysDataSource, SysOntologyEntity, SysOntologyRelation, SysOntologyProperty, SysDomain
from app.services.source_data_service import SourceDataService

router = APIRouter(prefix="/ontology", tags=["本体浏览管理"])

@router.get("/graph", response_model=ApiResponse)
async def get_ontology_graph(
    source_id: str = Query(..., description="执行 DDL 的目标对象数据库"),
    graph_name: str | None = Query(default=None, description="Oracle Property Graph 名称"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """从 Oracle Property Graph 数据字典读取实际拓扑。"""
    source = db.query(SysDataSource).filter(
        SysDataSource.source_id == source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not source:
        raise HTTPException(status_code=400, detail="目标对象数据库不存在或未启用")
    if (source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="本体图谱浏览当前仅支持 Oracle 数据源")
    try:
        topology = SourceDataService(db).get_remote_property_graph_topology(
            source_id=source_id,
            graph_name=graph_name,
            schema=source.schema_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Oracle Property Graph 元数据失败: {str(exc)}")
    return ApiResponse(data=topology)


@router.put("/tables/{table_name}/comments", response_model=ApiResponse)
async def update_table_comments(
    table_name: str,
    req: CommentsUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """修改已构建对象的Comments"""
    # Find entity by table_name
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.table_name == table_name).first()
    if entity:
        entity.entity_desc = req.comments
        entity.updated_at = datetime.utcnow()
        db.commit()
    return ApiResponse(message="Comments已更新")


@router.post("/tables/{table_name}/columns", response_model=ApiResponse)
async def add_column(
    table_name: str,
    req: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """为已构建节点添加属性"""
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.table_name == table_name).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")

    from app.models.models import generate_id, SysOntologyProperty
    prop = SysOntologyProperty(
        property_id=generate_id("prop"),
        entity_id=entity.entity_id,
        property_name=req.get("property_name"),
        property_display_name=req.get("property_display_name"),
        data_type=req.get("data_type", "VARCHAR2"),
        is_primary_key=req.get("is_primary_key", "N"),
        is_nullable=req.get("is_nullable", "Y"),
        property_desc=req.get("property_desc")
    )
    db.add(prop)
    db.commit()
    return ApiResponse(data={"property_id": prop.property_id})


@router.post("/ontology/relations", response_model=ApiResponse)
async def add_relation(
    req: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """添加新关系"""
    from app.models.models import generate_id, SysOntologyRelation

    # Verify entities exist
    source = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == req.get("source_entity_id")).first()
    target = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == req.get("target_entity_id")).first()
    if not source or not target:
        raise HTTPException(status_code=400, detail="源实体或目标实体不存在")

    relation = SysOntologyRelation(
        relation_id=generate_id("rel"),
        domain_id=req.get("domain_id"),
        source_entity_id=req.get("source_entity_id"),
        target_entity_id=req.get("target_entity_id"),
        relation_name=req.get("relation_name"),
        relation_type=req.get("relation_type", "ASSOCIATION"),
        relation_desc=req.get("relation_desc")
    )
    db.add(relation)
    db.commit()
    return ApiResponse(data={"relation_id": relation.relation_id})


from datetime import datetime
