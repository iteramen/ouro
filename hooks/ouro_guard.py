"""ouro_guard.py -- what the guards under hooks/ share: the event read, the statement split, the
override comment and the entry point. Not a hook itself.

A guard is `python3 hooks/<guard>.py` with a PreToolUse event on standard input. It exits 2 with
the reason on stderr to refuse the call, and 0 to let it run: Claude Code treats every other exit
as a non-blocking error, so a guard raises nothing it can report instead.

Stdlib only; python3 3.11+.
"""
import json
import re
import sys

SHELL_TOOLS = ("Bash", "PowerShell")

# Words before the command name that name no command: variable assignments and the wrappers.
_PREFIX = re.compile(r"^(?:[A-Za-z_]\w*=\S*|sudo|nohup|exec|command|time|env|builtin|[({]+)\s+", re.IGNORECASE)
# A program a statement hands to something that runs it: an inline shell (`bash -c`, `bash -lc`,
# `pwsh -Command`), `eval`, or a command substitution anywhere in the statement, quoted or not.
_INNER_SHELL = re.compile(
    r"^(?:bash|sh|zsh|dash|ksh|pwsh|powershell)(?:\.exe)?\s+(?:-\S+\s+)*?(?:-[a-zA-Z]*c[a-zA-Z]*|-e|-CommandWithArgs)\s+"
    r"(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|(\S+))", re.IGNORECASE)
_EVAL = re.compile(r"^eval\s+(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|(.+))$", re.IGNORECASE)
_SUBSTITUTION = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`")


def read_event(stdin=None):
    """(tool_name, tool_input, cwd) of the event, or None when it is not a JSON object with a
    string tool_name; tool_input is a dict, empty when absent, and cwd a string."""
    raw = (sys.stdin.buffer.read() if stdin is None else stdin).decode("utf-8", "replace")
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(event, dict) or not isinstance(event.get("tool_name"), str):
        return None
    tool_input = event.get("tool_input")
    cwd = event.get("cwd")
    return event["tool_name"], tool_input if isinstance(tool_input, dict) else {}, cwd if isinstance(cwd, str) else ""


def shell_command(tool_name, tool_input):
    """The command text of a Bash or PowerShell call, else None."""
    if tool_name not in SHELL_TOOLS:
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def split(line):
    """[(segment, the operator after it)] of one line, cut at `||`, `&&`, `|&`, `;`, `|` and `&`
    outside quotes; the last operator is ''. A redirection's `&` (`2>&1`, `&>`) is not an operator,
    and a backslash escapes the character after it."""
    out, buf, i, quote = [], [], 0, None
    while i < len(line):
        c = line[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < len(line):
                buf.append(line[i + 1])
                i += 1
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < len(line):
            buf.append(c + line[i + 1])
            i += 2
            continue
        two = line[i:i + 2]
        if two in ("||", "&&", "|&"):
            out.append(("".join(buf), two))
            buf = []
            i += 2
            continue
        if c in ";|":
            out.append(("".join(buf), c))
            buf = []
            i += 1
            continue
        if c == "&":
            if (i and line[i - 1] == ">") or line[i + 1:i + 2] == ">":
                buf.append(c)
                i += 1
                continue
            out.append(("".join(buf), "&"))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    out.append(("".join(buf), ""))
    return out


def strip_prefix(statement):
    """The statement without its leading assignments and wrappers (`sudo`, `command`, `env`...)."""
    while True:
        stripped = _PREFIX.sub("", statement, count=1)
        if stripped == statement:
            return statement
        statement = stripped.lstrip()


def programs(statement):
    """The programs the statement hands to an inline shell, to `eval`, or to a command
    substitution: each is a command of its own that the shell runs."""
    found = []
    m = _INNER_SHELL.match(statement) or _EVAL.match(statement)
    if m:
        found.append(next(g for g in m.groups() if g is not None).replace('\\"', '"'))
    for m in _SUBSTITUTION.finditer(statement):
        found.append(next(g for g in m.groups() if g is not None))
    return found


def statements(command):
    """Each segment of the command between operators and line breaks, outside quotes, its leading
    assignments and wrappers dropped, with the line it sits on: [(statement, line)]. A program
    handed to an inline shell (`bash -c '...'`, `pwsh -Command "..."`), to `eval` or to a command
    substitution is split the same way. A command's name is the first word of a statement, so
    prose that mentions one mid-sentence is not a statement of it."""
    found = []
    for line in re.sub(r"\\\r?\n", " ", command).splitlines():
        for part, _ in split(line):
            part = strip_prefix(part.strip())
            if not part:
                continue
            found.append((part, line))
            for program in programs(part):
                found += statements(program)
    return found


def words(statement):
    """The statement's words, a quoted segment or a `$(...)` staying in its word, quotes dropped."""
    return [re.sub(r"[\"']", "", w) for w in re.findall(r"(?:\"[^\"]*\"|'[^']*'|\$\([^)]*\)|\S)+", statement)]


def first_word(statement):
    """The command name of a statement: its first word, without a directory or `.exe`, lowered."""
    word = statement.split(None, 1)[0].strip("\"'")
    word = word.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"\.exe$", "", word, flags=re.IGNORECASE).lower()


def override(command, key):
    """The text after `# <key>:` on any line of the command, or None."""
    m = re.search(r"#\s*" + re.escape(key) + r":\s*(\S[^\r\n]*)", command)
    return m.group(1).strip() if m else None


def run(check):
    """Entry point of a guard: `check(tool_name, tool_input, cwd)` returns the reason to refuse,
    or None. An event the guard cannot read lets the call run: the guard has nothing to judge."""
    event = read_event()
    if event is None:
        return 0
    try:
        reason = check(*event)
    except Exception as e:  # a guard's own defect is reported, never a silent allow
        reason = f"{type(e).__name__}: {e} (the guard failed; name the command literally or run it yourself)"
    if not reason:
        return 0
    sys.stderr.write(reason.rstrip("\n") + "\n")
    return 2
