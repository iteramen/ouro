#!/usr/bin/env python3
"""guard-commit.py -- a PreToolUse guard on Bash and PowerShell: a commit carries the human who
ran the session as its author.

    python3 hooks/guard-commit.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2, wherever the event's `cwd` is: an author or
committer identity naming an agent (`claude`, `anthropic`, `copilot`, `codex`, `[bot]`,
`noreply@anthropic.com`, case-insensitive) in `git commit --author`, `git -c user.name=` or
`user.email=`, `git config user.name` or `user.email`, or a `GIT_AUTHOR_*` or `GIT_COMMITTER_*`
assignment; `--author="$AUTHOR"` passes, since a variable names nobody.

Stdlib only; python3 3.11+. The recorded events under tests/hooks/guard-commit/ are its test.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

AGENT = re.compile(r"claude|anthropic|copilot|codex|\[bot\]|noreply@anthropic\.com", re.IGNORECASE)
IDENTITY = re.compile(
    r"--author(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)"
    r"|-c\s+[\"']?user\.(?:name|email)=(\"[^\"]*\"|'[^']*'|\S+)"
    r"|\bconfig\b[^\n;|&]*?\buser\.(?:name|email)\s+(\"[^\"]*\"|'[^']*'|\S+)"
    r"|\bGIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)")


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None:
        return None
    for m in IDENTITY.finditer(command):
        value = next(g for g in m.groups() if g)
        if AGENT.search(value):
            return (f"an agent is never a commit's author: the identity {value} names one; a commit carries the human "
                    f"who ran the session, with no --author, user.name, user.email or GIT_*_NAME override naming an agent")
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
