from __future__ import annotations

from typing import Any


def get_event_summary() -> dict[str, Any]:
    """Return the current event payload.

    TODO: Replace hardcoded placeholder with a real event data source.
    """
    return {
        "name": "风力发电场电气设计",
        "time": "18:00",
        "date": "2026-05-05",
        "location": "主楼B412",
    }
