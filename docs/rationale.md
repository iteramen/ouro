# Why the loop is shaped this way

Every mechanism in the contract exists because something failed without it. This page is the
reasoning; `contract.md` is the rule; `binding.md` is what a repo declares.

## The loop

```
file  →  triage  →  (rule if needed)  →  execute  →  PR review  →  land
              ↘
                (checkpoint)  →  findings + a recommendation  →  back to triage
```

Humans do exactly two things: answer ruling questions and review PRs. The lower branch exists
because those two jobs are not equally cheap: reviewing a PR is bounded; answering a ruling can
mean an open-ended investigation before the question even makes sense. A checkpoint run does
that investigation read-only and hands back a recommendation, so the ruling costs a review
rather than a study.

## Four kinds of context

Agent context separates into four kinds, and nearly every failure a loop has had was one kind
leaking into another's job.

**1. Stable instruction — how to work.** The per-session briefing (`CLAUDE.md`), path-scoped
rules that load only when the touched files match, the plugin's skills (each a drill with its
gotchas baked in, amended the same day a run teaches something), the per-repo binding, and the
repo's deterministic gates. The skills are a runbook for one harness; the contract is the spec
they run. *Why:* an always-loaded instruction file grows until it is a
graveyard of stale rules and adherence quietly drops. Stable instruction has to earn its place
per line, and detail has to load on demand.

**2. Current state — what the goal means now.** The unit is the GitHub issue, and it is
*verified* state, not merely recorded state — the sharpest departure from the usual "keep a
`current.md`" pattern. The `agent-ready` body is the per-task spec with its claims anchored in
the code; `needs-ruling` is the open-questions register; `human-ready` is the bench queue;
`blocked` names its trigger; `needs-triage` makes "ungraded" visible; `trivial` is graded
autonomy; `checkpoint` is the lane for a reviewable opinion; the rulings partition lets a
mid-execution judgment call park cleanly. *Why:* agents executed against specs the code had
moved out from under (→ the anchor ritual and the weekly anchor gate); agents silently picked
design decisions (→ `needs-ruling`); review time on mechanical changes cost more than it
protected (→ `trivial`).

**3. The map — what exists and where.** A top-level map that points at subsystem docs with an
explicit instruction not to read them proactively; feature docs beside the feature; a memory
index. The map is **audited, not trusted**: a deterministic freshness gate on every push, a
weekly LLM drift audit verifying doc *claims* against artifacts, a weekly cross-check of
settings-side configuration no diff can see, weekly anchor and contract-shape gates on the
agent-ready queue. *Why:* every restatement is
a copy that can rot — and did, in both directions: docs found stale, and docs found right while
the supposed authority was stale. Hence "verify against the artifact, fix whichever is wrong,
same pass" rather than a fixed precedence. The verifier files each disagreement against the doc;
fixing whichever is wrong is the fix pass's verdict, not the audit's.

The audit is not trusted either. Its report is current state too, and it ages from the moment it
was read — the commit, and any live state its verifiers probed: re-verifying 195 findings from a
ten-day-old report, before acting on any of them, found 13 already resolved, in exactly the docs
someone had cleaned in between, and two wrong when written. So consuming a report is a
verification pass, not a work list:

- re-locate each claim by its content, never by the line number it cites;
- read the artifact the finding cites, not just the doc, and re-probe any live state it cites;
- give each finding a verdict — still valid, moved, self-resolved, or wrong when written
  (misread, or wrong on the merits);
- say in the PR which findings were declined and why, or a reviewer cannot tell a finding that
  was handled from one that was quietly dropped; one declined as wrong when written also goes on
  the audit's ledger, or the next run reports it again.

**4. History — what happened, kept out of the way.** Docs are current state, git is history:
no progress logs, no "previously X"; commits, PR bodies and issue threads hold how-we-got-here;
superseded docs are deleted, not archived; frozen session artifacts are quarantined from
reading. *Why:* stale artifacts leak dead paths into long-lived material, and progress-log
sections rot into misinformation. History is safe exactly once it cannot outrank current state.

## Why the automation is report-only

The weekly pass is where the unattended LLM grading runs: deterministic gates first, then two
LLM passes that propose in two different ways. The drift audit proposes a rewrite of the drift
ledger's body and a comment per audited doc, in files; the pass, not the session, reopens or files
the ledger, with `umbrella` alone, and a later step with no model in it posts the files after a
poster refuses any directory out of layout, over a size cap, holding a secret shape, carrying a
run marker before its last comment, ending without one or naming a doc that is not a target. The intake proposes a comment and `needs-triage` / `needs-ruling` in a
manifest, which a later step with no model in it applies within the applier's unattended bounds.
Neither session writes to the tracker: each reads text anyone can write, so its grant names no
`gh` and no git command that runs a program or writes a file, and its step carries no token. A
permission rule cannot constrain what a session writes after its literal ending, so the bound on
what it writes is the program that reads what the session wrote, not a narrower grant, and the bound
on what it reads is a setting that confines its file tools to the workspace, with a Bash grant of
three git reads; of those, `git rev-parse --resolve-git-dir` prints the target line of an outside
file in gitfile form, and `git ls-files -X` tests a whole-line guess against one. An
unattended `/ouro:execute` run's only label move is the demotion §9 enumerates. The pass's
LLM never picks its own workload — a deterministic script selects targets — and never
promotes, closes, edits code, or merges. Promotion, `trivial`, and every merge are
human-approved. A zero-turn session is a failure, not a pass: the silent skip is the
failure mode this design refuses.

## The bottleneck the design creates

The loop converges the backlog to a state where every open issue is mechanically executable,
explicitly waiting on a named decision, or a curated umbrella. That moves the constraint:
throughput becomes **ruling velocity**, not agent capacity. When the `agent-ready` menu is empty
and the `needs-ruling` queue is long, the fix is upstream — more work shaped into answerable
questions (checkpoints, decision tickets) — not more autonomy downstream. The first question
to ask of anything in the rulings queue is whether it belongs there: a ruling that would begin
with the owner reading the code is a checkpoint wearing the wrong label.

## Gate lifecycle

No new blocking gate without a measured baseline, a fixture suite, and a report-only or
diff-scoped debut — and a rule with a measured zero-use record is removed rather than kept for
symmetry. A mechanism that looks like rigor and buys nothing is cargo cult.
