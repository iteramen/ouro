#!/usr/bin/env python3
"""forbidden-tokens.py -- check text against a private list of forbidden patterns that lives
outside every repository, and scan the text this plugin ships.

    python3 forbidden-tokens.py check  < text
    python3 forbidden-tokens.py scan
    python3 forbidden-tokens.py hook   < PreToolUse event (JSON)
    python3 forbidden-tokens.py --selftest

The list is one UTF-8 file (a byte-order mark is accepted) that the environment variable
OURO_FORBIDDEN_TOKENS names. Each line that is not blank and does not start with `#` is one entry,
a regular expression compiled case-insensitive and searched in each line of the text; where an
entry is a common word, its author writes the word boundaries into it. Whitespace around an entry
is dropped. Entries are numbered from 1 in file order, comments and blank lines not counted. A
comment line `# protects: <owner/repo> ...` names the repositories the list protects, for the hook
below; the list may hold several, and one with none protects every repository.

A list is missing when the variable is unset or empty, the file cannot be read, is not UTF-8 or
holds a NUL character, holds no entry, or an entry does not compile; the message names the cause
and, for an entry, its line number in the file, and never the entry itself or the compiler's text.
An entry's own regex cost is its author's: a pathological entry can stall the scan.

check   Reads standard input as UTF-8 (a byte-order mark is dropped) and prints
        `stdin:<line>: entry <n>` for each line, a trailing carriage return removed, that an entry
        matches, and `stdin: entry <n>, across a line break` for an entry that matches only once
        whitespace runs are collapsed to one space. Exit 0 when nothing matches, 1 on a hit,
        2 on a missing list or on input that is not UTF-8.
scan    Reads every file `git ls-files -z` lists under the top-level skills/, agents/, templates/
        and docs/ of the working directory's repository, from any working directory, as UTF-8,
        and prints `<path>:<line>: entry <n>` for each hit; a tracked file that is not UTF-8 is
        printed as `<path>: not UTF-8`, and one that cannot be opened (a submodule) as
        `<path>: cannot be read`, each counting as a hit. Each tracked path is matched too and
        reported as `path <k> of git ls-files: entry <n>`, k being the 1-based position in that
        list, never the path; every other report line of such a file names it `path <k>` in
        place of its path. An entry that matches a file's text only once whitespace runs are
        collapsed to one space is reported as `<path>: entry <n>, across a line break`. An empty
        file list is an error. Exit 0 when clean, 1 on a hit or an empty list. On a missing list
        it exits 1 and prints the cause, unless the environment variable CI is exactly `true`:
        then it prints one line starting `SKIP:` that names the missing list, and exits 0. A CI
        runner has no list, so this scan checks nothing there; the check runs on the machines
        that hold the list.
hook    A Claude Code PreToolUse hook, registered by `hooks/hooks.json`, which the plugin manifest
        names (README.md, `The private token list`). It reads the event's JSON on standard input and
        acts on a Bash or PowerShell call whose `tool_input.command` holds `gh issue create`
        (or `new`), `comment`, `edit`, `close` or `reopen`, `gh pr create` (or `new`), `edit`,
        `comment`, `review`, `merge`, `close`, `reopen` or `revert`, or `git commit`. `gh` and
        `git` are matched in any case with an optional path and `.exe`; gh global flags may
        precede the noun and flags may sit between noun and verb; git options before `commit`
        are included. For such a call it checks the whole command text, a heredoc included, and
        the contents of a file named by `--body-file`, `-F` or `--file` (spaced or `=` form
        anywhere, `-F<file>` attached only outside quotes in the publishing call's own statement,
        resolved as the shell would: `~`, a Git Bash drive path on Windows, and a relative path
        under each directory the call lands in). A value of `-`, or a path under `/dev/` or `/proc/` once
        slashes, `.` and `..` are normalised (as given and joined to the event's `cwd`), is input
        the hook cannot see and passes only when the whole command is one publishing gh or git
        call whose first line ends in a quoted heredoc delimiter, `<<'DELIM'` or `<<"DELIM"`
        (`<<-` allowed; an unquoted delimiter never passes), holds no `;`, `&`, `|`, `<`, `>`,
        parenthesis, brace, backtick, `#` or `$'`, and has balanced quotes before the operator,
        followed by the heredoc body and the closing delimiter and nothing else. Exit 0 lets the
        call run: no hit, or any other tool call or command. The command text is scanned less the
        value of each `-R` or `--repo` whose flag stands outside quotes before any heredoc, which
        is the address the call targets; with unbalanced quotes the whole text is scanned.
        Exit 2 blocks it, and Claude Code shows stderr to the session as the reason: a hit
        (`command text line <l>: entry <n>`, `command text: entry <n>, across a line break`,
        `the file <flag> names, line <l>: entry <n>`, or `the file <flag> names: entry <n>,
        across a line break`), a missing list where a repository the command lands in (the working
        directory's or a target's, below) may not publish without one (below),
        the reason named, a body file it cannot read, a body it cannot see,
        a payload it cannot parse, or any error of its own. Any other nonzero exit would let the
        call run, so this mode has none. It does not see `gh api`, nor a value expanded at run
        time. A body file is read before the command runs, so a write to it earlier in that command
        is not seen. A body or
        later command that mentions `--file`, `--body-file` or a spaced `-F` in prose is read as
        a file name and blocks. A hook whose `python3` cannot be found exits 127, and one that
        exceeds Claude Code's hook timeout does not block either: Claude Code treats both as
        non-blocking, so a missing interpreter or a stall disables the hook.
        The hook follows `ship.forbidden_check` on the binding at each directory the call lands
        in (the event's `cwd`, each `cd`, `chdir`, `pushd` or `Set-Location` target, with any
        options before it and anything after it, at the start of a segment, a brace group, a shell
        keyword or the string a `bash -c` or `pwsh -Command` takes, each `git … commit`'s (and an
        `env -C` or `env --chdir` before it) `-C`, `--git-dir` and `--work-tree` target and each
        GIT_DIR or GIT_WORK_TREE assignment, a `.git` directory read as its clone, chained in order;
        a `cd` inside quotes, after a heredoc opens, piped, sent to the background, or in a command
        whose quotes do not balance, keeps the directories before it; every one must pass), read
        through the ouro-binding.py beside this script: a binding that passes `check` and declares
        `ship.forbidden_check = "off"` passes the call, a hit on the list included. With a list
        that names the repositories it protects, a hit also passes at a directory whose binding
        passes `check` and declares no key (or that has no binding) when neither its repository
        (the binding's `repo.slug`, else `git remote get-url origin`) nor any repository the
        command names (a `-R`, its value attached included, `--repo` or `GH_REPO` value, or an
        issue or pull request URL) is one of them; a repository that cannot be resolved is
        scanned. With no list,
        the call also passes when the binding passes `check` and declares no key (or there is no
        binding) and `gh repo view` of the binding's `repo.slug`, else that directory's
        repository, reports PRIVATE or INTERNAL. A binding that fails `check`, `"required"`, PUBLIC,
        no `gh` on PATH, and a read that fails or runs past the reads' shared budget of READ_BUDGET
        (6) seconds, inside the hook's own 10-second timeout, block; the binding and origin are
        read only when a hit or a missing list would. `"off"` counts only when the command names
        no other repository, since the key is honoured only for the repository the run posts to: a
        call naming another is read as if the key were absent, and with no list the visibility read
        is that repository's. For these reads every `-R` or `--repo` in the command text counts,
        one inside a quoted body included, and with a `# protects:` line every repository the
        command names as above: it fails closed.
--selftest  Runs each mode against a temporary list and repository.

A hit is reported by path, line number and entry number, never by the matched text or the entry,
since gate output is pasted into pull request bodies.

Stdlib only; Python 3.12+.
"""
import bisect
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys

# Claude Code treats exit 2 as a refusal; other errors can let a publishing call through.
if tuple(sys.version_info[:2]) < (3, 12):
    sys.stderr.write(f"forbidden-tokens.py needs Python 3.12+; this python is {sys.version_info[0]}.{sys.version_info[1]}\n")
    sys.exit(2)

import tempfile
import time

VAR = "OURO_FORBIDDEN_TOKENS"
SCAN_DIRS = ("skills", "agents", "templates", "docs")


class MissingList(Exception):
    pass


def load_list(environ=None):
    """(entries, protects): the compiled entries, and the repositories `# protects:` lines name,
    as slug() spells them; an empty set when the list has no such line."""
    environ = os.environ if environ is None else environ
    path = environ.get(VAR, "")
    if not path:
        raise MissingList(f"{VAR} is not set")
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8-sig")
        if "\0" in text:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "NUL")
    except OSError as e:
        raise MissingList(f"{VAR} names a file that cannot be read ({e.strerror or type(e).__name__})")
    except UnicodeDecodeError:
        raise MissingList(f"the file {VAR} names is not UTF-8")
    entries, protects = [], set()
    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if line.startswith("# protects:"):
            protects.update(slug(s) for s in line[len("# protects:"):].split())
        if not line or line.startswith("#"):
            continue
        try:
            entries.append(re.compile(line, re.IGNORECASE))
        except Exception:
            raise MissingList(f"line {lineno} of the file {VAR} names holds an entry that does not compile")
    if not entries:
        raise MissingList(f"the file {VAR} names holds no entry")
    return entries, protects


def hits(text, entries):
    """(line number, entry number) for each line an entry matches, in order."""
    found = []
    for lineno, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        for n, rx in enumerate(entries, 1):
            if rx.search(line):
                found.append((lineno, n))
    return found


def wrapped(text, entries, found):
    """Numbers of the entries that match the whitespace-collapsed text but no single line."""
    lined = {n for _, n in found}
    collapsed = re.sub(r"\s+", " ", text)
    return [n for n, rx in enumerate(entries, 1) if n not in lined and rx.search(collapsed)]


def out(line):
    sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def cmd_check():
    try:
        entries, _ = load_list()
    except MissingList as e:
        print(f"forbidden-tokens: missing list: {e}", file=sys.stderr)
        return 2
    try:
        text = sys.stdin.buffer.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        print("forbidden-tokens: standard input is not UTF-8", file=sys.stderr)
        return 2
    found = hits(text, entries)
    for lineno, n in found:
        out(f"stdin:{lineno}: entry {n}")
    across = wrapped(text, entries, found)
    for n in across:
        out(f"stdin: entry {n}, across a line break")
    return 1 if found or across else 0


_WS = r"(?:\\\r?\n|`\r?\n|\s)+"
# Any words may sit between `git` and `commit` (options, quoted or substituted values, continuations), so
# a commit is never missed; but `git` directly followed by a subcommand that never commits makes the word
# `commit` an argument (`git grep commit`, `git diff -- commit.py`). Options before the subcommand keep the
# broad reading, so a directory named like a subcommand cannot hide a commit.
_GIT_READS = (r"(?!(?:grep|log|diff|show|blame|status|rev-parse|rev-list|ls-files|ls-tree|cat-file|describe|"
              r"shortlog|reflog|help|merge-base|name-rev|for-each-ref|show-ref|whatchanged|range-diff|cherry)(?![\w-]))")
_FLAGS = "(?:" + _WS + r"--?[A-Za-z0-9][\w-]*(?:=\S+|" + _WS + r"[^\s-]\S*)?)*"
PUBLISH = re.compile(
    r"(?<![\w-])(?i:gh(?:\.exe)?)[\"']?" + _FLAGS + _WS + r"(?:"
    r"issue" + _FLAGS + _WS + r"(?:create|new|comment|edit|close|reopen)|"
    r"pr" + _FLAGS + _WS + r"(?:create|new|edit|comment|review|merge|close|reopen|revert))(?![\w-])|"
    r"(?<![\w-])(?i:git(?:\.exe)?)[\"']?\s+" + _GIT_READS + r"(?:[^\s;&|]+\s+)*?commit(?![\w-])")
BODY_FILE = re.compile(
    r"""(?<!\S)(--body-file|--file|-F)(?:=|(?:\\\r?\n|\s)+)("[^"]*"|'[^']*'|[^\s;&|)]+)""")
ATTACHED = re.compile(r"""(?<!\S)(-F)(?=[^\s=])("[^"]*"|'[^']*'|[^\s;&|)]+)""")
STATEMENT_END = re.compile(r"&&|\|\||;|\n|\||(?<![<>&])&(?![>&])")
HEREDOC_LINE = re.compile(r"""(?<=\s)<<(-?)(?:'([^'\n]*)'|"([^"\n]*)")\s*\Z""")


def lone_heredoc(command):
    """Whether the whole command is one publishing gh or git invocation whose stdin is its own
    heredoc: a first line holding no shell metacharacter or `#`, whose part before the operator
    splits as shell words and which ends in `<<'DELIM'`, `<<"DELIM"` or `<<-` of either, then the
    body, the closing delimiter, and nothing after it."""
    head, nl, rest = command.partition("\n")
    m = HEREDOC_LINE.search(head)
    if not nl or not m:
        return False
    line = head[:m.start()]
    p = PUBLISH.search(line)
    if re.search(r"[;&|<>(){}`#]|\$'", line) or not p or not re.fullmatch(r"\s*(?:\S*[/\\])?", line[:p.start()]):
        return False
    try:
        shlex.split(line, posix=True)
    except ValueError:
        return False
    delim = m.group(2) if m.group(2) is not None else m.group(3)
    lines = rest.split("\n")
    for k, text in enumerate(lines):
        text = text.rstrip("\r")
        if (text.lstrip("\t") if m.group(1) else text) == delim:
            return not "".join(lines[k + 1:]).strip()
    return False


def body_flags(command):
    """(flag, value) for each body file flag: spaced and `=` forms anywhere, the attached `-F<file>`
    only outside quotes in a publishing call's own statement, which ends at the first unquoted `&&`,
    `||`, `;`, `|`, `&` or line break (at the end of its line when the quotes do not balance)."""
    found = [(m.start(), m.group(1), m.group(2)) for m in BODY_FILE.finditer(command)]
    spans = quoted_spans(command)
    quoted = lambda at: spans is not None and any(s <= at < e for s, e in spans)
    for p in PUBLISH.finditer(command):
        eol = command.find("\n", p.start())
        end = len(command) if eol < 0 else eol
        if spans is not None:
            end = next((m.start() for m in STATEMENT_END.finditer(command, p.end()) if not quoted(m.start())), len(command))
        found += [(m.start(), m.group(1), m.group(2)) for m in ATTACHED.finditer(command, p.start(), end) if not quoted(m.start())]
    return [(f, v) for _, f, v in sorted(set(found))]


class Block(Exception):
    pass


READ_BUDGET = 6  # seconds for every binding and visibility read of one event together: the registered hook's own timeout is 10


def reader(cwd, deadline=None):
    """A bounded reader of child output for one event: every read shares READ_BUDGET from the
    first call; output goes to files, not pipes (a stalled read is killed at the deadline, and a
    pipe a grandchild still holds would make the wait outlive the kill); a read past the deadline,
    timed out or failing to start answers (None, "")."""
    deadline = deadline or time.monotonic() + READ_BUDGET

    def read(args):
        left = deadline - time.monotonic()
        if left <= 0:
            return None, ""
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                r = subprocess.run(args, stdout=out, stderr=err, timeout=left, cwd=cwd)
            except (subprocess.TimeoutExpired, OSError):
                return None, ""
            stream = out if r.returncode == 0 else err
            stream.seek(0)
            return r.returncode, stream.read().decode("utf-8", "replace")
    return read


def policy(read):
    """The binding's say on the check, read through the ouro-binding.py beside this script:
    ("off", [], "") for a binding that passes `check` and declares
    `ship.forbidden_check = "off"` and ("absent", [slug], "") for one that passes and declares no
    key, the slug being the binding's repo.slug; ("absent", [], "") with no binding; ("required",
    [], why) for every other case, why naming it: the binding's own key, a binding that fails
    `check` (the check's first line), a read that fails or runs past the budget."""
    binding = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ouro-binding.py")
    tool = [sys.executable, binding]
    code, text = read(tool + ["check"])
    if code is None:
        return "required", [], "the binding could not be read within the hook's budget"
    if code != 0:
        if text.startswith(("error: no binding at", "error: not inside a git work tree")):
            return "absent", [], ""
        return "required", [], "binding fails check: " + (text.strip().splitlines() or ["(no output)"])[0]
    code, text = read(tool + ["get", "ship.forbidden_check"])
    if code is None:
        return "required", [], "the binding's ship.forbidden_check could not be read within the hook's budget"
    if code == 0:
        if text.rstrip("\r\n") != "off":
            return "required", [], "the binding requires the check (ship.forbidden_check)"
        kind = "off"
    elif not text.startswith("error: no such key: ship.forbidden_check"):
        return "required", [], "the binding's ship.forbidden_check could not be read"
    else:
        kind = "absent"
    code, text = read(tool + ["get", "repo.slug"])
    if code is None:
        return "required", [], "the binding's repo.slug could not be read within the hook's budget"
    if code != 0 or not text.strip():
        return "required", [], "the binding's repo.slug could not be read"
    return kind, [text.rstrip("\r\n")], ""


TARGET = re.compile(r"(?:^|\s)(?:-R|--repo)(?:\s+|=)(?:\"([^\"]+)\"|'([^']+)'|(\S+))")
# TARGET with the value attached to -R too: read only for repositories, never blanked from the scan
REPO_FLAG = re.compile(r"(?:^|\s)(?:-R(?:\s+|=)?|--repo(?:\s+|=))(?:\"([^\"]+)\"|'([^']+)'|(\S+))")
GH_REPO = re.compile(r"(?<![\w-])GH_REPO\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|\"'<>]+))")
ISSUE_URL = re.compile(r"https?://[^\s/\"']+/([^\s/\"']+)/([^\s/\"']+)/(?:issues|pull)/\d", re.I)
# an origin that resolves: a scheme and host, or an scp host, before owner/repo; a local path or a bare name does not
HOSTED = re.compile(r"(?:\w[\w+.-]*://[^/\s]+/|[^/:\s]{2,}:)[^/\s]+/[^/\s]+/?$")


def slug(name):
    """owner/repo in lower case from a slug, a URL or a remote's scp spelling: a host prefix and a
    trailing `.git` or slash dropped."""
    return "/".join(re.sub(r"(?:\.git)?/*$", "", name.replace(":", "/")).split("/")[-2:]).lower()


def named_repos(command):
    """Every repository a -R or --repo anywhere on the command names, as owner/repo in lower case
    (a host prefix dropped), a quoted body's mention included, so the binding and visibility reads
    fail closed."""
    return ["/".join((m[1] or m[2] or m[3]).strip("/").split("/")[-2:]).lower() for m in TARGET.finditer(command)]


def addressed(command):
    """Every repository the command names for a `# protects:` list, as slug() spells it: a -R (its
    value attached included), --repo or GH_REPO value, or an issue or pull request URL, anywhere on
    the command, a quoted body's mention included, so the read fails closed."""
    return ([slug(next(g for g in m.groups() if g)) for r in (REPO_FLAG, GH_REPO) for m in r.finditer(command)]
            + ["/".join(m).lower() for m in ISSUE_URL.findall(command)])


def in_prose(spans, text, start, end):
    """Whether a verb found at [start, end) of text sits inside one of the sorted, disjoint quoted spans as prose:
    it does unless the span is the verb's own quotes, closed inside the match and holding no blank."""
    i = bisect.bisect_right(spans, (start, float("inf"))) - 1
    return i >= 0 and start < spans[i][1] and not (start == spans[i][0] and spans[i][1] <= end and not re.search(r"\s", text[start:spans[i][1]]))


def quoted_spans(command):
    """The [start, end) spans inside single or double quotes, or None when the quotes do not balance."""
    spans, start, quote, i = [], 0, None, 0
    while i < len(command):
        c = command[i]
        if quote is None and c in "'\"":
            quote, start = c, i
        elif quote == '"' and c in "\\`":
            i += 1
        elif c == quote:
            spans.append((start, i + 1))
            quote = None
        i += 1
    return None if quote else spans


def without_targets(command):
    """The command with the value of each -R or --repo whose flag stands outside quotes, before any
    heredoc, blanked to spaces: it is the address the call targets, not content it publishes. The
    whole text when the quotes do not balance, so the scan fails closed."""
    head = command.split("<<", 1)[0]
    spans = quoted_spans(head)
    if spans is None:
        return command
    out = list(command)
    for m in TARGET.finditer(head):
        flag = m.start() + (m.group(0)[:1].isspace())
        if any(a <= flag < b for a, b in spans):
            continue
        g = next(k for k in (1, 2, 3) if m.group(k) is not None)
        out[m.start(g):m.end(g)] = " " * (m.end(g) - m.start(g))
    return "".join(out)


def other_repo(command, slug):
    """The first repository the command names that is not the binding's, or None."""
    return next((t for t in named_repos(command) if t != slug.lower()), None)


def check_off(cwd, command, protects, deadline=None):
    """True when a hit passes at cwd: the binding there turns the check off for the repository the
    command posts to, or, with the list's `# protects:` set non-empty and the binding's say absent,
    neither cwd's repository (repo.slug, else origin) nor any repository the command names
    (addressed) is in it. A repository that cannot be resolved is scanned. With the set empty, the
    names `"off"` weighs are named_repos'. A hit passes when every directory the call lands in says
    so."""
    read = reader(cwd, deadline)
    kind, slugs, _ = policy(read)
    targets = addressed(command) if protects else named_repos(command)
    if kind == "off" and all(t == slugs[0].lower() for t in targets):
        return True
    if kind == "required" or not protects or any(t in protects for t in targets):
        return False
    if not slugs:
        code, text = read(["git", "remote", "get-url", "origin"])
        slugs = [text.strip()] if code == 0 and HOSTED.match(text.strip()) else []
    return bool(slugs) and slug(slugs[0]) not in protects


SEGMENT = re.compile(r"&&|\|\||;|\n|\(|\)")
# a NAME=value prefix word: bare, quoted and ANSI-C parts, an escaped character inside any of them read as text,
# += too; possessive, so a word that fails is given up without backtracking into its parts
PREFIX_WORD = r"[A-Za-z_]\w*\+?=(?:\$'(?:[^'\\]|\\.)*'|\$\w+|\"(?:[^\"\\]|\\.)*\"|'[^']*'|\\.|[^\s\"'\\`$])*+"
# the prefix words a segment opens with, as far as the reader follows them; what they stop at says whether the
# rest is a command word (read on) or a part the reader does not follow: a backtick, a `${` or `$(`, an unpaired quote
PREFIX_SCAN = re.compile(r"\s*(?:" + PREFIX_WORD + r"\s+)*+(?:" + PREFIX_WORD + r")?+")
ASSIGN_OPENS = re.compile(r"\s*[A-Za-z_]\w*\+?=")
KEYWORDS = r"builtin|command|exec|if|elif|then|else|do|while|until|time"
# a run of prefix tokens (assignment words, keywords, braces) too long to read: refused before the cd reader,
# whose work per start grows with the run
PREFIX_RUN = re.compile(r"(?:(?<!\S)(?:" + PREFIX_WORD + "|" + KEYWORDS + r"|[{&!])\s+){65}")
# the verb, spelled bare or with the quotes and escapes bash removes before it runs the word ('cd', $'cd', $"cd", c\d, c''d, \cd)
SPELL = r"(?:\$?['\"]|\\)*+"
VERB = SPELL + r"(?:" + "|".join(SPELL.join(map(re.escape, v)) for v in ("cd", "chdir", "pushd", "sl", "Set-Location", "Push-Location")) + r")" + SPELL
# a segment whose prefix words stop at a part the reader does not follow, with a bare cd verb after it: refused, not skipped;
# a match starts only where a run of quotes or escapes starts, so a run no verb follows fails once, not once per position
CD_VERB = re.compile(r"(?<![\w'\"\\$-])" + VERB + r"\s+\S", re.I)
CD_HEAD = (r"(?:\s|[{&!]|(?:" + KEYWORDS + r")(?=\s)|" + PREFIX_WORD + r"(?=\s))*+"
           r"(?P<verb>" + VERB + r")(?:\s+(?:-[^\s\"':]*|/d))*")
CD = re.compile(CD_HEAD + r"(?:\s+-[a-z]+:|\s+)(?P<dir>(?:\"[^\"]*\"|\$?'[^']*'|[^\s\"';&|<>])+)(?P<tail>.*)$", re.I)
# a cd verb with nothing readable after it: the segment was cut at a `(` (a command substitution or a
# PowerShell expression) or at a segment separator, or only a quote or a `$` follows; it lands somewhere the hook cannot name
CD_BARE = re.compile(CD_HEAD + r"(?:\s+-[a-z]+:)?\s*[\"']?\$?\s*$", re.I)
# a pipe or a background & after the directory (not a redirect's, as in 2>&1 or &>)
FORKED = re.compile(r"\||(?<![<>&])&(?![>&])")
# the string (or bare word) a child shell runs: `bash -c`, `bash -lc`, `pwsh -Command`, `cmd /c`, `eval`, `iex`, `Invoke-Expression`
SHELL_STRING = re.compile(r"(?<!\S)(?:[-/](?:[a-z]*c|c[a-z]*)|eval|iex|Invoke-Expression)\s+[\"']?", re.I)
GIT_SPAN = re.compile(r"(?<![\w-])(?:env\s+(?:[^\s;&|]+\s+)*?)?[\"']?(?i:git(?:\.exe)?)[\"']?\s+" + _GIT_READS + r"(?:[^\s;&|]+\s+)*?commit(?![\w.-])")
GIT_OPT = re.compile(r"(?<!\S)(-C|--chdir|--git-dir|--work-tree)(=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|\"']+))")
GIT_ENV = re.compile(r"(?<![\w-])GIT_(?:DIR|WORK_TREE)\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|\"'<>]+))", re.I)
MAX_BASES = 32


def join_dir(base, rel):
    """rel against base; an MSYS drive spelling (/c/…) is read as C:/ on Windows."""
    if os.name == "nt":
        rel = re.sub(r"^/([A-Za-z])/", lambda m: m.group(1).upper() + ":/", rel)
    return os.path.normpath(os.path.join(base, rel))


def at_home(word, unquoted):
    """An unquoted leading `~` or `~/` read as the home directory, HOME first (Git Bash's `~` on Windows)
    and the platform's home otherwise, as bash reads it; `~user` and a quoted `~` stay literal, and so does
    every `~` when no home is known."""
    if not unquoted or not (word == "~" or word.startswith("~/")):
        return word
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return word if home == "~" else home + word[1:]


def work_tree(d):
    """A path naming a .git directory, read as the clone it belongs to."""
    return os.path.dirname(d) if os.path.basename(d) == ".git" else d


def segments(command):
    """(offset, text) of the command's segments between &&, ||, ;, newlines and parentheses, quotes
    disregarded: a split inside a quoted span can only add a directory to judge, never remove one."""
    out, last = [], 0
    for m in SEGMENT.finditer(command):
        out.append((last, command[last:m.start()]))
        last = m.end()
    out.append((last, command[last:]))
    return out


def target_dirs(command, cwd):
    """Every directory a publishing call may land in besides cwd: landing_dirs' keys."""
    return list(landing_dirs(command, cwd))


def landing_dirs(command, cwd):
    """The directories a publishing command lands in, after cwd, each mapped to how a `cd` reached
    it (`unexpanded` for `cd -` or a shell variable, `prose` for one in quoted text or after a
    heredoc opens, else empty), for the refusal to name: a `cd`, `chdir`, `pushd` or
    `Set-Location` command moves the base where it opens a segment or the string a child shell
    runs (`bash -c`, `bash -lc`, `pwsh -Command`, `cmd /c`, `eval`, `iex`, `Invoke-Expression`,
    quoted or a bare word, an escaped quote inside it read as text), after a `{`, `&`, `!`,
    `builtin`, `command`, a shell keyword or one or more `NAME=value` prefix assignments (cd is a
    regular builtin, so the directory change outlives them; a value bare, quoted or ANSI-C, an
    escaped character inside any part read as text; a prefix shape the reader cannot follow, a
    backtick or `${…}` part with a space or a quote it cannot pair, before a cd verb is refused as
    unreadable, and so is a run of more than 64 prefix words), and the -C
    options of each `git … commit` (an
    `env -C` or `env --chdir` before it included, the git inside a child's string too) chain from
    it; its --git-dir and --work-tree,
    and a GIT_DIR or GIT_WORK_TREE assignment, name one more (a .git directory read as its
    clone). A `cd` inside quotes, after a heredoc opens, piped, sent to
    the background, in a child's string or bare word, or in a command whose quotes do not balance,
    adds a base and keeps the ones before it, since it may be prose, a child shell's, or
    PowerShell's, whose pipe moves it. A `cd` whose directory reads empty (spliced quotes) or is
    cut away (a command substitution, a parenthesised expression) is refused as unreadable, or
    read as a directory that does not exist and refused for that: it lands somewhere unknown. A
    bare `cd` at the very end of the command lands in the home directory and is not judged; one
    with more after it is refused as unreadable, like a cut one. An unquoted
    leading `~` or `~/` in a directory, a space-separated -C value or a GIT_DIR/GIT_WORK_TREE value
    is the home directory (HOME, else the platform's), as bash reads it; `~user`, a quoted `~`, a
    `~` after `=` in an option word and every `~` in a command that assigns HOME stay literal, and
    are refused as missing where they do not resolve.
    Every one is judged, and the call passes only when all pass, so a mention in a quoted body
    cannot redirect the judgement away from where the call lands."""
    if PREFIX_RUN.search(command):
        raise Block("a run of more than 64 prefix words before a command, too many to read")
    spans = quoted_spans(command)
    heredoc = command.find("<<")
    # a command that assigns HOME changes what `~` means for what follows (a statement) or leaves it to the
    # old home (a prefix, bound after the expansion): the hook reads neither value, so `~` stays literal
    home_set = re.search(r"(?<![\w-])HOME\s*=", command) is not None
    bases, out = [cwd], {}  # directory -> how a cd reached it: "unexpanded", "prose" or ""
    for at, seg in segments(command):
        # once per segment; a word inside a quoted span is prose, unless the span is the verb's own quotes, closed inside its match
        verbs = [v.start() for v in CD_VERB.finditer(seg) if not (spans and in_prose(spans, command, at + v.start(), at + v.end()))]
        for e in GIT_ENV.finditer(seg):
            for b in bases:
                out.setdefault(work_tree(join_dir(b, at_home(next(g for g in e.groups() if g), bool(e.group(3)) and not home_set))), "")
        for pos in [0] + [s.end() for s in SHELL_STRING.finditer(seg)]:
            m = CD.match(seg, pos)
            if not m:
                b = CD_BARE.match(seg, pos)
                # a bare cd with nothing but separators after it ends the command: it lands in the home directory, where nothing
                # publishes, and is not judged; one with more of the command after it is refused
                if b and command[at + len(seg):].strip(" \t\r\n;&|()"):
                    raise Block(f"a {b.group('verb')} whose directory cannot be read, so the directory the call lands in is unknown")
                end = PREFIX_SCAN.match(seg, pos).end()
                if ASSIGN_OPENS.match(seg, pos) and end < len(seg) and seg[end] in "`$\"'" and any(v >= end for v in verbs):
                    raise Block("a cd behind prefix assignments the hook cannot read, so the directory the call lands in is unknown")
                continue
            word = m.group("dir")
            if pos:  # the word ends where the child's string does: an escaped quote inside it is not that end
                word = word.replace("\\" + seg[pos - 1], "\0").split(seg[pos - 1])[0].replace("\0", seg[pos - 1])
            bare = (word == "~" or word.startswith("~/")) and not home_set  # on the raw word: a quoted ~, even ~""/x, is literal
            word = re.sub(r"\"([^\"]*)\"|\$?'([^']*)'", lambda q: q.group(1) or q.group(2) or "", word)
            if not word:  # a spliced or empty directory: it lands somewhere, and the hook cannot say where
                raise Block(f"a {m.group('verb')} whose directory cannot be read, so the directory the call lands in is unknown")
            moved = [join_dir(b, at_home(word, bare)) for b in bases]
            keeps = (pos > 0 or spans is None or -1 < heredoc < at or FORKED.search(m.group("tail"))
                     or any(a <= at + m.start("verb") < b for a, b in spans))
            bases = list(dict.fromkeys(bases + moved if keeps else moved))
            if len(bases) > MAX_BASES:
                raise Block(f"the command moves through more than {MAX_BASES} directories inside quotes, too many to judge")
            # how the refusal names a directory that is not there: a word the hook does not expand, else a cd it read in
            # text it cannot tell from a script (quoted, or after a heredoc opens)
            raw = re.sub(r"'[^']*'", "", m.group("dir"))  # a $ inside single quotes is literal to the shell too
            how = ("unexpanded" if word == "-" or "$" in raw else
                   "prose" if pos == 0 and (-1 < heredoc < at or any(a <= at + m.start("verb") < b for a, b in spans or ())) else "")
            for d in moved:
                out.setdefault(d, how)
        if "commit" not in seg.lower():
            continue
        for span in GIT_SPAN.finditer(seg):
            for d in bases:
                named = []  # git resolves --git-dir and --work-tree after every -C
                for o in GIT_OPT.finditer(span.group(0)):
                    # bash leaves a ~ after = in an option word literal: only a space-separated bare value expands
                    v = at_home(next(g for g in o.groups()[2:] if g), bool(o.group(5)) and o.group(2) != "=" and not home_set)
                    if o.group(1) in ("-C", "--chdir"):
                        d = join_dir(d, v)
                    else:
                        named.append(v)
                for t in [d] + [work_tree(join_dir(d, v)) for v in named]:
                    out.setdefault(t, "")
    return {d: how for d, how in out.items() if d != cwd}


def missing_dir(where, how, e):
    """The no-list refusal for a landing directory that is not there, naming why the hook judged it and what clears it."""
    head = f"the directory {where} does not exist, so the repository the call lands in cannot be judged, and the machine has no list ({e})"
    if how == "unexpanded":
        return (f"{head}: the hook reads a `cd -` or a `cd` to a shell variable as a literal directory, since it expands neither; "
                "run the call from that directory, or `cd` to it by its literal path")
    if how == "prose":
        return (f"{head}: it is a `cd` in quoted text or after a heredoc opens, which the hook cannot tell from a script; "
                "if it is text, reword it so no line opens with `cd`; if it is a real `cd`, create the directory first, "
                "or run the call where the machine has a list")
    return head


def refusal(why, where, e):
    """The no-list refusal: a binding that fails its check is the one reason; a binding that requires
    the check is the missing list; any other reason names the list the machine lacks beside it."""
    if why.startswith("binding fails check"):
        return f"{why} (at {where})"
    if why.startswith("the binding requires the check"):
        return f"missing list: {e} ({why}, at {where})"
    return f"{why}, and the machine has no list ({e})"


def publishes_unchecked(cwd, command, deadline=None):
    """(True, "") when a publishing call judged at cwd may run with no list: the binding there
    turns the check off for the repository the command posts to, or the key is absent (no binding,
    no key, or `"off"` for another repository than the command names) and `gh repo view` reports
    the repository (the one named on the command, else the binding's repo.slug, else cwd's)
    PRIVATE or INTERNAL. Otherwise (False, why), why naming the reason: the binding's say
    (`"required"`, a binding that fails `check`, a read that fails or runs past the shared
    READ_BUDGET), no `gh` on PATH, a visibility read that failed, or the visibility it reported."""
    read = reader(cwd, deadline)
    kind, slug, why = policy(read)
    target = other_repo(command, slug[0]) if slug else next(iter(named_repos(command)), None)
    if kind == "off":
        if target is None:
            return True, ""
        kind = "absent"
    if kind != "absent":
        return False, why
    if target is not None:
        slug = [target]
    gh = shutil.which("gh")
    if gh is None:
        return False, "no gh on PATH to read the repository's visibility"
    code, text = read([gh, "repo", "view", *slug, "--json", "visibility"])
    name = slug[0] if slug else "the repository at " + cwd
    if code is None:
        return False, f"gh repo view of {name} did not answer within the hook's budget"
    if code != 0:
        return False, f"gh repo view of {name} failed"
    try:
        vis = json.loads(text)["visibility"]
    except (ValueError, KeyError, TypeError):
        return False, f"gh repo view of {name} gave no visibility"
    if vis in ("PRIVATE", "INTERNAL"):
        return True, ""
    return False, f"{name} is {vis}, not private"


def shell_path(path, tool):
    """A path as the shell resolves it: `~` expanded, and on Windows a Git Bash drive path (`/c/...`)."""
    path = os.path.expanduser(path)
    drive = re.fullmatch(r"/([A-Za-z])(/.*)?", path) if os.name == "nt" and tool == "Bash" else None
    return f"{drive.group(1)}:{drive.group(2) or '/'}" if drive else path


def body_paths(value, command, cwd, tool):
    """Where a body-file argument may point: itself when absolute, else under each directory the call lands in."""
    value = shell_path(value, tool)
    if os.path.isabs(value):
        return [value]
    return [os.path.join(shell_path(d, tool), value) for d in dict.fromkeys([cwd, *landing_dirs(command, cwd)])]


def hook_check(payload):
    """Reasons to block the event, or an empty list. Raises Block for a cause that is not a hit."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
        raise Block("the event is not a JSON object with a tool_name")
    if payload["tool_name"] not in ("Bash", "PowerShell"):
        return []
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        raise Block("the event holds no tool_input.command text")
    if not PUBLISH.search(command):
        return []
    # A call is judged at every directory it lands in: the working directory and each `cd` or
    # `git -C` target. A directory that is not there is a refusal, not a guess.
    cwd = payload.get("cwd") or os.getcwd()
    deadline = time.monotonic() + READ_BUDGET  # one budget for every read of the event, however many directories
    try:
        entries, protects = load_list()
    except MissingList as e:
        for where, how in {cwd: "", **landing_dirs(command, cwd)}.items():
            if not os.path.isdir(where):
                raise Block(missing_dir(where, how, e))
            ok, why = publishes_unchecked(where, command, deadline)
            if not ok:
                raise Block(refusal(why, where, e))
        return []
    reasons = []
    scanned = without_targets(command)
    found = hits(scanned, entries)
    for lineno, n in found:
        reasons.append(f"command text line {lineno}: entry {n}")
    for n in wrapped(scanned, entries, found):
        reasons.append(f"command text: entry {n}, across a line break")
    for flag, value in body_flags(command):
        value = value[1:-1] if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1 else value
        joined = os.path.join(payload.get("cwd") or os.getcwd(), value)
        norms = [posixpath.normpath(re.sub(r"/+", "/", s.replace("\\", "/"))) for s in (value, joined)]
        if value == "-" or any(s.startswith(("/dev/", "/proc/")) for s in norms):
            if not lone_heredoc(command):
                raise Block(f"the body of {flag} comes from input the hook cannot see")
            continue
        texts = []
        for path in body_paths(value, command, cwd, payload["tool_name"]):
            try:
                with open(path, "rb") as f:
                    texts.append(f.read().decode("utf-8-sig"))
            except FileNotFoundError:
                continue
            except (OSError, UnicodeDecodeError) as e:
                raise Block(f"the file {flag} names cannot be read ({type(e).__name__})")
        if not texts:
            raise Block(f"the file {flag} names cannot be read (FileNotFoundError); the hook reads it before the command runs, so a "
                        "file the same command writes, or a path given through a shell variable, is not there yet: write the file "
                        "in a command of its own, then post it by its absolute literal path to a UTF-8 file")
        for text in texts:
            found = hits(text, entries)
            for lineno, n in found:
                reasons.append(f"the file {flag} names, line {lineno}: entry {n}")
            for n in wrapped(text, entries, found):
                reasons.append(f"the file {flag} names: entry {n}, across a line break")
    landed = landing_dirs(command, cwd) if reasons else {}
    if reasons and all(check_off(d, command, protects, deadline) for d in [cwd, *landed]):
        return []
    # A binding's "off" cannot apply where the hook cannot see the call land: name that target beside the hit.
    return reasons + [f"the cd/-C target {d} " + ("is a shell variable or `cd -` the hook does not expand"
                                                if how == "unexpanded" or "$" in d else "does not exist")
                      + ", so the binding's forbidden_check cannot apply there; write the directory literally"
                      for d, how in landed.items() if how == "unexpanded" or "$" in d or not os.path.isdir(d)]


def cmd_hook():
    try:
        try:
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            raise Block("the event is not UTF-8 JSON")
        reasons = hook_check(payload)
    except Block as e:
        print(f"forbidden-tokens: blocked: {e}", file=sys.stderr)
        return 2
    except BaseException as e:
        print(f"forbidden-tokens: blocked: the hook failed ({type(e).__name__})", file=sys.stderr)
        return 2
    for r in reasons:
        print(f"forbidden-tokens: blocked: {r}", file=sys.stderr)
    return 2 if reasons else 0


def tracked_files():
    """(top-level directory, tracked paths relative to it under SCAN_DIRS)."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
    if r.returncode != 0:
        raise SystemExit("forbidden-tokens: not in a git repository: " + r.stderr.decode("utf-8", "replace").strip())
    top = r.stdout.decode("utf-8").strip()
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", top, "ls-files", "-z", "--",
                        *[":/" + d for d in SCAN_DIRS]], capture_output=True)
    if r.returncode != 0:
        raise SystemExit("forbidden-tokens: git ls-files failed: " + r.stderr.decode("utf-8", "replace").strip())
    return top, [p for p in r.stdout.decode("utf-8").split("\0") if p]


def cmd_scan():
    try:
        entries, _ = load_list()
    except MissingList as e:
        if os.environ.get("CI") == "true":
            out(f"SKIP: forbidden-tokens scan: missing list: {e}; CI holds no list")
            return 0
        print(f"forbidden-tokens: missing list: {e}", file=sys.stderr)
        return 1
    top, files = tracked_files()
    if not files:
        print("forbidden-tokens: no tracked file under " + ", ".join(d + "/" for d in SCAN_DIRS)
              + "; the scan would check nothing", file=sys.stderr)
        return 1
    bad = False
    for k, path in enumerate(files, 1):
        full = os.path.join(top, path)
        if not os.path.exists(full):
            continue  # tracked, deleted in the working tree: nothing there ships
        label = path
        for n, rx in enumerate(entries, 1):
            if rx.search(path):
                out(f"path {k} of git ls-files: entry {n}")
                label = f"path {k}"
                bad = True
        try:
            with open(full, "rb") as f:
                raw = f.read()
        except OSError:
            out(f"{label}: cannot be read")
            bad = True
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            out(f"{label}: not UTF-8")
            bad = True
            continue
        found = hits(text, entries)
        for lineno, n in found:
            out(f"{label}:{lineno}: entry {n}")
            bad = True
        for n in wrapped(text, entries, found):
            out(f"{label}: entry {n}, across a line break")
            bad = True
    return 1 if bad else 0


def selftest():
    import tempfile
    this = os.path.abspath(__file__)
    # Refuse before compiling the directory parser, including hook mode's blocking exit.
    for minor in (10, 11):
        for mode in ("check", "scan", "hook", "--selftest"):
            fake = (f"import runpy, sys; sys.version_info = (3, {minor}, 2, 'final', 0); "
                    f"sys.argv = [{this!r}, {mode!r}]; runpy.run_path({this!r}, run_name='__main__')")
            old = subprocess.run([sys.executable, "-c", fake], capture_output=True, text=True)
            assert old.returncode == 2 and not old.stdout, (mode, old)
            assert old.stderr.strip() == f"forbidden-tokens.py needs Python 3.12+; this python is 3.{minor}", old.stderr

    def run(args, env_add=None, cwd=None, stdin=b"", unset=(), timeout=None, script=None):
        env = {k: v for k, v in os.environ.items() if k not in (VAR, "CI") and k not in unset}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(env_add or {})
        r = subprocess.run([sys.executable, script or this, *args], input=stdin, capture_output=True,
                           cwd=cwd, env=env, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        def lst(name, data):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(data)
            return {VAR: p}

        good = lst("good.txt", b"# comment\n\n  alpha\\d+  \r\nbeta\\b\n")
        bom = lst("bom.txt", b"\xef\xbb\xbfalpha\n")
        bad = lst("bad.txt", b"fine\n\nbroken(\n")
        empty = lst("empty.txt", b"# only a comment\n\n")
        notutf = lst("notutf.txt", b"\xff\xfe\n")
        nul = lst("nul.txt", "alpha\n".encode("utf-16-le"))
        echo = lst("echo.txt", b"(?P<secretword>x)(?P<secretword>y)\n")
        anchors = lst("anchors.txt", b"acme$\n^bom\n")
        wrapchk = lst("wrapchk.txt", b"acme\\s+corp\n")

        # check
        assert run(["check"], good, stdin=b"nothing\nhere\n")[0] == 0
        code, o, _ = run(["check"], good, stdin=b"x\nAlpha12 here\n\nBETA\n")
        assert (code, o) == (1, "stdin:2: entry 1\nstdin:4: entry 2\n"), (code, o)
        assert run(["check"], good, stdin=b"alpha\n")[0] == 0, "entry 1 needs digits"
        assert run(["check"], good, stdin=b"betas\n")[0] == 0, "word boundary written in the entry"
        assert run(["check"], good, stdin=b"a\r\nbeta\r\n")[1] == "stdin:2: entry 2\n", "CRLF input"
        assert run(["check"], bom, stdin=b"ALPHA\n")[:2] == (1, "stdin:1: entry 1\n"), "BOM accepted"
        assert run(["check"], anchors, stdin=b"x acme\r\n")[:2] == (1, "stdin:1: entry 1\n"), "end anchor, CRLF"
        assert run(["check"], anchors, stdin=b"\xef\xbb\xbfbom\n")[:2] == (1, "stdin:1: entry 2\n"), "BOM on input"
        assert run(["check"], anchors, stdin=b"x acmes\r\n")[0] == 0, "end anchor still anchors"
        assert run(["check"], wrapchk, stdin=b"the acme\ncorp\n")[:2] == (1, "stdin: entry 1, across a line break\n")
        assert run(["check"], wrapchk, stdin=b"the acme corp\n")[:2] == (1, "stdin:1: entry 1\n"), "reported once"
        assert run(["check"], wrapchk, stdin=b"the acme\nthe corp\n")[0] == 0, "not adjacent"
        assert run(["check"], good, stdin=b"\xff\n")[0] == 2, "non-UTF-8 input"
        assert run(["check"], good, stdin="café alpha1\n".encode("utf-8"))[1] == "stdin:1: entry 1\n"
        for name, env, cause in (("unset", {}, VAR + " is not set"),
                                 ("empty var", {VAR: ""}, VAR + " is not set"),
                                 ("no such file", {VAR: os.path.join(tmp, "nope.txt")}, "cannot be read"),
                                 ("directory", {VAR: tmp}, "cannot be read"),
                                 ("not UTF-8", notutf, "not UTF-8"),
                                 ("NUL, UTF-16 without a mark", nul, "not UTF-8"),
                                 ("no entry", empty, "holds no entry"),
                                 ("bad entry", bad, "line 3 of the file")):
            code, o, e = run(["check"], env, stdin=b"fine broken\n")
            assert (code, o) == (2, ""), (name, code, o)
            assert cause in e, (name, e)
        assert "broken" not in run(["check"], bad)[2], "the entry is never echoed"
        code, o, e = run(["check"], echo)
        assert code == 2 and "line 1 of the file" in e and "secretword" not in e, (code, e)
        flags = lst("flags.txt", b"fine\n(?a)(?u)zzflag\n")
        code, o, e = run(["check"], flags, stdin=b"fine\n")
        assert (code, o) == (2, "") and "line 2 of the file" in e and "does not compile" in e \
            and "zzflag" not in e and "(?a)" not in e and "Traceback" not in e, (code, o, e)

        # scan, over a throwaway repository
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        def w(rel, data):
            p = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        w("skills/a/SKILL.md", b"clean\nthe skillword\n")
        w("docs/café/notes.md".encode("utf-8").decode("utf-8"), "ok\nthe Secretword here\n".encode())
        w("README.md", b"secretword outside the scanned directories\n")
        w("agents/x.md", b"nothing\nagentword\r\n")
        w("templates/t.md", b"nothing\ntemplateword\n")
        subprocess.run(["git", "-c", "core.autocrlf=false", "add", "-A"], cwd=repo, check=True,
                       capture_output=True)
        w("docs/untracked.md", b"secretword but untracked\n")
        sec = lst("sec.txt", b"nomatch\\d\nsecretword\nskillword\nagentword$\ntemplateword\n")
        want = ("agents/x.md:2: entry 4\ndocs/café/notes.md:2: entry 2\n"
                "skills/a/SKILL.md:2: entry 3\ntemplates/t.md:2: entry 5\n")
        code, o, _ = run(["scan"], sec, cwd=repo)
        assert (code, o) == (1, want), (code, o)
        assert "ecretword" not in o and "Secretword" not in o
        code, o, _ = run(["scan"], sec, cwd=os.path.join(repo, "docs", "café"))
        assert (code, o) == (1, want), ("from a subdirectory", code, o)
        code, o, _ = run(["scan"], good, cwd=repo)
        assert (code, o) == (0, ""), (code, o)
        w("agents/y.md", b"\xff\xfe\n")
        subprocess.run(["git", "-c", "core.autocrlf=false", "add", "-A"], cwd=repo, check=True,
                       capture_output=True)
        code, o, _ = run(["scan"], good, cwd=repo)
        assert (code, o) == (1, "agents/y.md: not UTF-8\n"), (code, o)
        os.remove(os.path.join(repo, "agents", "y.md"))
        assert run(["scan"], good, cwd=repo)[0] == 0, "tracked and deleted in the work tree"
        # missing list: red locally, SKIP in CI
        code, o, e = run(["scan"], cwd=repo)
        assert (code, o) == (1, "") and VAR in e, (code, o, e)
        code, o, e = run(["scan"], {"CI": "true"}, cwd=repo)
        assert code == 0 and o.startswith("SKIP:") and o.count("\n") == 1 and VAR in o, (code, o)
        for ci in ("TRUE", "1", "false", ""):
            assert run(["scan"], {"CI": ci}, cwd=repo)[0] == 1, ci
        code, o, _ = run(["scan"], {"CI": "true", VAR: bad[VAR]}, cwd=repo)
        assert code == 0 and o.startswith("SKIP:") and "line 3" in o, (code, o)
        assert run(["scan"], {"CI": "true", **good}, cwd=repo)[:2] == (0, ""), "CI with a list still scans"
        code, o, _ = run(["scan"], {"CI": "true", **sec}, cwd=repo)
        assert code == 1, "CI with a list and a hit"
        def mkrepo(name, files):
            d = os.path.join(tmp, name)
            for rel, data in files.items():
                p = os.path.join(d, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "-c", "core.autocrlf=false", "add", "-A"], cwd=d, check=True,
                           capture_output=True)
            return d

        # a token in a path, and a token wrapped across a line break
        repo2 = mkrepo("repo2", {"docs/once.md": b"acme corp\n", "docs/pathword/p.md": b"clean\n",
                                 "docs/wrap.md": b"the acme\n  corp here\nwrapped\nwords\n"})
        wrap = lst("wrap.txt", b"pathword\nacme\\s+corp\nwrapped words\n")
        code, o, _ = run(["scan"], wrap, cwd=repo2)
        assert (code, o) == (1, "docs/once.md:1: entry 2\npath 2 of git ls-files: entry 1\n"
                                "docs/wrap.md: entry 2, across a line break\n"
                                "docs/wrap.md: entry 3, across a line break\n"), (code, o)
        assert "pathword" not in o
        # a path that holds a token is named by position in every report line of its file
        repo4 = mkrepo("repo4", {"docs/leakword/a.md": b"x\nhitword here\nacme\ncorp\n",
                                 "docs/leakword/b.bin": b"\xff\xfe\n", "docs/plain.md": b"hitword\n"})
        leak = lst("leak.txt", b"leakword\nhitword\nacme corp\n")
        code, o, _ = run(["scan"], leak, cwd=repo4)
        assert (code, o) == (1, "path 1 of git ls-files: entry 1\npath 1:2: entry 2\n"
                                "path 1: entry 3, across a line break\n"
                                "path 2 of git ls-files: entry 1\npath 2: not UTF-8\n"
                                "docs/plain.md:1: entry 2\n"), (code, o)
        assert "leakword" not in o
        # a tracked entry that is a directory (a submodule) cannot be read
        repo5 = mkrepo("repo5", {"docs/a.md": b"clean\n"})
        for sub in ("docs/leakword-sub", "docs/plainsub"):
            subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                            "160000,1111111111111111111111111111111111111111," + sub],
                           cwd=repo5, check=True, capture_output=True)
            os.makedirs(os.path.join(repo5, sub))
        code, o, e = run(["scan"], leak, cwd=repo5)
        assert (code, o) == (1, "path 2 of git ls-files: entry 1\npath 2: cannot be read\n"
                                "docs/plainsub: cannot be read\n"), (code, o)
        assert "leakword" not in o + e and "Traceback" not in e, e
        # no tracked file under the scanned directories
        repo3 = mkrepo("repo3", {"README.md": b"readme\n"})
        code, o, e = run(["scan"], good, cwd=repo3)
        assert (code, o) == (1, "") and "no tracked file" in e, (code, o, e)
        assert run(["nonsense"])[0] != 0

        # hook, over recorded PreToolUse events
        hl = lst("hook.txt", b"secretword\nacme\\s+corp\n")
        work = os.path.join(tmp, "work")
        os.makedirs(os.path.join(work, "dir"))
        for name, data in (("hit.md", b"fine\nthe Secretword\n"), ("clean.md", b"fine\n"),
                           ("wrap.md", b"the acme\ncorp\n"), ("notutf.md", b"\xff\xfe\n"),
                           ("sp ace.md", b"secretword\n")):
            with open(os.path.join(work, name), "wb") as f:
                f.write(data)

        def ev(command, tool="Bash", cwd=work):
            return json.dumps({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": tool,
                               "tool_input": {"command": command}, "cwd": cwd}).encode("utf-8")

        def hook(command, env=hl, **kw):
            return run(["hook"], env, stdin=ev(command, **kw))

        def blocked(command, want, env=hl, **kw):
            code, o, e = hook(command, env, **kw)
            assert (code, o) == (2, "") and want in e, (command, code, o, e)
            assert "secretword" not in (o + e).lower() and "Traceback" not in e, (command, e)

        def allowed(command, env=hl, **kw):
            code, o, e = hook(command, env, **kw)
            assert (code, o, e) == (0, "", ""), (command, code, o, e)

        subs = ("gh issue create", "gh issue comment 1", "gh issue edit 1", "gh issue close 1",
                "gh pr create", "gh pr edit 1", "gh pr comment 1", "gh pr review 1",
                "gh pr merge 1", "gh pr close 1", "git commit", "git -C . -c a=b commit",
                "git commit -a", 'git -c user.name="A B" commit', "git -c 'user.name=A B' commit",
                "git --no-pager commit", "git $GIT_OPTS commit")
        for tool in ("Bash", "PowerShell"):
            for sub in subs:
                blocked(sub + ' -m "x Secretword y"', "command text line 1: entry 1", tool=tool)
                blocked(sub + ' --body "x\\nSECRETWORD"', "command text line 1: entry 1", tool=tool)
                allowed(sub + ' -m "x fine y"', tool=tool)
        blocked("gh pr merge 1 --subject 'secretword' --body fine", "entry 1")
        blocked("gh issue create --title secretword", "entry 1")
        blocked("gh issue comment 1 --body-file - <<'EOF'\nfine\nsecretword\nEOF", "command text line 3: entry 1")
        blocked("gh issue comment 1 --body 'the acme\ncorp'", "command text: entry 2, across a line break")
        blocked("git commit -m 'acme\ncorp'", "command text: entry 2, across a line break")
        blocked("cd x && git commit -m secretword", "entry 1")
        # near misses: not a writing subcommand, another tool, another command
        for cmd in ("gh issue view 1 secretword", "gh issue list --search secretword",
                    "gh pr checks 1 secretword", "gh issue comments secretword",
                    "gh pr merged secretword", "gh api repos/x/y -f body=secretword",
                    "git commit-tree secretword", "git log --grep secretword", "echo secretword",
                    "xgh issue create secretword", "xgit commit secretword", "ls",
                    "git grep -n -c 'reverts commit' secretword", "git diff -- commit.py secretword",
                    "git log --oneline --grep fix commit secretword", "git grep -C 3 commit secretword"):
            allowed(cmd)
            allowed(cmd, tool="PowerShell")
            allowed(cmd, env={})
        for tool in ("Read", "Write", "Task", "WebFetch"):
            allowed("gh issue create secretword", tool=tool)
            allowed("gh issue create secretword", tool=tool, env={})
        assert run(["hook"], hl, stdin=json.dumps({"tool_name": "Read", "tool_input": {
            "file_path": "gh issue create secretword"}}).encode())[:3] == (0, "", "")
        # a body file
        for flag in ("--body-file", "-F", "--file"):
            for sep in (" ", "="):
                blocked(f"gh issue comment 1 {flag}{sep}hit.md", f"the file {flag} names, line 2: entry 1")
                allowed(f"gh issue comment 1 {flag}{sep}clean.md")
        blocked("gh issue comment 1 --body-file ./hit.md --repo x", "the file --body-file names, line 2")
        blocked("gh issue comment 1 --body-file " + os.path.join(work, "hit.md").replace("\\", "/"),
                "the file --body-file names, line 2: entry 1")
        blocked("gh issue comment 1 --body-file 'sp ace.md'", "the file --body-file names, line 1")
        blocked('gh issue comment 1 --body-file="sp ace.md"', "the file --body-file names, line 1")
        blocked("git commit -F hit.md", "the file -F names, line 2")
        blocked("gh pr create --title t --body-file wrap.md",
                "the file --body-file names: entry 2, across a line break")
        blocked("gh pr create --body-file hit.md", "entry 1", tool="PowerShell")
        blocked("gh pr create --body-file clean.md --body-file hit.md", "entry 1")
        blocked("(gh pr create --body-file hit.md)", "entry 1")
        blocked("gh pr create --body-file hit.md; echo", "entry 1")
        allowed("gh pr create --body-file clean.md;")
        allowed("gh pr create --body-file wrap.md", env=lst("w2.txt", b"nomatch\n"))
        for f in ("missing.md", "dir", "notutf.md", "$BODY"):
            blocked(f"gh issue comment 1 --body-file {f}", "cannot be read")
        blocked("gh issue comment 1 --body-file missing.md", "write the file in a command of its own", cwd=os.path.join(work, "dir"))
        blocked("gh issue comment 1 --body-file hit.md", "cannot be read", cwd=os.path.join(work, "dir"))
        # resolved the way the shell does: a cd before the call, ~, and on Windows a Git Bash drive path
        blocked(f'cd "{work}" && git commit -F hit.md', "the file -F names, line 2: entry 1", cwd=os.path.join(work, "dir"))
        blocked("git commit -F ~/hit.md", "the file -F names, line 2: entry 1", env={**hl, "HOME": work, "USERPROFILE": work})
        if os.name == "nt":
            posix = os.path.join(work, "hit.md").replace("\\", "/")
            blocked(f"git commit -F /{posix[0].lower()}{posix[2:]}", "the file -F names, line 2: entry 1")
            blocked(f"git commit -F /{posix[0].lower()}{posix[2:]}", "cannot be read", tool="PowerShell")
        # flag text outside a publishing command is not acted on
        allowed("cat --body-file missing.md")
        allowed("echo --file missing.md")
        # a '-' body: a heredoc passes (its text is checked), anything else blocks
        allowed("gh issue edit 1 --body-file - <<'EOF'\nclean text\nEOF")
        allowed("gh issue edit 1 -F - <<'EOF'\nclean text\nEOF", tool="PowerShell")
        blocked("gh issue edit 1 --body-file - < clean.md", "comes from input the hook cannot see")
        blocked("cat clean.md | gh issue edit 1 --body-file -", "comes from input the hook cannot see")
        blocked("gh issue edit 1 -F -", "comes from input the hook cannot see", tool="PowerShell")
        blocked("gh issue edit 1 --file=-", "comes from input the hook cannot see")
        blocked("gh issue edit 1 --body-file - <<'EOF'\nthe Secretword\nEOF", "command text line 2: entry 1")
        # the spellings gh and git accept, each with the control that clean text passes
        for form in ("GH issue comment 1", "gh.exe issue comment 1", "gh issue new",
                     '& "C:/Program Files/GitHub CLI/gh.exe" issue comment 1', "gh -R o/r issue create",
                     "gh --repo o/r pr comment 1", "gh pr -R o/r comment 1", "gh issue -R o/r comment 1", "gh issue reopen 1 -c",
                     "gh pr reopen 1 -c", "gh pr revert 1 -b", "gh pr new", "Git commit", "GIT.EXE commit",
                     "git -C dir commit", "/usr/bin/gh issue edit 1"):
            for tool in ("Bash", "PowerShell"):
                blocked(form + ' -m "x Secretword y"', "command text line 1: entry 1", tool=tool)
                allowed(form + ' -m "x fine y"', tool=tool)
        for cmd in ("git commit-tree secretword", "gh issue comments secretword", "xgh issue create secretword",
                    "gh -R o/r issue view secretword", "gh issue -R o/r list secretword",
                    "gh pr revert-all secretword", "Xgit commit secretword"):
            allowed(cmd)
        allowed("git grep -n -c 'reverts commit' -- bin; git grep -n -F 'no such file' -- bin")
        for cmd in ("git \\\ncommit -m secretword", "git -C a \\\ncommit -m secretword",
                    "git -C $(git rev-parse --show-toplevel) commit -m secretword", "git -C grep commit -m secretword",
                    "git -C a\\ b commit -m secretword", "git ${opts[@]} commit -m secretword"):
            blocked(cmd, "entry 1")
        # each subcommand that never commits makes a later `commit` an argument; any other subcommand does not
        for word in ("grep", "log", "diff", "show", "blame", "status", "rev-parse", "rev-list", "ls-files", "ls-tree",
                     "cat-file", "describe", "shortlog", "reflog", "help", "merge-base", "name-rev", "for-each-ref",
                     "show-ref", "whatchanged", "range-diff", "cherry"):
            allowed(f"git {word} commit secretword")
        blocked("git stash commit secretword", "entry 1")
        blocked("git -C a `\ncommit -m secretword", "entry 1", tool="PowerShell")
        blocked("git $env:GIT_OPTS commit -m secretword", "entry 1", tool="PowerShell")
        # a body file named with its value attached is read only on the publishing call's own line
        blocked("git commit -Fhit.md", "the file -F names, line 2: entry 1")
        blocked("gh issue comment 1 -Fhit.md", "the file -F names, line 2: entry 1")
        blocked("echo x\ngit commit -Fhit.md", "the file -F names, line 2: entry 1")
        blocked("git commit -m fine -Force", "cannot be read")
        allowed("git commit -Fclean.md")
        allowed("gh issue comment 1 -Fclean.md")
        allowed("Remove-Item x -Force; git commit -m fine", tool="PowerShell")
        allowed("Remove-Item x -Force\ngit commit -m fine", tool="PowerShell")
        allowed("git commit -m fine\nRemove-Item x -Force", tool="PowerShell")
        # ...and only in its own statement, outside quotes: a later command's or a title's `-File` is no body file
        allowed("git commit -m fine && pwsh -NoProfile -File x.ps1")
        allowed("git commit -m fine; Remove-Item x -Force", tool="PowerShell")
        allowed('gh issue create --title "reads pwsh -File after" --body-file clean.md')
        blocked("git commit -Fhit.md && echo", "the file -F names, line 2: entry 1")
        blocked('git commit -m "a; b" -Fhit.md', "the file -F names, line 2: entry 1")
        blocked('git commit -m "a -Fhit.md', "the file -F names, line 2: entry 1")  # unbalanced quotes: to the line's end
        gone = "comes from input the hook cannot see"
        lone = "<<'EOF'\nclean\nEOF"
        # a '-' body, or a /dev or /proc one, passes only when the whole command is one publishing
        # invocation with its own heredoc: the control rows pass, every other shape blocks
        for f in ("-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0", "//dev/stdin", "/./dev/stdin",
                  "/proc/../dev/stdin", "/dev//fd/0"):
            for flag, sep in ((("--body-file", " "), ("-F", " "), ("--file", "="))
                              if f in ("-", "/dev/stdin") else (("--body-file", " "),)):
                blocked(f"gh issue comment 1 {flag}{sep}{f}", gone)
                blocked(f"gh issue comment 1 {flag}{sep}{f} < hit.md", gone)
                allowed(f"gh issue comment 1 {flag}{sep}{f} {lone}")
            blocked(f"git commit -F{f}", gone)
            allowed(f"git commit -F {f} {lone}")
        allowed("gh issue comment 1 --body-file ./clean.md")
        blocked("gh issue comment 1 --body-file ../../../../dev/stdin", gone, cwd="/a/b/c/d")
        blocked("gh issue comment 1 --body-file ../../../proc/self/fd/0", gone, cwd="/a/b/c")
        allowed("gh issue comment 1 --body-file ../../../../dev/stdin <<'EOF'\nclean\nEOF", cwd="/a/b/c/d")
        allowed("gh issue comment 1 -F - <<-'EOF'\n\tclean\n\tEOF\n", tool="PowerShell")
        allowed("gh issue comment 1 -F - <<-\"EOF\"\n\tclean\n\tEOF\n")
        allowed('gh issue comment 1 -F - <<"EOF"\r\nclean\r\nEOF\r\n')
        allowed("/usr/bin/gh issue comment 1 -F - <<'EOF'\nclean\nEOF")
        allowed("gh issue comment 1 -F - -t $\"x\" <<'EOF'\nclean\nEOF")
        allowed("gh issue comment 1 -F - -t 'a title' <<'EOF'\nclean\nEOF")
        allowed("gh issue comment 1 -F - -t \"a title\" <<'EOF'\nclean\nEOF")
        allowed("gh issue edit 12 -R owner/repo --body-file - <<'DRIFT_LEDGER_BODY'\nfine text `x` $y\nDRIFT_LEDGER_BODY\n")
        blocked("gh issue comment 1 -F - <<'EOF'\nthe Secretword\nEOF", "command text line 2: entry 1")
        for cmd in ("cat > n <<'EOF'\nok\nEOF\ncat hit.md | gh issue comment 1 --body-file -",
                    "gh issue comment 1 --body-file - < hit.md # <<",
                    "echo $((1<<2)); gh issue comment 1 -F - < hit.md",
                    "cat <<EOF && gh issue comment 1 -F -\nclean\nEOF",
                    "cat <<EOF\nclean\nEOF\ngh issue comment 1 -F -",
                    "gh issue comment 1 -F - <<< hit",
                    "cat hit.md | gh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "cat hit.md |\ngh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "gh issue comment 1 -F - <<EOF < hit.md\nclean\nEOF",
                    "gh issue comment 1 -F - 3<<EOF\nclean\nEOF",
                    "gh issue comment 1 -F - <<$'EOF'\nclean\nEOF",
                    "gh issue comment 1 -F - <<EOF; cat hit.md | gh pr comment 1 -F -\nclean\nEOF",
                    "exec <hit.md; gh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "exec <hit.md\ngh issue comment 1 -F -",
                    "{ gh issue comment 1 -F -; } < hit.md",
                    "{ gh issue comment 1 -F - <<EOF\nclean\nEOF\n} < hit.md",
                    "( gh issue comment 1 -F - ) < hit.md",
                    "(gh issue comment 1 -F - <<EOF\nclean\nEOF\n)",
                    "echo hi; gh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "echo hi && gh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "gh issue comment 1 -F - <<EOF\nclean\nEOF\ncat hit.md | gh issue comment 2 -F -",
                    "gh issue comment 1 -F - <<EOF\nclean\nEOF\necho x",
                    "gh issue comment 1 -F - <<EOF\nclean\nEOF; echo x",
                    "gh issue comment 1 -F - <<EOF\nclean\n",
                    "gh issue comment 1 -F - <<EOF",
                    "echo '<<' ; gh issue comment 1 -F -",
                    "gh issue comment 1 -F - -b '<<'",
                    "cat <<EOF\ngh issue comment 1 -F - <<X\nEOF",
                    "gh issue comment 1 -b fine; foo -F - <<EOF\nclean\nEOF",
                    "exec gh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "gh issue comment 1 -F - <<EOF\nclean\nEOF",
                    "gh issue comment 1 -F - <<EOF\n$(cat hit.md)\nEOF",
                    "gh issue comment 1 -b x # <<'EOF'\ncat hit.md | gh issue comment 2 -F -\nEOF",
                    "gh issue comment 1 -b x # <<EOF\ncat hit.md | gh issue comment 2 -F -\nEOF",
                    "gh issue comment 1 -F - -t 'x <<EOF\n' < hit.md\nEOF",
                    "gh issue comment 1 -F - -t 'x <<'EOF'\n' < hit.md\nEOF",
                    "gh issue comment 1 -F - -t \"x <<EOF\n\" < hit.md\nEOF",
                    "gh issue comment 1 -F - -t \"x <<\"EOF\"\n\" < hit.md\nEOF",
                    "gh issue comment 1 -F - -t $'x <<EOF\\n' < hit.md\nEOF",
                    "gh issue comment 1 -F - -t $'x\\' <<'EOF'\n' < hit.md\nEOF",
                    "gh issue comment 1 -F - -t 'x <<'EOF'",
                    "gh issue comment 1 -F - -t \"x <<'EOF'"):
            blocked(cmd, gone)
        for exe in ("/usr/bin/git", "git.exe", '"C:/Program Files/Git/bin/git.exe"', "GIT"):
            blocked(exe + ' commit -m "x Secretword y"', "command text line 1: entry 1")
            allowed(exe + ' commit -m "x fine y"')
        # a line continuation or a PowerShell backtick between gh and its noun or verb
        for sp in (" \\\n", " `\n"):
            blocked(f'gh{sp}issue{sp}comment 1 -m "x Secretword y"', "command text line 3: entry 1", tool="PowerShell")
            blocked(f'gh{sp}-R o/r{sp}pr{sp}create -m "x Secretword y"', "command text line 4: entry 1", tool="PowerShell")
        # a hostile event must not stall the hook; each names a listed token, so the directory reader runs on it
        for hostile in ("gh " + "--aa " * 30 + "x" * 100000, "gh " + "--aa " * 30 + "issue " + "x" * 100000,
                        "gh " + "--aa=" + "x" * 100000, "git " + "x " * 30 + "x" * 100000,
                        "gh " + "-" * 100000, "git commit -m Secretword; " + "A=' -c ' " * 9000, "git commit -m Secretword; " + 'A=" -c " ' * 9000,
                        "git commit -m Secretword; " + "A=' -c ' builtin " * 6000, "git commit -m Secretword; A=" + "$'a'" * 40 + '"',
                        "gh pr create -t Secretword; A=" + "$'a'" * 40 + "x", "git commit -m Secretword; " + ("A=1 " * 60 + "x ") * 400,
                        "git commit -m Secretword; " + "-c A=1 " * 8000, "gh pr create -t Secretword; " + "eval A=1 " * 8000,
                        "echo " + "'" * 20000 + "x && git commit -m Secretword", "echo " + "\\" * 20000 + "x && git commit -m Secretword", "echo " + "$'" * 10000 + "x && git commit -m Secretword",
                        "echo " + "c''d x " * 10000 + "&& git commit -m Secretword", "A=`echo a b` " + "'cd' x " * 10000 + "&& git commit -m Secretword",
                        "git " + "-c " * 40 + "x; git commit -m Secretword", "git " + "-c $A " * 40 + "x; git commit -m Secretword"):
            run(["hook"], hl, stdin=ev(hostile), timeout=2)
        # a missing list blocks a publishing command and no other, unless the working directory's
        # repository may publish without one, over a gh stand-in on PATH
        stubs = os.path.join(tmp, "stubs")
        os.makedirs(stubs)
        with open(os.path.join(stubs, "gh"), "w", encoding="utf-8", newline="\n") as f:
            f.write('#!/bin/sh\ncase "$3$PWD" in */pub*) printf \'{"visibility":"PUBLIC"}\'; exit 0;; *norepo*) exit 1;; esac\n'
                    '[ "$STUB_VIS" = slow ] && sleep 20\n[ "$STUB_VIS" = slow4 ] && { sleep 4; printf \'{"visibility":"PRIVATE"}\'; exit 0; }\n[ "$STUB_VIS" = fail ] && exit 1\n[ "$STUB_VIS" = novis ] && { printf "{}"; exit 0; }\n'
                    'printf \'{"visibility":"%s"}\' "$STUB_VIS"\n')
        os.chmod(os.path.join(stubs, "gh"), 0o755)
        with open(os.path.join(stubs, "gh.cmd"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write('@echo off\necho %3%CD% | findstr /i /l /c:"/pub" /c:"\\pub" >nul && (echo {"visibility":"PUBLIC"} & exit /b 0)\n'
                    'echo %CD% | findstr /i /l /c:"norepo" >nul && exit /b 1\n'
                    'if "%STUB_VIS%"=="slow" ping -n 21 127.0.0.1 >nul\nif "%STUB_VIS%"=="slow4" (ping -n 5 127.0.0.1 >nul & echo {"visibility":"PRIVATE"} & exit /b 0)\nif "%STUB_VIS%"=="fail" exit /b 1\nif "%STUB_VIS%"=="novis" (echo {} & exit /b 0)\n'
                    'echo {"visibility":"%STUB_VIS%"}\n')

        def vis(value, env=None):
            return {**(env or {}), "PATH": stubs + os.pathsep + os.environ.get("PATH", ""), "STUB_VIS": value}

        for env, cause in (({}, VAR + " is not set"), ({VAR: os.path.join(tmp, "nope.txt")}, "cannot be read"),
                           (bad, "line 3 of the file"), (empty, "holds no entry")):
            blocked("gh pr merge 1 --body fine", cause, env=vis("fail", env))
            blocked("gh pr merge 1 --body fine", "gh repo view of the repository at", env=vis("fail", env))
            blocked("git commit -m fine", "failed, and the machine has no list (", env=vis("fail", env), tool="PowerShell")
        for value, reason in (("PUBLIC", "is PUBLIC, not private, and the machine has no list ("), ("nonsense", "is nonsense, not private, and"), ("fail", " failed, and"), ("novis", "gave no visibility, and")):
            blocked("gh pr merge 1 --body fine", reason, env=vis(value))
        for value in ("PRIVATE", "INTERNAL"):
            allowed("gh pr merge 1 --body fine", env=vis(value))
            allowed("git commit -m fine", env=vis(value), tool="PowerShell")
        nogh = os.path.join(tmp, "nogh")
        os.makedirs(nogh)
        blocked("gh pr merge 1 --body fine", "no gh on PATH to read the repository's visibility, and the machine has no list (", env={**vis("PRIVATE"), "PATH": nogh})  # no gh resolves
        # a landing directory that is not there names why the hook judged it: a word it does not expand, a cd in text it
        # cannot tell from a script, else plainly; each still refused
        for cmd in ("cd $REPO && gh pr merge 1 --body fine", 'cd "$REPO" && gh pr merge 1 --body fine', "cd - && gh pr merge 1 --body fine"):
            blocked(cmd, "expands neither; run the call from that directory", env=vis("PRIVATE"))
        for cmd in ('git commit -m "notes\ncd nowhere-dir and back\n"', "gh issue comment 1 --body-file - <<'EOF'\ncd nowhere-dir\nEOF"):
            blocked(cmd, "which the hook cannot tell from a script; if it is text, reword it", env=vis("PRIVATE"))
        for cmd in ("cd nowhere-dir && gh pr merge 1 --body fine", "bash -c 'cd nowhere-dir && gh pr merge 1 --body fine'",
                    "cd '$lit' && gh pr merge 1 --body fine"):  # a child shell's own cd, a single-quoted $: neither is text nor unexpanded
            code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev(cmd))
            assert code == 2 and "does not exist" in e and "expands neither" not in e and "cannot tell" not in e, (cmd, code, o, e)
        # the binding at cwd decides first: required blocks a private repository, off passes a failed read
        toml = ('schema = 1\n\n[repo]\nslug = "o/r"\ndefault_branch = "master"\n\n[ship]\npolicy = "stop-at-pr"\n'
                'review = "none"\n\n[owner]\nruling_approvers = ["o"]\n')
        for key, value, want in (("required", "PRIVATE", 2), ("off", "fail", 0)):
            bound = mkrepo("bound-" + key, {".claude/ouro.toml": toml.replace('review = "none"\n', f'review = "none"\nforbidden_check = "{key}"\n').encode()})
            code, o, e = run(["hook"], vis(value), stdin=ev("gh pr merge 1 --body fine", cwd=bound))
            assert code == want and "Traceback" not in e, (key, value, code, o, e)
        bound = mkrepo("bound-absent", {".claude/ouro.toml": toml.encode()})
        assert run(["hook"], vis("PRIVATE"), stdin=ev("gh pr merge 1 --body fine", cwd=bound))[0] == 0
        assert run(["hook"], vis("PUBLIC"), stdin=ev("gh pr merge 1 --body fine", cwd=bound))[0] == 2
        # a binding that fails check blocks as required does, whatever gh says
        bound = mkrepo("bound-broken", {".claude/ouro.toml": toml.replace('review = "none"', 'review = "bogus"').encode()})
        code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev("gh pr merge 1 --body fine", cwd=bound))
        assert code == 2 and "binding fails check:" in e and "missing list" not in e, (code, o, e)
        required = mkrepo("bound-required", {".claude/ouro.toml": toml.replace('policy = "stop-at-pr"\n', 'policy = "stop-at-pr"\nforbidden_check = "required"\n').encode()})
        code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev("gh pr merge 1 --body fine", cwd=required))
        assert code == 2 and e.startswith("forbidden-tokens: blocked: missing list: ") and "the binding requires the check" in e, (code, o, e)
        # the refusal names its reason: the visibility read's answer, or no gh to ask
        code, o, e = run(["hook"], vis("PUBLIC"), stdin=ev("gh pr merge 1 --body fine", cwd=mkrepo("bound-absent2", {".claude/ouro.toml": toml.encode()})))
        assert code == 2 and "o/r is PUBLIC, not private, and the machine has no list (" in e, (code, o, e)
        code, o, e = run(["hook"], {**vis("PRIVATE"), "PATH": nogh}, stdin=ev("git commit -m fine", cwd=mkrepo("bound-absent3", {".claude/ouro.toml": toml.encode()})))
        assert code == 2 and "no gh on PATH" in e, (code, o, e)
        # a call is judged at every directory it lands in: the working directory, each cd target and
        # each git -C target, chained; all must pass. The stub answers PUBLIC for a slug or a path
        # component starting with "pub", and fails in a directory holding "norepo", so a public clone
        # and a plain directory are told from the private working directory.
        home = mkrepo("bound-home", {".claude/ouro.toml": toml.encode()})
        pubclone = mkrepo("pubclone", {".claude/ouro.toml": toml.replace('slug = "o/r"', 'slug = "o/pub"').encode()})
        privclone = mkrepo("privclone", {".claude/ouro.toml": toml.replace('slug = "o/r"', 'slug = "o/r2"').encode()})
        pubplain = mkrepo("pubplain", {"x.txt": b"x\n"})
        norepo = os.path.join(tmp, "norepo")
        os.makedirs(norepo)
        rows = [(f'git -C "{pubclone}" commit -m fine', 2, "o/pub is PUBLIC, not private, and the machine has no list ("),
                (f"git -C '{privclone}' commit -m fine", 0, ""),
                (f'git -c user.name=x -C "{pubclone}" commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"cd '{privclone}'; git commit -m fine", 0, ""),
                (f'git -C "{pubplain}" commit -m fine', 2, "is PUBLIC, not private"),
                (f'git -C "{norepo}" commit -m fine', 2, "gh repo view of the repository at"),
                (f'git -C "{os.path.join(tmp, "nowhere")}" commit -m fine', 2, "the directory"),
                ("git commit -m fine", 0, ""),
                ("git commit -C HEAD -m fine", 0, ""),
                (f'git -C "{privclone}" commit -m a && git -C "{pubclone}" commit -m b', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}"\ngit commit -m fine', 2, "o/pub is PUBLIC"),
                (f'true && cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'(cd "{pubclone}" && git commit -m fine)', 2, "o/pub is PUBLIC"),
                (f'pushd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" || exit 1; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" && git -C . commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{privclone}" && git -C "{pubclone}" commit -m fine', 2, "o/pub is PUBLIC"),
                (f'Set-Location "{pubclone}"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'git commit -m "see git -C {pubclone} commit"', 2, "o/pub is PUBLIC"),  # a mention adds a directory: it can block, never pass
                (f'bash -c "git -C {pubclone} commit -m fine"', 2, "o/pub is PUBLIC"),
                (f'git -C "{pubclone}" commit>log', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" # go\ngit commit -m fine', 2, "o/pub is PUBLIC"),
                (f"cat > m.txt <<'EOF'\nit's\nEOF\ncd \"{pubclone}\" && git commit -F m.txt # don't push", 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" && git commit -F - <<\'EOF\'\nfix: don\'t break\nEOF', 2, "o/pub is PUBLIC"),
                (f'Set-Location "{pubclone}"; git commit -m @\'\nit\'s done\n\'@', 2, "o/pub is PUBLIC"),
                (f'git -c commit.gpgsign=false -C "{pubclone}" commit -m fine', 2, "o/pub is PUBLIC"),
                (f'Set-Location -Path "{pubclone}"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'Push-Location "{pubclone}"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd -P "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" \\\n&& git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'pushd "{pubclone}" >/dev/null && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{pubclone}" 2>/dev/null && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd {pubclone}>/dev/null 2>&1 && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'Set-Location -Path "{pubclone}" -PassThru; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'git --git-dir="{pubclone}/.git" --work-tree="{pubclone}" commit -m fine', 2, "o/pub is PUBLIC"),
                (f'git --git-dir "{pubclone}/.git" commit -m fine', 2, "o/pub is PUBLIC"),
                (f'GIT_DIR="{pubclone}/.git" git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'$env:GIT_DIR = "{pubclone}/.git"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'bash -c "cd {pubclone} && git commit -m x"', 2, "o/pub is PUBLIC"),
                (f"sh -c 'cd \"{pubclone}\"; git commit -m x'", 2, "o/pub is PUBLIC"),
                (f'pwsh -NoProfile -Command "Set-Location {pubclone}; git commit -m x"', 2, "o/pub is PUBLIC"),
                (f'cd "{privclone}" >/dev/null && git commit -m fine', 0, ""),
                (f'git --git-dir="{privclone}/.git" commit -m fine', 0, ""),
                (f'bash -c "cd {privclone} && git commit -m x"', 0, ""),
                (f'bash --norc -o pipefail -c "cd {pubclone} && git commit -m x"', 2, "o/pub is PUBLIC"),
                (f'env bash -c "cd {pubclone} && git commit -m x"', 2, "o/pub is PUBLIC"),
                (f'pwsh -NoProfile -ExecutionPolicy Bypass -Command "& {{ Set-Location {pubclone}; git commit -m x }}"', 2, "o/pub is PUBLIC"),
                (f'{{ cd "{pubclone}" && git commit -m fine; }}', 2, "o/pub is PUBLIC"),
                (f'builtin cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'FOO=1 cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'A=1 B="x y" C=\'z\' cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'A=1 B="x y" C=\'z\' cd "{privclone}" && git commit -m fine', 0, ""),
                (f'A=x"y" cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"A=a'b c' cd \"{pubclone}\" && git commit -m fine", 2, "o/pub is PUBLIC"),
                (f"A=$'x' cd \"{pubclone}\" && git commit -m fine", 2, "o/pub is PUBLIC"),
                (f'A+=1 cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'A=x\\ cd "{pubclone}"; git -C "{privclone}" commit -m fine', 0, ""),  # bash: A='x cd', no cd; the -C target is judged
                (f'A=x\\\\ cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),  # an escaped backslash, then a real cd
                (f'A="a\\"b" cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"A=$'it\\'s' cd \"{pubclone}\" && git commit -m fine", 2, "o/pub is PUBLIC"),
                ("true;" + " " * 201 + f'cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'A=`echo x` cd "{pubclone}" && git commit -m fine', 2, "prefix assignments the hook cannot read"),
                (f'A=${{X:- y}} cd "{pubclone}" && git commit -m fine', 2, "prefix assignments the hook cannot read"),
                ('A=1 git commit -m "cd into the repo"', 0, ""),  # the verb inside quotes is prose, no refusal
                ("MSYS_NO_PATHCONV=1 git grep -n cd -- bin; git commit -m fine", 0, ""),  # a readable prefix before a command that is not a cd: no refusal
                ("LC_ALL=C grep -rnw sl src && git commit -m fine", 0, ""),
                (f'A=$HOME cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),  # a plain variable in a value is read through
                (f'FOO=1 cd && git -C "{pubclone}" commit -m fine', 2, "cd whose directory cannot be read"),
                ("A=1 " * 65 + f'cd "{privclone}" && git commit -m fine', 2, "more than 64 prefix words"),
                ("A=1 builtin " * 33 + f'cd "{privclone}" && git commit -m fine', 2, "more than 64 prefix words"),
                (f'if cd "{pubclone}"; then git commit -m fine; fi', 2, "o/pub is PUBLIC"),
                (f"'cd' \"{pubclone}\" && git commit -m fine", 2, "o/pub is PUBLIC"),  # a quoted or escaped verb is the builtin all the same
                (f'"cd" "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"$'cd' \"{pubclone}\" && git commit -m fine", 2, "o/pub is PUBLIC"),
                (f'c\\d "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"c''d \"{pubclone}\" && git commit -m fine", 2, "o/pub is PUBLIC"),
                (f'\\cd "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'"pushd" "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'$"cd" "{pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"A=`echo a b` 'cd' \"{pubclone}\" && git commit -m fine", 2, "cd behind prefix assignments the hook cannot read"),  # a quoted verb after an unreadable prefix is still a verb
                ('A=`echo a b` git commit -m "cd there"', 0, ""),  # a message that opens with the word is prose, not a verb
                ('A=`echo a b` git commit -m "cd "', 0, ""),  # so is a quoted word with a blank inside its quotes
                (f'cdx "{pubclone}" && git commit -m fine', 0, ""),  # not the verb
                (f"'cd'x \"{pubclone}\" && git commit -m fine", 0, ""),
                ("cd; git commit -m fine", 2, "cd whose directory cannot be read"),  # a bare cd ending its segment, with more after it: it lands in HOME
                ("'cd'; git commit -m fine", 2, "whose directory cannot be read"),
                ("cd && git commit -m fine", 2, "cd whose directory cannot be read"),
                ("git commit -m fine; cd", 0, ""),  # a bare cd ending the whole command moves nothing the commit sees
                ("git commit -m fine; cd\n", 0, ""),  # a trailing newline, as a multi-line tool command ends
                (f'cd -- "{privclone}" >/dev/null && git commit -m fine', 0, ""),
                (f'pushd -n "{privclone}" && git commit -m fine', 0, ""),
                (f'Set-Location "{pubclone}" | Out-Null; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'pwsh -Command "Push-Location {pubclone} | Out-Null; git commit -m x"', 2, "o/pub is PUBLIC"),
                (f'cd "{tmp}" && cd "{privclone}" | cat; git -C pubclone commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "{tmp}"/pubclone && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'Set-Location -Path:"{pubclone}"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f"cd $'{pubclone}' && git commit -m fine", 2, "o/pub is PUBLIC"),
                (f'env -C "{pubclone}" git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'chdir "{pubclone}"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'bash -c "cd \\"{pubclone}\\" && git commit -m fine"', 2, "o/pub is PUBLIC"),
                (f"bash -c 'cd '\"{pubclone}\"' && git commit -m fine'", 2, "directory cannot be read"),
                (f'env -C "{pubclone}" bash -c "git commit -m fine"', 2, "o/pub is PUBLIC"),
                (f'cmd /c "cd /d {pubclone} && git commit -m fine"', 2, "o/pub is PUBLIC"),
                (f'eval cd "{pubclone}"; git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'iex "cd {pubclone}; git commit -m fine"', 2, "o/pub is PUBLIC"),
                (f'Invoke-Expression "cd {pubclone}; git commit -m fine"', 2, "o/pub is PUBLIC"),
                (f'echo x | xargs sh -c cd {pubclone} && git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'cd "$(dirname {pubclone}/x)" && git commit -m fine', 2, "directory cannot be read"),
                (f'Set-Location (Resolve-Path {pubclone}); gh pr create -t x', 2, "directory cannot be read"),
                (f'env --chdir="{pubclone}" git commit -m fine', 2, "o/pub is PUBLIC"),
                (f'env --chdir="{tmp}" git -C pubclone commit -m fine', 2, "o/pub is PUBLIC"),
                (f'bash -lc "cd {pubclone}" && git commit -m fine', 2, "o/pub is PUBLIC"),
                ('git commit -m "fix the \'cd nowhere\' hint"', 0, ""),
                ('git commit -m "cd into the dir to build"', 0, "")]
        if os.name == "nt":
            msys = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), privclone).replace("\\", "/")
            rows += [(f"git -C {msys} commit -m fine", 0, ""), (f"cd {msys} && git commit -m fine", 0, "")]
        for command, want, text in rows:
            code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev(command, cwd=home))
            assert code == want and text in e and "Traceback" not in e, (command, code, o, e)
        # an unquoted leading ~ is the home directory, HOME before USERPROFILE, as bash reads it; a quoted ~,
        # ~user, a ~ after = in an option and any ~ in a command that assigns HOME stay literal (and are
        # refused as missing, never judged at the hook's own home)
        pub, priv = os.path.basename(pubclone), os.path.basename(privclone)
        athome = {**vis("PRIVATE"), "HOME": tmp, "USERPROFILE": norepo}
        literal = os.path.join(home, "~")
        for command, want, text in ((f"cd ~/{pub} && git commit -m fine", 2, "o/pub is PUBLIC"),
                                    (f"cd ~/{priv} && git commit -m fine", 0, ""),
                                    (f"git -C ~/{pub} commit -m fine", 2, "o/pub is PUBLIC"),
                                    (f"GIT_WORK_TREE=~/{pub} git commit -m fine", 2, "o/pub is PUBLIC"),
                                    (f"cd '~/{pub}' && git commit -m fine", 2, f"{literal}"),
                                    (f'cd ~""/{pub} && git commit -m fine', 2, f"{literal}"),
                                    (f"cd ~nobody/{pub} && git commit -m fine", 2, f"{literal}nobody"),
                                    (f"git -C '~/{pub}' commit -m fine", 2, f"{literal}"),
                                    (f'GIT_DIR="~/{pub}" git commit -m fine', 2, f"{literal}"),
                                    (f"git --git-dir=~/{pub} commit -m fine", 2, f"{literal}"),
                                    (f"HOME=/zz; cd ~/{pub} && git commit -m fine", 2, f"{literal}"),
                                    (f"HOME=/zz cd ~/{pub} && git commit -m fine", 2, f"{literal}"),
                                    (f'HOME=/zz bash -c "cd ~/{pub} && git commit -m fine"', 2, f"{literal}")):
            code, o, e = run(["hook"], athome, stdin=ev(command, cwd=home))
            assert code == want and text in e and "Traceback" not in e, (command, code, o, e)
        for command in ("git commit -m fine", f'git commit -m "see git -C {privclone} commit"', f'git -C "{privclone}" commit -m fine'):
            code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev(command, cwd=pubclone))
            assert code == 2 and "o/pub is PUBLIC" in e, (command, code, o, e)
        code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev("git -C pubclone commit -m fine", cwd=tmp))
        assert code == 2 and "o/pub is PUBLIC" in e, (code, o, e)
        # a cd inside quotes, or with quotes that do not balance, adds a base and loses none
        for command in ('git commit -m "x\ncd /elsewhere now" && git -C a/b commit -m y',
                        "cd a && git -C b commit -F - <<'EOF'\ndon't\nEOF",
                        "cd a && git commit -F - <<EOF\ncd into the repo\nEOF\ngit -C b commit -m y"):
            assert join_dir(tmp, "a/b") in target_dirs(command, tmp), command
        assert target_dirs("git grep -C 3 commit; git diff -C away -- commit.py", tmp) == [], "a subcommand's -C is no git -C"
        try:
            target_dirs('git commit -m "x\n' + "\n".join(f"cd d{i}" for i in range(6)) + '"', tmp)
            raise AssertionError("six relative cds inside quotes")
        except Block as b:
            assert "too many to judge" in str(b), b
        # a long quoted body that mentions git -C many times is scanned in linear time: the hook
        # stays far inside its registered timeout with a list and a hit
        big = "secretword\n" + "\n".join("- run git -C ../repo-a status --short, then git -C ../repo-b log -1 and git -C ../repo-c diff --stat" for _ in range(170))
        t = time.monotonic()
        blocked(f"gh pr comment 1 --body '{big}'", "entry 1")
        took = time.monotonic() - t
        assert took < 5, took
        # one read budget for the whole event: a gh that answers after 4 s is cut off on the second
        # directory, and the hook refuses inside its own timeout
        t = time.monotonic()
        code, o, e = run(["hook"], vis("slow4"), stdin=ev(f'cd "{privclone}" && git commit -m a && cd "{pubclone}" && git commit -m b', cwd=home), timeout=30)
        assert code == 2 and "did not answer within the hook's budget" in e and time.monotonic() - t < 9, (code, e, time.monotonic() - t)
        # with a list, the binding's "off" passes a hit; no key and
        # "required" keep it, and no key reads no visibility (no gh on PATH changes nothing)
        for key, want in (("off", 0), ("required", 2)):
            bound = mkrepo("listed-" + key, {".claude/ouro.toml": toml.replace('review = "none"\n', f'review = "none"\nforbidden_check = "{key}"\n').encode()})
            code, o, e = run(["hook"], vis("fail", hl), stdin=ev("gh pr merge 1 --body secretword", cwd=bound))
            assert code == want and "Traceback" not in e and (want == 0 or "entry 1" in e), (key, code, o, e)
            code, o, e = run(["hook"], vis("fail", hl), stdin=ev("git commit -F note.md", cwd=mkrepo("listed-file-" + key, {"note.md": b"acme corp\n"})))
            assert code == 2 and "entry 2" in e, (key, code, o, e)  # no binding: the list governs
        bound = mkrepo("listed-absent", {".claude/ouro.toml": toml.encode()})
        code, o, e = run(["hook"], {**hl, "PATH": nogh}, stdin=ev("gh pr merge 1 --body secretword", cwd=bound))
        assert code == 2 and "entry 1" in e, (code, o, e)
        bound = mkrepo("listed-broken", {".claude/ouro.toml": toml.replace('review = "none"', 'review = "bogus"\nforbidden_check = "off"').encode()})
        code, o, e = run(["hook"], vis("fail", hl), stdin=ev("gh pr merge 1 --body secretword", cwd=bound))
        assert code == 2 and "entry 1" in e, (code, o, e)
        bound = mkrepo("listed-off-file", {".claude/ouro.toml": toml.replace('review = "none"\n', 'review = "none"\nforbidden_check = "off"\n').encode(), "note.md": b"acme corp\n"})
        allowed("git commit -F note.md", env=vis("fail", hl), cwd=bound)
        # a literal -C target into it passes too; one through a shell variable cannot be read, so the hit stands, and the
        # refusal names that target and how to clear it
        allowed(f'git -C "{bound}" commit -m secretword', env=vis("fail", hl), cwd=bound)
        for command in (f'W="{bound}" && git -C $W commit -m secretword', f'W="{bound}" && cd $W && git commit -m secretword'):
            blocked(command, "is a shell variable or `cd -` the hook does not expand, so the binding's forbidden_check cannot apply there; "
                    "write the directory literally", env=vis("fail", hl), cwd=bound)
            blocked(command, "entry 1", env=vis("fail", hl), cwd=bound)
        # "off" is the binding's repository's: a call naming another with -R or --repo keeps the hit,
        # the binding's own in any case or spelling passes it; with no list the other is read
        for command in ("gh -R other/repo pr merge 1 --body secretword", "gh pr merge 1 --repo=other/repo --body secretword",
                        'gh pr merge 1 --repo "github.com/other/repo" --body secretword'):
            code, o, e = run(["hook"], vis("fail", hl), stdin=ev(command, cwd=bound))
            assert code == 2 and "entry 1" in e, (command, code, o, e)
            blocked(command, "not private, and the machine has no list (", env=vis("PUBLIC"), cwd=bound)
            allowed(command, env=vis("PRIVATE"), cwd=bound)
        for command in ("gh -R O/R pr merge 1 --body secretword", "gh pr merge 1 --repo=https://github.com/o/r/ --body secretword"):
            allowed(command, env=vis("fail", hl), cwd=bound)
            allowed(command, env=vis("fail"), cwd=bound)
        # a -R inside a quoted body counts too: the hook cannot tell it from the flag, so it fails closed
        command = "gh pr create --body '-R o/r' --repo other/repo --title secretword"
        code, o, e = run(["hook"], vis("fail", hl), stdin=ev(command, cwd=bound))
        assert code == 2 and "entry 1" in e, (code, o, e)
        blocked(command, "not private, and the machine has no list (", env=vis("PUBLIC"), cwd=bound)
        # a -R or --repo value is the call's address, not scanned content; quoted text still is
        for command in ("gh issue comment 5 -R o/secretword --body-file clean.md",
                        "gh issue comment 5 --repo=o/secretword --body-file clean.md",
                        'gh issue comment 5 -R "o/secretword" --body-file clean.md'):
            allowed(command)
        blocked("gh issue comment 5 -R o/secretword --body-file hit.md", "the file --body-file names, line 2: entry 1")
        blocked("gh issue comment 5 --body 'see -R o/secretword'", "command text line 1: entry 1")
        blocked("gh issue comment 5 -R o/secretword --body \"fine", "command text line 1: entry 1")
        # a list whose `# protects:` line names repositories scans a call only into one of them: a
        # directory's repo.slug, else its origin, one that cannot be resolved, or a -R or --repo;
        # "required" scans and "off" passes whatever the header names; no header scans every call
        prot = lst("prot.txt", b"# protects: O/Prot https://github.com/o/mirror.git\nsecretword\n")

        def slugged(name, value, key=None):
            t = toml.replace('slug = "o/r"', f'slug = "{value}"')
            if key:
                t = t.replace('review = "none"\n', f'review = "none"\nforbidden_check = "{key}"\n')
            return mkrepo(name, {".claude/ouro.toml": t.encode()})

        def origined(name, url):
            d = mkrepo(name, {"x.txt": b"x\n"})
            subprocess.run(["git", "remote", "add", "origin", url], cwd=d, check=True)
            return d
        protected, mirror, other = slugged("p-prot", "o/prot"), slugged("p-mirror", "o/mirror"), slugged("p-other", "o/other")
        rows = [(prot, "gh pr merge 1 --body secretword", protected, 2),
                (prot, "gh pr merge 1 --body secretword", mirror, 2),
                (prot, "gh pr merge 1 --body secretword", other, 0),
                (prot, "gh -R o/prot pr merge 1 --body secretword", other, 2),
                (prot, "gh pr merge 1 --repo https://github.com/O/Mirror --body secretword", other, 2),
                (prot, "gh -R o/else pr merge 1 --body secretword", other, 0),
                (prot, "gh issue comment https://github.com/o/prot/issues/5 --body secretword", other, 2),
                (prot, "gh pr merge https://github.com/O/Mirror/pull/7 --body secretword", other, 2),
                (prot, "gh issue comment https://github.com/o/else/issues/5 --body secretword", other, 0),
                (prot, "GH_REPO=o/prot gh issue comment 5 --body secretword", other, 2),
                (prot, "gh issue comment 5 -Ro/prot --body secretword", other, 2),
                (prot, "gh pr merge 1 --repo https://github.com/o/else --body secretword", other, 0),
                (prot, "GH_REPO=o/else gh issue comment 5 --body secretword", other, 0),
                (prot, "gh issue comment 5 -Ro/else --body secretword", other, 0),
                (prot, "gh issue comment https://github.com/o/prot/issues/5 --body secretword",
                 slugged("p-off-other", "o/other", "off"), 2),
                (prot, f'cd "{protected}" && git commit -m secretword', other, 2),
                (prot, "git commit -m secretword", origined("p-origin-else", "git@github.com:o/else.git"), 0),
                (prot, "git commit -m secretword", origined("p-origin-prot", "git@github.com:O/Prot.git"), 2),
                (prot, "git commit -m secretword", mkrepo("p-no-origin", {"x.txt": b"x\n"}), 2),
                (prot, "git commit -m secretword", origined("p-origin-bare", "foo"), 2),
                (prot, "git commit -m secretword", origined("p-origin-path", "/srv/mirrors/prot-copy"), 2),
                (prot, "git commit -m secretword", origined("p-origin-drive", "C:/mirrors/prot-copy"), 2),
                (prot, "git commit -m secretword", origined("p-origin-else-ssh", "ssh://git@github.com:22/o/else.git"), 0),
                (prot, "git commit -m secretword", norepo, 2),
                (prot, "git commit -m secretword", slugged("p-required", "o/other", "required"), 2),
                (prot, "git commit -m secretword", slugged("p-off", "o/prot", "off"), 0),
                (prot, "gh issue comment HTTPS://GITHUB.COM/o/prot/issues/5 --body secretword", other, 2),
                (prot, "git commit -m fix -m -Rsecretword", protected, 2),
                (hl, "gh pr merge 1 --body secretword", other, 2),
                (hl, "gh -R o/else pr merge 1 --body secretword", other, 2),
                (hl, "git commit -m fix -m -Rsecretword", other, 2),
                (hl, "gh issue comment 5 --body -Rsecretword", other, 2),
                (hl, "GH_REPO=o/else gh issue comment 5 --body secretword", slugged("p-off-hl", "o/other", "off"), 0)]
        for env, command, where, want in rows:
            code, o, e = run(["hook"], vis("fail", env), stdin=ev(command, cwd=where))
            assert code == want and "Traceback" not in e and (want == 0 or "entry 1" in e), (command, where, code, o, e)
        # a read that stalls blocks inside the hook's own timeout
        started = time.monotonic()
        # from the temp root, which outlives the orphaned stand-in that holds its directory
        code, o, e = run(["hook"], vis("slow"), stdin=ev("gh pr merge 1 --body fine", cwd=tempfile.gettempdir()), timeout=30)
        assert code == 2 and "did not answer within the hook's budget, and the machine has no list (" in e and time.monotonic() - started < 9, (code, e, time.monotonic() - started)
        # a binding read that stalls names the budget too, not a read that failed: a copy of this script beside a
        # stand-in binding that sleeps on one chosen key
        copy = os.path.join(tmp, "hookcopy")
        os.makedirs(copy)
        shutil.copyfile(this, os.path.join(copy, os.path.basename(this)))
        with open(os.path.join(copy, "ouro-binding.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write('import os, sys, time\na = sys.argv[1:]\nif a[-1:] == [os.environ.get("STUB_SLOW_KEY")]:\n    time.sleep(20)\n'
                    'if a == ["check"]:\n    sys.exit(0)\nif a[:1] == ["get"]:\n'
                    '    if a[1] == "ship.forbidden_check":\n        print("error: no such key: ship.forbidden_check", file=sys.stderr)\n        sys.exit(1)\n'
                    '    if a[1] == "repo.slug":\n        print("o/r")\n        sys.exit(0)\nsys.exit(1)\n')
        hook_copy = os.path.join(copy, os.path.basename(this))
        for key, what in (("check", "the binding"), ("ship.forbidden_check", "the binding's ship.forbidden_check"), ("repo.slug", "the binding's repo.slug")):
            started = time.monotonic()
            code, o, e = run(["hook"], {**vis("PRIVATE"), "STUB_SLOW_KEY": key}, stdin=ev("gh pr merge 1 --body fine", cwd=tempfile.gettempdir()), timeout=30, script=hook_copy)
            assert code == 2 and f"{what} could not be read within the hook's budget, and the machine has no list (" in e and time.monotonic() - started < 9, (key, code, o, e, time.monotonic() - started)
        code, o, e = run(["hook"], vis("PRIVATE"), stdin=ev("gh pr merge 1 --body fine", cwd=tempfile.gettempdir()), timeout=30, script=hook_copy)
        assert code == 0 and "Traceback" not in e, ("control: the stand-in binding with no stalled read passes a private repository", code, o, e)
        # a payload the hook cannot parse
        for raw in (b"", b"not json", b"[]", b"null", b"{}", b'{"tool_name": 5}', b"\xff\xfe",
                    b'{"tool_name": "Bash"}', b'{"tool_name": "Bash", "tool_input": []}',
                    b'{"tool_name": "PowerShell", "tool_input": {"command": 5}}',
                    b'{"tool_name": "Bash", "tool_input": {"command": "gh pr merge 1", "x": '):
            code, o, e = run(["hook"], hl, stdin=raw)
            assert (code, o) == (2, "") and "blocked" in e and "Traceback" not in e, (raw, code, o, e)
        code, o, e = run(["hook"], hl, stdin=b"\xef\xbb\xbf" + ev("gh pr merge 1 --body secretword"))
        assert code == 2 and "entry 1" in e, "BOM on the event"
        assert run(["hook"], hl, stdin=ev("echo hi", cwd=5))[0] == 0
        # an error of its own blocks too, with no traceback
        code, o, e = run(["hook"], hl, stdin=ev("gh pr create --body-file clean.md", cwd=5))
        assert (code, o) == (2, "") and "the hook failed" in e and "Traceback" not in e, (code, o, e)
        assert run(["hook", "extra"])[0] == 2 and run(["hook", "-x"])[0] == 2
    print("selftest ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        selftest()
        sys.exit(0)
    if args == ["check"]:
        sys.exit(cmd_check())
    if args == ["scan"]:
        sys.exit(cmd_scan())
    if args == ["hook"]:
        sys.exit(cmd_hook())
    print("usage: forbidden-tokens.py check | scan | hook | --selftest", file=sys.stderr)
    sys.exit(2)
