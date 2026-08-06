import unittest

from app.api.mapping import (
    _annotate_oracle_vertex_mapping,
    _build_bulk_relation_mapping_result,
    _build_oracle_edge_sql,
    _merge_holistic_node_design,
)
from app.models.models import SysOntologyEntity, SysOntologyProperty, SysOntologyRelation
from app.schemas.schemas import BulkMappingApplyRequest


class MappingOracleGraphTest(unittest.TestCase):
    def test_vertex_mapping_marks_ontology_primary_property_as_key(self):
        entity = SysOntologyEntity(
            entity_id="entity_work_order",
            entity_name="WorkOrder",
            entity_display_name="工单",
            build_type="VIEW",
        )
        entity.properties = [
            SysOntologyProperty(
                property_id="property_work_order_id",
                property_name="work_order_id",
                property_display_name="工单号",
                is_primary_key="Y",
            ),
            SysOntologyProperty(
                property_id="property_status",
                property_name="status",
                property_display_name="状态",
                is_primary_key="N",
            ),
        ]
        mappings = [
            {
                "propertyName": "status",
                "matchedPropertyId": "property_status",
                "sourceTable": "WORK_ORDER",
                "sourceColumn": "STATUS",
            },
            {
                "propertyName": "work_order_id",
                "matchedPropertyId": "property_work_order_id",
                "sourceTable": "WORK_ORDER",
                "sourceColumn": "WORK_ORDER_ID",
                "sourceDataType": "VARCHAR2",
            },
        ]

        annotated, vertex = _annotate_oracle_vertex_mapping(entity, mappings)

        self.assertFalse(annotated[0]["is_vertex_key"])
        self.assertTrue(annotated[1]["is_vertex_key"])
        self.assertEqual(vertex["vertex_table"], "ONTO_WORKORDER_V")
        self.assertEqual(vertex["key_property"], "work_order_id")
        self.assertEqual(vertex["key_source_column"], "WORK_ORDER_ID")
        self.assertEqual(len(vertex["properties"]), 2)
        self.assertTrue(vertex["properties"][1]["is_vertex_key"])
        self.assertTrue(vertex["oracle_graph_ready"])

    def test_edge_sql_exposes_oracle_property_graph_key_columns(self):
        sql = _build_oracle_edge_sql(
            relation_id="rel_has_defect",
            source_table="WORK_ORDER",
            target_table="DEFECT",
            source_key_column="WORK_ORDER_ID",
            target_key_column="DEFECT_ID",
            join_condition="src.WORK_ORDER_ID = dst.WORK_ORDER_ID",
        )

        self.assertIn("AS EDGE_ID", sql)
        self.assertIn("AS SOURCE_ID", sql)
        self.assertIn("AS TARGET_ID", sql)
        self.assertIn(
            "JOIN DEFECT dst ON src.WORK_ORDER_ID = dst.WORK_ORDER_ID",
            sql,
        )
        self.assertIn("SELECT DISTINCT", sql)

    def test_relation_design_connects_ontology_vertex_tables_by_primary_keys(self):
        source = SysOntologyEntity(entity_id="entity_work_order", entity_name="WorkOrder")
        target = SysOntologyEntity(entity_id="entity_defect", entity_name="Defect")
        relation = SysOntologyRelation(
            relation_id="rel_has_defect",
            relation_name="HAS_DEFECT",
            source_entity_id=source.entity_id,
            target_entity_id=target.entity_id,
        )
        relation.source_entity = source
        relation.target_entity = target
        result = _build_bulk_relation_mapping_result(
            db=None,
            relation=relation,
            blueprint_payload=None,
            entity_results_by_id={
                source.entity_id: {"oracle_vertex": {"vertex_table": "ONTO_WORK_ORDER", "key_property": "WORK_ORDER_ID"}},
                target.entity_id: {"oracle_vertex": {"vertex_table": "ONTO_DEFECT", "key_property": "DEFECT_ID"}},
            },
        )

        self.assertEqual(result["oracle_edge"]["source_vertex_table"], "ONTO_WORK_ORDER")
        self.assertEqual(result["oracle_edge"]["source_vertex_key_property"], "WORK_ORDER_ID")
        self.assertEqual(result["oracle_edge"]["target_vertex_table"], "ONTO_DEFECT")
        self.assertEqual(result["oracle_edge"]["target_vertex_key_property"], "DEFECT_ID")
        self.assertNotIn("edge_sql", result)
        self.assertNotIn("source_table", result)

    def test_bulk_apply_request_accepts_vertex_and_edge_payloads(self):
        request = BulkMappingApplyRequest.model_validate({
            "entities": [{
                "entity_id": "entity_work_order",
                "build_type": "TABLE",
                "table_name": "ONTO_WORK_ORDER",
                "view_sql": "SELECT WORK_ORDER_ID FROM WORK_ORDER",
                "mappings": [{"property_name": "work_order_id"}],
            }],
            "relations": [{
                "relation_id": "rel_has_defect",
                "edge_table_name": "ONTO_EDGE_HAS_DEFECT",
                "source_table": "WORK_ORDER",
                "target_table": "DEFECT",
                "join_condition": "src.WORK_ORDER_ID = dst.WORK_ORDER_ID",
                "edge_sql": "SELECT 1 AS EDGE_ID, 1 AS SOURCE_ID, 2 AS TARGET_ID FROM DUAL",
            }],
        })

        self.assertEqual(len(request.entities), 1)
        self.assertEqual(len(request.relations), 1)
        self.assertEqual(request.relations[0].relation_id, "rel_has_defect")
        self.assertEqual(request.entities[0].table_name, "ONTO_WORK_ORDER")
        self.assertEqual(request.relations[0].edge_table_name, "ONTO_EDGE_HAS_DEFECT")

    def test_holistic_node_design_replaces_inferred_vertex_key_and_table(self):
        merged = _merge_holistic_node_design(
            {
                "entity_id": "entity_work_order",
                "mappings": [
                    {"propertyName": "status", "is_vertex_key": True},
                    {"propertyName": "work_order_id", "is_vertex_key": False},
                ],
                "oracle_vertex": {
                    "vertex_table": "ONTO_OLD",
                    "key_property": "status",
                    "properties": [
                        {"property_name": "status", "is_vertex_key": True},
                        {"property_name": "work_order_id", "is_vertex_key": False},
                    ],
                },
            },
            {
                "node_table_name": "ONTO_WORK_ORDER",
                "build_type": "TABLE",
                "key_property_name": "work_order_id",
                "key_output_column": "WORK_ORDER_ID",
                "source_tables": ["WORK_ORDER"],
                "node_sql": "SELECT WORK_ORDER_ID, STATUS FROM WORK_ORDER",
                "design_reason": "按完整本体确定工单节点",
            },
        )

        self.assertEqual(merged["oracle_vertex"]["vertex_table"], "ONTO_WORK_ORDER")
        self.assertEqual(merged["oracle_vertex"]["key_property"], "work_order_id")
        self.assertFalse(merged["mappings"][0]["is_vertex_key"])
        self.assertTrue(merged["mappings"][1]["is_vertex_key"])
        self.assertIn("SELECT WORK_ORDER_ID", merged["node_mapping"]["node_sql"])


if __name__ == "__main__":
    unittest.main()
