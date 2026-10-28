"""Final execution proof uses the real bounded, leased validator; observations are not proof."""
import json
import os
import pathlib
import sys
import tempfile
import time
import types
import unittest
import uuid
from unittest.mock import patch

from ouro import developer, execution, sh, validation
from ouro.run import Run


class GateExecutionTests(unittest.TestCase):
    def test_current_branch_gates_extend_cached_grants_without_caller_approval(self):
        old = ("python3 old_gate.py", {"cheap": True})
        added = ("python3 new_gate.py", {"cheap": True})
        run = Run.__new__(Run)
        run.body = ""
        run.spec_amendments = lambda: []
        run.env = types.SimpleNamespace(pr_checks=lambda: True, lookup=lambda key: "local" if key == "ship.validation" else None)
        run.area_gates = lambda tree=True: [added, added] if tree else [old]
        for cached in ([old], [], None):
            with self.subTest(cached=cached), patch.object(execution, "grants", return_value=[old]) as history:
                run.execution_grants = cached
                expected = [old[0], added[0]] if cached != [] else [added[0]]
                self.assertEqual(run.required_execution(), expected)
                self.assertEqual(run.validation_commands(), [old[0]])
                self.assertEqual(history.call_count, int(cached is None))

    def fixture(self, source, *, caller=False):
        temporary = tempfile.TemporaryDirectory(prefix="ouro-gate-proof-")
        self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name).resolve()
        def git(*args, check=True):
            result = sh.run(["git", "-C", str(root), *args], timeout=10)
            if check and result.code:
                raise AssertionError(result.err)
            return result
        git("init", "-q")
        (root / ".gitignore").write_text("output/\n", encoding="utf-8")
        (root / "gate.py").write_text(source, encoding="utf-8")
        (root / "output").mkdir()
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        command = sh.cmdline([sys.executable, "gate.py"])
        run = types.SimpleNamespace(worktree=root, wt_git=git,
            head=lambda: git("rev-parse", "HEAD").out.strip(),
            env=types.SimpleNamespace(default="main", validate=caller, runtime=developer.Settings(),
                                      lookup=lambda key: "local" if key == "ship.validation" else None),
            required_execution=lambda: [command], validation_commands=lambda: [command])
        return run, command

    def test_changed_path_selection_does_not_execute_configured_diff_helpers(self):
        run, _ = self.fixture("print('old')\n")
        git, root = run.wt_git, run.worktree
        (root / ".gitattributes").write_text("gate.py diff=fixture\n", encoding="utf-8")
        git("add", ".gitattributes")
        commit = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm")
        git(*commit, "attributes")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        (root / "gate.py").write_text("print('new')\n", encoding="utf-8")
        git("add", "gate.py")
        git(*commit, "change")
        marker = root / "output" / "metadata-helper"
        helper = root / ".git" / "metadata-helper.py"
        helper.write_text("from pathlib import Path\nPath(" + repr(str(marker)) +
                          ").write_text('seen')\nraise SystemExit(1)\n", encoding="utf-8")
        command = f'"{pathlib.Path(sys.executable).as_posix()}" "{helper.as_posix()}"'
        git("config", "diff.fixture.command", command)
        git("config", "diff.fixture.trustExitCode", "true")
        config = (root / ".git" / "config").read_bytes()
        ignores = (root / ".gitignore").read_bytes()
        # Calibrate the configured helper even on Git versions where --quiet
        # does not invoke trusted helpers; no negative control is skipped.
        git("diff", "--ext-diff", "origin/main...HEAD", "--", "gate.py", check=False)
        self.assertTrue(marker.exists(), "configured external helper was not calibrated")
        marker.unlink()
        self.assertTrue(Run.touches(run, ["gate.py"]))
        self.assertFalse(marker.exists(), "read-only path selection executed an external helper")
        self.assertFalse(Run.touches(run, ["absent.py"]))
        self.assertEqual((root / ".git" / "config").read_bytes(), config)
        self.assertEqual((root / ".gitignore").read_bytes(), ignores)

    def native(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("native Linux validation lease controls")
        try:
            validation.check_platform()
        except sh.DriverError as error:
            self.skipTest(str(error))

    def test_driver_selected_success_and_failure_use_settled_results(self):
        self.native()
        for code in (0, 7):
            with self.subTest(code=code):
                run, command = self.fixture(f"raise SystemExit({code})\n")
                result = Run.settled_gates(run, run.head(), [(command, {})], [])
                self.assertEqual(result is None, code == 0)
                self.assertEqual(Run.missing_execution(run), [] if code == 0 else [command])
                self.assertEqual(run.execution_results[-1]["exit"], code)
                self.assertTrue(pathlib.Path(run.execution_results[-1]["log"]).is_file())

    def test_zero_parent_with_detached_work_never_produces_successful_proof(self):
        self.native()
        child = ("import pathlib,time; pathlib.Path('output/ready').touch(); "
                 "time.sleep(.4); pathlib.Path('output/delayed').touch()")
        run, command = self.fixture(
            "import pathlib,subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True, "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "deadline=time.monotonic()+3\n"
            "while not pathlib.Path('output/ready').exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "assert pathlib.Path('output/ready').exists()\n")
        result = Run.settled_gates(run, run.head(), [(command, {})], [])
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[:2], (command, 1))
        self.assertIn("owned descendants", result[2])
        self.assertEqual(Run.missing_execution(run), [command])
        self.assertFalse(list((run.worktree / ".git" / "ouro-validation").glob("*/receipt.json")))

    def test_final_proof_has_real_time_and_output_bounds(self):
        self.native()
        for source, options in (("import time; time.sleep(10)\n", {"TIMEOUT": .1}),
                ("import sys,time; sys.stdout.write('x'*8192); sys.stdout.flush(); time.sleep(10)\n",
                 {"OUTPUT_LIMIT": 512})):
            with self.subTest(options=options), patch.multiple(validation, **options):
                run, command = self.fixture(source)
                result = Run.settled_gates(run, run.head(), [(command, {})], [])
                self.assertEqual(result[:2], (command, 124))
                self.assertIn("time/output limit", result[2])
                self.assertEqual(Run.missing_execution(run), [command])

    def test_worker_candidate_needs_caller_consent_and_reuses_only_genuine_proof(self):
        self.native()
        run, command = self.fixture(
            "from pathlib import Path\np=Path('output/count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n")
        with patch.object(execution, "candidates", return_value=[command]):
            self.assertEqual(Run.settled_gates(run, run.head(), [], []), 75)
            self.assertFalse((run.worktree / "output/count").exists())
            run.env.validate = True
            self.assertIsNone(Run.settled_gates(run, run.head(), [], []))
            self.assertEqual(Run.missing_execution(run), [])
        # Losing candidate metadata does not invalidate an intact validator receipt.
        run.env.validate = False
        with patch.object(execution, "candidates", return_value=[]):
            self.assertIsNone(Run.settled_gates(run, run.head(), [], []))
            self.assertEqual(Run.missing_execution(run), [])
        self.assertEqual((run.worktree / "output/count").read_text(), "1")

    def test_candidate_cannot_expand_the_invoking_caller_allowlist(self):
        self.native()
        run, command = self.fixture("from pathlib import Path\nPath('output/ran').touch()\n", caller=True)
        run.validation_commands = lambda: []
        with patch.object(execution, "candidates", return_value=[command]):
            self.assertIsNone(Run.settled_gates(run, run.head(), [], []))
        self.assertFalse((run.worktree / "output/ran").exists())
        self.assertEqual(Run.missing_execution(run), [command])

    def test_private_observations_alone_never_clear_the_final_barrier(self):
        run, command = self.fixture("print('not executed')\n")
        with patch.object(execution, "proven", return_value={command}):
            self.assertEqual(Run.missing_execution(run), [command])

    def test_non_linux_posix_refuses_before_any_unowned_lease_or_launch(self):
        run, command = self.fixture("raise SystemExit(0)\n", caller=True)
        head = run.head()
        for system in ("Darwin", "FreeBSD"):
            kernel = types.SimpleNamespace(sysname=system, release="fixture", version="fixture")
            with self.subTest(system=system), patch.object(validation.os, "name", "posix"), \
                    patch.object(validation.os, "uname", create=True, return_value=kernel), \
                    patch.object(validation.validation_lock, "Lease") as lease, \
                    patch.object(validation.subprocess, "Popen") as launch:
                self.assertEqual(validation.validate(run, [{"cmd": command, "reason": "fixture"}]), ([], 75))
                self.assertEqual(validation.check_inputs(run, head), 75)
                self.assertEqual(Run.settled_gates(run, head, [(command, {})], []), 75)
                lease.assert_not_called()
                launch.assert_not_called()

    def test_input_check_has_no_command_attempt_or_receipt_and_preserves_dirty_inputs(self):
        self.native()
        run, _ = self.fixture("print('input')\n")
        head = run.head()
        self.assertIsNone(validation.check_inputs(run, head))
        source = run.worktree / "gate.py"
        source.write_text("changed input\n", encoding="utf-8")
        self.assertEqual(validation.check_inputs(run, head), 1)
        self.assertEqual(validation.check_inputs(run, "0" * 40), 1)
        self.assertEqual(source.read_text(), "changed input\n")
        state = run.worktree / ".git" / "ouro-validation"
        self.assertFalse(list(state.glob("*/attempt-*")))
        self.assertFalse(list(state.glob("*/receipt.json")))
        self.assertFalse((state / "lock").exists())

    def test_selected_missing_executable_keeps_gate_uncovered_before_validation(self):
        from unittest.mock import Mock
        run, _ = self.fixture("print('not executed')\n")
        missing = "ouro-fixture-missing-" + uuid.uuid4().hex
        run.stop_issue = Mock(side_effect=sh.Stopped(3))
        with patch.object(validation, "validate") as batch, patch.object(validation, "check_platform") as platform:
            with self.assertRaises(sh.Stopped) as stopped:
                Run.settled_gates(run, run.head(), [(missing, {})], [])
            self.assertEqual(stopped.exception.args, (3,))
            self.assertEqual(run.stop_issue.call_args.args[0], "gate uncovered")
            self.assertEqual(run.stop_issue.call_args.args[2], 3)
            batch.assert_not_called()
            platform.assert_not_called()

    def test_explicit_caller_missing_executable_still_waits(self):
        self.native()
        run, _ = self.fixture("print('not executed')\n", caller=True)
        missing = "ouro-fixture-missing-" + uuid.uuid4().hex
        run.required_execution = run.validation_commands = lambda: [missing]
        with patch.object(execution, "candidates", return_value=[missing]):
            self.assertEqual(Run.settled_gates(run, run.head(), [], []), 75)
        self.assertEqual(run.execution_results[-1]["exit"], 75)
        self.assertEqual(Run.missing_execution(run), [missing])

    def resumed_run(self):
        base, command = self.fixture("print('complete')\n")
        proof, failure = validation.validate(base, [{"cmd": command, "reason": "genuine resume proof"}])
        self.assertIsNone(failure)
        self.assertEqual(proof[0]["exit"], 0)
        run = Run.__new__(Run)
        run.__dict__.update(vars(base))
        head = run.head()
        pr = {"state": "OPEN", "isDraft": False, "number": 1}
        run.env.pr_checks = lambda: True
        run.commits_past_base = lambda: 1
        run.rederive = lambda: None
        run.find_pr = lambda: pr
        run.records = lambda _: [{"sha": head, "round": 1, "findings": []}]
        run.build_facts = lambda **_: {"validation_pending": []}
        run.select_gates = lambda _: ([(command, {})], [])
        run.origin_tip = lambda: head
        run.checks = lambda *_: "green"
        run.flow, run.primary, run.copilot, run.in_lane = "standard", "node", False, False
        run.n, run.k, run.gated = 1, None, set()
        state = {"ready": False}
        def decide(*_):
            state["ready"] = True
            return 0
        run.decide = decide
        return run, state

    @staticmethod
    def probe_live(probe):
        try:
            witness = json.loads((probe / "ready.json").read_text())
            fields = pathlib.Path(f"/proc/{witness['pid']}/stat").read_text().rsplit(")", 1)[1].split()
            return fields[0] != "Z" and fields[19] == witness["birth"]
        except (OSError, ValueError, KeyError, IndexError):
            return False

    def finish_probe(self, probe):
        (probe / "release").touch()
        deadline = time.monotonic() + 3
        while self.probe_live(probe) and time.monotonic() < deadline:
            time.sleep(.01)
        if self.probe_live(probe):
            # Only the generated nonce/birth-identified child is eligible for cleanup.
            witness = json.loads((probe / "ready.json").read_text())
            os.kill(witness["pid"], 9)

    def fsmonitor(self, run, *, only_without_lock):
        probe = run.worktree / ".git" / ("input-probe-" + uuid.uuid4().hex)
        probe.mkdir()
        nonce = uuid.uuid4().hex
        child = probe / "child.py"
        child.write_text(
            "import json,os,pathlib,time\n"
            f"probe=pathlib.Path({str(probe)!r})\n"
            "birth=pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]\n"
            f"data={{'pid':os.getpid(),'birth':birth,'nonce':{nonce!r}}}\n"
            "temporary=probe/'ready.tmp'; temporary.write_text(json.dumps(data)); temporary.replace(probe/'ready.json')\n"
            f"deadline=time.monotonic()+{5 if only_without_lock else .4}\n"
            "while not (probe/'release').exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "if (probe/'release').exists():\n"
            f" source=pathlib.Path({str(run.worktree / 'gate.py')!r})\n"
            " source.write_text(source.read_text()+'# delayed fixture mutation\\n')\n"
            " (probe/'delayed').touch()\n", encoding="utf-8")
        hook = probe / "hook.py"
        hook.write_text(
            "import pathlib,subprocess,sys,time\n"
            f"probe=pathlib.Path({str(probe)!r}); lock=pathlib.Path({str(run.worktree / '.git/ouro-validation/lock')!r})\n"
            f"if ({not only_without_lock!r} or not lock.exists()) and not (probe/'ready.json').exists():\n"
            f" subprocess.Popen([sys.executable,{str(child)!r}],cwd={str(run.worktree)!r},start_new_session=True,"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            " deadline=time.monotonic()+3\n"
            " while not (probe/'ready.json').exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            " assert (probe/'ready.json').exists(),'generated child did not become ready'\n"
            "sys.stdout.buffer.write(b'fixture-token\\0/\\0')\n", encoding="utf-8")
        run.wt_git("config", "core.fsmonitor", sh.cmdline([sys.executable, hook]))
        run.wt_git("config", "core.fsmonitorHookVersion", "2")
        self.addCleanup(self.finish_probe, probe)
        return probe, nonce

    def test_same_head_resume_never_reaches_ready_with_unowned_fsmonitor_work(self):
        self.native()
        # Calibrate the exact old raw-helper behavior without editing production source.
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                run, state = self.resumed_run()
                probe, nonce = self.fsmonitor(run, only_without_lock=True)
                def raw_inputs(current, head):
                    return None if validation.inputs_match(current, head) else 1
                if legacy:
                    with patch.object(validation, "check_inputs", side_effect=raw_inputs):
                        self.assertEqual(Run.drive(run, None), 0)
                    self.assertTrue(state["ready"])
                    self.assertTrue(self.probe_live(probe), "the old helper must demonstrate the live-child escape")
                    self.assertEqual(json.loads((probe / "ready.json").read_text())["nonce"], nonce)
                    self.finish_probe(probe)
                    self.assertTrue((probe / "delayed").exists())
                    self.assertIn("delayed fixture mutation", (run.worktree / "gate.py").read_text())
                else:
                    self.assertEqual(Run.drive(run, None), 0)
                    self.assertTrue(state["ready"])
                    self.assertFalse((probe / "ready.json").exists())
                    self.assertFalse(self.probe_live(probe))
                    self.assertNotIn("delayed fixture mutation", (run.worktree / "gate.py").read_text())

    def test_leased_guard_rejects_an_incomplete_fsmonitor_snapshot(self):
        self.native()
        run, state = self.resumed_run()
        private = run.worktree / ".git" / "ouro-validation"
        before = {str(path.relative_to(private)): path.read_bytes() for path in private.rglob("*") if path.is_file()}
        probe, nonce = self.fsmonitor(run, only_without_lock=False)
        with self.assertRaisesRegex(sh.DriverError, "helper snapshot refused"):
            Run.drive(run, None)
        self.assertFalse(state["ready"])
        self.assertEqual(json.loads((probe / "ready.json").read_text())["nonce"], nonce)
        self.assertEqual(before, {str(path.relative_to(private)): path.read_bytes()
                                 for path in private.rglob("*") if path.is_file()})

    @unittest.skipUnless(os.name == "nt", "actual Windows unsupported caller boundary")
    def test_actual_windows_refuses_proof_before_launch_even_with_consent(self):
        run, command = self.fixture("from pathlib import Path\nPath('output/ran').touch()\n", caller=True)
        self.assertEqual(Run.settled_gates(run, run.head(), [(command, {})], []), 75)
        self.assertEqual(run.execution_results, [])
        self.assertFalse((run.worktree / "output/ran").exists())
        self.assertFalse((run.worktree / ".git" / "ouro-validation").exists())
