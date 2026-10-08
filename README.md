# ouro

The issue-driven agent loop, as a Claude Code plugin. A backlog where every open issue is
either mechanically executable or waiting on exactly one named thing; agents do the mechanical
parts; humans answer rulings and review PRs.

```
file  →  triage  →  (rule if needed)  →  execute  →  PR review  →  land
              ↘
                (checkpoint)  →  finding + a recommendation  →  back to triage
```

## Install

```bash
claude plugin marketplace add iteramen/ouro
claude plugin install ouro@ouro
```

Then run `pwsh <ouro>/bin/Install-Ouro.ps1` from the repo's root (`-WhatIf` shows the writes
first). It creates the binding, `.claude/ouro.toml`, from `templates/ouro.toml.example` with the
slug, default branch and ruling approver filled from `gh` — the slug and branch of the repository
this clone's `origin` names, so a fork's clone is onboarded to the fork, not to its parent;
gitignores `.claude/worktrees/` and `.claude/ouro.local.toml` unless a `.gitignore` already
ignores them; and, when that slug is a repository rather than the example's placeholder and `gh`
is signed in, creates the labels on the repository the binding names — the contract's ten, and
the binding's declared `[labels]` scope, area and type, only the ones missing there and never
with `--force`, since those are the team's, not the contract's. `-Review` sets
`ship.review` (`copilot`, `adversarial-review`, `external-audit` or `none`; default
`adversarial-review`; `external-audit` also needs `-ExternalCli`) and `-Landing` sets
`ship.landing` (`merge-squash`, `rebase-squash`,
`rebase-merge` or `merge-merge`; default `merge-squash`), each written into a new binding only.
`-DocsFreshness` also copies the docs-freshness workflow. `-CI` copies the CI gate-loop workflow,
which runs the repo's `[[gate]]` list through the shipped `actions/gates` Action. `-IssueIntake`
copies the issue form, its template chooser config and the intake workflow. It never overwrites a
file, but it names a copy of the issue form or the intake workflow that differs from the plugin's
template, so a re-run after an upgrade shows which predates a change (the intake workflow's state
list, say). The new binding declares no `[[gate]]`: add the repo's verify commands, fill any
placeholder the run names, and run `python3 <ouro>/bin/ouro-binding.py check`. Schema and rules:
`docs/binding.md`.

In a Claude Code session, `/ouro:init` asks what the installer needs, runs it, and reports what
it wrote, skipped and refused. It then reports whether the repository has Issues — the loop reads
and writes them at nearly every step, and a new fork has none by default — and whether GitHub
deletes a merged PR's branch, offering to turn either on when it is off.

To see which ouro a session runs, put `bin/ouro-version.py` on the status line. It prints
`ouro <version>` from the user-scope install, with `(stale)` when the marketplace clone on the
machine is newer; it prints nothing when it cannot read the installed version, and the version
alone when it cannot read the clone. It reads local files only. Point the command at the
marketplace clone, whose path does not change when the plugin updates, in `settings.json` under
`$CLAUDE_CONFIG_DIR`, else `~/.claude`. Claude Code runs a status-line command through a POSIX
shell (Git Bash on Windows), so the path below takes the same default:

```json
{ "statusLine": { "type": "command", "command": "python3 \"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/ouro/bin/ouro-version.py\"" } }
```

A status-line tool that takes custom commands takes the same command as a widget. The stale check
sees only what the clone holds: it cannot tell that the channel moved until the clone is updated
(`claude plugin marketplace update ouro`).

A running session keeps the skills it loaded, so a skill a newer release adds takes three
commands in the session, in order: `/plugin marketplace update ouro` updates the marketplace
clone, `/plugin update ouro` updates the installed plugin from it, and `/reload-plugins` loads the
new skills into the running session. Outside a session, run `claude plugin marketplace update ouro`
and then `claude plugin update ouro@ouro`, the id the install line uses; the update applies after
a restart.

## The product

The durable part is the contract (`docs/contract.md`), the reasoning behind it
(`docs/rationale.md`), and the deterministic gates under `bin/` with the fixture suites under
`tests/`, together with what the gates read and ship through: the binding schema
(`docs/binding.md`) and its validator, the docs rule (`docs/docs-governance.md`), and the Action
under `actions/`. They travel by being read and run. The skills implement that contract for
Claude Code, with the verifier agent under `agents/` and the Claude steps of the weekly-pass
template: drills amended the day a run teaches something, and the part a model or harness change
re-opens.

## Skills

| Skill | Does |
|---|---|
| `/ouro:init` | Onboard a repo: asks what `Install-Ouro.ps1` needs, runs it, and reports each file written, skipped or refused, then reports whether the repo has Issues and whether it deletes a merged PR's branch, offering to turn either on when it is off. Re-run to add the docs-freshness workflow, the CI workflow or the issue intake files. |
| `/ouro:triage` | Grade issues against the contract: PROMOTE / NEEDS-RULING / CHECKPOINT / SPLIT / STALE. Report-and-approve. |
| `/ouro:e2e <N>` | One `agent-ready` issue, autonomously: entry ritual, execute, review to convergence, land, cleanup, and the follow-ups the landing surfaces. One issue only — queue order, waves and parallelism are not its business. |
| `/ouro:execute <N>` | One `agent-ready` issue → one PR. Re-verifies anchors first; a dead anchor stops the run. `trivial` and `checkpoint` variants. |
| `/ouro:land` | One branch to the default branch through the full PR flow. |
| `/ouro:land-batch` | Several reviewed PRs, one validated composition — the composed tree is checked, not each PR; each PR then lands through GitHub's squash-merge, checked against its local squash, or in one push when GitHub cannot reproduce the set. |
| `/ouro:fuse <N> <N>…` | Build several `agent-ready` issues that share files together: regions by shared files, one brief per region and one commit per issue, review split by file cluster, one PR per region landed through `/ouro:land`. |
| `/ouro:compile` | Read-only queue planner: runs the footprint analyzer and prints, in chat, which `agent-ready` issues can run at once, which `/ouro:fuse` sets to build together, which rulings hold blocked issues and what ready work their build would collide with, and what an umbrella or area still needs. Writes nothing and runs no work; the owner names what runs. |
| `/ouro:drift` | LLM audit of doc claims against artifacts. Report-only, append-only ledger. |
| `/ouro:seam` | Adversarial review of a contract seam — both sides locally right, the defect in the gap. |
| `/ouro:review` | Adversarial second reviewer. Light mode for a diff that changes only documentation, skill text, rules or comments; probe format, REJECTED ledger mandatory, for every other diff. |
| `/ouro:external-audit` | The `ship.review = "external-audit"` reader: a repo-reading, JSON-verdict adversarial review, over a CLI adapter `ship.external_cli` names (`grok`, `claude`, `codex`, `copilot` shipped), capped at two rounds. Diff mode judges a change against a goal; claims mode refutes a plan's factual assertions about the code. |
| `/ouro:intake` | The unattended weekly entry: gates, then intake grading. Uncredentialed — it proposes a manifest (`needs-triage`/`needs-ruling` only) that a model-free step applies; never promotes. |

What a repo's docs must be true of, and what each docs signal means: `docs/docs-governance.md`.
The labels: eight states, two modifiers, defined in the contract.

## Layout

- `skills/` — the reference implementation of the protocol for Claude Code. Nothing in here
  names a repo.
- `agents/` — `verifier`, the read-only doc-vs-artifact checker `drift` dispatches.
- `bin/` — the deterministic gates (`pwsh`), the binding validator (`python3`), label creation,
  `Install-Ouro.ps1`, which writes a repo's onboarding files, `Invoke-BranchSweep.ps1`, which
  reports, and on request removes, the branches and worktrees whose PR is finished, and
  `Vendor-Ouro.ps1`, which copies the plugin's contents into a repo's tree for a standalone exit.
  `forbidden-tokens.py` is the private-token matcher the scan gate and the applier run (see The
  private token list below).
  `Get-FootprintGraph.ps1` reads the file footprint of every open `agent-ready` and `needs-ruling`
  issue and prints which collide, which share a batch (a fuse set of at most five issues, merged
  along the strongest collisions), which can run at once and which ruling's build would collide
  with the most ready work as JSON, a proposal (recall 0.83 on the anchor paths, and 3.7% of
  disjoint pairs still touched a common file) that is not conflict-free and is re-checked against
  the diff after each build.
  `Get-CompileProgram.ps1` renders that JSON as text for `/ouro:compile`: the waves re-packed to a
  width, the batches as `/ouro:fuse` arguments, the rulings that hold blocked issues, an umbrella's
  children and the cleanup candidates, with its own lists checked before it prints.
  `Test-ChangelogEntry.ps1` is the diff-scoped one: it reports a change that moves a skill's front
  matter, the binding schema (or a value it newly refuses), a script's parameters or usage text, a
  shipped template, or an Action's own definition file, and adds no entry under
  `## [UNRELEASED]` in `CHANGELOG.md`; it also reports a `.claude-plugin/plugin.json` version with
  no `## [v<version>]` or `## [<version>]` heading there.
- `actions/` — `docs-freshness`, the composite Action a consumer workflow `uses:` at a pinned tag
  to run the docs-freshness gate against its own checkout, and `gates`, the composite Action a
  consumer's CI workflow `uses:` the same way to run its own `[[gate]]` loop.
- `tests/` — fixture suites for the gates, the installer, the sweep, the vendor script and the
  manifest applier, and one asserting that every `bin/*.ps1` exposes its own help.
  `changelog-entry/` drives the changelog gate over a scratch repository, one branch per surface.
- `templates/` — the issue form and its template chooser config, the intake workflow, the
  weekly-pass workflow, the docs-freshness gate workflow, the CI gate-loop workflow, the
  documentation rule, the example binding. `Install-Ouro.ps1` writes the binding from the example,
  the docs-freshness workflow with -DocsFreshness, the CI workflow with -CI, and with -IssueIntake
  the issue form to .github/ISSUE_TEMPLATE/, the chooser config to
  .github/ISSUE_TEMPLATE/config.yml (the only name GitHub reads it under) and the intake workflow
  to .github/workflows/; `Vendor-Ouro.ps1` copies the documentation rule into .claude/rules/. The
  weekly-pass workflow is copied by hand to .github/workflows/, the
  destination its header names.

## The private token list

Some values must never ship in this plugin's text. They live in one file outside every repository,
which the environment variable `OURO_FORBIDDEN_TOKENS` names. The file is UTF-8 (a byte-order mark
is accepted); each line that is not blank and does not start with `#` is one entry, a regular
expression matched case-insensitive against each line of the text, so an entry that is a common
word carries its own word boundaries (`\bword\b`). The binding does not hold the list.

`python3 bin/forbidden-tokens.py scan` checks every tracked file under the top-level `skills/`,
`agents/`, `templates/` and `docs/` from any working directory, and each tracked path too;
`check` reads text on standard input. A hit is reported by path, line and entry number, never by
the matched text; a path hit is reported by its position in `git ls-files`, and that file's other
hits name it by that position too, as does a tracked entry that cannot be opened (`cannot be
read`). A token wrapped across a line break, in a file or on standard input, is reported as
`entry <n>, across a line break`. An entry's own regex cost is its author's: a pathological entry
can stall the scan. Every layer fails closed: with the variable unset, the file unreadable or
holding a NUL character, an entry that does not compile, or no tracked file to scan, `scan` exits
1 and names the cause. CI is the gap: it has no list, so with `CI=true` the scan prints one
`SKIP:` line and exits 0, and checks nothing there. The check that matters runs on the machines that hold the list.
`bin/apply-manifest.py` runs `check` over every body file (placeholders unfilled) and `create` title it would post, before it
makes any `gh` call, and refuses on a hit or a missing list; `--no-forbidden-check` skips that check,
as the weekly pass's CI step does, and the run prints a line saying so. A binding may turn the
applier's check off with `ship.forbidden_check = "off"`, for a private repository whose own issues
carry such values (docs/binding.md, "The forbidden-token check"); the run then prints a line saying
so. That key does not reach `scan` or `hook`, which stay fail-closed.

`python3 bin/forbidden-tokens.py hook` is an example Claude Code `PreToolUse` hook for the commands
a session runs directly. On a `Bash` or `PowerShell` call it acts on `gh issue create` (or `new`),
`comment`, `edit`, `close` and `reopen`, `gh pr create` (or `new`), `edit`, `comment`, `review`,
`merge`, `close`, `reopen` and `revert`, and `git commit`, whatever the case of `gh` or `git`, with
a path or `.exe`, and with gh global flags before the noun; it checks the whole command text
(titles, bodies, commit messages, a heredoc) and the contents of a file named by `--body-file`,
`-F` or `--file`. It blocks the call on a hit, on a missing list, on a body file it cannot read, on
a `-` body (or a `/dev/` or `/proc/` path, once slashes, `.` and `..` are normalised, as given and
joined to the event's `cwd`) unless the whole command is one publishing `gh` or `git` call whose
first line ends in a quoted heredoc delimiter, `<<'DELIM'` or `<<"DELIM"` (`<<-` allowed; an
unquoted delimiter never passes), holds no `;`, `&`, `|`, `<`, `>`, parenthesis, brace, backtick,
`#` or `$'`, and has balanced quotes before the operator, followed by the heredoc body and its closing
delimiter and nothing else, and on an event it cannot parse, by exit 2:
Claude Code blocks a call only on exit 2, and any other nonzero exit lets it run. The reason names
the entry number, never the text. ouro installs none of this:
the owner registers the hook in their own `settings.json`, with `OURO_FORBIDDEN_TOKENS` set in the
environment Claude Code starts in, and the path pointing at the marketplace clone.

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Bash|PowerShell", "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/ouro/bin/forbidden-tokens.py\" hook" } ] } ] } }
```

The hook does not see `gh api` (`/ouro:land` posts review replies through it), nor a value expanded
at run time from a variable or a command substitution. A body file is read from the session's
working directory before the command runs, so a `cd` or a write to the same file earlier in that
command is not seen. A body or later command that mentions `--file`, `--body-file` or a spaced `-F` in
prose is read as a file name and blocks. A hook whose `python3` cannot be found exits 127, and one
that exceeds Claude Code's hook timeout does not block either: Claude Code treats both as
non-blocking, so a missing interpreter or a stall disables the hook. A guard against a bare
`git stash` or a kill by pattern can take the same layout.

## License

PolyForm Noncommercial 1.0.0 — free for individuals, nonprofits, education, and any noncommercial purpose; commercial use needs a license from the author. Full terms: `LICENSE`.

The public repository takes issues, not pull requests.
