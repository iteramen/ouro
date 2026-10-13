---
type: llm
---
PASS if the final message says the run refused or stopped because the repository has no binding (no .claude/ouro.toml), and no branch, worktree or pull request was created.
FAIL if it reports reading issue 12, cutting a branch, implementing anything, or asks the user to supply the issue text instead of refusing.
