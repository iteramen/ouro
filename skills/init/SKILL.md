---
name: init
description: >
  Onboards a repo to ouro: asks the owner what the plugin's installer needs, runs it from the repo
  root, reports each file it wrote, skipped or refused, then reads the repository's Issues and
  merged-branch settings and offers to turn either on. Invoked by the owner as `/ouro:init`, for a
  first run and again to add the docs-freshness workflow, the CI workflow or the issue intake
  files. Writes no file itself and never edits an existing binding.
disable-model-invocation: true
---

# Onboard a repo — `/ouro:init`

`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` in Claude, or the root named by the installed Codex wrapper. Run from the repo root.
1. **Binding.** `.claude/ouro.toml` absent: a first run, the installer writes the stub. Present:
   `python3 <ouro>/bin/ouro-binding.py check`; a failure is refused with its output (the owner
   fixes a binding by hand). Then `pwsh <ouro>/bin/Install-Ouro.ps1 -DocsFreshness -CI -IssueIntake
   -WhatIf` and relay what this clone lacks.
2. **Ask once:** add `-DocsFreshness`? `-CI`? `-IssueIntake`? On a first run also `-Review`
   (`copilot`, `adversarial-review`, `external-audit`, `none`; `docs/binding.md`, "Choosing
   `ship.review`"), with `-CopilotBotId` for copilot (github.com has one id for every repository;
   `docs/binding.md` shows it), `-ExternalCli` and optional `-ExternalModel` for external-audit,
   and `-Landing` (`merge-squash` by default). With a binding present pass none of the last four.
3. **Run** `pwsh <ouro>/bin/Install-Ouro.ps1` with exactly the flags chosen, and relay every line
   it prints: `wrote:`, `skip:`, `REFUSED:`, `WARNING:` (what the owner still fills by hand), an
   indented line belonging to the line above. A non-zero exit is reported with the steps that
   completed. Leave the files uncommitted. With `-CI` declined, run
   `python3 <ouro>/bin/ouro-binding.py pr-workflows`: when it prints nothing, no workflow checks a
   pull request, so say that each `ouro run` then runs every selected gate itself and leaves the
   merge to the owner, and that nothing checks a pull request a person pushes by hand.
4. **Settings.** Read `python3 <ouro>/bin/ouro-binding.py get repo.slug`; a failure or the
   placeholder slug is a finding and nothing below runs. Then for that slug:
   `gh repo view <slug> --json hasIssuesEnabled --jq .hasIssuesEnabled` (the loop cannot run
   without Issues; offer `gh repo edit <slug> --enable-issues`) and
   `gh repo view <slug> --json deleteBranchOnMerge --jq .deleteBranchOnMerge` (offer
   `gh repo edit <slug> --delete-branch-on-merge`). Edit only on an explicit yes, read the value
   again and report what it prints. An unreadable value is a finding, not a stop.

Copilot's id on another host: `gh api "repos/<owner/name>/issues/<N>/timeline" --paginate --jq
'.[] | select(.event=="review_requested") | .requested_reviewer | {login, node_id, type}'`.

Codex developer setup is separate: run `python3 <ouro>/bin/ouro-developer.py install --backend codex` once per developer. It installs user-wide wrappers; the repository installer and existing binding keep their own scope.
