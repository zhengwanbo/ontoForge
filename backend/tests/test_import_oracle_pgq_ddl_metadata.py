import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_oracle_pgq_ddl_metadata.py"
DDL_PATH = Path(__file__).resolve().parents[2] / "database" / "oracle" / "jylt" / "ontology_view_pgq.sql"


spec = importlib.util.spec_from_file_location("import_oracle_pgq_ddl_metadata", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ImportOraclePgqDdlMetadataTest(unittest.TestCase):
    def test_build_import_summary_for_jylt_ddl(self) -> None:
        summary = module.build_import_summary(DDL_PATH.read_text(encoding="utf-8"), DDL_PATH)

        self.assertEqual("TIRE_PROCESS_GRAPH", summary["graph_name"])
        self.assertEqual(10, summary["entity_count"])
        self.assertEqual(10, len(summary["entities"]))
        self.assertEqual(11, summary["relation_count"])
        self.assertEqual(11, len(summary["relations"]))

    def test_summary_contains_production_process_entity_and_mapping(self) -> None:
        summary = module.build_import_summary(DDL_PATH.read_text(encoding="utf-8"), DDL_PATH)
        entity = next(item for item in summary["entities"] if item["entity_name"] == "ProductionProcess")

        self.assertEqual("生产过程", entity["entity_display_name"])
        self.assertEqual("ONTO_NODE_PRODUCTION_PROCESS", entity["table_name"])
        self.assertEqual("VIEW", entity["build_type"])
        self.assertIn("MV_V_PRODUCTION_PROCESS", entity["entity_mapping"]["view_sql"])
        process_id = next(prop for prop in entity["properties"] if prop["property_name"] == "process_id")
        self.assertEqual("Y", process_id["is_primary_key"])
        self.assertEqual("ONTO_NODE_PRODUCTION_PROCESS", process_id["mapping"]["source_table"])
        self.assertEqual("PROCESS_ID", process_id["mapping"]["source_column"])

    def test_summary_contains_instance_of_relation_and_edge_sql(self) -> None:
        summary = module.build_import_summary(DDL_PATH.read_text(encoding="utf-8"), DDL_PATH)
        relation = next(item for item in summary["relations"] if item["relation_name"] == "InstanceOf")

        self.assertEqual("ProcessStep", relation["source_entity_name"])
        self.assertEqual("ProductionProcess", relation["target_entity_name"])
        self.assertEqual("ONTO_EDGE_INSTANCE_OF", relation["relation_table_name"])
        self.assertEqual("MANY_TO_ONE", relation["mapping"]["relation_cardinality"])
        self.assertIn("FROM ONTO_NODE_PROCESS_STEP", relation["mapping"]["edge_sql"])


if __name__ == "__main__":
    unittest.main()
