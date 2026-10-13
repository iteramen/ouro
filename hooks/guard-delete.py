#!/usr/bin/env python3
"""guard-delete.py -- a PreToolUse guard on Bash and PowerShell: a recursive delete removes one
literal path the session can name, with its errors visible, and a forced worktree removal or a
`git clean` of untracked files happens only in scratch.

    python3 hooks/guard-delete.py  < PreToolUse event (JSON)

Refused, with the reason on stderr and exit 2:

- a recursive delete (`rm -r`, `Remove-Item -Recurse` and its aliases, `rd /s`) whose target holds
  a glob (`*`, `?`, `[`), a command substitution, or a variable this guard cannot see assigned
  (not in its environment and not assigned earlier in the command: an unassigned variable is a
  path somewhere else), or whose target resolves to `/`, a drive root, a home directory, the
  `.claude` state under the home directory, the main checkout of the event's `cwd` or a worktree
  of that repository; a recursive `Remove-Item` with no path, which removes what a pipeline hands
  it, is refused too;
- a recursive delete whose errors are silenced on the same statement: `2>/dev/null`, `2>$null`,
  `>/dev/null 2>&1`, `&>/dev/null`, `*>$null`, `2>&-`, `|| true`, `|| :`, a pipe into `true`, `:`
  or `Out-Null`, `-ErrorAction SilentlyContinue` or `Ignore`;
- an assignment to a PowerShell variable the runtime reserves, in the `$name`, `${name}`,
  `$scope:name` or `Set-Variable` form: a ReadOnly or Constant automatic variable (`$home`,
  `$host`, `$pid`, `$error`, `$isWindows` among them), whose assignment fails without stopping
  the script and leaves the old value for every later use, or one the runtime rebinds (`$input`,
  `$args`, `$matches`, `$_`);
- `git worktree remove --force` (or `-f`), and `git clean -f` with `-d` or `-x`, from a `cwd` (or a
  `-C` directory) outside the scratch directory, unless the command carries `# delete-ok: <why>`.
  Scratch is `CLAUDE_SCRATCHPAD_DIR` when set, else the temporary directory `TMPDIR`, `TEMP` or
  `TMP` names, else `/tmp` and `/var/tmp`.

A target under scratch passes the location check and still fails the glob, variable and silence
checks. A target is resolved from the event's `cwd`, with `~`, the home variables, the
environment's variables and a variable assigned a literal earlier in the command expanded (the
glob check runs on the expanded value too), and compared case-insensitively on Windows; a
variable assigned from a create (`mktemp -d`, `New-Item` without `-Force`) holds a path that did
not exist before, so it skips the location check, and one assigned from anything else, or more
than once, is refused as a path the guard cannot name. A program handed to an inline shell
(`bash -c '...'`) is read as statements too. The
main checkout and the worktrees are read with `git -C <cwd> rev-parse --show-toplevel` and
`git worktree list --porcelain`, only when the command holds a recursive delete, and skipped where
`cwd` is not in a repository.

Stdlib only; python3 3.11+. The recorded events under tests/hooks/guard-delete/ are its test.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ouro_guard  # noqa: E402

CMD_RM = ("rd", "rmdir", "del", "erase")
PS_RM = ("remove-item", "ri") + CMD_RM
# Get-Variable | Where-Object { $_.Options -match 'ReadOnly|Constant' }, plus what the runtime rebinds.
RESERVED = ("HOME", "Host", "PID", "PSHOME", "PSStyle", "PSCulture", "PSUICulture", "PSEdition",
            "PSVersionTable", "ShellId", "Error", "ExecutionContext", "EnabledExperimentalFeatures",
            "IsWindows", "IsLinux", "IsMacOS", "IsCoreCLR", "input", "args", "matches", "_")
RESERVED_ASSIGN = re.compile(r"(?<![\w:.$])\$(?:\w+:)?\{?(" + "|".join(re.escape(n) for n in RESERVED) + r")\}?\s*=(?!=)"
                             r"|\bSet-Variable\s+(?:-Name\s+)?[\"']?(" + "|".join(re.escape(n) for n in RESERVED) + r")\b", re.IGNORECASE)
RM_RECURSIVE = re.compile(r"(?<!\S)(?:--recursive|-[a-zA-Z]*[rR][a-zA-Z]*)(?!\S)")
PS_RECURSIVE = re.compile(r"(?<!\S)-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?(?::\$true)?(?!\S)", re.IGNORECASE)
CMD_RECURSIVE = re.compile(r"(?<!\S)/s(?!\S)", re.IGNORECASE)
SILENCED = re.compile(r"2>\s*(?:/dev/null|\$null|nul\b)|>\s*/dev/null\s+2>&1|2>&1\s*>\s*/dev/null|&>\s*/dev/null|\*>\s*\$null|\d?>&-"
                      r"|-(?:ErrorAction|ea)[\s:]+(?:SilentlyContinue|Ignore|0|4)\b", re.IGNORECASE)
SWALLOWERS = ("true", ":", "out-null", "cat >/dev/null", "cat > /dev/null")
GLOB = re.compile(r"[*?\[]")
SUBSTITUTION = re.compile(r"\$\(|`")
VARIABLE = re.compile(r"\$env:(\w+)|%(\w+)%|\$\{(\w+)\}|\$(\w+)")
ASSIGNMENT = (r"(?:^|[\s;&|({])(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?|set\s+|\$)?", r"\s*=(?!=)\s*(.+?)\s*(?:[;&|]|\r?\n|$)")
LITERAL = re.compile(r"^(?:\"([^\"$`]*)\"|'([^']*)'|([^\s$`()%\"']+))$")
CREATE = re.compile(r"\b(?:mktemp|New-Item|New-TemporaryFile|mkdtemp|TemporaryDirectory)\b", re.IGNORECASE)
HOME_ROOT = re.compile(r"^(?:/(?:home|users)/[^/]+|[a-z]:/users/[^/]+|[a-z]:|)/?$", re.IGNORECASE)
PS_VALUED = ("-path", "-literalpath", "-p", "-lp", "-filter", "-include", "-exclude", "-erroraction", "-ea", "-credential", "-stream")


def norm(path):
    path = os.path.normpath(path).replace("\\", "/")
    return path.lower() if os.name == "nt" else path


def under(path, root):
    path, root = norm(path), norm(root).rstrip("/")
    return path == root or path.startswith(root + "/")


def scratch_roots():
    """CLAUDE_SCRATCHPAD_DIR alone when set, as the docstring says: on Windows TEMP is always set, and a
    union read every repository under it as scratch."""
    scratch = os.environ.get("CLAUDE_SCRATCHPAD_DIR")
    if scratch:
        return [scratch]
    roots = [r for r in (os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP")) if r]
    return roots or ["/tmp", "/var/tmp"]


def repo_roots(cwd):
    """The main checkout and every worktree of cwd's repository, or [] outside one."""
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, timeout=5)
        if top.returncode != 0:
            return []
        roots = [top.stdout.decode("utf-8", "replace").strip()]
        wt = subprocess.run(["git", "-C", cwd, "worktree", "list", "--porcelain"], capture_output=True, timeout=5)
        roots += [ln[9:].strip() for ln in wt.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("worktree ")]
        return [norm(r) for r in roots if r]
    except (OSError, subprocess.SubprocessError):
        return []


words = ouro_guard.words


def is_recursive(name, statement):
    if name == "rm":
        return RM_RECURSIVE.search(statement)
    if name in CMD_RM:
        return CMD_RECURSIVE.search(statement) or PS_RECURSIVE.search(statement)
    return PS_RECURSIVE.search(statement)


def targets(statement, name):
    """The path words of a delete statement: every word that is not an option, the value of
    `-Path` or `-LiteralPath` kept and the value of any other PowerShell option dropped."""
    ws = words(statement)[1:]
    found, i = [], 0
    while i < len(ws):
        w, lw = ws[i], ws[i].lower()
        if w == "--":
            found += ws[i + 1:]
            break
        if lw.startswith("-"):
            key, _, val = lw.partition(":")
            if name != "rm" and key in PS_VALUED:
                if key in ("-path", "-literalpath", "-p", "-lp"):
                    if val:
                        found.append(w.partition(":")[2])
                    elif i + 1 < len(ws):
                        found.append(ws[i + 1])
                        i += 1
                elif not val:
                    i += 1
        elif not (name in CMD_RM and len(w) == 2 and w.startswith("/")):
            found.append(w)
        i += 1
    return found


def expand(target, command):
    """The target with its variables expanded from the environment, or from a literal assignment
    earlier in the command; the reason a variable cannot be, or None; and whether a variable
    assigned from a create (the object `mktemp -d` or `New-Item` without `-Force` returned) stays
    unexpanded, which is the one form that needs no location check."""
    home = os.path.expanduser("~")
    opaque = False

    def value(m):
        nonlocal opaque
        name = next(g for g in m.groups() if g)
        if name.upper() in ("HOME", "USERPROFILE"):
            return home
        if name in os.environ:
            return os.environ[name]
        if os.name == "nt" and name.upper() in (k.upper() for k in os.environ):
            return next(v for k, v in os.environ.items() if k.upper() == name.upper())
        if m.group(1) or m.group(2):
            raise LookupError(f"{m.group(0)} is not set in this environment: an unassigned variable is a path somewhere else")
        a = re.search(ASSIGNMENT[0] + re.escape(name) + ASSIGNMENT[1], command, re.IGNORECASE | re.MULTILINE)
        if not a:
            raise LookupError(f"{m.group(0)} is assigned nowhere this guard can see: an unassigned variable is a path somewhere else")
        # A second assignment or an in-place change (`+=`, `.=`) is a value this guard cannot follow.
        if len(re.findall(r"(?:^|[\s;&|({])\$?" + re.escape(name) + r"\s*[-+*/.]?=(?!=)", command, re.IGNORECASE | re.MULTILINE)) > 1:
            raise LookupError(f"{m.group(0)} is assigned more than once in the command: a recursive delete names one path it can follow")
        rhs = a.group(1)
        lit = LITERAL.match(rhs)
        # After `$name =` a bare word is a command, not a path: only a quoted value is a literal there.
        if lit and not (lit.group(3) is not None and re.search(r"\$" + re.escape(name) + r"\s*=", a.group(0))):
            return next(g for g in lit.groups() if g is not None)
        if CREATE.search(rhs) and not re.search(r"(?<!\S)-Force(?!\S)", rhs, re.IGNORECASE):
            opaque = True
            return m.group(0)
        raise LookupError(f"{m.group(0)} is assigned from `{rhs}`, which is neither a literal path nor the object a create "
                          f"returned (`mktemp -d`, `New-Item` without `-Force`): a recursive delete removes a path it can name")

    try:
        expanded = VARIABLE.sub(value, re.sub(r"^~(?=[/\\]|$)", lambda _: home, target))
    except LookupError as e:
        return target, str(e), False
    return expanded, None, opaque


def check_delete(statement, line, name, command, cwd, roots):
    if not is_recursive(name, statement):
        return None
    segments = ouro_guard.split(line)
    at = next((k for k, (s, _) in enumerate(segments) if ouro_guard.strip_prefix(s.strip()) == statement), None)
    after = segments[at][1] if at is not None else ""
    following = segments[at + 1][0].strip().lower() if at is not None and at + 1 < len(segments) else ""
    if SILENCED.search(statement) or (after in ("||", "|", "|&") and following in SWALLOWERS):
        return f"a recursive delete never silences its errors: {statement}"
    ts = targets(statement, name)
    if not ts:
        return f"a recursive delete names one literal path, never what a pipeline hands it: {statement}"
    for t in ts:
        if SUBSTITUTION.search(t):
            return f"a recursive delete removes a literal path, never one a substitution computes: {t}"
        path, reason, opaque = expand(t, command)
        if reason:
            return f"{reason}: {t}"
        if GLOB.search(t) or GLOB.search(path):
            return f"a recursive delete removes a literal path, never a pattern (another run's files sit under the same root): {t}"
        if opaque:
            continue
        if not os.path.isabs(path) and not re.match(r"^[A-Za-z]:", path):
            path = os.path.join(cwd or os.getcwd(), path)
        path = norm(path)
        if HOME_ROOT.match(path) or path.rstrip("/") == norm(os.path.expanduser("~")).rstrip("/"):
            return f"a recursive delete of a root or home directory: {t}"
        if under(path, os.path.join(os.path.expanduser("~"), ".claude")):
            return f"a recursive delete inside the .claude state: {t}"
        for r in roots:
            if path.rstrip("/") == r.rstrip("/"):
                return f"a recursive delete of the checkout or a worktree ({r}); a worktree goes with git worktree remove, without --force: {t}"
    return None


def check_git(statement, command, cwd):
    ws = words(statement)
    i, c_dir = 1, None
    while i < len(ws) and ws[i].startswith("-"):
        if ws[i] in ("-C", "-c") and i + 1 < len(ws):
            if ws[i] == "-C":
                c_dir = ws[i + 1]
            i += 1
        i += 1
    rest = ws[i:]
    short = "".join(w[1:] for w in rest if w.startswith("-") and not w.startswith("--"))
    long = {w for w in rest if w.startswith("--")}
    if rest[:2] == ["worktree", "remove"] and ("f" in short or "--force" in long):
        what = "a worktree is removed without --force, so a refusal says a process sits in it or it holds work"
    elif rest[:1] == ["clean"] and ("f" in short or "--force" in long) and set("dxX") & set(short):
        what = "git clean with -d or -x removes untracked work"
    else:
        return None
    base = os.path.join(cwd or os.getcwd(), c_dir) if c_dir else (cwd or os.getcwd())
    if any(under(base, r) for r in scratch_roots()) or ouro_guard.override(command, "delete-ok"):
        return None
    return f"{what}; outside scratch it is refused unless the command carries `# delete-ok: <why>`: {statement}"


def check(tool_name, tool_input, cwd):
    command = ouro_guard.shell_command(tool_name, tool_input)
    if command is None:
        return None
    m = RESERVED_ASSIGN.search(command)
    if m:
        return (f"the command assigns ${m.group(1) or m.group(2)}, a variable PowerShell reserves: the assignment fails "
                f"without stopping the script and the old value stays for every later use; rename it ($workDir, $scratch)")
    roots = None
    for statement, line in ouro_guard.statements(command):
        name = ouro_guard.first_word(statement)
        if name == "rm" or name in PS_RM:
            if roots is None:
                roots = repo_roots(cwd or os.getcwd())
            reason = check_delete(statement, line, name, command, cwd, roots)
        elif name == "git":
            reason = check_git(statement, command, cwd)
        else:
            continue
        if reason:
            return reason
    return None


if __name__ == "__main__":
    sys.exit(ouro_guard.run(check))
