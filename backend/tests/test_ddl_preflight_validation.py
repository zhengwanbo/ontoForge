import unittest
from types import SimpleNamespace

from app.services.ddl_service import DDLService


class DDLPreflightValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DDLService.__new__(DDLService)

    @staticmethod
    def _entity(entity_id: str, name: str, with_pk: bool):
        properties = [
            SimpleNamespace(
                property_name="product_id",
                is_primary_key="Y" if with_pk else "N",
                mapping=None,
            )
        ]
        return SimpleNamespace(
            entity_id=entity_id,
            entity_name=name,
            entity_display_name=name,
            properties=properties,
            entity_mapping=SimpleNamespace(view_sql="SELECT PRODUCT_ID FROM MD_PRODUCT"),
        )

    def test_returns_structured_entity_and_relation_fix_targets(self) -> None:
        source = self._entity("ent_source", "Source", with_pk=False)
        target = self._entity("ent_target", "Target", with_pk=True)
        relation = SimpleNamespace(
            relation_id="rel_belongs",
            relation_name="归属",
            relation_table_name="",
            source_entity_id=source.entity_id,
            target_entity_id=target.entity_id,
            relation_mapping=SimpleNamespace(mapping_mode="DIRECT", join_condition=""),
        )

        issues = self.service.validate_ddl_readiness([source, target], [relation])
        codes = {issue["code"] for issue in issues}

        self.assertIn("ENTITY_PRIMARY_KEY_MISSING", codes)
        self.assertIn("RELATION_EDGE_NAME_MISSING", codes)
        self.assertIn("RELATION_ENDPOINT_PRIMARY_KEY_MISSING", codes)
        relation_issue = next(issue for issue in issues if issue["code"] == "RELATION_EDGE_NAME_MISSING")
        self.assertEqual("/mapping/manage", relation_issue["navigate_to"]["path"])
        self.assertEqual("rel_belongs", relation_issue["navigate_to"]["query"]["relation_id"])
