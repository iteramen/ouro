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
    """An unrelated external finding, not a defect introduced by the original PR (contract section 7)."""
    return isinstance(f, dict) and str(f.get("scope", "")).strip().upper() == "OUTSIDE"


ROUTES = ("fix-now", "issue", "idea", "ledger")
REASONS = ("own-change", "data-loss", "wrong-place", "breaks-gate", "misleads-run", "open-decision")


def named(item, key, names):
    """The leftover's `key` folded, when it is one of `names`; "" otherwise."""
    r = str(item.get(key, "")).strip().lower() if isinstance(item, dict) else ""
    return r if r in names else ""


def route(item):
    """The route the review gave a leftover (an L finding or a residue item), one of ROUTES or ""."""
    return named(item, "route", ROUTES)


def reason(item):
    """The leftover's reason, one of REASONS or ""."""
    return named(item, "reason", REASONS)


def fix_now(f):
    """A leftover the review routed `fix-now`: an L finding, or a residue item, which carries no severity."""
    return sev(f) in ("L", "") and route(f) == "fix-now"


def filing_route(item):
    """(route, why) a leftover the fix-now round did not fix is filed under (contract section 7); `why` says why the driver
    reads it otherwise than the review routed it, "" when the route stands. An H or M finding is an `issue`."""
    r = route(item)
    if sev(item) in ("H", "M"):
        return "issue", ""
    if not r:
        return "issue", "it had no route"
    if r == "issue" and not reason(item):
        return "ledger", "it was routed `issue` with no reason"
    if r == "fix-now":
        return (("issue", "it was routed `fix-now` and is not fixed in this change") if reason(item)
                else ("ledger", "it was routed `fix-now` with no reason and is not fixed in this change"))
    return r, ""


def routed_residue(ans):
    """A REVIEW answer's routes for the record's residue items, each with its `item` text."""
    return [{**x, "item": str(x.get("item") or "")} for x in ans.get("residue") or [] if isinstance(x, dict)]


def pinned_residue(entries, items):
    """A REVIEW answer's residue routes, each tied to the record item its `index` names (1-based, in the record's
    `## Follow-up` list the review read): its `item` becomes that item's text, so a reworded entry keeps its route
    after a later round reorders the list. An entry whose index is not an integer inside the list, or repeats an
    earlier entry's, keeps the reviewer's text and says why under `unmatched`."""
    out, seen = [], set()
    for x in entries or []:
        if not isinstance(x, dict):
            continue
        k = x.get("index")
        if not (isinstance(k, int) and not isinstance(k, bool) and 1 <= k <= len(items)):
            out.append({**x, "unmatched": f"its index {k!r} names none of the {len(items)} item(s) of the record's Follow-up list"})
        elif k in seen:
            out.append({**x, "unmatched": f"its index {k} repeats an earlier entry's"})
        else:
            seen.add(k)
            out.append({**x, "item": str(items[k - 1])})
    return out


def residue_route(records, item):
    """A residue item's route: the newest record's that routes its text, whitespace folded; {} when none does. The text is
    the record item's own where the entry's index pinned it, the reviewer's where it did not."""
    key = " ".join(str(item).split())
    return next((x for r in reversed(records) for x in r.get("residue") or [] if " ".join(x["item"].split()) == key), {})


def untied_routes(records, items):
    """One sentence per residue route of the newest record that routes any whose index tied it to no record item."""
    newest = next((r for r in reversed(records) if r.get("residue")), {})
    keys = {" ".join(str(x).split()) for x in items}
    return [f"the review's route `{x.get('route')}` for {' '.join(x['item'].split())[:120]!r} is tied to no item: {x['unmatched']}"
            + (", though its text matches one" if " ".join(x["item"].split()) in keys else ", and its text matches none, so no item takes the route")
            for x in newest.get("residue") or [] if x.get("unmatched")]


def finding_key(f):
    """A leftover's identity across rounds, less the severity: its file, line and claim (a residue item's text), whitespace folded."""
    return (str(f.get("file")), str(f.get("line")), " ".join(str(f.get("claim") or f.get("item") or "").split()))


def needs_fix(rec):
    """An H or M finding a fix must answer, unless an approver ruled the record; a first review has no fix, so its scope is ignored."""
    verifying = rec.get("round", 1) > 1
    return not rec.get("ruled") and any(sev(f) in ("H", "M") and not (verifying and outside(f)) for f in rec["findings"])


REPO_POLICY = "the repository's [ship]"


def ship_policy(env):
    """(policy, unattended_classes, whose): the viewer's [[ship.person]] entry over [ship], key by key. `whose` names
    the policy's source, then the unattended classes' where another source sets them."""
    policy, classes = env.lookup("ship.policy"), env.lookup("ship.unattended_classes")
    people = env.lookup("ship.person") or []
    login = env.viewer() if people else None  # no gh call for a binding without entries
    entry = next((p for p in people if str(p.get("login", "")).lower() == login), None)
    if entry is None:
        return policy, classes, REPO_POLICY
    src = {k: f"{login}'s [[ship.person]] entry" if k in entry else REPO_POLICY for k in ("policy", "unattended_classes")}
    classes = entry.get("unattended_classes", classes)
    whose = src["policy"] + (f", unattended_classes from {src['unattended_classes']}"
                             if classes is not None and src["unattended_classes"] != src["policy"] else "")
    return entry.get("policy", policy), classes, whose


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
            "findings": ans.get("findings") or [], "residue": routed_residue(ans), "false": ans.get("false_record_sentences"), "reader": ans.get("reader")}


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
    return [line[2:] for line in head.splitlines() if line.startswith("- ")]


def review_line(records, reviewer_on):
    if not reviewer_on:
        return "Review: none"
    fixes = sum(1 for r in records if needs_fix(r)) - (1 if records and needs_fix(records[-1]) else 0)
    counts, seen = {k: 0 for k in "HML"}, set()
    for r in records:
        for f in r["findings"]:
            key = (sev(f), str(f.get("file")), str(f.get("line")), " ".join(str(f.get("claim") or "").split()))
            # Count once in the first round reporting it. OUTSIDE is unrelated to the original PR;
            # IN-PR still counts even outside the latest fix. STANDS is an earlier finding.
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
    ans = {**ans, "verdict": verdict}  # one decision for the heading, machine record and progression
    lines = [f"**Review** round {rnd}: {verdict} @ {sha}", "", f"{len(findings)} finding(s).", ""]
    for f in findings:
        lines.append(f"- {sev(f) or '?'} {f.get('file', '')}:{f.get('line', '')} {f.get('claim', '')} ({f.get('evidence', '')})")
    lines += ["", "```json", json.dumps(ans, indent=1), "```", ""]
    return "\n".join(lines)


USAGE_COLUMNS = ("node", "session", "input", "output", "cache_read", "cache_creation", "cost", "backend", "turns", "effort")  # a new one goes last: usage_rows reads an older table by position
USAGE_TABLE = re.compile(r"(?m)^\| node \| session \|[^\n]*(?:\n\|[^\n]*)*")  # a drawn table, whatever columns it had


def usage_table(rows):
    """The usage rows as a Markdown table; a missing value, a cost or turn count no backend reported or an effort the host's
    own settings decided, reads unknown."""
    if not rows:
        return ""
    out = ["| node | session | input | output | cache read | cache creation | cost USD | backend | turns | effort |", "|" + "---|" * len(USAGE_COLUMNS)]
    out += ["| " + " | ".join("unknown" if u.get(k) is None else str(u[k]) for k in USAGE_COLUMNS) + " |" for u in rows]
    return "\n".join(out)


def usage_rows(fresh, earlier):
    """The record's usage rows: `earlier`'s (a record's list, or the table a record drew before it kept rows) whose session
    no row of `fresh` holds, then `fresh`'s, so a resume keeps the rows of earlier invocations and counts none twice."""
    if isinstance(earlier, str):
        earlier = [dict(zip(USAGE_COLUMNS, (c.strip() for c in line.strip().strip("|").split("|"))))
                   for line in earlier.splitlines()[2:] if line.startswith("|")]
    rows = [{k: u.get(k) for k in USAGE_COLUMNS} for u in fresh]
    sessions = {u["session"] for u in rows}
    return [{k: u.get(k) for k in USAGE_COLUMNS} for u in earlier or [] if isinstance(u, dict) and u.get("session") not in sessions] + rows


def with_usage(body, fresh):
    """`body` with `fresh` merged into its record's usage rows and the table drawn from them, the rest as it stands: a
    merged pull request's body, which nothing redraws."""
    body = body.replace("\r\n", "\n")
    rec = read_record(body)
    rec["usage"] = usage_rows(fresh, rec.get("usage"))
    table = usage_table(rec["usage"])
    raw = json.dumps(rec).replace("--", "-\\u002d")
    body = RECORD.sub(lambda _: f"<!-- ouro-record: {raw} -->", body, count=1)
    return USAGE_TABLE.sub(lambda _: table, body, count=1) if USAGE_TABLE.search(body) else body.replace("</details>", table + "\n\n</details>", 1)


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
                out += ["", "  " + fence, *["  " + line for line in str(m["edit"]).splitlines()], "  " + fence, ""]
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
    fix = rec.get("fix_now") if isinstance(rec.get("fix_now"), dict) else {}
    if fix:
        beyond = fix.get("outside") or []
        out.append(f"- Fix-now round, after review round {fix.get('round')} and outside the fix rounds: "
                   f"{len(fix.get('findings') or [])} leftover(s) handed to the BUILD node"
                   + (f"; outside the change's own files its commit touched {', '.join(f'`{p}`' for p in beyond)}" if beyond else "")
                   + ("" if fix.get("kept") else f"; not pushed, since {fix.get('why')}, so they go to the FOLLOW-UP node"))
    if rec.get("note"):
        out.append(f"- {rec['note']}")
    out += [f"- {review}", ""]
    usage = rec.get("usage")
    if usage:
        out += [usage_table(usage) if isinstance(usage, list) else usage, ""]
    out += ["</details>", "", "## Follow-up", *[f"- {x}" for x in rec.get("residue", [])], f"<!-- ouro-record: {raw} -->", ""]
    return "\n".join(out)


PYTEST_SECTION = re.compile(r"^={3,} (.+?) ={3,}$")
PYTEST_KEEP = re.compile(r"^(FAILED|ERROR) |^={3,} (short test summary info|.*\b(failed|errors?)\b.*) ={3,}$")
LOG_PREFIX = re.compile(r"^[^\t]*\t[^\t]*\t\d{4}-\d\d-\d\dT[\d:.]+Z ?|\x1b\[[0-9;]*m")  # gh's job, step and timestamp; a colour
ERROR_MARK = re.compile(r"\b(error|failed|failure|fatal|panic|traceback|exception)\b", re.I)


def verdict_end(text, i):
    """One past the last line of the `FAIL:` verdict at line `i`: the verdict, and the Python traceback right under it
    through the first line at column 0 after its indented frames. For a chained traceback that is the first exception's
    line, so `During handling...` and the later traceback fall outside; a multi-line exception message keeps its first line."""
    if i + 1 >= len(text) or not text[i + 1].startswith("Traceback (most recent call last):"):
        return i + 1
    j = i + 2
    while j < len(text) and text[j][:1] in (" ", "\t"):
        j += 1
    return min(j + 1, len(text))


GATE_FAILED = re.compile(r"^(?:::error::|##\[error\])gate failed: (.+?)\s*$|^FAIL exit=-?\d+  (.+?)\s*$")  # the gates Action's verdicts


def failed_gates(log):
    """{job name: the gate commands its lines name failed} of a `gh run view --log-failed` text, whose lines gh prefixes
    with the job's name."""
    got = {}
    for line in log.splitlines():
        job = line.split("\t", 1)[0]
        if m := GATE_FAILED.match(LOG_PREFIX.sub("", line)):
            got.setdefault(job, set()).add(m.group(1) or m.group(2))
    return got


def failure_extract(log, cap=200, tail=60):
    """What a fix round reads of a red log: pytest's FAILURES and ERRORS sections with the FAILED and ERROR lines and the
    result line, or else every line an error marker names, with two lines of context, then the log's tail. Over `cap`
    lines it cuts from the middle; a fixed tail misses a failure that a long SKIPPED list follows. A line that starts with
    `FAIL:`, a fixture suite's verdict, is added back after the cut with the traceback under it, so the cut never drops
    either."""
    lines = log.strip().splitlines()
    text = [LOG_PREFIX.sub("", line) for line in lines]
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
    keep = sorted(set(keep) | {j for i, t in enumerate(text) if t.startswith("FAIL:") for j in range(i, verdict_end(text, i))})
    if not pytest:
        keep = sorted(set(keep) | set(range(max(0, len(lines) - tail), len(lines))))
    out, last = [], -1
    for i in keep:
        if i > last + 1:
            out.append(f"[... {i - last - 1} line(s) left out ...]")
        out.append(lines[i])
        last = i
    return "\n".join(out)


VERIFICATION = re.compile(r"(?ms)^<details><summary>Verification</summary>$.*?^</details>$")


DRAWN_HEADINGS = ("## Merge danger", "## Manual steps", "## Summary", "## Changes", "## Follow-up")  # every one render_body draws


def body_sections(body, cut=True):
    """[(`## ` heading, its section's text)] of a pull request body in order, a heading named twice listed twice, each
    heading and line with trailing whitespace stripped, the record comment cut and line ends as git writes them.
    With `cut`, the Verification block is cut too: it has no heading of its own, so the section above it would take it in.
    A `## ` heading inside the block survives the cut, with the block's lines from it to the block's end."""
    def cut_block(m):
        at = re.search(r"(?m)^## ", m.group(0))
        return m.group(0)[at.start():].removesuffix("</details>") if at else ""
    text = RECORD.sub("", (body or "").replace("\r\n", "\n"))
    parts = re.split(r"(?m)^(?=## )", VERIFICATION.sub(cut_block, text) if cut else text)
    return [(p.split("\n", 1)[0].rstrip(), "\n".join(line.rstrip() for line in p.strip().splitlines())) for p in parts if p.startswith("## ")]


def drawn_body(rec, review, body=""):
    """render_body, keeping each section of `body` a person edited since the driver drew it: the record holds a digest of
    each section as drawn, and a section that no longer matches its digest is theirs, kept as they left it. A section
    under a heading the driver does not draw is theirs too, wherever they put it, inside the Verification block included,
    kept in its order, before `## Follow-up`, with only the lines the person wrote: the drawn lines above the first heading
    (the part, `Fixes` and review lines), and for a section inside the block the block's drawn lines, whose digests the
    record holds, are redrawn, never kept inside it. A record an older driver drew may hold no `drawn` table: a heading
    the driver draws is then never a person's; or no `drawn_lines`: the record's part and `Fixes` lines and a `Review: `
    line are then the drawn lines."""
    def digest(t):
        return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]
    drawn = rec.get("drawn") or {}
    body = (body or "").replace("\r\n", "\n")
    legacy = {rec.get("part_line"), rec.get("fixes_line")} - {None, ""}
    lines = set(rec.get("drawn_lines") or [digest(x.rstrip()) for x in body.splitlines() if x.rstrip() in legacy or x.startswith("Review: ")])
    block_lines = set(rec.get("block_lines") or [])
    block = VERIFICATION.search(body)
    inside = {x.rstrip() for x in re.findall(r"(?m)^## .*$", block.group(0))} if block else set()
    blk = "\n".join(x.rstrip() for x in block.group(0).splitlines()) if block else ""
    had = body_sections(body)
    # a record an older driver drew holds the digest of `## Changes` with the Verification block in it
    kept = {h: t for h, t in had if h in drawn and drawn[h] not in (digest(t), digest(f"{t}\n{blk}"))}
    rec["drawn"] = {}
    text = render_body(rec, review)
    fresh = dict(body_sections(text))
    rec["drawn"] = {h: digest(t) for h, t in fresh.items()}
    rec["drawn_lines"] = [digest(x.rstrip()) for x in re.split(r"(?m)^## ", text, maxsplit=1)[0].splitlines() if x.strip()]
    rec["block_lines"] = [digest(x.rstrip()) for x in VERIFICATION.search(text).group(0).splitlines() if x.strip()]
    out = render_body(rec, review)
    for h, t in kept.items():
        if h in fresh:
            out = out.replace(fresh[h], t, 1)
    added = ["\n".join(x for x in t.splitlines() if digest(x) not in (lines | block_lines if h in inside else lines)).strip()
             for h, t in had if h not in fresh and h not in (drawn or DRAWN_HEADINGS)]
    if added:
        at = out.rindex("\n## Follow-up\n") + 1  # render_body draws it last, so a summary that quotes the heading comes before it
        out = out[:at] + "".join(t + "\n\n" for t in added) + out[at:]
    return out


def without_build_lines(text):
    return "\n".join(line for line in text.splitlines() if not line.startswith(BUILD_LINE)).strip()


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


def anchor_sections(body):
    """Each line of `body` and whether it sits under an `Anchors` heading, as the anchor gate scopes one: a `#` line outside a
    fence opens or ends the section."""
    anchors, fence = False, ""
    for line in body.splitlines():
        if marker := re.match(r"\s*(`{3,})(.*)$", line):  # as the gate reads a fence: a bare run at least as long closes it
            if not fence:
                fence = marker.group(1)
            elif len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                fence = ""
        if line.startswith("#") and not fence:
            anchors = bool(re.match(r"#+\s*Anchors\b", line))
        yield line, anchors


def anchor_fragments(line):
    """The quoted fragments of an anchor line that the gate checks: 12 to 120 characters, escapes undone, edge backticks cut."""
    frags = [f.replace('\\"', '"') for f in re.findall(r'"((?:[^"\\]|\\.)*)"', line)]
    return [f.strip("`") for f in frags if 12 <= len(f) <= 120]


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
    out, moved = [], 0
    for line, anchors in anchor_sections(body):
        m = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s[^`\r\n]*`)([^`\r\n]+)(`.*)$", line) if anchors else None
        span = m.group(2) if m else ""
        base = re.split(r"[:#]", span, 1)[0]  # the gate cuts a span at its first : or # (`path:171`, `path#L171`)
        if span and any(d == base or d.endswith("/" + base) for d in dead):
            files = None
            for frag in anchor_fragments(line):
                hits = {entry[len(ref) + 1:] for entry in e.git("grep", "-F", "-l", "-z", "-e", frag, ref, check=False).out.split("\0") if entry.startswith(ref + ":")}
                files = hits if files is None else files & hits
            if files and len(files) == 1 and (new := next(iter(files))) != base and Path(new).suffix == Path(base).suffix:
                line, moved = m.group(1) + new + span[len(base):] + m.group(3), moved + 1
        out.append(line)
    if not moved:
        return body
    sha = e.git("rev-parse", "--short=7", ref).out.strip()
    return re.sub(r"(Anchors\s*@\s*)[0-9a-f]{7,40}", lambda m: m.group(1) + sha, "\n".join(out) + ("\n" if body.endswith("\n") else ""))
