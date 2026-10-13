#!/usr/bin/env python3
"""guard-worktree.py -- a PreToolUse guard on Bash and PowerShell: a worktree is removed by a
literal path that resolves, and a background command never pins a worktree.

    python3 hooks/guard-worktree.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2:

- `git worktree remove <path>` whose path holds a `$`, a `%...%` or a backtick, or does not
  resolve to a directory from the event's `cwd` (or the `-C` directory): a guard that cannot read
  the path cannot check what sits in the worktree, so it refuses rather than skips;
- a `Bash` call with `run_in_background` whose `cwd` is inside a `.claude/worktrees/` worktree and
  whose first statement is not `cd <absolute path>` to somewhere outside `.claude/worktrees/`,
  joined by `&&`, with no other change of directory after it: a background command
  inherits the shell's directory and holds the worktree for as long as it runs, so
  `git worktree remove` fails, or deregisters a directory the command still sits in.

Only a statement whose first word is `git` is read, so prose that mentions the command is not a
statement of it.

Stdlib only; python3 3.11+. The recorded events under tests/hooks/guard-worktree/ are its test.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

WORKTREES = re.compile(r"[/\\]\.claude[/\\]worktrees[/\\]", re.IGNORECASE)
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


def check_background(tool_name, tool_input, command, cwd):
    if tool_name != "Bash" or not tool_input.get("run_in_background") or not WORKTREES.search(cwd + "/"):
        return None
    parts = ouro_guard.split(command.splitlines()[0]) if command.strip() else []
    first, op = (parts[0][0].strip(), parts[0][1]) if parts else ("", "")
    ws = ouro_guard.words(first)
    # One leading cd to an absolute path outside the worktrees, joined by && (a `;` would run on
    # after a failed cd), and no other change of directory anywhere after it: `cd -`, a relative
    # cd or a cd in a subshell (`&`) leaves the background shell in the worktree.
    later = [s for s, _ in ouro_guard.statements(command)[1:] if ouro_guard.first_word(s) in ("cd", "pushd", "chdir", "set-location", "sl")]
    if len(ws) >= 2 and ws[0].lower() == "cd" and op == "&&" and not later:
        target = os.path.expanduser(ws[1])
        if re.match(r"^(?:[/\\]|[A-Za-z]:[/\\])", target) and not WORKTREES.search(target + "/"):
            return None
    return (f"a background command started from a worktree holds it for as long as it runs: begin it with "
            f"`cd <absolute path outside .claude/worktrees/> &&` (the main checkout or scratch) and change directory "
            f"nowhere after it, cwd is {cwd}")


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None:
        return None
    reason = check_background(tool_name, tool_input, command, cwd)
    if reason:
        return reason
    for statement, _ in ouro_guard.statements(command):
        if ouro_guard.first_word(statement) == "git":
            reason = check_remove(statement, cwd)
            if reason:
                return reason
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
