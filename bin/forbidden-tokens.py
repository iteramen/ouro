#!/usr/bin/env python3
"""forbidden-tokens.py -- check text against a private list of forbidden patterns that lives
outside every repository, and scan the text this plugin ships.

    python3 forbidden-tokens.py check  < text
    python3 forbidden-tokens.py scan
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
--selftest  Runs each mode against a temporary list and repository.

A hit is reported by path, line number and entry number, never by the matched text or the entry,
since gate output is pasted into pull request bodies.

Stdlib only; Python 3.12+.
"""
import os
import re
import subprocess
import sys

if tuple(sys.version_info[:2]) < (3, 12):
    sys.stderr.write(f"forbidden-tokens.py needs Python 3.12+; this python is {sys.version_info[0]}.{sys.version_info[1]}\n")
    sys.exit(2)

VAR = "OURO_FORBIDDEN_TOKENS"
SCAN_DIRS = ("skills", "agents", "templates", "docs")


class MissingList(Exception):
    pass


def load_list():
    """The compiled entries."""
    path = os.environ.get(VAR, "")
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
    for minor in (10, 11):
        for mode in ("check", "scan", "--selftest"):
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
    print("usage: forbidden-tokens.py check | scan | --selftest", file=sys.stderr)
    sys.exit(2)
