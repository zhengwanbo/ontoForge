import json
import unittest

from app.services.llm_service import LLMService
from app.models.models import SysOntologyEntity


class MappingPromptCompactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = LLMService.__new__(LLMService)

    def test_column_omits_nullable_and_blank_comments(self) -> None:
        compact = self.service._compact_auto_mapping_prompt_column({
            "column_name": " PRODUCT_ID ",
            "data_type": " VARCHAR2(64) ",
            "nullable": "N",
            "default_value": "   ",
            "comments": "   ",
            "column_id": 1,
        })

        self.assertEqual(
            compact,
            {
                "column_name": "PRODUCT_ID",
                "data_type": "VARCHAR2(64)",
            },
        )

    def test_column_keeps_non_blank_comments_and_meaningful_default(self) -> None:
        compact = self.service._compact_auto_mapping_prompt_column({
            "column_name": "RESULT_CODE",
            "data_type": "NUMBER(1)",
            "nullable": "Y",
            "default_value": 0,
            "comments": " 检测结果编码 ",
        })

        self.assertEqual(
            compact,
            {
                "column_name": "RESULT_CODE",
                "data_type": "NUMBER(1)",
                "default_value": 0,
                "comments": "检测结果编码",
            },
        )

    def test_table_prompt_json_does_not_restore_removed_column_fields(self) -> None:
        compact = self.service._compact_auto_mapping_prompt_table({
            "owner": " PROD ",
            "table_name": "PRODUCT_DEFECT",
            "comments": "",
            "sample_rows": [],
            "columns": [
                {
                    "column_name": "PRODUCT_ID",
                    "data_type": "VARCHAR2(64)",
                    "nullable": "N",
                    "comments": None,
                },
                {
                    "column_name": "DEFECT_CODE",
                    "data_type": "VARCHAR2(30)",
                    "nullable": "Y",
                    "comments": " 缺陷编码 ",
                },
            ],
        })
        encoded = json.dumps(compact, ensure_ascii=False)

        self.assertNotIn("nullable", encoded)
        self.assertNotIn('"comments": null', encoded)
        self.assertNotIn('"comments": ""', encoded)
        self.assertNotIn("table_comments", compact)
        self.assertNotIn("sample_rows", compact)
        self.assertNotIn("comments", compact["columns"][0])
        self.assertEqual(compact["columns"][1]["comments"], "缺陷编码")


class MappingPromptIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_auto_mapping_sends_compacted_columns_to_llm(self) -> None:
        service = LLMService.__new__(LLMService)
        captured = {}

        async def fake_call_llm(system_prompt, user_prompt, config):
            captured["user_prompt"] = user_prompt
            return '{"mappings": []}'

        service.call_llm = fake_call_llm
        service._get_config_by_id = lambda _config_id: None
        entity = SysOntologyEntity(
            entity_id="entity_defect",
            entity_name="Defect",
            entity_display_name="缺陷",
            entity_desc="生产缺陷",
        )

        await service.auto_mapping(
            entity=entity,
            properties=[],
            source_tables=[{
                "table_name": "PRODUCT_DEFECT",
                "columns": [{
                    "column_name": "DEFECT_CODE",
                    "data_type": "VARCHAR2(30)",
                    "nullable": "N",
                    "comments": "",
                }],
            }],
        )

        prompt = captured["user_prompt"]
        self.assertNotIn('"nullable"', prompt)
        self.assertNotIn('"comments": ""', prompt)
        self.assertNotIn('"comments": null', prompt)
        self.assertIn('"column_name": "DEFECT_CODE"', prompt)
        self.assertIn('"data_type": "VARCHAR2(30)"', prompt)


if __name__ == "__main__":
    unittest.main()
