"""Private stdio MCP bridge. Only declared file operations and sandboxed commands exist."""
import fnmatch
import functools
import hashlib
import importlib.util
import itertools
import json
import os
import re
import sys
import stat
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ouro import execution, sh, validation
from ouro.runtime import process

PROTECTED = {".git", ".claude", ".codex", ".agents", ".aws"}
# Protect absent names too: commands must not create a mixed-case harness alias.
PROTECTED_NAMES = frozenset(
    "".join(chars) for name in PROTECTED
    for chars in itertools.product(*[(c.lower(), c.upper()) if c.isalpha() else (c,) for c in name]))
SKIP = {".git", "node_modules", "__pycache__"}
LIMIT = 1024 * 1024


def probe_root(root):
    """Fail closed on inode aliases and unproved filesystem topologies before commands."""
    if sys.platform not in ("linux", "darwin"):
        raise sh.DriverError("PROBE boundary requires native Linux or macOS")
    root = Path(root).resolve(strict=True)
    try:
        sources = [(sh.PLUGIN / name).resolve() for name in ("bin", "hooks", "skills", "agents")]
        if (not root.is_dir() or root.is_mount() or root == Path(root.anchor)
                or any(p.is_relative_to(root) or root.is_relative_to(p) for p in sources)):
            raise ValueError("PROBE copy overlaps installed runtime or filesystem root")
        metadata = root / ".git"
        admin = metadata
        if metadata.is_file():
            if metadata.stat().st_size > 4096:
                raise ValueError("oversized Git pointer")
            pointer = metadata.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: ") or "\n" in pointer or "\x00" in pointer:
                raise ValueError("unproved Git pointer")
            admin = (root / pointer[8:]).resolve(strict=True)
            if admin.is_relative_to(root):
                raise ValueError("linked Git admin is inside the writable copy")
        if admin.exists():
            if not admin.is_dir():
                raise ValueError("Git admin is not a directory")
            common = admin
            pointer = admin / "commondir"
            if pointer.exists():
                if not pointer.is_file() or pointer.stat().st_size > 4096:
                    raise ValueError("unproved common Git pointer")
                value = pointer.read_text(encoding="utf-8").strip()
                if not value or "\n" in value or "\x00" in value:
                    raise ValueError("unproved common Git pointer")
                common = (admin / value).resolve(strict=True)
                if not common.is_dir():
                    raise ValueError("common Git is not a directory")
            if (root.is_relative_to(common) or (common.is_relative_to(root)
                    and not common.is_relative_to(metadata))):
                raise ValueError("common Git overlaps the writable copy")
        if sys.platform == "linux":
            process.check_platform()  # Kernel/proc facts refuse WSL and unprovable native boundaries.
            # is_mount alone misses bind mounts on the same filesystem.
            for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
                value = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
                if Path(value).is_relative_to(root):
                    raise ValueError("PROBE copy contains a mount: " + value)
        for directory, dirs, files in os.walk(root, followlinks=False,
                                              onerror=lambda ex: (_ for _ in ()).throw(ex)):
            for name in [*dirs, *files]:
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                    raise ValueError("PROBE copy contains a symlink or hard link: " + str(path))
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise ValueError("PROBE copy contains a special file: " + str(path))
                if path.is_mount():
                    raise ValueError("PROBE copy contains a mount: " + str(path))
    except (OSError, ValueError) as ex:
        raise sh.DriverError("PROBE boundary admission refused: " + str(ex)) from ex
    return root


def probe_configuration(root, environment, launch):
    """Read-only RPC admission; never expose raw configuration or server diagnostics."""
    options = launch[launch.index("-c"):launch.index("--permission-profile")]
    executable = launch[:launch.index("sandbox")]
    messages, buffered, phase = {}, b"", 0
    admission_stage = "invocation"

    def request(identifier, method, params):
        return (json.dumps(dict(id=identifier, method=method, params=params)) + "\n").encode()

    def exchange(data):
        nonlocal buffered, phase
        buffered += data
        outgoing = b""
        if phase == 0:
            phase = 1
            outgoing = request(1, "initialize", dict(
                clientInfo=dict(name="ouro-boundary", version="0"),
                capabilities=dict(experimentalApi=True)))
        while b"\n" in buffered:
            line, buffered = buffered.split(b"\n", 1)
            reply = json.loads(line)
            if isinstance(reply, dict) and "id" not in reply:
                # Installed app-server emits this notification during config/read.
                # Validate and discard it without retaining private remote identities.
                params = reply.get("params")
                if (set(reply) <= {"method", "params", "emittedAtMs"}
                        and reply.get("method") == "remoteControl/status/changed"
                        and isinstance(params, dict)
                        and {"installationId", "serverName", "status"} <= set(params)
                        and set(params) <= {"installationId", "serverName", "status", "environmentId"}
                        and all(isinstance(params[k], str) for k in ("installationId", "serverName"))
                        and params["status"] in ("disabled", "connecting", "connected", "errored")
                        and (params.get("environmentId") is None or isinstance(params["environmentId"], str))
                        and ("emittedAtMs" not in reply or type(reply["emittedAtMs"]) is int)):
                    continue
                raise ValueError("unsupported RPC notification")
            if (not isinstance(reply, dict) or set(reply) != {"id", "result"}
                    or type(reply["id"]) is not int or phase not in (1, 2, 3) or reply["id"] != phase):
                raise ValueError("unexpected RPC response")
            messages[phase] = reply["result"]
            if phase == 1:
                outgoing += b'{"method":"initialized"}\n'
                outgoing += request(2, "config/read", dict(includeLayers=True, cwd=str(root)))
                phase = 2
            elif phase == 2:
                outgoing += request(3, "configRequirements/read", {})
                phase = 3
            else:
                phase = 4
        return None if phase == 4 else outgoing

    try:
        if (root != root.resolve(strict=True) or not Path(executable[0]).is_absolute()
                or not isinstance(environment, dict)):
            raise ValueError("unproved invocation context")
        executable_identity = []
        for value in executable:
            path = Path(value)
            if path.is_absolute():
                resolved = path.resolve(strict=True)
                if resolved.is_relative_to(root):
                    raise ValueError("runtime is writable by PROBE")
                info = resolved.stat()
                executable_identity.append((str(path), str(resolved), info.st_dev, info.st_ino,
                                            info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        codex_home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        if not codex_home.is_absolute() or codex_home.resolve() != codex_home:
            raise ValueError("CODEX_HOME alias")
        admission_stage = "read-only-rpc"
        process.run([*executable, "app-server", "-c", 'default_permissions="ouro"',
                     *options, "--listen", "stdio://"], cwd=root, env=environment,
                    timeout=30, output_limit=LIMIT, stderr_limit=LIMIT, exchange=exchange)
        if phase != 4:
            raise ValueError("incomplete RPC")
        admission_stage = "selected-profile"
        response = messages[2]
        expected_files = {str(root): "write", **{str(root / p): "read" for p in sorted(PROTECTED_NAMES)}}
        config = response["config"]
        if config.get("default_permissions") != "ouro":
            raise ValueError("selected profile")
        selected = config["permissions"]["ouro"]
        if set(selected) != {"description", "extends", "workspace_roots", "filesystem", "network"}:
            raise ValueError("unknown permission fields")
        if (selected["description"] is not None or selected["extends"] != ":read-only"
                or selected["workspace_roots"] is not None):
            raise ValueError("inherited permission grants")
        admission_stage = "filesystem"
        filesystem = dict(selected["filesystem"])
        if filesystem.pop("glob_scan_max_depth", None) is not None or filesystem != expected_files:
            raise ValueError("filesystem grants")
        admission_stage = "network"
        network = selected["network"]
        network_fields = {"enabled", "proxy_url", "enable_socks5", "socks_url", "enable_socks5_udp",
                          "allow_upstream_proxy", "dangerously_allow_non_loopback_proxy",
                          "dangerously_allow_all_unix_sockets", "mode", "domains", "unix_sockets",
                          "allow_local_binding", "mitm"}
        if (set(network) != network_fields or network["enabled"] is not False
                or any(value is not None for key, value in network.items() if key != "enabled")):
            raise ValueError("network grants")
        admission_stage = "requirements"
        requirements = messages[3]
        if (set(requirements) != {"requirements", "supportsIndependentSpeedModes"}
                or requirements["requirements"] is not None
                or type(requirements["supportsIndependentSpeedModes"]) is not bool):
            raise ValueError("unproved requirements")
        admission_stage = "layer-provenance"
        layers = response["layers"]
        origins = response["origins"]
        if not isinstance(layers, list) or not layers or not isinstance(origins, dict):
            raise ValueError("missing provenance")
        identities, files = [], {}
        for layer in layers:
            if (not {"name", "version", "config"} <= set(layer)
                    or not set(layer) <= {"name", "version", "disabledReason", "config"}):
                raise ValueError("unknown layer fields")
            name, version = layer["name"], layer["version"]
            if not isinstance(version, str) or not version or not isinstance(layer["config"], dict):
                raise ValueError("unproved layer version")
            if layer.get("disabledReason") is not None:
                raise ValueError("disabled layer")
            kind = name["type"]
            fields = {
                "packagedDefaults": {"type", "file"}, "system": {"type", "file"},
                "user": {"type", "file", "profile"}, "project": {"type", "dotCodexFolder"},
                "legacyManagedConfigTomlFromFile": {"type", "file"}, "sessionFlags": {"type"}}
            if (kind not in fields or not set(name) <= fields[kind]
                    or not (fields[kind] - ({"profile"} if kind == "user" else set())) <= set(name)
                    or (kind == "user" and name.get("profile") is not None
                        and not isinstance(name["profile"], str))):
                raise ValueError("unprovable managed or unknown layer")
            if kind == "sessionFlags":
                if name != {"type": "sessionFlags"}:
                    raise ValueError("session identity")
            else:
                field = "dotCodexFolder" if kind == "project" else "file"
                source = Path(name[field])
                if not source.is_absolute() or source.resolve() != source:
                    raise ValueError("configuration alias")
                if kind == "user":
                    home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
                    if not home.is_absolute() or home.resolve() != home or source != home / "config.toml":
                        raise ValueError("CODEX_HOME provenance")
                if kind == "project":
                    if not root.is_relative_to(source.parent):
                        raise ValueError("project context")
                    source = source / "config.toml"
                if source.is_relative_to(root) and source.relative_to(root).parts[0] not in PROTECTED:
                    raise ValueError("configuration input is writable by PROBE")
                # Track absent files and parent identities too: creation/replacement is drift.
                chain = []
                for path in (source, *source.parents):
                    try:
                        info = path.lstat()
                        if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                            raise ValueError("configuration alias")
                        if path == source and not stat.S_ISREG(info.st_mode):
                            raise ValueError("configuration is not a regular file")
                        chain.append((str(path), info.st_dev, info.st_ino, info.st_mode,
                                      *((info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                                        if stat.S_ISREG(info.st_mode) else ())))
                    except FileNotFoundError:
                        chain.append((str(path), None))
                digest = None
                if source.exists():
                    with source.open("rb") as stream:
                        data = stream.read(LIMIT + 1)
                    if len(data) > LIMIT:
                        raise ValueError("configuration input exceeds admission bound")
                    digest = hashlib.sha256(data).hexdigest()
                files[str(source)] = chain, digest
            identity = dict(name=name, version=version)
            if identity in identities:
                raise ValueError("duplicate layer")
            identities.append(identity)
        if sum(item["name"] == {"type": "sessionFlags"} for item in identities) != 1:
            raise ValueError("missing session layer")
        admission_stage = "permission-origins"
        for metadata in origins.values():
            if metadata not in identities:
                raise ValueError("unproved origin")
        required = ["default_permissions", "permissions.ouro.extends", "permissions.ouro.network.enabled",
                    *("permissions.ouro.filesystem." + path for path in expected_files)]
        if any(origins.get(key, {}).get("name") != {"type": "sessionFlags"} for key in required):
            raise ValueError("permission origin")
        # Opaque versions are compared, never interpreted as raw-file hashes.
        return response, requirements, files, executable, executable_identity, dict(environment)
    except Exception:
        # Only a source-defined stage is public. Exception strings, RPC payloads and
        # configuration paths may contain secrets and must never enter worker evidence.
        raise sh.DriverError("PROBE effective configuration admission refused: " + admission_stage +
                             " (unavailable, conflicting, aliased or unprovable configuration)") from None


def sandbox_argv(root, argv, *, probe=False):
    if probe:
        # Explicit absolute grants do not inherit :workspace's ambient temporary writes.
        root = Path(root).resolve()
        filesystem = {str(root): "write", **{str(root / p): "read" for p in sorted(PROTECTED_NAMES)}}
        sandbox = ["sandbox", "-c", 'permissions.ouro.extends=":read-only"',
                   "-c", "permissions.ouro.network.enabled=false",
                   "-c", "permissions.ouro.filesystem={" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in filesystem.items()) + "}",
                   "--permission-profile", "ouro", "--include-managed-config"]
        if os.name == "nt":
            sandbox += ["-C", str(root)]
        return [*sh.argv_for("codex", *sandbox), *argv]
    sandbox = ["sandbox",
               "-c", 'permissions.ouro.extends=":workspace"', "-c", "permissions.ouro.network.enabled=false",
               "-c", 'permissions.ouro.filesystem={":workspace_roots"={".git"="read",".claude"="read",".codex"="read",".agents"="read",".aws"="read"}}',
               "--permission-profile", "ouro", "--include-managed-config"]
    if os.name == "nt":
        sandbox += ["-C", str(root)]
    return [*sh.argv_for("codex", *sandbox), *argv]


def git_read_argv(argv):
    """Use the same helper restrictions for readiness and advertised Git reads."""
    argv = list(argv)
    if argv[1] in ("diff", "log"):
        argv[2:2] = ["--no-ext-diff", "--no-textconv"]
    argv[1:1] = ["-c", "core.fsmonitor=false", "--no-pager"]
    return argv


def git_environment(overrides=None):
    """Keep Git diagnostics classifiable without replacing configuration or ignores."""
    values = {**(overrides or {}), "LC_ALL": "C", "LANGUAGE": "C"}
    return {**values, **{key: None for key, value in (overrides or {}).items() if value is None}}


def git_configuration_failure(result):
    # Git can warn about unreadable excludes yet return success. Inspect stderr only:
    # filenames printed by status/log/diff are data, not capability diagnostics.
    return any(re.search(r"^(?:fatal|warning|error): "
                         r"(?:unable to access|cannot access|could not open config file|unable to read config).*"
                         r"(?:Permission denied|Access is denied)", line, re.I)
               for line in result.err.splitlines())


def git_readiness(root, env=None, *, probe=False):
    """Refuse before mutation, without changing host configuration or sandbox grants."""
    for args in (["status", "--short", "--untracked-files=all"], ["log", "-3", "--oneline", "--all"], ["diff", "--stat"]):
        label = "git " + " ".join(args)
        try:
            arguments = sh.argv_for(*git_read_argv(["git", *args]))
            boundary = sandbox_argv(root, arguments, probe=True) if probe else sandbox_argv(root, arguments)
            environment = git_environment(env)
            if probe:
                environment.update({key: str(Path(root).resolve()) for key in ("TMPDIR", "TMP", "TEMP")})
                # Keep removal markers: process.run normalizes this environment once more.
                environment = {**process.child_env(environment),
                               **{key: None for key, value in environment.items() if value is None}}
                first = probe_configuration(Path(root).resolve(), environment, boundary)
                if first != probe_configuration(Path(root).resolve(), environment, boundary):
                    raise sh.DriverError("PROBE effective configuration changed before readiness")
            result = process.run(boundary, cwd=root, timeout=30, env=environment)
        except sh.DriverError as ex:
            raise sh.DriverError(f"Git capability readiness: {label}: sandbox/process unavailable: {ex}") from ex
        if git_configuration_failure(result):
            raise sh.DriverError(f"Git capability readiness: {label}: inaccessible configuration or excludes "
                                 f"under the OS sandbox (exit {result.code}): {result.err[-2000:]}")
        if result.code != 0:
            raise sh.DriverError(f"Git capability readiness: {label}: query failed "
                                 f"(exit {result.code}): {result.err[-2000:]}")



def caller_source_writable(root, plugin_root=None):
    """Why installed caller sources overlap the worker write scope, or None."""
    try:
        bridge = Bridge({"root": str(root), "edit": True, "commands": [], "probe": False})
        plugin = Path(plugin_root or sh.PLUGIN)
        pending = [plugin / name for name in ("bin", "hooks", "agents", "skills")]
        seen = set()
        for visited in range(10000):
            if not pending:
                return None
            source = pending.pop().resolve()
            if bridge.root.is_relative_to(source):
                return "the main BUILD scope overlaps installed runtime or instruction sources"
            if source.is_relative_to(bridge.root):
                try:
                    bridge.path(str(source), write=True)  # Physical targets include externally named aliases.
                except ValueError:
                    pass  # A protected directory may still contain writable-target aliases.
                else:
                    return "the main BUILD scope can edit installed runtime or instruction sources"
            if source.is_dir() and source not in seen:
                seen.add(source)
                for child in source.iterdir():
                    if visited + len(pending) >= 9999:
                        return "separation from installed runtime or instruction sources is unproved"
                    pending.append(child)
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return "separation from installed runtime or instruction sources is unproved"


@functools.cache
def guard_checks():
    """The command guards' checks, loaded once from the installed plugin's hooks."""
    checks = []
    for guard in ("guard-delete", "guard-stash-kill", "guard-discard", "guard-commit"):
        path = sh.PLUGIN / "hooks" / (guard + ".py")
        if not path.is_file():
            raise sh.DriverError(f"required guard {guard} is missing; reinstall ouro")
        spec = importlib.util.spec_from_file_location("ouro_" + guard.replace("-", "_"), path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as ex:
            raise sh.DriverError(f"{guard} failed to load: {type(ex).__name__}: {ex}") from ex
        checks.append((guard, module.check))
    return checks


class Bridge:
    def __init__(self, policy):
        self.policy = policy
        self.root = Path(policy["root"]).resolve()

    def path(self, value, write=False):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("invalid file path")
        raw = self.root / value
        path = raw.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("path leaves the node worktree")
        relative = path.relative_to(self.root)
        if tuple(p.lower() for p in relative.parts[:2]) == (".claude", "worktrees"):
            raise ValueError("another worktree is outside this node's file scope")
        if write:
            lexical = Path(os.path.abspath(raw)).relative_to(self.root)
            if (not self.policy["edit"] or any(p.lower() in PROTECTED for p in (*lexical.parts[:1], *relative.parts[:1]))
                    or (os.name == "nt" and any(":" in p for p in relative.parts))):
                raise ValueError("protected path or node has no edit capability")
        return path

    def files(self, pattern):
        count = 0
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP and not (Path(directory) / d).is_symlink()
                       and not (d.lower() == "worktrees" and Path(directory).parent == self.root
                                and Path(directory).name.lower() == ".claude")]
            for name in sorted(files):
                path = Path(directory) / name
                rel = path.relative_to(self.root).as_posix()
                if fnmatch.fnmatchcase(rel, pattern):
                    count += 1
                    if count > 20000:
                        return
                    try:
                        yield self.path(rel), rel
                    except ValueError:
                        pass

    def call(self, name, args):
        allowed = {t["name"] for t in self.tools()}
        if name not in allowed:
            raise ValueError("tool is not granted to this node")
        if name in ("write", "edit"):
            path = self.path(args["path"], write=True)
            if path.exists() and path.stat().st_nlink > 1:
                raise ValueError("hard-linked file: replace this alias with an independent copy before editing")
        if name == "list":
            return [rel for _, rel in self.files(args.get("pattern", "*"))]
        if name == "read":
            path = self.path(args["path"])
            if path.stat().st_size > LIMIT:
                raise ValueError("file exceeds 1 MiB; narrow the input")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            start, count = args.get("start", 1), args.get("count", 250)
            if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 1000:
                raise ValueError("invalid line range")
            return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines) if start <= i + 1 < start + count)
        if name == "search":
            regex = re.compile(args["pattern"])
            hits = []
            for path, rel in self.files(args.get("glob", "*")):
                try:
                    if path.stat().st_size > LIMIT:
                        continue
                    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if regex.search(line):
                            hits.append(f"{rel}:{i}: {line[:1000]}")
                            if len(hits) >= 200:
                                return hits
                except (UnicodeError, OSError):
                    continue
            return hits
        if name == "write":
            content = args["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > LIMIT:
                raise ValueError("invalid or oversized file content")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
            return "written"
        if name == "edit":
            text = path.read_text(encoding="utf-8")
            if not args["old"] or text.count(args["old"]) != 1:
                raise ValueError("edit requires exactly one matching old fragment")
            path.write_bytes(text.replace(args["old"], args["new"], 1).encode("utf-8"))
            return "edited"
        if name == "delete":
            self.path(args["path"], write=True).unlink()
            return "deleted"
        if name == "run":
            return self.run(args["command"])
        raise ValueError("unknown tool")

    def run(self, command):
        self.inputs = None
        try:
            argv, extra = validation.command(command)
        except sh.DriverError as ex:
            # Parser refusals are tool input errors; execution/guard failures stay fatal.
            raise ValueError(str(ex)) from ex
        if extra:
            raise ValueError("environment assignments are not granted")
        git_query = len(argv) >= 2 and argv[0] == "git" and argv[1] in ("rm", "mv", "status", "diff", "log", "ls-files")
        wildcard = f"git {argv[1]} *" if git_query else ""
        if not (command in self.policy["commands"] or wildcard in self.policy["commands"]):
            if not self.policy["probe"]:
                raise ValueError("command is not on the node's approved list")
            if Path(argv[0]).stem.lower() == "gh" or (Path(argv[0]).stem.lower() == "git" and any(a in ("push", "commit") for a in argv[1:])):
                raise ValueError("review cannot publish or commit")
        if git_query and argv[1] in ("rm", "mv"):
            paths = [a for a in argv[2:] if a != "--"]
            if any(a.startswith("-") or any(c in a for c in "*?[") for a in paths):
                raise ValueError("file moves/deletes require literal paths without options")
            targets = [self.path(a, write=True) for a in paths]
            if argv[1] == "rm" and targets and all(p.is_file() for p in targets):
                for path in targets:
                    path.unlink()
            elif argv[1] == "mv" and len(targets) == 2 and targets[0].is_file() and not targets[1].exists():
                targets[0].replace(targets[1])
            else:
                raise ValueError("invalid file move/delete")
            return {"exit": 0, "stdout": "Worktree updated; the driver stages changes.", "stderr": ""}
        if git_query and any(a.split("=", 1)[0] in ("--output", "--ext-diff", "--textconv", "--no-index") for a in argv[2:]):
            raise ValueError("git exploration cannot write output files or execute diff helpers")
        if git_query:
            argv = git_read_argv(argv)
        for guard, check in guard_checks():
            try:
                reason = check("Bash", {"command": command}, str(self.root))
            except Exception as ex:  # a guard's own defect is infrastructure, never a refusal the model answers
                raise sh.DriverError(f"{guard} failed: {type(ex).__name__}: {ex}") from ex
            if reason:
                raise ValueError(reason)
        def inventory(*args):
            result = process.run(sandbox_argv(self.root, sh.argv_for(
                "git", "-c", "core.fsmonitor=false", *args)), cwd=self.root, timeout=30, env=git_environment())
            if result.code or git_configuration_failure(result):
                raise sh.DriverError("execution input inventory unavailable")
            return result.out
        probe = self.policy["probe"]
        environment = git_environment() if git_query else {}
        if probe:
            probe_root(self.root)
            # The existing owned root also supports read-only inspection without a pre-admission mkdir.
            environment.update({key: str(self.root) for key in ("TMPDIR", "TMP", "TEMP")})
        before = execution.fingerprint(self.root, inventory) if self.policy["edit"] else None
        arguments = sh.argv_for(argv[0], *argv[1:])
        command_argv = sandbox_argv(self.root, arguments, probe=True) if probe else sandbox_argv(self.root, arguments)
        with tempfile.TemporaryDirectory(prefix="ouro-probe-gh-") as gh:
            if probe:
                # Removal markers stay: process.run normalizes this environment once more.
                environment = {**process.child_env(environment), **process.probe_env(True, gh)}
                first = probe_configuration(self.root, environment, command_argv)
                if first != probe_configuration(self.root, environment, command_argv):
                    raise sh.DriverError("PROBE effective configuration changed before command")
            r = process.run(command_argv, cwd=self.root, timeout=600, env=environment or None)
        after = execution.fingerprint(self.root, inventory) if before else None
        self.inputs = before if before == after else None
        if git_query and git_configuration_failure(r):
            raise sh.DriverError(f"Git capability unavailable: inaccessible configuration or excludes "
                                 f"under the OS sandbox (exit {r.code}): {r.err[-2000:]}")
        return {"exit": r.code, "stdout": r.out[-LIMIT:], "stderr": r.err[-LIMIT:]}

    def tools(self):
        def tool(name, description, properties, required):
            return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": properties,
                    "required": required, "additionalProperties": False}}
        string, integer = {"type": "string"}, {"type": "integer"}
        tools = [
            tool("list", "List worktree files without executing a command.", {"pattern": string}, []),
            tool("read", "Read UTF-8 worktree text with line numbers.", {"path": string, "start": integer, "count": integer}, ["path"]),
            tool("search", "Search worktree file contents using a regular expression.", {"pattern": string, "glob": string}, ["pattern"])]
        if self.policy["edit"]:
            tools += [
                tool("write", "Write a worktree text file; harness/config paths are protected.", {"path": string, "content": string}, ["path", "content"]),
                tool("edit", "Replace exactly one literal fragment in a worktree text file.", {"path": string, "old": string, "new": string}, ["path", "old", "new"]),
                tool("delete", "Delete one worktree file; harness/config paths are protected.", {"path": string}, ["path"])]
        if self.policy["commands"] or self.policy["probe"]:
            tools.append(tool("run", "Run one approved command through the Codex OS sandbox. No shell chaining.", {"command": string}, ["command"]))
        return tools


def serve(policy_path):
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    bridge = Bridge(policy)
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        result = {}
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "ouro-node", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": bridge.tools()}
        elif method == "tools/call":
            params = request["params"]
            args, failure, infrastructure = params.get("arguments", {}), None, False
            try:
                value = bridge.call(params["name"], args)
            except (ValueError, OSError, KeyError, TypeError, sh.DriverError) as ex:
                failure, value = str(ex), str(ex)
                infrastructure = isinstance(ex, sh.DriverError)
            with open(policy["evidence"], "a", encoding="utf-8") as evidence:
                evidence.write(json.dumps({"tool": params["name"], "path": args.get("path"),
                                           "command": args.get("command"), "error": failure,
                                           "tool_input": args if failure and params["name"] in ("write", "edit", "delete") else None,
                                           "inputs": getattr(bridge, "inputs", None) if params["name"] == "run" else None,
                                           "infrastructure": infrastructure,
                                           "exit": value.get("exit") if isinstance(value, dict) else None}) + "\n")
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": bool(failure)}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    serve(sys.argv[1])
