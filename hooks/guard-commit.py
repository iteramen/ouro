#!/usr/bin/env python3
"""guard-commit.py -- a PreToolUse guard on Bash and PowerShell: a commit carries the human who
ran the session as its author, a publishing call runs only once the step before it succeeded,
and a pull request opens as a draft.

    python3 hooks/guard-commit.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2:

- an author or committer identity naming an agent (`claude`, `anthropic`, `copilot`, `codex`,
  `[bot]`, `noreply@anthropic.com`, case-insensitive) in `git commit --author`, `git -c
  user.name=` or `user.email=`, `git config user.name` or `user.email`, or a `GIT_AUTHOR_*` or
  `GIT_COMMITTER_*` assignment; `--author="$AUTHOR"` passes, since a variable names nobody;
- `git commit`, `git push`, and `gh issue` or `gh pr` with a write verb (`create`, `new`,
  `comment`, `edit`, `close`, `reopen`, `merge`, `review`, `revert`, `ready`) when the operator
  before it is `;` or `&`, or when it is a stage of a pipeline (`|` or `|&` before or after it, a
  line ending in `|` included, a blank line or a continuation between notwithstanding); `&&`
  passes, a line of its own passes, an operator inside quotes is text, PowerShell's leading `&`
  is the call operator, a wrapper such as `command` or `env` is looked through, a program handed
  to an inline shell, `eval` or a command substitution is scanned the same way, and a here-doc
  body with a quoted delimiter is data;
- `gh pr create` without `--draft`.

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
HEREDOC = re.compile(r"<<-?\s*(['\"])([^'\"\n]+)\1")
GH_WRITE = {"create", "new", "comment", "edit", "close", "reopen", "merge", "review", "revert", "ready"}


def publishing(statement):
    """Whether the statement is a git commit, a git push, or a gh issue or pr write."""
    ws = ouro_guard.words(statement)
    name = ouro_guard.first_word(statement) if ws else ""
    if name == "git":
        i = 1
        while i < len(ws) and ws[i].startswith("-"):
            i += 2 if ws[i] in ("-C", "-c") else 1
        return ws[i:i + 1] in (["commit"], ["push"])
    if name == "gh":
        rest = [w for w in ws[1:] if not w.startswith("-")]
        return len(rest) >= 2 and rest[0] in ("issue", "pr") and rest[1] in GH_WRITE
    return False


def lines_of(command):
    """The command's lines with a quoted here-doc's body dropped: it is data, not a list."""
    out, delim = [], None
    for line in command.splitlines():
        if delim is not None:
            if line.strip() == delim:
                delim = None
            continue
        m = HEREDOC.search(line)
        if m:
            delim = m.group(2)
            line = line[:m.start()]
        out.append(line)
    return out


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None:
        return None
    for m in IDENTITY.finditer(command):
        value = next(g for g in m.groups() if g)
        if AGENT.search(value):
            return (f"an agent is never a commit's author: the identity {value} names one; a commit carries the human "
                    f"who ran the session, with no --author, user.name, user.email or GIT_*_NAME override naming an agent")
    return scan(command)


def scan(text):
    """The reason to refuse the first publishing call the text chains, pipes or opens as a
    non-draft, or None; a program the text hands to an inline shell, `eval` or a substitution is
    scanned the same way."""
    carried = None  # the operator a line ended with: a trailing `|` makes the next line a stage
    for line in lines_of(re.sub(r"\\\r?\n", " ", text)):  # a continuation is the same line
        if not line.strip():
            continue  # a blank line keeps a carried pipe
        segments = ouro_guard.split(line)  # outside quotes, so a `;` or `|` in a message is text
        for k, (part, after) in enumerate(segments):
            before = segments[k - 1][1] if k else carried
            # PowerShell's call operator is a leading `&` with nothing before it, not a chain.
            if before == "&" and k and not segments[k - 1][0].strip():
                before = None
            part = ouro_guard.strip_prefix(part.strip())  # `command git push`, `env X=1 git push`
            if not part or part.startswith("#"):
                continue
            for program in ouro_guard.programs(part):
                reason = scan(program)
                if reason:
                    return reason
            if not publishing(part):
                continue
            if before in (";", "&"):
                return (f"a commit, push or tracker write runs only once the step before it succeeded: chain it with "
                        f"&&, never after `;` or `&`: {part}")
            if before in ("|", "|&") or after in ("|", "|&"):
                return f"a commit, push or tracker write is never a stage of a pipeline: {part}"
            ws = ouro_guard.words(part)
            rest = [w for w in ws[1:] if not w.startswith("-")]
            if ouro_guard.first_word(part) == "gh" and rest[:2] in (["pr", "create"], ["pr", "new"]) and "--draft" not in ws:
                return f"a pull request opens as a draft (`gh pr create --draft`), and is marked ready once its checks and review are in: {part}"
        carried = segments[-2][1] if len(segments) > 1 and segments[-1][0].strip() == "" else None
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
