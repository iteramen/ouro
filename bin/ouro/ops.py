"""`weekly` and `compile`: thin wrappers over loop-outcomes.py and the compile scripts."""
import json

from . import sh
from .sh import DriverError


def weekly(env, post):
    argv = sh.script("loop-outcomes.py", *(["--comment"] if post else []))
    r = env.run(argv, cwd=env.main)
    if r.code != 0:
        raise DriverError(f"{sh.cmdline(argv)} failed (exit {r.code}): {r.text}")
    print(r.out.rstrip())  # a report, relayed as printed
    return 0


def compile_(env):
    g = env.run(sh.script("Get-FootprintGraph.ps1"), cwd=env.main)
    if g.code != 0:
        raise DriverError(f"Get-FootprintGraph.ps1 failed (exit {g.code}): {g.text}")
    areas = json.dumps(env.lookup("labels.area", []) or [])
    argv = sh.script("Get-CompileProgram.ps1", "-Repo", env.slug, "-Areas", areas)
    c = env.run(argv, cwd=env.main, input_text=g.out)
    if c.code != 0:
        raise DriverError(f"{sh.cmdline(argv)} failed (exit {c.code}): {c.text}")
    print(c.out.rstrip())  # a report, relayed as printed
    return 0
