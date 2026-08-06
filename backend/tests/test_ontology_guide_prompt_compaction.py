import unittest

from app.services.llm_service import LLMService
from app.services.ontology_guide_service import OntologyGuideService


class OntologyGuidePromptCompactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.guide_service = OntologyGuideService.__new__(OntologyGuideService)
        self.llm_service = LLMService.__new__(LLMService)

    def test_compact_column_removes_nullable_position_and_blank_comments(self) -> None:
        column = {
            "column_name": " PRODUCT_ID ",
            "data_type": " VARCHAR2(64) ",
            "nullable": "N",
            "column_id": 1,
            "comments": "   ",
        }

        compact = self.guide_service._compact_column_for_llm(column)

        self.assertEqual(
            compact,
            {
                "column_name": "PRODUCT_ID",
                "data_type": "VARCHAR2(64)",
            },
        )

    def test_compact_column_keeps_non_blank_comments(self) -> None:
        compact = self.guide_service._compact_column_for_llm({
            "column_name": "DEFECT_CODE",
            "data_type": "VARCHAR2(30)",
            "nullable": "Y",
            "column_id": 8,
            "comments": " 缺陷编码 ",
        })

        self.assertEqual(
            compact,
            {
                "column_name": "DEFECT_CODE",
                "data_type": "VARCHAR2(30)",
                "comments": "缺陷编码",
            },
        )

    def test_llm_selected_schema_omits_empty_table_and_column_metadata(self) -> None:
        schema = self.guide_service._build_selected_table_schema(
            [{
                "table_name": "PRODUCT_DEFECT",
                "table_comment": "",
                "source_role": "measurement",
                "columns": [
                    {
                        "column_name": "PRODUCT_ID",
                        "data_type": "VARCHAR2(64)",
                        "nullable": "N",
                        "column_id": 1,
                        "comments": None,
                        "is_primary_key": "Y",
                    },
                    {
                        "column_name": "DEFECT_CODE",
                        "data_type": "VARCHAR2(30)",
                        "nullable": "Y",
                        "column_id": 2,
                        "comments": "缺陷编码",
                    },
                ],
            }],
            for_llm=True,
        )

        table = schema["tables"][0]
        self.assertNotIn("table_comment", table)
        self.assertEqual(table["source_role"], "measurement")
        self.assertEqual(table["primary_keys"], ["PRODUCT_ID"])
        self.assertNotIn("nullable", table["columns"][0])
        self.assertNotIn("column_id", table["columns"][0])
        self.assertNotIn("comments", table["columns"][0])
        self.assertEqual(table["columns"][1]["comments"], "缺陷编码")

    def test_llm_prompt_table_does_not_restore_empty_values(self) -> None:
        compact = self.llm_service._compact_guide_prompt_table({
            "table_name": "PRODUCT_DEFECT",
            "source_role": "",
            "table_comment": None,
            "total_columns": 2,
            "selected_column_count": 2,
            "omitted_column_count": 0,
            "segment_index": None,
            "columns": [
                {
                    "column_name": "PRODUCT_ID",
                    "data_type": "VARCHAR2(64)",
                    "comments": "",
                    "nullable": "N",
                    "column_id": 1,
                },
            ],
            "sample_rows": [],
        }, max_sample_rows=2)

        self.assertEqual(compact["omitted_column_count"], 0)
        self.assertNotIn("source_role", compact)
        self.assertNotIn("table_comment", compact)
        self.assertNotIn("segment_index", compact)
        self.assertNotIn("sample_rows", compact)
        self.assertEqual(
            compact["columns"],
            [{
                "column_name": "PRODUCT_ID",
                "data_type": "VARCHAR2(64)",
            }],
        )

    def test_relation_name_removes_source_and_target_entity_labels(self) -> None:
        name = self.guide_service._prefer_chinese_relation_name(
            relation_name="测试会话产生观测指标",
            relation_desc="测试会话产生观测指标，用于保留测量结果。",
            source_entity_name="TestSession",
            target_entity_name="ObservedMetric",
            entity_display_map={"TestSession": "测试会话", "ObservedMetric": "观测指标"},
        )

        self.assertEqual(name, "产生")
        self.assertLessEqual(len(name), 12)


if __name__ == "__main__":
    unittest.main()
