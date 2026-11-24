"""Qwen Code PreToolUse hook: deny every tool not on the allow list, decide each shell command and each
write from the node's policy, and log each call it sees.

    python3 qwen_hook.py <allowed,tools> <evidence.jsonl> [<policy.json>]   (event JSON on stdin)

The policy is {"root", "commands", "probe", "edit"}. A write (write_file, edit, notebook_edit) needs
"edit" and a path inside the root, spelled and resolved, whose first component is none of
.git, .claude, .codex, .agents, .aws and .qwen in any case; a refused write's input is logged for the
manual step it becomes. A listed command must match exactly; git's query family
also matches its `git <sub> *` grant. A probe runs any other single command but git push, git commit
and gh. It refuses a word that names them, even quoted or as a dashed builtin (`git-push`); in a
command that names git, a `-c`, `--config-env` or `alias.` word, or a `GIT_CONFIG` variable, which
`env` passes to git; any `$`, which bash would expand into words this check never reads; and a glob
or brace word whose basename, its brace sets expanded, can match git, gh or a `git-<sub>` name, `.exe`
or not; a word with a quote or a backslash after an unquoted `[`, or anywhere beside an unquoted `{`,
a bracket member or brace character this check does not read; and any whitespace but space and tab, which bash keeps inside a word. The words a brace word expands to are the words the name check reads. A compound
command, a redirection, a substitution, a leading assignment, a background run or another directory
is refused either way. The check reads the names of the words bash runs, not what a nested
interpreter or a command that re-splits its argument does with it (`python3 -c`, `sh -c`, `env -S`),
nor a git binary under another name (a symlink or copy): the reach of Claude's `git push *` deny
pattern. So a probe's refusals stop a reviewer's slip, not a determined push.

Exit 2 is Qwen Code's blocking outcome; any other exit lets the call through. So a denial and a
fault both exit 2, and only a granted tool exits 0. Stdlib only: it runs as its own process,
outside the driver's package.
"""
import fnmatch
import json
import os
import re
import shlex
import sys

SHELL = "run_shell_command"
GIT_QUERIES = ("rm", "mv", "status", "diff", "log", "ls-files")
OPERATORS = set("();<>|&")
WRITES = {"write_file": "file_path", "edit": "file_path", "notebook_edit": "notebook_path"}
PROTECTED = {".git", ".claude", ".codex", ".agents", ".aws", ".qwen"}
EXPANSIONS = 256  # a brace word that makes more words is refused unread
SUBCOMMAND = "abcdefghijklmnopqrstuvwxyz0123456789-"


def simple(command):
    """The words of one simple command, or None for a compound, redirected, substituted or assigning one."""
    if any(c in command for c in "`\n\r") or "$(" in command:
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # bash reads a `#` inside a word literally, where shlex would drop the rest of the line
    try:
        argv = list(lexer)
    except ValueError:
        return None
    # shortcut: a quoted argument made only of operator characters is refused too; rare in a real command.
    if not argv or any(set(token) <= OPERATORS for token in argv) or re.match(r"[A-Za-z_]\w*=", argv[0]):
        return None
    return argv


def configures(word):
    """Whether a word in a git command sets config inline or names an alias, either of which can alias a push."""
    return word == "-c" or word.startswith("--config-env") or word.lower().startswith("alias.")


def brace_items(word):
    """The first brace group bash expands in `word`, as (start, end, items), or None."""
    for start, c in enumerate(word):
        if c != "{":
            continue
        depth, cuts = 0, [start]
        for end in range(start, len(word)):
            depth += {"{": 1, "}": -1}.get(word[end], 0)
            if word[end] == "," and depth == 1:
                cuts.append(end)
            if depth == 0:
                break
        else:
            continue
        if len(cuts) > 1:
            return start, end, [word[a + 1:b] for a, b in zip(cuts, [*cuts[1:], end])]
        bounds = re.fullmatch(r"(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])(?:\.\.-?\d+)?", word[start + 1:end])
        if bounds:  # the whole range, whatever its increment: a superset of bash's words
            a, b = (int(x) if x.lstrip("-").isdigit() else ord(x) for x in bounds.groups())
            if abs(b - a) >= EXPANSIONS:
                return start, end, None
            items = range(min(a, b), max(a, b) + 1)
            return start, end, [str(i) if bounds[1].lstrip("-").isdigit() else chr(i) for i in items]
    return None


def expand(word):
    """The words bash's brace expansion makes of `word`, or None past EXPANSIONS of them."""
    pending, done = [word], []
    while pending:
        current = pending.pop()
        group = brace_items(current)
        if group is None:
            done.append(current)
            continue
        start, end, items = group
        if items is None:
            return None
        pending += [current[:start] + item + current[end + 1:] for item in items]
        if len(pending) + len(done) > EXPANSIONS:
            return None
    return done


PREFIXES = ("g", "gi", "git", "gh", "git-", ".e", ".ex", ".exe")
ENDS = ("git", "gh", "git-", "sub")  # the states a name can end in, before an optional `.exe`


def step(state, c):
    """The state after the lower-case character `c` in reading git, gh or git-<sub>, then `.exe` or not; None off them."""
    if state + c in PREFIXES:
        return state + c
    if c in SUBCOMMAND and state in ("git-", "sub"):
        return "sub"
    return "." if c == "." and state in ENDS else None


def names_git(pattern):
    """Whether a glob word's basename can match git, gh or a dashed builtin `git-<sub>` (`.exe` or not), in any case:
    a filesystem that ignores case runs each spelling."""
    pattern = os.path.basename(pattern).replace("[^", "[!")
    if any(s in pattern for s in ("[:", "[=", "[.")):  # a class, equivalence class or collating symbol fnmatch does not read
        return True
    states = {""}
    for token in re.findall(r"\[!?\]?[^\]]*\]|.", pattern, re.S):
        if token == "*":
            while (more := states | {step(s, c) for s in states for c in SUBCOMMAND + "."} - {None}) != states:
                states = more
        else:
            chars = SUBCOMMAND + SUBCOMMAND.upper() + "."
            states = {step(s, c.lower()) for s in states for c in chars if fnmatch.fnmatchcase(c, token)} - {None}
    return bool(states & {*ENDS, ".exe"})


def quoted_member(command):
    """Whether a word holds a quote or a backslash after an unquoted `[`, or anywhere in a word with an unquoted `{`:
    bash reads the character it quotes as a literal bracket member (`[\\!iI]` is no negation) or brace character
    (`{push,"-o}"}` is one group), where the words this check reads have lost the quoting."""
    quote, bracket, brace, quoted = None, False, False, False
    chars = iter(command)
    for c in chars:
        if quote:
            quote = None if c == quote else quote
            if c == "\\" and quote == '"':
                next(chars, None)
        elif c in "'\"\\":
            if bracket or brace:
                return True
            quoted = True
            if c == "\\":
                next(chars, None)
            else:
                quote = c
        elif c.isspace():
            bracket = brace = quoted = False
        elif c == "[":
            bracket = True
        elif c == "{":
            if quoted:
                return True
            brace = True
    return False


def publishes(command, argv):
    """Whether bash would expand a `$`, a glob or brace word can expand into git or gh, a quote hides a bracket member
    or brace character from this check, or a word names gh,
    or git (a dashed builtin such as git-push included) with push, commit, config or GIT_CONFIG."""
    if "$" in command or quoted_member(command):
        return True
    words = []
    for word in (word for token in argv for word in token.split()):
        expanded = expand(word)
        if expanded is None or (expanded != [word] or any(c in word for c in "*?[")) and any(map(names_git, expanded)):
            return True
        words += expanded
    names = [os.path.splitext(os.path.basename(word))[0].lower() for word in words]
    names += [part for name in names if name.startswith("git-") for part in name.split("-", 1)]
    return "gh" in names or "git" in names and bool({"push", "commit"} & set(names) or any(map(configures, words))
                                                    or "GIT_CONFIG" in command.upper())


def refusal(args, policy):
    """Why the shell call `args` is refused under `policy`, or None."""
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "no command"
    if args.get("is_background"):
        return "background commands are not granted"
    root = os.path.realpath(policy["root"])
    directory = args.get("directory")
    if directory and os.path.realpath(os.path.join(root, directory)) != root:
        return "commands run in the node's worktree only"
    argv = simple(command)
    if argv is None:
        return "one simple command per call: no chaining, pipes, redirection, substitution or leading assignment"
    wildcard = f"git {argv[1]} *" if len(argv) >= 2 and argv[0] == "git" and argv[1] in GIT_QUERIES else None
    if command.strip() in policy["commands"] or wildcard in policy["commands"]:
        return None
    if not policy["probe"]:
        return "command is not on the node's approved list"
    # bash splits words on space and tab alone, where str.split() and isspace() also cut at \v, \f or U+00A0
    if any(c.isspace() and c not in " \t" for c in command):
        return "review runs no whitespace but space and tab, which bash keeps inside a word"
    return "review cannot publish or commit, nor run a `$` or a word bash could expand into git or gh" if publishes(command, argv) else None


def write_refusal(path, policy):
    """Why a write to `path` is refused under `policy`, or None."""
    if not policy.get("edit"):
        return "editing is not granted to this node"
    if not isinstance(path, str) or not path.strip():
        return "no path"
    root = os.path.realpath(policy["root"])
    target = os.path.join(root, path)
    for spelled in (os.path.abspath(target), os.path.realpath(target)):
        first = os.path.relpath(spelled, root).split(os.sep)[0]
        if first in (os.curdir, os.pardir):
            return "edits stay inside the node's worktree"
        if first.lower() in PROTECTED:
            return "protected path: give its edit under manual instead"
    return None


def main(argv, stdin, stderr):
    allowed, evidence = set(argv[1].split(",")), argv[2]
    event = json.load(stdin)
    tool = event.get("tool_name")
    entry = {"tool": tool}
    reason = None if isinstance(tool, str) and tool in allowed else "tool not granted to this node"
    if reason is None and (tool == SHELL or tool in WRITES):
        args = event.get("tool_input")
        if not isinstance(args, dict):
            raise ValueError(f"{tool} call without an input object")
        with open(argv[3], encoding="utf-8") as stream:
            policy = json.load(stream)
        if tool == SHELL:
            entry["command"] = args.get("command")
            reason = refusal(args, policy)
        else:
            entry["path"] = args.get(WRITES[tool])
            reason = write_refusal(entry["path"], policy)
            if reason:
                entry["input"] = args
    entry["granted"] = reason is None
    with open(evidence, "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")
    if reason is None:
        return 0
    stderr.write(f"ouro: {reason}\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv, sys.stdin, sys.stderr))
    except Exception as ex:  # fail closed
        sys.stderr.write(f"ouro qwen hook: {ex}\n")
        sys.exit(2)
