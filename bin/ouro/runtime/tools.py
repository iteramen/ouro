"""Private stdio MCP bridge. Only declared file operations and sandboxed commands exist."""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ouro import execution, sh, validation
from ouro.runtime import process

PROTECTED = {".git", ".claude", ".codex", ".agents", ".aws"}
SKIP = {".git", "node_modules", "__pycache__"}
LIMIT = 1024 * 1024


def sandbox_argv(root, argv):
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


def git_readiness(root, env=None):
    """Refuse before mutation, without changing host configuration or sandbox grants."""
    for args in (["status", "--short", "--untracked-files=all"], ["log", "-3", "--oneline", "--all"], ["diff", "--stat"]):
        label = "git " + " ".join(args)
        try:
            result = process.run(sandbox_argv(root, sh.argv_for(*git_read_argv(["git", *args]))),
                                 cwd=root, timeout=30, env=git_environment(env))
        except sh.DriverError as ex:
            raise sh.DriverError(f"Git capability readiness: {label}: sandbox/process unavailable: {ex}") from ex
        if git_configuration_failure(result):
            raise sh.DriverError(f"Git capability readiness: {label}: inaccessible configuration or excludes "
                                 f"under the OS sandbox (exit {result.code}): {result.err[-2000:]}")
        if result.code != 0:
            raise sh.DriverError(f"Git capability readiness: {label}: query failed "
                                 f"(exit {result.code}): {result.err[-2000:]}")



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
        for guard in ("guard-delete", "guard-stash-kill", "guard-discard", "guard-commit"):
            path = sh.PLUGIN / "hooks" / (guard + ".py")
            if not path.is_file():
                raise sh.DriverError(f"required guard {guard} is missing; reinstall ouro")
            result = sh.run([sys.executable, str(path)],
                            input_text=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.root)}), timeout=10)
            if result.code not in (0, 2):
                raise sh.DriverError(f"{guard} failed (exit {result.code}): {result.err}")
            if result.code != 0:
                raise ValueError(result.err or f"{guard} unavailable")
        def inventory(*args):
            result = process.run(sandbox_argv(self.root, sh.argv_for(
                "git", "-c", "core.fsmonitor=false", *args)), cwd=self.root, timeout=30, env=git_environment())
            if result.code or git_configuration_failure(result):
                raise sh.DriverError("execution input inventory unavailable")
            return result.out
        before = execution.fingerprint(self.root, inventory) if self.policy["edit"] else None
        r = process.run(sandbox_argv(self.root, sh.argv_for(argv[0], *argv[1:])),
                        cwd=self.root, timeout=600, env=git_environment() if git_query else None)
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
