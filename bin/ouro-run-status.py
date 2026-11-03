#!/usr/bin/env python3
"""ouro-run-status.py - one status line from the newest ouro run progress file.

Usage:
    python3 ouro-run-status.py
        Print one line for the newest `<git-common-dir>/ouro-run/<issue>.json`
        in the current repository, or print nothing when none can be read. A
        finished run prints a one-line summary for 5 minutes after its last
        update, then nothing.
    python3 ouro-run-status.py --selftest
        Run the self-check.

A status line runs this on every refresh, so it reads one local git directory
and the JSON files in it, never the network, never stdin, and never fails:
any error prints nothing and exits 0.

Stdlib only.
"""
import calendar
import errno
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_NAME = re.compile(r"\d+\.json")
_WIDTH = 10
_SHOWN = 300  # seconds a finished run's summary stays after its `updated` stamp
_MARKS = {"ok": "done ✓", "waiting": "waiting ⏸"}


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _when(value):
    if not isinstance(value, str):
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _plain(value, limit=60):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable())[:limit]


def _span(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _bar(step, steps):
    step = step or 0
    if not steps or step <= 0:
        filled = 0
    else:
        filled = min(_WIDTH, max(0, round(_WIDTH * min(step, steps) / steps)))
    return "▕" + ("█" * filled) + ("░" * (_WIDTH - filled)) + "▏"


def alive(pid):
    """Whether `pid` is a live process. Signal 0 is not used on Windows: there it terminates."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError as ex:
        return ex.errno == errno.EPERM
    except OverflowError:
        return False
    return True


def render(data, *, now, alive=True):
    """The status line, or "" when `data` is not a progress object."""
    if not isinstance(data, dict):
        return ""
    issue = _int(data.get("issue"))
    if issue is None:
        return ""
    step, steps = _int(data.get("step")) or 0, _int(data.get("steps")) or 0
    head = f"ouro #{issue}"
    part = _int(data.get("part"))
    if part is not None:
        head += f" p{part}"
    if data.get("lane") == "direct":
        head += " direct"
    node = _plain(data.get("node"))
    pr = _int(data.get("pr"))
    start, end = _when(data.get("started")), _when(data.get("updated"))
    outcome = data.get("outcome")
    if outcome != "running" or (_int(data.get("pid")) is not None and not alive):
        if end is None or now - end > _SHOWN:
            return ""
        word = "interrupted" if outcome == "running" else _plain(outcome, 20) or "interrupted"
        text = f"{head} {_MARKS.get(word, word + ' ✗')}"
        if word != "ok" and node:
            text += f" at {node}"
        clauses = [text]
        if pr is not None:
            clauses.append(f"PR #{pr}")
        if start is not None:
            clauses.append(_span(end - start))
        return " · ".join(clauses)
    label = f"{step}/{steps}" if steps else str(step)
    if node:
        label += " " + node
    round_ = _int(data.get("review_round"))
    if round_ is not None:
        label += f" r{round_}"
    reviewer = _plain(data.get("reviewer"))
    if reviewer:
        label += f" ({reviewer})"
    clauses = [f"{head} {_bar(step, steps)} {label}"]
    gates = data.get("gates") if isinstance(data.get("gates"), dict) else {}
    total, done, failed = _int(gates.get("total")), _int(gates.get("done")) or 0, _int(gates.get("failed")) or 0
    if total is not None:
        text = f"gates {done}/{total}"
        if failed:
            text += f", {failed} failed"
        clauses.append(text)
    if pr is not None:
        clauses.append(f"PR #{pr}")
    if start is not None:
        clauses.append(_span(now - start))
    return " · ".join(clauses)


def common_dir():
    try:
        found = subprocess.run(
            ["git", "-c", "core.quotepath=false", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if found.returncode != 0:
        return None
    text = found.stdout.decode("utf-8", "replace").strip()
    return Path(text) / "ouro-run" if text else None


def read_latest(directory):
    if directory is None or not directory.is_dir():
        return None
    best = None
    for path in directory.glob("*.json"):
        if not _NAME.fullmatch(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or _int(data.get("issue")) is None:
            continue
        stamp = str(data.get("updated") or "")
        if best is None or stamp >= best[0]:
            best = (stamp, data)
    return None if best is None else best[1]


def _git_env():
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    return env


def selftest():
    import tempfile

    started, updated = "2026-10-31T14:00:00Z", "2026-10-31T14:14:00Z"
    now = calendar.timegm(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
    data = {
        "version": 1, "issue": 142, "part": None, "lane": "pr", "step": 7, "steps": 11,
        "node": "REVIEW", "review_round": 2, "reviewer": "codex",
        "gates": {"done": 9, "total": 9, "failed": 0}, "pr": 318,
        "started": started, "updated": updated, "pid": 1, "exit": None, "outcome": "running",
    }
    want = "ouro #" + "142 ▕██████░░░░▏ 7/11 REVIEW r2 (codex) · gates 9/9 · PR #" + "318 · 14m"
    assert render(data, now=now, alive=True) == want
    assert render(dict(data, pid=None), now=now, alive=False) == want
    assert render(data, now=now + 120, alive=True) == want.replace("· 14m", "· 16m")
    tail = " · PR #" + "318 · 14m"
    finished = [
        (dict(data, outcome="ok", exit=0), True, "done ✓" + tail),
        (dict(data, outcome="stopped", node="gates"), True, "stopped ✗ at gates" + tail),
        (dict(data, outcome="waiting"), True, "waiting ⏸ at REVIEW" + tail),
        (data, False, "interrupted ✗ at REVIEW" + tail),
        ({k: v for k, v in data.items() if k != "outcome"}, True, "interrupted ✗ at REVIEW" + tail),
        (dict(data, outcome=7), True, "interrupted ✗ at REVIEW" + tail),
    ]
    for row, live, end in finished:
        assert render(row, now=now + 300, alive=live) == "ouro #" + "142 " + end, row
        assert render(row, now=now + 301, alive=live) == "", row
    assert render(dict(data, outcome="ok", pr=None), now=now, alive=True) == "ouro #" + "142 done ✓ · 14m"
    assert render(dict(data, outcome="ok", updated="later"), now=now, alive=True) == ""
    direct = dict(data, part=2, lane="direct")
    assert render(direct, now=now, alive=True).startswith("ouro #" + "142 p2 direct ")
    quiet = dict(data, step=1, node="bind", review_round=None, reviewer=None, pr=None,
                 gates={"done": 0, "total": None, "failed": 0})
    text = render(quiet, now=now, alive=True)
    assert "gates" not in text and "PR #" not in text and "r2" not in text
    failed = dict(data, gates={"done": 8, "total": 9, "failed": 1})
    assert "gates 8/9, 1 failed" in render(failed, now=now, alive=True)
    assert render({"issue": "142"}, now=now, alive=True) == ""
    this = str(Path(__file__).resolve())
    env = _git_env()

    def run(cwd):
        return subprocess.run([sys.executable, this], cwd=cwd, capture_output=True, env=env, timeout=30)

    with tempfile.TemporaryDirectory() as tmp:
        bare = Path(tmp) / "empty"
        bare.mkdir()
        got = run(bare)
        assert (got.returncode, got.stdout, got.stderr) == (0, b"", b""), got
        repo = Path(tmp) / "repo"
        subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True, capture_output=True, env=env)
        assert run(repo).stdout == b""
        common = subprocess.run(
            ["git", "-c", "core.quotepath=false", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo, capture_output=True, check=True, env=env, text=True).stdout.strip()
        directory = Path(common) / "ouro-run"
        directory.mkdir()
        (directory / "1.json").write_text("{", encoding="utf-8")
        sample = dict(data, issue=7, updated="2026-10-31T14:14:30Z", pid=os.getpid())
        (directory / "7.json").write_text(json.dumps(sample), encoding="utf-8")
        out = run(repo)
        assert out.returncode == 0 and out.stderr == b"" and out.stdout.decode("utf-8").startswith("ouro #" + "7 "), out
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        raise SystemExit(0)
    try:
        directory = common_dir()
        data = read_latest(directory)
        if data:
            text = render(data, now=time.time(), alive=alive(data.get("pid")))
            if text:
                sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
                sys.stdout.flush()
    except Exception:
        pass
    raise SystemExit(0)
