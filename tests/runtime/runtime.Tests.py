"""Runtime contract, admission, transport, cleanup and isolated developer-install regressions."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
BASE = pathlib.Path(__file__).resolve().parents[2]
if not (BASE / "bin" / "ouro").is_dir():
    print("SKIP: runtime suite requires the plugin layout")
    sys.exit(0)
sys.path.insert(0, str(BASE / "bin"))
from ouro import developer, node, sh
from ouro.runtime import READ, builder, codex, diagnostics, git_capabilities, process, schema
from ouro.runtime.tools import Bridge, sandbox_argv
from coordination_tests import CoordinationTests
from git_capability_tests import GitCapabilityTests

from codex_capture_tests import CodexCaptureTests
from network_tests import NetworkReadinessTests
from file_only_tests import FileOnlyReadinessTests
from validation_tests import ValidationTests
from execution_tests import ExecutionTests, FingerprintTests
from execution_lifecycle_tests import GateExecutionTests
from owned_children import reap_fixture_children


def sample(spec):
    if "anyOf" in spec:
        return None
    if "enum" in spec:
        return spec["enum"][0]
    return {"string": lambda: "synthetic", "integer": lambda: 0, "array": lambda: [],
            "object": lambda: {k: sample(v) for k, v in spec["properties"].items()}}[spec["type"]]()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ouro-runtime-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.policy = {"root": str(self.root), "edit": False, "commands": [], "probe": False}

    def config(self, text):
        path = self.root / "developer.toml"
        path.write_text(text, encoding="utf-8")
        return developer.load(path)

    def test_developer_scope_and_precedence(self):
        cfg = self.config('[runtime]\nbackend="codex"\n[runtime.models]\nbuilder="default"\n')
        self.assertEqual(cfg.model("builder", "opus"), "default")
        self.assertEqual(cfg.model("reviewer", "opus"), "default")
        self.assertEqual(developer.Settings().model("builder", "sonnet"), "sonnet")
        with patch.dict(os.environ, {"OURO_DEVELOPER_CONFIG": str(self.root / "missing.toml")}):
            self.assertEqual(developer.load(self.root / "developer.toml").backend, "codex")
            with self.assertRaises(sh.DriverError):
                developer.load()
        for text in ('[ship]\npolicy="merge-on-green"', '[runtime]\nbackend="unknown"',
                     '[runtime]\nbackend="codex"\n[runtime.models]\nbuilder="opus"',
                     '[runtime]\nbackend="claude"\n[runtime.models]\nbuilder="gpt-example"',
                     '[runtime]\nextra=true', '[runtime]\nmodels=[]'):
            with self.subTest(text=text), self.assertRaises(sh.DriverError):
                self.config(text)

    def test_file_only_has_no_execution_or_edits(self):
        b = Bridge(self.policy)
        (self.root / "sample.txt").write_text("alpha\nbeta")
        self.assertIn("2: beta", b.call("read", {"path": "sample.txt"}))
        self.assertEqual(b.call("search", {"pattern": "beta"}), ["sample.txt:2: beta"])
        for tool, args in (("run", {"command": "echo forbidden"}), ("write", {"path": "bad", "content": "x"}),
                           ("delete", {"path": "sample.txt"})):
            with self.assertRaises(ValueError):
                b.call(tool, args)
        self.assertFalse((self.root / "bad").exists())
        self.assertTrue((self.root / "sample.txt").exists())

    def test_edit_boundaries_and_exact_commands(self):
        b = Bridge({**self.policy, "edit": True, "commands": ["git diff *", "git rm *", "git mv *", "python3 check.py"]})
        b.call("write", {"path": "sample.txt", "content": "alpha"})
        b.call("edit", {"path": "sample.txt", "old": "alpha", "new": "beta"})
        self.assertEqual((self.root / "sample.txt").read_text(), "beta")
        b.call("write", {"path": "tests/fixtures/.claude/ouro.toml", "content": "fixture"})
        self.assertEqual((self.root / "tests/fixtures/.claude/ouro.toml").read_text(), "fixture")
        fixture = "tests/fixtures/.claude/worktrees/example.txt"
        b.call("write", {"path": fixture, "content": "fixture"})
        self.assertIn(fixture, b.call("list", {}))
        with self.assertRaises(ValueError):
            b.call("read", {"path": ".claude/worktrees/peer/example.txt"})
        protected = self.root / ".claude/protected.txt"
        protected.parent.mkdir()
        protected.write_text("preserve")
        os.link(protected, self.root / "linked.txt")
        for tool, args in (("write", {"content": "changed"}), ("edit", {"old": "preserve", "new": "changed"})):
            with self.assertRaisesRegex(ValueError, "hard-linked"):
                b.call(tool, {"path": "linked.txt", **args})
        b.call("delete", {"path": "linked.txt"})
        self.assertEqual(protected.read_text(), "preserve")
        for path in ("../outside", ".claude/rules/x.md", ".codex/config.toml", ".git/config", ".agents/skills/x", ".aws/config"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                b.call("write", {"path": path, "content": "x"})
        for command in ("python3 evil.py", "python3 check.py; echo evil", "git diff --output=.claude/config",
                        "git diff --ext-diff", "git rm -r ."):
            with self.subTest(command=command), self.assertRaises((ValueError, sh.DriverError)):
                b.call("run", {"command": command})
        b.call("run", {"command": "git rm sample.txt"})
        self.assertFalse((self.root / "sample.txt").exists())
        b.call("write", {"path": "source.txt", "content": "source"})
        b.call("write", {"path": "existing.txt", "content": "preserve"})
        with self.assertRaises(ValueError):
            b.call("run", {"command": "git mv source.txt existing.txt"})
        self.assertEqual((self.root / "existing.txt").read_text(), "preserve")
        b.call("run", {"command": "git mv source.txt moved.txt"})
        self.assertEqual((self.root / "moved.txt").read_text(), "source")
        self.assertFalse((self.root / "source.txt").exists())
        with patch.object(sh, "run", return_value=sh.R(1, "", "guard crashed", [])), patch.object(process, "run") as launch:
            with self.assertRaisesRegex(sh.DriverError, "guard-delete failed"):
                b.call("run", {"command": "python3 check.py"})
            launch.assert_not_called()
        if os.name != "nt":
            outside = self.root.parent / (self.root.name + "-outside")
            outside.mkdir()
            self.addCleanup(outside.rmdir)
            (self.root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                b.call("write", {"path": "link/escape", "content": "x"})

    def test_sandbox_interface_and_profile_on_all_platforms(self):
        for platform, name in (("linux", "posix"), ("darwin", "posix"), ("win32", "nt")):
            with self.subTest(platform=platform), patch("ouro.runtime.tools.sys.platform", platform), patch("ouro.runtime.tools.os.name", name), patch.object(sh, "argv_for", side_effect=lambda *args: list(args)):
                command = sandbox_argv(self.root, ["python3", "-c", "print('ready')"])
            expected = ["codex", "sandbox", "-c", 'permissions.ouro.extends=":workspace"',
                        "-c", "permissions.ouro.network.enabled=false",
                        "-c", 'permissions.ouro.filesystem={":workspace_roots"={".git"="read",".claude"="read",".codex"="read",".agents"="read",".aws"="read"}}',
                        "--permission-profile", "ouro", "--include-managed-config"]
            if name == "nt":
                expected += ["-C", str(self.root)]
            self.assertEqual(command, [*expected, "python3", "-c", "print('ready')"])

    def test_probe_cannot_publish(self):
        b = Bridge({**self.policy, "probe": True})
        for command in ("gh issue comment 1 --body x", "git push origin HEAD", "git -c user.name=x commit -m x"):
            with self.assertRaises(ValueError):
                b.call("run", {"command": command})

    def test_strict_schemas_and_meaningful_stops(self):
        for name, spec in schema.SCHEMAS.items():
            value = sample(spec)
            schema.validate(value, spec)
            with self.assertRaises(sh.DriverError):
                schema.validate({**value, "extra": True}, spec)
        with self.assertRaises(sh.DriverError):
            schema.validate_answer("CHECKPOINT", {"finding": "", "stop": {"reason": "tooling", "question": ""}})
        with self.assertRaises(sh.DriverError):
            schema.validate_answer("TRIAGE", {"verdict": "NEEDS-RULING", "question": ""})

    def test_codex_transport_uses_final_file_and_records_evidence(self):
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        def fake(argv, **kwargs):
            argv = list(argv)
            answer = {"finding": "synthetic evidence", "stop": None}
            pathlib.Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps(answer))
            answer_path = pathlib.Path(argv[argv.index("--output-last-message") + 1])
            (answer_path.parent / "tools.jsonl").write_text(json.dumps({"tool": "read", "path": "sample.txt", "error": None}) + "\n")
            self.assertIn("features.shell_tool=false", argv)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--ignore-rules", argv)
            self.assertIn("skills.include_instructions=false", argv)
            self.assertIn("features.skill_search=false", argv)
            self.assertIn('web_search="disabled"', argv)
            return sh.R(0, '\n'.join(json.dumps(e) for e in [
                {"type": "thread.started", "thread_id": "synthetic-thread"},
                {"type": "turn.completed", "usage": {"input_tokens": 3, "cached_input_tokens": 2, "output_tokens": 1}}]), "", argv)
        with patch.object(process, "run", side_effect=fake), patch.object(sh, "which", side_effect=lambda name: name):
            answer, session, usage, cost = codex.execute("CHECKPOINT", "synthetic prompt", "default", READ, self.root, 5, None, {}, developer.Settings("codex"))
        self.assertEqual(answer, {"finding": "synthetic evidence"})
        self.assertEqual(session, "synthetic-thread")
        self.assertEqual(usage["cache_read_input_tokens"], 2)
        self.assertIsNone(cost)
        self.assertEqual(diagnostics.ATTEMPTS[-1]["outcome"], "adapter_returned")
        self.assertTrue(diagnostics.ATTEMPTS[-1]["provider_completed"])

    def test_missing_output_and_unsupported_capabilities_refuse(self):
        good = sh.R(0, '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed"}', "", [])
        with patch.object(process, "run", return_value=good), patch.object(sh, "which", side_effect=lambda name: name):
            with self.assertRaisesRegex(sh.DriverError, "final structured result"):
                codex.execute("CHECKPOINT", "p", "default", READ, self.root, 1, None, {}, developer.Settings("codex"))
        for body in ("not JSON", '{"finding":"evidence","stop":null,"unexpected":true}'):
            def malformed(argv, **kwargs):
                pathlib.Path(argv[argv.index("--output-last-message") + 1]).write_text(body)
                return good
            with patch.object(process, "run", side_effect=malformed), patch.object(sh, "which", side_effect=lambda name: name):
                with self.assertRaisesRegex(sh.DriverError, "final structured result"):
                    codex.execute("CHECKPOINT", "p", "default", READ, self.root, 1, None, {}, developer.Settings("codex"))
        with patch.object(process, "run") as launch:
            with self.assertRaisesRegex(sh.DriverError, "capability"):
                codex.execute("CHECKPOINT", "p", "default", ["Read"], self.root, 1, None, {}, developer.Settings("codex"))
            launch.assert_not_called()

    def test_command_refusals_recover_through_stdio_and_codex(self):
        import io
        from ouro.runtime import tools as bridge_tools
        commands = [
            'python3 -c "import pathlib; pathlib.Path(\'rejected.txt\').touch()"',
            'python3 "unterminated',
            "python3 check.py | echo forbidden", "python3 check.py && echo forbidden",
            "python3 check.py > rejected.txt", "python3 check.py\necho forbidden",
            "python3 check.py $(echo forbidden)", "python3 check.py `echo forbidden`",
            "A=1 A=2 python3 check.py", "A=1", "", "python3 unapproved.py",
            "A=1 python3 check.py",
        ]
        approved = ["python3 check.py", *[c for c in commands if c not in ("", "python3 unapproved.py")]]
        (self.root / "sample.txt").write_text("preserved")
        for command in commands:
            with self.subTest(command=command):
                calls = []
                def fake(argv, **kwargs):
                    target = pathlib.Path(argv[argv.index("--output-last-message") + 1])
                    policy = target.with_name("policy.json")
                    requests = [
                        {"id": 1, "method": "tools/call", "params": {"name": "run", "arguments": {"command": command}}},
                        {"id": 2, "method": "tools/call", "params": {"name": "list", "arguments": {}}},
                    ]
                    output = io.StringIO()
                    with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)))), patch.object(sys, "stdout", output), patch.object(sh, "run") as guard, patch.object(process, "run") as launch:
                        bridge_tools.serve(policy)
                    guard.assert_not_called()
                    launch.assert_not_called()
                    responses = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
                    self.assertTrue(responses[0]["isError"])
                    self.assertFalse(responses[1]["isError"])
                    self.assertIn("sample.txt", json.loads(responses[1]["content"][0]["text"]))
                    calls.extend(json.loads(line) for line in target.with_name("tools.jsonl").read_text().splitlines())
                    self.assertEqual(calls[0]["command"], command)
                    self.assertTrue(calls[0]["error"])
                    self.assertFalse(calls[0]["infrastructure"])
                    self.assertEqual(calls[1]["tool"], "list")
                    self.assertIsNone(calls[1]["error"])
                    target.write_text(json.dumps({"finding": "synthetic evidence", "stop": None}))
                    return sh.R(0, '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed"}', "", argv)
                with patch.object(process, "run", side_effect=fake), patch.object(sh, "which", side_effect=lambda name: name), patch.object(codex, "git_readiness"), patch.object(codex, "WINDOWS", False):
                    answer, session, usage, cost = codex.execute("CHECKPOINT", "p", "default", builder(approved), self.root, 5, None, {}, developer.Settings("codex"))
                self.assertEqual(answer, {"finding": "synthetic evidence"})
                self.assertEqual(usage["tool_calls"], calls)
                self.assertFalse((self.root / "rejected.txt").exists())

    def test_tool_infrastructure_failure_cannot_become_owner_stop(self):
        import io
        from ouro.runtime import tools as bridge_tools
        policy = self.root / "policy.json"
        evidence = self.root / "tools.jsonl"
        policy.write_text(json.dumps({**self.policy, "evidence": str(evidence), "commands": ["synthetic"]}))
        requests = [
            {"id": 1, "method": "tools/call", "params": {"name": "run", "arguments": {"command": "synthetic"}}},
            {"id": 2, "method": "tools/call", "params": {"name": "list", "arguments": {}}},
        ]
        output = io.StringIO()
        with patch.object(sh, "run", return_value=sh.R(1, "", "guard crashed", [])), patch.object(process, "run") as launch, patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)))), patch.object(sys, "stdout", output):
            bridge_tools.serve(policy)
        launch.assert_not_called()
        calls = [json.loads(line) for line in evidence.read_text().splitlines()]
        self.assertTrue(calls[0]["infrastructure"])
        self.assertIn("guard-delete failed", calls[0]["error"])
        self.assertIsNone(calls[1]["error"])
        self.assertFalse(calls[1]["infrastructure"])
        responses = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
        self.assertTrue(responses[0]["isError"])
        self.assertFalse(responses[1]["isError"])
        def fake(argv, **kwargs):
            answer = sample(schema.SCHEMAS["BUILD"])
            answer["stop"] = {"reason": "tooling", "question": "Please repair the host"}
            target = pathlib.Path(argv[argv.index("--output-last-message") + 1])
            target.write_text(json.dumps(answer))
            target.with_name("tools.jsonl").write_text(evidence.read_text())
            return sh.R(0, '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed"}', "", argv)
        with patch.object(process, "run", side_effect=fake), patch.object(sh, "which", side_effect=lambda name: name), patch.object(codex, "git_readiness"), patch.object(codex, "WINDOWS", False):
            with self.assertRaisesRegex(sh.DriverError, "codex BUILD: tool infrastructure failed"):
                codex.execute("BUILD", "p", "default", builder(["synthetic"]), self.root, 1, None, {}, developer.Settings("codex"))

    def test_attempt_diagnostics_survive_rejection_without_accepting_review(self):
        self.addCleanup(node.USAGE.clear)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        node.USAGE.clear()
        diagnostics.ATTEMPTS.clear()
        def fake(argv, **kwargs):
            target = pathlib.Path(argv[argv.index("--output-last-message") + 1])
            target.write_text(json.dumps(sample(schema.SCHEMAS["REVIEW"])))
            target.with_name("tools.jsonl").write_text(json.dumps(
                {"infrastructure": True, "error": "private-tool-content"}))
            return sh.R(0, '\n'.join(map(json.dumps, [
                {"type": "thread.started", "thread_id": "synthetic-session"},
                {"type": "turn.completed", "usage": {"input_tokens": 42, "output_tokens": 7,
                                                     "private": "private-usage-content"}}])), "", argv)
        with patch.object(process, "run", side_effect=fake), patch.object(sh, "which", side_effect=lambda n: n):
            with self.assertRaisesRegex(sh.DriverError, "tool infrastructure failed"):
                node._real("REVIEW", "private-prompt", "default", READ, self.root, 1, None,
                           developer.Settings("codex"))
        row = diagnostics.ATTEMPTS[-1]
        self.assertEqual((row["session"], row["input"], row["output"]), ("synthetic-session", 42, 7))
        self.assertEqual((row["provider_completed"], row["outcome"], row["phase"]),
                         (True, "failed", "bridge_evidence"))
        self.assertEqual(row["failure_class"], "driver_error")
        self.assertIsNone(row["cache_read"])
        self.assertIsNone(row["cost"])
        self.assertGreaterEqual(row["elapsed_seconds"], 0)
        self.assertEqual(node.USAGE, [])
        self.assertEqual(node.usage_table(), "")
        self.assertNotIn("private", diagnostics.table())

    def test_early_and_interrupted_attempt_metadata(self):
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        for error in (sh.DriverError("private-early-failure"), KeyboardInterrupt()):
            with patch.object(process, "run", side_effect=error), patch.object(sh, "which", side_effect=lambda n: n):
                with self.assertRaises(type(error)):
                    codex.execute("REVIEW", "private", "default", READ, self.root, 1, None, {},
                                  developer.Settings("codex"))
            row = diagnostics.ATTEMPTS[-1]
            for key in ("session", "input", "output", "provider_completed", "exit", "events"):
                self.assertIsNone(row[key], key)
        def interrupted(argv, **kwargs):
            kwargs["observe"]('{"type":"thread.started","thread_id":"partial-session"}\n'
                              '{"type":"item.completed","item":{"text":"private-source"}}', -2)
            raise sh.DriverError("private interruption")
        with patch.object(process, "run", side_effect=interrupted), patch.object(sh, "which", side_effect=lambda n: n):
            with self.assertRaises(sh.DriverError):
                codex.execute("BUILD", "private", "default", READ, self.root, 1, None, {},
                              developer.Settings("codex"))
        row = diagnostics.ATTEMPTS[-1]
        self.assertEqual((row["session"], row["events"], row["exit"]), ("partial-session", 2, -2))
        self.assertIsNone(row["input"])
        self.assertIsNone(row["provider_completed"])
        self.assertNotIn("private", diagnostics.table())

    def test_attempt_projection_is_bounded_and_sanitized(self):
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        diagnostics.ATTEMPTS.clear()
        for _ in range(140):
            with diagnostics.attempt("REVIEW", "codex", "bad|model\n" + "x" * 200) as row:
                diagnostics.events(row, json.dumps({"type": "thread.started", "thread_id": "bad|session\n"}))
                diagnostics.events(row, json.dumps({"type": "turn.completed", "usage":
                    {"input_tokens": -1, "output_tokens": True, "cached_input_tokens": 2**100}}))
        self.assertEqual(len(diagnostics.ATTEMPTS), 128)
        row = diagnostics.ATTEMPTS[-1]
        for key in ("model", "session", "input", "output", "cache_read"):
            self.assertIsNone(row[key], key)
        self.assertLess(len(diagnostics.table()), 40000)
        self.assertNotIn("bad", diagnostics.table())
        for code in (-2**31, -2, 0, 255, 0xC0000005, 2**32 - 1):
            diagnostics.transport(row, "", code)
            self.assertEqual(row["exit"], code)
        for code in (-2**31 - 1, 2**32, True, "1", None):
            diagnostics.transport(row, "", code)
            self.assertIsNone(row["exit"])

    def test_cli_finally_emits_success_and_failure_metadata(self):
        import io
        from ouro import cli
        self.addCleanup(node.USAGE.clear)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        for failure in (sh.DriverError("coordinator failed"), KeyboardInterrupt()):
            def run(*args):
                node._record("BUILD", "accepted-session", {"input_tokens": 3}, None)
                with diagnostics.attempt("REVIEW", "codex", "default"):
                    raise sh.DriverError("private adapter error")
            # Model a coordinator error after a completed node and a rejected attempt.
            def coordinator(*args):
                try:
                    run()
                except sh.DriverError:
                    raise failure
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(cli, "bind") as bind, patch.object(cli.run, "main", side_effect=coordinator), patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
                if isinstance(failure, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        cli.main(["run", "1"])
                else:
                    self.assertEqual(cli.main(["run", "1"]), 1)
                bind.return_value.close.assert_called_once()
            text = output.getvalue()
            self.assertIn("accepted-session", text)
            self.assertIn("Runtime attempt diagnostics", text)
            self.assertIn("failed", text)
            self.assertNotIn("private", text)
            self.assertEqual(len(node.USAGE), 1)
            self.assertEqual(len(diagnostics.ATTEMPTS), 1)

    def test_diagnostics_attempt_projection_is_bounded_and_sanitized(self):
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        diagnostics.ATTEMPTS.clear()
        for _ in range(140):
            with diagnostics.attempt("REVIEW", "codex", "bad|model\n" + "x" * 200) as row:
                diagnostics.events(row, json.dumps({"type": "thread.started", "thread_id": "bad|session\n"}))
                diagnostics.events(row, json.dumps({"type": "turn.completed", "usage":
                    {"input_tokens": -1, "output_tokens": True, "cached_input_tokens": 2**100}}))
        self.assertEqual(len(diagnostics.ATTEMPTS), 128)
        row = diagnostics.ATTEMPTS[-1]
        for key in ("model", "session", "input", "output", "cache_read"):
            self.assertIsNone(row[key], key)
        self.assertLess(len(diagnostics.table()), 40000)
        self.assertNotIn("bad", diagnostics.table())
        for code in (-2**31, -2, 0, 255, 0xC0000005, 2**32 - 1):
            diagnostics.transport(row, "", code)
            self.assertEqual(row["exit"], code)
        for code in (-2**31 - 1, 2**32, True, "1", None):
            diagnostics.transport(row, "", code)
            self.assertIsNone(row["exit"])


    def test_cli_triage_dry_run_emits_rejected_provider_diagnostics(self):
        import io
        from ouro import cli
        self.addCleanup(node.USAGE.clear)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        def fake(argv, **kwargs):
            output = pathlib.Path(argv[argv.index("--output-last-message") + 1])
            output.write_text(json.dumps(sample(schema.SCHEMAS["TRIAGE"])), encoding="utf-8")
            output.with_name("tools.jsonl").write_text(json.dumps(
                {"infrastructure": True, "error": "private-tool-content"}), encoding="utf-8")
            return sh.R(0, '\n'.join(map(json.dumps, [
                {"type": "thread.started", "thread_id": "rejected-session"},
                {"type": "turn.completed", "usage": {"input_tokens": 42, "output_tokens": 7}}])), "", argv)
        def triage(env, number, dry):
            self.assertTrue(dry)
            node._record("PREFLIGHT", "accepted-session", {"input_tokens": 3}, None)
            node._real("TRIAGE", "private-prompt", "default", READ, self.root, 1, None,
                       developer.Settings("codex"))
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "bind") as bind, \
                patch.object(cli.triage, "triage", side_effect=triage), \
                patch.object(process, "run", side_effect=fake), \
                patch.object(sh, "which", side_effect=lambda n: n), \
                patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
            self.assertEqual(cli.main(["triage", "1", "--dry-run"]), 1)
            bind.return_value.close.assert_called_once()
        text = output.getvalue()
        for value in ("accepted-session", "rejected-session", "Runtime attempt diagnostics", "bridge_evidence", "42"):
            self.assertIn(value, text)
        self.assertNotIn("private", text)
        self.assertEqual(len(node.USAGE), 1)
        self.assertEqual(len(diagnostics.ATTEMPTS), 1)
        row = diagnostics.ATTEMPTS[0]
        self.assertTrue(row["provider_completed"])
        self.assertIsNone(row["cache_read"])
        self.assertIsNone(row["cost"])
















    def test_process_observer_gets_partial_output_on_failure(self):
        observed = []
        with self.assertRaisesRegex(sh.DriverError, "timed out"):
            process.run([sys.executable, "-c",
                         "import time; print('synthetic-event', flush=True); time.sleep(30)"],
                        timeout=1, observe=lambda output, code: observed.append((output, code)))
        self.assertEqual(len(observed), 1)
        self.assertIn("synthetic-event", observed[0][0])
        self.assertIsNotNone(observed[0][1])

    @unittest.skipUnless(os.name == "posix", "POSIX cleanup exception observation")
    def test_process_cleanup_failure_retains_diagnostics(self):
        from ouro import validation
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        real_table, real_cleanup = validation.process_table, validation.terminate_owned
        for broken_observer in (False, True):
            with self.subTest(broken_observer=broken_observer):
                ready = self.root / ("ready-" + str(broken_observer))
                release = self.root / ("release-" + str(broken_observer))
                events = [dict(type="thread.started", thread_id="cleanup-session"),
                          dict(type="turn.completed", usage=dict(input_tokens=42, output_tokens=3))]
                script = ("import os,pathlib,time\n"
                          + "print(" + repr("\n".join(map(json.dumps, events))) + ",flush=True)\n"
                          + "pathlib.Path(" + repr(str(ready)) + ").write_text(str(os.getpid()))\n"
                          + "while not pathlib.Path(" + repr(str(release)) + ").exists(): time.sleep(.01)\n")
                def snapshot(env=None):
                    table = real_table(env)
                    if ready.exists() and int(ready.read_text()) in table:
                        release.touch()  # Completion follows a genuine ownership sample.
                    return table
                def cleanup(*args):
                    real_cleanup(*args)
                    raise sh.DriverError("cleanup proof unavailable")
                observed = []
                def observe(output, code):
                    observed.append(code)
                    diagnostics.transport(row, output, code)
                    if broken_observer:
                        raise ValueError("secondary observer failure")
                with patch.object(validation, "process_table", side_effect=snapshot), \
                        patch.object(validation, "terminate_owned", side_effect=cleanup), \
                        self.assertRaisesRegex(sh.DriverError, "cleanup proof unavailable"):
                    with diagnostics.attempt("BUILD", "codex", "fixture") as row:
                        process.run([sys.executable, "-c", script], timeout=5, observe=observe)
                self.assertEqual(observed, [0])
                row = diagnostics.ATTEMPTS[-1]
                self.assertEqual(row["session"], "cleanup-session")
                self.assertTrue(row["provider_completed"])
                self.assertEqual(row["input"], 42)
                self.assertEqual(row["outcome"], "failed")
                self.assertEqual(row["failure_class"], "driver_error")


    def test_network_readiness_requires_nonce_receipt_and_explicit_denial(self):
        from ouro.runtime import network
        from unittest.mock import MagicMock
        nonce = "a" * 48
        for outcome in ("admitted", "accepted-empty", "denied", "windows-denied", "inconclusive", "startup failed"):
            calls = []
            def launch(argv, **kw):
                calls.append((argv, kw))
                if argv[0] != "restricted" or outcome == "admitted":
                    return sh.R(0, "ouro-network-sent", "", [])
                return sh.R(1 if outcome == "startup failed" else 0,
                            "ouro-network-denied\n" if outcome in ("denied", "windows-denied") else "", "", [])
            server, connection = MagicMock(), MagicMock()
            connection.__enter__.return_value = connection
            connection.recv.return_value = nonce.encode()
            if outcome == "accepted-empty":
                connection.recv.side_effect = [nonce.encode(), b""]
            server.__enter__.return_value = server
            server.getsockname.return_value = ("127.0.0.1", 12345)
            server.accept.side_effect = [(connection, None), (connection, None)] if outcome in ("admitted", "accepted-empty") else [(connection, None), network.socket.timeout()]
            with patch.object(network.socket, "socket", return_value=server), patch.object(network.secrets, "token_hex", return_value=nonce), patch.object(network, "sandbox_argv", side_effect=lambda root, argv: ["restricted", *argv]), patch.object(process, "run", side_effect=launch), patch.object(network, "WINDOWS", outcome == "windows-denied"):
                if outcome == "denied":
                    network.readiness(self.root, env={"PUBLIC_CONTROL": "yes"})
                else:
                    with self.assertRaisesRegex(sh.DriverError, "host received generated nonce" if outcome == "admitted"
                                                else "host accepted a TCP connection" if outcome == "accepted-empty"
                                                else "Windows sandbox implementation/policy is not established" if outcome == "windows-denied"
                                                else "sandbox denial not established"):
                        network.readiness(self.root, env={"PUBLIC_CONTROL": "yes"})
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(c[1]["timeout"] == 15 and c[1]["env"] == {"PUBLIC_CONTROL": "yes"} for c in calls))
        server.accept.side_effect = network.socket.timeout()
        with patch.object(network.socket, "socket", return_value=server), patch.object(process, "run", return_value=sh.R(0, "ouro-network-sent", "", [])):
            with self.assertRaisesRegex(sh.DriverError, "unrestricted nonce control failed"):
                network.readiness(self.root)
        with patch.object(network.socket, "socket", side_effect=PermissionError("socket unavailable")), patch.object(process, "run") as launch:
            with self.assertRaisesRegex(sh.DriverError, "cannot establish the disabled-network"):
                network.readiness(self.root)
            launch.assert_not_called()


    def test_network_refusal_precedes_model_probe(self):
        result = lambda out: sh.R(0, out, "", [])
        good = [result("codex 0.164.0"), result("Logged in"),
                result("--ignore-user-config --ignore-rules --output-schema --output-last-message --ephemeral"),
                result('{"models":[]}')]
        with patch.object(sh, "which", side_effect=lambda n: n), patch.object(sh, "run", side_effect=good), \
                patch.object(process, "run", return_value=result("ouro-sandbox-ready")), \
                patch.object(codex, "network_readiness", side_effect=sh.DriverError("network refused")) as network, \
                patch.object(codex, "execute") as model:
            with self.assertRaisesRegex(sh.DriverError, "network refused"):
                codex.preflight(developer.Settings("codex"))
            network.assert_called_once()
            model.assert_not_called()


    def test_codex_preflight_checks_auth_models_and_sandbox(self):

        def result(out='', code=0):
            return sh.R(code, out, '', [])
        flags = '--ignore-user-config --ignore-rules --output-schema --output-last-message --ephemeral'
        good = [result('codex-cli 0.163.0'), result('Logged in using ChatGPT'), result(flags), result(json.dumps({'models': [{'slug': 'gpt-example'}]})), result()]
        proof = {'tool_calls': [{'tool': 'read', 'path': './readiness.txt', 'error': None}]}
        with patch.object(codex, 'network_readiness'), patch.object(sh, 'which', side_effect=lambda name: name), patch.object(sh, 'run', side_effect=good), patch.object(process, 'run', return_value=result('ouro-sandbox-ready')), patch.object(codex, 'execute', return_value=({'finding': 'ouro-ready'}, 't', proof, None)):
            codex.preflight(developer.Settings('codex', {'builder': 'gpt-example'}))
        with patch.object(codex, 'network_readiness'), patch.object(sh, 'which', side_effect=lambda name: name), patch.object(sh, 'run', side_effect=good), patch.object(process, 'run', return_value=result('ouro-sandbox-ready')), patch.object(codex, 'execute', return_value=({'finding': 'ouro-ready'}, 't', {'tool_calls': [{'tool': 'list', 'error': None}]}, None)):
            with self.assertRaisesRegex(sh.DriverError, 'file capability probe failed'):
                codex.preflight(developer.Settings('codex'))
        with patch.object(codex, 'network_readiness'), patch.object(sh, 'which', side_effect=lambda name: name), patch.object(sh, 'run', side_effect=good[:1] + [result('Not logged in', 1)]), patch.object(process, 'run') as launch:
            with self.assertRaisesRegex(sh.DriverError, 'authenticated'):
                codex.preflight(developer.Settings('codex'))
            launch.assert_not_called()
        with patch.object(codex, 'network_readiness'), patch.object(sh, 'which', side_effect=lambda name: name), patch.object(sh, 'run', side_effect=good), patch.object(process, 'run') as launch:
            with self.assertRaisesRegex(sh.DriverError, 'does not serve'):
                codex.preflight(developer.Settings('codex', {'builder': 'unavailable'}))
            launch.assert_not_called()

    def test_unsupported_sandbox_refuses_before_model_probe(self):
        result = lambda out, code=0: sh.R(code, out, 'unsupported sandbox interface', [])
        good = [result('codex 0.163.0'), result('Logged in'), result('--ignore-user-config --ignore-rules --output-schema --output-last-message --ephemeral'), result('{"models":[]}')]
        with patch.object(sh, 'which', side_effect=lambda name: name), patch.object(sh, 'run', side_effect=good), patch.object(process, 'run', return_value=result('', 101)), patch.object(codex, 'execute') as model:
            with self.assertRaisesRegex(sh.DriverError, 'OS sandbox unavailable'):
                codex.preflight(developer.Settings('codex'))
            model.assert_not_called()


    @reap_fixture_children
    def test_process_timeout_kills_child_tree(self):
        import time
        marker = self.root / "escaped.txt"
        ready = self.root / "child-started.txt"
        child = f"import time, pathlib; pathlib.Path({str(ready)!r}).write_text('started'); time.sleep(6); pathlib.Path({str(marker)!r}).write_text('escaped')"
        parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True); time.sleep(30)"
        with self.assertRaisesRegex(sh.DriverError, "timed out"):
            process.run([sys.executable, "-c", parent], timeout=5)
        self.assertTrue(ready.exists(), "cleanup proof requires the escaped child to have actually started")
        time.sleep(6.1)
        self.assertFalse(marker.exists())
        self.assertEqual(process.run([sys.executable, "-c", "print('synthetic')"]).out.strip(), "synthetic")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc completion interleaving")
    @reap_fixture_children
    def test_validation_completion_snapshot_distinguishes_waited_and_live_children(self):
        import time
        from ouro import validation
        try:
            validation.check_platform()
        except sh.DriverError as ex:
            self.skipTest(str(ex))
        for mode in ("sync", "async"):
            work = self.root / mode
            work.mkdir()
            (work / "child.py").write_text(
                "import os,pathlib,time\npathlib.Path('child.pid').write_text(str(os.getpid()))\n"
                "while not pathlib.Path('sampled').exists(): time.sleep(0.002)\n"
                + ("time.sleep(1); pathlib.Path('escaped').write_text('escaped')\n" if mode == "async" else ""))
            (work / "parent.py").write_text(
                "import os,pathlib,subprocess,sys,time\npathlib.Path('parent.pid').write_text(str(os.getpid()))\n"
                "child=subprocess.Popen([sys.executable,'child.py'])\n"
                + ("while not pathlib.Path('sampled').exists(): time.sleep(0.002)\n" if mode == "async" else "child.wait()\n"))
            original, delayed = validation.process_table, False
            def snapshot(env=None):
                nonlocal delayed
                table = original(env)
                try:
                    child = int((work / "child.pid").read_text())
                    parent = int((work / "parent.pid").read_text())
                except (FileNotFoundError, ValueError):
                    return table
                if not delayed and child in table and parent in table:
                    delayed = True
                    (work / "sampled").write_text("observed real parent and child")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        try:
                            if (pathlib.Path('/proc') / str(parent) / 'stat').read_text().split()[2] == 'Z':
                                break
                        except FileNotFoundError:
                            break
                        time.sleep(0.002)
                    else:
                        self.fail("generated parent did not finish within the control bound")
                return table
            with patch.object(validation, "process_table", side_effect=snapshot):
                code = validation.execute('"' + sys.executable + '" parent.py', work, work / "output.log")
            self.assertTrue(delayed, "the snapshot must precede actual parent completion")
            self.assertEqual(code, 0 if mode == "sync" else 1, (work / "output.log").read_text())
            if mode == "async":
                time.sleep(1.1)
                self.assertFalse((work / "escaped").exists(), "genuine live descendants must be reaped")

    @unittest.skipUnless(os.name == "nt", "Windows job-assignment failure")
    def test_process_refuses_failed_containment_without_hanging(self):
        marker = self.root / "assignment-reached.txt"
        script = (
            "import ctypes, pathlib, sys\n"
            "from ouro.runtime import process\n"
            "original = ctypes.WinDLL\n"
            "def dll(name, *args, **kwargs):\n"
            "    kernel = original(name, *args, **kwargs)\n"
            "    if name == 'kernel32':\n"
            "        def refuse(*args):\n"
            f"            pathlib.Path({str(marker)!r}).write_text('reached')\n"
            "            return 0\n"
            "        kernel.AssignProcessToJobObject = refuse\n"
            "    return kernel\n"
            "ctypes.WinDLL = dll\n"
            "try:\n"
            "    process.run([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "except process.DriverError as ex:\n"
            "    assert 'containment unavailable' in str(ex), str(ex)\n"
            "    print('setup refused without hanging')\n"
            "else:\n"
            "    raise AssertionError('failed containment was admitted')\n")
        result = process.run([sys.executable, "-c", script], timeout=5, env={"PYTHONPATH": str(BASE / "bin")})
        self.assertTrue(marker.exists(), "failure injection must reach actual job assignment")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("setup refused without hanging", result.out)

    def test_installer_preserves_configuration_and_unowned_files(self):
        spec = importlib.util.spec_from_file_location("ouro_setup", BASE / "bin" / "ouro-developer.py")
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        args = ["--home", str(self.root)]
        self.assertEqual(setup.main(["install", *args, "--backend", "codex"]), 0)
        config = self.root / ".config" / "ouro" / "developer.toml"
        config.write_text('[runtime]\nbackend="claude"\n')
        self.assertEqual(setup.main(["install", *args]), 0)
        self.assertEqual(developer.load(config).backend, "claude")
        owned = self.root / ".agents" / "skills" / "ouro-run" / "SKILL.md"
        saved = owned.read_bytes()
        owned.write_text("personal edits")
        self.assertEqual(setup.main(["install", *args]), 1)
        self.assertEqual(setup.main(["uninstall", *args]), 1)
        owned.write_bytes(saved)
        self.assertEqual(setup.main(["uninstall", *args]), 0)
        self.assertTrue(config.is_file())
        owned.write_text("unowned")
        self.assertEqual(setup.main(["install", *args]), 1)
        self.assertEqual(owned.read_text(), "unowned")

    def test_installer_refuses_aliased_or_unowned_manifest(self):
        spec = importlib.util.spec_from_file_location("ouro_setup", BASE / "bin" / "ouro-developer.py")
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        manifest = self.root / ".config/ouro/installation.json"
        manifest.parent.mkdir(parents=True)
        unrelated = self.root / "personal.json"
        original = '{"source":"fixture","files":{}}'
        unrelated.write_text(original)
        os.link(unrelated, manifest)
        self.assertEqual(setup.main(["install", "--home", str(self.root)]), 1)
        self.assertEqual(unrelated.read_text(), original)
        self.assertFalse(manifest.with_name("developer.toml").exists())
        manifest.unlink()
        manifest.write_text('{"files":{},"personal":"preserve"}')
        self.assertEqual(setup.main(["install", "--home", str(self.root)]), 1)
        self.assertEqual(manifest.read_text(), '{"files":{},"personal":"preserve"}')

    def test_denied_configuration_alias_keeps_manual_handoff(self):
        from ouro.run import Run
        runner = object.__new__(Run)
        runner.worktree = self.root
        runner.manual_paths = lambda: []
        bridge = Bridge({**self.policy, "edit": True})
        aliases = [".CODEX/config.toml"] + ([".codex./config.toml"] if os.name == "nt" else [])
        for alias in aliases:
            with self.assertRaises(ValueError):
                bridge.call("write", {"path": alias, "content": "blocked"})
            answer = {"manual": [{"path": alias, "edit": "owner edit"}],
                      "_denied": [{"tool_input": {"file_path": alias}}]}
            self.assertEqual(runner.manual_steps(answer), [{"path": ".codex/config.toml", "edit": "owner edit"}])
            runner.manual_paths = lambda: [alias]
            self.assertEqual(runner.manual_steps(answer), [{"path": ".codex/config.toml", "edit": "owner edit"}])
            runner.manual_paths = lambda: []
        self.assertIsNone(runner.protected_path(".CLAUDE/WORKTREES/peer/file"))
        self.assertIsNone(runner.protected_path("../.codex/config.toml"))

    def test_protected_lexical_path_keeps_manual_handoff(self):
        from ouro.run import Run
        config, target = self.root / ".codex", self.root / "normal-data"
        config.mkdir()
        target.mkdir()
        (target / "settings.toml").write_text("preserve")
        alias = config / "normal-alias"
        if os.name == "nt":
            script = self.root / "junction.ps1"
            script.write_text('param($Link,$Target)\n$ErrorActionPreference="Stop"\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n')
            subprocess.run(["pwsh", "-NoProfile", "-File", str(script), str(alias), str(target)], check=True)
            self.addCleanup(alias.rmdir)
        else:
            alias.symlink_to(target, target_is_directory=True)
            self.addCleanup(alias.unlink)
        self.assertEqual(alias.resolve(), target)
        bridge = Bridge({**self.policy, "edit": True})
        path = ".codex/normal-alias/settings.toml"
        with self.assertRaises(ValueError):
            bridge.call("write", {"path": path, "content": "blocked"})
        runner = object.__new__(Run)
        runner.worktree = self.root
        runner.manual_paths = lambda: []
        self.assertEqual(runner.manual_steps({"manual": [{"path": path, "edit": "owner edit"}],
                                             "_denied": [{"tool_input": {"file_path": path}}]}),
                         [{"path": path, "edit": "owner edit"}])
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            short = ctypes.create_unicode_buffer(32768)
            size = kernel.GetShortPathNameW(str(self.root), short, len(short))
            if size and pathlib.Path(short.value) != self.root:
                self.assertEqual(runner.protected_path(str(pathlib.Path(short.value) / path)), path)
        bridge.call("write", {"path": "normal-data/control.txt", "content": "allowed"})
        self.assertEqual((target / "settings.toml").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main(verbosity=2)
