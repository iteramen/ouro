---
description: an adversarial review of a one-commit documentation change takes light mode, dispatches the reviewer and reports what it read and a verdict
tags: [review]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Agent, Bash]
---

Run an adversarial review of the commit on this branch, master..HEAD, before I push it. It changes only a documentation sentence.
