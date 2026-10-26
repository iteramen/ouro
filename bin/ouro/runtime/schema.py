"""Complete Codex structured-output schemas and validation of final node results."""
from ..sh import DriverError


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def arr(items):
    return {"type": "array", "items": items}


S, I = {"type": "string"}, {"type": "integer"}
STOP = {"anyOf": [obj(reason=S, question=S), {"type": "null"}]}
SCHEMAS = {
    "TRIAGE": obj(verdict={"type": "string", "enum": ["PROMOTE", "NEEDS-RULING", "CHECKPOINT", "SPLIT", "STALE"]},
                  size=S, flow=S, deliverable=S, doc_impact=S, manual=arr(S), labels=arr(S), comment=S, question=S),
    "BUILD": obj(subject=S, body=S, claims=arr(obj(path=S, claim=S, verified_by=S)), gates_run=arr(obj(cmd=S, exit=I)),
                 validation_pending=arr(obj(cmd=S, reason=S)), residue=arr(S), manual=arr(obj(path=S, edit=S)),
                 door=S, blast_radius=S, stop=STOP, replies=arr(obj(comment_id=I, reply=S))),
    "REVIEW": obj(verdict={"type": "string", "enum": ["CLEAN", "FINDINGS"]},
                  findings=arr(obj(severity={"type": "string", "enum": ["H", "M", "L"]}, file=S, line=I, claim=S, evidence=S, scope=S)),
                  false_record_sentences=I, files_read=arr(S), stop=STOP),
    "CHECKPOINT": obj(finding=S, stop=STOP),
    "FOLLOWUP": obj(issues=arr(obj(title=S, body=S, labels=arr(S)))),
}


def validate(value, schema, path="answer"):
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                validate(value, branch, path)
                return
            except DriverError:
                pass
        raise DriverError(f"{path}: invalid union value")
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    if type(value) is not types[kind]:
        raise DriverError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise DriverError(f"{path}: invalid value")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise DriverError(f"{path}: missing or unknown fields")
        for key, child in schema["properties"].items():
            validate(value[key], child, path + "." + key)
    if kind == "array":
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")


def validate_answer(name, answer):
    # Transport validates the strict wire shape before null optional fields are removed.
    if answer.get("stop") is not None and not answer["stop"]["question"].strip():
        raise DriverError(f"codex node {name}: stop has no meaningful question")
    if name == "TRIAGE" and answer["verdict"] == "NEEDS-RULING" and not answer["question"].strip():
        raise DriverError("codex TRIAGE: NEEDS-RULING has no question")
    if name == "CHECKPOINT" and not answer.get("stop") and not answer["finding"].strip():
        raise DriverError("codex CHECKPOINT: missing finding")
