"""Bounded metadata for runtime attempts, independent of accepted node accounting."""
from collections import deque
from contextlib import contextmanager
import re
import time

from . import codex_events
from ..sh import DriverError

ATTEMPTS = deque(maxlen=128)


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) else None


def count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def events(row, output):
    """Project bounded transport output onto metadata; never retain event bodies."""
    transcript = codex_events.scan(output)
    if transcript.session is not None:
        row["session"] = identifier(transcript.session)
    if transcript.completed:
        row["provider_completed"] = True
        for target, source in (("input", "input_tokens"), ("output", "output_tokens"),
                               ("cache_read", "cached_input_tokens")):
            row[target] = count(transcript.usage.get(source))
    row["events"] = transcript.count


def transport(row, output, code):
    events(row, output)
    # Preserve signed process statuses and unsigned Windows DWORD/NTSTATUS values.
    row["exit"] = code if type(code) is int and -(2**31) <= code <= 2**32 - 1 else None


@contextmanager
def attempt(name, backend, model):
    row = dict.fromkeys(("session", "input", "output", "cache_read", "cache_creation", "cost",
                         "exit", "events"))
    row.update(node=identifier(name), backend=identifier(backend), model=identifier(model),
               provider_completed=None, phase="execution", outcome="failed", failure_class=None)
    start = time.monotonic()
    try:
        yield row
        row["outcome"] = "adapter_returned"
    except BaseException as ex:
        row["failure_class"] = ("driver_error" if isinstance(ex, DriverError) else
                                "interrupted" if isinstance(ex, (KeyboardInterrupt, SystemExit)) else "exception")
        raise
    finally:
        row["elapsed_seconds"] = round(max(0, time.monotonic() - start), 3)
        ATTEMPTS.append(row)


def table():
    if not ATTEMPTS:
        return ""
    keys = ("node", "backend", "model", "session", "elapsed_seconds", "provider_completed",
            "input", "output", "cache_read", "cache_creation", "cost", "exit", "events",
            "phase", "outcome", "failure_class")
    rows = ["Runtime attempt diagnostics (adapter return is not workflow acceptance):",
            "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    rows.extend("| " + " | ".join("unknown" if row[k] is None else str(row[k]) for k in keys) + " |"
                for row in ATTEMPTS)
    return "\n".join(rows)
