---
name: triage
description: >
  Grade one GitHub issue against the ouro contract with the ouro driver: it posts the `**Triage**`
  verdict comment, writes the Size, Flow and Doc impact lines and sets the labels; on a
  cleanup-ledger SPLIT it also files the children, posts the sweep mark naming them and triages
  each child. Invoked as
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
   verdict is a split, and the issue does not become `agent-ready`: the owner files the children
   by hand, except on a cleanup ledger, where the command files them with `needs-triage`, posts
   the sweep mark naming them and triages each one (a dry run only prints them). Relay the filed
   child numbers, the sweep mark and each child's verdict. A split that stops partway is exit
   `1`, its message naming the children already filed: relay those. A
   question for the owner is `needs-ruling` with the recommendation attached; the owner answers
   it, and this session never guesses a ruling. Exit `3` is a stop, on the issue or a filed child:
   a preflight gate withheld the promotion and the posted NEEDS-RULING names its finding, or the
   body changed while the node graded and only a `**Stop:**` comment was written, so run triage
   again. Exit `2` is a refusal, `1` a driver error: quote its message.

The grading rules are the contract's (`docs/contract.md` §3 and §4); the driver quotes them into
the node, so there is one copy.
