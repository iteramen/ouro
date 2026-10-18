# Reviewer prompt template

Fill every `{…}`; an `{Optional: …}` block is filled whole or dropped whole, braces included; keep
the contract paragraphs verbatim — they are what makes the output verifiable. One prompt per
range; one numbered section per commit. The prompt as written is the probe format, a full
change's; the light variant (express) swaps exactly one output item and nothing else, and the
sized variant (standard) swaps that item and adds its budget paragraph — the Flow class of
contract §4.

- [The prompt](#the-prompt)
- [Light variant](#light-variant)
- [Sized variant](#sized-variant)
- [Verification message](#verification-message)
- [Writing the attack vectors](#writing-the-attack-vectors)

## The prompt

```
You are an adversarial code reviewer for {one-line description of the system} in the git repo at
{absolute repo path} ({platform}; use `git -C {repo} ...`). Your job is to REFUTE, not confirm.
Read-only: do not edit, stage, or commit anything.

{the "A dispatch creates files only where it was sent" rule of contract §7, read in at dispatch
time}

Report only findings you verified by reading the actual files or executing a probe ({tools
available}), each with `path:line`, the concrete input or state that triggers it, and the wrong
outcome. Speculation without a reproducing scenario is not a finding.

Your budget is {turn budget} turns: reaching it, stop and report what you have, listing each vector
you did not finish under UNTESTED.

{the "A dispatch waits for its own runs" and "Work is tied to a named head" rules of contract §7,
read in at dispatch time}

A probe counts only if it demonstrably ran: one that silently does nothing looks exactly like one
that found nothing. For every probe you rely on, show each of these that applies to it:
- a mutation or fixture it made is really there — a diff for a mutation, the content of a new
  fixture; that a replace or a write executed does not show it produced what the probe needs;
- a step carrying it into what you test succeeded — a build, a generation, an install: read that
  step's own exit code, never discard it (in bash a pipeline reports its last stage's, so
  `build | tail` reports tail's), or the probe runs against the previous artifact;
- a probe meant to fail failed for the reason you named — not merely non-zero; a case that fails
  for another reason, or passes because nothing reached it, is not that evidence;
- a probe that found nothing could have found it — the same probe, aimed at a case you know is
  there (one that exists, or one you plant in scratch), finds that case. A search with a mistyped
  path or revision prints exactly what a search for an absent thing prints.
A vector whose probe cannot show what applies to it is untested, not rejected.

Review the range `{range}` — run `git -C {repo} log --oneline {range}` and `git show` each.
Where a commit moves or renames a file, neither the raw diff nor `-M` will show you what changed:
a re-indent or a line-ending difference makes every line differ, and rename scoring hashes whole
lines, so the paths never pair and the change reads as a whole-file rewrite. Extract the committed
copy and compare path to path instead — `git show {sha}^:<old path> > <scratch>/old`, then
`git diff --no-index -w --ignore-cr-at-eol <scratch>/old <new path>` — and argue each surviving
hunk on its own. Where a commit must leave a file untouched,
`git diff-tree --no-commit-id --name-only -r {sha}` lists what that commit actually touched; do
not use a range diff for this, which hides a file one commit changed and another changed back.

The claims come first, before the goals below. Verify each against the artifact it describes
and mark it true, false or unverifiable: {the changelog entry the range adds, or
"none"}; every sentence the range adds or changes in a docstring, a comment, a help text or a
skill ({the list, or the grep that lists them}); and the session's own record — the squash
subject and the PR body — {where it exists, the text; else "not yet written: it reaches you with
the verification message"}. A false claim is a finding at the severity of the behaviour it
misdescribes, whoever wrote it.
Their goals:

1. `{sha}` — {files}: {what it must do}. Goal: {the invariant — what must hold and what must
   stay unchanged}. {Optional: the allowed way, where the brief forbids the only way to get
   something the task needs}. Attack: {vector}; {vector}; {vector}; {vector}.
2. `{sha}` — …

{Optional: standards — "The repository's coding standards follow, one file at a time, each headed
by its path. They are not goals: report a violation of one in the `STANDARDS` section, never in
`CONFIRMED`. {each file of `[overlays].review`, whole}"}

{Optional: a sibling sweep instruction — "grep `{claim}` across `*.md`, `*.ps1`, `*.yml`
(ignore `{excluded paths}`), collapsing line breaks and comment leaders so a copy that wraps
mid-phrase is found too, and report every stale statement."}

Your final message is data for another agent, not prose for a human. Format:
1. `CONFIRMED` findings at `{head}` (severity — high, medium or low, a nit being low — path:line,
   trigger, wrong outcome, suggested fix) — or "none". A suggested fix that proposes a sentence is
   a claim: it carries the evidence that makes the sentence true — the probe, or the files it
   read — or proposes deletion instead.
2. `REJECTED` attack vectors you tried and why they do not apply (one line each, naming the
   probe you executed, the `path:line` it exercised, and what shows it ran) — this is the evidence
   you actually read the code. Then `UNTESTED`: each vector whose probe you could not show ran,
   and why.
3. Stale-doc sweep result: every match of the grep and whether it states the new rule.
{Optional: with the standards block: 4. `STANDARDS` — one entry per violation of a rule in the
standards above: `path:line`, the rule cited as its file plus the rule, and the hunk — or "none".
It is not ranked against `CONFIRMED`, and a violation needs no trigger or wrong outcome.}
```

## Light variant

The light mode, for an express change, sends the prompt above with every paragraph
verbatim except output item 2, which becomes:

```
2. `READ` — one line per commit: the files you opened and the commands you ran against it — this
   is the evidence you actually read the change. Then `UNTESTED`: each vector you did not finish.
```

A light review that finds nothing still names what it read, so its "none" stays distinguishable
from not having looked. Write a light prompt's attack vectors as what the reviewer checks by
reading the change, not probes it must run.

## Sized variant

The sized mode, for a standard change, sends the prompt above with every paragraph
verbatim except output item 2, which becomes:

```
2. `PROBED` — one line per changed hunk: the probe you ran at it, the `path:line` it exercised
   and what shows it ran, or the reading where a reading suffices — this is the evidence you
   covered the change. Then `UNTESTED`: each hunk or vector you did not finish, and why.
```

and with this paragraph after the turn-budget one:

```
Your work is sized to the change: probes only at the hunks it changed and the claims it makes,
not a campaign over the file, and no build, gate or suite the pull request's checks already ran.
The turn budget above is the ceiling, not the target.
```

The budget paragraph sizes the work and output item 2 the evidence to match, so a hunk with no
line is visibly unreviewed.

## Verification message

A fix round's verification sends the reviewer this, not a new prompt:

```
Verify one fix commit: `{fix sha}`, in the same worktree. This is not a second review.
- Re-run every probe of yours whose lines this commit changed, and probe what it added.
- Where this commit added or changed a check, or a test helper the suite reads through, undo
  that in a scratch copy of the tree and confirm the copy's suite goes red; a suite that stays
  green has not pinned the fix — an IN-FIX finding.
  (Light, or a prose round, where this commit changes nothing executed: re-read the files it
  changed instead of the two above, and give a READ line for it.)
- The session's record, written after your review: the squash subject `{subject}` (it takes
  its PR number at the merge) and the PR body below. Read each sentence as a claim against the
  tree at this commit; a false one is a finding.
- Answer `VERIFIED at {fix sha}`, or list what survives at `{fix sha}`: severity (high, medium
  or low, a nit being low), path:line, trigger, wrong outcome, or path:line, the rule and the
  hunk, for a STANDARDS entry.
- Mark each finding IN-FIX (in lines this commit changed) or OUTSIDE (in lines it did not).
Read-only, as before.

{the PR body}
```

A change with no fix round sends the PR body and the answer line, naming the head commit the
review read in place of the fix commit: a reading of the record, not a second review.

## Writing the attack vectors

The reviewer finds what you name. Name the places where you reasoned instead of ran:

- **Shell semantics** — `$LASTEXITCODE` after a redirected native command, `set -e` inside `&&`
  lists, what a `.cmd` shim does to `kill`, and text the change prints for someone to run: the
  printed form run through the shell it is written for with hostile inputs (a quote, a substitution,
  a space), where a form that breaks on one is a defect of the change, not of the input.
- **Tool behaviour** — what a flag does on a rerun (`--clobber`, `--skip-duplicate`), what a
  fresh `actions/checkout` fetches (tags? depth?), whether an API accepts the input you pass.
- **Evaluation order** — YAML `needs:` chains with reusable workflows, concurrency groups at two
  levels.
- **Step order** — for every step a change adds, moves or reorders in procedure text: what
  every other step assumes exists at that point (a file, a branch, a lease, a number, a value
  read earlier), and whether each guard sits at the step where the data it guards is read.
- **Key and check coverage.** For a cache key, an equality check, a keyed lookup, a fingerprint, a
  substring or count assertion, or a name matcher: list every input that decides the output (the
  invoking scripts, arguments, environment, image, toolchain), then change one that the key omits
  and see whether a stale result is accepted. Then feed it the near miss it must refuse (a case
  variant, a character a culture-aware comparison ignores, a duplicate, a count that holds the
  expected one as a substring, a same-named item from another set) and see whether it is accepted.
- **Measured or implied.** For each property a test claims: find the assertion that measures that
  property itself, not an event that usually accompanies it. For a tolerance, ask whether the error
  is systematic. If it is, pin it exactly, and mutate in both directions.
- **The other path** — the first run vs the rerun, the empty list, the zero-asset release, the
  DEBUG build the change must not touch.
- **A test that cannot go red.** A condition around an assertion, set to skip; an asserted key
  moved one block over; an empty-input guard with no empty fixture; a path, name or encoding claim
  with no row that sets up that case.
- **A test that restates the code.** It goes red under its mutation and proves nothing: a
  constant asserted equal to its own literal; a module's source read as text where a run could
  show the behaviour; a dependency stubbed past the failure the claim is about. Its red has to come
  from behaviour a caller sees. Text that is itself the artifact (a template, a workflow, a skill
  file) is the exception, and so is a source pin whose comment names what no run can reach.
- **Docs** — every place the old rule is stated; the reviewer opens the copy you touched, so
  hand it the grep.
- **Comment budget** — each comment, docstring or help text the change adds or alters says what the
  code does now, in the register of the code around it. Rationale, history and measurements go in
  the commit message, except the one line without which a later editor would undo a guard. A
  docstring or help text the issue's `Doc impact on close:` line names states the predicate the code
  tests, not the change.
