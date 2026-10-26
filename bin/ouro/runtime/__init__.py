"""Harness implementations share capabilities; they do not own workflow transitions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Capabilities:
    edit: bool = False
    commands: tuple = ()
    probe: bool = False

READ = Capabilities()
PROBE = Capabilities(probe=True)


def builder(commands):
    return Capabilities(True, tuple(f"git {c} *" for c in ("rm", "mv", "status", "diff", "log", "ls-files")) + tuple(commands))
