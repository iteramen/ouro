---
name: review
description: Adversarial review of a commit range, before pushing — a subagent (Opus by default, `[models].reviewer`) briefed to refute each commit's stated goal, with a sibling-doc sweep. Probe format for anything executed (CI scripts, workflows, release machinery, gates) — executed probes and a mandatory REJECTED ledger as its evidence; `--light` for a diff that changes only documentation, skill text, rules or comments — no probes, and a per-commit files-read line as its evidence.
---

# review

Invoked as `/ouro:review <range>`.

A second reviewer that has to **refute**, not approve. It reads the actual files, runs probes
(whatever the claim needs), and may report a finding only with the input that triggers it and the
wrong outcome it produces. Everything it could not break goes in a **REJECTED ledger**, one line per
attack with the probe it ran and what shows it ran — or, when it cannot show the probe ran, in an
**UNTESTED list**. In the probe format that ledger is the evidence it read the code, what separates
"found nothing" from "looked at nothing"; in light mode (below) a per-commit line naming the files
it read and the gates it ran does that job. A probe counts only if it demonstrably ran: what it must
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
   plugin); refuse without it. Its `[[gate]]` `run` commands are the repo's verify commands and
   the probes the reviewer may execute against the range — hand them to the prompt's
   tools-available line alongside git, each `<ouro>` in a run replaced by the plugin root, since
   the reviewer resolves nothing. `[models].reviewer` (via `ouro-binding.py get`; `no such key` at
   exit 1 means unset: default `opus`) is the dispatch's model, step 2. `[overlays].review` is
   the list of standards files: read each and fill the prompt's Standards block with them whole,
   or drop the block when the list is empty or absent. Nothing else in the binding is the
   reviewer's business.
1. **Claims first, then a brief per commit, not per diff.** Fill the prompt's claims block
   before the goals: the changelog entry the range adds, every sentence it adds or changes in a
   docstring, a comment, a help text or a skill (the list, or the grep that lists them), and the
   session's record where it already exists. The reviewer marks each true, false or unverifiable
   before the goals; a false claim is a finding at the severity of the behaviour it
   misdescribes. Then, for each commit in the range, write one paragraph: what it
   must do, what must stay unchanged, and 4–8 **attack vectors**. Start from four defaults, named
   as the prompt's menu spells them, which count toward the 4–8, in this order: The other path,
   Docs, Comment budget, Key and check coverage — the order measured as most often needed. Then
   add up to four more, the specific semantics this commit makes you least sure of. The vectors
   most often needed next were, in order: A test that cannot go red, Tool behaviour, Step order,
   Shell semantics. An unnamed front is invisible to the critic even when
   it walks past the evidence. Build the prompt from [`REVIEWER-PROMPT.md`](REVIEWER-PROMPT.md);
   its `{changed lines}` is the range's `git diff --shortstat`, insertions plus deletions, over
   the files under review — a version bump and its changelog entry left out.
   Where the brief forbids the only way to get something the task needs, it names the allowed
   one — a throwaway repository, a stub first on PATH that refuses the call it forbids.
2. **Dispatch** one subagent (`Agent`, `model:` set from step 0's `[models].reviewer`, default
   `opus`) with that prompt, its `{turn budget}` the one the plugin's `docs/contract.md` sets.
   Read-only: it may run probes but never edit, stage or commit — and it runs them on this
   machine, per step 0's binding-supplied `[[gate]]` `run` commands, so don't run a heavy local gate
   while the review is in flight. Keep working; the result arrives as a notification.
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
5. **Reread the REJECTED ledger and the UNTESTED list.** Any vector rejected by reasoning instead
   of a probe, or reported untested, is still open: probe it yourself or send it back. Only one
   that nothing here can reach — a behaviour only another platform or a CI runner exercises,
   or code with no test host, which no test project in the repository can load (UI code-behind,
   host glue) — stays untested after that: name it, with why and with what covers it, where
   step 6 records, and never count it as rejected. A coverer is either a CI leg that runs on the
   pull request and must be green before it merges, or the PR record. Where code has no test host
   the coverer is a `Smoke by hand:` item in the PR body's Merge danger section, which is the PR
   record; in a `trivial` run, which no person merges, it counts as uncovered. A vector that
   nothing covers is named as uncovered.
6. **Record**: in the commit or PR body, `adversarial-review: C claims: T true, F false, V
   unverifiable; N confirmed, fixed by class in <sha>; S standards findings, fixed or declined;
   M rejected with probes; U untested; K re-run against <fix sha>` — C counts the claims block's
   sentences, plus the record's once step 7 has read them; S counts the `STANDARDS` findings;
   U is the vectors still untested after step 5, K is 0 when no fix touched
   a probed line. A change with prose rounds
   (step 7) adds `; P prose rounds, tokens identical <prior sha>..<fix sha>, read-verified at
   <fix sha>`. Findings you chose not to fix become documented limitations or an issue — never
   silence; a finding outside the change goes where the severity floor (contract §3) sends it.
   Every count in the record (this line, the PR body, the squash subject and the changelog entries)
   is taken from the reviewer's report or from a command run at the head it describes, and is
   taken again after every commit. A count is never carried from an earlier record, a commit
   message or a builder's hand-back. The session saves each reviewer's and each builder's report
   to a file in its scratch directory as it receives it, and the output of each command whose
   count the record takes, such as the gate run. Once the record is written, and again after each
   change to it, run
   `python3 <ouro>/bin/record-counts.py --record <record files> --report <report files>`: the
   record files are the PR body and one file holding the squash subject and the changelog
   entries the branch adds; the report files are the saved ones. It lists each count, a number
   beside a listed noun such as `3 rows` or `0 failed`, that no report holds beside the same
   noun, singular or plural; step 7 reads that list. The `adversarial-review` line, light or not,
   counts its fields from the reviewer's report sections and the session's own claim reading and
   rounds; the record says so once, which justifies each of them. Tracing does not replace the
   rule: a count that traces still comes from a report or a command run at the head, taken again
   after every commit.
   The record also carries the `Review:` line that `/ouro:land` defines beside the `Fixes` line.
   Its numbers come from the reviewer's reports (the severity each `CONFIRMED` finding names) and
   from the session's own round tally and record reading, as the `adversarial-review` line's do,
   and are taken again after every commit. `record-counts.py` lists none of its numbers.
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
   reads them, each sentence a claim against the tree at that commit. A change with no fix round
   sends the record bullet, the PR body, the counts line and the answer line, naming the head
   the review read — a reading of the record, not a second review — which is the channel the
   e2e skill's "whoever reviews the diff reviews the message with it" names. The list of
   untraced counts from step 6 goes with the record, in either message. Each listed count is a
   finding of that reading, and before the PR is marked ready it is fixed, taken again from a
   report or from a command run at the head, or justified, the record naming its source. The
   script's exit 1 says only that the list is not empty; it does not stop the run.
   **A prose round** — a fix round in which nothing executed changed — is verified by reading,
   and the contract's cap does not count it. Show the identity in the PR body: for every source
   or test file the fix commit touches, its token stream from the language's own tokenizer with
   comment tokens removed is identical to the commit the standing verdict read, beside a
   control: the same comparison against a commit whose executed lines in those files differ
   reports a difference, or the identity has shown nothing; a file no earlier commit holds has
   no control, and its round is ordinary. It is an
   ordinary round instead when any token differs; when the fix touched something executed that
   cannot be tokenized that way (a workflow, a template, a command block, a build file, a
   fixture, data, a configuration file); when the fix reworded a docstring the file itself
   reads, which a comparison blanks but the code prints; or when it changed a comment something
   else executes — a `#requires`, a shebang, an encoding line, a lint pragma, a marker a gate
   greps. Unsure means ordinary. The gates re-run on the fix commit as after any fix:
   comment-based help is comment to a tokenizer and is still what a help gate reads. A
   verification that finds anything executed did change shows the round was not prose, and it
   counts. The bound is two prose passes on a sentence, then the sentence is deleted rather than
   reworded a third time — a sentence the issue's acceptance requires is a STOP instead.

## Light mode

`/ouro:review --light <range>` dispatches the same reviewer, at `[models].reviewer`'s model
(default opus), with the same read-only brief, minus the probe requirement and the REJECTED
ledger: build the prompt from the light variant in [`REVIEWER-PROMPT.md`](REVIEWER-PROMPT.md),
which replaces the ledger with one line per commit naming the files the reviewer read and the
gates it ran — light mode's evidence that it looked. Every CONFIRMED finding still carries
`path:line` and the wrong outcome it produces, the reviewer still runs the binding's gates, the
claims block stays (the record is in no diff, so light mode reads it the same way), and the
stale-doc sweep stays. Steps 3–4 apply unchanged; step 5 becomes: every commit in the range has
a READ line naming the files that commit changed — a commit without one was not reviewed, send
it back — a vector listed UNTESTED is still open, check it yourself or send it back; one that
stays untested (a behaviour only another platform or a CI runner exercises,
or code with no test host, which no test project in the repository can load (UI code-behind,
host glue)) is named with why and with what covers it, a CI leg or the PR record, or as
uncovered if nothing does; where code has no test host the coverer is a `Smoke by hand:` item in
the PR body's Merge danger section, which is the PR record, and in a `trivial` run, which no
person merges, it counts as uncovered; and a fix that makes the diff executed sends the range
back in the probe format, which is that change's one probe review; step 6 records it as
`adversarial-review (light): C claims: T true, F false, V unverifiable; N confirmed, fixed by
class in <sha>; S standards findings, fixed or declined; U untested; read: <the READ lines>;
verified at <fix sha>`; step 7's
verification is a reading one, its answer a READ line for the fix commit. A prose round (step 7)
is verified this way after a probe-format review too; in light mode this record already ends in
that reading, so there it adds `; P prose rounds` alone.

Which weight a diff takes: wherever `/ouro:review` runs, a diff with any executed change takes
the probe format, at any size; light mode (`/ouro:review --light`) is only for a diff that
changes nothing but documentation, skill text, rules or comments, and every other diff takes the
probe format too. A change is **executed** when something runs it: scripts, workflows and gate
files, CI or build configuration, gate code and gate fixtures, parsers, release machinery,
product code, and any command block a skill or template tells a session to run. A comment-only
change inside a script, a workflow, a gate file or such a command block is executed; a
comment-only change anywhere else (product code, parsers) is a comments change and takes light
mode. Executed wins when both could apply. Under `/ouro:execute`, every M takes this review
before the PR is marked ready, whatever `[ship].review` names, and an S takes it under `adversarial-review`; under
`copilot` and `none` an S gets only the reviewer the binding names; under `external-audit` it
gets the named reader, or `/ouro:review` in its place when the reader is unavailable
(`docs/binding.md`).

## When not to use

- A contract seam (IPC, C-ABI, serialization, public API) → `/ouro:seam`.
- A plan or design, judged on its merits rather than against a diff.
- A one-line question ("is this regex right") that needs no repository read.
- A diff that changes nothing but documentation, skill text, rules or comments (Light mode,
  above) → **light mode** (`--light`), not the probe format; every other diff takes the probe
  format. `external-audit` in a scoped `--paths` round is the external alternative to light mode.
