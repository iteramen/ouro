"""Caller-validation refusal tests; WSL and other unsupported platforms are simulated rows."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from ouro import developer, sh, validation
from ouro.worktrees import Tree
from ouro.runtime import process as owned_process

HINT = str  # stands in for Run.wait_hint: the wait's text names its commands


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ouro-validation-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def fixture(self, scripts):
        def git(*args, cwd=None, check=True):
            p = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                               encoding="utf-8", check=True)
            return types.SimpleNamespace(out=p.stdout)
        git("init", "-q")
        (self.root / ".gitignore").write_text("output/\n", encoding="utf-8")
        for name, text in scripts.items():
            (self.root / name).write_text(text, encoding="utf-8")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        return Tree(types.SimpleNamespace(git=git, default="main", runtime=developer.Settings(),
                                          lookup=lambda key: "local" if key == "ship.validation" else None), self.root, None)

    def test_unsupported_wait_names_authority_commands_and_coverage_without_reading_proof(self):
        for authority in ("local", "ci"):
            with self.subTest(authority=authority):
                from ouro.run import Run
                run = types.SimpleNamespace(n=17, k=2,
                    env=types.SimpleNamespace(lookup=lambda key: authority),
                    required_execution=lambda: ["python3 selected.py"])
                hint = lambda commands=None: Run.wait_hint(run, commands)
                pending = [{"cmd": "python3 inherited.py", "reason": "caller"}]
                with patch.object(validation, "check_platform", side_effect=sh.DriverError("unsupported host")), \
                        patch.object(validation.sh, "say") as say, \
                        patch.object(validation, "receipt") as receipt, \
                        patch.object(validation.validation_lock, "Lease") as lease:
                    self.assertEqual(validation.validate(None, pending, hint), ([], 75))
                    message = say.call_args.args[0]
                    self.assertIn("authority=" + authority, message)
                    self.assertIn("python3 inherited.py", message)
                    self.assertNotIn("python3 selected.py", message)
                    self.assertIn('ship.validation = "ci"', message)
                    self.assertIn("exact gate.run", message)
                    self.assertIn("paths select", message)
                    self.assertIn("check names alone", message)
                    self.assertIn("local-only", message)
                    self.assertIn("ouro run 17 --part 2 --validate", message)
                    self.assertEqual(validation.check_inputs(None, "a" * 40, hint), 75)
                    self.assertIn("python3 selected.py", say.call_args.args[0])
                    receipt.assert_not_called()
                    lease.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "kernel platform detection")
    def test_platform_facts_ignore_environment_and_fail_closed(self):
        kernel = types.SimpleNamespace(sysname="Linux", release="6.1", version="native")
        with patch.object(validation.os, "uname", return_value=kernel), \
                patch.object(validation.Path, "read_text", return_value="native Linux"), \
                patch.object(validation.Path, "stat", side_effect=FileNotFoundError), \
                patch.dict(os.environ, {"WSL_INTEROP": "", "WSL_DISTRO_NAME": "",
                                        "WSLENV": "", "OURO_WSL": "0"}):
            validation.check_platform()
            for field, value in (("release", "4.4.0-Microsoft"),
                                 ("release", "6.6.87.2-microsoft-standard-WSL2"),
                                 ("version", "custom WSL kernel")):
                with self.subTest(field=field, value=value), \
                        patch.object(kernel, field, value), \
                        self.assertRaisesRegex(sh.DriverError, "native Linux host"):
                    validation.check_platform()
            with patch.object(validation.Path, "read_text", return_value="Microsoft"), \
                    self.assertRaisesRegex(sh.DriverError, "WSL"):
                validation.check_platform()
            for marker in ("/proc/sys/fs/binfmt_misc/WSLInterop", "/run/WSL"):
                def stat(path):
                    if str(path) != marker:
                        raise FileNotFoundError
                with self.subTest(marker=marker), \
                        patch.object(validation.Path, "stat", stat), \
                        self.assertRaisesRegex(sh.DriverError, "WSL"):
                    validation.check_platform()
            for operation in ("read_text", "stat"):
                with patch.object(validation.Path, operation, side_effect=PermissionError), \
                        self.assertRaisesRegex(sh.DriverError, "platform visibility unavailable"):
                    validation.check_platform()

    @unittest.skipUnless(os.name == "posix", "simulated WSL admission")
    def test_wsl_refuses_before_any_executable_lookup_or_launch(self):
        kernel = types.SimpleNamespace(sysname="Linux", release="Microsoft", version="WSL")
        with patch.object(validation.os, "uname", return_value=kernel), \
                patch.object(validation.Path, "read_text", return_value="Microsoft"), \
                patch.object(validation.Path, "stat", side_effect=FileNotFoundError), \
                patch.object(validation.shutil, "which") as lookup, \
                patch.object(validation.subprocess, "Popen") as launch, \
                patch.dict(os.environ, {}, clear=True):
            for cmd in ("powershell.exe -NoProfile", "python3 native-parent.py",
                        "WSL_INTEROP= WSL_DISTRO_NAME= python3 native-parent.py"):
                self.assertEqual(validation.execute(cmd, self.root, self.root / "attempt.log"), 75)
            lookup.assert_not_called()
            launch.assert_not_called()
        self.assertIn("including through native launchers", (self.root / "attempt.log").read_text())

    def driver_fixture(self, *, validating=True, script="raise SystemExit(75)\n"):
        from ouro.run import Run, RunOptions
        base = self.fixture({"native.py": script})
        pending = [{"cmd": '"' + sys.executable + '" native.py', "reason": "caller"}]
        base.git("update-ref", "refs/remotes/origin/main", "HEAD")
        base.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm",
                    "handoff\n\nOuro-Build: " + json.dumps({"validation_pending": pending}))
        run = Run.__new__(Run)
        run.tree = base
        run.options = RunOptions(validate=validating)
        run.env = types.SimpleNamespace(pr_checks=lambda: True,
                                        lookup=lambda key: "local" if key == "ship.validation" else None,
                                        default="main", runtime=developer.Settings())
        run.rederive, run.find_pr, run.records = lambda: None, lambda: None, lambda pr: []
        run.validation_commands = lambda: [p["cmd"] for p in pending]
        run.n, run.k = 1, None
        return run, pending

    def test_driver_refuses_unsupported_validation_before_preflight_dispatch(self):
        from ouro.run import Run
        run, _ = self.driver_fixture()
        with patch.object(validation, "check_platform", side_effect=sh.DriverError("unsupported platform")), \
                patch.object(validation, "clean") as clean, \
                patch.object(validation, "receipt") as receipt, \
                patch.object(validation.validation_lock, "Lease") as lease:
            # Real BUILD metadata parsing must not trigger receipt/state preflight.
            self.assertEqual(Run.drive(run, None), 0)
            clean.assert_not_called()
            receipt.assert_not_called()
            lease.assert_not_called()


    def test_driver_nonvalidating_wait_keeps_clean_input_barrier(self):
        from ouro.run import Run
        run, pending = self.driver_fixture(validating=False)
        with patch.object(validation, "validate", return_value=([], 75)) as validate:
            self.assertEqual(Run.drive(run, None), 0)
            validate.assert_called_once_with(run.tree, pending, run.wait_hint, reuse=True, execute_missing=False)
        with patch.object(validation, "validate", side_effect=sh.DriverError("dirty inputs")):
            with self.assertRaisesRegex(sh.DriverError, "dirty inputs"):
                Run.drive(run, None)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux lease component control")
    @patch.object(validation, "check_platform")
    def test_driver_real_metadata_defers_evidence_and_reuses_leased_receipt(self, platform):
        from ouro.run import Run
        # Simulated admission only; real Git, BUILD parsing, lease and receipt lookup.
        # Native Linux death/recovery controls remain separate obligations.
        run, pending = self.driver_fixture(script="print('completed')\n")
        run.gated, run.committed_here = set(), set()
        def next_phase(head):
            raise RuntimeError("reached gates")
        run.run_gates = next_phase
        original = validation.receipt
        def leased_receipt(*args, **kwargs):
            self.assertIsNotNone(kwargs.get("inputs"), "receipt preflight escaped the lease")
            self.assertTrue((self.root / ".git/ouro-validation/lock").exists())
            return original(*args, **kwargs)
        with patch.object(validation, "receipt", side_effect=leased_receipt) as receipt, \
                patch.object(validation, "attempts") as attempts:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "reached gates"):
                    Run.drive(run, None)
            self.assertEqual(receipt.call_count, 2)
            attempts.assert_not_called()
        directory = validation.state(run.tree, pending)[2]
        self.assertEqual(len(list(directory.glob("attempt-*/attempt.json"))), 1)
        # Ordinary resume still accepts real proof only after its clean-input barrier.
        run.options.validate = False
        with patch.object(validation, "validate", wraps=validation.validate) as validate, \
                patch.object(validation, "execute") as execute:
            with self.assertRaisesRegex(RuntimeError, "reached gates"):
                Run.drive(run, None)
            self.assertEqual(validate.call_args_list, [
                unittest.mock.call(run.tree, pending, run.wait_hint, reuse=True, execute_missing=False),
                unittest.mock.call(run.tree, [], run.wait_hint, check_head=run.tree.head()),
            ])
            execute.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "native Linux admission controls")
    def test_native_delayed_children_cannot_reach_gates(self):
        from ouro.run import Run
        try:
            validation.check_platform()
        except sh.DriverError as ex:
            self.skipTest(str(ex))
        run, pending = self.driver_fixture(script=
            "import os,pathlib,time\n"
            "print('parent evidence',flush=True)\n"
            "if os.fork()==0:\n"
            " os.setsid(); time.sleep(.4); pathlib.Path('output/marker').touch(); os._exit(0)\n"
            "os._exit(0)\n")
        (self.root / "output").mkdir()
        def fix(note, records):
            self.assertIn("exited 1", note)
            raise RuntimeError("failed attempt reached fix")
        run.fix_round = fix
        with patch.object(validation, "process_table",
                          side_effect=lambda env=None: {os.getpid(): (os.getppid(), "executor")}), \
                patch.object(run, "run_gates") as gates:
            with self.assertRaisesRegex(RuntimeError, "failed attempt reached fix"):
                Run.drive(run, None)
            gates.assert_not_called()
        self.assertTrue((self.root / "output/marker").exists())
        directory = validation.state(run.tree, pending)[2]
        attempt = json.loads(next(directory.glob("attempt-*/attempt.json")).read_text())
        result = attempt["results"][0]
        self.assertEqual(result["exit"], 1)
        self.assertIn("parent evidence", pathlib.Path(result["log"]).read_text())
        self.assertIn("incomplete execution", pathlib.Path(result["log"]).read_text())
        self.assertIsNone(validation.receipt(run.tree, pending))
        self.assertEqual(validation.attempts(run.tree), attempt["results"])
        self.assertFalse((self.root / ".git/ouro-validation/lock").exists())

    @unittest.skipUnless(os.name == "posix", "simulated WSL receipt barrier")
    def test_wsl_rejects_existing_proof_before_preflight(self):
        tree = self.fixture({"native.py": "print('native success')\n"})
        pending = [{"cmd": '"' + sys.executable + '" native.py', "reason": "caller context"}]
        # Simulated supported platform: exercise the real execution/receipt path.
        with patch.object(validation, "check_platform"):
            results, failure = validation.validate(tree, pending, HINT)
            self.assertIsNone(failure)
            self.assertIsNotNone(validation.receipt(tree, pending))
            reused, failure = validation.validate(tree, pending, HINT, reuse=True)
            self.assertIsNone(failure)
            self.assertEqual(reused, results)
        directory = validation.state(tree, pending)[2]
        proof = (directory / "receipt.json").read_bytes()
        with patch.object(validation, "check_platform", side_effect=sh.DriverError("WSL boundary unavailable")), \
                patch.object(tree, "git") as git, patch.object(tree, "head") as head:
            self.assertIsNone(validation.receipt(tree, pending))
            results, failure = validation.validate(tree, pending, HINT)
            self.assertEqual(failure, 75)
            self.assertIsNone(validation.receipt(tree, pending))
            git.assert_not_called()
            head.assert_not_called()
        self.assertEqual((directory / "receipt.json").read_bytes(), proof)
        self.assertEqual(results, [])
        self.assertEqual(len(list(directory.glob("attempt-*/attempt.json"))), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX visibility and PID identities")
    def test_visibility_failure_and_pid_reuse_never_authorize_launch_or_cleanup(self):
        with patch.object(validation, "check_platform"), \
                patch.object(validation, "process_table", side_effect=sh.DriverError("visibility unavailable")), \
                patch.object(validation.subprocess, "Popen") as launch:
            self.assertEqual(validation.execute("python3 native.py", self.root, self.root / "attempt.log"), 75)
            launch.assert_not_called()
        # A reused parent PID must neither acquire the new parent's children nor
        # authorize signalling it. The original still-live child remains owned.
        owned = {10: "old-parent", 20: "owned-child"}
        table = {10: (1, "reused-parent"), 20: (1, "owned-child"), 30: (10, "unrelated-child")}
        process = types.SimpleNamespace(wait=lambda: None)
        with patch.object(owned_process, "process_table", return_value=table), \
                patch.object(validation.os, "kill") as kill:
            validation.terminate_owned(process, owned)
        self.assertEqual(set(owned), {10, 20})
        self.assertEqual([args[0][0] for args in kill.call_args_list], [20, 20])
