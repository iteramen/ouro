---
name: weekly
description: >
  Print the ouro loop's weekly outcome table, or the queue's next wave, with the ouro driver.
  Invoked as `/ouro:weekly [--post]` for the table and `/ouro:weekly compile` for the wave. Use
  when the user asks how the loop did this week, for the outcome or quality table, or for what
  can run next. `--post` also posts the table on the `[rolling_issues].loop_runs` issue.
---

# Weekly table and next wave — `/ouro:weekly`

`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` in Claude, or the root named by the installed Codex wrapper.

1. **Bind.** Read `.claude/ouro.toml` at the repo root. None, or one that fails
   `python3 <ouro>/bin/ouro-binding.py check`: refuse and say so.
2. **Run** from the repo's main checkout, one of:

   ```bash
   python3 <ouro>/bin/ouro weekly [--post]
   python3 <ouro>/bin/ouro compile
   ```

3. **Relay** what it prints, unedited. `weekly` reads the tracker's own record; `compile` is
   read-only and prints which issues can run at once, which rulings hold others, and what to
   clean up. Neither runs work: the owner names what runs, through `/ouro:run`. Exit `2` is a
   refusal, `1` a driver error: quote its message.
