"""Claude transport. Tool spelling and the CLI envelope stay inside this module."""
import json
import re
import tempfile
from pathlib import Path

from .. import sh
from ..coordination import worker_env
from . import Capabilities

FLAGS = ("--output-format", "--model", "--allowedTools", "--disallowedTools", "--permission-mode", "--json-schema", "--settings")


def tools_for(capabilities):
    tools = ["Read", "Grep", "Glob"]
    if capabilities.edit:
        tools = ["Edit", "Write", *tools]
    tools += [f"Bash({c})" for c in capabilities.commands]
    if capabilities.probe:
        tools.append("Bash")
    denied = ["Bash(git push *)", "Bash(git commit *)", "Bash(gh *)"] if capabilities.probe else None
    return tools, denied


def tail(text):
    return "\n".join(str(text).strip().splitlines()[-8:])[-800:]


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
        raise sh.DriverError(f"preflight: claude auth status --json (exit {r.code}) does not report a signed-in CLI: {tail(r.out or r.err)!r}; run claude auth login")
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
        extra = []
        if name == "BUILD" and isinstance(tools, Capabilities):
            commands = list(tools.commands)
            policy.write_text(json.dumps({"root": str(Path(cwd).resolve()), "commands": commands,
                                         "evidence": str(evidence)}), encoding="utf-8")
            hooks = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{
                "type": "command", "command": observed.invocation("hook", policy)}]}]}}
            extra = ["--settings", json.dumps(hooks)]
        answer, session, usage, cost = _execute(
            name, prompt, model, tools, cwd, timeout, denied, schema, settings, extra)
        usage = {**usage, "tool_calls": [json.loads(line) for line in evidence.read_text(
            encoding="utf-8").splitlines()] if evidence.exists() else []}
        return answer, session, usage, cost


def _execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings, extra=()):
    if isinstance(tools, Capabilities):
        tools, denied = tools_for(tools)
    argv = sh.argv_for("claude", "-p", "--output-format", "json", "--model", model, "--allowedTools", ",".join(tools),
                       *(["--disallowedTools", ",".join(denied)] if denied else []),
                       "--permission-mode", "dontAsk", "--json-schema", json.dumps(schema), *extra)
    r = sh.run(argv, cwd=cwd, input_text=prompt, timeout=timeout, env=worker_env(settings))
    if r.code is None:
        raise sh.DriverError(f"node {name} timed out after {timeout}s")
    try:
        env = json.loads(r.out)
    except ValueError:
        raise sh.DriverError(f"node {name}: claude exited {r.code} with no JSON envelope; stderr tail: {tail(r.err)!r}; stdout tail: {tail(r.out)!r}")
    if not isinstance(env, dict):
        raise sh.DriverError("claude returned a non-object envelope")
    if r.code != 0 or env.get("is_error"):
        raise sh.DriverError(f"node {name} (session {env.get('session_id', 'unknown')}): claude exited {r.code} with is_error; result: {tail(env.get('result', ''))!r}; stderr tail: {tail(r.err)!r}")
    answer = env.get("structured_output", env.get("result", ""))
    if isinstance(answer, dict) and name in ("BUILD", "REVIEW") and env.get("permission_denials"):
        answer = {**answer, "_denied": env["permission_denials"]}
    return answer, env.get("session_id", ""), env.get("usage") or {}, env.get("total_cost_usd")
