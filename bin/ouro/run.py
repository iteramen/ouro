"""`run N`: the fixed sequence from the verified issue to the landed change, resumable from the tracker.

Every transition reads the tracker, the branch and the pull request first and continues from the
first state not yet reached; nothing is kept between invocations. The model is asked only at the
BUILD, REVIEW and FOLLOWUP nodes; everything else is a call to a script under bin/ or to git/gh.
"""
import json
import os
import posixpath
import re
import shlex
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import briefs, node, sh
from .sh import DriverError, Refused, Stopped, argv_for, say, script

FIX_CAP = 3
ORDER = ["direct", "express", "standard", "full"]
MODE = {"express": "light", "standard": "sized", "full": "probe"}
STOP_REASONS = ("review cap", "open decision", "dead anchor", "gate uncovered")
FULL_PREFIXES = (".github/workflows/", ".github/actions/", "actions/", "hooks/", "release/", ".claude/ouro")
EXEC_EXT = (".ps1", ".py", ".sh", ".yml", ".yaml", ".json", ".toml", ".js", ".ts", ".cs", ".cmd", ".bat")
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
    verdict = "FINDINGS" if any(sev(f) in ("H", "M") for f in findings) else ("FINDINGS" if findings else "CLEAN")
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
    out += [f"- BUILD ran `{g.get('cmd')}`: exit {g.get('exit')}" for g in rec.get("build_gates") or []]
    if rec.get("note"):
        out.append(f"- {rec['note']}")
    out += [f"- {review}", ""]
    if rec.get("usage"):
        out += [rec["usage"], ""]
    out += ["</details>", "", "## Follow-up", *[f"- {x}" for x in rec.get("residue", [])], f"<!-- ouro-record: {raw} -->", ""]
    return "\n".join(out)


def without_build_lines(text):
    return "\n".join(l for l in text.splitlines() if not l.startswith(BUILD_LINE)).strip()


def acceptance_commands(body):
    """Backticked spans that start like a runner, in the sections whose heading names acceptance, tests or verification."""
    out, level = [], 0
    text = []
    for line in body.splitlines():
        h = re.match(r"(#{2,3})\s+(.*)", line)
        if h and (not level or len(h.group(1)) <= level):
            level = len(h.group(1)) if re.search(r"\b(acceptance|tests?|testing|verif\w*)\b", h.group(2), re.I) else 0
        elif level:
            text.append(line)
    for span in re.findall(r"`([^`\n]+)`", "\n".join(text)):
        span = span.strip()
        if span.startswith(ACCEPTANCE) and span not in out:
            out.append(span)
    return out


def read_record(body):
    m = RECORD.search(body or "")
    return json.loads(m.group(1)) if m else {}


def review_plan(value, size):
    """(the reviewer that reads first, Copilot after it) for a ship.review value and a Size: the not-fused rows of the binding doc's table."""
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
            known += [m["path"] for m in self.build_facts()["manual"] if m["path"] not in known]
        return known

    def manual_block(self):
        paths = self.manual_known()
        if not paths:
            return "(none)"
        return ("\n".join(f"- `{p}`" for p in paths) + "\n\nYou cannot edit these: the harness refuses an edit under .claude/. Build everything else, "
                "and answer `manual` with one entry per path holding the exact edit a person makes there, as a unified diff or the whole new content.")

    def claude_path(self, fp):
        """A path under this worktree's .claude/ (never its worktrees), worktree-relative, else None: what makes a node's path or a denial's a manual step."""
        if not fp:
            return None
        p = Path(str(fp))
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(self.worktree.resolve())
            except ValueError:
                return None
        rel = p.as_posix()
        return rel if rel.startswith(".claude/") and not rel.startswith(".claude/worktrees/") else None

    def manual_steps(self, ans):
        """The record's manual steps from one answer: the node's `manual` entries under .claude/, then each planned or denied .claude/ path it gave no edit for."""
        steps = []
        for m in ans.get("manual") or []:
            rel = self.claude_path(m.get("path")) if isinstance(m, dict) else None
            if rel and rel not in [s["path"] for s in steps]:
                steps.append({"path": rel, "edit": str(m.get("edit") or "")})
        denied = []
        for d in ans.get("_denied") or []:
            rel = self.claude_path((d.get("tool_input") or {}).get("file_path")) if isinstance(d, dict) else None
            if rel and rel not in denied:
                denied.append(rel)
        if denied:
            say(f"  denied under .claude/: {', '.join(denied)}: manual steps")
        for p in self.manual_paths() + denied:
            if p not in [s["path"] for s in steps]:
                steps.append({"path": p, "edit": ""})
        return steps

    def spec_comments(self):
        """The newest trusted `**Triage**` comment and every trusted comment after it: what amends the body (contract section 8)."""
        trusted = self.env.trusted()
        cs = [c for c in self.issue.get("comments") or [] if str((c.get("author") or {}).get("login") or "").lower() in trusted]
        since = newest_trusted_triage(cs, trusted)
        cs = [c for c in cs if since is not None and (when(c.get("createdAt")) or MIN_TIME) >= since]
        return "\n\n".join(f"{(c.get('author') or {}).get('login')} at {c.get('createdAt')}:\n{str(c.get('body') or '').strip()}" for c in cs) or "(none)"

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
        say(f"stopped: {reason}")
        raise Stopped(code)

    # ---- transitions
    def go(self):
        e = self.env
        self.read_issue()
        self.plan_part()
        say(f"#{self.n} {self.title}")
        say(f"  class {self.flow}, size {self.size}, branch {self.branch}")
        e.git("fetch", "origin", check=False)
        pr = self.find_pr()
        branch_known = self.branch_exists()
        lane_pr = self.find_pr(f"direct/{self.n}", states=("OPEN",)) if self.flow == "direct" and pr is None else None
        say(f"[1] bind: {e.slug}, default branch {e.default}, policy {e.lookup('ship.policy')}, review {e.lookup('ship.review')}")
        if pr is None and not branch_known and lane_pr is None:
            if self.issue.get("state") == "CLOSED":
                say(f"[2] verify: #{self.n} is closed: nothing to do")
                return 0
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
        else:
            say("[2] verify: skipped, the run already has a branch or a pull request")
            if not (pr and pr["state"] == "MERGED"):
                why = self.label_problem()
                if why:
                    say(f"stopped: {why}")
                    raise Stopped(3)
                self.check_spec_trust()  # the body the nodes read on this pass, not the one the fresh run checked
        if lane_pr:
            return self.lane_resume(lane_pr)
        if pr and pr["state"] == "MERGED":
            return self.after_merge(pr)
        if e.dry:
            return self.plan(pr, branch_known)
        self.ensure_worktree()
        return self.drive(pr)

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
        """The question of a node's stop answer, whether it is an object or a bare string."""
        return str((stop.get("question") if isinstance(stop, dict) else None) or (stop if isinstance(stop, str) else json.dumps(stop)))

    def checkpoint(self):
        """A checkpoint's run: the same spec checks as a build, then a read-only node whose finding is posted; no branch, worktree or commit."""
        e = self.env
        trusted = e.trusted()
        comments = self.issue.get("comments") or []
        found = newest_trusted_triage(comments, trusted, "**Checkpoint finding**")
        if found is not None and found > (newest_trusted_triage(comments, trusted) or MIN_TIME):
            raise Refused(f"#{self.n} already has a `**Checkpoint finding**` newer than its newest `**Triage**`: "
                          "triage converts the issue, or a new `**Triage**` comment reopens the checkpoint")
        self.verify(checkpoint=True)
        if e.dry:
            say("[4] CHECKPOINT: would run node CHECKPOINT (read tools only) and post its finding as a `**Checkpoint finding**` comment")
            return 0
        prompt = briefs.render("checkpoint", number=self.n, title=self.title, body=self.body,
                               anchors=briefs.anchors_block(e.main, self.body), contract=briefs.contract_sections(e, (3,)))
        model = e.lookup("models.builder", "sonnet")
        say(f"[4] CHECKPOINT node ({model})")
        ans = node.run_node("CHECKPOINT", prompt, model, list(node.READ_TOOLS), str(e.main), node.TIMEOUTS["CHECKPOINT"])
        if ans.get("stop"):
            self.stop_issue("open decision", self.stop_text(ans["stop"]), 3)
        finding = str(ans.get("finding") or "").strip()
        if not finding:
            raise DriverError("node CHECKPOINT answered no finding")
        e.comment_issue(self.n, f"**Checkpoint finding**\n\n{finding}\n")
        say(f"[4] finding posted on #{self.n}")
        return 0

    def verify(self, checkpoint=False):
        e = self.env
        why = self.label_problem(checkpoint)
        if why:
            raise Refused(why)
        self.check_spec_trust()
        args =["-Issue", str(self.n)] + ([] if e.dry else ["-Demote"])
        r = sh.run(script("Test-AgentReadyShape.ps1", *args), cwd=e.main)
        if r.code != 0:
            if "SHAPE:" not in r.out:
                raise DriverError(f"the shape gate failed: {sh.cmdline(r.argv)}: {r.text}")
            say("[2] verify: shape finding")
            say(r.out.strip())
            raise Refused(f"#{self.n} fails the shape gate" + ("" if e.dry else ": demoted to needs-triage, comment posted"))
        r = sh.run(script("Test-AgentReadyAnchors.ps1", "-Ref", f"origin/{e.default}"), cwd=e.main)
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
        if (self.worktree / ".git").exists():
            say(f"[3] worktree: {self.worktree} exists")
            return
        # git check-ignore refuses a path beyond a symlink, so a symlinked .claude/worktrees is checked at its target
        real = self.worktree.resolve()
        if not real.is_relative_to(e.main.resolve()):
            raise DriverError(f"{self.worktree} resolves to {real}, outside the repository: point .claude/worktrees at a directory inside it")
        rel = real.relative_to(e.main.resolve()).as_posix()
        self.worktree.mkdir(parents=True, exist_ok=True)
        r = e.git("check-ignore", "-q", "--no-index", rel, check=False)
        if r.code != 0:
            shutil.rmtree(self.worktree, ignore_errors=True)
            raise DriverError(f"{rel} is not ignored (git check-ignore exit {r.code}): add {rel.rsplit('/', 1)[0]}/ to the repo's .gitignore")
        local = e.git("rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}", check=False).code == 0
        remote = bool(e.git("ls-remote", "--heads", "origin", f"refs/heads/{self.branch}", check=False).out.strip())
        if local:
            argv = ["worktree", "add", rel, self.branch]
        elif remote:
            argv = ["worktree", "add", rel, "-b", self.branch, f"origin/{self.branch}"]
        else:
            argv = ["worktree", "add", rel, "-b", self.branch, f"origin/{e.default}"]
        say(f"[3] worktree: git {' '.join(argv)}")
        e.git(*argv)

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
        if self.commits_past_base():
            self.rederive()  # a resumed run re-reads the tightening from the diff; it is kept nowhere else
        for _ in range(40):
            pr = self.find_pr() if pr is None or pr["state"] != "MERGED" else pr
            if pr and pr["state"] == "MERGED":
                return self.after_merge(pr)
            if self.commits_past_base() == 0:
                self.build()
                continue
            head = self.head()
            records = self.records(pr)
            at_head = [r for r in records if r["sha"] == head]
            if head not in self.gated and (pr is None or not at_head or head in self.committed_here):
                red = self.run_gates(head)
                self.gated.add(head)
                if red:
                    self.fix_round(f"gate `{red[0]}` exited {red[1]}; the tail of its log:\n{red[2]}", records)
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

    def build_commands(self):
        """(the brief's command list, the commands the node may run): contract section 4's Gates bullet."""
        runnable = acceptance_commands(self.body)
        lines = [f"- `{c}` (acceptance)" for c in runnable]
        for run, g in self.area_gates():
            paths = g.get("paths")
            if self.notice(g) or not (self.locally_run(run, g) or paths):
                continue
            when = f" (run it when the change touches: {', '.join(paths)})" if paths and not self.locally_run(run, g) else ""
            lines.append(f"- `{run}`{when}")
            runnable.append(run)
        return "\n".join(lines) or "(none: the pull request's checks are the run)", runnable

    def build(self, findings=None, threads=None, track=True, record_ok=False):
        """Ask the BUILD node and commit what it left; None when a Copilot round declined every thread and changed nothing.

        `record_ok`: a review's fix round may change no file, since its findings may be the record's sentences; any other build must change the tree."""
        e = self.env
        cmds, gates = self.build_commands()
        contract = briefs.contract_sections(e, (4, 7))
        prompt = briefs.render("build", number=self.n, title=self.title, body=self.body, part=self.part_scope("build"),
                               comments=self.spec_comments(), manual=self.manual_block(), anchors=briefs.anchors_block(self.worktree, self.body),
                               implement_docs=briefs.implement_docs(e, self.worktree), flow=self.flow,
                               commands=cmds, findings=findings or "(none)", contract=contract)
        tools = ["Edit", "Write", "Read", "Grep", "Glob"] + [f"Bash(git {c} *)" for c in ("rm", "mv", "status", "diff", "log", "ls-files")] + [f"Bash({c})" for c in gates]
        model = e.lookup("models.builder_trivial" if "trivial" in self.labels else "models.builder", "sonnet")
        say(f"[4] BUILD node ({model})" + (" fix round" if findings else ""))
        ans = node.run_node("BUILD", prompt, model, tools, str(self.worktree), node.TIMEOUTS["BUILD"])
        if ans.get("stop"):
            self.stop_issue("open decision", self.stop_text(ans["stop"]), 3)
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
        msg += f"\n{BUILD_LINE}{json.dumps(facts)}\n"
        f = e.body_file("commit-msg.txt", msg)
        self.wt_git("add", "-A")
        self.wt_git("commit", "-q", "--allow-empty", "-F", f)
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
        """Whether the driver runs a selected gate itself: one the checks only skip, or every one in the direct lane, which has no pull request."""
        return self.locally_run(run, g) or flow == "direct"

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
        r = self.wt_git("diff", "--quiet", f"origin/{self.env.default}...HEAD", "--", *entries, check=False)
        return r.code != 0

    def run_gates(self, head):
        chosen, skipped = self.select_gates(self.flow)
        rec_gates = []
        for i, (run, _) in enumerate(chosen):
            argv = shlex.split(run)
            exe = shutil.which(argv[0]) if argv else None
            if not exe:
                self.stop_issue("gate uncovered", f"The driver cannot run the gate `{run}`: `{argv[0] if argv else ''}` is not on PATH.", 3)
            argv[0] = exe
            r = sh.run(argv, cwd=self.worktree)
            log = self.env.scratch / f"gate-{i}.log"
            sh.write_bytes(log, r.out + r.err)
            say(f"[5] gate `{run}`: exit {r.code}")
            rec_gates.append({"cmd": run, "exit": r.code, "sha": head})
            if r.code != 0:
                tail = "\n".join((r.out + r.err).strip().splitlines()[-40:])
                self.gate_log = rec_gates
                return (run, r.code, tail)
        self.gate_log = rec_gates
        self.skipped = skipped
        return None

    # ---- push and the pull request
    def sync_and_push(self, records):
        e = self.env
        r = sh.run(script("Sync-Branch.ps1"), cwd=self.worktree)
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
        p = sh.run(script("Push-Branch.ps1", *args), cwd=self.worktree)
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
        f = e.body_file("pr-body.md", render_body(rec, review))
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
        """What the BUILD node answered that a later invocation needs, from the `Ouro-Build:` line of one commit."""
        for line in reversed(self.wt_git("log", "-1", "--format=%B", sha).out.splitlines()):
            if line.startswith(BUILD_LINE):
                try:
                    return json.loads(line[len(BUILD_LINE):])
                except ValueError as ex:
                    raise DriverError(f"the Ouro-Build line of {sha[:7]} is not JSON: {ex}")
        return {}

    def build_facts(self):
        """The record's door, blast, summary, residue and build_gates: the newest BUILD answer on the branch, which describes the whole branch; its manual steps, the union over the branch's answers."""
        shas = self.wt_git("rev-list", f"origin/{self.env.default}..HEAD").out.split()
        facts = [(s, self.commit_facts(s)) for s in shas]
        sha, f = next(((s, x) for s, x in facts if x), (shas[-1], {}))  # no facts anywhere: the first commit's body
        manual = {}
        for _, x in reversed(facts):  # oldest first: a later answer's edit replaces an earlier one, a later silence keeps it
            for m in x.get("manual") or []:
                if isinstance(m, dict) and m.get("path"):
                    manual[m["path"]] = {"path": m["path"], "edit": str(m.get("edit") or "") or manual.get(m["path"], {}).get("edit", "")}
        return {"door": str(f.get("door") or "") or "unstated", "blast": str(f.get("blast_radius") or "") or "unstated",
                "summary": without_build_lines(self.wt_git("log", "-1", "--format=%b", sha).out),
                "residue": [str(x) for x in f.get("residue") or []], "build_gates": f.get("gates_run") or [], "manual": list(manual.values())}

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
        f = e.body_file("pr-body.md", render_body(rec, review_line(records, bool(self.primary))))
        e.write("update the pull request body", argv_for("gh", "pr", "edit", str(pr["number"]), "-R", e.slug, "--body-file", f))

    # ---- review
    def review(self, pr, head, records):
        e = self.env
        rnd = max([r["round"] for r in records], default=0) + 1
        ans = self.reader_round(rnd, head, records) if self.primary == "reader" else None
        if ans is None:
            ans = self.node_review(pr, head, records, rnd)
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
        prior = self.verification(records, head)
        prompt = briefs.render("review", mode=MODE.get(self.flow, "sized"), number=self.n, head=head, title=self.title, part=self.part_scope("review"),
                               range=f"origin/{e.default}...{head}", diff=diff, body=self.body, comments=self.spec_comments(), record=record,
                               verification=prior,
                               reviewer_prompt=rp.read_text(encoding="utf-8") if rp.is_file() else "(REVIEWER-PROMPT.md is not in this tree)",
                               contract=briefs.contract_sections(e, (7,)))
        model = e.lookup("models.reviewer", "opus")
        say(f"[7] REVIEW node round {rnd} ({model}, {MODE.get(self.flow, 'sized')})")
        tools, denied, cwd, copy = list(node.READ_TOOLS), None, self.worktree, None
        if self.flow == "full":
            copy = e.scratch / f"review-{rnd}"
            e.git("worktree", "add", "--detach", str(copy), head)
            tools, denied, cwd = tools + ["Bash"], REVIEW_DENIED, copy
        try:
            return node.run_node("REVIEW", prompt, model, tools, str(cwd), node.TIMEOUTS["REVIEW"], denied)
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
        args = ["--cli", cli, "--range", f"origin/{e.default}...{head}", "--goal", self.reader_goal()]
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
            r = sh.run(script("external-audit.py", *args), cwd=self.worktree, timeout=700)
            if r.code != 3:
                break
        out = re.search(r"external-audit: session (\S+) round (\d+) -> (.+)", r.out)
        sess = re.search(r"--resume (\S+) --digest", r.out)
        verdict = Path(out.group(3).strip()) / "verdict.json" if out else None
        if r.code not in (0, 2) or not (verdict and verdict.is_file() and sess):
            self.add_note(f"The external reader ({cli}) was unavailable (exit {r.code}), so the REVIEW node reviewed in its place.")
            return None
        found = json.loads(verdict.read_text(encoding="utf-8"))
        return {"verdict": "CLEAN" if r.code == 0 else "FINDINGS", "findings": reader_findings(found),
                "files_read": found.get("files_read") or [], "reader": {"cli": cli, "session": sess.group(1), "round": len(spent) + 1}}

    def reader_goal(self):
        """What the change must do, for the external reader: the title, the Deliverable section and the newest comments that amend it, each cut on its own so a long one cannot push the others out."""
        want = re.search(r"(?ms)^## Deliverable\s*\n(.*?)(?=^## |\Z)", self.body)
        comments = self.spec_comments()
        cut = " (earlier ones cut)" if len(comments) > 3000 else ""
        return f"{self.title}. {(want.group(1).strip() if want else '')[:3000]}\n\nComments that amend it, the newest last{cut}: {comments[-3000:]}"

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
    def checks(self, pr, head):
        e = self.env
        n = pr["number"]
        r = sh.run(script("Watch-PullRequestChecks.ps1", "-Pr", str(n)), cwd=self.worktree, timeout=watch_limit())
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
        retried = False
        for run_id, _ in bad:
            attempt = e.gh_json("run", "view", run_id, "--json", "attempt").get("attempt", 1)
            if attempt == 1:
                e.write(f"re-run failed jobs of run {run_id} (a flake is a failure whose re-run succeeds)",
                        argv_for("gh", "run", "rerun", run_id, "-R", e.slug, "--failed"))
                retried = True
        if retried and not e.dry:
            r2 = sh.run(script("Watch-PullRequestChecks.ps1", "-Pr", str(n)), cwd=self.worktree, timeout=watch_limit())
            if r2.code is None:
                return "running"
            if r2.code == 0:
                say("[8] the failed check passed on re-run: a flake")
                return "green"
        log = e.gh("run", "view", bad[0][0], "-R", e.slug, "--log-failed", check=False).text
        self.fix_round(f"check run {bad[0][0]} failed; the tail of its log:\n" + "\n".join(log.splitlines()[-60:]), self.records(pr))
        return "fix"

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
        view = e.gh_json("pr", "view", str(n), "--json", "reviewDecision,headRefOid")
        last = [r for r in records if r["sha"] == head]
        clean = (not self.primary) or (bool(last) and not needs_fix(last[-1]))
        n_green = len([c for c in e.gh_json("pr", "checks", str(n), "--json", "name,bucket") if c.get("bucket") == "pass"])
        line = review_line(records, bool(self.primary))
        facts = f"Head: {head}\nChecks: {n_green} green\n{line}\nClass: {self.flow}\nPolicy: {policy}"
        if last and last[-1].get("ruled"):
            facts += f"\nRuled: {last[-1]['ruled']}, round {last[-1]['round']}"
        if view.get("reviewDecision") == "CHANGES_REQUESTED":
            say(f"waiting: a review on #{n} requests changes")
            return 0
        if view.get("headRefOid") != head:
            raise DriverError(f"the head {view.get('headRefOid')} does not match the reviewed head {head}")
        if not clean:
            raise DriverError(f"the last verdict at {head} is not clean")
        rec = read_record(e.gh_json("pr", "view", str(n), "--json", "body")["body"]) or {}
        steps = rec.get("manual") or []
        if steps or not may_merge(policy, classes, self.labels, self.flow):
            if not any(c.get("body", "").startswith("**Ready for merge**") and head in c.get("body", "") for c in self.pr_comments(pr)):
                manual = ("Manual steps, applied on the branch before the merge:\n" + "\n".join(f"- `{m.get('path')}`" for m in steps) + "\n\n") if steps else ""
                e.comment_pr(n, f"**Ready for merge**\n\n{manual}{facts}\n")
            say(f"[9] ready for merge: #{n} waits for the owner's merge on GitHub" + (", after the manual steps" if steps else ""))
            return 0
        subject = re.sub(r"\s*\(#\d+\)\s*$", "", pr["title"]) + f" (#{n})"
        tail = f"Fixes #{self.n}" if self.fixes else f"Part {self.k}/{self.parts_n} of #{self.n}"
        bf = e.body_file("merge-body.md", (rec.get("summary", "") + "\n\n" + "\n".join(f"- {c}" for c in rec.get("changes", [])) + f"\n\n{tail}\n"))
        sha = self.merge_pr(n, subject, bf, self.worktree)
        if sha is None:
            return self.conflicted(head, 9, "the merge")
        e.comment_pr(n, f"**Landed unattended** by ouro run at {sha}: checks {n_green} green, {line}, class {self.flow}" + (f"\n{facts.splitlines()[-1]}" if "\nRuled: " in facts else "") + "\n")
        say(f"[9] merged #{n} as {sha}")
        return self.after_merge(self.find_pr())

    @staticmethod
    def merge_pr(n, subject, body_file, cwd):
        """The merge commit; None when the script refuses a CONFLICTING pull request, which the caller syncs."""
        r = sh.run(script("Merge-PullRequest.ps1", "-Pr", str(n), "-Subject", subject, "-BodyFile", body_file, "-Linear", "no"), cwd=cwd)
        if r.code != 0:
            if "'CONFLICTING " in r.text:
                return None
            raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}")
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
        if self.flow == "full" and sha:
            r = sh.run(script("Watch-PullRequestChecks.ps1", "-Sha", sha), cwd=e.main, timeout=watch_limit())
            if r.code not in (0, None):
                raise DriverError(f"the default branch's run on {sha} is not green: {r.text}")
            say("[10] the default branch's runs are green" if r.code == 0 else "[10] the default branch's runs are still running")
        if self.branch_exists() or self.worktree.exists():
            r = sh.run(script("Remove-LandedBranch.ps1", "-Pr", str(n)), cwd=e.main)
            if r.code != 0:
                raise DriverError(f"{sh.cmdline(r.argv)} failed (exit {r.code}): {r.text}")
            say("[10] branch and worktree removed")
        r = sh.run(script("Test-AgentReadyAnchors.ps1", "-Ref", f"origin/{e.default}"), cwd=e.main)
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
            ans = node.run_node("FOLLOWUP", prompt, e.lookup("models.builder", "sonnet"), list(node.READ_TOOLS), str(e.main), node.TIMEOUTS["FOLLOWUP"])
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
        r = sh.run(script("ouro-binding.py", "direct", *paths, "--binding", str(self.env.main / ".claude" / "ouro.toml")), cwd=self.env.main)
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
        """Whether the main checkout is ahead of origin's default branch by commits that all hold `Fixes #<N>`: the lane's own, not yet pushed."""
        e = self.env
        base = f"origin/{e.default}"
        if e.git("merge-base", "--is-ancestor", base, "HEAD", check=False).code != 0:
            return False
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
            self.build(track=False)
            if self.build_facts()["manual"]:
                say("[2a] direct: the build has manual steps, which the lane cannot carry")
                return self.lane_fallback()
        sha = self.lane_ship(before)
        if sha is None:
            return self.lane_fallback()
        return self.lane_after(sha)

    def lane_resume(self, pr):
        """An open pull request on direct/<N>: the lane goes on from its checks, with no build."""
        e = self.env
        say(f"[2a] direct: pull request #{pr['number']} is open on direct/{self.n}: the lane resumes at its checks")
        if e.dry:
            say("  would: watch its checks, merge it, watch the default branch's run on the merge commit")
            return 0
        self.in_lane, self.worktree = True, e.main
        body = e.body_file("direct-pr.md", (pr.get("body") or "").strip() + "\n")
        return self.lane_after(self.lane_finish(pr, pr["title"], body))

    def lane_after(self, sha):
        """The default branch's run on the shipped `sha`: green ends the lane, a red run gets one fix commit, a second red is reverted."""
        e = self.env
        state, url, tail = self.lane_watch(sha)
        if state != "red":
            return 0
        why = self.direct_blocked()
        if why:
            self.stop_issue("open decision", f"The default branch's run on the direct commit {sha[:7]} is red ({url}), and the lane's "
                            f"fix cannot be built in the main checkout: {why}.", 3)
        say(f"[2a] direct: the default branch's run is red ({url}): one fix commit through the lane")
        self.build(findings=f"the default branch's run {url} is red at {sha[:7]}; the tail of its log:\n{tail}", track=False)
        fix = self.lane_ship(sha)
        if fix is None:
            return self.lane_fallback()
        state, url2, tail = self.lane_watch(fix)
        if state != "red":
            return 0
        say(f"[2a] direct: the fix's run is red too ({url2}): both commits are reverted through the lane")
        self.lane_revert([fix, sha], [url, url2])
        return 0

    def lane_ship(self, prev, gates=True):
        """Gates, then Push-Direct.ps1 and its vehicle, for the commits past `prev`: the sha now on the default branch, or None when nothing was pushed."""
        e = self.env
        head = e.git("rev-parse", "HEAD").out.strip()
        paths = [x for x in e.git("diff", "--name-only", "-z", f"{prev}..HEAD").out.split("\0") if x]
        if sh.run(script("ouro-binding.py", "direct", *paths, "--binding", str(e.main / ".claude" / "ouro.toml")), cwd=e.main).code != 0:
            say("[2a] direct: the diff reaches a path outside [ship].direct")
            return None
        red = self.run_gates(head) if gates else None
        if red:
            say(f"[2a] direct: gate `{red[0]}` exited {red[1]}; nothing is pushed")
            return None
        p = sh.run(script("Push-Direct.ps1", "-Issue", str(self.n)), cwd=e.main)
        out = parse_direct(p.out)
        if p.code != 0 and not out["pushed"]:
            say(f"[2a] direct: Push-Direct.ps1 stopped before a push: {p.text.splitlines()[-1] if p.text else p.code}")
            return None
        if p.code != 0:
            e.git("fetch", "origin", check=False)
            say("[2a] direct: the read-back after the push failed; origin is fetched and the run goes on")
        elif out["vehicle"] not in ("push", "pr"):
            raise DriverError(f"{sh.cmdline(p.argv)} exited 0 without a vehicle line: {p.text}")
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
        w = sh.run(script("Watch-PullRequestChecks.ps1", "-Pr", str(n)), cwd=e.main, timeout=watch_limit())
        if w.code is None:
            say(f"[2a] direct: the checks on #{n} are still running: invoke `run {self.n}` again")
            raise Stopped(0)
        if w.code != 0:
            raise DriverError(f"the checks on #{n} are not green: {w.text}")
        sha = self.merge_pr(n, f"{subject} (#{n})", body, e.main)
        if sha is None:
            raise DriverError(f"#{n} conflicts with {e.default}: sync the direct/{self.n} branch by hand, then invoke `run {self.n}` again")
        e.git("fetch", "origin", check=False)
        if e.git("rev-parse", "--abbrev-ref", "HEAD").out.strip() == e.default:
            e.git("merge", "-q", "--ff-only", f"origin/{e.default}", check=False)
        e.git("branch", "-D", branch, check=False)
        say(f"[2a] direct: #{n} merged as {sha}")
        return sha

    def lane_watch(self, sha):
        """(state, run url, log tail): the default branch's run on `sha` is green, still running, or red."""
        e = self.env
        r = sh.run(script("Watch-PullRequestChecks.ps1", "-Sha", sha), cwd=e.main, timeout=watch_limit())
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
        return "red", url, "\n".join(log.splitlines()[-60:])

    def lane_revert(self, shas, urls):
        """Two red runs: one revert commit of both, pushed the same way, the issue reopened if it closed, then the stop."""
        e = self.env
        r = e.git("revert", "--no-commit", *shas, check=False)
        if r.code != 0:
            e.git("revert", "--abort", check=False)
            raise DriverError(f"cannot revert {' '.join(s[:7] for s in shas)}: {r.text}")
        tip = e.git("rev-parse", "HEAD").out.strip()
        e.git("commit", "-q", "-F", e.body_file("revert-msg.txt", f"Revert the direct commits for #{self.n}\n\nThe default branch's runs on both were red: {urls[0]} and {urls[1]}.\n"))
        pushed = self.lane_ship(tip, gates=False)
        if e.gh_json("issue", "view", str(self.n), "--json", "state")["state"] == "CLOSED":
            e.write(f"reopen #{self.n}", argv_for("gh", "issue", "reopen", str(self.n), "-R", e.slug))
        self.stop_issue("open decision", f"The direct commit {shas[1][:7]} and its one fix {shas[0][:7]} each left the default branch's run red: "
                        f"{urls[0]} and {urls[1]}.\n\n" + (f"Both are reverted in {pushed[:7]}." if pushed else
                        "Both are reverted in a commit the lane could not push; it is in the main checkout, unpushed."), 3)

    def lane_fallback(self):
        """Nothing was pushed: the commit moves to a worktree branch, the main checkout goes back to origin's tip, the run goes on as express or tighter."""
        e = self.env
        say("[2a] direct: the commit moves to a worktree and the run continues as express")
        e.git("branch", self.branch, "HEAD")
        self.in_lane = False
        self.worktree = e.main / ".claude" / "worktrees" / self.dirname
        self.ensure_worktree()
        e.git("reset", "-q", "--hard", f"origin/{e.default}")
        self.flow = "express"
        self.rederive()
        return self.drive(None)

    # ---- dry run
    def plan(self, pr, branch_known):
        e = self.env
        say("[3] worktree and branch: " + (f"exists ({self.branch})" if branch_known else
            f"would: git worktree add .claude/worktrees/{self.dirname} -b {self.branch} origin/{e.default}"))
        if pr:
            say(f"[4] BUILD: skipped, pull request #{pr['number']} exists")
        else:
            say(f"[4] BUILD: would run node BUILD ({e.lookup('models.builder', 'sonnet')}) in the worktree, then git add -A and git commit -F <message>")
        chosen = [r for r, g in self.area_gates() if self.runs_here(r, g, self.flow)]
        say("[5] gates: " + ("; ".join(chosen) if chosen else "none selected for the class beyond the pull request's checks") + " (a gate with paths runs only when the diff touches them)")
        say(f"[6] push: {sh.cmdline(script('Sync-Branch.ps1'))} then {sh.cmdline(script('Push-Branch.ps1'))}; "
            f"would open a draft pull request" + (" at convergence" if self.flow == "express" else " at the first push"))
        say(f"[7] REVIEW: ship.review = {e.lookup('ship.review')}, size {self.size}: first reader {self.primary or 'none'}"
            f"{', then Copilot on the pull request' if self.copilot else ''}; mode {MODE.get(self.flow, 'sized')}, up to {FIX_CAP} fix rounds")
        say(f"[8] checks: {sh.cmdline(script('Watch-PullRequestChecks.ps1', '-Pr', '<PR>'))}")
        policy = e.lookup("ship.policy")
        ok = may_merge(policy, e.lookup("ship.unattended_classes"), self.labels, self.flow) and not self.manual_paths()
        say(f"[9] merge: policy {policy}, class {self.flow}: " + (f"would run {sh.cmdline(script('Merge-PullRequest.ps1', '-Pr', '<PR>', '-Subject', '<subject> (#<PR>)', '-BodyFile', '<file>', '-Linear', 'no'))}"
                                                           if ok else "would post **Ready for merge** and exit 0"))
        say(f"[10] after the merge: {sh.cmdline(script('Remove-LandedBranch.ps1', '-Pr', '<PR>'))}; "
            f"{sh.cmdline(script('Test-AgentReadyAnchors.ps1', '-Ref', 'origin/' + e.default))}")
        say("[11] FOLLOWUP: would run only when the record's Follow-up section or an unfixed L finding is not empty")
        say("[12] close: " + (f"the squash body's Fixes #{self.n}" if self.fixes else f"would tick part {self.k}; the issue stays open"))
        return 0


def main(env, n, part):
    try:
        return Run(env, n, part).go()
    except Stopped as ex:
        return ex.args[0]
