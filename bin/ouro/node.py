"""The node adapter: one function that asks a model a question and returns the JSON it answered.

Real mode spawns `claude -p` with the prompt on stdin (a command line is capped near 32k
characters on Windows, and the briefs are longer). Stub mode, selected by OURO_NODE_STUB naming
a directory, spawns nothing: the k-th call of a node in this process returns <dir>/<name>.<k>.json,
else <dir>/<name>.json, and leaves <dir>/<name>.<k>.call.json holding the cwd and the tools it got.
"""
import json
import os
import re
from pathlib import Path

from . import sh

USAGE = []  # one row per node call: {node, session, input, output, cache_read, cache_creation, cost}
_CALLS = {}

TIMEOUTS = {"BUILD": 3600, "REVIEW": 3600, "TRIAGE": 600, "FOLLOWUP": 600, "CHECKPOINT": 3600}
MODEL_ROLE = {"BUILD": "builder", "REVIEW": "reviewer", "TRIAGE": "builder", "FOLLOWUP": "builder", "CHECKPOINT": "builder"}
READ_TOOLS = ["Read", "Grep", "Glob"]

SCHEMAS = {
    "TRIAGE": {"type": "object", "required": ["verdict", "comment"], "properties": {
        "verdict": {"type": "string", "enum": ["PROMOTE", "NEEDS-RULING", "CHECKPOINT", "SPLIT", "FUSE", "STALE"]},
        "size": {"type": "string"}, "flow": {"type": "string"}, "doc_impact": {"type": "string"}, "manual": {"type": "array"},
        "comment": {"type": "string"}, "question": {"type": "string"}}},
    "BUILD": {"type": "object", "properties": {
        "subject": {"type": "string"}, "body": {"type": "string"}, "claims": {"type": "array"},
        "gates_run": {"type": "array"}, "residue": {"type": "array"}, "stop": {"type": "object"}, "manual": {"type": "array"},
        "replies": {"type": "array"}, "door": {"type": "string"}, "blast_radius": {"type": "string"}}},
    "REVIEW": {"type": "object", "required": ["verdict", "findings"], "properties": {
        "verdict": {"type": "string", "enum": ["CLEAN", "FINDINGS"]}, "findings": {"type": "array"},
        "false_record_sentences": {"type": ["integer", "string"]}, "files_read": {"type": "array"}}},
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
                  "cache_creation": usage.get("cache_creation_input_tokens", 0), "cost": cost or 0})


def _stub(name, prompt, cwd, directory, tools, denied):
    k = _CALLS[name] = _CALLS.get(name, 0) + 1
    d = Path(directory)
    f = d / f"{name}.{k}.json"
    if not f.is_file():
        f = d / f"{name}.json"
    if not f.is_file():
        raise sh.DriverError(f"node stub: no {name}.{k}.json or {name}.json in {d}")
    (d / f"{name}.{k}.prompt.txt").write_bytes(prompt.encode("utf-8"))
    (d / f"{name}.{k}.call.json").write_text(json.dumps({"cwd": str(cwd), "cwd_exists": Path(cwd).is_dir(), "tools": tools,
                                                          "denied": denied}), encoding="utf-8")
    ans = json.loads(f.read_text(encoding="utf-8"))
    _record(name, "stub", ans.pop("_usage", None), 0)
    if "_raw" in ans:
        return ans["_raw"], "stub", ans.get("_stderr", "")
    for rel, content in (ans.pop("writes", None) or {}).items():
        target = Path(cwd) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    return json.dumps(ans), "stub", ""


def _tail(text, lines=8):
    return "\n".join(str(text).strip().splitlines()[-lines:])[-800:]


def _real(name, prompt, model, tools, cwd, timeout, denied):
    argv = sh.argv_for("claude", "-p", "--output-format", "json", "--model", model, "--allowedTools", ",".join(tools),
                       *(["--disallowedTools", ",".join(denied)] if denied else []),
                       "--permission-mode", "dontAsk", "--json-schema", json.dumps(SCHEMAS[name]))
    r = sh.run(argv, cwd=cwd, input_text=prompt, timeout=timeout)
    if r.code is None:
        raise sh.DriverError(f"node {name} timed out after {timeout}s")
    try:
        env = json.loads(r.out)
    except ValueError:
        return "", "", f"claude exited {r.code} with no JSON envelope; stderr tail: {_tail(r.err)!r}; stdout tail: {_tail(r.out)!r}"
    _record(name, env.get("session_id"), env.get("usage"), env.get("total_cost_usd"))
    if env.get("is_error"):
        return "", env.get("session_id", ""), f"claude exited {r.code} with is_error; result: {_tail(env.get('result', ''))!r}; stderr tail: {_tail(r.err)!r}"
    if isinstance(env.get("structured_output"), dict):
        denied = env.get("permission_denials") if name == "BUILD" else None  # the calls the harness refused BUILD, each with its tool_input: a path under .claude/ is a manual step
        return json.dumps({**env["structured_output"], **({"_denied": denied} if denied else {})}), env.get("session_id", ""), ""
    return str(env.get("result", "")), env.get("session_id", ""), ""


def run_node(name, prompt, model, allowed_tools, cwd, timeout, denied=None):
    """Ask node `name` and return its answer as a dict; a node with no parseable JSON twice is a driver error."""
    stub = os.environ.get("OURO_NODE_STUB")
    session = diag = ""
    for _ in range(2):
        text, session, diag = (_stub(name, prompt, cwd, stub, allowed_tools, denied) if stub
                               else _real(name, prompt, model, allowed_tools, cwd, timeout, denied))
        ans = parse_first_json(text)
        if ans is not None:
            return ans
        diag = diag or f"the answer held no JSON object: {_tail(text, 4)!r}"
    raise sh.DriverError(f"node {name} returned no parseable JSON twice (session {session or 'unknown'}): {diag}")


def usage_table():
    if not USAGE:
        return ""
    rows = ["| node | session | input | output | cache read | cache creation | cost USD |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {u['node']} | {u['session']} | {u['input']} | {u['output']} | {u['cache_read']} | "
             f"{u['cache_creation']} | {u['cost']} |" for u in USAGE]
    return "\n".join(rows)
