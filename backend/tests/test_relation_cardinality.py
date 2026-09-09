import unittest
from unittest.mock import patch

from app.api.ddl import _effective_relation_mapping
from app.api.mapping import _normalize_relation_cardinality
from app.models.models import SysOntologyRelation
from app.services.llm_service import LLMService


class RelationCardinalityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.llm_service = LLMService.__new__(LLMService)

    def test_mapping_normalize_relation_cardinality_accepts_alias_and_fallback(self) -> None:
        self.assertEqual("ONE_TO_MANY", _normalize_relation_cardinality("one_to_many"))
        self.assertEqual("MANY_TO_ONE", _normalize_relation_cardinality("n_to_1"))
        self.assertEqual("ONE_TO_ONE", _normalize_relation_cardinality(None, fallback="ONE_TO_ONE"))
        self.assertIsNone(_normalize_relation_cardinality("association"))

    def test_llm_normalize_relation_mapping_preserves_relation_cardinality(self) -> None:
        normalized = self.llm_service._normalize_ontology_property_graph_mapping(
            {
                "relationMappings": [{
                    "relationId": "rel_order_store",
                    "relationCardinality": "many_to_one",
                    "joinCondition": "src.STORE_ID = dst.STORE_ID",
                    "edgeSql": "SELECT 1 AS EDGE_ID, 1 AS SOURCE_ID, 2 AS TARGET_ID FROM DUAL",
                }],
            },
            ontology_entities=[
                {
                    "entity_id": "order",
                    "entity_name": "Order",
                    "properties": [{"property_name": "store_id", "is_primary_key": False}],
                },
                {
                    "entity_id": "store",
                    "entity_name": "Store",
                    "properties": [{"property_name": "store_id", "is_primary_key": True}],
                },
            ],
            ontology_relations=[{
                "relation_id": "rel_order_store",
                "relation_name": "下单门店",
                "relation_type": "MANY_TO_ONE",
                "source_entity_id": "order",
                "target_entity_id": "store",
            }],
            entity_mapping_results=[
                {"entity_id": "order", "mappings": [{"propertyName": "store_id", "sourceColumn": "STORE_ID"}]},
                {"entity_id": "store", "mappings": [{"propertyName": "store_id", "sourceColumn": "STORE_ID"}]},
            ],
        )

        self.assertEqual("MANY_TO_ONE", normalized["relation_mappings"][0]["relation_cardinality"])

    def test_ddl_effective_relation_mapping_surfaces_recommended_cardinality(self) -> None:
        relation = SysOntologyRelation(
            relation_id="rel_order_store",
            domain_id="dm_sales",
            source_entity_id="order",
            target_entity_id="store",
            relation_name="下单门店",
            relation_type="MANY_TO_ONE",
        )

        with patch(
            "app.api.ddl._find_latest_relation_task_recommendation",
            return_value={
                "relation_cardinality": "MANY_TO_ONE",
                "source_table": "ONTO_NODE_ORDER",
                "target_table": "ONTO_NODE_STORE",
                "join_condition": "src.STORE_ID = dst.STORE_ID",
            },
        ):
            effective = _effective_relation_mapping(None, relation)

        self.assertEqual("MANY_TO_ONE", effective["relation_cardinality"])


if __name__ == "__main__":
    unittest.main()
