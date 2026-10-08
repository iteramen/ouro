---
name: init
description: >
  Onboards a repo to ouro: asks the owner what the plugin's installer needs, runs it from the
  repo root, and reports each file it wrote, skipped or refused; then reports whether the
  repository has Issues and whether GitHub deletes a merged PR's branch, and offers to turn
  either on. Invoked by the owner as
  `/ouro:init`, for a repo's first-run onboarding and again to add the docs-freshness
  workflow or the issue intake files. Writes no file itself and never edits an existing
  binding.
disable-model-invocation: true
---

# Onboard a repo — `/ouro:init`

`claude plugin install` writes none of the repo's onboarding files. The installer writes them;
this skill asks the owner what the installer needs, runs it, and reports its output. Then it
reads two repository settings, and changes either only on the owner's yes (step 4).

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the marketplace
checkout. The installed plugin lives under a versioned cache directory.

Run every command from the repo root (`git rev-parse --show-toplevel`): the installer refuses
any other directory.

## 0. Binding

Look for `.claude/ouro.toml` at the repo root.

- **Absent** — the first run. Go on to step 1; the installer writes the stub.
- **Present** — run `python3 <ouro>/bin/ouro-binding.py check`. It fails → refuse and relay its
  output. The owner fixes the binding by hand; the installer never edits one. It passes → also
  run `pwsh <ouro>/bin/Install-Ouro.ps1 -DocsFreshness -CI -IssueIntake -WhatIf` (writes nothing) and
  relay its output: the clone check, what this clone lacks against the committed binding —
  declared labels, the workflow files a first install wrote, gitignore lines.

## 1. Ask — one round

Ask only what the installer uses, all in one message:

- **Always:** add the docs-freshness PR workflow? Yes → `-DocsFreshness`.
- **Always:** add the CI gate-loop workflow? Yes → `-CI`.
- **Always:** add the issue form, its template chooser config and the issue-intake workflow?
  Yes → `-IssueIntake`.
- **Only when step 0 found no binding:** `[ship].review` — `copilot`, `adversarial-review` (the
  default), `external-audit` or `none` → `-Review <value>`. The plugin's `docs/binding.md`
  ("Choosing `ship.review`") says what each can observe and what each decides for an S and an
  M. With a binding present, leave it out and pass no `-Review`: the installer writes it into a
  new binding only.
- **If copilot:** the Copilot reviewer's bot id → `-CopilotBotId <id>`. It is the reviewer's
  `node_id`, and on github.com it is one value for every repository — the plugin's
  `docs/binding.md` shows it in its `[ship]` example. Put that to the owner to confirm, rather
  than sending them hunting: a first run has no binding to read it from, and step 4's
  `repo.slug` does not exist yet either.

  On another host, or to check the value, rediscover it: name a repository the owner can read
  and a PR **in that repository** which already carries a Copilot review. Off github.com, add
  `--hostname <host>` — `gh api` defaults to github.com whatever repository the path names, so
  without it the answer is github.com's id, or a 404. A row of nulls is a team's review request,
  not an id; no output at all means *that PR* had no review requested, not that there is no id —
  try another PR, then another repository. With no id to give, ask for another review value: the
  installer refuses `-Review copilot` without `-CopilotBotId`, before it writes anything.

  ```bash
  gh api "repos/<owner/name>/issues/<N>/timeline" --paginate \
    --jq '.[] | select(.event=="review_requested") | .requested_reviewer | {login, node_id, type}'
  ```

- **If external-audit:** the CLI it runs → `-ExternalCli <name>`, one of the adapters
  `<ouro>/bin/external-audit.py` ships (`grok`, `claude`, `codex` or `copilot` today). The
  installer refuses `-Review external-audit` without `-ExternalCli`, before it writes anything.
  Also ask the model, optional: default
  `default`, meaning the CLI's own default model → `-ExternalModel <name>` when the owner names
  one.
- **Only when step 0 found no binding:** `[ship].landing` — how a branch is brought up to date
  and how its PR lands, as one named pair: `merge-squash` (the default), `rebase-squash`,
  `rebase-merge` or `merge-merge` → `-Landing <value>`. The plugin's `docs/binding.md` ("The
  landing policy") has the table of what each pair means. `/ouro:land` lands the way the pair
  says; `/ouro:land-batch` lands squash commits only and refuses a batch on a binding that lands
  by merge — say so when the owner picks a pair ending in `merge`.
  With a binding present, leave it out and pass no `-Landing`: the installer writes it into a new
  binding only.

## 2. Run

```bash
pwsh <ouro>/bin/Install-Ouro.ps1 [-DocsFreshness] [-CI] [-IssueIntake] [-Review <value>] \
  [-CopilotBotId <id>] [-ExternalCli <name>] [-ExternalModel <name>] [-Landing <value>]
```

Pass exactly the flags step 1's answers selected.

## 3. Report

The installer's steps run in order — binding, gitignore entries, docs-freshness workflow (with
`-DocsFreshness` only), CI workflow (with `-CI` only), issue intake files (with `-IssueIntake`
only), labels. Relay every line it prints, grouped:

- `wrote:` — written. The label step prints `ok: <label>` per label created or updated on
  GitHub.
- `skip:` — skipped, with the reason the line gives. A second run with the same answers writes
  no file and names each one here.
- `REFUSED:` — not written, with the reason the line gives: the binding, the docs-freshness
  workflow, the CI workflow or an issue intake file, or a gitignore entry when the repo's
  `.gitignore` cannot be read or written; the run still exits 0.
- `WARNING:` — what the owner must still do: the binding keys it names, which kept the example's
  placeholder and are filled by hand, or the ignore rule that keeps the binding out of every
  other clone.
- An indented line belongs to the line above it.

A non-zero exit is a failure: report the error, and the lines printed before it as the steps
that completed. The installer's own refusals (not the git root, `-Review copilot` without an
id) come before any write; a failed label create comes after the binding, gitignore, workflow
and issue intake steps.

Leave the written files uncommitted, for the owner to review and commit.

## 4. Repository settings

With the report relayed, read two settings on the repository the binding names: whether it has
Issues at all, and whether a merged PR's branch is deleted. First read the slug on its own —
`python3 <ouro>/bin/ouro-binding.py get repo.slug` — and write what it prints into each command
below as `<slug>`: without it, `gh` picks a repository from the checkout's remotes, which need
not be the one the binding names. If that read fails, or prints the placeholder slug of the
plugin's `templates/ouro.toml.example` (the installer carries that placeholder too, for a
vendored tree where the example is not there to read), that is a binding nobody filled in, which
is step 4's finding, and nothing below runs.

### Issues

The loop reads and writes issues at nearly every step — intake grades the new ones, triage
rules on them, execute reads the one it is building, land closes it through `Fixes`, drift writes
its ledger there — so a repository without them cannot run it. GitHub gives a new fork none by
default, and a fork is what the installer onboards when a clone's `origin` names one: the slug it
wrote into the binding.

```bash
gh repo view <slug> --json hasIssuesEnabled --jq .hasIssuesEnabled
```

Report each result with the slug it was read for:

- `true` — report Issues on, and offer nothing.
- `false` — report them off, and say the loop cannot run without them. Offer to turn them on,
  and run this only on an explicit yes, since it changes a repository-wide setting rather than a
  file in the tree:

  ```bash
  gh repo edit <slug> --enable-issues
  ```

  Then run the read again and report the value it prints, not the one asked for. An edit that
  fails, such as one the token is not allowed to make, goes in the report with its output. A no
  leaves the repository untouched; say so. Either way the run goes on: read the setting below and
  finish the report. If Issues are still off, it says the loop cannot run there until they are on.
- Anything else — the read exits non-zero or prints neither `true` nor `false` — is a finding:
  report it with the command's output, and read the setting below anyway.

### Merged branches

```bash
gh repo view <slug> --json deleteBranchOnMerge --jq .deleteBranchOnMerge
```

Report each result with the slug it was read for:

- `true` — report it on, and offer nothing.
- `false` — report it off: GitHub then leaves a merged PR's branch on the remote unless whoever
  merged it deleted it too, as `/ouro:land` does. Offer to turn it on, and run this only on an
  explicit yes, since it changes a repository-wide setting rather than a file in the tree:

  ```bash
  gh repo edit <slug> --delete-branch-on-merge
  ```

  Then run the read again and report the value it prints, not the one asked for. An edit that
  fails, such as one the token is not allowed to make, goes in the report with its output. A
  no leaves the repository untouched; say so.
- Anything else — the read exits non-zero or prints neither `true` nor `false` — is a finding:
  report it with the command's output. The run is still complete; nothing above depends on it.
