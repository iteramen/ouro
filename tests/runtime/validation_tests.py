"""Caller-validation refusal tests; real WSL controls are distinct from simulated platform rows."""
import base64
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from ouro import developer, sh, validation


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ouro-validation-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def fixture(self, scripts):
        def git(*args):
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
        return types.SimpleNamespace(
            worktree=self.root, head=lambda: git("rev-parse", "HEAD").out.strip(), wt_git=git,
            env=types.SimpleNamespace(runtime=developer.Settings()))

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
        from ouro.run import Run
        base = self.fixture({"native.py": script})
        pending = [{"cmd": '"' + sys.executable + '" native.py', "reason": "caller"}]
        base.wt_git("update-ref", "refs/remotes/origin/main", "HEAD")
        base.wt_git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--allow-empty", "-qm",
                    "handoff\n\nOuro-Build: " + json.dumps({"validation_pending": pending}))
        run = Run.__new__(Run)
        run.worktree, run.wt_git = base.worktree, base.wt_git
        run.env = types.SimpleNamespace(pr_checks=lambda: True, validate=validating,
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
            validate.assert_called_once_with(run, pending, reuse=True, execute_missing=False)
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
        directory = validation.state(run, pending)[2]
        self.assertEqual(len(list(directory.glob("attempt-*/attempt.json"))), 1)
        # Ordinary resume still accepts real proof only after its clean-input barrier.
        run.env.validate = False
        with patch.object(validation, "validate", wraps=validation.validate) as validate, \
                patch.object(validation, "execute") as execute:
            with self.assertRaisesRegex(RuntimeError, "reached gates"):
                Run.drive(run, None)
            self.assertEqual(validate.call_args_list, [
                unittest.mock.call(run, pending, reuse=True, execute_missing=False),
                unittest.mock.call(run, [], check_head=run.head()),
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
        directory = validation.state(run, pending)[2]
        attempt = json.loads(next(directory.glob("attempt-*/attempt.json")).read_text())
        result = attempt["results"][0]
        self.assertEqual(result["exit"], 1)
        self.assertIn("parent evidence", pathlib.Path(result["log"]).read_text())
        self.assertIn("incomplete execution", pathlib.Path(result["log"]).read_text())
        self.assertIsNone(validation.receipt(run, pending))

    @unittest.skipUnless(sys.platform.startswith("linux"), "native Linux receipt admission controls")
    def test_native_receipt_resume_refuses_delayed_helper_mutation_before_gates(self):
        from ouro.run import Run
        try:
            validation.check_platform()
        except sh.DriverError as ex:
            self.skipTest(str(ex))
        run, pending = self.driver_fixture(script="print('completed')\n")
        results, failure = validation.validate(run, pending)
        self.assertIsNone(failure)
        directory = validation.state(run, pending)[2]
        proof = (directory / "receipt.json").read_bytes()
        source = self.root / "native.py"
        original = source.read_bytes()
        output = self.root / "output"
        output.mkdir()
        for validating in (True, False):
            with self.subTest(validating=validating):
                run.env.validate = validating
                (output / "count").unlink(missing_ok=True)
                hook = output / "fsmonitor"
                worker = ("import pathlib,time; time.sleep(.3); pathlib.Path("
                          + repr(str(source)) + ").write_text('changed source\\n')")
                hook.write_text(
                    "#!" + sys.executable + "\n"
                    "import pathlib,subprocess,sys\n"
                    "count=pathlib.Path(" + repr(str(output / "count")) + ")\n"
                    "n=int(count.read_text())+1 if count.exists() else 1; count.write_text(str(n))\n"
                    "if n==2: subprocess.Popen([sys.executable,'-c'," + repr(worker)
                    + "],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                    "print('token\\n')\n")
                hook.chmod(0o700)
                run.wt_git("config", "core.fsmonitor", str(hook))
                try:
                    with patch.object(run, "run_gates") as gates:
                        with self.assertRaisesRegex(sh.DriverError, "helper snapshot refused"):
                            Run.drive(run, None)
                        gates.assert_not_called()
                    self.assertEqual(source.read_text(), "changed source\n")
                    self.assertEqual((directory / "receipt.json").read_bytes(), proof)
                    self.assertEqual(len(list(directory.glob("attempt-*"))), 1)
                finally:
                    run.wt_git("config", "--unset", "core.fsmonitor")
                    source.write_bytes(original)  # scratch fixture only

    @unittest.skipUnless(os.name == "posix", "simulated WSL receipt barrier")
    def test_wsl_rejects_existing_proof_before_preflight(self):
        run = self.fixture({"native.py": "print('native success')\n"})
        pending = [{"cmd": '"' + sys.executable + '" native.py', "reason": "caller context"}]
        # Simulated supported platform: exercise the real execution/receipt path.
        with patch.object(validation, "check_platform"):
            results, failure = validation.validate(run, pending)
            self.assertIsNone(failure)
            self.assertIsNotNone(validation.receipt(run, pending))
            reused, failure = validation.validate(run, pending, reuse=True)
            self.assertIsNone(failure)
            self.assertEqual(reused, results)
        directory = validation.state(run, pending)[2]
        proof = (directory / "receipt.json").read_bytes()
        with patch.object(validation, "check_platform", side_effect=sh.DriverError("WSL boundary unavailable")), \
                patch.object(run, "wt_git") as git, patch.object(run, "head") as head:
            self.assertIsNone(validation.receipt(run, pending))
            results, failure = validation.validate(run, pending)
            self.assertEqual(failure, 75)
            self.assertIsNone(validation.receipt(run, pending))
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
        with patch.object(validation, "process_table", return_value=table), \
                patch.object(validation.os, "kill") as kill:
            validation.terminate_owned(process, owned)
        self.assertEqual(set(owned), {10, 20})
        self.assertEqual([args[0][0] for args in kill.call_args_list], [20, 20])

    @unittest.skipUnless(sys.platform.startswith("linux"), "actual WSL conformance requires Linux")
    def test_actual_wsl_direct_and_nested_activation(self):
        powershell, wslpath = shutil.which("powershell.exe"), shutil.which("wslpath")
        kernel = os.uname()
        # Do not let a detector regression skip its own actual-host test.
        hint = (re.search("microsoft|wsl", kernel.release + kernel.version, re.IGNORECASE)
                or os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP")
                or pathlib.Path("/run/WSL").exists()
                or pathlib.Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists())
        if not hint and not (powershell and wslpath):
            self.skipTest("actual WSL conformance unavailable on pure Linux")
        with self.assertRaisesRegex(sh.DriverError, "on WSL:"):
            validation.check_platform()
        self.assertIsNotNone(powershell, "actual WSL controls require powershell.exe on PATH")
        self.assertIsNotNone(wslpath, "actual WSL controls require wslpath")
        # Calibrate a real broker-created Windows child outside validation, then
        # prove validation starts neither that direct parent nor its native wrapper.
        run = self.fixture({})
        output = self.root / "output"
        output.mkdir()
        def quote(text):
            return "'" + text.replace("'", "''") + "'"
        def encoded(script):
            return base64.b64encode(script.encode("utf-16le")).decode("ascii")
        # Windows cannot necessarily traverse the native, private Linux Git fixture.
        # Create only the markers on the Windows account's temp volume, with ordinary
        # inherited Windows ACLs; keep the repository on its native Linux filesystem.
        temp_query = "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetTempPath()))"
        windows_temp = base64.b64decode(subprocess.check_output(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(temp_query)],
            encoding="ascii", timeout=15).strip(), validate=True).decode("utf-8")
        linux_temp = subprocess.check_output(
            [wslpath, "-u", windows_temp], encoding="utf-8", timeout=5).strip()
        markers = tempfile.TemporaryDirectory(prefix="ouro-validation-markers-", dir=linux_temp)
        self.addCleanup(markers.cleanup)
        # Even a failed baseline assertion leaves only a finite, five-second child.
        # Allow it to finish before removing its exclusively created marker directory.
        self.addCleanup(time.sleep, 5.2)
        marker_root = pathlib.Path(markers.name)
        windows = subprocess.check_output(
            [wslpath, "-w", str(marker_root)], encoding="utf-8", timeout=5).strip()
        for nested in (False, True):
            child = (
                "$ErrorActionPreference='Stop'; "
                + "[IO.File]::WriteAllText(" + quote(windows + "\\ready") + ", [string]$PID); "
                + "Start-Sleep -Seconds 5; "
                + "[IO.File]::WriteAllText(" + quote(windows + "\\delayed") + ", 'written')"
            )
            parent = (
                "$ErrorActionPreference='Stop'; "
                + "$p=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') "
                + "-WindowStyle Hidden -PassThru -ArgumentList "
                + quote("-NoProfile -NonInteractive -EncodedCommand " + encoded(child)) + "; "
                + "$deadline=[DateTime]::UtcNow.AddSeconds(15); "
                + "while (!(Test-Path " + quote(windows + "\\ready") + ")) { "
                + "if ([DateTime]::UtcNow -gt $deadline) { throw 'child not ready' }; Start-Sleep -Milliseconds 20 }; "
                + "$p.Refresh(); if ($p.HasExited) { throw 'child exited before liveness sample' }; "
                + "[IO.File]::WriteAllText(" + quote(windows + "\\alive") + ", [string]$p.Id)"
            )
            argv = [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(parent)]
            if nested:
                script = self.root / "native.py"
                script.write_text(
                    "import pathlib,subprocess\npathlib.Path('output/native-ready').write_text('ready')\n"
                    + "subprocess.run(" + repr(argv) + ", check=True)\n", encoding="utf-8")
                run.wt_git("add", "native.py")
                run.wt_git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "commit", "-qm", "native wrapper")
                argv = [sys.executable, str(script)]
            subprocess.run(argv, cwd=self.root, check=True, timeout=25, capture_output=True)
            ready, alive, delayed = (marker_root / name for name in ("ready", "alive", "delayed"))
            self.assertTrue(ready.exists())
            child_pid = ready.read_text(encoding="utf-8-sig")
            self.assertEqual(child_pid, alive.read_text(encoding="utf-8-sig"))
            self.assertEqual((output / "native-ready").exists(), nested)
            self.assertFalse(delayed.exists(), "control must observe the Windows child still able to write later")
            time.sleep(5.2)
            self.assertTrue(delayed.exists(), "baseline must demonstrate actual delayed Windows work")
            for path in (ready, alive, delayed, output / "native-ready"):
                path.unlink(missing_ok=True)
            cmd = " ".join(shlex.quote(arg) for arg in argv)
            pending = [{"cmd": cmd, "reason": "actual WSL activation control"}]
            with patch.object(validation, "shutil") as command_tools, \
                    patch.object(validation, "subprocess") as command_process:
                results, failure = validation.validate(run, pending)
                self.assertEqual((results, failure), ([], 75))
                self.assertIsNone(validation.receipt(run, pending))
                command_tools.which.assert_not_called()
                command_process.Popen.assert_not_called()
            self.assertFalse(ready.exists())
            self.assertFalse(alive.exists())
            self.assertFalse((output / "native-ready").exists())
            time.sleep(5.2)
            self.assertFalse(delayed.exists())
            self.assertFalse((validation.state(run, pending)[2] / "receipt.json").exists())
            print("WSL conformance " + json.dumps({
                "nested": nested, "baseline_pid": child_pid, "baseline_native_ready": nested,
                "baseline_ready": True, "baseline_alive": True,
                "baseline_delayed": True, "validation_exit": failure,
                "validation_ready": False, "validation_alive": False, "validation_delayed": False,
                "successful_receipt": False, "command_attempts": results,
                "command_lookup": False, "command_launch": False}))
