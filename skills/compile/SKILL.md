---
name: compile
description: >
  Read-only queue planner: runs the footprint analyzer over the open `agent-ready` and
  `needs-ruling` issues and prints, in chat, which can run at once (waves), which `/ouro:fuse`
  sets to build together (batches), which rulings hold blocked issues and what ready work their
  build would collide with, what an umbrella or area still needs, and what to clean up. Invoked as
  `/ouro:compile [<scope>] [--intent <name>] [--target <issue|area>] [--width <n>] [--explain <N>] [--graph <file>] [--help]`.
  Use when the owner says "what can run at once", "which ruling unblocks the most", "what does
  umbrella N need", "plan the queue". Writes nothing and runs no work: no label, comment, body,
  file, branch or rolling issue, and it never dispatches, executes, merges or promotes. The owner
  names what runs, through `/ouro:e2e` or `/ouro:fuse`.
---

# Plan the queue, read-only — `/ouro:compile`

`/ouro:e2e` runs one issue; `/ouro:fuse` builds several that share files. Ordering a queue and
choosing what to run together belong one level up, and this skill is that level, as a proposal
only. It never chooses work for itself (contract §9): the owner names what runs. Like
`/ouro:drift` run interactively, it reports in chat and applies nothing.

The program is printed by `<ouro>/bin/Get-CompileProgram.ps1`, a deterministic renderer of the
analyzer's JSON, which is too large to read whole on a real queue. This skill binds, runs the two
scripts and presents what the renderer prints. It re-packs, counts and orders nothing itself.

`<ouro>` below is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set, else the marketplace checkout.
`<slug>` is `[repo].slug`.

## Arguments

| Argument | Passed to the renderer as |
|---|---|
| `<scope>`, an area label | `-Scope <scope>` |
| `--intent <name>`: `throughput` (default), `unblock`, `finish`, `cleanup`, `batch` | `-Intent <name>` |
| `--target <issue\|area>`, required by `--intent finish` | `-Target <value>` |
| `--width <n>`, from 1 to 4, default 2 | `-Width <n>` |
| `--explain <N>`, in place of the intent | `-Explain <N>` |
| `--graph <file>`, a saved analyzer output for a replay or a review | `-Graph <file>`, and the analyzer is not run |

`--help` prints the invocation line and the intent names, and stops.

## 0. Bind

Refuse without a binding, or when `check` fails, as `/ouro:drift` §0 refuses. Read `[repo].slug`.
The area list is read in step 1.

```bash
python3 <ouro>/bin/ouro-binding.py check || { echo "STOP: no usable binding"; exit 1; }
python3 <ouro>/bin/ouro-binding.py get repo.slug
```

## 1. Run

From the repository root. Name the mirrored files: a mirrored file is one that changes only
because a policy or a gate requires a claim to be mirrored into it, by contract §4's S-shape test,
as `/ouro:fuse` §2 names them. It stays in a footprint but links no two issues. Print the list
passed, and omit `-Mirrored` when no file qualifies.

The area list is `[labels].area`; an unset key is an empty list, and the renderer then refuses a
scope with a line and goes on unscoped.

```bash
set -o pipefail
AREAS=$(python3 <ouro>/bin/ouro-binding.py get labels.area) || AREAS=''
pwsh -NoProfile -File <ouro>/bin/Get-FootprintGraph.ps1 -Mirrored <comma-separated mirrored files> \
  | pwsh -NoProfile -File <ouro>/bin/Get-CompileProgram.ps1 -Repo <slug> -Areas "$AREAS" <renderer arguments> \
  || { echo "STOP: the analyzer or the renderer failed; its stderr is above"; exit 1; }
```

With `--graph`, the renderer reads the file and nothing else runs:

```bash
AREAS=$(python3 <ouro>/bin/ouro-binding.py get labels.area) || AREAS=''
pwsh -NoProfile -File <ouro>/bin/Get-CompileProgram.ps1 -Repo <slug> -Areas "$AREAS" -Graph <file> <renderer arguments> \
  || { echo "STOP: the renderer failed; its stderr is above"; exit 1; }
```

The renderer reads the open pull requests (an issue a `Fixes #N` line names is in flight), and,
when the intent needs them, the blocked issues and the issue list, each by a `gh` call that names
the repository. Under `--graph` those reads stay live.

A nonzero exit stops the run and reports the stderr; it is never an empty proposal and never a
program improvised from the JSON. Exit 1 is a failed read, input that is not the analyzer's
output, or a `schema` other than 2. Exit 2 is a bad argument or a scope that is not a declared area.
Exit 3 is a failed self-check, which means the renderer's lists disagreed with each other. With no
stdout, there is nothing to present.

## 2. Present

Print the renderer's stdout as it came, then add the judged layer, which the renderer leaves to
the session:

- **`unblock`:** for each of the three rows the renderer names, one sentence on what answering the
  ruling would start, from the issue's own question (`gh issue view <N> -R <slug>`).
- **`cleanup`:** for each duplicate pair, read both bodies and say "one deliverable" or "siblings",
  with the sentence that decides it.

The output opens with the graph's head, the mirrored list, the counts and the precision line.
That line says where the footprints come from, the files the issues' text names (the anchors and
the `Doc impact on close` line) and not a build, that its figures are replays over closed issues,
measured on the anchor paths before root files joined the footprint and quoted from the
analyzer's help, and that this is a proposal, never a guarantee, to re-check
against the diff after each build, as `/ouro:fuse` §2 does. With no `agent-ready` issue read, the
next line says the menu is empty and that, as contract §10 says, the next action is triage. A
batch is a proposed fuse set, not a region, and it is not conflict-free: batches land one after
another, and `/ouro:fuse` §2 asks the owner before a region of more than five issues. Nothing here
runs: the owner names what runs.
