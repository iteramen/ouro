"""A stand-in for the external CLI `grok` (tests/driver/driver.Tests.py): prints the k-th canned answer.

Env: FAKE_GROK_DIR holds grok.<k>.json (else grok.json) as {"stdout": text, "exit": n}, and
this call's arguments are appended to calls.txt there, one line per call.
"""
import json
import os
import sys
from pathlib import Path

d = Path(os.environ["FAKE_GROK_DIR"])
log = d / "calls.txt"
k = len(log.read_text(encoding="utf-8").splitlines()) + 1 if log.is_file() else 1
with open(log, "a", encoding="utf-8", newline="\n") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
answer = d / f"grok.{k}.json"
if not answer.is_file():
    answer = d / "grok.json"
ans = json.loads(answer.read_text(encoding="utf-8"))
sys.stdout.write(ans.get("stdout", ""))
sys.exit(ans.get("exit", 0))
