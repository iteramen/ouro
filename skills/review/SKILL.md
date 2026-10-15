---
name: review
description: Adversarial review of a commit range, before the pull request is marked ready — a subagent (Opus by default, `[models].reviewer`) briefed to refute each commit's stated goal, with a sibling-doc sweep. The weight follows the issue's Flow class (contract §4). Probe format for a full change (CI scripts, workflows, release machinery, gates) — executed probes and a mandatory REJECTED ledger as its evidence; `--sized` for a standard change — probes at the changed hunks only, no build, gate or suite the pull request's checks already ran, one PROBED line per hunk as its evidence; `--light` for an express change (documentation, skill text, rules or comments, or a test-accepted change within the S shape) — no probes, and a per-commit files-read line as its evidence.
---

# review

Invoked as `/ouro:review <range>`.

A second reviewer that has to **refute**, not approve. It reads the actual files, runs probes
(whatever the claim needs), and may report a finding only with the input that triggers it and the
wrong outcome it produces. Everything it could not break goes in a **REJECTED ledger**, one line per
attack with the probe it ran and what shows it ran — or, when it cannot show the probe ran, in an
**UNTESTED list**. In the probe format that ledger is the evidence it read the code, what separates
"found nothing" from "looked at nothing"; in light mode (below) a per-commit line naming the files
it read and the commands it ran does that job. A probe counts only if it demonstrably ran: what it must
show, each where it applies, is stated once, in the reviewer prompt, since the reviewer reads
nothing else — and the same holds for a probe the session runs itself in steps 3 and 5. A vector
whose probe cannot show what applies to it is **untested**, not rejected — untested is honest,
rejected is a claim.

Measured on the 2026-08-26 release-machinery fixes: a verdict reviewer (grok-audit, scoped)
returned one comment-truth finding; this format returned eight confirmed defects in the same diff,
including a narrower SDK switch the author had missed and a fresh-dispatch hole no dry run could
reach. Cost ~190 k tokens, ~14 min for two commits.

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the
marketplace checkout.

## Steps

0. **Read the binding.** `.claude/ouro.toml` at the repo root (schema: `docs/binding.md` in the
   plugin); refuse without it. Its `[[gate]]` commands belong to the two re-runs the contract
   names (§7), never to the reviewer: the prompt's tools-available line names git and the
   targeted commands the brief asks for, a single test or a probe at the changed lines, with each
   `<ouro>` replaced by the plugin root, since the reviewer resolves nothing. `[models].reviewer`
   (via `ouro-binding.py get`; `no such key` at exit 1 means unset: default `opus`) is the
   dispatch's model, step 2. `[overlays].review` is
   the list of standards files: read each and fill the prompt's Standards block with them whole,
   or drop the block when the list is empty or absent. Nothing else in the binding is the
   reviewer's business.
1. **Claims first, then a brief per commit, not per diff.** Fill the prompt's claims block
   before the goals: the changelog entry the range adds, every sentence it adds or changes in a
   docstring, a comment, a help text or a skill (the list, or the grep that lists them), and the
   session's record where it already exists. The reviewer marks each true, false or unverifiable
   before the goals; a false claim is a finding at the severity of the behaviour it
   misdescribes. Then, for each commit in the range, write one paragraph: what it
   must do, what must stay unchanged, and 4–8 **attack vectors**, or at most two in a sized
   review (below). Start from four defaults, named
   as the prompt's menu spells them, which count toward the 4–8, in this order: The other path,
   Docs, Comment budget, Key and check coverage — the order measured as most often needed. Then
   add up to four more, the specific semantics this commit makes you least sure of. The vectors
   most often needed next were, in order: A test that cannot go red, Tool behaviour, Step order,
   Shell semantics. An unnamed front is invisible to the critic even when
   it walks past the evidence. Build the prompt from [`REVIEWER-PROMPT.md`](REVIEWER-PROMPT.md).
   Where the brief forbids the only way to get something the task needs, it names the allowed
   one — a throwaway repository, a stub first on PATH that refuses the call it forbids.
2. **Dispatch** one subagent (`Agent`, `model:` set from step 0's `[models].reviewer`, default
   `opus`) with that prompt, its `{turn budget}` the one the plugin's `docs/contract.md` sets.
   Read-only: it may run probes but never edit, stage or commit. Keep working; the result
   arrives as a notification.
3. **Verify every CONFIRMED finding yourself** before touching code — open the cited
   `path:line`, reproduce the trigger. A finding you cannot reproduce is a question back to the
   reviewer, not a fix. Reading the cited line reproduces nothing when the trigger needs state
   that other code decides. A finding about what a user can reach in a running app is
   reproduced in that app, or traced through the app's own gating above the code it cites:
   navigation, a dialog, a disabled parent. Until then it is UNTESTED, and
   step 5 names it with its coverer; it is never put to the owner as a defect or as the premise of
   a ruling. A `STANDARDS` finding is verified by opening the cited `path:line` and the rule it
   cites.
4. **Fix the class, then sweep siblings.** A finding names one occurrence; the fix covers every
   occurrence of that rule — the cloned workflow, the same claim in the second doc, the same
   guard in the sibling script. The reviewer's stale-doc sweep is the starting list, not the
   whole one. When the change adds or alters something other code depends on (a token, a table
   key, a helper's input shape, a rule every party must carry), the sweep first lists everything
   that reads it: its callers, every reader of the table or file (CI workflows included), every
   template that becomes a prompt, and every other build target, variant or consumer that
   compiles or instantiates the changed code, each marked affected: yes, no, or unknown with the
   rule used. Then it checks each one, since a reader that never held the phrase is invisible
   to a grep for it. The claims table gets one row naming the readers found and the search that
   found them. Sweep with line breaks and comment leaders collapsed: a copy wrapped mid-phrase is
   invisible to a line-based grep. **Prose is fixed by subtraction:**
   a sentence a probe showed false is deleted, or replaced by the predicate the code tests —
   never described again in other words, which is a new unprobed claim. Re-read the paragraph a
   cut leaves: the sentences after it may still point at what the cut sentence named — put the
   name where the pointer is, or cut the pointer too — and rewrap the block. A comment or
   docstring says what the code does now; what used to break goes in the commit message.
   **A LOW or a nit is fixed by cutting or narrowing.** Where its only fix would add a check, a
   guard, a branch or a test row, the fix is not made in this change: the finding goes where the
   severity floor (contract §3) sends it and step 6 names it, unless the finding is itself that
   the check is missing.
5. **Reread the REJECTED ledger and the UNTESTED list.** Any vector rejected by reasoning instead of
   a probe, or reported untested, is still open: probe it yourself or send it back. Only one that
   nothing here can reach — a behaviour only another platform or a CI runner exercises, or code with
   no test host, which no test project in the repository can load (UI code-behind, host glue), less
   a workflow step's `run:` block whose probe (`skills/execute/references/mutation-rows.md`) gave
   two different exits — stays untested after that: name it, with why and with what covers it, where
   step 6 records, and never count it as rejected. A coverer is either a CI leg that runs on the
   pull request and must be green before it merges, or the PR record. Where code has no test host
   the coverer is a `Smoke by hand:` item in the PR body's Merge danger section, which is the PR
   record; in a `trivial` run, which no person merges, it counts as uncovered. A vector that nothing
   covers is named as uncovered.
6. **Record**: the reviewer's full report, and each verification's answer, post as one PR comment
   each, written with the file-write tool and sent with `gh pr comment --body-file` once the PR
   exists; until then they wait in the session's scratch directory, where the session saves each
   report as it receives it. The PR body carries the `Review:` line `/ouro:land` defines beside
   the `Fixes` line — `rounds` from the session's own tally, `findings` from the severity each
   `CONFIRMED` finding names, `false-sentences` from the one record reading step 7 makes — and
   the verdict line, `VERIFIED at <sha>` or what survives, with a link to the report comment. A
   change with prose rounds (step 7) says so beside the verdict. Findings you chose not to fix
   become documented limitations or an issue — never silence; a finding outside the change goes
   where the severity floor (contract §3) sends it.
7. **A fix round is verified, not reviewed again.** One full review per change. A ledger line
   proves the lines it probed, not a fix that replaced them, so after a fix — before the PR or
   on an open one, before it is pushed — resume the reviewer that ran the review (`SendMessage`)
   with the verification message in [`REVIEWER-PROMPT.md`](REVIEWER-PROMPT.md) and the fix commit
   alone.
   What it finds in lines the fix did not touch is a follow-up, not a failed round, and takes the
   severity floor (contract §3): the contract's cap counts what a fix introduced. A reviewer that
   cannot be resumed is replaced by a fresh one given that message, the same scope and the first
   review's report. **The session's record goes with that message**: the squash subject and the
   PR body are finalized after the review, so the verification of the last fix commit is what
   reads them, each sentence a claim against the tree at that commit, once; the false sentences
   it finds are the `Review:` line's count, never re-taken. A change with no fix round sends the
   PR body and the answer line, naming the head the review read — a reading of the record, not a
   second review — which is the channel the e2e skill's "whoever reviews the diff reviews the
   message with it" names.
   **A prose round** — a fix round in which nothing executed changed — is verified by reading,
   and the contract's cap does not count it. Show the identity in the PR body: for every source
   or test file the fix commit touches, its token stream from the language's own tokenizer with
   comment tokens removed is identical to the commit the standing verdict read, beside a
   control: the same comparison against a commit whose executed lines in those files differ
   reports a difference, or the identity has shown nothing; a file no earlier commit holds has
   no control, and its round is ordinary. A comment-only change in a script, a workflow (a
   workflow template included), a gate file or a command block that no tokenizer covers is shown
   by reading every changed line instead. It is an
   ordinary round instead when any token differs; when the fix touched a build file, a fixture,
   data or a configuration file, comment-only or not;
   when the fix reworded a docstring the file itself
   reads, which a comparison blanks but the code prints; or when it changed a comment something
   executes or reads — a `#requires`, a shebang, an encoding line, a lint pragma, a marker a
   gate greps, comment-based help a help gate reads. Unsure means ordinary. The pull request's
   checks re-run on the push that carries it; the verification does not wait on them, and no
   gate re-runs locally under any class except one the pull request's checks do not run at all
   or run only to print a skip (contract §4 Gates). A
   verification that finds anything executed did change shows the round was not prose, and it
   counts. The bound is two prose passes on a sentence, then the sentence is deleted rather than
   reworded a third time — a sentence the issue's acceptance requires is a STOP instead.

## Light mode

`/ouro:review --light <range>` dispatches the same reviewer, at `[models].reviewer`'s model
(default opus), with the same read-only brief, minus the probe requirement and the REJECTED
ledger: build the prompt from the light variant in [`REVIEWER-PROMPT.md`](REVIEWER-PROMPT.md),
which replaces the ledger with one line per commit naming the files the reviewer read and the
commands it ran — light mode's evidence that it looked. Every CONFIRMED finding still carries
`path:line` and the wrong outcome it produces, the claims block stays (the record is in no diff,
so light mode reads it the same way), and the stale-doc sweep stays. Steps 3–4 apply unchanged; step 5 becomes: every commit in the range has
a READ line naming the files that commit changed — a commit without one was not reviewed, send
it back — a vector listed UNTESTED is still open, check it yourself or send it back; one that
stays untested (a behaviour only another platform or a CI runner exercises, or code with no test
host, which no test project in the repository can load (UI code-behind, host glue), less a workflow
step's `run:` block whose probe (`skills/execute/references/mutation-rows.md`) gave two different
exits) is named with why and with what covers it, a CI leg or the PR record, or as
uncovered if nothing does; where code has no test host the coverer is a `Smoke by hand:` item in
the PR body's Merge danger section, which is the PR record, and in a `trivial` run, which no
person merges, it counts as uncovered; and a fix that tightens the change's Flow class sends the
range back at that class's weight, which is that change's one probing review; step 6 posts the
light report as it posts any; step 7's verification is a reading one, its answer a READ line for
the fix commit. A prose round (step 7) is verified this way after a probe-format review too. The
session's own hand-back of a light review, to the user and in the record, opens by naming the
mode (light), the range and the files the reviewer read, then the verdict: a reader must know
which weight the review took without opening the transcript. Under express the range is
local and pushed only at convergence.

Which weight a review takes, light, sized (below) or the probe format, follows the issue's Flow
class: contract §4's rule, cited and not restated here. Which issue takes this review at all, by
its size and `[ship].review`, is the table in `docs/binding.md` "Choosing ship.review".

## Sized review

`/ouro:review --sized <range>` is the probe format cut to the diff: the same reviewer, the same
read-only brief, the claims block and the stale-doc sweep. Its budget is hard: probes stand only
at the changed hunks, and it runs no build, gate or suite the pull request's checks already ran.
Step 1 writes one paragraph per commit, what it must do and what must stay unchanged, and names
at most two attack vectors, the ones the session is least sure of, in place of the four defaults.
The prompt's output item 2 is
the sized variant in [`REVIEWER-PROMPT.md`](REVIEWER-PROMPT.md): one `PROBED` line per changed
hunk, the probe run at it and what shows it ran, or the reading where a reading suffices, then
`UNTESTED`. Steps 3, 4 and 7 apply unchanged; step 5 rereads the `PROBED` lines as it rereads a
ledger, and a hunk with no line is unreviewed, sent back; step 6 records it as the probe format
does, marked sized, the `PROBED` lines standing where the ledger's count stands. A fix that
tightens the change's Flow class to full sends the range back in the probe format.

## When not to use

- A contract seam (IPC, C-ABI, serialization, public API) → `/ouro:seam`.
- A plan or design, judged on its merits rather than against a diff.
- A one-line question ("is this regex right") that needs no repository read.
- The weight is not a choice: it follows the issue's Flow class (contract §4), express taking
  **light mode** (`--light`), standard the **sized review** (`--sized`), and full the probe format.
  `external-audit` in a scoped `--paths` round is the external alternative to light mode.
