from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import ensure_domain_access, get_current_user
from app.schemas.schemas import ApiResponse, CommentsUpdateRequest, SourceDataQueryRequest, SourceDataResponse, GraphInstanceQueryRequest, GraphInstanceLineageRequest
from app.models.models import SysDataSource, SysOntologyEntity, SysOntologyRelation, SysOntologyProperty, SysDomain
from app.services.source_data_service import SourceDataService

router = APIRouter(prefix="/ontology", tags=["本体浏览管理"])


def _normalize_object_name(value: str | None) -> str:
    """Compare Oracle object names regardless of owner, quoting, or case."""
    normalized = str(value or "").strip().replace('"', "").upper()
    return normalized.rsplit(".", 1)[-1]


def _enrich_topology_display_names(
    topology: dict,
    entities: list[SysOntologyEntity],
    relations: list[SysOntologyRelation],
) -> dict:
    """Use platform ontology metadata as the display layer for deployed graphs."""
    entity_by_table: dict[str, SysOntologyEntity] = {}
    for entity in entities:
        candidates = [entity.table_name]
        if entity.entity_name:
            candidates.extend([
                f"ONTO_NODE_{entity.entity_name.upper()}",
                f"ONTO_NODE_{entity.entity_name.upper()}_V",
            ])
        for table_name in candidates:
            normalized = _normalize_object_name(table_name)
            if normalized:
                entity_by_table[normalized] = entity

    relation_by_table = {
        _normalize_object_name(relation.relation_table_name): relation
        for relation in relations
        if _normalize_object_name(relation.relation_table_name)
    }

    for node in topology.get("nodes") or []:
        entity = entity_by_table.get(_normalize_object_name(node.get("tableName")))
        if not entity:
            continue
        technical_name = node.get("displayName") or node.get("name")
        node["displayName"] = entity.entity_display_name or entity.entity_name or technical_name
        node["technicalName"] = technical_name
        node["entityId"] = entity.entity_id
        node["entityName"] = entity.entity_name

    for edge in topology.get("edges") or []:
        relation = relation_by_table.get(_normalize_object_name(edge.get("relationTableName")))
        if not relation:
            continue
        technical_name = edge.get("name") or edge.get("relationTableName")
        edge["name"] = relation.relation_name or relation.relation_type or technical_name
        edge["technicalName"] = technical_name
        edge["relationId"] = relation.relation_id

    return topology


@router.get("/graph", response_model=ApiResponse)
async def get_ontology_graph(
    source_id: str = Query(..., description="执行 DDL 的目标对象数据库"),
    domain_id: str = Query(..., description="当前全局业务分析域"),
    graph_name: str | None = Query(default=None, description="Oracle Property Graph 名称"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """从 Oracle Property Graph 数据字典读取实际拓扑。"""
    ensure_domain_access(db, current_user, domain_id)
    source = db.query(SysDataSource).filter(
        SysDataSource.source_id == source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not source:
        raise HTTPException(status_code=400, detail="目标对象数据库不存在或未启用")
    if (source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="本体图谱浏览当前仅支持 Oracle 数据源")
    if source.business_domain_id != domain_id:
        raise HTTPException(status_code=400, detail="目标对象数据库不属于当前业务分析域")
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
    entity_query = db.query(SysOntologyEntity)
    relation_query = db.query(SysOntologyRelation)
    if source.business_domain_id:
        entity_query = entity_query.filter(SysOntologyEntity.domain_id == source.business_domain_id)
        relation_query = relation_query.filter(SysOntologyRelation.domain_id == source.business_domain_id)
    # 部分历史数据源尚未绑定分析域。此时仍可根据已部署节点/边表的
    # 精确名称反查平台元数据，让图谱浏览显示中文名；未命中的对象保留
    # Oracle 原始 Label，不会被错误覆盖。
    topology = _enrich_topology_display_names(
        topology,
        entity_query.all(),
        relation_query.all(),
    )
    return ApiResponse(data=topology)


@router.post("/graph/instances", response_model=ApiResponse)
async def query_ontology_graph_instances(
    req: GraphInstanceQueryRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    ensure_domain_access(db, current_user, req.domain_id)
    source = db.query(SysDataSource).filter(
        SysDataSource.source_id == req.source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not source or source.business_domain_id != req.domain_id:
        raise HTTPException(status_code=400, detail="目标对象数据库不属于当前业务分析域")
    try:
        data = SourceDataService(db).get_remote_property_graph_instances(
            source_id=req.source_id, graph_name=req.graph_name, node_id=req.node_id,
            property_name=req.property_name, operator=req.operator, value=req.value,
            row_limit=req.row_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取图谱实例失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/graph/instances/lineage", response_model=ApiResponse)
async def query_ontology_graph_instance_lineage(req: GraphInstanceLineageRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    ensure_domain_access(db, current_user, req.domain_id)
    source = db.query(SysDataSource).filter(SysDataSource.source_id == req.source_id, SysDataSource.is_active == "Y").first()
    if not source or source.business_domain_id != req.domain_id:
        raise HTTPException(status_code=400, detail="目标对象数据库不属于当前业务分析域")
    try:
        data = SourceDataService(db).get_remote_property_graph_instance_lineage(req.source_id, req.graph_name, req.node_id, req.instance_key, req.max_depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取图谱实例链路失败: {str(exc)}")
    return ApiResponse(data=data)


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
