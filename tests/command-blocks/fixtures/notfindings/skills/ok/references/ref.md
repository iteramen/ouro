# ref (fixture)

A reference file beside the fixture skill: its fence is scanned like the SKILL.md's.

```bash
DEFAULT=<[repo].default_branch>
git diff --quiet origin/"$DEFAULT"...HEAD -- '<entry>' '<entry>'; echo "$?"
```
