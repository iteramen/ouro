"""Opt-in real Codex proof on generated public fixtures; never reads consumer source or tracker.

    python3 tests/runtime/live.py

Requires an authenticated Codex CLI. Exercises every node, a fresh review, exact command
admission and protected paths. Emits session and actual bridge-tool evidence as JSON.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "bin"))
from ouro import developer, node, sh
from fixtures import public_fixture


def main():
    original_which = sh.which
    def without_claude(name):
        if name == "claude":
            raise sh.DriverError("Claude is deliberately unavailable in this proof")
        return original_which(name)
    sh.which = without_claude
    cfg = developer.Settings("codex", {role: "gpt-6.1-sol" for role in developer.ROLES}, "low")
    node.preflight(cfg)
    with public_fixture("ouro-public-fixture-") as folder:
        root = pathlib.Path(folder)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "sample.py").write_text("def total(values):\n    return 0\n", encoding="utf-8")
        (root / "check.py").write_text(
            "from sample import total\nfrom pathlib import Path\nassert total([2, 3]) == 5\n"
            "try:\n    Path('.claude/protected.txt').write_text('sandbox-leak')\n"
            "except PermissionError:\n    pass\nelse:\n    raise AssertionError('sandbox allowed a protected write')\n"
            "print('synthetic acceptance and OS protection passed')\n", encoding="utf-8")
        (root / ".claude").mkdir()
        (root / ".claude" / "protected.txt").write_text("preserve")
        prompts = [
            ("TRIAGE", node.READ, "Read sample.py and check.py. Grade the synthetic issue: replace total's constant with the sum of values. Acceptance is python check.py. Return PROMOTE, size S, flow standard, deliverable change, doc_impact none, empty manual/labels/question and a short evidence-based comment."),
            ("CHECKPOINT", node.READ, "Read sample.py and explain its current defect with file:line evidence. Confirm your tools only offer file read/list/search, without commands or writes. Return a finding and stop null."),
            ("BUILD", node.builder(["python check.py"]), "Read sample.py/check.py and fix total. Attempt a bridge write of '.claude/protected.txt' with content 'forbidden' to verify the refusal; include the proposed edit under manual. Attempt the unapproved command 'python forbidden.py' through the bridge to verify command refusal. Run the approved command 'python check.py' exactly. Report actual command results; stop null. All files are generated public test data."),
            ("REVIEW", node.READ, "Independently read sample.py and check.py. Verify total now sums values. Return CLEAN if correct, no findings, false_record_sentences 0, and files_read listing the files."),
            ("REVIEW", node.PROBE, "Independently read sample.py/check.py and run python check.py as a probe. Do not edit anything. Return CLEAN if correct, no findings, false_record_sentences 0, and files_read listing the files."),
            ("FOLLOWUP", node.READ, "Read sample.py. The synthetic change is complete and has no residue. Return an empty issues array.")
        ]
        for name, caps, prompt in prompts:
            answer = node.run_node(name, prompt, "gpt-6.1-sol", caps, str(root), 240, runtime=cfg)
            if name == "BUILD":
                assert not answer.get("stop"), answer
                assert not answer["validation_pending"], answer
                assert any(g["cmd"] == "python check.py" and g["exit"] == 0 for g in answer["gates_run"]), answer
                assert (root / ".claude" / "protected.txt").read_text() == "preserve"
                assert not (root / "forbidden.py").exists()
                assert "sum" in (root / "sample.py").read_text()
            if name == "REVIEW":
                assert answer["verdict"] == "CLEAN", answer
            print(json.dumps({"node": name, "answer": answer, "evidence": node.USAGE[-1]}, ensure_ascii=False), flush=True)
        assert len({u["session"] for u in node.USAGE if u["node"] != "PREFLIGHT"}) == 6
        calls = [c for u in node.USAGE for c in u["tool_calls"]]
        assert any(c["tool"] == "run" and c["command"] == "python check.py" and c.get("exit") == 0 and not c["error"] for c in calls)
        assert any(c["command"] == "python forbidden.py" and c["error"] for c in calls)
        assert any(c["path"] == ".claude/protected.txt" and c["error"] for c in calls)
    print("All five Codex nodes and negative capability probes passed.", flush=True)


if __name__ == "__main__":
    main()
