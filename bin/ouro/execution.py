"""Observed BUILD commands, bound to final file inputs and a committed head."""
from contextlib import ExitStack
import hashlib
import json
import os
import stat
import time
from pathlib import Path, PurePosixPath

from . import sh

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_ENTRIES = 100000
MAX_DEPTH = 16
INVENTORY_LIMIT = 8 * 1024 * 1024
SNAPSHOT_SECONDS = 60
CHUNK = 65536


def ci_mode(run):
    """Repository-owned PR validation; independent of the selected worker backend."""
    return (not getattr(run, "in_lane", False) and "checkpoint" not in getattr(run, "labels", [])
            and run.env.lookup("ship.validation") == "ci")


def ci_commands(run, *, selected=True):
    """The owner's aggregate checks cover these exact gate runs, never new branch policy."""
    if not ci_mode(run):
        return set()
    if not run.env.pr_checks():
        raise sh.DriverError("CI validation requires a pull_request workflow on the default branch")
    if run.gate_entries() != run.env.data.get("gate", []):
        raise sh.DriverError("CI gate coverage changed: update the owner's invoking binding before resuming")
    return {cmd for cmd, gate in run.area_gates(tree=False)
            if not run.locally_run(cmd, gate)
            and (not selected or not gate.get("paths") or run.touches(gate["paths"]))}


def check_ci(run, pr, head, *, direct_gates=None):
    """Fresh GitHub results, not a local receipt or a successful notice in a gate command."""
    run.ci_execution_head, run.ci_execution_commands, run.ci_execution_checks = None, set(), []
    if direct_gates is None:
        commands = ci_commands(run)
    else:
        # Only an actual direct PR's trusted structural obligations use this
        # route. Normal BUILD/in-lane admission and selection stay unchanged.
        if run.env.lookup("ship.validation") != "ci" or not run.env.pr_checks():
            raise sh.DriverError("direct CI validation requires the owner's PR validation checks")
        if run.gate_entries() != run.env.data.get("gate", []):
            raise sh.DriverError("CI gate coverage changed: update the owner's invoking binding before resuming")
        commands = {cmd for cmd, gate in direct_gates if not run.locally_run(cmd, gate)}
    view = run.env.gh_json("pr", "view", str(pr["number"]), "--json", "headRefOid,statusCheckRollup")
    names = run.env.lookup("ship.validation_checks") or []
    if not names:
        raise sh.DriverError("CI validation requires nonempty ship.validation_checks")
    if view.get("headRefOid") != head:
        sh.say(f"waiting for CI validation at {head}: the pull request head changed")
        return False
    rows = view.get("statusCheckRollup") or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise sh.DriverError("GitHub returned malformed CI check results; nothing is validated")
    evidence = []
    for name in names:
        matches = [row for row in rows if row.get("name", row.get("context")) == name]
        if not matches or any(not ((row.get("__typename") == "CheckRun" and row.get("name") == name
                                   and row.get("status") == "COMPLETED" and row.get("conclusion") == "SUCCESS")
                                  or (row.get("__typename") == "StatusContext" and row.get("context") == name
                                      and row.get("state") == "SUCCESS")) for row in matches):
            sh.say(f"waiting for CI validation at {head}: required check {name!r} has no successful completion")
            return False
        evidence.extend({"name": name, "sha": head, "url": row.get("detailsUrl", row.get("targetUrl", ""))}
                        for row in matches)
    run.ci_execution_head, run.ci_execution_commands, run.ci_execution_checks = head, commands, evidence
    return True


def ci_inputs(run, head):
    """Visible clean inputs for the portable local part of repository CI validation."""
    from . import validation
    validation.clean(run, head)
    entries = run.wt_git("ls-files", "-v", "-z").out.split("\0")
    if any(row[:1].islower() or row.startswith("S ") for row in entries):
        raise sh.Refused("CI validation requires visible local inputs; preserve and resolve assume-unchanged/skip-worktree entries")


class Budget:
    def __init__(self):
        self.deadline = time.monotonic() + SNAPSHOT_SECONDS
        self.bytes = self.entries = 0

    def check(self, *, size=0, entries=0):
        self.bytes += size
        self.entries += entries
        if (time.monotonic() >= self.deadline or self.bytes > MAX_TOTAL
                or self.entries > MAX_ENTRIES):
            raise ValueError("execution snapshot exceeds resource budget")


def unchanged(before, after):
    return (os.path.samestat(before, after)
            and (before.st_mode, before.st_nlink, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_mode, after.st_nlink, after.st_size, after.st_mtime_ns, after.st_ctime_ns))


class Directory:
    """POSIX directory identity held across relative no-follow reads."""
    def __init__(self, path, parent=None, name=None):
        self.path, self.parent, self.name = path, parent, name
        self.fd = None
        before = parent.stat(name) if parent else path.lstat()
        try:
            self.fd = os.open(name if parent else path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              **({"dir_fd": parent.fd} if parent else {}))
            self.identity = os.fstat(self.fd)
            if not stat.S_ISDIR(before.st_mode) or not self.identity.st_ino or not os.path.samestat(before, self.identity):
                raise ValueError("execution input directory changed")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def stat(self, name):
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def check(self):
        current = self.parent.stat(self.name) if self.parent else self.path.lstat()
        if not os.path.samestat(self.identity, current):
            raise ValueError("execution input directory changed")

    def file(self, name):
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)

    def link(self, name):
        return os.readlink(name, dir_fd=self.fd)

    def empty(self):
        with os.scandir(self.fd) as entries:
            return next(entries, None) is None


def directory_fd(stack, parent, name):
    child = Directory(parent.path / name, parent, name)
    stack.callback(child.close)
    return child


def regular_digest(directory, name, named, budget):
    fd = directory.file(name)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE
                or not unchanged(named, before)):
            raise ValueError("nonregular, aliased, oversized or replaced execution input")
        digest = hashlib.sha256()
        count = 0
        while True:
            budget.check()
            data = os.read(fd, min(CHUNK, MAX_FILE - count + 1))
            budget.check(size=len(data))
            count += len(data)
            if count > MAX_FILE:
                raise ValueError("execution input exceeds file bound")
            if not data:
                break
            digest.update(data)
        if (not unchanged(before, os.fstat(fd)) or count != before.st_size
                or not unchanged(before, directory.stat(name))):
            raise ValueError("execution input changed during read")
        return digest.hexdigest()
    finally:
        os.close(fd)


def fingerprint(root, git=None):
    """Bounded stable no-follow snapshot; unsupported primitives provide no proof.

    The deadline is checked between local regular-file reads; it is not a hard
    deadline for a stalled filesystem. Accepted caller snapshots run under its lease.
    """
    if (os.name != "posix" or not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
            or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd):
        return None
    budget = Budget()
    try:
        return snapshot(Path(root).resolve(strict=True), git, budget, 0)
    except (OSError, ValueError, sh.DriverError, NotImplementedError):
        return None


def snapshot(root, git, budget, depth, identity=None):
    if depth > MAX_DEPTH:
        raise ValueError("execution submodule recursion exceeds bound")
    def query(*args):
        from .runtime import process
        budget.check()
        r = process.run(sh.argv_for("git", "-c", "core.fsmonitor=false", *args), cwd=root,
                        timeout=min(30, max(.01, budget.deadline - time.monotonic())),
                        output_limit=INVENTORY_LIMIT, stderr_limit=INVENTORY_LIMIT)
        if r.code:
            raise sh.DriverError("execution input inventory unavailable")
        return r.out
    def inventory(*args):
        budget.check()
        result = (git or query)(*args)
        # The process transport replaces undecodable Git path bytes with U+FFFD.
        # Such a name cannot safely identify its source file; provide no candidate.
        if not isinstance(result, str) or "\ufffd" in result or len(result.encode("utf-8")) > INVENTORY_LIMIT:
            raise ValueError("unsupported or oversized execution inventory")
        budget.check(size=len(result.encode("utf-8")))
        return result
    with ExitStack() as stack:
        rootfd = Directory(root)
        stack.callback(rootfd.close)
        if identity is not None and not os.path.samestat(identity, rootfd.identity):
            raise ValueError("execution submodule directory changed")
        names = inventory("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        index = inventory("ls-files", "--stage", "-z")
        head = inventory("rev-parse", "--revs-only", "HEAD")
        links = {}
        for row in index.split("\0"):
            if row.startswith("160000 "):
                metadata, name = row.split("\t", 1)
                fields = metadata.split()
                if len(fields) != 3:
                    raise ValueError("invalid execution gitlink inventory")
                links[name] = fields[1]
        rows = [["HEAD", head], ["index", index]]
        for name in sorted(set(names.split("\0")) - {""}):
            budget.check(entries=1)
            parts = PurePosixPath(name).parts
            if (not parts or PurePosixPath(name).is_absolute() or "/".join(parts) != name or any(p in (".", "..") for p in parts)
                    or (os.name == "nt" and any(c in name for c in ("\\", ":")))):
                raise ValueError("invalid execution input path")
            with ExitStack() as parents:
                directory = rootfd
                held = []
                try:
                    for part in parts[:-1]:
                        child = directory_fd(parents, directory, part)
                        held.append((directory, part, child))
                        directory = child
                    info = directory.stat(parts[-1])
                except FileNotFoundError:
                    continue  # tracked deletion; a future inventory invalidates changed names
                mode = info.st_mode
                if not info.st_ino:
                    raise ValueError("execution input identity unavailable")
                if stat.S_ISLNK(mode):
                    kind, data = "link", os.fsencode(directory.link(parts[-1]))
                    if len(data) > MAX_FILE or not unchanged(info, directory.stat(parts[-1])):
                        raise ValueError("execution symlink changed")
                    budget.check(size=len(data));digest = hashlib.sha256(data).hexdigest()
                elif stat.S_ISREG(mode):
                    kind, digest = "file", regular_digest(directory, parts[-1], info, budget)
                elif stat.S_ISDIR(mode) and name in links:
                    kind = "gitlink"
                    child = directory_fd(parents, directory, parts[-1])
                    held.append((directory, parts[-1], child))
                    try:
                        marker = child.stat(".git")
                    except FileNotFoundError:
                        if not child.empty():
                            raise ValueError("uninitialized gitlink is not empty")
                        nested = "uninitialized"
                    else:
                        if not (stat.S_ISREG(marker.st_mode) or stat.S_ISDIR(marker.st_mode)):
                            raise ValueError("aliased submodule administrative marker")
                        path = root / name
                        nested = snapshot(path, lambda *args: inventory("-C", str(path), *args), budget, depth + 1, child.identity)
                    digest = hashlib.sha256(json.dumps([links[name], nested]).encode()).hexdigest()
                else:
                    raise ValueError("nonregular execution input")
                for _, _, child in held:
                    child.check()
                rows.append([name, kind, bool(mode & 0o111) if os.name != "nt" else False, digest])
        rootfd.check()
        if (names != inventory("ls-files", "-z", "--cached", "--others", "--exclude-standard")
                or index != inventory("ls-files", "--stage", "-z")
                or head != inventory("rev-parse", "--revs-only", "HEAD")):
            raise ValueError("execution Git inventory changed")
        budget.check()
        return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode()).hexdigest()


def directory(run):
    return Path(run.wt_git("rev-parse", "--absolute-git-dir").out.strip()) / "ouro-execution"


def retain(run, calls, *, recheck=None):
    """Only the driver writes these receipts; BUILD answers cannot provide observations."""
    inputs = fingerprint(run.worktree)
    previous = [{"tool": "run", "command": cmd, "exit": 0, "inputs": inputs} for cmd in proven(run)]
    latest = {c["command"]: c for c in [*previous, *calls] if c.get("tool") == "run"
              and isinstance(c.get("command"), str)}
    results = [c for c in latest.values() if inputs and c.get("inputs") == inputs
               and not c.get("error") and type(c.get("exit")) is int and c["exit"] == 0]
    root = directory(run)
    root.mkdir(exist_ok=True)
    head = run.head()
    if root.is_symlink() or any((root / (head + suffix)).is_symlink() for suffix in (".json", ".sha256")):
        raise sh.DriverError("aliased execution evidence directory or file")
    requirements = getattr(run, "execution_grants", None)
    if requirements is None:
        requirements = grants(run)
    data = {"sha": head, "inputs": inputs, "results": results, "grants": requirements,
            "recheck": list(recheck) if recheck is not None else candidates(run)}
    raw = json.dumps(data).encode("utf-8")
    (root / (head + ".json")).write_bytes(raw)
    (root / (head + ".sha256")).write_text(hashlib.sha256(raw).hexdigest(), encoding="utf-8")


def load(path):
    if path.is_symlink() or path.parent.is_symlink() or path.with_suffix(".sha256").is_symlink():
        raise ValueError("aliased execution receipt")
    raw = path.read_bytes()
    if path.with_suffix(".sha256").read_text(encoding="utf-8") != hashlib.sha256(raw).hexdigest():
        raise ValueError("corrupt execution receipt")
    return json.loads(raw)


def grants(run):
    """Obligations survive descendant heads; results never do."""
    heads = run.wt_git("rev-list", "HEAD", "--not", f"origin/{run.env.default}").out.split()
    values = {}
    for head in heads:
        try:
            data = load(directory(run) / (head + ".json"))
            value = data.get("grants")
            if data["sha"] == head and isinstance(value, list):
                for row in value:
                    if (isinstance(row, list) and len(row) == 2
                            and isinstance(row[0], str) and isinstance(row[1], dict)):
                        values[json.dumps(row, sort_keys=True)] = row
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return list(values.values())


def candidates(run):
    """Pre-commit observations authorize a re-run, never a pass at a new head."""
    try:
        data = load(directory(run) / (run.head() + ".json"))
        if data["sha"] == run.head() and data["inputs"] == fingerprint(run.worktree):
            return [cmd for cmd in data.get("recheck", []) if isinstance(cmd, str)]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return []


def proven(run):
    head = run.head()
    path = directory(run) / (head + ".json")
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return set()
        data = load(path)
        inputs = fingerprint(run.worktree)
        if not inputs or data["sha"] != head or data["inputs"] != inputs:
            return set()
        return {r["command"] for r in data["results"] if r["inputs"] == inputs
                and type(r["exit"]) is int and r["exit"] == 0 and not r.get("error")}
    except (OSError, ValueError, KeyError, TypeError):
        return set()
