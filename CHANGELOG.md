# Changelog

What each release of this plugin changed for a repository that installs it, newest first.
Consumers pin the plugin by tag, so a release is the unit a person or a resolver reads before
moving a pin. One bullet per landing; an entry names no issue or pull request number; the
`chore(release)` bumps themselves are not entries.

Released sections stay in this file. The oldest ones, including their **Consumer contract:**
migration notes, are in frozen root archives:
[v0.2 through v1.3](CHANGELOG-0.2-through-1.3.md) and [v1.4](CHANGELOG-1.4.md). The linked
current documentation defines the protocol.

A `**Consumer contract:**` line marks an entry that moves something a consumer's own files
name: a skill's invocation flags or its credential and tool requirements, a table or key of the
binding schema (or a value the binding check newly refuses), a script's parameters or a python
tool's usage lines, the shape of a template a consumer copied, or an Action's own definition file
a consumer's workflow uses at a pinned tag. The line names the old form, the new form and the
one-line migration. A release carrying any such entry takes a minor version bump; every other
release takes a patch.

## [UNRELEASED]

## [v1.9.1] - 2026-10-30

**Net lines:** +838 across skills, docs, hooks, bin and tests, because this release is bug fixes
only and each carries the rows that fail without it: +474 for the coordinator SIGTERM unwind and
incomplete-attempt classification, mostly runtime rows, and +364 for the post-merge push-run
reader, triage's acceptance check and the macOS and Windows platform fixes.

- On macOS, the kill guard no longer calls the session's own process another session's when the
  worktree's path runs through a symlink (`/var` is `/private/var` there): both directories are
  resolved before they are compared.
- Triage checks acceptance the way the run does. An `Acceptance command:` field the node writes
  that the run would refuse, such as a `grep` or a pipeline, is posted as prose rather than a
  grant, so it no longer stops every run of the issue as `acceptance unrunnable`. A body whose
  own acceptance the run refuses gets NEEDS-RULING, naming each command, instead of PROMOTE.
- On Windows, a gate command whose executable is an app execution alias, such as the Microsoft
  Store's `python3`, no longer fails the direct lane's executable pin: the alias file is pinned
  as its own identity, and one inside the checkout still requires a pull request.
- On macOS, a timed-out or cancelled command whose process group had already exited no longer
  fails with `PermissionError`: Darwin refuses to signal a group of exited, unreaped processes,
  and that refusal now counts as nothing left to kill, once `ps` shows no running member.
- Default POSIX SIGTERM now unwinds the CLI, runtime subprocess and caller-validation boundaries
  through owned cleanup with a non-success exit, restoring prior handlers even when the first
  termination request arrives at cleanup entry or interrupts its body. The run skill requires
  the invoking lead to retain execution ownership; turn-owned monitors provide no durable
  continuation, and SIGKILL or host death provides no cleanup guarantee.
- Codex incomplete attempts report bounded reasons without provider error text; failed initial BUILD runs require explicit preservation, worker-exit inspection and a fresh completed build with proof and review.
- A full run's post-merge watch no longer reads a missing default-branch run as green. Each
  enabled workflow whose push filters (branches, tags, paths and their `-ignore` forms) select
  the merge commit must show a push run, and the watch waits up to ten minutes for one GitHub
  starts late, then stops. A run another event starts on the commit, such as a label workflow,
  no longer counts. A commit no workflow's filters select still passes after a minute. A watch
  that ends before the verdicts keeps the worktree and leaves the checklist part unticked for the
  resume, with or without a claim board. `ouro-binding.py push-workflows <sha>...` prints the
  selected workflows.
- The developer binding accepts `[followup].assignees`, the GitHub logins (`@me` included) a run
  assigns to each follow-up issue it files; absent, follow-ups are filed unassigned as before. An
  older driver refuses the table, so set it only once the plugin is at this release.
- A required acceptance command that BUILD's first call was denied for, and that a later call in
  the same session ran, no longer stops the run as `acceptance unrunnable`.

## [v1.9.0] - 2026-10-29

- The plugin no longer registers the forbidden-tokens PreToolUse hook, so a `gh` or `git commit`
  call is never blocked by the private token list. `forbidden-tokens.py scan` (tracked files),
  `check` (text on standard input) and the release export's check stay.
  **Consumer contract:** `ship.forbidden_check` was `required` or `off`; `check` now refuses it as
  an unknown key, and `forbidden-tokens.py hook` is gone. Migration: delete the `forbidden_check`
  line from `[ship]`.

- `ouro run` no longer offers BUILD recovery from a dirty worktree.
  **Consumer contract:** `run N --recover-build PATH` is gone from the driver and the run skill.
  Migration: commit or stash the partial files you want and resume with a plain `run N`.

- `ouro run N --validate` takes a plain lock file per worktree and kills each command's process
  group when it returns or passes its bound, in place of the lease and the process-table watch.
  A worker that leaves its process group is no longer tracked.
  **Consumer contract:** `ouro validation-lock [--recover]` is gone. Migration: a lock left by a
  killed driver is deleted by hand; the refusal names its path.

- A direct-lane pull request merges once its checks are green, with no second proof of its head in
  a scratch copy. A run that finds a `direct/<N>` pull request already open waits for you to merge
  or close it instead of resuming it.

- The fingerprint that tells whether a command changed its own inputs is read from Git (HEAD, the
  index, and the mode and bytes of each changed, untracked or hidden path) in place of a directory
  walk. A dirty submodule or a nested repository gives no proof.

## [v1.8.1] - 2026-10-29

**Net lines:** +69 across bin and tests, because three bug fixes and the BLOCKED verdict each carry
the driver or suite rows that fail without them.

- Triage has a BLOCKED verdict, which sets `blocked`. It promotes no issue whose body's first line
  still names an `**Unblocks when:**` trigger, so a queue run no longer picks up a waiting issue.
  A BLOCKED answer without that line is a question for the owner.

- `loop-outcomes.py` skips an anchor whose path the working tree holds but its stamp does not,
  instead of stopping on git's "exists on disk, but not in" error.

- Under `ship.validation = "ci"`, the driver runs and proves an acceptance command that no CI-owned
  gate runs, as it runs its own gates, so such a change lands. It no longer repeats
  `Missing successful execution evidence` on empty fix rounds until `review cap`.

- Allow five ordinary corrective rounds before the review-cap handoff, including BUILD recovery;
  retain validation, review and early-stop requirements.

## [v1.8.0] - 2026-10-28

- Under `ship.validation = "ci"`, an acceptance command only the pull request's checks run, through
  a gate whose `paths` the change does not touch, stops the run right after BUILD with
  `acceptance unrunnable`, naming the gate's paths, before any push or CI wait. An unavailable
  external reader's note names the last line of its stderr, such as an expired login.

- The token hook's refusal names a `cd` or `git -C` target it cannot resolve (a shell variable,
  `cd -`, a missing directory), where a binding's `forbidden_check = "off"` cannot apply, and says
  to write the directory literally.

- A gate's `paths` is its only selector. The driver, the direct lane and the gates Action read
  one rule, which `python3 bin/ouro-binding.py gates --changed <base> [--head <rev>]` prints: the
  diff from the merge base, with a gate without `paths` running on every change. The BUILD brief
  grants every gate, whatever the issue's area labels; those labels still drive triage, branch
  names and coordination claims.
  **Consumer contract:** `[[gate]].areas` was required and selected gates by the issue's area
  labels; `check` now refuses it, and `gates <area-label>` is now `gates [--changed <base>]`.
  Migration: delete every gate's `areas` line, and give each gate that was scoped to an area the
  `paths` that area's changes touch.

- Unsupported validation hosts name the selected authority and exact pending commands, with
  explicit CI coverage or native-Linux resume guidance. Mac/Windows BUILD handoffs stay usable;
  the Mac existing-PR migration recipe preserves prepared source and local-only obligations.

- A run that waits because its host cannot prove BUILD's commands (native Windows, WSL) now names
  both ways forward: let the pull request's checks prove them with `ship.validation = "ci"` and
  `ship.validation_checks`, or prove them from a native Linux host with `ouro run N --validate`.

- The token hook reads an attached `-F<file>` only outside quotes in the publishing call's own
  statement, so a later `pwsh -File`, `-Force` or `-Filter`, or the same word in a quoted title, no
  longer blocks a commit or issue as an unreadable body file.

## [v1.7.1] - 2026-10-28

**Net lines:** +4 across tests, because the public export's verify tree needs its own branch in
the gates Action suite, and v1.7.0's export refused without it.

- The gates Action suite skips its rows about this repository's own Gates workflow in an exported
  tree, which leaves that workflow out by design, and still fails where the workflow is missing
  but not excluded.

## [v1.7.0] - 2026-10-28

**Net lines:** +5579 across skills, docs, hooks, bin and tests, because this release keeps the
features below and their tests (tests +3837, bin +1772): pull-request adoption, collection of
finished driver worktrees, the session-start sync hook, Codex BUILD bridge guidance, the direct
lane's size and check policy with pinned publication, CI-backed validation, and the gates Action's
parallel runs and timing reports. The docs shrank by 139 lines and duplicated driver code was
merged; restructuring the driver and its tests into modules, in the next minor, pays the rest back.

- The gates Action takes an optional `parallel` input: how many selected gates run at once. The
  default `1` keeps the one-after-another loop with live output; above it, each gate's output,
  stderr included, prints as one group when the gate ends.

- A `SessionStart` hook fast-forwards a bound repository's clean main checkout on its default
  branch to `origin/<default>`, then asks for a restart so the session loads the current
  instructions; a checkout it cannot move is left untouched with a one-line notice.

- The driver's children run with .NET's multicore-JIT profile off, so parallel `pwsh` starts no
  longer share, and corrupt, one startup profile cache (an invalid-assembly-name abort).

- `ouro run N --pr M` adopts an existing open or merged pull request from the same repository into
  the default branch whose body has its own `Fixes #N` line (`Part k/n of #N` for a part short of
  the last): the run gates, reviews and ships that branch from its own worktree under the binding's
  usual policy, or finishes the landing of a merged one. Only an explicit request adopts; every
  resume repeats `--pr M`, and the refusal for an issue another pull request already fixes names it.

- Run and triage start by removing the driver's own finished worktrees: a clean, settled tree whose
  pull request is closed or merged and whose HEAD is on origin, with its local branch. A dirty,
  unpushed, active or uncertain tree stays and is named; remote branches are never deleted.

- Codex BUILD is told that its file bridge may edit although the native sandbox is read-only, and
  gets the installed run skill as context, so it no longer stops claiming read-only access.

- A direct run whose BUILD could write the installed ouro runtime or instruction sources takes the
  pull-request path instead.

- **Consumer contract:** the direct lane publishes inline only when the change fits
  `ship.direct_max_lines` and `ship.direct_max_files` (defaults 20 and 3) and at least one
  applicable `cheap` gate passes, with a `gate.direct_code_check` for each changed code path;
  otherwise it opens a pull request. A binding with no applicable cheap gate therefore always takes
  the pull-request path. Update the plugin before adding the new keys, which older readers refuse.

- **Consumer contract:** `Push-Direct.ps1` accepts optional `ExpectedHead` and `ExpectedBase` pins,
  and `Push-Branch.ps1` an optional `ExpectedHead`; unpinned calls work as before. Update flat
  direct helpers together: a new direct publisher refuses an old companion that cannot pin the head.

- **Consumer contract:** `repo.release_notes`, an optional binding key, lists the release-note
  paths as regexes (per-change fragment files, a localised twin): the S shape's file count leaves
  them out, and the REVIEW brief names them as the release-note entries to verify. Old form: only a
  `CHANGELOG.md` basename counted; new form: `release_notes = ['^changes/']` under `[repo]`;
  migration: none, since an unset key keeps the `CHANGELOG.md` basename rule. `check` refuses a
  pattern that does not compile.

- **Consumer contract:** `Merge-PullRequest.ps1` accepts optional `-ExpectedHead <full-sha>`, and
  the driver passes the reviewed head, so a pull request whose head moved after review is not
  merged. Manual callers without the pin work as before.

- **Consumer contract:** a repository may set `ship.validation = "ci"` with exact
  `ship.validation_checks` names, so pull-request CI validates the final head instead of the local
  run. The default stays `local`. Update older ouro pins before adding these keys.

- **Consumer contract:** the gates Action logs each gate's elapsed time and the platform, writes
  them to the job summary, and outputs `selection-mode` (`full`, `paths`, `fallback`). Callers need
  no change; one that reads `selection-mode` treats a missing value as unknown.

- **Consumer contract:** flat vendoring copies all tests except the plugin-only `tests/driver/`
  and `tests/runtime/` roots. Remove flat-layout gate entries naming those suites; files already
  copied stay until removed by hand. Plugin installs and exports keep both roots.

- **Consumer contract:** docs-index entries match exact repository-relative paths. Replace a
  shortened suffix entry with the doc's full path; a root basename covers only its own root doc.

- Triage applies its label changes before posting the verdict, so a failed label edit leaves no
  verdict that contradicts the labels. Promoting an answered measurement removes `checkpoint`, and a
  stale checkpoint also loses `agent-ready`.

- Native Windows Codex refuses command and edit workers at preflight, before any probe, and names a
  Claude developer profile as the alternative.

- On Windows, prompts reach npm batch launchers complete, with EOF, and a timed command no longer
  hangs on stdin backpressure or on a descendant that keeps an output pipe open.

- The token hook reads a commit or issue body file the way the shell resolves it: after a `cd` in
  the same command, through `~`, and on Windows through a Git Bash drive path (`/c/...`).

- Check watches poll every 15 seconds instead of 60, and the driver's own waits every 15 instead
  of 30, so a run notices finished checks sooner.

- The No binding provider counts uppercase and mixed-case skip-only output as skipped.

- Codex Git readiness accepts a stable symlinked worktree parent and refuses a retargeted one.

## [v1.6.0] - 2026-10-26

- **Consumer contract:** readiness requires successful final-input execution of every required BUILD
  command through the bounded, leased validator. Grants, worker observations and BUILD reports
  alone cannot satisfy it. Selected driver gates retain their execution authority; additional
  observed worker candidates require `ouro run N --validate` and the invoking command allowlist.
  Final proof and caller validation require native Linux; unsupported platforms wait without
  spending fixes. Resumes retain branch gate obligations even with partial receipt recovery.
  Worker fingerprints use bounded held-file reads and Git/submodule inputs; unsupported or
  unstable snapshots provide no candidate proof. Direct-lane and read-only checkpoint rules remain.

- Keep the current and two preceding minor series in the main changelog; older release
  and consumer migration notes remain in linked, dated root archives.

- **Consumer contract:** TRIAGE requests explicit file-only Codex readiness with a genuine MCP
  read and native tools disabled; its trusted MCP orchestration host remains available. Unused
  command controls do not gate that contract. Delivery runs
  and developer inspection retain full readiness. Native Windows command, probe and edit dispatch
  remains refused, and file-only startup failures never trigger setup or a sandbox-policy fallback.

- Preserve generated network probes through Windows CMD launchers with an immutable encoded
  argument. Readiness refusals distinguish missing receipts and process failures using safe
  marker classifications and output sizes; network admission and unsupported Windows policy
  still refuse before model dispatch or tracker changes.

- Triage validates its proposed promotion before publishing the verdict or setting agent-ready;
  mechanical refusals name the gate finding in one NEEDS-RULING comment and return exit 3.
  Bare top-level evidence paths require `./filename.ext`, regardless of quoted fragment contents;
  ordinary inline mentions remain prose.

- Confirm native Windows process exit after forced termination before returning from runtime
  cleanup. A cached exit code no longer leaves the child working directory temporarily locked;
  unavailable exit proof preserves the original output-limit or timeout failure.

- **Consumer contract:** explicit `Acceptance command:` fields in trusted spec amendments grant
  exact commands; sized reviews use the existing disposable probe copy. Unrunnable acceptance
  stops before empty fixes. External diff verdicts may carry an acceptance-unrunnable `stop`,
  returned as exit 2 even with APPROVE; callers must surface that constraint instead of fixing it.
  Trusted comment boundaries and valid Markdown fences preserve grant scope; external readers
  retain explicit acceptance stops instead of approving or spending empty fix rounds.

- **Consumer contract:** `ouro run N --recover-build PATH` explicitly offers selected partial
  edits to normal BUILD in a private clean checkout, preserving the dirty original and evidence.
  Repeat the flag per file and use `--validate` for genuine final-input proof; native Linux
  process visibility, existing review/fix caps and normal coordination conflicts still apply.

- Refuse Codex readiness when the configured command sandbox admits host loopback or its
  disabled-network boundary cannot be established with generated positive/restricted controls. Windows
  workers remain unavailable until a supported implementation/policy is established.

- Check Codex Git capabilities with intended configuration and ignore semantics before workflow
  mutations, including prospective and occupied worktrees; preserve unproved probe state for inspection.

- Bound Codex event capture separately from command output, accepting larger MCP transcripts
  while retaining final-message authority, late failures, diagnostic metadata and cleanup guards.

- Retain bounded Codex attempt diagnostics through rejected results and cleanup failures,
  including nonempty dry-run triage ledgers; unknown usage remains unknown.

- **Consumer contract:** optional coordination checks direct pushes against actual path/area
  overlap, with the existing explicit `--override-claim` refusal boundary. Owned threads record
  parent/fix/revert heads and publication/check outcomes; disabled coordination needs no migration.
  Direct commits and merge bodies retain execution provenance for the one-fix cap; revert
  retries reuse the prepared commit, and completed revert PRs reopen and stop for a ruling.
  Ambiguous legacy resume evidence requires manual reconciliation without rewriting history.

- **Consumer contract:** Caller-validation locks now retain owner and command lifecycle evidence.
  Use `ouro validation-lock` in the validation worktree to inspect, and `--recover` to quarantine
  a proven inactive native-Linux lease. Uncertain executions retain their lock and attempts;
  recovery never creates a receipt. Retry the normal explicit validation batch after recovery.
  Validation execution preflight and receipt reuse enter the leased Linux subreaper boundary before
  their Git/head/clean helpers. BUILD metadata and ordinary CLI bootstrap/issue lookups remain outside
  it. Ordinary receipt-backed resume uses the same lease. Remaining children invalidate helper
  snapshots and fail commands with preserved attempt rows/logs, even after lifecycle settlement.
  Settled proof requires all owned children reaped. Bounded batch fingerprints/counts replace
  command text in locks, while
  full command evidence and large approved batches remain supported. Older lock proofs refuse.

- Exclude the configured coordination credential from caller validation and coordinator-launched
  repository commands, including setup, gates and Git hooks/helpers, while preserving unrelated
  environment values and validation receipts.

- Caller validation waits before launching any command on WSL, including native launchers that
  could activate Windows descendants outside POSIX accounting. Prior attempts remain preserved;
  an early unsupported refusal may create no new command-attempt log or successful receipt.
  Existing proof is not admitted on unsupported platforms. Retry on
  a native Linux host. Host interoperability and the coordinator's Windows bridge are unchanged;
  no proven child-only interop helper is supported. Regression controls distinguish simulated
  admission from actual WSL direct/nested activation evidence.

- **Consumer contract:** Python 3.11+ becomes Python 3.12+ for the tools, hooks and binding-reading
  gates. Install `python3` 3.12 or newer on each host; CI exercises the 3.12 minimum. Unsupported
  runtimes refuse before token parsing or release extraction; archive extraction retains its data filter.

- Fix Codex sandbox invocation on Linux and macOS to use automatic platform selection,
  retaining CLI 0.163.0+, the named permission profile, and filesystem/network restrictions.

- **Consumer contract:** issue runs use configured optional board claims before mutation; known
  conflicts refuse unless the invoking person supplies `--override-claim`. Resume correlates owned
  threads, waiting retains them, and observed land/terminal stop closes them with an outcome.
  Disabled coordination preserves the existing workflow; no migration is required. Board outages
  remain fail-open, with no change to review or shipping authority.

- **Consumer contract:** the developer binding admits optional `[coordination]` settings for
  endpoint, person, forum and a credential environment-variable name (default `MOMENTUS_API_KEY`).
  Runtime-only files retain their defaults; no migration is required. Configured credentials
  are removed from Claude/Codex worker, external-reader and bridge/tool environments. A bounded stdlib MCP client
  supports JSON/SSE and sanitized unavailable results; issue runs do not post claims.

- **Consumer contract:** BUILD receives every selected `cheap` gate even when CI also runs it.
  Worktree builds with no approved acceptance/gate command stop with `gate uncovered`, including
  resumed dispatches, instead of building with an empty list. Name an acceptance command or mark
  a fast gate `cheap = true`; existing bindings that grant a command need no migration. The
  direct lane keeps its driver-run gates, and checkpoints retain their read-only fallback.

- POSIX validation observes parent completion before collecting the ownership snapshot used to
  judge it. A child the parent already waited for cannot be mistaken for unfinished work when
  completion races with process-table collection. Live asynchronous descendants still fail
  validation and are cleaned up.

- **Consumer contract:** `[[gate]]` accepts optional boolean `cheap`, describing a gate that needs
  no toolchain and runs in seconds. A non-boolean value is refused, naming the gate. Bindings
  without the key keep their behavior; adding the key has no effect on command grants in this
  increment. No migration is required.

- A checkpoint gets the issue's approved commands beside its read tools. Command-bearing runs
  use a disposable default-branch worktree with repository setup, report any uncommitted changes
  in the finding, and remove the worktree after findings, stops or errors. With no command it
  reads the main checkout. Nothing is committed by a checkpoint.

- **Consumer contract:** a developer-owned runtime binding and user-wide skill installer select
  Claude Code or Codex workers without changing repository shipping policy. Existing installations
  retain Claude defaults; Codex requires CLI 0.163.0+, authentication and sandbox readiness.
  Use `python3 bin/ouro-developer.py install --backend codex` and inspect the developer binding.
  Codex nodes use a constrained file bridge, exact approved commands, strict structured results,
  independent sessions and owned-process cleanup; unreported cost is unknown.

- A refresh of the pull request body keeps a section a person edited since the driver drew it: the
  record comment holds a digest of each `## ` section as drawn, and a section that no longer matches
  is kept as it stands, a web edit's CRLF line ends included. A lead's correction to the summary or
  the follow-up no longer comes back on the next round.

- An `Ouro-Build:` line is checked where it is read: one that is not a JSON object, or whose
  `gates_run`, `manual` or `replies` holds an entry that is not an object with its key, or whose
  `residue` is not a list, is a driver error naming the commit and the field. A commit message a
  person reworded by hand no longer crashes the body's render after the review is posted.

- A fix round reads the failure in a red log, not its last lines: for a pytest log, the FAILURES and
  ERRORS sections, the FAILED and ERROR lines and the result line; for any other, each line an error
  marker names with two lines of context, then the tail; over 200 lines, cut from the middle. This
  covers a red pull-request check, a red default-branch run in the direct lane, a red local gate and a
  failed validation, so a failure that a long SKIPPED list follows reaches the BUILD node.

**Net lines:** +12275 across skills, docs, hooks, bin and tests, because the shared Codex
binding, optional coordination and final-input execution proof need executable process
boundaries and regression coverage while stage orchestration remains shared.

## [v1.5.0] - 2026-10-22

- **Consumer contract:** BUILD can report optional `validation_pending` command/reason records;
  `ouro run` waits before review until explicit `--validate` executes approved commands and
  records clean exact-head receipts. Existing BUILD answers and invocations remain valid;
  use `--validate` from an admitted lead context for environment-blocked acceptance. Genuine
  owner stops retain their handling; no builder permissions or resource policies change.
  Live owned workers prevent successful completion; Windows validation waits before launch
  until safe lifecycle accounting is available.

**Net lines:** +2512 across skills, docs, bin, tests, because the validation hand-off, the driver's
worktree catch-up, preflight and check reading, the no-workflow and repo.setup paths, the gates
sweeps and each rule's own rows all landed as lines and nothing was removed.

- `ouro run` no longer posts **Ready for merge**, or merges, on a head with a check that is not green:
  at the merge decision a check in any bucket but `pass` or `skipping` (a failed, cancelled or still
  pending one) is a driver error naming it. A failed check is called a flake only when its re-run attempt, read by
  number from the API, concluded `success`, not when the run list's record reads green; and a run
  whose job never started (an Actions budget, a runner never acquired, a startup failure) stops as
  `**Stop:** gate uncovered`, CI could not run, with no re-run and no fix round.

- **Consumer contract:** `python3 bin/ouro-binding.py direct <path>... --binding` with no path
  after `--binding` exits 2 with the usage line on stderr; it exited 1, the code that means a path
  matched no glob. The usage lines name every exit-2 cause: an empty or absent `ship.direct`, a
  binding that cannot be loaded, and `--binding` with no path. Migration: a caller that read exit
  1 as a usage error reads 2; `Push-Direct.ps1` never passes `--binding` and is unaffected.
- `bin/external-audit.py` refuses CR and LF, beside `&|<>^%`, in an argument to a `.cmd` or `.bat`
  shim, and runs git by the path `shutil.which` resolves, a `git.cmd` shim included, with the same
  refusal on git's arguments. A grok home writable by other accounts that holds a file the seeded
  copy would not replace is refused, not read.
- The forbidden-token hook's refusals say why: a body file it cannot read names the two causes (a
  file the same command writes, a path through a shell variable) and the fix, and with no list a
  landing directory that is not there names a `cd -` or `cd $VAR` it does not expand, or a `cd` in
  quoted text or a heredoc it cannot tell from a script. What it refuses is unchanged. The README
  says where `OURO_FORBIDDEN_TOKENS` is set.
- The release tools: the tracker-reference scanner carries a single-quoted Python string across a
  line its trailing backslash continues; `skill-growth.py` reads an on-invoke cell printed as
  `< 20`; the export stops when ls-remote of the public URL changes while the gates run, and gives
  a copied 100755 file its executable bit in the verification repository.

- The anchor gate, the docs-freshness gate and the anchor parser read a path holding `[` as spelled:
  `-LiteralPath` where a repo or doc path was handed positionally to a wildcard-aware cmdlet, the
  parser probing a span literally first and as a wildcard only when it holds one. The parser no
  longer folds case when it counts paths and fragments or matches a path at a `-Ref`, and a suffix
  match takes whole path segments only (x/foo.ps1 no longer names bax/foo.ps1).
- `Test-ChangelogEntry.ps1` prints a `NOTE` line counting the working tree's uncommitted paths,
  which it does not read: run before a commit, its green no longer reads as coverage.
- `Sync-Branch.ps1` and `Push-Branch.ps1` name each other where they cited a deleted skill's step
  numbers.
- `ouro run` brings a worktree or a local branch it reuses to `origin/<branch>` before it works on it:
  a fast-forward when origin is ahead, the local head kept when it is only ahead of origin, and a
  `**Stop:** open decision` naming the local commits when the two have diverged, with nothing reset.
  A failed `ls-remote` or fetch of the branch there is a driver error. Before, a run resumed in an
  old worktree kept its head, missed commits pushed to the branch since, and failed its push as
  non-fast-forward.
- `ouro run` and `ouro triage` check the `claude` CLI before any write, branch or node: on PATH,
  signed in (`claude auth status --json`), and listing every flag the driver passes. A failure is a
  driver error (exit 1) naming the check and the remedy. A node's stop with no question is a driver
  error too, never a `needs-ruling` stop with an empty question.
- `ouro triage` reads the issue's body again before it writes anything. When the body changed while the
  TRIAGE node graded it, triage writes no body line, label or verdict comment, posts
  `**Stop:** open decision` saying so, and exits 3; run it again on the body as it stands. Before, the
  verdict's body write overwrote the edit.
- A `**Triage**` comment holds one verdict line and one of each closing line the driver writes: a
  node's own verdict opening is dropped in more forms (the key or the word in bold, a lowercase key,
  the word followed by `:`, `(` or a dash), and so is its own copy of a SPLIT's owner-files-the-children
  line or of a PROMOTE's `Parts:` line.
- FUSE is no longer a TRIAGE verdict: the contract never defined it and the driver moved nothing on it.
- `ouro run --dry-run` names every trigger of the FOLLOWUP node at step [11]: the record's Follow-up
  section, an L finding of the last review round, or an OUTSIDE finding of a later round.

- `ouro run` waits for GitHub to report the pushed head before it watches a pull request's checks:
  it reads the pull request's `headRefOid` until it is the pushed commit, bounded by the watch limit
  (`OURO_WATCH_LIMIT`, 900 s by default), and past the bound ends as still running (exit 0), so a
  re-run resumes there. Before, GitHub serving the old head for minutes after a push had the watcher
  read green checks of the old head. A `headRefOid` that differs from the reviewed head at the merge
  decision now ends the run as waiting (exit 0, resumable) instead of a driver error (exit 1).
- The token hook no longer reads `git` directly followed by a subcommand that never commits (`grep`,
  `log`, `diff`, `show`, `blame`, `status`, `rev-parse` and the other read-only ones) as a `git commit`
  because the word `commit` appears later as an argument. Before, `git grep 'reverts commit'`,
  `git diff -- commit.py` or `git grep -C 3 commit` read as a commit, so a `git grep -F <pattern>` beside
  it was opened as a body file and refused, and a grep's `-C 3` was judged as a directory named 3. Any
  other words between `git` and `commit` still count, so no real commit goes unscanned.
- With no workflow at `origin/<default>` whose `on:` names `pull_request`, `ouro run` runs every
  selected gate itself in every class (a gate whose `ci` only prints a notice included), grants
  them all to the BUILD node, opens the pull request with a record sentence saying so, skips the
  checks step, and posts `**Ready for merge**` instead of merging unattended, since the merge
  script refuses a pull request no check ran on; triage grants the same gates. Before, such a run
  stopped at the watcher, which had no run to watch. The watcher's zero-run stop names the missing
  workflow and `Install-Ouro.ps1 -CI` in place of "push (or re-push)", `/ouro:init` says what
  declining `-CI` means, and `python3 bin/ouro-binding.py pr-workflows` prints the workflows that
  check a pull request.
- Under `ship.review = "external-audit"`, an `S` whose diff the driver would grade `express` and
  whose every path is a docs file (`.md`, `.txt`, `.rst`, `.adoc`, an image) skips the external
  reader: no reader round is spent, the record reads `Review: none` with a note saying the diff is
  docs-only, and the merge decision runs as under `none`. A diff with one other path keeps the
  reader, and a fix round that adds one brings it back.
- A run whose acceptance names a command-like span (a runner, after any leading `NAME=value`
  words) while the BUILD node would get an empty command list (no `[[gate]]` for the area runs
  here or declares `paths`, and the span's prefix is not a runner the brief grants) stops with
  `**Stop:** gate uncovered` before the first build, naming the command; triage says so in its
  comment. Before, such a run spent every fix round on a check no node could act on.
- `bin/Install-Ouro.ps1` asks the Action's repository for the tag before writing the docs-freshness
  or CI workflow: an absent tag refuses the file with a `REFUSED:` line naming the tag and the
  newest published one (in version order) and says to update the plugin; a gh that cannot answer
  writes the file with a `WARNING:` that the pin is unverified; `-WhatIf` asks too. Before, the
  workflow pinned `v<installed version>` unchecked, and a version the public repository never
  published left a `uses:` line that cannot resolve.
- A verification that re-raises an H or M finding a fix must answer (not an OUTSIDE one) of the
  verification before it unchanged (the same file, claim and evidence) stops the run with
  `**Stop:** open decision` naming the finding and
  the two rounds, before the cap; the first review's finding counts once a verification re-raises
  it. Before, such a run spent every fix round to the cap on a finding the fixes never moved.
- `bin/Install-Ouro.ps1` prints a `WARNING:` naming the repository's owner when that is a person
  other than the operator it writes into `owner.ruling_approvers`, since the owner's comments then
  close no needs-ruling or promotion; an organisation owner, or an owner who is the operator, draws
  none. The repository view it already reads also asks for the owner.
- **Consumer contract:** `models.builder` defaults to `opus` where a binding leaves it out: the
  BUILD, TRIAGE, FOLLOWUP and CHECKPOINT nodes run on opus, which halves the fix rounds at equal
  cost per run; `models.builder_trivial` keeps `sonnet`, and an explicit `builder` still wins. The
  default lives in one constant the five readers share. Old form: no `[models]` table meant
  sonnet; new form: it means opus; migration: set `builder = "sonnet"` under `[models]` to keep
  the old builder.
- `bin/forbidden-tokens.py` refuses a bare cd verb that ends its segment with more of the command
  after it (`cd; git commit ...`, `'cd'; git commit ...`) as a cd whose directory cannot be read, as it
  already refused `cd && git commit ...`; before, a segment that held only the verb matched no reader
  and the commit after it was judged at the working directory alone while bash ran it in the home
  directory. A bare cd that ends the whole command, with nothing but separators after it, stays
  unjudged.
- **Consumer contract:** `repo.setup` joins the binding schema, optional: one command line the
  driver runs once in each worktree it works in (the issue's, and the full flow's review copy;
  a marker in the worktree's git directory says it ran), before any gate or node runs there, so
  a repository whose gates need installed dependencies declares the install there and not as a
  first gate; `check` refuses it empty, holding a line break, or naming `<ouro>` where the copy
  has no plugin root, as it refuses a gate's `run`. A setup that fails stops the run with
  `**Stop:** gate uncovered` naming the exit and its output; one that leaves entries the build
  would commit stops it naming them. Old form: none (an install listed as the first `[[gate]]`); new
  form: `setup = "npm ci --no-audit --no-fund"` under `[repo]`; migration: add `repo.setup` for the
  driver and keep CI's install where it is (the first gate, or a workflow step before the gates
  Action), since the Action does not read `repo.setup`.

- `bin/forbidden-tokens.py` reads a cd verb spelled with the quotes and escapes bash removes before
  it runs the word (`'cd' /x`, `"cd" /x`, `$'cd' /x`, `$"cd" /x`, `c\d /x`, `c''d /x`, `\cd /x`) as the cd it is,
  so the directory the call lands in is judged; before, only the bare spelling was read and the
  commit after a quoted or escaped one was judged at the working directory alone.

- `bin/forbidden-tokens.py` reads a `cd` behind one or more `NAME=value` prefix assignments
  (`FOO=1 cd /zz/y && git commit …`; a value bare, quoted or ANSI-C, `+=` too, an escaped
  character inside any part read as text) as the cd it is, so the directory the call lands in is
  judged; before, such a segment matched nothing and the commit was judged at the working
  directory alone. A prefix shape the reader cannot follow (a backtick or `${…}` part with a
  space, a quote it cannot pair) before a cd verb, and a run of more than 64 prefix words, are
  refused as unreadable.
- A SPLIT verdict labels the issue `umbrella`, removes the state label it held (`needs-ruling`,
  `needs-triage`, ...) and ends its comment with the line that the owner files the children by
  hand. No verdict removes `umbrella`: a promotion the node holds on an issue that carries it, an L
  the node promotes (no split lines), and a split that still asks a question are posted as
  NEEDS-RULING with the question the owner answers.
- The anchor gate looks once more for a cited-file fragment that greps on no single line: in the
  text a reader sees, comment leaders at line starts dropped and whitespace collapsed, so a quote
  of a comment the file wraps is no finding; a miss there is reported with a note that a phrase
  split by string concatenation reads so. The repo-wide pass stays line-based.
- `bin/forbidden-tokens.py` reads an unquoted leading `~` in a `cd` target, a space-separated git
  `-C` value or a `GIT_DIR`/`GIT_WORK_TREE` value as the home directory (`HOME`, else the
  platform's), as bash does; `~user`, a quoted `~`, a `~` after `=` in an option word and every
  `~` in a command that assigns `HOME` stay literal and are refused as missing where they do not
  resolve.
- The installer's warning for an ignored binding says, when the binding's own directory is ignored,
  that a negation line for the binding alone cannot undo that, and names the pair (`.claude/*` and
  the negation line) for a rule that names `.claude`, narrowing for a broader one, or `git add -f`;
  a rule that matches the file alone keeps the un-ignore text.
- `bin/forbidden-tokens.py` names the binding read that ran past the hook's budget
  (`ship.forbidden_check` or `repo.slug`) instead of reporting it as a read that failed, so a
  selftest row red under load says so.
- A merge the landing script attempted after which the pull request still reads OPEN (the default
  branch moved under it, or the platform refused it) is tried once more before the run stops, once
  the reviewed head is re-read and unchanged; the script re-reads everything else before the
  second attempt, and a second failure quotes both.
- Past the watch limit, the pull request's checks watch runs again while the head's pull_request
  runs, the ones the watcher waits for, still move (one appeared, changed status or concluded
  within the limit), at most four more times; a limit in which they did not move ends the run as
  before, with the message to invoke it again. The default branch's watch after a merge is as it was.
