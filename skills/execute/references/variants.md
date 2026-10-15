# The trivial and checkpoint variants

What changes when the issue carries one of the two modifiers (contract §4): `trivial`
pre-authorizes the landing under `[ship].policy = "trivial-merge"`, and `checkpoint` turns the
run into a read-only finding. The last section is the `Flow: direct` lane, a Flow value, not a
modifier.

- [Trivial under trivial-merge](#trivial-under-trivial-merge)
- [Checkpoint: a finding, no branch](#checkpoint-a-finding-no-branch)
- [The proposed conversion](#the-proposed-conversion)
- [A committed SHA](#a-committed-sha)
- [Direct](#direct)

## Trivial under trivial-merge

Under `stop-at-pr` a `trivial` label is advisory: note it in the report and stop at the PR
(`/ouro:execute` §4). When the issue carries `trivial` **and** `[ship].policy =
"trivial-merge"`, the full loop is pre-authorized. It waits for CI and the review as the
stop-at-PR flow does, in the foreground and within the same limit; once CI is green and the
review is triaged with **zero unresolved findings**, follow `/ouro:land` to the end — the merge
the binding declares (`/ouro:land` step 0), with branch delete (`Fixes #<N>` closes the issue on
a deliverable with no checklist, or its last part).

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

## Direct

Where the issue is stamped `Flow: direct` (contract §4) and the binding declares `[ship].direct`.
A checklist part (contract §3) is never `Flow: direct`. No worktree, no PR and no review: CI on
the default branch is the check, `Fixes #<N>` closes the
issue, and the commit message is the record. In order:

1. The entry ritual (`/ouro:execute` §0 and §1), the edit in the main checkout on the default
   branch, and the commit there with `Fixes #<N>`.
2. The gates the diff selects, run locally, narrowed by paths as `references/gate-selection.md`
   gives; the pull request's checks that would have run them do not exist here.
3. `pwsh <ouro>/bin/Push-Direct.ps1 -Issue <N>`, which reads the binding's globs and the default
   branch's protection and prints its outcome:
   - `vehicle: push`: pushed, origin's tip read back.
   - `vehicle: pr`: the commits are on the branch `direct/<N>`, and the local default branch is
     back at origin's tip. `gh pr create` with the commit message as its body and `Fixes #<N>`,
     then `<ouro>/bin/Merge-PullRequest.ps1` once the required checks are green
     (`Watch-PullRequestChecks.ps1 -Pr`); no review is requested.
   - `fallback: express` (exit 3), or a stop naming a path outside the globs: nothing is pushed
     and the run continues as express or tighter, by contract §4's classes, from a worktree cut at
     the commit, `git worktree add .claude/worktrees/<N>-<slug> -b <prefix>/<N>-<slug> HEAD`
     from the main checkout, whose default branch then returns to origin's tip with
     `git reset -q --hard origin/<default>` on its clean tree, the commit living on the
     worktree's branch. Every other stop before a push (a non-fast-forward or refused push, a
     missing `-Issue`, a failed fetch) takes the same worktree-and-reset recovery. A failed
     read-back is the one stop after a push: under `vehicle: push` the commit is on origin's
     default branch and someone pushed after it, so fetch, read origin's tip, and go on to step
     4's watch of the commit; under `vehicle: pr` the commits are on origin's `direct/<N>` and
     the local default branch still holds them, so fetch, read that branch, and go on to the
     `gh pr create` above.
4. The watch of the default branch's run on the commit, `Watch-PullRequestChecks.ps1 -Sha <sha>`
   (under `vehicle: pr`, the merge commit).

On a red run there is one fix commit through the lane (steps 1 to 4 on a new commit). On a second
red, both commits are reverted through the lane, the issue is reopened with a `**Stop:**` comment
naming the two runs, and it runs again through express.
