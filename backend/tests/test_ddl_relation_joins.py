import unittest

from app.models.models import (
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
    SysRelationMapping,
)
from app.services.ddl_service import DDLService


class _CountCursor:
    def __init__(self, count):
        self.count = count
        self.statement = ""

    def execute(self, statement):
        self.statement = statement

    def fetchone(self):
        return (self.count,)


class RelationJoinGenerationTests(unittest.TestCase):
    def setUp(self):
        self.service = DDLService(db=None)
        self.bottle = SysOntologyEntity(
            entity_id="bottle",
            entity_name="BottleCode",
            table_name="ONTO_NODE_BOTTLECODE",
            properties=[
                SysOntologyProperty(property_name="bottle_id", is_primary_key="Y"),
                SysOntologyProperty(property_name="product_id", is_primary_key="N"),
            ],
        )
        self.product = SysOntologyEntity(
            entity_id="product",
            entity_name="Product",
            table_name="ONTO_NODE_PRODUCT",
            properties=[SysOntologyProperty(property_name="product_id", is_primary_key="Y")],
        )

    def test_supply_chain_bottle_product_uses_product_id_join(self):
        relation = SysOntologyRelation(
            relation_id="belongs",
            source_entity_id="bottle",
            target_entity_id="product",
            relation_name="归属",
            relation_table_name="ONTO_EDGE_BOTTLE_PRODUCT",
        )

        ddl = self.service._generate_relation_table_ddl(relation, [self.bottle, self.product])

        self.assertIn("JOIN ONTO_NODE_PRODUCT dst ON src.PRODUCT_ID = dst.PRODUCT_ID", ddl)
        self.assertIn("src.BOTTLE_ID AS SOURCE_ID", ddl)
        self.assertNotIn("src.BOTTLE_ID = dst.PRODUCT_ID", ddl)

    def test_missing_join_never_falls_back_to_two_primary_keys(self):
        source = SysOntologyEntity(
            entity_id="source",
            entity_name="Source",
            properties=[SysOntologyProperty(property_name="source_id", is_primary_key="Y")],
        )
        target = SysOntologyEntity(
            entity_id="target",
            entity_name="Target",
            properties=[SysOntologyProperty(property_name="target_id", is_primary_key="Y")],
        )
        relation = SysOntologyRelation(source_entity_id="source", target_entity_id="target")

        self.assertIsNone(self.service._build_relation_join_condition(relation, source, target, "SOURCE_ID", "TARGET_ID"))

    def test_preflight_counts_generated_edge_join(self):
        cursor = _CountCursor(5)
        statement = """CREATE TABLE ONTO_EDGE_BOTTLE_PRODUCT AS
SELECT src.BOTTLE_ID AS SOURCE_ID, dst.PRODUCT_ID AS TARGET_ID
FROM ONTO_NODE_BOTTLECODE src
JOIN ONTO_NODE_PRODUCT dst ON src.PRODUCT_ID = dst.PRODUCT_ID"""

        result = self.service._preflight_edge_join(cursor, statement)

        self.assertEqual({"edge_table": "ONTO_EDGE_BOTTLE_PRODUCT", "matched_count": 5}, result)
        self.assertIn("src.PRODUCT_ID = dst.PRODUCT_ID", cursor.statement)

    def test_relation_table_mode_builds_three_table_edge_join(self):
        relation = SysOntologyRelation(
            relation_id="packed",
            source_entity_id="bottle",
            target_entity_id="product",
            relation_name="装入",
            relation_table_name="ONTO_EDGE_BOTTLE_PACK",
        )
        relation.relation_mapping = SysRelationMapping(
            mapping_mode="RELATION_TABLE",
            relation_table="BOTTLE_PACK_RELATION",
            relation_source_column="BOTTLE_ID",
            relation_target_column="PACK_ID",
        )
        pack = SysOntologyEntity(
            entity_id="product",
            entity_name="PackCode",
            table_name="ONTO_NODE_PACKCODE",
            properties=[SysOntologyProperty(property_name="pack_id", is_primary_key="Y")],
        )

        ddl = self.service._generate_relation_table_ddl(relation, [self.bottle, pack])

        self.assertIn("FROM BOTTLE_PACK_RELATION rel", ddl)
        self.assertIn("JOIN ONTO_NODE_BOTTLECODE src ON src.BOTTLE_ID = rel.BOTTLE_ID", ddl)
        self.assertIn("JOIN ONTO_NODE_PACKCODE dst ON rel.PACK_ID = dst.PACK_ID", ddl)

    def test_preflight_counts_relation_table_edge_join(self):
        cursor = _CountCursor(2)
        statement = """CREATE TABLE ONTO_EDGE_BOTTLE_PACK AS
SELECT src.BOTTLE_ID AS SOURCE_ID, dst.PACK_ID AS TARGET_ID
FROM BOTTLE_PACK_RELATION rel
JOIN ONTO_NODE_BOTTLECODE src ON src.BOTTLE_ID = rel.BOTTLE_ID
JOIN ONTO_NODE_PACKCODE dst ON rel.PACK_ID = dst.PACK_ID"""

        result = self.service._preflight_edge_join(cursor, statement)

        self.assertEqual({"edge_table": "ONTO_EDGE_BOTTLE_PACK", "matched_count": 2}, result)
        self.assertIn("FROM BOTTLE_PACK_RELATION rel", cursor.statement)


if __name__ == "__main__":
    unittest.main()
