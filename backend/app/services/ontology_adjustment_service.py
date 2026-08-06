from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.models import (
    SysDomain,
    SysEntityMapping,
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
    SysPropertyMapping,
    generate_id,
)
from app.services.llm_service import LLMService


class OntologyAdjustmentService:
    def __init__(self, db: Session):
        self.db = db
        self.llm_service = LLMService(db)

    async def generate(
        self,
        domain_id: str,
        instruction: str,
        selected_entity_id: Optional[str] = None,
        model_config_id: Optional[str] = None,
        auto_apply: bool = False,
        created_by: str = "unknown",
    ) -> Dict[str, Any]:
        domain = self.db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
        if not domain:
            raise ValueError("业务分析域不存在")
        if not (instruction or "").strip():
            raise ValueError("请输入自然语言调整说明")

        entities = (
            self.db.query(SysOntologyEntity)
            .options(joinedload(SysOntologyEntity.properties))
            .filter(SysOntologyEntity.domain_id == domain_id)
            .order_by(SysOntologyEntity.created_at)
            .all()
        )
        relations = (
            self.db.query(SysOntologyRelation)
            .options(
                joinedload(SysOntologyRelation.source_entity),
                joinedload(SysOntologyRelation.target_entity),
            )
            .filter(SysOntologyRelation.domain_id == domain_id)
            .order_by(SysOntologyRelation.created_at)
            .all()
        )

        if selected_entity_id and not any(entity.entity_id == selected_entity_id for entity in entities):
            raise ValueError("当前选中的实体不存在，无法作为调整范围")

        plan = await self.llm_service.generate_ontology_adjustment_plan(
            domain=domain,
            entities=entities,
            relations=relations,
            instruction=instruction,
            selected_entity_id=selected_entity_id,
            config_id=model_config_id,
        )

        result = {
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "domain_desc": domain.domain_desc,
            **plan,
        }
        if auto_apply:
            result["apply_result"] = self.apply_plan(domain_id, plan, created_by=created_by)
        return result

    def apply_plan(
        self,
        domain_id: str,
        plan: Dict[str, Any],
        created_by: str = "unknown",
    ) -> Dict[str, Any]:
        domain = self.db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
        if not domain:
            raise ValueError("业务分析域不存在")

        result = {
            "entities": {"created": 0, "updated": 0, "deleted": 0, "skipped": 0},
            "properties": {"created": 0, "updated": 0, "deleted": 0, "skipped": 0},
            "relations": {"created": 0, "updated": 0, "deleted": 0, "skipped": 0},
            "applied_entities": [],
            "applied_properties": [],
            "applied_relations": [],
            "warnings": [],
        }

        entity_actions = plan.get("entityActions") or []
        property_actions = plan.get("propertyActions") or []
        relation_actions = plan.get("relationActions") or []

        next_position_index = self.db.query(SysOntologyEntity).filter(SysOntologyEntity.domain_id == domain_id).count()

        entity_id_map: Dict[str, str] = {}
        property_id_map: Dict[str, str] = {}
        relation_id_map: Dict[str, str] = {}

        for action in entity_actions:
            op = (action.get("action") or "").strip().lower()
            if op == "create":
                created = self._apply_entity_create(domain_id, action, next_position_index, created_by)
                if created:
                    entity, warning = created
                    next_position_index += 1
                    result["entities"]["created"] += 1
                    result["applied_entities"].append({
                        "action": op,
                        "entity_id": entity.entity_id,
                        "entity_name": entity.entity_name,
                    })
                    if action.get("entityId"):
                        entity_id_map[action["entityId"]] = entity.entity_id
                    if warning:
                        result["warnings"].append(warning)
                else:
                    result["entities"]["skipped"] += 1
            elif op == "update":
                entity = self._resolve_entity(domain_id, action.get("entityId"), action.get("entityName"))
                if not entity:
                    result["entities"]["skipped"] += 1
                    result["warnings"].append(f"未找到待更新实体: {action.get('entityName') or action.get('entityId') or '未提供定位信息'}")
                    continue
                warning = self._apply_entity_update(domain_id, entity, action)
                result["entities"]["updated"] += 1
                result["applied_entities"].append({
                    "action": op,
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                })
                if action.get("entityId"):
                    entity_id_map[action["entityId"]] = entity.entity_id
                if warning:
                    result["warnings"].append(warning)
            elif op == "delete":
                entity = self._resolve_entity(domain_id, action.get("entityId"), action.get("entityName"))
                if not entity:
                    result["entities"]["skipped"] += 1
                    result["warnings"].append(f"未找到待删除实体: {action.get('entityName') or action.get('entityId') or '未提供定位信息'}")
                    continue
                self._delete_entity_with_relations(entity.entity_id)
                result["entities"]["deleted"] += 1
                result["applied_entities"].append({
                    "action": op,
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                })

        self.db.flush()

        for action in property_actions:
            op = (action.get("action") or "").strip().lower()
            entity = self._resolve_entity_for_property_action(domain_id, action, entity_id_map)
            if not entity:
                result["properties"]["skipped"] += 1
                result["warnings"].append(f"属性动作缺少有效实体定位: {action.get('propertyName') or action.get('propertyId') or '未命名属性'}")
                continue

            prop = self._resolve_property(entity.entity_id, action.get("propertyId"), action.get("propertyName"))
            if op == "create":
                if prop:
                    result["properties"]["skipped"] += 1
                    result["warnings"].append(f"属性已存在，跳过创建: {entity.entity_name}.{prop.property_name}")
                    continue
                created = self._apply_property_create(entity.entity_id, action)
                if not created:
                    result["properties"]["skipped"] += 1
                    continue
                result["properties"]["created"] += 1
                result["applied_properties"].append({
                    "action": op,
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                    "property_id": created.property_id,
                    "property_name": created.property_name,
                })
                if action.get("propertyId"):
                    property_id_map[action["propertyId"]] = created.property_id
            elif op == "update":
                if not prop:
                    result["properties"]["skipped"] += 1
                    result["warnings"].append(f"未找到待更新属性: {entity.entity_name}.{action.get('propertyName') or action.get('propertyId') or '未命名属性'}")
                    continue
                warning = self._apply_property_update(entity.entity_id, prop, action)
                result["properties"]["updated"] += 1
                result["applied_properties"].append({
                    "action": op,
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                    "property_id": prop.property_id,
                    "property_name": prop.property_name,
                })
                if action.get("propertyId"):
                    property_id_map[action["propertyId"]] = prop.property_id
                if warning:
                    result["warnings"].append(warning)
            elif op == "delete":
                if not prop:
                    result["properties"]["skipped"] += 1
                    result["warnings"].append(f"未找到待删除属性: {entity.entity_name}.{action.get('propertyName') or action.get('propertyId') or '未命名属性'}")
                    continue
                self._delete_property(prop)
                self._refresh_entity_mapping_state(entity.entity_id)
                result["properties"]["deleted"] += 1
                result["applied_properties"].append({
                    "action": op,
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                    "property_id": prop.property_id,
                    "property_name": prop.property_name,
                })

        self.db.flush()

        for action in relation_actions:
            op = (action.get("action") or "").strip().lower()
            relation = self._resolve_relation(domain_id, action.get("relationId"), action)
            if op == "create":
                if relation:
                    result["relations"]["skipped"] += 1
                    result["warnings"].append(f"关系已存在，跳过创建: {relation.relation_name}")
                    continue
                created = self._apply_relation_create(domain_id, action, entity_id_map)
                if not created:
                    result["relations"]["skipped"] += 1
                    result["warnings"].append(f"未能创建关系: {action.get('relationName') or '未命名关系'}")
                    continue
                result["relations"]["created"] += 1
                result["applied_relations"].append({
                    "action": op,
                    "relation_id": created.relation_id,
                    "relation_name": created.relation_name,
                })
                if action.get("relationId"):
                    relation_id_map[action["relationId"]] = created.relation_id
            elif op == "update":
                if not relation:
                    result["relations"]["skipped"] += 1
                    result["warnings"].append(f"未找到待更新关系: {action.get('relationName') or action.get('relationId') or '未命名关系'}")
                    continue
                updated = self._apply_relation_update(domain_id, relation, action, entity_id_map)
                if not updated:
                    result["relations"]["skipped"] += 1
                    continue
                result["relations"]["updated"] += 1
                result["applied_relations"].append({
                    "action": op,
                    "relation_id": relation.relation_id,
                    "relation_name": relation.relation_name,
                })
                if action.get("relationId"):
                    relation_id_map[action["relationId"]] = relation.relation_id
            elif op == "delete":
                if not relation:
                    result["relations"]["skipped"] += 1
                    result["warnings"].append(f"未找到待删除关系: {action.get('relationName') or action.get('relationId') or '未命名关系'}")
                    continue
                self.db.delete(relation)
                result["relations"]["deleted"] += 1
                result["applied_relations"].append({
                    "action": op,
                    "relation_id": relation.relation_id,
                    "relation_name": relation.relation_name,
                })

        self.db.commit()
        return result

    def _apply_entity_create(
        self,
        domain_id: str,
        action: Dict[str, Any],
        position_index: int,
        created_by: str,
    ) -> Optional[Tuple[SysOntologyEntity, Optional[str]]]:
        entity_name = self.llm_service._sanitize_entity_name(action.get("entityName") or "")
        if not entity_name:
            return None
        existing = self._resolve_entity(domain_id, action.get("entityId"), entity_name)
        if existing:
            return existing, f"实体 {entity_name} 已存在，本次未重复创建"

        build_type = (action.get("buildType") or "TABLE").strip().upper()
        if build_type not in {"TABLE", "VIEW"}:
            build_type = "TABLE"
        entity = SysOntologyEntity(
            entity_id=generate_id("ent"),
            domain_id=domain_id,
            entity_name=entity_name,
            entity_display_name=(action.get("entityDisplayName") or "")[:200] or None,
            entity_desc=(action.get("entityDesc") or "")[:1000] or None,
            build_type=build_type,
            table_name=f"ONTO_{entity_name.upper()}_V" if build_type == "VIEW" else f"ONTO_{entity_name.upper()}",
            status="DRAFT",
            color=(action.get("color") or "")[:20] or None,
            graph_position=json.dumps(self._build_graph_position(position_index)),
            created_by=created_by,
        )
        self.db.add(entity)
        self.db.flush()
        return entity, None

    def _apply_entity_update(
        self,
        domain_id: str,
        entity: SysOntologyEntity,
        action: Dict[str, Any],
    ) -> Optional[str]:
        warning = None
        next_entity_name = self.llm_service._sanitize_entity_name(action.get("entityName") or entity.entity_name)
        if next_entity_name and next_entity_name != entity.entity_name:
            duplicate = (
                self.db.query(SysOntologyEntity)
                .filter(
                    SysOntologyEntity.domain_id == domain_id,
                    SysOntologyEntity.entity_name == next_entity_name,
                    SysOntologyEntity.entity_id != entity.entity_id,
                )
                .first()
            )
            if duplicate:
                warning = f"实体名 {next_entity_name} 已存在，保留原名称 {entity.entity_name}"
            else:
                entity.entity_name = next_entity_name

        if action.get("entityDisplayName"):
            entity.entity_display_name = action.get("entityDisplayName")
        if "entityDesc" in action:
            entity.entity_desc = action.get("entityDesc") or None
        if action.get("buildType") in {"TABLE", "VIEW"}:
            entity.build_type = action["buildType"]
        if action.get("color"):
            entity.color = action.get("color")
        entity.table_name = f"ONTO_{entity.entity_name.upper()}_V" if entity.build_type == "VIEW" else f"ONTO_{entity.entity_name.upper()}"
        entity.updated_at = datetime.utcnow()
        self.db.flush()
        return warning

    def _apply_property_create(self, entity_id: str, action: Dict[str, Any]) -> Optional[SysOntologyProperty]:
        property_name = self.llm_service._sanitize_property_name(action.get("propertyName") or "")
        if not property_name:
            return None
        prop = SysOntologyProperty(
            property_id=generate_id("prop"),
            entity_id=entity_id,
            property_name=property_name,
            property_display_name=(action.get("propertyDisplayName") or "")[:200] or None,
            data_type=(action.get("dataType") or "VARCHAR2")[:50],
            is_primary_key="Y" if action.get("isPrimaryKey") == "Y" else "N",
            is_nullable="N" if action.get("isNullable") == "N" else "Y",
            property_desc=(action.get("propertyDesc") or "")[:500] or None,
            order_num=max(int(action.get("orderNum") or 0), 0),
        )
        self.db.add(prop)
        self.db.flush()
        return prop

    def _apply_property_update(
        self,
        entity_id: str,
        prop: SysOntologyProperty,
        action: Dict[str, Any],
    ) -> Optional[str]:
        warning = None
        next_property_name = self.llm_service._sanitize_property_name(action.get("propertyName") or prop.property_name)
        if next_property_name and next_property_name != prop.property_name:
            duplicate = (
                self.db.query(SysOntologyProperty)
                .filter(
                    SysOntologyProperty.entity_id == entity_id,
                    SysOntologyProperty.property_name == next_property_name,
                    SysOntologyProperty.property_id != prop.property_id,
                )
                .first()
            )
            if duplicate:
                warning = f"属性名 {next_property_name} 已存在，保留原属性名 {prop.property_name}"
            else:
                prop.property_name = next_property_name

        if action.get("propertyDisplayName"):
            prop.property_display_name = action.get("propertyDisplayName")
        if "propertyDesc" in action:
            prop.property_desc = action.get("propertyDesc") or None
        if action.get("dataType"):
            prop.data_type = action.get("dataType")
        if action.get("isPrimaryKey") in {"Y", "N"}:
            prop.is_primary_key = action.get("isPrimaryKey")
        if action.get("isNullable") in {"Y", "N"}:
            prop.is_nullable = action.get("isNullable")
        if "orderNum" in action:
            prop.order_num = max(int(action.get("orderNum") or 0), 0)
        prop.updated_at = datetime.utcnow()
        self.db.flush()
        return warning

    def _apply_relation_create(
        self,
        domain_id: str,
        action: Dict[str, Any],
        entity_id_map: Dict[str, str],
    ) -> Optional[SysOntologyRelation]:
        source_entity = self._resolve_entity_for_relation_side(domain_id, action.get("sourceEntityId"), action.get("sourceEntityName"), entity_id_map)
        target_entity = self._resolve_entity_for_relation_side(domain_id, action.get("targetEntityId"), action.get("targetEntityName"), entity_id_map)
        relation_name = (action.get("relationName") or "").strip()
        if not source_entity or not target_entity or not relation_name or source_entity.entity_id == target_entity.entity_id:
            return None
        relation_type = (action.get("relationType") or "ASSOCIATION").strip().upper()
        if relation_type not in {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY", "INHERITANCE", "ASSOCIATION"}:
            relation_type = "ASSOCIATION"
        relation = SysOntologyRelation(
            relation_id=generate_id("rel"),
            domain_id=domain_id,
            source_entity_id=source_entity.entity_id,
            target_entity_id=target_entity.entity_id,
            relation_name=relation_name,
            relation_type=relation_type,
            relation_desc=(action.get("relationDesc") or "")[:1000] or None,
            relation_table_name=self._build_relation_table_name(relation_type, source_entity.entity_name, target_entity.entity_name),
        )
        self.db.add(relation)
        self.db.flush()
        return relation

    def _apply_relation_update(
        self,
        domain_id: str,
        relation: SysOntologyRelation,
        action: Dict[str, Any],
        entity_id_map: Dict[str, str],
    ) -> bool:
        source_entity = self._resolve_entity_for_relation_side(domain_id, action.get("sourceEntityId"), action.get("sourceEntityName"), entity_id_map) or relation.source_entity
        target_entity = self._resolve_entity_for_relation_side(domain_id, action.get("targetEntityId"), action.get("targetEntityName"), entity_id_map) or relation.target_entity
        if not source_entity or not target_entity or source_entity.entity_id == target_entity.entity_id:
            return False
        relation.source_entity_id = source_entity.entity_id
        relation.target_entity_id = target_entity.entity_id
        if action.get("relationName"):
            relation.relation_name = action.get("relationName")
        relation_type = (action.get("relationType") or relation.relation_type).strip().upper()
        if relation_type not in {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY", "INHERITANCE", "ASSOCIATION"}:
            relation_type = "ASSOCIATION"
        relation.relation_type = relation_type
        if "relationDesc" in action:
            relation.relation_desc = action.get("relationDesc") or None
        relation.relation_table_name = self._build_relation_table_name(relation_type, source_entity.entity_name, target_entity.entity_name)
        relation.updated_at = datetime.utcnow()
        self.db.flush()
        return True

    def _resolve_entity(self, domain_id: str, entity_id: Optional[str], entity_name: Optional[str]) -> Optional[SysOntologyEntity]:
        if entity_id:
            entity = (
                self.db.query(SysOntologyEntity)
                .filter(SysOntologyEntity.domain_id == domain_id, SysOntologyEntity.entity_id == entity_id)
                .first()
            )
            if entity:
                return entity
        normalized_name = self.llm_service._sanitize_entity_name(entity_name or "")
        if not normalized_name:
            return None
        return (
            self.db.query(SysOntologyEntity)
            .filter(SysOntologyEntity.domain_id == domain_id, SysOntologyEntity.entity_name == normalized_name)
            .first()
        )

    def _resolve_entity_for_property_action(
        self,
        domain_id: str,
        action: Dict[str, Any],
        entity_id_map: Dict[str, str],
    ) -> Optional[SysOntologyEntity]:
        entity_id = action.get("entityId") or ""
        mapped_entity_id = entity_id_map.get(entity_id, entity_id)
        return self._resolve_entity(domain_id, mapped_entity_id, action.get("entityName"))

    def _resolve_entity_for_relation_side(
        self,
        domain_id: str,
        entity_id: Optional[str],
        entity_name: Optional[str],
        entity_id_map: Dict[str, str],
    ) -> Optional[SysOntologyEntity]:
        actual_id = entity_id_map.get(entity_id or "", entity_id or "")
        return self._resolve_entity(domain_id, actual_id, entity_name)

    def _resolve_property(self, entity_id: str, property_id: Optional[str], property_name: Optional[str]) -> Optional[SysOntologyProperty]:
        if property_id:
            prop = (
                self.db.query(SysOntologyProperty)
                .filter(SysOntologyProperty.entity_id == entity_id, SysOntologyProperty.property_id == property_id)
                .first()
            )
            if prop:
                return prop
        normalized_name = self.llm_service._sanitize_property_name(property_name or "")
        if not normalized_name:
            return None
        return (
            self.db.query(SysOntologyProperty)
            .filter(SysOntologyProperty.entity_id == entity_id, SysOntologyProperty.property_name == normalized_name)
            .first()
        )

    def _resolve_relation(self, domain_id: str, relation_id: Optional[str], action: Dict[str, Any]) -> Optional[SysOntologyRelation]:
        if relation_id:
            relation = (
                self.db.query(SysOntologyRelation)
                .filter(SysOntologyRelation.domain_id == domain_id, SysOntologyRelation.relation_id == relation_id)
                .first()
            )
            if relation:
                return relation
        relation_name = (action.get("relationName") or "").strip()
        if not relation_name:
            return None

        source_entity = self._resolve_entity(domain_id, action.get("sourceEntityId"), action.get("sourceEntityName"))
        target_entity = self._resolve_entity(domain_id, action.get("targetEntityId"), action.get("targetEntityName"))
        if source_entity and target_entity:
            return (
                self.db.query(SysOntologyRelation)
                .filter(
                    SysOntologyRelation.domain_id == domain_id,
                    SysOntologyRelation.source_entity_id == source_entity.entity_id,
                    SysOntologyRelation.target_entity_id == target_entity.entity_id,
                    SysOntologyRelation.relation_name == relation_name,
                )
                .first()
            )
        return None

    def _delete_entity_with_relations(self, entity_id: str) -> None:
        entity = self.db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
        if not entity:
            return
        related_relations = (
            self.db.query(SysOntologyRelation)
            .filter(
                or_(
                    SysOntologyRelation.source_entity_id == entity_id,
                    SysOntologyRelation.target_entity_id == entity_id,
                )
            )
            .all()
        )
        for relation in related_relations:
            self.db.delete(relation)
        self.db.delete(entity)
        self.db.flush()

    def _delete_property(self, prop: SysOntologyProperty) -> None:
        mappings = self.db.query(SysPropertyMapping).filter(SysPropertyMapping.property_id == prop.property_id).all()
        for mapping in mappings:
            self.db.delete(mapping)
        self.db.delete(prop)
        self.db.flush()

    def _refresh_entity_mapping_state(self, entity_id: str) -> None:
        entity = self.db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
        if not entity:
            return
        has_mapped_property = (
            self.db.query(SysOntologyProperty)
            .filter(
                SysOntologyProperty.entity_id == entity_id,
                SysOntologyProperty.source_mark == "MAPPED",
            )
            .first()
        )
        entity.status = "MAPPED" if has_mapped_property else "DRAFT"
        entity_mapping = self.db.query(SysEntityMapping).filter(SysEntityMapping.entity_id == entity_id).first()
        if entity_mapping:
            entity_mapping.mapping_status = "CONFIRMED" if has_mapped_property else "PENDING"
            entity_mapping.mapped_at = datetime.utcnow()
        self.db.flush()

    def _build_graph_position(self, index: int) -> Dict[str, int]:
        column = index % 4
        row = index // 4
        return {
            "x": 80 + column * 220,
            "y": 60 + row * 160,
        }

    def _build_relation_table_name(self, relation_type: str, source_entity_name: str, target_entity_name: str) -> Optional[str]:
        if relation_type != "MANY_TO_MANY":
            return None
        return f"ONTO_REL_{source_entity_name.upper()}_{target_entity_name.upper()}"
