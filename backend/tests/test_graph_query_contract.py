import unittest

from app.services.source_data_service import SourceDataService


class GraphQueryContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = {
            "graph_name": "TIRE_GRAPH",
            "vertices": [
                {"label": "TIRE", "properties": [{"name": "TIRE_BARCODE"}]},
                {"label": "PROCESSEVENT", "properties": [{"name": "PROCESS_EVENT_ID"}]},
                {"label": "PROCESSSTEP", "properties": [{"name": "STEP_NAME"}]},
                {"label": "EQUIPMENT", "properties": [{"name": "EQUIPMENT_NAME"}]},
            ],
            "edges": [
                {"edge_element": "TIRE_EVENT", "edge_label": "TIRE_EVENT", "source_label": "TIRE", "target_label": "PROCESSEVENT", "row_count": 10},
                {"edge_element": "EVENT_STEP", "edge_label": "EVENT_STEP", "source_label": "PROCESSEVENT", "target_label": "PROCESSSTEP", "row_count": 10},
                {"edge_element": "EVENT_EQUIPMENT", "edge_label": "EVENT_EQUIPMENT", "source_label": "PROCESSEVENT", "target_label": "EQUIPMENT", "row_count": 10},
            ],
        }

    def test_shared_event_paths_compile_to_multiple_match_patterns(self) -> None:
        result = SourceDataService.compile_property_graph_query_plan(self.contract, {
            "id": "tire-trace", "title": "轮胎追溯", "description": "追溯事件工序设备",
            "anchor_var": "t", "target_var": "pe",
            "patterns": [
                {"source_var": "t", "source_label": "TIRE", "edge_element": "TIRE_EVENT", "target_var": "pe", "target_label": "PROCESSEVENT"},
                {"source_var": "pe", "source_label": "PROCESSEVENT", "edge_element": "EVENT_STEP", "target_var": "st", "target_label": "PROCESSSTEP"},
                {"source_var": "pe", "source_label": "PROCESSEVENT", "edge_element": "EVENT_EQUIPMENT", "target_var": "eq", "target_label": "EQUIPMENT"},
            ],
            "properties": [{"var": "st", "property": "STEP_NAME"}],
        })
        self.assertIn("(pe IS PROCESSEVENT)-[e2 IS EVENT_STEP]->(st IS PROCESSSTEP)", result["sql"])
        self.assertIn("(pe IS PROCESSEVENT)-[e3 IS EVENT_EQUIPMENT]->(eq IS EQUIPMENT)", result["sql"])

    def test_nonexistent_downstream_chain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在关系路径"):
            SourceDataService.compile_property_graph_query_plan(self.contract, {
                "title": "错误路径", "description": "不应通过", "anchor_var": "t", "target_var": "eq",
                "patterns": [
                    {"source_var": "t", "source_label": "TIRE", "edge_element": "TIRE_EVENT", "target_var": "pe", "target_label": "PROCESSEVENT"},
                    {"source_var": "pe", "source_label": "PROCESSEVENT", "edge_element": "EVENT_STEP", "target_var": "st", "target_label": "PROCESSSTEP"},
                    {"source_var": "st", "source_label": "PROCESSSTEP", "edge_element": "EVENT_EQUIPMENT", "target_var": "eq", "target_label": "EQUIPMENT"},
                ],
            })
