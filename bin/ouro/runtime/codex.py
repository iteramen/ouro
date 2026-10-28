"""Codex transport with an isolated file bridge and no model-visible shell tool."""
import json
import os
import stat
import tempfile
from pathlib import Path

from .. import sh
from ..coordination import worker_env
from . import Capabilities, READ, diagnostics, process, schema
from . import codex_events
from .claude import tail
from .network import WINDOWS, readiness as network_readiness
from .tools import git_readiness

MIN_VERSION = (0, 163, 0)
DISABLED = ("shell_tool", "apps", "plugins", "multi_agent", "hooks", "memories", "shell_snapshot",
            "image_generation", "browser_use", "computer_use", "view_image", "request_permissions_tool", "tool_suggest",
            "skill_search", "skill_mcp_dependency_install")


def options(cwd, *, capabilities=None):
    values = {**{"features." + f: False for f in DISABLED}, "web_search": "disabled",
              "project_doc_max_bytes": 0, "mcp_servers": {}, "skills.config": [],
              "skills.include_instructions": False, "skills.bundled.enabled": False,
              "projects": {str(Path(cwd).resolve()): {"trust_level": "untrusted"}}}
    if capabilities == READ:
        values["features.code_mode.enabled"] = False
    return [arg for key, value in values.items() for arg in ("-c", key + "=" + toml(value))]


def toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml(v) for k, v in value.items()) + "}"
    return json.dumps(value)


def bootstrap(name, tools):
    """Trusted adapter guidance; repository instructions remain user context."""
    text = ("Use only the ouro bridge operations actually granted to this worker. Native Codex tools remain read-only; "
            "that native restriction does not remove separately granted bridge operations. ")
    text += ("Bridge write/edit/delete are granted for ordinary worktree files; protected paths remain refused. "
             if tools.edit else "Editing is not granted; do not write, edit or delete files. ")
    if name != "BUILD" or not tools.edit:
        return text
    try:
        plugin = sh.PLUGIN.resolve(strict=True)
        source = (plugin / "skills/run/SKILL.md").resolve(strict=True)
        if not source.is_relative_to(plugin):
            raise ValueError("outside plugin")
        with os.fdopen(os.open(source, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not a regular skill")
            raw = stream.read(32 * 1024 + 1)
        if not raw or len(raw) > 32 * 1024:
            raise ValueError("empty or oversized skill")
        reference = raw.decode("utf-8-sig")
    except (OSError, RuntimeError, UnicodeError, ValueError) as ex:
        raise sh.DriverError("codex BUILD: trusted run skill is missing, invalid or outside the installed plugin; reinstall ouro") from ex
    return (text + "You are already inside BUILD; the driver owns the workflow. Do not invoke the driver again, "
            "activate skills, commit, publish or write to the tracker. Environment-blocked approved tests use "
            "validation_pending with cmd/reason, never invented success or an owner design question; runtime failures remain errors. "
            "The complete run skill below describes the outer caller. Its 'this session' restriction refers to that caller, "
            "not to your admitted bridge edits. This reference grants no external file access.\n\n" + reference)


def preflight(settings, *, capabilities=None):
    """Default checks the full runtime; only an explicit READ contract omits command probes."""
    if capabilities is not None and not isinstance(capabilities, Capabilities):
        raise sh.DriverError("preflight codex: a capability contract is required")
    if WINDOWS and capabilities != READ:
        raise sh.DriverError("preflight codex: native Windows Codex supports file-only triage; command/edit/probe workers are unsupported; "
                             "select a separate Claude developer profile via --developer-config or OURO_DEVELOPER_CONFIG "
                             "for a Windows driver with repository CI validation, or use native Linux Codex; "
                             "explicit caller-validation handoffs still require native Linux")
    try:
        binary = sh.argv_for("codex")
    except sh.DriverError as ex:
        raise sh.DriverError(f"preflight codex: {ex}; install Codex CLI 0.163.0+ on this host") from ex
    v = sh.run([*binary, "--version"], timeout=30, env=worker_env(settings))
    import re
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", v.out)
    if v.code != 0 or not match or tuple(map(int, match.groups())) < MIN_VERSION:
        raise sh.DriverError("preflight codex: CLI 0.163.0+ required; update Codex")
    auth = sh.run([*binary, "login", "status"], timeout=30, env=worker_env(settings))
    if auth.code != 0 or not auth.text.lower().startswith("logged in"):
        raise sh.DriverError("preflight codex: not authenticated; run codex login in this host context")
    help_result = sh.run([*binary, "exec", "--help"], timeout=30, env=worker_env(settings))
    for flag in ("--ignore-user-config", "--ignore-rules", "--output-schema", "--output-last-message", "--ephemeral"):
        if flag not in help_result.out:
            raise sh.DriverError(f"preflight codex: missing {flag}; update Codex")
    # Parse locally; never print the catalog, which can contain large instruction bodies.
    catalog = sh.run([*binary, "debug", "models"], timeout=120, env=worker_env(settings))
    try:
        models = {m["slug"] for m in json.loads(catalog.out)["models"]}
    except (ValueError, KeyError, TypeError):
        raise sh.DriverError(f"preflight codex: model catalog unavailable: {tail(catalog.err)}")
    unavailable = set(settings.models.values()) - models - {"default"}
    if unavailable:
        raise sh.DriverError(f"preflight codex: account does not serve models {', '.join(sorted(unavailable))}")
    # Compile/import the bridge before the driver creates a branch or posts anything.
    from .tools import Bridge, sandbox_argv
    if {t["name"] for t in Bridge({"root": ".", "edit": False, "commands": [], "probe": False}).tools()} != {"read", "list", "search"}:
        raise sh.DriverError("preflight codex: file-only capability contract unavailable")
    import sys
    with tempfile.TemporaryDirectory(prefix="ouro-sandbox-probe-") as root:
        if capabilities != READ:
            try:
                probe = process.run(sandbox_argv(root, [sys.executable, "-c", "print('ouro-sandbox-ready')"]), cwd=root, timeout=30, env=worker_env(settings))
            except sh.DriverError as ex:
                raise sh.DriverError(f"preflight codex: sandbox/process readiness failed: {ex}; check this host's process permissions") from ex
            if probe.code != 0 or "ouro-sandbox-ready" not in probe.out:
                raise sh.DriverError(f"preflight codex: OS sandbox unavailable; configure Codex sandbox on this host: {tail(probe.err)}")
            network_readiness(root, env=worker_env(settings))
            # Inspection also runs outside repositories. Never copy configured private templates.
            for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
                if os.environ.get(key):
                    raise sh.DriverError("preflight codex: repository-routing Git environment prevents a generated fixture")
            initialized = sh.run(sh.argv_for("git", "init", "--quiet", "--template=", root),
                                 cwd=root, env=worker_env(settings), timeout=30)
            if initialized.code or initialized.err:
                raise sh.DriverError("preflight codex: generated Git fixture unavailable")
            git_readiness(root, env=worker_env(settings))
        # A catalog alone cannot establish that auth, model access, MCP admission and structured
        # outputs actually work. Probe each selected model on generated public data before writes.
        ready = Path(root) / "readiness.txt"
        ready.write_text("ouro-ready", encoding="utf-8")
        selected = {settings.model(role, "") for role in ("builder", "builder_trivial", "reviewer")}
        proofs = []
        for model in sorted(selected):
            answer, session, usage, cost = execute(
                "CHECKPOINT", "Read readiness.txt. Return its exact content as finding, with stop null.",
                model, READ, root, 180, None, {}, settings)
            read = any(c.get("tool") == "read" and not c.get("error")
                       and (Path(root) / (c.get("path") or "")).resolve() == ready.resolve()
                       for c in usage.get("tool_calls", []))
            if answer.get("finding") != "ouro-ready" or not read:
                raise sh.DriverError(f"preflight codex model {model}: file capability probe failed")
            proofs.append((session, usage, cost))
        return proofs


def execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings):
    with diagnostics.attempt(name, "codex", model) as diagnostic:
        return _execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings, diagnostic)


def _execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings, diagnostic):
    if not isinstance(tools, Capabilities):
        raise sh.DriverError(f"codex {name}: a capability contract is required before dispatch")
    if WINDOWS and tools != READ:
        raise sh.DriverError(f"codex {name}: native Windows supports file-only capabilities; "
                             "command, probe and edit workers require an established disabled-network boundary")
    context = bootstrap(name, tools)
    if tools.edit or tools.probe or any(c.startswith("git ") for c in tools.commands):
        git_readiness(cwd, env=worker_env(settings))
    with tempfile.TemporaryDirectory(prefix="ouro-codex-") as directory:
        scratch = Path(directory)
        policy = {"root": str(Path(cwd).resolve()), "edit": tools.edit, "commands": list(tools.commands),
                  "probe": tools.probe, "evidence": str(scratch / "tools.jsonl")}
        (scratch / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        (scratch / "schema.json").write_text(json.dumps(schema.SCHEMAS[name]), encoding="utf-8")
        bridge = {"ouro": {"command": sh.which("python3"),
                  "args": [str(Path(__file__).with_name("tools.py")), str(scratch / "policy.json")],
                  "required": True, "tool_timeout_sec": 660, "default_tools_approval_mode": "approve"}}
        # Resolve .cmd/.ps1 Python launchers in the same way as every driver subprocess.
        bridge["ouro"]["command"], *bridge["ouro"]["args"] = [*sh.argv_for("python3"), *bridge["ouro"]["args"]]
        argv = [*sh.argv_for("codex"), "-a", "never", "exec", "--ignore-user-config", "--ignore-rules",
                "--ephemeral", "--json", "--skip-git-repo-check", "--sandbox", "read-only", *options(cwd, capabilities=tools),
                "-c", "developer_instructions=" + toml(context),
                "-c", "mcp_servers=" + toml(bridge), "--output-schema", str(scratch / "schema.json"),
                "--output-last-message", str(scratch / "answer.json")]
        if model != "default":
            argv += ["--model", model]
        if settings.effort:
            argv += ["-c", "model_reasoning_effort=" + json.dumps(settings.effort)]
        prompt = ("First call the ouro bridge list tool to verify file access. Use only the ouro file bridge. Its list/read/search tools are file operations, not commands. "
                  "Do not substitute unavailable built-in tools. Optional stop is null when continuing.\n\n" +
                  instructions(cwd) + "\n\n" + prompt)
        try:
            r = process.run([*argv, "-"], cwd=cwd, prompt=prompt, timeout=timeout, env=worker_env(settings),
                            observe=lambda output, code: diagnostics.transport(diagnostic, output, code),
                            output_limit=codex_events.CAPTURE_LIMIT)
        except sh.DriverError as ex:
            raise sh.DriverError(f"codex {name}: {ex}") from ex
        diagnostics.transport(diagnostic, r.out, r.code)
        transcript = codex_events.scan(r.out)
        if r.code != 0 or transcript.error or transcript.failure or not transcript.completed:
            reason = r.err or transcript.error or transcript.failure or "completion missing"
            raise sh.DriverError(f"codex {name}: execution failed (exit {r.code}); {tail(reason)}")
        diagnostic["phase"] = "structured_result"
        try:
            if (scratch / "answer.json").stat().st_size > codex_events.FINAL_LIMIT:
                raise sh.DriverError("final structured result limit exceeded")
            answer = json.loads((scratch / "answer.json").read_text(encoding="utf-8"))
            schema.validate(answer, schema.SCHEMAS[name])
            schema.validate_answer(name, answer)
        except (OSError, ValueError, sh.DriverError) as ex:
            raise sh.DriverError(f"codex {name}: missing or malformed final structured result: {ex}") from ex
        if answer.get("stop") is None:
            answer.pop("stop", None)
        session = transcript.session
        if not session:
            raise sh.DriverError(f"codex {name}: missing thread identity")
        usage = transcript.usage
        usage["cache_read_input_tokens"] = usage.get("cached_input_tokens", 0)
        usage["provider"], usage["model"] = "codex", model
        diagnostic["phase"] = "bridge_evidence"
        evidence = scratch / "tools.jsonl"
        usage["tool_calls"] = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()] if evidence.exists() else []
        faults = [call for call in usage["tool_calls"] if call.get("infrastructure")]
        if faults:
            raise sh.DriverError(f"codex {name}: tool infrastructure failed: {faults[0].get('error') or 'runtime unavailable'}")
        if not any(not call.get("error") for call in usage["tool_calls"]):
            raise sh.DriverError(f"codex {name}: file bridge was not usable; check MCP tool admission")
        denied_paths = [e["path"] for e in usage["tool_calls"] if e.get("error") and e.get("path")]
        if name == "BUILD" and denied_paths:
            answer["_denied"] = [{"tool_input": {"file_path": path}} for path in denied_paths]
        return answer, session, usage, None


def instructions(cwd):
    root = Path(cwd).resolve()
    paths = [root / "AGENTS.md", root / "CLAUDE.md", *sorted((root / ".claude" / "rules").rglob("*.md"))]
    blocks = []
    for path in paths:
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise sh.DriverError("codex instructions leave the worktree")
            blocks.append(f"### {path.relative_to(root).as_posix()}\n" + path.read_text(encoding="utf-8-sig"))
    text = "\n\n".join(blocks)
    if len(text.encode("utf-8")) > 128 * 1024:
        raise sh.DriverError("codex repository instructions exceed 128 KiB")
    return ("Repository instructions follow. Rules with paths front matter apply only to their declared paths; "
            "unscoped rules apply throughout the worktree.\n\n" + text) if text else ""
