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

## [v1.12.0] - 2026-11-24

- **Consumer contract:** `ship.review_sandbox = true` admits a Qwen Code reviewer on Linux with
  `/usr/bin/bwrap` again: the opt-in `python3 tests/runtime/live.py --qwen-sandbox` qualification
  passes now that the review copy's git directory is outside the copy. It was refused, naming its
  route as not qualified. Migration: none.

- The Claude preflight refuses a Claude Code whose `--help` lists no `--effort` when a role it
  serves sets an effort, so a CLI too old for the flag fails before any write instead of at the
  first node. The usage table adds an `effort` column, `unknown` where the host's own settings
  decided it. The coordination-unavailable notice prints once per distinct cause. The BUILD and
  REVIEW briefs' anchor block reads the lines the anchor gate checks: list items under an
  `Anchors` heading, fragments of 12 characters or more. A ledger split reads the open issues
  updated since its newest sweep mark. `docs/guide.md` states the `gh` 2.46 floor.

- The developer binding takes `runtime.advisor` and `runtime.roles.<role>.advisor`: `up`, one tier
  above the node's model in the order `haiku`, `sonnet`, `opus`, `fable` (none above `fable`, and
  the run says so), or a Claude model alias, passed as written. The Claude backend passes it as
  `advisorModel` in the node's single `--settings` argument; Codex and Qwen Code refuse the key.
  Absent, the host's settings decide. The runtime attempt diagnostics add `advisor` and
  `second_model` columns.

- `bin/loop-outcomes.py` counts a publication through a direct PR vehicle in the direct
  publications table: each merged pull request on a `direct/` head once, in the week of its merge,
  naming an issue on `direct/<Issue>` and issue-less on `direct/<12 hex>`. A rebase-merged
  vehicle's commits, matched by subject and author date, are no longer counted as plain pushes.
  The pull-request fetch reads `headRefName` and each vehicle's commits; a `--from-cache`
  `prs.json` written without them is a failed read: fetch it again.

- `bin/Get-FootprintGraph.ps1` writes an `anchorOnly` list per issue: the footprint files its body
  names only inside an Anchors section. `bin/Get-CompileProgram.ps1` marks such a file on a batch's
  `shared:` line, `(only an anchor in #N)`, and keeps the link. Its finish intent reads a
  blocked child's trigger by the unblock reference rule, so a `#N` right after `.` or `-` no
  longer reads as fired.

- A Qwen Code PROBE runs a glob or brace that cannot name git: `ls *.py`, `grep -E '[a-z]+' f` and
  `echo {a,b}` are admitted. The hook still refuses any `$`, and a glob or brace word whose
  basename, its brace sets expanded, can match `git`, `gh` or a `git-<sub>` name, `.exe` or not, and
  a word with a quote or a backslash after an unquoted `[` or anywhere beside an unquoted `{`
  (`git {push,"-o}"}`), and any whitespace but space and tab,
  which bash keeps inside a word (`g[<VT>i]t push`); it refuses a
  `GIT_CONFIG` word only in a command that names git, and reads the words a brace word expands to
  for push and commit.

- **Consumer contract:** `ship.review_sandbox = true` refuses a Qwen Code reviewer on Linux, before
  any tracker call, naming its route as not qualified: the opt-in
  `python3 tests/runtime/live.py --qwen-sandbox` qualification fails, because Qwen Code's
  `workspace-write` sandbox lets the node write into the review copy's `.git`. It was admitted
  wherever `/usr/bin/bwrap` exists. The qualification itself still runs. Migration: select another
  reviewer backend in `[runtime.roles.reviewer]` (Claude or Codex), or unset the key.

- **Consumer contract:** `bin/Push-Direct.ps1` publishes with no issue. Under a pull-request
  requirement without `-Issue` it pushes its vehicle branch as `direct/<12-character head sha>`
  instead of stopping with exit 1; with `-Issue <N>` the branch stays `direct/<N>`. The person who
  ran an issue-less publication watches default-branch CI after it; the driver's lane, which always
  passes `-Issue`, is unchanged. Migration: none for the driver; a caller that relied on the stop to
  require an issue must check for one itself.

- `bin/loop-outcomes.py` prints a direct-publications table after the quality table: per ISO week,
  each first-parent default-branch commit whose subject ends in no `(#PR)` and that is no pull
  request's merge commit, split into those with a `Fixes` line and the issue-less rest.

- The driver checks that a bug fix's test rows fail on the code before the fix. On a run whose
  issue carries the `bug` label, with `ship.test_first_for_bugs = true`, it puts every changed
  file outside `ship.test_paths` back to its `origin/<default>` version in a scratch copy of the
  head and runs each selected gate whose `paths` hold a changed test file there, the checks' own
  gates included. One must go red, and the record names it; all green is a fix-round finding,
  and in the direct lane a fallback to the ordinary pull-request flow, whose fix round answers it. A
  diff that changes no test file, or no file outside the test paths, skips the check, and the
  record says which.
  **Consumer contract:** `ship.test_first_for_bugs` and `ship.test_paths` were read by nothing;
  the driver now reads both. Migration: a binding that sets `test_first_for_bugs = true` checks
  that its `test_paths` name the test files its gates' `paths` hold, or drops the key.

- A Claude or Codex PROBE review node launches with a home of its own: `HOME`, `USERPROFILE` and
  `XDG_CONFIG_HOME` name an empty directory private to the launch, so a plain `git push` that keeps
  every credential override no longer sends a matching `~/.netrc` entry, and `~/.git-credentials`,
  `~/.config/gh` and `~/.gitconfig` resolve to nothing there. A raw `ssh` ignores `HOME` and still
  reads the account's `~/.ssh` from the password database. The harness's own state
  is found past it. A declared `CLAUDE_CONFIG_DIR` is passed on; with none, the Claude launch
  leaves it unset, because a set one moves Claude Code's global `.claude.json` into `~/.claude`,
  and gives the home a link to `~/.claude` (a directory junction on Windows) and a copy of
  `~/.claude.json`. `CODEX_HOME` is the declared value or `~/.codex`, which the Codex MCP bridge now
  always receives. Whether the harness login survives it is unproved on macOS and Windows.

- Under `ship.review_sandbox = true`, a Qwen Code reviewer's preflight launches its PROBE live
  call under Qwen Code's tool execution sandbox, with the settings the review node uses, and
  accepts it only when the transcript shows the listed command ran. A host where the sandboxed
  route cannot run a granted command now fails preflight, before any tracker write, not at REVIEW.

- On Windows, the Qwen Code allow-list hook's command is built for the shell Qwen Code runs it
  under: POSIX-quoted under Git Bash, and unquoted under cmd.exe, which Qwen Code's hook launch
  leaves no quoting for. The Qwen Code worker and the `--cli qwen` reader refuse before launch,
  naming the path, when the Python interpreter, the plugin or the temporary directory holds
  whitespace, a double quote or one of `&|<>^%`, and when `ComSpec` names PowerShell.

- `bin/Test-DocsFreshness.ps1` matches a `file:` ignore entry on whole path segments: `file:
  README.md` exempts `docs/README.md` and not `xREADME.md`. A bare token keeps its suffix match.
  **Consumer contract:** an entry that named only the tail of a file name resolved to that doc;
  it now stops the run as resolving to no in-scope doc. Migration: write the entry as the doc's
  path, or as its trailing path segments.

- Each developer picks their own reviewer in a `[review]` table of the developer binding:
  `reviewer` (`adversarial-review`, `external-audit` or `copilot`), `external_cli`,
  `external_model` and `external_effort`, each with the values of the `ship.*` key it stands for.
  A key left unset keeps the repository's value. `reviewer = "none"` is refused: a developer cannot
  opt out of review. `copilot` still needs `ship.copilot_bot_id` in the repository binding. The run's
  bind line names the reviewer and its source, and the pull request record notes a reviewer the
  developer binding selected. `ship.review` is optional; with neither, the reviewer is
  `adversarial-review`.
  **Consumer contract:** `bin/ouro-binding.py check` required `ship.review`, and a developer
  binding admitted only `[runtime]`, `[coordination]` and `[followup]`. Now `ship.review` may be
  absent, and the developer binding admits `[review]`. Migration: none; an existing binding and
  developer file read as before.

- `templates/ouro.toml.example` shows a commented `[[ship.person]]` entry under `[ship]`: a login
  with its own `policy` and `unattended_classes`.

- `[ship]` admits `test_paths`, a non-empty list of globs naming the repository's test files.
  `bin/ouro-binding.py check` refuses `ship.test_first_for_bugs = true` without it, an empty
  list, a blank entry, and an entry that is not a string. `docs/binding.md` no longer claims a BUILD-node
  test-first rule; the driver's base check, above, reads both keys.
  **Consumer contract:** `[ship]` admitted no `test_paths`, and `check` accepted
  `test_first_for_bugs = true` alone; it now refuses that. Migration: a binding that sets
  `test_first_for_bugs = true` adds `test_paths` naming its test files, or drops the key.

- A redraw of the pull request body keeps a `## ` section a person added under a heading the driver
  does not draw, in its order and before `## Follow-up`, so the REVIEW brief reads it and a later
  body update leaves it on the pull request: an `## Acceptance` section that records a hand-run
  acceptance now reaches the reviewer. The redraw keeps only the text the person wrote: the drawn
  text under no heading of its own (the Verification block, the part, `Fixes` and `Review:` lines)
  is redrawn from the current record and appears once, wherever the person put their heading,
  inside the Verification block included: a heading there is kept with the lines the person wrote
  under it, before `## Follow-up`.

- `Watch-PullRequestChecks.ps1 -Pr` waits up to ten minutes for the head's `pull_request` run
  when a workflow at the default branch names `pull_request`, as it does for a push run a filter
  selects, so a run missing from the list by commit for its first minute no longer stops
  `ouro run` with a driver error. A pull request that reads CONFLICTING after the first minute
  with no run still stops then; with no workflow naming `pull_request` the stop still comes after
  a minute. The stop for a run that never shows, and the script's help, say ten minutes.

- A landing that closes an issue triages each open `blocked` issue whose `**Unblocks when:**`
  first line names it, in number order, after the queue's anchors are refreshed, and prints one
  line per issue with its verdict; a triage that fails is reported on its line and the landing
  still succeeds.

- A `needs-ruling` triage question on an anchor line whose fragment no longer greps in a file that
  is still tracked carries the full replacement anchor line the TRIAGE node proposes, posted only
  when every quoted fragment on it greps in that file at the tip; otherwise the question says no
  replacement was found. Triage never edits the line: whoever answers the question applies it.

- Triage clears a fired `**Unblocks when:**` trigger before its node grades the issue: when the
  body's first line names exactly one issue, by the unblock reference rule of
  `bin/Get-CompileProgram.ps1`, and that issue is closed as completed, or closed by a pull request
  whose merge commit is on the default branch, and the text right after the reference, less an
  optional `part <k>`, starts with one of `lands`, `landed`, `has landed`, `merges`, `is merged`,
  `closes`, `is closed`, `ships` or `has shipped`, ignoring case, the driver deletes the line in a
  body edit before the `**Triage**` comment, which names the line and the evidence. Prose, several
  references, an open issue, one closed as not planned, and a line whose named issue has landed
  but whose wording waits for something more stay blocked; for that last one the comment says the
  issue has landed and the line does not say only that it lands, so the owner clears it once its
  event has happened. A failed read of the issue leaves the line and the comment says so.

- The REVIEW answer's `residue` entries carry an `index`, the item's 1-based position in the
  record's `## Follow-up` list, and the driver ties each route to the item that position names,
  so a reworded entry keeps its `ledger` or `idea` route instead of being filed as an `issue`.
  An entry whose index is outside the list or repeats an earlier one, and an item no entry
  routes, are listed in the `**Follow-ups filed**` comment as well as printed; an unrouted item
  is still filed as an `issue`.
- Under `ship.validation = "ci"` the BUILD node is now granted each gate with `paths` the pull
  request's checks run, notice gates excepted, with the note local validation gives it: run it
  when the change touches those paths. The brief no longer lists that gate among the CI-owned
  commands, so a builder that edits a suite, or the code it covers, sees it run before the push.
  A gate without `paths` the checks run, and a gate another workflow runs, stay CI-owned. The
  checks remain the proof: a builder's run proves nothing, and a granted gate it reports as
  `validation_pending` waits on the checks alone.

- Under `ship.validation = "ci"` the BUILD node is now granted every gate the binding marks
  `cheap`, with the note local validation gives it, and the brief no longer lists that gate among
  the CI-owned commands, so a seconds-long check runs before the first push. The pull request's
  checks remain the proof for a cheap gate they run.
  A `validation_pending` request for a gate the checks run whose paths the diff does not touch
  now waits on nothing, since nothing requires that gate, and the record shows it as awaiting
  nothing.

- Every leftover of a landing now gets one of four routes before the merge: `fix-now`, `issue`,
  `idea` or `ledger`, each with a `reason`
  (empty, `own-change`, `data-loss`, `wrong-place`, `breaks-gate`, `misleads-run` or
  `open-decision`). The REVIEW node routes each L finding and, in a new `residue` list of its
  answer, each residue item of the pull request record. The fix-now round opens only when a
  `fix-now` item carries a reason; the round is handed the record's other `fix-now` items too, and
  may fix a trivial bug in a file the change does not touch, as long as the commit keeps the flow
  class. The record's fix-now line names the paths that commit touched outside the change's own
  files. The FOLLOW-UP node is handed each item with the route it is filed under, which the run
  prints whenever it differs from the review's: an `issue` with no reason is `ledger`, an item with
  no route is `issue`, and a `fix-now` item no round fixed is `issue` with a reason and `ledger`
  without one. The BUILD node no longer lists as residue a trivial thing in the lines it changes.

- The FOLLOW-UP node now writes the issue for each leftover and decides nothing else: the driver
  numbers the items it hands the node, the node answers one entry per number (`item`, `title`,
  `body`, `labels`), and the driver files every item under the route it read, whatever the node
  answers, with the item's own text where the node wrote none. An `issue` is filed with
  `needs-triage`, an `idea` as its own issue with the `idea` label and no other state label, and a
  `ledger` item is appended to the area's `Cleanup: <area>` ledger even when the node titles it
  otherwise, and an `issue` or `idea` item the node titles as a ledger is titled with its own text.
  Each entry of the `**Follow-ups filed**` comment names its route as `(route: <route>)`.

- The quality table of `bin/loop-outcomes.py` gains a `Follow-up issues`, `Issues per landing`,
  `Follow-up ideas` and `Ledger lines` column, between the turns and the flake columns: per
  landing week, the entries the newest `**Follow-ups filed**` comment of each landed pull request
  lists as filed, read as the driver reads it (its author the job token's bot or an approver, its
  bullets before any `Not filed` line), counted by the `(route: <route>)` tag each entry carries, before any note on dead anchors. An
  entry with no route, from a comment written before routes, is a ledger line when it files,
  appends to or reopens a `Cleanup` ledger, and an issue otherwise. A landing with no such comment
  counts 0.

- A low review finding about the change itself is now fixed in its pull request instead of being
  filed. When the standing verdict is CLEAN and its record holds `fix-now` findings (routed as the
  first entry of this section says), `ouro run` hands them to one BUILD before the merge
  decision; the commit is pushed, the checks run on it and the REVIEW node verifies it. The round runs once per change, does not count toward
  the five fix rounds, and the pull request record names it in a line of its own, so the `Review:`
  line's `rounds` and the weekly quality table do not count it. An H or M fix round is handed the
  `fix-now` findings of the record it answers instead. A fix-now commit that would raise the
  change's flow class is not pushed: the reviewed head merges and its findings are filed. A
  `fix-now` finding the verification still finds, any new L finding, and the other findings of the
  record the round answered go to the FOLLOW-UP node. A landing that takes the
  round, and had no other fix round, runs the pull request's checks once more.

- The pull request record's usage rows now carry each node call's turn count, `unknown` where the
  backend's usage carries none, and they accumulate across invocations by session: a
  resume that runs a fix BUILD or a later REVIEW round keeps the earlier rows, and the FOLLOWUP
  node's row is added after the merge. The quality table of `bin/loop-outcomes.py` gains a
  `BUILD turns`, `REVIEW turns` and `FOLLOWUP turns` column, before the flake columns: the median
  and total of the node's known counts, with the unknown ones apart. A pull request whose record
  holds no usage rows, as every one drawn before this release, counts as unknown.

- A triage SPLIT of a cleanup ledger now files the children itself, in the same command. It
  files each child with `needs-triage`, posts the `**Sweep** at <sha>` mark on the ledger naming
  them, and triages each child with a fresh TRIAGE node. A child the ledger triage asks a question
  about is posted NEEDS-RULING with that question, and a child's own SPLIT is left to the owner,
  so nothing recurses. A write that fails partway is a driver error that names the children
  already filed, and a re-triage of the ledger resumes it: the open `Part of #<N>` children
  already filed stand for their comments, so no child is filed twice. The ledger's verdict comment ends with the line that the driver files the
  children. A SPLIT of any other issue still leaves the children to the owner.
  **Consumer contract:** `ouro triage <N>` on a ledger SPLIT wrote the verdict and left the
  children to the owner; it now files and triages them. Migration: stop filing a ledger's
  children by hand, and run `ouro triage <N> --dry-run` first to read the bodies and the sweep
  mark it would post.
- The triage skill's description and its relay step say what a cleanup-ledger SPLIT does in the
  same command: it files the children, posts the sweep mark naming them and triages each child.
  The session relays the filed child numbers, the sweep mark and each child's verdict, and, when
  the split stops partway, the children the driver error names as already filed.

- A full-flow run no longer watches the default branch's push runs on the merge commit when the
  pull request was merged at a green head, the merged tree is that head's and the base did not
  move (the merge commit's first parent is an ancestor of that head): it prints one line saying
  the watch is skipped and why, then cleans up and files follow-ups. The head is green when the
  run merged it itself once its checks passed, or, for the owner's merge a resumed run reads,
  when the pull request's checks re-read at its head show one passed and none outside pass or
  skipping. Any other merge, and one whose checks cannot be read, is watched as before. On a
  skipped landing a push-only workflow is not watched.

- The TRIAGE brief gains a section of closed issues that may be the same work. The driver
  searches closed issues for each distinctive identifier in the body's code blocks (a camelCase
  or snake_case name; type and encoding names such as `UINT32` or `utf8` are not searched) and
  for the title less a leading tag of at most three words and a colon. For each hit it lists the
  closed date, the merged pull requests that close it (by a closing reference to its number or
  URL, or by its own closing event, which covers a sidebar link), each merge commit, whether the
  commit is on the default branch, and the earliest release tags that contain it, with their
  dates. Hits found by more searches come first, and the brief names the hits and identifiers it
  cut. A search that fails reads as unknown in the brief, never as none.
- Triage gains a `DUPLICATE` verdict, whose `duplicate_of` names a closed issue from that
  section. The driver accepts it only when that issue is in the section's list, is closed when
  read again, and has a merged pull request closing it whose merge commit is on the default
  branch; any other is a driver error naming the failed condition. An accepted one moves the
  issue to `needs-ruling` with the question "Close as a duplicate of #N?" and the lookup's lines
  for that issue; the driver closes nothing. A report filed after a release tag held the fix gets
  a comment on whether the reporter ran such a build, and a question asking for the version when
  the body names none.
- The anchor parser and the driver's re-point close a code fence only on a bare backtick run at
  least as long as the one that opened it, so a shorter marker, or one with an info string, inside
  a fence is text. An `Anchors` heading in a code block inside a longer fence no longer opens an
  `Anchors` section, and a quoted fragment under it is not grepped.
- The anchor parser greps a double-quoted fragment only on an anchor line: a list item under the
  body's `Anchors` heading whose first backticked span names a tracked file, the section scoped
  as the driver's re-point scopes it, where a `#` line inside a fence is no heading. Every other double-quoted
  string in an issue body or a `-Doc` file is prose and is not checked, so a quoted person,
  phrase, error message or test name no longer comes back as a dead anchor. A backticked path is
  still resolved wherever it stands. The anchor gate, the shape gate, triage and the run's verify
  step read the same parser, and their help text and the shape gate's no-anchor message say so.
  **Consumer contract:** a quoted fragment on a list item citing a file was grepped in that file
  wherever the item stood, and a code-looking quote on any other line was grepped repo-wide; now
  only the first is checked, and only under an `Anchors` heading. A body that anchors inline
  with no `Anchors` heading keeps its path checks and loses its fragment checks. Migration: move
  the anchor lines whose quotes should be verified under an `## Anchors` heading; a quote meant
  as prose needs no rewrite.

- The contract no longer gives a dispatch a 100-turn budget, and the reviewer prompt no longer
  tells a REVIEW node about one: nothing enforced it, and the node's wall-clock timeout stays the
  bound. A reviewer whose run ends early still lists what it did not finish under UNTESTED. The
  runtime attempt diagnostics record a Claude node's turn count from its `num_turns`, and show
  `unknown` where the envelope has none.

- The Claude backend takes `runtime.effort` and `runtime.roles.<role>.effort` (`low`, `medium`,
  `high`, `xhigh` or `max`) and launches `claude -p` with `--effort <level>`; with no effort set it
  passes none, and the host's Claude Code settings decide as before. The runtime attempt
  diagnostics record each node's effort beside its model, `unknown` where none was passed.

- Under `ship.validation = "ci"`, the PR record and the REVIEW brief no longer present a
  CI-covered `validation_pending` request as outstanding once the declared checks have succeeded
  at the head: it moves to `validation_met`, naming the head and the checks, and one the checks
  have not proved yet is marked as awaiting them. A request BUILD recorded on an earlier commit
  no longer draws the same false-record finding every review round. Caller requests are
  unchanged.

- `ouro run N` exits 4 when it waits on pending validation that only `ouro run N --validate`, or
  a native Linux host, clears: the `waiting for validation` returns, `execution unavailable`
  and `requires a supported lifecycle boundary` included. A wait on checks or a merge still
  exits 0, so a rerun loop that treats 0 as "still waiting" no longer spins on a wait re-running
  the same command cannot clear.
  **Consumer contract:** `ouro run` exited 0 on a pending-validation wait; it now exits 4.
  Migration: a caller that loops on exit 0 handles 4 by invoking `ouro run N --validate`, or by
  stopping and reporting the wait.

- A binding may declare extra acceptance runner prefixes in `ship.acceptance_runners`, such as
  `["cargo ", "make "]`. `ouro run` and `ouro triage` grant a command that starts with one
  exactly as they grant one that starts with a built-in runner, in an Acceptance section and in
  an `Acceptance command:` field, so a repository whose toolchain is not on the built-in list
  can name a targeted test as acceptance without wrapping it in `bash -c`. A prefix is matched
  as written, adds no shell grammar, and `check` refuses one that is empty, whitespace only, or
  holds a character the single-command grammar rejects. Older readers refuse the unknown key,
  so update the ouro pin before declaring it.

## [v1.11.3] - 2026-11-17

- A heading inside a fenced code block opens no Acceptance, Tests or Verification section, so
  an example body quoted in a fence grants none of the commands that follow it. A line that
  opens with three or more backticks and holds another backtick later on is an inline code
  span, not a fence opener, as in CommonMark, so it no longer hides the headings below it.

- An `Acceptance command:` label with no backticked command, bare or carrying prose such as
  `run from the repo root`, is no longer refused when a span or shell fence below it in the same
  Acceptance, Tests or Verification run grants a command, with prose lines or a sub-heading in
  between, so such a body runs again instead of stopping as `acceptance unrunnable` with the
  wrong fix, "put the command in backticks". A label carrying an unbackticked runner command, or
  one above no granted command, is still refused.

## [v1.11.2] - 2026-11-17

- The plugin draws a local `ouro run`'s progress line above the prompt in Claude Code with no
  setup: `hooks/hooks.json` registers the mod `hooks/band/register.tsx` under `modules`, which
  shows `bin/ouro-run-status.py`'s line dimmed, redraws it about every ten seconds while it
  shows, and leaves the slot empty when the script prints nothing or a survey holds it. It
  reads the session repository's progress file only, never `gh`. A `statusLine` command can
  still run the script.
- `ouro run`'s preflight proves only the roles the command can dispatch, on a single backend as
  on mixed ones: a run resumed on a merged pull request the triager alone, a checkpoint the
  checkpointer alone, any other run the triager, its one build role and the reviewer. Qwen Code
  makes one live call per distinct contract and model among them, so an express run makes no
  PROBE call, a run with no checkpoint no commands call, and `builder` and `builder_trivial` on
  different models each get the BUILD proof. Codex checks and probes the models of those roles
  alone, and `ouro triage` the triager's alone. A diff that tightens the flow from express or
  direct to standard or full preflights the reviewer again at PROBE before REVIEW, on a single backend as on mixed ones.
- An issue body's `Acceptance command:` line that grants nothing, because its command has no
  backticks or the line sits outside an Acceptance, Tests or Verification section, is refused
  as acceptance: triage answers NEEDS-RULING and a run stops as `acceptance unrunnable` before
  BUILD, naming the fix (add the backticks, or move the line under an `## Acceptance`
  heading). Such a line used to pass unread, and a run resumed after BUILD could fail on it as a driver error. A
  body holding such a line that used to run now stops until the line is fixed.
- A Claude REVIEW node with probe capability is granted Claude Code's `PowerShell` tool beside
  `Bash`, with `git push`, `git commit` and `gh` refused in both, so a reviewer on Windows can
  run its mutation probes; under `ship.review_sandbox` it keeps `Bash` alone. The REVIEW brief
  says probes are made and restored with shell commands, and a REVIEW answer that carries
  refused calls prints one line in the run's log naming each refused tool and its command or path.

## [v1.11.1] - 2026-11-09

- `ship.review_sandbox = true` refuses a Codex reviewer on WSL2 before any tracker call, naming
  the missing Codex sandbox route. Codex's route is Linux and macOS, not Linux, WSL2 and macOS
  as the v1.11.0 entry says: its PROBE admission refuses every WSL host, and the run used to be
  refused only when the REVIEW node launched.
- A triage `NEEDS-RULING`, `BLOCKED` or `SPLIT` that removes `agent-ready` also removes the
  `checkpoint` and `trivial` modifiers, and a `STALE` on a checkpoint removes `trivial` with
  them, so no modifier is left standing alone on a state the driver does not run.
- A fix round started by a red validation command or a red gate, whose BUILD leaves the tree
  unchanged, ends as `**Stop:** open decision` (exit 3) naming the command, its exit code and
  its evidence log, not as the driver error "node BUILD left no change in the tree". A
  no-change BUILD outside a red-command round is still a driver error.
- A resumed run under CI validation syncs its branch with the default branch before comparing
  `[[gate]]` entries, so a `[[gate]]` change that landed after the BUILD no longer strands the
  run on "CI gate coverage changed". A mismatch left after the sync names the copy that differs:
  the branch's HEAD or the invoking checkout.
- A resume whose branch records a pending validation command the invoking binding no longer
  grants stops as `**Stop:** open decision`, naming the command and its BUILD commit, not as the
  driver error "validation command not granted".
- On a resumed local-validation run, `--validate` runs the required commands missing at the
  invoking head even when no BUILD offered them as candidates, provided each is in the invoking
  Acceptance or binding allowlist. A fresh BUILD or a later fix head still needs candidates, and
  a failed or malformed BUILD exit report keeps its finding path.
- The TRIAGE output schemas of the Claude, Codex and Qwen Code backends admit the `BLOCKED`
  verdict, which the driver already accepted and moved labels for.

## [v1.11.0] - 2026-11-06

- A Qwen Code preflight that is not file-only proves each contract its roles need through the
  real CLI and the allow-list hook: CHECKPOINT's commands and PROBE run a granted shell command,
  and BUILD also writes one file in the trusted scratch directory. A transcript in which the tool
  is missing, failed or not granted refuses the backend before the run writes anything.
- A Qwen Code node that runs commands or edits (PROBE review, CHECKPOINT with commands, BUILD)
  gets its shell and write tools: its private trusted-folders file trusts its resolved working
  directory, so Qwen Code no longer overrides `--approval-mode yolo` to `default`, and marks
  every ancestor of that directory untrusted, so no parent directory's `.env` loads. READ nodes
  and the external reader keep a trust file that trusts nothing.
- A Qwen Code node that runs commands or edits (PROBE review, CHECKPOINT with commands, BUILD)
  refuses before launch when its working directory's top level holds a `.qwen` or `.env` entry,
  in any case, and the refusal names the path. READ nodes are unchanged.
- A PROBE review node launches without git or GitHub credentials on every backend (Claude, Codex
  and Qwen Code), with or without `ship.review_sandbox`. `GH_TOKEN`, `GITHUB_TOKEN`,
  `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `SSH_AUTH_SOCK`, `GIT_ASKPASS` and
  `SSH_ASKPASS` are removed, `GIT_TERMINAL_PROMPT=0` is set, `GH_CONFIG_DIR` names an empty
  directory private to the launch, `GIT_CONFIG_COUNT` entries clear `credential.helper` and
  `core.askPass`, and `GIT_SSH_COMMAND` runs an ssh with no config, agent or identity. Codex's
  bridge applies the same to each command it runs. The review copy is a standalone repository
  with no remote in place of a linked worktree, which shared the main checkout's `origin`. A
  push or `gh` call that keeps these overrides, through a nested interpreter or a renamed git
  alike, finds no helper, askpass, agent, ssh key or `gh` login. The overrides redirect and do
  not delete: a command that drops one or names a credential source itself
  (`git -c credential.helper=store`) authenticates again, and on Claude and Codex git still sends
  a matching `~/.netrc` entry to an http(s) remote, even on a plain `git push` that keeps every
  override, while `~/.git-credentials`, `~/.ssh` and `~/.config/gh` stay readable. The commands still run unconfined outside a sandbox: they can
  write outside the copy and reach the network, and a raw `ssh` with an explicit key is not
  covered. The harness's own model login and the READ, CHECKPOINT and BUILD environments are
  unchanged.
- A repository can require a sandboxed REVIEW node: with `ship.review_sandbox = true`, `ouro run`
  refuses before any tracker call when the reviewer role's backend has no sandbox route on the
  host, its prerequisites are missing, or the host is native Windows, WSL1 or another platform
  with no harness sandbox. The refusal names the backend, the platform and the missing piece.
  Codex keeps its own boundary, on Linux, WSL2 and macOS. A Claude reviewer launches its PROBE
  review inside Claude Code's sandbox (`allowUnsandboxedCommands` off, `failIfUnavailable` on, no
  allowed network domain, and a temp directory private to the launch, deleted when it returns, in
  place of the `/tmp/claude-<uid>` the user's sessions share), on macOS and, with `bwrap` and `socat` on PATH, on Linux and WSL2.
  A Qwen Code reviewer launches its PROBE review under Qwen Code's tool execution sandbox
  (`bwrap`, `workspace-write` on the review copy, `network: closed`, deny rules for `git push`,
  `git commit` and `gh`), on Linux alone and with `/usr/bin/bwrap` present; the allow-list hook
  does not run there, so its grant check does not discard that node's answer.
  **Consumer contract:** `[ship]` admitted no `review_sandbox`; it now admits a boolean, and
  `check` refuses any other type. Migration: none; an unset key runs as before.
- A developer can select Qwen Code workers, `[runtime] backend = "qwen"`. The model comes from an
  OpenAI-compatible endpoint named by `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` in
  the launch environment. An allow-list hook and isolation from the repository's and the
  developer's own Qwen configuration bound each node. With every role on Qwen Code, `ouro run`'s
  preflight proves every role's model, and `ouro triage`'s the triager's alone.
  **Consumer contract:** `runtime.backend` admitted `claude` or `codex`, and `ouro-developer.py
  install --backend` took `claude|codex`; both now also take `qwen`. Migration: none; an existing
  developer binding is unchanged.
- The developer binding names worker roles per node: `triager` (TRIAGE and FOLLOWUP) and
  `checkpointer` (CHECKPOINT) join `builder`, `builder_trivial` and `reviewer`, and take the
  `builder` model when nothing names theirs, as those nodes do today. An optional
  `[runtime.roles.<role>]` table holds `backend`, `model` and `effort`, validated against the
  role's backend; its `model` precedes `runtime.models`.
  **Consumer contract:** `[runtime]` admitted `backend`, `effort` and `models` with the roles
  `builder`, `builder_trivial` and `reviewer`; it now also admits `roles`, and both tables take
  `triager` and `checkpointer`. `ouro-developer.py install --model` also writes those two roles.
  Migration: none; an existing developer binding is unchanged.
- Each node dispatches through its role's backend, so one `ouro run` can triage and file
  follow-ups on Qwen Code while it builds and reviews on Claude. Before any write, the run
  preflights each backend its roles select once, at the widest contract those roles can need for
  the command, and refuses a role whose backend does not admit it, naming the role, the backend
  and the contract; a diff that tightens the flow past express after BUILD commits replans
  REVIEW, refusing there a reviewer whose backend does not admit PROBE. Every backend declares
  what it admits: Claude and Qwen Code every contract, Codex every contract on Linux and READ on
  native Windows. The attempt diagnostics, the usage table (a new last `backend` column)
  and the progress file's `reviewer` field name the backend each node used. `ouro-developer.py
  inspect` prints each role's backend and model, and `inspect --check` preflights every backend a
  role selects. A binding with no role table dispatches and preflights every node as before.
  **Consumer contract:** a `runtime.roles.<role>` `backend` or `effort` other than the runtime's
  own was refused; it is now admitted, and a `runtime.models` entry for a role whose table
  selects another backend is refused. `inspect` prints a `roles` object, and the usage table a
  `backend` column. Migration: none; an existing developer binding is unchanged.
- Qwen Code can be the external review reader: `ship.external_cli = "qwen"` runs the pull
  request's outside read through `bin/external-audit.py --cli qwen`. The worker's allow-list hook
  and isolation bound the read. A tool that ran without the hook's grant discards the verdict,
  and each denied call prints a warning. The endpoint comes from `OPENAI_API_KEY`,
  `OPENAI_BASE_URL` and `OPENAI_MODEL`. A vendored copy of `external-audit.py`, which has no
  `ouro` package beside it, refuses `qwen` and runs every other reader. The reader takes the
  CLI's output from `stdout.txt`, which the CLI writes directly, not from a pipe: Qwen Code
  truncates a piped `-o json` stream and still exits 0.
  **Consumer contract:** `ship.external_cli`, the installer's `-ExternalCli` and
  `external-audit.py --cli` took `grok`, `claude`, `codex` or `copilot`; each now also takes
  `qwen`. Migration: none.
- Qwen Code workers run commands: a `checkpointer` whose brief grants commands and a `reviewer`
  that probes (standard and full flows) can select Qwen Code. Such a node runs in approval mode
  `yolo`, and the allow-list hook decides each shell call: CHECKPOINT's exact commands, or for
  PROBE any single command but `git push`, `git commit` (dashed builtins such as `git-push`
  included) and `gh`, with no `$`, glob, brace, git inline or `GIT_CONFIG` environment config, or
  alias; a compound command, a redirection, a substitution, a leading
  assignment, a background run and another directory are refused, as bash parses the command
  line. A nested interpreter's argument (`python3 -c`), an argument a command re-splits
  (`env -S`) and a git binary under another name (a symlink or copy) are not inspected, as with
  Claude's deny patterns, so PROBE's refusals stop a reviewer's slip, not a determined push.
  The commands run unconfined, as on Claude. Preflight proves the hook admits a listed
  command and refuses an unlisted, a chained and a probe's `git push` one.
- Qwen Code workers build: a `builder` or `builder_trivial` role can select Qwen Code. The
  allow-list hook decides each `write_file`, `edit` and `notebook_edit` call: its path must lie
  inside the worktree, spelled and resolved, outside `.git`, `.claude`, `.codex`, `.agents`,
  `.aws` and `.qwen`. Its shell runs the brief's exact commands and `git rm`, `git mv`,
  `git status`, `git diff`, `git log` and `git ls-files` with any arguments. A refused write
  returns in the shared denial shape, so one under a harness configuration directory becomes a
  manual step on the pull request with the proposed content or old and new text; a refused
  `notebook_edit` is listed with no proposed edit. `.qwen` is among those directories only while
  BUILD runs on Qwen Code: on Claude and Codex a `.qwen/` path is no manual step. BUILD's prompt carries the installed run skill, as on Codex. Preflight also
  proves the hook admits a worktree write and refuses a `.claude/` and an outside one.

## [v1.10.0] - 2026-11-03

- The binding accepts `[[ship.person]]` entries: a gh `login`, unique without regard to case and
  refused with surrounding whitespace, which no gh login has, with
  that developer's own `policy`, `unattended_classes` or both, each under the rules of the `[ship]`
  key it stands for. The binding check validates them. The merge decision, the dry run's plan and
  triage's binding notes take the entry of the gh login running the driver over `[ship]`, key by
  key, still narrowed by the issue; `**Ready for merge**` and `**Landed unattended**` name whose
  policy applied.
  **Consumer contract:** `[ship]` held one `policy` and one `unattended_classes` for every
  developer, and `check` refused `ship.person` as an unknown key; it now admits the entries above.
  Migration: none; a binding without entries is unchanged.

- A configuration edit the Claude worker was refused becomes a manual step that carries the
  proposed content or old and new text, as a refused Codex edit already did: the Claude adapter
  returns its refusals in the shared denial shape.

- The worker backends are one registry, and each adapter exports one interface: preflight,
  worktree readiness, execution, PROBE confinement and answer validation, each implemented or
  declared not required, unsupported or unconfined. The developer binding, the installer and the
  node adapter read it in place of per-backend branches; behaviour, arguments and error text are
  unchanged.

## [v1.9.3] - 2026-11-02

**Net lines:** +1743 across docs, bin and tests, because the Codex PROBE boundary needs its write
confinement and fail-closed alias checks, and the reviewer section and the preflight file-proof
classification carry their test rows.


- The TRIAGE brief carries the commands the driver reads as the body's grants, or says it grants
  none, and tells the node that a command off that list is not granted and that an earlier
  comment's `Acceptance command:` field stops counting once its verdict posts. A
  `validation_pending` entry the driver refuses now names its command and the cause: not granted,
  or duplicate.

- The TRIAGE and FOLLOWUP briefs tell the node to read the binding file's comments around
  `[labels].area`, where a repository may say what each area covers, before it names an area
  label or asks which one applies.

- `loop-outcomes.py` reports the H and M review findings, per build cohort and per reader,
  counted per round with no pairing of a re-report to a finding: raised, fixed, re-raised (a
  round's findings the next round lists STANDS, and how many entries ended in the
  unchanged-finding stop), ruled away or open, with OUTSIDE follow-ups apart and a reviewer
  precision of fixed over fixed plus ruled away. Fixed, ruled away and open sum to raised per pull
  request, not per reader row. It reads the review records' JSON on every pull request, landed or
  not. Report-only.

- Codex PROBE commands use a read-only parent with writes confined to the canonical disposable
  copy, including command temporary storage. Unsupported alias topologies fail closed; an opt-in
  default-provider qualification retains generated public boundary evidence on failure.

- `bin/ouro-run-status.py` renders a finished run (any outcome but `running`, or a `running`
  file whose process is gone) as a one-line summary with a finished marker, `done ✓`,
  `waiting ⏸` or `<outcome> ✗ at <node>`, for 5 minutes after the file's last update, then
  prints nothing. A frozen progress bar and the `(stale)` suffix no longer show.

## [v1.9.2] - 2026-11-01

**Net lines:** +1337 across docs, bin and tests, because two owner-requested features carry
+735: +662 for the observe-only run progress file and its status-line reader, +73 for the native
issue type; +422 for CI validation reconciling exact-head Actions jobs the rollup omits, whose
admission checks are each covered by refusal rows; and +180 across three Codex and review bug
fixes, each with the rows that fail without it.

- CI validation also admits a required job that the pull request's check rollup omits, when an
  authenticated GitHub Actions run of the same unchanged workflow at the exact head (the PR's own
  run or a `workflow_dispatch` run) completed it successfully. A failing, pending or ambiguous row
  still waits, and the wait names the uncovered checks.

- Codex bridge read failures remain tool observations without creating manual configuration
  edits. Failed configuration mutations retain their paths and proposed changes for the manual barrier.

- A run keeps an observe-only progress file at `<git-common-dir>/ouro-run/<issue>.json`,
  replaced at each logged step and left in place at exit. A failed write is one stderr
  line and changes no exit, log or tracker write. The plugin's `bin/ouro-run-status.py`
  prints one status line from the newest file, or nothing. `--dry-run` writes no such file.

- An issue's native GitHub type (an organization's issue types) counts as its type label when
  `[labels].type` names it, compared without case. The shape gate and triage's preflight read it
  through GraphQL and count it once beside a label of the same name, and triage no longer adds a
  type label to an issue whose type already supplies one. Where the type cannot be read (a
  repository without organization issue types, or a server without the field), the labels alone
  count, as before, and a failed read says so.

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
