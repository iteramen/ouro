"""Fixture suite for bin/loop-outcomes.py: what becomes of an `agent-ready` promotion, per cohort.

The script is loaded as a module. GitHub's answer is inline issue and pull-request nodes, or a gh
stand-in (a python script that proves itself first, answers every call the code under test makes
and logs each one); git runs against scratch repositories built under a temp directory with fixed
commit dates. Nothing touches the network. One row reads skills/land/SKILL.md, whose `Review:`
pattern the script must quote exactly. Each row names the rule it holds, and each row that
asserts an absence runs beside a control that must present.

The script is not vendored: in the vendored layout (the scripts flat, Test-DocsFreshness.ps1 among
them, and no bin/loop-outcomes.py) the suite prints one `skip:` line and exits 0, which is what
.github/workflows/no-binding.yml runs. In the plugin layout a missing script is red.

stdlib only; needs python3 3.11 or later, and pwsh for the one row that runs the shared resolver.
Its [[gate]] in .claude/ouro.toml: run = "python3 tests/loop-outcomes/loop-outcomes.Tests.py"
"""
import atexit, contextlib, importlib.util, io, json, os, pathlib, re, shutil, stat, subprocess, sys, tempfile, traceback

sys.dont_write_bytecode = True
BASE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = BASE / "bin" / "loop-outcomes.py"
if not SCRIPT.is_file() and (BASE / "Test-DocsFreshness.ps1").is_file():
    print("skip: no bin/loop-outcomes.py beside the flat gate scripts (vendored layout); the script ships with the plugin")
    sys.exit(0)

TMP = pathlib.Path(tempfile.mkdtemp(prefix="loop-outcomes-test-run-"))


def _remove_tmp():
    for root, _, files in os.walk(TMP):
        for name in files:
            path = os.path.join(root, name)
            try:
                os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
            except OSError:
                pass
    try:
        shutil.rmtree(TMP)
    except OSError as e:
        print(f"WARN: could not remove {TMP}: {e}", file=sys.stderr)


atexit.register(_remove_tmp)
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

failures = 0


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


def section(name, fn):
    global failures
    print(name)
    try:
        fn()
    except Exception:
        failures += 1
        print("FAIL: the section raised\n" + traceback.format_exc())


check(f"the script is there: {SCRIPT}", SCRIPT.is_file())
spec = importlib.util.spec_from_file_location("loop_outcomes", SCRIPT)
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)

# ---- builders -----------------------------------------------------------------------------


def d(day, hh=0, mm=0, ss=0):
    return f"2026-09-{day:02d}T{hh:02d}:{mm:02d}:{ss:02d}Z"


KIND = {"+": "LabeledEvent", "-": "UnlabeledEvent", "x": "ClosedEvent"}


def conn(nodes):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}


def issue(n, created, events=(), comments=(), edits=(), body="", state="OPEN", title=None):
    """events: (time, '+'|'-'|'x', label); comments: (time, author, body); edits: (time, body)."""
    nodes = [{"__typename": KIND[k], "createdAt": t, "actor": {"login": "owner"},
              **({"label": {"name": label}} if k != "x" else {})} for t, k, label in
             [(e + (None,))[:3] if len(e) == 2 else e for e in events]]
    return {"number": n, "state": state, "createdAt": created, "body": body,
            **({} if title is None else {"title": title}),
            "userContentEdits": conn([{"editedAt": t, "diff": b} for t, b in edits]),
            "comments": conn([{"createdAt": t, "author": {"login": a}, "body": b} for t, a, b in comments]),
            "timelineItems": conn(nodes)}


def promoted(n, t=None, **kw):
    """An issue promoted at t (default 10 Sep 10:00) after its other events."""
    events = list(kw.pop("events", ()))
    return issue(n, kw.pop("created", d(1)), [(t or d(10, 10), "+", "agent-ready")] + events, **kw)


def pr(n, body, state="MERGED", merged=None, oid=None, comments=()):
    return {"number": n, "state": state, "mergedAt": merged, "body": body,
            "mergeCommit": {"oid": oid} if oid else None,
            "comments": conn([{"createdAt": t, "author": {"login": a}, "body": b} for t, a, b in comments])}


def fake(n):
    return f"{n:040x}"


def git_in(repo, *args, env=None):
    p = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", "-c", "user.name=t",
                        "-c", "user.email=t@example.invalid", *args], cwd=repo, capture_output=True,
                       env={**ENV, **(env or {})})
    if p.returncode != 0:
        raise RuntimeError(f"git {args} failed: {p.stderr.decode('utf-8', 'replace')}")
    return p.stdout.decode("utf-8").strip()


def make_repo(specs):
    """A repository whose commits are specs: msg (or a function of the earlier shas), date, files."""
    repo = pathlib.Path(tempfile.mkdtemp(prefix="repo-", dir=TMP))
    git_in(repo, "init", "-q", "-b", "main")
    shas = []
    for s in specs:
        for path, text in s.get("files", {}).items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        msg = s["msg"](shas) if callable(s["msg"]) else s["msg"]
        git_in(repo, "add", "-A")
        git_in(repo, "commit", "-q", "--allow-empty", "-m", msg,
               env={"GIT_AUTHOR_DATE": s["date"], "GIT_COMMITTER_DATE": s["date"]})
        shas.append(git_in(repo, "rev-parse", "HEAD"))
    git_in(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    lo.GIT_CWD = str(repo)
    return repo, shas


NOW = "2026-10-06T00:00:00+00:00"
BASE_REPO, _ = make_repo([{"msg": "init", "date": d(1), "files": {"a.txt": "x\n"}}])
BASE_COMMITS = lo.first_parent("origin/main")


def at(text):
    return lo.utc(text if "T" in text else text + "T00:00:00+00:00")


def analyze(issues, prs, commits=None, now=NOW, since="2026-08-01", approvers=("owner",)):
    return lo.analyze(issues, prs, BASE_COMMITS if commits is None else commits, set(approvers), at(now), at(since))


def of(attempts, n):
    return [a for a in attempts if a["issue"] == n]


def first(attempts, n):
    return of(attempts, n)[0]


@contextlib.contextmanager
def patched(**kw):
    old = {k: getattr(lo, k) for k in kw}
    for k, v in kw.items():
        setattr(lo, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(lo, k, v)


def call_main(argv):
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")
    err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = lo.main(argv)
    finally:
        sys.stdout, sys.stderr = saved
    out.flush()
    err.flush()
    return code, out.buffer.getvalue().decode("utf-8"), err.buffer.getvalue().decode("utf-8")


# ---- attempts, cohorts, size ----------------------------------------------------------------


def attempts_rows():
    a = promoted(1, events=[(d(10, 11), "+", "agent-ready")])
    b = issue(2, d(1), [(d(10, 10), "+", "agent-ready"), (d(10, 12), "-", "agent-ready"), (d(10, 12), "+", "blocked"),
                        (d(11, 10), "+", "agent-ready")])
    res, _ = analyze([a, b], [])
    check("a repeated labeled agent-ready is one attempt", len(of(res, 1)) == 1)
    check("a re-promotion after a demotion is a second attempt", len(of(res, 2)) == 2)
    check("the first attempt of a re-promoted issue is bounded by the second", of(res, 2)[0]["limit"] == lo.utc(d(11, 10)))
    c = issue(3, d(1), [(d(10, 12), "-", "agent-ready"), (d(10, 13), "+", "agent-ready")])
    res, _ = analyze([c], [])
    check("an unlabeled agent-ready while the label is absent starts nothing, a later labeled does", len(res) == 1)
    res, _ = analyze([promoted(4, t=d(10)), promoted(5, t="2026-07-31T23:59:59Z")], [], since="2026-09-01")
    check("--since keeps a promotion on or after it", len(of(res, 4)) == 1)
    res, _ = analyze([promoted(6, t=d(10)), promoted(7, t="2026-09-09T23:59:59Z")], [], since="2026-09-10")
    check("--since keeps a promotion at exactly the instant, and drops one a second before",
          len(of(res, 6)) == 1 and len(of(res, 7)) == 0)
    check("--since drops an earlier promotion (control: the same issue is kept without it)",
          len(of(res, 5)) == 0 and len(of(analyze([promoted(5, t="2026-07-31T23:59:59Z")], [], since="2026-07-01")[0], 5)) == 1)


def size_rows():
    cases = [
        ("the body at the promotion is read, not today's",
         promoted(1, body="Size: M", edits=[(d(9), "Size: S\n"), (d(12), "Size: M\n")]), "S"),
        ("a revision edited 90 s after the promotion is the body at promotion",
         promoted(2, edits=[(d(9), "Size: S"), (d(10, 10, 1, 30), "Size: M")]), "M"),
        ("a revision edited 3 min after is not (control for the 2-minute reach)",
         promoted(3, edits=[(d(9), "Size: S"), (d(10, 10, 3), "Size: M")]), "S"),
        ("when every revision is later the oldest is read",
         promoted(4, body="Size: L", edits=[(d(12), "Size: M"), (d(13), "Size: S")]), "M"),
        ("with no history the current body is read", promoted(5, body="Size: M"), "M"),
        ("a body with no Size line is none", promoted(6, body="no size here"), "none"),
        ("Size: L is none", promoted(7, body="x\nSize: L"), "none"),
        ("a lowercase size line is none", promoted(8, body="size: S"), "none"),
        ("an indented Size line is none", promoted(9, body="  Size: M"), "none"),
        ("the first Size line wins", promoted(10, body="Intro\nSize: M\nSize: S"), "M"),
        ("a CRLF body reads its value", promoted(11, body="Size: M\r\nrest"), "M"),
        ("a value that only starts with S is none", promoted(12, body="Size: SM"), "none"),
        ("no space after the colon still reads", promoted(13, body="Size:S"), "S"),
    ]
    res, _ = analyze([c[1] for c in cases], [])
    for what, node, want in cases:
        check(f"size: {what}", first(res, node["number"])["size"] == want)


def cohort_rows():
    iss = [
        promoted(1, t=d(6, 23, 59)), promoted(2, t=d(7, 0, 0)),
        promoted(3, t="2026-09-07T01:00:00+02:00"), promoted(4, t="2026-12-31T12:00:00Z"),
        promoted(5, t="2027-01-01T12:00:00Z"),
    ]
    res, _ = analyze(iss, [], now="2027-02-01T00:00:00+00:00")
    check("a promotion at Sunday 23:59 UTC is in W36", first(res, 1)["week"] == "2026-W36")
    check("a promotion at Monday 00:00 UTC is in W37", first(res, 2)["week"] == "2026-W37")
    check("an offset timestamp is read in UTC", first(res, 3)["week"] == "2026-W36")
    check("the week carries the ISO year, not the calendar year", first(res, 5)["week"] == "2026-W53")
    check("a Thursday of the last week stays in it", first(res, 4)["week"] == "2026-W53")


# ---- landings -------------------------------------------------------------------------------


def landing_rows():
    repo, shas = make_repo([
        {"msg": "init", "date": d(1), "files": {"a.txt": "x\n"}},
        {"msg": "feat: drill (#51)", "date": d(12), "files": {"a.txt": "y\n"}},
        {"msg": "feat: both (#52)", "date": d(13), "files": {"a.txt": "z\n"}},
        {"msg": "feat: direct (#83)", "date": d(14), "files": {"a.txt": "w\n"}},
    ])
    git_in(repo, "checkout", "-q", "-b", "side", shas[0])
    (repo / "b.txt").write_text("s\n")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "feat: side (#80)", env={"GIT_AUTHOR_DATE": d(15), "GIT_COMMITTER_DATE": d(15)})
    git_in(repo, "checkout", "-q", "main")
    git_in(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side", env={"GIT_AUTHOR_DATE": d(16), "GIT_COMMITTER_DATE": d(16)})
    git_in(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    commits = lo.first_parent("origin/main")
    arabic = chr(0x661) + chr(0x666)
    iss = [promoted(n) for n in (10, 11, 12, 13, 14, 15, 150, 16, 17, 18, 22, 23, 24, 81, 82)]
    iss.append(promoted(19, events=[(d(10, 12), "-", "agent-ready"), (d(10, 12), "+", "needs-ruling")]))
    iss[-1]["timelineItems"]["nodes"] += issue(19, d(1), [(d(14), "+", "agent-ready")])["timelineItems"]["nodes"]
    prs = [
        pr(50, "Fixes #10", merged=d(11), oid=fake(50)),
        pr(51, "Fixes #11", state="CLOSED"),
        pr(53, "`Fixes #12`", merged=d(11), oid=fake(53)),
        pr(54, "Closes the loop; see Fixes #13 in the text", merged=d(11), oid=fake(54)),
        pr(55, "fixes #14", merged=d(11), oid=fake(55)),
        pr(56, "Fixes #150", merged=d(11), oid=fake(56)),
        pr(57, f"Fixes #{arabic}", merged=d(11), oid=fake(57)),
        pr(58, "Fixes #17", state="CLOSED"),
        pr(59, "Fixes #17", merged=d(12), oid=fake(59)),
        pr(60, "Fixes #18", merged=d(9), oid=fake(60)),
        pr(61, "Fixes #19", merged=d(15), oid=fake(61)),
        pr(52, "Fixes #22", merged=d(13, 5), oid=fake(52)),
        pr(62, "Fixes #23\nFixes #24", merged=d(11), oid=fake(62)),
        pr(80, "Fixes #81", state="CLOSED"),
        pr(83, "Fixes #82", state="CLOSED"),
    ]
    res, _ = analyze(iss, prs, commits)
    out = lambda n: first(res, n)["outcome"]
    land = lambda n: first(res, n).get("landing")
    check("a merged pull request lands, on its merge commit", out(10) == "landed" and land(10) == fake(50))
    check("a closed-unmerged pull request lands by its (#N) first-parent subject, on that commit",
          out(11) == "landed" and land(11) == shas[1] and first(res, 11)["land_time"] == lo.utc(d(12)))
    check("a backticked Fixes line counts", out(12) == "landed")
    check("a mid-line Fixes does not count", out(13) == "open")
    check("a lowercase fixes line counts", out(14) == "landed")
    check("Fixes #150 does not name #15 (the count holds it as a prefix), and does name #150",
          out(15) == "open" and out(150) == "landed")
    check("a non-ASCII digit run names nothing", out(16) == "open")
    check("an unmerged pull request plus a later landed one reads as landed on the later",
          out(17) == "landed" and land(17) == fake(59))
    check("a landing before the promotion is not the attempt's", out(18) == "open")
    a1, a2 = of(res, 19)
    check("a landing after the second promotion belongs to the second attempt, not the first",
          a1["outcome"] == "refused" and a2["outcome"] == "landed")
    check("the merge wins over a first-parent subject for the same pull request",
          land(22) == fake(52) and first(res, 22)["land_time"] == lo.utc(d(13, 5)))
    check("a pull request naming two issues gives two landed attempts on one landing commit",
          out(23) == "landed" and out(24) == "landed" and land(23) == land(24) == fake(62))
    f = lo.figures([first(res, 23), first(res, 24)], lo.utc(NOW))
    check("per attempt that is 2 landed and per landing commit 1", f["landed"] == 2 and f["commits"] == 1)
    check("a (#N) subject reachable only through a merge's second parent is not a landing", out(81) == "open")
    check("control: the same shape directly on the first-parent line lands", out(82) == "landed")
    cafe = "feat: caf" + chr(0xE9) + " " + chr(0x4E2D) + " (#95)"
    repo, shas = make_repo([{"msg": "init", "date": d(1), "files": {"a.txt": "x\n"}}, {"msg": cafe, "date": d(12), "files": {"a.txt": "y\n"}}])
    check("a first-parent subject with non-ASCII characters is read as UTF-8", lo.first_parent("origin/main")[0]["subject"] == cafe)
    res, _ = analyze([promoted(95)], [pr(95, "Fixes #95", state="CLOSED")], lo.first_parent("origin/main"))
    check("and lands the attempt by that subject's (#N)", first(res, 95)["outcome"] == "landed")
    swap = [(d(10, 12), "-", "agent-ready"), (d(10, 12), "+", "needs-ruling")]
    again = [(d(11, 10), "+", "agent-ready")]
    res, _ = analyze([promoted(91, events=swap + again), promoted(92, events=swap + again)],
                     [pr(91, "Fixes #91", merged=d(11, 10), oid=fake(91)), pr(92, "Fixes #92", merged=d(11, 9, 59, 59), oid=fake(92))])
    a1, a2 = of(res, 91)
    check("a landing at the instant of the next promotion is the next attempt's, not the first's",
          a1["outcome"] == "refused" and a2["outcome"] == "landed")
    a1, a2 = of(res, 92)
    check("control: a landing a second before the next promotion is the first attempt's",
          a1["outcome"] == "landed" and a2["outcome"] == "open")


# ---- reversal -------------------------------------------------------------------------------


def revert_rows():
    def land_commit(name, number, date):
        return {"msg": f"feat: {name} (#{number})", "date": date, "files": {f"{name}.txt": name}}
    specs = [{"msg": "init", "date": d(1), "files": {"a.txt": "x\n"}}]
    for name, number in (("za", 601), ("zb", 602), ("zc", 603), ("zd", 604), ("ze", 605), ("zf", 606), ("zg", 607), ("zh", 609), ("zi", 610)):
        specs.append(land_commit(name, number, d(10, 12)))
    idx = {name: i + 1 for i, name in enumerate(("za", "zb", "zc", "zd", "ze", "zf", "zg", "zh", "zi"))}
    specs += [
        {"msg": lambda s: f'Revert "feat: za (#601)"\n\nThis reverts commit {s[idx["za"]]}.', "date": d(20), "files": {"r1": "1"}},
        {"msg": 'Revert "feat: zb (#602)"', "date": d(20), "files": {"r2": "1"}},
        {"msg": lambda s: f"Undo zc\n\nThis reverts commit {s[idx['zc']]}.", "date": d(20), "files": {"r3": "1"}},
        {"msg": lambda s: f'Revert "feat: zd (#604)"\n\nThis reverts commit {s[idx["zd"]]}.', "date": d(25), "files": {"r4": "1"}},
        {"msg": 'Revert "feat: ze (#605)"', "date": d(24, 12), "files": {"r5": "1"}},
        {"msg": 'Revert "feat: zf (#606)"', "date": d(24, 12, 1), "files": {"r6": "1"}},
        {"msg": 'Revert "feat: zgg (#608)"', "date": d(20), "files": {"r7": "1"}},
        {"msg": lambda s: f"Undo another\n\nThis reverts commit {s[idx['za']]}.", "date": d(21), "files": {"r8": "1"}},
    ]
    repo, shas = make_repo(specs)
    commits = lo.first_parent("origin/main")
    numbers = {"za": 41, "zb": 42, "zc": 43, "zd": 44, "ze": 45, "zf": 46, "zg": 47, "zh": 48, "zi": 49}
    iss = [promoted(n, t=d(9)) for n in numbers.values()]
    prs = [pr(600 + i, f"Fixes #{numbers[name]}", merged=d(10, 12), oid=shas[idx[name]]) for i, name in enumerate(numbers)]
    res, _ = analyze(iss, prs, commits)
    rev = lambda name: first(res, numbers[name])["reversed"]
    check("a revert naming the sha and the subject reverses", rev("za"))
    check("a revert by its subject alone reverses", rev("zb"))
    check("a revert by its This-reverts line alone reverses", rev("zc"))
    check("a revert after W does not", not rev("zd"))
    check("a revert exactly W after the landing does", rev("ze"))
    check("a revert one minute past W does not", not rev("zf"))
    check("a revert whose subject only starts with the landing's subject does not", not rev("zg"))
    check("a This-reverts line naming another landing does not", not rev("zh"))
    check("control: a landing nothing reverts is not reversed", not rev("zi"))
    count = lambda n: (lambda f: (f["clean"], f["reversed"], f["assisted"]))(lo.figures([first(res, n)], lo.utc(NOW)))
    check("a reversed landing is not clean", count(numbers["za"]) == (0, 1, 0))
    check("a landing neither reversed nor assisted is clean", count(numbers["zi"]) == (1, 0, 0))


ANCHOR_FILE = "line one is here\nsecond line is here\n"
LANDED_LINE = "def landed_function(arg): return arg + 1"


def anchor_repo():
    repo, shas = make_repo([
        {"msg": "init", "date": d(1), "files": {"src/app.py": ANCHOR_FILE}},
        {"msg": "feat: add landed (#70)", "date": d(10), "files": {"src/app.py": ANCHOR_FILE + LANDED_LINE + "\n"}},
        {"msg": "chore: unrelated", "date": d(12), "files": {"src/other.py": "o\n"}},
    ])
    return shas, lo.first_parent("origin/main")


def anchor_rows():
    shas, commits = anchor_repo()
    s0, sl, sm = shas[0][:7], shas[1][:7], shas[2][:7]
    landed = promoted(31, t=d(9))
    landing = pr(70, "Fixes #31", merged=d(10), oid=shas[1])

    def bug(n, created, body, labels=("bug",), title=None):
        return issue(n, created, [(created, "+", x) for x in labels], body=body, title=title)

    def verdict(*bugs):
        res, skipped = analyze([landed, *bugs], [landing], commits)
        return first(res, 31)["reversed"], skipped
    frag = f'"{LANDED_LINE}"'
    head = f"## Anchors @ {sm}\n\n"
    item = f"- `src/app.py` - the function - {frag} :3\n"
    check("a bug filed within W whose stamped anchor blames to the landing reverses it", verdict(bug(91, d(15), head + item)) == (True, 0))
    check("the same issue filed after W does not", verdict(bug(92, d(30), head + item))[0] is False)
    check("a bug filed before the landing does not", verdict(bug(97, d(5), head + item))[0] is False)
    check("an issue that is not labelled bug does not", verdict(bug(93, d(15), head + item, labels=("enhancement",)))[0] is False)
    sweep = lambda t: verdict(bug(110, d(15), head + item, title=t))[0]
    check("a bug titled with sweep and ledger lines does not reverse", sweep("gates: sweep six ledger lines") is False)
    check("control: sweep alone still reverses", sweep("branch-sweep: a defect") is True)
    check("control: ledger line alone still reverses", sweep("drop a ledger line from the docs") is True)
    check("the title match is case-insensitive", sweep("Gates: Sweep Six Ledger Lines") is False)
    check("an issue node with no title key (the old cache) still reverses", verdict(bug(111, d(15), head + item))[0] is True)
    check("a landing charged by a sweep child and an ordinary bug is still reversed",
          verdict(bug(112, d(15), head + item, title="sweep ledger line"), bug(113, d(16), head + item, title="a defect"))[0] is True)
    check("an excluded bug adds nothing to the skipped count", verdict(bug(114, d(15), item, title="sweep ledger line")) == (False, 0))
    check("the report's footer says the reversal figure is an upper bound",
          "reversal figure is an upper bound" in lo.render("o/r", analyze([landed], [landing], commits)[0], 0, lo.utc(NOW), at("2026-08-01")))
    check("an unstamped anchor does not reverse and raises the skipped count", verdict(bug(94, d(15), item)) == (False, 1))
    check("a stamp git cannot read does not reverse and raises the skipped count",
          verdict(bug(95, d(15), f"- `src/app.py` - {frag} @ deadbee\n")) == (False, 1))
    check("an anchor whose line blames to an earlier commit does not",
          verdict(bug(96, d(15), f'{head}- `src/app.py` - "second line is here" :2\n')) == (False, 0))
    check("a stamp on the line wins over the heading's (the older stamp holds no such line)",
          verdict(bug(98, d(15), f"{head}- `src/app.py` - {frag} @ {s0}\n")) == (False, 0))
    check("control: the stamp on the line alone, at the landing, reverses",
          verdict(bug(100, d(15), f"- `src/app.py` - {frag} @ {sl}\n")) == (True, 0))
    check("an anchor under Doc impact on close is not read",
          verdict(bug(101, d(15), f"## Doc impact on close\n\n- `src/app.py` - {frag} @ {sm}\n"))[0] is False)
    check("a fragment of 11 characters is not an anchor", verdict(bug(99, d(15), f'{head}- `src/app.py` - "def landed_" :3\n'))[0] is False)
    check("control: a fragment of 12 characters is", verdict(bug(102, d(15), f'{head}- `src/app.py` - "def landed_f" :3\n'))[0] is True)
    check("a line that is not a list item is not an anchor line", verdict(bug(104, d(15), f"{head}`src/app.py` - {frag} :3\n")) == (False, 0))
    check("a line with no backticked file is not an anchor line", verdict(bug(103, d(15), f"{head}- the function {frag} :3\n")) == (False, 0))
    bug_ok = bug(91, d(15), head + item)
    ruled = promoted(31, t=d(9), comments=[(d(9, 12), "owner", "**Ruling**\n\nfine")])
    res, _ = analyze([ruled, bug_ok], [landing], commits)
    a = first(res, 31)
    check("a landing both reversed and assisted is reported as reversed and counts in both", a["reversed"] and a["assisted"])
    f = lo.figures([a], lo.utc(NOW))
    check("so the row has 0 clean, 1 reversed, 1 assisted", (f["clean"], f["reversed"], f["assisted"]) == (0, 1, 1))
    text = lo.render("o/r", res, 0, lo.utc(NOW), at("2026-08-01"))
    check("and both rates read 100% (1/1) in the report", text.count("100% (1/1)") >= 2)
    repo, shas = make_repo([{"msg": "init", "date": d(1), "files": {"src/app.py": ANCHOR_FILE}}])
    git_in(repo, "checkout", "-q", "-b", "side")
    (repo / "src" / "app.py").write_bytes((ANCHOR_FILE + LANDED_LINE + "\n").encode("utf-8"))
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "side work", env={"GIT_AUTHOR_DATE": d(9), "GIT_COMMITTER_DATE": d(9)})
    git_in(repo, "checkout", "-q", "main")
    git_in(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #71", "side", env={"GIT_AUTHOR_DATE": d(10), "GIT_COMMITTER_DATE": d(10)})
    merge = git_in(repo, "rev-parse", "HEAD")
    git_in(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    res, _ = analyze([promoted(32, t=d(9)), bug(105, d(15), f"- `src/app.py` - {frag} @ {merge[:7]}\n")],
                     [pr(71, "Fixes #32", merged=d(10), oid=merge)], lo.first_parent("origin/main"))
    check("a merge-commit landing owns the lines its side branch brought: blame follows the first parent", first(res, 32)["reversed"])


# ---- assisted -------------------------------------------------------------------------------


def assisted_rows():
    def assisted(comments=(), events=(), pr_comments=(), approvers=("owner",)):
        node = promoted(101, comments=list(comments), events=list(events))
        prn = pr(100, "Fixes #101", merged=d(12), oid=fake(100), comments=list(pr_comments))
        res, _ = analyze([node], [prn], approvers=approvers)
        assert first(res, 101)["outcome"] == "landed"
        return first(res, 101)["assisted"]
    check("an approver's Ruling comment in the window assists", assisted([(d(11), "owner", "**Ruling**\nGo")]))
    check("a non-approver's does not", not assisted([(d(11), "someone", "**Ruling**\nGo")]))
    check("an approver's login in another case assists", assisted([(d(11), "OWNER", "**Ruling**\nGo")]))
    check("an approver listed in another case than the author's login assists",
          assisted([(d(11), "owner", "**Ruling**\nGo")], approvers=("Owner",)))
    check("the bot's does not", not assisted([(d(11), "github-actions", "**Ruling**\nGo")]))
    check("an approver's before the promotion does not", not assisted([(d(9), "owner", "**Ruling**\nGo")]))
    check("an approver's after the landing does not", not assisted([(d(13), "owner", "**Ruling**\nGo")]))
    check("one on the landing pull request assists", assisted(pr_comments=[(d(11), "owner", "**Ruling**\nGo")]))
    check("the Ruling mark is a prefix of the first line", assisted([(d(11), "owner", "**Ruling** (2026-10-06)\nGo")]))
    check("the first line is trimmed", assisted([(d(11), "owner", "  **Ruling**  \nGo")]))
    check("a case variant is not the mark", not assisted([(d(11), "owner", "**ruling**\nGo")]))
    check("the mark must be on the first line", not assisted([(d(11), "owner", "\n**Ruling**\nGo")]))
    check("the mark must start the first line", not assisted([(d(11), "owner", "see **Ruling**\nGo")]))
    check("an approver's other comment does not assist", not assisted([(d(11), "owner", "looks fine")]))
    check("the approver's reply before an in-window needs-ruling clearing assists",
          assisted([(d(11), "owner", "yes, go")], [(d(11, 1), "-", "needs-ruling")]))
    check("the same reply with no clearing does not", not assisted([(d(11), "owner", "yes, go")]))
    check("a reply after the clearing does not", not assisted([(d(11, 2), "owner", "yes, go")], [(d(11, 1), "-", "needs-ruling")]))
    check("a clearing outside the window does not", not assisted([(d(11), "owner", "yes, go")], [(d(13), "-", "needs-ruling")]))
    node = promoted(101, comments=[(d(11), "owner", "**Ruling**\nGo")])
    res, _ = analyze([node], [pr(100, "Fixes #101", merged=d(12), oid=fake(100))])
    f = lo.figures(res, lo.utc(NOW))
    check("an assisted landing is not clean", (f["clean"], f["reversed"], f["assisted"]) == (0, 0, 1))
    check("the clearing of another label is not a ruling's", not assisted([(d(11), "owner", "yes, go")], [(d(11, 1), "-", "blocked")]))
    check("a non-approver's reply before the clearing does not", not assisted([(d(11), "someone", "yes, go")], [(d(11, 1), "-", "needs-ruling")]))
    ruled = promoted(102, comments=[(d(11), "owner", "**Ruling**\nGo")])
    plain = promoted(103)
    res, _ = analyze([ruled, plain], [pr(100, "Fixes #102\nFixes #103", merged=d(12), oid=fake(100))])
    f = lo.figures(res, lo.utc(NOW))
    check("two attempts on one landing commit, one assisted: the commit is assisted and not clean",
          (f["landed"], f["assisted"], f["commits"], f["commit_assisted"], f["commit_clean"]) == (2, 1, 1, 1, 0))


# ---- refusals, parking, precision -----------------------------------------------------------

T = d(10, 12)


def refusal(comments=(), after=((T, "+", "needs-ruling"),), n=1):
    node = promoted(n, comments=list(comments), events=[(T, "-", "agent-ready"), *after])
    return first(analyze([node], [])[0], n)


def refusal_rows():
    a = refusal([(d(10, 11, 30), "owner", "**Stop:** review cap\n\ntext")])
    check("a Stop: review cap comment gives the review-cap reason", (a["outcome"], a["reason"]) == ("refused", "review cap"))
    for reason in lo.STOP_REASONS:
        check(f"the reason {reason} is read", refusal([(d(10, 11, 30), "owner", f"**Stop:** {reason}\n\nx")])["reason"] == reason)
    check("a Triage comment gives a triage reversal", refusal([(d(10, 11, 30), "owner", "**Triage**\n\nx")])["reason"] == "triage reversal")
    check("no marker is unclassified", refusal()["reason"] == "unclassified")
    for who in ("stranger", "o", None):
        check(f"a Stop line from {who} does not count", refusal([(d(10, 11, 30), who, "**Stop:** review cap\n\nx")])["reason"] == "unclassified")
        check(f"a Triage comment from {who} does not count", refusal([(d(10, 11, 30), who, "**Triage**\n\nx")])["reason"] == "unclassified")
    check("the bot's Stop line counts", refusal([(d(10, 11, 30), "github-actions", "**Stop:** review cap\n\nx")])["reason"] == "review cap")
    check("the bot's Triage comment counts", refusal([(d(10, 11, 30), "github-actions", "**Triage**\n\nx")])["reason"] == "triage reversal")
    check("an approver's login in another case counts", refusal([(d(10, 11, 30), "OWNER", "**Triage**\n\nx")])["reason"] == "triage reversal")
    check("the bot's login in another case counts", refusal([(d(10, 11, 30), "GitHub-Actions", "**Stop:** review cap\n\nx")])["reason"] == "review cap")
    check("an approver listed in mixed case matches a lower-case login", lo.trusted("owner", {"Owner"}))
    check("a login that only holds an approver's name is not trusted", not lo.trusted("owner2", {"owner"}))
    check("a stranger's newer Stop line does not hide the approver's",
          refusal([(d(10, 11, 30), "owner", "**Stop:** dead anchor\n\nx"), (d(10, 11, 40), "stranger", "**Stop:** review cap\n\nx")])["reason"] == "dead anchor")
    for what, body in (("a case variant", "**Stop:** Review cap"), ("trailing text", "**Stop:** review cap."), ("an unknown reason", "**Stop:** unknown reason"),
                       ("a second space", "**Stop:**  review cap"), ("the mark on line 2", "text\n**Stop:** review cap")):
        check(f"{what} is unclassified", refusal([(d(10, 11, 30), "owner", body)])["reason"] == "unclassified")
    check("a Stop line and a Triage comment in the window give the stop line's reason",
          refusal([(d(10, 11, 40), "owner", "**Triage**\n\nx"), (d(10, 11, 30), "owner", "**Stop:** dead anchor\n\nx")])["reason"] == "dead anchor")
    check("the newest Stop comment wins",
          refusal([(d(10, 11, 0), "owner", "**Stop:** review cap\n\nx"), (d(10, 11, 30), "owner", "**Stop:** open decision\n\nx")])["reason"] == "open decision")
    check("a Stop comment 60 minutes before the removal is in the window", refusal([(d(10, 11), "owner", "**Stop:** review cap\n\nx")])["reason"] == "review cap")
    check("one 61 minutes before is not", refusal([(d(10, 10, 59), "owner", "**Stop:** review cap\n\nx")])["reason"] == "unclassified")
    check("one 10 minutes after is in the window", refusal([(d(10, 12, 10), "owner", "**Stop:** review cap\n\nx")])["reason"] == "review cap")
    check("one 11 minutes after is not", refusal([(d(10, 12, 11), "owner", "**Stop:** review cap\n\nx")])["reason"] == "unclassified")
    check("needs-triage is a refusal", refusal(after=((T, "+", "needs-triage"),))["outcome"] == "refused")
    p = refusal(after=((T, "+", "blocked"),))
    check("blocked is parked, and named", (p["outcome"], p["parked"]) == ("parked", "blocked"))
    check("idea is parked", refusal(after=((T, "+", "idea"),))["parked"] == "idea")
    check("another state label is parked and named", refusal(after=((T, "+", "human-ready"),))["parked"] == "human-ready")
    p = refusal(after=())
    check("no state label is parked as none", (p["outcome"], p["parked"]) == ("parked", "none"))
    check("a needs-ruling 11 minutes after is not the swap", refusal(after=((d(10, 12, 11), "+", "needs-ruling"),))["outcome"] == "parked")
    check("a needs-ruling 10 minutes before is the swap", refusal(after=((d(10, 11, 50), "+", "needs-ruling"),))["outcome"] == "refused")
    check("needs-ruling beside blocked is a refusal", refusal(after=((T, "+", "blocked"), (T, "+", "needs-ruling")))["outcome"] == "refused")
    f = lo.figures([refusal(after=((T, "+", "blocked"),), n=1), refusal(after=((T, "+", "idea"),), n=2)], lo.utc(NOW))
    check("a parked attempt is neither a refusal nor a delivery", f["refused"] == 0 and f["landed"] == 0 and len(f["parked"]) == 2)


def precision_rows():
    stop = lambda reason: [(d(10, 11, 30), "owner", f"**Stop:** {reason}\n\nx")]
    swap = [(T, "-", "agent-ready"), (T, "+", "needs-ruling")]
    again = [(d(11, 10), "+", "agent-ready")]
    iss = [
        promoted(1, comments=stop("open decision"), events=swap + again, edits=[(d(9), "same body\n"), (d(11, 9), "same body   ")]),
        promoted(2, comments=stop("dead anchor"), events=swap + again, edits=[(d(9), "v1"), (d(11, 9), "v2")]),
        promoted(3, comments=stop("review cap"), events=swap + again, edits=[(d(9), "same"), (d(11, 9), "same")]),
        promoted(4, comments=stop("gate uncovered"), events=swap),
        promoted(5, comments=stop("open decision"), events=swap + [(d(11), "x")]),
        promoted(6, events=swap + again, edits=[(d(9), "same"), (d(11, 9), "same")]),
    ]
    res, _ = analyze(iss, [])
    refused = [a for a in res if a["outcome"] == "refused"]
    f = lo.figures(refused, lo.utc(NOW))
    check("six stops are six refusals", f["refused"] == 6)
    check("by reason: 2 open decision, 1 dead anchor, 1 review cap, 1 gate uncovered, 1 unclassified",
          dict(f["reasons"]) == {"open decision": 2, "dead anchor": 1, "review cap": 1, "gate uncovered": 1, "unclassified": 1})
    check("a decision stop re-promoted unchanged (trimmed) is unnecessary", first(res, 1)["unnecessary"] and first(res, 1)["resolved"])
    check("one re-promoted changed is upheld", first(res, 2)["resolved"] and not first(res, 2)["unnecessary"])
    check("an unresolved decision stop is pending", not first(res, 4)["resolved"])
    check("a decision stop whose issue closed is resolved and upheld", first(res, 5)["resolved"] and not first(res, 5)["unnecessary"])
    check("resolved 3, unnecessary 1, pending 1: cap and unclassified stops are outside the figure",
          (f["resolved"], f["unnecessary"], f["pending"]) == (3, 1, 1))
    text = lo.render("o/r", res, 0, lo.utc(NOW), at("2026-08-01"))
    check("precision (3 - 1) / 3 reads 67% (2/3)", "67% (2/3)" in text)
    check("a refused cap stop re-promoted unchanged is still a refusal (the cap working)", first(res, 3)["outcome"] == "refused")


# ---- checkpoints, open, abandoned, report ---------------------------------------------------


def checkpoint_rows():
    cp = [(d(10, 9, 59), "+", "checkpoint")]
    swap = [(T, "-", "agent-ready"), (T, "+", "needs-ruling")]
    finding = [(d(10, 11), "owner", "**Checkpoint finding**\n\nthe finding")]
    iss = [
        promoted(1, events=cp + swap, comments=finding),
        promoted(2, events=cp + [(T, "x")]),
        promoted(3, events=[(d(10, 10, 5), "+", "checkpoint")] + swap, comments=finding),
        promoted(4, events=[(d(10, 10, 11), "+", "checkpoint")] + swap, comments=finding),
        promoted(5, events=swap, comments=finding),
        promoted(6, events=cp + swap, comments=[(d(10, 11), "owner", "**Checkpoint finding** more\n\nx")]),
    ]
    res, _ = analyze(iss, [])
    check("a finding followed by a swap to needs-ruling is finding delivered", first(res, 1)["outcome"] == "finding")
    check("a checkpoint closed with no finding is abandoned", first(res, 2)["outcome"] == "abandoned")
    check("checkpoint labeled 5 minutes after the promotion is in the cohort", first(res, 3)["checkpoint"])
    check("labeled 11 minutes after is not", not first(res, 4)["checkpoint"])
    check("a finding comment on a non-checkpoint attempt delivers nothing", first(res, 5)["outcome"] == "refused")
    check("the finding mark is exact", first(res, 6)["outcome"] == "refused")
    who = lambda n, w: first(analyze([promoted(n, events=cp + swap, comments=[(d(10, 11), w, "**Checkpoint finding**\n\nx")])], [])[0], n)["outcome"]
    check("an approver's finding delivers (control)", who(8, "owner") == "finding")
    check("a stranger's finding delivers nothing", who(9, "stranger") == "refused")
    check("a finding with no author delivers nothing", who(10, None) == "refused")
    check("the bot's finding delivers", who(11, "github-actions") == "finding")
    check("an approver's finding in another case delivers", who(12, "OWNER") == "finding")
    both, _ = analyze([promoted(7, events=cp, comments=finding)], [pr(7, "Fixes #7", merged=d(12), oid=fake(7))])
    check("a checkpoint that landed and posted a finding is landed", first(both, 7)["outcome"] == "landed")
    text = lo.render("o/r", res, 0, lo.utc(NOW), at("2026-08-01"))
    check("the checkpoint table counts the 4 checkpoint attempts apart from the 2 build attempts",
          "## Checkpoint cohort" in text and "| all | 4 |" in text)


def open_rows():
    res, _ = analyze([promoted(1, t=d(20)), promoted(2, t=d(10), events=[(d(9), "x")], created=d(1))], [], now=d(23, 12))
    check("an open attempt reports its age in whole days", first(res, 1)["outcome"] == "open" and first(res, 1)["age"] == 3)
    check("a closure before the promotion is not an abandonment", first(res, 2)["outcome"] == "open")
    res, _ = analyze([promoted(3, t=d(10), events=[(d(12), "x")])], [])
    check("an issue closed within the attempt with no landing is abandoned", first(res, 3)["outcome"] == "abandoned")
    text = lo.render("o/r", analyze([promoted(1, t=d(20))], [], now=d(23, 12))[0], 0, lo.utc(d(23, 12)), at("2026-08-01"))
    check("the open cell names the oldest age", "(oldest 3 d)" in text)
    for landed, now, word in ((d(25), d(30), "provisional"), (d(10), d(30), "final")):
        res, _ = analyze([promoted(1, t=d(9))], [pr(5, "Fixes #1", merged=landed, oid=fake(5))], now=now)
        text = lo.render("o/r", res, 0, lo.utc(now), at("2026-08-01"))
        check(f"a row whose landing is {'younger' if word == 'provisional' else 'older'} than W is {word}",
              f"0% (0/1) {word}" in text)
    bare = lo.render("o/r", analyze([promoted(1)], [])[0], 0, lo.utc(NOW), at("2026-08-01"))
    check("a row with no landing carries neither word", "provisional" not in bare and "final" not in bare)


def report_rows():
    iss = [promoted(1, body="Size: S"), promoted(2, body="Size: M"), promoted(3)]
    prs = [pr(5, "Fixes #1", merged=d(11), oid=fake(5))]
    res, skipped = analyze(iss, prs)
    text = lo.render("o/r", res, 4, lo.utc(NOW), at("2026-09-01"))
    check("the header names the slug, the range and W",
          "# Loop outcomes for o/r" in text and "from 2026-09-01 to 2026-10-06 UTC" in text and "W = 14 days" in text)
    check("a cohort row is named by week and size", "| 2026-W37 S |" in text and "| 2026-W37 M |" in text and "| 2026-W37 none |" in text)
    check("the sizes are in S, M, none order", text.index("2026-W37 S") < text.index("2026-W37 M") < text.index("2026-W37 none"))
    check("the report ends with the two blind-spot lines",
          text.rstrip().endswith("An attempt that leaves no tracker trace is not seen.") and "4 anchor(s)" in text)
    check("the build and checkpoint tables are both there", "## Build cohorts" in text and "## Checkpoint cohort" in text)
    check("the default range is the Monday eleven ISO weeks back", lo.default_since(lo.utc("2026-10-06T10:00:00+00:00")) == lo.utc("2026-07-20T00:00:00+00:00"))
    check("a Monday now is its own week's Monday", lo.default_since(lo.utc("2026-10-05T00:00:00+00:00")) == lo.utc("2026-07-20T00:00:00+00:00"))
    del skipped


def rate_rows():
    repo, shas = make_repo([
        {"msg": "init", "date": d(1), "files": {"a.txt": "x\n"}},
        {"msg": "feat: x (#601)", "date": d(10, 12), "files": {"x.txt": "x"}},
        {"msg": 'Revert "feat: x (#601)"', "date": d(12), "files": {"r": "1"}},
    ])
    commits = lo.first_parent("origin/main")
    swap = [(T, "-", "agent-ready"), (T, "+", "needs-ruling")]
    cp = [(d(10, 9, 59), "+", "checkpoint")]
    finding = [(d(10, 11), "owner", "**Checkpoint finding**\n\nthe finding")]
    body = "Size: S"
    iss = [
        promoted(1, body=body), promoted(2, body=body, comments=[(d(11), "owner", "**Ruling**\nGo")]), promoted(3, body=body),
        promoted(4, body=body, events=swap), promoted(5, body=body), promoted(6, body=body, events=[(T, "-", "agent-ready"), (T, "+", "blocked")]),
        promoted(7, body=body, events=cp, comments=finding), promoted(8, body=body, events=cp), promoted(9, body=body, events=cp + swap),
    ]
    prs = [pr(11, "Fixes #1", merged=d(11), oid=fake(11)), pr(12, "Fixes #2", merged=d(11), oid=fake(12)),
           pr(13, "Fixes #3", merged=d(10, 12), oid=shas[1])]
    res, _ = analyze(iss, prs, commits)
    text = lo.render("o/r", res, 0, lo.utc(NOW), at("2026-09-01"))
    row = lambda title: next(x for x in text.split("## ")[1:] if x.startswith(title)).split("\n| all |", 1)[1].split("\n", 1)[0].split("|")
    build, checkpoint = row("Build cohorts"), row("Checkpoint cohort")
    names = ("attempts", "landed", "clean", "reversed", "assisted", "refused", "parked", "abandoned", "open",
             "delivery", "refusal", "reversal", "assist")
    got = dict(zip(names, (c.strip() for c in build)))
    want = {"attempts": "6", "landed": "3", "clean": "1", "reversed": "1", "assisted": "1", "refused": "1",
            "parked": "1 (blocked 1)", "abandoned": "0", "delivery": "20% (1/5)", "refusal": "17% (1/6)",
            "reversal": "33% (1/3) final", "assist": "33% (1/3)"}
    check("a cohort with attempts above landings above 0 and one open attempt: counts", all(got[k] == v for k, v in want.items()))
    check("delivery is clean over attempts less open", got["delivery"] == "20% (1/5)")
    check("refusal is refused over all attempts, open included", got["refusal"] == "17% (1/6)")
    check("reversal is reversed over landings", got["reversal"] == "33% (1/3) final")
    check("assist is assisted over landings", got["assist"] == "33% (1/3)")
    check("the open cell counts the open attempt", got["open"].startswith("1 (oldest "))
    cp = [c.strip() for c in checkpoint]
    check("the finding rate is findings over attempts less open", cp[:7] == ["3", "0", "1", "1", "0", "0", "1"] and cp[7] == "50% (1/2)")



# ---- the quality table: the Review: line ------------------------------------------------------

LINE = "Review: rounds:2 findings:H0/M3/L4 false-sentences:1"
LINE_FIELDS = (2, 0, 3, 4, 1)
FENCED = "```\nReview: rounds:9 findings:H9/M9/L9 false-sentences:9\n```"


def review_rows():
    skill = (BASE / "skills" / "land" / "SKILL.md").read_text(encoding="utf-8")
    spans = re.findall(r"`(\^Review: [^`\n]*)`", skill)
    check("the land skill states the Review pattern once, as one backticked span", len(spans) == 1)
    check("the script's pattern is that span, character for character", spans == [lo.REVIEW_PATTERN])
    cases = [
        ("a body with no line has none", "Fixes #1\n\nSome text", None),
        ("a line under the Fixes line reads its five fields", f"Fixes #1\n{LINE}\nrest", LINE_FIELDS),
        ("a CRLF body reads the same line", f"Fixes #1\r\n{LINE}\r\nrest\r\n", LINE_FIELDS),
        ("control: the same body in LF reads it", f"Fixes #1\n{LINE}\nrest\n", LINE_FIELDS),
        ("a line at the very end of the body, no newline", f"Fixes #1\n{LINE}", LINE_FIELDS),
        ("trailing spaces and tabs are admitted", f"{LINE} \t \n", LINE_FIELDS),
        ("a line inside a backtick fence is skipped", f"Fixes #1\n{FENCED}\ntext", None),
        ("a line inside a tilde fence is skipped", "~~~\n" + LINE + "\n~~~\n", None),
        ("a fence is not closed by a shorter one", "````\n```\n" + LINE + "\n````\n", None),
        ("a fence is not closed by the other character", "```\n~~~\n" + LINE + "\n```\n", None),
        ("a fence opened with an info string is a fence", "```text\n" + LINE + "\n```\n", None),
        ("a fence indented three spaces is a fence", "   ```\n" + LINE + "\n   ```\n", None),
        ("a fence line with text after it does not close the fence", "```\nx\n``` y\n" + LINE + "\n```\n", None),
        ("a CRLF fence is a fence", "```\r\n" + LINE + "\r\n```\r\n", None),
        ("control: a line after the closing fence counts", FENCED + "\n" + LINE + "\n", LINE_FIELDS),
        ("an unclosed fence runs to the end of the body", "```\n" + LINE + "\n", None),
        ("a line holding both fence and closing backticks opens nothing", "```x```\n" + LINE + "\n", LINE_FIELDS),
        ("a line indented four spaces is no fence", "    ```\n" + LINE + "\n", LINE_FIELDS),
        ("Review: none reads as none", "Fixes #1\nReview: none\n", "none"),
        ("Review: none in CRLF reads as none", "Review: none\r\n", "none"),
        ("unread keeps its other fields", "Review: rounds:1 findings:H0/M0/L2 false-sentences:unread", (1, 0, 0, 2, "unread")),
        ("two lines: the first counts", LINE + "\nReview: rounds:5 findings:H1/M1/L1 false-sentences:3", LINE_FIELDS),
        ("two lines, the first none: it counts", "Review: none\n" + LINE, "none"),
        ("a malformed first line is no line and the next valid one counts",
         "Review: rounds:2 findings:H0/M3/L4\n" + LINE, LINE_FIELDS),
    ]
    for what, body, want in cases:
        check(f"review line: {what}", lo.review_of(body) == want)
    near = [
        ("a missing field", "Review: rounds:2 findings:H0/M3/L4"),
        ("the equals spelling", "Review: rounds=2 findings=H0/M3/L4 false-sentences=1"),
        ("a lowercase word", "review: none"),
        ("none with a trailing word", "Review: none."),
        ("an indented line", " Review: none"),
        ("a quoted line", "> Review: none"),
        ("no space after the colon", "Review:none"),
        ("a prefix", "xReview: none"),
        ("a count with a trailing letter", "Review: rounds:2 findings:H0/M3/L4 false-sentences:1x"),
        ("a negative count", "Review: rounds:2 findings:H0/M3/L4 false-sentences:-1"),
        ("a non-ASCII digit", "Review: rounds:" + chr(0x661) + " findings:H0/M3/L4 false-sentences:1"),
        ("the Unread word capitalised", "Review: rounds:2 findings:H0/M3/L4 false-sentences:Unread"),
        ("a letter other than H, M, L", "Review: rounds:2 findings:H0/X3/L4 false-sentences:1"),
        ("a line that only holds the word", "Review:"),
        ("the line in the middle of a sentence", "see Review: none for it"),
    ]
    for what, body in near:
        check(f"review line near miss, read as no line: {what}", lo.review_of(body) is None)


# ---- the quality table: PR rows ---------------------------------------------------------------


def qpr(n, review, merged):
    return pr(n, f"Fixes #{n}" + ("\n" + review if review else ""), merged=merged, oid=fake(n))


def quality_pr_rows():
    prs = [
        qpr(101, LINE, d(8)),
        qpr(102, "Review: rounds:0 findings:H1/M0/L0 false-sentences:0", d(9)),
        qpr(103, "Review: rounds:1 findings:H0/M2/L0 false-sentences:unread", d(10)),
        qpr(104, "Review: none", d(11)),
        qpr(105, "", d(11)),
        qpr(106, FENCED, d(11)),
        pr(107, "Fixes #107\r\nReview: rounds:1 findings:H0/M1/L1 false-sentences:2\r\n", merged=d(11), oid=fake(107)),
        qpr(108, "Review: rounds:3 findings:H0/M0/L1 false-sentences:0", d(14)),
        pr(109, "Fixes #109\nFixes #110\n" + LINE, merged=d(15), oid=fake(109)),
        pr(111, LINE, merged=d(11), oid=fake(111)),
        pr(112, "Fixes #112\n" + LINE, state="CLOSED"),
        qpr(113, LINE, "2026-08-20T00:00:00Z"),
    ]
    got = lo.pr_quality(prs, BASE_COMMITS, at("2026-09-01"))
    check("PR rows are keyed by the ISO week of the landing", sorted(got) == ["2026-W37", "2026-W38"])
    w37, w38 = lo.pr_figures(got["2026-W37"]), lo.pr_figures(got["2026-W38"])
    check("W37 counts 7 landed PRs: a PR with no Fixes line, an unmerged one and one before --since are left out",
          w37["landed"] == 7)
    check("W37: 5 with a line (none included), 1 none, 2 with no line (a fenced line is no line)",
          (w37["line"], w37["none"], w37["no_line"]) == (5, 1, 2))
    check("W37: 1 unread, 3 PRs whose false-sentences is a number", (w37["unread"], w37["counted"]) == (1, 3))
    check("W37: false sentences total 3 (1 + 0 + 2), 2 of the 3 PRs have one", (w37["false"], w37["with_false"]) == (3, 2))
    check("W37: findings H, M, L over the 4 numeric lines, the unread one included: 1 / 6 / 5",
          (w37["h"], w37["m"], w37["l"]) == (1, 6, 5))
    check("W37: rounds over the 4 numeric lines (2, 0, 1, 1): median 1, max 2", (w37["median"], w37["max"]) == (1, 2))
    check("W38: a PR naming two issues counts once, and the line counts", w38["landed"] == 2 and w38["line"] == 2)
    check("W38: rounds 3 and 2 give median 2.5 and max 3", (w38["median"], w38["max"]) == (2.5, 3))
    none_only = lo.pr_figures([None, "none"])
    check("a week with no numeric line has no median or maximum and no mean",
          none_only["median"] is None and none_only["max"] is None and none_only["counted"] == 0)
    check("control: the week with PRs and no line reads them as no line, not as clean",
          lo.pr_figures([None, None])["no_line"] == 2 and lo.pr_figures([None, None])["line"] == 0)
    text = lo.render_quality(got, [], at("2026-09-01"), at(NOW))
    row = next(x for x in text.splitlines() if x.startswith("| 2026-W37 |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    check("W37 row: landed, line, none, no line, unread", cells[1:6] == ["7", "5", "1", "2", "1"])
    check("W37 row: false total 3, mean 1.00, share 67% (2/3)", cells[6:9] == ["3", "1.00", "67% (2/3)"])
    check("W37 row: H, M, L and the rounds median and maximum", cells[9:14] == ["1", "6", "5", "1", "2"])
    check("a no-line PR is stated never to be counted as clean", "never counted as clean" in text)


# ---- the quality table: the CI flake rate -----------------------------------------------------


def att(run_id, attempt, conclusion, start, wf=10, sha="a" * 40, created=None):
    return {"id": run_id, "workflow_id": wf, "name": f"wf{wf}", "head_sha": sha, "run_attempt": attempt,
            "conclusion": conclusion, "run_started_at": start, "created_at": created or start}


def run_of(*attempts):
    """The run list's entry (the last attempt) with the earlier attempts folded in; as the API has it,
    every attempt carries the run's original created_at."""
    return {**attempts[-1], "created_at": attempts[0]["created_at"],
            "attempts": [{**a, "created_at": attempts[0]["created_at"]} for a in attempts[:-1]]}


def flake_rows():
    runs = [
        run_of(att(1, 1, "failure", d(8, 1)), att(1, 2, "success", d(8, 2))),
        run_of(att(2, 1, "failure", d(9, 1), sha="b" * 40)),
        run_of(att(3, 1, "success", d(9, 2), sha="b" * 40)),
        run_of(att(4, 1, "failure", d(9, 3), wf=11, sha="c" * 40)),
        run_of(att(5, 1, "success", d(9, 4), wf=12, sha="c" * 40)),
        run_of(att(6, 1, "success", d(8, 5), wf=14, sha="d" * 40)),
        run_of(att(7, 1, "failure", d(9, 5), wf=14, sha="d" * 40)),
        run_of(att(8, 1, "failure", d(9, 6), wf=15, sha="e" * 40)),
        run_of(att(9, 1, "success", d(9, 7), wf=15, sha="f" * 40)),
        run_of(att(10, 1, "failure", d(9, 8), wf=16, sha="1" * 40)),
        run_of(att(11, 1, "failure", d(9, 9), wf=17, sha="2" * 40), att(11, 2, "cancelled", d(9, 10), wf=17, sha="2" * 40)),
        run_of(att(12, 1, "cancelled", d(9, 11), wf=18, sha="3" * 40), att(12, 2, "success", d(9, 12), wf=18, sha="3" * 40)),
        run_of(att(13, 1, "failure", d(9, 13), wf=19, sha="4" * 40), att(13, 2, "cancelled", d(9, 14), wf=19, sha="4" * 40),
               att(13, 3, "success", d(9, 15), wf=19, sha="4" * 40)),
        run_of(att(14, 1, "success", d(9, 16), wf=20, sha="5" * 40), att(14, 2, "failure", d(9, 17), wf=20, sha="5" * 40)),
        run_of(att(15, 1, "skipped", d(9, 18), wf=21, sha="6" * 40)),
        run_of(att(16, 1, None, d(9, 19), wf=22, sha="7" * 40)),
        run_of(att(17, 1, "failure", d(11), wf=23, sha="8" * 40), att(17, 2, "success", d(14), wf=23, sha="8" * 40)),
        run_of(att(18, 1, "failure", "2026-08-30T10:00:00Z", wf=24, sha="9" * 40)),
        run_of(att(19, 1, "failure", d(9, 20), wf=25, sha="9" * 40), att(19, 2, "failure", d(9, 21), wf=25, sha="9" * 40)),
    ]
    got = lo.flake_attempts(runs, at("2026-09-01"))
    verdict = {(g["run"], g["attempt"]): g["flaky"] for g in got}
    check("a failed attempt then a passing re-run attempt is flaky", verdict.get((1, 1)) is True)
    check("a failure and a later run of the same workflow on the same SHA that passed is flaky", verdict.get((2, 1)) is True)
    check("control: the later passing run is not itself a failed attempt", (3, 1) not in verdict)
    check("a later success of another workflow on the same SHA does not make it flaky", verdict.get((4, 1)) is False)
    check("an earlier success of the same workflow and SHA does not make it flaky", verdict.get((7, 1)) is False)
    check("a later success on another SHA does not make it flaky", verdict.get((8, 1)) is False)
    check("a lone failure is failed", verdict.get((10, 1)) is False)
    check("a failure then a cancelled re-run is failed, and the cancelled attempt is neither",
          verdict.get((11, 1)) is False and (11, 2) not in verdict)
    check("a cancelled attempt then a passing re-run counts as neither", not any(k[0] == 12 for k in verdict))
    check("a failure, a cancelled re-run, then a passing one is flaky", verdict.get((13, 1)) is True)
    check("a success then a failing re-run is failed, and the success is not counted",
          verdict.get((14, 2)) is False and (14, 1) not in verdict)
    check("a skipped attempt and one with no conclusion yet are neither", not any(k[0] in (15, 16) for k in verdict))
    check("a failed attempt keeps the week of its start, not of the passing re-run", [g["week"] for g in got if g["run"] == 17] == ["2026-W37"])
    check("an attempt that started before --since is left out", not any(k[0] == 18 for k in verdict))
    straddle = lo.flake_attempts([run_of(att(30, 1, "failure", d(13, 23)), att(30, 2, "failure", d(14, 1), created=d(14, 1)))],
                                 at("2026-09-01"))
    check("a re-run attempt keys on its own start, not on the run's original creation: Sunday then Monday",
          [g["week"] for g in straddle] == ["2026-W37", "2026-W38"])
    check("two failed attempts of one run with no pass are two failed attempts",
          verdict.get((19, 1)) is False and verdict.get((19, 2)) is False)
    flaky = sum(verdict.values())
    check("4 flaky and 8 failed of the 12 failed attempts", (flaky, len(verdict) - flaky) == (4, 8))
    text = lo.render_quality({}, got, at("2026-09-01"), at(NOW))
    row = next(x for x in text.splitlines() if x.startswith("| all |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    check("the all row reads the flake figures: 12 failed attempts, 4 flaky, 8 failed, 33% (4/12)", cells[-4:] == ["12", "4", "8", "33% (4/12)"])
    check("the weeks are by the failed attempt's start", "| 2026-W37 |" in text)
    empty = lo.render_quality({}, [], at("2026-09-01"), at(NOW))
    weeks = [x.split("|")[1].strip() for x in empty.splitlines() if x.startswith("| 20") or x.startswith("| all")]
    check("every ISO week from --since to now has a row, zero-filled, then all",
          weeks == ["2026-W36", "2026-W37", "2026-W38", "2026-W39", "2026-W40", "2026-W41", "all"])
    check("with no run the rate reads n/a, not 0%", "n/a" in empty and "0%" not in empty)


# ---- the comment flag, the cache, failed reads -----------------------------------------------

FAKE_BINDING = {"repo.slug": "o/r", "repo.default_branch": "main", "owner.ruling_approvers": '["owner"]'}


def fake_binding(extra=None):
    values = {**FAKE_BINDING, **(extra or {})}

    def read(key, optional=False):
        if key in values:
            return values[key]
        if optional:
            return None
        raise lo.ReadError(f"no such key: {key}")
    return read


def flag_rows():
    iss = [promoted(1, body="Size: S")]
    prs = [pr(5, "Fixes #1", merged=d(11), oid=fake(5))]
    writes = []
    rec = dict(create_rolling_issue=lambda slug, title: writes.append(("create", slug, title)) or 99,
               post_comment=lambda slug, n, report: writes.append(("post", slug, n, report)))
    runs = [run_of(att(1, 1, "failure", d(11)), att(1, 2, "success", d(12)))]
    base = dict(fetch=lambda slug: (iss, prs), fetch_runs=lambda slug, since, now: runs, first_parent=lambda ref: BASE_COMMITS)
    with patched(binding=fake_binding(), resolve_issue=lambda t: 7, **rec, **base):
        code, out, err = call_main(["--since", "2026-09-01"])
        check("without --comment nothing is written", code == 0 and writes == [] and "# Loop outcomes for o/r" in out)
        check("the quality table follows the outcome tables in the same report",
              out.index("## Quality") > out.index("An attempt that leaves no tracker trace is not seen."))
        check("its W37 row holds the one landed PR with no line and the one flaky attempt",
              "| 2026-W37 | 1 | 0 | 0 | 1 | 0 |" in out and "| 1 | 1 | 0 | 100% (1/1) |" in out)
    with patched(binding=fake_binding(), resolve_issue=lambda t: 7, **rec, **base):
        code, out, err = call_main(["--comment"])
        check("an undeclared loop_runs key prints, says nothing was posted, makes no write and exits 0",
              code == 0 and writes == [] and "nothing posted" in out)
    with patched(binding=fake_binding({"rolling_issues.loop_runs": "Loop runs"}), resolve_issue=lambda t: 7, **rec, **base):
        code, out, err = call_main(["--comment"])
        report = out.split("\nappended to #")[0]
        check("an existing issue gets exactly one comment, the printed report, and no create",
              code == 0 and len(writes) == 1 and writes[0][0] == "post" and writes[0][2] == 7 and writes[0][3] == report
              and out.rstrip().endswith("appended to #7"))
    writes.clear()
    with patched(binding=fake_binding({"rolling_issues.loop_runs": "Loop runs"}), resolve_issue=lambda t: 0, **rec, **base):
        code, out, err = call_main(["--comment"])
        check("a missing issue is created, then gets one comment",
              code == 0 and [w[0] for w in writes] == ["create", "post"] and writes[0][2] == "Loop runs" and writes[1][2] == 99)
    with tempfile.TemporaryDirectory(dir=TMP) as cache:
        pathlib.Path(cache, "issues.json").write_text(json.dumps(iss), encoding="utf-8")
        pathlib.Path(cache, "prs.json").write_text(json.dumps(prs), encoding="utf-8")
        pathlib.Path(cache, "runs.json").write_text(json.dumps(runs), encoding="utf-8")

        def no_fetch(slug, *rest):
            raise AssertionError("fetch called")
        with patched(binding=fake_binding(), fetch=no_fetch, fetch_runs=no_fetch, first_parent=lambda ref: BASE_COMMITS):
            code, out, err = call_main(["--from-cache", cache, "--since", "2026-09-01"])
            check("--from-cache reads the recorded nodes and never calls GitHub", code == 0 and "| 2026-W37 S | 1 | 1 |" in out)
            check("--from-cache reads the recorded run list: the flaky attempt is in the report", "| 1 | 1 | 0 | 100% (1/1) |" in out)
        os.remove(os.path.join(cache, "prs.json"))
        with patched(binding=fake_binding(), fetch=no_fetch, fetch_runs=no_fetch, first_parent=lambda ref: BASE_COMMITS):
            code, out, err = call_main(["--from-cache", cache])
            check("a cache missing a file is a failed read", code == 1 and "prs.json" in err and out == "")
        pathlib.Path(cache, "prs.json").write_text(json.dumps(prs), encoding="utf-8")
        os.remove(os.path.join(cache, "runs.json"))
        with patched(binding=fake_binding(), fetch=no_fetch, fetch_runs=no_fetch, first_parent=lambda ref: BASE_COMMITS):
            code, out, err = call_main(["--from-cache", cache])
            check("a cache missing runs.json is a failed read naming it", code == 1 and "runs.json" in err and out == "")
    writes.clear()
    nopwsh = pathlib.Path(tempfile.mkdtemp(prefix="nopwsh-", dir=TMP))
    saved_path = os.environ["PATH"]
    os.environ["PATH"] = str(nopwsh)
    try:
        with patched(binding=fake_binding({"rolling_issues.loop_runs": "Loop runs"}), **rec, **base):
            try:
                code, out, err = call_main(["--comment"])
            except Exception as e:
                code, err = f"raised {type(e).__name__}", ""
    finally:
        os.environ["PATH"] = saved_path
    check("--comment with no pwsh on PATH is a failed read: exit 1, pwsh named on stderr, nothing posted",
          code == 1 and err.startswith("loop-outcomes.py: pwsh") and writes == [])
    with patched(binding=fake_binding({"owner.ruling_approvers": "[]"}), **base):
        code, out, err = call_main([])
        check("an empty ruling_approvers list is a failed read: exit 1, the key named on stderr, no report",
              code == 1 and "owner.ruling_approvers is empty" in err and out == "")


STUB = r'''
import json, os, re, sys
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read().decode("utf-8") if argv[:2] == ["issue", "comment"] else ""
if os.environ.get("STUB_LOG"):
    with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")
if os.environ.get("STUB_FAIL"):
    print("boom: the stand-in refused")
    sys.stderr.write("denied\n")
    sys.exit(1)
def conn(nodes, nxt=False):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": nxt, "endCursor": "z" if nxt else None}}
def variables():
    return {a2.split("=", 1)[0]: a2.split("=", 1)[1] for a, a2 in zip(argv, argv[1:]) if a in ("-f", "-F") and not a2.startswith("query=")}
def issue_node(n, nxt):
    return {"number": n, "title": f"stub title {n}", "state": "OPEN", "createdAt": "2026-09-01T00:00:00Z", "body": "b",
            "userContentEdits": conn([{"editedAt": "2026-09-02T00:00:00Z", "diff": "d1"}], nxt),
            "comments": conn([{"createdAt": "2026-09-02T00:00:00Z", "author": {"login": "o"}, "body": "c1"}], nxt),
            "timelineItems": conn([{"__typename": "LabeledEvent", "createdAt": "2026-09-01T00:00:00Z", "actor": {"login": "o"}, "label": {"name": "bug"}}], nxt)}
EXTRA = {"userContentEdits": {"editedAt": "2026-09-03T00:00:00Z", "diff": "d2"},
         "comments": {"createdAt": "2026-09-03T00:00:00Z", "author": {"login": "o"}, "body": "c2"},
         "timelineItems": {"__typename": "UnlabeledEvent", "createdAt": "2026-09-03T00:00:00Z", "actor": {"login": "o"}, "label": {"name": "bug"}}}
if argv[:2] == ["issue", "list"]:
    print(os.environ.get("STUB_ISSUES", "[]"))
elif argv[:2] == ["issue", "create"]:
    print("https://github.com/o/r/issues/99")
elif argv[:2] == ["issue", "comment"]:
    pass
elif argv[:2] == ["api", "graphql"]:
    q = next(a[len("query="):] for a in argv if a.startswith("query="))
    v = variables()
    if q == "stub-selftest":
        data = {"selftest": "stub-answer"}
    elif q == "stub-errors":
        print(json.dumps({"errors": [{"message": "stub-rejected"}]})); sys.exit(0)
    elif "issue(number: $n)" in q or "pullRequest(number: $n)" in q:
        field = re.search(r"(timelineItems|comments|userContentEdits)\(first: 100, after: \$c", q).group(1)
        one = "issue" if "issue(number" in q else "pullRequest"
        data = {"repository": {one: {field: conn([EXTRA[field]])}}}
    elif "issues(first: 25" in q:
        if "number title state" not in q:
            print("stub: an issues query that does not select title"); sys.exit(3)
        data = {"repository": {"issues": conn([issue_node(1, True)], True) if "cursor" not in v else conn([issue_node(2, False)])}}
        if "cursor" not in v:
            data["repository"]["issues"]["pageInfo"]["endCursor"] = "p1"
    elif "pullRequests(first: 25" in q:
        data = {"repository": {"pullRequests": conn([{"number": 5, "state": "MERGED", "mergedAt": "2026-09-11T00:00:00Z", "body": "Fixes #1",
                "mergeCommit": {"oid": "a" * 40}, "comments": conn([{"createdAt": "2026-09-02T00:00:00Z", "author": {"login": "o"}, "body": "c1"}], True)}])}}
    else:
        print("stub: unanswered query " + q); sys.exit(3)
    print(json.dumps({"data": data}))
elif argv[:1] == ["api"] and re.fullmatch(r"repos/o/r/actions/runs/[0-9]+/attempts/[0-9]+", argv[1]):
    rid, n = re.findall(r"[0-9]+", argv[1][len("repos/o/r/actions/runs/"):])
    print(json.dumps({"id": int(rid), "workflow_id": 10, "head_sha": "a" * 40, "run_attempt": int(n), "conclusion": "failure",
                      "run_started_at": "2026-09-03T00:00:00Z", "created_at": "2026-09-03T00:00:00Z", "stub_marker": f"stub-attempt-{rid}-{n}"}))
elif argv[:1] == ["api"] and argv[1].startswith("repos/o/r/actions/runs?") and "--paginate" in argv and "--jq" in argv:
    lo_, hi_ = re.search(r"created=([^&]+)\.\.([^&]+)", argv[1]).groups()
    RUNS = [{"id": 1, "workflow_id": 10, "head_sha": "a" * 40, "run_attempt": 2, "conclusion": "success", "created_at": "2026-09-03T00:00:00Z", "run_started_at": "2026-09-04T00:00:00Z"},
            {"id": 2, "workflow_id": 10, "head_sha": "b" * 40, "run_attempt": 1, "conclusion": "failure", "created_at": "2026-09-10T00:00:00Z", "run_started_at": "2026-09-10T00:00:00Z"},
            {"id": 3, "workflow_id": 10, "head_sha": "b" * 40, "run_attempt": 1, "conclusion": "success", "created_at": "2026-09-17T00:00:00Z", "run_started_at": "2026-09-17T00:00:00Z"}]
    inside = [r for r in RUNS if lo_ <= r["created_at"] <= hi_]
    total = int(os.environ.get("STUB_RUNS_TOTAL", len(inside)))
    if os.environ.get("STUB_CAP_WIDE") and lo_[:10] != hi_[:10]:
        total = 1000
    for page in ([[r] for r in inside] or [[]]):
        print(json.dumps({"total": total, "runs": page}))
else:
    print("stub: unanswered call " + " ".join(argv)); sys.exit(3)
'''


def stub_rows():
    stub = TMP / "gh-stub.py"
    stub.write_text(STUB, encoding="utf-8")
    log = TMP / "stub.log"
    os.environ["STUB_LOG"] = str(log)
    calls = lambda: [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    with patched(GH=[sys.executable, str(stub)]):
        answer = lo.gql("stub-selftest")
        check("the gh stand-in proves itself: its own answer comes back", answer == {"selftest": "stub-answer"})
        try:
            lo.gql("stub-errors")
            check("a GraphQL errors payload raises", False)
        except lo.ReadError as e:
            check("a GraphQL errors payload raises, naming the error", "stub-rejected" in str(e))
        log.unlink()
        issues, prs = lo.fetch("o/r")
        check("fetch pages the issues to the end", [i["number"] for i in issues] == [1, 2])
        check("fetch reads each issue's title (the stand-in refuses a query without it)", issues[0]["title"] == "stub title 1")
        i1 = issues[0]
        check("fetch drains a truncated timeline, comment list and edit history",
              len(i1["timelineItems"]["nodes"]) == 2 and len(i1["comments"]["nodes"]) == 2 and len(i1["userContentEdits"]["nodes"]) == 2)
        check("fetch drains a pull request's comments", len(prs[0]["comments"]["nodes"]) == 2)
        made = calls()
        check("fetch made 7 calls: 2 issue pages, 3 follow-ups, a PR page and its follow-up", len(made) == 7)
        check("every call names the repository", all('owner: "o"' in c["argv"][-1] or 'owner: "o"' in " ".join(c["argv"]) for c in made)
              and all('name: "r"' in " ".join(c["argv"]) for c in made))
        log.unlink()
        check("create posts through gh with the repository, the title and the umbrella label, and reads the number",
              lo.create_rolling_issue("o/r", "Loop runs") == 99)
        argv = calls()[0]["argv"]
        check("its argv names --repo o/r, --title and --label umbrella",
              argv[argv.index("--repo") + 1] == "o/r" and argv[argv.index("--title") + 1] == "Loop runs" and argv[argv.index("--label") + 1] == "umbrella")
        log.unlink()
        lo.post_comment("o/r", 7, "the report\nline two")
        c = calls()[0]
        check("the comment goes to the issue and the repository, the body through stdin",
              c["argv"][:3] == ["issue", "comment", "7"] and "--repo" in c["argv"] and c["argv"][c["argv"].index("--body-file") + 1] == "-"
              and c["stdin"] == "the report\nline two")
        os.environ["STUB_FAIL"] = "1"
        try:
            with patched(binding=fake_binding(), first_parent=lambda ref: BASE_COMMITS):
                code, out, err = call_main(["--since", "2026-09-01"])
            check("a failed read exits 1, names the command's output on stderr and prints nothing", code == 1 and "boom: the stand-in refused" in err and out == "")
        finally:
            del os.environ["STUB_FAIL"]
    with patched(binding=fake_binding({"repo.default_branch": "no-such-branch"}), fetch=lambda slug: ([], [])):
        code, out, err = call_main([])
        check("a failed git read exits 1 with git's message and prints nothing", code == 1 and "fatal" in err and out == "")


def runs_fetch_rows():
    stub = TMP / "gh-stub.py"
    stub.write_text(STUB, encoding="utf-8")
    log = TMP / "runs-stub.log"
    saved = os.environ.get("STUB_LOG")
    os.environ["STUB_LOG"] = str(log)
    calls = lambda: [json.loads(x)["argv"] for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    try:
        with patched(GH=[sys.executable, str(stub)]):
            runs = lo.fetch_runs("o/r", at("2026-09-01"), at("2026-09-20"))
            check("the stand-in proves itself: the attempt it answers carries its own marker",
                  runs[0]["attempts"][0].get("stub_marker") == "stub-attempt-1-1")
            check("fetch_runs returns the 3 runs of the three weekly chunks, each once", [r["id"] for r in runs] == [1, 2, 3])
            check("a run re-run once carries its 1 earlier attempt; the others carry none",
                  [len(r["attempts"]) for r in runs] == [1, 0, 0])
            made = calls()
            lists = [c for c in made if c[1].startswith("repos/o/r/actions/runs?")]
            check("the run list is asked in three chunks, contiguous from --since to now",
                  [re.search(r"created=([^&]+)", c[1]).group(1) for c in lists] ==
                  ["2026-09-01T00:00:00Z..2026-09-07T23:59:59Z", "2026-09-08T00:00:00Z..2026-09-14T23:59:59Z",
                   "2026-09-15T00:00:00Z..2026-09-20T00:00:00Z"])
            check("each list call pages and projects the total beside the runs",
                  all("--paginate" in c and c[c.index("--jq") + 1] == lo.RUNS_JQ for c in lists))
            check("the attempts endpoint is read once, for the run above attempt 1 and its earlier attempt",
                  [c[1] for c in made if "/attempts/" in c[1]] == ["repos/o/r/actions/runs/1/attempts/1"])
            os.environ["STUB_CAP_WIDE"] = "1"
            log.unlink()
            capped = lo.fetch_runs("o/r", at("2026-09-01"), at("2026-09-20"))
            ranges = [re.search(r"created=([^&]+)", c[1]).group(1) for c in calls() if c[1].startswith("repos/o/r/actions/runs?")]
            check("a week at the cap is read a day at a time and the runs come back whole, each once",
                  [r["id"] for r in capped] == [1, 2, 3])
            check("the day queries tile the capped week: 7 days in the first, the last day ending at now",
                  "2026-09-03T00:00:00Z..2026-09-03T23:59:59Z" in ranges and "2026-09-20T00:00:00Z..2026-09-20T00:00:00Z" in ranges
                  and len([x for x in ranges if x.split("..")[0][:10] == x.split("..")[1][:10]]) == 20)
            del os.environ["STUB_CAP_WIDE"]
            os.environ["STUB_RUNS_TOTAL"] = "1000"
            try:
                lo.fetch_runs("o/r", at("2026-09-01"), at("2026-09-07"))
                check("a chunk at the API's 1000-result cap raises", False)
            except lo.ReadError as e:
                check("a day still at the cap raises, naming the cap and the day",
                      "1000-result cap" in str(e) and "2026-09-01T00:00:00Z..2026-09-01T23:59:59Z" in str(e))
            os.environ["STUB_RUNS_TOTAL"] = "5"
            try:
                lo.fetch_runs("o/r", at("2026-09-01"), at("2026-09-07"))
                check("a chunk that returns fewer runs than its total raises", False)
            except lo.ReadError as e:
                check("a chunk that returns fewer runs than its total raises, naming both counts", "1 of 5" in str(e))
            del os.environ["STUB_RUNS_TOTAL"]
            os.environ["STUB_FAIL"] = "1"
            try:
                lo.fetch_runs("o/r", at("2026-09-01"), at("2026-09-07"))
                check("a failing gh makes fetch_runs raise", False)
            except lo.ReadError as e:
                check("a failing gh makes fetch_runs raise, with its output", "boom" in str(e))
            finally:
                del os.environ["STUB_FAIL"]
    finally:
        if saved is None:
            os.environ.pop("STUB_LOG", None)
        else:
            os.environ["STUB_LOG"] = saved


def resolver_rows():
    if not shutil.which("pwsh"):
        check("pwsh is on PATH (the resolver row needs it)", False)
        return
    repo = pathlib.Path(tempfile.mkdtemp(prefix="resolver-", dir=TMP))
    git_in(repo, "init", "-q", "-b", "main")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "ouro.toml").write_text('[repo]\nslug = "o/r"\ndefault_branch = "main"\n', encoding="utf-8")
    stubbin = TMP / "stubbin"
    stubbin.mkdir(exist_ok=True)
    stub = TMP / "gh-stub.py"
    (stubbin / "gh.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{stub}" %*\r\n', encoding="utf-8")
    if os.name != "nt":
        sh = stubbin / "gh"
        sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8", newline="\n")
        sh.chmod(0o755)
    saved = {k: os.environ.get(k) for k in ("PATH", "STUB_ISSUES", "STUB_FAIL")}
    os.environ["PATH"] = str(stubbin) + os.pathsep + os.environ["PATH"]
    try:
        with patched(GIT_CWD=str(repo)):
            os.environ["STUB_ISSUES"] = '[{"number":7,"title":"Loop runs","state":"OPEN"},{"number":8,"title":"Loop runs 2","state":"OPEN"}]'
            check("the shared resolver, in a pwsh child over the gh stand-in, returns the exact-title issue's number (7, the stand-in's answer)",
                  lo.resolve_issue("Loop runs") == 7)
            os.environ["STUB_ISSUES"] = "[]"
            check("with no such issue it returns 0", lo.resolve_issue("Loop runs") == 0)
            os.environ["STUB_FAIL"] = "1"
            try:
                lo.resolve_issue("Loop runs")
                check("a failing gh makes the resolver raise", False)
            except lo.ReadError as e:
                check("a failing gh makes the resolver raise, with its output", "boom" in str(e))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def binding_rows():
    repo = pathlib.Path(tempfile.mkdtemp(prefix="binding-", dir=TMP))
    git_in(repo, "init", "-q", "-b", "main")
    (repo / ".claude").mkdir()
    toml = repo / ".claude" / "ouro.toml"
    toml.write_text('[repo]\nslug = "o/r"\ndefault_branch = ""\n[owner]\nruling_approvers = ["a", "b"]\n', encoding="utf-8")
    with patched(GIT_CWD=str(repo)):
        check("a declared key is read", lo.binding("repo.slug") == "o/r")
        check("a list key reads as JSON", json.loads(lo.binding("owner.ruling_approvers")) == ["a", "b"])
        check("an undeclared optional key is None", lo.binding("rolling_issues.loop_runs", optional=True) is None)
        try:
            lo.binding("rolling_issues.loop_runs")
            check("an undeclared required key raises", False)
        except lo.ReadError as e:
            check("an undeclared required key raises, naming it", "no such key" in str(e))
        try:
            lo.binding("repo.default_branch")
            check("an empty value raises", False)
        except lo.ReadError as e:
            check("an empty value raises", "empty" in str(e))
        toml.write_text("[repo\n", encoding="utf-8")
        try:
            lo.binding("rolling_issues.loop_runs", optional=True)
            check("a broken binding raises even for an optional key", False)
        except lo.ReadError as e:
            check("a broken binding raises even for an optional key", "no such key" not in str(e))


def usage_rows():
    p = subprocess.run([sys.executable, str(SCRIPT), "--since", "2026-13-45"], capture_output=True, env=ENV, cwd=TMP)
    check("a bad --since is a usage error: exit 2, nothing on stdout", p.returncode == 2 and p.stdout == b"" and b"YYYY-MM-DD" in p.stderr)
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, env=ENV, cwd=TMP)
    check("--help exits 0 and lists the options", p.returncode == 0 and b"--comment" in p.stdout and b"--from-cache" in p.stdout and b"runs.json" in p.stdout)


def vendored_rows():
    flat = TMP / "vendored" / "scripts"
    suite_dir = flat / "tests" / "loop-outcomes"
    suite_dir.mkdir(parents=True)
    shutil.copy(__file__, suite_dir / "loop-outcomes.Tests.py")
    run = lambda: subprocess.run([sys.executable, str(suite_dir / "loop-outcomes.Tests.py")], capture_output=True, env=ENV)
    (flat / "Test-DocsFreshness.ps1").write_text("", encoding="utf-8")
    p = run()
    check("in the vendored layout the suite prints one skip: line and exits 0",
          p.returncode == 0 and p.stdout.decode().count("skip:") == 1 and len(p.stdout.decode().strip().splitlines()) == 1)
    (flat / "Test-DocsFreshness.ps1").unlink()
    p = run()
    check("control: the same tree without the flat gate scripts is red", p.returncode != 0)


for name, fn in (("attempts", attempts_rows), ("size at promotion", size_rows), ("cohorts", cohort_rows), ("landings", landing_rows),
                 ("reverts", revert_rows), ("anchor-attributed bugs", anchor_rows), ("assisted", assisted_rows),
                 ("refusals and parking", refusal_rows), ("precision", precision_rows), ("checkpoints", checkpoint_rows),
                 ("open, abandoned, provisional", open_rows), ("report", report_rows), ("rates", rate_rows), ("review line", review_rows), ("quality PR rows", quality_pr_rows),
                 ("flake rate", flake_rows), ("comment flag and cache", flag_rows),
                 ("gh stand-in", stub_rows), ("runs fetch", runs_fetch_rows), ("resolver", resolver_rows), ("binding", binding_rows), ("usage", usage_rows),
                 ("vendored layout", vendored_rows)):
    section(name, fn)

print(f"{failures} failure(s)")
sys.exit(1 if failures else 0)
