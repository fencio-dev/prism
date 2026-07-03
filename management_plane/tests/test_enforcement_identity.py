from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.enforcement_identity import normalize_enforcement_identity
from app.models import AgentIdentity, IntentEvent


def _intent(
    *,
    agent_call_id: str | None = "agent-call-1",
    event_id: str | None = "event-1",
) -> IntentEvent:
    return IntentEvent(
        event_type="tool_call",
        id="runtime-event-ignored",
        agent_call_id=agent_call_id,
        event_id=event_id,
        ts=1710000000.0,
        identity=AgentIdentity(
            agent_id="agent-1",
            principal_id="runtime-value-ignored",
            actor_type="agent",
        ),
        source_agent="agent-1",
        source_layer="input",
        destination_agent="agent-1",
        destination_layer="llm",
        t="llm",
        op="prompt_build",
        p="hello",
    )


def test_normalize_enforcement_identity_uses_agent_call_and_event_ids() -> None:
    event = _intent()

    identity = normalize_enforcement_identity(
        event,
        fallback_request_id="request-1",
    )

    assert identity.is_valid
    assert identity.agent_call_id == "agent-call-1"
    assert identity.event_id == "event-1"
    assert event.id == "event-1"
    assert event.identity.principal_id == "agent-call-1"
    assert event.runtime_identity == {
        "agent_call_id": "agent-call-1",
        "event_id": "event-1",
    }


def test_normalize_enforcement_identity_marks_missing_fields() -> None:
    event = _intent(agent_call_id=None, event_id=None)

    identity = normalize_enforcement_identity(
        event,
        fallback_request_id="request-2",
    )

    assert not identity.is_valid
    assert identity.missing_fields == ("agent_call_id", "event_id")
    assert identity.agent_call_id == "missing-agent-call:request-2"
    assert identity.event_id == "missing-event:request-2"
    assert event.id == "missing-event:request-2"
    assert event.identity.principal_id == "missing-agent-call:request-2"
