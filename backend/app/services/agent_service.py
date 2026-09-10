import json
import re
from collections import deque
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.models import (
    SysAgentSkill,
    SysBusinessActivity,
    SysBusinessRule,
    SysManagedAgentSkill,
    SysManagedAgentSkillTestSession,
    SysDataSource,
    SysDomain,
    SysLLMConfig,
    SysMetricDefinition,
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
    SysProcessDef,
    generate_id,
)
from app.services.agent_runtime import ManagedSkillTestOrchestrator, extract_execution_events, replay_execution_events
from app.services.llm_service import LLMService, normalize_model_name
from app.services.source_data_service import SourceDataService

MANAGED_SKILL_TEST_SAMPLE_LIMIT_MAX = 1000
MANAGED_SKILL_TEST_PLAN_MAX_STEPS = 10
MANAGED_SKILL_TEST_PLAN_MAX_TARGETS = 4
MANAGED_SKILL_TEST_PLAN_MAX_PATH_DEPTH = 4
MANAGED_SKILL_TEST_PLAN_MAX_PROPERTIES = 8
DEFAULT_SKILL_ANALYSIS_MODES = ["DEFECT_ANALYSIS", "ROOT_CAUSE", "IMPACT_INFERENCE"]
ALLOWED_SKILL_ANALYSIS_MODES = {
    "DEFECT_ANALYSIS",
    "ROOT_CAUSE",
    "IMPACT_INFERENCE",
    "TRACEBACK",
    "MIXED",
}
SKILL_ANALYSIS_MODE_OPTIONS = [
    {"value": "DEFECT_ANALYSIS", "label": "缺陷分析", "description": "围绕缺陷现象、异常分布和异常分层做分析。"},
    {"value": "ROOT_CAUSE", "label": "根因分析", "description": "沿设备、工艺、物料和批次关系寻找高疑似根因。"},
    {"value": "IMPACT_INFERENCE", "label": "影响推断", "description": "沿下游链路推断异常批次、工单、产品或客户影响范围。"},
    {"value": "TRACEBACK", "label": "追溯分析", "description": "根据码、批次或单据做上下游链路追溯。"},
    {"value": "MIXED", "label": "综合分析", "description": "允许 Agent 组合缺陷、根因和影响推断策略。"},
]
REFERENCE_LABEL_ALIAS_LEXICON = {
    "BOTTLECODE": ["瓶码", "瓶码记录", "瓶身码"],
    "PACKCODE": ["包码", "中包码"],
    "CASECODE": ["箱码", "外箱码"],
    "PALLETCODE": ["托码", "托盘码"],
    "STACKCODE": ["垛码"],
    "OUTBOUNDORDER": ["出库单", "出库记录", "出库订单"],
    "DISTRIBUTOR": ["经销商", "渠道商"],
    "FACTORY": ["工厂", "生产工厂", "厂区"],
    "QUALITYINSPECTION": ["质检", "质检记录", "检验", "检验记录"],
    "PRODUCTIONBATCH": ["批次", "生产批次"],
    "MATERIAL": ["物料", "原料"],
    "DEVICE": ["设备", "机台"],
    "PROCESS": ["工艺", "工序"],
}
REFERENCE_PROPERTY_ALIAS_LEXICON = {
    "NAME": ["名称", "名字"],
    "CODE": ["编码", "代码"],
    "NO": ["编号", "单号", "号码"],
    "ID": ["标识", "主键"],
    "TIME": ["时间", "时刻"],
    "DATE": ["日期", "时间"],
    "RESULT": ["结果", "结论"],
    "STATUS": ["状态"],
    "COUNT": ["数量", "总数"],
    "QTY": ["数量"],
    "QUANTITY": ["数量"],
    "RATE": ["比率", "比例", "占比"],
    "RATIO": ["比率", "比例", "占比"],
    "PERCENT": ["百分比", "占比", "比例"],
}
ANALYSIS_SCENARIO_TEMPLATES = [
    {
        "scenario_code": "QUALITY_DEFECT",
        "scenario_name": "质量缺陷分析",
        "description": "围绕缺陷现象、超规指标、批次与设备分布进行分析，并可继续下钻根因与影响。",
        "recommended_analysis_modes": ["DEFECT_ANALYSIS", "ROOT_CAUSE", "IMPACT_INFERENCE"],
        "default_time_window": "30D",
        "max_path_depth": 3,
        "enable_activity_recommendation": True,
        "entity_keywords": ["DEFECT", "缺陷", "INSPECT", "检验", "QUALITY", "质量", "METRIC", "指标", "BATCH", "批次"],
        "metric_keywords": ["DEFECT", "缺陷", "NG", "不良", "异常", "超规", "良率", "直通", "质量"],
        "rule_keywords": ["DEFECT", "缺陷", "异常", "超规", "判定", "质量", "ROOT", "根因"],
        "activity_types": ["MANUAL_REVIEW", "CREATE_TASK", "CALL_PROCESS", "NOTIFY"],
    },
    {
        "scenario_code": "QUALITY_ROOT_CAUSE",
        "scenario_name": "质量根因分析",
        "description": "聚焦异常样本、设备、工艺、物料与前序批次之间的因果追溯和根因筛选。",
        "recommended_analysis_modes": ["ROOT_CAUSE", "DEFECT_ANALYSIS"],
        "default_time_window": "90D",
        "max_path_depth": 4,
        "enable_activity_recommendation": True,
        "entity_keywords": ["CAUSE", "根因", "DEFECT", "缺陷", "DEVICE", "设备", "MATERIAL", "物料", "BATCH", "批次", "PROCESS", "工艺"],
        "metric_keywords": ["异常", "缺陷", "超规", "波动", "覆盖率", "命中", "富集"],
        "rule_keywords": ["根因", "超规", "异常", "设备", "物料", "批次", "因果"],
        "activity_types": ["MANUAL_REVIEW", "CREATE_TASK", "CALL_PROCESS"],
    },
    {
        "scenario_code": "SUPPLY_TRACE",
        "scenario_name": "供应链追溯分析",
        "description": "围绕码、箱、托、批次、出入库、经销商等对象进行上下游链路追溯。",
        "recommended_analysis_modes": ["TRACEBACK"],
        "default_time_window": "30D",
        "max_path_depth": 5,
        "enable_activity_recommendation": False,
        "entity_keywords": ["BOTTLE", "瓶码", "PACK", "包码", "CASE", "箱码", "PALLET", "托码", "STACK", "垛码", "TRACE", "追溯", "OUTBOUND", "出库", "INBOUND", "入库", "DISTRIBUTOR", "经销商"],
        "metric_keywords": ["出库", "入库", "运输", "扫码", "数量", "库存", "经销"],
        "rule_keywords": ["追溯", "链路", "码", "批次", "出库", "入库"],
        "activity_types": ["MANUAL_REVIEW", "NOTIFY"],
    },
    {
        "scenario_code": "SUPPLY_IMPACT",
        "scenario_name": "供应链影响推断",
        "description": "针对异常码、批次、订单或库存事件，分析下游波及范围和潜在召回影响。",
        "recommended_analysis_modes": ["TRACEBACK", "IMPACT_INFERENCE"],
        "default_time_window": "90D",
        "max_path_depth": 5,
        "enable_activity_recommendation": True,
        "entity_keywords": ["BATCH", "批次", "OUTBOUND", "出库", "INVENTORY", "库存", "ORDER", "订单", "CUSTOMER", "客户", "DISTRIBUTOR", "经销商", "PRODUCT", "产品"],
        "metric_keywords": ["影响", "覆盖", "出库", "库存", "召回", "客户", "数量", "范围"],
        "rule_keywords": ["影响", "召回", "冻结", "预警", "库存", "出库"],
        "activity_types": ["NOTIFY", "CREATE_TASK", "CALL_PROCESS", "MANUAL_REVIEW"],
    },
    {
        "scenario_code": "GENERAL_GRAPH",
        "scenario_name": "通用图探索分析",
        "description": "适用于未明确定义场景的图对象探索、关系浏览和保守分析。",
        "recommended_analysis_modes": ["MIXED"],
        "default_time_window": "7D",
        "max_path_depth": 2,
        "enable_activity_recommendation": False,
        "entity_keywords": [],
        "metric_keywords": [],
        "rule_keywords": [],
        "activity_types": ["MANUAL_REVIEW"],
    },
]


class AgentService:
    def __init__(self, db: Session):
        self.db = db
        self.source_service = SourceDataService(db)
        self.llm_service = LLMService(db)
        self.managed_skill_test_orchestrator = ManagedSkillTestOrchestrator(self)

    def get_analysis_semantics(self, domain_id: str) -> Dict[str, Any]:
        entities = self.db.query(SysOntologyEntity).filter(
            SysOntologyEntity.domain_id == domain_id,
        ).order_by(SysOntologyEntity.entity_display_name, SysOntologyEntity.entity_name).all()
        relations = self.db.query(SysOntologyRelation).filter(
            SysOntologyRelation.domain_id == domain_id,
        ).order_by(SysOntologyRelation.relation_name).all()
        processes = self.db.query(SysProcessDef).filter(
            SysProcessDef.domain_id == domain_id,
        ).order_by(SysProcessDef.process_name).all()
        metrics = self.db.query(SysMetricDefinition).filter(
            SysMetricDefinition.domain_id == domain_id,
        ).order_by(SysMetricDefinition.updated_at.desc(), SysMetricDefinition.metric_name).all()
        rules = self.db.query(SysBusinessRule).filter(
            SysBusinessRule.domain_id == domain_id,
        ).order_by(SysBusinessRule.priority.desc(), SysBusinessRule.updated_at.desc()).all()
        activities = self.db.query(SysBusinessActivity).filter(
            SysBusinessActivity.domain_id == domain_id,
        ).order_by(SysBusinessActivity.updated_at.desc(), SysBusinessActivity.activity_name).all()
        entity_index = {item.entity_id: item for item in entities}
        relation_index = {item.relation_id: item for item in relations}
        process_index = {item.process_id: item for item in processes}
        activity_index = {item.activity_id: item for item in activities}
        serialized_metrics = [
            self._serialize_metric_definition(item, entity_index)
            for item in metrics
        ]
        serialized_rules = [
            self._serialize_rule_definition(item, entity_index, relation_index, activity_index)
            for item in rules
        ]
        serialized_activities = [
            self._serialize_activity_definition(item, process_index)
            for item in activities
        ]
        return {
            "scenario_templates": self._build_analysis_scenario_templates(
                entities=[{
                    "entity_id": item.entity_id,
                    "entity_name": item.entity_name,
                    "entity_display_name": item.entity_display_name,
                    "object_type": item.object_type,
                    "build_type": item.build_type,
                    "status": item.status,
                } for item in entities],
                metrics=serialized_metrics,
                rules=serialized_rules,
                activities=serialized_activities,
            ),
            "analysis_mode_options": SKILL_ANALYSIS_MODE_OPTIONS,
            "default_analysis_modes": list(DEFAULT_SKILL_ANALYSIS_MODES),
            "default_time_window": "7D",
            "default_max_path_depth": 2,
            "entities": [
                {
                    "entity_id": item.entity_id,
                    "entity_name": item.entity_name,
                    "entity_display_name": item.entity_display_name,
                    "object_type": item.object_type,
                    "build_type": item.build_type,
                    "status": item.status,
                }
                for item in entities
            ],
            "relations": [
                {
                    "relation_id": item.relation_id,
                    "relation_name": item.relation_name,
                    "relation_type": item.relation_type,
                    "source_entity_id": item.source_entity_id,
                    "target_entity_id": item.target_entity_id,
                }
                for item in relations
            ],
            "processes": [
                {
                    "process_id": item.process_id,
                    "process_name": item.process_name,
                    "process_desc": item.process_desc,
                }
                for item in processes
            ],
            "metrics": serialized_metrics,
            "rules": serialized_rules,
            "activities": serialized_activities,
        }

    @staticmethod
    def _contains_any_keyword(texts: List[str], keywords: List[str]) -> bool:
        normalized = " ".join(item.upper() for item in texts if item).strip()
        return any(keyword.upper() in normalized for keyword in keywords if keyword)

    def _build_analysis_scenario_templates(
        self,
        *,
        entities: List[Dict[str, Any]],
        metrics: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        activities: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        templates = []
        for template in ANALYSIS_SCENARIO_TEMPLATES:
            entity_ids = [
                item.get("entity_id")
                for item in entities
                if self._contains_any_keyword(
                    [str(item.get("entity_name") or ""), str(item.get("entity_display_name") or "")],
                    template.get("entity_keywords") or [],
                )
            ]
            metric_ids = [
                item.get("metric_id")
                for item in metrics
                if self._contains_any_keyword(
                    [
                        str(item.get("metric_name") or ""),
                        str(item.get("metric_code") or ""),
                        str(item.get("metric_desc") or ""),
                        str(item.get("metric_category") or ""),
                    ],
                    template.get("metric_keywords") or [],
                )
            ]
            rule_ids = [
                item.get("rule_id")
                for item in rules
                if self._contains_any_keyword(
                    [
                        str(item.get("rule_name") or ""),
                        str(item.get("rule_desc") or ""),
                        str(item.get("rule_category") or ""),
                        str(item.get("scope_entity_name") or ""),
                        str(item.get("scope_entity_display_name") or ""),
                        str(item.get("scope_relation_name") or ""),
                    ],
                    template.get("rule_keywords") or [],
                )
            ]
            activity_ids = [
                item.get("activity_id")
                for item in activities
                if (item.get("activity_type") or "") in set(template.get("activity_types") or [])
            ]
            templates.append({
                "scenario_code": template["scenario_code"],
                "scenario_name": template["scenario_name"],
                "description": template["description"],
                "recommended_analysis_modes": template["recommended_analysis_modes"],
                "default_time_window": template["default_time_window"],
                "max_path_depth": template["max_path_depth"],
                "enable_activity_recommendation": template["enable_activity_recommendation"],
                "recommended_entry_entity_ids": self._normalize_string_list(entity_ids)[:8],
                "recommended_metric_ids": self._normalize_string_list(metric_ids)[:20],
                "recommended_rule_ids": self._normalize_string_list(rule_ids)[:20],
                "recommended_activity_ids": self._normalize_string_list(activity_ids)[:20],
            })
        return templates

    def _load_domain_ontology_definition(self, domain_id: str) -> Dict[str, Any]:
        entities = self.db.query(SysOntologyEntity).options(
            selectinload(SysOntologyEntity.properties),
        ).filter(
            SysOntologyEntity.domain_id == domain_id,
        ).order_by(
            SysOntologyEntity.entity_display_name,
            SysOntologyEntity.entity_name,
        ).all()
        relations = self.db.query(SysOntologyRelation).filter(
            SysOntologyRelation.domain_id == domain_id,
        ).order_by(
            SysOntologyRelation.relation_name,
        ).all()
        entity_index = {entity.entity_id: entity for entity in entities}
        serialized_entities = []
        for entity in entities:
            serialized_entities.append({
                "entity_id": entity.entity_id,
                "entity_name": entity.entity_name,
                "entity_display_name": entity.entity_display_name,
                "entity_desc": entity.entity_desc,
                "object_type": entity.object_type,
                "build_type": entity.build_type,
                "table_name": entity.table_name,
                "status": entity.status,
                "governance_status": entity.governance_status,
                "data_owner": entity.data_owner,
                "security_level": entity.security_level,
                "update_frequency": entity.update_frequency,
                "properties": [
                    {
                        "property_id": prop.property_id,
                        "property_name": prop.property_name,
                        "property_display_name": prop.property_display_name,
                        "property_desc": prop.property_desc,
                        "data_type": prop.data_type,
                        "is_primary_key": prop.is_primary_key,
                        "is_nullable": prop.is_nullable,
                        "unit": prop.unit,
                        "value_constraint": prop.value_constraint,
                        "usage_codes": self._safe_json_loads(getattr(prop, "usage_codes_json", None), []),
                        "is_required_filter": prop.is_required_filter,
                        "ref_property_id": prop.ref_property_id,
                    }
                    for prop in sorted(entity.properties or [], key=lambda item: (item.order_num or 0, item.property_name or ""))
                ],
            })
        serialized_relations = []
        for relation in relations:
            source_entity = entity_index.get(relation.source_entity_id)
            target_entity = entity_index.get(relation.target_entity_id)
            serialized_relations.append({
                "relation_id": relation.relation_id,
                "relation_name": relation.relation_name,
                "relation_type": relation.relation_type,
                "relation_desc": relation.relation_desc,
                "source_entity_id": relation.source_entity_id,
                "source_entity_name": source_entity.entity_name if source_entity else "",
                "target_entity_id": relation.target_entity_id,
                "target_entity_name": target_entity.entity_name if target_entity else "",
                "relation_table_name": relation.relation_table_name,
            })
        return {
            "domain_id": domain_id,
            "entity_count": len(serialized_entities),
            "relation_count": len(serialized_relations),
            "entities": serialized_entities,
            "relations": serialized_relations,
        }

    def _build_ontology_graph_binding(
        self,
        ontology_model: Dict[str, Any],
        topology: Dict[str, Any],
    ) -> Dict[str, Any]:
        nodes_by_label = {
            str(node.get("displayName") or node.get("name") or "").upper(): node
            for node in (topology.get("nodes") or [])
            if str(node.get("displayName") or node.get("name") or "").strip()
        }
        entity_bindings = []
        property_bindings = []
        entity_labels_by_id: Dict[str, List[str]] = {}
        for entity in ontology_model.get("entities") or []:
            graph_labels = self._graph_labels_for_entity(topology, entity.get("entity_name") or "")
            entity_labels_by_id[entity.get("entity_id") or ""] = graph_labels
            entity_bindings.append({
                "entity_id": entity.get("entity_id"),
                "entity_name": entity.get("entity_name"),
                "entity_display_name": entity.get("entity_display_name"),
                "graph_labels": graph_labels,
                "matched": bool(graph_labels),
                "reason": "按实体名与 Property Graph 节点标签匹配。" if graph_labels else "当前未在已部署 Property Graph 中匹配到同名或近似标签。",
            })
            node_properties = {}
            for label in graph_labels:
                node = nodes_by_label.get(label.upper()) or {}
                node_properties[label] = {
                    str(prop.get("property_name") or "").upper(): prop
                    for prop in (node.get("properties") or [])
                    if str(prop.get("property_name") or "").strip()
                }
            for prop in entity.get("properties") or []:
                matched_columns = []
                normalized_property_name = self._normalize_lookup_token(prop.get("property_name") or "")
                for label, property_index in node_properties.items():
                    for property_name, property_meta in property_index.items():
                        normalized_graph_property = self._normalize_lookup_token(property_name)
                        if (
                            normalized_property_name
                            and (normalized_graph_property == normalized_property_name
                                 or normalized_property_name in normalized_graph_property
                                 or normalized_graph_property in normalized_property_name)
                        ):
                            matched_columns.append({
                                "graph_label": label,
                                "graph_property_name": property_name,
                                "graph_data_type": property_meta.get("data_type"),
                                "is_primary_key": property_meta.get("is_primary_key"),
                            })
                property_bindings.append({
                    "entity_id": entity.get("entity_id"),
                    "entity_name": entity.get("entity_name"),
                    "property_id": prop.get("property_id"),
                    "property_name": prop.get("property_name"),
                    "property_display_name": prop.get("property_display_name"),
                    "matched_columns": matched_columns,
                    "matched": bool(matched_columns),
                })

        relation_bindings = []
        for relation in ontology_model.get("relations") or []:
            source_labels = entity_labels_by_id.get(relation.get("source_entity_id") or "", [])
            target_labels = entity_labels_by_id.get(relation.get("target_entity_id") or "", [])
            matched_edges = []
            for edge in topology.get("edges") or []:
                edge_source = str(edge.get("source") or "").upper()
                edge_target = str(edge.get("target") or "").upper()
                if edge_source in {label.upper() for label in source_labels} and edge_target in {label.upper() for label in target_labels}:
                    matched_edges.append({
                        "graph_edge_id": edge.get("id"),
                        "graph_edge_name": edge.get("name"),
                        "graph_source_label": edge.get("source"),
                        "graph_target_label": edge.get("target"),
                        "graph_table_name": edge.get("relationTableName") or edge.get("tableName"),
                    })
            relation_bindings.append({
                "relation_id": relation.get("relation_id"),
                "relation_name": relation.get("relation_name"),
                "source_entity_name": relation.get("source_entity_name"),
                "target_entity_name": relation.get("target_entity_name"),
                "source_graph_labels": source_labels,
                "target_graph_labels": target_labels,
                "matched_edges": matched_edges,
                "matched": bool(matched_edges),
            })
        return {
            "entity_bindings": entity_bindings,
            "property_bindings": property_bindings,
            "relation_bindings": relation_bindings,
        }

    @staticmethod
    def _normalize_string_list(values: Any) -> List[str]:
        result: List[str] = []
        for value in values or []:
            token = str(value or "").strip()
            if token and token not in result:
                result.append(token)
        return result

    def _normalize_analysis_modes(self, values: Any) -> List[str]:
        normalized = []
        for value in values or []:
            token = str(value or "").strip().upper()
            if token in ALLOWED_SKILL_ANALYSIS_MODES and token not in normalized:
                normalized.append(token)
        return normalized or list(DEFAULT_SKILL_ANALYSIS_MODES)

    @staticmethod
    def _normalize_time_window(value: Any) -> str:
        token = str(value or "").strip().upper()
        if re.fullmatch(r"\d+[DWMQY]", token):
            return token
        return "7D"

    @staticmethod
    def _normalize_max_path_depth(value: Any) -> int:
        try:
            return max(1, min(int(value or 2), 5))
        except (TypeError, ValueError):
            return 2

    def _serialize_metric_definition(
        self,
        metric: SysMetricDefinition,
        entity_index: Dict[str, SysOntologyEntity],
    ) -> Dict[str, Any]:
        entity = entity_index.get(metric.entity_id)
        return {
            "metric_id": metric.metric_id,
            "domain_id": metric.domain_id,
            "entity_id": metric.entity_id,
            "entity_name": entity.entity_name if entity else "",
            "entity_display_name": entity.entity_display_name if entity else "",
            "metric_code": metric.metric_code,
            "metric_name": metric.metric_name,
            "metric_category": metric.metric_category,
            "metric_desc": metric.metric_desc,
            "calculation_expr": metric.calculation_expr,
            "aggregation_method": metric.aggregation_method,
            "calculation_period": metric.calculation_period,
            "unit": metric.unit,
            "threshold_config": metric.threshold_config,
            "threshold_config_json": self._safe_json_loads(metric.threshold_config, {}),
            "status": metric.status,
        }

    def _serialize_rule_definition(
        self,
        rule: SysBusinessRule,
        entity_index: Dict[str, SysOntologyEntity],
        relation_index: Dict[str, SysOntologyRelation],
        activity_index: Dict[str, SysBusinessActivity],
    ) -> Dict[str, Any]:
        entity = entity_index.get(rule.scope_entity_id or "")
        relation = relation_index.get(rule.scope_relation_id or "")
        activity = activity_index.get(rule.activity_id or "")
        return {
            "rule_id": rule.rule_id,
            "domain_id": rule.domain_id,
            "rule_name": rule.rule_name,
            "rule_category": rule.rule_category,
            "rule_desc": rule.rule_desc,
            "trigger_event": rule.trigger_event,
            "scope_entity_id": rule.scope_entity_id,
            "scope_entity_name": entity.entity_name if entity else "",
            "scope_entity_display_name": entity.entity_display_name if entity else "",
            "scope_relation_id": rule.scope_relation_id,
            "scope_relation_name": relation.relation_name if relation else "",
            "condition_json": rule.condition_json,
            "condition_config": self._safe_json_loads(rule.condition_json, {}),
            "activity_id": rule.activity_id,
            "activity_name": activity.activity_name if activity else "",
            "priority": rule.priority,
            "status": rule.status,
        }

    def _serialize_activity_definition(
        self,
        activity: SysBusinessActivity,
        process_index: Dict[str, SysProcessDef],
    ) -> Dict[str, Any]:
        process = process_index.get(activity.process_id or "")
        return {
            "activity_id": activity.activity_id,
            "domain_id": activity.domain_id,
            "activity_name": activity.activity_name,
            "activity_type": activity.activity_type,
            "activity_desc": activity.activity_desc,
            "process_id": activity.process_id,
            "process_name": process.process_name if process else "",
            "config_json": activity.config_json,
            "config": self._safe_json_loads(activity.config_json, {}),
            "status": activity.status,
        }

    @staticmethod
    def _normalize_lookup_token(value: Any) -> str:
        return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

    def _graph_labels_for_entity(self, topology: Dict[str, Any], entity_name: str) -> List[str]:
        normalized_entity = self._normalize_lookup_token(entity_name)
        labels = []
        for node in topology.get("nodes") or []:
            label = str(node.get("displayName") or node.get("name") or "").strip().upper()
            normalized_label = self._normalize_lookup_token(label)
            if not label:
                continue
            if normalized_label == normalized_entity or normalized_entity in normalized_label or normalized_label in normalized_entity:
                labels.append(label)
        return list(dict.fromkeys(labels))

    def _load_analysis_semantics_for_skill(
        self,
        *,
        domain_id: str,
        payload: Dict[str, Any],
        topology: Dict[str, Any],
    ) -> Dict[str, Any]:
        catalog = self.get_analysis_semantics(domain_id)
        scenario_code = str(payload.get("analysis_scenario_code") or "").strip().upper()
        scenario_template = next(
            (item for item in (catalog.get("scenario_templates") or []) if str(item.get("scenario_code") or "").upper() == scenario_code),
            None,
        )
        entities = catalog.get("entities") or []
        relations = catalog.get("relations") or []
        entity_index = {item["entity_id"]: item for item in entities}
        relation_index = {item["relation_id"]: item for item in relations}

        selected_metric_ids = set(self._normalize_string_list(payload.get("selected_metric_ids")))
        selected_rule_ids = set(self._normalize_string_list(payload.get("selected_rule_ids")))
        selected_activity_ids = set(self._normalize_string_list(payload.get("selected_activity_ids")))
        if scenario_template and not selected_metric_ids:
            selected_metric_ids = set(self._normalize_string_list(scenario_template.get("recommended_metric_ids")))
        if scenario_template and not selected_rule_ids:
            selected_rule_ids = set(self._normalize_string_list(scenario_template.get("recommended_rule_ids")))
        if scenario_template and not selected_activity_ids:
            selected_activity_ids = set(self._normalize_string_list(scenario_template.get("recommended_activity_ids")))

        metrics = [item for item in (catalog.get("metrics") or []) if item.get("status") == "ACTIVE"]
        rules = [item for item in (catalog.get("rules") or []) if item.get("status") == "ACTIVE"]
        activities = [item for item in (catalog.get("activities") or []) if item.get("status") == "ACTIVE"]
        if selected_metric_ids:
            metrics = [item for item in (catalog.get("metrics") or []) if item.get("metric_id") in selected_metric_ids]
        if selected_rule_ids:
            rules = [item for item in (catalog.get("rules") or []) if item.get("rule_id") in selected_rule_ids]
        if selected_activity_ids:
            activities = [item for item in (catalog.get("activities") or []) if item.get("activity_id") in selected_activity_ids]

        entry_entity_ids = self._normalize_string_list(payload.get("entry_entity_ids"))
        if scenario_template and not entry_entity_ids:
            entry_entity_ids = self._normalize_string_list(scenario_template.get("recommended_entry_entity_ids"))
        if not entry_entity_ids:
            derived_entry_ids = [item.get("entity_id") for item in metrics if item.get("entity_id")]
            derived_entry_ids.extend(item.get("scope_entity_id") for item in rules if item.get("scope_entity_id"))
            entry_entity_ids = self._normalize_string_list(derived_entry_ids)[:8]

        analysis_profile = {
            "analysis_scenario_code": scenario_code or (scenario_template or {}).get("scenario_code") or "GENERAL_GRAPH",
            "analysis_scenario_name": (scenario_template or {}).get("scenario_name") or "通用图探索分析",
            "analysis_modes": self._normalize_analysis_modes(
                payload.get("analysis_modes") or (scenario_template or {}).get("recommended_analysis_modes")
            ),
            "entry_entity_ids": entry_entity_ids,
            "entry_entities": [
                {
                    "entity_id": entity_id,
                    "entity_name": (entity_index.get(entity_id) or {}).get("entity_name") or "",
                    "entity_display_name": (entity_index.get(entity_id) or {}).get("entity_display_name") or "",
                    "graph_labels": self._graph_labels_for_entity(topology, (entity_index.get(entity_id) or {}).get("entity_name") or ""),
                }
                for entity_id in entry_entity_ids
                if entity_id in entity_index
            ],
            "default_time_window": self._normalize_time_window(
                payload.get("default_time_window") or (scenario_template or {}).get("default_time_window")
            ),
            "max_path_depth": self._normalize_max_path_depth(
                payload.get("max_path_depth") or (scenario_template or {}).get("max_path_depth")
            ),
            "enable_activity_recommendation": bool(
                payload.get("enable_activity_recommendation")
                if "enable_activity_recommendation" in payload
                else (scenario_template or {}).get("enable_activity_recommendation", True)
            ),
        }

        graph_semantic_map = self._build_graph_semantic_map(
            topology=topology,
            metrics=metrics,
            rules=rules,
            activities=activities,
            entity_index=entity_index,
            relation_index=relation_index,
        )
        return {
            "analysis_profile": analysis_profile,
            "metrics": metrics,
            "rules": rules,
            "activities": activities,
            "graph_semantic_map": graph_semantic_map,
        }

    def _build_graph_semantic_map(
        self,
        *,
        topology: Dict[str, Any],
        metrics: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        activities: List[Dict[str, Any]],
        entity_index: Dict[str, Dict[str, Any]],
        relation_index: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        metric_bindings = []
        for metric in metrics:
            entity_name = (entity_index.get(metric.get("entity_id")) or {}).get("entity_name") or metric.get("entity_name") or ""
            graph_labels = self._graph_labels_for_entity(topology, entity_name)
            metric_bindings.append({
                "metric_id": metric.get("metric_id"),
                "metric_name": metric.get("metric_name"),
                "entity_id": metric.get("entity_id"),
                "entity_name": entity_name,
                "graph_labels": graph_labels,
                "aggregation_method": metric.get("aggregation_method"),
                "calculation_period": metric.get("calculation_period"),
                "reason": "按指标关联本体对象匹配 Property Graph 节点标签。",
            })

        rule_bindings = []
        for rule in rules:
            scoped_entity = entity_index.get(rule.get("scope_entity_id")) or {}
            scoped_relation = relation_index.get(rule.get("scope_relation_id")) or {}
            entity_labels = self._graph_labels_for_entity(topology, scoped_entity.get("entity_name") or "")
            source_labels = self._graph_labels_for_entity(
                topology, (entity_index.get(scoped_relation.get("source_entity_id")) or {}).get("entity_name") or ""
            ) if scoped_relation else []
            target_labels = self._graph_labels_for_entity(
                topology, (entity_index.get(scoped_relation.get("target_entity_id")) or {}).get("entity_name") or ""
            ) if scoped_relation else []
            rule_bindings.append({
                "rule_id": rule.get("rule_id"),
                "rule_name": rule.get("rule_name"),
                "rule_category": rule.get("rule_category"),
                "scope_entity_id": rule.get("scope_entity_id"),
                "scope_entity_labels": entity_labels,
                "scope_relation_id": rule.get("scope_relation_id"),
                "scope_relation_name": scoped_relation.get("relation_name") or "",
                "source_labels": source_labels,
                "target_labels": target_labels,
                "activity_id": rule.get("activity_id"),
                "reason": "按规则作用域实体/关系映射图查询约束与结果解释范围。",
            })

        activity_rule_map: Dict[str, List[str]] = {}
        for rule in rules:
            activity_id = rule.get("activity_id")
            if activity_id:
                activity_rule_map.setdefault(activity_id, []).append(rule.get("rule_name") or rule.get("rule_id") or "")

        activity_bindings = []
        for activity in activities:
            activity_bindings.append({
                "activity_id": activity.get("activity_id"),
                "activity_name": activity.get("activity_name"),
                "activity_type": activity.get("activity_type"),
                "process_id": activity.get("process_id"),
                "process_name": activity.get("process_name"),
                "triggered_by_rules": activity_rule_map.get(activity.get("activity_id"), []),
                "reason": "分析结论命中规则后，按活动类型给出处置建议而非直接执行写操作。",
            })

        return {
            "metric_bindings": metric_bindings,
            "rule_bindings": rule_bindings,
            "activity_bindings": activity_bindings,
        }

    def list_skills(self, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = (
            self.db.query(
                SysAgentSkill,
                SysDomain.domain_name,
                SysLLMConfig.config_name,
                SysLLMConfig.model_name,
                SysProcessDef.process_name,
                SysDataSource.source_name,
            )
            .join(SysDomain, SysDomain.domain_id == SysAgentSkill.domain_id)
            .outerjoin(SysLLMConfig, SysLLMConfig.config_id == SysAgentSkill.llm_config_id)
            .outerjoin(SysProcessDef, SysProcessDef.process_id == SysAgentSkill.process_id)
            .outerjoin(SysDataSource, SysDataSource.source_id == SysAgentSkill.source_id)
        )
        if domain_id:
            query = query.filter(SysAgentSkill.domain_id == domain_id)

        rows = query.order_by(SysAgentSkill.updated_at.desc(), SysAgentSkill.created_at.desc()).all()
        return [
            self._serialize_skill(skill, domain_name, llm_config_name, llm_model_name, process_name, source_name)
            for skill, domain_name, llm_config_name, llm_model_name, process_name, source_name in rows
        ]

    def get_skill(self, skill_id: str) -> Dict[str, Any]:
        row = (
            self.db.query(
                SysAgentSkill,
                SysDomain.domain_name,
                SysLLMConfig.config_name,
                SysLLMConfig.model_name,
                SysProcessDef.process_name,
                SysDataSource.source_name,
            )
            .join(SysDomain, SysDomain.domain_id == SysAgentSkill.domain_id)
            .outerjoin(SysLLMConfig, SysLLMConfig.config_id == SysAgentSkill.llm_config_id)
            .outerjoin(SysProcessDef, SysProcessDef.process_id == SysAgentSkill.process_id)
            .outerjoin(SysDataSource, SysDataSource.source_id == SysAgentSkill.source_id)
            .filter(SysAgentSkill.skill_id == skill_id)
            .first()
        )
        if not row:
            raise ValueError("技能不存在")
        skill, domain_name, llm_config_name, llm_model_name, process_name, source_name = row
        return self._serialize_skill(skill, domain_name, llm_config_name, llm_model_name, process_name, source_name)

    async def create_skill(self, domain_id: str, payload: Dict[str, Any], current_user: Dict[str, Any]) -> Dict[str, Any]:
        domain, process, entity, properties, relations = self._load_skill_dependencies(
            domain_id=domain_id,
            process_id=payload["process_id"],
            source_id=payload["source_id"],
            property_graph_name=payload["property_graph_name"],
        )
        llm_config = self._get_llm_config(payload.get("llm_config_id"))
        topology = self.source_service.get_remote_property_graph_topology(
            entity.source_id,
            entity.entity_name,
            schema=getattr(entity, "schema", None),
        )
        ontology_model = self._load_domain_ontology_definition(domain_id)
        ontology_graph_binding = self._build_ontology_graph_binding(ontology_model, topology)
        analysis_semantics = self._load_analysis_semantics_for_skill(
            domain_id=domain_id,
            payload=payload,
            topology=topology,
        )
        skill = SysAgentSkill(
            skill_id=generate_id("skill"),
            domain_id=domain_id,
            llm_config_id=llm_config.config_id,
            process_id=process.process_id,
            entity_id=entity.entity_id,
            source_id=entity.source_id,
            property_graph_name=entity.entity_name,
            skill_name=payload["skill_name"].strip(),
            skill_desc=(payload.get("skill_desc") or "").strip(),
            analysis_goal=(payload.get("analysis_goal") or "").strip(),
            execution_rules=(payload.get("execution_rules") or "").strip(),
            output_requirements=(payload.get("output_requirements") or "").strip(),
            status=payload.get("status") or "ACTIVE",
            created_by=current_user.get("username", "unknown"),
        )
        generated = await self._generate_skill_blueprint(
            domain=domain,
            process=process,
            entity=entity,
            properties=properties,
            relations=relations,
            skill=skill,
            llm_config=llm_config,
            topology=topology,
            analysis_semantics=analysis_semantics,
            ontology_model=ontology_model,
            ontology_graph_binding=ontology_graph_binding,
        )
        skill.skill_desc = skill.skill_desc or generated["skill_desc"]
        skill.analysis_goal = skill.analysis_goal or generated["analysis_goal"]
        skill.execution_rules = skill.execution_rules or generated["execution_rules"]
        skill.output_requirements = skill.output_requirements or generated["output_requirements"]
        skill.prompt_template = generated["prompt_template"]
        skill.context_json = json.dumps(
            self._build_skill_context(domain, process, entity, properties, relations, topology, analysis_semantics, ontology_model, ontology_graph_binding),
            ensure_ascii=False,
        )
        self.db.add(skill)
        self.db.commit()
        self.db.refresh(skill)
        return self.get_skill(skill.skill_id)

    async def update_skill(self, skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        skill = self.db.query(SysAgentSkill).filter(SysAgentSkill.skill_id == skill_id).first()
        if not skill:
            raise ValueError("技能不存在")

        process_id = payload.get("process_id") or skill.process_id
        source_id = payload.get("source_id") or skill.source_id
        property_graph_name = payload.get("property_graph_name") or skill.property_graph_name
        llm_config_id = payload.get("llm_config_id") or skill.llm_config_id
        domain, process, entity, properties, relations = self._load_skill_dependencies(
            domain_id=skill.domain_id,
            process_id=process_id,
            source_id=source_id,
            property_graph_name=property_graph_name,
        )
        llm_config = self._get_llm_config(llm_config_id)
        topology = self.source_service.get_remote_property_graph_topology(
            entity.source_id,
            entity.entity_name,
            schema=getattr(entity, "schema", None),
        )
        ontology_model = self._load_domain_ontology_definition(skill.domain_id)
        ontology_graph_binding = self._build_ontology_graph_binding(ontology_model, topology)
        merged_payload = {
            **self._skill_semantic_defaults_from_context(self._safe_json_loads(skill.context_json, {})),
            **payload,
        }
        analysis_semantics = self._load_analysis_semantics_for_skill(
            domain_id=skill.domain_id,
            payload=merged_payload,
            topology=topology,
        )

        for field in ["skill_name", "skill_desc", "analysis_goal", "execution_rules", "output_requirements", "status"]:
            if field in payload and payload[field] is not None:
                setattr(skill, field, payload[field].strip() if isinstance(payload[field], str) else payload[field])
        skill.llm_config_id = llm_config.config_id
        skill.process_id = process.process_id
        skill.entity_id = entity.entity_id
        skill.source_id = entity.source_id
        skill.property_graph_name = entity.entity_name
        generated = await self._generate_skill_blueprint(
            domain=domain,
            process=process,
            entity=entity,
            properties=properties,
            relations=relations,
            skill=skill,
            llm_config=llm_config,
            topology=topology,
            analysis_semantics=analysis_semantics,
            ontology_model=ontology_model,
            ontology_graph_binding=ontology_graph_binding,
        )
        skill.skill_desc = skill.skill_desc or generated["skill_desc"]
        skill.analysis_goal = skill.analysis_goal or generated["analysis_goal"]
        skill.execution_rules = skill.execution_rules or generated["execution_rules"]
        skill.output_requirements = skill.output_requirements or generated["output_requirements"]
        skill.prompt_template = generated["prompt_template"]
        skill.context_json = json.dumps(
            self._build_skill_context(domain, process, entity, properties, relations, topology, analysis_semantics, ontology_model, ontology_graph_binding),
            ensure_ascii=False,
        )
        skill.updated_at = datetime.utcnow()
        self.db.commit()
        return self.get_skill(skill.skill_id)

    def delete_skill(self, skill_id: str):
        skill = self.db.query(SysAgentSkill).filter(SysAgentSkill.skill_id == skill_id).first()
        if not skill:
            raise ValueError("技能不存在")
        self.db.delete(skill)
        self.db.commit()

    def list_managed_skills(self, domain_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(SysManagedAgentSkill).filter(
            SysManagedAgentSkill.domain_id == domain_id,
        ).order_by(
            SysManagedAgentSkill.updated_at.desc(),
            SysManagedAgentSkill.created_at.desc(),
        ).all()
        return [self._serialize_managed_skill(item) for item in rows]

    def upload_managed_skill(self, domain_id: str, filename: str, content: bytes, uploaded_by: str) -> Dict[str, Any]:
        if not self.db.query(SysDomain).filter(SysDomain.domain_id == domain_id, SysDomain.status == "ACTIVE").first():
            raise ValueError("当前业务分析域不存在或未启用")
        if not filename.lower().endswith(".zip"):
            raise ValueError("仅支持上传 Agent Skill ZIP 文件")
        if not content or len(content) > 10 * 1024 * 1024:
            raise ValueError("Skill ZIP 不能为空且不能超过 10MB")
        try:
            with ZipFile(BytesIO(content)) as archive:
                info_list = archive.infolist()
                if not info_list or len(info_list) > 30:
                    raise ValueError("Skill ZIP 文件数量必须在 1 到 30 个之间")
                if sum(item.file_size for item in info_list) > 3 * 1024 * 1024:
                    raise ValueError("Skill ZIP 解压后的总大小不能超过 3MB")
                names = [item.filename.replace("\\", "/") for item in info_list]
                if any(not name or name.startswith("/") or ".." in name.split("/") for name in names):
                    raise ValueError("Skill ZIP 包含不安全文件路径")
                skill_entry = next((item for item in info_list if item.filename.replace("\\", "/") == "SKILL.md"), None)
                if not skill_entry:
                    raise ValueError("Skill ZIP 必须在根目录包含 SKILL.md")
                if skill_entry.file_size > 256 * 1024:
                    raise ValueError("SKILL.md 不能超过 256KB")
                skill_markdown = archive.read(skill_entry).decode("utf-8-sig")
        except BadZipFile as exc:
            raise ValueError("上传文件不是有效的 ZIP 包") from exc
        except UnicodeDecodeError as exc:
            raise ValueError("SKILL.md 必须使用 UTF-8 编码") from exc

        metadata = self._extract_skill_metadata(skill_markdown, filename)
        record = SysManagedAgentSkill(
            managed_skill_id=generate_id("mskill"),
            domain_id=domain_id,
            skill_name=metadata["skill_name"],
            skill_desc=metadata["skill_desc"],
            package_filename=self._safe_uploaded_filename(filename),
            package_content=content,
            package_size=len(content),
            file_count=len(info_list),
            use_count=0,
            status="ACTIVE",
            uploaded_by=uploaded_by or "unknown",
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._serialize_managed_skill(record)

    def delete_managed_skill(self, managed_skill_id: str, domain_id: str):
        record = self.db.query(SysManagedAgentSkill).filter(
            SysManagedAgentSkill.managed_skill_id == managed_skill_id,
            SysManagedAgentSkill.domain_id == domain_id,
        ).first()
        if not record:
            raise ValueError("托管 Skill 不存在")
        self.db.query(SysManagedAgentSkillTestSession).filter(
            SysManagedAgentSkillTestSession.managed_skill_id == managed_skill_id
        ).delete(synchronize_session=False)
        self.db.delete(record)
        self.db.commit()

    def list_managed_skill_test_sessions(self) -> List[Dict[str, Any]]:
        rows = self.db.query(SysManagedAgentSkillTestSession).order_by(
            SysManagedAgentSkillTestSession.updated_at.desc(),
            SysManagedAgentSkillTestSession.created_at.desc(),
        ).all()
        return [self._serialize_managed_skill_test_session(item, include_result=False) for item in rows]

    def get_managed_skill_test_session(self, session_id: str) -> Dict[str, Any]:
        session = self.db.query(SysManagedAgentSkillTestSession).filter(
            SysManagedAgentSkillTestSession.session_id == session_id
        ).first()
        if not session:
            raise ValueError("测试历史不存在")
        return self._serialize_managed_skill_test_session(session, include_result=True)

    def start_managed_skill_test_session(
        self,
        managed_skill_id: str,
        payload: Dict[str, Any],
        created_by: str = "unknown",
    ) -> Dict[str, Any]:
        return self.managed_skill_test_orchestrator.start_session(
            managed_skill_id,
            payload,
            created_by,
        )

    def get_managed_skill_test_session_owner_domain(self, session_id: str) -> Optional[str]:
        session = self.db.query(SysManagedAgentSkillTestSession).filter(
            SysManagedAgentSkillTestSession.session_id == session_id
        ).first()
        if not session:
            raise ValueError("测试历史不存在")
        managed_skill = self.db.query(SysManagedAgentSkill).filter(
            SysManagedAgentSkill.managed_skill_id == session.managed_skill_id
        ).first()
        return managed_skill.domain_id if managed_skill else None

    async def stream_managed_skill_test_events(
        self,
        session_id: str,
        *,
        turn_no: Optional[int] = None,
        delay_ms: int = 0,
    ):
        session = self.db.query(SysManagedAgentSkillTestSession).filter(
            SysManagedAgentSkillTestSession.session_id == session_id
        ).first()
        if not session:
            raise ValueError("测试历史不存在")
        result_payload = self._safe_json_loads(session.result_json, {})
        events = extract_execution_events(result_payload, turn_no=turn_no)
        return replay_execution_events(
            events,
            session_id=session_id,
            turn_no=turn_no,
            delay_ms=max(0, min(int(delay_ms or 0), 1000)),
        )

    async def stream_managed_skill_test_turn(
        self,
        managed_skill_id: str,
        payload: Dict[str, Any],
        created_by: str = "unknown",
    ):
        return self.managed_skill_test_orchestrator.stream_execute_turn(
            managed_skill_id,
            payload,
            created_by,
        )

    async def test_managed_skill(self, managed_skill_id: str, payload: Dict[str, Any], created_by: str = "unknown") -> Dict[str, Any]:
        """Use an uploaded Skill package with sampled, read-only source data for an agent test."""
        return await self.managed_skill_test_orchestrator.execute_turn(
            managed_skill_id,
            payload,
            created_by,
        )

    def _save_managed_skill_test_session(
        self,
        *,
        session: Optional[SysManagedAgentSkillTestSession],
        managed_skill: SysManagedAgentSkill,
        source: Optional[SysDataSource],
        payload: Dict[str, Any],
        question: str,
        conversation: List[Dict[str, str]],
        response: Dict[str, Any],
        created_by: str,
    ) -> SysManagedAgentSkillTestSession:
        if not session:
            session = SysManagedAgentSkillTestSession(
                session_id=generate_id("mstest"),
                managed_skill_id=managed_skill.managed_skill_id,
                skill_name=managed_skill.skill_name,
                source_id=payload["source_id"],
                source_name=source.source_name if source else "",
                schema_name=payload.get("schema") or "",
                llm_config_id=payload["llm_config_id"],
                sample_limit=max(1, min(int(payload.get("sample_limit") or 100), MANAGED_SKILL_TEST_SAMPLE_LIMIT_MAX)),
                created_by=created_by or "unknown",
            )
            self.db.add(session)
        session.sample_limit = max(1, min(int(payload.get("sample_limit") or 100), MANAGED_SKILL_TEST_SAMPLE_LIMIT_MAX))
        session.session_title = question[:500]
        session.last_question = question[:2000]
        session.message_count = len(conversation)
        session.conversation_json = json.dumps(conversation, ensure_ascii=False)
        session.result_json = json.dumps(response, ensure_ascii=False, default=str)
        session.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(session)
        return session

    def _save_managed_skill_clarification(
        self,
        *,
        session: Optional[SysManagedAgentSkillTestSession],
        managed_skill: SysManagedAgentSkill,
        source: Optional[SysDataSource],
        payload: Dict[str, Any],
        question: str,
        stored_conversation_history: List[Dict[str, str]],
        previous_turn_results: List[Dict[str, Any]],
        created_by: str,
    ) -> Dict[str, Any]:
        """Persist an agent clarification turn without guessing a graph query."""
        clarification = "请说明本次要查询的对象或关系，例如“查询该瓶码的质检记录”“查询该批次的工厂信息”；如有编码，请一并提供。"
        conversation = stored_conversation_history + [{"role": "user", "content": question}, {"role": "assistant", "content": clarification}]
        trace = [
            {"step_no": 1, "stage": "SKILL_LOAD", "title": "加载上传 Skill", "status": "SUCCESS", "detail": "已加载 Skill，准备识别当前问题。"},
            {"step_no": 2, "stage": "CLARIFICATION", "title": "请求澄清当前问题", "status": "SUCCESS", "detail": "当前消息未明确查询目标，未执行 Oracle Graph SQL，也未复用历史查询。"},
        ]
        table_preview = {"columns": [], "sample_rows": []}
        current_turn = {
            "turn_no": len(previous_turn_results) + 1,
            "user_message_no": len([item for item in conversation if item.get("role") == "user"]) - 1,
            "question": question,
            "table_preview": table_preview,
            "plan": {"plan_version": "1.0", "intent_type": "CLARIFICATION_REQUIRED", "selected_objects": [], "steps": []},
            "evidence_tables": [],
            "analysis_result": {"summary": clarification, "intent_type": "CLARIFICATION_REQUIRED", "selected_objects": [], "evidence_table_keys": [], "applied_metrics": [], "matched_rules": [], "suggested_activities": [], "trend_summaries": [], "analysis_flags": [], "period_comparisons": [], "top_findings": []},
            "completion_assessment": {
                "status": "PENDING",
                "completed": False,
                "deterministic": {"status": "SKIPPED", "reason": "当前为澄清轮，尚未执行数据查询。"},
                "coverage_check": {"status": "SKIPPED", "reason": "当前为澄清轮，尚未执行数据查询。"},
                "judge": {"required": False, "status": "SKIPPED", "reason": "当前为澄清轮，尚未执行数据查询。"},
                "summary": "当前问题需要先澄清，尚未进入任务完成判定。",
            },
            "agent_output": clarification,
            "execution_trace": trace,
            "execution_events": [
                {"event_type": "SKILL_LOAD", "step_id": "skill_load", "title": "加载上传 Skill", "runtime_state": "COMPLETED", "status": "SUCCESS", "detail": "已加载 Skill，准备识别当前问题。", "payload": {}},
                {"event_type": "CLARIFICATION", "step_id": "clarify", "title": "请求澄清当前问题", "runtime_state": "COMPLETED", "status": "SUCCESS", "detail": "当前消息未明确查询目标，未执行 Oracle Graph SQL，也未复用历史查询。", "payload": {}},
            ],
            "executed_queries": [],
            "warnings": [],
        }
        response = {
            "managed_skill": self._serialize_managed_skill(managed_skill),
            "execution_model": {"llm_config_id": payload["llm_config_id"]},
            "test_context": {"source_id": payload["source_id"], "source_name": source.source_name if source else "", "schema": payload.get("schema"), "property_graph": "", "ontology_node": "", "intent_type": "CLARIFICATION_REQUIRED", "test_question": question},
            "conversation": conversation,
            "agent_output": clarification,
            "plan": current_turn["plan"],
            "execution_trace": trace,
            "execution_events": current_turn["execution_events"],
            "executed_queries": [],
            "warnings": [],
            "table_preview": table_preview,
            "evidence_tables": [],
            "analysis_result": current_turn["analysis_result"],
            "completion_assessment": current_turn["completion_assessment"],
            "turn_results": previous_turn_results + [current_turn],
        }
        response["planning_stats"] = self._build_managed_skill_planning_stats(response)
        saved_session = self._save_managed_skill_test_session(
            session=session, managed_skill=managed_skill, source=source, payload=payload,
            question=question, conversation=conversation, response=response, created_by=created_by,
        )
        response["session_id"] = saved_session.session_id
        return response

    @staticmethod
    def _needs_question_clarification(question: str) -> bool:
        normalized = re.sub(r"\s+", "", question or "")
        if re.search(r"\b(?:BOT|BATCH|CASE|PACK|PALLET|STACK|OUT|TRANS)-[A-Z0-9_-]+\b", normalized.upper()):
            return False
        ambiguous_messages = {"继续", "继续查询", "再查一下", "这个呢", "这个怎么样", "查一下", "查询一下", "分析一下"}
        return normalized in ambiguous_messages

    def _build_managed_skill_planning_stats(self, result_payload: Dict[str, Any]) -> Dict[str, Any]:
        turn_results = result_payload.get("turn_results") if isinstance(result_payload, dict) else None
        if not isinstance(turn_results, list):
            turn_results = []
        total_turns = 0
        reference_pattern_turns = 0
        llm_plan_turns = 0
        clarification_turns = 0
        session_ready_turns = 0
        pattern_counter: Dict[str, int] = {}
        latest_planning_mode = ""
        latest_reference_pattern_id = ""
        for item in turn_results:
            if not isinstance(item, dict):
                continue
            plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
            intent_type = str(plan.get("intent_type") or "").upper()
            if intent_type == "SESSION_READY":
                session_ready_turns += 1
                continue
            total_turns += 1
            planning_mode = str(plan.get("planning_mode") or "").upper()
            reference_pattern_id = str(plan.get("reference_pattern_id") or "").strip()
            latest_planning_mode = planning_mode or latest_planning_mode
            latest_reference_pattern_id = reference_pattern_id or latest_reference_pattern_id
            if intent_type == "CLARIFICATION_REQUIRED":
                clarification_turns += 1
                continue
            if planning_mode == "REFERENCE_PATTERN":
                reference_pattern_turns += 1
                if reference_pattern_id:
                    pattern_counter[reference_pattern_id] = pattern_counter.get(reference_pattern_id, 0) + 1
            elif planning_mode == "LLM_PLAN":
                llm_plan_turns += 1
        actionable_turns = max(0, total_turns - clarification_turns)
        reference_pattern_hit_rate = round((reference_pattern_turns / actionable_turns) * 100, 2) if actionable_turns else 0.0
        top_patterns = [
            {"reference_pattern_id": key, "count": value}
            for key, value in sorted(pattern_counter.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]
        return {
            "total_turns": total_turns,
            "actionable_turns": actionable_turns,
            "reference_pattern_turns": reference_pattern_turns,
            "llm_plan_turns": llm_plan_turns,
            "clarification_turns": clarification_turns,
            "session_ready_turns": session_ready_turns,
            "reference_pattern_hit_rate": reference_pattern_hit_rate,
            "llm_planner_saved_count": reference_pattern_turns,
            "latest_planning_mode": latest_planning_mode,
            "latest_reference_pattern_id": latest_reference_pattern_id,
            "top_reference_patterns": top_patterns,
        }

    def _serialize_managed_skill_test_session(
        self, session: SysManagedAgentSkillTestSession, *, include_result: bool
    ) -> Dict[str, Any]:
        result_payload = self._safe_json_loads(session.result_json, {})
        managed_skill = self.db.query(SysManagedAgentSkill).filter(
            SysManagedAgentSkill.managed_skill_id == session.managed_skill_id
        ).first()
        domain_name = None
        if managed_skill and managed_skill.domain_id:
            domain = self.db.query(SysDomain).filter(SysDomain.domain_id == managed_skill.domain_id).first()
            domain_name = domain.domain_name if domain else None
        data = {
            "session_id": session.session_id,
            "managed_skill_id": session.managed_skill_id,
            "domain_id": managed_skill.domain_id if managed_skill else None,
            "domain_name": domain_name,
            "skill_name": session.skill_name,
            "source_id": session.source_id,
            "source_name": session.source_name,
            "schema": session.schema_name,
            "llm_config_id": session.llm_config_id,
            "sample_limit": session.sample_limit,
            "session_title": session.session_title,
            "last_question": session.last_question,
            "message_count": session.message_count or 0,
            "created_by": session.created_by,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "planning_stats": self._build_managed_skill_planning_stats(result_payload),
        }
        if include_result:
            data["conversation"] = self._safe_json_loads(session.conversation_json, [])
            data["result"] = result_payload
        return data

    @staticmethod
    def _build_skill_query_guidance(skill_markdown: str, skill_files: Dict[str, str]) -> str:
        sections = [skill_markdown[:8000]]
        for path in [
            "references/ontology-model.json",
            "references/ontology-graph-binding.json",
            "references/execution-contract.json",
            "references/analysis-strategy.md",
            "references/metric-catalog.json",
            "references/rule-catalog.json",
            "references/activity-playbook.json",
        ]:
            content = skill_files.get(path)
            if content:
                sections.append(f"## {path}\n{content[:8000]}")
        return "\n\n".join(sections)

    @staticmethod
    def _derive_managed_skill_intent_type(question: str) -> str:
        text = (question or "").upper()
        if any(token in text for token in ("追溯", "链路", "路径", "上游", "下游")):
            return "TRACEBACK_ANALYSIS"
        if any(token in text for token in ("影响", "波及", "召回", "覆盖范围")):
            return "IMPACT_INFERENCE"
        if any(token in text for token in ("根因", "原因", "为何", "为什么")):
            return "ROOT_CAUSE_ANALYSIS"
        if any(token in text for token in ("规则", "判定", "是否异常", "是否超规")):
            return "RULE_JUDGEMENT"
        if any(token in text for token in ("指标", "趋势", "波动", "统计")):
            return "METRIC_EXPLANATION"
        return "RELATION_PATH_LOOKUP"

    def _load_execution_contract(self, skill_files: Dict[str, str]) -> Dict[str, Any]:
        raw = skill_files.get("references/execution-contract.json")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        if not isinstance(data.get("reference_patterns"), list) or not data.get("reference_patterns"):
            data["reference_patterns"] = self._default_managed_skill_reference_patterns(data)
        return data

    @staticmethod
    def _normalize_label_name(value: Any) -> str:
        return str(value or "").strip().upper()

    def _build_topology_indexes(self, topology: Dict[str, Any]) -> Dict[str, Any]:
        nodes = topology.get("nodes") or []
        nodes_by_label = {
            self._normalize_label_name(node.get("displayName") or node.get("name")): node
            for node in nodes
            if self._normalize_label_name(node.get("displayName") or node.get("name"))
        }
        node_id_to_label = {
            str(node.get("id") or ""): self._normalize_label_name(node.get("displayName") or node.get("name"))
            for node in nodes
            if str(node.get("id") or "")
        }
        adjacency: Dict[str, List[Dict[str, str]]] = {}
        for edge in topology.get("edges") or []:
            source_label = node_id_to_label.get(str(edge.get("source") or "")) or self._normalize_label_name(edge.get("source"))
            target_label = node_id_to_label.get(str(edge.get("target") or "")) or self._normalize_label_name(edge.get("target"))
            edge_label = self._normalize_label_name(edge.get("name"))
            if not source_label or not target_label or not edge_label:
                continue
            adjacency.setdefault(source_label, []).append({"target": target_label, "edge": edge_label, "direction": "OUT"})
            adjacency.setdefault(target_label, []).append({"target": source_label, "edge": edge_label, "direction": "IN"})
        return {
            "nodes_by_label": nodes_by_label,
            "adjacency": adjacency,
        }

    def _find_graph_path(
        self,
        topology: Dict[str, Any],
        root_label: str,
        target_label: str,
        max_depth: int = MANAGED_SKILL_TEST_PLAN_MAX_PATH_DEPTH,
    ) -> Optional[List[Dict[str, str]]]:
        indexes = self._build_topology_indexes(topology)
        adjacency = indexes["adjacency"]
        root = self._normalize_label_name(root_label)
        target = self._normalize_label_name(target_label)
        if not root or not target:
            return None
        if root == target:
            return []
        queue = deque([(root, [])])
        visited = {root}
        while queue:
            current, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            for item in adjacency.get(current, []):
                next_label = item["target"]
                if next_label in visited:
                    continue
                next_path = path + [item]
                if next_label == target:
                    return next_path
                visited.add(next_label)
                queue.append((next_label, next_path))
        return None

    @staticmethod
    def _safe_graph_sql_literal(value: str) -> str:
        return str(value or "").replace("'", "''")

    @staticmethod
    def _filter_queryable_properties(node: Dict[str, Any], requested: Optional[List[str]] = None) -> List[str]:
        identifier_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
        properties = []
        property_map = {
            str(prop.get("property_name") or "").upper(): prop
            for prop in (node.get("properties") or [])
        }
        requested_names = [
            str(name or "").upper() for name in (requested or [])
            if str(name or "").upper() in property_map
        ]
        if not requested_names:
            requested_names = list(property_map.keys())
        for name in requested_names:
            prop = property_map.get(name) or {}
            data_type = str(prop.get("data_type") or "").upper()
            if identifier_pattern.fullmatch(name) and not any(token in data_type for token in ("BLOB", "CLOB", "NCLOB", "BFILE", "LONG", "XMLTYPE")):
                properties.append(name)
        return properties[:MANAGED_SKILL_TEST_PLAN_MAX_PROPERTIES]

    def _build_graph_root_query_sql(
        self,
        graph_name: str,
        node: Dict[str, Any],
        filter_property: Optional[str],
        filter_value: Optional[str],
        display_properties: Optional[List[str]] = None,
    ) -> str:
        label = self._normalize_label_name(node.get("displayName") or node.get("name"))
        properties = self._filter_queryable_properties(node, display_properties)
        if filter_property:
            filter_name = self._normalize_label_name(filter_property)
            if filter_name not in properties:
                properties = [filter_name] + properties
        properties = list(dict.fromkeys(properties))
        if not properties:
            raise ValueError(f"本体节点 {label} 没有可用于查询的属性")
        projections = ",\n      ".join(f"n.{name} AS {name}" for name in properties)
        sql = f"""SELECT *
FROM GRAPH_TABLE(
  {graph_name.upper()}
  MATCH (n IS {label})
  COLUMNS (
      {projections}
  )
)"""
        if filter_property and filter_value:
            sql += f"\nWHERE {self._normalize_label_name(filter_property)} = '{self._safe_graph_sql_literal(filter_value)}'"
        return sql

    def _build_graph_relation_query_sql(
        self,
        graph_name: str,
        root_node: Dict[str, Any],
        target_node: Dict[str, Any],
        path: List[Dict[str, str]],
        filter_property: str,
        filter_value: str,
        root_properties: Optional[List[str]] = None,
        target_properties: Optional[List[str]] = None,
    ) -> str:
        root_label = self._normalize_label_name(root_node.get("displayName") or root_node.get("name"))
        target_label = self._normalize_label_name(target_node.get("displayName") or target_node.get("name"))
        root_columns = self._filter_queryable_properties(root_node, root_properties)
        target_columns = self._filter_queryable_properties(target_node, target_properties)
        filter_name = self._normalize_label_name(filter_property)
        if filter_name not in root_columns:
            root_columns = [filter_name] + root_columns
        root_columns = list(dict.fromkeys(root_columns))
        target_columns = list(dict.fromkeys(target_columns))
        if not target_columns:
            raise ValueError(f"目标节点 {target_label} 没有可用于查询的属性")
        aliases = ["r"]
        match_parts = [f"(r IS {root_label})"]
        current_label = root_label
        for hop_index, hop in enumerate(path, start=1):
            next_label = self._normalize_label_name(hop.get("target"))
            direction = hop.get("direction")
            edge_label = self._normalize_label_name(hop.get("edge"))
            alias = "t" if hop_index == len(path) else f"n{hop_index}"
            aliases.append(alias)
            relation = f"-[e{hop_index} IS {edge_label}]->" if direction == "OUT" else f"<-[e{hop_index} IS {edge_label}]-"
            match_parts.append(f"{relation}({alias} IS {next_label})")
            current_label = next_label
        if current_label != target_label:
            raise ValueError("关系路径终点与目标节点不一致")
        projections = [f"r.{name} AS ROOT_{name}" for name in root_columns]
        projections.extend(f"t.{name} AS {target_label}_{name}" for name in target_columns)
        sql = f"""WITH relation_result AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name.upper()}
    MATCH {''.join(match_parts)}
    COLUMNS (
      {', '.join(projections)}
    )
  )
)
SELECT *
FROM relation_result
WHERE ROOT_{filter_name} = '{self._safe_graph_sql_literal(filter_value)}'"""
        return sql

    def _derive_execution_contract_from_topology(
        self,
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
    ) -> Dict[str, Any]:
        nodes = topology.get("nodes") or []
        metric_catalog = self._safe_json_loads(skill_files.get("references/metric-catalog.json"), {})
        has_metrics = bool((metric_catalog.get("metrics") if isinstance(metric_catalog, dict) else None) or [])
        node_labels = [
            self._normalize_label_name(node.get("displayName") or node.get("name"))
            for node in nodes[:20]
            if self._normalize_label_name(node.get("displayName") or node.get("name"))
        ]
        object_aliases = self._build_reference_object_aliases(node_labels)
        property_aliases = self._build_reference_property_aliases(
            labels=node_labels,
            topology=topology,
            explicit_aliases={},
        )
        relation_aliases = self._build_reference_relation_aliases(
            topology=topology,
            object_aliases=object_aliases,
            explicit_aliases={},
        )
        contract = {
            "entry_objects": node_labels[:6],
            "target_objects": node_labels[:12],
            "query_modes": ["single_node", "path_expand"] + (["fact_aggregate", "group_by_object", "group_by_object_time_window", "group_by_time_window", "metric_formula", "filter_aggregate_result", "order_and_limit", "apply_rules"] if has_metrics else []),
            "preferred_paths": [],
            "required_display_properties": {},
            "time_dimensions": {},
            "object_aliases": object_aliases,
            "property_aliases": property_aliases,
            "relation_aliases": relation_aliases,
            "forbidden_properties": ["RAW_JSON", "LARGE_CLOB"],
        }
        contract["reference_patterns"] = self._default_managed_skill_reference_patterns(contract)
        return contract

    def _default_managed_skill_reference_patterns(self, execution_contract: Dict[str, Any]) -> List[Dict[str, Any]]:
        entry_objects = [
            self._normalize_label_name(item)
            for item in (execution_contract.get("entry_objects") or [])
            if self._normalize_label_name(item)
        ]
        target_objects = [
            self._normalize_label_name(item)
            for item in (execution_contract.get("target_objects") or [])
            if self._normalize_label_name(item)
        ]
        query_modes = {
            str(item or "").strip()
            for item in (execution_contract.get("query_modes") or [])
            if str(item or "").strip()
        }
        patterns: List[Dict[str, Any]] = []
        if target_objects:
            patterns.append({
                "pattern_id": "reference_relation_lookup",
                "question_pattern": "查询关联对象详情",
                "plan_template": ["select_root", "expand_relations", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["关联", "关系", "详情", "明细", "信息", "记录", "链路", "追溯"],
                "max_targets": 1,
            })
        if "group_by_object" in query_modes and target_objects:
            patterns.append({
                "pattern_id": "reference_group_by_object",
                "question_pattern": "查询关联对象分别多少",
                "plan_template": ["select_root", "expand_relations", "group_by_object", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "required_keywords": ["多少"],
                "optional_keywords": ["哪些", "分别", "各", "每个", "按"],
                "max_targets": 1,
            })
        if "group_by_time_window" in query_modes:
            patterns.append({
                "pattern_id": "reference_time_trend",
                "question_pattern": "按时间统计趋势",
                "plan_template": ["select_root", "expand_relations", "group_by_time_window", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["趋势", "按天", "按周", "按月", "每天", "每周", "每月", "最近", "近"],
                "max_targets": 1,
            })
        if "group_by_object_time_window" in query_modes and target_objects:
            patterns.append({
                "pattern_id": "reference_object_time_trend",
                "question_pattern": "按对象和时间统计趋势",
                "plan_template": ["select_root", "expand_relations", "group_by_object_time_window", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["趋势", "分别", "每个", "按", "每天", "每周", "每月", "近"],
                "max_targets": 1,
            })
        if "metric_formula" in query_modes:
            patterns.append({
                "pattern_id": "reference_metric_formula",
                "question_pattern": "查询比率或占比",
                "plan_template": ["select_root", "expand_relations", "metric_formula", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["率", "占比", "比例", "比率"],
                "max_targets": 1,
            })
        if "filter_aggregate_result" in query_modes and target_objects:
            patterns.append({
                "pattern_id": "reference_filtered_group",
                "question_pattern": "筛选统计结果",
                "plan_template": ["select_root", "expand_relations", "group_by_object", "filter_aggregate_result", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["大于", "超过", "高于", "至少", "不少于", "小于", "低于"],
                "max_targets": 1,
            })
        if "order_and_limit" in query_modes and target_objects:
            patterns.append({
                "pattern_id": "reference_ranked_group",
                "question_pattern": "TopN 排名统计",
                "plan_template": ["select_root", "expand_relations", "group_by_object", "order_and_limit", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["TOP", "前", "最多", "排名", "最高", "最低", "BOTTOM"],
                "max_targets": 1,
            })
        if "apply_rules" in query_modes and target_objects:
            patterns.append({
                "pattern_id": "reference_rule_judgement",
                "question_pattern": "规则判定",
                "plan_template": ["select_root", "expand_relations", "group_by_object", "apply_rules", "summarize_evidence"],
                "root_candidates": entry_objects[:3],
                "target_candidates": target_objects[:4],
                "optional_keywords": ["异常", "规则", "预警", "风险", "告警", "超规"],
                "max_targets": 1,
            })
        return patterns

    @staticmethod
    def _split_label_tokens(label: str) -> List[str]:
        text = str(label or "").strip()
        if not text:
            return []
        if "_" in text:
            return [item for item in text.upper().split("_") if item]
        parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", text)
        if parts:
            return [item.upper() for item in parts if item]
        return [text.upper()]

    def _label_alias_candidates(self, label: str, aliases: Optional[List[str]] = None) -> List[str]:
        normalized_label = self._normalize_label_name(label)
        candidates: List[str] = []
        for item in [normalized_label] + list(aliases or []) + REFERENCE_LABEL_ALIAS_LEXICON.get(normalized_label, []):
            text = str(item or "").strip()
            if text:
                candidates.append(text)
        tokens = self._split_label_tokens(normalized_label)
        if tokens:
            candidates.extend(tokens)
            if len(tokens) > 1:
                candidates.append(" ".join(tokens))
        deduped: List[str] = []
        seen = set()
        for item in candidates:
            normalized = str(item or "").strip()
            key = normalized.upper()
            if normalized and key not in seen:
                deduped.append(normalized)
                seen.add(key)
        return deduped

    def _build_reference_object_aliases(
        self,
        labels: List[str],
        explicit_aliases: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, List[str]]:
        alias_map: Dict[str, List[str]] = {}
        for label in labels or []:
            normalized = self._normalize_label_name(label)
            if not normalized:
                continue
            alias_map[normalized] = self._label_alias_candidates(
                normalized,
                aliases=(explicit_aliases or {}).get(normalized) or [],
            )
        return alias_map

    def _property_alias_candidates(self, property_name: str, aliases: Optional[List[str]] = None) -> List[str]:
        normalized_property = self._normalize_label_name(property_name)
        candidates: List[str] = []
        for item in [normalized_property] + list(aliases or []):
            text = str(item or "").strip()
            if text:
                candidates.append(text)
        tokens = self._split_label_tokens(normalized_property)
        if tokens:
            candidates.extend(tokens)
            if len(tokens) > 1:
                candidates.append(" ".join(tokens))
        for token in tokens:
            candidates.extend(REFERENCE_PROPERTY_ALIAS_LEXICON.get(token, []))
        deduped: List[str] = []
        seen = set()
        for item in candidates:
            normalized = str(item or "").strip()
            key = normalized.upper()
            if normalized and key not in seen:
                deduped.append(normalized)
                seen.add(key)
        return deduped

    def _build_reference_property_aliases(
        self,
        *,
        labels: List[str],
        topology: Dict[str, Any],
        explicit_aliases: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, List[str]]:
        nodes_by_label = self._build_topology_indexes(topology)["nodes_by_label"]
        alias_map: Dict[str, List[str]] = {}
        for label in labels or []:
            normalized = self._normalize_label_name(label)
            if not normalized:
                continue
            node = nodes_by_label.get(normalized) or {}
            candidates: List[str] = []
            for item in (explicit_aliases or {}).get(normalized) or []:
                if str(item or "").strip():
                    candidates.append(str(item or "").strip())
            for prop in (node.get("properties") or [])[:20]:
                candidates.extend(
                    self._property_alias_candidates(
                        str(prop.get("property_name") or ""),
                    )
                )
            alias_map[normalized] = self._normalize_string_list(candidates)
        return alias_map

    @staticmethod
    def _reference_relation_key(source_label: str, target_label: str) -> str:
        return f"{str(source_label or '').strip().upper()}->{str(target_label or '').strip().upper()}"

    def _relation_alias_candidates(self, relation_name: str, aliases: Optional[List[str]] = None) -> List[str]:
        normalized_relation = self._normalize_label_name(relation_name)
        candidates: List[str] = []
        for item in [normalized_relation] + list(aliases or []):
            text = str(item or "").strip()
            if text:
                candidates.append(text)
        tokens = self._split_label_tokens(normalized_relation)
        if tokens:
            candidates.extend(tokens)
            if len(tokens) > 1:
                candidates.append(" ".join(tokens))
        if normalized_relation and normalized_relation != "GRAPH_LABEL":
            candidates.append(f"关联{normalized_relation}")
        candidates.extend(["关联", "关系", "链路"])
        return self._normalize_string_list(candidates)

    def _build_reference_relation_aliases(
        self,
        *,
        topology: Dict[str, Any],
        object_aliases: Dict[str, List[str]],
        explicit_aliases: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, List[str]]:
        aliases_by_relation: Dict[str, List[str]] = {}
        for edge in topology.get("edges") or []:
            source_label = self._normalize_label_name(edge.get("source"))
            target_label = self._normalize_label_name(edge.get("target"))
            if not source_label or not target_label:
                continue
            relation_key = self._reference_relation_key(source_label, target_label)
            candidates: List[str] = []
            if relation_key in (explicit_aliases or {}):
                candidates.extend((explicit_aliases or {}).get(relation_key) or [])
            edge_name = str(edge.get("name") or "").strip()
            if edge_name:
                candidates.extend(self._relation_alias_candidates(edge_name))
            for target_alias in (object_aliases.get(target_label) or [])[:3]:
                candidates.append(f"关联{target_alias}")
                candidates.append(f"{target_alias}关系")
            aliases_by_relation[relation_key] = self._normalize_string_list(candidates)
        return aliases_by_relation

    def _load_reference_patterns(self, execution_contract: Dict[str, Any]) -> List[Dict[str, Any]]:
        patterns = execution_contract.get("reference_patterns") if isinstance(execution_contract, dict) else None
        if not isinstance(patterns, list) or not patterns:
            return self._default_managed_skill_reference_patterns(execution_contract or {})
        normalized: List[Dict[str, Any]] = []
        for index, item in enumerate(patterns, start=1):
            if not isinstance(item, dict):
                continue
            plan_template = [
                str(action or "").strip()
                for action in (item.get("plan_template") or [])
                if str(action or "").strip()
            ]
            if not plan_template or plan_template[0] != "select_root":
                continue
            normalized.append({
                "pattern_id": str(item.get("pattern_id") or f"reference_pattern_{index}").strip(),
                "question_pattern": str(item.get("question_pattern") or "").strip(),
                "plan_template": plan_template,
                "required_keywords": [str(token or "").strip() for token in (item.get("required_keywords") or []) if str(token or "").strip()],
                "optional_keywords": [str(token or "").strip() for token in (item.get("optional_keywords") or []) if str(token or "").strip()],
                "root_candidates": [self._normalize_label_name(token) for token in (item.get("root_candidates") or []) if self._normalize_label_name(token)],
                "target_candidates": [self._normalize_label_name(token) for token in (item.get("target_candidates") or []) if self._normalize_label_name(token)],
                "max_targets": max(1, min(int(item.get("max_targets") or 1), MANAGED_SKILL_TEST_PLAN_MAX_TARGETS)),
            })
        return normalized or self._default_managed_skill_reference_patterns(execution_contract or {})

    def _contract_object_aliases(self, execution_contract: Dict[str, Any]) -> Dict[str, List[str]]:
        alias_payload = execution_contract.get("object_aliases") if isinstance(execution_contract, dict) else None
        explicit_aliases: Dict[str, List[str]] = {}
        if isinstance(alias_payload, dict):
            for label, aliases in alias_payload.items():
                normalized = self._normalize_label_name(label)
                if not normalized or not isinstance(aliases, list):
                    continue
                explicit_aliases[normalized] = [str(item or "").strip() for item in aliases if str(item or "").strip()]
        all_labels = [
            self._normalize_label_name(item)
            for item in ((execution_contract.get("entry_objects") or []) + (execution_contract.get("target_objects") or []))
            if self._normalize_label_name(item)
        ]
        return self._build_reference_object_aliases(all_labels, explicit_aliases)

    def _contract_property_aliases(self, execution_contract: Dict[str, Any], topology: Dict[str, Any]) -> Dict[str, List[str]]:
        alias_payload = execution_contract.get("property_aliases") if isinstance(execution_contract, dict) else None
        explicit_aliases: Dict[str, List[str]] = {}
        if isinstance(alias_payload, dict):
            for label, aliases in alias_payload.items():
                normalized = self._normalize_label_name(label)
                if not normalized or not isinstance(aliases, list):
                    continue
                explicit_aliases[normalized] = [str(item or "").strip() for item in aliases if str(item or "").strip()]
        all_labels = [
            self._normalize_label_name(item)
            for item in ((execution_contract.get("entry_objects") or []) + (execution_contract.get("target_objects") or []))
            if self._normalize_label_name(item)
        ]
        return self._build_reference_property_aliases(
            labels=all_labels,
            topology=topology,
            explicit_aliases=explicit_aliases,
        )

    def _contract_relation_aliases(self, execution_contract: Dict[str, Any], topology: Dict[str, Any]) -> Dict[str, List[str]]:
        alias_payload = execution_contract.get("relation_aliases") if isinstance(execution_contract, dict) else None
        explicit_aliases: Dict[str, List[str]] = {}
        if isinstance(alias_payload, dict):
            for relation_key, aliases in alias_payload.items():
                normalized = str(relation_key or "").strip().upper()
                if not normalized or not isinstance(aliases, list):
                    continue
                explicit_aliases[normalized] = [str(item or "").strip() for item in aliases if str(item or "").strip()]
        object_aliases = self._contract_object_aliases(execution_contract)
        return self._build_reference_relation_aliases(
            topology=topology,
            object_aliases=object_aliases,
            explicit_aliases=explicit_aliases,
        )

    def _label_alias_hits(self, question: str, label: str, object_aliases: Dict[str, List[str]]) -> int:
        normalized = str(question or "").upper()
        aliases = object_aliases.get(self._normalize_label_name(label), [])
        return sum(1 for token in aliases if token and str(token).upper() in normalized)

    def _property_alias_hits(self, question: str, label: str, property_aliases: Dict[str, List[str]]) -> int:
        normalized = str(question or "").upper()
        aliases = property_aliases.get(self._normalize_label_name(label), [])
        return sum(1 for token in aliases if token and str(token).upper() in normalized)

    def _relation_alias_hits(
        self,
        question: str,
        source_label: str,
        target_label: str,
        relation_aliases: Dict[str, List[str]],
    ) -> int:
        normalized = str(question or "").upper()
        aliases = relation_aliases.get(self._reference_relation_key(source_label, target_label), [])
        return sum(1 for token in aliases if token and str(token).upper() in normalized)

    def _reference_pattern_score(
        self,
        question: str,
        pattern: Dict[str, Any],
        object_aliases: Optional[Dict[str, List[str]]] = None,
        property_aliases: Optional[Dict[str, List[str]]] = None,
        relation_aliases: Optional[Dict[str, List[str]]] = None,
    ) -> int:
        normalized = str(question or "").upper()
        plan_template = pattern.get("plan_template") or []
        required_keywords = [str(token or "").upper() for token in (pattern.get("required_keywords") or []) if str(token or "").strip()]
        optional_keywords = [str(token or "").upper() for token in (pattern.get("optional_keywords") or []) if str(token or "").strip()]
        object_aliases = object_aliases or {}
        property_aliases = property_aliases or {}
        relation_aliases = relation_aliases or {}
        if required_keywords and not all(token in normalized for token in required_keywords):
            return -1
        primary_action = next((action for action in reversed(plan_template) if action not in {"summarize_evidence", "order_and_limit", "filter_aggregate_result", "apply_rules"}), "")
        if "metric_formula" in plan_template and not self._question_requests_metric_formula(question):
            return -1
        if "group_by_object_time_window" in plan_template and not self._question_requests_group_by_object_time_window(question):
            return -1
        if "group_by_time_window" in plan_template and "group_by_object_time_window" not in plan_template and not self._question_requests_group_by_time_window(question):
            return -1
        if primary_action == "group_by_object" and not self._question_requests_group_by_object(question):
            return -1
        if "filter_aggregate_result" in plan_template and not self._question_requests_filter_aggregate_result(question):
            return -1
        if "order_and_limit" in plan_template and not self._question_requests_order_and_limit(question):
            return -1
        if "apply_rules" in plan_template and not self._question_requests_apply_rules(question):
            return -1
        if primary_action == "expand_relations" and (
            self._question_requests_group_by_object(question)
            or self._question_requests_group_by_time_window(question)
            or self._question_requests_metric_formula(question)
        ):
            return -1
        score = len(required_keywords) * 10
        score += sum(1 for token in optional_keywords if token in normalized)
        score += sum(self._label_alias_hits(question, label, object_aliases) * 2 for label in (pattern.get("root_candidates") or [])[:2])
        score += sum(self._label_alias_hits(question, label, object_aliases) * 3 for label in (pattern.get("target_candidates") or [])[:2])
        score += sum(self._property_alias_hits(question, label, property_aliases) * 2 for label in (pattern.get("root_candidates") or [])[:2])
        score += sum(self._property_alias_hits(question, label, property_aliases) * 2 for label in (pattern.get("target_candidates") or [])[:2])
        if pattern.get("root_candidates") and pattern.get("target_candidates"):
            score += max(
                (
                    self._relation_alias_hits(question, root_label, target_label, relation_aliases) * 4
                    for root_label in (pattern.get("root_candidates") or [])[:2]
                    for target_label in (pattern.get("target_candidates") or [])[:2]
                ),
                default=0,
            )
        score += len(plan_template)
        if pattern.get("question_pattern"):
            score += 1
        return score

    def _select_reference_pattern(self, question: str, execution_contract: Dict[str, Any], topology: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        best_pattern = None
        best_score = -1
        object_aliases = self._contract_object_aliases(execution_contract)
        property_aliases = self._contract_property_aliases(execution_contract, topology)
        relation_aliases = self._contract_relation_aliases(execution_contract, topology)
        for pattern in self._load_reference_patterns(execution_contract):
            score = self._reference_pattern_score(question, pattern, object_aliases, property_aliases, relation_aliases)
            if score > best_score:
                best_pattern = pattern
                best_score = score
        return best_pattern if best_pattern and best_score >= 0 else None

    def _choose_reference_root_label(
        self,
        *,
        pattern: Dict[str, Any],
        contract: Dict[str, Any],
        nodes_by_label: Dict[str, Any],
        excluded_roots: set[str],
        preferred_root: str,
        question: str,
        topology: Dict[str, Any],
    ) -> str:
        object_aliases = self._contract_object_aliases(contract)
        property_aliases = self._contract_property_aliases(contract, topology)
        candidate_lists = [pattern.get("root_candidates") or [], contract.get("entry_objects") or []]
        if preferred_root and preferred_root in nodes_by_label and preferred_root not in excluded_roots:
            return preferred_root
        scored_candidates: List[tuple[int, str]] = []
        for candidates in candidate_lists:
            for item in candidates:
                label = self._normalize_label_name(item)
                if label and label in nodes_by_label and label not in excluded_roots:
                    score = (
                        self._label_alias_hits(question, label, object_aliases) * 3
                        + self._property_alias_hits(question, label, property_aliases) * 2
                    )
                    scored_candidates.append((score, label))
        if scored_candidates:
            scored_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            if scored_candidates[0][0] > 0:
                return scored_candidates[0][1]
            return scored_candidates[0][1]
        return ""

    def _choose_reference_target_labels(
        self,
        *,
        pattern: Dict[str, Any],
        contract: Dict[str, Any],
        topology: Dict[str, Any],
        root_label: str,
        excluded_targets: set[str],
        question: str,
    ) -> List[str]:
        object_aliases = self._contract_object_aliases(contract)
        property_aliases = self._contract_property_aliases(contract, topology)
        relation_aliases = self._contract_relation_aliases(contract, topology)
        candidates = [
            self._normalize_label_name(item)
            for item in ((pattern.get("target_candidates") or []) or (contract.get("target_objects") or []))
            if self._normalize_label_name(item)
        ]
        scored_candidates = sorted(
            candidates,
            key=lambda item: (
                self._relation_alias_hits(question, root_label, item, relation_aliases) * 4
                + self._label_alias_hits(question, item, object_aliases) * 3
                + self._property_alias_hits(question, item, property_aliases) * 2,
                item,
            ),
            reverse=True,
        )
        selected: List[str] = []
        max_targets = max(1, min(int(pattern.get("max_targets") or 1), MANAGED_SKILL_TEST_PLAN_MAX_TARGETS))
        for target_label in scored_candidates:
            if target_label == root_label or target_label in excluded_targets or target_label in selected:
                continue
            if self._find_graph_path(topology, root_label, target_label):
                selected.append(target_label)
            if len(selected) >= max_targets:
                break
        return selected

    def _build_seed_steps_for_managed_skill_plan(
        self,
        *,
        topology: Dict[str, Any],
        root_label: str,
        filter_property: str,
        filter_value: str,
        root_properties: List[str],
        target_labels: List[str],
        target_properties: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        indexes = self._build_topology_indexes(topology)
        nodes_by_label = indexes["nodes_by_label"]
        root_node = nodes_by_label.get(root_label)
        if not root_node:
            raise ValueError("Agent 未能为当前问题选择有效的本体起点对象")
        root_display_properties = self._filter_queryable_properties(root_node, root_properties)
        if filter_property and filter_property not in root_display_properties:
            root_display_properties = [filter_property] + root_display_properties
        steps = [{
            "step_id": "s1",
            "action": "select_root",
            "label": root_label,
            "filter": {"property": filter_property, "value": filter_value},
            "display_properties": list(dict.fromkeys(root_display_properties))[:MANAGED_SKILL_TEST_PLAN_MAX_PROPERTIES],
        }]
        selected_objects = [root_label]
        for index, target_label in enumerate(target_labels, start=2):
            path = self._find_graph_path(topology, root_label, target_label)
            if not path:
                continue
            target_node = nodes_by_label.get(target_label)
            selected_props = self._filter_queryable_properties(target_node or {}, target_properties.get(target_label))
            steps.append({
                "step_id": f"s{index}",
                "action": "expand_relations",
                "from_step": "s1",
                "label": target_label,
                "path": path,
                "display_properties": selected_props,
            })
            selected_objects.append(target_label)
        return {
            "steps": steps,
            "selected_objects": selected_objects,
        }

    def _append_managed_skill_plan_actions(
        self,
        *,
        plan_seed: Dict[str, Any],
        forced_actions: Optional[List[str]],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
        execution_contract: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        steps = list(plan_seed.get("steps") or [])
        additional_steps: List[Dict[str, Any]] = []

        def current_plan() -> Dict[str, Any]:
            return {
                "steps": steps + additional_steps,
                "selected_objects": plan_seed.get("selected_objects") or [],
            }

        def append_single(step: Optional[Dict[str, Any]]) -> None:
            if step:
                additional_steps.append(step)

        if forced_actions is None:
            metric_formula_steps = self._build_metric_formula_steps(
                plan=current_plan(),
                topology=topology,
                skill_files=skill_files,
                question=question,
                execution_contract=execution_contract,
            )
            object_time_window_step = None if metric_formula_steps else self._build_group_by_object_time_window_step(
                plan=current_plan(),
                topology=topology,
                skill_files=skill_files,
                question=question,
                execution_contract=execution_contract,
            )
            time_window_step = None if metric_formula_steps or object_time_window_step else self._build_group_by_time_window_step(
                plan=current_plan(),
                topology=topology,
                skill_files=skill_files,
                question=question,
                execution_contract=execution_contract,
            )
            group_by_object_step = None if metric_formula_steps or object_time_window_step or time_window_step else self._build_group_by_object_step(
                plan=current_plan(),
                topology=topology,
                skill_files=skill_files,
                question=question,
            )
            fact_aggregate_step = None if metric_formula_steps or object_time_window_step or time_window_step or group_by_object_step else self._build_fact_aggregate_step(
                plan=current_plan(),
                topology=topology,
                skill_files=skill_files,
                question=question,
            )
            additional_steps.extend(metric_formula_steps)
            for item in (object_time_window_step, time_window_step, group_by_object_step, fact_aggregate_step):
                append_single(item)
        else:
            for action in [item for item in forced_actions if item not in {"select_root", "expand_relations"}]:
                if action == "metric_formula":
                    additional_steps.extend(self._build_metric_formula_steps(
                        plan=current_plan(),
                        topology=topology,
                        skill_files=skill_files,
                        question=question,
                        execution_contract=execution_contract,
                    ))
                elif action == "group_by_object_time_window":
                    append_single(self._build_group_by_object_time_window_step(
                        plan=current_plan(),
                        topology=topology,
                        skill_files=skill_files,
                        question=question,
                        execution_contract=execution_contract,
                    ))
                elif action == "group_by_time_window":
                    append_single(self._build_group_by_time_window_step(
                        plan=current_plan(),
                        topology=topology,
                        skill_files=skill_files,
                        question=question,
                        execution_contract=execution_contract,
                    ))
                elif action == "group_by_object":
                    append_single(self._build_group_by_object_step(
                        plan=current_plan(),
                        topology=topology,
                        skill_files=skill_files,
                        question=question,
                    ))
                elif action == "fact_aggregate":
                    append_single(self._build_fact_aggregate_step(
                        plan=current_plan(),
                        topology=topology,
                        skill_files=skill_files,
                        question=question,
                    ))
                elif action == "filter_aggregate_result":
                    append_single(self._build_filter_aggregate_result_step(
                        plan=current_plan(),
                        question=question,
                    ))
                elif action == "order_and_limit":
                    append_single(self._build_order_and_limit_step(
                        plan=current_plan(),
                        question=question,
                    ))
                elif action == "apply_rules":
                    append_single(self._build_apply_rules_step(
                        plan=current_plan(),
                        skill_files=skill_files,
                        question=question,
                    ))
                elif action == "summarize_evidence":
                    append_single(self._build_summarize_evidence_step(
                        plan=current_plan(),
                    ))
        if forced_actions is None:
            filter_step = self._build_filter_aggregate_result_step(
                plan=current_plan(),
                question=question,
            )
            append_single(filter_step)
            order_step = self._build_order_and_limit_step(
                plan=current_plan(),
                question=question,
            )
            append_single(order_step)
            apply_rules_step = self._build_apply_rules_step(
                plan=current_plan(),
                skill_files=skill_files,
                question=question,
            )
            append_single(apply_rules_step)
            append_single(self._build_summarize_evidence_step(plan=current_plan()))
        return additional_steps

    def _build_plan_from_reference_pattern(
        self,
        *,
        pattern: Dict[str, Any],
        intent_type: str,
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
        conversation_context: str,
        contract: Dict[str, Any],
        excluded_roots: set[str],
        excluded_targets: set[str],
        preferred_root: str,
    ) -> Optional[Dict[str, Any]]:
        indexes = self._build_topology_indexes(topology)
        nodes_by_label = indexes["nodes_by_label"]
        root_label = self._choose_reference_root_label(
            pattern=pattern,
            contract=contract,
            nodes_by_label=nodes_by_label,
            excluded_roots=excluded_roots,
            preferred_root=preferred_root,
            question=question,
            topology=topology,
        )
        if root_label not in nodes_by_label:
            return None
        root_node = nodes_by_label[root_label]
        target_labels = self._choose_reference_target_labels(
            pattern=pattern,
            contract=contract,
            topology=topology,
            root_label=root_label,
            excluded_targets=excluded_targets,
            question=question,
        )
        known_text = f"{question}\n{conversation_context}".upper()
        filter_property = ""
        filter_value = ""
        root_properties = []
        target_properties: Dict[str, List[str]] = {}
        required_display_properties = contract.get("required_display_properties") if isinstance(contract.get("required_display_properties"), dict) else {}
        root_properties = list(required_display_properties.get(root_label) or [])
        root_properties_by_name = {
            str(prop.get("property_name") or "").upper(): prop
            for prop in (root_node.get("properties") or [])
        }
        identifier_match = re.search(r"\b(?:BOT|BATCH|CASE|PACK|PALLET|STACK|OUT|TRANS)-[A-Z0-9_.:/-]+\b", known_text)
        if identifier_match:
            filter_value = identifier_match.group(0)
        for candidate_name in ("BOTTLE_CODE", "BATCH_NO", "CASE_CODE", "PACK_CODE", "PALLET_CODE", "STACK_CODE", "OUTBOUND_NO"):
            if candidate_name in root_properties_by_name and candidate_name in known_text:
                filter_property = candidate_name
                break
        if not filter_property:
            filter_property = next(
                (
                    str(prop.get("property_name") or "").upper()
                    for prop in (root_node.get("properties") or [])
                    if prop.get("is_primary_key") == "Y"
                ),
                "",
            )
        if filter_property and not filter_value:
            fallback_match = re.search(r"\b[A-Z0-9][A-Z0-9_.:/-]{3,}\b", question.upper())
            if fallback_match:
                filter_value = fallback_match.group(0)
        for label in target_labels:
            target_node = nodes_by_label.get(label) or {}
            target_properties[label] = list(required_display_properties.get(label) or self._select_group_display_properties(target_node))
        plan_seed = self._build_seed_steps_for_managed_skill_plan(
            topology=topology,
            root_label=root_label,
            filter_property=filter_property,
            filter_value=filter_value,
            root_properties=root_properties,
            target_labels=target_labels,
            target_properties=target_properties,
        )
        additional_steps = self._append_managed_skill_plan_actions(
            plan_seed=plan_seed,
            forced_actions=pattern.get("plan_template") or [],
            topology=topology,
            skill_files=skill_files,
            question=question,
            execution_contract=contract,
        )
        return {
            "plan_version": "1.0",
            "intent_type": intent_type,
            "reason": f"命中受控参考模式 {pattern.get('pattern_id')}",
            "selected_objects": plan_seed.get("selected_objects") or [],
            "planning_mode": "REFERENCE_PATTERN",
            "reference_pattern_id": pattern.get("pattern_id"),
            "steps": (list(plan_seed.get("steps") or []) + additional_steps)[:MANAGED_SKILL_TEST_PLAN_MAX_STEPS],
        }

    @staticmethod
    def _load_metric_reference_items(skill_files: Dict[str, str]) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(skill_files.get("references/metric-catalog.json") or "{}")
        except (TypeError, ValueError):
            return []
        metrics = payload.get("metrics") if isinstance(payload, dict) else None
        return metrics if isinstance(metrics, list) else []

    @staticmethod
    def _question_requests_fact_aggregate(question: str) -> bool:
        normalized = str(question or "").upper()
        tokens = ("多少", "数量", "总数", "总量", "合计", "平均", "均值", "最大", "最小", "统计", "COUNT", "SUM", "AVG", "MIN", "MAX")
        return any(token in normalized for token in tokens)

    @staticmethod
    def _question_requests_group_by_object(question: str) -> bool:
        normalized = str(question or "").upper()
        if not normalized:
            return False
        aggregate_tokens = ("多少", "数量", "总数", "总量", "合计", "统计", "趋势", "率", "占比", "比例", "COUNT", "SUM", "AVG", "MIN", "MAX", "RATE", "RATIO")
        group_tokens = ("分别", "各", "每个", "按", "哪些", "哪几个", "TOP", "排名")
        if not any(token in normalized for token in aggregate_tokens):
            return False
        if any(token in normalized for token in ("分别", "各自")):
            return True
        if any(token in normalized for token in ("每个", "TOP", "排名")):
            return True
        if "按" in normalized and "统计" in normalized:
            return True
        return bool(re.search(r"哪些.{0,12}(多少|数量|统计)", normalized))

    @staticmethod
    def _question_requests_apply_rules(question: str) -> bool:
        normalized = str(question or "").upper()
        tokens = ("异常", "超规", "规则", "预警", "风险", "告警", "是否正常", "是否异常", "RULE", "ALERT", "RISK")
        return any(token in normalized for token in tokens)

    @staticmethod
    def _question_requests_metric_formula(question: str) -> bool:
        normalized = str(question or "").upper()
        tokens = ("率", "占比", "比例", "比率", "PERCENT", "RATIO", "RATE")
        return any(token in normalized for token in tokens)

    @staticmethod
    def _question_requests_group_by_time_window(question: str) -> bool:
        normalized = str(question or "").upper()
        time_tokens = ("趋势", "按天", "按月", "按周", "每天", "每月", "每周", "近", "最近", "DAY", "WEEK", "MONTH", "TREND")
        aggregate_tokens = ("多少", "数量", "统计", "趋势", "COUNT", "SUM", "AVG", "MIN", "MAX", "率", "占比")
        return any(token in normalized for token in time_tokens) and any(token in normalized for token in aggregate_tokens)

    def _question_requests_group_by_object_time_window(self, question: str) -> bool:
        return self._question_requests_group_by_object(question) and self._question_requests_group_by_time_window(question)

    @staticmethod
    def _question_requests_order_and_limit(question: str) -> bool:
        normalized = str(question or "").upper()
        return any(token in normalized for token in ("TOP", "前", "最多", "排名", "最高", "最低", "BOTTOM"))

    @staticmethod
    def _question_requests_filter_aggregate_result(question: str) -> bool:
        normalized = str(question or "").upper()
        return any(token in normalized for token in ("大于", "超过", "高于", "至少", "不少于", "小于", "低于", "不高于", ">=", "<=", ">", "<"))

    @staticmethod
    def _extract_limit_value(question: str) -> int:
        text = str(question or "").upper()
        patterns = [
            r"TOP\s*(\d+)",
            r"前\s*(\d+)",
            r"最多\s*(\d+)",
            r"BOTTOM\s*(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return max(1, min(int(match.group(1)), 100))
        return 10

    @staticmethod
    def _extract_numeric_threshold(question: str) -> Optional[Dict[str, Any]]:
        text = str(question or "").upper()
        patterns = [
            (r"(?:不少于|至少|不低于)\s*(-?\d+(?:\.\d+)?)", ">="),
            (r"(?:不高于|至多|最多)\s*(-?\d+(?:\.\d+)?)", "<="),
            (r"(?:大于|高于|超过)\s*(-?\d+(?:\.\d+)?)", ">"),
            (r"(?:小于|低于)\s*(-?\d+(?:\.\d+)?)", "<"),
            (r">=\s*(-?\d+(?:\.\d+)?)", ">="),
            (r"<=\s*(-?\d+(?:\.\d+)?)", "<="),
            (r">\s*(-?\d+(?:\.\d+)?)", ">"),
            (r"<\s*(-?\d+(?:\.\d+)?)", "<"),
        ]
        for pattern, operator in patterns:
            match = re.search(pattern, text)
            if match:
                raw = match.group(1)
                value = float(raw) if "." in raw else int(raw)
                return {"operator": operator, "threshold": value}
        return None

    @staticmethod
    def _load_rule_reference_items(skill_files: Dict[str, str]) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(skill_files.get("references/rule-catalog.json") or "{}")
        except (TypeError, ValueError):
            return []
        rules = payload.get("rules") if isinstance(payload, dict) else None
        return rules if isinstance(rules, list) else []

    @staticmethod
    def _metric_reference_index(skill_files: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        metrics = AgentService._load_metric_reference_items(skill_files)
        return {
            str(item.get("metric_code") or item.get("metric_id") or "").strip().upper(): item
            for item in metrics
            if str(item.get("metric_code") or item.get("metric_id") or "").strip()
        }

    def _time_dimension_priority_score(self, property_name: str) -> int:
        name = str(property_name or "").upper()
        keywords = ("TIME", "DATE", "DATETIME", "DAY", "MONTH")
        for index, keyword in enumerate(keywords):
            if keyword in name:
                return len(keywords) - index
        return 0

    def _time_dimension_candidates_for_label(
        self,
        *,
        label: str,
        node: Dict[str, Any],
        execution_contract: Optional[Dict[str, Any]],
    ) -> List[str]:
        contract_dimensions = ((execution_contract or {}).get("time_dimensions") or {}).get(label) or []
        contract_candidates = [
            str(item or "").upper()
            for item in contract_dimensions
            if str(item or "").upper()
        ]
        queryable = self._filter_queryable_properties(node)
        queryable_set = set(queryable)
        candidates = [item for item in contract_candidates if item in queryable_set]
        inferred = [
            item for item in queryable
            if self._time_dimension_priority_score(item) > 0
        ]
        inferred.sort(key=lambda item: (self._time_dimension_priority_score(item), -queryable.index(item)), reverse=True)
        for item in inferred:
            if item not in candidates:
                candidates.append(item)
        return candidates[:3]

    @staticmethod
    def _extract_time_window(question: str) -> str:
        normalized = str(question or "").upper()
        direct = re.search(r"(近|最近)\s*(\d+)\s*(天|日|周|月)", normalized)
        if direct:
            value = int(direct.group(2))
            unit = direct.group(3)
            if unit in {"天", "日"}:
                return f"{value}D"
            if unit == "周":
                return f"{value}W"
            if unit == "月":
                return f"{value}M"
        if "本月" in normalized:
            return "1M"
        if "本周" in normalized:
            return "1W"
        if "今日" in normalized or "今天" in normalized:
            return "1D"
        return ""

    @staticmethod
    def _extract_time_granularity(question: str) -> str:
        normalized = str(question or "").upper()
        if any(token in normalized for token in ("按月", "每月", "MONTH")):
            return "MONTH"
        if any(token in normalized for token in ("按周", "每周", "WEEK")):
            return "WEEK"
        if any(token in normalized for token in ("按天", "每天", "每日", "DAY", "趋势", "近")):
            return "DAY"
        return "DAY"

    def _graph_labels_for_metric(self, metric: Dict[str, Any], topology: Dict[str, Any]) -> List[str]:
        labels = []
        for value in (metric.get("entity_name"), metric.get("entity_display_name")):
            token = str(value or "").strip()
            if not token:
                continue
            labels.extend(self._graph_labels_for_entity(topology, token))
        return list(dict.fromkeys(label for label in labels if label))

    @staticmethod
    def _first_queryable_primary_key(node: Dict[str, Any]) -> str:
        for prop in node.get("properties") or []:
            name = str(prop.get("property_name") or "").upper()
            if prop.get("is_primary_key") == "Y" and re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", name):
                return name
        return ""

    def _default_metric_column(self, node: Dict[str, Any]) -> str:
        primary_key = self._first_queryable_primary_key(node)
        if primary_key:
            return primary_key
        queryable = self._filter_queryable_properties(node)
        return queryable[0] if queryable else ""

    @staticmethod
    def _property_priority_score(property_name: str) -> int:
        name = str(property_name or "").upper()
        if not name:
            return 0
        keywords = (
            "NAME",
            "CODE",
            "NO",
            "TYPE",
            "STATUS",
            "RESULT",
            "CATEGORY",
            "LEVEL",
            "DATE",
            "TIME",
        )
        for index, keyword in enumerate(keywords):
            if keyword in name:
                return len(keywords) - index
        return 0

    def _select_group_display_properties(
        self,
        node: Dict[str, Any],
        requested: Optional[List[str]] = None,
        max_count: int = 3,
    ) -> List[str]:
        queryable = self._filter_queryable_properties(node, requested)
        if not queryable:
            return []
        primary_key = self._first_queryable_primary_key(node)
        if requested:
            requested_order = [name for name in queryable if name != primary_key]
            preferred_requested = [
                name for name in requested_order
                if self._property_priority_score(name) > 0
            ]
            if preferred_requested:
                return list(dict.fromkeys(preferred_requested))[:max_count]
            if requested_order:
                return list(dict.fromkeys(requested_order))[:max_count]
        preferred = [
            name for name in queryable
            if name != primary_key and self._property_priority_score(name) > 0
        ]
        preferred.sort(key=lambda item: (self._property_priority_score(item), -queryable.index(item)), reverse=True)
        selected = preferred[:max_count]
        if not selected:
            selected = [name for name in queryable if name != primary_key][:max_count]
        if not selected and primary_key:
            selected = [primary_key]
        return list(dict.fromkeys(selected))[:max_count]

    def _score_group_step_candidate(
        self,
        *,
        question: str,
        label: str,
        display_properties: List[str],
    ) -> int:
        score = 30
        upper_question = str(question or "").upper()
        normalized_question = self._normalize_lookup_token(question)
        for text in [label] + list(display_properties):
            token = str(text or "").strip()
            if not token:
                continue
            if token.upper() in upper_question:
                score += 25
            normalized_token = self._normalize_lookup_token(token)
            if normalized_token and normalized_token in normalized_question:
                score += 15
        return score

    def _score_fact_metric_candidate(
        self,
        *,
        metric: Dict[str, Any],
        question: str,
        label: str,
        step: Dict[str, Any],
        step_index: int,
        total_steps: int,
    ) -> int:
        score = 0
        question_text = str(question or "")
        upper_question = question_text.upper()
        normalized_question = self._normalize_lookup_token(question_text)
        if str(step.get("action") or "") == "expand_relations":
            score += 20
        score += max(0, total_steps - step_index)
        for text in (
            metric.get("metric_name"),
            metric.get("metric_code"),
            metric.get("metric_desc"),
            metric.get("entity_name"),
            metric.get("entity_display_name"),
            label,
        ):
            token = str(text or "").strip()
            if not token:
                continue
            if token.upper() in upper_question:
                score += 30
            normalized_token = self._normalize_lookup_token(token)
            if normalized_token and normalized_token in normalized_question:
                score += 20
        return score

    def _collect_metric_step_candidates(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
    ) -> List[Dict[str, Any]]:
        steps = plan.get("steps") or []
        metrics = self._load_metric_reference_items(skill_files)
        nodes_by_label = self._build_topology_indexes(topology)["nodes_by_label"]
        applicable = []
        for step_index, step in enumerate(steps, start=1):
            label = self._normalize_label_name(step.get("label"))
            node = nodes_by_label.get(label)
            if not node:
                continue
            for metric in metrics:
                labels = self._graph_labels_for_metric(metric, topology)
                if label not in labels:
                    continue
                method = str(metric.get("aggregation_method") or "").strip().upper()
                if method not in {"COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"}:
                    continue
                expression = self._sanitize_metric_expression(metric, node)
                if method in {"SUM", "AVG", "MIN", "MAX"} and not expression:
                    continue
                if method == "COUNT_DISTINCT" and not expression:
                    expression = self._default_metric_column(node) or "*"
                applicable.append({
                    **metric,
                    "_graph_label": label,
                    "_expression": expression,
                    "_step_id": step.get("step_id"),
                    "_step_action": step.get("action"),
                    "_path": step.get("path") or [],
                    "_display_properties": step.get("display_properties") or [],
                    "_score": self._score_fact_metric_candidate(
                        metric=metric,
                        question=question,
                        label=label,
                        step=step,
                        step_index=step_index,
                        total_steps=len(steps),
                    ),
                })
        return applicable

    @staticmethod
    def _sanitize_metric_expression(metric: Dict[str, Any], node: Dict[str, Any]) -> Optional[str]:
        property_names = {
            str(prop.get("property_name") or "").upper()
            for prop in (node.get("properties") or [])
        }
        expr = str(metric.get("calculation_expr") or "").strip().upper()
        if not expr:
            return None
        if re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", expr) and expr in property_names:
            return expr
        if re.fullmatch(r"COUNT\s*\(\s*\*\s*\)", expr):
            return "*"
        return None

    def _build_fact_aggregate_step(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._question_requests_fact_aggregate(question):
            return None
        steps = plan.get("steps") or []
        if not steps:
            return None
        root_step = steps[0]
        root_label = self._normalize_label_name(root_step.get("label"))
        applicable = self._collect_metric_step_candidates(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question=question,
        )
        if not applicable:
            return None
        applicable.sort(key=lambda item: item.get("_score") or 0, reverse=True)
        metric = applicable[0]
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "fact_aggregate",
            "label": metric.get("_graph_label") or root_label,
            "root_label": root_label,
            "scope": "related_object" if metric.get("_step_action") == "expand_relations" else "root_object",
            "based_on_step": metric.get("_step_id") or root_step.get("step_id"),
            "path": metric.get("_path") or [],
            "metric_code": metric.get("metric_code") or metric.get("metric_id"),
            "metric_name": metric.get("metric_name") or metric.get("metric_code"),
            "aggregation_method": str(metric.get("aggregation_method") or "").strip().upper(),
            "column_name": metric.get("_expression"),
            "unit": metric.get("unit") or "",
        }

    def _build_group_by_object_step(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._question_requests_group_by_object(question):
            return None
        steps = plan.get("steps") or []
        if len(steps) < 2:
            return None
        root_step = steps[0]
        root_label = self._normalize_label_name(root_step.get("label"))
        nodes_by_label = self._build_topology_indexes(topology)["nodes_by_label"]
        metric_candidates = [
            item for item in self._collect_metric_step_candidates(
                plan=plan,
                topology=topology,
                skill_files=skill_files,
                question=question,
            )
            if item.get("_step_action") == "expand_relations"
        ]
        metric_candidates.sort(key=lambda item: item.get("_score") or 0, reverse=True)
        if metric_candidates:
            metric = metric_candidates[0]
            target_label = self._normalize_label_name(metric.get("_graph_label"))
            target_node = nodes_by_label.get(target_label)
            group_properties = self._select_group_display_properties(
                target_node or {},
                metric.get("_display_properties") or [],
            )
            if not target_node or not group_properties:
                return None
            return {
                "step_id": f"s{len(steps) + 1}",
                "action": "group_by_object",
                "label": target_label,
                "root_label": root_label,
                "scope": "related_object",
                "based_on_step": metric.get("_step_id"),
                "path": metric.get("_path") or [],
                "group_properties": group_properties,
                "metric_code": metric.get("metric_code") or metric.get("metric_id"),
                "metric_name": metric.get("metric_name") or metric.get("metric_code"),
                "aggregation_method": str(metric.get("aggregation_method") or "").strip().upper(),
                "column_name": metric.get("_expression"),
                "unit": metric.get("unit") or "",
            }

        synthetic_candidates = []
        for step in steps[1:]:
            if str(step.get("action") or "") != "expand_relations":
                continue
            label = self._normalize_label_name(step.get("label"))
            node = nodes_by_label.get(label)
            if not node:
                continue
            group_properties = self._select_group_display_properties(node, step.get("display_properties") or [])
            if not group_properties:
                continue
            synthetic_candidates.append({
                "step": step,
                "label": label,
                "group_properties": group_properties,
                "score": self._score_group_step_candidate(
                    question=question,
                    label=label,
                    display_properties=group_properties,
                ),
            })
        if not synthetic_candidates:
            return None
        synthetic_candidates.sort(key=lambda item: item.get("score") or 0, reverse=True)
        selected = synthetic_candidates[0]
        step = selected["step"]
        target_node = nodes_by_label.get(selected["label"]) or {}
        fallback_column = self._default_metric_column(target_node)
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "group_by_object",
            "label": selected["label"],
            "root_label": root_label,
            "scope": "related_object",
            "based_on_step": step.get("step_id"),
            "path": step.get("path") or [],
            "group_properties": selected["group_properties"],
            "metric_code": f"{selected['label']}_COUNT",
            "metric_name": f"{selected['label']}分组数量",
            "aggregation_method": "COUNT_DISTINCT" if fallback_column else "COUNT",
            "column_name": fallback_column,
            "unit": "",
        }

    def _build_metric_aggregate_step_from_definition(
        self,
        *,
        metric: Dict[str, Any],
        label: str,
        root_label: str,
        base_step: Dict[str, Any],
        node: Dict[str, Any],
        step_id: str,
    ) -> Optional[Dict[str, Any]]:
        method = str(metric.get("aggregation_method") or "").strip().upper()
        if method not in {"COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"}:
            return None
        expression = self._sanitize_metric_expression(metric, node)
        if method in {"SUM", "AVG", "MIN", "MAX"} and not expression:
            return None
        if method == "COUNT_DISTINCT" and not expression:
            expression = self._default_metric_column(node) or "*"
        return {
            "step_id": step_id,
            "action": "fact_aggregate",
            "label": label,
            "root_label": root_label,
            "scope": "related_object" if str(base_step.get("action") or "") == "expand_relations" else "root_object",
            "based_on_step": base_step.get("step_id"),
            "path": base_step.get("path") or [],
            "metric_code": metric.get("metric_code") or metric.get("metric_id"),
            "metric_name": metric.get("metric_name") or metric.get("metric_code"),
            "aggregation_method": method,
            "column_name": expression,
            "unit": metric.get("unit") or "",
        }

    def _build_group_by_time_window_step(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
        execution_contract: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not self._question_requests_group_by_time_window(question):
            return None
        steps = plan.get("steps") or []
        if not steps:
            return None
        root_step = steps[0]
        root_label = self._normalize_label_name(root_step.get("label"))
        nodes_by_label = self._build_topology_indexes(topology)["nodes_by_label"]
        metric_candidates = self._collect_metric_step_candidates(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question=question,
        )
        metric_candidates.sort(key=lambda item: item.get("_score") or 0, reverse=True)
        selected_metric = metric_candidates[0] if metric_candidates else None
        selected_step = None
        selected_label = ""
        selected_node = None
        if selected_metric:
            selected_label = self._normalize_label_name(selected_metric.get("_graph_label"))
            selected_node = nodes_by_label.get(selected_label)
            selected_step = next((item for item in steps if item.get("step_id") == selected_metric.get("_step_id")), root_step)
        else:
            selected_step = next((item for item in reversed(steps) if str(item.get("action") or "") in {"expand_relations", "select_root"}), root_step)
            selected_label = self._normalize_label_name(selected_step.get("label"))
            selected_node = nodes_by_label.get(selected_label)
        if not selected_node or not selected_step:
            return None
        time_dimensions = self._time_dimension_candidates_for_label(
            label=selected_label,
            node=selected_node,
            execution_contract=execution_contract,
        )
        if not time_dimensions:
            return None
        metric_code = ""
        metric_name = ""
        aggregation_method = "COUNT"
        column_name = ""
        unit = ""
        if selected_metric:
            metric_code = selected_metric.get("metric_code") or selected_metric.get("metric_id")
            metric_name = selected_metric.get("metric_name") or metric_code
            aggregation_method = str(selected_metric.get("aggregation_method") or "").strip().upper() or "COUNT"
            column_name = selected_metric.get("_expression")
            unit = selected_metric.get("unit") or ""
        else:
            column_name = self._default_metric_column(selected_node)
            aggregation_method = "COUNT_DISTINCT" if column_name else "COUNT"
            metric_code = f"{selected_label}_TREND"
            metric_name = f"{selected_label}趋势统计"
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "group_by_time_window",
            "label": selected_label,
            "root_label": root_label,
            "scope": "related_object" if str(selected_step.get("action") or "") == "expand_relations" else "root_object",
            "based_on_step": selected_step.get("step_id"),
            "path": selected_step.get("path") or [],
            "metric_code": metric_code,
            "metric_name": metric_name,
            "aggregation_method": aggregation_method,
            "column_name": column_name,
            "unit": unit,
            "time_dimension": time_dimensions[0],
            "time_granularity": self._extract_time_granularity(question),
            "time_window": self._extract_time_window(question),
        }

    def _build_group_by_object_time_window_step(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
        execution_contract: Optional[Dict[str, Any]],
        metric_override: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not metric_override and not self._question_requests_group_by_object_time_window(question):
            return None
        steps = plan.get("steps") or []
        if len(steps) < 1:
            return None
        root_step = steps[0]
        root_label = self._normalize_label_name(root_step.get("label"))
        nodes_by_label = self._build_topology_indexes(topology)["nodes_by_label"]

        selected_metric = metric_override
        selected_step = None
        selected_label = ""
        selected_node = None

        if selected_metric:
            metric_step_id = str(selected_metric.get("_step_id") or "")
            selected_step = next((item for item in steps if str(item.get("step_id") or "") == metric_step_id), None)
            selected_label = self._normalize_label_name(selected_metric.get("_graph_label"))
            selected_node = nodes_by_label.get(selected_label)
        else:
            metric_candidates = [
                item for item in self._collect_metric_step_candidates(
                    plan=plan,
                    topology=topology,
                    skill_files=skill_files,
                    question=question,
                )
                if item.get("_step_action") == "expand_relations"
            ]
            metric_candidates.sort(key=lambda item: item.get("_score") or 0, reverse=True)
            if metric_candidates:
                selected_metric = metric_candidates[0]
                selected_step = next((item for item in steps if item.get("step_id") == selected_metric.get("_step_id")), None)
                selected_label = self._normalize_label_name(selected_metric.get("_graph_label"))
                selected_node = nodes_by_label.get(selected_label)
            else:
                selected_step = next((item for item in steps[1:] if str(item.get("action") or "") == "expand_relations"), None)
                if selected_step:
                    selected_label = self._normalize_label_name(selected_step.get("label"))
                    selected_node = nodes_by_label.get(selected_label)
        if not selected_step or not selected_node or not selected_label:
            return None
        time_dimensions = self._time_dimension_candidates_for_label(
            label=selected_label,
            node=selected_node,
            execution_contract=execution_contract,
        )
        group_properties = self._select_group_display_properties(
            selected_node,
            selected_step.get("display_properties") or [],
        )
        group_properties = [
            item for item in group_properties
            if item not in set(time_dimensions)
        ]
        if not group_properties:
            group_properties = [
                item for item in self._select_group_display_properties(selected_node)
                if item not in set(time_dimensions)
            ]
        if not time_dimensions or not group_properties:
            return None

        metric_code = ""
        metric_name = ""
        aggregation_method = "COUNT"
        column_name = ""
        unit = ""
        if selected_metric:
            metric_code = selected_metric.get("metric_code") or selected_metric.get("metric_id")
            metric_name = selected_metric.get("metric_name") or metric_code
            aggregation_method = str(selected_metric.get("aggregation_method") or "").strip().upper() or "COUNT"
            column_name = selected_metric.get("_expression")
            unit = selected_metric.get("unit") or ""
        else:
            column_name = self._default_metric_column(selected_node)
            aggregation_method = "COUNT_DISTINCT" if column_name else "COUNT"
            metric_code = f"{selected_label}_OBJECT_TIME_COUNT"
            metric_name = f"{selected_label}对象时间趋势统计"
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "group_by_object_time_window",
            "label": selected_label,
            "root_label": root_label,
            "scope": "related_object" if str(selected_step.get("action") or "") == "expand_relations" else "root_object",
            "based_on_step": selected_step.get("step_id"),
            "path": selected_step.get("path") or [],
            "group_properties": group_properties,
            "metric_code": metric_code,
            "metric_name": metric_name,
            "aggregation_method": aggregation_method,
            "column_name": column_name,
            "unit": unit,
            "time_dimension": time_dimensions[0],
            "time_granularity": self._extract_time_granularity(question),
            "time_window": self._extract_time_window(question),
        }

    def _build_metric_formula_steps(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
        execution_contract: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not self._question_requests_metric_formula(question):
            return []
        steps = plan.get("steps") or []
        if not steps:
            return []
        metric_index = self._metric_reference_index(skill_files)
        if not metric_index:
            return []
        root_step = steps[0]
        root_label = self._normalize_label_name(root_step.get("label"))
        nodes_by_label = self._build_topology_indexes(topology)["nodes_by_label"]
        formula_metrics = []
        normalized_question = self._normalize_lookup_token(question)
        for metric in metric_index.values():
            formula_type = str(metric.get("formula_type") or metric.get("aggregation_method") or "").strip().upper()
            if formula_type not in {"RATIO", "RATE", "PERCENT"}:
                continue
            numerator_code = str(metric.get("numerator_metric_code") or "").strip().upper()
            denominator_code = str(metric.get("denominator_metric_code") or "").strip().upper()
            if not numerator_code or not denominator_code:
                continue
            name_tokens = [
                self._normalize_lookup_token(metric.get("metric_name")),
                self._normalize_lookup_token(metric.get("metric_code")),
            ]
            score = 0
            for token in name_tokens:
                if token and token in normalized_question:
                    score += 40
            formula_metrics.append((score, metric))
        if not formula_metrics:
            return []
        formula_metrics.sort(key=lambda item: item[0], reverse=True)
        formula_metric = formula_metrics[0][1]
        candidate_steps = [
            step for step in steps
            if str(step.get("action") or "") in {"select_root", "expand_relations"}
        ]
        selected_base_step = None
        selected_label = ""
        selected_node = None
        for base_step in candidate_steps:
            label = self._normalize_label_name(base_step.get("label"))
            node = nodes_by_label.get(label)
            if not node:
                continue
            labels = self._graph_labels_for_metric(formula_metric, topology)
            if labels and label not in labels:
                continue
            selected_base_step = base_step
            selected_label = label
            selected_node = node
            break
        if not selected_base_step or not selected_node:
            return []
        numerator_metric = metric_index.get(str(formula_metric.get("numerator_metric_code") or "").strip().upper())
        denominator_metric = metric_index.get(str(formula_metric.get("denominator_metric_code") or "").strip().upper())
        if not numerator_metric or not denominator_metric:
            return []
        object_time_mode = self._question_requests_group_by_object_time_window(question)
        time_mode = self._question_requests_group_by_time_window(question)
        time_dimension = self._time_dimension_candidates_for_label(
            label=selected_label,
            node=selected_node,
            execution_contract=execution_contract,
        )
        if time_mode and not time_dimension:
            time_mode = False
            object_time_mode = False
        next_step_no = len(steps) + 1
        if object_time_mode:
            numerator_step = self._build_group_by_object_time_window_step(
                plan=plan,
                topology=topology,
                skill_files=skill_files,
                question=question,
                execution_contract=execution_contract,
                metric_override={
                    **numerator_metric,
                    "_graph_label": selected_label,
                    "_step_id": selected_base_step.get("step_id"),
                    "_step_action": selected_base_step.get("action"),
                    "_path": selected_base_step.get("path") or [],
                    "_expression": self._sanitize_metric_expression(numerator_metric, selected_node) or self._default_metric_column(selected_node),
                },
            )
            denominator_step = self._build_group_by_object_time_window_step(
                plan=plan,
                topology=topology,
                skill_files=skill_files,
                question=question,
                execution_contract=execution_contract,
                metric_override={
                    **denominator_metric,
                    "_graph_label": selected_label,
                    "_step_id": selected_base_step.get("step_id"),
                    "_step_action": selected_base_step.get("action"),
                    "_path": selected_base_step.get("path") or [],
                    "_expression": self._sanitize_metric_expression(denominator_metric, selected_node) or self._default_metric_column(selected_node),
                },
            )
            if numerator_step:
                numerator_step["step_id"] = f"s{next_step_no}"
            if denominator_step:
                denominator_step["step_id"] = f"s{next_step_no + 1}"
        elif time_mode:
            numerator_step = {
                **(self._build_group_by_time_window_step(
                    plan=plan,
                    topology=topology,
                    skill_files=skill_files,
                    question=question,
                    execution_contract=execution_contract,
                ) or {}),
                "step_id": f"s{next_step_no}",
                "label": selected_label,
                "root_label": root_label,
                "scope": "related_object" if str(selected_base_step.get("action") or "") == "expand_relations" else "root_object",
                "based_on_step": selected_base_step.get("step_id"),
                "path": selected_base_step.get("path") or [],
                "metric_code": numerator_metric.get("metric_code") or numerator_metric.get("metric_id"),
                "metric_name": numerator_metric.get("metric_name") or numerator_metric.get("metric_code"),
                "aggregation_method": str(numerator_metric.get("aggregation_method") or "").strip().upper(),
                "column_name": self._sanitize_metric_expression(numerator_metric, selected_node) or self._default_metric_column(selected_node),
                "unit": numerator_metric.get("unit") or "",
                "time_dimension": time_dimension[0],
                "time_granularity": self._extract_time_granularity(question),
                "time_window": self._extract_time_window(question),
            }
            denominator_step = {
                **numerator_step,
                "step_id": f"s{next_step_no + 1}",
                "metric_code": denominator_metric.get("metric_code") or denominator_metric.get("metric_id"),
                "metric_name": denominator_metric.get("metric_name") or denominator_metric.get("metric_code"),
                "aggregation_method": str(denominator_metric.get("aggregation_method") or "").strip().upper(),
                "column_name": self._sanitize_metric_expression(denominator_metric, selected_node) or self._default_metric_column(selected_node),
                "unit": denominator_metric.get("unit") or "",
            }
        else:
            numerator_step = self._build_metric_aggregate_step_from_definition(
                metric=numerator_metric,
                label=selected_label,
                root_label=root_label,
                base_step=selected_base_step,
                node=selected_node,
                step_id=f"s{next_step_no}",
            )
            denominator_step = self._build_metric_aggregate_step_from_definition(
                metric=denominator_metric,
                label=selected_label,
                root_label=root_label,
                base_step=selected_base_step,
                node=selected_node,
                step_id=f"s{next_step_no + 1}",
            )
        if not numerator_step or not denominator_step:
            return []
        formula_step = {
            "step_id": f"s{next_step_no + 2}",
            "action": "metric_formula",
            "label": selected_label,
            "root_label": root_label,
            "metric_code": formula_metric.get("metric_code") or formula_metric.get("metric_id"),
            "metric_name": formula_metric.get("metric_name") or formula_metric.get("metric_code"),
            "formula_type": str(formula_metric.get("formula_type") or formula_metric.get("aggregation_method") or "RATIO").strip().upper(),
            "numerator_metric_code": numerator_step.get("metric_code"),
            "denominator_metric_code": denominator_step.get("metric_code"),
            "numerator_step_id": numerator_step.get("step_id"),
            "denominator_step_id": denominator_step.get("step_id"),
            "time_granularity": numerator_step.get("time_granularity", ""),
            "group_properties": numerator_step.get("group_properties") or [],
            "unit": formula_metric.get("unit") or "%",
        }
        return [numerator_step, denominator_step, formula_step]

    def _build_filter_aggregate_result_step(
        self,
        *,
        plan: Dict[str, Any],
        question: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._question_requests_filter_aggregate_result(question):
            return None
        threshold = self._extract_numeric_threshold(question)
        if not threshold:
            return None
        steps = plan.get("steps") or []
        based_on = next(
            (
                item for item in reversed(steps)
                if str(item.get("action") or "") in {"group_by_object", "group_by_object_time_window", "fact_aggregate", "group_by_time_window", "metric_formula"}
            ),
            None,
        )
        if not based_on:
            return None
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "filter_aggregate_result",
            "label": self._normalize_label_name(based_on.get("label")),
            "based_on_step": based_on.get("step_id"),
            "metric_code": based_on.get("metric_code"),
            "metric_name": based_on.get("metric_name"),
            "operator": threshold["operator"],
            "threshold": threshold["threshold"],
        }

    def _build_order_and_limit_step(
        self,
        *,
        plan: Dict[str, Any],
        question: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._question_requests_order_and_limit(question):
            return None
        steps = plan.get("steps") or []
        based_on = next(
            (
                item for item in reversed(steps)
                if str(item.get("action") or "") in {"group_by_object", "group_by_object_time_window", "group_by_time_window", "metric_formula", "filter_aggregate_result"}
            ),
            None,
        )
        if not based_on:
            return None
        normalized = str(question or "").upper()
        direction = "ASC" if any(token in normalized for token in ("最低", "最少", "BOTTOM")) else "DESC"
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "order_and_limit",
            "label": self._normalize_label_name(based_on.get("label")),
            "based_on_step": based_on.get("step_id"),
            "metric_code": based_on.get("metric_code"),
            "metric_name": based_on.get("metric_name"),
            "order_by": "METRIC_VALUE",
            "direction": direction,
            "limit": self._extract_limit_value(question),
        }

    def _build_apply_rules_step(
        self,
        *,
        plan: Dict[str, Any],
        skill_files: Dict[str, str],
        question: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._load_rule_reference_items(skill_files):
            return None
        if not self._question_requests_apply_rules(question):
            return None
        steps = plan.get("steps") or []
        based_on = next(
            (
                item for item in reversed(steps)
                if str(item.get("action") or "") in {"group_by_object", "group_by_object_time_window", "fact_aggregate", "group_by_time_window", "metric_formula", "filter_aggregate_result", "order_and_limit"}
            ),
            None,
        )
        if not based_on:
            return None
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "apply_rules",
            "label": self._normalize_label_name(based_on.get("label")),
            "based_on_step": based_on.get("step_id"),
            "metric_code": based_on.get("metric_code"),
        }

    def _build_summarize_evidence_step(
        self,
        *,
        plan: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        steps = plan.get("steps") or []
        if not steps:
            return None
        if any(str(step.get("action") or "") == "summarize_evidence" for step in steps):
            return None
        return {
            "step_id": f"s{len(steps) + 1}",
            "action": "summarize_evidence",
            "label": self._normalize_label_name((steps[-1] or {}).get("label")),
            "based_on_step": (steps[-1] or {}).get("step_id"),
        }

    @staticmethod
    def _build_fact_aggregate_expression(
        aggregation_method: str,
        column_name: str,
        *,
        fallback_column: str = "*",
    ) -> str:
        method = str(aggregation_method or "").upper()
        column = str(column_name or "").upper()
        fallback = str(fallback_column or "*").upper()
        if method == "COUNT":
            return "COUNT(*)"
        if method == "COUNT_DISTINCT":
            target = column or fallback or "*"
            if target == "*":
                return "COUNT(*)"
            if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", target):
                raise ValueError("事实聚合步骤缺少合法的去重列")
            return f"COUNT(DISTINCT {target})"
        if not column or not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", column):
            raise ValueError("事实聚合步骤缺少合法的指标列")
        return f"{method}({column})"

    def _build_fact_aggregate_sql(
        self,
        *,
        metric_step: Dict[str, Any],
        root_node: Dict[str, Any],
        graph_name: str = "",
        root_step: Optional[Dict[str, Any]] = None,
        based_on_step: Optional[Dict[str, Any]] = None,
        target_node: Optional[Dict[str, Any]] = None,
        filter_property: str,
        filter_value: str,
    ) -> str:
        aggregation_method = str(metric_step.get("aggregation_method") or "").upper()
        metric_code = str(metric_step.get("metric_code") or "METRIC_VALUE").upper()
        column_name = str(metric_step.get("column_name") or "").upper()
        based_on = based_on_step or root_step or {}
        if str(based_on.get("action") or "") == "expand_relations":
            if not graph_name:
                raise ValueError("目标对象聚合缺少属性图名称")
            related_node = target_node or root_node
            path = based_on.get("path") or metric_step.get("path") or []
            if not path:
                raise ValueError("目标对象聚合缺少图路径")
            root_label = self._normalize_label_name(root_node.get("displayName") or root_node.get("name"))
            target_label = self._normalize_label_name(related_node.get("displayName") or related_node.get("name"))
            filter_name = self._normalize_label_name(filter_property)
            match_parts = [f"(r IS {root_label})"]
            current_label = root_label
            for hop_index, hop in enumerate(path, start=1):
                next_label = self._normalize_label_name(hop.get("target"))
                direction = hop.get("direction")
                edge_label = self._normalize_label_name(hop.get("edge"))
                alias = "t" if hop_index == len(path) else f"n{hop_index}"
                relation = f"-[e{hop_index} IS {edge_label}]->" if direction == "OUT" else f"<-[e{hop_index} IS {edge_label}]-"
                match_parts.append(f"{relation}({alias} IS {next_label})")
                current_label = next_label
            if current_label != target_label:
                raise ValueError("目标对象聚合路径终点与聚合对象不一致")
            aggregate_alias = f"AGG_{column_name}" if column_name and column_name != "*" else ""
            projections = []
            if filter_name:
                projections.append(f"r.{filter_name} AS ROOT_{filter_name}")
            if aggregate_alias:
                projections.append(f"t.{column_name} AS {aggregate_alias}")
            elif not projections:
                anchor_column = self._default_metric_column(related_node)
                if not anchor_column:
                    raise ValueError("目标对象聚合没有可用的锚点属性")
                projections.append(f"t.{anchor_column} AS ROW_ANCHOR")
            expression = self._build_fact_aggregate_expression(
                aggregation_method,
                aggregate_alias,
                fallback_column=aggregate_alias or "*",
            )
            sql = f"""WITH related_rows AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name.upper()}
    MATCH {''.join(match_parts)}
    COLUMNS (
      {', '.join(projections)}
    )
  )
)
SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE, {expression} AS METRIC_VALUE
FROM related_rows"""
            if filter_name and filter_value:
                sql += f"\nWHERE ROOT_{filter_name} = '{self._safe_graph_sql_literal(filter_value)}'"
            return sql
        table_name = SourceDataService._safe_graph_object_name(root_node.get("tableName") or "")
        fallback_column = column_name or self._default_metric_column(root_node) or filter_property or "*"
        expression = self._build_fact_aggregate_expression(
            aggregation_method,
            column_name,
            fallback_column=fallback_column,
        )
        where_sql = ""
        if filter_property and filter_value:
            where_sql = f" WHERE {filter_property} = '{self._safe_graph_sql_literal(filter_value)}'"
        return f"SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE, {expression} AS METRIC_VALUE FROM {table_name}{where_sql}"

    def _build_group_by_object_sql(
        self,
        *,
        group_step: Dict[str, Any],
        root_node: Dict[str, Any],
        graph_name: str,
        based_on_step: Dict[str, Any],
        target_node: Dict[str, Any],
        filter_property: str,
        filter_value: str,
    ) -> str:
        if str(based_on_step.get("action") or "") != "expand_relations":
            raise ValueError("对象分组统计必须基于关系扩展步骤")
        if not graph_name:
            raise ValueError("对象分组统计缺少属性图名称")
        path = based_on_step.get("path") or group_step.get("path") or []
        if not path:
            raise ValueError("对象分组统计缺少图路径")
        root_label = self._normalize_label_name(root_node.get("displayName") or root_node.get("name"))
        target_label = self._normalize_label_name(target_node.get("displayName") or target_node.get("name"))
        filter_name = self._normalize_label_name(filter_property)
        group_properties = self._select_group_display_properties(
            target_node,
            group_step.get("group_properties") or [],
        )
        if not group_properties:
            raise ValueError("对象分组统计缺少可用分组字段")
        column_name = str(group_step.get("column_name") or "").upper()
        aggregate_alias = f"AGG_{column_name}" if column_name and column_name != "*" else ""
        match_parts = [f"(r IS {root_label})"]
        current_label = root_label
        for hop_index, hop in enumerate(path, start=1):
            next_label = self._normalize_label_name(hop.get("target"))
            direction = hop.get("direction")
            edge_label = self._normalize_label_name(hop.get("edge"))
            alias = "t" if hop_index == len(path) else f"n{hop_index}"
            relation = f"-[e{hop_index} IS {edge_label}]->" if direction == "OUT" else f"<-[e{hop_index} IS {edge_label}]-"
            match_parts.append(f"{relation}({alias} IS {next_label})")
            current_label = next_label
        if current_label != target_label:
            raise ValueError("对象分组统计路径终点与目标对象不一致")
        projections = []
        if filter_name:
            projections.append(f"r.{filter_name} AS ROOT_{filter_name}")
        outer_group_columns = []
        for property_name in group_properties:
            alias = f"GROUP_{property_name}"
            projections.append(f"t.{property_name} AS {alias}")
            outer_group_columns.append((alias, property_name))
        if aggregate_alias:
            projections.append(f"t.{column_name} AS {aggregate_alias}")
        elif not projections:
            anchor_column = self._default_metric_column(target_node)
            if anchor_column:
                projections.append(f"t.{anchor_column} AS ROW_ANCHOR")
        expression = self._build_fact_aggregate_expression(
            str(group_step.get("aggregation_method") or ""),
            aggregate_alias,
            fallback_column=aggregate_alias or column_name or self._default_metric_column(target_node) or "*",
        )
        group_projection_sql = ", ".join(f"{alias} AS {property_name}" for alias, property_name in outer_group_columns)
        group_by_sql = ", ".join(alias for alias, _property_name in outer_group_columns)
        sql = f"""WITH related_rows AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name.upper()}
    MATCH {''.join(match_parts)}
    COLUMNS (
      {', '.join(projections)}
    )
  )
)
SELECT '{self._safe_graph_sql_literal(str(group_step.get("metric_code") or "").upper())}' AS METRIC_CODE,
       {group_projection_sql},
       {expression} AS METRIC_VALUE
FROM related_rows"""
        if filter_name and filter_value:
            sql += f"\nWHERE ROOT_{filter_name} = '{self._safe_graph_sql_literal(filter_value)}'"
        sql += f"\nGROUP BY {group_by_sql}\nORDER BY METRIC_VALUE DESC"
        return sql

    def _build_filter_aggregate_result_sql(
        self,
        *,
        base_sql: str,
        filter_step: Dict[str, Any],
    ) -> str:
        operator = str(filter_step.get("operator") or "").strip()
        if operator not in {">", ">=", "<", "<=", "=", "=="}:
            raise ValueError("统计结果筛选步骤缺少合法的比较运算符")
        threshold = filter_step.get("threshold")
        if not isinstance(threshold, (int, float)):
            raise ValueError("统计结果筛选步骤缺少合法阈值")
        normalized_operator = "=" if operator == "==" else operator
        return (
            "SELECT * FROM (\n"
            f"{base_sql}\n"
            f") WHERE METRIC_VALUE {normalized_operator} {threshold}"
        )

    def _build_order_and_limit_sql(
        self,
        *,
        base_sql: str,
        order_step: Dict[str, Any],
    ) -> str:
        direction = str(order_step.get("direction") or "DESC").upper()
        if direction not in {"ASC", "DESC"}:
            raise ValueError("排序步骤方向不合法")
        limit = max(1, min(int(order_step.get("limit") or 10), 100))
        order_by = str(order_step.get("order_by") or "METRIC_VALUE").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", order_by):
            raise ValueError("排序步骤字段不合法")
        return (
            "SELECT * FROM (\n"
            f"{base_sql}\n"
            f") ORDER BY {order_by} {direction} FETCH FIRST {limit} ROWS ONLY"
        )

    @staticmethod
    def _time_bucket_expression(column_expr: str, granularity: str) -> str:
        if granularity == "MONTH":
            return f"TO_CHAR(TRUNC({column_expr}, 'MM'), 'YYYY-MM')"
        if granularity == "WEEK":
            return f"TO_CHAR(TRUNC({column_expr}, 'IW'), 'YYYY-MM-DD')"
        return f"TO_CHAR(TRUNC({column_expr}), 'YYYY-MM-DD')"

    @staticmethod
    def _time_window_predicate(column_expr: str, time_window: str) -> str:
        token = str(time_window or "").strip().upper()
        if not token:
            return ""
        match = re.fullmatch(r"(\d+)([DWM])", token)
        if not match:
            return ""
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "D":
            return f"{column_expr} >= TRUNC(SYSDATE) - {value}"
        if unit == "W":
            return f"{column_expr} >= TRUNC(SYSDATE, 'IW') - {value * 7}"
        if unit == "M":
            return f"{column_expr} >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -{value})"
        return ""

    def _build_group_by_time_window_sql(
        self,
        *,
        time_step: Dict[str, Any],
        root_node: Dict[str, Any],
        graph_name: str,
        root_step: Optional[Dict[str, Any]] = None,
        based_on_step: Optional[Dict[str, Any]] = None,
        target_node: Optional[Dict[str, Any]] = None,
        filter_property: str,
        filter_value: str,
    ) -> str:
        granularity = str(time_step.get("time_granularity") or "DAY").upper()
        time_dimension = str(time_step.get("time_dimension") or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", time_dimension):
            raise ValueError("时间分组步骤缺少合法时间维度")
        aggregation_method = str(time_step.get("aggregation_method") or "").upper()
        column_name = str(time_step.get("column_name") or "").upper()
        metric_code = str(time_step.get("metric_code") or "").upper()
        based_on = based_on_step or root_step or {}
        time_window = str(time_step.get("time_window") or "").upper()
        if str(based_on.get("action") or "") == "expand_relations":
            if not graph_name:
                raise ValueError("时间分组统计缺少属性图名称")
            related_node = target_node or root_node
            path = based_on.get("path") or time_step.get("path") or []
            if not path:
                raise ValueError("时间分组统计缺少图路径")
            root_label = self._normalize_label_name(root_node.get("displayName") or root_node.get("name"))
            target_label = self._normalize_label_name(related_node.get("displayName") or related_node.get("name"))
            filter_name = self._normalize_label_name(filter_property)
            match_parts = [f"(r IS {root_label})"]
            current_label = root_label
            for hop_index, hop in enumerate(path, start=1):
                next_label = self._normalize_label_name(hop.get("target"))
                direction = hop.get("direction")
                edge_label = self._normalize_label_name(hop.get("edge"))
                alias = "t" if hop_index == len(path) else f"n{hop_index}"
                relation = f"-[e{hop_index} IS {edge_label}]->" if direction == "OUT" else f"<-[e{hop_index} IS {edge_label}]-"
                match_parts.append(f"{relation}({alias} IS {next_label})")
                current_label = next_label
            if current_label != target_label:
                raise ValueError("时间分组路径终点与目标对象不一致")
            aggregate_alias = f"AGG_{column_name}" if column_name and column_name != "*" else ""
            projections = [f"t.{time_dimension} AS RAW_TIME"]
            if filter_name:
                projections.append(f"r.{filter_name} AS ROOT_{filter_name}")
            if aggregate_alias:
                projections.append(f"t.{column_name} AS {aggregate_alias}")
            expression = self._build_fact_aggregate_expression(
                aggregation_method,
                aggregate_alias,
                fallback_column=aggregate_alias or self._default_metric_column(related_node) or "*",
            )
            time_bucket = self._time_bucket_expression("RAW_TIME", granularity)
            sql = f"""WITH time_rows AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name.upper()}
    MATCH {''.join(match_parts)}
    COLUMNS (
      {', '.join(projections)}
    )
  )
)
SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE,
       {time_bucket} AS TIME_BUCKET,
       {expression} AS METRIC_VALUE
FROM time_rows"""
            predicates = []
            if filter_name and filter_value:
                predicates.append(f"ROOT_{filter_name} = '{self._safe_graph_sql_literal(filter_value)}'")
            time_window_predicate = self._time_window_predicate("RAW_TIME", time_window)
            if time_window_predicate:
                predicates.append(time_window_predicate)
            if predicates:
                sql += "\nWHERE " + " AND ".join(predicates)
            sql += "\nGROUP BY " + time_bucket + "\nORDER BY TIME_BUCKET ASC"
            return sql
        table_name = SourceDataService._safe_graph_object_name(root_node.get("tableName") or "")
        time_bucket = self._time_bucket_expression(time_dimension, granularity)
        expression = self._build_fact_aggregate_expression(
            aggregation_method,
            column_name,
            fallback_column=column_name or self._default_metric_column(root_node) or "*",
        )
        predicates = []
        if filter_property and filter_value:
            predicates.append(f"{filter_property} = '{self._safe_graph_sql_literal(filter_value)}'")
        time_window_predicate = self._time_window_predicate(time_dimension, time_window)
        if time_window_predicate:
            predicates.append(time_window_predicate)
        where_sql = f"\nWHERE {' AND '.join(predicates)}" if predicates else ""
        return (
            f"SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE, "
            f"{time_bucket} AS TIME_BUCKET, {expression} AS METRIC_VALUE "
            f"FROM {table_name}{where_sql} GROUP BY {time_bucket} ORDER BY TIME_BUCKET ASC"
        )

    def _build_metric_formula_sql(
        self,
        *,
        formula_step: Dict[str, Any],
        numerator_sql: str,
        denominator_sql: str,
    ) -> str:
        formula_type = str(formula_step.get("formula_type") or "RATIO").upper()
        metric_code = str(formula_step.get("metric_code") or "").upper()
        if formula_type not in {"RATIO", "RATE", "PERCENT"}:
            raise ValueError("当前仅支持 ratio/rate/percent 公式指标")
        numerator_cte = "numerator_result"
        denominator_cte = "denominator_result"
        numerator_step_kind = str(formula_step.get("numerator_step_kind") or "").upper()
        denominator_step_kind = str(formula_step.get("denominator_step_kind") or "").upper()
        grouped = numerator_step_kind == "TIME_WINDOW_GROUP_BY" and denominator_step_kind == "TIME_WINDOW_GROUP_BY"
        object_time_grouped = (
            numerator_step_kind == "OBJECT_TIME_WINDOW_GROUP_BY"
            and denominator_step_kind == "OBJECT_TIME_WINDOW_GROUP_BY"
        )
        if object_time_grouped:
            group_properties = [
                str(item or "").upper()
                for item in (formula_step.get("group_properties") or [])
                if str(item or "").upper()
            ]
            join_clauses = ["n.TIME_BUCKET = d.TIME_BUCKET"] + [f"n.{name} = d.{name}" for name in group_properties]
            projection = ",\n       ".join(["n.TIME_BUCKET AS TIME_BUCKET"] + [f"n.{name} AS {name}" for name in group_properties])
            return f"""WITH {numerator_cte} AS (
{numerator_sql}
),
{denominator_cte} AS (
{denominator_sql}
)
SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE,
       {projection},
       CASE
         WHEN d.METRIC_VALUE IS NULL OR d.METRIC_VALUE = 0 THEN NULL
         ELSE ROUND((n.METRIC_VALUE / d.METRIC_VALUE) * 100, 4)
       END AS METRIC_VALUE
FROM {numerator_cte} n
LEFT JOIN {denominator_cte} d
  ON {' AND '.join(join_clauses)}
ORDER BY n.TIME_BUCKET ASC"""
        if grouped:
            return f"""WITH {numerator_cte} AS (
{numerator_sql}
),
{denominator_cte} AS (
{denominator_sql}
)
SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE,
       n.TIME_BUCKET AS TIME_BUCKET,
       CASE
         WHEN d.METRIC_VALUE IS NULL OR d.METRIC_VALUE = 0 THEN NULL
         ELSE ROUND((n.METRIC_VALUE / d.METRIC_VALUE) * 100, 4)
       END AS METRIC_VALUE
FROM {numerator_cte} n
LEFT JOIN {denominator_cte} d
  ON n.TIME_BUCKET = d.TIME_BUCKET
ORDER BY n.TIME_BUCKET ASC"""
        return f"""WITH {numerator_cte} AS (
{numerator_sql}
),
{denominator_cte} AS (
{denominator_sql}
)
SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE,
       CASE
         WHEN d.METRIC_VALUE IS NULL OR d.METRIC_VALUE = 0 THEN NULL
         ELSE ROUND((n.METRIC_VALUE / d.METRIC_VALUE) * 100, 4)
       END AS METRIC_VALUE
FROM {numerator_cte} n
CROSS JOIN {denominator_cte} d"""

    def _build_group_by_object_time_window_sql(
        self,
        *,
        step: Dict[str, Any],
        root_node: Dict[str, Any],
        graph_name: str,
        root_step: Optional[Dict[str, Any]] = None,
        based_on_step: Optional[Dict[str, Any]] = None,
        target_node: Optional[Dict[str, Any]] = None,
        filter_property: str,
        filter_value: str,
    ) -> str:
        granularity = str(step.get("time_granularity") or "DAY").upper()
        time_dimension = str(step.get("time_dimension") or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", time_dimension):
            raise ValueError("对象时间分组步骤缺少合法时间维度")
        aggregation_method = str(step.get("aggregation_method") or "").upper()
        column_name = str(step.get("column_name") or "").upper()
        metric_code = str(step.get("metric_code") or "").upper()
        group_properties = [str(item or "").upper() for item in (step.get("group_properties") or []) if str(item or "").upper()]
        if not group_properties:
            raise ValueError("对象时间分组步骤缺少对象分组字段")
        based_on = based_on_step or root_step or {}
        time_window = str(step.get("time_window") or "").upper()
        if str(based_on.get("action") or "") == "expand_relations":
            if not graph_name:
                raise ValueError("对象时间分组统计缺少属性图名称")
            related_node = target_node or root_node
            path = based_on.get("path") or step.get("path") or []
            if not path:
                raise ValueError("对象时间分组统计缺少图路径")
            root_label = self._normalize_label_name(root_node.get("displayName") or root_node.get("name"))
            target_label = self._normalize_label_name(related_node.get("displayName") or related_node.get("name"))
            filter_name = self._normalize_label_name(filter_property)
            match_parts = [f"(r IS {root_label})"]
            current_label = root_label
            for hop_index, hop in enumerate(path, start=1):
                next_label = self._normalize_label_name(hop.get("target"))
                direction = hop.get("direction")
                edge_label = self._normalize_label_name(hop.get("edge"))
                alias = "t" if hop_index == len(path) else f"n{hop_index}"
                relation = f"-[e{hop_index} IS {edge_label}]->" if direction == "OUT" else f"<-[e{hop_index} IS {edge_label}]-"
                match_parts.append(f"{relation}({alias} IS {next_label})")
                current_label = next_label
            if current_label != target_label:
                raise ValueError("对象时间分组路径终点与目标对象不一致")
            aggregate_alias = f"AGG_{column_name}" if column_name and column_name != "*" else ""
            projections = [f"t.{time_dimension} AS RAW_TIME"]
            if filter_name:
                projections.append(f"r.{filter_name} AS ROOT_{filter_name}")
            outer_group_columns = []
            for property_name in group_properties:
                alias = f"GROUP_{property_name}"
                projections.append(f"t.{property_name} AS {alias}")
                outer_group_columns.append((alias, property_name))
            if aggregate_alias:
                projections.append(f"t.{column_name} AS {aggregate_alias}")
            expression = self._build_fact_aggregate_expression(
                aggregation_method,
                aggregate_alias,
                fallback_column=aggregate_alias or self._default_metric_column(related_node) or "*",
            )
            time_bucket = self._time_bucket_expression("RAW_TIME", granularity)
            group_projection_sql = ", ".join(f"{alias} AS {property_name}" for alias, property_name in outer_group_columns)
            group_by_sql = ", ".join(["TIME_BUCKET"] + [alias for alias, _ in outer_group_columns])
            sql = f"""WITH object_time_rows AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name.upper()}
    MATCH {''.join(match_parts)}
    COLUMNS (
      {', '.join(projections)}
    )
  )
)
SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE,
       {time_bucket} AS TIME_BUCKET,
       {group_projection_sql},
       {expression} AS METRIC_VALUE
FROM object_time_rows"""
            predicates = []
            if filter_name and filter_value:
                predicates.append(f"ROOT_{filter_name} = '{self._safe_graph_sql_literal(filter_value)}'")
            time_window_predicate = self._time_window_predicate("RAW_TIME", time_window)
            if time_window_predicate:
                predicates.append(time_window_predicate)
            if predicates:
                sql += "\nWHERE " + " AND ".join(predicates)
            sql += f"\nGROUP BY {group_by_sql}\nORDER BY TIME_BUCKET ASC"
            return sql
        table_name = SourceDataService._safe_graph_object_name(root_node.get("tableName") or "")
        time_bucket = self._time_bucket_expression(time_dimension, granularity)
        expression = self._build_fact_aggregate_expression(
            aggregation_method,
            column_name,
            fallback_column=column_name or self._default_metric_column(root_node) or "*",
        )
        predicates = []
        if filter_property and filter_value:
            predicates.append(f"{filter_property} = '{self._safe_graph_sql_literal(filter_value)}'")
        time_window_predicate = self._time_window_predicate(time_dimension, time_window)
        if time_window_predicate:
            predicates.append(time_window_predicate)
        where_sql = f"\nWHERE {' AND '.join(predicates)}" if predicates else ""
        group_projection_sql = ", ".join(group_properties)
        group_by_sql = ", ".join([time_bucket] + group_properties)
        return (
            f"SELECT '{self._safe_graph_sql_literal(metric_code)}' AS METRIC_CODE, "
            f"{time_bucket} AS TIME_BUCKET, {group_projection_sql}, {expression} AS METRIC_VALUE "
            f"FROM {table_name}{where_sql} GROUP BY {group_by_sql} ORDER BY TIME_BUCKET ASC"
        )

    async def _resolve_managed_skill_plan(
        self,
        *,
        skill_markdown: str,
        skill_files: Optional[Dict[str, str]] = None,
        question: str,
        conversation_context: str,
        topology: Dict[str, Any],
        llm_config: SysLLMConfig,
        skill_guidance: str = "",
        execution_contract: Optional[Dict[str, Any]] = None,
        planning_feedback: str = "",
        excluded_root_labels: Optional[List[str]] = None,
        excluded_target_labels: Optional[List[str]] = None,
        preferred_root_label: str = "",
    ) -> Dict[str, Any]:
        intent_type = self._derive_managed_skill_intent_type(question)
        nodes = topology.get("nodes") or []
        indexes = self._build_topology_indexes(topology)
        nodes_by_label = indexes["nodes_by_label"]
        contract = execution_contract or {}
        excluded_roots = {
            self._normalize_label_name(item)
            for item in (excluded_root_labels or [])
            if self._normalize_label_name(item)
        }
        excluded_targets = {
            self._normalize_label_name(item)
            for item in (excluded_target_labels or [])
            if self._normalize_label_name(item)
        }
        preferred_root = self._normalize_label_name(preferred_root_label)
        matched_reference_pattern = self._select_reference_pattern(question, contract, topology)
        if matched_reference_pattern:
            reference_plan = self._build_plan_from_reference_pattern(
                pattern=matched_reference_pattern,
                intent_type=intent_type,
                topology=topology,
                skill_files=skill_files or {},
                question=question,
                conversation_context=conversation_context,
                contract=contract,
                excluded_roots=excluded_roots,
                excluded_targets=excluded_targets,
                preferred_root=preferred_root,
            )
            if reference_plan:
                return reference_plan
        candidate_catalog = [
            {
                "label": self._normalize_label_name(node.get("displayName") or node.get("name")),
                "properties": [str(prop.get("property_name") or "").upper() for prop in (node.get("properties") or [])[:20]],
            }
            for node in nodes[:80]
        ]
        planner_prompt = f"""根据用户问题、Skill 语义和 Oracle Property Graph 拓扑，生成一个受控的查询计划。
只返回 JSON，格式：
{{
  "root_label":"起点节点标签",
  "filter_property":"过滤属性",
  "filter_value":"过滤值",
  "root_properties":["起点展示属性"],
  "target_labels":["目标节点标签"],
  "target_properties":{{"目标节点标签":["展示属性"]}},
  "reason":"不超过80字"
}}

规则：
1. root_label、filter_property、target_labels 和属性名必须来自候选节点。
2. filter_value 必须来自当前问题；只有当前问题没有精确编码时才可从会话上下文继承。
3. 若问题只需单对象回答，可返回空的 target_labels。
4. 若问题明确要求多个对象或关系链路，优先返回多个 target_labels。
5. target_labels 最多 {MANAGED_SKILL_TEST_PLAN_MAX_TARGETS} 个；每个对象展示属性最多 {MANAGED_SKILL_TEST_PLAN_MAX_PROPERTIES} 个。
6. 不返回 SQL，不编造对象、属性、关系和过滤值。

Skill 语义与策略：
{skill_guidance[:12000] or '无'}

执行契约：
{json.dumps(contract, ensure_ascii=False)}

规划反馈：
{planning_feedback or '无'}

避免重复选择的起点对象：
{json.dumps(list(excluded_roots), ensure_ascii=False)}

避免重复选择的目标对象：
{json.dumps(list(excluded_targets), ensure_ascii=False)}

优先保留的起点对象：
{preferred_root or '无'}

当前问题：{question}
会话上下文：{conversation_context or '无'}
候选节点：{json.dumps(candidate_catalog, ensure_ascii=False)}"""
        raw = await self.llm_service.call_llm(
            "你是 Oracle Property Graph 受控计划生成器，只返回合法 JSON 计划。",
            planner_prompt,
            llm_config,
            timeout_override=max(llm_config.timeout, 60),
        )
        plan_data = self.llm_service._extract_json_object(raw or "") or {}
        if not isinstance(plan_data, dict):
            plan_data = {}
        root_label = self._normalize_label_name(plan_data.get("root_label"))
        filter_property = self._normalize_label_name(plan_data.get("filter_property"))
        filter_value = str(plan_data.get("filter_value") or "").strip()
        root_properties = [
            str(name or "").upper() for name in (plan_data.get("root_properties") or [])
            if isinstance(plan_data.get("root_properties"), list)
        ]
        target_labels = []
        for item in plan_data.get("target_labels") or []:
            label = self._normalize_label_name(item)
            if label and label != root_label and label in nodes_by_label and label not in target_labels and label not in excluded_targets:
                target_labels.append(label)
        target_labels = target_labels[:MANAGED_SKILL_TEST_PLAN_MAX_TARGETS]
        raw_target_properties = plan_data.get("target_properties") if isinstance(plan_data.get("target_properties"), dict) else {}
        target_properties = {
            self._normalize_label_name(label): [str(name or "").upper() for name in values][:MANAGED_SKILL_TEST_PLAN_MAX_PROPERTIES]
            for label, values in raw_target_properties.items()
            if isinstance(values, list) and self._normalize_label_name(label) in target_labels
        }
        known_text = f"{question}\n{conversation_context}".upper()
        identifier_match = re.search(r"\b(?:BOT|BATCH|CASE|PACK|PALLET|STACK|OUT|TRANS)-[A-Z0-9_.:/-]+\b", known_text)
        if not filter_value and identifier_match:
            filter_value = identifier_match.group(0)
        if root_label in excluded_roots:
            root_label = ""
        if not root_label and preferred_root and preferred_root in nodes_by_label and preferred_root not in excluded_roots:
            root_label = preferred_root
        if root_label not in nodes_by_label:
            contract_entry = next(
                (
                    self._normalize_label_name(item)
                    for item in (contract.get("entry_objects") or [])
                    if self._normalize_label_name(item) and self._normalize_label_name(item) not in excluded_roots
                ),
                "",
            )
            root_label = contract_entry
        if root_label not in nodes_by_label and len(nodes) == 1:
            root_label = self._normalize_label_name(nodes[0].get("displayName") or nodes[0].get("name"))
        if root_label not in nodes_by_label:
            selected_node = await self._select_managed_skill_graph_node(
                skill_markdown=skill_markdown,
                question=question,
                llm_config=llm_config,
                topology=topology,
                skill_guidance=skill_guidance,
            )
            root_label = self._normalize_label_name(selected_node.get("displayName") or selected_node.get("name"))
            if root_label in excluded_roots:
                root_label = next(
                    (
                        self._normalize_label_name(node.get("displayName") or node.get("name"))
                        for node in nodes
                        if self._normalize_label_name(node.get("displayName") or node.get("name")) not in excluded_roots
                    ),
                    root_label,
                )
        if preferred_root and preferred_root in nodes_by_label and preferred_root not in excluded_roots:
            root_label = preferred_root
        root_node = nodes_by_label.get(root_label)
        if not root_node:
            raise ValueError("Agent 未能为当前问题选择有效的本体起点对象")
        if not filter_property:
            root_properties_by_name = {
                str(prop.get("property_name") or "").upper(): prop
                for prop in (root_node.get("properties") or [])
            }
            for candidate_name in ("BOTTLE_CODE", "BATCH_NO", "CASE_CODE", "PACK_CODE", "PALLET_CODE", "STACK_CODE", "OUTBOUND_NO"):
                if candidate_name in root_properties_by_name and candidate_name in known_text:
                    filter_property = candidate_name
                    break
            if not filter_property:
                filter_property = next(
                    (
                        str(prop.get("property_name") or "").upper()
                        for prop in (root_node.get("properties") or [])
                        if prop.get("is_primary_key") == "Y"
                    ),
                    "",
                )
        if filter_value and filter_value.upper() not in known_text:
            filter_value = ""
        if filter_property and not filter_value:
            fallback_match = re.search(r"\b[A-Z0-9][A-Z0-9_.:/-]{3,}\b", question.upper())
            if fallback_match:
                filter_value = fallback_match.group(0)
        plan_seed = self._build_seed_steps_for_managed_skill_plan(
            topology=topology,
            root_label=root_label,
            filter_property=filter_property,
            filter_value=filter_value,
            root_properties=root_properties,
            target_labels=target_labels,
            target_properties=target_properties,
        )
        additional_steps = self._append_managed_skill_plan_actions(
            plan_seed=plan_seed,
            forced_actions=None,
            topology=topology,
            skill_files=skill_files or {},
            question=question,
            execution_contract=contract,
        )
        return {
            "plan_version": "1.0",
            "intent_type": intent_type,
            "reason": str(plan_data.get("reason") or "根据当前问题选择本体对象并扩展相关关系。").strip()[:200],
            "selected_objects": plan_seed.get("selected_objects") or [],
            "planning_mode": "LLM_PLAN",
            "reference_pattern_id": "",
            "steps": (list(plan_seed.get("steps") or []) + additional_steps)[:MANAGED_SKILL_TEST_PLAN_MAX_STEPS],
        }

    def _plan_step_title(self, step: Dict[str, Any]) -> str:
        action = str(step.get("action") or "")
        label = self._normalize_label_name(step.get("label"))
        if action == "select_root":
            return f"选择起点对象 {label}"
        if action == "expand_relations":
            return f"扩展关联对象 {label}"
        if action == "group_by_object":
            return f"按对象分组统计 {label}"
        if action == "group_by_object_time_window":
            return f"按对象与时间统计 {label}"
        if action == "filter_aggregate_result":
            return f"筛选统计结果 {label}"
        if action == "order_and_limit":
            return f"排序截取结果 {label}"
        if action == "apply_rules":
            return f"规则判定 {label}"
        if action == "summarize_evidence":
            return f"总结证据 {label}"
        if action == "group_by_time_window":
            return f"按时间窗口统计 {label}"
        if action == "metric_formula":
            return f"计算公式指标 {step.get('metric_name') or step.get('metric_code') or label}"
        if action == "fact_aggregate":
            return f"聚合指标 {step.get('metric_name') or step.get('metric_code') or label}"
        return label or "执行步骤"

    def _plan_step_detail(self, step: Dict[str, Any]) -> str:
        action = str(step.get("action") or "")
        label = self._normalize_label_name(step.get("label"))
        if action == "select_root":
            filter_config = step.get("filter") or {}
            return f"以 {label} 作为起点对象，使用 {filter_config.get('property') or '主键'} = {filter_config.get('value') or '未指定'} 做受限检索。"
        if action == "expand_relations":
            path = step.get("path") or []
            route = " -> ".join([self._normalize_label_name(label)] + [self._normalize_label_name(item.get("target")) for item in path])
            return f"沿图关系路径扩展关联对象，路径为 {route}。"
        if action == "group_by_object":
            route = " -> ".join(
                [self._normalize_label_name(step.get("root_label"))]
                + [self._normalize_label_name(item.get("target")) for item in (step.get("path") or [])]
            )
            group_properties = ", ".join(step.get("group_properties") or []) or "对象标识字段"
            return f"先沿 {route} 定位到 {label}，再按 {group_properties} 分组执行 {step.get('aggregation_method') or '聚合'} 统计。"
        if action == "group_by_object_time_window":
            route = " -> ".join(
                [self._normalize_label_name(step.get("root_label"))]
                + [self._normalize_label_name(item.get("target")) for item in (step.get("path") or [])]
            )
            group_properties = ", ".join(step.get("group_properties") or []) or "对象标识字段"
            return (
                f"先沿 {route} 定位到 {label}，再按 {group_properties} 和 {step.get('time_dimension') or '时间字段'} "
                f"执行 {step.get('time_granularity') or 'DAY'} 粒度统计。"
            )
        if action == "filter_aggregate_result":
            return f"对统计结果执行阈值筛选，仅保留 METRIC_VALUE {step.get('operator') or ''} {step.get('threshold')} 的记录。"
        if action == "order_and_limit":
            return f"按 {step.get('order_by') or 'METRIC_VALUE'} {step.get('direction') or 'DESC'} 排序，并截取前 {step.get('limit') or 10} 条记录。"
        if action == "apply_rules":
            return "基于 Skill 规则目录对当前统计结果执行异常判定与规则命中识别。"
        if action == "summarize_evidence":
            return "汇总本轮证据表，生成结构化证据摘要。"
        if action == "group_by_time_window":
            return (
                f"基于 {step.get('time_dimension') or '时间字段'} 按 {step.get('time_granularity') or 'DAY'} 聚合，"
                f"统计指标 {step.get('metric_name') or step.get('metric_code')}，时间窗口 {step.get('time_window') or '未指定'}。"
            )
        if action == "metric_formula":
            return (
                f"使用公式 {step.get('formula_type') or 'RATIO'}，"
                f"基于 {step.get('numerator_metric_code')} / {step.get('denominator_metric_code')} 计算指标 "
                f"{step.get('metric_name') or step.get('metric_code')}。"
            )
        if action == "fact_aggregate":
            if step.get("path"):
                route = " -> ".join(
                    [self._normalize_label_name(step.get("root_label"))]
                    + [self._normalize_label_name(item.get("target")) for item in (step.get("path") or [])]
                )
                return f"先沿 {route} 定位到 {label}，再对指标 {step.get('metric_name') or step.get('metric_code')} 执行 {step.get('aggregation_method') or '聚合'} 统计。"
            return f"基于 {label} 对指标 {step.get('metric_name') or step.get('metric_code')} 执行 {step.get('aggregation_method') or '聚合'} 统计。"
        return "执行受控查询。"

    def _build_managed_skill_evidence_tables(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        source_id: str,
        schema: Optional[str],
        sample_limit: int,
        event_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        graph_name = str(topology.get("graph_name") or "").upper()
        indexes = self._build_topology_indexes(topology)
        nodes_by_label = indexes["nodes_by_label"]
        steps = plan.get("steps") or []
        if not steps:
            raise ValueError("当前执行计划没有可执行步骤")
        root_step = steps[0]
        root_label = self._normalize_label_name(root_step.get("label"))
        root_node = nodes_by_label.get(root_label)
        if not root_node:
            raise ValueError("起点对象不存在于当前属性图")
        step_lookup = {
            str(item.get("step_id") or ""): item
            for item in steps
            if str(item.get("step_id") or "")
        }
        filter_config = root_step.get("filter") or {}
        filter_property = self._normalize_label_name(filter_config.get("property"))
        filter_value = str(filter_config.get("value") or "").strip()
        evidence_tables: List[Dict[str, Any]] = []
        executed_queries: List[Dict[str, Any]] = []
        execution_events: List[Dict[str, Any]] = []
        for step_index, step in enumerate(steps, start=1):
            action = str(step.get("action") or "")
            label = self._normalize_label_name(step.get("label"))
            if action == "select_root":
                sql = self._build_graph_root_query_sql(
                    graph_name,
                    root_node,
                    filter_property,
                    filter_value,
                    step.get("display_properties") or [],
                )
                title = f"{label} 本体对象属性"
                kind = "GRAPH_ROOT"
                related_objects = [label]
            elif action == "expand_relations":
                target_node = nodes_by_label.get(label)
                path = step.get("path") or []
                if not target_node or not path:
                    continue
                sql = self._build_graph_relation_query_sql(
                    graph_name,
                    root_node,
                    target_node,
                    path,
                    filter_property,
                    filter_value,
                    root_step.get("display_properties") or [],
                    step.get("display_properties") or [],
                )
                title = f"{root_label} 关联 {label}"
                kind = "GRAPH_RELATION"
                related_objects = [root_label, label]
            elif action == "fact_aggregate":
                based_on_step = step_lookup.get(str(step.get("based_on_step") or "")) or root_step
                target_node = nodes_by_label.get(label)
                sql = self._build_fact_aggregate_sql(
                    metric_step=step,
                    root_node=root_node,
                    graph_name=graph_name,
                    root_step=root_step,
                    based_on_step=based_on_step,
                    target_node=target_node,
                    filter_property=filter_property,
                    filter_value=filter_value,
                )
                title = f"{label} 指标聚合：{step.get('metric_name') or step.get('metric_code')}"
                kind = "FACT_AGGREGATE"
                related_objects = [root_label, label] if str(step.get("scope") or "") == "related_object" and label != root_label else [label]
            elif action == "group_by_object":
                based_on_step = step_lookup.get(str(step.get("based_on_step") or "")) or {}
                target_node = nodes_by_label.get(label)
                if not target_node:
                    continue
                sql = self._build_group_by_object_sql(
                    group_step=step,
                    root_node=root_node,
                    graph_name=graph_name,
                    based_on_step=based_on_step,
                    target_node=target_node,
                    filter_property=filter_property,
                    filter_value=filter_value,
                )
                title = f"{label} 分组统计：{step.get('metric_name') or step.get('metric_code')}"
                kind = "OBJECT_GROUP_BY"
                related_objects = [root_label, label]
            else:
                continue
            execution_events.append({
                "event_type": "STEP_SQL_COMPILED",
                "step_id": step.get("step_id"),
                "title": self._plan_step_title(step),
                "status": "READY",
                "detail": self._plan_step_detail(step),
                "payload": {"sql": sql},
            })
            if event_callback:
                event_callback(execution_events[-1])
            if kind in {"FACT_AGGREGATE", "OBJECT_GROUP_BY"}:
                result = self.source_service.execute_remote_readonly_sql(
                    source_id=source_id,
                    query_sql=sql,
                    schema=schema,
                    row_limit=sample_limit,
                )
            else:
                result = self.source_service.execute_remote_graph_query(
                    source_id=source_id,
                    graph_sql=sql,
                    schema=schema,
                    row_limit=sample_limit,
                )
            evidence = {
                "key": f"evidence_{step.get('step_id')}",
                "step_id": step.get("step_id"),
                "title": title,
                "kind": kind,
                "related_objects": related_objects,
                "sql": sql,
                "row_count": len(result.get("rows", [])),
                "columns": [{"column_name": column} for column in (result.get("columns") or [])],
                "sample_rows": result.get("rows", []),
            }
            evidence_tables.append(evidence)
            executed_queries.append({
                "purpose": title,
                "sql": sql,
                "row_count": evidence["row_count"],
            })
            execution_events.append({
                "event_type": "STEP_EXECUTED",
                "step_id": step.get("step_id"),
                "title": self._plan_step_title(step),
                "status": "SUCCESS",
                "detail": f"{title} 查询完成，返回 {evidence['row_count']} 条记录。",
                "payload": {"row_count": evidence["row_count"], "evidence_key": evidence["key"]},
            })
            if event_callback:
                event_callback(execution_events[-1])
            if step_index == 1 and evidence["row_count"] == 0:
                execution_events.append({
                    "event_type": "TURN_WARNING",
                    "step_id": step.get("step_id"),
                    "title": self._plan_step_title(step),
                    "status": "WARNING",
                    "detail": "起点对象未命中任何记录，后续关系扩展可能为空。",
                    "payload": {},
                })
                if event_callback:
                    event_callback(execution_events[-1])
        return {
            "evidence_tables": evidence_tables,
            "executed_queries": executed_queries,
            "execution_events": execution_events,
        }

    @staticmethod
    def _build_table_preview_from_evidence(evidence_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        first = next((item for item in evidence_tables if item.get("sample_rows")), evidence_tables[0] if evidence_tables else None)
        if not first:
            return {"columns": [], "sample_rows": []}
        return {
            "columns": first.get("columns") or [],
            "sample_rows": first.get("sample_rows") or [],
        }

    @staticmethod
    def _extract_analysis_reference_codes(reference_text: str, key: str) -> List[str]:
        try:
            payload = json.loads(reference_text or "{}")
        except (TypeError, ValueError):
            return []
        values = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return []
        codes = []
        for item in values[:12]:
            if not isinstance(item, dict):
                continue
            for field in ("metric_code", "metric_name", "rule_name", "activity_name"):
                value = str(item.get(field) or "").strip()
                if value:
                    codes.append(value)
                    break
        return codes

    @staticmethod
    def _to_numeric_metric_value(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _trend_group_fields(row: Dict[str, Any]) -> Dict[str, Any]:
        excluded = {"METRIC_CODE", "METRIC_VALUE", "TIME_BUCKET", "RULE_NAME", "RULE_CATEGORY", "RULE_DESC", "ACTIVITY_NAME", "OPERATOR", "THRESHOLD"}
        return {
            str(key).upper(): value
            for key, value in (row or {}).items()
            if str(key).upper() not in excluded
        }

    @staticmethod
    def _infer_time_bucket_granularity(time_buckets: List[str]) -> str:
        buckets = [str(item or "").strip() for item in time_buckets if str(item or "").strip()]
        if not buckets:
            return "UNKNOWN"
        if all(re.fullmatch(r"\d{4}-\d{2}", item) for item in buckets):
            return "MONTH"
        if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", item) for item in buckets):
            if len(buckets) >= 2:
                try:
                    dates = [datetime.strptime(item, "%Y-%m-%d") for item in buckets]
                    deltas = [
                        abs((dates[index + 1] - dates[index]).days)
                        for index in range(len(dates) - 1)
                    ]
                    if deltas and all(delta == 7 for delta in deltas):
                        return "WEEK"
                except ValueError:
                    pass
            return "DAY"
        return "UNKNOWN"

    @staticmethod
    def _period_comparison_label(granularity: str) -> str:
        return {
            "DAY": "DOD",
            "WEEK": "WOW",
            "MONTH": "MOM",
        }.get(granularity, "PERIOD")

    def _build_trend_summaries(self, evidence_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        trend_kinds = {"TIME_WINDOW_GROUP_BY", "OBJECT_TIME_WINDOW_GROUP_BY", "METRIC_FORMULA"}
        for evidence in evidence_tables:
            if str(evidence.get("kind") or "") not in trend_kinds:
                continue
            rows = [item for item in (evidence.get("sample_rows") or []) if isinstance(item, dict) and item.get("TIME_BUCKET")]
            if len(rows) < 2:
                continue
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                group_fields = self._trend_group_fields(row)
                key = json.dumps(group_fields, ensure_ascii=False, sort_keys=True)
                grouped.setdefault(key, []).append(row)
            for key, points in grouped.items():
                parsed_group = self._safe_json_loads(key, {}) if key else {}
                ordered_points = sorted(points, key=lambda item: str(item.get("TIME_BUCKET") or ""))
                granularity = self._infer_time_bucket_granularity(
                    [item.get("TIME_BUCKET") for item in ordered_points]
                )
                numeric_points = [
                    {
                        "time_bucket": item.get("TIME_BUCKET"),
                        "metric_value": self._to_numeric_metric_value(item.get("METRIC_VALUE")),
                    }
                    for item in ordered_points
                ]
                numeric_points = [item for item in numeric_points if item.get("metric_value") is not None]
                if len(numeric_points) < 2:
                    continue
                peak_point = max(numeric_points, key=lambda item: item["metric_value"])
                trough_point = min(numeric_points, key=lambda item: item["metric_value"])
                deltas = [
                    numeric_points[index + 1]["metric_value"] - numeric_points[index]["metric_value"]
                    for index in range(len(numeric_points) - 1)
                ]
                direction = "FLAT"
                if deltas and all(delta > 0 for delta in deltas):
                    direction = "RISING"
                elif deltas and all(delta < 0 for delta in deltas):
                    direction = "FALLING"
                elif deltas and any(delta > 0 for delta in deltas) and any(delta < 0 for delta in deltas):
                    direction = "VOLATILE"
                change_value = numeric_points[-1]["metric_value"] - numeric_points[0]["metric_value"]
                change_rate = None
                if numeric_points[0]["metric_value"] not in (None, 0):
                    change_rate = round((change_value / numeric_points[0]["metric_value"]) * 100, 4)
                previous_bucket = numeric_points[-2]["time_bucket"]
                previous_value = numeric_points[-2]["metric_value"]
                period_change_value = numeric_points[-1]["metric_value"] - previous_value
                period_change_rate = None
                if previous_value not in (None, 0):
                    period_change_rate = round((period_change_value / previous_value) * 100, 4)
                comparison_label = self._period_comparison_label(granularity)
                max_rise_bucket = None
                max_drop_bucket = None
                max_rise_value = None
                max_drop_value = None
                if deltas:
                    max_rise_index, max_rise = max(enumerate(deltas), key=lambda item: item[1])
                    max_drop_index, max_drop = min(enumerate(deltas), key=lambda item: item[1])
                    max_rise_bucket = numeric_points[max_rise_index + 1]["time_bucket"]
                    max_drop_bucket = numeric_points[max_drop_index + 1]["time_bucket"]
                    max_rise_value = round(max_rise, 4)
                    max_drop_value = round(max_drop, 4)
                avg_abs_delta = (sum(abs(delta) for delta in deltas) / len(deltas)) if deltas else 0
                spike_buckets = []
                for delta_index, delta in enumerate(deltas):
                    if avg_abs_delta > 0 and abs(delta) >= max(5, avg_abs_delta * 1.4):
                        spike_buckets.append(numeric_points[delta_index + 1]["time_bucket"])
                summaries.append({
                    "evidence_key": evidence.get("key"),
                    "title": evidence.get("title"),
                    "metric_code": evidence.get("metric_code") or (ordered_points[0].get("METRIC_CODE") if ordered_points else ""),
                    "group": parsed_group,
                    "time_granularity": granularity,
                    "point_count": len(numeric_points),
                    "start_bucket": numeric_points[0]["time_bucket"],
                    "start_value": numeric_points[0]["metric_value"],
                    "end_bucket": numeric_points[-1]["time_bucket"],
                    "end_value": numeric_points[-1]["metric_value"],
                    "change_value": round(change_value, 4),
                    "comparison_type": comparison_label,
                    "previous_bucket": previous_bucket,
                    "previous_value": previous_value,
                    "period_change_value": round(period_change_value, 4),
                    "period_change_rate": period_change_rate,
                    "peak_bucket": peak_point["time_bucket"],
                    "peak_value": peak_point["metric_value"],
                    "trough_bucket": trough_point["time_bucket"],
                    "trough_value": trough_point["metric_value"],
                    "direction": direction,
                    "change_rate": change_rate,
                    "max_rise_bucket": max_rise_bucket,
                    "max_rise_value": max_rise_value,
                    "max_drop_bucket": max_drop_bucket,
                    "max_drop_value": max_drop_value,
                    "spike_buckets": spike_buckets,
                })
        return summaries[:20]

    def _build_analysis_flags(self, evidence_tables: List[Dict[str, Any]], trend_summaries: List[Dict[str, Any]]) -> List[str]:
        flags = []
        if any(str(item.get("kind") or "") == "RULE_APPLICATION" and (item.get("row_count") or 0) > 0 for item in evidence_tables):
            flags.append("RULE_HIT")
        directions = {str(item.get("direction") or "") for item in trend_summaries}
        if "RISING" in directions:
            flags.append("RISING_TREND")
        if "FALLING" in directions:
            flags.append("FALLING_TREND")
        if "VOLATILE" in directions:
            flags.append("VOLATILE_TREND")
        if any((item.get("peak_value") or 0) == (item.get("trough_value") or 0) for item in trend_summaries):
            flags.append("FLAT_SEGMENT")
        if any(item.get("spike_buckets") for item in trend_summaries):
            flags.append("ANOMALOUS_SPIKE")
        if any(item.get("comparison_type") == "MOM" for item in trend_summaries):
            flags.append("MOM_AVAILABLE")
        if any(item.get("comparison_type") == "WOW" for item in trend_summaries):
            flags.append("WOW_AVAILABLE")
        if any(item.get("comparison_type") == "DOD" for item in trend_summaries):
            flags.append("DOD_AVAILABLE")
        return self._normalize_string_list(flags)

    def _build_period_comparisons(self, trend_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        comparisons = []
        for item in trend_summaries:
            comparisons.append({
                "evidence_key": item.get("evidence_key"),
                "metric_code": item.get("metric_code"),
                "group": item.get("group") or {},
                "comparison_type": item.get("comparison_type"),
                "previous_bucket": item.get("previous_bucket"),
                "previous_value": item.get("previous_value"),
                "period_change_value": item.get("period_change_value"),
                "period_change_rate": item.get("period_change_rate"),
                "start_bucket": item.get("start_bucket"),
                "end_bucket": item.get("end_bucket"),
                "change_value": item.get("change_value"),
                "change_rate": item.get("change_rate"),
                "max_rise_bucket": item.get("max_rise_bucket"),
                "max_rise_value": item.get("max_rise_value"),
                "max_drop_bucket": item.get("max_drop_bucket"),
                "max_drop_value": item.get("max_drop_value"),
                "spike_buckets": item.get("spike_buckets") or [],
            })
        return comparisons[:20]

    def _build_top_findings(self, trend_summaries: List[Dict[str, Any]], evidence_tables: List[Dict[str, Any]]) -> List[str]:
        findings: List[str] = []
        sorted_trends = sorted(
            trend_summaries,
            key=lambda item: abs(float(item.get("change_value") or 0)),
            reverse=True,
        )
        for item in sorted_trends[:3]:
            group_text = ", ".join(f"{key}={value}" for key, value in (item.get("group") or {}).items()) or "整体"
            findings.append(
                f"{group_text} 在 {item.get('start_bucket')} 到 {item.get('end_bucket')} 间"
                f"{'上升' if item.get('direction') == 'RISING' else '下降' if item.get('direction') == 'FALLING' else '波动'} "
                f"{item.get('change_value')}"
            )
            if item.get("period_change_rate") is not None:
                findings.append(
                    f"{group_text} 最新一期较 {item.get('previous_bucket')} 的 {item.get('comparison_type')} 变化率为 {item.get('period_change_rate')}%"
                )
            if item.get("change_rate") is not None:
                findings.append(
                    f"{group_text} 变化率为 {item.get('change_rate')}%，峰值出现在 {item.get('peak_bucket')}"
                )
            if item.get("spike_buckets"):
                findings.append(
                    f"{group_text} 在 {', '.join(item.get('spike_buckets') or [])} 出现异常波动"
                )
        rule_hits = [
            row for evidence in evidence_tables
            if str(evidence.get("kind") or "") == "RULE_APPLICATION"
            for row in (evidence.get("sample_rows") or [])
            if isinstance(row, dict)
        ]
        for row in rule_hits[:2]:
            findings.append(
                f"规则命中：{row.get('RULE_NAME') or '未命名规则'}，"
                f"METRIC_VALUE={row.get('METRIC_VALUE')}"
            )
        return findings[:5]

    def _build_managed_skill_analysis_result(
        self,
        *,
        agent_output: str,
        plan: Dict[str, Any],
        evidence_tables: List[Dict[str, Any]],
        skill_files: Dict[str, str],
    ) -> Dict[str, Any]:
        applied_metrics = self._normalize_string_list([
            item.get("metric_code")
            for item in evidence_tables
            if item.get("metric_code")
        ]) or self._extract_analysis_reference_codes(skill_files.get("references/metric-catalog.json", ""), "metrics")
        matched_rules = self._normalize_string_list([
            row.get("RULE_NAME")
            for item in evidence_tables
            if item.get("kind") == "RULE_APPLICATION"
            for row in (item.get("sample_rows") or [])
            if isinstance(row, dict)
        ]) or self._extract_analysis_reference_codes(skill_files.get("references/rule-catalog.json", ""), "rules")
        suggested_activities = self._normalize_string_list([
            row.get("ACTIVITY_NAME")
            for item in evidence_tables
            if item.get("kind") == "RULE_APPLICATION"
            for row in (item.get("sample_rows") or [])
            if isinstance(row, dict)
        ]) or self._extract_analysis_reference_codes(skill_files.get("references/activity-playbook.json", ""), "activities")
        trend_summaries = self._build_trend_summaries(evidence_tables)
        analysis_flags = self._build_analysis_flags(evidence_tables, trend_summaries)
        period_comparisons = self._build_period_comparisons(trend_summaries)
        top_findings = self._build_top_findings(trend_summaries, evidence_tables)
        return {
            "summary": agent_output,
            "intent_type": plan.get("intent_type"),
            "selected_objects": plan.get("selected_objects") or [],
            "evidence_table_keys": [item.get("key") for item in evidence_tables],
            "applied_metrics": applied_metrics,
            "matched_rules": matched_rules,
            "suggested_activities": suggested_activities,
            "trend_summaries": trend_summaries,
            "analysis_flags": analysis_flags,
            "period_comparisons": period_comparisons,
            "top_findings": top_findings,
        }

    async def _select_managed_skill_graph_node(
        self,
        *,
        skill_markdown: str,
        question: str,
        llm_config: SysLLMConfig,
        topology: Dict[str, Any],
        skill_guidance: str = "",
    ) -> Dict[str, Any]:
        candidates = topology.get("nodes") or []
        if len(candidates) == 1:
            return {**candidates[0], "reason": "当前属性图仅有一个可访问本体节点。"}
        catalog = [
            {
                "element_name": item.get("name"),
                "label": item.get("displayName"),
                "table": item.get("tableName"),
                "properties": [prop.get("property_name") for prop in (item.get("properties") or [])[:20]],
            }
            for item in candidates[:80]
        ]
        prompt = f'''根据上传 Skill 与用户问题，从 Oracle Property Graph 的本体节点中选择一个最适合首次查询业务属性的节点。
只返回 JSON：{{"label":"候选节点标签","reason":"不超过50字的理由"}}。必须选择候选列表中的 label；不得返回 SQL。

Skill：
{skill_markdown[:12000]}

语义与策略：
{skill_guidance[:12000] or '无'}

用户问题：{question}

本体节点候选：{json.dumps(catalog, ensure_ascii=False)}'''
        raw = await self.llm_service.call_llm(
            "你是 Oracle 图本体节点选择器，只能选择候选图节点并关注业务属性。", prompt, llm_config,
            timeout_override=max(llm_config.timeout, 60),
        )
        selection = self.llm_service._extract_json_object(raw or "") or {}
        selected_label = str(selection.get("label") or "").strip().upper()
        by_label = {str(item.get("displayName") or "").upper(): item for item in candidates}
        if selected_label in by_label:
            return {**by_label[selected_label], "reason": str(selection.get("reason") or "与 Skill 和当前问题匹配。").strip()[:200]}
        raise ValueError("Agent 未能从 Oracle 属性图中选择有效本体节点，请在对话中补充更明确的问题或检查 Skill 指令")

    @staticmethod
    def _build_graph_node_property_sql(graph_name: str, node: Dict[str, Any]) -> str:
        identifier_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
        label = str(node.get("displayName") or "").upper()
        if not identifier_pattern.fullmatch(str(graph_name or "")) or not identifier_pattern.fullmatch(label):
            raise ValueError("属性图或本体节点标签包含不支持的标识符")
        properties = []
        for prop in node.get("properties") or []:
            name = str(prop.get("property_name") or "").upper()
            data_type = str(prop.get("data_type") or "").upper()
            if identifier_pattern.fullmatch(name) and not any(token in data_type for token in ("BLOB", "CLOB", "NCLOB", "BFILE", "LONG", "XMLTYPE")):
                properties.append(name)
        if not properties:
            raise ValueError(f"本体节点 {label} 没有可用于 Oracle Graph SQL 查询的属性")
        projections = ",\n      ".join(f"n.{name} AS {name}" for name in properties[:30])
        return f'''SELECT *
FROM GRAPH_TABLE(
  {graph_name.upper()}
  MATCH (n IS {label})
  COLUMNS (
      {projections}
  )
)'''

    async def _plan_graph_query_from_topology(
        self,
        *,
        question: str,
        conversation_context: str,
        topology: Dict[str, Any],
        llm_config: SysLLMConfig,
        skill_guidance: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Plan a graph query from live topology, then compile it without accepting model SQL."""
        nodes = topology.get("nodes") or []
        if not nodes:
            return None
        catalog = {
            "nodes": [
                {
                    "label": node.get("displayName"),
                    "properties": [
                        {"name": prop.get("property_name"), "primary_key": prop.get("is_primary_key")}
                        for prop in (node.get("properties") or [])[:30]
                    ],
                }
                for node in nodes[:80]
            ],
            "relationships": [
                {"from": edge.get("source"), "to": edge.get("target"), "label": edge.get("name")}
                for edge in (topology.get("edges") or [])[:160]
            ],
        }
        planner_prompt = f'''根据用户问题和 Oracle Property Graph 知识地图，规划一次有精确过滤条件的只读图查询。
只返回 JSON，不返回 SQL，格式如下：
{{"root_label":"起点节点标签","filter_property":"起点过滤属性","filter_value":"必须出现在当前问题或会话上下文中的精确值","root_properties":["起点需展示的属性名"],"target_labels":["目标节点标签"],"target_properties":{{"目标节点标签":["需展示的属性名"]}}}}

规则：
1. root_label、filter_property、target_labels 和属性名必须来自候选节点。
2. 仅在用户给出精确标识时规划；未给出精确标识时返回 {{}}。
3. target_labels 应覆盖用户明确要求的全部业务对象；路径由系统依据关系地图搜索。
4. 不得编造节点、属性、关系或过滤值。
5. root_properties 与 target_properties 是表格展示字段清单：必须优先、完整覆盖用户明确要求的码、名称、状态、时间、数量等业务信息；每个对象仅保留回答问题所需字段，通常不超过 5 个。
6. 不得为了“信息更全”加入无关属性、内部主键或关联外键；除非用户明确询问 ID。起点过滤属性可作为必要定位字段保留。
7. 若无法从知识地图精确映射用户要求的对象或展示字段，返回 {{}}，由系统选择其他受控查询方式。
8. 当前问题优先于会话上下文：若当前问题含有 BOT-、BATCH-、CASE-、PACK-、PALLET-、STACK- 等精确业务编码，filter_value 必须取当前问题中的编码；只有当前问题未给出精确编码且使用“该瓶码/继续”等指代时，才可从上下文继承。
9. 用户显式提到 `ONTO_NODE_XXX` 时，`XXX` 就是必须覆盖的目标图节点标签；图关系可顺向或反向遍历。
10. 若 Skill 明确给出了优先分析入口、关键指标、规则约束或活动建议，应优先选择与这些业务语义最匹配的起点节点和目标节点。

Skill 语义与策略：{skill_guidance[:12000] or '无'}
当前问题：{question}
会话上下文：{conversation_context or '无'}
知识地图：{json.dumps(catalog, ensure_ascii=False)}'''
        raw = await self.llm_service.call_llm(
            "你是 Oracle Property Graph 查询规划器，只输出可由实时拓扑验证的 JSON 计划。",
            planner_prompt,
            llm_config,
            timeout_override=max(llm_config.timeout, 60),
        )
        plan = self.llm_service._extract_json_object(raw or "") or {}
        return self._compile_topology_graph_plan(
            plan, topology, f"{question}\n{conversation_context}",
            display_request_text=question, current_question=question,
        )

    @staticmethod
    def _compile_topology_graph_plan(
        plan: Dict[str, Any], topology: Dict[str, Any], known_text: str,
        display_request_text: Optional[str] = None,
        current_question: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Validate a model plan and compile only known labels/properties/edges to Graph SQL."""
        identifier_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
        graph_name = str(topology.get("graph_name") or "").upper()
        if not identifier_pattern.fullmatch(graph_name):
            return None
        nodes_by_label = {str(node.get("displayName") or "").upper(): node for node in (topology.get("nodes") or [])}
        root_label = str(plan.get("root_label") or "").upper()
        root = nodes_by_label.get(root_label)
        filter_property = str(plan.get("filter_property") or "").upper()
        filter_value = str(plan.get("filter_value") or "").strip()
        if not root or not filter_value or filter_value.upper() not in (known_text or "").upper():
            return None
        if not re.fullmatch(r"[A-Za-z0-9_:\-./]+", filter_value):
            return None
        current_text = (current_question or "").upper()
        identifier_requirements = (
            (r"\bBOT-[A-Z0-9_-]+\b", "BOTTLECODE", "BOTTLE_CODE"),
            (r"\bBATCH-[A-Z0-9_-]+\b", "PRODUCTIONBATCH", "BATCH_NO"),
            (r"\bCASE-[A-Z0-9_-]+\b", "CASECODE", "CASE_CODE"),
            (r"\bPACK-[A-Z0-9_-]+\b", "PACKCODE", "PACK_CODE"),
            (r"\bPALLET-[A-Z0-9_-]+\b", "PALLETCODE", "PALLET_CODE"),
            (r"\bSTACK-[A-Z0-9_-]+\b", "STACKCODE", "STACK_CODE"),
        )
        current_identifier_requirements = [
            (match.group(0), expected_root, expected_filter)
            for pattern, expected_root, expected_filter in identifier_requirements
            for match in re.finditer(pattern, current_text)
        ]
        if current_identifier_requirements and not any(
            filter_value.upper() == value and root_label == expected_root and filter_property == expected_filter
            for value, expected_root, expected_filter in current_identifier_requirements
        ):
            return None
        root_properties = {str(prop.get("property_name") or "").upper(): prop for prop in (root.get("properties") or [])}
        root_key = next((name for name, prop in root_properties.items() if prop.get("is_primary_key") == "Y"), "")
        if not root_key or filter_property not in root_properties:
            return None
        target_labels = []
        for item in plan.get("target_labels") or []:
            label = str(item or "").upper()
            if label and label != root_label and label in nodes_by_label and label not in target_labels:
                target_labels.append(label)
        if not target_labels:
            return None
        explicitly_named_nodes = {
            item.upper() for item in re.findall(r"ONTO_NODE_([A-Z0-9_]+)", current_text)
        }
        if "质检" in (display_request_text or current_question or ""):
            explicitly_named_nodes.add("QUALITYINSPECTION")
        if any(label in nodes_by_label and label not in target_labels for label in explicitly_named_nodes):
            return None

        # 对“包码、箱码、托码、垛码”等明确点名的包装层级，使用当前问题而非
        # 会话上下文来收紧字段。模型即使额外选择状态或生产外键，也不得污染结果表。
        packaging_code_fields = (
            ("瓶码", "BOTTLECODE", "BOTTLE_CODE"),
            ("包码", "PACKCODE", "PACK_CODE"),
            ("箱码", "CASECODE", "CASE_CODE"),
            ("托码", "PALLETCODE", "PALLET_CODE"),
            ("垛码", "STACKCODE", "STACK_CODE"),
        )
        requested_packaging_fields = [
            (label, property_name)
            for term, label, property_name in packaging_code_fields
            if term in (display_request_text or known_text or "")
        ]
        forced_target_properties: Dict[str, List[str]] = {}
        if requested_packaging_fields:
            required_target_labels = [label for label, _property_name in requested_packaging_fields if label != root_label]
            if not all(label in nodes_by_label and label in target_labels for label in required_target_labels):
                return None
            target_labels = [label for label in target_labels if label in set(required_target_labels)]
            forced_target_properties = {label: [property_name] for label, property_name in requested_packaging_fields if label != root_label}

        node_id_to_label = {str(node.get("id") or ""): str(node.get("displayName") or "").upper() for node in nodes_by_label.values()}
        adjacency: Dict[str, List[tuple[str, str, str]]] = {}
        for edge in topology.get("edges") or []:
            source = node_id_to_label.get(str(edge.get("source") or ""))
            target = node_id_to_label.get(str(edge.get("target") or ""))
            edge_label = str(edge.get("name") or "").upper()
            if source and target and identifier_pattern.fullmatch(edge_label):
                adjacency.setdefault(source, []).append((target, edge_label, "OUT"))
                adjacency.setdefault(target, []).append((source, edge_label, "IN"))

        def find_path(target_label: str) -> Optional[List[tuple[str, str, str]]]:
            queue = [(root_label, [])]
            visited = {root_label}
            while queue:
                current, path = queue.pop(0)
                if current == target_label:
                    return path
                for next_label, edge_label, direction in adjacency.get(current, []):
                    if next_label not in visited:
                        visited.add(next_label)
                        queue.append((next_label, path + [(next_label, edge_label, direction)]))
            return None

        requested_root_properties = [
            str(name or "").upper() for name in (plan.get("root_properties") or [])
            if str(name or "").upper() in root_properties
        ]
        if requested_packaging_fields:
            requested_root_properties = []
        root_projection_names = [filter_property] + [
            name for name in requested_root_properties
            if name not in {root_key, filter_property}
            and not any(token in str(root_properties[name].get("data_type") or "").upper() for token in ("BLOB", "CLOB", "NCLOB", "LONG", "XMLTYPE"))
        ][:5]
        root_projection_names = list(dict.fromkeys(root_projection_names))
        root_columns = [f"r.{root_key} AS ROOT_ID"] + [f"r.{name} AS ROOT_{name}" for name in root_projection_names]
        ctes = [f'''root_node AS (
  SELECT * FROM GRAPH_TABLE(
    {graph_name}
    MATCH (r IS {root_label})
    COLUMNS ({', '.join(root_columns)})
  )
)''']
        joins: List[str] = []
        outer_columns = [f"r.ROOT_{name}" for name in root_projection_names]
        target_properties = {
            str(label or "").upper(): properties
            for label, properties in (plan.get("target_properties") or {}).items()
        } if isinstance(plan.get("target_properties"), dict) else {}
        if forced_target_properties:
            target_properties = forced_target_properties
        for index, target_label in enumerate(target_labels, start=1):
            path = find_path(target_label)
            if not path:
                return None
            target = nodes_by_label[target_label]
            properties_by_name = {str(prop.get("property_name") or "").upper(): prop for prop in (target.get("properties") or [])}
            selected_names = [
                str(name or "").upper() for name in (target_properties.get(target_label) or [])
                if str(name or "").upper() in properties_by_name
            ]
            if not selected_names:
                return None
            selected_names = [name for name in selected_names if identifier_pattern.fullmatch(name)][:12]
            selected_names = list(dict.fromkeys(selected_names))
            if not selected_names:
                return None
            aliases = ["r"]
            match_parts = [f"(r IS {root_label})"]
            for hop_index, (next_label, edge_label, direction) in enumerate(path, start=1):
                alias = f"n{hop_index}"
                aliases.append(alias)
                relation = f"-[e{hop_index} IS {edge_label}]->" if direction == "OUT" else f"<-[e{hop_index} IS {edge_label}]-"
                match_parts.append(f"{relation}({alias} IS {next_label})")
            target_alias = aliases[-1]
            cte_name = f"path_{index}"
            columns = [f"r.{root_key} AS ROOT_ID"] + [f"{target_alias}.{name} AS {target_label}_{name}" for name in selected_names]
            ctes.append(f'''{cte_name} AS (
  SELECT * FROM GRAPH_TABLE(
    {graph_name}
    MATCH {''.join(match_parts)}
    COLUMNS ({', '.join(columns)})
  )
)''')
            joins.append(f"LEFT JOIN {cte_name} p{index} ON p{index}.ROOT_ID = r.ROOT_ID")
            outer_columns.extend(f"p{index}.{target_label}_{name}" for name in selected_names)
        if not joins:
            return None
        filter_alias = f"ROOT_{filter_property}"
        cte_sql = ",\n".join(ctes)
        sql = f'''WITH {cte_sql}
SELECT {', '.join(outer_columns)}
FROM root_node r
{' '.join(joins)}
WHERE r.{filter_alias} = '{filter_value}' '''
        return {
            "sql": sql,
            "selection": {
                "displayName": f"{root_label} → {' / '.join(target_labels)}",
                "tableName": "由实时 Property Graph 拓扑自动规划",
                "reason": f"根据问题选择起点 {root_label}、过滤属性 {filter_property}，并从知识地图搜索到目标节点路径。",
            },
        }

    @staticmethod
    def _build_supply_chain_graph_plan(
        question: str, topology: Dict[str, Any], conversation_context: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Return approved multi-ontology graph templates for high-value supply-chain questions.

        Quantities live in OUTBOUND_DETAIL rather than the graph edge, so the
        template first uses GRAPH_TABLE to establish the business relationship
        and then performs a read-only aggregate over that fact table.
        """
        normalized = (question or "").upper()
        current_question_text = (question or "").upper()
        code_search_text = f"{question or ''}\n{conversation_context or ''}".upper()
        nodes_by_label = {str(item.get("displayName") or "").upper(): item for item in (topology.get("nodes") or [])}
        graph_name = str(topology.get("graph_name") or "").upper()
        identifier_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
        if not identifier_pattern.fullmatch(graph_name):
            return None

        current_exact_identifier = re.search(r"\b(?:BOT|BATCH|CASE|PACK|PALLET|STACK)-[A-Z0-9_-]+\b", current_question_text)
        bottle_code_match = re.search(r"\bBOT-[A-Z0-9_-]+\b", current_question_text)
        if not bottle_code_match and not current_exact_identifier:
            bottle_code_match = re.search(r"\bBOT-[A-Z0-9_-]+\b", code_search_text)
        asks_code_chain = bottle_code_match and any(
            term in question for term in ("包码", "箱码", "托码", "垛码", "五码", "链路", "追溯")
        )
        if asks_code_chain:
            chain_specs = [
                ("BOTTLECODE", "b", "BOTTLE_ID", "BOTTLE_CODE", "bottle"),
                ("PACKCODE", "p", "PACK_ID", "PACK_CODE", "pack"),
                ("CASECODE", "c", "CASE_ID", "CASE_CODE", "case"),
                ("PALLETCODE", "pal", "PALLET_ID", "PALLET_CODE", "pallet"),
                ("STACKCODE", "s", "STACK_ID", "STACK_CODE", "stack"),
            ]
            if not all(
                nodes_by_label.get(label)
                and {key, code}.issubset({str(prop.get("property_name") or "").upper() for prop in (nodes_by_label[label].get("properties") or [])})
                for label, _alias, key, code, _prefix in chain_specs
            ):
                return None
            bottle_code = bottle_code_match.group(0)
            sql = f'''WITH code_chain AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name}
    MATCH (b IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(p IS PACKCODE)
          -[e2 IS GRAPH_LABEL]->(c IS CASECODE)
          -[e3 IS GRAPH_LABEL]->(pal IS PALLETCODE)
          -[e4 IS GRAPH_LABEL]->(s IS STACKCODE)
    COLUMNS (
      b.BOTTLE_ID AS BOTTLE_ID,
      b.BOTTLE_CODE AS BOTTLE_CODE,
      p.PACK_ID AS PACK_ID,
      p.PACK_CODE AS PACK_CODE,
      c.CASE_ID AS CASE_ID,
      c.CASE_CODE AS CASE_CODE,
      pal.PALLET_ID AS PALLET_ID,
      pal.PALLET_CODE AS PALLET_CODE,
      s.STACK_ID AS STACK_ID,
      s.STACK_CODE AS STACK_CODE,
      e1.RELATION_NAME AS BOTTLE_PACK_RELATION,
      e2.RELATION_NAME AS PACK_CASE_RELATION,
      e3.RELATION_NAME AS CASE_PALLET_RELATION,
      e4.RELATION_NAME AS PALLET_STACK_RELATION
    )
  )
)
SELECT BOTTLE_CODE, PACK_CODE, CASE_CODE, PALLET_CODE, STACK_CODE,
       BOTTLE_PACK_RELATION, PACK_CASE_RELATION,
       CASE_PALLET_RELATION, PALLET_STACK_RELATION
FROM code_chain
WHERE BOTTLE_CODE = '{bottle_code}' '''
            return {
                "sql": sql,
                "selection": {
                    "displayName": "瓶码 → 包码 → 箱码 → 托码 → 垛码",
                    "tableName": "BOTTLECODE → PACKCODE → CASECODE → PALLETCODE → STACKCODE",
                    "reason": f"识别到精确瓶码 {bottle_code} 和五层包装链路需求，使用四跳属性图路径并精确过滤。",
                },
            }

        asks_production_trace = bottle_code_match and any(
            term in question for term in ("生产", "产品", "批次", "产线", "工厂", "生产信息")
        )
        if asks_production_trace:
            production_specs = {
                "BOTTLECODE": {"BOTTLE_ID", "BOTTLE_CODE"},
                "PRODUCT": {"PRODUCT_ID", "SKU_CODE", "PRODUCT_NAME"},
                "PRODUCTIONBATCH": {"BATCH_ID", "BATCH_NO", "PRODUCTION_DATE", "QUALITY_STATUS"},
                "PRODUCTIONLINE": {"LINE_ID", "LINE_CODE", "LINE_NAME", "WORKSHOP"},
                "FACTORY": {"FACTORY_ID", "FACTORY_CODE", "FACTORY_NAME", "PROVINCE", "CITY"},
            }
            if not all(
                nodes_by_label.get(label)
                and required.issubset({str(prop.get("property_name") or "").upper() for prop in (nodes_by_label[label].get("properties") or [])})
                for label, required in production_specs.items()
            ):
                return None
            bottle_code = bottle_code_match.group(0)
            sql = f'''WITH bottle_product AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name}
    MATCH (b IS BOTTLECODE)-[e IS GRAPH_LABEL]->(p IS PRODUCT)
    COLUMNS (
      b.BOTTLE_ID AS BOTTLE_ID,
      b.BOTTLE_CODE AS BOTTLE_CODE,
      p.SKU_CODE AS SKU_CODE,
      p.PRODUCT_NAME AS PRODUCT_NAME,
      e.RELATION_NAME AS BOTTLE_PRODUCT_RELATION
    )
  )
), bottle_batch AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name}
    MATCH (b IS BOTTLECODE)-[e IS GRAPH_LABEL]->(pb IS PRODUCTIONBATCH)
    COLUMNS (
      b.BOTTLE_ID AS BOTTLE_ID,
      b.BOTTLE_CODE AS BOTTLE_CODE,
      pb.BATCH_ID AS BATCH_ID,
      pb.BATCH_NO AS BATCH_NO,
      pb.PRODUCTION_DATE AS PRODUCTION_DATE,
      pb.QUALITY_STATUS AS QUALITY_STATUS,
      e.RELATION_NAME AS BOTTLE_BATCH_RELATION
    )
  )
), bottle_line AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name}
    MATCH (b IS BOTTLECODE)-[e IS GRAPH_LABEL]->(l IS PRODUCTIONLINE)
    COLUMNS (
      b.BOTTLE_ID AS BOTTLE_ID,
      l.LINE_CODE AS LINE_CODE,
      l.LINE_NAME AS LINE_NAME,
      l.WORKSHOP AS WORKSHOP,
      e.RELATION_NAME AS BOTTLE_LINE_RELATION
    )
  )
), batch_factory AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name}
    MATCH (pb IS PRODUCTIONBATCH)-[e IS GRAPH_LABEL]->(f IS FACTORY)
    COLUMNS (
      pb.BATCH_ID AS BATCH_ID,
      f.FACTORY_CODE AS FACTORY_CODE,
      f.FACTORY_NAME AS FACTORY_NAME,
      f.PROVINCE AS FACTORY_PROVINCE,
      f.CITY AS FACTORY_CITY,
      e.RELATION_NAME AS BATCH_FACTORY_RELATION
    )
  )
)
SELECT bp.BOTTLE_CODE,
       bp.SKU_CODE, bp.PRODUCT_NAME,
       bb.BATCH_NO, bb.PRODUCTION_DATE, bb.QUALITY_STATUS,
       bl.LINE_CODE, bl.LINE_NAME, bl.WORKSHOP,
       bf.FACTORY_CODE, bf.FACTORY_NAME, bf.FACTORY_PROVINCE, bf.FACTORY_CITY,
       bp.BOTTLE_PRODUCT_RELATION, bb.BOTTLE_BATCH_RELATION,
       bl.BOTTLE_LINE_RELATION, bf.BATCH_FACTORY_RELATION
FROM bottle_product bp
JOIN bottle_batch bb ON bb.BOTTLE_ID = bp.BOTTLE_ID
JOIN bottle_line bl ON bl.BOTTLE_ID = bp.BOTTLE_ID
JOIN batch_factory bf ON bf.BATCH_ID = bb.BATCH_ID
WHERE bp.BOTTLE_CODE = '{bottle_code}' '''
            return {
                "sql": sql,
                "selection": {
                    "displayName": "瓶码 → 产品 / 批次 / 产线 / 工厂",
                    "tableName": "BOTTLECODE → PRODUCT；BOTTLECODE → PRODUCTIONBATCH → FACTORY；BOTTLECODE → PRODUCTIONLINE",
                    "reason": f"识别到精确瓶码 {bottle_code} 和生产追溯需求，按图关系查询产品、批次、产线和工厂明细。",
                },
            }

        asks_outbound_distributor = ("出库" in question or "OUTBOUND" in normalized) and ("经销商" in question or "DISTRIBUTOR" in normalized)
        if not asks_outbound_distributor:
            return None
        outbound = nodes_by_label.get("OUTBOUNDORDER")
        distributor = nodes_by_label.get("DISTRIBUTOR")
        if not outbound or not distributor:
            return None
        outbound_properties = {str(item.get("property_name") or "").upper() for item in (outbound.get("properties") or [])}
        distributor_properties = {str(item.get("property_name") or "").upper() for item in (distributor.get("properties") or [])}
        if not {"OUTBOUND_ID", "OUTBOUND_NO", "OUTBOUND_TIME"}.issubset(outbound_properties) or not {"DISTRIBUTOR_ID", "DISTRIBUTOR_NAME"}.issubset(distributor_properties):
            return None
        month_filter = "\n  AND od.OUTBOUND_TIME >= TRUNC(SYSDATE, 'MM')\n  AND od.OUTBOUND_TIME < ADD_MONTHS(TRUNC(SYSDATE, 'MM'), 1)" if "本月" in question else ""
        sql = f'''WITH outbound_distributor AS (
  SELECT *
  FROM GRAPH_TABLE(
    {graph_name}
    MATCH (o IS OUTBOUNDORDER)-[e IS GRAPH_LABEL]->(d IS DISTRIBUTOR)
    COLUMNS (
      o.OUTBOUND_ID AS OUTBOUND_ID,
      o.OUTBOUND_NO AS OUTBOUND_NO,
      o.OUTBOUND_TIME AS OUTBOUND_TIME,
      o.OUTBOUND_TYPE AS OUTBOUND_TYPE,
      o.STATUS AS OUTBOUND_STATUS,
      d.DISTRIBUTOR_ID AS DISTRIBUTOR_ID,
      d.DISTRIBUTOR_CODE AS DISTRIBUTOR_CODE,
      d.DISTRIBUTOR_NAME AS DISTRIBUTOR_NAME,
      e.RELATION_NAME AS RELATION_NAME
    )
  )
)
SELECT od.OUTBOUND_NO,
       od.OUTBOUND_TIME,
       od.OUTBOUND_TYPE,
       od.OUTBOUND_STATUS,
       od.DISTRIBUTOR_CODE,
       od.DISTRIBUTOR_NAME,
       SUM(NVL(obd.QUANTITY, 0)) AS OUTBOUND_QUANTITY
FROM outbound_distributor od
LEFT JOIN OUTBOUND_DETAIL obd ON obd.OUTBOUND_ID = od.OUTBOUND_ID
WHERE od.RELATION_NAME = '发往'{month_filter}
GROUP BY od.OUTBOUND_NO, od.OUTBOUND_TIME, od.OUTBOUND_TYPE, od.OUTBOUND_STATUS,
         od.DISTRIBUTOR_CODE, od.DISTRIBUTOR_NAME
ORDER BY od.OUTBOUND_TIME DESC, od.DISTRIBUTOR_NAME'''
        return {
            "sql": sql,
            "selection": {
                "displayName": "出库单 → 经销商",
                "tableName": f"{outbound.get('tableName')} → {distributor.get('tableName')}；OUTBOUND_DETAIL（数量事实）",
                "reason": "问题同时涉及出库单、经销商与出库数量，需通过图关系定位两端本体并汇总出库明细。",
            },
        }

    async def _select_managed_skill_table(
        self,
        *,
        skill_markdown: str,
        source_id: str,
        schema: Optional[str],
        question: str,
        llm_config: SysLLMConfig,
    ) -> Dict[str, str]:
        table_list = self.source_service.get_remote_tables(source_id=source_id, schema=schema)
        candidates = (table_list.get("tables") or [])[:150]
        if not candidates:
            raise ValueError("所选数据源 Schema 中没有可供 Agent 分析的数据表")
        if len(candidates) == 1:
            item = candidates[0]
            return {"owner": item["owner"], "table_name": item["table_name"], "reason": "当前 Schema 仅有一个可访问数据对象。"}
        catalog = json.dumps(
            [{"table_name": item["table_name"], "comments": item.get("comments") or "", "num_rows": item.get("num_rows") or 0} for item in candidates],
            ensure_ascii=False,
        )
        select_prompt = f"""根据用户上传 Skill 与用户问题，从候选数据对象中选择最适合进行首次只读采样分析的一张表。
只返回 JSON 对象，格式严格为 {{"table_name":"候选表名","reason":"不超过50字的选择理由"}}。不得选择候选列表以外的表，不得输出 SQL。

Skill：
{skill_markdown[:12000]}

用户问题：{question or '请基于当前数据给出分析结论。'}

候选表：{catalog}
"""
        raw = await self.llm_service.call_llm(
            "你是数据对象选择器，只能从候选表中返回一个精确表名。", select_prompt, llm_config,
            timeout_override=max(llm_config.timeout, 60),
        )
        selection = self.llm_service._extract_json_object(raw or "") or {}
        selected_name = str(selection.get("table_name") or "").strip()
        selection_reason = str(selection.get("reason") or "").strip()
        candidate_by_name = {(item["table_name"] or "").upper(): item for item in candidates}
        if selected_name.upper() in candidate_by_name:
            item = candidate_by_name[selected_name.upper()]
            return {"owner": item["owner"], "table_name": item["table_name"], "reason": selection_reason[:200] or "与 Skill 和问题匹配。"}
        raise ValueError("Agent 未能从数据源候选对象中选择有效表，请在对话中补充更明确的问题或检查 Skill 指令")

    @staticmethod
    def _normalize_conversation_history(history: Any, limit: Optional[int] = 5) -> List[Dict[str, str]]:
        """Normalize dialogue messages; use a bound only for the context sent to the model."""
        normalized: List[Dict[str, str]] = []
        items = history or []
        if limit is not None:
            items = items[-limit:]
        for item in items:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                normalized.append({"role": role, "content": content[:6000]})
        return normalized

    @staticmethod
    def _format_conversation_history(history: List[Dict[str, str]]) -> str:
        role_labels = {"user": "用户", "assistant": "Agent"}
        return "\n".join(f"{role_labels.get(item['role'], item['role'])}：{item['content']}" for item in history)

    @staticmethod
    def _read_managed_skill_files(skill: SysManagedAgentSkill) -> Dict[str, str]:
        try:
            with ZipFile(BytesIO(skill.package_content), "r") as archive:
                files = {}
                for info in archive.infolist():
                    path = info.filename.replace("\\", "/")
                    if path == "SKILL.md" or path.startswith("references/"):
                        files[path] = archive.read(info).decode("utf-8-sig")
        except (BadZipFile, UnicodeDecodeError) as exc:
            raise ValueError(f"无法读取托管 Skill 包：{exc}") from exc
        if "SKILL.md" not in files:
            raise ValueError("托管 Skill 包缺少 SKILL.md")
        return files

    @staticmethod
    def _safe_uploaded_filename(filename: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", (filename or "agent_skill.zip"))[:255] or "agent_skill.zip"

    def _extract_skill_metadata(self, skill_markdown: str, filename: str) -> Dict[str, str]:
        frontmatter = re.match(r"^---\s*\n(.*?)\n---", skill_markdown or "", flags=re.DOTALL)
        values: Dict[str, str] = {}
        if frontmatter:
            for line in frontmatter.group(1).splitlines():
                match = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
                if match:
                    values[match.group(1).lower()] = match.group(2).strip().strip('"\'')
        heading = re.search(r"^#\s+(.+?)\s*$", skill_markdown or "", flags=re.MULTILINE)
        skill_name = values.get("name") or (heading.group(1).strip() if heading else "") or filename.rsplit(".", 1)[0]
        skill_desc = values.get("description") or ""
        if not skill_desc:
            paragraphs = [line.strip() for line in (skill_markdown or "").splitlines() if line.strip() and not line.lstrip().startswith(("#", "---"))]
            skill_desc = next((line for line in paragraphs if len(line) > 8), "未提供技能说明")
        return {"skill_name": skill_name[:200], "skill_desc": skill_desc[:2000]}

    @staticmethod
    def _serialize_managed_skill(skill: SysManagedAgentSkill) -> Dict[str, Any]:
        return {
            "managed_skill_id": skill.managed_skill_id,
            "domain_id": skill.domain_id,
            "skill_name": skill.skill_name,
            "skill_desc": skill.skill_desc,
            "package_filename": skill.package_filename,
            "package_size": skill.package_size or 0,
            "file_count": skill.file_count or 0,
            "use_count": skill.use_count or 0,
            "status": skill.status,
            "uploaded_by": skill.uploaded_by,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
        }

    async def build_skill_package(self, skill_id: str) -> Dict[str, Any]:
        """Build a portable, multi-file Agent Skill package from live graph metadata."""
        skill = self.db.query(SysAgentSkill).filter(SysAgentSkill.skill_id == skill_id).first()
        if not skill:
            raise ValueError("技能不存在")
        domain, process, entity, properties, relations = self._load_skill_dependencies(
            domain_id=skill.domain_id,
            process_id=skill.process_id,
            source_id=skill.source_id,
            property_graph_name=skill.property_graph_name,
        )
        llm_config = self._get_llm_config(skill.llm_config_id, purpose="技能包生成")
        topology = self.source_service.get_remote_property_graph_topology(
            skill.source_id,
            skill.property_graph_name,
            schema=getattr(entity, "schema", None),
        )
        skill_context = self._safe_json_loads(skill.context_json, {})
        package_files = await self._generate_skill_package_files(
            domain=domain,
            process=process,
            entity=entity,
            skill=skill,
            llm_config=llm_config,
            topology=topology,
            skill_context=skill_context,
        )
        archive = BytesIO()
        with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
            for path, content in package_files.items():
                zip_file.writestr(path, content)
        safe_name = self._safe_package_name(skill.skill_name)
        return {
            "filename": f"{safe_name}.zip",
            "content": archive.getvalue(),
            "files": list(package_files.keys()),
        }

    async def _generate_skill_package_files(
        self,
        *,
        domain: SysDomain,
        process: SysProcessDef,
        entity: Any,
        skill: SysAgentSkill,
        llm_config: SysLLMConfig,
        topology: Dict[str, Any],
        skill_context: Dict[str, Any],
    ) -> Dict[str, str]:
        graph_reference = self._build_graph_reference(topology)
        flow_reference = self._build_flow_reference(process)
        semantics = (skill_context or {}).get("analysis_semantics") or {}
        ontology_model = (skill_context or {}).get("ontology_model") or {}
        ontology_graph_binding = (skill_context or {}).get("ontology_graph_binding") or {}
        metric_reference = self._build_metric_reference(semantics.get("metrics") or [])
        rule_reference = self._build_rule_reference(semantics.get("rules") or [])
        activity_reference = self._build_activity_reference(semantics.get("activities") or [])
        ontology_model_reference = self._build_ontology_model_reference(ontology_model)
        ontology_graph_binding_reference = self._build_ontology_graph_binding_reference(ontology_graph_binding)
        strategy_reference = self._build_analysis_strategy_reference(skill, skill_context, topology)
        execution_contract_reference = self._build_execution_contract_reference(skill_context, topology)
        fallback = {
            "SKILL.md": self._build_skill_markdown(domain, process, entity, skill, topology, skill_context),
            "references/property-graph.md": graph_reference,
            "references/ontology-model.json": ontology_model_reference,
            "references/ontology-graph-binding.json": ontology_graph_binding_reference,
            "references/analysis-flow.md": flow_reference,
            "references/metric-catalog.json": metric_reference,
            "references/rule-catalog.json": rule_reference,
            "references/activity-playbook.json": activity_reference,
            "references/analysis-strategy.md": strategy_reference,
            "references/execution-contract.json": execution_contract_reference,
        }
        system_prompt = """你是 Agent Skill 打包专家。根据用户给出的技能配置、业务流程和 Oracle Property Graph 实时拓扑，生成可直接被 Agent 加载的技能包文件。

必须遵守：
1. 输出严格 JSON，格式为 {"files":[{"path":"SKILL.md","content":"..."}]}。
2. 必须包含 SKILL.md；可额外生成 references/*.md、references/*.sql、references/*.json 文件。
3. SKILL.md 使用标准 Agent Skill 风格：YAML frontmatter（name、description）、适用范围、输入、执行工作流、只读安全约束、输出格式和限制。
4. 数据库只允许 SELECT / WITH ... SELECT / GRAPH_TABLE 查询；严禁 DDL、DML、PL/SQL、权限操作、凭据及任何密码。
5. 所有图标签、关系、顶点属性必须来自提供的实时拓扑；不要臆造数据库对象。对于图形结果，要求返回 SOURCE_ID、TARGET_ID、RELATION_NAME，并为不同类型 ID 加前缀。
6. 文件路径必须是相对路径，不能包含 ..；总文件数不超过 8 个，每个文件不超过 24000 字符。
7. 用中文编写说明和规则，SQL 保持 Oracle 语法。
8. 必须把给定的指标定义、业务规则、业务活动和图语义绑定写入 Skill；不能只描述图拓扑。"""
        payload = {
            "skill_config": {
                "skill_name": skill.skill_name,
                "skill_desc": skill.skill_desc,
                "analysis_goal": skill.analysis_goal,
                "execution_rules": skill.execution_rules,
                "output_requirements": skill.output_requirements,
            },
            "domain": {"name": domain.domain_name, "description": domain.domain_desc},
            "process": {
                "name": process.process_name,
                "description": process.process_desc,
                "steps": self._extract_process_steps(process.process_json),
            },
            "property_graph": self._compact_topology(topology),
            "ontology_model": ontology_model,
            "ontology_graph_binding": ontology_graph_binding,
            "analysis_semantics": semantics,
            "required_references": {
                "property_graph_reference": graph_reference,
                "ontology_model_reference": ontology_model_reference,
                "ontology_graph_binding_reference": ontology_graph_binding_reference,
                "analysis_flow_reference": flow_reference,
                "metric_reference": metric_reference,
                "rule_reference": rule_reference,
                "activity_reference": activity_reference,
                "analysis_strategy_reference": strategy_reference,
            },
        }
        try:
            result_text = await self.llm_service.call_llm(
                system_prompt,
                json.dumps(payload, ensure_ascii=False, indent=2),
                llm_config,
                timeout_override=max(llm_config.timeout, 180),
            )
            parsed = self._safe_json_loads(self.llm_service._extract_json_object(result_text), {})
            files = self._normalize_skill_package_files(parsed)
            if "SKILL.md" in files:
                # Real database facts are always included even if the model omitted its references.
                files.setdefault("references/property-graph.md", graph_reference)
                files.setdefault("references/ontology-model.json", ontology_model_reference)
                files.setdefault("references/ontology-graph-binding.json", ontology_graph_binding_reference)
                files.setdefault("references/analysis-flow.md", flow_reference)
                files.setdefault("references/metric-catalog.json", metric_reference)
                files.setdefault("references/rule-catalog.json", rule_reference)
                files.setdefault("references/activity-playbook.json", activity_reference)
                files.setdefault("references/analysis-strategy.md", strategy_reference)
                files.setdefault("references/execution-contract.json", execution_contract_reference)
                return files
        except Exception:
            pass
        return fallback

    @staticmethod
    def _safe_package_name(value: str) -> str:
        cleaned = "".join(char if char.isascii() and (char.isalnum() or char in ("-", "_")) else "_" for char in (value or "agent_skill"))
        return (cleaned.strip("_") or "agent_skill")[:80]

    def _normalize_skill_package_files(self, raw: Any) -> Dict[str, str]:
        files = raw.get("files") if isinstance(raw, dict) else None
        if not isinstance(files, list):
            return {}
        normalized: Dict[str, str] = {}
        allowed_suffixes = (".md", ".sql", ".json", ".txt")
        for item in files[:8]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").replace("\\", "/").strip().lstrip("/")
            content = str(item.get("content") or "").strip()
            if not path or ".." in path.split("/") or not path.endswith(allowed_suffixes) or len(content) > 24000:
                continue
            if path != "SKILL.md" and not path.startswith("references/"):
                continue
            normalized[path] = content
        return normalized

    def _compact_topology(self, topology: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "graph_name": topology.get("graph_name"),
            "schema": topology.get("schema"),
            "nodes": [
                {
                    "label": node.get("name"),
                    "display_name": node.get("displayName"),
                    "table": node.get("tableName"),
                    "properties": [
                        {"name": prop.get("property_name"), "type": prop.get("data_type"), "primary_key": prop.get("is_primary_key")}
                        for prop in (node.get("properties") or [])[:20]
                    ],
                }
                for node in (topology.get("nodes") or [])[:30]
            ],
            "edges": [
                {
                    "label": edge.get("name"),
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "table": edge.get("tableName"),
                }
                for edge in (topology.get("edges") or [])[:60]
            ],
        }

    def _build_graph_reference(self, topology: Dict[str, Any]) -> str:
        compact = self._compact_topology(topology)
        lines = [
            "# Oracle Property Graph 实时参考",
            "",
            f"- Schema：`{compact.get('schema') or '当前 Schema'}`",
            f"- Property Graph：`{compact.get('graph_name') or '未识别'}`",
            "- 仅允许使用只读 `SELECT` 或 `WITH ... SELECT`；图查询使用 `GRAPH_TABLE`。",
            "",
            "## 顶点标签",
        ]
        for node in compact["nodes"]:
            props = ", ".join(item.get("name") or "" for item in node["properties"][:12]) or "无属性元数据"
            lines.append(f"- `{node.get('label')}`：底表 `{node.get('table')}`；属性：{props}")
        lines.extend(["", "## 边关系"])
        for edge in compact["edges"]:
            lines.append(f"- `{edge.get('label')}`：`{edge.get('source')}` → `{edge.get('target')}`；边表 `{edge.get('table')}`")
        lines.extend([
            "",
            "## 图形结果约定",
            "如需返回图形数据，必须输出 `SOURCE_ID`、`TARGET_ID`、`RELATION_NAME`，并使用 `标签:主键` 形式避免跨节点表主键冲突。",
        ])
        return "\n".join(lines)

    def _build_flow_reference(self, process: SysProcessDef) -> str:
        lines = ["# 分析流程参考", "", f"流程名称：{process.process_name}", process.process_desc or ""]
        for step in self._extract_process_steps(process.process_json):
            lines.append(f"{step['step_no']}. {step['label']}（{step['type_label']}）{('：' + step['desc']) if step['desc'] else ''}")
        return "\n".join(lines)

    def _build_ontology_model_reference(self, ontology_model: Dict[str, Any]) -> str:
        return json.dumps({
            "ontology_model": ontology_model,
            "usage": "Agent 可从这里读取平台定义的本体对象、属性、关系及其业务语义，不得臆造未定义对象。",
        }, ensure_ascii=False, indent=2)

    def _build_ontology_graph_binding_reference(self, ontology_graph_binding: Dict[str, Any]) -> str:
        return json.dumps({
            "ontology_graph_binding": ontology_graph_binding,
            "usage": "Agent 可从这里读取平台本体定义与已部署 Property Graph 之间的标签、属性、关系绑定。",
        }, ensure_ascii=False, indent=2)

    def _build_metric_reference(self, metrics: List[Dict[str, Any]]) -> str:
        return json.dumps({
            "metrics": metrics,
            "usage": "Agent 只能使用这里出现的指标定义、口径、聚合方式和阈值配置。",
        }, ensure_ascii=False, indent=2)

    def _build_rule_reference(self, rules: List[Dict[str, Any]]) -> str:
        return json.dumps({
            "rules": rules,
            "usage": "Agent 只能依据这里的规则定义做判定、分级、派生或预警解释。",
        }, ensure_ascii=False, indent=2)

    def _build_activity_reference(self, activities: List[Dict[str, Any]]) -> str:
        return json.dumps({
            "activities": activities,
            "usage": "Agent 只能从这里的活动中选择建议动作；默认只建议，不直接执行。",
        }, ensure_ascii=False, indent=2)

    def _build_execution_contract_reference(self, skill_context: Dict[str, Any], topology: Dict[str, Any]) -> str:
        analysis_semantics = (skill_context or {}).get("analysis_semantics") or {}
        ontology_graph_binding = (skill_context or {}).get("ontology_graph_binding") or {}
        ontology_model = (skill_context or {}).get("ontology_model") or {}
        matched_labels = [
            self._normalize_label_name(item.get("graph_labels", [None])[0] if isinstance(item.get("graph_labels"), list) else None)
            for item in (ontology_graph_binding.get("entity_bindings") or [])
            if item.get("matched")
        ]
        matched_labels = [item for item in matched_labels if item]
        entry_entity_ids = analysis_semantics.get("analysis_profile", {}).get("entry_entity_ids") or []
        entity_bindings_by_id = {
            item.get("entity_id"): item for item in (ontology_graph_binding.get("entity_bindings") or [])
        }
        entry_objects = []
        for entity_id in entry_entity_ids:
            binding = entity_bindings_by_id.get(entity_id) or {}
            for label in binding.get("graph_labels") or []:
                normalized = self._normalize_label_name(label)
                if normalized and normalized not in entry_objects:
                    entry_objects.append(normalized)
        if not entry_objects:
            entry_objects = matched_labels[:6]
        target_objects = matched_labels[:12]
        entity_aliases_by_label: Dict[str, List[str]] = {}
        property_aliases_by_label: Dict[str, List[str]] = {}
        entities_by_id = {
            item.get("entity_id"): item
            for item in (ontology_model.get("entities") or [])
            if item.get("entity_id")
        }
        for binding in (ontology_graph_binding.get("entity_bindings") or []):
            if not binding.get("matched"):
                continue
            aliases = [
                str(binding.get("entity_display_name") or "").strip(),
                str(binding.get("entity_name") or "").strip(),
            ]
            entity_meta = entities_by_id.get(binding.get("entity_id")) or {}
            aliases.extend([
                str(entity_meta.get("entity_display_name") or "").strip(),
                str(entity_meta.get("entity_name") or "").strip(),
            ])
            for label in binding.get("graph_labels") or []:
                normalized = self._normalize_label_name(label)
                if normalized:
                    entity_aliases_by_label.setdefault(normalized, [])
                    entity_aliases_by_label[normalized].extend([item for item in aliases if item])
        for binding in (ontology_graph_binding.get("property_bindings") or []):
            aliases = [
                str(binding.get("property_display_name") or "").strip(),
                str(binding.get("property_name") or "").strip(),
            ]
            for matched in (binding.get("matched_columns") or []):
                label = self._normalize_label_name(matched.get("graph_label"))
                if label:
                    property_aliases_by_label.setdefault(label, [])
                    property_aliases_by_label[label].extend([item for item in aliases if item])
        required_display_properties: Dict[str, List[str]] = {}
        time_dimensions: Dict[str, List[str]] = {}
        for metric in analysis_semantics.get("metrics") or []:
            entity_id = metric.get("entity_id")
            binding = entity_bindings_by_id.get(entity_id) or {}
            for label in binding.get("graph_labels") or []:
                normalized = self._normalize_label_name(label)
                if normalized:
                    required_display_properties.setdefault(normalized, [])
        nodes_by_label = {
            self._normalize_label_name(node.get("displayName") or node.get("name")): node
            for node in (topology.get("nodes") or [])
        }
        for label in target_objects or entry_objects:
            node = nodes_by_label.get(self._normalize_label_name(label)) or {}
            candidates = self._time_dimension_candidates_for_label(
                label=self._normalize_label_name(label),
                node=node,
                execution_contract={},
            )
            if candidates:
                time_dimensions[self._normalize_label_name(label)] = candidates
        query_modes = ["single_node", "path_expand"]
        if analysis_semantics.get("metrics"):
            query_modes.append("fact_aggregate")
            query_modes.append("group_by_object")
            query_modes.append("group_by_object_time_window")
            query_modes.append("group_by_time_window")
            query_modes.append("metric_formula")
            query_modes.append("filter_aggregate_result")
            query_modes.append("order_and_limit")
        if analysis_semantics.get("rules"):
            query_modes.append("apply_rules")
        all_labels = list(dict.fromkeys(entry_objects + (target_objects or [])))
        object_aliases = self._build_reference_object_aliases(all_labels, entity_aliases_by_label)
        property_aliases = self._build_reference_property_aliases(
            labels=all_labels,
            topology=topology,
            explicit_aliases=property_aliases_by_label,
        )
        explicit_relation_aliases: Dict[str, List[str]] = {}
        for binding in (ontology_graph_binding.get("relation_bindings") or []):
            aliases = [
                str(binding.get("relation_name") or "").strip(),
                str(binding.get("source_entity_name") or "").strip(),
                str(binding.get("target_entity_name") or "").strip(),
            ]
            for edge in (binding.get("matched_edges") or []):
                relation_key = self._reference_relation_key(
                    self._normalize_label_name(edge.get("graph_source_label")),
                    self._normalize_label_name(edge.get("graph_target_label")),
                )
                if relation_key != "->":
                    explicit_relation_aliases.setdefault(relation_key, [])
                    explicit_relation_aliases[relation_key].extend([item for item in aliases if item])
        contract_payload = {
            "entry_objects": entry_objects,
            "target_objects": target_objects or [
                self._normalize_label_name(node.get("displayName") or node.get("name"))
                for node in (topology.get("nodes") or [])[:12]
            ],
            "query_modes": query_modes,
            "preferred_paths": [],
            "required_display_properties": required_display_properties,
            "time_dimensions": time_dimensions,
            "object_aliases": object_aliases,
            "property_aliases": property_aliases,
            "relation_aliases": self._build_reference_relation_aliases(
                topology=topology,
                object_aliases=object_aliases,
                explicit_aliases=explicit_relation_aliases,
            ),
            "forbidden_properties": ["RAW_JSON", "LARGE_CLOB"],
        }
        contract_payload["reference_patterns"] = self._default_managed_skill_reference_patterns(contract_payload)
        return json.dumps(contract_payload, ensure_ascii=False, indent=2)

    def _build_analysis_strategy_reference(self, skill: SysAgentSkill, skill_context: Dict[str, Any], topology: Dict[str, Any]) -> str:
        analysis_profile = (skill_context or {}).get("analysis_semantics", {}).get("analysis_profile", {})
        graph_map = (skill_context or {}).get("analysis_semantics", {}).get("graph_semantic_map", {})
        ontology_model = (skill_context or {}).get("ontology_model") or {}
        ontology_graph_binding = (skill_context or {}).get("ontology_graph_binding") or {}
        lines = [
            "# 分析策略参考",
            "",
            f"- Skill：{skill.skill_name}",
            f"- 分析场景：{analysis_profile.get('analysis_scenario_name') or '通用图探索分析'}",
            f"- 分析模式：{', '.join(analysis_profile.get('analysis_modes') or DEFAULT_SKILL_ANALYSIS_MODES)}",
            f"- 默认时间窗：{analysis_profile.get('default_time_window') or '7D'}",
            f"- 最大路径跳数：{analysis_profile.get('max_path_depth') or 2}",
            f"- 是否输出活动建议：{'是' if analysis_profile.get('enable_activity_recommendation', True) else '否'}",
            f"- Property Graph：`{topology.get('schema')}.{topology.get('graph_name')}`",
            "",
            "## 执行原则",
            "1. 先识别问题属于缺陷分析、根因分析、影响推断还是追溯分析。",
            "2. 先用 Property Graph 定位入口节点与关联路径，再使用指标做聚合和异常解释。",
            "3. 只有命中规则时才能给出强结论；证据不足时必须输出疑似或待人工确认。",
            "4. 活动只作为建议动作输出，不直接执行写操作、通知或流程调用。",
            "",
            "## 图语义绑定摘要",
            f"- 本体对象数：{ontology_model.get('entity_count') or len(ontology_model.get('entities') or [])}",
            f"- 本体关系数：{ontology_model.get('relation_count') or len(ontology_model.get('relations') or [])}",
            f"- 本体对象绑定数：{len(ontology_graph_binding.get('entity_bindings') or [])}",
            f"- 本体属性绑定数：{len(ontology_graph_binding.get('property_bindings') or [])}",
            f"- 本体关系绑定数：{len(ontology_graph_binding.get('relation_bindings') or [])}",
            f"- 指标绑定数：{len(graph_map.get('metric_bindings') or [])}",
            f"- 规则绑定数：{len(graph_map.get('rule_bindings') or [])}",
            f"- 活动绑定数：{len(graph_map.get('activity_bindings') or [])}",
        ]
        return "\n".join(lines)

    def _build_skill_markdown(
        self,
        domain: SysDomain,
        process: SysProcessDef,
        entity: Any,
        skill: SysAgentSkill,
        topology: Dict[str, Any],
        skill_context: Dict[str, Any],
    ) -> str:
        steps = self._extract_process_steps(process.process_json)
        step_text = "\n".join(f"{item['step_no']}. {item['label']}（{item['type_label']}）" for item in steps) or "1. 校验输入并准备分析上下文。"
        graph_name = topology.get("graph_name") or entity.entity_name
        analysis_profile = (skill_context or {}).get("analysis_semantics", {}).get("analysis_profile", {})
        analysis_modes = "、".join(analysis_profile.get("analysis_modes") or DEFAULT_SKILL_ANALYSIS_MODES)
        ontology_model = (skill_context or {}).get("ontology_model") or {}
        ontology_graph_binding = (skill_context or {}).get("ontology_graph_binding") or {}
        metrics = (skill_context or {}).get("analysis_semantics", {}).get("metrics") or []
        rules = (skill_context or {}).get("analysis_semantics", {}).get("rules") or []
        activities = (skill_context or {}).get("analysis_semantics", {}).get("activities") or []
        metric_names = "、".join(item.get("metric_name") or item.get("metric_code") or "" for item in metrics[:12]) or "无已选择指标"
        rule_names = "、".join(item.get("rule_name") or "" for item in rules[:12]) or "无已选择规则"
        activity_names = "、".join(item.get("activity_name") or "" for item in activities[:12]) or "无已选择活动"
        ontology_entity_count = ontology_model.get("entity_count") or len(ontology_model.get("entities") or [])
        ontology_relation_count = ontology_model.get("relation_count") or len(ontology_model.get("relations") or [])
        matched_entity_count = sum(1 for item in (ontology_graph_binding.get("entity_bindings") or []) if item.get("matched"))
        return f"""---
name: {self._safe_package_name(skill.skill_name).lower()}
description: {skill.skill_desc or f'面向{domain.domain_name}的{skill.skill_name}。'}
---

# {skill.skill_name}

## 分析目标

{skill.analysis_goal or f'围绕 Oracle Property Graph `{graph_name}` 完成结构化分析。'}

## 适用范围与输入

- 分析域：{domain.domain_name}
- Oracle Property Graph：`{graph_name}`
- 分析场景：{analysis_profile.get('analysis_scenario_name') or '通用图探索分析'}
- 分析模式：{analysis_modes}
- 默认时间窗：{analysis_profile.get('default_time_window') or '7D'}
- 最大路径跳数：{analysis_profile.get('max_path_depth') or 2}
- 使用用户提供的精确业务标识（如码、批次、单据）作为查询入口；标识无法命中时如实说明，不做无边界检索。

## 执行工作流

{step_text}

## 执行规则

{skill.execution_rules or '先校验输入和图谱对象，再以最小范围只读查询取得证据；数据缺失时明确限制。'}

## 安全约束

- 仅允许 `SELECT` 或 `WITH ... SELECT`，图查询使用 `GRAPH_TABLE`。
- 禁止 DDL、DML、PL/SQL、权限操作、凭据和密码。
- 先参考 `references/ontology-model.json` 理解平台定义的本体对象、属性、关系语义。
- 再参考 `references/ontology-graph-binding.json` 确认本体定义如何映射到当前已部署 Property Graph。
- 只使用 `references/property-graph.md` 中存在的图标签、关系和属性；不要臆造对象。
- 参考 `references/execution-contract.json` 中的入口对象、目标对象、查询模式和字段约束生成受控查询计划。
- 只使用 `references/metric-catalog.json` 中的指标口径做聚合或趋势解释。
- 只使用 `references/rule-catalog.json` 中的规则做异常判定、风险分级和派生结论。
- 只使用 `references/activity-playbook.json` 中的活动输出建议动作；默认不直接执行活动。

## 已加载本体

- 本体对象数：{ontology_entity_count}
- 本体关系数：{ontology_relation_count}
- 已映射到当前 Property Graph 的对象数：{matched_entity_count}

## 已加载业务语义

- 核心指标：{metric_names}
- 关键规则：{rule_names}
- 可建议活动：{activity_names}

## 输出要求

{skill.output_requirements or '输出结论、证据、异常或限制、建议动作，以及必要时使用的只读 SQL。'}

## 参考文件

- `references/ontology-model.json`：平台定义的本体对象、属性、关系和属性业务语义。
- `references/ontology-graph-binding.json`：平台本体定义与当前 Property Graph 的标签、属性、关系绑定。
- `references/property-graph.md`：从数据库实时读取的图谱拓扑。
- `references/analysis-flow.md`：当前技能配置的分析流程。
- `references/metric-catalog.json`：当前技能允许使用的指标定义。
- `references/rule-catalog.json`：当前技能允许使用的业务规则。
- `references/activity-playbook.json`：当前技能允许输出的业务活动建议。
- `references/analysis-strategy.md`：当前技能的分析策略、限制和图语义绑定摘要。
- `references/execution-contract.json`：当前技能的入口对象、目标对象、查询模式和受控字段约束。
"""

    async def test_skill(self, skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        skill = self.db.query(SysAgentSkill).filter(SysAgentSkill.skill_id == skill_id).first()
        if not skill:
            raise ValueError("技能不存在")
        llm_config = self._get_llm_config(payload.get("llm_config_id") or skill.llm_config_id, purpose="智能体测试")

        context = self._safe_json_loads(skill.context_json, {})
        process_steps = self._extract_process_steps(context.get("process", {}).get("process_json"))
        entity_context = context.get("entity", {})
        analysis_semantics = context.get("analysis_semantics") or {}
        table_detail = self.source_service.get_remote_table_detail(
            source_id=payload["source_id"],
            table_name=payload["graph_table"],
            schema=payload.get("schema"),
            sample_limit=max(1, min(int(payload.get("sample_limit") or 5), 10)),
        )
        source = self.db.query(SysDataSource).filter(SysDataSource.source_id == payload["source_id"]).first()

        matched_columns = self._match_columns_with_entity(
            entity_context.get("properties", []),
            table_detail.get("columns", []),
        )
        warnings: List[str] = []
        if not process_steps:
            warnings.append("所选技能对应流程没有可解析的节点，当前按通用数据分析流程进行测试。")
        if not matched_columns:
            warnings.append("graph 表字段与本体对象属性未形成明显匹配，请检查表选择是否正确。")

        process_trace = self._build_process_trace(
            process_steps=process_steps,
            skill_name=skill.skill_name,
            entity_context=entity_context,
            graph_table=table_detail.get("table_name"),
            matched_columns=matched_columns,
            test_question=payload.get("test_question") or "",
        )
        suggested_columns = [item["column_name"] for item in matched_columns[:8]] or [col["column_name"] for col in table_detail.get("columns", [])[:8]]
        suggested_sql = (
            f"SELECT {', '.join(suggested_columns)}\n"
            f"FROM {table_detail.get('owner')}.{table_detail.get('table_name')}\n"
            f"FETCH FIRST {max(1, min(int(payload.get('sample_limit') or 5), 10))} ROWS ONLY"
        )
        prompt_preview = self._build_test_prompt(skill, entity_context, table_detail, payload, analysis_semantics)
        agent_output = await self._execute_skill_test_with_llm(
            skill=skill,
            llm_config=llm_config,
            prompt_preview=prompt_preview,
            process_trace=process_trace,
            matched_columns=matched_columns,
            table_detail=table_detail,
            suggested_sql=suggested_sql,
            payload=payload,
        )

        return {
            "skill": self.get_skill(skill_id),
            "execution_model": {
                "llm_config_id": llm_config.config_id,
                "llm_config_name": llm_config.config_name,
                "llm_model_name": normalize_model_name(llm_config.model_name, llm_config.api_base_url),
            },
            "test_context": {
                "source_id": payload["source_id"],
                "source_name": source.source_name if source else "",
                "schema": table_detail.get("owner"),
                "graph_table": table_detail.get("table_name"),
                "table_comment": table_detail.get("table_comment"),
                "test_question": payload.get("test_question") or "",
                "input_payload": payload.get("input_payload") or "",
            },
            "matched_columns": matched_columns,
            "warnings": warnings,
            "suggested_sql": suggested_sql,
            "prompt_preview": prompt_preview,
            "agent_output": agent_output,
            "process_trace": process_trace,
            "table_preview": {
                "columns": table_detail.get("columns", []),
                "sample_rows": table_detail.get("sample_rows", []),
            },
            "expected_output": {
                "summary": f"技能 {skill.skill_name} 将围绕 {entity_context.get('entity_display_name') or entity_context.get('entity_name')} 对 {table_detail.get('table_name')} 进行分析。",
                "focus_points": [
                    skill.analysis_goal or "围绕业务对象完成数据分析",
                    f"使用 {len(analysis_semantics.get('metrics') or [])} 个指标、{len(analysis_semantics.get('rules') or [])} 条规则和 {len(analysis_semantics.get('activities') or [])} 个活动建议语义",
                    "按既定流程节点逐步执行",
                    "结合 graph 表字段和样例数据形成分析结论",
                ],
                "recommended_next_actions": [
                    "确认字段映射关系后，再接入真实 Agent 执行器。",
                    "根据测试结果完善技能描述、输出要求和流程节点配置。",
                ],
            },
        }

    def _get_llm_config(self, llm_config_id: Optional[str], purpose: str = "技能构建") -> SysLLMConfig:
        if not llm_config_id:
            raise ValueError(f"请选择用于{purpose}的大模型")
        config = self.db.query(SysLLMConfig).filter(
            SysLLMConfig.config_id == llm_config_id,
            SysLLMConfig.is_active == "Y",
        ).first()
        if not config:
            raise ValueError("所选大模型配置不存在或未启用")
        return config

    async def _execute_skill_test_with_llm(
        self,
        skill: SysAgentSkill,
        llm_config: SysLLMConfig,
        prompt_preview: str,
        process_trace: List[Dict[str, Any]],
        matched_columns: List[Dict[str, Any]],
        table_detail: Dict[str, Any],
        suggested_sql: str,
        payload: Dict[str, Any],
    ) -> str:
        system_prompt = """你是一个业务分析智能体，需要严格根据给定的 skill、业务流程、字段映射和样例数据完成测试执行。

要求：
1. 使用中文输出。
2. 必须体现你是依据 skill 和流程逐步执行，而不是泛泛而谈。
3. 输出结构固定为以下 5 段：
   一、结论摘要
   二、流程执行说明
   三、关键发现
   四、风险与不确定性
   五、建议动作
4. 如果样例数据不足以支持强结论，必须明确说明。"""
        trace_text = "\n".join(
            [
                f"{item['step_no']}. {item['step_name']}（{item['step_type']}）- {item['action']}"
                for item in process_trace
            ]
        )
        matched_text = json.dumps(matched_columns[:12], ensure_ascii=False, indent=2)
        sample_rows = json.dumps(table_detail.get("sample_rows", [])[:3], ensure_ascii=False, indent=2)
        user_prompt = (
            f"{prompt_preview}\n\n"
            f"补充上下文：\n"
            f"- 建议 SQL：\n{suggested_sql}\n"
            f"- 流程执行轨迹：\n{trace_text}\n"
            f"- 字段匹配：\n{matched_text}\n"
            f"- 样例数据：\n{sample_rows}\n"
            f"- 用户测试问题：{payload.get('test_question') or '未提供'}\n"
            f"- 额外输入：{payload.get('input_payload') or '无'}\n"
            f"- graph 表：{table_detail.get('owner')}.{table_detail.get('table_name')}"
        )
        return await self.llm_service.call_llm(system_prompt, user_prompt, llm_config, timeout_override=max(llm_config.timeout, 120))

    def _load_skill_dependencies(self, domain_id: str, process_id: str, source_id: str, property_graph_name: str):
        domain = self.db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
        if not domain:
            raise ValueError("分析域不存在")
        process = self.db.query(SysProcessDef).filter(
            SysProcessDef.process_id == process_id,
            SysProcessDef.domain_id == domain_id,
        ).first()
        if not process:
            raise ValueError("分析流程不存在")
        source = self.db.query(SysDataSource).filter(
            SysDataSource.source_id == source_id,
            SysDataSource.is_active == "Y",
        ).first()
        if not source:
            raise ValueError("属性图数据源不存在或未启用")
        if (source.db_type or "").lower() != "oracle":
            raise ValueError("属性图对象仅支持 Oracle 数据源")
        if source.business_domain_id and source.business_domain_id != domain_id:
            raise ValueError("属性图数据源不属于当前业务分析域")
        requested_name = (property_graph_name or "").strip().upper()
        if not requested_name:
            raise ValueError("请选择 Oracle Property Graph")
        graphs = self.source_service.get_remote_property_graphs(source_id).get("graphs", [])
        graph = next((item for item in graphs if (item.get("graph_name") or "").upper() == requested_name), None)
        if not graph:
            raise ValueError("所选 Oracle Property Graph 不存在或不可访问")
        entity = SimpleNamespace(
            entity_id="PROPERTY_GRAPH",
            entity_name=graph["graph_name"],
            entity_display_name=graph["graph_name"],
            entity_desc=f"Oracle Property Graph，Schema：{graph.get('owner') or source.schema_name or ''}",
            table_name=graph["graph_name"],
            build_type="PROPERTY_GRAPH",
            status="DEPLOYED",
            source_id=source.source_id,
            source_name=source.source_name,
            schema=graph.get("owner") or source.schema_name,
        )
        properties: List[SysOntologyProperty] = []
        relations: List[SysOntologyRelation] = []
        return domain, process, entity, properties, relations

    def _build_skill_context(
        self,
        domain: SysDomain,
        process: SysProcessDef,
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
        relations: List[SysOntologyRelation],
        topology: Dict[str, Any],
        analysis_semantics: Dict[str, Any],
        ontology_model: Dict[str, Any],
        ontology_graph_binding: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "domain": {
                "domain_id": domain.domain_id,
                "domain_name": domain.domain_name,
                "domain_desc": domain.domain_desc,
            },
            "process": {
                "process_id": process.process_id,
                "process_name": process.process_name,
                "process_desc": process.process_desc,
                "process_json": self._safe_json_loads(process.process_json, {}),
                "steps": self._extract_process_steps(process.process_json),
            },
            "entity": {
                "entity_id": entity.entity_id,
                "entity_name": entity.entity_name,
                "entity_display_name": entity.entity_display_name,
                "entity_desc": entity.entity_desc,
                "table_name": entity.table_name,
                "build_type": entity.build_type,
                "status": entity.status,
                "properties": [
                    {
                        "property_id": item.property_id,
                        "property_name": item.property_name,
                        "property_display_name": item.property_display_name,
                        "data_type": item.data_type,
                        "is_primary_key": item.is_primary_key,
                        "property_desc": item.property_desc,
                    }
                    for item in properties
                ],
                "relations": [
                    {
                        "relation_id": rel.relation_id,
                        "relation_name": rel.relation_name,
                        "relation_type": rel.relation_type,
                        "relation_desc": rel.relation_desc,
                        "direction": "OUT" if rel.source_entity_id == entity.entity_id else "IN",
                    }
                    for rel in relations
                ],
            },
            "property_graph": {
                "source_id": getattr(entity, "source_id", None),
                "source_name": getattr(entity, "source_name", None),
                "schema": getattr(entity, "schema", None),
                "graph_name": entity.entity_name,
                "object_type": "PROPERTY GRAPH",
            },
            "topology_summary": self._compact_topology(topology),
            "ontology_model": ontology_model,
            "ontology_graph_binding": ontology_graph_binding,
            "analysis_semantics": analysis_semantics,
        }

    async def _generate_skill_blueprint(
        self,
        domain: SysDomain,
        process: SysProcessDef,
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
        relations: List[SysOntologyRelation],
        skill: SysAgentSkill,
        llm_config: SysLLMConfig,
        topology: Dict[str, Any],
        analysis_semantics: Dict[str, Any],
        ontology_model: Dict[str, Any],
        ontology_graph_binding: Dict[str, Any],
    ) -> Dict[str, str]:
        fallback = {
            "skill_desc": self._default_skill_desc(domain, process, entity, llm_config),
            "analysis_goal": self._default_analysis_goal(domain, entity),
            "execution_rules": skill.execution_rules or "优先按照流程节点顺序执行，遇到数据不完整时给出风险提示。",
            "output_requirements": skill.output_requirements or "输出结构化结论、关键指标、异常点和建议动作。",
            "prompt_template": self._build_fallback_prompt_template(domain, process, entity, properties, relations, skill, llm_config, analysis_semantics),
        }
        prop_payload = [
            {
                "property_name": item.property_name,
                "property_display_name": item.property_display_name,
                "data_type": item.data_type,
                "property_desc": item.property_desc,
                "is_primary_key": item.is_primary_key,
            }
            for item in properties[:20]
        ]
        relation_payload = [
            {
                "relation_name": rel.relation_name,
                "relation_type": rel.relation_type,
                "relation_desc": rel.relation_desc,
            }
            for rel in relations[:12]
        ]
        process_steps = self._extract_process_steps(process.process_json)
        system_prompt = """你是一个资深智能体架构师，需要根据分析域、本体对象和业务流程，为数据分析 agent 生成一个可执行的技能定义。

要求：
1. 输出必须是严格 JSON。
2. skill_desc 用中文，描述技能职责与适用范围。
3. analysis_goal 用中文，聚焦该技能要完成的业务分析目标。
4. execution_rules 用中文，强调执行顺序、风险控制和异常处理。
5. output_requirements 用中文，描述输出结构与重点。
6. prompt_template 直接生成给大模型执行的提示词正文，中文为主，结构清晰，可引用流程步骤、本体属性、关系、指标、规则与活动。
7. prompt_template 必须明确要求 Agent：先识别分析意图，再按图探索、指标计算、规则判定、活动建议的顺序输出。

输出格式：
{
  "skill_desc": "string",
  "analysis_goal": "string",
  "execution_rules": "string",
  "output_requirements": "string",
  "prompt_template": "string"
}"""
        user_prompt = json.dumps({
            "domain": {
                "domain_name": domain.domain_name,
                "domain_desc": domain.domain_desc,
            },
            "llm": {
                "config_name": llm_config.config_name,
                "model_name": normalize_model_name(llm_config.model_name, llm_config.api_base_url),
            },
            "skill": {
                "skill_name": skill.skill_name,
                "skill_desc": skill.skill_desc,
                "analysis_goal": skill.analysis_goal,
                "execution_rules": skill.execution_rules,
                "output_requirements": skill.output_requirements,
            },
            "ontology_model": ontology_model,
            "ontology_graph_binding": ontology_graph_binding,
            "analysis_semantics": analysis_semantics,
            "entity": {
                "entity_name": entity.entity_name,
                "entity_display_name": entity.entity_display_name,
                "entity_desc": entity.entity_desc,
                "properties": prop_payload,
                "relations": relation_payload,
            },
            "process": {
                "process_name": process.process_name,
                "process_desc": process.process_desc,
                "steps": process_steps,
            },
            "property_graph": self._compact_topology(topology),
        }, ensure_ascii=False, indent=2)
        try:
            result_text = await self.llm_service.call_llm(system_prompt, user_prompt, llm_config)
            parsed = self._safe_json_loads(self.llm_service._extract_json_object(result_text), {})
            if isinstance(parsed, dict) and parsed.get("prompt_template"):
                return {
                    "skill_desc": (parsed.get("skill_desc") or fallback["skill_desc"]).strip(),
                    "analysis_goal": (parsed.get("analysis_goal") or fallback["analysis_goal"]).strip(),
                    "execution_rules": (parsed.get("execution_rules") or fallback["execution_rules"]).strip(),
                    "output_requirements": (parsed.get("output_requirements") or fallback["output_requirements"]).strip(),
                    "prompt_template": (parsed.get("prompt_template") or fallback["prompt_template"]).strip(),
                }
        except Exception:
            pass
        return fallback

    def _build_fallback_prompt_template(
        self,
        domain: SysDomain,
        process: SysProcessDef,
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
        relations: List[SysOntologyRelation],
        skill: SysAgentSkill,
        llm_config: SysLLMConfig,
        analysis_semantics: Dict[str, Any],
    ) -> str:
        # Graph runtime is the source of truth for executable labels/properties,
        # while ontology-model and ontology-graph-binding provide business
        # semantics and allowed mapping context.
        prop_text = "、".join(
            [
                item.property_display_name or item.property_name
                for item in properties[:8]
            ]
        ) or "无已配置属性"
        relation_text = "、".join([rel.relation_name for rel in relations[:6]]) or "无显式关系"
        metric_text = "、".join([item.get("metric_name") or item.get("metric_code") or "" for item in (analysis_semantics.get("metrics") or [])[:8]]) or "无已配置指标"
        rule_text = "、".join([item.get("rule_name") or "" for item in (analysis_semantics.get("rules") or [])[:8]]) or "无已配置规则"
        activity_text = "、".join([item.get("activity_name") or "" for item in (analysis_semantics.get("activities") or [])[:8]]) or "无已配置活动"
        analysis_profile = analysis_semantics.get("analysis_profile") or {}
        steps = self._extract_process_steps(process.process_json)
        step_text = "\n".join([f"{idx + 1}. {step['label']}（{step['type_label']}）" for idx, step in enumerate(steps[:10])]) or "1. 开始准备分析"
        return (
            f"你是业务分析智能体中的数据分析技能“{skill.skill_name}”。\n"
            f"本技能构建所使用的大模型：{llm_config.config_name} / {normalize_model_name(llm_config.model_name, llm_config.api_base_url)}\n"
            f"分析域：{domain.domain_name}\n"
            f"分析目标：{skill.analysis_goal or self._default_analysis_goal(domain, entity)}\n"
            f"分析场景：{analysis_profile.get('analysis_scenario_name') or '通用图探索分析'}\n"
            f"分析模式：{'、'.join(analysis_profile.get('analysis_modes') or DEFAULT_SKILL_ANALYSIS_MODES)}\n"
            f"默认时间窗：{analysis_profile.get('default_time_window') or '7D'}\n"
            f"最大路径跳数：{analysis_profile.get('max_path_depth') or 2}\n"
            f"本体对象：{entity.entity_display_name or entity.entity_name}\n"
            f"对象说明：{entity.entity_desc or '暂无'}\n"
            f"关键属性：{prop_text}\n"
            f"相关关系：{relation_text}\n"
            f"关键指标：{metric_text}\n"
            f"关键规则：{rule_text}\n"
            f"建议活动：{activity_text}\n"
            f"业务流程：\n{step_text}\n"
            f"执行规则：{skill.execution_rules or '优先按照流程节点顺序执行，遇到数据不完整时给出风险提示。'}\n"
            f"输出要求：{skill.output_requirements or '输出结构化结论、关键指标、异常点和建议动作。'}\n"
            "回答时必须先依据 references/ontology-model.json 理解本体对象与属性语义，再依据 references/ontology-graph-binding.json 确认其在当前 Property Graph 中的映射，最后结合 references/property-graph.md、指标、规则和活动做分析。\n"
            "回答时必须依次说明：分析意图、图查询路径、关键指标、命中规则、结论、建议活动、风险与限制。"
        )

    def _extract_process_steps(self, process_json: Any) -> List[Dict[str, Any]]:
        parsed = process_json if isinstance(process_json, dict) else self._safe_json_loads(process_json, {})
        nodes = parsed.get("nodes") if isinstance(parsed, dict) else []
        edges = parsed.get("edges") if isinstance(parsed, dict) else []
        if not isinstance(nodes, list):
            nodes = []
        if not isinstance(edges, list):
            edges = []

        node_map = {}
        indegree = {}
        adjacency: Dict[str, List[str]] = {}
        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                continue
            node_map[node_id] = node
            indegree.setdefault(node_id, 0)
            adjacency.setdefault(node_id, [])

        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source in node_map and target in node_map:
                adjacency[source].append(target)
                indegree[target] = indegree.get(target, 0) + 1

        queue = deque(sorted([node_id for node_id, degree in indegree.items() if degree == 0]))
        ordered_ids: List[str] = []
        while queue:
            current = queue.popleft()
            ordered_ids.append(current)
            for nxt in adjacency.get(current, []):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        for node in nodes:
            if node.get("id") not in ordered_ids:
                ordered_ids.append(node.get("id"))

        return [
            {
                "step_no": idx + 1,
                "node_id": node_id,
                "label": (node_map[node_id].get("label") or node_map[node_id].get("typeName") or "未命名节点").strip(),
                "type": node_map[node_id].get("type") or "analysis",
                "type_label": self._flow_type_label(node_map[node_id].get("type") or "analysis"),
                "desc": node_map[node_id].get("desc") or "",
                "config": node_map[node_id].get("config") or {},
                "next_nodes": adjacency.get(node_id, []),
            }
            for idx, node_id in enumerate(ordered_ids)
            if node_id in node_map
        ]

    def _build_process_trace(
        self,
        process_steps: List[Dict[str, Any]],
        skill_name: str,
        entity_context: Dict[str, Any],
        graph_table: str,
        matched_columns: List[Dict[str, Any]],
        test_question: str,
    ) -> List[Dict[str, Any]]:
        if not process_steps:
            process_steps = [
                {"step_no": 1, "label": "数据准备", "type": "dataInput", "type_label": "数据输入", "config": {}, "desc": ""},
                {"step_no": 2, "label": "对象分析", "type": "analysis", "type_label": "分析节点", "config": {}, "desc": ""},
                {"step_no": 3, "label": "结果输出", "type": "action", "type_label": "操作节点", "config": {}, "desc": ""},
            ]
        entity_name = entity_context.get("entity_display_name") or entity_context.get("entity_name") or "本体对象"
        key_columns = "、".join([item["column_name"] for item in matched_columns[:5]]) or "待确认字段"
        trace = []
        for step in process_steps:
            step_type = step.get("type")
            if step_type == "start":
                action = f"初始化技能 {skill_name} 的执行上下文，准备分析对象 {entity_name}。"
            elif step_type == "dataInput":
                action = f"从 graph 表 {graph_table} 读取与 {entity_name} 相关的数据，重点关注字段：{key_columns}。"
            elif step_type == "decision":
                action = f"依据流程节点规则对 {entity_name} 的状态或风险进行判断，并记录判定依据。"
            elif step_type == "action":
                action = "生成分析结果、建议动作或输出报表。"
            elif step_type == "end":
                action = "结束流程并整理最终结论。"
            else:
                action = f"调用技能 {skill_name} 进行分析处理，形成中间结论。"
            if test_question:
                action = f"{action} 当前测试问题：{test_question}"
            trace.append({
                "step_no": step["step_no"],
                "step_name": step["label"],
                "step_type": step.get("type_label") or self._flow_type_label(step_type or "analysis"),
                "action": action,
                "config": step.get("config") or {},
                "desc": step.get("desc") or "",
            })
        return trace

    def _build_test_prompt(
        self,
        skill: SysAgentSkill,
        entity_context: Dict[str, Any],
        table_detail: Dict[str, Any],
        payload: Dict[str, Any],
        analysis_semantics: Optional[Dict[str, Any]] = None,
    ) -> str:
        columns = "、".join([col["column_name"] for col in table_detail.get("columns", [])[:10]])
        sample_rows = self._safe_json_dumps(table_detail.get("sample_rows", [])[:2])
        input_payload = payload.get("input_payload") or ""
        analysis_semantics = analysis_semantics or {}
        profile = analysis_semantics.get("analysis_profile") or {}
        metric_names = "、".join(item.get("metric_name") or item.get("metric_code") or "" for item in (analysis_semantics.get("metrics") or [])[:8]) or "无"
        rule_names = "、".join(item.get("rule_name") or "" for item in (analysis_semantics.get("rules") or [])[:8]) or "无"
        activity_names = "、".join(item.get("activity_name") or "" for item in (analysis_semantics.get("activities") or [])[:8]) or "无"
        return (
            f"{skill.prompt_template or ''}\n\n"
            f"测试上下文：\n"
            f"- graph 表：{table_detail.get('owner')}.{table_detail.get('table_name')}\n"
            f"- 表说明：{table_detail.get('table_comment') or '暂无'}\n"
            f"- 字段：{columns or '暂无'}\n"
            f"- 样例数据：{sample_rows}\n"
            f"- 分析模式：{'、'.join(profile.get('analysis_modes') or DEFAULT_SKILL_ANALYSIS_MODES)}\n"
            f"- 默认时间窗：{profile.get('default_time_window') or '7D'}\n"
            f"- 指标：{metric_names}\n"
            f"- 规则：{rule_names}\n"
            f"- 活动：{activity_names}\n"
            f"- 测试问题：{payload.get('test_question') or '未提供'}\n"
            f"- 额外输入：{input_payload or '无'}\n"
            f"- 分析对象：{entity_context.get('entity_display_name') or entity_context.get('entity_name')}"
        ).strip()

    def _match_columns_with_entity(self, properties: List[Dict[str, Any]], columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        matched = []
        for prop in properties:
            prop_names = {
                (prop.get("property_name") or "").upper(),
                (prop.get("property_display_name") or "").upper(),
            }
            for col in columns:
                column_name = (col.get("column_name") or "").upper()
                if not column_name:
                    continue
                if any(name and (name == column_name or name in column_name or column_name in name) for name in prop_names):
                    matched.append({
                        "property_name": prop.get("property_name"),
                        "property_display_name": prop.get("property_display_name"),
                        "column_name": col.get("column_name"),
                        "data_type": col.get("data_type"),
                        "column_comment": col.get("comments"),
                    })
                    break
        return matched

    def _serialize_skill(
        self,
        skill: SysAgentSkill,
        domain_name: Optional[str],
        llm_config_name: Optional[str],
        llm_model_name: Optional[str],
        process_name: Optional[str],
        source_name: Optional[str],
    ) -> Dict[str, Any]:
        context = self._safe_json_loads(skill.context_json, {})
        defaults = self._skill_semantic_defaults_from_context(context)
        return {
            "skill_id": skill.skill_id,
            "domain_id": skill.domain_id,
            "domain_name": domain_name,
            "llm_config_id": skill.llm_config_id,
            "llm_config_name": llm_config_name,
            "llm_model_name": normalize_model_name(llm_model_name) if llm_model_name else None,
            "process_id": skill.process_id,
            "process_name": process_name,
            "entity_id": skill.entity_id,
            "entity_name": skill.property_graph_name,
            "entity_display_name": skill.property_graph_name,
            "source_id": skill.source_id,
            "source_name": source_name,
            "property_graph_name": skill.property_graph_name,
            "skill_name": skill.skill_name,
            "skill_desc": skill.skill_desc,
            "analysis_goal": skill.analysis_goal,
            "execution_rules": skill.execution_rules,
            "output_requirements": skill.output_requirements,
            "analysis_scenario_code": defaults["analysis_scenario_code"],
            "analysis_modes": defaults["analysis_modes"],
            "entry_entity_ids": defaults["entry_entity_ids"],
            "selected_metric_ids": defaults["selected_metric_ids"],
            "selected_rule_ids": defaults["selected_rule_ids"],
            "selected_activity_ids": defaults["selected_activity_ids"],
            "enable_activity_recommendation": defaults["enable_activity_recommendation"],
            "default_time_window": defaults["default_time_window"],
            "max_path_depth": defaults["max_path_depth"],
            "prompt_template": skill.prompt_template,
            "context_json": skill.context_json,
            "status": skill.status,
            "created_by": skill.created_by,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
        }

    def _skill_semantic_defaults_from_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        semantics = (context or {}).get("analysis_semantics") or {}
        profile = semantics.get("analysis_profile") or {}
        return {
            "analysis_scenario_code": str(profile.get("analysis_scenario_code") or "").strip().upper() or "GENERAL_GRAPH",
            "analysis_modes": self._normalize_analysis_modes(profile.get("analysis_modes")),
            "entry_entity_ids": self._normalize_string_list(profile.get("entry_entity_ids")),
            "selected_metric_ids": self._normalize_string_list([item.get("metric_id") for item in (semantics.get("metrics") or []) if item.get("metric_id")]),
            "selected_rule_ids": self._normalize_string_list([item.get("rule_id") for item in (semantics.get("rules") or []) if item.get("rule_id")]),
            "selected_activity_ids": self._normalize_string_list([item.get("activity_id") for item in (semantics.get("activities") or []) if item.get("activity_id")]),
            "enable_activity_recommendation": bool(profile.get("enable_activity_recommendation", True)),
            "default_time_window": profile.get("default_time_window") or "7D",
            "max_path_depth": self._normalize_max_path_depth(profile.get("max_path_depth")),
        }

    def _default_skill_desc(self, domain: SysDomain, process: SysProcessDef, entity: SysOntologyEntity, llm_config: Optional[SysLLMConfig] = None) -> str:
        model_text = ""
        if llm_config:
            model_text = f"，使用大模型“{llm_config.config_name} / {normalize_model_name(llm_config.model_name, llm_config.api_base_url)}”进行构建"
        return f"面向分析域“{domain.domain_name}”，围绕 Oracle 属性图“{entity.entity_display_name or entity.entity_name}”并按照流程“{process.process_name}”执行的数据分析技能{model_text}。"

    def _default_analysis_goal(self, domain: SysDomain, entity: SysOntologyEntity) -> str:
        return f"基于分析域“{domain.domain_name}”的数据和流程，对 Oracle 属性图“{entity.entity_display_name or entity.entity_name}”进行结构化分析。"

    def _flow_type_label(self, flow_type: str) -> str:
        return {
            "start": "开始",
            "dataInput": "数据输入",
            "analysis": "分析节点",
            "decision": "决策节点",
            "action": "操作节点",
            "end": "结束",
        }.get(flow_type, flow_type or "分析节点")

    def _safe_json_loads(self, raw: Any, default: Any) -> Any:
        if raw is None:
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return default

    def _safe_json_dumps(self, raw: Any) -> str:
        try:
            return json.dumps(raw, ensure_ascii=False)
        except Exception:
            return "[]"
