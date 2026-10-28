#!/usr/bin/env python3
"""Plan direct eligibility and run its mandatory checks; never publish.

    python3 ouro-direct.py --repo <checkout> --base <full-oid> --head <full-oid> [--check]

The base must be the caller's independently fetched default-branch tip. Planning
does not fetch, establish remote freshness or approve publication. --check uses the
shared owned subprocess core, then rechecks the same pinned inputs and authority.
Results are ordinary direct check results, not normal BUILD validator receipts.
Unproved eligibility returns a reasoned PR plan; the public publisher owns protection.
"""
import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
if sys.version_info < (3, 12):
    raise SystemExit("ouro-direct.py needs Python 3.12+")

MAX_OUTPUT = 8 * 1024 * 1024
MAX_BLOB = 2 * 1024 * 1024
MAX_BLOB_TOTAL = 16 * 1024 * 1024
HARNESS = ("ouro-direct.py", "ouro-process.py", "ouro-binding.py", "Push-Direct.ps1", "Push-Branch.ps1", "Get-LandBinding.ps1")
OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class Unproved(ValueError):
    pass


def _git(repo, *args, limit=MAX_OUTPUT, codes=(0,), deadline=None):
    """Bound metadata/blob capture; no shell, external diff/textconv or fsmonitor."""
    git = shutil.which("git")
    if not git:
        raise Unproved("git is unavailable")
    git = Path(git).resolve(strict=True)
    if git == repo or repo in git.parents:
        raise Unproved("Git resolves inside the candidate checkout")
    deadline = min(deadline if deadline is not None else float("inf"), time.monotonic() + 30)
    if time.monotonic() >= deadline:
        raise Unproved("direct planning exhausted its shared Git read budget")
    argv = [str(git), "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", *args]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=repo, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
        try:
            while process.poll() is None:
                if time.monotonic() > deadline or os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limit:
                    raise Unproved("Git read exceeded its time or output bound")
                time.sleep(0.01)
            if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limit:
                raise Unproved("Git read exceeded its output bound")
            if process.returncode not in codes:
                raise Unproved("Git could not prove " + args[0])
            out.seek(0)
            return process.returncode, out.read(limit)
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise Unproved("Git process exit is unproved") from error
            if os.name == "nt":
                import _winapi
                if _winapi.WaitForSingleObject(int(process._handle), 5000) != _winapi.WAIT_OBJECT_0:
                    raise Unproved("Git process exit is unproved")


def _text(data):
    return data.decode("utf-8", "strict")


def _path(data):
    path = _text(data)
    if (not path or path.startswith("/") or "\\" in path or "\ufffd" in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(p in ("", ".", "..") for p in path.split("/"))):
        raise Unproved("ambiguous repository path")
    return path


def _binding():
    spec = importlib.util.spec_from_file_location("ouro_direct_binding", Path(__file__).with_name("ouro-binding.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree_entry(read, ref, path):
    _, raw = read("ls-tree", "-z", ref, "--", ":(literal)" + path)
    rows = [r for r in raw.split(b"\0") if r]
    if len(rows) != 1:
        raise Unproved("trusted base binding is absent or ambiguous")
    metadata, found = rows[0].split(b"\t", 1)
    mode, kind, oid = _text(metadata).split()
    if _path(found) != path or mode != "100644" or kind != "blob" or not OID.fullmatch(oid):
        raise Unproved("trusted base binding is not a regular blob")
    return oid


def _history(read, base, head):
    _, raw = read("rev-list", "--reverse", "--topo-order", "--parents", f"{base}..{head}")
    rows = _text(raw).splitlines()
    if not rows or len(rows) > 1000:
        raise Unproved("empty or unbounded unpublished range")
    previous, pairs = base, []
    for row in rows:
        fields = row.split()
        if len(fields) != 2 or fields[1] != previous or not all(OID.fullmatch(x) for x in fields):
            raise Unproved("nonlinear unpublished history needs a PR")
        _, committed = read("cat-file", "commit", fields[0])
        parents = [line[7:] for line in committed.split(b"\n\n", 1)[0].splitlines() if line.startswith(b"parent ")]
        if parents != [previous.encode("ascii")]:
            raise Unproved("Git traversal differs from the committed parent identity")
        pairs.append((previous, fields[0]))
        previous = fields[0]
    if previous != head:
        raise Unproved("unpublished history does not end at the proposed head")
    return pairs


def _metadata_policy(read):
    # A planner is not a sandbox. Refuse configurations where metadata reads may
    # launch clean/smudge/process helpers, lazy network fetches or nested Git.
    for pattern, reason in ((r"^filter\..*\.(clean|smudge|process)$", "configured content filters"),
                            (r"^(extensions\.partialclone|remote\..*\.promisor)$", "partial/promisor object access")):
        if read("config", "--get-regexp", pattern, codes=(0, 1))[1]:
            raise Unproved(reason + " require a PR; metadata side effects are unproved")
    _, inventory = read("ls-files", "--stage", "-z")
    for row in inventory.split(b"\0"):
        if not row:
            continue
        mode, oid, stage = row.split(b"\t", 1)[0].split()
        if mode == b"160000" or stage != b"0":
            raise Unproved("indexed submodule or unresolved index state requires a PR")
    _, flags = read("ls-files", "-v", "-z")
    if any(not row.startswith(b"H ") for row in flags.split(b"\0") if row):
        raise Unproved("hidden, sparse or unknown index flags require a PR")


def _inline(argv):
    name = Path(argv[0]).stem.casefold()
    # Nested launchers do not pin their child executable or command ownership.
    if name in {"env", "sudo", "doas", "nice", "nohup", "timeout", "time", "uv", "pipenv", "poetry", "npx", "npm", "pnpm", "yarn"}:
        return True
    arguments = argv[1:]
    if name in {"make", "gmake", "ninja", "cmake", "ctest", "dotnet", "cargo", "go", "mvn", "gradle", "gradlew", "ant", "pytest", "tox", "nox"}:
        return True  # Implicit project/configuration ownership is not proved by argv.
    if name.startswith(("python", "pypy")) or name == "py":
        if arguments in (["--version"], ["-V"]):
            return False
        while arguments and arguments[0] in {"-B", "-u", "-E", "-s", "-S", "-I", "-P", "-q"}:
            arguments = arguments[1:]
        return not arguments or arguments[0].startswith("-")
    if name in {"pwsh", "powershell"}:
        while arguments and arguments[0].casefold() in {"-noprofile", "-noninteractive", "-nologo"}:
            arguments = arguments[1:]
        return len(arguments) < 2 or arguments[0].casefold() != "-file" or arguments[1].startswith("-")
    if name == "cmd":
        return True
    if name in {"sh", "bash", "dash", "zsh", "node", "ruby", "perl", "php"}:
        return not arguments or arguments[0].startswith("-")
    return any(
        arg.casefold() in {"-c", "/c", "/k", "-command", "-encodedcommand", "-enc", "-e", "--eval", "-r", "-m"}
        for arg in argv[1:])


def _argv(line, ob, *, obligation=False):
    if ob.OURO_TOKEN in line:
        root = ob.plugin_root()
        if root is None:
            raise Unproved("gate plugin-root expansion is unavailable")
        line = line.replace(ob.OURO_TOKEN, Path(root).as_posix())
    if obligation and re.search(r"[\n\r\x00;&|<>`$]", line):
        raise Unproved("unsupported shell grammar in a mandatory direct command")
    argv = shlex.split(line)
    if not argv:
        raise Unproved("empty direct command")
    if obligation and _inline(argv):
        raise Unproved("inline interpreter command grammar is unproved for direct")
    return argv


def _external_leaf(repo, lexical):
    # A failed leaf lookup must not hide checkout ownership behind a short-path
    # or directory alias. Unprovable parent identity remains a PR refusal.
    parent = lexical.parent.resolve(strict=True)
    if lexical == repo or repo in lexical.parents or parent == repo or repo in parent.parents:
        raise Unproved("unresolved checkout-owned gate path requires a PR")
    if lexical.is_dir():
        raise Unproved("unresolved gate directory identity requires a PR")
    return parent / lexical.name


def _executable(repo, name, *, required=False):
    # Resolve using the execution checkout, not this planner's own working directory.
    if Path(name).is_absolute() or "/" in name or "\\" in name:
        candidate = Path(name) if Path(name).is_absolute() else repo / name
        found = shutil.which(str(candidate))
    else:
        search = [str(repo / p) if not Path(p).is_absolute() else p for p in os.get_exec_path()]
        found = shutil.which(name, path=os.pathsep.join(([str(repo)] if os.name == "nt" else []) + search))
    if found:
        lexical = Path(os.path.abspath(found))
        try:
            resolved = lexical.resolve(strict=True)
        except OSError:
            # Structural CI obligations do not require a usable local external
            # toolchain. Execution still resolves strictly, and an unresolved
            # checkout-owned executable never becomes eligible this way.
            if required:
                raise
            _external_leaf(repo, lexical)
            return None
        if resolved == repo or repo in resolved.parents:
            raise Unproved("protected checkout-owned executable requires a PR")
        return str(resolved)
    if required:
        raise Unproved("required gate executable is unavailable")
    return None


def _execution_pin(repo, name):
    executable = _executable(repo, name, required=True)
    info = Path(executable).stat()
    if not stat.S_ISREG(info.st_mode):
        raise Unproved("required gate executable is not a regular file")
    return executable, (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _gate_harness(repo, base, data, ob, read):
    protected = set(ob.DIRECT_PROBES) | set(ob.gate_paths(data))
    for gate in data.get("gate", []):
        for field in ("run", "ci"):
            if not gate.get(field):
                continue
            argv = _argv(gate[field], ob)
            if _inline(argv):
                # A literal CI notice owns no script and grants no execution. Do
                # not interpret a dynamic command string to discover authority.
                notice = (field == "ci" and len(argv) >= 3 and Path(argv[0]).stem.casefold() in {"pwsh", "powershell"}
                          and argv[-2].casefold() == "-command"
                          and all(arg.casefold() in {"-noprofile", "-noninteractive", "-nologo"} for arg in argv[1:-2])
                          and re.fullmatch(r"Write-Host (['\"])[\w .,:!?-]*\1", argv[-1]))
                if notice:
                    argv = argv[:-2]  # Still protect the actual executable.
                else:
                    raise Unproved("dynamic gate command grammar ownership is unproved")
            executable = _executable(repo, argv[0])
            for token in ([executable, *argv[1:]] if executable else argv):
                if re.search(r"[\x00\r\n$`*?\[\]{}]", token):
                    raise Unproved("gate-owned path identity is unproved")
                if token.startswith("-"):
                    if "=" not in token:
                        continue
                    token = token.split("=", 1)[1]
                candidate = Path(token)
                shaped = "/" in token or "\\" in token or re.search(r"\.\w+$", token)
                if not shaped and not (repo / candidate).exists():
                    continue
                lexical = Path(os.path.abspath(candidate if candidate.is_absolute() else repo / candidate))
                try:
                    resolved = lexical.resolve(strict=False)
                except OSError:
                    resolved = _external_leaf(repo, lexical)  # --check must still prove execution.
                owned = [p for p in (lexical, resolved) if p == repo or repo in p.parents]
                if not owned:
                    continue
                for path in owned:
                    relative = path.relative_to(repo).as_posix()
                    if relative == ".":
                        raise Unproved("protected gate owns the checkout root")
                    _, raw = read("ls-tree", "-z", base, "--", ":(literal)" + relative)
                    entries = [entry for entry in raw.split(b"\0") if entry]
                    if len(entries) != 1 or entries[0].split(b" ", 1)[0] not in {b"100644", b"100755", b"040000"}:
                        raise Unproved("protected gate-owned path has no ordinary trusted base identity")
                    protected.add(relative + "/" if path.is_dir() else relative)
    return protected


def _changes(read, pairs):
    paths, total, blobs = set(), 0, {}
    for parent, commit in pairs:
        options = ("--no-renames", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none")
        _, raw = read("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "-z", *options, parent, commit)
        fields = raw.split(b"\0")
        if fields[-1] != b"" or (len(fields) - 1) % 2:
            raise Unproved("malformed raw change inventory")
        current = set()
        for metadata, name in zip(fields[0:-1:2], fields[1:-1:2]):
            old, new, before, after, status = _text(metadata).split()
            old = old.removeprefix(":")
            if (old not in ("000000", "100644", "100755") or new not in ("000000", "100644", "100755")
                    or (old != "000000" and new != "000000" and old != new)
                    or (old != new and "100755" in (old, new))
                    or status not in ("A", "D", "M")):
                raise Unproved("non-regular file or executable-mode change needs a PR")
            path = _path(name)
            current.add(path)
            for mode, oid in ((old, before), (new, after)):
                if mode != "000000":
                    if not OID.fullmatch(oid):
                        raise Unproved("unproved blob identity")
                    blobs[oid] = None
        _, raw = read("diff", "--numstat", "-z", *options, parent, commit)
        counted = set()
        for row in raw.split(b"\0"):
            if not row:
                continue
            added, deleted, name = row.split(b"\t", 2)
            if not added.isdigit() or not deleted.isdigit():
                raise Unproved("binary or unmeasurable changes need a PR")
            path = _path(name)
            if path in counted:
                raise Unproved("duplicate numstat path")
            counted.add(path)
            total += int(added) + int(deleted)
        if counted != current:
            raise Unproved("change and line inventories disagree")
        paths.update(current)
    budget = 0
    for oid in blobs:
        _, raw = read("cat-file", "blob", oid, limit=MAX_BLOB)
        budget += len(raw)
        if budget > MAX_BLOB_TOTAL:
            raise Unproved("text verification exceeded its byte bound")
        text = _text(raw)
        if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
            raise Unproved("non-text blob needs a PR")
    return sorted(paths), total


def _matched(read, pairs, specs, paths):
    if not specs:
        return set(paths)
    found = set()
    for parent, commit in pairs:
        _, raw = read("diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
                     "--ignore-submodules=none", parent, commit, "--", *specs)
        found.update(_path(p) for p in raw.split(b"\0") if p)
    if not found <= set(paths):
        raise Unproved("gate selector disagrees with change inventory")
    return found


def _documentation(path):
    p = Path(path)
    return (p.suffix.lower() in (".md", ".rst", ".adoc") or p.name in ("README", "LICENSE", "NOTICE", "COPYING")
            or path.startswith("docs/") and p.suffix.lower() == ".txt")


def plan(repo, base, head):
    """Return eligibility and must-pass obligations, never execution/remote evidence."""
    result = {"kind": "plan", "route": "pr", "eligible": False, "reasons": [],
              "base": base, "head": head, "paths": [], "lines": None, "files": None,
              "commands": [], "code_coverage": {}, "execution_proof": False}
    try:
        if not isinstance(base, str) or not isinstance(head, str) or not OID.fullmatch(base) or not OID.fullmatch(head):
            raise Unproved("base and head must be full immutable object IDs")
        if any(os.environ.get(k) for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                                            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_COUNT")):
            raise Unproved("redirected Git inputs are unproved")
        repo = Path(repo).resolve(strict=True)
        deadline = time.monotonic() + 60
        read = lambda *args, **kw: _git(repo, *args, deadline=deadline, **kw)
        _, raw = read("rev-parse", "--show-toplevel")
        if Path(_text(raw).strip()).resolve() != repo:
            raise Unproved("repo must be the effective checkout root")
        _, raw = read("rev-parse", "HEAD")
        if _text(raw).strip() != head:
            raise Unproved("checkout HEAD differs from the proposed head")
        _metadata_policy(read)
        if read("merge-base", "--is-ancestor", base, head, codes=(0, 1))[0] != 0:
            raise Unproved("base is not an ancestor of head")
        if read("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")[1]:
            raise Unproved("final tracked and untracked inputs must be clean")
        ob = _binding()
        binding_oid = _tree_entry(read, base, ".claude/ouro.toml")
        _, raw = read("cat-file", "blob", binding_oid, limit=MAX_BLOB)
        data = ob.tomllib.loads(raw.decode("utf-8-sig", "strict"))
        errors = ob.validate(data)
        if errors:
            raise Unproved("trusted base binding is invalid: " + errors[0])
        result["binding_blob"] = binding_oid
        ship = data.get("ship", {})
        if not ship.get("direct"):
            raise Unproved("ship.direct is empty or absent: direct is disabled")
        pairs = _history(read, base, head)
        paths, lines = _changes(read, pairs)
        result.update(paths=paths, lines=lines, files=len(paths), commits=[c for _, c in pairs])
        if not paths:
            raise Unproved("no changed paths to publish")
        harness = _gate_harness(repo, base, data, ob, read)
        try:
            folder = Path(__file__).resolve().parent.relative_to(repo).as_posix()
            harness.update((folder + "/" + name).removeprefix("./") for name in HARNESS)
            harness.add((folder + "/ouro/").removeprefix("./"))
        except ValueError:
            pass  # Installed plugin outside this consumer; flat/bin probes still apply.
        for path in paths:
            if (any(path.casefold().startswith(p.casefold()) for p in ob.DIRECT_PREFIXES)
                    or any(path.casefold() == p.casefold() or p.endswith("/") and path.casefold().startswith(p.casefold())
                           for p in harness)):
                raise Unproved("protected policy, publication or gate path: " + path)
            if not any(fnmatch.fnmatchcase(path, glob) for glob in ship["direct"]):
                raise Unproved("path is outside trusted ship.direct: " + path)
        limits = {"lines": ship.get("direct_max_lines", 20), "files": ship.get("direct_max_files", 3)}
        result["limits"] = limits
        if lines > limits["lines"] or len(paths) > limits["files"]:
            raise Unproved("change exceeds the trusted direct line/file limit")
        code = {p for p in paths if not _documentation(p)}
        coverage = {p: [] for p in sorted(code)}
        commands, cheap = {}, False
        for ordinal, gate in enumerate(data.get("gate", []), 1):
            if not gate.get("cheap") and not gate.get("direct_code_check"):
                continue
            matched = _matched(read, pairs, gate.get("paths"), paths)
            covered = sorted(code & matched) if gate.get("direct_code_check") else []
            if not matched or not gate.get("cheap") and not covered:
                continue
            cheap |= gate.get("cheap", False)
            argv = _argv(gate["run"], ob, obligation=True)
            key = (gate["run"], tuple(argv))
            row = commands.setdefault(key, {"argv": argv, "run": gate["run"], "gates": [], "cheap": False,
                                            "code_paths": [], "must_pass": True})
            row["gates"].append(ordinal)
            row["cheap"] |= gate.get("cheap", False)
            row["code_paths"] = sorted(set(row["code_paths"]) | set(covered))
            for path in covered:
                coverage[path].append(ordinal)
        result.update(commands=list(commands.values()), code_coverage=coverage)
        if not cheap:
            raise Unproved("no nonempty applicable cheap-gate floor")
        if any(not checks for checks in coverage.values()):
            raise Unproved("changed code lacks an explicit applicable direct_code_check")
        # Re-read after the potentially expensive inventory; caller must repeat after gates.
        if _text(read("rev-parse", "HEAD")[1]).strip() != head or read(
                "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")[1]:
            raise Unproved("final inputs changed while planning")
        _metadata_policy(read)
        result.update(route="direct", eligible=True)
    except (Unproved, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        result["reasons"].append(str(error))
    return result


def check(repo, base, head):
    """Use the existing owned executor; ordinary direct results are not validator receipts."""
    expected = plan(repo, base, head)
    result = {**expected, "kind": "checked-plan", "checks": [], "outputs": []}
    if not expected["eligible"]:
        return result
    try:
        folder = Path(__file__).resolve().parent
        executor_path = folder / "ouro-process.py"
        if not executor_path.is_file():
            executor_path = folder / "ouro/runtime/process.py"
        if not executor_path.is_file():
            raise Unproved("owned direct executor is unavailable; use a PR")
        spec = importlib.util.spec_from_file_location("ouro_direct_process", executor_path)
        executor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(executor)
        try:
            executor.check_platform()  # Caller admission, not the generic harness or structural plan.
        except executor.DriverError as error:
            raise Unproved(str(error)) from error
        pins = [_execution_pin(repo, row["argv"][0]) for row in expected["commands"]]
        authority = (Path(__file__).resolve(), folder / "ouro-binding.py", executor_path)

        def fingerprints():
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in authority}

        originals = fingerprints()
        def unchanged():
            if fingerprints() != originals or plan(repo, base, head) != expected:
                raise Unproved("final inputs or direct authority changed during mandatory checks")
            if [_execution_pin(repo, row["argv"][0]) for row in expected["commands"]] != pins:
                raise Unproved("mandatory check executable identity changed")

        for command, (executable, _) in zip(expected["commands"], pins):
            unchanged()
            observed = {}
            try:
                ran = executor.run([executable, *command["argv"][1:]], cwd=repo, timeout=600,
                                   output_limit=1024 * 1024, stderr_limit=1024 * 1024,
                                   observe=lambda output, code: observed.update(out=output))
            except executor.DriverError as error:
                result["outputs"].append({"cmd": command["run"], "exit": None,
                                          "out": observed.get("out", ""), "err": str(error)})
                raise Unproved(str(error)) from error
            result["checks"].append({"cmd": command["run"], "exit": ran.code, "sha": head, "gates": command["gates"]})
            result["outputs"].append({"cmd": command["run"], "exit": ran.code, "out": ran.out, "err": ran.err})
            if ran.code != 0:
                raise Unproved(f"mandatory check failed (exit {ran.code}): " + command["run"])
            unchanged()
    except (Unproved, OSError, ValueError, ImportError) as error:
        result.update(route="pr", eligible=False, reasons=[str(error)])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--check", action="store_true", help="execute the planned checks through the shared owned executor")
    args = parser.parse_args(argv)
    result = (check if args.check else plan)(args.repo, args.base, args.head)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["eligible"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
