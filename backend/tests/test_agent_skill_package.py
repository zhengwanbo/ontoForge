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


if __name__ == "__main__":
    unittest.main()
