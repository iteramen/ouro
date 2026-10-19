"""`triage N`: the TRIAGE node grades the issue; the driver posts the verdict and moves the labels."""
import json
import re

from . import briefs, node, sh
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


def with_manual(body, wanted):
    """(body, paths): a `Manual: <path>` line for each .claude/ path the node named among the body's backticked ones; the build node cannot edit there."""
    paths = []
    for p in re.findall(r"`(\.claude/[^`\s]+)`", body):
        if p in wanted and not p.startswith(".claude/worktrees/") and p not in paths:
            paths.append(p)
    add = [f"Manual: {p}" for p in paths if not re.search(rf"(?m)^Manual: {re.escape(p)}\s*$", body)]
    return (body if not add else body.rstrip("\n") + "\n\n" + "\n".join(add) + "\n"), paths


def referencing_prs(env, n):
    """The brief's lines for the pull requests that name the issue, each with its state and merge commit."""
    prs = env.gh_json("pr", "list", "--state", "all", "--search", f"{n} in:title,body", "--limit", "50",
                      "--json", "number,title,body,state,mergeCommit")
    ref = re.compile(rf"(?<![\w/])#{n}\b")
    lines = []
    for p in prs:
        if not ref.search(f"{p.get('title') or ''}\n{p.get('body') or ''}"):
            continue
        sha = (p.get("mergeCommit") or {}).get("oid") or ""
        landed = ""
        if sha:  # the node has no shell, so the driver runs the ancestry check
            r = env.git("merge-base", "--is-ancestor", sha, f"origin/{env.default}", check=False)
            landed = f"; on origin/{env.default}: " + ("yes" if r.code == 0 else "no" if r.code == 1 else "unknown")
        lines.append(f"- #{p['number']} {p['state']}, merge commit {sha or 'none'}{landed}: {p['title']}")
    return "\n".join(lines) or "(none)"


def behind_note(env):
    """Fetch origin; a note when the main checkout's default branch is behind it."""
    f = env.git("fetch", "origin", check=False)
    r = env.git("rev-list", "--count", f"{env.default}..origin/{env.default}", check=False)
    if f.code != 0 or r.code != 0:
        note = f"Whether the main checkout's {env.default} is behind origin/{env.default} is unknown: the fetch or the comparison failed."
    elif r.out.strip() not in ("", "0"):
        note = f"The main checkout's {env.default} is {r.out.strip()} commit(s) behind origin/{env.default}: its files are stale."
    else:
        return "The main checkout's default branch is not behind origin."
    say("WARNING: " + note)
    return note


def triage(env, n, dry):
    i = env.gh_json("issue", "view", str(n), "--json", "number,title,body,labels,comments,state")
    labels = [l["name"] for l in i.get("labels", [])]
    body = i.get("body") or ""
    comments = "\n\n".join(f"[{(c.get('author') or {}).get('login')}] {c.get('body')}" for c in i.get("comments", [])) or "(none)"
    sets = {k: env.lookup(f"labels.{k}", []) for k in ("scope", "area", "type")}
    files = env.git("ls-files", "-z").out.split("\0")
    behind = behind_note(env)  # fetches origin: the ancestry checks below read the fetched ref
    prompt = briefs.render("triage", number=n, title=i["title"], body=body, comments=comments,
                           prs=referencing_prs(env, n), behind=behind,
                           labels=json.dumps({**sets, "state": list(STATE_LABELS)}), trusted=", ".join(sorted(env.trusted())),
                           authority="\n".join(f"- {p}" for p in env.lookup("authority.local", [])) or "(none declared)",
                           files="\n".join(files[:3000]), contract=briefs.contract_sections(env, (3, 4, 8)))
    say(f"TRIAGE node for #{n}")
    ans = node.run_node("TRIAGE", prompt, env.lookup("models.builder", "sonnet"), list(node.READ_TOOLS),
                        str(env.main), node.TIMEOUTS["TRIAGE"])
    verdict = str(ans.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        raise DriverError(f"node TRIAGE answered verdict {ans.get('verdict')!r}")
    if verdict == "PROMOTE" and str(ans.get("deliverable") or "").strip().lower() == "finding":
        raise DriverError("node TRIAGE answered PROMOTE for a deliverable that is a finding: that is CHECKPOINT")
    size = str(ans.get("size") or "").strip().upper()
    comment = f"**Triage**\n\nVerdict: {verdict}\n\n{ans.get('comment') or ''}\n"
    if ans.get("question"):
        comment += f"\nQuestion for the owner:\n\n{ans['question']}\n"
    add, remove = label_moves(verdict, size, labels)
    new_body = (with_lines(body, size, str(ans.get("flow") or "").strip(), str(ans.get("doc_impact") or "").strip())
                if verdict in ("PROMOTE", "CHECKPOINT") else body)
    if verdict == "PROMOTE":
        new_body, manual = with_manual(new_body, [str(p) for p in ans.get("manual") or []])
        if manual:
            comment += "\nManual steps (the build node cannot edit under `.claude/`):\n\n" + "\n".join(f"- `{p}`" for p in manual) + "\n"
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
    if "agent-ready" in add and not dry:
        # the repository's own shape gate on the issue as promoted: a body it refuses goes back to needs-triage
        r = sh.run(sh.script("Test-AgentReadyShape.ps1", "-Issue", str(n), "-Demote"), cwd=env.main)
        if r.code != 0:
            if "SHAPE:" not in r.out:
                raise DriverError(f"the shape gate failed: {sh.cmdline(r.argv)}: {r.text}")
            say(r.out.strip())
            say(f"#{n} fails the shape gate: demoted to needs-triage, comment posted")
            return 3
    return 0
