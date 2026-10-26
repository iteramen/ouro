"""Real abrupt-death controls for caller-validation leases; scratch repositories only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
BIN = Path(__file__).resolve().parents[2] / "bin"
sys.path.insert(0, str(BIN))
from ouro import developer, sh, validation, validation_lock as locks


@unittest.skipUnless(sys.platform.startswith("linux"), "native Linux lease controls")
class ValidationLockTests(unittest.TestCase):
    def setUp(self):
        try:
            validation.check_platform()
        except sh.DriverError as ex:
            self.skipTest(str(ex))
        self.tmp = tempfile.TemporaryDirectory(prefix="ouro-lock-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def git(*args):
            p = subprocess.run(["git", "-C", str(self.root), *args], check=True,
                               capture_output=True, encoding="utf-8")
            return types.SimpleNamespace(out=p.stdout)
        self.git = git
        git("init", "-q")
        (self.root / ".gitignore").write_text("output/\n")
        (self.root / "worker.py").write_text(
            "import pathlib,time\n"
            "root=pathlib.Path('output'); end=time.monotonic()+15\n"
            "while not (root/'release').exists() and time.monotonic()<end: time.sleep(.02)\n"
            "(root/'worker-done').touch()\n")
        (self.root / "child.py").write_text(
            "import pathlib,subprocess,sys\n"
            "root=pathlib.Path('output'); root.mkdir(exist_ok=True)\n"
            "child=subprocess.Popen([sys.executable,'worker.py'],start_new_session=True)\n"
            "(root/'child').write_text(str(child.pid))\n"
            "child.wait()\n"
            "(root/'finished').touch()\n")
        (self.root / "pass.py").write_text("print('completed first command')\n")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "validation head")
        self.run = types.SimpleNamespace(
            head=lambda: git("rev-parse", "HEAD").out.strip(), wt_git=git,
            worktree=self.root, env=types.SimpleNamespace(runtime=developer.Settings(), default="main"))
        self.pending = [{"cmd": '"' + sys.executable + '" ' + name, "reason": "fixture"}
                        for name in ("pass.py", "child.py")]
        self.directory = validation.state(self.run, self.pending)[2]
        self.lockroot = self.directory.parent

    def until(self, fn):
        end = time.monotonic() + 12
        while time.monotonic() < end:
            value = fn()
            if value:
                return value
            time.sleep(.02)
        self.fail("bounded lifecycle observation did not arrive")

    def start(self, wait_for="child"):
        # Launch a real coordinator. Its executor and command survive SIGKILL.
        script = (
            "import sys,types,subprocess; from pathlib import Path\n"
            "sys.path.insert(0," + repr(str(BIN)) + ")\n"
            "from ouro import validation,developer\n"
            "def git(*a):\n"
            " p=subprocess.run(['git',*a],check=True,capture_output=True,encoding='utf-8'); "
            "return types.SimpleNamespace(out=p.stdout)\n"
            "run=types.SimpleNamespace(head=lambda:git('rev-parse','HEAD').out.strip(),wt_git=git,"
            "worktree=Path.cwd(),env=types.SimpleNamespace(runtime=developer.Settings()))\n"
            "validation.validate(run," + repr(self.pending) + ")\n")
        p = subprocess.Popen([sys.executable, "-B", "-c", script], cwd=self.root,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def cleanup():
            (self.root / "output").mkdir(exist_ok=True)
            (self.root / "output/release").touch()
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)
            if (self.root / "output/child").exists():
                self.until(lambda: (self.root / "output/finished").exists())
            if wait_for == "helper":
                self.until(lambda: (self.root / "output/worker-done").exists())
        self.addCleanup(cleanup)
        self.until(lambda: (self.root / ("output/" + wait_for)).exists())
        if wait_for == "child":
            self.until(lambda: len(json.loads(next(self.directory.glob("attempt-*/attempt.json")).read_text())["results"]) == 1)
        return p

    def test_abrupt_coordinator_death_live_child_then_recovery_and_resume(self):
        p = self.start()
        before = self.git("status", "--porcelain").out, self.run.head()
        attempt = next(self.directory.glob("attempt-*/attempt.json"))
        partial = attempt.read_bytes()
        with self.assertRaisesRegex(sh.DriverError, "already running"):
            validation.validate(self.run, self.pending)
        with self.assertRaisesRegex(sh.DriverError, "lease is live"):
            locks.inspect(self.lockroot, True)
        p.kill()
        p.wait(timeout=5)
        child = int((self.root / "output/child").read_text())
        os.kill(child, 0)  # actual command child remains alive after coordinator death
        data = locks.inspect(self.lockroot)
        self.assertEqual(data["phase"], "active")
        self.assertIn("birth", data["executor"])
        with self.assertRaisesRegex(sh.DriverError, "lease is live"):
            locks.inspect(self.lockroot, True)
        (self.root / "output/release").touch()
        self.until(lambda: locks.inspect(self.lockroot).get("phase") == "settled")
        # Kernel lease must also be released, not merely the durable phase.
        def recover():
            try:
                return locks.inspect(self.lockroot, True)
            except sh.DriverError as ex:
                if "lease is live" not in str(ex):
                    raise
        recovered = self.until(recover)
        self.assertEqual(recovered["status"], "recovered")
        self.assertTrue(Path(recovered["evidence"]).exists())
        self.assertEqual(locks.inspect(self.lockroot, True), {"status": "absent"})
        self.assertEqual(attempt.read_bytes(), partial)
        self.assertEqual([r["cmd"] for r in validation.attempts(self.run)], [self.pending[0]["cmd"]])
        self.assertIsNone(validation.receipt(self.run, self.pending))
        self.assertEqual((self.git("status", "--porcelain").out, self.run.head()), before)
        # Ordinary current-input validation must rerun the entire batch.
        results, failure = validation.validate(self.run, self.pending)
        self.assertIsNone(failure)
        self.assertEqual([r["cmd"] for r in results], [r["cmd"] for r in self.pending])
        self.assertIsNotNone(validation.receipt(self.run, self.pending))
        self.assertEqual(len(list(self.directory.glob("attempt-*/attempt.json"))), 2)
        self.assertEqual(attempt.read_bytes(), partial)

    def test_git_helper_death_controls(self):
        for transition, orphan in ((1, False), (2, False), (1, True)):
            with self.subTest(transition=transition, orphan=orphan):
                output = self.root / "output"
                output.mkdir(exist_ok=True)
                for name in ("release", "helper", "worker-done", "count"):
                    (output / name).unlink(missing_ok=True)
                hook = output / "fsmonitor"
                hook.write_text(
                    "#!" + sys.executable + "\n"
                    "import os,pathlib,subprocess,sys,time\n"
                    "root=pathlib.Path('output'); count=root/'count'\n"
                    "n=int(count.read_text())+1 if count.exists() else 1; count.write_text(str(n))\n"
                    "if n==" + str(transition) + ":\n"
                    " child=subprocess.Popen([sys.executable,'worker.py'],start_new_session=True)\n"
                    " (root/'helper').write_text(str(child.pid))\n"
                    + (" child.wait()\n" if not orphan else "") +
                    "print('token\\n')\n")
                hook.chmod(0o700)
                self.git("config", "core.fsmonitor", str(hook))
                self.pending = [self.pending[0]]
                self.directory = validation.state(self.run, self.pending)[2]
                p = self.start("helper")
                p.kill()
                p.wait(timeout=5)
                os.kill(int((output / "helper").read_text()), 0)
                with self.assertRaisesRegex(sh.DriverError, "lease is live"):
                    locks.inspect(self.lockroot, True)
                self.assertEqual(locks.inspect(self.lockroot)["phase"], "active")
                self.assertIsNone(validation.receipt(self.run, self.pending))
                (output / "release").touch()
                self.until(lambda: locks.inspect(self.lockroot).get("phase") == "settled")
                settled = locks.inspect(self.lockroot)
                if orphan:
                    self.assertIn("helper snapshot refused", settled["error"])
                else:
                    self.assertNotIn("error", settled)
                def recover():
                    try:
                        return locks.inspect(self.lockroot, True)
                    except sh.DriverError as ex:
                        if "lease is live" not in str(ex):
                            raise
                self.assertEqual(self.until(recover)["status"], "recovered")
                self.assertTrue((output / "worker-done").exists())
                self.assertIsNone(validation.receipt(self.run, self.pending))
                self.git("config", "--unset", "core.fsmonitor")

    def test_detached_command_invalidates_result_but_preserves_attempt(self):
        (self.root / "pass.py").write_text(
            "import os,pathlib,time\n"
            "print('parent output',flush=True)\n"
            "if os.fork()==0:\n"
            " os.setsid(); time.sleep(.4); pathlib.Path('output/late').touch(); os._exit(0)\n"
            "os._exit(0)\n")
        self.git("add", "pass.py")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "detached fixture")
        (self.root / "output").mkdir()
        pending = self.pending[:1]
        # Deliberately miss the fork in ps: the kernel adoption boundary must refuse it.
        observer = lambda env=None: {os.getpid(): (os.getppid(), "executor")}
        with patch.object(validation, "process_table", side_effect=observer):
            results, failure = validation.validate(self.run, pending)
        self.assertEqual(failure, 1)
        self.assertEqual(results[0]["exit"], 1)
        self.assertTrue((self.root / "output/late").exists())
        log = Path(results[0]["log"])
        self.assertIn("parent output", log.read_text())
        self.assertIn("incomplete execution", log.read_text())
        attempt = json.loads((log.parent / "attempt.json").read_text())
        self.assertEqual(attempt["results"], results)
        self.assertEqual(validation.attempts(self.run), results)
        self.assertIsNone(validation.receipt(self.run, pending))
        self.assertFalse((self.lockroot / "lock").exists())

    def test_adopted_zombie_is_not_completion_proof(self):
        self.lockroot.mkdir(parents=True)
        lease = locks.Lease(self.lockroot / "lock", self.run.head(), [])
        def helper():
            child = os.fork()
            if child == 0:
                os._exit(0)
            # Observe the zombie without reaping it before the first settlement wait.
            os.waitid(os.P_PID, child, os.WEXITED | os.WNOWAIT)
            return "stale"
        try:
            with self.assertRaisesRegex(sh.DriverError, "helper snapshot refused"):
                lease.execute(helper)
            self.assertEqual(lease.data["phase"], "settled")
            self.assertTrue(lease.safe)
        finally:
            lease.close()

    def test_delayed_fsmonitor_mutation_refuses_final_and_reuse_snapshots(self):
        pending = self.pending[:1]
        output = self.root / "output"
        output.mkdir()
        source = self.root / "pass.py"
        original = source.read_bytes()
        # status calls: initial clean, post-command clean, final clean; reuse has two.
        for mode, trigger in (("final", 3), ("reuse", 2), ("resume", 2)):
            with self.subTest(mode=mode):
                if mode != "final":
                    results, failure = validation.validate(self.run, pending)
                    self.assertIsNone(failure)
                directory = validation.state(self.run, pending)[2]
                before = {p: p.read_bytes() for p in directory.glob("attempt-*/*")}
                hook = output / "fsmonitor"
                (output / "count").unlink(missing_ok=True)
                (output / "mutated").unlink(missing_ok=True)
                hook.write_text(
                    "#!" + sys.executable + "\n"
                    "import os,pathlib,subprocess,sys\n"
                    "root=pathlib.Path(" + repr(str(self.root)) + ")\n"
                    "count=root/'output/count'\n"
                    "n=int(count.read_text())+1 if count.exists() else 1; count.write_text(str(n))\n"
                    "if n==" + str(trigger) + ":\n"
                    " subprocess.Popen([sys.executable,'-c',"
                    + repr("import pathlib,time; time.sleep(.3); "
                           + "pathlib.Path(" + repr(str(source)) + ").write_text('changed source\\n'); "
                           + "pathlib.Path(" + repr(str(output / "mutated")) + ").touch()")
                    + "],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                    "print('token\\n')\n")
                hook.chmod(0o700)
                self.git("config", "core.fsmonitor", str(hook))
                try:
                    with self.assertRaisesRegex(sh.DriverError, "helper snapshot refused"):
                        validation.validate(self.run, pending, reuse=mode != "final",
                                            execute_missing=mode != "resume")
                    self.assertTrue((output / "mutated").exists())
                    self.assertEqual(source.read_text(), "changed source\n")
                    # The deliberate hook reports no changed paths. Inspect the
                    # preserved mutation independently after proving its refusal.
                    self.git("config", "core.fsmonitor", "false")
                    self.assertTrue(self.git("status", "--porcelain").out)
                    self.assertFalse((self.lockroot / "lock").exists())
                    for path, raw in before.items():
                        self.assertEqual(path.read_bytes(), raw)
                    if mode == "final":
                        self.assertIsNone(validation.receipt(self.run, pending))
                        attempt = json.loads(next(directory.glob("attempt-*/attempt.json")).read_text())
                        self.assertEqual(attempt["results"][0]["exit"], 0)
                    # Even with historical proof, dirty current inputs cannot resume.
                    with self.assertRaises(sh.DriverError):
                        validation.validate(self.run, pending, reuse=True, execute_missing=False)
                finally:
                    self.git("config", "--unset", "core.fsmonitor")
                    source.write_bytes(original)  # restore only this scratch fixture

    def test_large_approved_batch_retains_full_evidence(self):
        cmd = '"' + sys.executable + '" -c pass ' + "x" * 70000
        validation.command(cmd)
        pending = [{"cmd": cmd, "reason": "large approved command control"}]
        with patch.object(sh, "say"):
            results, failure = validation.validate(self.run, pending)
        self.assertIsNone(failure)
        self.assertEqual(results[0]["cmd"], cmd)
        self.assertEqual(validation.receipt(self.run, pending), results)
        evidence = validation.state(self.run, pending)[2]
        self.assertEqual(json.loads(next(evidence.glob("attempt-*/attempt.json")).read_text())["commands"], [cmd])
        proof = (evidence / "receipt.json").read_bytes()
        with patch.object(sh, "say"):
            reused, failure = validation.validate(self.run, pending, reuse=True)
        self.assertIsNone(failure)
        self.assertEqual(reused, results)
        self.assertEqual((evidence / "receipt.json").read_bytes(), proof)
        self.assertEqual(len(list(evidence.glob("attempt-*/attempt.json"))), 1)
        self.assertFalse((self.lockroot / "lock").exists())
        lease = locks.Lease(self.lockroot / "lock", self.run.head(), [cmd])
        try:
            self.assertLess(os.fstat(lease.fd).st_size, 2048)
            self.assertEqual(locks.read(lease.fd)["count"], 1)
            self.assertNotIn("commands", locks.read(lease.fd))
        finally:
            lease.close()

    def test_executor_death_never_settles_even_after_child_ends(self):
        p = self.start()
        p.kill()
        p.wait(timeout=5)
        data = locks.inspect(self.lockroot)
        os.kill(data["executor"]["pid"], signal.SIGKILL)
        child = int((self.root / "output/child").read_text())
        os.kill(child, 0)
        # Neither a dead coordinator nor eventual absence of workers repairs the
        # missing lifecycle transition. Repeated requests remain bounded refusals.
        for _ in range(2):
            with self.assertRaisesRegex(sh.DriverError, "insufficient lifecycle proof|lease is live"):
                locks.inspect(self.lockroot, True)
        (self.root / "output/release").touch()
        self.until(lambda: (self.root / "output/finished").exists())
        with self.assertRaisesRegex(sh.DriverError, "insufficient lifecycle proof"):
            locks.inspect(self.lockroot, True)
        self.assertTrue((self.lockroot / "lock").exists())
        self.assertIsNone(validation.receipt(self.run, self.pending))

    def test_missing_visibility_reused_pid_and_mismatched_context_refuse(self):
        self.lockroot.mkdir(parents=True)
        path = self.lockroot / "lock"
        lease = locks.Lease(path, self.run.head(), [])
        data = dict(lease.data)
        os.close(lease.fd)  # fixture a released lease, retaining durable evidence
        for update, message in (({"version": 1}, "insufficient lifecycle"),
                                ({"context": {}}, "insufficient lifecycle"),
                                ({"phase": "active"}, "insufficient lifecycle"),
                                ({"birth": "reused"}, "PID reused")):
            path.write_text(json.dumps({**data, **update}))
            with self.subTest(update=update), self.assertRaisesRegex(sh.DriverError, message):
                locks.inspect(self.lockroot, True)
        path.write_text(json.dumps(data))
        with patch.object(validation, "process_table", side_effect=sh.DriverError("visibility unavailable")), \
                self.assertRaisesRegex(sh.DriverError, "visibility unavailable"):
            locks.inspect(self.lockroot, True)
        with patch.object(validation, "process_table", return_value={}), \
                self.assertRaisesRegex(sh.DriverError, "visibility unavailable"):
            locks.inspect(self.lockroot, True)
        for raw in ("", "{}", "{broken"):
            path.write_text(raw)
            with self.assertRaisesRegex(sh.DriverError, "insufficient lifecycle"):
                locks.inspect(self.lockroot, True)
        self.assertTrue(path.exists())

    def test_interrupted_attempt_update_preserves_prior_results(self):
        evidence = self.root / ".git" / "attempt-fixture"
        evidence.mkdir()
        validation.record_attempt(evidence, self.run.head(), ["first", "second"], [{"cmd": "first"}])
        before = (evidence / "attempt.json").read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("interrupted update")), \
                self.assertRaises(OSError):
            validation.record_attempt(evidence, self.run.head(), ["first", "second"], [])
        self.assertEqual((evidence / "attempt.json").read_bytes(), before)

    def test_recovery_rereads_phase_after_acquiring_lease(self):
        self.lockroot.mkdir(parents=True)
        path = self.lockroot / "lock"
        lease = locks.Lease(path, self.run.head(), [])
        data = dict(lease.data)
        os.close(lease.fd)
        active = {**data, "phase": "active"}
        # Simulate the owner advancing and then dying between the optimistic
        # inspection read and acquisition. A cached idle observation is unsafe.
        with patch.object(locks, "read", side_effect=[data, active]), \
                self.assertRaisesRegex(sh.DriverError, "insufficient lifecycle proof"):
            locks.inspect(self.lockroot, True)
        self.assertTrue(path.exists())

    def test_live_helper_settlement_is_bounded_and_never_authorizes_recovery(self):
        self.lockroot.mkdir(parents=True)
        lease = locks.Lease(self.lockroot / "lock", self.run.head(), [])
        ownership = self.root / "owned-helper.json"
        def work():
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
            ownership.write_text(json.dumps({"pid": child.pid, "birth": locks.birth(child.pid)}))
        try:
            start = time.monotonic()
            with patch.object(locks, "SETTLE_TIMEOUT", 0.2):
                with self.assertRaisesRegex(sh.DriverError, "executor interrupted"):
                    lease.execute(work)
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(locks.inspect(self.lockroot)["phase"], "active")
        finally:
            lease.close()
            if ownership.exists():
                child = json.loads(ownership.read_text())
                try:
                    if locks.birth(child["pid"]) == child["birth"]:
                        os.kill(child["pid"], signal.SIGKILL)
                except FileNotFoundError:
                    pass
        with self.assertRaisesRegex(sh.DriverError, "insufficient lifecycle proof"):
            locks.inspect(self.lockroot, True)

    def test_cli_inspection_has_no_binding_or_tracker_requirement(self):
        result = subprocess.run([sys.executable, "-B", str(BIN / "ouro"),
                                 "validation-lock", "--recover"], cwd=self.root,
                                capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "absent"})


if __name__ == "__main__":
    unittest.main()
