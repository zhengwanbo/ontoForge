import unittest
import asyncio
from io import BytesIO
from types import SimpleNamespace
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
        class DummyQuery:
            def filter(self, *_args, **_kwargs):
                return self

            def first(self):
                return object()

        class DummyDb:
            def query(self, *_args, **_kwargs):
                return DummyQuery()

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

        uploaded = self.service.upload_managed_skill("dm_supply", "supply-trace.zip", archive.getvalue(), "tester")

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

    def test_find_graph_path_returns_shortest_path_between_labels(self):
        topology = {
            "nodes": [
                {"id": "n1", "displayName": "BOTTLECODE"},
                {"id": "n2", "displayName": "PRODUCTIONBATCH"},
                {"id": "n3", "displayName": "FACTORY"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "name": "GRAPH_LABEL"},
                {"source": "n2", "target": "n3", "name": "GRAPH_LABEL"},
            ],
        }

        path = self.service._find_graph_path(topology, "BOTTLECODE", "FACTORY")

        self.assertEqual(
            [
                {"target": "PRODUCTIONBATCH", "edge": "GRAPH_LABEL", "direction": "OUT"},
                {"target": "FACTORY", "edge": "GRAPH_LABEL", "direction": "OUT"},
            ],
            path,
        )

    def test_build_graph_relation_query_sql_uses_root_filter_and_target_columns(self):
        root_node = {
            "displayName": "BOTTLECODE",
            "properties": [
                {"property_name": "BOTTLE_ID", "is_primary_key": "Y"},
                {"property_name": "BOTTLE_CODE"},
            ],
        }
        target_node = {
            "displayName": "QUALITYINSPECTION",
            "properties": [
                {"property_name": "INSPECTION_ID", "is_primary_key": "Y"},
                {"property_name": "INSPECTION_RESULT"},
                {"property_name": "INSPECTION_TIME"},
            ],
        }

        sql = self.service._build_graph_relation_query_sql(
            "PG_TRACE",
            root_node,
            target_node,
            [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
            "BOTTLE_CODE",
            "BOT-001",
            ["BOTTLE_CODE"],
            ["INSPECTION_RESULT", "INSPECTION_TIME"],
        )

        self.assertIn("MATCH (r IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(t IS QUALITYINSPECTION)", sql)
        self.assertIn("r.BOTTLE_CODE AS ROOT_BOTTLE_CODE", sql)
        self.assertIn("t.INSPECTION_RESULT AS QUALITYINSPECTION_INSPECTION_RESULT", sql)
        self.assertIn("WHERE ROOT_BOTTLE_CODE = 'BOT-001'", sql)

    def test_build_execution_contract_reference_prefers_entry_bindings(self):
        skill_context = {
            "analysis_semantics": {
                "analysis_profile": {"entry_entity_ids": ["ent_bottle"]},
                "metrics": [{"metric_code": "defect_rate", "entity_id": "ent_bottle"}],
            },
            "ontology_graph_binding": {
                "entity_bindings": [
                    {"entity_id": "ent_bottle", "matched": True, "graph_labels": ["BOTTLECODE"]},
                    {"entity_id": "ent_batch", "matched": True, "graph_labels": ["PRODUCTIONBATCH"]},
                ],
            },
        }

        contract = self.service._safe_json_loads(
            self.service._build_execution_contract_reference(skill_context, {"nodes": []}),
            {},
        )

        self.assertEqual(["BOTTLECODE"], contract["entry_objects"])
        self.assertIn("PRODUCTIONBATCH", contract["target_objects"])
        self.assertIn("fact_aggregate", contract["query_modes"])
        self.assertIn("group_by_object", contract["query_modes"])
        self.assertIn("filter_aggregate_result", contract["query_modes"])
        self.assertIn("order_and_limit", contract["query_modes"])
        self.assertIn("group_by_time_window", contract["query_modes"])
        self.assertIn("metric_formula", contract["query_modes"])
        self.assertIn("group_by_object_time_window", contract["query_modes"])
        self.assertTrue(contract["reference_patterns"])
        self.assertIn("reference_group_by_object", [item["pattern_id"] for item in contract["reference_patterns"]])
        self.assertIn("瓶码", contract["object_aliases"]["BOTTLECODE"])
        self.assertIn("property_aliases", contract)
        self.assertIn("BOTTLECODE", contract["property_aliases"])

    def test_load_execution_contract_adds_default_reference_patterns_when_missing(self):
        json_text = """
        {
          "entry_objects": ["QUALITYINSPECTION"],
          "target_objects": ["FACTORY"],
          "query_modes": ["single_node", "path_expand", "group_by_time_window"]
        }
        """
        contract = self.service._load_execution_contract({
            "references/execution-contract.json": json_text
        })

        self.assertTrue(contract["reference_patterns"])
        self.assertIn("reference_time_trend", [item["pattern_id"] for item in contract["reference_patterns"]])
        self.assertIn("QUALITYINSPECTION", contract["entry_objects"])

    def test_resolve_managed_skill_plan_prefers_reference_pattern_before_llm_planner(self):
        class DummyLLMService:
            def __init__(self):
                self.called = False

            async def call_llm(self, *_args, **_kwargs):
                self.called = True
                raise AssertionError("reference pattern hit should bypass llm planner")

            @staticmethod
            def _extract_json_object(_raw):
                return {}

        self.service.llm_service = DummyLLMService()

        contract = {
            "entry_objects": ["OUTBOUNDORDER"],
            "target_objects": ["DISTRIBUTOR"],
            "query_modes": ["single_node", "path_expand", "group_by_object"],
            "required_display_properties": {
                "OUTBOUNDORDER": ["OUTBOUND_NO"],
                "DISTRIBUTOR": ["DISTRIBUTOR_NAME", "DISTRIBUTOR_CODE"],
            },
            "time_dimensions": {},
            "reference_patterns": [
                {
                    "pattern_id": "dist_group",
                    "question_pattern": "查询出库单关联哪些经销商，分别多少",
                    "plan_template": ["select_root", "expand_relations", "group_by_object", "summarize_evidence"],
                    "root_candidates": ["OUTBOUNDORDER"],
                    "target_candidates": ["DISTRIBUTOR"],
                    "required_keywords": ["多少"],
                    "optional_keywords": ["哪些", "分别", "经销商"],
                    "max_targets": 1,
                }
            ],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [
                        {"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"},
                        {"property_name": "DISTRIBUTOR_CODE"},
                        {"property_name": "DISTRIBUTOR_NAME"},
                    ],
                },
            ],
            "edges": [
                {"name": "GRAPH_LABEL", "source": "OUTBOUNDORDER", "target": "DISTRIBUTOR"},
            ],
        }

        plan = asyncio.run(self.service._resolve_managed_skill_plan(
            skill_markdown="# skill",
            skill_files={},
            question="查询 OUT-001 关联哪些经销商，分别多少？",
            conversation_context="无",
            topology=topology,
            llm_config=type("Cfg", (), {"timeout": 30})(),
            skill_guidance="",
            execution_contract=contract,
        ))

        self.assertEqual("REFERENCE_PATTERN", plan["planning_mode"])
        self.assertEqual("dist_group", plan["reference_pattern_id"])
        self.assertEqual("OUTBOUNDORDER", plan["steps"][0]["label"])
        self.assertEqual("DISTRIBUTOR", plan["steps"][1]["label"])
        self.assertIn("group_by_object", [item["action"] for item in plan["steps"]])
        self.assertFalse(self.service.llm_service.called)

    def test_reference_pattern_uses_object_aliases_to_pick_matching_root_and_target(self):
        class DummyLLMService:
            def __init__(self):
                self.called = False

            async def call_llm(self, *_args, **_kwargs):
                self.called = True
                raise AssertionError("reference pattern hit should bypass llm planner")

            @staticmethod
            def _extract_json_object(_raw):
                return {}

        self.service.llm_service = DummyLLMService()
        contract = {
            "entry_objects": ["BOTTLECODE", "OUTBOUNDORDER"],
            "target_objects": ["FACTORY", "DISTRIBUTOR"],
            "query_modes": ["single_node", "path_expand", "group_by_object"],
            "required_display_properties": {
                "OUTBOUNDORDER": ["OUTBOUND_NO"],
                "DISTRIBUTOR": ["DISTRIBUTOR_NAME"],
            },
            "object_aliases": {
                "OUTBOUNDORDER": ["出库单"],
                "DISTRIBUTOR": ["经销商"],
                "BOTTLECODE": ["瓶码"],
                "FACTORY": ["工厂"],
            },
            "reference_patterns": [
                {
                    "pattern_id": "default_group",
                    "question_pattern": "查询关联对象分别多少",
                    "plan_template": ["select_root", "expand_relations", "group_by_object", "summarize_evidence"],
                    "root_candidates": ["BOTTLECODE", "OUTBOUNDORDER"],
                    "target_candidates": ["FACTORY", "DISTRIBUTOR"],
                    "required_keywords": ["多少"],
                    "optional_keywords": ["哪些", "分别"],
                    "max_targets": 1,
                }
            ],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "BOTTLECODE",
                    "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                    "properties": [{"property_name": "BOTTLE_ID", "is_primary_key": "Y"}, {"property_name": "BOTTLE_CODE"}],
                },
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
                },
                {
                    "displayName": "FACTORY",
                    "tableName": "GYL.ONTO_NODE_FACTORY",
                    "properties": [{"property_name": "FACTORY_ID", "is_primary_key": "Y"}, {"property_name": "FACTORY_NAME"}],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [{"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"}, {"property_name": "DISTRIBUTOR_NAME"}],
                },
            ],
            "edges": [
                {"name": "GRAPH_LABEL", "source": "BOTTLECODE", "target": "FACTORY"},
                {"name": "GRAPH_LABEL", "source": "OUTBOUNDORDER", "target": "DISTRIBUTOR"},
            ],
        }

        plan = asyncio.run(self.service._resolve_managed_skill_plan(
            skill_markdown="# skill",
            skill_files={},
            question="查询出库单关联哪些经销商，分别多少？",
            conversation_context="无",
            topology=topology,
            llm_config=type("Cfg", (), {"timeout": 30})(),
            skill_guidance="",
            execution_contract=contract,
        ))

        self.assertEqual("REFERENCE_PATTERN", plan["planning_mode"])
        self.assertEqual("OUTBOUNDORDER", plan["steps"][0]["label"])
        self.assertEqual("DISTRIBUTOR", plan["steps"][1]["label"])
        self.assertFalse(self.service.llm_service.called)

    def test_reference_pattern_can_use_property_and_relation_aliases_for_non_builtin_labels(self):
        class DummyLLMService:
            def __init__(self):
                self.called = False

            async def call_llm(self, *_args, **_kwargs):
                self.called = True
                raise AssertionError("reference pattern hit should bypass llm planner")

            @staticmethod
            def _extract_json_object(_raw):
                return {}

        self.service.llm_service = DummyLLMService()
        contract = {
            "entry_objects": ["ORDER"],
            "target_objects": ["STORE", "CUSTOMER"],
            "query_modes": ["single_node", "path_expand", "group_by_object"],
            "required_display_properties": {
                "ORDER": ["ORDER_NO"],
                "STORE": ["STORE_NAME"],
                "CUSTOMER": ["CUSTOMER_NAME"],
            },
            "object_aliases": {
                "ORDER": ["订单"],
                "STORE": ["门店"],
                "CUSTOMER": ["客户"],
            },
            "property_aliases": {
                "STORE": ["门店号", "门店名称"],
                "CUSTOMER": ["客户号", "客户名称"],
            },
            "relation_aliases": {
                "ORDER->STORE": ["订单归属门店"],
                "ORDER->CUSTOMER": ["订单关联客户"],
            },
            "reference_patterns": [
                {
                    "pattern_id": "order_group",
                    "question_pattern": "查询订单关联对象分别多少",
                    "plan_template": ["select_root", "expand_relations", "group_by_object", "summarize_evidence"],
                    "root_candidates": ["ORDER"],
                    "target_candidates": ["STORE", "CUSTOMER"],
                    "required_keywords": ["多少"],
                    "optional_keywords": ["关联", "分别"],
                    "max_targets": 1,
                }
            ],
        }
        topology = {
            "nodes": [
                {"displayName": "ORDER", "tableName": "T_ORDER", "properties": [{"property_name": "ORDER_ID", "is_primary_key": "Y"}, {"property_name": "ORDER_NO"}]},
                {"displayName": "STORE", "tableName": "T_STORE", "properties": [{"property_name": "STORE_ID", "is_primary_key": "Y"}, {"property_name": "STORE_NAME"}]},
                {"displayName": "CUSTOMER", "tableName": "T_CUSTOMER", "properties": [{"property_name": "CUSTOMER_ID", "is_primary_key": "Y"}, {"property_name": "CUSTOMER_NAME"}]},
            ],
            "edges": [
                {"name": "BELONGS_TO", "source": "ORDER", "target": "STORE"},
                {"name": "LINK_TO", "source": "ORDER", "target": "CUSTOMER"},
            ],
        }

        plan = asyncio.run(self.service._resolve_managed_skill_plan(
            skill_markdown="# skill",
            skill_files={},
            question="查询订单归属门店号分别多少？",
            conversation_context="无",
            topology=topology,
            llm_config=type("Cfg", (), {"timeout": 30})(),
            skill_guidance="",
            execution_contract=contract,
        ))

        self.assertEqual("REFERENCE_PATTERN", plan["planning_mode"])
        self.assertEqual("ORDER", plan["steps"][0]["label"])
        self.assertEqual("STORE", plan["steps"][1]["label"])
        self.assertFalse(self.service.llm_service.called)

    def test_build_managed_skill_planning_stats_counts_reference_pattern_turns(self):
        stats = self.service._build_managed_skill_planning_stats({
            "turn_results": [
                {"plan": {"intent_type": "SESSION_READY"}},
                {"plan": {"intent_type": "CLARIFICATION_REQUIRED", "planning_mode": ""}},
                {"plan": {"intent_type": "RELATION_PATH_LOOKUP", "planning_mode": "REFERENCE_PATTERN", "reference_pattern_id": "reference_relation_lookup"}},
                {"plan": {"intent_type": "METRIC_EXPLANATION", "planning_mode": "REFERENCE_PATTERN", "reference_pattern_id": "reference_group_by_object"}},
                {"plan": {"intent_type": "METRIC_EXPLANATION", "planning_mode": "LLM_PLAN", "reference_pattern_id": ""}},
            ]
        })

        self.assertEqual(4, stats["total_turns"])
        self.assertEqual(3, stats["actionable_turns"])
        self.assertEqual(2, stats["reference_pattern_turns"])
        self.assertEqual(1, stats["llm_plan_turns"])
        self.assertEqual(1, stats["clarification_turns"])
        self.assertEqual(66.67, stats["reference_pattern_hit_rate"])
        self.assertEqual(2, stats["llm_planner_saved_count"])
        self.assertEqual("LLM_PLAN", stats["latest_planning_mode"])

    def test_serialize_managed_skill_test_session_includes_domain_fields(self):
        class DummyQuery:
            def __init__(self, result):
                self.result = result

            def filter(self, *_args, **_kwargs):
                return self

            def first(self):
                return self.result

        class DummyDb:
            def query(self, model, *_args, **_kwargs):
                if model.__name__ == "SysManagedAgentSkill":
                    return DummyQuery(SimpleNamespace(domain_id="dm_1"))
                if model.__name__ == "SysDomain":
                    return DummyQuery(SimpleNamespace(domain_name="质量分析域"))
                return DummyQuery(None)

        self.service.db = DummyDb()
        payload = self.service._serialize_managed_skill_test_session(
            SimpleNamespace(
                session_id="mst_1",
                managed_skill_id="ms_1",
                skill_name="技能A",
                source_id="src_1",
                source_name="数据源A",
                schema_name="GYL",
                llm_config_id="cfg_1",
                sample_limit=100,
                session_title="title",
                last_question="question",
                message_count=2,
                created_by="tester",
                created_at="2026-09-10T00:00:00",
                updated_at="2026-09-10T00:10:00",
                conversation_json="[]",
                result_json='{"turn_results":[]}',
            ),
            include_result=False,
        )

        self.assertEqual("dm_1", payload["domain_id"])
        self.assertEqual("质量分析域", payload["domain_name"])

    def test_build_fact_aggregate_step_appends_count_metric_for_stat_question(self):
        plan = {
            "steps": [{
                "step_id": "s1",
                "action": "select_root",
                "label": "OUTBOUNDORDER",
                "filter": {"property": "OUTBOUND_NO", "value": "OUT-001"},
                "display_properties": ["OUTBOUND_NO"],
            }],
            "selected_objects": ["OUTBOUNDORDER"],
        }
        topology = {
            "nodes": [{
                "displayName": "OUTBOUNDORDER",
                "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
            }]
        }
        skill_files = {
            "references/metric-catalog.json": """
            {
              "metrics": [
                {
                  "metric_code": "OUTBOUND_COUNT",
                  "metric_name": "出库单数量",
                  "entity_name": "OutboundOrder",
                  "aggregation_method": "COUNT"
                }
              ]
            }
            """
        }

        step = self.service._build_fact_aggregate_step(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question="这个出库单的数量统计是多少？",
        )

        self.assertIsNotNone(step)
        self.assertEqual("fact_aggregate", step["action"])
        self.assertEqual("OUTBOUND_COUNT", step["metric_code"])
        self.assertEqual("COUNT", step["aggregation_method"])

    def test_build_fact_aggregate_step_prefers_related_target_metric(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "BOTTLECODE",
                    "filter": {"property": "BOTTLE_CODE", "value": "BOT-001"},
                    "display_properties": ["BOTTLE_CODE"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "QUALITYINSPECTION",
                    "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["INSPECTION_ID", "INSPECTION_RESULT"],
                },
            ],
            "selected_objects": ["BOTTLECODE", "QUALITYINSPECTION"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "BOTTLECODE",
                    "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                    "properties": [{"property_name": "BOTTLE_ID", "is_primary_key": "Y"}, {"property_name": "BOTTLE_CODE"}],
                },
                {
                    "displayName": "QUALITYINSPECTION",
                    "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                    "properties": [
                        {"property_name": "INSPECTION_ID", "is_primary_key": "Y"},
                        {"property_name": "INSPECTION_RESULT"},
                    ],
                },
            ],
        }
        skill_files = {
            "references/metric-catalog.json": """
            {
              "metrics": [
                {
                  "metric_code": "OUTBOUND_COUNT",
                  "metric_name": "出库单数量",
                  "entity_name": "BottleCode",
                  "aggregation_method": "COUNT"
                },
                {
                  "metric_code": "QUALITY_COUNT",
                  "metric_name": "质检记录数量",
                  "entity_name": "QualityInspection",
                  "aggregation_method": "COUNT_DISTINCT",
                  "calculation_expr": "INSPECTION_ID"
                }
              ]
            }
            """,
        }

        step = self.service._build_fact_aggregate_step(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question="这个瓶码对应的质检记录数量是多少？",
        )

        self.assertIsNotNone(step)
        self.assertEqual("QUALITYINSPECTION", step["label"])
        self.assertEqual("related_object", step["scope"])
        self.assertEqual("s2", step["based_on_step"])
        self.assertEqual("QUALITY_COUNT", step["metric_code"])
        self.assertEqual("INSPECTION_ID", step["column_name"])

    def test_build_group_by_object_step_prefers_related_target_metric(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "OUTBOUNDORDER",
                    "filter": {"property": "OUTBOUND_NO", "value": "OUT-001"},
                    "display_properties": ["OUTBOUND_NO"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "DISTRIBUTOR",
                    "path": [{"target": "DISTRIBUTOR", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["DISTRIBUTOR_CODE", "DISTRIBUTOR_NAME"],
                },
            ],
            "selected_objects": ["OUTBOUNDORDER", "DISTRIBUTOR"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [
                        {"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"},
                        {"property_name": "DISTRIBUTOR_CODE"},
                        {"property_name": "DISTRIBUTOR_NAME"},
                    ],
                },
            ],
        }
        skill_files = {
            "references/metric-catalog.json": """
            {
              "metrics": [
                {
                  "metric_code": "DISTRIBUTOR_LINK_COUNT",
                  "metric_name": "经销商关联数量",
                  "entity_name": "Distributor",
                  "aggregation_method": "COUNT"
                }
              ]
            }
            """,
        }

        step = self.service._build_group_by_object_step(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question="这个出库单关联哪些经销商，分别多少？",
        )

        self.assertIsNotNone(step)
        self.assertEqual("group_by_object", step["action"])
        self.assertEqual("DISTRIBUTOR", step["label"])
        self.assertEqual("s2", step["based_on_step"])
        self.assertEqual("DISTRIBUTOR_LINK_COUNT", step["metric_code"])
        self.assertEqual(["DISTRIBUTOR_CODE", "DISTRIBUTOR_NAME"], step["group_properties"])

    def test_build_group_by_object_step_falls_back_to_safe_object_count(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "OUTBOUNDORDER",
                    "filter": {"property": "OUTBOUND_NO", "value": "OUT-001"},
                    "display_properties": ["OUTBOUND_NO"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "DISTRIBUTOR",
                    "path": [{"target": "DISTRIBUTOR", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["DISTRIBUTOR_NAME"],
                },
            ],
            "selected_objects": ["OUTBOUNDORDER", "DISTRIBUTOR"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [
                        {"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"},
                        {"property_name": "DISTRIBUTOR_NAME"},
                    ],
                },
            ],
        }

        step = self.service._build_group_by_object_step(
            plan=plan,
            topology=topology,
            skill_files={},
            question="这个出库单关联哪些经销商，分别多少？",
        )

        self.assertIsNotNone(step)
        self.assertEqual("group_by_object", step["action"])
        self.assertEqual("COUNT_DISTINCT", step["aggregation_method"])
        self.assertEqual("DISTRIBUTOR_ID", step["column_name"])
        self.assertEqual(["DISTRIBUTOR_NAME"], step["group_properties"])

    def test_build_filter_aggregate_result_step_uses_threshold_from_question(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "group_by_object",
                    "label": "DISTRIBUTOR",
                    "metric_code": "DISTRIBUTOR_LINK_COUNT",
                    "metric_name": "经销商关联数量",
                }
            ]
        }

        step = self.service._build_filter_aggregate_result_step(
            plan=plan,
            question="找出经销商关联数量大于5的对象",
        )

        self.assertIsNotNone(step)
        self.assertEqual("filter_aggregate_result", step["action"])
        self.assertEqual(">", step["operator"])
        self.assertEqual(5, step["threshold"])

    def test_build_order_and_limit_step_reads_topn_from_question(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "group_by_object",
                    "label": "DISTRIBUTOR",
                    "metric_code": "DISTRIBUTOR_LINK_COUNT",
                    "metric_name": "经销商关联数量",
                },
                {
                    "step_id": "s2",
                    "action": "filter_aggregate_result",
                    "label": "DISTRIBUTOR",
                    "based_on_step": "s1",
                    "metric_code": "DISTRIBUTOR_LINK_COUNT",
                    "metric_name": "经销商关联数量",
                },
            ]
        }

        step = self.service._build_order_and_limit_step(
            plan=plan,
            question="取前10个经销商",
        )

        self.assertIsNotNone(step)
        self.assertEqual("order_and_limit", step["action"])
        self.assertEqual("s2", step["based_on_step"])
        self.assertEqual(10, step["limit"])
        self.assertEqual("DESC", step["direction"])

    def test_build_apply_rules_step_uses_rule_catalog_and_based_on_latest_aggregate(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "group_by_object",
                    "label": "QUALITYINSPECTION",
                    "metric_code": "QUALITY_COUNT",
                    "metric_name": "质检记录数量",
                }
            ]
        }
        skill_files = {
            "references/rule-catalog.json": """
            {
              "rules": [
                {
                  "rule_name": "质检超规",
                  "rule_desc": "质检记录数量大于5视为异常"
                }
              ]
            }
            """
        }

        step = self.service._build_apply_rules_step(
            plan=plan,
            skill_files=skill_files,
            question="判断这些质检记录数量是否异常",
        )

        self.assertIsNotNone(step)
        self.assertEqual("apply_rules", step["action"])
        self.assertEqual("s1", step["based_on_step"])
        self.assertEqual("QUALITY_COUNT", step["metric_code"])

    def test_build_group_by_time_window_step_uses_time_dimension_and_granularity(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "QUALITYINSPECTION",
                    "filter": {"property": "BATCH_NO", "value": "BATCH-001"},
                    "display_properties": ["BATCH_NO"],
                }
            ],
            "selected_objects": ["QUALITYINSPECTION"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "QUALITYINSPECTION",
                    "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                    "properties": [
                        {"property_name": "INSPECTION_ID", "is_primary_key": "Y"},
                        {"property_name": "INSPECTION_TIME"},
                        {"property_name": "RESULT_COUNT"},
                    ],
                }
            ]
        }
        skill_files = {
            "references/metric-catalog.json": """
            {
              "metrics": [
                {
                  "metric_code": "QUALITY_COUNT",
                  "metric_name": "质检数量",
                  "entity_name": "QualityInspection",
                  "aggregation_method": "COUNT_DISTINCT",
                  "calculation_expr": "INSPECTION_ID"
                }
              ]
            }
            """
        }

        step = self.service._build_group_by_time_window_step(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question="近30天每天的质检数量趋势",
            execution_contract={"time_dimensions": {"QUALITYINSPECTION": ["INSPECTION_TIME"]}},
        )

        self.assertIsNotNone(step)
        self.assertEqual("group_by_time_window", step["action"])
        self.assertEqual("INSPECTION_TIME", step["time_dimension"])
        self.assertEqual("DAY", step["time_granularity"])
        self.assertEqual("30D", step["time_window"])
        self.assertEqual("QUALITY_COUNT", step["metric_code"])

    def test_build_group_by_object_time_window_step_uses_group_fields_and_time_dimension(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "OUTBOUNDORDER",
                    "filter": {"property": "OUTBOUND_NO", "value": "OUT-001"},
                    "display_properties": ["OUTBOUND_NO"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "DISTRIBUTOR",
                    "path": [{"target": "DISTRIBUTOR", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["DISTRIBUTOR_NAME", "SCAN_TIME"],
                },
            ],
            "selected_objects": ["OUTBOUNDORDER", "DISTRIBUTOR"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [
                        {"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"},
                        {"property_name": "DISTRIBUTOR_NAME"},
                        {"property_name": "SCAN_TIME"},
                    ],
                },
            ]
        }

        step = self.service._build_group_by_object_time_window_step(
            plan=plan,
            topology=topology,
            skill_files={},
            question="近30天每天每个经销商的数量趋势",
            execution_contract={"time_dimensions": {"DISTRIBUTOR": ["SCAN_TIME"]}},
        )

        self.assertIsNotNone(step)
        self.assertEqual("group_by_object_time_window", step["action"])
        self.assertEqual("DISTRIBUTOR", step["label"])
        self.assertEqual(["DISTRIBUTOR_NAME"], step["group_properties"])
        self.assertEqual("SCAN_TIME", step["time_dimension"])
        self.assertEqual("30D", step["time_window"])

    def test_build_metric_formula_steps_creates_ratio_pipeline(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "QUALITYINSPECTION",
                    "filter": {"property": "BATCH_NO", "value": "BATCH-001"},
                    "display_properties": ["BATCH_NO"],
                }
            ],
            "selected_objects": ["QUALITYINSPECTION"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "QUALITYINSPECTION",
                    "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                    "properties": [
                        {"property_name": "INSPECTION_ID", "is_primary_key": "Y"},
                        {"property_name": "NG_COUNT"},
                        {"property_name": "TOTAL_COUNT"},
                        {"property_name": "INSPECTION_TIME"},
                    ],
                }
            ]
        }
        skill_files = {
            "references/metric-catalog.json": """
            {
              "metrics": [
                {
                  "metric_code": "NG_COUNT",
                  "metric_name": "不良数",
                  "entity_name": "QualityInspection",
                  "aggregation_method": "SUM",
                  "calculation_expr": "NG_COUNT"
                },
                {
                  "metric_code": "TOTAL_COUNT",
                  "metric_name": "检验总数",
                  "entity_name": "QualityInspection",
                  "aggregation_method": "SUM",
                  "calculation_expr": "TOTAL_COUNT"
                },
                {
                  "metric_code": "DEFECT_RATE",
                  "metric_name": "缺陷率",
                  "entity_name": "QualityInspection",
                  "aggregation_method": "RATIO",
                  "formula_type": "ratio",
                  "numerator_metric_code": "NG_COUNT",
                  "denominator_metric_code": "TOTAL_COUNT"
                }
              ]
            }
            """
        }

        steps = self.service._build_metric_formula_steps(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question="这个批次的缺陷率是多少？",
            execution_contract={"time_dimensions": {"QUALITYINSPECTION": ["INSPECTION_TIME"]}},
        )

        self.assertEqual(3, len(steps))
        self.assertEqual("fact_aggregate", steps[0]["action"])
        self.assertEqual("NG_COUNT", steps[0]["metric_code"])
        self.assertEqual("TOTAL_COUNT", steps[1]["metric_code"])
        self.assertEqual("metric_formula", steps[2]["action"])
        self.assertEqual("DEFECT_RATE", steps[2]["metric_code"])

    def test_build_metric_formula_steps_creates_object_time_ratio_pipeline(self):
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "PRODUCTIONBATCH",
                    "filter": {"property": "BATCH_NO", "value": "BATCH-001"},
                    "display_properties": ["BATCH_NO"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "FACTORY",
                    "path": [{"target": "FACTORY", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["FACTORY_NAME", "PRODUCTION_DATE", "NG_COUNT", "TOTAL_COUNT"],
                },
            ],
            "selected_objects": ["PRODUCTIONBATCH", "FACTORY"],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "PRODUCTIONBATCH",
                    "tableName": "GYL.ONTO_NODE_PRODUCTIONBATCH",
                    "properties": [{"property_name": "BATCH_ID", "is_primary_key": "Y"}, {"property_name": "BATCH_NO"}],
                },
                {
                    "displayName": "FACTORY",
                    "tableName": "GYL.ONTO_NODE_FACTORY",
                    "properties": [
                        {"property_name": "FACTORY_ID", "is_primary_key": "Y"},
                        {"property_name": "FACTORY_NAME"},
                        {"property_name": "PRODUCTION_DATE"},
                        {"property_name": "NG_COUNT"},
                        {"property_name": "TOTAL_COUNT"},
                    ],
                },
            ]
        }
        skill_files = {
            "references/metric-catalog.json": """
            {
              "metrics": [
                {"metric_code": "NG_COUNT", "metric_name": "不良数", "entity_name": "Factory", "aggregation_method": "SUM", "calculation_expr": "NG_COUNT"},
                {"metric_code": "TOTAL_COUNT", "metric_name": "总数", "entity_name": "Factory", "aggregation_method": "SUM", "calculation_expr": "TOTAL_COUNT"},
                {"metric_code": "DEFECT_RATE", "metric_name": "缺陷率", "entity_name": "Factory", "aggregation_method": "RATIO", "formula_type": "ratio", "numerator_metric_code": "NG_COUNT", "denominator_metric_code": "TOTAL_COUNT"}
              ]
            }
            """
        }

        steps = self.service._build_metric_formula_steps(
            plan=plan,
            topology=topology,
            skill_files=skill_files,
            question="近30天每天每个工厂的缺陷率趋势",
            execution_contract={"time_dimensions": {"FACTORY": ["PRODUCTION_DATE"]}},
        )

        self.assertEqual(3, len(steps))
        self.assertEqual("group_by_object_time_window", steps[0]["action"])
        self.assertEqual("group_by_object_time_window", steps[1]["action"])
        self.assertEqual("metric_formula", steps[2]["action"])
        self.assertEqual(["FACTORY_NAME"], steps[2]["group_properties"])

    def test_build_fact_aggregate_sql_uses_root_table_and_filter(self):
        sql = self.service._build_fact_aggregate_sql(
            metric_step={
                "metric_code": "OUTBOUND_COUNT",
                "aggregation_method": "COUNT",
                "metric_name": "出库单数量",
            },
            root_node={
                "displayName": "OUTBOUNDORDER",
                "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                "properties": [{"property_name": "OUTBOUND_NO"}],
            },
            filter_property="OUTBOUND_NO",
            filter_value="OUT-001",
        )

        self.assertIn("FROM GYL.ONTO_NODE_OUTBOUNDORDER", sql)
        self.assertIn("COUNT(*) AS METRIC_VALUE", sql)
        self.assertIn("WHERE OUTBOUND_NO = 'OUT-001'", sql)

    def test_build_fact_aggregate_sql_uses_graph_path_for_related_target(self):
        sql = self.service._build_fact_aggregate_sql(
            metric_step={
                "metric_code": "QUALITY_COUNT",
                "aggregation_method": "COUNT_DISTINCT",
                "metric_name": "质检记录数量",
                "column_name": "INSPECTION_ID",
                "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
            },
            root_node={
                "displayName": "BOTTLECODE",
                "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                "properties": [{"property_name": "BOTTLE_ID", "is_primary_key": "Y"}, {"property_name": "BOTTLE_CODE"}],
            },
            graph_name="PG_TRACE",
            root_step={"step_id": "s1", "action": "select_root", "label": "BOTTLECODE"},
            based_on_step={
                "step_id": "s2",
                "action": "expand_relations",
                "label": "QUALITYINSPECTION",
                "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
            },
            target_node={
                "displayName": "QUALITYINSPECTION",
                "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                "properties": [{"property_name": "INSPECTION_ID", "is_primary_key": "Y"}],
            },
            filter_property="BOTTLE_CODE",
            filter_value="BOT-001",
        )

        self.assertIn("FROM GRAPH_TABLE(", sql)
        self.assertIn("MATCH (r IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(t IS QUALITYINSPECTION)", sql)
        self.assertIn("t.INSPECTION_ID AS AGG_INSPECTION_ID", sql)
        self.assertIn("COUNT(DISTINCT AGG_INSPECTION_ID) AS METRIC_VALUE", sql)
        self.assertIn("WHERE ROOT_BOTTLE_CODE = 'BOT-001'", sql)

    def test_build_group_by_object_sql_uses_graph_path_and_group_columns(self):
        sql = self.service._build_group_by_object_sql(
            group_step={
                "metric_code": "DISTRIBUTOR_LINK_COUNT",
                "metric_name": "经销商关联数量",
                "aggregation_method": "COUNT",
                "group_properties": ["DISTRIBUTOR_CODE", "DISTRIBUTOR_NAME"],
            },
            root_node={
                "displayName": "OUTBOUNDORDER",
                "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
            },
            graph_name="PG_TRACE",
            based_on_step={
                "step_id": "s2",
                "action": "expand_relations",
                "label": "DISTRIBUTOR",
                "path": [{"target": "DISTRIBUTOR", "edge": "GRAPH_LABEL", "direction": "OUT"}],
            },
            target_node={
                "displayName": "DISTRIBUTOR",
                "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                "properties": [
                    {"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"},
                    {"property_name": "DISTRIBUTOR_CODE"},
                    {"property_name": "DISTRIBUTOR_NAME"},
                ],
            },
            filter_property="OUTBOUND_NO",
            filter_value="OUT-001",
        )

        self.assertIn("MATCH (r IS OUTBOUNDORDER)-[e1 IS GRAPH_LABEL]->(t IS DISTRIBUTOR)", sql)
        self.assertIn("t.DISTRIBUTOR_CODE AS GROUP_DISTRIBUTOR_CODE", sql)
        self.assertIn("t.DISTRIBUTOR_NAME AS GROUP_DISTRIBUTOR_NAME", sql)
        self.assertIn("GROUP_DISTRIBUTOR_CODE AS DISTRIBUTOR_CODE", sql)
        self.assertIn("GROUP BY GROUP_DISTRIBUTOR_CODE, GROUP_DISTRIBUTOR_NAME", sql)
        self.assertIn("ORDER BY METRIC_VALUE DESC", sql)

    def test_build_filter_aggregate_result_sql_wraps_base_query(self):
        sql = self.service._build_filter_aggregate_result_sql(
            base_sql="SELECT METRIC_CODE, METRIC_VALUE FROM RESULT_VIEW",
            filter_step={"operator": ">", "threshold": 5},
        )

        self.assertIn("SELECT * FROM (", sql)
        self.assertIn("WHERE METRIC_VALUE > 5", sql)

    def test_build_order_and_limit_sql_wraps_base_query(self):
        sql = self.service._build_order_and_limit_sql(
            base_sql="SELECT METRIC_CODE, METRIC_VALUE FROM RESULT_VIEW",
            order_step={"order_by": "METRIC_VALUE", "direction": "DESC", "limit": 10},
        )

        self.assertIn("ORDER BY METRIC_VALUE DESC", sql)
        self.assertIn("FETCH FIRST 10 ROWS ONLY", sql)

    def test_build_group_by_time_window_sql_uses_time_bucket(self):
        sql = self.service._build_group_by_time_window_sql(
            time_step={
                "metric_code": "QUALITY_COUNT",
                "aggregation_method": "COUNT_DISTINCT",
                "column_name": "INSPECTION_ID",
                "time_dimension": "INSPECTION_TIME",
                "time_granularity": "DAY",
                "time_window": "30D",
            },
            root_node={
                "displayName": "QUALITYINSPECTION",
                "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                "properties": [{"property_name": "INSPECTION_ID", "is_primary_key": "Y"}, {"property_name": "INSPECTION_TIME"}],
            },
            graph_name="PG_TRACE",
            root_step={"step_id": "s1", "action": "select_root", "label": "QUALITYINSPECTION"},
            based_on_step={"step_id": "s1", "action": "select_root", "label": "QUALITYINSPECTION"},
            target_node=None,
            filter_property="BATCH_NO",
            filter_value="BATCH-001",
        )

        self.assertIn("TIME_BUCKET", sql)
        self.assertIn("TRUNC(INSPECTION_TIME)", sql)
        self.assertIn("INSPECTION_TIME >= TRUNC(SYSDATE) - 30", sql)
        self.assertIn("ORDER BY TIME_BUCKET ASC", sql)

    def test_build_metric_formula_sql_builds_ratio_from_two_queries(self):
        sql = self.service._build_metric_formula_sql(
            formula_step={
                "metric_code": "DEFECT_RATE",
                "formula_type": "RATIO",
                "numerator_step_kind": "FACT_AGGREGATE",
                "denominator_step_kind": "FACT_AGGREGATE",
            },
            numerator_sql="SELECT 'NG_COUNT' AS METRIC_CODE, 4 AS METRIC_VALUE FROM DUAL",
            denominator_sql="SELECT 'TOTAL_COUNT' AS METRIC_CODE, 20 AS METRIC_VALUE FROM DUAL",
        )

        self.assertIn("CROSS JOIN denominator_result", sql)
        self.assertIn("ROUND((n.METRIC_VALUE / d.METRIC_VALUE) * 100, 4)", sql)
        self.assertIn("'DEFECT_RATE' AS METRIC_CODE", sql)

    def test_build_group_by_object_time_window_sql_uses_time_and_group_columns(self):
        sql = self.service._build_group_by_object_time_window_sql(
            step={
                "metric_code": "FACTORY_COUNT",
                "aggregation_method": "COUNT_DISTINCT",
                "column_name": "FACTORY_ID",
                "group_properties": ["FACTORY_NAME"],
                "time_dimension": "PRODUCTION_DATE",
                "time_granularity": "MONTH",
                "time_window": "3M",
            },
            root_node={
                "displayName": "PRODUCTIONBATCH",
                "tableName": "GYL.ONTO_NODE_PRODUCTIONBATCH",
                "properties": [{"property_name": "BATCH_ID", "is_primary_key": "Y"}, {"property_name": "BATCH_NO"}],
            },
            graph_name="PG_TRACE",
            root_step={"step_id": "s1", "action": "select_root", "label": "PRODUCTIONBATCH"},
            based_on_step={
                "step_id": "s2",
                "action": "expand_relations",
                "label": "FACTORY",
                "path": [{"target": "FACTORY", "edge": "GRAPH_LABEL", "direction": "OUT"}],
            },
            target_node={
                "displayName": "FACTORY",
                "tableName": "GYL.ONTO_NODE_FACTORY",
                "properties": [{"property_name": "FACTORY_ID", "is_primary_key": "Y"}, {"property_name": "FACTORY_NAME"}, {"property_name": "PRODUCTION_DATE"}],
            },
            filter_property="BATCH_NO",
            filter_value="BATCH-001",
        )

        self.assertIn("TIME_BUCKET", sql)
        self.assertIn("GROUP_FACTORY_NAME AS FACTORY_NAME", sql)
        self.assertIn("GROUP BY TIME_BUCKET, GROUP_FACTORY_NAME", sql)
        self.assertIn("RAW_TIME >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -3)", sql)

    def test_build_metric_formula_sql_builds_object_time_ratio(self):
        sql = self.service._build_metric_formula_sql(
            formula_step={
                "metric_code": "DEFECT_RATE",
                "formula_type": "RATIO",
                "numerator_step_kind": "OBJECT_TIME_WINDOW_GROUP_BY",
                "denominator_step_kind": "OBJECT_TIME_WINDOW_GROUP_BY",
                "group_properties": ["FACTORY_NAME"],
            },
            numerator_sql="SELECT 'NG_COUNT' AS METRIC_CODE, '2026-09-01' AS TIME_BUCKET, '工厂A' AS FACTORY_NAME, 4 AS METRIC_VALUE FROM DUAL",
            denominator_sql="SELECT 'TOTAL_COUNT' AS METRIC_CODE, '2026-09-01' AS TIME_BUCKET, '工厂A' AS FACTORY_NAME, 20 AS METRIC_VALUE FROM DUAL",
        )

        self.assertIn("n.TIME_BUCKET = d.TIME_BUCKET", sql)
        self.assertIn("n.FACTORY_NAME = d.FACTORY_NAME", sql)
        self.assertIn("n.FACTORY_NAME AS FACTORY_NAME", sql)

    def test_build_managed_skill_analysis_result_includes_trend_summary_and_flags(self):
        result = self.service._build_managed_skill_analysis_result(
            agent_output="趋势分析完成",
            plan={"intent_type": "METRIC_EXPLANATION", "selected_objects": ["QUALITYINSPECTION"]},
            evidence_tables=[
                {
                    "key": "evidence_s2",
                    "kind": "TIME_WINDOW_GROUP_BY",
                    "metric_code": "QUALITY_COUNT",
                    "sample_rows": [
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-01", "METRIC_VALUE": 3},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-02", "METRIC_VALUE": 5},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-03", "METRIC_VALUE": 8},
                    ],
                }
            ],
            skill_files={},
        )

        self.assertIn("RISING_TREND", result["analysis_flags"])
        self.assertIn("DOD_AVAILABLE", result["analysis_flags"])
        self.assertEqual(1, len(result["trend_summaries"]))
        self.assertEqual("2026-09-03", result["trend_summaries"][0]["peak_bucket"])
        self.assertEqual("DOD", result["trend_summaries"][0]["comparison_type"])
        self.assertEqual(166.6667, result["trend_summaries"][0]["change_rate"])
        self.assertEqual("2026-09-03", result["period_comparisons"][0]["max_rise_bucket"])
        self.assertTrue(result["top_findings"])

    def test_build_managed_skill_analysis_result_includes_grouped_trend_summary(self):
        result = self.service._build_managed_skill_analysis_result(
            agent_output="联合趋势分析完成",
            plan={"intent_type": "METRIC_EXPLANATION", "selected_objects": ["PRODUCTIONBATCH", "FACTORY"]},
            evidence_tables=[
                {
                    "key": "evidence_s3",
                    "kind": "OBJECT_TIME_WINDOW_GROUP_BY",
                    "metric_code": "FACTORY_COUNT",
                    "sample_rows": [
                        {"METRIC_CODE": "FACTORY_COUNT", "TIME_BUCKET": "2026-09", "FACTORY_NAME": "工厂A", "METRIC_VALUE": 2},
                        {"METRIC_CODE": "FACTORY_COUNT", "TIME_BUCKET": "2026-10", "FACTORY_NAME": "工厂A", "METRIC_VALUE": 7},
                        {"METRIC_CODE": "FACTORY_COUNT", "TIME_BUCKET": "2026-09", "FACTORY_NAME": "工厂B", "METRIC_VALUE": 6},
                        {"METRIC_CODE": "FACTORY_COUNT", "TIME_BUCKET": "2026-10", "FACTORY_NAME": "工厂B", "METRIC_VALUE": 3},
                    ],
                }
            ],
            skill_files={},
        )

        self.assertEqual(2, len(result["trend_summaries"]))
        groups = [item["group"] for item in result["trend_summaries"]]
        self.assertIn({"FACTORY_NAME": "工厂A"}, groups)
        self.assertIn({"FACTORY_NAME": "工厂B"}, groups)
        self.assertIn("RISING_TREND", result["analysis_flags"])
        self.assertIn("FALLING_TREND", result["analysis_flags"])
        self.assertIn("MOM_AVAILABLE", result["analysis_flags"])
        self.assertEqual("MOM", result["trend_summaries"][0]["comparison_type"])

    def test_build_managed_skill_analysis_result_marks_anomalous_spike(self):
        result = self.service._build_managed_skill_analysis_result(
            agent_output="波动分析完成",
            plan={"intent_type": "METRIC_EXPLANATION", "selected_objects": ["QUALITYINSPECTION"]},
            evidence_tables=[
                {
                    "key": "evidence_s5",
                    "kind": "TIME_WINDOW_GROUP_BY",
                    "metric_code": "QUALITY_COUNT",
                    "sample_rows": [
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-01", "METRIC_VALUE": 1},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-02", "METRIC_VALUE": 2},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-03", "METRIC_VALUE": 20},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-04", "METRIC_VALUE": 3},
                    ],
                }
            ],
            skill_files={},
        )

        self.assertIn("ANOMALOUS_SPIKE", result["analysis_flags"])
        self.assertIn("2026-09-03", result["trend_summaries"][0]["spike_buckets"])
        self.assertTrue(any("异常波动" in item for item in result["top_findings"]))

    def test_build_managed_skill_analysis_result_includes_dod_comparison_fields(self):
        result = self.service._build_managed_skill_analysis_result(
            agent_output="日环比分析完成",
            plan={"intent_type": "METRIC_EXPLANATION", "selected_objects": ["QUALITYINSPECTION"]},
            evidence_tables=[
                {
                    "key": "evidence_s6",
                    "kind": "TIME_WINDOW_GROUP_BY",
                    "metric_code": "QUALITY_COUNT",
                    "sample_rows": [
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-01", "METRIC_VALUE": 10},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-02", "METRIC_VALUE": 15},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-03", "METRIC_VALUE": 12},
                    ],
                }
            ],
            skill_files={},
        )

        summary = result["trend_summaries"][0]
        comparison = result["period_comparisons"][0]
        self.assertEqual("DOD", summary["comparison_type"])
        self.assertEqual("2026-09-02", summary["previous_bucket"])
        self.assertEqual(-3.0, summary["period_change_value"])
        self.assertEqual(-20.0, summary["period_change_rate"])
        self.assertEqual("DOD", comparison["comparison_type"])
        self.assertIn("DOD_AVAILABLE", result["analysis_flags"])
        self.assertTrue(any("DOD" in item for item in result["top_findings"]))

    def test_build_managed_skill_analysis_result_infers_wow_from_weekly_buckets(self):
        result = self.service._build_managed_skill_analysis_result(
            agent_output="周环比分析完成",
            plan={"intent_type": "METRIC_EXPLANATION", "selected_objects": ["QUALITYINSPECTION"]},
            evidence_tables=[
                {
                    "key": "evidence_s7",
                    "kind": "TIME_WINDOW_GROUP_BY",
                    "metric_code": "QUALITY_COUNT",
                    "sample_rows": [
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-01", "METRIC_VALUE": 10},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-08", "METRIC_VALUE": 12},
                        {"METRIC_CODE": "QUALITY_COUNT", "TIME_BUCKET": "2026-09-15", "METRIC_VALUE": 9},
                    ],
                }
            ],
            skill_files={},
        )

        self.assertEqual("WOW", result["trend_summaries"][0]["comparison_type"])
        self.assertIn("WOW_AVAILABLE", result["analysis_flags"])

    def test_build_managed_skill_evidence_tables_executes_related_fact_aggregate(self):
        class DummySourceService:
            def __init__(self):
                self.graph_sql = []
                self.readonly_sql = []

            def execute_remote_graph_query(self, *, source_id, graph_sql, schema, row_limit):
                self.graph_sql.append(graph_sql)
                return {
                    "columns": ["ROOT_BOTTLE_CODE", "QUALITYINSPECTION_INSPECTION_ID"],
                    "rows": [{"ROOT_BOTTLE_CODE": "BOT-001", "QUALITYINSPECTION_INSPECTION_ID": "Q-01"}],
                }

            def execute_remote_readonly_sql(self, *, source_id, query_sql, schema, row_limit):
                self.readonly_sql.append(query_sql)
                return {
                    "columns": ["METRIC_CODE", "METRIC_VALUE"],
                    "rows": [{"METRIC_CODE": "QUALITY_COUNT", "METRIC_VALUE": 1}],
                }

        self.service.source_service = DummySourceService()
        topology = {
            "graph_name": "PG_TRACE",
            "nodes": [
                {
                    "displayName": "BOTTLECODE",
                    "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                    "properties": [{"property_name": "BOTTLE_ID", "is_primary_key": "Y"}, {"property_name": "BOTTLE_CODE"}],
                },
                {
                    "displayName": "QUALITYINSPECTION",
                    "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                    "properties": [{"property_name": "INSPECTION_ID", "is_primary_key": "Y"}, {"property_name": "INSPECTION_RESULT"}],
                },
            ],
        }
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "BOTTLECODE",
                    "filter": {"property": "BOTTLE_CODE", "value": "BOT-001"},
                    "display_properties": ["BOTTLE_CODE"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "QUALITYINSPECTION",
                    "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["INSPECTION_ID"],
                },
                {
                    "step_id": "s3",
                    "action": "fact_aggregate",
                    "label": "QUALITYINSPECTION",
                    "root_label": "BOTTLECODE",
                    "scope": "related_object",
                    "based_on_step": "s2",
                    "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "metric_code": "QUALITY_COUNT",
                    "metric_name": "质检记录数量",
                    "aggregation_method": "COUNT_DISTINCT",
                    "column_name": "INSPECTION_ID",
                },
            ],
        }
        emitted = []

        result = self.service._build_managed_skill_evidence_tables(
            plan=plan,
            topology=topology,
            source_id="src_1",
            schema="GYL",
            sample_limit=20,
            event_callback=emitted.append,
        )

        self.assertEqual(2, len(self.service.source_service.graph_sql))
        self.assertEqual(1, len(self.service.source_service.readonly_sql))
        self.assertIn("FROM GRAPH_TABLE(", self.service.source_service.readonly_sql[0])
        self.assertEqual("FACT_AGGREGATE", result["evidence_tables"][-1]["kind"])
        self.assertEqual(["BOTTLECODE", "QUALITYINSPECTION"], result["evidence_tables"][-1]["related_objects"])
        self.assertTrue(any(item.get("step_id") == "s3" and item.get("event_type") == "STEP_SQL_COMPILED" for item in emitted))

    def test_build_managed_skill_evidence_tables_executes_group_by_object(self):
        class DummySourceService:
            def __init__(self):
                self.graph_sql = []
                self.readonly_sql = []

            def execute_remote_graph_query(self, *, source_id, graph_sql, schema, row_limit):
                self.graph_sql.append(graph_sql)
                return {
                    "columns": ["OUTBOUND_NO", "DISTRIBUTOR_DISTRIBUTOR_NAME"],
                    "rows": [{"OUTBOUND_NO": "OUT-001", "DISTRIBUTOR_DISTRIBUTOR_NAME": "华东经销商"}],
                }

            def execute_remote_readonly_sql(self, *, source_id, query_sql, schema, row_limit):
                self.readonly_sql.append(query_sql)
                return {
                    "columns": ["METRIC_CODE", "DISTRIBUTOR_NAME", "METRIC_VALUE"],
                    "rows": [{"METRIC_CODE": "DISTRIBUTOR_LINK_COUNT", "DISTRIBUTOR_NAME": "华东经销商", "METRIC_VALUE": 3}],
                }

        self.service.source_service = DummySourceService()
        topology = {
            "graph_name": "PG_TRACE",
            "nodes": [
                {
                    "displayName": "OUTBOUNDORDER",
                    "tableName": "GYL.ONTO_NODE_OUTBOUNDORDER",
                    "properties": [{"property_name": "OUTBOUND_ID", "is_primary_key": "Y"}, {"property_name": "OUTBOUND_NO"}],
                },
                {
                    "displayName": "DISTRIBUTOR",
                    "tableName": "GYL.ONTO_NODE_DISTRIBUTOR",
                    "properties": [
                        {"property_name": "DISTRIBUTOR_ID", "is_primary_key": "Y"},
                        {"property_name": "DISTRIBUTOR_NAME"},
                    ],
                },
            ],
        }
        plan = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "select_root",
                    "label": "OUTBOUNDORDER",
                    "filter": {"property": "OUTBOUND_NO", "value": "OUT-001"},
                    "display_properties": ["OUTBOUND_NO"],
                },
                {
                    "step_id": "s2",
                    "action": "expand_relations",
                    "from_step": "s1",
                    "label": "DISTRIBUTOR",
                    "path": [{"target": "DISTRIBUTOR", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "display_properties": ["DISTRIBUTOR_NAME"],
                },
                {
                    "step_id": "s3",
                    "action": "group_by_object",
                    "label": "DISTRIBUTOR",
                    "root_label": "OUTBOUNDORDER",
                    "scope": "related_object",
                    "based_on_step": "s2",
                    "path": [{"target": "DISTRIBUTOR", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                    "group_properties": ["DISTRIBUTOR_NAME"],
                    "metric_code": "DISTRIBUTOR_LINK_COUNT",
                    "metric_name": "经销商关联数量",
                    "aggregation_method": "COUNT",
                    "column_name": "",
                },
            ],
        }
        emitted = []

        result = self.service._build_managed_skill_evidence_tables(
            plan=plan,
            topology=topology,
            source_id="src_1",
            schema="GYL",
            sample_limit=20,
            event_callback=emitted.append,
        )

        self.assertEqual(2, len(self.service.source_service.graph_sql))
        self.assertEqual(1, len(self.service.source_service.readonly_sql))
        self.assertIn("GROUP BY GROUP_DISTRIBUTOR_NAME", self.service.source_service.readonly_sql[0])
        self.assertEqual("OBJECT_GROUP_BY", result["evidence_tables"][-1]["kind"])
        self.assertEqual(["OUTBOUNDORDER", "DISTRIBUTOR"], result["evidence_tables"][-1]["related_objects"])
        self.assertTrue(any(item.get("step_id") == "s3" and item.get("event_type") == "STEP_EXECUTED" for item in emitted))


if __name__ == "__main__":
    unittest.main()
