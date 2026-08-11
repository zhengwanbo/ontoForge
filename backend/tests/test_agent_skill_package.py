import unittest
from io import BytesIO
from zipfile import ZipFile

from app.services.agent_service import AgentService


class AgentSkillPackageTests(unittest.TestCase):
    def setUp(self):
        self.service = AgentService.__new__(AgentService)

    def test_normalize_package_files_keeps_only_safe_skill_paths(self):
        files = self.service._normalize_skill_package_files({
            "files": [
                {"path": "SKILL.md", "content": "# skill"},
                {"path": "references/graph.md", "content": "# graph"},
                {"path": "../outside.md", "content": "unsafe"},
                {"path": "/absolute.md", "content": "unsafe"},
                {"path": "scripts/run.py", "content": "unsafe"},
            ]
        })

        self.assertEqual({"SKILL.md", "references/graph.md"}, set(files))

    def test_graph_reference_uses_live_topology_labels_and_graph_result_contract(self):
        reference = self.service._build_graph_reference({
            "schema": "GYL",
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [{
                "name": "BOTTLECODE",
                "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                "properties": [{"property_name": "BOTTLE_ID", "data_type": "NUMBER", "is_primary_key": "Y"}],
            }],
            "edges": [{
                "name": "GRAPH_LABEL",
                "source": "BOTTLECODE",
                "target": "PACKCODE",
                "tableName": "GYL.ONTO_EDGE_BOTTLECODE_REL_BOTTLECODE_PACKCODE_PACKCODE",
            }],
        })

        self.assertIn("PG_JDXQ_SUPPLY_TRACE", reference)
        self.assertIn("BOTTLECODE", reference)
        self.assertIn("SOURCE_ID", reference)
        self.assertIn("TARGET_ID", reference)

    def test_conversation_history_keeps_full_display_history_and_bounds_model_context(self):
        history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"消息 {index}"}
            for index in range(16)
        ]

        self.assertEqual(16, len(self.service._normalize_conversation_history(history, limit=None)))
        self.assertEqual(5, len(self.service._normalize_conversation_history(history)))

    def test_ambiguous_followup_requests_clarification_without_reusing_history_query(self):
        self.assertTrue(self.service._needs_question_clarification("继续查询"))
        self.assertTrue(self.service._needs_question_clarification("这个怎么样"))
        self.assertFalse(self.service._needs_question_clarification("查询该瓶码的质检记录"))
        self.assertFalse(self.service._needs_question_clarification("查询 BATCH-202608-005 的质检记录"))

    def test_upload_managed_skill_extracts_frontmatter_metadata(self):
        class DummyDb:
            def add(self, item):
                self.item = item

            def commit(self):
                pass

            def refresh(self, item):
                pass

        archive = BytesIO()
        with ZipFile(archive, "w") as package:
            package.writestr("SKILL.md", "---\nname: supply-trace\ndescription: 五码供应链追溯技能\n---\n\n# 供应链追溯")
            package.writestr("references/query.md", "# 查询")
        self.service.db = DummyDb()

        uploaded = self.service.upload_managed_skill("supply-trace.zip", archive.getvalue(), "tester")

        self.assertEqual("supply-trace", uploaded["skill_name"])
        self.assertEqual("五码供应链追溯技能", uploaded["skill_desc"])
        self.assertEqual(2, uploaded["file_count"])

    def test_outbound_distributor_question_uses_graph_relation_and_quantity_fact(self):
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [
                        {"property_name": name}
                        for name in ("OUTBOUND_ID", "OUTBOUND_NO", "OUTBOUND_TIME", "OUTBOUND_TYPE", "STATUS")
                    ],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [
                        {"property_name": name}
                        for name in ("DISTRIBUTOR_ID", "DISTRIBUTOR_CODE", "DISTRIBUTOR_NAME")
                    ],
                },
            ],
        }

        plan = self.service._build_supply_chain_graph_plan("本月出库单对应哪些经销商，出库数量分别是多少？", topology)

        self.assertIsNotNone(plan)
        self.assertIn("MATCH (o IS OUTBOUNDORDER)-[e IS GRAPH_LABEL]->(d IS DISTRIBUTOR)", plan["sql"])
        self.assertIn("d.DISTRIBUTOR_NAME AS DISTRIBUTOR_NAME", plan["sql"])
        self.assertIn("SUM(NVL(obd.QUANTITY, 0)) AS OUTBOUND_QUANTITY", plan["sql"])
        self.assertIn("OUTBOUND_DETAIL", plan["sql"])

    def test_bottle_code_chain_question_uses_four_hop_graph_path_and_exact_filter(self):
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {"displayName": label, "properties": [{"property_name": key}, {"property_name": code}]}
                for label, key, code in (
                    ("BOTTLECODE", "BOTTLE_ID", "BOTTLE_CODE"),
                    ("PACKCODE", "PACK_ID", "PACK_CODE"),
                    ("CASECODE", "CASE_ID", "CASE_CODE"),
                    ("PALLETCODE", "PALLET_ID", "PALLET_CODE"),
                    ("STACKCODE", "STACK_ID", "STACK_CODE"),
                )
            ],
        }

        plan = self.service._build_supply_chain_graph_plan(
            "请继续查询瓶码 BOT-202608-000277 对应的包码、箱码、托码、垛码链路。", topology
        )

        self.assertIsNotNone(plan)
        self.assertIn("MATCH (b IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(p IS PACKCODE)", plan["sql"])
        self.assertIn("-[e4 IS GRAPH_LABEL]->(s IS STACKCODE)", plan["sql"])
        self.assertIn("WHERE BOTTLE_CODE = 'BOT-202608-000277'", plan["sql"])
        self.assertIn("PACK_CODE, CASE_CODE, PALLET_CODE, STACK_CODE", plan["sql"])

    def test_bottle_production_question_uses_product_batch_line_factory_graph_paths(self):
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {"displayName": label, "properties": [{"property_name": name} for name in properties]}
                for label, properties in (
                    ("BOTTLECODE", ("BOTTLE_ID", "BOTTLE_CODE")),
                    ("PRODUCT", ("PRODUCT_ID", "SKU_CODE", "PRODUCT_NAME")),
                    ("PRODUCTIONBATCH", ("BATCH_ID", "BATCH_NO", "PRODUCTION_DATE", "QUALITY_STATUS")),
                    ("PRODUCTIONLINE", ("LINE_ID", "LINE_CODE", "LINE_NAME", "WORKSHOP")),
                    ("FACTORY", ("FACTORY_ID", "FACTORY_CODE", "FACTORY_NAME", "PROVINCE", "CITY")),
                )
            ],
        }

        plan = self.service._build_supply_chain_graph_plan(
            "请继续执行该瓶码 BOT-202608-000277 的生产信息查询，返回产品、批次、产线、工厂明细。", topology
        )

        self.assertIsNotNone(plan)
        self.assertIn("MATCH (b IS BOTTLECODE)-[e IS GRAPH_LABEL]->(p IS PRODUCT)", plan["sql"])
        self.assertIn("MATCH (b IS BOTTLECODE)-[e IS GRAPH_LABEL]->(pb IS PRODUCTIONBATCH)", plan["sql"])
        self.assertIn("MATCH (b IS BOTTLECODE)-[e IS GRAPH_LABEL]->(l IS PRODUCTIONLINE)", plan["sql"])
        self.assertIn("MATCH (pb IS PRODUCTIONBATCH)-[e IS GRAPH_LABEL]->(f IS FACTORY)", plan["sql"])
        self.assertIn("WHERE bp.BOTTLE_CODE = 'BOT-202608-000277'", plan["sql"])

    def test_topology_plan_compiler_uses_validated_path_and_exact_filter(self):
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {
                    "id": "g:VERTEX:BOTTLE",
                    "displayName": "BOTTLECODE",
                    "properties": [
                        {"property_name": "BOTTLE_ID", "is_primary_key": "Y"},
                        {"property_name": "BOTTLE_CODE"},
                        {"property_name": "CODE_TYPE"},
                        {"property_name": "PRODUCT_ID"},
                        {"property_name": "BATCH_ID"},
                        {"property_name": "LINE_ID"},
                    ],
                },
                {
                    "id": "g:VERTEX:BATCH",
                    "displayName": "PRODUCTIONBATCH",
                    "properties": [
                        {"property_name": "BATCH_ID", "is_primary_key": "Y"},
                        {"property_name": "BATCH_NO"},
                    ],
                },
                {
                    "id": "g:VERTEX:FACTORY",
                    "displayName": "FACTORY",
                    "properties": [
                        {"property_name": "FACTORY_ID", "is_primary_key": "Y"},
                        {"property_name": "FACTORY_NAME"},
                    ],
                },
            ],
            "edges": [
                {"source": "g:VERTEX:BOTTLE", "target": "g:VERTEX:BATCH", "name": "GRAPH_LABEL"},
                {"source": "g:VERTEX:BATCH", "target": "g:VERTEX:FACTORY", "name": "GRAPH_LABEL"},
            ],
        }
        plan = {
            "root_label": "BOTTLECODE",
            "filter_property": "BOTTLE_CODE",
            "filter_value": "BOT-202608-000277",
            "root_properties": [],
            "target_labels": ["PRODUCTIONBATCH", "FACTORY"],
            "target_properties": {"PRODUCTIONBATCH": ["BATCH_NO"], "FACTORY": ["FACTORY_NAME"]},
        }

        compiled = self.service._compile_topology_graph_plan(plan, topology, "查询 BOT-202608-000277 的生产追溯")

        self.assertIsNotNone(compiled)
        self.assertIn("MATCH (r IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(n1 IS PRODUCTIONBATCH)", compiled["sql"])
        self.assertIn("-[e2 IS GRAPH_LABEL]->(n2 IS FACTORY)", compiled["sql"])
        self.assertIn("WHERE r.ROOT_BOTTLE_CODE = 'BOT-202608-000277'", compiled["sql"])
        self.assertNotIn("ROOT_CODE_TYPE", compiled["sql"])
        self.assertNotIn("ROOT_PRODUCT_ID", compiled["sql"])
        self.assertNotIn("ROOT_BATCH_ID", compiled["sql"])
        self.assertNotIn("ROOT_LINE_ID", compiled["sql"])

    def test_topology_plan_compiler_limits_explicit_packaging_query_to_code_fields(self):
        labels = (
            ("BOTTLECODE", "BOTTLE_ID", "BOTTLE_CODE"),
            ("PACKCODE", "PACK_ID", "PACK_CODE"),
            ("CASECODE", "CASE_ID", "CASE_CODE"),
            ("PALLETCODE", "PALLET_ID", "PALLET_CODE"),
            ("STACKCODE", "STACK_ID", "STACK_CODE"),
        )
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {
                    "id": f"g:VERTEX:{label}",
                    "displayName": label,
                    "properties": [
                        {"property_name": key, "is_primary_key": "Y"},
                        {"property_name": code},
                        {"property_name": "CODE_STATUS"},
                    ],
                }
                for label, key, code in labels
            ],
            "edges": [
                {"source": f"g:VERTEX:{labels[index][0]}", "target": f"g:VERTEX:{labels[index + 1][0]}", "name": "GRAPH_LABEL"}
                for index in range(len(labels) - 1)
            ],
        }
        plan = {
            "root_label": "BOTTLECODE",
            "filter_property": "BOTTLE_CODE",
            "filter_value": "BOT-202608-000277",
            "root_properties": ["CODE_STATUS"],
            "target_labels": ["PACKCODE", "CASECODE", "PALLETCODE", "STACKCODE"],
            "target_properties": {
                "PACKCODE": ["PACK_CODE", "CODE_STATUS"],
                "CASECODE": ["CASE_CODE", "CODE_STATUS"],
                "PALLETCODE": ["PALLET_CODE", "CODE_STATUS"],
                "STACKCODE": ["STACK_CODE", "CODE_STATUS"],
            },
        }

        compiled = self.service._compile_topology_graph_plan(
            plan, topology, "查询 BOT-202608-000277", display_request_text="查询瓶码 BOT-202608-000277 对应的包码、箱码、托码、垛码链路"
        )

        self.assertIsNotNone(compiled)
        self.assertIn("ROOT_BOTTLE_CODE", compiled["sql"])
        for field in ("PACKCODE_PACK_CODE", "CASECODE_CASE_CODE", "PALLETCODE_PALLET_CODE", "STACKCODE_STACK_CODE"):
            self.assertIn(field, compiled["sql"])
        self.assertNotIn("CODE_STATUS", compiled["sql"])

    def test_topology_plan_rejects_historical_bottle_filter_when_current_question_has_batch_code(self):
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {"id": "bottle", "displayName": "BOTTLECODE", "properties": [{"property_name": "BOTTLE_ID", "is_primary_key": "Y"}, {"property_name": "BOTTLE_CODE"}]},
                {"id": "batch", "displayName": "PRODUCTIONBATCH", "properties": [{"property_name": "BATCH_ID", "is_primary_key": "Y"}, {"property_name": "BATCH_NO"}]},
            ],
            "edges": [{"source": "bottle", "target": "batch", "name": "GRAPH_LABEL"}],
        }
        stale_plan = {
            "root_label": "BOTTLECODE", "filter_property": "BOTTLE_CODE", "filter_value": "BOT-202608-000277",
            "target_labels": ["PRODUCTIONBATCH"], "target_properties": {"PRODUCTIONBATCH": ["BATCH_NO"]},
        }

        compiled = self.service._compile_topology_graph_plan(
            stale_plan, topology, "BATCH-202608-005\nBOT-202608-000277", current_question="查询批次 BATCH-202608-005 的质检记录"
        )

        self.assertIsNone(compiled)

    def test_topology_plan_compiler_supports_reverse_batch_to_quality_path(self):
        topology = {
            "graph_name": "PG_JDXQ_SUPPLY_TRACE",
            "nodes": [
                {"id": "batch", "displayName": "PRODUCTIONBATCH", "properties": [{"property_name": "BATCH_ID", "is_primary_key": "Y"}, {"property_name": "BATCH_NO"}]},
                {"id": "quality", "displayName": "QUALITYINSPECTION", "properties": [
                    {"property_name": "INSPECTION_ID", "is_primary_key": "Y"}, {"property_name": "INSPECTION_TYPE"},
                    {"property_name": "INSPECTION_RESULT"}, {"property_name": "INSPECTION_TIME"},
                    {"property_name": "REPORT_NO"}, {"property_name": "INSPECTOR"}, {"property_name": "REMARK"},
                ]},
            ],
            "edges": [{"source": "quality", "target": "batch", "name": "GRAPH_LABEL"}],
        }
        plan = {
            "root_label": "PRODUCTIONBATCH", "filter_property": "BATCH_NO", "filter_value": "BATCH-202608-005",
            "target_labels": ["QUALITYINSPECTION"],
            "target_properties": {"QUALITYINSPECTION": ["INSPECTION_ID", "INSPECTION_TYPE", "INSPECTION_RESULT", "INSPECTION_TIME", "REPORT_NO", "INSPECTOR", "REMARK"]},
        }

        compiled = self.service._compile_topology_graph_plan(
            plan, topology, "BATCH-202608-005", current_question="查询 BATCH-202608-005 的 ONTO_NODE_QUALITYINSPECTION 质检记录"
        )

        self.assertIsNotNone(compiled)
        self.assertIn("MATCH (r IS PRODUCTIONBATCH)<-[e1 IS GRAPH_LABEL]-(n1 IS QUALITYINSPECTION)", compiled["sql"])
        for field in ("INSPECTION_ID", "INSPECTION_TYPE", "INSPECTION_RESULT", "INSPECTION_TIME", "REPORT_NO", "INSPECTOR", "REMARK"):
            self.assertIn(f"QUALITYINSPECTION_{field}", compiled["sql"])


if __name__ == "__main__":
    unittest.main()
