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

The end-to-end "feature branch → default branch" flow, with the gotchas that
bite each time baked in. Run it top to bottom; each step says **why** so you
can adapt when reality differs.

Throughout: `$REPO` = `[repo].slug`, `$DEFAULT` = `[repo].default_branch`,
`$PR` is the PR number, `$BRANCH` is the feature branch. The batch variant is
`/ouro:land-batch`.

**Every `gh` call names the repository** — `-R "$REPO"`, and `gh api` its explicit
`repos/$REPO/…` path, `$REPO`'s owner and name as GraphQL variables, or a node id
this skill read from a call that named the repository. A bare call addresses whatever
gh picks from the clone's remotes, which in a fork's clone is the parent: this skill
would open and merge PRs on the upstream while `/ouro:execute` read the issue from
the fork. Only a call that addresses no repository (`gh auth status`) stays bare.

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the
marketplace checkout.

## 0. Bind — and refuse what is not yours to land

Read `.claude/ouro.toml` at the repo root (`[repo].checkout` if set, else the cwd; read it
via `python3 <ouro>/bin/ouro-binding.py get repo.checkout` — a gitignored `ouro.local.toml`
overlay may supply it; the key is optional, and `no such key` on stderr at exit 1 is that read's
answer for unset). No binding, or `python3 <ouro>/bin/ouro-binding.py check` fails → **refuse
to run**; say why. Then:

- **Protected prefixes are absolute.** If `$BRANCH` *or the target* starts with
  any entry of `[repo].protected_branch_prefixes`, stop. Those follow the
  repo's own release procedure — never this skill. Name the prefix that
  matched; do not offer a workaround.
- Note `[ship].review` (step 6 switches on it) and `[overlays].land` (step 3
  reads it).

**How this repo lands: two reads, three answers each.** Step 2 brings the branch up to date by
the sync answer, and step 10 lands it by the merge answer. Each is one dotted `get` of the binding
tool, `ship.sync` and `ship.merge`. Those two answer from a `[ship].landing` preset where the
binding declares only that, while a read of the whole `[ship]` table, or of `ship.landing`,
answers what the file holds. A value is the answer. `no such key` at exit 1 means nothing is
declared: the run takes the default (merge to sync, squash to land) and says so in one line. Any
other failure stops the run, since a read that did not answer is never taken for an undeclared key.
```bash
OUT=$(python3 <ouro>/bin/ouro-binding.py get ship.sync 2>&1); RC=$?
case "$RC $OUT" in
  "0 merge"|"0 rebase") echo "ship.sync: $OUT" ;;
  "1 "*"no such key"*) echo "ship.sync: not declared - step 2 merges, the default" ;;
  *) echo "STOP: ship.sync did not read (exit $RC): $OUT"; exit 1 ;;
esac
OUT=$(python3 <ouro>/bin/ouro-binding.py get ship.merge 2>&1); RC=$?
case "$RC $OUT" in
  "0 squash"|"0 merge") echo "ship.merge: $OUT" ;;
  "1 "*"no such key"*) echo "ship.merge: not declared - step 10 squashes, the default" ;;
  *) echo "STOP: ship.merge did not read (exit $RC): $OUT"; exit 1 ;;
esac
```
Carry the two answers, or the default each took, into steps 2, 4 and 10: a shell keeps no variable
between calls.

**The repository's own landing rule.** Read the documents `[authority].local` names for one thing:
a statement of how a branch is brought up to date or how a pull request lands. Where they are
silent, or agree with the two answers, go on. Where one contradicts them, **refuse before step
1**. Examples: a document forbids merge commits on a branch while the sync answer is merge, or it
requires preserved commits while the merge answer is squash. Name both sides: the document and its
sentence, and the key with its value or the default it took. Then hold. Add no note to the PR body
and pick no side; the owner changes the binding or the document. A document that requires a linear
history agrees with a squash answer only while the range has one author, because step 10's
authorship exception lands a range with more than one author email by merge commit. Note whether
such a document exists (`LINEAR=yes`, else `no`) for step 10's block. That block reads the authors
of the head the checks saw, so it stops in this case before it merges.

The git mechanics below, how step 2 syncs and how step 10 lands, are the binding's choice and the
defaults behind it, not law.

## The one hard gate

Pushing to `$DEFAULT` is gated by `[ship].policy` and by the user. A user
request to "land / merge / ship this branch" **is** the authorization to merge
**this** PR — it does not authorize unrelated default-branch pushes. If the
user only said "open a PR", or `[ship].review` resolves to "stop at the PR"
(step 6), stop after step 5 and don't merge.

A rewrite of a branch origin already has, by step 2's rebase or step 10's compression, is
authorized by the same request to land this branch, under the backup and lease rules there, and
by nothing less. The unattended `trivial` loop does not carry it (contract §5): a trivial run that
would have to rewrite a pushed branch stops at the PR.

## Preconditions

- Working tree clean, and you're on the **feature branch** (not `$DEFAULT`).
- **The branch name satisfies the repo's naming policy** (a `*` gate or CI
  check enforces it where the repo has one). A worktree created without `-b` sits on a
  `worktree-*` local branch: rename **before** pushing or opening the PR
  (`git branch -m worktree-x feature/x`). Renaming the **remote** branch after
  the PR exists **closes the PR** — GitHub's branches/rename API does not
  retarget an open PR despite its docs; you'd be opening a successor PR and
  re-requesting reviews.
- `gh auth status` is logged in.
- **Reproduce the CI-only gates locally before pushing** (step 3). Don't push
  a red PR you could have caught locally.

## The sequence

### 1. Verify state
```bash
git rev-parse --abbrev-ref HEAD              # confirm feature branch
git status -s                                # confirm clean
git --no-pager log --oneline "$DEFAULT"..HEAD  # the commits you're landing
```

### 2. Sync with the default branch  ⚠️ don't skip
CI runs on the branch head **as-is**, and GitHub only surfaces staleness at
merge time (`mergeStateStatus` = `DIRTY`/`BEHIND`) — so a green PR can still
be built against a base it never compiled with. Bring the default branch in
FIRST, so CI validates the combined result, the way step 0's sync answer says.

**Sync answer merge, or none declared:**
```bash
git fetch origin "$DEFAULT"
git --no-pager log --oneline HEAD..origin/"$DEFAULT"   # what the base gained since branching
git merge origin/"$DEFAULT" --no-edit
```

**Sync answer rebase:** the block fetches and shows what the base gained. Where the base gained
nothing, it does nothing more. Otherwise it first checks, where origin already has the branch, for
the three readable signs of a branch someone else is working on; a lease does not cover that case,
so any one sign stops the run and names itself, and the decision is the user's. Then it names a
backup branch at the current tip and rebases onto the default branch's remote tip. Only then does
it print origin's tip, or `none`, for step 4's lease. A run that rewrote nothing prints no lease,
and step 4 pushes plainly, which refuses to overwrite a commit origin holds.
```bash
REPO=<[repo].slug>; DEFAULT=<[repo].default_branch>; BRANCH=<the feature branch>
: "${REPO:?}" "${DEFAULT:?}" "${BRANCH:?}"
stop() { echo "STOP: $*"; exit 1; }
git fetch origin "$DEFAULT" || stop "cannot fetch origin/$DEFAULT"
LS=$(git ls-remote --heads origin "refs/heads/$BRANCH") || stop "cannot list $BRANCH on origin"
LEASE=${LS%%[[:space:]]*}
git --no-pager log --oneline HEAD..origin/"$DEFAULT"   # what the base gained since branching
git merge-base --is-ancestor "origin/$DEFAULT" HEAD && { echo "the base gained nothing - no rewrite, so step 4 pushes plainly, without a lease"; exit 0; }
if test -n "$LEASE"; then
  git fetch origin "refs/heads/$BRANCH" || stop "cannot fetch origin's $BRANCH"
  git merge-base --is-ancestor "$LEASE" HEAD \
    || stop "shared branch: origin's $BRANCH holds a commit the local branch lacks ($LEASE) - the rewrite is yours to decide"
  N=$(gh pr list -R "$REPO" --base "$BRANCH" --state open --json number --jq length) || stop "cannot count the PRs based on $BRANCH"
  test "$N" = 0 || stop "shared branch: an open PR is based on $BRANCH ($N of them) - the rewrite is yours to decide"
  HERE=$(git rev-parse --show-toplevel)
  OTHER=$(git worktree list --porcelain | awk -v here="worktree $HERE" -v ref="branch refs/heads/$BRANCH" '/^worktree /{w=$0} $0==ref && w!=here {print substr(w,10)}')
  test -z "$OTHER" || stop "shared branch: $BRANCH is checked out in another worktree ($OTHER) - the rewrite is yours to decide"
fi
BACKUP="backup/$BRANCH-$(date +%Y%m%d%H%M%S)"
git branch "$BACKUP" HEAD || stop "cannot create the backup branch $BACKUP"
echo "backup: $BACKUP at $(git rev-parse --short HEAD)"
git rebase "origin/$DEFAULT" || stop "the rebase stopped (git's message is above) - resolve, build, test, then git rebase --continue; or abandon it with git rebase --abort, and print the lease by hand: ${LEASE:-none}"
echo "lease: ${LEASE:-none}"
```
If the incoming delta includes **code** (not only docs), re-run the gates for
the overlapping areas (step 3) before pushing. A conflict here is the cheap
place to find it — resolve, build, test, then continue. Under a rebase, `git rebase --abort`
abandons it and returns to the tip the backup holds. A finished rebase that proves wrong goes back
with `git reset --hard <backup>`. The backup is a local branch; step 11 deletes it once the PR has
landed.

A sync is read against the branch, not trusted because git took it. The pre-sync tip is `HEAD^1`
after a merge and the backup branch after a rebase; the old base is `git merge-base <pre-sync tip>
origin/$DEFAULT`.

**What the base changed.** After a sync that brought anything in, read what the default branch
changed in each file the branch edits: `git diff <old base> origin/$DEFAULT -- <file>`, over the
files `git diff --name-only <old base> <pre-sync tip>` lists. Check each hunk against the branch's
own additions: a twin, copy or moved block of the changed text that the branch added keeps the old
text, and git merges it cleanly.

**An append-only file** (a changelog-like file every branch appends to) that conflicts under either
sync answer is resolved by keeping both sides' entries. The result is read against both sides'
entries before the sync is committed: a union can drop or resurrect lines.

**A conflicted sync is a change.** Before pushing, read-verify the resolution of every conflicted
file. Under a merge, every line each parent added since the merge base
(`git diff <merge base> <parent> -- <file>`) is still in the result, or the record says why not.
Under a rebase, `git range-diff --creation-factor=100 <old base>..<backup> origin/$DEFAULT..HEAD`
shows each replayed commit against its original, and a line it drops is a loss. The PR record
names the conflicted files and how the resolution was verified.

### 3. Run the gates locally
Run every `[[gate]]` whose `areas` covers what the branch touched, **plus every `areas = ["*"]`
gate** — the `*` gates are the ones that catch people who correctly decide the area gates don't
apply (a lone `.ps1` or `.md` change is still a change). A gate in that set that declares `paths`
then runs only when the branch's diff touches one of them: `/ouro:execute` §3 Gates holds that
narrowing, its fallbacks to the full area-selected set, how each entry reaches git, and the
evidence line for a skipped gate, and this step applies it as written there. Each gate's `run`
executes from the repo root, with any `<ouro>` in it replaced by the plugin root first.
A gate that replays a CI job runs with that job's `env:` set, read from the workflow. What the local
run cannot reproduce, such as the runner account's `PATH` or a layout the job builds, is named in
the gate evidence as CI's to check, never assumed green.

- A long gate is judged by two reads, never by the clock. One is whether the gate's own process —
  the process its command started to do the checking, not a helper it spawned that may outlive it —
  is still present. The other is its last progress marker, its newest output or log write, read
  again to see whether it moved. Either one present means the gate is alive and the run keeps
  waiting. With neither, the gate is wedged, and the run reports it at once: the finding names the
  gate command, the last progress marker it read and the absence of the gate's process. A wedged
  gate is neither a pass nor a fail, and its evidence goes where gate results already go. No
  duration, no timeout, no polling interval. A fresh core file, one written after the gate started,
  says a process the gate depends on died: it may mark the gate wedged while the gate's own process
  is still present. The finding then names the core file beside the gate command and the last
  progress marker. The run waits for the gate within its own turn: it runs the gate in the
  foreground, or blocks on a gate it started in the background, re-issuing the blocking call until
  the gate ends. It never ends its turn while a gate it started still runs, to wait for a
  completion notice or a scheduled wake-up: a headless session that ends its turn exits, and
  nothing reads the gate's result.
  A gate the run cannot finish here, wedged or unable to run, is covered only by
  a CI leg the PR's own check list shows ran, the leg that runs that gate's job:
  `gh pr checks <PR> -R <slug>` once the PR is open, where a path-filtered leg reads `skipping` and
  covers nothing. With no such leg, the record names the gate uncovered.
- **Commit first, then gate.** A gate that diffs committed state against a
  base ref reports on the *previous* commit when your edit is still unstaged,
  and hands you a false green. Use `$(git merge-base origin/$DEFAULT HEAD)`
  where a gate takes a base ref.
- **A green gate run stands when its tree still matches.** This governs this step on its own
  and its re-run under the review ordering (step 6's adversarial-review, below): it stands
  when `git rev-parse HEAD^{tree}` equals the tree that run gated, the base
  (`git merge-base origin/$DEFAULT HEAD`) is unchanged, and the step needs no gate that run
  did not include. Cite that run — its commit and the tree match — in the PR's Verification
  section instead of a new one. Any tree difference re-runs the gates, and so does a gate
  whose input is not the tree and the base, such as one that reads commit messages.
- **Read `[overlays].land` now.** Those checklists carry the repo's own
  gate gotchas — the CI-only style analyzers a plain build won't show, the
  language-agnostic checks that fire on docs, the build flags a standalone
  project needs. They add to this step; they never replace it.

### 4. Push the branch
Under sync answer merge, or none declared, and wherever step 2 printed no lease sha (`lease: none`,
or no lease line because nothing was rewritten):
```bash
git push -u origin "$BRANCH"
```
Where step 2 rebased a branch origin has and printed its tip as the lease, push with the lease
pinned to that tip. A push someone made since is then refused, not overwritten. The plain force
flag is never the push, in any step.
```bash
BRANCH=<the feature branch>; LEASE=<the tip step 2 printed after lease:>
: "${BRANCH:?}" "${LEASE:?}"
git push --force-with-lease="refs/heads/$BRANCH:$LEASE" origin "$BRANCH"
```

### 5. Open the PR
Open it as a draft, at the point `/ouro:execute` §4 names, and mark it ready once that
section's condition holds; under `[ship].review = "adversarial-review"`, step 6's
verdict is in the body before it is marked ready. Write a body that summarizes the **whole branch**
(not just the last commit) and links any follow-ups you'll file in step 12.
Labels: one or more from `[labels].area` plus the type the contract uses.
Set `BODY_FILE` to the file the body is written to: the session writes the body below to it with its
file-write tool and reads it back.
```markdown
## Merge danger
- **Door:** <two-way or one-way>: <one sentence of why>
- **Blast radius:** <what breaks if this is wrong, and for whom>: <one sentence of why>
- **Smoke by hand:** <what the merger checks in the running app, for code with no test host>
## Summary
<what + why, branch-wide, three lines or fewer>
## Shape
<one small diagram of what changed; drop this section for a change with no shape>
## Changes
- <bullet per logical commit>
<details><summary>Verification and evidence</summary>

- <local gate evidence, or "CI re-validates">
- Files another issue edits: <each match, the other issue, and why this deliverable needs the file or that it was reverted; or none>

<every evidence table and review record the run requires>

</details>

## Follow-up
- <#issue, filled in step 12>
```
```bash
BODY_FILE=<the file holding the body above>
: "${BODY_FILE:?}"
test -s "$BODY_FILE" || { echo "STOP: $BODY_FILE is empty or missing"; exit 1; }
gh pr create -R "$REPO" --base "$DEFAULT" --head "$BRANCH" --draft \
  --label <Area> --label <Type> \
  --title "type(scope): summary" \
  --body-file "$BODY_FILE"
```
The body opens with what a human needs to decide whether to read on. **Door** is two-way when a
revert undoes the change, one-way when it does not: a migration, data written or deleted, a
message sent outward, a published tag or release, a flag default consumers pick up, or a
consumer-contract change (a `**Consumer contract:**` changelog entry). **Blast radius** is what
breaks if the change is wrong, and for whom: this repository's gates, every consumer at the next
tag, an unattended run. **Smoke by hand** lists what the person merging checks in the running app
for code with no test host (`/ouro:review` step 5). The line is dropped when there is no such
code. **Shape** is a fenced `mermaid` block, or a fenced ASCII sketch where
Mermaid does not fit: a before/after of a flow or a step order, a sequence between parties, a tree
of new flags. A wording fix has no shape and drops the section. Verification and every evidence
table the run requires (measurement and mutation tables, gate output, the review record, the
REJECTED ledger and UNTESTED list) go inside `<details>`: off the first screen, still in the body
`/ouro:review`'s verification message reads. GitHub renders Markdown there only after a blank
line: keep the one after `</summary>`, and put one before each table. The `Fixes #<N>` line
`/ouro:execute` adds stays at the start of its own line, where the loop metrics match it. Door,
Blast radius and the diagram are claims like every other sentence in the body: a diagram showing a
step the diff does not add is a false claim.

**The `Review:` line** is the one line the loop metrics read for the change's review. It stands
alone, at the start of its own line, outside any fenced block (a reader skips fenced blocks before
matching), directly under the body's `Fixes` line or lines. It reads either `Review: rounds:2
findings:H0/M3/L4 false-sentences:1` (every field a decimal count, `false-sentences` also `unread`)
or `Review: none`, and this pattern, read multiline, matches it:
`^Review: (?:none|rounds:([0-9]+) findings:H([0-9]+)/M([0-9]+)/L([0-9]+) false-sentences:([0-9]+|unread))[ \t\r]*$`.
- **`rounds`** is the fix rounds the change took, counted as contract §7 counts them: a prose
  round is not counted. It is 0 when the first review needed no fix.
- **`findings`** is the `CONFIRMED` findings of the full review and of every verification, each
  counted once, at its severity: H is high, M is medium, L is low, as `REVIEWER-PROMPT.md` has the
  reviewer name it. `external-audit`'s blocker counts as H, major as M and minor as L. `OUTSIDE`
  findings and `STANDARDS` entries are not counted.
- **`false-sentences`** is the record sentences the record reading (`/ouro:review` step 7) found
  false, counted when found, so a sentence fixed afterwards still counts. It reads `unread` when
  no record reading ran.
- **`Review: none`** is for a change that neither `/ouro:review` nor `external-audit` reviewed.
  Copilot's comments carry no severity, so they are not counted.

`/ouro:review` step 6 fills the line and `/ouro:fuse` §8 fills a region's; both cite this
definition.

### 6. Request the review — switch on `[ship].review`

**`copilot`**  ⚠️ gotcha
Every `gh` and REST route fails, and **REST fails silently** — that's the
expensive part. `gh pr edit $PR -R "$REPO" --add-reviewer Copilot` prints the PR URL and
adds nobody; both REST spellings return **HTTP 200 with an empty
`requested_reviewers` array**:
```bash
# BOTH of these no-op. 200 OK, nobody attached, no error to notice.
gh api --method POST "repos/$REPO/pulls/$PR/requested_reviewers" -f "reviewers[]=Copilot"
gh api --method POST "repos/$REPO/pulls/$PR/requested_reviewers" -f "reviewers[]=copilot-pull-request-reviewer[bot]"
```
Use GraphQL `requestReviews` with **`botIds`** (not `userIds` — Copilot is a Bot
node, and `userIds` rejects it), `BOT_ID` = `[ship].copilot_bot_id`:
```bash
PR_NODE=$(gh pr view "$PR" -R "$REPO" --json id --jq .id)
gh api graphql -f query="
mutation { requestReviews(input: {pullRequestId: \"$PR_NODE\", botIds: [\"$BOT_ID\"], union: true}) {
  pullRequest { reviewRequests(first: 10) { nodes { requestedReviewer { __typename ... on Bot { login } } } } } } }"
```
Success looks like `{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}`
in that mutation's own response — **verify there, not from the POST you just
sent**, since the failing REST call also returns a healthy-looking 200.

Don't hardcode-and-hope on the bot id: if the binding's value is rejected,
rediscover it from any PR in the repo that already has a Copilot review, and
fix the binding:
```bash
gh api "repos/$REPO/issues/<some-reviewed-pr>/timeline" --paginate \
  --jq '.[] | select(.event=="review_requested") | .requested_reviewer | {login, node_id, type}'
```
Note the display login is `Copilot` while the API login is
`copilot-pull-request-reviewer` — and `copilot-swe-agent` is a **different bot**
(the coding agent, attached with `addAssigneesToAssignable`). Don't cross them.

Also don't verify via `gh pr view -R "$REPO" --json reviewRequests --jq '.[].login'` — that
jq path doesn't surface bot reviewers and looks empty even on success.

**`adversarial-review`** — run `/ouro:review` on the branch range
`$(git merge-base origin/$DEFAULT HEAD)..HEAD` **before the PR is marked ready** (it
may already be open as a draft, `/ouro:execute` §4), at the weight the class of change
selects (the review skill's Light mode states the rule). Fix what it proves, have it
verify the fix (its step 7), re-run step 3, then put the verdict in the PR's Verification
section — and, for a probe review, its REJECTED ledger and UNTESTED list — and mark it ready.

**`external-audit`** — the external critic `ship.external_cli` names. Run `/ouro:external-audit`
on the same range. When the CLI `ship.external_cli` names is not found, round 1 exits 1,
round 1 exits 3 twice, or the run may not call it (a policy or network rule forbids it),
`/ouro:review` takes `/ouro:external-audit`'s place, and the record says so (the availability rule
`/ouro:fuse` step 5 states).

**`none`** — stop at the PR. Human review only; the merge is theirs.

### 7. Watch CI in the background
Opening the PR triggers the checks. Find the head's `pull_request` runs the way step 10 counts
them, wait for them to appear, watch each, then list again for any run a watch outlived — one
the PR's `ready_for_review` event starts, or a workflow that started seconds later — and watch
those too, until a list brings no id not already watched. A list holds the newest run per
workflow and event on the head, picked the way `/ouro:land-batch` step 6 picks it: the workflow
by its `workflowDatabaseId`, not its name; the newest by the latest `createdAt`, the higher
`databaseId` on a tie. A run superseded by a newer run of the same workflow and event is not
judged: that is the run a workflow's `concurrency` cancel ends as `cancelled`. Judge the last
list's runs, each by its own conclusion, not by `gh run watch`'s exit code:
```bash
PR=<the PR number>; REPO=<[repo].slug>
: "${PR:?}" "${REPO:?}"
stop() { echo "STOP: $*"; exit 1; }
HEAD_SHA=$(gh pr view "$PR" -R "$REPO" --json headRefOid --jq .headRefOid) || stop "cannot read #$PR's head"
test -n "$HEAD_SHA" || stop "#$PR's head read empty"
# The newest run per workflow and event on the head: one id per line.
list_runs() {
  R=$(gh run list -R "$REPO" --commit "$HEAD_SHA" --event pull_request --limit 100 --json databaseId,workflowDatabaseId,event,createdAt \
    --jq '.[] | "\(.createdAt)\t\(.databaseId)\t\(.workflowDatabaseId)/\(.event)"') || return 1
  printf '%s\n' "$R" | sort -k1,1r -k2,2nr | awk -F '\t' 'NF == 3 && !seen[$3]++ {print $2}'
}
for i in 1 2 3 4 5 6; do
  RUNS=$(list_runs) || stop "cannot list #$PR's runs"
  test -n "$RUNS" && break
  test "$i" = 6 || sleep 10
done
test -n "$RUNS" || stop "no pull_request run for $HEAD_SHA after a minute - step 10's zero-run case: nothing is known to have built the head, push (or re-push) it and come back"
WATCHED=""
while true; do
  NEW=""
  for run in $(printf '%s\n' "$RUNS"); do
    case " $WATCHED " in *" $run "*) ;; *) NEW="$NEW $run" ;; esac
  done
  test -n "$NEW" || break
  for run in $(printf '%s\n' "$NEW"); do gh run watch "$run" -R "$REPO" --interval 60; WATCHED="$WATCHED $run"; done
  RUNS=$(list_runs) || stop "cannot list #$PR's runs"
done
BAD=""
for run in $(printf '%s\n' "$RUNS"); do
  STATE=$(gh run view "$run" -R "$REPO" --json status,conclusion --jq '"\(.status) \(.conclusion)"') || stop "cannot read run $run's status"
  read -r RSTATUS CONCLUSION <<< "$STATE"
  test "$RSTATUS" = completed || stop "run $run is still $RSTATUS, with no conclusion yet"
  case "$CONCLUSION" in success|skipped|neutral) ;; *) BAD="$BAD $run:$CONCLUSION" ;; esac
done
test -z "$BAD" || stop "run(s) concluded outside success/skipped/neutral:$BAD"
```
This skips `gh pr checks -R "$REPO" --watch`: while the head has no check yet, as when its only
run is queued with no jobs, that command exits 1 with `no checks reported`.

Which checks appear depends on the paths you touched — CI is path-filtered, so
**don't wait for a check that was never going to run**. The set is whatever
`gh pr checks -R "$REPO"` lists; entries showing `skipping` are normal, not failures; the
ones that gate the merge are the repo's required checks, and branch protection
(not this skill) names them. Some checks run on everything (naming policy,
hygiene, the `*` gates); the heavy build/test tiers run only when their area
changed.

### 8. Triage the review
When the review lands, apply review-reception discipline (superpowers
`receiving-code-review` if available) — **verify each comment against the code
before changing anything**, and never reply with performative agreement.

For each inline comment:
- **Confirm the premise in the actual code.** A reviewer reasons from the diff
  and is often right but sometimes assumes a reachable state that isn't. Read
  the cited file/line and the call sites before acting.
- **Valid + in scope** → fix it, commit, and have the fix commit verified
  (`/ouro:review` step 7). The round's commits are pushed together once that verification passes
  (`/ouro:execute` §4), not one push per finding. After the push, reply
  **in-thread** with what changed:
  ```bash
  : "${REPLY_FILE:?}"
  test -s "$REPLY_FILE" || { echo "STOP: $REPLY_FILE is empty or missing"; exit 1; }
  gh api "repos/$REPO/pulls/$PR/comments/<comment_id>/replies" \
    -X POST -F body=@"$REPLY_FILE"
  ```
  `REPLY_FILE` holds the reply, `Fixed in <sha>. <one line on the change>.`, written with the
  file-write tool.
  Get `<comment_id>` from
  `gh api repos/$REPO/pulls/$PR/comments --paginate --jq '.[] | {id,path,line}'`.
- **Valid, after `/ouro:review` has verified the change** → the fix above is the one extra
  round contract §7 allows. A valid finding after that round is answered on its thread with the
  reason and goes where the severity floor (contract §3) sends it, not into a further round.
- **Wrong / out of scope** → reply with the technical reason; don't implement.
- Copilot reviews are `COMMENTED` (non-blocking) — they don't gate GitHub's
  merge, but step 10 stops on any open review thread, so every finding gets its
  in-thread reply (fixed, or declined with the reason) or is resolved before
  step 10. Your job is to address them honestly, not to satisfy an approval.

### 9. Diagnose a red CI check — flaky vs real  ⚠️ gotcha
A red required check is **not** automatically your bug. Find the real failure,
then decide:
```bash
gh run view <run-id> -R "$REPO" --log-failed 2>&1 \
  | grep -aE "##\[error\]|Process completed with exit code|Test Run Failed|  Failed [A-Za-z0-9_]+ \[|Error Message|Expected:" \
  | grep -avE '[0-9]Z .\[36;1m'     # drop echoed script lines (the [36;1m cyan prefix after the timestamp)
```
Then ask: **does this branch's diff touch the failing area at all?** Compare the
last green commit to the red one. If the only delta to the failing component is
unrelated (docs, a comment, a different project), it's a **pre-existing flake** —
rerun just the failed jobs:
```bash
gh run rerun <run-id> -R "$REPO" --failed
```
If it reproduces with no relevant code delta, file it (step 12) and tell the
user; don't block this PR on someone else's flake. If the diff **does** touch
it, treat it as a real regression and fix it.

**A job that ended without its own verdict is not this diff's** until it ends the same way
twice. A job that ran to its workflow's `timeout-minutes`, or has a step that ended
`cancelled` with no user named in its annotations, was stopped by its runner or by GitHub. Read
why with `gh api "repos/$REPO/check-runs/<job-id>/annotations" --jq '.[].message'`. A cancel
by a person names them, as in `The run was canceled by @<login>.`; that run was cancelled on
purpose and is not rerun. Any other ending re-runs that job by its id:
`gh run rerun --job <job-id> -R "$REPO"`. If it ends the same way again, judge it by the drill
above: the diff's when the diff touches the step that hung, the runner's to file (step 12)
otherwise. Do not cancel a check in progress to rerun it early:
no read through `gh` tells a hung job from a slow one before its `timeout-minutes`.

### 10. Merge — only when CLEAN + green
**Under merge answer merge, read the range before the block.** An explicit merge lands every
commit of the range on the default branch, so read `git --no-pager log --format='%h %aN %s'
"origin/$DEFAULT..HEAD"`. A range whose commits are each a coherent step with a clean subject lands
as it is. A range carrying fix-up, review-round or work-in-progress commits is compressed to its
arc: commits grouped by risk surface, not necessarily one. Only a single-author range is compressed,
so compression never folds one person's commits into another's; a range with more than one author
lands as it is. The compression rewrites a branch origin has, so the hard gate says who may
authorize it. It compresses exactly the head the PR shows, so a local commit origin lacks stops it.
A range that lands as it is skips the two compression blocks and goes to the merge block. For
one that is compressed, this block first checks that, the same three signs as step 2, and names
a backup:
```bash
REPO=<[repo].slug>; DEFAULT=<[repo].default_branch>; BRANCH=<the feature branch>
: "${REPO:?}" "${DEFAULT:?}" "${BRANCH:?}"
stop() { echo "STOP: $*"; exit 1; }
git fetch origin "$DEFAULT" || stop "cannot fetch origin/$DEFAULT"
LS=$(git ls-remote --heads origin "refs/heads/$BRANCH") || stop "cannot list $BRANCH on origin"
LEASE=${LS%%[[:space:]]*}
test -n "$LEASE" || stop "origin has no $BRANCH - push it (step 4) and open the PR first"
git fetch origin "refs/heads/$BRANCH" || stop "cannot fetch origin's $BRANCH"
git merge-base --is-ancestor "$LEASE" HEAD \
  || stop "shared branch: origin's $BRANCH holds a commit the local branch lacks ($LEASE) - the rewrite is yours to decide"
test "$LEASE" = "$(git rev-parse HEAD)" || stop "the local $BRANCH has commits origin lacks - push them (step 4) and let the checks run, or drop them, first"
N=$(gh pr list -R "$REPO" --base "$BRANCH" --state open --json number --jq length) || stop "cannot count the PRs based on $BRANCH"
test "$N" = 0 || stop "shared branch: an open PR is based on $BRANCH ($N of them) - the rewrite is yours to decide"
HERE=$(git rev-parse --show-toplevel)
OTHER=$(git worktree list --porcelain | awk -v here="worktree $HERE" -v ref="branch refs/heads/$BRANCH" '/^worktree /{w=$0} $0==ref && w!=here {print substr(w,10)}')
test -z "$OTHER" || stop "shared branch: $BRANCH is checked out in another worktree ($OTHER) - the rewrite is yours to decide"
BASE=$(git merge-base "origin/$DEFAULT" HEAD) || stop "no merge base between origin/$DEFAULT and HEAD"
EMAILS=$(git log --no-merges --format='%aE' "$BASE..HEAD" | tr '[:upper:]' '[:lower:]' | sort -u)
test -n "$EMAILS" || stop "no author read from $BASE..HEAD - nothing to compress"
test -z "$(printf '%s\n' "$EMAILS" | sed -n 2p)" || stop "more than one author - the range lands as it is, uncompressed:"$'\n'"$EMAILS"
AUTHOR=$(git log -1 --no-merges --format='%aN <%aE>' "$BASE..HEAD")
BACKUP="backup/$BRANCH-$(date +%Y%m%d%H%M%S)"
git branch "$BACKUP" HEAD || stop "cannot create the backup branch $BACKUP"
printf 'backup: %s\nlease: %s\nbase: %s\nauthor: %s\n' "$BACKUP" "$LEASE" "$BASE" "$AUTHOR"
```
Then compose the arc on top of the base it printed. Run `git reset <base>`, which leaves the changes
in the working tree, and commit them back in groups, each with a clean subject and
`--author="<the author it printed>"`. Then this block checks the rewrite changed nothing but the
history and pushes it with the lease:
```bash
BRANCH=<the feature branch>; BACKUP=<the backup printed above>; LEASE=<the lease printed above>
BASE=<the base printed above>; AUTHOR="<the author printed above>"
: "${BRANCH:?}" "${BACKUP:?}" "${LEASE:?}" "${BASE:?}" "${AUTHOR:?}"
stop() { echo "STOP: $*"; exit 1; }
test -z "$(git status --porcelain)" || stop "the working tree is not clean - the arc is not all committed"
test "$(git rev-parse "HEAD^{tree}")" = "$(git rev-parse "$BACKUP^{tree}")" \
  || stop "the compressed tree differs from $BACKUP - nothing is pushed; go back with git reset --hard $BACKUP"
git merge-base --is-ancestor "$BASE" HEAD || stop "HEAD is not built on $BASE"
EMAIL=${AUTHOR##*<}; EMAIL=$(printf '%s' "${EMAIL%>}" | tr '[:upper:]' '[:lower:]')
test "$(git log --no-merges --format='%aE' "$BASE..HEAD" | tr '[:upper:]' '[:lower:]' | sort -u)" = "$EMAIL" \
  || stop "a compressed commit's author is not $AUTHOR - recommit it with --author"
git push --force-with-lease="refs/heads/$BRANCH:$LEASE" origin "$BRANCH" \
  || stop "the leased push was refused (git's message is above) - origin's $BRANCH moved since $LEASE; nothing is overwritten"
echo "compressed and pushed as $(git rev-parse HEAD) - back to step 7: the checks must pass on this head before the block below runs"
```
The block below pins the head the checks were read for, so a compressed head nobody built cannot
land.

Don't trust a background-watch exit code alone. **Confirm directly, in the call that
merges**: a shell keeps no variable between calls, so the block sets its own inputs and checks
them with `${VAR:?}`, and every other check ends the call with `exit 1` instead of printing and
going on. Feature branches land the way step 0's merge answer says: a squash (the default) or an
explicit merge commit. A range with more than one author lands by merge commit whatever the
answer (below). Give a clean subject and body, not gh's concatenation of every commit message. The
subject ends in `(#$PR)` either way, so the commit on the default branch's first parent carries
the subject the loop metrics count:
```bash
PR=<the PR number>; REPO=<[repo].slug>; DEFAULT=<[repo].default_branch>; BRANCH=<the feature branch>
MERGE_ANSWER=<step 0's merge answer: squash or merge, squash where none was declared>
LINEAR=<yes where step 0 found a document that requires a linear history, else no>
SUBJECT="type(scope): summary (#$PR)"
BODY_FILE=<a file holding the squash body, written with the file-write tool: the branch-wide summary; bullet the logical changes; link follow-ups>
: "${PR:?}" "${REPO:?}" "${DEFAULT:?}" "${BRANCH:?}" "${MERGE_ANSWER:?}" "${LINEAR:?}" "${SUBJECT:?}" "${BODY_FILE:?}"
stop() { echo "STOP: $*"; exit 1; }
test -s "$BODY_FILE" || stop "BODY_FILE ($BODY_FILE) is empty or missing - write the squash body with the file-write tool"
case "$MERGE_ANSWER" in squash) HOW=--squash ;; merge) HOW=--merge ;; *) stop "MERGE_ANSWER reads '$MERGE_ANSWER', not squash or merge" ;; esac
case "$LINEAR" in yes|no) ;; *) stop "LINEAR reads '$LINEAR', not yes or no" ;; esac
for i in 1 2 3 4 5; do
  read -r HEAD_SHA BASE STATE <<< "$(gh pr view "$PR" -R "$REPO" --json headRefOid,baseRefName,mergeable,mergeStateStatus --jq '"\(.headRefOid) \(.baseRefName) \(.mergeable) \(.mergeStateStatus)"')"
  case "$STATE" in *UNKNOWN*) test "$i" = 5 || sleep 10 ;; *) break ;; esac
done
test -n "$HEAD_SHA" || stop "cannot read #$PR's head, base and state"
test "$(gh pr view "$PR" -R "$REPO" --json isDraft --jq .isDraft)" = false \
  || stop "#$PR is still a draft, or its draft state did not read - mark it ready (gh pr ready) once /ouro:execute §4's condition holds"
test "$BASE" = "$DEFAULT" || stop "#$PR's base reads '$BASE', not $DEFAULT"
test "$STATE" = "MERGEABLE CLEAN" || stop "#$PR is '$STATE', not MERGEABLE CLEAN"
gh pr checks "$PR" -R "$REPO" || stop "gh pr checks exits $?, not 0 - 8 is pending; 1 with no checks reported means nothing built the head"
test "$(gh run list -R "$REPO" --commit "$HEAD_SHA" --event pull_request --json databaseId --jq length)" -ge 1 2>/dev/null \
  || stop "no pull_request run reads for $HEAD_SHA - nothing built it"
OPEN=$(gh api graphql --paginate -F number="$PR" -f owner="${REPO%/*}" -f name="${REPO#*/}" -f query='query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { author { login } reviewThreads(first: 100, after: $endCursor) {
  pageInfo { hasNextPage endCursor } nodes { isResolved comments(first: 1) { totalCount nodes { author { login } path url } } } } } } }' \
  --jq '.data.repository.pullRequest as $p | $p.reviewThreads.nodes[] | select(.isResolved == false and .comments.totalCount == 1)
  | .comments.nodes[0] | select(.author.login != $p.author.login) | "\(.author.login) \(.path) \(.url)"') \
  || stop "cannot read #$PR's review threads"
test -z "$OPEN" || stop "#$PR has a review thread nobody answered - reply in it (step 8) or resolve it:"$'\n'"$OPEN"
git fetch -q origin "$DEFAULT" "refs/pull/$PR/head" || stop "cannot fetch origin/$DEFAULT and #$PR's head"
git cat-file -e "$HEAD_SHA^{commit}" || stop "#$PR's head $HEAD_SHA did not fetch"
EMAILS=$(git log --no-merges --format='%aE' "origin/$DEFAULT..$HEAD_SHA" | tr '[:upper:]' '[:lower:]' | sort -u)
test -n "$EMAILS" || stop "no author read from origin/$DEFAULT..$HEAD_SHA - the range is empty or holds only merge commits"
FIX="change that setting or the binding"
if test -n "$(printf '%s\n' "$EMAILS" | sed -n 2p)"; then
  test "$HOW" = --squash && { echo "authorship exception: #$PR's range has more than one author - it lands by merge commit, not squash:"; git log --no-merges --format='%aN <%aE>' "origin/$DEFAULT..$HEAD_SHA" | sort -u; }
  FIX="allow merge commits, or, where the addresses are one person's, add a .mailmap entry for them"
  test "$LINEAR" = no || stop "#$PR's range has more than one author, so it would land by merge commit, and a document step 0 read requires a linear history - add a .mailmap entry where the addresses are one person's, or the owner changes the document"
  HOW=--merge
fi
DEL=; test "$(gh pr list -R "$REPO" --base "$BRANCH" --state open --json number --jq length)" = 0 && DEL=--delete-branch
gh pr merge "$PR" -R "$REPO" $HOW --match-head-commit "$HEAD_SHA" $DEL --subject "$SUBJECT" --body-file "$BODY_FILE"
VIEW=$(gh pr view "$PR" -R "$REPO" --json state,mergeCommit --jq '"\(.state) \(.mergeCommit.oid // "")"') \
  || stop "cannot read #$PR after the merge - check it by hand"
read -r AFTER MERGE <<< "$VIEW"
test "$AFTER" = MERGED && test -n "$MERGE" || stop "#$PR reads '$VIEW', not MERGED with a merge commit - after 'Base branch was modified' the default branch moved and nothing merged: go back to step 2, then run this block again; under --merge, a refusal (gh's message is above) is the repository disallowing merge commits or a ruleset requiring linear history: $FIX; never land it as a squash instead"
echo "#$PR merged as $MERGE"
```
- **A PR still in draft stops here.** `/ouro:execute` §4 opens it as a draft; mark it ready
  (`gh pr ready -R "$REPO" "$PR"`) once that section's condition holds, then run the block
  again. A run that stopped before marking it ready (a STOP, an interruption) leaves the PR a
  draft for this step to finish.
- **Based on `$DEFAULT`, MERGEABLE and CLEAN, and nothing else.** A PR retargeted since step 5
  would merge into a branch step 0's protected-prefix check never saw. `gh pr merge -R "$REPO"` itself
  refuses only `BLOCKED`, `BEHIND` and `DIRTY`, so `UNKNOWN` and `UNSTABLE` would merge.
  `UNKNOWN` is routine while GitHub recomputes after the base moved: the read re-polls it five
  times, ten seconds apart, and anything but `MERGEABLE CLEAN` stops. A failed read stops too.
- **Every listed check passes or skips: `gh pr checks -R "$REPO"` exits 0.** Exit 8 is a pending check —
  let step 7's watch finish, or run step 7's block again when it already has, then run this block
  again. Exit 1 with `no checks reported` is the
  zero-run case below; any other failing check is step 9's.
- **At least one `pull_request` run for the head the block read.** Filter by commit and event
  **on the server** (`--commit` / `--event`). Do not `--branch` plus a client-side `jq` on the
  default 20-row page: older runs on a busy branch push the matching sha off the page and the
  count is a false 0. `--commit` also finds the sha when `$BRANCH` does not match the run's
  head (fork PR). A `CONFLICTING` head dispatches **no `pull_request` event at all** — not
  even a workflow with no `paths:` filter runs — so `gh pr checks -R "$REPO"` reports no checks and
  exits 1. A PR that was conflicting at open and became `MERGEABLE` because the default branch
  moved still has zero runs while the mergeable check passes. Zero runs (or a failed count)
  means nothing is known to have built the head: push (or re-push) it so CI runs, then come back.
- **No open review thread.** A thread that is not resolved, was opened by anyone but the PR's
  author, and has no reply stops the merge, whoever the reviewer is. Step 8's in-thread reply
  answers it, whether the finding was fixed or declined with the reason, and so does resolving
  the thread; a commit pushed after the comment does not. The read is GraphQL, since a REST
  comment list carries no resolved state, and `--paginate` reads every page; `--jq` runs once
  per page, so the filter judges each thread by its own comment count and adds nothing up
  across pages. Issue comments and review summary bodies open no thread. A failed read stops.
- **The merge answer picks the flag, and authorship overrides it one way.** Squash is
  `--squash` and merge is `--merge`, both with the head pin and every guard above. A range with
  more than one author lands by `--merge` under either answer, with its commits as they are,
  because a squash would reassign one person's work to whoever pressed the button. The range
  runs from the default branch's remote tip to the head, with merge commits excluded and the
  mailmap applied, counted by author email with case folded. Under a squash answer this is the only way an explicit
  merge happens, and the block prints that it fired and the authors it read. Two addresses for
  one person are a mailmap entry, not a second author. The test is that log read and nothing
  else. Where step 0 found a document that requires a linear history (`LINEAR=yes`), the
  block stops there instead, before any merge, and names the case.
- **No rebase-merge, under any answer.** `gh pr merge`'s rebase flag rewrites every commit, so
  the default branch would stop pointing at the commit the checks verified. No path here runs
  it.
- **A refused merge commit stops the run.** The repository disallows merge commits, or a
  ruleset requires linear history. The stop carries gh's message and names the fix: that setting
  or the binding, or, where the range has more than one author, the setting or a `.mailmap` entry.
  The run never falls back to a squash.
- **`--match-head-commit` pins the head the checks were read for.** A push to the PR branch
  after the read makes `gh pr merge -R "$REPO"` refuse, instead of landing a head no check built.
- **`--delete-branch` only when no open PR is based on `$BRANCH`.** Deleting a branch another
  open PR targets closes or strands that PR; a failed count adds no delete.
- **Judge the merge by `gh pr view -R "$REPO"`, not by `gh`'s exit code.** The local cleanup of
  `gh pr merge -R "$REPO"` can fail after the server merge succeeded (step 11); MERGED with a
  merge commit is the server's answer. A read that fails after the merge is not an answer: it
  stops without calling the PR merged or not merged.
- **`Base branch was modified` is not a merge.** gh prints it when the default branch moved
  between the block's read and the merge. The PR is still OPEN, and the read-back stops. Go
  back to step 2: fetch, sync, re-run the gates when step 3's rule says the tree changed,
  push, wait for the new head's checks (step 7), then run this block again. Never treat that
  error as a merge that landed.

### 11. Delete the branch + worktree self-lock  ⚠️ gotcha
`gh pr merge -R "$REPO" --delete-branch` does the **server merge** fine but its **local**
cleanup step fails inside a worktree:
`fatal: '<default>' is already used by worktree at <checkout>`. The merge
still happened — confirm and finish the cleanup by hand:
```bash
PR=<the PR number>; REPO=<[repo].slug>; BRANCH=<the feature branch>
: "${PR:?}" "${REPO:?}" "${BRANCH:?}"
stop() { echo "STOP: $*"; exit 1; }
STATE=$(gh pr view "$PR" -R "$REPO" --json state --jq .state) || stop "cannot read #$PR's state"
test "$STATE" = MERGED || stop "#$PR is '$STATE', not MERGED"
test "$(gh pr list -R "$REPO" --base "$BRANCH" --state open --json number --jq length)" = 0 \
  || stop "an open PR is based on $BRANCH, or the count failed - the branch stays"
git ls-remote --exit-code --heads origin "refs/heads/$BRANCH"
case $? in
  0) git push origin --delete "refs/heads/$BRANCH" ;;
  2) echo "$BRANCH is already deleted on origin - nothing to do" ;;
  *) stop "cannot list $BRANCH on origin" ;;
esac
```
- **MERGED, or no delete.** On GitHub, deleting an open pull request's head branch closes the
  pull request. Anything but MERGED, or a read that fails, stops before `git push`.
- **Not a branch another open PR is based on**, the same rule as step 10's `--delete-branch`;
  a failed count keeps the branch too.
- **Delete only a branch origin still lists.** `git ls-remote --exit-code` exits 2 when nothing
  matches: the merge's `--delete-branch` already took it, and there is nothing to do. Any other
  failure stops. The full `refs/heads/` name matters in both commands: a bare name also
  matches `<prefix>/<branch>` in the listing, and a tag of the same name makes the delete
  ambiguous.

Worktree cleanup needs **no process with its current directory inside the worktree** — that is
the lock, and an agent host holds several shells, each with its **own** current directory, so
moving one out frees nothing while another still sits there. A `git worktree remove` that hits
the lock deletes the tracked files, fails on the directory, and **deregisters the entry anyway
before it exits**: `git worktree list` then comes back clean and `git worktree prune` finds
nothing to do while the directory is still on disk.

In order: move **every** tool shell out of the worktree; then, from the main checkout
(`[repo].checkout`, `$CHECKOUT`), run both:
```bash
git -C "$CHECKOUT" worktree remove .claude/worktrees/<worktree-dir>
git -C "$CHECKOUT" branch -D "$BRANCH"   # -D: a squash-merged branch is never "fully merged"; a merge-committed one is, and -D is harmless there
```
Delete each backup branch step 2 or step 10 named the same way, once the PR reads MERGED.
then verify the directory is gone **from the filesystem** — `[ -d <dir> ]` false, `Test-Path
<dir>` `$false`, or the worktrees directory no longer listing it. Assert the absence, not the
presence: a bare `test -d` succeeds when the directory is still there, which is the opposite of
what this step confirms. `git worktree list` cannot see an orphan.

Keep the move and the delete in **separate calls**: one call that both changes directory and
deletes can be refused wholesale by a host's safety guard, read as a removal targeting a system
path — seen where the move went to a drive root.

- **Session launched in the main checkout** — it created the worktree itself, and typically ran a
  build shell inside it: move every shell out, then run both in-band.
- **Session launched inside the worktree**: move every shell out and run both in-band. Defer only
  when a shell genuinely cannot be moved, and then tell the user the two commands. The deferred
  worktree is not lost: step 14's sweep reports it on every run until it is gone.

### 12. File follow-ups
Every finding outside the diff takes the severity floor (contract §3) first.
Below it, it is one line — `path:line`, the evidence, the finding, this PR —
in one comment on the cleanup issue for the area the finding's file belongs
to, or the first area label of the issue you landed where that is not plain.
One comment per landing, and where no issue carries that title in any state
(contract §3), you file it, with this body word for word, one paragraph, so every ledger
states the contract's threshold rather than one a session improvised:

```text
The cleanup ledger for this area (contract §3: a finding outside the change under review that is below the severity floor goes here as one line, not as an issue of its own). Each landing appends one comment and never edits the body; the body changes only with contract §3's threshold. At about five unswept lines triage proposes one child issue that fixes them.
```

After the append, read that ledger's comments and count its unswept lines: one appended
by a landing comment that no sweep comment has claimed, where a sweep comment is the one
triage posts on approval, naming the child issue and the lines it took. At or past
contract §3's sweep threshold, name the ledger to the user as due, a sweep candidate for
`/ouro:triage <ledger>`, next to the PROMOTE candidates below. The count is your own
reading — no marker, no fixed bullet shape, no counting script.

Anything else you deferred (a flake from step 9, a "another day" refactor, a
TODO you created): open an issue with its **area** labels (`[labels].area`) +
one type, a concrete repro/symptom, and a back-reference to this PR. Link them
in the PR body / merge body so the trail is closed. A worktree deferred in step
11 needs no issue: step 14's sweep finds it.

File to the agent-ready contract **while the anchors are fresh** (one
deliverable, symbol + verbatim-fragment anchors `@ SHA`, a `Doc impact on
close:` line) — you just landed the branch, so you know the exact symbols. File
it carrying `needs-triage`, or `needs-ruling` + the question when it has an open
design decision — never `agent-ready`. Name a follow-up that qualifies (no open
design decisions) to the user as a PROMOTE candidate for
`/ouro:triage <N>`.

### 13. Refresh the queue's anchors
1. Run `pwsh <ouro>/bin/Test-AgentReadyAnchors.ps1` once, in report mode — not `-Comment` and
   not `-Demote`: a fragment a landing split is a refresh, not a demotion. It runs in a tree
   whose HEAD is the landed `origin/$DEFAULT`: `$CHECKOUT` (step 11), on `$DEFAULT`, after
   `git -C "$CHECKOUT" fetch origin` and `git -C "$CHECKOUT" merge --ff-only "origin/$DEFAULT"`.
   A fetch alone moves only the remote-tracking ref, and the local branch would still be the
   tree before this landing. The gate takes the repository root from its working directory, so
   it runs with `$CHECKOUT` as that directory (`Push-Location`, then `Pop-Location`), never from
   the worktree the session sits in. If the checkout is on another branch, or the merge cannot
   fast-forward, run nothing and tell the user why: a run against an older tree reports
   anchors this landing did not split.
2. Read the files this PR changed: `gh pr view "$PR" -R "$REPO" --json files`.
3. A finding on an anchor line names the file it cites. One that names no file is placed by
   its line in the issue body. Each finding on an anchor citing a file this PR changed goes
   to `/ouro:triage <N>`, named to the user the way step 12 names a PROMOTE candidate. Triage
   re-verifies the anchors and proposes the mechanical refresh for the owner's approval,
   recorded in its `**Triage**` comment, so the next wave does not start from a dead anchor. A
   finding on an anchor this PR did not touch is not this landing's: report it to the user
   and leave it.

### 14. Sweep finished branches
From the main checkout (`$CHECKOUT`, as in step 11), run the sweep in report mode and relay its
report to the user:
```bash
pwsh <ouro>/bin/Invoke-BranchSweep.ps1
```
It names every local branch, origin branch and `.claude/worktrees/` worktree whose PR is merged or
closed, however that happened — this run, the GitHub UI, another device, a run that stopped early —
and every one it skips, with why: the default branch, the main checkout's branch, a branch checked
out in a worktree outside `.claude/worktrees/`, a protected prefix, where the filesystem folds case
(Windows, macOS) a local branch whose name differs only in case from another's, an open PR, no PR,
commits beyond the PR's head, a worktree with no branch checked out, a worktree whose branch has no
commit yet, a worktree (and its branch) that holds the current directory, holds another worktree, is
locked (`git worktree lock`), is prunable (its directory is gone: `git worktree prune` is yours to
run), has uncommitted changes, or has an ignored directory holding a nested git repository, an
origin branch another open PR is based on, a PR state it could not read (a failed read, or 1000 PRs
or more under one head name). A directory under `.claude/worktrees/` that git does not list is
reported as an orphan and left. The PR decides, not git: a squash-merged branch is never "merged" to
`git branch --merged`.

Ignored files do not keep a worktree: it is made per issue, so they are disposable, and
`git worktree remove` deletes them with it — a `.env`, a local overlay, the `bin/__pycache__/` a
gate leaves behind. The worktree's `finished:` and `deleted:` lines list every ignored path, as
`git status --ignored` names them (an ignored directory is one path), so the report shows all
that `-Delete` takes. The one exception: an ignored directory holding a nested git repository (a
`.git` directory or file right inside it) keeps the worktree and its branch, since its commits
are work no PR carried. Uncommitted changes always keep a worktree.

A merged PR's head reads as finished even when the branch lives on — a long-lived branch once
merged as a PR's head — so a branch the sweep must never remove belongs in
`[repo].protected_branch_prefixes`. Report mode lists everything `-Delete` would remove: read
it before any `-Delete`.

Run it with `-Delete` only when the user asks. It removes in step 11's order — worktree, local
branch, origin branch — each ref only at the tip it checked. Origin's delete is leased, so a tip
that moved on origin is refused and reported `failed:`. The local branch is re-read just before
`git branch -D`, so a move before that re-read is refused the same way; the re-read and the
delete are two process spawns, and a concurrent write to a branch checked out nowhere in that
window is not caught. Each worktree is checked after its removal. One git still lists was
refused (a worktree with a submodule, say) and is reported `failed:`, whole and registered.
`left on disk` is step 11's lock, a shell whose current directory is inside the worktree: git
has already deregistered that worktree, so a later sweep reports the directory as an orphan and
leaves it. Move every shell out and remove it by hand; the next `-Delete` takes the branch. A
worktree locked with `git worktree lock` is never removed: it is skipped, still registered. Exit
1 means the binding failed its check (its errors are printed; nothing else ran), the fetch or
another git read failed, a PR state could not be read, or a removal failed; a skip alone is no
failure.

## Quick gotcha index

| Symptom | Cause → fix |
|---|---|
| Branch or target has a protected prefix | Not this skill's to land → step 0 refuses; the repo's release procedure owns it |
| Copilot reviewer never appears | `--add-reviewer Copilot` **and** the `requested_reviewers` REST call both no-op (REST returns a 200 with an empty array) → GraphQL `requestReviews` with `botIds: ["<[ship].copilot_bot_id>"]` |
| `reviewRequests` jq looks empty after adding Copilot | jq doesn't surface bots → verify from the GraphQL mutation's own response, never from the REST POST |
| GraphQL rejects the bot id | binding value stale → rediscover from a reviewed PR's timeline (step 6), fix `[ship].copilot_bot_id` |
| `gh pr merge -R "$REPO"` errors `'<default>' is already used by worktree` | local cleanup step only; server merge succeeded → delete remote branch by hand; move **every** tool shell out of the worktree (each holds its own current directory), then `worktree remove` from the main checkout; verify on the filesystem, because `worktree list` cannot see an orphan; defer only when a shell cannot be moved |
| Remote branch survives a close with `--delete-branch` | close-time deletion silently no-ops (unlike merge-time) → verify `git ls-remote --heads origin refs/heads/<branch>`, `git push origin --delete refs/heads/<branch>` the survivors |
| Waiting on a check that never starts | CI is path-filtered; the check's paths weren't touched → trust what `gh pr checks -R "$REPO"` lists, step 7 |
| Checks show `skipping` | path-filtered, not a failure |
| Required check red | grep `--log-failed`; check code delta in the failing area; rerun if it's a pre-existing flake |
| Job ended `cancelled`, or ran to its workflow's `timeout-minutes` | not yet this diff's → step 9's paragraph on a job that ended without its own verdict: read the annotations; re-run that job once, unless they name a person who cancelled it |
| Gate green locally, red in CI | you ran it before committing (it diffs committed state), or it's a CI-only analyzer → step 3 and the `[overlays].land` checklists |
| Green PR conflicts or breaks right at merge | branch was stale — CI validated the branch head, not branch+base → step 2: bring `origin/$DEFAULT` in the way the binding's sync answer says (merge by default) and re-validate BEFORE pushing |
| MERGEABLE + empty `gh pr checks -R "$REPO"` | CONFLICTING open dispatched no `pull_request` run; default-branch move made it MERGEABLE with zero PR runs → step 10: `gh run list -R "$REPO" --commit $HEAD_SHA --event pull_request` must be >= 1; re-push the head |
| PR closed itself after a branch rename | GitHub drops a PR whose head ref is renamed server-side → rename BEFORE opening the PR; if it already happened, open a successor PR and link the closed one |
| `Base branch was modified` from `gh pr merge -R "$REPO"` | the default branch moved after the block's read; the PR is still OPEN and nothing merged → step 2: sync, re-run the gates if the tree changed, push, wait for the new head's checks (step 7), run step 10's block again |
| Local `origin/$DEFAULT` missing the merge you just made | `gh pr merge -R "$REPO"` merges server-side only → `git fetch origin "$DEFAULT"` before branching off or comparing against it |
