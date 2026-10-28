"""Bounded harness subprocesses; close owned process trees on completion or cancellation."""
import errno
import os
import signal
import subprocess
import sys
import tempfile
import time

from pathlib import Path
import re

class DriverError(Exception):
    """Exit 1: a script failed, a node answered nothing parseable, the platform refused."""


class R:
    def __init__(self, code, out, err, argv):
        self.code, self.out, self.err, self.argv = code, out, err, argv

    @property
    def text(self):
        return (self.out + self.err).strip()


def child_env(overrides=None):
    """A None override removes an inherited variable, including Windows case aliases."""
    # No multicore-JIT profile: parallel pwsh starts corrupt the shared one (an upstream race).
    values = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "DOTNET_MultiCoreJitMinNumCpus": "65535"}
    overrides = overrides or {}
    values.update({key: value for key, value in overrides.items() if value is not None})
    # Apply removals last so a later Windows case alias cannot restore the key.
    for key, value in overrides.items():
        if value is None:
            for inherited in list(values):
                if inherited == key or (os.name == "nt" and inherited.casefold() == key.casefold()):
                    del values[inherited]
    return values


def check_platform():
    """Caller validation needs a lifecycle boundary, independent of command environment."""
    if os.name == "nt":
        raise DriverError("validation process lifecycle accounting unavailable on Windows")
    if os.name != "posix":
        raise DriverError("validation process lifecycle accounting requires native Linux")
    try:
        kernel = os.uname()
        if kernel.sysname != "Linux":
            raise DriverError("validation process lifecycle accounting requires native Linux")
        identity = kernel.release + " " + kernel.version
        # Kernel-owned facts, not WSL_* environment variables or the first executable:
        # a native launcher can activate the Windows broker at any depth.
        for name in ("osrelease", "version"):
            identity += " " + (Path("/proc/sys/kernel") / name).read_text(encoding="utf-8")
        wsl = bool(re.search(r"microsoft|wsl", identity, re.IGNORECASE))
        for marker in ("/proc/sys/fs/binfmt_misc/WSLInterop", "/run/WSL"):
            try:
                Path(marker).stat()
                wsl = True
            except FileNotFoundError:
                pass
    except (OSError, UnicodeError) as ex:
        raise DriverError(
            "validation platform visibility unavailable; restore kernel/proc visibility before retrying") from ex
    if wsl:
        raise DriverError(
            "validation lifecycle accounting unavailable on WSL: Windows interoperability can escape "
            "the POSIX process table, including through native launchers; run caller validation on "
            "a native Linux host. No proven child-only interop boundary is supported; unshare availability "
            "or WSL environment settings are not containment proof")


def process_table(env=None):
    """Read ownership edges and birth identities; never select by name/group."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,lstart="], capture_output=True,
            encoding="utf-8", errors="replace", timeout=5, check=True, env=env)
        table = {}
        for line in result.stdout.splitlines():
            pid, parent, born = line.strip().split(None, 2)
            table[int(pid)] = (int(parent), born)
        if os.getpid() not in table:
            raise ValueError("process visibility unavailable")
        return table
    except (OSError, subprocess.SubprocessError, ValueError) as ex:
        raise DriverError("validation process visibility unavailable") from ex


def descendants(table, owned):
    # Remember escaped sessions while their parent edge is still visible. A PID
    # alone is never authority to signal a process after PID reuse.
    changed = True
    while changed:
        changed = False
        for pid, (parent, born) in table.items():
            if (pid not in owned and parent in owned and parent in table
                    and table[parent][1] == owned[parent]):
                owned[pid] = born
                changed = True


def terminate_owned(p, owned, env=None):
    # Freeze owned parents before collecting another generation. Children may
    # create sessions, so process-group signals cannot implement tree cleanup.
    stopped = set()
    while True:
        table = process_table(env)
        descendants(table, owned)
        live = {pid for pid, born in owned.items()
                if pid in table and table[pid][1] == born}
        fresh = live - stopped
        if not fresh:
            break
        for pid in fresh:
            try:
                os.kill(pid, signal.SIGSTOP)
            except ProcessLookupError:
                pass
        stopped.update(fresh)
    for pid in reversed(list(owned)):
        if pid in live:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    p.wait()



LIMIT = 8 * 1024 * 1024
WINDOWS_EXIT_WAIT_MS = 5000


def _wait_windows_exit(p):
    """A cached Popen exit code is not proof its native process has finished."""
    import _winapi

    try:
        status = _winapi.WaitForSingleObject(int(p._handle), WINDOWS_EXIT_WAIT_MS)
    except OSError as ex:
        raise DriverError(f"cannot verify runtime process exit: {ex}") from ex
    if status != _winapi.WAIT_OBJECT_0:
        raise DriverError(f"runtime process exit not established within {WINDOWS_EXIT_WAIT_MS}ms (wait result {status})")
    p.wait(timeout=0)


def feed(stdin, pending):
    """Write what a nonblocking pipe takes now; close it once drained or when its reader has gone."""
    try:
        if pending:
            pending = pending[os.write(stdin.fileno(), pending[:65536]):]
        if not pending:
            stdin.close()
    except BlockingIOError:
        pass
    except OSError as ex:
        # Windows reports a reader's early close as EINVAL too, as subprocess.communicate() does.
        if not isinstance(ex, BrokenPipeError) and ex.errno != errno.EINVAL:
            raise
        stdin.close()
    return pending


def run(argv, cwd=None, prompt=None, timeout=600, env=None, observe=None, output_limit=None, stderr_limit=None):
    output_limit = LIMIT if output_limit is None else output_limit
    stderr_limit = LIMIT if stderr_limit is None else stderr_limit
    environment = child_env(env)
    job = None
    owned = {}
    if os.name != "nt":
        process_table(environment)  # refuse before starting when ownership cannot be observed
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        source.write((prompt or "").encode("utf-8"))
        source.seek(0)

        def bound():
            if (os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > output_limit
                    or os.fstat(err.fileno()).st_size > stderr_limit):
                raise DriverError("runtime output limit exceeded")
        try:
            # Implicit Windows batch launchers lose file-backed stdin. A pipe
            # preserves it; nonblocking writes below keep backpressure bounded.
            p = subprocess.Popen(argv, cwd=cwd, env=environment,
                                 stdin=subprocess.PIPE if os.name == "nt" else source,
                                 stdout=out, stderr=err, bufsize=0,
                                 start_new_session=os.name != "nt",
                                 creationflags=0x00000004 if os.name == "nt" else 0)  # CREATE_SUSPENDED
        except OSError as ex:
            raise DriverError(f"cannot start runtime: {ex}") from ex
        try:
            if os.name == "nt":
                os.set_blocking(p.stdin.fileno(), False)
                pending = memoryview((prompt or "").encode("utf-8"))
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.CreateJobObjectW.restype = wintypes.HANDLE
                kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
                kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
                job = kernel.CreateJobObjectW(None, None)
                class BasicLimits(ctypes.Structure):
                    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                                ("max_working_set", ctypes.c_size_t), ("active", wintypes.DWORD),
                                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
                class ExtendedLimits(ctypes.Structure):
                    _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
                limits = ExtendedLimits()
                limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if (not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                        or not kernel.AssignProcessToJobObject(job, int(p._handle))):
                    raise DriverError("runtime process-tree containment unavailable")
                ntdll = ctypes.WinDLL("ntdll")
                ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
                if ntdll.NtResumeProcess(int(p._handle)) != 0:
                    raise DriverError("runtime process could not resume")
            start = time.monotonic()
            while p.poll() is None:
                if os.name != "nt":
                    table = process_table(environment)
                    if p.pid in table:
                        owned.setdefault(p.pid, table[p.pid][1])
                    descendants(table, owned)
                if time.monotonic() - start >= timeout:
                    raise DriverError(f"runtime timed out after {timeout}s")
                bound()
                if os.name == "nt" and not p.stdin.closed:
                    pending = feed(p.stdin, pending)
                time.sleep(0.03)
            bound()
            out.seek(0)
            err.seek(0)
            return R(p.returncode, out.read(output_limit).decode("utf-8", "replace"),
                     err.read(stderr_limit).decode("utf-8", "replace"), argv)
        finally:
            try:
                if p.stdin is not None:
                    p.stdin.close()
                if os.name == "nt":
                    primary = str(sys.exception()) if sys.exception() is not None else None
                    if job:
                        kernel.TerminateJobObject(job, 1)
                        kernel.CloseHandle(job)
                    if p.poll() is None:
                        p.kill()
                    try:
                        # terminate() can cache an exit code on ACCESS_DENIED while
                        # TerminateJobObject is still completing. wait() then skips
                        # its kernel wait, leaving the child's cwd temporarily held.
                        _wait_windows_exit(p)
                    except DriverError as ex:
                        if primary is not None:
                            raise DriverError(f"{primary}; {ex}") from ex
                        raise
                else:
                    if owned:
                        terminate_owned(p, owned, environment)
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    p.wait()
            finally:
                if observe is not None:
                    failed = sys.exception() is not None
                    try:
                        out.seek(0)
                        observe(out.read(output_limit).decode("utf-8", "replace"), p.returncode)
                    except BaseException:
                        if not failed:
                            raise
