"""The node adapter: one function that asks a model a question and returns the JSON it answered.

Real mode selects a developer-bound harness with the prompt on stdin (a command line is capped near 32k
characters on Windows, and the briefs are longer). Stub mode, selected by OURO_NODE_STUB naming
a directory, spawns nothing: the k-th call of a node in this process returns <dir>/<name>.<k>.json,
else <dir>/<name>.json, and leaves <dir>/<name>.<k>.call.json holding the cwd, the tools and the model it got.
"""
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path

from . import developer, sh
from .runtime import Capabilities, READ, PROBE, builder, diagnostics

USAGE = []  # one row per node call: {node, session, input, output, cache_read, cache_creation, cost}
_CALLS = {}

TIMEOUTS = {"BUILD": 3600, "REVIEW": 3600, "TRIAGE": 600, "FOLLOWUP": 600, "CHECKPOINT": 3600}
DEFAULT_BUILDER = "opus"  # the builder every reader of models.builder falls back to; a trivial build keeps sonnet
MODEL_ROLE = {"BUILD": "builder", "REVIEW": "reviewer", "TRIAGE": "builder", "FOLLOWUP": "builder", "CHECKPOINT": "builder"}
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


def _record(name, session, usage, cost):
    usage = usage if isinstance(usage, dict) else {}
    USAGE.append({"node": name, "session": session or "stub", "input": usage.get("input_tokens", 0),
                  "output": usage.get("output_tokens", 0), "cache_read": usage.get("cache_read_input_tokens", 0),
                  "cache_creation": usage.get("cache_creation_input_tokens", 0), "cost": cost,
                  "provider": usage.get("provider", "claude"), "model": usage.get("model"),
                  "tool_calls": usage.get("tool_calls", [])})


def _stub(name, prompt, cwd, directory, tools, denied, model):
    k = _CALLS[name] = _CALLS.get(name, 0) + 1
    d = Path(directory)
    f = d / f"{name}.{k}.json"
    if not f.is_file():
        f = d / f"{name}.json"
    if not f.is_file():
        raise sh.DriverError(f"node stub: no {name}.{k}.json or {name}.json in {d}")
    (d / f"{name}.{k}.prompt.txt").write_bytes(prompt.encode("utf-8"))
    (d / f"{name}.{k}.call.json").write_text(json.dumps({"cwd": str(cwd), "cwd_exists": Path(cwd).is_dir(), "tools": tools,
                                                          "denied": denied, "model": model}), encoding="utf-8")
    ans = json.loads(f.read_text(encoding="utf-8"))
    usage = ans.pop("_usage", None) or {}
    commands = ans.pop("_run", [])
    if "_raw" in ans:
        _record(name, "stub", usage, 0)
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
    _record(name, "stub", {**usage, "tool_calls": calls}, 0)
    return json.dumps(ans), "stub", ""


def _tail(text, lines=8):
    return "\n".join(str(text).strip().splitlines()[-lines:])[-800:]


def model(env, role, default):
    legacy = env.lookup("models." + role, default)
    return getattr(env, "runtime", developer.Settings()).model(role, legacy)


def worktree_readiness(settings, main, target, branch, base, *, scratch=None):
    """Backend proof before worktree creation, catch-up or repository setup."""
    if not os.environ.get("OURO_NODE_STUB") and settings.backend == "codex":
        from .runtime import git_capabilities
        git_capabilities.worktree_readiness(settings, main, target, branch, base, scratch=scratch)



def preflight(settings=None, *, capabilities=None):
    if capabilities is not None and not isinstance(capabilities, Capabilities):
        raise sh.DriverError("preflight: a capability contract is required")
    if os.environ.get("OURO_NODE_STUB"):
        return
    cfg = settings or developer.load()
    if cfg.backend == "codex":
        from .runtime import codex
        for session, usage, cost in codex.preflight(cfg, capabilities=capabilities):
            _record("PREFLIGHT", session, usage, cost)
    else:
        from .runtime import claude
        claude.preflight(cfg)


def _real(name, prompt, model, tools, cwd, timeout, denied, settings=None):
    cfg = settings or developer.Settings()
    if cfg.backend == "codex":
        from .runtime import codex as adapter
    else:
        from .runtime import claude as adapter
    # Codex also records direct preflight attempts through this same ledger.
    with (nullcontext() if cfg.backend == "codex" else diagnostics.attempt(name, cfg.backend, model)) as diagnostic:
        answer, session, usage, cost = adapter.execute(name, prompt, model, tools, cwd, timeout, denied, SCHEMAS[name], cfg)
        if diagnostic is not None:
            diagnostic["session"] = diagnostics.identifier(session)
            for target, source in (("input", "input_tokens"), ("output", "output_tokens"),
                                   ("cache_read", "cache_read_input_tokens"), ("cache_creation", "cache_creation_input_tokens")):
                diagnostic[target] = diagnostics.count((usage or {}).get(source))
    _record(name, session, usage, cost)
    return json.dumps(answer) if isinstance(answer, dict) else str(answer), session, ""


def run_node(name, prompt, model, allowed_tools, cwd, timeout, denied=None, runtime=None):
    """Ask node `name` and return its answer as a dict; a node with no parseable JSON twice is a driver error."""
    caps = allowed_tools if isinstance(allowed_tools, Capabilities) else None
    stub = os.environ.get("OURO_NODE_STUB")
    if caps and stub:
        from .runtime.claude import tools_for
        allowed_tools, denied = tools_for(caps)
    session = diag = ""
    for _ in range(2):
        text, session, diag = (_stub(name, prompt, cwd, stub, allowed_tools, denied, model) if stub
                               else _real(name, prompt, model, caps or allowed_tools, cwd, timeout, denied, runtime))
        ans = parse_first_json(text)
        if ans is not None and ans.get("stop") is not None:
            stop = ans["stop"]
            question = stop.get("question") if isinstance(stop, dict) else stop
            if not isinstance(question, str) or not question.strip():
                raise sh.DriverError(f"node {name}: infrastructure or malformed stop with no question")
        if ans is not None and runtime and runtime.backend == "codex":
            from .runtime.schema import validate_answer
            validate_answer(name, ans)
        if ans is not None:
            return ans
        diag = diag or f"the answer held no JSON object: {_tail(text, 4)!r}"
    raise sh.DriverError(f"node {name} returned no parseable JSON twice (session {session or 'unknown'}): {diag}")


def usage_table():
    if not USAGE:
        return ""
    rows = ["| node | session | input | output | cache read | cache creation | cost USD |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {u['node']} | {u['session']} | {u['input']} | {u['output']} | {u['cache_read']} | "
             f"{u['cache_creation']} | {u['cost'] if u['cost'] is not None else 'unknown'} |" for u in USAGE]
    return "\n".join(rows)
