# Opt-in mechanics for interactive multi-ticket work

> The default `/ouro:execute` path dispatches exactly one builder per **M** issue — one session,
> one PR — at the model its Size section names (`[models].builder`, default Sonnet;
> `[models].builder_trivial`, default Sonnet, for `trivial`); an S builds inline,
> with no builder. The table below is
> this reference's own economics, opt-in for an owner-driven expedition across several tickets.

## Model tiering (who does what)

| Role | Default | Why |
|---|---|---|
| builds, audits | Sonnet sub-agents | fast, cheap, good enough when the contract is clear |
| subtle contracts, all adversarial reviews | Opus | reviews are where quality is bought |
| the single hardest correctness-critical piece | the session's top tier | one ticket, not a habit |
| web/data gathering | Haiku | mechanical legwork |
| adverse checks (cheap tier) | Haiku panel + beast advisory | distinct lenses, see below |

External builder (`builder=codex` / `builder=grok`) replaces the *build* row only —
reviews stay in-family with the arbiter, which keeps author ≠ reviewer across
model families for free.

## The dispatch template

Every builder dispatch — Agent tool or external CLI — carries:

```
TASK: <one ticket / one seam>
CONTEXT: <spec link, files, contracts it must honor>
DONE-CONDITION: <deterministic, checkable — "all 12/12 N=4 band cases show X",
  "pytest tests/test_foo.py green", never "improve" or "make work">
STOP-RULE: any done-condition miss → STOP and report what you observed;
  no tuning, no scope adjustment, no second theory.
TURN-BUDGET: <the turn budget the plugin's docs/contract.md sets> turns; reaching
  it is the STOP-RULE's STOP — report what is done, what is outstanding, and the
  evidence so far.
FILE-RULE: A dispatch creates files only where it was sent. Sub-agent, external builder and reviewer
  alike create files only inside the worktree they were handed, and only the files the task names; a
  read-only dispatch creates none inside any working tree. Every probe, copy, fixture, scratch repo,
  download and log goes outside every working tree, in the session's own scratch directory, which is
  never the main checkout. A location the tree git-ignores counts as outside it for that sentence
  and for nothing else: what it protects is an empty porcelain status and a worktree that can still
  be removed. It is somewhere to leave transient output, never a licence to write configuration the
  tooling reads -- an ignored overlay or settings file is no more a dispatch's to write than a
  tracked one. The exception is the repo's own gate commands: what they write where they run is
  theirs, not yours.
DELETE-RULE: Delete only scratch you created, by exact name, never by a pattern: another run's files
  sit under the same root.
SHELL-RULE: A command that removes a path removes a literal path, the object returned by a create
  that never returns an existing path (`New-Item` without `-Force`, which fails on one; `mktemp -d`,
  which makes a new one), or a path it has just proved lies under the session's scratch directory:
  the target's resolved path starts with that directory, its own trailing separator trimmed, then a
  separator, compared case-insensitively on Windows. A recursive delete never silences its errors.
  Stop only a process you started, by the PID you recorded when you started it: never `pkill`,
  `killall`, `Stop-Process -Name`, `taskkill /IM`, or a `kill` fed by `pgrep`. A pattern matches
  every process on the host that holds it, another session's jobs and your own shell included, and a
  count of processes by pattern counts the command doing the counting. In PowerShell, no variable is
  named after one PowerShell reserves: a name `Get-Variable` shows ReadOnly or Constant in a fresh
  `pwsh -NoProfile` (`$home`, `$pshome`, `$isWindows`, `$host`, `$error`, `$pid` among them), whose
  assignment fails and leaves the old value, or one the runtime rebinds (`$input`, `$args`,
  `$matches`, `$_`). A shell variable that was not assigned is a path somewhere else. Text a command
  carries as data (an issue or PR body, a commit message, a fixture, a mutant, a JSON line) that
  holds a backslash escape, a `$`, a backtick or a template tag is written to a file with the
  agent's file-write or edit tool, never through a here-doc, an `echo` or a quoted shell string, and
  reaches a command through a flag that reads a file, such as `--body-file` or `git commit -F`. The
  file is then read back and compared with the intended text before it is used: a tool call can
  decode an escape on the way, and a `\uXXXX` escape sometimes
  arrives decoded even through the file-write tool. Text that must keep such an escape is produced
  by a script that builds the backslash from its character code.
REPORT: what you did, proof output verbatim, what the proof cannot show.
```

The adverse review dispatch is the same shape with `TASK: try to BREAK <change>`,
a lens, and `REPORT: verdict MERGE / MERGE-WITH-FIXES / REJECT with file:line
evidence`. Reviewers re-run the proof themselves — a verdict without a re-run is
returned to the reviewer, not accepted.

## Trek

In-session orchestration, one repo:

1. Slice the spec into dispatches (one seam each). Order by dependency.
2. Per slice: builder sub-agent (Sonnet, or the external builder) → adverse
   review (Opus for contract-touching changes, Haiku+beast panel for mechanical
   ones) → arbiter audits the diff → fix rounds (≤3) → arbiter commits.
3. Slices that are independent run as one parallel wave; dependent slices wait.
4. Full proof once at the end, on the assembled result.

## Expedition

Everything trek has, plus scale machinery:

- **Issue map.** Execution tickets are GitHub issues that went through `/ouro:triage` and
  carry `agent-ready` plus the repo's scope labels from `[labels].scope`; dependencies are
  native sub-issues + blocking. A ticket is a build, never a question — questions are
  `needs-ruling`. Close decisions with the human in batched rounds *before* the first
  wave; mid-flight calls the arbiter makes alone are logged on the ticket as PROVISIONAL.
- **Worktree isolation.** One worktree per builder:
  `git -C <repo> worktree add .claude/worktrees/<N>-<slug> -b <prefix>/<N>-<slug> origin/<default>`
  (prefix per the repo's branch policy; never a `[repo].protected_branch_prefixes`
  branch; the repo must gitignore `.claude/worktrees/` — before the first add, assert that
  once for the first builder's worktree, its directory created first as `/ouro:execute`
  creates it and removed on a STOP with each parent it left empty:
  `mkdir -p <repo>/.claude/worktrees/<N>-<slug> || exit 2; git -C <repo> check-ignore -q --no-index .claude/worktrees/<N>-<slug> || { rc=$?; rmdir -p <repo>/.claude/worktrees/<N>-<slug> 2>/dev/null; exit $rc; }`
  — exit 1 STOPs the wave with the line to add, 128 is a git error, and 2 is a filesystem
  error, something in the way of the directory, not a missing entry). Builders never touch
  the trunk checkout; the arbiter merges (`merge → test → commit`), then removes the
  worktree. Push per the kickoff authority.
- **Waves.** Dispatch the current frontier (unblocked tickets) as one wave; review
  and merge the wave before opening the next. A wave's STOP reports are resolved —
  human call or arbiter PROVISIONAL — before its tickets are retried.
- **The map is the log.** Resolution comments on tickets, PROVISIONAL markers,
  STOP reports: on the issues, not in chat scrollback. A fresh session can pick up
  the effort from the map alone.

## External-builder recipes (write-enabled — audit everything)

Precondition for both: clean tree, and the dispatch template rendered into the
prompt. The builder edits; it never commits (instruct it so, and verify: any
commit it makes gets soft-reset and audited as a plain diff).

- **Codex:**
  `codex exec -s workspace-write --json -o <scratch>/codex-build.txt "<dispatch>"`
  Fix rounds resume the session id captured from the JSONL:
  `codex exec resume <SESSION_ID> -c sandbox_mode="workspace-write" --json -o ... "<fix dispatch>"`
- **Grok:**
  `grok -p "<dispatch>" -s <UUID>` (fix rounds: `-r <UUID>`). Grant write by
  omitting `--disallowed-tools`; the no-commit/no-push rule rides in the dispatch
  prompt, and the soft-reset guard above catches a violation deterministically.
- **Beast is never a builder** — a local coder model drives an agent harness fine for
  drafting, but builder output must clear the same adverse review as everyone
  else's, and the beast's cost advantage disappears once Opus has to review twice.
  Advisory and adverse-check roles only.

## Cheap adverse panel (trek default for mechanical changes)

2–3 Haiku sub-agents in parallel, distinct lenses (correctness, silent-failure,
regression), plus one beast advisory pass. Majority REJECT or any confirmed file:line
defect → fix round. Contract-touching changes skip straight to an Opus review.
