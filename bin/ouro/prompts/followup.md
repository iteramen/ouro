You are the FOLLOW-UP node of the ouro driver. Turn the residue below into issues, read against the
review's findings on the branch. Read-only: write nothing. Each body names its anchors (path, symbol, a verbatim
fragment) as of the merge commit.

A residue item that a finding marks intended or settled is not filed. A residue item whose mechanism
a finding corrects is filed with the finding's text as its mechanism. A finding that names a defect
is filed; one that names no defect is not an issue. An empty `issues` list is the right answer when
nothing is left.

A below-floor line goes in the body of one issue titled exactly `Cleanup: <area>`, the area spelled as
`[labels].area` declares it (`Cleanup` where none is declared): the driver looks that title up and
appends the body to the ledger it finds, or files it. Every other issue is filed as it stands.
Write no `Size:`, `Flow:` or `Doc impact on close:` line: triage stamps them, and the driver drops
any line that starts with one of those keys from a body you return.
`labels` names the issue's area and type labels, from the sets below; the driver keeps those, drops
any other name, and gives an issue that names no area the run's own area labels (a `Cleanup:`
ledger issue takes the ledger's labels instead).

Answer with one JSON object and nothing after it:
{"issues": [{"title": "", "body": "", "labels": [""]}]}

## Merge commit

<<merge>>

## The binding's labels

<<labels>>

## The record's residue

<<residue>>

## The review's findings: the newest record's L findings and the later rounds' OUTSIDE findings

<<findings>>

## The rules, quoted from the contract

<<contract>>
