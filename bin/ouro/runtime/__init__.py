"""Harness implementations share capabilities; they do not own workflow transitions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Capabilities:
    edit: bool = False
    commands: tuple = ()
    probe: bool = False
    sandbox: bool = False  # ship.review_sandbox: the backend's sandbox route confines the probe commands

# A refused call, as every adapter returns it in answer["_denied"]: tool_name write, edit, delete or run
# (another tool keeps its own name), and tool_input with file_path, content (write), old and new (edit)
# or command (run). run.Run.manual_steps and acceptance_answer read only this shape.

READ = Capabilities()
PROBE = Capabilities(probe=True)
SANDBOXED_PROBE = Capabilities(probe=True, sandbox=True)


def builder(commands):
    return Capabilities(True, tuple(f"git {c} *" for c in ("rm", "mv", "status", "diff", "log", "ls-files")) + tuple(commands))
