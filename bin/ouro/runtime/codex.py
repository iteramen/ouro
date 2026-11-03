"""Codex transport with an isolated file bridge and no model-visible shell tool."""
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .. import sh
from .process import worker_env
from . import Capabilities, READ, diagnostics, process, schema
from . import codex_events
from .network import WINDOWS, readiness as network_readiness
from .tools import git_readiness
from .backends import CONFINED, Interface

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
        raise sh.DriverError(f"preflight codex: model catalog unavailable: {sh.tail(catalog.err)}")
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
                raise sh.DriverError(f"preflight codex: OS sandbox unavailable; configure Codex sandbox on this host: {sh.tail(probe.err)}")
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
        identity = {"cli": match.group(0), "runtime": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        proofs = []
        for model in sorted(selected):
            answer, session, usage, cost = execute(
                "CHECKPOINT", "Read readiness.txt. Return its exact content as finding, with stop null.",
                model, READ, root, 180, None, {}, settings)
            proof = {**file_proof(answer, usage.get("tool_calls", []), root, ready), **identity}
            diagnostics.file_proof(session, model, proof)
            if proof["proof"] != "accepted":
                raise sh.DriverError(f"preflight codex model {model}: file capability probe failed: {proof['proof']} "
                                     f"(content {proof['content']}, read path {proof['read_path']}); "
                                     "see the attempt diagnostics, then rerun the same command to retry")
            proofs.append((session, usage, cost))
        return proofs


def file_proof(answer, calls, root, ready):
    """Classify the generated read proof in fixed classes and booleans; never a path or answer text.
    Accepted only on the exact content and a successful read whose resolved path is the generated file."""
    finding = answer.get("finding")
    content = ("exact" if finding == "ouro-ready" else "absent" if not finding else
               "numbered" if re.fullmatch(r"\s*1:\s*ouro-ready\s*", str(finding)) else
               "whitespace" if str(finding).strip() == "ouro-ready" else "other")
    done = [c for c in calls if not c.get("error")]
    paths = {read_path(c.get("path"), root, ready) for c in done if c.get("tool") == "read"}
    path = next((p for p in ("exact", "alias", "mismatch") if p in paths), "absent")
    failed = any(c.get("tool") == "read" and c.get("error") for c in calls)
    proof = ("failed_read" if path == "absent" and failed else "missing_read" if path == "absent" else
             "wrong_read" if path != "exact" else "content" if content != "exact" else "accepted")
    return {"proof": proof, "content_match": content == "exact", "content": content,
            "read_observed": path == "exact", "read_path": path, "read_failed": failed,
            "stop_present": answer.get("stop") is not None,
            "tools": "+".join(sorted({n for c in done if (n := diagnostics.identifier(c.get("tool")))})) or "none"}


def read_path(value, root, ready):
    """An alias (hard link, or a case variant the filesystem serves as the same file) names the file
    another way; it is classified, never admitted."""
    path = (Path(root) / (value or "")).resolve()
    if path == ready.resolve():
        return "exact"
    try:
        return "alias" if os.path.samefile(path, ready) else "mismatch"
    except OSError:
        return "mismatch"


def execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings):
    with diagnostics.attempt(name, "codex", model) as diagnostic:
        return _execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings, diagnostic)


def _execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings, diagnostic):
    if not isinstance(tools, Capabilities):
        raise sh.DriverError(f"codex {name}: a capability contract is required before dispatch")
    if WINDOWS and tools != READ:
        raise sh.DriverError(f"codex {name}: native Windows supports file-only capabilities; "
                             "command, probe and edit workers require an established disabled-network boundary")
    if tools.probe:
        from .tools import probe_root
        probe_root(cwd)
    context = bootstrap(name, tools)
    if tools.edit or tools.probe or any(c.startswith("git ") for c in tools.commands):
        if tools.probe:
            git_readiness(cwd, env=worker_env(settings), probe=True)
        else:
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
        # MCP's default environment omits CODEX_HOME. Preserve the caller's exact
        # configuration context, including aliases that PROBE admission must refuse.
        if "CODEX_HOME" in os.environ:
            bridge["ouro"]["env"] = {"CODEX_HOME": os.environ["CODEX_HOME"]}
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
        preserve = ("; failed BUILD: verify the old worker ended; inspect and preserve only identified "
                    "task-owned partial files and retained logs, preserve unrelated edits before a normal run; "
                    "stop if ownership or liveness is unproved. Require a new completed BUILD and fresh "
                    "exact-head proof/review" if name == "BUILD" else "")
        try:
            r = process.run([*argv, "-"], cwd=cwd, prompt=prompt, timeout=timeout, env=worker_env(settings),
                            observe=lambda output, code: diagnostics.transport(diagnostic, output, code),
                            output_limit=codex_events.CAPTURE_LIMIT)
        except sh.DriverError as ex:
            raise sh.DriverError(f"codex {name}: {ex}{preserve}") from ex
        diagnostics.transport(diagnostic, r.out, r.code)
        transcript = codex_events.scan(r.out)
        if r.code != 0 or transcript.error or transcript.failure or not transcript.completed:
            reason = transcript.error or diagnostic["incomplete_reason"]
            raise sh.DriverError(f"codex {name}: execution failed (exit {r.code}); {reason}{preserve}")
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
        # Failed mutations remain conservative barriers, including ambiguous denials.
        # Read and command failures are observations, never configuration proposals.
        if name == "BUILD":
            answer["_denied"] = [
                {"tool_name": e["tool"], "tool_input": {**(e.get("tool_input") or {}),
                                                       "file_path": e["path"]}}
                for e in usage["tool_calls"]
                if e.get("error") and e.get("path") and e.get("tool") in ("write", "edit", "delete")
            ]
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


def _worktree_readiness(settings, main, target, branch, base, *, scratch=None):
    from . import git_capabilities
    git_capabilities.worktree_readiness(settings, main, target, branch, base, scratch=scratch)


INTERFACE = Interface(preflight=lambda settings, capabilities: preflight(settings, capabilities=capabilities),
                      worktree_readiness=_worktree_readiness, execute=_execute, probe_confinement=CONFINED,
                      validate_answer=schema.validate_answer)
