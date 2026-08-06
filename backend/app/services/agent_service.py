import json
from collections import deque
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import (
    SysAgentSkill,
    SysDataSource,
    SysDomain,
    SysLLMConfig,
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
    SysProcessDef,
    generate_id,
)
from app.services.llm_service import LLMService, normalize_model_name
from app.services.source_data_service import SourceDataService


class AgentService:
    def __init__(self, db: Session):
        self.db = db
        self.source_service = SourceDataService(db)
        self.llm_service = LLMService(db)

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
        )
        skill.skill_desc = skill.skill_desc or generated["skill_desc"]
        skill.analysis_goal = skill.analysis_goal or generated["analysis_goal"]
        skill.execution_rules = skill.execution_rules or generated["execution_rules"]
        skill.output_requirements = skill.output_requirements or generated["output_requirements"]
        skill.prompt_template = generated["prompt_template"]
        skill.context_json = json.dumps(
            self._build_skill_context(domain, process, entity, properties, relations),
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
        )
        skill.skill_desc = skill.skill_desc or generated["skill_desc"]
        skill.analysis_goal = skill.analysis_goal or generated["analysis_goal"]
        skill.execution_rules = skill.execution_rules or generated["execution_rules"]
        skill.output_requirements = skill.output_requirements or generated["output_requirements"]
        skill.prompt_template = generated["prompt_template"]
        skill.context_json = json.dumps(
            self._build_skill_context(domain, process, entity, properties, relations),
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

    async def test_skill(self, skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        skill = self.db.query(SysAgentSkill).filter(SysAgentSkill.skill_id == skill_id).first()
        if not skill:
            raise ValueError("技能不存在")
        llm_config = self._get_llm_config(payload.get("llm_config_id") or skill.llm_config_id, purpose="智能体测试")

        context = self._safe_json_loads(skill.context_json, {})
        process_steps = self._extract_process_steps(context.get("process", {}).get("process_json"))
        entity_context = context.get("entity", {})
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
        prompt_preview = self._build_test_prompt(skill, entity_context, table_detail, payload)
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
    ) -> Dict[str, str]:
        fallback = {
            "skill_desc": self._default_skill_desc(domain, process, entity, llm_config),
            "analysis_goal": self._default_analysis_goal(domain, entity),
            "execution_rules": skill.execution_rules or "优先按照流程节点顺序执行，遇到数据不完整时给出风险提示。",
            "output_requirements": skill.output_requirements or "输出结构化结论、关键指标、异常点和建议动作。",
            "prompt_template": self._build_fallback_prompt_template(domain, process, entity, properties, relations, skill, llm_config),
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
6. prompt_template 直接生成给大模型执行的提示词正文，中文为主，结构清晰，可引用流程步骤、本体属性和关系。

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
    ) -> str:
        prop_text = "、".join(
            [
                item.property_display_name or item.property_name
                for item in properties[:8]
            ]
        ) or "无已配置属性"
        relation_text = "、".join([rel.relation_name for rel in relations[:6]]) or "无显式关系"
        steps = self._extract_process_steps(process.process_json)
        step_text = "\n".join([f"{idx + 1}. {step['label']}（{step['type_label']}）" for idx, step in enumerate(steps[:10])]) or "1. 开始准备分析"
        return (
            f"你是业务分析智能体中的数据分析技能“{skill.skill_name}”。\n"
            f"本技能构建所使用的大模型：{llm_config.config_name} / {normalize_model_name(llm_config.model_name, llm_config.api_base_url)}\n"
            f"分析域：{domain.domain_name}\n"
            f"分析目标：{skill.analysis_goal or self._default_analysis_goal(domain, entity)}\n"
            f"本体对象：{entity.entity_display_name or entity.entity_name}\n"
            f"对象说明：{entity.entity_desc or '暂无'}\n"
            f"关键属性：{prop_text}\n"
            f"相关关系：{relation_text}\n"
            f"业务流程：\n{step_text}\n"
            f"执行规则：{skill.execution_rules or '优先按照流程节点顺序执行，遇到数据不完整时给出风险提示。'}\n"
            f"输出要求：{skill.output_requirements or '输出结构化结论、关键指标、异常点和建议动作。'}"
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
    ) -> str:
        columns = "、".join([col["column_name"] for col in table_detail.get("columns", [])[:10]])
        sample_rows = self._safe_json_dumps(table_detail.get("sample_rows", [])[:2])
        input_payload = payload.get("input_payload") or ""
        return (
            f"{skill.prompt_template or ''}\n\n"
            f"测试上下文：\n"
            f"- graph 表：{table_detail.get('owner')}.{table_detail.get('table_name')}\n"
            f"- 表说明：{table_detail.get('table_comment') or '暂无'}\n"
            f"- 字段：{columns or '暂无'}\n"
            f"- 样例数据：{sample_rows}\n"
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
            "prompt_template": skill.prompt_template,
            "context_json": skill.context_json,
            "status": skill.status,
            "created_by": skill.created_by,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
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
