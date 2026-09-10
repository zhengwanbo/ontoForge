import json
from typing import Any, Dict, List

from app.services.llm_service import normalize_model_name

from .completion_assessor import ManagedSkillCompletionAssessor


class ManagedSkillResponseBuilder:
    DEFAULT_WARNING = "当前测试已支持基于本体图路径的对象证据查询、单值聚合、按对象分组统计、按时间窗口趋势统计、按对象与时间联合统计以及受控公式指标；复杂事实口径仍限制为服务端白名单编译，不支持任意公式直出 SQL。"

    def __init__(self, service: Any):
        self.service = service
        self.completion_assessor = ManagedSkillCompletionAssessor(service)

    def build_intro_response(self, *, runtime: Any, payload: Dict[str, Any], topology: Dict[str, Any], intro: str) -> Dict[str, Any]:
        return {
            "managed_skill": self.service._serialize_managed_skill(runtime.managed_skill),
            "execution_model": {
                "llm_config_id": runtime.llm_config.config_id,
                "llm_config_name": runtime.llm_config.config_name,
                "llm_model_name": normalize_model_name(runtime.llm_config.model_name, runtime.llm_config.api_base_url),
            },
            "test_context": {
                "source_id": payload["source_id"],
                "source_name": runtime.source.source_name if runtime.source else "",
                "schema": topology.get("schema"),
                "property_graph": topology.get("graph_name"),
                "ontology_node": "",
                "intent_type": "SESSION_READY",
                "test_question": "",
            },
            "conversation": [{"role": "assistant", "content": intro}],
            "agent_output": intro,
            "plan": {"plan_version": "1.0", "intent_type": "SESSION_READY", "selected_objects": [], "steps": []},
            "execution_trace": [
                {"step_no": 1, "stage": "SKILL_LOAD", "title": "加载上传 Skill", "status": "SUCCESS", "detail": f"已加载 SKILL.md 及 {len(runtime.skill_files) - 1} 个参考文件。"},
                {"step_no": 2, "stage": "SESSION_READY", "title": "初始化测试会话", "status": "SUCCESS", "detail": f"已绑定数据源 {runtime.source.source_name if runtime.source else payload['source_id']} 与属性图 {topology.get('graph_name')}。"},
            ],
            "execution_events": [
                {"event_type": "SESSION_READY", "step_id": "session", "title": "测试会话已初始化", "runtime_state": "COMPLETED", "status": "SUCCESS", "detail": intro, "payload": {}},
            ],
            "executed_queries": [],
            "warnings": [],
            "table_preview": {"columns": [], "sample_rows": []},
            "evidence_tables": [],
            "analysis_result": {
                "summary": intro,
                "intent_type": "SESSION_READY",
                "selected_objects": [],
                "evidence_table_keys": [],
                "applied_metrics": [],
                "matched_rules": [],
                "suggested_activities": [],
                "trend_summaries": [],
                "analysis_flags": [],
                "period_comparisons": [],
                "top_findings": [],
            },
            "completion_assessment": {
                "status": "PENDING",
                "completed": False,
                "deterministic": {"status": "SKIPPED", "reason": "测试会话刚初始化，尚未开始执行。"},
                "coverage_check": {"status": "SKIPPED", "reason": "测试会话刚初始化，尚未开始执行。"},
                "judge": {"required": False, "status": "SKIPPED", "reason": "测试会话刚初始化，尚未开始执行。"},
                "summary": "测试会话已初始化，等待用户问题。",
            },
            "planning_stats": {
                "total_turns": 0,
                "actionable_turns": 0,
                "reference_pattern_turns": 0,
                "llm_plan_turns": 0,
                "clarification_turns": 0,
                "session_ready_turns": 1,
                "reference_pattern_hit_rate": 0.0,
                "llm_planner_saved_count": 0,
                "latest_planning_mode": "",
                "latest_reference_pattern_id": "",
                "top_reference_patterns": [],
            },
            "turn_results": runtime.previous_turn_results,
        }

    def build_analysis_prompt(
        self,
        *,
        skill_markdown: str,
        skill_files: Dict[str, str],
        source_name: str,
        topology: Dict[str, Any],
        plan: Dict[str, Any],
        selected_node: Dict[str, Any],
        question: str,
        conversation_context: str,
        evidence_tables: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        references = "\n\n".join(
            f"## {path}\n{content}" for path, content in skill_files.items() if path != "SKILL.md"
        )[:30000]
        evidence_payload = json.dumps(
            [
                {
                    "title": item.get("title"),
                    "kind": item.get("kind"),
                    "related_objects": item.get("related_objects"),
                    "row_count": item.get("row_count"),
                    "columns": [column.get("column_name") for column in (item.get("columns") or [])],
                    "sample_rows": item.get("sample_rows"),
                }
                for item in evidence_tables[:6]
            ],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        system_prompt = """你是供应链数据分析智能体。严格遵守用户上传的 Skill：只依据给定 Skill、Oracle Property Graph 本体属性、只读查询结果和用户问题分析，不臆造字段、数据或查询结果。

当前用户问题是本轮唯一需要回答的目标。历史对话仅用于解析“该瓶码”“继续”等指代，或寻找与当前问题直接相关的已知标识；不得复用历史问题的结论、SQL、字段或表格来代替当前问题的回答。若当前问题无法确定查询对象、关系或标识，直接提出简洁澄清问题，不能猜测或执行与上一轮相同的查询。

平台会在你的文字回答前先以结构化表格展示本轮一个或多个证据表。你的职责是在表格之后，严格按 Skill 要求解读数据、给出结论和可继续追问的问题；如果 Skill 未规定格式，则依次输出结论摘要、数据解读和建议追问。不要重复罗列整张原始数据表，也不要强制输出“风险与限制”章节。

不得只罗列实例 ID；必须优先说明查询结果中实际返回的产品、批次、工厂、质检、码、仓储或渠道等本体业务属性。不要输出或建议任何写入、删除、DDL、权限或凭据操作。"""
        user_prompt = f"""# 已加载 Skill
{skill_markdown[:30000]}

# Skill 参考文件
{references or '无'}

# 数据源上下文
- 数据源：{source_name}
- Oracle Property Graph：{topology.get('schema')}.{topology.get('graph_name')}
- 分析意图：{plan.get('intent_type')}
- 本体查询对象：{selected_node.get('displayName')}（底层对象：{selected_node.get('tableName')}）
- 当前用户问题：{question}

# 历史对话（仅用于解析指代与寻找当前问题相关标识，不是本轮回答目标）
{conversation_context or '这是一次新会话，尚无历史消息。'}

# 受控执行计划
{json.dumps(plan, ensure_ascii=False, indent=2)}

# 已执行的证据表
{evidence_payload}
"""
        return {"system_prompt": system_prompt, "user_prompt": user_prompt}

    def build_turn_response(
        self,
        *,
        runtime: Any,
        payload: Dict[str, Any],
        plan: Dict[str, Any],
        selected_node: Dict[str, Any],
        topology: Dict[str, Any],
        evidence_tables: List[Dict[str, Any]],
        executed_queries: List[Dict[str, Any]],
        execution_events: List[Dict[str, Any]],
        agent_output: str,
        previous_turn_results: List[Dict[str, Any]],
        stored_conversation_history: List[Dict[str, str]],
        question: str,
        is_session_start: bool,
    ) -> Dict[str, Any]:
        table_preview = self.service._build_table_preview_from_evidence(evidence_tables)
        executed_sql = executed_queries[0]["sql"] if executed_queries else ""
        trace = [
            {"step_no": 1, "stage": "SKILL_LOAD", "title": "加载上传 Skill", "status": "SUCCESS", "detail": f"已加载 SKILL.md 及 {len(runtime.skill_files) - 1} 个参考文件。"},
            {"step_no": 2, "stage": "SEMANTIC_GUIDANCE_LOAD", "title": "加载指标规则活动语义", "status": "SUCCESS", "detail": "已向计划生成器和分析 Agent 提供 Skill 中的指标目录、规则目录、活动手册、分析策略与执行契约。"},
            {"step_no": 3, "stage": "PLAN_BUILD", "title": "生成受控执行计划", "status": "SUCCESS", "detail": f"识别分析意图为 {plan.get('intent_type')}，选择对象 {selected_node.get('displayName')}。原因：{selected_node.get('reason')}"},
            {"step_no": 4, "stage": "GRAPH_QUERY_EXECUTION", "title": "执行图证据查询", "status": "SUCCESS", "detail": f"共执行 {len(executed_queries)} 条只读 Oracle 查询，生成 {len(evidence_tables)} 个证据表。", "sql": executed_sql},
            {"step_no": 5, "stage": "AGENT_ANALYSIS", "title": "Agent 按 Skill 分析", "status": "SUCCESS", "detail": "已将 Skill 指令、会话上下文、受控计划和多对象证据表发送给分析 Agent。"},
        ]
        conversation = stored_conversation_history + ([] if is_session_start else [{"role": "user", "content": question}])
        conversation.append({"role": "assistant", "content": agent_output})
        analysis_result = self.service._build_managed_skill_analysis_result(
            agent_output=agent_output,
            plan=plan,
            evidence_tables=evidence_tables,
            skill_files=runtime.skill_files,
        )
        completion_assessment = self.completion_assessor.assess(
            plan=plan,
            selected_node=selected_node,
            evidence_tables=evidence_tables,
            executed_queries=executed_queries,
            analysis_result=analysis_result,
        )
        current_turn = {
            "turn_no": len(previous_turn_results) + 1,
            "user_message_no": len([item for item in conversation if item.get("role") == "user"]) - 1,
            "question": "" if is_session_start else question,
            "table_preview": table_preview,
            "plan": plan,
            "evidence_tables": evidence_tables,
            "analysis_result": analysis_result,
            "completion_assessment": completion_assessment,
            "agent_output": agent_output,
            "execution_trace": trace,
            "execution_events": execution_events,
            "executed_queries": executed_queries,
            "warnings": [self.DEFAULT_WARNING],
        }
        turn_results = previous_turn_results + ([] if is_session_start else [current_turn])
        planning_stats = self.service._build_managed_skill_planning_stats({"turn_results": turn_results})
        return {
            "managed_skill": self.service._serialize_managed_skill(runtime.managed_skill),
            "execution_model": {
                "llm_config_id": runtime.llm_config.config_id,
                "llm_config_name": runtime.llm_config.config_name,
                "llm_model_name": normalize_model_name(runtime.llm_config.model_name, runtime.llm_config.api_base_url),
            },
            "test_context": {
                "source_id": payload["source_id"],
                "source_name": runtime.source.source_name if runtime.source else "",
                "schema": topology.get("schema"),
                "property_graph": topology.get("graph_name"),
                "ontology_node": selected_node.get("displayName"),
                "intent_type": plan.get("intent_type"),
                "test_question": "" if is_session_start else question,
            },
            "conversation": conversation,
            "agent_output": agent_output,
            "plan": plan,
            "execution_trace": trace,
            "execution_events": execution_events,
            "executed_queries": executed_queries,
            "warnings": [self.DEFAULT_WARNING],
            "table_preview": table_preview,
            "evidence_tables": evidence_tables,
            "analysis_result": analysis_result,
            "completion_assessment": completion_assessment,
            "planning_stats": planning_stats,
            "turn_results": turn_results,
        }
