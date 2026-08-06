import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.models import (
    SysDomain,
    SysEntityMapping,
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
    SysPropertyMapping,
    SysRelationMapping,
)
from app.services.ddl_service import DDLService
from app.services.llm_service import LLMService


class OntologyGraphMappingDesignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.llm_service = LLMService.__new__(LLMService)
        self.ddl_service = DDLService.__new__(DDLService)

    def test_normalizes_complete_node_and_edge_design(self) -> None:
        entities = [
            {"entity_id": "ent_unit", "entity_name": "Unit"},
            {"entity_id": "ent_lot", "entity_name": "ProductLot"},
        ]
        relations = [{
            "relation_id": "rel_belongs",
            "relation_name": "BELONGS_TO",
            "source_entity_id": "ent_unit",
            "source_entity_name": "Unit",
            "target_entity_id": "ent_lot",
            "target_entity_name": "ProductLot",
        }]
        payload = {
            "entityMappings": [
                {
                    "entityId": "ent_unit",
                    "entityName": "Unit",
                    "nodeTableName": "TAMS_UNIT",
                    "buildType": "TABLE",
                    "sourceTables": ["PDX25_TAMS_UNIT"],
                    "keyPropertyName": "vcm_id",
                    "keyOutputColumn": "VCM_ID",
                    "nodeSql": "SELECT CAST(VCM_ID AS VARCHAR2(512)) AS VCM_ID, LOT FROM PDX25_TAMS_UNIT WHERE VCM_ID IS NOT NULL",
                },
                {
                    "entityId": "ent_lot",
                    "entityName": "ProductLot",
                    "nodeTableName": "TAMS_PRODUCT_LOT",
                    "buildType": "TABLE",
                    "sourceTables": ["PDX25_TAMS_UNIT"],
                    "keyPropertyName": "product_lot_id",
                    "keyOutputColumn": "PRODUCT_LOT_ID",
                    "nodeSql": "SELECT TRIM(LOT) AS PRODUCT_LOT_ID, COUNT(*) AS UNIT_COUNT FROM PDX25_TAMS_UNIT WHERE LOT IS NOT NULL GROUP BY TRIM(LOT)",
                },
            ],
            "relationMappings": [{
                "relationId": "rel_belongs",
                "relationName": "BELONGS_TO",
                "sourceEntityName": "Unit",
                "targetEntityName": "ProductLot",
                "edgeTableName": "TAMS_EDGE_BELONGS_TO",
                "sourceTables": ["TAMS_UNIT"],
                "joinCondition": "UNIT.LOT IS NOT NULL",
                "edgeSql": "SELECT VCM_ID || '->BELONGS_TO->' || LOT AS EDGE_ID, VCM_ID AS SOURCE_ID, TRIM(LOT) AS TARGET_ID FROM TAMS_UNIT WHERE LOT IS NOT NULL",
            }],
        }

        normalized = self.llm_service._normalize_ontology_property_graph_mapping(
            payload,
            ontology_entities=entities,
            ontology_relations=relations,
        )

        self.assertEqual(len(normalized["entity_mappings"]), 2)
        self.assertEqual(normalized["entity_mappings"][0]["node_table_name"], "TAMS_UNIT")
        self.assertEqual(normalized["entity_mappings"][1]["key_property_name"], "product_lot_id")
        self.assertEqual(len(normalized["relation_mappings"]), 1)
        self.assertEqual(normalized["relation_mappings"][0]["edge_table_name"], "TAMS_EDGE_BELONGS_TO")
        self.assertIn("AS SOURCE_ID", normalized["relation_mappings"][0]["edge_sql"])

    def test_rejects_mutating_node_sql_and_incomplete_edge_sql(self) -> None:
        normalized = self.llm_service._normalize_ontology_property_graph_mapping(
            {
                "entityMappings": [{
                    "entityId": "ent_unit",
                    "entityName": "Unit",
                    "nodeTableName": "TAMS_UNIT",
                    "keyPropertyName": "vcm_id",
                    "nodeSql": "CREATE TABLE BAD_TABLE (ID NUMBER)",
                }],
                "relationMappings": [{
                    "relationId": "rel_belongs",
                    "edgeSql": "SELECT VCM_ID AS SOURCE_ID, LOT AS TARGET_ID FROM TAMS_UNIT",
                }],
            },
            ontology_entities=[{"entity_id": "ent_unit", "entity_name": "Unit"}],
            ontology_relations=[{
                "relation_id": "rel_belongs",
                "relation_name": "BELONGS_TO",
                "source_entity_id": "ent_unit",
                "source_entity_name": "Unit",
                "target_entity_id": "ent_lot",
                "target_entity_name": "ProductLot",
            }],
        )

        self.assertEqual(normalized["entity_mappings"], [])
        self.assertEqual(normalized["relation_mappings"], [])

    def test_rejects_different_primary_keys_and_accepts_shared_fk_pk_join(self) -> None:
        entities = [
            {
                "entity_id": "bottle",
                "entity_name": "BottleCode",
                "properties": [
                    {"property_name": "bottle_id", "is_primary_key": True},
                    {"property_name": "product_id", "is_primary_key": False},
                ],
            },
            {
                "entity_id": "product",
                "entity_name": "Product",
                "properties": [{"property_name": "product_id", "is_primary_key": True}],
            },
        ]
        relations = [{
            "relation_id": "belongs",
            "relation_name": "归属",
            "source_entity_id": "bottle",
            "target_entity_id": "product",
        }]
        base_edge_sql = "SELECT 1 AS EDGE_ID, 1 AS SOURCE_ID, 2 AS TARGET_ID FROM DUAL"
        payload = {"relationMappings": [
            {"relationId": "belongs", "joinCondition": "src.BOTTLE_ID = dst.PRODUCT_ID", "edgeSql": base_edge_sql},
            {"relationId": "belongs", "joinCondition": "src.PRODUCT_ID = dst.PRODUCT_ID", "edgeSql": base_edge_sql},
        ]}

        normalized = self.llm_service._normalize_ontology_property_graph_mapping(
            payload,
            ontology_entities=entities,
            ontology_relations=relations,
            entity_mapping_results=[
                {"entity_id": "bottle", "mappings": [{"propertyName": "bottle_id", "sourceColumn": "BOTTLE_ID"}, {"propertyName": "product_id", "sourceColumn": "PRODUCT_ID"}]},
                {"entity_id": "product", "mappings": [{"propertyName": "product_id", "sourceColumn": "PRODUCT_ID"}]},
            ],
        )

        self.assertEqual(1, len(normalized["relation_mappings"]))
        self.assertEqual("src.PRODUCT_ID = dst.PRODUCT_ID", normalized["relation_mappings"][0]["join_condition"])

    def test_injects_verified_fk_pk_candidate_when_llm_omits_relation(self) -> None:
        entities = [
            {"entity_id": "batch", "entity_name": "ProductionBatch", "properties": [{"property_name": "batch_id", "is_primary_key": True}, {"property_name": "product_id", "is_primary_key": False}]},
            {"entity_id": "product", "entity_name": "Product", "properties": [{"property_name": "product_id", "is_primary_key": True}]},
        ]
        relations = [{"relation_id": "batch_product", "relation_name": "对应", "source_entity_id": "batch", "target_entity_id": "product"}]
        mapping_results = [
            {"entity_id": "batch", "mappings": [{"propertyName": "product_id", "sourceTable": "PRODUCTION_BATCH", "sourceColumn": "PRODUCT_ID"}]},
            {"entity_id": "product", "mappings": [{"propertyName": "product_id", "sourceTable": "PRODUCT", "sourceColumn": "PRODUCT_ID"}]},
        ]
        candidates = self.llm_service._build_verified_direct_relation_candidates(entities, relations, mapping_results)
        normalized = self.llm_service._normalize_ontology_property_graph_mapping(
            {"relationMappings": []},
            ontology_entities=entities,
            ontology_relations=relations,
            entity_mapping_results=mapping_results,
            verified_direct_relation_candidates=candidates,
        )

        self.assertEqual("src.PRODUCT_ID = dst.PRODUCT_ID", candidates[0]["join_condition"])
        self.assertEqual(1, len(normalized["relation_mappings"]))
        self.assertEqual("src.PRODUCT_ID = dst.PRODUCT_ID", normalized["relation_mappings"][0]["join_condition"])

    def test_ddl_matches_reference_ctas_primary_key_edge_and_graph_pattern(self) -> None:
        unit = SysOntologyEntity(
            entity_id="ent_unit",
            domain_id="dm_tams",
            entity_name="Unit",
            entity_display_name="产品单元",
            build_type="TABLE",
            table_name="TAMS_UNIT",
        )
        unit.properties = [
            SysOntologyProperty(
                property_id="prop_vcm_id",
                entity_id="ent_unit",
                property_name="vcm_id",
                data_type="VARCHAR2(512)",
                is_primary_key="Y",
            ),
            SysOntologyProperty(
                property_id="prop_lot",
                entity_id="ent_unit",
                property_name="lot",
                data_type="VARCHAR2(512)",
                is_primary_key="N",
            ),
        ]
        unit.entity_mapping = SysEntityMapping(
            mapping_id="emap_unit",
            entity_id="ent_unit",
            build_type="TABLE",
            view_sql="SELECT CAST(VCM_ID AS VARCHAR2(512)) AS VCM_ID, LOT FROM PDX25_TAMS_UNIT WHERE VCM_ID IS NOT NULL",
            mapping_status="CONFIRMED",
        )

        lot = SysOntologyEntity(
            entity_id="ent_lot",
            domain_id="dm_tams",
            entity_name="ProductLot",
            entity_display_name="产品批次",
            build_type="TABLE",
            table_name="TAMS_PRODUCT_LOT",
        )
        lot.properties = [
            SysOntologyProperty(
                property_id="prop_lot_id",
                entity_id="ent_lot",
                property_name="product_lot_id",
                data_type="VARCHAR2(512)",
                is_primary_key="Y",
            ),
        ]
        lot.entity_mapping = SysEntityMapping(
            mapping_id="emap_lot",
            entity_id="ent_lot",
            build_type="TABLE",
            view_sql="SELECT TRIM(LOT) AS PRODUCT_LOT_ID FROM PDX25_TAMS_UNIT WHERE LOT IS NOT NULL GROUP BY TRIM(LOT)",
            mapping_status="CONFIRMED",
        )

        relation = SysOntologyRelation(
            relation_id="rel_belongs",
            domain_id="dm_tams",
            source_entity_id="ent_unit",
            target_entity_id="ent_lot",
            relation_name="BELONGS_TO",
            relation_type="MANY_TO_ONE",
            relation_table_name="TAMS_EDGE_BELONGS_TO",
        )
        relation.source_entity = unit
        relation.target_entity = lot
        relation.relation_mapping = SysRelationMapping(
            mapping_id="rmap_belongs",
            relation_id="rel_belongs",
            join_condition="src.VCM_ID = dst.PRODUCT_LOT_ID",
            edge_sql="SELECT VCM_ID AS SOURCE_ID, PRODUCT_LOT_ID AS TARGET_ID FROM TAMS_UNIT",
            mapping_status="SUGGESTED",
        )

        node_ddl = self.ddl_service._generate_table_ddl(unit)
        edge_ddl = self.ddl_service._generate_relation_table_ddl(relation, [unit, lot])
        graph_ddl = self.ddl_service._generate_property_graph_ddl(
            domain=SysDomain(domain_id="dm_tams", domain_name="TAMS"),
            blueprint_package={},
            entities=[unit, lot],
            relations=[relation],
        )

        self.assertIn("CREATE TABLE TAMS_UNIT AS", node_ddl)
        self.assertIn("DELETE FROM TAMS_UNIT t", node_ddl)
        self.assertIn("ROWID", node_ddl)
        self.assertIn("PRIMARY KEY (VCM_ID)", node_ddl)
        self.assertIn("CREATE TABLE TAMS_EDGE_BELONGS_TO AS", edge_ddl)
        self.assertIn("ROW_NUMBER() OVER", edge_ddl)
        self.assertIn("src.VCM_ID AS SOURCE_ID", edge_ddl)
        self.assertIn("dst.PRODUCT_LOT_ID AS TARGET_ID", edge_ddl)
        self.assertIn("JOIN TAMS_PRODUCT_LOT dst ON src.VCM_ID = dst.PRODUCT_LOT_ID", edge_ddl)
        self.assertIn("PRIMARY KEY (EDGE_ID)", edge_ddl)
        self.assertIn("CREATE OR REPLACE PROPERTY GRAPH", graph_ddl)
        self.assertIn("KEY (VCM_ID)", graph_ddl)
        self.assertIn("SOURCE KEY (SOURCE_ID) REFERENCES TAMS_UNIT (VCM_ID)", graph_ddl)
        self.assertIn(
            "DESTINATION KEY (TARGET_ID) REFERENCES TAMS_PRODUCT_LOT (PRODUCT_LOT_ID)",
            graph_ddl,
        )
        self.assertIn("TAMS_EDGE_BELONGS_TO", graph_ddl)
        self.assertNotIn("ONTO_EDGE_BELONGS_TO_V", graph_ddl)
        self.assertIn("OPTIONS (ALLOW MIXED PROPERTY TYPES)", graph_ddl)

    def test_recreate_cleanup_drops_only_generated_graph_view_and_tables_in_dependency_order(self) -> None:
        cleanup = self.ddl_service._generate_recreate_cleanup_ddl([
            {"type": "create_table", "name": "ONTO_UNIT", "sql": "CREATE TABLE ONTO_UNIT (ID NUMBER);"},
            {"type": "create_table", "name": "ONTO_EDGE_HAS", "sql": "CREATE TABLE ONTO_EDGE_HAS (ID NUMBER);"},
            {"type": "create_view", "name": "ONTO_UNIT_V", "sql": "CREATE VIEW ONTO_UNIT_V AS SELECT 1 ID FROM DUAL;"},
            {"type": "create_graph", "name": "ONTOLOGY_PG", "sql": "CREATE PROPERTY GRAPH ONTOLOGY_PG VERTEX TABLES (ONTO_UNIT);"},
            {"type": "comment_table", "name": "SOURCE_BUSINESS_TABLE", "sql": "COMMENT ON TABLE SOURCE_BUSINESS_TABLE IS 'source';"},
        ])

        self.assertEqual([item["type"] for item in cleanup], ["drop_graph", "drop_view", "drop_table", "drop_table"])
        self.assertEqual(cleanup[0]["sql"], "DROP PROPERTY GRAPH ONTOLOGY_PG;")
        self.assertEqual(cleanup[1]["sql"], "DROP VIEW ONTO_UNIT_V;")
        self.assertIn("CASCADE CONSTRAINTS PURGE", cleanup[2]["sql"])
        self.assertNotIn("SOURCE_BUSINESS_TABLE", "\n".join(item["sql"] for item in cleanup))

    def test_relation_ddl_normalizes_legacy_tgt_alias_and_source_columns(self) -> None:
        product = SysOntologyEntity(entity_id="product", entity_name="ProductUnit", table_name="ONTO_PRODUCT")
        product_model = SysOntologyEntity(entity_id="model", entity_name="ProductModel", table_name="ONTO_MODEL")
        product.properties = [
            SysOntologyProperty(property_id="p_vcm", property_name="vcm_id", is_primary_key="Y"),
            SysOntologyProperty(property_id="p_model", property_name="model", is_primary_key="N"),
        ]
        product_model.properties = [
            SysOntologyProperty(property_id="m_code", property_name="model_code", is_primary_key="Y"),
        ]
        product_model.properties[0].mapping = SysPropertyMapping(
            mapping_id="model_code_mapping",
            property_id="m_code",
            source_column="MODEL",
        )
        relation = SysOntologyRelation(
            relation_id="rel_model",
            source_entity_id="product",
            target_entity_id="model",
            relation_name="属于",
            relation_table_name="ONTO_EDGE_PRODUCT_MODEL",
        )
        relation.relation_mapping = SysRelationMapping(
            mapping_id="map_model",
            relation_id="rel_model",
            join_condition="src.MODEL = tgt.MODEL",
        )

        sql = self.ddl_service._generate_relation_table_ddl(relation, [product, product_model])

        self.assertIsNotNone(sql)
        self.assertIn("JOIN ONTO_MODEL dst ON src.MODEL = dst.MODEL_CODE", sql)

    def test_relation_ddl_skips_unmapped_semantic_columns(self) -> None:
        metric = SysOntologyEntity(entity_id="metric", entity_name="Metric", table_name="ONTO_METRIC")
        defect = SysOntologyEntity(entity_id="defect", entity_name="Defect", table_name="ONTO_DEFECT")
        metric.properties = [
            SysOntologyProperty(property_id="metric_id", property_name="metric_id", is_primary_key="Y"),
            SysOntologyProperty(property_id="metric_name", property_name="metric_name", is_primary_key="N"),
        ]
        defect.properties = [
            SysOntologyProperty(property_id="defect_id", property_name="defect_id", is_primary_key="Y"),
        ]
        relation = SysOntologyRelation(
            relation_id="rel_defect",
            source_entity_id="metric",
            target_entity_id="defect",
            relation_name="指向",
            relation_table_name="ONTO_EDGE_METRIC_DEFECT",
        )
        relation.relation_mapping = SysRelationMapping(
            mapping_id="map_defect",
            relation_id="rel_defect",
            join_condition="src.METRIC_NAME = tgt.SEMANTIC_MAPPING_METRIC_NAME",
        )

        sql = self.ddl_service._generate_relation_table_ddl(relation, [metric, defect])
        graph_sql = self.ddl_service._generate_property_graph_ddl(
            domain=SysDomain(domain_id="dm_test", domain_name="test"),
            blueprint_package={},
            entities=[metric, defect],
            relations=[relation],
        )

        self.assertIsNone(sql)
        self.assertNotIn("ONTO_EDGE_METRIC_DEFECT", graph_sql)

    def test_legacy_relation_edge_view_is_cleanup_only_not_generated(self) -> None:
        source = SysOntologyEntity(entity_id="source", entity_name="Source", table_name="ONTO_SOURCE")
        target = SysOntologyEntity(entity_id="target", entity_name="Target", table_name="ONTO_TARGET")
        source.properties = [SysOntologyProperty(property_id="source_id", property_name="id", is_primary_key="Y")]
        target.properties = [SysOntologyProperty(property_id="target_id", property_name="id", is_primary_key="Y")]
        relation = SysOntologyRelation(
            relation_id="rel_legacy",
            source_entity_id="source",
            target_entity_id="target",
            relation_name="属于",
            relation_table_name="ONTO_EDGE_SOURCE_TARGET",
        )
        relation.relation_mapping = SysRelationMapping(
            mapping_id="legacy_mapping",
            relation_id="rel_legacy",
            edge_sql="SELECT 'S' AS SOURCE_ID, 'T' AS TARGET_ID FROM dual",
        )

        semantic = self.ddl_service._generate_semantic_layer_ddl(
            SysDomain(domain_id="dm_test", domain_name="test"), {}, [source, target], [relation]
        )
        cleanup = self.ddl_service._generate_obsolete_semantic_view_cleanup({}, [source, target], [relation])

        self.assertNotIn("ONTO_EDGE_EDGE_V", "\n".join(item["sql"] for item in semantic))
        self.assertIn("DROP VIEW ONTO_EDGE_EDGE_V;", "\n".join(item["sql"] for item in cleanup))

    def test_tams_alarm_relation_uses_vcm_id_when_legacy_mapping_uses_product_unit_id(self) -> None:
        product = SysOntologyEntity(entity_id="product", entity_name="ProductUnit", table_name="ONTO_PRODUCT")
        alarm = SysOntologyEntity(entity_id="alarm", entity_name="AlarmEvent", table_name="ONTO_ALARM")
        product.properties = [SysOntologyProperty(property_id="product_vcm", property_name="vcm_id", is_primary_key="Y")]
        alarm.properties = [SysOntologyProperty(property_id="alarm_id", property_name="alarm_event_id", is_primary_key="Y")]
        alarm.entity_mapping = SysEntityMapping(
            mapping_id="alarm_map",
            entity_id="alarm",
            view_sql="SELECT VCM_ID, 'ALARM' AS ALARM_EVENT_ID FROM SOURCE_ALARM",
        )
        relation = SysOntologyRelation(
            relation_id="rel_alarm",
            source_entity_id="product",
            target_entity_id="alarm",
            relation_name="关联报警",
            relation_table_name="ONTO_EDGE_PRODUCT_ALARM",
        )
        relation.relation_mapping = SysRelationMapping(
            mapping_id="alarm_relation_map",
            relation_id="rel_alarm",
            join_condition="src.VCM_ID = dst.PRODUCT_UNIT_ID",
        )

        sql = self.ddl_service._generate_relation_table_ddl(relation, [product, alarm])

        self.assertIsNotNone(sql)
        self.assertIn("JOIN ONTO_ALARM dst ON src.VCM_ID = dst.VCM_ID", sql)

    def test_tams_process_resource_and_metric_defect_relations_use_canonical_joins(self) -> None:
        process = SysOntologyEntity(entity_id="process", entity_name="ProcessEvent", table_name="ONTO_PROCESS")
        equipment = SysOntologyEntity(entity_id="equipment", entity_name="Equipment", table_name="ONTO_EQUIPMENT")
        material = SysOntologyEntity(entity_id="material", entity_name="MaterialLot", table_name="ONTO_MATERIAL")
        metric = SysOntologyEntity(entity_id="metric", entity_name="MetricResult", table_name="ONTO_METRIC")
        defect = SysOntologyEntity(entity_id="defect", entity_name="DefectType", table_name="ONTO_DEFECT")
        process.properties = [SysOntologyProperty(property_id="process_id", property_name="process_event_id", is_primary_key="Y")]
        process.entity_mapping = SysEntityMapping(mapping_id="process_map", entity_id="process", view_sql="SELECT EQUIPMENT_CODE, MATERIAL_LOT_ID, 'P' AS PROCESS_EVENT_ID FROM DUAL")
        equipment.properties = [SysOntologyProperty(property_id="equipment_id", property_name="equipment_id", is_primary_key="Y")]
        material.properties = [SysOntologyProperty(property_id="material_id", property_name="material_lot_id", is_primary_key="Y")]
        metric.properties = [
            SysOntologyProperty(property_id="metric_id", property_name="metric_result_id", is_primary_key="Y"),
            SysOntologyProperty(property_id="metric_name", property_name="metric_name", is_primary_key="N"),
        ]
        defect.properties = [SysOntologyProperty(property_id="defect_id", property_name="defect_type_code", is_primary_key="Y")]

        def relation(relation_id, source, target, name):
            return SysOntologyRelation(relation_id=relation_id, source_entity_id=source.entity_id, target_entity_id=target.entity_id, relation_name=name, relation_table_name=f"ONTO_EDGE_{relation_id.upper()}")

        equipment_sql = self.ddl_service._generate_relation_table_ddl(relation("runs", process, equipment, "运行于"), [process, equipment])
        material_sql = self.ddl_service._generate_relation_table_ddl(relation("consumes", process, material, "消耗"), [process, material])
        defect_sql = self.ddl_service._generate_relation_table_ddl(relation("indicates", metric, defect, "指向"), [metric, defect])

        self.assertIn("src.EQUIPMENT_CODE = dst.EQUIPMENT_ID", equipment_sql)
        self.assertIn("src.MATERIAL_LOT_ID = dst.MATERIAL_LOT_ID", material_sql)
        self.assertIn("CASE", defect_sql)
        self.assertIn("SFR_DEFECT_OTHER", defect_sql)

    def test_relation_storage_name_is_unique_for_non_ascii_relation_names(self) -> None:
        first = SimpleNamespace(
            relation_id="rel_111aaa",
            relation_name="属于",
            relation_table_name=None,
        )
        second = SimpleNamespace(
            relation_id="rel_222bbb",
            relation_name="产生观测",
            relation_table_name=None,
        )

        first_name = self.ddl_service._resolve_relation_storage_name(first)
        second_name = self.ddl_service._resolve_relation_storage_name(second)

        self.assertEqual(first_name, "ONTO_EDGE_EDGE_REL_111AAA")
        self.assertEqual(second_name, "ONTO_EDGE_EDGE_REL_222BBB")
        self.assertNotEqual(first_name, second_name)

    def test_filters_llm_property_graph_and_comments_only_projected_columns(self) -> None:
        entity = SysOntologyEntity(
            entity_id="ent_test",
            domain_id="dm_test",
            entity_name="TestNode",
            table_name="ONTO_NODE_TEST",
            entity_display_name="测试节点",
        )
        entity.properties = [
            SysOntologyProperty(property_id="prop_id", property_name="id", property_desc="主键"),
            SysOntologyProperty(property_id="prop_missing", property_name="missing_col", property_desc="不存在列"),
        ]
        entity.entity_mapping = SysEntityMapping(
            mapping_id="emap_test",
            entity_id="ent_test",
            view_sql="SELECT SOURCE_ID AS ID FROM SOURCE_TABLE",
        )

        comments = self.ddl_service._generate_comments_ddl(entity)
        filtered = self.ddl_service._filter_to_required_object_views(
            [
                {"type": "create_graph", "name": "BAD_GRAPH", "sql": "CREATE PROPERTY GRAPH BAD_GRAPH"},
                {"type": "comment_column", "name": "ONTO_NODE_TEST.MISSING_COL", "sql": "COMMENT ON COLUMN ONTO_NODE_TEST.MISSING_COL IS 'bad'"},
            ],
            [entity],
        )

        self.assertIn("COMMENT ON COLUMN ONTO_NODE_TEST.ID", "\n".join(item["sql"] for item in comments))
        self.assertNotIn("MISSING_COL", "\n".join(item["sql"] for item in comments))
        self.assertEqual(filtered, [])


class DDLExecutionScriptTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_drop_target_is_skipped_before_oracle_execution(self) -> None:
        class FakeCursor:
            def __init__(self):
                self.executed = []

            def execute(self, statement):
                self.executed.append(str(statement))

            def close(self):
                return None

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                return None

            def rollback(self):
                return None

            def close(self):
                return None

        service = DDLService(SimpleNamespace())
        connection = FakeConnection()
        with patch("app.services.ddl_service.SourceDataService") as source_service_cls, patch.object(
            service, "_ddl_object_exists", return_value=False
        ):
            source_service_cls.return_value._connect_to_oracle.return_value = connection
            result = await service.execute_ddl(
                "DROP VIEW VW_NOT_CREATED;",
                target_source=SimpleNamespace(source_name="目标对象库"),
            )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(connection.cursor_instance.executed, [])

    async def test_leading_description_comments_do_not_hide_ddl(self) -> None:
        class FakeCursor:
            def __init__(self):
                self.executed = []

            def execute(self, statement):
                self.executed.append(str(statement))

            def close(self):
                return None

            def fetchone(self):
                return None

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                return None

            def rollback(self):
                return None

            def close(self):
                return None

        class FakeDB:
            pass

        db = FakeDB()
        service = DDLService(db)
        connection = FakeConnection()
        with patch("app.services.ddl_service.SourceDataService") as source_service_cls:
            source_service_cls.return_value._connect_to_oracle.return_value = connection
            result = await service.execute_ddl(
                """-- 本体节点: UNIT
CREATE TABLE TAMS_UNIT AS
SELECT VCM_ID FROM PDX25_TAMS_UNIT;

-- 节点主键
ALTER TABLE TAMS_UNIT ADD CONSTRAINT PK_TAMS_UNIT PRIMARY KEY (VCM_ID);""",
                target_source=SimpleNamespace(source_name="目标对象库"),
            )

        self.assertEqual(result["success"], 2)
        self.assertEqual(len(connection.cursor_instance.executed), 2)
        self.assertTrue(connection.cursor_instance.executed[0].startswith("CREATE TABLE TAMS_UNIT"))
        self.assertFalse(connection.cursor_instance.executed[0].endswith(";"))


if __name__ == "__main__":
    unittest.main()
