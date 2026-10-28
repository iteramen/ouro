# Hooks

The guards the plugin ships: Claude Code `PreToolUse` hooks that refuse a call whose shape breaks a
rule of the contract's law of the road (`docs/contract.md` §7). `hooks.json` registers them and
`.claude-plugin/plugin.json` names that file, so a plugin install runs them with no settings edit;
`${CLAUDE_PLUGIN_ROOT}` in a command is the installed plugin's root. Every registered entry whose
matcher fits runs on each call, and any one refusal stops the call.

| Hook | Refuses | Override | Fixtures |
|---|---|---|---|
| `bin/forbidden-tokens.py hook` | a `gh issue` or `gh pr` publishing call, or a `git commit`, whose text (less the value of a `-R` or `--repo` outside quotes) or whose body file matches an entry of the private list `OURO_FORBIDDEN_TOKENS` names, unless the binding at each directory the call lands in turns the check off for the repository the call posts to, or, the key absent there, the list's `# protects:` line names neither that directory's repository nor one the call names (a `-R`, `--repo` or `GH_REPO` value, or an issue or pull request URL), and any such call on a machine with no list, unless the binding turns the check off or, with the key absent, `gh repo view` reports the repository PRIVATE or INTERNAL (`README.md`, The private token list) | none: the list is the owner's | `tests/hooks/forbidden-tokens/` |
| `hooks/guard-delete.py` | a recursive delete whose target is a pattern, a substitution, a variable the guard cannot see assigned, a pipeline, or a root, home, `~/.claude`, the checkout or a worktree; a recursive delete with its errors silenced; an assignment to a PowerShell variable the runtime reserves; `git worktree remove --force` and `git clean -f` with `-d` or `-x` outside scratch. A command that removes a path removes a literal path: the object returned by a create that never returns an existing path, or a path under the session's scratch directory, by exact name, since another run's files sit under the same root. | `# delete-ok: <why>` on the worktree and clean rule only | `tests/hooks/guard-delete/` |
| `hooks/guard-stash-kill.py` | every `git stash` but `push -m <message>`, `apply <sha>`, `list` and `show`, since the stash stack is shared by every worktree of the repository and a pop can take another session's entry (stage the work instead); `pkill`, `killall`, `Stop-Process` without `-Id`, `taskkill /IM`, a `kill` fed by `pgrep`, `pidof` or `ps`, a group or all-process kill: stop only a process you started, by the PID you recorded, since a pattern matches every process on the host that holds it, another session's jobs and your own shell included; and a `kill` of a PID whose working directory lies in a `.claude/worktrees/` worktree other than the call's own, read from the process on Linux and macOS: the session reads the PID's cwd before a kill, and a process in another worktree is another session's. | `# pid-ok: <where the PID was recorded>` on the worktree rule only | `tests/hooks/guard-stash-kill/` |
| `hooks/guard-commit.py` | an author or committer identity naming an agent (`claude`, `anthropic`, `copilot`, `codex`, `[bot]`, `noreply@anthropic.com`) in `git commit --author`, `git -c user.name=` or `user.email=`, `git config user.name` or `user.email`, or a `GIT_AUTHOR_*` or `GIT_COMMITTER_*` assignment: a commit carries the human who ran the session, wherever the call's directory is. | none | `tests/hooks/guard-commit/` |
| `hooks/guard-discard.py` | `git checkout -- <path>`, `git checkout <ref> -- <path>`, `git checkout .`, `git restore <path>` and its `--worktree` forms when a named path has changes in the worktree and nothing staged, so the restore would drop the only copy of the work, or when a path is a variable; a path with staged changes passes, since the restore returns the staged work. Stage the work before mutating it, or mutate on an export (`git archive HEAD \| tar -x -C <scratch>`). | `# discard-ok: <why>` | `tests/hooks/guard-discard/` |

## Session start

`hooks/session-sync.py`, a `SessionStart` hook on `startup`, keeps a bound repository's main
checkout current: on the default branch with no tracked changes, it fetches and fast-forwards to
`origin/<default>`, never over an untracked or ignored file.
Claude Code reads the instructions before the hook runs, so a move prints one line asking for a
restart. A dirty, diverged or other-branch checkout is left as it is, with one line saying why;
a linked worktree and an unbound repository are untouched. It never stashes, resets or rebases.
`tests/hooks/session_sync_tests.py` is its test.

## How a guard fails

A guard refuses by exit 2, with the reason on stderr, which Claude Code shows the session. Every
other outcome lets the call run: exit 0, any other exit code, an interpreter it cannot find (exit
127) and a run past the entry's `timeout` are all non-blocking. A guard therefore fails open: a
machine without `python3` on the `PATH` Claude Code starts with, or a guard that stalls, disables
itself silently. So a guard keeps its parsing cheap (no network, no `gh`), every entry sets a
`timeout`, the suite below pins the exit codes, and the rule each guard enforces stays in the
prose beside it. Codex supports user-level PreToolUse hooks, canonical Bash events and exit-2
refusals too. Its non-managed hooks require explicit trust; installing ouro's Codex skills does
not register or trust global hooks. See the [Codex hook documentation](https://learn.chatgpt.com/docs/hooks).

Ouro's Codex nodes use a constrained private file bridge and disable inherited hooks. The bridge
runs the existing delete, stash/kill, discard and author guards before approved commands, then
executes those commands through the Codex OS sandbox. File-only nodes expose no command tool.
Exit 2 is a policy refusal. A missing, crashed or timed-out guard is a driver infrastructure
failure, and cannot become an owner question through the model's answer.
This node boundary is independent of hooks installed in an interactive developer session.

## Testing

`pwsh -NoProfile -File tests/hooks/Test-Hooks.Tests.ps1` runs every `PreToolUse` hook `hooks.json`
registers once per recorded event under `tests/hooks/<hook>/`, `<hook>` being the script's file name
without its extension, and compares the exit code with the fixture's decision: `allow` is exit 0
and `deny` is exit 2, and any other code fails the row by name. Each hook needs at least one event
it allows and one it refuses. A fixture is one JSON file: `decision`, the `event` as Claude Code
sends it on standard input, an optional `reason` regex the refusal's stderr must match, and an
optional `env` map set for that run (`null` removes a variable); `${FIXTURES}` anywhere in the
file is the fixture directory.

## Without the plugin

A vendored tree (`bin/Vendor-Ouro.ps1`) has no plugin root, so nothing registers these hooks
there. Register the ones wanted in `settings.json` under `hooks`, each entry as `hooks.json`
spells it, with the path of a plugin clone in place of `${CLAUDE_PLUGIN_ROOT}`.
