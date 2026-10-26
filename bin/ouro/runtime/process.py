"""Bounded harness subprocesses; close owned process trees on completion or cancellation."""
import os
import signal
import subprocess
import sys
import tempfile
import time

from ..sh import DriverError, R, child_env

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


def run(argv, cwd=None, prompt=None, timeout=600, env=None, observe=None, output_limit=None, stderr_limit=None):
    output_limit = LIMIT if output_limit is None else output_limit
    stderr_limit = LIMIT if stderr_limit is None else stderr_limit
    environment = child_env(env)
    job = None
    owned = {}
    if os.name != "nt":
        from ..validation import process_table, descendants, terminate_owned
        process_table(environment)  # refuse before starting when ownership cannot be observed
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        source.write((prompt or "").encode("utf-8"))
        source.seek(0)
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=source, stdout=out, stderr=err,
                                 start_new_session=os.name != "nt",
                                 creationflags=0x00000004 if os.name == "nt" else 0)  # CREATE_SUSPENDED
        except OSError as ex:
            raise DriverError(f"cannot start runtime: {ex}") from ex
        try:
            if os.name == "nt":
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
                if (os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > output_limit
                        or os.fstat(err.fileno()).st_size > stderr_limit):
                    raise DriverError("runtime output limit exceeded")
                time.sleep(0.03)
            if (os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > output_limit
                        or os.fstat(err.fileno()).st_size > stderr_limit):
                raise DriverError("runtime output limit exceeded")
            out.seek(0)
            err.seek(0)
            return R(p.returncode, out.read(output_limit).decode("utf-8", "replace"),
                     err.read(stderr_limit).decode("utf-8", "replace"), argv)
        finally:
            try:
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
