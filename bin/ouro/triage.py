"""`triage N`: the TRIAGE node grades the issue; the driver posts the verdict and moves the labels."""
import json
import re

from . import briefs, node, sh
from .run import parse_parts, restamped
from .sh import DriverError, argv_for, say

STATE_LABELS = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage", "idea")
VERDICTS = ("PROMOTE", "NEEDS-RULING", "CHECKPOINT", "SPLIT", "FUSE", "STALE")


def label_moves(verdict, size, labels, named=()):
    """(labels to add, labels to remove) for a verdict, the labels the node names for the issue first; an L never reaches agent-ready."""
    if verdict in ("PROMOTE", "CHECKPOINT") and size == "L":
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
    add = list(named) + add
    remove = [l for l in STATE_LABELS if l in present and l not in add]
    return [a for a in add if a not in present], remove


SIZES = ("S", "M", "L")
FLOWS = ("direct", "express", "standard", "full")


def with_lines(body, size, flow, doc_impact):
    """The body with each of Size:, Flow:, Doc impact on close: written where it is missing, and a Size: or Flow: line whose value is outside its set replaced by the answer's, so a sentence a node wrote as a line does not reach the gate."""
    add = []
    for key, val, allowed in (("Size", size, SIZES), ("Flow", flow, FLOWS), ("Doc impact on close", doc_impact, None)):
        m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*?)\r?$", body)
        if m is None:
            if val:
                add.append(f"{key}: {val}")
        elif allowed and val in allowed and (m.group(1).split() or [""])[0] not in allowed:  # the gate reads the first token
            body = body[:m.start()] + f"{key}: {val}" + body[m.end(1):]
    return body if not add else body.rstrip("\n") + "\n\n" + "\n".join(add) + "\n"


def size_problem(body):
    """Why the shape gate would refuse the body's Size lines, read as it reads them: the first token of each, lines that repeat one value counted as one, exactly one value, S or M; None when they pass."""
    values = list(dict.fromkeys(re.findall(r"(?m)^Size:[^\S\r\n]*(\S+)", body)))
    if len(values) != 1:
        return f"{len(values)} `Size:` values ({', '.join(values) or 'none'}); a body states exactly one"
    if values[0] not in ("S", "M"):
        return f"`Size: {values[0]}`; only S or M ride on agent-ready"
    return None


def with_manual(body, wanted):
    """(body, paths): a `Manual: <path>` line for each .claude/ path the node named among the body's backticked ones; the build node cannot edit there."""
    paths = []
    for p in re.findall(r"`(\.claude/[^`\s]+)`", body):
        if p in wanted and not p.startswith(".claude/worktrees/") and p not in paths:
            paths.append(p)
    add = [f"Manual: {p}" for p in paths if not re.search(rf"(?m)^Manual: {re.escape(p)}\s*$", body)]
    return (body if not add else body.rstrip("\n") + "\n\n" + "\n".join(add) + "\n"), paths


def reasoning(text):
    """The node's comment text less a leading `**Triage**` line and a leading `Verdict: <word>` line or sentence prefix: the driver writes both, its verdict the one that counts."""
    text = re.sub(r"\A\s*\*\*Triage\*\*[ \t]*(?:\r?\n|\Z)\s*", "", text)
    return re.sub(r"\A\s*Verdict:[ \t]*[\w-]+(?:[.,]|[ \t]*(?:\r?\n|\Z))\s*", "", text)


def label_problem(labels, sets):
    """Why the shape gate would refuse the labels, read as it reads them: at least one area entry and exactly one type entry, compared without regard to case, a kind with no declared set skipped; None when they pass."""
    for kind, rule in (("area", "at least one"), ("type", "exactly one")):
        declared = [str(x) for x in sets.get(kind) or [] if str(x)]
        low = {x.lower() for x in declared}
        on = [str(l) for l in labels if str(l).lower() in low]
        if declared and (not on or (rule == "exactly one" and len(on) != 1)):
            return f"{len(on)} {kind} label(s) ({', '.join(on) or 'none'}); the binding requires {rule} of: {', '.join(declared)}"
    return None


def file_list(files, cap=3000):
    """The brief's file list: the first `cap` tracked paths, then a line naming how many are not listed."""
    out = "\n".join(files[:cap])
    return out if len(files) <= cap else out + f"\n({len(files) - cap} more tracked files are not listed; the anchor gate above read them all)"


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
    files = [f for f in env.git("ls-files", "-z").out.split("\0") if f]
    behind = behind_note(env)  # fetches origin: the ancestry checks below and the anchor gate read the fetched ref
    anchors = env.dead_anchors(body, f"origin/{env.default}", need_one=True)
    prompt = briefs.render("triage", number=n, title=i["title"], body=body, comments=comments,
                           prs=referencing_prs(env, n), behind=behind,
                           labels=json.dumps({**sets, "state": list(STATE_LABELS)}), issue_labels=", ".join(labels) or "(none)", trusted=", ".join(sorted(env.trusted())),
                           authority="\n".join(f"- {p}" for p in env.lookup("authority.local", [])) or "(none declared)",
                           anchors="\n".join(f"- {a}" for a in anchors) or "No finding: every anchor line resolves.",
                           files=file_list(files), contract=briefs.contract_sections(env, (3, 4, 8)))
    say(f"TRIAGE node for #{n}")
    ans = node.run_node("TRIAGE", prompt, env.lookup("models.builder", "sonnet"), list(node.READ_TOOLS),
                        str(env.main), node.TIMEOUTS["TRIAGE"])
    verdict = str(ans.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        raise DriverError(f"node TRIAGE answered verdict {ans.get('verdict')!r}")
    if verdict == "PROMOTE" and str(ans.get("deliverable") or "").strip().lower() == "finding":
        raise DriverError("node TRIAGE answered PROMOTE for a deliverable that is a finding: that is CHECKPOINT")
    size = str(ans.get("size") or "").strip().strip(".,;:").upper()
    flow = str(ans.get("flow") or "").strip().strip(".,;:").lower()
    new_body = with_lines(body, size, flow, str(ans.get("doc_impact") or "").strip()) if verdict in ("PROMOTE", "CHECKPOINT") else body
    declared = {str(x).lower(): str(x) for k in ("area", "type") for x in sets.get(k) or []}
    types, have = {str(x).lower() for x in sets.get("type") or []}, {str(l).lower() for l in labels}
    named = []  # a label the node names that the issue lacks, in the binding's spelling; one type at most, none where the issue has one
    for l in dict.fromkeys(str(x).lower() for x in ans.get("labels") or []):
        if l in declared and l not in have and not (l in types and (have & types or any(n.lower() in types for n in named))):
            named.append(declared[l])
    problem = label_problem(labels + named, sets) if verdict in ("PROMOTE", "CHECKPOINT") and size != "L" else None
    dead = [a for a in anchors if a != sh.NO_ANCHOR] if verdict in ("PROMOTE", "CHECKPOINT") and size != "L" else []
    if dead and (moved := restamped(env, body, dead)) != body and not env.dead_anchors(moved, f"origin/{env.default}"):
        dead = []  # the run re-points these lines at the file their fragments moved to, as it reads them
    if problem:  # the shape gate would demote the promotion for its labels: ask first, and post once
        verdict = "NEEDS-RULING"
        ans["question"] = (f"The issue has {problem}. Which label? The shape gate refuses the promotion without it; once "
                           f"labeled, run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    elif verdict in ("PROMOTE", "CHECKPOINT") and size != "L" and size_problem(new_body):  # the gate reads the body's line, not the answer
        verdict = "NEEDS-RULING"
        ans["question"] = (f"The body holds {size_problem(new_body)}. The shape gate refuses the promotion as the body stands; fix the "
                           f"line, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    elif dead:  # the run would stop on a dead anchor: ask first, and post once
        verdict = "NEEDS-RULING"
        ans["question"] = ("The anchor gate finds:\n\n" + "\n".join(f"- {a}" for a in dead) + "\n\nThe run stops on a dead anchor; fix the "
                           "anchors, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    comment = f"**Triage**\n\nVerdict: {verdict}\n\n{reasoning(str(ans.get('comment') or ''))}\n"
    if ans.get("question"):
        comment += f"\nQuestion for the owner:\n\n{ans['question']}\n"
    add, remove = label_moves(verdict, size, labels, named)
    if verdict not in ("PROMOTE", "CHECKPOINT"):
        new_body = body
    if verdict == "PROMOTE":
        comment += f"\nParts: {len(parse_parts(new_body)) or 'none'}\n"  # what the driver will read: a mismatch with the verdict shows here
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
