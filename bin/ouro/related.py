"""The related-work lookup triage's brief reads: closed-issue searches, their ranking, the fixes that closed each and where those landed."""
import json
import re

from .sh import DriverError


def graphql_issue(env, query, n):
    """Issue #n as `query` reads it, and gh's exit code; the issue is None when the read fails or answers no issue."""
    owner, repo = env.slug.split("/", 1)
    r = env.gh("api", "graphql", "-f", f"query={query}", "-f", f"o={owner}", "-f", f"r={repo}", "-F", f"n={n}", check=False)
    try:
        issue = json.loads(r.out)["data"]["repository"]["issue"] if r.code == 0 else None
    except (ValueError, KeyError, TypeError):
        issue = None
    return (issue if isinstance(issue, dict) else None), r.code


def on_default(env, sha):
    """Whether the commit is on the fetched default branch: yes, no or unknown. The node has no shell, so the driver runs it."""
    r = env.git("merge-base", "--is-ancestor", sha, f"origin/{env.default}", check=False)
    return "yes" if r.code == 0 else "no" if r.code == 1 else "unknown"


def pr_line(env, p, on=None):
    """A pull request's state and merge commit, with whether that commit is on the fetched default branch."""
    sha = (p.get("mergeCommit") or {}).get("oid") or ""
    if sha and on is None:
        on = on_default(env, sha)
    landed = f"; on origin/{env.default}: {on}" if sha else ""
    return f"#{p['number']} {p['state']}, merge commit {sha or 'none'}{landed}"


FENCE = re.compile(r"(?ms)^[ ]{0,3}(`{3,}|~{3,})[^\n]*\n(.*?)^[ ]{0,3}\1")
# a name joined from words, camelCase with an inner capital or snake_case, minus `*_t` types: type, encoding and register
# names (UINT32, utf8, sha256, x86_64) run letters into digits, are in every code base, and would crowd out the real one
IDENTIFIER = re.compile(r"\b(?!\w+_t\b)(?=\w*[a-z][A-Z]|\w*[A-Za-z]_[A-Za-z])[A-Za-z_]\w{3,}\b")
CLOSING = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)[ \t]*:?[ \t]+"
CLOSER_QUERY = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){timelineItems(last:5,itemTypes:[CLOSED_EVENT])"
                "{nodes{... on ClosedEvent{closer{__typename ... on PullRequest{number title state mergeCommit{oid}}}}}}}}}")


def lookup_queries(title, body, cap=3):
    """The related-work searches and the identifiers past the cap: one search per distinctive identifier in the body's fenced
    code blocks, then the title with a leading tag of at most three words and a colon stripped, since GitHub ANDs a query's
    terms and a tag the earlier report lacks hides it."""
    idents = list(dict.fromkeys(t for block in FENCE.findall(body) for t in IDENTIFIER.findall(block[1])))
    stripped = re.sub(r"\A(?:[^\s:]+[ \t]+){0,2}[^\s:]+:\s+(?=\S)", "", title.strip())
    return [f"{t} in:body" for t in idents[:cap]] + ([stripped] if stripped else []), idents[cap:]


def listed(r):
    """A gh call's JSON list, or None when the call failed or printed something else."""
    try:
        out = json.loads(r.out) if r.code == 0 else None
    except ValueError:
        return None
    return out if isinstance(out, list) else None


def fixing_prs(env, m):
    """The merged pull requests whose title or body closes #m, by a closing keyword before `#m`, `<slug>#m` or the issue's
    URL; None when the search fails."""
    prs = listed(env.ghr("pr", "list", "--state", "merged", "--search", f"{m} in:title,body", "--limit", "20",
                         "--json", "number,title,body,state,mergeCommit", check=False))
    slug = re.escape(env.slug)
    ref = re.compile(rf"(?i)\b{CLOSING}(?:(?:{slug})?#|https?://[^/\s]+/{slug}/issues/){m}\b")
    return None if prs is None else [p for p in prs if isinstance(p, dict) and p.get("state") == "MERGED"
                                     and ref.search(f"{p.get('title') or ''}\n{p.get('body') or ''}")]


def closing_prs(env, m):
    """The merged pull requests #m's closing events name, which include a Development-sidebar link no text search sees;
    None when the read fails."""
    nodes = ((graphql_issue(env, CLOSER_QUERY, m)[0] or {}).get("timelineItems") or {}).get("nodes")
    if not isinstance(nodes, list):
        return None
    closers = [(x.get("closer") or {}) if isinstance(x, dict) else {} for x in nodes]
    return [c for c in closers if c.get("__typename") == "PullRequest" and c.get("state") == "MERGED"]


def containing_tags(env, sha, cap=3):
    """The earliest release tags that contain the commit, each with its date, oldest first."""
    r = env.git("tag", "--contains", sha, "--sort=creatordate", "--format=%(refname:short) (%(creatordate:short))", check=False)
    if r.code != 0:
        return "unknown: `git tag --contains` failed"
    tags = [t for t in r.out.splitlines() if t.strip()]
    return (", ".join(tags[:cap]) + (f", and {len(tags) - cap} later" if len(tags) > cap else "")) if tags else "none"


def closed_hits(env, n, queries):
    """Each closed issue but #n the searches return, with the searches that found it and its best rank in one, in first-found
    order; and an unknown line per failed search."""
    found, failed = {}, []
    for q in queries:
        hits = listed(env.ghr("issue", "list", "--state", "closed", "--search", q, "--limit", "10",
                              "--json", "number,title,state,closedAt", check=False))
        if hits is None:
            failed.append(f"- The search `{q}` failed: whether a closed issue matches it is unknown.")
        hits = [h for h in hits or [] if isinstance(h, dict) and h.get("state") == "CLOSED" and h.get("number") != n]
        for rank, h in enumerate(hits):
            e = found.setdefault(h["number"], {"h": h, "qs": [], "rank": rank})
            e["qs"].append(q)
            e["rank"] = min(e["rank"], rank)
    return found, failed


def related_work(env, n, title, body, created, listed_out=None, cap=8):
    """The brief's lines for the closed issues that may be the same work: each with its closed date and the merged pull
    request that fixed it, that PR's merge commit, whether it is on the default branch, and the tags that contain it.
    A failed search is an unknown line: the brief never reads a search that did not run as one that found nothing.
    `listed_out` receives each listed candidate as {number: {"lines": its brief lines, "landed": a fix on the default branch}}."""
    lines = [f"This issue was created at {created or 'an unknown time'}."]
    queries, unsearched = lookup_queries(title, body)
    found, failed = closed_hits(env, n, queries)
    # more searches first, then each search's own ranking, so one broad search cannot fill the list
    order = sorted(found, key=lambda m: (-len(found[m]["qs"]), found[m]["rank"]))
    for m in order[:cap]:
        h, qs = found[m]["h"], ", ".join(f"`{q}`" for q in found[m]["qs"])
        fixes, landed = fix_lines(env, m)
        own = [f"- #{m}, closed at {h.get('closedAt') or 'an unknown time'}, found by {qs}: {h.get('title')}", *fixes]
        lines += own
        if listed_out is not None:
            listed_out[m] = {"lines": own, "landed": landed}
    if len(order) > cap:
        lines.append(f"- {len(order) - cap} more closed issues the searches found are not listed: {', '.join(f'#{m}' for m in order[cap:])}.")
    if unsearched:
        lines.append(f"- The code blocks hold {len(unsearched)} more identifiers this lookup did not search: {', '.join(f'`{t}`' for t in unsearched)}.")
    if not found and not failed:
        lines.append("- No search found a closed issue other than this one.")
    return "\n".join(lines + failed)


def fix_lines(env, m):
    """The brief's lines under candidate #m: each merged pull request that closes it, its landing and the tags that contain it;
    and whether one of those merge commits is on the default branch."""
    named, closers = fixing_prs(env, m), closing_prs(env, m)
    lines, landed = [], False
    for p in {p["number"]: p for p in (closers or []) + (named or [])}.values():
        sha = (p.get("mergeCommit") or {}).get("oid") or ""
        on = on_default(env, sha) if sha else None
        landed = landed or on == "yes"
        lines.append(f"  - fixed by {pr_line(env, p, on)}; in tags: {containing_tags(env, sha) if sha else 'none'}: {p.get('title')}")
    if named is None:
        lines.append(f"  - the search for merged pull requests that name #{m} failed: a fix it would find is unknown")
    if closers is None:
        lines.append(f"  - #{m}'s closing events are unreadable: a pull request linked only in its sidebar is unknown")
    return lines or [f"  - no merged pull request closes #{m}"], landed


def duplicate_question(env, n, value, listed_):
    """The owner's question for a DUPLICATE of the issue `value` names, with the lookup's evidence for it; a driver error naming
    the failed condition unless that issue is in the lookup's list, is closed now, and has a fix on the default branch."""
    m = re.fullmatch(r"#?(\d+)", str(value if value is not None else "").strip())
    if not m:
        raise DriverError(f"node TRIAGE answered DUPLICATE with duplicate_of {value!r}, which names no issue number")
    dup = int(m.group(1))
    if dup not in listed_:
        raise DriverError(f"node TRIAGE answered DUPLICATE of #{dup}, which is not in the related-work lookup's list for #{n}")
    state = str(env.gh_json("issue", "view", str(dup), "--json", "state").get("state") or "")
    if state != "CLOSED":  # the search index can lag a reopen
        raise DriverError(f"node TRIAGE answered DUPLICATE of #{dup}, which is {state.lower() or 'of unknown state'}, not closed")
    if not listed_[dup]["landed"]:
        raise DriverError(f"node TRIAGE answered DUPLICATE of #{dup}, which has no merged pull request closing it whose merge commit "
                          f"is on origin/{env.default}")
    return f"Close as a duplicate of #{dup}?\n\nThe related-work lookup's evidence:\n\n" + "\n".join(listed_[dup]["lines"])
