---
name: triage
description: >
  Grade one GitHub issue against the ouro contract with the ouro driver: it posts the `**Triage**`
  verdict comment, writes the Size, Flow and Doc impact lines and sets the labels. Invoked as
  `/ouro:triage <N> [--dry-run]`. Use when the user asks to triage an issue, promote it to
  agent-ready, grade its quality, re-triage after a ruling landed, or when the anchor or shape
  gate demoted it. `--dry-run` prints the comment and the label moves and writes nothing.
---

# Triage an issue — `/ouro:triage`

`<ouro>` is the plugin root: `${CLAUDE_PLUGIN_ROOT}` in Claude, or the root named by the installed Codex wrapper.

1. **Bind.** Read `.claude/ouro.toml` at the repo root. None, or one that fails
   `python3 <ouro>/bin/ouro-binding.py check`: refuse and say so.
2. **Run** from the repo's main checkout:

   ```bash
   python3 <ouro>/bin/ouro triage <N> [--dry-run]
   ```

3. **Relay** the verdict, the comment it posted (or would post) and the labels it moved. An `L`
   verdict is a split: the comment says how, and the issue does not become `agent-ready`. A
   question for the owner is `needs-ruling` with the recommendation attached; the owner answers
   it, and this session never guesses a ruling. Exit `2` is a refusal, `1` a driver error: quote
   its message.

The grading rules are the contract's (`docs/contract.md` §3 and §4); the driver quotes them into
the node, so there is one copy.
