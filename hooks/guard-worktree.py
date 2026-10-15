#!/usr/bin/env python3
"""guard-worktree.py -- a PreToolUse guard on Bash and PowerShell: a worktree is removed by a
literal path that resolves.

    python3 hooks/guard-worktree.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2:

- `git worktree remove <path>` whose path holds a `$`, a `%...%` or a backtick, or does not
  resolve to a directory from the event's `cwd` (or the `-C` directory): a guard that cannot read
  the path cannot check what sits in the worktree, so it refuses rather than skips.

Only a statement whose first word is `git` is read, so prose that mentions the command is not a
statement of it.

Stdlib only; python3 3.11+. The recorded events under tests/hooks/guard-worktree/ are its test.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

UNREADABLE = re.compile(r"[$`]|%\w+%")


def git_words(statement):
    """(the words after `git` and its options, the -C directory or None)."""
    ws = ouro_guard.words(statement)
    i, c_dir = 1, None
    while i < len(ws) and ws[i].startswith("-"):
        if ws[i] in ("-C", "-c") and i + 1 < len(ws):
            if ws[i] == "-C":
                c_dir = ws[i + 1]
            i += 1
        i += 1
    return ws[i:], c_dir


def check_remove(statement, cwd):
    rest, c_dir = git_words(statement)
    if rest[:2] != ["worktree", "remove"]:
        return None
    paths = [w for w in rest[2:] if not w.startswith("-")]
    if not paths:
        return f"git worktree remove names the worktree path literally: {statement}"
    base = os.path.join(cwd or os.getcwd(), c_dir) if c_dir else (cwd or os.getcwd())
    for p in paths:
        if UNREADABLE.search(p):
            return f"name the worktree path literally: `{p}` holds a variable or a substitution, and a path this guard cannot read it cannot check for a process sitting in it: {statement}"
        if not os.path.isdir(os.path.join(base, os.path.expanduser(p))):
            return f"name the worktree path literally: `{p}` does not resolve to a directory from {base}: {statement}"
    return None


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None:
        return None
    for statement, _ in ouro_guard.statements(command):
        if ouro_guard.first_word(statement) == "git":
            reason = check_remove(statement, cwd)
            if reason:
                return reason
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
