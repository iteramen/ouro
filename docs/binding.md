# The binding — `.claude/ouro.toml`

Every `ouro` skill starts by reading `.claude/ouro.toml` at the repo root. It is the only
place repo-specific facts live: the contract states the protocol and the skills implement it;
the binding carries the bindings. **No binding, or a binding that fails validation, means the skill refuses to run** —
the same honesty as `/ouro:run` refusing an issue without `agent-ready`. `/ouro:init` is the
exception: with no binding it runs the installer, which writes one; a binding that fails
validation still makes it refuse.

Three consumers read it — a person, the coding agent, and the deterministic gates — so it is
typed and validated, not prose. `bin/ouro-binding.py check` (Python 3.12+, stdlib only) fails
closed on an unknown key. Comments are welcome; they are for the people. In a repo that has a
binding, the deterministic gates that read it need `python3` 3.12+ on PATH — a Windows dev box
needs a `python3` there too; the Store's `python3` alias and an msys2 `python3` both count; in a
repo with none, `bin/Test-AgentReadyShape.ps1` and `bin/Get-RollingIssue.ps1` (and the gates that
post through it) skip the read with an INFO line, and those and `bin/Test-AgentReadyAnchors.ps1`
address the repository `origin` names in place of `[repo].slug`.

## Schema (v1)

`Vendor-Ouro.ps1` keeps the consumer's binding and copies the flat fixture suites except
`tests/driver/` and `tests/runtime/`, which require the plugin layout. Remove any flat-layout
gate entries naming those two suites; old copied files are retained, with removal optional.
Plugin installs and public plugin exports still include both roots.

One row per key the validator accepts, spelled dotted; `[[gate]]` is a list with one entry per
verify command, and `[docs.banned]` a table whose keys are data. Required means `check` refuses
the binding without it. `templates/ouro.toml.example` is the copy-paste example.

| Key | Values | Default | What it changes |
|---|---|---|---|
| `schema` | `1` | required | the schema version this document describes |
| `repo.slug` | `"owner/name"` | required | the GitHub repository every `gh` call names; never embedded in a skill |
| `repo.default_branch` | a branch name | required | the branch runs cut from, land on and watch |
| `repo.protected_branch_prefixes` | branch-name prefixes | `[]` | branches an agent never squash-lands, pushes to, branches from or sweeps; list a long-lived branch a merged PR once had as its head, which the sweep otherwise reads as finished, and read the sweep's report mode before any `-Delete` |
| `repo.branch_prefix` | text | none | the prefix of every task branch the driver cuts (`<prefix>/<N>-<slug>`), where the repository's branch policy requires one; absent, it is the first area label, then the first type label, then `ouro`; `check` refuses it empty, holding a slash, whitespace or `..`, or equal to or starting with a `protected_branch_prefixes` entry |
| `repo.checkout` | an absolute path | none | the canonical local checkout, which no ouro script reads (the driver finds the main checkout through git); per-machine, so it belongs in the overlay below, and `check` warns when the committed binding carries it |
| `repo.setup` | a command line | none | run once in each worktree the driver works in, the full flow's review copy included, before any gate or node runs there, so a repository whose gates need installed dependencies (`npm ci`, `uv sync`) gets them in a tree that carries only tracked files; checked as a gate's `run` is; a marker in the worktree's git directory says it ran, and a resumed run skips it where the marker is; a setup that fails, or leaves entries the build would commit (it commits with `add -A`), stops the run with `**Stop:** gate uncovered` naming them; the direct lane builds in the main checkout and does not run it, and the gates Action in CI does not read it |
| `repo.release_notes` | regexes | `["(^\|/)CHANGELOG\\.md$"]`, a `CHANGELOG.md` basename | the release-note paths, such as per-change fragment files or a localised twin: the driver leaves a changed path one matches out of the S shape's file count (contract §4), not its line count, and the REVIEW brief names those paths as the release-note entries to verify; each is searched in the repo-relative path with Python's `re`, case-sensitively, so `check` refuses exactly what the driver cannot compile; `[]` means none |
| `labels.scope` | label names | `[]` | scope or effort labels that may ride on any state |
| `labels.area` | label names | `[]` | the area labels triage, branch names and coordination read; they select no gate; the shape gate requires at least one on every agent-ready issue, an empty or undeclared set skipping that check |
| `labels.type` | label names | `[]` | the type labels; the shape gate requires exactly one on every agent-ready issue, an empty or undeclared set skipping that check; disjoint from `area` |
| `gate.run` | a command line | required per entry | the verify command, run from the repository root; `<ouro>` in it is the plugin root |
| `gate.ci` | a pwsh command line | `run` | what CI runs in place of `run`; one that only prints a skip makes the driver run `run` locally in every class |
| `gate.paths` | git pathspecs | none | the gate runs only when the diff changes one of them; without them it runs on every change |
| `gate.cheap` | `true`, `false` | `false` | the gate needs no toolchain and runs in seconds: the BUILD node is granted it on every build, whatever the checks run, so an issue with no acceptance command still gets a command (contract §4) |
| `gate.direct_code_check` | `true`, `false` | `false` | an explicit meaningful direct code check; requires nonempty `paths`, and need not be cheap |
| `ship.policy` | `stop-at-pr`, `trivial-merge`, `merge-on-green` | required | who merges; the effective policy is the stricter of the repository's and the issue's |
| `ship.unattended_classes` | `express`, `standard`, `full` | `["express", "standard"]` | under `merge-on-green`, the Flow classes the driver merges unattended |
| `ship.review` | `copilot`, `adversarial-review`, `external-audit`, `none` | required | the reviewer, by what it can observe: Choosing `ship.review` below |
| `ship.validation` | `local`, `ci` | `local` | final-input validation authority for PR flows, including resumed direct PR vehicles; inline direct checks remain local |
| `ship.validation_checks` | exact GitHub check names | none; required under `ci` | nonempty unique names declaring aggregate CI coverage of selected nonlocal gates |
| `ship.copilot_bot_id` | a Bot node id | none; required under `copilot` | the id the review request names; github.com-wide, not the repository's |
| `ship.external_cli` | `grok`, `claude`, `codex`, `copilot` | none; required under `external-audit` | the CLI `bin/external-audit.py` runs as the reader |
| `ship.external_model` | a model name | the CLI's own default | the reader's model; one the CLI refuses counts as a round-1 failure |
| `ship.external_effort` | an effort level, as the CLI names it | the adapter's own (grok `medium`, none passed on the others) | the reader's reasoning effort, passed as written; `default` or an empty value on the reader's command line is the adapter's own; one the CLI refuses counts as a round-1 failure |
| `ship.landing` | `merge-squash`, `rebase-squash`, `rebase-merge`, `merge-merge` | `merge-squash` | the sync and merge answers in one word: The landing policy below |
| `ship.sync` | `merge`, `rebase` | `merge` | how a branch is brought up to date before its pull request |
| `ship.merge` | `squash`, `merge` | `squash` | how a pull request lands |
| `ship.test_first_for_bugs` | `true`, `false` | none | accepted and read by nothing: Rules the skills apply below |
| `ship.forbidden_check` | `required`, `off` | follows the list's `# protects:` line, or with no list the target repository's visibility | whether the hook checks what a session posts against the private token list: The forbidden-token check below |
| `ship.direct` | globs | none, the lane off | the files the direct lane may push |
| `ship.direct_max_lines` | positive integer | `20` | direct policy's added-plus-deleted line limit over the whole linear commit range |
| `ship.direct_max_files` | positive integer | `3` | direct policy's changed-file union limit over that range |
| `models.builder` | `opus`, `sonnet`, `haiku`, `fable` | `opus` | the model of the BUILD, TRIAGE, CHECKPOINT and FOLLOWUP nodes |
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
| `overlays.implement` | paths | `[]` | checklists the builder reads before acting, inlined whole into every BUILD brief on every build and fix round with no path or area condition: a few unconditional hazards (a gate only CI runs, a write that corrupts state), never a standard or a path-scoped rule, which belongs under `.claude/rules` with a `paths:` header and reaches every `claude -p` node on its own |
| `overlays.review` | paths | `[]` | design and style standards for a reviewer; the driver's REVIEW brief does not carry them |
| `overlays.land` | paths | `[]` | landing checklists and tail-append hazards; no ouro script reads it |
| `owner.role` | text | none | the role the contract calls the owner |
| `owner.ruling_approvers` | logins | required | whose comment closes a needs-ruling and converts an architecture issue; every trust decision but two reads this set beside the job token's bot (the driver's `**Ruling**` and `bin/loop-outcomes.py`'s Assisted count read the set alone): the rolling-issue ledgers, the shape gate's `**Triage**` provenance, the body edits that stop a run, a checkpoint finding's author, and the `**Stop:**`, `**Triage**` and `**Checkpoint finding**` comments `bin/loop-outcomes.py` counts, so list every login that edits an agent-ready body or runs triage or run |

The eight states and two modifiers are the contract's and are not declared under `[labels]`;
`check` refuses a declared label that names one, or one both `area` and `type` hold, ignoring
case as GitHub does, and the installer creates the declared labels on the repository beside the
contract's, only the missing ones and never with `--force`, since they are the team's. A
`[rolling_issues]` title is matched exactly, never with `in:title`. Under `[docs]` an explicit
parameter on the gate's command line wins over what is declared, and an empty list means empty,
not the default; `docs.banned` carries a reason per entry because that signal blocks; reading
the table needs `ouro-binding.py` beside the gate and `python3` 3.12+ on PATH: without the tool
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

**The docs-only rule.** An `S` whose diff the driver would grade `express` (no gate file, no
path under a full-class prefix, no executable suffix) and whose every path is a docs file
(`.md`, `.txt`, `.rst`, `.adoc`, an image) skips the reader under `external-audit`: no reader
round is spent, its record reads `Review: none` with a note saying why, and the merge decision
runs as under `none`. A diff with one other path keeps the reader, and a fix round that adds one
(after a red gate or check, which no reviewer sees) brings the reader back for that fix.

**The availability rule.** An unavailable reader — the CLI `ship.external_cli` names is not
found, round 1 exits 1, round 1 exits 3 twice, or the run may not call it (a policy or network
rule forbids it) — falls back to the REVIEW node in every cell that names the reader, and the
record says so. An `S` re-derived as `M` after the commit (contract §4) takes the `M` row. Under
`stop-at-pr` the owner sees every PR whatever the value. The binding names one value, and nothing
requires it to name a second; the REVIEW node every `M` takes is the loop's own.

### The landing policy

`ship.landing` names the pair below it in one word: how the driver brings a branch up to date before
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

### Validation authority

`ship.validation` is `local` by default. A repository may opt its PR flows into CI validation:

```toml
[ship]
validation = "ci"
validation_checks = ["gates (windows)", "gates (linux)"]
```

The nonempty, unique check names are literal and case-sensitive, not globs. The owner declares
that together they execute all selected nonlocal gates, including gates delegated by a `ci`
notice. `gate.run`, `gate.ci` substitutions and `gate.paths` are the command and selection
declarations; there is no second command registry. The invoking gate list must equal
the committed gate list: a branch cannot silently change its own declared validation coverage.
Update the trusted invoking binding before resuming a gate-list change.

What CI mode requires before review, Ready or merge is [contract §4](contract.md), "Execution
evidence". macOS coordinators with admitted workers use this route; Windows needs an admitted
worker backend and does not gain native Windows Codex command/edit support. Local authority
and explicit caller-validation handoffs still require native Linux lifecycle accounting.
A Mac/Windows BUILD may prepare a commit for native-Linux validation. An unsupported
validation host waits with its selected authority and unresolved commands; the prepared work is kept.

To migrate an existing PR, the repository owner declares the check names above in the trusted
invoking binding and the branch's binding. Verify that those checks execute every selected
nonlocal `gate.run`. An additional acceptance command needs an exact `gate.run` declaration
whose paths select it and whose CI job actually executes it; check names alone add no coverage.
Local-only gates still run on the coordinator. Inherited caller handoffs without that CI
coverage require native Linux and `--validate`. No binding or obligation is changed automatically.
Resume `run N` with its original `--part` and `--pr` flags, where used. The prepared source is
kept; successful exact-head checks precede independent review, Ready and merge. Missing, skipped,
pending, failed or stale-head checks remain unproven. `validation_checks` has no effect in local
mode. Update older Ouro pins before adding these keys, since older readers reject unknown keys.

### Model tiers

`[models]` names the model tier per role, as an alias — `opus`, `sonnet`, `haiku` or `fable` —
never a pinned version, so a role follows whatever the alias currently means. Every key is
optional; absent means the reading skill's own default, named in the schema above.

`builder` is the driver's BUILD, TRIAGE, CHECKPOINT and FOLLOWUP nodes, and its fix-round build;
`builder_trivial` is the BUILD node when the issue carries `trivial`. `reviewer` is the REVIEW
node, the adversarial pass an M always takes whatever `ship.review` names.

### The forbidden-token check

`bin/forbidden-tokens.py hook` matches the text of each publishing `gh` or `git` call a session
makes against the private token list (README, "The private token list") and blocks on a hit and on
a missing list, except where the key, the list or the repository's visibility say below that it
does not.
`ship.forbidden_check = "off"` turns that check off for this repository: the hook passes the call,
a hit on the list included. It is for a private repository whose own issues legitimately carry the
values a list would hold, where the check blocks every call on a host with no list and nothing
useful can go on one. `"required"` runs the check whatever the list protects and the repository's
visibility, and a missing list blocks.

Absent, the check follows the list. A list (`OURO_FORBIDDEN_TOKENS` set and non-empty) names the
repositories it protects on a comment line `# protects: <owner/repo> ...`, and then a hit blocks
only a call that lands in one of them, by the directory's `repo.slug` or else its `origin`, or that
names one with a `-R`, `--repo` or `GH_REPO` value or an issue or pull request URL; a directory whose repository cannot be resolved counts as
protected. A list with no such line protects every repository, whatever the visibility. So a
consumer repository the list does not name needs no key. When no list is set, the hook reads the visibility with one `gh repo view` call:
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

## The developer binding

Worker execution is selected outside the repository in `~/.config/ouro/developer.toml`
(the ouro/developer.toml file under XDG_CONFIG_HOME when set). Precedence is the driver's
`--developer-config <path>`, then `OURO_DEVELOPER_CONFIG`, then that default file.
The flag precedes the subcommand. An absent default file preserves Claude behavior; an explicit
missing file, malformed TOML, unknown key or provider/model mismatch is a driver error.

| Scope | Location | Owns |
|---|---|---|
| Repository | `.claude/ouro.toml` | workflow, gates, review and shipping policy, labels and approvers |
| Developer | `~/.config/ouro/developer.toml` or the XDG configuration directory | worker backend, models, reasoning effort and optional board connection |
| Checkout on this machine | `.claude/ouro.local.toml` | the optional `repo.checkout` value |
| Launch environment | process or user environment | host variables such as `OURO_FORBIDDEN_TOKENS` |

```toml
[runtime]
backend = "codex"                 # claude | codex; absent means claude
effort = "high"                   # Codex only: low | medium | high | xhigh | max

[runtime.models]
builder = "default"
builder_trivial = "default"
reviewer = "default"
```

The runtime table admits only `runtime.backend`, `runtime.effort` and the three `runtime.models` roles.
The optional `coordination` table below is the only other developer table.
Developer settings never merge into repository shipping policy, review requirements, gates,
approvers or the external reader selection. For Claude, developer role overrides precede the
repository's model aliases and the usual role defaults. For Codex, roles use explicit
account-served model names or `default`, the CLI's built-in default; repository Claude aliases
are not passed to Codex or guessed into another provider's tiers. There is no provider fallback.
Without a developer override the external-audit reader still follows `ship.external_cli`;
choosing Codex workers does not change who must review a change.

### Optional advisory board connection

The same developer file accepts a strict `[coordination]` table, independently of `[runtime]`.
Existing runtime-only files keep their defaults. Omitting coordination disables the client;
there is no default endpoint, person or forum. All three must be supplied when the table exists.

```toml
[coordination]
endpoint = "https://board.example.invalid/mcp"
person = "Your display name"
forum = "Your forum"
credential_env = "MOMENTUS_API_KEY"  # default; names a variable, never contains the key
```

| Key | Validation | Default |
|---|---|---|
| `coordination.endpoint` | HTTPS URL without userinfo, query, fragment or whitespace; HTTP only for loopback fixtures | required |
| `coordination.person` | non-empty text, no surrounding whitespace or control characters | required |
| `coordination.forum` | non-empty text, no surrounding whitespace or control characters | required |
| `coordination.credential_env` | environment-variable name: letters, digits and underscores, starting with a letter or underscore | `MOMENTUS_API_KEY` |

Unknown keys, inline keys/tokens and repository shipping tables are refused. Put the key only in
the invoking coordinator's environment. Claude and Codex preflight and worker subprocesses,
external Claude/Codex reader launches and their tool and bridge children run without the
configured variable, Windows case aliases included, as do caller validation, setup, gate and
Git-hook children; the coordinator keeps it. No repository configuration is required.

The client, `bin/ouro/coordination.py`, serves the
[optional issue claim lifecycle and prepared-change scope checks](contract.md#optional-issue-claims).
A timeout, authentication failure, malformed response or incomplete MCP tool discovery returns
unavailable evidence with a short fixed notice, never proof that no claim exists; errors expose no
response bodies, headers, credentials or endpoint URLs. Board text and self-announced identities
are data, with no verified identity or shipping authority.

### Git capability readiness

Codex checks the advertised status, log and diff reads in its configured command sandbox, with
filesystem monitoring, external diff/textconv helpers and output-file options disabled for probes
and worker reads. Configuration and excludes come from their intended sources; an unreadable one,
including a permission warning when Git returns zero, is an infrastructure failure. Ordinary Git
errors are command results, not owner rulings.

Before a workflow worktree is created or set up, Git evaluates the selected commit, branch and
target path, onbranch, gitdir and nested includes included, in a disposable checkout the driver
exclusively owns; the probe creates no branch ref and runs no Git hooks. Cleanup removes only the
identities it created; unknown or changed state is kept. One final configured parent symlink to a
plain admitted directory is supported; retargeted parents, chained or intermediate aliases,
junctions and unknown reparse kinds refuse.

An occupied worktree must prove its actual root and that a file-changing fast-forward cannot change
its configuration dependencies. Unprovable tracked, relative or aliased dependencies refuse with
files and branch kept; manually sync the inspected tree or recreate it from the selected branch
before retrying. No configuration, excludes, credential-directory permission or caller execution
override is applied to force success.

### Runtime capability boundary

Codex workers require CLI 0.163.0+ and saved authentication. Preflight checks the selected CLI,
authentication, flags, each selected model's availability, the bridge contract and a structured
file-read proof; full readiness also checks OS sandbox startup, the Git reads above and a network
capability proof, all before tracker writes or branch creation. Each distinct selected model gets
a small file-only call on generated readiness data, reported as PREFLIGHT usage. TRIAGE takes the
file-only (READ) preflight; delivery runs and `inspect --check` take full readiness. A READ result
never admits commands, probes or edits. Process and guard failures are driver errors even when a
model returns an owner-stop answer. Root AGENTS.md and CLAUDE.md plus .claude/rules text are
supplied explicitly; path-scoped rules keep their declared scope. Instructions exceeding 128 KiB
are refused.

| Node | Codex tools |
|---|---|
| TRIAGE, FOLLOWUP and express/light REVIEW | worktree text read, glob listing and regex search; no command or write tools |
| CHECKPOINT | file tools plus the issue's listed acceptance/gate commands in a disposable default-branch worktree; no file edit tools or commits. With no command, reads the main checkout |
| BUILD | the file tools plus constrained write/edit/delete, the listed acceptance/gate commands and git exploration |
| Sized/full REVIEW | file tools plus probes in a separate detached worktree; no file edit tools, publishing or commits |

Codex's native shell, apps, plugins, hooks, browser/computer tools, image tools, subagents and
automatic skill catalogs are disabled for nodes; a private stdio MCP bridge exposes only the
node's granted operations, and the driver supplies the workflow and repository instructions.
The CLI itself runs read-only. Bridge edits are confined to the worktree and protect .git,
.claude, .codex, .agents and .aws paths at its root; symlink escapes and content writes through
hard-linked files are refused. Protected manual paths are classified both lexically and resolved,
junctions and Windows short names included, and a denial keeps its owner edit and merge barrier.
Peer worktrees are outside the node's scope. Edit-capable BUILD also receives the complete
installed run skill as outer-caller context: the worker edits only through its bridge and never
re-enters the driver, activates skills or publishes. Missing or invalid installed skill content
refuses BUILD before dispatch.

Approved commands run through a named Codex workspace sandbox with networking disabled and
managed requirements included. No shell chaining or environment assignments are admitted.
Literal `git rm` and `git mv` update worktree files through the bridge; the driver stages them.
A command blocked only by its environment uses the validation handoff; permissions are never
expanded. The network proof requires a generated loopback nonce to reach an unrestricted child
and the production sandbox to deny it; admission, timeout or an ambiguous result refuses readiness
before source or tracker mutation. It detects loopback admission, not every destination or
protocol.

Native Windows Codex refuses non-READ preflight before CLI, authentication or capability probes,
and rejects every command, probe or edit dispatch before worker launch: no Windows command-capable
worker is supported. READ still requires the outer read-only CLI and the file bridge to work.
Ouro runs no sandbox setup, creates no accounts and changes no global configuration, ACLs or
firewall policy. For a Windows driver, select a separate Claude developer profile via
`--developer-config` or `OURO_DEVELOPER_CONFIG` and use repository CI validation, or use native
Linux Codex. Execution evidence, caller validation and its lock, and interrupted BUILD recovery
are the driver's rules, in [contract §4](contract.md).

Every node uses a fresh session. Final results are validated against complete node schemas;
missing results, error events or unusable file tools are driver errors. The CLI prints usage and
attempt tables on exit, including coordinator errors after successful nodes and interrupted
attempts; missing metadata and unreported cost are unknown. Attempt diagnostics keep no event
bodies, prompts, source, tool arguments, environment values or error messages. Node timeouts and
cancellation clean up owned descendants.

`python3 bin/ouro-developer.py install` installs user-wide Codex skills and a launcher;
`inspect --check` inspects selection and runs preflight; `uninstall` removes owned installation
files while preserving the developer binding. Re-running install updates only unchanged owned
files and never overwrites unrelated user settings. Invalid, redirected or hard-linked ownership
metadata is refused before writes. Installer options seed a new binding; they do not replace an
existing developer's runtime or model choices.

The installer imports no Claude settings or environment map. A private-token gate uses the
existing list selected by `OURO_FORBIDDEN_TOKENS` in the launching environment; it does not copy
the list into a repository. The README's Codex setup shows persistent Windows configuration and
the app restart needed to inherit it.

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
  under any `ship.review` value, for a manual run. `claude` always uses the Claude model family,
  whatever model alias is named; when Claude also builds, the review has a fresh context and
  this reader's contract but shares the model family. `codex` refuses a model its account does not serve —
  the same round-1 failure as an absent CLI, but slower to surface, since a refusal can cost
  several reconnect retries first: name `ship.external_model` a model the account actually
  serves. `copilot` must be enabled by the organization's GitHub Copilot policy — a disabled
  account answers `Access denied by policy settings`, the same round-1 failure as an absent CLI —
  and it is a different thing from `ship.review = "copilot"`: this one is the Copilot **CLI**, a
  briefed repository reader, where `ship.review = "copilot"` names the Copilot PR-review bot,
  which takes no brief and runs only on an open PR. `ship.external_model` is optional and, when
  present, must be non-empty — absent, or `"default"`, means the CLI's own default model.
  `ship.external_effort` is optional and, when present, must be non-empty; absent means the
  adapter's own effort, which `"default"` and an empty value on the reader's command line name too.
- Every `[models]` key is optional, and each must be one of `opus`, `sonnet`, `haiku`, `fable` —
  see "Model tiers".
- `ship.forbidden_check` is optional and, when present, must be `"required"` or `"off"`; any
  other value or type is refused. Absent follows the list's `# protects:` line, or with no list
  the target repository's visibility, as "The forbidden-token check" says. Like every key but `repo.checkout`, the overlay may not set it.
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
  absent. This path-only query is a prefilter: `Push-Direct.ps1` validates the whole linear
  commit range against the fetched base binding by the direct lane's rules (contract §4). Like
  every key but `repo.checkout`, the overlay may not set these keys.
- Every `[[gate]]` needs a non-empty `run`. `check` refuses `areas`: `paths` is the only gate
  selector, and a gate without it runs on every change. A `run` may contain
  `<ouro>`, the plugin root, replaced before the command runs — that is how a gate the plugin ships
  is named, since its path differs per machine and per version. The root is inserted as it is, so
  where it may hold a space, quote the token in the `run` string. A copy of the binding tool that
  is not inside an ouro plugin has no root to insert — a vendored one in the consumer's scripts
  directory, or one under a consumer's own plugin, which carries another name — and there `check`
  fails such a `run`, naming the directory the copy sits in. A `run` is one command line: one
  holding a line break is refused, since `gates` prints one run per line.
  `python3 bin/ouro-binding.py gates --changed <base> [--head <rev>]` prints the run of each gate
  a diff selects, by the one rule the gates Action, the driver and the direct lane all read: the
  diff from the merge base of `<base>` and `<rev>` (default `HEAD`), with no rename pairing and no
  configured diff helper; it exits 1 on a diff it cannot read. Without `--changed` it prints every
  gate's run, and `--json` prints the selected `[[gate]]` tables instead.
- `repo.setup`, optional, is checked as a gate's `run` is: non-empty, one command line, and
  `<ouro>` refused where the copy has no plugin root.
- A `[[gate]]` may also declare `ci`, optional: the command the CI gate loop runs, as a pwsh
  command line, in place of `run` when a gate declares one — `run` stays what a local session,
  the driver and `gates` read. It is checked the same way as `run`: non-empty,
  one command line, and `<ouro>` refused where the copy has no plugin root. Declare it for a gate
  whose `run` wraps the real command for the dev box, like `wsl … bash -lc`, which a CI runner
  cannot run as written, or for a gate that another workflow already runs in CI, with a `ci` that
  only prints a notice saying so. The driver reads a `ci` that only prints as that notice: on a
  pull request neither it nor the builder runs the gate, and the record names it, by its `ci`
  text, among the gates the checks run; a `ci` whose printed text starts with `skip` is the other
  case, a gate the checks cannot run, which the driver runs itself, as it runs the token scan by
  name. The gates Action's `selection-mode` output is exactly `full`, `paths` or `fallback`; a
  missing output is unknown, never proof that selection was by paths. Callers delegating a
  whole-set provider should run it conservatively for full, fallback or unknown modes after their
  required gates pass.
- A `[[gate]]` may also declare `paths`, optional: a non-empty list of non-empty strings. `check`
  refuses a value that is not a list, an empty list and an empty or non-string entry, naming the
  gate. Each entry is a git pathspec, matched against the repository-relative paths a pull request
  or a push changes, the way `git diff --name-only` matches its pathspec arguments when run from the
  repository root: git sees each entry as written, magic such as `:(glob)` included. Without magic,
  a wildcard such as `*.md` matches at every level. A gate with no `paths` runs on every change:
  the checks run it, or the driver does where the `ci` bullet says so. A gate
  with `paths` runs only when the pull request changes a path that one of its entries matches, and
  is skipped otherwise. A changed path that no gate's `paths` claims selects no path-scoped gate:
  with only such paths changed, only the gates without `paths` run. The gates Action reads
  `paths` when its selection input is on, and the driver and the direct lane always read them,
  all by the `gates --changed` rule above; in the driver, a gate whose diff cannot be read runs.
  With selection on, a pull request's diff is read against its base and a push's against
  the tip the branch had before it, and every gate runs in these cases: an event that is neither;
  a push with no previous tip, such as a tag or a new branch; a diff that cannot be read; and a
  change to the gates Action. A gate that reads the binding names `.claude/ouro.toml`
  in its `paths`. A path-scoped gate whose list misses a file it depends on is skipped wrongly
  when only that file changes, so a list names everything its gate reads.
  A gate whose CI leg is path-filtered declares as its `paths` pathspecs that select the same paths
  as that filter, and every file the gate reads: without them, the driver runs it locally
  whenever it is selected, though the diff misses the filter.
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
  All are matched case-insensitively. `repo.release_notes` is compiled the same way and matched
  as its row says.
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
  `[[gate]]` entries as bound. The driver runs selected gates the checks skip. Local validation
  separately establishes committed-input proof; explicit CI validation uses the declared final-head
  checks for covered gates. A gate that merely repeats a PR's required status query
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
