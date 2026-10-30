"""Real command observation and final-input receipt controls."""
import errno
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from ouro import execution, sh
from ouro.runtime import observed, tools


class ExecutionTests(unittest.TestCase):
    def test_bridge_and_native_observe_real_commands(self):
        with tempfile.TemporaryDirectory(prefix="ouro-proof-test-") as temporary:
            directory = pathlib.Path(temporary)
            root = directory / "work"
            root.mkdir()
            self.assertEqual(sh.run(["git", "init", "-q", str(root)]).code, 0)
            script = root / "check.py"
            script.write_text("print('observed pass')\n", encoding="utf-8")
            command = sh.cmdline([sys.executable, "check.py"])
            bridge = tools.Bridge({"root": str(root), "edit": True, "commands": [command], "probe": False})
            with patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv):
                result = bridge.run(command)
            self.assertEqual(result["exit"], 0)
            self.assertIn("observed pass", result["stdout"])
            self.assertEqual(bridge.inputs, execution.fingerprint(root))
            inputs = bridge.inputs
            bridge.policy["commands"].append("git diff --check")
            with patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv):
                self.assertEqual(bridge.run("git diff --check")["exit"], 0)
            self.assertEqual(bridge.inputs, inputs, "an exact Git gate also provides execution evidence")
            script.write_text("from pathlib import Path\nPath('late').write_text('mutation')\n", encoding="utf-8")
            with patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv):
                self.assertEqual(bridge.run(command)["exit"], 0)
            self.assertIsNone(bridge.inputs, "a command that edits its inputs cannot prove them")
            script.write_text("raise SystemExit(9)\n", encoding="utf-8")
            policy = directory / "policy.json"
            evidence = directory / "commands.jsonl"
            policy.write_text(json.dumps({"root": str(root), "commands": [command], "evidence": str(evidence)}))
            helper = pathlib.Path(observed.__file__)
            event = {"tool_name": "Bash", "tool_input": {"command": command}}
            hook = sh.run([sys.executable, str(helper), "hook", str(policy)], input_text=json.dumps(event))
            updated = json.loads(hook.out)["hookSpecificOutput"]
            self.assertEqual(updated["permissionDecision"], "allow")
            self.assertIn("run", updated["updatedInput"]["command"])
            denied = sh.run([sys.executable, str(helper), "hook", str(policy)], input_text=json.dumps({
                "tool_name": "Bash", "tool_input": {"command": command + " --unapproved"}}))
            self.assertEqual(denied.out, "")
            for code in (9, 0):
                script.write_text(f"raise SystemExit({code})\n", encoding="utf-8")
                run = sh.run([sys.executable, str(helper), "run", str(policy), "0"])
                row = json.loads(evidence.read_text().splitlines()[-1])
                self.assertEqual(run.code, code)
                self.assertEqual(row["exit"], code)
                self.assertEqual(row["inputs"], execution.fingerprint(root))
            self.assertNotEqual(inputs, execution.fingerprint(root))
            with patch.object(sys, "argv", [str(helper), "run", str(policy), "0"]), \
                    patch.object(observed.process, "run", side_effect=sh.DriverError("execution unavailable")), \
                    patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(observed.main(), 1)
            row = json.loads(evidence.read_text().splitlines()[-1])
            self.assertIsNone(row["exit"])
            self.assertIsNone(row["inputs"])
            self.assertEqual(row["error"], "execution unavailable")

    def test_git_state_and_submodules_are_execution_inputs(self):
        with tempfile.TemporaryDirectory(prefix="ouro-proof-git-") as temporary:
            root = pathlib.Path(temporary) / "root"
            dependency = pathlib.Path(temporary) / "dependency"
            def git(where, *args):
                result = sh.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                 *args], cwd=where)
                self.assertEqual(result.code, 0, result.err)
                return result.out
            root.mkdir()
            dependency.mkdir()
            for repo in (root, dependency):
                git(repo, "init", "-q")
                (repo / "input").write_text("original", encoding="utf-8")
                git(repo, "add", "input")
                git(repo, "commit", "-qm", "initial")
            original = execution.fingerprint(root)
            self.assertIsNotNone(original)
            (root / "input").write_text("modified", encoding="utf-8")
            unstaged = execution.fingerprint(root)
            git(root, "add", "input")
            staged = execution.fingerprint(root)
            self.assertNotEqual(unstaged, staged, "staging changes Git-readable inputs")
            git(root, "commit", "-qm", "changed")
            self.assertNotEqual(staged, execution.fingerprint(root), "commit changes HEAD-readable inputs")
            git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(dependency), "dep")
            git(root, "commit", "-qm", "dependency")
            initialized = execution.fingerprint(root)
            self.assertIsNotNone(initialized, "a clean gitlink directory supports execution proof")
            (root / "dep" / "input").write_text("dirty dependency", encoding="utf-8")
            self.assertNotEqual(initialized, execution.fingerprint(root))
            (root / "dep" / "input").write_text("original", encoding="utf-8")
            git(root / "dep", "commit", "-q", "--allow-empty", "-m", "new dependency HEAD")
            self.assertNotEqual(initialized, execution.fingerprint(root))
            git(root, "submodule", "deinit", "-q", "-f", "--", "dep")
            self.assertIsNotNone(execution.fingerprint(root), "an uninitialized gitlink has a recorded identity")

    def test_receipts_bind_inputs_and_head_and_reject_corruption(self):
        with tempfile.TemporaryDirectory(prefix="ouro-proof-inputs-") as temporary:
            root = pathlib.Path(temporary)
            self.assertEqual(sh.run(["git", "init", "-q", str(root)]).code, 0)
            (root / ".gitignore").write_text("output/\n")
            source = root / "café.py"
            source.write_text("input")
            private = root / ".git"
            tree = types.SimpleNamespace(path=root, head=lambda: "a" * 40, admin=lambda: private,
                                         git=lambda *args, **kw: types.SimpleNamespace(out=""), env=types.SimpleNamespace(default="main"))
            def retain():
                execution.retain(tree, [], recheck=["approved"])
            retain()
            self.assertEqual(execution.candidates(tree), ["approved"])
            (root / "output").mkdir()
            (root / "output" / "ignored").write_text("output")
            self.assertEqual(execution.candidates(tree), ["approved"], "ignored output is not an input")
            source.write_text("changed")
            self.assertEqual(execution.candidates(tree), [], "an untracked file's bytes are inputs")
            retain()
            extra = root / "untracked"
            extra.write_text("new")
            self.assertEqual(execution.candidates(tree), [])
            extra.unlink()
            self.assertEqual(execution.candidates(tree), ["approved"])
            tree.head = lambda: "b" * 40
            self.assertEqual(execution.candidates(tree), [], "another head has its own receipt")
            tree.head = lambda: "a" * 40
            proof = execution.directory(tree) / (tree.head() + ".json")
            value = json.loads(proof.read_text())
            value["recheck"] = ["forged"]
            proof.write_text(json.dumps(value))
            self.assertEqual(execution.candidates(tree), [], "valid JSON edits also corrupt the receipt")
            if os.name != "nt":
                retain()
                source.chmod(0o755)
                self.assertEqual(execution.candidates(tree), [], "the exec bit is an input")

    def test_hidden_entries_and_nested_repositories_never_prove_a_mutating_command(self):
        with tempfile.TemporaryDirectory(prefix="ouro-proof-hidden-") as temporary:
            root = pathlib.Path(temporary)
            def git(*args, cwd=root):
                r = sh.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=cwd)
                self.assertEqual(r.code, 0, r.err)
            git("init", "-q")
            for name in ("hidden-a", "hidden-b"):
                (root / name).write_text("original")
            git("add", "-A"); git("commit", "-qm", "base")
            git("update-index", "--assume-unchanged", "hidden-a")
            git("update-index", "--skip-worktree", "hidden-b")
            for name in ("hidden-a", "hidden-b"):
                before = execution.fingerprint(root)
                (root / name).write_text("changed " + name)
                self.assertNotEqual(before, execution.fingerprint(root), name + " is read though Git hides its change")
            nested = root / "nested"
            nested.mkdir()
            git("init", "-q", cwd=nested)
            (nested / "inner").write_text("inside")
            self.assertIsNone(execution.fingerprint(root), "a nested repository's contents are not listed, so nothing is proved")
            many = root / "many"
            many.mkdir()
            for i in range(3000):
                (many / f"untracked-{i:04}.txt").write_text(str(i))
            (nested / ".git").rename(nested / "git-off")
            self.assertIsNotNone(execution.fingerprint(root), "thousands of untracked files still fit")

    @unittest.skipUnless(os.name == "posix", "byte file names Windows cannot create")
    def test_real_git_lossy_names_refuse_while_valid_utf8_stays_observable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.assertEqual(sh.run(["git", "init", "-q", str(root)]).code, 0)
            valid = root / "caf\u00e9.py"
            valid.write_bytes(b"before")
            first = execution.fingerprint(root)
            valid.write_bytes(b"after")
            second = execution.fingerprint(root)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            for name in (b"invalid-\xff.py", "literal-\ufffd.py".encode("utf-8")):
                with self.subTest(name=name):
                    path = os.fsencode(root) + b"/" + name
                    try:
                        stream = open(path, "wb")
                    except OSError as ex:
                        if ex.errno != errno.EILSEQ:
                            raise
                        self.skipTest(f"the filesystem refuses the name {name!r} (APFS): Git can never report it")
                    with stream:
                        stream.write(b"before")
                    try:
                        self.assertIsNone(execution.fingerprint(root))
                        with open(path, "wb") as stream:stream.write(b"after")
                        self.assertIsNone(execution.fingerprint(root))
                    finally:
                        os.unlink(path)
