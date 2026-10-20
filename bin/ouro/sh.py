"""Process wrappers, the resolved executables, the binding loader and the dry-run seam.

The driver's failure classes are exceptions here: DriverError (exit 1), Refused (exit 2) and
Stopped (the exit code it carries). Every command the driver spawns goes through run().
Env.write() in --dry-run prints the command and runs nothing; a dry run returns before the
transitions that commit, push or merge.
"""
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
BIN = PLUGIN / "bin"
NO_ANCHOR = "no parseable anchor in the body (a backticked repo path or a quoted code fragment): the shape gate refuses a promotion without one"


class DriverError(Exception):
    """Exit 1: a script failed, a node answered nothing parseable, the platform refused."""


class Refused(Exception):
    """Exit 2: refused; a fresh run has made no branch, a resumed run leaves its branch as it stands."""


class Stopped(Exception):
    """Stopped mid-run: the run exits with the code it carries."""


def say(msg=""):
    print(f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}".rstrip(), flush=True)  # UTC, as the tracker's stamps: the log is its own timeline


_RESOLVED = {}


def which(name):
    """Resolve an executable once; a bare name never reaches subprocess on Windows."""
    if name not in _RESOLVED:
        found = shutil.which(name)
        if not found:
            raise DriverError(f"{name} is not on PATH")
        _RESOLVED[name] = found
    return _RESOLVED[name]


def argv_for(name, *args):
    path = which(name)
    if path.lower().endswith(".ps1"):
        return [which("pwsh"), "-NoProfile", "-File", path, *args]
    return [path, *args]


def script(name, *args):
    """argv for a plugin script under bin/: pwsh for .ps1, python3 for .py."""
    path = str(BIN / name)
    if name.endswith(".ps1"):
        return [which("pwsh"), "-NoProfile", "-File", path, *args]
    return [which("python3"), path, *args]


class R:
    def __init__(self, code, out, err, argv):
        self.code, self.out, self.err, self.argv = code, out, err, argv

    @property
    def text(self):
        return (self.out + self.err).strip()


def run(argv, cwd=None, input_text=None, env=None, timeout=None):
    """Run a command; output is decoded as UTF-8, never the locale's code page."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", **(env or {})}
    try:
        p = subprocess.run(argv, cwd=cwd, input=None if input_text is None else input_text.encode("utf-8"),
                           capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return R(None, "", f"timed out after {timeout}s", argv)
    except OSError as e:
        raise DriverError(f"cannot start {shlex.join(map(str, argv))}: {e}")
    return R(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), argv)


def cmdline(argv):
    return shlex.join(str(a) for a in argv)


def write_bytes(path, text):
    """Files handed to --body-file / -F are UTF-8 bytes with LF endings."""
    Path(path).write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def load_binding_module():
    spec = importlib.util.spec_from_file_location("ouro_binding", BIN / "ouro-binding.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Env:
    """What a run knows: the main checkout, the binding, the repository, the dry-run switch."""

    def __init__(self, main, binding, data, dry):
        self.main, self.ob, self.data, self.dry = Path(main), binding, data, dry
        self.slug = data["repo"]["slug"]
        self.default = data["repo"]["default_branch"]
        self.scratch = Path(tempfile.mkdtemp(prefix="ouro-run-"))
        self._viewer = None

    def close(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def lookup(self, dotted, default=None):
        try:
            return self.ob.lookup(self.data, dotted)
        except (KeyError, IndexError):
            return default

    # reads ---------------------------------------------------------------------------------
    def git(self, *args, cwd=None, check=True):
        r = run([which("git"), "-c", "core.quotepath=false", *args], cwd=cwd or self.main)
        if check and r.code != 0:
            raise DriverError(f"git {' '.join(args)} failed (exit {r.code}): {r.text}")
        return r

    def gh(self, *args, check=True):
        r = run([*argv_for("gh", *args)], cwd=self.main)
        if check and r.code != 0:
            raise DriverError(f"gh {' '.join(args)} failed (exit {r.code}): {r.text}")
        return r

    def ghr(self, *args, check=True):
        return self.gh(*args, "-R", self.slug, check=check)

    def gh_json(self, *args):
        import json
        r = self.ghr(*args)
        try:
            return json.loads(r.out)
        except ValueError:
            raise DriverError(f"gh {' '.join(args)} printed no JSON: {r.out[:200]!r}")

    def viewer(self):
        if self._viewer is None:
            self._viewer = self.gh("api", "user", "--jq", ".login").out.strip().lower()
        return self._viewer

    def approvers(self):
        return {str(a).lower() for a in self.lookup("owner.ruling_approvers", []) or []}

    def trusted(self):
        return {self.viewer(), *self.approvers()}

    # writes --------------------------------------------------------------------------------
    def write(self, desc, argv, cwd=None, input_text=None, timeout=None, check=True):
        """A command that changes something. In --dry-run: print it, run nothing, answer ok."""
        if self.dry:
            say(f"  would {desc}: {cmdline(argv)}")
            return R(0, "", "", argv)
        say(f"  {desc}")
        r = run(argv, cwd=cwd or self.main, input_text=input_text, timeout=timeout)
        if check and r.code != 0:
            raise DriverError(f"{desc} failed: {cmdline(argv)} (exit {r.code}): {r.text}")
        return r

    def body_file(self, name, text):
        p = self.scratch / name
        write_bytes(p, text)
        return str(p)

    def dead_anchors(self, body, ref, need_one=False):
        """The anchor gate's findings for a body at a ref (`Get-AnchorFindings.ps1` over the tracked files): none when every anchor line resolves; with `need_one`, a body with no parseable anchor is a finding, as the shape gate reads it."""
        q = lambda p: str(p).replace("'", "''")  # a PowerShell single-quoted literal
        bf = self.body_file("anchor-body.md", body)
        ps = self.scratch / "anchors.ps1"
        write_bytes(ps, f". '{q((BIN / 'Get-AnchorFindings.ps1').as_posix())}'\n$t = [IO.File]::ReadAllText('{q(Path(bf).as_posix())}')\n"
                        f"$r = Get-AnchorFindings -Text $t -Ref '{q(ref)}'\nWrite-Output \"COUNT: $($r.PathCount + $r.FragCount)\"\n"
                        f"$r.Findings | ForEach-Object {{ Write-Output \"FINDING: $_\" }}\n")
        r = run([which("pwsh"), "-NoProfile", "-File", str(ps)], cwd=self.main)
        if r.code != 0:
            raise DriverError(f"the anchor check failed: {r.text}")
        out = [l[len("FINDING:"):].strip() for l in r.out.splitlines() if l.startswith("FINDING:")]
        if need_one and "COUNT: 0" in r.out.splitlines():
            out.append(NO_ANCHOR)
        return out

    def comment_issue(self, n, text):
        f = self.body_file(f"comment-{n}.md", text)
        self.write(f"comment on #{n}", argv_for("gh", "issue", "comment", str(n), "-R", self.slug, "--body-file", f))

    def comment_pr(self, n, text):
        f = self.body_file(f"prcomment-{n}.md", text)
        self.write(f"comment on PR #{n}", argv_for("gh", "pr", "comment", str(n), "-R", self.slug, "--body-file", f))
