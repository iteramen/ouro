---
name: fuse
description: >
  Build several agent-ready issues together, on one branch per region, a region being issues that
  share files or an area batch the owner names: one brief per region and one commit per issue, the
  gates the region's Flow class has the session run on a fresh clone, review split by file cluster,
  an area batch as one, a fix-round builder, and one PR per region landed through `/ouro:land`.
  Invoked as `/ouro:fuse <N> <N>…`. Use when the owner says "fuse the issues that touch the
  same file", "build these issues together", "fuse N, N, N", or "batch these" for issues that
  share files or for an area batch `/ouro:compile` proposed.
  Never a set of already-built, already-reviewed PRs (`/ouro:land-batch`), and never one issue
  run alone (`/ouro:e2e`). Refuses an issue that is not `agent-ready` at Size S or M, that
  carries `checkpoint`, or whose deliverable is a checklist.
---

# Build several issues into one PR per region — `/ouro:fuse`

`/ouro:execute` builds, `/ouro:review` attacks, `/ouro:land` merges — the same three `/ouro:e2e`
joins for one issue. This skill joins them for several that share files or form an area batch:
grouping, the shared brief, the review split by file cluster and the per-issue record are its own;
everything else it takes by reference. **It states no rule those three already state.** Where a
step is theirs, this cites and defers.

## 0. Binding

Refuse without a binding, exactly as `/ouro:execute` §0 and `/ouro:land` §0 refuse. It reads:

- `[repo]` and `[[gate]]`, as `/ouro:execute` §0 reads them;
- `ship.review` and `ship.merge`;
- `[models].builder` — "the M build dispatch's model" `/ouro:execute` §0 reads: via
  `ouro-binding.py get`, `no such key` at exit 1 meaning unset, default `sonnet`;
- `[models].reviewer`, read the same way, default `opus`;
- `[rolling_issues].loop_runs`, read the same way, for §8's record: unset means no comment.

## 1. Eligibility

Each named issue must carry `agent-ready` at `Size: S` or `Size: M` (contract §4), without
`checkpoint`, and with a deliverable that is not a checklist (contract §3): its parts land one
cycle each through `/ouro:e2e`. It re-verifies each issue's anchors — grepping every anchor at
HEAD, as
`/ouro:execute` §1's entry ritual does. An issue with a dead anchor takes that ritual's
"Any anchor dead" path and is left out; an issue with the wrong label, the wrong size,
`checkpoint` or a checklist deliverable is named and left out; the rest proceed. Each issue's
body is read under
`/ouro:execute` §1's **Trust the spec only as far as its authors** and **An untrusted edit stops
the run** bullets: an issue that trips the stop is named, left out and swapped as that bullet says. An issue `/ouro:execute`'s Testability
bullet warns on is not left out: it proceeds, §2's region report names it with its items, and the
region PR's Merge danger names each warned item, as that bullet says.

## 2. Regions

- A region is a set of issues that share an edited file, transitively, or an area batch the owner
  names (`/ouro:compile`'s `Area batches`), taken as one region though its members share no file.
  An issue's edited files are the paths its deliverable changes and its `Doc impact on close:`
  line. Its anchor paths are a hint only, since an anchor may cite a file the issue only reads.
- A file an issue changes only because a policy or a gate requires a claim to be mirrored into
  it, by contract §4's S-shape test, is left out of region linking, out of §7's conflict test and
  out of §8's sync trigger only: it links no two issues, a revert that conflicts only there takes
  the remaining members' lines and drops the reverted member's, and a sync that changes only such
  a file re-reviews no cluster.
  A file the repository's docs policy obliges, such as a feature README or a format doc's
  implemented-by line, is such a file.
  Each mirrored hunk is reviewed with the cluster of the issue that wrote it.
- Each region is one branch and one PR. Regions run in dependency order: an issue that needs
  another's key or file comes after it.
- Before step 3, the run reports each region's issue count and expected files. A region of
  more than five issues asks the owner whether to split it at a dependency boundary; a split
  lands in two parts, the first as its own region and the second a new region run from the
  updated default branch. The run is attended (§9), so this is asked in the run, not parked.
  There is no other numeric cap.
- After each build, the region is re-checked against the diff. A commit that touches a file of
  another region, a mirrored file aside, stops the run for a regroup.
- Once a region's builds are in, and before step 5, the session runs the
  **Files another issue edits** step of `/ouro:execute` §3 on the region's diff, once per region,
  against the open `agent-ready` issues outside the run, and again on the region's final range
  before its merge, as that step says. The region PR's body names its matches (§8).

## 3. Build

One brief per region, from the issue bodies plus the standing brief lines below. Builders run at
`[models].builder`, one commit per issue — `/ouro:execute` §3's mutation-row and test-first rules
apply unchanged, cited here and not restated. An issue whose deliverable is a verbatim copy with a
grep acceptance may be applied inline.

**One builder per issue.** Each issue not applied inline gets its own builder, briefed as
`/ouro:execute`'s M dispatch briefs its builder (its Size section), with the region brief and its
own issue body in place of the issue body, so its dispatch names the commit it starts from. The
builders run one at a time, in the region's dependency order, in the region's worktree. The
session commits each one before the next builder starts, as `/ouro:execute`'s Size section says:
"The session alone commits."

**A renamed-symbol map.** After each commit, the session greps the remaining issues' anchors at
the new head. For each symbol or path the commit renamed or moved, an entry (old name, new name,
file) goes into a map that every later builder in the region is given with the brief. An anchor
that is dead for any other reason takes `/ouro:execute` §1's dead-anchor path.

**In-run rulings.** A builder STOP, or a conflict between two issues' acceptance that writing the
region brief surfaces, is asked of the owner in the run — the run is attended (§9), so this is
asked in the run, not parked — and the same builder resumes with the ruling, recorded as an
in-run ruling in that issue's Deviation cell (§8). `/ouro:execute` §3's "swap `agent-ready` →
`needs-ruling`" is not taken for an issue the owner ruled on in the run.
A STOP names its conflict. The region brief names, for each issue,
each conflict that stops its builder: two sources the issue cites that disagree, or an acceptance that meets a rule of the
repository's. A builder that meets one ends its turn before it writes the code that depends on it.
The session reads each hand-back for decisions the brief did not settle, its `Settled by the spec`
section first, as `/ouro:execute`'s STOP rule has it read. Each decision that section does not
show a clause deciding is a STOP taken late: it goes to the owner as an in-run ruling.

**The standing brief lines**, added beside the issue bodies:

- Quoted rule text is swept verbatim across the region, because a cheaper builder paraphrases.
- An issue that adds a suite names its `[[gate]]` line.
- A test stand-in for `gh` answers every call the code under test makes.
- A new gate states what each CI leg's account has on its PATH.
- A test that must run green before the change: stage the work, restore only the changed
  production file from `HEAD` (`git restore --source=HEAD --worktree -- <file>`), run the test,
  then restore the staged version (`git restore --worktree -- <file>`) — never a
  `git worktree add`, and never a directory outside the one the builder was sent to.

## 4. Pre-PR check

A region takes the tightest Flow class among its issues (contract §4; no `Flow:` line reads as
standard), re-derived from the region's diff after the commits as `/ouro:execute` Size has it, so it
only tightens. A region opens a PR, so its class is never direct: express at the loosest. The region
PR's checks are the gate run, as for one issue, less what the session runs itself: a fresh clone of
the branch head, on a host the session chooses, runs only what `/ouro:execute` §3 has the session
run for that class, narrowed by `paths` as `skills/execute/references/gate-selection.md` has it. The
clone is made with the git settings the tree needs to check out, the ones the session's own checkout
carries for that reason, passed as `git clone -c <key>=<value>`. `core.longpaths=true` on Windows is
the example. The record names each setting passed, and code that runs only through a shipped Action,
which CI alone checks.
The run follows the Gate environment, What CI checks, Two reads and Coverer sections of
`skills/execute/references/dispatch-rules.md`.

The region PR opens after steps 5 to 7 (§8), so a real failure in its checks, told from a flaky
one as `/ouro:land` §9 does, takes a fix round counted toward step 5's cap, as `/ouro:execute` §4
has it under express, where CI first runs after convergence.

## 5. Review, per file cluster

- Which reviewer takes each cluster, by `ship.review`, is the fused row of the table in
  `docs/binding.md` "Choosing ship.review", and an unavailable reader falls back to `/ouro:review`
  by the availability rule beside it, the record saying so. `/ouro:review`'s own Steps are the
  procedure, cited and not copied, at the weight the region's Flow class gives it (contract §4),
  and a `copilot` reader also reads the PR once it opens (step 8).
- A cluster under `/ouro:review` is capped by contract §7's three fix rounds. Round 1 is the
  review; each fix and its step 7 verification is one fuse round, and
  a fuse round counts as a contract fix round.
  A cluster under `/ouro:external-audit` is capped by that reader's own cap, cited and not restated.
  The brief names any repository that owns the other half of a claim.
- An area batch is one cluster: one reviewer on the whole range, at the region's class (§4), with
  contract §7's three rounds per batch.
- The session gives each reviewer that builds or runs a gate
  its own scratch clone of the region head, made as §4 makes its clone. No reviewer builds in the
  region worktree or in another reviewer's clone. Before each step 5 dispatch, the session first
  re-reads each region issue's state, as §8 says.
- A finding the session rejects with a probe, the probe's command and result in the record,
  counts as resolved.
- A PR reviewer's finding after verification takes contract §7's "one fix round of its own", its
  fix commit verified as step 6 verifies a fix.

## 6. Fix round

A fix-round builder at `[models].builder` fixes round 1's findings. The session verifies the fix
commit by its red→green tests and by its own reading of the fix diff, never by the fix-round
builder's report, and does not hand-edit. Each round's fix-round builders start only once every
cluster's report for that round is in. A region's fix-round builders, one per cluster or per
issue, are dispatched as §3's builders are: one at a time in the region worktree and in the
region's dependency order, each committed before the next starts, and each dispatch
names the commit it starts from. Where `/ouro:review` took the cluster (step 5), its step 7
verification also runs.
Where external-audit took the cluster, its second round on the cluster, within step 5's cap,
hunts new defects in the fix commit and is not asked to re-confirm round 1's fixes. The fix round:

- hands back its changes split file by file to the issue each serves, and the session makes one
  fix commit per issue from that split;
- adds a mutant for every row it adds, and keeps each row's red check;
- adds a control row where a row asserts an absence;
- re-runs the acceptance of every issue it touches;
- adds no new justification text;
- once the region's PR is open (step 5's round for a PR reviewer's finding, §4's for a failed
  check, or §8's review after a sync), pushes its fix commits as `/ouro:execute` §4 says:
  together, once the round's verification passes.

## 7. An issue that cannot converge

A cluster whose remaining findings are all resolved, or fixed and verified
within step 5's cap, has converged. A leftover at the round cap in which nothing executed changes
takes `/ouro:review` step 7's prose round: verified by reading, outside the cap.
Any other issue whose findings do not converge within step 5's cap is reverted out of the branch,
and the rest of the region lands:

- its commits are reverted with `git revert`: its build commit (§3's one commit per issue) and
  each fix commit §6 made for it, with those of every member that leaves with it, newest first
  across them all;
- its `Fixes #N` line leaves the PR body;
- it gets `/ouro:execute` §1's `**Stop:** review cap` comment, its open findings from line 3, and
  the swap `agent-ready` → `needs-ruling`, and its open findings leave its cluster with it;
- a member that builds on it (a `Builds with:` line naming it, or a commit whose own revert would
  conflict with it outside a mirrored file, §2) leaves with it, in the same way;
- the rest land, and the PR body says so in one sentence.

## 8. Record and landing

- One PR per region, with a `Fixes #N` line per issue, and one `Review:` line, as `/ouro:land`
  defines it, directly under them: its `rounds` is the largest of the clusters' fix-round
  counts, as `/ouro:land` defines `rounds`, its `findings` are summed over the clusters, and its
  `false-sentences` is one count, from the record reading of the one region record, `unread` only
  when no record reading ran.
- A per-issue table: issue, commit, verdict, and a Deviation column for a wrong premise, an
  unmeetable acceptance, or an in-run ruling. Under a squash `ship.merge`, the default branch
  keeps one commit per region; the per-issue record there is the PR body's per-issue table.
- The landing is `/ouro:land`, run from its step 0 and cited here, not restated. Its PR opens
  here, after steps 5–7, and not at the point `/ouro:execute` §4 names for one issue:
  the per-cluster review reads the branch, not a PR. Land step 6's review, per `ship.review`:
  under `adversarial-review` and `external-audit`, it is met by step 5's per-cluster verdicts,
  already in the PR body; under `copilot`, its request on the open PR stays, as step 5 already
  says; under `none`, its stop at the PR stands, and the merge is the owner's. The verdicts that
  count are those on the tree land step 2's sync produces: a cluster whose files the sync
  changed, per §2's mirrored-file exclusion, takes a fresh step 5 on the synced range, a new
  review with its own rounds under step 5's cap, and that review's verdict replaces the earlier one.
- At every sync and before every step 5 dispatch, the session re-reads each region issue's state
  and the pull requests that would close it, with
  `gh issue view <N> -R <slug> --json state,closedByPullRequestsReferences`. An issue closed under
  the region, or one a PR other than the region's own would close, stops that region. The report
  names the issue and the PR, and the owner rules.
- Invoking `/ouro:fuse` authorizes merging exactly the regions' PRs once `/ouro:land`'s guards
  pass, as `/ouro:e2e` §4's invocation does for its one PR, and no other branch.
- After a region lands, one comment on the issue titled by `[rolling_issues].loop_runs` (§0) records
  the region's issues, changed lines, files, minutes and rounds. The minutes are wall-clock from the
  region's first builder dispatch (§3) to its merge. Where the key is unset (`no such key`) or no
  issue carries that title, the run posts nothing and creates no issue: the numbers go in its
  report, as §9's "A run files no issue of its own" has it.

## 9. Relation to the rest

- A cleanup-ledger sweep child is eligible like any other issue.
- `trivial` stays per issue and unattended; a run through this skill is attended.
- A run files no issue of its own — its PR is its record.
- `/ouro:land-batch` is the several-PR path: it validates and lands PRs that are already built
  and reviewed. This skill builds and reviews the issues first, landing one PR per region.
