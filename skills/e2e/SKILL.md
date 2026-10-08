---
name: e2e
description: >
  Run one agent-ready issue end to end, autonomously: the entry ritual, execute, the review to
  convergence, land, cleanup, and the follow-ups the landing surfaces. Invoked as
  `/ouro:e2e <N>`. Use when the user says "take issue N end to end", "run N autonomously",
  "execute and land N", or hands over one issue and steps away. It owns the whole cycle for ONE
  issue; queue order, waves and parallelism are not its business. Refuses an issue that is not
  agent-ready, and routes every decision it was not given to the owner rather than guessing.
---

# Run one issue end to end — `/ouro:e2e`

`/ouro:execute` builds, `/ouro:review` attacks, `/ouro:land` merges. This skill is only what joins
them: where one ends and the next begins, what authority the run carries between them, and what it
does with the work its own landing surfaces.

**It states no rule those three already state, except the sections
`skills/execute/references/dispatch-rules.md` pastes here.** A rule copied here drifts from its
original and leaves a reader guessing which copy governs. Where a step is theirs, this cites and
defers.

**One issue**, from its entry ritual to a landed, cleaned-up merge — then stop and report.
Choosing which issue, running several at once and ordering a queue belong one level up; several
issues that share files are built together by `/ouro:fuse`.

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the
marketplace checkout.

## 0. Bind, then refuse what is not yours to run

Read the binding as `/ouro:execute` §0 and `/ouro:land` §0 do, and refuse without it as they do.

Run **`/ouro:execute`'s Preconditions** and refuse exactly what they refuse,
and warn where they warn. Do not re-enumerate them here; a partial copy is how a cited list drifts.

Then read what decides this run's shape, and say what you got:

- **The modifiers.** A `checkpoint` run is read-only, as `/ouro:execute` §2 and §4 say. Here that
  means it ends at §2, with the finding comment `/ouro:execute` §4 prescribes; §§3-5 do not apply.
  `trivial` changes only what `/ouro:execute` says.
- **The effective ship policy**, `min([ship].policy, the issue's labels)`, as `docs/contract.md`
  defines it. It decides who lands: where it reaches `trivial-merge`, `/ouro:execute` merges the
  PR itself and §4 is this run's verification pass.
- **`[ship].review`**, which names the reviewer the binding adds — a bot on the open PR, an
  external auditor, or none. It does not decide whether there is a review: every M takes
  `/ouro:review` before the PR is marked ready, whatever the binding names, as the contract says.

## 1. The authority this run carries

State it once, so the boundary is not re-litigated mid-run.

**Without asking**, and only as far as §4's authorization reaches: cut the worktree and branch,
commit, open the PR, run the gates and the review, fix what the review proves, merge when every
guard in `/ouro:land` §10 passes, clean up, and file follow-ups.

**Ask, and wait:** a judgment call the spec does not settle; a finding that needs a ruling; a STOP
rule firing; anything touching a protected branch prefix. Ask with the measured finding, ranked
options and a recommendation.

**Every shell it runs, and every one it dispatches.** A command that removes a path removes a
literal path, the object returned by a create that never returns an existing path (`New-Item`
without `-Force`, which fails on one; `mktemp -d`, which makes a new one), or a path it has just
proved lies under the session's scratch directory: the target's resolved path starts with that
directory, its own trailing separator trimmed, then a separator, compared case-insensitively on
Windows. A recursive delete never silences its errors. Stop only a process you started, by the PID
you recorded when you started it: never `pkill`, `killall`, `Stop-Process -Name`, `taskkill /IM`, or
a `kill` fed by `pgrep`. A pattern matches every process on the host that holds it, another
session's jobs and your own shell included, and a count of processes by pattern counts the command
doing the counting. In PowerShell, no variable is named after one PowerShell reserves: a name
`Get-Variable` shows ReadOnly or Constant in a fresh `pwsh -NoProfile` (`$home`, `$pshome`,
`$isWindows`, `$host`, `$error`, `$pid` among them), whose assignment fails and leaves the old
value, or one the runtime rebinds (`$input`, `$args`, `$matches`, `$_`). A shell variable that was
not assigned is a path somewhere else. Text a command carries as data (an issue or PR body, a commit
message, a fixture, a mutant, a JSON line) that holds a backslash escape, a `$`,
a backtick or a template tag is written to a file with the agent's file-write or edit tool, never
through a here-doc, an `echo` or a quoted shell string, and reaches a command through a flag that
reads a file, such as `--body-file` or `git commit -F`. The file is then read back and compared with
the intended text before it is used: a tool call can decode an escape on the way, and a `\uXXXX`
escape sometimes arrives decoded even through the file-write tool. Text that must keep such an
escape is produced by a script that builds the backslash from its character code.

**Never:** promote the issue you are running, certify your own follow-up, push to the default
branch, merge a head nothing built, or take a release step — tagging and version numbering are the
repo's own procedure, not this skill's.

## 2. Execute

Run `/ouro:execute <N>`. That run opens the PR as a draft and marks it ready at the points and on
the condition `/ouro:execute` §4 sets, reviews as that section says before it is marked ready,
and under every value waits on the checks — §4 owns that order, so do not reorder around
it.

One joining rule, because a run in a worktree can gate the wrong tree: **invoke each `[[gate]]`
as this worktree's own copy, from the worktree root**. The gate classes disagree about which tree
that is — one takes its root from the working directory, another from its own location — so only
this worktree's copy, run from this worktree, satisfies both. Any other way can report a confident
green on a tree you did not change. A gate under `<ouro>/bin/` is the exception: it is invoked as
the plugin's copy, still from the worktree root, because those scripts take their root from the
working directory and so gate this worktree. A gate the plugin ships elsewhere, a fixture suite
among them, takes its root from its own location, so `<ouro>` outside `bin/` is not exempt.

## 3. Review until it converges

`/ouro:review`'s Steps are the procedure: its step 7 says how a fix round is verified, and the cap
and what a surviving finding becomes are the contract's. What this run adds:

- **Attack the claims, not only the code.** A builder's own mutation table proves the mutations it
  chose and is silent on the ones it did not. Spot-check it: a case counts only when it goes red
  on the platform its row names, or the row names the platform that would show it red.
- **Verify every claim before it is published.** A PR body and a commit message are read later as
  fact, and prose written from reading code is wrong often enough to measure. Whoever reviews the
  diff reviews the message with it.
- **Record what convergence cost** — the rounds run, and what the last one found — with the
  verdict in the PR body, so the next run can see it.

## 4. Land

**The invocation is the authorization, and it reaches this PR only.** "Take issue N end to end" is
the explicit instruction to land that `/ouro:execute` §4 and `/ouro:land`'s "The one hard gate"
require — which is what lets a run under `stop-at-pr` merge at all — and it authorizes no other
branch. What still stops the merge: `/ouro:land` step 6's answer for `[ship].review`, and a
`trivial` loop `/ouro:execute` demoted, which stopped for a reason no label records — this run
stops with it and reports that reason. So does a run whose issue is one of a set the owner
lands together: that set goes through `/ouro:land-batch`. A run that stops at the PR files its
follow-ups (§5); stopping there is a success, not a refusal.

`/ouro:execute` has already run `/ouro:land` steps 1-6 — the branch is pushed, the PR is open, the
review is requested — so **re-enter at step 7** and never re-run `gh pr create`. Run `/ouro:land`
step 0's two landing reads and its landing-rule check first: execute reads neither, and step 10
needs the merge answer. Then:

- **Diagnose a missing check before waiting on one.** `/ouro:land` §10 counts the runs for the
  exact head and says what zero of them means; `mergeable` and `mergeStateStatus` only explain a
  missing run, and a healthy-looking pair is not a run.
- **Watch the default branch's CI after the merge** and report its verdict. The merge is not the
  end of the run; the release channel going red is this run's finding.
- **Verify the cleanup on the filesystem**, as `/ouro:land` §11 describes, rather than trusting an
  exit code. For every worktree and checkout the run created, confirm that no dispatch of the run is
  still working there ("it has reported, and nothing it started is still running: none of the runs
  its state note lists") and that no process has its current directory there or names the path, as
  far as the platform lets a process's current directory and command line be read. The run's report
  (§7) names what the platform could not read. A dispatch's report alone is not that confirmation.

## 5. The follow-ups this landing surfaces

Every landing surfaces work: a sibling defect outside the diff, a stale claim, a pre-existing bug
the fix exposed. Each gets a decision, recorded in the report:

- **fix it here** when it is the same defect in another location — the same request, not adjacent
  work;
- **file it** through `/ouro:land` §12, which runs after the merge precisely so the anchors are
  fresh: what clears the severity floor (contract §3) becomes an issue, everything else one line
  in the area's cleanup issue;
- **park it** when it needs a ruling: `needs-ruling`, with the question narrowed and the options.

`/ouro:land` §12 already forbids labelling your own follow-up `agent-ready`; this run holds to it.

## 6. A ruling, and an interruption

**A ruling.** `/ouro:execute`'s STOP path posts the question and swaps the issue to
`needs-ruling`. When the owner rules, record it on the issue and run `/ouro:triage <N>`: triage
applies the ruling, restores the state label with the provenance comment the shape gate requires,
and settles the modifiers — none of which this run may do to the issue it is running. Then
**resume the same builder**, so its measurements survive; a fresh one re-measures from nothing and
often answers differently.

**An interruption.** Session limits, rate limits and reaped tasks end subagents mid-flight:

- keep a **resume point** naming the issue, the branch and worktree, the commit, which reviews are
  in flight and what each has reported;
- **on resume, read the world before redoing anything** — worktrees on disk, open PRs, the issue's
  labels — and never re-run a step whose output already exists;
- a **dead reviewer is re-dispatched**, its partial report handed over as unverified hints;
- a **replacement dispatch is handed the dead one's state note** before its transcript, and treats
  the note as unverified hints, as it would a partial report;
- a **killed gate is re-run**, not assumed: read its exit code, not its last line.

## 7. Report

At any moment the run can answer: which issue, which branch and worktree, which commit, what the
gates said and on which commit, which review rounds ran and what each found, what is fixed and
what is parked, where the PR is, what the default branch's CI said after the merge, and what
remains. It ends with exactly that, plus every follow-up filed or appended (§5) and every
decision routed to the owner.

If the run stopped at the entry ritual, that report is the finding — a refused start is the gate
working, not the run failing.
