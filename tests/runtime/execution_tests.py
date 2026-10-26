"""Real command observation and final-input receipt controls."""
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
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
            if os.name == "nt":
                self.assertIsNone(execution.fingerprint(root), "unsupported held reads cannot provide Windows proof")
            else:
                self.assertNotEqual(inputs, execution.fingerprint(root))
            with patch.object(sys, "argv", [str(helper), "run", str(policy), "0"]), \
                    patch.object(observed.process, "run", side_effect=sh.DriverError("execution unavailable")), \
                    patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(observed.main(), 1)
            row = json.loads(evidence.read_text().splitlines()[-1])
            self.assertIsNone(row["exit"])
            self.assertIsNone(row["inputs"])
            self.assertEqual(row["error"], "execution unavailable")

    @unittest.skipUnless(os.name == "posix", "POSIX held-file fingerprint positive control")
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

    @unittest.skipUnless(os.name == "posix", "POSIX held-file fingerprint positive control")
    def test_receipts_bind_every_input_and_head_and_reject_corruption(self):
        with tempfile.TemporaryDirectory(prefix="ouro-proof-inputs-") as temporary:
            root = pathlib.Path(temporary)
            self.assertEqual(sh.run(["git", "init", "-q", str(root)]).code, 0)
            (root / ".gitignore").write_text("output/\n")
            source = root / "café.py"
            source.write_text("input")
            private = root / ".git"
            run = types.SimpleNamespace(worktree=root, head=lambda: "a" * 40,
                wt_git=lambda *args: types.SimpleNamespace(out=str(private)), execution_grants=[])
            def retain(code=0):
                execution.retain(run, [{"tool": "run", "command": "approved", "exit": code,
                                        "inputs": execution.fingerprint(root)}])
            retain()
            self.assertEqual(execution.proven(run), {"approved"})
            (root / "output").mkdir()
            (root / "output" / "ignored").write_text("output")
            self.assertEqual(execution.proven(run), {"approved"})
            source.write_text("changed")
            self.assertEqual(execution.proven(run), set())
            retain()
            extra = root / "untracked"
            extra.write_text("new")
            self.assertEqual(execution.proven(run), set())
            extra.unlink()
            retain(9)
            self.assertEqual(execution.proven(run), set(), "a later failed attempt invalidates success")
            retain()
            execution.retain(run, [{"tool": "run", "command": "approved", "exit": 9, "inputs": None}])
            self.assertEqual(execution.proven(run), set(), "a later attempt with changed inputs invalidates success")
            retain()
            run.head = lambda: "b" * 40
            self.assertEqual(execution.proven(run), set())
            run.head = lambda: "a" * 40
            proof = execution.directory(run) / (run.head() + ".json")
            value = json.loads(proof.read_text())
            value["results"][0]["command"] = "forged"
            proof.write_text(json.dumps(value))
            self.assertEqual(execution.proven(run), set(), "valid JSON edits also corrupt the receipt")
            if os.name != "nt":
                retain()
                source.chmod(0o755)
                self.assertEqual(execution.proven(run), set())

    @unittest.skipIf(os.name == "posix", "unsupported-platform negative control")
    def test_unsupported_fingerprint_never_reads_or_proves(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            with patch.object(execution, "snapshot") as reader:
                self.assertIsNone(execution.fingerprint(root))
            reader.assert_not_called()


@unittest.skipUnless(os.name == "posix", "actual relative no-follow descriptor controls require POSIX")
class FingerprintTests(unittest.TestCase):
    @staticmethod
    def inventory(names, index=""):
        def query(*args):
            if args[0] == "rev-parse":
                return "a" * 40 + "\n"
            return index if "--stage" in args else "\0".join(names) + "\0"
        return query

    def test_streams_binary_and_stable_links_without_following_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "input").write_bytes(bytes(range(256)) * 300)
            (root / "link").symlink_to("missing-target")
            query = self.inventory(["input", "link"])
            first = execution.fingerprint(root, query)
            self.assertIsNotNone(first)
            self.assertEqual(first, execution.fingerprint(root, query))
            (root / "missing-target").write_text("not an inventoried input")
            self.assertEqual(first, execution.fingerprint(root, query))

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
                        with open(path, "wb") as stream:stream.write(b"before")
                        self.assertIsNone(execution.fingerprint(root))
                        with open(path, "wb") as stream:stream.write(b"after")
                        self.assertIsNone(execution.fingerprint(root))
                    finally:
                        os.unlink(path)
            # Custom transports must also reject text that cannot encode losslessly.
            self.assertIsNone(execution.fingerprint(root, self.inventory(["surrogate-\udcff"])))

    def test_leaf_replacement_never_reads_symlink_target_or_fifo(self):
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary);path = root / "input"
                path.write_text("original")
                outside = root / "outside";outside.write_text("must not read")
                original = execution.Directory.file
                def replace(directory, name):
                    path.unlink()
                    if kind == "fifo":os.mkfifo(path)
                    else:path.symlink_to(outside)
                    return original(directory, name)
                answer = [];errors = []
                def observe():
                    try:answer.append(execution.fingerprint(root, self.inventory(["input"])))
                    except BaseException as ex:errors.append(ex)
                with patch.object(execution.Directory, "file", replace), patch.object(execution.os, "read", wraps=os.read) as read:
                    thread = threading.Thread(target=observe, daemon=True);thread.start();thread.join(1)
                    blocked = thread.is_alive()
                    if blocked and kind == "fifo":
                        release = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                        os.close(release);thread.join(1)
                    self.assertFalse(blocked, "FIFO replacement must not block the snapshot")
                    self.assertEqual(errors, [])
                    self.assertEqual(answer, [None])
                    read.assert_not_called()

    def test_replaced_parent_and_changed_held_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary);(root / "dir").mkdir();(root / "dir/input").write_text("old")
            original = execution.directory_fd
            def replace(stack, parent, name):
                held = original(stack, parent, name)
                (root / "dir").rename(root / "old-dir");(root / "dir").mkdir();(root / "dir/input").write_text("new")
                return held
            with patch.object(execution, "directory_fd", replace):
                self.assertIsNone(execution.fingerprint(root, self.inventory(["dir/input"])))
            real_read = os.read;changed = False
            def mutate(fd, amount):
                nonlocal changed
                data = real_read(fd, amount)
                if not changed:
                    changed = True;(root / "dir/input").write_text("changed while held")
                return data
            with patch.object(execution.os, "read", mutate):
                self.assertIsNone(execution.fingerprint(root, self.inventory(["dir/input"])))

    def test_resource_and_inventory_budgets_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary);(root / "one").write_bytes(b"x" * 100);(root / "two").write_bytes(b"y" * 100)
            query = self.inventory(["one", "two"])
            self.assertIsNotNone(execution.fingerprint(root, query))
            for setting, limit in [("MAX_FILE", 99), ("MAX_TOTAL", 200), ("MAX_ENTRIES", 1), ("INVENTORY_LIMIT", 10), ("SNAPSHOT_SECONDS", 0)]:
                with self.subTest(setting=setting), patch.object(execution, setting, limit):
                    self.assertIsNone(execution.fingerprint(root, query))
            calls = 0
            def changing(*args):
                nonlocal calls
                calls += 1
                return query(*args) if calls <= 3 else query(*args) + "changed"
            self.assertIsNone(execution.fingerprint(root, changing))

    def test_submodules_share_count_depth_and_byte_budgets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary);(root / "dep").mkdir();(root / "dep/.git").write_text("gitdir: fixture")
            (root / "dep/input").write_text("submodule data")
            def query(*args):
                if args[0] == "-C":return self.inventory(["input"])(*args[2:])
                return self.inventory(["dep"], "160000 " + "b" * 40 + " 0\tdep\0")(*args)
            self.assertIsNotNone(execution.fingerprint(root, query))
            with patch.object(execution, "MAX_DEPTH", 0):self.assertIsNone(execution.fingerprint(root, query))
            with patch.object(execution, "MAX_ENTRIES", 1):self.assertIsNone(execution.fingerprint(root, query))
