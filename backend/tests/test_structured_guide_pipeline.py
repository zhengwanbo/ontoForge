import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.models import SysDomain, SysOntologyEntity, SysOntologyProperty
from app.services.ddl_service import DDLService
from app.services.domain_ontology_generators import build_canonical_model, build_view_plan
from app.services.ontology_guide_service import OntologyGuideService


class StructuredGuidePipelineTest(unittest.TestCase):
    def test_tams_sfr_generator_builds_stable_canonical_model_and_view_plan(self) -> None:
        analysis_context = {
            "focus_scope": {
                "focus_metric_families": ["DARK-B", "SFRMACRO", "SFRSUPERMACRO"],
                "focus_stations": ["ST24105_LBI", "ST21160_AA_INLINE_AA", "ST21A30_CUBE_FTU"],
            },
            "schema_analysis": {
                "key_tables": {
                    "product_index_table": "PDX25_TAMS_UNIT",
                    "process_table": "PDX25_TAMS_PROCESS",
                    "test_tables": [
                        "PDX25_TAMS_CUBE_FTD_DARK_B",
                        "PDX25_TAMS_CUBE_FTU_SFRMACRO",
                    ],
                    "rule_tables": ["TAMS_SPEC_LIMIT"],
                    "aa_feature_tables": ["PDX25_TAMS_ACTIVE_ALIGNMENT_C_LOG"],
                    "alarm_tables": ["PDX25_TAMS_ALARM"],
                    "history_case_tables": ["PDX25_TAMS_SFR_FACA_CASE"],
                },
            },
            "rule_analysis": {
                "primary_metric_families": ["DARK-B", "SFRMACRO", "SFRSUPERMACRO"],
                "family_stats": [
                    {
                        "family_name": "DARK-B",
                        "metric_examples": ["cen_avg_1", "edge_min_1", "LR_Edge_Delta"],
                    },
                    {
                        "family_name": "SFRMACRO",
                        "metric_examples": ["30F_min_a", "60F_min_b", "FPDC_Coefficient_1"],
                    },
                ],
            },
            "metric_semantics": {
                "semantic_categories": [
                    {"semantic_label": "中心解析力偏低"},
                    {"semantic_label": "边缘解析力偏低"},
                ],
            },
            "selected_table_schema": {
                "tables": [
                    {
                        "table_name": "PDX25_TAMS_UNIT",
                        "columns": [
                            {"column_name": "VCM_ID"},
                            {"column_name": "MODULE_ID"},
                            {"column_name": "LOT"},
                            {"column_name": "MODEL"},
                            {"column_name": "CONFIG"},
                            {"column_name": "SENSOR_ID"},
                            {"column_name": "LENS_ID"},
                            {"column_name": "FLEX_ID"},
                            {"column_name": "DEFECT_CODE"},
                        ],
                    },
                    {
                        "table_name": "PDX25_TAMS_CUBE_FTD_DARK_B",
                        "columns": [
                            {"column_name": "VCM_ID"},
                            {"column_name": "PASS_FAIL_DESCRIPTION"},
                            {"column_name": "SOCKETID"},
                            {"column_name": "DCKEY"},
                        ],
                    },
                    {
                        "table_name": "TAMS_SPEC_LIMIT",
                        "columns": [
                            {"column_name": "SPEC_FAMILY"},
                            {"column_name": "DB_NAME"},
                            {"column_name": "LSL"},
                            {"column_name": "USL"},
                        ],
                    },
                    {
                        "table_name": "PDX25_TAMS_PROCESS",
                        "columns": [
                            {"column_name": "VCM_ID"},
                            {"column_name": "ST24105_LBI_MC_ID"},
                            {"column_name": "ST24105_LBI_INPUT_TIME"},
                            {"column_name": "ST21160_AA_INLINE_AA_TOOLING"},
                        ],
                    },
                    {
                        "table_name": "PDX25_TAMS_ACTIVE_ALIGNMENT_C_LOG",
                        "columns": [{"column_name": "VCM_ID"}],
                    },
                    {
                        "table_name": "PDX25_TAMS_ALARM",
                        "columns": [{"column_name": "ALARM_ID"}],
                    },
                    {
                        "table_name": "PDX25_TAMS_SFR_FACA_CASE",
                        "columns": [{"column_name": "CASE_ID"}],
                    },
                ],
            },
        }

        canonical_model = build_canonical_model(analysis_context)
        view_plan = build_view_plan(analysis_context, canonical_model)

        entity_names = {item["entityName"] for item in canonical_model["entities"]}
        relation_names = {item["relationName"] for item in canonical_model["relations"]}
        view_names = {item["view_name"] for item in view_plan["standardized_views"]}

        self.assertIn("ProductUnit", entity_names)
        self.assertIn("TestRun", entity_names)
        self.assertIn("MetricSpec", entity_names)
        self.assertIn("ProcessEvent", entity_names)
        self.assertIn("AALogFeature", entity_names)
        self.assertIn("HistoricalCase", entity_names)
        self.assertIn("有测试", relation_names)
        self.assertIn("对照", relation_names)
        self.assertIn("支持", relation_names)
        self.assertIn("V_UNIT_BASE", view_names)
        self.assertIn("V_PROCESS_EVENT", view_names)
        self.assertIn("V_METRIC_RESULT", view_names)
        self.assertIn("V_METRIC_SPEC", view_names)
        alarm_entity = next(item for item in canonical_model["entities"] if item["entityName"] == "AlarmEvent")
        self.assertIn("product_unit_id", {prop["propertyName"] for prop in alarm_entity["properties"]})
        process_entity = next(item for item in canonical_model["entities"] if item["entityName"] == "ProcessEvent")
        self.assertTrue({"equipment_code", "material_lot_id"}.issubset({prop["propertyName"] for prop in process_entity["properties"]}))
        self.assertEqual(
            view_plan["graph_layer"]["focus_metric_families"],
            ["DARK-B", "SFRMACRO", "SFRSUPERMACRO"],
        )

    def test_ddl_service_prefers_view_plan_for_semantic_layer_generation(self) -> None:
        ddl_service = DDLService.__new__(DDLService)
        ddl_service.db = SimpleNamespace()

        entity = SysOntologyEntity(
            entity_id="ent_product",
            domain_id="dm_structured",
            entity_name="ProductUnit",
            entity_display_name="产品",
            build_type="VIEW",
            table_name="ONTO_NODE_PRODUCTUNIT_V",
        )
        entity.properties = [
            SysOntologyProperty(
                property_id="prop_vcm",
                entity_id="ent_product",
                property_name="vcm_id",
                data_type="VARCHAR2(128)",
                is_primary_key="Y",
            ),
        ]

        blueprint_package = {
            "view_plan": {
                "standardized_views": [
                    {
                        "view_name": "V_UNIT_BASE",
                        "view_kind": "standardized",
                        "source_role": "standardized",
                        "source_tables": ["PDX25_TAMS_UNIT"],
                        "purpose": "产品主索引标准化视图",
                        "deploy": True,
                        "deploy_reason": "首期正式部署",
                        "sql": "SELECT VCM_ID, MODEL FROM PDX25_TAMS_UNIT",
                        "sql_confirmed": True,
                    },
                    {
                        "view_name": "V_AA_FEATURE",
                        "view_kind": "standardized",
                        "source_role": "standardized",
                        "source_tables": ["PDX25_TAMS_ACTIVE_ALIGNMENT_C_LOG"],
                        "purpose": "AA 特征",
                        "deploy": False,
                        "deploy_reason": "暂不部署",
                        "sql": "SELECT VCM_ID FROM PDX25_TAMS_ACTIVE_ALIGNMENT_C_LOG",
                    },
                ],
                "edge_views": [
                    {
                        "view_name": "VW_E_PRODUCT_TEST",
                        "source_views": ["V_UNIT_BASE", "V_TEST_RUN"],
                        "purpose": "产品到测试事件",
                        "deploy": True,
                        "deploy_reason": "首期部署",
                        "sql": "SELECT 'E1' AS EDGE_ID, 'S1' AS SOURCE_ID, 'T1' AS TARGET_ID FROM dual",
                        "sql_confirmed": True,
                    },
                ],
                "graph_layer": {
                    "graph_name": "STRUCTURED_PG",
                    "vertex_entities": ["ProductUnit"],
                    "edge_relations": ["有测试"],
                    "note": "结构化属性图",
                },
            },
            "deployment_design": {
                "semantic_views": [
                    {
                        "view_name": "OLD_VIEW",
                        "deploy": True,
                        "sql": "SELECT 1 FROM dual",
                    },
                ],
            },
        }

        semantic_statements = ddl_service._generate_semantic_layer_ddl(
            domain=SysDomain(domain_id="dm_structured", domain_name="Structured Domain"),
            blueprint_package=blueprint_package,
            entities=[entity],
            relations=[],
        )

        statement_sql = "\n".join(item["sql"] for item in semantic_statements)
        statement_names = {item["name"] for item in semantic_statements}

        self.assertIn("CREATE OR REPLACE VIEW V_UNIT_BASE AS", statement_sql)
        self.assertIn("CREATE OR REPLACE VIEW VW_E_PRODUCT_TEST AS", statement_sql)
        self.assertNotIn("OLD_VIEW", statement_sql)
        self.assertNotIn("V_AA_FEATURE", statement_sql)
        self.assertIn("CREATE OR REPLACE PROPERTY GRAPH STRUCTURED_PG", statement_sql)
        self.assertIn("V_UNIT_BASE", statement_names)
        self.assertIn("VW_E_PRODUCT_TEST", statement_names)
        self.assertIn("STRUCTURED_PG", statement_names)

    def test_apply_blueprint_auto_fills_edge_table_name_for_structured_relations(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            domain = SysDomain(
                domain_id="dm_structured",
                domain_name="Structured Domain",
                domain_desc="test",
            )
            db.add(domain)
            db.commit()

            service = OntologyGuideService(db)
            apply_result = service.apply_blueprint(
                domain_id="dm_structured",
                blueprint={
                    "entities": [
                        {
                            "entityName": "ProductUnit",
                            "entityDisplayName": "产品",
                            "buildType": "VIEW",
                            "properties": [
                                {
                                    "propertyName": "vcm_id",
                                    "propertyDisplayName": "VCM_ID",
                                    "dataType": "VARCHAR2",
                                    "isPrimaryKey": "Y",
                                    "isNullable": "N",
                                },
                            ],
                        },
                        {
                            "entityName": "TestRun",
                            "entityDisplayName": "测试事件",
                            "buildType": "VIEW",
                            "properties": [
                                {
                                    "propertyName": "test_run_id",
                                    "propertyDisplayName": "测试事件ID",
                                    "dataType": "VARCHAR2",
                                    "isPrimaryKey": "Y",
                                    "isNullable": "N",
                                },
                            ],
                        },
                    ],
                    "relations": [
                        {
                            "sourceEntityName": "ProductUnit",
                            "targetEntityName": "TestRun",
                            "relationName": "有测试",
                            "relationType": "ASSOCIATION",
                            "relationDesc": "产品有测试事件。",
                        },
                    ],
                },
                overwrite_existing=False,
                created_by="tester",
            )

            self.assertEqual(apply_result["relations"]["created"], 1)
            relation = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_name == "ProductUnit").first()
            self.assertIsNotNone(relation)
            from app.models.models import SysOntologyRelation  # local import to keep test imports compact
            stored_relation = db.query(SysOntologyRelation).filter(SysOntologyRelation.domain_id == "dm_structured").first()
            self.assertEqual(stored_relation.relation_table_name, "ONTO_EDGE_PRODUCTUNIT_HAS_TEST_RUN_TESTRUN")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
