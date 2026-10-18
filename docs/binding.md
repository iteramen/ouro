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
paths = ["Sdk/"]                         # optional — git pathspecs; `execute`, `land` and `fuse`, and CI with selection on, run this gate only when a diff changes one

[[gate]]
areas = ["*"]
run = "pwsh -File scripts/Test-Invariants.ps1 -BaseRef origin/main"

# [[gate]]                               # a gate the plugin ships, named through the plugin root
# areas = ["*"]
# run = 'pwsh -File <ouro>/bin/Test-DocsFreshness.ps1'

[ship]
policy = "stop-at-pr"                    # stop-at-pr | trivial-merge | merge-on-green   (effective policy = min(repo, issue))
unattended_classes = ["express", "standard"]   # optional, only under merge-on-green; the Flow classes the driver may merge
                                         # unattended — express, standard, full (default: express, standard)
review = "copilot"                       # copilot | adversarial-review | external-audit | none — see "Choosing ship.review"
copilot_bot_id = "BOT_kgDOCnlnWA"        # required when review = "copilot"; that id is github.com-wide, not this repo's
external_cli = "grok"                    # required when review = "external-audit"; the adapter bin/external-audit.py runs — grok, claude, codex or copilot today
external_model = "grok-4-fast"           # optional; "default" (the CLI's own default model) when absent
landing = "merge-squash"                 # sync and merge in one word — see "The landing policy" (default: merge-squash)
sync = "merge"                           # merge | rebase — how `land` brings the branch up to date (default: merge)
merge = "squash"                         # squash | merge — how the PR lands (default: squash)
forbidden_check = "required"             # required | off — whether the manifest applier checks what it
                                         # posts against the private token list — see "The
                                         # forbidden-token check" (default: follows the target's
                                         # visibility)
direct = ["docs/notes/*.md"]             # optional — globs of the files the direct lane may
                                         # push; off when absent — see "Rules the validator
                                         # enforces"

[models]                                 # every key optional — the model tier per role, an alias
                                         # never a pinned version, so a role follows the current
                                         # model. Absent means the reading skill's own default.
builder = "sonnet"                       # opus | sonnet | haiku | fable (default: sonnet)
builder_trivial = "sonnet"               # the `trivial`-labelled M build (default: sonnet)
reviewer = "opus"                        # the driver's REVIEW node (default: opus)
verifier = "opus"                        # a dispatch of agents/verifier.md (default: opus)

[rolling_issues]                         # exact titles; selected with an exact-title match, never `in:title`
drift_audit = "Docs drift audit"
loop_runs = "Loop runs"                  # the loop metrics: Get-LoopMetrics.ps1 and loop-outcomes.py each
                                         # append one comment per run
docs_freshness = "Docs freshness"        # the deterministic docs sweep's rolling issue
comments_freshness = "Comments freshness" # the rolling issue of a comments-freshness sweep the repo
                                         # runs itself; no ouro script reads it

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
                                         # convert an architecture issue. Every trust decision reads
                                         # this set beside the job token's bot: the rolling-issue
                                         # ledgers and the intake and blocked-check markers, the
                                         # shape gate's **Triage** provenance check, the issue-body
                                         # edits that stop an execute run, and bin/loop-outcomes.py,
                                         # which counts a **Stop:**, **Triage** or **Checkpoint
                                         # finding** comment only from them, and whose Assisted
                                         # count reads the set without the bot. List every login
                                         # that edits an agent-ready body or runs /ouro:triage
                                         # or /ouro:run: a checkpoint finding counts
                                         # as delivered only from these logins (and the bot's), and
                                         # a finding from another login repeats the checkpoint run
```

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

`builder` is the driver's BUILD, TRIAGE and FOLLOW-UP nodes, and its fix-round build;
`builder_trivial` is the BUILD node when the issue carries `trivial`. `reviewer` is the REVIEW
node, the adversarial pass an M always takes whatever `ship.review` names. `verifier` is the
model of a dispatch of `agents/verifier.md`, whose own front matter (`model: opus`) is only the
fallback for a call that passes none.

### The forbidden-token check

`bin/apply-manifest.py` matches every body file and `create` title it would post against the
private token list (README, "The private token list") and refuses on a hit, and on a missing
list except where the key and the repository's visibility say below that it does not.
`ship.forbidden_check = "off"` turns that check off for this repository: the applier skips the
check and the matcher lookup, and prints one line saying the binding turned it off, as it does for
`--no-forbidden-check`. It is for a private repository whose own issues legitimately carry the
values a list would hold, where the check refuses every apply on a host with no list and nothing
useful can go on one. `"required"` runs the check whatever the repository's visibility, and a
missing list refuses.

Absent, the check follows the visibility of the repository the run posts to. When a list is set
(`OURO_FORBIDDEN_TOKENS` set and non-empty) the check runs and a hit refuses, whatever the
visibility. When none is set, the applier reads the visibility with one `gh repo view` call, in a
dry run too: `PRIVATE` or `INTERNAL` prints `forbidden-token check skipped: no list, private
repository` and goes on, while `PUBLIC`, a read that fails or runs past its time bound, or any other
value refuses as a missing list does. A private repository that wants the refusal declares
`"required"`, which holds for runs from its own checkout.

The applier reads the key from the working directory's binding, and honours it only when that
binding's `repo.slug` is the repository the run posts to, compared case-insensitively, and
`ouro-binding.py check` passes that binding: run from that checkout with `--repo` naming another,
the key counts as absent whatever its value, and an `"off"` binding says it does not turn the check
off there. No binding is absent too. For a binding naming the repository the run posts to, a
failed `check` or any other value counts as `"required"`. A binding read that fails counts as
`"required"` whatever the binding names. The key is repository policy, so the machine-local overlay
below cannot set it. It does not reach `bin/forbidden-tokens.py`'s `scan` gate, which stays
fail-closed whatever a binding declares, since it guards the plugin's own text; the `hook` mode
follows this same rule, read from the binding at the session's working directory and the
repository its `repo.slug` names: `"off"` passes a hit too, a call naming another repository
with `-R` reads as if the key were absent, no key follows the visibility only with no list, and a
binding that fails `check` blocks as `"required"` does. The hook's scan of the command text leaves
out the value of a `-R` or `--repo` written outside quotes: it addresses the call, it is not
published.

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
- **A gate is the narrowest command that can fail for the diff.** The driver runs the matching
  `[[gate]]` entries as bound, and a gate that repeats a PR's required check
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
