---
name: drift
description: >
  LLM semantic drift audit of repo docs against their artifacts — the layer
  above whatever deterministic docs gate the repo runs (which checks links and
  paths, not meaning). Use when the user asks to audit docs for
  drift/staleness/correctness ("audit the docs", "check docs vs code", "docs
  drift", "are the docs still true"), names a doc area to verify, or when
  running the weekly CI audit (--targets JSON from Get-DriftAuditTargets.ps1).
  Verifies claims against workflows/scripts/code/live state via verifier
  subagents and reports categorized findings with two-sided citations;
  report-only — never edits docs.
---

# Docs drift audit — `/ouro:drift [<scope>] [--targets <json>] [--issue <N>] [--ci] [--deep]`

Two layers. Whatever deterministic docs gate the repo runs, if any, proves
links and paths resolve; this skill is the layer above it — it proves the
PROSE is still true. It never edits anything; the output is findings.

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the
marketplace checkout.

## 0. Bind

**Interactive.** Read `.claude/ouro.toml` at the repo root (`[repo].checkout` if set, else the
cwd; read it via `python3 <ouro>/bin/ouro-binding.py get repo.checkout` — a gitignored
`ouro.local.toml` overlay may supply it; the key is optional, and `no such key` on stderr at
exit 1 is that read's answer for unset).
No binding, or `python3 <ouro>/bin/ouro-binding.py check` fails → refuse.

**CI** (`--ci`). Read `.claude/ouro.toml` in the working directory; no binding → refuse. Run
no `ouro-binding.py` command: the weekly pass runs its check before this session starts and
fails the job there. A CI run without `--issue <N>`, a number, refuses: the ledger is resolved or
filed by the pass, never found or filed by this session.

Either mode: take `[repo].slug` as the `<slug>` every `gh` call below names and any
`[overlays].drift` entry as the list of point-in-time paths to skip. The ledger issue is
`[rolling_issues].drift_audit` by exact title; in CI it is the issue `--issue` names.
`[models].verifier` (default `opus`) is the `model:` every step-4 verifier `Agent` call passes,
absent `--deep`.

## Modes

- **Interactive**: `/ouro:drift <scope>` — scope is a doc area ("CI docs", a
  component's READMEs, a directory, an explicit file list). Report findings in
  chat; applying fixes is a separate follow-up the user approves.
- **CI**: `/ouro:drift --targets <json> --issue <N> --ci` — targets come from
  `<ouro>/bin/Get-DriftAuditTargets.ps1` (never self-select in CI), and the ledger's number
  from the weekly pass. Rewrite that issue (below) instead of chatting.
- **--deep** (interactive only): pass `model:` set to the session's top model
  tier on each verifier Agent call, for a sweep-grade audit. Never the CI
  default — usage cost.

## Procedure

1. **Resolve the target list.** CI: read the JSON — `targets[].path` with its `reason`
   (`evidence` = something it cites moved since its own last audit, with the changed files
   named in `via`; `stale` = longest unverified), plus `overflow`, `bootstrap`, `head`,
   `ledgerSize`. Every target also carries `staleClaims`, a count, and `claims`, a list, both 0
   and empty when nothing is stale. When `via` reads `stale claims: <n>`, `claims` holds that
   doc's stale claims, one entry per range: the claim's `statement`, the `path` it cites, the
   old `range` (start and end line) and the `class` (`changed`: the lines no longer read as
   recorded; `gone`: the file is no longer there). Interactive: enumerate the scope's files yourself — tracked `.md` only;
   skip the paths the binding declares point-in-time (`[overlays].drift`), `CHANGELOG*`,
   gitignored trees.
2. **Group by subject area**, docs sharing ground-truth artifacts together; a large doc
   can be a group alone. Aim for **~5 docs per group**, up to 10 groups — the selector
   can hand you 40 targets, and squeezing those into a few groups buys breadth by
   spending the depth that finds anything. Past ~50, audit what fits and list the rest
   under `## Unaudited` — never silently shrink.
3. **Compute each doc's code delta first.** For every doc, get what moved underneath it
   since it was last touched. Both modes run two plain `git log` commands, one at a time: the
   first gives the doc's last commit time, which you write into the second with the doc's
   directory (`.` at the root). Write the values in: the CI allowlist denies the same pair
   built with `$( )`. In a single-quoted literal, here and below, a `'` is written `'\''`.

   ```bash
   git log -1 --format=%cI -- '<doc>'
   git log --oneline --since=<that timestamp> -- '<doc-dir>'
   ```

   Pass that commit list into the group's prompt and **name the highest-risk commit**
   ("a repo-wide `_camelCase` field rename" beats "some refactors"). This is what turns
   a generic "check this doc" into a hit: a verifier told which sweep to suspect finds
   the identifiers it renamed. Skipping this step is the single biggest yield loss.
4. **Spawn one `verifier` subagent per group** (the plugin's `agents/verifier.md`) —
   parallel, fresh context each; groups must never share conclusions. Give each agent:
   its doc list, the likely ground-truth artifacts (CI workflows, scripts, project and
   solution files, the code the docs describe), the step-3 delta, the point-in-time
   exclusions, and the turn budget the plugin's `docs/contract.md` sets — reaching it, the
   agent reports the findings it has and names the docs it did not finish. With `--ci`, ask
   each verifier for its claim records too; interactive, ask for none. Pass step 0's
   `[models].verifier` as each call's `model:`; `agents/verifier.md`'s own front matter
   (`model: opus`) is the fallback for a call that passes none. With `--deep`, pass the
   top-tier `model:` instead, on every call.

   With `--ci`, when a doc's `staleClaims` is above 0, name its `claims` first in the brief,
   each with its statement, path, old range and class, and ask for a verdict on each before the rest
   of the doc: does the claim still hold in the tree at `head`. The agent still audits the
   whole doc; a stale claim is where it starts, never where it stops. The verifier answers with
   the claim-verdict lines `agents/verifier.md` defines; step 6 writes each as a `verdict-records`
   line.
5. **Consolidate**: dedupe cross-group findings, drop anything lacking two-sided
   evidence, keep AMBIGUOUS items as explicit questions for the human.

   **Re-read the enclosing scope of any identifier finding before accepting it.** A
   correctly-quoted line can still support a wrong conclusion: a verifier can cite a
   real `file:line` for a same-named member in a nested type and "prove" a doc example
   broken that the real member, further down, makes correct — and "fixing" it would
   break the example. Nested types, overloads, partials, and same-named members at
   different levels all produce this. Two-sided evidence is necessary, not sufficient.

   **Check the ledger issue's recorded false positives.** A finding the owner already
   rejected in the rolling issue is closed, not new; re-reporting it costs the audit
   its credibility. When the owner rejects one, it stays recorded there for this
   reason. Interactive, find the ledger by exact title, case included, in any state:
   `gh issue list -R <slug> --search '<title> in:title' --state all --limit 200 --json
   number,title,state`, then only an exact `title` match counts, since `in:title` alone is
   token search and hijacks look-alike titles.
6. **Deliver.** Findings describe what this run read — the tree at its commit (in CI, the ledger
   marker's `sha=`) plus any live state a verifier probed — and age from there: whoever applies
   them re-verifies each one against the current tree and state first.
   - Interactive: categorized findings in chat, most severe first, counts at the end, then every
     doc a verifier did not finish; the report opens with the commit read
     (`git rev-parse --short HEAD`) and says if the tree was dirty.
   - CI: rewrite the rolling issue `<N>` that `--issue` names. The weekly pass resolved it
     before this session started: an open exact-title match wins, a closed one is reopened only
     when none is open, and when there is neither, one is filed with `umbrella`. So this session
     selects, reopens and files nothing, and its grant holds neither the reopen nor the create
     verb. Write the slug, the issue number and the body into each command as literals: the CI
     allowlist denies a command built from `$( )`, `||` and `exit`, and a redirect that writes a
     body file. Every call names the repository with `-R <slug>`: a bare
     `gh issue` reads and writes whatever repository gh picks from the clone's remotes, the
     parent in a fork's clone. A nonzero exit from any command here stops the run.

     Pass the body through a quoted here-doc, which keeps the findings' backticks, `$` and
     quotes literal. Its end marker must equal no line of the body, or the here-doc ends early
     and the rest runs as commands: `DRIFT_LEDGER_BODY` below, another marker if a body line is
     exactly that:

     ```bash
     gh issue edit <N> -R <slug> --body-file - <<'DRIFT_LEDGER_BODY'
     <body>
     DRIFT_LEDGER_BODY
     ```

     Body layout: one section per category with the findings, and an `## Unaudited`
     section (the JSON's `overflow` + any group whose verifier died + every doc a verifier
     named as unfinished at its turn budget).

     Then **append one comment per audited doc** — never edit an existing one — carrying that
     doc's findings (or `no findings`) and, last, a `claim-records` block with the records the
     verifier returned for it, one JSON line per record, in this form:

     ```
     <!-- claim-records: sha=<40-hex head> doc=<JSON string>
     {"s":<statement>,"p":<path>,"r":[<start>,<end>]}
     -->
     ```

     `sha=` is the `head` the targets file names, and `doc` is the doc's path as a JSON string.
     Every JSON string in the block writes `<`, `>`, `&`, a double quote and a backslash as the
     entities `&lt;`, `&gt;`, `&amp;`, `&quot;` and `&#92;`, so no statement closes the comment or
     spells a marker; a JSON escape will not do, since a tool call can decode it before the
     command runs. A doc with no confirmed
     claim gets no block, and so does a doc you leave out of `docs=` below. Post each of these
     comments the way the body is passed above, with `--body-file -` and its own end marker.
     **You never write an `audit-claims` marker**: the workflow step after this session reads
     the blocks of this run's comments, hashes each range from the tree at `sha=`, drops what it
     cannot verify, and appends the markers itself. A block you write is data until that step
     has hashed it.

     A doc whose targets entry named stale claims also gets a `verdict-records` block in that
     same comment, beside its `claim-records`, with the same `sha=` and `doc=` header and the
     same escapes. It holds one line per entry of that doc's `claims`: the entry's statement,
     path and class, and the verdict on its claim, `true` when the verifier found the claim
     still holds in the tree at `sha=` and `false` when it found the claim no longer holds or
     could not confirm it:

     ```
     <!-- verdict-records: sha=<40-hex head> doc=<JSON string>
     {"s":<statement>,"p":<path>,"c":<"changed" or "gone">,"v":<true or false>}
     -->
     ```

     A claim the verifier gave no verdict gets no line. **You never write a `claim-verdicts`
     marker either**: the same workflow step reads the `verdict-records` blocks of this run's
     comments by the same checks, drops a line that does not parse, and appends one marker for
     the run.

     Then **append a new comment** — never edit an existing one — carrying this run's
     ledger entry. Keep the format EXACT; the next run parses it:

     ```
     <!-- audit-run: sha=<head> docs=<comma-separated paths> -->
     ```

     `docs=` is the targets you **actually audited**: every `targets[].path` MINUS
     everything listed under `## Unaudited`. This is the one place the audit can lie in a
     way nothing else catches — a doc stamped here is treated as verified and will not be
     re-selected on staleness for weeks. A verifier that died audited nothing; say so by
     omitting its docs. Omitting a doc is free (it simply ranks high again next week);
     wrongly including one hides it.

     A zero-target run still rewrites the body ("no drift candidates") and appends a
     marker with an empty `docs=`, so silence stays distinguishable from breakage.

## Non-negotiables

- **Report-only**: no Edit/Write on repo files under this skill, ever. Fixes are a
  separate, human-approved pass. When that pass defers work to an issue, file it to
  the agent-ready contract carrying `needs-triage`, or `needs-ruling` plus the question;
  a promotion goes through `/ouro:triage <N>`.
- **Artifact wins**; every finding cites both sides file:line. The verifier agent
  enforces this — do not water it down while consolidating.
- **A verifier failure shrinks the audit, not the truth**: list its group under
  `## Unaudited` rather than silently dropping it.
- **Don't duplicate the deterministic gate**: where the repo runs one, broken
  links/paths are its findings, not yours — skip them unless the surrounding claim is
  also semantically wrong.
