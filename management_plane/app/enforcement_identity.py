"""Agent-call enforcement identity normalization for Prism telemetry."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import IntentEvent


@dataclass(frozen=True)
class EnforcementIdentity:
    agent_call_id: str
    event_id: str
    missing_fields: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.missing_fields


def _trimmed(value: str | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def normalize_enforcement_identity(
    event: IntentEvent,
    *,
    fallback_request_id: str,
) -> EnforcementIdentity:
    """
    Normalize the only Prism telemetry identities we accept.

    agent_call_id is the parent for one agent request. event_id is one
    interception inside that agent request. Runtime conversation/session ids are
    intentionally ignored and are not copied into telemetry.
    """
    agent_call_id = _trimmed(event.agent_call_id)
    event_id = _trimmed(event.event_id)
    missing: list[str] = []

    if not agent_call_id:
        missing.append("agent_call_id")
        agent_call_id = f"missing-agent-call:{fallback_request_id}"
    if not event_id:
        missing.append("event_id")
        event_id = f"missing-event:{fallback_request_id}"

    event.agent_call_id = agent_call_id
    event.event_id = event_id
    event.id = event_id
    event.identity.principal_id = agent_call_id

    if not isinstance(event.runtime_identity, dict):
        event.runtime_identity = {}
    event.runtime_identity.update(
        {
            "agent_call_id": agent_call_id,
            "event_id": event_id,
        }
    )

    return EnforcementIdentity(
        agent_call_id=agent_call_id,
        event_id=event_id,
        missing_fields=tuple(missing),
    )
