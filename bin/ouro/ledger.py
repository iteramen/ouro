"""A cleanup ledger's split: its unswept lines, the child bodies, filing the children and the sweep mark that names them."""
import json
import re

from .sh import DriverError, say

SWEEP_MARK = re.compile(r"\*\*Sweep\*\* at [0-9a-f]{7,40}")


def is_ledger(issue):
    """A cleanup ledger (contract section 3): an open `umbrella` issue titled `Cleanup: <area>`, or `Cleanup` where no area is declared."""
    title = str(issue.get("title") or "")
    return (issue.get("state") or "OPEN") == "OPEN" and "umbrella" in [tag["name"] for tag in issue.get("labels", [])] \
        and (title == "Cleanup" or title.startswith("Cleanup: "))


def swept_at(comments, trusted):
    """When the ledger's newest sweep mark was posted, counting a mark only from a trusted author; "" when it has none."""
    return max((str(c.get("createdAt") or "") for c in comments
                if SWEEP_MARK.fullmatch(str(c.get("body") or "").split("\n", 1)[0].strip())
                and str((c.get("author") or {}).get("login") or "").lower() in trusted), default="")


def child_title(title):
    """The title holds `sweep` and `ledger line`, as loop-outcomes' sweep_child reads it, whatever the node named."""
    low = title.lower()
    return title if "sweep" in low and "ledger line" in low else f"Sweep of ledger lines: {title}"


def fenced(text):
    """Text inside a fence longer than any backtick run it holds, which no marker inside it closes: it stays byte for byte, and the
    anchor gate, which opens an Anchors section only on a heading outside a fence, greps the node's quoted fragments and not a copied
    line's own; it still resolves every backticked path."""
    fence = "`" * max(4, 1 + max(map(len, re.findall(r"`+", text)), default=0))
    end = "" if text.endswith("\n") else "\n"
    return f"{fence}\n{text}{end}{fence}"


def child_body(n, child, ids, by_id, sha):
    """A ledger child's body: a `Part of` line, the node's deliverable shape, Exit, anchors, Size and Flow, then each covered
    comment copied from the ledger with the node's note where the tree has moved."""
    parts = [p for p in child.get("parts") or [] if isinstance(p, dict)]
    deliverable = "\n".join(f"- [ ] {k}. **{str(p.get('text') or '').strip()} (Flow: {str(p.get('flow') or '').strip()}).**"
                            for k, p in enumerate(parts, 1)) or "Fix each ledger line below."
    notes = {str(x.get("comment")): str(x.get("note") or "").strip() for x in child.get("notes") or [] if isinstance(x, dict)}
    lines = []
    for cid in ids:
        c = by_id[cid]
        lines.append(f"### Ledger comment {cid}, by {(c.get('author') or {}).get('login')} at {c.get('createdAt')}\n\n{fenced(str(c.get('body') or ''))}"
                     + (f"\n\nWhere the tree has moved: {notes[cid]}" if notes.get(cid) else ""))
    anchors = "\n".join(str(a).strip() for a in child.get("anchors") or [])
    return (f"Part of #{n}\n\n## What\n\nThe ledger lines below, copied from #{n}, fixed.\n\n## Deliverable\n\n{deliverable}\n\n"
            f"## Exit\n\n{str(child.get('exit') or '').strip()}\n\n## Anchors @ {sha[:7]}\n\n{anchors}\n\n"
            f"Size: {str(child.get('size') or '').strip()}\nFlow: {str(child.get('flow') or '').strip()}\n\n## Ledger lines\n\n"
            + "\n\n".join(lines) + "\n")


LEDGER_LINE = re.compile(r"^### Ledger comment (\S+), by [^\n]*\n\n(`{4,})\n.*?^\2$", re.M | re.S)  # a match spans the copy's fence


def filed_children(env, n, by_id, swept):
    """The open children of ledger #n a trusted account filed for its comments newer than the newest sweep mark, as
    {number, title, comments, question}: a split that stopped partway left them, and a re-run files none of them again. The open
    issues are read through the REST list, every page, which holds a child filed moments before; a search would miss it while
    GitHub's index lags. With a sweep mark, only the issues updated since it are read: a child is created after the mark, and
    GitHub's updated_at, which `since` filters on, is never earlier than created_at."""
    # open is the endpoint's default state; the path holds no `&`, at which cmd.exe would split a call through a gh.cmd shim,
    # so `since` goes as a -f field, which -X GET puts in the query
    since = ["-X", "GET", "-f", f"since={swept}"] if swept else []
    pages = json.loads(env.gh("api", f"repos/{env.slug}/issues?per_page=100", "--paginate", "--slurp", *since).out)
    out = []
    for h in (h for page in pages for h in page if "pull_request" not in h):  # the REST list holds pull requests too
        body = str(h.get("body") or "").replace("\r\n", "\n")  # a body edited on the web comes back with CRLF
        if not body.startswith(f"Part of #{n}\n") or str((h.get("user") or {}).get("login") or "").lower() not in env.trusted():
            continue
        ids = [cid for cid in dict.fromkeys(m.group(1) for m in LEDGER_LINE.finditer(body))
               if cid in by_id and not (swept and str(by_id[cid].get("createdAt") or "") <= swept)]
        if ids:
            out.append({"number": int(h["number"]), "title": str(h.get("title") or ""), "comments": ids, "question": ""})
    return out


def ledger_children(env, n, issue, children, declared):
    """Each child of a ledger SPLIT as {title, labels, comments, body, question}, and the sweep mark's sha; a child an earlier,
    stopped split filed comes first, as {number, title, comments, question}, and no new child covers its comments. A driver
    error, before any write, on a comment id the ledger does not hold, one no newer than its newest trusted sweep mark, one two
    children claim, or a child body the anchor gate finds a dead anchor in."""
    if not children or not all(isinstance(c, dict) for c in children):
        raise DriverError(f"node TRIAGE answered SPLIT on ledger #{n} with no children")
    comments = issue.get("comments") or []
    by_id = {str(c["id"]): c for c in comments if c.get("id")}
    swept = swept_at(comments, env.trusted())
    sha = env.git("rev-parse", f"origin/{env.default}").out.strip()
    filed = filed_children(env, n, by_id, swept)
    covered = {cid: f for f in filed for cid in f["comments"]}
    claimed, out = {}, []
    for k, child in enumerate(children, 1):
        ids = list(dict.fromkeys(str(x) for x in child.get("comments") or []))
        if not ids:
            raise DriverError(f"node TRIAGE child {k} of ledger #{n} covers no ledger comment")
        for cid in ids:
            if cid not in by_id:
                raise DriverError(f"node TRIAGE child {k} names comment {cid!r}, which is not on ledger #{n}")
            if swept and str(by_id[cid].get("createdAt") or "") <= swept:
                raise DriverError(f"node TRIAGE child {k} names comment {cid!r}, which is no newer than ledger #{n}'s newest sweep mark ({swept})")
            if cid in claimed:
                raise DriverError(f"node TRIAGE children {claimed[cid]} and {k} both name comment {cid!r} of ledger #{n}")
            claimed[cid] = k
        question = str(child.get("question") or "").strip()
        for cid in ids:
            if cid in covered:  # the filed child stands for this one: the ledger triage's question rides on it
                covered[cid]["question"] = covered[cid]["question"] or question
        ids = [cid for cid in ids if cid not in covered]
        if not ids:
            continue
        body = child_body(n, child, ids, by_id, sha)
        if dead := env.dead_anchors(body, f"origin/{env.default}"):
            raise DriverError(f"node TRIAGE child {k} of ledger #{n} has anchors the anchor gate finds dead: {'; '.join(dead)}")
        labels = ["needs-triage"] + [declared[t] for t in dict.fromkeys(str(x).lower() for x in child.get("labels") or []) if t in declared]
        out.append({"title": child_title(str(child.get("title") or "").strip()), "labels": labels, "comments": ids, "body": body,
                    "question": question})
    return filed + out, sha


def ledger_note(env, issue):
    """The brief's line saying whether the issue is a ledger, and when its newest trusted sweep mark was posted."""
    if not is_ledger(issue):
        return "This issue is not a cleanup ledger: `children` stays empty."
    swept = swept_at(issue.get("comments") or [], env.trusted())
    return "This issue is a cleanup ledger: a SPLIT answers `children`. " + (
        f"Its newest trusted sweep mark was posted at {swept}." if swept else "It has no trusted sweep mark.")


def print_children(env, n, issue, ans, declared):
    """Build and check a ledger SPLIT's children before any write, print each body and the sweep mark; the children and the sha."""
    children, sha = ledger_children(env, n, issue, ans.get("children"), declared)
    for k, c in enumerate(children, 1):
        say(f"child {k}/{len(children)} of ledger #{n}: "
            + (f"#{c['number']} {c['title']}, filed already for comments {', '.join(c['comments'])}" if "number" in c
               else f"{c['title']}; labels {c['labels']}\n{c['body']}")
            + (f"\nQuestion for the owner:\n\n{c['question']}\n" if c["question"] else ""))
    say("sweep comment:\n" + sweep_comment(sha, children))
    return children, sha


def sweep_comment(sha, children):
    """The sweep mark the ledger gets: the lines before it are swept, into the children it lists, by number once filed."""
    return (f"**Sweep** at {sha[:7]}\n\nThe children of this ledger's lines:\n\n"
            + "\n".join(f"- {'#' + str(c['number']) + ' ' if 'number' in c else ''}{c['title']}: comments {', '.join(c['comments'])}" for c in children) + "\n")


def file_children(env, n, children, sha, triage):
    """File a ledger SPLIT's children with `needs-triage`, post the sweep mark naming them on the ledger, and triage each with a
    fresh TRIAGE node, its question asked; the highest exit. A write that fails partway is a driver error naming the children filed;
    a child an earlier, stopped split filed is not filed again."""
    try:
        for c in (c for c in children if "number" not in c):
            r = env.create_issue(f"file ledger #{n}'s child {c['title']!r}", c["title"], c["body"], c["labels"])
            m = re.search(r"/issues/(\d+)\Z", r.out.strip())
            if not m:
                raise DriverError(f"gh issue create printed no issue URL: {r.out.strip()[:200]!r}")
            c["number"] = int(m.group(1))
        env.comment_issue(n, sweep_comment(sha, children))
        return max(triage(env, c["number"], False, asked=c["question"]) for c in children)
    except DriverError as x:
        filed = ", ".join(f"#{c['number']}" for c in children if "number" in c) or "none"
        raise DriverError(f"ledger #{n}'s split stopped partway; the children already filed: {filed}. {x}")
