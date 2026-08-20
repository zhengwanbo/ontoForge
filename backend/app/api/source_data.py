import re
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


def _graph_identifier(value: str) -> str:
    """Return a safe unquoted Oracle Property Graph identifier."""
    identifier = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", identifier):
        raise ValueError(f"Oracle 属性图对象名称不合法: {value}")
    return identifier


def _build_graph_query_recommendations(topology: dict) -> list[dict[str, str]]:
    """Build six marketing-oriented, executable GRAPH_TABLE query samples."""
    graph_name = _graph_identifier(topology.get("graph_name") or "")
    vertex = lambda name: f"JSON_SERIALIZE(VERTEX_ID({name}) RETURNING VARCHAR2(4000))"
    edge = "JSON_SERIALIZE(EDGE_ID(rel) RETURNING VARCHAR2(4000))"
    labels = {str(node.get("displayName") or node.get("name") or "").upper() for node in (topology.get("nodes") or [])}

    def graph_query(match: str, columns: str) -> str:
        return f"SELECT *\nFROM GRAPH_TABLE(\n  {graph_name}\n  MATCH {match}\n  COLUMNS (\n{columns}\n  )\n)"

    fallback = (
        f"SELECT *\nFROM GRAPH_TABLE(\n  {graph_name}\n  MATCH (src)-[rel]->(dst)\n  COLUMNS (\n"
        f"    {vertex('src')} AS SOURCE_ID, {vertex('src')} AS SOURCE_LABEL,\n"
        f"    {vertex('dst')} AS TARGET_ID, {vertex('dst')} AS TARGET_LABEL,\n    {edge} AS RELATION_NAME\n  )\n)"
    )

    def scenario(item_id: str, title: str, description: str, required: set[str], match: str, columns: str) -> dict[str, str]:
        ready = required.issubset(labels)
        return {
            "id": item_id,
            "title": title,
            "description": description if ready else f"当前属性图缺少 {', '.join(sorted(required - labels))} 对象，已降级为全图关联探索。",
            "graph_name": graph_name,
            "sql": graph_query(match, columns) if ready else fallback,
        }

    return [
        scenario("promotion-effect-trace", "场景1：促销活动全链路效果追溯", "从促销活动追溯扫码触达、兑换订单和终端去向。", {"PROMOTIONACTIVITY", "CONSUMERSCAN", "EXCHANGEORDER", "RETAILSTORE"}, "(activity IS PROMOTIONACTIVITY)<-[rel]-(scan IS CONSUMERSCAN)-[]->(exchange_order IS EXCHANGEORDER)-[]->(store IS RETAILSTORE)", f"    {vertex('activity')} AS SOURCE_ID, activity.ACTIVITY_NAME AS SOURCE_LABEL,\n    {vertex('store')} AS TARGET_ID, store.STORE_NAME AS TARGET_LABEL,\n    '促销触达终端' AS RELATION_NAME, activity.ACTIVITY_CODE, activity.BUDGET_AMOUNT, scan.SCAN_TIME, exchange_order.ORDER_NO, exchange_order.PAY_AMOUNT"),
        scenario("channel-risk-root-cause", "场景2：窜货根因分析", "定位高风险窜货记录关联的经销商、商品与区域。", {"CHANNELRISK", "DISTRIBUTOR", "PRODUCTSKU", "REGION"}, "(risk IS CHANNELRISK)-[rel]->(distributor IS DISTRIBUTOR), (risk IS CHANNELRISK)-[]->(sku IS PRODUCTSKU), (risk IS CHANNELRISK)-[]->(region IS REGION)", f"    {vertex('risk')} AS SOURCE_ID, risk.CHANNEL_RISK_ID AS SOURCE_LABEL,\n    {vertex('distributor')} AS TARGET_ID, distributor.DISTRIBUTOR_NAME AS TARGET_LABEL,\n    '窜货风险关联经销商' AS RELATION_NAME, risk.RISK_LEVEL, risk.RISK_SCORE, risk.CROSS_SCAN_COUNT, sku.SKU_NAME, region.REGION_NAME"),
        scenario("low-sales-store", "场景3：低动销终端诊断", "按近 90 天零售订单定位终端、经销商与 SKU 的低动销线索。", {"RETAILORDER", "RETAILSTORE", "RETAILORDERITEM", "PRODUCTSKU"}, "(order_item IS RETAILORDERITEM)<-[rel]-(retail_order IS RETAILORDER)-[]->(store IS RETAILSTORE), (retail_order IS RETAILORDER)-[]->(order_item IS RETAILORDERITEM)-[]->(sku IS PRODUCTSKU)", f"    {vertex('retail_order')} AS SOURCE_ID, retail_order.ORDER_NO AS SOURCE_LABEL,\n    {vertex('store')} AS TARGET_ID, store.STORE_NAME AS TARGET_LABEL,\n    '终端零售订单' AS RELATION_NAME, retail_order.ORDER_DATE, retail_order.TOTAL_AMOUNT, order_item.QTY, sku.SKU_NAME"),
        scenario("distributor-profile", "场景4：经销商全链路画像", "查看经销商覆盖终端、采购订单与库存的关联画像。", {"DISTRIBUTOR", "RETAILSTORE", "PURCHASEORDER", "DISTRIBUTORINVENTORY"}, "(store IS RETAILSTORE)-[rel]->(distributor IS DISTRIBUTOR)<-[]-(purchase_order IS PURCHASEORDER), (inventory IS DISTRIBUTORINVENTORY)-[]->(distributor IS DISTRIBUTOR)", f"    {vertex('store')} AS SOURCE_ID, store.STORE_NAME AS SOURCE_LABEL,\n    {vertex('distributor')} AS TARGET_ID, distributor.DISTRIBUTOR_NAME AS TARGET_LABEL,\n    '经销商覆盖终端' AS RELATION_NAME, distributor.DISTRIBUTOR_CODE, distributor.LEVEL_TYPE, purchase_order.ORDER_NO, purchase_order.TOTAL_AMOUNT, inventory.AVAILABLE_QTY"),
        scenario("one-code-scan-insight", "场景5：一物一码扫码热度与消费者洞察", "查看消费者扫码、五码对象、商品和活动之间的关联，支持按时间与区域进一步筛选。", {"CONSUMERSCAN", "CODE", "PRODUCTSKU", "PROMOTIONACTIVITY"}, "(scan IS CONSUMERSCAN)-[rel]->(code IS CODE)-[]->(sku IS PRODUCTSKU), (scan IS CONSUMERSCAN)-[]->(activity IS PROMOTIONACTIVITY)", f"    {vertex('scan')} AS SOURCE_ID, scan.CONSUMER_SCAN_ID AS SOURCE_LABEL,\n    {vertex('code')} AS TARGET_ID, code.CODE_VALUE AS TARGET_LABEL,\n    '消费者扫码五码' AS RELATION_NAME, scan.SCAN_TIME, scan.PROVINCE, scan.CITY, scan.PRIZE_FLAG, scan.VERIFY_STATUS, sku.SKU_NAME, activity.ACTIVITY_NAME"),
        scenario("promotion-roi-root-cause", "场景6：费用 ROI 根因分析", "关联活动预算、规则奖励、扫码和兑换订单，分析费用投入与转化结果。", {"PROMOTIONACTIVITY", "PROMOTIONRULE", "CONSUMERSCAN", "EXCHANGEORDER"}, "(activity IS PROMOTIONACTIVITY)-[rel]->(rule IS PROMOTIONRULE), (scan IS CONSUMERSCAN)-[]->(activity IS PROMOTIONACTIVITY), (scan IS CONSUMERSCAN)-[]->(exchange_order IS EXCHANGEORDER)", f"    {vertex('activity')} AS SOURCE_ID, activity.ACTIVITY_NAME AS SOURCE_LABEL,\n    {vertex('exchange_order')} AS TARGET_ID, exchange_order.ORDER_NO AS TARGET_LABEL,\n    '活动投入兑换转化' AS RELATION_NAME, activity.BUDGET_AMOUNT, rule.REWARD_AMOUNT, scan.SCAN_TIME, scan.PRIZE_FLAG, exchange_order.PAY_AMOUNT, exchange_order.ORDER_STATUS"),
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
    return ApiResponse(data={
        "graph_name": topology["graph_name"],
        "graphs": topology.get("graphs") or [],
        "recommendations": _build_graph_query_recommendations(topology),
    })


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
