"""Fixture suite for bin/record-counts.py: which counts a PR record holds, and which of them trace.

Every row drives the script as a subprocess over record and report files written under a temp
directory, from that directory, so the paths it prints are the relative names the row passed. The
fixture text is generic, shaped like the counts a record carries: a suite's row tally, a mutant
count, fix rounds, a gate run's failed count.

A row that asserts an absence (an excluded shape, a window that holds no listed noun) runs a
control beside it: the same text with the one character that excludes the number taken out, which
must be listed. Without it, a row would pass on a script that lists nothing.

stdlib only, like bin/*.py; it needs python3 3.11 or later and nothing else, so it runs on the
Windows leg, whose account has no bash on its PATH, as on the Linux leg. Its [[gate]] in
.claude/ouro.toml: run = "python3 tests/record-counts/record-counts.Tests.py"
"""
import atexit, os, pathlib, shutil, subprocess, sys, tempfile

# Two levels up is the plugin root (the script under bin/) or, once vendored, the scripts dir
# itself, where the vendor copies it flat. With neither, every row runs against the missing path
# and goes red, rather than the suite stopping before its first row.
BASE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = next((p for p in (BASE / "record-counts.py", BASE / "bin" / "record-counts.py") if p.is_file()),
              BASE / "bin" / "record-counts.py")

TMP = pathlib.Path(tempfile.mkdtemp(prefix="record-counts-test-run-"))
def _remove_tmp():
    try:
        shutil.rmtree(TMP)
    except OSError as e:
        print(f"WARN: could not remove {TMP}: {e}", file=sys.stderr)


atexit.register(_remove_tmp)
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

failures = 0
# The row names quote non-ASCII fixture text; a console in a legacy code page would not encode it.
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


check(f"the script is there: {SCRIPT}", SCRIPT.is_file())

_rows = 0


def write(name, content, d=None):
    """Write content (str as UTF-8, or bytes as given) to d/name and return its name."""
    p = (d or TMP) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return name


def run(args, cwd=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, env=ENV, cwd=cwd or TMP)
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def counts(record, reports=(), d=None):
    """Run the script over one record text and report texts; return (exit, stdout as text)."""
    global _rows
    _rows += 1
    rec = write(f"rec{_rows}.md", record, d)
    reps = [write(f"rep{_rows}-{i}.txt", r, d) for i, r in enumerate(reports)] or [write(f"rep{_rows}-empty.txt", "", d)]
    code, out, _ = run(["--record", rec, "--report", *reps], cwd=d)
    return code, out.decode("utf-8", "replace"), rec


def listed(what, record, reports, expected):
    """A row whose record must list exactly the `expected` lines (each `<line>: <text>`), at exit 1."""
    code, out, rec = counts(record, reports)
    want = "".join(f"{rec}:{e}\n" for e in expected)
    check(f"{what}: exit 1, listed {expected}" + ("" if (code, out) == (1, want) else f" (got exit {code}, {out!r})"),
          code == 1 and out == want)


def traced(what, record, reports):
    """A row whose record must list nothing, at exit 0."""
    code, out, _ = counts(record, reports)
    check(f"{what}: exit 0, nothing printed" + ("" if (code, out) == (0, "") else f" (got exit {code}, {out!r})"),
          code == 0 and out == "")


print("-- false counts are listed")
listed("a record's `all 412 suite rows pass` against a report's `409 passed, 3 failed`",
       "We ran all 412 suite rows pass on the head.\n", ["409 passed, 3 failed\n"], ["1: 412 suite rows"])
listed("a record's `21 passed` against a report's `20 passed, 1 failed`",
       "Gate run: 21 passed.\n", ["20 passed, 1 failed\n"], ["1: 21 passed"])
listed("a record's `closes 16 gaps` against a report's `closes 6 gaps`",
       "This change closes 16 gaps.\n", ["It closes 6 gaps.\n"], ["1: 16 gaps"])
listed("a record's `409 of the 412 rows pass` against a report's `412 rows`: only 409 is listed",
       "Here 409 of the 412 rows pass.\n", ["The suite holds 412 rows.\n"], ["1: 409 of the 412 rows pass"])

print("-- counts that trace print nothing")
traced("the same pair in the report", "Sixteen mutants went red.\n", ["sixteen mutants went red\n"])
traced("`1 files` against `1 file`", "It changed 1 files.\n", ["1 file changed\n"])
traced("`three fix rounds` against `3 rounds`", "It took three fix rounds.\n", ["after 3 rounds\n"])
traced("`0 FAIL:` against `0 failed`", "The suite printed 0 FAIL: lines.\n", ["17 passed, 0 failed\n"])
traced("a pair that is only in the second of two report files", "We confirmed 8 defects.\n",
       ["nothing here\n", "8 defects confirmed\n"])
traced("`14 scenarios pass` against `14 passed`", "All 14 scenarios pass.\n", ["14 passed\n"])
traced("a record that holds no count at all", "No numbers here, and 5 widgets.\n", [])

print("-- the window")
listed("`4 false, 0 unverifiable` gives two counts", "Claims: 4 false, 0 unverifiable.\n", [],
       ["1: 4 false", "1: 0 unverifiable"])
listed("`3 false or misleading claims` pairs only with false: a report's `3 claims` does not trace it",
       "We found 3 false or misleading claims.\n", ["3 claims\n"], ["1: 3 false or"])
traced("and a report's `3 false` does", "We found 3 false or misleading claims.\n", ["3 false\n"])
traced("`5 widgets` is not a count", "It built 5 widgets.\n", [])
listed("and its control: `5 rows` is", "It built 5 rows.\n", [], ["1: 5 rows"])
traced("a listed noun three words after its number is not a count", "It took 6 more fix rounds.\n", [])
listed("and its control: two words after is", "It took 6 fix rounds.\n", [], ["1: 6 fix rounds"])
traced("punctuation ends the window", "It built 5 widgets, rows and all.\n", [])
listed("and its control: whitespace does not", "It built 5 widgets rows and all.\n", [], ["1: 5 widgets rows"])
listed("a count wrapped across a line break is found, at its number's line",
       "first line\nit took 7 fix\nrounds to settle\n", [], ["2: 7 fix rounds"])
listed("`one finding` with an empty report is listed: one is a number", "It left one finding.\n", [],
       ["1: one finding"])
listed("spelled numbers run to twenty, in any case", "TWENTY rows, Twelve runs\n", [],
       ["1: TWENTY rows", "1: Twelve runs"])
traced("and stop there: twenty-one is no number, nor is someone", "twenty-one rows, someone rows\n", [])
traced("the case is ASCII: `ſix` (U+017F) is no number", "ſix rows\n", [])
listed("and its control: `six` is", "six rows\n", [], ["1: six rows"])
listed("`zero findings` is a count: zero is a number", "It left zero findings.\n", [], ["1: zero findings"])
listed("a run of six digits is a number", "It read 123456 lines.\n", [], ["1: 123456 lines"])
traced("a word ending in a colon is a word, so the window runs past it", "2 FAIL: lines\n", ["2 lines\n"])
traced("`150 ok:` against `150 ok`: the colon is dropped before the noun is read", "All 150 ok:\n", ["150 ok\n"])
listed("and its control: `150 ok:` is a count", "All 150 ok:\n", [], ["1: 150 ok:"])
listed("a word holds hyphens: `one pre-existing finding`", "It left one pre-existing finding.\n", [],
       ["1: one pre-existing finding"])
traced("a word touches no `_`: `3 file_x` is not a count", "It changed 3 file_x.\n", [])
listed("and its control: `3 file x` is", "It changed 3 file x.\n", [], ["1: 3 file x"])
traced("`2 entries` against `2 entry`", "It adds 2 entries.\n", ["2 entry\n"])
listed("and its control: `2 entries` is a count", "It adds 2 entries.\n", [], ["1: 2 entries"])
listed("`of` in any case: `3 Of 4 rows` gives 3 the window of 4", "Read 3 Of 4 rows.\n", [],
       ["1: 3 Of 4 rows", "1: 4 rows"])

print("-- the exclusions: each shape with a listed noun after it and an empty report gives exit 0,")
print("   and its control, the same text with the touching character taken out, is listed")
EXCLUSIONS = [
    ("a version, `v0.1.166`", "Shipped in v0.1.166 rows.\n", "Shipped in v0.1 166 rows.\n", "1: 166 rows"),
    ("a dotted version, `3.12.3`", "Built on 3.12.3 runs.\n", "Built on 3.12 3 runs.\n", "1: 3 runs"),
    ("a decimal", "It took 1.5 runs.\n", "It took 1 5 runs.\n", "1: 5 runs"),
    ("a duration", "It took 1:30 runs.\n", "It took 1 30 runs.\n", "1: 30 runs"),
    ("a dash date", "On 2026-10-05 runs.\n", "On 2026-10 05 runs.\n", "1: 05 runs"),
    ("a slash date", "On 10/05 runs.\n", "On 10 05 runs.\n", "1: 05 runs"),
    ("a SHA", "At 3f2a1b9 commits.\n", "At 3f2a1b 9 commits.\n", "1: 9 commits"),
    ("a `#` reference", "See #123 rows.\n", "See # 123 rows.\n", "1: 123 rows"),
    ("a `§` reference", "See §3 rows.\n", "See § 3 rows.\n", "1: 3 rows"),
    ("a `:123` line reference", "See :123 lines.\n", "See : 123 lines.\n", "1: 123 lines"),
    ("a `file.py:123` line reference", "See file.py:123 lines.\n", "See file.py 123 lines.\n", "1: 123 lines"),
    ("a percentage", "About 70% rows.\n", "About 70 rows.\n", "1: 70 rows"),
    ("a number after `%`, as in an escape", "See a%20 rows.\n", "See a% 20 rows.\n", "1: 20 rows"),
    ("a label, `F1`", "Fixed F1 findings.\n", "Fixed F 1 findings.\n", "1: 1 findings"),
    ("a label, `R1-1`", "Fixed R1-1 findings.\n", "Fixed R1- 1 findings.\n", "1: 1 findings"),
    ("a possessive, `step 9's`", "Read step 9's findings.\n", "Read step 9 findings.\n", "1: 9 findings"),
    ("a number after `'`, as in a short year", "Since '26 runs.\n", "Since ' 26 runs.\n", "1: 26 runs"),
    ("an id of seven digits", "Job 1234567 runs.\n", "Job 1234 567 runs.\n", "1: 567 runs"),
    ("a number after `$`", "Cost $5 runs.\n", "Cost $ 5 runs.\n", "1: 5 runs"),
    ("a number after `@`", "Pinned @3 gates.\n", "Pinned @ 3 gates.\n", "1: 3 gates"),
    ("a number after `_`", "Named x_3 gates.\n", "Named x_ 3 gates.\n", "1: 3 gates"),
    ("a number after a comma followed by a digit", "About 1,234 rows.\n", "About 1, 234 rows.\n", "1: 234 rows"),
    ("a number after a non-ASCII letter, read as UTF-8", "Named é3 rows.\n", "Named é 3 rows.\n", "1: 3 rows"),
]
for what, excluded, control, line in EXCLUSIONS:
    traced(f"{what} is not a count", excluded, [])
    listed(f"{what}: its control is", control, [], [line])
listed("a spelled number after a comma that a letter follows is a number", "rows,three rows\n", [], ["1: three rows"])
listed("backticks and ** are removed first", "It made **3** `fix` rounds.\n", [], ["1: 3 fix rounds"])

print("-- the contract")
d = TMP / "order"
write("b.md", "x\nIt left 2 gaps.\n", d)
write("a.md", "It left 4 gaps.\nIt took 5 rows.\n", d)
write("r.txt", "nothing\n", d)
code, out, _ = run(["--record", "b.md", "a.md", "--report", "r.txt"], cwd=d)
want = b"b.md:2: 2 gaps\na.md:1: 4 gaps\na.md:2: 5 rows\n"
check(f"two record files: lines in record-argument order, then line order (got {out!r})", code == 1 and out == want)
code2, out2, _ = run(["--record", "b.md", "a.md", "--report", "r.txt"], cwd=d)
check("and a second run prints the same bytes", (code2, out2) == (code, out) and out != b"")
code, out, _ = run(["--record", "a.md", "--record", "b.md", "--report", "r.txt"], cwd=d)
check(f"a repeated --record adds its files in order (got {out!r})",
      code == 1 and out == b"a.md:1: 4 gaps\na.md:2: 5 rows\nb.md:2: 2 gaps\n")

PLAIN_REC = "Named é3 rows.\nIt took 7 fix\nrounds, and 2 runs passed.\nAll 4 suites ok\n"
PLAIN_REP = "4 suites\n"
for kind, enc in (("plain", lambda s: s.encode("utf-8")),
                  ("BOM", lambda s: b"\xef\xbb\xbf" + s.encode("utf-8")),
                  ("CRLF", lambda s: s.replace("\n", "\r\n").encode("utf-8")),
                  ("BOM and CRLF", lambda s: b"\xef\xbb\xbf" + s.replace("\n", "\r\n").encode("utf-8"))):
    d = TMP / f"enc-{kind.replace(' ', '-')}"
    write("rec.md", enc(PLAIN_REC), d)
    write("rep.txt", enc(PLAIN_REP), d)
    code, out, _ = run(["--record", "rec.md", "--report", "rep.txt"], cwd=d)
    check(f"{kind} input gives the plain results (got exit {code}, {out!r})",
          code == 1 and out == b"rec.md:2: 7 fix rounds\nrec.md:3: 2 runs passed\n")

d = TMP / "usage"
write("rec.md", "It left 2 gaps.\n", d)
write("rep.txt", "2 gaps\n", d)
code, out, err = run(["--record", "rec.md", "--report", "rep.txt"], cwd=d)
check("the control for the usage rows: the full invocation exits 0", (code, out) == (0, b""))
code, out, err = run(["--record", "rec.md"], cwd=d)
check(f"no --report: exit 2, stdout empty, stderr names --report (got {code}, {out!r})",
      code == 2 and out == b"" and "--report" in err)
code, out, err = run(["--report", "rep.txt"], cwd=d)
check(f"no --record: exit 2, stdout empty, stderr names --record (got {code}, {out!r})",
      code == 2 and out == b"" and "--record" in err)
code, out, err = run(["--record", "rec.md", "--report", "rep.txt", "no-such-report.txt"], cwd=d)
check(f"an unreadable report: exit 2, stdout empty, stderr names it (got {code}, {out!r})",
      code == 2 and out == b"" and "no-such-report.txt" in err)
write("bad.md", "It left 9 gaps.\n", d)
code, out, err = run(["--record", "bad.md", "no-such-record.md", "--report", "rep.txt"], cwd=d)
check(f"an unreadable record after a readable one with a count: exit 2, stdout empty (got {code}, {out!r})",
      code == 2 and out == b"" and "no-such-record.md" in err)
write("latin.md", b"It left 9 gaps \xe9.\n", d)
code, out, err = run(["--record", "latin.md", "--report", "rep.txt"], cwd=d)
check(f"a record that is not UTF-8: exit 2, stdout empty, stderr names it (got {code}, {out!r})",
      code == 2 and out == b"" and "latin.md" in err)

print()
if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("all ok")
