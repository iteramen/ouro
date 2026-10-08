---
name: external-audit
description: The ouro-shipped external reader named by `ship.review = "external-audit"` — a repo-reading, JSON-verdict adversarial review, capped at two rounds, run through one of several CLI adapters named by `ship.external_cli` (`grok`, `claude`, `codex` and `copilot` are shipped). Two modes. DIFF mode reviews a git diff: use when `/ouro:execute` §4, `/ouro:land` step 6 or `/ouro:fuse` step 5 name this reader, or when the user asks for an external audit / second opinion on a diff before landing. CLAIMS mode refutes a plan's factual assertions about the code, each with the file:line it cites, returning per-claim CONFIRMED/REFUTED/MISLEADING/UNVERIFIABLE — use before committing to a rework whose shape depends on those readings being right. Claims mode needs no diff and can span several repositories. Not for judging a plan's DESIGN or priorities, and not for session-context questions.
---

# external-audit

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the marketplace checkout.

## 0. Bind

Read `.claude/ouro.toml` at the repo root (schema: `docs/binding.md`); refuse without it.

- **The CLI.** `--cli` when this run was invoked with one directly (a manual, ad-hoc run) — else
  `ship.external_cli` (`python3 <ouro>/bin/ouro-binding.py get ship.external_cli`). `/ouro:execute`
  §4, `/ouro:land` step 6 and `/ouro:fuse` step 5 leave `--cli` to this skill's own read above
  rather than passing it themselves. Refuse, naming both places it could come from, with neither.
- **The model.** `ship.external_model` (`ouro-binding.py get`; `no such key` at exit 1 means
  unset), or `"default"` when absent. Passed to the driver as `--model`.

The reader's contract — read-only, a JSON verdict naming the files it read, two rounds at most,
a third only for a new blocker — is stated once, in `docs/binding.md`'s "Choosing `ship.review`";
this skill follows it rather than restating it.

One driver, one round: `python3 <ouro>/bin/external-audit.py --cli <cli> [--model <model>]`
builds the brief, runs the named CLI read-only against the repo (or `--cwd`), parses the JSON
verdict, and drops every artifact under `%TEMP%/external-audit-<user>/<session>/round-N/`. Exit 0 =
APPROVE, 2 = REVISE, 1 = transport/parse failure, an unknown `--cli`, or one not found on PATH,
or a `.cmd`/`.bat` shim given an argument holding one of `&|<>^%`, **3 = unfalsifiable** — an
APPROVE carrying neither a critique nor a `files_read` entry, i.e. nothing showing the repo was
opened. A 3 is a failed round, not a round: re-run it once — a
second exit 3 counts as no verdict, and "When the reader is unavailable" (below) applies.

Shipped adapters: `grok`, `claude`, `codex`, `copilot`.

Two modes, one round discipline. **`--range`** judges a change against what it must do.
**`--claims`** refutes a plan's factual assertions about the repo — before any code exists
to diff.

```bash
DEFAULT=<[repo].default_branch>
# round 1 — mints the session id and prints it
python3 <ouro>/bin/external-audit.py --cli grok \
  --range "$(git merge-base origin/$DEFAULT HEAD)..HEAD" \
  --goal "The new lint must FAIL the run when a diff ADDS a source line that names a retired symbol; comment lines and string literals must not count; removed/context lines never count."

# claims mode — one file of numbered claims, each carrying the file:line it cites
python3 <ouro>/bin/external-audit.py --cli grok \
  --claims claims.md \
  --goal "A rework plan rests on these readings of the code; the plan itself is not under review."

# round 2 — after fixing the CLASS of every finding and running the probe matrix; --digest is
# round 1's own triage-template.md, filled in, at the artifact path round 1 printed
python3 <ouro>/bin/external-audit.py --cli grok \
  --range "$(git merge-base origin/$DEFAULT HEAD)..HEAD" --goal "..." --resume <uuid> \
  --digest "${TMPDIR:-/tmp}/external-audit-<user>/<uuid>/round-1/triage-template.md"

# round 2 with codex — it mints its own session id rather than accepting round 1's <uuid>, so
# --resume takes the id round 1's "resume with" line printed, not the <uuid> the "session ...
# round 1 ->" line announced at the start; --digest still names the triage-template.md filed
# under that announced <uuid>'s folder, since the folder itself is never renamed
python3 <ouro>/bin/external-audit.py --cli codex \
  --range "$(git merge-base origin/$DEFAULT HEAD)..HEAD" --goal "..." --resume <id-from-the-resume-with-line> \
  --digest "${TMPDIR:-/tmp}/external-audit-<user>/<uuid>/round-1/triage-template.md"
```

The range is the one `/ouro:execute` and `/ouro:land` give `/ouro:review`. A two-dot range against a
default branch that moved after the branch forked diffs two trees, and reviews the base's newer
commits in reverse.

## When the reader is unavailable

When the CLI `ship.external_cli` names is not found, round 1 exits 1, round 1 exits 3 twice,
or the run may not call it (a policy or network rule forbids it), `/ouro:review` takes
`/ouro:external-audit`'s place, and the record says so. Stated in
`/ouro:fuse` step 5; `/ouro:execute` §4 and `/ouro:land` step 6 apply it.

A round, in either round, whose CLI exited non-zero on a billing, quota or auth cause is not re-run:
it fails again however often it is re-run, and `/ouro:review` takes the reader's place for the rest
of the change. The driver prints `<cli> exited <code>` with the last lines of its stderr, or names
`stdout.txt` when stderr was empty; read the whole file the message names.

## The round discipline (the part that saves the afternoon)

A strong reviewer with file access will always find one more edge in a lexical rule.
Five rounds on one check happened because each round fixed the *instance* it named.
Hard cap: **two rounds**; a third only for a *new blocker*.

1. **Round 1** — `--goal` states what the change must do in one paragraph; that is the
   only thing the critic judges against. A change with several independent fronts (build
   wiring / CI scripts / docs) reviews better as **parallel scoped sessions**, one `--paths`
   + `--lens` per front, each on its own two-round budget — one monolithic prompt spends its
   attention on the biggest file. Edits made while another session is still running
   trip that session's dirty-tree warning; it names the paths, so read it before reverting. The
   default `--lens` covers correctness, code/doc consistency and **comment/doc truth in every
   touched file**; a mechanical sweep or rename needs its extra fronts named (`--lens "... plus
   repo conventions: one class-level member per consumer, no adjacent cleanup"`) — an unnamed
   front is invisible to the critic even when it walks right past the evidence. **For a bug fix
   that ships a regression test, name test adequacy as a front** — "can this test pass for a
   reason unrelated to the defect?" is not part of the default lens, and it is where a green
   suite hides a half-fix.
   Read the verdict. An **empty APPROVE** is caught for you — the driver exits **3** unless the
   verdict names the files it opened — but read `files_read` anyway when it is present: a list
   that misses the file the change actually turns on is a thin review wearing a receipt.
2. **Group the critiques by root cause**, not by id. Three findings about comment
   handling are one cause: "the scanner does not model comments/strings".
3. **Fix the class.** For anything lexical, write the tokenizer once (strings — regular,
   verbatim, interpolated holes — then comments — line, block, doc), not the hole named.
   **A documentation finding has a class too, and it is the easier one to miss**: the class of
   "this doc states X, the code says Y" is *every other place that states X* — the sibling in a
   cloned directory, the same claim in a policy doc, the same list in a third file. Sweep by
   claim (`rg` the assertion, not the filename), because a critic reads the diff and will only
   ever name the copy it happened to open. Every round-2 finding in a doc-heavy review is
   typically a sibling of a round-1 finding you fixed in one place.
4. **Author the probe matrix before re-review**: every case every critique named, plus
   the obvious siblings, as a throwaway commit / fixture / test — run it, keep the table
   of expected vs actual. Rejected critiques get a probe too (that is the rebuttal).
5. **Round 2** with a triage digest (`--digest`): per prior critique, `ACCEPTED → what
   changed + probe result` or `REJECTED → why (probe result)`. Expect APPROVE. Surviving
   minors become **documented limitations** in the code/doc, or an issue — not round 3.
6. Record the outcome in the commit or PR body: "external-audit (\<cli\>): N rounds →
   APPROVE; limitations: …". The verdict files stay in `%TEMP%`, never in the repo. A REVISE
   round also writes `triage-template.md` next to its verdict — one ACCEPTED/REJECTED + probe
   slot per critique; fill it in and pass it as `--digest`.

## Hygiene

- Each adapter denies writes its own way — `<ouro>/bin/external-audit.py`'s adapter seam states
  them per CLI. Grok passes
  `--disallowed-tools write,search_replace,run_terminal_command,spawn_subagent`, so the critic
  reads/greps the repo as it sees fit — the diff or claim set in the prompt is the scope anchor,
  the repo is what it verifies against. Claude passes `--tools Read,Grep,Glob`,
  `--permission-mode dontAsk`, `--permission-prompts none` and `--safe-mode`. Codex passes
  `-s read-only` on round 1 and `-c sandbox_mode=read-only` on resume. Copilot passes
  `--deny-tool=write` and `--deny-tool=shell` too, since Copilot's shell can write
  (`git log/diff --output=<file>`), and never passes `--allow-tool`,
  `--enable-all-github-mcp-tools`, `--allow-all-tools`, `--allow-all`, `--yolo`,
  `--allow-all-paths`, nor `--disable-builtin-mcps`, so the built-in GitHub MCP server keeps
  its default read-only tool set.
  The driver also diffs `git status --porcelain` before/after and names any path that changed; if
  you did not edit it, the critic did — revert, drop the round.
- Payload cap 100 KB — narrow with `--paths` rather than truncating (the summary line says
  when a diff was truncated). The prompt never lands on argv, since Windows caps a command line
  near 32K characters: each adapter's `stdin_prompt` says how it travels instead — grok reads it
  from a `--prompt-file` (`stdin_prompt = False`), claude, codex and copilot from stdin
  (`stdin_prompt = True`). Either way, size is a token budget, not an argv limit.
- Escaping is the driver's job. Never hand-build the prompt in a shell here-string:
  bash turns `\b` into a backspace and f-strings eat `{holes}` — both cost real rounds.
- Where the verdict text is read from is also per adapter (`Adapter.verdict_text`): for grok and
  copilot, the LAST JSON object on stdout — narration before it is normal, but it is the model's
  prose, **not a tool-use log**. For claude, the `result` field of the one JSON object
  `--output-format json` prints; anything else on stdout — not exactly that one JSON object, or
  `is_error` true — counts as no verdict, the same as no `{...}` on stdout at all. For codex,
  stdout is a JSONL event stream, not the reply at all — the verdict is read from the file
  `-o`/`--output-last-message` names, and only when that file is newer than this round's
  `prompt.txt`: a stale file left by an earlier attempt at the same round number is never
  mistaken for this attempt's answer. Codex also
  mints its own session id (`thread_id`, the first `thread.started` event in that stream, read by
  `session_from_output`, which skips a non-JSON line in the stream rather than failing the round
  over it) rather than accepting the one this driver mints — `--resume` takes codex's own id, from
  the "resume with" line, never the `<uuid>` round 1 announced at the start. Narration cannot be
  scored, which is why `files_read` exists in the contract: a round that read nothing and a round
  that found a real defect can leave near-identical amounts of narration, and neither names a file
  outside the verdict JSON. A round with no parseable verdict is dropped, not resent. In a
  caller's round 1 this falls back to `/ouro:review` ("When the reader is unavailable", above); a
  manual run, or round 2, may re-run it instead, and it does not count against the two-round cap.
  A round whose CLI exited non-zero is read from the file the driver's message names; a billing,
  quota or auth cause is the exception "When the reader is unavailable" gives.
- A repo-reading round on a doc-heavy diff can take 5-6 minutes (it reads the touched files
  and their helpers); the driver's default `--timeout` is 900 s and stdout streams to
  `stdout.txt` as it arrives, so a timed-out round still leaves its narration.
- **Elapsed time is not evidence of substance.** A long round is not necessarily a read round:
  `files_read` and the critiques are the evidence; the clock is not.
- **Do not run a heavy local gate while a round is in flight.** The critic's read/grep tool
  calls execute *locally*, so a box saturated by a build can starve exactly the tool calls the
  review depends on.

## Claims mode

For the moment before a rework is committed to: an investigation has produced findings, a
plan is built on them, and being wrong about the code is much cheaper to discover now than
after the sequencing is fixed. The named CLI reads the repo, so it can settle that; a prompt-only
critic cannot.

- **One claim per id, each carrying the `file:line` it cites**, stated the way the plan states
  it. Include the ones you are least sure of — a claim set that only contains your confident
  readings wastes the round.
- **State the conclusion separately from the observation.** "Line X does Y" and "therefore the
  design must Z" fail differently: the first is refuted, the second is `MISLEADING`. Ask for
  the structural conclusions to be attacked hardest — those are where a confident sweep is
  most likely to have over-reached.
- **`--goal` is context, not the thing judged.** Say what the claims underpin so the reviewer
  can spot an unsound inference, and say explicitly that the plan's design is out of scope.
- **No git call, so `--cwd` can sit above several repositories.** Name each tree in the claim
  set. This skill still runs bound to one repository (step 0's binding read); if the claims span
  repos, pass `--cwd` at their common parent rather than trying to run from it.
- **A citation you cannot find is not thereby false.** The driver tells the critic to search
  for the symbol before declaring a path absent; apply the same rule to yourself when checking
  its findings.
- Verify the refutations yourself before acting on them, the same as diff mode. A confident
  reviewer with file access will occasionally cite a real file for a wrong reason — **and the
  inverse, which is easier to mistake for a hallucination**: a correct finding hung on a path
  that does not exist. Judge the claim, not the citation: a bad path refutes the path.

Output is per-claim, plus `new_findings` for anything material the claim set missed. The
console prints only the non-`CONFIRMED` rows — those are what the plan has to answer for —
and writes a triage template for round 2.

## When not to use

- Judging a plan's DESIGN, priorities or sequencing — a different question from claims mode's,
  which judges only whether the plan's statements about the code are true.
- Trivia ("is this regex right") that needs no repository read.
- Anything that needs the session's conversation — the reader sees the repo and the brief only.
