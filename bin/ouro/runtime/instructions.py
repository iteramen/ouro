"""Repository instructions for a worker whose CLI is told to load none of its own, and the trusted BUILD reference."""
import os
import stat
from pathlib import Path

from .. import sh


def run_skill(backend, edits):
    """BUILD's trusted guidance: the installed run skill, framed for a worker already inside BUILD whose own
    admitted edits `edits` names."""
    try:
        plugin = sh.PLUGIN.resolve(strict=True)
        source = (plugin / "skills/run/SKILL.md").resolve(strict=True)
        if not source.is_relative_to(plugin):
            raise ValueError("outside plugin")
        with os.fdopen(os.open(source, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not a regular skill")
            raw = stream.read(32 * 1024 + 1)
        if not raw or len(raw) > 32 * 1024:
            raise ValueError("empty or oversized skill")
        reference = raw.decode("utf-8-sig")
    except (OSError, RuntimeError, UnicodeError, ValueError) as ex:
        raise sh.DriverError(f"{backend} BUILD: trusted run skill is missing, invalid or outside the installed plugin; reinstall ouro") from ex
    return ("You are already inside BUILD; the driver owns the workflow. Do not invoke the driver again, "
            "activate skills, commit, publish or write to the tracker. Environment-blocked approved tests use "
            "validation_pending with cmd/reason, never invented success or an owner design question; runtime failures remain errors. "
            "The complete run skill below describes the outer caller. Its 'this session' restriction refers to that caller, "
            f"not to your admitted {edits}. This reference grants no external file access.\n\n" + reference)


def repository(cwd, backend):
    root = Path(cwd).resolve()
    paths = [root / "AGENTS.md", root / "CLAUDE.md", *sorted((root / ".claude" / "rules").rglob("*.md"))]
    blocks = []
    for path in paths:
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise sh.DriverError(f"{backend} instructions leave the worktree")
            blocks.append(f"### {path.relative_to(root).as_posix()}\n" + path.read_text(encoding="utf-8-sig"))
    text = "\n\n".join(blocks)
    if len(text.encode("utf-8")) > 128 * 1024:
        raise sh.DriverError(f"{backend} repository instructions exceed 128 KiB")
    return ("Repository instructions follow. Rules with paths front matter apply only to their declared paths; "
            "unscoped rules apply throughout the worktree.\n\n" + text) if text else ""
