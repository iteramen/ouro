# Changelog

What each release of this plugin changed for a repository that installs it, newest first.
Consumers pin the plugin by tag, so a release is the unit a person or a resolver reads before
moving a pin. One bullet per landing; an entry names no issue or pull request number; the
`chore(release)` bumps themselves are not entries.

A `**Consumer contract:**` line marks an entry that moves something a consumer's own files
name: a skill's invocation flags or its credential and tool requirements, a table or key of the
binding schema (or a value the binding check newly refuses), a script's parameters or a python
tool's usage lines, the shape of a template a consumer copied, or an Action's own definition file
a consumer's workflow uses at a pinned tag. The line names the old form, the new form and the
one-line migration. A release carrying any such entry takes a minor version bump; every other
release takes a patch.

## [UNRELEASED]

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
