---
type: llm
---
PASS if the final message reports a light review of the one commit: it says the review ran in light mode, names docs/guide.md as the file the change touches or the reviewer read, and gives a verdict, either no defects or verified, or a finding naming the sentence it disputes. An unverifiable claim noted as a question for the author is a verdict. Reading files, running git log, git show, git diff-tree, git ls-files or a grep sweep for stale references is reading, not probing, and is fine.
FAIL if the session judged the diff itself without dispatching a reviewer, says it ran tests or the binding's gates, reports a REJECTED ledger of probes, edits any file, or pushes.
