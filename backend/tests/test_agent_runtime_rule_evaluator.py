import unittest

from app.services.agent_runtime.rule_evaluator import ManagedSkillRuleEvaluator
from app.services.agent_service import AgentService


class AgentRuntimeRuleEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AgentService.__new__(AgentService)
        self.evaluator = ManagedSkillRuleEvaluator(self.service)

    def test_evaluate_supports_multi_condition_all_logic(self):
        evidence = self.evaluator.evaluate(
            step={"label": "DISTRIBUTOR", "metric_code": "DISTRIBUTOR_LINK_COUNT"},
            based_on_step={"label": "DISTRIBUTOR"},
            based_on_evidence={
                "metric_code": "DISTRIBUTOR_LINK_COUNT",
                "related_objects": ["OUTBOUNDORDER", "DISTRIBUTOR"],
                "sample_rows": [
                    {"METRIC_CODE": "DISTRIBUTOR_LINK_COUNT", "METRIC_VALUE": 8, "DISTRIBUTOR_NAME": "华东经销商"},
                    {"METRIC_CODE": "DISTRIBUTOR_LINK_COUNT", "METRIC_VALUE": 4, "DISTRIBUTOR_NAME": "华南经销商"},
                ],
            },
            skill_files={
                "references/rule-catalog.json": """
                {
                  "rules": [
                    {
                      "rule_name": "经销商高风险",
                      "rule_category": "ALERT",
                      "rule_desc": "数量过高且不超过十",
                      "activity_name": "人工复核",
                      "condition_config": {
                        "logic": "all",
                        "conditions": [
                          {"field": "METRIC_VALUE", "operator": ">=", "threshold": 5, "metric_code": "DISTRIBUTOR_LINK_COUNT"},
                          {"field": "METRIC_VALUE", "operator": "<=", "threshold": 10}
                        ]
                      }
                    }
                  ]
                }
                """,
            },
        )

        self.assertEqual("RULE_APPLICATION", evidence["kind"])
        self.assertEqual(1, evidence["row_count"])
        self.assertEqual("华东经销商", evidence["sample_rows"][0]["DISTRIBUTOR_NAME"])
        self.assertEqual("ALL", evidence["sample_rows"][0]["OPERATOR"])
        self.assertIn("METRIC_VALUE >= 5.0", evidence["sample_rows"][0]["THRESHOLD"])

    def test_evaluate_supports_multi_condition_any_logic(self):
        evidence = self.evaluator.evaluate(
            step={"label": "QUALITYINSPECTION", "metric_code": "QUALITY_COUNT"},
            based_on_step={"label": "QUALITYINSPECTION"},
            based_on_evidence={
                "metric_code": "QUALITY_COUNT",
                "related_objects": ["QUALITYINSPECTION"],
                "sample_rows": [
                    {"METRIC_CODE": "QUALITY_COUNT", "METRIC_VALUE": 2, "QUALITY_LEVEL": 3},
                    {"METRIC_CODE": "QUALITY_COUNT", "METRIC_VALUE": 8, "QUALITY_LEVEL": 1},
                ],
            },
            skill_files={
                "references/rule-catalog.json": """
                {
                  "rules": [
                    {
                      "rule_name": "质检关注",
                      "rule_desc": "数量高或等级高即关注",
                      "condition_config": {
                        "logic": "any",
                        "conditions": [
                          {"field": "METRIC_VALUE", "operator": ">=", "threshold": 5, "metric_code": "QUALITY_COUNT"},
                          {"field": "QUALITY_LEVEL", "operator": ">=", "threshold": 3}
                        ]
                      }
                    }
                  ]
                }
                """,
            },
        )

        self.assertEqual(2, evidence["row_count"])
        self.assertTrue(any(row["METRIC_VALUE"] == 2 for row in evidence["sample_rows"]))
        self.assertTrue(any(row["METRIC_VALUE"] == 8 for row in evidence["sample_rows"]))


if __name__ == "__main__":
    unittest.main()
