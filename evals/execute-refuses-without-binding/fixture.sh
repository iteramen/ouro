#!/usr/bin/env bash
# A repository with a commit and no .claude/ouro.toml.
set -eu
git init -q -b master .
printf '%s\n' '# Fixture' 'A repository that never bound ouro.' > README.md
git -c user.name=fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false add -A
git -c user.name=fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false commit -q -m 'base'
