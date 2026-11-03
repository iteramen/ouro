"""Backend interface conformance: every registered backend implements each member or declares a marker."""
import dataclasses
import json
import pathlib
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from ouro import developer, node, sh
from ouro.runtime import diagnostics
from ouro.runtime.backends import BACKENDS, CONFINED, NOT_REQUIRED, UNCONFINED, UNSUPPORTED, Interface

MARKERS = {"worktree_readiness": {NOT_REQUIRED}, "validate_answer": {UNSUPPORTED},
           "probe_confinement": {CONFINED, UNCONFINED}}
# Today's matrix; "implemented" means a callable. A new backend or member must be added here.
MATRIX = {
    "claude": {"preflight": "implemented", "worktree_readiness": NOT_REQUIRED, "execute": "implemented",
               "probe_confinement": UNCONFINED, "validate_answer": UNSUPPORTED},
    "codex": {"preflight": "implemented", "worktree_readiness": "implemented", "execute": "implemented",
              "probe_confinement": CONFINED, "validate_answer": "implemented"},
}


class BackendInterfaceTests(unittest.TestCase):
    def test_every_backend_implements_or_declares_every_member(self):
        seen = {}
        for name, backend in BACKENDS.items():
            interface = backend.interface
            self.assertIsInstance(interface, Interface)
            row = seen[name] = {}
            for member in (f.name for f in dataclasses.fields(Interface)):
                value = getattr(interface, member)
                if member == "probe_confinement":
                    self.assertIn(value, MARKERS[member], (name, member))
                    row[member] = value
                elif callable(value):
                    row[member] = "implemented"
                else:
                    self.assertIn(value, MARKERS.get(member, set()), (name, member))
                    row[member] = value
        self.assertEqual(seen, MATRIX)

    def test_binding_errors_keep_their_text(self):
        cases = {'[runtime]\nbackend="unknown"': "runtime.backend must be claude or codex",
                 '[runtime]\nbackend="claude"\n[runtime.models]\nbuilder="gpt-example"':
                     "runtime.models.builder: Claude requires a supported alias",
                 '[runtime]\nbackend="codex"\n[runtime.models]\nbuilder="opus"':
                     "runtime.models.builder: Claude aliases cannot select Codex models",
                 '[runtime]\nbackend="claude"\neffort="low"':
                     "runtime.effort requires Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="codex"\neffort="none"':
                     "runtime.effort requires Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="codex"\n[runtime.models]\nbuilder=" x"':
                     "runtime.models.builder must be a non-empty model"}
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "developer.toml"
            for text, message in cases.items():
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(sh.DriverError) as caught:
                    developer.load(path)
                self.assertEqual(str(caught.exception), f"developer binding {path}: {message}")

    def test_a_claude_denial_reaches_the_shared_shape_and_the_codex_manual_step(self):
        from types import SimpleNamespace
        from ouro.run import Run
        from ouro.runtime import claude
        envelope = {"session_id": "s", "structured_output": {"subject": "x"}, "permission_denials": [
            {"tool_name": "Write", "tool_use_id": "t1", "tool_input": {"file_path": ".claude/rules/x.md", "content": "a rule"}},
            {"tool_name": "Edit", "tool_use_id": "t2", "tool_input": {"file_path": ".codex/config.toml", "old_string": "a",
                                                                      "new_string": "b", "replace_all": False}},
            {"tool_name": "Bash", "tool_use_id": "t3", "tool_input": {"command": "gh pr merge 1", "description": "merge"}}]}
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", return_value=sh.R(0, json.dumps(envelope), "", [])):
            answer = claude._execute("BUILD", "p", "opus", ["Read"], ".", 5, None, {}, developer.Settings())[0]
        codex_shape = [{"tool_name": "write", "tool_input": {"file_path": ".claude/rules/x.md", "content": "a rule"}},
                       {"tool_name": "edit", "tool_input": {"file_path": ".codex/config.toml", "old": "a", "new": "b"}},
                       {"tool_name": "run", "tool_input": {"command": "gh pr merge 1"}}]
        self.assertEqual(answer["_denied"], codex_shape)
        with tempfile.TemporaryDirectory() as d:
            runner = object.__new__(Run)
            runner.tree, runner.manual_paths = SimpleNamespace(path=pathlib.Path(d)), lambda: []
            steps = runner.manual_steps(answer)
            self.assertEqual(steps, runner.manual_steps({"_denied": codex_shape}))
        self.assertEqual(steps, [{"path": ".claude/rules/x.md", "edit": "a rule"},
                                 {"path": ".codex/config.toml", "edit": '{"old": "a", "new": "b"}'}])

    def test_node_opens_one_attempt_row_per_execution_for_every_backend(self):
        for name, backend in BACKENDS.items():
            calls = []

            def execute(*args):
                calls.append(args)
                args[-1]["session"] = "filled-by-backend"
                return {"finding": "x"}, "s", {}, None
            fake = dataclasses.replace(backend.interface, execute=execute)
            before, usage = len(diagnostics.ATTEMPTS), len(node.USAGE)
            with self.subTest(backend=name), patch.object(type(backend), "interface", new_callable=PropertyMock, return_value=fake):
                node._real("CHECKPOINT", "p", "m", [], ".", 1, None, developer.Settings(name))
            del node.USAGE[usage:]
            self.assertEqual(len(diagnostics.ATTEMPTS), before + 1)
            row = diagnostics.ATTEMPTS[-1]
            self.assertEqual((row["backend"], row["outcome"], row["session"]), (name, "adapter_returned", "filled-by-backend"))
            self.assertEqual(calls[0][7], node.SCHEMAS["CHECKPOINT"])
