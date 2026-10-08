---
name: ok
description: fixture skill for Test-CommandBlocks.Tests.ps1 -- plants acceptance 3's
  not-findings, anchored to skills/land/SKILL.md's own text
---

# ok (fixture)

A placeholder over sixty characters that holds an apostrophe:

```bash
MERGE_ANSWER=<step 0's merge answer: squash or merge, squash where none was declared>
echo "$MERGE_ANSWER"
```

Two parameter expansions sharing a line, each with its own `<` or `>`:

```bash
AUTHOR="Jane Doe <jane@example.com>"
EMAIL=${AUTHOR##*<}; EMAIL=$(printf '%s' "${EMAIL%>}" | tr '[:upper:]' '[:lower:]')
echo "$EMAIL"
```

A quoted message holding a `gh` subcommand, and a block with no `gh` at all:

```bash
test -z "$DRAFT" || echo "mark it ready (gh pr ready) once /ouro:execute completes"
echo "no gh here at all"
```

A `git remote` write subcommand is not a read of one:

```bash
git remote add origin https://example.com/x.git
git remote rm origin
git remote --help
echo "$gh_repo"
```

A fenced `toml` block holding `GH_REPO` -- not a bash or sh block, so it is never extracted:

```toml
GH_REPO = "owner/repo"
```
