#!/usr/bin/env python3
"""check-agent-brief.py -- a PreToolUse guard on the Agent tool: a brief names the commit it
applies to, the path of its state note, and its turn budget.

    python3 hooks/check-agent-brief.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2, a dispatch whose prompt holds no head (a 7- to
40-digit hex commit, or the literal `[no-head]` for work tied to no commit), no state-note path
(the words `state note` on a line that holds a path), or no turn budget (`<n> turns`, or the
words `turn budget`). The read-only agent types pass with none of them: `Explore`, `Plan`,
`claude-code-guide`, `statusline-setup`, and `verifier` under any plugin prefix. Claude Code does
not apply an updated input to an Agent prompt, so the guard names what is missing and the
dispatcher adds it and dispatches again.

Stdlib only; python3 3.11+. The recorded events under tests/hooks/check-agent-brief/ are its test.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

READ_ONLY = re.compile(r"^(?:[\w-]+:)?(?:Explore|Plan|claude-code-guide|statusline-setup|verifier)$")
HEAD = re.compile(r"\b[0-9a-f]{7,40}\b|\[no-head\]", re.IGNORECASE)
STATE_NOTE = re.compile(r"state[ -]note[^\n]*?(?:[A-Za-z]:[\\/]|/|~[\\/]|\$\w+[\\/]|%\w+%)\S+", re.IGNORECASE)
TURNS = re.compile(r"\b\d+\s+turns?\b|turn[ -]budget", re.IGNORECASE)


def check(tool_name, tool_input, cwd):
    if tool_name != "Agent":
        return None
    kind = tool_input.get("subagent_type") or ""
    if READ_ONLY.match(str(kind)):
        return None
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return "the brief has no prompt text"
    missing = []
    if not HEAD.search(prompt):
        missing.append("the commit it applies to (a hex sha, or `[no-head]` for work tied to no commit)")
    if not STATE_NOTE.search(prompt):
        missing.append("the state-note path under the session's scratch directory (`state note: <path>`)")
    if not TURNS.search(prompt):
        missing.append("the turn budget (`<n> turns`)")
    if missing:
        return "contract §7: every brief names " + "; ".join(missing) + ". Add it and dispatch again."
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
