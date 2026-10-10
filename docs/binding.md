# The binding — `.claude/ouro.toml`

Every `ouro` skill starts by reading `.claude/ouro.toml` at the repo root. It is the only
place repo-specific facts live: the contract states the protocol and the skills implement it;
the binding carries the bindings. **No binding, or a binding that fails validation, means the skill refuses to run** —
the same honesty as `execute` refusing an issue without `agent-ready`. `/ouro:init` is the
exception: with no binding it runs the installer, which writes one; a binding that fails
validation still makes it refuse.

Three consumers read it — a person, the coding agent, and a deterministic supervisor — so it is
typed and validated, not prose. `bin/ouro-binding.py check` (Python 3.11+, stdlib only) fails
closed on an unknown key. Comments are welcome; they are for the people. In a repo that has a
binding, the deterministic gates that read it need `python3` 3.11+ on PATH — a Windows dev box
needs a `python3` there too; the Store's `python3` alias and an msys2 `python3` both count; in a
repo with none, `bin/Test-AgentReadyShape.ps1` and `bin/Get-RollingIssue.ps1` (and the gates that
post through it) skip the read with an INFO line, and those and `bin/Test-AgentReadyAnchors.ps1`
address the repository `origin` names in place of `[repo].slug`.

## Schema (v1)

```toml
schema = 1

[repo]
slug = "owner/name"                      # required — the GitHub repo; never embed it in a skill
default_branch = "master"                # required
protected_branch_prefixes = ["release/"] # never squash-landed, pushed to, or branched from by an agent
                                         # — nor removed by the branch sweep: a long-lived branch a
                                         # merged PR once had as its head reads as finished, so list
                                         # it here. The sweep's report mode lists everything first:
                                         # read it before any -Delete
checkout = "C:/src/name"                 # optional — canonical local path; skills use cwd when absent
                                         # per-machine: belongs in ouro.local.toml (below), not here

[labels]
# The eight states (agent-ready, human-ready, needs-ruling, blocked, needs-triage, idea, umbrella,
# architecture) and the two modifiers (trivial, checkpoint) are fixed by the contract and are NOT
# declared here.
scope = ["compiler"]                     # scope/effort labels that may ride on any state
area = ["app", "sdk"]                    # area labels; a gate selects on these
type = ["bug", "enhancement", "documentation"]   # type labels the repo's policy requires on every issue
# The shape gate requires at least one area label and exactly one type label on every agent-ready
# issue; an issue with several area labels runs every gate any of them selects, less those whose
# `paths` its diff misses. An empty or undeclared set skips that check. area and type must be
# disjoint. `check` enforces that rule and the states-and-modifiers rule at the top of this table,
# ignoring case as GitHub label names do.
# The installer creates these on the repository too, alongside the contract's ten: only the ones
# missing there, and never with --force, since they are the team's, not the contract's.

[[gate]]                                 # one entry per verify command; areas = ["*"] matches every issue
areas = ["sdk"]
run = "wsl -e bash -lc 'dotnet test Sdk/Sdk.sln -c Debug'" # dev-box only: the CI runner has no wsl
ci = "dotnet test Sdk/Sdk.sln -c Debug"  # optional — what CI runs in place of `run`, as a pwsh command line
paths = ["Sdk/"]                         # optional — git pathspecs; `execute` and `land`, and CI with selection on, run this gate only when a diff changes one

[[gate]]
areas = ["*"]
run = "pwsh -File scripts/Test-Invariants.ps1 -BaseRef origin/main"

# [[gate]]                               # a gate the plugin ships, named through the plugin root
# areas = ["*"]
# run = 'pwsh -File <ouro>/bin/Test-DocsFreshness.ps1'

[ship]
policy = "stop-at-pr"                    # stop-at-pr | trivial-merge   (effective policy = min(repo, issue))
review = "copilot"                       # copilot | adversarial-review | external-audit | none — see "Choosing ship.review"
copilot_bot_id = "BOT_kgDOCnlnWA"        # required when review = "copilot"; that id is github.com-wide, not this repo's
external_cli = "grok"                    # required when review = "external-audit"; the adapter bin/external-audit.py runs — grok, claude, codex or copilot today
external_model = "grok-4-fast"           # optional; "default" (the CLI's own default model) when absent
landing = "merge-squash"                 # sync and merge in one word — see "The landing policy" (default: merge-squash)
sync = "merge"                           # merge | rebase — how `land` brings the branch up to date (default: merge)
merge = "squash"                         # squash | merge — how the PR lands (default: squash)
forbidden_check = "required"             # required | off — whether the manifest applier checks what it
                                         # posts against the private token list — see "The
                                         # forbidden-token check" (default: required)

[models]                                 # every key optional — the model tier per role, an alias
                                         # never a pinned version, so a role follows the current
                                         # model. Absent means the reading skill's own default.
builder = "sonnet"                       # opus | sonnet | haiku | fable (default: sonnet)
builder_trivial = "sonnet"               # the `trivial`-labelled M build (default: sonnet)
reviewer = "opus"                        # /ouro:review's dispatch (default: opus)
verifier = "opus"                        # /ouro:drift's per-group verifier dispatch (default: opus)
weekly = "sonnet"                        # the weekly-pass template's two `claude -p` sessions —
                                         # see "Model tiers" (default: sonnet)

[rolling_issues]                         # exact titles; selected with an exact-title match, never `in:title`
drift_audit = "Docs drift audit"
loop_runs = "Loop runs"                  # the loop metrics: Get-LoopMetrics.ps1 and loop-outcomes.py each
                                         # append one comment per run
docs_freshness = "Docs freshness"        # the deterministic docs sweep's rolling issue
comments_freshness = "Comments freshness" # the comments-freshness sweep's rolling issue

[docs]                                   # every key optional; an absent table means the docs
                                         # gate runs on its own defaults, which name no repo.
                                         # The gate READS these; an explicit parameter on the
                                         # command line still wins over what is declared here.
                                         # An empty list means EMPTY, not "use the default":
                                         # `exclude = []` scans node_modules. Reading needs
                                         # ouro-binding.py beside the gate and python3 on PATH;
                                         # without the tool (a vendored tree) the gate says so
                                         # and runs on its defaults. No python3 on PATH is not
                                         # a fallback for a repo that has a binding: the gate
                                         # stops naming it, as it stops for a python3 older
                                         # than 3.11 with the floor message ouro-binding.py
                                         # prints.
extensions        = [".md", ".rs"]       # what makes a code-span token path-shaped
exclude           = ["/vendor/"]         # scope exclusions, matched as substrings of "/<relpath>"
suppress_prefixes = ["sibling/"]         # references never reported (another repo, build output)
generated_pattern = "api/generated/"     # REGEX for a generated doc tree to skip
html_globs        = ["site/*.html"]      # HTML pages whose relative hrefs and <code> paths resolve;
                                         # exclude still applies, and its default covers /_site/
index_path        = "docs/index.md"      # the doc index; with indexed_trees, arms both index checks
indexed_trees     = ["src/"]             # trees whose docs must be indexed; with index_path, arms both
index_exempt      = ['(^|/)CHANGELOG']   # REGEXES exempting a path from the coverage check
planning_paths    = ['^docs/policy/']    # REGEXES for docs where work-remaining content is allowed
report_only       = ["S9"]               # signals that never block

[docs.banned]                            # known-dead substring -> reason. The KEYS are data, so a
                                         # typo in one cannot be detected; the reason is required
                                         # because this signal blocks.
"dead.example.com/old" = "renamed; Pages URLs do not follow renames"

[authority]
local = ["docs/policies.md"]             # repo docs that bind beyond the contract; cited, not restated

[overlays]                               # checklists READ at the named step — never protocol replacements
implement = [".claude/rules/localization.md"]  # what the builder must know while acting:
                                         # a gate only CI runs, a write that corrupts state
review = ["docs/architecture-rules.md", "docs/coding-standards.md"]
                                         # design and style standards, which the reviewer reads
land = []                                # tail-append hazards, landing checklists
drift = ["docs/incidents/**"]            # point-in-time paths the drift audit skips (path prefixes; `/**` is decoration)

[owner]
role = "loop owner"                      # the role the contract calls "the owner"
ruling_approvers = ["BoJl4apa"]          # logins whose comment closes a needs-ruling, and who
                                         # convert an architecture issue; only these logins' (and
                                         # the job token's bot's) **Triage** comments are
                                         # provenance, and bin/loop-outcomes.py counts a **Stop:**,
                                         # **Triage** or **Checkpoint finding** comment only from
                                         # them, so list every login that runs /ouro:triage,
                                         # /ouro:execute or /ouro:fuse: a checkpoint finding counts
                                         # as delivered only from these logins, and a finding from
                                         # another login repeats the checkpoint run
```

### Choosing `ship.review`

Choose by what the reviewer can observe, not by its name. `adversarial-review` dispatches
`/ouro:review`, which reads the repository and executes probes against what its brief names — the
suite, a gate, a case the session sets. `external-audit` and `copilot` read without running the
suite: a repository-reading audit, run by the CLI `ship.external_cli` names (`/ouro:external-audit`,
`bin/external-audit.py`), and GitHub's review of the pull request, which gathers repository
context and may run configured static analysers. `ship.external_model` names the model the CLI
runs (`"default"`, the CLI's own default, when absent); a model the CLI refuses counts as a
round-1 failure, the same as an absent CLI. A reader and a prober are pointed at different
things and tend to find different defects, so the choice is a real one.

**The reader's contract**, whatever CLI runs it: read-only, editing nothing; it takes a goal and
a range, or a claim set; it answers with a JSON verdict that names the files it read; two rounds
at most, a third only for a new blocker, with the triage of round 1 given to round 2.

What the value decides, by the issue's size (its `Size:` line, contract §4). Under
`external-audit`, this applies to every S, M and fused deliverable alike: an unavailable reader —
the CLI `ship.external_cli` names is not found, round 1 exits 1, round 1 exits 3 twice,
or the run may not call it (a policy or network rule forbids it) — falls back to `/ouro:review`
in its place, and the record says so.

- **An S** not built by `/ouro:fuse` — under `adversarial-review` it takes `/ouro:review`;
  under `copilot` or `external-audit` it takes the named reader; under `none` it takes no
  reviewer.
- **An M** — every M not built by `/ouro:fuse` takes `/ouro:review`, whatever is named; under
  `copilot` or `external-audit` it takes the named reader as well, under `adversarial-review` that
  pass alone.
- **An S or M built by `/ouro:fuse`** — reviewed per file cluster, by the reader `/ouro:fuse` step 5
  names: under `external-audit`, that reader takes each cluster in place of `/ouro:review`; under
  any other value, `/ouro:review` takes each cluster, and under `copilot` the named reader also
  reads the PR.

Under `stop-at-pr` the owner sees every PR whatever the value. The binding names one value, and
nothing requires it to name a second; the `/ouro:review` every M not fused takes is the loop's own.

### The landing policy

`ship.landing` names the pair below it in one word: how `land` brings a branch up to date before
the PR, and how the PR lands. It is what `/ouro:init` asks at onboarding, and what the installer
writes into a new binding. `/ouro:land` reads `sync` and `merge` at its step 0, through the two
dotted reads below, and lands the way they answer. A range with more than one author lands by
merge commit under either merge answer, since a squash would reassign their work.
`/ouro:land-batch` reads `merge`, lands squash commits only, and refuses a batch on a binding
that lands by merge.

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

`builder` and `builder_trivial` are `/ouro:execute`'s M build dispatch, read at its step 3 —
`builder` for the dispatch as such, `builder_trivial` when the issue carries `trivial`.
`/ouro:fuse` also reads `builder`, for its builders and its fix-round builder.
`reviewer` is `/ouro:review`'s own dispatch, at its step 2 — the adversarial pass an M not built
by `/ouro:fuse` always takes, whatever `ship.review` names. `verifier` is `/ouro:drift`'s
per-group verifier dispatch, passed on every call at its step 4; `agents/verifier.md`'s own
front matter (`model: opus`) is only the fallback for a call the skill passes none. `--deep`
overrides it, on every call, with the session's own top tier.

`weekly` is `templates/weekly-pass.yml`'s two `claude -p` sessions, the drift audit and the intake
grading: each step reads it with `ouro-binding.py get models.weekly`, as the template's other steps
read their keys. The smoke check keeps `--model haiku`: it tests reachability, not work.

### The forbidden-token check

`bin/apply-manifest.py` matches every body file and `create` title it would post against the
private token list (README, "The private token list") and refuses on a hit or a missing list.
`ship.forbidden_check = "off"` turns that check off for this repository: the applier skips the
check and the matcher lookup, and prints one line saying the binding turned it off, as it does for
`--no-forbidden-check`. It is for a private repository whose own issues legitimately carry the
values a list would hold, where the check refuses every apply on a host with no list and nothing
useful can go on one. Absent, or `"required"`, the check runs.

The applier reads the key from the working directory's binding, and honours `"off"` only when that
binding's `repo.slug` is the repository the run posts to, compared case-insensitively, and
`ouro-binding.py check` passes that binding: run from that checkout with `--repo` naming another,
the check runs, and a line says so. No binding, a binding that fails `check`, a read that fails, or
any other value leaves the check on. The key is repository policy, so the machine-local overlay
below cannot set it. It does not reach `bin/forbidden-tokens.py`: its `scan`
gate and its `hook` mode stay fail-closed whatever a binding declares, since they guard the
plugin's own text and the commands a session runs.

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
above) and `.claude/worktrees/`. `/ouro:execute` runs every issue in a worktree at
`.claude/worktrees/<N>-<slug>`, inside the repo; unignored, creating it leaves the tree dirty,
and `/ouro:land`'s clean-working-tree precondition then refuses the branch execute just built.
ouro ships no `.gitignore` template; `<ouro>/bin/Install-Ouro.ps1` appends each entry that no
`.gitignore` in the work tree already ignores, and without it they are the repo's to add. It
asks git about `.claude/worktrees/` as an empty directory in a temp tree holding copies of the
repo's `.gitignore` files, so the question writes nothing into the work tree. Before it adds
that worktree, `/ouro:execute` creates its empty directory, runs
`git check-ignore -q --no-index .claude/worktrees/<N>-<slug>` on it, and when the path is not
ignored removes the directory and each parent that leaves empty, then stops with the line to
add — in that clone, by any exclude source; the `.gitignore` entry is what makes every clone
pass. The directory comes first because git applies a rule ending in a slash only to a path it
sees as a directory. The overlay entry is unchecked.

## Rules the validator enforces

- `schema`, `repo.slug`, `repo.default_branch`, `ship.policy`, `ship.review` are required.
- Unknown tables or keys fail. A typo is a silent misconfiguration otherwise.
- `ship.review = "none"` forbids `ship.policy = "trivial-merge"` — no reviewer beyond an M's
  `/ouro:review`, no unattended merge.
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
  other value or type is refused. Like every key but `repo.checkout`, the overlay may not set it.
- `ship.landing` must name one of the four pairs above, and an explicit `ship.sync` or
  `ship.merge` beside it must agree with the pair it names — the error names both keys. All three
  are optional; absent means today's behaviour, which is what `merge-squash` names.
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
  cannot run as written.
- A `[[gate]]` may also declare `paths`, optional: a non-empty list of non-empty strings. `check`
  refuses a value that is not a list, an empty list and an empty or non-string entry, naming the
  gate. Each entry is a git pathspec, matched against the repository-relative paths a pull request
  changes, the way `git diff --name-only` matches its pathspec arguments when run from the
  repository root: git sees each entry as written, magic such as `:(glob)` included. Without magic,
  a wildcard such as `*.md` matches at every level. A gate with no `paths` always runs. A gate
  with `paths` runs only when the pull request changes a path that one of its entries matches, and
  is skipped otherwise. A changed path that no gate's `paths` claims selects no path-scoped gate:
  with only such paths changed, only the gates without `paths` run. The gates Action reads
  `paths` when its selection input is on, and `execute` and `land` always read them, to narrow
  their area-selected set with the same fallbacks; `gates`, `land-batch` and `fuse` never read
  them. With selection on, every gate runs in these cases: an event other than a pull request; a
  diff that cannot be read; and a change to `.claude/ouro.toml`, a workflow or the gates Action.
  A path-scoped gate whose list misses a file it depends on is skipped wrongly when only that file
  changes, so a list names everything its gate reads.
- `owner.ruling_approvers` is non-empty.
- No `[labels]` set names one of the eight states or the two modifiers — the contract fixes those
  — and `area` and `type` are disjoint. Both are compared case-insensitively, because GitHub label
  names are.
- Every `[docs]` key is optional, and so is the table. `docs.banned` is a **free-key sub-table**:
  its keys are data (substrings to match), so an unknown one is not an error, but every value
  must be a string reason, and an empty key is rejected — it would match every line of every doc.
  A typo in a `[docs]` key, or in the sub-table's own name, still fails. Because the keys may
  contain dots, `get docs.banned` fetches the whole map; a single free key is not addressable.
- `index_exempt`, `planning_paths` and `generated_pattern` must **compile as regexes**; an
  invalid pattern is rejected here rather than thrown by the gate mid-run. All are matched
  case-insensitively.
- `report_only` names signals **exactly**: the index checks are `S4a` and `S4b`, and `S4` matches
  neither. The full set is `S1 S2 S3 S4a S4b S7 S8 S9 S10`.
- **`index_exempt` and `planning_paths` are regexes; `exclude` and `suppress_prefixes` are not.**
  The two regex lists are matched against the repo-relative path with `-match`; `exclude` is a
  substring test against `"/<relpath>"` and `suppress_prefixes` is a `StartsWith`. Writing a
  regex where a prefix is expected silently matches nothing.

## Rules the skills apply

- **Ship policy is the minimum of repo and issue.** A `trivial` label cannot exceed
  `ship.policy`; `stop-at-pr` in the binding means `trivial` is advisory and the run stops at the
  PR.
- **A gate is the narrowest command that can fail for the diff, in a repository that never
  batch-lands.** `/ouro:land-batch` runs every `[[gate]]` as its only check on the composed tree,
  so a repository that batches keeps its full suite in a gate and pays for that run on each PR.
  Elsewhere, `execute` runs every matching `[[gate]]` as bound, and names one that repeats a PR's
  required check (`gh pr checks <PR> --required`) as a binding smell: its `run` should name the
  narrow, already-scoped check and leave the full suite to CI.
- **Protected prefixes are absolute.** `land` and `land-batch` refuse them; `execute` never cuts
  a branch from one; the supervisor skips any issue whose open PR targets one.
- **Overlays are read, not obeyed as protocol.** They add repo checklists to a named step; a
  binding cannot remove, reorder, or replace a step.
- **Nothing in a skill names a repo, a path, a bot id, a workflow, or a script.** If it does,
  that is a defect in the skill — file it against `ouro`.
- **`ship.test_first_for_bugs` is accepted and read by nothing.** The schema keeps the key valid
  so an existing binding does not fail `check`; `execute`'s test-first bullet runs unconditionally
  for a bug whose code has a test host.
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
