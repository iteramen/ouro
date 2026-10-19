"""A gh stand-in for the driver suite: answers from a per-case JSON table, keeps a little state, logs every call.

Env: STUB_GH_TABLE (the table), STUB_GH_LOG (one line per call), STUB_GH_STATE (a directory),
STUB_GH_ORIGIN (the bare repository standing for the remote).

The table maps a call's signature (its arguments, `-R <slug>` removed, joined by spaces) to
{"Exit": n, "Out": text-or-json} or {"Seq": [rows]} (the Nth call the key names takes the Nth row, the
last repeats); a key holding `*` is a pattern, and one counter serves every call it matches. A call the table does not name and the stand-in does not
model exits 1 and names itself. The stand-in models what the driver and the land scripts read and
write: issues, one pull request (create, view, list, ready, edit, comment, merge), the viewer.
Table keys beginning `_` seed that state: `_viewer`, `_issue_<N>`, `_pr`, `_comments_<N>`, and for the
GraphQL model `_edits` (the issue body's edit history, pages keyed by cursor, "" for the first),
`_prs` (what a pull request search answers), `_threads` (the pull request's review threads), `_copilot_bot` (the bot id Copilot answers to),
`_copilot_polls` (reads before its review shows) and `_copilot_never`. Copilot's review is made on
the head the pull request has when it first shows, and keeps that commit.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
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


def git(*a):
    return subprocess.run(["git", "--git-dir", origin, *a], capture_output=True, text=True, encoding="utf-8").stdout.strip()


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
    live.setdefault("mergeable", "MERGEABLE")
    live.setdefault("mergeStateStatus", "CLEAN")
    live.setdefault("reviewDecision", "")
    if tip and origin:
        git("update-ref", f"refs/pull/{pr['number']}/head", tip)
    return {f: live.get(f) for f in (fields or "").split(",") if f}


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
        out(pr_view(pr, flag("--json")))
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
        out([{"name": "ci", "bucket": "pass"}] if "--json" in args else "All checks were successful")
if verb == "run list":
    out([{"databaseId": 11, "workflowDatabaseId": 1, "workflowName": "ci", "event": "pull_request",
          "createdAt": "2026-01-01T00:00:00Z", "status": "completed", "conclusion": "success"}])
if verb == "run watch":
    out("")
if verb == "run view" and flag("--json") == "url":
    out({"url": f"https://example.test/o/n/actions/runs/{n}"})
if verb == "run view" and "--log-failed" in args:
    out("the failed log line")
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
