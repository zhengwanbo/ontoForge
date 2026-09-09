import unittest
from types import SimpleNamespace

from app.services.agent_service import AgentService


class AgentSkillSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AgentService.__new__(AgentService)

    def test_skill_semantic_defaults_are_read_from_context(self) -> None:
        defaults = self.service._skill_semantic_defaults_from_context({
            "analysis_semantics": {
                "analysis_profile": {
                    "analysis_modes": ["ROOT_CAUSE", "IMPACT_INFERENCE"],
                    "entry_entity_ids": ["ent_defect"],
                    "enable_activity_recommendation": False,
                    "default_time_window": "30D",
                    "max_path_depth": 4,
                },
                "metrics": [{"metric_id": "metric_defect_rate"}],
                "rules": [{"rule_id": "rule_over_limit"}],
                "activities": [{"activity_id": "act_review"}],
            }
        })

        self.assertEqual(["ROOT_CAUSE", "IMPACT_INFERENCE"], defaults["analysis_modes"])
        self.assertEqual(["ent_defect"], defaults["entry_entity_ids"])
        self.assertEqual(["metric_defect_rate"], defaults["selected_metric_ids"])
        self.assertEqual(["rule_over_limit"], defaults["selected_rule_ids"])
        self.assertEqual(["act_review"], defaults["selected_activity_ids"])
        self.assertFalse(defaults["enable_activity_recommendation"])
        self.assertEqual("30D", defaults["default_time_window"])
        self.assertEqual(4, defaults["max_path_depth"])

    def test_build_graph_semantic_map_binds_metrics_rules_and_activities(self) -> None:
        topology = {
            "nodes": [
                {"displayName": "DEFECTEVENT"},
                {"displayName": "METRICRESULT"},
                {"displayName": "PRODUCTIONBATCH"},
            ]
        }
        entity_index = {
            "ent_defect": {"entity_id": "ent_defect", "entity_name": "DefectEvent"},
            "ent_batch": {"entity_id": "ent_batch", "entity_name": "ProductionBatch"},
        }
        relation_index = {
            "rel_defect_batch": {
                "relation_id": "rel_defect_batch",
                "relation_name": "关联批次",
                "source_entity_id": "ent_defect",
                "target_entity_id": "ent_batch",
            }
        }
        mapping = self.service._build_graph_semantic_map(
            topology=topology,
            metrics=[{"metric_id": "m1", "metric_name": "缺陷率", "entity_id": "ent_defect", "aggregation_method": "RATIO", "calculation_period": "7D"}],
            rules=[{"rule_id": "r1", "rule_name": "超规判定", "rule_category": "DECISION", "scope_entity_id": "ent_defect", "scope_relation_id": "rel_defect_batch", "activity_id": "a1"}],
            activities=[{"activity_id": "a1", "activity_name": "人工复核", "activity_type": "MANUAL_REVIEW", "process_id": "p1", "process_name": "缺陷处理流程"}],
            entity_index=entity_index,
            relation_index=relation_index,
        )

        self.assertEqual(["DEFECTEVENT"], mapping["metric_bindings"][0]["graph_labels"])
        self.assertEqual(["DEFECTEVENT"], mapping["rule_bindings"][0]["scope_entity_labels"])
        self.assertEqual(["超规判定"], mapping["activity_bindings"][0]["triggered_by_rules"])

    def test_build_ontology_graph_binding_maps_entities_properties_and_relations(self) -> None:
        ontology_model = {
            "entities": [
                {
                    "entity_id": "ent_order",
                    "entity_name": "Order",
                    "entity_display_name": "订单",
                    "properties": [
                        {"property_id": "prop_order_id", "property_name": "orderId", "property_display_name": "订单号"},
                        {"property_id": "prop_total", "property_name": "totalAmount", "property_display_name": "金额"},
                    ],
                },
                {
                    "entity_id": "ent_store",
                    "entity_name": "Store",
                    "entity_display_name": "门店",
                    "properties": [
                        {"property_id": "prop_store_id", "property_name": "storeId", "property_display_name": "门店号"},
                    ],
                },
            ],
            "relations": [
                {
                    "relation_id": "rel_order_store",
                    "relation_name": "订单归属门店",
                    "source_entity_id": "ent_order",
                    "source_entity_name": "Order",
                    "target_entity_id": "ent_store",
                    "target_entity_name": "Store",
                }
            ],
        }
        topology = {
            "nodes": [
                {
                    "displayName": "ORDER",
                    "properties": [
                        {"property_name": "ORDER_ID", "data_type": "VARCHAR2", "is_primary_key": True},
                        {"property_name": "TOTAL_AMOUNT", "data_type": "NUMBER", "is_primary_key": False},
                    ],
                },
                {
                    "displayName": "STORE",
                    "properties": [
                        {"property_name": "STORE_ID", "data_type": "VARCHAR2", "is_primary_key": True},
                    ],
                },
            ],
            "edges": [
                {
                    "id": "edge_order_store",
                    "name": "BELONGS_TO",
                    "source": "ORDER",
                    "target": "STORE",
                    "tableName": "ORDER_STORE_EDGE",
                }
            ],
        }

        binding = self.service._build_ontology_graph_binding(ontology_model, topology)

        self.assertEqual(["ORDER"], binding["entity_bindings"][0]["graph_labels"])
        self.assertTrue(binding["entity_bindings"][0]["matched"])
        self.assertEqual("ORDER_ID", binding["property_bindings"][0]["matched_columns"][0]["graph_property_name"])
        self.assertEqual("TOTAL_AMOUNT", binding["property_bindings"][1]["matched_columns"][0]["graph_property_name"])
        self.assertEqual("BELONGS_TO", binding["relation_bindings"][0]["matched_edges"][0]["graph_edge_name"])
        self.assertTrue(binding["relation_bindings"][0]["matched"])

    def test_skill_markdown_mentions_metric_rule_activity_references(self) -> None:
        domain = SimpleNamespace(domain_name="制造缺陷分析")
        process = SimpleNamespace(process_name="缺陷闭环流程", process_json='{"nodes":[],"edges":[]}')
        entity = SimpleNamespace(entity_name="PG_DEFECT", entity_display_name="PG_DEFECT")
        skill = SimpleNamespace(
            skill_name="缺陷根因分析技能",
            skill_desc="用于缺陷根因与影响分析。",
            analysis_goal="识别缺陷根因并评估影响。",
            execution_rules="先定位异常，再沿关系追溯。",
            output_requirements="输出关键指标、命中规则与建议活动。",
        )
        skill_context = {
            "analysis_semantics": {
                "analysis_profile": {
                    "analysis_modes": ["DEFECT_ANALYSIS", "ROOT_CAUSE"],
                    "default_time_window": "7D",
                    "max_path_depth": 3,
                },
                "metrics": [{"metric_name": "缺陷率"}],
                "rules": [{"rule_name": "超规判定"}],
                "activities": [{"activity_name": "人工复核"}],
            }
            ,
            "ontology_model": {
                "entity_count": 2,
                "relation_count": 1,
            },
            "ontology_graph_binding": {
                "entity_bindings": [{"matched": True}, {"matched": False}],
            },
        }
        markdown = self.service._build_skill_markdown(
            domain,
            process,
            entity,
            skill,
            {"graph_name": "PG_DEFECT"},
            skill_context,
        )

        self.assertIn("references/metric-catalog.json", markdown)
        self.assertIn("references/rule-catalog.json", markdown)
        self.assertIn("references/activity-playbook.json", markdown)
        self.assertIn("references/ontology-model.json", markdown)
        self.assertIn("references/ontology-graph-binding.json", markdown)
        self.assertIn("## 已加载本体", markdown)
        self.assertIn("- 本体对象数：2", markdown)
        self.assertIn("- 已映射到当前 Property Graph 的对象数：1", markdown)
        self.assertIn("- 核心指标：缺陷率", markdown)
        self.assertIn("- 关键规则：超规判定", markdown)
        self.assertIn("- 可建议活动：人工复核", markdown)

    def test_skill_query_guidance_embeds_ontology_references(self) -> None:
        guidance = self.service._build_skill_query_guidance(
            "# Skill\n主说明",
            {
                "references/ontology-model.json": '{"entities":[]}',
                "references/ontology-graph-binding.json": '{"entity_bindings":[]}',
                "references/analysis-strategy.md": "# strategy",
            },
        )

        self.assertIn("## references/ontology-model.json", guidance)
        self.assertIn("## references/ontology-graph-binding.json", guidance)
        self.assertIn("## references/analysis-strategy.md", guidance)

    def test_build_analysis_scenario_templates_recommends_supply_trace_defaults(self) -> None:
        templates = self.service._build_analysis_scenario_templates(
            entities=[
                {"entity_id": "bottle", "entity_name": "BottleCode", "entity_display_name": "瓶码"},
                {"entity_id": "outbound", "entity_name": "OutboundOrder", "entity_display_name": "出库单"},
            ],
            metrics=[
                {"metric_id": "m_trace", "metric_name": "出库数量", "metric_code": "OUTBOUND_QTY", "metric_desc": "按出库单统计数量", "metric_category": "BUSINESS"},
            ],
            rules=[
                {"rule_id": "r_trace", "rule_name": "追溯链路一致性", "rule_desc": "校验码与批次链路", "rule_category": "VALIDATION", "scope_entity_name": "BottleCode", "scope_entity_display_name": "瓶码", "scope_relation_name": "关联出库"},
            ],
            activities=[
                {"activity_id": "a_notify", "activity_type": "NOTIFY"},
                {"activity_id": "a_review", "activity_type": "MANUAL_REVIEW"},
            ],
        )

        supply_trace = next(item for item in templates if item["scenario_code"] == "SUPPLY_TRACE")
        self.assertEqual(["TRACEBACK"], supply_trace["recommended_analysis_modes"])
        self.assertIn("bottle", supply_trace["recommended_entry_entity_ids"])
        self.assertIn("m_trace", supply_trace["recommended_metric_ids"])
        self.assertIn("r_trace", supply_trace["recommended_rule_ids"])

    def test_load_analysis_semantics_uses_scenario_defaults_when_manual_selection_is_empty(self) -> None:
        self.service.get_analysis_semantics = lambda _domain_id: {
            "scenario_templates": [{
                "scenario_code": "QUALITY_DEFECT",
                "scenario_name": "质量缺陷分析",
                "recommended_analysis_modes": ["DEFECT_ANALYSIS", "ROOT_CAUSE"],
                "default_time_window": "30D",
                "max_path_depth": 3,
                "enable_activity_recommendation": True,
                "recommended_entry_entity_ids": ["ent_defect"],
                "recommended_metric_ids": ["m1"],
                "recommended_rule_ids": ["r1"],
                "recommended_activity_ids": ["a1"],
            }],
            "entities": [{"entity_id": "ent_defect", "entity_name": "DefectEvent", "entity_display_name": "缺陷事件"}],
            "relations": [],
            "metrics": [{"metric_id": "m1", "entity_id": "ent_defect", "metric_name": "缺陷率", "status": "ACTIVE"}],
            "rules": [{"rule_id": "r1", "rule_name": "超规判定", "scope_entity_id": "ent_defect", "status": "ACTIVE"}],
            "activities": [{"activity_id": "a1", "activity_name": "人工复核", "activity_type": "MANUAL_REVIEW", "status": "ACTIVE"}],
        }
        semantics = self.service._load_analysis_semantics_for_skill(
            domain_id="dm_quality",
            payload={"analysis_scenario_code": "QUALITY_DEFECT"},
            topology={"nodes": [{"displayName": "DEFECTEVENT"}]},
        )

        profile = semantics["analysis_profile"]
        self.assertEqual("QUALITY_DEFECT", profile["analysis_scenario_code"])
        self.assertEqual(["DEFECT_ANALYSIS", "ROOT_CAUSE"], profile["analysis_modes"])
        self.assertEqual("30D", profile["default_time_window"])
        self.assertEqual(["m1"], [item["metric_id"] for item in semantics["metrics"]])
        self.assertEqual(["r1"], [item["rule_id"] for item in semantics["rules"]])
        self.assertEqual(["a1"], [item["activity_id"] for item in semantics["activities"]])


if __name__ == "__main__":
    unittest.main()
