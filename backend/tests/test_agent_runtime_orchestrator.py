import asyncio
import unittest
from types import SimpleNamespace

from app.services.agent_runtime.managed_skill_test_orchestrator import ManagedSkillTestOrchestrator


class AgentRuntimeOrchestratorTests(unittest.TestCase):
    def test_execute_turn_emits_reference_pattern_matched_event(self):
        class DummyDb:
            def commit(self):
                return None

        class DummySourceService:
            def get_remote_property_graph_topology(self, *, source_id, schema):
                return {"schema": schema or "GYL", "graph_name": "PG_TRACE", "nodes": [{"displayName": "OUTBOUNDORDER"}]}

        class DummyService:
            def __init__(self):
                self.db = DummyDb()
                self.source_service = DummySourceService()
                self.llm_service = SimpleNamespace(call_llm=self.call_llm)

            async def call_llm(self, *_args, **_kwargs):
                return "分析完成"

            @staticmethod
            def _needs_question_clarification(_question):
                return False

            @staticmethod
            def _format_conversation_history(_history):
                return "无"

            @staticmethod
            def _save_managed_skill_test_session(**kwargs):
                return SimpleNamespace(session_id="mstest_1")

        service = DummyService()
        orchestrator = ManagedSkillTestOrchestrator(service)
        orchestrator.context_loader = SimpleNamespace(load=lambda managed_skill_id, payload: SimpleNamespace(
            managed_skill=SimpleNamespace(skill_name="skill", use_count=0),
            llm_config=SimpleNamespace(timeout=30),
            skill_files={"SKILL.md": "# skill"},
            skill_markdown="# skill",
            existing_session=None,
            previous_turn_results=[],
            stored_conversation_history=[],
            conversation_history=[],
            source=SimpleNamespace(source_name="src"),
        ))

        async def fake_plan_and_execute(**_kwargs):
            return {
                "plan": {
                    "intent_type": "METRIC_EXPLANATION",
                    "planning_mode": "REFERENCE_PATTERN",
                    "reference_pattern_id": "reference_group_by_object",
                    "selected_objects": ["OUTBOUNDORDER", "DISTRIBUTOR"],
                    "steps": [{"step_id": "s1", "action": "select_root", "label": "OUTBOUNDORDER"}],
                },
                "selected_node": {"displayName": "OUTBOUNDORDER"},
                "evidence_tables": [],
                "executed_queries": [],
                "execution_events": [],
                "needs_replan": False,
            }

        orchestrator._plan_and_execute = fake_plan_and_execute
        orchestrator.response_builder = SimpleNamespace(
            build_analysis_prompt=lambda **kwargs: {"system_prompt": "s", "user_prompt": "u"},
            build_turn_response=lambda **kwargs: {
                "conversation": [{"role": "assistant", "content": "分析完成"}],
                "completion_assessment": {"summary": "完成"},
                "turn_results": [{"execution_events": []}],
            },
        )

        response = asyncio.run(orchestrator.execute_turn(
            "ms1",
            {
                "source_id": "src_1",
                "schema": "GYL",
                "test_question": "查询出库单关联哪些经销商，分别多少？",
                "sample_limit": 10,
            },
        ))

        event_types = [item.get("event_type") for item in response["execution_events"]]
        self.assertIn("REFERENCE_PATTERN_MATCHED", event_types)


if __name__ == "__main__":
    unittest.main()
