"""Qwen Code transport for nodes that read, run commands or edit. Tool spelling and the CLI envelope stay inside this
module and qwen_isolation.

The model and its OpenAI-compatible endpoint come from the launch environment (OPENAI_API_KEY,
OPENAI_BASE_URL, and OPENAI_MODEL for the `default` model); Qwen Code's own user and project
configuration are never read.
"""
import json
import os
import re
import shlex
import tempfile
from pathlib import Path

from .. import sh
from .process import probe_env, worker_env
from . import PROBE, READ, Capabilities, builder, diagnostics, process, schema
from .backends import NOT_REQUIRED, UNCONFINED, Interface
from .instructions import repository, run_skill
from .qwen_isolation import SHARED_TOOLS, SHELL, WRITES, final_text, hook_command, isolated_env, settings_for, tool_calls, unhooked

MIN_VERSION = (0, 26, 6)
FLAGS = ("--auth-type", "--output-format", "--approval-mode", "--json-schema", "--chat-recording", "--model")
CREDENTIALS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
PLAIN_TEXT = "plain text instead of calling the structured_output tool"
SHARED_INPUT = {"content": "content", "old_string": "old", "new_string": "new"}
BWRAP = "/usr/bin/bwrap"  # the one bwrap Qwen Code's tool execution sandbox runs; it never searches PATH


def sandbox_route(where):
    """What Qwen Code's tool execution sandbox lacks on platform `where`, or None where it has none: Linux alone,
    since its macOS route is untested."""
    return None if where != "linux" else [] if os.path.exists(BWRAP) else [BWRAP]


def sandboxed(name, tools):
    """Whether the node runs under the tool execution sandbox in place of the allow-list hook: a PROBE review node alone."""
    if tools.sandbox and (name != "REVIEW" or not tools.probe):
        raise sh.DriverError(f"qwen {name}: the tool execution sandbox serves a PROBE review node alone")
    return tools.sandbox


def guidance(tools):
    """The tool line of the prompt; the hook enforces it."""
    shell = tools.probe or bool(tools.commands)
    names = ["read_file", "glob", "grep_search", *(WRITES if tools.edit else ()), *(["run_shell_command"] if shell else [])]
    text = f"Use only {', '.join(names[:-1])} and {names[-1]}."
    if tools.edit:
        text += (" Edits take paths inside the working directory; .git, .claude, .codex, .agents, .aws and .qwen are refused, "
                 "so an edit there goes under manual.")
    if not shell:
        return text
    scope = ("any single command but git push, git commit and gh, with no `$`, glob, brace or git `-c`" if tools.probe else
             "only these exact commands: " + ", ".join(f"`{c}`" for c in tools.commands))
    return (text + " run_shell_command takes " + scope +
            ", one per call, in the working directory and in the foreground; chaining, pipes, redirection, "
            "substitution and leading variable assignments are refused.")


def denied_writes(hook):
    """The hook's refused writes in the shared denial shape (see Capabilities); notebook_edit keeps its own name."""
    return [{"tool_name": SHARED_TOOLS.get(h["tool"], h["tool"]),
             "tool_input": {"file_path": h.get("path"),
                            **{SHARED_INPUT[k]: v for k, v in (h.get("input") or {}).items() if k in SHARED_INPUT}}}
            for h in hook if h.get("tool") in WRITES and not h.get("granted")]


def refuse_repository_config(name, tools, cwd):
    """A command or edit node refuses a working directory whose top level holds a `.qwen` or `.env` entry, in any
    case: Qwen Code loads both from a trusted folder, and a case-insensitive file system resolves `.QWEN` as `.qwen`.
    A READ node stays untrusted, so it dispatches there."""
    if not (tools.probe or tools.commands or tools.edit):
        return
    found = sorted(str(p) for p in Path(cwd).iterdir() if p.name.casefold() in (".qwen", ".env"))
    if found:
        raise sh.DriverError(f"qwen {name}: the working directory holds {', '.join(found)}, which Qwen Code would load as "
                             "repository configuration; a command or edit node does not dispatch there")


def _execute(name, prompt, model, tools, cwd, timeout, denied, ignored_schema, settings, diagnostic):
    if not isinstance(tools, Capabilities):
        raise sh.DriverError(f"qwen {name}: a capability contract is required before dispatch")
    refuse_repository_config(name, tools, cwd)
    reference = run_skill("qwen", "edits") + "\n\n" if name == "BUILD" and tools.edit else ""
    with tempfile.TemporaryDirectory(prefix="ouro-qwen-") as directory:
        scratch = Path(directory)
        evidence = scratch / "hook.jsonl"
        policy = scratch / "policy.json" if tools.probe or tools.commands or tools.edit else None
        if policy:
            policy.write_text(json.dumps({"root": str(Path(cwd).resolve()), "commands": list(tools.commands),
                                          "probe": tools.probe, "edit": tools.edit}), encoding="utf-8")
        (scratch / "system.json").write_text(json.dumps(settings_for(evidence, policy, tools.edit, sandboxed(name, tools))),
                                             encoding="utf-8")
        (scratch / "gh").mkdir()
        # An untrusted folder overrides yolo to `default`; refuse_repository_config keeps the trusted one free of .qwen and .env.
        env = {**isolated_env(settings, scratch, Path(cwd).resolve() if policy else None), **probe_env(tools.probe, scratch / "gh")}
        # Headless `default` removes the shell tool; under `yolo` the hook alone decides each call, or under the sandbox the deny rules.
        argv = sh.argv_for("qwen", "--auth-type", "openai", "--output-format", "json",
                           "--approval-mode", "yolo" if policy else "default",
                           "--json-schema", json.dumps(schema.SCHEMAS[name]), "--chat-recording", "false",
                           *(["--model", model] if model != "default" else []))
        prompt = (guidance(tools) + " Finish by calling the structured_output tool with the answer; "
                  "an answer written as text is lost. Optional stop is null when continuing.\n\n" + reference
                  + repository(cwd, "qwen") + "\n\n" + prompt)
        try:
            r = process.run(argv, cwd=cwd, prompt=prompt, timeout=timeout, env=env)
        except sh.DriverError as ex:
            raise sh.DriverError(f"qwen {name}: {ex}") from ex
        try:
            events = json.loads(r.out)
        except ValueError:
            events = None
        results = [e for e in events if isinstance(e, dict) and e.get("type") == "result"] if isinstance(events, list) else []
        if not results:
            raise sh.DriverError(f"node {name}: qwen exited {r.code} with no result event; stderr tail: {sh.tail(r.err)!r}; stdout tail: {sh.tail(r.out)!r}")
        result = results[-1]
        session = result.get("session_id") or ""
        diagnostic["session"] = diagnostics.identifier(session)
        error = result.get("error")
        reason = str(error.get("message") if isinstance(error, dict) else error or result.get("result", ""))
        succeeded = r.code == 0 and not result.get("is_error") and result.get("subtype") == "success"
        # A model that writes its answer as text, not through the tool, fails the run; the same strict check admits that text.
        answer = result.get("structured_result") if succeeded else (
            final_text(events) if (r.code, result.get("subtype")) == (1, "error_during_execution") and PLAIN_TEXT in reason else None)
        if not succeeded and answer is None:
            raise sh.DriverError(f"node {name} (session {session or 'unknown'}): qwen exited {r.code} with {result.get('subtype')!r}; "
                                 f"{sh.tail(reason)!r}; stderr tail: {sh.tail(r.err)!r}")
        if not session:
            raise sh.DriverError(f"qwen {name}: missing session identity")
        hook = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()] if evidence.exists() else []
        # Under the sandbox no hook runs, so none grants: the sandbox and its deny rules are this node's boundary.
        bypassed = [] if tools.sandbox else unhooked(events, hook)
        if bypassed:
            raise sh.DriverError(f"qwen {name} (session {session}): {', '.join(bypassed)} ran without the allow-list hook's grant; "
                                 "the node boundary is unproven, so the answer is discarded")
        try:
            schema.validate(answer, schema.SCHEMAS[name])
            schema.validate_answer(name, answer)
        except sh.DriverError as ex:
            raise sh.DriverError(f"qwen {name}: missing or malformed structured result: {ex}") from ex
        if answer.get("stop") is None:
            answer.pop("stop", None)
        if name == "BUILD":
            answer["_denied"] = denied_writes(hook)
        reported = result.get("usage")
        usage = {**(reported if isinstance(reported, dict) else {}), "provider": "qwen", "model": model,
                 "tool_calls": tool_calls(events), "hook": hook}
    for target, source in (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_input_tokens")):
        diagnostic[target] = diagnostics.count(usage.get(source))
    return answer, session, usage, None


LISTED = "python3 --version"
# (policy, tool, input, exit): the hook as the node's settings start it, before any model call.
HOOK_CASES = ((None, "run_shell_command", {}, 2), (None, "read_file", {}, 0),
              ("listed", "run_shell_command", {"command": LISTED}, 0), ("listed", "run_shell_command", {"command": "python3 -V"}, 2),
              ("listed", "run_shell_command", {"command": f"{LISTED}; {LISTED}"}, 2),
              ("probe", "run_shell_command", {"command": "git status"}, 0),
              ("probe", "run_shell_command", {"command": "git push origin HEAD"}, 2),
              ("build", "write_file", {"file_path": "notes.txt", "content": "x"}, 0),
              ("build", "write_file", {"file_path": ".claude/settings.json", "content": "{}"}, 2),
              ("build", "edit", {"file_path": "../outside.txt", "old_string": "a", "new_string": "b"}, 2))


def hook_proof(settings):
    """The allow-list hook itself blocks (exit 2) an ungranted tool, an unlisted or chained command, a probe's
    push and a protected or outside write, and admits (exit 0) a granted tool, a listed command, a probe's other
    command and a worktree write. It only decides: no case writes a file."""
    with tempfile.TemporaryDirectory(prefix="ouro-qwen-hook-") as directory:
        scratch = Path(directory)
        evidence = scratch / "hook.jsonl"
        for name, commands, probe in (("listed", [LISTED], False), ("probe", [], True), ("build", [], False)):
            (scratch / f"{name}.json").write_text(json.dumps({"root": directory, "commands": commands, "probe": probe,
                                                              "edit": name == "build"}), encoding="utf-8")
        for policy, tool, args, code in HOOK_CASES:
            r = sh.run(shlex.split(hook_command(evidence, policy and scratch / f"{policy}.json", policy == "build")),
                       input_text=json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args}),
                       timeout=30, env=worker_env(settings))
            if r.code != code:
                what = (f"the {policy} command `{args['command']}`" if "command" in args else
                        f"the {policy} {tool} of `{args['file_path']}`" if "file_path" in args else tool)
                raise sh.DriverError(f"preflight qwen: the allow-list hook did not {'block' if code else 'admit'} {what} "
                                     f"(exit {r.code}): {sh.tail(r.err)!r}")


def file_proof(answer, usage, root):
    """Accepted only on the exact content, a successful read of the generated file, and the hook having granted that read."""
    ready = (Path(root) / "readiness.txt").resolve()
    read = any(c["tool"] == "read" and not c["error"] and Path(root, c["path"] or "").resolve() == ready
               for c in usage["tool_calls"])
    hooked = any(h.get("tool") == "read_file" and h.get("granted") for h in usage["hook"])
    if answer.get("finding") != "ouro-ready" or not read or not hooked:
        raise sh.DriverError(f"preflight qwen model {usage.get('model')}: file read proof failed (content "
                             f"{'exact' if answer.get('finding') == 'ouro-ready' else 'other'}, read observed {read}, "
                             f"hook observed {hooked}); rerun the same command to retry")


WRITTEN = "proof.txt"
ASKS = {"run": f"Run `{LISTED}` with run_shell_command.", "write": f"Write {WRITTEN} with the content ouro-ready using write_file."}
# (label, the roles it serves, its contract, the calls its live proof must show granted): one CLI call per served contract.
LIVE = (("commands", ("checkpointer",), Capabilities(commands=(LISTED,)), ("run",)),
        ("PROBE", ("reviewer",), PROBE, ("run",)),
        ("BUILD", ("builder", "builder_trivial"), builder([LISTED]), ("run", "write")))


def contract_proof(label, calls, usage, root):
    """Accepted only when each of `calls` ran without error and the hook granted it: the listed command, the write of
    WRITTEN inside `root`. A tool Qwen Code withheld or declined shows no such call."""
    target = (Path(root) / WRITTEN).resolve()
    for call in calls:
        ran = any(c["tool"] == call and not c["error"] and ((c.get("command") or "").strip() == LISTED if call == "run" else
                                                             Path(root, c["path"] or "").resolve() == target)
                  for c in usage["tool_calls"])
        hooked = any(h.get("tool") == {"run": SHELL, "write": "write_file"}[call] and h.get("granted") for h in usage["hook"])
        if not ran or not hooked:
            raise sh.DriverError(f"preflight qwen model {usage.get('model')}: the {label} contract's {call} proof failed "
                                 f"(call observed {ran}, hook observed {hooked}); the tool is missing or not granted")


def live_proofs(settings, roles, root):
    """One live CLI call per contract a served role needs beyond READ, each through the hook in a trusted `root`."""
    proofs = []
    for label, served, contract, calls in LIVE:
        role = next((r for r in served if r in roles), None)
        if role is None:
            continue
        model = settings.model(role, "")
        with diagnostics.attempt("CHECKPOINT", "qwen", model) as diagnostic:
            _, session, usage, cost = _execute(
                "CHECKPOINT", " ".join(ASKS[c] for c in calls) + " Then return done as finding, with stop null.",
                model, contract, root, 180, None, {}, settings, diagnostic)
        contract_proof(label, calls, usage, root)
        proofs.append((session, usage, cost))
    return proofs


def preflight(settings, capabilities):
    try:
        binary = sh.argv_for("qwen")
    except sh.DriverError as ex:
        raise sh.DriverError(f"preflight qwen: {ex}; install Qwen Code 0.26.6+ (npm @qwen-code/qwen-code) on this host") from ex
    replies = {arg: sh.run([*binary, arg], timeout=60, env=worker_env(settings)) for arg in ("--version", "--help")}
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", replies["--version"].out)
    version = tuple(map(int, found.groups())) if found else ()
    missing = sorted(set(FLAGS) - set(re.findall(r"--[\w-]+", replies["--help"].out)))
    if any(r.code != 0 for r in replies.values()) or version < MIN_VERSION or missing:
        raise sh.DriverError(f"preflight qwen: Qwen Code 0.26.6+ with {', '.join(FLAGS)} required "
                             f"(version {'.'.join(map(str, version)) or 'unknown'}, missing {', '.join(missing) or 'none'}); update it")
    # Without a planned scope, a READ contract is ouro triage's preflight (the triager's model alone); any other is a run's, every role's.
    roles = settings.serving("qwen", ("triager",)) if capabilities == READ else settings.serving("qwen")
    models = sorted({settings.model(role, "") for role in roles})
    unset = [n for n in (*CREDENTIALS, *(("OPENAI_MODEL",) if "default" in models else ())) if not os.environ.get(n)]
    if unset:
        raise sh.DriverError(f"preflight qwen: set {', '.join(unset)} in the launch environment "
                             "(an OpenAI-compatible endpoint, its key and, for the default model, the model name)")
    hook_proof(settings)
    proofs = []
    with tempfile.TemporaryDirectory(prefix="ouro-qwen-ready-") as root:
        (Path(root) / "readiness.txt").write_text("ouro-ready", encoding="utf-8")
        for model in models:
            with diagnostics.attempt("CHECKPOINT", "qwen", model) as diagnostic:
                answer, session, usage, cost = _execute(
                    "CHECKPOINT", "Read readiness.txt. Return its exact content as finding, with stop null.",
                    model, READ, root, 180, None, {}, settings, diagnostic)
            file_proof(answer, usage, root)
            proofs.append((session, usage, cost))
        if capabilities != READ:
            proofs += live_proofs(settings, roles, root)
    return proofs


INTERFACE = Interface(preflight=preflight, worktree_readiness=NOT_REQUIRED, execute=_execute,
                      probe_confinement=UNCONFINED, sandbox_route=sandbox_route, validate_answer=schema.validate_answer,
                      admits=lambda capabilities: True)
