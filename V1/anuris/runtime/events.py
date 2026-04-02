from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_runtime_event(event_type: str, **payload: Any) -> Dict[str, Any]:
    event = {
        "event_id": uuid4().hex,
        "timestamp": utc_timestamp(),
        "type": event_type,
    }
    event.update(payload)
    return event
