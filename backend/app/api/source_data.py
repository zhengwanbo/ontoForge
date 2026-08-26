import json
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import ensure_domain_access, get_current_user
from app.schemas.schemas import (
    ApiResponse,
    DataObjectCommentGenerateRequest, DataObjectCommentSaveRequest, GraphQueryRequest
)
from app.models.models import SysDataSource, SysDomain, SysOntologyBlueprint, SysGraphQueryRecommendation
from app.services.llm_service import LLMService
from app.services.source_data_service import SourceDataService

router = APIRouter(prefix="/source", tags=["源数据浏览管理"])


def _ensure_graph_recommendation_table(db: Session) -> None:
    """Support existing Oracle installations without a separate migration step."""
    SysGraphQueryRecommendation.__table__.create(bind=db.bind, checkfirst=True)


def _get_graph_query_recommendation(
    db: Session, domain_id: str, source_id: str, schema_name: str, graph_name: str,
) -> SysGraphQueryRecommendation | None:
    _ensure_graph_recommendation_table(db)
    return db.query(SysGraphQueryRecommendation).filter(
        SysGraphQueryRecommendation.domain_id == domain_id,
        SysGraphQueryRecommendation.source_id == source_id,
        SysGraphQueryRecommendation.schema_name == schema_name.upper(),
        SysGraphQueryRecommendation.graph_name == graph_name.upper(),
    ).order_by(SysGraphQueryRecommendation.updated_at.desc()).first()


def _load_cached_recommendations(record: SysGraphQueryRecommendation | None) -> list[dict]:
    if not record:
        return []
    try:
        items = json.loads(record.recommendations_json or "[]")
        return items if isinstance(items, list) else []
    except (TypeError, ValueError):
        return []


def _graph_identifier(value: str) -> str:
    """Return a safe unquoted Oracle Property Graph identifier."""
    identifier = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", identifier):
        raise ValueError(f"Oracle 属性图对象名称不合法: {value}")
    return identifier


def _build_graph_query_recommendations(topology: dict) -> list[dict[str, str]]:
    """Build topology-aware read-only fallbacks when the LLM is unavailable.

    These are intentionally generic. Business-specific names and SQL are produced
    by LLMService from the current domain and the live graph metadata.
    """
    graph_name = _graph_identifier(topology.get("graph_name") or "")
    vertex = lambda name: f"JSON_SERIALIZE(VERTEX_ID({name}) RETURNING VARCHAR2(4000))"
    nodes = topology.get("nodes") or []
    edges = topology.get("edges") or []

    def safe_label(item: dict) -> str:
        label = str(item.get("displayName") or item.get("name") or "").upper()
        return label if re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", label) else ""

    labels = [safe_label(item) for item in nodes]
    labels = [item for item in labels if item]
    first_label = labels[0] if labels else ""
    second_label = labels[1] if len(labels) > 1 else first_label
    first_name = str((nodes[0] if nodes else {}).get("displayName") or first_label or "实体")
    second_name = str((nodes[1] if len(nodes) > 1 else {}).get("displayName") or second_label or "关联实体")
    edge_name = str((edges[0] if edges else {}).get("name") or "关联")

    def graph_query(match: str, columns: str) -> str:
        return f"SELECT *\nFROM GRAPH_TABLE(\n  {graph_name}\n  MATCH {match}\n  COLUMNS (\n{columns}\n  )\n)"

    direct_columns = f"    {vertex('src')} AS SOURCE_ID, {vertex('src')} AS SOURCE_LABEL,\n    {vertex('dst')} AS TARGET_ID, {vertex('dst')} AS TARGET_LABEL,\n    '{edge_name}' AS RELATION_NAME"
    two_hop_columns = f"    {vertex('src')} AS SOURCE_ID, {vertex('src')} AS SOURCE_LABEL,\n    {vertex('dst')} AS TARGET_ID, {vertex('dst')} AS TARGET_LABEL,\n    '两跳关联' AS RELATION_NAME, {vertex('mid')} AS MIDDLE_ID"
    source_pattern = f"(src IS {first_label})-[rel]->(dst)" if first_label else "(src)-[rel]->(dst)"
    target_pattern = f"(src)-[rel]->(dst IS {second_label})" if second_label else "(src)-[rel]->(dst)"
    pair_pattern = f"(src IS {first_label})-[rel]->(dst IS {second_label})" if first_label and second_label else "(src)-[rel]->(dst)"

    return [
        {"id": "graph-overview", "title": "全图直接关联概览", "description": "浏览当前属性图中全部直接关联，确认可查询的实体连接情况。", "graph_name": graph_name, "sql": graph_query("(src)-[rel]->(dst)", direct_columns)},
        {"id": "two-hop-path", "title": "两跳业务链路发现", "description": "发现经由中间实体形成的两跳业务关联，用于梳理可追溯链路。", "graph_name": graph_name, "sql": graph_query("(src)-[rel1]->(mid)-[rel2]->(dst)", two_hop_columns)},
        {"id": "source-object-explore", "title": f"{first_name}关联对象探索", "description": f"从 {first_name} 出发查看其下游关联对象和 {edge_name} 关系。", "graph_name": graph_name, "sql": graph_query(source_pattern, direct_columns)},
        {"id": "target-object-explore", "title": f"{second_name}上游关联探索", "description": f"定位与 {second_name} 直接相连的上游对象，辅助核查数据关系。", "graph_name": graph_name, "sql": graph_query(target_pattern, direct_columns)},
        {"id": "object-pair-check", "title": f"{first_name}与{second_name}关系核查", "description": "核查两个代表性实体之间是否存在可用的直接图关系。", "graph_name": graph_name, "sql": graph_query(pair_pattern, direct_columns)},
        {"id": "relation-sample", "title": "关系实例抽样核查", "description": "抽取属性图中的关系实例，作为后续按业务条件编写查询的起点。", "graph_name": graph_name, "sql": graph_query("(src)-[rel]->(dst)", direct_columns)},
    ]


@router.get("/graph-query/recommendations", response_model=ApiResponse)
async def get_graph_query_recommendations(
    domain_id: str = Query(...),
    source_id: str = Query(...),
    schema: Optional[str] = Query(default=None),
    graph_name: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Generate six common Graph SQL samples from the selected live Property Graph."""
    ensure_domain_access(db, current_user, domain_id)
    source = db.query(SysDataSource).filter(
        SysDataSource.source_id == source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not source:
        raise HTTPException(status_code=400, detail="数据源不存在或未启用")
    if (source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="图数据查询仅支持 Oracle 数据源")
    if source.business_domain_id and source.business_domain_id != domain_id:
        raise HTTPException(status_code=400, detail="数据源不属于当前业务分析域")
    try:
        topology = SourceDataService(db).get_remote_property_graph_topology(
            source_id=source_id,
            graph_name=graph_name,
            schema=schema or source.schema_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Oracle Property Graph 元数据失败: {str(exc)}")
    if not topology.get("graph_name"):
        raise HTTPException(status_code=400, detail="当前业务分析域的目标数据库中没有可用的 Oracle 属性图")
    cached = _get_graph_query_recommendation(
        db, domain_id, source_id, topology.get("schema") or schema or "", topology["graph_name"],
    )
    recommendations = _load_cached_recommendations(cached)
    cached_errors: list[str] = []
    # 新机制保存的计划已经做过结构、语法及命中预检，可直接复用；旧缓存
    # 没有 query_plan 时才做一次兼容性检查，防止历史自由 SQL 继续误导用户。
    if recommendations and not all(isinstance(item, dict) and item.get("query_plan") for item in recommendations):
        graph_service = SourceDataService(db)
        for item in recommendations:
            try:
                graph_service.validate_remote_graph_query(
                    source_id=source_id,
                    graph_sql=str(item.get("sql") or ""),
                    schema=topology.get("schema") or schema or source.schema_name,
                )
                if not graph_service.probe_remote_graph_query(
                    source_id=source_id,
                    graph_sql=str(item.get("sql") or ""),
                    schema=topology.get("schema") or schema or source.schema_name,
                ):
                    raise ValueError("完整业务路径当前没有命中实例数据")
            except Exception as exc:
                cached_errors.append(str(exc))
                break
        if cached_errors:
            recommendations = []
    return ApiResponse(data={
        "graph_name": topology["graph_name"],
        "graphs": topology.get("graphs") or [],
        "recommendations": recommendations,
        "generation_mode": "cached" if recommendations else "idle",
        "generation_message": "已显示此前生成并保存的 6 条业务场景与 Graph SQL；已通过 Oracle 语法和路径命中预检。" if recommendations else ("此前保存的 Graph SQL 未通过当前 Oracle 语法或路径命中预检，请点击“生成业务场景与 SQL”修复。" if cached_errors else "尚未生成业务场景。请点击“生成业务场景与 SQL”，系统将按当前业务分析域和属性图生成 6 条查询。"),
        "generated_at": cached.updated_at.isoformat() if cached and recommendations else None,
    })


@router.post("/graph-query/recommendations/generate", response_model=ApiResponse)
async def generate_graph_query_recommendations(
    domain_id: str = Query(...),
    source_id: str = Query(...),
    schema: Optional[str] = Query(default=None),
    graph_name: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Explicitly regenerate and persist six LLM business graph query scenarios."""
    ensure_domain_access(db, current_user, domain_id)
    source = db.query(SysDataSource).filter(
        SysDataSource.source_id == source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not source:
        raise HTTPException(status_code=400, detail="数据源不存在或未启用")
    if (source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="图数据查询仅支持 Oracle 数据源")
    if source.business_domain_id and source.business_domain_id != domain_id:
        raise HTTPException(status_code=400, detail="数据源不属于当前业务分析域")
    try:
        topology = SourceDataService(db).get_remote_property_graph_topology(
            source_id=source_id,
            graph_name=graph_name,
            schema=schema or source.schema_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Oracle Property Graph 元数据失败: {str(exc)}")
    if not topology.get("graph_name"):
        raise HTTPException(status_code=400, detail="当前业务分析域的目标数据库中没有可用的 Oracle 属性图")

    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    blueprint = db.query(SysOntologyBlueprint).filter(
        SysOntologyBlueprint.domain_id == domain_id,
    ).order_by(SysOntologyBlueprint.updated_at.desc()).first()
    graph_service = SourceDataService(db)
    try:
        graph_contract = graph_service.get_remote_property_graph_query_contract(
            source_id=source_id,
            topology=topology,
            schema=topology.get("schema") or schema or source.schema_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取属性图节点、边、属性及数据概况失败: {str(exc)}")

    def compile_and_validate(plans: list[dict]) -> tuple[list[dict], list[str]]:
        compiled: list[dict] = []
        errors: list[str] = []
        for plan in plans:
            try:
                recommendation = graph_service.compile_property_graph_query_plan(graph_contract, plan)
                graph_service.validate_remote_graph_query(
                    source_id=source_id,
                    graph_sql=recommendation["sql"],
                    schema=graph_contract.get("schema") or source.schema_name,
                )
                if not graph_service.probe_remote_graph_query(
                    source_id=source_id,
                    graph_sql=recommendation["sql"],
                    schema=graph_contract.get("schema") or source.schema_name,
                ):
                    raise ValueError("该完整业务路径当前没有命中实例数据，请使用已有数据的关系或缩短路径")
                compiled.append(recommendation)
            except Exception as exc:
                errors.append(f"{plan.get('title') or plan.get('id')}: {str(exc)}")
        if len(compiled) != 6 and not errors:
            errors.append("模型未返回恰好 6 条可编译的查询计划")
        return compiled, errors

    try:
        generated = await LLMService(db).generate_property_graph_query_recommendations(
            domain=domain,
            graph_topology=graph_contract,
            blueprint=blueprint,
        )
    except Exception as exc:
        # 查询页面仍应可用；模型或上下文异常不能阻断用户查看实时图结构。
        generated = {"plans": [], "generation_mode": "fallback", "generation_error": str(exc)}
    plans = generated.get("plans") or []
    recommendations, errors = compile_and_validate(plans) if plans else ([], [generated.get("generation_error") or "模型未生成查询计划"])
    if errors:
        try:
            generated = await LLMService(db).generate_property_graph_query_recommendations(
                domain=domain,
                graph_topology=graph_contract,
                blueprint=blueprint,
                validation_feedback=errors,
            )
            plans = generated.get("plans") or []
            recommendations, errors = compile_and_validate(plans) if plans else ([], [generated.get("generation_error") or "模型未生成修复后的查询计划"])
        except Exception as exc:
            recommendations = []
            errors = [str(exc)]
    if not recommendations or errors:
        # 不以保底查询覆盖已有业务场景；用户可以继续使用最近一次有效结果。
        cached = _get_graph_query_recommendation(
            db, domain_id, source_id, topology.get("schema") or schema or "", topology["graph_name"],
        )
        cached_items = _load_cached_recommendations(cached)
        if cached_items:
            return ApiResponse(data={
                "graph_name": topology["graph_name"], "graphs": topology.get("graphs") or [],
                "recommendations": cached_items, "generation_mode": "cached",
                "generation_message": "本次模型未生成有效的 6 条查询，已保留并显示此前保存的业务场景。",
                "generated_at": cached.updated_at.isoformat(),
            })
        return ApiResponse(data={
            "graph_name": topology["graph_name"], "graphs": topology.get("graphs") or [],
            "recommendations": _build_graph_query_recommendations(topology), "generation_mode": "fallback",
            "generation_message": "模型未生成通过 Oracle 语法预检的 6 条查询，当前展示基于实时属性图结构的保底探索查询；可稍后再次点击生成。",
        })

    schema_name = str(topology.get("schema") or schema or "").upper()
    record = _get_graph_query_recommendation(db, domain_id, source_id, schema_name, topology["graph_name"])
    if not record:
        record = SysGraphQueryRecommendation(
            domain_id=domain_id, source_id=source_id, schema_name=schema_name,
            graph_name=topology["graph_name"].upper(), generated_by=current_user.get("user_id"),
        )
        db.add(record)
    record.recommendations_json = json.dumps(recommendations, ensure_ascii=False)
    record.generation_mode = "llm"
    record.generated_by = current_user.get("user_id")
    db.commit()
    db.refresh(record)
    return ApiResponse(data={
        "graph_name": topology["graph_name"],
        "graphs": topology.get("graphs") or [],
        "recommendations": recommendations,
        "generation_mode": "llm",
        "generation_message": "已基于当前业务分析域、属性图 DDL、顶点/边及其全部属性生成；6 条 SQL 均已通过 Oracle 语法预检并保存。",
        "generated_at": record.updated_at.isoformat(),
    })


@router.post("/graph-query", response_model=ApiResponse)
async def execute_graph_query(
    req: GraphQueryRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """在选择的业务 Oracle 数据源上执行只读 Graph SQL。"""
    ensure_domain_access(db, current_user, req.domain_id)
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
    if domain_id:
        ensure_domain_access(db, current_user, domain_id)
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
