# The binding — `.claude/ouro.toml`

Every `ouro` skill starts by reading `.claude/ouro.toml` at the repo root. It is the only
place repo-specific facts live: the contract states the protocol and the skills implement it;
the binding carries the bindings. **No binding, or a binding that fails validation, means the skill refuses to run** —
the same honesty as `/ouro:run` refusing an issue without `agent-ready`. `/ouro:init` is the
exception: with no binding it runs the installer, which writes one; a binding that fails
validation still makes it refuse.

Three consumers read it — a person, the coding agent, and the deterministic gates — so it is
typed and validated, not prose. `bin/ouro-binding.py check` (Python 3.11+, stdlib only) fails
closed on an unknown key. Comments are welcome; they are for the people. In a repo that has a
binding, the deterministic gates that read it need `python3` 3.11+ on PATH — a Windows dev box
needs a `python3` there too; the Store's `python3` alias and an msys2 `python3` both count; in a
repo with none, `bin/Test-AgentReadyShape.ps1` and `bin/Get-RollingIssue.ps1` (and the gates that
post through it) skip the read with an INFO line, and those and `bin/Test-AgentReadyAnchors.ps1`
address the repository `origin` names in place of `[repo].slug`.

## Schema (v1)

One row per key the validator accepts, spelled dotted; `[[gate]]` is a list with one entry per
verify command, and `[docs.banned]` a table whose keys are data. Required means `check` refuses
the binding without it. `templates/ouro.toml.example` is the copy-paste example.

| Key | Values | Default | What it changes |
|---|---|---|---|
| `schema` | `1` | required | the schema version this document describes |
| `repo.slug` | `"owner/name"` | required | the GitHub repository every `gh` call names; never embedded in a skill |
| `repo.default_branch` | a branch name | required | the branch runs cut from, land on and watch |
| `repo.protected_branch_prefixes` | branch-name prefixes | `[]` | branches an agent never squash-lands, pushes to, branches from or sweeps; list a long-lived branch a merged PR once had as its head, which the sweep otherwise reads as finished, and read the sweep's report mode before any `-Delete` |
| `repo.checkout` | an absolute path | none | the canonical local checkout, which no ouro script reads (the driver finds the main checkout through git); per-machine, so it belongs in the overlay below, and `check` warns when the committed binding carries it |
| `labels.scope` | label names | `[]` | scope or effort labels that may ride on any state |
| `labels.area` | label names | `[]` | the area labels a gate's `areas` selects on; the shape gate requires at least one on every agent-ready issue, an empty or undeclared set skipping that check, and an issue with several runs every gate any of them selects |
| `labels.type` | label names | `[]` | the type labels; the shape gate requires exactly one on every agent-ready issue, an empty or undeclared set skipping that check; disjoint from `area` |
| `gate.areas` | area labels, or `"*"` | required per entry | the issues that select the gate; `"*"` is every issue |
| `gate.run` | a command line | required per entry | the verify command, run from the repository root; `<ouro>` in it is the plugin root |
| `gate.ci` | a pwsh command line | `run` | what CI runs in place of `run`; one that only prints a skip makes the driver run `run` locally in every class |
| `gate.paths` | git pathspecs | none | the gate runs only when the diff changes one of them |
| `ship.policy` | `stop-at-pr`, `trivial-merge`, `merge-on-green` | required | who merges; the effective policy is the stricter of the repository's and the issue's |
| `ship.unattended_classes` | `express`, `standard`, `full` | `["express", "standard"]` | under `merge-on-green`, the Flow classes the driver merges unattended |
| `ship.review` | `copilot`, `adversarial-review`, `external-audit`, `none` | required | the reviewer, by what it can observe: Choosing `ship.review` below |
| `ship.copilot_bot_id` | a Bot node id | none; required under `copilot` | the id the review request names; github.com-wide, not the repository's |
| `ship.external_cli` | `grok`, `claude`, `codex`, `copilot` | none; required under `external-audit` | the CLI `bin/external-audit.py` runs as the reader |
| `ship.external_model` | a model name | the CLI's own default | the reader's model; one the CLI refuses counts as a round-1 failure |
| `ship.landing` | `merge-squash`, `rebase-squash`, `rebase-merge`, `merge-merge` | `merge-squash` | the sync and merge answers in one word: The landing policy below |
| `ship.sync` | `merge`, `rebase` | `merge` | how a branch is brought up to date before its pull request |
| `ship.merge` | `squash`, `merge` | `squash` | how a pull request lands |
| `ship.test_first_for_bugs` | `true`, `false` | none | accepted and read by nothing: Rules the skills apply below |
| `ship.forbidden_check` | `required`, `off` | follows the target repository's visibility | whether the hook checks what a session posts against the private token list: The forbidden-token check below |
| `ship.direct` | globs | none, the lane off | the files the direct lane may push |
| `models.builder` | `opus`, `sonnet`, `haiku`, `fable` | `sonnet` | the model of the BUILD, TRIAGE, CHECKPOINT and FOLLOWUP nodes |
| `models.builder_trivial` | the same | `sonnet` | the BUILD node's model on a `trivial` issue |
| `models.reviewer` | the same | `opus` | the REVIEW node's model |
| `rolling_issues.loop_runs` | an exact issue title | none | the issue `bin/loop-outcomes.py` appends one comment per run to |
| `rolling_issues.docs_freshness` | an exact issue title | none | the docs sweep's rolling issue; no ouro script reads it |
| `rolling_issues.comments_freshness` | an exact issue title | none | the rolling issue of a comments sweep the repository runs itself; no ouro script reads it |
| `docs.extensions` | file extensions | the gate's | what makes a code-span token path-shaped |
| `docs.exclude` | substrings of the slash-prefixed relative path | the gate's | scope exclusions; `[]` means none, so a vendored tree is scanned |
| `docs.suppress_prefixes` | path prefixes | the gate's | references never reported: another repository, build output |
| `docs.generated_pattern` | a regex | the gate's | a generated doc tree the gate skips |
| `docs.html_globs` | globs | the gate's | HTML pages whose relative hrefs and code paths must resolve; `exclude` still applies |
| `docs.index_path` | a path | the gate's | the doc index; with `indexed_trees`, arms both index checks |
| `docs.indexed_trees` | paths | the gate's | the trees whose docs must be indexed |
| `docs.index_exempt` | regexes | the gate's | paths exempt from the coverage check |
| `docs.planning_paths` | regexes | the gate's | docs where work-remaining content is allowed |
| `docs.report_only` | signal ids | the gate's | signals that never block |
| `docs.banned` | substring = reason | none | known-dead substrings, each with the reason the block names; the keys are data, so a typo in one is not caught |
| `authority.local` | paths | `[]` | repository documents that bind beyond the contract: cited, never restated |
| `overlays.implement` | paths | `[]` | checklists the builder reads before acting: a gate only CI runs, a write that corrupts state |
| `overlays.review` | paths | `[]` | design and style standards for a reviewer; the driver's REVIEW brief does not carry them |
| `overlays.land` | paths | `[]` | landing checklists and tail-append hazards; no ouro script reads it |
| `owner.role` | text | none | the role the contract calls the owner |
| `owner.ruling_approvers` | logins | required | whose comment closes a needs-ruling and converts an architecture issue; every trust decision reads this set beside the job token's bot: the rolling-issue ledgers, the shape gate's `**Triage**` provenance, the body edits that stop a run, a checkpoint finding's author, and the `**Stop:**`, `**Triage**` and `**Checkpoint finding**` comments `bin/loop-outcomes.py` counts (its Assisted count reads the set without the bot), so list every login that edits an agent-ready body or runs triage or run |

The eight states and two modifiers are the contract's and are not declared under `[labels]`;
`check` refuses a declared label that names one, or one both `area` and `type` hold, ignoring
case as GitHub does, and the installer creates the declared labels on the repository beside the
contract's, only the missing ones and never with `--force`, since they are the team's. A
`[rolling_issues]` title is matched exactly, never with `in:title`. Under `[docs]` an explicit
parameter on the gate's command line wins over what is declared, and an empty list means empty,
not the default; `docs.banned` carries a reason per entry because that signal blocks; reading
the table needs `ouro-binding.py` beside the gate and `python3` 3.11+ on PATH: without the tool
(a vendored tree) the gate says so and runs on its defaults, and a `python3` absent or below the
floor stops it with the floor message. An overlay adds a checklist to its step and never
replaces the protocol. `[models]` names an alias, never a pinned version, so a role follows the
current model.

### Choosing `ship.review`

Choose by what the reviewer can observe, not by its name. `adversarial-review` runs the driver's
REVIEW node (`bin/ouro/prompts/REVIEWER-PROMPT.md`), which reads the repository and executes probes
against what its brief names — the suite, a gate, a case the node sets. `external-audit` and
`copilot` read without running the suite: a repository-reading audit, run by the CLI
`ship.external_cli` names (`bin/external-audit.py`), and GitHub's review of the pull request, which gathers repository
context and may run configured static analysers. `ship.external_model` names the model the CLI
runs (`"default"`, the CLI's own default, when absent); a model the CLI refuses counts as a
round-1 failure, the same as an absent CLI. A reader and a prober are pointed at different
things and tend to find different defects, so the choice is a real one.

**The reader's contract**, whatever CLI runs it: read-only, editing nothing; it takes a goal and
a range, or a claim set; it answers with a JSON verdict that names the files it read; two rounds
at most, a third only for a new blocker, with the triage of round 1 given to round 2.

**Who reviews what**, by the value and the issue's `Size:` line (contract §4), is this table and
nowhere else: the driver's REVIEW step follows it. The table picks which reviewer a cell gets; the
review's weight within the cell follows the issue's `Flow:` class (contract §4): the reading brief
for express, sized with its budget for standard, the probe format for full.

| | `adversarial-review` | `copilot` | `external-audit` | `none` |
|---|---|---|---|---|
| **S** | the REVIEW node | Copilot on the PR | the reader | no reviewer |
| **M** | the REVIEW node | the REVIEW node, then Copilot on the PR | the REVIEW node | the REVIEW node |

**The availability rule.** An unavailable reader — the CLI `ship.external_cli` names is not
found, round 1 exits 1, round 1 exits 3 twice, or the run may not call it (a policy or network
rule forbids it) — falls back to the REVIEW node in every cell that names the reader, and the
record says so. An `S` re-derived as `M` after the commit (contract §4) takes the `M` row. Under
`stop-at-pr` the owner sees every PR whatever the value. The binding names one value, and nothing
requires it to name a second; the REVIEW node every `M` takes is the loop's own.

### The landing policy

`ship.landing` names the pair below it in one word: how `land` brings a branch up to date before
the PR, and how the PR lands. It is what `/ouro:init` asks at onboarding, and what the installer
writes into a new binding. The driver's landing scripts read `sync` and `merge` through the two
dotted reads below, and land the way they answer. A range with more than one author lands by
merge commit under either merge answer, since a squash would reassign their work.

| `landing` | `sync` | `merge` | the default branch's history |
|---|---|---|---|
| `merge-squash` (the default) | `merge` | `squash` | linear, but for a merge commit per PR with more than one author |
| `rebase-squash` | `rebase` | `squash` | linear, but for a merge commit per PR with more than one author |
| `rebase-merge` | `rebase` | `merge` | a merge commit per PR |
| `merge-merge` | `merge` | `merge` | a merge commit per PR |

`sync` and `merge` stay declarable on their own, and each still defaults to today's behaviour, so
a binding that declares none of the three keys lands as `merge-squash` — the loop's original
shape, and what every binding written before the preset keeps. Beside a `landing`, each explicit
key must **agree** with it: a disagreement is a `check` error naming both, since neither reading
wins over the other. A reader asks for the key it needs, and `get ship.sync` and `get ship.merge`
answer from the preset where the binding declares only that — those two keys, not a read of the
whole `[ship]` table, which answers what the file holds; where it declares nothing, both are
absent, which is the reader's cue to take today's behaviour.

### Model tiers

`[models]` names the model tier per role, as an alias — `opus`, `sonnet`, `haiku` or `fable` —
never a pinned version, so a role follows whatever the alias currently means. Every key is
optional; absent means the reading skill's own default, named in the schema above.

`builder` is the driver's BUILD, TRIAGE, CHECKPOINT and FOLLOWUP nodes, and its fix-round build;
`builder_trivial` is the BUILD node when the issue carries `trivial`. `reviewer` is the REVIEW
node, the adversarial pass an M always takes whatever `ship.review` names.

### The forbidden-token check

`bin/forbidden-tokens.py hook` matches the text of each publishing `gh` or `git` call a session
makes against the private token list (README, "The private token list") and blocks on a hit, and on
a missing list except where the key and the repository's visibility say below that it does not.
`ship.forbidden_check = "off"` turns that check off for this repository: the hook passes the call,
a hit on the list included. It is for a private repository whose own issues legitimately carry the
values a list would hold, where the check blocks every call on a host with no list and nothing
useful can go on one. `"required"` runs the check whatever the repository's visibility, and a
missing list blocks.

Absent, the check follows the visibility of the repository the call posts to. When a list is set
(`OURO_FORBIDDEN_TOKENS` set and non-empty) the check runs and a hit blocks, whatever the
visibility. When none is set, the hook reads the visibility with one `gh repo view` call:
`PRIVATE` or `INTERNAL` passes the call, while `PUBLIC`, a read that fails or runs past its time
bound, or any other value blocks as a missing list does. A private repository that wants the block
declares `"required"`.

The hook reads the key from the working directory's binding, and honours it only when that
binding's `repo.slug` is the repository the call posts to, compared case-insensitively, and
`ouro-binding.py check` passes that binding: with `-R` or `--repo` naming another repository, the
key counts as absent whatever its value. No binding is absent too. For a binding naming the
repository the call posts to, a failed `check` or any other value counts as `"required"`. A binding
read that fails counts as `"required"` whatever the binding names. The key is repository policy, so
the machine-local overlay below cannot set it. It does not reach `bin/forbidden-tokens.py`'s `scan`
gate, which stays fail-closed whatever a binding declares, since it guards the plugin's own text.
The hook's scan of the command text leaves out the value of a `-R` or `--repo` written outside
quotes: it addresses the call, it is not published.

## The machine-local overlay — `.claude/ouro.local.toml`

Optional, **gitignored**, beside the committed binding; every command merges it over
`ouro.toml` before validating, so `get repo.checkout` returns the merged value.

```toml
[repo]
checkout = "D:/src/name"                 # the only key a local file may set
```

**`repo.checkout` is the whole whitelist** — a machine may say where the repo lives, never what
the policy is. Any other table or key is a hard error, not a silent ignore. `check` warns (exit
code unchanged) when the *committed* binding carries `repo.checkout`: a per-machine path in
shared config, move it here or omit it. A checkout arriving only from the overlay is silent.

## What a consumer must gitignore

Two entries, beside whatever the repo already ignores: `.claude/ouro.local.toml` (the overlay
above) and `.claude/worktrees/`. The driver runs every issue in a worktree at
`.claude/worktrees/<N>-<slug>`, inside the repo; unignored, creating it leaves the tree dirty,
and the landing steps' clean-working-tree precondition then refuses the branch it just built.
ouro ships no `.gitignore` template; `<ouro>/bin/Install-Ouro.ps1` appends each entry that no
`.gitignore` in the work tree already ignores, and without it they are the repo's to add. It
asks git about `.claude/worktrees/` as an empty directory in a temp tree holding copies of the
repo's `.gitignore` files, so the question writes nothing into the work tree. Before it adds
that worktree, the driver creates its empty directory, runs
`git check-ignore -q --no-index .claude/worktrees/<N>-<slug>` on it, and when the path is not
ignored removes the directory, then stops with the line to
add — in that clone, by any exclude source; the `.gitignore` entry is what makes every clone
pass. The directory comes first because git applies a rule ending in a slash only to a path it
sees as a directory. The overlay entry is unchecked.

## Rules the validator enforces

- `schema`, `repo.slug`, `repo.default_branch`, `ship.policy`, `ship.review` and
  `owner.ruling_approvers` are required.
- Unknown tables or keys fail. A typo is a silent misconfiguration otherwise.
- `ship.review = "none"` forbids `ship.policy = "trivial-merge"` and `"merge-on-green"` — no
  independent reviewer, no unattended merge.
- `ship.unattended_classes` is allowed only under `ship.policy = "merge-on-green"` and holds only
  `express`, `standard` and `full`. Absent, the driver reads `["express", "standard"]`; the owner
  adds `full` by hand, and the driver never changes the list.
- `ship.review = "copilot"` requires `ship.copilot_bot_id`.
- `ship.review = "external-audit"` requires `ship.external_cli`, one of the adapters
  `bin/external-audit.py` ships (`grok`, `claude`, `codex`, `copilot`); `external_cli` is allowed
  under any `ship.review` value, for a manual run. `claude` runs in the same model family as the
  Claude Code session that invokes it, whatever model is named — a fresh context and this
  reader's contract, not a different family. `codex` refuses a model its account does not serve —
  the same round-1 failure as an absent CLI, but slower to surface, since a refusal can cost
  several reconnect retries first: name `ship.external_model` a model the account actually
  serves. `copilot` must be enabled by the organization's GitHub Copilot policy — a disabled
  account answers `Access denied by policy settings`, the same round-1 failure as an absent CLI —
  and it is a different thing from `ship.review = "copilot"`: this one is the Copilot **CLI**, a
  briefed repository reader, where `ship.review = "copilot"` names the Copilot PR-review bot,
  which takes no brief and runs only on an open PR. `ship.external_model` is optional and, when
  present, must be non-empty — absent, or `"default"`, means the CLI's own default model.
- Every `[models]` key is optional, and each must be one of `opus`, `sonnet`, `haiku`, `fable` —
  see "Model tiers".
- `ship.forbidden_check` is optional and, when present, must be `"required"` or `"off"`; any
  other value or type is refused. Absent follows the target repository's visibility, as "The
  forbidden-token check" says. Like every key but `repo.checkout`, the overlay may not set it.
- `ship.landing` must name one of the four pairs above, and an explicit `ship.sync` or
  `ship.merge` beside it must agree with the pair it names — the error names both keys. All three
  are optional; absent means today's behaviour, which is what `merge-squash` names.
- `ship.direct` is optional: a list of globs, empty when absent, and the direct lane (contract §4,
  `Flow: direct`) is off while it is empty. A glob is matched with `fnmatch.fnmatchcase` against the
  repository-relative path written with forward slashes, so `*` also crosses `/`; there is no
  negation, so a new file under a directory joins the lane by a binding edit. `check` refuses a
  glob whose literal prefix, the text before its first `*`, `?` or `[`, starts with '.github/',
  'actions/', 'hooks/', 'release/' or '.claude/' (the prefix rule: a literal path there is
  refused too), a glob with a wildcard or a class in its first path segment (`*.md` or `[.]github/…`
  reaches every top-level directory, the protected ones included: name the directory, as
  `docs/*.md`), and a glob that matches any of these probes: '.github/workflows/x.yml',
  '.github/workflows/x.yaml', '.github/actions/x/action.yml', '.github/actions/x/action.yaml',
  'actions/x/action.yml', 'hooks/hooks.json', 'hooks/x.py', 'hooks/x.sh', 'release/x.py',
  'release/x.ps1', '.claude/ouro.toml', '.claude/ouro.local.toml', '.claude/settings.json', or a
  repository path a `[[gate]]` `run` or `ci` line names (a token not starting with `-` that holds
  a slash and does not end in one, or that ends in a file extension, `<ouro>/` stripped). The
  error names the glob and the rule or the probe it matched.
  `python3 bin/ouro-binding.py direct <path>...` reads `ship.direct` and prints each given path
  that no glob matches, one per line, by the same `fnmatch.fnmatchcase` reading: exit 1 when it
  printed any, 0 when every path matches, 2 with a message on stderr when the key is empty or
  absent; `Push-Direct.ps1` asks it. Like every key but `repo.checkout`, the overlay may not set it.
- Every `[[gate]]` needs a non-empty `areas` and `run`. An issue carrying an area label that matches
  no gate, with no `"*"` gate, is not executable; `execute` stops and says so. A `run` may contain
  `<ouro>`, the plugin root, replaced before the command runs — that is how a gate the plugin ships
  is named, since its path differs per machine and per version. The root is inserted as it is, so
  where it may hold a space, quote the token in the `run` string. A copy of the binding tool that
  is not inside an ouro plugin has no root to insert — a vendored one in the consumer's scripts
  directory, or one under a consumer's own plugin, which carries another name — and there `check`
  fails such a `run`, naming the directory the copy sits in. A `run` is one command line: one
  holding a line break is refused, since `gates` prints one run per line.
- A `[[gate]]` may also declare `ci`, optional: the command the CI gate loop runs, as a pwsh
  command line, in place of `run` when a gate declares one — `run` stays what a local session,
  `execute`, `land`, `land-batch` and `gates` read. It is checked the same way as `run`: non-empty,
  one command line, and `<ouro>` refused where the copy has no plugin root. Declare it for a gate
  whose `run` wraps the real command for the dev box, like `wsl … bash -lc`, which a CI runner
  cannot run as written, or for a gate that another workflow already runs in CI, with a `ci` that
  only prints a notice saying so.
- A `[[gate]]` may also declare `paths`, optional: a non-empty list of non-empty strings. `check`
  refuses a value that is not a list, an empty list and an empty or non-string entry, naming the
  gate. Each entry is a git pathspec, matched against the repository-relative paths a pull request
  or a push changes, the way `git diff --name-only` matches its pathspec arguments when run from the
  repository root: git sees each entry as written, magic such as `:(glob)` included. Without magic,
  a wildcard such as `*.md` matches at every level. A gate with no `paths` always runs. A gate
  with `paths` runs only when the pull request changes a path that one of its entries matches, and
  is skipped otherwise. A changed path that no gate's `paths` claims selects no path-scoped gate:
  with only such paths changed, only the gates without `paths` run. The gates Action reads
  `paths` when its selection input is on, and `execute`, `land` and `fuse` always read them, to
  narrow their area-selected set with the same fallbacks; `gates` and `land-batch` never read
  them. With selection on, a pull request's diff is read against its base and a push's against
  the tip the branch had before it, and every gate runs in these cases: an event that is neither;
  a push with no previous tip, such as a tag or a new branch; a diff that cannot be read; and a
  change to the gates Action. A gate that reads the binding names `.claude/ouro.toml`
  in its `paths`. A path-scoped gate whose list misses a file it depends on is skipped wrongly
  when only that file changes, so a list names everything its gate reads.
  A gate whose CI leg is path-filtered declares as its `paths` pathspecs that select the same paths
  as that filter, and every file the gate reads: without them, `execute`, `land` and `fuse` run it
  locally whenever it is selected and the diff misses the filter.
- `owner.ruling_approvers` is non-empty.
- No `[labels]` set names one of the eight states or the two modifiers — the contract fixes those
  — and `area` and `type` are disjoint. Both are compared case-insensitively, because GitHub label
  names are.
- Every `[docs]` key is optional, and so is the table. `docs.banned` is a **free-key sub-table**:
  its keys are data (substrings to match), so an unknown one is not an error, but every value
  must be a string reason, and an empty key is rejected — it would match every line of every doc.
  A typo in a `[docs]` key, or in the sub-table's own name, still fails. Because the keys may
  contain dots, `get docs.banned` fetches the whole map; a single free key is not addressable.
- `index_exempt`, `planning_paths` and `generated_pattern` must **compile as regexes**.
  `check` compiles each with Python's `re`, but the
  gate matches with .NET, so `check` catches only what Python refuses: Python's named group
  `(?P<n>x)` and possessive `x++` pass `check` and throw in the gate, and .NET's `(?<n>x)` is
  refused by `check` though the gate accepts it. Keep a pattern in the syntax both engines accept.
  All are matched case-insensitively.
- `report_only` names signals without regard to case: the index checks are `S4a` and `S4b`, and
  `S4` matches neither. The full set is `S1 S2 S3 S4a S4b S7 S8 S9 S10`. `check` refuses a
  non-list or a non-string entry but not an unknown name; a name outside the set silently
  matches nothing.
- **`index_exempt` and `planning_paths` are regexes; `exclude` and `suppress_prefixes` are not.**
  The two regex lists are matched against the repo-relative path with `-match`; `exclude` is a
  substring test against `"/<relpath>"` and `suppress_prefixes` is a `StartsWith`. Writing a
  regex where a prefix is expected silently matches nothing.

## Rules the skills apply

- **Ship policy is the minimum of repo and issue.** A `trivial` label cannot exceed
  `ship.policy`; `stop-at-pr` in the binding means `trivial` is advisory and the run stops at the
  PR.
- **A gate is the narrowest command that can fail for the diff.** CI runs the matching
  `[[gate]]` entries as bound and the driver runs locally only those the checks skip, and a
  gate that repeats a PR's required check
  (`gh pr checks <PR> --required`) is a binding smell: its `run` should name the narrow,
  already-scoped check and leave the full suite to CI.
- **Protected prefixes are absolute.** The driver refuses to land them and never cuts a branch
  from one.
- **Overlays are read, not obeyed as protocol.** They add repo checklists to a named step; a
  binding cannot remove, reorder, or replace a step.
- **Nothing in a skill names a repo, a path, a bot id, a workflow, or a script.** If it does,
  that is a defect in the skill — file it against `ouro`.
- **`ship.test_first_for_bugs` is accepted and read by nothing.** The schema keeps the key valid
  so an existing binding does not fail `check`; the BUILD node's test-first rule runs
  unconditionally for a bug whose code has a test host.
- **The plugin targets github.com.** The binding has no host key, and no gate or template names
  a host to `gh`; a skill names one only where `/ouro:init` has the operator add `--hostname` to
  its bot-id lookup off github.com. A consumer on another GitHub host sets `GH_HOST` in the
  environment its sessions and workflows run in: gh uses it for every command that names no host,
  so the skills' `-R <slug>` and `gh api` calls, and the gates' `GH_REPO` set from the slug, follow
  it without an edit. A clone of that host's repository does not stand in for it: `-R` and
  `gh api` never read the remote's host. gh authenticates with a token from the environment
  variables `gh help environment` names for that kind of host, or with the host's stored
  credentials where none is set. A host key in the binding is absent by decision, not oversight;
  what would add one is a consumer that actually runs on such a host.
- **A session's host must reach GitHub's GraphQL API.** The skills' tracker calls (`gh issue`,
  `gh pr`, `gh api graphql`) and the scripts they run all use it. A Claude Code cloud session's
  proxy serves GitHub's REST API and refuses GraphQL, so the driver's first tracker read
  (`gh issue view`) fails there and the run ends as a driver error (exit 1).
