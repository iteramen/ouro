# Gate selection by paths

How `/ouro:execute` §3 picks which `[[gate]]` entries run on committed work, and what the
evidence says about each. The gates Action reads the diff the same way, so a local run and CI
select the same set.

- [The selected set](#the-selected-set)
- [Narrowing by paths](#narrowing-by-paths)
- [The two fallbacks](#the-two-fallbacks)
- [Quoting the entries](#quoting-the-entries)
- [The evidence](#the-evidence)
- [A gate that repeats a required check](#a-gate-that-repeats-a-required-check)

## The selected set

Every `[[gate]]` whose `areas` contains any of the issue's area labels, plus every `*` gate,
narrowed by `paths` as below, each `<ouro>` in a `run` replaced by the plugin root
(`/ouro:execute` §0). Which of that set the session runs itself on **committed** work, and which
the checks the pull request runs, follows the issue's Flow class (`/ouro:execute` §3); under full
a gate's own code, which the diff must change for the session to run it, is the file its `run`
names and what that file reads: the suite, its fixtures, the script under test. A gate that
cannot run locally is a finding, not a skipped step — say so. The run follows the Gate
environment, What CI checks, Two reads and Coverer sections of `dispatch-rules.md`.

The narrowing applies to every gate run on committed work whose evidence the PR body carries:
this step, the S run on the commit, the M session's run on the commit (`/ouro:execute` Size)
and `/ouro:land` step 3. The M builder's brief names the gates the issue's Flow class gives it
(`/ouro:execute` Size): under express and standard the change's own acceptance run, even when
that command is a gate the checks also run, and the selected gates the pull request's checks do
not run at all or run only to print a skip; under full also each selected gate whose own code
the change changes, which the builder reads from its own uncommitted changes, new files
included, against the commit the build starts from, since it works before any commit exists; no
other selected gate the checks run goes in the brief, and a list that comes out empty is the
builder's run.

## Narrowing by paths

**`paths` narrow the area-selected set; they never add to it.** A selected gate with no `paths`
runs as before. One that declares `paths` runs only when the branch's diff touches one of its
entries, and a gate the areas did not select stays out even when the diff touches its list. The
diff is the branch's committed changes against its merge base with the default branch, as
`/ouro:land` and CI read it; the three-dot form below diffs against that merge base, and git's
own pathspec matching decides what it touches. From the repository root, with `$DEFAULT` read
from `[repo].default_branch`:

```bash
DEFAULT=<[repo].default_branch>
# the fallback: the gates Action
git diff --quiet origin/"$DEFAULT"...HEAD -- 'actions/gates/'; echo "$?"
# one selected gate that declares paths: each of its entries, as written
git diff --quiet origin/"$DEFAULT"...HEAD -- '<entry>' '<entry>'; echo "$?"
```

Exit 1 means touched, 0 means not touched, and any other exit means the diff cannot be read.
Each selected gate that declares `paths` gets the second command with its entries: exit 1 runs
it, 0 skips it.

A gate whose CI leg is path-filtered declares as its `paths` pathspecs that select the same paths
as that filter, and every file the gate reads. Without them, this narrowing never skips the gate,
and the session runs it locally wherever the diff misses the filter, where the leg covers nothing
(`dispatch-rules.md`, Coverer). No check compares the two.

## The two fallbacks

The area-selected set runs in full, with no `paths` narrowing — "in full" means the
area-selected set, not every gate the binding declares — in the gates Action's own two fallback
cases:

- the diff cannot be read: git exits with anything but 0 or 1, for either command, for example
  because `origin/$DEFAULT` is missing;
- the first command exits 1: the diff touches `actions/gates/` in the checked-out repository, the
  one pathspec the Action checks (in the plugin, the Action's own directory). A workflow edit
  keeps the narrowing: a consumer that reaches the Action through its workflow's `uses:` line
  gets the narrowed set under a new pin, not the full set. A gate that reads the binding names
  `.claude/ouro.toml` in its `paths`, so a binding change runs those gates and no others.

## Quoting the entries

Each entry reaches git as written, magic such as `:(glob)` included. In bash, single-quote each
entry, or expand a quoted array (`"${arr[@]}"`); an entry holding a `'` is written `'\''`. An
unquoted entry is globbed against the working tree, so a wildcard entry names only the files
that still exist, and a branch that deletes a file it matches reads as untouched. On Linux and
macOS, pwsh globs a native argument even from a variable, so a pwsh session there either runs
the check from bash or starts git through `ProcessStartInfo.ArgumentList`, as the gates Action
does; it never puts the entries on a pwsh native command line there. In pwsh, read the exit of a
git typed on the command line from `$LASTEXITCODE`, not `$?`, and of one started through
`ProcessStartInfo` from the process's `ExitCode`; write a `'` inside an entry as `''`.

## The evidence

One line per gate, its command and exit code, goes in the PR body, and the output is saved and
posted as a comment (`/ouro:land` step 5). The gate evidence lists each skipped gate with its
command and the words `skipped: the diff touches none of its paths`. When a fallback runs the
set in full although a selected gate declares `paths`, the evidence says in one line which
fallback applied, as the Action's `every gate runs: <why>` line does. As in CI, a list that
misses a file its gate reads skips that gate wrongly (the plugin's `docs/binding.md`), and the
push to the default branch still runs every gate in CI.

## A gate that repeats a required check

In a repository that never batch-lands, a gate earns its place by being the narrowest command
that can actually fail for the diff: one that repeats a PR's required check
(`gh pr checks <PR> --required`, once the PR is open) costs a full run to catch what CI catches
anyway. Under express and standard the session runs no such gate: the checks the pull request
runs are its run (`/ouro:execute` §3). `/ouro:land-batch` runs every `[[gate]]` as its only check
on the composed tree, so a repository that batches keeps its full suite in a gate and pays for
that run on each PR. Elsewhere, under full, run the gate as bound all the same, and name it in
the report as a binding smell, with the required check it repeats — the binding narrows it, not
the run.
