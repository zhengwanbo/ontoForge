import asyncio
import contextlib
from typing import Any, Dict, List, Optional

from .event_stream import build_sse_frame
from .plan_builder import ManagedSkillPlanBuilder
from .response_builder import ManagedSkillResponseBuilder
from .runtime_context import ManagedSkillRuntimeContextLoader
from .step_executor import ManagedSkillStepExecutor


class ManagedSkillTestOrchestrator:
    """Orchestrates managed-skill test sessions while reusing AgentService helpers."""

    def __init__(self, service: Any):
        self.service = service
        self.db = service.db
        self.context_loader = ManagedSkillRuntimeContextLoader(service)
        self.plan_builder = ManagedSkillPlanBuilder(service)
        self.response_builder = ManagedSkillResponseBuilder(service)
        self.step_executor = ManagedSkillStepExecutor(service)

    def start_session(self, managed_skill_id: str, payload: Dict[str, Any], created_by: str = "unknown") -> Dict[str, Any]:
        runtime = self.context_loader.load(managed_skill_id, payload)
        topology = self.service.source_service.get_remote_property_graph_topology(
            source_id=payload["source_id"], schema=payload.get("schema")
        )
        if not topology.get("graph_name") or not topology.get("nodes"):
            raise ValueError("所选数据源没有可用 Oracle Property Graph，无法启动测试会话")
        intro = (
            f"已加载 Skill《{runtime.managed_skill.skill_name}》和属性图 "
            f"{topology.get('schema')}.{topology.get('graph_name')}。"
            "请直接输入客户问题，我会根据 Skill 选择合适的本体对象和关系组合进行只读分析。"
        )
        response = self.response_builder.build_intro_response(runtime=runtime, payload=payload, topology=topology, intro=intro)
        session = self.service._save_managed_skill_test_session(
            session=runtime.existing_session,
            managed_skill=runtime.managed_skill,
            source=runtime.source,
            payload=payload,
            question="初始化测试会话",
            conversation=response["conversation"],
            response=response,
            created_by=created_by,
        )
        response["session_id"] = session.session_id
        return response

    @staticmethod
    def _event_payload(
        event_type: str,
        title: str,
        detail: str,
        *,
        status: str = "RUNNING",
        runtime_state: str = "",
        step_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "event_type": event_type,
            "step_id": step_id,
            "title": title,
            "runtime_state": runtime_state,
            "status": status,
            "detail": detail,
            "payload": payload or {},
        }

    async def _plan_and_execute(
        self,
        *,
        runtime: Any,
        payload: Dict[str, Any],
        topology: Dict[str, Any],
        question: str,
        conversation_context: str,
        planning_feedback: str = "",
        excluded_root_labels: Optional[List[str]] = None,
        excluded_target_labels: Optional[List[str]] = None,
        preferred_root_label: str = "",
        event_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        plan_context = await self.plan_builder.build(
            skill_markdown=runtime.skill_markdown,
            skill_files=runtime.skill_files,
            topology=topology,
            llm_config=runtime.llm_config,
            question=question,
            conversation_context=conversation_context,
            planning_feedback=planning_feedback,
            excluded_root_labels=excluded_root_labels or [],
            excluded_target_labels=excluded_target_labels or [],
            preferred_root_label=preferred_root_label,
        )
        step_execution = self.step_executor.execute_graph_evidence(
            plan=plan_context["plan"],
            topology=topology,
            source_id=payload["source_id"],
            schema=payload.get("schema"),
            sample_limit=max(1, min(int(payload.get("sample_limit") or 100), 1000)),
            skill_files=runtime.skill_files,
            event_callback=event_callback,
        )
        return {**plan_context, **step_execution}

    async def execute_turn(self, managed_skill_id: str, payload: Dict[str, Any], created_by: str = "unknown") -> Dict[str, Any]:
        runtime = self.context_loader.load(managed_skill_id, payload)
        is_session_start = bool(payload.get("start_session"))
        all_execution_events: List[Dict[str, Any]] = []

        def collect(event: Dict[str, Any]) -> None:
            all_execution_events.append(event)

        question = (payload.get("test_question") or "").strip()
        if not question:
            question = "请开始测试会话，说明你将如何依据已加载 Skill 对当前数据源进行分析，并等待我的问题。"
        if not is_session_start and self.service._needs_question_clarification(question):
            return self.service._save_managed_skill_clarification(
                session=runtime.existing_session,
                managed_skill=runtime.managed_skill,
                source=runtime.source,
                payload=payload,
                question=question,
                stored_conversation_history=runtime.stored_conversation_history,
                previous_turn_results=runtime.previous_turn_results,
                created_by=created_by,
            )
        topology = self.service.source_service.get_remote_property_graph_topology(
            source_id=payload["source_id"], schema=payload.get("schema")
        )
        if not topology.get("graph_name") or not topology.get("nodes"):
            raise ValueError("所选数据源没有可用 Oracle Property Graph，无法按本体属性执行图查询")
        conversation_context = self.service._format_conversation_history(runtime.conversation_history)
        collect(self._event_payload("SKILL_LOAD", "加载上传 Skill", f"已加载 SKILL.md 及 {len(runtime.skill_files) - 1} 个参考文件。", status="SUCCESS", runtime_state="COMPLETED", step_id="skill_load"))
        collect(self._event_payload("GRAPH_READY", "加载属性图拓扑", f"已加载 {topology.get('schema')}.{topology.get('graph_name')} 拓扑。", status="SUCCESS", runtime_state="COMPLETED", step_id="graph_ready"))
        collect(self._event_payload("PLAN_BUILD", "生成受控执行计划", "正在根据 Skill、问题和拓扑生成查询计划。", status="RUNNING", runtime_state="PLANNED", step_id="plan_build"))
        execution_result = await self._plan_and_execute(
            runtime=runtime,
            payload=payload,
            topology=topology,
            question=question,
            conversation_context=conversation_context,
            event_callback=collect,
        )
        if execution_result.get("needs_replan"):
            replan_feedback = execution_result.get("replan_reason") or "上一次计划未命中数据，请改用新的根对象或关系路径。"
            replan_hints = execution_result.get("replan_hints") or {}
            collect(self._event_payload("REPLANNING", "自动重规划", replan_feedback, status="RUNNING", runtime_state="REPLANNING", step_id="replan"))
            execution_result = await self._plan_and_execute(
                runtime=runtime,
                payload=payload,
                topology=topology,
                question=question,
                conversation_context=conversation_context,
                planning_feedback=replan_feedback,
                excluded_root_labels=replan_hints.get("excluded_root_labels") or ([execution_result["plan"]["steps"][0]["label"]] if execution_result.get("plan", {}).get("steps") else []),
                excluded_target_labels=replan_hints.get("excluded_target_labels") or [],
                preferred_root_label=replan_hints.get("preferred_root_label") or "",
                event_callback=collect,
            )
            collect(self._event_payload("REPLANNING", "自动重规划完成", "已根据空结果重新生成并执行计划。", status="SUCCESS", runtime_state="REPLANNING", step_id="replan"))
        plan = execution_result["plan"]
        selected_node = execution_result["selected_node"]
        evidence_tables = execution_result["evidence_tables"]
        executed_queries = execution_result["executed_queries"]
        if plan.get("planning_mode") == "REFERENCE_PATTERN":
            collect(self._event_payload(
                "REFERENCE_PATTERN_MATCHED",
                "命中受控参考模式",
                f"已命中参考模式 {plan.get('reference_pattern_id') or 'unknown'}，本轮绕过 LLM planner。",
                status="SUCCESS",
                runtime_state="PLANNED",
                step_id="reference_pattern",
                payload={
                    "reference_pattern_id": plan.get("reference_pattern_id"),
                    "planning_mode": plan.get("planning_mode"),
                },
            ))
        collect(self._event_payload("PLAN_READY", "受控执行计划已生成", f"识别分析意图为 {plan.get('intent_type')}，涉及对象 {selected_node.get('displayName')}。", status="SUCCESS", runtime_state="COMPLETED", step_id="plan_ready", payload={"plan": plan}))
        collect(self._event_payload("AGENT_ANALYSIS", "生成数据解读", "正在基于证据表生成结构化分析结论。", status="RUNNING", runtime_state="JUDGING", step_id="agent_analysis"))
        prompt_payload = self.response_builder.build_analysis_prompt(
            skill_markdown=runtime.skill_markdown,
            skill_files=runtime.skill_files,
            source_name=runtime.source.source_name if runtime.source else payload["source_id"],
            topology=topology,
            plan=plan,
            selected_node=selected_node,
            question=question,
            conversation_context=conversation_context,
            evidence_tables=evidence_tables,
        )
        agent_output = await self.service.llm_service.call_llm(
            prompt_payload["system_prompt"], prompt_payload["user_prompt"], runtime.llm_config, timeout_override=max(runtime.llm_config.timeout, 120)
        )
        runtime.managed_skill.use_count = (runtime.managed_skill.use_count or 0) + 1
        self.db.commit()
        response = self.response_builder.build_turn_response(
            runtime=runtime,
            payload=payload,
            plan=plan,
            selected_node=selected_node,
            topology=topology,
            evidence_tables=evidence_tables,
            executed_queries=executed_queries,
            execution_events=all_execution_events,
            agent_output=agent_output,
            previous_turn_results=runtime.previous_turn_results,
            stored_conversation_history=runtime.stored_conversation_history,
            question=question,
            is_session_start=is_session_start,
        )
        collect(self._event_payload(
            "TASK_ASSESSED",
            "完成度判定",
            response.get("completion_assessment", {}).get("summary") or "已完成任务级判定。",
            status="SUCCESS",
            runtime_state="JUDGING",
            step_id="task_assessed",
            payload={"completion_assessment": response.get("completion_assessment", {})},
        ))
        collect(self._event_payload("TURN_COMPLETED", "本轮执行完成", "已完成受控计划执行和大模型解读。", status="SUCCESS", runtime_state="COMPLETED", step_id="turn_complete"))
        response["execution_events"] = all_execution_events
        if response.get("turn_results"):
            response["turn_results"][-1]["execution_events"] = all_execution_events
        session = self.service._save_managed_skill_test_session(
            session=runtime.existing_session,
            managed_skill=runtime.managed_skill,
            source=runtime.source,
            payload=payload,
            question=question,
            conversation=response["conversation"],
            response=response,
            created_by=created_by,
        )
        response["session_id"] = session.session_id
        return response

    async def stream_execute_turn(self, managed_skill_id: str, payload: Dict[str, Any], created_by: str = "unknown"):
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        all_execution_events: List[Dict[str, Any]] = []

        def emit(event: Dict[str, Any]) -> None:
            all_execution_events.append(event)
            loop.call_soon_threadsafe(queue.put_nowait, ("execution_event", event))

        async def produce() -> None:
            try:
                runtime = self.context_loader.load(managed_skill_id, payload)
                is_session_start = bool(payload.get("start_session"))
                question = (payload.get("test_question") or "").strip() or "请开始测试会话，说明你将如何依据已加载 Skill 对当前数据源进行分析，并等待我的问题。"

                emit(self._event_payload("SKILL_LOAD", "加载上传 Skill", f"已加载 SKILL.md 及 {len(runtime.skill_files) - 1} 个参考文件。", status="SUCCESS", runtime_state="COMPLETED", step_id="skill_load"))

                if not is_session_start and self.service._needs_question_clarification(question):
                    response = self.service._save_managed_skill_clarification(
                        session=runtime.existing_session,
                        managed_skill=runtime.managed_skill,
                        source=runtime.source,
                        payload=payload,
                        question=question,
                        stored_conversation_history=runtime.stored_conversation_history,
                        previous_turn_results=runtime.previous_turn_results,
                        created_by=created_by,
                    )
                    emit(self._event_payload("CLARIFICATION", "请求澄清当前问题", "当前问题未明确查询目标，已返回澄清提示。", status="SUCCESS", runtime_state="COMPLETED", step_id="clarify"))
                    await queue.put(("turn_result", response))
                    return

                topology = self.service.source_service.get_remote_property_graph_topology(
                    source_id=payload["source_id"], schema=payload.get("schema")
                )
                if not topology.get("graph_name") or not topology.get("nodes"):
                    raise ValueError("所选数据源没有可用 Oracle Property Graph，无法按本体属性执行图查询")
                emit(self._event_payload("GRAPH_READY", "加载属性图拓扑", f"已加载 {topology.get('schema')}.{topology.get('graph_name')} 拓扑。", status="SUCCESS", runtime_state="COMPLETED", step_id="graph_ready"))

                conversation_context = self.service._format_conversation_history(runtime.conversation_history)
                emit(self._event_payload("PLAN_BUILD", "生成受控执行计划", "正在根据 Skill、问题和拓扑生成查询计划。", status="RUNNING", runtime_state="PLANNED", step_id="plan_build"))
                execution_result = await self._plan_and_execute(
                    runtime=runtime,
                    payload=payload,
                    topology=topology,
                    question=question,
                    conversation_context=conversation_context,
                    event_callback=emit,
                )
                if execution_result.get("needs_replan"):
                    replan_feedback = execution_result.get("replan_reason") or "上一次计划未命中数据，请改用新的根对象或关系路径。"
                    replan_hints = execution_result.get("replan_hints") or {}
                    emit(self._event_payload("REPLANNING", "自动重规划", replan_feedback, status="RUNNING", runtime_state="REPLANNING", step_id="replan"))
                    execution_result = await self._plan_and_execute(
                        runtime=runtime,
                        payload=payload,
                        topology=topology,
                        question=question,
                        conversation_context=conversation_context,
                        planning_feedback=replan_feedback,
                        excluded_root_labels=replan_hints.get("excluded_root_labels") or ([execution_result["plan"]["steps"][0]["label"]] if execution_result.get("plan", {}).get("steps") else []),
                        excluded_target_labels=replan_hints.get("excluded_target_labels") or [],
                        preferred_root_label=replan_hints.get("preferred_root_label") or "",
                        event_callback=emit,
                    )
                    emit(self._event_payload("REPLANNING", "自动重规划完成", "已根据空结果重新生成并执行计划。", status="SUCCESS", runtime_state="REPLANNING", step_id="replan"))
                plan = execution_result["plan"]
                selected_node = execution_result["selected_node"]
                if plan.get("planning_mode") == "REFERENCE_PATTERN":
                    emit(self._event_payload(
                        "REFERENCE_PATTERN_MATCHED",
                        "命中受控参考模式",
                        f"已命中参考模式 {plan.get('reference_pattern_id') or 'unknown'}，本轮绕过 LLM planner。",
                        status="SUCCESS",
                        runtime_state="PLANNED",
                        step_id="reference_pattern",
                        payload={
                            "reference_pattern_id": plan.get("reference_pattern_id"),
                            "planning_mode": plan.get("planning_mode"),
                        },
                    ))
                emit(self._event_payload("PLAN_READY", "受控执行计划已生成", f"识别分析意图为 {plan.get('intent_type')}，涉及对象 {selected_node.get('displayName')}。", status="SUCCESS", runtime_state="COMPLETED", step_id="plan_ready", payload={"plan": plan}))
                evidence_tables = execution_result["evidence_tables"]
                executed_queries = execution_result["executed_queries"]
                emit(self._event_payload("AGENT_ANALYSIS", "生成数据解读", "正在基于证据表生成结构化分析结论。", status="RUNNING", runtime_state="JUDGING", step_id="agent_analysis"))
                prompt_payload = self.response_builder.build_analysis_prompt(
                    skill_markdown=runtime.skill_markdown,
                    skill_files=runtime.skill_files,
                    source_name=runtime.source.source_name if runtime.source else payload["source_id"],
                    topology=topology,
                    plan=plan,
                    selected_node=selected_node,
                    question=question,
                    conversation_context=conversation_context,
                    evidence_tables=evidence_tables,
                )
                agent_output = await self.service.llm_service.call_llm(
                    prompt_payload["system_prompt"], prompt_payload["user_prompt"], runtime.llm_config, timeout_override=max(runtime.llm_config.timeout, 120)
                )
                runtime.managed_skill.use_count = (runtime.managed_skill.use_count or 0) + 1
                self.db.commit()
                response = self.response_builder.build_turn_response(
                    runtime=runtime,
                    payload=payload,
                    plan=plan,
                    selected_node=selected_node,
                    topology=topology,
                    evidence_tables=evidence_tables,
                    executed_queries=executed_queries,
                    execution_events=all_execution_events,
                    agent_output=agent_output,
                    previous_turn_results=runtime.previous_turn_results,
                    stored_conversation_history=runtime.stored_conversation_history,
                    question=question,
                    is_session_start=is_session_start,
                )
                emit(self._event_payload(
                    "TASK_ASSESSED",
                    "完成度判定",
                    response.get("completion_assessment", {}).get("summary") or "已完成任务级判定。",
                    status="SUCCESS",
                    runtime_state="JUDGING",
                    step_id="task_assessed",
                    payload={"completion_assessment": response.get("completion_assessment", {})},
                ))
                emit(self._event_payload("TURN_COMPLETED", "本轮执行完成", "已完成受控计划执行和大模型解读。", status="SUCCESS", runtime_state="COMPLETED", step_id="turn_complete"))
                response["execution_events"] = all_execution_events
                if response.get("turn_results"):
                    response["turn_results"][-1]["execution_events"] = all_execution_events
                session = self.service._save_managed_skill_test_session(
                    session=runtime.existing_session,
                    managed_skill=runtime.managed_skill,
                    source=runtime.source,
                    payload=payload,
                    question=question,
                    conversation=response["conversation"],
                    response=response,
                    created_by=created_by,
                )
                response["session_id"] = session.session_id
                await queue.put(("turn_result", response))
            except Exception as exc:
                emit(self._event_payload("TURN_FAILED", "本轮执行失败", str(exc), status="ERROR", runtime_state="FAILED", step_id="turn_failed"))
                await queue.put(("error", str(exc)))
            finally:
                await queue.put(("done", None))

        producer = asyncio.create_task(produce())
        yield build_sse_frame({"managed_skill_id": managed_skill_id}, event="start")
        try:
            while True:
                event_type, payload_data = await queue.get()
                if event_type == "execution_event":
                    yield build_sse_frame({"event": payload_data}, event="execution_event")
                elif event_type == "turn_result":
                    yield build_sse_frame(payload_data, event="turn_result")
                elif event_type == "error":
                    yield build_sse_frame({"message": payload_data}, event="error")
                elif event_type == "done":
                    break
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer
        yield build_sse_frame({"managed_skill_id": managed_skill_id}, event="complete")
