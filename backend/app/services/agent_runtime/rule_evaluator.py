import json
import re
from typing import Any, Dict, List, Optional


class ManagedSkillRuleEvaluator:
    SUPPORTED_OPERATORS = {
        ">": lambda left, right: left > right,
        ">=": lambda left, right: left >= right,
        "<": lambda left, right: left < right,
        "<=": lambda left, right: left <= right,
        "=": lambda left, right: left == right,
        "==": lambda left, right: left == right,
    }

    def __init__(self, service: Any):
        self.service = service

    def _load_rules(self, skill_files: Dict[str, str]) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(skill_files.get("references/rule-catalog.json") or "{}")
        except (TypeError, ValueError):
            return []
        rules = payload.get("rules") if isinstance(payload, dict) else None
        return rules if isinstance(rules, list) else []

    @staticmethod
    def _to_number(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _extract_rule_operator(self, rule: Dict[str, Any]) -> Optional[str]:
        condition = rule.get("condition_config") if isinstance(rule.get("condition_config"), dict) else {}
        for key in ("operator", "comparison", "compare_op"):
            operator = str(condition.get(key) or "").strip()
            if operator in self.SUPPORTED_OPERATORS:
                return operator
        desc = " ".join(
            str(rule.get(name) or "")
            for name in ("rule_name", "rule_desc", "rule_category")
        ).upper()
        if any(token in desc for token in ("超规", "超过", "大于", "ABOVE", "GREATER")):
            return ">"
        if any(token in desc for token in ("不少于", "至少", "不低于", "AT LEAST")):
            return ">="
        if any(token in desc for token in ("低于", "小于", "LESS THAN", "LOWER")):
            return "<"
        return None

    def _extract_rule_threshold(self, rule: Dict[str, Any]) -> Optional[float]:
        condition = rule.get("condition_config") if isinstance(rule.get("condition_config"), dict) else {}
        for key in ("threshold", "value", "compare_value", "warning_value", "critical_value"):
            number = self._to_number(condition.get(key))
            if number is not None:
                return number
        text = " ".join(str(rule.get(name) or "") for name in ("rule_name", "rule_desc"))
        match = re.search(r"(-?\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
        return None

    def _rule_metric_code(self, rule: Dict[str, Any]) -> str:
        condition = rule.get("condition_config") if isinstance(rule.get("condition_config"), dict) else {}
        for key in ("metric_code", "metricCode"):
            value = str(condition.get(key) or "").strip().upper()
            if value:
                return value
        return ""

    def _normalized_conditions(self, rule: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
        condition = rule.get("condition_config") if isinstance(rule.get("condition_config"), dict) else {}
        logic = str(condition.get("logic") or condition.get("mode") or "all").strip().lower()
        if logic not in {"all", "any"}:
            logic = "all"
        raw_conditions = condition.get("conditions")
        if not isinstance(raw_conditions, list):
            raw_conditions = []
        normalized: List[Dict[str, Any]] = []
        for item in raw_conditions:
            if not isinstance(item, dict):
                continue
            operator = str(item.get("operator") or item.get("comparison") or "").strip()
            if operator not in self.SUPPORTED_OPERATORS:
                continue
            threshold = self._to_number(item.get("threshold"))
            if threshold is None:
                threshold = self._to_number(item.get("value"))
            if threshold is None:
                continue
            field = str(item.get("field") or item.get("column") or "METRIC_VALUE").strip().upper()
            if not field:
                field = "METRIC_VALUE"
            metric_code = str(item.get("metric_code") or item.get("metricCode") or "").strip().upper()
            normalized.append({
                "field": field,
                "operator": operator,
                "threshold": threshold,
                "metric_code": metric_code,
            })
        return logic, normalized

    def _rule_matches_scope(self, rule: Dict[str, Any], based_on_step: Dict[str, Any], evidence: Dict[str, Any]) -> bool:
        label = self.service._normalize_label_name(based_on_step.get("label"))
        related_objects = {self.service._normalize_label_name(item) for item in (evidence.get("related_objects") or [])}
        scope_candidates = {
            self.service._normalize_label_name(rule.get("scope_entity_name")),
            self.service._normalize_label_name(rule.get("scope_entity_display_name")),
        }
        scope_candidates = {item for item in scope_candidates if item}
        if not scope_candidates:
            return True
        return bool(scope_candidates & ({label} | related_objects))

    def _row_matches_condition(
        self,
        *,
        row: Dict[str, Any],
        condition: Dict[str, Any],
        based_metric_code: str,
    ) -> bool:
        condition_metric_code = str(condition.get("metric_code") or "").upper()
        row_metric_code = str(row.get("METRIC_CODE") or based_metric_code or "").upper()
        if condition_metric_code and row_metric_code and condition_metric_code != row_metric_code:
            return False
        comparator = self.SUPPORTED_OPERATORS.get(str(condition.get("operator") or ""))
        if comparator is None:
            return False
        row_value = self._to_number(row.get(condition.get("field") or "METRIC_VALUE"))
        threshold = self._to_number(condition.get("threshold"))
        if row_value is None or threshold is None:
            return False
        return bool(comparator(row_value, threshold))

    def evaluate(
        self,
        *,
        step: Dict[str, Any],
        based_on_step: Dict[str, Any],
        based_on_evidence: Dict[str, Any],
        skill_files: Dict[str, str],
    ) -> Dict[str, Any]:
        rules = self._load_rules(skill_files)
        rows = based_on_evidence.get("sample_rows") or []
        based_metric_code = str(step.get("metric_code") or based_on_evidence.get("metric_code") or "").upper()
        hit_rows = []
        for rule in rules:
            if not self._rule_matches_scope(rule, based_on_step, based_on_evidence):
                continue
            logic, normalized_conditions = self._normalized_conditions(rule)
            if normalized_conditions:
                for row in rows:
                    matched = [
                        self._row_matches_condition(
                            row=row or {},
                            condition=condition,
                            based_metric_code=based_metric_code,
                        )
                        for condition in normalized_conditions
                    ]
                    passed = all(matched) if logic == "all" else any(matched)
                    if not passed:
                        continue
                    passthrough = {
                        str(key).upper(): value
                        for key, value in (row or {}).items()
                        if str(key).upper() not in {"RULE_NAME", "RULE_CATEGORY", "RULE_DESC", "ACTIVITY_NAME", "OPERATOR", "THRESHOLD"}
                    }
                    hit_rows.append({
                        **passthrough,
                        "RULE_NAME": rule.get("rule_name") or "",
                        "RULE_CATEGORY": rule.get("rule_category") or "",
                        "RULE_DESC": rule.get("rule_desc") or "",
                        "METRIC_CODE": str((row or {}).get("METRIC_CODE") or based_metric_code or "").upper(),
                        "METRIC_VALUE": (row or {}).get("METRIC_VALUE"),
                        "OPERATOR": logic.upper(),
                        "THRESHOLD": ", ".join(
                            f"{condition['field']} {condition['operator']} {condition['threshold']}"
                            for condition in normalized_conditions
                        ),
                        "ACTIVITY_NAME": rule.get("activity_name") or "",
                    })
                continue
            operator = self._extract_rule_operator(rule)
            threshold = self._extract_rule_threshold(rule)
            if not operator or threshold is None:
                continue
            rule_metric_code = self._rule_metric_code(rule)
            if rule_metric_code and based_metric_code and rule_metric_code != based_metric_code:
                continue
            comparator = self.SUPPORTED_OPERATORS.get(operator)
            if comparator is None:
                continue
            for row in rows:
                metric_value = self._to_number((row or {}).get("METRIC_VALUE"))
                if metric_value is None or not comparator(metric_value, threshold):
                    continue
                passthrough = {
                    str(key).upper(): value
                    for key, value in (row or {}).items()
                    if str(key).upper() not in {"RULE_NAME", "RULE_CATEGORY", "RULE_DESC", "ACTIVITY_NAME", "OPERATOR", "THRESHOLD"}
                }
                hit_rows.append({
                    **passthrough,
                    "RULE_NAME": rule.get("rule_name") or "",
                    "RULE_CATEGORY": rule.get("rule_category") or "",
                    "RULE_DESC": rule.get("rule_desc") or "",
                    "METRIC_CODE": based_metric_code or str((row or {}).get("METRIC_CODE") or "").upper(),
                    "METRIC_VALUE": metric_value,
                    "OPERATOR": operator,
                    "THRESHOLD": threshold,
                    "ACTIVITY_NAME": rule.get("activity_name") or "",
                })

        passthrough_columns = []
        passthrough_column_names = []
        for row in hit_rows:
            for key in row.keys():
                upper_key = str(key).upper()
                if upper_key in {
                    "RULE_NAME",
                    "RULE_CATEGORY",
                    "RULE_DESC",
                    "METRIC_CODE",
                    "METRIC_VALUE",
                    "OPERATOR",
                    "THRESHOLD",
                    "ACTIVITY_NAME",
                }:
                    continue
                if upper_key not in passthrough_column_names:
                    passthrough_column_names.append(upper_key)
                    passthrough_columns.append({"column_name": upper_key})
        return {
            "title": f"规则判定：{step.get('label') or based_on_step.get('label') or '当前对象'}",
            "kind": "RULE_APPLICATION",
            "related_objects": list(based_on_evidence.get("related_objects") or []),
            "columns": passthrough_columns + [
                {"column_name": "RULE_NAME"},
                {"column_name": "RULE_CATEGORY"},
                {"column_name": "RULE_DESC"},
                {"column_name": "METRIC_CODE"},
                {"column_name": "METRIC_VALUE"},
                {"column_name": "OPERATOR"},
                {"column_name": "THRESHOLD"},
                {"column_name": "ACTIVITY_NAME"},
            ],
            "sample_rows": hit_rows,
            "row_count": len(hit_rows),
        }
