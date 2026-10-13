---
name: land
description: >
  Land a feature branch to the repo's default branch via the full GitHub PR
  flow: verify state, sync, run the gates, push, open a PR, request the
  configured review, watch CI, triage review comments, rerun flaky checks,
  merge the way the binding declares (squash by default), delete the branch,
  and file follow-up issues. Use WHENEVER the
  user wants to land / ship / merge a branch — "land this", "PR, review, merge,
  delete", "open and merge a PR", "do the PR dance", "ship this branch",
  "finish this feature branch" — and for the individual sub-steps too:
  requesting a review on a PR, diagnosing a red CI check (flaky vs real), or
  merging + cleaning up a branch/worktree. Trigger even if the user only
  names part of the flow.
---

# Land a PR — `/ouro:land`

The end-to-end "feature branch → default branch" flow. Run it top to bottom; each step says
**why** so you can adapt when reality differs. The deterministic steps are scripts under
`<ouro>/bin/` (`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the marketplace
checkout): each reads the binding through `ouro-binding.py` beside it, names the binding's
repository on every `gh` call, and refuses with one `STOP: <why, and what to do>` line and exit 1;
its help holds every guard. What stays here is the order, the judgment, and the gotchas.
Throughout: `$REPO` = `[repo].slug`, `$DEFAULT` = `[repo].default_branch`, `$PR` is the PR number,
`$BRANCH` is the feature branch. The batch variant is `/ouro:land-batch`.

**Every `gh` call names the repository** — `-R "$REPO"`, `gh api` its explicit `repos/$REPO/…`
path or `$REPO`'s owner and name as GraphQL variables. A bare call addresses whatever gh picks from
the clone's remotes, which in a fork's clone is the parent: this skill would open and merge PRs on
the upstream while `/ouro:execute` read the issue from the fork. Only `gh auth status` stays bare.

## 0. Bind — and refuse what is not yours to land

Step 1's script reads `.claude/ouro.toml` at the repo root (`[repo].checkout` if set, else the
cwd; a gitignored `ouro.local.toml` overlay may supply it) and refuses to run without a binding,
or with one that fails `check`. It refuses what is not this skill's to land:

- **Protected prefixes are absolute.** If `$BRANCH` *or the target* starts with any entry of
  `[repo].protected_branch_prefixes`, it stops. Those follow the repo's own release procedure —
  never this skill. It names the prefix that matched; do not offer a workaround.

**How this repo lands.** Step 2 brings the branch up to date by the sync answer and step 10 lands
it by the merge answer, `ship.sync` and `ship.merge`, a `[ship].landing` preset answering where
the binding declares only that, and the default (merge to sync, squash to land) where nothing is
declared. Step 1's script prints both, with `[ship].review` (step 6), `[authority].local` (below)
and `[overlays].land` (step 3); the later scripts read the same keys themselves.

**The repository's own landing rule.** Read the documents `[authority].local` names for one thing:
a statement of how a branch is brought up to date or how a pull request lands. Where they are
silent, or agree with the two answers, go on. Where one contradicts them, **refuse before step
1**. Examples: a document forbids merge commits on a branch while the sync answer is merge, or it
requires preserved commits while the merge answer is squash. Name both sides: the document and its
sentence, and the key with its value or the default it took. Then hold. Add no note to the PR body
and pick no side; the owner changes the binding or the document. A document that requires a linear
history agrees with a squash answer only while the range has one author, because step 10's
authorship exception lands a range with more than one author email by merge commit. Note whether
such a document exists (`-Linear yes`, else `no`) for step 10's script, which reads the authors of
the head the checks saw and stops in this case before it merges.

The git mechanics below, how step 2 syncs and how step 10 lands, are the binding's choice, not law.

## The one hard gate

Pushing to `$DEFAULT` is gated by `[ship].policy` and by the user. A user request to "land / merge
/ ship this branch" **is** the authorization to merge **this** PR — it does not authorize unrelated
default-branch pushes. If the user only said "open a PR", or `[ship].review` resolves to "stop at
the PR" (step 6), stop after step 5 and don't merge.

A rewrite of a branch origin already has, by step 2's rebase or step 10's compression, is
authorized by the same request, under the backup and lease rules there, and by nothing less. The
unattended `trivial` loop does not carry it (contract §5): a trivial run that would have to rewrite
a pushed branch stops at the PR.

## Preconditions

Step 1's script checks the first three; the fourth is yours.

- Working tree clean (an untracked file is listed, not a stop: it rides no push), and you're on
  the **feature branch** (not `$DEFAULT`).
- **The branch name satisfies the repo's naming policy** (a `*` gate or CI check enforces it where
  the repo has one). A worktree created without `-b` sits on a `worktree-*` local branch, which
  the script refuses: rename **before** pushing or opening the PR (`git branch -m worktree-x
  feature/x`). Renaming the **remote** branch after the PR exists **closes the PR** — GitHub's
  branches/rename API does not retarget an open PR despite its docs.
- `gh auth status` is logged in.
- **Reproduce the CI-only gates locally before pushing** (step 3). Don't push a red PR you could
  have caught locally.

## The sequence

### 1. Verify state
`pwsh <ouro>/bin/Test-LandState.ps1`

One call: the binding read and step 0's refusals, the tree and the login, then what the landing
needs — the branch, the default branch, the sync and merge answers, `ship.review`,
`[authority].local`, `[overlays].land` — and the commits you're landing, oldest first. Nothing is
written.

### 2. Sync with the default branch  ⚠️ don't skip
CI runs on the branch head **as-is**, and GitHub only surfaces staleness at merge time
(`mergeStateStatus` = `DIRTY`/`BEHIND`) — so a green PR can still be built against a base it never
compiled with. Bring the default branch in FIRST, so CI validates the combined result:

`pwsh <ouro>/bin/Sync-Branch.ps1 [-Sync merge|rebase]`

`-Sync` defaults to step 0's sync answer. The script fetches `origin/$DEFAULT`, stops where the
base gained nothing (step 4 then pushes plainly), leaves a merge conflict in the tree to resolve or
abort, refuses to rebase a branch someone else is working on, and after a rebase names a backup
branch and prints `lease:` for step 4; its STOP line names the refusal. After a sync that brought
anything in, it prints the pre-sync tip, the old base, and the files both sides changed since it.

If the incoming delta includes **code** (not only docs), re-run the gates for the overlapping
areas (step 3) before pushing. A finished rebase that proves wrong goes back with
`git reset --hard <backup>`; the backup is a local branch, and step 11 deletes it once the PR has
landed. A sync is read against the branch, not trusted because git took it:

- **What the base changed.** For each file the script lists as changed by both sides, read
  `git diff <old base> origin/$DEFAULT -- <file>` against the branch's own additions: a twin, copy
  or moved block of the changed text that the branch added keeps the old text, and git merges it
  cleanly.
- **An append-only file** (a changelog-like file every branch appends to) that conflicts under
  either sync answer is resolved by keeping both sides' entries, and the result is read against
  both sides' entries before the sync is committed: a union can drop or resurrect lines.
- **A conflicted sync is a change.** Before pushing, read-verify every resolution: under a merge,
  every line each parent added since the merge base (`git diff <merge base> <parent> -- <file>`) is
  still in the result, or the record says why not; under a rebase,
  `git range-diff --creation-factor=100 <old base>..<backup> origin/$DEFAULT..HEAD` shows each
  replayed commit against its original, and a line it drops is a loss. The PR record names the
  conflicted files and how the resolution was verified.

### 3. Run the gates locally
Run every `[[gate]]` whose `areas` covers what the branch touched, **plus every `areas = ["*"]`
gate** — the `*` gates catch people who correctly decide the area gates don't apply (a lone `.ps1`
or `.md` change is still a change). A gate in that set that declares `paths` then runs only when
the branch's diff touches one of them: `/ouro:execute`'s `references/gate-selection.md` holds that
narrowing, its fallbacks, how each entry reaches git, and the evidence line for a skipped gate, and
this step applies it as written there. Each gate's `run` executes from the repo root, with any `<ouro>` in it replaced by
the plugin root first. The run follows the Gate environment, What CI checks, Two reads and Coverer
sections of `skills/execute/references/dispatch-rules.md`.

- **Commit first, then gate.** A gate that diffs committed state against a base ref reports on the
  *previous* commit when your edit is still unstaged, and hands you a false green. Use
  `$(git merge-base origin/$DEFAULT HEAD)` where a gate takes a base ref.
- **A green gate run stands when its tree still matches**, here and on its re-run under step 6's
  adversarial review: `git rev-parse HEAD^{tree}` equals the tree that run gated, the base
  (`git merge-base origin/$DEFAULT HEAD`) is unchanged, and the step needs no gate that run did not
  include. Cite that run — its commit and the tree match — in the PR's Verification section instead
  of a new one. Any tree difference re-runs the gates, and so does a gate whose input is not the
  tree and the base, such as one that reads commit messages.
- **Read `[overlays].land` now.** Those checklists carry the repo's own gate gotchas — the CI-only
  style analyzers a plain build won't show, the language-agnostic checks that fire on docs, the
  build flags a standalone project needs. They add to this step; they never replace it.

### 4. Push the branch
`pwsh <ouro>/bin/Push-Branch.ps1 [-Lease <the tip step 2 printed after lease:>]`

Without `-Lease` it is a plain `git push -u`, which refuses to overwrite a commit origin holds:
`STOP: the push was refused` is a non-fast-forward, and the fix is to fetch and read what origin
has, never to force. Where step 2 rebased a branch origin has and printed its tip as the lease,
pass it: the push is `--force-with-lease` pinned to that tip. The plain force flag is never the
push, in any step. The script reads origin's tip back and stops unless it is the pushed HEAD.

### 5. Open the PR
Open it as a draft, at the point `/ouro:execute` §4 names, and mark it ready once that section's
condition holds; under `[ship].review = "adversarial-review"`, step 6's verdict is in the body
before it is marked ready. Write a body that summarizes the **whole branch** (not just the last
commit) and links any follow-ups you'll file in step 12, to a file with the file-write tool:

```markdown
## Merge danger
- **Door:** <two-way or one-way>: <one sentence of why>
- **Blast radius:** <what breaks if this is wrong, and for whom>: <one sentence of why>
- **Smoke by hand:** <what the merger checks in the running app, for code with no test host>
## Summary
<what + why, branch-wide, three lines or fewer>
## Changes
- <bullet per logical commit>
<details><summary>Verification</summary>

- Gates at <short SHA>: <one line per gate run: its command and exit code; output in the linked comment or file>
- Review: <the verdict line, and the link to the report comment>
- Files another issue edits: <each match, the other issue, and why this deliverable needs the file or that it was reverted; the line is dropped when there is none>

<the measurement table for a bug, and the mutation table when the change adds a test>

</details>

## Follow-up
- <#issue, filled in step 12>
```

Then `gh pr create -R "$REPO" --base "$DEFAULT" --head "$BRANCH" --draft --label <Area> --label
<Type> --title "type(scope): summary" --body-file <that file>`, with one or more labels from
`[labels].area` plus the type the contract uses.

The body opens with what a human needs to decide whether to read on. **Door** is two-way when a
revert undoes the change, one-way when it does not: a migration, data written or deleted, a
message sent outward, a published tag or release, a flag default consumers pick up, or a
consumer-contract change. **Blast radius** is what breaks if the change is wrong, and for whom.
**Smoke by hand** is what the merger checks in the running app for code with no test host
(`/ouro:review` step 5), dropped when there is none. All three are claims like every other sentence
in the body. Verification goes inside `<details>`, off the first screen but in the body
`/ouro:review`'s verification message reads: one line per gate, its command and exit code, the
output posted as or linked from a PR comment, never pasted; the reviewer's report and each
verification's answer as PR comments (`/ouro:review` step 6), the body keeping the verdict line and
the link; the measurement table for a bug and the mutation table when the change adds a test, and
nothing else. GitHub renders Markdown there only after a blank line: keep the one after
`</summary>`, and put one before each table. The `Fixes #<N>` line `/ouro:execute` adds stays at
the start of its own line, where the loop metrics match it.

**The `Review:` line** is the one line the loop metrics read for the change's review. It stands
alone, at the start of its own line, outside any fenced block (a reader skips fenced blocks before
matching), directly under the body's `Fixes` line or lines. It reads either `Review: rounds:2
findings:H0/M3/L4 false-sentences:1` (every field a decimal count, `false-sentences` also `unread`)
or `Review: none`, and this pattern, read multiline, matches it:
`^Review: (?:none|rounds:([0-9]+) findings:H([0-9]+)/M([0-9]+)/L([0-9]+) false-sentences:([0-9]+|unread))[ \t\r]*$`.
- **`rounds`** is the fix rounds the change took, counted as contract §7 counts them: a prose
  round is not counted. It is 0 when the first review needed no fix.
- **`findings`** is the `CONFIRMED` findings of the full review and of every verification, each
  counted once, at its severity as `REVIEWER-PROMPT.md` has the reviewer name it (`external-audit`'s
  blocker is H, major M, minor L); `OUTSIDE` findings and `STANDARDS` entries are not counted.
- **`false-sentences`** is the record sentences the one record reading (`/ouro:review` step 7)
  found false, counted at that reading and never re-taken, so a sentence fixed afterwards still
  counts. It reads `unread` when no record reading ran.
- **`Review: none`** is for a change that neither `/ouro:review` nor `external-audit` reviewed.
  Copilot's comments carry no severity, so they are not counted.

`/ouro:review` step 6 fills the line and `/ouro:fuse` §8 fills a region's; both cite this
definition.

### 6. Request the review — switch on `[ship].review`

**`copilot`**  ⚠️ gotcha
Every `gh` and REST route fails, and **REST fails silently** — that's the expensive part.
`gh pr edit --add-reviewer Copilot` prints the PR URL and adds nobody; a POST to
`repos/$REPO/pulls/$PR/requested_reviewers` with `reviewers[]=Copilot` or
`reviewers[]=copilot-pull-request-reviewer[bot]` returns **HTTP 200 with an empty array**. Use
GraphQL `requestReviews` with **`botIds`** (not `userIds` — Copilot is a Bot node), the PR's node id
from `gh pr view "$PR" -R "$REPO" --json id --jq .id` and the bot id from `[ship].copilot_bot_id`:
`gh api graphql -f query='mutation { requestReviews(input: {pullRequestId: "<node id>", botIds: ["<bot id>"], union: true}) { pullRequest { reviewRequests(first: 10) { nodes { requestedReviewer { __typename ... on Bot { login } } } } } } }'`.
Success is `{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}` in
that mutation's own response — **verify there**, not from the REST POST (a healthy-looking 200) nor
`gh pr view --json reviewRequests`, whose jq path hides bots. If the binding's bot id is rejected,
rediscover it from a PR that already has a Copilot review and fix the binding:
`gh api "repos/$REPO/issues/<some-reviewed-pr>/timeline" --paginate --jq '.[] | select(.event=="review_requested") | .requested_reviewer | {login, node_id, type}'`.
The display login is `Copilot`, the API login `copilot-pull-request-reviewer`; `copilot-swe-agent`
is a **different bot** (the coding agent, attached with `addAssigneesToAssignable`).

**`adversarial-review`** — run `/ouro:review` on the branch range
`$(git merge-base origin/$DEFAULT HEAD)..HEAD` **before the PR is marked ready** (it may already be
open as a draft, `/ouro:execute` §4), at the weight contract §4 selects (light, sized or the probe
format). Fix what it proves, have it verify the fix (its step 7), re-run step 3, then put the
verdict in the PR's Verification section, post its report as a comment, and mark it ready.

**`external-audit`** — the external critic `ship.external_cli` names. Run `/ouro:external-audit`
on the same range; an unavailable reader falls back to `/ouro:review` by the availability rule in
`docs/binding.md` "Choosing ship.review", and the record says so.

**`none`** — stop at the PR. Human review only; the merge is theirs.

### 7. Watch CI
Opening the PR triggers the checks. One call watches them to the end and judges them:

`pwsh <ouro>/bin/Watch-PullRequestChecks.ps1 -Pr $PR`

It judges the newest `pull_request` run per workflow and event by its own conclusion, waits for
each, and lists again for a run the `ready_for_review` event starts. `STOP: no pull_request run …
after a minute` is step 10's zero-run case: push (or re-push) the head and come back. `STOP: run(s)
concluded outside success/skipped/neutral` names the red run for step 9. CI is path-filtered:
**don't wait for a check that was never going to run**; `skipping` entries are normal, and the
required checks branch protection names are the ones that gate the merge.

### 8. Triage the review
When the review lands, apply review-reception discipline (superpowers `receiving-code-review` if
available) — **verify each comment against the code before changing anything**, and never reply
with performative agreement. For each inline comment:

- **Confirm the premise in the actual code.** A reviewer reasons from the diff and is often right
  but sometimes assumes a reachable state that isn't. Read the cited file/line and the call sites
  before acting.
- **Valid + in scope** → fix it, commit, and have the fix commit verified (`/ouro:review` step 7).
  The round's commits are pushed together once that verification passes (`/ouro:execute` §4), not
  one push per finding. After the push, reply **in-thread** with what changed —
  `Fixed in <sha>. <one line on the change>.`, written to a file with the file-write tool, then
  `gh api "repos/$REPO/pulls/$PR/comments/<comment_id>/replies" -X POST -F body=@<that file>`,
  the comment id from `gh api repos/$REPO/pulls/$PR/comments --paginate --jq '.[] | {id,path,line}'`.
- **Valid, after `/ouro:review` has verified the change** → the fix above is the one extra round
  contract §7 allows. A valid finding after that round is answered on its thread with the reason
  and goes where the severity floor (contract §3) sends it, not into a further round.
- **Wrong / out of scope** → reply with the technical reason; don't implement.
- Copilot reviews are `COMMENTED` (non-blocking) — they don't gate GitHub's merge, but step 10
  stops on any open review thread, so every finding gets its in-thread reply (fixed, or declined
  with the reason) or is resolved before step 10. Address them honestly, not to satisfy an approval.

### 9. Diagnose a red CI check — flaky vs real  ⚠️ gotcha
A red required check is **not** automatically your bug. Find the real failure with
`gh run view <run-id> -R "$REPO" --log-failed`, filtered to the lines that matter (`##[error]`,
`Process completed with exit code`, `Test Run Failed`, `Error Message`, `Expected:`; drop the echoed
script lines, the ones with the cyan `[36;1m` prefix after the timestamp). Then ask: **does this
branch's diff touch the failing area at all?** If the only delta to the failing component is
unrelated, it's a **pre-existing flake** — `gh run rerun <run-id> -R "$REPO" --failed` reruns the
failed jobs; if it reproduces with no relevant code delta, file it (step 12) and tell the user, and
don't block this PR on someone else's flake. If the diff **does** touch it, fix the regression.

**A job that ended without its own verdict is not this diff's** until it ends the same way twice:
one that ran to its workflow's `timeout-minutes`, or has a step that ended `cancelled` with no user
named in its annotations, was stopped by its runner or by GitHub. Read why with
`gh api "repos/$REPO/check-runs/<job-id>/annotations" --jq '.[].message'`: a cancel by a person
names them (`The run was canceled by @<login>.`) and is not rerun; any other ending re-runs that job
with `gh run rerun --job <job-id> -R "$REPO"`, and a second identical ending is judged by the drill
above. Do not cancel a check in progress to rerun it early: no read through `gh` tells a hung job
from a slow one before its `timeout-minutes`.

### 10. Merge — only when CLEAN + green
**Under merge answer merge, read the range first.** An explicit merge lands every commit of the
range on the default branch, so read `git --no-pager log --format='%h %aN %s' "origin/$DEFAULT..HEAD"`.
A range whose commits are each a coherent step with a clean subject lands as it is. A range
carrying fix-up, review-round or work-in-progress commits is compressed to its arc: commits
grouped by risk surface, not necessarily one, and only a single-author range, so compression
never folds one person's commits into another's. The compression rewrites a branch origin has, so
the hard gate says who may authorize it. It takes two calls around the session's own step:

`pwsh <ouro>/bin/Compress-Branch.ps1 prepare`

It refuses a branch origin lacks, a shared branch (step 2's two signs), local commits origin lacks
and a range with more than one author, then names a backup branch and prints `backup:`, `lease:`,
`base:` and `author:`. Compose the arc on the base: `git reset <base>` leaves the changes in the
working tree; commit them back in groups, each with a clean subject and `--author="<the author it
printed>"`. Then:

`pwsh <ouro>/bin/Compress-Branch.ps1 push -Backup <the backup it printed>`

It refuses a dirty tree, a HEAD whose tree differs from the backup's (`git reset --hard <backup>`
goes back), a HEAD not built on the base, or a commit with another author, then pushes with the
lease pinned to the backup's commit. The checks must pass on the new head before the merge: back
to step 7.

Then one call merges, with every guard the merge needs, and judges the result by GitHub's answer,
not by `gh`'s exit code:

`pwsh <ouro>/bin/Merge-PullRequest.ps1 -Pr $PR -Subject "type(scope): summary (#$PR)" -BodyFile <the merge body, written with the file-write tool> -Linear <yes|no> [-Merge squash|merge]`

`-Merge` defaults to step 0's merge answer; `-Linear yes` where step 0 found a document that
requires a linear history. The subject ends in `(#$PR)`, the subject the loop metrics count; the
body is the branch-wide summary with the follow-ups linked, not gh's concatenation of every commit
message. The script merges only a ready PR based on `$DEFAULT` that reads `MERGEABLE CLEAN` with
green checks, a `pull_request` run on its head and no review thread nobody answered (step 8's
reply or a resolve answers it; a later commit does not); its STOP line names the refusal, and its
help holds every guard. It pins the head, deletes the branch only when no open PR is based on it, lands more than one
author email by merge commit under either answer (two addresses for one person are a `.mailmap`
entry), never rebase-merges, and never falls back from a refused merge commit to a squash.
`Base branch was modified` is not a merge: the PR is still OPEN, so back to step 2, the gates when
the tree changed, step 4, step 7, then this call again. A read that fails after the merge stops
without calling the PR merged or not merged.

### 11. Delete the branch + worktree self-lock  ⚠️ gotcha
`gh pr merge -R "$REPO" --delete-branch` does the **server merge** fine but its **local** cleanup
step fails inside a worktree: `fatal: '<default>' is already used by worktree at <checkout>`. The
merge still happened. From the main checkout (`[repo].checkout`, `$CHECKOUT`), with **every** tool
shell moved out of the worktree first — an agent host holds several shells, each with its **own**
current directory, and one still inside is the lock — run the script below. A background command
started while the shell's current directory is inside a worktree begins with
`cd <absolute path outside .claude/worktrees/>` (the main checkout or the scratch directory), so it
never pins the worktree.

`pwsh <ouro>/bin/Remove-LandedBranch.ps1 -Pr $PR`

It confirms the PR reads MERGED and that no open PR is based on the branch, deletes the branch on
origin where origin still lists it, removes the worktree under `.claude/worktrees/` that holds the
branch and checks the directory is gone **from the filesystem** (`git worktree list` cannot see an
orphan a locked removal leaves), and deletes the local branch and each `backup/<branch>-*` branch
step 2 or 10 named. `STOP: left on disk` means a shell still sat inside: move it out and delete the
directory by hand, in **separate calls**, since one call that both changes directory and deletes
can be refused wholesale by a host's safety guard. A worktree a shell cannot leave is deferred:
tell the user the command; step 14's sweep reports it until it is gone.

### 12. File follow-ups
Every finding outside the diff takes the severity floor (contract §3) first. Below it, it is one
line — `path:line`, the evidence, the finding, this PR — in one comment per landing on the cleanup
issue for the area the finding's file belongs to (the landed issue's first area label where that is
not plain). Where no issue carries that title in any state (contract §3), you file it, with this
body word for word, so every ledger states the contract's threshold, not one a session improvised:

```text
The cleanup ledger for this area (contract §3: a finding outside the change under review that is below the severity floor goes here as one line, not as an issue of its own). Each landing appends one comment and never edits the body; the body changes only with contract §3's threshold. At about five unswept lines triage proposes one child issue that fixes them.
```

After the append, read that ledger's comments and count its unswept lines: one appended by a
landing comment that no sweep comment has claimed, where a sweep comment is the one triage posts on
approval, naming the child issue and the lines it took. At or past contract §3's sweep threshold,
name the ledger to the user as due, a sweep candidate for `/ouro:triage <ledger>`, next to the
PROMOTE candidates below. The count is your own reading — no marker, no fixed bullet shape, no
counting script.

Anything else you deferred (a flake from step 9, a "another day" refactor, a TODO you created):
open an issue with its **area** labels (`[labels].area`) + one type, a concrete repro/symptom, and
a back-reference to this PR, linked in the PR body / merge body so the trail is closed; a worktree
deferred in step 11 needs no issue, since step 14's sweep finds it. File to the agent-ready
contract **while the anchors are fresh** (one deliverable, symbol + verbatim-fragment anchors
`@ SHA`, a `Doc impact on close:` line), carrying `needs-triage`, or `needs-ruling` + the question
when it has an open design decision — never `agent-ready`. Name a follow-up that qualifies (no open
design decisions) to the user as a PROMOTE candidate for `/ouro:triage <N>`.

### 13. Refresh the queue's anchors
1. Run `pwsh <ouro>/bin/Test-AgentReadyAnchors.ps1` once, in report mode — not `-Comment` and not
   `-Demote`: a fragment a landing split is a refresh, not a demotion. It runs with `$CHECKOUT`
   (step 11) as the working directory, on `$DEFAULT` after `git -C "$CHECKOUT" fetch origin` and
   `git -C "$CHECKOUT" merge --ff-only "origin/$DEFAULT"` (a fetch alone moves only the
   remote-tracking ref), never from the worktree the session sits in. If the checkout is on another
   branch, or the merge cannot fast-forward, run nothing and tell the user why: a run against an
   older tree reports anchors this landing did not split.
2. Read the files this PR changed: `gh pr view "$PR" -R "$REPO" --json files`.
3. A finding on an anchor line names the file it cites; one that names no file is placed by its
   line in the issue body. Each finding on an anchor citing a file this PR changed goes to
   `/ouro:triage <N>`, named to the user the way step 12 names a PROMOTE candidate: triage
   re-verifies the anchors and proposes the mechanical refresh for the owner's approval, so the
   next wave does not start from a dead anchor. A finding on an anchor this PR did not touch is
   not this landing's: report it to the user and leave it.

### 14. Sweep finished branches
From the main checkout (`$CHECKOUT`, as in step 11), run `pwsh <ouro>/bin/Invoke-BranchSweep.ps1`
in report mode and relay its report to the user. It names every local branch, origin branch and
`.claude/worktrees/` worktree whose PR is merged or closed, however that happened — this run, the
GitHub UI, another device, a run that stopped early — and every one it skips, with why; its help
lists the skip reasons. The PR decides, not git: a squash-merged branch is never "merged" to
`git branch --merged`. A merged PR's head reads as finished even when the branch lives on, so a
branch the sweep must never remove belongs in `[repo].protected_branch_prefixes`. Report mode lists
everything `-Delete` would remove: read it before any `-Delete`, and run `-Delete` only when the
user asks. `left on disk` is step 11's lock; move every shell out and remove the directory by hand.

## Quick gotcha index

| Symptom | Cause → fix |
|---|---|
| Branch or target has a protected prefix | Not this skill's to land → step 1's script refuses; the repo's release procedure owns it |
| Copilot reviewer never appears | `--add-reviewer Copilot` **and** the `requested_reviewers` REST call both no-op (REST returns a 200 with an empty array) → GraphQL `requestReviews` with `botIds: ["<[ship].copilot_bot_id>"]`; verify from the mutation's own response, never from the REST POST or the `reviewRequests` jq, which hides bots |
| GraphQL rejects the bot id | binding value stale → rediscover from a reviewed PR's timeline (step 6), fix `[ship].copilot_bot_id` |
| `gh pr merge -R "$REPO"` errors `'<default>' is already used by worktree` | local cleanup step only; server merge succeeded → move **every** tool shell out of the worktree (each holds its own current directory), then step 11's script from the main checkout; it verifies on the filesystem, because `worktree list` cannot see an orphan; defer only when a shell cannot be moved |
| Remote branch survives a close with `--delete-branch` | close-time deletion silently no-ops (unlike merge-time) → verify `git ls-remote --heads origin refs/heads/<branch>`, `git push origin --delete refs/heads/<branch>` the survivors |
| Waiting on a check that never starts, or checks show `skipping` | CI is path-filtered; the check's paths weren't touched → trust what `gh pr checks -R "$REPO"` lists, step 7; `skipping` is not a failure |
| Required check red | grep `--log-failed`; check code delta in the failing area; rerun if it's a pre-existing flake |
| Job ended `cancelled`, or ran to its workflow's `timeout-minutes` | not yet this diff's → step 9's paragraph on a job that ended without its own verdict: read the annotations; re-run that job once, unless they name a person who cancelled it |
| Gate green locally, red in CI | you ran it before committing (it diffs committed state), or it's a CI-only analyzer → step 3 and the `[overlays].land` checklists |
| Green PR conflicts or breaks right at merge | branch was stale — CI validated the branch head, not branch+base → step 2: bring `origin/$DEFAULT` in the way the binding's sync answer says (merge by default) and re-validate BEFORE pushing |
| MERGEABLE + empty `gh pr checks -R "$REPO"` | CONFLICTING open dispatched no `pull_request` run; default-branch move made it MERGEABLE with zero PR runs → step 10's script stops at its zero-run check (`gh run list -R "$REPO" --commit $HEAD_SHA --event pull_request` must be >= 1); re-push the head |
| PR closed itself after a branch rename | GitHub drops a PR whose head ref is renamed server-side → rename BEFORE opening the PR; if it already happened, open a successor PR and link the closed one |
| `Base branch was modified` from `gh pr merge -R "$REPO"` | the default branch moved after the script's read; the PR is still OPEN and nothing merged → step 2: sync, re-run the gates if the tree changed, push, wait for the new head's checks (step 7), run step 10's script again |
| Local `origin/$DEFAULT` missing the merge you just made | `gh pr merge -R "$REPO"` merges server-side only → `git fetch origin "$DEFAULT"` before branching off or comparing against it |
