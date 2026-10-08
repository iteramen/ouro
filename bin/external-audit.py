#!/usr/bin/env python3
"""external-audit.py -- one external-CLI review round with a JSON verdict, over a diff or a claim set.

    # diff mode -- judge a change against what it must do
    python3 external-audit.py --cli grok --range <base>..<head> --goal "<what the change must do>" [--lens "..."]
    python3 external-audit.py --cli grok --range ... --goal ... --resume <id> --digest <triage.md>   # round 2+

    # claims mode -- refute assertions about code that has not been written yet
    python3 external-audit.py --cli grok --claims claims.md --goal "<what the claims underpin>"

Diff mode reviews a change. Claims mode reviews a *plan's factual assertions about the repo* --
the findings a design rests on, each stated with the file:line it cites -- and returns a
per-claim CONFIRMED / REFUTED / MISLEADING / UNVERIFIABLE verdict. Use it before committing to
a rework whose shape depends on those readings being right.

--cli selects the adapter for the external CLI that runs the round (see the ADAPTERS registry
below); an unknown one, or one whose binary is not on PATH, is a round-1 failure. --cwd sets
where that CLI runs and what it may read; the default is the repository root of the current
working directory (git rev-parse --show-toplevel), not the working directory itself. --model
defaults to "default", which passes no model flag, so the CLI's own default applies; any other
value maps to the adapter's own model flag. Claims mode makes no git call, so --cwd may sit above
several repositories when the claims span more than one.

Prints the session id (round 1 mints one) and the parsed verdict; the id to pass to --resume next
round is always the one on the "resume with" line, not necessarily this one. Exits 0 on APPROVE, 2
on REVISE, 1 on a transport/parse failure, an unknown or missing --cli, a CLI not found on PATH, or
a CLI found as a .cmd or .bat shim with an argument holding one of &|<>^% (cmd.exe's own syntax;
refused before it starts), and 3 when an APPROVE carries no evidence that anything was read - no
critique and an empty files_read. A 3 is a failed round, not a round: re-run it, and do not spend
the two-round budget on it. Every artifact (prompt, raw stdout, verdict.json) lands under
%TEMP%/external-audit-<user>/<session>/round-N/ -- never in the repo. Repo-agnostic by design.

Ported from the owner's prior single-CLI reader; the adapter seam below is what this port adds,
so a second CLI is a new Adapter subclass, not a rewrite of main().
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

# tomllib is not used here, but the floor is shared with every other bin/*.py: a script that
# imports nothing 3.11-only still needs the fleet's one interpreter contract, and importing an
# f-string-in-f-string or another 3.11+ construct below on an older python dies with a traceback
# that does not say what to install.
if tuple(sys.version_info[:2]) < (3, 11):
    sys.exit(f"external-audit.py needs Python 3.11+; this python is {sys.version_info[0]}.{sys.version_info[1]}")

# Critiques quote the diff verbatim (arrows, dashes); a cp1252 console must not crash the
# verdict printout after the round has already been paid for.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"): _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_LENS = (
    "CORRECTNESS of the change (false negatives and false positives against its stated goal, "
    "off-by-one and parsing edge cases, error paths); CONSISTENCY between the code and any docs "
    "the diff touches; and TRUTH of the comments and docs in every touched file - a comment or "
    "doc line that states something no longer true after this change is a finding even when the "
    "goal does not mention it. Name additional fronts (conventions, hygiene) with --lens."
)
PAYLOAD_CAP = 100_000  # bytes; larger diffs get truncated with a marker

VERDICT_CONTRACT = (
    '{"verdict": "APPROVE | REVISE", '
    '"files_read": ["<repo-relative path you actually opened, one per file>"], '
    '"critiques": [{"id": "finding-<n>", '
    '"severity": "blocker | major | minor", "lens": "<lens>", '
    '"claim": "<one-sentence defect statement>", '
    '"evidence": "<file:line, quoted text, or reasoning>", '
    '"probe": "<a concrete input or scenario that demonstrates it, when one exists>", '
    '"in_scope_claim": true}]}'
)

CLAIMS_LENS = (
    "TRUTH of each stated claim against the actual code. Open every cited file and read enough "
    "surrounding context to judge the substance, not the citation. Line numbers drift: if the "
    "claim is right but the line is off, that is CONFIRMED with a note, never REFUTED. Reserve "
    "MISLEADING for a claim that is literally true but supports an unsound conclusion - a real "
    "code path that cannot be reached, or a defect some other mechanism already prevents. Say "
    "UNVERIFIABLE rather than guessing, and say WHERE you looked."
)

CLAIMS_VERDICT_CONTRACT = (
    '{"verdict": "APPROVE | REVISE", '
    '"files_read": ["<repo-relative path you actually opened, one per file>"], '
    '"claims": [{"id": "<the id given in the claim set>", '
    '"status": "CONFIRMED | REFUTED | MISLEADING | UNVERIFIABLE", '
    '"evidence": "<repo-relative file:line plus the quoted code that settles it>", '
    '"note": "<one or two sentences>"}], '
    '"new_findings": [{"severity": "blocker | major | minor", "summary": "<one sentence>", '
    '"evidence": "<repo-relative file:line>", "why_it_matters": "<one sentence>"}], '
    '"verdict_reason": "<one paragraph>"}'
)


def sh(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False, cwd=cwd).stdout


def _artifact_user() -> str:
    """The per-user segment of the artifact parent folder name, so two accounts sharing one
    machine's temp dir never fight over an `external-audit` directory the other one owns --
    mkdir raising PermissionError with a bare traceback, the observed CI cause. Sanitized to
    characters safe in a path component (a domain-qualified or space-holding username is not),
    falling back to the numeric uid when the platform cannot name a user at all. A name the
    sanitizing changed carries the first 8 hex digits of its own SHA-256 too, so two names that
    sanitize alike (two non-ASCII names of one length) still get two folders."""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if not user:
        try:
            user = str(os.getuid())
        except AttributeError:
            user = "unknown"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    if safe == user:
        return user
    return f"{safe}-{hashlib.sha256(user.encode('utf-8', 'surrogatepass')).hexdigest()[:8]}"


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the CLI and everything it spawned -- POSIX: the process group it leads (started
    with start_new_session below, so os.killpg reaches every descendant, not just proc.pid);
    Windows: taskkill /T, since Windows has no such group. Shared by the --timeout kill and the
    signal handling below, so both take the same tree down the same way."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _raise_system_exit(signum: int, frame: object) -> None:
    """SIGTERM has no Python-level default action (the OS terminates the process before any of
    our code runs), so a harness kill would otherwise leave the CLI's process group orphaned --
    map it to SystemExit so the try/except around the wait loop in main() can still kill it."""
    raise SystemExit(1)


def _default_cwd() -> str | None:
    """--cwd's default: the repository root of the invocation directory (not the directory
    itself), so a run from a subdirectory still diffs/reads the whole repo. Outside a repo, or
    with no git on PATH at all (--dry-run and claims mode need neither), this answers nothing
    and the caller falls back to None -- "here", the old default -- rather than refusing."""
    if shutil.which("git") is None:
        return None
    return sh("git", "rev-parse", "--show-toplevel").strip() or None


def build_prompt(diff: str, goal: str, lens: str, digest: str | None, round_no: int) -> str:
    head = (
        f"Round {round_no}. " if round_no > 1 else ""
    ) + (
        "You are an adversarial code reviewer. Your lens: " + lens + ". "
        "Try to BREAK this change - find what fails, not what's nice. You have read-only access "
        "to the repository at the current working directory; verify every claim against the "
        "actual code (read the touched files in full, not just the diff) before you make it. "
        "Judge ONLY against the stated goal below; propose nothing beyond it. Prefer one "
        "well-evidenced blocker over ten speculative minors; a claim you could not verify in "
        "the repo is 'minor' at most and says so in its evidence. "
        "List in files_read every repository file you actually opened. An APPROVE that names "
        "none is discarded unread - if you did not open the files, say that instead of approving."
    )
    parts = [head, "", "=== GOAL (what the change must do) ===", goal.strip(), ""]
    if digest:
        parts += ["=== TRIAGE OF YOUR PRIOR CRITIQUES (accepted -> how it changed; rejected -> why) ===",
                  digest.strip(), "", "Re-review the revised change under your lens.", ""]
    parts += ["=== DIFF ===", diff, "=== END DIFF ===", "",
              "Reply with ONLY this JSON object - no prose, no fences:", VERDICT_CONTRACT]
    return "\n".join(parts)


def build_claims_prompt(claims: str, goal: str, lens: str, digest: str | None, round_no: int) -> str:
    head = (
        f"Round {round_no}. " if round_no > 1 else ""
    ) + (
        "You are an adversarial technical reviewer with read-only access to the tree at the "
        "current working directory - which may contain SEVERAL repositories. Below is a set of "
        "factual claims a plan makes about that code. You are NOT judging the plan, its design "
        "or its priorities; you are judging ONLY whether each claim is true. Your job is to "
        "REFUTE them. Your lens: " + lens + " "
        "List in files_read every repository file you actually opened - an approval that names "
        "none is discarded unread, because it is indistinguishable from not having looked. "
        "If a cited path does not exist, search the "
        "tree for the symbol before concluding it is absent: a claim you cannot locate is not "
        "thereby false, and saying so costs a round."
    )
    parts = [head, "", "=== WHAT THESE CLAIMS UNDERPIN (context only - not the thing you judge) ===",
             goal.strip(), ""]
    if digest:
        parts += ["=== TRIAGE OF YOUR PRIOR VERDICTS (accepted -> how the claim changed; rejected -> why) ===",
                  digest.strip(), "", "Re-judge the revised claim set.", ""]
    parts += ["=== CLAIMS ===", claims, "=== END CLAIMS ===", "",
              "Return one entry per claim id above - none omitted. The verdict is REVISE if any "
              "claim is REFUTED or MISLEADING, or if you found a blocker-severity new finding.",
              "", "Reply with ONLY this JSON object - no prose, no fences:", CLAIMS_VERDICT_CONTRACT]
    return "\n".join(parts)


# A lone backslash inside a JSON string is an invalid escape, and a reviewer writes one whenever
# its evidence quotes a Windows path or a substitution like `Paths with \ -> /`. json.loads then
# refuses the whole object and a completed round is discarded over one character of prose.
# The alternation consumes a VALID escape first, so an already-escaped \\ is never touched -
# scanning character by character would mangle the second half of every `C:\\path`. A \u is
# valid only with four hex digits after it; a bare one, as in `C:\dev\utils`, is a lone backslash.
_ESCAPE_OR_LONE_BACKSLASH = re.compile(r"\\([\"\\/bfnrt]|u[0-9a-fA-F]{4})|\\")


def _loads_tolerating_bare_backslash(text: str) -> dict | None:
    # Strict first: a well-formed verdict is never rewritten.
    repaired = _ESCAPE_OR_LONE_BACKSLASH.sub(
        lambda m: m.group(0) if m.group(1) else r"\\", text)
    for candidate in (text, repaired):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def parse_verdict(text: str) -> dict | None:
    # A CLI may prefix tool-use narration; the verdict is the LAST {...} block in the text.
    candidates = re.findall(r"\{.*\}", text, flags=re.S)
    for cand in reversed(candidates):
        cand = re.sub(r"^```(?:json)?|```$", "", cand.strip(), flags=re.M).strip()
        obj = _loads_tolerating_bare_backslash(cand)
        if obj is None:
            # the greedy match may have swallowed narration braces; try the shortest tail
            m = re.search(r"\{\s*\"verdict\".*\}\s*$", cand, flags=re.S)
            if not m:
                continue
            obj = _loads_tolerating_bare_backslash(m.group(0))
            if obj is None:
                continue
        if isinstance(obj, dict) and "verdict" in obj:
            return obj
    return None


# --- the adapter seam ------------------------------------------------------------------------
# One entry per external CLI, selected by --cli. Each adapter states how a session starts (an id
# this driver mints and passes via argv(), or one the CLI mints itself and prints in its own
# output -- grok, claude and copilot accept the driver's minted id, so session_from_output() is
# not overridden for them; codex mints its own thread id instead and overrides it, read back from
# its event stream after the round), the resume form, the flags
# that deny every write, how the prompt travels (a file, or stdin when stdin_prompt is set --
# never argv, since Windows caps a command line near 32K characters), where the verdict text lands
# before the parse above reads it, and how a model is passed.
class Adapter:
    name = ""
    binary = ""
    #: True when the adapter's argv omits the prompt file and main() pipes it over stdin instead.
    stdin_prompt = False

    def argv(self, prompt_path: Path, session: str, resume: bool, model: str) -> list[str]:
        raise NotImplementedError

    def verdict_text(self, stdout_text: str, out_dir: Path) -> str:
        """Where the verdict text lands before the parse reads it: stdout by default. An adapter
        whose verdict lands in a JSON envelope field or a file overrides this."""
        return stdout_text

    def session_from_output(self, stdout_text: str) -> str | None:
        """The session id to report and resume with, read back from the CLI's own output -- for
        a CLI that mints its own id rather than accepting the one this driver mints and passes
        via argv(). None (the default) means the driver's minted id is authoritative, as it is
        for grok, claude and copilot; codex overrides this instead. main() calls this after the
        round, not at argv()."""
        return None


class GrokAdapter(Adapter):
    """As the prior single-CLI reader ran it: the prompt file, -s to mint a session and -r to
    resume, its disallowed-tools list, and the last JSON object on stdout. Model flag -m (grok
    --help, 1.0.34)."""

    name = "grok"
    binary = "grok"
    DISALLOWED = "write,search_replace,run_terminal_command,spawn_subagent"

    def argv(self, prompt_path: Path, session: str, resume: bool, model: str) -> list[str]:
        cmd = ["grok", "--prompt-file", str(prompt_path), ("-r" if resume else "-s"), session,
               "--disallowed-tools", self.DISALLOWED]
        if model and model != "default":
            cmd += ["-m", model]
        return cmd


class ClaudeAdapter(Adapter):
    """-p prints one JSON result object and exits; the prompt travels on stdin, never argv.
    --session-id mints round 1's id (the driver's, as for Grok); -r/--resume repeats it on round
    2+. --tools limits the round to the three read-only tools; --permission-mode dontAsk and
    --permission-prompts none deny anything that would otherwise prompt; --safe-mode disables
    CLAUDE.md, skills, plugins, hooks and MCP servers, so the reader brings neither the invoking
    session's hooks nor the ouro plugin itself into its round. Model flag --model."""

    name = "claude"
    binary = "claude"
    stdin_prompt = True
    TOOLS = "Read,Grep,Glob"

    def argv(self, prompt_path: Path, session: str, resume: bool, model: str) -> list[str]:
        cmd = ["claude", "-p", "--output-format", "json",
               ("--resume" if resume else "--session-id"), session,
               "--tools", self.TOOLS, "--permission-mode", "dontAsk",
               "--permission-prompts", "none", "--safe-mode"]
        if model and model != "default":
            cmd += ["--model", model]
        return cmd

    def verdict_text(self, stdout_text: str, out_dir: Path) -> str:
        """The verdict is not stdout itself but the `result` field of the one JSON object
        --output-format json prints: a string holding the model's reply, which the driver's own
        parse_verdict() then reads for the embedded {"verdict": ...} object. Empty (treated as
        no verdict, the same as no {...} on stdout at all) when stdout holds no such object, or
        when is_error is true -- a good round carries is_error false."""
        try:
            obj = json.loads(stdout_text.strip())
        except json.JSONDecodeError:
            return ""
        if not isinstance(obj, dict) or obj.get("is_error"):
            return ""
        return str(obj.get("result", ""))


class CodexAdapter(Adapter):
    """`codex exec` has no flag that mints or sets a session id -- round 1's argv carries none;
    Codex mints its own thread id and prints it as the first event's `thread_id` on the --json
    stdout stream, read back by session_from_output() below (a stray non-JSON line in that stream
    is skipped, not fatal). Resume is `codex exec resume <SESSION_ID> -`, which drops -s/--sandbox
    for `-c sandbox_mode=read-only` -- the exact unquoted token. --skip-git-repo-check runs on
    both commands, since argv() is not told which mode this round is. The driver's own --cwd
    already sets the working directory the process runs in (-C/--cd is not used). The verdict
    travels in the file -o/--output-last-message names, not stdout -- an event stream, not the
    reply -- read back by verdict_text() below only when newer than this round's prompt.txt, so a
    stale file left by an earlier failed attempt at the same round (main()'s round folder is
    reused, exist_ok=True) is never mistaken for this attempt's answer. Model flag -m."""

    name = "codex"
    binary = "codex"
    stdin_prompt = True

    def _last_message_path(self, out_dir: Path) -> Path:
        return out_dir / "codex-last-message.txt"

    def argv(self, prompt_path: Path, session: str, resume: bool, model: str) -> list[str]:
        out_file = self._last_message_path(prompt_path.parent)
        if resume:
            cmd = ["codex", "exec", "resume", session, "-c", "sandbox_mode=read-only"]
        else:
            cmd = ["codex", "exec", "-s", "read-only"]
        cmd += ["--skip-git-repo-check", "--json", "-o", str(out_file)]
        if model and model != "default":
            cmd += ["-m", model]
        cmd.append("-")
        return cmd

    def verdict_text(self, stdout_text: str, out_dir: Path) -> str:
        """The -o file's contents, but only when it is newer than this round's prompt.txt (always
        written just before the round runs, at the same fixed name) -- older or missing means no
        verdict, the same as an adapter whose verdict never lands on stdout at all."""
        last_message = self._last_message_path(out_dir)
        prompt_file = out_dir / "prompt.txt"
        try:
            if not last_message.is_file():
                return ""
            if prompt_file.is_file() and last_message.stat().st_mtime <= prompt_file.stat().st_mtime:
                return ""
            return last_message.read_text(encoding="utf-8")
        except OSError:
            return ""

    def session_from_output(self, stdout_text: str) -> str | None:
        """Codex's own thread id, from the first `thread.started` event on the --json stdout
        stream -- one JSON object per line; a line that fails to parse as JSON (Codex interleaves
        its own log lines with the events) is skipped, not fatal."""
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if thread_id:
                    return str(thread_id)
        return None


class CopilotAdapter(Adapter):
    """-s/--silent prints only the reply; --no-ask-user disables the question tool. --session-id
    passes round 1's id (the driver's, as for grok); --resume repeats it on round 2+. No
    `-p/--prompt`: the prompt travels on stdin, like claude's and codex's. --deny-tool=write and
    --deny-tool=shell deny every write, including a shell write through
    `git log/diff --output=<file>`. No `--allow-tool` grant, `--enable-all-github-mcp-tools`,
    `--allow-all-tools`, `--allow-all`, `--yolo`, `--allow-all-paths` or `--disable-builtin-mcps`:
    the built-in GitHub MCP server stays on its default read-only tool set. `--deny-tool` takes a
    variadic value, so each denial is its own `=`-joined argv element. Model flag --model, `auto`
    included."""

    name = "copilot"
    binary = "copilot"
    stdin_prompt = True

    def argv(self, prompt_path: Path, session: str, resume: bool, model: str) -> list[str]:
        cmd = ["copilot", "-s", "--no-ask-user",
               ("--resume" if resume else "--session-id"), session,
               "--deny-tool=write", "--deny-tool=shell"]
        if model and model != "default":
            cmd += ["--model", model]
        return cmd


ADAPTERS: dict[str, Adapter] = {a.name: a for a in
                                (GrokAdapter(), ClaudeAdapter(), CodexAdapter(), CopilotAdapter())}


class _ArgumentParser(argparse.ArgumentParser):
    """argparse's own .error() exits 2 -- the same code this driver uses for REVISE, so a bad
    invocation (a missing --cli, an unrecognized flag) would be indistinguishable from a verdict
    to a caller that only checks the exit code. Usage errors exit 1 instead, with the driver."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def main() -> int:
    ap = _ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cli", required=True,
                    help="the external CLI adapter: " + ", ".join(sorted(ADAPTERS)))
    ap.add_argument("--cwd", help="where the CLI runs and what it may read; default: the repository root of the current working directory")
    ap.add_argument("--model", default="default",
                    help="the CLI's model, or 'default' (the default) to pass no model flag")
    ap.add_argument("--range", help="diff mode: git diff range, e.g. master..HEAD or <sha>..<sha>")
    ap.add_argument("--claims", metavar="FILE", help="claims mode: a file of numbered factual claims about the repo, each with the file:line it cites")
    ap.add_argument("--goal", required=True, help="one paragraph: what the change must do (diff mode), or what the claims underpin (claims mode)")
    ap.add_argument("--lens", default=None)
    ap.add_argument("--paths", nargs="*", default=[], help="optional pathspecs to narrow the diff")
    ap.add_argument("--resume", metavar="UUID", help="round 2+: resume this session")
    ap.add_argument("--digest", metavar="FILE", help="round 2+: triage digest of the prior critiques (markdown/text)")
    ap.add_argument("--round", type=int, default=0, help="round number for the artifact folder (default: 1, or 2 with --resume)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds; a repo-reading round on a doc-heavy diff can take 5-6 min")
    ap.add_argument("--dry-run", action="store_true", help="write the prompt and print the argv the adapter would run; call nothing")
    args = ap.parse_args()

    adapter = ADAPTERS.get(args.cli)
    if adapter is None:
        print(f"external-audit: unknown --cli {args.cli!r}; adapters: {', '.join(sorted(ADAPTERS))}", file=sys.stderr)
        return 1

    if bool(args.resume) != bool(args.digest):
        print("external-audit: --resume and --digest go together (round 2+)", file=sys.stderr)
        return 1
    if bool(args.range) == bool(args.claims):
        print("external-audit: pass exactly one of --range (diff mode) or --claims (claims mode)", file=sys.stderr)
        return 1

    cwd = args.cwd or _default_cwd()
    claims_mode = bool(args.claims)
    lens = args.lens or (CLAIMS_LENS if claims_mode else DEFAULT_LENS)

    session = args.resume or str(uuid.uuid4())
    round_no = args.round or (2 if args.resume else 1)
    # Per-user parent, not a bare "external-audit": a shared temp dir where another account
    # already owns that folder must not fail every round with a PermissionError traceback.
    out = Path(tempfile.gettempdir()) / f"external-audit-{_artifact_user()}" / session / f"round-{round_no}"
    out.mkdir(parents=True, exist_ok=True)

    if claims_mode:
        # No git call: the claims may span several repositories, so --cwd is free to sit above them.
        subject = Path(args.claims).read_text(encoding="utf-8")
        if not subject.strip():
            print(f"external-audit: empty claim set in {args.claims!r}", file=sys.stderr)
            return 1
        cap_note = "\n[... claims truncated at 100 KB - split the claim set ...]\n"
    else:
        # -c core.quotepath=false: otherwise git C-quotes a diff header path holding a non-ASCII byte.
        subject = sh("git", "-c", "core.quotepath=false", "diff", args.range, "--", *args.paths, cwd=cwd)
        if not subject.strip():
            print(f"external-audit: empty diff for range {args.range!r}", file=sys.stderr)
            return 1
        cap_note = "\n[... diff truncated at 100 KB - narrow with --paths ...]\n"

    if len(subject.encode("utf-8")) > PAYLOAD_CAP:
        subject = subject.encode("utf-8")[:PAYLOAD_CAP].decode("utf-8", errors="ignore") + cap_note
        print(f"external-audit: payload truncated at 100 KB ({'split the claim set' if claims_mode else 'narrow with --paths'})", file=sys.stderr)

    digest = Path(args.digest).read_text(encoding="utf-8") if args.digest else None
    prompt = (build_claims_prompt if claims_mode else build_prompt)(subject, args.goal, lens, digest, round_no)
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    cmd = adapter.argv(out / "prompt.txt", session, bool(args.resume), args.model)
    print(f"external-audit: session {session} round {round_no} -> {out}")
    if args.dry_run:
        print("argv: " + shlex.join(cmd))
        return 0

    # Resolved, not the bare name: Windows' CreateProcess (what Popen uses without shell=True)
    # auto-appends only .exe to an extensionless name, never .cmd or .bat, so a CLI installed as
    # one of those shims (as many npm- or pipx-installed ones are) would raise "file not found"
    # from the Popen call below if it ran on the bare name. shutil.which already walks PATHEXT
    # and hands back the resolved, extensioned path, so that is what runs. Checked here, after
    # --dry-run's early return above: printing the argv the adapter would run does not need the
    # CLI installed.
    resolved = shutil.which(adapter.binary)
    if resolved is None:
        print(f"external-audit: {adapter.binary!r} not found on PATH", file=sys.stderr)
        return 1
    cmd[0] = resolved
    # Windows runs a .cmd or .bat through cmd.exe, which reads these as its own syntax in an
    # argument list2cmdline leaves unquoted (it quotes only whitespace) and expands % even inside
    # quotes: an argument holding one would be cut there and the rest run as a command.
    if os.path.splitext(resolved)[1].lower() in (".cmd", ".bat"):
        for arg in cmd:
            meta = next((ch for ch in arg if ch in "&|<>^%"), None)
            if meta:
                print(f"external-audit: the argument {arg!r} holds {meta!r}, which cmd.exe would interpret "
                      f"when it runs {resolved}; refused, nothing started", file=sys.stderr)
                return 1

    # Unconditional in both modes, as the port's source ran it: claims mode makes no git call
    # to build its subject, but --cwd may still sit inside a repo, and outside one this is
    # silent (sh() does not check the exit code).
    dirty_before = sh("git", "-c", "core.quotepath=false", "status", "--porcelain", cwd=cwd)
    started = time.time()
    stdin_arg = None
    if adapter.stdin_prompt:
        stdin_arg = subprocess.PIPE
    # stream stdout to disk as it arrives: a timed-out round still leaves its narration to read
    stdout_path = out / "stdout.txt"
    stdout_chunks: list[str] = []
    # POSIX only: the child leads its own process group, so a timeout kills the whole tree
    # (_kill_tree below) the way taskkill /T already does on Windows -- a launcher shim's own
    # kill would otherwise leave a node child holding the pipe and the pump never ends. The same
    # detachment means a terminal SIGINT no longer reaches the child on its own, and nothing
    # kills the group if this driver itself is killed mid-round -- SIGTERM is mapped to
    # SystemExit above so the try/except below catches it the same as a Ctrl-C's KeyboardInterrupt.
    popen_kwargs = {} if sys.platform == "win32" else {"start_new_session": True}
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _raise_system_exit)
    stderr_path = out / "stderr.txt"
    # stderr goes straight to a file, so a CLI that writes more of it than a pipe holds is never blocked
    with stdout_path.open("w", encoding="utf-8") as sink, stderr_path.open("w", encoding="utf-8") as stderr_sink:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_sink, stdin=stdin_arg,
                                text=True, encoding="utf-8", errors="replace", cwd=cwd, **popen_kwargs)
        def pump():
            for chunk in iter(lambda: proc.stdout.read(256), ""):
                stdout_chunks.append(chunk); sink.write(chunk); sink.flush()
        t = threading.Thread(target=pump, daemon=True); t.start()
        deadline = time.time() + args.timeout
        stdin_errors: list[BaseException] = []
        feed_thread: threading.Thread | None = None
        if adapter.stdin_prompt:
            # Off the main thread, and only started once the pump above is already draining
            # stdout and the deadline is set: a write issued synchronously here, before either,
            # can deadlock a CLI that fills its stdout pipe before it reads the prompt from
            # stdin -- and --timeout cannot rescue a hang inside a blocking write() that has not
            # even set its own deadline yet. A CLI that never reads stdin at all, or exits before
            # doing so, turns the write into a BrokenPipeError/OSError that must fail this round,
            # not crash the driver with an unhandled traceback.
            def feed():
                try:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                except (BrokenPipeError, OSError) as e:
                    stdin_errors.append(e)
            feed_thread = threading.Thread(target=feed, daemon=True)
            feed_thread.start()
        try:
            while proc.poll() is None and time.time() < deadline:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            # A terminal Ctrl-C or a harness kill (SIGTERM, mapped above): take the child's
            # process group down before this process itself exits, then let the exit proceed.
            _kill_tree(proc)
            raise
        if proc.poll() is None:
            # the process the driver started, killed by the PID (POSIX: the process group it
            # leads) recorded above -- not by name or pattern.
            _kill_tree(proc)
            t.join(5)
            print(f"external-audit: {adapter.binary} timed out after {args.timeout}s - partial narration in {stdout_path}", file=sys.stderr)
            return 1
        t.join(5)
        if feed_thread is not None:
            # Bounded: a broken pipe fails fast, so this is not a hang, but nothing should wait
            # forever on a daemon thread. Joined before stdin_errors is read below, so an early
            # exit that never touched stdin is reported as the pipe failure it is, not raced
            # into the generic "no JSON verdict" branch further down.
            feed_thread.join(5)
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    if not stderr_text.strip():
        try:
            stderr_path.unlink()
        except OSError:  # a descendant can still hold the file open on Windows
            pass
    stdout_text = "".join(stdout_chunks)
    if stdin_errors and not proc.returncode:
        print(f"external-audit: could not write the prompt to {adapter.binary}'s stdin ({stdin_errors[0]}) "
              "- failed round, re-run it", file=sys.stderr)
        return 1
    dirty_after = sh("git", "-c", "core.quotepath=false", "status", "--porcelain", cwd=cwd)
    if dirty_after != dirty_before:
        changed = sorted(set(dirty_after.splitlines()) ^ set(dirty_before.splitlines()))
        # write tools are disallowed, so this is usually the author editing during a parallel
        # round; naming the paths makes that case a glance, and a real critic write a revert.
        print("external-audit: WARNING - the working tree changed during the review: "
              + "; ".join(ln.strip() for ln in changed)
              + ". If you did not make these edits, the critic did - revert them and drop this round.",
              file=sys.stderr)

    verdict = None if stdin_errors else parse_verdict(adapter.verdict_text(stdout_text, out))
    if verdict is None and proc.returncode:
        if stderr_text.strip():
            tail = [ln for ln in stderr_text.splitlines() if ln.strip()][-5:]
            print(f"external-audit: {adapter.binary} exited {proc.returncode}; the last lines of its stderr "
                  f"(all of it in {out / 'stderr.txt'}):\n  " + "\n  ".join(tail), file=sys.stderr)
        else:
            print(f"external-audit: {adapter.binary} exited {proc.returncode}; its stderr was empty "
                  f"(see {stdout_path})", file=sys.stderr)
        return 1
    if verdict is None:
        print(f"external-audit: no JSON verdict from {adapter.binary} (see stdout.txt) - round 1 with no "
              "verdict means the caller falls back to /ouro:review; a manual run may re-run", file=sys.stderr)
        return 1
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")

    # The only machine-readable evidence that the repo was opened at all. Narration cannot be
    # scored -- measured on the port's source over two rounds of the same review, the one that
    # read nothing and the one that found a real defect had 329 and 291 characters of it, and
    # neither named a single file outside the verdict JSON. So an APPROVE carrying neither a
    # critique nor a files_read entry shows nothing whatsoever.
    files_read = [str(f).strip() for f in (verdict.get("files_read") or []) if str(f).strip()]
    approved = str(verdict.get("verdict", "")).upper() == "APPROVE"

    # A CLI that mints its own session id overrides session_from_output() -- codex does, reading
    # it back from its event stream; grok, claude and copilot leave it None, so the id this driver
    # minted and passed via argv() stays authoritative for them. The triage-template headings
    # below use this reported id too, not the driver's minted one, so --resume off a saved
    # template works for codex the same as for the others; round 1's own folder name and the
    # "session ... round" line above it stay the driver's minted id regardless, since the round
    # folder is created, and that line printed, before the round runs and any reported id is known.
    reported_session = adapter.session_from_output(stdout_text) or session

    elapsed = int(time.time() - started)
    print(f"VERDICT: {verdict.get('verdict')}   ({elapsed}s; "
          f"narration: {stdout_path})   (resume with: --cli {args.cli} --resume {reported_session} --digest <triage.md>)")

    if claims_mode:
        claims = verdict.get("claims", []) or []
        # Anything that is not a clean CONFIRMED is what the plan has to answer for.
        bad = [c for c in claims if str(c.get("status", "")).upper() != "CONFIRMED"]
        counts: dict[str, int] = {}
        for c in claims:
            k = str(c.get("status", "?")).upper()
            counts[k] = counts.get(k, 0) + 1
        counts_line = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(("  " + counts_line) if counts_line else "  (no claims returned)")
        for c in bad:
            print(f"  [{str(c.get('status','?')):12}] {c.get('id','?')}: {c.get('note','')}")
            if c.get("evidence"):
                print(f"                 evidence: {c['evidence']}")
        for f in verdict.get("new_findings", []) or []:
            print(f"  [NEW {f.get('severity','?'):8}] {f.get('summary','')}")
            if f.get("evidence"):
                print(f"                 evidence: {f['evidence']}")
        if verdict.get("verdict_reason"):
            print(f"  reason: {verdict['verdict_reason']}")
        if bad:
            lines = [f"# Round-{round_no} claim triage (session {reported_session})", ""]
            for c in bad:
                lines += [f"{c.get('id','?')} ({c.get('status','?')}) - {c.get('note','')}",
                          "  ACCEPTED -> <how the claim was corrected> | REJECTED -> <why, with file:line>", ""]
            (out / "triage-template.md").write_text("\n".join(lines), encoding="utf-8")
            print(f"  triage template: {out / 'triage-template.md'}")
        if files_read:
            print(f"  files_read ({len(files_read)}): " + ", ".join(files_read[:12]))
        elif approved and not bad:
            print("external-audit: UNFALSIFIABLE - every claim CONFIRMED with an empty files_read. "
                  "Nothing here shows the tree was read. Re-run; this is a failed round, not a "
                  "round, so do not count it against the two-round cap.", file=sys.stderr)
            return 3
        return 0 if approved else 2

    critiques = verdict.get("critiques", []) or []
    if not critiques:
        # an empty APPROVE is unfalsifiable from the JSON alone: show what the critic says it verified
        narration = re.sub(r"\{.*\}\s*$", "", stdout_text, flags=re.S).strip()
        print("  no critiques - narration (what it verified):")
        for ln in (narration[-1500:] or "(none)").splitlines():
            print("    " + ln)
        if files_read:
            print(f"  files_read ({len(files_read)}): " + ", ".join(files_read[:12]))
        elif approved:
            print("external-audit: UNFALSIFIABLE - APPROVE with no critiques and an empty files_read. "
                  "Nothing here shows the repo was read. Re-run; this is a failed round, not a "
                  "round, so do not count it against the two-round cap.", file=sys.stderr)
            return 3
    for c in critiques:
        print(f"  [{c.get('severity','?'):7}] {c.get('id','?')}: {c.get('claim','')}")
        if c.get("evidence"):
            print(f"           evidence: {c['evidence']}")
        if c.get("probe"):
            print(f"           probe:    {c['probe']}")
    if critiques:
        # fill-in-the-blanks digest for the next round: one entry per critique, probe column mandatory
        lines = [f"# Round-{round_no} triage (session {reported_session})", ""]
        for c in critiques:
            lines += [f"{c.get('id','?')} ({c.get('severity','?')}) - {c.get('claim','')}",
                      "  ACCEPTED -> <what changed, file:line> | REJECTED -> <why>",
                      "  probe: <what was run, expected vs actual>", ""]
        (out / "triage-template.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"  triage template: {out / 'triage-template.md'}")
        if files_read:
            print(f"  files_read ({len(files_read)}): " + ", ".join(files_read[:12]))
    return 0 if approved else 2


if __name__ == "__main__":
    sys.exit(main())
