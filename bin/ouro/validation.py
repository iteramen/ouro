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
from pathlib import Path

from . import sh, validation_lock
from .coordination import worker_env

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


def state(run, pending):
    head = run.head()
    commands = [p["cmd"] for p in pending]
    root = Path(run.wt_git("rev-parse", "--absolute-git-dir").out.strip()) / "ouro-validation"
    return head, commands, root / key(head, commands)


def receipt(run, pending, *, inputs=None):
    try:
        check_platform()
        head, commands, directory = inputs if inputs is not None else state(run, pending)
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


def inputs_match(run, head):
    return run.head() == head and not run.wt_git("status", "--porcelain", "-z", "--untracked-files=all").out


def clean(run, head):
    if not inputs_match(run, head):
        raise sh.DriverError("validation requires unchanged HEAD and clean tracked/untracked inputs")


def attempts(run):
    """Historical execution evidence is labelled by its own head, never used as proof."""
    root = Path(run.wt_git("rev-parse", "--absolute-git-dir").out.strip()) / "ouro-validation"
    heads = set(run.wt_git("rev-list", f"origin/{run.env.default}..HEAD").out.split())
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


def check_platform():
    """Caller validation needs a lifecycle boundary, independent of command environment."""
    if os.name == "nt":
        raise sh.DriverError("validation process lifecycle accounting unavailable on Windows")
    if os.name != "posix":
        raise sh.DriverError("validation process lifecycle accounting requires native Linux")
    try:
        kernel = os.uname()
        if kernel.sysname != "Linux":
            raise sh.DriverError("validation process lifecycle accounting requires native Linux")
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
        raise sh.DriverError(
            "validation platform visibility unavailable; restore kernel/proc visibility before retrying") from ex
    if wsl:
        raise sh.DriverError(
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
        raise sh.DriverError("validation process visibility unavailable") from ex


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


def execute(cmd, cwd, log, settings=None):
    argv, extra = command(cmd)
    exclusions = worker_env(settings) if settings else {}
    env = sh.child_env({**extra, **exclusions})
    # Approved command assignments must never choose the lifecycle observer.
    helpers = sh.child_env(exclusions)
    try:
        check_platform()
        process_table(helpers)
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
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=os.name != "nt")
        except OSError as ex:
            log.write_text(str(ex)[:OUTPUT_LIMIT], encoding="utf-8")
            return 75
        start, limited, owned = time.monotonic(), False, {}
        incomplete = False
        while True:
            done = p.poll() is not None  # completion must precede the ownership snapshot used to judge it
            if os.name != "nt":
                try:
                    table = process_table(helpers)
                    if not owned and p.pid in table:
                        owned[p.pid] = table[p.pid][1]
                    descendants(table, owned)
                except sh.DriverError:
                    # Do not report success when lifecycle accounting was lost.
                    p.kill()
                    p.wait()
                    raise sh.DriverError("validation lost process visibility; inspect descendants before resuming")
            if done:
                # Parent success is not completion while owned workers can still
                # change inputs. Reap only our recorded descendants and refuse
                # proof rather than silently turning interrupted work green.
                live = {pid for pid, born in owned.items()
                        if pid != p.pid and pid in table and table[pid][1] == born}
                if live:
                    terminate_owned(p, owned, helpers)
                    incomplete = True
                break
            if time.monotonic() - start > TIMEOUT or os.fstat(output.fileno()).st_size > OUTPUT_LIMIT:
                limited = True
                if os.name == "nt":
                    # taskkill's tree traversal needs the parent to still exist.
                    try:
                        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=30, check=True, env=helpers)
                    except (OSError, subprocess.SubprocessError) as ex:
                        p.kill()
                        p.wait()
                        raise sh.DriverError("validation process-tree cleanup failed; inspect descendants before resuming") from ex
                else:
                    terminate_owned(p, owned, helpers)
                break
            time.sleep(0.02)
        code = p.wait()
        output.seek(0)
        raw = output.read(OUTPUT_LIMIT + 1)
        if len(raw) > OUTPUT_LIMIT:
            limited = True
        if limited and os.name != "nt":
            terminate_owned(p, owned, helpers)
        log.write_bytes(raw[:OUTPUT_LIMIT] + (b"\nvalidation time/output limit exceeded\n" if limited else b"")
                        + (b"\nvalidation parent exited with owned descendants; incomplete execution\n" if incomplete else b""))
        if incomplete:
            return 1
        return 124 if limited else code


def record_attempt(evidence, head, commands, results):
    # Abrupt death during an update must not truncate the prior successful results.
    temporary = evidence / "attempt.tmp"
    temporary.write_text(json.dumps({"sha": head, "commands": commands, "results": results}), encoding="utf-8")
    temporary.replace(evidence / "attempt.json")


def check_inputs(run, head):
    """Check the proof boundary under its existing lease, without executing a command or writing a receipt."""
    return validate(run, [], check_head=head)[1]


def validate(run, pending, *, reuse=False, execute_missing=True, check_head=None):
    """Return (results, failure): None means accepted; 75 means wait. Resume never executes missing proof."""
    try:
        check_platform()
    except sh.DriverError as ex:
        sh.say(str(ex))
        return [], 75
    commands = [p["cmd"] for p in pending]
    # Locate the standard worktree administrative directory without launching Git
    # before ownership exists. Refuse other layouts rather than run an unowned helper.
    marker = run.worktree / ".git"
    if marker.is_dir():
        gitdir = marker.resolve()
    else:
        text = marker.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir: "):
            raise sh.DriverError("validation requires a standard Git worktree directory")
        gitdir = (run.worktree / text[8:]).resolve()
    root = gitdir / "ouro-validation"
    if root.is_symlink():
        raise sh.DriverError("validation state must be Git-private directories")
    root.mkdir(parents=True, exist_ok=True)
    lease = validation_lock.Lease(root / "lock", None, commands)
    try:
        actual = lease.execute(lambda: run.wt_git("rev-parse", "--absolute-git-dir").out.strip())
        if Path(actual).resolve() != gitdir:
            raise sh.DriverError("validation Git directory differs from owned directory")
        head = lease.execute(run.head)
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            raise sh.DriverError("validation HEAD identity unavailable")
        lease.data["sha"] = head
        validation_lock.write(lease.fd, lease.data)
        if check_head is not None:
            return [], None if lease.execute(inputs_match, run, check_head) else 1
        directory = root / key(head, commands)
        if directory.is_symlink():
            raise sh.DriverError("validation state must be Git-private directories")
        directory.mkdir(parents=True, exist_ok=True)
        lease.execute(clean, run, head)
        if reuse:
            # These inputs were acquired under the lease. Reading their evidence
            # launches no helper and keeps full batch results out of the bounded lock.
            results = receipt(run, pending, inputs=(head, commands, directory))
            if results is not None:
                lease.execute(clean, run, head)
                return results, None
        if not execute_missing:
            return [], 75
        (directory / "receipt.json").unlink(missing_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="attempt-", dir=directory))
        results = []
        record_attempt(evidence, head, commands, results)
        for i, cmd in enumerate(commands):
            log = evidence / f"{i}.log"
            code = lease.execute(execute, cmd, run.worktree, log, run.env.runtime, command_log=log)
            r = {"cmd": cmd, "sha": head, "exit": code, "log": str(log),
                 "digest": hashlib.sha256(log.read_bytes()).hexdigest()}
            results.append(r)
            sh.say(f"validation @ {head}: `{cmd}` exit {code}; evidence {log}")
            # Every attempt is evidence, including failures and source mutation.
            record_attempt(evidence, head, commands, results)
            lease.execute(clean, run, head)
            if code != 0:
                return results, code
        lease.execute(clean, run, head)
        temporary = directory / "receipt.tmp"
        temporary.write_text(json.dumps({"sha": head, "commands": commands, "results": results, "attempt": evidence.name}), encoding="utf-8")
        temporary.replace(directory / "receipt.json")
        return results, None
    finally:
        lease.close()
