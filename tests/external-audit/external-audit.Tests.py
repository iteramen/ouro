"""Unit test for bin/external-audit.py against a stub CLI on PATH.

The stub is a tiny launcher (named for whichever CLI it stands in for -- e.g. "grok" on POSIX,
"grok.cmd" on Windows, since Windows CreateProcess resolves an extensionless name only against
.exe/.com, never .cmd, when the caller has no extension of its own) that runs stub_impl.py: it
records its own argv, cwd and (when asked) stdin to a file, optionally sleeps without touching
stdin (to probe the stdin-feeder thread), optionally writes a file inside the fixture repo (to
probe the dirty-tree warning) or into the path its own argv names after "-o" (codex's verdict
file), then prints a canned reply and exits with a canned code. Every row drives the driver as a
real subprocess -- the point is what the driver puts on the CLI's argv and how it reads the CLI's
stdout, not the driver's Python internals.

Claims mode is used for most rows (no diff to construct, and no git call the driver makes to
build its subject), except that the driver's dirty-tree check runs unconditionally in both modes
(ported from the prior single-CLI reader, which never gated it on the mode either), so one shared
git-init'd fixture repo serves every claims-mode row, the dirty-tree row included -- most run the
driver FROM that repo (its own process cwd, not the --cwd flag) and leave --cwd unset, so the
driver's own default (the repository root of its invocation directory) resolves back to it; the
rows that exercise --cwd itself, and diff mode's own two-commit fixture, say so.

stdlib only, like bin/*.py. Run: python3 tests/external-audit/external-audit.Tests.py
"""
import atexit, json, os, pathlib, re, shutil, signal, stat, subprocess, sys, tempfile, time

# Two levels up is the plugin root (the driver under bin/) or, once vendored, the scripts dir
# itself, where the vendor copies it flat -- the way the apply-manifest suite resolves its subject.
BASE = pathlib.Path(__file__).resolve().parents[2]
DRIVER = next((p for p in (BASE / "external-audit.py", BASE / "bin" / "external-audit.py") if p.is_file()), None)
if DRIVER is None:
    raise SystemExit(f"external-audit.py not found under {BASE}")

PARENT_TMP = pathlib.Path(tempfile.mkdtemp(prefix="external-audit-test-run-"))


def _clear_readonly_and_retry(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_tmp():
    try:
        shutil.rmtree(PARENT_TMP, onerror=_clear_readonly_and_retry)
    except OSError as e:
        print(f"WARN: could not remove {PARENT_TMP}: {e}", file=sys.stderr)


atexit.register(_remove_tmp)

failures = 0


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


# --- the stub CLI -------------------------------------------------------------------------
# Reads STUB_* from its environment, which run_driver sets per call: STUB_RECORD_ARGV (required)
# is where it writes its own argv, cwd, pid and stdin, as a JSON object; STUB_RECORD_STDIN, if
# set, makes it read stdin and record that too -- opt-in, not unconditional: a stub launched for
# an adapter whose stdin_prompt is False inherits the *driver's* stdin rather than a pipe the
# driver feeds and closes, and reading unconditionally would block forever waiting for an EOF
# nothing sends. STUB_REPLY_FILE, if set, is printed to stdout verbatim; STUB_WRITE_FILE, if set,
# is a path the stub writes a line to, inside the fixture repo, to probe the dirty-tree warning;
# STUB_FORK_CHILD_RECORD, if set (POSIX only), forks a grandchild that outlives the stub unless
# the whole process GROUP is killed, and writes the grandchild's pid there -- distinguishes
# killing the tree from killing just the stub's own pid, which a stub that runs Python via exec
# (one process) cannot; STUB_SLEEP, if set, is seconds to sleep before any of that, without ever
# touching stdin unless STUB_RECORD_STDIN says to, to probe the stdin-feeder thread and the
# timeout kill; STUB_O_FILE_CONTENT, if set, is written into the path the stub's own argv names
# right after an "-o" element -- codex's adapter carries its verdict there, not on stdout, so a
# row that drives it must have the stub itself write that file, at whatever path the driver
# happened to pick, the way `codex exec -o <FILE>` would; STUB_EXIT is the exit code (default 0).
# STUB_STDERR, if set, is written to the stub's stderr, and STUB_STDERR_FILL, if set, is a count of
# filler lines written after it (an environment variable cannot carry a large stderr). The driver
# reads the stub's exit code only when no verdict parses: a nonzero exit with no reply is what "the
# CLI refused" looks like.
STUB_IMPL_SOURCE = '''
import json, os, sys
argv = sys.argv[1:]
stdin_text = sys.stdin.read() if os.environ.get("STUB_RECORD_STDIN") else None
with open(os.environ["STUB_RECORD_ARGV"], "w", encoding="utf-8") as f:
    json.dump({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(), "stdin": stdin_text}, f)
fork_record = os.environ.get("STUB_FORK_CHILD_RECORD")
if fork_record:
    child_pid = os.fork()
    if child_pid == 0:
        import time
        time.sleep(120)
        os._exit(0)
    with open(fork_record, "w", encoding="utf-8") as f:
        f.write(str(child_pid))
sleep_s = os.environ.get("STUB_SLEEP")
if sleep_s:
    import time
    time.sleep(float(sleep_s))
write_file = os.environ.get("STUB_WRITE_FILE")
if write_file:
    with open(write_file, "w", encoding="utf-8") as f:
        f.write("the stub wrote this\\n")
o_file_content = os.environ.get("STUB_O_FILE_CONTENT")
if o_file_content is not None and "-o" in argv:
    with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as f:
        f.write(o_file_content)
reply_file = os.environ.get("STUB_REPLY_FILE")
if reply_file:
    with open(reply_file, "r", encoding="utf-8") as f:
        sys.stdout.write(f.read())
stderr_text = os.environ.get("STUB_STDERR")
if stderr_text is not None:
    sys.stderr.write(stderr_text)
detached = os.environ.get("STUB_DETACHED_CHILD")
if detached:
    import subprocess
    kw = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(" + detached + ")"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, **kw)
stderr_fill = os.environ.get("STUB_STDERR_FILL")
if stderr_fill:
    sys.stderr.write("stderr filler line\\n" * int(stderr_fill))
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
'''


def make_stub(name="grok", reply_text=None, exit_code=0, write_file=None, sleep_s=None,
              record_stdin=False, o_file_content=None, stderr_text=None, stderr_fill=None, detached_child=None):
    """A fresh CLI stub named `name`, in its own directory. Returns (stub_dir, argv_record_path,
    env_extra). record_stdin makes the stub read and record its stdin too -- pass it only for a
    row whose adapter pipes the prompt (stdin_prompt True): a stub whose stdin is merely
    inherited, not fed and closed by the driver, must not be made to read() it, or a row for a
    non-stdin adapter would hang waiting for an EOF nothing sends. o_file_content, for codex's
    adapter, makes the stub write that text into the path its own argv names after "-o" --
    its verdict never lands on stdout (a JSONL event stream), reply_text does, for that mode.
    stderr_text makes the stub write that text to its stderr; stderr_fill, that many more lines;
    detached_child, a grandchild that inherits the stub's stderr and sleeps that many seconds past
    the stub's exit."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="stub-", dir=PARENT_TMP))
    impl = d / "stub_impl.py"
    impl.write_text(STUB_IMPL_SOURCE, encoding="utf-8")
    record = d / "argv.json"
    env_extra = {"STUB_RECORD_ARGV": str(record), "STUB_EXIT": str(exit_code)}
    if reply_text is not None:
        reply_path = d / "reply.txt"
        reply_path.write_text(reply_text, encoding="utf-8")
        env_extra["STUB_REPLY_FILE"] = str(reply_path)
    if write_file:
        env_extra["STUB_WRITE_FILE"] = str(write_file)
    if sleep_s:
        env_extra["STUB_SLEEP"] = str(sleep_s)
    if record_stdin:
        env_extra["STUB_RECORD_STDIN"] = "1"
    if o_file_content is not None:
        env_extra["STUB_O_FILE_CONTENT"] = o_file_content
    if stderr_text is not None:
        env_extra["STUB_STDERR"] = stderr_text
    if stderr_fill is not None:
        env_extra["STUB_STDERR_FILL"] = str(stderr_fill)
    if detached_child is not None:
        env_extra["STUB_DETACHED_CHILD"] = str(detached_child)
    if os.name == "nt":
        (d / f"{name}.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n', encoding="utf-8")
    else:
        launcher = d / name
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{impl}" "$@"\n', encoding="utf-8")
        os.chmod(launcher, 0o755)
    return d, record, env_extra


# The directory shutil.which resolves each real CLI this suite stubs to -- looked up once here,
# against the machine's real PATH, before any stub directory is ever prepended to it -- is
# dropped from every child's PATH: "never run a real external review CLI" holds even when a real
# install's directory does not contain the CLI's name at all (a real `claude` resolves to
# `claude.EXE` under `~/.local/bin` on this box, a path that does not name it), so a name
# substring cannot be the filter; the resolved directory can. realpath, not the raw PATH entry,
# on both sides of the comparison: the Windows CI account's TEMP is an 8.3 short path, and a path
# compared with its resolved form differs there. normcase folds case on Windows, a no-op on
# POSIX, so a differently-cased PATH entry naming the same directory still matches. This is the
# fleet's own guard, not the driver's: the driver trusts whatever PATH it is given, the way
# shutil.which does.
_STUBBED_CLIS = ("grok", "claude", "codex", "copilot")
_REAL_CLI_DIRS = {
    os.path.normcase(os.path.realpath(os.path.dirname(w)))
    for w in (shutil.which(name) for name in _STUBBED_CLIS) if w
}
SAFE_PATH = os.pathsep.join(
    p for p in os.environ.get("PATH", "").split(os.pathsep)
    if p and os.path.normcase(os.path.realpath(p)) not in _REAL_CLI_DIRS)

for _name in _STUBBED_CLIS:
    check(f"the suite never reaches a real CLI: shutil.which({_name!r}, path=SAFE_PATH) is None",
          shutil.which(_name, path=SAFE_PATH) is None)


def run_driver(args, stub_dir=None, env_extra=None, cwd=None):
    """python3 external-audit.py <args>, with stub_dir (if given) ahead of a PATH that never
    resolves a real external CLI. TEMP/TMP/TMPDIR point at this suite's own scratch parent, not
    the real machine TEMP, so a driver run under this suite never leaves an artifact folder
    there. Returns the completed subprocess: .returncode, .stdout, .stderr."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PATH"] = (str(stub_dir) + os.pathsep + SAFE_PATH) if stub_dir is not None else SAFE_PATH
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(DRIVER), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd, env=env, timeout=60)


def read_argv(record_path):
    # A missing record means the stub never ran (the driver refused, or timed/killed it first);
    # [] fails the row's own check() cleanly rather than crashing the rest of this script with an
    # uncaught FileNotFoundError and losing every check after it.
    if not record_path.exists():
        return []
    return json.loads(record_path.read_text(encoding="utf-8"))["argv"]


def read_cwd(record_path):
    if not record_path.exists():
        return None
    return json.loads(record_path.read_text(encoding="utf-8"))["cwd"]


def read_stdin(record_path):
    if not record_path.exists():
        return None
    return json.loads(record_path.read_text(encoding="utf-8")).get("stdin")


def session_of(stdout_text):
    m = re.search(r"session (\S+) round", stdout_text)
    return m.group(1) if m else None


def _pid_alive(pid):
    """POSIX only. signal 0 sends nothing, just probes whether the pid still exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else -- not expected for our own descendants
    return True


def _wait_until(pred, timeout=5.0, interval=0.1):
    deadline = time.time() + timeout
    ok = pred()
    while not ok and time.time() < deadline:
        time.sleep(interval)
        ok = pred()
    return ok


# One repo for every claims-mode row: the dirty-tree check runs whatever the mode, and claims
# mode makes no git call of its own, so a plain git-init'd directory is enough.
REPO = pathlib.Path(tempfile.mkdtemp(prefix="external-audit-repo-", dir=PARENT_TMP))
subprocess.run(["git", "init", "-q"], cwd=REPO, capture_output=True, check=True)
CLAIMS = REPO / "claims.md"
CLAIMS.write_text("1. Claim one, cites external-audit.py:1.\n", encoding="utf-8")
DIGEST = REPO / "digest.md"
DIGEST.write_text("finding-1 (minor) - x\n  ACCEPTED -> fixed | REJECTED -> \n", encoding="utf-8")

BASE_ARGS = ["--claims", str(CLAIMS), "--goal", "prove the stub CLI is driven correctly", "--cli", "grok"]

APPROVE_REPLY = json.dumps({"verdict": "APPROVE", "files_read": ["external-audit.py"], "claims": [
    {"id": "1", "status": "CONFIRMED", "evidence": "external-audit.py:1", "note": "ok"}]})
REVISE_REPLY = json.dumps({"verdict": "REVISE", "files_read": ["external-audit.py"], "claims": [
    {"id": "1", "status": "REFUTED", "evidence": "external-audit.py:1", "note": "no"}]})
UNFALSIFIABLE_REPLY = json.dumps({"verdict": "APPROVE", "files_read": [], "claims": [
    {"id": "1", "status": "CONFIRMED", "evidence": "", "note": ""}]})
NO_CLAIMS_REPLY = json.dumps({"verdict": "APPROVE", "files_read": ["external-audit.py"], "claims": []})

# A two-commit repo for diff mode's own rows (--range HEAD~1..HEAD), untested until now: claims
# mode never builds a diff, so REPO above (no commits) cannot stand in for it.
DIFF_REPO = pathlib.Path(tempfile.mkdtemp(prefix="external-audit-diffrepo-", dir=PARENT_TMP))
subprocess.run(["git", "init", "-q"], cwd=DIFF_REPO, capture_output=True, check=True)
_git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  cwd=DIFF_REPO, capture_output=True, check=True)
(DIFF_REPO / "file.txt").write_text("a\n", encoding="utf-8")
_git("add", "-A"); _git("commit", "-q", "-m", "one")
(DIFF_REPO / "file.txt").write_text("b\n", encoding="utf-8")
_git("add", "-A"); _git("commit", "-q", "-m", "two")

DIFF_ARGS = ["--range", "HEAD~1..HEAD", "--goal", "prove diff mode is driven correctly", "--cli", "grok"]
DIFF_APPROVE_EMPTY = json.dumps({"verdict": "APPROVE", "files_read": [], "critiques": []})
DIFF_REVISE = json.dumps({"verdict": "REVISE", "files_read": ["file.txt"], "critiques": [
    {"id": "finding-1", "severity": "minor", "lens": "x", "claim": "y", "evidence": "file.txt:1"}]})

# The Grok adapter's exact argv shape (bin/external-audit.py's GrokAdapter.DISALLOWED): asserted
# here instead of by a substring-absence check on the goal text, which a Windows .cmd launcher's
# %* forwarding (cut at the first embedded newline) can pass even when the whole multi-line
# prompt leaked onto argv -- the leaked fragment before the first newline may not contain the
# goal text either, so "the goal text is absent from argv" is not evidence the prompt is.
GROK_DISALLOWED = "write,search_replace,run_terminal_command,spawn_subagent"


def expected_grok_argv(prompt_path, session, resume=False, model=None):
    a = ["--prompt-file", str(prompt_path), ("-r" if resume else "-s"), session,
         "--disallowed-tools", GROK_DISALLOWED]
    if model and model != "default":
        a += ["-m", model]
    return a

# --- the stub launcher finds python3 by its own absolute path, not PATH: SAFE_PATH drops any
# directory holding a stubbed real CLI, so a box where python3 shares that directory loses it from
# every child's PATH too. PATH here drops every directory that resolves python3, to prove the
# launcher does not need it on PATH at all. On a box where that directory also holds git (e.g. a
# Linux box where both sit in /usr/bin), the dirty-tree check still needs git, so a fresh scratch
# directory holding only a symlink to it is put back on PATH; where the symlink is refused (no
# privilege for it on Windows, say), the row is skipped with a visible line naming why. Linux always
# grants the symlink, so a skip there is a broken probe and fails the row.
PATH_NO_PYTHON3 = os.pathsep.join(
    p for p in SAFE_PATH.split(os.pathsep) if p and not shutil.which("python3", path=p))
_skip_no_python3_reason = None
if shutil.which("git", path=PATH_NO_PYTHON3) is None:
    _real_git = shutil.which("git")
    if _real_git is None:
        _skip_no_python3_reason = "no git resolves on this machine's own PATH"
    else:
        _git_only = pathlib.Path(tempfile.mkdtemp(prefix="git-only-", dir=PARENT_TMP))
        try:
            os.symlink(_real_git, _git_only / pathlib.Path(_real_git).name)
            PATH_NO_PYTHON3 = str(_git_only) + os.pathsep + PATH_NO_PYTHON3
        except OSError as e:
            _skip_no_python3_reason = f"git shares python3's directory and symlinking it failed: {e}"
if _skip_no_python3_reason and sys.platform.startswith("linux"):
    check(f"the stub launcher still runs when no directory on PATH resolves python3 (skipped on Linux: {_skip_no_python3_reason})",
          False)
elif _skip_no_python3_reason:
    print(f"  skip: the stub launcher still runs when no directory on PATH resolves python3 ({_skip_no_python3_reason})")
else:
    stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
    r = run_driver(BASE_ARGS, stub_dir, {**env_extra, "PATH": str(stub_dir) + os.pathsep + PATH_NO_PYTHON3}, cwd=REPO)
    check("the stub launcher still runs when no directory on PATH resolves python3",
          r.returncode == 0)

# --- exit codes on the four verdict shapes -------------------------------------------------
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("an APPROVE with files_read exits 0", r.returncode == 0)

stub_dir, record, env_extra = make_stub(reply_text=REVISE_REPLY, exit_code=0)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a REVISE exits 2", r.returncode == 2)

stub_dir, record, env_extra = make_stub(reply_text="not a json verdict at all", exit_code=0)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("no JSON verdict exits 1", r.returncode == 1)
check("exit 0 with no verdict keeps the no-JSON-verdict message",
      "no JSON verdict from grok (see stdout.txt)" in r.stderr and "exited" not in r.stderr)

STDERR_LINES = ["HTTP 402 usage balance exhausted"] + [f"diagnostic line {i}" for i in range(2, 8)]
stub_dir, record, env_extra = make_stub(exit_code=7, stderr_text="\n".join(STDERR_LINES) + "\n")
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a CLI that exits non-zero with no verdict: the driver names the exit code, the last five stderr lines and stderr.txt",
      r.returncode == 1 and "grok exited 7" in r.stderr and "diagnostic line 7" in r.stderr
      and "402 usage balance exhausted" not in r.stderr and "stderr.txt" in r.stderr)
check("a CLI that exits non-zero with no verdict: the driver does not say no JSON verdict",
      "no JSON verdict" not in r.stderr)
m = re.search(r"all of it in (.+?stderr\.txt)\)", r.stderr)
check("a CLI that exits non-zero with stderr leaves stderr.txt holding all of it",
      bool(m) and pathlib.Path(m.group(1)).is_file()
      and pathlib.Path(m.group(1)).read_text(encoding="utf-8").splitlines() == STDERR_LINES)

stub_dir, record, env_extra = make_stub(exit_code=3)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a CLI that exits non-zero with empty stderr says so and names stdout.txt",
      r.returncode == 1 and "grok exited 3" in r.stderr and "stderr was empty" in r.stderr
      and "stdout.txt" in r.stderr and "no JSON verdict" not in r.stderr)
m = re.search(r"\(see (.+?)stdout\.txt\)", r.stderr)
check("and leaves no stderr.txt",
      bool(m) and pathlib.Path(m.group(1) + "stdout.txt").is_file() and not pathlib.Path(m.group(1) + "stderr.txt").exists())

# a descendant still holding the stderr file open (on Windows, where this can go red: the file cannot
# be removed while it is open) must not crash the empty-stderr cleanup
stub_dir, record, env_extra = make_stub(exit_code=1, detached_child=4)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a CLI whose detached descendant holds stderr open, exiting 1 with stderr empty, says so without a traceback",
      r.returncode == 1 and "grok exited 1" in r.stderr and "stderr was empty" in r.stderr
      and "Traceback" not in r.stderr)

# a stderr past any pipe's buffer, exit 2: a stderr nothing reads until the CLI exits deadlocks
# the round into the timeout instead
stub_dir, record, env_extra = make_stub(exit_code=2, stderr_text="HTTP 402 usage balance exhausted\n", stderr_fill=12000)
r = run_driver(BASE_ARGS + ["--timeout", "4"], stub_dir, env_extra, cwd=REPO)
check("a CLI that writes 200 KB to stderr and exits 2 is reported as exited 2, not timed out",
      r.returncode == 1 and "grok exited 2" in r.stderr and "timed out" not in r.stderr)

stub_dir, record, env_extra = make_stub(reply_text=UNFALSIFIABLE_REPLY, exit_code=0)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("an APPROVE with no critique and an empty files_read exits 3", r.returncode == 3)

# --- claims mode: an empty claims list prints "(no claims returned)", not a bare "  " --------
stub_dir, record, env_extra = make_stub(reply_text=NO_CLAIMS_REPLY, exit_code=0)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("an empty claims list prints the (no claims returned) marker",
      r.returncode == 0 and "(no claims returned)" in r.stdout)

# --- diff mode, --range HEAD~1..HEAD: untested until now -----------------------------------
stub_dir, record, env_extra = make_stub(reply_text=DIFF_APPROVE_EMPTY, exit_code=0)
r = run_driver(DIFF_ARGS, stub_dir, env_extra, cwd=DIFF_REPO)
check("diff mode: an APPROVE with empty files_read exits 3", r.returncode == 3)

stub_dir, record, env_extra = make_stub(reply_text=DIFF_REVISE, exit_code=0)
r = run_driver(DIFF_ARGS, stub_dir, env_extra, cwd=DIFF_REPO)
check("diff mode: a REVISE exits 2", r.returncode == 2)

# --- the dirty-tree warning ------------------------------------------------------------
written = REPO / "stub-wrote-this.txt"
if written.exists():
    written.unlink()
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0, write_file=written)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a stub that writes a file gets the warning naming it",
      "WARNING" in r.stderr and "stub-wrote-this.txt" in r.stderr)
if written.exists():
    written.unlink()

# --- round 1's argv is the exact Grok shape, the prompt travels only by file ---------------
# Not a substring-absence check on the goal text: on Windows, a .cmd launcher's %* forwarding
# cuts at the first embedded newline, so a regression that put the whole multi-line prompt on
# argv could still leave the (later-appearing) goal text out of what the stub ever sees --
# passing this row even though the bug is real. An exact-shape check has no such blind spot: any
# extra argv element, truncated or not, fails it.
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
r1 = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
session1 = session_of(r1.stdout)
check("round 1 mints and prints a session id", bool(session1))
argv1 = read_argv(record)
prompt_path = argv1[argv1.index("--prompt-file") + 1] if "--prompt-file" in argv1 else None
check("round 1's argv is exactly --prompt-file <path> -s <id> --disallowed-tools <list>",
      prompt_path is not None and session1 is not None and
      argv1 == expected_grok_argv(prompt_path, session1))
check("and the prompt file (not argv) holds the goal",
      prompt_path is not None and
      pathlib.Path(prompt_path).read_text(encoding="utf-8").count("prove the stub CLI is driven correctly") == 1)

# --- round 2 passes -r and the round-1 id, same exact shape ---------------------------------
stub_dir2, record2, env_extra2 = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
r2 = run_driver(BASE_ARGS + ["--resume", session1 or "missing", "--digest", str(DIGEST)],
                stub_dir2, env_extra2, cwd=REPO)
argv2 = read_argv(record2)
prompt_path2 = argv2[argv2.index("--prompt-file") + 1] if "--prompt-file" in argv2 else None
check("round 2's argv is exactly --prompt-file <path> -r <round-1 id> --disallowed-tools <list>",
      prompt_path2 is not None and
      argv2 == expected_grok_argv(prompt_path2, session1, resume=True))

# --- ClaudeAdapter: stdin transport, the safe-mode flag shape, the result-object verdict source -
# The stub's argv never carries "claude" itself (STUB_IMPL_SOURCE records sys.argv[1:]), so the
# expected shape below has none either -- the same convention expected_grok_argv already uses.
CLAUDE_TOOLS = "Read,Grep,Glob"
CLAUDE_FORBIDDEN = {"bypassPermissions", "--dangerously-skip-permissions",
                    "--allow-dangerously-skip-permissions", "--fork-session",
                    "--no-session-persistence"}


def expected_claude_argv(session, resume=False, model=None):
    a = ["-p", "--output-format", "json", ("--resume" if resume else "--session-id"), session,
         "--tools", CLAUDE_TOOLS, "--permission-mode", "dontAsk", "--permission-prompts", "none",
         "--safe-mode"]
    if model and model != "default":
        a += ["--model", model]
    return a


def claude_envelope(result_text, is_error=False, subtype="success"):
    """The one JSON object `claude -p --output-format json` prints: the reply sits in `result`,
    a string that may itself hold the verdict JSON the driver's parse_verdict() reads next."""
    return json.dumps({"type": "result", "subtype": subtype, "is_error": is_error,
                        "result": result_text, "session_id": "unused-claude-mints-none"})


CLAUDE_INNER_APPROVE = json.dumps({"verdict": "APPROVE", "files_read": ["external-audit.py"], "claims": [
    {"id": "1", "status": "CONFIRMED", "evidence": "external-audit.py:1", "note": "ok"}]})
CLAUDE_APPROVE_ENVELOPE = claude_envelope(CLAUDE_INNER_APPROVE)
CLAUDE_NO_VERDICT_ENVELOPE = claude_envelope("I looked around but found nothing to report.")
# is_error true with a result that LOOKS like a valid verdict -- proves is_error, not an absent
# or malformed result, is what makes this exit 1: a result field alone cannot tell the two apart.
CLAUDE_ERROR_ENVELOPE = claude_envelope(CLAUDE_INNER_APPROVE, is_error=True, subtype="error_max_turns")

CLAUDE_BASE_ARGS = ["--claims", str(CLAIMS), "--goal", "prove the stub CLI is driven correctly", "--cli", "claude"]

stub_dir, record, env_extra = make_stub(name="claude", reply_text=CLAUDE_APPROVE_ENVELOPE, exit_code=0, record_stdin=True)
r1c = run_driver(CLAUDE_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("claude round 1 exits 0 on a canned APPROVE inside result", r1c.returncode == 0)
session1c = session_of(r1c.stdout)
check("claude round 1 mints and prints a session id", bool(session1c))
argv1c = read_argv(record)
check("claude round 1's argv is exactly the --safe-mode shape",
      session1c is not None and argv1c == expected_claude_argv(session1c))
check("and none of the forbidden flags are on argv", not (CLAUDE_FORBIDDEN & set(argv1c)))
stdin1c = read_stdin(record)
check("the prompt arrives on stdin, not on argv",
      stdin1c is not None and "prove the stub CLI is driven correctly" in stdin1c and
      not any("prove the stub CLI is driven correctly" in a for a in argv1c))

# --- claude round 2: --resume in place of --session-id, same exact shape -------------------
stub_dir2, record2, env_extra2 = make_stub(name="claude", reply_text=CLAUDE_APPROVE_ENVELOPE, exit_code=0)
run_driver(CLAUDE_BASE_ARGS + ["--resume", session1c or "missing", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2c = read_argv(record2)
check("claude round 2's argv holds --resume and round 1's id, same exact shape",
      argv2c == expected_claude_argv(session1c, resume=True))

# --- where the verdict is read from: the `result` field, not stdout whole ------------------
stub_dir, record, env_extra = make_stub(name="claude", reply_text=CLAUDE_NO_VERDICT_ENVELOPE, exit_code=0)
r = run_driver(CLAUDE_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a result object whose result holds no verdict exits 1", r.returncode == 1)

stub_dir, record, env_extra = make_stub(name="claude", reply_text=CLAUDE_ERROR_ENVELOPE, exit_code=0)
r = run_driver(CLAUDE_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("an error result (is_error true, subtype not success) exits 1", r.returncode == 1)

# --- the model: --model default puts no --model on argv, a named one puts it on both rounds -
stub_dir, record, env_extra = make_stub(name="claude", reply_text=CLAUDE_APPROVE_ENVELOPE, exit_code=0)
run_driver(CLAUDE_BASE_ARGS + ["--model", "default"], stub_dir, env_extra, cwd=REPO)
check("claude --model default puts no --model on the argv", "--model" not in read_argv(record))

stub_dir, record, env_extra = make_stub(name="claude", reply_text=CLAUDE_APPROVE_ENVELOPE, exit_code=0)
r1m = run_driver(CLAUDE_BASE_ARGS + ["--model", "sonnet"], stub_dir, env_extra, cwd=REPO)
session1m = session_of(r1m.stdout)
argv1m = read_argv(record)
check("claude --model sonnet puts --model sonnet on round 1's argv, exact shape",
      session1m is not None and argv1m == expected_claude_argv(session1m, model="sonnet"))
stub_dir2, record2, env_extra2 = make_stub(name="claude", reply_text=CLAUDE_APPROVE_ENVELOPE, exit_code=0)
run_driver(CLAUDE_BASE_ARGS + ["--model", "sonnet", "--resume", session1m or "missing", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2m = read_argv(record2)
check("and on the resume round's argv too",
      argv2m == expected_claude_argv(session1m, resume=True, model="sonnet"))

# --- a claude stub that refuses the model: nonzero exit, no verdict, driver exits 1 --------
stub_dir, record, env_extra = make_stub(name="claude", reply_text=None, exit_code=1)
r = run_driver(CLAUDE_BASE_ARGS + ["--model", "no-such-model"], stub_dir, env_extra, cwd=REPO)
check("a claude stub that refuses the model (nonzero exit, no verdict) makes the driver exit 1",
      r.returncode == 1)
check("and the model still reached the argv", "--model" in read_argv(record) and "no-such-model" in read_argv(record))

# --- CodexAdapter: stdin transport, the fresh/resume argv shapes (no session flag on round 1,
# codex has none; -c sandbox_mode=read-only, the exact unquoted token, in place of -s on resume),
# the -o file as the verdict source (never stdout, a JSONL event stream), session_from_output
# reading codex's own thread id back from that stream (skipping a non-JSON log line), the
# triage-template heading naming that reported id rather than the driver-minted one, and the
# stale -o guard. codex mints no session of its own on round 1's argv, so -- unlike grok and
# claude -- round 1 and the resume round have genuinely different shapes, not the same shape with
# a flag swapped; expected_codex_argv below takes resume_id=None for the fresh-session shape.
CODEX_OUTPUT_FILENAME = "codex-last-message.txt"


def expected_codex_argv(out_file, resume_id=None, model=None):
    if resume_id:
        a = ["exec", "resume", resume_id, "-c", "sandbox_mode=read-only"]
    else:
        a = ["exec", "-s", "read-only"]
    a += ["--skip-git-repo-check", "--json", "-o", str(out_file)]
    if model and model != "default":
        a += ["-m", model]
    a.append("-")
    return a


CODEX_INNER_APPROVE = json.dumps({"verdict": "APPROVE", "files_read": ["external-audit.py"], "claims": [
    {"id": "1", "status": "CONFIRMED", "evidence": "external-audit.py:1", "note": "ok"}]})
CODEX_INNER_REVISE = json.dumps({"verdict": "REVISE", "files_read": ["external-audit.py"], "claims": [
    {"id": "1", "status": "REFUTED", "evidence": "external-audit.py:1", "note": "no"}]})
# A non-JSON line (Codex's own log lines interleave with its JSONL events) before the first real
# event -- proves session_from_output skips it rather than failing the round over it.
CODEX_STREAM = "\n".join([
    "ERROR codex_core::tools::router: a log line, not an event",
    json.dumps({"type": "thread.started", "thread_id": "thread-abc-123"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "turn.completed"}),
])
CODEX_STREAM_REVISE = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "thread-revise-99"}),
    json.dumps({"type": "turn.completed"}),
])
CODEX_BASE_ARGS = ["--claims", str(CLAIMS), "--goal", "prove the stub CLI is driven correctly", "--cli", "codex"]

stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0,
                                         record_stdin=True, o_file_content=CODEX_INNER_APPROVE)
r1x = run_driver(CODEX_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("codex round 1 exits 0 on a canned APPROVE in the -o file", r1x.returncode == 0)
driver_session1x = session_of(r1x.stdout)
check("round 1's driver-minted id is still announced up front, same as any adapter", bool(driver_session1x))
argv1x = read_argv(record)
out_file1 = argv1x[argv1x.index("-o") + 1] if "-o" in argv1x else None
check("codex round 1's argv is exactly the fresh-session shape -- no session flag at all",
      out_file1 is not None and argv1x == expected_codex_argv(out_file1) and
      pathlib.Path(out_file1).name == CODEX_OUTPUT_FILENAME)
stdin1x = read_stdin(record)
check("the prompt arrives on stdin, not argv",
      stdin1x is not None and "prove the stub CLI is driven correctly" in stdin1x and
      not any("prove the stub CLI is driven correctly" in a for a in argv1x))
m = re.search(r"\(resume with: --cli codex --resume (\S+) --digest", r1x.stdout)
check("codex's own reported thread id, not the driver-minted uuid, appears on the resume-with line",
      bool(m) and m.group(1) == "thread-abc-123" and m.group(1) != driver_session1x)

# --- codex round 2: resume + the unquoted sandbox_mode token, no -s, same -o/--json/--skip-git- --
stub_dir2, record2, env_extra2 = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0,
                                            o_file_content=CODEX_INNER_APPROVE)
run_driver(CODEX_BASE_ARGS + ["--resume", "thread-abc-123", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2x = read_argv(record2)
out_file2 = argv2x[argv2x.index("-o") + 1] if "-o" in argv2x else None
check("codex round 2's argv is exactly the resume shape, no -s/--sandbox at all",
      out_file2 is not None and argv2x == expected_codex_argv(out_file2, resume_id="thread-abc-123") and
      "-s" not in argv2x)
check("the sandbox_mode value is the exact unquoted token sandbox_mode=read-only",
      "-c" in argv2x and argv2x[argv2x.index("-c") + 1] == "sandbox_mode=read-only")

# --- the triage-template heading names codex's own reported id, not the driver-minted uuid ------
stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM_REVISE, exit_code=0,
                                         o_file_content=CODEX_INNER_REVISE)
rXr = run_driver(CODEX_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("codex REVISE exits 2", rXr.returncode == 2)
driver_sessionXr = session_of(rXr.stdout)
tmpl_m = re.search(r"triage template: (.+)", rXr.stdout)
check("a triage template is written for the REVISE", bool(tmpl_m))
heading = pathlib.Path(tmpl_m.group(1).strip()).read_text(encoding="utf-8").splitlines()[0] if tmpl_m else ""
check("the triage-template heading names codex's reported thread id",
      "thread-revise-99" in heading)
check("and not the driver-minted uuid",
      not driver_sessionXr or driver_sessionXr not in heading)

# --- diff mode's own triage-template heading (main()'s critiques branch, not the claims branch
# the row above exercises) also names codex's reported id, not the driver-minted uuid ------------
CODEX_DIFF_ARGS = ["--range", "HEAD~1..HEAD", "--goal", "prove diff mode is driven correctly", "--cli", "codex"]
stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM_REVISE, exit_code=0,
                                         o_file_content=DIFF_REVISE)
rXd = run_driver(CODEX_DIFF_ARGS, stub_dir, env_extra, cwd=DIFF_REPO)
check("codex diff-mode REVISE exits 2", rXd.returncode == 2)
tmpl_dm = re.search(r"triage template: (.+)", rXd.stdout)
check("a triage template is written for the diff-mode REVISE", bool(tmpl_dm))
heading_dm = pathlib.Path(tmpl_dm.group(1).strip()).read_text(encoding="utf-8").splitlines()[0] if tmpl_dm else ""
check("the diff-mode triage-template heading names codex's reported thread id, not the minted uuid",
      "thread-revise-99" in heading_dm)

# --- a missing -o file (codex produced events but never wrote one) exits 1 ----------------------
stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0)  # no o_file_content
r = run_driver(CODEX_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("codex with no -o file at all exits 1 even though stdout parses as a JSONL stream",
      r.returncode == 1)

# --- a missing -o file still exits 1 even when stdout alone is a verdict-shaped JSON object -------
stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_INNER_APPROVE, exit_code=0)  # no o_file_content
r = run_driver(CODEX_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("codex with no -o file exits 1 even when stdout alone is a verdict-shaped JSON object",
      r.returncode == 1)

# --- the model: default puts no -m on either round's argv, a named one puts it on both ----------
stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0, o_file_content=CODEX_INNER_APPROVE)
run_driver(CODEX_BASE_ARGS + ["--model", "default"], stub_dir, env_extra, cwd=REPO)
check("codex --model default puts no -m on the argv", "-m" not in read_argv(record))

stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0, o_file_content=CODEX_INNER_APPROVE)
run_driver(CODEX_BASE_ARGS + ["--model", "gpt-5-codex"], stub_dir, env_extra, cwd=REPO)
argv1cm = read_argv(record)
out_file_m = argv1cm[argv1cm.index("-o") + 1] if "-o" in argv1cm else None
check("codex --model <name> puts -m <name> on round 1's argv, exact shape",
      out_file_m is not None and argv1cm == expected_codex_argv(out_file_m, model="gpt-5-codex"))
stub_dir2, record2, env_extra2 = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0, o_file_content=CODEX_INNER_APPROVE)
run_driver(CODEX_BASE_ARGS + ["--model", "gpt-5-codex", "--resume", "thread-abc-123", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2cm = read_argv(record2)
out_file_m2 = argv2cm[argv2cm.index("-o") + 1] if "-o" in argv2cm else None
check("and on the resume round's argv too",
      out_file_m2 is not None and
      argv2cm == expected_codex_argv(out_file_m2, resume_id="thread-abc-123", model="gpt-5-codex"))

# --- a codex stub that refuses the model: nonzero exit, no -o file, driver exits 1 --------------
stub_dir, record, env_extra = make_stub(name="codex", reply_text=None, exit_code=1)
r = run_driver(CODEX_BASE_ARGS + ["--model", "no-such-model"], stub_dir, env_extra, cwd=REPO)
check("a codex stub that refuses the model (nonzero exit, no -o file) makes the driver exit 1",
      r.returncode == 1)
check("and the model still reached the argv", "-m" in read_argv(record) and "no-such-model" in read_argv(record))

# --- the stale -o guard: a re-run of round 2 with the same --resume id reuses round-2/'s folder
# (main()'s out.mkdir(parents=True, exist_ok=True)); an -o file left there by an earlier attempt
# must not be mistaken for this attempt's answer. The fresh run's own -o file is backdated well
# before this round's prompt.txt (always rewritten on every call) so the comparison cannot land on
# a filesystem timestamp tie, whatever its resolution. -------------------------------------------
stub_dir, record, env_extra = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0,
                                         o_file_content=CODEX_INNER_APPROVE)
r_fresh = run_driver(CODEX_BASE_ARGS + ["--resume", "thread-stale-test", "--digest", str(DIGEST)],
                      stub_dir, env_extra, cwd=REPO)
check("codex resume round with a fresh -o file exits 0", r_fresh.returncode == 0)
folder_m = re.search(r"-> (.+)$", r_fresh.stdout, re.M)
check("the round folder path is printed", bool(folder_m))
round_dir = pathlib.Path(folder_m.group(1).strip()) if folder_m else None
stale_file = round_dir / CODEX_OUTPUT_FILENAME if round_dir else None
check("the -o file landed in that round folder", bool(stale_file) and stale_file.is_file())
if stale_file and stale_file.is_file():
    long_ago = time.time() - 3600
    os.utime(stale_file, (long_ago, long_ago))
stub_dir2, record2, env_extra2 = make_stub(name="codex", reply_text=CODEX_STREAM, exit_code=0)  # no o_file_content: writes nothing new
r_stale = run_driver(CODEX_BASE_ARGS + ["--resume", "thread-stale-test", "--digest", str(DIGEST)],
                      stub_dir2, env_extra2, cwd=REPO)
check("a re-run that leaves the -o file stale (older than this round's prompt.txt) is treated as "
      "no verdict, not the leftover APPROVE",
      r_stale.returncode == 1)

# --- CopilotAdapter: stdin transport, the --deny-tool permission-list shape (a permission list,
# not a sandbox (codex) or a tool set with no write tool in it (claude)), the default stdout
# verdict source (no override, same as grok). The stub's argv
# never carries "copilot" itself (STUB_IMPL_SOURCE records sys.argv[1:]), so the expected shape
# below has none either, the same convention the other adapters' expected_*_argv already use.
COPILOT_FORBIDDEN = {"--allow-tool", "--enable-all-github-mcp-tools", "--allow-all-tools",
                      "--allow-all", "--yolo", "--allow-all-paths", "-p", "--disable-builtin-mcps"}


def expected_copilot_argv(session, resume=False, model=None):
    a = ["-s", "--no-ask-user", ("--resume" if resume else "--session-id"), session,
         "--deny-tool=write", "--deny-tool=shell"]
    if model and model != "default":
        a += ["--model", model]
    return a


COPILOT_INNER_APPROVE = json.dumps({"verdict": "APPROVE", "files_read": ["external-audit.py"], "claims": [
    {"id": "1", "status": "CONFIRMED", "evidence": "external-audit.py:1", "note": "ok"}]})
COPILOT_BASE_ARGS = ["--claims", str(CLAIMS), "--goal", "prove the stub CLI is driven correctly", "--cli", "copilot"]

stub_dir, record, env_extra = make_stub(name="copilot", reply_text=COPILOT_INNER_APPROVE, exit_code=0, record_stdin=True)
r1cp = run_driver(COPILOT_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("copilot round 1 exits 0 on a canned APPROVE on stdout", r1cp.returncode == 0)
session1cp = session_of(r1cp.stdout)
check("copilot round 1 mints and prints a session id", bool(session1cp))
argv1cp = read_argv(record)
check("copilot round 1's argv is exactly the --deny-tool shape",
      session1cp is not None and argv1cp == expected_copilot_argv(session1cp))
check("and none of the forbidden grant flags (or -p) are on argv",
      not (COPILOT_FORBIDDEN & set(argv1cp)))
stdin1cp = read_stdin(record)
check("the prompt arrives on stdin, not on argv",
      stdin1cp is not None and "prove the stub CLI is driven correctly" in stdin1cp and
      not any("prove the stub CLI is driven correctly" in a for a in argv1cp))

# --- copilot round 2: --resume in place of --session-id, same two denials -------------------
stub_dir2, record2, env_extra2 = make_stub(name="copilot", reply_text=COPILOT_INNER_APPROVE, exit_code=0)
run_driver(COPILOT_BASE_ARGS + ["--resume", session1cp or "missing", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2cp = read_argv(record2)
check("copilot round 2's argv holds --resume and round 1's id, same two denials",
      argv2cp == expected_copilot_argv(session1cp, resume=True))

# --- the model: --model default puts no --model on argv, a named one puts it on both rounds -
stub_dir, record, env_extra = make_stub(name="copilot", reply_text=COPILOT_INNER_APPROVE, exit_code=0)
run_driver(COPILOT_BASE_ARGS + ["--model", "default"], stub_dir, env_extra, cwd=REPO)
check("copilot --model default puts no --model on the argv", "--model" not in read_argv(record))

stub_dir, record, env_extra = make_stub(name="copilot", reply_text=COPILOT_INNER_APPROVE, exit_code=0)
r1cpm = run_driver(COPILOT_BASE_ARGS + ["--model", "gpt-5.4"], stub_dir, env_extra, cwd=REPO)
session1cpm = session_of(r1cpm.stdout)
argv1cpm = read_argv(record)
check("copilot --model gpt-5.4 puts --model gpt-5.4 on round 1's argv, exact shape",
      session1cpm is not None and argv1cpm == expected_copilot_argv(session1cpm, model="gpt-5.4"))
stub_dir2, record2, env_extra2 = make_stub(name="copilot", reply_text=COPILOT_INNER_APPROVE, exit_code=0)
run_driver(COPILOT_BASE_ARGS + ["--model", "gpt-5.4", "--resume", session1cpm or "missing", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2cpm = read_argv(record2)
check("and on the resume round's argv too",
      argv2cpm == expected_copilot_argv(session1cpm, resume=True, model="gpt-5.4"))

# --- a copilot stub that refuses the model: nonzero exit, no verdict, driver exits 1 ------------
stub_dir, record, env_extra = make_stub(name="copilot", reply_text=None, exit_code=1)
r = run_driver(COPILOT_BASE_ARGS + ["--model", "gpt-5"], stub_dir, env_extra, cwd=REPO)
check("a copilot stub that refuses the model (nonzero exit, no verdict) makes the driver exit 1",
      r.returncode == 1)
check("and the model still reached the argv", "--model" in read_argv(record) and "gpt-5" in read_argv(record))

# --- org policy denial: a stub that prints the policy-refusal text and exits 1, with no
# verdict on stdout, makes the driver exit 1 the same way an absent CLI or a refused model does -
stub_dir, record, env_extra = make_stub(name="copilot", reply_text="Access denied by policy settings", exit_code=1)
r = run_driver(COPILOT_BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("a stub that prints 'Access denied by policy settings' and exits 1 makes the driver exit 1",
      r.returncode == 1)

# --- an unknown --cli is refused, listing exactly the shipped adapter set (a set, not an
# ordered list: a further adapter widens this set in whatever order it is declared) -------------
r = run_driver(BASE_ARGS[:-2] + ["--cli", "bogus-cli"], stub_dir=None, cwd=REPO)
check("an unknown --cli is refused", r.returncode == 1)
m = re.search(r"adapters: ([^\r\n]*)", r.stderr)
check("and the refusal lists exactly the shipped adapter set",
      bool(m) and {a.strip() for a in m.group(1).split(",")} == {"grok", "claude", "codex", "copilot"})

# --- a missing CLI exits 1 -------------------------------------------------------------------
empty_path = pathlib.Path(tempfile.mkdtemp(prefix="empty-path-", dir=PARENT_TMP))
r = run_driver(BASE_ARGS, stub_dir=None, env_extra={"PATH": str(empty_path)}, cwd=REPO)
check("a missing CLI exits 1", r.returncode == 1 and "grok" in r.stderr)

# --- a missing --cli is a usage error, and exits 1 -- not argparse's own 2, which collides
# with REVISE -----------------------------------------------------------------------------
r = run_driver(["--goal", "g", "--claims", str(CLAIMS)], stub_dir=None, cwd=REPO)
check("a missing --cli (an argparse usage error) exits 1, not argparse's own 2",
      r.returncode == 1 and "--cli" in r.stderr)

# --- --dry-run needs no CLI installed: the which() check is skipped by its early return -----
r = run_driver(BASE_ARGS + ["--dry-run"], stub_dir=None, env_extra={"PATH": str(empty_path)}, cwd=REPO)
check("--dry-run prints the argv and exits 0 with no CLI on PATH at all",
      r.returncode == 0 and "argv: grok" in r.stdout)


# --- the per-user segment of the artifact folder: getpass reads the user from these variables
# first, so a --dry-run under each name prints the folder that name gets -----------------------
def artifact_segment(user):
    r = run_driver(BASE_ARGS + ["--dry-run"], env_extra={k: user for k in ("LOGNAME", "USER", "LNAME", "USERNAME")},
                   cwd=REPO)
    m = re.search(r"-> (.+)$", r.stdout, re.M)
    return pathlib.Path(m.group(1).strip()).parents[1].name if r.returncode == 0 and m else None


PATH_SAFE_SEGMENT = re.compile(r"external-audit-[A-Za-z0-9_.-]+")
check("a user name that needs no sanitizing keeps its own segment",
      artifact_segment("jane.doe") == "external-audit-jane.doe")
spaced = artifact_segment("CORP\\jane doe")
check(f"a user name holding a space and a domain separator yields a path-safe segment ({spaced})",
      spaced is not None and bool(PATH_SAFE_SEGMENT.fullmatch(spaced)))
jurgen, jorgen = artifact_segment("Jürgen"), artifact_segment("Jörgen")
check(f"two non-ASCII names of the same length yield different path-safe segments ({jurgen}, {jorgen})",
      jurgen is not None and jorgen is not None and jurgen != jorgen
      and all(PATH_SAFE_SEGMENT.fullmatch(s) for s in (jurgen, jorgen)))
jurgen_again = artifact_segment("Jürgen")
check(f"the same name yields the same segment on every run ({jurgen}, {jurgen_again})",
      jurgen is not None and jurgen == jurgen_again)

# --- a .cmd shim runs through cmd.exe, which reads &|<>^% in an unquoted argument as its own
# syntax, so such an argument is refused before the shim starts ------------------------------
if os.name == "nt":
    for ch in "&|<>^%":
        stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
        r = run_driver(BASE_ARGS + ["--model", f"a{ch}b"], stub_dir, env_extra, cwd=REPO)
        check(f"a .cmd shim given --model 'a{ch}b' is refused, naming the argument and the character, and never runs",
              r.returncode == 1 and repr(f"a{ch}b") in r.stderr and repr(ch) in r.stderr and not record.exists())
else:
    print("  skip: a .cmd or .bat shim resolves only on Windows, so the cmd.exe metacharacter refusal is untested here")

# --- --cwd points the CLI at the named repo even when run from elsewhere -------------------
elsewhere = pathlib.Path(tempfile.mkdtemp(prefix="external-audit-elsewhere-", dir=PARENT_TMP))
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
r = run_driver(BASE_ARGS + ["--cwd", str(REPO)], stub_dir, env_extra, cwd=elsewhere)
check("--cwd points the CLI at the fixture repo even when the driver runs from elsewhere",
      r.returncode == 0 and os.path.samefile(read_cwd(record), REPO))

# --- --cwd omitted defaults to the repository root, not the invocation subdirectory --------
subdir = REPO / "sub"
subdir.mkdir(exist_ok=True)
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
r = run_driver(BASE_ARGS, stub_dir, env_extra, cwd=subdir)
check("--cwd omitted defaults to the repository root (git rev-parse --show-toplevel), not the invocation subdirectory",
      r.returncode == 0 and os.path.samefile(read_cwd(record), REPO))

# --- --model default, and no --model at all, put no -m on the argv --------------------------
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
run_driver(BASE_ARGS, stub_dir, env_extra, cwd=REPO)
check("no --model at all puts no -m on the argv", "-m" not in read_argv(record))
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
run_driver(BASE_ARGS + ["--model", "default"], stub_dir, env_extra, cwd=REPO)
check("--model default puts no -m on the argv", "-m" not in read_argv(record))

# --- --model <name> puts -m <name> on round 1's and round 2's argv, exact shape -------------
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
r1 = run_driver(BASE_ARGS + ["--model", "grok-4-fast"], stub_dir, env_extra, cwd=REPO)
argv1 = read_argv(record)
prompt_path1 = argv1[argv1.index("--prompt-file") + 1] if "--prompt-file" in argv1 else None
session1 = session_of(r1.stdout)
check("--model <name> puts exactly -m <name> after --disallowed-tools on round 1's argv",
      prompt_path1 is not None and session1 is not None and
      argv1 == expected_grok_argv(prompt_path1, session1, model="grok-4-fast"))
stub_dir2, record2, env_extra2 = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
run_driver(BASE_ARGS + ["--model", "grok-4-fast", "--resume", session1 or "missing", "--digest", str(DIGEST)],
          stub_dir2, env_extra2, cwd=REPO)
argv2 = read_argv(record2)
prompt_path2 = argv2[argv2.index("--prompt-file") + 1] if "--prompt-file" in argv2 else None
check("and on the resume round's argv too",
      prompt_path2 is not None and
      argv2 == expected_grok_argv(prompt_path2, session1, resume=True, model="grok-4-fast"))

# --- a stub that refuses the model: nonzero exit, no verdict, driver exits 1 ----------------
stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=1)
r = run_driver(BASE_ARGS + ["--model", "no-such-model"], stub_dir, env_extra, cwd=REPO)
check("a stub that refuses the model (nonzero exit, no verdict) makes the driver exit 1",
      r.returncode == 1)
check("and the model still reached the argv", "-m" in read_argv(record) and "no-such-model" in read_argv(record))

# --- a stdin-fed adapter: the write happens off the main thread, after the deadline and the
# stdout pump start, so a stub that never reads stdin fails the round within --timeout instead
# of deadlocking on a blocked write, and a broken pipe is a clean failed round, not a crash.
# This drives the driver through a harness that injects a throwaway subclass into its own
# ADAPTERS registry -- the stub CLI here, made without record_stdin, never reads stdin, so no
# stub change is needed to be "a stub that never reads stdin".
STDIN_HARNESS_SOURCE = '''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("external_audit_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class StdinAdapter(mod.Adapter):
    name = "stdin-test"
    binary = "grok"
    stdin_prompt = True
    def argv(self, prompt_path, session, resume, model):
        return ["grok"]

mod.ADAPTERS["stdin-test"] = StdinAdapter()
sys.argv = [sys.argv[1], *sys.argv[2:]]
sys.exit(mod.main())
'''
stdin_harness = PARENT_TMP / "stdin_harness.py"
stdin_harness.write_text(STDIN_HARNESS_SOURCE, encoding="utf-8")
# Bigger than any pipe's kernel buffer once inside the prompt, so a synchronous write (the old
# bug) blocks for real rather than completing instantly into the buffer regardless of whether
# the child ever reads it.
big_claims = REPO / "big-claims.md"
big_claims.write_text("claim about external-audit.py:1.\n" * 6000, encoding="utf-8")
stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=0, sleep_s=10)
env = dict(os.environ)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
env.update(env_extra)
t0 = time.time()
try:
    r = subprocess.run([sys.executable, str(stdin_harness), str(DRIVER), "--cli", "stdin-test",
                        "--timeout", "3", "--claims", str(big_claims), "--goal", "prove the stub CLI is driven correctly"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=REPO, env=env, timeout=20)
    elapsed = time.time() - t0
    traceback = "Traceback" in r.stderr
    # the bound: --timeout, then the up-to-5 s join of the stdout pump, then start-up on a loaded runner
    check(f"a stub that never reads stdin fails the round within --timeout, no crash "
          f"(elapsed {elapsed:.1f}s, exit {r.returncode}, traceback {traceback})",
          r.returncode == 1 and elapsed < 3 + 5 + 4 and not traceback)
except subprocess.TimeoutExpired:
    check("a stub that never reads stdin fails the round within --timeout, no crash", False)

# --- S3: the CLI exits at once, never touching stdin at all -- no sleep, so the process is
# already gone by the driver's first poll() check, not killed by --timeout. Distinguishes the
# broken-pipe failure from a parse failure: the feed thread must be joined (bounded) before
# stdin_errors is read, or this races and prints "no JSON verdict" instead. ------------------
stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=0)  # no sleep: exits at once
env = dict(os.environ)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
env.update(env_extra)
r = subprocess.run([sys.executable, str(stdin_harness), str(DRIVER), "--cli", "stdin-test",
                    "--timeout", "10", "--claims", str(big_claims), "--goal", "prove the stub CLI is driven correctly"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=REPO, env=env, timeout=20)
check("a stub that exits at once without reading stdin fails rc 1 with the broken-pipe message",
      r.returncode == 1 and "could not write the prompt" in r.stderr)
check("and reports the pipe failure, not a raced 'no JSON verdict'",
      "no JSON verdict" not in r.stderr)

# The same, with the feed thread held 3 s before it writes: its broken pipe then lands after the
# CLI has exited and the stdout pump has ended, so only the bounded join of the feed thread keeps
# the round from reading stdin_errors before the error is there.
DELAYED_FEED_HARNESS_SOURCE = STDIN_HARNESS_SOURCE.replace("sys.exit(mod.main())", '''import threading, time, types

class DelayedFeedThread(threading.Thread):
    def __init__(self, target=None, **kw):
        if getattr(target, "__name__", "") == "feed":
            feed = target
            sys.stderr.write("delayed-feed: armed\\n")
            def target():
                time.sleep(3)
                feed()
        super().__init__(target=target, **kw)

mod.threading = types.SimpleNamespace(Thread=DelayedFeedThread)
sys.exit(mod.main())''')
delayed_feed_harness = PARENT_TMP / "delayed_feed_harness.py"
delayed_feed_harness.write_text(DELAYED_FEED_HARNESS_SOURCE, encoding="utf-8")
stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=0)
env = dict(os.environ)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
env.update(env_extra)
r = subprocess.run([sys.executable, str(delayed_feed_harness), str(DRIVER), "--cli", "stdin-test",
                    "--timeout", "10", "--claims", str(big_claims), "--goal", "prove the stub CLI is driven correctly"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=REPO, env=env, timeout=30)
check("a broken pipe that lands after the CLI exited is still the pipe failure, not 'no JSON verdict'",
      "delayed-feed: armed" in r.stderr and r.returncode == 1 and "could not write the prompt" in r.stderr
      and "no JSON verdict" not in r.stderr)

# a CLI that exits non-zero before reading the prompt is the exit-code failure, not a re-run;
# the control, the same stub exiting 0, keeps the pipe message
for stdin_exit in (1, 0):
    stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=stdin_exit, stderr_text="401 Unauthorized\n")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
    env.update(env_extra)
    r = subprocess.run([sys.executable, str(stdin_harness), str(DRIVER), "--cli", "stdin-test",
                        "--timeout", "10", "--claims", str(big_claims), "--goal", "prove the stub CLI is driven correctly"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=REPO, env=env, timeout=20)
    if stdin_exit:
        check("a stdin-fed CLI that exits 1 without reading the prompt is reported as exited 1 with its stderr, not as a re-run",
              r.returncode == 1 and "grok exited 1" in r.stderr and "401 Unauthorized" in r.stderr
              and "re-run" not in r.stderr)
    else:
        check("control: the same CLI exiting 0 still gets the pipe message",
              r.returncode == 1 and "could not write the prompt" in r.stderr and "re-run" in r.stderr)

# a verdict on a prompt the CLI never received is not taken, whatever it printed
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=1)
env = dict(os.environ)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
env.update(env_extra)
r = subprocess.run([sys.executable, str(stdin_harness), str(DRIVER), "--cli", "stdin-test",
                    "--timeout", "10", "--claims", str(big_claims), "--goal", "prove the stub CLI is driven correctly"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=REPO, env=env, timeout=20)
check("a stdin-fed CLI that prints an APPROVE and exits 1 without reading the prompt fails the round with no verdict",
      r.returncode == 1 and "grok exited 1" in r.stderr and "VERDICT" not in r.stdout + r.stderr)

# --- S4: session_from_output -- a CLI that mints its own session id, read back from its stdout,
# not the id this driver minted and passed via argv(). The "resume with" line must carry it. ---
SESSION_HARNESS_SOURCE = '''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("external_audit_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FixedSessionAdapter(mod.GrokAdapter):
    name = "session-test"
    def session_from_output(self, stdout_text):
        return "FIXED-SESSION-42"

mod.ADAPTERS["session-test"] = FixedSessionAdapter()
sys.argv = [sys.argv[1], *sys.argv[2:]]
sys.exit(mod.main())
'''
session_harness = PARENT_TMP / "session_harness.py"
session_harness.write_text(SESSION_HARNESS_SOURCE, encoding="utf-8")
stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
env = dict(os.environ)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
env.update(env_extra)
r = subprocess.run([sys.executable, str(session_harness), str(DRIVER), *BASE_ARGS[:-2], "--cli", "session-test"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=REPO, env=env, timeout=20)
check("session_from_output's id, not the driver-minted one, appears on the 'resume with' line",
      "--resume FIXED-SESSION-42" in r.stdout)
m = re.search(r"narration: (.+?)\)\s+\(resume", r.stdout)
check("and the artifact path on the same line still names the real round folder on disk",
      bool(m) and pathlib.Path(m.group(1)).exists())

# --- S1 and S2 need POSIX process groups (killpg) and os.fork; Windows has neither, and its
# own tree-kill (taskkill /T) is exercised by the --timeout rows above on every platform already.
if os.name == "nt":
    print("  skip: SIGTERM/SIGINT/timeout process-GROUP kill tests need POSIX (Windows: taskkill /T, already covered above)")
else:
    # --- S1, a regression: start_new_session detaches the CLI into its own process group, so a
    # terminal SIGINT no longer reaches it and nothing kills the group when the driver dies. Send
    # the driver a SIGTERM (mapped to SystemExit) and a SIGINT (a Ctrl-C, KeyboardInterrupt by
    # Python's own default) while the stub sleeps, and assert the stub's own process is gone. A
    # shell that starts this suite in the background starts it with SIGINT ignored, and Python
    # installs no KeyboardInterrupt handler over an ignored SIGINT, so the driver gets the default. ---
    for sig in (signal.SIGTERM, signal.SIGINT):
        stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=0, sleep_s=60)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
        env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(PARENT_TMP)
        env.update(env_extra)
        driver_proc = subprocess.Popen([sys.executable, str(DRIVER), *BASE_ARGS, "--timeout", "300"],
                                        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        got_record = _wait_until(lambda: record.exists(), timeout=10)
        stub_pid = json.loads(record.read_text(encoding="utf-8"))["pid"] if got_record else None
        if stub_pid is not None:
            os.kill(driver_proc.pid, sig)
        try:
            driver_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            driver_proc.kill(); driver_proc.wait(timeout=5)
        dead = stub_pid is not None and _wait_until(lambda: not _pid_alive(stub_pid), timeout=10)
        check(f"a {sig.name} to the driver also kills the stub's own process, not just the driver",
              stub_pid is not None and dead)
        if stub_pid is not None and _pid_alive(stub_pid):
            try: os.kill(stub_pid, signal.SIGKILL)
            except ProcessLookupError: pass

    # --- S2: the tree kill has no discriminating test as long as the stub is one process (exec
    # replaces the shell, so killing proc.pid and killing its group look identical). A stub that
    # forks a sleeping grandchild tells them apart: only a group kill (os.killpg) reaches it. ---
    fork_record = REPO / "fork-child.pid"
    if fork_record.exists():
        fork_record.unlink()
    stub_dir, record, env_extra = make_stub(reply_text=None, exit_code=0, sleep_s=30)
    env_extra["STUB_FORK_CHILD_RECORD"] = str(fork_record)
    r = run_driver(BASE_ARGS + ["--timeout", "2"], stub_dir, env_extra, cwd=REPO)
    got_fork = _wait_until(lambda: fork_record.exists(), timeout=5)
    child_pid = int(fork_record.read_text(encoding="utf-8").strip()) if got_fork else None
    dead = child_pid is not None and _wait_until(lambda: not _pid_alive(child_pid), timeout=5)
    check("a timeout kills the whole process group -- the stub's forked grandchild dies too",
          r.returncode == 1 and child_pid is not None and dead)
    if child_pid is not None and _pid_alive(child_pid):
        try: os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError: pass
    if fork_record.exists():
        fork_record.unlink()

# --- S5, the real CI cause: the artifact parent folder is shared across every user of the
# machine's temp dir. A pre-existing "external-audit" folder another account owns must not take
# down every round with a PermissionError traceback -- the fix is a per-user parent folder name,
# so this one is never touched at all. --------------------------------------------------------
if os.name == "nt":
    print("  skip: unwritable shared-tmp test needs POSIX chmod semantics")
else:
    s5_tmp = pathlib.Path(tempfile.mkdtemp(prefix="external-audit-s5-", dir=PARENT_TMP))
    shared = s5_tmp / "external-audit"
    shared.mkdir()
    os.chmod(shared, 0o555)
    try:
        stub_dir, record, env_extra = make_stub(reply_text=APPROVE_REPLY, exit_code=0)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PATH"] = str(stub_dir) + os.pathsep + SAFE_PATH
        env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(s5_tmp)
        env.update(env_extra)
        r = subprocess.run([sys.executable, str(DRIVER), *BASE_ARGS], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=REPO, env=env, timeout=60)
        check("a pre-existing unwritable 'external-audit' folder in TEMP does not block the driver "
              "(per-user parent folder)",
              r.returncode == 0 and "Traceback" not in r.stderr and "Permission" not in r.stderr)
    finally:
        os.chmod(shared, 0o755)


print("all external-audit cases pass" if not failures else f"{failures} failure(s)")
sys.exit(1 if failures else 0)
