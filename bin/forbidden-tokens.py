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
is dropped. Entries are numbered from 1 in file order, comments and blank lines not counted.

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
hook    An example Claude Code PreToolUse hook, registered by the owner in their own settings.json
        (README.md, `The private token list`). It reads the event's JSON on standard input and
        acts on a Bash or PowerShell call whose `tool_input.command` holds `gh issue create`
        (or `new`), `comment`, `edit`, `close` or `reopen`, `gh pr create` (or `new`), `edit`,
        `comment`, `review`, `merge`, `close`, `reopen` or `revert`, or `git commit`. `gh` and
        `git` are matched in any case with an optional path and `.exe`; gh global flags may
        precede the noun and flags may sit between noun and verb; git options before `commit`
        are included. For such a call it checks the whole command text, a heredoc included, and
        the contents of a file named by `--body-file`, `-F` or `--file` (spaced or `=` form
        anywhere, `-F<file>` attached only between the publishing call and the end of its line,
        relative to the event's `cwd`). A value of `-`, or a path under `/dev/` or `/proc/` once
        slashes, `.` and `..` are normalised (as given and joined to the event's `cwd`), is input
        the hook cannot see and passes only when the whole command is one publishing gh or git
        call whose first line ends in a quoted heredoc delimiter, `<<'DELIM'` or `<<"DELIM"`
        (`<<-` allowed; an unquoted delimiter never passes), holds no `;`, `&`, `|`, `<`, `>`,
        parenthesis, brace, backtick, `#` or `$'`, and has balanced quotes before the operator,
        followed by the heredoc body and the closing delimiter and nothing else. Exit 0 lets the
        call run: no hit, or any other tool call or command.
        Exit 2 blocks it, and Claude Code shows stderr to the session as the reason: a hit
        (`command text line <l>: entry <n>`, `command text: entry <n>, across a line break`,
        `the file <flag> names, line <l>: entry <n>`, or `the file <flag> names: entry <n>,
        across a line break`), a missing list, a body file it cannot read, a body it cannot see,
        a payload it cannot parse, or any error of its own. Any other nonzero exit would let the
        call run, so this mode has none. It does not see `gh api`, nor a value expanded at run
        time. A body file is read from the session's working directory before the command runs,
        so a `cd` or a write to the same file earlier in that command is not seen. A body or
        later command that mentions `--file`, `--body-file` or a spaced `-F` in prose is read as
        a file name and blocks. A hook whose `python3` cannot be found exits 127, and one that
        exceeds Claude Code's hook timeout does not block either: Claude Code treats both as
        non-blocking, so a missing interpreter or a stall disables the hook.
--selftest  Runs each mode against a temporary list and repository.

A hit is reported by path, line number and entry number, never by the matched text or the entry,
since gate output is pasted into pull request bodies.

Stdlib only.
"""
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys

VAR = "OURO_FORBIDDEN_TOKENS"
SCAN_DIRS = ("skills", "agents", "templates", "docs")


class MissingList(Exception):
    pass


def load_list(environ=None):
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
    entries = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entries.append(re.compile(line, re.IGNORECASE))
        except Exception:
            raise MissingList(f"line {lineno} of the file {VAR} names holds an entry that does not compile")
    if not entries:
        raise MissingList(f"the file {VAR} names holds no entry")
    return entries


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
        entries = load_list()
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
_FLAGS = "(?:" + _WS + r"--?[A-Za-z0-9][\w-]*(?:=\S+|" + _WS + r"[^\s-]\S*)?)*"
PUBLISH = re.compile(
    r"(?<![\w-])(?i:gh(?:\.exe)?)[\"']?" + _FLAGS + _WS + r"(?:"
    r"issue" + _FLAGS + _WS + r"(?:create|new|comment|edit|close|reopen)|"
    r"pr" + _FLAGS + _WS + r"(?:create|new|edit|comment|review|merge|close|reopen|revert))(?![\w-])|"
    r"(?<![\w-])(?i:git(?:\.exe)?)[\"']?\s+(?:[^\s;&|]+\s+)*?commit(?![\w-])")
BODY_FILE = re.compile(
    r"""(?<!\S)(--body-file|--file|-F)(?:=|(?:\\\r?\n|\s)+)("[^"]*"|'[^']*'|[^\s;&|)]+)""")
ATTACHED = re.compile(r"""(?<!\S)(-F)(?=[^\s=])("[^"]*"|'[^']*'|[^\s;&|)]+)""")
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
    only between a publishing match and the end of its line."""
    found = [(m.start(), m.group(1), m.group(2)) for m in BODY_FILE.finditer(command)]
    for p in PUBLISH.finditer(command):
        eol = command.find("\n", p.start())
        end = len(command) if eol < 0 else eol
        found += [(m.start(), m.group(1), m.group(2)) for m in ATTACHED.finditer(command, p.start(), end)]
    return [(f, v) for _, f, v in sorted(set(found))]


class Block(Exception):
    pass


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
    try:
        entries = load_list()
    except MissingList as e:
        raise Block(f"missing list: {e}")
    reasons = []
    found = hits(command, entries)
    for lineno, n in found:
        reasons.append(f"command text line {lineno}: entry {n}")
    for n in wrapped(command, entries, found):
        reasons.append(f"command text: entry {n}, across a line break")
    for flag, value in body_flags(command):
        value = value[1:-1] if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1 else value
        joined = os.path.join(payload.get("cwd") or os.getcwd(), value)
        norms = [posixpath.normpath(re.sub(r"/+", "/", s.replace("\\", "/"))) for s in (value, joined)]
        if value == "-" or any(s.startswith(("/dev/", "/proc/")) for s in norms):
            if not lone_heredoc(command):
                raise Block(f"the body of {flag} comes from input the hook cannot see")
            continue
        try:
            with open(os.path.join(payload.get("cwd") or os.getcwd(), value), "rb") as f:
                text = f.read().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            raise Block(f"the file {flag} names cannot be read ({type(e).__name__}); "
                        "retry with an absolute path to a UTF-8 file")
        found = hits(text, entries)
        for lineno, n in found:
            reasons.append(f"the file {flag} names, line {lineno}: entry {n}")
        for n in wrapped(text, entries, found):
            reasons.append(f"the file {flag} names: entry {n}, across a line break")
    return reasons


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
        entries = load_list()
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

    def run(args, env_add=None, cwd=None, stdin=b"", unset=(), timeout=None):
        env = {k: v for k, v in os.environ.items() if k not in (VAR, "CI") and k not in unset}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(env_add or {})
        r = subprocess.run([sys.executable, this, *args], input=stdin, capture_output=True,
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
                "git commit -a")
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
                    "xgh issue create secretword", "xgit commit secretword", "ls"):
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
        blocked("gh issue comment 1 --body-file missing.md", "cannot be read", cwd=os.path.join(work, "dir"))
        blocked("gh issue comment 1 --body-file hit.md", "cannot be read", cwd=os.path.join(work, "dir"))
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
        # a hostile event must not stall the hook
        for hostile in ("gh " + "--aa " * 30 + "x" * 100000, "gh " + "--aa " * 30 + "issue " + "x" * 100000,
                        "gh " + "--aa=" + "x" * 100000, "git " + "x " * 30 + "x" * 100000,
                        "gh " + "-" * 100000):
            run(["hook"], hl, stdin=ev(hostile), timeout=2)
        # a missing list blocks a publishing command and no other
        for env, cause in (({}, VAR + " is not set"), ({VAR: os.path.join(tmp, "nope.txt")}, "cannot be read"),
                           (bad, "line 3 of the file"), (empty, "holds no entry")):
            blocked("gh pr merge 1 --body fine", cause, env=env)
            blocked("gh pr merge 1 --body fine", "blocked: missing list: ", env=env)
            blocked("git commit -m fine", "missing list: ", env=env, tool="PowerShell")
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
