import unittest

from app.api.ontology_browse import _enrich_topology_display_names
from app.models.models import SysOntologyEntity, SysOntologyRelation


class OntologyBrowseDisplayNameTest(unittest.TestCase):
    def test_enriches_deployed_topology_with_chinese_ontology_names(self) -> None:
        entity = SysOntologyEntity(
            entity_id="ent_product",
            domain_id="dm_quality",
            entity_name="ProductUnit",
            entity_display_name="产品",
            table_name="ONTO_NODE_PRODUCTUNIT",
        )
        relation = SysOntologyRelation(
            relation_id="rel_test_product",
            domain_id="dm_quality",
            relation_name="属于产品",
            relation_type="ASSOCIATION",
            relation_table_name="ONTO_EDGE_TEST_PRODUCT",
        )
        topology = {
            "nodes": [{
                "name": "ONTO_NODE_PRODUCTUNIT",
                "displayName": "PRODUCTUNIT",
                "tableName": "QUALITY.ONTO_NODE_PRODUCTUNIT",
            }],
            "edges": [{
                "name": "TEST_PRODUCT",
                "relationTableName": "QUALITY.ONTO_EDGE_TEST_PRODUCT",
            }],
        }

        result = _enrich_topology_display_names(topology, [entity], [relation])

        self.assertEqual(result["nodes"][0]["displayName"], "产品")
        self.assertEqual(result["nodes"][0]["technicalName"], "PRODUCTUNIT")
        self.assertEqual(result["nodes"][0]["entityName"], "ProductUnit")
        self.assertEqual(result["edges"][0]["name"], "属于产品")
        self.assertEqual(result["edges"][0]["technicalName"], "TEST_PRODUCT")

    def test_keeps_oracle_label_when_no_platform_metadata_matches(self) -> None:
        topology = {
            "nodes": [{"name": "EXTERNAL", "displayName": "EXTERNAL", "tableName": "EXT_TABLE"}],
            "edges": [],
        }

        result = _enrich_topology_display_names(topology, [], [])

        self.assertEqual(result["nodes"][0]["displayName"], "EXTERNAL")
        self.assertNotIn("technicalName", result["nodes"][0])

    def test_matches_unbound_data_source_topology_using_deployed_table_name(self) -> None:
        entity = SysOntologyEntity(
            entity_id="ent_bottle_code",
            domain_id="dm_supply_chain",
            entity_name="BottleCode",
            entity_display_name="瓶码",
            table_name="ONTO_NODE_BOTTLECODE",
        )
        topology = {
            "nodes": [{
                "name": "ONTO_NODE_BOTTLECODE",
                "displayName": "BOTTLECODE",
                "tableName": "GYL.ONTO_NODE_BOTTLECODE",
            }],
            "edges": [],
        }

        result = _enrich_topology_display_names(topology, [entity], [])

        self.assertEqual(result["nodes"][0]["displayName"], "瓶码")


if __name__ == "__main__":
    unittest.main()
