---
name: run
description: >
  Take one agent-ready issue to a landed change with the ouro driver. Invoked as
  `/ouro:run <N> [--part <k>] [--dry-run]`. Use when the user says "run issue N", "execute and
  land N", "take N end to end", or hands over an agent-ready issue number. The driver builds,
  gates, reviews, opens the pull request, watches the checks and merges or waits for the owner;
  it is resumable, so a second call continues from the first state not yet reached.
---

# Run an issue — `/ouro:run`

`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` if set.

1. **Bind.** Read `.claude/ouro.toml` at the repo root. None, or one that fails
   `python3 <ouro>/bin/ouro-binding.py check`: refuse and say so.
2. **Run** from the repo's main checkout:

   ```bash
   python3 <ouro>/bin/ouro run <N> [--part <k>] [--dry-run]
   ```

3. **Relay** the driver's message and act on its exit code:
   - `0` landed, waiting on the owner's merge, or waiting on checks: say which; re-run after the
     merge or when the checks finish.
   - `2` refused before a branch was made; the stop comment is on the issue.
   - `3` stopped on a ruling, the fix-round cap or a gate nothing can run; the `**Stop:**`
     comment is on the issue, and the owner answers it.
   - `1` a driver error; quote the message, which names the transition and the command. Do not
     retry it silently.

The driver edits, commits, pushes and writes to the tracker itself. This session does none of
that and answers no stop it posts.
