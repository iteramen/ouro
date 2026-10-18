"""Fixture suite for the ouro driver (bin/ouro): the run sequence, its failure table, the pure decisions.

The driver runs as `python3 bin/ouro <command>` in a scratch fixture repository whose origin is a
local bare repository. gh is a stand-in first on PATH (tests/driver/gh_stub.py behind a gh.cmd or an
executable gh) that answers from a per-case JSON table, models the issue and the one pull request,
and logs every call; the land scripts and gates the driver spawns meet the same stand-in. The
nodes are stubbed through OURO_NODE_STUB, a directory of canned answers per case, so a transition
is red when the driver ignores the answer. Nothing touches a network or a real remote.

OURO_TEST_ONLY=<text> runs only the sections whose name holds the text (a mutation probe's row).
OURO_LIVE_NODE=1 adds one real `claude -p` call; without it that section prints a SKIP: line.

stdlib only; python3 3.11+, git and pwsh on PATH. Its [[gate]] in .claude/ouro.toml:
run = "python3 tests/driver/driver.Tests.py"
"""
import atexit
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True
BASE = pathlib.Path(__file__).resolve().parents[2]
BIN = BASE / "bin"
if not (BIN / "ouro" / "__main__.py").is_file():
    print("skip: no bin/ouro beside the flat gate scripts (vendored layout); the driver ships with the plugin")
    sys.exit(0)
STUB = pathlib.Path(__file__).resolve().parent / "gh_stub.py"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="driver-test-run-"))
ONLY = os.environ.get("OURO_TEST_ONLY", "")
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
sys.path.insert(0, str(BIN))


def _remove_tmp():
    for root, _, files in os.walk(TMP):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(_remove_tmp)
failures = 0


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


def section(name, fn):
    global failures
    if ONLY and ONLY not in name:
        return
    print(name)
    try:
        fn()
    except Exception:
        failures += 1
        print("FAIL: the section raised\n" + traceback.format_exc())


# ---- the stand-in gh, first on PATH ----------------------------------------------------------
STUBDIR = TMP / "stub"
STUBDIR.mkdir()
if os.name == "nt":
    (STUBDIR / "gh.cmd").write_text(f'@"{sys.executable}" "{STUB}" %*\r\n', encoding="utf-8")
else:
    (STUBDIR / "gh").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" "$@"\n', encoding="utf-8")
    (STUBDIR / "gh").chmod(0o755)


def shim(directory, name, script):
    """An executable `name` in `directory` that runs the python `script` with its arguments."""
    directory.mkdir()
    if os.name == "nt":
        (directory / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        (directory / name).write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        (directory / name).chmod(0o755)


GROKDIR, CLAUDEDIR = TMP / "grokshim", TMP / "claudeshim"
shim(GROKDIR, "grok", STUB.parent / "fake_grok.py")
(TMP / "fake_claude.py").write_text(
    "import json, os, sys\n"
    "open(os.environ['FAKE_CLAUDE_ARGV'], 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
    "if os.environ['FAKE_CLAUDE_MODE'] == 'garbage':\n"
    "    sys.stderr.write('claude: the boom tail\\n')\n"
    "    print('this is not json')\n"
    "    sys.exit(1)\n"
    "print(json.dumps({'is_error': True, 'result': 'the result text', 'session_id': 'S-ERR'}))\n", encoding="utf-8")
shim(CLAUDEDIR, "claude", TMP / "fake_claude.py")
EMPTYCFG = TMP / "gitconfig"
EMPTYCFG.write_text("", encoding="utf-8")
GITENV = {"GIT_CONFIG_GLOBAL": str(EMPTYCFG), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
os.environ.update(GITENV)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PATH"] = str(STUBDIR) + os.pathsep + os.environ["PATH"]


def sh(*argv, cwd=None, env=None, input_text=None):
    p = subprocess.run(list(map(str, argv)), cwd=cwd, capture_output=True, env={**os.environ, **(env or {})}, input=input_text)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def git(cwd, *a):
    code, out, err = sh("git", *a, cwd=cwd)
    if code != 0:
        raise RuntimeError(f"git {' '.join(a)} failed: {out}{err}")
    return out.strip()


BINDING = """schema = 1
[repo]
slug = "o/n"
default_branch = "master"
protected_branch_prefixes = ["release/"]
[labels]
area = ["gates"]
type = ["bug"]
[[gate]]
areas = ["*"]
run = "python3 gates/check.py"
ci = "echo the checks print a skip"
[ship]
policy = "{policy}"
review = "{review}"
direct = ["docs/*.md"]
[owner]
ruling_approvers = ["o"]
"""
ISSUE_BODY = """## What
Fix the total.

## Deliverable
- `src/app.py` -- compute_total -- "def compute_total(items):"

Doc impact on close: none

Size: S
Flow: standard
"""


TRIAGED = "2026-01-01T00:00:00Z"


def issue(n=1, labels=("agent-ready", "gates", "bug"), body=ISSUE_BODY, comments=None, title="Fix the total", state="OPEN"):
    return {"number": n, "title": title, "body": body, "state": state, "labels": [{"name": l} for l in labels],
            "comments": comments if comments is not None else [{"author": {"login": "o"}, "body": "**Triage**\n\nok", "createdAt": TRIAGED}]}


class Case:
    """One scratch world: a main checkout with a bare origin, a gh table, a node-stub directory."""

    def __init__(self, name, policy="stop-at-pr", review="adversarial-review", binding=None, table=None, files=None):
        self.name = name
        self.root = TMP / name
        self.main, self.origin = self.root / "main", self.root / "origin.git"
        self.state, self.nodes = self.root / "state", self.root / "nodes"
        for d in (self.state, self.nodes):
            d.mkdir(parents=True)
        sh("git", "init", "-q", "--bare", "-b", "master", str(self.origin))
        sh("git", "init", "-q", "-b", "master", str(self.main))
        self.write(".claude/ouro.toml", binding or BINDING.format(policy=policy, review=review))
        self.write(".gitignore", ".claude/worktrees/\n")
        self.write("src/app.py", "def compute_total(items):\n    return sum(items)\n")
        self.write("gates/check.py", "import pathlib, sys\nf = pathlib.Path('gates/exit.txt')\nsys.exit(int(f.read_text()) if f.is_file() else 0)\n")
        self.write("docs/note.md", "note\n")
        for rel, text in (files or {}).items():
            self.write(rel, text)
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "init")
        git(self.main, "remote", "add", "origin", str(self.origin))
        git(self.main, "push", "-q", "origin", "master")
        self.set_table({"_issue_1": issue(), **(table or {})})

    def write(self, rel, text):
        p = self.main / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def set_table(self, table):
        (self.root / "gh-table.json").write_text(json.dumps(table), encoding="utf-8")

    def node(self, name, answer, k=None):
        (self.nodes / (f"{name}.{k}.json" if k else f"{name}.json")).write_text(json.dumps(answer), encoding="utf-8")

    def env(self, extra=None):
        return {"STUB_GH_TABLE": str(self.root / "gh-table.json"), "STUB_GH_LOG": str(self.root / "gh.log"),
                "STUB_GH_STATE": str(self.state), "STUB_GH_ORIGIN": str(self.origin), "OURO_NODE_STUB": str(self.nodes),
                "OURO_WATCH_LIMIT": "120", **(extra or {})}

    def ouro(self, *args, extra=None):
        code, out, err = sh(sys.executable, BIN / "ouro", *args, cwd=self.main, env=self.env(extra))
        print("    | " + (out + err).strip().replace("\n", "\n    | ")[-3000:])
        return code, out, err

    def calls(self):
        p = self.state / "calls.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l] if p.is_file() else []

    def sigs(self):
        return [c["sig"] for c in self.calls()]

    def wrote(self):
        """Calls that change the tracker."""
        return [s for s in self.sigs() if re.match(r"(issue (comment|edit|create)|pr (create|comment|ready|edit|merge))", s)]

    def pr_comments(self):
        p = self.state / "pr-7-comments.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    def issue_state(self, n=1):
        p = self.state / f"issue-{n}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def pr(self):
        p = self.state / "pr.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def prompts(self, name):
        return sorted(p.name for p in self.nodes.glob(f"{name}.*.prompt.txt"))

    def grok(self, *answers):
        """Canned answers for the stand-in `grok`, and the env that puts it on PATH with a private temp directory."""
        d, t = self.root / "grok", self.root / "tmp"
        d.mkdir(exist_ok=True)
        t.mkdir(exist_ok=True)
        for k, a in enumerate(answers, 1):
            (d / f"grok.{k}.json").write_text(json.dumps(a), encoding="utf-8")
        return {"PATH": str(GROKDIR) + os.pathsep + os.environ["PATH"], "FAKE_GROK_DIR": str(d), "TMP": str(t), "TEMP": str(t), "TMPDIR": str(t)}

    def grok_calls(self):
        p = self.root / "grok" / "calls.txt"
        return p.read_text(encoding="utf-8").splitlines() if p.is_file() else []

    def node_call(self, name, k=1):
        return json.loads((self.nodes / f"{name}.{k}.call.json").read_text(encoding="utf-8"))

    def clear_prompts(self):
        for p in self.nodes.glob("*.prompt.txt"):
            p.unlink()

    def branches(self):
        return git(self.origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()


BUILD = {"subject": "fix(app): sum the items", "body": "Sum the items in compute_total.", "claims": [], "gates_run": [],
         "residue": [], "door": "two-way: a revert undoes it", "blast_radius": "the total, for callers",
         "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) + 0\n"}}
CLEAN = {"verdict": "CLEAN", "findings": [], "false_record_sentences": 0, "files_read": ["src/app.py"]}


def finding(sev="H", claim="the total drops the last item"):
    return {"severity": sev, "file": "src/app.py", "line": 2, "claim": claim, "evidence": "probe"}


def stop_comment_ok(body, reason):
    lines = body.split("\n")
    return lines[0] == f"**Stop:** {reason}" and lines[1] == "" and len(lines) > 2 and lines[2] != ""


def labels_of(case, n=1):
    return [l["name"] for l in (case.issue_state(n) or {"labels": []})["labels"]]


def happy_case(name, **kw):
    c = Case(name, **kw)
    c.node("BUILD", BUILD)
    c.node("REVIEW", CLEAN)
    return c


# ---- pure rows -------------------------------------------------------------------------------
from ouro import node as nodemod  # noqa: E402
from ouro import run as runmod  # noqa: E402
from ouro import sh as shmod  # noqa: E402
from ouro import triage as triagemod  # noqa: E402


def sec_may_merge():
    mm = runmod.may_merge
    check("stop-at-pr never merges, not even a trivial issue", mm("stop-at-pr", None, ["trivial"], "express") is False)
    check("trivial-merge merges a trivial issue", mm("trivial-merge", None, ["trivial"], "full") is True)
    check("trivial-merge leaves an issue without the label to the owner", mm("trivial-merge", None, ["agent-ready"], "express") is False)
    check("merge-on-green default classes include express", mm("merge-on-green", None, [], "express") is True)
    check("merge-on-green default classes include standard", mm("merge-on-green", None, [], "standard") is True)
    check("merge-on-green default classes leave full to the owner", mm("merge-on-green", None, [], "full") is False)
    check("merge-on-green with full listed merges full", mm("merge-on-green", ["full"], [], "full") is True)
    check("a listed class does not admit its neighbour", mm("merge-on-green", ["full"], [], "standard") is False)
    check("an empty class list merges nothing", mm("merge-on-green", [], [], "express") is False)
    check("an unknown policy merges nothing", mm("merge-on-everything", None, ["trivial"], "express") is False)


def sec_pure():
    check("slugify keeps five kebab words", runmod.slugify("Fix the total of the whole thing now please") == "fix-the-total-of-the")
    check("slugify of punctuation alone has a name", runmod.slugify("!!!") == "change")
    body = "- [x] 1. **One (Flow: express).** a\n- [ ] 2. **Two (Flow: full).** b\n- [ ]3. no space\n"
    check("parse_parts reads number, tick and text; a malformed item is no part", [(k, t) for k, t, _ in runmod.parse_parts(body)] == [(1, True), (2, False)])
    check("body_line takes the first Flow line", runmod.body_line("Flow: full\nFlow: express\n", "Flow") == "full")
    check("body_line is case-sensitive", runmod.body_line("flow: full\n", "Flow") is None)
    check("derive_flow: docs only is express", runmod.derive_flow(["docs/a.md"], set()) == "express")
    check("derive_flow: a script is standard", runmod.derive_flow(["bin/x.ps1"], set()) == "standard")
    check("derive_flow: a hook is full", runmod.derive_flow(["hooks/x.py"], set()) == "full")
    check("derive_flow: a gate's own file is full", runmod.derive_flow(["tests/t.py"], {"tests/t.py"}) == "full")
    check("flow only tightens", runmod.tighter("full", "express") == "full" and runmod.tighter("express", "standard") == "standard")
    sha = "a" * 40
    good = {"author": {"login": "O"}, "body": f"**Review** round 2: FINDINGS @ {sha}\n\ntext\n```json\n{{\"findings\": [{{\"severity\": \"M\"}}]}}\n```\n"}
    trusted = {"o"}
    rec = runmod.parse_review_comment(good, trusted)
    check("a review marker from a trusted author (case-insensitive) parses", rec and rec["round"] == 2 and rec["verdict"] == "FINDINGS" and rec["sha"] == sha)
    check("its findings come from the json block", rec and runmod.needs_fix(rec))
    near = {
        "an untrusted author": {**good, "author": {"login": "stranger"}},
        "a null author": {**good, "author": None},
        "a trailing space on line 1": {**good, "body": good["body"].replace("FINDINGS @ " + sha, "FINDINGS @ " + sha + " ")},
        "a lower-case marker": {**good, "body": good["body"].replace("**Review**", "**review**")},
        "a short sha": {**good, "body": good["body"].replace(sha, "a" * 7)},
        "an upper-case sha": {**good, "body": good["body"].replace(sha, "A" * 40)},
        "the marker on line 2": {**good, "body": "intro\n" + good["body"]},
        "an unknown verdict": {**good, "body": good["body"].replace("FINDINGS", "MAYBE")},
        "a null body": {**good, "body": None},
    }
    for what, c in near.items():
        check(f"near miss refused: {what}", runmod.parse_review_comment(c, trusted) is None)
    check("a CRLF body still parses", runmod.parse_review_comment({**good, "body": good["body"].replace("\n", "\r\n")}, trusted) is not None)
    dup = runmod.review_records([good, good], trusted)
    check("a duplicated round is counted once", len(dup) == 1)
    r1 = {"round": 1, "findings": [finding("H"), finding("l"), {"severity": "M"}], "false": 1}
    r2 = {"round": 2, "findings": [finding("L")], "false": "unread"}
    check("review_line counts severities, fix rounds and false sentences",
          runmod.review_line([r1, r2], True) == "Review: rounds:1 findings:H1/M1/L2 false-sentences:1")
    check("review_line is `none` with no reviewer", runmod.review_line([], False) == "Review: none")
    check("review_line says unread when no round counted false sentences",
          runmod.review_line([{"round": 1, "findings": [], "false": None}], True).endswith("false-sentences:unread"))
    pattern = re.compile(r"^Review: (?:none|rounds:([0-9]+) findings:H([0-9]+)/M([0-9]+)/L([0-9]+) false-sentences:([0-9]+|unread))[ \t\r]*$", re.M)
    body = runmod.render_body({"part_line": "Part 1/2 of #5", "fixes_line": "", "gates": [], "usage": ""}, runmod.review_line([r1], True))
    check("the PR body's Review line matches the pattern the loop metrics read", pattern.search(body) is not None)
    check("the record survives the body", runmod.read_record(body).get("part_line") == "Part 1/2 of #5")
    check("severity 'M ' is not H or M by accident", runmod.sev({"severity": "low"}) == "L" and runmod.sev(None) == "")
    lr = runmod.Run.locally_run
    check("a gate the checks run only to print a skip runs here: the forbidden-token scan", lr("python3 bin/forbidden-tokens.py scan", {}) is True)
    check("a gate whose CI line only prints runs here", lr("python3 x.py", {"ci": "echo skipped in CI"}) is True)
    check("a plain gate is the checks' run, not ours", lr("python3 x.py", {}) is False and lr("python3 x.py", {"ci": "python3 y.py"}) is False)
    named = ("## What\nThe driver is `python3 bin/ouro run N`.\n## Tests and verification\nRun `python3 tests/x.Tests.py`, then `pytest -q`, "
             "not `ls -la` or `python3`.\n### Detail\nAgain `pytest -q`, and `dotnet test`.\n## Exit\n`npm run release`\n### Acceptance\n`go test ./...`")
    check("acceptance_commands takes the runner spans of the acceptance, test and verification sections, once each",
          runmod.acceptance_commands(named) == ["python3 tests/x.Tests.py", "pytest -q", "dotnet test", "go test ./..."])
    check("a heading that only holds those letters inside a word is not such a section",
          runmod.acceptance_commands("## Latest changes\n`python3 tools/publish.py`\n## Attestation\n`pwsh x.ps1`\n## Testing\n`pytest`") == ["pytest"])
    check("without_build_lines drops every Ouro-Build line and nothing else", runmod.without_build_lines("a\nOuro-Build: {}\nb\nOuro-Build: {\"x\": 1}") == "a\nb")
    f = TMP / "bytes.txt"
    shmod.write_bytes(f, "é\r\nx\n")
    check("files for --body-file are UTF-8 bytes with LF endings", f.read_bytes() == b"\xc3\xa9\nx\n")
    r = shmod.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write('é'.encode('utf-8'))"])
    check("child output is decoded as UTF-8, whatever the locale's code page", r.out == "é")


def sec_labels():
    t = triagemod
    check("PROMOTE adds agent-ready and clears the state it replaces", t.label_moves("PROMOTE", "S", ["needs-triage", "gates"]) == (["agent-ready"], ["needs-triage"]))
    check("an L never reaches agent-ready", t.label_moves("PROMOTE", "L", ["needs-triage"]) == ([], []))
    check("NEEDS-RULING moves to needs-ruling", t.label_moves("NEEDS-RULING", "M", ["needs-triage"]) == (["needs-ruling"], ["needs-triage"]))
    check("SPLIT moves nothing", t.label_moves("SPLIT", "L", ["needs-triage"]) == ([], []))
    check("an already-present label is not added twice", t.label_moves("PROMOTE", "S", ["agent-ready"]) == ([], []))
    b = t.with_lines("text\n", "M", "full", "none")
    check("with_lines writes the three lines where missing", b.endswith("Size: M\nFlow: full\nDoc impact on close: none\n"))
    check("with_lines leaves a present line alone", t.with_lines("Size: S\n", "M", "", "") == "Size: S\n")
    check("with_lines is case-sensitive about the key", "Size: M" in t.with_lines("size: S\n", "M", "", ""))


def sec_node():
    d = TMP / "nodestub"
    d.mkdir()
    os.environ["OURO_NODE_STUB"] = str(d)
    try:
        (d / "X.json").write_text(json.dumps({"verdict": "CLEAN"}), encoding="utf-8")
        (d / "X.2.json").write_text(json.dumps({"verdict": "FINDINGS"}), encoding="utf-8")
        a = nodemod.run_node("X", "prompt one", "sonnet", [], str(d), 1)
        b = nodemod.run_node("X", "prompt two", "sonnet", [], str(d), 1)
        c = nodemod.run_node("X", "prompt three", "sonnet", [], str(d), 1)
        check("the k-th call takes <name>.<k>.json, else <name>.json", (a["verdict"], b["verdict"], c["verdict"]) == ("CLEAN", "FINDINGS", "CLEAN"))
        check("a stub call records the prompt it got", (d / "X.2.prompt.txt").read_text(encoding="utf-8") == "prompt two")
        try:
            nodemod.run_node("NOPE", "p", "sonnet", [], str(d), 1)
            check("a missing stub file is a driver error", False)
        except shmod.DriverError as e:
            check("a missing stub file is a driver error naming the node", "NOPE" in str(e))
        (d / "W.json").write_text(json.dumps({"writes": {"a/b.txt": "hi"}, "ok": 1}), encoding="utf-8")
        w = nodemod.run_node("W", "p", "sonnet", [], str(d), 1)
        check("the stub layer applies `writes` under cwd and strips it from the answer", (d / "a" / "b.txt").read_text() == "hi" and "writes" not in w)
        (d / "G.1.json").write_text(json.dumps({"_raw": "no json here"}), encoding="utf-8")
        (d / "G.2.json").write_text(json.dumps({"_raw": "text ```json\n{\"v\": 2}\n``` more"}), encoding="utf-8")
        check("an unparseable first answer is retried once, and a fenced block counts", nodemod.run_node("G", "p", "sonnet", [], str(d), 1) == {"v": 2})
        (d / "H.json").write_text(json.dumps({"_raw": "[1, 2]"}), encoding="utf-8")
        try:
            nodemod.run_node("H", "p", "sonnet", [], str(d), 1)
            check("no JSON object twice is a driver error", False)
        except shmod.DriverError as e:
            check("no JSON object twice is a driver error naming the node and the session", "H" in str(e) and "session" in str(e))
        (d / "U.json").write_text(json.dumps({"ok": 1, "_usage": {"input_tokens": 5, "output_tokens": 7}}), encoding="utf-8")
        nodemod.USAGE.clear()
        nodemod.run_node("U", "p", "sonnet", [], str(d), 1)
        check("usage is recorded per call", nodemod.USAGE and nodemod.USAGE[0]["input"] == 5 and nodemod.USAGE[0]["output"] == 7 and "| U |" in nodemod.usage_table())
    finally:
        del os.environ["OURO_NODE_STUB"]
    check("the first JSON object wins over a later one", nodemod.parse_first_json('x {"a": 1} y {"b": 2}') == {"a": 1})
    check("a brace that is not JSON is skipped", nodemod.parse_first_json('{nope} {"a": 1}') == {"a": 1})
    d2 = TMP / "garbagestub"
    d2.mkdir()
    (d2 / "S.json").write_text(json.dumps({"_raw": "no json", "_stderr": "the stub's stderr tail"}), encoding="utf-8")
    os.environ["OURO_NODE_STUB"] = str(d2)
    try:
        nodemod.run_node("S", "p", "sonnet", [], str(d2), 1)
        check("a garbage stub answer is a driver error", False)
    except shmod.DriverError as e:
        check("the error after the retry names the node, the session and the stderr tail", "node S" in str(e) and "session stub" in str(e) and "the stub's stderr tail" in str(e))
    finally:
        del os.environ["OURO_NODE_STUB"]
    argv_file = TMP / "claude-argv.json"
    old_path = os.environ["PATH"]
    os.environ.update({"PATH": str(CLAUDEDIR) + os.pathsep + old_path, "FAKE_CLAUDE_ARGV": str(argv_file)})
    shmod._RESOLVED.pop("claude", None)
    try:
        for mode, want in (("garbage", ("the boom tail", "this is not json")), ("iserror", ("the result text", "S-ERR"))):
            os.environ["FAKE_CLAUDE_MODE"] = mode
            try:
                nodemod.run_node("BUILD", "p", "sonnet", ["Read", "Bash"], str(TMP), 60, ["Bash(gh *)", "Bash(git push *)"])
                check(f"a claude that fails ({mode}) is a driver error", False)
            except shmod.DriverError as e:
                check(f"claude failing ({mode}): the error names the node and carries what claude said", "node BUILD" in str(e) and all(w in str(e) for w in want))
        argv = json.loads(argv_file.read_text(encoding="utf-8"))
        check("the real adapter passes the allowed tools and the denied tools as their own flags",
              argv[argv.index("--allowedTools") + 1] == "Read,Bash" and argv[argv.index("--disallowedTools") + 1] == "Bash(gh *),Bash(git push *)")
        nodemod.USAGE.clear()
        try:
            nodemod.run_node("BUILD", "p", "sonnet", ["Read"], str(TMP), 60)
        except shmod.DriverError:
            pass
        check("without a deny list the flag is not passed", "--disallowedTools" not in json.loads(argv_file.read_text(encoding="utf-8")))
    finally:
        os.environ["PATH"] = old_path
        shmod._RESOLVED.pop("claude", None)


def sec_stub_proof():
    c = Case("stubproof", table={"_viewer": "stub-only-viewer"})
    gh = shutil.which("gh")
    check("PATH resolves gh to the stand-in, not the real one", gh is not None and pathlib.Path(gh).parent.resolve() == STUBDIR.resolve())
    code, out, err = sh(gh, "api", "user", "--jq", ".login", env=c.env())
    check("the stand-in shadows the real gh: its stub-only answer is in the capture", out.strip() == "stub-only-viewer")
    check("the call is logged by the stand-in", "api user --jq .login" in c.sigs())
    code, out, err = sh(gh, "pr", "frobnicate", env=c.env())
    check("an unlisted call exits 1 and names itself", code == 1 and "no answer for: pr frobnicate" in out)


# ---- the failure table and the transitions ---------------------------------------------------
def sec_no_binding():
    c = Case("nobinding")
    (c.main / ".claude" / "ouro.toml").unlink()
    code, out, err = c.ouro("run", "1")
    check("no binding: exit 2", code == 2)
    check("no binding: stderr names the binding", "no binding" in err)
    check("no binding: no tracker call, no comment, no label", c.sigs() == [])


def sec_bad_binding():
    c = Case("badbinding", binding=BINDING.format(policy="stop-at-pr", review="adversarial-review") + "\n[ship2]\nx = 1\n")
    code, out, err = c.ouro("run", "1")
    check("a key the reader refuses: exit 2", code == 2)
    check("stderr names the key", "ship2" in err)
    check("no tracker call", c.sigs() == [])
    c2 = Case("badpolicy", policy="merge-always")
    code, out, err = c2.ouro("run", "1")
    check("a policy the binding does not accept: exit 2, no tracker call", code == 2 and "ship.policy" in err and c2.sigs() == [])
    c3 = Case("greenpolicy", policy="merge-on-green", table={"_issue_1": issue(labels=("needs-triage", "gates", "bug"))})
    code, out, err = c3.ouro("run", "1")
    check("merge-on-green binds: the refusal is the issue's missing agent-ready, not the policy", code == 2 and "agent-ready" in err and "ship.policy" not in err)


def sec_not_ready():
    c = Case("notready", table={"_issue_1": issue(labels=("needs-triage", "gates", "bug"))})
    code, out, err = c.ouro("run", "1")
    check("an issue without agent-ready: exit 2", code == 2 and "agent-ready" in err)
    check("it writes nothing", c.wrote() == [])
    c = happy_case("checkpoint", table={"_issue_1": issue(labels=("agent-ready", "checkpoint", "gates", "bug"))})
    code, out, err = c.ouro("run", "1")
    check("a checkpoint issue is refused before any branch: exit 2, nothing built or written", code == 2 and "checkpoint" in err
          and c.prompts("BUILD") == [] and c.branches() == ["master"] and c.wrote() == [])
    c = happy_case("needsruling", table={"_issue_1": issue(labels=("agent-ready", "needs-ruling", "gates", "bug"))})
    code, out, err = c.ouro("run", "1")
    check("an issue waiting on a ruling is refused before any branch: exit 2, nothing built or written", code == 2 and "needs-ruling" in err
          and c.prompts("BUILD") == [] and c.branches() == ["master"] and c.wrote() == [])


def sec_shape():
    c = Case("shape", table={"_issue_1": issue(body="## What\nno anchors, no size\n")})
    code, out, err = c.ouro("run", "1")
    check("a shape finding: exit 2", code == 2)
    check("the gate's own demotion ran (agent-ready -> needs-triage)", any(s.startswith("issue edit 1") and "--add-label needs-triage" in s for s in c.sigs()))
    check("the gate's finding comment was posted", any(s.startswith("issue comment 1") for s in c.sigs()))
    check("no branch was made", c.branches() == ["master"] and not (c.main / ".claude" / "worktrees").exists())


def sec_dead_anchor():
    body = ISSUE_BODY.replace("def compute_total(items):", "def a_symbol_that_is_gone(items):")
    c = Case("deadanchor", table={"_issue_1": issue(body=body, labels=("agent-ready", "trivial", "gates", "bug"))})
    code, out, err = c.ouro("run", "1")
    check("a dead anchor: exit 2", code == 2)
    comments = (c.issue_state() or {"comments": []})["comments"]
    stops = [x["body"] for x in comments if x["body"].startswith("**Stop:**")]
    check("the stop comment is line 1 alone, a blank line, the finding from line 3", len(stops) == 1 and stop_comment_ok(stops[0], "dead anchor"))
    check("the finding names the fragment", stops and "a_symbol_that_is_gone" in stops[0])
    labs = labels_of(c)
    check("agent-ready -> needs-ruling, trivial removed", "needs-ruling" in labs and "agent-ready" not in labs and "trivial" not in labs)
    check("no branch, no node call", c.branches() == ["master"] and c.prompts("BUILD") == [])
    d = Case("deadanchor-dry", table={"_issue_1": issue(body=body)})
    code, out, err = d.ouro("run", "1", "--dry-run")
    check("--dry-run on a dead anchor still exits 2 and prints the stop it would post", code == 2 and "would comment on #1" in out and "would swap agent-ready" in out)
    check("--dry-run on a dead anchor writes nothing to the tracker", d.wrote() == [] and d.issue_state() is None)


def sec_dry_run():
    c = happy_case("dryrun")
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("--dry-run exits 0", code == 0)
    check("it names each transition", all(f"[{k}]" in out for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)))
    check("it prints the command a transition would run", "Push-Branch.ps1" in out and "git worktree add" in out)
    check("it names the reader the Size and ship.review give", "size S: first reader node" in out)
    check("it writes nothing to the tracker", c.wrote() == [])
    check("it makes no branch and no worktree", c.branches() == ["master"] and not (c.main / ".claude" / "worktrees").exists())
    check("it makes no node call", c.prompts("BUILD") == [] and c.prompts("REVIEW") == [])
    check("the shape check ran without -Demote", not any("issue edit" in s for s in c.sigs()))


def sec_happy():
    c = happy_case("happy")
    code, out, err = c.ouro("run", "1")
    check("a stop-at-pr run ends at the PR: exit 0", code == 0)
    check("the BUILD answer's subject is the commit", git(c.origin, "log", "-1", "--format=%s", "refs/heads/gates/1-fix-the-total") == "fix(app): sum the items")
    branch_head = git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
    check("the branch is `<area>/<N>-<slug>` from the default branch, pushed", git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "1")
    check("the worktree is under .claude/worktrees/<N>-<slug>", (c.main / ".claude" / "worktrees" / "1-fix-the-total" / "src" / "app.py").is_file())
    check("the BUILD stub's file is in the commit", "+ 0" in git(c.origin, "show", branch_head, "--", "src/app.py"))
    last_line = git(c.origin, "log", "-1", "--format=%B", branch_head).splitlines()[-1]
    check("the commit message ends with one Ouro-Build line holding the answer's door, blast radius, residue and replies",
          last_line.startswith("Ouro-Build: ") and json.loads(last_line[len("Ouro-Build: "):]) ==
          {"door": BUILD["door"], "blast_radius": BUILD["blast_radius"], "residue": [], "replies": []})
    check("the local gate (checks print a skip) ran", "gate `python3 gates/check.py`: exit 0" in out)
    pr = c.pr()
    check("a draft pull request was opened, then marked ready", pr is not None and pr["isDraft"] is False and any(s.startswith("pr create") and "--draft" in s for s in c.sigs()))
    check("the PR body has Fixes #1 and the Review line", "Fixes #1" in pr["body"] and re.search(r"(?m)^Review: rounds:0 findings:H0/M0/L0 false-sentences:0$", pr["body"]))
    check("the PR body carries the gate line and the usage table", "exit 0" in pr["body"] and "<details>" in pr["body"])
    cm = [x["body"] for x in c.pr_comments()]
    check("the review report is a PR comment whose line 1 is the marker at the full head sha", any(b.split("\n")[0] == f"**Review** round 1: CLEAN @ {branch_head}" for b in cm))
    ready = [b for b in cm if b.startswith("**Ready for merge**")]
    check("Ready for merge is posted once with the facts", len(ready) == 1 and branch_head in ready[0] and "Checks: 1 green" in ready[0])
    check("nothing was merged", not any(s.startswith("pr merge") for s in c.sigs()))
    review_brief = open(c.nodes / "REVIEW.1.prompt.txt", encoding="utf-8").read()
    check("the REVIEW brief holds the diff and the quoted contract", "compute_total" in review_brief and "## 7." in review_brief)
    check("the REVIEW brief holds the reviewer prompt, read where it is", "# Reviewer prompt template" in review_brief)
    call = c.node_call("REVIEW")
    check("under standard the REVIEW node keeps read tools in the build worktree and has no copy", call["tools"] == ["Read", "Grep", "Glob"] and call["denied"] is None
          and pathlib.Path(call["cwd"]).resolve() == (c.main / ".claude" / "worktrees" / "1-fix-the-total").resolve())
    check("the BUILD brief holds the issue body and the anchor's current line", "src/app.py:1: def compute_total(items):" in open(c.nodes / "BUILD.1.prompt.txt", encoding="utf-8").read())
    # resume 1: still waiting on the owner
    c.clear_prompts()
    sigs_before = len(c.sigs())
    code, out, err = c.ouro("run", "1")
    again = c.sigs()[sigs_before:]
    check("a second invocation while waiting: exit 0", code == 0)
    check("it repeats no build, no review and no PR", c.prompts("BUILD") == [] and c.prompts("REVIEW") == [] and not any(s.startswith(("pr create", "pr comment")) for s in again))
    check("it does not post a second Ready for merge", len([b for b in c.pr_comments() if b["body"].startswith("**Ready for merge**")]) == 1)
    # resume 2: the owner merges on GitHub
    sh(sys.executable, STUB, "pr", "merge", "7", "--squash", "--subject", "fix(app): sum the items (#7)", "--delete-branch", env=c.env())
    sigs_before = len(c.sigs())
    code, out, err = c.ouro("run", "1")
    again = c.sigs()[sigs_before:]
    check("after the owner's merge the next invocation finishes: exit 0", code == 0 and "done: #1 landed" in out)
    check("it repeats no build or review", c.prompts("BUILD") == [] and c.prompts("REVIEW") == [])
    check("the branch and the worktree are removed", "gates/1-fix-the-total" not in c.branches() and not (c.main / ".claude" / "worktrees" / "1-fix-the-total").exists())
    check("the local branch is gone", git(c.main, "branch", "--list", "gates/1-fix-the-total") == "")
    check("the queue's anchors were refreshed", any(s.startswith("issue list") for s in again))
    code, out, err = c.ouro("run", "1")
    check("a third invocation does nothing new and exits 0", code == 0 and not any(s.startswith(("pr create", "pr comment", "issue comment")) for s in c.sigs()[sigs_before:]))


def sec_trivial_merge():
    c = happy_case("trivialmerge", policy="trivial-merge", table={"_issue_1": issue(labels=("agent-ready", "trivial", "gates", "bug"))})
    code, out, err = c.ouro("run", "1")
    check("trivial-merge with the trivial label merges: exit 0", code == 0 and "done: #1 landed" in out)
    merge = [s for s in c.sigs() if s.startswith("pr merge")]
    check("the merge is Merge-PullRequest's pinned squash with the subject `<subject> (#7)`", len(merge) == 1 and "--squash" in merge[0] and "--subject fix(app): sum the items (#7)" in merge[0])
    landed = [b["body"] for b in c.pr_comments() if b["body"].startswith("**Landed unattended**")]
    check("the Landed unattended comment names the merge sha, the checks, the Review line and the class",
          len(landed) == 1 and re.search(r"by ouro run at [0-9a-f]{40}: checks 1 green, Review: rounds:0 findings:H0/M0/L0 false-sentences:0, class standard", landed[0]))
    check("no Ready for merge was posted", not any(b["body"].startswith("**Ready for merge**") for b in c.pr_comments()))
    check("the usage table prints once", out.count("| node | session |") == 1)
    merge_body = next((x["body"] for x in c.calls() if x["sig"].startswith("pr merge")), None)
    check("the merge body and the pull request's body hold no Ouro-Build line", bool(merge_body) and "Ouro-Build" not in merge_body and "Ouro-Build" not in c.pr()["body"])
    check("the branch and worktree are gone", "gates/1-fix-the-total" not in c.branches() and not (c.main / ".claude" / "worktrees" / "1-fix-the-total").exists())
    c2 = happy_case("trivialnolabel", policy="trivial-merge")
    code, out, err = c2.ouro("run", "1")
    check("trivial-merge without the label stops at Ready for merge", code == 0 and not any(s.startswith("pr merge") for s in c2.sigs())
          and any(b["body"].startswith("**Ready for merge**") for b in c2.pr_comments()))


def sec_findings_fix():
    c = Case("fixround")
    c.node("BUILD", BUILD, 1)
    c.node("BUILD", {**BUILD, "subject": "fix(app): handle the last item", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) + 1\n"}}, 2)
    c.node("REVIEW", {"verdict": "FINDINGS", "findings": [finding("H"), finding("L", "a nit")], "false_record_sentences": 1, "files_read": ["x"]}, 1)
    c.node("REVIEW", CLEAN, 2)
    code, out, err = c.ouro("run", "1")
    check("an H finding then a fix then clean: exit 0", code == 0)
    check("the fix round's BUILD brief carries the finding", "the total drops the last item" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read())
    check("the fix is a second commit, pushed", git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "2")
    head = git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
    firsts = [b["body"].split("\n")[0] for b in c.pr_comments() if b["body"].startswith("**Review**")]
    check("round 1 FINDINGS at the first commit, round 2 CLEAN at the head",
          len(firsts) == 2 and firsts[0].startswith("**Review** round 1: FINDINGS @ ") and firsts[1] == f"**Review** round 2: CLEAN @ {head}")
    check("the PR body's Review line counts the fix round, the findings and the false sentence",
          re.search(r"(?m)^Review: rounds:1 findings:H1/M0/L1 false-sentences:1$", c.pr()["body"]) is not None)
    check("the second review's brief carries the first round's report", "**Review** round 1: FINDINGS" in open(c.nodes / "REVIEW.2.prompt.txt", encoding="utf-8").read())
    check("an L-only round is clean: no fix round for it", len(c.prompts("BUILD")) == 2)

    class FakeEnv:
        def lookup(self, key, default=None):
            return {"ship.policy": "stop-at-pr", "ship.review": "adversarial-review"}.get(key, default)

        def gh_json(self, *args):
            return [] if "checks" in args else {"reviewDecision": "", "headRefOid": "a" * 40, "comments": [], "body": ""}

        def comment_pr(self, n, text):
            pass

    r = runmod.Run.__new__(runmod.Run)
    r.env, r.primary, r.flow, r.labels = FakeEnv(), "node", "standard", ["trivial"]
    unclean = [{"round": 1, "verdict": "FINDINGS", "sha": "a" * 40, "findings": [finding("H")], "false": 0}]
    try:
        r.decide({"number": 7, "title": "t"}, "a" * 40, unclean)
        refused = False
    except shmod.DriverError as ex:
        refused = "is not clean" in str(ex) and "does not match" not in str(ex)
    check("the merge decision refuses a head whose last verdict needs a fix, whatever the loop did", refused)
    clean_at_b = [{"round": 1, "verdict": "CLEAN", "sha": "b" * 40, "findings": [], "false": 0}]
    try:
        r.decide({"number": 7, "title": "t"}, "b" * 40, clean_at_b)
        moved = False
    except shmod.DriverError as ex:
        moved = "does not match the reviewed head" in str(ex) and "not clean" not in str(ex)
    check("the merge decision refuses a pull request whose head is not the reviewed one, with its own message", moved)


def sec_review_cap():
    c = Case("reviewcap", table={"_issue_1": issue(labels=("agent-ready", "trivial", "gates", "bug"))})
    for k in range(1, 5):
        c.node("BUILD", {**BUILD, "subject": f"fix(app): round {k}", "writes": {"src/app.py": f"def compute_total(items):\n    return {k}\n"}}, k)
        c.node("REVIEW", {"verdict": "FINDINGS", "findings": [finding("M", f"defect {k}")], "false_record_sentences": 0, "files_read": []}, k)
    code, out, err = c.ouro("run", "1")
    check("a fourth fix round is a stop: exit 3", code == 3)
    check("three fix rounds were built, a fourth was not", len(c.prompts("BUILD")) == 4 and "BUILD.5.prompt.txt" not in c.prompts("BUILD"))
    stops = [x["body"] for x in (c.issue_state() or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]
    check("the stop is `**Stop:** review cap`, line 1 alone, a blank line 2, content from line 3", len(stops) == 1 and stop_comment_ok(stops[0], "review cap"))
    labs = labels_of(c)
    check("agent-ready -> needs-ruling, trivial removed", "needs-ruling" in labs and "agent-ready" not in labs and "trivial" not in labs)
    check("nothing was merged or marked ready", not any(s.startswith(("pr merge", "pr ready")) for s in c.sigs()))


def sec_build_stop():
    c = Case("buildstop")
    c.node("BUILD", {"stop": {"reason": "open decision", "question": "Which of the two formats?"}})
    code, out, err = c.ouro("run", "1")
    check("a BUILD node that cannot meet the spec: exit 3", code == 3)
    stops = [x["body"] for x in (c.issue_state() or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]
    check("`**Stop:** open decision` with the node's question from line 3", len(stops) == 1 and stop_comment_ok(stops[0], "open decision") and "Which of the two formats?" in stops[0].split("\n")[2])
    check("agent-ready -> needs-ruling", "needs-ruling" in labels_of(c) and "agent-ready" not in labels_of(c))
    check("nothing was pushed or opened", c.branches() == ["master"] and not any(s.startswith("pr create") for s in c.sigs()))
    wrote = c.wrote()
    c.node("BUILD", BUILD)
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    check("the next invocation reads the labels the stop left: exit 3 with the reason, no build, no pull request", code == 3 and "not agent-ready" in out
          and c.prompts("BUILD") == [] and c.pr() is None)
    check("it writes nothing new: the stop that put it there already wrote the comment and the labels", c.wrote() == wrote and len(stops_of(c)) == 1)
    c = happy_case("checkpointlater")
    code, out, err = c.ouro("run", "1")
    tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    tbl["_issue_1"] = issue(labels=("agent-ready", "checkpoint", "gates", "bug"))
    c.set_table(tbl)
    c.clear_prompts()
    wrote = c.wrote()
    code, out, err = c.ouro("run", "1")
    check("a checkpoint label added once the branch exists: exit 3, nothing built or reviewed, nothing written",
          code == 3 and "checkpoint" in out and c.prompts("BUILD") == [] and c.prompts("REVIEW") == [] and c.wrote() == wrote)
    c = happy_case("rulinglater")
    c.ouro("run", "1")
    tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    tbl["_issue_1"] = issue(labels=("agent-ready", "gates", "bug", "needs-ruling"))
    c.set_table(tbl)
    code, out, err = c.ouro("run", "1")
    check("an issue that went to needs-ruling after the branch was made: exit 3", code == 3 and "needs-ruling" in out)
    sh(sys.executable, STUB, "pr", "merge", "7", "--squash", "--subject", "fix(app): sum the items (#7)", "--delete-branch", env=c.env())
    code, out, err = c.ouro("run", "1")
    check("the owner's merge still finishes the run whatever the labels say", code == 0 and "done: #1 landed" in out)


def sec_gate_red():
    c = Case("gatered")
    c.node("BUILD", {**BUILD, "writes": {**BUILD["writes"], "gates/exit.txt": "1"}}, 1)
    c.node("BUILD", {**BUILD, "subject": "fix(app): make the gate pass", "writes": {"gates/exit.txt": "0"}}, 2)
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    check("a red gate is a fix round, then green: exit 0", code == 0 and "gate `python3 gates/check.py`: exit 1" in out and "gate `python3 gates/check.py`: exit 0" in out)
    check("the fix round's brief carries the gate's command and exit", "gate `python3 gates/check.py` exited 1" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read())
    check("the red commit was never pushed alone: the branch has both commits at the first push", git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "2")


def sec_gate_uncovered():
    b = BINDING.format(policy="stop-at-pr", review="adversarial-review").replace('areas = ["*"]', 'areas = ["elsewhere"]')
    c = Case("uncovered", binding=b)
    code, out, err = c.ouro("run", "1")
    check("an area no gate covers: exit 3", code == 3)
    stops = [x["body"] for x in (c.issue_state() or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]
    check("`**Stop:** gate uncovered` naming the area", len(stops) == 1 and stop_comment_ok(stops[0], "gate uncovered") and "gates" in stops[0])
    check("agent-ready -> needs-ruling and no branch", "needs-ruling" in labels_of(c) and c.branches() == ["master"])
    b2 = BINDING.format(policy="stop-at-pr", review="adversarial-review").replace("python3 gates/check.py", "no-such-tool-anywhere gates/check.py")
    c2 = happy_case("unrunnable", binding=b2)
    code, out, err = c2.ouro("run", "1")
    stops = [x["body"] for x in (c2.issue_state() or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]
    check("a gate the driver cannot run: `gate uncovered`, exit 3", code == 3 and len(stops) == 1 and stop_comment_ok(stops[0], "gate uncovered") and "no-such-tool-anywhere" in stops[0])


def sec_full_paths():
    b = BINDING.format(policy="stop-at-pr", review="adversarial-review") + (
        '[[gate]]\nareas = ["*"]\nrun = "python3 gates/src_gate.py"\npaths = ["src/"]\n'
        '[[gate]]\nareas = ["*"]\nrun = "python3 gates/doc_gate.py"\npaths = ["docs/"]\n')
    c = Case("fullpaths", binding=b, files={"gates/src_gate.py": "print('src gate ran')\n", "gates/doc_gate.py": "print('doc gate ran')\n"},
             table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: full"))})
    c.node("BUILD", BUILD)
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    check("under full a selected gate whose paths the diff touches runs on the commit", code == 0 and "gate `python3 gates/src_gate.py`: exit 0" in out)
    check("a selected gate whose paths the diff misses does not run", "doc_gate" not in out.split("[6]")[0])
    check("the record says which gate was skipped for its paths", "Gate skipped, the diff touches none of its paths: `python3 gates/doc_gate.py`" in c.pr()["body"])
    check("the gate the checks run only to print a skip also ran", "gate `python3 gates/check.py`: exit 0" in out)
    check("under full the REVIEW node is the probe mode", "Mode: probe" in open(c.nodes / "REVIEW.1.prompt.txt", encoding="utf-8").read())
    call = c.node_call("REVIEW")
    check("under full the REVIEW node runs in its own detached worktree copy, not the build worktree", "review-1" in call["cwd"] and call["cwd_exists"] is True
          and pathlib.Path(call["cwd"]).resolve() != (c.main / ".claude" / "worktrees" / "1-fix-the-total").resolve())
    check("under full its tools are Read, Grep, Glob and Bash, with push, commit and gh denied", call["tools"] == ["Read", "Grep", "Glob", "Bash"] and call["denied"] == ["Bash(git push *)", "Bash(git commit *)", "Bash(gh *)"])
    check("the copy is removed afterwards, its registration with it", "review-1" not in git(c.main, "worktree", "list", "--porcelain") and not pathlib.Path(call["cwd"]).exists())
    c2 = happy_case("standardpaths", binding=b, files={"gates/src_gate.py": "print('src gate ran')\n", "gates/doc_gate.py": "print('doc gate ran')\n"})
    code, out, err = c2.ouro("run", "1")
    check("under standard the pull request's checks run a gate whose paths are touched: not run here", code == 0 and "src_gate" not in out.split("[6]")[0])
    brief = open(c.nodes / "BUILD.1.prompt.txt", encoding="utf-8").read()
    check("under full the BUILD brief lists each gate with paths, with its paths and when to run it",
          "- `python3 gates/src_gate.py` (run it when the change touches: src/)" in brief and "- `python3 gates/doc_gate.py` (run it when the change touches: docs/)" in brief)
    check("under full the BUILD node may run those gates", {"Bash(python3 gates/src_gate.py)", "Bash(python3 gates/doc_gate.py)"} <= set(c.node_call("BUILD")["tools"]))
    check("under standard the BUILD brief leaves a gate the checks run out", "src_gate" not in open(c2.nodes / "BUILD.1.prompt.txt", encoding="utf-8").read())
    c3 = Case("branchbinding", binding=b, files={"gates/src_gate.py": "print('src gate ran')\n", "gates/doc_gate.py": "print('doc gate ran')\n"},
              table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: full"))})
    c3.node("BUILD", {**BUILD, "writes": {**BUILD["writes"], ".claude/ouro.toml": b.replace('run = "python3 gates/src_gate.py"', 'run = "python3 gates/doc_gate.py"')}})
    c3.node("REVIEW", CLEAN)
    code, out, err = c3.ouro("run", "1")
    check("a gate the branch's binding drops does not run, and the one it rewrites in does", code == 0
          and "src_gate" not in out.split("[6]")[0] and "gate `python3 gates/doc_gate.py`: exit 0" in out.split("[6]")[0])


def sec_fix_round_scope():
    b = BINDING.format(policy="stop-at-pr", review="adversarial-review") + (
        '[[gate]]\nareas = ["*"]\nrun = "python3 gates/src_gate.py"\npaths = ["src/"]\n'
        '[[gate]]\nareas = ["*"]\nrun = "python3 gates/doc_gate.py"\npaths = ["docs/"]\n')
    c = Case("fixgates", binding=b, files={"gates/src_gate.py": "print('src gate ran')\n", "gates/doc_gate.py": "print('doc gate ran')\n"},
             table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: full"))})
    c.node("BUILD", {**BUILD, "writes": {**BUILD["writes"], "docs/note.md": "note, first\n"}}, 1)
    c.node("BUILD", {**BUILD, "subject": "docs: reword the note", "writes": {"docs/note.md": "note, fixed\n"}}, 2)
    c.node("REVIEW", {"verdict": "FINDINGS", "findings": [finding("M", "the note is wrong")], "false_record_sentences": 0, "files_read": ["x"]}, 1)
    c.node("REVIEW", CLEAN, 2)
    code, out, err = c.ouro("run", "1")
    check("a full fix round that touches only docs/: exit 0", code == 0)
    check("the gate whose paths the fix misses ran once, before the fix", out.count("gate `python3 gates/src_gate.py`: exit 0") == 1)
    check("the gate whose paths the fix touches ran again on it", out.count("gate `python3 gates/doc_gate.py`: exit 0") == 2)
    check("the gate the checks run only to print a skip ran on both heads", out.count("gate `python3 gates/check.py`: exit 0") == 2)
    body = c.pr()["body"]
    check("the record keeps the standing gate's green line and does not call it skipped",
          "`python3 gates/src_gate.py` exit 0" in body and "touches none of its paths: `python3 gates/src_gate.py`" not in body)

    c2 = happy_case("outsidefix", policy="trivial-merge", table={"_issue_1": issue(labels=("agent-ready", "trivial", "gates", "bug"))})
    c2.node("BUILD", BUILD, 1)
    c2.node("BUILD", {**BUILD, "subject": "fix(app): handle the last item", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) + 1\n"}}, 2)
    c2.node("REVIEW", {"verdict": "FINDINGS", "findings": [finding("H")], "false_record_sentences": 0, "files_read": ["x"]}, 1)
    c2.node("REVIEW", {"verdict": "FINDINGS", "findings": [{**finding("M", "an older helper drops a row"), "scope": "OUTSIDE"}],
                       "false_record_sentences": 0, "files_read": ["x"]}, 2)
    c2.node("FOLLOWUP", {"issues": []})
    code, out, err = c2.ouro("run", "1")
    check("a verification whose only H/M finding is OUTSIDE starts no fix round", len(c2.prompts("BUILD")) == 2)
    check("and the change merges as clean", code == 0 and "done: #1 landed" in out)
    check("the Review line counts one fix round", re.search(r"(?m)^Review: rounds:1 findings:H1/M1/L0 ", c2.pr()["body"]) is not None)
    check("the OUTSIDE finding reaches the FOLLOWUP node", c2.prompts("FOLLOWUP") != [] and "an older helper drops a row" in open(c2.nodes / c2.prompts("FOLLOWUP")[0], encoding="utf-8").read())
    check("needs_fix: on a verification an IN-FIX or unscoped M needs a fix, an OUTSIDE one does not",
          runmod.needs_fix({"round": 2, "findings": [{**finding("M"), "scope": "IN-FIX"}]}) and runmod.needs_fix({"round": 2, "findings": [finding("M")]})
          and not runmod.needs_fix({"round": 2, "findings": [{**finding("H"), "scope": "outside "}]}))
    check("needs_fix: a first review's OUTSIDE H still needs a fix", runmod.needs_fix({"round": 1, "findings": [{**finding("H"), "scope": "OUTSIDE"}]}))
    brief = open(c2.nodes / "REVIEW.2.prompt.txt", encoding="utf-8").read()
    check("the verification brief names the fix's range and carries its diff",
          re.search(r"The fix is `[0-9a-f]{40}\.\.[0-9a-f]{40}`", brief) is not None and "+    return sum(items) + 1" in brief
          and "still stands is IN-FIX" in brief)

    class Out:
        def __init__(self, code, out):
            self.code, self.out = code, out

    rec1 = [{"round": 1, "sha": "a" * 40, "body": "**Review** round 1: FINDINGS", "findings": []}]
    for name, res in (("an unreadable range", Out(128, "")), ("a diff over the cut", Out(0, "+x\n" * 30000))):
        r = runmod.Run.__new__(runmod.Run)
        r.wt_git = lambda *a, check=True, res=res: res
        block = r.verification(rec1, "b" * 40)
        check(f"{name}: the verification block asks for no scope and shows no partial diff",
              "leave `scope` empty on every finding" in block and "```diff" not in block and "round 1: FINDINGS" in block)

    c3 = Case("outsidelater", policy="trivial-merge", table={"_issue_1": issue(labels=("agent-ready", "trivial", "gates", "bug"))})
    c3.node("BUILD", BUILD, 1)
    c3.node("BUILD", {**BUILD, "subject": "fix(app): handle the last item", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) + 1\n"}}, 2)
    c3.node("BUILD", {**BUILD, "subject": "fix(app): handle the empty list", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items or []) + 1\n"}}, 3)
    c3.node("REVIEW", {"verdict": "FINDINGS", "findings": [finding("H")], "false_record_sentences": 0, "files_read": ["x"]}, 1)
    c3.node("REVIEW", {"verdict": "FINDINGS", "findings": [{**finding("M", "the empty list"), "scope": "IN-FIX"},
                                                           {**finding("M", "an older helper drops a row"), "scope": "OUTSIDE"}],
                       "false_record_sentences": 0, "files_read": ["x"]}, 2)
    c3.node("REVIEW", CLEAN, 3)
    c3.node("FOLLOWUP", {"issues": []})
    code, out, err = c3.ouro("run", "1")
    check("an OUTSIDE finding beside an IN-FIX one reaches the FOLLOWUP node after a later clean round", code == 0 and c3.prompts("FOLLOWUP") != []
          and "an older helper drops a row" in open(c3.nodes / c3.prompts("FOLLOWUP")[0], encoding="utf-8").read())

    c4 = Case("fixaddsgate", binding=b, files={"gates/src_gate.py": "print('src gate ran')\n", "gates/doc_gate.py": "print('doc gate ran')\n"},
              table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: full"))})
    c4.node("BUILD", BUILD, 1)
    c4.node("BUILD", {**BUILD, "subject": "gates: add a gate", "writes": {
        ".claude/ouro.toml": b + '[[gate]]\nareas = ["*"]\nrun = "python3 gates/new_gate.py"\npaths = ["src/"]\n',
        "gates/new_gate.py": "print('new gate ran')\n"}}, 2)
    c4.node("REVIEW", {"verdict": "FINDINGS", "findings": [finding("M", "no gate covers it")], "false_record_sentences": 0, "files_read": ["x"]}, 1)
    c4.node("REVIEW", CLEAN, 2)
    code, out, err = c4.ouro("run", "1")
    check("a gate a full fix adds to the binding runs although the fix misses its paths", code == 0 and "gate `python3 gates/new_gate.py`: exit 0" in out)


def sec_brief():
    body = ISSUE_BODY.replace("Fix the total.", "Fix the total; `python3 tools/prose.py` is only prose.\n\n## Acceptance\n`python3 tests/x.Tests.py` passes; `ls -la` is only prose.")
    c = happy_case("briefcmds", table={"_issue_1": issue(body=body)})
    code, out, err = c.ouro("run", "1")
    brief = open(c.nodes / "BUILD.1.prompt.txt", encoding="utf-8").read()
    tools = c.node_call("BUILD")["tools"]
    check("the issue's acceptance command is in the BUILD brief and in the node's tools", "- `python3 tests/x.Tests.py` (acceptance)" in brief and "Bash(python3 tests/x.Tests.py)" in tools)
    check("a backticked span that is not a runner command is neither", "ls -la" not in brief.split("## Commands you may run")[1].split("## Findings")[0] and "Bash(ls -la)" not in tools)
    check("a runner span outside the acceptance section is neither", "tools/prose.py" not in brief.split("## Commands you may run")[1] and "Bash(python3 tools/prose.py)" not in tools)
    check("the gate the checks run only to print a skip is listed and granted", "- `python3 gates/check.py`" in brief and "Bash(python3 gates/check.py)" in tools)
    git_grants = [f"Bash(git {g} *)" for g in ("rm", "mv", "status", "diff", "log", "ls-files")]
    check("the BUILD node holds the six git grants and no commit, push, gh or bare Bash",
          all(g in tools for g in git_grants) and not {"Bash(git commit *)", "Bash(git push *)", "Bash(gh *)", "Bash"} & set(tools))
    check("the brief no longer promises commands named below over an empty list", "named below" not in brief)
    check("the brief says a listed command runs exactly as written, with no cd, redirection, pipe or ;", "exactly as written" in brief and "a pipe or a `;`" in brief)
    plain = happy_case("briefnone", binding=BINDING.format(policy="stop-at-pr", review="adversarial-review").replace('ci = "echo the checks print a skip"', 'ci = "python3 gates/check.py"'))
    code, out, err = plain.ouro("run", "1")
    check("with no acceptance command and no gate the checks skip, the list says the pull request's checks are the run",
          "(none: the pull request's checks are the run)" in open(plain.nodes / "BUILD.1.prompt.txt", encoding="utf-8").read() and plain.node_call("BUILD")["tools"] == ["Edit", "Write", "Read", "Grep", "Glob"] + git_grants)


def sec_checks_running():
    running = [{"databaseId": 11, "workflowDatabaseId": 1, "workflowName": "ci", "event": "pull_request", "createdAt": "2026-01-01T00:00:00Z", "status": "in_progress", "conclusion": ""}]
    c = happy_case("running", table={"_issue_1": issue(), "run list*": {"Exit": 0, "Out": running}})
    code, out, err = c.ouro("run", "1")
    check("checks still running at the limit: exit 0 and the message says so", code == 0 and "still running" in out)
    check("no Ready for merge, no merge, no label change", not any(b["body"].startswith("**Ready for merge**") for b in c.pr_comments()) and not any(s.startswith("pr merge") for s in c.sigs()))
    check("no stop comment", (c.issue_state() or {"comments": []})["comments"] == [] or all(not x["body"].startswith("**Stop:**") for x in c.issue_state()["comments"]))


def sec_check_flake():
    failed = {"databaseId": 11, "workflowDatabaseId": 1, "workflowName": "ci", "event": "pull_request", "createdAt": "2026-01-01T00:00:00Z", "status": "completed", "conclusion": "failure"}
    ok = {**failed, "conclusion": "success"}
    c = happy_case("flake", table={"_issue_1": issue(), "run list*": {"Seq": [{"Exit": 0, "Out": [failed]}, {"Exit": 0, "Out": [failed]}, {"Exit": 0, "Out": [ok]}]},
                                   "run view 11 --json attempt": {"Exit": 0, "Out": {"attempt": 1}}, "run rerun 11 --failed": {"Exit": 0, "Out": ""}})
    code, out, err = c.ouro("run", "1")
    check("a failure whose re-run succeeds is a flake: retried once, then exit 0", code == 0 and any(s.startswith("run rerun 11") for s in c.sigs()) and "a flake" in out)
    check("a flake is no fix round", c.prompts("BUILD") == ["BUILD.1.prompt.txt"])
    failed2 = {**failed}
    c2 = happy_case("realfail", table={"_issue_1": issue(), "run list*": {"Exit": 0, "Out": [failed2]}, "run view 11 --json attempt": {"Exit": 0, "Out": {"attempt": 2}},
                                       "run view 11 --log-failed": {"Exit": 0, "Out": "the real failure line"}})
    c2.node("BUILD", {**BUILD, "subject": "fix(app): the check", "writes": {"src/app.py": "def compute_total(items):\n    return 3\n"}}, 2)
    c2.node("BUILD", {**BUILD, "subject": "fix(app): the check again", "writes": {"src/app.py": "def compute_total(items):\n    return 4\n"}}, 3)
    c2.node("BUILD", {**BUILD, "subject": "fix(app): the check once more", "writes": {"src/app.py": "def compute_total(items):\n    return 5\n"}}, 4)
    code, out, err = c2.ouro("run", "1")
    check("a failure that is not a flake is a fix round with the log as the finding", "the real failure line" in open(c2.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read())
    check("it counts toward the cap: three rounds then review cap, exit 3", code == 3 and any(x["body"].startswith("**Stop:** review cap") for x in c2.issue_state()["comments"]))


def sec_merge_refused():
    c = happy_case("mergerefused", policy="trivial-merge", table={"_issue_1": issue(labels=("agent-ready", "trivial", "gates", "bug")),
                                                                  "pr merge 7*": {"Exit": 1, "Out": "GraphQL: the platform refused this merge"}})
    code, out, err = c.ouro("run", "1")
    check("a merge the platform refuses: exit 1", code == 1)
    check("stderr quotes the refusal", "the platform refused this merge" in err)
    check("no Landed unattended comment, no stop comment, no label change",
          not any(b["body"].startswith("**Landed") for b in c.pr_comments()) and (c.issue_state() is None or all(not x["body"].startswith("**Stop:**") for x in c.issue_state()["comments"])))


def sec_node_garbage():
    c = Case("garbage")
    c.node("BUILD", {"_raw": "I could not produce JSON"}, 1)
    c.node("BUILD", {"_raw": "still none"}, 2)
    code, out, err = c.ouro("run", "1")
    check("a node with no parseable JSON twice: exit 1", code == 1)
    check("stderr names the node and the session", "node BUILD" in err and "session" in err)
    check("it was retried once, not more", len(c.prompts("BUILD")) == 2)
    check("nothing was written to the tracker", c.wrote() == [])


def sec_express():
    c = Case("express", table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: express"))})
    c.node("BUILD", {**BUILD, "writes": {"docs/note.md": "note, edited\n"}})
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    check("an express run reviews the local range before any push, then opens the draft: exit 0", code == 0 and "REVIEW node round 1" in out)
    sigs = c.sigs()
    check("the PR is opened after the review ran (one push at convergence)", out.index("REVIEW node round 1") < out.index("draft pull request"))
    check("the held review record is posted on the PR once it exists", any(b["body"].startswith("**Review** round 1: CLEAN @ ") for b in c.pr_comments()))
    check("the express review brief is the light mode", "Mode: light" in open(c.nodes / "REVIEW.1.prompt.txt", encoding="utf-8").read())


def sec_flow_tighten():
    c = Case("tighten", table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: express"))})
    c.node("BUILD", BUILD)
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    check("Flow only tightens: a script diff under an express line becomes standard, recorded", "Flow tightened from express to standard" in out and "Flow tightened from express to standard" in c.pr()["body"])
    code, out, err = c.ouro("run", "1")
    check("a resumed run re-derives the tightening from the diff: it keeps standard", code == 0 and "Flow tightened from express to standard" in out)
    c = Case("tightenfull", table={"_issue_1": issue(body=ISSUE_BODY)})
    c.node("BUILD", {**BUILD, "writes": {"hooks/guard.py": "print('x')\n"}})
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    ready = [x["body"] for x in c.pr_comments() if x["body"].startswith("**Ready for merge**")]
    check("a standard issue whose diff reaches a hook is full, and the record carries that class", code == 0 and bool(ready) and "Class: full" in ready[0]
          and runmod.read_record(c.pr()["body"]).get("flow") == "full")
    sh(sys.executable, STUB, "pr", "merge", "7", "--squash", "--subject", "fix (#7)", "--delete-branch", env=c.env())
    merged = git(c.origin, "rev-parse", "refs/heads/master")
    before = len(c.sigs())
    code, out, err = c.ouro("run", "1")
    check("after the owner's merge the default branch's run on the merge commit is watched, as the tightened class", code == 0
          and any(s.startswith("run list") and "--branch master" in s and merged in s for s in c.sigs()[before:]))


def sec_express_base_moves():
    """Express reviews before the push; the push's sync merges a base that moved meanwhile, so the merged head is gated and reviewed again."""
    mover = MOVER.replace("src/app.py", "gates/base.txt")
    c = Case("expressmoves", files={"gates/check.py": mover, "gates/base.txt": "base\n"},
             table={"_issue_1": issue(body=ISSUE_BODY.replace("Flow: standard", "Flow: express"))})
    c.node("BUILD", {**BUILD, "writes": {"docs/note.md": "note, edited\n"}})
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1", extra={"CONFLICT_ORIGIN": str(c.origin)})
    head = git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
    check("express whose base moves before the push ends at the PR: exit 0", code == 0 and "ready for merge" in out)
    check("the pushed head holds the moved base", sh("git", "merge-base", "--is-ancestor", "master", head, cwd=c.origin)[0] == 0)
    firsts = [b["body"].split("\n")[0] for b in c.pr_comments() if b["body"].startswith("**Review**")]
    check("the merged head got its own review, after the one at the build commit", len(c.prompts("REVIEW")) == 2 and firsts[-1] == f"**Review** round 2: CLEAN @ {head}")


MOVER = """import os, pathlib, subprocess, sys, tempfile
o = os.environ.get('CONFLICT_ORIGIN')
if o and not pathlib.Path(o + '.done').exists():
    pathlib.Path(o + '.done').write_text('1')
    d = tempfile.mkdtemp()
    subprocess.run(['git', 'clone', '-q', o, d], check=True)
    pathlib.Path(d, 'src/app.py').write_bytes(b'def compute_total(items):\\n    return sum(items) * 2\\n')
    subprocess.run(['git', '-C', d, 'commit', '-qam', 'base moves'], check=True)
    subprocess.run(['git', '-C', d, 'push', '-q', 'origin', 'master'], check=True)
sys.exit(0)
"""


def sec_sync_conflict():
    """The gate script moves the base after the build commit, so the sync meets a conflict."""
    c = Case("syncconflict", files={"gates/check.py": MOVER})
    c.node("BUILD", BUILD, 1)
    c.node("BUILD", {**BUILD, "subject": "fix(app): resolve", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) * 2 + 2\n"}}, 2)
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1", extra={"CONFLICT_ORIGIN": str(c.origin)})
    check("a sync conflict goes to the BUILD node as a finding, once; its resolution lands: exit 0", code == 0 and len(c.prompts("BUILD")) == 2)
    check("the finding is the conflict", "conflict" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read().lower())
    check("the branch pushed holds the base", sh("git", "merge-base", "--is-ancestor", "master", "gates/1-fix-the-total", cwd=c.origin)[0] == 0)
    c2 = Case("syncconflict2", files={"gates/check.py": MOVER})
    c2.node("BUILD", BUILD, 1)
    c2.node("BUILD", {**BUILD, "subject": "fix(app): unresolved", "writes": {"src/app.py": "<<<<<<< ours\n"}}, 2)
    code, out, err = c2.ouro("run", "1", extra={"CONFLICT_ORIGIN": str(c2.origin)})
    stops = [x["body"] for x in (c2.issue_state() or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]
    check("a resolution that leaves markers is `open decision`, exit 3", code == 3 and len(stops) == 1 and stop_comment_ok(stops[0], "open decision"))
    check("nothing was pushed to the branch", "gates/1-fix-the-total" not in c2.branches())


def sec_direct():
    body = "## What\nEdit the note.\n\n## Deliverable\n- `docs/note.md`\n\nDoc impact on close: none\n\nSize: S\nFlow: direct\n"
    c = Case("direct", table={"_issue_1": issue(body=body)})
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a Flow: direct issue inside the globs takes the direct transition (dry run)", code == 0 and "[2a] direct" in out and "Push-Direct.ps1" in out)
    check("the dry run writes nothing", c.wrote() == [] and c.prompts("BUILD") == [])
    body2 = body.replace("`docs/note.md`", "`src/app.py`")
    c2 = Case("directoutside", table={"_issue_1": issue(body=body2)})
    code, out, err = c2.ouro("run", "1", "--dry-run")
    check("a path outside the globs is not the direct lane", code == 0 and "[2a]" not in out)


def sec_parts():
    body = ("## Deliverable\n- [ ] 1. **First (Flow: express).** a\n- [ ] 2. **Second (Flow: full).** b\n\n"
            "- `src/app.py` -- compute_total -- \"def compute_total(items):\"\n\nDoc impact on close: none\n\nSize: M\nFlow: full\n")
    c = Case("parts", table={"_issue_1": issue(body=body)})
    code, out, err = c.ouro("run", "1", "--dry-run", "--part", "2")
    check("--part k reads that part's own Flow and names the -p<k> branch", "class full" in out and "gates/1-fix-the-total-p2" in out)
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("without --part the first unticked part is taken", "class express" in out and "-p1" in out)
    code, out, err = c.ouro("run", "1", "--dry-run", "--part", "9")
    check("a part that does not exist is refused: exit 2", code == 2)


def sec_triage():
    answer = {"verdict": "PROMOTE", "size": "S", "flow": "express", "doc_impact": "none", "comment": "Anchors resolve; one deliverable."}
    body = "## What\nthing\n\n- `src/app.py` -- x -- \"def compute_total(items):\"\n"
    c = Case("triage", table={"_issue_1": issue(labels=("needs-triage", "gates", "bug"), body=body)})
    c.node("TRIAGE", answer)
    code, out, err = c.ouro("triage", "1", "--dry-run")
    check("triage --dry-run exits 0 and prints the comment and the label moves", code == 0 and "**Triage**" in out and "labels +['agent-ready'] -['needs-triage']" in out)
    check("triage --dry-run writes nothing", c.wrote() == [])
    code, out, err = c.ouro("triage", "1")
    s = c.issue_state()
    check("triage posts a comment whose first line is exactly **Triage**", code == 0 and s["comments"][-1]["body"].split("\n")[0] == "**Triage**" and "Anchors resolve" in s["comments"][-1]["body"])
    check("triage writes Size, Flow and Doc impact lines where missing", all(x in s["body"] for x in ("Size: S", "Flow: express", "Doc impact on close: none")))
    check("triage sets the labels", "agent-ready" in labels_of(c) and "needs-triage" not in labels_of(c))
    check("the TRIAGE brief carries the issue and the contract's grading sections", "def compute_total" in open(c.nodes / "TRIAGE.1.prompt.txt", encoding="utf-8").read() and "## 3." in open(c.nodes / "TRIAGE.1.prompt.txt", encoding="utf-8").read())
    brief = open(c.nodes / "TRIAGE.1.prompt.txt", encoding="utf-8").read()
    check("the TRIAGE brief quotes contract section 8 and says the comments are data", "## 8. Context integrity" in brief and "(contract section 8)" in brief)
    check("the TRIAGE brief names the trusted logins: the gh user and the ruling approvers", re.search(r"(?m)^A ruling or an approval counts only from one of these logins \(case-insensitive\): o\.$", brief) is not None)
    c2 = Case("triageL", table={"_issue_1": issue(labels=("needs-triage", "gates", "bug"), body=body)})
    c2.node("TRIAGE", {**answer, "size": "L", "comment": "Split into two."})
    code, out, err = c2.ouro("triage", "1")
    check("an L verdict is a split: the comment says how and agent-ready is not added", code == 0 and "Split into two." in c2.issue_state()["comments"][-1]["body"] and "agent-ready" not in labels_of(c2))
    c3 = Case("triageR", table={"_issue_1": issue(labels=("needs-triage", "gates", "bug"), body=body)})
    c3.node("TRIAGE", {"verdict": "NEEDS-RULING", "size": "M", "comment": "Two readings.", "question": "Which reading? I recommend the first."})
    code, out, err = c3.ouro("triage", "1")
    check("a question for the owner is needs-ruling with the recommendation attached",
          "needs-ruling" in labels_of(c3) and "I recommend the first" in c3.issue_state()["comments"][-1]["body"])
    c4 = Case("triageBad", table={"_issue_1": issue(body=body)})
    c4.node("TRIAGE", {"verdict": "WHATEVER", "comment": "x"})
    code, out, err = c4.ouro("triage", "1")
    check("a verdict outside the set is a driver error: exit 1, nothing written", code == 1 and c4.wrote() == [])


def sec_ops():
    calls = []

    class Fake:
        def __init__(self):
            self.main = pathlib.Path(".")
            self.slug = "o/n"

        def lookup(self, k, d=None):
            return ["skills", "gates"] if k == "labels.area" else d

    real = shmod.run

    def fake(argv, cwd=None, input_text=None, env=None, timeout=None):
        calls.append((argv, input_text))
        return shmod.R(0, "graph-json" if "Get-FootprintGraph.ps1" in str(argv) else "the table", "", argv)

    shmod.run = fake
    try:
        from ouro import ops
        ops.weekly(Fake(), False)
        ops.weekly(Fake(), True)
        ops.compile_(Fake())
    finally:
        shmod.run = real
    check("weekly runs bin/loop-outcomes.py with no argument", calls[0][0][-1].endswith("loop-outcomes.py"))
    check("weekly --post adds --comment", calls[1][0][-1] == "--comment")
    check("compile pipes the graph into the renderer with the repo and the areas", calls[3][1] == "graph-json" and "-Repo" in calls[3][0] and "o/n" in calls[3][0] and '["skills", "gates"]' in calls[3][0])


def stops_of(case, n=1):
    return [x["body"] for x in (case.issue_state(n) or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]


# ---- the spec-trust check (execute section 1) ------------------------------------------------
AFTER, BEFORE = "2026-02-01T00:00:00Z", "2025-12-01T00:00:00Z"


def edits(*rows, nxt=None):
    return {"nodes": [{"editedAt": at, "editor": ({"login": who} if who else None)} for at, who in rows],
            "hasNextPage": nxt is not None, "endCursor": nxt}


def sec_spec_trust():
    c = Case("trustnone", table={"_issue_1": issue(comments=[{"author": {"login": "stranger"}, "body": "**Triage**\n\nok", "createdAt": TRIAGED}])})
    code, out, err = c.ouro("run", "1")
    check("no trusted Triage comment: exit 2, nothing built", code == 2 and c.prompts("BUILD") == [] and c.branches() == ["master"])
    check("the stop is `open decision` and says no trusted Triage exists", len(stops_of(c)) == 1 and stop_comment_ok(stops_of(c)[0], "open decision") and "no `**Triage**` comment" in stops_of(c)[0])
    check("agent-ready -> needs-ruling", "needs-ruling" in labels_of(c) and "agent-ready" not in labels_of(c))
    c = Case("trustpad", table={"_issue_1": issue(comments=[{"author": {"login": "O"}, "body": "  **Triage**  \n\nok", "createdAt": TRIAGED}])})
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a trusted author's Triage line is read case-insensitively, trimmed", code == 0 and "[2] verify: agent-ready" in out)
    c = Case("trustloose", table={"_issue_1": issue(comments=[{"author": {"login": "o"}, "body": "**Triage** later\n\nok", "createdAt": TRIAGED}])})
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a first line that is more than `**Triage**` is no Triage comment: the trust check refuses it, not a later gate",
          code == 2 and "stopped: open decision" in out and "shape gate" not in err)
    c = Case("trustedit", table={"_edits": {"": edits((AFTER, "stranger"))}})
    code, out, err = c.ouro("run", "1")
    check("an untrusted body edit after the Triage comment: exit 2, nothing built", code == 2 and c.prompts("BUILD") == [] and c.branches() == ["master"])
    check("the stop names the editor and the time", len(stops_of(c)) == 1 and stop_comment_ok(stops_of(c)[0], "open decision") and "stranger" in stops_of(c)[0] and AFTER in stops_of(c)[0])
    check("agent-ready -> needs-ruling", "needs-ruling" in labels_of(c) and "agent-ready" not in labels_of(c))
    c = Case("trustnull", table={"_edits": {"": edits((AFTER, None))}})
    code, out, err = c.ouro("run", "1")
    check("an edit with no editor (a deleted account) is untrusted", code == 2 and len(stops_of(c)) == 1 and "deleted" in stops_of(c)[0])
    c = Case("trustok", table={"_edits": {"": edits((AFTER, "O"), (BEFORE, "stranger"))}})
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a trusted edit after the Triage comment passes, and an untrusted edit before it", code == 0 and "[2] verify: agent-ready" in out)
    c = Case("trustpage", table={"_edits": {"": edits((BEFORE, "o"), nxt="p2"), "p2": edits((AFTER, "late-stranger"))}})
    code, out, err = c.ouro("run", "1")
    check("an untrusted edit on the second page is read: exit 2 naming it", code == 2 and len(stops_of(c)) == 1 and "late-stranger" in stops_of(c)[0])
    check("the second page was fetched with the first page's cursor", any("api graphql" in s and "c=p2" in s for s in c.sigs()))


# ---- the review table: size x ship.review ----------------------------------------------------
def sec_review_plan():
    plan = runmod.review_plan
    cells = {("adversarial-review", "S"): ("node", False), ("copilot", "S"): (None, True), ("external-audit", "S"): ("reader", False), ("none", "S"): (None, False),
             ("adversarial-review", "M"): ("node", False), ("copilot", "M"): ("node", True), ("external-audit", "M"): ("node", False), ("none", "M"): ("node", False)}
    for (value, size), want in cells.items():
        check(f"review_plan: {size} x {value} is {want}", plan(value, size) == want)
    ds = runmod.derive_size
    check("three files and sixty lines stay S", ds(["a", "b", "c"], 60) == ("S", ""))
    check("a fourth file makes it M", ds(["a", "b", "c", "d"], 5)[0] == "M")
    check("a changelog mirrored beside three files does not count toward the files", ds(["a", "b", "c", "CHANGELOG.md"], 5) == ("S", ""))
    check("a changelog still counts toward the lines", ds(["a", "CHANGELOG.md"], 61)[0] == "M")
    check("sixty-one changed lines make it M", ds(["a"], 61)[0] == "M")
    check("a workflow path makes it M whatever its size", ds([".github/workflows/ci.yml"], 1)[0] == "M")
    check("parse_direct reads the vehicle and whether a push happened", runmod.parse_direct("vehicle: pr\npushed direct/1 to origin at abc\n") == {"vehicle": "pr", "pushed": True}
          and runmod.parse_direct("fallback: express\n") == {"vehicle": None, "pushed": False})
    f = runmod.reader_findings({"critiques": [{"severity": "blocker", "claim": "c1", "evidence": "src/app.py:2 text"}, {"severity": "major", "claim": "c2"}, {"severity": "minor", "claim": "c3"}, {"severity": "odd", "claim": "c4"}]})
    check("reader critiques map blocker H, major M, minor L, unknown M, with file and line from the evidence",
          [x["severity"] for x in f] == ["H", "M", "L", "M"] and f[0]["file"] == "src/app.py" and f[0]["line"] == 2)


M_BODY = ISSUE_BODY.replace("Size: S", "Size: M")
WHAT_BODY = ISSUE_BODY.replace("Fix the total.", "Sum the line items correctly.")


def sec_review_none():
    c = happy_case("reviewnoneS", review="none")
    code, out, err = c.ouro("run", "1")
    check("S x none: no reviewer runs, the draft goes ready, the owner is asked: exit 0", code == 0 and c.prompts("REVIEW") == [] and c.pr()["isDraft"] is False
          and any(b["body"].startswith("**Ready for merge**") for b in c.pr_comments()))
    check("S x none: the record says `Review: none`", re.search(r"(?m)^Review: none$", c.pr()["body"]) is not None)
    c = happy_case("reviewnoneM", review="none", table={"_issue_1": issue(body=M_BODY)})
    code, out, err = c.ouro("run", "1")
    check("M x none: the REVIEW node runs", code == 0 and c.prompts("REVIEW") == ["REVIEW.1.prompt.txt"] and re.search(r"(?m)^Review: rounds:0", c.pr()["body"]) is not None)
    c = happy_case("sizetighten", review="none", table={"_issue_1": issue(body=ISSUE_BODY)})
    c.node("BUILD", {**BUILD, "writes": {**BUILD["writes"], "docs/a.md": "a\n", "docs/b.md": "b\n", "docs/c.md": "c\n"}})
    code, out, err = c.ouro("run", "1")
    check("an S whose first commit changes four files is an M: the REVIEW node runs under none", code == 0 and c.prompts("REVIEW") == ["REVIEW.1.prompt.txt"])
    check("the record says the Size tightened", "Size tightened from S to M" in c.pr()["body"] and "four" not in c.pr()["body"] and "4 files" in c.pr()["body"])
    c = happy_case("sizenote", review="none", table={"_issue_1": issue(body=ISSUE_BODY)})
    c.node("BUILD", {**BUILD, "writes": {**BUILD["writes"], "docs/a.md": "a\n", "docs/b.md": "b\n", "docs/c.md": "c\n"}})
    c.node("REVIEW", {"_raw": "no json here"})
    code, out, err = c.ouro("run", "1")
    check("the tightening sentence is in the body the draft opens with, before any later refresh", code == 1 and "Size tightened from S to M" in c.pr()["body"])


COPILOT = BINDING.replace('review = "{review}"', 'review = "{review}"\ncopilot_bot_id = "BOT_ok"')
THREAD = {"id": "T1", "isResolved": False, "comments": {"nodes": [{"databaseId": 501, "author": {"login": "copilot-pull-request-reviewer"}, "body": "use math.fsum", "path": "src/app.py", "line": 2}]}}
FIX = {**BUILD, "subject": "fix(app): use fsum", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) + 1\n"},
       "replies": [{"comment_id": 501, "reply": "Fixed in <sha>. Sums with fsum."}]}


def copilot_case(name, body=ISSUE_BODY, table=None, **kw):
    c = Case(name, binding=COPILOT.format(policy="stop-at-pr", review="copilot"), table={"_issue_1": issue(body=body), **(table or {})}, **kw)
    c.node("BUILD", BUILD, 1)
    c.node("REVIEW", CLEAN)
    return c


def sec_copilot():
    c = copilot_case("copilotS", table={"_threads": [THREAD]})
    c.node("BUILD", FIX, 2)
    code, out, err = c.ouro("run", "1")
    sigs = c.sigs()
    check("S x copilot with a thread: exit 0", code == 0)
    check("S x copilot: no REVIEW node, Copilot only", c.prompts("REVIEW") == [] and re.search(r"(?m)^Review: none$", c.pr()["body"]) is not None)
    asked = [k for k, s in enumerate(sigs) if "requestReviews" in s]
    ready = [k for k, s in enumerate(sigs) if s.startswith("pr ready")]
    check("Copilot is requested once, through requestReviews with botIds, after the pull request is marked ready", len(asked) == 1 and ready and asked[0] > ready[0] and 'botIds: ["BOT_ok"]' in sigs[asked[0]] and "userIds" not in sigs[asked[0]])
    check("the thread went to the BUILD node as a finding with its comment id", "comment_id 501" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read() and "use math.fsum" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read())
    head = git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
    replies = json.loads((c.state / "replies.json").read_text(encoding="utf-8")) if (c.state / "replies.json").is_file() else []
    check("the fix is a second commit, pushed", git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "2")
    check("one reply was posted in the thread, with the fix commit's short sha", len(replies) == 1 and replies[0]["id"] == 501 and replies[0]["body"] == f"Fixed in {head[:7]}. Sums with fsum.")
    check("the reply was posted through the replies endpoint with a body file", any(s.startswith("api repos/o/n/pulls/7/comments/501/replies -X POST") for s in sigs))
    check("Ready for merge is posted", any(b["body"].startswith("**Ready for merge**") for b in c.pr_comments()))
    before = len(c.sigs())
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    again = c.sigs()[before:]
    check("a second invocation repeats nothing: no request, no build, no reply", code == 0 and not any("requestReviews" in s or "/replies" in s for s in again) and c.prompts("BUILD") == [])
    c = copilot_case("copilotM", body=M_BODY, table={"_threads": [THREAD]})
    c.node("BUILD", FIX, 2)
    c.node("REVIEW", CLEAN, 1)
    c.node("REVIEW", CLEAN, 2)
    code, out, err = c.ouro("run", "1")
    sigs = c.sigs()
    check("M x copilot: the REVIEW node runs to clean before Copilot is asked", code == 0 and out.index("REVIEW node round 1") < out.index("Copilot requested"))
    check("M x copilot: the REVIEW node verifies the Copilot fix commit", c.prompts("REVIEW") == ["REVIEW.1.prompt.txt", "REVIEW.2.prompt.txt"]
          and "This verifies a fix" in open(c.nodes / "REVIEW.2.prompt.txt", encoding="utf-8").read())
    check("M x copilot: one Copilot request, one reply", len([s for s in sigs if "requestReviews" in s]) == 1 and len([s for s in sigs if "/replies" in s]) == 1)
    check("Copilot's thread is not counted in the Review line", re.search(r"(?m)^Review: rounds:0 findings:H0/M0/L0 false-sentences:0$", c.pr()["body"]) is not None)
    c = copilot_case("copilotdecline", table={"_threads": [THREAD]})
    c.node("BUILD", {**BUILD, "subject": "x", "writes": {}, "replies": [{"comment_id": 501, "reply": "Declined: fsum changes the result type."}]}, 2)
    code, out, err = c.ouro("run", "1")
    replies = json.loads((c.state / "replies.json").read_text(encoding="utf-8")) if (c.state / "replies.json").is_file() else []
    check("a round that declines every thread changes nothing and still answers each thread", code == 0 and git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "1"
          and len(replies) == 1 and replies[0]["body"] == "Declined: fsum changes the result type.")
    c = copilot_case("copilotnever", table={"_copilot_never": True})
    code, out, err = c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "2", "OURO_POLL_SECONDS": "0"})
    check("no Copilot review at the watch limit is exit 0 `re-invoke`, with no Ready for merge", code == 0 and "still pending" in out and not any(b["body"].startswith("**Ready for merge**") for b in c.pr_comments()))
    check("a pending Copilot review ends the invocation before the checks are watched", "[8]" not in out and "checks on #7" not in out)
    c = copilot_case("copilotbot", table={"_copilot_bot": "BOT_other"})
    code, out, err = c.ouro("run", "1")
    check("a bot id the mutation's own response does not confirm is a driver error naming it", code == 1 and "copilot_bot_id" in err)
    c = copilot_case("copilotslow", table={"_copilot_polls": 2})
    code, out, err = c.ouro("run", "1", extra={"OURO_POLL_SECONDS": "0"})
    check("a review that shows a few reads after the request is waited for, and Copilot is asked once", code == 0 and any(b["body"].startswith("**Ready for merge**") for b in c.pr_comments())
          and len([s for s in c.sigs() if "requestReviews" in s]) == 1)
    failing = "api repos/o/n/pulls/7/comments/501/replies*"
    c = copilot_case("copilotresume", table={"_threads": [THREAD], failing: {"Exit": 1, "Out": "HTTP 502"}})
    c.node("BUILD", FIX, 2)
    code, out, err = c.ouro("run", "1")
    check("a reply that fails to post is a driver error naming the thread, after the fix is pushed", code == 1 and "reply in thread 501" in err
          and git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "2")
    tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    del tbl[failing]
    c.set_table(tbl)
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    fixed = git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
    replies = json.loads((c.state / "replies.json").read_text(encoding="utf-8")) if (c.state / "replies.json").is_file() else []
    check("the next invocation posts the reply the fix commit's Ouro-Build line holds, with that commit's short sha, and asks the build nothing",
          code == 0 and c.prompts("BUILD") == [] and git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "2"
          and len(replies) == 1 and replies[0]["body"] == f"Fixed in {fixed[:7]}. Sums with fsum.")
    thread2 = {"id": "T2", "isResolved": False, "comments": {"nodes": [{**THREAD["comments"]["nodes"][0], "databaseId": 502, "body": "rename total"}]}}
    c = copilot_case("copilotsilent", table={"_threads": [THREAD, thread2]})
    c.node("BUILD", FIX, 2)
    code, out, err = c.ouro("run", "1")
    replies = {x["id"]: x["body"] for x in json.loads((c.state / "replies.json").read_text(encoding="utf-8"))} if (c.state / "replies.json").is_file() else {}
    check("a thread the build gave no reply for is answered with a line saying so, in the same round", code == 0 and len(c.prompts("BUILD")) == 2
          and replies.get(502) == runmod.NO_REPLY and replies.get(501, "").startswith("Fixed in "))


def sec_resume():
    """What a later invocation needs from an earlier one is read back from the tracker or the repository."""
    ans = {**BUILD, "residue": ["the caller in src/report.py still rounds twice"]}
    c = Case("resumepr", table={"pr create*": {"Exit": 1, "Out": "HTTP 502"}})
    c.node("BUILD", ans)
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    check("a pr create that fails after the push is a driver error: exit 1, the branch is pushed", code == 1 and "gates/1-fix-the-total" in c.branches())
    tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    del tbl["pr create*"]
    c.set_table(tbl)
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    body = (c.pr() or {}).get("body", "")
    check("the next invocation opens the pull request with the door and blast radius the build answered, and asks it nothing",
          code == 0 and c.prompts("BUILD") == [] and f"- **Door:** {BUILD['door']}" in body and f"- **Blast radius:** {BUILD['blast_radius']}" in body)
    check("the record's Follow-up section holds the build's residue", "- the caller in src/report.py still rounds twice" in body.split("## Follow-up", 1)[-1])
    c = Case("resumetwo")
    c.node("BUILD", {**BUILD, "door": "one-way: a migration", "residue": ["first"], "writes": {**BUILD["writes"], "gates/exit.txt": "1"}}, 1)
    c.node("BUILD", {**BUILD, "subject": "fix(app): make the gate pass", "door": "two-way: later", "residue": ["second", "first"], "writes": {"gates/exit.txt": "0"}}, 2)
    c.node("REVIEW", CLEAN)
    code, out, err = c.ouro("run", "1")
    body = (c.pr() or {}).get("body", "")
    check("with two commits the first door that was stated wins and the residue is their union, once each", code == 0 and "- **Door:** one-way: a migration" in body
          and body.split("## Follow-up", 1)[-1].split("<!--", 1)[0].strip().splitlines() == ["- first", "- second"])


def sec_external():
    ok = audit_ok()
    c = happy_case("readerok", binding=EXTERNAL.format(policy="stop-at-pr", review="external-audit"), table={"_issue_1": issue(body=WHAT_BODY)})
    empty = {"stdout": json.dumps({"verdict": "APPROVE", "files_read": [], "critiques": []}), "exit": 0}
    code, out, err = c.ouro("run", "1", extra=c.grok(empty, ok))
    check("S x external-audit: an unfalsifiable round is run once more, then the reader's APPROVE is clean: exit 0", code == 0 and len(c.grok_calls()) == 2 and c.prompts("REVIEW") == [])
    marks = [b["body"].split("\n")[0] for b in c.pr_comments() if b["body"].startswith("**Review**")]
    check("the verdict is posted as `**Review** round 1: CLEAN @ <head>`", marks == [f"**Review** round 1: CLEAN @ {git(c.origin, 'rev-parse', 'refs/heads/gates/1-fix-the-total')}"])
    prompts = list((c.root / "tmp").glob("external-audit-*/*/round-1/prompt.txt"))
    check("the goal carries the issue title and its What section", prompts and "Sum the line items correctly." in prompts[0].read_text(encoding="utf-8") and "Fix the total" in prompts[0].read_text(encoding="utf-8"))
    check("the reader ran on the range origin/<default>...<head>, from the build's worktree", "--prompt-file" in c.grok_calls()[0])
    c = Case("readerrevise", binding=EXTERNAL.format(policy="stop-at-pr", review="external-audit"))
    c.node("BUILD", BUILD, 1)
    c.node("BUILD", {**BUILD, "subject": "fix(app): the last item", "writes": {"src/app.py": "def compute_total(items):\n    return sum(items) + 2\n"}}, 2)
    crit = {"id": "finding-1", "severity": "major", "lens": "x", "claim": "the total drops the last item", "evidence": "src/app.py:2 return sum(items)", "probe": "[1, 2]", "in_scope_claim": True}
    code, out, err = c.ouro("run", "1", extra=c.grok({"stdout": json.dumps({"verdict": "REVISE", "files_read": ["src/app.py"], "critiques": [crit]}), "exit": 0}, ok))
    calls = c.grok_calls()
    check("a REVISE with a major is a fix round, then the reader's second round on the same session approves: exit 0", code == 0 and len(calls) == 2 and len(c.prompts("BUILD")) == 2 and c.prompts("REVIEW") == [])
    check("round 2 resumes round 1's session", " -s " in " " + calls[0] and " -r " in " " + calls[1] and calls[1].split(" -r ")[1].split()[0] == calls[0].split(" -s ")[1].split()[0])
    check("the finding reached the BUILD node as an M", "M src/app.py:2 the total drops the last item" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read())
    check("the Review line counts the reader's round", re.search(r"(?m)^Review: rounds:1 findings:H0/M1/L0 false-sentences:unread$", c.pr()["body"]) is not None)
    firsts = [b["body"].split("\n")[0] for b in c.pr_comments() if b["body"].startswith("**Review**")]
    check("both rounds are `**Review** round k` comments", len(firsts) == 2 and firsts[0].startswith("**Review** round 1: FINDINGS @ ") and firsts[1].startswith("**Review** round 2: CLEAN @ "))
    fix_head = git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
    round2 = list((c.root / "tmp").glob("external-audit-*/*/round-2/prompt.txt"))
    digest = round2[0].read_text(encoding="utf-8") if round2 else ""
    check("the round-2 digest names each prior finding's claim and the fix commit's sha and subject, and nothing the driver did not read",
          "the total drops the last item" in digest and f"the fix commit {fix_head[:7]}: fix(app): the last item" in digest and "ACCEPTED" not in digest and "gates ran green" not in digest)
    c = Case("readercap", binding=EXTERNAL.format(policy="stop-at-pr", review="external-audit"))
    for k in (1, 2, 3):
        c.node("BUILD", {**BUILD, "subject": f"fix(app): round {k}", "writes": {"src/app.py": f"def compute_total(items):\n    return {k}\n"}}, k)
    c.node("REVIEW", CLEAN)
    revise = {"stdout": json.dumps({"verdict": "REVISE", "files_read": ["src/app.py"], "critiques": [crit]}), "exit": 0}
    code, out, err = c.ouro("run", "1", extra=c.grok(revise, revise))
    check("after the reader's two rounds the REVIEW node verifies the later fix: exit 0, two reader calls, one REVIEW call", code == 0 and len(c.grok_calls()) == 2 and c.prompts("REVIEW") == ["REVIEW.1.prompt.txt"])
    check("the record says the reader's rounds are spent", "two rounds are spent" in c.pr()["body"])
    c = happy_case("readerfail", binding=EXTERNAL.format(policy="stop-at-pr", review="external-audit"))
    code, out, err = c.ouro("run", "1", extra=c.grok({"stdout": "", "exit": 1}))
    check("a round 1 that exits 1 falls back to the REVIEW node: exit 0", code == 0 and c.prompts("REVIEW") == ["REVIEW.1.prompt.txt"])
    check("the record says the reader was unavailable", "was unavailable" in c.pr()["body"] and "(grok)" in c.pr()["body"])
    c = happy_case("readerthree", binding=EXTERNAL.format(policy="stop-at-pr", review="external-audit"))
    empty = {"stdout": json.dumps({"verdict": "APPROVE", "files_read": [], "critiques": []}), "exit": 0}
    code, out, err = c.ouro("run", "1", extra=c.grok(empty, empty))
    check("round 1 exiting 3 twice falls back to the REVIEW node and says so", code == 0 and len(c.grok_calls()) == 2 and c.prompts("REVIEW") == ["REVIEW.1.prompt.txt"] and "was unavailable" in c.pr()["body"])
    c = happy_case("readerM", binding=EXTERNAL.format(policy="stop-at-pr", review="external-audit"), table={"_issue_1": issue(body=M_BODY)})
    code, out, err = c.ouro("run", "1", extra=c.grok(ok))
    check("M x external-audit: the REVIEW node, the reader not called", code == 0 and c.grok_calls() == [] and c.prompts("REVIEW") == ["REVIEW.1.prompt.txt"])


def audit_ok():
    return {"stdout": json.dumps({"verdict": "APPROVE", "files_read": ["src/app.py"], "critiques": []}), "exit": 0}


EXTERNAL = BINDING.replace('review = "{review}"', 'review = "{review}"\nexternal_cli = "grok"')


# ---- the direct lane (variants, Direct) ------------------------------------------------------
DIRECT_BODY = "## What\nEdit the note.\n\n## Deliverable\n- `docs/note.md`\n\nDoc impact on close: none\n\nSize: S\nFlow: direct\n"
NOTE_BUILD = {**BUILD, "writes": {"docs/note.md": "note, edited\n"}}
UNPROTECTED = {"api repos/o/n/rules/branches/master": {"Exit": 0, "Out": []},
               "api repos/o/n/branches/master/protection": {"Exit": 1, "Out": "Branch not protected"}}
LANE_GATES = ('[[gate]]\nareas = ["*"]\nrun = "python3 gates/doc_gate.py"\npaths = ["docs/"]\n'
              '[[gate]]\nareas = ["*"]\nrun = "python3 gates/src_gate.py"\npaths = ["src/"]\n')
RED = {"databaseId": 11, "workflowDatabaseId": 1, "workflowName": "ci", "event": "push", "createdAt": "2026-01-01T00:00:00Z", "status": "completed", "conclusion": "failure"}
GREEN = {**RED, "conclusion": "success"}


def lane_case(name, protection=UNPROTECTED, table=None, files=None):
    c = Case(name, binding=BINDING.format(policy="stop-at-pr", review="adversarial-review") + LANE_GATES, table={"_issue_1": issue(body=DIRECT_BODY), **protection, **(table or {})},
             files={"gates/doc_gate.py": "print('doc gate ran')\n", "gates/src_gate.py": "print('src gate ran')\n", **(files or {})})
    c.node("BUILD", NOTE_BUILD)
    c.node("REVIEW", CLEAN)
    return c


def run_rows(*rows):
    return {"run list*": {"Seq": [{"Exit": 0, "Out": [r]} for r in rows]}}


def sec_lane():
    c = lane_case("lanepush")
    code, out, err = c.ouro("run", "1")
    tip = git(c.origin, "rev-parse", "refs/heads/master")
    msg = git(c.origin, "log", "-1", "--format=%B", tip)
    check("a clean main checkout takes the lane: the commit is on origin's default branch, exit 0", code == 0 and git(c.origin, "log", "-1", "--format=%s", tip) == "fix(app): sum the items" and git(c.origin, "rev-list", "--count", "master") == "2")
    check("the commit message carries Fixes #1", re.search(r"(?m)^Fixes #1$", msg) is not None)
    check("no branch, no pull request, no worktree, no reviewer", c.branches() == ["master"] and not any(s.startswith("pr create") for s in c.sigs()) and not (c.main / ".claude" / "worktrees").exists() and c.prompts("REVIEW") == [])
    check("the BUILD node ran in the main checkout", pathlib.Path(c.node_call("BUILD")["cwd"]).resolve() == c.main.resolve())
    check("the default branch's run on the commit was watched (--sha)", any(s.startswith("run list") and f"--commit {tip}" in s and "--branch master" in s for s in c.sigs()))
    check("gates run in the lane: the one whose paths the diff touches ran, the other did not", "gate `python3 gates/doc_gate.py`: exit 0" in out and "src_gate" not in out)
    check("the main checkout is clean and at origin's tip", git(c.main, "status", "--porcelain") == "" and git(c.main, "rev-parse", "HEAD") == tip)
    c = lane_case("lanedirty")
    c.write("scratch.txt", "mine\n")
    before = git(c.main, "rev-parse", "HEAD")
    code, out, err = c.ouro("run", "1")
    check("a dirty main checkout does not take the lane: it says why and runs express from a worktree, exit 0", code == 0 and "[2a] direct: not taken, the main checkout has uncommitted changes" in out and c.pr() is not None)
    check("the main checkout's files and HEAD are untouched", (c.main / "scratch.txt").read_text() == "mine\n" and git(c.main, "rev-parse", "HEAD") == before and git(c.main, "status", "--porcelain") == "?? scratch.txt")
    check("the BUILD node ran in a worktree, not in the main checkout", ".claude" in c.node_call("BUILD")["cwd"] and "worktrees" in c.node_call("BUILD")["cwd"])
    check("nothing reached origin's default branch", git(c.origin, "rev-list", "--count", "master") == "1")
    c = lane_case("laneother")
    git(c.main, "checkout", "-q", "-b", "other")
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a main checkout on another branch does not take the lane", code == 0 and "not taken, the main checkout is on other, not master" in out)
    c = lane_case("laneahead")
    c.write("docs/note.md", "ahead\n")
    git(c.main, "commit", "-qam", "local only")
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a main checkout ahead of origin does not take the lane", code == 0 and "not taken, the main checkout's HEAD is not origin/master" in out)
    c = lane_case("laneclosed", table={"_issue_1": issue(body=DIRECT_BODY, state="CLOSED")})
    code, out, err = c.ouro("run", "1")
    check("a closed issue with no branch is not run again", code == 0 and "is closed" in out and c.prompts("BUILD") == [])
    c = lane_case("lanepr", protection={"api repos/o/n/rules/branches/master": {"Exit": 0, "Out": [{"type": "required_status_checks"}]},
                                        "api repos/o/n/branches/master/protection": {"Exit": 1, "Out": "Branch not protected"}})
    code, out, err = c.ouro("run", "1")
    merged = git(c.origin, "rev-parse", "refs/heads/master")
    check("vehicle: pr opens a pull request from direct/1, merges it once green, no review: exit 0", code == 0 and any(s.startswith("pr create") and "--head direct/1" in s for s in c.sigs())
          and any(s.startswith("pr merge") for s in c.sigs()) and c.prompts("REVIEW") == [])
    check("the pull request's body is the commit message with Fixes #1", "Fixes #1" in c.pr()["body"] and c.pr()["body"].startswith("fix(app): sum the items"))
    check("the merge commit's run was watched", any(s.startswith("run list") and f"--commit {merged}" in s for s in c.sigs()))
    check("the main checkout is back on a clean default branch at origin's tip, direct/1 gone locally", git(c.main, "status", "--porcelain") == "" and git(c.main, "rev-parse", "HEAD") == merged and git(c.main, "branch", "--list", "direct/1") == "")
    lane_msg = [m.strip().splitlines() for m in git(c.main, "log", "-g", "--format=%B%x00", "HEAD").split("\0") if "sum the items" in m]
    check("the lane commit ends with its Ouro-Build line, and the direct/1 pull request's body and merge commit hold none",
          any(m and m[-1].startswith("Ouro-Build: ") for m in lane_msg) and "Ouro-Build" not in c.pr()["body"] and "Ouro-Build" not in git(c.origin, "log", "-1", "--format=%B", merged))
    approval = {"api repos/o/n/rules/branches/master": {"Exit": 0, "Out": [{"type": "pull_request", "parameters": {"required_approving_review_count": 1}}]},
                "api repos/o/n/branches/master/protection": {"Exit": 1, "Out": "Branch not protected"}}
    c = lane_case("lanefallback", protection=approval)
    c.node("BUILD", {**NOTE_BUILD, "door": "one-way: a published note", "residue": ["the index page still links the old note"]})
    code, out, err = c.ouro("run", "1")
    check("fallback: the pull request carries the lane build's door and its residue as a follow-up",
          "- **Door:** one-way: a published note" in c.pr()["body"] and "the index page still links the old note" in c.pr()["body"].split("## Follow-up", 1)[-1])
    check("fallback: express: the commit ends on the worktree's branch and the run goes on to a pull request: exit 0", code == 0 and c.pr() is not None and "gates/1-fix-the-total" in c.branches()
          and git(c.origin, "rev-list", "--count", "master..gates/1-fix-the-total") == "1")
    check("fallback: the main checkout is back at origin's tip and clean", git(c.main, "rev-parse", "HEAD") == git(c.origin, "rev-parse", "refs/heads/master") and git(c.main, "status", "--porcelain") == "")
    check("fallback: the worktree is the branch's", git(c.main, "worktree", "list", "--porcelain").count("worktrees/1-fix-the-total") == 1)
    c = lane_case("laneoutside")
    c.node("BUILD", BUILD)
    code, out, err = c.ouro("run", "1")
    check("a diff outside [ship].direct pushes nothing and continues from a worktree", code == 0 and "outside [ship].direct" in out and git(c.origin, "rev-list", "--count", "master") == "1" and c.pr() is not None
          and git(c.main, "rev-parse", "HEAD") == git(c.origin, "rev-parse", "refs/heads/master"))
    c = lane_case("lanestop", files={"gates/doc_gate.py": "open('junk.txt', 'w').write('x')\n"})
    code, out, err = c.ouro("run", "1")
    check("a stop before a push (Push-Direct finds the tree dirty) takes the same worktree recovery: exit 0", code == 0 and "stopped before a push" in out and c.pr() is not None
          and git(c.origin, "rev-list", "--count", "master") == "1" and git(c.main, "rev-parse", "HEAD") == git(c.origin, "rev-parse", "refs/heads/master"))
    c = lane_case("lanereadback")
    hook = c.origin / "hooks" / "post-receive"
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes(b"#!/bin/sh\nn=$(git commit-tree master^{tree} -p master -m 'pushed after')\ngit update-ref refs/heads/master $n\n")
    hook.chmod(0o755)
    code, out, err = c.ouro("run", "1")
    check("a failed read-back after the push fetches and goes on to the watch of the pushed commit: exit 0", code == 0 and "the read-back after the push failed" in out
          and c.pr() is None and any(s.startswith("run list") and "--commit " + git(c.origin, "rev-parse", "master~1") in s for s in c.sigs()))
    c = lane_case("lanegreen", table=run_rows(RED, RED, GREEN))
    c.node("BUILD", {**NOTE_BUILD, "subject": "fix(note): second try", "writes": {"docs/note.md": "note, again\n"}}, 2)
    code, out, err = c.ouro("run", "1")
    check("one red run gets one fix commit through the lane, then green: exit 0", code == 0 and git(c.origin, "rev-list", "--count", "master") == "3" and git(c.origin, "log", "-1", "--format=%s", "master") == "fix(note): second try")
    check("the fix round's brief carries the run's url and log tail", "actions/runs/11" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read() and "the failed log line" in open(c.nodes / "BUILD.2.prompt.txt", encoding="utf-8").read())
    check("the fix commit carries Fixes #1 too and no stop was posted", re.search(r"(?m)^Fixes #1$", git(c.origin, "log", "-1", "--format=%B", "master")) is not None and stops_of(c) == [])
    c = lane_case("lanetwored", table={**run_rows(RED, RED, {**RED, "databaseId": 12}, {**RED, "databaseId": 12}), "issue view 1 --json state": {"Exit": 0, "Out": {"state": "CLOSED"}}})
    c.node("BUILD", {**NOTE_BUILD, "subject": "fix(note): second try", "writes": {"docs/note.md": "note, again\n"}}, 2)
    init_tree = git(c.main, "rev-parse", "HEAD^{tree}")
    code, out, err = c.ouro("run", "1")
    check("a second red reverts both commits through the lane: one revert commit pushed, the tree is the original, exit 3", code == 3 and git(c.origin, "rev-list", "--count", "master") == "4"
          and git(c.origin, "log", "-1", "--format=%s", "master").startswith("Revert") and git(c.origin, "rev-parse", "master^{tree}") == init_tree)
    check("the stop is `open decision`, names both run urls, and the labels move to needs-ruling", len(stops_of(c)) == 1 and stop_comment_ok(stops_of(c)[0], "open decision") and "runs/11" in stops_of(c)[0] and "runs/12" in stops_of(c)[0]
          and "needs-ruling" in labels_of(c) and "agent-ready" not in labels_of(c))
    check("a closed issue is reopened", any(s.startswith("issue reopen 1") for s in c.sigs()))
    c = lane_case("lanetworedopen", table=run_rows(RED))
    c.node("BUILD", {**NOTE_BUILD, "subject": "fix(note): second try", "writes": {"docs/note.md": "note, again\n"}}, 2)
    code, out, err = c.ouro("run", "1")
    check("an open issue is not reopened", code == 3 and not any(s.startswith("issue reopen") for s in c.sigs()))
    protected = {"api repos/o/n/rules/branches/master": {"Exit": 0, "Out": [{"type": "required_status_checks"}]},
                 "api repos/o/n/branches/master/protection": {"Exit": 1, "Out": "Branch not protected"}}
    c = lane_case("lanepresume", protection=protected, table={"run list*": {"Exit": 0, "Out": []}})
    code, out, err = c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "6"})
    creates = len([s for s in c.sigs() if s.startswith("pr create")])
    check("vehicle: pr with the checks still running at the limit: exit 0, one pull request on direct/1", code == 0 and "still running" in out and creates == 1 and c.pr()["head"] == "direct/1")
    c.clear_prompts()
    code, out, err = c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "6"})
    check("the next invocation finds the open pull request on direct/1: no build, no second pull request, exit 0", code == 0 and "still running" in out and c.prompts("BUILD") == []
          and len([s for s in c.sigs() if s.startswith("pr create")]) == creates and git(c.origin, "rev-list", "--count", "master") == "1")
    tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    del tbl["run list*"]
    c.set_table(tbl)
    code, out, err = c.ouro("run", "1")
    merged = git(c.origin, "rev-parse", "refs/heads/master")
    check("once the checks are green the resumed run merges it and watches the merge commit, with no build", code == 0 and c.prompts("BUILD") == [] and len([s for s in c.sigs() if s.startswith("pr create")]) == creates
          and any(s.startswith("pr merge") for s in c.sigs()) and any(s.startswith("run list") and f"--commit {merged}" in s for s in c.sigs()))
    check("the main checkout is on a clean default branch at origin's tip, direct/1 gone locally", git(c.main, "status", "--porcelain") == "" and git(c.main, "rev-parse", "HEAD") == merged and git(c.main, "branch", "--list", "direct/1") == "")
    c = lane_case("lanepresumelabel", protection=protected, table={"run list*": {"Exit": 0, "Out": []}})
    c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "6"})
    tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    tbl["_issue_1"] = issue(body=DIRECT_BODY, labels=("gates", "bug", "needs-ruling"))
    c.set_table(tbl)
    code, out, err = c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "6"})
    check("the lane's open pull request is not resumed for an issue that left agent-ready: exit 3", code == 3 and not any(s.startswith("pr merge") for s in c.sigs()))
    c = lane_case("laneresume")
    c.write("docs/note.md", "note, edited\n")
    git(c.main, "commit", "-qam", "fix(app): sum the items\n\nSum the items in compute_total.\n\nFixes #1")
    unpushed = git(c.main, "rev-parse", "HEAD")
    code, out, err = c.ouro("run", "1")
    check("a main checkout ahead by this issue's own unpushed commit ships it: no build, the commit is origin's tip, exit 0", code == 0 and c.prompts("BUILD") == []
          and git(c.origin, "rev-parse", "refs/heads/master") == unpushed and git(c.main, "rev-parse", "HEAD") == unpushed and git(c.main, "status", "--porcelain") == "")
    check("the pushed commit's run was watched", any(s.startswith("run list") and f"--commit {unpushed}" in s for s in c.sigs()))
    c = lane_case("laneresumeother")
    c.write("docs/note.md", "note, edited\n")
    git(c.main, "commit", "-qam", "fix(app): another issue\n\nFixes #2")
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("a main checkout ahead by another issue's commit does not take the lane", code == 0 and "not taken, the main checkout's HEAD is not origin/master" in out)
    c.write("docs/note.md", "note, again\n")
    git(c.main, "commit", "-qam", "fix(app): this issue\n\nFixes #1")
    code, out, err = c.ouro("run", "1", "--dry-run")
    check("one commit without this issue's Fixes line among the ahead commits keeps the refusal", code == 0 and "not taken, the main checkout's HEAD is not origin/master" in out)


def sec_live():
    if os.environ.get("OURO_LIVE_NODE") != "1":
        print("SKIP: OURO_LIVE_NODE=1 not set; no real claude -p call was made")
        return
    nodemod.USAGE.clear()
    ans = nodemod.run_node("FOLLOWUP", 'Answer with the JSON object {"issues": []} and nothing else.', "haiku", list(nodemod.READ_TOOLS), str(TMP), 300)
    check("a real claude -p call returns a parsed answer", ans == {"issues": []} or isinstance(ans.get("issues"), list))
    check("the call's usage is recorded", bool(nodemod.USAGE) and (nodemod.USAGE[0]["input"] + nodemod.USAGE[0]["cache_read"] + nodemod.USAGE[0]["cache_creation"]) > 0 and nodemod.USAGE[0]["output"] > 0)


def sec_lane_moved_main():
    """A lane resumed at its pull request after the owner moved the main checkout: no fix is built there."""
    protected = {"api repos/o/n/rules/branches/master": {"Exit": 0, "Out": [{"type": "required_status_checks"}]},
                 "api repos/o/n/branches/master/protection": {"Exit": 1, "Out": "Branch not protected"}}

    def resumed(name, move, runs):
        c = lane_case(name, protection=protected, table={"run list*": {"Exit": 0, "Out": []}})
        c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "6"})
        move(c)
        tbl = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
        tbl["run list*"] = {"Seq": [{"Exit": 0, "Out": [{**GREEN, "event": "pull_request"}]}] * 4 + [{"Exit": 0, "Out": [r]} for r in runs]}
        c.set_table(tbl)
        c.clear_prompts()
        c.node("BUILD", {**NOTE_BUILD, "subject": "fix(note): second try", "writes": {"docs/note.md": "note, again\n"}})
        return c, c.ouro("run", "1")

    def stops(c):
        return [x["body"] for x in (c.issue_state() or {"comments": []})["comments"] if x["body"].startswith("**Stop:**")]

    c, (code, out, err) = resumed("lanewip", lambda c: c.write("owner-wip.txt", "my unfinished work\n"), [RED, RED, GREEN])
    check("a red run on a resumed lane whose main checkout holds an untracked file: exit 3, `**Stop:** open decision`, no BUILD",
          code == 3 and len(stops(c)) == 1 and stop_comment_ok(stops(c)[0], "open decision") and c.prompts("BUILD") == [])
    check("the owner's untracked file stays in the main checkout, untracked", (c.main / "owner-wip.txt").is_file() and git(c.main, "status", "--porcelain").strip() == "?? owner-wip.txt")

    def branch_off(c):
        git(c.main, "checkout", "-q", "-b", "feature-x")
        c.write("src/feature.py", "x = 1\n")
        git(c.main, "add", "-A")
        git(c.main, "commit", "-qm", "owner: feature work")
    c, (code, out, err) = resumed("lanebranch", branch_off, [RED, RED, GREEN])
    tip = git(c.main, "rev-parse", "feature-x")
    check("a red run on a resumed lane whose main checkout is on the owner's branch: exit 3, no BUILD, the branch untouched and not pushed",
          code == 3 and len(stops(c)) == 1 and c.prompts("BUILD") == [] and git(c.main, "rev-parse", "--abbrev-ref", "HEAD") == "feature-x"
          and git(c.main, "log", "-1", "--format=%s", tip) == "owner: feature work" and "feature-x" not in git(c.origin, "branch", "--list"))
    c, (code, out, err) = resumed("lanebranchgreen", lambda c: git(c.main, "checkout", "-q", "-b", "feature-y"), [GREEN, GREEN])
    check("a green run on a resumed lane whose main checkout is on a fresh owner's branch: exit 0, that branch not fast-forwarded to the merge",
          code == 0 and git(c.main, "rev-parse", "feature-y") == git(c.main, "rev-parse", "master") != git(c.origin, "rev-parse", "refs/heads/master")
          and git(c.main, "rev-parse", "--abbrev-ref", "HEAD") == "feature-y")


for name, fn in [("may_merge: the merge decision", sec_may_merge), ("pure: parsers, markers, the Review line", sec_pure),
                 ("triage: label moves and body lines", sec_labels), ("node: the adapter in stub mode", sec_node),
                 ("stub: the gh stand-in shadows gh and refuses the unlisted", sec_stub_proof),
                 ("run: no binding", sec_no_binding), ("run: a binding the reader refuses", sec_bad_binding),
                 ("run: not agent-ready", sec_not_ready), ("run: shape finding", sec_shape), ("run: dead anchor", sec_dead_anchor),
                 ("run: --dry-run", sec_dry_run), ("run: parts", sec_parts), ("run: direct lane", sec_direct),
                 ("run: BUILD stop", sec_build_stop), ("run: node garbage", sec_node_garbage), ("run: gate uncovered", sec_gate_uncovered), ("run: the BUILD brief's commands", sec_brief),
                 ("run: happy path, resume, owner merge", sec_happy), ("run: trivial-merge", sec_trivial_merge),
                 ("run: review findings and the fix round", sec_findings_fix), ("run: review cap", sec_review_cap),
                 ("run: red gate", sec_gate_red), ("run: full class gates by paths", sec_full_paths), ("run: a fix round's gates and OUTSIDE findings", sec_fix_round_scope), ("run: checks still running", sec_checks_running), ("run: flake and real check failure", sec_check_flake),
                 ("run: merge refused", sec_merge_refused), ("run: express", sec_express), ("run: flow tightens", sec_flow_tighten), ("run: express base moves", sec_express_base_moves),
                 ("run: sync conflict", sec_sync_conflict), ("run: spec trust", sec_spec_trust), ("review table: plan and size", sec_review_plan),
                 ("run: review none and size tightening", sec_review_none), ("run: Copilot", sec_copilot), ("run: resume from the tracker", sec_resume), ("run: external reader", sec_external),
                 ("run: lane outcomes", sec_lane), ("run: lane resumed in a moved main checkout", sec_lane_moved_main), ("triage: the TRIAGE node", sec_triage), ("ops: weekly and compile", sec_ops),
                 ("live: one real claude -p call", sec_live)]:
    section(name, fn)

if failures:
    print(f"\n{failures} check(s) failed")
    sys.exit(1)
print("\nall driver cases pass")
