"""Command line: bind first, then dispatch. Exit 0 landed or waiting, 1 driver error, 2 refused, 3 stopped."""
import argparse
import sys
from pathlib import Path

from . import node, ops, run, sh, triage
from .sh import DriverError, Env, Refused, Stopped


def parser():
    ap = argparse.ArgumentParser(prog="ouro", description="The ouro driver.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="take an agent-ready issue to a landed change; resumable")
    r.add_argument("n", type=int)
    r.add_argument("--part", type=int, help="part k of a checklist deliverable")
    r.add_argument("--dry-run", action="store_true", help="print the transitions and commands; write nothing")
    t = sub.add_parser("triage", help="grade an issue with the TRIAGE node and post the verdict")
    t.add_argument("n", type=int)
    t.add_argument("--dry-run", action="store_true", help="run the node, print the comment and label moves, write nothing")
    w = sub.add_parser("weekly", help="print the loop-outcomes table")
    w.add_argument("--post", action="store_true", help="also post it as a comment on the [rolling_issues].loop_runs issue")
    sub.add_parser("compile", help="print the next wave of the queue")
    return ap


def main_checkout():
    r = sh.run([sh.which("git"), "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if r.code == 0 and r.out.strip():
        return Path(r.out.strip()).parent
    top = sh.run([sh.which("git"), "rev-parse", "--show-toplevel"])
    if top.code != 0:
        raise Refused("not inside a git work tree")
    return Path(top.out.strip())


def bind(dry):
    """The binding is read before any tracker call; no binding, or one the reader refuses, refuses the run."""
    main = main_checkout()
    ob = sh.load_binding_module()
    try:
        data = ob.load(main / ".claude" / "ouro.toml")
    except SystemExit as e:
        raise Refused(str(e.code))
    errors = ob.validate(data)
    if errors:
        raise Refused("the binding fails its check:\n" + "\n".join(errors))
    return Env(main, ob, data, dry)


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    env = None
    try:
        env = bind(getattr(args, "dry_run", False))
        if args.cmd == "run":
            code = run.main(env, args.n, args.part)
            table = node.usage_table()
            if table and not args.dry_run:
                print(table)
            return code
        if args.cmd == "triage":
            return triage.triage(env, args.n, args.dry_run)
        if args.cmd == "weekly":
            return ops.weekly(env, args.post)
        return ops.compile_(env)
    except Refused as e:
        print(f"ouro: refused: {e}", file=sys.stderr)
        return 2
    except Stopped as e:
        return e.args[0]
    except DriverError as e:
        print(f"ouro: error: {e}", file=sys.stderr)
        return 1
    finally:
        if env:
            env.close()
