"""Real subprocess capture regressions; no model authentication or receipt writes."""
import json
import hashlib
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from ouro import cli, developer, node, sh
from ouro.runtime import READ, PROBE, builder, codex, codex_events, diagnostics, process


class CodexCaptureTests(unittest.TestCase):
    def test_edit_bootstrap_is_additive_and_keeps_repository_text_untrusted(self):
        """Mocked transport contract only; real BUILD usability is opt-in qualification."""
        (self.root / "AGENTS.md").write_text("generated-user-context-marker", encoding="utf-8")
        skill = (sh.PLUGIN / "skills/run/SKILL.md").read_bytes().decode("utf-8-sig")
        answer = dict(subject="generated", body="", claims=[], gates_run=[], validation_pending=[],
                      residue=[], manual=[], door="", blast_radius="", stop=None, replies=[])
        def fake(argv, **kwargs):
            extra = [arg for arg in argv if arg.startswith("developer_instructions=")]
            self.assertEqual(len(extra), 1, "edit-capable worker lacks additive developer bootstrap")
            text = json.loads(extra[0].split("=", 1)[1])
            self.assertIn("write/edit/delete are granted", text)
            self.assertIn("Do not invoke the driver again", text)
            self.assertIn("outer caller", text)
            self.assertIn(skill, text)
            self.assertNotIn("generated-user-context-marker", text)
            self.assertIn("generated-user-context-marker", kwargs["prompt"])
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertEqual(argv[argv.index("--model") + 1], "generated-account-model")
            self.assertIn("features.shell_tool=false", argv)
            self.assertFalse(any(arg.startswith(("model_catalog_json=", "model_instructions_file=")) for arg in argv))
            target = pathlib.Path(argv[argv.index("--output-last-message") + 1])
            policy = json.loads((target.parent / "policy.json").read_text())
            self.assertIs(policy["edit"], True)
            self.assertEqual(policy["commands"], list(builder(["python3 check.py"]).commands))
            target.write_text(json.dumps(answer), encoding="utf-8")
            (target.parent / "tools.jsonl").write_text(json.dumps({"tool": "read", "path": "fixture.txt", "error": None}) + "\n")
            return sh.R(0, '\n'.join(json.dumps(event) for event in (
                {"type": "thread.started", "thread_id": "generated-bootstrap"}, {"type": "turn.completed"})), "", argv)
        # The POSIX dispatch contract is modeled here, never Windows worker admission.
        with patch.object(codex, "WINDOWS", False), patch.object(codex, "git_readiness"), \
                patch.object(sh, "argv_for", side_effect=lambda name, *args: [name, *args]), \
                patch.object(sh, "which", side_effect=lambda name: name), patch.object(process, "run", side_effect=fake):
            result, session, _, _ = codex.execute("BUILD", "generated task", "generated-account-model",
                                                 builder(["python3 check.py"]), self.root, 5, None, {}, developer.Settings("codex"))
        self.assertEqual((result["subject"], session), ("generated", "generated-bootstrap"))

    def test_read_and_probe_bootstrap_never_grants_edit_or_requires_run_skill(self):
        with patch.object(sh, "PLUGIN", self.root / "no-installed-skill"):
            for caps in (READ, PROBE):
                text = codex.bootstrap("REVIEW", caps)
                self.assertNotIn("write/edit/delete are granted", text)
                self.assertNotIn("name: run", text)
                self.assertIn("Editing is not granted", text)

    def test_invalid_trusted_run_skill_refuses_before_dispatch(self):
        installed = self.root / "installed"
        skill = installed / "skills/run/SKILL.md"
        skill.parent.mkdir(parents=True)
        for payload in (None, b"x" * (32 * 1024 + 1), b"\xff", b""):
            with self.subTest(payload=None if payload is None else len(payload)):
                if payload is not None:
                    skill.write_bytes(payload)
                with patch.object(sh, "PLUGIN", installed), patch.object(codex, "WINDOWS", False), \
                        patch.object(codex, "git_readiness") as git_probe, patch.object(process, "run") as launch:
                    with self.assertRaisesRegex(sh.DriverError, "trusted run skill"):
                        codex.execute("BUILD", "generated task", "default", builder([]), self.root, 1, None, {}, developer.Settings("codex"))
                    git_probe.assert_not_called()
                    launch.assert_not_called()
        skill.unlink()
        skill.mkdir()
        with patch.object(sh, "PLUGIN", installed), self.assertRaisesRegex(sh.DriverError, "trusted run skill"):
            codex.bootstrap("BUILD", builder([]))

    def test_trusted_run_skill_cannot_resolve_outside_installed_plugin(self):
        installed = self.root / "installed"
        skill = installed / "skills/run/SKILL.md"
        outside = self.root / "outside-skill.md"
        real_resolve = pathlib.Path.resolve
        def escaped(path, *args, **kwargs):
            return outside if path == skill else real_resolve(path, *args, **kwargs)
        with patch.object(sh, "PLUGIN", installed), patch.object(pathlib.Path, "resolve", escaped):
            with self.assertRaisesRegex(sh.DriverError, "trusted run skill"):
                codex.bootstrap("BUILD", builder([]))

    @unittest.skipUnless(os.name == "nt", "Windows early-closed stdin")
    def test_windows_closed_prompt_reader_preserves_exit_and_releases_cwd(self):
        for code in (0, 7):
            with self.subTest(code=code):
                cwd = self.root / f"closed-input-{code}"
                cwd.mkdir()
                command = ("import os,sys,time; os.close(0); "
                           f"print('reader closed', flush=True); time.sleep(0.3); sys.exit({code})")
                result = process.run([sys.executable, "-c", command], cwd=cwd,
                                     prompt="x" * (1024 * 1024), timeout=5)
                self.assertEqual(result.code, code)
                self.assertEqual(result.out.strip(), "reader closed")
                cwd.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows batch-launcher stdin")
    def test_windows_batch_launcher_receives_exact_prompt_and_eof(self):
        probe = self.root / "stdin probe.py"
        probe.write_text("import hashlib,sys; data=sys.stdin.buffer.read(); "
                         "print(hashlib.sha256(data).hexdigest()); sys.exit(7)", encoding="utf-8")
        for suffix in ("cmd", "bat"):
            launcher = self.root / f"runtime launcher.{suffix}"
            launcher.write_text(f'@echo off\n"{sys.executable}" "{probe}"\n', encoding="utf-8")
            for prompt in ("", "line one\nUnicode: 中文\n", "large prompt\n" * 100000):
                with self.subTest(suffix=suffix, size=len(prompt)):
                    result = process.run([str(launcher)], cwd=self.root, prompt=prompt, timeout=15)
                    self.assertEqual(result.code, 7)
                    self.assertEqual(result.out.strip(), hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    @unittest.skipUnless(os.name == "nt", "Windows stdin backpressure")
    def test_windows_unread_prompt_still_times_out_and_releases_cwd(self):
        cwd = self.root / "blocked-input"
        cwd.mkdir()
        with self.assertRaisesRegex(sh.DriverError, "timed out"):
            process.run([sys.executable, "-c", "import time; time.sleep(30)"],
                        cwd=cwd, prompt="x" * (1024 * 1024), timeout=0.3)
        cwd.rmdir()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ouro-capture-test-")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        self.root = pathlib.Path(tmp.name).resolve()
        self.fake = self.root / "fake_codex.py"
        self.fake.write_text('''import json,pathlib,sys
assert sys.stdin.buffer.read().endswith(b'\u0072ead fixture'), 'prompt transport lost or changed input'
mode=sys.argv[1]
answer=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
answer.write_text('{"finding":"bounded fixture","stop":null}',encoding='utf-8')
answer.with_name('tools.jsonl').write_text(json.dumps({'tool':'read','path':'fixture.txt','error':None})+'\\n',encoding='utf-8')
print(json.dumps({'type':'thread.started','thread_id':'capture-fixture'}),flush=True)
if mode in ('large','late-failure','nonzero'):
    frame=json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call','result':'x'*(512*1024)}})
    for i in range(20):print(frame)
if mode=='budget':
    pathlib.Path('partial.txt').write_text('task-owned partial',encoding='utf-8')
    print(json.dumps({'type':'turn.failed','error':{'message':'Incomplete response returned, reason: max_output_tokens'}}),flush=True)
if mode=='malformed':answer.write_text('not JSON',encoding='utf-8')
if mode=='large-final':answer.write_text(' '* (4*1024*1024+1),encoding='utf-8')
if mode=='large-frame':print(json.dumps({'type':'item.completed','body':'x'*(8*1024*1024)}))
if mode!='missing':print(json.dumps({'type':'turn.completed','usage':{'input_tokens':3,'output_tokens':1,'cached_input_tokens':2}}),flush=True)
if mode=='late-failure':print(json.dumps({'type':'turn.failed','error':{'message':'late provider failure'}}),flush=True)
if mode=='nonzero':sys.exit(7)
''', encoding="utf-8")

    def execute(self, mode, batch=False):
        argv_for = sh.argv_for
        launcher = self.root / "codex launcher.cmd"
        if batch:
            launcher.write_text(f'@echo off\n"{sys.executable}" "{self.fake}" {mode} %*\n', encoding="utf-8")
        def argv(name, *args):
            if name == "codex":
                return [str(launcher), *args] if batch else [sys.executable, str(self.fake), mode, *args]
            return argv_for(name, *args)
        with patch.object(sh, "argv_for", side_effect=argv):
            return codex.execute("CHECKPOINT", "read fixture", "default", READ, self.root, 10, None, {}, developer.Settings("codex"))

    @unittest.skipUnless(os.name == "nt", "Windows Codex batch launcher")
    def test_codex_adapter_consumes_prompt_through_batch_launcher(self):
        answer, session, usage, cost = self.execute("normal", batch=True)
        self.assertEqual(answer, {"finding": "bounded fixture"})
        self.assertEqual(session, "capture-fixture")

    def test_large_legitimate_transcript_uses_final_file_and_preserves_usage(self):
        answer, session, usage, cost = self.execute("large")
        self.assertEqual(answer, {"finding": "bounded fixture"})
        self.assertEqual(session, "capture-fixture")
        self.assertEqual(usage["cache_read_input_tokens"], 2)
        self.assertIsNone(cost)
        self.assertEqual(diagnostics.ATTEMPTS[-1]["events"], 22)
        # A genuine process demonstrates why the former shared 8-MiB cap fails.
        with patch.object(codex_events, "CAPTURE_LIMIT", process.LIMIT):
            with self.assertRaisesRegex(sh.DriverError, "output limit exceeded"):
                self.execute("large")

    def test_capture_does_not_turn_failures_or_invalid_results_into_success(self):
        for mode, message in (("late-failure", "unknown"), ("nonzero", "exit 7"),
                              ("missing", "missing_completion"), ("malformed", "final structured result"),
                              ("large-final", "result limit exceeded"), ("large-frame", "frame limit exceeded")):
            with self.subTest(mode=mode), self.assertRaisesRegex(sh.DriverError, message):
                self.execute(mode)

    def test_real_transport_budget_failure_survives_later_completion_and_final_file(self):
        with self.assertRaisesRegex(sh.DriverError, "output_budget_exhausted"):
            self.execute("budget")
        self.assertEqual((self.root / "partial.txt").read_text(), "task-owned partial")
        row = diagnostics.ATTEMPTS[-1]
        self.assertEqual(row["incomplete_reason"], "output_budget_exhausted")
        self.assertEqual(row["outcome"], "failed")
        self.assertTrue(row["provider_completed"])
        self.assertNotIn("Incomplete response", diagnostics.table())

    def test_incomplete_attempt_cli_rejects_false_completion_and_preserves_edits(self):
        import contextlib
        import io
        self.addCleanup(node.USAGE.clear)
        answer = dict(subject="generated", body="", claims=[], gates_run=[],
                      residue=[], manual=[], door="", blast_radius="")
        for reason, expected in (("max_output_tokens", "output_budget_exhausted"),
                                 ("private-provider-text", "unknown"),
                                 (None, "missing_completion"), ("terminated", "terminated_transport")):
            for completed in (False, True):
                if reason is None and completed:
                    continue
                with self.subTest(reason=reason, completed=completed):
                    def fake(argv, **kwargs):
                        target = pathlib.Path(argv[argv.index("--output-last-message") + 1])
                        target.write_text(json.dumps(answer), encoding="utf-8")
                        # An inert provider models a completed tool edit before its turn fails.
                        (self.root / "partial.txt").write_text("task-owned partial", encoding="utf-8")
                        events = [{"type": "thread.started", "thread_id": "bad|session"}]
                        if reason not in (None, "terminated"):
                            events.append({"type": "turn.failed", "error": {
                                "message": "Incomplete response returned, reason: " + reason}})
                        if completed:
                            events.append({"type": "turn.completed", "usage": {"output_tokens": 7}})
                        return sh.R(9 if reason == "terminated" else 0,
                                    "\n".join(map(json.dumps, events)), "private-stderr", argv)
                    output = io.StringIO()
                    def dispatch(*args):
                        return node._real("BUILD", "generated", "default", builder([]),
                                          self.root, 5, None, developer.Settings("codex"))
                    with patch.object(codex, "WINDOWS", False), patch.object(codex, "git_readiness"), \
                            patch.object(sh, "which", side_effect=lambda name: name), \
                            patch.object(process, "run", side_effect=fake), \
                            patch.object(cli, "bind", return_value=None), patch.object(cli.worktrees, "sweep"), \
                            patch.object(cli.run, "main", side_effect=dispatch), \
                            contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                        self.assertEqual(cli.main(["run", "1"]), 1)
                    row = diagnostics.ATTEMPTS[-1]
                    self.assertEqual(row["incomplete_reason"], expected)
                    self.assertIsNone(row["session"])
                    self.assertEqual(node.USAGE, [])
                    self.assertEqual(row["output"], 7 if completed else None)
                    self.assertIsNone(row["cost"])
                    self.assertEqual((self.root / "partial.txt").read_text(), "task-owned partial")
                    self.assertNotIn("private-", output.getvalue())
                    self.assertIn("verify the old worker ended", output.getvalue())
                    self.assertEqual(list(self.root.glob("*.json")), [])

    def test_generic_capture_and_stderr_keep_their_existing_limit(self):
        command = [sys.executable, "-c", "import sys; sys.stdout.write('x'*(9*1024*1024))"]
        with self.assertRaisesRegex(sh.DriverError, "output limit exceeded"):
            process.run(command, cwd=self.root)
        result = process.run(command, cwd=self.root, output_limit=codex_events.CAPTURE_LIMIT)
        self.assertEqual(len(result.out), 9 * 1024 * 1024)
        command[-1] = "import sys; sys.stderr.write('x'*(9*1024*1024))"
        with self.assertRaisesRegex(sh.DriverError, "output limit exceeded"):
            process.run(command, cwd=self.root, output_limit=codex_events.CAPTURE_LIMIT)
        command[-1] = "import sys; sys.stdout.write('x'*(1024*1024))"
        with self.assertRaisesRegex(sh.DriverError, "output limit exceeded"):
            process.run(command, cwd=self.root, output_limit=512 * 1024)

    @unittest.skipUnless(os.name == "nt", "Windows native process-exit synchronization")
    def test_windows_forced_exit_releases_cwd_before_return(self):
        for name in ("overflow", "timeout", "success", "nonzero"):
            with self.subTest(path=name):
                cwd = self.root / name
                cwd.mkdir()
                if name == "overflow":
                    command = [sys.executable, "-c", "import sys,time; sys.stdout.write('x'*(1024*1024)); sys.stdout.flush(); time.sleep(30)"]
                    with self.assertRaisesRegex(sh.DriverError, "output limit exceeded"):
                        process.run(command, cwd=cwd, output_limit=512 * 1024, timeout=5)
                elif name == "timeout":
                    with self.assertRaisesRegex(sh.DriverError, "timed out"):
                        process.run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=cwd, timeout=0.1)
                else:
                    command = "print('done')" if name == "success" else "raise SystemExit(7)"
                    result = process.run([sys.executable, "-c", command], cwd=cwd)
                    self.assertEqual(result.code, 0 if name == "success" else 7)
                # A process return/cached status must not leave its cwd held.
                # This is the first deletion attempt, without retry or delay.
                cwd.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows native process-exit synchronization")
    def test_windows_exit_wait_requires_kernel_signal_even_with_cached_status(self):
        import _winapi
        from types import SimpleNamespace
        from unittest.mock import Mock
        for status in (_winapi.WAIT_TIMEOUT, 128, OSError("native wait unavailable")):
            process_handle = SimpleNamespace(_handle=12345, returncode=1, wait=Mock())
            options = {"side_effect": status} if isinstance(status, OSError) else {"return_value": status}
            with self.subTest(status=str(status)), patch.object(_winapi, "WaitForSingleObject", **options) as native:
                with self.assertRaisesRegex(sh.DriverError, "exit not established|cannot verify runtime process exit"):
                    process._wait_windows_exit(process_handle)
                native.assert_called_once_with(12345, process.WINDOWS_EXIT_WAIT_MS)
                process_handle.wait.assert_not_called()
        process_handle = SimpleNamespace(_handle=12345, returncode=1, wait=Mock())
        with patch.object(_winapi, "WaitForSingleObject", return_value=_winapi.WAIT_OBJECT_0) as native:
            process._wait_windows_exit(process_handle)
            native.assert_called_once_with(12345, process.WINDOWS_EXIT_WAIT_MS)
            process_handle.wait.assert_called_once_with(timeout=0)

    @unittest.skipUnless(os.name == "nt", "Windows native process-exit synchronization")
    def test_windows_exit_proof_failure_retains_original_output_failure(self):
        cwd = self.root / "cleanup-failure"
        cwd.mkdir()
        real_wait = process._wait_windows_exit
        def refuse_after_owned_exit(child):
            real_wait(child)
            raise sh.DriverError("native exit proof refused control")
        command = [sys.executable, "-c", "import sys,time; sys.stdout.write('x'*(1024*1024)); sys.stdout.flush(); time.sleep(30)"]
        with patch.object(process, "_wait_windows_exit", side_effect=refuse_after_owned_exit):
            with self.assertRaisesRegex(sh.DriverError, "output limit exceeded; native exit proof refused control"):
                process.run(command, cwd=cwd, output_limit=512 * 1024, timeout=5)
        cwd.rmdir()

    def test_budget_code_is_recognized_without_retaining_provider_message(self):
        for error in ({"code": "max_output_tokens", "message": "private-provider-text"},
                      {"code": "max_output_tokens"},
                      {"message": "max_output_tokens"}):
            with self.subTest(error=error):
                transcript = codex_events.scan("\n".join(map(json.dumps, [
                    {"type": "turn.failed", "error": error},
                    {"type": "turn.completed", "usage": {}},
                    {"type": "error", "message": "private-provider-text"},
                ])))
                self.assertEqual(transcript.failure, "output_budget_exhausted")
                self.assertNotIn("private-provider-text", repr(transcript))

    def test_projection_discards_items_and_bounds_metadata_and_frames(self):
        output = '\n'.join(map(json.dumps, [
            {"type": "thread.started", "thread_id": "known"},
            {"type": "item.completed", "item": {"secret_fixture_body": "discard me"}},
            {"type": "turn.completed", "usage": {"input_tokens": 3, "unknown": "discard me"}},
            {"type": "turn.failed", "error": {"message": "x" * 2000}},
        ]))
        transcript = codex_events.scan(output)
        self.assertEqual(transcript.count, 4)
        self.assertEqual(transcript.usage, {"input_tokens": 3})
        self.assertEqual(transcript.failure, "unknown")
        self.assertNotIn("discard me", repr(transcript))
        invalid = codex_events.scan(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": {"huge_body": "discard me"}, "output_tokens": True,
            "cached_input_tokens": -1, "cost_usd": float("inf")}}))
        self.assertEqual(invalid.usage, {})
        self.assertEqual(codex_events.scan('{"type":"error","message":""}').failure, "unknown")
        with patch.object(codex_events, "MAX_FRAMES", 2):
            self.assertIn("count limit", codex_events.scan(output).error)
        with patch.object(codex_events, "FRAME_LIMIT", 60):
            transcript = codex_events.scan(output)
            self.assertIn("frame limit", transcript.error)
            self.assertEqual(transcript.session, "known")


if __name__ == "__main__":
    unittest.main()
