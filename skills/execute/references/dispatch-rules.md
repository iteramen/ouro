# Dispatch rules

Each section below is pasted whole into the hosts the table names, never cited. A change is made
here and pasted into every host in the same commit. Contract §7 is the authority for Files, Waits
and Named head, which quote it.

| Section | Pasted in |
|---|---|
| Writes off this machine | execute M brief; reviewer prompt main block |
| Scratch | execute M brief; reviewer prompt; build-tiers template; verifier |
| Stash | execute M brief |
| Shell | execute M brief; reviewer prompt; e2e 1; build-tiers template |
| Sweep | execute 3 Doc impact; execute M brief |
| Files | execute Law of the road; reviewer prompt; build-tiers template; verifier |
| Waits | execute M brief; reviewer prompt |
| Named head | execute M brief; reviewer prompt |
| State note | execute M brief; reviewer prompt |
| Gate environment | execute 3 Gates and M brief; land 3; land-batch 4; fuse 4 |
| What CI checks | execute 3 Gates; land 3; land-batch 4; fuse 4 |
| Two reads | execute 3 Gates; land 3; land-batch 4; fuse 4 |
| Coverer | execute 3 Gates; land 3; fuse 4 |

## Writes off this machine

Beyond the gates it names, drive nothing that writes off this machine (a remote, a registry, an API,
the tracker) without a dry run or a refusing stub first on PATH, probes included. Before any call
relies on a stub, the stub proves itself: one call whose stub-only answer must appear in the
capture.

## Scratch

Delete only scratch you created, by exact name, never by a pattern: another run's files sit under
the same root.

## Stash

Never a bare `git stash`: the stash stack is shared by every worktree of the repository, so a pop
can take another session's entry. Stage the work.

## Shell

A command that removes a path removes a literal path, the object returned by a create that never
returns an existing path (`New-Item` without `-Force`, which fails on one; `mktemp -d`, which makes
a new one), or a path it has just proved lies under the session's scratch directory: the target's
resolved path starts with that directory, its own trailing separator trimmed, then a separator,
compared case-insensitively on Windows. A recursive delete never silences its errors. Stop only a
process you started, by the PID you recorded when you started it: never `pkill`, `killall`,
`Stop-Process -Name`, `taskkill /IM`, or a `kill` fed by `pgrep`. A pattern matches every process on
the host that holds it, another session's jobs and your own shell included, and a count of processes
by pattern counts the command doing the counting. In PowerShell, no variable is named after one
PowerShell reserves: a name `Get-Variable` shows ReadOnly or Constant in a fresh `pwsh -NoProfile`
(`$home`, `$pshome`, `$isWindows`, `$host`, `$error`, `$pid` among them), whose assignment fails and
leaves the old value, or one the runtime rebinds (`$input`, `$args`, `$matches`, `$_`). A shell
variable that was not assigned is a path somewhere else. Text a command carries as data (an issue or
PR body, a commit message, a fixture, a mutant, a JSON line) that holds a backslash escape, a `$`,
a backtick or a template tag is written to a file with the agent's file-write or edit tool, never
through a here-doc, an `echo` or a quoted shell string, and reaches a command through a flag that
reads a file, such as `--body-file` or `git commit -F`. The file is then read back and compared with
the intended text before it is used: a tool call can decode an escape on the way, and a `\uXXXX`
escape sometimes arrives decoded even through the file-write tool. Text that must keep such an
escape is produced by a script that builds the backslash from its character code.

## Sweep

Where the change makes a stated claim false (a count, a type, a supported set, a rule, a message),
sweep every tracked file for each restatement of the claim itself, not only the file you already
opened, with line breaks and comment leaders collapsed so a copy wrapped mid-phrase is found: a
line-based grep reports nothing for it. The same sentence is usually in a sibling doc, in a
generated header's note, or in another section of the file you just edited. Change every copy in the
same commit, or name it as out of scope in the hand-back. A reviewer names only the copy it happened
to open, so this is the one class review cannot find for you.

## Files

A dispatch creates files only where it was sent. Sub-agent, external builder and reviewer alike
create files only inside the worktree they were handed, and only the files the task names; a
read-only dispatch creates none inside any working tree. Every probe, copy, fixture, scratch repo,
download and log goes outside every working tree, in the session's own scratch directory, which is
never the main checkout. A location the tree git-ignores counts as outside it for that sentence and
for nothing else: what it protects is an empty porcelain status and a worktree that can still be
removed. It is somewhere to leave transient output, never a licence to write configuration the
tooling reads -- an ignored overlay or settings file is no more a dispatch's to write than a tracked
one. The exception is the repo's own gate commands: what they write where they run is theirs, not
yours.

## Waits

A dispatch waits for its own runs within its turn. A builder or reviewer that starts a long run
polls its output in the foreground, with check calls of bounded length repeated until the run ends,
and reports in the same turn. It never ends its turn to wait for a completion notice: a notice can
fail to arrive, and a dispatch that has ended its turn is not woken by the run it started.

## Named head

Work is tied to a named head. Every brief names the commit it applies to, and every report restates
it. A report on a commit other than the one the session holds is stale: the session discards it and
says so. Once a dispatch has reported, it makes no edit and starts no run in its worktree until a
brief naming a commit hands the worktree back. The session moves on from a dispatch by reading its
worktree at the named head (the diff, the gate output, and no process the dispatch started still
running), never by the arrival of its report or a completion notice: a report can be lost, and a
notice can fire while the dispatch's own runs still work.

## State note

A dispatch keeps a state note at the path the brief names in the session's scratch directory: the
commit, the step, each run in flight, with its PID and the file it writes, and the next step. It
rewrites the note whenever a step ends or a run starts, since a dispatch cut off by its model cannot
write one at the end.

## Gate environment

A gate that replays a CI job runs with that job's `env:` set, read from the workflow.

## What CI checks

What the local run cannot reproduce, such as the runner account's `PATH` or a layout the job builds,
is named in the gate evidence as CI's to check, never assumed green.

## Two reads

A long gate is judged by two reads, never by the clock. One is whether the gate's own process — the
process its command started to do the checking, not a helper it spawned that may outlive it — is
still present. The other is its last progress marker, its newest output or log write, read again to
see whether it moved. Either one present means the gate is alive and the run keeps waiting. With
neither, the gate is wedged, and the run reports it at once: the finding names the gate command, the
last progress marker it read and the absence of the gate's process. A wedged gate is neither a pass
nor a fail, and its evidence goes where gate results already go. No duration, no timeout, no polling
interval. A fresh core file, one written after the gate started, says a process the gate depends on
died: it may mark the gate wedged while the gate's own process is still present. The finding then
names the core file beside the gate command and the last progress marker. The run waits for the
gate within its own turn: it runs the gate in the foreground, or blocks on a gate it started in the
background, re-issuing the blocking call until the gate ends.
It never ends its turn while a gate it started still runs, to wait for a completion notice or a
scheduled wake-up: a headless session that ends its turn exits, and nothing reads the gate's result.

## Coverer

A gate the run cannot finish here, wedged or unable to run, is covered only by
a CI leg the PR's own check list shows ran, the leg that runs that gate's job:
`gh pr checks <PR> -R <slug>` once the PR is open, where a path-filtered leg reads `skipping` and
covers nothing. With no such leg, the record names the gate uncovered.
