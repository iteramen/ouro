#!/usr/bin/env python3
"""drift-claims.py -- claim evidence for the drift audit: hash line ranges, relocate moved ones,
encode or parse the `audit-claims` marker, and turn a drift run's `claim-records` into markers.

    python3 drift-claims.py record --rev <rev> [--cwd <dir>]            < records.json > records.json
    python3 drift-claims.py check --base <rev> --rev <rev> [--cwd <dir>] < records.json > result.json
    python3 drift-claims.py encode --sha <40-hex> < records.json         > markers.txt
    python3 drift-claims.py parse [--kind audit-claims|claim-records|verdict-records] < text > markers.json
    python3 drift-claims.py ingest --comments <file> --since <time> --head <40-hex> --out-dir <dir>
                                   [--cwd <dir>] [--max-chars <n>]       > bodies.txt
    python3 drift-claims.py outbox --dir <dir>                           > paths.txt

A record is {"doc", "statement", "path", "start", "end"}, 1-based and inclusive; `record` adds
"sha256", and `check` and `encode` read records that carry it. Every file is read as its git blob
(`git cat-file blob <rev>:<path>`), never from the working tree, so a CRLF checkout or an
uncommitted edit cannot change a hash. The hash is the sha256 of the blob's lines start..end,
split on LF, joined by LF and followed by one LF.

record  Hashes each record's range at --rev. A file missing at --rev, or a range outside it, is an
        error: nothing is printed and the exit is nonzero.
check   Classifies each record's range at --rev against the hash it stores:
          same     the range at its old lines hashes the same.
          moved    an identical block sits elsewhere in the file ("new_start", "new_end"). Failing
                   that, the block as it reads at --base, with every whitespace run collapsed,
                   matches the file collapsed the same way, line breaks included, and the match
                   starts on the first word of a line and ends on the last word of a line. With
                   several matches the one nearest the old start wins, the earlier on a tie.
          changed  neither search finds the block.
          gone     the file is not a blob at --rev.
        Prints {"ranges": [...], "claims": [...]}: a claim is the records sharing a doc and a
        statement, and it is "stale" when any of its ranges is changed or gone.
encode  Prints one marker per doc, for the records on stdin, at head --sha:

          <!-- audit-claims: sha=<40-hex> doc=<JSON string>
          {"s":<statement>,"p":<path>,"r":[<start>,<end>],"h":<64-hex sha256>}
          -->

        `<`, `>` and `&` in every JSON string are written \\u003c, \\u003e and \\u0026, so no
        statement closes the comment or spells an `audit-run` marker.
parse   Reads markers from the text on stdin and prints them as JSON. A marker with no closing
        `-->` line, or with a line that is not a valid claim, is dropped; the markers around it
        are kept. With --kind claim-records it reads the blocks the drift session writes instead:

          <!-- claim-records: sha=<40-hex> doc=<JSON string>
          {"s":<statement>,"p":<path>,"r":[<start>,<end>]}
          -->

        A line that is not a valid record (one with an "h" key is not) is dropped and listed
        under "dropped"; the records around it are kept. With --kind verdict-records it reads the
        blocks that carry the session's verdicts on a doc's stale claims:

          <!-- verdict-records: sha=<40-hex> doc=<JSON string>
          {"s":<statement>,"p":<path>,"c":"changed"|"gone","v":true|false}
          -->

        A line that is not exactly those four keys, with a class other than changed or gone and a
        JSON boolean for "v", is dropped and listed under "dropped". In both kinds of block, the
        entities &lt; &gt; &amp; &quot; and &#92; in a statement, a path or the doc name decode to
        <, >, &, a double quote and a backslash, each once.
ingest  Turns the claim-records blocks of one drift run into audit-claims comment bodies.
        --comments is the rolling issue's comments as JSON (a list, or gh's {"comments": [...]}),
        each with "body" and "createdAt"; --since is the newest createdAt the run saw before its
        session started, and an empty --since reads every comment. It reads only comments created
        after --since, and only blocks whose sha= is --head. The doc list is the "docs=" of the
        newest audit-run marker with sha= --head among those comments; a record for any other doc
        is dropped. A record is also dropped when it does not parse, names a path that is not in
        --head's tree, or holds a range outside that file. Every drop is logged to stderr. What
        is left is hashed as `record` hashes it at --head and encoded as `encode` does, one
        marker per doc, and the markers are packed into bodies of at most --max-chars UTF-16 code
        units (default 65536, GitHub's comment cap). A doc whose single marker is over that is
        skipped with a warning. Each body is written to <out-dir>/claims-NNN.md and its path is
        printed, one per line; no records means no file, no output and exit 0.
        It reads the verdict-records blocks of the same comments by the same checks (created
        after --since, sha= --head, a doc in docs=, a line that parses), keeps one verdict per
        doc, statement and path (the later wins), and writes them to <out-dir>/claim-verdicts.md
        as one marker, printed after the claims bodies:

          <!-- claim-verdicts: sha=<40-hex>
          {"doc":<doc>,"statement":<statement>,"path":<path>,"class":"changed"|"gone","verdict":true|false}
          -->

        with the strings escaped as `encode` escapes them. A marker over --max-chars is skipped
        with a warning; no verdicts means no file.

outbox  --dir <dir> --targets <file>
        Reads the directory a CI drift session wrote and prints the paths of what the workflow step after
        it posts: the ledger body file, then the comment files in posting order, one per line. The layout
        is body.md and comment-NNN.md (three digits), and the order is the number. It prints nothing and
        exits 1, naming every cause, when:
          - an entry is not a regular file directly in the directory (a subdirectory, a symlink), or its
            name is outside the layout;
          - there is no body.md, or no comment, or the last comment holds no audit-run marker (a session
            that stopped early), or an audit-run marker sits in any comment but the last;
          - a docs= entry of the last comment's marker is not the path of a targets[] entry of --targets, the
            targets file the run was given (a file that is not that JSON refuses the directory too);
          - a file is empty or blank, is not UTF-8, is over the applier's COMMENT_MAX characters, or
            matches one of its SECRET_SHAPES. The cause names the file and the shape, never the text.
        COMMENT_MAX and SECRET_SHAPES are read from the apply-manifest.py beside this script, so the
        limits are the applier's own; without that file outbox exits 1, and no other subcommand needs it.

Exits 0 on success, 1 on unreadable input, a refused directory or a git failure, and 2 on a usage error or a bad option value. stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys

if tuple(sys.version_info[:2]) < (3, 11):
    sys.exit(f"drift-claims.py needs Python 3.11+; this python is {sys.version_info[0]}.{sys.version_info[1]}")

sys.stdin.reconfigure(encoding="utf-8", newline="")
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stderr.reconfigure(encoding="utf-8")

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEADER = re.compile(r'<!--[ \t]*audit-claims:[ \t]+sha=([0-9a-f]{40})[ \t]+doc=(".*")[ \t]*')
HEADER_START = re.compile(r"<!--[ \t]*audit-claims:")
RECORDS_HEADER = re.compile(r'<!--[ \t]*claim-records:[ \t]+sha=([0-9a-f]{40})[ \t]+doc=(".*")[ \t]*')
RECORDS_START = re.compile(r"<!--[ \t]*claim-records:")
VERDICTS_HEADER = re.compile(r'<!--[ \t]*verdict-records:[ \t]+sha=([0-9a-f]{40})[ \t]+doc=(".*")[ \t]*')
VERDICTS_START = re.compile(r"<!--[ \t]*verdict-records:")
# The selector's $AuditRunPattern; the suite asserts the two are equal.
AUDIT_RUN = re.compile(r"<!--\s*audit-run:\s*sha=([0-9a-fA-F]+)\s+docs=([^>]*?)\s*-->")
COMMENT_CAP = 65536
OUTBOX_BODY = "body.md"
OUTBOX_COMMENT = re.compile(r"comment-([0-9]{3})\.md")


def fail(message: str) -> "NoReturn":  # noqa: F821
    sys.exit(f"drift-claims.py: {message}")


# --- reading blobs and hashing ----------------------------------------------------------------

class Repo:
    def __init__(self, cwd: str):
        self.cwd = cwd
        self.blobs: dict[tuple[str, str], list[bytes] | None] = {}

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "core.quotepath=false", "-C", self.cwd, *args],
                              capture_output=True)

    def verify(self, rev: str) -> None:
        run = self.git("rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}")
        if run.returncode != 0:
            fail(f"{rev!r} is not a commit in {self.cwd}")

    def lines(self, rev: str, path: str) -> list[bytes] | None:
        """The blob's lines (LF-split, no empty last element), or None if it is not a blob."""
        key = (rev, path)
        if key not in self.blobs:
            run = self.git("cat-file", "blob", f"{rev}:{path}")
            if run.returncode != 0:
                self.blobs[key] = None
            else:
                parts = run.stdout.split(b"\n")
                if parts[-1] == b"":
                    parts.pop()
                self.blobs[key] = parts
        return self.blobs[key]


def digest(lines: list[bytes]) -> str:
    return hashlib.sha256(b"\n".join(lines) + b"\n").hexdigest()


def span(lines: list[bytes], start: int, end: int) -> list[bytes] | None:
    return lines[start - 1:end] if 1 <= start <= end <= len(lines) else None


def tokens(lines: list[bytes]) -> list[tuple[bytes, int]]:
    return [(word, number) for number, line in enumerate(lines, 1) for word in line.split()]


def nearest(candidates: list[int], old_start: int) -> int:
    return min(candidates, key=lambda c: (abs(c - old_start), c))


def locate(repo: Repo, base: str, rev: str, rec: dict) -> dict:
    """The class of one record's range at rev, with its new lines when it moved."""
    lines = repo.lines(rev, rec["path"])
    if lines is None:
        return {"class": "gone"}
    start, end, stored = rec["start"], rec["end"], rec["sha256"]
    here = span(lines, start, end)
    if here is not None and digest(here) == stored:
        return {"class": "same"}
    size = end - start + 1
    hits = [i + 1 for i in range(len(lines) - size + 1) if digest(lines[i:i + size]) == stored]
    if hits:
        at = nearest(hits, start)
        return {"class": "moved", "new_start": at, "new_end": at + size - 1}
    old = repo.lines(base, rec["path"])
    old_block = span(old, start, end) if old is not None else None
    if old_block is not None and digest(old_block) == stored:
        want = [word for word, _ in tokens(old_block)]
        have = tokens(lines)
        n = len(want)
        hits = [i for i in range(len(have) - n + 1)
                if want and [w for w, _ in have[i:i + n]] == want
                and (i == 0 or have[i - 1][1] != have[i][1])
                and (i + n == len(have) or have[i + n][1] != have[i + n - 1][1])]
        if hits:
            first = nearest([have[i][1] for i in hits], start)
            at = next(i for i in hits if have[i][1] == first)
            return {"class": "moved", "new_start": have[at][1], "new_end": have[at + len(want) - 1][1]}
    return {"class": "changed"}


# --- the audit-claims codec -------------------------------------------------------------------

def dump(value) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def encode_marker(sha: str, doc: str, records: list[dict]) -> str:
    body = "".join(dump({"s": r["statement"], "p": r["path"], "r": [r["start"], r["end"]],
                         "h": r["sha256"]}) + "\n" for r in records)
    return f"<!-- audit-claims: sha={sha} doc={dump(doc)}\n{body}-->\n"


def valid_range(value) -> bool:
    return (isinstance(value, list) and len(value) == 2 and all(type(n) is int for n in value)
            and 1 <= value[0] <= value[1])


def parse_claim(line: str, hashed: bool = True) -> dict | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    keys = {"s", "p", "r", "h"} if hashed else {"s", "p", "r"}
    if (not isinstance(raw, dict) or set(raw) != keys or not isinstance(raw["s"], str)
            or not isinstance(raw["p"], str) or not valid_range(raw["r"])
            or (hashed and (not isinstance(raw["h"], str) or not HEX64.fullmatch(raw["h"])))):
        return None
    try:
        raw["s"].encode("utf-8")
        raw["p"].encode("utf-8")
    except UnicodeEncodeError:
        return None
    claim = {"statement": raw["s"], "path": raw["p"], "start": raw["r"][0], "end": raw["r"][1]}
    if hashed:
        claim["sha256"] = raw["h"]
    return claim


def parse_verdict(line: str) -> dict | None:
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if (not isinstance(raw, dict) or set(raw) != {"s", "p", "c", "v"} or not isinstance(raw["s"], str)
            or not isinstance(raw["p"], str) or raw["c"] not in ("changed", "gone") or type(raw["v"]) is not bool):
        return None
    try:
        raw["s"].encode("utf-8")
        raw["p"].encode("utf-8")
    except UnicodeEncodeError:
        return None
    return {"statement": raw["s"], "path": raw["p"], "class": raw["c"], "verdict": raw["v"]}


def encode_verdicts(sha: str, rows: list[dict]) -> str:
    body = "".join(dump({k: r[k] for k in ("doc", "statement", "path", "class", "verdict")}) + "\n" for r in rows)
    return f"<!-- claim-verdicts: sha={sha}\n{body}-->\n"


def read_blocks(text: str, start: re.Pattern, header_re: re.Pattern):
    """Each block that opens with `start`, as (sha, doc, body lines). One whose header does not
    parse, or that never closes with a `-->` line, comes back with body None."""
    starts = [m.start() for m in start.finditer(text)] + [len(text)]
    for begin, stop in zip(starts, starts[1:]):
        lines = text[begin:stop].replace("\r\n", "\n").split("\n")
        header = header_re.fullmatch(lines[0])
        doc = None
        if header:
            try:
                doc = json.loads(header.group(2))
            except ValueError:
                pass
        if isinstance(doc, str):
            try:
                doc.encode("utf-8")
            except UnicodeEncodeError:
                doc = None
        if not isinstance(doc, str):
            yield None, None, None
            continue
        close = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "-->"), None)
        yield header.group(1), doc, (None if close is None else lines[1:close])


def parse_markers(text: str) -> list[dict]:
    markers = []
    for sha, doc, body in read_blocks(text, HEADER_START, HEADER):
        if body is None:
            continue
        claims = [parse_claim(line) for line in body]
        if None in claims:
            continue
        markers.append({"sha": sha, "doc": doc, "claims": claims})
    return markers


ENTITIES = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": chr(34), "&#92;": chr(92)}
ENTITY = re.compile("|".join(map(re.escape, ENTITIES)))


def unentity(text: str) -> str:
    """The drift session writes <, >, &, a double quote and a backslash as these entities in a record block,
    since a model's tool input can decode a JSON escape; each is decoded once, in one pass."""
    return ENTITY.sub(lambda m: ENTITIES[m.group(0)], text)


def parse_record_blocks(text: str) -> list[dict]:
    blocks = []
    for sha, doc, body in read_blocks(text, RECORDS_START, RECORDS_HEADER):
        if body is None:
            continue
        claims, dropped = [], []
        for line in body:
            if not line.strip():
                continue
            parsed = parse_claim(line, hashed=False)
            if parsed is None:
                dropped.append(line)
            else:
                claims.append({**parsed, "statement": unentity(parsed["statement"]), "path": unentity(parsed["path"])})
        blocks.append({"sha": sha, "doc": unentity(doc), "claims": claims, "dropped": dropped})
    return blocks


def parse_verdict_blocks(text: str) -> list[dict]:
    blocks = []
    for sha, doc, body in read_blocks(text, VERDICTS_START, VERDICTS_HEADER):
        if body is None:
            continue
        verdicts, dropped = [], []
        for line in body:
            if not line.strip():
                continue
            parsed = parse_verdict(line)
            if parsed is None:
                dropped.append(line)
            else:
                verdicts.append({**parsed, "statement": unentity(parsed["statement"]), "path": unentity(parsed["path"])})
        blocks.append({"sha": sha, "doc": unentity(doc), "verdicts": verdicts, "dropped": dropped})
    return blocks


# --- commands ---------------------------------------------------------------------------------

def read_records(need_hash: bool) -> list[dict]:
    try:
        records = json.load(sys.stdin)
    except ValueError as err:
        fail(f"stdin is not JSON: {err}")
    if not isinstance(records, list):
        fail("stdin is not a JSON list of records")
    for n, r in enumerate(records):
        ok = (isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("doc", "statement", "path"))
              and valid_range([r.get("start"), r.get("end")])
              and (not need_hash or (isinstance(r.get("sha256"), str) and HEX64.fullmatch(r["sha256"]))))
        if not ok:
            fail(f"record {n} is not {{doc, statement, path, start, end{', sha256' if need_hash else ''}}}")
    return records


def cmd_record(args) -> None:
    repo = Repo(args.cwd)
    repo.verify(args.rev)
    out = []
    for n, r in enumerate(read_records(False)):
        lines = repo.lines(args.rev, r["path"])
        if lines is None:
            fail(f"record {n}: {r['path']} is not a file at {args.rev}")
        block = span(lines, r["start"], r["end"])
        if block is None:
            fail(f"record {n}: lines {r['start']}-{r['end']} are outside {r['path']} ({len(lines)} lines) at {args.rev}")
        out.append({"doc": r["doc"], "statement": r["statement"], "path": r["path"],
                    "start": r["start"], "end": r["end"], "sha256": digest(block)})
    print(json.dumps(out, ensure_ascii=False))


def cmd_check(args) -> None:
    repo = Repo(args.cwd)
    repo.verify(args.base)
    repo.verify(args.rev)
    ranges, stale = [], {}
    for r in read_records(True):
        found = locate(repo, args.base, args.rev, r)
        ranges.append({"doc": r["doc"], "statement": r["statement"], "path": r["path"],
                       "start": r["start"], "end": r["end"], **found})
        key = (r["doc"], r["statement"])
        stale[key] = stale.get(key, False) or found["class"] in ("changed", "gone")
    claims = [{"doc": d, "statement": s, "stale": v} for (d, s), v in stale.items()]
    print(json.dumps({"ranges": ranges, "claims": claims}, ensure_ascii=False))


def cmd_encode(args) -> None:
    by_doc: dict[str, list[dict]] = {}
    for r in read_records(True):
        by_doc.setdefault(r["doc"], []).append(r)
    sys.stdout.write("".join(encode_marker(args.sha, d, rs) for d, rs in by_doc.items()))


def cmd_parse(args) -> None:
    parse = {"claim-records": parse_record_blocks, "verdict-records": parse_verdict_blocks}.get(args.kind, parse_markers)
    print(json.dumps(parse(sys.stdin.read()), ensure_ascii=False))


def log(message: str) -> None:
    print(f"drift-claims.py: {message}", file=sys.stderr)


def shown(text: str) -> str:
    """Model-written text for a log line: escaped onto one line and cut short."""
    text = repr(text)
    return text if len(text) <= 100 else text[:97] + "..."


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def parse_time(text: str, what: str) -> datetime.datetime:
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        fail(f"{what} {text!r} is not an ISO 8601 timestamp")
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def read_comments(path: str, since: datetime.datetime | None) -> list[str]:
    """The bodies of the comments created after `since`, oldest first."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as err:
        fail(f"cannot read {path}: {err}")
    if isinstance(data, dict):
        data = data.get("comments")
    if not isinstance(data, list):
        fail(f"{path} is not a JSON list of comments")
    fresh = []
    for n, c in enumerate(data):
        if not (isinstance(c, dict) and isinstance(c.get("body"), str) and isinstance(c.get("createdAt"), str)):
            fail(f"comment {n} in {path} is not {{body, createdAt}}")
        when = parse_time(c["createdAt"], f"comment {n}'s createdAt")
        if since is None or when > since:
            fresh.append((when, c["body"]))
    fresh.sort(key=lambda pair: pair[0])
    return [body for _, body in fresh]


def cmd_ingest(args) -> None:
    since = parse_time(args.since, "--since") if args.since else None
    repo = Repo(args.cwd)
    repo.verify(args.head)
    bodies = read_comments(args.comments, since)

    docs = None
    for body in bodies:
        for m in AUDIT_RUN.finditer(body):
            if m.group(1).lower() == args.head:
                docs = [p.strip() for p in m.group(2).split(",") if p.strip()]
    if docs is None:
        log(f"no audit-run marker with sha={args.head} among this run's comments; nothing appended")
        docs = []

    kept: dict[str, list[dict]] = {}
    for body in bodies:
        for block in parse_record_blocks(body):
            if block["sha"] != args.head:
                continue
            doc = block["doc"]
            for line in block["dropped"]:
                log(f"dropped a line of doc {shown(doc)}'s claim-records that is not a claim record: {shown(line)}")
            if doc not in docs:
                log(f"dropped {len(block['claims'])} record(s) for doc {shown(doc)}: it is not in the audit-run docs=")
                continue
            for c in block["claims"]:
                lines = repo.lines(args.head, c["path"])
                where = f"doc {shown(doc)}, claim {shown(c['statement'])}"
                if lines is None:
                    log(f"dropped a record ({where}): {shown(c['path'])} is not a tracked file at {args.head}")
                    continue
                part = span(lines, c["start"], c["end"])
                if part is None:
                    log(f"dropped a record ({where}): lines {c['start']}-{c['end']} are outside {c['path']} ({len(lines)} lines)")
                    continue
                record = {**c, "doc": doc, "sha256": digest(part)}
                if record not in kept.setdefault(doc, []):
                    kept[doc].append(record)

    verdicts: dict[tuple[str, str, str], dict] = {}
    for body in bodies:
        for block in parse_verdict_blocks(body):
            if block["sha"] != args.head:
                continue
            doc = block["doc"]
            for line in block["dropped"]:
                log(f"dropped a line of doc {shown(doc)}'s verdict-records that is not a verdict record: {shown(line)}")
            if doc not in docs:
                log(f"dropped {len(block['verdicts'])} verdict(s) for doc {shown(doc)}: it is not in the audit-run docs=")
                continue
            for v in block["verdicts"]:
                verdicts[(doc, v["statement"], v["path"])] = {"doc": doc, **v}

    out, current = [], ""
    for doc in dict.fromkeys(docs):
        if not kept.get(doc):
            continue
        marker = encode_marker(args.head, doc, kept[doc])
        if utf16_len(marker) > args.max_chars:
            log(f"warning: skipped doc {shown(doc)}: its marker is {utf16_len(marker)} characters, over the {args.max_chars} cap")
            continue
        if current and utf16_len(current + marker) > args.max_chars:
            out.append(current)
            current = ""
        current += marker
    if current:
        out.append(current)

    os.makedirs(args.out_dir, exist_ok=True)
    for n, body in enumerate(out, 1):
        path = os.path.join(args.out_dir, f"claims-{n:03}.md")
        with open(path, "wb") as handle:
            handle.write(body.encode("utf-8"))
        print(path)
    if verdicts:
        marker = encode_verdicts(args.head, list(verdicts.values()))
        if utf16_len(marker) > args.max_chars:
            log(f"warning: skipped the claim-verdicts marker: it is {utf16_len(marker)} characters, over the {args.max_chars} cap")
        else:
            path = os.path.join(args.out_dir, "claim-verdicts.md")
            with open(path, "wb") as handle:
                handle.write(marker.encode("utf-8"))
            print(path)


def load_applier() -> tuple[int, tuple]:
    """COMMENT_MAX and SECRET_SHAPES from the apply-manifest.py beside this script, so the poster's
    limits are the applier's and not a second copy. Bytecode is not written: the plugin checkout
    must stay as the tree check left it."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply-manifest.py")
    if not os.path.isfile(path):
        fail(f"{path} is not there: outbox reads the comment cap and the secret shapes from it")
    spec = importlib.util.spec_from_file_location("apply_manifest_for_outbox", path)
    module = importlib.util.module_from_spec(spec)
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module.COMMENT_MAX, module.SECRET_SHAPES


def read_target_paths(path: str) -> set[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return {t["path"] for t in data["targets"]}
    except (OSError, ValueError, KeyError, TypeError) as err:
        fail(f"cannot read the targets of {path}: {type(err).__name__}")


def cmd_outbox(args) -> None:
    cap, shapes = load_applier()
    target_paths = read_target_paths(args.targets)
    try:
        names = sorted(os.listdir(args.dir))
    except OSError as err:
        fail(f"cannot read {args.dir}: {err}")
    problems, comments, has_body = [], [], False
    for name in names:
        path = os.path.join(args.dir, name)
        if not stat.S_ISREG(os.lstat(path).st_mode):
            problems.append(f"{shown(name)} is not a regular file directly in the directory")
        elif name == OUTBOX_BODY:
            has_body = True
        elif OUTBOX_COMMENT.fullmatch(name):
            comments.append(name)
        else:
            problems.append(f"{shown(name)} is outside the layout: {OUTBOX_BODY} and comment-NNN.md")
    if not has_body:
        problems.append(f"there is no {OUTBOX_BODY}")
    if not comments:
        problems.append("there is no comment, so no audit-run marker closes the run")
    texts = {}
    for name in ([OUTBOX_BODY] if has_body else []) + comments:
        try:
            with open(os.path.join(args.dir, name), "rb") as handle:
                text = handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            problems.append(f"{name} cannot be read as UTF-8 text: {type(err).__name__}")
            continue
        texts[name] = text
        if not text.strip():
            problems.append(f"{name} is empty")
        if len(text) > cap:
            problems.append(f"{name} is {len(text)} characters, over the {cap}-character cap")
        for what, shape in shapes:
            if re.search(shape, text):
                problems.append(f"{name} carries {what}")
    for name in comments[:-1]:
        if name in texts and AUDIT_RUN.search(texts[name]):
            problems.append(f"{name}, not the last comment, carries an audit-run marker: only the last may close the run")
    if comments and comments[-1] in texts and not AUDIT_RUN.search(texts[comments[-1]]):
        problems.append(f"{comments[-1]}, the last comment, carries no audit-run marker: the session stopped early")
    elif comments and comments[-1] in texts:
        for marker in AUDIT_RUN.finditer(texts[comments[-1]]):
            for doc in (p.strip() for p in marker.group(2).split(",") if p.strip()):
                if doc not in target_paths:
                    problems.append(f"{comments[-1]}'s audit-run marker names {shown(doc)}, which is not a target of this run")
    if problems:
        sys.exit(f"drift-claims.py: outbox refused {args.dir}, nothing to post:\n  " + "\n  ".join(problems))
    for name in [OUTBOX_BODY] + comments:
        print(os.path.join(args.dir, name))


def main() -> None:
    top = argparse.ArgumentParser(prog="drift-claims.py", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = top.add_subparsers(dest="command", required=True)
    for name, fn in (("record", cmd_record), ("check", cmd_check), ("encode", cmd_encode), ("parse", cmd_parse),
                     ("ingest", cmd_ingest), ("outbox", cmd_outbox)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name in ("record", "check"):
            p.add_argument("--rev", required=True)
            p.add_argument("--cwd", default=".")
        if name == "check":
            p.add_argument("--base", required=True)
        if name == "encode":
            p.add_argument("--sha", required=True)
        if name == "parse":
            p.add_argument("--kind", choices=("audit-claims", "claim-records", "verdict-records"), default="audit-claims")
        if name == "outbox":
            p.add_argument("--dir", required=True)
            p.add_argument("--targets", required=True)
        if name == "ingest":
            p.add_argument("--comments", required=True)
            p.add_argument("--since", required=True)
            p.add_argument("--head", required=True)
            p.add_argument("--out-dir", required=True)
            p.add_argument("--cwd", default=".")
            p.add_argument("--max-chars", type=int, default=COMMENT_CAP)
    args = top.parse_args()
    if args.command == "encode" and not HEX40.fullmatch(args.sha):
        top.error("--sha is not 40 lowercase hex digits")
    if args.command == "ingest":
        if not HEX40.fullmatch(args.head):
            top.error("--head is not 40 lowercase hex digits")
        if args.max_chars < 1:
            top.error("--max-chars is not a positive number")
        if args.since:
            try:
                datetime.datetime.fromisoformat(args.since)
            except ValueError:
                top.error(f"--since {args.since!r} is not an ISO 8601 timestamp")
    args.fn(args)


if __name__ == "__main__":
    main()
