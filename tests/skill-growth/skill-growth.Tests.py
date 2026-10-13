#!/usr/bin/env python3
"""Fixture suite for release/skill-growth.py: the report's contract, driven as a subprocess.

Scratch repositories under a temporary directory, built by git with fixed identities, stand in
for a branch and its base; a `claude` stand-in first on PATH (claude.cmd on Windows, a shell
launcher elsewhere, each running stub.py with this interpreter) answers `plugin details` with a
canned table, records its argv and exits as told. Nothing touches the network. Each row names the
rule it holds, and a row that asserts an absence sits beside a presence that proves it read the
report.

The script is not vendored: in the vendored layout (the scripts flat, Test-DocsFreshness.ps1
among them, and no release/skill-growth.py) the suite prints one `skip:` line and exits 0, which
is what the No binding workflow runs. In the plugin layout a missing script is red.

stdlib only; needs python3 3.11 or later and git 2.28 or later on PATH.
Its [[gate]] in .claude/ouro.toml: run = "python3 tests/skill-growth/skill-growth.Tests.py"
"""
import atexit, json, os, pathlib, shutil, stat, subprocess, sys, tempfile

sys.dont_write_bytecode = True
BASE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = BASE / "release" / "skill-growth.py"
if not SCRIPT.is_file() and (BASE / "Test-DocsFreshness.ps1").is_file():
    print("skip: no release/skill-growth.py beside the flat gate scripts (vendored layout); the script is this repository's own")
    sys.exit(0)

TMP = pathlib.Path(tempfile.mkdtemp(prefix="skill-growth-test-run-"))


def _remove_tmp():
    for root, _, files in os.walk(TMP):
        for name in files:
            path = os.path.join(root, name)
            try:
                os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
            except OSError:
                pass
    try:
        shutil.rmtree(TMP)
    except OSError as e:
        print(f"WARN: could not remove {TMP}: {e}", file=sys.stderr)


atexit.register(_remove_tmp)
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

failures = 0


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "commit.gpgsign=false",
                        "-c", "core.autocrlf=false", *args], cwd=cwd, capture_output=True)
    if r.returncode:
        raise RuntimeError(f"git {args} in {cwd}: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout.decode("utf-8", "replace").strip()


def commit(root, msg, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode("utf-8"))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def run(cwd, *args, env=ENV):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


FM = "---\nname: x\n---\n"
check(f"the script is there: {SCRIPT}", SCRIPT.is_file())

print("the selftest")
code, out, err = run(TMP, "--selftest")
check("--selftest is green", code == 0 and out.strip().endswith("skill-growth: selftest ok"))

print("the base is the merge base")
# master: m 2 -> 5 after the branch point; topic: n 1 -> 3.
repo = TMP / "repo"
repo.mkdir()
git(repo, "init", "-q", "-b", "master")
fork = commit(repo, "one", {"skills/m/SKILL.md": FM + "a\nb\n", "skills/n/SKILL.md": FM + "a\n"})
git(repo, "checkout", "-q", "-b", "topic")
commit(repo, "topic grows n", {"skills/n/SKILL.md": FM + "a\nb\nc\n"})
git(repo, "checkout", "-q", "master")
commit(repo, "master grows m", {"skills/m/SKILL.md": FM + "a\nb\nc\nd\ne\n"})
git(repo, "checkout", "-q", "topic")
code, out, err = run(repo, "--base", "master")
check("the branch's own change is a line", code == 0 and "  skills/n/SKILL.md: 1 -> 3 (+2)\n" in out)
check("the base's later edit is not, and the total is the branch's", "skills/m" not in out and "  total: 3 -> 5 (+2)\n" in out)
check("the header names the merge base and the ref", f"{fork[:7]} (merge base of master and HEAD)" in out)

print("the default base is origin/master")
clone = TMP / "clone"
git(TMP, "clone", "-q", str(repo), str(clone))
code, out, err = run(clone)
check("no --base: the clone's origin/master", code == 0 and "  skills/n/SKILL.md: 1 -> 3 (+2)\n" in out
      and "(merge base of origin/master and HEAD)" in out)

print("nothing changed")
code, out, err = run(repo, "--base", "topic")
check("the totals only, and nothing to estimate", code == 0 and "  skills/" not in out and "  total: 5 -> 5 (+0)\n" in out
      and "overall: 5 -> 5 (+0)\n" in out and "on-invoke" not in out)

print("an unreadable repository is exit 2; a base or HEAD that is not a commit here is one INFO line")
plain = TMP / "plain"
plain.mkdir()
code, out, err = run(plain)
check("outside a repository: no report, the reason on stderr", code == 2 and out == "" and "rev-parse --show-toplevel failed" in err)
code, out, err = run(repo, "--base", "no-such-ref")
check("a base that is not a commit: the INFO line names it, exit 0", code == 0 and err == ""
      and out == "INFO - no-such-ref is not a commit in this clone: nothing to diff\n")
git(plain, "init", "-q")
(plain / "skills").mkdir()
(plain / "skills" / "SKILL.md").write_bytes(b"staged, never committed\n")
git(plain, "add", "-A")
git(plain, "fetch", "-q", str(repo), "master:refs/remotes/origin/master")
code, out, err = run(plain)
check("a staged tree with a fetched base and no commit: the INFO line names HEAD, exit 0", code == 0 and err == ""
      and out == "INFO - HEAD is not a commit in this clone: nothing to diff\n")

print("the on-invoke estimate from a claude stand-in")
bindir = TMP / "bin"
bindir.mkdir()
stub = bindir / "stub.py"
stub.write_text("import json, os, sys\n"
                "open(os.environ['GROWTH_STUB_ARGV'], 'w').write(json.dumps(sys.argv[1:]))\n"
                "sys.stdout.write(open(os.environ['GROWTH_STUB_TABLE']).read())\n"
                "sys.exit(int(os.environ.get('GROWTH_STUB_EXIT', '0')))\n", encoding="utf-8")
if os.name == "nt":
    (bindir / "claude.cmd").write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
else:
    (bindir / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
    (bindir / "claude").chmod(0o755)
table = TMP / "table.txt"
table.write_text("fx 0.1.0\n  Description: fixture\n\nPer-component (rounded)\n"
                 "  component       always-on  on-invoke\n"
                 "  m                    ~310      ~1.8k\n"
                 "  n                    ~200     ~12.2k\n"
                 "  verifier             ~100        ~2k\n\n"
                 "  On-invoke cost is paid each time a skill or agent fires.\n", encoding="utf-8")
argv_file = TMP / "argv.json"
stub_env = {**ENV, "PATH": str(bindir) + os.pathsep + ENV["PATH"],
            "GROWTH_STUB_ARGV": str(argv_file), "GROWTH_STUB_TABLE": str(table)}
fx = TMP / "fx"
fx.mkdir()
git(fx, "init", "-q", "-b", "master")
one = commit(fx, "one", {".claude-plugin/plugin.json": '{"name": "fx"}\n',
                         "skills/m/SKILL.md": FM + "a\n", "skills/n/SKILL.md": FM + "a\n"})
commit(fx, "two", {"skills/n/SKILL.md": FM + "a\nb\n", "agents/q.md": FM + "agent\n"})
code, out, err = run(fx, "--base", one, env=stub_env)
check("claude is called with --plugin-dir <root> plugin details <plugin.json name>",
      json.loads(argv_file.read_text()) == ["--plugin-dir", git(fx, "rev-parse", "--show-toplevel"), "plugin", "details", "fx"])
check("the changed skill's estimate is the table's on-invoke column", code == 0 and "  n: ~12.2k\n" in out)
check("an unchanged skill is not listed", "  m:" not in out)
check("a changed agent missing from the table says so", "  q: not in the table\n" in out)
check("the base's estimate is declared not read", "the base's estimate needs a checkout of the base and is not read" in out)

print("claude failing is one line, not a failed report")
argv_file.unlink()
code, out, err = run(fx, "--base", one, env={**stub_env, "GROWTH_STUB_EXIT": "1"})
check("claude failing: exit 0, the numbers printed, the estimate not", code == 0 and "  skills/n/SKILL.md: 1 -> 2 (+1)\n" in out
      and "on-invoke tokens: not read, claude plugin details exited 1: " in out and "  n:" not in out)

print("claude off PATH is one line")
gitdir = os.path.dirname(shutil.which("git"))
check("the row's premise: git's directory holds no claude", shutil.which("claude", path=gitdir) is None)
code, out, err = run(fx, "--base", one, env={**ENV, "PATH": gitdir})
check("claude off PATH: exit 0, the numbers printed, the estimate not", code == 0 and "  skills/n/SKILL.md: 1 -> 2 (+1)\n" in out
      and "on-invoke tokens: not read, claude is not on PATH\n" in out)

print("a claude that cannot start is one line")
baddir = TMP / "bin-bad"
baddir.mkdir()
bad = baddir / ("claude.exe" if os.name == "nt" else "claude")
bad.write_bytes(b"not a program\n")
bad.chmod(0o755)
code, out, err = run(fx, "--base", one, env={**ENV, "PATH": str(baddir) + os.pathsep + ENV["PATH"]})
check("claude failing to start: exit 0, the numbers printed, the reason on one line", code == 0
      and "  skills/n/SKILL.md: 1 -> 2 (+1)\n" in out and "on-invoke tokens: not read, claude could not be started (" in out)

print("no plugin.json is one line, before claude is looked for")
argv_file.unlink(missing_ok=True)
code, out, err = run(repo, "--base", "master", env=stub_env)
check("no plugin.json: exit 0, the reason printed, claude not called", code == 0
      and "on-invoke tokens: not read, .claude-plugin/plugin.json names no plugin (" in out and not argv_file.exists())

print(f"skill-growth suite: {failures} failure(s)" if failures else "skill-growth suite: every row ok")
sys.exit(1 if failures else 0)
