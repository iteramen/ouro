# Fixture doc for Test-RepoVariablesDoc.Tests.ps1

Only the `### Repo variables` table below is read; the trailing section proves
rows outside it are ignored.

### Repo variables

| Variable | Value | Purpose |
|---|---|---|
| `REAL_VAR` | `x` | matches the happy-path listing |
| `DOC_ONLY_VAR` | `42` | phantom bait for the empty-listing case |

### Not the variables table

| `NOT_A_VAR` | `no` |
