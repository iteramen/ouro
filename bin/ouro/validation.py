"""Explicit driver-context validation; receipts belong to this worktree's Git-private state."""
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from . import sh
from .runtime.process import cancellation, check_platform, defer_cancellation, finish_cleanup, worker_env

TIMEOUT = 3600
OUTPUT_LIMIT = 1024 * 1024


def command(cmd):
    # No shell grammar, even inside an otherwise approved string. Leading environment
    # assignments are data passed to exec, never evaluated by a shell.
    if not isinstance(cmd, str) or not cmd.strip() or re.search(r"[\n\r\x00;&|<>`$]", cmd):
        raise sh.DriverError("invalid validation command")
    try:
        argv = shlex.split(cmd)
    except ValueError as ex:
        raise sh.DriverError(f"invalid validation command: {ex}")
    env = {}
    while argv and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", argv[0]):
        k, v = argv.pop(0).split("=", 1)
        if k in env:
            raise sh.DriverError("duplicate validation environment assignment")
        env[k] = v
    if not argv:
        raise sh.DriverError("validation command has no executable")
    return argv, env


def requests(value, allowed, gates=(), *, inherited=()):
    """Fresh answers are exclusive; history may repeat a caller obligation from earlier valid records."""
    if not isinstance(value, list):
        raise sh.DriverError("validation_pending must be an array")
    seen = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"cmd", "reason"}
                or not isinstance(item["reason"], str) or not item["reason"].strip()
                or not isinstance(item["cmd"], str)):
            raise sh.DriverError("malformed validation_pending record")
        cmd = item["cmd"]
        argv, env = command(cmd)
        identity = (tuple(argv), tuple(sorted(env.items())))
        if cmd not in allowed or identity in seen:
            raise sh.DriverError("unapproved or duplicate validation command")
        if cmd not in inherited and any(isinstance(g, dict) and g.get("cmd") == cmd for g in gates):
            raise sh.DriverError("validation command conflicts with gates_run")
        seen.add(identity)
    return value


def key(head, commands):
    """The state directory's name for a head and its ordered commands: 16 hex digits, since a Windows path
    stops at 260 characters and the worktree's git directory can sit deep under TEMP."""
    return hashlib.sha256(json.dumps([head, commands]).encode("utf-8")).hexdigest()[:16]


def state(tree, pending):
    head = tree.head()
    commands = [p["cmd"] for p in pending]
    root = tree.admin() / "ouro-validation"
    return head, commands, root / key(head, commands)


def receipt(tree, pending, *, inputs=None):
    try:
        check_platform()
        head, commands, directory = inputs if inputs is not None else state(tree, pending)
        if directory.parent.is_symlink() or directory.is_symlink() or (directory / "receipt.json").is_symlink():
            return None
        data = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        attempt = data["attempt"]
        if not isinstance(attempt, str) or not re.fullmatch(r"attempt-[a-z0-9_]+", attempt):
            return None
        evidence = directory / attempt
        if evidence.is_symlink():
            return None
        results = data["results"]
        if data["sha"] != head or data["commands"] != commands or len(results) != len(commands):
            return None
        for i, (cmd, r) in enumerate(zip(commands, results)):
            log = evidence / f"{i}.log"
            if (log.is_symlink() or r["sha"] != head or r["cmd"] != cmd or type(r["exit"]) is not int or r["exit"] != 0
                    or r["log"] != str(log) or r["digest"] != hashlib.sha256(log.read_bytes()).hexdigest()):
                return None
        return results
    except (sh.DriverError, OSError, ValueError, KeyError, TypeError):
        return None


def inputs_match(tree, head):
    return tree.head() == head and not tree.git("status", "--porcelain", "-z", "--untracked-files=all").out


def clean(tree, head):
    if not inputs_match(tree, head):
        raise sh.DriverError("validation requires unchanged HEAD and clean tracked/untracked inputs")


def attempts(tree):
    """Historical execution evidence is labelled by its own head, never used as proof."""
    root = tree.admin() / "ouro-validation"
    heads = set(tree.git("rev-list", f"origin/{tree.env.default}..HEAD").out.split())
    results = []
    if root.is_symlink():
        return results
    for path in sorted(root.glob("*/attempt-*/attempt.json")):
        try:
            if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            head, commands = data["sha"], data["commands"]
            if head not in heads or path.parent.parent.name != key(head, commands):
                continue
            for i, r in enumerate(data["results"]):
                log = path.parent / f"{i}.log"
                if (r["cmd"] != commands[i] or r["sha"] != head or type(r["exit"]) is not int
                        or r["log"] != str(log) or log.is_symlink()
                        or r["digest"] != hashlib.sha256(log.read_bytes()).hexdigest()):
                    break
                results.append(r)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
    return results










@contextmanager
def lock(root):
    """One validation per worktree at a time."""
    path = root / "lock"
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        raise sh.DriverError(f"validation is already running in this worktree; if none is, delete {path}") from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@cancellation()
def execute(cmd, cwd, log, settings=None):
    argv, extra = command(cmd)
    env = sh.child_env({**extra, **(worker_env(settings) if settings else {})})
    try:
        check_platform()
    except sh.DriverError as ex:
        log.write_text(str(ex) + "\n", encoding="utf-8")
        return 75
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if not exe:
        log.write_text("executable unavailable\n", encoding="utf-8")
        return 75
    argv[0] = exe
    # A temporary file avoids unbounded captured output. Poll its size and abort on
    # overflow; only the bounded prefix is retained as durable evidence.
    with tempfile.TemporaryFile() as output:
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as ex:
            log.write_text(str(ex)[:OUTPUT_LIMIT], encoding="utf-8")
            return 75
        try:
            start, limited = time.monotonic(), False
            try:
                while p.poll() is None:
                    if time.monotonic() - start > TIMEOUT or os.fstat(output.fileno()).st_size > OUTPUT_LIMIT:
                        limited = True
                        break
                    time.sleep(0.02)
            finally:
                defer_cancellation()
        finally:
            def cleanup():
                # Preserve the validator's process-group boundary on cancellation.
                if type(p.pid) is int and p.pid > 1:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                return p.wait()
            code = finish_cleanup(cleanup)
        output.seek(0)
        raw = output.read(OUTPUT_LIMIT + 1)
        if len(raw) > OUTPUT_LIMIT:
            limited = True
        log.write_bytes(raw[:OUTPUT_LIMIT] + (b"\nvalidation time/output limit exceeded\n" if limited else b""))
        return 124 if limited else code


def record_attempt(evidence, head, commands, results):
    # Abrupt death during an update must not truncate the prior successful results.
    temporary = evidence / "attempt.tmp"
    temporary.write_text(json.dumps({"sha": head, "commands": commands, "results": results}), encoding="utf-8")
    temporary.replace(evidence / "attempt.json")


def check_inputs(tree, head, hint):
    """Check the proof boundary under the validation lock, without executing a command or writing a receipt."""
    return validate(tree, [], hint, check_head=head)[1]


def wait_hint(env, n, k, commands):
    """Describe metadata-only obligations; never inspect or create execution proof here."""
    part = f" --part {k}" if k else ""
    authority = env.lookup("ship.validation") or "local"
    obligations = ", ".join(f"`{cmd}`" for cmd in dict.fromkeys(commands)) or "committed-input verification"
    return (f"authority={authority}; waiting for proof: {obligations}. "
            'The owner can select ship.validation = "ci" in the trusted invoking and branch bindings, '
            "with ship.validation_checks naming successful exact-head checks. Each command needs an exact gate.run "
            "whose paths select it and whose CI job actually executes it; check names alone add no coverage. "
            "Genuine local-only gates retain local proof. Otherwise resume the same branch/commit from a native Linux host: "
            f"`ouro run {n}{part} --validate`")


def validate(tree, pending, hint, *, reuse=False, execute_missing=True, check_head=None):
    """Return (results, failure): None means accepted; 75 means wait. Resume never executes missing proof.

    `hint(commands)` is the wait's text on a host that cannot validate; None stands for the run's required execution."""
    try:
        check_platform()
    except sh.DriverError as ex:
        sh.say(f"{ex}; {hint(None if check_head is not None else [p['cmd'] for p in pending])}")
        return [], 75
    commands = [p["cmd"] for p in pending]
    root = tree.admin() / "ouro-validation"
    if root.is_symlink():
        raise sh.DriverError("validation state must be Git-private directories")
    root.mkdir(parents=True, exist_ok=True)
    with lock(root):
        head = tree.head()
        if check_head is not None:
            return [], None if inputs_match(tree, check_head) else 1
        directory = root / key(head, commands)
        if directory.is_symlink():
            raise sh.DriverError("validation state must be Git-private directories")
        directory.mkdir(parents=True, exist_ok=True)
        clean(tree, head)
        if reuse:
            results = receipt(tree, pending, inputs=(head, commands, directory))
            if results is not None:
                return results, None
        if not execute_missing:
            return [], 75
        (directory / "receipt.json").unlink(missing_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="attempt-", dir=directory))
        results = []
        record_attempt(evidence, head, commands, results)
        for i, cmd in enumerate(commands):
            log = evidence / f"{i}.log"
            code = execute(cmd, tree.path, log, tree.env.runtime)
            r = {"cmd": cmd, "sha": head, "exit": code, "log": str(log),
                 "digest": hashlib.sha256(log.read_bytes()).hexdigest()}
            results.append(r)
            sh.say(f"validation @ {head}: `{cmd}` exit {code}; evidence {log}")
            # Every attempt is evidence, including failures and source mutation.
            record_attempt(evidence, head, commands, results)
            clean(tree, head)
            if code != 0:
                return results, code
        temporary = directory / "receipt.tmp"
        temporary.write_text(json.dumps({"sha": head, "commands": commands, "results": results, "attempt": evidence.name}), encoding="utf-8")
        temporary.replace(directory / "receipt.json")
        return results, None
