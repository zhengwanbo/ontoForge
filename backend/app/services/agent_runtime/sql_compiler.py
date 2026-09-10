from typing import Any, Dict, Optional


class ManagedSkillSQLCompiler:
    def __init__(self, service: Any):
        self.service = service

    def compile_step(
        self,
        *,
        step: Dict[str, Any],
        topology: Dict[str, Any],
        root_step: Dict[str, Any],
        root_node: Dict[str, Any],
        filter_property: str,
        filter_value: str,
        step_lookup: Dict[str, Dict[str, Any]],
        compiled_steps: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        graph_name = str(topology.get("graph_name") or "").upper()
        nodes_by_label = self.service._build_topology_indexes(topology)["nodes_by_label"]
        action = str(step.get("action") or "")
        label = self.service._normalize_label_name(step.get("label"))

        if action == "select_root":
            return {
                "sql": self.service._build_graph_root_query_sql(
                    graph_name,
                    root_node,
                    filter_property,
                    filter_value,
                    step.get("display_properties") or [],
                ),
                "title": f"{label} 本体对象属性",
                "kind": "GRAPH_ROOT",
                "related_objects": [label],
                "execution_mode": "graph",
            }

        if action == "expand_relations":
            target_node = nodes_by_label.get(label)
            path = step.get("path") or []
            if not target_node or not path:
                return None
            return {
                "sql": self.service._build_graph_relation_query_sql(
                    graph_name,
                    root_node,
                    target_node,
                    path,
                    filter_property,
                    filter_value,
                    root_step.get("display_properties") or [],
                    step.get("display_properties") or [],
                ),
                "title": f"{self.service._normalize_label_name(root_step.get('label'))} 关联 {label}",
                "kind": "GRAPH_RELATION",
                "related_objects": [self.service._normalize_label_name(root_step.get("label")), label],
                "execution_mode": "graph",
            }

        if action == "fact_aggregate":
            based_on_step = step_lookup.get(str(step.get("based_on_step") or "")) or root_step
            target_node = nodes_by_label.get(label)
            return {
                "sql": self.service._build_fact_aggregate_sql(
                    metric_step=step,
                    root_node=root_node,
                    graph_name=graph_name,
                    root_step=root_step,
                    based_on_step=based_on_step,
                    target_node=target_node,
                    filter_property=filter_property,
                    filter_value=filter_value,
                ),
                "title": f"{label} 指标聚合：{step.get('metric_name') or step.get('metric_code')}",
                "kind": "FACT_AGGREGATE",
                "related_objects": [self.service._normalize_label_name(root_step.get("label")), label]
                if str(step.get("scope") or "") == "related_object" and label != self.service._normalize_label_name(root_step.get("label"))
                else [label],
                "execution_mode": "readonly",
            }

        if action == "group_by_object":
            based_on_step = step_lookup.get(str(step.get("based_on_step") or "")) or {}
            target_node = nodes_by_label.get(label)
            if not target_node:
                return None
            return {
                "sql": self.service._build_group_by_object_sql(
                    group_step=step,
                    root_node=root_node,
                    graph_name=graph_name,
                    based_on_step=based_on_step,
                    target_node=target_node,
                    filter_property=filter_property,
                    filter_value=filter_value,
                ),
                "title": f"{label} 分组统计：{step.get('metric_name') or step.get('metric_code')}",
                "kind": "OBJECT_GROUP_BY",
                "related_objects": [self.service._normalize_label_name(root_step.get("label")), label],
                "execution_mode": "readonly",
            }

        if action == "group_by_object_time_window":
            based_on_step = step_lookup.get(str(step.get("based_on_step") or "")) or root_step
            target_node = nodes_by_label.get(label)
            return {
                "sql": self.service._build_group_by_object_time_window_sql(
                    step=step,
                    root_node=root_node,
                    graph_name=graph_name,
                    root_step=root_step,
                    based_on_step=based_on_step,
                    target_node=target_node,
                    filter_property=filter_property,
                    filter_value=filter_value,
                ),
                "title": f"{label} 对象时间统计：{step.get('metric_name') or step.get('metric_code')}",
                "kind": "OBJECT_TIME_WINDOW_GROUP_BY",
                "related_objects": [self.service._normalize_label_name(root_step.get("label")), label]
                if str(step.get("scope") or "") == "related_object" and label != self.service._normalize_label_name(root_step.get("label"))
                else [label],
                "execution_mode": "readonly",
            }

        if action == "group_by_time_window":
            based_on_step = step_lookup.get(str(step.get("based_on_step") or "")) or root_step
            target_node = nodes_by_label.get(label)
            compiled_kind = "TIME_WINDOW_GROUP_BY"
            return {
                "sql": self.service._build_group_by_time_window_sql(
                    time_step=step,
                    root_node=root_node,
                    graph_name=graph_name,
                    root_step=root_step,
                    based_on_step=based_on_step,
                    target_node=target_node,
                    filter_property=filter_property,
                    filter_value=filter_value,
                ),
                "title": f"{label} 时间趋势统计：{step.get('metric_name') or step.get('metric_code')}",
                "kind": compiled_kind,
                "related_objects": [self.service._normalize_label_name(root_step.get("label")), label]
                if str(step.get("scope") or "") == "related_object" and label != self.service._normalize_label_name(root_step.get("label"))
                else [label],
                "execution_mode": "readonly",
            }

        if action == "metric_formula":
            numerator_step_id = str(step.get("numerator_step_id") or "")
            denominator_step_id = str(step.get("denominator_step_id") or "")
            numerator = compiled_steps.get(numerator_step_id)
            denominator = compiled_steps.get(denominator_step_id)
            if not numerator or not denominator:
                return None
            step["numerator_step_kind"] = numerator.get("kind") or ""
            step["denominator_step_kind"] = denominator.get("kind") or ""
            return {
                "sql": self.service._build_metric_formula_sql(
                    formula_step=step,
                    numerator_sql=numerator["sql"],
                    denominator_sql=denominator["sql"],
                ),
                "title": f"公式指标计算：{step.get('metric_name') or step.get('metric_code')}",
                "kind": "METRIC_FORMULA",
                "related_objects": list(dict.fromkeys((numerator.get("related_objects") or []) + (denominator.get("related_objects") or []))),
                "execution_mode": "readonly",
            }

        if action == "filter_aggregate_result":
            base = compiled_steps.get(str(step.get("based_on_step") or ""))
            if not base:
                return None
            return {
                "sql": self.service._build_filter_aggregate_result_sql(
                    base_sql=base["sql"],
                    filter_step=step,
                ),
                "title": f"筛选统计结果：{step.get('metric_name') or step.get('metric_code') or step.get('step_id')}",
                "kind": "FILTERED_RESULT",
                "related_objects": list(base.get("related_objects") or []),
                "execution_mode": "readonly",
            }

        if action == "order_and_limit":
            base = compiled_steps.get(str(step.get("based_on_step") or ""))
            if not base:
                return None
            return {
                "sql": self.service._build_order_and_limit_sql(
                    base_sql=base["sql"],
                    order_step=step,
                ),
                "title": f"排序截取结果：{step.get('metric_name') or step.get('metric_code') or step.get('step_id')}",
                "kind": "ORDERED_RESULT",
                "related_objects": list(base.get("related_objects") or []),
                "execution_mode": "readonly",
            }

        return None
