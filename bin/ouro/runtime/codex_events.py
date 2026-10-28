"""Bounded Codex JSON transcript projection; final answers remain a separate file."""
from dataclasses import dataclass, field
import io
import json
import math

CAPTURE_LIMIT = 64 * 1024 * 1024
FRAME_LIMIT = 8 * 1024 * 1024
FINAL_LIMIT = 4 * 1024 * 1024
MAX_FRAMES = 100000


@dataclass
class Transcript:
    session: object = None
    usage: dict = field(default_factory=dict)
    completed: bool = False
    failure: str | None = None
    error: str | None = None
    count: int = 0


def scan(output):
    result = Transcript()
    # Iterate without making a list of every frame or retaining MCP item bodies.
    for index, line in enumerate(io.StringIO(output)):
        if index >= MAX_FRAMES:
            result.error = "Codex event count limit exceeded"
            break
        if len(line.encode("utf-8", "replace")) > FRAME_LIMIT:
            result.error = "Codex event frame limit exceeded"
            continue  # Keep available later completion/failure metadata for diagnostics.
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict):
            continue
        result.count += 1
        kind = event.get("type")
        if kind == "thread.started" and result.session is None:
            value = event.get("thread_id")
            result.session = value if isinstance(value, str) and len(value) <= 128 else None
        elif kind == "turn.completed":
            result.completed = True
            usage = event.get("usage")
            if isinstance(usage, dict):
                result.usage = {key: usage[key] for key in (
                    "input_tokens", "output_tokens", "cached_input_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens", "cache_write_input_tokens", "cost_usd")
                    if key in usage and ((type(usage[key]) is int and 0 <= usage[key] <= 2**63 - 1)
                        or (key == "cost_usd" and type(usage[key]) is float
                            and math.isfinite(usage[key]) and 0 <= usage[key] <= 2**63 - 1))}
            else:
                result.usage = {}
        elif kind in ("turn.failed", "error"):
            message = event.get("error") or event.get("message")
            if isinstance(message, dict):
                message = message.get("message") or message.get("code")
            result.failure = (message[:1600] or kind) if isinstance(message, str) else kind
    return result
