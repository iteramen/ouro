"""Git-private validation lease and conservative, explicit recovery."""
import ctypes
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path

from . import sh

SETTLE_TIMEOUT = 30

if os.name == "posix":
    import fcntl


def context():
    """Kernel identities scope the lifecycle proof to this boot and process view."""
    return {
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "pid_ns": os.readlink("/proc/self/ns/pid"),
        "mount_ns": os.readlink("/proc/self/ns/mnt"),
    }


def birth(pid):
    # /proc stat's comm can contain spaces and parentheses.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, 65537)
    if len(raw) > 65536:
        raise ValueError("validation ownership record exceeds bound")
    return json.loads(raw)


def write(fd, data):
    raw = json.dumps(data).encode("utf-8")
    if len(raw) > 65536:
        raise sh.DriverError("validation ownership record exceeds bound; retain lock")
    os.lseek(fd, 0, os.SEEK_SET)
    if os.write(fd, raw) != len(raw):
        raise sh.DriverError("short validation lock write; retain lock for inspection")
    os.ftruncate(fd, len(raw))
    os.fsync(fd)


def checked_open(path, flags):
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise sh.DriverError("validation lock must be a private regular file")
    return fd


def same(path, fd):
    info = path.lstat()
    held = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
        raise sh.DriverError("validation lock changed; retain state for inspection")


class Lease:
    def __init__(self, path, head, commands):
        self.path = path
        self.safe = False
        try:
            self.fd = checked_open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            raise sh.DriverError(
                "validation is already running; inspect with ouro validation-lock in the validation worktree; "
                "use --recover only when its lifecycle proof permits recovery")
        except OSError as ex:
            raise sh.DriverError(f"validation lock unavailable; retain state for inspection: {ex}") from ex
        self.data = {"version": 2, "token": uuid.uuid4().hex, "pid": os.getpid(),
                     "batch": hashlib.sha256(json.dumps(commands).encode("utf-8")).hexdigest(),
                     "count": len(commands), "phase": "idle"}
        self.supervised = sys.platform.startswith("linux")
        try:
            if os.name == "posix":
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.supervised:
                self.data.update(context=context(), birth=birth(os.getpid()))
            write(self.fd, self.data)
            self.safe = True
        except OSError as ex:
            os.close(self.fd)
            raise sh.DriverError(
                f"validation ownership visibility unavailable; retain lock for inspection: {ex}") from ex
        except BaseException:
            os.close(self.fd)
            raise

    def execute(self, execute, *args, command_log=None):
        if not self.supervised:
            return execute(*args)
        # Persist uncertainty BEFORE launch. Only the surviving executor can settle
        # this phase; coordinator death is not evidence that command workers ended.
        self.safe = False
        self.data["phase"] = "active"
        self.data.pop("error", None)
        write(self.fd, self.data)
        pid = os.fork()
        if pid == 0:
            try:
                # This specific executor adopts orphaned grandchildren, including
                # Git hooks and double-forked workers. ECHILD, not a ps snapshot or
                # a direct subprocess return, certifies the entire owned lifecycle.
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                    raise OSError(ctypes.get_errno(), "validation subreaper unavailable")
                self.data.update(executor={"pid": os.getpid(), "birth": birth(os.getpid())})
                write(self.fd, self.data)
                try:
                    code = execute(*args)
                except sh.DriverError as ex:
                    code = None
                    self.data["error"] = str(ex)[:4096]
                deadline = time.monotonic() + SETTLE_TIMEOUT
                incomplete = False
                while True:
                    try:
                        child, _ = os.waitpid(-1, os.WNOHANG)
                    except ChildProcessError:
                        break
                    # Even an already-dead adopted child lacks completion proof.
                    # Settlement permits recovery, never acceptance of its parent's result.
                    incomplete = True
                    if time.monotonic() >= deadline:
                        raise sh.DriverError("owned validation helpers remain unsettled; retain active lock for inspection")
                    if child == 0:
                        time.sleep(0.02)
                if incomplete:
                    message = "validation parent returned with owned descendants; incomplete execution"
                    if command_log is None:
                        self.data["error"] = message + "; helper snapshot refused"
                    else:
                        from .validation import OUTPUT_LIMIT
                        raw = command_log.read_bytes()
                        note = ("\n" + message + "\n").encode("utf-8")[:OUTPUT_LIMIT]
                        command_log.write_bytes(raw[:OUTPUT_LIMIT - len(note)] + note)
                        code = 1
                self.data.update(phase="settled", code=code)
                write(self.fd, self.data)
                os._exit(0)
            except BaseException:
                os._exit(1)  # leave active; never certify unknown descendants
        _, status = os.waitpid(pid, 0)
        if status != 0:
            raise sh.DriverError("validation executor interrupted; retain lock and inspect descendants")
        self.data = read(self.fd)
        if self.data.get("phase") != "settled":
            raise sh.DriverError("validation executor has no settled lifecycle proof")
        self.safe = True
        if "error" in self.data:
            raise sh.DriverError(self.data["error"])
        return self.data["code"]

    def close(self):
        try:
            if self.safe:
                same(self.path, self.fd)
                self.path.unlink()
        finally:
            os.close(self.fd)


def inspect(root, recover=False, helpers=None):
    """No process is signalled; only affirmative idle/settled proof permits quarantine."""
    path = root / "lock"
    if root.is_symlink():
        raise sh.DriverError("validation state must be Git-private directories")
    if not path.exists() and not path.is_symlink():
        return {"status": "absent"}
    try:
        fd = checked_open(path, os.O_RDWR)
    except OSError as ex:
        raise sh.DriverError(f"validation lock inspection refused; retain evidence: {ex}") from ex
    try:
        try:
            data = read(fd)
        except (ValueError, UnicodeError):
            data = {"status": "unreadable ownership; manual lifecycle inspection required"}
        if not recover:
            return data
        if not sys.platform.startswith("linux"):
            raise sh.DriverError("validation lock recovery requires native Linux lifecycle proof")
        from .validation import check_platform, process_table
        check_platform()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise sh.DriverError("validation lease is live; coordinator or executor still owns it")
        # The owner may have advanced idle -> active between inspection and lease
        # acquisition. Only the final state read under the acquired lease is proof.
        try:
            data = read(fd)
        except (ValueError, UnicodeError):
            data = {}
        if (data.get("version") != 2 or data.get("context") != context()
                or data.get("phase") not in ("idle", "settled")
                or not isinstance(data.get("token"), str) or not re.fullmatch(r"[0-9a-f]{32}", data["token"])
                or type(data.get("pid")) is not int or data["pid"] <= 0 or not isinstance(data.get("birth"), str)):
            raise sh.DriverError(
                "insufficient lifecycle proof; retain lock and attempts. Empty/legacy locks, boot/namespace "
                "mismatch and interrupted executors require manual inspection of the prior execution and descendants")
        # A missing snapshot never suffices: require a working observer AND the
        # kernel lease AND a durable no-command-in-flight transition.
        table = process_table(helpers)
        if os.getpid() not in table:
            raise sh.DriverError("validation process visibility unavailable; retain validation lock")
        if data["pid"] in table:
            try:
                identity = birth(data["pid"])
            except OSError as ex:
                raise sh.DriverError("owner visibility unavailable; retain validation lock") from ex
            detail = "owner still visible" if identity == data["birth"] else "owner PID reused"
            raise sh.DriverError(detail + "; retain validation lock for inspection")
        same(path, fd)
        archive = root / ("recovered-" + data["token"] + ".json")
        # Link without replacing prior evidence, then unlink under the lease.
        os.link(path, archive, follow_symlinks=False)
        path.unlink()
        return {"status": "recovered", "evidence": str(archive)}
    except (OSError, ValueError, TypeError, AttributeError) as ex:
        raise sh.DriverError(f"validation lock recovery refused; retain evidence: {ex}") from ex
    finally:
        os.close(fd)
