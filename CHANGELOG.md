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
