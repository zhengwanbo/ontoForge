import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional


def extract_execution_events(result_payload: Dict[str, Any], turn_no: Optional[int] = None) -> List[Dict[str, Any]]:
    if not isinstance(result_payload, dict):
        return []
    turn_results = result_payload.get("turn_results")
    if isinstance(turn_results, list) and turn_results:
        if turn_no is not None:
            matched = next((item for item in turn_results if item.get("turn_no") == turn_no), None)
            if isinstance(matched, dict):
                events = matched.get("execution_events")
                if isinstance(events, list):
                    return events
        for item in reversed(turn_results):
            if not isinstance(item, dict):
                continue
            events = item.get("execution_events")
            if isinstance(events, list) and events:
                return events
    top_level_events = result_payload.get("execution_events")
    return top_level_events if isinstance(top_level_events, list) else []


def build_sse_frame(data: Dict[str, Any], event: str = "message") -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def replay_execution_events(
    events: List[Dict[str, Any]],
    *,
    session_id: str,
    turn_no: Optional[int] = None,
    delay_ms: int = 0,
) -> AsyncGenerator[str, None]:
    yield build_sse_frame(
        {
            "session_id": session_id,
            "turn_no": turn_no,
            "event_count": len(events),
        },
        event="start",
    )
    for index, item in enumerate(events):
        payload = {
            "session_id": session_id,
            "turn_no": turn_no,
            "event_index": index,
            "event": item,
        }
        yield build_sse_frame(payload, event="execution_event")
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
    yield build_sse_frame(
        {
            "session_id": session_id,
            "turn_no": turn_no,
            "event_count": len(events),
        },
        event="complete",
    )
