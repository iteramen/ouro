"""Pure record and issue-body helpers the driver modules share; they import nothing of the driver."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

FIX_CAP = 5
PART_ITEM = re.compile(r"(?m)^- \[([ xX])\] ([0-9]+)\. (\*\*.+?\(Flow: \w+\)\.\*\*.*)$")  # the contract's part form alone: a plain task list is no checklist
ORDER = ["direct", "express", "standard", "full"]
FULL_PREFIXES = (".github/workflows/", ".github/actions/", "actions/", "hooks/", "release/", ".claude/ouro")
EXEC_EXT = (".ps1", ".py", ".sh", ".yml", ".yaml", ".json", ".toml", ".js", ".ts", ".cs", ".cmd", ".bat")
REVIEW_MARK = re.compile(r"\*\*Review\*\* round ([0-9]+): (CLEAN|FINDINGS) @ ([0-9a-f]{40})")
RECORD = re.compile(r"<!-- ouro-record: (.*?) -->", re.DOTALL)
SEVERITY = {"blocker": "H", "major": "M", "minor": "L"}
BUILD_LINE = "Ouro-Build: "
MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)


def parse_parts(body):
    return [(int(m.group(2)), m.group(1) in "xX", m.group(3)) for m in PART_ITEM.finditer(body)]


def sev(f):
    return str(f.get("severity", "")).strip().upper()[:1] if isinstance(f, dict) else ""


def outside(f):
    """A verification finding in lines no fix touched: a follow-up, never a fix round (contract section 7)."""
    return isinstance(f, dict) and str(f.get("scope", "")).strip().upper() == "OUTSIDE"


def needs_fix(rec):
    """An H or M finding a fix must answer, unless an approver ruled the record; a first review has no fix, so its scope is ignored."""
    verifying = rec.get("round", 1) > 1
    return not rec.get("ruled") and any(sev(f) in ("H", "M") and not (verifying and outside(f)) for f in rec["findings"])


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
    out += [f"- BUILD reported `{g.get('cmd')}`: exit {g.get('exit')}" for g in rec.get("build_gates") or []]
    out += [f"- Driver validation @ {g['sha']}: `{g['cmd']}` exit {g['exit']}; evidence `{g['log']}`"
            for g in rec.get("validation_results") or []]
    out += [f"- Driver validation attempt @ {g['sha']}: `{g['cmd']}` exit {g['exit']}; evidence `{g['log']}`"
            for g in rec.get("validation_attempts") or [] if g not in (rec.get("validation_results") or [])]
    out += [f"- CI validation @ {g['sha']}: `{g['name']}` succeeded; {g['url']}"
            for g in rec.get("ci_validation") or []]
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


def read_record(body):
    m = RECORD.search(body or "")
    return json.loads(m.group(1)) if m else {}


def review_plan(value, size):
    """(the reviewer that reads first, Copilot after it) for a ship.review value and a Size: the rows of the binding doc's table."""
    if size == "S":
        return {"adversarial-review": ("node", False), "copilot": (None, True), "external-audit": ("reader", False)}.get(value, (None, False))
    return ("node", value == "copilot")


RELEASE_NOTES = [r"(^|/)CHANGELOG\.md$"]  # repo.release_notes unset


def release_notes(paths, patterns=RELEASE_NOTES):
    """The paths a repo.release_notes pattern matches, searched case-sensitively as git compares paths."""
    return [p for p in paths if any(re.search(x, p) for x in patterns)]


def derive_size(paths, lines, patterns=RELEASE_NOTES):
    """('M', why) when a diff is over the S shape (contract section 4), else ('S', ''); release notes count toward the lines only."""
    notes = release_notes(paths, patterns)
    counted = [p for p in paths if p not in notes]
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
