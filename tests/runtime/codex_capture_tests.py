"""Real subprocess capture regressions; no model authentication or receipt writes."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from ouro import developer, sh
from ouro.runtime import READ, codex, codex_events, diagnostics, process


class CodexCaptureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ouro-capture-test-")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        self.root = pathlib.Path(tmp.name).resolve()
        self.fake = self.root / "fake_codex.py"
        self.fake.write_text('''import json,pathlib,sys
mode=sys.argv[1]
answer=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
answer.write_text('{"finding":"bounded fixture","stop":null}',encoding='utf-8')
answer.with_name('tools.jsonl').write_text(json.dumps({'tool':'read','path':'fixture.txt','error':None})+'\\n',encoding='utf-8')
print(json.dumps({'type':'thread.started','thread_id':'capture-fixture'}),flush=True)
if mode in ('large','late-failure','nonzero'):
    frame=json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call','result':'x'*(512*1024)}})
    for i in range(20):print(frame)
if mode=='malformed':answer.write_text('not JSON',encoding='utf-8')
if mode=='large-final':answer.write_text(' '* (4*1024*1024+1),encoding='utf-8')
if mode=='large-frame':print(json.dumps({'type':'item.completed','body':'x'*(8*1024*1024)}))
if mode!='missing':print(json.dumps({'type':'turn.completed','usage':{'input_tokens':3,'output_tokens':1,'cached_input_tokens':2}}),flush=True)
if mode=='late-failure':print(json.dumps({'type':'turn.failed','error':{'message':'late provider failure'}}),flush=True)
if mode=='nonzero':sys.exit(7)
''', encoding="utf-8")

    def execute(self, mode):
        argv_for = sh.argv_for
        def argv(name, *args):
            return [sys.executable, str(self.fake), mode, *args] if name == "codex" else argv_for(name, *args)
        with patch.object(sh, "argv_for", side_effect=argv):
            return codex.execute("CHECKPOINT", "read fixture", "default", READ, self.root, 10, None, {}, developer.Settings("codex"))

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
        for mode, message in (("late-failure", "late provider failure"), ("nonzero", "exit 7"),
                              ("missing", "completion missing"), ("malformed", "final structured result"),
                              ("large-final", "result limit exceeded"), ("large-frame", "frame limit exceeded")):
            with self.subTest(mode=mode), self.assertRaisesRegex(sh.DriverError, message):
                self.execute(mode)

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
        self.assertEqual(len(transcript.failure), 1600)
        self.assertNotIn("discard me", repr(transcript))
        invalid = codex_events.scan(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": {"huge_body": "discard me"}, "output_tokens": True,
            "cached_input_tokens": -1, "cost_usd": float("inf")}}))
        self.assertEqual(invalid.usage, {})
        self.assertEqual(codex_events.scan('{"type":"error","message":""}').failure, "error")
        with patch.object(codex_events, "MAX_FRAMES", 2):
            self.assertIn("count limit", codex_events.scan(output).error)
        with patch.object(codex_events, "FRAME_LIMIT", 60):
            transcript = codex_events.scan(output)
            self.assertIn("frame limit", transcript.error)
            self.assertEqual(transcript.session, "known")


if __name__ == "__main__":
    unittest.main()
