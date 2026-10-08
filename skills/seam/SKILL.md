---
name: seam
description: Catch compositional bugs at a contract seam (IPC, C-ABI, serialization, public API) — where each side is locally correct and the defect lives in the gap between their readings. Contract-first phases, evidence-anchored findings, iterate until dry. Invoked as `/ouro:seam`; the seam catalog, confusables, bug history, and verification bar come from the repo binding.
---

# Adversarial Seam Review

Invoked as `/ouro:seam`. The methodology below is repo-agnostic; everything repo-specific
comes from the binding read in step 0.

A methodology for auditing components that share a contract seam, using an external
reviewer (another AI agent or a human) whose findings you then verify and act on. It
exists because the serious bugs at a seam are **compositional**: each side is locally
reasonable, and the defect lives in the difference between their readings of the
contract. A monolithic "review this codebase" pass reliably misses these — it
re-narrates instead of cross-examining. The origin run of this methodology produced
11/11 verified-true findings across two sweeps, including two latent protocol bugs and
one channel-lifecycle leak that years of conventional review had not caught.

## Step 0 — read the binding

Read `.claude/ouro.toml` at the repo root (schema and rules: `docs/binding.md` in the plugin).
No binding, or one that fails `bin/ouro-binding.py check`, means this skill refuses to run — say
so and stop. The seam catalog, the structural confusables, the bug history, the internals docs
that serve as phase-2 "claims to verify", and the test suites that form the verification bar for
the fix side are whatever `[authority].local` and `[overlays]` point at — this skill carries none
of them. If those documents do not name them for the seam under review, ask for them before
phase 0; do not infer a catalog from the code.

## When it applies

Two or more components communicate across a boundary you can write a contract for:
a wire protocol, a C ABI, a serialization format, a public API with multiple
independent consumers. You want an audit that neither side's author could produce —
including you, if you wrote or recently modified either side.

## Phase 0 — the contract must exist

The review is only as good as its ground truth. If the seam has no single
authoritative contract document, **write it first** (or consolidate scattered
descriptions into one). It must cover: message/type semantics field by field, ordering
and lifetime invariants (each naming the test that pins it, if one exists), error
taxonomy, limits and timeouts, and a version-compatibility matrix. Writing it is
itself a mini-review — you will find drift while consolidating. Do not skip to phase 1
with the contract spread across three files: the reviewers will each pick a different
file as truth.

For a **wide multi-seam scope** where consolidating every contract yourself would blow
your context, delegate the read-only *surface map* (who calls what across the boundary,
with file:line) to a subagent and write the contract from its report — you keep the
mini-review and skip the raw reading. Your consolidation is itself reviewed by phase 1:
reviewers routinely catch a transcription slip in the phase-0 contract against the real
code. Fix such slips in the transient contract mid-run — don't defend them.

## The phases

Each phase is a **fresh reviewer session with no shared context** — independence is
the point. Give each reviewer a self-contained brief; templates with the exact
structure are in `references/phase-templates.md` (read it when preparing briefs). Every
brief to an agent, the surface map's included, states the turn budget the plugin's
`docs/contract.md` sets; what an agent names as unfinished at it is unreviewed — brief a fresh
reviewer on it, or say so in the report.

**Phase 1 — contract soundness.** The spec and the shared primitives ONLY, no
implementations. Question: would two independent teams, given only this contract,
build interoperable peers? Products: per-section verdicts (SOUND / GAPS / AMBIGUOUS),
findings with quoted anchors, and a coverage-gap list (unspecified situations). The
gap list looks low-value at first — it becomes ammunition in phase 3. Phase 1 also
audits YOUR phase-0 work: when you consolidated the contract, you may have
transcribed wrong — treat consolidation errors it finds as findings and correct the
contract mid-run.

**Phase 2 — each side vs the contract, separately.** One review per component, in
parallel, each getting: the contract (ground truth), that side's internals doc
(framed explicitly as *claims to verify, not truth*), that side's code, and its
tests. Products: a per-invariant verdict table (CONFIRMED / VIOLATED / UNVERIFIABLE,
each with file:line evidence), an **unspecified-behavior list** (everything this side
puts on or expects from the seam that the contract doesn't specify), test-strength
judgments (does the pinning test actually pin it?), and traced robustness probes.

A seam with only ONE implemented side (a spec and its sole implementation — e.g. a
fresh ABI nobody consumes independently yet) collapses to phase 1 plus a single
merged phase 2 over the producer *and its consumer-facing artifacts* (header, samples,
tests); there is no composition to cross-examine, so skip phase 3 rather than staging
an artificial one. Record why in the report.

**Phase 3 — adversarial cross-reference.** One reviewer gets the contract, both
phase-2 outputs, and code access. Its jobs: cross-examine double-CONFIRMED verdicts
for divergent readings; build an **assumption ledger** (what one side relies on ×
what the other actually does, verified in code); enumerate interleavings per
cross-boundary exchange and check both sides tolerate all of them; **weaponize
phase 1's gap list** (for each unspecified situation, what does each side actually do
today?); list doc drift; and predict the next bug of the family. Products: ranked
findings with evidence on both sides, the ledger, recommended contract amendments,
recommended new contract tests.

## Rules that make it work

These are the load-bearing rules — each one earned its place by catching or
preventing a specific failure mode in practice:

- **No finding without an evidence anchor** (file:line or quoted contract clause).
  This single rule is why the origin run had a 100% verified-true rate. A reviewer
  that can't cite the line is guessing; make the brief refuse the finding.
- **Anchoring quarantine.** Withhold the known bug history from phases 1–2 — a
  reviewer told what was found before goes looking for the same shapes. Give the
  history to phase 3 deliberately, framed as "these are fixed; what is the next
  member of this family?"
- **Docs are claims, not truth.** Each side's internals doc goes into its phase-2
  brief labeled as claims to verify. Doc drift is a finding class, not noise.
- **Brief the reviewer on structural confusables up front** — deliberate type
  duplication, file-linked shared sources, generated code, vendored copies. An
  unwarned reviewer burns its findings budget "discovering" intentional design.
- **Demand structured output** (verdict tables, severity taxonomy: live-mismatch /
  latent-divergence / contract-gap / doc-drift, ranked findings). Prose reviews
  cannot be triaged or composed across phases.

## Responding to findings

The review's value is realized in the response, and the response has its own
discipline:

1. **Verify every finding against code before acting** — the reviewer is adversarial
   to the code, and you are adversarial to the reviewer. Confirm or refute each with
   your own file:line evidence; track the hit rate (it tells you whether to trust the
   rest of the report). At scale, *sample* each reviewer's highest-severity claims
   rather than every one, and say so — a clean sample bounds the tail, it doesn't prove
   it. A later phase narrowing an earlier phase's claim (e.g. a blast radius that
   phase 3 shows is smaller) is the composition working, not a refutation — log it as a
   refinement.
2. **Sort confirmed findings by what they actually are.** A real code bug gets a
   red-test-first fix — write the test, watch it fail on the exact defect, then fix.
   A contract *overpromise* gets a contract amendment, not code: if the code's
   behavior is the physically honest one (e.g. "guaranteed delivery" to a peer that
   stopped reading), fix the words. A contract *gap* gets an amendment plus a pinning
   test. Doc drift gets a doc fix.
3. **Check existing coverage before adding recommended tests** — reviewers recommend
   tests without knowing the suite; some are already covered, some need
   infrastructure that outweighs their value. Skipping one is fine if you log why.
4. **Log nontrivial decisions** (fix vs document, skipped tests, scope calls) — the
   next sweep and the next human need the reasoning, not just the diff.

## Iterate until dry

**A sweep is a loop, not a one-shot — but the loop alternates with fixing.** A first
sweep that returns many findings is DONE: hand the findings to the fix loop; more
sweeping on unfixed code just re-finds the same defects. Re-sweep AFTER a fix round
lands, scoped to the changed surface plus its neighbors. The origin run's second
sweep found a channel-membership leak sitting directly under the first sweep's fix —
the fix's new "replacement semantics" made the adjacent gap visible — and its third
found the next gap under THAT fix. Stop when a re-sweep returns nothing new or
nothing actionable. Findings from a re-sweep get the same verification discipline;
so does your own fresh code.

## Logistics

- Keep briefs and raw results in a **transient, git-ignored location** — they are
  scaffolding, not documentation. What survives: contract amendments, tests, fixes,
  and decisions (in commit messages). Delete the scaffolding when the loop closes.
- The external reviewer can be any capable agent given the brief verbatim. Paste each
  phase's raw output into the next phase's placeholder — no summarizing in between
  (summaries launder away the evidence anchors phase 3 needs).
- Phase 2 sides can run in parallel; phases are otherwise sequential.
- **Everything on disk, incrementally.** Write each brief, each reviewer output, and
  the report (per-seam sections as each closes) to the transient folder AS PRODUCED —
  never hold results only in context. A multi-seam run is long enough to hit session
  limits; a file-addressed run resumes losslessly, an in-context one doesn't.
- **Multi-seam scope needs an orchestrator budget.** Reviewing several seams at once:
  delegate the initial surface map to a read-only explore agent, run seams as
  independent tracks, and spend your own context on brief preparation and finding
  verification — not on reading whole components yourself.
