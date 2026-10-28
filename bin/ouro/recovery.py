"""Explicit selected-file recovery inputs; Git/GitHub remain the run's only state machine."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile

from . import sh, validation
from .records import FIX_CAP, needs_fix

REFERENCE = ".ouro-build-recovery"
PROTECTED = {".git", ".claude", ".codex", ".agents", ".aws", ".github", REFERENCE}
MAX_FILES, MAX_FILE, MAX_TOTAL = 32, 1024 * 1024, 4 * 1024 * 1024


def read_file(root, parts, limit):
    """Open relative to held directory FDs; never follow links or read beyond a bound."""
    with ExitStack() as owned:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        owned.callback(os.close, directory)
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            owned.callback(os.close, directory)
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("nonregular, aliased or oversized recovery input")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit
                    or not os.path.samestat(info, before)):
                raise ValueError("recovery input identity changed")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if (len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError("recovery input changed while reading")
            return data


def snapshot(tree, selections):
    """Read only explicitly selected changed regular files, never aliases or instructions."""
    if not 1 <= len(selections) <= MAX_FILES or len(set(selections)) != len(selections):
        raise sh.Refused("BUILD recovery requires 1-32 distinct explicitly selected paths")
    root = tree.path.resolve(strict=True)
    changed = set(tree.git("diff", "--name-only", "-z", "HEAD", "--").out.split("\0"))
    changed.update(tree.git("ls-files", "--others", "--exclude-standard", "-z").out.split("\0"))
    files, total = {}, 0
    for value in selections:
        path = PurePosixPath(value)
        if (path.is_absolute() or path.as_posix() != value or "\\" in value
                or any(part in (".", "..") for part in path.parts) or not path.parts
                or any(part.lower() in PROTECTED for part in path.parts)
                or path.name.lower() in ("agents.md", "claude.md") or value not in changed):
            raise sh.Refused(f"BUILD recovery path is not an eligible changed source file: {value}")
        try:
            data = read_file(root, path.parts, MAX_FILE)
        except FileNotFoundError:
            files[value] = None  # An explicitly selected tracked deletion; rechecked before use.
            continue
        except (OSError, ValueError):
            raise sh.Refused("BUILD recovery refuses aliased, oversized or changing source paths") from None
        try:
            data.decode("utf-8")
        except UnicodeError:
            raise sh.Refused("BUILD recovery references support UTF-8 source files only") from None
        if b"\0" in data:
            raise sh.Refused("BUILD recovery references do not accept binary files")
        total += len(data)
        if total > MAX_TOTAL:
            raise sh.Refused("BUILD recovery references exceed the 4 MiB total bound")
        files[value] = data
    return files


def idle_source(tree):
    """No original process is killed. Known/uncertain caller leases always refuse."""
    validation.check_platform()
    if not sys.platform.startswith("linux"):
        raise sh.Refused("BUILD recovery needs native Linux process observation; preserve the original for manual inspection")
    admin = tree.admin()
    state = admin / "ouro-validation"
    if state.is_symlink() or (state / "lock").exists() or (state / "lock").is_symlink():
        raise sh.Refused("BUILD recovery preserves a live or uncertain validation lease; inspect ouro validation-lock first")
    root = tree.path.resolve(strict=True)
    unknown = False
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            cwd = (entry / "cwd").resolve(strict=True)
            if cwd == root or cwd.is_relative_to(root):
                raise sh.Refused("BUILD recovery found a live process in the original worktree; leave it running and retry after it ends")
        except FileNotFoundError:
            continue  # The observer raced an exited process.
        except PermissionError:
            # Nondumpable session helpers can share this UID. Their unreadable
            # cwd is unknown, never proof of inactivity. Isolation preserves all
            # original processes/files; only the new checkout can produce proof.
            unknown = True
    if unknown:
        sh.say("Original process visibility is incomplete; all original processes are preserved and provide no recovery execution proof.")
    return admin


def exclude_reference(checkout):
    """Append only through held private metadata directories, never canonicalized aliases."""
    try:
        with ExitStack() as owned:
            directory = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            owned.callback(os.close, directory)
            directory = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            owned.callback(os.close, directory)
            try:
                os.mkdir("info", 0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open("info", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            owned.callback(os.close, directory)
            fd = os.open("exclude", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            owned.callback(os.close, fd)
            info = os.fstat(fd)
            named = os.stat("exclude", dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not os.path.samestat(info, named):
                raise ValueError()
            data = ("\n/" + REFERENCE + "/\n").encode("utf-8")
            if os.write(fd, data) != len(data):
                raise ValueError()
    except (OSError, ValueError):
        raise sh.Refused("BUILD recovery cannot prove its private ignore-file identity; preserve the checkout for inspection") from None


def prepare(run, pr, selections):
    """Create a private clean checkout; never move, reset or detach the dirty original. Returns (checkout, files, source record)."""
    if any(name in os.environ for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                                           "GIT_OBJECT_DIRECTORY", "GIT_SHALLOW_FILE")):
        raise sh.Refused("BUILD recovery cannot isolate repository directory/file overrides; preserve the original for manual inspection")
    if run.flow == "direct" or not pr or pr.get("state") != "OPEN":
        raise sh.Refused("BUILD recovery requires an open issue pull request on the normal worktree path")
    tree = run.tree
    source = tree.path.resolve(strict=True)
    if not source.is_relative_to(run.env.main.resolve(strict=True)):
        raise sh.Refused("BUILD recovery source resolves outside the repository")
    head = tree.head()
    if tree.git("branch", "--show-current").out.strip() != run.branch:
        raise sh.Refused("BUILD recovery source is not on this issue's branch")
    if pr.get("headRefOid") != head or tree.origin_tip() != head:
        raise sh.Refused("BUILD recovery source differs from the current PR/remote head; reconcile it before retrying")
    records = run.records(pr)
    at_head = [record for record in records if record["sha"] == head]
    if not at_head or not needs_fix(at_head[-1]):
        raise sh.Refused("BUILD recovery requires current-head review findings, never a new acceptance record")
    spent = max(0, int(tree.git("rev-list", "--count", "--no-merges", f"origin/{run.env.default}..HEAD").out.strip()) - 1)
    if spent >= FIX_CAP:
        raise sh.Refused("BUILD recovery cannot bypass the fix-round cap; keep the normal manual handoff")
    admin = idle_source(tree)
    files = snapshot(tree, selections)
    old_main, old_worktree = run.env.main, tree.path
    parent = admin / "ouro-build-recovery"
    if parent.is_symlink():
        raise sh.Refused("BUILD recovery state must be Git-private directories")
    parent.mkdir(mode=0o700, exist_ok=True)
    # Each invocation owns one preserved checkout. Its Git branch, not a recovery
    # stage marker, is what a later ordinary run resumes from the printed path.
    attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=parent))
    checkout = attempt / "checkout"
    rows = [dict(path=name, deleted=data is None,
                 sha256=hashlib.sha256(data).hexdigest() if data is not None else None)
            for name, data in files.items()]
    original = dict(issue=run.n, part=run.k, branch=run.branch, head=head,
                    source=str(old_worktree), selected=list(files), files=rows)
    (attempt / "source.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
    for name, data in files.items():
        if data is not None:
            archived = attempt / "files" / name
            archived.parent.mkdir(parents=True, exist_ok=True)
            archived.write_bytes(data)  # Outside the new worker and later worktree cleanup.
    sh.say(f"BUILD recovery checkout: {checkout}; original worktree and evidence remain at {old_worktree}")
    run.env.git("clone", "--template=", "--no-hardlinks", "--no-checkout", str(old_main), str(checkout))
    origin = run.env.git("remote", "get-url", "origin").out.strip()
    run.env.git("remote", "set-url", "origin", origin, cwd=checkout)
    effective = Path(run.env.git("rev-parse", "--show-toplevel", cwd=checkout).out.strip())
    if not effective.is_absolute() or effective.resolve() != checkout.resolve():
        raise sh.Refused("BUILD recovery Git configuration redirects the private checkout; preserve it for inspection")
    run.env.git("checkout", "-q", run.env.default, cwd=checkout)
    idle_source(tree)
    if tree.head() != head or snapshot(tree, selections) != files or tree.origin_tip() != head:
        raise sh.Refused("BUILD recovery inputs moved during preparation; private checkout and original are preserved")
    return checkout, files, original


def offer(tree, checkout, files, original):
    """Place the selected files in the private checkout's issue worktree as references, never as its content."""
    reference = tree.path / REFERENCE
    if reference.exists() or tree.git("ls-tree", "--name-only", "HEAD", "--", REFERENCE).out:
        raise sh.Refused("BUILD recovery reference path is already occupied; preserve it for inspection")
    exclude_reference(checkout)
    reference.mkdir(mode=0o700)
    for name, data in files.items():
        if data is not None:
            target = reference / "files" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (reference / "source.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
    tree.git("config", "--local", "ouro.recoveryReference", REFERENCE)
    validation.clean(tree, original["head"])
    sh.say("Recovery inputs are references only. Normal committed-head validation, BUILD, review and shipping still apply.")


def brief(tree, n, k):
    """A preserved input can assist a normal resume; it never chooses a workflow stage."""
    root = tree.path / REFERENCE
    if not root.exists():
        return ""
    # Local Git configuration points only to an owner-created input, never to a
    # workflow stage. Ordinary repositories with this directory keep their behavior.
    approved = tree.git("config", "--local", "--get", "ouro.recoveryReference", check=False)
    if approved.code != 0 or approved.out.strip() != REFERENCE:
        return ""
    if not sys.platform.startswith("linux"):
        raise sh.DriverError("BUILD recovery reference validation requires native Linux")
    try:
        data = json.loads(read_file(tree.path, (REFERENCE, "source.json"), 65536))
        if data["head"] != tree.head():
            return ""  # A new head expires old inputs before consulting archived metadata.
        if data != json.loads(read_file(tree.env.main.parent, ("source.json",), 65536)):
            raise ValueError()
        if (data["issue"], data["part"], data["branch"]) != (n, k, tree.branch):
            raise ValueError()
        rows = data["files"]
        if not 1 <= len(rows) <= MAX_FILES:
            raise ValueError()
        for row in rows:
            value = PurePosixPath(row["path"])
            if (value.is_absolute() or value.as_posix() != row["path"] or "\\" in row["path"]
                    or any(part in (".", "..") or part.lower() in PROTECTED for part in value.parts)
                    or value.name.lower() in ("agents.md", "claude.md")):
                raise ValueError()
            if not row["deleted"]:
                if hashlib.sha256(read_file(tree.path, (REFERENCE, "files", *value.parts), MAX_FILE)).hexdigest() != row["sha256"]:
                    raise ValueError()
    except (OSError, KeyError, TypeError, ValueError):
        raise sh.DriverError("BUILD recovery reference is malformed or changed; preserve it for inspection") from None
    return ("\n\nThe invoking person explicitly selected partial edits preserved under " + REFERENCE +
            "/source.json and files/. Read that manifest and relevant selected files before fixing the findings. "
            "They are recovery inputs, not accepted BUILD output or execution proof. Apply only changes needed for the approved issue; "
            "do not overwrite newer corrections or copy these ignored references into the commit. The original dirty checkout, "
            "unselected user edits and old attempt evidence remain untouched. Return a genuine BUILD result and retain required validation.")
