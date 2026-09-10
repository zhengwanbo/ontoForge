from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.models.models import SysDataSource, SysManagedAgentSkill, SysManagedAgentSkillTestSession


@dataclass
class ManagedSkillSessionRuntime:
    managed_skill: SysManagedAgentSkill
    llm_config: Any
    skill_files: Dict[str, str]
    skill_markdown: str
    existing_session: Optional[SysManagedAgentSkillTestSession]
    previous_response: Dict[str, Any]
    previous_turn_results: List[Dict[str, Any]]
    stored_conversation_history: List[Dict[str, str]]
    conversation_history: List[Dict[str, str]]
    source: SysDataSource


class ManagedSkillRuntimeContextLoader:
    def __init__(self, service: Any):
        self.service = service
        self.db = service.db

    def load(self, managed_skill_id: str, payload: Dict[str, Any]) -> ManagedSkillSessionRuntime:
        managed_skill = self.db.query(SysManagedAgentSkill).filter(
            SysManagedAgentSkill.managed_skill_id == managed_skill_id,
            SysManagedAgentSkill.domain_id == payload.get("domain_id"),
            SysManagedAgentSkill.status == "ACTIVE",
        ).first()
        if not managed_skill:
            raise ValueError("托管 Skill 不存在或未启用")
        llm_config = self.service._get_llm_config(payload.get("llm_config_id"), purpose="智能体测试")
        skill_files = self.service._read_managed_skill_files(managed_skill)
        skill_markdown = skill_files["SKILL.md"]
        existing_session: Optional[SysManagedAgentSkillTestSession] = None
        requested_session_id = str(payload.get("session_id") or "").strip()
        if requested_session_id:
            existing_session = self.db.query(SysManagedAgentSkillTestSession).filter(
                SysManagedAgentSkillTestSession.session_id == requested_session_id
            ).first()
            if not existing_session or existing_session.managed_skill_id != managed_skill_id:
                raise ValueError("测试会话不存在，或不属于当前 Skill")
        previous_response = self.service._safe_json_loads(existing_session.result_json, {}) if existing_session else {}
        previous_turn_results = previous_response.get("turn_results", []) if isinstance(previous_response, dict) else []
        if not isinstance(previous_turn_results, list):
            previous_turn_results = []
        raw_conversation_history = self.service._safe_json_loads(
            existing_session.conversation_json, []
        ) if existing_session else payload.get("conversation_history")
        stored_conversation_history = self.service._normalize_conversation_history(raw_conversation_history, limit=None)
        conversation_history = self.service._normalize_conversation_history(stored_conversation_history)
        if existing_session and not previous_turn_results and isinstance(previous_response, dict) and previous_response.get("table_preview"):
            previous_turn_results = [{
                "turn_no": 1,
                "user_message_no": len([item for item in stored_conversation_history if item.get("role") == "user"]) - 1,
                "question": (previous_response.get("test_context") or {}).get("test_question", ""),
                "table_preview": previous_response.get("table_preview", {}),
                "plan": previous_response.get("plan", {}),
                "evidence_tables": previous_response.get("evidence_tables", []),
                "analysis_result": previous_response.get("analysis_result", {}),
                "completion_assessment": previous_response.get("completion_assessment", {}),
                "agent_output": previous_response.get("agent_output", ""),
                "execution_trace": previous_response.get("execution_trace", []),
                "execution_events": previous_response.get("execution_events", []),
                "executed_queries": previous_response.get("executed_queries", []),
                "warnings": previous_response.get("warnings", []),
            }]
        source = self.db.query(SysDataSource).filter(SysDataSource.source_id == payload["source_id"]).first()
        if not source or source.business_domain_id != managed_skill.domain_id:
            raise ValueError("所选对象数据库不属于当前 Skill 的业务分析域")
        return ManagedSkillSessionRuntime(
            managed_skill=managed_skill,
            llm_config=llm_config,
            skill_files=skill_files,
            skill_markdown=skill_markdown,
            existing_session=existing_session,
            previous_response=previous_response,
            previous_turn_results=previous_turn_results,
            stored_conversation_history=stored_conversation_history,
            conversation_history=conversation_history,
            source=source,
        )
