# Release history: v0.2 through v1.3

**Frozen archive — 2026-10-26.** These published release notes and their
**Consumer contract:** migration notes are historical records, not current protocol
authority. The released sections below are preserved verbatim. Current release notes
are in [CHANGELOG.md](CHANGELOG.md); current usage and contracts are in
[README.md](README.md), [docs/contract.md](docs/contract.md) and
[docs/binding.md](docs/binding.md).

## [v1.3.0] - 2026-10-20

- The build brief frames the `overlays.implement` documents as the repository's unconditional
  hazards, not its rule set, and the binding doc says the overlay is inlined whole into every
  BUILD brief with no path condition, so a path-scoped rule stays under `.claude/rules`.
- A run re-points a dead anchor line under the body's `Anchors` heading whose fragments all grep in
  exactly one other tracked file of the same suffix, edits the body and re-stamps it, and stops
  on every other dead anchor; the
  TRIAGE brief reads a Deliverable clause naming a deleted file as stale, not a ruling; the
  `review cap` stop names the branch and the pull request as a hand-off; the checks watcher names
  a CONFLICTING pull request, for which GitHub dispatches no run, and the driver syncs the branch
  once per head when the watcher or the merge reports the conflict.
- A `.claude/worktrees` that is a symlink to another directory in the repository works end to end:
  the driver checks a worktree's ignore rule at its resolved path (before, every run stopped with
  `git check-ignore` exit 128; the target directory must be ignored, and the stop names it), and
  `bin/Remove-LandedBranch.ps1` and `bin/Invoke-BranchSweep.ps1` take the link's target as the
  worktrees root, so a landed run's cleanup and the sweep remove those worktrees. A link that
  leaves the repository stops the run before a branch.
- `bin/forbidden-tokens.py` judges a publishing call at every directory it lands in: the working
  directory, each `cd`, `pushd`, `sl`, `Set-Location` or `Push-Location` target (anything after
  it, or a `bash -c` or `pwsh -Command` string around it, included), each `git … commit`'s (and an
  `env -C` before it) `-C`, `--git-dir` and `--work-tree` target and each `GIT_DIR` or `GIT_WORK_TREE`
  assignment, chained in order (a `cd` inside quotes keeps the ones before it), all of which must pass under
  one read budget, so a commit into a public clone from a private checkout is refused when there
  is no list; a refusal names its reason (a binding that fails its check, a binding that requires
  the check as the missing list, no `gh` on PATH, the visibility read's answer, or a named
  directory that does not exist).
- A follow-up the driver files carries the area and type labels the FOLLOWUP node names, filtered to
  the binding's `[labels]` sets, the run's own area labels when it names no area, and `needs-triage`;
  the brief carries the sets. The TRIAGE answer's `labels` names an area or type label the issue
  lacks, in any case; the driver counts it in its label pre-check and writes it, in the binding's
  spelling, with the verdict's state labels, so a missing label is graded, not ruled; a named type
  is dropped where the issue has one.
- **Consumer contract:** a `[[gate]]` whose `ci` only prints is a notice that another workflow runs
  the gate in CI: on a pull request neither the driver nor the BUILD node runs it, and the record
  names the gate among the checks by its `ci` text. A `ci` whose printed text starts with `skip`
  keeps its reading, a gate the checks cannot run, which the driver runs itself, as it runs the
  token scan by name; the direct lane runs every selected gate locally as before. Every message
  the driver logs starts with the clock, `HH:MM:SS` UTC, on its first line; the weekly and compile
  reports print as they are.
- The anchor gate's brace expansion follows the contract: the brace sets of a body expanding past
  64 paths, counted across its distinct spans, are a finding on the span that crosses, which says to
  trim the sets, not a literal path check; a comma-less or unbalanced set no longer
  stops a later set expanding; and `docs/contract.md` states that an expansion with a rooted, home
  or parent prefix is skipped.
- `bin/external-audit.py` runs the grok CLI bare: it points the home variables at an empty
  directory and `GROK_HOME` at a per-session directory under the artifact folder, seeded with
  copies of `auth.json`, `config.toml` and `models_cache.json` from the user's grok home, so the
  reader no longer loads the working directory's CLAUDE.md files, skills, MCP servers and hooks.
  The copied `auth.json` is removed when the driver exits, and a user grok home with no
  `auth.json` fails the round.
- **Consumer contract:** `[repo].branch_prefix`, an optional binding key, names the prefix of
  every task branch the driver cuts, in place of the first area label, first type label or `ouro`
  it derives otherwise; `ouro-binding.py check` refuses it empty, holding a slash, whitespace or
  `..`, or equal to or starting with a `[repo].protected_branch_prefixes` entry.
- **Consumer contract:** `ship.external_effort`, an optional binding key, sets the external
  reader's reasoning effort, and `bin/external-audit.py` takes `--effort` (default `default`, the
  adapter's own effort) beside `--model`: grok and copilot `--reasoning-effort`, claude
  `--effort`, codex `-c model_reasoning_effort=`. `ouro-binding.py check` refuses the key empty,
  and the driver exits 1 when an adapter with no effort route is given a non-default value.
  Grok now runs at `--reasoning-effort medium` when no effort is set, where it ran at the CLI's
  default, high; set `external_effort = "high"` to keep that.
- `bin/external-audit.py` reads an empty `--effort` as `default`, the adapter's own effort, where it
  passed no flag and let grok run at the CLI's high; `docs/binding.md` states the validator's rule for
  `ship.external_effort` and the README's key table carries its row.

## [v1.2.0] - 2026-10-19

- **Consumer contract:** the REVIEW brief carries the issue body and the comments that amend the
  spec, as the BUILD brief does, and says a change that does not deliver the issue's Deliverable,
  as those comments amend it, is a finding at the severity of what it misses, IN-FIX in every
  round (contract section 7 states the exception: a reviewer that raises it again after an
  approver's ruling needs a new ruling, as any finding at a later head does); the external
  reader's goal carries the Deliverable section and those comments, the newest kept when cut, in
  place of the What section. A resumed run checks the body's edit history as a fresh run does, so
  an untrusted edit after the triage verdict stops it before any node reads the body. Migration:
  none.
- `triage` checks the issue's area and type labels before it posts, as the shape gate reads them
  (at least one area entry, exactly one type entry, compared without regard to case, a kind with
  no declared set skipped): a PROMOTE or CHECKPOINT the counts refuse is posted as NEEDS-RULING
  naming the kind and the binding's set, and no shape-gate demotion follows; a CHECKPOINT at L is
  a SPLIT, as a PROMOTE at L is. The `**Triage**` comment of a PROMOTE carries a `Parts:` line,
  the parts the driver will read.
- **Consumer contract:** a checklist part is a task-list item of the contract's form alone,
  `- [ ] k. **<title> (Flow: <class>).**`. Before, any `- [ ] k.` item anywhere in the body was a
  part, so a numbered task list made the issue a checklist and its landing ticked part 1 and
  left the issue open; now such an item is not a part and is not ticked. Migration: a body whose
  parts lack the bold title or the Flow class is a plain deliverable until it carries them.
- The TRIAGE brief carries the anchor gate's answer for the body at the default branch's tip, a
  body with no parseable anchor reported as the shape gate reads it, and says which path shapes
  the gate did not check; its file list says when it is cut, and it names `.claude/worktrees/`
  as not the repository.
- **Consumer contract:** the driver reads the owner's ruling on the pull request. Before, a reply
  that a review finding needs no change was ignored and every run opened a fix round; now a
  comment from a `[owner].ruling_approvers` login whose first line starts
  `**Ruling**` ends the fix rounds for the newest review record before it, and the decision
  comment gains a `Ruled:` line. Migration: none; post the ruling after the review record.

## [v1.1.0] - 2026-10-19

- The driver runs no gate the pull request's checks run, under full as under the other classes:
  the checks are the gate run, and the record names the selected gates they run in place of
  "none run by the driver". The BUILD brief names, in every class, each selected gate with
  paths, for the builder to run when its change touches them, and the reviewer runs no build,
  gate or suite the checks already ran, in the probe format as in the sized review.
- The pull request record is the newest BUILD answer's: door, blast radius, summary, follow-ups
  and the gates it ran move with each fix round, and a fix round that changes no file commits
  its record on an empty commit instead of ending the run. The BUILD brief carries the trusted
  triage verdict and the trusted comments after it, which amend the spec. The `Review:` line
  counts a finding once, a verification marking one that still stands `STANDS`. `ouro run N`
  refuses, before any branch, when an open pull request from another branch carries `Fixes #N`
  or this part's `Part` line, and the follow-up node no longer refiles a line that names an issue.
- A path under `.claude/`, which the harness refuses the build node, is a manual step: on a
  promotion the triage node names the paths the change edits among the body's backticked ones,
  triage writes a `Manual: <path>` line for each, the build node returns the edit it would make
  there, the pull request lists them under Manual steps, the union over the branch's commits,
  and waits for a person to apply them and merge, whatever the policy. A denial under `.claude/`
  the node meets joins that list, the run no longer stops on it as an open decision, and a
  direct-lane build with a step takes the pull request path.
- Copilot gets one review round per pull request: a thread its re-review of the fix opens, or
  one left after a round that declined, is answered in a reply for the merger, not built again.
- The anchor gate skips a backticked span that holds a double quote, so the quoted argument of a
  backticked command is no longer read as a fragment that fails to grep. Quoted strings and
  spans are scanned together left to right, so backticks inside a quoted fragment still belong
  to it.
- **Consumer contract:** `ouro run N` on an `agent-ready` issue carrying `checkpoint` runs the
  spec trust check and the anchor verify, then a read-only node (Read, Grep, Glob), and posts its
  `**Checkpoint finding**` comment: no branch, worktree or commit, exit 0. A trusted finding newer
  than the newest trusted `**Triage**` makes a re-run refuse, naming it.
- The scripts only the weekly pass ran are gone: `apply-manifest.py`, `drift-claims.py`,
  `Get-IntakeTargets.ps1`, `Get-DriftAuditTargets.ps1`, `Get-LoopMetrics.ps1`,
  `Test-BlockedTriggers.ps1`, `Test-UmbrellaChildren.ps1`, `Test-IssueLabelInvariants.ps1`
  and `Test-RepoVariablesDoc.ps1`, with their suites, and the `verifier` agent. The
  binding schema drops `[models].verifier`, `[rolling_issues].drift_audit` and `[overlays].drift`;
  `Get-RollingIssueNumber -Key` defaults to `rolling_issues.loop_runs`; `Vendor-Ouro.ps1` no
  longer copies `apply-manifest.py` or `drift-claims.py`.
  **Consumer contract:** a binding that sets `[models].verifier`, `[rolling_issues].drift_audit`
  or `[overlays].drift` now fails the binding check as an unknown key; delete the key. A
  workflow step that calls a deleted script is removed, and a `suppress_prefixes` entry naming
  `bin/apply-manifest.py` is dropped. A caller of `Get-RollingIssueNumber` that relied on the
  `-Key` default passes `-Key` or `-Title`.
- `ouro triage` runs the shape gate on an issue it promotes, as `ouro run` does at verify
  (`Test-AgentReadyShape.ps1 -Issue N -Demote`). A body the gate refuses goes back to
  `needs-triage` with the gate's finding, and the command exits 3.
- The driver reads the push lease only from `Sync-Branch.ps1`'s own `lease:` line. A merged base
  commit whose subject held `release: ` was read as a lease, and the push refused it.

## [v1.0.2] - 2026-10-18

- The release carries `.docs-freshness-ignore`, whose entries exempt the paths this changelog's
  history names and 1.0.0 deleted. Without it the docs gate failed on the released tree.
- `bin/Test-ChangelogEntry.ps1` reads a python tool's docstring usage lines only, and its
  `-ModulePattern` default is `^bin/[^/]+\.py$`: the argparse reading merged after v1.0.1 is
  withdrawn before any release carried it.

## [v1.0.1] - 2026-10-17

- A driver fix round costs what contract §4 and §7 say. Under the full class it re-runs locally
  only the gates whose paths changed since the last head where every gate passed, a gate the fix
  adds to the binding and the gates the pull request's checks do not run; a gate already green on
  an earlier head keeps its line in the record. A verification finding the REVIEW node marks
  `OUTSIDE`, in lines the fix did not change, starts no fix round and goes to the follow-up step
  with the low findings. The verification brief carries the fix's range and whole diff, and a
  finding still standing from the round it answers is IN-FIX. A first review's scope is ignored,
  and when the fix's diff cannot be shown whole the brief asks for none.

## [v1.0.0] - 2026-10-17

- The contract names the driver as the loop. `docs/contract.md` §1 says `bin/ouro` runs the fixed
  sequence and that humans answer rulings and merge the pull request the binding's `[ship].policy`
  leaves to them; §5 and §9 admit the merge `merge-on-green` grants per class beside
  `trivial`; every citation of a removed skill in the contract, the rationale, the `bin/` scripts'
  help and messages and their suites is cut or names the driver step, bar the sync, push and
  merge scripts' numbered references to the removed landing steps. Removed from §7: the rule
  that a commit, comment or push joins its step with `&&` (`guard-commit.py`'s worktree clause), and the
  clause that the prose stays because a guard fails open.
- `CLAUDE.md` loses the rules that `master` moves only at a tag, that a wave lands as a batch
  under one bump, and the export steps after the tag; the same-day rule becomes the mass budget: a
  release adds no net lines across `skills/`, `docs/`, `hooks/`, `bin/` and `tests/`, and a rule
  survives only with a check that fails without it. The README describes the shipped thing: the
  protocol, the binding, the driver, the four entry points and the gates.
- `bin/Get-CompileProgram.ps1` prints a proposed batch as `batch: <numbers>` in place of
  `/ouro:fuse <numbers>`.
  **Consumer contract:** a reader of the renderer's text that matched the `/ouro:fuse` line
  matches `batch:` instead; `/ouro:fuse` no longer exists.
- The plugin is 1.0.0.

- Four skills replace thirteen: `/ouro:run <N> [--part <k>] [--dry-run]`, `/ouro:triage <N>
  [--dry-run]`, `/ouro:weekly [--post]` (also `compile`) and `/ouro:init` each bind, run the
  matching `bin/ouro` command and relay its message. The skills `/ouro:execute`, `/ouro:land`,
  `/ouro:land-batch`, `/ouro:e2e`, `/ouro:seam`, `/ouro:fuse`, `/ouro:compile`, `/ouro:intake`,
  `/ouro:drift`, `/ouro:external-audit` and `/ouro:review` are gone, with the `evals/` cases that
  exercised them; the reviewer prompt the driver quotes lives at `bin/ouro/prompts/REVIEWER-PROMPT.md`,
  and the `Review:` line's pattern is defined in `docs/contract.md`, which `bin/loop-outcomes.py`
  reads.
  **Consumer contract:** a session, bot or workflow that invokes a removed skill by name calls
  `python3 <plugin>/bin/ouro run <N>` (or `/ouro:run <N>`) in place of `/ouro:execute`,
  `/ouro:e2e`, `/ouro:land` and `/ouro:fuse`, and `/ouro:weekly compile` in place of
  `/ouro:compile`. The weekly pass that ran `/ouro:drift` and `/ouro:intake` goes too (the next
  entry). `/ouro:triage` now takes one issue number and no `--all`.
- `templates/weekly-pass.yml` and its suite are deleted, and with them the binding's
  `[models].weekly` role: no drift audit or intake grading runs unattended any more. Contract §9
  governs the driver's unattended runs and the gates a consumer's workflows run.
  `bin/Vendor-Ouro.ps1`'s workflow note no longer exempts a loop-outcomes step,
  `bin/Install-Ouro.ps1`'s pin reminders name the agent-ready shape-gate workflow, and
  `python3 bin/ouro weekly --post` says it posts on the `[rolling_issues].loop_runs` issue.
  **Consumer contract:** a binding that sets `[models].weekly` fails the binding check: delete the
  key. A repository that copied the weekly pass deletes its `.github/workflows/` copy.
- Two hooks go and one shrinks: `hooks/hooks.json` no longer registers the worktree-removal guard
  or the Agent brief check, and `hooks/guard-commit.py` keeps only its author rule, so a commit
  carrying an agent's identity is still refused and a chained, piped or non-draft publishing call
  in a worktree no longer is.
  **Consumer contract:** a consumer that copied those two hook entries into its own settings drops
  them; there is nothing else to migrate.
- `bin/Invoke-LandBatch.ps1`, `bin/Compress-Branch.ps1` and `bin/Test-LandState.ps1` are deleted
  with their suites: nothing called them but the removed land skills.
  **Consumer contract:** a wrapper that runs one of the three stops working; the driver's merge
  step replaces them.
- The binding's `[ship].policy` accepts `merge-on-green`, under which the driver merges a green,
  cleanly reviewed pull request of a class listed in the new `[ship].unattended_classes` (`express`,
  `standard`, `full`; default `express` and `standard`) and posts `**Landed unattended**`.
  `ship.review = "none"` forbids it as it forbids `trivial-merge`, and `unattended_classes` without
  `merge-on-green` is refused.
  **Consumer contract:** `policy` gains a value and `[ship]` a key, both optional; a binding that
  declares neither reads as before and there is nothing to migrate.
- The driver selects its gates from the binding in the tree under test, as CI does, so a branch
  that adds, drops or changes a `[[gate]]` is gated by its own entries.

- A new python package, `bin/ouro`, runs the loop's fixed sequence as one program, beside the
  skills, which keep working unchanged. `python3 <plugin>/bin/ouro run <N> [--part <k>] [--dry-run]`
  takes an agent-ready issue through the worktree, the build, the binding's gates, the push, the
  draft pull request, the review rounds, the checks and the merge decision to the landed change,
  calling the `bin/` land and gate scripts as subprocesses and a model only at the BUILD, REVIEW,
  FOLLOWUP and TRIAGE nodes; it keeps no state between invocations and continues from the tracker's
  labels, comments, the branch and the pull request; a resumed run whose issue has left
  `agent-ready`, or holds `needs-ruling` or `checkpoint`, stops with exit 3 and writes nothing, and
  what the BUILD node answered that a later invocation needs is the last line of its commit
  message, `Ouro-Build: <json>`. `triage <N> [--dry-run]` grades an issue with the TRIAGE node and
  posts the `**Triage**` comment, the body lines and the labels; `weekly [--post]` prints (or
  posts) the `loop-outcomes.py` table; `compile` prints the next wave. Exit 0 is landed, waiting on
  the owner's merge or on running checks; 1 a driver error; 2 refused before any branch; 3
  stopped, by the run's own `**Stop:**` comment with `agent-ready` swapped to `needs-ruling`, or by
  the labels a resumed run reads. It refuses a `checkpoint` issue, and a spec whose body an
  untrusted author edited after the newest trusted `**Triage**` comment; it runs the reviewer the
  binding's table gives the issue's Size (the REVIEW node, Copilot requested through
  `requestReviews` with one fix round, or `bin/external-audit.py`, falling back to the REVIEW node
  when the reader is unavailable), re-derives Size from the first commit, and takes `Flow: direct`
  issues through `Push-Direct.ps1` from the main checkout only when it is clean and at origin's
  tip, or ahead of it by nothing but this issue's own unpushed `Fixes #N` commits, which it ships
  without a build; an open pull request on `direct/<N>` is resumed at its checks. A red
  default-branch run gets one fix commit through the lane while the main checkout still meets
  those conditions, and stops otherwise; a second red reverts both and stops.
  **Consumer contract:** `bin/ouro` is a new command surface with the four commands and exit codes
  above, and it reads the binding through `bin/ouro-binding.py` with no new key; no existing
  invocation changes and there is nothing to migrate. A review record on a pull request the driver
  opens is a comment whose first line alone is `**Review** round <k>: CLEAN|FINDINGS @ <full head
  sha>`, counted only from the authenticated user or a login in `[owner].ruling_approvers`.

- No landing step moves or writes the owner's main checkout. `/ouro:land` step 13 fetches and runs
  `Test-AgentReadyAnchors.ps1 -Ref "origin/$DEFAULT"` from any checkout, in place of fast-forwarding
  the main checkout so the gate could read a working tree. `/ouro:land-batch` always runs from its
  batch worktree, which `Invoke-LandBatch.ps1 worktree` adds detached at `origin/$DEFAULT`, whatever
  branch the main checkout is on: 5b pushes `HEAD:refs/heads/$DEFAULT`, `reset` resets HEAD, and the
  local `$DEFAULT` branch is never moved. The skills lose the main-checkout form and the guards that
  existed only for the fast-forward.
  **Consumer contract:** `Get-AnchorFindings.ps1` and `Test-AgentReadyAnchors.ps1` read the working
  tree and index only; both now take `-Ref <commit-ish>` and read that commit instead, and a ref that
  does not resolve throws. `Invoke-LandBatch.ps1 worktree` added the batch worktree on the local
  `$DEFAULT` branch, and `push` sent that branch; the worktree is now detached and `push` sends HEAD,
  so a consumer's own wrapper reading the local `$DEFAULT` after a batch reads origin's tip from the
  worktree's HEAD instead; no other migration.

- The forbidden-token hook scans a publishing call's command text less the value of each `-R` or
  `--repo` written outside quotes, before any heredoc: that value addresses the call and is not
  published, so a repository slug that matches a list entry no longer refuses the call. A `-R`
  inside a quoted body is still scanned, and with unbalanced quotes the whole text is.
- `/ouro:execute` refuses before reading the issue on a host where `gh api graphql` fails, as a
  Claude Code cloud session's proxy makes it, since every tracker call the skills make needs it.
  The brief hook's refusal names where each missing line is defined (the head and the turn budget
  in contract §7, the state note in the dispatch reference), and `/ouro:triage` and
  `/ouro:intake` tell their verifier's brief to carry all three.
- `/ouro:compile`'s renderer reads an issue as in flight when an open pull request closes it
  (GitHub's closing references) or names it on a `Fixes #N` or `Part k/n of #N` line; an in-flight
  issue that is not `agent-ready` is printed as a finding instead of counted. A batch whose dropped
  member was the only link prints its survivors as separate fuse sets, or none. `finish` marks a
  blocked child whose trigger names only closed issues as `fired` and ends with the next move for
  each child. The loop metrics and the outcomes reader's quality table count a part's pull request
  by its `Part` line.

## [v0.12.1] - 2026-10-15

- An area's express and standard issues can build as one batch. The batch intent of `/ouro:compile`
  prints `Area batches` after the analyzer's batches: the issues the graph reads as `agent-ready`,
  in no analyzer batch, not in flight and in scope, that carry a declared area label, no
  `checkpoint` label, no checklist line and no `Flow:` line other than express or standard, grouped
  under the first of `-Areas` an issue carries, in ascending order, in groups of at most five with a
  group of one left out, each as a `/ouro:fuse` line with an `area:` line under it. The renderer
  reads the issue list with `body` for it, and `-IssuesJson` takes `body`. `/ouro:fuse` takes an
  area batch the owner names as one region though its members share no file, the five-issue ask
  still its only cap, reviews it as one cluster at the region's class under contract §7's three
  rounds, and records each landed region's issues, changed lines, files, minutes and rounds as one
  comment on the `[rolling_issues].loop_runs` issue, posting nothing where the key is unset or no
  issue carries the title. Removed with it: §7's stop of the run for an issue whose findings do not
  converge. The issue is reverted out with `git revert`, newest commit first, its `Fixes` line
  leaves the pull request body, it gets the `**Stop:** review cap` comment and `needs-ruling`, a
  member that builds on it leaves with it, and the rest land.

- A change inside a workflow step's `run:` block owes a probe row in the `**Mutation-tested**` table
  in place of the untested-line note: the step's literal `run:` block, run against a stub of the CLI
  it calls in a scratch workspace once with the change and once without, each run's exit read; the M
  brief asks for it where the diff touches such a block. A choice the spec's own clauses already
  decide, an exit code its table assigns, an error class it names or a default it states, is
  settled, not a STOP: the builder lists each under a `Settled by the spec` hand-back section, which
  the session reads against the spec before the commit, and `/ouro:fuse` step 3 reads a hand-back
  the same way. A gate whose CI leg is path-filtered declares as its `paths` pathspecs that select
  the same paths as that filter, and every file the gate reads; no check compares the two.
  `/ouro:fuse` follows the Flow class: a region takes the tightest class among its issues, never
  direct, the region pull request's checks are the gate run, its fresh clone runs only what
  `/ouro:execute` runs for that class, narrowed by `paths`, and a real failure in those checks takes
  a fix round counted toward the review cap. The mutation reference names a fifth shape that stays
  green, a predicate ported from one language to another, whose row is a differential one. Removed
  with it: the fused region's run of every `[[gate]]` whatever its `areas`, and `fuse` in the list
  of skills that never read `paths`.

- A deliverable's parts land one cycle each: a part carrying a risk surface is its own pull
  request, which opens with a `Part k/n of #N` line and carries `Fixes` only when it is the last
  part, and the session ticks the part's box in the issue's checklist after the merge, unticking
  it when it reverts the pull request. Triage writes such a deliverable as a checklist with one
  Flow per part and a `Part k:` prefix on its anchor lines, and `/ouro:e2e` takes the next
  unticked part; `/ouro:fuse` refuses a checklist issue, and a checklist part is never direct. Of
  the fuse exceptions only an open decision and a different blocking trigger remain: a full-class
  or consumer-contract part is a risk surface staged inside the same issue. The outcomes reader
  reads a `Part k/n of #N` line in a landed pull request as a part landing: an open attempt's record
  carries the highest part landed since its promotion and its n, and only a `Fixes` line lands.

## [v0.12.0] - 2026-10-15

- A fourth Flow value, `direct`, is a lane for text-only changes: no worktree, no PR and no
  review; the session commits on the default branch with `Fixes #N`, runs locally the gates the
  diff selects, and pushes with the new `bin/Push-Direct.ps1`, and CI on the default branch is the
  check. `Push-Direct.ps1` stops on a path outside the binding's `[ship].direct` globs, pushes
  plainly where the default branch requires no pull request, pushes a `direct/<issue>` branch where
  it requires one without an approving review, and exits 3 with `fallback: express` where an
  approval is required or the protection read fails. Triage stamps `Flow: direct` only when the
  whole deliverable lies inside the globs; `bin/loop-outcomes.py` counts a first-parent commit
  with a `Fixes #N` line and no `(#PR)` subject as a landing; contract §7's author-reviewer rule
  names the lane as its exception.
  **Consumer contract:** the binding had no `[ship].direct`; it now takes a list of globs, empty
  when absent (the lane is off), and `ouro-binding.py check` refuses one that matches a CI
  definition, the hook registry or a hook, release machinery, the binding or a gate's own script,
  and one whose literal prefix, before its first `*`, `?` or `[`, starts with `.github/`,
  `actions/`, `hooks/`, `release/` or `.claude/`, or whose first path segment holds a wildcard
  (it reaches every top-level directory); `ouro-binding.py direct <path>...` is new and prints
  the paths no glob matches; no migration.

- Triage gains a FUSE verdict: issues that change the same skill file, hook, script or mechanism
  become one issue listing their parts, and a part that is an open decision, sits on full's list
  while the rest does not, is blocked on a different trigger or must be revertible alone stays its
  own issue; `/ouro:compile` names each batch's members as FUSE candidates. Under express
  the review reads the local range, fix rounds are committed locally, and one push at convergence
  opens the draft; standard and full open it at the first push.
- The gates Action's path selection keeps the narrowing on a workflow edit: with `select-by-paths`
  on, a diff that touches a workflow and nothing under `actions/gates/` in the checked-out
  repository runs the `paths`-narrowed set; every gate still runs when the diff touches that
  path, cannot be read, has no previous tip, or belongs to another event.
  **Consumer contract:** with `select-by-paths` on, every gate ran when the diff touched a
  workflow under `.github/workflows/`; it now runs the narrowed set, so a pin bump in a workflow
  runs the narrowed set under the new Action; a consumer that wants the full set on a workflow
  change runs the Action with `select-by-paths` off.
- The external-audit driver's `--timeout` default is 600 s, the reader's cap in every class, and
  `--help` prints it; a run that passes no flag no longer waits up to 900 s.
  **Consumer contract:** `bin/external-audit.py --timeout` defaulted to 900 s; it now defaults to
  600 s; pass `--timeout 900` to keep the old bound.
- An M under `ship.review = "external-audit"` takes `/ouro:review` alone: the review table drops
  the external reader from that row, and an S and a fused change keep it. The reader runs with
  `--timeout 600` in every class, not only under full, and the full-only rule that it runs first
  and alone is gone with the second reviewer it ordered.
- A new opt-in workflow template, `templates/agent-ready-shape.yml`, runs the shape gate's
  demoting mode on each `agent-ready` promotion and sends one that skipped triage back to
  `needs-triage`; the weekly-pass template's shape-gate comment now points to it, and the triage
  skill, the applier and the contract describe the labeled-event demotion as that template's.
  The triage skill's description says the anchor or shape gate demoted the issue, not a weekly
  gate: the weekly template runs neither in its demoting mode.

## [v0.11.5] - 2026-10-14

- Triage stamps a flow class, `Flow: express|standard|full`, beside `Size:`, and every later
  step's weight follows it: express (nothing executed changes, or an S-shaped change a same-PR
  test or a gate accepts) and standard run no gate in the session, open the draft at the first
  push and take the pull request's checks as the run, with a gate those checks do not run still
  run locally by its paths; full (a change to what runs the checks, or a migration, flag default
  or authorization change) runs locally the gates whose own code the diff changes and watches the
  default branch's run. Execute re-derives the class from the diff and only tightens it; an issue
  with no `Flow:` line reads as standard; with no checks to run, the session runs the selected
  set locally and says so. The review's weight within the reviewer the binding's table names is
  the reading brief for express, sized with a budget that re-runs nothing the checks already ran
  for standard, and the probe format for full, with an external reader run first and alone,
  capped at 600 s. Removed with it: the 20-line sized bound and the derive-the-weight-at-review
  paragraph, "any tree difference re-runs the gates" for express and standard, the review's "no
  heavy local gate while a review is in flight", and the three-way draft timing.
- `hooks/guard-commit.py` refuses a non-draft `gh pr create` and a publishing call chained after
  `;` or `&` or inside a pipeline only when the call's directory is inside a `.claude/worktrees/`
  worktree, where every loop run lives; a developer's own checkout keeps plain `gh pr create` and
  pipelines. The agent-identity rule still applies everywhere.
- `hooks/guard-worktree.py` no longer refuses a background command started inside a worktree; in
  its place `bin/Remove-LandedBranch.ps1` and `bin/Invoke-BranchSweep.ps1` list the processes
  whose current directory is inside the worktree before deregistering it, on Linux and macOS,
  and stop naming them. On Windows the removal's own refusal stays the stop.
- `bin/Get-FootprintGraph.ps1` links nothing for an issue carrying `checkpoint`: a checkpoint run
  edits no file, so the issue keeps its footprint but is in no collision, no batch and no ruling's
  leverage. `/ouro:compile` could print a `/ouro:fuse` set holding a checkpoint issue, which
  `/ouro:fuse` refuses.

## [v0.11.4] - 2026-10-13

- `bin/forbidden-tokens.py hook` honours a binding's `ship.forbidden_check = "off"` on a machine
  with a list too: a hit in the command text or a body file passes there, as it does for the
  applier; no key and `"required"` keep the hit, a call naming another repository with `-R` or
  `--repo` reads as if the key were absent, and the binding is read only when a hit or a missing
  list would block. A private repository whose own text carries the list's values could not
  commit from a machine holding the list.

## [v0.11.3] - 2026-10-13

- `release/skill-growth.py`, this repository's own report-only gate on every pull request that
  touches `skills/` or `agents/`: against the merge base with `origin/master`, one line per `*.md`
  there whose line count changed, a `SKILL.md` counted by its body and every other file whole,
  each group's total, the overall net and, where `claude` is on PATH, the on-invoke token estimate
  of the checkout for each skill or agent a changed file belongs to, or that the table has no row
  for it. Its fixture suite, `tests/skill-growth/`, skips in a vendored tree, which has no
  `release/`.

## [v0.11.2] - 2026-10-13

- The v0.7.0 entry names this repository's own gates workflow in prose, not by a path: the
  export excludes that workflow, and the public tree's docs gate read the path as dead.

## [v0.11.1] - 2026-10-13

- `hooks/guard-delete.py` reads `CLAUDE_SCRATCHPAD_DIR` alone as the scratch directory when it is
  set, as its docstring said; it had unioned it with `TMPDIR`, `TEMP` and `TMP`, so on Windows,
  where `TEMP` is always set, a repository under the temporary directory read as scratch and a
  worktree delete there passed the location check. `.docs-freshness-ignore` leaves the public
  tree with the old changelog it exempts, so the public docs gate reads no stale entry.

## [v0.11.0] - 2026-10-13

- `bin/forbidden-tokens.py hook` follows the manifest applier's rule on a machine with no list:
  a publishing call passes when the binding at the working directory declares
  `ship.forbidden_check = "off"`, or declares no key and `gh repo view` reports the repository
  PRIVATE or INTERNAL; a binding that fails `check`, `"required"`, PUBLIC, no `gh` and a failed
  read still block, and every read shares one budget inside the hook's own timeout. A machine
  with the hook and no list could not commit in a private repository.

- `skills/review/REVIEWER-PROMPT.md` and `skills/seam/references/phase-templates.md`, both over
  the guide's hundred lines, gain a table of contents.

- `/ouro:execute` reads the binding in a call of its own, before any other command, and a missing
  binding ends the run there: a first command that chained the binding read with the issue fetch
  read the tracker before refusing, which the eval's execute case caught in one run of three.

- `/ouro:triage`'s Non-negotiables sit right after its binding step, within what a compaction keeps
  of the body, and its S-shape paragraph cites contract §4 instead of restating the shape and the
  mirrored-file exclusion. In `/ouro:triage`, `/ouro:land` and `/ouro:land-batch`, a paragraph that
  restated a script's own refusals is cut to the rule and the gotcha: the script's STOP line names
  the refusal, and its help holds the guards.

- Who reviews what, by `[ship].review`, the issue's size and whether `/ouro:fuse` built it, is
  one table in `docs/binding.md` "Choosing ship.review", with the availability rule for an absent
  reader beside it; contract §4, `/ouro:execute`, `/ouro:review`, `/ouro:fuse`, `/ouro:triage`,
  `/ouro:land` and `/ouro:e2e` cite it in place of their own statements, and the review weight
  by class and size is stated once, in contract §4: `/ouro:review`'s description and the README
  name the bound as the contract's instead of as a number.

- `/ouro:execute`'s body is under the guide's 500 lines, and the rules a run keeps when a
  compaction drops the rest of its body, no push to the default branch, no agent as an author,
  three fix rounds, no silent pick, a read-only checkpoint, sit in a `What a run never does` list
  near its top. The gate selection by `paths`, the mutation rows and the `trivial` and
  `checkpoint` variants move to `skills/execute/references/gate-selection.md`, `mutation-rows.md`
  and `variants.md`, each with a table of contents, as `build-tiers.md` now has; the skill cites
  each at the step that applies it, and `/ouro:land` step 3 and `/ouro:e2e` cite the references
  in place of the sections. The `build-tiers.md` bullet that named one host as never a builder
  goes: the builder is whatever `[models].builder` names.

- **Consumer contract:** `bin/Test-CommandBlocks.ps1` scans every `skills/*/references/*.md`
  as it scans a `SKILL.md`: its `-Globs` default was `skills/*/SKILL.md` and `agents/*.md`, and
  is now those two and `skills/*/references/*.md`. A repository whose own skill reference files
  hold a `bash` or `sh` fence sees them checked from this version; a run line that passes
  `-Globs` is unchanged.

## [v0.10.0] - 2026-10-12

- No skill or agent file pastes a section of `skills/execute/references/dispatch-rules.md` any
  more: the session reads the sections a brief needs into it at dispatch time (the execute M brief
  and the reviewer prompt say which, the build-tiers template and the verifier's brief carry a
  placeholder for them), and a step that applies one cites it; the file gains a table of contents
  that names each section's readers. The ceiling on shared twelve-word runs is zero.

- `hooks/guard-commit.py` on `Bash` and `PowerShell`, and `hooks/check-agent-brief.py` on `Agent`,
  registered in `hooks/hooks.json` with recorded events under `tests/hooks/`: the commit guard
  refuses an author or committer identity naming an agent, a `git commit`, `git push` or `gh`
  tracker write chained after `;` or placed in a pipeline, and `gh pr create` without `--draft`;
  the brief check refuses a dispatch whose prompt names no head, no state-note path or no turn
  budget, the read-only agent types excepted. **Consumer contract:** the old form is those rules as
  prose in the contract and the skills; the new form is one more hook on every `Bash` and
  `PowerShell` call and the first hook on `Agent`, refusing with the reason on stderr; the
  migration is none beyond rewording a refused command or brief as its reason says.

- `hooks/guard-worktree.py` and `hooks/guard-discard.py`, registered in `hooks/hooks.json`, and
  the kill guard's provenance check, with recorded events under `tests/hooks/`: the worktree
  guard refuses `git worktree remove` with a path it cannot read (a variable, a substitution) or
  that does not resolve, and a background `Bash` call from inside a `.claude/worktrees/` worktree
  that does not begin with `cd <absolute path outside .claude/worktrees/>`; the discard guard
  refuses `git checkout -- <path>`, `git checkout .`, `git restore <path>` and its `--worktree`
  forms on a file changed in the worktree with nothing staged, or on a variable path
  (`# discard-ok: <why>` overrides); the kill guard refuses a `kill` of a PID whose working
  directory lies in another session's worktree, read from the process on Linux and macOS
  (`# pid-ok: <where it was recorded>` overrides). The suite builds a scratch repository or
  starts a sleeping process for an event that asks for one. `skills/land/SKILL.md` step 11 and
  `skills/execute/SKILL.md` say a background command from a worktree begins with that `cd`, and
  the execute skill names mutating on an export as the restore-free alternative.
  **Consumer contract:** the old form is the prose; the new form is two more hooks and one more
  refusal a plugin install runs on every `Bash` and `PowerShell` call; the migration is none
  beyond rewording a refused command as its reason says.

- `hooks/guard-delete.py` and `hooks/guard-stash-kill.py`, registered in `hooks/hooks.json`, with
  recorded events under `tests/hooks/`: the delete guard refuses a recursive delete by pattern,
  substitution, pipeline or a variable it cannot see assigned, one aimed at a root, a home
  directory, `~/.claude`, the checkout or a worktree, one with its errors silenced, an assignment
  to a PowerShell variable the runtime reserves, and a forced worktree removal or a `git clean` of
  untracked work outside scratch (`# delete-ok: <why>` overrides those two); the stash-and-kill
  guard refuses every `git stash` but `push -m`, `apply <sha>`, `list` and `show`, and a process
  stopped by a pattern, a listing, a name or a group. The sentences each guard enforces leave
  `skills/execute/references/dispatch-rules.md` (its Scratch and Stash sections, and the Shell
  section's delete, kill and variable rules) and the briefs that pasted them; `hooks/README.md`
  keeps each rule beside its guard. **Consumer contract:** the old form is those rules as pasted
  prose; the new form is two hooks a plugin install runs on every `Bash` and `PowerShell` call,
  refusing the commands above with the reason on stderr; the migration is none beyond rewording a
  refused command as its reason says.

- `hooks/hooks.json`, named by `.claude-plugin/plugin.json`: the plugin registers its Claude Code
  `PreToolUse` guards itself, the forbidden-tokens hook (`bin/forbidden-tokens.py hook`) first,
  with `hooks/README.md` for what each refuses and how a guard fails, and
  `tests/hooks/Test-Hooks.Tests.ps1`, which runs every registered hook over the recorded events
  under `tests/hooks/<hook>/`; `bin/Test-ChangelogEntry.ps1` counts the registry as a surface
  that takes an entry. **Consumer contract:** the old form is a hook entry the owner writes into
  `settings.json`, pointing at the marketplace clone; the new form is the plugin's own
  registration, which a plugin install loads with no settings edit, so the forbidden-tokens hook
  runs on every `Bash` and `PowerShell` call of a session with the plugin enabled and refuses a
  publishing call on a machine with no `OURO_FORBIDDEN_TOKENS` list; the migration is to delete
  the `settings.json` entry, so the hook runs once, and to set the variable on every machine that
  runs the plugin.

## [v0.9.0] - 2026-10-12

- `bin/Watch-PullRequestChecks.ps1`, `bin/Merge-PullRequest.ps1` and `bin/Remove-LandedBranch.ps1`,
  with `bin/Get-LandBinding.ps1` they dot-source and a fixture suite each under `tests/land-scripts/`:
  the deterministic steps of `/ouro:land` — watching the head's checks (step 7, and the batch's
  default-branch runs for `/ouro:land-batch`), the guarded merge (step 10) and the branch and
  worktree cleanup (step 11) — run as scripts that read the binding, and the skill names each one's
  invocation where it held the bash block a session copied, filled and ran. **Consumer contract:**
  the old form is three bash blocks in `skills/land/SKILL.md` the session runs one call each; the
  new form is `pwsh <ouro>/bin/Watch-PullRequestChecks.ps1 -Pr <n>` (or `-Sha <sha>...`),
  `pwsh <ouro>/bin/Merge-PullRequest.ps1 -Pr <n> -Subject <s> -BodyFile <f> -Linear yes|no
  [-Merge squash|merge]` and `pwsh <ouro>/bin/Remove-LandedBranch.ps1 -Pr <n> [-Branch <b>]`; the
  migration is none for a plugin install, since the skill names them, and a vendored tree re-runs
  `bin/Vendor-Ouro.ps1` to receive the four scripts and the suites.

- `bin/Test-LandState.ps1`, `bin/Sync-Branch.ps1` and `bin/Push-Branch.ps1`, with a suite each
  under `tests/land-scripts/`: `/ouro:land` steps 0 to 4 — the binding read and its refusals, the
  state and the commits to land, the sync by the binding's answer with the backup and the lease,
  the plain or leased push — run as scripts, and the skill names each one's invocation where it
  held six bash blocks. `bin/Get-LandBinding.ps1` gains the worktree readers and the
  checked-out-branch read. **Consumer contract:** the old form is those six bash blocks in
  `skills/land/SKILL.md`, filled and run one call each; the new form is
  `pwsh <ouro>/bin/Test-LandState.ps1`, `pwsh <ouro>/bin/Sync-Branch.ps1 [-Sync merge|rebase]`
  and `pwsh <ouro>/bin/Push-Branch.ps1 [-Lease <sha>|none]`; the migration is none for a plugin
  install, and a vendored tree re-runs `bin/Vendor-Ouro.ps1` to receive the scripts and the suites.

- `bin/Invoke-LandBatch.ps1`, with its suite under `tests/land-scripts/`: the whole of
  `/ouro:land-batch`'s deterministic work as subcommands over the batch files in the git directory
  — `check`, `worktree`, `start`, `add`, `stacked`, `record`, `amend`, `route`, `land`, `report`,
  `reset`, `push`, `close`, `watch` — and the skill keeps the order, the judgment and the sharp
  edges in prose, with no bash block. **Consumer contract:** the old form is seventeen bash blocks
  in `skills/land-batch/SKILL.md`, filled and run one call each; the new form is
  `pwsh <ouro>/bin/Invoke-LandBatch.ps1 <subcommand> [-Pr <n>] [-Branch <b>] [-Message <file>]
  [-Note <text>] [-Sha <sha>]`; the migration is none for a plugin install, and a vendored tree
  re-runs `bin/Vendor-Ouro.ps1` to receive the script and the suite.

- `bin/Compress-Branch.ps1`, with its suite under `tests/land-scripts/`: `/ouro:land` step 10's
  compression of a fix-up range before an explicit merge, as `prepare` (the shared-branch signs,
  the single-author check, the backup branch, the lease, the base and the author printed) and
  `push -Backup <branch>` (the tree-identity and author checks, then the leased push), around the
  session's own composing of the arc; the skill names the two invocations where it held its last
  two bash blocks of step 10. **Consumer contract:** the old form is two bash blocks in
  `skills/land/SKILL.md`, filled and run one call each; the new form is
  `pwsh <ouro>/bin/Compress-Branch.ps1 prepare` and
  `pwsh <ouro>/bin/Compress-Branch.ps1 push -Backup <branch>`; the migration is none for a plugin
  install, and a vendored tree re-runs `bin/Vendor-Ouro.ps1` to receive the script and the suite.

## [v0.8.0] - 2026-10-12

- `record-counts.py` and its suite are gone from `bin/` and `tests/`, with their entries in the
  binding, the No binding workflow's path list and `bin/Vendor-Ouro.ps1`'s copy plan: the review
  skill no longer traces the counts in a PR record, since the record is read once at verification
  and its counts are never re-taken. **Consumer contract:** the old form is that the vendor script
  copies `record-counts.py` and its suite into a vendored tree; the new form copies neither; the
  migration is to delete `<ScriptsDir>/record-counts.py` and `<ScriptsDir>/tests/record-counts/`
  from a vendored tree, which nothing there runs.

- `skills/review`, `docs/contract.md` §4: review weight follows the class of change and its size,
  where it followed the class alone. An executed diff of twenty changed text lines or fewer, by
  the reviewer prompt's count, takes the new sized review, `/ouro:review --sized <range>`: probes
  at the changed hunks only, at most two attack vectors in place of the four defaults, and one
  `PROBED` line per hunk in place of the REJECTED ledger. The probe format stands above twenty
  lines and light mode stands for a documentation-only diff. **Consumer contract:** the old form
  is two weights, light and probe, chosen by the class of change alone; the new form adds
  `--sized` for a small executed diff; the migration is none, since `/ouro:execute` and
  `/ouro:land` pick the weight.

## [v0.7.0] - 2026-10-12

- `actions/gates/action.yml`, this repository's own gates workflow, `.github/workflows/no-binding.yml`,
  `.claude/ouro.toml`: with `select-by-paths` on, a push selects its gates by the diff against the
  tip the branch had before it, as a pull request does against its base, and the
  `.claude/ouro.toml` fallback is gone: a gate that reads the binding names it in its `paths`.
  Every gate runs on a push with no previous tip (a new branch, a tag), on any other event, when
  the diff cannot be read, and when the diff touches a workflow or the Action. This repository's
  gates workflow runs on `v*` tags and once a week, its full-set runs; every gate in its binding
  declares `paths` but the two whole-tree checks; and its No binding workflow no longer runs on a
  pull request. **Consumer contract:** the old form is that a push runs every gate whatever the
  input; the new form is that a push with the input on selects by its own diff, and a push with
  no previous tip runs every gate; the migration is none, since the input is off by default, and
  a consumer that turned it on gets the narrower push run.

## [v0.6.0] - 2026-10-11

- `skills/land`, `skills/land-batch`, `bin/Test-CommandBlocks.ps1`: invoking either skill with
  arguments no longer rewrites the awk field references in its bash blocks (Claude Code replaces
  a dollar-digit in a skill body with the invocation's arguments, so `$3` could render as an
  argument word); the six awk programs now reach their fields through `-v` variables with the same
  output, and the command-blocks gate gains a `skill-argument` rule that reports a dollar-digit
  or `$ARGUMENTS` on any command-block line.

- `bin/apply-manifest.py`, `docs/binding.md`, `bin/ouro-binding.py`: an absent
  `ship.forbidden_check` now follows the visibility of the repository the run posts to. With no
  private token list set, the applier reads that visibility with one `gh repo view` call, in a dry
  run too; a `PRIVATE` or `INTERNAL` repository skips the check with the line
  `forbidden-token check skipped: no list, private repository`, and `PUBLIC`, a failed or
  timed-out read, or any other value refuses as a missing list does. A list that is set,
  `--no-forbidden-check`, and a `"required"` or `"off"` key in a binding naming the repository
  the run posts to make no read. **Consumer contract:** the old
  form is an absent `ship.forbidden_check` meaning `"required"`, so a missing list refuses on
  every repository; the new form is that an absent key follows the target's visibility, so a
  private or internal repository with no list skips with one line; the migration is one line: a
  private repository that wants the old refusal declares `forbidden_check = "required"` under
  `[ship]`, which holds for runs from its own checkout.

- `skills/init/SKILL.md`: the `description` names the CI workflow among what a re-run adds,
  beside the docs-freshness workflow and the issue intake files, as the skill's step 1 and the
  README row already do.

- `bin/Test-DocsFreshness.ps1`: the docs sweep's rolling-issue sentence lists S9 as report-only
  whatever `[docs].report_only` holds, since only Sweep mode computes it. A comment in
  `templates/weekly-pass.yml` no longer describes that sentence as emitted signals minus the
  report-only list.

- `bin/Vendor-Ouro.ps1`: the help names `loop-outcomes.py` and `ouro-version.py` as not copied,
  each with its reason, and the closing list's workflows line exempts the weekly pass's
  `loop-outcomes.py` step, which keeps its plugin checkout.

- `bin/external-audit.py`: with no git on PATH, claims mode skips its working-tree check with one
  stderr line and runs to its verdict, and diff mode exits 1 naming the missing git; the usage
  text's exit-1 list names that case.

## [v0.5.2] - 2026-10-11

- `templates/ouro.toml.example`: the `suppress_prefixes` advice gains `bin/external-audit.py`,
  `bin/apply-manifest.py`, `bin/forbidden-tokens.py` and `bin/loop-outcomes.py`, which the
  vendored binding and contract docs cite; `bin/Vendor-Ouro.ps1` prints the same list. The
  `[models]` and `[docs]` notes say `[owner]` is required, and the `report_only` note says S9
  never blocks. The `bin/ouro-binding.py` usage text notes the `warning:` line `check` may print.

- `bin/Test-DocsFreshness.ps1`: the help says S9 never blocks whatever `-ReportOnlySignals` holds,
  and describes the repo-root `.docs-freshness-ignore` file. The docs-gate and drift-process docs
  match the code, and the steps for consuming a drift report now live in the drift skill.

- `bin/Get-DriftAuditTargets.ps1`, `bin/New-OuroLabels.ps1`, `bin/Test-IssueLabelInvariants.ps1`,
  `bin/Test-RepoVariablesDoc.ps1`, `bin/drift-claims.py`: help only, no behaviour change.
  `Get-DriftAuditTargets.ps1` documents `-MaxTargets`, `-RotationFloor`, `-ExcludePrefix` and
  `-OutFile`; the two gates document `-RollingIssueTitle`; the `drift-claims.py` usage line for
  `outbox` names `--targets <file>`, which it requires. `New-OuroLabels.ps1` documents
  `-BindingPath`.

- `templates/weekly-pass.yml`: comments only, no step changes. They now say that the pass files
  the docs_freshness, drift_audit and loop_runs issues the repo lacks, that the six gate steps
  share the loud-failure posture, that `/ouro:compile` reads the blocked trigger line too, and
  that the blocked-trigger check stays silent only when the newest notice on the issue from the
  bot or a ruling approver says the same.

## [v0.5.1] - 2026-10-10

- `bin/loop-outcomes.py`: the `**Checkpoint finding**`, `**Stop:**` and `**Triage**` markers
  count only from the job token's bot or a `[owner].ruling_approvers` login, compared without
  regard to case, as the gates count them; a stop or finding from any other author is ignored:
  it names no reason and delivers no finding. The Assisted approver compare ignores case too.

- `docs/contract.md`: section 7's working-discipline block gains a bullet. A session that
  changes state other sessions share (a host service, a CI runner's configuration, the container
  runtime, the harness's settings, hooks or rules) proves the change with the thing that uses it,
  such as a job on that runner, not with the symptom it set out to fix.

- `/ouro:execute`: a `**Checkpoint finding**` comment stops a queued checkpoint run only from a
  trusted author, and it is measured against the newest trusted `**Triage**` comment, so a
  stranger's comment neither stops the run nor makes a delivered finding read as stale. A
  finding posted by a login outside the trusted set repeats the checkpoint run; the
  `[owner].ruling_approvers` comment in `docs/binding.md` now says to list every login that
  runs triage, execute or fuse.

## [v0.5.0] - 2026-10-10

- `templates/weekly-pass.yml`: the four native command lines that took a relative path spelled
  with a backslash now spell it with forward slashes, so the apply step works on a Linux runner,
  where a native program receives the string verbatim and no file has that name. The weekly-pass
  suite gains a finding for a run line whose first word is `python3`, `pwsh`, `claude`, `gh` or
  `git` and that holds a path with a backslash. **Consumer contract:** the old form is a copied
  weekly pass whose apply step reads `python3 ouro/bin/apply-manifest.py
  TestResults\intake-manifest ... --targets TestResults\intake-targets.json`, which finds neither
  path on a Linux runner; the new form spells both with `/`; the migration is to re-copy four
  lines: the apply step's `python3` line, the intake prompt's `--targets` and `--manifest`, the
  drift prompt's `--targets`, `--ledger` and `--outbox`, and the target selection's `-OutFile`.

## [v0.4.0] - 2026-10-09

- `bin/apply-manifest.py`, `templates/weekly-pass.yml`, `skills/intake/SKILL.md`,
  `docs/contract.md`: the unattended applier posts only the intake's own verdict, and only on the
  run's targets. Under `--unattended` it requires `--targets <file>`, the JSON the intake's
  selector wrote, and refuses a step whose issue is not the `number` of one of that file's
  `targets` rows (a file it cannot read, or one with no `targets` array, refuses too); it refuses
  a comment whose first line, as posted, is not `**Intake triage** (automated)`, so a manifest
  steered by a planted issue body cannot post under the job token's bot on an issue outside the
  run's targets file or open with `**Triage**`, `**Checkpoint finding**` or the blocked check's
  marker; and it refuses `--targets` outside `--unattended`. The marker line is compared with only
  spaces, tabs and CRs trimmed, characters the selector's trim also drops, so a comment the applier admits is one the selector counts. The "Compute intake targets" step records
  the targets file's SHA-256 in `INTAKE_TARGETS_SHA256`, and the apply step throws before the
  applier runs when the file's hash differs. The weekly-pass suite checks that the apply step
  passes the file the grading step was handed and compares the hash. **Consumer contract:** the old form is the apply step
  `python3 ouro/bin/apply-manifest.py TestResults\intake-manifest --unattended --repo $slug
  --no-forbidden-check`, which the applier now refuses; the new form is that line with `--targets
  TestResults\intake-targets.json` added; the migration is to add `--targets
  TestResults\intake-targets.json` to the copied apply step, and to copy the template's hash
  line in "Compute intake targets" and the hash comparison before the applier runs.

- `skills/execute/SKILL.md`, `skills/fuse/SKILL.md`, `bin/Test-AgentReadyShape.ps1`,
  `docs/contract.md`, `docs/binding.md`: the spec
  `/ouro:execute` builds is the body as the newest trusted `**Triage**` comment left it, and only a
  trusted author (the job token's bot or a login in `[owner].ruling_approvers`) amends it by
  comment. The entry ritual reads the issue's comments with their authors and the body's edit
  history, and stops with `**Stop:** open decision`, swapping the issue to `needs-ruling`, when an
  untrusted editor changed the body after that comment. The shape gate counts a `**Triage**`
  comment as provenance only from a trusted author, through `Get-TrustedComments`; a repo with no
  `.claude/ouro.toml`, or a vendored copy without `ouro-binding.py`, skips the author check with
  an INFO line and counts any author's comment as before. The gate takes a new optional
  `-Approvers` parameter (for tests) and dot-sources `Get-RollingIssue.ps1`, which a vendored copy
  carries beside it. `/ouro:fuse` reads each issue under the same two rules and leaves out one that
  trips the stop. A shape finding for a `**Triage**` comment by an author who does not count names
  that author. Migration: list every login that runs `/ouro:triage` in `[owner].ruling_approvers`;
  a `**Triage**` comment by anyone else no longer counts, so the sweep comments on that promotion,
  `-Demote` demotes it and execute stops.

- `bin/Get-IntakeTargets.ps1`, `bin/Test-BlockedTriggers.ps1`, `bin/Get-RollingIssue.ps1`,
  `docs/contract.md`, `skills/intake/SKILL.md`: the intake's selector and the blocked check count a
  marker comment only when its author is the job token's bot or a login in
  `[owner].ruling_approvers`, so a stranger can no longer hide a new issue from the unattended
  intake or pre-empt the notice that a blocked issue's trigger has fired. Both read the comments'
  authors through the new `Get-TrustedMarkerBodies`, which uses `Get-TrustedComments`; a repo with
  no `.claude/ouro.toml` trusts only the bot there and prints an INFO line, and a binding that is
  there and cannot be read fails the gate. A vendored copy with no `ouro-binding.py` beside the
  library does the same bot-only read with its own INFO line. A vendored copy needs
  `Get-RollingIssue.ps1` beside the selector and the blocked gate, so a consumer re-vendors.

## [v0.3.0] - 2026-10-08

- `templates/weekly-pass.yml`, `skills/drift/SKILL.md`, `agents/verifier.md`, `docs/contract.md`,
  `docs/rationale.md`: the weekly pass's drift audit reads text anyone with comment rights can
  write, so its model session now holds the intake's posture: no token, no `gh` tool, no git
  command that runs a program or writes a file, and a step with no model in it posts what the
  session wrote. **Consumer contract:** the old form is a drift step that inherits the job token,
  is granted `Bash(gh issue list *)`, `Bash(gh issue view *)`, `Bash(gh issue comment *)`,
  `Bash(gh issue edit *)`, `Bash(git log:*)`, `Bash(git diff:*)`, `Bash(git show:*)` and
  `Bash(git grep:*)`, and is prompted `--issue $env:DRIFT_ISSUE_NUMBER --ci`; the new form is a
  drift step with `env: GH_TOKEN: ''` that first runs `New-Item -ItemType Directory
  TestResults\drift-outbox | Out-Null` and is prompted `/ouro:drift --targets
  TestResults\drift-targets.json --ledger TestResults\audit-ledger.txt --outbox
  TestResults\drift-outbox --ci` with `--allowedTools
  "Skill,Read,Grep,Glob,Agent,Task,Edit(TestResults/drift-outbox/**),Bash(git ls-files:*),Bash(git cat-file:*),Bash(git rev-parse:*)"`;
  a new step, `Post the drift audit's output (deterministic)`, between the drift step and the
  claims step, which runs the tree check, `python3 ouro/bin/drift-claims.py outbox --dir
  TestResults/drift-outbox --targets TestResults/drift-targets.json` and then `gh issue edit` and `gh issue comment` on
  `$env:DRIFT_ISSUE_NUMBER` with `--body-file`; and `TestResults/drift-outbox/**` in the artifact
  upload's `path`. Migration in a copied workflow: replace the drift step with the template's, add
  the posting step after it and the upload path. The skill refuses a CI run without `--ledger`
  and `--outbox`. A CI verifier holds no
  history command and no `gh`, so a claim only history or live state could settle is reported
  AMBIGUOUS.
- `bin/drift-claims.py`, `bin/Get-DriftAuditTargets.ps1`: `drift-claims.py` gains `outbox --dir
  <dir> --targets <file>`, which prints the ledger body file and then the comment files of a drift
  session's output directory, or refuses the whole directory and prints nothing; it refuses an
  `audit-run` marker on any comment but the last and a `docs=` entry that is not the `path` of a
  `targets[]` entry of the targets file, and it reads the comment cap and the secret shapes from
  the `apply-manifest.py` beside it. `Get-DriftAuditTargets.ps1` gains
  `-MaxCommits` (default 50), and each target gains `lastCommit`, `commits` and `commitsOmitted`,
  the history step 3 of `/ouro:drift` reads. **Consumer contract:** the old form is a targets file
  with no history and a drift script with no `outbox`; the new form carries both. Migration: a
  consumer that vendors the scripts re-vendors them, both files together; one that reads the
  plugin checkout needs no edit.
- `templates/weekly-pass.yml`, `bin/Get-RollingIssue.ps1`, `skills/drift/SKILL.md`,
  `agents/verifier.md`, `docs/contract.md`: the weekly pass counts a comment on the drift ledger
  only when its author is the job token's bot or a login in `[owner].ruling_approvers`, so a
  stranger's `audit-run` or `audit-claims` marker no longer removes a doc from the next run's
  targets or chooses which docs' claim records are kept; the job names its token permissions; and
  the drift skill and the verifier agent state that what they read is data; the gates that quote an
  issue title or a variable's value into a comment on the ledger write every `<` as `&lt;`, so a
  gate's comment carries no marker. `Get-RollingIssue.ps1` gains `Get-TrustedComments` and
  `ConvertTo-InertCommentText`. **Consumer contract:** the old form is a harvest step that writes
  `gh issue view $num --comments --json comments --jq '.comments[].body'` to
  `TestResults\audit-ledger.txt`, a claims step that writes `gh issue view
  $env:DRIFT_ISSUE_NUMBER --comments --json comments` to `TestResults/drift-comments.json`, and a
  job with no `permissions:` block; the new form is a harvest step that reads the comments into a
  variable, runs `$ledger = Get-TrustedComments -CommentsJson $raw` and writes `$ledger.Bodies` to
  the ledger file, printing how many it kept and dropped and raising a `::warning::` when the
  ledger has comments and none is kept; a claims step that runs `$trusted = Get-TrustedComments
  -CommentsJson $raw` and writes `$trusted.Json` to the comments file; and a job `permissions:`
  block of `contents: read`, `issues: write`, `pull-requests: read` and `actions: read`. Migration
  in a copied workflow: replace the harvest step and the claims step with the template's, and add
  the block under the job's `timeout-minutes`. A consumer that vendors the scripts re-vendors
  `Get-RollingIssue.ps1`. Two things are unmeasured and settle on the first run of a copy that
  carries the change: the bot's login is `github-actions` as `gh --json comments` spells it, and a
  wrong login shows as the harvest's `::warning::` and a ledger that reads empty; and the
  repo-variable cross-check runs `gh variable list`, which no `permissions:` key names, so compare
  that step's output on that run with the run before it.
- `templates/weekly-pass.yml`, `bin/drift-claims.py`, `docs/contract.md`, `docs/rationale.md`: both
  model sessions load the plugin from the workflow's own `ouro/` checkout and cannot read a file
  outside the workspace with a file tool, where the runner's credentials and `gh` hosts file sit;
  of the git reads their Bash holds, `rev-parse --resolve-git-dir` prints the target line of an
  outside file in gitfile form and `ls-files -X` tests a whole-line guess against one; the intake's Edit
  names its manifest directory and no longer the whole results directory; and the usage step posts
  numbers only. **Consumer contract:** the old form is a drift step and an intake step whose
  `claude -p` carries neither `--plugin-dir` nor `--settings`, so the session loads whatever plugin
  the runner has installed and reads any file the runner user can; an intake granted
  `Edit(TestResults/**)`; a posting step that runs `drift-claims.py outbox --dir
  TestResults/drift-outbox`; and a usage step that posts `ConvertTo-Json` of the result's `usage`.
  The new form is, in each of the two steps, `$confine = Join-Path $env:RUNNER_TEMP
  'ouro-read-confined.json'` and `Set-Content -LiteralPath $confine -Value
  '{"permissions":{"blockReadsOutsideWorkingDirectories":true}}' -Encoding utf8` before the
  `claude -p` call, which gains `--plugin-dir ouro` and `--settings $confine`; the intake grant
  `Edit(TestResults/intake-manifest/**)`; the posting step's `--targets TestResults/drift-targets.json`;
  and the usage step's casts, which post four token counts, the turn count and the duration.
  Migration in a copied workflow: copy those lines from the template, and re-vendor
  `drift-claims.py` and `Get-RollingIssue.ps1` together with the gates that post to the ledger.
  `--plugin-dir ouro` pins the sessions' skills to `OURO_REF`; a runner that relied on its own
  installed plugin loses that.

## [v0.2.0] - 2026-10-08

- `actions/gates/action.yml`: the step's comment no longer names the workflow of the repository that
  develops this plugin. No input, output or behavior changes.
- `templates/weekly-pass.yml`, `templates/documentation-rule.md`: a comment and an example no longer
  carry an issue number. No step, key or rule changes; an existing copy needs no edit.
- `README.md`, `templates/weekly-pass.yml`, `templates/docs-freshness.yml`, `templates/ci.yml`: the
  plugin is installed from the public repository `iteramen/ouro`, which takes issues, not pull
  requests. **Consumer contract:** the old form is the weekly-pass plugin checkout of
  `BoJl4apa/ouro` with `token: ${{ secrets.OURO_READ_TOKEN }}`, and the `uses:` pins of
  `BoJl4apa/ouro/actions/docs-freshness@<tag>` and `BoJl4apa/ouro/actions/gates@<tag>`; the new form
  is `iteramen/ouro` for the checkout's `repository:` and for both `uses:`, with no `token:` input.
  Migration in a copied workflow: re-point `repository:` and every `uses:`, and delete the `token:`
  line before the `OURO_READ_TOKEN` secret, because actions/checkout reads `token` as a required
  input and an emptied secret fails the checkout.
  The public repository's tags start at v0.2.0, so `OURO_REF` and each `@<tag>` pin move to v0.2.0 or
  later: no v0.1.x tag exists on `iteramen/ouro`, and a pin left at one fails the checkout and the `uses:`.
