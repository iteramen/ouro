---
name: triage
description: >
  Grade GitHub issues against the ouro contract (the plugin's `docs/contract.md` plus the
  docs the binding's `[authority].local` names): verify every claim against code, grade each
  issue, promote the ones that qualify. Invoked as `/ouro:triage --all` or
  `/ouro:triage <N> [<N>…]`. Use when the user asks to triage the backlog or an issue,
  promote issues to agent-ready, grade issue quality, re-triage after a ruling landed, or
  when a weekly gate (anchor or shape) demoted an issue. Verdicts: PROMOTE / NEEDS-RULING /
  CHECKPOINT / SPLIT / STALE. Report-and-approve — issue bodies are the owner's artifacts;
  rewrites and labels apply only after approval. The unattended weekly pass is
  `/ouro:intake`, not this skill.
---

# Triage (agent-ready pipeline intake)

Grades issues against the `agent-ready` contract and moves them toward it. The contract
(authority: the plugin's `docs/contract.md`, plus every doc listed under `[authority].local`
in the binding — cited, never restated): **one deliverable; claims verified against code;
anchors are symbol + verbatim fragment with advisory line numbers stamped `@ SHA`;
`Doc impact on close:` line; no open design decisions.**

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the
marketplace checkout.

## 0. Binding

Sync the checkout first — `git fetch origin` and fast-forward the default branch — then read
`.claude/ouro.toml` at the repo root. The binding may have landed after the last pull, and
every anchor is verified at HEAD, so a stale checkout grades against the wrong tree. No
binding, or one that fails `ouro-binding.py check` → refuse and say so. This skill uses:

- `[repo].slug` — every `gh` call carries `-R <slug>`: a bare call addresses whatever
  repository gh picks from the clone's remotes, which in a fork's clone is the parent.
- `[rolling_issues]` — the exact titles to exclude from `--all`. Select them by exact-title
  match, never `in:title` (a substring match catches unrelated issues).
- `[authority].local` — the docs that bind beyond the contract; the verifiers read them.
- `[labels].scope` / `[labels].area` / `[labels].type` — the labels allowed to ride on a state. An issue
  carrying any other non-state label is a finding, not a verdict.
- `[owner].ruling_approvers` — whose comment counts as a ruling having landed, and who alone
  converts an `architecture` issue.

## Modes

- `/ouro:triage --all` — the open backlog, excluding the rolling issues and already-
  `agent-ready` ones (unless `--recheck`). `umbrella` issues are indexed, not graded — a
  container is never executed. A cleanup issue (contract §3) yields its sweep proposal at
  contract §3's sweep threshold: one child issue, its lines each re-verified and anchored, a
  line that turns out to need a decision left behind; on approval, a comment on the cleanup issue names
  that child and the lines it took. `blocked` and `idea` get a one-line check (has the trigger
  fired? has anything changed?) and are otherwise left alone; they are out of the working
  view by design. `architecture` issues are skipped: a design being shaped is graded only when
  the owner names it, and only the owner (`[owner].ruling_approvers`) converts it — to
  `umbrella` once slices exist, to `needs-ruling` or `agent-ready` once it is crisp, or closed.
- `/ouro:triage <N> [<N>…]` — specific issues: new arrivals, re-triage after a ruling
  landed, after the anchor or shape gate demoted an issue, after `/ouro:intake` posted a PROMOTE
  proposal that needs interactive approval, after `/ouro:execute` delivered a checkpoint's
  finding with its proposed conversion, or an `architecture` issue the owner names.

## Procedure

1. **Inventory** via `gh issue list -R <slug>` and `gh issue view <N> -R <slug>`. Group into
   batches of ~8 by subject area so a verifier holds coherent code context.
2. **Spawn one verification subagent per batch** (top tier; **read-only** — a subagent never
   writes to the tracker: the owner's approval lives in the session's context, not the
   subagent's, and the permission layer refuses it there). Its brief states the contract's turn
   budget; reaching it, it reports the verdicts it has and names the issues it did not finish.
   Per issue it:
   - Reads the body **and every comment** — a later comment may amend the spec, and a
     comment from a login in `[owner].ruling_approvers` may be the ruling that clears a
     `needs-ruling`.
   - **Greps every cited path/symbol/fragment at HEAD** — a fragment that no longer greps
     is the staleness signal (re-locate or mark dead). Line numbers are advisory; the
     fragment is the anchor.
   - **Verifies the core claim** (the bug still reproduces in code reading; the missing
     feature is still missing) — artifact wins, two-sided evidence, never from memory. A
     measurement counts only in the context where the code runs: a call is timed inside its
     caller, what a skill sees is read from its rendered text, a path is resolved the way the
     code under test resolves it, a tool's behaviour is probed, not read from its help text,
     and work said to be missing is looked for on the default branch at HEAD, the branch it
     would land on, never read off a diff between another branch and its own fork point.
     An anchor resolving proves the code is there, never that the claim about it is true;
     read the predicate, the usage, the test.
   - Checks: single deliverable? open design decisions? doc-impact present/deducible?
     exactly one state label? For a defect or change in code that more than one build target
     compiles or instantiates, which other build targets, variants or consumers share it, and
     is each affected: yes, no, or unknown with the rule used?
   - Emits a verdict:
     - **PROMOTE** — contract met (or met after a mechanical body refresh). Include the
       refreshed body draft: verified anchors (`symbol — "fragment"` `@ <short SHA>`),
       single deliverable, `Doc impact on close:` line — and a **`Size: S/M/L`**
       estimate. **S** is the eligibility rule `/ouro:execute` re-checks on the diff: one
       deliverable; every anchor resolves; no new API, workflow edit, migration or flag
       default; no behaviour change beyond what a gate or a same-PR test covers; a bounded
       diff — no more than three files and about sixty changed lines, a starting point that
       measured loop runs move. The file count excludes a file that changed **only** because a
       policy or a gate **requires** a claim to be mirrored into it — a changelog, a generated
       artefact, a disclosure or release-notes surface — since fan-out is not judgment; those
       files still count toward the line condition. The requirement is the test and the examples
       are only examples: a file nothing obliges the change to mirror counts, whatever it is
       called. Name the exclusions you can foresee and say why; the executor applies the same
       test to any you could not. One sentence corrected in a repo that
       mirrors it into a changelog, a gated disclosure card and a header is three excluded files
       and a handful of lines: **S**, not M. Twenty files of real edits is M however few lines
       each carries. **M** is everything else that fits one focused session;
       L = multi-session.
       **L is a SPLIT verdict, not a size an issue gets promoted at** — an issue is never
       executed as L. If the verifier cannot find the split lines, the verdict is
       NEEDS-RULING with the question "owner-driven session, or split how?". A ruling for an
       owner-driven session re-triages the issue to `human-ready` while the owner drives it,
       never to `agent-ready`.
       Size picks `/ouro:execute`'s build path (S inline, M dispatched) and, under `copilot`,
       `external-audit` and `none`, whether `/ouro:review` runs before the PR is marked ready; it also lets
       unattended runs budget their queue. Review weight follows the class of change, not
       the size.
     - **NEEDS-RULING** — sound issue, but open design decisions. List each as a crisply
       answerable question. These become the rulings queue.
       When a question's options are existing designs, it also offers none of them: an open design brief,
       which is the `architecture` state.
     - **CHECKPOINT** — the blocker is **missing evidence**, not a missing decision.
       The test: *would going and looking answer this?* If yes it is a checkpoint; if
       the answer depends on what the owner wants, it is NEEDS-RULING. Emit the
       analysis brief as the refreshed body — what to inventory, where to look (and, where
       the evidence is in another repository, the ref it is read at), what
       shape the answer takes (a table, a call-site map, a measured rate) — plus the
       `Size` estimate. Promotes to `agent-ready` + `checkpoint`. Prefer this over
       NEEDS-RULING whenever a ruling would currently cost the owner a cold-start
       investigation: a checkpoint turns that into reviewing a recommendation. Splitting
       a fat NEEDS-RULING into a checkpoint child + the residual ruling is a normal,
       encouraged outcome.
     - **SPLIT** — multiple deliverables. Propose the split lines (each a would-be issue
       title + one-line scope). If one child would ride on another's code, say so
       explicitly: sequenced siblings force a stacked PR (squash-residue risk on the
       base's merge) — weigh one issue with two staged deliverables instead. When they do
       split, the later child is filed `blocked`, its first line the `**Unblocks when:**` line
       naming the first child.
     - **STALE** — the premise no longer holds (fixed, deleted, superseded). Cite the
       disproving artifact; propose close-with-comment.
3. **Consolidate.** Batch ALL ruling questions into few AskUserQuestion rounds (rulings
   partition — never one interrupt per issue). Present PROMOTE drafts, SPLIT lines, and
   STALE closures for approval in the same summary. Attach the planning-type suggestion
   (below) where one applies, marked as a suggestion.
4. **Apply only what was approved — from this session.** Have each verifier draft its bodies
   and comments as files plus an ordered `manifest.json`, then run
   `python3 <ouro>/bin/apply-manifest.py <dir> --repo <slug> --dry-run` and, clean,
   `python3 <ouro>/bin/apply-manifest.py <dir> --repo <slug>`, with the `<slug>` step 0 read: the
   applier refuses a manifest naming an issue it does not create without `--repo`, the
   repository otherwise being whatever binding the working directory holds. The applier creates
   children first, links native sub-issues, and re-renders forward references, after a failed
   step too.
   **Order the steps `create` → `comment` → `edit` → `close` → `subissue`**, and create children
   with no state label: the shape gate fires on the `labeled` event and demotes a promotion
   whose `**Triage**` comment is not there yet, so an edits-first manifest un-promotes
   everything it just promoted. The applier refuses that order rather than reordering silently.
   After its last write it waits past the last `create` and re-reads every issue it created or
   moved to a state, removing a second state label: an intake automation reads the labels on
   `opened` and stamps after it reads, so its stamp can land after the edit's own read.
   `{{key}}` placeholders stand in for children not yet created. Keep a create key to letters,
   digits and underscores, and give each `create` its own: the applier refuses any other spelling,
   and a key a second `create` declares again. A reference to a key no earlier
   `create` step declares — a step's `issue`, a subissue's `child` or `parent`, a placeholder in
   a comment, edit or close body — is refused before anything runs, as is one that resolves below
   1; a create body may name any key the manifest creates, since a final pass re-renders it.
   A double-brace word in any manifest body is a placeholder to the applier, and one that names
   no created key is refused: a body that must mention one describes it, or quotes it without
   its braces, and an anchor on a line that holds one picks a brace-free substring of that line
   (a fragment is grepped as a substring, so stripping the braces out of it leaves an anchor
   that greps nothing).
   A step carries only the fields its op reads: a `create` its key, title, body file and labels;
   a `comment` or `close` its issue and body file; an `edit` its issue, an optional body file,
   `add_labels`, `remove_labels` and `allow_shrink`; a `subissue` its parent and child. Any other
   field refuses the manifest, and an `edit` cannot set a title.
   A `create` step's `title` is literal text: the applier refuses a placeholder in one, so a
   manifest that wants a sibling's number puts that reference in the body, which the final pass
   re-renders, and a title that must mention a placeholder spells it without its braces.
   It also refuses an empty body file, and an `edit` that cuts an existing issue's body below
   half unless that step carries `allow_shrink: true`; the body an edit replaces is kept beside
   the manifest as `<N>.body.before.md` (a dry run reads no issue, so checks only for empty files);
   an existing file of that name is kept as an earlier run's backup, so a manifest directory must
   not ship one.
   It refuses, before any `gh` call, a body file or `create` title the private token list matches,
   and a missing list too; `--no-forbidden-check` skips that check, for a host that holds no list
   by design, and so does a binding that declares `ship.forbidden_check = "off"`. A session that
   meets the refusal reports it to the owner rather than passing `--no-forbidden-check`.
   Every body file a step names is a bare file name that resolves inside the manifest directory —
   a name holding a slash, a backslash or a colon, or ending in a space or a dot, since a name
   Windows rewrites is not that name, is refused wherever the manifest is applied, and so is a
   `..` segment, an absolute path or a symlink out — so keep each body beside its `manifest.json`.
   A step object giving a key twice is refused as an unreadable manifest, naming the key.
   A body edited by hand outside the applier is never transformed in the same shell statement
   that uploads it: fetch it to a file, edit the file with an exact-match editor, upload the
   file, then re-read the issue and compare sizes.
   The mapping:
   - PROMOTE: the `**Triage**` verdict comment, then `gh issue edit N -R <slug> --body-file …` (original
     text survives in the issue's edit history) `--remove-label <previous state>`, then
     `gh issue edit N -R <slug> --add-label agent-ready`. `checkpoint` rides on top when the verdict was
     CHECKPOINT; a delivered checkpoint whose finding settled the question is promoted with
     `checkpoint` declared among the edit's removals.
     `trivial` is applied here or nowhere — same bar as promotion, interactive
     approval only; note that under `[ship].policy = "stop-at-pr"` it is advisory. The applier
     sends an edit's body and removals before its additions, in two calls whose exits it checks:
     gh writes one call's additions and removals independently, and a label the repository lacks
     fails only its own half. The shape gate fires on the second call's `labeled` event, so it
     reads the new body. The applier removes only labels the issue carries, printing each
     declared removal it drops, and after a failed step it still re-renders forward references and
     runs the state-label re-read above, then stops the run.
   - NEEDS-RULING: `--remove-label <previous state>`, then `--add-label needs-ruling`, and post
     the ruling questions as an issue comment. On a move to any state but `agent-ready` the
     applier also removes the `trivial` and `checkpoint` the issue carries: the modifiers ride
     only on `agent-ready`. When a ruling lands later, re-triage that issue (usually → PROMOTE;
     a ruling that picked the open design brief moves it to `architecture`).
   - SPLIT: file the child issues (the repo's work-item form), convert the parent to a
     curated `umbrella` or close it — per approval.
   - STALE: close with the disproving citation, per approval.
   - Every verdict comment this skill posts starts with the literal line `**Triage**`; the
     applier's `comment` step normalizes that marker line (a verdict on it moves to line 3,
     blank lines above it are dropped), and a `close` step posts its comment as written. A
     comment that is not this skill's verdict on that issue does not open with that marker: the
     normalizer would treat it as one, and the shape gate would read it as provenance.
5. **Report** counts per verdict, the applied changes, and the issues no verifier finished.

### The shape gate is literal — six patterns that fail while looking right

`Test-AgentReadyShape.ps1` is a regex gate, and it runs on the `labeled` event within a
minute of a promotion. Its regexes are the authority; these are the six ways ordinary
markdown breaks them, each of which costs a demotion and a bot comment:

| Write | Not | Why |
|---|---|---|
| `Size: M` alone on its line | `Size: M.` | `^Size:[^\S\r\n]*(\S+)` captures the trailing period into the value |
| `Doc impact on close: …` or `### Doc impact on close` | `**Doc impact on close:**` | the literal must start the line; bold markers precede it |
| `**Triage**` alone on line 1, verdict from line 3 | `**Triage** — PROMOTE (Size M).` | the first line is compared whole, trimmed |
| `Size: S` or `Size: M` | `Size: L` | L is a SPLIT verdict; it never rides on `agent-ready` |
| one `Size:` line | `Size: M` and, further down, `Size: L` | every `^Size:` line is read; a body states one size, so different values are a finding |
| `Size: M` on one line | `Size:` with the value on the line below | a value ends at its line break, so an empty `Size:` line is no `Size:` line |

The anchor gate is literal too: a quoted fragment must `git grep -F` verbatim, so quote what
the file says rather than a tidied version of it, and from one line of it: a phrase the file
wraps never greps. On an anchor line — a list item whose first backticked span names a tracked
file — every quoted fragment of 12 to 120 characters is checked, plain words included, and
only in the file that line cites, so a phrase from a neighbouring file is dead there. Both
gates skip the `Doc impact on close` declaration — it names destinations, which need not exist
yet.

Three traps in a body only a gate finds, after the body is applied:

- The applier writes a create-key placeholder as the bare number, so a body that wants a link
  writes the hash sign before it.
- A backticked repo path must resolve at HEAD, so a file the change creates is named
  without its directory.
- A double-quoted phrase on an anchor line is read as an anchor, so a phrase the change will add
  is quoted with single quotes.

## Planning-type suggestions (the triager proposes, never applies automatically)

Issues that arrive from a planning session may carry a planning-type label (`wayfinder:*`).
It records what kind of work the author expected; it is not a state and decides nothing by
itself. When one is present, the triager **suggests** the mapped destination in the report —
the verifier still grades on its own evidence, the owner still approves, and a mapping that
disagrees with the evidence loses to the evidence (a `wayfinder:task` with an open decision
is NEEDS-RULING whatever its label says).

| Planning label | Suggested destination | Why |
|---|---|---|
| `wayfinder:map` | `umbrella` | An index of work; its children carry the real states |
| `wayfinder:grilling` | `needs-ruling`, **resolved by a session** | The owner has to be grilled, not asked; the ruling comment says "session, not a one-liner" and a one-line reply does not clear it |
| `wayfinder:prototype` | `agent-ready`, deliverable is an artifact | The throwaway build answers the question; the PR is the artifact, not a feature |
| `wayfinder:research` | `agent-ready` + `checkpoint` | The deliverable is a finding: read-only, posted as a comment, converts rather than closes |
| `wayfinder:task` | `agent-ready` or `human-ready` | Decided by who can do it — a bench, a rig, a release step is `human-ready` |

## Non-negotiables

- **Never guess a ruling.** An unanswerable question is the finding itself.
- **Never close or rewrite unilaterally** — interactive approval first. The unattended
  `/ouro:intake` adds `needs-triage` and `needs-ruling` only, never PROMOTE.
- **One state label, always.** Adding a state means removing the one it replaces —
  an issue must never carry two. An issue found with two is a finding; fix it in the
  applied changes, with approval.
- **Idempotency marker.** The intake's comment starts with `**Intake triage** (automated)`;
  its presence means the issue was graded once, unattended, at an older SHA. Read it as
  evidence, re-grep everything it cites, and let your verdict supersede it. Never post a
  second intake-style comment; the interactive comment starts with `**Triage**`.
- Verification discipline is the drift-audit's: artifact wins; every kept claim cites the
  artifact; a claim that couldn't be checked is said out loud, not assumed.
- Don't inflate, but don't reflex to NEEDS-RULING either. An assessment-shaped issue
  ("Assess: …", "Decide the fate of …") is usually a **CHECKPOINT** — the deliverable is the
  assessment. Reserve NEEDS-RULING for what survives *after* the evidence, and leave curated
  roadmap umbrellas as `umbrella` by design. Not everything should be agent-ready.
