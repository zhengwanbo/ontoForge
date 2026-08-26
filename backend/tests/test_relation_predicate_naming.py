import unittest

from app.services.ontology_guide_service import OntologyGuideService


class RelationPredicateNamingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = OntologyGuideService.__new__(OntologyGuideService)

    def test_explicit_edge_name_has_highest_priority(self) -> None:
        name = self.service._resolve_blueprint_relation_table_name(
            {"relationTableName": "onto_edge_manages", "relationName": "管理", "relationPredicate": "MANAGES"},
            "Equipment",
            "Workshop",
            "ASSOCIATION",
        )
        self.assertEqual("ONTO_EDGE_MANAGES", name)

    def test_dictionary_precedes_llm_predicate(self) -> None:
        name = self.service._resolve_blueprint_relation_table_name(
            {"relationName": "属于", "relationPredicate": "CUSTOM_PREDICATE"},
            "BottleCode",
            "Product",
            "ASSOCIATION",
        )
        self.assertEqual("ONTO_EDGE_BOTTLECODE_BELONGS_TO_PRODUCT", name)

    def test_llm_predicate_and_fallback_do_not_repeat_endpoints(self) -> None:
        llm_name = self.service._resolve_blueprint_relation_table_name(
            {"relationName": "调拨至", "relationPredicate": "TRANSFERS_TO"},
            "OutboundOrder",
            "Warehouse",
            "ASSOCIATION",
        )
        fallback_name = self.service._resolve_blueprint_relation_table_name(
            {"relationName": "调拨至"},
            "OutboundOrder",
            "Warehouse",
            "ASSOCIATION",
        )

        self.assertEqual("ONTO_EDGE_OUTBOUNDORDER_TRANSFERS_TO_WAREHOUSE", llm_name)
        self.assertEqual("ONTO_EDGE_OUTBOUNDORDER_RELATED_TO_WAREHOUSE", fallback_name)

