---
name: bad
description: fixture skill for Test-CommandBlocks.Tests.ps1 -- plants one block that fails
  bash -n and one line per grep rule
---

# bad (fixture)

A block whose shell never closes its `if`, so `bash -n` reports it:

```bash
if [ -z "$X" ]; then
  echo "missing"
```

A block with no parse error, one line per grep rule:

```bash
git remote
gh repo set-default owner/repo
echo "$GH_REPO"
git remote -v
git remote get-url origin
git remote show origin
git remote --verbose
echo "git remote"
x=`git remote`
git remote 2>&1
```

An `sh` fence is extracted as well:

```sh
echo "$GH_REPO"
```
