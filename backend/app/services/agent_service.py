import json
import re
from collections import deque
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import (
    SysAgentSkill,
    SysManagedAgentSkill,
    SysManagedAgentSkillTestSession,
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

    def list_managed_skills(self) -> List[Dict[str, Any]]:
        rows = self.db.query(SysManagedAgentSkill).order_by(
            SysManagedAgentSkill.updated_at.desc(),
            SysManagedAgentSkill.created_at.desc(),
        ).all()
        return [self._serialize_managed_skill(item) for item in rows]

    def upload_managed_skill(self, filename: str, content: bytes, uploaded_by: str) -> Dict[str, Any]:
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

    def delete_managed_skill(self, managed_skill_id: str):
        record = self.db.query(SysManagedAgentSkill).filter(SysManagedAgentSkill.managed_skill_id == managed_skill_id).first()
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

    async def test_managed_skill(self, managed_skill_id: str, payload: Dict[str, Any], created_by: str = "unknown") -> Dict[str, Any]:
        """Use an uploaded Skill package with sampled, read-only source data for an agent test."""
        managed_skill = self.db.query(SysManagedAgentSkill).filter(
            SysManagedAgentSkill.managed_skill_id == managed_skill_id,
            SysManagedAgentSkill.status == "ACTIVE",
        ).first()
        if not managed_skill:
            raise ValueError("托管 Skill 不存在或未启用")
        llm_config = self._get_llm_config(payload.get("llm_config_id"), purpose="智能体测试")
        skill_files = self._read_managed_skill_files(managed_skill)
        skill_markdown = skill_files["SKILL.md"]
        existing_session = None
        requested_session_id = str(payload.get("session_id") or "").strip()
        if requested_session_id:
            existing_session = self.db.query(SysManagedAgentSkillTestSession).filter(
                SysManagedAgentSkillTestSession.session_id == requested_session_id
            ).first()
            if not existing_session or existing_session.managed_skill_id != managed_skill_id:
                raise ValueError("测试会话不存在，或不属于当前 Skill")
        previous_response = self._safe_json_loads(existing_session.result_json, {}) if existing_session else {}
        previous_turn_results = previous_response.get("turn_results", []) if isinstance(previous_response, dict) else []
        if not isinstance(previous_turn_results, list):
            previous_turn_results = []
        raw_conversation_history = self._safe_json_loads(
            existing_session.conversation_json, []
        ) if existing_session else payload.get("conversation_history")
        stored_conversation_history = self._normalize_conversation_history(raw_conversation_history, limit=None)
        conversation_history = self._normalize_conversation_history(stored_conversation_history)
        # 兼容本次升级前仅保存最后一轮结果的会话；更早轮次没有落库，无法可靠补建。
        if existing_session and not previous_turn_results and isinstance(previous_response, dict) and previous_response.get("table_preview"):
            previous_turn_results = [{
                "turn_no": 1,
                "user_message_no": len([item for item in stored_conversation_history if item.get("role") == "user"]) - 1,
                "question": (previous_response.get("test_context") or {}).get("test_question", ""),
                "table_preview": previous_response.get("table_preview", {}),
                "agent_output": previous_response.get("agent_output", ""),
                "execution_trace": previous_response.get("execution_trace", []),
                "executed_queries": previous_response.get("executed_queries", []),
                "warnings": previous_response.get("warnings", []),
            }]
        is_session_start = bool(payload.get("start_session"))
        question = (payload.get("test_question") or "").strip()
        if not question:
            question = "请开始测试会话，说明你将如何依据已加载 Skill 对当前数据源进行分析，并等待我的问题。"
        source = self.db.query(SysDataSource).filter(SysDataSource.source_id == payload["source_id"]).first()
        if not is_session_start and self._needs_question_clarification(question):
            return self._save_managed_skill_clarification(
                session=existing_session,
                managed_skill=managed_skill,
                source=source,
                payload=payload,
                question=question,
                stored_conversation_history=stored_conversation_history,
                previous_turn_results=previous_turn_results,
                created_by=created_by,
            )
        topology = self.source_service.get_remote_property_graph_topology(
            source_id=payload["source_id"], schema=payload.get("schema")
        )
        if not topology.get("graph_name") or not topology.get("nodes"):
            raise ValueError("所选数据源没有可用 Oracle Property Graph，无法按本体属性执行图查询")
        graph_plan = await self._plan_graph_query_from_topology(
            question=question,
            conversation_context=self._format_conversation_history(conversation_history),
            topology=topology,
            llm_config=llm_config,
        )
        if not graph_plan:
            graph_plan = self._build_supply_chain_graph_plan(
                question, topology, self._format_conversation_history(conversation_history)
            )
        if graph_plan:
            selected_node = graph_plan["selection"]
            graph_sql = graph_plan["sql"]
        else:
            selected_node = await self._select_managed_skill_graph_node(
                skill_markdown=skill_markdown,
                question=question,
                llm_config=llm_config,
                topology=topology,
            )
            graph_sql = self._build_graph_node_property_sql(
                graph_name=topology["graph_name"],
                node=selected_node,
            )
        graph_result = self.source_service.execute_remote_graph_query(
            source_id=payload["source_id"],
            graph_sql=graph_sql,
            schema=payload.get("schema"),
            row_limit=max(1, min(int(payload.get("sample_limit") or 100), 100)),
        )
        executed_sql = graph_sql
        references = "\n\n".join(
            f"## {path}\n{content}" for path, content in skill_files.items() if path != "SKILL.md"
        )[:30000]
        sample_rows = json.dumps(graph_result.get("rows", [])[:100], ensure_ascii=False, indent=2, default=str)
        columns = json.dumps(graph_result.get("columns", [])[:30], ensure_ascii=False, indent=2, default=str)
        system_prompt = """你是供应链数据分析智能体。严格遵守用户上传的 Skill：只依据给定 Skill、Oracle Property Graph 本体属性、只读查询结果和用户问题分析，不臆造字段、数据或查询结果。

当前用户问题是本轮唯一需要回答的目标。历史对话仅用于解析“该瓶码”“继续”等指代，或寻找与当前问题直接相关的已知标识；不得复用历史问题的结论、SQL、字段或表格来代替当前问题的回答。若当前问题无法确定查询对象、关系或标识，直接提出简洁澄清问题，不能猜测或执行与上一轮相同的查询。

平台会在你的文字回答前先以结构化表格展示本轮 SQL 返回数据。你的职责是在表格之后，严格按 Skill 要求解读数据、给出结论和可继续追问的问题；如果 Skill 未规定格式，则依次输出结论摘要、数据解读和建议追问。不要重复罗列整张原始数据表，也不要强制输出“风险与限制”章节。

不得只罗列实例 ID；必须优先说明查询结果中实际返回的产品、批次、工厂、质检、码、仓储或渠道等本体业务属性。不要输出或建议任何写入、删除、DDL、权限或凭据操作。"""
        user_prompt = f"""# 已加载 Skill
{skill_markdown[:30000]}

# Skill 参考文件
{references or '无'}

# 数据源上下文
- 数据源：{source.source_name if source else payload['source_id']}
- Oracle Property Graph：{topology.get('schema')}.{topology.get('graph_name')}
- 本体查询对象：{selected_node.get('displayName')}（底层对象：{selected_node.get('tableName')}）
- 当前用户问题：{question}

# 历史对话（仅用于解析指代与寻找当前问题相关标识，不是本轮回答目标）
{self._format_conversation_history(conversation_history) or '这是一次新会话，尚无历史消息。'}

# 已执行的只读 SQL
{executed_sql}

# 可用字段
{columns}

# SQL 返回的数据样例
{sample_rows}
"""
        agent_output = await self.llm_service.call_llm(
            system_prompt, user_prompt, llm_config, timeout_override=max(llm_config.timeout, 120)
        )
        managed_skill.use_count = (managed_skill.use_count or 0) + 1
        self.db.commit()
        trace = [
            {"step_no": 1, "stage": "SKILL_LOAD", "title": "加载上传 Skill", "status": "SUCCESS", "detail": f"已加载 SKILL.md 及 {len(skill_files) - 1} 个参考文件。"},
            {"step_no": 2, "stage": "ONTOLOGY_NODE_SELECTION", "title": "Agent 选择本体查询对象", "status": "SUCCESS", "detail": f"在属性图 {topology.get('graph_name')} 中选择 {selected_node.get('displayName')}。原因：{selected_node.get('reason')}"},
            {"step_no": 3, "stage": "GRAPH_SCHEMA_INSPECTION", "title": "检查本体属性与关系", "status": "SUCCESS", "detail": f"底层对象为 {selected_node.get('tableName')}，本次 GRAPH_TABLE 返回 {len(graph_result.get('columns', []))} 个本体属性或汇总字段。"},
            {"step_no": 4, "stage": "ORACLE_GRAPH_QUERY", "title": "执行 Oracle Graph SQL", "status": "SUCCESS", "detail": f"使用 GRAPH_TABLE 查询并返回 {len(graph_result.get('rows', []))} 条本体实例记录。", "sql": executed_sql},
            {"step_no": 5, "stage": "AGENT_ANALYSIS", "title": "Agent 按 Skill 分析", "status": "SUCCESS", "detail": "已将 Skill 指令、会话上下文、本体属性字段、Graph SQL 结果和当前问题发送给分析 Agent。"},
        ]
        conversation = stored_conversation_history + ([] if is_session_start else [{"role": "user", "content": question}])
        conversation.append({"role": "assistant", "content": agent_output})
        table_preview = {"columns": [{"column_name": column} for column in graph_result.get("columns", [])], "sample_rows": graph_result.get("rows", [])}
        current_turn = {
            "turn_no": len(previous_turn_results) + 1,
            "user_message_no": len([item for item in conversation if item.get("role") == "user"]) - 1,
            "question": "" if is_session_start else question,
            "table_preview": table_preview,
            "agent_output": agent_output,
            "execution_trace": trace,
            "executed_queries": [{"purpose": "按 Skill 获取本体节点属性证据（Oracle Graph SQL）", "sql": executed_sql, "row_count": len(graph_result.get("rows", []))}],
            "warnings": ["当前测试使用 Oracle GRAPH_TABLE 返回本体业务属性；涉及数量、金额等未建模为图属性的事实指标时，会在图关系定位后通过经批准的只读事实表聚合。"],
        }
        turn_results = previous_turn_results + ([] if is_session_start else [current_turn])
        response = {
            "managed_skill": self._serialize_managed_skill(managed_skill),
            "execution_model": {"llm_config_id": llm_config.config_id, "llm_config_name": llm_config.config_name, "llm_model_name": normalize_model_name(llm_config.model_name, llm_config.api_base_url)},
            "test_context": {"source_id": payload["source_id"], "source_name": source.source_name if source else "", "schema": topology.get("schema"), "property_graph": topology.get("graph_name"), "ontology_node": selected_node.get("displayName"), "test_question": "" if is_session_start else question},
            "conversation": conversation,
            "agent_output": agent_output,
            "execution_trace": trace,
            "executed_queries": [{"purpose": "按 Skill 获取本体节点属性证据（Oracle Graph SQL）", "sql": executed_sql, "row_count": len(graph_result.get("rows", []))}],
            "warnings": ["当前测试使用 Oracle GRAPH_TABLE 返回本体业务属性；涉及数量、金额等未建模为图属性的事实指标时，会在图关系定位后通过经批准的只读事实表聚合。"],
            "table_preview": table_preview,
            "turn_results": turn_results,
        }
        session = self._save_managed_skill_test_session(
            session=existing_session,
            managed_skill=managed_skill,
            source=source,
            payload=payload,
            question=question,
            conversation=conversation,
            response=response,
            created_by=created_by,
        )
        response["session_id"] = session.session_id
        return response

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
                sample_limit=max(1, min(int(payload.get("sample_limit") or 100), 100)),
                created_by=created_by or "unknown",
            )
            self.db.add(session)
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
            "agent_output": clarification,
            "execution_trace": trace,
            "executed_queries": [],
            "warnings": [],
        }
        response = {
            "managed_skill": self._serialize_managed_skill(managed_skill),
            "execution_model": {"llm_config_id": payload["llm_config_id"]},
            "test_context": {"source_id": payload["source_id"], "source_name": source.source_name if source else "", "schema": payload.get("schema"), "property_graph": "", "ontology_node": "", "test_question": question},
            "conversation": conversation,
            "agent_output": clarification,
            "execution_trace": trace,
            "executed_queries": [],
            "warnings": [],
            "table_preview": table_preview,
            "turn_results": previous_turn_results + [current_turn],
        }
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

    def _serialize_managed_skill_test_session(
        self, session: SysManagedAgentSkillTestSession, *, include_result: bool
    ) -> Dict[str, Any]:
        data = {
            "session_id": session.session_id,
            "managed_skill_id": session.managed_skill_id,
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
        }
        if include_result:
            data["conversation"] = self._safe_json_loads(session.conversation_json, [])
            data["result"] = self._safe_json_loads(session.result_json, {})
        return data

    async def _select_managed_skill_graph_node(
        self,
        *,
        skill_markdown: str,
        question: str,
        llm_config: SysLLMConfig,
        topology: Dict[str, Any],
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
        package_files = await self._generate_skill_package_files(
            domain=domain,
            process=process,
            entity=entity,
            skill=skill,
            llm_config=llm_config,
            topology=topology,
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
    ) -> Dict[str, str]:
        graph_reference = self._build_graph_reference(topology)
        flow_reference = self._build_flow_reference(process)
        fallback = {
            "SKILL.md": self._build_skill_markdown(domain, process, entity, skill, topology),
            "references/property-graph.md": graph_reference,
            "references/analysis-flow.md": flow_reference,
        }
        system_prompt = """你是 Agent Skill 打包专家。根据用户给出的技能配置、业务流程和 Oracle Property Graph 实时拓扑，生成可直接被 Agent 加载的技能包文件。

必须遵守：
1. 输出严格 JSON，格式为 {"files":[{"path":"SKILL.md","content":"..."}]}。
2. 必须包含 SKILL.md；可额外生成 references/*.md、references/*.sql、references/*.json 文件。
3. SKILL.md 使用标准 Agent Skill 风格：YAML frontmatter（name、description）、适用范围、输入、执行工作流、只读安全约束、输出格式和限制。
4. 数据库只允许 SELECT / WITH ... SELECT / GRAPH_TABLE 查询；严禁 DDL、DML、PL/SQL、权限操作、凭据及任何密码。
5. 所有图标签、关系、顶点属性必须来自提供的实时拓扑；不要臆造数据库对象。对于图形结果，要求返回 SOURCE_ID、TARGET_ID、RELATION_NAME，并为不同类型 ID 加前缀。
6. 文件路径必须是相对路径，不能包含 ..；总文件数不超过 8 个，每个文件不超过 24000 字符。
7. 用中文编写说明和规则，SQL 保持 Oracle 语法。"""
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
            "required_references": {
                "property_graph_reference": graph_reference,
                "analysis_flow_reference": flow_reference,
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
                files.setdefault("references/analysis-flow.md", flow_reference)
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

    def _build_skill_markdown(self, domain: SysDomain, process: SysProcessDef, entity: Any, skill: SysAgentSkill, topology: Dict[str, Any]) -> str:
        steps = self._extract_process_steps(process.process_json)
        step_text = "\n".join(f"{item['step_no']}. {item['label']}（{item['type_label']}）" for item in steps) or "1. 校验输入并准备分析上下文。"
        graph_name = topology.get("graph_name") or entity.entity_name
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
- 使用用户提供的精确业务标识（如码、批次、单据）作为查询入口；标识无法命中时如实说明，不做无边界检索。

## 执行工作流

{step_text}

## 执行规则

{skill.execution_rules or '先校验输入和图谱对象，再以最小范围只读查询取得证据；数据缺失时明确限制。'}

## 安全约束

- 仅允许 `SELECT` 或 `WITH ... SELECT`，图查询使用 `GRAPH_TABLE`。
- 禁止 DDL、DML、PL/SQL、权限操作、凭据和密码。
- 只使用 `references/property-graph.md` 中存在的图标签、关系和属性；不要臆造对象。

## 输出要求

{skill.output_requirements or '输出结论、证据、异常或限制、建议动作，以及必要时使用的只读 SQL。'}

## 参考文件

- `references/property-graph.md`：从数据库实时读取的图谱拓扑。
- `references/analysis-flow.md`：当前技能配置的分析流程。
"""

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
