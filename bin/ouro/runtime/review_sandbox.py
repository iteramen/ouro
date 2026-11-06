"""ship.review_sandbox: whether this host can confine the REVIEW node's PROBE commands, decided before any dispatch."""
import os
import re
from pathlib import Path

from .backends import BACKENDS, NOT_REQUIRED, UNSUPPORTED

PLATFORMS = ("linux", "wsl2", "macos")  # where a harness sandbox can run; native Windows, WSL1 and the rest have none
WSL_MARKERS = ("/proc/sys/fs/binfmt_misc/WSLInterop", "/run/WSL")


def platform():
    """linux, wsl1, wsl2, macos, windows, or the kernel's own name lowered: from kernel facts, never the environment."""
    if os.name == "nt":
        return "windows"
    kernel = os.uname()
    if kernel.sysname == "Darwin":
        return "macos"
    if kernel.sysname != "Linux":
        return kernel.sysname.lower()
    if not (re.search(r"microsoft|wsl", f"{kernel.release} {kernel.version}", re.I)
            or any(Path(m).exists() for m in WSL_MARKERS)):
        return "linux"
    # WSL2 runs Microsoft's Linux kernel ("-microsoft-standard", later "-WSL2"); WSL1 reports the Windows build ("-Microsoft").
    return "wsl2" if re.search(r"microsoft-standard|wsl2", kernel.release, re.I) else "wsl1"


def refusal(settings, where=None):
    """Why ship.review_sandbox = true refuses the reviewer role's backend on platform `where` (this host's by default), else None."""
    where = where or platform()
    backend = BACKENDS[settings.backend_of("reviewer")]
    head = f"ship.review_sandbox = true, and the reviewer role selects {backend.label} on {where}"
    if where not in PLATFORMS:
        return f"{head}: missing a platform with a harness sandbox ({', '.join(PLATFORMS)}); native Windows and WSL1 have none"
    route = backend.interface.sandbox_route
    missing = [] if route == NOT_REQUIRED else None if route == UNSUPPORTED else route(where)
    if missing is None:
        return (f"{head}: missing a {backend.label} sandbox route for PROBE review commands; "
                "select a backend that has one in [runtime.roles.reviewer]")
    if missing:
        return f"{head}: missing {' and '.join(missing)}, which the {backend.label} sandbox needs; install what is missing on this host"
    return None
