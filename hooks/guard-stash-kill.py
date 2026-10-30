#!/usr/bin/env python3
"""guard-stash-kill.py -- a PreToolUse guard on Bash and PowerShell: the stash stack is shared by
every worktree of a repository, and a process is stopped by a PID the session recorded, never by
a pattern.

    python3 hooks/guard-stash-kill.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2:

- `git stash` in any form but `git stash push -m <message>` (`--message` too), `git stash apply
  <sha>` with a 7- to 40-digit hex sha, `git stash list` and `git stash show`: a bare `git stash`,
  `save`, `pop`, `drop`, `clear`, `branch`, `apply stash@{n}` and a `push` with no message, since
  a pop or a drop can take another session's entry and a nameless entry is nobody's;
- a process stopped by a pattern or a group: `pkill`, `killall`, `Stop-Process` with no `-Id`
  (`-Name`, or a pipeline feeding it), `taskkill /IM` or `/FI`, a `kill` on the same line as a
  `pgrep`, `pidof` or `ps` (a substitution, a pipe into `xargs kill`), a `kill` operand that is
  not a positive PID, a job (`%n`) or a variable (`$pid`, `$(cat run.pid)`), so `kill -- -<pgid>`,
  `kill -9 -<pgid>`, `kill -1` and `kill 0` are refused: a pattern matches every process on the
  host that holds it, another session's jobs and your own shell included; the listing is looked
  for anywhere in the command, a previous line included;
- a `kill` of a literal PID whose working directory lies in a `.claude/worktrees/` worktree other
  than the call's own, read from `/proc` on Linux and `lsof` on macOS (not read on Windows): a
  process in another worktree is another session's, unless the command carries
  `# pid-ok: <where the PID was recorded>`.

Stdlib only; python3 3.12+. The recorded events under tests/hooks/guard-stash-kill/ are its test.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

SHA = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
PID_OPERAND = re.compile(r"^(?:[1-9]\d*|%\d+|\$\w+|\$\{\w+\}|\$\([^)]*\)|`[^`]*`)$")
PROCESS_LISTERS = re.compile(r"(?<![\w$])(?:pgrep|pidof|ps|Get-Process|gps)\b", re.IGNORECASE)  # a command, not `$ps`


words = ouro_guard.words


def check_stash(statement):
    ws = words(statement)
    i = 1
    while i < len(ws) and ws[i].startswith("-"):
        i += 2 if ws[i] in ("-C", "-c") else 1
    if ws[i:i + 1] != ["stash"]:
        return None
    sub = ws[i + 1:]
    verb = sub[0] if sub else ""
    if verb in ("list", "show"):
        return None
    if verb == "push" and any(w in ("-m", "--message") or w.startswith(("-m", "--message=")) and len(w) > 2 for w in sub[1:]):
        return None
    if verb == "apply" and len(sub) == 2 and SHA.match(sub[1]):
        return None
    return (f"the stash stack is shared by every worktree of the repository, so a pop or a drop can take another "
            f"session's entry: `git stash push -m <message>` to put work there, `git stash apply <sha>` to take your "
            f"own back, or stage the work: {statement}")


def check_kill(name, statement, line, command, cwd):
    if name in ("pkill", "killall"):
        return f"a process is stopped by the PID you recorded when you started it, never by a pattern: {statement}"
    if name == "taskkill" and re.search(r"(?<!\S)/(?:im|fi)(?!\S)", statement, re.IGNORECASE):
        return f"a process is stopped by its PID (`taskkill /PID <pid>`), never by an image name or a filter: {statement}"
    if name == "stop-process" and not re.search(r"(?<!\S)-id(?!\S)", statement, re.IGNORECASE):
        return f"a process is stopped by its PID (`Stop-Process -Id <pid>`), never by name or by what a pipeline hands it: {statement}"
    if re.search(r"\bkill\b", line) and PROCESS_LISTERS.search(command):
        return f"a kill fed by a process listing is a kill by pattern: it matches another session's jobs and the shell doing the listing: {line.strip()}"
    if name == "kill":
        ws = words(re.sub(r"\s#.*$", "", statement))[1:]
        if "--" in ws:
            after = ws[ws.index("--") + 1:]
        else:
            after, skip = [], False
            for w in ws:
                if skip:
                    skip = False
                elif w in ("-s", "-n"):  # the signal, as a word of its own
                    skip = True
                elif not w.startswith("-"):
                    after.append(w)
        for w in after:
            if not PID_OPERAND.match(w):
                return f"a kill names a positive PID the session recorded; `{w}` is a group, every process, or a pattern: {statement}"
        # The first word may be the signal (`-9`, `-TERM`); a later `-<digits>` is a process group.
        if any(re.fullmatch(r"-\d+", w) for w in ws[1:] if w not in after) or any(re.fullmatch(r"-(?:1|0)", w) for w in ws[:1] if len(ws) == 1):
            return f"a negative number after the signal is a process group, and `kill -1` or `kill 0` reaches every process the session can signal: {statement}"
        if ouro_guard.override(command, "pid-ok"):
            return None
        for w in after:
            if w.isdigit():
                where = process_cwd(int(w))
                if where and WORKTREES.search(where + "/") and not under_same_worktree(where, cwd):
                    return (f"PID {w} sits in {where}, another session's worktree, so it is another session's process; a kill "
                            f"names a PID this session recorded, and carries `# pid-ok: <where it was recorded>` when it is: {statement}")
    return None


WORKTREES = re.compile(r"[/\\]\.claude[/\\]worktrees[/\\]", re.IGNORECASE)


def process_cwd(pid):
    """The working directory of a live process: /proc on Linux, lsof elsewhere, None on Windows
    or where the process cannot be read."""
    try:
        if os.path.isdir("/proc"):
            return os.readlink(f"/proc/{pid}/cwd")
        if os.name != "nt":
            r = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, timeout=5)
            for ln in r.stdout.decode("utf-8", "replace").splitlines():
                if ln.startswith("n"):
                    return ln[1:]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


def under_same_worktree(where, cwd):
    """Whether the process's directory lies in the worktree the event's cwd lies in."""
    # lsof reports the resolved path (macOS /private/var), the event the spelled one (/var).
    where, cwd = os.path.realpath(where), os.path.realpath(cwd)
    m = WORKTREES.search(cwd + "/")
    if not m:
        return False
    own = cwd[:m.end()] + (cwd[m.end():] + "/").split("/", 1)[0]
    w = where.replace("\\", "/").rstrip("/") + "/"
    return w.lower().startswith(own.replace("\\", "/").lower() + "/") if os.name == "nt" else w.startswith(own + "/")


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None:
        return None
    for statement, line in ouro_guard.statements(command):
        name = ouro_guard.first_word(statement)
        reason = check_stash(statement) if name == "git" else check_kill(name, statement, line, command, cwd)
        if reason:
            return reason
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
