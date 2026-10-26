"""A gh stand-in for the driver suite: answers from a per-case JSON table, keeps a little state, logs every call.

Env: STUB_GH_TABLE (the table), STUB_GH_LOG (one line per call), STUB_GH_STATE (a directory),
STUB_GH_ORIGIN (the bare repository standing for the remote).

The table maps a call's signature (its arguments, `-R <slug>` removed, joined by spaces) to
{"Exit": n, "Out": text-or-json} or {"Seq": [rows]} (the Nth call the key names takes the Nth row, the
last repeats; a row {"Model": true} lets the stand-in model that call); a key holding `*` is a pattern, and one counter serves every call it matches. A call the table does not name and the stand-in does not
model exits 1 and names itself; an `issue create` with a `--label` outside the origin's [labels] sets,
`needs-triage` and `umbrella` exits 1 as gh refuses an unknown label. The stand-in models what the driver and the land scripts read and
write: issues, one pull request (create, view, list, ready, edit, comment, merge), the viewer, and the runs on each commit it is asked
about: `_runs` seeds them in first-seen order, one list of per-attempt conclusions per commit (the last entry repeats; "" or
"in_progress" is a run still going), `run rerun` moves a run to its next attempt, and `run view`, `pr checks` and `run list` answer
from that one state however often they are read. `_mergeable` is a list of `mergeable mergeStateStatus` pairs the pull request
steps through once per head change (an UNKNOWN that settles at one head is not expressible); a CONFLICTING entry moves the base
once (a commit on master), as a conflicting head means it did. `_head_lag` is a list of booleans, one per `pr view` that
asks for `headRefOid` (the last repeats): true serves `STALE_HEAD` there in place of the branch tip, as GitHub does for a while after a push.
Table keys beginning `_` seed that state: `_viewer`, `_issue_<N>`, `_pr`, `_comments_<N>`, `_mergeable`, `_head_lag`, and for the
GraphQL model `_edits` (the issue body's edit history, pages keyed by cursor, "" for the first),
`_prs` (what a pull request search answers), `_threads` (the pull request's review threads), `_copilot_bot` (the bot id Copilot answers to),
`_copilot_polls` (reads before its review shows) and `_copilot_never`; `_stale_list` makes the run list read a re-run attempt
as success whatever it concluded, `_checks_extra` adds check rows to `pr checks --json`, and a `budget` plan entry is an attempt
that fails with a job that never started, as an Actions budget leaves it, a `matrix` one a real failure beside a leg its
fail-fast cancelled while it queued (`api .../runs/<id>/attempts/<n>[/jobs]` answer them). Copilot's review is made on
the head the pull request has when it first shows, and keeps that commit.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

args = sys.argv[1:]
clean, i = [], 0
while i < len(args):
    if args[i] in ("-R", "--repo") and i + 1 < len(args):
        i += 2
        continue
    clean.append(args[i])
    i += 1
args = clean
table = json.loads(Path(os.environ["STUB_GH_TABLE"]).read_text(encoding="utf-8"))
state = Path(os.environ["STUB_GH_STATE"])
origin = os.environ.get("STUB_GH_ORIGIN", "")
sig = " ".join(args)
STALE_HEAD = "d" * 40


def flag(name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def body_text():
    f = flag("--body-file")
    if not f:
        f = next((a[len("body=@"):] for a in args if a.startswith("body=@")), None)
    return Path(f).read_text(encoding="utf-8") if f else (flag("--body") or "")


def load(name, default):
    p = state / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


def save(name, obj):
    (state / name).write_text(json.dumps(obj), encoding="utf-8")


with open(os.environ["STUB_GH_LOG"], "a", encoding="utf-8", newline="\n") as log:
    log.write(sig.replace("\n", " ") + "\n")
with open(state / "calls.jsonl", "a", encoding="utf-8", newline="\n") as log:
    log.write(json.dumps({"sig": sig, "body": body_text() if "--body-file" in args or any(a.startswith("body=@") for a in args) else None}) + "\n")


def out(obj, code=0):
    print(obj if isinstance(obj, str) else json.dumps(obj))
    sys.exit(code)


def git(*a, env=None, check=False):
    return subprocess.run(["git", "--git-dir", origin, *a], capture_output=True, text=True, encoding="utf-8", env=env, check=check).stdout.strip()


def move_base(name):
    """A commit on master adding `name`: what a CONFLICTING answer models, so the driver's sync has something to merge."""
    (state / name).write_text("moved\n", encoding="utf-8")
    env = {**os.environ, "GIT_INDEX_FILE": str(state / "index"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    git("read-tree", "refs/heads/master", env=env, check=True)
    git("update-index", "--add", "--cacheinfo", f"100644,{git('hash-object', '-w', str(state / name), check=True)},{name}", env=env, check=True)
    git("update-ref", "refs/heads/master", git("commit-tree", git("write-tree", env=env, check=True), "-p", git("rev-parse", "refs/heads/master"), "-m", "base moved", env=env, check=True), check=True)


def pr_state():
    pr = load("pr.json", None)
    if pr is None and "_pr" in table:
        pr = dict(table["_pr"])
    return pr


def pr_view(pr, fields):
    live = dict(pr)
    tip = git("rev-parse", f"refs/heads/{pr['head']}") if origin else ""
    live["headRefOid"] = tip or pr.get("headRefOid", "")
    live["headRefName"] = pr["head"]
    live.setdefault("baseRefName", "master")
    if "mergeable" in (fields or "").split(",") and table.get("_mergeable"):
        k, seen = load("mergeable.n", 0), load("mergeable.tip", "")
        if seen and tip != seen:
            k += 1
        save("mergeable.n", k)
        save("mergeable.tip", tip)
        i = min(k, len(table["_mergeable"]) - 1)
        live["mergeable"], live["mergeStateStatus"] = table["_mergeable"][i].split()
        if live["mergeable"] == "CONFLICTING" and origin and not load(f"base-moved.{i}", False):
            move_base(f"base-{i}.txt")
            save(f"base-moved.{i}", True)
    live.setdefault("mergeable", "MERGEABLE")
    live.setdefault("mergeStateStatus", "CLEAN")
    live.setdefault("reviewDecision", "")
    if tip and origin:
        git("update-ref", f"refs/pull/{pr['number']}/head", tip)
    return {f: live.get(f) for f in (fields or "").split(",") if f}


def run_on(sha):
    """The run on a commit, seeded by `_runs` in first-seen order: its id, attempt and the conclusion of each attempt."""
    runs = load("runs.json", {})
    if sha not in runs:
        plan = table.get("_runs") or [["success"]]
        runs[sha] = {"id": 11 + len(runs), "attempt": 1, "plan": plan[min(len(runs), len(plan) - 1)]}
        save("runs.json", runs)
    return runs, runs[sha]


def attempt_conclusion(r, k):
    """Attempt k's conclusion as the API records it: `budget` is a failure whose job never started."""
    c = r["plan"][min(k, len(r["plan"])) - 1]
    return "failure" if c in ("budget", "matrix") else c


def run_row(r):
    c = attempt_conclusion(r, r["attempt"])
    if table.get("_stale_list") and r["attempt"] > 1:
        c = "success"  # the list record lags the attempt it names
    done = c not in ("", "in_progress", "queued")
    return {"databaseId": r["id"], "workflowDatabaseId": 1, "workflowName": "ci", "event": "pull_request" if flag("--event") == "pull_request" else "push",
            "createdAt": "2026-01-01T00:00:00Z", "status": "completed" if done else c or "in_progress", "conclusion": c if done else ""}


def comments(kind, n):
    seed = table.get(f"_comments_{n}", []) if kind == "pr" else []
    return seed + load(f"{kind}-{n}-comments.json", [])


def issue(n):
    iss = load(f"issue-{n}.json", None)
    if iss is None:
        iss = dict(table[f"_issue_{n}"])
        iss.setdefault("comments", table.get(f"_comments_issue_{n}", []))
    return iss


def table_answer():
    """A table row, when one names this call, wins over everything the stand-in models."""
    row, named = table.get(sig), sig
    if row is None:
        for key in table:
            if "*" in key and not key.startswith("_") and fnmatch.fnmatchcase(sig, key):
                row, named = table[key], key
                break
    if row is None:
        return
    if "Seq" in row:
        counter = state / ("seq-" + "".join(c if c.isalnum() else "_" for c in named) + ".n")
        k = int(counter.read_text()) if counter.is_file() else 0
        counter.write_text(str(k + 1))
        row = row["Seq"][min(k, len(row["Seq"]) - 1)]
    if row.get("Model"):
        return
    o = row.get("Out", "")
    print(o if isinstance(o, str) else json.dumps(o))
    sys.exit(row.get("Exit", 0))


table_answer()

viewer = table.get("_viewer", "o")
verb = " ".join(args[:2])
n = args[2] if len(args) > 2 else ""

if sig == "api user --jq .login":
    out(viewer)
if verb == "issue view" and f"_issue_{n}" in table:
    out(issue(n))
if verb == "issue list" and "--label" in args:
    out([v for k, v in ((k, issue(k[len("_issue_"):])) for k in table if k.startswith("_issue_"))
         if flag("--label") in [l["name"] for l in v["labels"]] and v.get("state", "OPEN") == "OPEN"])
if verb == "issue list" and "--search" in args:
    out([{k: v[k] for k in ("number", "title", "state")} for k, v in ((k, issue(k[len("_issue_"):])) for k in table if k.startswith("_issue_"))])
if verb == "issue comment":
    iss = issue(n)
    iss["comments"].append({"author": {"login": viewer}, "body": body_text(), "createdAt": "2026-06-01T00:00:00Z"})
    save(f"issue-{n}.json", iss)
    out("")
if verb == "issue edit":
    iss = issue(n)
    names = [l["name"] for l in iss["labels"]]
    for k, a in enumerate(args):
        if a == "--remove-label":
            names = [x for x in names if x != args[k + 1]]
        if a == "--add-label" and args[k + 1] not in names:
            names.append(args[k + 1])
    iss["labels"] = [{"name": x} for x in names]
    if "--body-file" in args:
        iss["body"] = body_text()
    save(f"issue-{n}.json", iss)
    out("")
if verb == "issue reopen":
    iss = issue(n)
    iss["state"] = "OPEN"
    save(f"issue-{n}.json", iss)
    out("")
if verb == "issue create":
    sets = tomllib.loads(git("show", "master:.claude/ouro.toml") if origin else "").get("labels", {})
    known = {"needs-triage", "umbrella", *sets.get("area", []), *sets.get("type", [])}
    bad = [args[k + 1] for k, a in enumerate(args[:-1]) if a == "--label" and args[k + 1] not in known]
    if bad:
        out(f"could not add label: '{bad[0]}' not found", 1)
    out("https://example.test/o/n/issues/99")
if verb == "pr create":
    pr = {"number": int(table.get("_pr_number", 7)), "head": flag("--head"), "state": "OPEN", "isDraft": "--draft" in args,
          "title": flag("--title"), "body": body_text(), "url": "https://example.test/o/n/pull/7", "mergeCommit": None}
    save("pr.json", pr)
    pr_view(pr, "")
    out(pr["url"])
pr = pr_state() if verb.startswith("pr ") else None
if verb == "pr list" and "--head" in args:
    out([pr_view(pr, "number,state,isDraft,headRefOid,mergeCommit,title,body,url,baseRefName")] if pr and pr["head"] == flag("--head") else [])
if verb == "pr list" and "--search" in args:
    out(table.get("_prs", []))
if verb == "pr list" and "--base" in args:
    out([])
if pr and n == str(pr["number"]):
    if verb == "pr view" and flag("--json") == "comments":
        out({"comments": comments("pr", n)})
    if verb == "pr view":
        view = pr_view(pr, flag("--json"))
        if "headRefOid" in view and table.get("_head_lag"):
            k = load("head-lag.n", 0)
            save("head-lag.n", k + 1)
            if table["_head_lag"][min(k, len(table["_head_lag"]) - 1)]:
                view["headRefOid"] = STALE_HEAD
        out(view)
    if verb == "pr comment":
        save(f"pr-{n}-comments.json", load(f"pr-{n}-comments.json", []) + [{"author": {"login": viewer}, "body": body_text()}])
        out("")
    if verb == "pr ready":
        pr["isDraft"] = False
        save("pr.json", pr)
        out("")
    if verb == "pr edit":
        if "--body-file" in args:
            pr["body"] = body_text()
        save("pr.json", pr)
        out("")
    if verb == "pr merge":
        tree = git("rev-parse", f"refs/heads/{pr['head']}^{{tree}}")
        parent = git("rev-parse", "refs/heads/master")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sha = subprocess.run(["git", "--git-dir", origin, "commit-tree", tree, "-p", parent, "-m", flag("--subject") or "merge"],
                             capture_output=True, text=True, env=env).stdout.strip()
        git("update-ref", "refs/heads/master", sha)
        if "--delete-branch" in args:
            git("update-ref", "-d", f"refs/heads/{pr['head']}")
        pr.update(state="MERGED", mergeCommit={"oid": sha}, headRefOid=git("rev-parse", f"refs/pull/{n}/head"))
        save("pr.json", pr)
        out("")
    if verb == "pr checks":
        bucket = {"success": "pass", "": "pending"}.get(run_row(run_on(pr_view(pr, "headRefOid")["headRefOid"] or "none")[1])["conclusion"], "fail")
        if "--json" in args:
            out([{"name": "ci", "bucket": bucket}] + table.get("_checks_extra", []))
        out("All checks were successful" if bucket == "pass" else "checks " + bucket, {"pass": 0, "pending": 8}.get(bucket, 1))  # gh's exit codes
if verb == "run list":
    out([run_row(run_on(flag("--commit") or "none")[1])])
if verb in ("run view", "run rerun"):
    runs = load("runs.json", {})
    hit = next((r for r in runs.values() if str(r["id"]) == n), None)
    if verb == "run rerun" and hit:
        hit["attempt"] += 1
        save("runs.json", runs)
        out("")
    if verb == "run view" and flag("--json") == "attempt" and hit:
        out({"attempt": hit["attempt"]})
if verb == "run watch":
    out("")
if verb == "run view" and flag("--json") == "url":
    out({"url": f"https://example.test/o/n/actions/runs/{n}"})
if verb == "run view" and "--log-failed" in args:
    out("the failed log line")
if sig.startswith("api repos/") and "/actions/runs/" in args[1] and "/attempts/" in args[1]:
    m = re.search(r"/actions/runs/(\d+)/attempts/(\d+)(/jobs)?(?:\?per_page=\d+)?$", args[1])
    hit = next((r for r in load("runs.json", {}).values() if str(r["id"]) == m.group(1)), None)
    if hit:
        k = int(m.group(2))
        c, raw = attempt_conclusion(hit, k), hit["plan"][min(k, len(hit["plan"])) - 1]
        done = c not in ("", "in_progress", "queued")
        if m.group(3):
            started = raw not in ("budget", "startup_failure")
            jobs = [{"name": "build", "conclusion": c if done else None, "steps": [{"name": "run"}] if started else [],
                     "runner_name": "runner-1" if started else None}]
            if raw == "matrix":  # the leg fail-fast cancelled while it queued: no steps, no runner
                jobs.append({"name": "build (2)", "conclusion": "cancelled", "steps": [], "runner_name": None})
            out({"jobs": jobs})
        out({"run_attempt": k, "status": "completed" if done else c or "in_progress", "conclusion": c if done else None})
if sig.startswith("api repos/") and "/replies" in args[1]:
    save("replies.json", load("replies.json", []) + [{"id": int(args[1].split("/comments/")[1].split("/")[0]), "body": body_text()}])
    out({})
if sig.startswith("api graphql"):
    q = next((a[len("query="):] for a in args if a.startswith("query=")), "")
    var = {a.split("=", 1)[0]: a.split("=", 1)[1] for k, a in enumerate(args) if k and args[k - 1] in ("-f", "-F") and not a.startswith("query=")}
    live = pr_state() or {"number": 7, "head": "none"}
    node_id = f"PR_{live['number']}"
    bot = {"requestedReviewer": {"__typename": "Bot", "login": "copilot-pull-request-reviewer"}}

    def data(obj):
        out({"data": obj})

    if "userContentEdits" in q:
        page = table.get("_edits", {"": {"nodes": [], "hasNextPage": False, "endCursor": None}})[var.get("c", "")]
        data({"repository": {"issue": {"userContentEdits": {"nodes": page["nodes"], "pageInfo": {
            "hasNextPage": page["hasNextPage"], "endCursor": page["endCursor"]}}}}})
    if "requestReviews" in q:
        want = re.search(r'pullRequestId: "([^"]*)", botIds: \["([^"]*)"\]', q)
        ok = bool(want) and want.group(1) == node_id and want.group(2) == table.get("_copilot_bot", "BOT_ok")
        if ok:
            save("copilot.json", {"requested": True, "reads": 0})
        data({"requestReviews": {"pullRequest": {"reviewRequests": {"nodes": [bot] if ok else []}}}})
    cop = load("copilot.json", {"requested": False, "reads": 0})
    if "reviews(first:100)" in q:
        reviewed = cop["requested"] and not table.get("_copilot_never") and cop["reads"] >= table.get("_copilot_polls", 0)
        if cop["requested"] and not reviewed:
            save("copilot.json", {**cop, "reads": cop["reads"] + 1})
        if reviewed and "oid" not in cop:
            cop = {**cop, "oid": pr_view(live, "headRefOid")["headRefOid"]}
            save("copilot.json", cop)
        review = {"author": {"login": "copilot-pull-request-reviewer"}, "commit": {"oid": cop.get("oid")}}
        data({"repository": {"pullRequest": {"id": node_id, "reviewRequests": {"nodes": [bot] if cop["requested"] and not reviewed else []},
                                              "reviews": {"nodes": [review] if reviewed else []}}}})
    if "reviewThreads" in q:
        threads = json.loads(json.dumps(table.get("_threads", []))) if cop["requested"] else []
        for t in threads:
            for rep_ in load("replies.json", []):
                if rep_["id"] == t["comments"]["nodes"][0]["databaseId"]:
                    t["comments"]["nodes"].append({"databaseId": 9000, "author": {"login": viewer}, "body": rep_["body"]})
            t["comments"]["totalCount"] = len(t["comments"]["nodes"])
            t["comments"]["nodes"][0].setdefault("url", f"https://example.test/thread/{t['comments']['nodes'][0]['databaseId']}")
        page = {"data": {"repository": {"pullRequest": {"author": {"login": "me"}, "reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": threads}}}}}
        out([page] if "--slurp" in args else page)
    out([{"data": {"repository": {"pullRequest": {"author": {"login": "me"},
                                                  "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}])

print(f"stub gh: no answer for: {sig}")
sys.exit(1)
