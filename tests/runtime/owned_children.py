"""Scope adoption/reaping to negative fixtures that deliberately kill parents."""
import ctypes
import functools
import os
from pathlib import Path
import signal
import sys
import time


def reap_fixture_children(method):
    @functools.wraps(method)
    def run(*args, **kwargs):
        if not sys.platform.startswith("linux"):
            return method(*args, **kwargs)
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "fixture subreaper inspection failed")
        children = Path(f"/proc/self/task/{os.getpid()}/children")
        before = set(map(int, children.read_text().split()))
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "fixture subreaper unavailable")
        try:
            return method(*args, **kwargs)
        finally:
            primary = sys.exception()
            live = []
            try:
                for pid in set(map(int, children.read_text().split())) - before:
                    try:
                        child, _ = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        continue  # Never signal a PID without a current child relationship.
                    if child == 0:
                        live.append(pid)
                        os.kill(pid, signal.SIGKILL)
                        deadline = time.monotonic() + 2
                        while os.waitpid(pid, os.WNOHANG)[0] == 0:
                            if time.monotonic() >= deadline:
                                raise AssertionError("fixture child did not settle after owned cleanup")
                            time.sleep(.01)
            finally:
                if libc.prctl(36, previous.value, 0, 0, 0) != 0:
                    raise OSError(ctypes.get_errno(), "fixture subreaper restore failed")
            if live and primary is None:
                raise AssertionError("negative fixture left live children after its cleanup")
    return run
