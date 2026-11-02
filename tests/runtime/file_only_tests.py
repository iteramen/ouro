"""Explicit READ admission must not become a command-worker capability proof."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ouro import developer, node, run, sh
from ouro.runtime import Capabilities, READ, PROBE, builder, codex, diagnostics, process
from ouro.runtime.tools import Bridge


class FileOnlyReadinessTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="ouro-file-only-test-")
        self.addCleanup(folder.cleanup)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        self.root = Path(folder.name)
        self.cfg = developer.Settings("codex")

    def metadata(self):
        return [sh.R(0, text, "", []) for text in (
            "codex 0.164.0", "Logged in", "--ignore-user-config --ignore-rules --output-schema --output-last-message --ephemeral",
            '{"models":[]}')]

    def model_read(self, name, prompt, model, tools, cwd, *args):
        self.assertEqual(tools, READ)
        self.assertEqual((Path(cwd) / "readiness.txt").read_text(), "ouro-ready")
        return {"finding": "ouro-ready"}, "fixture", {"tool_calls": [
            {"tool": "read", "path": "readiness.txt", "error": None}]}, None

    def test_explicit_read_keeps_real_model_read_and_skips_unused_command_controls(self):
        with patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                patch.object(sh, "run", side_effect=self.metadata()) as metadata, \
                patch.object(process, "run", side_effect=AssertionError("unexpected command execution")) as command, patch.object(codex, "network_readiness") as network, \
                patch.object(codex, "git_readiness") as git, patch.object(codex, "execute", side_effect=self.model_read) as model:
            proofs = codex.preflight(self.cfg, capabilities=READ)
        self.assertEqual(len(proofs), 1)
        self.assertEqual(metadata.call_count, 4)
        model.assert_called_once()
        command.assert_not_called()
        network.assert_not_called()
        git.assert_not_called()

    def test_read_still_requires_auth_model_interface_and_genuine_read(self):
        for index, replacement, message in ((0, "codex 0.162.0", "0.163.0"), (1, "Not logged in", "authenticated"),
                                             (2, "--ephemeral", "missing --ignore-user-config"), (3, "not JSON", "catalog unavailable")):
            results = self.metadata()
            results[index] = sh.R(0, replacement, "", [])
            with self.subTest(message=message), patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                    patch.object(sh, "run", side_effect=results), patch.object(codex, "execute") as model:
                with self.assertRaisesRegex(sh.DriverError, message):
                    codex.preflight(self.cfg, capabilities=READ)
                model.assert_not_called()
        with patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                patch.object(sh, "run", side_effect=self.metadata()), patch.object(codex, "execute") as model:
            with self.assertRaisesRegex(sh.DriverError, "does not serve"):
                codex.preflight(developer.Settings("codex", {"builder": "unavailable"}), capabilities=READ)
            model.assert_not_called()
        for proof in ({"tool_calls": []}, {"tool_calls": [{"tool": "read", "path": "readiness.txt", "error": "denied"}]}):
            with self.subTest(proof=proof), patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                    patch.object(sh, "run", side_effect=self.metadata()), \
                    patch.object(process, "run", side_effect=AssertionError("unexpected command execution")), \
                    patch.object(codex, "execute", return_value=({"finding": "ouro-ready"}, "t", proof, None)):
                with self.assertRaisesRegex(sh.DriverError, "file capability probe failed"):
                    codex.preflight(self.cfg, capabilities=READ)
        with patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                patch.object(sh, "run", side_effect=self.metadata()), patch.object(Bridge, "tools", return_value=[{"name": "run"}]):
            with self.assertRaisesRegex(sh.DriverError, "file-only capability contract unavailable"):
                codex.preflight(self.cfg, capabilities=READ)

    def probe(self, answer, calls, setup=None):
        def model(name, prompt, model, tools, cwd, *args):
            if setup:
                setup(Path(cwd))
            with diagnostics.attempt(name, "codex", model) as row:
                row["session"] = "fixture-session"
            return answer, "fixture-session", {"tool_calls": calls}, None
        with patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                patch.object(sh, "run", side_effect=self.metadata()), \
                patch.object(process, "run", side_effect=AssertionError("unexpected command execution")), \
                patch.object(codex, "execute", side_effect=model):
            try:
                codex.preflight(self.cfg, capabilities=READ)
                return None, diagnostics.ATTEMPTS[-1]
            except sh.DriverError as ex:
                return str(ex), diagnostics.ATTEMPTS[-1]

    def test_rejected_file_proof_retains_classes_and_booleans_bound_to_attempt_identity(self):
        def read(path, error=None):
            return {"tool": "read", "path": path, "error": error}
        listing, ok = {"tool": "list", "path": None, "error": None}, {"finding": "ouro-ready"}
        stop = {"finding": "ouro-ready", "stop": {"reason": "open decision", "question": "private-question"}}
        def other(cwd):
            (cwd / "private-dir").mkdir()
            (cwd / "private-dir/other.txt").write_text("ouro-ready")
        def link(cwd):
            os.link(cwd / "readiness.txt", cwd / "private-link.txt")
        case = {}
        def variant(cwd):  # a case-sensitive host holds a distinct file under the variant name
            try:
                with open(cwd / "READINESS.TXT", "x") as distinct:
                    distinct.write("something else")
                case["served"] = None
            except FileExistsError:  # resolve() may return the on-disk case: the file itself
                case["served"] = "exact" if (cwd / "READINESS.TXT").resolve() == (cwd / "readiness.txt").resolve() else "alias"
        for name, answer, calls, setup, expected in (
                ("pass", ok, [listing, read("readiness.txt")], None,
                 dict(proof="accepted", content_match=True, read_observed=True, read_path="exact", tools="list+read")),
                ("pass ./ spelling", ok, [read("./readiness.txt")], None, dict(proof="accepted", read_path="exact", tools="read")),
                ("line-number echo", {"finding": "1: ouro-ready"}, [listing, read("readiness.txt")], None,
                 dict(proof="content", content_match=False, content="numbered", read_observed=True, read_path="exact")),
                ("trailing newline", {"finding": "ouro-ready\n"}, [read("readiness.txt")], None,
                 dict(proof="content", content="whitespace", read_observed=True)),
                ("other content", {"finding": "private-answer"}, [read("readiness.txt")], None, dict(proof="content", content="other")),
                ("no read call", ok, [listing], None,
                 dict(proof="missing_read", content_match=True, read_observed=False, read_path="absent", read_failed=False, tools="list")),
                ("failed read", ok, [listing, read("readiness.txt", "private-error")], None,
                 dict(proof="failed_read", read_observed=False, read_path="absent", read_failed=True, tools="list")),
                ("another path", ok, [read("private-dir/other.txt")], other, dict(proof="wrong_read", read_path="mismatch")),
                ("hard-link alias", ok, [read("private-link.txt")], link, dict(proof="wrong_read", read_path="alias", read_observed=False)),
                ("stop with exact proof", stop, [read("readiness.txt")], None, dict(proof="accepted", stop_present=True)),
                ("stop without finding", {"stop": stop["stop"]}, [read("readiness.txt")], None,
                 dict(proof="content", content="absent", stop_present=True))):
            with self.subTest(name):
                diagnostics.ATTEMPTS.clear()
                error, row = self.probe(answer, calls, setup)
                self.assertEqual({k: row["file_proof"][k] for k in expected}, expected)
                self.assertEqual((row["session"], row["model"], row["outcome"]), ("fixture-session", "default", "adapter_returned"))
                self.assertEqual(row["file_proof"]["cli"], "0.164.0")
                self.assertEqual(row["file_proof"]["runtime"], hashlib.sha256(Path(codex.__file__).read_bytes()).hexdigest())
                if expected["proof"] == "accepted":
                    self.assertIsNone(error)
                else:
                    self.assertIn(f"file capability probe failed: {expected['proof']}", error)
                    self.assertIn("rerun the same command", error)
                retained = json.dumps(row) + diagnostics.table() + (error or "")
                for raw in ("private", "other.txt", "readiness.txt", "ouro-sandbox-probe-", "1: ouro"):
                    self.assertNotIn(raw, retained)
                self.assertIn("Preflight file proofs", diagnostics.table())
        error, row = self.probe(ok, [read("READINESS.TXT")], variant)
        if case["served"] == "exact":
            self.assertIsNone(error)
        else:
            self.assertEqual((row["file_proof"]["proof"], row["file_proof"]["read_path"]),
                             ("wrong_read", case["served"] or "mismatch"))

    def test_inspect_check_prints_the_rejected_file_proof_row(self):
        def rejected(cfg):
            with diagnostics.attempt("PREFLIGHT", "codex", "default") as row:
                row["session"] = "fixture-session"
            diagnostics.file_proof("fixture-session", "default", {k: "fixture-class" for k in (
                "proof", "content_match", "content", "read_observed", "read_path", "read_failed",
                "stop_present", "tools", "cli", "runtime")})
            raise sh.DriverError("file capability probe failed")
        spec = importlib.util.spec_from_file_location("file_proof_inspection", Path(node.__file__).parents[1] / "ouro-developer.py")
        inspector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inspector)
        config = self.root / ".config/ouro/developer.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[runtime]\nbackend="codex"\n')
        out = io.StringIO()
        with patch.object(node, "preflight", side_effect=rejected), redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(inspector.main(["inspect", "--check", "--home", str(self.root)]), 1)
        self.assertIn("Preflight file proofs", out.getvalue())
        self.assertIn("| default | fixture-session | fixture-class", out.getvalue())

    def test_windows_nonread_preflight_refuses_before_cli_or_probe_work(self):
        for caps in (None, PROBE, builder([]), Capabilities(edit=True),
                     Capabilities(commands=("python3 check.py",)), Capabilities(commands=("git status *",))):
            with self.subTest(caps=caps), patch.object(codex, "WINDOWS", True), \
                    patch.object(sh, "argv_for", side_effect=sh.DriverError("unexpected CLI resolution")) as resolve, patch.object(sh, "run") as metadata, \
                    patch.object(process, "run") as command, patch.object(codex, "network_readiness") as network, \
                    patch.object(codex, "git_readiness") as git, patch.object(codex, "execute") as model, \
                    patch.object(codex.tempfile, "TemporaryDirectory") as workspace:
                with self.assertRaisesRegex(sh.DriverError, "native Windows Codex supports file-only triage") as caught:
                    codex.preflight(self.cfg, capabilities=caps)
                for advice in ("--developer-config", "OURO_DEVELOPER_CONFIG", "Claude", "repository CI validation",
                               "native Linux Codex", "explicit caller-validation handoffs"):
                    self.assertIn(advice, str(caught.exception))
                for unused in (resolve, metadata, command, network, git, model, workspace):
                    unused.assert_not_called()

    @patch.object(codex, "WINDOWS", False)
    def test_default_and_every_nonread_contract_keep_network_and_git_checks(self):
        for caps in (None, PROBE, builder([]), Capabilities(edit=True), Capabilities(commands=("python3 check.py",))):
            with self.subTest(caps=caps), patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                    patch.object(sh, "run", side_effect=self.metadata()), \
                    patch.object(process, "run", return_value=sh.R(0, "ouro-sandbox-ready", "", [])) as startup, \
                    patch.object(codex, "network_readiness", side_effect=sh.DriverError("network control")) as network, \
                    patch.object(codex, "execute") as model:
                with self.assertRaisesRegex(sh.DriverError, "network control"):
                    codex.preflight(self.cfg, capabilities=caps)
                startup.assert_called_once()
                network.assert_called_once()
                model.assert_not_called()
        with patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                patch.object(sh, "run", side_effect=self.metadata() + [sh.R(0, "", "", [])]), \
                patch.object(process, "run", return_value=sh.R(0, "ouro-sandbox-ready", "", [])), \
                patch.object(codex, "network_readiness"), patch.object(codex, "git_readiness", side_effect=sh.DriverError("git control")), \
                patch.object(codex, "execute") as model:
            with self.assertRaisesRegex(sh.DriverError, "git control"):
                codex.preflight(self.cfg)
            model.assert_not_called()

    def test_capability_types_refuse_before_preflight_or_dispatch(self):
        for value in ([], {}, "READ", False):
            with self.subTest(value=value), patch.object(codex, "WINDOWS", True), patch.object(sh, "run") as metadata, patch.object(process, "run") as command:
                with self.assertRaisesRegex(sh.DriverError, "capability contract"):
                    node.preflight(self.cfg, capabilities=value)
                with self.assertRaisesRegex(sh.DriverError, "capability contract"):
                    codex.preflight(self.cfg, capabilities=value)
                with self.assertRaisesRegex(sh.DriverError, "capability contract"):
                    codex.execute("TRIAGE", "p", "default", value, self.root, 1, None, {}, self.cfg)
                metadata.assert_not_called()
                command.assert_not_called()

    def test_windows_read_dispatch_closes_ambient_tools_and_exposes_only_file_bridge(self):
        def launch(argv, **kwargs):
            for option in ("features.code_mode.enabled=false", "features.shell_tool=false",
                           "features.apps=false", "features.plugins=false", "features.multi_agent=false", 'web_search="disabled"'):
                self.assertIn(option, argv)
            self.assertFalse(any(arg.startswith("features.code_mode_host=") for arg in argv))
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            output = Path(argv[argv.index("--output-last-message") + 1])
            policy = json.loads(output.with_name("policy.json").read_text())
            bridge = Bridge(policy)
            self.assertEqual({tool["name"] for tool in bridge.tools()}, {"read", "list", "search"})
            for tool, args in (("run", {"command": "python3 check.py"}), ("write", {"path": "blocked", "content": "x"}),
                               ("edit", {}), ("delete", {})):
                with self.assertRaisesRegex(ValueError, "tool is not granted"):
                    bridge.call(tool, args)
            output.write_text(json.dumps({"finding": "fixture", "stop": None}))
            output.with_name("tools.jsonl").write_text(json.dumps({"tool": "list", "error": None}) + "\n")
            return sh.R(0, '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed"}', "", argv)
        with patch.object(codex, "WINDOWS", True), patch.object(codex, "git_readiness") as git, \
                patch.object(sh, "which", side_effect=lambda name: name), patch.object(process, "run", side_effect=launch):
            result = codex.execute("CHECKPOINT", "p", "default", READ, self.root, 1, None, {}, self.cfg)
            self.assertEqual(result[0], {"finding": "fixture"})
            git.assert_not_called()

    def test_windows_read_success_never_admits_later_commands_probe_or_edits(self):
        with patch.object(codex, "WINDOWS", True), patch.object(sh, "argv_for", side_effect=lambda *args: list(args)), \
                patch.object(sh, "run", side_effect=self.metadata()), patch.object(codex, "execute", side_effect=self.model_read), \
                patch.object(process, "run", side_effect=AssertionError("unexpected command execution")):
            codex.preflight(self.cfg, capabilities=READ)
        for caps in (PROBE, builder([]), Capabilities(edit=True), Capabilities(commands=("python3 check.py",)),
                     Capabilities(commands=("git status *",))):
            with self.subTest(caps=caps), patch.object(codex, "WINDOWS", True), \
                    patch.object(codex, "git_readiness") as git, patch.object(process, "run", side_effect=AssertionError("unexpected command execution")) as command:
                with self.assertRaisesRegex(sh.DriverError, "native Windows supports file-only capabilities"):
                    codex.execute("BUILD", "p", "default", caps, self.root, 1, None, {}, self.cfg)
                git.assert_not_called()
                command.assert_not_called()

    def test_capable_platform_command_dispatch_retains_git_boundary(self):
        with patch.object(codex, "WINDOWS", False), patch.object(codex, "git_readiness", side_effect=sh.DriverError("git control")) as git, \
                patch.object(process, "run") as command:
            with self.assertRaisesRegex(sh.DriverError, "git control"):
                codex.execute("BUILD", "p", "default", builder([]), self.root, 1, None, {}, self.cfg)
            git.assert_called_once()
            command.assert_not_called()

    def test_shared_preflight_propagates_scope_and_delivery_keeps_full_readiness(self):
        with patch.dict(os.environ, {"OURO_NODE_STUB": ""}), patch.object(codex, "preflight", return_value=[]) as preflight:
            node.preflight(self.cfg, capabilities=READ)
            preflight.assert_called_once_with(self.cfg, capabilities=READ)
            preflight.reset_mock()
            node.preflight(self.cfg)
            preflight.assert_called_once_with(self.cfg, capabilities=None)
        with patch.dict(os.environ, {"OURO_NODE_STUB": ""}), patch("ouro.runtime.claude.preflight") as preflight:
            cfg = developer.Settings("claude")
            node.preflight(cfg, capabilities=READ)
            preflight.assert_called_once_with(cfg)
        with patch.object(node, "preflight") as preflight:
            runner = object.__new__(run.Run)
            runner.env = SimpleNamespace(runtime=self.cfg, dry=False)
            runner.preflight()
            preflight.assert_called_once_with(self.cfg)
            preflight.reset_mock()
            path = Path(node.__file__).parents[1] / "ouro-developer.py"
            spec = importlib.util.spec_from_file_location("file_only_inspection", path)
            inspector = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(inspector)
            config = self.root / ".config/ouro/developer.toml"
            config.parent.mkdir(parents=True)
            config.write_text('[runtime]\nbackend="codex"\n')
            self.assertEqual(inspector.main(["inspect", "--check", "--home", str(self.root)]), 0)
            self.assertEqual(preflight.call_args.kwargs, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
