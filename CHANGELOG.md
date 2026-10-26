# Changelog

What each release of this plugin changed for a repository that installs it, newest first.
Consumers pin the plugin by tag, so a release is the unit a person or a resolver reads before
moving a pin. One bullet per landing; an entry names no issue or pull request number; the
`chore(release)` bumps themselves are not entries.

This file keeps the minor series being prepared under UNRELEASED and the two preceding
minor series. Older published sections, including their **Consumer contract:** migration
notes, remain verbatim in dated root archives:
[v0.2 through v1.3](CHANGELOG-0.2-through-1.3.md). An archive is frozen history; the linked
current documentation defines the protocol.

A `**Consumer contract:**` line marks an entry that moves something a consumer's own files
name: a skill's invocation flags or its credential and tool requirements, a table or key of the
binding schema (or a value the binding check newly refuses), a script's parameters or a python
tool's usage lines, the shape of a template a consumer copied, or an Action's own definition file
a consumer's workflow uses at a pinned tag. The line names the old form, the new form and the
one-line migration. A release carrying any such entry takes a minor version bump; every other
release takes a patch.

## [UNRELEASED]

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

## [v1.4.1] - 2026-10-20

- `docs/architecture.html` names this repository's own rules file instead of linking to it: the
  public tree does not hold that file, so the link was dead there.

## [v1.4.0] - 2026-10-20

- `bin/forbidden-tokens.py hook` reads a `# protects: <owner/repo> ...` line in the token list:
  with one, a hit blocks only a call that lands in a named repository (the directory's
  `repo.slug`, else its `origin`; one it cannot resolve counts as named) or names one with a `-R`,
  `--repo` or `GH_REPO` value or an issue or pull request URL, and `ship.forbidden_check` stays the
  override, `"required"` scanning and `"off"` passing whatever the line names; with the line,
  `"off"` passes a hit only when no such name is another repository's. **Consumer contract:** old form, with a list set an absent
  `ship.forbidden_check` scans every publishing call in every repository; new form, it follows the
  list's `# protects:` line, and a list without one scans every call as before. Migration: add
  the line to the list naming the repositories it guards, then drop the `forbidden_check = "off"`
  a binding carried only to opt out.
- `bin/forbidden-tokens.py` judges a commit at the directory a `cd` behind an escaped inner quote,
  a `cmd /c`, `eval`, `iex` or `Invoke-Expression` string, an unquoted `-c` word, or an `env -C`
  before a `bash -c` moves to, and refuses a `cd` whose directory reads empty (spliced quotes) or
  is cut away (a command substitution, a parenthesised expression) instead of skipping it.
- A follow-up write that fails after an earlier one succeeded leaves the unfiled items in the
  `**Follow-ups filed**` comment, and a resumed run files exactly those, once each, then posts
  the comment with everything filed. Only a comment from the bot or an approver counts, both for
  the items to file and for ending the step; another account's comment is data.
- The `Review:` line no longer counts a verification's OUTSIDE finding, as the contract's
  Review-line rule says; the FOLLOWUP brief still carries it.
- `bin/forbidden-tokens.py` reads `chdir`, pwsh's alias of `Set-Location`, as a cd verb wherever
  `cd` is one; its selftest pins `chdir`, `env --chdir=` and a `--chdir` a `-C` chains from.
- `bin/external-audit.py` creates its artifact tree (the per-user parent, the session directory
  and the round folder) owner-only for every adapter, on POSIX re-setting each level to 0700 and
  refusing one another account owns, before anything is written; `prompt.txt`, which holds the
  diff, is written 0600.
- The FOLLOWUP brief lists an OUTSIDE finding once when a later round reports it again with other
  evidence or another severity, as the latest round reported it: the identity is the file, the
  line and the claim, the one the `Review:` line counts by, less the severity.
