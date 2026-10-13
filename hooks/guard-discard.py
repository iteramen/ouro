#!/usr/bin/env python3
"""guard-discard.py -- a PreToolUse guard on Bash and PowerShell: a git command that puts a
worktree file back from the index never drops work that was never staged.

    python3 hooks/guard-discard.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2: `git checkout -- <path>`, `git checkout <ref> --
<path>`, `git checkout <ref> <path>`, `git checkout .`, `git restore <path>` (with or without
`--worktree` or `--source`, not `--staged` alone) and `git restore .`, when a named path, or with `.` any file, has unstaged changes and
nothing staged (`git status --porcelain` reads ` M`, so the restore would drop the only copy of
the work), or when a path holds a variable or a substitution this guard cannot resolve. A path
with staged changes is the rule's own flow, since the restore returns the staged work, and
passes; `git restore --staged <path>` and `git checkout <branch>` pass. `# discard-ok: <why>` on
the command overrides, so an unwanted change can still be dropped on purpose. The status is read
with `git -C <cwd> status --porcelain -- <paths>`, from the `-C` directory where the command
gives one.

Only a statement whose first word is `git` is read, so prose that mentions the command is not a
statement of it.

Stdlib only; python3 3.11+. The recorded events under tests/hooks/guard-discard/ are its test.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

UNREADABLE = re.compile(r"[$`]|%\w+%")


def discarded_paths(statement):
    """The worktree paths a checkout or restore statement puts back from the index, or None when
    the statement is not such a command."""
    ws = ouro_guard.words(statement)
    i = 1
    while i < len(ws) and ws[i].startswith("-"):
        i += 2 if ws[i] in ("-C", "-c") else 1
    rest = ws[i:]
    if not rest:
        return None
    verb, args = rest[0], rest[1:]
    if verb == "checkout":
        if "--" in args:
            return args[args.index("--") + 1:]
        operands = [a for a in args if not a.startswith("-")]
        if any(a in ("-b", "-B", "--orphan", "--detach") for a in args):
            return None
        if operands[-1:] == ["."] and len(operands) == 1:
            return ["."]
        # `git checkout <ref> <path>...` restores the paths from the ref, with no `--` needed.
        return operands[1:] if len(operands) >= 2 else None
    if verb == "restore":
        opts = [a for a in args if a.startswith("-") and a != "--"]
        # --staged alone restores the index only; with --worktree too, or without --staged at all
        # (--source changes where from, not where to), the worktree copy is what goes.
        if any(o in ("-S", "--staged") for o in opts) and not any(o in ("-W", "--worktree") for o in opts):
            return None
        paths = args[args.index("--") + 1:] if "--" in args else [a for a in args if not a.startswith("-")]
        return paths or None
    return None


def unstaged_only(cwd, paths):
    """The paths whose status is ` M` (changed in the worktree, nothing staged), among `paths`."""
    r = subprocess.run(["git", "-C", cwd, "status", "--porcelain", "-z", "--untracked-files=no", "--", *paths],
                       capture_output=True, timeout=10)
    if r.returncode != 0:
        return []
    found = []
    for entry in r.stdout.decode("utf-8", "replace").split("\0"):
        if entry[:2] == " M":
            found.append(entry[3:])
    return found


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None or ouro_guard.override(command, "discard-ok"):
        return None
    for statement, _ in ouro_guard.statements(command):
        if ouro_guard.first_word(statement) != "git":
            continue
        paths = discarded_paths(statement)
        if not paths:
            continue
        for p in paths:
            if UNREADABLE.search(p):
                return f"a restore names its path literally: `{p}` is a variable this guard cannot resolve, so it cannot tell staged work from unstaged: {statement}"
        ws = ouro_guard.words(statement)
        base = cwd or os.getcwd()
        if "-C" in ws[:-1]:
            base = os.path.join(base, ws[ws.index("-C") + 1])
        lost = unstaged_only(base, paths)
        if lost:
            return (f"{', '.join(lost)}: changed in the worktree and nothing staged, so this restore would drop the only copy; "
                    f"`git add` it first (the restore then returns the staged work), or carry `# discard-ok: <why>`: {statement}")
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
