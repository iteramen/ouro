"""The node adapter: one function that asks a model a question and returns the JSON it answered.

Real mode selects a developer-bound harness with the prompt on stdin (a command line is capped near 32k
characters on Windows, and the briefs are longer). Stub mode, selected by OURO_NODE_STUB naming
a directory, spawns nothing: the k-th call of a node in this process returns <dir>/<name>.<k>.json,
else <dir>/<name>.json, and leaves <dir>/<name>.<k>.call.json holding the cwd, the tools and the model it got.
"""
import json
import os
import re
from dataclasses import replace
from pathlib import Path

from . import developer, sh
from .runtime import Capabilities, READ, PROBE, builder, diagnostics
from .runtime.backends import BACKENDS, NOT_REQUIRED, UNSUPPORTED

USAGE = []  # one row per node call: {node, session, input, output, cache_read, cache_creation, cost}
_CALLS = {}

TIMEOUTS = {"BUILD": 3600, "REVIEW": 3600, "TRIAGE": 600, "FOLLOWUP": 600, "CHECKPOINT": 3600}
DEFAULT_BUILDER = "opus"  # the builder every reader of models.builder falls back to; a trivial build keeps sonnet
MODEL_ROLE = {"BUILD": "builder", "REVIEW": "reviewer", "TRIAGE": "triager", "FOLLOWUP": "triager", "CHECKPOINT": "checkpointer"}
READ_TOOLS = ["Read", "Grep", "Glob"]

SCHEMAS = {
    "TRIAGE": {"type": "object", "required": ["verdict", "comment"], "properties": {
        "verdict": {"type": "string", "enum": ["PROMOTE", "NEEDS-RULING", "CHECKPOINT", "SPLIT", "STALE"]},
        "size": {"type": "string"}, "flow": {"type": "string"}, "doc_impact": {"type": "string"}, "manual": {"type": "array"}, "labels": {"type": "array"},
        "comment": {"type": "string"}, "question": {"type": "string"}}},
    "BUILD": {"type": "object", "properties": {
        "subject": {"type": "string"}, "body": {"type": "string"}, "claims": {"type": "array"},
        "gates_run": {"type": "array"}, "residue": {"type": "array"}, "stop": {"type": "object"}, "manual": {"type": "array"},
        "validation_pending": {"type": "array", "items": {"type": "object", "required": ["cmd", "reason"], "additionalProperties": False,
            "properties": {"cmd": {"type": "string"}, "reason": {"type": "string"}}}},
        "replies": {"type": "array"}, "door": {"type": "string"}, "blast_radius": {"type": "string"}}},
    "REVIEW": {"type": "object", "required": ["verdict", "findings"], "properties": {
        "verdict": {"type": "string", "enum": ["CLEAN", "FINDINGS"]}, "findings": {"type": "array"},
        "false_record_sentences": {"type": ["integer", "string"]}, "files_read": {"type": "array"},
        "stop": {"type": ["object", "null"], "properties": {"reason": {"type": "string"}, "question": {"type": "string"}}}}},
    "CHECKPOINT": {"type": "object", "properties": {"finding": {"type": "string"}, "stop": {"type": "object"}}},
    "FOLLOWUP": {"type": "object", "required": ["issues"], "properties": {"issues": {"type": "array"}}},
}


def parse_first_json(text):
    """The first complete JSON object in text; a fenced ```json block counts first."""
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    for chunk in ([fence.group(1)] if fence else []) + [text]:
        dec = json.JSONDecoder()
        for m in re.finditer(r"\{", chunk):
            try:
                obj, _ = dec.raw_decode(chunk[m.start():])
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def _record(name, session, usage, cost, backend="claude"):
    usage = usage if isinstance(usage, dict) else {}
    USAGE.append({"node": name, "backend": backend, "session": session or "stub", "input": usage.get("input_tokens", 0),
                  "output": usage.get("output_tokens", 0), "cache_read": usage.get("cache_read_input_tokens", 0),
                  "cache_creation": usage.get("cache_creation_input_tokens", 0), "cost": cost,
                  "provider": usage.get("provider", "claude"), "model": usage.get("model"),
                  "tool_calls": usage.get("tool_calls", [])})


def _stub(name, prompt, cwd, directory, tools, denied, model, backend="claude", sandbox=False):
    k = _CALLS[name] = _CALLS.get(name, 0) + 1
    d = Path(directory)
    f = d / f"{name}.{k}.json"
    if not f.is_file():
        f = d / f"{name}.json"
    if not f.is_file():
        raise sh.DriverError(f"node stub: no {name}.{k}.json or {name}.json in {d}")
    (d / f"{name}.{k}.prompt.txt").write_bytes(prompt.encode("utf-8"))
    (d / f"{name}.{k}.call.json").write_text(json.dumps({"cwd": str(cwd), "cwd_exists": Path(cwd).is_dir(), "tools": tools,
                                                          "denied": denied, "model": model, "backend": backend,
                                                          "sandbox": sandbox}), encoding="utf-8")
    ans = json.loads(f.read_text(encoding="utf-8"))
    usage = ans.pop("_usage", None) or {}
    commands = ans.pop("_run", [])
    if "_raw" in ans:
        _record(name, "stub", usage, 0, backend)
        return ans["_raw"], "stub", ans.get("_stderr", "")
    for rel, content in (ans.pop("writes", None) or {}).items():
        target = Path(cwd) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    from . import execution, validation
    calls = []
    for command in commands:
        if f"Bash({command})" not in tools:
            raise sh.DriverError("stub execution command was not granted")
        argv, env = validation.command(command)
        before = execution.fingerprint(cwd)
        r = sh.run(sh.argv_for(*argv), cwd=cwd, env=env, timeout=600)
        after = execution.fingerprint(cwd)
        calls.append({"tool": "run", "command": command, "exit": r.code,
                      "inputs": before if before == after else None})
    for rel, content in (ans.pop("_after_run", None) or {}).items():
        (Path(cwd) / rel).write_bytes(content.encode("utf-8"))
    _record(name, "stub", {**usage, "tool_calls": calls}, 0, backend)
    return json.dumps(ans), "stub", ""


def model(env, role, default):
    legacy = env.lookup("models." + developer.INHERITS.get(role, role), default)
    return getattr(env, "runtime", developer.Settings()).model(role, legacy)


def worktree_readiness(settings, main, target, branch, base, *, scratch=None):
    """Backend proof before worktree creation, catch-up or repository setup."""
    ready = BACKENDS[settings.backend].interface.worktree_readiness
    if not os.environ.get("OURO_NODE_STUB") and ready != NOT_REQUIRED:
        ready(settings, main, target, branch, base, scratch=scratch)



# Each role's contracts, narrowest first; None is full readiness: BUILD's edits, and the commands a CHECKPOINT brief grants.
CONTRACTS = {"triager": (READ,), "checkpointer": (READ, None), "builder": (None,), "builder_trivial": (None,),
             "reviewer": (READ, PROBE)}


def contract_name(capabilities):
    return ("full" if capabilities is None else "READ" if capabilities == READ else
            "PROBE" if capabilities == PROBE else "commands")


def widest(settings):
    """{role: the widest contract its backend admits, else its widest}: what `inspect --check` proves."""
    return {role: next((c for c in reversed(options) if BACKENDS[settings.backend_of(role)].interface.admits(c)), options[-1])
            for role, options in CONTRACTS.items()}


def plan(settings, needs):
    """{backend: (settings scoped to its roles, READ or None)} for `needs`, {role: contract}: each backend once,
    file-only when all its roles are. A role whose backend does not admit its contract is refused; there is no fallback."""
    groups = {}
    for role, contract in needs.items():
        backend = BACKENDS[settings.backend_of(role)]
        if not backend.interface.admits(contract):
            raise sh.DriverError(f"preflight: role {role} selects {backend.label}, which does not admit its "
                                 f"{contract_name(contract)} contract; select another backend in [runtime.roles.{role}]")
        groups.setdefault(backend.name, {})[role] = contract
    # shortcut: a backend's proof runs at its first role's effort; distinct efforts on one backend are not each proven.
    return {name: (replace(settings, serves=tuple(roles), effort=settings.effort_of(next(iter(roles)))),
                   READ if all(c == READ for c in roles.values()) else None)
            for name, roles in groups.items()}


def preflight(settings=None, *, capabilities=None, needs=None):
    """The runtime check before any write. `needs`, {role: the widest contract it can need}, plans one preflight per backend
    its roles select when any is not runtime.backend; otherwise runtime.backend's preflight runs once at `capabilities`."""
    if capabilities is not None and not isinstance(capabilities, Capabilities):
        raise sh.DriverError("preflight: a capability contract is required")
    stub = os.environ.get("OURO_NODE_STUB")
    if stub and not needs:
        return
    cfg = settings or developer.load()
    groups = plan(cfg, needs) if needs and cfg.mixed(needs) else {cfg.backend: (cfg, capabilities)}
    if stub:
        return
    for backend, (scoped, contract) in groups.items():
        for session, usage, cost in BACKENDS[backend].interface.preflight(scoped, contract):
            _record("PREFLIGHT", session, usage, cost, backend)


def _real(name, prompt, model, tools, cwd, timeout, denied, settings=None, role=None):
    cfg = (settings or developer.Settings()).of(role or MODEL_ROLE[name])
    with diagnostics.attempt(name, cfg.backend, model) as diagnostic:
        answer, session, usage, cost = BACKENDS[cfg.backend].interface.execute(
            name, prompt, model, tools, cwd, timeout, denied, SCHEMAS[name], cfg, diagnostic)
    _record(name, session, usage, cost, cfg.backend)
    return json.dumps(answer) if isinstance(answer, dict) else str(answer), session, ""


def run_node(name, prompt, model, allowed_tools, cwd, timeout, denied=None, runtime=None, role=None):
    """Ask node `name` and return its answer as a dict; a node with no parseable JSON twice is a driver error.

    The node dispatches through the backend of `role`, by default MODEL_ROLE's for the node."""
    caps = allowed_tools if isinstance(allowed_tools, Capabilities) else None
    stub = os.environ.get("OURO_NODE_STUB")
    if caps and stub:
        from .runtime.claude import tools_for
        allowed_tools, denied = tools_for(caps)
    role = role or MODEL_ROLE.get(name, "builder")
    backend = runtime.backend_of(role) if runtime else "claude"
    session = diag = ""
    for _ in range(2):
        text, session, diag = (_stub(name, prompt, cwd, stub, allowed_tools, denied, model, backend, bool(caps and caps.sandbox)) if stub
                               else _real(name, prompt, model, caps or allowed_tools, cwd, timeout, denied, runtime, role))
        ans = parse_first_json(text)
        if ans is not None and ans.get("stop") is not None:
            stop = ans["stop"]
            question = stop.get("question") if isinstance(stop, dict) else stop
            if not isinstance(question, str) or not question.strip():
                raise sh.DriverError(f"node {name}: infrastructure or malformed stop with no question")
        check = BACKENDS[backend].interface.validate_answer if runtime else UNSUPPORTED
        if ans is not None and check != UNSUPPORTED:
            check(name, ans)
        if ans is not None:
            return ans
        diag = diag or f"the answer held no JSON object: {sh.tail(text, 4)!r}"
    raise sh.DriverError(f"node {name} returned no parseable JSON twice (session {session or 'unknown'}): {diag}")


def usage_table():
    if not USAGE:
        return ""
    rows = ["| node | session | input | output | cache read | cache creation | cost USD | backend |", "|---|---|---|---|---|---|---|---|"]
    rows += [f"| {u['node']} | {u['session']} | {u['input']} | {u['output']} | {u['cache_read']} | "
             f"{u['cache_creation']} | {u['cost'] if u['cost'] is not None else 'unknown'} | {u.get('backend', 'claude')} |" for u in USAGE]
    return "\n".join(rows)
