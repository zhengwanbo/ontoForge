import unittest

from app.models.models import SysDataSource
from app.services.source_data_service import SourceDataService


class PropertyGraphTopologyTests(unittest.TestCase):
    def test_topology_comes_from_graph_elements_and_edge_relationships(self):
        source = SysDataSource(source_id="src_1", source_name="目标库")
        result = SourceDataService._assemble_property_graph_topology(
            source=source,
            owner="OONBUILD",
            graph_names=["QUALITY_GRAPH", "OTHER_GRAPH"],
            graph_name="QUALITY_GRAPH",
            element_rows=[
                ("PRODUCT", "VERTEX", "OONBUILD", "ONTO_NODE_PRODUCT", "TABLE", "产品节点"),
                ("ALARM", "VERTEX", "OONBUILD", "ONTO_NODE_ALARM", "VIEW", "报警节点"),
                ("HAS_ALARM", "EDGE", "OONBUILD", "ONTO_EDGE_HAS_ALARM", "TABLE", None),
            ],
            label_rows=[("PRODUCT", "ProductUnit"), ("ALARM", "AlarmEvent"), ("HAS_ALARM", "关联报警")],
            key_rows=[("PRODUCT", "VCM_ID"), ("ALARM", "ALARM_EVENT_ID")],
            column_rows=[
                ("PRODUCT", "VCM_ID", "VARCHAR2(50)", "N", 1, "产品标识"),
                ("ALARM", "ALARM_EVENT_ID", "VARCHAR2(80)", "N", 1, "报警标识"),
            ],
            edge_rows=[
                ("HAS_ALARM", "PRODUCT", "SOURCE", "SOURCE_ID", "VCM_ID"),
                ("HAS_ALARM", "ALARM", "DESTINATION", "TARGET_ID", "ALARM_EVENT_ID"),
            ],
        )

        self.assertEqual([item["graph_name"] for item in result["graphs"]], ["QUALITY_GRAPH", "OTHER_GRAPH"])
        self.assertEqual(len(result["nodes"]), 2)
        nodes_by_name = {item["name"]: item for item in result["nodes"]}
        self.assertEqual(nodes_by_name["ALARM"]["displayName"], "AlarmEvent")
        self.assertEqual(nodes_by_name["PRODUCT"]["properties"][0]["is_primary_key"], "Y")
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["edges"][0]["name"], "关联报警")
        self.assertTrue(result["edges"][0]["source"].endswith(":VERTEX:PRODUCT"))
        self.assertTrue(result["edges"][0]["target"].endswith(":VERTEX:ALARM"))

    def test_composite_edge_keys_still_create_one_visual_edge(self):
        source = SysDataSource(source_id="src_1", source_name="目标库")
        result = SourceDataService._assemble_property_graph_topology(
            source=source,
            owner="OONBUILD",
            graph_names=["QUALITY_GRAPH"],
            graph_name="QUALITY_GRAPH",
            element_rows=[
                ("A", "VERTEX", "OONBUILD", "A", "TABLE", None),
                ("B", "VERTEX", "OONBUILD", "B", "TABLE", None),
                ("AB", "EDGE", "OONBUILD", "AB", "TABLE", None),
            ],
            label_rows=[],
            key_rows=[],
            column_rows=[],
            edge_rows=[
                ("AB", "A", "SOURCE", "A_1", "ID_1"),
                ("AB", "A", "SOURCE", "A_2", "ID_2"),
                ("AB", "B", "DESTINATION", "B_1", "ID_1"),
                ("AB", "B", "DESTINATION", "B_2", "ID_2"),
            ],
        )

        self.assertEqual(len(result["edges"]), 1)
        self.assertIn("A_1", result["edges"][0]["desc"])
        self.assertIn("B_2", result["edges"][0]["desc"])
