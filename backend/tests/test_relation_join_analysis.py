import unittest

from app.api.mapping import _relation_join_candidates
from app.models.models import SysOntologyEntity, SysOntologyProperty, SysOntologyRelation


class RelationJoinAnalysisTests(unittest.TestCase):
    def test_bottle_product_prefers_product_id_not_primary_key(self):
        bottle = SysOntologyEntity(
            entity_id="bottle",
            entity_name="BottleCode",
            properties=[
                SysOntologyProperty(property_name="bottle_id", is_primary_key="Y"),
                SysOntologyProperty(property_name="product_id", is_primary_key="N"),
            ],
        )
        product = SysOntologyEntity(
            entity_id="product",
            entity_name="Product",
            properties=[SysOntologyProperty(property_name="product_id", is_primary_key="Y")],
        )
        relation = SysOntologyRelation(relation_name="归属")
        relation.source_entity = bottle
        relation.target_entity = product

        candidates = _relation_join_candidates(
            relation,
            "BOTTLE_CODE",
            "PRODUCT",
            {"BOTTLE_ID", "PRODUCT_ID"},
            {"PRODUCT_ID"},
        )

        self.assertEqual("src.PRODUCT_ID = dst.PRODUCT_ID", candidates[0]["join_condition"])
        self.assertNotIn("src.BOTTLE_ID = dst.PRODUCT_ID", [item["join_condition"] for item in candidates])

    def test_different_primary_keys_are_not_a_candidate(self):
        source = SysOntologyEntity(
            entity_name="Source",
            properties=[SysOntologyProperty(property_name="source_id", is_primary_key="Y")],
        )
        target = SysOntologyEntity(
            entity_name="Target",
            properties=[SysOntologyProperty(property_name="target_id", is_primary_key="Y")],
        )
        relation = SysOntologyRelation()
        relation.source_entity = source
        relation.target_entity = target

        candidates = _relation_join_candidates(relation, "SRC", "DST", {"SOURCE_ID"}, {"TARGET_ID"})

        self.assertEqual([], candidates)


if __name__ == "__main__":
    unittest.main()
