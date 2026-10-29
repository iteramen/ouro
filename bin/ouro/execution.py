"""Observed BUILD commands, bound to final file inputs and a committed head."""
import hashlib
import json
import os
from pathlib import Path

from . import sh


def ci_checks(env, pr, head):
    """Fresh GitHub results, not a local receipt or a successful notice in a gate command: the evidence, or None to wait."""
    view = env.gh_json("pr", "view", str(pr["number"]), "--json", "headRefOid,statusCheckRollup")
    names = env.lookup("ship.validation_checks") or []
    if not names:
        raise sh.DriverError("CI validation requires nonempty ship.validation_checks")
    if view.get("headRefOid") != head:
        sh.say(f"waiting for CI validation at {head}: the pull request head changed")
        return None
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
            return None
        evidence.extend({"name": name, "sha": head, "url": row.get("detailsUrl", row.get("targetUrl", ""))}
                        for row in matches)
    return evidence


def ci_inputs(tree, head):
    """Visible clean inputs for the portable local part of repository CI validation."""
    from . import validation
    validation.clean(tree, head)
    entries = tree.git("ls-files", "-v", "-z").out.split("\0")
    if any(row[:1].islower() or row.startswith("S ") for row in entries):
        raise sh.Refused("CI validation requires visible local inputs; preserve and resolve assume-unchanged/skip-worktree entries")


def fingerprint(root, git=None):
    """HEAD, the index, and the mode and bytes of every path Git reports changed, untracked or hidden
    (assume-unchanged, skip-worktree): equal before and after a command that changed none of its
    inputs. None when Git cannot say, or a changed path is a directory whose contents it does not list."""
    root = Path(root)
    def query(*args):
        r = sh.run(sh.argv_for("git", "-c", "core.fsmonitor=false", *args), cwd=root, timeout=60)
        if r.code:
            raise sh.DriverError("execution input inventory unavailable")
        return r.out
    git = git or query
    try:
        status = git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--ignore-submodules=none")
        hidden = [row[2:] for row in git("ls-files", "-v", "-z").split("\0") if row[:1].islower() or row.startswith("S ")]
        if "\ufffd" in status + "".join(hidden):
            return None  # an undecodable name cannot be read back to its file
        rows = [git("rev-parse", "--revs-only", "HEAD"), git("ls-files", "--stage", "-z"), status]
        for name in sorted({row[3:] for row in status.split("\0") if row[3:]} | set(hidden)):
            path = root / name
            if path.is_symlink():
                rows.append([name, "link", os.readlink(path)])
            elif path.is_file():
                rows.append([name, path.stat().st_mode & 0o111, hashlib.sha256(path.read_bytes()).hexdigest()])
            elif path.exists():
                return None  # a dirty submodule or a nested repository
    except (OSError, ValueError, sh.DriverError):
        return None
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode("utf-8")).hexdigest()


def directory(tree):
    return tree.admin() / "ouro-execution"


def retain(tree, requirements, *, recheck=None):
    """Only the driver writes these receipts: the head's inputs, its obligations and the commands BUILD's
    observations let it re-run. None `requirements`: the branch's own."""
    inputs = fingerprint(tree.path)
    root = directory(tree)
    root.mkdir(exist_ok=True)
    head = tree.head()
    if root.is_symlink() or any((root / (head + suffix)).is_symlink() for suffix in (".json", ".sha256")):
        raise sh.DriverError("aliased execution evidence directory or file")
    if requirements is None:
        requirements = grants(tree)
    data = {"sha": head, "inputs": inputs, "grants": requirements,
            "recheck": list(recheck) if recheck is not None else candidates(tree)}
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


def grants(tree):
    """Obligations survive descendant heads; results never do."""
    heads = tree.git("rev-list", "HEAD", "--not", f"origin/{tree.env.default}").out.split()
    values = {}
    for head in heads:
        try:
            data = load(directory(tree) / (head + ".json"))
            value = data.get("grants")
            if data["sha"] == head and isinstance(value, list):
                for row in value:
                    if (isinstance(row, list) and len(row) == 2
                            and isinstance(row[0], str) and isinstance(row[1], dict)):
                        values[json.dumps(row, sort_keys=True)] = row
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return list(values.values())


def candidates(tree):
    """Pre-commit observations authorize a re-run, never a pass at a new head."""
    try:
        data = load(directory(tree) / (tree.head() + ".json"))
        if data["sha"] == tree.head() and data["inputs"] == fingerprint(tree.path):
            return [cmd for cmd in data.get("recheck", []) if isinstance(cmd, str)]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return []
