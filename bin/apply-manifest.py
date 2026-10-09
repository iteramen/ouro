"""Execute a triage manifest against GitHub from the session that holds the owner's approval.

Verifier subagents are read-only: they draft bodies and comments as files plus an ordered
manifest.json; the session with the approval in its context runs this. Repo comes from the
binding (repo.slug) or --repo, and --repo is required of a manifest naming an issue it does not
create -- see check_repo. A manifest referencing only the issues it creates still takes the
binding's.

usage: python3 apply-manifest.py <manifest-dir> [--repo owner/name] [--dry-run] [--unattended --targets <file>] [--no-forbidden-check]
--repo is required when a step's issue, child or parent is an issue the manifest does not create.
Before any step runs, dry runs included, every body file a step posts (placeholders {{key}}
unfilled, as the check runs before rendering) and every create title is piped, one call each, to
`check` mode of the forbidden-tokens.py beside this script, which matches the private list
OURO_FORBIDDEN_TOKENS names. A hit refuses the manifest, relaying each report line with the step and
the file name or 'title' and never the matched text; a missing or unreadable list, any other
nonzero exit, or no forbidden-tokens.py beside this script refuses too. --no-forbidden-check
skips the check and the lookup, and the run prints one line saying so -- for a host that holds no
list, as the weekly pass's CI does. So does a working directory's binding declaring
ship.forbidden_check = "off" when its repo.slug is the repository the run posts to and
`ouro-binding.py check` passes it; a binding that fails that check, a failed read of it, or no
binding leaves the check on. Each matcher call and each binding read is bounded at HELPER_TIMEOUT
(30) seconds: a binding read that runs past it is a failed read, and a matcher call that does
refuses. See check_forbidden.
manifest.json is read as utf-8-sig, and render reads each body file the same way and drops a
repeated mark, so a UTF-8 byte-order mark on either applies and no posted body carries one; a
manifest that cannot be read as JSON -- absent, not UTF-8, text that does not parse, an object
giving a key twice, nesting too deep to parse -- is refused by name ahead of every check.
A manifest that is not a list, or a step in it that is not an object, is refused naming what was
found, ahead of every check.
Steps run in the order the manifest lists them. Order them create | comment | edit | close |
subissue: the shape gate fires on the `labeled` event and wants the `**Triage**` provenance
comment already there, so an edit that adds a state label must come AFTER that issue's comment.
This is checked before anything runs -- see check_order. So is each step's shape: a step with no op,
an op the applier does not know, one missing a field its op requires, a create key a reference
cannot name (not a string, only digits, or not only letters, digits and underscores), a create key a
second create step declares again, a create title that is not a string, a body_file that is not a
string, a body_file whose file name -- the one PureWindowsPath reads, on every platform --
casefolded, takes the rendered- prefix render writes its own output under, a body_file that is not a
bare file name (one PureWindowsPath reads as anything but that one name, one holding a colon, or one
ending in a space or a dot, which Windows drops when it opens a name), a labels, add_labels or
remove_labels that is not a list of strings, or a field the step's op does not read -- each op reads
a fixed set of fields, READS, and a step carrying any other is refused naming the field and the op
-- refuses the manifest -- see check_fields.
So does a body file that cannot be read: one that is missing, is a directory, is not UTF-8 text or
sits in a symlink loop is refused by step and name -- see check_readable.
So does a rendered copy's path render cannot write: a directory or a file the run cannot open
there is refused by step, name and strerror before any step runs, and on Windows so is a hidden or
system file, named in words; a FIFO or a hard link there is refused in words too, before any open;
a subissue step renders nothing and is not probed -- see check_writable.
So does a reference to a key no create step binds in time: an issue, child or parent that is not a
number, or a placeholder in a comment, edit or close body, naming no key an earlier create step
declares, or a placeholder in a create body naming no key any create step declares, every one named
in one refusal; so does an issue, child or parent that resolves below 1; so does a placeholder in
a create title, whether or not a create declares that key, a title being literal text that gh
posts as written -- see check_refs. So are the body files: an empty one, or an edit that cuts an
existing issue's body below half without allow_shrink, refuses the manifest; so does a path an
edit's before-copy cannot be kept at, a directory, a symlink, a FIFO or a hard link, named by step
before any issue is read -- see check_bodies.
Every body file a manifest names, and every before-copy it keeps, must resolve inside the manifest
directory, in both modes: a `..` segment, an absolute path and a symlink out are refused, and
nothing outside it is read, written or posted -- see manifest_file. A manifest is model-written either way (a verifier subagent drafts the
interactive one too), and the approval that precedes it covers the verdicts, not the file paths.
The unattended mode is for a manifest a model wrote from untrusted input, where what this
accepts is the security boundary rather than a convenience. Before anything runs it refuses any op
but comment and edit, an addition or removal list that is not a list of strings, any label but
needs-triage and needs-ruling among an edit's additions and removals, an edit carrying a body file
(the intake never rewrites a body), an issue that is not an issue number -- ASCII digits, at most
ISSUE_DIGITS of them, written as a string or as a JSON integer -- or that resolves below 1, a
comment step with no body file or one that is not a string or cannot be read (this mode opens the
file before check_fields types it), and a comment body carrying a placeholder, over COMMENT_MAX
characters or carrying a secret-shaped string -- see check_unattended. It requires --targets
<file>, the JSON object Get-IntakeTargets.ps1 wrote (its `targets` rows each carry a `number`), and
refuses a step whose issue is not one of those numbers, and a comment whose posted first line is not
`**Intake triage** (automated)`; --targets outside --unattended is refused. In that mode an edit also
sends exactly the removals it declares: an allowlist over declared labels bounds nothing while the
applier synthesizes removals on top of them. The one-state invariant is then not this script's job
in that mode, and the label-invariants gate reports a second state label every week.
An edit step reads every label of its issue when it adds a state or declares a removal, and
removes only labels the issue carries -- see resolve_labels; a declared removal the issue does not
carry is dropped, with a line naming it. gh writes one edit's additions and removals as two
independent halves, and a label the repository lacks fails only its own half, so the step sends
two calls and checks each exit: the body and the removals, then the additions. The shape gate
fires on the second call's `labeled` event, so it reads the body the first call set.
gh writes the body in its own half beside the label halves, so a first call that fails may still
have landed its removals, and its message says so when they name a state.
State and modifier names match case-insensitively, as gh matches labels; a removal names the label
as the issue carries it.
Outside a dry run, a create whose gh call answers no issue number is a failed step, named, and no
step after it runs -- the deferred re-render and the state re-check still run, as after any failed
step. Neither covers the issue that create may have made: the call exited 0, so it may exist with
no key bound to it, on neither list, and the refusal says so.
After the last write, and after a step that fails -- whatever it raised, short of a
KeyboardInterrupt -- it waits out an intake stamp and removes a second state label from every issue
it created or moved to a state so far -- see recheck_states; unattended, only a needs-triage or
needs-ruling second state, any other being reported. A failed step then exits nonzero with
its failure; with every step applied, a correction that fails exits nonzero.
Body files may contain {{key}} placeholders for issues created earlier in the same manifest, and a
create body for issues created after it too: its forward references are resolved in a final pass
that re-renders it once the creates have run. They are substituted with '#N'-less numbers (write
'#{{key}}' in the file to get '#NN'). After a step that fails, that pass still runs, with the ids
bound so far: it substitutes the keys that resolved and reports each issue whose body still names a
key no create bound. A body a later edit step set is not re-rendered, that body being the issue's;
where that edit's own call exited nonzero, the pass sends the edit's body instead of the create's,
since either half of the call may have landed. That trades one harm for a smaller one: a body gh
rejects for its own sake is re-sent once and reported, and the create's body stays up with its
placeholders unresolved and unnamed, where reverting an edit that did land would have published a
body the manifest had already corrected. A call that raised rather than exited -- no gh on PATH --
landed nothing, so the create's body is the one re-rendered. Each body is re-rendered on its own,
so one that fails is reported and the next still runs; a failed step still exits with its own
failure, and with every step applied, a re-render that fails exits nonzero naming each issue it
left.
"""
import errno, json, pathlib, re, stat, subprocess, sys, time
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MATCHER = pathlib.Path(__file__).resolve().parent / "forbidden-tokens.py"
BINDING = pathlib.Path(__file__).resolve().parent / "ouro-binding.py"
HELPER_TIMEOUT = 30  # seconds, per matcher call and per binding read
MATCH_RUN = subprocess.run  # bound at import: a caller that replaces subprocess.run to stand in for gh leaves the matcher and check_off's binding read real
REPO = None  # --repo, resolved at the bottom, before main() runs
UNATTENDED = False  # --unattended, resolved at the bottom
TARGETS = None  # --targets <file>, resolved at the bottom: the intake's own targets file
USAGE = ("usage: python3 apply-manifest.py <manifest-dir> [--repo owner/name] [--dry-run] [--unattended --targets <file>] [--no-forbidden-check]\n"
         "--repo is required when a step's issue, child or parent is an issue the manifest does not create.")
# --repo's value: an owner and a name, one slash, no whitespace, and never the next flag.
# check_repo tests for the token, so a blank value satisfies it and reaches gh as `-R ''`, which
# gh reads as no -R at all and resolves from the clone's own remotes -- the repository the whole
# guard exists to keep a manifest off.
REPO_SLUG = re.compile(r"[^-/\s][^/\s]*/[^/\s]+")

# The contract's eight states. An issue carries exactly one, so adding one means removing
# whichever other it already had -- including a `needs-triage` an intake automation stamped
# at creation time, which no manifest author can predict. Outside --unattended a declared step
# synthesizes that removal; in it, only the post-apply correction does.
STATES = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage", "idea",
          "umbrella", "architecture")
MODIFIERS = ("trivial", "checkpoint")  # ride only on agent-ready
MARKER = "**Triage**"
INTAKE_MARKER = "**Intake triage** (automated)"  # the one first line an unattended comment may open with
# ponytail: a fixed wait, not a poll for the stamp. Ceiling: an intake job queued longer than
# this stamps after the re-read, and only the label-invariant sweep sees that.
INTAKE_WAIT = 20  # seconds from the last `create` to the post-apply state re-read

# What --unattended accepts: the intake comments and moves one label, and does nothing else.
UNATTENDED_OPS = ("comment", "edit")
UNATTENDED_LABELS = ("needs-triage", "needs-ruling")
# Measured on this repo: 65 real verdict comments, the largest 5217 characters, median 1895. The
# cap is close to four times that largest, and under a third of GitHub's own 65536 maximum. A cap
# set at the platform limit would refuse only what the platform already refuses, which bounds
# nothing -- and the bound is the point, the comment being the one piece of model-written text
# that reaches the tracker whole.
COMMENT_MAX = 20000
# Secret shapes refused in an unattended comment body: prefixed token formats and the PEM header,
# each unmistakable in prose. A general high-entropy rule is deliberately absent -- it would refuse
# the SHAs and the base64 an ordinary verdict quotes.
# This is a list of known shapes, not a scanner, and a body carrying one of these gets posted: the
# `extraheader = AUTHORIZATION: basic <base64>` blob actions/checkout leaves in .git/config, a JWT,
# a basic-auth URL, and any credential whose format has no distinguishing prefix.
SECRET_SHAPES = (
    ("a GitHub token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("a GitHub fine-grained token", r"github_pat_[A-Za-z0-9_]{20,}"),
    ("an Anthropic API key", r"sk-ant-[A-Za-z0-9_-]{16,}"),
    ("an AWS access key id", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    ("a private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("a Slack token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
)


def normalize_triage(text):
    """A comment whose first non-blank line starts with **Triage** (case-sensitive), in the shape
    the shape gate reads: a byte-order mark and leading blank lines dropped, the marker alone on
    line 1, line 2 blank, and any text after the marker -- less one leading em dash, hyphen or
    colon -- on line 3. Lines split on LF only, as the gate splits them. It rewrites rather than
    refuses: the content is right, only the shape is wrong. Any other text comes back unchanged."""
    lines = text.removeprefix("﻿").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or not lines[0].startswith(MARKER):
        return text
    rest = lines[0][len(MARKER):].lstrip()
    if rest[:1] in ("—", "-", ":"):
        rest = rest[1:].lstrip()
    body = ([rest] if rest else []) + lines[1:]
    if body and body[0].strip():
        body.insert(0, "")
    new = "\n".join([MARKER] + body)
    return text if new == text else new


def one_of_each_key(pairs):
    """The dict of a JSON object, refusing a key it gives twice: json.loads keeps the last of a
    repeated key and says nothing, so a step spelling op twice ran as its second op."""
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise ValueError(f"an object gives the key {k!r} twice")
        seen.add(k)
    return dict(pairs)


def check_args(argv):
    """The manifest directory, refusing any argument this script does not recognise, by name.

    The bounds are opt-in -- no --unattended means no bounds -- so a mistyped flag must never read
    as an interactive run: --Unattended, --unattended=true, -unattended and --unattend each used to
    run unbounded and silent, and each is a refusal now.
    --repo's value is checked here, this being the one site that owns it: an owner/name and
    nothing else -- see REPO_SLUG -- so a missing, blank or flag-shaped value is one refusal here
    rather than a slug every reader below has to make sense of.
    """
    rest, dirs = list(argv), []
    while rest:
        a = rest.pop(0)
        if a == "--repo":
            v = rest.pop(0) if rest else ""
            if not REPO_SLUG.fullmatch(v):
                raise SystemExit(f"--repo needs an owner/name value, got {v!r}\n" + USAGE)
        elif a == "--targets":
            v = rest.pop(0) if rest else ""
            if not v or v.startswith("-"):
                raise SystemExit(f"--targets needs a file, got {v!r}\n" + USAGE)
        elif a in ("--dry-run", "--unattended", "--no-forbidden-check"):
            continue
        elif a.startswith("-"):
            raise SystemExit(f"unknown argument {a}, nothing applied.\n" + USAGE)
        else:
            dirs.append(a)
    if "--targets" in argv and "--unattended" not in argv:
        raise SystemExit("--targets is the unattended intake's file, and this run is not --unattended, nothing applied.\n" + USAGE)
    if len(dirs) != 1:
        raise SystemExit(f"expected one manifest directory, got {len(dirs)}: {', '.join(dirs) or 'none'}\n" + USAGE)
    return dirs[0]


def manifest_file(d, name):
    """The manifest's own file <name>, refused when it lands outside the manifest directory.

    Every read of a manifest-named file goes through here, in both modes: a manifest is
    model-written either way, and an interactive approval covers the verdicts, not the file paths.
    Otherwise a `..` segment, an absolute path or a symlink is a read of any file the session can
    reach -- and what render reads is what gets posted. resolve() walks `..` and follows symlinks,
    so comparing the resolved path against the resolved directory refuses every shape. render
    passes the rendered copy's path through here too: "rendered-" + name is still a string the
    manifest wrote.
    A symlink loop resolves to no file at all. Where resolve() answers one with a RuntimeError,
    for the manifest directory itself or for the file in it,
    -- as it answers a chain too deep to walk, whose RecursionError is one -- or the strict
    resolve does, or the strict resolve raises an OSError whose winerror is 1921, that becomes
    the OSError carrying ELOOP which check_readable and check_unattended name by step, name and
    strerror. Any other OSError from the strict resolve is left to the read. The strerror is
    spelled out: errno.ELOOP is the Winsock number on Windows, where os.strerror answers
    'Unknown error'.
    """
    loop = OSError(errno.ELOOP, "Too many levels of symbolic links")
    try:
        root = d.resolve()
        p = (d / name).resolve()
    except RuntimeError:  # the ELOOP the read raises where resolve() does not
        raise loop
    try:
        p.resolve(strict=True)
    except RuntimeError:
        raise loop
    except OSError as e:
        if getattr(e, "winerror", None) == 1921:  # Windows: a loop, or a chain too deep to follow
            raise loop
    if root not in p.parents:
        raise SystemExit(f"body file '{name}' resolves outside the manifest directory")
    return p


def label_list(v):
    """A label field as every reader of one assumes it: a list of strings. A create sends one
    --label per item, resolve_labels and the unattended allowlist casefold each, and a string
    passed instead iterates as its characters -- one --label per letter, sent to a repository
    that has no such label."""
    return isinstance(v, list) and all(isinstance(l, str) for l in v)


def check_repo(steps):
    """Refuse, before anything runs, a manifest naming an issue it does not create with no --repo.

    Without --repo the repository is whatever repo.slug the WORKING DIRECTORY's binding answers,
    and the manifest carries none of its own: a manifest drafted for one repository then
    comments on, edits and closes whatever issues carry its numbers in another. A manifest
    whose every reference is a create key binds its numbers in the run itself and still takes the
    binding's; where that read fails there is nothing to take and resolve_repo's own refusal
    stands, which already names --repo.
    --repo's presence is the whole test here: check_args has already refused every value that is
    not an owner/name, so a token that is there names a repository.
    An issue number is is_issue_number's reading of a string, or a JSON integer, which resolve_ref
    hands gh as it stands; a bool is neither, though Python counts one as an int.
    This runs ahead of check_fields, which is what types a step's op and its fields, so it reads
    the three reference fields on every step and none of them may raise on a value of any type.
    Every step naming one is named in one refusal, and nothing is applied."""
    if "--repo" in sys.argv:
        return
    bad = []
    for i, s in enumerate(steps, 1):
        who = s.get("key") if s.get("op") == "create" else s.get("issue")  # a create's identity is its key
        where = " ".join(str(x) for x in (s.get("op"), who or s.get("issue") or s.get("key")) if x)
        for f in ("issue", "child", "parent"):
            v = s.get(f)
            if not isinstance(v, bool) and (isinstance(v, int)
                                            or (isinstance(v, str) and is_issue_number(v))):
                bad.append(f"step {i} ({where}): {f} {v!r} is a bare issue reference, not one "
                           "this manifest creates")
    if bad:
        raise SystemExit(
            "no --repo, and this manifest names issues it does not create, nothing applied:\n  "
            + "\n  ".join(bad) +
            "\nPass --repo owner/name: the repository is otherwise the working directory's "
            "binding, and a number names one issue only against the repository this manifest "
            "was drafted for.")


def target_numbers():
    """The issue numbers the intake's targets file names, or the reason it cannot be used.

    The file is the JSON object Get-IntakeTargets.ps1 writes: a `targets` array whose rows each
    carry a `number`. The grading session's Edit names only the manifest directory, and the
    weekly pass compares the file's SHA-256 before it calls this applier, so the file is what
    bounds where an unattended step may go. A row without an integer number names no issue."""
    if not TARGETS:
        return None, "--unattended needs --targets <file>: the intake's own targets file bounds where a step may post"
    try:
        doc = json.loads(pathlib.Path(TARGETS).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError) as e:
        return None, f"the targets file cannot be read as JSON: {e.strerror if isinstance(e, OSError) else e}"
    rows = doc.get("targets") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return None, "the targets file has no targets array"
    return {r["number"] for r in rows if isinstance(r, dict) and type(r.get("number")) is int}, None


def check_unattended(d, steps):
    """Refuse, before any step runs, a manifest --unattended must not be able to apply.

    Unattended, the manifest is model-written from untrusted issue text, so what this accepts is
    the security boundary. The intake comments and moves one label: every other op is refused, so is
    any label but needs-triage and needs-ruling among an edit's additions and removals, and so is an
    edit carrying a body file -- the intake never rewrites a body. The comment body is bounded too,
    being the one piece of model-written text that reaches the tracker whole: over COMMENT_MAX
    characters, or carrying a secret-shaped string, it is refused rather than posted. A step must
    name an issue the --targets file lists, and a comment must open with INTAKE_MARKER, the only
    marker the intake writes: neither is left to the model-written manifest, which a planted issue
    body may steer. The cap and
    the scan read the text the comment step will post -- normalize_triage rewrites the marker line,
    and that rewrite is what lands -- not the bytes on disk.
    Refused here too is everything that would otherwise raise mid-run, with earlier steps already
    posted: a body file missing or not UTF-8, one that is not a string (this mode opens the file
    before check_fields types it), one outside the manifest directory (that confinement is every
    run's -- see manifest_file -- and this only brings it forward of the first step), a comment
    step carrying none, a {{placeholder}} (only a create step resolves one, and unattended there
    are none), an issue that is not an issue number -- is_issue_number's reading of a string, and
    the same digit bound on an int -- or that resolves below 1, and an add_labels or remove_labels
    that is not a list of strings -- the allowlist casefolds each label, so anything else refused
    the wrong thing or raised, by its type: a string one letter at a time, a list holding a number
    on the casefold, None and a number in the unpacking.
    "Before any step runs" has to hold for the whole mode, not just for the rules above.
    Every violation in the manifest is named in one refusal, and nothing is applied.
    """
    if not UNATTENDED:
        return
    numbers, why = target_numbers()
    if why:
        raise SystemExit("unattended mode refuses this manifest, nothing applied:\n  " + why)
    bad = []
    for i, s in enumerate(steps, 1):
        op = s.get("op")
        who = (s.get("key") if op == "create" else s.get("issue")) or s.get("issue") or s.get("key") or ""
        at = f"step {i} ({op} {who})" if who else f"step {i} ({op})"
        if op not in UNATTENDED_OPS:
            bad.append(f"{at}: op {op} is not one of {', '.join(UNATTENDED_OPS)}")
            continue
        issue = s.get("issue")
        not_a_number = f"{at}: issue {issue!r} is not an issue number, and nothing resolves one here"
        if isinstance(issue, bool) or not (isinstance(issue, int)
                                           or (isinstance(issue, str) and is_issue_number(issue))):
            bad.append(not_a_number)
        elif int(issue) < 1:  # check_refs refuses the same, on every field that takes a reference
            bad.append(f"{at}: issue {issue!r} resolves to {int(issue)}, and no issue is numbered below 1")
        elif len(str(issue)) > ISSUE_DIGITS:  # an int: is_issue_number bounds a string's own digits
            bad.append(not_a_number)
        elif int(issue) not in numbers:  # where a step goes: only an issue this run was handed
            bad.append(f"{at}: issue {issue!r} is not one of the targets this run was handed")
        for f in ("add_labels", "remove_labels"):  # the allowlist reads each label, casefolded
            if not label_list(s.get(f, [])):
                bad.append(f"{at}: {f} {s[f]!r} is not a list of strings")
                continue
            for l in s.get(f, []):
                if l.casefold() not in UNATTENDED_LABELS:
                    bad.append(f"{at}: label {l} is not one of {', '.join(UNATTENDED_LABELS)}")
        if op == "edit" and s.get("body_file"):
            bad.append(f"{at}: carries the body file '{s['body_file']}', and the intake never rewrites a body")
        if op == "comment":
            name = s.get("body_file")
            if not name:
                bad.append(f"{at}: has no body_file, and a comment step posts one")
                continue
            if not isinstance(name, str):  # this mode reads the file here, before check_fields types it
                bad.append(f"{at}: body_file {name!r} is not a string")
                continue
            try:
                text = manifest_file(d, name).read_text(encoding="utf-8")
            except SystemExit as e:
                bad.append(f"{at}: {e}")
                continue
            except (OSError, ValueError) as e:  # not there, or not UTF-8: a UnicodeDecodeError
                bad.append(f"{at}: the comment body '{name}' cannot be read: "
                           f"{e.strerror if isinstance(e, OSError) else e}")
                continue
            posted = normalize_triage(text)  # what the step posts, not what the file holds
            first = posted.removeprefix("\ufeff").split("\n")[0].strip(" \t\r")
            if first != INTAKE_MARKER:
                bad.append(f"{at}: the comment body '{name}' opens with {first[:60]!r}, and an unattended comment opens with the line {INTAKE_MARKER}")
            if placeholders(posted):
                bad.append(f"{at}: the comment body '{name}' carries a placeholder, which only a create step resolves")
            if len(posted) > COMMENT_MAX:
                bad.append(f"{at}: the comment body '{name}' is over the {COMMENT_MAX}-character cap once posted")
            for what, shape in SECRET_SHAPES:
                if re.search(shape, posted):
                    bad.append(f"{at}: the comment body '{name}' carries {what}")
    if bad:
        raise SystemExit("unattended mode refuses this manifest, nothing applied:\n  " + "\n  ".join(bad))


# Every field each op reads unguarded. An edit and a subissue need no body_file: a label-only edit
# is a step, and a subissue renders nothing it carries.
REQUIRED = {"create": ("key", "title", "body_file"), "comment": ("issue", "body_file"),
            "edit": ("issue",), "close": ("issue", "body_file"), "subissue": ("parent", "child")}
# Every field each op reads, op included. A step carrying any other field is refused naming the
# field and the op: the applier would ignore it and nothing would say so -- an edit's title was
# never sent, a comment's add_labels moved no label. A subissue reads parent and child only:
# main resolves an issue on any step and check_refs checks one, but no subissue call uses it.
READS = {"create": ("op", "key", "title", "body_file", "labels"), "comment": ("op", "issue", "body_file"),
         "edit": ("op", "issue", "body_file", "add_labels", "remove_labels", "allow_shrink"),
         "close": ("op", "issue", "body_file"), "subissue": ("op", "parent", "child")}
LABEL_FIELDS = ("labels", "add_labels", "remove_labels")  # a create's, and an edit's two


def check_fields(steps):
    """Refuse, by name and before any step runs, a step with no op, an op the applier does not know,
    one missing a field its op requires, or one carrying a field its op does not read (READS, every
    offending field of every step named in the one refusal) -- and a create key a reference cannot
    name: one that is not a string, which "{{key}}" never matches, one that is only digits, which a
    bare reference reads as an issue number where the number test accepts it, and which no reader
    can tell from one where it does not, or one holding a character other than a letter, a digit or
    an underscore, which the placeholder pattern never matches either. A key an earlier create step
    already declared is refused too, naming both steps: ids binds one number per key, so the later
    create's is what every reference to that key resolves to, the earlier issue's body included.

    Refused here too is a field whose type the run then assumes: a create title that is not a
    string, which gh takes as one, a body_file that is not one, which manifest_file joins to a path,
    and a labels, add_labels or remove_labels that is not a list of strings -- see label_list. The
    title is typed on a create only; body_file and the three label fields are typed on any op, so a
    wrongly typed one of those on an op that does not read it is named for its type as well as for
    being unread, and a wrongly typed title on any other op once, as unread. A wrongly-typed field
    that is truthy passes the required-field line above, which tests exactly that; a falsy one it
    catches where that op requires it, so `title: 0` is refused there as a missing title rather than
    as a type. A label field is required by no op, and body_file by every op but edit and subissue,
    so a falsy body_file on either is caught by nothing -- and read by nothing either, the step loop
    testing it the same way; a subissue, which renders nothing it carries, needs none at all.
    Refused as well is a body_file whose file name, casefolded, takes the rendered- prefix render
    writes its own output under: the file name of the path the step spells, so a `./`, a `..`
    segment, either separator, a drive, a directory or an absolute path in front of that name is the
    same refusal. A body_file is a bare file name beside the manifest, so three more shapes are
    refused: one PureWindowsPath reads as anything but that one name -- either separator, a drive,
    more than one component -- one holding a colon, and one ending in a space or a dot, which
    Windows drops when it opens a name, so that two step spellings are one file there and two files
    on POSIX. That reading is Windows' on every platform: `a\\b.md` is one name on POSIX and two on
    Windows. On Windows a colon never names a plain file beside anything, `C:x.md` being that
    drive's current directory and `ab:c.md` an alternate data stream of a file `ab`. render writes
    its output under "rendered-" + the name the step spells, which sends a subdirectory body into a
    directory nobody made and "a/../x.md" onto x.md itself. A name the rendered- check refuses is
    named as that, the nearer of the two diagnoses.

    Left to the step loop, each of these raised where the step used the field -- after the steps
    before it had applied. A create with no key was the worst: `gh issue create` ran, then the key
    raised, leaving an issue on the tracker that nothing recorded. A `labels` string raised
    nowhere: it sent one --label per character, and the create failed on labels no repository
    has."""
    bad, declared = [], {}  # declared: create key -> the step that bound it
    for i, s in enumerate(steps, 1):
        op = s.get("op")
        who = s.get("key") if op == "create" else s.get("issue")  # a create's identity is its key
        where = " ".join(str(x) for x in (op, who or s.get("issue") or s.get("key")) if x)
        if not op:
            bad.append(f"step {i}: no op")
        elif not isinstance(op, str) or op not in REQUIRED:  # a list or dict op is unhashable
            bad.append(f"step {i} ({where}): unknown op {op}")
        else:
            bad += [f"step {i} ({where}): no {f}" for f in REQUIRED[op] if not s.get(f)]
            bad += [f"step {i} ({where}): {f!r} is not a field {op} reads" for f in s if f not in READS[op]]
            key = s.get("key")
            if op == "create" and key and not isinstance(key, str):  # "{{key}}" matches a string only
                bad.append(f"step {i} ({where}): key {key!r} is not a string")
            elif op == "create" and key and key.isdecimal():  # resolve_ref reads a bare "43" as issue 43
                bad.append(f"step {i} ({where}): key {key!r} is only digits"
                           + (f", and a bare reference to it would name issue #{int(key)}"
                              if is_issue_number(key) else ""))
            elif op == "create" and key and not re.fullmatch(r"\w+", key):  # "{{c-d}}": no match
                bad.append(f"step {i} ({where}): key {key!r} is not only letters, digits and "
                           "underscores, so no placeholder can name it")
            if op == "create" and key and isinstance(key, str):  # ids binds one number per key
                if key in declared:
                    bad.append(f"step {i} ({where}): key {key!r} is already declared by step {declared[key]}")
                else:
                    declared[key] = i
            if op == "create" and s.get("title") and not isinstance(s["title"], str):  # gh takes a string
                bad.append(f"step {i} ({where}): title {s['title']!r} is not a string")
            if s.get("body_file") and not isinstance(s["body_file"], str):  # manifest_file joins it to a path
                bad.append(f"step {i} ({where}): body_file {s['body_file']!r} is not a string")
            elif s.get("body_file") and pathlib.PureWindowsPath(s["body_file"]).name.casefold().startswith("rendered-"):
                bad.append(f"step {i} ({where}): body_file '{s['body_file']}' takes the rendered- prefix "
                           "render writes its output under")
            elif s.get("body_file") and (pathlib.PureWindowsPath(s["body_file"]).name != s["body_file"]
                                         or ":" in s["body_file"] or s["body_file"][-1] in " ."):
                bad.append(f"step {i} ({where}): body_file '{s['body_file']}' is not a bare file name: "
                           "a body file holds no separator and no colon, does not end in a space or a "
                           "dot, which Windows drops when it opens a name, and sits beside the manifest")
            bad += [f"step {i} ({where}): {f} {s[f]!r} is not a list of strings"
                    for f in LABEL_FIELDS if f in s and not label_list(s[f])]
    if bad:
        raise SystemExit("a malformed step, nothing applied: " + ", ".join(bad))


def check_readable(d, steps):
    """Refuse, by step and name and before any step runs, a body file that cannot be read as UTF-8.

    Every check below and every step opens the body files the same way, so one that is missing, is
    a directory, or is not UTF-8 text ended the run on a traceback: check_order's read of a comment
    body, or check_refs's of every other. Opening each one here, once, is what leaves those later
    reads reachable only with a file that has already opened. A path outside the manifest directory
    raises manifest_file's own refusal, which says more than a read error would. An OSError's own
    message carries the absolute path, so a read error is named by its strerror -- here and in
    check_unattended's own read, above.
    This runs after check_fields, which is what makes every body_file here a string.
    Every unreadable file is named in one refusal, and nothing is applied."""
    bad = []
    for i, s in enumerate(steps, 1):
        name = s.get("body_file")
        if not name:  # an edit or a subissue needs none; the step loop reads none a step lacks
            continue
        try:
            manifest_file(d, name).read_text(encoding="utf-8")
        except (OSError, ValueError) as e:  # a decode failure is a UnicodeDecodeError, a ValueError
            where = " ".join(str(x) for x in (s["op"], s.get("issue") or s.get("key")) if x)
            bad.append(f"step {i} ({where}): {name}: "
                       f"{e.strerror if isinstance(e, OSError) else e}")
    if bad:
        raise SystemExit("an unreadable body file, nothing applied:\n  " + "\n  ".join(bad))


# The two file attributes an append-open is blind to. Measured on Windows: an existing hidden or
# system file takes an append-open and refuses write_text -- CreateFile replaces one only when the
# new file is created carrying that same attribute, and a truncating write asks for none.
# st_file_attributes is Windows' alone; the constants are defined on every platform.
HIDDEN_OR_SYSTEM = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM


def check_writable(d, steps):
    """Refuse, by step and name and before any step runs, a rendered copy's path render cannot write.

    render writes its output beside the body file it reads, as rendered-<name>, and a directory or a
    read-only file already at that path ended the run inside the step loop, with the steps before it
    applied -- the write side of the read check above. Every op but subissue renders the body file it
    carries, a subissue renders none, and the deferred pass re-renders a body its own step already
    rendered, so the steps probed here are every render the run makes.
    The probe is an open for append: it neither truncates a rendered copy an earlier run left nor
    writes a byte to one, and the empty file it creates where nothing was there it removes again, so
    the contents a check that passes leaves behind are the contents it found -- the directory's own
    mtime aside, which the create and the remove bump on both platforms. manifest_file resolves the
    path inside the guard, so its confinement refusal passes through as it is, being a SystemExit,
    and an OSError it raises is named here like any other. An OSError's own message carries the
    absolute path, so a failure is named by its strerror -- "Is a directory" for a directory on
    Linux, "Permission denied" for one on Windows and for a read-only file on either.
    A hidden or system file is the shape the append is blind to -- it opens, and render's write is
    what fails -- so that one is read from the file attributes, on the platform that keeps them, and
    named in words: there is no OSError to take a strerror from. See HIDDEN_OR_SYSTEM.
    What is at the path is looked at before the open, since two shapes the open cannot judge sit
    there: a FIFO blocks an open for append until a reader comes, and a hard link resolves to
    itself, so the confinement has nothing to follow and render would write through it into the
    file it shares an inode with. A path that is neither a regular file nor a directory is refused
    as not a regular file, and a regular file with more than one link as a hard link, both in words
    and with no open; a directory still meets the open and is named by its strerror.
    Every step whose rendered copy render cannot write is named in one refusal, and nothing is
    applied."""
    bad = []
    for i, s in enumerate(steps, 1):
        name = s.get("body_file")
        if not name or s["op"] == "subissue":  # a subissue renders nothing, whatever it carries
            continue
        where = " ".join(str(x) for x in (s["op"], s.get("issue") or s.get("key")) if x)
        try:
            out = manifest_file(d, "rendered-" + name)  # render's own path, confined the same way
            existed = out.exists()
            if existed:  # what is there is looked at before it is opened
                st = out.stat()
                if not stat.S_ISREG(st.st_mode) and not stat.S_ISDIR(st.st_mode):
                    bad.append(f"step {i} ({where}): rendered-{name}: not a regular file")
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    bad.append(f"step {i} ({where}): rendered-{name}: a hard link, shared with another path")
                    continue
            out.open("a").close()
            if not existed:
                out.unlink()
            elif getattr(out.stat(), "st_file_attributes", 0) & HIDDEN_OR_SYSTEM:
                bad.append(f"step {i} ({where}): rendered-{name}: a hidden or system file, which a "
                           "write replaces only with that attribute")
        except OSError as e:
            bad.append(f"step {i} ({where}): rendered-{name}: {e.strerror}")
    if bad:
        raise SystemExit("an unwritable rendered copy, nothing applied:\n  " + "\n  ".join(bad))


def check_order(d, steps):
    """Refuse a manifest whose state label lands before its **Triage** comment.

    The shape gate runs on the `labeled` event and demotes a promotion with no provenance
    comment, so an edits-first manifest silently un-promotes everything it just promoted.
    Refusing here beats reordering: a manifest may legitimately want an edit before some
    unrelated comment, and only the author knows which.
    A comment counts as provenance when its first line, normalized as the comment step posts it
    (normalize_triage), trims to **Triage** -- the gate's own reading, so the two cannot disagree.
    """
    triage_at, promote_at = {}, {}
    for i, s in enumerate(steps):
        key = str(resolve_ref(s.get("issue") or s.get("key") or "", KEYS_AS_NAMES))  # "{{c}}", "c" -> c; "042" -> 42
        if s["op"] == "comment" and s.get("body_file"):
            first = normalize_triage(manifest_file(d, s["body_file"]).read_text(encoding="utf-8")).split("\n")
            if first and first[0].strip() == "**Triage**":
                triage_at.setdefault(key, i)
        elif s["op"] == "edit" and any(l.casefold() in STATES for l in s.get("add_labels", [])):
            promote_at.setdefault(key, i)
    bad = [k for k, i in promote_at.items() if k in triage_at and triage_at[k] > i]
    if bad:
        raise SystemExit(
            "manifest order: " + ", ".join(f"issue {k}" for k in bad) +
            " label the issue before posting its **Triage** comment.\n"
            "The shape gate fires on `labeled` and demotes a promotion whose provenance comment\n"
            "is not there yet. Move each comment step ahead of that issue's edit step.")


def check_refs(d, steps):
    """Refuse, by step and before any gh read, a reference to a key no create step binds in time.

    A create binds its key for every step after it. So an issue, child or parent that is not a
    number must name a key an earlier create declares, and so must a placeholder in a comment,
    edit or close body, which render resolves as that step runs. A create body may name any key a
    create declares: the final pass re-renders it once the creates have run. Left to the run, each
    of these failed mid-run, after the steps before it had applied -- a bare KeyError, or render's
    refusal -- and a create body's was posted literally and refused after every step had.
    A reference that resolves below 1 is refused as well, by step and field: GitHub resolves no
    issue there, and a subissue's child read answers a 404 that api() only warns about.
    A create's title is refused for holding a placeholder at all, whether or not a create declares
    that key: gh posts a title as written, and the final pass re-renders a body, not a title.
    A body is read through placeholders(), render's own reading, so the two cannot disagree.
    Every violation is named in one refusal, and nothing is applied."""
    every = {s["key"] for s in steps if s["op"] == "create"}
    bound, bad = set(), []
    for i, s in enumerate(steps, 1):
        op, name = s["op"], s.get("body_file")
        where = " ".join(str(x) for x in (op, s.get("issue") or s.get("key")) if x)
        fields = ("issue", "child", "parent") if op == "subissue" else ("issue",)  # main's reads
        for f in (f for f in fields if f in s):
            if isinstance(s[f], bool) or not isinstance(s[f], (int, str)):  # resolve_ref passes it on
                bad.append(f"step {i} ({where}): {f} {s[f]!r} is neither an issue number nor a key")
                continue
            key = resolve_ref(s[f], KEYS_AS_NAMES)  # an int for a number, else the key it names
            if isinstance(key, str) and key not in bound:
                bad.append(f"step {i} ({where}): {f} {s[f]!r} names no key an earlier create "
                           "step declares")
            elif isinstance(key, int) and key < 1:  # the number the run sends gh
                bad.append(f"step {i} ({where}): {f} {s[f]!r} resolves to {key}, and no issue is "
                           "numbered below 1")
        if op == "create":
            titled = sorted(placeholders(s["title"]))
            if titled:
                refs = ", ".join("{{" + k + "}}" for k in titled)
                bad.append(f"step {i} ({where}): title {s['title']!r} holds {refs}, and a title is "
                           "literal text: put the reference in the body, which the final pass "
                           "re-renders, or spell a literal one without its braces")
        if name:
            text = manifest_file(d, name).read_text(encoding="utf-8")
            missing = sorted(placeholders(text) - (every if op == "create" else bound))
            if missing:
                refs = ", ".join("{{" + k + "}}" for k in missing)
                scope = "" if op == "create" else "earlier "
                bad.append(f"step {i} ({where}): body {name} names {refs}, which no {scope}create "
                           "step declares")
        if op == "create":
            bound.add(s["key"])
    if bad:
        raise SystemExit("a bad reference, nothing applied:\n  " + "\n  ".join(bad))


def check_forbidden(d, steps):
    """Refuse, before any gh call, a manifest whose posted text the private token list matches.

    Each step's body file and each create step's title goes, one call each, to `check` mode of the
    forbidden-tokens.py beside this script (run with sys.executable). Exit 1 refuses naming each
    report line by step and file name or 'title', `stdin` swapped for the label; the matched text
    never prints, and a line of any other shape is not relayed. Exit 2, any other nonzero exit
    (an exit 1 with no recognised report line included), or
    no matcher beside this script refuses too, relaying the matcher's stderr cause or what was
    missing; so does a matcher call that runs past HELPER_TIMEOUT (30) seconds.
    --no-forbidden-check skips all of it, the lookup included, and says so once; so does a binding
    that turns the check off -- see check_off.
    """
    if "--no-forbidden-check" in sys.argv:
        print("forbidden-token check skipped: --no-forbidden-check")
        return
    if check_off():
        print('forbidden-token check skipped: the binding declares ship.forbidden_check = "off"')
        return
    if not MATCHER.is_file():
        raise SystemExit(f"forbidden-token check refused, nothing applied: {MATCHER.name} is not beside this script")
    report = re.compile(r"stdin(:\d+)?(: entry \d+(, across a line break)?)")
    bad = []
    for i, s in enumerate(steps, 1):
        texts = []
        if s.get("body_file"):
            texts.append((s["body_file"], manifest_file(d, s["body_file"]).read_text(encoding="utf-8-sig")))
        if s["op"] == "create":
            texts.append(("title", s["title"]))
        for label, text in texts:
            try:
                r = MATCH_RUN([sys.executable, str(MATCHER), "check"], input=text.encode("utf-8"), capture_output=True,
                              timeout=HELPER_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise SystemExit(f"forbidden-token check refused, nothing applied: the matcher timed out after {HELPER_TIMEOUT} s")
            found = []
            if r.returncode == 1:
                for line in r.stdout.decode("utf-8", "replace").splitlines():
                    m = report.fullmatch(line)
                    if m:
                        found.append(f"step {i} {label}{m.group(1) or ''}{m.group(2)}")
                bad += found
            if r.returncode != 0 and not found:
                cause = r.stderr.decode("utf-8", "replace").strip() or "no stderr"
                raise SystemExit(f"forbidden-token check refused, nothing applied: matcher exit {r.returncode}: {cause}")
    if bad:
        raise SystemExit("the private token list matches the text of this manifest, nothing applied:\n"
                         + "\n".join(bad))


def check_off():
    """True when the working directory's binding declares ship.forbidden_check = "off", its
    repo.slug is the repository this run posts to, compared case-insensitively as GitHub does, and
    `ouro-binding.py check` passes it.

    The key is that repository's policy, so a run from its checkout with --repo naming another
    keeps the check, and says so. No binding, a binding that fails check, a failed read, or any
    other value keeps it silently: the run is then the one the key's absence makes. Each binding
    call is bounded at HELPER_TIMEOUT (30) seconds, and one that runs past it is a failed read."""
    def binding(*args):
        try:
            return MATCH_RUN([sys.executable, str(BINDING), *args], capture_output=True, encoding="utf-8",
                             errors="replace", timeout=HELPER_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None

    def get(key):
        r = binding("get", key)
        return r.stdout.removesuffix("\n") if r is not None and r.returncode == 0 else None
    if get("ship.forbidden_check") != "off":
        return False
    slug = get("repo.slug")
    if slug is None:
        return False
    if slug.encode().lower() == str(REPO).encode().lower():
        r = binding("check")
        return r is not None and r.returncode == 0
    print(f'ship.forbidden_check = "off" covers the binding\'s {slug}, not {REPO}: the forbidden-token check runs')
    return False


def check_bodies(d, steps):
    """Refuse a manifest that would blank or gut an issue body, before any step runs.

    A transform that failed upstream leaves an empty file and `--body-file` uploads it as a blank
    body, so every step's body_file must hold more than whitespace. An edit that replaces an
    existing issue's body keeps the current one beside the manifest as <issue>.body.before.md and
    is refused when the new file (placeholders not yet rendered) is under half that size, unless
    the step carries allow_shrink: true. An existing <issue>.body.before.md is kept, not rewritten:
    a re-run after a partial apply must keep the original body, not the one its first run set, so
    a manifest directory must not ship a file of that name, which this takes for its own earlier
    backup. A link at that path is refused as a link before anything resolves it; any other shape
    is a manifest-named path like any other, so it goes through manifest_file, and what is already
    there is looked at before it is kept or written. This run only ever writes
    a regular file there, so nothing else is its backup: a symlink out of the directory would be
    written through, one inside onto a path the run overwrites or another edit's backup; a
    directory would leave the edit no backup; and a FIFO or a hard link would be kept as the backup
    though this run never wrote it. Every such path is named by step, name and reason in one
    refusal, before any issue is read, and a regular file there is kept. An edit of an issue this
    manifest creates gets only the empty check: its current body is this manifest's own create
    file. A dry run reads no issue, writes no before-copy and does not look at these paths either.
    """
    empty = [f"step {i} ({' '.join(str(x) for x in (s['op'], s.get('issue') or s.get('key')) if x)}): {s['body_file']}"
             for i, s in enumerate(steps, 1)
             if s.get("body_file") and not manifest_file(d, s["body_file"]).read_text(encoding="utf-8").lstrip("﻿").strip()]
    if empty:
        raise SystemExit("empty body file, nothing applied: " + ", ".join(empty))
    edits = [(i, s) for i, s in enumerate(steps, 1)
             if s["op"] == "edit" and s.get("body_file")
             and (isinstance(s.get("issue"), int) or is_issue_number(str(s.get("issue"))))]
    if edits and DRY:
        print("  dry run: no issue body read, so the before-copies and the shrink check are skipped")
        return
    befores, bad = {}, []
    for i, s in edits:
        name = f"{s['issue']}.body.before.md"
        where = f"step {i} (edit {s['issue']}): {name}"
        try:
            # The link itself, dangling or not, is never a backup this wrote, wherever it points. It is
            # named before anything resolves it: resolving a dangling link is platform-dependent -- on
            # Windows a target stored in 8.3 short form (a service account's temp directory) stays
            # short while the directory resolves long, and an in-directory link reads as outside.
            if (d / name).is_symlink():
                bad.append(f"{where}: a symlink (to {(d / name).readlink()}), which this never writes as a backup")
                continue
            before = manifest_file(d, name)
            if before.exists():
                st = before.stat()
                if stat.S_ISDIR(st.st_mode):
                    bad.append(f"{where}: a directory, which leaves the edit no backup")
                elif not stat.S_ISREG(st.st_mode):
                    bad.append(f"{where}: not a regular file")
                elif st.st_nlink > 1:
                    bad.append(f"{where}: a hard link, shared with another path")
        except SystemExit:  # manifest_file's confinement, named here with its step rather than raised alone
            bad.append(f"{where}: resolves outside the manifest directory")
            continue
        except OSError as e:
            bad.append(f"{where}: {e.strerror}")
            continue
        befores[i] = before
    if bad:
        raise SystemExit("a before-copy path that cannot hold a backup, nothing applied:\n  " + "\n  ".join(bad))
    shrunk = []
    for i, s in edits:
        current = gh("issue", "view", str(s["issue"]), "--json", "body", "--jq", ".body", capture=True)
        before = befores[i]
        if not before.exists():  # a re-run after a partial apply keeps the original, not its edit
            before.write_text(current, encoding="utf-8")
        new = len(manifest_file(d, s["body_file"]).read_text(encoding="utf-8"))
        if new < len(current) / 2 and s.get("allow_shrink") is not True:
            shrunk.append(f"step {i} (edit {s['issue']}): {s['body_file']} is {new} chars, the current body {len(current)}")
    if shrunk:
        raise SystemExit(
            "body shrinks below half, nothing applied: " + "; ".join(shrunk) + ".\n"
            "Each current body is kept beside the manifest as <issue>.body.before.md, unless one was\n"
            "already there from an earlier run.\n"
            "Set allow_shrink: true on a step whose cut is meant.")


def gh(*args, capture=False):
    cmd = ["gh", *args, "-R", REPO]
    if DRY:
        print("  $", " ".join(cmd)); return ""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout.strip()


def resolve_labels(adds, removes, current, synthesize=True):
    """Labels for one edit: the additions as declared, and removals that name only labels the issue
    carries (current), matched case-insensitively as gh matches them, spelled as the issue carries
    them. Those are:
    - the declared removals it carries. One it does not carry is dropped: the repository may lack
      it, and a label the repository lacks fails the removal half it is in, not the whole call.

    With synthesize, two more that the manifest never declared:
    - when the edit adds a state, every other state it carries. Exactly one state label: the
      manifest names the state it replaces, but an issue created moments earlier may also carry
      one an intake automation stamped on the `opened` event -- invisible to whoever wrote the
      manifest.
    - when that state is not agent-ready, the trivial and checkpoint it carries: the modifiers
      ride only on agent-ready.

    A declared step passes synthesize=False under --unattended, where the manifest is model-written
    from untrusted input: a step declaring only permitted labels would otherwise still strip a
    promotion off any issue it names, and an allowlist over declared labels would bound nothing.
    The post-apply correction keeps synthesis whatever the mode, since that is what clears a state
    an automation stamped after the edit's own read. A manifest still steers that correction by
    choosing the issue and the surviving state, so unattended it may remove only the intake's own
    two labels and reports the rest -- see recheck_states."""
    added, drop = {l.casefold() for l in adds}, {l.casefold() for l in removes}
    if synthesize and added & set(STATES):   # a modifier-only edit changes no state
        drop |= set(STATES) - added
        if "agent-ready" not in added:
            drop |= set(MODIFIERS)
    return list(adds), [l for l in current if l.casefold() in drop]


def current_labels(issue):
    """Every label the issue carries right now, [] in a dry run."""
    if DRY:
        return []
    out = gh("issue", "view", str(issue), "--json", "labels", "--jq", ".labels[].name", capture=True)
    return [l.strip() for l in (out or "").splitlines() if l.strip()]


def current_states(issue):
    """State labels the issue carries right now, matched case-insensitively and spelled as it carries
    them, [] in a dry run."""
    return [l for l in current_labels(issue) if l.casefold() in STATES]


def api(*args):
    cmd = ["gh", "api", *args]
    if DRY:
        print("  $", " ".join(cmd)); return "{}"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  WARN api failed: {' '.join(cmd)}\n  {r.stderr.strip()[:300]}")
        return None
    return r.stdout


def recheck_states(moved, created, last_create, step_failed=False):
    """Remove a second state label from every issue the manifest created or moved to a state.

    An intake automation reads the labels on `opened` and stamps `needs-triage` when it sees no
    state, so its stamp can land after an edit step's own read and leave two. This waits until
    INTAKE_WAIT seconds have passed since the last create (no wait when the manifest created
    nothing), re-reads the state labels of each issue, and where one carries more than one state
    removes every state but the one the manifest set, matched case-insensitively, through
    resolve_labels, printing each correction. An issue whose states do not include one the manifest
    set -- a created issue it set no state on -- is reported, not changed. A clean check prints one
    line. Unattended it may remove only needs-triage and needs-ruling: it still clears the stamp it
    exists for, and reports rather than removes any other state, since the manifest chooses both the
    issue this reads and the state that survives here. A stamp that lands after the re-read is left
    to the label-invariant sweep, which reports two state labels as a conflict.
    A re-read that fails is reported, saying whether every step applied (step_failed), and does not
    fail the run. A correction that fails is reported and the next issue is still re-read; after the
    last one this exits nonzero naming each issue it could not correct.
    A dry run prints the wait and the issues, and sleeps and reads nothing.
    main() runs it after a step that fails too, over the issues created or moved so far, and then
    exits nonzero with that step's failure, whatever this raised: a failed step does not leave an
    earlier issue's late stamp in place.
    """
    issues = [n for n in dict.fromkeys([*created, *moved]) if n]  # a dry-run step's issue can be 0
    if not issues:
        return
    wait = max(0, last_create + INTAKE_WAIT - time.monotonic()) if created else 0
    print(f"[check] wait {wait:.0f}s, then re-read the state labels of " + ", ".join(f"#{n}" for n in issues))
    if DRY:
        return
    if wait:
        time.sleep(wait)
    clean, stuck = True, []
    for n in issues:
        try:
            states = current_states(n)
        except (SystemExit, Exception) as e:  # a failed re-read is reported, not a failed apply
            print(f"  state re-check of #{n} failed ({'after a failed step' if step_failed else 'every step applied'}): {e}")
            clean = False
            continue
        if len(states) < 2:
            continue
        clean = False
        kept = [l for l in states if l.casefold() == moved.get(n, "").casefold()]  # as the issue spells it
        if not kept:
            print(f"  #{n} carries {', '.join(states)} and the manifest set {moved.get(n) or 'no state'} on it: reported, not changed")
            continue
        _, removes = resolve_labels(kept, [], states)
        if UNATTENDED:
            # Ruled: unattended this may clear only the intake's own two labels. Synthesis stays --
            # it is what clears a state stamped after the edit's own read -- but the manifest steers
            # this by choosing the issue and the state that survives, so a promotion, a human-ready
            # or a modifier orphaned beside it is reported and left to the label-invariants gate.
            left = [l for l in removes if l.casefold() not in UNATTENDED_LABELS]
            removes = [l for l in removes if l.casefold() in UNATTENDED_LABELS]
            if left:
                print(f"  #{n} also carries {', '.join(left)}, which unattended mode may not remove: reported, not changed")
            if not removes:
                continue
        try:
            gh("issue", "edit", str(n), *(a for l in removes for a in ("--remove-label", l)))
        except (SystemExit, Exception) as e:  # the next issue still gets its re-check
            print(f"  #{n}: removing {', '.join(removes)} failed, so it still carries {', '.join(states)}: {e}")
            stuck.append(n)
            continue
        print(f"  #{n}: removed {', '.join(removes)}, kept {kept[0]}")
    if clean:
        print("  no re-read issue carries more than one state label")
    if stuck:
        raise SystemExit("state re-check: could not remove a second state label from " + ", ".join(f"#{n}" for n in stuck))


# The most digits an issue number is spelled in. Nine is orders of magnitude past the largest
# number GitHub's busiest repositories carry, and far short of int()'s own 4300-digit limit.
ISSUE_DIGITS = 9


def is_issue_number(ref):
    """Whether a string is an issue number: ASCII decimal digits, at most ISSUE_DIGITS of them.

    One reading for every field that takes one."""
    return ref.isascii() and ref.isdecimal() and len(ref) <= ISSUE_DIGITS


def resolve_ref(ref, ids):
    """An issue reference as a step names it -- a step's issue, or a subissue's child or parent -- as a
    number: an int as it is, an issue-number string as its int, "{{key}}" or a bare key as the issue
    this manifest created under that key. One resolver for every field, so a spelling one of them
    accepts, the others accept too. A digit string the number test rejects is read as a key."""
    if not isinstance(ref, str):
        return ref
    m = re.fullmatch(r"\{\{(\w+)\}\}", ref)
    return ids[m.group(1)] if m else (int(ref) if is_issue_number(ref) else ids[ref])


class _KeysAsNames(dict):
    """Answers every key with itself. Resolving through it names a created issue by its key, before
    any number exists -- so the pre-run checks see two spellings of one issue as one issue."""

    def __missing__(self, key):
        return key


KEYS_AS_NAMES = _KeysAsNames()


def placeholders(text):
    """The keys text's {{key}} placeholders name: the one reading of a placeholder, render's and
    every pre-run check's."""
    return set(re.findall(r"\{\{(\w+)\}\}", text))


def render(d, name, ids, strict=True, triage=False):
    src, out = manifest_file(d, name), manifest_file(d, "rendered-" + name)
    text = src.read_text(encoding="utf-8-sig").lstrip("﻿")
    if triage and normalize_triage(text) != text:
        text = normalize_triage(text)
        print(f"  normalized the {MARKER} marker line of {name}")
    missing = placeholders(text) - set(ids)
    if missing and strict:
        raise SystemExit(f"{name}: unresolved placeholders {sorted(missing)}")
    for k, v in ids.items():
        text = text.replace("{{" + k + "}}", str(v))
    out.write_text(text, encoding="utf-8", newline="")  # LF as written: no platform translation
    return str(out)


def main():
    d = pathlib.Path(check_args(sys.argv[1:]))
    # The file itself, ahead of its shape: utf-8-sig reads the UTF-8 byte-order mark Windows
    # PowerShell 5.1 writes whenever it is asked for UTF-8, and a file that cannot be read as JSON
    # at all -- absent, bytes that are not UTF-8, text that does not parse, an object giving a key
    # twice, nesting too deep to parse -- is a refusal by name. An OSError's own message carries
    # the absolute path, so it is named by its strerror. Windows reads a manifest directory that is
    # a junction loop as EINVAL, so a failed read asks manifest_file, which names the loop.
    try:
        steps = json.loads((d / "manifest.json").read_text(encoding="utf-8-sig"), object_pairs_hook=one_of_each_key)
    except (OSError, ValueError, RecursionError) as e:
        cause = e.strerror if isinstance(e, OSError) else e
        if isinstance(e, OSError):
            try:
                manifest_file(d, "manifest.json")
            except OSError as loop:
                if loop.errno == errno.ELOOP:
                    cause = loop.strerror
            except SystemExit:  # resolves outside the directory: the read's own cause stands
                pass
        raise SystemExit(f"manifest.json cannot be read as JSON, nothing applied: {cause}")
    shape = ([f"manifest.json is {type(steps).__name__}, not a list"] if not isinstance(steps, list)
             else [f"step {i} is {type(s).__name__}, not an object"
                   for i, s in enumerate(steps, 1) if not isinstance(s, dict)])
    if shape:  # before the checks below, each of which iterates steps and reads a step's fields
        raise SystemExit("a malformed manifest, nothing applied: " + ", ".join(shape))
    check_repo(steps)  # first: nothing below reads an issue of a repository this run cannot name
    check_unattended(d, steps)  # ahead of the checks that read issues: this one refuses outright
    check_fields(steps)  # before check_order, which reads each step's op
    check_readable(d, steps)  # after check_fields types body_file, before every read of one below
    check_writable(d, steps)  # the write side: render's own path, before the first step renders one
    check_order(d, steps)
    check_refs(d, steps)  # before check_bodies, whose shrink check reads issues
    check_forbidden(d, steps)  # before check_bodies too: a refusal makes no gh call, a read included
    check_bodies(d, steps)
    ids = {}
    deferred = []  # (issue, body_file) whose body had forward references
    moved, created, last_create = {}, [], 0  # for recheck_states: issue -> state set, created issues
    failed = None
    try:
        for i, s in enumerate(steps, 1):
            op = s["op"]
            if "issue" in s:
                s["issue"] = resolve_ref(s["issue"], ids)
            print(f"[{i}/{len(steps)}] " + " ".join(str(x) for x in (op, s.get("issue") or s.get("key")) if x))
            if op == "create":
                body = render(d, s["body_file"], ids, strict=False)  # forward refs fixed in the final pass
                args = ["issue", "create", "--title", s["title"], "--body-file", body]
                for l in s.get("labels", []):
                    args += ["--label", l]
                url = gh(*args)
                tail = url.rstrip("/").rsplit("/", 1)[-1]
                num = int(tail) if is_issue_number(tail) else 0
                if not num and not DRY:  # a dry run sends nothing, and binds 0 for every create
                    raise SystemExit(f"step {i} (create {s['key']}): gh answered no issue number, {url!r}"
                                     "; the call exited 0, so the issue may exist")
                ids[s["key"]] = num
                created.append(num or "{{" + s["key"] + "}}")
                moved.update((num, l) for l in s.get("labels", []) if l.casefold() in STATES)
                last_create = time.monotonic()
                if placeholders(manifest_file(d, s["body_file"]).read_text(encoding="utf-8")):
                    deferred.append((num, s["body_file"]))
                print(f"  -> #{num}")
            elif op == "edit":
                n = s["issue"]
                body = ["--body-file", render(d, s["body_file"], ids)] if s.get("body_file") else []
                adds = list(s.get("add_labels", []))
                removes = list(s.get("remove_labels", []))
                # Read the issue only when a state is moving or a removal is declared. A dry run reads
                # nothing, so it shows the declared removals as if the issue carried them.
                current = []
                if removes or any(l.casefold() in STATES for l in adds):
                    current = removes if DRY else current_labels(n)
                carried = {l.casefold() for l in current}
                dropped = [l for l in removes if l.casefold() not in carried]
                if dropped:
                    print(f"  #{n} does not carry {', '.join(dropped)}: not removed")
                adds, removes = resolve_labels(adds, removes, current, synthesize=not UNATTENDED)
                moved.update((n, l) for l in adds if l.casefold() in STATES)
                # Removing a state leaves no state label only when no other state the issue carried is left.
                off_state = any(l.casefold() in STATES for l in removes)
                stateless = off_state and not any(l.casefold() in STATES for l in current if l not in removes)
                # gh writes one call's additions and removals independently, and a label the
                # repository lacks fails only its own half: the body and removals go first, the
                # additions second, so the shape gate's `labeled` run reads the new body.
                if body or removes:
                    try:
                        gh("issue", "edit", str(n), *body, *(a for l in removes for a in ("--remove-label", l)))
                        # Only once it landed: the issue's body is this one, and re-rendering the
                        # create's would revert it. A call that exited nonzero leaves the edit's
                        # own body to the pass instead -- the except path below.
                        # A dry run's n is 0, the number every create it did not make binds.
                        if body and n:
                            deferred = [x for x in deferred if x[0] != n]
                    except SystemExit as e:  # gh writes the body beside the removal half: either may have landed
                        # Which is why the deferred body becomes this step's: the body half may
                        # have landed, so re-rendering the create's would revert it, and it may
                        # not have, so dropping the entry would leave a placeholder up. Sending
                        # this step's body is the one end state the manifest asked for either way.
                        if body and n:
                            deferred = [(x[0], s["body_file"]) if x[0] == n else x for x in deferred]
                        what = " and ".join((["setting the body"] if body else []) + (["removing " + ", ".join(removes)] if removes else []))
                        raise SystemExit(f"#{n}: the call {what} failed"
                                         + (f", so the additions were not sent: {', '.join(adds)}" if adds else "")
                                         + ("; its removals may have landed" if off_state else "")
                                         + (", and the issue may then carry no state label" if stateless else "") + f"\n{e}")
                if adds:
                    try:
                        gh("issue", "edit", str(n), *(a for l in adds for a in ("--add-label", l)))
                    except SystemExit as e:
                        raise SystemExit(f"#{n}: adding {', '.join(adds)} failed: the repository may lack one of them"
                                         + (", and the issue now carries no state label" if stateless else "") + f"\n{e}")
            elif op == "comment":
                gh("issue", "comment", str(s["issue"]), "--body-file", render(d, s["body_file"], ids, triage=True))
            elif op == "close":
                gh("issue", "close", str(s["issue"]), "--comment", pathlib.Path(render(d, s["body_file"], ids)).read_text(encoding="utf-8"))
            elif op == "subissue":
                child, parent = resolve_ref(s["child"], ids), resolve_ref(s["parent"], ids)
                cid = api(f"repos/{REPO}/issues/{child}", "--jq", ".id")
                if cid:
                    api("-X", "POST", f"repos/{REPO}/issues/{parent}/sub_issues", "-F", f"sub_issue_id={cid.strip()}")
            else:
                raise SystemExit(f"unknown op {op}")
    except (SystemExit, Exception) as e:  # the issues created or moved so far still get the re-render and the
        failed = e                        # state re-check; a KeyboardInterrupt is not caught, so it stops at once
    step_failed = failed is not None  # a re-render that fails below is not a step that failed
    left = []  # deferred issues whose re-render failed
    for num, name in deferred:  # after a failed step too, with the ids bound so far; each body on its own
        print(f"[final] re-render #{num or name} with the ids bound so far")
        try:
            unbound = sorted(placeholders(manifest_file(d, name).read_text(encoding="utf-8")) - set(ids))
            gh("issue", "edit", str(num), "--body-file", render(d, name, ids, strict=False))
        except (SystemExit, Exception) as e:  # the next body still gets its re-render
            print(f"  #{num}: re-rendering {name} failed: {e}")
            left.append(num)
            continue
        if unbound:  # only after a failed step: check_refs refuses a key no create step declares
            print(f"  #{num} still holds " + ", ".join("{{" + k + "}}" for k in unbound) + ", whose create never ran")
    if left and failed is None:  # a failed step's own message stays the exit
        failed = SystemExit("deferred re-render: could not re-render the body of " + ", ".join(f"#{n}" for n in left))
    print("ids:", json.dumps(ids))
    try:
        recheck_states(moved, created, last_create, step_failed)
    finally:
        if failed is not None:  # the step's failure is the exit, whatever the re-check raised; it printed its own
            raise failed


def resolve_repo():
    if "--repo" in sys.argv:
        return sys.argv[sys.argv.index("--repo") + 1]
    # The refusal carries a path, and the binding tool writes UTF-8 whatever the locale.
    r = subprocess.run([sys.executable, str(BINDING), "get", "repo.slug"], capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        # The read fails for more than a missing binding -- an invalid one, a python below the 3.11
        # floor -- and ouro-binding.py's stderr says which; the prefix must be true for all of them.
        raise SystemExit("no --repo, and reading repo.slug from the binding failed: " + r.stderr.strip())
    return r.stdout.strip().strip('"')


if __name__ == "__main__":
    check_args(sys.argv[1:])  # before the binding read, so a mistyped flag is what the run reports
    DRY = "--dry-run" in sys.argv
    UNATTENDED = "--unattended" in sys.argv
    TARGETS = sys.argv[sys.argv.index("--targets") + 1] if "--targets" in sys.argv else None
    REPO = resolve_repo()
    main()
