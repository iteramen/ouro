#!/usr/bin/env python3
"""Report what became of every `agent-ready` promotion, per weekly cohort.

Usage: python3 loop-outcomes.py [--since YYYY-MM-DD] [--comment] [--from-cache DIR]

Run from the root of a `fetch-depth: 0` clone whose binding (.claude/ouro.toml) declares
`repo.slug`, `repo.default_branch` and `owner.ruling_approvers` (not empty);
`rolling_issues.loop_runs` is optional. The binding is read through `ouro-binding.py get` beside
this script. Prints a Markdown report and, with --comment, appends it as one comment on the issue
titled by `[rolling_issues].loop_runs` (created with the `umbrella` label when none exists; nothing
is posted, and the exit is 0, when the key is undeclared). Nothing else is written, and no comment
or issue body is ever edited. The report triggers nothing: it is report-only until three cohorts
have closed their window.

Data: GitHub through `gh api graphql` (issue timelines, comments and body edits; pull requests),
and git (the first-parent line of origin/<default_branch>, `git grep` and `git blame` at a given
sha), and the Actions run list through `gh api repos/<slug>/actions/runs` (a week of `created` per
query, paged) with `.../runs/<id>/attempts/<n>` for each earlier attempt of a re-run run: the job's
token needs `actions: read`. --from-cache DIR reads DIR/issues.json, DIR/prs.json and DIR/runs.json
(the run list, each run above attempt 1 carrying its earlier attempts under `attempts`), in the
shapes the fetch builds, instead of calling GitHub. --since sets the first promotion included (UTC
date); the default is the Monday of the ISO week eleven weeks before the current one, twelve weeks
in all. Label replay always reads each issue's whole timeline.

Definitions, all UTC:
 * Attempt: a `labeled agent-ready` on an issue that did not carry the label, replayed in time
   order. It runs until the issue's next promotion, or now. Cohort: the ISO week of the promotion
   and the Size (`S`, `M` or `none`) the body stated then: the first `Size:` line, case-sensitive,
   from the newest body revision edited no later than two minutes after the promotion, else the
   oldest revision, else the current body. An attempt on an issue that carried `checkpoint` ten
   minutes after the promotion is in the checkpoint cohort, reported in its own table.
 * Landing: a pull request with a line starting `Fixes #N`, with or without a leading backtick and
   in any case, names issue N. It landed when it merged (at mergedAt, on its merge commit) or
   when a first-parent commit's subject ends in its `(#PR)` (at the committer date, on that
   commit); the merge wins. An attempt's landing is the earliest one from its promotion up to the
   next promotion. Many attempts may share one landing commit (a fuse region, a batch).
 * Reversed: within W = 14 days after the landing, a first-parent commit whose message holds
   `This reverts commit <landing sha>` or whose subject is `Revert "<landing subject>"`, or a
   `bug`-labelled issue created in that window whose body cites an anchor (a list item whose first
   backticked span is a file, with double-quoted fragments of 12 to 120 characters and an
   `@ <sha>` stamp on the line or on the nearest heading above) whose fragment, found with
   `git grep` at the stamp, blames (`--first-parent`) to the landing commit. An anchor with no
   stamp, or whose stamp or file git cannot read, is not counted, and the report says how many.
   A `bug` whose title holds both `sweep` and `ledger line` (substrings, in any case: a cleanup-
   ledger sweep child) is left out of that set; a revert still reverses. The figure is an upper
   bound: a bug's anchors cite context lines around a defect as well as the defect, and each one
   that blames to a landing charges it.
 * Assisted: a comment by an author in `[owner].ruling_approvers`, on the issue or the landing
   pull request between the promotion and the landing, whose first line starts `**Ruling**`, or
   that precedes an `unlabeled needs-ruling` event in that window.
 * Refused: the `unlabeled agent-ready` ending the attempt has a `labeled needs-ruling` or
   `labeled needs-triage` within ten minutes either side. The reason is the newest comment from 60
   minutes before to 10 minutes after the removal whose first line, trimmed, is `**Stop:** `
   followed by one of `review cap`, `open decision`, `dead anchor` or `gate uncovered` (the stop
   comment of skills/execute/SKILL.md section 1); else a comment whose first line is `**Triage**`
   makes it a `triage reversal`; else `unclassified`. Parked: the removal comes with any other
   state label, or none, and counts as neither a delivery nor a refusal. Finding delivered
   (checkpoint cohort): a comment whose first line is `**Checkpoint finding**` within the attempt.
   Abandoned: the issue closed within the attempt with no landing and, for a checkpoint, no
   finding. An issue closed and later reopened within the attempt still reads abandoned.
   Open: none of these yet, with its queue age in days.
 * Precedence: landed (reversed, then assisted, then clean), finding delivered, refused, parked,
   abandoned, open. The reversal and assist rates are taken over all landings, so a landing both
   reversed and assisted counts in both.
 * Refusal precision: over resolved decision stops (open decision, dead anchor, gate uncovered;
   resolved means a later attempt exists or the issue closed); one re-promoted with a body that,
   trimmed, equals the body at the removal was unnecessary.

Quality table, after the outcome tables, one row per ISO week and an `all` row (report-only):
 * Review line: the first line of a landed PR's body, outside fenced blocks (a line opening with
   three backticks or tildes, to its closing fence, or to the end of the body), that matches the
   pattern skills/land/SKILL.md defines, REVIEW_PATTERN below, read multiline. A landed PR is one
   `landings()` finds, counted once however many issues it fixes, keyed by the week of the landing.
   A PR with no matching line is "no line", shown and never counted as clean; `Review: none` is a
   line. False sentences are totalled, averaged and shared over the PRs whose line holds a number
   there (`unread` is counted apart); H, M, L and the rounds (median, maximum; a PR is a region) are
   over every PR whose line has numbers.
 * CI flake rate: a failed attempt is a run attempt whose conclusion is `failure`, keyed by the week
   it started. It is flaky when a later attempt of the same run, or a later run (created after the
   attempt started) of the same workflow on the same head SHA, concluded `success`; else failed. A
   `cancelled` or `skipped` attempt is neither. Rate = flaky / (flaky + failed). A week's run query
   at the API's 1000-result cap is read a day at a time; a day at the cap, or a query returning
   fewer runs than it counts, is a failed read.

Exit 0 when the report printed (or was posted), 1 when any read or write failed (the message, with
the failing command's output, on stderr), 2 on a usage error. A failed read never reads as zero.
"""
import argparse, collections, json, os, re, statistics, subprocess, sys
from datetime import datetime, timedelta, timezone

# The floor every bin/*.py shares.
if tuple(sys.version_info[:2]) < (3, 11):
    sys.stderr.write(f"loop-outcomes.py needs Python 3.11+; this python is {sys.version_info[0]}.{sys.version_info[1]}\n")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
GH = ["gh"]
GIT_CWD = None
W = timedelta(days=14)
STOP_REASONS = ("review cap", "open decision", "dead anchor", "gate uncovered")
DECISION_STOPS = ("open decision", "dead anchor", "gate uncovered")
REASONS = STOP_REASONS + ("triage reversal", "unclassified")
OTHER_STATES = ("blocked", "idea", "human-ready", "umbrella", "architecture")
FIXES = re.compile(r"^`?Fixes #([0-9]+)", re.IGNORECASE | re.MULTILINE)
STOP_LINE = re.compile(r"\*\*Stop:\*\* (" + "|".join(STOP_REASONS) + ")")
SIZE_LINE = re.compile(r"^Size:[^\S\r\n]*(\S+)", re.MULTILINE)
LANDED_SUBJECT = re.compile(r"\(#([0-9]+)\)\s*$")
REVIEW_PATTERN = r"^Review: (?:none|rounds:([0-9]+) findings:H([0-9]+)/M([0-9]+)/L([0-9]+) false-sentences:([0-9]+|unread))[ \t\r]*$"
REVIEW_LINE = re.compile(REVIEW_PATTERN, re.MULTILINE)
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
RUNS_JQ = "{total: .total_count, runs: .workflow_runs}"
RUNS_CAP = 1000
STAMP = re.compile(r"@ ([0-9a-fA-F]{7,40})(?![0-9A-Za-z])")


class ReadError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def run(cmd, input_text=None, cwd=None):
    try:
        p = subprocess.run(cmd, input=None if input_text is None else input_text.encode("utf-8"),
                           capture_output=True, cwd=cwd)
    except OSError as e:
        raise ReadError(f"{' '.join(cmd[:3])}: {e}")
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        raise ReadError(f"{' '.join(cmd[:3])} failed (exit {p.returncode}): {out}{p.stderr.decode('utf-8', 'replace')}", p.returncode)
    return out


def binding(key, optional=False):
    cmd = [sys.executable, os.path.join(HERE, "ouro-binding.py"), "get", key]
    try:
        value = run(cmd, cwd=GIT_CWD).strip()
    except ReadError as e:
        if optional and "no such key" in str(e):
            return None
        raise
    if not value:
        raise ReadError(f"{key} resolved to an empty value")
    return value


def gql(query, **variables):
    cmd = GH + ["api", "graphql", "-f", "query=" + query]
    for k, v in variables.items():
        if v is not None:
            cmd += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    data = json.loads(run(cmd))
    if "errors" in data:
        raise ReadError("gh api graphql returned errors: " + json.dumps(data["errors"])[:2000])
    return data["data"]


TIMELINE = """nodes { __typename
  ... on LabeledEvent { createdAt actor { login } label { name } }
  ... on UnlabeledEvent { createdAt actor { login } label { name } }
  ... on ClosedEvent { createdAt actor { login } } }
pageInfo { hasNextPage endCursor }"""
EDITS = "nodes { editedAt diff } pageInfo { hasNextPage endCursor }"
COMMENTS = "nodes { createdAt author { login } body } pageInfo { hasNextPage endCursor }"
ISSUE_FIELDS = f"""number title state createdAt body
userContentEdits(first: 100) {{ {EDITS} }}
comments(first: 100) {{ {COMMENTS} }}
timelineItems(first: 100, itemTypes: [LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT]) {{ {TIMELINE} }}"""
PR_FIELDS = f"""number state mergedAt body mergeCommit {{ oid }}
comments(first: 100) {{ {COMMENTS} }}"""


def fetch(slug):
    """Every issue and pull request of slug, as GraphQL nodes with their lists paged to the end."""
    owner, name = slug.split("/", 1)
    repo = f'repository(owner: "{owner}", name: "{name}")'

    def pages(kind, fields):
        out, cursor = [], None
        while True:
            q = (f"query($cursor: String) {{ {repo} {{ {kind}(first: 25, after: $cursor, "
                 f"orderBy: {{field: CREATED_AT, direction: ASC}}) {{ nodes {{ {fields} }} "
                 f"pageInfo {{ hasNextPage endCursor }} }} }} }}")
            d = gql(q, cursor=cursor)["repository"][kind]
            out += d["nodes"]
            if not d["pageInfo"]["hasNextPage"]:
                return out
            cursor = d["pageInfo"]["endCursor"]

    def drain(node, one, field, selection, item_types=""):
        conn = node[field]
        while conn["pageInfo"]["hasNextPage"]:
            q = (f"query($n: Int!, $c: String) {{ {repo} {{ {one}(number: $n) {{ "
                 f"{field}(first: 100, after: $c{item_types}) {{ {selection} }} }} }} }}")
            more = gql(q, n=node["number"], c=conn["pageInfo"]["endCursor"])["repository"][one][field]
            conn["nodes"] += more["nodes"]
            conn["pageInfo"] = more["pageInfo"]

    issues = pages("issues", ISSUE_FIELDS)
    for i in issues:
        drain(i, "issue", "timelineItems", TIMELINE, ", itemTypes: [LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT]")
        drain(i, "issue", "comments", COMMENTS)
        drain(i, "issue", "userContentEdits", EDITS)
    prs = pages("pullRequests", PR_FIELDS)
    for p in prs:
        drain(p, "pullRequest", "comments", COMMENTS)
    return issues, prs


def load_cache(directory):
    out = []
    for name in ("issues.json", "prs.json", "runs.json"):
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError) as e:
            raise ReadError(f"cannot read {name} in {directory}: {e}")
    return out


def git(*args):
    return run(["git", "-c", "core.quotepath=false", *args], cwd=GIT_CWD)


def first_parent(ref):
    """The first-parent line of ref, newest first: sha, parents, committer date, subject, message."""
    commits = []
    for rec in git("log", "--first-parent", "-z", "--format=%H%x1f%P%x1f%cI%x1f%s%x1f%B", ref).split("\0"):
        if rec.strip():
            sha, parents, date, subject, message = rec.lstrip("\n").split("\x1f", 4)
            commits.append({"sha": sha, "parents": parents.split(), "date": utc(date), "subject": subject,
                            "message": message})
    return commits


def utc(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def first_line(body):
    return (body or "").split("\n", 1)[0].strip()


def prep_issue(node):
    events = sorted(((utc(e["createdAt"]), e["__typename"], (e.get("label") or {}).get("name"))
                     for e in node["timelineItems"]["nodes"]), key=lambda e: e[0])
    return {
        "number": node["number"], "title": node.get("title") or "", "created": utc(node["createdAt"]), "body": node.get("body") or "",
        "edits": sorted(((utc(e["editedAt"]), e["diff"] or "") for e in node["userContentEdits"]["nodes"]),
                        key=lambda e: e[0]),
        "comments": sorted(((utc(c["createdAt"]), (c.get("author") or {}).get("login"), c["body"] or "")
                            for c in node["comments"]["nodes"]), key=lambda c: c[0]),
        "events": events,
        "labeled": [(t, n) for t, k, n in events if k == "LabeledEvent"],
        "unlabeled": [(t, n) for t, k, n in events if k == "UnlabeledEvent"],
        "closed": [t for t, k, _ in events if k == "ClosedEvent"],
    }


def labels_at(issue, t):
    held = set()
    for when, kind, name in issue["events"]:
        if when > t:
            break
        if kind == "LabeledEvent":
            held.add(name)
        elif kind == "UnlabeledEvent":
            held.discard(name)
    return held


def attempts_of(issue, now):
    out, present = [], False
    for when, kind, name in issue["events"]:
        if name != "agent-ready":
            continue
        if kind == "LabeledEvent" and not present:
            present = True
            out.append({"issue": issue["number"], "start": when, "end": None})
        elif kind == "UnlabeledEvent" and present:
            present = False
            out[-1]["end"] = when
    for i, a in enumerate(out):
        a["limit"] = out[i + 1]["start"] if i + 1 < len(out) else now
        a["next"] = out[i + 1]["start"] if i + 1 < len(out) else None
    return out


def body_at(issue, t):
    edits = issue["edits"]
    if not edits:
        return issue["body"]
    best = None
    for when, text in edits:
        if when <= t + timedelta(minutes=2):
            best = text
    return edits[0][1] if best is None else best


def size_of(body):
    m = SIZE_LINE.search(body)
    return m.group(1) if m and m.group(1) in ("S", "M") else "none"


def landed_prs(prs, commits):
    """[((time, landing sha, PR number), PR, {issues its Fixes lines name})], one per landed PR with a Fixes line."""
    by_subject = {}
    for c in commits:
        m = LANDED_SUBJECT.search(c["subject"])
        if m:
            by_subject.setdefault(int(m.group(1)), c)
    out = []
    for p in prs:
        n = p["number"]
        merge = (p.get("mergeCommit") or {}).get("oid")
        if p.get("mergedAt") and merge:
            land = (utc(p["mergedAt"]), merge, n)
        elif n in by_subject:
            land = (by_subject[n]["date"], by_subject[n]["sha"], n)
        else:
            continue
        issues = {int(x) for x in FIXES.findall(p.get("body") or "")}
        if issues:
            out.append((land, p, issues))
    return out


def landings(prs, commits):
    """issue number -> sorted [(time, landing sha, PR number)]."""
    out = collections.defaultdict(list)
    for land, _, issues in landed_prs(prs, commits):
        for issue in issues:
            out[issue].append(land)
    return {k: sorted(v) for k, v in out.items()}


def anchors(body):
    """(file, stamp or None, [fragments]) for each anchor line of a body."""
    body = re.sub(r"(?ms)^#{1,6}[ \t]*Doc impact on close\b.*?(?=^#{1,6}[ \t]|\Z)", "", body)
    body = re.sub(r"(?m)^Doc impact on close\b.*$", "", body)
    out, heading = [], None
    for line in body.splitlines():
        if re.match(r"#{1,6}\s", line):
            m = STAMP.search(line)
            heading = m.group(1) if m else None
            continue
        if '"' not in line or not re.match(r"\s*(?:[-*+]|\d+[.)])\s", line):
            continue
        span = re.search(r"`([^`\r\n]+)`", line)
        path = span and path_candidate(span.group(1))
        if not path:
            continue
        frags = [f.replace('\\"', '"') for f in
                 (m.group("frag") for m in re.finditer(r'"(?P<frag>(?:[^"\\\r\n]|\\.)*)\\?(?P<close>"|(?=[\r\n]|\Z))', line)
                  if m.group("close") == '"')]
        frags = [re.sub(r"^`+|`+$", "", f) for f in frags if 12 <= len(f) <= 120]
        m = STAMP.search(line)
        out.append((path, m.group(1) if m else heading, frags))
    return out


def path_candidate(span):
    c = span.strip()
    if re.search(r"\s", c) or not re.search(r"[/\\]", c) or not re.search(r"\.[A-Za-z0-9]{1,7}(\Z|[:#])", c):
        return None
    if re.match(r"(\$|%|<|~[\\/]|\.\.[\\/]|[\\/]|[A-Za-z][A-Za-z0-9+.-]*:)", c):
        return None
    path = re.split(r"[:#]", c)[0].strip().replace("\\", "/")
    return re.sub(r"^(\./)+", "", path) or None


class Blamer:
    """The commits an issue's anchors blame to, and how many anchors could not be read, memoized."""

    def __init__(self):
        self.memo = {}
        self.skipped = 0

    def readable(self, stamp, path):
        try:
            git("cat-file", "-e", f"{stamp}^{{commit}}")
            git("cat-file", "-e", f"{stamp}:{path}")
        except ReadError as e:
            if e.code == 1 or "Not a valid object name" in str(e) or "does not exist in" in str(e):
                return False
            raise
        return True

    def of(self, issue):
        if issue["number"] in self.memo:
            return self.memo[issue["number"]]
        shas = set()
        for path, stamp, frags in anchors(issue["body"]):
            if not stamp or not self.readable(stamp, path):
                self.skipped += 1
                continue
            for frag in frags:
                try:
                    hits = git("grep", "-n", "-F", "-e", frag, stamp, "--", f":(literal){path}")
                except ReadError as e:
                    if e.code == 1:
                        continue
                    raise
                prefix = f"{stamp}:{path}:"
                for row in hits.splitlines():
                    if row.startswith(prefix):
                        n = int(row[len(prefix):].split(":", 1)[0])
                        shas.add(git("blame", "--first-parent", "-l", "-L", f"{n},{n}", stamp, "--", path).split(" ", 1)[0].lstrip("^"))
        self.memo[issue["number"]] = shas
        return shas


def sweep_child(issue):
    """A cleanup-ledger sweep child: its title holds both `sweep` and `ledger line`, in any case."""
    title = issue["title"].lower()
    return "sweep" in title and "ledger line" in title


def reversed_by(land, commits, bugs, blamer):
    when, sha = land[0], land[1]
    subject = next((c["subject"] for c in commits if c["sha"] == sha), "")
    stripped = re.sub(r"\s*\(#[0-9]+\)\s*$", "", subject)
    revert = re.compile(r'Revert "' + re.escape(stripped) + r'(?: \(#[0-9]+\))?"') if stripped else None
    for c in commits:
        if c["sha"] != sha and when <= c["date"] <= when + W:
            if f"This reverts commit {sha}" in c["message"] or (revert and revert.match(c["subject"])):
                return True
    return any(when <= b["created"] <= when + W and sha in blamer.of(b) for b in bugs)


def assisted_by(attempt, issue, land, approvers, comments_of_pr):
    lo, hi = attempt["start"], land[0]
    ruled = [(t, body) for t, author, body in issue["comments"] + comments_of_pr.get(land[2], [])
             if author in approvers and lo <= t <= hi]
    if any(first_line(b).startswith("**Ruling**") for _, b in ruled):
        return True
    cleared = [t for t, n in issue["unlabeled"] if n == "needs-ruling" and lo <= t <= hi]
    return any(t <= e for t, _ in ruled for e in cleared)


def classify(attempt, issue, lands, now, approvers, comments_of_pr, commits, bugs, blamer):
    t, limit = attempt["start"], attempt["limit"]
    attempt["checkpoint"] = "checkpoint" in labels_at(issue, t + timedelta(minutes=10))
    attempt["week"] = "%d-W%02d" % t.isocalendar()[:2]
    attempt["size"] = size_of(body_at(issue, t))
    land = next((x for x in lands.get(issue["number"], []) if t <= x[0] < limit), None)
    if land:
        attempt["landing"] = land[1]
        attempt["reversed"] = reversed_by(land, commits, bugs, blamer)
        attempt["assisted"] = assisted_by(attempt, issue, land, approvers, comments_of_pr)
        attempt["land_time"] = land[0]
        attempt["outcome"] = "landed"
        return
    if attempt["checkpoint"] and any(first_line(b) == "**Checkpoint finding**" for c, _, b in issue["comments"] if t <= c <= limit):
        attempt["outcome"] = "finding"
        return
    end = attempt["end"]
    if end:
        near = {n for when, n in issue["labeled"] if abs(when - end) <= timedelta(minutes=10)}
        if near & {"needs-ruling", "needs-triage"}:
            attempt["outcome"] = "refused"
            attempt["reason"] = stop_reason(issue, end)
            nxt = attempt["next"]
            attempt["resolved"] = bool(nxt) or any(c >= end for c in issue["closed"])
            attempt["unnecessary"] = bool(nxt) and body_at(issue, end).strip() == body_at(issue, nxt).strip()
        else:
            attempt["outcome"] = "parked"
            attempt["parked"] = next((n for n in OTHER_STATES if n in near), "none")
        return
    if any(t <= c <= limit for c in issue["closed"]):
        attempt["outcome"] = "abandoned"
        return
    attempt["outcome"] = "open"
    attempt["age"] = (now - t).days


def stop_reason(issue, end):
    window = [(c, b) for c, _, b in issue["comments"] if end - timedelta(minutes=60) <= c <= end + timedelta(minutes=10)]
    for _, body in reversed(window):
        m = STOP_LINE.fullmatch(first_line(body))
        if m:
            return m.group(1)
    if any(first_line(b) == "**Triage**" for _, b in window):
        return "triage reversal"
    return "unclassified"


def analyze(issue_nodes, pr_nodes, commits, approvers, now, since):
    """(attempts promoted at or after since, each classified; the count of anchors skipped)."""
    issues = {n["number"]: prep_issue(n) for n in issue_nodes}
    lands = landings(pr_nodes, commits)
    comments_of_pr = {p["number"]: sorted(((utc(c["createdAt"]), (c.get("author") or {}).get("login"), c["body"] or "")
                                           for c in p["comments"]["nodes"]), key=lambda c: c[0]) for p in pr_nodes}
    bugs = [i for i in issues.values() if any(n == "bug" for _, n in i["labeled"]) and not sweep_child(i)]
    blamer = Blamer()
    out = []
    for issue in issues.values():
        attempts = attempts_of(issue, now)
        for a in attempts:
            if a["start"] >= since:
                classify(a, issue, lands, now, approvers, comments_of_pr, commits, bugs, blamer)
                out.append(a)
    return sorted(out, key=lambda a: (a["start"], a["issue"])), blamer.skipped


def pct(n, d):
    return f"{round(100 * n / d)}% ({n}/{d})" if d else "n/a"


def figures(rows, now):
    """The counts of one cohort's attempts."""
    landed = [a for a in rows if a["outcome"] == "landed"]
    refused = [a for a in rows if a["outcome"] == "refused"]
    f = {"attempts": len(rows), "landed": len(landed),
         "reversed": sum(a["reversed"] for a in landed), "assisted": sum(a["assisted"] for a in landed),
         "clean": sum(not a["reversed"] and not a["assisted"] for a in landed),
         "finding": sum(a["outcome"] == "finding" for a in rows),
         "refused": len(refused), "parked": [a["parked"] for a in rows if a["outcome"] == "parked"],
         "abandoned": sum(a["outcome"] == "abandoned" for a in rows),
         "open": [a["age"] for a in rows if a["outcome"] == "open"]}
    f["reasons"] = collections.Counter(a["reason"] for a in refused)
    decisions = [a for a in refused if a["reason"] in DECISION_STOPS]
    f["resolved"] = sum(a["resolved"] for a in decisions)
    f["unnecessary"] = sum(a["resolved"] and a["unnecessary"] for a in decisions)
    f["pending"] = sum(not a["resolved"] for a in decisions)
    by_commit = collections.defaultdict(list)
    for a in landed:
        by_commit[a["landing"]].append(a)
    f["commits"] = len(by_commit)
    f["commit_reversed"] = sum(any(a["reversed"] for a in g) for g in by_commit.values())
    f["commit_assisted"] = sum(any(a["assisted"] for a in g) for g in by_commit.values())
    f["commit_clean"] = sum(not any(a["reversed"] or a["assisted"] for a in g) for g in by_commit.values())
    f["final"] = all(a["land_time"] + W <= now for a in landed)
    return f


def counted(items):
    if not items:
        return "0"
    return f"{len(items)} (" + ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(items).items())) + ")"


def render(slug, attempts, skipped, now, since):
    date = "%Y-%m-%d"
    size_order = {"S": 0, "M": 1, "none": 2}
    lines = [f"# Loop outcomes for {slug}", "",
             f"Promotions from {since.strftime(date)} to {now.strftime(date)} UTC, W = 14 days after the landing, "
             "no horizon. Report-only until three cohorts have closed their window.", ""]

    def section(title, rows, checkpoint):
        cohorts = sorted({(a["week"], a["size"]) for a in rows} if not checkpoint else {(a["week"], "") for a in rows},
                         key=lambda k: (k[0], size_order.get(k[1], 3)))
        groups = [((f"{w} {s}".strip()), [a for a in rows if a["week"] == w and (checkpoint or a["size"] == s)])
                  for w, s in cohorts] + [("all", rows)]
        figs = [(label, figures(g, now)) for label, g in groups]
        out = [f"## {title}", ""]
        if checkpoint:
            out += ["| Cohort | Attempts | Landed | Finding delivered | Refused | Parked | Abandoned | Open | Finding rate |",
                    "|---|---|---|---|---|---|---|---|---|"]
            for label, f in figs:
                out.append(f"| {label} | {f['attempts']} | {f['landed']} | {f['finding']} | {f['refused']} | {counted(f['parked'])} "
                           f"| {f['abandoned']} | {len(f['open'])} | {pct(f['finding'], f['attempts'] - len(f['open']))} |")
            return out + [""]
        out += ["| Cohort | Attempts | Landed | Clean | Reversed | Assisted | Refused | Parked | Abandoned | Open | Delivery | Refusal | Reversal | Assist |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for label, f in figs:
            oldest = f" (oldest {max(f['open'])} d)" if f["open"] else ""
            status = "" if not f["landed"] else (" final" if f["final"] else " provisional")
            out.append(f"| {label} | {f['attempts']} | {f['landed']} | {f['clean']} | {f['reversed']} | {f['assisted']} "
                       f"| {f['refused']} | {counted(f['parked'])} | {f['abandoned']} | {len(f['open'])}{oldest} "
                       f"| {pct(f['clean'], f['attempts'] - len(f['open']))} | {pct(f['refused'], f['attempts'])} "
                       f"| {pct(f['reversed'], f['landed'])}{status} | {pct(f['assisted'], f['landed'])} |")
        out += ["", "Refusals by reason, and refusal precision over the resolved decision stops "
                "(open decision, dead anchor, gate uncovered):", "",
                "| Cohort | " + " | ".join(REASONS) + " | Resolved | Unnecessary | Pending | Precision |",
                "|---|" + "---|" * (len(REASONS) + 4)]
        for label, f in figs:
            out.append(f"| {label} | " + " | ".join(str(f["reasons"][r]) for r in REASONS)
                       + f" | {f['resolved']} | {f['unnecessary']} | {f['pending']} | {pct(f['resolved'] - f['unnecessary'], f['resolved'])} |")
        out += ["", "Per landing commit, a commit counting in each row its attempts span:", "",
                "| Cohort | Commits | Clean | Reversed | Assisted |", "|---|---|---|---|---|"]
        for label, f in figs:
            out.append(f"| {label} | {f['commits']} | {f['commit_clean']} | {f['commit_reversed']} | {f['commit_assisted']} |")
        return out + [""]

    lines += section("Build cohorts", [a for a in attempts if not a["checkpoint"]], False)
    lines += section("Checkpoint cohort", [a for a in attempts if a["checkpoint"]], True)
    lines += ["A defect whose issue cites no stamped anchor in the landed lines is not counted: "
              f"{skipped} anchor(s) had no stamp or a stamp or file git could not read.",
              "The reversal figure is an upper bound: a bug's anchors cite context lines around a defect "
              "as well as the defect, and each one that blames to a landing charges it.",
              "An attempt that leaves no tracker trace is not seen."]
    return "\n".join(lines)


def week_of(t):
    return "%d-W%02d" % t.isocalendar()[:2]


def strip_fences(body):
    """The body without its fenced blocks; a fence left open runs to the end of the body."""
    out, fence = [], None
    for line in body.split("\n"):
        m = FENCE.match(line)
        if m and fence is None and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
            fence = m.group(1)
        elif m and fence and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
            fence = None
        elif fence is None:
            out.append(line)
    return "\n".join(out)


def review_of(body):
    """None for a body with no Review line, "none", or (rounds, H, M, L, false-sentences or "unread")."""
    m = REVIEW_LINE.search(strip_fences(body or ""))
    if not m:
        return None
    if m.group(1) is None:
        return "none"
    rounds, h, mid, low = (int(m.group(i)) for i in range(1, 5))
    return (rounds, h, mid, low, m.group(5) if m.group(5) == "unread" else int(m.group(5)))


def pr_quality(prs, commits, since):
    """ISO week of the landing -> the review_of each PR landed at or after since, one per PR."""
    out = collections.defaultdict(list)
    for (when, _, _), p, _ in landed_prs(prs, commits):
        if when >= since:
            out[week_of(when)].append(review_of(p.get("body")))
    return dict(out)


def pr_figures(reviews):
    """The counts of one week's PRs, from their review_of values."""
    numeric = [r for r in reviews if isinstance(r, tuple)]
    false = [r[4] for r in numeric if r[4] != "unread"]
    rounds = sorted(r[0] for r in numeric)
    return {"landed": len(reviews), "line": sum(r is not None for r in reviews), "none": reviews.count("none"),
            "no_line": reviews.count(None), "unread": len(numeric) - len(false), "counted": len(false),
            "false": sum(false), "with_false": sum(n > 0 for n in false),
            "h": sum(r[1] for r in numeric), "m": sum(r[2] for r in numeric), "l": sum(r[3] for r in numeric),
            "median": statistics.median(rounds) if rounds else None, "max": rounds[-1] if rounds else None}


def flake_attempts(runs, since):
    """Each failed attempt that started at or after since: its run, attempt, week and whether it is flaky."""
    same = collections.defaultdict(list)
    for r in runs:
        same[(r.get("workflow_id"), r["head_sha"])].append(r)
    out = []
    for r in runs:
        chain = sorted(r.get("attempts", []) + [r], key=lambda a: a["run_attempt"])
        for i, a in enumerate(chain):
            start = utc(a.get("run_started_at") or a["created_at"])
            if a["conclusion"] != "failure" or start < since:
                continue
            flaky = (any(x["conclusion"] == "success" for x in chain[i + 1:])
                     or any(o["id"] != r["id"] and o["conclusion"] == "success" and utc(o["created_at"]) > start
                            for o in same[(r.get("workflow_id"), r["head_sha"])]))
            out.append({"run": r["id"], "attempt": a["run_attempt"], "week": week_of(start), "flaky": flaky})
    return out


def render_quality(pr_weeks, flakes, since, now):
    date = "%Y-%m-%d"
    days = [since + timedelta(days=i) for i in range((now - since).days + 1)]
    weeks = sorted(set(pr_weeks) | {f["week"] for f in flakes} | {week_of(t) for t in days})

    def row(label, reviews, failed_attempts):
        p = pr_figures(reviews)
        flaky = sum(f["flaky"] for f in failed_attempts)
        mean = f"{p['false'] / p['counted']:.2f}" if p["counted"] else "n/a"
        median = "n/a" if p["median"] is None else f"{p['median']:g}"
        top = "n/a" if p["max"] is None else str(p["max"])
        return (f"| {label} | {p['landed']} | {p['line']} | {p['none']} | {p['no_line']} | {p['unread']} | {p['false']} | {mean} "
                f"| {pct(p['with_false'], p['counted'])} | {p['h']} | {p['m']} | {p['l']} | {median} | {top} "
                f"| {len(failed_attempts)} | {flaky} | {len(failed_attempts) - flaky} | {pct(flaky, len(failed_attempts))} |")

    lines = ["## Quality", "",
             f"Pull requests by the ISO week of the landing and CI failed attempts by the week they started, from "
             f"{since.strftime(date)} to {now.strftime(date)} UTC. Report-only.", "",
             "| Week | Landed | With line | none | No line | unread | False sentences | Mean per PR | PRs with one | H | M | L "
             "| Rounds median | Rounds max | Failed attempts | Flaky | Failed | Flake rate |",
             "|---|" + "---|" * 17]
    for w in weeks:
        lines.append(row(w, pr_weeks.get(w, []), [f for f in flakes if f["week"] == w]))
    lines.append(row("all", [r for w in weeks for r in pr_weeks.get(w, [])], flakes))
    return "\n".join(lines + ["",
        "A pull request with no `Review:` line is shown under no line and never counted as clean; the series starts at the "
        "first pull request that carries one. False sentences are over the lines with a number there (`unread` is "
        "counted apart); findings and rounds are over every numeric line, `unread` included, and a pull request is a region.",
        "A failed attempt is one whose conclusion is `failure`. It is flaky when a later attempt of its run, or a later run "
        "of the same workflow on the same head SHA, concluded `success`, and failed otherwise. A cancelled or skipped attempt "
        "is neither. The flake rate is flaky over flaky plus failed."])


def json_docs(text):
    """The JSON documents in text, whatever whitespace separates them."""
    decoder, i, out = json.JSONDecoder(), 0, []
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        doc, i = decoder.raw_decode(text, i)
        out.append(doc)


def fetch_runs(slug, since, now):
    """The Actions run list from since to now, a week per query (a day per query for a week at the cap), each run above attempt 1 with its earlier attempts."""
    stamp = "%Y-%m-%dT%H:%M:%SZ"

    def query(lo, hi):
        """The runs created from lo to hi, or None when the API's result cap makes the list partial."""
        path = f"repos/{slug}/actions/runs?created={lo.strftime(stamp)}..{hi.strftime(stamp)}&per_page=100"
        pages = json_docs(run(GH + ["api", path, "--paginate", "--jq", RUNS_JQ]))
        if not pages:
            raise ReadError(f"{path} returned no page")
        total, got = pages[0]["total"], [r for page in pages for r in page["runs"]]
        if total >= RUNS_CAP:
            return None
        if len(got) != total:
            raise ReadError(f"{path} returned {len(got)} of {total} runs")
        return got

    runs, lo = [], since
    while lo <= now:
        hi = min(lo + timedelta(days=7) - timedelta(seconds=1), now)
        got = query(lo, hi)
        if got is None:
            got, day = [], lo
            while day <= hi:
                end = min(day + timedelta(days=1) - timedelta(seconds=1), hi)
                part = query(day, end)
                if part is None:
                    raise ReadError(f"runs created {day.strftime(stamp)}..{end.strftime(stamp)} hold {RUNS_CAP} or more: "
                                    f"at the API's {RUNS_CAP}-result cap, a day's run list cannot be read whole")
                got += part
                day += timedelta(days=1)
        runs += got
        lo += timedelta(days=7)
    for r in runs:
        r["attempts"] = [json.loads(run(GH + ["api", f"repos/{slug}/actions/runs/{r['id']}/attempts/{n}"]))
                         for n in range(1, r["run_attempt"])]
    return runs


def resolve_issue(title):
    """The number of the rolling issue titled `title`, or 0 when none exists, from the shared resolver."""
    script = ("$ErrorActionPreference = 'Stop'; . (Join-Path $env:LOOP_OUTCOMES_BIN 'Get-RollingIssue.ps1'); "
              "$n = Get-RollingIssueNumber -Key rolling_issues.loop_runs -ToolDir $env:LOOP_OUTCOMES_BIN -Title $env:LOOP_OUTCOMES_TITLE; "
              "Write-Output \"RESOLVED=$n\"")
    env = {**os.environ, "LOOP_OUTCOMES_BIN": HERE, "LOOP_OUTCOMES_TITLE": title}
    try:
        p = subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True, env=env, cwd=GIT_CWD)
    except OSError as e:
        raise ReadError(f"pwsh: {e}")
    out = p.stdout.decode("utf-8", "replace")
    m = re.search(r"^RESOLVED=([0-9]+)\s*$", out, re.MULTILINE)
    if p.returncode != 0 or not m:
        raise ReadError(f"the rolling-issue resolver failed (exit {p.returncode}): {out}{p.stderr.decode('utf-8', 'replace')}")
    return int(m.group(1))


def create_rolling_issue(slug, title):
    out = run(GH + ["issue", "create", "--repo", slug, "--title", title, "--label", "umbrella", "--body",
                    "Loop outcomes: one comment per run, appended and never edited. A rolling issue is machinery, not backlog."])
    m = re.search(r"/issues/([0-9]+)\s*$", out.strip())
    if not m:
        raise ReadError(f"gh issue create printed no issue URL: {out}")
    return int(m.group(1))


def post_comment(slug, number, report):
    run(GH + ["issue", "comment", str(number), "--repo", slug, "--body-file", "-"], input_text=report)


def default_since(now):
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday - timedelta(weeks=11)


def date_arg(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    ap = argparse.ArgumentParser(prog="loop-outcomes.py", description="Report what became of every agent-ready promotion, per weekly cohort.")
    ap.add_argument("--since", type=date_arg, metavar="YYYY-MM-DD", help="the first promotion included, UTC; default: twelve ISO weeks")
    ap.add_argument("--comment", action="store_true", help="append the report as one comment on the [rolling_issues].loop_runs issue")
    ap.add_argument("--from-cache", metavar="DIR", help="read DIR/issues.json, DIR/prs.json and DIR/runs.json instead of calling GitHub")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        slug = binding("repo.slug")
        default = binding("repo.default_branch")
        approvers = set(json.loads(binding("owner.ruling_approvers")))
        if not approvers:
            raise ReadError("owner.ruling_approvers is empty: no assist can be counted")
        title = binding("rolling_issues.loop_runs", optional=True)
        issues, prs, *cached = load_cache(args.from_cache) if args.from_cache else (*fetch(slug),)
        commits = first_parent(f"origin/{default}")
        since = args.since or default_since(now)
        runs = cached[0] if cached else fetch_runs(slug, since, now)
        attempts, skipped = analyze(issues, prs, commits, approvers, now, since)
        report = (render(slug, attempts, skipped, now, since) + "\n\n"
                  + render_quality(pr_quality(prs, commits, since), flake_attempts(runs, since), since, now))
        print(report)
        if args.comment:
            if not title:
                print("no [rolling_issues].loop_runs declared in .claude/ouro.toml: printed only, nothing posted.")
                return 0
            number = resolve_issue(title) or create_rolling_issue(slug, title)
            post_comment(slug, number, report)
            print(f"appended to #{number}")
    except ReadError as e:
        sys.stderr.write(f"loop-outcomes.py: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
