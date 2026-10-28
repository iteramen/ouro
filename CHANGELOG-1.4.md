# Release history: v1.4

**Frozen archive — 2026-10-28.** These published release notes and their
**Consumer contract:** migration notes are historical records, not current protocol
authority. The released sections below are preserved verbatim. Current release notes
are in [CHANGELOG.md](CHANGELOG.md); current usage and contracts are in
[README.md](README.md), [docs/contract.md](docs/contract.md) and
[docs/binding.md](docs/binding.md).

## [v1.4.1] - 2026-10-20

- `docs/architecture.html` names this repository's own rules file instead of linking to it: the
  public tree does not hold that file, so the link was dead there.

## [v1.4.0] - 2026-10-20

- `bin/forbidden-tokens.py hook` reads a `# protects: <owner/repo> ...` line in the token list:
  with one, a hit blocks only a call that lands in a named repository (the directory's
  `repo.slug`, else its `origin`; one it cannot resolve counts as named) or names one with a `-R`,
  `--repo` or `GH_REPO` value or an issue or pull request URL, and `ship.forbidden_check` stays the
  override, `"required"` scanning and `"off"` passing whatever the line names; with the line,
  `"off"` passes a hit only when no such name is another repository's. **Consumer contract:** old form, with a list set an absent
  `ship.forbidden_check` scans every publishing call in every repository; new form, it follows the
  list's `# protects:` line, and a list without one scans every call as before. Migration: add
  the line to the list naming the repositories it guards, then drop the `forbidden_check = "off"`
  a binding carried only to opt out.
- `bin/forbidden-tokens.py` judges a commit at the directory a `cd` behind an escaped inner quote,
  a `cmd /c`, `eval`, `iex` or `Invoke-Expression` string, an unquoted `-c` word, or an `env -C`
  before a `bash -c` moves to, and refuses a `cd` whose directory reads empty (spliced quotes) or
  is cut away (a command substitution, a parenthesised expression) instead of skipping it.
- A follow-up write that fails after an earlier one succeeded leaves the unfiled items in the
  `**Follow-ups filed**` comment, and a resumed run files exactly those, once each, then posts
  the comment with everything filed. Only a comment from the bot or an approver counts, both for
  the items to file and for ending the step; another account's comment is data.
- The `Review:` line no longer counts a verification's OUTSIDE finding, as the contract's
  Review-line rule says; the FOLLOWUP brief still carries it.
- `bin/forbidden-tokens.py` reads `chdir`, pwsh's alias of `Set-Location`, as a cd verb wherever
  `cd` is one; its selftest pins `chdir`, `env --chdir=` and a `--chdir` a `-C` chains from.
- `bin/external-audit.py` creates its artifact tree (the per-user parent, the session directory
  and the round folder) owner-only for every adapter, on POSIX re-setting each level to 0700 and
  refusing one another account owns, before anything is written; `prompt.txt`, which holds the
  diff, is written 0600.
- The FOLLOWUP brief lists an OUTSIDE finding once when a later round reports it again with other
  evidence or another severity, as the latest round reported it: the identity is the file, the
  line and the claim, the one the `Review:` line counts by, less the severity.
