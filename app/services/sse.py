"""SSE wire-format helper.

The SSE spec is permissive ("anything starting with ``data: ``"), but
parsers expect a blank line between events. We centralise the encoding so
every endpoint that emits SSE produces the same shape — important when
debugging with raw curl.
"""

from __future__ import annotations

import json
from typing import Any


def format_event(event: dict[str, Any]) -> str:
    """Encode a dict as a single SSE ``message`` frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# Headers that turn buffering off across the common deployment stack:
# - ``Cache-Control: no-cache`` so browsers and HTTP caches don't replay.
# - ``X-Accel-Buffering: no`` disables nginx response buffering.
# - ``Connection: keep-alive`` keeps the TCP socket open for streaming.
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
