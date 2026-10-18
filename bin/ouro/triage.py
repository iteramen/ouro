"""`triage N`: the TRIAGE node grades the issue; the driver posts the verdict and moves the labels."""
import json
import re

from . import briefs, node
from .sh import DriverError, argv_for, say

STATE_LABELS = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage", "idea")
VERDICTS = ("PROMOTE", "NEEDS-RULING", "CHECKPOINT", "SPLIT", "FUSE", "STALE")


def label_moves(verdict, size, labels):
    """(labels to add, labels to remove) for a verdict; an L never reaches agent-ready."""
    if verdict == "PROMOTE" and size == "L":
        verdict = "SPLIT"
    present = set(labels)
    if verdict == "PROMOTE":
        add = ["agent-ready"]
    elif verdict == "CHECKPOINT":
        add = ["agent-ready", "checkpoint"]
    elif verdict == "NEEDS-RULING":
        add = ["needs-ruling"]
    else:
        return [], []
    remove = [l for l in STATE_LABELS if l in present and l not in add]
    return [a for a in add if a not in present], remove


def with_lines(body, size, flow, doc_impact):
    """The body with each of Size:, Flow:, Doc impact on close: written where it is missing."""
    add = []
    for key, val in (("Size", size), ("Flow", flow), ("Doc impact on close", doc_impact)):
        if val and not re.search(rf"(?m)^{re.escape(key)}:", body):
            add.append(f"{key}: {val}")
    return body if not add else body.rstrip("\n") + "\n\n" + "\n".join(add) + "\n"


def triage(env, n, dry):
    i = env.gh_json("issue", "view", str(n), "--json", "number,title,body,labels,comments,state")
    labels = [l["name"] for l in i.get("labels", [])]
    body = i.get("body") or ""
    comments = "\n\n".join(f"[{(c.get('author') or {}).get('login')}] {c.get('body')}" for c in i.get("comments", [])) or "(none)"
    sets = {k: env.lookup(f"labels.{k}", []) for k in ("scope", "area", "type")}
    files = env.git("ls-files", "-z").out.split("\0")
    prompt = briefs.render("triage", number=n, title=i["title"], body=body, comments=comments,
                           labels=json.dumps({**sets, "state": list(STATE_LABELS)}), trusted=", ".join(sorted(env.trusted())),
                           files="\n".join(files[:3000]), contract=briefs.contract_sections(env, (3, 4, 8)))
    say(f"TRIAGE node for #{n}")
    ans = node.run_node("TRIAGE", prompt, env.lookup("models.builder", "sonnet"), list(node.READ_TOOLS),
                        str(env.main), node.TIMEOUTS["TRIAGE"])
    verdict = str(ans.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        raise DriverError(f"node TRIAGE answered verdict {ans.get('verdict')!r}")
    size = str(ans.get("size") or "").strip().upper()
    comment = f"**Triage**\n\nVerdict: {verdict}\n\n{ans.get('comment') or ''}\n"
    if ans.get("question"):
        comment += f"\nQuestion for the owner:\n\n{ans['question']}\n"
    add, remove = label_moves(verdict, size, labels)
    new_body = (with_lines(body, size, str(ans.get("flow") or "").strip(), str(ans.get("doc_impact") or "").strip())
                if verdict in ("PROMOTE", "CHECKPOINT") else body)
    say(f"verdict {verdict}; labels +{add} -{remove}" + ("; body lines written" if new_body != body else ""))
    say("comment:\n" + comment)
    if new_body != body:
        env.write("write the Size/Flow/Doc impact lines",
                  argv_for("gh", "issue", "edit", str(n), "-R", env.slug, "--body-file", env.body_file("issue-body.md", new_body)))
    env.comment_issue(n, comment)
    if add or remove:
        argv = argv_for("gh", "issue", "edit", str(n), "-R", env.slug)
        for l in remove:
            argv += ["--remove-label", l]
        for l in add:
            argv += ["--add-label", l]
        env.write("set the labels", argv)
    return 0
