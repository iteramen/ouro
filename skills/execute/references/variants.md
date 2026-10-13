# The trivial and checkpoint variants

What changes when the issue carries one of the two modifiers (contract §4): `trivial`
pre-authorizes the landing under `[ship].policy = "trivial-merge"`, and `checkpoint` turns the
run into a read-only finding.

- [Trivial under trivial-merge](#trivial-under-trivial-merge)
- [Checkpoint: a finding, no branch](#checkpoint-a-finding-no-branch)
- [The proposed conversion](#the-proposed-conversion)
- [A committed SHA](#a-committed-sha)

## Trivial under trivial-merge

Under `stop-at-pr` a `trivial` label is advisory: note it in the report and stop at the PR
(`/ouro:execute` §4). When the issue carries `trivial` **and** `[ship].policy =
"trivial-merge"`, the full loop is pre-authorized. It waits for CI and the review as the
stop-at-PR flow does, in the foreground and within the same limit; once CI is green and the
review is triaged with **zero unresolved findings**, follow `/ouro:land` to the end — the merge
the binding declares (`/ouro:land` step 0), with branch delete (`Fixes #<N>` closes the issue).

Demote to the stop-at-PR flow if: the limit passes, any review finding is declined rather than
fixed, a review fix is itself non-trivial, the review names an untested vector nothing covers,
the landing would rewrite a branch origin already has (`/ouro:land` step 2's rebase or step
10's compression, contract §5), the implementation surfaced anything the label's bar excludes
(behavior change beyond gate/test coverage, new API), or the diff edits tests beyond additions
or touches migrations, flag defaults, authorization or workflows. Unsure means not trivial. A
demotion is stated in the PR body, in one sentence giving its reason (edit the body with
`--body-file`), and again in the report; then stop at the PR.

## Checkpoint: a finding, no branch

There is no branch and no PR. The deliverable is a **finding**, and the run is **read-only** —
no `Edit`/`Write` to the repo at all, no worktree, no builder. Do the analysis the body briefs,
then post it as a comment on the issue whose first line is `**Checkpoint finding**` alone, line
2 blank, and from line 3: the evidence with anchors (`symbol — "fragment"` `@ <short SHA>`),
what it shows, an explicit **recommendation** with its reasoning, and the proposed conversion
below. Say what you could not determine and what would settle it. That first line is the shape
the promotion provenance mark uses (contract §4), and it is what a later run reads to see that
this checkpoint has already delivered (`/ouro:execute` Preconditions).

A checkpoint that ends in "it depends" without a recommendation has not delivered.

## The proposed conversion

Propose the conversion rather than closing the issue — `Fixes #<N>` never applies here — and
change no label. The conversion is a triage verdict, applied through `/ouro:triage <N>` with
the owner's approval (contract §3); until then the issue keeps `agent-ready` and `checkpoint`,
and the finding comment carries the proposal:

- evidence settled the question → propose the `agent-ready` body for the follow-on work (the
  proposed body goes without `checkpoint`), for owner approval;
- a real decision survives → propose `needs-ruling` with the question **narrowed** and the
  recommendation attached, so the owner rules in one pass instead of investigating;
- the premise did not hold → propose a STALE closure citing the disproving artifact.

## A committed SHA

A checkpoint reads the repo as it is on the branch it was pointed at — it cannot see work in
flight elsewhere. If a premise in the issue cites a doc section, a count or a file that does not
resolve there, **say so and do not use it**; an unverifiable premise is a finding, not an
assumption to carry. Stamp everything you cite with the SHA you read it at.
