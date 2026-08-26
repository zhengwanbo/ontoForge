import unittest
from types import SimpleNamespace

from app.services.ddl_service import DDLService
from app.services.llm_service import LLMService


class MultiSourceNodeMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.llm_service = LLMService.__new__(LLMService)
        self.ddl_service = DDLService.__new__(DDLService)

    def test_rejects_multi_source_node_sql_that_omits_confirmed_property(self) -> None:
        result = self.llm_service._normalize_ontology_property_graph_mapping(
            {
                "entityMappings": [{
                    "entityId": "tire",
                    "nodeTableName": "ONTO_NODE_TIRE",
                    "buildType": "VIEW",
                    "sourceTables": ["MES_PROCESS_EVENT", "MES_QUALITY_INSPECTION"],
                    "keyPropertyName": "tire_barcode",
                    "keyOutputColumn": "TIRE_BARCODE",
                    "nodeSql": "SELECT P.TIRE_BARCODE AS TIRE_BARCODE FROM MES_PROCESS_EVENT P",
                    "multiSourceJoinPlan": {
                        "anchorSourceTable": "MES_PROCESS_EVENT",
                        "anchorKeyColumn": "TIRE_BARCODE",
                        "joins": [{"sourceTable": "MES_QUALITY_INSPECTION"}],
                    },
                }],
            },
            ontology_entities=[{"entity_id": "tire", "entity_name": "Tire"}],
            ontology_relations=[],
            entity_mapping_results=[{
                "entity_id": "tire",
                "mappings": [
                    {"propertyName": "tire_barcode", "sourceTable": "MES_PROCESS_EVENT", "sourceColumn": "TIRE_BARCODE"},
                    {"propertyName": "inspection_id", "sourceTable": "MES_QUALITY_INSPECTION", "sourceColumn": "INSPECTION_ID"},
                ],
            }],
        )

        self.assertEqual([], result["entity_mappings"])
        self.assertEqual("NODE_SQL_PROPERTY_PROJECTION_INCOMPLETE", result["entity_mapping_issues"][0]["code"])
        self.assertEqual(["INSPECTION_ID"], result["entity_mapping_issues"][0]["missing_properties"])

    def test_detects_stale_multi_source_view_before_ddl_generation(self) -> None:
        def prop(name, table, column):
            return SimpleNamespace(
                property_name=name,
                mapping=SimpleNamespace(mapping_type="DIRECT", source_table=table, source_column=column, formula_expr=""),
            )

        entity = SimpleNamespace(
            properties=[
                prop("tire_barcode", "MES_PROCESS_EVENT", "TIRE_BARCODE"),
                prop("inspection_id", "MES_QUALITY_INSPECTION", "INSPECTION_ID"),
            ],
            entity_mapping=SimpleNamespace(
                view_sql="SELECT P.TIRE_BARCODE AS TIRE_BARCODE FROM MES_PROCESS_EVENT P"
            ),
        )

        self.assertEqual(["INSPECTION_ID"], self.ddl_service._get_multi_source_node_projection_gaps(entity))

