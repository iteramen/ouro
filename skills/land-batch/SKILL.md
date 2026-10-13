---
name: land-batch
description: >
  Land multiple reviewed PRs onto the default branch in one pass
  ("merge-close-clean N, N, N"): squash each branch locally, validate the
  COMPOSED state, then land — each PR through GitHub's own squash-merge,
  checked against its validated squash commit, or one push of the default
  branch when GitHub cannot reproduce the composition. Merge (or, on the push
  path, close) the PRs, delete branches, clean worktrees. Use when the owner
  names a set of PRs/issues to land together — that naming is the push or merge
  authorization for exactly that set. Closes the wave by filing each landed PR's
  follow-ups and sending the anchors the landing split in queued issues to triage.
---

# Land a PR batch — `/ouro:land-batch`

One validated composition for several reviewed PRs, landed so GitHub records each
PR as merged. The single-PR flow is `/ouro:land`; this drill exists because a batch
has a failure mode no PR's own CI ever saw — the **composition**. Steps 2–4 squash
every PR locally and gate the composed tree; step 5 lands those same squashes on
GitHub one PR at a time, each merge checked against its local squash commit (5a), or,
for a set GitHub cannot reproduce, pushes the default branch once (5b). `$DEFAULT` =
`[repo].default_branch` and `$SLUG` = `[repo].slug` throughout.

**Every deterministic step is one subcommand of one script**, `<ouro>/bin/Invoke-LandBatch.ps1`
(`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the marketplace checkout). It
runs from the repo root of the checkout the batch runs in, reads the binding through
`ouro-binding.py` beside it, names the binding's repository on every `gh` call (a bare call
addresses whatever gh picks from the clone's remotes, which in a fork's clone is the parent), and
keeps whatever a later step needs in the batch files in that checkout's git directory
(`land-batch.tsv`, `land-batch.route`, `land-batch.landed`, `land-batch.stop`): nothing is carried
between calls. Every refusal is one `STOP: <why, and what to do>` line and exit 1; the script's
help holds each subcommand's guards. What stays in this document is what the script cannot carry:
the order, the judgment, and the sharp edges.

## 0. Bind — and refuse what is not yours to land

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 check`

The script refuses without a binding, or with one that fails `check`. **Protected prefixes are
absolute**: if any branch in the set, or the target, starts with an entry of
`[repo].protected_branch_prefixes`, stop and name it — those follow the repo's own release
procedure, never this skill. Read `[overlays].land` now: it names the files this repo treats as
**tail-append hazards** (step 3) and any gate gotchas.

**What the binding and the default branch accept decides whether and how the batch lands, and it
has to be found here** — not after every squash, every composed-state gate and the first merge or
the push have already run. `check` reads `ship.merge` (a `[ship].landing` preset answers it; the
sync key is never read, since the batch brings no branch up to date), then the branch's rulesets,
classic branch protection (which the rules endpoint does not show), a merge queue from either kind,
and the repository's squash setting; it prints each reading and ends in one `verdict:` line:

| Reading | Verdict |
|---|---|
| squash merges refused by a ruleset, or `allow_squash_merge` false | **STOP: the batch is not for this branch.** Both paths land squash commits. (`allow_squash_merge` reads `null` without admin access; then a refusal surfaces at the first merge, before anything lands.) |
| `ship.merge: merge`, declared or by a `rebase-merge` or `merge-merge` preset | **STOP**: a branch whose PRs land as merge commits is not this drill's business; the set goes to `/ouro:land` per PR. Nothing has been squashed, pushed, merged or closed. |
| a merge queue ("Require merge queue" is a classic setting too) | **STOP: hand the set to the queue.** `gh pr merge` enqueues instead of merging, so no merge can be checked against its squash, and a direct push is refused. |
| a rules or merge-queue read that failed for any reason but the plan | **per PR through `/ouro:land`**: the batch cannot tell whether a queue or a required pull request is there, and landing blind is not safe. A `403 … Upgrade to GitHub Pro` is nothing to find: rulesets and, on a private repository, classic protection are gated by plan. |
| strict checks: a ruleset's up-to-date policy, classic `strict`, or classic protection whose details did not read | **a set of two or more PRs goes to `/ouro:land` per PR**: 5a lands the first and finds the next `BEHIND`; 5b's push is refused, since the checks never ran on the squashes. |
| required pull requests or status checks, by either kind, or classic details that did not read | **5a only**: a set that routes to 5b goes to `/ouro:land` per PR instead. |

Classic protection whose details did not read is `protection.enabled` with the `/protection` read
refused (it needs admin access; a non-admin gets `404`): the script says so and treats up-to-date
branches, pull requests and checks as all required.

## Preconditions

- Owner named the set ("merge-close-clean 270, 323, 359"). Numbers may be issues
  or PRs — resolve each to its PR + branch and **echo the resolved set** before
  merging (a typo'd number resolves to something real; check it exists and is
  part of the current work before treating it as intended). That echo, and the
  owner's naming, is the push or merge authorization — for exactly that set.
- Main checkout, on `$DEFAULT`, clean tree, `git fetch origin` fresh, local `$DEFAULT` at
  `origin/$DEFAULT` — the first squash then sits on the pre-batch tip. The batch files live
  in the git directory of the checkout the drill runs from; any other worktree has its own
  and sees no batch.
- **Main checkout on another branch: the drill runs from a batch worktree** on the local
  `$DEFAULT` branch. From the main checkout, after the fetch,
  `pwsh <ouro>/bin/Invoke-LandBatch.ps1 worktree` checks `.claude/worktrees/` is ignored, adds
  `.claude/worktrees/land-batch` on `$DEFAULT` and fast-forwards it to `origin/$DEFAULT`; a
  worktree a stopped run left registered is removed (the end of this skill) before it runs again.
  Every call from step 1 through step 7 then runs from that worktree, step 4's gates from its
  root, so every batch file resolves in its one git directory. Re-read the binding there: step 0
  read the main checkout's, which its branch may have changed, and step 4's `[[gate]]` list and
  `[overlays].land` are the default branch's. Git refuses the worktree while the main checkout is
  itself on `$DEFAULT`, the form above. **Never a detached worktree:** 5b's push sends the local
  `$DEFAULT` branch, never HEAD, so a detached worktree's squashes do not land. Commit message
  files stay outside the worktree's tree.
- Every PR in the set: CI green, review triaged.

## 1. Start the batch; merge order

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 start` — once, before anything else: it clears any previous
batch's files.

Base-before-stacked: a PR whose base is another feature branch lands **after**
its base. Branches that append to the same file tails (the `[overlays].land`
tail-append hazards — locale tables, release notes) will conflict with each
other — order doesn't prevent that, step 3 handles it. A stacked PR
does not land through step 2's squash at all: once its base's squash is in the
batch, that squash replays the base over lines already written and conflicts, so
the stacked PR lands as its own diff (step 3's `stacked`).

**A stacked PR routes the set to the local push drill (5b)**, and `add` records that route
when a PR's base is not `$DEFAULT`. Its GitHub base is its base's feature branch until that
branch is deleted and GitHub retargets the PR, and a retarget after
`gh pr merge --delete-branch` deletes the ref through the API is not reliable — the
dependent PR can be closed instead.

## 2. Per PR: check the head, squash, commit, record

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 add -Pr <n> -Branch <head branch> -Message <file>`

One call per PR, in merge order, with that PR's commit message written to a file — subject
`type(scope): summary (#PR)`, body ending with `Closes #N`. The call refuses, before the squash, a
malformed message, a head that moved since the fetch (a PR nobody validated: refetch and start the
batch over), a base that is neither `$DEFAULT` nor a branch already in the batch, and a range with
no author (already landed, or merge commits only: drop it) or more than one; its STOP line says
which.

The commit carries the author of the PR's own range, never the session's, mailmap applied, with
the committer the session's identity: a squash would reassign a second author's work, so a
two-author PR lands on its own through `/ouro:land` and the batch is recomposed without it. A
squash that stages nothing records no row; a failed commit undoes the squash, and the same call
runs again once the cause is fixed.

Each row of `land-batch.tsv` is `PR BRANCH HEAD_SHA SQUASH`, in merge order: 5a merges
exactly that head with that commit's subject and body, and checks the merge against exactly
that commit; 5b pushes the commit itself, so either path lands the `(#PR)` subject. The first
row's `SQUASH` sits on the pre-batch tip.

`Closes #N` in the body makes GitHub close the issue when the commit lands on
`$DEFAULT` — close nothing by hand, but **verify** afterwards. A PR with no issue
omits it.

## 3. Conflict recipe  ⚠️ the batch's sharp edges

**A conflict resolved by hand routes the set to the local push drill (5b):** a server
squash of the PR head cannot reproduce a hand resolution. `add` stops with
`conflicts with the batch` and leaves the squash in the tree:

- **Check ALL conflict codes**: `git status --short` — `UU` (both modified) but
  also `AA` (both added: a file two branches introduced). A `grep ^UU` missed a
  real `AA` conflict once; use `grep -E '^(UU|AA|AU|UA)'`.
- **Tail-append conflicts** (the files `[overlays].land` names): union-resolve
  (ours then theirs), then **dedupe** — a stacked branch carries its base's
  additions, so blind union duplicates them. Dedupe by the entry's key (e.g.
  `data name=` for a resx-style table).
- **Hand-resolved XML gets a real parse before commit** — load the file with an
  XML parser and assert key uniqueness and, for paired locale files, entry-count
  sync. A line-based fix once left a dangling `</data>` that only the build
  caught.
- Add/add of an identical-purpose file (stacked base + extension): take the
  superset side (`git checkout --theirs`), then diff-sanity it.

Once every path is resolved and staged, record the PR and the route:

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 record -Pr <n> -Branch <head branch> -Message <file>`

It refuses while a path is unresolved, validates the message and the head as `add` does, reads the
range's one author the same way, and commits the staged resolution with the message — or takes a
commit already made with that subject, when that commit's author is the range's (else it names the
`--amend --author` to run). It records the row and `conflict resolved by hand`, and the stacked
route where the base is not `$DEFAULT`.

### A PR stacked on another branch in the set

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 stacked -Pr <n> -Branch <head branch> -Message <file>`

A PR whose base is another feature branch already in the batch needs neither `add`'s squash nor a
hand resolution: its branch carries its base's commits, so a squash would conflict, while its own
diff applies cleanly on top. `stacked` dry-runs that diff against the batch as GitHub merges, stops
if it conflicts, applies it with the range's author, and records the row and the stacked route. A
PR whose base is `$DEFAULT` is refused: it is `add`'s. A stale base reports the base's own commits
as a second author when it was force-pushed since the PR was cut: fetch and rebase the stacked
branch onto its base, then run it again.

A stacked PR has already routed the set to 5b, which pushes the commit itself and closes each PR
by its row's `HEAD_SHA`, so a commit made by applying a diff lands exactly like a squash.

## 4. Validate the COMPOSED state — before anything lands

No PR's CI built this tree. Non-negotiable: run **every `[[gate]]` the binding declares**
(`python3 <ouro>/bin/ouro-binding.py get gate` lists them), whatever its `areas`, from the repo
root, on the composed tree, after the last squash commit; any `<ouro>` in a `run` is replaced by the
plugin root first, and a base-ref gate takes `origin/$DEFAULT`, the pre-batch tip, since nothing
has landed yet. A gate that was green on one PR's head says nothing about the composition. The run
follows the Gate environment, What CI checks and Two reads sections of
`skills/execute/references/dispatch-rules.md`.

A local commit is cheap to amend; a broken default branch is not.
Fix-in-place and amend the offending squash commit (unlanded history — amend is
fine here, and it keeps the author git already recorded). **An amend routes the set to the
local push drill (5b):** a server squash of the PR head lands the unfixed composition, and
5a's tree check would not see it — it compares each merge with the squash recorded before the
amend, so the unfixed tree matches. Record the route:

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 amend -Note "<what the amend fixed>"`

5a also refuses, before its first merge, a HEAD that is not the last recorded squash or
that no longer contains every recorded one — an amend nobody recorded stops there.

**On 5a, no other commit goes on local `$DEFAULT` from here until `reset`.** A release
commit, a version bump or any other follow-up is made after the batch has landed, on top
of `origin/$DEFAULT`: `reset` ends 5a by resetting local `$DEFAULT` to what GitHub merged, and
refuses while a commit made in between sits there, since the reset would drop it. On 5b such a
commit can ride the single push.

## 5. Land — route first

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 route`

The set lands on GitHub (5a) unless one of these happened, and any one sends the whole
set to the local push drill (5b) — or, where step 0's verdict refuses 5b on this branch, to
`/ouro:land` per PR. Each is recorded as a line of `land-batch.route`, and `land` refuses while
that file holds one:

- a PR in the set is stacked on another feature branch (`add` and `stacked` record it);
- step 3 resolved a conflict by hand (`record` records it);
- step 4 amended a squash commit (`amend` records it);
- a path two or more PRs in the set change carries a `merge=` attribute (`route` records it).

`route` prints `route: 5a`, or `ROUTE: 5b -` with the lines. A `merge=` driver (`union` on a
tail-append file, a custom driver) runs where the merge runs: locally it resolves the file, while
GitHub's squash may conflict on the same inputs. That difference is measured with `git merge-tree`
as a stand-in for GitHub's merge engine, not on GitHub itself; the route takes the safe side of it.
The route compares the set's PRs with each other; a PR whose own change meets a base change made
since it forked is what `land`'s first-call mergeability read catches instead.

5a can otherwise reproduce the validated tree because a squash commit carries no branch parent, so
a clean squash of the same head onto the same tree yields the same tree; `land` confirms that per
merge rather than trusting it.

### 5a. GitHub path — merge each PR, check each merge

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 land` — once per PR. It takes the next recorded PR that has
not landed, so it cannot run out of order; `STOP: every recorded PR has landed` is the signal to go
on to `reset`. Every stop it records in `land-batch.stop` refuses every later call.

- **Before the first merge, the batch must still be the one step 4 validated**: HEAD is the last
  recorded squash and contains every recorded one, and every PR in the set reads `MERGEABLE`
  against `$DEFAULT` and is not a draft. That read catches a conflict with a base change made
  since the PR forked, which a local `merge=` driver resolved and GitHub's squash would not.
- **The tip must be the previously landed commit before each merge**, the pre-batch tip for the
  first PR: a moved default branch stops the batch, since `--match-head-commit` pins only the PR
  head, never the base.
- **Based on `$DEFAULT`, `MERGEABLE` and `CLEAN`, and nothing else.** `gh pr merge` itself refuses
  only `BLOCKED`, `BEHIND` and `DIRTY`, so `UNKNOWN` and `UNSTABLE` would merge; `UNKNOWN` is
  routine right after the previous merge moved the base, and is re-polled.
- **No open review thread on a PR still to land**, read before each merge by `/ouro:land` step
  10's rule, so a finding posted while the earlier PRs merged still stops.
- **Never `--auto` or `--admin`.** `--auto` merges later, outside the check; `--admin`
  bypasses the rules the batch exists to pass.
- **`--delete-branch` only when no open PR is based on `$BRANCH`.**
- **Judge the merge by `gh pr view`, not by `gh`'s exit code**: the local cleanup of
  `gh pr merge` can fail after the server merge succeeded (`/ouro:land` step 11). A read that
  fails is not an answer: it stops the batch without recording the PR as landed or as not landed.
- **Check the merge commit, not the tip.** Its parent must be the previously landed commit and
  its tree must equal `SQUASH`'s tree; comparing `origin/$DEFAULT`'s tip instead misreads a push
  that landed after the merge as a mismatch.

**Stop and report.** A moved tip, a batch that is no longer the validated one, a PR not
MERGEABLE, not based on `$DEFAULT`, not MERGEABLE and CLEAN, not MERGED or not readable (gh's
`Base branch was modified` is this case: the default branch moved, nothing merged, and the PRs not
on the report's origin list go to a new batch), an open review thread or a failed thread read, or
a parent or tree mismatch stops the batch, and `land-batch.stop` keeps every later call from
merging. Report what landed from `origin`, not from `land-batch.landed` alone — a read that failed
after a successful merge leaves that PR out of the file:

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 report` — the stop, then what is on `origin/$DEFAULT` since
the batch began. A PR whose merge mismatched is already merged, so it is on that list, with the
mismatch.

**Local `$DEFAULT` has then diverged from `origin/$DEFAULT`:** it still holds every local
squash, so `git status -sb` reads ahead — and behind as well once something merged or
someone pushed, when `git pull --ff-only` fails. The way back to a clean tree is `reset`. A
continuation re-squashes the PRs that are not on the report's `origin` list from there and
re-gates: steps 1–4, as a new batch. What to do about the rest of the set is the owner's call.

After the last merge — or after a stop — replace the local squash commits with what GitHub
merged:

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 reset` — it fetches and resets local `$DEFAULT` to
`origin/$DEFAULT`. It refuses while local `$DEFAULT` holds a commit after the last squash, which the
reset would drop, and names the way to carry it: `git rebase --onto origin/$DEFAULT <last squash>`.
After a clean batch the reset is tree-equal to the local squashes by the checks above.
Then clean up as in 5b's last four bullets (surviving remote branches, worktrees and
local branches, the sweep, the `Closes #N` issues).

### 5b. Local push drill — push once, then close each PR

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 push`

**Before `git push`, every PR in the set must still be the head step 2 squashed, with no open
review thread.** The call reads each PR in `land-batch.tsv`: its head against its row's `HEAD_SHA`,
whether it is still a draft, and its review threads by `/ouro:land` step 10's rule; a moved head, a
draft, an open thread or a failed read stops it, and nothing lands or closes. A moved head is step
8's usual path for a valid finding, and what was pushed is not in the squash, so recompose as the
non-fast-forward bullet below says. Then it pushes the local `$DEFAULT` once and checks that origin
now holds exactly what was pushed.

- **A `GH013` or `GH006` rejection means step 0 missed a rule** — one it could not read, one added since,
  or a bypass the pusher does not have. `GH013: Repository rule violations found` names a
  ruleset; `GH006: Protected branch update failed` names classic protection. Nothing is lost:
  the push stops there, and a close runs only for a commit it finds on `origin`. The
  set has to land through `/ouro:land` per PR instead.
- **A non-fast-forward rejection (`[rejected] … (fetch first)` or `(non-fast-forward)`) means
  `$DEFAULT` moved since the batch was composed** — step 4's gates validated a composition on
  the old tip, not what would land. The landing stops there and nothing is closed. Recompose
  on the new tip: first `reset`, which reads `land-batch.tsv` before `start` clears it. A commit it
  names after the last squash — a release commit that was to ride the push — is not carried: reset
  to that squash, run `reset` again, and re-make the commit on top of the new batch. Then steps 1–4
  again, as a new batch, from the fetched `origin/$DEFAULT`.

Then one call per PR. `-Sha` is that PR's squash commit as pushed — `git log --oneline
"origin/$DEFAULT"` shows it by its `(#PR)` subject:

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 close -Pr <n> -Sha <its squash commit on origin>`

The close refuses unless that commit is on `origin/$DEFAULT` and the PR's head is still the
`HEAD_SHA` step 2 squashed — a commit pushed to the PR after its squash never landed. It closes the
PR with the landing comment and `--delete-branch`.

- Close a **stacked** PR before its base (avoids GitHub retarget noise).
- **`--delete-branch` on close silently no-ops** (unlike on merge): verify with
  `git ls-remote --heads origin refs/heads/<branch>` and `git push origin --delete refs/heads/<branch>`
  the survivors.
- Worktrees from the main checkout, every tool shell moved out of them first:
  `git worktree remove .claude/worktrees/<n>`; then `git branch -D <branch>` (squash means `-d`
  refuses — `-D` is correct). Verify on the filesystem, per `/ouro:land` step 11.
- Then the sweep, as `/ouro:land` step 14 runs it: report mode from the main checkout
  (`pwsh <ouro>/bin/Invoke-BranchSweep.ps1`), relayed to the owner; `-Delete` only when the owner
  asks. It also finds what this cleanup missed or deferred.
- Verify the `Closes #N` issues actually closed.

## 6. Watch default-branch CI

`pwsh <ouro>/bin/Invoke-LandBatch.ps1 watch`

It hands the right commits to `<ouro>/bin/Watch-PullRequestChecks.ps1 -Sha`, the watch `/ouro:land`
step 7 describes: the newest run per workflow and event across the commits, each judged by its
own conclusion.

- **5a** — every merge commit `land` recorded. Each merge is its own push, and a workflow with a
  `paths:` filter runs for a merge only when that merge's own diff touches its list, so the last
  merge may not trigger that workflow at all: each workflow's newest run per event across the
  batch's commits built the validated composition. After a stop at a parent or tree mismatch, the
  mismatched merge commit landed and is recorded, so it is watched with the matching ones before
  it. After a stop whose read failed after its merge, that commit is not in `land-batch.landed`:
  run the watch script by hand with every batch commit on the report's `origin` list, up to and
  including the newest one.
- **5b** — `origin/$DEFAULT` right after the push: that one push's diff covers the whole batch.
- **Nothing landed** (a stop before the first merge) — there is nothing to watch; it says so.

No run after a minute means no workflow was triggered for them (path filters, as `/ouro:land`
step 7 notes); a run that concluded outside success, skipped and neutral is `/ouro:land` step 9's
to judge.

The drill isn't done at the last merge or the push, nor when `$DEFAULT` is green: step 7 closes
the wave.

## 7. Close the wave: follow-ups, then the queue's anchors

Run it before the batch worktree goes, since it reads the batch files there. Nothing landed (step 6
watched nothing): skip it.

A batch lands several reviewed PRs at once, and two things each landing leaves are still open when
`$DEFAULT` goes green: each review's findings outside its diff, and whatever its PR deferred; and
the lines the landing split or changed that queued `agent-ready` issues quote as anchors.

1. **Follow-ups, per landed PR.** Run `/ouro:land` step 12 once for each PR this batch landed. On
   5a, that is each batch PR on `origin/$DEFAULT`; after a stop, each batch PR on the stop report's
   `origin` list, since a read that failed after its merge leaves a PR out of `land-batch.landed`.
   On 5b, it is each PR of `land-batch.tsv`, since the one push landed every one, whether or not
   its close went through. Its rules apply as written there: the severity floor, one comment on the
   area's cleanup issue for what falls below it, an issue for anything else deferred, filed to the
   agent-ready contract and never `agent-ready`. One comment per landed PR, not one per wave: the
   findings are that PR's review, and a comment mixing several reviews loses which PR a line came
   from. A PR an `/ouro:e2e` run handed to this batch may have filed its follow-ups already, when
   that run stopped at its PR (e2e §5): file nothing again for a PR whose line is already on the
   cleanup issue or whose e2e report lists its follow-ups. A PR the batch did not land files
   nothing here; its own landing does.
2. **Then the queue's anchors.** Run `/ouro:land` step 13 as it is written there, once for the
   wave rather than once per PR: in the checkout the batch ran in (the batch worktree, where
   there is one), whose `$DEFAULT` `reset` or 5b's push left at `origin/$DEFAULT`, and
   against the files of every PR this batch landed (`land-batch.tsv`, or the stop report's
   `origin` list after a stop) in place of the one PR step 13 names.

**A batch worktree goes last** — after step 7, or once the owner ends a stopped batch: steps 6 and
7 read the batch files in its git directory, and a stopped batch runs `reset` there first, so no
squash stays on the local `$DEFAULT` branch the next batch starts from. An untracked file left in
it makes the removal refuse. Move **every** tool shell out of it, then from the main checkout
(`[repo].checkout`, else the directory the `worktree` call ran from)
`git -C <that checkout> worktree remove .claude/worktrees/land-batch`, and verify the directory is
gone from the **filesystem**, as `/ouro:land` step 11 says. Its branch is `$DEFAULT` and is never
deleted; a PR branch the main checkout itself is on is deleted once the checkout has switched away.
