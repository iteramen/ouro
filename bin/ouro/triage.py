"""`triage N`: the TRIAGE node grades the issue; the driver posts the verdict and moves the labels."""
import json
import re

from . import briefs, developer, node, sh
from .acceptance import acceptance_commands, acceptance_declarations, acceptance_problems, declared_source, empty_list, runners
from .ledger import file_children, is_ledger, ledger_note, print_children
from .records import REPO_POLICY, body_line, parse_parts, restamped, ship_policy
from .related import closing_prs, duplicate_question, graphql_issue, on_default, pr_line, related_work
from .run import Run
from .sh import DriverError, argv_for, say

STATE_LABELS = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage", "idea")  # umbrella is cleared by its children closing, never by a re-triage
MODIFIERS = ("checkpoint", "trivial")  # ride on agent-ready only
VERDICTS = ("PROMOTE", "NEEDS-RULING", "CHECKPOINT", "BLOCKED", "SPLIT", "STALE", "DUPLICATE")


def label_moves(verdict, size, labels, named=()):
    """(labels to add, labels to remove) for a verdict, the labels the node names for the issue first; an L never reaches agent-ready."""
    if verdict in ("PROMOTE", "CHECKPOINT") and size == "L":
        verdict = "SPLIT"
    present = set(labels)
    if verdict == "PROMOTE":
        add = ["agent-ready"]
    elif verdict == "CHECKPOINT":
        add = ["agent-ready", "checkpoint"]
    elif verdict in ("NEEDS-RULING", "DUPLICATE"):  # a duplicate is proposed to the owner, never closed
        add = ["needs-ruling"]
    elif verdict == "BLOCKED":
        add = ["blocked"]
    elif verdict == "SPLIT":
        add = ["umbrella"]
    else:
        remove = [label for label in ("agent-ready", *MODIFIERS) if label in present] if verdict == "STALE" and "checkpoint" in present else []
        return [], remove
    add = list(named) + add
    remove = [label for label in STATE_LABELS if label in present and label not in add]
    if "agent-ready" not in add:  # a modifier never stands alone
        remove += [label for label in MODIFIERS if label in present]
    elif verdict == "PROMOTE" and "checkpoint" in present:
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
LEDGER_SPLIT_LINE = "The driver files the children, posts the sweep mark that names them and triages each one."
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
        on = list({str(label).lower(): str(label) for label in labels if str(label).lower() in low}.values())  # a native type beside its label counts once
        if declared and (not on or (rule == "exactly one" and len(on) != 1)):
            return f"{len(on)} {kind} label(s) ({', '.join(on) or 'none'}); the binding requires {rule} of: {', '.join(declared)}"
    return None


ISSUE_TYPE_QUERY = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){issueType{name}}}}"


def issue_type(env, n):
    """The issue's native GitHub type (an organization's issue types), or None; `gh issue view` does not expose it.
    A read that fails says so and leaves the labels to count, as the shape gate does."""
    issue, code = graphql_issue(env, ISSUE_TYPE_QUERY, n)
    if issue is None:
        say(f"issue type unreadable (gh exit {code}): the type labels alone count")
        return None
    return (issue.get("issueType") or {}).get("name")


def file_list(files, cap=3000):
    """The brief's file list: the first `cap` tracked paths, then a line naming how many are not listed."""
    out = "\n".join(files[:cap])
    return out if len(files) <= cap else out + f"\n({len(files) - cap} more tracked files are not listed; the anchor gate above read them all)"


def referencing_prs(env, n):
    """The brief's lines for the pull requests that name the issue, each with its state and merge commit."""
    prs = env.gh_json("pr", "list", "--state", "all", "--search", f"{n} in:title,body", "--limit", "50",
                      "--json", "number,title,body,state,mergeCommit")
    ref = re.compile(rf"(?<![\w/])#{n}\b")
    lines = [f"- {pr_line(env, p)}: {p['title']}" for p in prs if ref.search(f"{p.get('title') or ''}\n{p.get('body') or ''}")]
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


def granted_gates(env):
    """The gate runs the BUILD node may run: what the run's build_commands grants, before any diff selects among them."""
    return [str(g.get("run") or "") for g in env.data.get("gate", [])
            if (g.get("cheap") is True or not env.pr_checks() or not Run.notice(g) and (Run.locally_run(str(g.get("run") or ""), g) or g.get("paths")))]


def ci_covered(env):
    """The gate runs the run's ci_commands(selected=False) counts as CI-covered. None without a pull_request workflow: the run
    refuses CI validation there, and granted_gates already grants every gate."""
    if env.lookup("ship.validation") != "ci" or not env.pr_checks():
        return []
    return [cmd for cmd, _ in Run.ci_covered((str(g.get("run") or ""), g) for g in env.data.get("gate", []))]


def acceptance_callers(env):
    """The gate commands the run admits as approved callers when it checks acceptance (its validation_commands, less acceptance)."""
    return granted_gates(env) + [str(g["ci"]) for g in env.data.get("gate", []) if g.get("ci")]


def ungranted_declarations(text, body, callers, prefixes):
    """The node's comment with each `Acceptance command:` field the run would refuse turned into prose, and what was turned:
    the run reads the newest triage's fields as grants, so a refused one would stop every run of the issue."""
    turned = []
    for cmd in acceptance_declarations(text):
        why = [p.split(": ", 1)[1] for p in acceptance_problems(declared_source(body, [cmd]), callers, prefixes) if p.startswith(f"`{cmd}`: ")]
        if why:
            tag = r"(?:[ \t]+\(part[ \t]+\d+\))?"
            field = rf"(?m)^([ ]{{0,3}}(?:[-*][ ]+)?)(?:Acceptance command{tag}:|\*\*Acceptance command{tag}:\*\*)(\s*`{re.escape(cmd)}`)"
            text = re.sub(field, lambda m: f"{m.group(1)}Acceptance check, not a grant ({why[0]}):{m.group(2)}", text)
            turned.append(cmd)
    return text, turned


def binding_notes(env, body, labels):
    """What the binding makes of the issue's asks: a `Flow: direct` with no `ship.direct` globs, a `trivial` label under a policy that does not merge on it;
    the flow value and the label are read as the run reads them, exactly."""
    notes = []
    if body_line(body, "Flow") == "direct" and not env.lookup("ship.direct", []):
        notes.append("The body asks `Flow: direct`, and the binding declares no `ship.direct` globs: the run takes the class the diff warrants, not the direct lane.")
    policy, _, whose = ship_policy(env)
    whose = whose.split(", unattended_classes from ")[0]  # the policy's own source
    if "trivial" in labels and policy != "trivial-merge":
        where = f'`ship.policy = "{policy}"`' if whose == REPO_POLICY else f'`policy = "{policy}"` in {whose}'
        notes.append(f"The issue carries `trivial`, which does not let the driver merge under {where} (only `trivial-merge` merges on it): "
                     "here it picks the trivial builder and nothing more.")
    return notes


TRIGGER_REF = re.compile(r"(?<![\w/.-])#(\d+)")  # Get-CompileProgram.ps1's unblock rule: a #N after a word character, `/`, `.` or `-` is another reference
LANDS = re.compile(r"[ \t]*(?:part[ \t]+\d+[ \t]+)?(?:lands|landed|has[ \t]+landed|merges|is[ \t]+merged|closes|is[ \t]+closed|ships|has[ \t]+shipped)\b",
                   re.IGNORECASE)  # contract section 4: the wordings that say only that the named issue lands


def trigger_refs(body):
    """The issue numbers the body's first line names by TRIGGER_REF when it is an `**Unblocks when:**` trigger; empty otherwise."""
    first = body.lstrip().partition("\n")[0]
    return {int(x) for x in TRIGGER_REF.findall(first)} if first.startswith("**Unblocks when:**") else set()


def fired_trigger(env, body):
    """(body, note): the body less its first line when that line is an `**Unblocks when:**` trigger naming exactly one issue that
    is closed as completed, or closed by a pull request whose merge commit is on the fetched default branch, and the text right
    after the reference says only that it lands (LANDS); the note is the comment's sentence on it, None when the line names no
    single issue or that issue has not landed."""
    first, _, rest = body.lstrip().partition("\n")
    if len(refs := trigger_refs(body)) != 1:
        return body, None
    m = refs.pop()
    try:
        i = env.gh_json("issue", "view", str(m), "--json", "state,stateReason")
    except DriverError as exc:  # the line stays: an unread trigger is not a fired one
        return body, f"The body's first line names #{m}, whose read failed ({exc}), so the line stays in place."
    reason = str(i.get("stateReason") or "").upper()
    if i.get("state") != "CLOSED" or reason == "NOT_PLANNED":
        return body, None
    if reason == "COMPLETED":
        evidence = f"#{m} is closed as completed"
    else:
        closers = closing_prs(env, m)
        if closers is None:
            return body, f"The body's first line names #{m}, closed as {reason.lower() or 'no reason'}, whose closing pull requests are unreadable, so the line stays in place."
        landed = [(p["number"], sha) for p in closers if (sha := (p.get("mergeCommit") or {}).get("oid")) and on_default(env, sha) == "yes"]
        if not landed:
            return body, None
        evidence = f"#{m} is closed by #{landed[0][0]}, whose merge commit {landed[0][1]} is an ancestor of origin/{env.default}"
    if not LANDS.match(first, TRIGGER_REF.search(first).end()):
        return body, (f"The body's first line, `{first.strip()}`, names #{m}, and #{m} has landed ({evidence}), but the line does not say only "
                      f"that #{m} lands, so it stays in place: the owner clears it once its event has happened.")
    return re.sub(r"\A(?:[ \t]*\r?\n)+", "", rest), f"Trigger cleared: the driver deleted the body's first line, `{first.strip()}`: {evidence}."


def replacement_note(env, dead, proposed):
    """The question's paragraph on the dead anchor lines whose file is still tracked (a fragment finding): the node's proposed lines
    that name such a file, are one line, quote nothing inside a command span (which the gate does not grep) and that the anchor gate
    finds live at the default branch's tip, grepping at least one fragment; None when no finding is of that kind. Triage edits no
    such line: whoever answers the question pastes it."""
    paths = {p for a in dead if (m := re.match(r"fragment no longer greps in '(.+?)': ", a)) for p in m.group(1).split("', '")}
    if not paths:
        return None
    kept = []
    for line in dict.fromkeys(str(p).strip() for p in proposed or []):
        m = re.match(r"(?:[-*+]|\d+[.)])\s[^`\r\n]*`([^`\r\n]+)`", line)
        base = re.split(r"[:#]", m.group(1), 1)[0].removeprefix("./") if m else ""  # the gate cuts a span at its first : or #
        if (base and any(p == base or p.endswith("/" + base) for p in paths) and not re.search(r"[\r\n]", line)
                and not any(t[0] == "`" and re.search(r'(?<!\\)"', t) for t in re.findall(r'"(?:[^"\\\r\n]|\\.)*\\?"|`[^`\r\n]*`', line))
                and not env.dead_anchors(f"## Anchors\n\n{line}\n", f"origin/{env.default}", need_frag=True)):
            kept.append(line)
    if not kept:
        return "No replacement anchor line was found that greps in its file at the tip: write one under the body's `Anchors` heading."
    return ("Proposed replacement anchor lines, each fragment checked to grep in its file at the tip; paste them under the body's "
            "`Anchors` heading in place of the dead lines:\n\n```markdown\n" + "\n".join(kept) + "\n```")


def shape_findings(env, issue, body, labels, comment):
    """Run the real shape gate on the proposed issue before publishing its triage or labels."""
    candidate = {**issue, "body": body, "labels": [{"name": label} for label in labels],
                 "comments": [*(issue.get("comments") or []),
                              {"author": {"login": env.viewer()}, "body": comment}]}
    proposed = env.body_file("triage-shape.json", json.dumps([candidate]))
    def q(value):
        return str(value).replace("'", "''")
    wrapper = env.body_file("triage-shape.ps1", f"& '{q((sh.BIN / 'Test-AgentReadyShape.ps1').as_posix())}' "
                           f"-Issue {int(issue['number'])} -IssuesJson ([IO.File]::ReadAllText('{q(proposed)}'))\n")
    result = env.run([sh.which("pwsh"), "-NoProfile", "-File", wrapper], cwd=env.main)
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.out)
    findings = [line.split("SHAPE:", 1)[1].strip() for line in output.splitlines() if "SHAPE:" in line]
    if result.code != 0 and not findings:
        raise DriverError(f"the shape preflight failed before any triage write: {result.text}")
    return findings


def triage(env, n, dry, asked=None):
    """Grade #n and post the verdict; the exit. `asked` is None for the issue the command names; for a ledger child the same command
    filed, it is that child's question from the ledger triage ("" when it has none), and the node is already preflighted."""
    return graded(env, n, dry, asked)[0]


def graded(env, n, dry, asked=None):
    """triage's (exit, verdict): the verdict as posted, or what stopped it from being written."""
    needs = {"triager": node.READ}  # the TRIAGE node runs even in a dry run
    if asked is None and isinstance(env.runtime, developer.Settings):
        node.preflight(env.runtime, needs=needs)
    elif asked is None:
        node.preflight(env.runtime, capabilities=node.READ)
    i = env.gh_json("issue", "view", str(n), "--json", "number,title,body,labels,comments,state,createdAt")
    labels = [tag["name"] for tag in i.get("labels", [])]
    body = i.get("body") or ""
    comments = "\n\n".join(f"[{(c.get('author') or {}).get('login')}] (comment {c.get('id') or 'with no id'}, {c.get('createdAt')}) {c.get('body')}"
                           for c in i.get("comments", [])) or "(none)"
    sets = {k: env.lookup(f"labels.{k}", []) for k in ("scope", "area", "type")}
    native = issue_type(env, n) if sets.get("type") else None  # counts as the type label when the type set names it
    if native:
        i["issueType"] = {"name": native}  # the shape preflight reads it from the issue it is handed
    files = [f for f in env.git("ls-files", "-z").out.split("\0") if f]
    prefixes = runners(env)
    behind = behind_note(env)  # fetches origin: the ancestry checks below and the anchor gate read the fetched ref
    posted = body
    body, trigger_note = fired_trigger(env, posted)  # contract section 4: a fired single-issue trigger is cleared before grading
    trigger_text = f"\n{trigger_note}\n" if trigger_note else ""
    anchors = env.dead_anchors(body, f"origin/{env.default}", need_one=True)
    candidates = {}
    prompt = briefs.render("triage", number=n, title=i["title"], body=body, comments=comments, ledger=ledger_note(env, i),
                           prs=referencing_prs(env, n), related=related_work(env, n, i["title"], body, i.get("createdAt"), candidates), behind=behind,
                           grants=", ".join(f"`{cmd}`" for cmd in acceptance_commands(body, prefixes)) or "none, the body grants no command",
                           labels=json.dumps({**sets, "state": list(STATE_LABELS)}), issue_labels=", ".join(labels + ([f"{native} (its issue type)"] if native else [])) or "(none)", trusted=", ".join(sorted(env.trusted())),
                           authority="\n".join(f"- {p}" for p in env.lookup("authority.local", [])) or "(none declared)",
                           anchors="\n".join(f"- {a}" for a in anchors) or "No finding: every anchor line resolves.",
                           files=file_list(files), contract=briefs.contract_sections(env, (3, 4, 8)))
    say(f"TRIAGE node for #{n}")
    ans = node.run_node("TRIAGE", prompt, node.model(env, "triager", node.DEFAULT_BUILDER), node.READ,
                        str(env.main), node.TIMEOUTS["TRIAGE"], runtime=env.runtime)
    verdict = str(ans.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        raise DriverError(f"node TRIAGE answered verdict {ans.get('verdict')!r}")
    if verdict == "PROMOTE" and str(ans.get("deliverable") or "").strip().lower() == "finding":
        raise DriverError("node TRIAGE answered PROMOTE for a deliverable that is a finding: that is CHECKPOINT")
    if verdict == "DUPLICATE":
        ans["question"] = duplicate_question(env, n, ans.get("duplicate_of"), candidates) + (f"\n\n{ans['question']}" if ans.get("question") else "")
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
    unblocks = body.lstrip().startswith("**Unblocks when:**")  # contract section 4: a blocked body's first line names its trigger
    if verdict in ("PROMOTE", "CHECKPOINT") and unblocks:  # the trigger is still named: never agent-ready before it fires
        verdict = "BLOCKED"
        ans["comment"] = (str(ans.get("comment") or "") + "\n\nThe body's first line still names an unblock trigger, so the issue stays "
                          "`blocked`. Once the trigger fires, remove that line and run triage again.")
    elif verdict == "BLOCKED" and not unblocks:
        verdict = "NEEDS-RULING"
        ans["question"] = ("The issue waits on an event, but its body's first line names none: write `**Unblocks when:** <the trigger>` "
                           "as the first line, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    if asked:  # the ledger triage's question for this child: a ruling to take, whatever the child's own verdict
        verdict = "NEEDS-RULING"
        ans["question"] = asked + (f"\n\n{ans['question']}" if ans.get("question") else "")
    new_body = with_lines(body, size, flow, str(ans.get("doc_impact") or "").strip()) if verdict in ("PROMOTE", "CHECKPOINT") else body
    declared = {str(x).lower(): str(x) for k in ("area", "type") for x in sets.get(k) or []}
    types, have = {str(x).lower() for x in sets.get("type") or []}, {str(tag).lower() for tag in labels + ([native] if native else [])}
    named = []  # a label the node names that the issue lacks, in the binding's spelling; one type at most, none where the issue has one
    for tag in dict.fromkeys(str(x).lower() for x in ans.get("labels") or []):
        if tag in declared and tag not in have and not (tag in types and (have & types or any(n.lower() in types for n in named))):
            named.append(declared[tag])
    problem = label_problem(labels + named + ([native] if native else []), sets) if verdict in ("PROMOTE", "CHECKPOINT") and size != "L" else None
    dead = [a for a in anchors if a != sh.NO_ANCHOR]
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
    elif dead and verdict in ("PROMOTE", "CHECKPOINT") and size != "L":  # the run would stop on a dead anchor: ask first, and post once
        preflight_failed = True
        verdict = "NEEDS-RULING"
        ans["question"] = ("The anchor gate finds:\n\n" + "\n".join(f"- {a}" for a in dead) + "\n\nThe run stops on a dead anchor; fix the "
                           "anchors, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    elif verdict == "PROMOTE" and (unrunnable := acceptance_problems(body, acceptance_callers(env), prefixes)):  # the run stops on these
        preflight_failed = True
        verdict = "NEEDS-RULING"
        ans["question"] = ("The run refuses the body's acceptance as written:\n\n" + "\n".join(f"- {p}" for p in unrunnable)
                           + "\n\nName a single exact runner command without shell operators or environment prefixes, or state the "
                             "check as prose, then run triage again." + (f"\n\n{ans['question']}" if ans.get("question") else ""))
    if verdict == "NEEDS-RULING" and (note := replacement_note(env, dead, ans.get("replacements"))):
        ans["question"] = (f"{ans['question']}\n\n" if ans.get("question") else "") + note
    ans["comment"], turned = ungranted_declarations(str(ans.get("comment") or ""), body, acceptance_callers(env), prefixes)
    if turned:
        say("acceptance fields the run would refuse, kept as prose: " + ", ".join(f"`{cmd}`" for cmd in turned))
    comment = f"**Triage**\n\nVerdict: {verdict}\n\n{reasoning(str(ans.get('comment') or ''), verdict)}\n{trigger_text}"
    if verdict != "STALE" and (notes := binding_notes(env, body, labels)):  # a stale issue runs nowhere
        comment += "\n" + "\n".join(f"- {x}" for x in notes) + "\n"
    if verdict not in ("STALE", "CHECKPOINT", "BLOCKED") and not (acceptance_commands(body, prefixes) + granted_gates(env) + ci_covered(env)):
        comment += "\n- " + empty_list(body, prefixes) + " A worktree BUILD dispatch stops with `gate uncovered`; the direct lane runs every selected gate in the driver.\n"
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
    children = None
    if verdict == "SPLIT" and is_ledger(i) and asked is None:  # a ledger child is no ledger: its own SPLIT is the owner's to file
        children, sha = print_children(env, n, i, ans, declared)
        comment += f"\n{LEDGER_SPLIT_LINE}\n"
    elif verdict == "SPLIT":  # nothing files the children: say who does
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
            comment = (f"**Triage**\n\nVerdict: {verdict}\n\n{reasoning(str(ans.get('comment') or ''), verdict)}\n{trigger_text}"
                       f"\nQuestion for the owner:\n\n{question}\n")
            add, remove = label_moves(verdict, size, labels, named)
    say(f"verdict {verdict}; labels +{add} -{remove}" + ("; body lines written" if new_body != body else ""))
    say("comment:\n" + comment)
    if (env.gh_json("issue", "view", str(n), "--json", "body").get("body") or "") != posted:  # an edit landed while the node graded
        say(f"#{n}'s body changed while triage ran: nothing written, stop posted")
        env.comment_issue(n, "**Stop:** open decision\n\nThe issue's body changed while triage ran, so this verdict graded a body that is no "
                             "longer the issue's: no body line, label or verdict is written. Run triage again on the body as it stands.\n")
        return 3, f"{verdict} not written: the body changed while triage ran"
    if new_body != posted:
        edits = [what for what, done in (("delete the fired trigger line", body != posted), ("write the Size/Flow/Doc impact lines", new_body != body)) if done]
        env.write(" and ".join(edits),
                  argv_for("gh", "issue", "edit", str(n), "-R", env.slug, "--body-file", env.body_file("issue-body.md", new_body)))
    if add or remove:
        argv = argv_for("gh", "issue", "edit", str(n), "-R", env.slug)
        for tag in remove:
            argv += ["--remove-label", tag]
        for tag in add:
            argv += ["--add-label", tag]
        env.write("set the labels", argv)
    env.comment_issue(n, comment)
    if children is not None and not dry:
        return file_children(env, n, children, sha, triage), verdict
    return 3 if preflight_failed else 0, verdict
