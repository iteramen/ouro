"""`triage N`: the TRIAGE node grades the issue; the driver posts the verdict and moves the labels."""
import json
import re

from . import briefs, node, sh
from .acceptance import acceptance_commands, empty_list
from .run import Run, body_line, parse_parts, restamped
from .sh import DriverError, argv_for, say

STATE_LABELS = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage", "idea")  # umbrella is cleared by its children closing, never by a re-triage
VERDICTS = ("PROMOTE", "NEEDS-RULING", "CHECKPOINT", "SPLIT", "STALE")


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
    elif verdict == "SPLIT":
        add = ["umbrella"]
    else:
        remove = [l for l in ("agent-ready", "checkpoint") if l in present] if verdict == "STALE" and "checkpoint" in present else []
        return [], remove
    add = list(named) + add
    remove = [l for l in STATE_LABELS if l in present and l not in add]
    if verdict == "PROMOTE" and "checkpoint" in present:
        remove.append("checkpoint")
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
    for p in re.findall(r"`((?:\.claude|\.codex|\.agents|\.aws)/[^`\s]+)`", body):
        if p in wanted and not p.startswith(".claude/worktrees/") and p not in paths:
            paths.append(p)
    add = [f"Manual: {p}" for p in paths if not re.search(rf"(?m)^Manual: {re.escape(p)}\s*$", body)]
    return (body if not add else body.rstrip("\n") + "\n\n" + "\n".join(add) + "\n"), paths


# a node's own verdict opening, in the forms a node writes: the key and the word bold or not, any case, the word ended by a
# line end, `.`, `,`, `:` or a dash, after an optional parenthetical
VERDICT_OPENING = re.compile(r"\A\s*(?:\*\*)?verdict(?:\*\*)?:(?:\*\*)?[ \t]*(?:\*\*)?[\w-]+(?:\*\*)?(?:[ \t]*\([^)\r\n]*\))?"
                             r"(?:[ \t]*[.,:]|[ \t]*(?:[\u2014\u2013]|-(?=\s))|[ \t]*(?:\r?\n|\Z))\s*", re.I)
SPLIT_LINE = "The owner files the children this comment lists, by hand."
# the closing lines the driver writes itself, by verdict: a node's own copy of one, as its last line, is dropped
DRIVER_TAILS = {"SPLIT": re.compile(r"(?im)\s*^[ \t]*the owner files (?:the|its) children\b[^\n]*\bby hand\b[^\n]*\s*\Z"),
                "PROMOTE": re.compile(r"(?m)\s*^[ \t]*Parts:[ \t]*\S[^\n]*\s*\Z")}


def reasoning(text, verdict):
    """The node's comment text less what the driver writes itself: a leading `**Triage**` line, a leading verdict opening, and
    for the verdict that gets one, the closing line the driver appends; the driver's verdict is the one that counts."""
    text = re.sub(r"\A\s*\*\*Triage\*\*[ \t]*(?:\r?\n|\Z)\s*", "", text)
    text = VERDICT_OPENING.sub("", text)
    return DRIVER_TAILS[verdict].sub("", text) if verdict in DRIVER_TAILS else text


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


def granted_gates(env, labels, sets):
    """The gate runs the BUILD node may run for the issue's area labels, in the binding's spelling: what the run's build_commands grants."""
    declared = {str(x).lower(): str(x) for x in sets.get("area") or []}
    areas = {declared[str(l).lower()] for l in labels if str(l).lower() in declared}
    return [str(g.get("run") or "") for g in env.data.get("gate", []) if ("*" in g.get("areas", []) or areas & set(g.get("areas", [])))
            and (g.get("cheap") is True or not env.pr_checks() or not Run.notice(g) and (Run.locally_run(str(g.get("run") or ""), g) or g.get("paths")))]


def binding_notes(env, body, labels):
    """What the binding makes of the issue's asks: a `Flow: direct` with no `ship.direct` globs, a `trivial` label under a policy that does not merge on it;
    the flow value and the label are read as the run reads them, exactly."""
    notes = []
    if body_line(body, "Flow") == "direct" and not env.lookup("ship.direct", []):
        notes.append("The body asks `Flow: direct`, and the binding declares no `ship.direct` globs: the run takes the class the diff warrants, not the direct lane.")
    policy = env.lookup("ship.policy", "")
    if "trivial" in labels and policy != "trivial-merge":
        notes.append(f"The issue carries `trivial`, which does not let the driver merge under `ship.policy = \"{policy}\"` (only `trivial-merge` merges on it): "
                     "here it picks the trivial builder and nothing more.")
    return notes


def shape_findings(env, issue, body, labels, comment):
    """Run the real shape gate on the proposed issue before publishing its triage or labels."""
    candidate = {**issue, "body": body, "labels": [{"name": label} for label in labels],
                 "comments": [*(issue.get("comments") or []),
                              {"author": {"login": env.viewer()}, "body": comment}]}
    proposed = env.body_file("triage-shape.json", json.dumps([candidate]))
    q = lambda value: str(value).replace("'", "''")
    wrapper = env.body_file("triage-shape.ps1", f"& '{q((sh.BIN / 'Test-AgentReadyShape.ps1').as_posix())}' "
                           f"-Issue {int(issue['number'])} -IssuesJson ([IO.File]::ReadAllText('{q(proposed)}'))\n")
    result = env.run([sh.which("pwsh"), "-NoProfile", "-File", wrapper], cwd=env.main)
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.out)
    findings = [line.split("SHAPE:", 1)[1].strip() for line in output.splitlines() if "SHAPE:" in line]
    if result.code != 0 and not findings:
        raise DriverError(f"the shape preflight failed before any triage write: {result.text}")
    return findings


def triage(env, n, dry):
    node.preflight(env.runtime, capabilities=node.READ)  # the TRIAGE node runs even in a dry run
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
    ans = node.run_node("TRIAGE", prompt, node.model(env, "builder", node.DEFAULT_BUILDER), node.READ,
                        str(env.main), node.TIMEOUTS["TRIAGE"], runtime=env.runtime)
    verdict = str(ans.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        raise DriverError(f"node TRIAGE answered verdict {ans.get('verdict')!r}")
    if verdict == "PROMOTE" and str(ans.get("deliverable") or "").strip().lower() == "finding":
        raise DriverError("node TRIAGE answered PROMOTE for a deliverable that is a finding: that is CHECKPOINT")
    size = str(ans.get("size") or "").strip().strip(".,;:").upper()
    flow = str(ans.get("flow") or "").strip().strip(".,;:").lower()
    if verdict in ("PROMOTE", "CHECKPOINT") and size == "L":  # the node found no split lines: the contract's question, not a silent split
        verdict = "NEEDS-RULING"
        ans["question"] = "An L is never agent-ready: owner-driven session, or split how?" + (f"\n\n{ans['question']}" if ans.get("question") else "")
    elif verdict in ("PROMOTE", "CHECKPOINT") and "umbrella" in labels:  # an umbrella is never executed: its children are
        verdict = "NEEDS-RULING"
        ans["question"] = ("The issue holds `umbrella`, which no verdict removes, and an umbrella is never run. Close it when its children "
                           "are done, or remove the label and run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    elif verdict == "SPLIT" and ans.get("question"):  # a split that still asks is a ruling to take, where the owner sees it
        verdict = "NEEDS-RULING"
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
    preflight_failed = False
    if problem:  # the shape gate would demote the promotion for its labels: ask first, and post once
        preflight_failed = True
        verdict = "NEEDS-RULING"
        ans["question"] = (f"The issue has {problem}. Which label? The shape gate refuses the promotion without it; once "
                           f"labeled, run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    elif verdict in ("PROMOTE", "CHECKPOINT") and size != "L" and size_problem(new_body):  # the gate reads the body's line, not the answer
        preflight_failed = True
        verdict = "NEEDS-RULING"
        ans["question"] = (f"The body holds {size_problem(new_body)}. The shape gate refuses the promotion as the body stands; fix the "
                           f"line, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    elif dead:  # the run would stop on a dead anchor: ask first, and post once
        preflight_failed = True
        verdict = "NEEDS-RULING"
        ans["question"] = ("The anchor gate finds:\n\n" + "\n".join(f"- {a}" for a in dead) + "\n\nThe run stops on a dead anchor; fix the "
                           "anchors, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    comment = f"**Triage**\n\nVerdict: {verdict}\n\n{reasoning(str(ans.get('comment') or ''), verdict)}\n"
    if verdict != "STALE" and (notes := binding_notes(env, body, labels)):  # a stale issue runs nowhere
        comment += "\n" + "\n".join(f"- {x}" for x in notes) + "\n"
    if verdict not in ("STALE", "CHECKPOINT") and not (acceptance_commands(body) + granted_gates(env, labels + named, sets)):  # the labels the verdict adds count
        comment += "\n- " + empty_list(body) + " A worktree BUILD dispatch stops with `gate uncovered`; the direct lane runs every selected gate in the driver.\n"
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
    if verdict == "SPLIT":  # nothing files the children: say who does
        comment += f"\n{SPLIT_LINE}\n"
    if verdict in ("PROMOTE", "CHECKPOINT"):
        findings = shape_findings(env, i, new_body, [label for label in labels if label not in remove] + add, comment)
        if findings:
            preflight_failed = True
            verdict, new_body = "NEEDS-RULING", body
            question = ("Test-AgentReadyShape.ps1 refuses the proposed promotion:\n\n"
                        + "\n".join(f"- {finding}" for finding in findings)
                        + "\n\nThe promotion was withheld; fix these findings and run triage again."
                        + (f"\n\n{ans['question']}" if ans.get("question") else ""))
            comment = (f"**Triage**\n\nVerdict: {verdict}\n\n{reasoning(str(ans.get('comment') or ''), verdict)}\n"
                       f"\nQuestion for the owner:\n\n{question}\n")
            add, remove = label_moves(verdict, size, labels, named)
    say(f"verdict {verdict}; labels +{add} -{remove}" + ("; body lines written" if new_body != body else ""))
    say("comment:\n" + comment)
    if (env.gh_json("issue", "view", str(n), "--json", "body").get("body") or "") != body:  # an edit landed while the node graded
        say(f"#{n}'s body changed while triage ran: nothing written, stop posted")
        env.comment_issue(n, "**Stop:** open decision\n\nThe issue's body changed while triage ran, so this verdict graded a body that is no "
                             "longer the issue's: no body line, label or verdict is written. Run triage again on the body as it stands.\n")
        return 3
    if new_body != body:
        env.write("write the Size/Flow/Doc impact lines",
                  argv_for("gh", "issue", "edit", str(n), "-R", env.slug, "--body-file", env.body_file("issue-body.md", new_body)))
    if add or remove:
        argv = argv_for("gh", "issue", "edit", str(n), "-R", env.slug)
        for l in remove:
            argv += ["--remove-label", l]
        for l in add:
            argv += ["--add-label", l]
        env.write("set the labels", argv)
    env.comment_issue(n, comment)
    return 3 if preflight_failed else 0
