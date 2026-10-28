#!/usr/bin/env python3
"""session-sync.py -- a SessionStart hook: fast-forward a bound repository's main checkout to
origin's default branch, so its instruction files are current.

    python3 hooks/session-sync.py  < SessionStart event (JSON)

Acts only where the event's `cwd` is inside the main checkout (not a linked worktree) of a
repository with a `.claude/ouro.toml`. On the default branch (origin/HEAD) with no tracked changes
it runs `git fetch origin <default>` and `git merge --ff-only --no-overwrite-ignore`; Claude Code
has read the instructions already, so a move prints one line asking for a restart. A checkout it
cannot move (dirty, another branch, diverged, fetch or merge failed) is left as it is and the line
says why. It never stashes, resets or rebases, and always exits 0.

Stdlib only. tests/hooks/session_sync_tests.py is its test.
"""
import json
import os
import subprocess
import sys

STALE = "ouro: instructions may be stale: "


def git(cwd, *args, timeout=10):
    # a session of its own: ssh cannot prompt on the terminal Claude Code runs in
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
                       timeout=timeout, start_new_session=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    return r.returncode, r.stdout.strip()


def sync(cwd):
    """The notice line for this session, or None when there is nothing to say."""
    code, top = git(cwd, "rev-parse", "--show-toplevel")
    if code or not os.path.isfile(os.path.join(top, ".claude", "ouro.toml")):
        return None
    _, gitdir = git(top, "rev-parse", "--absolute-git-dir")
    _, common = git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if os.path.normcase(os.path.normpath(gitdir)) != os.path.normcase(os.path.normpath(common)):
        return None
    code, head = git(top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if code:
        return STALE + "origin/HEAD is unset (`git remote set-head origin --auto`)"
    default = head.removeprefix("origin/")
    _, branch = git(top, "branch", "--show-current")
    if branch != default:
        return STALE + f"the main checkout is on {branch or 'a detached HEAD'}, not {default}"
    code, dirty = git(top, "status", "--porcelain", "--untracked-files=no")
    if code or dirty:
        return STALE + "the main checkout has uncommitted changes"
    try:
        code, _ = git(top, "fetch", "--quiet", "origin", default, timeout=20)
    except subprocess.TimeoutExpired:
        code = 1
    if code:
        return STALE + f"git fetch origin {default} failed"
    _, before = git(top, "rev-parse", "HEAD")
    _, tip = git(top, "rev-parse", f"origin/{default}")
    if before == tip or git(top, "merge-base", "--is-ancestor", tip, before)[0] == 0:
        return None
    if git(top, "merge-base", "--is-ancestor", before, tip)[0]:
        return STALE + f"{default} has diverged from origin/{default}"
    # never killed partway: an interrupted merge leaves index.lock behind
    code, _ = git(top, "merge", "--ff-only", "--no-overwrite-ignore", "--quiet", tip, timeout=None)
    if code:
        return STALE + f"git merge --ff-only origin/{default} refused (local or ignored files in the way?)"
    return (f"ouro: fast-forwarded {default} {before[:7]}..{tip[:7]}; this session loaded the older "
            "instructions: restart it to load the current ones")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    try:
        line = sync(event.get("cwd") or os.getcwd())
    except (OSError, subprocess.TimeoutExpired) as error:
        line = STALE + f"the check could not run ({error})"
    if line:
        print(line)
    sys.exit(0)
