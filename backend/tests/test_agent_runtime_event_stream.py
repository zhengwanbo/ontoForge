import asyncio
import unittest

from app.services.agent_runtime.event_stream import (
    build_sse_frame,
    extract_execution_events,
    replay_execution_events,
)


class AgentRuntimeEventStreamTests(unittest.TestCase):
    def test_extract_execution_events_prefers_requested_turn(self):
        payload = {
            "turn_results": [
                {"turn_no": 1, "execution_events": [{"event_type": "STEP_STARTED", "title": "T1"}]},
                {"turn_no": 2, "execution_events": [{"event_type": "STEP_STARTED", "title": "T2"}]},
            ]
        }

        events = extract_execution_events(payload, turn_no=2)

        self.assertEqual([{"event_type": "STEP_STARTED", "title": "T2"}], events)

    def test_extract_execution_events_falls_back_to_latest_turn(self):
        payload = {
            "turn_results": [
                {"turn_no": 1, "execution_events": [{"event_type": "STEP_STARTED", "title": "T1"}]},
                {"turn_no": 2, "execution_events": []},
                {"turn_no": 3, "execution_events": [{"event_type": "STEP_COMPLETED", "title": "T3"}]},
            ]
        }

        events = extract_execution_events(payload)

        self.assertEqual([{"event_type": "STEP_COMPLETED", "title": "T3"}], events)

    def test_build_sse_frame_formats_event_and_data(self):
        frame = build_sse_frame({"ok": True}, event="execution_event")

        self.assertIn("event: execution_event", frame)
        self.assertIn('data: {"ok": true}', frame)

    def test_replay_execution_events_wraps_start_and_complete(self):
        async def collect() -> list[str]:
            chunks = []
            async for item in replay_execution_events(
                [{"event_type": "STEP_STARTED", "title": "Load"}],
                session_id="mstest_1",
                turn_no=3,
            ):
                chunks.append(item)
            return chunks

        chunks = asyncio.run(collect())

        self.assertEqual(3, len(chunks))
        self.assertIn("event: start", chunks[0])
        self.assertIn("event: execution_event", chunks[1])
        self.assertIn('"title": "Load"', chunks[1])
        self.assertIn("event: complete", chunks[2])


if __name__ == "__main__":
    unittest.main()
