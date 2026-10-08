---
paths:
  - "**/*.md"
---

# Documentation rules

<!-- ouro template — copy to .claude/rules/documentation.md and adjust for this repo.
     The agent-facing short form of the plugin's docs-governance doc (copy that one in too if
     the binding is to cite it). The loop contract is the authority for everything issue-side;
     this file summarises, it does not override. Keep the three from disagreeing. -->

## Naming

`README.md` is for directory overviews only ("what's in this folder"). Everything else is
`kebab-lower-case.md`, named by topic — `math-expressions.md`, not `TERMINAL_MATH.md`.
Uppercase filenames are reserved for the conventional set (`README.md`, `CHANGELOG*.md`,
`CLAUDE.md`, skill/agent manifests like `SKILL.md`, tool-mandated files) — never invent one.

## Location

Feature docs live **beside the feature** — a doc for a subsystem goes in that subsystem's
folder, not in the shared docs tree. Cross-cutting docs (CI/CD, team process, architecture
spanning modules) live in the docs tree.

If this repo's binding declares a doc index, that file indexes the subsystem docs and you add an
entry when you create one. The gate-enforced scope is whatever indexed trees the binding names,
minus its index exemptions. If the binding declares no index, this repo has no index convention
and nothing is required here.

## Referencing something that does not exist yet

A reference to a file, path, or doc that is **planned but not written** carries a literal
`(planned)` marker on the same line:

    Ties into [workspace docking](docs/workspace-docking.md) (planned).

The gate reports an unmarked missing reference, and fails the build on it wherever that signal
is not report-only. The marker suppresses only when its line has exactly **one** reference — a
link target, a link's anchor, or a backtick path. A line naming two, even of different kinds,
still reports both normally, so the marker can never silently hide a second, real break riding
along next to the intentional one. **A backticked path inside the link text counts as a second
reference**, so keep the link text prose.

A marker is a claim that expires. When the target gets written, remove the marker in the same
change. A stale marker pre-suppresses: if that path later gets reused or the file moves, the
break goes undetected instead of failing the gate.

## Fixing a broken reference

"This file does not exist" is equally consistent with three different situations that demand
opposite fixes: it is **planned** (mark it), it was **deleted** (reword or remove the claim), or
it **already landed somewhere else** (fix the path). Only `git log` distinguishes them —
`git log --diff-filter=A` for when something arrived, `--diff-filter=D` for when it was removed.
Checking only whether the path resolves today will confidently produce the wrong fix.

And when you retarget a path, re-read the sentence around it: a corrected path inside a
now-false claim satisfies the checker and misinforms the reader.

## Forward-state framing — docs are current state, git is history

Every doc describes the current target state. Banned everywhere, **including planning docs**:

- "previously X, now Y" / "stayed → moved" reconciliations
- dated migration narratives and slice-by-slice progress logs
- verification ceremony — test counts, "builds clean", "exit 0", merge/squash hashes, PR numbers

When work lands, **edit the current-state description in place. Never append a progress
entry.** A doc answers *what is true now / what's next / why it's this way*. A commit or PR
answers *what changed / how we got here / did it pass*.

## Undone work goes to issues

The test for any paragraph: does it describe what the system **is** (doc) or what work
**remains** (issue)? TODO lists, `## Remaining Work`/`## Future Work`/`## Roadmap`/`## Next
steps` sections, status ledgers, phase plans, and checkbox lists go to issues — distill (claims
verified against code, cited by symbol + verbatim fragment), then delete the section in the same
pass. Exceptions that stay in docs: known-limitations sections, procedural runbook checklists,
documented non-adoption rationale, and the planning sections of policy docs. **There is no
planning tree** — a doc that describes undone work is an issue that has not been filed yet.

A procedural checklist that is a per-run template rather than tracked work is declared as a
planning path in the binding, not silenced with a suppression entry.

Issue side, in full in the loop contract §3 and summarised here only so it is in front of you:
one issue = one deliverable + a `Doc impact on close:` line naming the doc(s) edited when the
work lands ("none" said explicitly). Umbrella issues stay curated current-state indexes. Code
references anchor on symbol + verbatim fragment; line numbers are advisory (stamp the SHA).
Cross-repo citations have their own spelling — the contract §3 is the authority, not this file.

## No artifact clutter

No standalone documents for bug fixes, code-review findings, or resolved discussions — a fix
lives in the code and the commit message. If a decision matters going forward, update the
relevant spec. Delete superseded docs; don't archive them. The one sanctioned exception is a
dated incident-investigation tree, whose point-in-time nature *is* the content.

## No future-plan cross-refs in descriptive docs

Architecture docs, subsystem references, and operational guides describe what exists — no
"see issue #NN", no "tracked in roadmap", no "future feature X coming". Those rot the moment
work lands. The exception is a **documented non-adoption**: naming a design that was
deliberately *not* taken, and the trigger that would reopen it, is current state. Its execution
plan is not — that is an issue.

## Compress aggressively

No duplication across docs — link instead. Cut ceremony, framing paragraphs, and restated
context. Shorter is almost always better. Documentation is part of the work, not a follow-up.
