"""Unit test for apply-manifest.py's manifest-safety rules.

Most exist because a manifest that looks right un-promotes what it just promoted:
the shape gate fires on `labeled` and wants the **Triage** comment already there, and
an intake automation may stamp a state label on `opened` that no manifest author saw --
possibly after the edit's own read, which the post-apply state check re-reads for.
The body-file guard exists because a failed transform uploads an empty file as a blank body.
gh and time.sleep are stubbed, and time.monotonic in the cases that assert the intake wait; no case
really calls GitHub or sleeps.

stdlib only, like bin/*.py. Run: python3 tests/apply-manifest/apply-manifest.Tests.py
"""
import atexit, contextlib, ctypes, errno, importlib.util, io, os, pathlib, re, shutil, stat, sys, tempfile, json, time

# Two levels up is the plugin root (the applier under bin/) or, once vendored, the scripts dir
# itself, where the vendor copies it flat -- the way the .ps1 suites resolve their own subject.
BASE = pathlib.Path(__file__).resolve().parents[2]
APPLIER = next((p for p in (BASE / "apply-manifest.py", BASE / "bin" / "apply-manifest.py") if p.is_file()), None)
if APPLIER is None:
    raise SystemExit(f"apply-manifest.py not found under {BASE}")
spec = importlib.util.spec_from_file_location("applier", APPLIER)
applier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(applier)

# One parent directory for every temp tree a row makes below (manifest_dir, the read-only probe,
# the escape rows), so the whole run leaves one object to remove instead of hundreds. A read-only
# file a row leaves behind when it fails before restoring it gets its write bit back before the
# retry; anything else surfaces. Registered once so a failing row's sys.exit(1), or an
# uncaught exception, still removes it.
PARENT_TMP = pathlib.Path(tempfile.mkdtemp(prefix="apply-manifest-test-run-"))


def _clear_readonly_and_retry(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


atexit.register(shutil.rmtree, PARENT_TMP, onerror=_clear_readonly_and_retry)

failures = 0


def check(what, cond):
    global failures
    if cond:
        print(f"  ok: {what}")
    else:
        print(f"FAIL: {what}")
        failures += 1


def manifest_dir(steps, files):
    d = pathlib.Path(tempfile.mkdtemp(prefix="apply-manifest-test-", dir=PARENT_TMP))
    (d / "manifest.json").write_text(json.dumps(steps), encoding="utf-8")
    for name, text in files.items():  # bytes: the file as the applier meets it; None: no such file
        if text is None:
            (d / name).unlink(missing_ok=True)
        elif isinstance(text, bytes):
            (d / name).write_bytes(text)
        else:
            (d / name).write_text(text, encoding="utf-8")
    return d


TRIAGE = "**Triage**\n\nPROMOTE (Size S).\n"
PLAIN = "Just a note, no provenance marker.\n"

# --- the order check ---------------------------------------------------------------
d = manifest_dir(
    [{"op": "edit", "issue": 42, "add_labels": ["agent-ready"], "remove_labels": ["needs-triage"]},
     {"op": "comment", "issue": 42, "body_file": "c.md"}],
    {"c.md": TRIAGE})
try:
    applier.check_order(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
    check("edits-first manifest is refused", False)
except SystemExit as e:
    check("edits-first manifest is refused", "issue 42" in str(e))

d = manifest_dir(
    [{"op": "comment", "issue": 42, "body_file": "c.md"},
     {"op": "edit", "issue": 42, "add_labels": ["agent-ready"], "remove_labels": ["needs-triage"]}],
    {"c.md": TRIAGE})
applier.check_order(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
check("comment-first manifest is accepted", True)

# A comment that is not the provenance marker does not satisfy the gate, so it must not
# make an edits-first manifest look safe -- and must not refuse a manifest either.
d = manifest_dir(
    [{"op": "edit", "issue": 7, "add_labels": ["blocked"]},
     {"op": "comment", "issue": 7, "body_file": "c.md"}],
    {"c.md": PLAIN})
applier.check_order(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
check("a non-**Triage** comment is not treated as provenance", True)

# An edit that changes only the body is not a promotion and is unordered.
d = manifest_dir(
    [{"op": "edit", "issue": 9, "body_file": "b.md"},
     {"op": "comment", "issue": 9, "body_file": "c.md"}],
    {"b.md": "body", "c.md": TRIAGE})
applier.check_order(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
check("a body-only edit before a comment is allowed", True)

# The verdict on the marker line is posted normalized, so it is still the provenance comment
# and the edits-first order is still wrong.
d = manifest_dir(
    [{"op": "edit", "issue": 42, "add_labels": ["agent-ready"], "remove_labels": ["needs-triage"]},
     {"op": "comment", "issue": 42, "body_file": "c.md"}],
    {"c.md": "**Triage** — PROMOTE (Size M)\n\nVerified.\n"})
try:
    applier.check_order(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
    check("edits-first manifest with the verdict on the marker line is refused", False)
except SystemExit as e:
    check("edits-first manifest with the verdict on the marker line is refused", "issue 42" in str(e))

# --- the posted **Triage** comment -------------------------------------------------
# The shape gate compares a comment's first line whole, so the applier posts the marker
# alone on line 1, line 2 blank, the verdict from line 3.
def post(text):
    """Dry-run a one-comment manifest; return the rendered file it would post and the output."""
    d = manifest_dir([{"op": "comment", "issue": 42, "body_file": "c.md"}], {"c.md": text})
    out, saved = io.StringIO(), (sys.argv, getattr(applier, "DRY", False), applier.REPO)
    applier.DRY, applier.REPO = True, "owner/name"
    sys.argv = ["apply-manifest.py", str(d), "--repo", "owner/name", "--dry-run", "--no-forbidden-check"]
    try:
        with contextlib.redirect_stdout(out):
            applier.main()
    finally:
        sys.argv, applier.DRY, applier.REPO = saved
    return (d / "rendered-c.md").read_text(encoding="utf-8"), out.getvalue()


posted, out = post("**Triage** — PROMOTE (Size M)\n\nVerified.\n")
check("the verdict on the marker line moves to line 3",
      posted.splitlines()[:4] == ["**Triage**", "", "PROMOTE (Size M)", ""])
check("and the applier says it normalized the comment", out.count("normalized") == 1)

posted, out = post("\n**Triage**\n\nPROMOTE (Size S).\n")
check("a blank line above the marker is dropped", posted == TRIAGE)

posted, out = post("**triage** — looks good\n")
check("a lowercase marker is left alone", posted == "**triage** — looks good\n" and "normalized" not in out)

posted, out = post(PLAIN)
check("a plain comment is left alone", posted == PLAIN and "normalized" not in out)

posted, out = post("**Triage**: PROMOTE (Size S)\n")
check("a colon after the marker goes too", posted.splitlines()[:3] == ["**Triage**", "", "PROMOTE (Size S)"])

posted, out = post("﻿**Triage** - PROMOTE (Size S)\n")
check("a byte-order mark and a hyphen after the marker go",
      posted.splitlines()[:3] == ["**Triage**", "", "PROMOTE (Size S)"])

# --- state-label exclusivity -------------------------------------------------------
adds, removes = applier.resolve_labels(["agent-ready"], [], ["needs-triage", "enhancement"])
check("an undeclared stamped state is removed", removes == ["needs-triage"])
check("a non-state label is left alone", "enhancement" not in removes)

adds, removes = applier.resolve_labels(["agent-ready"], ["needs-ruling"], ["needs-ruling"])
check("a declared removal is not duplicated", removes == ["needs-ruling"])

adds, removes = applier.resolve_labels(["agent-ready"], [], ["agent-ready"])
check("the state being added is never removed", removes == [])

adds, removes = applier.resolve_labels(["trivial"], [], ["agent-ready"])
check("a modifier-only edit leaves the state alone", removes == [])

adds, removes = applier.resolve_labels(["needs-ruling"], ["Agent-Ready", "blocked"], ["agent-ready", "Trivial", "bug"])
check("removals name only carried labels, matched case-insensitively, a modifier included off agent-ready",
      removes == ["agent-ready", "Trivial"])

# --- the repo.slug read ------------------------------------------------------------
# Without --repo the slug comes from ouro-binding.py, and whatever that read failed on -- an
# invalid binding, a python below the 3.11 floor, no binding at all -- is on its stderr. The
# refusal carries that line and names no single cause for all of them.
class FailedRead:
    returncode, stdout, stderr = 1, "", "ouro-binding.py needs Python 3.11+ (tomllib); this python is 3.10\n"


real_run, real_argv = applier.subprocess.run, sys.argv
applier.subprocess.run, sys.argv = (lambda *a, **k: FailedRead()), ["apply-manifest.py", "some-dir", "--dry-run"]
try:
    applier.resolve_repo()
    check("a failed repo.slug read refuses", False)
except SystemExit as e:
    check("a failed repo.slug read carries ouro-binding.py's reason", "needs Python 3.11+" in str(e))
    check("and does not call every failure a missing binding", "no binding" not in str(e))
finally:
    applier.subprocess.run, sys.argv = real_run, real_argv

# The refusal names the binding's path, which the binding tool writes as UTF-8 whatever the
# locale, so a read in the locale's encoding mangled a non-ASCII path, or, where the code page
# cannot decode it at all, left stderr None and turned the refusal into an AttributeError. Run
# for real against an invalid binding: the binding tool only reads, and --dry-run stays set.
binding_tool = APPLIER.parent / "ouro-binding.py"
if sys.version_info < (3, 11) or not binding_tool.is_file():
    print("  skip: the repo.slug read needs ouro-binding.py beside the applier and python 3.11+")
else:
    for name in (f"r{chr(0xE9)}po", f"r{chr(0x141)}po"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = pathlib.Path(tmp) / name
            try:
                (repo / ".claude").mkdir(parents=True)
            except (OSError, UnicodeError) as e:
                print(f"  skip: cannot create a directory named {name!a} ({e})")
                continue
            (repo / ".claude" / "ouro.toml").write_text("schema = 1\n[repo\n", encoding="utf-8")
            applier.subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, check=True)
            saved_cwd, real_argv = os.getcwd(), sys.argv
            os.chdir(repo)
            sys.argv = ["apply-manifest.py", "some-dir", "--dry-run"]
            try:
                applier.resolve_repo()
                refusal = "no refusal"
            except SystemExit as e:
                refusal = str(e)
            except Exception as e:
                refusal = f"raised {type(e).__name__}: {e}"
            finally:
                os.chdir(saved_cwd)
                sys.argv = real_argv
            check(f"a failed repo.slug read under {name!a} refuses naming the binding's path as written",
                  refusal.startswith("no --repo, and reading repo.slug") and name in refusal)

# The apply() harness above stubs REPO to "owner/name" itself and never calls resolve_repo() for
# real when no --repo is given, so the binding's own success path -- ouro-binding.py get's raw
# string answer, returned bare -- runs by no row. Run it for real. This is coverage of a path that
# was already right, not a regression row: it passes at every revision of the applier.
if sys.version_info < (3, 11) or not binding_tool.is_file():
    print("  skip: the repo.slug read needs ouro-binding.py beside the applier and python 3.11+")
else:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = pathlib.Path(tmp) / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "ouro.toml").write_text('schema = 1\n[repo]\nslug = "owner/name"\n', encoding="utf-8")
        applier.subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, check=True)
        saved_cwd, real_argv = os.getcwd(), sys.argv
        os.chdir(repo)
        sys.argv = ["apply-manifest.py", "some-dir", "--dry-run"]
        try:
            slug = applier.resolve_repo()
        finally:
            os.chdir(saved_cwd)
            sys.argv = real_argv
        check("a valid binding's repo.slug comes back bare, with no quotes", slug == "owner/name")

# --- the body-file guard -----------------------------------------------------------
# A transform that failed upstream leaves an empty file, and `--body-file` uploads it as a blank
# body. Before any step runs the applier refuses an empty body file and an edit that guts an
# existing issue's body, and keeps the body that edit replaces beside the manifest.
BEFORE = "A line of the issue body as it stands.\n" * 130   # about 5 KB
AFTER = "A line of the cut-down body.\n" * 35                 # about 1 KB


class Ran:
    returncode, stderr = 0, ""

    def __init__(self, stdout):
        self.stdout = stdout


class Failed:
    returncode, stdout = 1, ""

    def __init__(self, stderr):
        self.stderr = stderr


class Repo:
    """A fake GitHub that writes an edit as gh does: its additions and its removals are two halves,
    each failing whole when it names a label outside the repository's label set, and the call exits
    1 when either half failed. Label names match case-insensitively, and an added label is carried
    as the repository spells it. issues maps a number to the labels it carries. A create answers
    issue 101 carrying its --label values, a body read BEFORE, and the issue numbered stamp gets
    needs-triage right after its first edit: an intake stamp landing after the edit's read. With
    body_fails, a --body-file write fails as a third half, beside the label halves that still write."""

    def __init__(self, labels, issues, stamp=None, body_fails=False):
        self.labels, self.issues, self.stamp = {l.casefold(): l for l in labels}, issues, stamp
        self.body_fails = body_fails

    def run(self, cmd):
        a = cmd[1:-2]  # less gh and -R owner/name
        values = lambda flag: [a[i + 1] for i, x in enumerate(a) if x == flag]
        if a[:2] == ["issue", "create"]:
            self.issues[101] = values("--label")
            return Ran("https://github.com/owner/name/issues/101")
        if a[:2] == ["issue", "view"]:
            return Ran("\n".join(self.issues[int(a[2])]) if "labels" in a else BEFORE)
        if a[:2] != ["issue", "edit"]:
            return Ran("")
        n, adds, removes, errors = int(a[2]), values("--add-label"), values("--remove-label"), []
        have = self.issues[n]
        if self.body_fails and "--body-file" in a:
            errors.append("HTTP 502: the body write failed")
        for half in (adds, removes):
            missing = [l for l in half if l.casefold() not in self.labels]
            if missing:
                errors.append(f"'{missing[0]}' not found")
            elif half is adds:
                have += [self.labels[l.casefold()] for l in adds if l.casefold() not in {h.casefold() for h in have}]
            else:
                have[:] = [h for h in have if h.casefold() not in {l.casefold() for l in removes}]
        if n == self.stamp:
            self.stamp = None
            have.append("needs-triage")
        return Failed("\n".join(errors)) if errors else Ran("")


slept = []  # the seconds each stubbed time.sleep was asked for in the last apply()


def apply(steps, files, dry=False, labels=(), repo=None, unattended=False, named_repo="owner/name", forbidden=False,
          binding=None, overlay=None):
    """Run main() over a manifest with gh and time.sleep stubbed; return the dir, the gh commands,
    the output and the refusal (None when it ran through; any other exception main() raises is the
    refusal too, as its type and message). A create answers issue 101, each labels read the next
    entry of labels (none left: no labels), a body read BEFORE. Given a Repo, the Repo answers every
    call instead. With unattended, main() runs as `--unattended` does. named_repo is the value the
    command line spells after --repo, and None is a command line carrying no --repo at all, REPO
    standing for what the binding answered. Every run passes --no-forbidden-check, as CI holds no
    list; with forbidden, the check runs. With forbidden or a binding, the run is from a fresh git
    repository of its own holding binding as its .claude/ouro.toml and overlay as its
    ouro.local.toml, or no binding when binding is None."""
    d, calls, out, refused, labels = manifest_dir(steps, files), [], io.StringIO(), None, list(labels)
    slept.clear()
    saved_cwd = os.getcwd()
    if forbidden or binding is not None:  # check_off reads the working directory's binding, never the suite's own
        home = pathlib.Path(tempfile.mkdtemp(prefix="binding-", dir=PARENT_TMP))
        applier.subprocess.run(["git", "init", "-q"], cwd=home, capture_output=True, check=True)
        for name, text in ((binding is not None and "ouro.toml", binding), (overlay is not None and "ouro.local.toml", overlay)):
            if name:
                (home / ".claude").mkdir(exist_ok=True)
                (home / ".claude" / name).write_text(text, encoding="utf-8")
        os.chdir(home)

    def run(cmd, **k):
        calls.append(cmd)
        if repo:
            return repo.run(cmd)
        if "create" in cmd:
            return Ran("https://github.com/owner/name/issues/101")
        if "labels" in cmd:  # None stands for a failed read
            v = labels.pop(0) if labels else ""
            return Ran(v) if v is not None else type("Failed", (), {"returncode": 1, "stdout": "", "stderr": "HTTP 502"})()
        return Ran(BEFORE if "view" in cmd else "")

    saved = (sys.argv, getattr(applier, "DRY", False), applier.REPO, applier.subprocess.run, time.sleep,
             applier.UNATTENDED)
    applier.DRY, applier.subprocess.run, time.sleep = dry, run, slept.append
    applier.UNATTENDED = unattended
    sys.argv = (["apply-manifest.py", str(d)] + ([] if named_repo is None else ["--repo", named_repo])
                + (["--dry-run"] if dry else []) + ([] if forbidden else ["--no-forbidden-check"]))
    try:
        # As the bottom of the script does, in its order: check_args before the repository is
        # resolved, then resolve_repo reading --repo's own value, "owner/name" standing in for the
        # binding's answer where the command line carries none. Inside the try, so a raise here
        # leaves the next case its own stubs.
        applier.check_args(sys.argv[1:])
        applier.REPO = "owner/name" if named_repo is None else applier.resolve_repo()
        with contextlib.redirect_stdout(out):
            applier.main()
    except SystemExit as e:
        refused = str(e)
    except Exception as e:
        refused = f"{type(e).__name__}: {e}"
    finally:
        sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, time.sleep, applier.UNATTENDED = saved
        os.chdir(saved_cwd)
    return d, calls, out.getvalue(), refused


d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}], {"b.md": " \n\n"})
check("a whitespace-only body file is refused, naming the step and the file",
      refused is not None and "step 1" in refused and "b.md" in refused)
check("and no gh call runs", calls == [])

d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": "c.md"}], {"c.md": "﻿\n"})
check("an empty comment file, byte-order mark and all, is refused too",
      refused is not None and "c.md" in refused and calls == [])

# A subissue step carries neither an issue nor a key, so the who slot is empty and the step is
# named "step 1 (subissue)".
d, calls, out, refused = apply([{"op": "subissue", "parent": 42, "child": 43, "body_file": "e.md"}],
                               {"e.md": " \n"})
check("a subissue step carrying a body_file is refused for the field, naming the step with no stray space",
      refused is not None and "step 1 (subissue): 'body_file'" in refused and calls == [])

# A body file that cannot be opened at all -- FileNotFoundError, or a UnicodeDecodeError past the
# one guard there was -- ended the run on a traceback where the first read of it sat: check_order's
# of a comment body, check_refs's of every other. Every body a step carries is opened once before
# any step runs, so each is refused by step and name instead, whichever op carries it. A traceback
# names no step, so the step number is what tells the refusal from the crash; and an OSError's own
# message carries the absolute path, so the refusal names the file by its strerror.
UTF16 = TRIAGE.encode("utf-16")  # what a Windows editor saves: 0xff at byte 0, no UTF-8 reading
for what, step in (("a create", {"op": "create", "key": "c", "title": "A child", "body_file": "x.md"}),
                   ("an edit", {"op": "edit", "issue": 42, "body_file": "x.md"}),
                   ("a comment", {"op": "comment", "issue": 42, "body_file": "x.md"}),
                   ("a close", {"op": "close", "issue": 42, "body_file": "x.md"})):
    for cause, files in (("is not there", {}), ("is not UTF-8 text", {"x.md": UTF16})):
        d, calls, out, refused = apply([step], files)
        check(f"{what} body file that {cause} is refused by step and name, not a traceback",
              refused is not None and "step 1" in refused and "x.md" in refused
              and d.name not in refused and calls == [])

d, calls, out, refused = apply([{"op": "subissue", "parent": 42, "child": 43, "body_file": "x.md"}], {})
check("and so is one whose body_file names no file, before any read",
      refused is not None and "step 1 (subissue): 'body_file'" in refused and "x.md" not in refused and calls == [])

# The step loop's own progress line takes that idiom too, and ends at the op.
d, calls, out, refused = apply([{"op": "subissue", "parent": 42, "child": 43}], {})
check("and the progress line of a step carrying neither ends at the op",
      refused is None and "[1/1] subissue\n" in out)

d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": "x.md"},
                                {"op": "close", "issue": 43, "body_file": "y.md"}], {"y.md": UTF16})
check("both causes are named in one refusal, headed 'an unreadable body file'",
      (refused or "").startswith("an unreadable body file, nothing applied:")
      and "x.md" in refused and "y.md" in refused and calls == [])
check("and the one that is not there is named by its strerror, not by the absolute path",
      "No such file or directory" in refused and "Errno" not in refused)

# A body file that lands outside the manifest directory is manifest_file's refusal, not a read
# error, so the confinement keeps its own words. Called directly, so the row states what
# check_readable does with one rather than which check happens to reach it first.
try:
    applier.check_readable(manifest_dir([], {}),
                           [{"op": "comment", "issue": 9, "body_file": "../secret.md"}])
    check("check_readable passes manifest_file's own refusal through", False)
except SystemExit as e:
    check("check_readable passes manifest_file's own refusal through",
          str(e).startswith("body file '../secret.md' resolves outside the manifest directory"))

# A falsy body_file is read by nothing: an edit needs none, and the step loop tests it the same way.
d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "", "add_labels": ["idea"]}], {})
check("an edit whose body_file is empty still applies",
      refused is None and any("--add-label" in c for c in calls))


# A step missing a field its op requires is refused by name before any step runs, as every other
# malformed manifest is -- not left to raise where the step uses the field. A crash's own message
# can carry the field name (a KeyError does), so the check also asks for the step number and for no
# exception type at the front: the harness reports a crash as "<Type>Error: ...", a refusal as its
# message alone.
def refused_by_name(refused, step, field):
    return (refused is not None and not re.match(r"\w+(Error|Exception): ", refused)
            and f"step {step}" in refused and field in refused)


for mode in (False, True):
    d, calls, out, refused = apply([{"op": "comment", "issue": 42}], {}, unattended=mode)
    check(f"a comment step with no body_file is refused by name{' (unattended)' if mode else ''}",
          refused_by_name(refused, 1, "body_file"))
    check(f"and no gh call runs{' (unattended)' if mode else ''}", calls == [])
    if mode:  # check_unattended keeps its own refusal: it runs first, before check_fields
        check("unattended, the refusal is check_unattended's own", "unattended mode refuses" in (refused or ""))

d, calls, out, refused = apply([{"op": "close", "issue": 42}], {})
check("a close step with no body_file is refused by name", refused_by_name(refused, 1, "body_file"))
check("and no gh call runs", calls == [])

d, calls, out, refused = apply([{"op": "create", "key": "a", "body_file": "a.md"}], {"a.md": "A body.\n"})
check("a create step with no title is refused by name", refused_by_name(refused, 1, "title"))
check("and no gh call runs", calls == [])

d, calls, out, refused = apply([{"op": "create", "key": "a", "title": "A"}], {})
check("a create step with no body_file is refused by name", refused_by_name(refused, 1, "body_file"))
check("and no gh call runs", calls == [])

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}, {"op": "comment", "issue": 7}],
                               {"b.md": BEFORE})
check("the refusal names the offending step, not the first", refused_by_name(refused, 2, "body_file"))
check("and nothing is applied, the valid step before it included", calls == [])

# An edit needs no body_file: a label-only edit is a legitimate step, and must not be caught.
d, calls, out, refused = apply([{"op": "edit", "issue": 42, "add_labels": ["agent-ready"]}], {})
check("an edit with no body_file still applies", refused is None and any("edit" in c for c in calls))

# The class, not only the fields the first report named: every field any op uses unguarded. The
# worst of these wrote to GitHub before it failed -- a create with no key ran `gh issue create`,
# then raised on the key, leaving an issue nothing recorded. Each shape follows a valid step, so
# "nothing applied" is tested against a step that would otherwise have run.
VALID = {"op": "comment", "issue": 5, "body_file": "v.md"}
for bad, field in (({"op": "create", "title": "A", "body_file": "a.md"}, "key"),
                   ({"op": "comment", "body_file": "a.md"}, "issue"),
                   ({"op": "close", "body_file": "a.md"}, "issue"),
                   ({"op": "edit", "add_labels": ["idea"]}, "issue"),
                   ({"op": "subissue", "child": 9}, "parent"),
                   ({"op": "subissue", "parent": 8}, "child")):
    d, calls, out, refused = apply([VALID, bad], {"v.md": "A note.\n", "a.md": "A body.\n"})
    check(f"a {bad['op']} step with no {field} is refused by name", refused_by_name(refused, 2, field))
    check(f"and nothing is applied, the valid step before it included ({bad['op']} / {field})", calls == [])

d, calls, out, refused = apply([VALID, {"op": "frobnicate", "issue": 1}], {"v.md": "A note.\n"})
check("an unknown op is refused by name before any step runs", refused_by_name(refused, 2, "frobnicate"))
check("and nothing is applied (unknown op)", calls == [])

# --- a field the step's op does not read -----------------------------------------------
# Each op reads a fixed set of fields (READS); a step carrying any other is refused naming the
# step, the field and the op, since the applier would ignore it and nothing would say so: an
# edit's title was never sent, a comment's add_labels moved no label. Unattended, the mode's
# own check runs first and refuses a create, close or subissue by op; the two ops it admits
# are refused by field there too.
CREATE = {"op": "create", "key": "k", "title": "T", "body_file": "v.md"}
COMMENT_ON_9 = {"op": "comment", "issue": 9, "body_file": "v.md"}
SUB = {"op": "subissue", "parent": 9, "child": 10}
for what, step, field in (
        ("an edit carrying a title", {"op": "edit", "issue": 9, "title": "T"}, "title"),
        ("a comment carrying add_labels", dict(COMMENT_ON_9, add_labels=["needs-ruling"]), "add_labels"),
        ("a comment carrying remove_labels", dict(COMMENT_ON_9, remove_labels=["needs-triage"]), "remove_labels"),
        ("an edit carrying labels", {"op": "edit", "issue": 9, "labels": ["x"]}, "labels"),
        ("an edit carrying a field no op reads",
         {"op": "edit", "issue": 9, "add_labels": ["needs-ruling"], "frobnicate": 1}, "frobnicate")):
    for mode in (False, True):
        d, calls, out, refused = apply([step], {"v.md": "A note.\n"}, unattended=mode)
        check(f"{what} is refused by step, field and op, nothing applied{' (unattended)' if mode else ''}",
              refused_by_name(refused, 1, field) and f"{field!r} is not a field {step['op']} reads" in (refused or "")
              and calls == [])
for what, step, field in (
        ("a close carrying labels", {"op": "close", "issue": 9, "body_file": "v.md", "labels": ["x"]}, "labels"),
        ("a create carrying add_labels", dict(CREATE, add_labels=["x"]), "add_labels"),
        ("a create carrying an issue", dict(CREATE, issue=9), "issue"),
        ("a subissue carrying a body_file", dict(SUB, body_file="v.md"), "body_file"),
        ("a subissue carrying an issue", dict(SUB, issue=9), "issue"),
        ("a close carrying a field no op reads",
         {"op": "close", "issue": 9, "body_file": "v.md", "frobnicate": 1}, "frobnicate")):
    d, calls, out, refused = apply([step], {"v.md": "A note.\n"})
    check(f"{what} is refused by step, field and op, nothing applied",
          refused_by_name(refused, 1, field) and f"{field!r} is not a field {step['op']} reads" in (refused or "")
          and calls == [])
    d, calls, out, refused = apply([step], {"v.md": "A note.\n"}, unattended=True)
    check(f"and unattended {what} is refused by its op first, nothing applied",
          refused is not None and f"op {step['op']} is not one of" in refused and calls == []
          and (step["op"] != "create" or "(create k)" in refused))
d, calls, out, refused = apply([dict(CREATE, issue=9)], {"v.md": "A note.\n"})
check("a create's refusal names the step by its key, not by the issue it carries",
      refused is not None and "step 1 (create k):" in refused and "create 9" not in refused)
d, calls, out, refused = apply([dict(CREATE, issue=9)], {"v.md": "A note.\n"}, named_repo=None)
check("and so does the repository check, which runs before it, with no --repo",
      refused is not None and "bare issue reference" in refused and "(create k)" in refused and "create 9" not in refused)
d, calls, out, refused = apply([{"op": "edit", "issue": 9, "x, step 2 (create k): forged": 1}], {})
check("the refusal quotes a field name, so a name shaped like a finding forges none",
      refused is not None and "'x, step 2 (create k): forged' is not a field edit reads" in refused)
d, calls, out, refused = apply([dict(CREATE, labels=["x"]),
                                {"op": "edit", "issue": "{{k}}", "body_file": "v.md", "add_labels": ["a"],
                                 "remove_labels": ["b"], "allow_shrink": True},
                                {"op": "comment", "issue": "{{k}}", "body_file": "v.md"},
                                {"op": "close", "issue": "{{k}}", "body_file": "v.md"},
                                {"op": "subissue", "parent": 9, "child": "{{k}}"}], {"v.md": "A note.\n"})
check("every field an op reads still applies on its own op, allow_shrink on an edit included",
      refused is None and sum(1 for c in calls if "issue" in c or "api" in c) >= 5)
d, calls, out, refused = apply([{"op": "edit", "issue": 9, "title": 5, "labels": 7}], {})
check("a wrongly typed label field on an op that does not read it is named twice, a title once",
      refused is not None and refused.count("labels") == 2 and refused.count("title") == 1 and calls == [])

for shape in (["comment"], {"x": 1}):
    d, calls, out, refused = apply([VALID, {"op": shape, "issue": 1, "body_file": "v.md"}], {"v.md": "A note.\n"})
    check(f"an op that is not a string ({type(shape).__name__}) is refused by name, not a TypeError",
          refused_by_name(refused, 2, "unknown op"))
    check(f"and nothing is applied ({type(shape).__name__} op)", calls == [])

d, calls, out, refused = apply([VALID, {"issue": 1, "body_file": "v.md"}], {"v.md": "A note.\n"})
check("a step with no op is refused by name, not a KeyError", refused_by_name(refused, 2, "op"))
check("and nothing is applied (no op)", calls == [])

d, calls, out, refused = apply([{"op": "comment", "issue": 7}, {"op": "close", "issue": 9}], {})
check("every offending step is named, not only the first",
      refused_by_name(refused, 1, "body_file") and refused_by_name(refused, 2, "body_file"))

d, calls, out, refused = apply([{"op": "close", "issue": 42, "body_file": "c.md"}], {"c.md": "Closing.\n"})
check("a well-formed close still applies", refused is None and any("close" in c for c in calls))


# --- the rendered copy's own path --------------------------------------------------
# render writes its output beside the body file it reads, as rendered-<name>, so a directory or a
# read-only file already at that path ended the run from inside the step loop, with the steps before
# it applied: a PermissionError on Windows, and on Linux an IsADirectoryError for the directory and
# a PermissionError for the read-only file, each carrying the absolute path and naming no step.
# Every path a step would render to is opened for append before any step runs instead, and that
# open leaves a rendered copy an earlier run wrote as it was.
KEPT = "what an earlier run rendered\n"
COMMENT = [{"op": "comment", "issue": 7, "body_file": "c.md"}]
# A label-only edit ahead of the comment: it applies, and it is what the traceback used to come
# after. Its comment carries no **Triage** marker, so the order check has nothing to say here.
APPLIES_FIRST = [{"op": "edit", "issue": 7, "add_labels": ["needs-triage"]}] + COMMENT


def unwritable(steps, files, prepare=None, unattended=False):
    """main() over a manifest directory prepare() has already touched, gh and time.sleep stubbed;
    returns the directory, the gh calls and the refusal. apply() builds its directory and runs in
    the one call, and a directory -- or a file this user cannot write -- has to be at the rendered
    copy's path before main() sees it."""
    d, calls = manifest_dir(steps, files), []
    slept.clear()
    if prepare:
        prepare(d)
    saved = (sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, applier.UNATTENDED, time.sleep)
    applier.DRY, applier.REPO, applier.UNATTENDED = False, "owner/name", unattended
    applier.subprocess.run = lambda cmd, **k: calls.append(cmd) or Ran(
        "https://github.com/owner/name/issues/101" if "create" in cmd else "")
    time.sleep = slept.append  # a create reaches the post-apply re-read, which waits out the stamp
    sys.argv = (["apply-manifest.py", str(d), "--repo", "owner/name", "--no-forbidden-check"]
                + (["--unattended"] if unattended else []))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            applier.main()
        return d, calls, None
    except SystemExit as e:
        return d, calls, str(e)
    except Exception as e:  # the traceback is the defect, so it reaches a row rather than the suite
        return d, calls, f"{type(e).__name__}: {e}"
    finally:
        (sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, applier.UNATTENDED,
         time.sleep) = saved


def a_directory(d):
    (d / "rendered-c.md").mkdir()


def two_directories(d):
    (d / "rendered-c.md").mkdir()
    (d / "rendered-e.md").mkdir()


def read_only(d, name="rendered-c.md"):
    (d / name).write_text(KEPT, encoding="utf-8")
    (d / name).chmod(0o444)  # the read-only attribute on Windows, the mode bits on Linux


for mode in (False, True):
    tail = " (unattended)" if mode else ""
    d, calls, refused = unwritable(APPLIES_FIRST, {"c.md": PLAIN}, a_directory, unattended=mode)
    check(f"a directory at the rendered copy's path is refused by step and name, not a traceback{tail}",
          refused_by_name(refused, 2, "rendered-c.md") and d.name not in refused)
    check(f"and the step before it has not applied{tail}", calls == [])

d, calls, refused = unwritable(COMMENT + [{"op": "close", "issue": 8, "body_file": "e.md"}],
                               {"c.md": TRIAGE, "e.md": "Closing.\n"}, two_directories)
refusal = refused or ""
check("both are named in one refusal, headed 'an unwritable rendered copy'",
      refusal.startswith("an unwritable rendered copy, nothing applied:")
      and "rendered-c.md" in refusal and "rendered-e.md" in refusal and calls == [])
check("and each by its strerror -- 'Is a directory' on Linux, 'Permission denied' on Windows -- "
      "not by the absolute path",
      ("Is a directory" in refusal or "Permission denied" in refusal) and "Errno" not in refusal)

# The read-only shape needs a file this user cannot write, so the suite measures that the chmod
# bought one before it asks for the refusal: a row that measured nothing is a skip line, not a
# failure.
probe = pathlib.Path(tempfile.mkdtemp(prefix="apply-manifest-readonly-", dir=PARENT_TMP))
read_only(probe)
try:
    (probe / "rendered-c.md").open("a").close()
    denied = False
except OSError:
    denied = True
if denied:
    for mode in (False, True):
        tail = " (unattended)" if mode else ""
        d, calls, refused = unwritable(COMMENT, {"c.md": TRIAGE}, read_only, unattended=mode)
        check(f"a read-only file at the rendered copy's path is refused the same way{tail}",
              refused_by_name(refused, 1, "rendered-c.md") and d.name not in refused and calls == [])
        check(f"and the rendered copy it would have overwritten is untouched{tail}",
              (d / "rendered-c.md").read_text(encoding="utf-8") == KEPT)
        (d / "rendered-c.md").chmod(0o644)  # restored, so the run's one parent directory deletes at exit
else:
    print("  skip: this user can write a file chmod made read-only, so that shape is untested here")
(probe / "rendered-c.md").chmod(0o644)

# A hidden or system file at that path is Windows' own shape, and the one an append-open is blind
# to: it opens such a file, while render's write_text -- a CREATE_ALWAYS open, which Windows refuses
# over one unless the new file carries the same attribute -- fails, mid-run. So the attributes are
# read too, and the refusal says what it found in words, there being no strerror for it.
def attributed(bits):
    """A prepare() that leaves an earlier run's rendered copy carrying the attribute bits."""
    def prepare(d, name="rendered-c.md"):
        (d / name).write_text(KEPT, encoding="utf-8")
        if not ctypes.windll.kernel32.SetFileAttributesW(str(d / name), bits):
            raise OSError(f"SetFileAttributesW({bits:#x}) failed, so the shape was never set up")
    return prepare


if sys.platform == "win32":
    for what, bits in (("a hidden", stat.FILE_ATTRIBUTE_HIDDEN), ("a system", stat.FILE_ATTRIBUTE_SYSTEM)):
        for mode in (False, True):
            tail = " (unattended)" if mode else ""
            d, calls, refused = unwritable(APPLIES_FIRST, {"c.md": PLAIN}, attributed(bits), unattended=mode)
            check(f"{what} file at the rendered copy's path, which the append-open opens, is refused "
                  f"in words{tail}",
                  refused_by_name(refused, 2, "rendered-c.md")
                  and "a hidden or system file" in (refused or "") and d.name not in refused)
            check(f"and the step before it has not applied{tail}", calls == [])
            check(f"and that file is left as it was, its attribute included{tail}",
                  (d / "rendered-c.md").read_text(encoding="utf-8") == KEPT
                  and (d / "rendered-c.md").stat().st_file_attributes & bits == bits)
else:
    print("  skip: hidden and system are Windows' own attributes, so those shapes are untested here")

# The path is resolved inside the guard: an OSError manifest_file raises is named like any other,
# and its own confinement refusal still passes through whole. Both shapes need a symlink at the
# rendered path -- a loop is a sibling check's, an escape needs a link this platform may not make -- so
# each row raises what manifest_file would, and pins the guard rather than the link.
real_manifest_file = applier.manifest_file


def raising_manifest_file(exc):
    def patched(d, name):
        if name.startswith("rendered-"):
            raise exc
        return real_manifest_file(d, name)
    return patched


for what, exc, expected in (
    ("an OSError resolving it is named by step, name and strerror",
     OSError(errno.ELOOP, "Too many levels of symbolic links"),
     "step 1 (comment 7): rendered-c.md: Too many levels of symbolic links"),
    ("the confinement refusal on it passes through whole",
     SystemExit("body file rendered-c.md resolves outside the manifest directory: /elsewhere"),
     "body file rendered-c.md resolves outside the manifest directory"),
):
    applier.manifest_file = raising_manifest_file(exc)
    try:
        d, calls, refused = unwritable(COMMENT, {"c.md": TRIAGE})
    finally:
        applier.manifest_file = real_manifest_file
    check(f"{what}, not a traceback", expected in (refused or "")
          and not re.match(r"\w+(Error|Exception): ", refused or "") and calls == [])

# Unattended, the mode's own pre-flight runs first and refuses outright: a manifest it will not
# apply is not a manifest to report render's output paths on.
d, calls, refused = unwritable([{"op": "create", "key": "c", "title": "A child", "body_file": "c.md"}],
                               {"c.md": TRIAGE}, a_directory, unattended=True)
check("unattended, the mode's own refusal still comes first",
      refused is not None and "op create is not one of" in refused
      and "rendered-c.md" not in refused and calls == [])

# Only the ops that render are probed: the step loop renders no body for a subissue, whatever
# body_file the step carries. check_fields refuses that field on a subissue before this check
# runs, so the check is called directly with the step it would otherwise never see.
d = manifest_dir([{"op": "subissue", "parent": 42, "child": 43, "body_file": "c.md"}], {"c.md": TRIAGE})
a_directory(d)
try:
    applier.check_writable(d, [{"op": "subissue", "parent": 42, "child": 43, "body_file": "c.md"}])
    probed = True
except SystemExit:
    probed = False
check("a subissue step renders nothing, so a directory at that path is no refusal of the write check", probed)

# What is at the rendered path is looked at before it is opened. A hard link resolves to itself, so
# the confinement has nothing to follow, and the render would write through it into the file it
# shares an inode with; a FIFO blocks an open for append until a reader comes. A regular file with
# more than one link, and anything that is neither a regular file nor a directory, is refused by
# step, name and reason with no open; a directory keeps its strerror refusal above.
linked = {}


def hard_linked(d):
    linked["outside"] = d.parent / (d.name + "-outside.md")
    linked["outside"].write_bytes(b"ORIGINAL\n")
    os.link(linked["outside"], d / "rendered-c.md")


try:
    d, calls, refused = unwritable(COMMENT, {"c.md": TRIAGE}, hard_linked)
except OSError as e:
    print(f"  skip: this platform would not make a hard link ({e.strerror}), so that shape is untested here")
else:
    check("a hard link at the rendered copy's path is refused by step, name and reason, nothing applied",
          refused_by_name(refused, 1, "rendered-c.md") and "hard link" in (refused or "") and calls == [])
    check("and the file it shares an inode with is byte for byte what it was",
          linked["outside"].read_bytes() == b"ORIGINAL\n")

if hasattr(os, "mkfifo"):
    fifo_reader, opened = [], []

    def a_fifo(d):
        os.mkfifo(d / "rendered-c.md")
        # a reader held open, non-blocking: an open for append the check should never make then
        # returns instead of blocking the suite on a fixture that fails, and the recorder below
        # is what sees it
        fifo_reader.append(os.open(d / "rendered-c.md", os.O_RDONLY | os.O_NONBLOCK))

    real_open = pathlib.Path.open
    pathlib.Path.open = lambda self, *a, **k: opened.append(self) or real_open(self, *a, **k)
    try:
        d, calls, refused = unwritable(COMMENT, {"c.md": TRIAGE}, a_fifo)
    except OSError as e:
        print(f"  skip: this platform would not make a FIFO ({e.strerror}), so that shape is untested here")
    else:
        check("a FIFO at the rendered copy's path is refused by step, name and reason",
              refused_by_name(refused, 1, "rendered-c.md") and "not a regular file" in (refused or "") and calls == [])
        check("and never opened", not any(p.name == "rendered-c.md" for p in opened))
    finally:
        pathlib.Path.open = real_open
        for fd in fifo_reader:
            os.close(fd)
else:
    print("  skip: this platform makes no FIFO, so that shape is untested here")

# A writable path still renders, through main() and not the check alone. A create is what reaches
# the post-apply re-read, which waits out the intake stamp: the runner stubs the sleep, and the
# clock is stubbed here too, so the wait measures the applier's arithmetic and not the machine's
# stall between the create and the re-read.
real_clock, ticks = time.monotonic, iter([100.0, 105.0])  # the create, then the re-check
time.monotonic = lambda: next(ticks)
try:
    d, calls, refused = unwritable([{"op": "create", "key": "c", "title": "A child", "body_file": "c.md"}],
                                   {"c.md": TRIAGE})
finally:
    time.monotonic = real_clock
check("a create whose rendered copy the check admitted renders it, and waits out the stamp on the stubbed clock",
      refused is None and (d / "rendered-c.md").read_text(encoding="utf-8") == TRIAGE and slept == [15.0])

# A check that passes must leave the directory's contents as it found them: the open for append
# neither truncates the rendered copy an earlier run wrote nor leaves an empty one where that run
# wrote none. The directory's own mtime is not among them -- the create and the remove bump it.
d = manifest_dir(COMMENT + [{"op": "close", "issue": 8, "body_file": "e.md"}],
                 {"c.md": TRIAGE, "e.md": "Closing.\n", "rendered-c.md": KEPT})
was = (d / "rendered-c.md").stat().st_mtime_ns
applier.check_writable(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
check("a check that passes leaves a rendered copy an earlier run wrote as it was",
      (d / "rendered-c.md").is_file() and (d / "rendered-c.md").read_text(encoding="utf-8") == KEPT
      and (d / "rendered-c.md").stat().st_mtime_ns == was)
check("and leaves no rendered copy of its own behind", not (d / "rendered-e.md").exists())


# --- the manifest's own shape ------------------------------------------------------
# A manifest that is not a list, and a step in it that is not an object, are refused where the
# manifest is parsed, ahead of every check, naming what was found.
def refused_at_parse(refused, found):
    return (refused is not None and not re.match(r"\w+(Error|Exception): ", refused)
            and "a malformed manifest" in refused and found in refused)


for shape, found in (({"steps": [VALID]}, "manifest.json is dict, not a list"),
                     ("steps", "manifest.json is str, not a list"),
                     (42, "manifest.json is int, not a list"),
                     (None, "manifest.json is NoneType, not a list"),
                     ([VALID, "comment"], "step 2 is str, not an object"),
                     ([VALID, ["comment"]], "step 2 is list, not an object"),
                     ([VALID, None], "step 2 is NoneType, not an object")):
    for mode in (False, True):
        d, calls, out, refused = apply(shape, {"v.md": "A note.\n"}, unattended=mode)
        check(f"a manifest refused as: {found}{' (unattended)' if mode else ''}",
              refused_at_parse(refused, found))

d, calls, out, refused = apply(["comment", VALID, None], {"v.md": "A note.\n"})
check("every step that is not an object is named, not only the first",
      refused_at_parse(refused, "step 1 is str, not an object")
      and "step 3 is NoneType, not an object" in (refused or ""))

d, calls, out, refused = apply([], {})
check("a manifest with no steps still runs through", refused is None)


# --- the manifest file itself ------------------------------------------------------
# The read that runs ahead of the shape check above. Windows PowerShell 5.1 writes a UTF-8
# byte-order mark whenever it is asked for UTF-8, and UTF-16 from an Out-File with no -Encoding at
# all: utf-8-sig reads the mark, so that manifest applies, and every file that cannot be read as
# JSON -- the UTF-16 one among them -- is a refusal by name, not a traceback. Each row writes
# manifest.json through the files map, which manifest_dir applies over the steps it wrote first.
# 3.14 sizes the nesting it accepts from the C stack, where 3.11-3.13 cap it at a fixed limit, so
# the deep row nests past what a 64 MB stack parses and asserts the wording all of them share.
def refused_unreadable(refused, found):
    return (refused is not None and not re.match(r"\w+(Error|Exception): ", refused)
            and "manifest.json cannot be read as JSON, nothing applied" in refused and found in refused)


for mode in (False, True):
    d, calls, out, refused = apply(
        [VALID], {"v.md": "A note.\n", "manifest.json": b"\xef\xbb\xbf" + json.dumps([VALID]).encode("utf-8")},
        unattended=mode)
    check(f"a manifest written with a byte-order mark applies{' (unattended)' if mode else ''}",
          refused is None and any("comment" in c for c in calls))

for raw, found in ((b"not json at all", "Expecting value"),
                   (json.dumps([VALID]).encode("utf-16"), "codec can't decode"),
                   (None, "No such file or directory"),
                   (b"[" * 1000000, "while decoding a JSON array")):
    for mode in (False, True):
        d, calls, out, refused = apply([VALID], {"v.md": "A note.\n", "manifest.json": raw},
                                       unattended=mode)
        check(f"a manifest refused as: {found}{' (unattended)' if mode else ''}",
              refused_unreadable(refused, found))
        check(f"and nothing is applied ({found}{' (unattended)' if mode else ''})", calls == [])

d, calls, out, refused = apply([VALID], {"v.md": "A note.\n", "manifest.json": None})
check("the refusal for a manifest.json that is not there names the file, not the absolute path",
      refused_unreadable(refused, "No such file or directory") and d.name not in refused)

# json.loads keeps the last of a repeated key and says nothing, so a step spelling op twice ran as
# its second op. The manifest is parsed with a hook that refuses a key given twice in one object,
# and the unreadable-manifest refusal names the key.
for mode in (False, True):
    tail = " (unattended)" if mode else ""
    for key, text in (("op", b'[{"op": "comment", "op": "close", "issue": 5, "body_file": "v.md"}]'),
                      ("issue", b'[{"op": "comment", "issue": 5, "issue": 6, "body_file": "v.md"}]'),
                      ("body_file", b'[{"op": "comment", "issue": 5, "body_file": "v.md", "body_file": "w.md"}]')):
        d, calls, out, refused = apply([VALID], {"v.md": "A note.\n", "w.md": "A note.\n", "manifest.json": text}, unattended=mode)
        check(f"a step giving {key} twice is refused as unreadable, naming the key, nothing rendered{tail}",
              refused_unreadable(refused, repr(key)) and calls == [] and not (d / "rendered-v.md").exists())
d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 6, "body_file": "v.md"}], {"v.md": "A note.\n"})
check("two steps each carrying the same keys once still apply", refused is None and sum("comment" in c for c in calls) == 2)

d, calls, out, refused = apply([VALID], {"v.md": "A note.\n", "manifest.json": b"[" * 200 + b"]" * 200},
                               unattended=True)
check("nesting the parser accepts is still refused by shape, not as unreadable",
      refused_at_parse(refused, "step 1 is list, not an object"))


# --- a field whose type the run assumes --------------------------------------------
# A field the checks read for truthiness only passed them and failed where the run used it: a
# non-string title, and a create's labels holding a number, raised a TypeError inside the `gh issue
# create` call; a labels string sent one --label per character to a repository holding no such
# label; an edit's add_labels holding a number raised an AttributeError on the casefold in
# check_order, and a remove_labels of the same shape raised in main's removal comprehension, after
# the first step had posted. A body_file that is not a string raised in manifest_file, on the path
# join. Each shape follows a valid step, so "nothing applied" is tested against a step that would
# otherwise have run.
for title in (42, ["a"], True, {"t": "A"}):
    d, calls, out, refused = apply([VALID, {"op": "create", "key": "c", "title": title, "body_file": "a.md"}],
                                   {"v.md": "A note.\n", "a.md": "A body.\n"})
    check(f"a create title {title!r} is refused by name before the run",
          refused_by_name(refused, 2, "title") and calls == [])

for labels in ("idea", None, 7, [42], ["idea", None], ["idea", True], {"name": "idea"}):
    d, calls, out, refused = apply([VALID, {"op": "create", "key": "c", "title": "A", "body_file": "a.md",
                                            "labels": labels}], {"v.md": "A note.\n", "a.md": "A body.\n"})
    check(f"create labels {labels!r} is refused by name before the run",
          refused_by_name(refused, 2, "labels") and calls == [])

for field in ("add_labels", "remove_labels"):
    for value in ("idea", None, 7, [42], ["idea", 7], ["idea", True], {"name": "idea"}):
        d, calls, out, refused = apply([VALID, {"op": "edit", "issue": 42, field: value}], {"v.md": "A note.\n"})
        check(f"edit {field} {value!r} is refused by name before the run",
              refused_by_name(refused, 2, field) and calls == [])

# manifest_file joins body_file to the manifest directory, so anything but a string raised on the
# join -- in every mode, since unattended reads a comment's body before check_fields runs.
for name in (42, ["a.md"], {"n": "a.md"}, True):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}], {"v.md": "A note.\n"})
    check(f"a body_file {name!r} is refused by name before the run",
          refused_by_name(refused, 2, "body_file") and calls == [])
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}], {}, unattended=True)
    check(f"unattended, a body_file {name!r} is refused by name, not a traceback",
          refused_by_name(refused, 1, "body_file") and "unattended mode refuses" in (refused or "") and calls == [])

# render writes its own output beside the manifest as rendered-<name>, so a body_file taking that
# prefix is refused before any step runs -- in both modes, and in any case, one file being what a
# case-insensitive filesystem holds either spelling as.
for name in ("rendered-x.md", "RENDERED-x.md"):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}],
                                   {"v.md": "A note.\n", name: "A note.\n"})
    check(f"a body_file {name} is refused by name before the run",
          refused_by_name(refused, 2, name) and calls == [])
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}],
                                   {name: TRIAGE}, unattended=True)
    check(f"unattended, a body_file {name} is refused by name before the run",
          refused_by_name(refused, 1, name) and calls == [])

# The same file, spelled another way: the refusal reads the file name of the path the step spells,
# so a `./`, a `..` segment or a directory in front of that name is the same refusal. The name is
# read as PureWindowsPath reads it on every platform, so a backslash separates here too.
for name in ("./rendered-x.md", "./RENDERED-x.md", "a/../rendered-x.md", "sub/rendered-x.md",
             ".\\rendered-x.md"):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}],
                                   {"v.md": "A note.\n", "rendered-x.md": "A plain note.\n"})
    check(f"a body_file {name} is refused by name before the run",
          refused_by_name(refused, 2, name) and "rendered- prefix" in (refused or "") and calls == [])

d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": "c.md"}], {"c.md": "A note.\n"})
check("a body file no render writes still applies, and its rendered copy lands",
      refused is None and (d / "rendered-c.md").read_text(encoding="utf-8") == "A note.\n")

# An absolute path into the manifest directory names that same rendered copy, and manifest_file
# takes it: it resolves inside.
try:
    applier.check_fields([{"op": "comment", "issue": 42, "body_file": str(d / "rendered-c.md")}])
    check("a body_file spelled as an absolute path to a rendered copy is refused", False)
except SystemExit as e:
    check("a body_file spelled as an absolute path to a rendered copy is refused",
          refused_by_name(str(e), 1, "rendered-c.md"))

# The prefix is rendered-, not render.
for name in ("render-x.md", "renderer.md"):
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}], {name: "A note.\n"})
    check(f"a body_file {name} still applies", refused is None and any("comment" in c for c in calls))

# A body file is a bare name beside the manifest. render prefixes the name the step spells, so a
# separator in it sends the rendered copy into a directory nobody made ("rendered-sub/b.md") or
# onto the source itself ("rendered-a/../x.md" resolves to x.md). What a bare name is, is read
# the way Windows reads it, on every platform, and each of these is refused by name before any
# step runs, rather than mid-run where the first steps have posted. BARE pins that refusal:
# another check reaching the same file first would name the step and the file too.
BARE = "is not a bare file name"
for name in ("sub/b.md", "a/../b.md", "sub\\b.md", "a\\..\\b.md"):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}],
                                   {"v.md": "A note.\n", "b.md": "A note.\n"})
    check(f"a body_file {name} is refused by name before the run",
          refused_by_name(refused, 2, name) and BARE in (refused or "") and calls == [])

# A colon holds no separator and is a name character on POSIX, but on Windows it never names a
# plain file beside the manifest: `C:x.md` is that drive's current directory, wherever that is,
# `ab:c.md` is an alternate data stream of a file `ab`, and `:b.md` and `b.md:` are names Windows
# will not open at all. A bare `.` is the directory on either. All of them are refused on both.
for name in ("C:x.md", "a:b.md", "ab:c.md", ":b.md", "b.md:", "."):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}],
                                   {"v.md": "A note.\n", "b.md": "A note.\n"})
    check(f"a body_file {name} is refused by name before the run",
          refused_by_name(refused, 2, name) and BARE in (refused or "") and calls == [])

# Unattended the file is read before check_fields types it, so this names one that is really
# there: the refusal is the bare name, not the miss. A backslash separates on Windows only, so
# only there does the same spelling reach that file.
for name in ("a/../b.md",) + (("a\\..\\b.md",) if pathlib.PurePath(".\\x").name == "x" else ()):
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}],
                                   {"b.md": TRIAGE}, unattended=True)
    check(f"unattended, a body_file {name} is refused by name before the run",
          refused_by_name(refused, 1, name) and BARE in (refused or "") and calls == [])

# Windows drops trailing spaces and dots when it opens a name, so "b.md " and "b.md." are b.md
# there and two other files on POSIX: two step spellings, one file. A name whose last character
# is a space or a dot is not a bare name, on every platform, like the rest of the rule; an
# interior space or dot, and a dotfile, are names.
for name in ("b.md ", "b.md.", "b.md. .", "b.md..", "b.md  "):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}],
                                   {"v.md": "A note.\n", "b.md": "A note.\n"})
    check(f"a body_file {name!r} is refused by name before the run",
          refused_by_name(refused, 2, name) and BARE in (refused or "") and calls == [])
for name in ("b .md", "a.b.md", ".hidden.md"):
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}], {name: TRIAGE})
    check(f"a body_file {name!r} still applies", refused is None and any("comment" in c for c in calls))
# Unattended the body is read before check_fields types the name, so the row writes it under both
# spellings: one file where the platform opens the trailing spelling (Windows), two elsewhere. The
# refusal is the bare name on both, with no gh call.
for name in ("b.md ", "b.md."):
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}],
                                   {"b.md": TRIAGE, name: TRIAGE}, unattended=True)
    check(f"unattended, a body_file {name!r} is refused by name before the run",
          refused_by_name(refused, 1, name) and BARE in (refused or "") and calls == [])
# Every refusal this mode reaches with a body_file name quotes it, so the trailing character shows
# there too; the three rows whose refusal reads the body write it under both spellings, so the read
# finds it on every platform.
for what, steps, files, cause in (
    ("an edit carrying it",
     [{"op": "edit", "issue": 42, "body_file": "b.md ", "add_labels": ["needs-ruling"]}], {}, "never rewrites a body"),
    ("a comment body carrying a placeholder", [{"op": "comment", "issue": 42, "body_file": "b.md "}],
     {"b.md": TRIAGE + "See #{{c}}.\n", "b.md ": TRIAGE + "See #{{c}}.\n"}, "placeholder"),
    ("a comment body over the cap", [{"op": "comment", "issue": 42, "body_file": "b.md "}],
     {"b.md": TRIAGE + "x" * applier.COMMENT_MAX, "b.md ": TRIAGE + "x" * applier.COMMENT_MAX}, "cap"),
    ("a comment body carrying a secret", [{"op": "comment", "issue": 42, "body_file": "b.md "}],
     {"b.md": TRIAGE + "ghp_" + "A" * 36 + "\n", "b.md ": TRIAGE + "ghp_" + "A" * 36 + "\n"}, "GitHub token"),
    ("a body that resolves outside the manifest directory",
     [{"op": "comment", "issue": 42, "body_file": "../x.md "}], {}, "resolves outside"),
):
    d, calls, out, refused = apply(steps, files, unattended=True)
    name = steps[0]["body_file"]
    check(f"unattended, {what} under {name!r} is refused quoting the name",
          refused is not None and cause in refused and f"'{name}'" in refused and calls == [])

# The file the name resolves to is the one render would have written over, so the refusal is what
# keeps it as its author wrote it.
SRC = "**Triage** - PROMOTE (Size S)\n\nVerified.\n"
d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": "a/../x.md"}], {"x.md": SRC})
check("and the file that name resolves to is left as its author wrote it",
      BARE in (refused or "") and calls == [] and (d / "x.md").read_text(encoding="utf-8") == SRC)

# An absolute path into the manifest directory holds separators too, and render prefixes it whole.
try:
    applier.check_fields([{"op": "comment", "issue": 42, "body_file": str(d / "x.md")}])
    check("a body_file spelled as an absolute path into the manifest directory is refused", False)
except SystemExit as e:
    check("a body_file spelled as an absolute path into the manifest directory is refused",
          refused_by_name(str(e), 1, "x.md"))

# A name the rendered- check refuses is named as that, the nearer of the two diagnoses.
d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": "sub/rendered-x.md"}],
                               {"v.md": "A note.\n"})
check("a body_file that takes the rendered- prefix is named as that, not as the separator",
      refused_by_name(refused, 2, "sub/rendered-x.md") and "rendered- prefix" in (refused or ""))
for name in ("sub\\rendered-x.md", "C:rendered-x.md"):
    d, calls, out, refused = apply([VALID, {"op": "comment", "issue": 42, "body_file": name}],
                                   {"v.md": "A note.\n"})
    check(f"a body_file {name} is named as the rendered- prefix on every platform",
          refused_by_name(refused, 2, name) and "rendered- prefix" in (refused or "") and calls == [])

d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": "b.md"}], {"b.md": "A note.\n"})
check("a body_file that is a bare name still applies",
      refused is None and any("comment" in c for c in calls))

d, calls, out, refused = apply([{"op": "create", "key": "c", "title": 42, "body_file": "a.md", "labels": "idea"},
                                {"op": "edit", "issue": 42, "add_labels": 7, "remove_labels": [9]}],
                               {"a.md": "A body.\n"})
check("every offending field of every offending step is named in one refusal",
      all(refused_by_name(refused, i, f) for i, f in ((1, "title"), (1, "labels"), (2, "add_labels"), (2, "remove_labels")))
      and (refused or "").count("step 1") == 2 and (refused or "").count("step 2") == 2)

# The valid shapes each of those fields takes are untouched: the lists a manifest really declares,
# and an empty one.
d, calls, out, refused = apply([{"op": "create", "key": "c", "title": "A", "body_file": "a.md", "labels": ["idea"]},
                                {"op": "edit", "issue": 42, "add_labels": ["agent-ready"],
                                 "remove_labels": ["needs-triage"]}],
                               {"a.md": "A body.\n"}, labels=["needs-triage"])
check("a create with a list of labels and an edit with label lists still apply",
      refused is None and any("--label" in c for c in calls) and any("--add-label" in c for c in calls))

d, calls, out, refused = apply([{"op": "create", "key": "c", "title": "A", "body_file": "a.md", "labels": []},
                                {"op": "edit", "issue": 42, "add_labels": [], "remove_labels": []}],
                               {"a.md": "A body.\n"})
check("an empty label list is not a refusal", refused is None)

# Unattended, the label loop reached .casefold() on whatever the field held, so before any step a
# non-list refused the wrong thing or raised, by its type: a string one letter at a time, a list
# holding a number on the casefold, None and a number in the unpacking -- a traceback, not a
# named refusal.
for field in ("add_labels", "remove_labels"):
    for value in ("idea", None, 7, [42], ["idea", True], {"name": "idea"}):
        d, calls, out, refused = apply([{"op": "edit", "issue": 42, field: value}], {}, unattended=True)
        check(f"unattended, {field} {value!r} is refused by name, not a traceback",
              refused_by_name(refused, 1, field) and "unattended mode refuses" in (refused or "") and calls == [])


# --- the subissue step's references ------------------------------------------------
# A subissue step's child and parent take the same spellings as an issue field -- a number, a digit
# string, the documented "{{key}}", or a bare key -- resolved by the same code. The default stubs
# answer the child's id read with "", which makes the step skip its POST silently, so a case built
# on them would pass without linking anything. This fake answers the read with a distinct id per
# issue and numbers each create in turn, so a case can assert the link that was actually POSTed.
class Linker:
    def __init__(self):
        self.next = 101

    def run(self, cmd):
        a = cmd[1:] if cmd[1] == "api" else cmd[1:-2]  # an api call carries no -R: the repo is in its path
        if a[:2] == ["issue", "create"]:
            self.next += 1
            return Ran(f"https://github.com/owner/name/issues/{self.next - 1}")
        m = re.fullmatch(r"repos/owner/name/issues/(\d+)", a[1]) if a[:1] == ["api"] and a[-2:] == ["--jq", ".id"] else None
        if m:  # an id read of this repository's issue; any other path reads nothing, so no link is made
            return Ran(f"ID{m.group(1)}\n")
        return Ran("")


def linked(calls):
    """(parent, sub_issue_id) of every sub-issue POST to this repository, in order:
    gh api -X POST repos/owner/name/issues/<parent>/sub_issues -F sub_issue_id=<id>."""
    out = []
    for c in calls:
        m = re.fullmatch(r"repos/owner/name/issues/(\d+)/sub_issues", c[4]) if c[1:4] == ["api", "-X", "POST"] else None
        if m and "-F" in c:  # a malformed call is no link, so a case fails by name instead of crashing
            out.append((m.group(1), c[c.index("-F") + 1].split("=", 1)[1]))
    return out


TWO = [{"op": "create", "key": "p", "title": "parent", "body_file": "b.md"},
       {"op": "create", "key": "c", "title": "child", "body_file": "b.md"}]  # p is #101, c is #102
for child, want in (("{{c}}", ("46", "ID102")), ("c", ("46", "ID102")), (43, ("46", "ID43")), ("43", ("46", "ID43"))):
    d, calls, out, refused = apply([*TWO, {"op": "subissue", "parent": 46, "child": child}], {"b.md": "B.\n"}, repo=Linker())
    check(f"a subissue child written {child!r} links that issue", refused is None and linked(calls) == [want])
for parent, want in (("{{p}}", ("101", "ID43")), ("p", ("101", "ID43")), (46, ("46", "ID43")), ("46", ("46", "ID43"))):
    d, calls, out, refused = apply([*TWO, {"op": "subissue", "parent": parent, "child": 43}], {"b.md": "B.\n"}, repo=Linker())
    check(f"a subissue parent written {parent!r} receives the link", refused is None and linked(calls) == [want])
d, calls, out, refused = apply([*TWO, {"op": "subissue", "parent": "{{p}}", "child": "{{c}}"}], {"b.md": "B.\n"}, repo=Linker())
check("both written as placeholders: the created child links under the created parent",
      refused is None and linked(calls) == [("101", "ID102")])

# A create key made only of digits is refused before the run. Bare, "43" reads as issue 43, so a
# key "43" would make a bare reference to the created issue link an unrelated existing one instead.
d, calls, out, refused = apply([{"op": "create", "key": "43", "title": "c", "body_file": "b.md"},
                                {"op": "subissue", "parent": 46, "child": "43"}], {"b.md": "B.\n"}, repo=Linker())
check("a create key of digits only is refused by name", refused_by_name(refused, 1, "key"))
check("and nothing is applied (digit key)", calls == [])

# Every spelling that would collide: an int key, a leading zero, digits in another script. And a key
# that is not a string at all is refused too -- left alone, its issue was created and the next render
# raised a TypeError on it, leaving an issue nothing recorded. The refusal names the issue number
# only where a bare reference would really resolve to one: int() reads "٤٣" as 43, and no reference
# does.
for key, names_one in (("43", True), (43, False), ("043", True), ("٤٣", False), (True, False), (43.0, False)):
    d, calls, out, refused = apply([{"op": "create", "key": key, "title": "c", "body_file": "b.md"}], {"b.md": "B.\n"})
    check(f"a create key {key!r} is refused by name before the run", refused_by_name(refused, 1, "key") and calls == [])
    check(f"the refusal of a create key {key!r} {'names' if names_one else 'names no'} issue #43",
          ("#43" in (refused or "")) == names_one)

# "Is this an issue number" has one answer everywhere: ASCII decimal digits, at most nine of them.
# isdigit() is wider -- "²" passes it and int() rejects it -- and used to crash where it disagreed;
# isdecimal(), exactly what int() reads, is wider than this rule in both directions: any script,
# any length.
d, calls, out, refused = apply([{"op": "create", "key": "²", "title": "c", "body_file": "b.md"},
                                {"op": "subissue", "parent": 46, "child": "²"}], {"b.md": "B.\n"}, repo=Linker())
check("a key int() cannot read ('²') is a name, and a bare reference reaches the created issue",
      refused is None and linked(calls) == [("46", "ID101")])
d, calls, out, refused = apply([{"op": "edit", "issue": 42, "add_labels": ["agent-ready"]},
                                {"op": "comment", "issue": 42, "body_file": "t.md"},
                                {"op": "comment", "issue": "²", "body_file": "t.md"}], {"t.md": TRIAGE})
check("an issue '²' beside an edits-first violation leaves the order refusal named, not a ValueError",
      refused is not None and refused.startswith("manifest order") and calls == [])
d, calls, out, refused = apply([{"op": "comment", "issue": "²", "body_file": "t.md"}], {"t.md": TRIAGE}, unattended=True)
check("unattended, an issue '²' is refused by name, not admitted to crash mid-run",
      refused is not None and "unattended mode refuses" in refused and calls == [])

# A digit string of another script is a number int() reads and a reader of the manifest does not:
# "٣" sent gh issue comment 3 and the fullwidth 42 sent 42, in both modes. Each is a reference
# instead, which check_refs refuses by step and field for naming no key a create step declares.
for issue in ("٣", "４２"):
    d, calls, out, refused = apply([{"op": "comment", "issue": issue, "body_file": "t.md"}], {"t.md": TRIAGE})
    check(f"an issue {issue!r} names no issue number, and nothing is sent",
          refused_by_name(refused, 1, "names no key") and calls == [])
    d, calls, out, refused = apply([{"op": "subissue", "parent": issue, "child": issue}], {})
    check(f"a subissue child and parent {issue!r} are refused the same way",
          refused_by_name(refused, 1, "names no key") and calls == [])

# A digit string past int()'s own limit ended the run on a ValueError traceback -- no step, no
# refusal -- wherever the number was read: a step's issue, a subissue's child or parent, and the
# message check_fields formats for a create key. Ten characters is already past an issue number.
LONG = "1" * 5000
for what, step, named in (
    ("a comment issue of 5000 digits", {"op": "comment", "issue": LONG, "body_file": "t.md"}, "names no key"),
    ("an edit issue of 5000 digits", {"op": "edit", "issue": LONG, "body_file": "t.md"}, "names no key"),
    ("a subissue child of 5000 digits", {"op": "subissue", "parent": 46, "child": LONG}, "names no key"),
    ("a subissue parent of 5000 digits", {"op": "subissue", "parent": LONG, "child": 46}, "names no key"),
    ("a create key of 5000 digits", {"op": "create", "key": LONG, "title": "c", "body_file": "t.md"}, "only digits"),
    ("an issue of ten characters", {"op": "comment", "issue": "0000000042", "body_file": "t.md"}, "names no key"),
):
    d, calls, out, refused = apply([step], {"t.md": TRIAGE})
    check(f"{what} is refused by name, not a traceback",
          refused_by_name(refused, 1, named) and calls == [])

# The spellings that still resolve: an int, its string, and a zero-padded one within the bound.
for issue in (42, "42", "000000042"):
    d, calls, out, refused = apply([{"op": "comment", "issue": issue, "body_file": "t.md"}], {"t.md": TRIAGE})
    check(f"an issue {issue!r} still sends 42",
          refused is None and [c[1:4] for c in calls] == [["issue", "comment", "42"]])

# The shrink check reads the body of the issue an edit replaces: an int names one whatever its
# digits, and so does a string the number test accepts.
for issue in (10 ** 30, "42"):
    d, calls, out, refused = apply([{"op": "edit", "issue": issue, "body_file": "s.md"}], {"s.md": AFTER})
    check(f"an edit whose issue is {issue!r} still has its body read",
          refused is not None and "shrinks below half" in refused
          and any(c[1:3] == ["issue", "view"] for c in calls))

# A key holding a character outside letters, digits and underscores is one no placeholder names:
# "{{c-d}}" in a field raised a KeyError mid-run, and a body's typo of it was posted as written.
for key in ("c-d", "c d", "{{c}}"):
    step = {"op": "create", "key": key, "title": "c", "body_file": "b.md"}
    d, calls, out, refused = apply([step], {"b.md": "B.\n"})
    check(f"a create key {key!r} is refused by name before the run",
          refused_by_name(refused, 1, repr(key)) and calls == [])

# One key binds one number, so two creates declaring the same key leave every reference to it -- a
# later step's, and a create body's re-rendered in the final pass -- naming the second issue. The
# refusal names both steps, so the author can tell which create to re-key.
d, calls, out, refused = apply([{"op": "create", "key": "c", "title": "one", "body_file": "a.md"},
                                {"op": "create", "key": "c", "title": "two", "body_file": "b.md"}],
                               {"a.md": "See #{{c}}.\n", "b.md": "B.\n"})
check("a create key a second create declares again is refused, naming both steps",
      refused_by_name(refused, 2, "'c'") and "step 1" in (refused or "") and calls == [])

# --- a reference to no created key ---------------------------------------------------------
# A create binds its key for every step after it. A reference to a key no earlier create step
# declares -- a typo, or a key a create declares only later -- used to fail mid-run, after the
# steps before it had applied, as a bare KeyError or render's refusal; a create body's was posted
# as written and refused after every step had. Each is refused before any gh call, by step and
# field or body file, and each follows a valid step, so "nothing applied" is tested against a step
# that would otherwise have run. The edit's body would have its issue read by the shrink check.
C = {"op": "create", "key": "c", "title": "child", "body_file": "b.md"}
REFS = {"v.md": "A note.\n", "b.md": "B.\n", "u.md": "Refers to #{{zz}}.\n",
        "f.md": "See #{{c}}.\n"}
for what, bad, field in (
    ("an issue '{{zz}}'", {"op": "comment", "issue": "{{zz}}", "body_file": "v.md"}, "issue"),
    ("an issue 'zz'", {"op": "edit", "issue": "zz", "add_labels": ["idea"]}, "issue"),
    ("a subissue child", {"op": "subissue", "parent": 46, "child": "zz"}, "child"),
    ("a subissue parent", {"op": "subissue", "parent": "{{zz}}", "child": 43}, "parent"),
    ("a comment body", {"op": "comment", "issue": 42, "body_file": "u.md"}, "u.md"),
    ("an edit body", {"op": "edit", "issue": 42, "body_file": "u.md"}, "u.md"),
    ("a close body", {"op": "close", "issue": 42, "body_file": "u.md"}, "u.md"),
    ("a create body", {**C, "body_file": "u.md"}, "u.md"),
):
    d, calls, out, refused = apply([VALID, bad], REFS)
    check(f"{what} naming no created key is refused by name before any gh call",
          refused_by_name(refused, 2, field) and "zz" in refused and calls == [])

# Neither a number nor a string names anything, and resolve_ref hands it to gh as it is: a list
# reached `gh issue comment [42]` after the step before it had posted. A bool is an int to Python.
for what, bad, field in (
    ("an issue [42]", {"op": "comment", "issue": [42], "body_file": "v.md"}, "issue"),
    ("an issue true", {"op": "edit", "issue": True, "add_labels": ["idea"]}, "issue"),
    ("a subissue child ['c']", {"op": "subissue", "parent": 46, "child": ["c"]}, "child"),
    ("a subissue parent 42.5", {"op": "subissue", "parent": 42.5, "child": 43}, "parent"),
):
    d, calls, out, refused = apply([VALID, bad], REFS)
    check(f"{what} is refused by name before any gh call",
          refused_by_name(refused, 2, field) and "neither an issue number" in refused and calls == [])

# A reference of the right type and the wrong value: GitHub resolves no issue below 1, so a call
# built from one reaches nothing -- `gh issue view -1`, `gh issue edit 0 --add-label idea`, and a
# subissue child's `gh api repos/owner/name/issues/0`, whose 404 api() only warns about.
for what, bad, field in (
    ("an issue -1", {"op": "comment", "issue": -1, "body_file": "v.md"}, "issue"),
    ("an issue '0'", {"op": "edit", "issue": "0", "add_labels": ["idea"]}, "issue"),
    ("an issue '00'", {"op": "comment", "issue": "00", "body_file": "v.md"}, "issue"),
    ("a subissue child '0'", {"op": "subissue", "parent": 46, "child": "0"}, "child"),
    ("a subissue child -1", {"op": "subissue", "parent": 46, "child": -1}, "child"),
    ("a subissue parent '00'", {"op": "subissue", "parent": "00", "child": 43}, "parent"),
):
    d, calls, out, refused = apply([VALID, bad], REFS)
    check(f"{what} is refused by name before any gh call",
          refused_by_name(refused, 2, field) and "below 1" in refused and calls == [])

# The int 0 is falsy, so the required-field line names it first, and still before any call.
for bad, field in (({"op": "edit", "issue": 0, "add_labels": ["idea"]}, "issue"),
                   ({"op": "subissue", "parent": 46, "child": 0}, "child")):
    d, calls, out, refused = apply([VALID, bad], REFS)
    check(f"{bad['op']} {field} 0 is refused by name before any gh call",
          refused_by_name(refused, 2, field) and calls == [])

# The key c is declared, but by a create after the step that names it: not bound yet there.
for what, bad, field in (
    ("an issue", {"op": "comment", "issue": "{{c}}", "body_file": "v.md"}, "issue"),
    ("a subissue child", {"op": "subissue", "parent": 46, "child": "c"}, "child"),
    ("a subissue parent", {"op": "subissue", "parent": "{{c}}", "child": 43}, "parent"),
    ("a comment body", {"op": "comment", "issue": 42, "body_file": "f.md"}, "f.md"),
    ("an edit body", {"op": "edit", "issue": 42, "body_file": "f.md"}, "f.md"),
    ("a close body", {"op": "close", "issue": 42, "body_file": "f.md"}, "f.md"),
):
    d, calls, out, refused = apply([VALID, bad, C], REFS)
    check(f"{what} naming a key a later create declares is refused by name before any gh call",
          refused_by_name(refused, 2, field) and calls == [])

d, calls, out, refused = apply([{"op": "comment", "issue": "{{c}}", "body_file": "u.md"},
                                {**C, "body_file": "u.md"},
                                {"op": "subissue", "parent": "zz", "child": "{{c}}"}], REFS)
named = ("step 1 (comment {{c}}): issue", "step 1 (comment {{c}}): body", "step 2 (create c): body",
         "step 3 (subissue): parent")
check("a manifest with several is refused once, naming every one",
      refused is not None and len(re.findall(r"step \d+ \(", refused)) == 4 and calls == []
      and all(s in refused for s in named))

# What a create binds in time still applies: a comment on the issue an earlier step created, its
# body naming that key, and a create body naming a key created after it, which the final pass fills.
d, calls, out, refused = apply([{**C, "key": "p", "body_file": "p.md"}, C,
                                {"op": "comment", "issue": "{{c}}", "body_file": "f.md"}],
                               {"p.md": "Split into #{{c}}.\n", "b.md": "B.\n",
                                "f.md": "See #{{c}}.\n"},
                               repo=Linker())  # p is #101, c is #102
check("a comment on a key an earlier step created, its body naming that key, still applies",
      refused is None and any(c[1:4] == ["issue", "comment", "102"] for c in calls)
      and (d / "rendered-f.md").read_text(encoding="utf-8") == "See #102.\n")
edits = [c for c in calls if c[1:4] == ["issue", "edit", "101"] and "--body-file" in c]
check("a create body naming a key created after it still applies, the final pass filling it in",
      refused is None and len(edits) == 1
      and (d / "rendered-p.md").read_text(encoding="utf-8") == "Split into #102.\n")

# A title is literal text: gh posts one as written, and the final pass re-renders a body, not a
# title. So a create whose title holds a placeholder is refused by step and key, which create
# declares that key -- or whether any does -- being beside the point.
for what, key, steps in (
    ("no create declares", "zz", [VALID, {**C, "title": "Part of #{{zz}}"}]),
    ("an earlier create declares", "p", [{**C, "key": "p", "title": "parent"},
                                         {**C, "title": "Part of #{{p}}"}]),
    ("a later create declares", "c", [VALID, {**C, "key": "p", "title": "Part of #{{c}}"}, C]),
):
    d, calls, out, refused = apply(steps, REFS)
    check(f"a create title naming a key {what} is refused by step and key before any gh call",
          refused_by_name(refused, 2, "title") and "{{" + key + "}}" in refused and calls == [])

d, calls, out, refused = apply([{**C, "key": "p", "title": "Part of #{{zz}}", "body_file": "u.md"},
                                {**C, "title": "Part of #{{p}} and #{{zz}}"}], REFS)
named = ("step 1 (create p): title", "step 1 (create p): body", "step 2 (create c): title")
check("several titles and a body are refused once, naming every one",
      refused is not None and all(s in refused for s in named)
      and "{{p}}, {{zz}}" in refused and calls == [])

# placeholders() matches a double-brace word, so braces that do not enclose exactly a word are
# title text.
for title in ("Not a {{ key }} here", "${{ github.event.issue.number }}", "Braces {{}} alone"):
    d, calls, out, refused = apply([{**C, "title": title}], REFS)
    check(f"a create title holding {title!r} still applies", refused is None and any(title in c for c in calls))

# The refusal offers both ways out, because a title may hold a placeholder as its subject rather
# than as a reference -- this repository's own issue titles do -- and a body naming a key no create
# declares is refused in its turn, so the body half alone leaves that author nowhere to go.
d, calls, out, refused = apply([{**C, "title": "apply-manifest: refuse {{key}} in a create title"}], REFS)
check("a title mentioning a placeholder literally is refused, naming both ways out",
      refused_by_name(refused, 1, "title") and "in the body" in refused and "without its braces" in refused)

d, calls, out, refused = apply([{**C, "title": "apply-manifest: refuse key in a create title"}], REFS)
check("and that title spelled without its braces applies",
      refused is None and any("apply-manifest: refuse key in a create title" in c for c in calls))

# A key a create declares is no dangling reference, nor is an issue number below 1: the one refusal
# heads them all as bad.
d, calls, out, refused = apply([{**C, "key": "p", "title": "parent"},
                                {**C, "title": "Part of #{{p}}"}], REFS)
check("check_refs heads its one refusal 'a bad reference, nothing applied'",
      (refused or "").startswith("a bad reference, nothing applied:"))

# --- one reference, one issue, to the order check too --------------------------------------
# Two spellings of one issue are one issue to check_order as well, or an edits-first manifest
# written with a bare key on one step and "{{key}}" on the other slips past the check it exists for.
for edit_ref, comment_ref in (("c", "{{c}}"), ("{{c}}", "c"), (42, "042")):
    steps = ([{"op": "create", "key": "c", "title": "c", "body_file": "b.md"}] if "c" in str(edit_ref) else []) + [
        {"op": "edit", "issue": edit_ref, "add_labels": ["agent-ready"]},
        {"op": "comment", "issue": comment_ref, "body_file": "t.md"}]
    d, calls, out, refused = apply(steps, {"b.md": "B.\n", "t.md": TRIAGE})
    check(f"an edits-first manifest spelling one issue {edit_ref!r} then {comment_ref!r} is refused",
          refused is not None and "Triage" in refused and not any("edit" in c for c in calls))

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}], {"b.md": AFTER})
check("a 5 KB body cut to 1 KB is refused, printing both sizes",
      refused is not None and str(len(AFTER)) in refused and str(len(BEFORE.strip())) in refused)
check("and no edit runs", not any("edit" in c for c in calls))
check("the body it would replace is kept as <issue>.body.before.md",
      (d / "42.body.before.md").is_file() and (d / "42.body.before.md").read_text(encoding="utf-8").strip() == BEFORE.strip())

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md", "allow_shrink": True}], {"b.md": AFTER})
check("with allow_shrink: true the same edit runs", refused is None and any("edit" in c for c in calls))
check("and the before-copy is still written", (d / "42.body.before.md").is_file())

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md", "allow_shrink": "false"}], {"b.md": AFTER})
check("allow_shrink must be true, not merely truthy", refused is not None and not any("edit" in c for c in calls))

# A before-file already beside the manifest is an earlier run's backup and is kept as it is: the
# applier cannot tell a shipped file from its own, so a directory must not ship one.
d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}],
                               {"b.md": BEFORE, "42.body.before.md": "the author's own text\n"})
check("a before-file already beside the manifest is kept unchanged, and the edit applies",
      refused is None and any("edit" in c for c in calls)
      and (d / "42.body.before.md").read_text(encoding="utf-8") == "the author's own text\n")

# The before-copy's path is a manifest-named path like any other: confined to the manifest
# directory, and what is already there looked at before it is kept or written, as at the rendered
# copy's path. Each shape is refused by step and name before any issue is read or step applied.
EDIT_B = [{"op": "edit", "issue": 42, "body_file": "b.md"}]


def before_dir(d):
    (d / "42.body.before.md").mkdir()


d, calls, refused = unwritable(EDIT_B, {"b.md": BEFORE}, before_dir)
check("a directory at the before-copy's path is refused by step, name and reason, nothing read",
      refused_by_name(refused, 1, "42.body.before.md") and "directory" in (refused or "") and calls == [])

escape = {}


def before_dangling(d):
    escape["target"] = d.parent / (d.name + "-before-escape.md")
    (d / "42.body.before.md").symlink_to(escape["target"])


try:
    d, calls, refused = unwritable(EDIT_B, {"b.md": BEFORE}, before_dangling)
except (OSError, NotImplementedError) as e:
    print(f"  skip: this platform would not create a symlink ({e}), so the before-copy escape is untested here")
else:
    check("a dangling symlink at the before-copy's path, pointing out of the directory, is refused by step and name",
          refused_by_name(refused, 1, "42.body.before.md") and "a symlink" in (refused or "")
          and calls == [])
    check("and nothing is written through it", not escape["target"].exists())


before_linked = {}  # its own: the suite's `linked` is rebound to a function below


def before_hard_linked(d):
    before_linked["target"] = d.parent / (d.name + "-before-outside.md")
    before_linked["target"].write_bytes(b"ORIGINAL\n")
    os.link(before_linked["target"], d / "42.body.before.md")


try:
    d, calls, refused = unwritable(EDIT_B, {"b.md": BEFORE}, before_hard_linked)
except OSError as e:
    print(f"  skip: this platform would not make a hard link ({e.strerror}), so that before-copy shape is untested here")
else:
    check("a hard link at the before-copy's path is refused by step, name and reason",
          refused_by_name(refused, 1, "42.body.before.md") and "hard link" in (refused or "") and calls == [])
    check("and the file it shares an inode with is byte for byte what it was",
          before_linked["target"].read_bytes() == b"ORIGINAL\n")

# Any link at that name is refused as a link before it is resolved, wherever it points: one inside
# the directory, dangling onto the rendered copy, would put the backup on a file render then
# overwrites, and a loop at that name resolves to no file at all.
def before_link_inside(d):
    (d / "42.body.before.md").symlink_to(d / "rendered-b.md")


def before_loop(d):
    (d / "42.body.before.md").symlink_to(d / "42.body.before.md")


for why, prepare, reason in (("pointing at the rendered copy inside the directory", before_link_inside, "a symlink"),
                            ("to itself", before_loop, "a symlink")):
    try:
        d, calls, refused = unwritable(EDIT_B, {"b.md": BEFORE}, prepare)
    except (OSError, NotImplementedError) as e:
        print(f"  skip: this platform would not create a symlink ({e}), so a before-copy link {why} is untested here")
        continue
    check(f"a symlink at the before-copy's path {why} is refused by step and name, nothing read",
          refused_by_name(refused, 1, "42.body.before.md") and reason in (refused or "") and calls == [])

if hasattr(os, "mkfifo"):
    def before_fifo(d):
        os.mkfifo(d / "42.body.before.md")

    try:
        d, calls, refused = unwritable(EDIT_B, {"b.md": BEFORE}, before_fifo)
    except OSError as e:
        print(f"  skip: this platform would not make a FIFO ({e.strerror}), so that before-copy shape is untested here")
    else:
        check("a FIFO at the before-copy's path is refused by step, name and reason",
              refused_by_name(refused, 1, "42.body.before.md") and "not a regular file" in (refused or "")
              and calls == [])
else:
    print("  skip: this platform makes no FIFO, so that before-copy shape is untested here")

d, calls, out, refused = apply(
    [{"op": "create", "key": "c", "title": "child", "body_file": "c.md"},
     {"op": "edit", "issue": "{{c}}", "body_file": "b.md"}],
    {"c.md": BEFORE, "b.md": AFTER})
check("an edit of an issue this manifest creates reads no body", refused is None and not any("body" in c for c in calls))

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}], {"b.md": ""}, dry=True)
check("a dry run still refuses an empty body file", refused is not None and "b.md" in refused)

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}], {"b.md": AFTER}, dry=True)
check("a dry run fetches no body and writes no before-copy",
      refused is None and calls == [] and "issue view" not in out and not (d / "42.body.before.md").exists())
check("and says the shrink check was skipped", "skipped" in out)

# --- the post-apply state check ----------------------------------------------------
# An intake automation reads the labels on `opened` and stamps `needs-triage` when it sees no
# state, which can land after the edit step's own read. After its last write the applier waits
# out that stamp and re-reads every issue it created or moved to a state.
CHILD = ([{"op": "create", "key": "c", "title": "child", "body_file": "b.md"},
          {"op": "comment", "issue": "{{c}}", "body_file": "t.md"},
          {"op": "edit", "issue": "{{c}}", "add_labels": ["agent-ready"]}],
         {"b.md": "The child's body.\n", "t.md": TRIAGE})

real_clock, ticks = time.monotonic, iter([100.0, 105.0])  # the create, then the re-check
time.monotonic = lambda: next(ticks)
try:
    d, calls, out, refused = apply(*CHILD, labels=["", "agent-ready\nneeds-triage"])
finally:
    time.monotonic = real_clock
removals = [c for c in calls if "--remove-label" in c]
check("a created child stamped after the edit's read gets needs-triage removed",
      refused is None and removals == [["gh", "issue", "edit", "101", "--remove-label", "needs-triage", "-R", "owner/name"]])
check("and the correction is printed", any("101" in l and "needs-triage" in l for l in out.splitlines()))
check("and the manifest's state stays", not any("agent-ready" in c for c in removals))
check("after waiting out the stamp, once, on the stubbed clock", slept == [15.0])

d, calls, out, refused = apply(
    [{"op": "comment", "issue": 42, "body_file": "t.md"},
     {"op": "edit", "issue": 42, "add_labels": ["agent-ready"]}],
    {"t.md": TRIAGE}, labels=["needs-triage", "agent-ready"])
check("a manifest with no create step never sleeps", refused is None and slept == [])
check("and still re-reads the issue it moved, a clean check saying so in one line",
      sum("labels" in c for c in calls) == 2 and out.count("more than one state label") == 1)

d, calls, out, refused = apply(
    [{"op": "comment", "issue": 42, "body_file": "t.md"},
     {"op": "edit", "issue": 42, "add_labels": ["agent-ready"]}],
    {"t.md": TRIAGE}, labels=["", "blocked\nneeds-triage"])
check("states that leave out the manifest's are reported, not changed",
      refused is None and not any("--remove-label" in c for c in calls) and "reported, not changed" in out)

real_clock, ticks = time.monotonic, iter([100.0, 105.0])  # the create, then the re-check
time.monotonic = lambda: next(ticks)
try:
    d, calls, out, refused = apply(*CHILD, labels=["", "agent-ready"])
finally:
    time.monotonic = real_clock
check("the wait is counted from the last create", refused is None and slept == [15.0])

d, calls, out, refused = apply(*CHILD, labels=["", None])
check("a failed re-read is reported and does not fail the apply",
      refused is None and "re-check of #101 failed" in out)

# The re-read goes through subprocess.run, which raises FileNotFoundError, not SystemExit, when gh
# is not on PATH. A create reads no labels, so here the re-check is the first read to meet it.
class GhGoneReads(Linker):
    """A Linker whose gh is gone for every labels read. With render_fails the deferred re-render
    fails too, as a 502 does."""

    def __init__(self, render_fails=False):
        super().__init__()
        self.render_fails = render_fails

    def run(self, cmd):
        if "labels" in cmd:
            raise FileNotFoundError(2, "No such file or directory", "gh")
        if self.render_fails and cmd[1:3] == ["issue", "edit"] and "--body-file" in cmd:
            return Failed("HTTP 502: the re-render failed")
        return super().run(cmd)


d, calls, out, refused = apply(TWO, {"b.md": "B.\n"}, repo=GhGoneReads())
check("a re-read that raises past SystemExit is reported, the next issue is still re-read, and the apply does not fail",
      refused is None and out.count("failed (every step applied)") == 2
      and sum("labels" in c for c in calls) == 2)

d, calls, out, refused = apply(
    [{"op": "create", "key": "p", "title": "parent", "body_file": "p.md"},
     {"op": "create", "key": "c", "title": "child", "body_file": "b.md"}],
    {"p.md": "Parent of #{{c}}.\n", "b.md": "B.\n"}, repo=GhGoneReads(render_fails=True))
check("and beside a deferred re-render that already failed it is printed too, the re-render staying the exit",
      refused is not None and "deferred re-render" in refused and "re-check of #101 failed" in out)

real_clock, ticks = time.monotonic, iter([100.0, 105.0])  # the create, then the re-check
time.monotonic = lambda: next(ticks)
try:
    d, calls, out, refused = apply(*CHILD, dry=True)
finally:
    time.monotonic = real_clock
check("a dry run neither sleeps nor reads", refused is None and slept == [] and calls == [] and "view" not in out)
check("and prints the wait, on the stubbed clock, and the issues it would re-read", "wait 15s" in out and "#{{c}}" in out)

# --- the edit's two calls ----------------------------------------------------------
# gh writes one edit's additions and removals as two independent halves, and a label the
# repository lacks fails only its own half. So an edit removes only labels the issue carries,
# drops trivial and checkpoint on a move off agent-ready, sends the body and the removals before
# the additions, and a failed step still gets the state re-check.
LOOP = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage", "idea", "umbrella",
        "architecture", "trivial", "checkpoint")


def without(label):
    return [l for l in LOOP if l != label]


repo = Repo(without("needs-triage"), {42: ["needs-ruling"]})
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 42, "body_file": "t.md"},
     {"op": "edit", "issue": 42, "body_file": "b.md", "add_labels": ["agent-ready"], "remove_labels": ["needs-triage"]}],
    {"t.md": TRIAGE, "b.md": BEFORE}, repo=repo)
edits = [c for c in calls if c[1:3] == ["issue", "edit"]]
check("a PROMOTE off needs-ruling, in a repository without needs-triage, ends on agent-ready alone",
      refused is None and repo.issues[42] == ["agent-ready"])
check("and no call names the needs-triage the issue does not carry, the step printing it",
      not any("needs-triage" in c for c in calls) and "needs-triage" in out)
check("the body and the removal go in the first edit, the addition alone in the second",
      len(edits) == 2 and "--body-file" in edits[0] and "needs-ruling" in edits[0] and "--add-label" not in edits[0]
      and edits[1] == ["gh", "issue", "edit", "42", "--add-label", "agent-ready", "-R", "owner/name"])

for modifier in ("trivial", "checkpoint"):
    repo = Repo(LOOP, {43: ["agent-ready", modifier]})
    d, calls, out, refused = apply(
        [{"op": "comment", "issue": 43, "body_file": "t.md"},
         {"op": "edit", "issue": 43, "add_labels": ["needs-ruling"], "remove_labels": ["agent-ready"]}],
        {"t.md": TRIAGE}, repo=repo)
    check(f"a NEEDS-RULING re-grade of agent-ready and {modifier} ends on needs-ruling alone",
          refused is None and repo.issues[43] == ["needs-ruling"])

# architecture is a state like any other: adding it removes the state the issue carried, which the
# step never declared.
repo = Repo(LOOP, {44: ["idea"]})
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 44, "body_file": "t.md"},
     {"op": "edit", "issue": 44, "add_labels": ["architecture"]}],
    {"t.md": TRIAGE}, repo=repo)
check("an edit adding architecture to an issue on idea ends on architecture alone: it is a state",
      refused is None and repo.issues[44] == ["architecture"])

repo = Repo(without("checkpoint"), {45: ["needs-triage"]})
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 45, "body_file": "t.md"},
     {"op": "edit", "issue": 45, "add_labels": ["agent-ready", "checkpoint"], "remove_labels": ["needs-triage"]}],
    {"t.md": TRIAGE}, repo=repo)
check("an addition the repository lacks exits nonzero with a message naming checkpoint",
      refused is not None and "checkpoint" in refused)
check("which says the repository may lack it and the issue now carries no state label",
      refused is not None and "may lack" in refused and "no state label" in refused)
check("and the state re-check still ran", "[check]" in out and "#45" in out)

repo = Repo(without("checkpoint"), {46: ["needs-triage"]}, stamp=101)
d, calls, out, refused = apply(
    [*CHILD[0],
     {"op": "comment", "issue": 46, "body_file": "t.md"},
     {"op": "edit", "issue": 46, "add_labels": ["agent-ready", "checkpoint"], "remove_labels": ["needs-triage"]}],
    CHILD[1], repo=repo)
check("a child stamped after its edit, then a step that fails: the child ends on agent-ready alone",
      refused is not None and repo.issues[101] == ["agent-ready"])

# --- a failed step's message -------------------------------------------------------
# gh writes an edit's body in its own goroutine beside the two label halves, so a first call that
# fails may still have landed its removals. A failed call's message names the additions it did not
# send only when there are some, and says the issue carries no state label only when no state it
# carried is left after the removals.
PROMOTE = lambda n: ([{"op": "comment", "issue": n, "body_file": "t.md"},
                      {"op": "edit", "issue": n, "body_file": "b.md", "add_labels": ["agent-ready"], "remove_labels": ["needs-triage"]}],
                     {"t.md": TRIAGE, "b.md": BEFORE})

repo = Repo(without("needs-triage"), {71: ["needs-triage"]})  # the label deleted between the read and the write
d, calls, out, refused = apply(*PROMOTE(71), repo=repo)
check("a first call whose removal the repository lost names the additions it did not send, says its removals "
      "may have landed and the issue may then carry no state label",
      refused is not None and "#71: the call setting the body and removing needs-triage failed, so the additions were not sent: agent-ready" in refused
      and "may have landed" in refused and "may then carry no state label" in refused and not any("--add-label" in c for c in calls))

repo = Repo(LOOP, {72: ["needs-triage"]}, body_fails=True)
d, calls, out, refused = apply(*PROMOTE(72), repo=repo)
check("a body write that fails beside a state removal that lands: the message says so, and the issue carries no state label",
      refused is not None and "may have landed" in refused and "may then carry no state label" in refused and repo.issues[72] == [])

repo = Repo(LOOP, {73: ["agent-ready", "needs-triage"]}, body_fails=True)
d, calls, out, refused = apply(*PROMOTE(73), repo=repo)
check("the same failure on an issue that keeps agent-ready does not say it may carry no state label",
      refused is not None and "may have landed" in refused and "no state label" not in refused and repo.issues[73] == ["agent-ready"])

repo = Repo(LOOP, {70: []}, body_fails=True)
d, calls, out, refused = apply([{"op": "edit", "issue": 70, "body_file": "b.md"}], {"b.md": BEFORE}, repo=repo)
check("a body-only edit whose call fails says the body call failed and names no additions",
      refused is not None and "#70: the call setting the body failed" in refused and "additions" not in refused)

repo = Repo(without("checkpoint"), {60: ["agent-ready", "needs-triage"]})
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 60, "body_file": "t.md"},
     {"op": "edit", "issue": 60, "add_labels": ["agent-ready", "checkpoint"], "remove_labels": ["needs-triage"]}],
    {"t.md": TRIAGE}, repo=repo)
check("an addition the repository lacks, on an issue left on agent-ready after its removals, does not say it carries no state label",
      refused is not None and "may lack" in refused and "no state label" not in refused and repo.issues[60] == ["agent-ready"])

d, calls, out, refused = apply(*PROMOTE(80), dry=True)
check("a dry run reads no issue and shows the declared removal on the body call, the addition on its own",
      refused is None and calls == [] and "--remove-label needs-triage" in out and "--add-label agent-ready" in out
      and "does not carry" not in out)

# --- the re-check after a failed step ----------------------------------------------
# A failed step's message reaches the exit whatever the re-check meets, a correction that fails
# does not stop the re-check of the next issue, and a step that raises past SystemExit still gets
# the re-check.
# The failing step must fail MID-RUN, after the child is created and moved, so the re-check has
# something to re-read -- and so where no pre-run check can see it. An unknown op, a placeholder
# naming no created key and a subissue child naming none each used to serve, and none can now:
# each is refused before any step runs. A gh call that exits nonzero can: here the labels read of
# a second edit fails, and so does the re-read of the child after it.
d, calls, out, refused = apply([*CHILD[0], {"op": "edit", "issue": 42, "add_labels": ["blocked"]}],
                               CHILD[1], labels=["", None, None])
check("a failed re-read after a failed step does not say every step applied",
      refused is not None and "FAILED: gh issue view 42" in refused
      and "re-check of #101 failed" in out and "every step applied" not in out)


def fix_then_reread(calls, fixed, reread):
    """True when a labels read of issue reread follows the first removal edit of issue fixed."""
    fixes = [i for i, c in enumerate(calls) if c[1:4] == ["issue", "edit", fixed] and "--remove-label" in c]
    reads = [i for i, c in enumerate(calls) if c[1:4] == ["issue", "view", reread] and "labels" in c]
    return bool(fixes and reads) and reads[-1] > fixes[0]


# The correction of issue 101 fails: the fake stamps a needs-triage the repository's label set lacks.
repo = Repo([l for l in LOOP if l not in ("checkpoint", "needs-triage")], {46: ["blocked"]}, stamp=101)
d, calls, out, refused = apply(
    [*CHILD[0],
     {"op": "comment", "issue": 46, "body_file": "t.md"},
     {"op": "edit", "issue": 46, "add_labels": ["agent-ready", "checkpoint"], "remove_labels": ["blocked"]}],
    CHILD[1], repo=repo)
check("a re-check correction that fails after a failed step: the run exits with the step's message, "
      "the correction's failure is printed, and the next issue is still re-read",
      refused is not None and "#46: adding agent-ready, checkpoint failed" in refused
      and "#101: removing needs-triage failed" in out and fix_then_reread(calls, "101", "46"))

repo = Repo(without("needs-triage"), {47: ["blocked"]}, stamp=101)
d, calls, out, refused = apply(
    [*CHILD[0],
     {"op": "comment", "issue": 47, "body_file": "t.md"},
     {"op": "edit", "issue": 47, "add_labels": ["agent-ready"], "remove_labels": ["blocked"]}],
    CHILD[1], repo=repo)
check("a correction that fails with every step applied: the next issue is still re-read, and the run exits nonzero naming #101",
      refused is not None and "#101" in refused and fix_then_reread(calls, "101", "47") and repo.issues[47] == ["agent-ready"])

# The correction goes through subprocess.run, which raises rather than exiting nonzero when the
# call cannot be spawned at all -- an ACL or a lock on gh, not a gh that is missing, which the
# re-read above meets first.
class CorrectionRaises(Repo):
    """A Repo whose needs-triage removal raises PermissionError. Every read, and every other write,
    goes through."""

    def run(self, cmd):
        if "--remove-label" in cmd and "needs-triage" in cmd:
            raise PermissionError(13, "Permission denied", "gh")
        return super().run(cmd)


repo = CorrectionRaises(LOOP, {47: ["blocked"]}, stamp=101)
d, calls, out, refused = apply(
    [*CHILD[0],
     {"op": "comment", "issue": 47, "body_file": "t.md"},
     {"op": "edit", "issue": 47, "add_labels": ["agent-ready"], "remove_labels": ["blocked"]}],
    CHILD[1], repo=repo)
check("a correction that raises past SystemExit is reported, the next issue is still re-read, and the exit still names #101",
      refused is not None and refused.startswith("state re-check:") and "#101: removing" in out
      and sum(c[1:4] == ["issue", "view", "47"] and "labels" in c for c in calls) == 2)

class GhGone(Repo):
    """A Repo whose gh is gone for the api calls alone: subprocess.run raises FileNotFoundError, as
    it does when gh is not on PATH, so a subissue step raises past SystemExit while the re-check
    after it still reads and corrects through the Repo."""

    def run(self, cmd):
        if cmd[1] == "api":
            raise FileNotFoundError(2, "No such file or directory", "gh")
        return super().run(cmd)


repo = GhGone(LOOP, {}, stamp=101)
d, calls, out, refused = apply([*CHILD[0], {"op": "subissue", "parent": 46, "child": 43}], CHILD[1],
                               repo=repo)
check("a step that raises an OSError after a promoted child: the re-check still runs, the child "
      "ends on agent-ready alone, and the run fails with the OSError",
      refused is not None and refused.startswith("FileNotFoundError") and "[check]" in out
      and repo.issues[101] == ["agent-ready"])


# --- the deferred re-render ----------------------------------------------------------
# A create body naming a key created after it is re-rendered once the step loop ends -- and after a
# step that fails too, with the ids bound so far, or a parent created ahead of its child keeps the
# literal placeholder on the tracker. Each deferred body is re-rendered on its own, so one that
# fails does not skip the next. The rendered file is rewritten per body file, so what a case asserts
# is what the tracker received.
class Tracker(Linker):
    """A Linker that keeps each issue's body as the tracker holds it: the text of the --body-file a
    create or an edit is sent, read as the call is made. A call fail answers true for exits 1, as gh
    does on a 502; with gone, it raises FileNotFoundError instead, as when gh is not on PATH."""

    def __init__(self, fail=lambda a: False, gone=False, issues=None):
        super().__init__()
        self.bodies, self.fail, self.gone = {}, fail, gone
        self.issues = issues or {}  # the labels an issue carries, for a removal's read

    def run(self, cmd):
        a = cmd[1:-2]  # less gh and -R owner/name
        if self.fail(a):  # before the reads below: a case may fail any call, a labels read included
            if self.gone:
                raise FileNotFoundError(2, "No such file or directory", "gh")
            return Failed(f"HTTP 502: {' '.join(a[:3])} failed")
        if a[:2] == ["issue", "view"] and "labels" in a:
            return Ran("\n".join(self.issues.get(int(a[2]), [])))
        r = super().run(cmd)
        if a[:1] == ["issue"] and "--body-file" in a:
            n = self.next - 1 if a[1] == "create" else int(a[2])
            self.bodies[n] = pathlib.Path(a[a.index("--body-file") + 1]).read_text(encoding="utf-8")
        return r


def made(key, title, body_file):
    return {"op": "create", "key": key, "title": title, "body_file": body_file}


KIN = {"p.md": "Parent of #{{c}}.\n", "q.md": "Sibling of #{{c}}.\n", "b.md": "B.\n", "v.md": "A note.\n"}
NOTE = {"op": "comment", "issue": 42, "body_file": "v.md"}
fails = lambda *calls: (lambda a: a[:3] in [c.split() for c in calls])


def once(pred):
    """A call that fails the first time only, as a 502 does, so a later call sending the same
    thing goes through -- which is how the final pass gets to re-send a body the step lost."""
    fired = []
    def fail(a):
        if pred(a) and not fired:
            fired.append(1)
            return True
        return False
    return fail

repo = Tracker(fail=fails("issue comment 42"))
d, calls, out, refused = apply([made("p", "parent", "p.md"), made("c", "child", "b.md"), NOTE], KIN, repo=repo)
check("a step whose gh call fails after a parent and its child are created: the parent's body on the "
      "tracker carries the child's number, and the run exits with the step's own failure",
      refused is not None and refused.startswith("FAILED: gh issue comment 42")
      and repo.bodies.get(101) == "Parent of #102.\n")

repo = Tracker(fail=lambda a: a[:2] == ["issue", "create"] and "late" in a)
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("q", "sibling", "q.md"), made("a", "early", "b.md"), made("b", "late", "b.md")],
    {"p.md": "Children: #{{a}} and #{{b}}.\n", "q.md": "Then #{{b}}, after #{{a}}.\n", "b.md": "B.\n"},
    repo=repo)  # p is #101, q #102, a #103; b's create fails
lines = out.splitlines()
check("a create whose gh call fails: the other keys in each deferred body naming it are still substituted",
      refused is not None and refused.startswith("FAILED: gh issue create")
      and repo.bodies.get(101) == "Children: #103 and #{{b}}.\n" and repo.bodies.get(102) == "Then #{{b}}, after #103.\n")
check("and each of those bodies is reported by issue and by the key whose create never ran",
      all(any(f"#{n}" in l and "{{b}}" in l for l in lines) for n in (101, 102)))
check("and the report names no key it did substitute",
      not any("{{a}}" in l for l in lines))

# An edit step's body is the issue's, so the create's is stale: re-rendering it would revert the
# correction the manifest itself applied, and the final pass runs after a failed step now.
repo = Tracker(fail=fails("issue comment 42"))
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "body_file": "p2.md"}, NOTE],
    {**KIN, "p2.md": "Parent of #{{c}}, and of every sibling the split turns up later.\n"}, repo=repo)
check("a body a later edit step replaced is not re-rendered over",
      refused is not None and repo.bodies.get(101) == "Parent of #102, and of every sibling the split turns up later.\n"
      and len([c for c in calls if c[1:4] == ["issue", "edit", "101"] and "--body-file" in c]) == 1)

# The edit's own call is what makes its body the issue's, and a failed one leaves that body to the
# final pass rather than the create's. Only the edit's call fails here: the re-render's carries the
# same first three arguments, and is told apart by the file it sends.
repo = Tracker(fail=once(lambda a: a[:3] == ["issue", "edit", "101"] and "p2.md" in a[-1]))
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "body_file": "p2.md"}],
    {**KIN, "p2.md": "Parent of #{{c}}, and of every sibling the split turns up later.\n"}, repo=repo)
check("a body whose edit step failed is re-rendered, as the edit meant it",
      refused is not None and "#101: the call setting the body failed" in refused
      and repo.bodies.get(101) == "Parent of #102, and of every sibling the split turns up later.\n")

# A labels-only edit names no body, so the create's is still the issue's and still re-rendered.
# It carries a removal the issue does carry, so the edit call is made: an edit that sends nothing
# would not reach the drop at all, and the case would pass whatever the drop does.
repo = Tracker(fail=fails("issue comment 42"), issues={101: ["blocked"]})
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "add_labels": ["idea"], "remove_labels": ["blocked"]}, NOTE], KIN, repo=repo)
check("a labels-only edit leaves the deferred body to the final pass",
      refused is not None and repo.bodies.get(101) == "Parent of #102.\n"
      and any(c[1:4] == ["issue", "edit", "101"] and "--remove-label" in c for c in calls))

# gh writes the body beside the removal half, so a call that raises may have landed either. The
# issue's body is meant to be the edit's, so that is what the pass sends -- not the create's,
# which would revert a body that did land, and not nothing, which would leave a placeholder up.
repo = Tracker(fail=lambda a: a[:3] == ["issue", "edit", "101"] and "--remove-label" in a, issues={101: ["blocked"]})
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "body_file": "p2.md", "remove_labels": ["blocked"]}],
    {**KIN, "p2.md": "Parent of #{{c}}, and of every sibling the split turns up later.\n"}, repo=repo)
check("an edit whose own call failed leaves its body, not the create's, to the final pass",
      refused is not None and "#101: the call setting the body" in refused
      and repo.bodies.get(101) == "Parent of #102, and of every sibling the split turns up later.\n")

# The same failure with no body to send: an edit that only removes a label names no body file, so
# there is nothing to re-point to and the create's body is still the issue's.
repo = Tracker(fail=once(fails("issue edit 101")), issues={101: ["blocked"]})
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "remove_labels": ["blocked"]}], KIN, repo=repo)
check("a failed edit that sent no body leaves the create's body to the final pass",
      refused is not None and refused.startswith("#101: the call removing blocked failed")
      and repo.bodies.get(101) == "Parent of #102.\n")

# And only that issue's: a second deferred body must keep its own, or one failed edit publishes
# its body onto every issue the manifest created.
repo = Tracker(fail=lambda a: a[:3] == ["issue", "edit", "101"] and "--remove-label" in a, issues={101: ["blocked"]})
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("q", "sibling", "q.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "body_file": "p2.md", "remove_labels": ["blocked"]}],
    {**KIN, "p2.md": "Parent of #{{c}}, and of every sibling the split turns up later.\n"}, repo=repo)
check("and a second deferred body keeps its own",  # p is #101, q #102, c #103
      refused is not None
      and repo.bodies.get(101) == "Parent of #103, and of every sibling the split turns up later.\n"
      and repo.bodies.get(102) == "Sibling of #103.\n")

# A dry run makes no issue, so every create binds 0 -- the number an edit's issue resolves to as
# well. Dropping by that number would drop every deferred body, and the preview would show none.
d, calls, out, refused = apply(
    [made("p", "parent", "p.md"), made("q", "sibling", "q.md"), made("c", "child", "b.md"),
     {"op": "edit", "issue": "{{p}}", "body_file": "p2.md"}],
    {**KIN, "p2.md": "Parent of #{{c}}, and of every sibling the split turns up later.\n"}, dry=True)
check("a dry run previews the re-render of every deferred body",
      refused is None and len([l for l in out.splitlines() if l.startswith("[final]")]) == 2)

repo = Tracker(fail=fails("issue edit 101", "issue view 101"))
d, calls, out, refused = apply([made("p", "parent", "p.md"), made("c", "child", "b.md")], KIN, repo=repo)
check("a re-render that fails with every step applied is not a step that failed, to the re-check's report",
      refused is not None and any("#101" in l and "every step applied" in l for l in out.splitlines()))

for gone in (False, True):
    how = "raises an OSError" if gone else "fails"
    repo = Tracker(fail=fails("issue edit 101"), gone=gone)
    d, calls, out, refused = apply([made("p", "parent", "p.md"), made("q", "sibling", "q.md"), made("c", "child", "b.md")],
                                   KIN, repo=repo)  # p is #101, q #102, c #103
    check(f"every step applied and the first deferred re-render {how}: the second is still re-rendered",
          repo.bodies.get(102) == "Sibling of #103.\n")
    check(f"and the run exits nonzero naming the issue it left, and only that one ({how})",
          refused is not None and "#101" in refused and "#102" not in refused)

repo = Tracker(fail=fails("issue comment 42", "issue edit 101"))
d, calls, out, refused = apply([made("p", "parent", "p.md"), made("q", "sibling", "q.md"), made("c", "child", "b.md"), NOTE],
                               KIN, repo=repo)
check("after a failed step, a deferred re-render that fails is reported and the next is still re-rendered, "
      "the run exiting with the step's own failure",
      refused is not None and refused.startswith("FAILED: gh issue comment 42")
      and any("#101" in l and "failed" in l for l in out.splitlines()) and repo.bodies.get(102) == "Sibling of #103.\n")

# --- a create that answers no issue number -------------------------------------------
# gh answers a create with the new issue's URL, and the step has nothing to bind when that
# answer carries no number in it. Binding 0 there sends every later call and render naming that
# key to issue 0, so the step fails instead.
class Mute(Tracker):
    """A Tracker whose `at`-th create answers `says` in place of the issue URL."""

    def __init__(self, says, at=1):
        super().__init__()
        self.says, self.at, self.creates = says, at, 0

    def run(self, cmd):
        r = super().run(cmd)
        if cmd[1:3] == ["issue", "create"]:
            self.creates += 1
            if self.creates == self.at:
                return Ran(self.says)
        return r


repo = Mute("")
d, calls, out, refused = apply([made("p", "parent", "b.md"), NOTE], KIN, repo=repo)
check("a create whose gh call answers no URL fails that step, naming it and the issue gh may have made",
      refused is not None and "step 1 (create p)" in refused and "may exist" in refused)
check("and no step after it is applied", [c[1:3] for c in calls] == [["issue", "create"]])

repo = Mute("https://github.com/owner/name/issues/new")
d, calls, out, refused = apply([made("p", "parent", "b.md"), NOTE], KIN, repo=repo)
check("a create whose URL ends in no number fails that step by name too",
      refused is not None and "step 1 (create p)" in refused and [c[1:3] for c in calls] == [["issue", "create"]])

# isdecimal() reads any script's digits, and int() would too -- a tail of them is not an issue
# number, so the same "no issue number" refusal applies, not a bind to a real-looking count.
repo = Mute("https://github.com/owner/name/issues/٤٢")
d, calls, out, refused = apply([made("p", "parent", "b.md"), NOTE], KIN, repo=repo)
check("a create whose URL ends in another script's digits fails that step too, as no number",
      refused is not None and "step 1 (create p)" in refused and [c[1:3] for c in calls] == [["issue", "create"]])

# The step before it created a real issue whose body defers to the key this one never bound, so
# the deferred pass and the state re-check have an issue to name after the failure. The failing
# create's own body would defer too, so a regression that bound 0 would send `gh issue edit 0`
# for it -- which the first assertion refuses.
repo = Mute("", at=2)
d, calls, out, refused = apply([made("p", "parent", "p.md"), made("c", "child", "r.md"), NOTE],
                              {**KIN, "r.md": "Child of #{{p}}.\n"}, repo=repo)
check("a later create that answers no URL: no call names issue 0",
      refused is not None and not any(c[1:4] == ["issue", "edit", "0"] for c in calls))
check("and the deferred pass re-renders the earlier body, which still names the unbound key",
      repo.bodies.get(101) == "Parent of #{{c}}.\n" and any("#101" in l and "{{c}}" in l for l in out.splitlines()))
check("and the state re-check reads the issue that was created, naming no key",
      any(l.startswith("[check]") and "#101" in l and "{{" not in l for l in out.splitlines()))

# A body is deferred for the keys its placeholders name, not for a brace pair: quoted braces with
# no key between them resolve to nothing, so re-sending that body would send the same text twice.
repo = Tracker()
d, calls, out, refused = apply([made("p", "parent", "p.md")], {"p.md": "Write `{{ }}` for a literal.\n"}, repo=repo)
check("a create body whose braces name no key is not deferred",
      refused is None and not any(c[1:3] == ["issue", "edit"] for c in calls)
      and not any(l.startswith("[final]") for l in out.splitlines()))

# --- label case --------------------------------------------------------------------
# gh matches label names case-insensitively, so a state spelled Agent-Ready is a state everywhere.
d = manifest_dir(
    [{"op": "edit", "issue": 50, "add_labels": ["Agent-Ready"]},
     {"op": "comment", "issue": 50, "body_file": "c.md"}],
    {"c.md": TRIAGE})
try:
    applier.check_order(d, json.loads((d / "manifest.json").read_text(encoding="utf-8")))
    check("an Agent-Ready edit before its comment is refused", False)
except SystemExit as e:
    check("an Agent-Ready edit before its comment is refused", "issue 50" in str(e))

repo = Repo(LOOP, {50: ["needs-triage"]}, stamp=50)
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 50, "body_file": "t.md"},
     {"op": "edit", "issue": 50, "add_labels": ["Agent-Ready"]}],
    {"t.md": TRIAGE}, repo=repo)
first_edit = next((i for i, c in enumerate(calls) if c[1:3] == ["issue", "edit"]), len(calls))
check("an Agent-Ready addition reads the labels first, is re-checked past a late needs-triage, and ends on agent-ready alone",
      refused is None and any(c[1:3] == ["issue", "view"] and "labels" in c for c in calls[:first_edit])
      and "kept agent-ready" in out and repo.issues[50] == ["agent-ready"])

# --- unattended mode ---------------------------------------------------------------
# An unattended intake hands the applier a manifest a model wrote from untrusted issue text, so
# what the applier accepts is the security boundary. It takes the intake's two ops and its two
# labels and nothing else, rewrites no body, bounds the comment it posts, and sends exactly the
# removals a step declares: synthesizing more lets a manifest naming only permitted labels strip a
# promotion off any issue it names. (Body-file confinement is every run's rule, not this mode's --
# see the manifest's own files, below.)
# Everything that would otherwise raise mid-run, with earlier steps already posted, is refused up
# front too -- "before any step runs" has to hold for the whole mode, not just for the label rules.
GRADED = {"t.md": TRIAGE, "b.md": BEFORE, "p.md": TRIAGE + "See #{{child}} for the split.\n",
          "u16.md": UTF16}
for what, steps, cause in (
    ("a create step", [{"op": "create", "key": "c", "title": "child", "body_file": "b.md"}], "op create"),
    ("a close step", [{"op": "close", "issue": 42, "body_file": "b.md"}], "op close"),
    ("a subissue step", [{"op": "subissue", "parent": 42, "child": 43}], "op subissue"),
    ("an edit carrying a body file",
     [{"op": "edit", "issue": 42, "body_file": "b.md", "add_labels": ["needs-ruling"]}], "never rewrites a body"),
    ("a comment step with no body file", [{"op": "comment", "issue": 42}], "no body_file"),
    ("a comment body carrying a placeholder",
     [{"op": "comment", "issue": 42, "body_file": "p.md"}], "placeholder"),
    ("an issue that is not a number",
     [{"op": "comment", "issue": "{{c}}", "body_file": "t.md"}], "not an issue number"),
    ("an issue true, which Python counts as an int",
     [{"op": "comment", "issue": True, "body_file": "t.md"}], "not an issue number"),
    ("an issue spelled in another script's digits",
     [{"op": "comment", "issue": "٣", "body_file": "t.md"}], "not an issue number"),
    ("an issue spelled in fullwidth digits",
     [{"op": "comment", "issue": "４２", "body_file": "t.md"}], "not an issue number"),
    ("an issue of 5000 digits",
     [{"op": "comment", "issue": "1" * 5000, "body_file": "t.md"}], "not an issue number"),
    ("an integer issue of 31 digits",
     [{"op": "comment", "issue": 10 ** 30, "body_file": "t.md"}], "not an issue number"),
    ("a body file that is not there",
     [{"op": "comment", "issue": 42, "body_file": "gone.md"}], "cannot be read"),
    ("a body file that is not UTF-8 text",
     [{"op": "comment", "issue": 42, "body_file": "u16.md"}], "cannot be read"),
):
    d, calls, out, refused = apply(steps, GRADED, unattended=True)
    check(f"unattended: {what} is refused, naming the step and the cause",
          refused is not None and "step 1" in refused and cause in refused)
    check(f"and {what} applies nothing", calls == [])

# Both of those are this mode's own refusal: it opens a comment body before check_fields types it,
# so the pre-flight read every run makes never sees the file. The file is named by its strerror, an
# OSError's own message carrying the absolute path of the manifest directory.
for name in ("gone.md", "u16.md"):
    d, calls, out, refused = apply([{"op": "comment", "issue": 42, "body_file": name}],
                                   GRADED, unattended=True)
    check(f"unattended: a comment body {name} is refused by this mode's own list, named not pathed",
          "unattended mode refuses" in (refused or "") and name in (refused or "")
          and d.name not in refused and calls == [])

# A comment body is the only one this mode ever opens: a create step, and an edit carrying a body
# file, are refused by the mode's own list first, whether or not that body can be read.
for what, step, cause in (
    ("a create", {"op": "create", "key": "c", "title": "child", "body_file": "u16.md"}, "op create"),
    ("an edit", {"op": "edit", "issue": 42, "body_file": "gone.md", "add_labels": ["needs-ruling"]},
     "never rewrites a body"),
):
    d, calls, out, refused = apply([step], GRADED, unattended=True)
    check(f"unattended: {what} naming an unreadable body is refused before that body is opened",
          "unattended mode refuses" in (refused or "") and cause in refused
          and "cannot be read" not in refused and calls == [])

# A reference below 1 is on this mode's own list too, which runs before every other check: the
# refusal is check_unattended's, not the one check_refs would raise a moment later.
for value in (-1, -999999999, "0", "00"):
    d, calls, out, refused = apply([{"op": "comment", "issue": value, "body_file": "t.md"}],
                                   GRADED, unattended=True)
    check(f"unattended: an issue {value!r} is refused by this mode's own list",
          refused_by_name(refused, 1, "issue") and "below 1" in (refused or "")
          and "unattended mode refuses" in (refused or "") and calls == [])

# Every label but the intake's two, added or removed. An allowlist widened by one entry survives a
# case that only ever names agent-ready and trivial.
# The forbidden set is spelled out here rather than read from the module: a case that derives its
# expectation from the constant under test cannot notice that constant growing an entry.
for label in [l for l in LOOP if l not in ("needs-triage", "needs-ruling")]:
    for side, field in (("added", "add_labels"), ("removed", "remove_labels")):
        d, calls, out, refused = apply([{"op": "edit", "issue": 42, field: [label]}], GRADED, unattended=True)
        check(f"unattended: {side} {label} is refused",
              refused is not None and f"label {label} " in refused and calls == [])

# gh matches label names case-insensitively, so the allowlist must too -- in both directions.
repo = Repo(LOOP, {58: ["needs-triage"]})
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 58, "body_file": "t.md"},
     {"op": "edit", "issue": 58, "add_labels": ["Needs-Ruling"], "remove_labels": ["Needs-Triage"]}],
    {"t.md": TRIAGE}, unattended=True, repo=repo)
check("unattended: a permitted label spelled Needs-Ruling is permitted, as gh matches it",
      refused is None and repo.issues[58] == ["needs-ruling"])

d, calls, out, refused = apply([{"op": "edit", "issue": 42, "add_labels": ["Agent-Ready"]}], GRADED, unattended=True)
check("and a forbidden one spelled Agent-Ready is still refused",
      refused is not None and "Agent-Ready" in refused and calls == [])

# --- the post-apply correction, unattended -----------------------------------------
# Ruled: unattended it may clear only the intake's own two labels. It still clears the late stamp it
# exists for, but the manifest steers it by choosing the issue and the state that survives, so a
# promotion or a human-ready beside that state is reported and left to the label-invariants gate.
SWAP = ([{"op": "comment", "issue": 55, "body_file": "t.md"},
         {"op": "edit", "issue": 55, "add_labels": ["needs-ruling"], "remove_labels": ["needs-triage"]}],
        {"t.md": TRIAGE})

repo = Repo(LOOP, {55: ["agent-ready", "trivial", "needs-triage"]})
d, calls, out, refused = apply(*SWAP, unattended=True, repo=repo)
edits = [c for c in calls if c[1:3] == ["issue", "edit"]]
check("unattended: the swap sends exactly the removal it declared",
      refused is None
      and edits == [["gh", "issue", "edit", "55", "--remove-label", "needs-triage", "-R", "owner/name"],
                    ["gh", "issue", "edit", "55", "--add-label", "needs-ruling", "-R", "owner/name"]])
check("and the agent-ready and trivial it never declared are still on the issue when the run ends",
      repo.issues[55] == ["agent-ready", "trivial", "needs-ruling"])
check("the re-check naming what it may not remove", "may not remove" in out and "agent-ready" in out)

repo = Repo(LOOP, {77: ["human-ready"]})
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 77, "body_file": "t.md"},
     {"op": "edit", "issue": 77, "add_labels": ["needs-triage"]}],
    {"t.md": TRIAGE}, unattended=True, repo=repo)
check("unattended: stamping needs-triage on an issue carrying human-ready strips nothing",
      refused is None and repo.issues[77] == ["human-ready", "needs-triage"]
      and not any("--remove-label" in c for c in calls))

repo = Repo(LOOP, {66: []}, stamp=66)
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 66, "body_file": "t.md"},
     {"op": "edit", "issue": 66, "add_labels": ["needs-ruling"]}],
    {"t.md": TRIAGE}, unattended=True, repo=repo)
check("unattended: the needs-triage an automation stamps after the edit's read is still removed, "
      "which is what the re-check exists for",
      refused is None and repo.issues[66] == ["needs-ruling"]
      and any("--remove-label" in c and "needs-triage" in c for c in calls))

repo = Repo(LOOP, {55: ["agent-ready", "trivial", "needs-triage"]})
d, calls, out, refused = apply(*SWAP, repo=repo)
sent = next((c for c in calls if "--remove-label" in c), [])
check("without the flag that same step still removes agent-ready, trivial and needs-triage",
      refused is None and [sent[i + 1] for i, x in enumerate(sent) if x == "--remove-label"]
      == ["agent-ready", "trivial", "needs-triage"] and repo.issues[55] == ["needs-ruling"])

# --- the manifest's own files ------------------------------------------------------
# A manifest names its own body files, so a `..` segment, an absolute path or a symlink is a read
# of any file the session can reach -- and what render reads is what gets posted. The confinement
# is every run's, not the unattended mode's: a manifest is model-written in both (a verifier
# subagent drafts the interactive one too), and the approval covers the verdicts, not the paths.
for name in ("../secret.md", "../../secret.md", "sub/../../secret.md"):
    d, calls, out, refused = apply([{"op": "comment", "issue": 9, "body_file": name}], {}, unattended=True)
    check(f"unattended: a body file at {name} is refused, naming it",
          refused is not None and "outside the manifest directory" in refused and calls == [])

esc = pathlib.Path(tempfile.mkdtemp(prefix="apply-manifest-escape-", dir=PARENT_TMP))
(esc / "outside.md").write_text(TRIAGE, encoding="utf-8")   # a real file, really outside
inner = esc / "m"
inner.mkdir()

# An absolute body_file needs no `..` at all.
d, calls, out, refused = apply(
    [{"op": "comment", "issue": 9, "body_file": str(esc / "outside.md")}], {}, unattended=True)
check("unattended: an absolute body file outside the manifest directory is refused",
      refused is not None and "outside the manifest directory" in refused and calls == [])

# Without the flag, every shape is refused just the same, and by the pre-flight, so nothing
# outside the directory is read. Each of these spells a separator, so check_fields names it a
# body file that is not a bare name before manifest_file is reached at all; what answers for the
# shapes that do reach it is the confinement, below. REL_OUT names a file that really is there: a
# manifest directory is a mkdtemp under the same parent as esc, so none of these is refused for
# being missing.
REL_OUT = "../" + esc.name + "/outside.md"
for name in (REL_OUT, "../secret.md", str(esc / "outside.md")):
    d, calls, out, refused = apply([{"op": "comment", "issue": 9, "body_file": name}], {})
    check(f"without the flag a comment body at {name} is refused too",
          refused_by_name(refused, 1, name) and BARE in (refused or "") and calls == [])

# An edit's body file is read by neither check_order nor the unattended pre-flight, and is
# refused just the same.
d, calls, out, refused = apply([{"op": "edit", "issue": 9, "body_file": "../secret.md"}], {})
check("without the flag an edit's body file outside the manifest directory is refused too",
      refused_by_name(refused, 1, "../secret.md") and BARE in (refused or "") and calls == [])
applier.UNATTENDED = False  # render is called directly below: the confinement must not need the flag


def render_refuses(what, name):
    """render must REFUSE, naming the confinement. Anything else -- a return, or the OSError of a
    write it went ahead and attempted -- is a failure, not a pass by accident."""
    try:
        applier.render(inner, name, {})
        check(what, False)
    except SystemExit as e:
        check(what, "outside the manifest directory" in str(e))
    except Exception as e:
        check(f"{what} -- it raised {type(e).__name__} instead of refusing", False)


render_refuses("render refuses a body file outside the manifest directory, in either mode", "../outside.md")
render_refuses("render refuses an absolute body file outside the manifest directory, in either mode",
               str(esc / "outside.md"))

# --- body files with a byte-order mark ----------------------------------------------
# The same Windows PowerShell 5.1 mark the manifest row above reads, at the body files: render
# reads them as utf-8-sig and drops a repeated mark, so the rendered copy, and the body gh posts
# from it, begins with the text. The normaliser dropped the mark on the marker path before this;
# the shape it posts and the line it prints for a verdict on the marker line are unchanged, and a
# body with no mark renders unchanged. These rows fold CRLF before comparing, since the mark is
# what they are after; the row below them compares the bytes whole.
for kind in ("create", "comment", "close", "edit"):
    (inner / f"bom-{kind}.md").write_bytes(b"\xef\xbb\xbf# A " + kind.encode("ascii") + b" body\n")
    rendered = pathlib.Path(applier.render(inner, f"bom-{kind}.md", {}))
    check(f"a {kind} body written with a byte-order mark renders to the text alone",
          rendered.read_bytes().replace(b"\r\n", b"\n") == b"# A " + kind.encode("ascii") + b" body\n")
(inner / "bom-twice.md").write_bytes(b"\xef\xbb\xbf\xef\xbb\xbf# Two marks\n")
check("a body written with two byte-order marks renders to the text alone",
      pathlib.Path(applier.render(inner, "bom-twice.md", {})).read_bytes().replace(b"\r\n", b"\n") == b"# Two marks\n")
(inner / "bom-triage.md").write_bytes(b"\xef\xbb\xbf**Triage** PROMOTE\n")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rendered = pathlib.Path(applier.render(inner, "bom-triage.md", {}, triage=True))
check("a triage body with the mark still posts the marker alone on line 1",
      rendered.read_bytes().replace(b"\r\n", b"\n") == b"**Triage**\n\nPROMOTE\n")
check("the normaliser still prints its line for a verdict on the marker line",
      buf.getvalue() == "  normalized the **Triage** marker line of bom-triage.md\n")
(inner / "bom-none.md").write_bytes(b"# No mark\n")
check("a body with no mark renders unchanged",
      pathlib.Path(applier.render(inner, "bom-none.md", {})).read_bytes().replace(b"\r\n", b"\n") == b"# No mark\n")

# The rendered copy is what gh posts as the body: written with LF alone, on every platform, not
# folded to compare, so a Windows run of this row would have caught write_text's translation.
(inner / "no-bom.md").write_bytes(b"# A body\nwith two lines\n")
check("render writes the copy with LF only, not the platform's line ending",
      pathlib.Path(applier.render(inner, "no-bom.md", {})).read_bytes() == b"# A body\nwith two lines\n")

try:
    (inner / "link.md").symlink_to(esc / "outside.md")
    linked = True
except (OSError, NotImplementedError):
    linked = False
if linked:
    render_refuses("render refuses a symlink pointing out of the manifest directory", "link.md")
else:
    print("  skip: this platform would not create a symlink, so that escape is untested here")

# A loop among the links resolves to no file at all, where the shapes above resolve to one outside
# the directory. Where resolve() answers the loop with a RuntimeError, or the strict resolve
# raises winerror 1921 (Windows), manifest_file raises the ELOOP itself; where it leaves the loop
# to the read, the read raises ELOOP. So a loop INSIDE
# the directory -- what these plant -- ends in the same refusal on either shape: by step and name,
# carrying the strerror and never the absolute path an OSError has. Interactively that refusal is
# check_readable's; unattended it is the one check_unattended makes of the comment body it opens.
# gh is stubbed with a recorder, so the rows read the calls rather than assume them.
loop = manifest_dir([{"op": "comment", "issue": 9, "body_file": "loop.md"}], {})
try:
    (loop / "loop.md").symlink_to(loop / "loop2.md")
    (loop / "loop2.md").symlink_to(loop / "loop.md")
    looped = True
except (OSError, NotImplementedError):
    looped = False
if looped:
    calls = []

    def record(cmd, **k):  # every gh call is one too many: the refusal precedes the first
        calls.append(cmd)
        return Ran("")

    for mode in (False, True):
        calls.clear()
        saved_loop = (sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, applier.UNATTENDED)
        applier.DRY, applier.REPO, applier.UNATTENDED = False, "owner/name", mode
        applier.subprocess.run = record
        sys.argv = ["apply-manifest.py", str(loop), "--repo", "owner/name", "--no-forbidden-check"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                applier.main()
            refused = None
        except SystemExit as e:
            refused = str(e)
        except Exception as e:
            refused = f"{type(e).__name__}: {e}"
        finally:
            sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, applier.UNATTENDED = saved_loop
        check("a symlink loop among the body files is refused by step, name and strerror"
              f"{' (unattended)' if mode else ''}, not a traceback",
              refused_by_name(refused, 1, "loop.md") and "symbolic links" in (refused or "")
              and str(loop) not in (refused or "") and calls == [])
else:
    print("  skip: this platform would not create a symlink, so the loop is untested here")

# The rows above need a symlink. This one needs none: resolve() is made to raise what pathlib
# raises for a loop, so the strerror manifest_file gives it is read on every platform.
eloop = manifest_dir([{"op": "comment", "issue": 9, "body_file": "loop.md"}], {"loop.md": TRIAGE})
saved_resolve = pathlib.Path.resolve


def looping_resolve(self, strict=False):
    if self.name == "loop.md":
        raise RuntimeError("Symlink loop from %r" % str(self))
    return saved_resolve(self, strict=strict)


pathlib.Path.resolve = looping_resolve
try:
    applier.check_readable(eloop, json.loads((eloop / "manifest.json").read_text(encoding="utf-8")))
    refused = None
except SystemExit as e:
    refused = str(e)
finally:
    pathlib.Path.resolve = saved_resolve
check("the loop's refusal reads the same strerror on every platform",
      refused_by_name(refused, 1, "loop.md") and "Too many levels of symbolic links" in (refused or ""))


# The Windows shape since Python 3.13: the non-strict resolve returns a path, and only the strict
# one raises, as OSError errno 22 carrying winerror 1921. winerror is assigned, not passed, so the
# row reads the same on every platform.
def winloop_resolve(self, strict=False):
    if self.name == "loop.md" and strict:
        e = OSError(22, "Invalid argument")
        e.winerror = 1921
        raise e
    return saved_resolve(self, strict=strict)


pathlib.Path.resolve = winloop_resolve
try:
    applier.check_readable(eloop, json.loads((eloop / "manifest.json").read_text(encoding="utf-8")))
    refused = None
except SystemExit as e:
    refused = str(e)
finally:
    pathlib.Path.resolve = saved_resolve
check("a loop the strict resolve names by winerror 1921 is refused as a loop, not read",
      refused_by_name(refused, 1, "loop.md") and "Too many levels of symbolic links" in (refused or ""))


# Any other OSError the non-strict resolve raises -- a junction to a file gives errno 20 on
# Windows -- is the read's refusal by name and strerror, as before the strict resolve existed.
def notdir_resolve(self, strict=False):
    if self.name == "loop.md":
        raise OSError(20, "The directory name is invalid")
    return saved_resolve(self, strict=strict)


pathlib.Path.resolve = notdir_resolve
try:
    applier.check_readable(eloop, json.loads((eloop / "manifest.json").read_text(encoding="utf-8")))
    refused = None
except SystemExit as e:
    refused = str(e)
except Exception as e:
    refused = f"{type(e).__name__}: {e}"
finally:
    pathlib.Path.resolve = saved_resolve
check("any other OSError resolving a body file is refused by name and strerror, not a traceback",
      refused_by_name(refused, 1, "loop.md") and "The directory name is invalid" in (refused or ""))

# A real loop needing no symlink privilege: two directory junctions pointing at each other, so
# the Windows CI account runs this row too. manifest_file is asked, from the directory holding
# the loop, for a file inside it.
if sys.platform == "win32":
    import _winapi
    jbase = pathlib.Path(tempfile.mkdtemp(dir=PARENT_TMP))
    ja, jb = jbase / "a", jbase / "b"
    try:
        ja.mkdir()
        _winapi.CreateJunction(str(ja), str(jb))
        ja.rmdir()
        _winapi.CreateJunction(str(jb), str(ja))
        applier.manifest_file(jbase, "a/loop.md")
        raised = None
    except OSError as e:
        raised = e.strerror
    finally:
        for j in (ja, jb):
            if os.path.lexists(j):
                os.rmdir(j)
    check("a loop of two directory junctions is refused as a loop", raised == "Too many levels of symbolic links")
else:
    print("  skip: directory junctions are Windows-only, so the junction loop is untested here")


# The manifest directory itself a link loop: main reads manifest.json before any manifest_file
# call, and Windows reads a junction loop as EINVAL. A junction loop on Windows, which needs no
# symlink privilege, and a symlink loop elsewhere.
lbase = pathlib.Path(tempfile.mkdtemp(dir=PARENT_TMP))
la, lb = lbase / "a", lbase / "b"
if sys.platform == "win32":
    import _winapi
    la.mkdir()
    _winapi.CreateJunction(str(la), str(lb))
    la.rmdir()
    _winapi.CreateJunction(str(lb), str(la))
else:
    la.symlink_to(lb)
    lb.symlink_to(la)
calls = []
saved_main = (sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, applier.UNATTENDED)
applier.DRY, applier.REPO, applier.UNATTENDED = False, "owner/name", False
applier.subprocess.run = lambda cmd, **k: calls.append(cmd) or Ran("")
sys.argv = ["apply-manifest.py", str(la), "--repo", "owner/name", "--no-forbidden-check"]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        applier.main()
    refused = None
except SystemExit as e:
    refused = str(e)
except Exception as e:
    refused = f"{type(e).__name__}: {e}"
finally:
    sys.argv, applier.DRY, applier.REPO, applier.subprocess.run, applier.UNATTENDED = saved_main
    for link in (la, lb):
        if sys.platform == "win32":
            os.rmdir(link)
        else:
            link.unlink()
check(f"a manifest directory that is a link loop is refused as a loop, nothing applied ({refused})",
      "manifest.json cannot be read" in (refused or "") and "Too many levels of symbolic links" in (refused or "")
      and "Invalid argument" not in (refused or "") and calls == [])

# Where the manifest directory's own resolve() answers the loop with a RuntimeError (3.11, 3.12),
# manifest_file names the loop for it as for a file inside.
rloop = manifest_dir([], {})


def dir_looping_resolve(self, strict=False):
    if self == rloop:
        raise RuntimeError("Symlink loop from %r" % str(self))
    return saved_resolve(self, strict=strict)


pathlib.Path.resolve = dir_looping_resolve
try:
    applier.manifest_file(rloop, "x.md")
    raised = None
except OSError as e:
    raised = e.strerror
except Exception as e:
    raised = f"{type(e).__name__}: {e}"
finally:
    pathlib.Path.resolve = saved_resolve
check(f"a RuntimeError resolving the manifest directory itself is refused as a loop ({raised})",
      raised == "Too many levels of symbolic links")

# --- the comment the mode posts ----------------------------------------------------
# The comment is the one piece of model-written text that reaches the tracker whole, so it is
# bounded before it is posted -- and what is measured is the text that will be posted.
VERDICT = TRIAGE + "Verified the cited anchor at HEAD; the claim holds. " * 2000
LEAKED = TRIAGE + "\nThe run log printed " + "ghp_" + "A" * 36 + " where the token was.\n"
LATE = TRIAGE + "Verified at HEAD. " * 600 + "\nkey: " + "sk-ant-" + "A" * 40 + "\n"
RAW_AT_CAP = ("**Triage** " + "Verified at HEAD. " * 4000)[:applier.COMMENT_MAX]  # normalized: one over
EXACT = (TRIAGE + "Verified at HEAD. " * 4000)[:applier.COMMENT_MAX]              # already normalized
WIDE = TRIAGE + "é" * (applier.COMMENT_MAX - len(TRIAGE) - 1)                # twice that in bytes
ONE = lambda: [{"op": "comment", "issue": 56, "body_file": "t.md"}]

for what, text, cause in (
    ("a body over the cap", VERDICT[:applier.COMMENT_MAX + 1], "cap"),
    ("a body at the cap that normalization pushes over it", RAW_AT_CAP, "cap"),
    ("a body carrying a secret-shaped string", LEAKED, "GitHub token"),
    ("a secret far past the first page of a long body", LATE, "Anthropic API key"),
):
    d, calls, out, refused = apply(ONE(), {"t.md": text}, unattended=True)
    check(f"unattended: {what} is refused, naming the step",
          refused is not None and "step 1" in refused and cause in refused and calls == [])

for what, text in (
    ("a body just under the cap, of ordinary verdict text", VERDICT[:applier.COMMENT_MAX - 1]),
    ("a body whose posted text is exactly the cap", EXACT),
    ("a body under the cap in characters but over it in UTF-8 bytes", WIDE),
):
    d, calls, out, refused = apply(ONE(), {"t.md": text}, unattended=True)
    check(f"unattended: {what} is admitted",
          refused is None and any(c[1:3] == ["issue", "comment"] for c in calls)
          and len((d / "rendered-t.md").read_text(encoding="utf-8")) <= applier.COMMENT_MAX)

d, calls, out, refused = apply(ONE(), {"t.md": LEAKED})
check("without the flag that same comment is posted: the bound is the unattended mode's, not a new "
      "interactive refusal",
      refused is None and any(c[1:3] == ["issue", "comment"] for c in calls))

# --- the repository a bare issue number names --------------------------------------
# Without --repo the slug is the WORKING DIRECTORY's binding and the manifest carries none of its
# own, so a manifest drafted for one repository addressed whatever issues carried its numbers in
# another: posting to them live, and echoing their gh lines in a dry run. Every step naming an
# issue it does not create is refused in one message now, before the first call.
NUMBERED = [{"op": "comment", "issue": 42, "body_file": "c.md"},
            {"op": "edit", "issue": "43", "add_labels": ["needs-triage"]},
            {"op": "close", "issue": 44, "body_file": "c.md"},
            {"op": "subissue", "parent": 45, "child": 46}]
# Unattended takes comment and edit only, and every unattended step names a number.
for what, kw, steps in ((".", {}, NUMBERED),
                        (" in a dry run.", {"dry": True}, NUMBERED),
                        (" unattended.", {"unattended": True}, NUMBERED[:2])):
    d, calls, out, refused = apply(steps, {"c.md": TRIAGE}, named_repo=None, **kw)
    check(f"no --repo: a manifest naming issues it does not create is refused before any gh call{what}",
          refused is not None and "--repo owner/name" in refused and calls == [] and not out.strip())
    check(f"no --repo: every step naming one is named in that one refusal{what}",
          all(f"step {i}" in (refused or "") for i in range(1, len(steps) + 1)))

d, calls, out, refused = apply(NUMBERED, {"c.md": TRIAGE}, named_repo=None)
check("no --repo: a subissue is refused for its parent and its child, both numbers",
      "parent 45" in (refused or "") and "child 46" in (refused or ""))

# Named, the same manifest applies, and every call says which repository: gh takes -R, the
# sub-issue API takes the slug in its path.
d, calls, out, refused = apply(NUMBERED, {"c.md": TRIAGE})
check("with --repo that manifest applies, and every gh call names the repository",
      refused is None and calls
      and all(c[-2:] == ["-R", "owner/name"] or any("repos/owner/name/" in str(x) for x in c)
              for c in calls))

# A manifest that references only the issues it creates binds its numbers in the run itself, so
# it names no issue of a repository it was not run against and still takes the binding's.
CREATED = [{"op": "create", "key": "c", "title": "A child", "body_file": "c.md"},
           {"op": "comment", "issue": "{{c}}", "body_file": "c.md"},
           {"op": "edit", "issue": "c", "add_labels": ["needs-triage"]},
           {"op": "subissue", "parent": "c", "child": "{{c}}"}]
d, calls, out, refused = apply(CREATED, {"c.md": TRIAGE}, named_repo=None)
check("no --repo: a manifest referencing only the keys it creates still applies",
      refused is None and any(c[1:3] == ["issue", "create"] for c in calls))

# The check runs ahead of check_fields, which is what types these fields, so every type has to
# pass through it to the refusal that names it rather than raise inside it.
for ref in ([42], {"n": 42}, True, None, 4.2, "{{c}}", "forty-two"):
    d, calls, out, refused = apply([{"op": "comment", "issue": ref, "body_file": "c.md"}],
                                   {"c.md": TRIAGE}, named_repo=None)
    check(f"no --repo: issue {ref!r} is left to the checks that name it, and raises nothing here",
          refused is not None and "--repo" not in refused and "Error:" not in refused
          and calls == [])

# --- what --repo is given ----------------------------------------------------------
# check_repo tests for the token, so a value that is not a repository satisfied it and the run
# went on, an empty one reaching gh as `-R ''` -- see REPO_SLUG for what that resolves to.
# check_args owns the value: every shape is one refusal there, in every mode, before any read.
for value, what in (("", "an empty value"), ("   ", "a whitespace value"),
                    ("--unattended", "the next flag as the value"), ("owner", "a value with no slash"),
                    ("/name", "a value with no owner"), ("owner/", "a value with no name"),
                    ("owner/name/extra", "a value with two slashes"),
                    ("-owner/name", "a value whose owner starts with a dash"),
                    (" owner/name", "a value with a leading space"),
                    ("owner/name ", "a value with a trailing space")):
    for mode, kw in (("", {}), (" in a dry run", {"dry": True}), (" unattended", {"unattended": True})):
        d, calls, out, refused = apply(NUMBERED[:2], {"c.md": TRIAGE}, named_repo=value, **kw)
        check(f"--repo with {what} is refused before anything runs{mode}",
              refused is not None and "--repo needs an owner/name value" in refused
              and calls == [] and not out.strip())

d, calls, out, refused = apply(NUMBERED, {"c.md": TRIAGE}, named_repo="Owner-1/name_2.x")
check("a well-formed owner/name still applies, and gh carries it",
      refused is None and all(c[-2:] == ["-R", "Owner-1/name_2.x"]
                              or any("repos/Owner-1/name_2.x/" in str(x) for x in c) for c in calls))

# --- the flag itself ---------------------------------------------------------------
# The bounds are opt-in, so an argument this script does not recognise must be a refusal: a
# mistyped flag that reads as "interactive" runs unbounded and says nothing.
check("a well-formed argument list gives back the manifest directory",
      applier.check_args(["some-dir", "--repo", "owner/name", "--dry-run", "--unattended"]) == "some-dir")
for spelling in ("--unattended=true", "--Unattended", "-unattended", "--unattend", "--UNATTENDED", "--dryrun"):
    try:
        applier.check_args(["some-dir", spelling])
        check(f"the near-miss flag {spelling} is refused rather than run unbounded", False)
    except SystemExit as e:
        check(f"the near-miss flag {spelling} is refused rather than run unbounded", spelling in str(e))
for argv, why in (([], "no manifest directory"), (["a", "b"], "two manifest directories"),
                  (["a", "--repo"], "--repo with no value")):
    try:
        applier.check_args(argv)
        check(f"{why} is refused", False)
    except SystemExit:
        check(f"{why} is refused", True)

# --- the private token list ---------------------------------------------------------
# check_forbidden pipes each body file and each create title to forbidden-tokens.py's check mode,
# a child the harnesses' gh stubs never see: a refusal row asserts calls == [], so a clean run is
# the control that the stub does record what the applier posts.
PLANT = "zebrafish"


@contextlib.contextmanager
def token_list(text, raw_path=None):
    """OURO_FORBIDDEN_TOKENS names a list holding text; text None leaves the variable unset."""
    saved = os.environ.pop("OURO_FORBIDDEN_TOKENS", None)
    if text is not None:
        f = pathlib.Path(tempfile.mkdtemp(prefix="tokens-", dir=PARENT_TMP)) / "list.txt"
        f.write_text(text, encoding="utf-8")
        os.environ["OURO_FORBIDDEN_TOKENS"] = raw_path or str(f)
    try:
        yield
    finally:
        os.environ.pop("OURO_FORBIDDEN_TOKENS", None)
        if saved is not None:
            os.environ["OURO_FORBIDDEN_TOKENS"] = saved


def refused_clean(refused, calls, out, planted=PLANT):
    return refused is not None and calls == [] and planted not in (refused or "") and planted not in out


CLEAN_STEPS = [{"op": "comment", "issue": 42, "body_file": "c.md"}]

with token_list(PLANT):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "A clean comment.\n"}, forbidden=True)
    check("a clean manifest runs with a list set, and gh is called (the control for the rows below)",
          refused is None and len(calls) > 0 and "skipped" not in out)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": f"Line one.\nA {PLANT} here.\n"}, forbidden=True)
    check("a hit in a body refuses, naming the step, the file, its line and the entry",
          refused_clean(refused, calls, out) and "step 1 c.md:2: entry 1" in refused)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": f"A {PLANT.upper()} here.\n"}, forbidden=True, dry=True)
    check("a dry run refuses too, and the match is case-insensitive",
          refused_clean(refused, calls, out, PLANT.upper()) and "step 1 c.md:1: entry 1" in refused)

    d, calls, out, refused = apply([{"op": "create", "key": "a", "title": f"The {PLANT} title", "body_file": "b.md"}],
                                   {"b.md": "Clean.\n"}, forbidden=True)
    check("a hit in a create title refuses, naming the step and 'title'",
          refused_clean(refused, calls, out) and "step 1 title:1: entry 1" in refused and "b.md" not in refused)

    d, calls, out, refused = apply(
        [{"op": "comment", "issue": 42, "body_file": "c.md"}, {"op": "close", "issue": 42, "body_file": "e.md"}],
        {"c.md": TRIAGE, "e.md": f"Closing {PLANT}.\n"}, forbidden=True)
    check("a hit in a later step names that step and its file, not the clean one before it",
          refused_clean(refused, calls, out) and "step 2 e.md:1: entry 1" in refused and "c.md" not in refused)

    d, calls, out, refused = apply([{"op": "edit", "issue": 42, "body_file": "b.md"}],
                                   {"b.md": f"{PLANT}\n" + "A longer body than the cut one.\n" * 5}, forbidden=True)
    check("an edit's hit refuses before check_bodies reads the issue: no gh call, a read included",
          refused_clean(refused, calls, out))

with token_list("zebra fish\n"):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "A zebra\nfish here.\n"}, forbidden=True)
    check("a token wrapped across a line break refuses, relaying the wrapped report by step and file",
          refused_clean(refused, calls, out, "zebra") and "step 1 c.md: entry 1, across a line break" in refused)

with token_list(None):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True)
    check("an unset list refuses, naming the missing list, even in a dry run",
          refused_clean(refused, calls, out) and "missing list" in refused and "OURO_FORBIDDEN_TOKENS is not set" in refused)

with token_list(PLANT, raw_path=str(PARENT_TMP / "no-such-list.txt")):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
    check("a list that cannot be read refuses without naming its path",
          refused_clean(refused, calls, out) and "cannot be read" in refused and "no-such-list" not in refused)

with token_list(f"({PLANT}\n"):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
    check("an entry that does not compile refuses, naming its line and never the entry",
          refused_clean(refused, calls, out) and "line 1" in refused and "does not compile" in refused)

with token_list(None):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"})
    check("--no-forbidden-check runs with no list, printing the one skip line",
          refused is None and len(calls) > 0 and out.count("forbidden-token check skipped") == 1)

with token_list(PLANT):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": f"A {PLANT} here.\n"}, dry=True)
    check("and with the flag a body the list would match is not read against it",
          refused is None and "skipped" in out)

# The matcher itself missing, or exiting with a code the contract does not name.
saved_matcher = applier.MATCHER
try:
    applier.MATCHER = PARENT_TMP / "no-such-matcher.py"
    with token_list(PLANT):
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
        check("a matcher not found beside the applier refuses, naming it",
              refused_clean(refused, calls, out) and "no-such-matcher.py is not beside this script" in refused)
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"})
        check("--no-forbidden-check skips the lookup too: no matcher needed",
              refused is None and len(calls) > 0 and "skipped" in out)
    odd = PARENT_TMP / "odd-matcher.py"
    odd.write_text("import sys\nsys.stderr.write('odd failure\\n')\nsys.exit(3)\n", encoding="utf-8")
    applier.MATCHER = odd
    with token_list(PLANT):
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
        check("a matcher exit other than 0 or 1 refuses, relaying its stderr cause",
              refused_clean(refused, calls, out) and "odd failure" in refused)
    odd.write_text("import sys\nsys.stdout.write('stdin:1: entry 1\\nzebrafish\\n')\nsys.exit(1)\n", encoding="utf-8")
    with token_list(PLANT):
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
        check("a report line of any other shape is not relayed, so matched text cannot leak",
              refused_clean(refused, calls, out) and "step 1 c.md:1: entry 1" in refused)
    odd.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    with token_list(PLANT):
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
        check("a matcher exit 1 with no report line refuses, naming its exit, with no gh call",
              refused_clean(refused, calls, out) and "exit 1" in refused)
finally:
    applier.MATCHER = saved_matcher

# ship.forbidden_check = "off" in the working directory's binding skips the check and the lookup,
# printing one line, for the repository that binding names only. Absent, "required", any other
# spelling, an overlay setting it or a binding naming another repository leaves it on: with no
# list each of those is the missing-list refusal.
def with_ship(extra):
    return ('schema = 1\n[repo]\nslug = "owner/name"\ndefault_branch = "main"\n'
            f'[ship]\npolicy = "stop-at-pr"\nreview = "adversarial-review"\n{extra}'
            '[owner]\nruling_approvers = ["someone"]\n')


OFF = with_ship('forbidden_check = "off"\n')
SKIP_BY_BINDING = 'forbidden-token check skipped: the binding declares ship.forbidden_check = "off"'

with token_list(None):
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True, binding=OFF)
    check('a binding declaring forbidden_check = "off" runs with no list, printing the one skip line',
          refused is None and out.count("forbidden-token check skipped") == 1 and SKIP_BY_BINDING in out
          and "$ gh issue comment 42" in out)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True, binding=OFF,
                                   named_repo="OWNER/Name")
    check("and the binding's repository is matched case-insensitively, as GitHub names it",
          refused is None and SKIP_BY_BINDING in out)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, dry=True, binding=OFF)
    check("--no-forbidden-check beside that binding still prints one skip line, its own",
          refused is None and out.count("forbidden-token check skipped") == 1
          and "skipped: --no-forbidden-check" in out)

    for what, text, over in (("a binding without the key", with_ship(""), None),
                             ('a binding declaring "required"', with_ship('forbidden_check = "required"\n'), None),
                             ('a binding spelling it "Off"', with_ship('forbidden_check = "Off"\n'), None),
                             ('a binding spelling it " off"', with_ship('forbidden_check = " off"\n'), None),
                             ('a binding spelling it "off\\t"', with_ship('forbidden_check = "off\\t"\n'), None),
                             ('a binding spelling it "off\\n"', with_ship('forbidden_check = "off\\n"\n'), None),
                             ('an overlay setting "off"', with_ship(""), '[ship]\nforbidden_check = "off"\n')):
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True,
                                       binding=text, overlay=over)
        check(f"{what} leaves the check on: no list refuses, as with no binding",
              refused_clean(refused, calls, out) and "missing list" in refused and "skipped" not in out)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True, binding=OFF,
                                   named_repo="other/name")
    check("an \"off\" binding naming another repository leaves the check on for --repo's, and says so",
          refused_clean(refused, calls, out) and "missing list" in refused and "skipped" not in out
          and "not other/name" in out)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True,
                                   binding=OFF.replace("owner/name", "owner/nake"), named_repo="owner/na\u212ae")
    check("the slugs are compared in ASCII case only: a KELVIN SIGN is not a k",
          refused_clean(refused, calls, out) and "missing list" in refused and "skipped" not in out)

    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True,
                                   binding=OFF.replace('slug = "owner/name"\n', ""))
    check('an "off" binding with no repo.slug leaves the check on, silently',
          refused_clean(refused, calls, out) and "missing list" in refused and "covers" not in out)

    # get answers from a binding that ouro-binding.py check refuses; that binding does not turn the check off
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True,
                                   binding='[repo]\nslug = "owner/name"\n[ship]\nforbidden_check = "off"\n')
    check('an "off" binding that fails ouro-binding.py check leaves the check on, with no skip line',
          refused_clean(refused, calls, out) and "missing list" in refused and "skipped" not in out)

# A helper that hangs: the binding read is a failed read, which keeps the check; the matcher call
# refuses. HELPER_TIMEOUT is lowered so each row ends within a few seconds.
hang = PARENT_TMP / "hang.py"
hang.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
saved_helpers = (applier.BINDING, applier.MATCHER, applier.HELPER_TIMEOUT)
try:
    applier.HELPER_TIMEOUT = 2
    applier.BINDING = hang
    with token_list(None):
        t0 = time.monotonic()
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True, binding=OFF)
        elapsed = time.monotonic() - t0
    check(f"a binding read that hangs keeps the check, ending within the bound ({elapsed:.1f}s)",
          refused_clean(refused, calls, out) and "missing list" in refused and "skipped" not in out and elapsed < 15)
    applier.BINDING = saved_helpers[0]
    applier.MATCHER = hang
    with token_list(PLANT):
        t0 = time.monotonic()
        d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True)
        elapsed = time.monotonic() - t0
    check(f"a matcher call that hangs refuses with nothing applied, within the bound ({elapsed:.1f}s)",
          refused_clean(refused, calls, out) and "timed out after 2 s" in refused and elapsed < 15)
finally:
    applier.BINDING, applier.MATCHER, applier.HELPER_TIMEOUT = saved_helpers

saved_matcher = applier.MATCHER
try:
    applier.MATCHER = PARENT_TMP / "no-such-matcher.py"
    d, calls, out, refused = apply(CLEAN_STEPS, {"c.md": "Clean.\n"}, forbidden=True, dry=True, binding=OFF)
    check('an "off" binding skips the lookup too: no matcher needed', refused is None and SKIP_BY_BINDING in out)
finally:
    applier.MATCHER = saved_matcher

for spelling in ("--no-forbidden", "--No-Forbidden-Check", "--no-forbidden-check=true"):
    try:
        applier.check_args(["some-dir", spelling])
        check(f"the near-miss flag {spelling} is refused", False)
    except SystemExit as e:
        check(f"the near-miss flag {spelling} is refused", spelling in str(e))
check("the flag itself is accepted", applier.check_args(["some-dir", "--no-forbidden-check"]) == "some-dir")
check("and the usage line names it", "--no-forbidden-check]" in applier.USAGE)

print("all apply-manifest cases pass" if not failures else f"{failures} failure(s)")
sys.exit(1 if failures else 0)
