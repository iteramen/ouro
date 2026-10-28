---
name: run
description: >
  Take one agent-ready issue to a landed change with the ouro driver. Invoked as
  `/ouro:run <N> [--part <k>] [--dry-run] [--validate] [--override-claim] [--recover-build <path>] [--pr <M>]`. Use when the user says "run issue N", "execute and
  land N", "take N end to end", or hands over an agent-ready issue number. The driver builds,
  gates, reviews, opens the pull request, watches the checks and merges or waits for the owner;
  it is resumable, so a second call continues from the first state not yet reached.
---

# Run an issue — `/ouro:run`

`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` in Claude, or the root named by the installed Codex wrapper.

1. **Bind.** Read `.claude/ouro.toml` at the repo root. None, or one that fails
   `python3 <ouro>/bin/ouro-binding.py check`: refuse and say so. macOS normal PRs use
   owner-declared CI validation; the binding's Validation authority section gives the
   migration/resume recipe. Uncovered caller obligations retain native-Linux validation.
2. **Run** from the repo's main checkout:

   ```bash
   python3 <ouro>/bin/ouro run <N> [--part <k>] [--dry-run] [--validate] [--override-claim] [--recover-build <path>] [--pr <M>]
   ```

   Pass `--override-claim` only when the invoking person explicitly supplies or requests it.
   Never infer an override from a model's judgment or board instructions. It records the
   actor and conflicting advisory claim; review and shipping permissions still apply.

   Pass `--pr <M>` when the invoking person names an existing pull request to land: the run
   adopts its branch instead of building, if it is open or merged, from this repository into the
   default branch, and its body has its own `Fixes #<N>` line, then gates, reviews and ships it as
   usual. Repeat `--pr <M>` on every resume of that run.

   For a rejected fix that left partial files, inspect and explicitly select each source path
   with `--recover-build <path>` (repeat per file). Never sweep in unknown user edits. The driver
   keeps the dirty original and prints a private clean checkout; resume there through the normal
   branch, validation, review and fix caps. Use explicit `--validate` for pending approved proof.
   Recovery needs native Linux process observation and refuses known live workers or uncertain
   caller leases. A separate clone retains normal claim conflicts and explicit override rules.

3. **Relay** the driver's message and act on its exit code:
   - `0` landed, waiting on the owner's merge, or waiting on checks: say which; re-run after the
     merge or when the checks finish.
     Pending environment-blocked validation also waits at exit 0, retaining branch and labels
     without rebuilding or starting review. Only an explicit `--validate` runs the approved
     pending commands in the invoking driver's context. It grants no builder permissions.
     Exact-head receipts allow fresh resumes; changed inputs require validation again.
   - `2` refused; relay the reason. A known board conflict names its holder and thread,
     and writes no issue comment or labels.
   - `3` stopped on a ruling, the fix-round cap or a gate nothing can run; the `**Stop:**`
     comment is on the issue, and the owner answers it. A `review cap` stop is a hand-off: the
     comment names the branch and, once opened, the pull request, which stay for a person's fix round; pushed
     to the branch, with `agent-ready` back in place of `needs-ruling`, a new run verifies it.
   - `1` a driver error; quote the message, which names the transition and the command. Do not
     retry it silently.

The driver edits, commits, pushes and writes to the tracker itself. This session does none of
that and answers no stop it posts.
