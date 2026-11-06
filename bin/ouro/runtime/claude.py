"""Claude transport. Tool spelling and the CLI envelope stay inside this module."""
import json
import re
import shutil
import tempfile
from pathlib import Path

from .. import sh
from .process import probe_env, worker_env
from . import Capabilities, diagnostics
from .backends import NOT_REQUIRED, UNCONFINED, UNSUPPORTED, Interface

FLAGS = ("--output-format", "--model", "--allowedTools", "--disallowedTools", "--permission-mode", "--json-schema", "--settings")
SHARED_TOOLS = {"Write": "write", "Edit": "edit", "Bash": "run", "PowerShell": "run"}
SHARED_INPUT = {"file_path": "file_path", "content": "content", "old_string": "old", "new_string": "new", "command": "command"}
# Bash writes only in the cwd (the disposable review copy) and the launch's own temp root; no domain is admitted;
# a missing sandbox fails the launch.
SANDBOX = {"sandbox": {"enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
                       "network": {"allowedDomains": []}}}
SANDBOX_PREREQUISITES = {"linux": ("bwrap", "socat"), "wsl2": ("bwrap", "socat"), "macos": ()}  # macOS: Seatbelt, built in


def sandbox_route(where):
    """The executables Claude Code's sandbox lacks on PATH on platform `where`, or None where it has no sandbox."""
    needs = SANDBOX_PREREQUISITES.get(where)
    return None if needs is None else [f"{b} on PATH" for b in needs if shutil.which(b) is None]


def tools_for(capabilities):
    tools = ["Read", "Grep", "Glob"]
    if capabilities.edit:
        tools = ["Edit", "Write", *tools]
    tools += [f"Bash({c})" for c in capabilities.commands]
    if capabilities.probe:
        tools.append("Bash")
    denied = ["Bash(git push *)", "Bash(git commit *)", "Bash(gh *)"] if capabilities.probe else None
    return tools, denied


def denials(native):
    """Claude Code's permission_denials in the shared denial shape (see Capabilities)."""
    return [{"tool_name": SHARED_TOOLS.get(d.get("tool_name"), d.get("tool_name")),
             "tool_input": {SHARED_INPUT[k]: v for k, v in (d.get("tool_input") or {}).items() if k in SHARED_INPUT}}
            if isinstance(d, dict) else d for d in native]


def preflight(settings):
    try:
        binary = sh.argv_for("claude")
    except sh.DriverError as ex:
        raise sh.DriverError(f"preflight: {ex}; install it on this host, or put it on PATH")
    r = sh.run([*binary, "auth", "status", "--json"], timeout=120, env=worker_env(settings))
    try:
        status = json.loads(r.out)
    except ValueError:
        status = None
    if r.code != 0 or not isinstance(status, dict) or status.get("loggedIn") is not True:
        raise sh.DriverError(f"preflight: claude auth status --json (exit {r.code}) does not report a signed-in CLI: {sh.tail(r.out or r.err)!r}; run claude auth login")
    h = sh.run([*binary, "--help"], timeout=120, env=worker_env(settings))
    missing = [f for f in FLAGS if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", h.out)]
    if h.code != 0 or missing:
        raise sh.DriverError(f"preflight: claude --help (exit {h.code}) lists no {', '.join(missing) or 'flags'}; update Claude Code")


def execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings):
    from . import observed
    with tempfile.TemporaryDirectory(prefix="ouro-claude-proof-") as directory:
        root = Path(directory)
        policy = root / "policy.json"
        evidence = root / "commands.jsonl"
        extra, env = [], {}
        if name == "BUILD" and isinstance(tools, Capabilities):
            commands = list(tools.commands)
            policy.write_text(json.dumps({"root": str(Path(cwd).resolve()), "commands": commands,
                                         "evidence": str(evidence)}), encoding="utf-8")
            hooks = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{
                "type": "command", "command": observed.invocation("hook", policy)}]}]}}
            extra = ["--settings", json.dumps(hooks)]
        elif isinstance(tools, Capabilities) and tools.sandbox:
            extra = ["--settings", json.dumps(SANDBOX)]
            # Claude Code's sandbox also admits writes under <CLAUDE_CODE_TMPDIR or TMPDIR>/claude-<uid>, by default
            # /tmp/claude-<uid>, which every session of the uid shares: this launch gets its own, deleted when it returns.
            (root / "tmp").mkdir()
            env = {"CLAUDE_CODE_TMPDIR": str(root / "tmp"), "TMPDIR": str(root / "tmp")}
        (root / "gh").mkdir()
        env.update(probe_env(isinstance(tools, Capabilities) and tools.probe, root / "gh"))
        answer, session, usage, cost = _execute(
            name, prompt, model, tools, cwd, timeout, denied, schema, settings, extra, env)
        usage = {**usage, "tool_calls": [json.loads(line) for line in evidence.read_text(
            encoding="utf-8").splitlines()] if evidence.exists() else []}
        return answer, session, usage, cost


def _execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings, extra=(), env=None):
    if isinstance(tools, Capabilities):
        tools, denied = tools_for(tools)
    argv = sh.argv_for("claude", "-p", "--output-format", "json", "--model", model, "--allowedTools", ",".join(tools),
                       *(["--disallowedTools", ",".join(denied)] if denied else []),
                       "--permission-mode", "dontAsk", "--json-schema", json.dumps(schema), *extra)
    r = sh.run(argv, cwd=cwd, input_text=prompt, timeout=timeout, env={**worker_env(settings), **(env or {})})
    if r.code is None:
        raise sh.DriverError(f"node {name} timed out after {timeout}s")
    try:
        env = json.loads(r.out)
    except ValueError:
        raise sh.DriverError(f"node {name}: claude exited {r.code} with no JSON envelope; stderr tail: {sh.tail(r.err)!r}; stdout tail: {sh.tail(r.out)!r}")
    if not isinstance(env, dict):
        raise sh.DriverError("claude returned a non-object envelope")
    if r.code != 0 or env.get("is_error"):
        raise sh.DriverError(f"node {name} (session {env.get('session_id', 'unknown')}): claude exited {r.code} with is_error; result: {sh.tail(env.get('result', ''))!r}; stderr tail: {sh.tail(r.err)!r}")
    answer = env.get("structured_output", env.get("result", ""))
    if isinstance(answer, dict) and name in ("BUILD", "REVIEW") and env.get("permission_denials"):
        answer = {**answer, "_denied": denials(env["permission_denials"])}
    return answer, env.get("session_id", ""), env.get("usage") or {}, env.get("total_cost_usd")


def _attempt(name, prompt, model, tools, cwd, timeout, denied, schema, settings, diagnostic):
    answer, session, usage, cost = execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings)
    diagnostic["session"] = diagnostics.identifier(session)
    for target, source in (("input", "input_tokens"), ("output", "output_tokens"),
                           ("cache_read", "cache_read_input_tokens"), ("cache_creation", "cache_creation_input_tokens")):
        diagnostic[target] = diagnostics.count((usage or {}).get(source))
    return answer, session, usage, cost


INTERFACE = Interface(preflight=lambda settings, capabilities: preflight(settings) or (), admits=lambda capabilities: True,
                      worktree_readiness=NOT_REQUIRED,
                      execute=_attempt, probe_confinement=UNCONFINED, sandbox_route=sandbox_route,
                      validate_answer=UNSUPPORTED)
