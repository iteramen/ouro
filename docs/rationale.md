# Why the loop is shaped this way

Every mechanism in the contract exists because something failed without it. This page is the
reasoning; `contract.md` is the rule; `binding.md` is what a repo declares.

## The loop

```
file  →  triage  →  (rule if needed)  →  run: build, gates, review, checks, merge
              ↘
                (checkpoint)  →  findings + a recommendation  →  back to triage
```

A person does two things: answers a ruling, and merges what `ship.policy` or a manual step
keeps for a person.
The lower branch exists because those two jobs are not equally cheap: reviewing a pull request
is bounded; answering a ruling can mean an open-ended investigation before the question even
makes sense. A checkpoint investigates with read tools and the issue's approved commands in a
disposable worktree, then hands back a
recommendation, so the ruling costs a review rather than a study.

## A driver, not a runbook

The loop's control flow is code, `bin/ouro`, and the model is asked only at the judgment nodes.
A harness integration's own units are skills,
agents and hooks, none of them a program, and control flow written as prose for the model to
execute fails in a way of its own: a step with no check behind it is the step a session skips,
and each rule then lives three times — in the prose, in a hook that catches the model when it
deviates, and in a copy for the next harness. A transition the driver owns cannot be skipped,
so the gates check the repository rather than police the model, and a hook catches a rule with
a command shape. The protocol — the labels, the comment shapes, the anchors, the Flow class,
the record on the pull request — is repository state, so any harness can read and write it.

Worker transports live behind the node capability contract. Developer runtime/model choices are
separate from repository grants; adding a harness adds a transport and constrained tool mapping,
not another workflow engine.

Outside the direct lane, who merges a pull request is the binding's `ship.policy`, a manual
step aside: autonomy is a value a repository sets, and the driver's unattended merge is logged
on the pull request. The driver's own watch, merge at the head SHA and branch removal need no
branch protection; protection is a guard against hand pushes, not the merge gate.
Repo-agnostic means a multi-project repository and a one-project private repository both bind,
which the binding's areas and gates already carry.

## Four kinds of context

Agent context separates into four kinds, and nearly every failure a loop has had was one kind
leaking into another's job.

**1. Stable instruction — how to work.** The per-session briefing (AGENTS.md or CLAUDE.md), path-scoped
rules that load only when the touched files match, the node briefs the driver builds from the
issue, the binding and the contract, and the repo's deterministic gates. A lesson a run teaches
lands at the lowest layer that can hold it: a hook for a rule with a command shape, a gate for a
rule a check can read, or nowhere. *Why:* an always-loaded instruction file grows until it is a
graveyard of stale rules and adherence quietly drops. Stable instruction has to earn its place
per line, and detail has to load on demand.

**2. Current state — what the goal means now.** The unit is the GitHub issue, and it is
*verified* state, not merely recorded state — the sharpest departure from the usual "keep a
`current.md`" pattern. The `agent-ready` body is the per-task spec with its claims anchored in
the code; `needs-ruling` is the open-questions register; `human-ready` is the bench queue;
`blocked` names its trigger; `needs-triage` makes "ungraded" visible; `trivial` is graded
autonomy; `checkpoint` is the lane for a reviewable opinion; the rulings partition lets a
mid-execution judgment call park cleanly. *Why:* agents executed against specs the code had
moved out from under (→ the anchor ritual and the anchor gate); agents silently picked
design decisions (→ `needs-ruling`); review time on mechanical changes cost more than it
protected (→ `trivial`).

**3. The map — what exists and where.** A top-level map that points at subsystem docs with an
explicit instruction not to read them proactively; feature docs beside the feature; a memory
index. The map is **audited, not trusted**: a deterministic freshness gate on every push, and
anchor and shape gates on the agent-ready queue. *Why:* every restatement is
a copy that can rot — and did, in both directions: docs found stale, and docs found right while
the supposed authority was stale. Hence "verify against the artifact, fix whichever is wrong,
same pass" rather than a fixed precedence. An audit is not trusted either: its report is
current state too, and it ages from the moment it was read, so consuming a report is a
verification pass, not a work list.

**4. History — what happened, kept out of the way.** Docs are current state, git is history:
no progress logs, no "previously X"; commits, PR bodies and issue threads hold how-we-got-here;
superseded docs are deleted, not archived; frozen session artifacts are quarantined from
reading. *Why:* stale artifacts leak dead paths into long-lived material, and progress-log
sections rot into misinformation. History is safe exactly once it cannot outrank current state.

## The bottleneck

Throughput is ruling velocity, not agent capacity; `contract.md` §10 holds the rule and what
the loop measures.

## Gate lifecycle

No new blocking gate without a measured baseline, a fixture suite, and a report-only or
diff-scoped debut — and a rule with a measured zero-use record is removed rather than kept for
symmetry. A mechanism that looks like rigor and buys nothing is cargo cult.
