import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import SysBusinessType, SysBusinessTypeSeed, generate_id


DEFAULT_BUSINESS_TYPES: List[Dict[str, Any]] = [
    {
        "type_code": "BUSINESS",
        "type_name": "业务主体域",
        "semantic_desc": "围绕客户、产品、供应商、组织、渠道和业务单据等主体，建立跨业务场景可复用的业务语义。",
        "semantic_patterns": [
            {"pattern_code": "master-data-linking", "pattern_name": "主数据关联", "description": "围绕稳定主实体建立归属、包含和层级关系。"},
            {"pattern_code": "business-event-flow", "pattern_name": "业务事件流转", "description": "围绕订单、交易、发运、入库等事件建立参与对象与流转关系。"},
            {"pattern_code": "lifecycle-state", "pattern_name": "生命周期状态", "description": "表达对象或单据的时间顺序、状态变化和阶段。"},
        ],
    },
    {
        "type_code": "OBJECT",
        "type_name": "制造对象域",
        "semantic_desc": "围绕产品、批次、设备、物料、工艺和测量数据，构建制造对象及其过程和质量语义。",
        "semantic_patterns": [
            {"pattern_code": "master-data-linking", "pattern_name": "主数据关联", "description": "围绕产品、批次、机种等稳定对象组织基础语义关系。"},
            {"pattern_code": "process-trace", "pattern_name": "过程追溯", "description": "围绕站位履历、设备、物料和时间构建追溯路径。"},
            {"pattern_code": "measurement-threshold-violation", "pattern_name": "测量阈值判定", "description": "围绕测量结果与规则目录构建规则判定和异常语义对象。"},
        ],
    },
    {
        "type_code": "SCENARIO",
        "type_name": "分析场景域",
        "semantic_desc": "围绕异常、案例、根因和处置动作构建面向分析与闭环改进的业务语义。",
        "semantic_patterns": [
            {"pattern_code": "measurement-threshold-violation", "pattern_name": "测量阈值判定", "description": "表达指标、规则、判定结果和异常事件。"},
            {"pattern_code": "case-rootcause-action", "pattern_name": "案例根因闭环", "description": "围绕历史案例、根因和改善动作构建经验复用链路。"},
        ],
    },
    {
        "type_code": "CUSTOM",
        "type_name": "自定义域",
        "semantic_desc": "由用户定义业务语义模式，适配营销、供应链等特定领域。",
        "semantic_patterns": [
            {"pattern_code": "master-data-linking", "pattern_name": "主数据关联", "description": "围绕稳定业务对象建立基础关系。"},
            {"pattern_code": "business-event-flow", "pattern_name": "业务事件流转", "description": "围绕业务事件建立对象之间的流转关系。"},
        ],
    },
    {
        "type_code": "SUPPLY_CHAIN",
        "type_name": "供应链业务语义",
        "semantic_desc": "围绕产品、供应商、仓库、渠道、订单、库存和物流，描述供应链中的主数据、单据、状态与货物流转。",
        "semantic_patterns": [
            {"pattern_code": "master-data-hierarchy", "pattern_name": "主数据与层级", "description": "围绕产品、供应商、仓库、组织、区域等稳定对象，建立包含、归属和上下级关系。"},
            {"pattern_code": "business-event-document", "pattern_name": "业务事件与单据", "description": "围绕采购单、销售单、入出库单、发运单等事件及其参与对象建立关系。"},
            {"pattern_code": "lifecycle-state", "pattern_name": "生命周期与状态", "description": "表达订单、库存或物流对象的创建、审批、发货、签收、退货等状态变化和时间顺序。"},
            {"pattern_code": "transaction-flow", "pattern_name": "交易与流转", "description": "表达货物在供应商、仓库、渠道、客户和地点之间的库存、调拨、发运与交付流转。"},
        ],
    },
    {
        "type_code": "MARKETING",
        "type_name": "营销业务语义",
        "semantic_desc": "围绕客户、商品、渠道、活动、订单、权益、触达和反馈，描述营销运营、转化与服务闭环。",
        "semantic_patterns": [
            {"pattern_code": "master-data-hierarchy", "pattern_name": "主数据与层级", "description": "围绕客户、商品、渠道、组织、区域和会员等稳定对象，建立归属、层级和服务范围关系。"},
            {"pattern_code": "business-event-document", "pattern_name": "业务事件与单据", "description": "围绕促销、触达、下单、支付、收款、权益发放等营销事件及其参与对象建立关系。"},
            {"pattern_code": "lifecycle-state", "pattern_name": "生命周期与状态", "description": "表达客户、订单、活动或权益的创建、审批、生效、核销、失效等状态变化和时间顺序。"},
            {"pattern_code": "metric-rule-evaluation", "pattern_name": "指标与规则", "description": "围绕指标、统计口径、目标、阈值和达成状态，建立通用评价关系，不预设缺陷或超规。"},
            {"pattern_code": "case-disposition", "pattern_name": "案例与处置", "description": "围绕投诉、工单、风险事件、处理动作和处理结果建立服务与处置闭环，不限定根因分析。"},
        ],
    },
    {
        "type_code": "MANUFACTURING_DEFECT",
        "type_name": "缺陷根因分析业务语义",
        "semantic_desc": "面向制造过程质量问题的归因与闭环：围绕产品/批次、质量特征与测量结果、规格或判定规则、缺陷事件、工序站位、设备工装、物料供应、历史案例、根因假设和改善动作，构建“规则判定—缺陷识别—过程追溯—根因分析—处置验证”的业务语义链路。SFR 等质量特征只是其中一种测量对象，不作为独立业务场景。",
        "semantic_patterns": [
            {"pattern_code": "process-trace", "pattern_name": "制造过程追溯", "description": "围绕产品、批次、工序站位、设备工装、物料和供应来源，构建可追溯的制造过程链路。"},
            {"pattern_code": "measurement-threshold-violation", "pattern_name": "质量规则与缺陷判定", "description": "围绕质量特征、测量结果、规格上下限和判定结果，识别缺陷或异常事件。"},
            {"pattern_code": "case-rootcause-action", "pattern_name": "根因分析与改善闭环", "description": "围绕历史案例、根因假设、验证证据、改善动作和效果评估，构建缺陷归因与闭环语义。"},
        ],
    },
]
def ensure_default_business_types(db: Session) -> None:
    base_seed_key = "business_type_defaults_v1"
    extension_seed_key = "business_type_defaults_v2"
    existing_codes = {row.type_code for row in db.query(SysBusinessType.type_code).all()}
    has_base_seed = db.query(SysBusinessTypeSeed).filter(SysBusinessTypeSeed.seed_key == base_seed_key).first()
    has_extension_seed = db.query(SysBusinessTypeSeed).filter(SysBusinessTypeSeed.seed_key == extension_seed_key).first()

    # 首次安装创建完整默认集；已有安装只追加 v2 新增的缺陷根因语义，
    # 不重新创建用户此前主动删除的旧默认业务语义。
    candidates = DEFAULT_BUSINESS_TYPES if not has_base_seed else [
        item for item in DEFAULT_BUSINESS_TYPES if item["type_code"] == "MANUFACTURING_DEFECT"
    ] if not has_extension_seed else []
    for item in candidates:
        if item["type_code"] not in existing_codes:
            db.add(SysBusinessType(
                type_id=generate_id("btype"),
                type_code=item["type_code"],
                type_name=item["type_name"],
                semantic_desc=item["semantic_desc"],
                semantic_patterns_json=json.dumps(item["semantic_patterns"], ensure_ascii=False),
                status="ACTIVE",
                created_by="system",
            ))
    if not has_base_seed:
        db.add(SysBusinessTypeSeed(seed_key=base_seed_key))
    if not has_extension_seed:
        db.add(SysBusinessTypeSeed(seed_key=extension_seed_key))
    if candidates or not has_base_seed or not has_extension_seed:
        db.commit()


def serialize_business_type(item: SysBusinessType) -> Dict[str, Any]:
    try:
        patterns = json.loads(item.semantic_patterns_json or "[]")
    except (TypeError, ValueError):
        patterns = []
    normalized_patterns = [
        {
            "pattern_code": str(pattern.get("pattern_code") or "").strip(),
            "pattern_name": str(pattern.get("pattern_name") or "").strip(),
            "description": pattern.get("description") or "",
        }
        for pattern in patterns
        if isinstance(pattern, dict) and (pattern.get("pattern_code") or "").strip()
    ]
    return {
        "type_id": item.type_id,
        "type_code": item.type_code,
        "type_name": item.type_name,
        "semantic_desc": item.semantic_desc,
        "semantic_patterns": normalized_patterns,
        "status": item.status or "ACTIVE",
        "created_by": item.created_by,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def get_business_type_by_code(db: Session, type_code: Optional[str]) -> Optional[SysBusinessType]:
    ensure_default_business_types(db)
    return db.query(SysBusinessType).filter(SysBusinessType.type_code == (type_code or "BUSINESS").strip().upper()).first()
