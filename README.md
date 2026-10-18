# ouro

The issue-driven agent loop, as a Claude Code plugin. A backlog where every open issue is
either mechanically executable or waiting on exactly one named thing; a program does the
mechanical parts and asks a model at four judgment nodes; humans answer rulings and merge the
pull requests the repo has not pre-authorized.

```
file  →  triage  →  (rule if needed)  →  build  →  review  →  checks  →  merge
              ↘
                (checkpoint)  →  finding + a recommendation  →  back to triage
```

ouro is five things:

- **A protocol** on the tracker and the pull request: eight state labels, the `**Triage**` and
  `**Stop:**` comments, anchors, the `Size:` and `Flow:` lines, the pull request record and its
  `Review:` line. `docs/contract.md` defines it; `docs/rationale.md` gives the reasons.
- **A binding**, `.claude/ouro.toml`, where a repo declares its slug, default branch, ship policy,
  reviewers, models and gates. `docs/binding.md` is the schema.
- **A driver**, `bin/ouro`, which runs the fixed sequence in code and calls a model at the
  triage, build, review and follow-up nodes only.
- **Four entry points**, `/ouro:init`, `/ouro:triage`, `/ouro:run` and `/ouro:weekly`, each of
  which binds, runs one driver command and relays its message.
- **The gates**: the scripts under `bin/` and their suites under `tests/`, which check the
  protocol whoever followed it, and the Actions under `actions/` that run them in CI.

## Calling it

`python3 <ouro>/bin/ouro <command>` from the repo's root, or the entry skill in a session:

| Command | Does |
|---|---|
| `run N [--part k] [--dry-run]` | One `agent-ready` issue to a landed change. Resumable: each call reads the tracker, the branch and the pull request, and continues from the first state not yet reached. |
| `triage N [--dry-run]` | The verdict comment, the `Size:`, `Flow:` and `Doc impact on close:` lines and the labels for one issue. |
| `weekly [--post]` | The outcomes and quality tables of `bin/loop-outcomes.py`. |
| `compile` | The next wave of the queue. |

Exit 0 is landed, waiting on the owner's merge or waiting on running checks; 1 a driver error; 2
refused before any branch was made; 3 stopped on a ruling, the fix-round cap or a gate nothing
can run. The driver keeps no file between calls, so a session, another user's bot or a cron on
any host with the binding and the tools calls the same command and gets the same continuation. A
session that follows the protocol by hand is held to the same marks by the gates.

## What a run posts

On the issue: the `**Triage**` verdict from `triage`, and a `**Stop:** <reason>` comment with
`agent-ready` swapped for `needs-ruling` when a run stops (`review cap`, `open decision`, `dead
anchor`, `gate uncovered`). On the pull request: a draft opened with the record (merge danger,
what changed, the `Review:` line, the verification), a `**Review** round <k>` comment per review
pass, and at the end either `**Landed unattended**` or `**Ready for merge**` with the checks, the
review line and the class. The issue closes through the squash message's `Fixes #N`. No outcome
line is written: `bin/loop-outcomes.py` reads the outcome from that record.

## What a consumer declares

The binding, from `templates/ouro.toml.example`: `[repo]`, `[owner]` (who may rule), `[ship]`
(`policy`: `stop-at-pr`, `trivial-merge` or `merge-on-green`; `unattended_classes`, the Flow
classes `merge-on-green` may merge, default `express` and `standard`; `review`; `landing`;
`direct`), `[models]`, `[labels]`, `[docs]`, `[authority]` and the `[[gate]]` list that CI and
the driver both run. The default is `stop-at-pr`: the driver opens the pull request and the owner merges.

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
`adversarial-review`; `external-audit` also needs `-ExternalCli`, and `copilot` also needs `-CopilotBotId`) and `-Landing` sets
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

## Entry skills

| Skill | Does |
|---|---|
| `/ouro:init` | Onboard a repo: asks what `Install-Ouro.ps1` needs, runs it, and reports each file written, skipped or refused, then reports whether the repo has Issues and whether it deletes a merged PR's branch, offering to turn either on when it is off. Re-run to add the docs-freshness workflow, the CI workflow or the issue intake files. |
| `/ouro:triage <N>` | `python3 bin/ouro triage`. |
| `/ouro:run <N>` | `python3 bin/ouro run`. |
| `/ouro:weekly` | `python3 bin/ouro weekly`; `/ouro:weekly compile` is `python3 bin/ouro compile`. |

What a repo's docs must be true of, and what each docs signal means: `docs/docs-governance.md`.
The labels: eight states, two modifiers, defined in the contract.

## Layout

- `skills/` — the four entry skills. Nothing in here names a repo.
- `agents/` — `verifier`, the read-only doc-vs-artifact checker.
- `bin/` — the deterministic gates (`pwsh` and `python3` scripts; the `pwsh` gates are the
  `bin/Test-*.ps1` scripts, and the `bin/Get-*.ps1` scripts are the target selectors, reports and
  dot-sourced helpers beside them; `git ls-files bin` lists every script), the binding validator
  (`ouro-binding.py`), label creation (`New-OuroLabels.ps1`),
  `Install-Ouro.ps1`, which writes a repo's onboarding files, `Invoke-BranchSweep.ps1`, which
  reports, and on request removes, the branches and worktrees whose PR is finished, and
  `Vendor-Ouro.ps1`, which copies the plugin's contents into a repo's tree for a standalone exit.
  `ouro/` is the driver (`python3 bin/ouro <run|triage|weekly|compile>`): it runs the loop's fixed
  sequence and calls the model at the triage, build, review and follow-up nodes. `Sync-Branch.ps1`,
  `Push-Branch.ps1`, `Watch-PullRequestChecks.ps1`, `Merge-PullRequest.ps1` and
  `Remove-LandedBranch.ps1` are the driver's deterministic landing steps — the sync by the
  binding's answer, the push, the head's checks watched to a verdict, the guarded merge, the
  branch and worktree cleanup — and read the binding through `Get-LandBinding.ps1` beside
  them. `Push-Direct.ps1`
  is the direct lane's push (`Flow: direct`): it pushes the default branch, or a branch for a pull
  request, only for a range inside `[ship].direct`.
  `forbidden-tokens.py` is the private-token matcher the scan gate and the applier run (see The
  private token list below).
  The other `python3` tools: `apply-manifest.py` executes a triage manifest against GitHub from
  the session that holds the owner's approval; `drift-claims.py` hashes, relocates and encodes
  the claim evidence for the drift audit; `external-audit.py` runs one external-CLI review round
  with a JSON verdict; `loop-outcomes.py` reports what became of every `agent-ready` promotion,
  per weekly cohort; `ouro-version.py` prints the installed plugin's version for a status line.
  `Get-FootprintGraph.ps1` reads the file footprint of every open `agent-ready` and `needs-ruling`
  issue and prints which collide, which share a batch (a set of at most five issues, merged
  along the strongest collisions), which can run at once and which ruling's build would collide
  with the most ready work as JSON, a proposal (recall 0.83 on the anchor paths, and 3.7% of
  disjoint pairs still touched a common file) that is not conflict-free and is re-checked against
  the diff after each build.
  `Get-CompileProgram.ps1` renders that JSON as text for `python3 bin/ouro compile`: the waves
  re-packed to a width, the batches, the rulings that hold blocked issues, an umbrella's
  children and the cleanup candidates, with its own lists checked before it prints.
  `Test-ChangelogEntry.ps1` is the diff-scoped one: it reports a change that moves a skill's front
  matter, the binding schema (or a value it newly refuses), a script's parameters or usage text, a
  shipped template, or an Action's own definition file, and adds no entry under
  `## [UNRELEASED]` in `CHANGELOG.md`; it also reports a `.claude-plugin/plugin.json` version with
  no `## [v<version>]` or `## [<version>]` heading there.
- `actions/` — `docs-freshness`, the composite Action a consumer workflow `uses:` at a pinned tag
  to run the docs-freshness gate against its own checkout, and `gates`, the composite Action a
  consumer's CI workflow `uses:` the same way to run its own `[[gate]]` loop.
- `hooks/` — the guards the plugin ships as Claude Code `PreToolUse` hooks, registered by
  `hooks/hooks.json`, which `.claude-plugin/plugin.json` names; `hooks/README.md` lists what each
  refuses, its override and how a guard fails.
- `tests/` — fixture suites for the scripts, Actions and workflow templates they drive, and one
  asserting that every `bin/*.ps1` exposes its own help.
  `changelog-entry/` drives the changelog gate over a scratch repository, one branch per surface;
  `land-scripts/` drives the landing scripts in-process over a gh stand-in, one suite per script;
  `hooks/` runs every registered hook over recorded events, one directory per hook.
- `templates/` — the issue form and its template chooser config, the intake workflow, the
  agent-ready shape-gate workflow, the docs-freshness gate workflow, the CI gate-loop workflow, the
  documentation rule, the example binding. `Install-Ouro.ps1` writes the binding from the example,
  the docs-freshness workflow with -DocsFreshness, the CI workflow with -CI, and with -IssueIntake
  the issue form to .github/ISSUE_TEMPLATE/, the chooser config to
  .github/ISSUE_TEMPLATE/config.yml (the only name GitHub reads it under) and the intake workflow
  to .github/workflows/; `Vendor-Ouro.ps1` copies the documentation rule into .claude/rules/. The
  agent-ready shape-gate workflow is copied by hand to .github/workflows/, the destination its
  header names.
- `release/` — this repository's own release tools, which the consumer release leaves out:
  `export.py`, which moves one tagged version to the consumer release channel;
  `Test-TrackerReferences.ps1`, the check that no shipped file cites this tracker's numbers;
  `no-binding-gate.py`, which runs the No binding workflow's step here; and two measurements of
  the skill and agent text, `shared-runs.py`, the runs of twelve words two files share, and
  `skill-growth.py`, the net change in lines of each `SKILL.md` body and each other `*.md` under
  `skills/` and `agents/` on a branch against its base, with the on-invoke token estimate where
  `claude` is on PATH.

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
makes any `gh` call but one visibility read, and refuses on a hit or a missing list. With no list set
and `ship.forbidden_check` absent, the read (`gh repo view`, even in a dry run) decides: a `PRIVATE`
or `INTERNAL` repository skips the check with one line, any other answer refuses as a missing list.
`--no-forbidden-check` skips that check, and the run prints a line saying so. A binding may turn the
applier's check off with `ship.forbidden_check = "off"`, for a private repository whose own issues
carry such values (docs/binding.md, "The forbidden-token check"); the run then prints a line saying
so. That key does not reach `scan`, which stays fail-closed; `hook` follows the applier's rule
(below): `"off"` passes a hit too, a call naming another repository with `-R` reads as if the
key were absent, and no key follows the visibility only with no list.

`python3 bin/forbidden-tokens.py hook` is an example Claude Code `PreToolUse` hook for the commands
a session runs directly. On a `Bash` or `PowerShell` call it acts on `gh issue create` (or `new`),
`comment`, `edit`, `close` and `reopen`, `gh pr create` (or `new`), `edit`, `comment`, `review`,
`merge`, `close`, `reopen` and `revert`, and `git commit`, whatever the case of `gh` or `git`, with
a path or `.exe`, and with gh global flags before the noun; it checks the whole command text
(titles, bodies, commit messages, a heredoc) and the contents of a file named by `--body-file`,
`-F` or `--file`. It blocks the call on a hit, on a missing list where the working directory's repository may not
publish without one (the applier's rule, above), on a body file it cannot read, on
a `-` body (or a `/dev/` or `/proc/` path, once slashes, `.` and `..` are normalised, as given and
joined to the event's `cwd`) unless the whole command is one publishing `gh` or `git` call whose
first line ends in a quoted heredoc delimiter, `<<'DELIM'` or `<<"DELIM"` (`<<-` allowed; an
unquoted delimiter never passes), holds no `;`, `&`, `|`, `<`, `>`, parenthesis, brace, backtick,
`#` or `$'`, and has balanced quotes before the operator, followed by the heredoc body and its closing
delimiter and nothing else, and on an event it cannot parse, by exit 2:
Claude Code blocks a call only on exit 2, and any other nonzero exit lets it run. The reason names
the entry number, never the text. The plugin registers the hook through `hooks/hooks.json`
(`hooks/README.md`), so a plugin install runs it on every such call, with `OURO_FORBIDDEN_TOKENS`
set in the environment Claude Code starts in: without a list it follows the applier's rule above,
and refuses every such call that rule does not pass.

The hook does not see `gh api` (the driver posts review replies through it), nor a value expanded
at run time from a variable or a command substitution. A body file is read from the session's
working directory before the command runs, so a `cd` or a write to the same file earlier in that
command is not seen. A body or later command that mentions `--file`, `--body-file` or a spaced `-F` in
prose is read as a file name and blocks. A hook whose `python3` cannot be found exits 127, and one
that exceeds Claude Code's hook timeout does not block either: Claude Code treats both as
non-blocking, so a missing interpreter or a stall disables the hook.

## License

PolyForm Noncommercial 1.0.0 — free for individuals, nonprofits, education, and any noncommercial purpose; commercial use needs a license from the author. Full terms: `LICENSE`.

The public repository takes issues, not pull requests.
