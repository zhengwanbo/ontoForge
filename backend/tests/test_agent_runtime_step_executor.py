import unittest

from app.services.agent_runtime.step_executor import ManagedSkillStepExecutor
from app.services.agent_service import AgentService


class AgentRuntimeStepExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AgentService.__new__(AgentService)

    def test_execute_graph_evidence_validates_probes_and_executes_query_steps(self):
        class DummySourceService:
            def __init__(self):
                self.validated_graph = []
                self.probed_graph = []
                self.executed_graph = []
                self.validated_readonly = []
                self.probed_readonly = []
                self.executed_readonly = []

            def validate_remote_graph_query(self, *, source_id, graph_sql, schema):
                self.validated_graph.append(graph_sql)

            def probe_remote_graph_query(self, *, source_id, graph_sql, schema):
                self.probed_graph.append(graph_sql)
                return True

            def execute_remote_graph_query(self, *, source_id, graph_sql, schema, row_limit):
                self.executed_graph.append(graph_sql)
                return {"columns": ["BOTTLE_CODE"], "rows": [{"BOTTLE_CODE": "BOT-001"}]}

            def validate_remote_readonly_sql(self, *, source_id, query_sql, schema):
                self.validated_readonly.append(query_sql)

            def probe_remote_readonly_sql(self, *, source_id, query_sql, schema):
                self.probed_readonly.append(query_sql)
                return True

            def execute_remote_readonly_sql(self, *, source_id, query_sql, schema, row_limit):
                self.executed_readonly.append(query_sql)
                return {"columns": ["METRIC_CODE", "METRIC_VALUE"], "rows": [{"METRIC_CODE": "BOTTLE_COUNT", "METRIC_VALUE": 1}]}

        self.service.source_service = DummySourceService()
        executor = ManagedSkillStepExecutor(self.service)
        emitted = []

        result = executor.execute_graph_evidence(
            plan={
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "select_root",
                        "label": "BOTTLECODE",
                        "filter": {"property": "BOTTLE_CODE", "value": "BOT-001"},
                        "display_properties": ["BOTTLE_CODE"],
                    },
                    {
                        "step_id": "s2",
                        "action": "fact_aggregate",
                        "label": "BOTTLECODE",
                        "root_label": "BOTTLECODE",
                        "scope": "root_object",
                        "based_on_step": "s1",
                        "metric_code": "BOTTLE_COUNT",
                        "metric_name": "瓶码数量",
                        "aggregation_method": "COUNT",
                        "column_name": "",
                    },
                ]
            },
            topology={
                "graph_name": "PG_TEST",
                "nodes": [
                    {
                        "displayName": "BOTTLECODE",
                        "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                        "properties": [
                            {"property_name": "BOTTLE_ID", "is_primary_key": "Y"},
                            {"property_name": "BOTTLE_CODE"},
                        ],
                    }
                ],
            },
            source_id="src_1",
            schema="GYL",
            sample_limit=20,
            event_callback=emitted.append,
        )

        self.assertFalse(result["needs_replan"])
        self.assertEqual(1, len(self.service.source_service.validated_graph))
        self.assertEqual(1, len(self.service.source_service.probed_graph))
        self.assertEqual(1, len(self.service.source_service.executed_graph))
        self.assertEqual(1, len(self.service.source_service.validated_readonly))
        self.assertEqual(1, len(self.service.source_service.probed_readonly))
        self.assertEqual(1, len(self.service.source_service.executed_readonly))
        self.assertTrue(any(item.get("event_type") == "STEP_VALIDATED" for item in emitted))
        self.assertTrue(any(item.get("event_type") == "STEP_PROBED" for item in emitted))
        self.assertTrue(any(item.get("runtime_state") == "PLANNED" for item in emitted))
        self.assertTrue(any(item.get("runtime_state") == "VALIDATING" for item in emitted))
        self.assertTrue(any(item.get("runtime_state") == "PROBING" for item in emitted))
        self.assertTrue(any(item.get("runtime_state") == "EXECUTING" for item in emitted))
        self.assertTrue(any(item.get("runtime_state") == "COMPLETED" for item in emitted))

    def test_execute_graph_evidence_requests_replan_when_root_probe_is_empty(self):
        class DummySourceService:
            def __init__(self):
                self.executed_graph = []

            def validate_remote_graph_query(self, *, source_id, graph_sql, schema):
                return None

            def probe_remote_graph_query(self, *, source_id, graph_sql, schema):
                return False

            def execute_remote_graph_query(self, *, source_id, graph_sql, schema, row_limit):
                self.executed_graph.append(graph_sql)
                return {"columns": [], "rows": []}

            def validate_remote_readonly_sql(self, *, source_id, query_sql, schema):
                return None

            def probe_remote_readonly_sql(self, *, source_id, query_sql, schema):
                return False

            def execute_remote_readonly_sql(self, *, source_id, query_sql, schema, row_limit):
                return {"columns": [], "rows": []}

        self.service.source_service = DummySourceService()
        executor = ManagedSkillStepExecutor(self.service)

        result = executor.execute_graph_evidence(
            plan={
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "select_root",
                        "label": "BOTTLECODE",
                        "filter": {"property": "BOTTLE_CODE", "value": "BOT-404"},
                        "display_properties": ["BOTTLE_CODE"],
                    }
                ]
            },
            topology={
                "graph_name": "PG_TEST",
                "nodes": [
                    {
                        "displayName": "BOTTLECODE",
                        "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                        "properties": [
                            {"property_name": "BOTTLE_ID", "is_primary_key": "Y"},
                            {"property_name": "BOTTLE_CODE"},
                        ],
                    }
                ],
            },
            source_id="src_1",
            schema="GYL",
            sample_limit=20,
        )

        self.assertTrue(result["needs_replan"])
        self.assertIn("BOTTLECODE", result["replan_reason"])
        self.assertEqual([], self.service.source_service.executed_graph)
        self.assertTrue(any(item.get("runtime_state") == "REPLANNING" for item in result["execution_events"]))

    def test_execute_graph_evidence_requests_replan_when_relation_probe_is_empty(self):
        class DummySourceService:
            def __init__(self):
                self.probe_calls = []
                self.executed_graph = []

            def validate_remote_graph_query(self, *, source_id, graph_sql, schema):
                return None

            def probe_remote_graph_query(self, *, source_id, graph_sql, schema):
                self.probe_calls.append(graph_sql)
                return len(self.probe_calls) == 1

            def execute_remote_graph_query(self, *, source_id, graph_sql, schema, row_limit):
                self.executed_graph.append(graph_sql)
                return {"columns": ["BOTTLE_CODE"], "rows": [{"BOTTLE_CODE": "BOT-001"}]}

            def validate_remote_readonly_sql(self, *, source_id, query_sql, schema):
                return None

            def probe_remote_readonly_sql(self, *, source_id, query_sql, schema):
                return False

            def execute_remote_readonly_sql(self, *, source_id, query_sql, schema, row_limit):
                return {"columns": [], "rows": []}

        self.service.source_service = DummySourceService()
        executor = ManagedSkillStepExecutor(self.service)

        result = executor.execute_graph_evidence(
            plan={
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "select_root",
                        "label": "BOTTLECODE",
                        "filter": {"property": "BOTTLE_CODE", "value": "BOT-001"},
                        "display_properties": ["BOTTLE_CODE"],
                    },
                    {
                        "step_id": "s2",
                        "action": "expand_relations",
                        "label": "QUALITYINSPECTION",
                        "from_step": "s1",
                        "path": [{"target": "QUALITYINSPECTION", "edge": "GRAPH_LABEL", "direction": "OUT"}],
                        "display_properties": ["INSPECTION_RESULT"],
                    },
                ]
            },
            topology={
                "graph_name": "PG_TEST",
                "nodes": [
                    {
                        "displayName": "BOTTLECODE",
                        "tableName": "GYL.ONTO_NODE_BOTTLECODE",
                        "properties": [
                            {"property_name": "BOTTLE_ID", "is_primary_key": "Y"},
                            {"property_name": "BOTTLE_CODE"},
                        ],
                    },
                    {
                        "displayName": "QUALITYINSPECTION",
                        "tableName": "GYL.ONTO_NODE_QUALITYINSPECTION",
                        "properties": [
                            {"property_name": "INSPECTION_ID", "is_primary_key": "Y"},
                            {"property_name": "INSPECTION_RESULT"},
                        ],
                    },
                ],
            },
            source_id="src_1",
            schema="GYL",
            sample_limit=20,
        )

        self.assertTrue(result["needs_replan"])
        self.assertEqual(["QUALITYINSPECTION"], result["replan_hints"]["excluded_target_labels"])
        self.assertEqual("BOTTLECODE", result["replan_hints"]["preferred_root_label"])
        self.assertEqual(1, len(self.service.source_service.executed_graph))


if __name__ == "__main__":
    unittest.main()
