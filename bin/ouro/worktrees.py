"""Conservative, local-only collection of affirmatively driver-created worktrees."""
import ctypes
import hashlib
import json
import os
import stat
import time
from pathlib import Path

from . import sh


def linked(path):
    return path.is_symlink() or path.is_junction()


def identity(path):
    info = path.lstat()
    if linked(path):
        raise ValueError("linked path")
    return [info.st_dev, info.st_ino]


def git(env, *args, cwd=None):
    r = env.run([sh.which("git"), "-c", "core.quotepath=false", "-c", "core.fsmonitor=false",
                 "-c", "core.untrackedCache=false", *args], cwd=cwd or env.main, timeout=30)
    if r.code:
        raise ValueError("Git evidence unavailable")
    return r.out


def registrations(env):
    entries = []
    for block in git(env, "worktree", "list", "--porcelain", "-z").strip("\0").split("\0\0"):
        entry = dict(field.split(" ", 1) if " " in field else (field, True) for field in block.split("\0"))
        if "worktree" in entry:
            entries.append(entry)
    return entries


def location(env, path):
    main, path = env.main.resolve(), Path(path).absolute()
    root = main / ".claude" / "worktrees"
    if not path.is_relative_to(root) or path == root or path.resolve() != path:
        raise ValueError("outside the ordinary worktree root or retargeted")
    for parent in [path, *path.parents]:
        if linked(parent):
            raise ValueError("linked worktree path")
        if parent == main:
            break
    common = Path(git(env, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    if common.resolve() != common or linked(common):
        raise ValueError("linked Git common directory")
    private = common / "ouro-worktrees"
    if private.exists() and (linked(private) or not private.is_dir()):
        raise ValueError("unsafe private worktree metadata")
    key = hashlib.sha256(os.fsencode(str(path))).hexdigest()
    return path, common, private, key


def read_record(path):
    before = path.lstat()
    if linked(path) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 8192:
        raise ValueError("unsafe creation record")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        held = os.fstat(fd)
        if (not stat.S_ISREG(held.st_mode) or held.st_nlink != 1
                or (before.st_dev, before.st_ino) != (held.st_dev, held.st_ino)):
            raise ValueError("creation record changed")
        raw = os.read(fd, 8193)
        if len(raw) > 8192:
            raise ValueError("creation record exceeds bound")
        return json.loads(raw)
    finally:
        os.close(fd)


def write_record(path, data, *, create=False):
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    before = None if create else identity(path)
    fd = os.open(path, flags | (os.O_CREAT | os.O_EXCL if create else 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or before not in (None, [info.st_dev, info.st_ino]):
            raise ValueError("creation record changed")
        raw = json.dumps(data, sort_keys=True).encode()
        if len(raw) > 8192 or os.write(fd, raw) != len(raw):
            raise ValueError("creation record write incomplete")
        os.ftruncate(fd, len(raw))
        os.fsync(fd)
    finally:
        os.close(fd)


class Lease:
    """Cooperating Ouro processes only; interrupted users leave an active record."""
    def __init__(self, private, key):
        self.record_identity = None
        self.may_settle = False
        private.mkdir(mode=0o700, exist_ok=True)
        path = private / (key + ".lock")
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(self.fd, b"0")
        except FileExistsError:
            before = identity(path)
            self.fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            if before != [os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino]:
                os.close(self.fd)
                raise ValueError("activity lock changed")
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe activity lock")
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)


def provenance(env, path, branch):
    path, common, _, _ = location(env, path)
    admin = Path(git(env, "rev-parse", "--absolute-git-dir", cwd=path).strip())
    if admin.resolve() != admin or not admin.is_relative_to(common / "worktrees"):
        raise ValueError("unexpected worktree administration")
    rows = [r for r in registrations(env) if Path(r["worktree"]).resolve() == path]
    if len(rows) != 1 or rows[0].get("branch") != "refs/heads/" + branch or any(k in rows[0] for k in ("locked", "prunable", "detached")):
        raise ValueError("registration changed or locked")
    return {"version": 1, "path": str(path), "common": str(common), "branch": branch,
            "directory": identity(path), "admin": str(admin), "admin_identity": identity(admin),
            "gitfile": identity(path / ".git")}


def use(env, path):
    """Acquire before add/resume, without manufacturing creation evidence for old trees."""
    if env.dry:
        return
    try:
        path, _, private, key = location(env, path)
    except ValueError:
        sh.say("  worktree cleanup: linked/nonordinary tree remains unmarked and ineligible")
        return
    leases = getattr(env, "_worktree_leases", {})
    if str(path) in leases:
        return
    try:
        lease = Lease(private, key)
    except (OSError, ValueError) as exc:
        raise sh.Refused("worktree activity lease unavailable or held; preserve this tree") from exc
    record = private / (key + ".json")
    try:
        if record.exists():
            data = read_record(record)
            try:
                current = identity(path)
            except FileNotFoundError:
                stale = True
            else:
                stale = (current != data["directory"] or identity(path / ".git") != data["gitfile"]
                         or identity(Path(data["admin"])) != data["admin_identity"])
            if stale:
                sh.say("  worktree cleanup: stale creation record retained; this tree stays ineligible")
            else:
                expected = provenance(env, path, data["branch"])
                if any(data.get(k) != v for k, v in expected.items()):
                    raise ValueError("creation identity changed")
                lease.may_settle = data.get("settled") is True
                data["settled"] = False
                write_record(record, data)
                lease.record_identity = identity(record)
    except (OSError, ValueError, KeyError, TypeError, sh.DriverError) as exc:
        lease.close()
        raise sh.Refused("worktree creation identity unavailable; preserve this tree") from exc
    leases[str(path)] = (lease, record)
    env._worktree_leases = leases
    env._worktrees_settled = False


def created(env, path, branch, issue, part):
    """Called only immediately after this invocation's successful worktree add."""
    held = getattr(env, "_worktree_leases", {}).get(str(Path(path).absolute()))
    if held is None:
        return  # Optional collection never retrostamps a legacy or linked tree.
    try:
        data = provenance(env, path, branch)
        data.update(issue=issue, part=part, settled=False)
        write_record(held[1], data, create=True)
        held[0].record_identity = identity(held[1])
        held[0].may_settle = True
    except (OSError, ValueError, sh.DriverError):
        sh.say("  worktree cleanup: creation evidence unavailable; this new tree stays ineligible")


def close(env):
    for lease, record in getattr(env, "_worktree_leases", {}).values():
        try:
            if getattr(env, "_worktrees_settled", False) and lease.record_identity is not None and lease.may_settle:
                if identity(record) != lease.record_identity:
                    raise ValueError("creation record replaced")
                data = read_record(record)
                expected = provenance(env, Path(data["path"]), data["branch"])
                if any(data.get(k) != v for k, v in expected.items()):
                    raise ValueError("creation identity changed")
                data["settled"] = True
                write_record(record, data)
        except (OSError, ValueError, KeyError, TypeError, sh.DriverError):
            sh.say("  worktree cleanup: shutdown evidence uncertain; creation record retained")
        finally:
            lease.close()
    env._worktree_leases = {}


def resources(path):
    """Immediate conflict/observer precheck, not exclusion of external Git/process races."""
    deadline = time.monotonic() + 5
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        paths = [path]
        def unavailable(error):
            raise ValueError("resource observer unavailable")
        for directory, dirs, files in os.walk(path, onerror=unavailable):
            paths.extend(Path(directory) / name for name in [*dirs, *files])
            if time.monotonic() > deadline or len(paths) > 100000:
                raise ValueError("resource observation exceeds bound")
        handles = []
        try:
            for item in paths:
                if time.monotonic() > deadline:
                    raise ValueError("resource observation exceeds bound")
                if linked(item):
                    raise ValueError("linked contents")
                if item == path / ".git":
                    continue
                handle = kernel.CreateFileW(str(item), 0x10000, 0, None, 3, 0x02000000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ValueError("resource delete conflict or observer unavailable")
                handles.append(handle)
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)
        return
    _process_resources(path, Path("/proc"))


def _process_resources(path, proc):
    """Retain visible users; unreadable external state is a reported limitation."""
    deadline = time.monotonic() + 5
    if not proc.is_dir():
        raise ValueError("resource observer unavailable")
    limited = False

    def unreadable():
        nonlocal limited
        if not limited:
            sh.say("  worktree cleanup: external process visibility limited; unreadable processes may still be active")
            limited = True

    def link(entry):
        try:
            target = Path(os.readlink(entry))
        except (ProcessLookupError, FileNotFoundError):
            return
        except PermissionError:
            unreadable()
            return
        if target.is_absolute() and target.is_relative_to(path):
            raise ValueError("observed external resource")

    for process in proc.iterdir():
        if time.monotonic() > deadline:
            raise ValueError("resource observation exceeds bound")
        if not process.name.isdecimal():
            continue
        try:
            for entry in (process / "cwd", process / "exe"):
                link(entry)
            try:
                for entry in (process / "fd").iterdir():
                    if time.monotonic() > deadline:
                        raise ValueError("resource observation exceeds bound")
                    link(entry)
            except PermissionError:
                unreadable()
            with (process / "maps").open("rb") as stream:
                maps = stream.read(1048577)
            if len(maps) > 1048576:
                raise ValueError("resource observation exceeds bound")
            for mapping in maps.splitlines():
                fields = mapping.split(None, 5)
                if len(fields) == 6 and os.fsdecode(fields[5]).startswith(str(path) + os.sep):
                    raise ValueError("observed external mapped resource")
        except (ProcessLookupError, FileNotFoundError):
            continue
        except PermissionError:
            unreadable()


def eligible(env, path, record):
    data = read_record(record)
    branch = data["branch"]
    expected = provenance(env, path, branch)
    if any(data.get(k) != v for k, v in expected.items()) or data.get("settled") is not True:
        raise ValueError("creation identity mismatched or prior run unsettled")
    protected = env.data["repo"].get("protected_branch_prefixes", [])
    if (branch == env.default or branch in ("main", "master") or any(branch.startswith(p) for p in protected)
            or path == env.main.resolve() or Path.cwd().resolve().is_relative_to(path)):
        raise ValueError("protected or current checkout")
    rows = registrations(env)
    if any(Path(r["worktree"]).resolve() != path and
           (Path(r["worktree"]).resolve().is_relative_to(path)
            or path.is_relative_to(Path(r["worktree"]).resolve()) and Path(r["worktree"]).resolve() != env.main.resolve()
            or r.get("branch") == "refs/heads/" + branch) for r in rows):
        raise ValueError("nested or shared registration")
    admin = Path(data["admin"])
    if (admin / "ouro-validation" / "lock").exists() or (admin / "ouro-validation" / "lock").is_symlink():
        raise ValueError("caller-validation state retained")
    flags = git(env, "ls-files", "-v", "-z", cwd=path).split("\0")
    if any(row and (row[0].islower() or row[0] == "S") for row in flags):
        raise ValueError("hidden tracked inputs")
    if git(env, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored", cwd=path):
        raise ValueError("tracked, untracked or ignored contents")
    head = git(env, "rev-parse", "HEAD", cwd=path).strip()
    remote = git(env, "ls-remote", "--heads", "origin", "refs/heads/" + branch).splitlines()
    if remote != [head + "\trefs/heads/" + branch]:
        raise ValueError("HEAD not exactly retained on origin")
    prs = env.gh_json("pr", "list", "--head", branch, "--state", "all", "--limit", "1000", "--json",
                      "state,headRefOid,headRefName,headRepository,headRepositoryOwner")
    if (not isinstance(prs, list) or len(prs) != 1 or not isinstance(prs[0], dict)
            or prs[0].get("state") not in ("CLOSED", "MERGED")):
        raise ValueError("open, missing, ambiguous or truncated pull-request evidence")
    pr = prs[0]
    if not isinstance(pr.get("headRepositoryOwner"), dict) or not isinstance(pr.get("headRepository"), dict):
        raise ValueError("pull-request repository evidence unavailable")
    repository = str((pr.get("headRepositoryOwner") or {}).get("login", "")) + "/" + str((pr.get("headRepository") or {}).get("name", ""))
    if (pr.get("headRefOid") != head or pr.get("headRefName") != branch
            or repository.lower() != env.slug.lower()):
        raise ValueError("pull-request head/repository mismatch")
    return data, head


def sweep(env, only=None):
    """Best effort: uncertainty retains candidates and never denies unrelated delivery."""
    if env.dry:
        return
    try:
        rows = registrations(env)
    except (OSError, ValueError, sh.DriverError):
        sh.say("  worktree cleanup: kept candidates; registration unavailable")
        return
    for row in rows:
        path = Path(row["worktree"])
        if only is None and not path.is_relative_to(env.main.resolve() / ".claude" / "worktrees"):
            continue
        if path == env.main.resolve() or only is not None and path != Path(only).absolute():
            continue
        lease = None
        removed = False
        try:
            path, _, private, key = location(env, path)
            record = private / (key + ".json")
            if not record.exists():
                raise ValueError("no driver creation record")
            if str(path) in getattr(env, "_worktree_leases", {}):
                raise ValueError("active driver worktree")
            lease = Lease(private, key)
            data, head = eligible(env, path, record)
            resources(path)
            if eligible(env, path, record) != (data, head):
                raise ValueError("candidate changed before removal")
            removed = True
            git(env, "worktree", "remove", str(path))
            if path.exists() or any(Path(r["worktree"]) == path for r in registrations(env)):
                raise ValueError("partial worktree removal; branch retained")
            sh.say(f"  worktree cleanup: removed {path.name}")
            branch = data["branch"]
            if any(r.get("branch") == "refs/heads/" + branch for r in registrations(env)):
                raise ValueError("branch is checked out; local branch retained")
            git(env, "update-ref", "-d", "refs/heads/" + branch, head)
            sh.say(f"  worktree cleanup: deleted local branch {branch}")
            record.unlink()
        except (OSError, ValueError, KeyError, TypeError, sh.DriverError) as exc:
            reason = str(exc) if type(exc) is ValueError else "ownership/evidence unavailable"
            outcome = "local cleanup incomplete; inspect residual directory/refs/metadata" if removed else "kept"
            sh.say(f"  worktree cleanup: {outcome} {path.name}: {reason}")
        finally:
            if lease:
                lease.close()


class Tree:
    """One checkout the driver reads and gates: its Git, its head, and its diff against the default branch."""
    def __init__(self, env, path, branch):
        self.env, self.path, self.branch = env, path, branch

    def git(self, *args, check=True):
        return self.env.git(*args, cwd=self.path, check=check)

    def head(self):
        return self.git("rev-parse", "HEAD").out.strip()

    def admin(self):
        return Path(self.git("rev-parse", "--absolute-git-dir").out.strip())

    def origin_tip(self):
        out = self.env.git("ls-remote", "--heads", "origin", f"refs/heads/{self.branch}", check=False).out.split()
        return out[0] if out else ""

    def changed(self):
        out = self.git("diff", "--name-only", "-z", f"origin/{self.env.default}...HEAD").out
        return [p for p in out.split("\0") if p]

    def touches(self, entries):
        """Whether the branch's diff touches any of `entries`; a diff git cannot read counts as touched, so its gate runs."""
        r = self.git(*self.env.ob.changed_diff(f"origin/{self.env.default}", "HEAD", entries), check=False)
        return r.code != 0 or bool(r.out.strip("\0"))
