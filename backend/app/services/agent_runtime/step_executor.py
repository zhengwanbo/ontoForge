from typing import Any, Callable, Dict, List, Optional

from .rule_evaluator import ManagedSkillRuleEvaluator
from .sql_compiler import ManagedSkillSQLCompiler


class ManagedSkillStepExecutor:
    def __init__(self, service: Any):
        self.service = service
        self.compiler = ManagedSkillSQLCompiler(service)
        self.rule_evaluator = ManagedSkillRuleEvaluator(service)

    @staticmethod
    def _build_step_evidence(step: Dict[str, Any], compiled: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "key": f"evidence_{step.get('step_id')}",
            "step_id": step.get("step_id"),
            "metric_code": step.get("metric_code") or "",
            "title": compiled["title"],
            "kind": compiled["kind"],
            "related_objects": compiled.get("related_objects") or [],
            "path": step.get("path") or [],
            "sql": compiled.get("sql") or "",
            "row_count": len(result.get("rows", [])),
            "columns": [{"column_name": column} for column in (result.get("columns") or [])],
            "sample_rows": result.get("rows", []),
        }

    @staticmethod
    def _build_summary_evidence(step: Dict[str, Any], evidence_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        rows = [
            {
                "EVIDENCE_KEY": item.get("key"),
                "EVIDENCE_TITLE": item.get("title"),
                "EVIDENCE_KIND": item.get("kind"),
                "ROW_COUNT": item.get("row_count"),
            }
            for item in evidence_tables
        ]
        return {
            "key": f"evidence_{step.get('step_id')}",
            "step_id": step.get("step_id"),
            "title": "证据摘要",
            "kind": "EVIDENCE_SUMMARY",
            "related_objects": [],
            "sql": "",
            "row_count": len(rows),
            "columns": [
                {"column_name": "EVIDENCE_KEY"},
                {"column_name": "EVIDENCE_TITLE"},
                {"column_name": "EVIDENCE_KIND"},
                {"column_name": "ROW_COUNT"},
            ],
            "sample_rows": rows,
        }

    def _emit(
        self,
        event: Dict[str, Any],
        events: List[Dict[str, Any]],
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        events.append(event)
        if callback:
            callback(event)

    @staticmethod
    def _state_event(
        *,
        event_type: str,
        step_id: str,
        title: str,
        runtime_state: str,
        detail: str,
        status: str = "RUNNING",
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

    def execute_graph_evidence(
        self,
        *,
        plan: Dict[str, Any],
        topology: Dict[str, Any],
        source_id: str,
        schema: Optional[str],
        sample_limit: int,
        skill_files: Optional[Dict[str, str]] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        steps = plan.get("steps") or []
        if not steps:
            raise ValueError("当前执行计划没有可执行步骤")

        nodes_by_label = self.service._build_topology_indexes(topology)["nodes_by_label"]
        root_step = steps[0]
        root_label = self.service._normalize_label_name(root_step.get("label"))
        root_node = nodes_by_label.get(root_label)
        if not root_node:
            raise ValueError("起点对象不存在于当前属性图")

        step_lookup = {
            str(item.get("step_id") or ""): item
            for item in steps
            if str(item.get("step_id") or "")
        }
        filter_config = root_step.get("filter") or {}
        filter_property = self.service._normalize_label_name(filter_config.get("property"))
        filter_value = str(filter_config.get("value") or "").strip()

        evidence_tables: List[Dict[str, Any]] = []
        executed_queries: List[Dict[str, Any]] = []
        execution_events: List[Dict[str, Any]] = []
        compiled_steps: Dict[str, Dict[str, Any]] = {}
        evidence_by_step: Dict[str, Dict[str, Any]] = {}

        for step_index, step in enumerate(steps, start=1):
            action = str(step.get("action") or "")
            step_id = str(step.get("step_id") or "")
            title = self.service._plan_step_title(step)
            detail = self.service._plan_step_detail(step)
            try:
                if action in {
                    "select_root",
                    "expand_relations",
                    "fact_aggregate",
                    "group_by_object",
                    "group_by_object_time_window",
                    "group_by_time_window",
                    "metric_formula",
                    "filter_aggregate_result",
                    "order_and_limit",
                }:
                    compiled = self.compiler.compile_step(
                        step=step,
                        topology=topology,
                        root_step=root_step,
                        root_node=root_node,
                        filter_property=filter_property,
                        filter_value=filter_value,
                        step_lookup=step_lookup,
                        compiled_steps=compiled_steps,
                    )
                    if not compiled:
                        continue
                    compiled_steps[step_id] = compiled
                    self._emit(
                        self._state_event(
                            event_type="STEP_SQL_COMPILED",
                            step_id=step_id,
                            title=title,
                            runtime_state="PLANNED",
                            detail=detail,
                            status="READY",
                            payload={"sql": compiled["sql"]},
                        ),
                        execution_events,
                        event_callback,
                    )

                    self._emit(
                        self._state_event(
                            event_type="STEP_VALIDATING",
                            step_id=step_id,
                            title=title,
                            runtime_state="VALIDATING",
                            detail="正在执行 Oracle 解析校验。",
                        ),
                        execution_events,
                        event_callback,
                    )
                    if compiled["execution_mode"] == "graph":
                        self.service.source_service.validate_remote_graph_query(
                            source_id=source_id,
                            graph_sql=compiled["sql"],
                            schema=schema,
                        )
                        validated_detail = "Graph SQL 已通过 Oracle 解析校验。"
                    else:
                        self.service.source_service.validate_remote_readonly_sql(
                            source_id=source_id,
                            query_sql=compiled["sql"],
                            schema=schema,
                        )
                        validated_detail = "只读 SQL 已通过 Oracle 解析校验。"
                    self._emit(
                        self._state_event(
                            event_type="STEP_VALIDATED",
                            step_id=step_id,
                            title=title,
                            runtime_state="VALIDATING",
                            detail=validated_detail,
                            status="SUCCESS",
                        ),
                        execution_events,
                        event_callback,
                    )

                    self._emit(
                        self._state_event(
                            event_type="STEP_PROBING",
                            step_id=step_id,
                            title=title,
                            runtime_state="PROBING",
                            detail="正在执行探测查询，确认当前步骤是否命中数据。",
                        ),
                        execution_events,
                        event_callback,
                    )
                    if compiled["execution_mode"] == "graph":
                        has_rows = self.service.source_service.probe_remote_graph_query(
                            source_id=source_id,
                            graph_sql=compiled["sql"],
                            schema=schema,
                        )
                    else:
                        has_rows = self.service.source_service.probe_remote_readonly_sql(
                            source_id=source_id,
                            query_sql=compiled["sql"],
                            schema=schema,
                        )

                    probe_status = "SUCCESS" if has_rows else "WARNING"
                    probe_detail = "查询探测已命中结果。" if has_rows else "查询探测未命中结果。"
                    self._emit(
                        self._state_event(
                            event_type="STEP_PROBED",
                            step_id=step_id,
                            title=title,
                            runtime_state="PROBING",
                            detail=probe_detail,
                            status=probe_status,
                            payload={"has_rows": has_rows},
                        ),
                        execution_events,
                        event_callback,
                    )

                    if step_index == 1 and not has_rows:
                        self._emit(
                            self._state_event(
                                event_type="REPLANNING_REQUIRED",
                                step_id=step_id,
                                title=title,
                                runtime_state="REPLANNING",
                                detail="起点对象探测为空，建议更换根对象并自动重规划一次。",
                                status="WARNING",
                                payload={"root_label": root_label},
                            ),
                            execution_events,
                            event_callback,
                        )
                        return {
                            "evidence_tables": evidence_tables,
                            "executed_queries": executed_queries,
                            "execution_events": execution_events,
                            "needs_replan": True,
                            "replan_reason": f"起点对象 {root_label} 在当前过滤条件下未命中数据，请更换更合适的起点对象或关系路径。",
                            "replan_hints": {
                                "excluded_root_labels": [root_label],
                                "excluded_target_labels": [],
                                "preferred_root_label": "",
                            },
                        }
                    if action == "expand_relations" and not has_rows:
                        self._emit(
                            self._state_event(
                                event_type="REPLANNING_REQUIRED",
                                step_id=step_id,
                                title=title,
                                runtime_state="REPLANNING",
                                detail="当前关系路径未命中结果，建议保留起点对象并改查其他关联对象。",
                                status="WARNING",
                                payload={"root_label": root_label, "target_label": self.service._normalize_label_name(step.get("label"))},
                            ),
                            execution_events,
                            event_callback,
                        )
                        return {
                            "evidence_tables": evidence_tables,
                            "executed_queries": executed_queries,
                            "execution_events": execution_events,
                            "needs_replan": True,
                            "replan_reason": f"起点对象 {root_label} 已命中数据，但关联对象 {self.service._normalize_label_name(step.get('label'))} 的关系路径未命中结果，请保留当前起点对象并尝试其他关联对象。",
                            "replan_hints": {
                                "excluded_root_labels": [],
                                "excluded_target_labels": [self.service._normalize_label_name(step.get('label'))],
                                "preferred_root_label": root_label,
                            },
                        }

                    self._emit(
                        self._state_event(
                            event_type="STEP_EXECUTING",
                            step_id=step_id,
                            title=title,
                            runtime_state="EXECUTING",
                            detail="正在执行正式只读查询并生成证据表。",
                        ),
                        execution_events,
                        event_callback,
                    )
                    if compiled["execution_mode"] == "graph":
                        result = self.service.source_service.execute_remote_graph_query(
                            source_id=source_id,
                            graph_sql=compiled["sql"],
                            schema=schema,
                            row_limit=sample_limit,
                        )
                    else:
                        result = self.service.source_service.execute_remote_readonly_sql(
                            source_id=source_id,
                            query_sql=compiled["sql"],
                            schema=schema,
                            row_limit=sample_limit,
                        )

                    evidence = self._build_step_evidence(step, compiled, result)
                    evidence_tables.append(evidence)
                    evidence_by_step[step_id] = evidence
                    executed_queries.append({
                        "purpose": compiled["title"],
                        "sql": compiled["sql"],
                        "row_count": evidence["row_count"],
                    })
                    self._emit(
                        self._state_event(
                            event_type="STEP_EXECUTED",
                            step_id=step_id,
                            title=title,
                            runtime_state="COMPLETED",
                            detail=f"{compiled['title']} 查询完成，返回 {evidence['row_count']} 条记录。",
                            status="SUCCESS",
                            payload={"row_count": evidence["row_count"], "evidence_key": evidence["key"]},
                        ),
                        execution_events,
                        event_callback,
                    )
                    continue

                if action == "apply_rules":
                    based_on_step = step_lookup.get(str(step.get("based_on_step") or ""))
                    based_on_evidence = evidence_by_step.get(str(step.get("based_on_step") or ""))
                    if not based_on_step or not based_on_evidence:
                        continue
                    self._emit(
                        self._state_event(
                            event_type="STEP_JUDGING",
                            step_id=step_id,
                            title=title,
                            runtime_state="JUDGING",
                            detail="正在对聚合结果应用规则判定。",
                        ),
                        execution_events,
                        event_callback,
                    )
                    evidence = self.rule_evaluator.evaluate(
                        step=step,
                        based_on_step=based_on_step,
                        based_on_evidence=based_on_evidence,
                        skill_files=skill_files or {},
                    )
                    evidence["key"] = f"evidence_{step_id}"
                    evidence["step_id"] = step_id
                    evidence_tables.append(evidence)
                    evidence_by_step[step_id] = evidence
                    self._emit(
                        self._state_event(
                            event_type="STEP_EXECUTED",
                            step_id=step_id,
                            title=title,
                            runtime_state="COMPLETED",
                            detail=f"规则判定完成，命中 {evidence['row_count']} 条规则结果。",
                            status="SUCCESS",
                            payload={"row_count": evidence["row_count"], "evidence_key": evidence["key"]},
                        ),
                        execution_events,
                        event_callback,
                    )
                    continue

                if action == "summarize_evidence":
                    self._emit(
                        self._state_event(
                            event_type="STEP_JUDGING",
                            step_id=step_id,
                            title=title,
                            runtime_state="JUDGING",
                            detail="正在汇总当前轮所有证据表。",
                        ),
                        execution_events,
                        event_callback,
                    )
                    evidence = self._build_summary_evidence(step, evidence_tables)
                    evidence_tables.append(evidence)
                    evidence_by_step[step_id] = evidence
                    self._emit(
                        self._state_event(
                            event_type="STEP_EXECUTED",
                            step_id=step_id,
                            title=title,
                            runtime_state="COMPLETED",
                            detail=f"证据摘要已生成，共汇总 {max(0, evidence['row_count'])} 个证据项。",
                            status="SUCCESS",
                            payload={"row_count": evidence["row_count"], "evidence_key": evidence["key"]},
                        ),
                        execution_events,
                        event_callback,
                    )
            except Exception as exc:
                self._emit(
                    self._state_event(
                        event_type="STEP_FAILED",
                        step_id=step_id,
                        title=title,
                        runtime_state="FAILED",
                        detail=str(exc),
                        status="ERROR",
                    ),
                    execution_events,
                    event_callback,
                )
                raise

        return {
            "evidence_tables": evidence_tables,
            "executed_queries": executed_queries,
            "execution_events": execution_events,
            "needs_replan": False,
            "replan_reason": "",
            "replan_hints": {},
        }
