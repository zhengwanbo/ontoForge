from typing import Any, Dict, List, Optional, Set, Tuple


class ManagedSkillCompletionAssessor:
    COMPLEX_EVIDENCE_KINDS = {
        "GRAPH_RELATION",
        "OBJECT_GROUP_BY",
        "TIME_WINDOW_GROUP_BY",
        "OBJECT_TIME_WINDOW_GROUP_BY",
        "METRIC_FORMULA",
        "FILTERED_RESULT",
        "ORDERED_RESULT",
        "RULE_APPLICATION",
    }
    METRIC_RESULT_ACTIONS = {
        "fact_aggregate",
        "group_by_object",
        "group_by_time_window",
        "group_by_object_time_window",
        "metric_formula",
        "filter_aggregate_result",
        "order_and_limit",
        "apply_rules",
    }

    def __init__(self, service: Any):
        self.service = service

    @staticmethod
    def _normalize_labels(values: List[Any]) -> List[str]:
        normalized: List[str] = []
        for item in values or []:
            value = str(item or "").strip().upper()
            if value:
                normalized.append(value)
        return normalized

    @staticmethod
    def _normalize_metric_codes(values: List[Any]) -> List[str]:
        normalized: List[str] = []
        for item in values or []:
            value = str(item or "").strip().upper()
            if value:
                normalized.append(value)
        return normalized

    @staticmethod
    def _normalize_field_name(value: Any) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def _normalize_field_list(values: List[Any]) -> List[str]:
        normalized: List[str] = []
        for item in values or []:
            value = str(item or "").strip().upper()
            if value:
                normalized.append(value)
        return list(dict.fromkeys(normalized))

    @staticmethod
    def _relation_signature(path: List[Dict[str, Any]]) -> str:
        tokens = []
        for hop in path or []:
            target = str(hop.get("target") or "").strip().upper()
            edge = str(hop.get("edge") or "").strip().upper()
            direction = str(hop.get("direction") or "").strip().upper() or "OUT"
            if target or edge:
                tokens.append(f"{direction}:{edge}:{target}")
        return " | ".join(tokens)

    def _collect_covered_objects(
        self,
        *,
        selected_node: Dict[str, Any],
        evidence_tables: List[Dict[str, Any]],
    ) -> Set[str]:
        covered: Set[str] = set()
        selected_label = self.service._normalize_label_name(
            selected_node.get("displayName") or selected_node.get("name")
        )
        if selected_label:
            covered.add(selected_label)
        for item in evidence_tables or []:
            for label in item.get("related_objects") or []:
                normalized = self.service._normalize_label_name(label)
                if normalized:
                    covered.add(normalized)
        return covered

    def _collect_expected_fields(
        self,
        *,
        plan: Dict[str, Any],
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        step_lookup = {
            str(step.get("step_id") or ""): step
            for step in (plan.get("steps") or [])
            if str(step.get("step_id") or "")
        }

        def expected_fields_for_step(step: Dict[str, Any]) -> List[str]:
            action = str(step.get("action") or "")
            if action in {"select_root", "expand_relations"}:
                return self._normalize_field_list(step.get("display_properties") or [])
            if action == "fact_aggregate":
                return ["METRIC_CODE", "METRIC_VALUE"]
            if action == "group_by_object":
                return self._normalize_field_list((step.get("group_properties") or []) + ["METRIC_CODE", "METRIC_VALUE"])
            if action == "group_by_time_window":
                return self._normalize_field_list(["TIME_BUCKET", "METRIC_CODE", "METRIC_VALUE"])
            if action == "group_by_object_time_window":
                return self._normalize_field_list((step.get("group_properties") or []) + ["TIME_BUCKET", "METRIC_CODE", "METRIC_VALUE"])
            if action == "metric_formula":
                fields = ["METRIC_CODE", "METRIC_VALUE"]
                if str(step.get("numerator_step_kind") or "").upper() in {"TIME_WINDOW_GROUP_BY", "OBJECT_TIME_WINDOW_GROUP_BY"}:
                    fields.append("TIME_BUCKET")
                fields.extend(step.get("group_properties") or [])
                return self._normalize_field_list(fields)
            if action in {"filter_aggregate_result", "order_and_limit"}:
                base = step_lookup.get(str(step.get("based_on_step") or ""))
                return expected_fields_for_step(base) if base else []
            if action == "apply_rules":
                return self._normalize_field_list([
                    "RULE_NAME",
                    "RULE_CATEGORY",
                    "METRIC_CODE",
                    "METRIC_VALUE",
                    "ACTIVITY_NAME",
                ])
            if action == "summarize_evidence":
                return ["EVIDENCE_KEY", "EVIDENCE_TITLE", "EVIDENCE_KIND", "ROW_COUNT"]
            return []

        by_step: Dict[str, List[str]] = {}
        for step in plan.get("steps") or []:
            step_id = str(step.get("step_id") or "")
            if step_id:
                by_step[step_id] = expected_fields_for_step(step)
        merged = self._normalize_field_list([field for values in by_step.values() for field in values])
        return merged, by_step

    def _collect_expected_relations(self, plan: Dict[str, Any]) -> List[str]:
        relation_signatures = []
        for step in plan.get("steps") or []:
            path = step.get("path") or []
            signature = self._relation_signature(path)
            if signature:
                relation_signatures.append(signature)
        return list(dict.fromkeys(relation_signatures))

    def _collect_covered_fields(self, evidence_tables: List[Dict[str, Any]]) -> List[str]:
        return self._normalize_field_list([
            column.get("column_name")
            for evidence in evidence_tables or []
            for column in (evidence.get("columns") or [])
            if isinstance(column, dict)
        ])

    def _collect_covered_relations(self, evidence_tables: List[Dict[str, Any]]) -> List[str]:
        signatures = []
        for evidence in evidence_tables or []:
            signature = self._relation_signature(evidence.get("path") or [])
            if signature:
                signatures.append(signature)
        return list(dict.fromkeys(signatures))

    @staticmethod
    def _missing(expected: List[str], covered: List[str]) -> List[str]:
        covered_set = set(covered or [])
        return [item for item in expected or [] if item not in covered_set]

    @staticmethod
    def _non_summary_evidence(evidence_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            item for item in (evidence_tables or [])
            if str(item.get("kind") or "") != "EVIDENCE_SUMMARY"
        ]

    @staticmethod
    def _time_series_evidence(evidence_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            item for item in (evidence_tables or [])
            if str(item.get("kind") or "") in {"TIME_WINDOW_GROUP_BY", "OBJECT_TIME_WINDOW_GROUP_BY", "METRIC_FORMULA"}
            and any(str(column.get("column_name") or "").upper() == "TIME_BUCKET" for column in (item.get("columns") or []))
        ]

    @staticmethod
    def _grouped_evidence(evidence_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped = []
        for item in evidence_tables or []:
            kind = str(item.get("kind") or "")
            if kind in {"OBJECT_GROUP_BY", "OBJECT_TIME_WINDOW_GROUP_BY", "FILTERED_RESULT", "ORDERED_RESULT"}:
                grouped.append(item)
        return grouped

    def _build_coverage_check(
        self,
        *,
        plan: Dict[str, Any],
        selected_node: Dict[str, Any],
        evidence_tables: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        expected_objects = self._normalize_labels(plan.get("selected_objects") or [])
        expected_metrics = self._normalize_metric_codes(
            [step.get("metric_code") for step in (plan.get("steps") or [])]
        )
        expected_fields, expected_fields_by_step = self._collect_expected_fields(plan=plan)
        expected_relations = self._collect_expected_relations(plan)

        covered_objects = sorted(self._collect_covered_objects(selected_node=selected_node, evidence_tables=evidence_tables))
        covered_metrics = sorted(
            {
                metric_code
                for metric_code in self._normalize_metric_codes(
                    [item.get("metric_code") for item in evidence_tables]
                )
            }
        )
        covered_fields = self._collect_covered_fields(evidence_tables)
        covered_relations = self._collect_covered_relations(evidence_tables)

        missing_objects = self._missing(expected_objects, covered_objects)
        missing_metrics = self._missing(expected_metrics, covered_metrics)
        missing_fields = self._missing(expected_fields, covered_fields)
        missing_relations = self._missing(expected_relations, covered_relations)

        requirements = {
            "objects_required": bool(expected_objects),
            "metrics_required": bool(expected_metrics),
            "fields_required": bool(expected_fields),
            "relations_required": bool(expected_relations),
        }

        dimension_results = {
            "objects": "PASSED" if not expected_objects or not missing_objects else ("PARTIAL" if covered_objects else "FAILED"),
            "metrics": "PASSED" if not expected_metrics or not missing_metrics else ("PARTIAL" if covered_metrics else "FAILED"),
            "fields": "PASSED" if not expected_fields or not missing_fields else ("PARTIAL" if covered_fields else "FAILED"),
            "relations": "PASSED" if not expected_relations or not missing_relations else ("PARTIAL" if covered_relations else "FAILED"),
        }
        ordered_dimension_states = [dimension_results["objects"], dimension_results["metrics"], dimension_results["fields"], dimension_results["relations"]]
        if all(state == "PASSED" for state in ordered_dimension_states):
            coverage_status = "PASSED"
            coverage_reason = "计划声明的对象、指标、字段和关系均已被证据覆盖。"
        elif any(state == "FAILED" for state in ordered_dimension_states):
            coverage_status = "FAILED"
            coverage_reason = "存在关键对象、指标、字段或关系完全未被证据覆盖。"
        else:
            coverage_status = "PARTIAL"
            coverage_reason = "已覆盖部分计划对象、指标、字段或关系，但仍存在缺口。"
        return {
            "status": coverage_status,
            "reason": coverage_reason,
            "requirements": requirements,
            "dimensions": dimension_results,
            "expected_objects": expected_objects,
            "covered_objects": covered_objects,
            "missing_objects": missing_objects,
            "expected_metrics": expected_metrics,
            "covered_metrics": covered_metrics,
            "missing_metrics": missing_metrics,
            "expected_fields": expected_fields,
            "expected_fields_by_step": expected_fields_by_step,
            "covered_fields": covered_fields,
            "missing_fields": missing_fields,
            "expected_relations": expected_relations,
            "covered_relations": covered_relations,
            "missing_relations": missing_relations,
        }

    @staticmethod
    def _count_non_empty(rows: List[Dict[str, Any]]) -> int:
        return len([item for item in rows or [] if int(item.get("row_count") or 0) > 0])

    def _build_judge(
        self,
        *,
        plan: Dict[str, Any],
        evidence_tables: List[Dict[str, Any]],
        analysis_result: Dict[str, Any],
        coverage_check: Dict[str, Any],
    ) -> Dict[str, Any]:
        non_summary_evidence = self._non_summary_evidence(evidence_tables)
        time_series_evidence = self._time_series_evidence(non_summary_evidence)
        grouped_evidence = self._grouped_evidence(non_summary_evidence)
        rule_evidence = [item for item in non_summary_evidence if str(item.get("kind") or "") == "RULE_APPLICATION" and int(item.get("row_count") or 0) > 0]
        complex_step_present = any(
            str(step.get("action") or "") in self.METRIC_RESULT_ACTIONS
            for step in (plan.get("steps") or [])
        )
        requires_judge = (
            len(non_summary_evidence) > 1
            or len(self._normalize_labels(plan.get("selected_objects") or [])) > 1
            or complex_step_present
        )

        trend_summaries = analysis_result.get("trend_summaries") or []
        period_comparisons = analysis_result.get("period_comparisons") or []
        top_findings = analysis_result.get("top_findings") or []
        matched_rules = analysis_result.get("matched_rules") or []
        applied_metrics = self._normalize_metric_codes(analysis_result.get("applied_metrics") or [])

        required_components: List[str] = []
        missing_components: List[str] = []

        required_metric_codes = self._normalize_metric_codes([item.get("metric_code") for item in non_summary_evidence])
        if required_metric_codes:
            required_components.append("applied_metrics")
            if any(code not in set(applied_metrics) for code in required_metric_codes):
                missing_components.append("applied_metrics")

        if time_series_evidence:
            required_components.append("trend_summaries")
            trend_keys = {str(item.get("evidence_key") or "") for item in trend_summaries if str(item.get("evidence_key") or "")}
            expected_trend_keys = {
                str(item.get("key") or "")
                for item in time_series_evidence
                if int(item.get("row_count") or 0) >= 2
            }
            if expected_trend_keys and not expected_trend_keys.issubset(trend_keys):
                missing_components.append("trend_summaries")

            required_components.append("period_comparisons")
            comparison_keys = {str(item.get("evidence_key") or "") for item in period_comparisons if str(item.get("evidence_key") or "")}
            if expected_trend_keys and not expected_trend_keys.issubset(comparison_keys):
                missing_components.append("period_comparisons")

        if grouped_evidence or time_series_evidence:
            required_components.append("top_findings")
            if not top_findings:
                missing_components.append("top_findings")

        if rule_evidence:
            required_components.append("matched_rules")
            if not matched_rules:
                missing_components.append("matched_rules")

        required_components = list(dict.fromkeys(required_components))
        missing_components = list(dict.fromkeys(missing_components))

        if not requires_judge:
            judge_status = "SKIPPED"
            judge_reason = "当前问题较简单，未触发额外判定。"
        elif missing_components:
            judge_status = "REVIEW"
            judge_reason = "复杂问数结果已生成，但仍缺少部分结构化判定组件。"
        elif coverage_check.get("dimensions", {}).get("fields") == "FAILED" or coverage_check.get("dimensions", {}).get("relations") == "FAILED":
            judge_status = "REVIEW"
            judge_reason = "字段或关系覆盖不足，建议人工复核。"
        else:
            judge_status = "PASSED"
            judge_reason = "复杂问数所需的结构化判定组件已齐备。"
        return {
            "required": requires_judge,
            "status": judge_status,
            "reason": judge_reason,
            "required_components": required_components,
            "missing_components": missing_components,
            "complex_evidence_count": len([item for item in non_summary_evidence if str(item.get("kind") or "") in self.COMPLEX_EVIDENCE_KINDS]),
            "time_series_evidence_count": len(time_series_evidence),
            "grouped_evidence_count": len(grouped_evidence),
            "rule_evidence_count": len(rule_evidence),
        }

    def assess(
        self,
        *,
        plan: Dict[str, Any],
        selected_node: Dict[str, Any],
        evidence_tables: List[Dict[str, Any]],
        executed_queries: List[Dict[str, Any]],
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        evidence_with_rows = [item for item in evidence_tables if int(item.get("row_count") or 0) > 0]
        deterministic_passed = bool(evidence_with_rows)
        deterministic = {
            "status": "PASSED" if deterministic_passed else "FAILED",
            "has_evidence_rows": deterministic_passed,
            "evidence_table_count": len(evidence_tables or []),
            "non_empty_evidence_count": len(evidence_with_rows),
            "executed_query_count": len(executed_queries or []),
            "reason": "已拿到至少一张非空证据表。"
            if deterministic_passed
            else "当前计划虽已执行，但尚未获得非空证据表。",
        }

        coverage_check = self._build_coverage_check(
            plan=plan,
            selected_node=selected_node,
            evidence_tables=evidence_tables,
        )
        judge = self._build_judge(
            plan=plan,
            evidence_tables=evidence_tables,
            analysis_result=analysis_result,
            coverage_check=coverage_check,
        )

        if deterministic["status"] == "FAILED":
            overall_status = "FAILED"
        elif coverage_check["status"] == "PASSED" and judge["status"] in {"PASSED", "SKIPPED"}:
            overall_status = "COMPLETED"
        else:
            overall_status = "PARTIAL"
        return {
            "status": overall_status,
            "completed": overall_status == "COMPLETED",
            "deterministic": deterministic,
            "coverage_check": coverage_check,
            "judge": judge,
            "summary": (
                "当前计划已完成并满足对象、指标、字段和关系覆盖要求。"
                if overall_status == "COMPLETED"
                else "当前计划已执行，但覆盖校验或结构化判定仍存在缺口。"
                if overall_status == "PARTIAL"
                else "当前计划未取得足够证据，无法判定为完成。"
            ),
        }
