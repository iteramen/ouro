"""Observe-only progress for one `ouro run`.

The file is `<git-common-dir>/ouro-run/<issue>.json`. The driver replaces it
when a logged step changes and leaves it in place at exit. The next run of
the same issue replaces it; another issue is another file. Two parts of one
issue share the file, and the last write wins. Nothing here is read back,
and a dry run writes nothing.

A failed write is one stderr line, once. The run's exit, log and tracker
writes stay as they were. `steps` is 11, the live tags `[1]` through `[11]`;
a later tag is recorded as step 11, node `close`.
"""
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

STEPS = 11
_UNSET = object()
_NODES = {1: "bind", 2: "verify", 3: "worktree", 5: "gates", 6: "push", 7: "REVIEW",
          8: "checks", 9: "merge", 10: "cleanup", 11: "FOLLOWUP"}
_STEP = re.compile(r"\[(\d+)([a-z])?\]\s*(.*)")
_REVIEW = re.compile(r"REVIEW node round (\d+)")
_READER = re.compile(r"external reader round (\d+) \(([^)]*)\)")
_GATE_EXIT = re.compile(r"gate `[^`]*`: exit (\S+)")
_GATE_RECEIPT = re.compile(r"gate `[^`]*`: exact-head validation receipt")
_PR = (
    re.compile(r"pull request #(\d+)"),
    re.compile(r"ready for merge: #(\d+)"),
    re.compile(r"\bmerged #(\d+)"),
    re.compile(r"checks on #(\d+)"),
    re.compile(r"so #(\d+) gets no check"),
    re.compile(r"direct: #(\d+) merged"),
    re.compile(r"^\[10\] #(\d+) is merged"),
)
_PR_LANE = ("continues as express", "ordinary PR flow", "moves to a worktree")


def _stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pull(line):
    for pattern in _PR:
        found = pattern.search(line)
        if found:
            return int(found.group(1))
    return None


class Progress:
    def __init__(self, env, issue, part):
        runtime = getattr(env, "runtime", None)
        backend = runtime.backend_of("reviewer") if hasattr(runtime, "backend_of") else getattr(runtime, "backend", None)
        self.env = env
        self.backend = backend if isinstance(backend, str) and backend.strip() else None
        self.path = None
        self._complained = False
        now = _stamp()
        self.data = {
            "version": 1,
            "issue": int(issue),
            "part": part,
            "lane": "pr",
            "step": 0,
            "steps": STEPS,
            "node": None,
            "review_round": None,
            "reviewer": None,
            "gates": {"done": 0, "total": None, "failed": 0},
            "pr": None,
            "started": now,
            "updated": now,
            "pid": os.getpid(),
            "exit": None,
            "outcome": "running",
        }

    def revise(self, *, part=_UNSET, lane=None, pr=_UNSET, gates_total=_UNSET, gates_add=0):
        """Record a fact the log line does not carry; `gates_add` widens the pass's total in place. Never raises."""
        try:
            if part is not _UNSET:
                self.data["part"] = part
            if lane in ("direct", "pr"):
                self.data["lane"] = lane
            if pr is not _UNSET:
                self.data["pr"] = None if pr is None else int(pr)
            if gates_total is not _UNSET:
                gates = self.data["gates"]
                gates["total"] = None if gates_total is None else int(gates_total)
                gates["done"] = 0
                gates["failed"] = 0
            if gates_add:
                gates = self.data["gates"]
                gates["total"] = (gates["total"] or 0) + int(gates_add)
            self.data["updated"] = _stamp()
            self._write()
        except Exception as ex:
            self._complain(ex)

    def observe(self, msg):
        """One log line. A line that changes nothing is not written."""
        try:
            if not self._apply(msg):
                return
            self.data["updated"] = _stamp()
            self._write()
        except Exception as ex:
            self._complain(ex)

    def finish(self, code, outcome):
        try:
            self.data["exit"] = code
            self.data["outcome"] = outcome
            self.data["updated"] = _stamp()
            self._write()
        except Exception as ex:
            self._complain(ex)

    def _apply(self, msg):
        line = (msg or "").split("\n", 1)[0].strip()
        if not line:
            return False
        changed = False
        matched = _STEP.match(line)
        if matched:
            tag = int(matched.group(1))
            rest = matched.group(3)
            if tag > STEPS:
                self.data["step"] = STEPS
                self.data["node"] = "close"
            else:
                self.data["step"] = tag
                if tag == 2 and matched.group(2):
                    self.data["node"] = _NODES[2] if any(phrase in rest for phrase in _PR_LANE) else "direct"
                elif tag == 4:
                    self.data["node"] = "CHECKPOINT" if ("CHECKPOINT" in rest or rest.startswith("finding posted")) else "BUILD"
                else:
                    self.data["node"] = _NODES.get(tag, self.data["node"])
            changed = True
        elif line.startswith("done:"):
            self.data["step"] = STEPS
            self.data["node"] = "close"
            changed = True
        elif line.startswith("stopped:"):
            self.data["outcome"] = "stopped"
            changed = True
        if self._detail(line):
            changed = True
        number = _pull(line)
        if number is not None and number != self.data["pr"]:
            self.data["pr"] = number
            changed = True
        return changed

    def _detail(self, line):
        changed = False
        if "[2a]" in line or any(phrase in line for phrase in _PR_LANE):
            lane = "pr" if any(phrase in line for phrase in _PR_LANE) else "direct"
            if "[2a]" in line and self.data["lane"] != lane:
                self.data["lane"] = lane
                changed = True
            elif any(phrase in line for phrase in _PR_LANE) and self.data["lane"] != "pr":
                self.data["lane"] = "pr"
                changed = True
        found = _REVIEW.search(line)
        if found:
            self.data["review_round"] = int(found.group(1))
            self.data["reviewer"] = self.backend
            self.data["node"] = "REVIEW"
            changed = True
        found = _READER.search(line)
        if found:
            self.data["review_round"] = int(found.group(1))
            self.data["reviewer"] = found.group(2) or None
            changed = True
        if line.startswith("[7] Copilot"):
            self.data["reviewer"] = "copilot"
            changed = True
        found = _GATE_EXIT.search(line)
        if found:
            self.data["gates"]["done"] += 1
            if found.group(1) != "0":
                self.data["gates"]["failed"] += 1
            changed = True
        elif _GATE_RECEIPT.search(line):
            self.data["gates"]["done"] += 1
            changed = True
        return changed

    def _path(self):
        if self.path is not None:
            return self.path
        try:
            found = self.env.git("rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
        except Exception:
            return None
        text = str(getattr(found, "out", "") or "").strip()
        if getattr(found, "code", 1) != 0 or not text:
            return None
        self.path = Path(text) / "ouro-run" / f"{int(self.data['issue'])}.json"
        return self.path

    def _write(self):
        if getattr(self.env, "dry", False):
            return
        temporary = None
        try:
            path = self._path()
            if path is None:
                return
            directory = path.parent
            if directory.is_symlink() or not directory.parent.is_dir():
                raise OSError("progress directory is not a real directory under the git common dir")
            directory.mkdir(exist_ok=True)
            temporary = directory / (path.name + "." + uuid.uuid4().hex)
            raw = json.dumps(self.data, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
            temporary.write_bytes(raw)
            os.replace(temporary, path)
            temporary = None
        except Exception as ex:
            self._complain(ex)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except Exception:
                    pass

    def _complain(self, ex):
        if self._complained:
            return
        self._complained = True
        try:
            sys.stderr.write(f"ouro: progress: {ex}\n")
            sys.stderr.flush()
        except Exception:
            pass
