"""`run N`: the fixed sequence from the verified issue to the landed change, resumable from the tracker.

Every transition reads the tracker, the branch and the pull request first and continues from the
first state not yet reached; optional board metadata caches only claim correlation. The model is
asked only at the CHECKPOINT, BUILD, REVIEW and FOLLOWUP nodes (TRIAGE is `triage N`'s); everything else is a call to
a script under bin/ or to git/gh.
"""
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import briefs, execution, node, sh, validation
from .coordination import Claim, worker_env
from .sh import DriverError, Refused, Stopped, argv_for, say, script

FIX_CAP = 3
ORDER = ["direct", "express", "standard", "full"]
MODE = {"express": "light", "standard": "sized", "full": "probe"}
STOP_REASONS = ("review cap", "open decision", "dead anchor", "gate uncovered", "acceptance unrunnable")
FULL_PREFIXES = (".github/workflows/", ".github/actions/", "actions/", "hooks/", "release/", ".claude/ouro")
EXEC_EXT = (".ps1", ".py", ".sh", ".yml", ".yaml", ".json", ".toml", ".js", ".ts", ".cs", ".cmd", ".bat")
DOCS_EXT = (".md", ".txt", ".rst", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg")  # what a docs-only diff may hold: nothing a reader probes
REVIEW_MARK = re.compile(r"\*\*Review\*\* round ([0-9]+): (CLEAN|FINDINGS) @ ([0-9a-f]{40})")
PART_ITEM = re.compile(r"(?m)^- \[([ xX])\] ([0-9]+)\. (\*\*.+?\(Flow: \w+\)\.\*\*.*)$")  # the contract's part form alone: a plain task list is no checklist
RECORD = re.compile(r"<!-- ouro-record: (.*?) -->", re.DOTALL)
BAD_RUN = re.compile(r"(\d+):(failure|cancelled|timed_out|action_required|startup_failure|stale)")
SEVERITY = {"blocker": "H", "major": "M", "minor": "L"}
EDITS_QUERY = ("query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){issue(number:$n){"
               "userContentEdits(first:100,after:$c){nodes{editedAt editor{login}}pageInfo{hasNextPage endCursor}}}}}")
COPILOT_STATE_QUERY = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){id "
                       "reviewRequests(first:20){nodes{requestedReviewer{__typename ... on Bot{login}}}} "
                       "reviews(first:100){nodes{author{login} commit{oid}}}}}}")
THREADS_QUERY = ("query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){"
                 "reviewThreads(first:100,after:$c){nodes{id isResolved comments(first:50){totalCount "
                 "nodes{databaseId author{login} body path line}}}pageInfo{hasNextPage endCursor}}}}}")
REVIEW_DENIED = ["Bash(git push *)", "Bash(git commit *)", "Bash(gh *)"]
BUILD_LINE = "Ouro-Build: "
ACCEPTANCE = ("python3 ", "python ", "pwsh ", "dotnet ", "npm ", "npx ", "pytest", "bash ", "go test")
NO_REPLY = "No reply came from the build for this thread; nothing was changed for it."
ONE_ROUND = "Copilot's one review round on this pull request is spent; this thread is left for the merger."
MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)


def watch_limit():
    return int(os.environ.get("OURO_WATCH_LIMIT", "900"))


def poll_seconds():
    return int(os.environ.get("OURO_POLL_SECONDS", "30"))


# ---- pure helpers (unit-tested directly) -------------------------------------------------------

def may_merge(policy, unattended_classes, issue_labels, flow):
    """Whether the effective policy lets the driver merge: the binding's value, narrowed by the issue."""
    if policy == "merge-on-green":
        return flow in (["express", "standard"] if unattended_classes is None else unattended_classes)
    if policy == "trivial-merge":
        return "trivial" in set(issue_labels)
    return False


def slugify(title):
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-").split("-")[:5]
    return "-".join(words)[:40].strip("-") or "change"


def parse_parts(body):
    return [(int(m.group(2)), m.group(1) in "xX", m.group(3)) for m in PART_ITEM.finditer(body)]


def body_line(body, key):
    m = re.search(rf"(?m)^{key}:[^\S\r\n]*(\S+)", body)
    return m.group(1) if m else None


def derive_flow(paths, gate_files):
    """The class a diff's paths imply; Flow only tightens, so the caller takes the greater."""
    if any(p.startswith(FULL_PREFIXES) or p in gate_files for p in paths):
        return "full"
    if any(p.endswith(EXEC_EXT) for p in paths):
        return "standard"
    return "express"


def tighter(a, b):
    return a if ORDER.index(a) >= ORDER.index(b) else b


def sev(f):
    return str(f.get("severity", "")).strip().upper()[:1] if isinstance(f, dict) else ""


def outside(f):
    """A verification finding in lines no fix touched: a follow-up, never a fix round (contract section 7)."""
    return isinstance(f, dict) and str(f.get("scope", "")).strip().upper() == "OUTSIDE"


def stands(f):
    """A verification's re-report of a finding an earlier round made: counted in that round, still a fix to make."""
    return isinstance(f, dict) and str(f.get("scope", "")).strip().upper() == "STANDS"


def unchanged_findings(records):
    """The H or M findings the last verification re-raised with the file, claim and evidence of the verification before it: a fix
    that moved nothing of them, stopped before the cap. The first review is no verification, so its findings count once re-raised."""
    verifs = [r for r in records if r.get("round", 1) > 1 and not r.get("ruled")]
    if len(verifs) < 2 or verifs[-1].get("round") != verifs[-2].get("round", 0) + 1:
        return []
    before = {(f.get("file"), f.get("claim"), f.get("evidence")) for f in verifs[-2]["findings"] if sev(f) in ("H", "M") and not outside(f)}
    return [f for f in verifs[-1]["findings"] if sev(f) in ("H", "M") and not outside(f) and (f.get("file"), f.get("claim"), f.get("evidence")) in before]


def needs_fix(rec):
    """An H or M finding a fix must answer, unless an approver ruled the record; a first review has no fix, so its scope is ignored."""
    verifying = rec.get("round", 1) > 1
    return not rec.get("ruled") and any(sev(f) in ("H", "M") and not (verifying and outside(f)) for f in rec["findings"])


def parse_review_comment(c, trusted):
    """A `**Review** round k: CLEAN|FINDINGS @ <sha>` record, only from a trusted author, line 1 alone."""
    login = str((c.get("author") or {}).get("login") or "").lower()
    if login not in trusted:
        return None
    body = str(c.get("body") or "").replace("\r\n", "\n")
    m = REVIEW_MARK.fullmatch(body.split("\n", 1)[0])
    if not m:
        return None
    ans = {}
    fences = re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)
    if fences:
        try:
            ans = json.loads(fences[-1])
        except ValueError:
            ans = {}
    return {"round": int(m.group(1)), "verdict": m.group(2), "sha": m.group(3), "body": body,
            "findings": ans.get("findings") or [], "false": ans.get("false_record_sentences"), "reader": ans.get("reader")}


def review_records(comments, trusted, approvers=()):
    """The records in order of round; an approver's `**Ruling**` comment marks the newest record posted before it as ruled."""
    seen, out, last = {}, [], None
    for c in comments:
        r = parse_review_comment(c, trusted)
        if r:
            key = (r["round"], r["sha"])
            if key not in seen:
                seen[key] = r
                out.append(r)
            last = seen[key]
            continue
        who = str((c.get("author") or {}).get("login") or "")
        if last and who.lower() in approvers and str(c.get("body") or "").split("\n", 1)[0].strip().startswith("**Ruling**"):
            last["ruled"] = who
    return sorted(out, key=lambda r: r["round"])


PENDING_MARK = "<!-- ouro-pending: "


def pending_json(items):
    """The unfiled follow-up items as one-line JSON an HTML comment can hold: no `>` reaches the comment's closer, and
    non-ASCII text stays itself (escaped, it grows six-fold against the comment's size limit)."""
    return json.dumps(items, ensure_ascii=False).replace(">", "\\u003e")


def pending_follow_ups(body):
    """The items a `**Follow-ups filed**` comment carries as unfiled, or none."""
    m = re.search(re.escape(PENDING_MARK) + r"(.*?) -->", body, re.S)
    return json.loads(m.group(1)) if m else []


def filed_follow_ups(body):
    """The entries a `**Follow-ups filed**` comment lists as filed: its bullets before any `Not filed` line."""
    head = body.split("\nNot filed", 1)[0]
    return [l[2:] for l in head.splitlines() if l.startswith("- ")]


def review_line(records, reviewer_on):
    if not reviewer_on:
        return "Review: none"
    fixes = sum(1 for r in records if needs_fix(r)) - (1 if records and needs_fix(records[-1]) else 0)
    counts, seen = {k: 0 for k in "HML"}, set()
    for r in records:
        for f in r["findings"]:
            key = (sev(f), str(f.get("file")), str(f.get("line")), " ".join(str(f.get("claim") or "").split()))
            # counted once, in the round that first reported it; a verification's OUTSIDE finding is a follow-up, not a
            # finding of the change, and a later round's restatement of it is the same finding
            if sev(f) in counts and key not in seen:
                seen.add(key)
                if not ((stands(f) or outside(f)) and r.get("round", 1) > 1):
                    counts[sev(f)] += 1
    falses = [r["false"] for r in records if isinstance(r["false"], int) and not isinstance(r["false"], bool)]
    return (f"Review: rounds:{fixes} findings:H{counts['H']}/M{counts['M']}/L{counts['L']} "
            f"false-sentences:{sum(falses) if falses else 'unread'}")


def review_comment(rnd, ans, sha):
    findings = ans.get("findings") or []
    verdict = "FINDINGS" if needs_fix({"round": rnd, "findings": findings}) else "CLEAN"
    lines = [f"**Review** round {rnd}: {verdict} @ {sha}", "", f"{len(findings)} finding(s).", ""]
    for f in findings:
        lines.append(f"- {sev(f) or '?'} {f.get('file', '')}:{f.get('line', '')} {f.get('claim', '')} ({f.get('evidence', '')})")
    lines += ["", "```json", json.dumps(ans, indent=1), "```", ""]
    return "\n".join(lines)


def render_body(rec, review):
    c = rec.get("changes") or []
    gates = rec.get("gates") or []
    raw = json.dumps(rec).replace("--", "-\\u002d")
    out = [rec.get("part_line", ""), rec.get("fixes_line", ""), review]
    out = [x for x in out if x]
    out += ["## Merge danger", f"- **Door:** {rec.get('door', 'unstated')}", f"- **Blast radius:** {rec.get('blast', 'unstated')}"]
    if rec.get("manual"):
        out.append("## Manual steps")
        for m in rec["manual"]:
            out.append(f"- `{m.get('path')}`" + ("" if m.get("edit") else ": the node gave no edit; make it by hand"))
            if m.get("edit"):
                fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", str(m["edit"]))), default=0) + 1)
                out += ["", "  " + fence, *["  " + l for l in str(m["edit"]).splitlines()], "  " + fence, ""]
    out += ["## Summary", rec.get("summary", ""), "## Changes", *[f"- {x}" for x in c],
            "<details><summary>Verification</summary>", ""]
    out += [f"- Gates at {g['sha'][:7]}: `{g['cmd']}` exit {g['exit']}" for g in gates]
    if rec.get("checked"):
        out.append("- Gates the pull request's checks run: " + ", ".join(f"`{c}`" for c in rec["checked"]))
    elif not gates:
        out.append("- Gates: none selected")
    out += [f"- Gate skipped, the diff touches none of its paths: `{s}`" for s in rec.get("skipped", [])]
    out += [f"- Observed successful execution @ {rec.get('execution_head')}: `{cmd}`; "
            f"evidence `{rec.get('execution_evidence')}`" for cmd in rec.get("execution_commands", [])]
    out += [f"- BUILD reported `{g.get('cmd')}`: exit {g.get('exit')}" for g in rec.get("build_gates") or []]
    out += [f"- Driver validation @ {g['sha']}: `{g['cmd']}` exit {g['exit']}; evidence `{g['log']}`"
            for g in rec.get("validation_results") or []]
    out += [f"- Driver validation attempt @ {g['sha']}: `{g['cmd']}` exit {g['exit']}; evidence `{g['log']}`"
            for g in rec.get("validation_attempts") or [] if g not in (rec.get("validation_results") or [])]
    if rec.get("note"):
        out.append(f"- {rec['note']}")
    out += [f"- {review}", ""]
    if rec.get("usage"):
        out += [rec["usage"], ""]
    out += ["</details>", "", "## Follow-up", *[f"- {x}" for x in rec.get("residue", [])], f"<!-- ouro-record: {raw} -->", ""]
    return "\n".join(out)


PYTEST_SECTION = re.compile(r"^={3,} (.+?) ={3,}$")
PYTEST_KEEP = re.compile(r"^(FAILED|ERROR) |^={3,} (short test summary info|.*\b(failed|errors?)\b.*) ={3,}$")
LOG_PREFIX = re.compile(r"^[^\t]*\t[^\t]*\t\d{4}-\d\d-\d\dT[\d:.]+Z ?|\x1b\[[0-9;]*m")  # gh's job, step and timestamp; a colour
ERROR_MARK = re.compile(r"\b(error|failed|failure|fatal|panic|traceback|exception)\b", re.I)


def failure_extract(log, cap=200, tail=60):
    """What a fix round reads of a red log: pytest's FAILURES and ERRORS sections with the FAILED and ERROR lines and the
    result line, or else every line an error marker names, with two lines of context, then the log's tail. Over `cap`
    lines it cuts from the middle; a fixed tail misses a failure that a long SKIPPED list follows."""
    lines = log.strip().splitlines()
    text = [LOG_PREFIX.sub("", l) for l in lines]
    keep, inside, pytest = [], False, False
    for i, t in enumerate(text):
        if m := PYTEST_SECTION.match(t):
            inside = m.group(1) in ("FAILURES", "ERRORS")
            pytest = pytest or inside
        if inside or (pytest and PYTEST_KEEP.search(t)):
            keep.append(i)
    if not pytest:
        keep = sorted({j for i, t in enumerate(text) if ERROR_MARK.search(t) for j in range(max(0, i - 2), i + 3) if j < len(lines)})
    if len(keep) > cap:
        keep = keep[:cap // 2] + keep[-(cap // 2):]
    if not pytest:
        keep = sorted(set(keep) | set(range(max(0, len(lines) - tail), len(lines))))
    out, last = [], -1
    for i in keep:
        if i > last + 1:
            out.append(f"[... {i - last - 1} line(s) left out ...]")
        out.append(lines[i])
        last = i
    return "\n".join(out)


def body_sections(body):
    """{`## ` heading: its section's text} of a pull request body, the record comment cut and line ends as git writes them."""
    parts = re.split(r"(?m)^(?=## )", RECORD.sub("", (body or "").replace("\r\n", "\n")))
    return {p.split("\n", 1)[0]: "\n".join(l.rstrip() for l in p.strip().splitlines()) for p in parts if p.startswith("## ")}


def drawn_body(rec, review, body=""):
    """render_body, keeping each section of `body` a person edited since the driver drew it: the record holds a digest of
    each section as drawn, and a section that no longer matches its digest is theirs, kept as they left it."""
    digest = lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]
    drawn = rec.get("drawn") or {}
    kept = {h: t for h, t in body_sections(body).items() if h in drawn and digest(t) != drawn[h]}
    rec["drawn"] = {}
    fresh = body_sections(render_body(rec, review))
    rec["drawn"] = {h: digest(t) for h, t in fresh.items()}
    out = render_body(rec, review)
    for h, t in kept.items():
        if h in fresh:
            out = out.replace(fresh[h], t, 1)
    return out


def without_build_lines(text):
    return "\n".join(l for l in text.splitlines() if not l.startswith(BUILD_LINE)).strip()


def acceptance_text(body):
    """The approved body's acceptance/test/verification sections, including prose requirements."""
    level, text = 0, []
    for line in body.splitlines():
        h = re.match(r"(#{2,3})\s+(.*)", line)
        if h and (not level or len(h.group(1)) <= level):
            level = len(h.group(1)) if re.search(r"\b(acceptance|tests?|testing|verif\w*)\b", h.group(2), re.I) else 0
        elif level:
            text.append(line)
    return "\n".join(text)


def acceptance_spans(body):
    """Backticked spans in the sections whose heading names acceptance, tests or verification."""
    joined = acceptance_text(body)
    spans = [s.strip() for s in re.findall(r"`([^`\n]+)`", joined)]
    for block in re.findall(r"(?ms)^```(?:sh|bash|shell|powershell|ps1)?[ \t]*\n(.*?)^```", joined):
        spans.extend(line.strip() for line in block.splitlines() if line.strip())
    return spans


def acceptance_commands(body):
    """The acceptance spans that start like a runner: what the brief grants."""
    return list(dict.fromkeys(s for s in acceptance_spans(body) if s.startswith(ACCEPTANCE)))


PREFIXED = re.compile(r"^(?:[A-Za-z_]\w*=\S*\s+)+")  # NAME=value words before a runner, which no grant covers


def named_commands(body):
    """The acceptance spans that name a command: a runner after any leading NAME=value words."""
    return list(dict.fromkeys(s for s in acceptance_spans(body) if PREFIXED.sub("", s).startswith(ACCEPTANCE)))


DECLARED_ACCEPTANCE = re.compile(r"(?mi)^[ ]{0,3}(?:[-*][ ]+)?(?:Acceptance command:|\*\*Acceptance command:\*\*)\s*`([^`\n]+)`[ \t]*$")


def acceptance_declarations(text):
    """An explicit field is prose, never a fenced or indented historical example."""
    result, fence = [], None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        marker = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence) and not marker.group(2).strip(" \t"):
                fence = None
            continue
        if fence is None and not line.startswith(("    ", "\t")):
            result.extend(DECLARED_ACCEPTANCE.findall(line))
    return result


def acceptance_source(run):
    """Only explicit fields in approved spec comments extend the body's grants."""
    comments = getattr(run, "spec_amendments", lambda: [])()
    declared = [command for comment in comments for command in acceptance_declarations(str(comment.get("body") or ""))]
    return run.body + ("\n## Acceptance commands\n" + "\n".join(f"Acceptance command: `{cmd}`" for cmd in declared) if declared else "")


def acceptance_problems(body, caller_commands=()):
    problems = []
    allowed = set(acceptance_commands(body))
    for cmd in dict.fromkeys(named_commands(body) + acceptance_declarations(body)):
        try:
            _, environment = validation.command(cmd)
            if environment:
                if cmd not in caller_commands:
                    raise DriverError("leading environment assignments require an already-approved caller validation command")
                continue  # caller handoff only; acceptance_commands never makes this a worker grant
            if cmd not in allowed:
                raise DriverError("this explicit command is outside the approved acceptance runner forms")
        except DriverError as ex:
            problems.append(f"`{cmd}`: {ex}")
    return problems


def empty_list(body):
    """Why a run whose BUILD node would get an empty command list stops before the first build: it would ship code no node ran."""
    named = named_commands(body)
    return (("The acceptance names " + ", ".join(f"`{s}`" for s in named) + ", and the" if named else "The")
            + " BUILD node would get an empty command list: no acceptance command it is granted, and no `[[gate]]` for the area marked `cheap`, "
            "run here, or declaring `paths` (an environment prefix is not a runner the brief grants). A build would ship code no node ran. "
            "Name the acceptance command in the issue, or mark a gate `cheap = true`, or give it `paths` or a local run.")


def read_record(body):
    m = RECORD.search(body or "")
    return json.loads(m.group(1)) if m else {}


def review_plan(value, size):
    """(the reviewer that reads first, Copilot after it) for a ship.review value and a Size: the rows of the binding doc's table."""
    if size == "S":
        return {"adversarial-review": ("node", False), "copilot": (None, True), "external-audit": ("reader", False)}.get(value, (None, False))
    return ("node", value == "copilot")


def derive_size(paths, lines):
    """('M', why) when a diff is over the S shape (contract section 4), else ('S', '')."""
    counted = [p for p in paths if posixpath.basename(p) != "CHANGELOG.md"]
    why = []
    if len(counted) > 3:
        why.append(f"{len(counted)} files")
    if lines > 60:
        why.append(f"{lines} changed lines")
    why += [f"a workflow ({p})" for p in paths if p.startswith(".github/workflows/")][:1]
    return ("M", " and ".join(why)) if why else ("S", "")


def when(text):
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def newest_trusted_triage(comments, trusted, mark="**Triage**"):
    """When the newest `mark` comment from a trusted author was made (the epoch when it carries no time), else None."""
    best = None
    for c in comments:
        login = str((c.get("author") or {}).get("login") or "").lower()
        first = str(c.get("body") or "").replace("\r\n", "\n").split("\n", 1)[0].strip()
        if login in trusted and first == mark:
            t = when(c.get("createdAt")) or MIN_TIME
            best = t if best is None or t > best else best
    return best


def is_copilot(login):
    return str(login or "").lower().startswith("copilot-pull-request-reviewer")


def parse_direct(out):
    """What Push-Direct.ps1 printed: its vehicle and whether a push happened."""
    m = re.search(r"(?m)^vehicle: (push|pr)\s*$", out)
    return {"vehicle": m.group(1) if m else None, "pushed": re.search(r"(?m)^pushed \S+ to origin at ", out) is not None}


def reader_findings(verdict):
    """The external reader's critiques as findings: blocker H, major M, minor L; an unknown severity is M."""
    out = []
    for c in verdict.get("critiques") or []:
        ev = str(c.get("evidence") or "")
        m = re.search(r"([\w./-]+\.\w+):(\d+)", ev)
        out.append({"severity": SEVERITY.get(str(c.get("severity", "")).lower(), "M"), "file": m.group(1) if m else "",
                    "line": int(m.group(2)) if m else 0, "claim": str(c.get("claim") or ""), "evidence": ev})
    return out


def restamped(e, body, findings):
    """The body with each anchor line under its `Anchors` heading that the findings name re-pointed at the one tracked file of the same
    suffix at the default branch's tip that holds every fragment on it, and the `Anchors @ <sha>` stamp set to that tip; the body as it is
    when no line can be re-pointed. A Deliverable clause is never re-pointed: one naming a deleted file is stale (contract section 3)."""
    ref = f"origin/{e.default}"
    dead = set()
    for f in findings:
        m = re.match(r"path does not resolve: '(.+)'$", f) or re.match(r"fragment no longer greps in '(.+?)': ", f)
        if m:
            dead.add(m.group(1))
    out, moved, anchors, fenced = [], 0, False, False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if line.startswith("#") and not fenced:
            anchors = bool(re.match(r"#+\s*Anchors\b", line))
        m = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s[^`\r\n]*`)([^`\r\n]+)(`.*)$", line) if anchors else None
        span = m.group(2) if m else ""
        base = re.split(r"[:#]", span, 1)[0]  # the gate cuts a span at its first : or # (`path:171`, `path#L171`)
        if span and any(d == base or d.endswith("/" + base) for d in dead):
            files = None
            for frag in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
                frag = frag.replace('\\"', '"')
                if 12 <= len(frag) <= 120:
                    hits = {l[len(ref) + 1:] for l in e.git("grep", "-F", "-l", "-z", "-e", frag.strip("`"), ref, check=False).out.split("\0") if l.startswith(ref + ":")}
                    files = hits if files is None else files & hits
            if files and len(files) == 1 and (new := next(iter(files))) != base and Path(new).suffix == Path(base).suffix:
                line, moved = m.group(1) + new + span[len(base):] + m.group(3), moved + 1
        out.append(line)
    if not moved:
        return body
    sha = e.git("rev-parse", "--short=7", ref).out.strip()
    return re.sub(r"(Anchors\s*@\s*)[0-9a-f]{7,40}", lambda m: m.group(1) + sha, "\n".join(out) + ("\n" if body.endswith("\n") else ""))


# ---- the run ----------------------------------------------------------------------------------

PRINTS = re.compile(r"\s*(?:echo|pwsh -NoProfile -Command [\"']Write-Host)\b", re.I)
SKIPS = re.compile(r"\s*(?:echo|pwsh -NoProfile -Command [\"']Write-Host)\s+(?:-\w+\s+\S+\s+)*[\"']?skip", re.I)


class Run:
    def __init__(self, env, n, part_arg):
        self.env, self.n, self.part_arg = env, n, part_arg
        self.gated = set()
        self.committed_here = set()
        self.pending = []  # review comments held until the express draft opens
        self.sync_tried = False
        self.synced = set()
        self.fresh = False
        self.notes = []  # sentences the record carries: a tightening, a reader that was unavailable
        self.in_lane = False
        self.claim = Claim(self)

    # ---- reading the tracker
    def read_issue(self):
        i = self.env.gh_json("issue", "view", str(self.n), "--json", "number,title,body,labels,comments,state")
        self.issue = i
        self.title, self.body = i["title"], i.get("body") or ""
        self.labels = [l["name"] for l in i.get("labels", [])]
        return i

    def plan_part(self):
        ps = parse_parts(self.body)
        self.parts_n = len(ps)
        self.k = None
        flow = body_line(self.body, "Flow") or "standard"
        if ps:
            if self.part_arg is not None:
                hit = [p for p in ps if p[0] == self.part_arg]
                if not hit:
                    raise Refused(f"#{self.n} has no part {self.part_arg}")
                self.k = self.part_arg
            else:
                open_ = [p for p in ps if not p[1]]
                if not open_:
                    raise Refused(f"every part of #{self.n} is ticked")
                self.k = open_[0][0]
            m = re.search(r"\(Flow: (\w+)\)", [p for p in ps if p[0] == self.k][0][2])
            flow = m.group(1) if m else flow
        elif self.part_arg is not None:
            raise Refused(f"#{self.n} is not a checklist deliverable: no --part")
        self.flow = flow if flow in ORDER else "standard"
        self.size = body_line(self.body, "Size") or "M"
        self.primary, self.copilot = review_plan(self.env.lookup("ship.review"), self.size)
        suffix = f"-p{self.k}" if self.k else ""
        areas = self.env.lookup("labels.area", []) or []
        types = self.env.lookup("labels.type", []) or []
        low = {str(a).lower(): str(a) for a in areas}
        self.areas = [low[l.lower()] for l in self.labels if l.lower() in low]  # the issue's order, the binding's spelling: the branch prefix depends on it
        prefix = self.env.lookup("repo.branch_prefix") or (self.areas + [l for l in self.labels if l in types] + ["ouro"])[0]
        self.dirname = f"{self.n}-{slugify(self.title)}{suffix}"
        self.branch = f"{prefix}/{self.dirname}"
        self.worktree = self.env.main / ".claude" / "worktrees" / self.dirname
        self.fixes = self.k is None or self.k == self.parts_n

    def part_scope(self, node):
        """The brief text that pins a checklist part's node to that part; empty on a deliverable with no checklist."""
        if not self.k:
            return ""
        line = [p for p in parse_parts(self.body) if p[0] == self.k][0][2]
        head = f"## Part {self.k}/{self.parts_n}\n\nThis run is part {self.k}/{self.parts_n}: {line}\n\n"
        if node == "build":
            return head + "Build this part only. Every other part is out of scope: each lands in its own pull request.\n\n"
        return head + "A gap that another part's acceptance covers is not a finding against this change.\n\n"

    def refuse_other_pr(self):
        """An open pull request from another branch whose body fixes the issue, or carries the part this run takes, on its own line: the run would duplicate it."""
        e = self.env
        part = rf"|Part {self.k}/\d+ of #{self.n}" if self.k else ""
        mine = re.compile(rf"(?m)^(Fixes #{self.n}{part})\s*$")
        for p in e.gh_json("pr", "list", "--state", "open", "--search", f"{self.n} in:body", "--limit", "50", "--json", "number,headRefName,body,url,state"):
            if p.get("state", "OPEN") == "OPEN" and p.get("headRefName") != self.branch and mine.search(p.get("body") or ""):
                raise Refused(f"#{self.n} is already being fixed by pull request #{p['number']} from {p['headRefName']} ({p.get('url') or ''}): nothing to build")

    def manual_paths(self):
        """The `Manual: <path>` lines triage wrote, those its own part names when the run takes a checklist part."""
        paths = re.findall(r"(?m)^Manual: (\S+)\s*$", self.body)
        if self.k:
            text = [p for p in parse_parts(self.body) if p[0] == self.k][0][2]
            paths = [p for p in paths if f"`{p}`" in text]
        return paths

    def manual_known(self):
        """The manual paths so far: the planned ones, then those the branch's commits already carry."""
        known = self.manual_paths()
        if self.commits_past_base():
            known += [m["path"] for m in self.build_facts(evidence=False)["manual"] if m["path"] not in known]
        return known

    def manual_block(self):
        paths = self.manual_known()
        if not paths:
            return "(none)"
        return ("\n".join(f"- `{p}`" for p in paths) + "\n\nThese configuration paths require manual edits. Build everything else, "
                "and answer `manual` with one entry per path holding the exact edit a person makes there, as a unified diff or the whole new content.")

    def protected_path(self, fp):
        """A harness configuration path in this worktree, normalized for the record."""
        if not fp:
            return None
        try:
            root = self.worktree.resolve()
            raw = root / Path(str(fp))
            resolved = Path(os.path.abspath(raw.resolve())).relative_to(root)
            lexical = Path(os.path.abspath(raw))
            try:
                lexical = lexical.relative_to(root)
            except ValueError:
                # Expand an alternate worktree spelling without resolving links below it.
                anchor = lexical.parent
                while anchor != anchor.parent and anchor.resolve() != root:
                    anchor = anchor.parent
                lexical = lexical.relative_to(anchor) if anchor.resolve() == root else resolved
            paths = [lexical, resolved]
        except (ValueError, OSError):
            return None
        relatives = [posixpath.normpath(p.as_posix()) for p in paths]
        if any(rel.lower().startswith(".claude/worktrees/") for rel in relatives):
            return None
        prefixes = (".claude/", ".codex/", ".agents/", ".aws/")
        for rel in relatives:
            first, separator, rest = rel.partition("/")
            rel = first.lower() + separator + rest
            if rel.startswith(prefixes):
                return rel
        return None

    def manual_steps(self, ans):
        """Manual configuration edits, then planned or denied paths the node gave no edit for."""
        steps = []
        for m in ans.get("manual") or []:
            rel = self.protected_path(m.get("path")) if isinstance(m, dict) else None
            if rel and rel not in [s["path"] for s in steps]:
                steps.append({"path": rel, "edit": str(m.get("edit") or "")})
        denied = []
        for d in ans.get("_denied") or []:
            rel = self.protected_path((d.get("tool_input") or {}).get("file_path")) if isinstance(d, dict) else None
            if rel and rel not in denied:
                denied.append(rel)
        if denied:
            say(f"  denied configuration paths: {', '.join(denied)}: manual steps")
        for p in self.manual_paths() + denied:
            p = self.protected_path(p) or p
            if p not in [s["path"] for s in steps]:
                steps.append({"path": p, "edit": ""})
        return steps

    def spec_amendments(self):
        """Preserve approved comment boundaries as well as their author/triage cutoff."""
        trusted = self.env.trusted()
        cs = [c for c in self.issue.get("comments") or [] if str((c.get("author") or {}).get("login") or "").lower() in trusted]
        since = newest_trusted_triage(cs, trusted)
        return [c for c in cs if since is not None and (when(c.get("createdAt")) or MIN_TIME) >= since]

    def spec_comments(self):
        """The newest trusted triage and later trusted amendments, rendered only for briefs."""
        return "\n\n".join(f"{(c.get('author') or {}).get('login')} at {c.get('createdAt')}:\n{str(c.get('body') or '').strip()}" for c in self.spec_amendments()) or "(none)"

    def find_pr(self, branch=None, states=("OPEN", "MERGED")):
        prs = self.env.gh_json("pr", "list", "--head", branch or self.branch, "--state", "all", "--limit", "20", "--json",
                               "number,state,isDraft,headRefOid,mergeCommit,title,body,url,baseRefName")
        for state in states:
            hit = [p for p in prs if p["state"] == state]
            if hit:
                return hit[0]
        return None

    def pr_comments(self, pr):
        return self.env.gh_json("pr", "view", str(pr["number"]), "--json", "comments")["comments"]

    def records(self, pr):
        recs = review_records(self.pr_comments(pr), self.env.trusted(), self.env.approvers()) if pr else []
        return recs + [r for r in self.pending]

    # ---- failures
    def stop_issue(self, reason, content, code):
        assert reason in STOP_REASONS
        self.env.comment_issue(self.n, f"**Stop:** {reason}\n\n{content}\n")
        remove = [l for l in ("agent-ready", "trivial", "checkpoint") if l in self.labels]
        argv = argv_for("gh", "issue", "edit", str(self.n), "-R", self.env.slug)
        for l in remove:
            argv += ["--remove-label", l]
        self.env.write("swap agent-ready -> needs-ruling", argv + ["--add-label", "needs-ruling"])
        self.claim.update(f"stopped: {reason}", close=True)
        say(f"stopped: {reason}")
        raise Stopped(code)

    # ---- transitions
    def go(self):
        e = self.env
        self.read_issue()
        coordination = self.claim.config and bool(os.environ.get(self.claim.config.credential_env)) and not e.dry
        if coordination and self.issue.get("state") == "CLOSED" and self.part_arg is None:
            parts = parse_parts(self.body)
            if parts and all(done for _, done, _ in parts):
                self.part_arg = parts[-1][0]
        self.plan_part()
        say(f"#{self.n} {self.title}")
        say(f"  class {self.flow}, size {self.size}, branch {self.branch}")
        if not coordination:
            e.git("fetch", "origin", check=False)
        pr = self.find_pr()
        if getattr(e, "recover_build", []) and (self.flow == "direct" or not pr or pr["state"] != "OPEN"):
            raise Refused("BUILD recovery requires an open normal-path issue pull request; it cannot start a fresh or direct BUILD")
        if not pr and coordination:
            pr = self.find_pr(states=("CLOSED",))
        branch_known = self.branch_exists()
        lane_pr = self.find_pr(f"direct/{self.n}", states=("OPEN",)) if self.flow == "direct" and pr is None else None
        published = None
        if self.flow == "direct" and (coordination and lane_pr or pr is None and lane_pr is None and not e.dry):
            e.git("fetch", "origin", check=False)
            if lane_pr:
                self.lane_trace(lane_pr)  # before enter can post an override record
            else:
                # A merge message need not repeat the direct commit or PR body.
                # Order publications by Git, so an old PR cannot supersede a fix/revert.
                merged = self.lane_merged_direct()
                marked = e.git("log", "--first-parent", "--format=%H", "--fixed-strings",
                               f"--grep=Fixes #{self.n}", f"--grep=Revert the direct commits for #{self.n}",
                               f"origin/{e.default}").out.splitlines()
                candidates = set(merged) | {h for h in marked if self.lane_fixes(h) or self.lane_is_revert(h)}
                heads = e.git("log", "--first-parent", "--format=%H", f"origin/{e.default}").out.splitlines()
                head = next((h for h in heads if h in candidates), None)
                if head:
                    message = e.git("log", "-1", "--format=%B", head).out
                    reconcile = (coordination or head in merged) and self.issue.get("state") == "CLOSED"
                    trace = self.lane_provenance(head) if head in merged else None
                    if ((trace and trace["stage"] == "revert")
                            or self.lane_is_revert(head) and re.search(r"(?m)^Ouro-Direct:", message)):
                        self.lane_provenance(head)  # validate even a completed stop's chain
                        # A trusted developer's final write acknowledges this execution.
                        reconcile = not self.lane_stop_complete(head) or "agent-ready" not in self.labels
                    if reconcile:
                        trace = self.lane_provenance(head)
                        published = (head, trace["parent"], trace["stage"])
                        self.lane_prepare(e.git("rev-parse", f"{head}^").out.strip(), head,
                                          trace["stage"], check=False)
        if published:
            local = e.git("rev-parse", "HEAD").out.strip()
            if (local != e.git("rev-parse", f"origin/{e.default}").out.strip()
                    and e.git("merge-base", "--is-ancestor", local, f"origin/{e.default}", check=False).code != 0):
                record = self.lane_provenance(local)
                expected = {"parent": "fix", "fix": "revert"}.get(published[2])
                if record["parent"] != published[0] or record["stage"] != expected or not self.lane_commits_ahead():
                    raise DriverError("the unpushed direct stage cannot be reconciled; claim retained")
        self.claim.enter(create=self.issue.get("state") != "CLOSED" and "agent-ready" in self.labels
                         and not (pr and pr["state"] in ("MERGED", "CLOSED")))
        if coordination and not published and not lane_pr and (self.issue.get("state") == "CLOSED" and not (pr and pr["state"] == "MERGED")
                                 or pr and pr["state"] == "CLOSED"):
            self.claim.update("closed externally without merge", close=True)
            say("[2] work closed externally; claim reconciled")
            return 0
        if pr and pr["state"] == "CLOSED":
            say("[2] pull request closed externally: nothing to do")
            return 0
        if coordination:
            e.git("fetch", "origin", check=False)
        say(f"[1] bind: {e.slug}, default branch {e.default}, policy {e.lookup('ship.policy')}, review {e.lookup('ship.review')}")
        if published and published[2] == "revert":
            self.in_lane, self.worktree = True, e.main
            return self.lane_revert_stop(published[0], published[1])
        if pr is None and not branch_known and lane_pr is None and not published:
            if self.issue.get("state") == "CLOSED":
                self.claim.update("closed externally", close=True)
                say(f"[2] verify: #{self.n} is closed: nothing to do")
                return 0
            self.preflight()
            if "checkpoint" in self.labels:
                return self.checkpoint()
            self.verify()
            self.refuse_other_pr()
            if self.flow == "direct" and self.direct_eligible():
                why = self.direct_blocked()
                if why is None:
                    return self.direct()
                say(f"[2a] direct: not taken, {why}; the run continues as express from a worktree")
                self.flow = "express"
            self.require_acceptance()
            if not self.build_commands()[1]:  # the direct lane runs every selected gate itself; a worktree's build runs what it is granted
                self.stop_issue("gate uncovered", empty_list(self.body), 3)
        else:
            say("[2] verify: skipped, the run already has a branch or a pull request")
            if not (pr and pr["state"] == "MERGED"):
                why = self.label_problem()
                if why:
                    say(f"stopped: {why}")
                    raise Stopped(3)
            self.preflight()
            if not (pr and pr["state"] == "MERGED"):
                self.check_spec_trust()  # the body the nodes read on this pass, not the one the fresh run checked
        if published:
            self.in_lane, self.worktree = True, e.main
            return self.lane_after(*published)
        if lane_pr:
            return self.lane_resume(lane_pr)
        if pr and pr["state"] == "MERGED":
            return self.after_merge(pr)
        if e.dry:
            if getattr(e, "recover_build", []):
                say("  would: preserve the dirty original and offer only explicitly selected files in a private recovery checkout; normal validation/review/caps still apply")
            return self.plan(pr, branch_known)
        if getattr(e, "recover_build", []):
            from . import recovery
            recovery.prepare(self, pr, e.recover_build)
        else:
            self.ensure_worktree()
        return self.drive(pr)

    def preflight(self):
        """The node runtime's check, where a node can follow and before anything this run writes; a dry run asks no node."""
        if not self.env.dry:
            node.preflight(self.env.runtime)  # a runtime fault is a driver error, never a stop or a spent fix round

    def branch_exists(self):
        e = self.env
        local = e.git("rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}", check=False).code == 0
        remote = bool(e.git("ls-remote", "--heads", "origin", f"refs/heads/{self.branch}", check=False).out.strip())
        return local or remote

    def label_problem(self, checkpoint_ok=False):
        """Why the labels forbid a run, or None: agent-ready present, needs-ruling absent, checkpoint absent unless a finding is wanted."""
        if "agent-ready" not in self.labels:
            return f"#{self.n} is not agent-ready; run `python3 bin/ouro triage {self.n}` first"
        if "needs-ruling" in self.labels:
            return f"#{self.n} is waiting on a ruling (needs-ruling)"
        if "checkpoint" in self.labels and not checkpoint_ok:
            return f"#{self.n} is a checkpoint: its deliverable is a finding comment, and the driver builds only changes"
        return None

    @staticmethod
    def stop_text(stop):
        """The question of a node's stop answer, an object's `question` or a bare string; a stop with no question is a driver error, never
        an owner's stop: no one can rule on an empty question, and a runtime fault surfaces as one."""
        q = stop.get("question") if isinstance(stop, dict) else stop if isinstance(stop, str) else None
        if not str(q or "").strip():
            raise DriverError(f"a node answered a stop with no question: {json.dumps(stop)[:400]}")
        return str(q)

    def checkpoint(self):
        """Post a finding with read tools and BUILD's issue commands; commands use a disposable default-branch worktree, never a commit."""
        e = self.env
        trusted = e.trusted()
        comments = self.issue.get("comments") or []
        found = newest_trusted_triage(comments, trusted, "**Checkpoint finding**")
        if found is not None and found > (newest_trusted_triage(comments, trusted) or MIN_TIME):
            raise Refused(f"#{self.n} already has a `**Checkpoint finding**` newer than its newest `**Triage**`: "
                          "triage converts the issue, or a new `**Triage**` comment reopens the checkpoint")
        self.verify(checkpoint=True)
        cmds, commands = self.build_commands()
        grant = "read tools and issue commands in a fresh worktree" if commands else "read tools only in the main checkout"
        if e.dry:
            say(f"[4] CHECKPOINT: would run node CHECKPOINT ({grant}) and post its finding as a `**Checkpoint finding**` comment")
            return 0
        copy = e.scratch / "checkpoint" if commands else None
        cwd = copy or e.main
        if copy:
            node.worktree_readiness(e.runtime, e.main, copy, None, f"origin/{e.default}", scratch=e.scratch)
            e.git("worktree", "add", "--detach", str(copy), f"origin/{e.default}")
        try:
            if copy:
                self.setup_tree(copy)
            prompt = briefs.render("checkpoint", number=self.n, title=self.title, body=self.body,
                                   commands=cmds if commands else "(none: read tools only)",
                                   anchors=briefs.anchors_block(cwd, self.body), contract=briefs.contract_sections(e, (3,)))
            model = node.model(e, "builder", node.DEFAULT_BUILDER)
            say(f"[4] CHECKPOINT node ({model})")
            tools = node.Capabilities(commands=tuple(commands))
            ans = node.run_node("CHECKPOINT", prompt, model, tools, str(cwd), node.TIMEOUTS["CHECKPOINT"], runtime=e.runtime)
            if ans.get("stop"):
                self.stop_issue("open decision", self.stop_text(ans["stop"]), 3)
            finding = str(ans.get("finding") or "").strip()
            if not finding:
                raise DriverError("node CHECKPOINT answered no finding")
            if copy:
                left = e.git("status", "--porcelain", "-z", "--untracked-files=all", cwd=copy).out
                if left:
                    finding += "\n\nThe checkpoint left worktree changes; none were committed:\n" + "\n".join(
                        f"- {json.dumps(p, ensure_ascii=False)}" for p in left.split("\0") if p)
            e.comment_issue(self.n, f"**Checkpoint finding**\n\n{finding}\n")
            self.claim.update("checkpoint finding posted", close=True)
            say(f"[4] finding posted on #{self.n}")
            return 0
        finally:
            if copy:
                r = e.git("worktree", "remove", "--force", str(copy), check=False)
                if r.code != 0:
                    say(f"  the checkpoint worktree {copy} was not removed: {r.text}")

    def verify(self, checkpoint=False):
        e = self.env
        why = self.label_problem(checkpoint)
        if why:
            raise Refused(why)
        self.check_spec_trust()
        args =["-Issue", str(self.n)] + ([] if e.dry else ["-Demote"])
        r = self.env.run(script("Test-AgentReadyShape.ps1", *args), cwd=e.main)
        if r.code != 0:
            if "SHAPE:" not in r.out:
                raise DriverError(f"the shape gate failed: {sh.cmdline(r.argv)}: {r.text}")
            say("[2] verify: shape finding")
            say(r.out.strip())
            raise Refused(f"#{self.n} fails the shape gate" + ("" if e.dry else ": demoted to needs-triage, comment posted"))
        r = self.env.run(script("Test-AgentReadyAnchors.ps1", "-Ref", f"origin/{e.default}"), cwd=e.main)
        if r.code != 0:
            raise DriverError(f"the anchor gate failed: {sh.cmdline(r.argv)}: {r.text}")
        found = self.anchor_findings(r.out)
        if found:
            body = restamped(e, self.body, found)
            if body == self.body or e.dead_anchors(body, f"origin/{e.default}"):
                self.stop_issue("dead anchor", "\n".join(f"- {f}" for f in found), 2)
            e.write("re-stamp the moved anchors", argv_for("gh", "issue", "edit", str(self.n), "-R", e.slug, "--body-file", e.body_file("issue-body.md", body)))
            self.body = body
            say("[2] verify: anchors re-pointed at the file their fragments moved to, the body re-stamped")
        say("[2] verify: agent-ready, shape green, anchors resolve")
        miss = [a for a in self.areas if not any("*" in g.get("areas", []) or a in g.get("areas", []) for g in e.data.get("gate", []))]
        if miss and not checkpoint:
            self.stop_issue("gate uncovered", f"No `[[gate]]` covers the area label(s): {', '.join(miss)}.", 3)

    def check_spec_trust(self):
        """The spec is the body as the newest trusted Triage comment left it: a later edit by anyone else stops the run."""
        e = self.env
        trusted = e.trusted()
        approved = newest_trusted_triage(self.issue.get("comments") or [], trusted)
        if approved is None:
            self.stop_issue("open decision", f"#{self.n} has no `**Triage**` comment from a trusted author, so it has no approved spec.", 2)
        owner, repo = e.slug.split("/", 1)
        cursor = None
        while True:
            args = ["api", "graphql", "-f", f"query={EDITS_QUERY}", "-f", f"o={owner}", "-f", f"r={repo}", "-F", f"n={self.n}"]
            page = json.loads(e.gh(*args, *(["-f", f"c={cursor}"] if cursor else [])).out)["data"]["repository"]["issue"]["userContentEdits"]
            for edit in page["nodes"]:
                who = str((edit.get("editor") or {}).get("login") or "")
                at = when(edit.get("editedAt"))
                if (at is None or at > approved) and who.lower() not in trusted:
                    self.stop_issue("open decision", f"The body was edited by {who or 'no account (a deleted one)'} at {edit.get('editedAt')}, "
                                    "after the newest trusted `**Triage**` comment: the spec is not the approved one.", 2)
            if not page["pageInfo"]["hasNextPage"]:
                return
            cursor = page["pageInfo"]["endCursor"]

    def anchor_findings(self, text):
        out, mine = [], False
        for line in text.splitlines():
            m = re.match(r"#(\d+) ", line)
            if m:
                mine = int(m.group(1)) == self.n
            elif mine and line.strip().startswith("STALE-ANCHOR:"):
                out.append(line.strip()[len("STALE-ANCHOR:"):].strip())
        return out

    def ensure_worktree(self):
        e = self.env
        def readiness(base):
            node.worktree_readiness(e.runtime, e.main, self.worktree, self.branch, base)
        if (self.worktree / ".git").exists():
            readiness("HEAD")
            say(f"[3] worktree: {self.worktree} exists")
            if self.catch_up("HEAD", self.worktree, before_move=readiness):
                readiness("HEAD")  # Check the final actual inputs before setup, without replacing an occupied tree.
            self.setup_tree(self.worktree, fresh=False)  # a tree an older driver or an interrupted setup left is set up once, and kept
            return
        # git check-ignore refuses a path beyond a symlink, so a symlinked .claude/worktrees is checked at its target
        real = self.worktree.resolve()
        if not real.is_relative_to(e.main.resolve()):
            raise DriverError(f"{self.worktree} resolves to {real}, outside the repository: point .claude/worktrees at a directory inside it")
        rel = real.relative_to(e.main.resolve()).as_posix()
        if self.worktree.exists() or self.worktree.is_symlink():
            raise DriverError(f"worktree path already exists without Git metadata: {self.worktree}")
        r = e.git("check-ignore", "-q", "--no-index", rel + "/", check=False)
        if r.code != 0:
            raise DriverError(f"{rel} is not ignored (git check-ignore exit {r.code}): add {rel.rsplit('/', 1)[0]}/ to the repo's .gitignore")
        local = e.git("rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}", check=False).code == 0
        remote = False if local else self.fetch_branch()
        if local:
            self.catch_up(f"refs/heads/{self.branch}", before_move=readiness)
            argv = ["worktree", "add", rel, self.branch]
        elif remote:
            readiness(f"refs/remotes/origin/{self.branch}")
            argv = ["worktree", "add", rel, "-b", self.branch, f"origin/{self.branch}"]
        else:
            readiness(f"origin/{e.default}")
            argv = ["worktree", "add", rel, "-b", self.branch, f"origin/{e.default}"]
        say(f"[3] worktree: git {' '.join(argv)}")
        e.git(*argv)
        self.setup_tree(self.worktree)

    def fetch_branch(self):
        """Whether origin has the branch, its tip fetched to origin/<branch>; a failed read is a driver error, never a stale ref."""
        e = self.env
        r = e.git("ls-remote", "--heads", "origin", f"refs/heads/{self.branch}", check=False)
        if r.code != 0:
            raise DriverError(f"git ls-remote origin {self.branch} failed (exit {r.code}), so whether origin moved the branch is unknown: {r.text}")
        if not r.out.strip():
            return False
        f = e.git("fetch", "origin", f"+refs/heads/{self.branch}:refs/remotes/origin/{self.branch}", check=False)
        if f.code != 0:
            raise DriverError(f"git fetch origin {self.branch} failed (exit {f.code}); the run does not compare against a stale origin/{self.branch}: {f.text}")
        return True

    def catch_up(self, head, tree=None, before_move=None):
        """Bring a reused worktree's head, or a local branch, to origin/<branch> when origin is ahead: a fast-forward is the only way
        the head moves. A local head ahead of origin is kept (an express run reviews its commits before its first push); one that has
        diverged from origin stops the run naming its commits, which may be work, and nothing is reset.
        Prove the selected tip through before_move before changing a ref; return True only after a fast-forward."""
        e = self.env
        def prove(commit):
            if before_move:
                before_move(commit)
        if not self.fetch_branch():
            prove(head)
            return  # never pushed: the local head is the branch
        local = e.git("rev-parse", head, cwd=tree).out.strip()
        remote = e.git("rev-parse", f"refs/remotes/origin/{self.branch}").out.strip()
        if local == remote:
            prove(local)
            return

        def ancestor(a, b):
            code = e.git("merge-base", "--is-ancestor", a, b, check=False).code
            if code not in (0, 1):
                raise DriverError(f"git merge-base --is-ancestor {a[:7]} {b[:7]} failed (exit {code})")
            return code == 0
        if ancestor(local, remote):
            prove(remote)
            say(f"[3] origin/{self.branch} is ahead: fast-forward {local[:7]}..{remote[:7]}")
            if tree:
                e.git("merge", "--ff-only", "--quiet", remote, cwd=tree)
            else:  # branch -f, not update-ref: it refuses a branch checked out elsewhere, whose files would then undo the commits
                e.git("branch", "-f", self.branch, remote)
            return True
        if ancestor(remote, local):
            prove(local)
            say(f"[3] the local head {local[:7]} is ahead of origin/{self.branch}: kept")
            return
        mine = e.git("log", "--format=- %h %s", f"{remote}..{local}").out.strip()
        self.stop_issue("open decision", f"The local branch `{self.branch}` ({local[:7]}) and `origin/{self.branch}` ({remote[:7]}) have diverged: "
                        f"each holds commits the other lacks. The local ones:\n\n{mine}\n\nNothing was reset. Push them onto the branch, "
                        f"or drop them from the local branch, then run again.", 3)

    def setup_tree(self, tree, fresh=True):
        """The binding's repo.setup, once per worktree (a marker in its git directory says it ran), before any gate or node runs there.
        What it leaves that the repository does not ignore would land in the build's commit, so that stops the run; a tree the driver
        did not just create keeps what it held, and only the entries setup added count."""
        e = self.env
        setup = e.lookup("repo.setup")
        if not setup:
            return
        marker = tree / e.git("rev-parse", "--git-path", "ouro-setup-done", cwd=tree).out.strip()
        if marker.exists():
            return
        argv = shlex.split(str(setup).replace(e.ob.OURO_TOKEN, Path(e.ob.plugin_root() or sh.PLUGIN).as_posix()))
        exe = shutil.which(argv[0]) if argv else None
        if not exe:
            self.stop_issue("gate uncovered", f"The driver cannot run the binding's `repo.setup` `{setup}`: `{argv[0] if argv else ''}` is not on PATH.", 3)
        def status():
            return [x for x in e.git("status", "--porcelain", "-z", "--untracked-files=all", cwd=tree).out.split("\0") if x]
        before = set(status()) if tree == self.worktree else set()
        r = self.env.run([exe, *argv[1:]], cwd=tree)
        say(f"[3] setup `{setup}` in {tree.name}: exit {r.code}")
        left = [x for x in status() if x not in before] if tree == self.worktree else []
        if r.code != 0 or left:
            if fresh and tree == self.worktree:  # a tree set up by half is not resumed as done: the next run creates it anew
                e.git("worktree", "remove", "--force", str(tree), check=False)
            # the build commits with add -A: `??` is a file to ignore or write elsewhere, ` M` a tracked file setup rewrote
            listed = ("\n\nIt left entries in `" + tree.name + "` that the build would commit (`??` untracked and not ignored, ` M` a tracked file it rewrote):\n\n"
                      + "\n".join(f"- `{p}`" for p in left[:40]) + "\n\nIgnore what it writes in the repository's .gitignore, or have setup write elsewhere.") if left else ""
            if r.code == 0:
                self.stop_issue("gate uncovered", f"The binding's `repo.setup` `{setup}` ran, but what it wrote is not ignored.{listed}\n", 3)
            tail = "\n".join((r.out + r.err).strip().splitlines()[-40:])
            self.stop_issue("gate uncovered", f"The binding's `repo.setup` `{setup}` exited {r.code} in `{tree.name}`, so no gate can run there:\n\n```\n{tail}\n```{listed}\n", 3)
        marker.write_text("", encoding="utf-8")

    def wt_git(self, *args, check=True):
        return self.env.git(*args, cwd=self.worktree, check=check)

    def head(self):
        return self.wt_git("rev-parse", "HEAD").out.strip()

    def commits_past_base(self):
        return int(self.wt_git("rev-list", "--count", f"origin/{self.env.default}..HEAD").out.strip() or 0)

    def changed_paths(self):
        out = self.wt_git("diff", "--name-only", "-z", f"origin/{self.env.default}...HEAD").out
        return [p for p in out.split("\0") if p]

    # ---- the drive loop (transitions 4-9)
    def drive(self, pr):
        e = self.env
        if not e.pr_checks():
            self.add_note(f"No workflow at origin/{e.default} names pull_request, so the pull request's checks cannot run the gates: every selected gate ran here, and the run skipped the checks step.")
        if self.commits_past_base():
            self.rederive()  # a resumed run re-reads the tightening from the diff; it is kept nowhere else
        for _ in range(40):
            pr = self.find_pr() if pr is None or pr["state"] != "MERGED" else pr
            if pr and pr["state"] == "MERGED":
                return self.after_merge(pr)
            if self.commits_past_base() == 0:
                self.build()
                continue
            records = self.records(pr)
            pending = self.build_facts(evidence=False)["validation_pending"]
            if pending:
                validation.requests(pending, self.validation_commands())
                if not getattr(e, "validate", False):
                    results, failure = validation.validate(self, pending, reuse=True, execute_missing=False)
                    if failure is not None:
                        say(f"waiting for validation: invoke `ouro run {self.n}"
                            + (f" --part {self.k}" if self.k else "") + " --validate`")
                        return 0
                else:
                    results, failure = validation.validate(self, pending, reuse=True)
                    if failure == 75:
                        say("waiting for validation: execution unavailable; no owner ruling or fix round")
                        return 0
                    if failure is not None:
                        failed = results[-1]
                        tail = failure_extract(Path(failed['log']).read_text(encoding="utf-8", errors="replace"))
                        self.fix_round(f"validation `{failed['cmd']}` exited {failure} @ {failed['sha']}; "
                                       f"evidence {failed['log']}:\n{tail}", records)
                        continue
            head = results[0]["sha"] if pending else self.head()
            inputs = validation.check_inputs(self, head)
            if inputs == 75:
                say("waiting for validation: execution requires a supported lifecycle boundary")
                return 0
            if inputs is not None:
                raise Refused("execution evidence requires clean tracked/untracked inputs; preserve and resolve the worktree edits")
            at_head = [r for r in records if r["sha"] == head]
            self.caller_execution_results = results if pending else []
            if head not in self.gated:
                red = self.run_gates(head)
                if red == 75:
                    say(f"waiting for validation: invoke `ouro run {self.n}"
                        + (f" --part {self.k}" if self.k else "")
                        + " --validate`; execution requires a supported lifecycle boundary")
                    return 0
                self.gated.add(head)
                if red:
                    self.fix_round(f"gate `{red[0]}` exited {red[1]}; the failure in its log:\n{red[2]}", records)
                    continue
            inputs = validation.check_inputs(self, head)
            if inputs == 75:
                say("waiting for validation: execution requires a supported lifecycle boundary")
                return 0
            if inputs is not None:
                raise Refused("execution evidence requires clean unchanged inputs after driver gates; preserve and resolve the worktree edits")
            missing = self.missing_execution(results if pending else [])
            if missing:
                finding = "Missing successful execution evidence @ " + head + ": " + ", ".join(
                    f"`{cmd}`" for cmd in missing) + ". Grants, BUILD reports and CI notices are not proof."
                say(finding)
                e.comment_issue(self.n, "**Execution finding**\n\n" + finding + "\n")
                self.fix_round(finding, records, record_ok=True)
                continue
            if self.origin_tip() != head and not (pr is None and self.fresh_review_needed(records, head)):
                if not self.sync_and_push(records):
                    continue
                # the sync may have merged the base: the next pass reads the new head, its gates and its review
                continue
            if pr is None and not self.fresh_review_needed(records, head):
                pr = self.open_pr(head)
            if self.primary:
                if not at_head:
                    self.review(pr, head, records)
                    pr = self.find_pr() if pr else None
                    continue
                last = at_head[-1]
                if needs_fix(last):
                    if same := unchanged_findings(records):  # the fix moved nothing of it: a third round would re-raise it again
                        self.stop_issue("open decision", f"Verification round {last['round']} re-raised unchanged what round {last['round'] - 1} raised, so the fix moved nothing of it:\n\n"
                                        + "\n".join(f"- {sev(f)} {f.get('file')}:{f.get('line')} {f.get('claim')} ({f.get('evidence')})" for f in same)
                                        + f"\n\nRule on the finding: fix it by hand on the branch, or dismiss it with a ruling; then `agent-ready` back in place of `needs-ruling`, and `ouro run {self.n}` verify it as any fix round.", 3)
                    self.fix_round("\n".join(f"- {sev(f)} {f.get('file')}:{f.get('line')} {f.get('claim')} ({f.get('evidence')})"
                                             for f in last["findings"]), records, record_ok=True)
                    continue
            if pr is None:
                pr = self.open_pr(head)
                continue
            if pr["isDraft"]:
                e.write("mark the pull request ready", argv_for("gh", "pr", "ready", str(pr["number"]), "-R", e.slug))
                self.refresh_body(pr, records)
            if self.copilot:
                state = self.copilot_round(pr)
                if state == "running":
                    return 0
                if state == "fix":
                    continue
            state = self.checks(pr, head)
            if state == "running":
                say(f"checks on #{pr['number']} are still running: invoke `run {self.n}` again")
                return 0
            if state == "fix":
                continue
            if state != "conflict":
                state = self.decide(pr, head, records)
            if state != "conflict":
                return state
            self.sync_and_push(records)
        raise DriverError("the run did not converge in 40 passes")

    def fresh_review_needed(self, records, head):
        """Express reviews the local range before any push: the draft opens at convergence."""
        return self.flow == "express" and bool(self.primary) and not any(r["sha"] == head and not needs_fix(r) for r in records)

    def origin_tip(self):
        out = self.env.git("ls-remote", "--heads", "origin", f"refs/heads/{self.branch}", check=False).out.split()
        return out[0] if out else ""

    def require_acceptance(self):
        problems = acceptance_problems(acceptance_source(self), self.validation_commands())
        if problems:
            self.stop_issue("acceptance unrunnable", "Required acceptance cannot be granted as written:\n\n"
                            + "\n".join("- " + problem for problem in problems)
                            + "\n\nName a single exact runner command without shell operators or environment prefixes. "
                              "A trusted triage/spec amendment may use the explicit Acceptance command field. "
                              "Approved commands blocked only by the environment still use validation_pending.", 3)

    def acceptance_answer(self, ans, name):
        stop = ans.get("stop")
        if stop:
            reason = "acceptance unrunnable" if isinstance(stop, dict) and stop.get("reason") == "acceptance unrunnable" else "open decision"
            self.stop_issue(reason, self.stop_text(stop), 3)
        required = set(acceptance_commands(acceptance_source(self)))
        refused = [str((denial.get("tool_input") or {}).get("command") or "")
                   for denial in ans.get("_denied") or [] if isinstance(denial, dict)]
        if node.USAGE and node.USAGE[-1].get("node") == name:
            refused += [call.get("command") for call in node.USAGE[-1].get("tool_calls", [])
                        if call.get("tool") == "run" and call.get("error")]
        blocked = sorted(required.intersection(refused))
        if blocked:
            self.stop_issue("acceptance unrunnable", "The " + name + " capability boundary refused required acceptance: "
                            + ", ".join(f"`{cmd}`" for cmd in blocked)
                            + ". Supply an admitted exact acceptance command or a genuine approved validation handoff; "
                              "no empty fix round was started.", 3)

    def build_commands(self):
        """(the brief's command list, the commands the node may run): contract section 4's Gates bullet."""
        runnable = acceptance_commands(acceptance_source(self))
        lines = [f"- `{c}` (acceptance)" for c in runnable]
        for run, g in self.area_gates():
            if g.get("cheap") is True:  # whatever the checks run: it needs no toolchain, so every build runs it
                lines.append(f"- `{run}` (cheap: run it on every build)")
                runnable.append(run)
                continue
            paths, here = g.get("paths"), self.locally_run(run, g) or not self.env.pr_checks()
            if (self.notice(g) and self.env.pr_checks()) or not (here or paths):
                continue
            when = f" (run it when the change touches: {', '.join(paths)})" if paths and not here else ""
            lines.append(f"- `{run}`{when}")
            runnable.append(run)
        return "\n".join(lines) or "(none: the pull request's checks are the run)", runnable

    def required_execution(self):
        """Recompute obligations from trusted grants and final changed paths, including resumed heads."""
        commands = acceptance_commands(acceptance_source(self))
        gates = getattr(self, "execution_grants", None)
        if gates is None:
            gates = execution.grants(self)
        for cmd, gate in [*gates, *self.area_gates()]:
            paths = gate.get("paths")
            if gate.get("cheap") is True or ((not self.notice(gate) or not self.env.pr_checks())
                    and (not paths or self.touches(paths))
                    and (not self.env.pr_checks() or self.locally_run(cmd, gate) or paths)):
                commands.append(cmd)
        return list(dict.fromkeys(commands))

    def missing_execution(self, validated=()):
        head = self.head()
        # Private worker observations/candidates do not establish settled command
        # completion. Only results returned by the leased validator count here.
        proven = {r["cmd"] for r in [*validated, *getattr(self, "execution_results", [])]
                  if r["sha"] == head and type(r["exit"]) is int and r["exit"] == 0}
        return [cmd for cmd in self.required_execution() if cmd not in proven]

    def validation_commands(self):
        """Approval comes from the invoking binding, never from a BUILD-edited branch binding."""
        here = not self.env.pr_checks()  # no workflow names pull_request: the BUILD brief grants every selected gate
        return acceptance_commands(acceptance_source(self)) + [run for run, g in self.area_gates(tree=False) if g.get("cheap") is True
                or (here or not self.notice(g)) and (here or self.locally_run(run, g) or g.get("paths"))]

    def build(self, findings=None, threads=None, track=True, record_ok=False):
        """Ask the BUILD node and commit what it left; None when a Copilot round declined every thread and changed nothing.

        `record_ok`: a review's fix round may change no file, since its findings may be the record's sentences; any other build must change the tree."""
        e = self.env
        if not self.in_lane:
            self.require_acceptance()
        cmds, gates = self.build_commands()
        previous = getattr(self, "execution_grants", None)
        if previous is None and self.commits_past_base():
            previous = execution.grants(self)
            if previous is None:
                previous = list(self.area_gates(tree=False))
        self.execution_grants = list({json.dumps(row, sort_keys=True): row
                                      for row in [*(previous or []), *self.area_gates()]}.values())
        if not self.in_lane and not gates:
            self.stop_issue("gate uncovered", empty_list(self.body), 3)
        contract = briefs.contract_sections(e, (4, 7))
        prompt = briefs.render("build", number=self.n, title=self.title, body=self.body, part=self.part_scope("build"),
                               comments=self.spec_comments(), manual=self.manual_block(), anchors=briefs.anchors_block(self.worktree, self.body),
                               implement_docs=briefs.implement_docs(e, self.worktree), flow=self.flow,
                               commands=cmds, findings=findings or "(none)", contract=contract)
        from . import recovery
        prompt += recovery.brief(self)
        tools = node.builder(gates)
        model = self.build_model()
        say(f"[4] BUILD node ({model})" + (" fix round" if findings else ""))
        ans = node.run_node("BUILD", prompt, model, tools, str(self.worktree), node.TIMEOUTS["BUILD"], runtime=e.runtime)
        observations = (node.USAGE[-1].get("tool_calls", []) if node.USAGE
                        and node.USAGE[-1].get("node") == "BUILD" else [])
        self.acceptance_answer(ans, "BUILD")
        pending = validation.requests(ans.get("validation_pending", []), self.validation_commands(), ans.get("gates_run") or [])
        if not self.in_lane and self.commits_past_base():
            combined = {p["cmd"]: p for p in pending}
            for p in self.build_facts(evidence=False)["validation_pending"]:
                combined.setdefault(p["cmd"], p)
            pending = validation.requests(list(combined.values()), self.validation_commands())
        if pending and self.in_lane:
            raise DriverError("direct lane does not support pending validation; nothing committed or pushed")
        subject = str(ans.get("subject") or "").strip()
        if not subject:
            raise DriverError("node BUILD answered no subject")
        steps = self.manual_steps(ans)
        if not self.wt_git("status", "--porcelain", "-z").out:
            if threads and ans.get("replies"):
                self.last_build = ans
                return None
            if not (record_ok or steps):
                raise DriverError("node BUILD left no change in the tree")
            say("  no file changed: the record rides an empty commit" + (", with the manual steps" if steps else ""))
        msg = subject + "\n\n" + without_build_lines(str(ans.get("body") or "")) + "\n" + (f"\nFixes #{self.n}\n" if self.in_lane else "")
        facts = {"door": ans.get("door") or "", "blast_radius": ans.get("blast_radius") or "", "residue": ans.get("residue") or [],
                 "gates_run": [g for g in ans.get("gates_run") or [] if isinstance(g, dict) and g.get("cmd")],
                 "manual": steps, "replies": self.thread_replies(ans, threads) if threads else []}
        if pending:  # an answer without the field keeps the footer it always wrote
            facts["validation_pending"] = pending
        if self.in_lane:
            msg = re.sub(r"(?m)^Ouro-Direct: .*\n?", "", msg)
            msg += (self.lane_build_record if hasattr(self, "lane_build_record") else
                    self.lane_record(*getattr(self, "lane_build", ("parent", None))))
        msg += f"\n{BUILD_LINE}{json.dumps(facts)}\n"
        f = e.body_file("commit-msg.txt", msg)
        inputs = execution.fingerprint(self.worktree)
        latest = {call.get("command"): call for call in observations if call.get("tool") == "run"}
        recheck = [cmd for cmd, call in latest.items() if cmd in gates and inputs
                   and call.get("inputs") == inputs and not call.get("error")
                   and type(call.get("exit")) is int and call["exit"] == 0]
        self.wt_git("add", "-A")
        self.wt_git("commit", "-q", "--allow-empty", "-F", f)
        execution.retain(self, [], recheck=recheck)
        self.committed_here.add(self.head())
        self.last_build = ans
        say(f"  committed: {subject}")
        if findings is None and track:
            self.rederive()
        return self.head()

    def rederive(self):
        e = self.env
        gate_files = set(e.ob.gate_paths({"gate": self.gate_entries()}))
        derived = derive_flow(self.changed_paths(), gate_files)
        new = tighter(self.flow, derived)
        if new != self.flow:
            self.add_note(f"Flow tightened from {self.flow} to {new} by the diff's paths.")
            self.flow = new
        if self.size == "S":
            numstat = self.wt_git("diff", "--numstat", "--no-renames", "-z", f"origin/{e.default}...HEAD").out
            lines = sum(int(a) + int(d) for a, d, _ in (x.split("\t", 2) for x in numstat.split("\0") if x) if a.isdigit() and d.isdigit())
            size, why = derive_size(self.changed_paths(), lines)
            if size != "S":
                self.add_note(f"Size tightened from S to {size}: the diff has {why}.")
                self.size = size
                self.primary, self.copilot = review_plan(e.lookup("ship.review"), size)
            elif self.primary == "reader" and self.docs_only(derived):  # nothing a reader's probe could run
                self.add_note("The diff is docs-only (no path the driver reads as executable), so the external reader is skipped: Review: none.")
                self.primary, self.reader_skipped = None, True

    def docs_only(self, derived):
        """A diff the driver would grade express whose every path is a docs file: the external reader has nothing to probe in it."""
        paths = self.changed_paths()
        return derived == "express" and bool(paths) and all(p.lower().endswith(DOCS_EXT) for p in paths)

    def add_note(self, sentence):
        say(f"  {sentence}")
        self.notes.append(sentence)

    def fix_round(self, findings, records, record_ok=False):
        """A fix is a commit past the first build's, so the rounds already taken are read from the branch."""
        done = max(0, int(self.wt_git("rev-list", "--count", "--no-merges", f"origin/{self.env.default}..HEAD").out.strip() or 0) - 1)
        if done >= FIX_CAP:
            pr = self.find_pr()
            where = (f"the branch `{self.branch}` and pull request #{pr['number']} stay as they are, with the review record. A fix commit pushed to the branch"
                     if pr else f"the branch `{self.branch}` stays in its worktree. A fix commit on the branch")
            self.stop_issue("review cap", "Three fix rounds are spent and the change is not clean.\n\n" + findings +
                            f"\n\nThe hand-off: {where}, `agent-ready` back in place of `needs-ruling`, and `ouro run {self.n}` verify it as any fix round.", 3)
        self.build(findings=findings, record_ok=record_ok)
        if getattr(self, "reader_skipped", False):  # a fix with no reviewer (a red gate or check) may add what a reader probes
            gate_files = set(self.env.ob.gate_paths({"gate": self.gate_entries()}))
            if not self.docs_only(derive_flow(self.changed_paths(), gate_files)):
                self.add_note("A fix added a path the driver reads as executable, so the external reader is back on.")
                self.primary, self.reader_skipped = "reader", False

    # ---- gates
    def gate_entries(self, tree=True):
        """The [[gate]] entries of the binding at the tree's HEAD, as CI reads them; the main checkout's when `tree` is off, or there is no worktree yet (a dry run), or the commit has none."""
        r = self.wt_git("show", "HEAD:.claude/ouro.toml", check=False) if tree and self.worktree.is_dir() else None  # the commit's, not the file's: a sync conflict leaves markers in the file
        if r is None or r.code != 0:
            return list(self.env.data.get("gate", []))
        try:
            return self.env.ob.tomllib.loads(r.out).get("gate", [])
        except ValueError as x:
            raise DriverError(f"the branch's .claude/ouro.toml cannot be read: {x}")

    def area_gates(self, tree=True):
        """Every [[gate]] the issue's area labels select, `<ouro>` replaced by the plugin root."""
        e = self.env
        root = Path(e.ob.plugin_root() or sh.PLUGIN).as_posix()
        for g in self.gate_entries(tree):
            ga = g.get("areas", [])
            if "*" in ga or set(self.areas) & set(ga):
                yield str(g.get("run", "")).replace(e.ob.OURO_TOKEN, root), g

    @staticmethod
    def locally_run(run, g):
        """A gate the pull request's checks cannot run, so it runs here in every class: the token scan (CI has no list and prints
        a skip), or a `ci` whose printed text starts with skip. A `ci` that only prints anything else is a notice that another
        workflow runs the gate in CI: the checks cover it, and neither the driver nor the builder runs it."""
        return "forbidden-tokens.py" in run or bool(SKIPS.match(str(g.get("ci") or "")))

    @staticmethod
    def notice(g):
        """A `ci` that only prints and does not print a skip: another workflow runs the gate in CI. One that prints and then runs
        a command is the checks' own run."""
        ci = str(g.get("ci") or "")
        return bool(PRINTS.match(ci)) and not SKIPS.match(ci) and not re.search(r"&&|;|\|", ci)

    def runs_here(self, run, g, flow):
        """Whether the driver runs a selected gate itself: one the checks only skip, or every one in the direct lane, which has no pull request,
        or where no workflow gives a pull request any check."""
        return self.locally_run(run, g) or flow == "direct" or not self.env.pr_checks()

    def select_gates(self, flow):
        """(gates to run here, gates skipped for their paths): contract section 4's rule per class."""
        chosen, skipped = [], []
        for run, g in self.area_gates():
            gp = g.get("paths")
            if gp and not self.touches(gp):
                skipped.append(run)
            elif self.runs_here(run, g, flow):
                chosen.append((run, g))
        return chosen, skipped

    def checked_gates(self):
        """The selected gates the pull request's checks run, each as CI runs it (`ci` where declared, else `run` as bound): what the record names where the driver ran none."""
        return [str(g.get("ci") or g.get("run") or "") for run, g in self.area_gates()
                if not self.runs_here(run, g, self.flow) and not (g.get("paths") and not self.touches(g["paths"]))]

    def touches(self, entries):
        r = self.wt_git("diff", "--quiet", "--no-ext-diff", "--no-textconv",
                        f"origin/{self.env.default}...HEAD", "--", *entries, check=False)
        return r.code != 0

    def run_gates(self, head):
        chosen, skipped = self.select_gates(self.flow)
        if not getattr(self, "in_lane", False):
            return self.settled_gates(head, chosen, skipped)
        # The direct lane retains its existing driver gates and does not accept
        # worker/caller execution evidence for the normal BUILD proof barrier.
        rec_gates = []
        for i, (run, _) in enumerate(chosen):
            argv = shlex.split(run)
            exe = shutil.which(argv[0]) if argv else None
            if not exe:
                self.stop_issue("gate uncovered", f"The driver cannot run the gate `{run}`: `{argv[0] if argv else ''}` is not on PATH.", 3)
            argv[0] = exe
            r = self.env.run(argv, cwd=self.worktree)
            log = self.env.scratch / f"gate-{i}.log"
            sh.write_bytes(log, r.out + r.err)
            say(f"[5] gate `{run}`: exit {r.code}")
            rec_gates.append({"cmd": run, "exit": r.code, "sha": head})
            if r.code != 0:
                self.gate_log = rec_gates
                return (run, r.code, failure_extract(r.out + r.err))
        self.gate_log = rec_gates
        self.skipped = skipped
        return None

    def settled_gates(self, head, chosen, skipped):
        """Use the existing bounded validator for every final BUILD execution proof.

        Binding-selected driver gates retain their existing authority. Worker-only
        candidates require explicit caller consent and the invoking allowlist;
        observing a worker command never widens caller permissions.
        """
        self.execution_results, self.gate_log, self.skipped = [], [], skipped
        caller = {r["cmd"]: r for r in getattr(self, "caller_execution_results", [])
                  if r["sha"] == head and type(r["exit"]) is int and r["exit"] == 0}
        selected = list(dict.fromkeys(cmd for cmd, _ in chosen))
        automatic = [cmd for cmd in selected if cmd not in caller]
        for command in automatic:
            argv = shlex.split(command)
            if not argv or not shutil.which(argv[0]):
                self.stop_issue("gate uncovered", f"The driver cannot run the gate `{command}`: `{argv[0] if argv else ''}` is not on PATH.", 3)
        if chosen or self.required_execution():
            try:
                validation.check_platform()
            except DriverError as error:
                say(str(error))
                return 75
        for cmd in selected:
            if cmd in caller:
                self.gate_log.append(caller[cmd])
                say(f"[5] gate `{cmd}`: exact-head validation receipt, evidence {caller[cmd]['log']}")

        def batch(commands, *, execute_missing):
            pending = [{"cmd": cmd, "reason": "final committed-input execution evidence"} for cmd in commands]
            validation.requests(pending, commands)
            results, failure = validation.validate(self, pending, reuse=True, execute_missing=execute_missing)
            self.execution_results.extend(results)
            self.gate_log.extend(results)
            for result in results:
                say(f"[5] gate `{result['cmd']}`: exit {result['exit']}")
            if failure in (None, 75):
                return failure
            failed = results[-1]
            tail = failure_extract(Path(failed["log"]).read_text(encoding="utf-8", errors="replace"))
            return failed["cmd"], failure, tail

        # These are exactly the gates the normal driver already chose to execute.
        if automatic:
            failed = batch(automatic, execute_missing=True)
            if failed is not None:
                return failed
        proven = {*caller, *(r["cmd"] for r in self.execution_results if r["exit"] == 0)}
        missing = [cmd for cmd in self.required_execution() if cmd not in proven]
        candidates = set(execution.candidates(self))
        allowed = set(self.validation_commands())
        # Unobserved/malformed/failed commands still take the bounded finding path.
        # A complete set of observed candidates can instead reuse genuine proof,
        # or wait for the explicit --validate consent to execute them as caller.
        if missing and all(cmd in allowed for cmd in missing):
            observed = all(cmd in candidates for cmd in missing)
            failed = batch(missing, execute_missing=observed and bool(getattr(self.env, "validate", False)))
            # Existing genuine proof survives loss of worker-candidate metadata.
            # Absent proof without observed candidates still needs a BUILD fix.
            return None if failed == 75 and not observed else failed
        return None

    # ---- push and the pull request
    def sync_and_push(self, records):
        e = self.env
        r = self.env.run(script("Sync-Branch.ps1"), cwd=self.worktree)
        if r.code != 0:
            merging = self.wt_git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).code == 0
            if not merging:
                raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}")
            if self.sync_tried:
                self.wt_git("merge", "--abort", check=False)
                self.stop_issue("open decision", f"The branch conflicts with `{e.default}` and the second attempt did not resolve it.\n\n{r.text}", 3)
            self.sync_tried = True
            say("[6] sync conflict: the BUILD node gets it as a finding, once")
            conflicted = [x for x in self.wt_git("diff", "--name-only", "--diff-filter=U", "-z").out.split("\0") if x]
            self.build(findings=f"Resolve this merge conflict with `origin/{e.default}` in the tree and leave no markers:\n{r.text}")
            left = [x for x in conflicted if re.search(r"(?m)^<{7} ", (self.worktree / x).read_text(encoding="utf-8", errors="replace"))]
            if left:
                self.stop_issue("open decision", "The resolution still holds conflict markers in: " + ", ".join(left), 3)
            return False
        lease = re.search(r"(?m)^lease: (\S+)\s*$", r.out)  # Sync-Branch's own line, not a merged commit subject
        args = ["-Lease", lease.group(1)] if lease else []
        p = self.env.run(script("Push-Branch.ps1", *args), cwd=self.worktree)
        if p.code != 0:
            raise DriverError(f"{sh.cmdline(p.argv)} failed (exit {p.code}): {p.text}")
        say("[6] pushed " + self.branch)
        return True

    def open_pr(self, head):
        e = self.env
        subject = self.wt_git("log", "-1", "--format=%s", self.first_commit()).out.strip()
        rec = {"subject": subject, "flow": self.flow, "part_line": f"Part {self.k}/{self.parts_n} of #{self.n}" if self.k else "",
               "fixes_line": f"Fixes #{self.n}" if self.fixes else "", **self.build_facts(),
               "changes": self.wt_git("log", "--reverse", "--format=%s", f"origin/{e.default}..HEAD").out.strip().splitlines(),
               "gates": getattr(self, "gate_log", []), "skipped": getattr(self, "skipped", []), "checked": self.checked_gates(),
               "note": " ".join(self.notes), "usage": node.usage_table()}
        review = review_line(self.pending, bool(self.primary))
        f = e.body_file("pr-body.md", drawn_body(rec, review))
        argv = argv_for("gh", "pr", "create", "-R", e.slug, "--base", e.default, "--head", self.branch, "--draft",
                        "--title", subject, "--body-file", f)
        for a in self.areas:
            argv += ["--label", a]
        r = e.write("open the draft pull request", argv)
        pr = self.find_pr()
        if pr is None:
            raise DriverError(f"gh pr create printed {r.out.strip()!r} but no pull request reads back for {self.branch}")
        say(f"[6] draft pull request #{pr['number']}")
        for body in self.pending:
            e.comment_pr(pr["number"], body["body"])
        self.pending = []
        return pr

    def commit_facts(self, sha):
        """What the BUILD node answered that a later invocation needs, from the `Ouro-Build:` line of one commit, checked
        once here: a person can reword a commit, and what reads these lists trusts their shape."""
        for line in reversed(self.wt_git("log", "-1", "--format=%B", sha).out.splitlines()):
            if line.startswith(BUILD_LINE):
                try:
                    facts = json.loads(line[len(BUILD_LINE):])
                except ValueError as ex:
                    raise DriverError(f"the Ouro-Build line of {sha[:7]} is not JSON: {ex}")
                if not isinstance(facts, dict):
                    raise DriverError(f"the Ouro-Build line of {sha[:7]} is not a JSON object: reword that commit's message")
                for key, need in (("gates_run", "cmd"), ("manual", "path"), ("replies", "comment_id"), ("residue", None)):
                    v = facts.get(key) or []
                    if not isinstance(v, list) or (need and not all(isinstance(x, dict) and x.get(need) for x in v)):
                        raise DriverError(f"the Ouro-Build line of {sha[:7]} holds a malformed `{key}`"
                                          + (f", whose every entry is an object with `{need}`" if need else ", which is a list")
                                          + ": reword that commit's message")
                return facts
        return {}

    def build_facts(self, *, evidence=True):
        """The record's door, blast, summary, residue and build_gates: the newest BUILD answer on the branch, which describes the whole branch; its manual steps, the union over the branch's answers."""
        # Metadata-only callers must not collect receipt/state evidence before validation owns a lease.
        shas = self.wt_git("rev-list", f"origin/{self.env.default}..HEAD").out.split()
        facts = [(s, self.commit_facts(s)) for s in shas]
        sha, f = next(((s, x) for s, x in facts if x), (shas[-1], {}))  # no facts anywhere: the first commit's body
        manual = {}
        pending = {}
        for _, x in reversed(facts):  # oldest first: a later answer's edit replaces an earlier one, a later silence keeps it
            newest = {p["cmd"]: p for p in validation.requests(x.get("validation_pending", []), self.validation_commands(),
                                                               x.get("gates_run") or [], inherited=pending)}
            pending = {**newest, **{cmd: p for cmd, p in pending.items() if cmd not in newest}}
            for m in x.get("manual") or []:
                manual[m["path"]] = {"path": m["path"], "edit": str(m.get("edit") or "") or manual.get(m["path"], {}).get("edit", "")}
        requested = list(pending.values())
        return {"door": str(f.get("door") or "") or "unstated", "blast": str(f.get("blast_radius") or "") or "unstated",
                "summary": without_build_lines(self.wt_git("log", "-1", "--format=%b", sha).out),
                "residue": [str(x) for x in f.get("residue") or []], "build_gates": f.get("gates_run") or [], "manual": list(manual.values()),
                "validation_pending": requested, "validation_results": validation.receipt(self, requested) if requested and evidence else [],
                "validation_attempts": validation.attempts(self) if requested and evidence else [],
                "execution_commands": sorted(execution.proven(self)) if evidence else [],
                "execution_head": shas[0], "execution_evidence": str(execution.directory(self) / (shas[0] + ".json")) if evidence else ""}

    def first_commit(self):
        return self.wt_git("rev-list", "--reverse", f"origin/{self.env.default}..HEAD").out.split()[0]

    def refresh_body(self, pr, records):
        e = self.env
        body = e.gh_json("pr", "view", str(pr["number"]), "--json", "body")["body"]
        rec = read_record(body)
        if not rec:
            return
        rec.update(self.build_facts())
        rec["gates"] = getattr(self, "gate_log", None) or rec.get("gates", [])
        rec["skipped"], rec["checked"] = getattr(self, "skipped", rec.get("skipped", [])), self.checked_gates()
        rec["changes"] = self.wt_git("log", "--reverse", "--format=%s", f"origin/{e.default}..HEAD").out.strip().splitlines()
        rec["flow"] = self.flow
        rec["usage"] = node.usage_table() or rec.get("usage", "")
        rec["note"] = " ".join([rec.get("note", "")] + [s for s in self.notes if s not in rec.get("note", "")]).strip()
        f = e.body_file("pr-body.md", drawn_body(rec, review_line(records, bool(self.primary)), body))
        e.write("update the pull request body", argv_for("gh", "pr", "edit", str(pr["number"]), "-R", e.slug, "--body-file", f))

    # ---- review
    def review(self, pr, head, records):
        e = self.env
        rnd = max([r["round"] for r in records], default=0) + 1
        ans = self.reader_round(rnd, head, records) if self.primary == "reader" else None
        if ans is None:
            ans = self.node_review(pr, head, records, rnd)
        self.acceptance_answer(ans, "REVIEW")
        ans["validation_results"] = self.build_facts()["validation_results"]
        ans["validation_attempts"] = self.build_facts()["validation_attempts"]
        body = review_comment(rnd, ans, head)
        rec = {"round": rnd, "verdict": body.split("\n", 1)[0].split(": ")[1].split(" ")[0], "sha": head, "body": body,
               "findings": ans.get("findings") or [], "false": ans.get("false_record_sentences"), "reader": ans.get("reader")}
        if pr:
            e.comment_pr(pr["number"], body)
            self.refresh_body(pr, records + [rec])
        else:
            self.pending.append(rec)

    def verification(self, records, head):
        """The REVIEW brief's verification block: the fix's range and whole diff, or no scope when the diff cannot be shown whole."""
        if not records:
            return ""
        since, prior = records[-1]["sha"], records[-1]["body"]
        ruled = records[-1].get("ruled")  # an approver ruled those findings: the brief shows the range, not the findings
        r = self.wt_git("diff", f"{since}..{head}", check=False)
        if r.code != 0 or len(r.out) > 60000:
            if ruled:
                return f"The diff `{since}..{head}` cannot be shown whole, so leave `scope` empty on every finding."
            return (f"This verifies a fix against the findings it answers. Its diff `{since}..{head}` cannot be shown whole, "
                    f"so leave `scope` empty on every finding. The findings it answers:\n\n{prior}")
        if ruled:
            return (f"A fix `{since}..{head}` follows. A finding in a line this diff changed is IN-FIX and one in any other "
                    f"line is OUTSIDE. Its diff:\n\n```diff\n{r.out}\n```")
        return (f"This verifies a fix against the findings it answers. The fix is `{since}..{head}`. A finding below that "
                f"still stands is STANDS wherever its lines are; a new finding is IN-FIX in a line this diff changed and "
                f"OUTSIDE in any other. Its diff:\n\n```diff\n{r.out}\n```\n\nThe findings it answers:\n\n{prior}")

    def node_review(self, pr, head, records, rnd):
        e = self.env
        diff = self.wt_git("diff", f"origin/{e.default}...HEAD").out
        if len(diff) > 150000:
            diff = diff[:150000] + "\n(diff cut at 150000 characters; read the rest from the worktree)"
        rp = briefs.PROMPTS / "REVIEWER-PROMPT.md"
        record = (e.gh_json("pr", "view", str(pr["number"]), "--json", "body")["body"] if pr else "(no pull request yet)")
        record += "\nDriver validation evidence: " + json.dumps(self.build_facts()["validation_results"])
        record += "\nHistorical validation attempts (each at its own head): " + json.dumps(self.build_facts()["validation_attempts"])
        prior = self.verification(records, head)
        prompt = briefs.render("review", mode=MODE.get(self.flow, "sized"), number=self.n, head=head, title=self.title, part=self.part_scope("review"),
                               range=f"origin/{e.default}...{head}", diff=diff, body=self.body, comments=self.spec_comments(), record=record,
                               verification=prior,
                               reviewer_prompt=rp.read_text(encoding="utf-8") if rp.is_file() else "(REVIEWER-PROMPT.md is not in this tree)",
                               contract=briefs.contract_sections(e, (7,)))
        model = node.model(e, "reviewer", "opus")
        say(f"[7] REVIEW node round {rnd} ({model}, {MODE.get(self.flow, 'sized')})")
        tools, denied, cwd, copy = node.READ, None, self.worktree, None
        if self.flow in ("standard", "full"):
            copy = e.scratch / f"review-{rnd}"
            node.worktree_readiness(e.runtime, e.main, copy, None, head, scratch=e.scratch)
            e.git("worktree", "add", "--detach", str(copy), head)
            tools, denied, cwd = node.PROBE, None, copy
        try:
            if copy:
                self.setup_tree(copy)
            return node.run_node("REVIEW", prompt, model, tools, str(cwd), node.TIMEOUTS["REVIEW"], denied, runtime=e.runtime)
        finally:
            if copy:
                r = e.git("worktree", "remove", "--force", str(copy), check=False)
                if r.code != 0:
                    say(f"  the review worktree {copy} was not removed: {r.text}")

    def reader_round(self, rnd, head, records):
        """One `bin/external-audit.py` round as a review answer; None, with the record saying so, when the reader is unavailable."""
        e = self.env
        cli = e.lookup("ship.external_cli")
        spent = [r for r in records if r.get("reader")]
        if len(spent) >= 2:
            self.add_note("The external reader's two rounds are spent, so the REVIEW node verified the later fixes.")
            return None
        args = ["--cli", cli, "--range", f"origin/{e.default}...{head}", "--goal", self.reader_goal()
                + "\nDriver validation evidence: " + json.dumps(self.build_facts()["validation_results"])
                + "\nHistorical validation attempts (each at its own head): " + json.dumps(self.build_facts()["validation_attempts"])]
        if e.lookup("ship.external_model"):
            args += ["--model", e.lookup("ship.external_model")]
        if e.lookup("ship.external_effort"):
            args += ["--effort", e.lookup("ship.external_effort")]
        if spent:
            fix_sha, fix_subject = self.wt_git("log", "-1", "--no-merges", "--format=%H%n%s", head).out.strip().split("\n", 1)
            digest = "\n\n".join(f"{f.get('claim')}\n  the fix commit {fix_sha[:7]}: {fix_subject}" for f in spent[-1]["findings"]) or "no findings"
            args += ["--resume", spent[-1]["reader"]["session"], "--digest", e.body_file("reader-digest.md", digest)]
        say(f"[7] external reader round {len(spent) + 1} ({cli})")
        for _ in range(2):
            r = self.env.run(script("external-audit.py", *args), cwd=self.worktree, timeout=700, env=worker_env(e.runtime))
            if r.code != 3:
                break
        out = re.search(r"external-audit: session (\S+) round (\d+) -> (.+)", r.out)
        sess = re.search(r"--resume (\S+) --digest", r.out)
        verdict = Path(out.group(3).strip()) / "verdict.json" if out else None
        found = json.loads(verdict.read_text(encoding="utf-8")) if verdict and verdict.is_file() else None
        stop = found.get("stop") if isinstance(found, dict) else None
        if stop is not None and (not isinstance(stop, dict) or stop.get("reason") != "acceptance unrunnable"
                                 or not isinstance(stop.get("question"), str) or not stop["question"].strip()):
            raise DriverError("the configured external reader returned a malformed acceptance stop; no fix round or replacement review was started")
        if r.code not in (0, 2) or not (found and sess):
            self.add_note(f"The external reader ({cli}) was unavailable (exit {r.code}), so the REVIEW node reviewed in its place.")
            return None
        answer = {"verdict": "CLEAN" if r.code == 0 else "FINDINGS", "findings": reader_findings(found),
                  "files_read": found.get("files_read") or [], "reader": {"cli": cli, "session": sess.group(1), "round": len(spent) + 1}}
        if stop is not None:
            answer["stop"] = stop
        return answer

    def reader_goal(self):
        """What the change must do, for the external reader: the title, the Deliverable section and the newest comments that amend it, each cut on its own so a long one cannot push the others out."""
        want = re.search(r"(?ms)^## Deliverable\s*\n(.*?)(?=^## |\Z)", self.body)
        comments = self.spec_comments()
        cut = " (earlier ones cut)" if len(comments) > 3000 else ""
        acceptance = acceptance_text(self.body)
        commands = "\n".join(f"- `{cmd}`" for cmd in acceptance_commands(acceptance_source(self)))
        return (f"{self.title}. {(want.group(1).strip() if want else '')[:3000]}"
                f"\n\nRequired acceptance (up to 3000 characters): {acceptance[:3000] or '(none)'}"
                f"\n\nApproved acceptance commands (up to 3000 characters): {commands[:3000] or '(none)'}"
                f"\n\nComments that amend it, the newest last{cut}: {comments[-3000:]}")

    # ---- Copilot
    def graphql(self, query, **variables):
        args = ["api", "graphql", "-f", f"query={query}"] + [x for k, v in variables.items() for x in ("-F" if isinstance(v, int) else "-f", f"{k}={v}")]
        return json.loads(self.env.gh(*args).out)["data"]

    def pull_read(self, pr):
        owner, repo = self.env.slug.split("/", 1)
        return self.graphql(COPILOT_STATE_QUERY, o=owner, r=repo, n=pr["number"])["repository"]["pullRequest"]

    def copilot_threads(self, pr):
        """(Copilot's review threads still open and unanswered by a trusted author, as findings; how many a trusted author answered, the mark of a round taken)."""
        owner, repo = self.env.slug.split("/", 1)
        trusted, cursor, out, answered_n = self.env.trusted(), None, [], 0
        while True:
            page = self.graphql(THREADS_QUERY, o=owner, r=repo, n=pr["number"], **({"c": cursor} if cursor else {}))["repository"]["pullRequest"]["reviewThreads"]
            for t in page["nodes"]:
                cs = t["comments"]["nodes"]
                answered = any(str((c.get("author") or {}).get("login") or "").lower() in trusted for c in cs[1:])
                if not (cs and is_copilot((cs[0].get("author") or {}).get("login"))):
                    continue
                if answered:
                    answered_n += 1
                elif not t["isResolved"]:
                    out.append({"id": cs[0]["databaseId"], "path": cs[0].get("path"), "line": cs[0].get("line"), "body": cs[0].get("body")})
            if not page["pageInfo"]["hasNextPage"]:
                return out, answered_n
            cursor = page["pageInfo"]["endCursor"]

    def request_copilot(self, node_id):
        bot = str(self.env.lookup("ship.copilot_bot_id") or "")
        if not (re.fullmatch(r"[\w=-]+", node_id) and re.fullmatch(r"[\w=-]+", bot)):
            raise DriverError(f"cannot request Copilot: the pull request id {node_id!r} or ship.copilot_bot_id {bot!r} is not an id")
        query = ('mutation { requestReviews(input: {pullRequestId: "%s", botIds: ["%s"], union: true}) { pullRequest { reviewRequests(first: 10) '
                 '{ nodes { requestedReviewer { __typename ... on Bot { login } } } } } } }' % (node_id, bot))
        nodes = self.graphql(query)["requestReviews"]["pullRequest"]["reviewRequests"]["nodes"]
        if not any((n.get("requestedReviewer") or {}).get("__typename") == "Bot" and is_copilot(n["requestedReviewer"].get("login")) for n in nodes):
            raise DriverError(f"the Copilot request named no Bot reviewer in its own response ({json.dumps(nodes)}): ship.copilot_bot_id {bot!r} is probably not Copilot's")
        say("[7] Copilot requested through requestReviews")

    def copilot_round(self, pr):
        """'done', 'running' (no review yet at the limit) or 'fix' (one round of Copilot's threads is committed)."""
        e = self.env
        deadline = time.time() + watch_limit()
        while True:
            st = self.pull_read(pr)
            reviews = [r for r in st["reviews"]["nodes"] if is_copilot((r.get("author") or {}).get("login"))]
            if reviews:
                break
            if not any(is_copilot(((n.get("requestedReviewer") or {}).get("login"))) for n in st["reviewRequests"]["nodes"]):
                self.request_copilot(st["id"])
            elif time.time() >= deadline:
                say(f"Copilot's review of #{pr['number']} is still pending: invoke `run {self.n}` again")
                return "running"
            else:
                time.sleep(poll_seconds())
        threads, answered = self.copilot_threads(pr)
        if not threads:
            return "done"
        text = ("Copilot's review threads on the pull request, one round only. Fix a valid one in scope, or decline it with the reason; "
                "answer `replies` with one entry per comment_id (\"Fixed in <sha>. ...\" or the reason).\n"
                + "\n".join(f"- comment_id {t['id']} {t['path']}:{t['line']}: {t['body']}" for t in threads))
        said = self.committed_replies()
        if all(t["id"] in said for t in threads):
            say(f"[7] Copilot round: the commits since its review already answer {len(threads)} open thread(s)")
            self.post_replies(pr, [(t["id"], said[t["id"]]) for t in threads])
            return "done"
        if said or answered:  # one round per pull request: a thread Copilot opens after it, or left after a round that declined, gets a reply, not a build
            say(f"[7] Copilot round: spent; {len(threads)} later thread(s) answered in a reply")
            self.post_replies(pr, [(t["id"], said.get(t["id"], ONE_ROUND)) for t in threads])
            return "done"
        say(f"[7] Copilot round: {len(threads)} open thread(s)")
        if self.build(findings=text, threads=threads) is None:
            self.post_replies(pr, [(r["comment_id"], r["reply"]) for r in self.thread_replies(self.last_build, threads)])
            return "done"
        return "fix"

    @staticmethod
    def thread_replies(ans, threads):
        """One reply per thread: the build's own, else a line saying it gave none."""
        said = {int(r.get("comment_id")): str(r.get("reply") or "") for r in (ans.get("replies") or []) if str(r.get("comment_id")).isdigit()}
        return [{"comment_id": t["id"], "reply": said.get(t["id"]) or NO_REPLY} for t in threads]

    def committed_replies(self):
        """{comment id: reply} from the `Ouro-Build:` lines of the branch's commits, `<sha>` filled with that commit's; the newest wins."""
        said = {}
        for sha in self.wt_git("rev-list", "--reverse", f"origin/{self.env.default}..HEAD").out.split():
            for item in self.commit_facts(sha).get("replies") or []:
                said[int(item["comment_id"])] = str(item["reply"]).replace("<sha>", sha[:7])
        return said

    def post_replies(self, pr, pairs):
        e = self.env
        for cid, reply in pairs:
            f = e.body_file(f"reply-{cid}.md", reply)
            e.write(f"reply in thread {cid}", argv_for("gh", "api", f"repos/{e.slug}/pulls/{pr['number']}/comments/{cid}/replies", "-X", "POST", "-F", f"body=@{f}"))

    # ---- checks
    def watch_pr(self, n, head, cwd):
        """The watcher on #n, run again past the limit while the head's pull_request runs, the ones the watcher
        waits for, still move (one appeared, changed status or concluded): a limit in which they did not, or
        four re-watches, ends it with the watcher's timed-out result."""
        snap = lambda: self.env.ghr("run", "list", "--commit", head, "--event", "pull_request", "--json", "databaseId,status,conclusion", check=False).out
        seen = snap()
        for k in range(5):
            r = self.env.run(script("Watch-PullRequestChecks.ps1", "-Pr", str(n)), cwd=cwd, timeout=watch_limit())
            if r.code is not None or k == 4:
                return r
            now = snap()
            if now == seen:
                return r
            seen = now
            say(f"  the runs on #{n} moved within the watch limit and still run: watching on ({k + 1} of 4 more)")

    def checks(self, pr, head):
        e = self.env
        n = pr["number"]
        if not e.pr_checks():
            say(f"[8] checks: skipped, no workflow at origin/{e.default} names pull_request")
            return "none"
        deadline = time.time() + watch_limit()  # GitHub can serve the old head minutes after a push, and the watcher reads its head there
        while (now := e.gh_json("pr", "view", str(n), "--json", "headRefOid").get("headRefOid")) != head:
            if time.time() >= deadline:
                say(f"[8] #{n}'s head on GitHub still reads {now}, not the pushed head {head}")
                return "running"
            time.sleep(poll_seconds())
        r = self.watch_pr(n, head, self.worktree)
        if r.code is None:
            return "running"
        if r.code == 0:
            say(f"[8] checks on #{n} are green")
            return "green"
        if re.search(r"is still \w+, with no conclusion yet", r.text):
            return "running"
        if "conflicts with" in r.text:
            return self.conflicted(head, 8, "the checks")
        bad = re.findall(r"(\d+):(failure|cancelled|timed_out|action_required|startup_failure|stale)", r.text)
        if not bad:
            raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}")
        retried = {}  # run id -> the attempt its re-run starts
        for run_id, _ in bad:
            attempt = e.gh_json("run", "view", run_id, "--json", "attempt").get("attempt", 1)
            self.ci_ran(run_id, attempt)
            if attempt == 1:
                e.write(f"re-run failed jobs of run {run_id} (a flake is a failure whose re-run succeeds)",
                        argv_for("gh", "run", "rerun", run_id, "-R", e.slug, "--failed"))
                retried[run_id] = attempt + 1
        if retried and not e.dry:
            r2 = self.watch_pr(n, head, self.worktree)
            if r2.code is None:
                return "running"
            # a flake only when each re-run attempt itself concluded success, read by its number: the watcher reads the
            # run list's record, which can lag the attempt it names
            got = {run_id: self.attempt_of(run_id, k) for run_id, k in retried.items()}
            if any(a.get("status") != "completed" for a in got.values()):
                return "running"
            for run_id, k in retried.items():
                self.ci_ran(run_id, k)
            if r2.code == 0 and all(a.get("conclusion") == "success" for a in got.values()):
                say("[8] the failed check passed on re-run: a flake")
                return "green"
            if r2.code == 0:
                say("[8] the watcher read the re-run green, but its attempt concluded "
                    + ", ".join(f"{a.get('conclusion')} (run {i}, attempt {retried[i]})" for i, a in got.items() if a.get("conclusion") != "success")
                    + ": no flake")
        log = e.gh("run", "view", bad[0][0], "-R", e.slug, "--log-failed", check=False).text
        self.fix_round(f"check run {bad[0][0]} failed; the failure in its log:\n" + failure_extract(log), self.records(pr))
        return "fix"

    def attempt_of(self, run_id, k):
        """Attempt k of a workflow run, as the API records it: its status and conclusion."""
        try:
            return json.loads(self.env.gh("api", f"repos/{self.env.slug}/actions/runs/{run_id}/attempts/{k}").out)
        except ValueError:
            raise DriverError(f"the API answered no JSON for attempt {k} of run {run_id}")

    def ci_ran(self, run_id, k):
        """Stop as infrastructure, `gate uncovered`, when attempt k of a run failed without running: a startup failure, or a
        failed job that never started (an Actions budget, a runner never acquired). A re-run and a fix round would both
        wait on CI that cannot run."""
        e = self.env
        attempt = self.attempt_of(run_id, k)
        try:
            jobs = json.loads(e.gh("api", f"repos/{e.slug}/actions/runs/{run_id}/attempts/{k}/jobs?per_page=100").out).get("jobs", [])
        except ValueError:
            raise DriverError(f"the API answered no JSON for the jobs of attempt {k} of run {run_id}")
        # a failure, not a cancel: a matrix's fail-fast cancels the legs still queued behind a real failure, and those
        # have no steps and no runner either
        never = [j.get("name") for j in jobs if j.get("conclusion") == "failure" and not j.get("steps") and not j.get("runner_name")]
        if attempt.get("conclusion") == "startup_failure" or never:
            what = f"its job(s) {', '.join(map(str, never))} never started" if never else "the run failed to start"
            self.stop_issue("gate uncovered", f"CI could not run: attempt {k} of run {run_id} on #{self.n}'s pull request failed without "
                            f"running, {what} (an Actions budget, a runner never acquired, or a workflow that cannot start). This is no "
                            f"flake and no fix round: restore CI, then run again.", 3)

    def conflicted(self, head, step, where):
        """The branch conflicts with the base at `head`, says `where`: synced once, as a person would; the same head again is an error."""
        if head in self.synced:
            raise DriverError(f"#{self.n}'s branch conflicts with {self.env.default} at {head[:7]} and the sync changed nothing ({where})")
        self.synced.add(head)
        say(f"[{step}] the branch conflicts with {self.env.default} ({where}): synced, then the pass repeats")
        return "conflict"

    # ---- merge decision
    def decide(self, pr, head, records):
        e = self.env
        n = pr["number"]
        policy = e.lookup("ship.policy")
        classes = e.lookup("ship.unattended_classes")
        view = e.gh_json("pr", "view", str(n), "--json", "reviewDecision,headRefOid" + ("" if e.pr_checks() else ",mergeable"))
        last = [r for r in records if r["sha"] == head]
        clean = (not self.primary) or (bool(last) and not needs_fix(last[-1]))
        cs = e.gh_json("pr", "checks", str(n), "--json", "name,bucket") if e.pr_checks() else []
        n_green = len([c for c in cs if c.get("bucket") == "pass"]) if e.pr_checks() else None
        red = [f"{c.get('name')} ({c.get('bucket')})" for c in cs if c.get("bucket") not in ("pass", "skipping")]
        if red:  # the watcher read the checks green; the head says otherwise (a pending one may never finish), so nothing is posted or merged
            raise DriverError(f"#{n} has checks that are not green at {head}: {', '.join(red)}; nothing posted or merged")
        line = review_line(records, bool(self.primary))
        checks = f"{n_green} green" if e.pr_checks() else "none, no workflow names pull_request"
        facts = f"Head: {head}\nChecks: {checks}\n{line}\nClass: {self.flow}\nPolicy: {policy}"
        if last and last[-1].get("ruled"):
            facts += f"\nRuled: {last[-1]['ruled']}, round {last[-1]['round']}"
        if view.get("reviewDecision") == "CHANGES_REQUESTED":
            say(f"waiting: a review on #{n} requests changes")
            return 0
        if view.get("headRefOid") != head:
            say(f"waiting: #{n}'s head on GitHub reads {view.get('headRefOid')}, not the reviewed head {head}: invoke `run {self.n}` again")
            return 0
        if view.get("mergeable") == "CONFLICTING":  # where a workflow runs, the watcher reads it
            return self.conflicted(head, 9, "the pull request")
        if not clean:
            raise DriverError(f"the last verdict at {head} is not clean")
        rec = read_record(e.gh_json("pr", "view", str(n), "--json", "body")["body"]) or {}
        steps = rec.get("manual") or []
        if steps or not e.pr_checks() or not may_merge(policy, classes, self.labels, self.flow):
            if not any(c.get("body", "").startswith("**Ready for merge**") and head in c.get("body", "") for c in self.pr_comments(pr)):
                manual = ("Manual steps, applied on the branch before the merge:\n" + "\n".join(f"- `{m.get('path')}`" for m in steps) + "\n\n") if steps else ""
                e.comment_pr(n, f"**Ready for merge**\n\n{manual}{facts}\n")
            say(f"[9] ready for merge: #{n} waits for the owner's merge on GitHub" + (", after the manual steps" if steps else "")
                + ("" if e.pr_checks() else ", since no check ran on it"))
            return 0
        subject = re.sub(r"\s*\(#\d+\)\s*$", "", pr["title"]) + f" (#{n})"
        tail = f"Fixes #{self.n}" if self.fixes else f"Part {self.k}/{self.parts_n} of #{self.n}"
        bf = e.body_file("merge-body.md", (rec.get("summary", "") + "\n\n" + "\n".join(f"- {c}" for c in rec.get("changes", [])) + f"\n\n{tail}\n"))
        sha = self.merge_pr(n, subject, bf, self.worktree, head)
        if sha is None:
            return self.conflicted(head, 9, "the merge")
        e.comment_pr(n, f"**Landed unattended** by ouro run at {sha}: checks {n_green} green, {line}, class {self.flow}" + (f"\n{facts.splitlines()[-1]}" if "\nRuled: " in facts else "") + "\n")
        say(f"[9] merged #{n} as {sha}")
        return self.after_merge(self.find_pr())

    def merge_pr(self, n, subject, body_file, cwd, head):
        """The merge commit; None when the script refuses a CONFLICTING pull request, which the caller syncs. A merge
        the script attempted after which the pull request still read OPEN (the base moved under it, or the platform
        refused it) is tried once more, the head re-read first: the script re-reads everything else before every
        attempt. A second failure quotes both."""
        first = ""
        for attempt in (1, 2):
            r = self.env.run(script("Merge-PullRequest.ps1", "-Pr", str(n), "-Subject", subject, "-BodyFile", body_file, "-Linear", "no"), cwd=cwd)
            if r.code == 0:
                break
            if "'CONFLICTING " in r.text:
                return None
            if attempt == 1 and "reads 'OPEN " in r.text and "not MERGED with a merge commit" in r.text:
                now = self.env.gh_json("pr", "view", str(n), "--json", "headRefOid").get("headRefOid")
                if now != head:
                    raise DriverError(f"#{n}'s head moved during the merge attempt: {now} now, the reviewed head was {head}")
                first = r.text
                say(f"  the merge of #{n} was attempted and did not land: retried once")
                continue
            raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}" + (f"\nthe first attempt: {first}" if first else ""))
        m = re.search(r"merged as ([0-9a-f]{7,40})", r.out)
        return m.group(1) if m else "unknown"

    # ---- after the merge (transitions 10-12)
    def after_merge(self, pr):
        e = self.env
        n = pr["number"]
        sha = (pr.get("mergeCommit") or {}).get("oid") or ""
        say(f"[10] #{n} is merged as {sha}")
        recorded = read_record(pr.get("body") or "").get("flow")
        if recorded in ORDER:
            self.flow = tighter(self.flow, recorded)
        if e.dry:
            say("  would: watch the default branch's runs (full), delete the branch and worktree, refresh the queue's anchors, file follow-ups, tick the part")
            return 0
        e.git("fetch", "origin", check=False)
        claim_ready = True
        if self.flow == "full" and sha:
            r = self.env.run(script("Watch-PullRequestChecks.ps1", "-Sha", sha), cwd=e.main, timeout=watch_limit())
            if r.code not in (0, None):
                raise DriverError(f"the default branch's run on {sha} is not green: {r.text}")
            say("[10] the default branch's runs are green" if r.code == 0 else "[10] the default branch's runs are still running")
            claim_ready = r.code == 0
        if not claim_ready and self.claim.config and os.environ.get(self.claim.config.credential_env):
            self.claim.update(f"merged: {sha}; waiting on default-branch checks")
            return 0  # keep the current checklist part resumable while its claim is open
        if claim_ready:
            self.claim.update(f"landed: {sha}", close=True)
        if self.branch_exists() or self.worktree.exists():
            r = self.env.run(script("Remove-LandedBranch.ps1", "-Pr", str(n)), cwd=e.main)
            if r.code != 0:
                raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}")
            say("[10] branch and worktree removed")
        r = self.env.run(script("Test-AgentReadyAnchors.ps1", "-Ref", f"origin/{e.default}"), cwd=e.main)
        say("[10] queue anchors refreshed" + ("" if r.code == 0 else f" (the gate exited {r.code})"))
        for line in r.out.splitlines():
            if line.startswith("#") or "STALE-ANCHOR" in line:
                say("  " + line.strip())
        self.follow_up(pr, sha)
        self.tick_part()
        say(f"done: #{self.n} landed" + (f" (part {self.k}/{self.parts_n})" if self.k else ""))
        return 0

    def follow_up(self, pr, sha):
        e = self.env
        n = pr["number"]
        rec = read_record(pr.get("body") or "")
        recs = review_records(self.pr_comments(pr), e.trusted(), e.approvers())
        line = lambda f: f"{sev(f)} {f.get('file')}:{f.get('line')} {f.get('claim')} (evidence: {f.get('evidence')})"
        key = lambda f: (str(f.get("file")), str(f.get("line")), " ".join(str(f.get("claim") or "").split()))  # review_line's identity, less the severity
        picked = {}  # one bullet per defect, as the latest round reported it: the newest record's L findings and the later rounds' OUTSIDE ones
        for r in recs:
            for f in r["findings"]:
                if (r is recs[-1] and sev(f) == "L") or (r["round"] > 1 and outside(f)):
                    picked[key(f)] = f
        found = [line(f) for f in picked.values()]
        residue = [x for x in rec.get("residue", []) if not re.search(r"(?i)\b(filed|tracked|see|opened)\b.*#\d+", str(x))]  # filed already
        if not residue and not found:
            return
        areas, types = e.lookup("labels.area", []) or [], e.lookup("labels.type", []) or []
        trusted = e.trusted()  # a comment any other account posts is data, never a list of issues to file
        posted = [str(c.get("body", "")) for c in self.pr_comments(pr) if str(c.get("body", "")).startswith("**Follow-ups filed**")
                  and str((c.get("author") or {}).get("login") or "").lower() in trusted]
        if posted:  # the newest comment records what a failed late write left unfiled, with the items themselves
            items, filed = pending_follow_ups(posted[-1]), filed_follow_ups(posted[-1])
            if not items:
                say("[11] follow-ups already filed")
                return
            say(f"[11] filing the {len(items)} follow-up(s) a failed write left unfiled")
        else:
            bullets = lambda xs: "\n".join(f"- {x}" for x in xs) or "(none)"
            prompt = briefs.render("followup", merge=sha, residue=bullets(residue), findings=bullets(found), contract=briefs.contract_sections(e, (3,)),
                                   labels=json.dumps({"area": areas, "type": types}))
            say("[11] FOLLOWUP node")
            ans = node.run_node("FOLLOWUP", prompt, node.model(e, "builder", node.DEFAULT_BUILDER), node.READ, str(e.main), node.TIMEOUTS["FOLLOWUP"], runtime=e.runtime)
            items, filed = [it for it in ans.get("issues") or [] if str(it.get("title") or "").strip()], []
        dropped = False
        for i, it in enumerate(items):
            try:
                entry = self.file_follow_up(it, sha, areas, types)
            except DriverError as x:  # the comment names what was filed and carries what was not, for the resume that files it
                if filed:
                    rest = "\n".join(f"- {str(t['title']).strip()}" for t in items[i:])
                    e.comment_pr(n, "**Follow-ups filed**\n\n" + "\n".join(f"- {f}" for f in filed)
                                 + f"\n\nNot filed, a resume files them: {x}\n\n{rest}\n\n{PENDING_MARK}{pending_json(items[i:])} -->\n")
                raise
            if entry:
                filed.append(entry)
            dropped = dropped or not entry
        e.comment_pr(n, "**Follow-ups filed**\n\n" + ("\n".join(f"- {x}" for x in filed) or ("none passed the anchor check" if dropped else "none")) + "\n")

    def file_follow_up(self, it, sha, areas, types):
        """Files one of the node's issues and returns its comment entry, or None when the anchor check drops it."""
        e = self.env
        title, body = str(it.get("title") or "").strip(), str(it.get("body") or "")
        body = re.sub(r"(?m)^(?:Size|Flow|Doc impact on close):[^\n]*\n?", "", body)  # triage stamps these lines
        if self.env.dead_anchors(body, sha):
            say(f"  dropped follow-up {title!r}: an anchor does not resolve at {sha[:7]}")
            return None
        f = e.body_file("followup.md", body)
        ledger = title == "Cleanup" or title.startswith("Cleanup: ")
        labels = ["needs-triage"]
        if not ledger:  # the node's area and type labels in the binding's spelling, one type at most; the run's areas when it names no area
            low, typed, kept = {str(x).lower(): str(x) for x in areas + types}, {str(x).lower() for x in types}, []
            for l in dict.fromkeys(str(x).lower() for x in it.get("labels") or []):
                if l in low and not (l in typed and any(k.lower() in typed for k in kept)):
                    kept.append(low[l])
            labels += kept + ([] if any(l.lower() not in typed for l in kept) else self.areas)
        if ledger:
            hits = [h for h in e.gh_json("issue", "list", "--search", f"{title} in:title", "--state", "all", "--limit", "200", "--json", "number,title,state")
                    if str(h.get("title")).lower() == title.lower()]
            hit = next((h for h in hits if h.get("state") != "CLOSED"), hits[0] if hits else None)
            if hit:
                num, how = str(hit["number"]), "appended"
                if hit.get("state") == "CLOSED":
                    how = "reopened"
                    e.write(f"reopen ledger #{num}", argv_for("gh", "issue", "reopen", num, "-R", e.slug, "--comment", body))
                else:
                    e.write(f"append to ledger #{num}", argv_for("gh", "issue", "comment", num, "-R", e.slug, "--body-file", f))
                return f"#{num} {hit['title']} ({how})"
            area = title[len("Cleanup: "):] if title != "Cleanup" else ""
            known = next((str(x) for x in areas if str(x).lower() == area.lower()), None) if area else None
            labels += ["umbrella"] + ([known] if known else [])
        argv = argv_for("gh", "issue", "create", "-R", e.slug, "--title", title, "--body-file", f, *[a for l in labels for a in ("--label", l)])
        r = e.write(f"file follow-up {title!r}", argv)
        unlabelled = f", without the label {area!r}, which [labels].area does not declare" if ledger and area and not known else ""
        return (r.out.strip().splitlines()[-1] if r.out.strip() else title) + (f" (ledger filed{unlabelled})" if ledger else "")

    def tick_part(self):
        if not self.k:
            return
        e = self.env
        body = e.gh_json("issue", "view", str(self.n), "--json", "body")["body"] or ""
        new = re.sub(rf"(?m)^- \[ \] ({self.k}\. \*\*.+?\(Flow: \w+\)\.\*\*)", r"- [x] \1", body)
        if new != body:
            e.write(f"tick part {self.k}", argv_for("gh", "issue", "edit", str(self.n), "-R", e.slug, "--body-file", e.body_file("issue-body.md", new)))

    # ---- the direct lane (2a)
    def direct_eligible(self):
        paths = re.findall(r"`([^`\s]+/[^`\s]+|[^`\s]+\.\w+)`", self.body)
        if not paths:
            return False
        r = self.env.run(script("ouro-binding.py", "direct", *paths, "--binding", str(self.env.main / ".claude" / "ouro.toml")), cwd=self.env.main)
        return r.code == 0

    def direct_blocked(self):
        """Why the lane cannot use the main checkout, or None: it is on the default branch, clean, at origin's tip or holding only this issue's unpushed commits."""
        e = self.env
        branch = e.git("rev-parse", "--abbrev-ref", "HEAD").out.strip()
        if branch != e.default:
            return f"the main checkout is on {branch}, not {e.default}"
        if e.git("status", "--porcelain", "-z").out:
            return "the main checkout has uncommitted changes"
        if e.git("rev-parse", "HEAD").out.strip() != e.git("rev-parse", f"origin/{e.default}", check=False).out.strip() and not self.lane_commits_ahead():
            return f"the main checkout's HEAD is not origin/{e.default}"
        return None

    def lane_commits_ahead(self):
        """Accept this issue's unpushed commits or one explicitly linked prepared revert."""
        e = self.env
        base = f"origin/{e.default}"
        if e.git("merge-base", "--is-ancestor", base, "HEAD", check=False).code != 0:
            return False
        ahead = e.git("rev-list", f"{base}..HEAD").out.splitlines()
        if len(ahead) == 1 and self.lane_is_revert(ahead[0]):
            record = self.lane_provenance(ahead[0])
            return (record["stage"] == "revert" and record["base"] == e.git("rev-parse", base).out.strip()
                    and e.git("rev-parse", f"{ahead[0]}^").out.strip() == record["base"])
        msgs = [m for m in e.git("log", "--format=%B%x00", f"{base}..HEAD").out.split("\0") if m.strip()]
        return bool(msgs) and all(re.search(rf"(?m)^Fixes #{self.n}\s*$", m) for m in msgs)

    def direct(self):
        e = self.env
        say("[2a] direct: every path sits inside [ship].direct")
        ahead = e.git("rev-parse", "HEAD").out.strip() != e.git("rev-parse", f"origin/{e.default}").out.strip()
        if e.dry:
            say(("  would: no BUILD, the main checkout already holds this issue's unpushed commit(s); gates the diff selects, "
                 if ahead else f"  would: BUILD node in the main checkout, gates the diff selects, commit with Fixes #{self.n} on {e.default}, ")
                + f"{sh.cmdline(script('Push-Direct.ps1', '-Issue', str(self.n)))}, watch the default branch's run")
            return 0
        self.in_lane, self.worktree = True, e.main
        before = e.git("rev-parse", f"origin/{e.default}").out.strip()
        if ahead:
            say("[2a] direct: the main checkout holds this issue's unpushed commit(s): shipped without a build")
        else:
            self.lane_build_record = self.lane_record("parent", None)
            self.build(track=False)
            if self.build_facts(evidence=False)["manual"]:
                say("[2a] direct: the build has manual steps, which the lane cannot carry")
                return self.lane_fallback()
        trace = dict(stage="parent", parent=None)
        if ahead and (self.lane_coordinated() or self.lane_is_revert("HEAD")
                      or "Ouro-Direct: " in e.git("log", "-1", "--format=%B").out):
            trace = self.lane_provenance("HEAD")
        sha = self.lane_ship(before, gates=trace["stage"] != "revert", stage=trace["stage"])
        if trace["stage"] == "revert":
            return self.lane_revert_stop(sha, trace["parent"])
        if sha is None:
            return self.lane_fallback()
        return self.lane_after(sha, trace["parent"], trace["stage"])

    def lane_resume(self, pr):
        """An open pull request on direct/<N>: the lane goes on from its checks, with no build."""
        e = self.env
        say(f"[2a] direct: pull request #{pr['number']} is open on direct/{self.n}: the lane resumes at its checks")
        if e.dry:
            say("  would: watch its checks, merge it, watch the default branch's run on the merge commit" if e.pr_checks() else
                f"  would: leave it to the owner's merge, since no workflow at origin/{e.default} names pull_request")
            return 0
        self.in_lane, self.worktree = True, e.main
        if not self.lane_coordinated():
            head = e.gh_json("pr", "view", str(pr["number"]), "--json", "headRefOid")["headRefOid"]
            message = e.git("log", "-1", "--format=%B", head).out
            if not re.search(r"(?m)^Ouro-Direct:", message):
                # Existing disabled integrations did not require execution metadata.
                # Explicitly recorded executions still use their validated stage chain.
                body = e.body_file("direct-pr.md", (pr.get("body") or "").strip() + "\n")
                return self.lane_after(self.lane_finish(pr, pr["title"], body))
        trace = self.lane_trace(pr)
        message = re.sub(r"(?m)^Ouro-Direct: .*\n?", "", pr.get("body") or "").strip()
        body = e.body_file("direct-pr.md", message + "\n\nOuro-Direct: " + json.dumps(trace, sort_keys=True) + "\n")
        return self.lane_after(self.lane_finish(pr, pr["title"], body), trace["parent"], trace["stage"])

    def lane_after(self, sha, parent=None, stage="parent"):
        """The default branch's run on the shipped `sha`: green ends the lane, a red run gets one fix commit, a second red is reverted."""
        e = self.env
        if stage == "revert":
            return self.lane_revert_stop(sha, parent)
        state, url, tail = self.lane_watch(sha)
        if state != "red":
            self.claim.update(f"landed: {sha}" if state == "green" else f"direct: {sha}; waiting on checks", close=state == "green")
            return 0
        self.claim.update(f"direct checks red: {sha}")
        head = e.git("rev-parse", "HEAD").out.strip()
        if (head != e.git("rev-parse", f"origin/{e.default}").out.strip()
                and e.git("merge-base", "--is-ancestor", f"origin/{e.default}", head, check=False).code == 0
                and self.lane_is_revert(head)):
            trace = self.lane_provenance(head)
            if trace["parent"] != sha or self.direct_blocked():
                raise DriverError("the unpushed direct revert cannot be reconciled; claim retained")
            pushed = self.lane_ship(e.git("rev-parse", f"{head}^").out.strip(), gates=False, stage="revert")
            return self.lane_revert_stop(pushed, sha)
        # A published head can be watched from an older checkout. Repair alone needs
        # a clean default checkout at origin, after claim admission in go().
        if (head != e.git("rev-parse", f"origin/{e.default}").out.strip()
                and e.git("rev-parse", "--abbrev-ref", "HEAD").out.strip() == e.default
                and not e.git("status", "--porcelain", "-z").out
                and e.git("merge-base", "--is-ancestor", head, f"origin/{e.default}", check=False).code == 0):
            e.git("merge", "-q", "--ff-only", f"origin/{e.default}")
        why = self.direct_blocked()
        if why:
            self.stop_issue("open decision", f"The default branch's run on the direct commit {sha[:7]} is red ({url}), and the lane's "
                            f"fix cannot be built in the main checkout: {why}.", 3)
        if parent:
            # A resumed fix has already spent the lane's one fix, even across invocations.
            state1, url1, _ = self.lane_watch(parent)
            if state1 != "red":
                raise DriverError("the direct parent failure cannot be reconciled; claim retained")
            self.lane_revert([sha, parent], [url1, url])
            return 0
        say(f"[2a] direct: the default branch's run is red ({url}): one fix commit through the lane")
        head = e.git("rev-parse", "HEAD").out.strip()
        if head != e.git("rev-parse", f"origin/{e.default}").out.strip():
            # A scope refusal leaves the committed fix in place. Git identifies it;
            # board/cache metadata cannot authorize another fix or skip publication checks.
            if not self.lane_commits_ahead() or self.lane_parent(head) != sha:
                raise DriverError("the unpushed direct fix cannot be reconciled; claim retained")
            # The failed parent identifies this execution; the recorded base scopes its repair.
            publication_base = self.lane_provenance(head)["base"]
            say("[2a] direct: reusing this issue's unpushed fix without a build")
        else:
            publication_base = head
            self.lane_build = ("fix", sha)
            self.lane_build_record = self.lane_record("fix", sha)
            self.build(findings=f"the default branch's run {url} is red at {sha[:7]}; the failure in its log:\n{tail}", track=False)
        fix = self.lane_ship(publication_base, stage="fix")
        if fix is None:
            return self.lane_fallback()
        state, url2, tail = self.lane_watch(fix)
        if state != "red":
            self.claim.update(f"landed: {fix}" if state == "green" else f"direct: {fix}; waiting on checks", close=state == "green")
            return 0
        say(f"[2a] direct: the fix's run is red too ({url2}): both commits are reverted through the lane")
        self.claim.update(f"direct checks red: {fix}")
        self.lane_revert([fix, sha], [url, url2])
        return 0

    def lane_trace(self, pr):
        """Restore a direct PR's actual scope before any resumed claim write."""
        e = self.env
        head = e.gh_json("pr", "view", str(pr["number"]), "--json", "headRefOid")["headRefOid"]
        base = e.git("merge-base", f"origin/{e.default}", head).out.strip()
        trace = self.lane_provenance(head)
        self.lane_prepare(base, head, trace["stage"], check=False)
        return trace

    def lane_fixes(self, head):
        return bool(re.search(rf"(?m)^Fixes #{self.n}\s*$",
                              self.env.git("log", "-1", "--format=%B", head).out))

    def lane_is_revert(self, head):
        return bool(re.fullmatch(rf"Revert the direct commits for #{self.n}(?: \(#\d+\))?",
                                 self.env.git("log", "-1", "--format=%s", head).out.strip()))

    def lane_coordinated(self):
        return bool(self.claim.config and not self.env.dry
                    and os.environ.get(self.claim.config.credential_env))

    def lane_record(self, stage, parent):
        """Only new driver commits receive provenance; never amend historical evidence."""
        e = self.env
        try:
            execution = self.lane_provenance(parent)["execution"] if parent else uuid.uuid4().hex
        except DriverError:
            if (self.lane_coordinated()
                    or "Ouro-Direct:" in e.git("log", "-1", "--format=%B", parent).out):
                raise
            # Legacy disabled-lane commits never required an execution record.
            return ""
        record = dict(issue=self.n, execution=execution, stage=stage, parent=parent,
                      base=e.git("rev-parse", "HEAD").out.strip())
        return "\nOuro-Direct: " + json.dumps(record, sort_keys=True) + "\n"

    def lane_merged_direct(self):
        """GitHub identifies the vehicle; Git proves its exact merged head and base."""
        e = self.env
        prs = e.gh_json("pr", "list", "--head", f"direct/{self.n}", "--state", "merged",
                        "--limit", "100", "--json", "state,headRefOid,mergeCommit,baseRefName")
        merged = {}
        first_parent = set(e.git("rev-list", "--first-parent", f"origin/{e.default}").out.splitlines())
        publications = {}
        for pr in prs:
            source, publication = pr.get("headRefOid"), (pr.get("mergeCommit") or {}).get("oid")
            if isinstance(source, str) and isinstance(publication, str):
                publications.setdefault(source, set()).add(publication)
        for pr in prs:
            sha = (pr.get("mergeCommit") or {}).get("oid")
            head = pr.get("headRefOid")
            if (pr.get("state") != "MERGED" or pr.get("baseRefName") != e.default
                    or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
                    or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)
                    or sha == head or sha not in first_parent or len(publications[head]) != 1):
                continue
            parents = e.git("rev-list", "--parents", "-n", "1", sha, check=False).out.split()
            lines = re.findall(r"(?m)^Ouro-Direct: (.+)$", e.git("log", "-1", "--format=%B", head, check=False).out)
            if len(lines) != 1 or len(parents) not in (2, 3):
                continue
            if len(parents) == 3:
                if parents[2] == head:
                    merged[sha] = head
                continue
            # A custom squash message carries no execution record. Require the
            # source's exact recorded delta at the publication's actual parent;
            # equal final trees alone would admit unrelated/reused branch heads.
            try:
                record = json.loads(lines[0])
                base = record["base"]
                if record["issue"] != self.n or not re.fullmatch(r"[0-9a-f]{40}", base):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            parent = parents[1]
            if (e.git("merge-base", parent, head, check=False).out.strip() != base
                    or e.git("merge-base", "--is-ancestor", head, parent, check=False).code == 0):
                continue
            # Compare the actual clean merge result, not patch/blob identities:
            # disjoint upstream edits in the same file change both blob IDs.
            # merge-tree writes objects only; it never touches an index/worktree.
            result = e.git("merge-tree", "--write-tree", parent, head, check=False)
            tree = result.out.strip()
            if (result.code == 0 and re.fullmatch(r"[0-9a-f]{40}", tree)
                    and tree == e.git("rev-parse", sha + "^{tree}").out.strip()
                    and tree != e.git("rev-parse", parent + "^{tree}").out.strip()):
                merged[sha] = head
        return merged

    def lane_provenance(self, head):
        """Git/merge-body evidence scopes the one fix to one execution, never a board cache."""
        e = self.env
        def read(sha):
            msg = e.git("log", "-1", "--format=%B", sha).out
            lines = re.findall(r"(?m)^Ouro-Direct: (.+)$", msg)
            if not lines:
                source = self.lane_merged_direct().get(e.git("rev-parse", sha).out.strip())
                if source:
                    return self.lane_provenance(source)
            try:
                record = json.loads(lines[0]) if len(lines) == 1 else {}
                if (record["issue"] != self.n or not re.fullmatch(r"[0-9a-f]{32}", record["execution"])
                        or record["stage"] not in ("parent", "fix", "revert")
                        or not re.fullmatch(r"[0-9a-f]{40}", record["base"])
                        or e.git("merge-base", "--is-ancestor", record["base"], sha, check=False).code != 0):
                    raise ValueError()
                return record
            except (KeyError, TypeError, ValueError):
                raise DriverError("direct execution provenance is missing or ambiguous; reconcile the Git execution chain manually before retrying; no BUILD or publication permitted") from None
        record = current = read(head)
        expected = {"revert": "fix", "fix": "parent", "parent": None}
        sha = head
        while expected[current["stage"]]:
            parent = current.get("parent")
            if not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{40}", parent):
                raise DriverError("invalid direct parent provenance; claim retained")
            prior = read(parent)
            if (prior["stage"] != expected[current["stage"]] or prior["execution"] != record["execution"]
                    or e.git("merge-base", "--is-ancestor", parent, sha, check=False).code != 0):
                raise DriverError("direct execution chain cannot be reconciled; claim retained")
            sha, current = parent, prior
        if current.get("parent") is not None:
            raise DriverError("direct parent exceeds the one-fix limit; claim retained")
        return record

    def lane_parent(self, head):
        return self.lane_provenance(head)["parent"]

    def lane_prepare(self, base, head, stage, *, check=True):
        """Reconstruct actual scope from Git and reviewed policy for publication or a resumed wait."""
        e = self.env
        if not (self.claim.config and not e.dry and os.environ.get(self.claim.config.credential_env)):
            return
        paths = [x for x in e.git("diff", "--no-renames", "--name-only", "-z", f"{base}..{head}").out.split("\0") if x]
        policy_ref = f"origin/{e.default}" if check else base
        policy = e.ob.tomllib.loads(e.git("show", f"{policy_ref}:.claude/ouro.toml").out)
        areas = set()
        for gate in policy.get("gate", []):
            specs = gate.get("paths")
            if not specs or e.git("diff", "--no-renames", "--name-only", "-z", f"{base}..{head}", "--", *specs).out:
                areas.update(a for a in gate["areas"] if a != "*")
        self.claim.prepare(base, head, paths, areas, stage, check=check)

    def lane_ship(self, prev, gates=True, stage="parent"):
        """Gates, then Push-Direct.ps1 and its vehicle, for the commits past `prev`: the sha now on the default branch, or None when nothing was pushed."""
        e = self.env
        if self.build_facts(evidence=False)["validation_pending"]:
            raise DriverError("direct lane does not support pending validation; nothing pushed")
        head = e.git("rev-parse", "HEAD").out.strip()
        paths = [x for x in e.git("diff", "--no-renames", "--name-only", "-z", f"{prev}..HEAD").out.split("\0") if x]
        if self.env.run(script("ouro-binding.py", "direct", *paths, "--binding", str(e.main / ".claude" / "ouro.toml")), cwd=e.main).code != 0:
            say("[2a] direct: the diff reaches a path outside [ship].direct")
            return None
        red = self.run_gates(head) if gates else None
        if red:
            say(f"[2a] direct: gate `{red[0]}` exited {red[1]}; nothing is pushed")
            return None
        self.lane_prepare(prev, head, stage)
        p = self.env.run(script("Push-Direct.ps1", "-Issue", str(self.n)), cwd=e.main)
        out = parse_direct(p.out)
        if p.code != 0 and not out["pushed"]:
            self.claim.update("direct push failed; nothing pushed")
            say(f"[2a] direct: Push-Direct.ps1 stopped before a push: {p.text.splitlines()[-1] if p.text else p.code}")
            return None
        if p.code != 0:
            e.git("fetch", "origin", check=False)
            say("[2a] direct: the read-back after the push failed; origin is fetched and the run goes on")
        elif out["vehicle"] not in ("push", "pr"):
            raise DriverError(f"{sh.cmdline(p.argv)} exited 0 without a vehicle line: {p.text}")
        self.claim.update("direct pushed; waiting on merge/checks")
        return self.lane_pr(head) if out["vehicle"] == "pr" else head

    def lane_pr(self, head):
        """vehicle: pr. The commits are on direct/<N>: a pull request with the commit message as its body, then lane_finish; the merge commit's sha."""
        e = self.env
        branch = f"direct/{self.n}"
        msg = without_build_lines(e.git("log", "-1", "--format=%B", head).out)
        subject = msg.splitlines()[0]
        if f"Fixes #{self.n}" not in msg and not subject.startswith("Revert"):
            msg += f"\n\nFixes #{self.n}"
        body = e.body_file("direct-pr.md", msg + "\n")
        e.git("fetch", "origin", check=False)
        e.write("open the pull request", argv_for("gh", "pr", "create", "-R", e.slug, "--base", e.default, "--head", branch, "--title", subject, "--body-file", body))
        pr = self.find_pr(branch)
        if pr is None:
            raise DriverError(f"gh pr create ran but no pull request reads back for {branch}")
        if e.git("rev-parse", "HEAD").out.strip() != e.git("rev-parse", f"origin/{e.default}").out.strip():
            e.git("reset", "-q", "--hard", f"origin/{e.default}")
        return self.lane_finish(pr, subject, body)

    def lane_finish(self, pr, subject, body):
        """The open pull request's checks, then its merge: the merge commit's sha."""
        e = self.env
        branch = f"direct/{self.n}"
        n = pr["number"]
        head = e.gh_json("pr", "view", str(n), "--json", "headRefOid").get("headRefOid") or ""
        if not e.pr_checks():  # every selected gate ran here; the merge script refuses a pull request no check ran on
            say(f"[2a] direct: no workflow at origin/{e.default} names pull_request, so #{n} gets no check: it waits for the owner's merge on GitHub")
            raise Stopped(0)
        w = self.watch_pr(n, head, e.main)
        if w.code is None:
            say(f"[2a] direct: the checks on #{n} are still running: invoke `run {self.n}` again")
            raise Stopped(0)
        if w.code != 0:
            raise DriverError(f"the checks on #{n} are not green: {w.text}")
        sha = self.merge_pr(n, f"{subject} (#{n})", body, e.main, head)
        if sha is None:
            raise DriverError(f"#{n} conflicts with {e.default}: sync the direct/{self.n} branch by hand, then invoke `run {self.n}` again")
        e.git("fetch", "origin", check=False)
        if e.git("rev-parse", "--abbrev-ref", "HEAD").out.strip() == e.default:
            e.git("merge", "-q", "--ff-only", f"origin/{e.default}", check=False)
        e.git("branch", "-D", branch, check=False)
        say(f"[2a] direct: #{n} merged as {sha}")
        return sha

    def lane_watch(self, sha):
        """(state, run url, the failure in its log): the default branch's run on `sha` is green, still running, or red."""
        e = self.env
        r = self.env.run(script("Watch-PullRequestChecks.ps1", "-Sha", sha), cwd=e.main, timeout=watch_limit())
        if r.code is None:
            say(f"[2a] direct: the run on {sha[:7]} is still running: invoke `run {self.n}` again")
            return "running", None, ""
        if r.code == 0:
            say(f"[2a] direct: {sha[:7]} is on {e.default} and its run is green")
            return "green", None, ""
        bad = BAD_RUN.findall(r.text)
        if not bad:
            raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}")
        url = e.gh_json("run", "view", bad[0][0], "--json", "url")["url"]
        log = e.ghr("run", "view", bad[0][0], "--log-failed", check=False).text
        return "red", url, failure_extract(log)

    def lane_revert(self, shas, urls):
        """Two red runs: one revert commit of both, pushed the same way, the issue reopened if it closed, then the stop."""
        e = self.env
        record = self.lane_record("revert", shas[0])
        # Mainline 1 also accepts ordinary commits; keep one abortable sequence.
        r = e.git("revert", "--no-commit", "-m", "1", *shas, check=False)
        if r.code != 0:
            e.git("revert", "--abort", check=False)
            raise DriverError(f"cannot revert {' '.join(s[:7] for s in shas)}: {r.text}")
        tip = e.git("rev-parse", "HEAD").out.strip()
        e.git("commit", "-q", "-F", e.body_file("revert-msg.txt", f"Revert the direct commits for #{self.n}\n\nThe default branch's runs on both were red: {urls[0]} and {urls[1]}.\n" + record))
        pushed = self.lane_ship(tip, gates=False, stage="revert")
        return self.lane_revert_stop(pushed, shas[0], urls, parent=shas[1])

    def lane_stop_complete(self, pushed):
        marker = f"<!-- ouro-direct-stop: {pushed} -->"
        if not any(marker in c.get("body", "").splitlines() for c in self.issue.get("comments") or []):
            return False
        trace = self.lane_provenance(pushed)
        if trace["stage"] != "revert":
            return False
        fix = trace["parent"]
        parent = self.lane_parent(fix)
        prefix = (f"**Stop:** open decision\n\nThe direct commit {parent[:7]} and its one fix {fix[:7]} "
                  "each left the default branch's run red: ")
        trusted = self.env.trusted()
        return any((c.get("author") or {}).get("login", "").lower() in trusted
                   and c.get("body", "").startswith(prefix)
                   and f"Both are reverted in {pushed[:7]}." in c.get("body", "").splitlines()
                   and marker in c.get("body", "").splitlines()
                   for c in self.issue.get("comments") or [])

    def lane_revert_stop(self, pushed, fix, urls=None, parent=None):
        e = self.env
        if parent is None:
            parent = self.lane_parent(fix)
        if urls is None:
            urls = [self.lane_watch(sha)[1] for sha in (parent, fix)]
        # The final comment acknowledges completion after labels and reopen.
        # Failed/ambiguous requests are safe to repeat using the published Git head.
        argv = argv_for("gh", "issue", "edit", str(self.n), "-R", e.slug)
        for label in ("agent-ready", "trivial", "checkpoint"):
            if label in self.labels:
                argv += ["--remove-label", label]
        e.write("swap agent-ready -> needs-ruling", argv + ["--add-label", "needs-ruling"])
        if e.gh_json("issue", "view", str(self.n), "--json", "state")["state"] == "CLOSED":
            e.write(f"reopen #{self.n}", argv_for("gh", "issue", "reopen", str(self.n), "-R", e.slug))
        content = (f"The direct commit {parent[:7]} and its one fix {fix[:7]} each left the default branch's run red: {urls[0]} and {urls[1]}.\n\n"
                   + (f"Both are reverted in {pushed[:7]}.\n\n<!-- ouro-direct-stop: {pushed} -->" if pushed else
                      "Both are reverted in a commit the lane could not push; it is in the main checkout, unpushed."))
        if not pushed or not self.lane_stop_complete(pushed):
            e.comment_issue(self.n, f"**Stop:** open decision\n\n{content}\n")
        self.claim.update("stopped: open decision", close=True)
        say("stopped: open decision")
        raise Stopped(3)

    def lane_fallback(self):
        """Nothing was pushed: the commit moves to a worktree branch, the main checkout goes back to origin's tip, the run goes on as express or tighter."""
        e = self.env
        say("[2a] direct: the commit moves to a worktree and the run continues as express")
        target = e.main / ".claude" / "worktrees" / self.dirname
        node.worktree_readiness(e.runtime, e.main, target, self.branch, "HEAD")
        inputs = execution.fingerprint(self.worktree)
        observations = [{"tool": "run", "command": cmd, "exit": 0, "inputs": inputs}
                        for cmd in execution.proven(self)]
        e.git("branch", self.branch, "HEAD")
        self.in_lane = False
        self.claim.prepared = None  # direct trace remains on the board; subsequent PR records use their own head
        self.worktree = e.main / ".claude" / "worktrees" / self.dirname
        e.git("reset", "-q", "--hard", f"origin/{e.default}")  # before the worktree: a setup stop there leaves the main checkout clean
        self.ensure_worktree()
        execution.retain(self, observations)
        self.flow = "express"
        self.rederive()
        return self.drive(None)

    # ---- dry run
    def build_model(self):
        """The BUILD node's model: builder_trivial (sonnet) on a trivial issue, else models.builder with the shared default."""
        e = self.env
        return node.model(e, "builder_trivial", "sonnet") if "trivial" in self.labels else node.model(e, "builder", node.DEFAULT_BUILDER)

    def plan(self, pr, branch_known):
        e = self.env
        say("[3] worktree and branch: " + (f"exists ({self.branch})" if branch_known else
            f"would: git worktree add .claude/worktrees/{self.dirname} -b {self.branch} origin/{e.default}")
            + (f", then repo.setup `{e.lookup('repo.setup')}` in it" if e.lookup("repo.setup") else ""))
        if pr:
            say(f"[4] BUILD: skipped, pull request #{pr['number']} exists")
        else:
            say(f"[4] BUILD: would run node BUILD ({self.build_model()}) in the worktree, then git add -A and git commit -F <message>")
        chosen = [r for r, g in self.area_gates() if self.runs_here(r, g, self.flow)]
        say("[5] gates: " + ("; ".join(chosen) if chosen else "none selected for the class beyond the pull request's checks") + " (a gate with paths runs only when the diff touches them)")
        say(f"[6] push: {sh.cmdline(script('Sync-Branch.ps1'))} then {sh.cmdline(script('Push-Branch.ps1'))}; "
            f"would open a draft pull request" + (" at convergence" if self.flow == "express" else " at the first push"))
        say(f"[7] REVIEW: ship.review = {e.lookup('ship.review')}, size {self.size}: first reader {self.primary or 'none'}"
            f"{', then Copilot on the pull request' if self.copilot else ''}; mode {MODE.get(self.flow, 'sized')}, up to {FIX_CAP} fix rounds")
        say(f"[8] checks: {sh.cmdline(script('Watch-PullRequestChecks.ps1', '-Pr', '<PR>'))}" if e.pr_checks() else
            f"[8] checks: skipped, no workflow at origin/{e.default} names pull_request: every selected gate runs at [5], and the owner merges")
        policy = e.lookup("ship.policy")
        ok = e.pr_checks() and may_merge(policy, e.lookup("ship.unattended_classes"), self.labels, self.flow) and not self.manual_paths()
        say(f"[9] merge: policy {policy}, class {self.flow}: " + (f"would run {sh.cmdline(script('Merge-PullRequest.ps1', '-Pr', '<PR>', '-Subject', '<subject> (#<PR>)', '-BodyFile', '<file>', '-Linear', 'no'))}"
                                                           if ok else "would post **Ready for merge** and exit 0"))
        say(f"[10] after the merge: {sh.cmdline(script('Remove-LandedBranch.ps1', '-Pr', '<PR>'))}; "
            f"{sh.cmdline(script('Test-AgentReadyAnchors.ps1', '-Ref', 'origin/' + e.default))}")
        say("[11] FOLLOWUP: would run only when the record's Follow-up section, an L finding of the last review round "
            "or an OUTSIDE finding of a later round holds an item")
        say("[12] close: " + (f"the squash body's Fixes #{self.n}" if self.fixes else f"would tick part {self.k}; the issue stays open"))
        return 0


def main(env, n, part):
    run = Run(env, n, part)
    try:
        code = run.go()
        if code == 0:
            run.claim.update("waiting; resume reads GitHub/git")
        return code
    except Stopped as ex:
        if not run.claim.terminal:
            run.claim.update("waiting" if ex.args[0] == 0 else "stopped", close=ex.args[0] != 0)
        return ex.args[0]
    except Refused:
        run.claim.update("refused", close=not getattr(run, "in_lane", False))
        raise
    except BaseException:
        run.claim.update("interrupted; outcome unknown, resume required")
        raise
