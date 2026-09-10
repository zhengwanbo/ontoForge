import unittest

from app.services.agent_runtime.completion_assessor import ManagedSkillCompletionAssessor
from app.services.agent_service import AgentService


class AgentRuntimeCompletionAssessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AgentService.__new__(AgentService)
        self.assessor = ManagedSkillCompletionAssessor(self.service)

    def test_assess_marks_completed_when_evidence_and_coverage_are_satisfied(self):
        result = self.assessor.assess(
            plan={
                "selected_objects": ["BOTTLECODE", "QUALITYINSPECTION"],
                "steps": [
                    {"step_id": "s1", "action": "select_root", "label": "BOTTLECODE", "display_properties": ["BOTTLE_CODE"]},
                    {
                        "step_id": "s2",
                        "action": "group_by_time_window",
                        "label": "QUALITYINSPECTION",
                        "metric_code": "QUALITY_COUNT",
                        "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    },
                ],
            },
            selected_node={"displayName": "BOTTLECODE"},
            evidence_tables=[
                {
                    "key": "evidence_s1",
                    "related_objects": ["BOTTLECODE", "QUALITYINSPECTION"],
                    "metric_code": "",
                    "row_count": 1,
                    "columns": [{"column_name": "BOTTLE_CODE"}],
                    "path": [],
                },
                {
                    "key": "evidence_s2",
                    "related_objects": ["QUALITYINSPECTION"],
                    "metric_code": "QUALITY_COUNT",
                    "row_count": 3,
                    "columns": [
                        {"column_name": "TIME_BUCKET"},
                        {"column_name": "METRIC_CODE"},
                        {"column_name": "METRIC_VALUE"},
                    ],
                    "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                },
            ],
            executed_queries=[{"purpose": "test", "sql": "SELECT 1 FROM DUAL", "row_count": 1}],
            analysis_result={
                "analysis_flags": ["RISING_TREND"],
                "top_findings": ["近三日持续上升"],
                "trend_summaries": [{"evidence_key": "evidence_s2"}],
                "period_comparisons": [{"evidence_key": "evidence_s2"}],
                "matched_rules": [],
                "applied_metrics": ["QUALITY_COUNT"],
            },
        )

        self.assertEqual("COMPLETED", result["status"])
        self.assertTrue(result["completed"])
        self.assertEqual("PASSED", result["deterministic"]["status"])
        self.assertEqual("PASSED", result["coverage_check"]["status"])
        self.assertEqual("PASSED", result["judge"]["status"])
        self.assertEqual("PASSED", result["coverage_check"]["dimensions"]["fields"])
        self.assertEqual("PASSED", result["coverage_check"]["dimensions"]["relations"])

    def test_assess_marks_failed_when_no_non_empty_evidence_exists(self):
        result = self.assessor.assess(
            plan={
                "selected_objects": ["BOTTLECODE"],
                "steps": [
                    {"step_id": "s1", "action": "select_root", "label": "BOTTLECODE", "display_properties": ["BOTTLE_CODE"]},
                    {"step_id": "s2", "action": "fact_aggregate", "label": "BOTTLECODE", "metric_code": "BOTTLE_COUNT"},
                ],
            },
            selected_node={"displayName": "BOTTLECODE"},
            evidence_tables=[
                {
                    "key": "evidence_s2",
                    "related_objects": ["BOTTLECODE"],
                    "metric_code": "BOTTLE_COUNT",
                    "row_count": 0,
                    "columns": [{"column_name": "METRIC_CODE"}, {"column_name": "METRIC_VALUE"}],
                    "path": [],
                }
            ],
            executed_queries=[{"purpose": "test", "sql": "SELECT 1 FROM DUAL", "row_count": 0}],
            analysis_result={"analysis_flags": [], "top_findings": [], "trend_summaries": [], "period_comparisons": [], "matched_rules": [], "applied_metrics": ["BOTTLE_COUNT"]},
        )

        self.assertEqual("FAILED", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("FAILED", result["deterministic"]["status"])

    def test_assess_marks_partial_when_required_fields_or_components_are_missing(self):
        result = self.assessor.assess(
            plan={
                "selected_objects": ["BOTTLECODE", "QUALITYINSPECTION"],
                "steps": [
                    {"step_id": "s1", "action": "select_root", "label": "BOTTLECODE", "display_properties": ["BOTTLE_CODE"]},
                    {
                        "step_id": "s2",
                        "action": "group_by_object_time_window",
                        "label": "QUALITYINSPECTION",
                        "metric_code": "QUALITY_COUNT",
                        "group_properties": ["FACTORY_NAME"],
                        "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    },
                ],
            },
            selected_node={"displayName": "BOTTLECODE"},
            evidence_tables=[
                {
                    "key": "evidence_s2",
                    "kind": "OBJECT_TIME_WINDOW_GROUP_BY",
                    "related_objects": ["BOTTLECODE", "QUALITYINSPECTION"],
                    "metric_code": "QUALITY_COUNT",
                    "row_count": 2,
                    "columns": [
                        {"column_name": "TIME_BUCKET"},
                        {"column_name": "METRIC_CODE"},
                        {"column_name": "METRIC_VALUE"},
                    ],
                    "path": [],
                }
            ],
            executed_queries=[{"purpose": "test", "sql": "SELECT 1 FROM DUAL", "row_count": 2}],
            analysis_result={
                "analysis_flags": [],
                "top_findings": [],
                "trend_summaries": [],
                "period_comparisons": [],
                "matched_rules": [],
                "applied_metrics": [],
            },
        )

        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("FAILED", result["coverage_check"]["status"])
        self.assertIn("FACTORY_NAME", result["coverage_check"]["missing_fields"])
        self.assertTrue(result["coverage_check"]["missing_relations"])
        self.assertEqual("REVIEW", result["judge"]["status"])
        self.assertIn("applied_metrics", result["judge"]["missing_components"])
        self.assertIn("trend_summaries", result["judge"]["missing_components"])


if __name__ == "__main__":
    unittest.main()
