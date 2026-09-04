"""Reusable event-session codes for OraTranslate live sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SessionSlot:
    """One predictable code shared by the host and listener."""

    code: str
    label: str
    day: int
    period: str
    sequence: int


SESSION_SLOTS = (
    SessionSlot("DAY1-AM", "September 15 · Morning", 1, "morning", 1),
    SessionSlot("DAY1-PM", "September 15 · Afternoon", 1, "afternoon", 2),
    SessionSlot("DAY2-AM", "September 16 · Morning", 2, "morning", 3),
    SessionSlot("DAY2-PM", "September 16 · Afternoon", 2, "afternoon", 4),
)
SESSION_SLOT_BY_CODE = {slot.code: slot for slot in SESSION_SLOTS}


def normalize_session_code(value: Any) -> str:
    """Normalize a fixed event code without accepting arbitrary codes."""

    raw = "".join(
        character
        for character in str(value or "").upper()
        if character.isalnum()
    )
    return {
        "DAY1AM": "DAY1-AM",
        "DAY1PM": "DAY1-PM",
        "DAY2AM": "DAY2-AM",
        "DAY2PM": "DAY2-PM",
    }.get(raw, str(value or "").strip().upper())


class SessionCodeCatalog:
    """Describe reusable codes and the one active session, if present."""

    def snapshot(
        self,
        *,
        active_code: str | None = None,
        active_state: str | None = None,
    ) -> dict[str, Any]:
        normalized_active = normalize_session_code(active_code)
        if normalized_active not in SESSION_SLOT_BY_CODE:
            normalized_active = None
        return {
            "slots": [
                {
                    **asdict(slot),
                    "status": (
                        active_state or "prepared"
                        if slot.code == normalized_active
                        else "available"
                    ),
                }
                for slot in SESSION_SLOTS
            ],
            "active_code": normalized_active,
            "active_state": active_state if normalized_active else None,
        }
