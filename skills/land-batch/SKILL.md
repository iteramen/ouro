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

**Every bash block below is one call, and nothing survives between calls.** A shell
variable set in one block is gone in the next, so whatever a later step needs is written
to the batch files in the git directory (`land-batch.tsv`, `land-batch.route`,
`land-batch.landed`, `land-batch.stop`), and each block sets its own inputs and checks
them with `${VAR:?}`, which ends the call before it reaches `gh` when a value is empty.
Every other check is a gate too: on failure it ends the call with `exit 1` instead of
printing and going on.

**Every `gh` call names the repository** — `-R "$SLUG"`, and `gh api` its explicit
`repos/$SLUG/…` path or `$SLUG`'s owner and name as GraphQL variables. A bare call
addresses whatever gh picks from the clone's remotes, which in a fork's clone is the
parent, so the batch would merge PRs on a repository nobody composed or validated.

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the
marketplace checkout.

## 0. Bind — and refuse what is not yours to land

Read `.claude/ouro.toml` at the repo root (`[repo].checkout` if set, else the cwd; read it
via `python3 <ouro>/bin/ouro-binding.py get repo.checkout` — a gitignored `ouro.local.toml`
overlay may supply it; the key is optional, and `no such key` on stderr at exit 1 is that read's
answer for unset). No binding, or `python3 <ouro>/bin/ouro-binding.py check` fails → refuse.
**Protected prefixes are absolute**: if any branch in the set, or the target,
starts with an entry of `[repo].protected_branch_prefixes`, stop and name it —
those follow the repo's own release procedure, never this skill. Read
`[overlays].land` now: it names the files this repo treats as **tail-append
hazards** (step 3) and any gate gotchas.

**How the binding lands a PR decides whether this batch may run at all.** One dotted `get` of
`ship.merge` reads it: that read answers from a `[ship].landing` preset where the binding
declares only the preset, while a read of the whole `[ship]` table answers what the file holds.
The sync key is never read — the batch brings no branch up to date.

```bash
OUT=$(python3 <ouro>/bin/ouro-binding.py get ship.merge 2>&1); RC=$?
case "$RC $OUT" in
  "0 squash") echo "ship.merge: squash" ;;
  "0 merge")  echo "ship.merge: merge" ;;
  "1 "*"no such key"*) echo "ship.merge: not declared - the batch lands squash commits, as a binding without the key does" ;;
  *) echo "ship.merge: no answer (exit $RC): $OUT" ;;
esac
```

**What the default branch accepts decides whether and how the batch lands, and it has to
be found here** — not after every squash, every composed-state gate and the first merge or
the push have already run. Read before squashing anything:

```bash
SLUG=<[repo].slug>; DEFAULT=<[repo].default_branch>
: "${SLUG:?}" "${DEFAULT:?}"
gh api "repos/$SLUG/rules/branches/$DEFAULT" --jq '{
  pull_request:   [.[] | select(.type == "pull_request")] | length,
  status_checks:  [.[] | select(.type == "required_status_checks")] | length,
  strict_checks:  [.[] | select(.type == "required_status_checks" and .parameters.strict_required_status_checks_policy == true)] | length,
  merge_queue:    [.[] | select(.type == "merge_queue")] | length,
  squash_refused: [.[] | select(.type == "pull_request" and ((.parameters.allowed_merge_methods // ["squash"]) | any(. == "squash") | not))] | length
}'
gh api "repos/$SLUG/branches/$DEFAULT" --jq '{classic_protection: .protection.enabled, classic_status_checks: ((.protection.required_status_checks.contexts // []) | length)}'
gh api "repos/$SLUG/branches/$DEFAULT/protection" --jq '{classic_strict: (.required_status_checks.strict // false), classic_pull_request: (.required_pull_request_reviews != null)}'
gh api graphql -f query='query($owner: String!, $name: String!, $branch: String!) { repository(owner: $owner, name: $name) { mergeQueue(branch: $branch) { url } } }' \
  -f owner="${SLUG%/*}" -f name="${SLUG#*/}" -f branch="$DEFAULT" --jq '{merge_queue: (.data.repository.mergeQueue != null)}'
gh api "repos/$SLUG" --jq '{allow_squash_merge}'
```

The first read is the branch's rulesets; the next two are classic branch protection, which
the rules endpoint does not show; the GraphQL read sees a merge queue from either kind.
Apply every row that matches:

| Reading | What it means for the batch |
|---|---|
| `squash_refused` non-zero, or `allow_squash_merge` false | **Refuse the batch and name the setting.** Both of this drill's paths land squash commits: the batch is not for this branch. (`allow_squash_merge` reads `null` without admin access; then a refusal surfaces at the first merge, before anything lands.) |
| `ship.merge: merge`, from the binding read above | **Refuse the batch and name the key and the value** — declared, or implied by a `[ship].landing` of `rebase-merge` or `merge-merge`. Both of this drill's paths land squash commits only, so a branch whose PRs land as merge commits is not its business: the set goes to `/ouro:land` per PR. Nothing has been squashed, and nothing is pushed, merged or closed. |
| `ship.merge: no answer` — the read gave anything but `squash`, `merge` or the `no such key` line | **Refuse the batch and name the read.** A read that answered neither value says nothing about the policy, and is never taken for an undeclared key. |
| a merge queue (`merge_queue` non-zero or `true`; "Require merge queue" is a classic protection setting too) | **Refuse and hand the set to the queue.** `gh pr merge -R "$SLUG"` enqueues the PR instead of merging it, so no merge can be checked against its squash commit, and a direct push is refused. |
| `strict_checks` non-zero, `classic_strict` true, or classic protection whose details did not read | **A set of two or more PRs goes to `/ouro:land` per PR.** Strict checks require each PR to be up to date with the base, so 5a lands the first PR and finds the next one `BEHIND`; and 5b's push is refused, since the required checks never ran on the local squash commits. |
| `pull_request` or `status_checks` non-zero, `classic_pull_request` true, `classic_status_checks` non-zero, or classic protection whose details did not read | **5a only.** A set that routes to 5b (step 5) goes to `/ouro:land` per PR instead: a branch that requires pull requests refuses a direct push, and required checks must pass on a commit before it can be pushed to the branch. |

**Classic protection whose details did not read** is `classic_protection` true with the
`/protection` read refused (it needs admin access; a non-admin gets `404`). Say so: whether
the branch requires up-to-date PRs, pull requests or checks is then unknown, and the
batch does not guess — it treats all three as required.

Two ways a read comes back without an answer, and they mean different things:

- **`403 … Upgrade to GitHub Pro or make this repository public`** — rulesets and, on a
  private repository, classic protection are gated by plan: a repository on a plan without
  them cannot carry what this read looks for. Continue; there is nothing to find.
- **Anything else** (no network, no read access) on the rules or merge-queue read — the
  batch cannot tell whether a merge queue or a required pull request is there, and landing
  blind is not safe: on a merge-queue branch `gh pr merge -R "$SLUG"` enqueues the PR, 5a's check
  stops at "not MERGED", and the queued PR can still merge later, outside any check. Say
  so and land the set through `/ouro:land` per PR.

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
  `$DEFAULT` branch. From the main checkout, after the fetch, run the gitignore check
  `/ouro:execute` runs before it creates a worktree, then add the worktree and fast-forward
  it to `origin/$DEFAULT`:

  ```bash
  DEFAULT=<[repo].default_branch>
  : "${DEFAULT:?}"
  mkdir -p .claude/worktrees/land-batch || exit 2
  git check-ignore -q --no-index .claude/worktrees/land-batch \
    || { rmdir -p .claude/worktrees/land-batch 2>/dev/null; echo "STOP: .claude/worktrees/ is not ignored - add it to .gitignore (exit 128 is a git error, above)"; exit 1; }
  git worktree add .claude/worktrees/land-batch "$DEFAULT" || exit 1
  git -C .claude/worktrees/land-batch merge --ff-only "origin/$DEFAULT" || exit 1
  test "$(git -C .claude/worktrees/land-batch rev-parse HEAD)" = "$(git rev-parse "origin/$DEFAULT")" \
    || { echo "STOP: local $DEFAULT is not origin/$DEFAULT - see git log origin/$DEFAULT..$DEFAULT"; exit 1; }
  ```

  A failed check removes the directory, and each parent it left empty, before its STOP. Exit 2
  is a filesystem error, something in the way of the directory such as a file at its path, not
  a missing entry.

  A block that stopped after `worktree add` leaves the worktree registered: remove it (the end
  of this skill) before running the block again. Every block from step 1 through step 7 then
  runs from that worktree, step 4's gates from its root, so every batch file resolves in its
  one git directory; the clean tree and the tip above are the worktree's. Re-read the binding
  there: step 0 read the main checkout's, which its branch may have changed, and step 4's
  `[[gate]]` list and `[overlays].land` are the default branch's. Git refuses the worktree
  while the main checkout is itself on `$DEFAULT`, the form above. **Never a detached
  worktree:** 5b's push sends the local `$DEFAULT` branch, never HEAD, so a detached
  worktree's squashes do not land. Commit message files stay outside the worktree's tree.
- Every PR in the set: CI green, review triaged.

## 1. Start the batch; merge order

Start a new batch once, before anything else — it clears any previous batch's files:

```bash
GD=$(git rev-parse --git-dir) && test -n "$GD" || { echo "STOP: not in a git repository"; exit 1; }
rm -f -- "$GD"/land-batch.*
```

Base-before-stacked: a PR whose base is another feature branch lands **after**
its base. Branches that append to the same file tails (the `[overlays].land`
tail-append hazards — locale tables, release notes) will conflict with each
other — order doesn't prevent that, the recipe below handles it. A stacked PR
does not land through step 2's squash at all: once its base's squash is in the
batch, that squash replays the base over lines already written and conflicts, so
the stacked PR lands as its own diff (step 3's stacked recipe).

**A stacked PR routes the set to the local push drill (5b)**, and step 2 records that route
when a PR's base is not `$DEFAULT`. Its GitHub base is its base's feature branch until that
branch is deleted and GitHub retargets the PR, and a retarget after
`gh pr merge -R "$SLUG" --delete-branch` deletes the ref through the API is not reliable — the
dependent PR can be closed instead.

## 2. Per PR: check the head, squash, commit, record

One call per PR, in merge order, with that PR's commit message written to a file — subject
`type(scope): summary (#PR)`, body ending with `Closes #N`:

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>; PR=<number>; BRANCH=<head branch>; MSG='<file holding the commit message>'
: "${DEFAULT:?}" "${SLUG:?}" "${PR:?}" "${BRANCH:?}" "${MSG:?}"
test -r "$MSG" || { echo "STOP: no readable commit message at $MSG"; exit 1; }
CLEAN=$(git stripspace < "$MSG")
printf '%s\n' "$CLEAN" | head -n 1 | grep -qE "\(#$PR\)[[:space:]]*\$" && test -z "$(printf '%s\n' "$CLEAN" | sed -n 2p)" \
  || { echo "STOP: the subject in $MSG reads '$(printf '%s\n' "$CLEAN" | head -n 1)' - it must end in (#$PR), with line 2 blank"; exit 1; }
GD=$(git rev-parse --git-dir)
read -r HEAD_SHA BASE <<< "$(gh pr view "$PR" -R "$SLUG" --json headRefOid,baseRefName --jq '"\(.headRefOid) \(.baseRefName)"')"
test -n "$HEAD_SHA" && test "$HEAD_SHA" = "$(git rev-parse "origin/$BRANCH")" \
  || { echo "STOP: #$PR's head is '$HEAD_SHA', not origin/$BRANCH - refetch and start the batch over"; exit 1; }
test "$BASE" = "$DEFAULT" || cut -f2 "$GD/land-batch.tsv" 2>/dev/null | grep -qx "$BASE" \
  || { echo "STOP: #$PR's base $BASE is not in the batch - land it first, or this diff misses what it builds on"; exit 1; }
RANGE=$DEFAULT; test "$BASE" = "$DEFAULT" || RANGE=$BASE
EMAILS=$(git log --no-merges --format='%aE' "origin/$RANGE..origin/$BRANCH" | tr '[:upper:]' '[:lower:]' | sort -u)
test -n "$EMAILS" || { echo "STOP: no author read from origin/$RANGE..origin/$BRANCH for #$PR - already landed (drop it from the set), holds only merge commits (it brings no commit of its own to squash: drop it from the set), or a ref did not resolve (refetch and start the batch over; git's error is above)"; exit 1; }
test -z "$(printf '%s\n' "$EMAILS" | sed -n 2p)" \
  || { echo "STOP: #$PR's range has more than one author:"; git log --no-merges --format='%aN <%aE>' "origin/$RANGE..origin/$BRANCH" | sort -u; echo "two addresses for one person are a mailmap entry, not a second author; otherwise a squash would reassign their work, and this drill lands nothing it has not squashed - land #$PR on its own with /ouro:land, and recompose the batch without it"; exit 1; }
AUTHOR=$(git log -1 --no-merges --format='%aN <%aE>' "origin/$RANGE..origin/$BRANCH")
if ! git merge --squash "origin/$BRANCH"; then
  test -z "$(git ls-files -u)" || { echo "STOP: #$PR conflicts with the batch - resolve it by step 3, then run step 3's block"; exit 1; }
  echo "STOP: git merge --squash origin/$BRANCH failed for #$PR without a conflict (its error is above)"; exit 1
fi
git diff --cached --quiet && { echo "STOP: #$PR adds nothing on top of the batch - already landed or named twice"; exit 1; }
git commit -q --author="$AUTHOR" -F "$MSG" \
  || { git reset -q --hard HEAD; echo "STOP: git commit failed for #$PR (its error is above); the squash is undone"; exit 1; }
printf '%s\t%s\t%s\t%s\n' "$PR" "$BRANCH" "$HEAD_SHA" "$(git rev-parse HEAD)" >> "$GD/land-batch.tsv"
test "$BASE" = "$DEFAULT" || printf 'stacked: #%s is based on %s\n' "$PR" "$BASE" >> "$GD/land-batch.route"
```

Each row of `land-batch.tsv` is `PR BRANCH HEAD_SHA SQUASH`, in merge order: 5a merges
exactly that head with that commit's subject and body, and checks the merge against exactly
that commit; 5b pushes the commit itself, so either path lands the `(#PR)` subject. The call
stops before the squash when the message's subject does not end in it or its second line is not
blank. The first row's
`SQUASH` sits on the pre-batch tip. A head that moved since the fetch is a PR nobody
validated. A failed commit (a hook, an unreadable message) undoes the squash — the tree is
clean by precondition, so the reset returns HEAD to the previous squash — and the same call
can run again once the cause is fixed. A squash that stages nothing records no row.

The commit carries the author of the PR's own range, never the session's: the distinct authors
of `origin/$DEFAULT..origin/$BRANCH` — its base branch's remote tip instead, for a stacked PR —
merge commits excluded, mailmap applied. One author is set on the commit, name and email as the
log gives them; the committer stays the session's identity. Two stop the call before anything is
staged — a squash would reassign their work and this drill lands nothing it has not squashed, so
that PR lands on its own through `/ouro:land` and the batch is recomposed without it. Step 3's
blocks read the same range and stop the same way, before they commit.

`Closes #N` in the body makes GitHub close the issue when the commit lands on
`$DEFAULT` — close nothing by hand, but **verify** afterwards. A PR with no issue
omits it.

## 3. Conflict recipe  ⚠️ the batch's sharp edges

**A conflict resolved by hand routes the set to the local push drill (5b):** a server
squash of the PR head cannot reproduce a hand resolution.

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

### A PR stacked on another branch in the set

A PR whose base is another feature branch already in the batch needs neither step 2's
squash nor a hand resolution. Its branch carries its base's commits, so the squash replays
them over the lines the base's squash already wrote and conflicts, while the stacked
branch's own diff applies cleanly on top. The recipe dry-runs that diff, discards the
conflicted squash, and applies it — one call, which validates the message exactly as step 2
does:

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>; PR=<number>; BRANCH=<head branch>; MSG='<file holding the commit message>'
: "${DEFAULT:?}" "${SLUG:?}" "${PR:?}" "${BRANCH:?}" "${MSG:?}"
test -r "$MSG" || { echo "STOP: no readable commit message at $MSG"; exit 1; }
CLEAN=$(git stripspace < "$MSG")
printf '%s\n' "$CLEAN" | head -n 1 | grep -qE "\(#$PR\)[[:space:]]*\$" && test -z "$(printf '%s\n' "$CLEAN" | sed -n 2p)" \
  || { echo "STOP: the subject in $MSG reads '$(printf '%s\n' "$CLEAN" | head -n 1)' - it must end in (#$PR), with line 2 blank"; exit 1; }
GD=$(git rev-parse --git-dir)
read -r HEAD_SHA BASE <<< "$(gh pr view "$PR" -R "$SLUG" --json headRefOid,baseRefName --jq '"\(.headRefOid) \(.baseRefName)"')"
test -n "$HEAD_SHA" && test "$HEAD_SHA" = "$(git rev-parse "origin/$BRANCH")" \
  || { echo "STOP: #$PR's head is '$HEAD_SHA', not origin/$BRANCH - refetch and start the batch over"; exit 1; }
test "$BASE" != "$DEFAULT" \
  || { echo "STOP: #$PR's base is $DEFAULT - it is not stacked, land it with step 2's squash"; exit 1; }
cut -f2 "$GD/land-batch.tsv" 2>/dev/null | grep -qx "$BASE" \
  || { echo "STOP: #$PR's base $BASE is not in the batch - land it first, or this diff misses what it builds on"; exit 1; }
EMAILS=$(git log --no-merges --format='%aE' "origin/$BASE..origin/$BRANCH" | tr '[:upper:]' '[:lower:]' | sort -u)
test -n "$EMAILS" || { echo "STOP: no author read from origin/$BASE..origin/$BRANCH for #$PR - already landed (drop it from the set), holds only merge commits (it brings no commit of its own to squash: drop it from the set), or a ref did not resolve (refetch and start the batch over; git's error is above)"; exit 1; }
test -z "$(printf '%s\n' "$EMAILS" | sed -n 2p)" \
  || { echo "STOP: #$PR's range has more than one author:"; git log --no-merges --format='%aN <%aE>' "origin/$BASE..origin/$BRANCH" | sort -u; echo "a stale base reports $BASE's own commits here, if it was force-pushed since #$PR was cut - fetch and rebase the stacked branch onto origin/$BASE, then run this call again; otherwise two addresses for one person are a mailmap entry, not a second author, and a real second author means a squash would reassign their work, so land #$PR on its own with /ouro:land, and recompose the batch without it"; exit 1; }
AUTHOR=$(git log -1 --no-merges --format='%aN <%aE>' "origin/$BASE..origin/$BRANCH")
OUT=$(git merge-tree --write-tree --merge-base="origin/$BASE" HEAD "origin/$BRANCH"); RC=$?
test "$RC" = 1 && { echo "STOP: #$PR's own diff conflicts with the batch:"; printf '%s\n' "$OUT" | sed -n '2,$p'; exit 1; }
test "$RC" = 0 || { echo "STOP: git merge-tree failed for #$PR (exit $RC) - its error is above, and the squash is still in the tree"; exit 1; }
git diff --binary "origin/$BASE" "origin/$BRANCH" > "$GD/land-batch.patch"
test -s "$GD/land-batch.patch" \
  || { echo "STOP: #$PR adds nothing on top of its base - already landed or named twice"; exit 1; }
git reset -q --hard HEAD
git apply --index --3way "$GD/land-batch.patch" \
  || { git reset -q --hard HEAD; echo "STOP: git apply --index --3way failed for #$PR (its error is above); the batch is unchanged"; exit 1; }
git diff --cached --quiet && { echo "STOP: #$PR adds nothing on top of the batch - already landed or named twice"; exit 1; }
git commit -q --author="$AUTHOR" -F "$MSG" \
  || { git reset -q --hard HEAD; echo "STOP: git commit failed for #$PR (its error is above); the applied diff is undone"; exit 1; }
printf '%s\t%s\t%s\t%s\n' "$PR" "$BRANCH" "$HEAD_SHA" "$(git rev-parse HEAD)" >> "$GD/land-batch.tsv"
printf 'stacked: #%s is based on %s\n' "$PR" "$BASE" >> "$GD/land-batch.route"
echo "#$PR landed as its own diff; the batch routes to 5b"
```

`merge-tree` merges three ways while `git apply` matches context strictly, so a diff the dry
run calls clean can still fail to apply when the batch moved lines around it: the apply is
`--3way` for that, and `--binary` so a binary file is not a `Bin` summary the applier refuses.

It records the stacked route and no hand-resolution line, because none happened. The row it
appends is
what the rest of the drill reads: a stacked PR has already routed the set to 5b, which
pushes the commit itself and closes each PR by its row's `HEAD_SHA`, so a commit made by
applying a diff lands exactly like a squash. 5a is never reached for such a set, and its
tree check would not accept one.

Once every path is resolved and staged, record the PR and the route in one call — it
commits the staged resolution with the PR's message, or takes a commit already made with
that message, when that commit's author is the range's one author:

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>; PR=<number>; BRANCH=<head branch>; MSG='<file holding the commit message>'
: "${DEFAULT:?}" "${SLUG:?}" "${PR:?}" "${BRANCH:?}" "${MSG:?}"
test -r "$MSG" || { echo "STOP: no readable commit message at $MSG"; exit 1; }
CLEAN=$(git stripspace < "$MSG")
printf '%s\n' "$CLEAN" | head -n 1 | grep -qE "\(#$PR\)[[:space:]]*\$" && test -z "$(printf '%s\n' "$CLEAN" | sed -n 2p)" \
  || { echo "STOP: the subject in $MSG reads '$(printf '%s\n' "$CLEAN" | head -n 1)' - it must end in (#$PR), with line 2 blank"; exit 1; }
GD=$(git rev-parse --git-dir)
test -z "$(git ls-files -u)" || { echo "STOP: unresolved paths remain:"; git ls-files -u | cut -f2 | sort -u; exit 1; }
read -r HEAD_SHA BASE <<< "$(gh pr view "$PR" -R "$SLUG" --json headRefOid,baseRefName --jq '"\(.headRefOid) \(.baseRefName)"')"
test -n "$HEAD_SHA" && test "$HEAD_SHA" = "$(git rev-parse "origin/$BRANCH")" \
  || { echo "STOP: #$PR's head is '$HEAD_SHA', not origin/$BRANCH - refetch and start the batch over"; exit 1; }
RANGE=$DEFAULT; test "$BASE" = "$DEFAULT" || RANGE=$BASE
EMAILS=$(git log --no-merges --format='%aE' "origin/$RANGE..origin/$BRANCH" | tr '[:upper:]' '[:lower:]' | sort -u)
test -n "$EMAILS" || { echo "STOP: no author read from origin/$RANGE..origin/$BRANCH for #$PR - already landed (drop it from the set), holds only merge commits (it brings no commit of its own to squash: drop it from the set), or a ref did not resolve (refetch and start the batch over; git's error is above)"; exit 1; }
test -z "$(printf '%s\n' "$EMAILS" | sed -n 2p)" \
  || { echo "STOP: #$PR's range has more than one author:"; git log --no-merges --format='%aN <%aE>' "origin/$RANGE..origin/$BRANCH" | sort -u; echo "two addresses for one person are a mailmap entry, not a second author; otherwise a squash would reassign their work, and this drill lands nothing it has not squashed - land #$PR on its own with /ouro:land, and recompose the batch without it"; exit 1; }
AUTHOR=$(git log -1 --no-merges --format='%aN <%aE>' "origin/$RANGE..origin/$BRANCH")
if git diff --cached --quiet; then
  ! cut -f4 "$GD/land-batch.tsv" 2>/dev/null | grep -qx "$(git rev-parse HEAD)" \
    && test "$(git log -1 --format=%s)" = "$(head -n 1 "$MSG")" \
    || { echo "STOP: nothing staged, and HEAD is not an unrecorded commit with #$PR's message"; exit 1; }
  HEAD_AUTHOR=$(git log -1 --format='%aN <%aE>')
  HEAD_EMAIL=$(printf '%s' "$HEAD_AUTHOR" | sed -E 's/.*<(.*)>/\1/' | tr '[:upper:]' '[:lower:]')
  AUTHOR_EMAIL=$(printf '%s' "$AUTHOR" | sed -E 's/.*<(.*)>/\1/' | tr '[:upper:]' '[:lower:]')
  test "$HEAD_EMAIL" = "$AUTHOR_EMAIL" \
    || { echo "STOP: HEAD's author is $HEAD_AUTHOR, not #$PR's range author $AUTHOR - amend it with: git commit --amend --no-edit --author=\"$AUTHOR\" - then run this call again"; exit 1; }
else
  git commit -q --author="$AUTHOR" -F "$MSG" || { echo "STOP: git commit failed for #$PR (its error is above); the resolution is still staged"; exit 1; }
fi
printf '%s\t%s\t%s\t%s\n' "$PR" "$BRANCH" "$HEAD_SHA" "$(git rev-parse HEAD)" >> "$GD/land-batch.tsv"
printf 'conflict resolved by hand: #%s\n' "$PR" >> "$GD/land-batch.route"
test "$BASE" = "$DEFAULT" || printf 'stacked: #%s is based on %s\n' "$PR" "$BASE" >> "$GD/land-batch.route"
echo "#$PR recorded; the batch now routes to 5b"
```

## 4. Validate the COMPOSED state — before anything lands

No PR's CI built this tree. Non-negotiable: run **every `[[gate]]` the
binding declares**, whatever its `areas`, from the repo root, on the composed
tree, after the last squash commit. A gate that was green on one PR's head
says nothing about the composition. A gate that replays a CI job runs with that job's `env:` set,
read from the workflow. What the local run cannot reproduce, such as the runner account's `PATH` or
a layout the job builds, is named in the gate evidence as CI's to check, never assumed green.

A long gate is judged by two reads, never by the clock. One is whether the gate's own process — the
process its command started to do the checking, not a helper it spawned that may outlive it — is
still present. The other is its last progress marker, its newest output or log write, read again to
see whether it moved. Either one present means the gate is alive and the run keeps waiting. With
neither, the gate is wedged, and the run reports it at once: the finding names the gate command, the
last progress marker it read and the absence of the gate's process. A wedged gate is neither a pass
nor a fail, and its evidence goes where gate results already go. No duration, no timeout, no polling
interval. A fresh core file, one written after the gate started, says a process the gate depends on
died: it may mark the gate wedged while the gate's own process is still present. The finding then
names the core file beside the gate command and the last progress marker. The run waits for the
gate within its own turn: it runs the gate in the foreground, or blocks on a gate it started in the
background, re-issuing the blocking call until the gate ends.
It never ends its turn while a gate it started still runs, to wait for a completion notice or a
scheduled wake-up: a headless session that ends its turn exits, and nothing reads the gate's result.

```bash
# for each [[gate]] in the binding, any <ouro> in the run replaced by the plugin root:
<gate.run>          # base-ref gates take origin/$DEFAULT — the pre-batch tip, since nothing has landed yet
```

A local commit is cheap to amend; a broken default branch is not.
Fix-in-place and amend the offending squash commit (unlanded history — amend is
fine here, and it keeps the author git already recorded). **An amend routes the set to the
local push drill (5b):** a server squash of the PR head lands the unfixed composition, and
5a's tree check would not see it — it compares each merge with the squash recorded before the
amend, so the unfixed tree matches. Record the route in one call:

```bash
NOTE='<what the amend fixed>'
: "${NOTE:?}"
printf 'amended after step 2: %s\n' "$NOTE" >> "$(git rev-parse --git-dir)/land-batch.route"
```

5a also refuses, before its first merge, a HEAD that is not the last recorded squash or
that no longer contains every recorded one — an amend nobody recorded stops there.

**On 5a, no other commit goes on local `$DEFAULT` from here until 5a's reset.** A release
commit, a version bump or any other follow-up is made after the batch has landed, on top
of `origin/$DEFAULT`: 5a ends by resetting local `$DEFAULT` to what GitHub merged, and a
commit made in between would be dropped there. On 5b such a commit can ride the single
push.

## 5. Land — route first

The set lands on GitHub (5a) unless one of these happened, and any one sends the whole
set to the local push drill (5b) — or, where step 0's table refuses 5b on this branch, to
`/ouro:land` per PR. Each is recorded as a line of `land-batch.route`, and 5a refuses while
that file holds one:

- a PR in the set is stacked on another feature branch (step 2 records it);
- step 3 resolved a conflict by hand (step 3's block records it);
- step 4 amended a squash commit (step 4's block records it);
- a path two or more PRs in the set change carries a `merge=` attribute (the block below
  records it).

The route block prints the decision and exits 0 either way:

```bash
GD=$(git rev-parse --git-dir)
test -s "$GD/land-batch.tsv" || { echo "STOP: no batch recorded - step 2"; exit 1; }
cut -f4 "$GD/land-batch.tsv" | while read -r s; do git diff-tree --no-commit-id --name-only -r "$s"; done \
  | sort | uniq -d | git check-attr --stdin merge | grep -v ': merge: unspecified$' \
  | sed 's/^/merge= attribute on a path two PRs change: /' >> "$GD/land-batch.route"
if test -s "$GD/land-batch.route"; then echo "ROUTE: 5b -"; sort -u "$GD/land-batch.route"; else echo "route: 5a"; fi
```

A `merge=` driver (`union` on a tail-append file, a custom driver) runs where the merge
runs: locally it resolves the file, while GitHub's squash may conflict on the same inputs.
That difference is measured with `git merge-tree` as a stand-in for GitHub's merge engine,
not on GitHub itself; the route takes the safe side of it. The route compares the set's
PRs with each other; a PR whose own change meets a base change made since it forked is
what 5a's first-call mergeability read catches instead.

Why 5a can otherwise reproduce the validated tree: squash commits carry no branch parent,
so the merge base of a PR head with the landed tip is the same commit locally and on
GitHub, and a clean squash of the same head onto a tip whose tree equals the previous
local squash's tree yields the local squash's tree. The checks below confirm that per
merge rather than trusting it.

### 5a. GitHub path — merge each PR, check each merge

Run this block once per PR. It takes the next recorded PR that has not landed, so it
cannot run out of order, and a stop recorded by any earlier call refuses every later one:

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>
: "${DEFAULT:?}" "${SLUG:?}"
GD=$(git rev-parse --git-dir)
stop() { echo "STOP: $*" | tee -a "$GD/land-batch.stop"; exit 1; }
test -s "$GD/land-batch.tsv" || { echo "STOP: no batch recorded - step 2"; exit 1; }
test ! -s "$GD/land-batch.route" || { echo "STOP: this batch routes to 5b, not 5a:"; sort -u "$GD/land-batch.route"; exit 1; }
test ! -s "$GD/land-batch.stop" || { echo "STOP: this batch already stopped:"; cat "$GD/land-batch.stop"; exit 1; }
N=$(( $(cat "$GD/land-batch.landed" 2>/dev/null | wc -l) + 1 ))
IFS=$'\t' read -r PR BRANCH HEAD_SHA SQUASH < <(sed -n "${N}p" "$GD/land-batch.tsv")
PREV=$(tail -n 1 "$GD/land-batch.landed" 2>/dev/null | cut -f2)
test -n "$PREV" || PREV=$(git rev-parse "$(head -n 1 "$GD/land-batch.tsv" | cut -f4)^")
: "${PR:?every recorded PR has landed}" "${BRANCH:?}" "${HEAD_SHA:?}" "${SQUASH:?}" "${PREV:?}"

git fetch origin "$DEFAULT" || stop "cannot fetch $DEFAULT before #$PR"
test "$(git rev-parse "origin/$DEFAULT")" = "$PREV" \
  || stop "$DEFAULT is at $(git rev-parse --short "origin/$DEFAULT"), not $PREV - it moved since validation"
if test "$N" = 1; then
  test "$(git rev-parse HEAD)" = "$(tail -n 1 "$GD/land-batch.tsv" | cut -f4)" \
    || stop "HEAD is not the last recorded squash - a squash was amended or a commit added after step 2"
  LOST=$(cut -f4 "$GD/land-batch.tsv" | while read -r s; do git merge-base --is-ancestor "$s" HEAD || echo "$s"; done)
  test -z "$LOST" || stop "recorded squashes no longer on HEAD: $LOST"
  for pr in $(cut -f1 "$GD/land-batch.tsv"); do
    for i in 1 2 3 4 5; do
      M=$(gh pr view "$pr" -R "$SLUG" --json mergeable --jq .mergeable)
      test "$M" = UNKNOWN && test "$i" != 5 || break
      sleep 10
    done
    test "$M" = MERGEABLE || stop "#$pr reads '$M' against $DEFAULT before anything landed"
    test "$(gh pr view "$pr" -R "$SLUG" --json isDraft --jq .isDraft)" = false \
      || stop "#$pr is still a draft, or its draft state did not read, before anything landed - mark it ready (/ouro:execute §4) and start a new batch (steps 1-4)"
  done
fi
for i in 1 2 3 4 5; do
  read -r BASE STATE <<< "$(gh pr view "$PR" -R "$SLUG" --json baseRefName,mergeable,mergeStateStatus --jq '"\(.baseRefName) \(.mergeable) \(.mergeStateStatus)"')"
  case "$STATE" in *UNKNOWN*) test "$i" = 5 || sleep 10 ;; *) break ;; esac
done
test -n "$BASE" || stop "cannot read #$PR's base and state"
test "$BASE" = "$DEFAULT" || stop "#$PR's base reads '$BASE', not $DEFAULT - a PR retargeted since step 2 would merge into a branch nobody validated"
test "$STATE" = "MERGEABLE CLEAN" || stop "#$PR is '$STATE', not MERGEABLE CLEAN"
for pr in $(tail -n +"$N" "$GD/land-batch.tsv" | cut -f1); do
  OPEN=$(gh api graphql --paginate -F number="$pr" -f owner="${SLUG%/*}" -f name="${SLUG#*/}" -f query='query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
    repository(owner: $owner, name: $name) { pullRequest(number: $number) { author { login } reviewThreads(first: 100, after: $endCursor) {
    pageInfo { hasNextPage endCursor } nodes { isResolved comments(first: 1) { totalCount nodes { author { login } path url } } } } } } }' \
    --jq '.data.repository.pullRequest as $p | $p.reviewThreads.nodes[] | select(.isResolved == false and .comments.totalCount == 1)
    | .comments.nodes[0] | select(.author.login != $p.author.login) | "\(.author.login) \(.path) \(.url)"') \
    || stop "cannot read #$pr's review threads"
  test -z "$OPEN" || stop "#$pr has a review thread nobody answered - reply in it (/ouro:land step 8) or resolve it:"$'\n'"$OPEN"
done
DEL=; test "$(gh pr list -R "$SLUG" --base "$BRANCH" --state open --json number --jq length)" = 0 && DEL=--delete-branch
gh pr merge "$PR" -R "$SLUG" --squash --match-head-commit "$HEAD_SHA" $DEL \
  --subject "$(git log -1 --format=%s "$SQUASH")" \
  --body "$(git log -1 --format=%b "$SQUASH")"
VIEW=$(gh pr view "$PR" -R "$SLUG" --json state,mergeCommit --jq '"\(.state) \(.mergeCommit.oid // "")"') \
  || stop "cannot read #$PR after the squash-merge - check it by hand; nothing is recorded for it"
read -r AFTER MERGE <<< "$VIEW"
test "$AFTER" = MERGED && test -n "$MERGE" || stop "#$PR reads '$VIEW', not MERGED with a merge commit - after 'Base branch was modified' the default branch moved and nothing merged for #$PR: the PRs not on the report's origin list go to a new batch (steps 1-4), since this stop file refuses every later call"
printf '%s\t%s\n' "$PR" "$MERGE" >> "$GD/land-batch.landed"
git fetch origin "$DEFAULT" || stop "#$PR merged as $MERGE, but $DEFAULT could not be fetched to check it"
test "$(git rev-parse "$MERGE^")" = "$PREV" \
  || stop "#$PR merged as $MERGE onto $(git rev-parse "$MERGE^"), not onto $PREV - someone pushed in between"
test "$(git rev-parse "$MERGE^{tree}")" = "$(git rev-parse "$SQUASH^{tree}")" \
  || stop "#$PR merged as $MERGE, whose tree is not the validated $SQUASH's"
echo "#$PR landed as $MERGE ($N of $(wc -l < "$GD/land-batch.tsv"))"
```

When every recorded PR has landed, the next call stops at the `PR` check with "every
recorded PR has landed" — that is the signal to go on to the reset below.

- **Before the first merge, the batch must still be the one step 4 validated.** HEAD is the
  last recorded squash and contains every recorded one, and every PR in the set reads
  `MERGEABLE` against `$DEFAULT` (`UNKNOWN` re-polled as below). That read catches what the
  `merge=` route cannot: a PR whose change conflicts with a base change made since it forked,
  which a local driver resolved and GitHub's squash would not — the batch stops before
  anything lands instead of after the first PRs have.
- **The tip must be the previously landed commit before each merge** — the pre-batch tip
  for the first PR. A moved default branch stops the batch before that PR merges: the
  composition step 4 validated is no longer what would land. `--match-head-commit` pins
  only the PR head, never the base.
- **Based on `$DEFAULT`, MERGEABLE and CLEAN, and nothing else.** A PR retargeted to another
  branch since step 2 would squash-merge into a tree nobody validated, past step 0's
  protected-prefix refusal. `gh pr merge -R "$SLUG"` itself refuses only `BLOCKED`,
  `BEHIND` and `DIRTY`, so `UNKNOWN` and `UNSTABLE` would merge. `UNKNOWN` is routine right
  after the previous merge moved the base, while GitHub recomputes: the read re-polls it
  five times, ten seconds apart, and anything but `MERGEABLE CLEAN` stops.
- **No open review thread on a PR still to land.** Before each merge the call reads the review
  threads of every recorded PR not yet landed, row `N` of `land-batch.tsv` onward, and stops on
  an open one by `/ouro:land` step 10's read and rule: not resolved, opened by anyone but the
  PR's author, no reply. The first call refuses before anything lands; each later call re-reads
  before its own merge, so a finding posted while the earlier PRs merged still stops. A failed
  read stops too.
- **Never `--auto` or `--admin`.** `--auto` merges later, outside the check; `--admin`
  bypasses the rules the batch exists to pass.
- **`--delete-branch` only when no open PR is based on `$BRANCH`.** Deleting a branch
  another open PR targets closes or strands that PR; a failed count adds no delete.
- **Judge the merge by `gh pr view -R "$SLUG"`, not by `gh`'s exit code.** The local cleanup of
  `gh pr merge -R "$SLUG"` can fail after the server merge succeeded (`/ouro:land` step 11);
  `state` and `mergeCommit` are the server's answer. A read that fails is not an answer: it
  stops the batch without recording the PR as landed or as not landed.
- **Check the merge commit, not the tip.** Its parent must be the previously landed commit
  and its tree must equal `$SQUASH`'s tree. Comparing `origin/$DEFAULT`'s tip instead
  misreads a push that landed after the merge as a mismatch; the parent check runs first,
  so a push that landed between the tip check and the merge is named as that.

**Stop and report.** A moved tip, a batch that is no longer the validated one, a PR not
MERGEABLE, not based on `$DEFAULT`, not MERGEABLE and CLEAN, not MERGED or not readable (gh's
`Base branch was modified` is this case: the default branch moved, nothing merged, and the PRs not
on the report's origin list go to a new batch), an open review thread or a failed thread read, or
a parent or tree mismatch
stops the batch, and `land-batch.stop` keeps every later call from merging. Report what
landed from `origin`, not from `land-batch.landed` alone — a read that failed after a
successful merge leaves that PR out of the file:

```bash
DEFAULT=<[repo].default_branch>
: "${DEFAULT:?}"
GD=$(git rev-parse --git-dir)
test -s "$GD/land-batch.tsv" || { echo "STOP: no batch recorded - step 2"; exit 1; }
git fetch origin "$DEFAULT" || exit 1
cat "$GD/land-batch.stop" 2>/dev/null
echo "on origin/$DEFAULT since the batch began:"
git log --oneline "$(git rev-parse "$(head -n 1 "$GD/land-batch.tsv" | cut -f4)^")..origin/$DEFAULT"
```

A PR whose merge mismatched is already merged, so it is on that list, with the mismatch.

**Local `$DEFAULT` has then diverged from `origin/$DEFAULT`:** it still holds every local
squash, so `git status -sb` reads ahead — and behind as well once something merged or
someone pushed, when `git pull --ff-only` fails. The way back to a clean tree is the reset
block below — fetch, the extra-commit check, reset. A continuation re-squashes the PRs
that are not on the report's `origin` list from there and re-gates: steps 1–4, as a new
batch. What to do about the rest of the set is the owner's call.

After the last merge — or after a stop — replace the local squash commits with what GitHub
merged:

```bash
DEFAULT=<[repo].default_branch>
: "${DEFAULT:?}"
GD=$(git rev-parse --git-dir)
LAST_SQUASH=$(tail -n 1 "$GD/land-batch.tsv" 2>/dev/null | cut -f4)
: "${LAST_SQUASH:?no batch recorded - step 2}"
git fetch origin "$DEFAULT" || exit 1
if test "$(git rev-parse HEAD)" != "$LAST_SQUASH"; then
  echo "STOP: local $DEFAULT has commits after the last squash $LAST_SQUASH, and a reset would drop them:"
  git log --oneline "$LAST_SQUASH..HEAD"
  echo "carry them onto what landed instead: git rebase --onto origin/$DEFAULT $LAST_SQUASH"
  exit 1
fi
git reset --hard "origin/$DEFAULT"
```

After a clean batch the reset is tree-equal to the local squashes by the checks above.
Then clean up as in 5b's last four bullets (surviving remote branches, worktrees and
local branches, the sweep, the `Closes #N` issues).

### 5b. Local push drill — push once, then close each PR

The push is its own call, and it checks that the remote now holds exactly what was pushed:

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>
: "${DEFAULT:?}" "${SLUG:?}"
GD=$(git rev-parse --git-dir)
test -s "$GD/land-batch.tsv" || { echo "STOP: no batch recorded - step 2"; exit 1; }
for pr in $(cut -f1 "$GD/land-batch.tsv"); do
  HEAD_SHA=$(awk -F'\t' -v pr="$pr" '$1 == pr {print $3}' "$GD/land-batch.tsv")
  HEAD_NOW=$(gh pr view "$pr" -R "$SLUG" --json headRefOid --jq .headRefOid)
  test -n "$HEAD_NOW" || { echo "STOP: cannot read #$pr's head"; exit 1; }
  test "$HEAD_NOW" = "$HEAD_SHA" \
    || { echo "STOP: #$pr's head is '$HEAD_NOW', not the $HEAD_SHA squashed in step 2 - what was pushed to it is not in its squash: recompose the batch (the non-fast-forward bullet)"; exit 1; }
  # 5a's merge guard refuses a draft by its merge state; this push reads no merge state, so it asks.
  test "$(gh pr view "$pr" -R "$SLUG" --json isDraft --jq .isDraft)" = false \
    || { echo "STOP: #$pr is still a draft, or its draft state did not read - it is marked ready once its fix commit verifies (/ouro:execute §4)"; exit 1; }
  OPEN=$(gh api graphql --paginate -F number="$pr" -f owner="${SLUG%/*}" -f name="${SLUG#*/}" -f query='query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
    repository(owner: $owner, name: $name) { pullRequest(number: $number) { author { login } reviewThreads(first: 100, after: $endCursor) {
    pageInfo { hasNextPage endCursor } nodes { isResolved comments(first: 1) { totalCount nodes { author { login } path url } } } } } } }' \
    --jq '.data.repository.pullRequest as $p | $p.reviewThreads.nodes[] | select(.isResolved == false and .comments.totalCount == 1)
    | .comments.nodes[0] | select(.author.login != $p.author.login) | "\(.author.login) \(.path) \(.url)"') \
    || { echo "STOP: cannot read #$pr's review threads"; exit 1; }
  test -z "$OPEN" || { echo "STOP: #$pr has a review thread nobody answered - reply in it (/ouro:land step 8) or resolve it:"; echo "$OPEN"; exit 1; }
done
git push origin "$DEFAULT" || exit 1
test "$(git ls-remote origin "refs/heads/$DEFAULT" | cut -f1)" = "$(git rev-parse HEAD)" \
  || { echo "STOP: origin's $DEFAULT is not local HEAD after the push"; exit 1; }
```

Then one call per PR. `SHA` is that PR's squash commit as pushed — `git log --oneline
"origin/$DEFAULT"` shows it by its `(#PR)` subject — and the close refuses unless that commit
is on `origin/$DEFAULT` and the PR's head is still the `HEAD_SHA` step 2 squashed — a commit
pushed to the PR after its squash never landed:

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>; PR=<number>; SHA=<its squash commit on origin>
: "${DEFAULT:?}" "${SLUG:?}" "${PR:?}" "${SHA:?}"
GD=$(git rev-parse --git-dir)
HEAD_SHA=$(awk -F'\t' -v pr="$PR" '$1 == pr {print $3}' "$GD/land-batch.tsv")
: "${HEAD_SHA:?no row for this PR in land-batch.tsv - step 2}"
git fetch origin "$DEFAULT" || exit 1
git merge-base --is-ancestor "$SHA" "origin/$DEFAULT" \
  || { echo "STOP: $SHA is not on origin/$DEFAULT - #$PR has not landed, so it is not closed"; exit 1; }
HEAD_NOW=$(gh pr view "$PR" -R "$SLUG" --json headRefOid --jq .headRefOid)
test -n "$HEAD_NOW" || { echo "STOP: cannot read #$PR's head - #$PR is not closed"; exit 1; }
test "$HEAD_NOW" = "$HEAD_SHA" \
  || { echo "STOP: #$PR's head is '$HEAD_NOW', not the $HEAD_SHA squashed in step 2 - what was pushed after it has not landed, so #$PR is not closed"; exit 1; }
gh pr close "$PR" -R "$SLUG" --comment "Landed on $DEFAULT via the local squash-merge drill — $SHA." --delete-branch
```

- **Before `git push`, every PR in the set must still be the head step 2 squashed, with no open
  review thread.** The push call reads each PR in `land-batch.tsv`: its head against its row's
  `HEAD_SHA`, then whether it is still a draft, then its review threads by `/ouro:land` step 10's
  read and rule. A moved head, a draft, an open thread or a failed read ends the call with exit 1: nothing lands and nothing is closed. A
  thread answered by a reply or a resolve alone lets the call run again. A moved head is step 8's
  usual path for a valid finding (fix, commit, verify, push, reply), and what was pushed is not in the
  squash, so recompose as the non-fast-forward bullet below says.
- **A `GH013` or `GH006` rejection means step 0 missed a rule** — one it could not read, one added since,
  or a bypass the pusher does not have. `GH013: Repository rule violations found` names a
  ruleset; `GH006: Protected branch update failed` names classic protection. Nothing is lost:
  the push call stops there, and a close runs only for a commit it finds on `origin`. The
  set has to land through `/ouro:land` per PR instead.
- **A non-fast-forward rejection (`[rejected] … (fetch first)` or `(non-fast-forward)`) means
  `$DEFAULT` moved since the batch was composed** — step 4's gates validated a composition on
  the old tip, not what would land. The landing stops there and nothing is closed. Recompose
  on the new tip: first the reset block under 5a, which reads `land-batch.tsv` before step 1
  clears it. A commit it names after the last squash — a release commit that was to ride the
  push — is not carried: reset to that squash, run the block again, and re-make the commit on
  top of the new batch. Then steps 1–4 again, as a new batch, from the fetched `origin/$DEFAULT`.
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

Watch the runs that built the batch's composition on `$DEFAULT`, listed from these commits:

- **5a** — every merge commit in `land-batch.landed` (`cut -f2` of it); after a stop, every
  batch commit on the stop report's `origin` list, up to and including the newest one. Each
  merge is its own push, and a workflow with a `paths:` filter runs for a merge only when that
  merge's own diff touches its list, so the last merge may not trigger that workflow at all:
  each workflow's newest run per event across the batch's commits built the validated
  composition. After a stop at a parent or tree mismatch, the mismatched merge commit landed and
  is on that list — include it, not only the matching ones before it.
- **5b** — `origin/$DEFAULT` right after the push: that one push's diff covers the whole batch.
- **Nothing landed** (a stop before the first merge) — there is nothing to watch; say so
  with `SHAS=none`.

`SHAS` may be space- or newline-separated. The block lists the runs of every commit in it and
watches the newest run per workflow and event across them: the workflow by its
`workflowDatabaseId`, not its name, which two workflow files can share; the newest by the latest
`createdAt`, the higher `databaseId` on a tie. A run that a later commit's run of the same
workflow and event supersedes is not watched once that newer run is listed, and the verdict
reads only the newest runs.

```bash
DEFAULT=<[repo].default_branch>; SLUG=<[repo].slug>; SHAS="<the commits above, space- or newline-separated, or none when nothing landed>"
: "${DEFAULT:?}" "${SLUG:?}"
test "$SHAS" != none || { echo "nothing landed: nothing to watch"; exit 0; }
: "${SHAS:?}"
stop() { echo "STOP: $*"; exit 1; }
# The newest run per workflow and event across every commit in $SHAS: one id per line.
list_runs() {
  ALL=""
  for s in $(printf '%s\n' "$SHAS"); do
    R=$(gh run list -R "$SLUG" --branch "$DEFAULT" --commit "$s" --limit 100 --json databaseId,workflowDatabaseId,event,createdAt \
      --jq '.[] | "\(.createdAt)\t\(.databaseId)\t\(.workflowDatabaseId)/\(.event)"') || return 1
    ALL="$ALL$R
"
  done
  printf '%s' "$ALL" | sort -k1,1r -k2,2nr | awk -F '\t' 'NF == 3 && !seen[$3]++ {print $2}'
}
for i in 1 2 3 4 5 6; do
  RUNS=$(list_runs) || stop "cannot list the runs of $SHAS"
  test -n "$RUNS" && break
  test "$i" = 6 || sleep 10
done
if test -z "$RUNS"; then
  echo "no run for any of $SHAS after a minute: no workflow was triggered for them (path filters, as /ouro:land step 7 notes)"
else
  WATCHED=""
  while true; do
    NEW=""
    for run in $(printf '%s\n' "$RUNS"); do
      case " $WATCHED " in *" $run "*) ;; *) NEW="$NEW $run" ;; esac
    done
    test -n "$NEW" || break
    for run in $(printf '%s\n' "$NEW"); do gh run watch "$run" -R "$SLUG" --interval 60; WATCHED="$WATCHED $run"; done
    RUNS=$(list_runs) || stop "cannot list the runs of $SHAS"
  done
  BAD=""
  for run in $(printf '%s\n' "$RUNS"); do
    STATE=$(gh run view "$run" -R "$SLUG" --json status,conclusion --jq '"\(.status) \(.conclusion)"') || stop "cannot read run $run's status"
    read -r RSTATUS CONCLUSION <<< "$STATE"
    test "$RSTATUS" = completed || stop "run $run is still $RSTATUS, with no conclusion yet"
    case "$CONCLUSION" in success|skipped|neutral) ;; *) BAD="$BAD $run:$CONCLUSION" ;; esac
  done
  test -z "$BAD" || stop "run(s) concluded outside success/skipped/neutral (/ouro:land step 9 judges a bad run):$BAD"
fi
```

The drill isn't done at the last merge or the push, nor when `$DEFAULT` is green: step 7 closes
the wave.

## 7. Close the wave: follow-ups, then the queue's anchors

Run it before the batch worktree goes, since it reads the batch files there. Nothing landed (step 6
watched `none`): skip it.

A batch lands several reviewed PRs at once, and two things each landing leaves are still open when
`$DEFAULT` goes green. Each review's findings outside its diff, and whatever its PR deferred, have
to go somewhere. And the landing has split or changed lines that queued `agent-ready` issues quote
as anchors.

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
   there is one), whose `$DEFAULT` 5a's reset block or 5b's push left at `origin/$DEFAULT`, and
   against the files of every PR this batch landed (`land-batch.tsv`, or the stop report's
   `origin` list after a stop) in place of the one PR step 13 names.

**A batch worktree goes last** — after step 7, or once the owner ends a stopped batch: steps 6
and 7 read the batch files in that worktree's git directory, and a stopped batch runs 5a's reset
block there first, so no squash stays on the local `$DEFAULT`
branch the next batch starts from. An untracked file left in the worktree, such as gate
output, makes the removal refuse: check `git -C .claude/worktrees/land-batch status --short`.
Move **every** tool shell out of it — each holds its own current directory, so moving one frees
nothing while another still sits there — then remove it from the main checkout, as `/ouro:land`
step 11 states: `[repo].checkout`, else the directory the batch-worktree block ran from, then
`git -C <that checkout> worktree remove .claude/worktrees/land-batch`, and verify the directory
is gone from the **filesystem** — `git worktree list` cannot see an orphan, for the reason step 11
gives. Its branch is
`$DEFAULT` and is never deleted. A PR branch the main checkout itself is on is deleted after
that, once the main checkout has switched away from it.
