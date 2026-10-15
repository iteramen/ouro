# Mutation rows: showing a test can go red

The evidence `/ouro:execute` §3 asks for each test a PR adds, and for every gate, lint, filter
or check it introduces: the narrower case it was built for, shown firing. `/ouro:review` asks
the same of a probe.

- [The scenario the change exists to catch](#the-scenario-the-change-exists-to-catch)
- [One mutation per test](#one-mutation-per-test)
- [Shapes that stay green](#shapes-that-stay-green)
- [Code with no test host](#code-with-no-test-host)
- [Stage before mutating](#stage-before-mutating)
- [The table](#the-table)

## The scenario the change exists to catch

A gate, lint, filter or test proves nothing by passing on the PR that introduces it — that PR
touches everything it added. Construct the narrower case it was built for (the one-sided change,
the missing input, the stale cache) and demonstrate the new machinery triggering on *that*.
Where it cannot be run, cite the artifact that decides it.

## One mutation per test

When the thing added **is** a test, that narrower case is a **mutation of the code the test
covers**. Each test the PR adds gets its own mutation, of what that test claims, and its own
row: run the suite that holds it and show that test go red for that reason — the failing
assertion's own message, not merely a non-zero exit. Among the PR's new tests it alone goes
red; an existing test that also goes red is named, not held against it, since tests
legitimately overlap. One coarse break that turns several new tests red at once proves none of
them: a test asserting a value the failure path also returns, or a field's unchanged initial
state, goes red under a throw and stays green under the break that matters. A test that stays
green under its own mutation is not coverage.

For a bug's regression test the red-first run (`/ouro:execute` §3) is that mutation — the
untouched tree is the break, its diff the fix reversed — and its row goes in this table; a bug
PR adding several tests still owes each its own. **A fix round that adds or changes a check in
executed code adds a row of its own**: it goes red under that change's reversal, in the table
re-stamped at the fix commit — the same evidence `/ouro:review` asks of a probe.

## Shapes that stay green

Five shapes keep staying green, and a test in one of them owes the row its shape names:

- a condition around an assertion: the row sets the condition to the value that makes it skip;
- an asserted key or line: the row moves it one block over;
- an empty-input guard: the row runs an empty fixture;
- a claim about a path, a name or an encoding: the row sets up that case (a non-ASCII name, a
  symbolic link, a short 8.3 path, a hostile code page), with a skip line naming the OS where
  the OS refuses it;
- a predicate ported from one language to another: the row is a differential one, the same
  inputs through both implementations with every disagreement red, or the port is replaced by a
  call to the original.

The opposite shape goes red and proves nothing: a test that restates the code, by asserting a
constant equals its own literal, reading a module's source as text where running it could show
the behaviour, or stubbing a dependency past the failure its claim is about. Its row mutates
behaviour reached through the code's interface, the result a caller sees. Asserting on text is
that behaviour where the text itself is the artifact under test (a template, a workflow, a skill
file a gate or a session reads), and a source pin stands where its comment names what no run
can reach.

## Code with no test host

Code with no test host (no test project in the repository can load it) owes no mutation row,
except a change inside a workflow step's `run:` block, which owes the probe below. First move
every piece of logic you can into code a test reaches, so only glue stays untested. The
hand-back names each untested line and why no test host reaches it, and the PR body's
`Smoke by hand:` line lists what the merger checks in the running app. That code stays UNTESTED
and is never counted as rejected.

**A `run:` block's probe.** Extract the step's literal `run:` block verbatim and run it against a
stub of the CLI it calls, in a scratch workspace, once with the change and once without it, and
read each run's exit. The stub proves itself first, as `dispatch-rules.md` has it under Writes off
this machine. Where the two exits differ, the row goes in the table below in place of the
untested-line note (the hand-back's
and the `Smoke by hand:` line): Mutation names the change taken out, Test the extracted block with
its stub, Result the two exits, How the commands.

## Stage before mutating

**Stage the work before mutating it, and never commit a mutation.** `git restore` and
`git checkout -- <file>` put a file back from the index, so on unstaged work they discard the
fix and the new test along with the mutant; the plugin's `hooks/guard-discard.py` refuses that
call. With the work staged (`git add`), `git diff` is the mutation alone, and `git restore`
returns exactly the staged work. The rule holds for the session's own edits in the worktree as
much as a builder's; the alternative that needs no restore is to mutate on an export
(`git archive HEAD | tar -x -C <scratch>`), as a reviewer does.

## The table

The evidence is a table in the PR body, in the measurement table's shape, stamped with the
commit that holds the tests and the code they cover (under M the session stamps it after
committing):

    **Mutation-tested** @ <short SHA>

    | Mutation | Test | Result | How |
    |---|---|---|---|
    | <what was broken> | <the test> | <red; the assertion's message> | <command, the platform, and its exit code> |
