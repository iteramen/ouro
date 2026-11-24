"""Claude transport. Tool spelling and the CLI envelope stay inside this module."""
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .. import sh
from .process import private_home, probe_env, worker_env
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
TIERS = ("haiku", "sonnet", "opus", "fable")  # runtime.advisor "up" names the next one


def advisor(configured, model):
    """The advisorModel a node on `model` gets: `up` is the next tier, none above the top; an alias as written."""
    if configured != "up":
        return configured
    return next(iter(TIERS[TIERS.index(model) + 1:]), None)


def sandbox_route(where):
    """The executables Claude Code's sandbox lacks on PATH on platform `where`, or None where it has no sandbox."""
    needs = SANDBOX_PREREQUISITES.get(where)
    return None if needs is None else [f"{b} on PATH" for b in needs if shutil.which(b) is None]


def tools_for(capabilities):
    tools = ["Read", "Grep", "Glob"]
    if capabilities.edit:
        tools = ["Edit", "Write", *tools]
    tools += [f"Bash({c})" for c in capabilities.commands]
    if not capabilities.probe:
        return tools, None
    shells = ("Bash", "PowerShell")  # PowerShell is Claude Code's shell tool on Windows
    # shortcut: a sandboxed probe keeps Bash alone, the shell SANDBOX is tested with; Windows, where PowerShell
    # serves, has no sandbox route. Grant PowerShell there too once Claude Code's sandbox is shown to confine it.
    granted = shells[:1] if capabilities.sandbox else shells
    return [*tools, *granted], [f"{s}({c} *)" for s in shells for c in ("git push", "git commit", "gh")]


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
    # --effort only when a role this preflight serves sets one: a host that sets none needs no Claude Code that has it
    flags = FLAGS + (("--effort",) if any(settings.effort_of(r) for r in settings.serving("claude")) else ())
    missing = [f for f in flags if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", h.out)]
    if h.code != 0 or missing:
        raise sh.DriverError(f"preflight: claude --help (exit {h.code}) lists no {', '.join(missing) or 'flags'}; update Claude Code")


def execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings):
    from . import observed
    with tempfile.TemporaryDirectory(prefix="ouro-claude-proof-") as directory:
        root = Path(directory)
        policy = root / "policy.json"
        evidence = root / "commands.jsonl"
        launch, env = {}, {}
        if name == "BUILD" and isinstance(tools, Capabilities):
            commands = list(tools.commands)
            policy.write_text(json.dumps({"root": str(Path(cwd).resolve()), "commands": commands,
                                         "evidence": str(evidence)}), encoding="utf-8")
            launch = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{
                "type": "command", "command": observed.invocation("hook", policy)}]}]}}
        elif isinstance(tools, Capabilities) and tools.sandbox:
            launch = dict(SANDBOX)
            # Claude Code's sandbox also admits writes under <CLAUDE_CODE_TMPDIR or TMPDIR>/claude-<uid>, by default
            # /tmp/claude-<uid>, which every session of the uid shares: this launch gets its own, deleted when it returns.
            (root / "tmp").mkdir()
            env = {"CLAUDE_CODE_TMPDIR": str(root / "tmp"), "TMPDIR": str(root / "tmp")}
        if chosen := advisor(settings.advisor, model):
            launch["advisorModel"] = chosen
        elif settings.advisor:
            sh.say(f"node {name}: runtime advisor up: {model} is the top tier, so this node has no advisor")
        extra = ["--settings", json.dumps(launch)] if launch else []
        (root / "gh").mkdir()
        probe = isinstance(tools, Capabilities) and tools.probe
        env.update(probe_env(probe, root / "gh"))
        if probe:
            env.update(_probe_home(root))
        answer, session, usage, cost = _execute(
            name, prompt, model, tools, cwd, timeout, denied, schema, settings, extra, env)
        usage = {**usage, "tool_calls": [json.loads(line) for line in evidence.read_text(
            encoding="utf-8").splitlines()] if evidence.exists() else []}
        return answer, session, usage, cost


def _probe_home(root):
    """A PROBE launch's private home and the variables that find Claude Code's state past it. A set
    CLAUDE_CONFIG_DIR is where Claude Code keeps its global .claude.json as well, so a declared one is passed on.
    With none declared that file is the developer's ~/.claude.json, and setting the variable would move it into
    ~/.claude: the home gets a link to their ~/.claude (login, settings) and a copy of their ~/.claude.json, whose
    writes stay in the scratch home. On Windows the link is a directory junction, which needs no privilege."""
    developer = Path.home()
    declared = os.environ.get("CLAUDE_CONFIG_DIR")
    env = private_home(root)
    if declared:
        return {**env, "CLAUDE_CONFIG_DIR": declared}
    home = Path(env["HOME"])
    if (developer / ".claude").is_dir():
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(developer / ".claude"), str(home / ".claude"))
        else:
            (home / ".claude").symlink_to(developer / ".claude", target_is_directory=True)
    if (developer / ".claude.json").is_file():
        shutil.copyfile(developer / ".claude.json", home / ".claude.json")
    return {**env, "CLAUDE_CONFIG_DIR": None}  # an inherited empty value is no declaration either


def _execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings, extra=(), env=None):
    if isinstance(tools, Capabilities):
        tools, denied = tools_for(tools)
    argv = sh.argv_for("claude", "-p", "--output-format", "json", "--model", model,
                       *(["--effort", settings.effort] if settings.effort else []), "--allowedTools", ",".join(tools),
                       *(["--disallowedTools", ",".join(denied)] if denied else []),
                       "--permission-mode", "dontAsk", "--json-schema", json.dumps(schema), *extra)
    # Auto-memory is the launching user's, keyed by repository: a node reads only what every host gives it.
    # A host's CLAUDE_CODE_EFFORT_LEVEL may take precedence over --effort, so a configured effort removes it.
    r = sh.run(argv, cwd=cwd, input_text=prompt, timeout=timeout,
               env={**worker_env(settings), **(env or {}), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                    **({"CLAUDE_CODE_EFFORT_LEVEL": None} if settings.effort else {})})
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
    models = env.get("modelUsage")
    # A second model in modelUsage is how a call to the advisor shows.
    return answer, env.get("session_id", ""), {**(env.get("usage") or {}), "num_turns": env.get("num_turns"),
                                               "second_model": len(models) > 1 if isinstance(models, dict) else None}, env.get("total_cost_usd")


def _attempt(name, prompt, model, tools, cwd, timeout, denied, schema, settings, diagnostic):
    answer, session, usage, cost = execute(name, prompt, model, tools, cwd, timeout, denied, schema, settings)
    diagnostic["session"] = diagnostics.identifier(session)
    for target, source in (("input", "input_tokens"), ("output", "output_tokens"),
                           ("cache_read", "cache_read_input_tokens"), ("cache_creation", "cache_creation_input_tokens")):
        diagnostic[target] = diagnostics.count((usage or {}).get(source))
    diagnostic["turns"] = diagnostics.count((usage or {}).get("num_turns"))
    diagnostic["advisor"] = diagnostics.identifier(advisor(settings.advisor, model))
    diagnostic["second_model"] = (usage or {}).get("second_model")
    return answer, session, usage, cost


INTERFACE = Interface(preflight=lambda settings, capabilities: preflight(settings) or (), admits=lambda capabilities: True,
                      worktree_readiness=NOT_REQUIRED,
                      execute=_attempt, probe_confinement=UNCONFINED, sandbox_route=sandbox_route,
                      validate_answer=UNSUPPORTED)
