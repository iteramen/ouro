# Documentation governance

What a repo's documentation must be true of, and what each deterministic docs signal means when
it fires. The loop contract (`docs/contract.md` §2) settles the doc/issue boundary; this document
covers the rest.

**Adopting it is a copy, not a reference.** `[authority].local` names paths inside the consumer's
own tree, so a repo that wants this as binding authority copies it in and cites its own copy —
the same way it copies `templates/documentation-rule.md` into `.claude/rules/` so the conventions
are in front of an agent while it edits markdown.

Where this document says the gate is *configured*, that configuration lives in the repo's
binding, not here. Signal names and meanings are stable; key names are not restated in this file,
so it cannot promise a schema the validator has not learned.

---

## 1. Docs are current state; git is history

Every doc describes the system as it is now. The one exempt form is a dated frozen snapshot whose
banner says it is not authority and not kept current; it is replaced or deleted, never edited.
Banned everywhere, **including planning docs**:

- "previously X, now Y" reconciliations, and dated migration narratives
- slice-by-slice progress logs
- verification ceremony — test counts, "builds clean", merge hashes, PR numbers

When work lands, **edit the current-state description in place. Never append a progress entry.**
A doc answers *what is true now, what's next, why it's this way*. A commit or PR answers *what
changed, how we got here, did it pass*.

The corollary is that deleting a rotted doc is a normal act, not a loss. Git has it.

## 2. The doc/issue boundary

`docs/contract.md` §2. Work-remaining content belongs in issues; four kinds of content stay in
docs because they describe current state. Read it there — restating it here is how the two
versions start to disagree.

## 3. Naming and location

`README.md` is for directory overviews — "what is in this folder". Everything else is
`kebab-lower-case.md`, named by topic. Uppercase filenames are reserved for the conventional set
(`README.md`, `CHANGELOG*.md`, `CLAUDE.md`, tool-mandated manifests); never invent one.

Feature docs live **beside the feature**. Cross-cutting docs — process, CI, architecture spanning
modules — live under a docs tree.

Where a repo keeps an index of its subsystem docs, the binding names the index and the trees it
covers. Declaring both arms S4a and S4b; either alone arms neither. A repo with no index
convention declares neither and both stay silent. **The plugin does not prescribe the filename**,
because the index is the repo's artifact — the binding is where a repo-specific path belongs.

## 4. Referencing something that does not exist yet

A reference to a file, path, or doc that is **planned but not written** carries a literal
`(planned)` marker on the same line:

    Ties into [workspace docking](docs/workspace-docking.md) (planned).

The marker suppresses only when its line has exactly **one** reference — a link target, a link's
anchor, or a backtick path. A line naming two, even of different kinds, still reports both, so the
marker can never silently hide a second real break riding along beside the intentional one.

**A backticked path inside the link text is itself a reference.** Writing
`` [`docs/x.md`](docs/x.md) (planned) `` puts two on the line and the marker stops working —
which is why the example above leaves the link text as prose.

**A marker is a claim that expires.** When the target is written, remove the marker in the same
change. A stale marker pre-suppresses: if that path is later reused, or the file moves, the break
goes undetected instead of failing the gate.

## 5. Fixing a broken reference

**This is the section that decides whether the gate helps or hurts.**

"This file does not exist" is equally consistent with three situations that demand opposite fixes:

- it is **planned** → mark it (§4)
- it was **deleted** → reword or remove the claim
- it **already landed somewhere else** → fix the path

Only history distinguishes them. `git log --diff-filter=A -- <path>` says when something arrived;
`--diff-filter=D` says when it was removed. Checking only whether the path resolves *today* will
confidently produce the wrong fix.

And when you retarget a path, **re-read the sentence around it.** A corrected path inside a
now-false claim satisfies the checker and misinforms the reader — strictly worse than the broken
link, because nothing will flag it again.

## 6. Compress, and do not duplicate

No duplication across docs — link instead. Cut ceremony, framing paragraphs, restated context. A
50-line README that gets read beats a 500-line README that gets skipped. Documentation is part of
the work, not a follow-up: it lands in the same change as the code.

No standalone documents for bug fixes, review findings, or resolved discussions — a fix lives in
the code and the commit message. Delete superseded docs rather than archiving them. The sanctioned
exception is a dated incident-investigation tree, whose point-in-time nature *is* the content;
a repo naming one under `[overlays].drift` also excludes it from the LLM drift audit.

## 7. No future-plan cross-references in descriptive docs

Architecture docs, subsystem references, and operational guides describe what exists. No "see
issue #NN", no "tracked in the roadmap", no "feature X coming" — those rot the moment work lands.

The exception is a **documented non-adoption**: naming a design deliberately *not* taken, and the
trigger that would reopen it, is current state. Its execution plan is not — that is an issue.

---

## 8. The signals, and how to clear each

The deterministic gate reports these. Which of them block is per-repo — a report-only list names
the ones that never fail a build — so a repo can adopt the gate before it is clean and narrow the
list as it ratchets. The shipped default is the cautious one: the signals with the widest
false-positive surface report rather than block until a repo says otherwise.

| | What fired | How to clear it |
|---|---|---|
| **S1** | A markdown link, or a relative `href` in an in-scope HTML page, whose target does not resolve. A markdown link starting with a single `/` resolves from the repository root; an `href` starting with a single `/` is a site root and is skipped; a target starting with `//` is skipped in both. | §5 — establish *why* first. On a markdown link: fix the path, reword the claim, or mark it `(planned)`. On an HTML page's `href`, `(planned)` clears nothing — fix the target, or add a `suppress_prefixes` entry. |
| **S2** | A link's `#anchor` names no heading in the target document. | Headings changed. Re-read the target's current headings rather than guessing the old slug. One case has no correct answer: where a document repeats a heading, the renderer disambiguates with a `-1` suffix the slugger does not model, so a *correct* link to the second one reports. Exempt the file rather than "fixing" the anchor to point at the first. |
| **S3** | A path in a code span — a backtick span in markdown, a `<code>` span in an in-scope HTML page — that does not resolve. | §5. A token the gate cannot verify is skipped rather than reported: a scheme- or drive-qualified path, a home-relative one, a parent-relative one that resolves outside the repo root (one that resolves inside it, against the doc's own directory, is checked), a UNC share, and a path that is gitignored by design (it can never appear in a tracked-files index, so it would be unclearable). Everything else carrying a slash and a known extension is checked. |
| **S4a** | The doc index lists an entry resolving to no file. An entry may name a markdown doc or an HTML page, and a page resolves only when `html_globs` brings it into scope. | The doc moved or was deleted; update or drop the index entry. Armed by declaring the index path and the indexed trees together. |
| **S4b** | A doc, or an in-scope HTML page, in an indexed tree is absent from the index. | Add its entry, or exempt its path class when the whole class is out of scope. Armed by the same two declarations as S4a. |
| **S7** | A code fence was opened and never closed. | Close it. Everything after an unclosed fence is invisible to every signal except S7 and S8, so this one hides other findings. |
| **S8** | A reference to something known dead, named in the banned table with its reason. | The reason says what replaced it. Zero false positives by construction, which is why it blocks — but note it is a raw-line scan with **no suppression path**: it sees fences and comments too, so the reason for a ban belongs in the binding, never quoted in prose. |
| **S9** | An external URL is definitively dead — 404/410, or DNS failure. | Report-only by default: external sites break for reasons that are not yours. Inconclusive — never findings — are 403, 405, 429, 5xx, timeouts, **and a 404 from `github.com` itself**, since anonymous requests to a private repo 404 by design. |
| **S10** | Work-remaining content in a doc: a section heading naming undone work, or a checkbox list. | §2 — distill it into an issue with verified claims, then delete the section in the same pass. A *procedural* checklist run per release or per test pass is not undone work: declare its path as a planning path. |

### Suppressions

A finding that is wrong — not a doc that is wrong — goes in the gate's ignore file, one entry per
line with a reason. Whole-document exemptions are spelled `file: <path>`; a bare token is a
repo-wide suffix suppression. **An entry without a reason is not a suppression, it is a
concealment.**

**It does not cover every signal.** The ignore file reaches S1 on markdown, S2 and S3 — the
signals that resolve a reference — and a `file:` entry may name an in-scope HTML page, which S3
reports on. S1 on HTML, S4, S7, S8, S9 and S10 take no ignore list: clear those by fixing the
doc, by configuration (an index exemption, a planning path), or not at all. Expecting a `file:`
line to silence an S10 is the common surprise. A `suppress_prefixes` entry is not the ignore list:
it reaches S1 on HTML too, tested against the href's root-relative form as on markdown.

The split is deliberate: the binding says what the *policy* is and is typed and validated; the
ignore file says which individual *findings* are excused and is expected to churn. Neither
belongs in the other.
