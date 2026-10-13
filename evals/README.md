# Evals

`claude plugin eval .` runs these cases against this plugin, three runs each plus a no-plugin
baseline. Each case is a directory with a `prompt.md`, a `case.yaml` naming its scaffold, the
scaffold itself, and one grader per file under `graders/`.

The cases exercise what a session must do before it reaches the tracker, since a run has no
network, no `gh` and no credential: a refusal the contract requires, and the review path on a
repository the scaffold builds. A scaffold runs only with `--scaffold`; the shell runs only with
`--allow-tools Bash`, under the OS sandbox, which native Windows lacks, so the suite runs on a
Linux host:

    claude plugin eval . --trust-plugin --scaffold --allow-tools Bash --model sonnet --judge-model haiku --no-publish --threshold 0.8

A case passes at a score of 0.8 or better across its runs: one grader missed in one of three
runs is the agent's variance, not a regression. `results/` is written by each run and ignored
by git.
