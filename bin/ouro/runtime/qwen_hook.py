"""Qwen Code PreToolUse hook: deny every tool not on the allow list, decide each shell command and each
write from the node's policy, and log each call it sees.

    python3 qwen_hook.py <allowed,tools> <evidence.jsonl> [<policy.json>]   (event JSON on stdin)

The policy is {"root", "commands", "probe", "edit"}. A write (write_file, edit, notebook_edit) needs
"edit" and a path inside the root, spelled and resolved, whose first component is none of
.git, .claude, .codex, .agents, .aws and .qwen in any case; a refused write's input is logged for the
manual step it becomes. A listed command must match exactly; git's query family
also matches its `git <sub> *` grant. A probe runs any other single command but git push, git commit
and gh. It refuses a word that names them, even quoted or as a dashed builtin (`git-push`); a git
`-c`, `--config-env` or `alias.` word; a `GIT_CONFIG` variable, which `env` passes to git; and any
`$`, glob or brace, which bash would expand into words this check never reads. A compound
command, a redirection, a substitution, a leading assignment, a background run or another directory
is refused either way. The check reads the names of the words bash runs, not what a nested
interpreter or a command that re-splits its argument does with it (`python3 -c`, `sh -c`, `env -S`),
nor a git binary under another name (a symlink or copy): the reach of Claude's `git push *` deny
pattern. So a probe's refusals stop a reviewer's slip, not a determined push.

Exit 2 is Qwen Code's blocking outcome; any other exit lets the call through. So a denial and a
fault both exit 2, and only a granted tool exits 0. Stdlib only: it runs as its own process,
outside the driver's package.
"""
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


def publishes(command, argv):
    """Whether bash would expand the command, it sets git config through the environment, or a word
    names gh, or git (a dashed builtin such as git-push included) with push, commit or config."""
    if any(c in command for c in "$*?[{") or "GIT_CONFIG" in command.upper():
        return True
    words = [word for token in argv for word in token.split()]
    names = [os.path.splitext(os.path.basename(word))[0].lower() for word in words]
    names += [part for name in names if name.startswith("git-") for part in name.split("-", 1)]
    return "gh" in names or "git" in names and bool({"push", "commit"} & set(names) or any(map(configures, words)))


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
    return "review cannot publish or commit, nor run a word bash expands" if publishes(command, argv) else None


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
