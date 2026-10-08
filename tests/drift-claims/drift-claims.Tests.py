"""Fixture suite for bin/drift-claims.py: the hash, the four classes, and the audit-claims codec.

Every row drives the script as a subprocess against a real git repository built under a temp
directory (git is on every CI leg's PATH). The fixture repo has two commits, so `check` compares
a base against a later tree; a row that needs another file layout commits its own.

The `audit-run` row reads `$AuditRunPattern` out of bin/Get-DriftAuditTargets.ps1 itself, so it
holds the selector's real regex, not a copy.

stdlib only, like bin/*.py. Run: python3 tests/drift-claims/drift-claims.Tests.py
"""
import atexit, hashlib, importlib.util, json, os, pathlib, re, shutil, stat, subprocess, sys, tempfile

# Two levels up is the plugin root (the script under bin/) or, once vendored, the scripts dir
# itself, where the vendor copies it flat.
BASE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = next((p for p in (BASE / "drift-claims.py", BASE / "bin" / "drift-claims.py") if p.is_file()), None)
if SCRIPT is None:
    raise SystemExit(f"drift-claims.py not found under {BASE}")
SELECTOR = next((p for p in (BASE / "Get-DriftAuditTargets.ps1", BASE / "bin" / "Get-DriftAuditTargets.ps1")
                 if p.is_file()), None)

PARENT_TMP = pathlib.Path(tempfile.mkdtemp(prefix="drift-claims-test-run-"))


def _clear_readonly_and_retry(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_tmp():
    try:
        shutil.rmtree(PARENT_TMP, onerror=_clear_readonly_and_retry)
    except OSError as e:
        print(f"WARN: could not remove {PARENT_TMP}: {e}", file=sys.stderr)


atexit.register(_remove_tmp)

failures = 0


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_CONFIG_SYSTEM": os.devnull}


def git(repo, *args):
    run = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                          "-c", "core.autocrlf=false", *args], capture_output=True, env=ENV)
    if run.returncode != 0:
        raise SystemExit(f"git {args} failed: {run.stderr.decode('utf-8', 'replace')}")
    return run.stdout.decode("utf-8").strip()


def commit(repo, files):
    """Write files (bytes or str; None deletes), commit, return the sha."""
    for rel, content in files.items():
        p = repo / rel
        if content is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    return git(repo, "rev-parse", "HEAD")


def new_repo(name):
    repo = PARENT_TMP / name
    repo.mkdir()
    git(repo, "init", "-q")
    return repo


def run(*args, stdin="", cwd=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin.encode("utf-8"),
                          capture_output=True, env=ENV, cwd=cwd)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def rec(path, start, end, sha, statement="s", doc="docs/a.md"):
    return {"doc": doc, "statement": statement, "path": path, "start": start, "end": end, "sha256": sha}


def classify(repo, base, rev, records):
    code, out, err = run("check", "--base", base, "--rev", rev, "--cwd", str(repo), stdin=json.dumps(records))
    if code != 0:
        raise SystemExit(f"check exited {code}: {err}")
    return json.loads(out)


def sha_of(lines):
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def first(out):
    try:
        return json.loads(out)[0]
    except (ValueError, IndexError):
        return None


def hash_at(repo, rev, path, start, end):
    code, out, err = run("record", "--rev", rev, "--cwd", str(repo),
                         stdin=json.dumps([{"doc": "d", "statement": "s", "path": path, "start": start, "end": end}]))
    return first(out)["sha256"] if code == 0 and first(out) else None


# --- the hash ------------------------------------------------------------------------------
print("hash and record")
repo = new_repo("hash")
text = "alpha\nbeta\ngamma\ndelta\n"
r0 = commit(repo, {"f.txt": text})
expected = hashlib.sha256(b"beta\ngamma\n").hexdigest()
code, out, err = run("record", "--rev", r0, "--cwd", str(repo),
                     stdin=json.dumps([{"doc": "d", "statement": "s", "path": "f.txt", "start": 2, "end": 3}]))
check("record hashes lines start..end joined by LF plus a trailing LF", code == 0 and (first(out) or {}).get("sha256") == expected)
check("and returns the record's own fields beside it",
      first(out) == {"doc": "d", "statement": "s", "path": "f.txt", "start": 2, "end": 3, "sha256": expected})
code, out, err = run("record", "--rev", r0, "--cwd", str(repo),
                     stdin=json.dumps([{"doc": "d", "statement": "s", "path": "f.txt", "start": 1, "end": 4}]))
check("the file's last line is a line, though the blob ends in LF", code == 0 and (first(out) or {}).get("sha256") == sha_of(["alpha", "beta", "gamma", "delta"]))
good = {"doc": "d", "statement": "s", "path": "f.txt", "start": 1, "end": 1}
for what, bad in (("a range past the end of the file", {**good, "start": 3, "end": 5}),
                  ("a file missing at the revision", {**good, "path": "nope.txt"}),
                  ("a start of 0", {**good, "start": 0}),
                  ("an end before the start", {**good, "start": 3, "end": 2})):
    code, out, err = run("record", "--rev", r0, "--cwd", str(repo), stdin=json.dumps([good, bad]))
    check(f"record with {what} exits nonzero and prints no partial result", code != 0 and out == "")
code, out, err = run("record", "--rev", r0, "--cwd", str(repo), stdin="not json")
check("record on stdin that is not JSON exits nonzero and prints nothing", code != 0 and out == "")
code, out, err = run("record", "--rev", "no-such-rev", "--cwd", str(repo), stdin=json.dumps([good]))
check("record at a revision that is no commit exits nonzero, saying so", code != 0 and out == "" and "is not a commit" in err)

print("working tree and CRLF")
h_before = hash_at(repo, r0, "f.txt", 2, 3)
(repo / "f.txt").write_bytes(b"alpha\r\nBETA edited\r\ngamma\r\ndelta\r\n")
check("an uncommitted working-tree edit leaves the hash at the named revision unchanged",
      h_before == expected and hash_at(repo, r0, "f.txt", 2, 3) == expected)
git(repo, "checkout", "--", "f.txt")
crlf = new_repo("crlf")
c0 = commit(crlf, {"w.txt": b"one\r\ntwo\r\nthree\r\n"})
check("a blob committed with CRLF hashes its bytes, CR included",
      hash_at(crlf, c0, "w.txt", 1, 2) == hashlib.sha256(b"one\r\ntwo\r\n").hexdigest())

# --- the classes ---------------------------------------------------------------------------
print("classes")
repo = new_repo("classes")
base_text = "\n".join(f"line {n}" for n in range(1, 11)) + "\n"
b0 = commit(repo, {"f.txt": base_text, "g.txt": "keep\nthis\nblock\n"})
h = hash_at(repo, b0, "f.txt", 4, 6)

result = classify(repo, b0, b0, [rec("f.txt", 4, 6, h)])
check("same: the range at its old lines hashes the same", result["ranges"][0]["class"] == "same")

b1 = commit(repo, {"f.txt": "new top\nnew top 2\n" + base_text})
result = classify(repo, b0, b1, [rec("f.txt", 4, 6, h)])
got = result["ranges"][0]
check("moved: an identical block elsewhere in the file, with its new lines",
      (got["class"], got.get("new_start"), got.get("new_end")) == ("moved", 6, 8))
check("and a moved range does not make its claim stale", result["claims"] == [{"doc": "docs/a.md", "statement": "s", "stale": False}])

b2 = commit(repo, {"f.txt": base_text.replace("line 5", "line five")})
result = classify(repo, b0, b2, [rec("f.txt", 4, 6, h)])
check("changed: neither search finds the block", result["ranges"][0]["class"] == "changed")
check("and a changed range makes its claim stale", result["claims"][0]["stale"] is True)

b3 = commit(repo, {"f.txt": None})
result = classify(repo, b0, b3, [rec("f.txt", 4, 6, h)])
check("gone: the file is not a blob at the tree", result["ranges"][0]["class"] == "gone")
check("and a gone range makes its claim stale", result["claims"][0]["stale"] is True)

# A re-wrap: the block's words survive, its line breaks move.
repo = new_repo("rewrap")
old = ["intro", "The review reads the diff,", "then names every claim", "it could not verify.", "outro"]
w0 = commit(repo, {"s.md": "\n".join(old) + "\n"})
hw = hash_at(repo, w0, "s.md", 2, 4)
new = ["intro", "pad", "The review reads", "the diff, then names", "every claim it could", "not verify.", "outro"]
w1 = commit(repo, {"s.md": "\n".join(new) + "\n"})
got = classify(repo, w0, w1, [rec("s.md", 2, 4, hw)])["ranges"][0]
check("moved: whitespace collapsed across a moved line break finds the re-wrapped block",
      (got["class"], got.get("new_start"), got.get("new_end")) == ("moved", 3, 6))
line_by_line = ["intro", "pad", "The review reads the diff,", "then names every", "claim it could not verify.", "outro"]
w2 = commit(repo, {"s.md": "\n".join(line_by_line) + "\n"})
got = classify(repo, w0, w2, [rec("s.md", 2, 4, hw)])["ranges"][0]
check("and the same words wrapped differently are found, line by line or not",
      (got["class"], got.get("new_start"), got.get("new_end")) == ("moved", 3, 5))
w3 = commit(repo, {"s.md": "\n".join(new).replace("diff, then", "diff; then") + "\n"})
check("a re-wrap that also changes a word is changed",
      classify(repo, w0, w3, [rec("s.md", 2, 4, hw)])["ranges"][0]["class"] == "changed")
stored_wrong = "0" * 64
check("a stored hash the base cannot reproduce gets no collapsed search: changed",
      classify(repo, w0, w1, [rec("s.md", 2, 4, stored_wrong)])["ranges"][0]["class"] == "changed")

# The collapsed match starts and ends on line boundaries: an edit inside a line is a change.
repo = new_repo("anchored")
q_old = ["head", "retries = 3", "tail"]
q0 = commit(repo, {"a.txt": "\n".join(q_old) + "\n"})
hq = hash_at(repo, q0, "a.txt", 2, 2)
q1 = commit(repo, {"a.txt": "head\nretries = 3 * factor\ntail\n"})
check("a line that gains words after its block is changed, not moved",
      classify(repo, q0, q1, [rec("a.txt", 2, 2, hq)])["ranges"][0]["class"] == "changed")
q2 = commit(repo, {"a.txt": "pre retries =\n3 post\n"})
check("a short block whose words reappear across two unrelated lines is changed",
      classify(repo, q0, q2, [rec("a.txt", 2, 2, hq)])["ranges"][0]["class"] == "changed")
r_old = ["top", "alpha one", "beta two", "gamma three", "bottom"]
r0 = commit(repo, {"b.txt": "\n".join(r_old) + "\n"})
hr = hash_at(repo, r0, "b.txt", 2, 4)
r1 = commit(repo, {"b.txt": "top\nalpha one\nbeta two\ngamma three and more\nbottom\n"})
check("a 3-line block whose last line gains words is changed",
      classify(repo, r0, r1, [rec("b.txt", 2, 4, hr)])["ranges"][0]["class"] == "changed")
r2 = commit(repo, {"b.txt": "top\nNEW alpha one\nbeta two\ngamma three\nbottom\n"})
check("a 3-line block whose first line gains a leading word is changed",
      classify(repo, r0, r2, [rec("b.txt", 2, 4, hr)])["ranges"][0]["class"] == "changed")
r3 = commit(repo, {"b.txt": "top\nalpha one beta\ntwo gamma three\nbottom\n"})
check("control: the same block re-wrapped in place, both ends on line boundaries, is moved",
      classify(repo, r0, r3, [rec("b.txt", 2, 4, hr)])["ranges"][0]["class"] == "moved")

# The block twice: nearest to the old start wins, the earlier on a tie.
repo = new_repo("twice")
blk = ["dup a", "dup b"]
t0 = commit(repo, {"d.txt": "\n".join(blk + ["x"] * 6 + ["y"]) + "\n"})
ht = hash_at(repo, t0, "d.txt", 1, 2)
t1 = commit(repo, {"d.txt": "\n".join(["p"] * 2 + blk + ["m"] * 3 + blk + ["z"]) + "\n"})
got = classify(repo, t0, t1, [rec("d.txt", 1, 2, ht)])["ranges"][0]
check("the block present twice resolves to the copy nearest the old start",
      (got["class"], got.get("new_start")) == ("moved", 3))
got = classify(repo, t0, t1, [rec("d.txt", 10, 11, ht)])["ranges"][0]
check("and to the later copy when that one is nearer", (got["class"], got.get("new_start")) == ("moved", 8))
t2 = commit(repo, {"d.txt": "\n".join(blk + ["m"] * 2 + blk + ["z"]) + "\n"})
got = classify(repo, t0, t2, [rec("d.txt", 3, 4, ht)])["ranges"][0]
check("and to the earlier copy on a tie", (got["class"], got.get("new_start")) == ("moved", 1))

# Two collapsed matches: the one nearest the old start wins.
repo = new_repo("collapsed-twice")
n_blk = ["red green", "blue"]
n0 = commit(repo, {"n.txt": "\n".join(["x"] * 9 + n_blk + ["y"]) + "\n"})
hn = hash_at(repo, n0, "n.txt", 10, 11)
wrapped = ["red", "green blue"]
n1 = commit(repo, {"n.txt": "\n".join(wrapped + ["pad"] * 8 + wrapped + ["end"]) + "\n"})
got = classify(repo, n0, n1, [rec("n.txt", 10, 11, hn)])["ranges"][0]
check("with two collapsed matches the nearest to the old start wins, not the first",
      (got["class"], got.get("new_start")) == ("moved", 11))

# An identical copy wins over a nearer copy that only matches once whitespace is collapsed.
repo = new_repo("exact-first")
e0 = commit(repo, {"e.txt": "x\na\nb\ny\n"})
he = hash_at(repo, e0, "e.txt", 2, 3)
e1 = commit(repo, {"e.txt": "pad\na b\npad\npad\npad\npad\na\nb\n"})
got = classify(repo, e0, e1, [rec("e.txt", 2, 3, he)])["ranges"][0]
check("an identical block wins over a nearer one found only by collapsing whitespace",
      (got["class"], got.get("new_start"), got.get("new_end")) == ("moved", 7, 8))

# A claim holds the records sharing doc and statement.
repo = new_repo("claims")
k0 = commit(repo, {"a.txt": "a1\na2\n", "b.txt": "b1\nb2\n"})
ra, rb = hash_at(repo, k0, "a.txt", 1, 2), hash_at(repo, k0, "b.txt", 1, 2)
k1 = commit(repo, {"b.txt": "b1\nCHANGED\n"})
result = classify(repo, k0, k1, [rec("a.txt", 1, 2, ra, statement="one"), rec("b.txt", 1, 2, rb, statement="one"),
                                 rec("a.txt", 1, 2, ra, statement="two")])
check("a claim is stale when any one of its ranges is changed",
      {c["statement"]: c["stale"] for c in result["claims"]} == {"one": True, "two": False})
code, out, err = run("check", "--base", k0, "--rev", k1, "--cwd", str(repo), stdin=json.dumps([{"doc": "d"}]))
check("check on a record without its fields exits nonzero and prints nothing", code != 0 and out == "")

# A blob with no final newline: its last line is a line, hashed with the trailing LF.
repo = new_repo("nonl")
z0 = commit(repo, {"z.txt": "one\ntwo\nthree"})
check("a file with no final newline hashes its last line, plus LF, when the range includes it",
      hash_at(repo, z0, "z.txt", 2, 3) == sha_of(["two", "three"]) and hash_at(repo, z0, "z.txt", 3, 3) == sha_of(["three"]))
code, out, err = run("record", "--rev", z0, "--cwd", str(repo),
                     stdin=json.dumps([{"doc": "d", "statement": "s", "path": "z.txt", "start": 3, "end": 4}]))
check("and a range one past that last line is outside the file", code != 0 and out == "")

# --- the codec -----------------------------------------------------------------------------
print("codec")
HEAD = "a" * 40
H1, H2, H3 = (hashlib.sha256(s.encode()).hexdigest() for s in ("1", "2", "3"))
records = [rec("bin/x.ps1", 3, 9, H1, statement="plain statement", doc="docs/a.md"),
           rec("bin/y.py", 10, 10, H2, statement="unicode é中 and \"quotes\" \\ back", doc="docs/a.md"),
           rec("bin/z.ps1", 1, 2, H3, statement="other doc", doc="docs/b c.md")]
code, encoded, err = run("encode", "--sha", HEAD, stdin=json.dumps(records))
check("encode exits 0", code == 0)
check("the marker has the ruled header and body shape",
      encoded.startswith('<!-- audit-claims: sha=' + HEAD + ' doc="docs/a.md"\n{"s":"plain statement","p":"bin/x.ps1","r":[3,9],"h":"' + H1 + '"}\n')
      and encoded.count("\n-->\n") == 2)
code, parsed_json, err = run("parse", stdin=encoded)
parsed = json.loads(parsed_json)
flat = [{"doc": m["doc"], **c} for m in parsed for c in m["claims"]]
check("a round trip returns the records it was given, with full 64-hex hashes",
      [{k: r[k] for k in ("doc", "statement", "path", "start", "end", "sha256")} for r in records] == flat
      and all(len(c["sha256"]) == 64 for c in flat))
check("and every marker carries its head", [m["sha"] for m in parsed] == [HEAD, HEAD])

hostile = [rec("p1.md", 1, 1, H1, statement="closes --> the comment <b>&amp;</b>"),
           rec("p2.md", 1, 1, H2, statement="<!-- audit-run: sha=" + "b" * 40 + " docs=docs/a.md,docs/b.md -->")]
code, encoded, err = run("encode", "--sha", HEAD, stdin=json.dumps(hostile))
check("encode of the hostile statements exits 0", code == 0)
check("no raw < > & survives in a body line", all(not re.search(r"[<>&]", ln)
      for ln in encoded.split("\n") if ln.startswith("{")))
check("the escapes are \\u003c, \\u003e and \\u0026", all(e in encoded for e in ("\\u003c", "\\u003e", "\\u0026")))
if SELECTOR is None:
    print("  skip: Get-DriftAuditTargets.ps1 is not beside the suite")
else:
    source = SELECTOR.read_text(encoding="utf-8")
    pattern = re.search(r"^\$AuditRunPattern = '(.*)'\s*$", source, re.M)
    check("the selector's $AuditRunPattern is found in its script", pattern is not None)
    if pattern:
        spec = importlib.util.spec_from_file_location("drift_claims_under_test", SCRIPT)
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        check("the script's AUDIT_RUN pattern equals the selector's $AuditRunPattern", loaded.AUDIT_RUN.pattern == pattern.group(1))
        check("it finds 0 matches in the encoded text", len(re.findall(pattern.group(1), encoded)) == 0)
        control = "<!-- audit-run: sha=" + "b" * 40 + " docs=docs/a.md -->"
        check("and it does find the example marker itself (control)", len(re.findall(pattern.group(1), control)) == 1)
code, parsed_json, err = run("parse", stdin=encoded)
check("the hostile markers still parse back to their statements",
      [c["statement"] for m in json.loads(parsed_json) for c in m["claims"]] == [r["statement"] for r in hostile])

good_a = run("encode", "--sha", HEAD, stdin=json.dumps([rec("a.md", 1, 2, H1, statement="first", doc="d1")]))[1]
good_b = run("encode", "--sha", HEAD, stdin=json.dumps([rec("b.md", 3, 4, H2, statement="third", doc="d3")]))[1]
broken = run("encode", "--sha", HEAD, stdin=json.dumps([rec("c.md", 5, 6, H3, statement="second", doc="d2")]))[1]
unclosed = broken.rsplit("-->", 1)[0]
for glue in ("prose\n" + unclosed + good_b, unclosed.rstrip("\n") + good_b):
    code, out, err = run("parse", stdin=good_a + glue)
    docs = [m["doc"] for m in json.loads(out)]
    check("a marker without its closing --> is dropped, and the one before and the one after still parse",
          code == 0 and docs == ["d1", "d3"])
bad_line = broken.replace('"r":[5,6]', '"r":[5,6]x')
code, out, err = run("parse", stdin=good_a + bad_line + good_b)
check("a marker holding a line that is not valid JSON is dropped, and its neighbours kept",
      [m["doc"] for m in json.loads(out)] == ["d1", "d3"])
bad_hash = broken.replace(H3, H3[:-1])
code, out, err = run("parse", stdin=good_a + bad_hash + good_b)
check("a marker whose hash is not 64 hex is dropped, and its neighbours kept",
      [m["doc"] for m in json.loads(out)] == ["d1", "d3"])
code, out, err = run("parse", stdin=(good_a + good_b).replace("\n", "\r\n"))
check("markers read back from CRLF text parse", [m["doc"] for m in json.loads(out)] == ["d1", "d3"])
code, out, err = run("parse", stdin="no markers here\n")
check("text with no marker parses to an empty list", code == 0 and json.loads(out) == [])
code, out, err = run("encode", "--sha", "abc", stdin=json.dumps(records))
check("encode with a head that is not 40 hex exits 2, prints nothing and names the option",
      code == 2 and out == "" and "--sha" in err)
dq = run("encode", "--sha", HEAD, stdin=json.dumps([rec("a.md", 1, 1, H1, doc='d<>&"q')]))[1]
dq_header = dq.split("\n")[0]
check("a doc name holding <, > and & is escaped in the encode header, as \\u003c \\u003e \\u0026",
      dq_header.endswith('doc="d\\u003c\\u003e\\u0026\\"q"') and not re.search(r"[<>&]", dq_header[len("<!--"):]))
code, out, err = run("parse", stdin=dq)
check("and the escaped doc name parses back", json.loads(out)[0]["doc"] == 'd<>&"q')
for what, doc in (("a number", "5"), ("null", "null"), ("an array", '["a"]')):
    text = f'<!-- audit-claims: sha={HEAD} doc={doc}\n{{"s":"s","p":"p","r":[1,1],"h":"{H1}"}}\n-->\n'
    code, out, err = run("parse", stdin=good_a + text + good_b)
    check(f"parse drops a marker whose doc= is {what}, not a JSON string, and keeps its neighbours",
          code == 0 and [m["doc"] for m in json.loads(out)] == ["d1", "d3"])

# --- the claim-records parser --------------------------------------------------------------
print("claim-records parser")


def records_block(sha, doc, lines):
    body = "".join((ln if isinstance(ln, str) else json.dumps(ln, ensure_ascii=False, separators=(",", ":"))) + "\n"
                   for ln in lines)
    return f"<!-- claim-records: sha={sha} doc={json.dumps(doc)}\n{body}-->\n"


def claim(path, lo, hi, statement="s"):
    return {"s": statement, "p": path, "r": [lo, hi]}


text = ("Findings go here.\n\n" + records_block(HEAD, "docs/a.md", [claim("x.py", 1, 2, "one"), claim("y.py", 3, 3, "two")])
        + "\nmore prose\n" + records_block("b" * 40, "docs/b.md", [claim("z.py", 5, 6, "three")]))
code, out, err = run("parse", "--kind", "claim-records", stdin=text)
got = json.loads(out) if code == 0 else None
check("parse --kind claim-records reads every block, with its head, doc and records",
      got == [{"sha": HEAD, "doc": "docs/a.md", "dropped": [],
               "claims": [{"statement": "one", "path": "x.py", "start": 1, "end": 2},
                          {"statement": "two", "path": "y.py", "start": 3, "end": 3}]},
              {"sha": "b" * 40, "doc": "docs/b.md", "dropped": [],
               "claims": [{"statement": "three", "path": "z.py", "start": 5, "end": 6}]}])
text = records_block(HEAD, "docs/a.md", [claim("x.py", 1, 2, "one"), "not json", {"s": "k", "p": "x.py", "r": [1, 2], "h": H1},
                                         {"s": "k", "p": "x.py", "r": [0, 2]}, "", claim("y.py", 4, 4, "two")])
code, out, err = run("parse", "--kind", "claim-records", stdin=text)
got = json.loads(out)[0] if code == 0 else {}
check("a line that is no claim record is dropped and listed, and the records around it are kept",
      [c["statement"] for c in got.get("claims", [])] == ["one", "two"] and len(got.get("dropped", [])) == 3)
code, out, err = run("parse", "--kind", "claim-records", stdin=records_block(HEAD, "d", [claim("x.py", 1, 1)]).replace("-->\n", ""))
check("a claim-records block with no closing --> is dropped", code == 0 and json.loads(out) == [])
code, out, err = run("parse", "--kind", "claim-records", stdin=encoded)
check("an audit-claims marker is no claim-records block", code == 0 and json.loads(out) == [])
code, out, err = run("parse", stdin=records_block(HEAD, "d", [claim("x.py", 1, 1)]))
check("parse without --kind still reads audit-claims only", code == 0 and json.loads(out) == [])
# Entities: the drift session writes <, >, &, a double quote and a backslash as &lt; &gt; &amp;
# &quot; &#92; in every JSON string of a claim-records block, since a model's tool input can
# decode a \u escape; the parser decodes the five entities, each once.
BS = chr(92)
ent_line = '{"s":"a --&gt; b &lt;c&gt; &amp;lt; x&#92;y &quot;","p":"dir&#92;f&amp;g.py","r":[1,2]}'
code, out, err = run("parse", "--kind", "claim-records",
                     stdin=records_block(HEAD, "docs/&lt;a&gt;.md", [ent_line, claim("y.py", 3, 3, "two")]))
got = json.loads(out) if code == 0 else []
check("a claim-records line in entity form decodes to <, >, & and a backslash, and is kept",
      len(got) == 1 and got[0]["dropped"] == []
      and got[0]["claims"][0] == {"statement": "a --> b <c> &lt; x" + BS + "y " + chr(34), "path": "dir" + BS + "f&g.py",
                                  "start": 1, "end": 2}
      and got[0]["claims"][1]["statement"] == "two")
check("&amp;lt; decodes once, to &lt;, and &quot; decodes to a double quote",
      len(got) == 1 and "&lt; x" in got[0]["claims"][0]["statement"] and got[0]["claims"][0]["statement"].endswith(chr(34)))
near = '{"s":"&LT; &lt &#092; &#x5c; &Amp; &apos;","p":"dir/&amp;lt;.py","r":[1,1]}'
code, out, err = run("parse", "--kind", "claim-records", stdin=records_block(HEAD, "d", [near]))
near_got = json.loads(out) if code == 0 else []
check("a near-miss entity stays as written, and a path holding &amp;lt; decodes once, to &lt;",
      len(near_got) == 1 and near_got[0]["claims"] == [{"statement": "&LT; &lt &#092; &#x5c; &Amp; &apos;",
                                               "path": "dir/&lt;.py", "start": 1, "end": 1}])
check("a claim-records block's doc name in entity form decodes too", len(got) == 1 and got[0]["doc"] == "docs/<a>.md")
code, out, err = run("parse", "--kind", "claim-records",
                     stdin=records_block(HEAD, "d", ['{"s":"a ' + BS + 'u003c b","p":"x.py","r":[1,1]}']))
check("control: a record written with a JSON escape still reads as the character",
      code == 0 and json.loads(out)[0]["claims"][0]["statement"] == "a < b")

# --- ingest ---------------------------------------------------------------------------------
print("ingest")
ing = new_repo("ingest")
doc_a = "\n".join(f"a line {n}" for n in range(1, 11)) + "\n"
code_x = "\n".join(f"x line {n}" for n in range(1, 21)) + "\n"
ing_head = commit(ing, {"docs/a.md": "alpha\n", "docs/b.md": "beta\n", "x.py": code_x, "y.py": doc_a})
(ing / "untracked.py").write_text("u1\nu2\n", encoding="utf-8")
T0, T1, T2 = "2026-10-03T10:00:00Z", "2026-10-03T11:00:00Z", "2026-10-03T12:00:00Z"
RUN = f"<!-- audit-run: sha={ing_head} docs=docs/a.md,docs/b.md -->"
OUT_N = [0]


def ingest(comments, since=T0, head=None, extra=(), repo=None):
    """(exit code, stdout, stderr, [body of each file written])."""
    OUT_N[0] += 1
    cfile = PARENT_TMP / f"comments-{OUT_N[0]}.json"
    cfile.write_text(json.dumps(comments), encoding="utf-8")
    odir = PARENT_TMP / f"markers-{OUT_N[0]}"
    code, out, err = run("ingest", "--comments", str(cfile), "--since", since, "--head", head or ing_head,
                         "--cwd", str(repo or ing), "--out-dir", str(odir), *extra)
    bodies = [pathlib.Path(f).read_bytes().decode("utf-8") for f in out.splitlines() if f.strip()]
    return code, out, err, bodies


def comment_at(body, at):
    return {"body": body, "createdAt": at, "author": {"login": "bot"}}


def parsed(bodies):
    return [m for b in bodies for m in json.loads(run("parse", stdin=b)[1])]


def report(lines, doc="docs/a.md", sha=None):
    return comment_at("## findings\n\n" + records_block(sha or ing_head, doc, lines), T1)


good = [claim("x.py", 3, 5, "x claim"), claim("y.py", 1, 2, "y claim")]
code, out, err, bodies = ingest([report(good), comment_at(RUN, T2)])
marks = parsed(bodies)
check("ingest hashes this run's records through record and writes one audit-claims marker",
      code == 0 and len(bodies) == 1 and [(m["doc"], m["sha"]) for m in marks] == [("docs/a.md", ing_head)]
      and [(c["statement"], c["path"], c["start"], c["end"], c["sha256"]) for c in marks[0]["claims"]]
      == [("x claim", "x.py", 3, 5, sha_of(["x line 3", "x line 4", "x line 5"])),
          ("y claim", "y.py", 1, 2, sha_of(["a line 1", "a line 2"]))])
check("and prints the path of each body it wrote, one per line", out.count("\n") == 1 and out.strip().endswith(".md"))
code, out, err, bodies = ingest([comment_at(records_block(ing_head, "docs/a.md", good), T0), comment_at(RUN, T0)])
check("a comment created at or before the exported createdAt is ignored", code == 0 and bodies == [])
code, out, err, bodies = ingest([comment_at(records_block(ing_head, "docs/a.md", good), T0), comment_at(RUN, T0)], since="2026-10-03T09:00:00Z")
check("control: the same comments, created after the exported createdAt, are read", code == 0 and len(parsed(bodies)) == 1)
code, out, err, bodies = ingest([comment_at(records_block(ing_head, "docs/a.md", good), T0), comment_at(RUN, T0)], since="")
check("no exported createdAt (an issue with no comments) reads every comment", code == 0 and len(parsed(bodies)) == 1)
code, out, err, bodies = ingest([comment_at(records_block(ing_head, "docs/a.md", good), "2026-10-03T13:00:00+02:00"), comment_at(RUN, T2)], since=T1)
check("createdAt is compared as a time, not as text (13:00+02:00 is not after 11:00Z)", code == 0 and bodies == [])

code, out, err, bodies = ingest([report(good, sha="c" * 40), comment_at(RUN, T2)])
check("a block whose sha= is not the head is ignored", code == 0 and bodies == [])
code, out, err, bodies = ingest([report(good), comment_at(RUN, T2)])
check("control: the same block at the head is read", len(parsed(bodies)) == 1)

code, out, err, bodies = ingest([report(good, doc="docs/c.md"), comment_at(RUN, T2)])
check("a record for a doc that is not in docs= is dropped, and the drop is logged",
      code == 0 and bodies == [] and "docs/c.md" in err and "dropped" in err)
code, out, err, bodies = ingest([report(good, doc="docs/b.md"), comment_at(f"<!-- audit-run: sha={ing_head} docs=docs/a.md -->", T2)])
check("a doc the audit-run marker leaves out gets no claims", bodies == [] and "docs/b.md" in err)
older = comment_at(f"<!-- audit-run: sha={ing_head} docs=docs/a.md,docs/b.md -->", T1)
newer = comment_at(f"<!-- audit-run: sha={ing_head} docs=docs/b.md -->", T2)
other = comment_at(f"<!-- audit-run: sha={'d' * 40} docs=docs/a.md -->", "2026-10-03T13:00:00Z")
code, out, err, bodies = ingest([report(good), report([claim("y.py", 1, 1, "b claim")], doc="docs/b.md"), newer, older, other])
check("docs= comes from the newest audit-run marker with the head's sha, whatever sha a newer marker carries",
      [m["doc"] for m in parsed(bodies)] == ["docs/b.md"])
code, out, err, bodies = ingest([report(good)])
check("no audit-run marker for the head among this run's comments appends nothing, saying so",
      code == 0 and bodies == [] and f"no audit-run marker with sha={ing_head}" in err)

code, out, err, bodies = ingest([report(good), report(good), comment_at(RUN, T2)])
check("the same record posted twice is one record in the marker",
      code == 0 and [c["statement"] for m in parsed(bodies) for c in m["claims"]] == ["x claim", "y claim"])
(ing / "x.py").write_text((chr(10).join(["changed"] * 20)) + chr(10), encoding="utf-8")
code, out, err, bodies = ingest([report(good), comment_at(RUN, T2)])
check("a record is hashed from the head's blob, not from the working tree",
      [c["sha256"] for m in parsed(bodies) for c in m["claims"]][0] == sha_of(["x line 3", "x line 4", "x line 5"]))
git(ing, "checkout", "--", "x.py")

bad = [claim("x.py", 1, 1, "kept"), claim("untracked.py", 1, 2, "no such tracked file"), claim("nope.py", 1, 1, "missing"),
       claim("x.py", 15, 25, "past the end"), claim("x.py", 21, 21, "one past the end"), claim("x.py", 20, 20, "last line"),
       "not json at all", {"s": "k", "p": "x.py", "r": [3, 2]}]
code, out, err, bodies = ingest([report(bad), comment_at(RUN, T2)])
check("a record naming an untracked path, a range past the end, or that does not parse is dropped; the rest are kept",
      code == 0 and [c["statement"] for m in parsed(bodies) for c in m["claims"]] == ["kept", "last line"])
for what, fragment in (("an untracked path", "untracked.py"), ("a missing path", "nope.py"), ("a range past the end", "15-25"),
                       ("a range one past the end", "21-21"), ("a line that is not a record", "not json at all"),
                       ("a range with its end before its start", "\"r\"")):
    check(f"each drop is logged: {what}", any("dropped" in ln and fragment in ln for ln in err.splitlines()))
check("and nothing is logged for a record that is kept", not any("'kept'" in ln or "last line" in ln for ln in err.splitlines()))
tracked_only = new_repo("ingest-untracked")
th = commit(tracked_only, {"t.py": "t1\nt2\n"})
(tracked_only / "w.py").write_text("w1\nw2\n", encoding="utf-8")
git(tracked_only, "add", "w.py")
code, out, err, bodies = ingest([report([claim("w.py", 1, 1, "staged only")], sha=th), comment_at(f"<!-- audit-run: sha={th} docs=docs/a.md -->", T2)],
                                head=th, repo=tracked_only)
check("a file that is not in the head's tree is untracked, though the index holds it", bodies == [] and "w.py" in err)

code, out, err, bodies = ingest([comment_at("a run with findings and no records block\n", T1), comment_at(RUN, T2)])
check("a run with no claim-records block appends nothing and succeeds", code == 0 and bodies == [] and out == "")
code, out, err, bodies = ingest([])
check("a run with no comments at all appends nothing and succeeds", code == 0 and bodies == [])

hostile = [claim("x.py", 1, 1, "closes --> the comment <b>&amp;</b>"),
           claim("x.py", 2, 2, "<!-- audit-run: sha=" + "b" * 40 + " docs=docs/a.md -->")]
def ent(text):
    """A statement as the drift session writes it in a record block: <, >, &, a double quote and a
    backslash as entities."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(chr(34), "&quot;")
            .replace(chr(92), "&#92;"))


code, out, err, bodies = ingest([report([{**h, "s": ent(h["s"])} for h in hostile]), comment_at(RUN, T2)])
check("a statement that spells --> or an audit-run marker reaches the body escaped, as data",
      code == 0 and len(bodies) == 1 and all(not re.search(r"[<>&]", ln) for ln in bodies[0].split("\n") if ln.startswith("{"))
      and [c["statement"] for m in parsed(bodies) for c in m["claims"]] == [h["s"] for h in hostile])

for what, line, fragment in (("s", r'{"s":"\ud800 bad","p":"x.py","r":[1,1]}', "ud800 bad"),
                             ("p", r'{"s":"p side","p":"x\udc00.py","r":[1,1]}', "udc00")):
    code, out, err, bodies = ingest([report([claim("x.py", 2, 2, "kept"), line, claim("y.py", 1, 1, "also kept")]), comment_at(RUN, T2)])
    check(f"a record with a lone surrogate in {what} is dropped and logged, and the records beside it still make a marker",
          code == 0 and [c["statement"] for m in parsed(bodies) for c in m["claims"]] == ["kept", "also kept"]
          and any("dropped" in ln and fragment in ln for ln in err.splitlines()))

code, out, err = run("parse", "--kind", "claim-records", stdin=records_block(ing_head, "docs/a.md", [r'{"s":"p side","p":"x\udc00.py","r":[1,1]}', claim("y.py", 1, 1, "kept")]))
got = json.loads(out)[0] if code == 0 else {}
check("parse drops a record whose path is a lone surrogate, as ingest's tree check is not what refuses it",
      [c["statement"] for c in got.get("claims", [])] == ["kept"] and len(got.get("dropped", [])) == 1)

# Packing: two docs, a cap that fits one marker at a time, then one that fits both.
two = [report([claim("x.py", 1, 1, "a" * 200)], doc="docs/a.md"), report([claim("y.py", 1, 1, "b" * 200)], doc="docs/b.md"), comment_at(RUN, T2)]
code, out, err, bodies = ingest(two)
check("markers for several docs are packed into one comment under the default cap",
      code == 0 and len(bodies) == 1 and [m["doc"] for m in parsed(bodies)] == ["docs/a.md", "docs/b.md"])
one_marker = len(bodies[0]) // 2
code, out, err, bodies = ingest(two, extra=("--max-chars", str(one_marker + 10)))
check("a cap that fits one marker splits the markers across comments, each within the cap",
      code == 0 and len(bodies) == 2 and all(len(b) <= one_marker + 10 for b in bodies)
      and [m["doc"] for m in parsed(bodies)] == ["docs/a.md", "docs/b.md"])
code, out, err, bodies = ingest(two, extra=("--max-chars", str(one_marker * 2)))
check("a cap equal to both markers together still packs them into one comment (the cap is inclusive)",
      code == 0 and len(bodies) == 1)
code, out, err, bodies = ingest(two, extra=("--max-chars", str(one_marker * 2 - 1)))
check("and one character less splits them", code == 0 and len(bodies) == 2)
code, out, err, bodies = ingest(two, extra=("--max-chars", str(one_marker)))
check("a cap equal to one marker fits it: each doc gets a comment of its own",
      code == 0 and len(bodies) == 2 and all(len(b) == one_marker for b in bodies))
code, out, err, bodies = ingest(two, extra=("--max-chars", str(one_marker - 1)))
check("a doc whose single marker exceeds the cap is skipped with a warning, and no body is written",
      code == 0 and bodies == [] and "warning" in err and "docs/a.md" in err and "docs/b.md" in err)
big = [report([claim("x.py", 1, 1, "a" * 300)], doc="docs/a.md"), report([claim("y.py", 1, 1, "b")], doc="docs/b.md"), comment_at(RUN, T2)]
code, out, err, bodies = ingest(big, extra=("--max-chars", str(one_marker)))
check("the doc that fits is still written when another is skipped",
      code == 0 and [m["doc"] for m in parsed(bodies)] == ["docs/b.md"] and "docs/a.md" in err and "warning" in err)
astral = [report([claim("x.py", 1, 1, "\U0001F600" * 40)], doc="docs/a.md"), comment_at(RUN, T2)]
width = len(ingest(astral)[3][0])
code, out, err, bodies = ingest(astral, extra=("--max-chars", str(width + 39)))
check("the cap counts UTF-16 code units, the way GitHub counts characters: an astral character is two",
      code == 0 and bodies == [] and "warning" in err)
code, out, err, bodies = ingest(astral, extra=("--max-chars", str(width + 40)))
check("control: a cap of the marker's length in UTF-16 code units fits it", code == 0 and len(bodies) == 1)

# Verdicts: a verdict-records block is read by the same this-run checks and written as one marker.
def verdict(path, cls, v, statement="s"):
    return {"s": statement, "p": path, "c": cls, "v": v}


def verdicts_block(sha, doc, lines):
    body = "".join((ln if isinstance(ln, str) else json.dumps(ln, ensure_ascii=False, separators=(",", ":"))) + "\n" for ln in lines)
    return f"<!-- verdict-records: sha={sha} doc={json.dumps(doc)}\n{body}-->\n"


def verdict_report(lines, doc="docs/a.md", sha=None, at=T1):
    return comment_at("## verdicts\n\n" + verdicts_block(sha or ing_head, doc, lines), at)


def verdict_rows(bodies):
    return [json.loads(ln) for b in bodies for ln in b.split("\n") if ln.startswith("{")]


vgood = [verdict("x.py", "changed", True, "x claim"), verdict("gone.py", "gone", False, "gone claim")]
code, out, err, bodies = ingest([verdict_report(vgood), comment_at(RUN, T2)])
check("ingest writes one claim-verdicts marker, with a row per verdict, and prints its path",
      code == 0 and len(bodies) == 1 and out.strip().endswith("claim-verdicts.md")
      and bodies[0].startswith(f"<!-- claim-verdicts: sha={ing_head}\n") and bodies[0].endswith("-->\n")
      and verdict_rows(bodies) == [{"doc": "docs/a.md", "statement": "x claim", "path": "x.py", "class": "changed", "verdict": True},
                                   {"doc": "docs/a.md", "statement": "gone claim", "path": "gone.py", "class": "gone", "verdict": False}])
code, out, err, bodies = ingest([report(good), verdict_report(vgood), comment_at(RUN, T2)])
check("the claim-verdicts marker follows the audit-claims bodies, one file each",
      code == 0 and len(bodies) == 2 and bodies[0].startswith("<!-- audit-claims:") and bodies[1].startswith("<!-- claim-verdicts:"))
code, out, err, bodies = ingest([verdict_report(vgood, at=T0), comment_at(RUN, T2)])
check("a verdict-records block created at or before the exported createdAt, an earlier run's, is ignored", code == 0 and bodies == [])
code, out, err, bodies = ingest([verdict_report(vgood, at=T1), comment_at(RUN, T2)])
check("control: the same block created after the exported createdAt is read", code == 0 and len(verdict_rows(bodies)) == 2)
code, out, err, bodies = ingest([verdict_report(vgood, at=T0), comment_at(RUN, T0)], since="2026-10-03T09:00:00Z")
check("control: the same block, created after the exported createdAt, is read", code == 0 and len(verdict_rows(bodies)) == 2)
code, out, err, bodies = ingest([verdict_report(vgood, sha="c" * 40), comment_at(RUN, T2)])
check("a verdict-records block whose sha= is not the head is ignored", code == 0 and bodies == [])
code, out, err, bodies = ingest([verdict_report(vgood, doc="docs/c.md"), comment_at(RUN, T2)])
check("a verdict for a doc that is not in docs= is dropped, and the drop is logged",
      code == 0 and bodies == [] and "docs/c.md" in err and "dropped" in err)
vbad = [verdict("x.py", "changed", True, "kept"), verdict("x.py", "same", True, "class same"), verdict("x.py", "moved", True, "class moved"),
        verdict("x.py", "changed", "true", "verdict a string"), verdict("x.py", "changed", 1, "verdict a number"),
        {"s": "extra", "p": "x.py", "c": "changed", "v": True, "x": 1}, {"s": "short", "p": "x.py", "c": "changed"}, "not json"]
code, out, err, bodies = ingest([verdict_report(vbad), comment_at(RUN, T2)])
check("a verdict line with a class other than changed or gone, a verdict that is no boolean, or other keys is dropped; the rest are kept",
      code == 0 and [r["statement"] for r in verdict_rows(bodies)] == ["kept"] and err.count("dropped a line") == 7)
for what, line, fragment in (("s", r'{"s":"\udfff verdict s","p":"x.py","c":"changed","v":true}', "udfff verdict s"),
                             ("p", r'{"s":"p side","p":"x\udc00.py","c":"changed","v":true}', "udc00")):
    code, out, err, bodies = ingest([verdict_report([verdict("x.py", "changed", True, "kept"), line, verdict("y.py", "gone", False, "also kept")]), comment_at(RUN, T2)])
    check(f"a verdict line with a lone surrogate in {what} is dropped and logged, ingest exits 0 and the marker holds the good verdicts",
          code == 0 and [r["statement"] for r in verdict_rows(bodies)] == ["kept", "also kept"]
          and any("dropped" in ln and fragment in ln for ln in err.splitlines()))
code, out, err, bodies = ingest([verdict_report([verdict("x.py", "changed", True, "q")]), verdict_report([verdict("x.py", "changed", False, "q")], at=T2), comment_at(RUN, T2)])
check("one verdict per doc, statement and path: the later wins", [(r["statement"], r["verdict"]) for r in verdict_rows(bodies)] == [("q", False)])
code, out, err, bodies = ingest([verdict_report([verdict("x.py", "changed", True, "q")]), verdict_report([verdict("y.py", "changed", False, "q")]), comment_at(RUN, T2)])
check("control: the same statement at another path is a second verdict", len(verdict_rows(bodies)) == 2)
vhostile = [verdict("x.py", "changed", True, "closes --> the comment <b>&amp;</b>")]
code, out, err, bodies = ingest([verdict_report([{**v, "s": ent(v["s"])} for v in vhostile]), comment_at(RUN, T2)])
check("a verdict statement that spells --> reaches the marker escaped, as data",
      code == 0 and len(bodies) == 1 and all(not re.search(r"[<>&]", ln) for ln in bodies[0].split("\n") if ln.startswith("{"))
      and [r["statement"] for r in verdict_rows(bodies)] == [vhostile[0]["s"]])
code, out, err, bodies = ingest([verdict_report(vgood), comment_at(RUN, T2)])
one_verdict = len(bodies[0])
code, out, err, bodies = ingest([verdict_report(vgood), comment_at(RUN, T2)], extra=("--max-chars", str(one_verdict - 1)))
check("a claim-verdicts marker over the cap is skipped with a warning", code == 0 and bodies == [] and "warning" in err)
code, out, err, bodies = ingest([verdict_report(vgood), comment_at(RUN, T2)], extra=("--max-chars", str(one_verdict)))
check("control: a cap of the marker's length fits it", code == 0 and len(bodies) == 1)
code, out, err = run("parse", "--kind", "verdict-records", stdin=verdicts_block(ing_head, "docs/a.md", [verdict("x.py", "gone", True, "one"), "junk"]))
check("parse --kind verdict-records lists the verdicts and the lines it dropped",
      code == 0 and json.loads(out) == [{"sha": ing_head, "doc": "docs/a.md", "dropped": ["junk"],
                                         "verdicts": [{"statement": "one", "path": "x.py", "class": "gone", "verdict": True}]}])
code, out, err = run("parse", "--kind", "claim-records", stdin=verdicts_block(ing_head, "docs/a.md", [verdict("x.py", "gone", True)]))
check("a verdict-records block is no claim-records block", code == 0 and json.loads(out) == [])
code, out, err = run("parse", "--kind", "verdict-records",
                     stdin=verdicts_block(ing_head, "docs/&lt;a&gt;.md", ['{"s":"x --&gt; y &amp;amp;","p":"dir&#92;f&amp;g&amp;lt;.py","c":"gone","v":true}']))
check("a verdict-records line in entity form decodes, &amp;amp; once, and is kept, with its doc name",
      code == 0 and json.loads(out) == [{"sha": ing_head, "doc": "docs/<a>.md", "dropped": [],
                                         "verdicts": [{"statement": "x --> y &amp;", "path": "dir" + BS + "f&g&lt;.py", "class": "gone", "verdict": True}]}])

surrogate_doc = '"\\ud800"'
for kind, flag, before, bad, after in (
        ("audit-claims", [], good_a, good_a.replace('doc="d1"', f"doc={surrogate_doc}"), good_b),
        ("claim-records", ["--kind", "claim-records"], records_block(HEAD, "d1", [claim("x.py", 1, 1)]),
         records_block(HEAD, "d2", [claim("x.py", 1, 1)]).replace('doc="d2"', f"doc={surrogate_doc}"),
         records_block(HEAD, "d3", [claim("x.py", 1, 1)])),
        ("verdict-records", ["--kind", "verdict-records"], verdicts_block(HEAD, "d1", [verdict("x.py", "gone", True)]),
         verdicts_block(HEAD, "d2", [verdict("x.py", "gone", True)]).replace('doc="d2"', f"doc={surrogate_doc}"),
         verdicts_block(HEAD, "d3", [verdict("x.py", "gone", True)]))):
    check(f"the {kind} fixture's bad header is the lone-surrogate one", f"doc={surrogate_doc}" in bad)
    code, out, err = run("parse", *flag, stdin=before + bad + after)
    check(f"parse drops the {kind} block whose doc= holds a lone surrogate, exits 0 and keeps the blocks around it",
          code == 0 and [m["doc"] for m in json.loads(out)] == ["d1", "d3"])
(PARENT_TMP / "c-empty.json").write_text("[]", encoding="utf-8")
code, out, err = run("ingest", "--comments", str(PARENT_TMP / "c-empty.json"), "--since", T0, "--head", "d" * 40,
                     "--cwd", str(ing), "--out-dir", str(PARENT_TMP / "o-nohead"))
check("ingest with a 40-hex head that is no commit exits nonzero and prints nothing", code != 0 and out == "")

cfile = PARENT_TMP / "c-bad.json"
cfile.write_text("[]", encoding="utf-8")
for what, over in (("a head that is an abbreviation of the commit", {"--head": ing_head[:12]}), ("a cap of 0", {"--max-chars": "0"}),
                   ("a since that is no timestamp", {"--since": "yesterday"})):
    base = {"--comments": str(cfile), "--since": T0, "--head": ing_head, "--cwd": str(ing), "--out-dir": str(PARENT_TMP / "o-bad"), **over}
    code, out, err = run("ingest", *[x for kv in base.items() for x in kv])
    check(f"ingest with {what} exits 2 and prints nothing", code == 2 and out == "")
cfile.write_text("not json", encoding="utf-8")
code, out, err = run("ingest", "--comments", str(cfile), "--since", T0, "--head", ing_head, "--cwd", str(ing), "--out-dir", str(PARENT_TMP / "o-bad"))
check("ingest on a comments file that is not JSON exits nonzero and prints nothing", code != 0 and out == "")
cfile.write_text(json.dumps({"comments": [report(good), comment_at(RUN, T2)]}), encoding="utf-8")
code, out, err = run("ingest", "--comments", str(cfile), "--since", T0, "--head", ing_head, "--cwd", str(ing), "--out-dir", str(PARENT_TMP / "o-obj"))
check("the comments file may be gh's own object, {\"comments\": [...]}", code == 0 and out.count("\n") == 1)

print()
if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("all ok")
