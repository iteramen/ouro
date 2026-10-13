#!/usr/bin/env bash
# A bound repository with one docs-only commit on a feature branch.
set -eu
git init -q -b master .
mkdir -p .claude docs
cat > .claude/ouro.toml <<'TOML'
schema = 1

[repo]
slug = "example/fixture"
default_branch = "master"
protected_branch_prefixes = ["release/"]

[ship]
policy = "stop-at-pr"
review = "adversarial-review"

[models]
reviewer = "sonnet"

[owner]
ruling_approvers = ["example-owner"]
TOML
printf '%s\n' '# Fixture' > README.md
printf '%s\n' '# Guide' '' 'The tool reads its settings from settings.toml at the repository root.' > docs/guide.md
git -c user.name=fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false add -A
git -c user.name=fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false commit -q -m 'base'
git switch -q -c docs/settings-path
printf '%s\n' '# Guide' '' 'The tool reads its settings from .config/settings.toml at the repository root.' > docs/guide.md
git -c user.name=fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false commit -q -am 'docs: the settings path moved under .config'
