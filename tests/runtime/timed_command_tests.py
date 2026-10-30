"""Real timed capture controls, independently bounded by the existing runtime runner."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import venv

from ouro import sh
from ouro.runtime import process


class TimedCommandTests(unittest.TestCase):
    def test_portable_return_codes_utf8_and_timeout(self):
        for code in (0, 7):
            result = sh.run([sys.executable, "-c", "import sys;print('caf\\u00e9');sys.exit(" + str(code) + ")"], timeout=2)
            self.assertEqual(result.code, code)
            self.assertEqual(result.out.strip(), "caf\u00e9")
        result = sh.run([sys.executable, "-c", "import time;time.sleep(5)"], timeout=.1)
        self.assertIsNone(result.code)
        self.assertEqual(result.out, "")
        self.assertEqual(result.err, "timed out after 0.1s")

    def invoke(self, root, python, source, *, unavailable=False, limit=1, lengths_only=False,
               input_text=None, inherited_text=None):
        """The outer Job owns only generated native helpers, never existing work."""
        inner = root / "inner.py"
        inner.write_text(source, encoding="utf-8")
        controller = root / "controller.py"
        controller.write_text(
            "import json,pathlib,sys,threading,time\n"
            f"sys.path.insert(0,{str(Path(__file__).resolve().parents[2] / 'bin')!r})\n"
            "from ouro import sh\nfrom ouro.run import watch_limit\n"
            f"assert watch_limit()=={limit}\n"
            + ("original=sh.os.set_blocking;calls=0\n"
               "def setup(*args):\n"
               " global calls\n calls+=1\n"
               " if calls==3:raise OSError('generated unavailable stdin control')\n"
               " return original(*args)\nsh.os.set_blocking=setup\n" if unavailable == "stdin" else
               "sh.os.set_blocking=lambda *args: (_ for _ in ()).throw(OSError('generated unavailable control'))\n" if unavailable else "")
            # A sleep right after a read that returned data is an active pipe throttled, whatever the host's load.
            + "_read,_sleep=sh.os.read,sh.time.sleep;busy={'last':False,'sleeps':0}\n"
            "def read(*a):\n busy['last']=False;data=_read(*a);busy['last']=bool(data);return data\n"
            "def sleep(d):\n busy['sleeps']+=busy['last'];_sleep(d)\n"
            "sh.os.read,sh.time.sleep=read,sleep\n"
            "before=len(threading.enumerate());start=time.monotonic()\n"
            "try:\n"
            f" r=sh.run([sys.executable,{str(inner)!r}],cwd={str(root)!r},timeout=watch_limit(),input_text={input_text!r})\n"
            + (" record={'code':r.code,'out':len(r.out),'err':len(r.err)}\n" if lengths_only
               else " record={'code':r.code,'out':r.out,'err':r.err}\n")
            +
            "except sh.DriverError as error:record={'error':str(error)}\n"
            "record.update(seconds=time.monotonic()-start,threadsBefore=before,threadsAfter=len(threading.enumerate()),busySleeps=busy['sleeps'])\n"
            "print(json.dumps(record),flush=True)\n", encoding="utf-8")
        result = process.run([str(python), str(controller)], cwd=root, prompt=inherited_text, timeout=limit + 3,
                             env={"OURO_WATCH_LIMIT": str(limit)}, output_limit=16384, stderr_limit=16384)
        self.assertEqual(result.code, 0, result.err)
        record = json.loads(result.out)
        self.assertEqual(record["threadsBefore"], record["threadsAfter"], "capture must not leave reader threads")
        self.assertLess(record["seconds"], limit + 1)
        return record

    def test_input_utf8_empty_inheritance_and_nonzero(self):
        source = ("import hashlib,json,sys\ndata=sys.stdin.buffer.read()\n"
                  "print(json.dumps([len(data),hashlib.sha256(data).hexdigest()]))\n")
        with tempfile.TemporaryDirectory(prefix="ouro-input-contract-") as directory:
            root = Path(directory).resolve()
            for code in (0, 7):
                for number, text in enumerate((None, "", "caf\u00e9\r\n")):
                    with self.subTest(code=code, input=text):
                        case = root / f"{code}-{number}"
                        case.mkdir()
                        inherited = "inherited caf\u00e9\r\n"
                        expected = (inherited if text is None else text).encode("utf-8")
                        answer = self.invoke(case, Path(sys.executable), source + f"sys.exit({code})\n",
                                             input_text=text, inherited_text=inherited, limit=2)
                        self.assertEqual(answer["code"], code)
                        self.assertEqual(json.loads(answer["out"]), [len(expected), hashlib.sha256(expected).hexdigest()])
                        self.assertEqual(answer["err"], "")

    @unittest.skipUnless(os.name == "nt", "actual Windows input pipe backpressure")
    def test_real_base_and_venv_input_deadlines_and_fairness(self):
        payload = "caf\u00e9\r\n" * 131072
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory(prefix="ouro-input-capture-") as directory:
            root = Path(directory).resolve()
            environment = root / "venv"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
            for chain, python in (("base", Path(sys.executable)), ("venv", environment / "Scripts/python.exe")):
                for mode in ("slow-input", "ignored-input", "retained-writer", "closed-input-0", "closed-input-7", "output-backpressure", "unavailable-input"):
                    with self.subTest(chain=chain, mode=mode):
                        case = root / (chain + "-" + mode)
                        case.mkdir()
                        limit, text, lengths = 1, payload, False
                        if mode == "slow-input":
                            source = ("import hashlib,sys,time\nparts=[]\n"
                                      "while part:=sys.stdin.buffer.read(4093):\n parts.append(part);time.sleep(.001)\n"
                                      "print(hashlib.sha256(b''.join(parts)).hexdigest())\n")
                            limit = 3
                        elif mode == "retained-writer":
                            writer = case / "writer.py"
                            writer.write_text("import pathlib,time\npathlib.Path('writer-ready').touch();time.sleep(15)\n", encoding="utf-8")
                            source = ("import pathlib,subprocess,sys,time\n"
                                      f"subprocess.Popen([sys.executable,{str(writer)!r}])\n"
                                      "deadline=time.monotonic()+.7\n"
                                      "while not pathlib.Path('writer-ready').exists() and time.monotonic()<deadline:time.sleep(.01)\n"
                                      "assert pathlib.Path('writer-ready').exists()\n"
                                      "assert sys.stdin.buffer.read()==b'owned input'\n")
                            text = "owned input"
                        elif mode.startswith("closed-input-"):
                            source = f"import os,sys\nos.close(0);print('closed input');sys.exit({mode[-1]})\n"
                        elif mode == "output-backpressure":
                            source = ("import hashlib,os,sys\n"
                                      "for i in range(64):os.write(1,b'x'*65536);os.write(2,b'y'*65536)\n"
                                      f"assert hashlib.sha256(sys.stdin.buffer.read()).hexdigest()=={digest!r}\n")
                            limit, lengths = 3, True
                        else:
                            source = "import time;time.sleep(15)\n"
                        answer = self.invoke(case, python, source, input_text=text, limit=limit,
                                             lengths_only=lengths, unavailable="stdin" if mode == "unavailable-input" else False)
                        if mode in ("ignored-input", "retained-writer"):
                            self.assertEqual((answer["code"], answer["out"], answer["err"]), (None, "", "timed out after 1s"))
                        elif mode == "unavailable-input":
                            self.assertIn("timed command capture unavailable", answer["error"])
                        elif mode.startswith("closed-input-"):
                            self.assertEqual(answer["code"], int(mode[-1]))
                            self.assertEqual(answer["out"].strip(), "closed input")
                        elif mode == "output-backpressure":
                            self.assertEqual((answer["code"], answer["out"], answer["err"]), (0, 4194304, 4194304))
                        else:
                            self.assertEqual(answer["code"], 0)
                            self.assertEqual(answer["out"].strip(), digest)

    @unittest.skipUnless(os.name == "nt", "actual Windows continuously active capture")
    def test_active_pipes_are_not_throttled_by_idle_poll_sleep(self):
        source = "import os\nfor i in range(256):os.write(1,b'x'*65536);os.write(2,b'y'*65536)\n"
        # Counted, not timed: the capture may sleep only when no pipe gave data, so a slow host stays green.
        with tempfile.TemporaryDirectory(prefix="ouro-active-capture-") as directory:
            answer = self.invoke(Path(directory).resolve(), Path(sys.executable), source, limit=20, lengths_only=True)
            self.assertEqual(answer["busySleeps"], 0)
            self.assertEqual(answer["code"], 0)
            self.assertEqual((answer["out"], answer["err"]), (16777216, 16777216))

    @unittest.skipUnless(os.name == "posix", "actual POSIX inherited-writer regression")
    def test_posix_timeout_still_returns_without_descendant_pipe_eof(self):
        with tempfile.TemporaryDirectory(prefix="ouro-posix-capture-") as directory:
            source = ("import subprocess,sys,time\n"
                      "subprocess.Popen([sys.executable,'-c','import time;time.sleep(15)'])\n"
                      "time.sleep(15)\n")
            answer = self.invoke(Path(directory).resolve(), Path(sys.executable), source)
            self.assertIsNone(answer["code"])
            self.assertEqual((answer["out"], answer["err"]), ("", "timed out after 1s"))

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_an_exited_unreaped_group_is_not_running(self):
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import sys;sys.stdin.read()"], stdin=subprocess.PIPE,
                                 start_new_session=True)
        try:
            self.assertTrue(process.group_running(child.pid))
            child.stdin.close()
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)  # exited, left a zombie
            self.assertFalse(process.group_running(child.pid))
        finally:
            child.wait()

    @unittest.skipUnless(os.name == "nt", "actual Windows timed-pipe capture")
    def test_real_base_and_venv_pipes_preserve_eof_and_timeout_contracts(self):
        with tempfile.TemporaryDirectory(prefix="ouro-timed-capture-") as directory:
            root = Path(directory).resolve()
            environment = root / "venv"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
            for chain, python in (("base", Path(sys.executable)), ("venv", environment / "Scripts/python.exe")):
                for mode in ("late-stderr", "zero-parent-writer", "live-parent-writer", "closed-output", "busy-output"):
                    with self.subTest(chain=chain, mode=mode):
                        case = root / (chain + "-" + mode)
                        case.mkdir()
                        if mode in ("late-stderr", "zero-parent-writer", "live-parent-writer"):
                            writer = case / "writer.py"
                            writer.write_text(
                                "import os,pathlib,sys,time\npathlib.Path('writer-ready').touch()\n"
                                + ("os.close(1);time.sleep(.25);sys.stderr.write('late stderr\\n');sys.stderr.flush()\n"
                                   if mode == "late-stderr" else "time.sleep(15)\n"), encoding="utf-8")
                            source = ("import pathlib,subprocess,sys,time\n"
                                      f"subprocess.Popen([sys.executable,{str(writer)!r}])\n"
                                      "deadline=time.monotonic()+.7\n"
                                      "while not pathlib.Path('writer-ready').exists() and time.monotonic()<deadline:time.sleep(.01)\n"
                                      "assert pathlib.Path('writer-ready').exists(),'owned writer not ready'\n"
                                      "print('parent output',flush=True)\n"
                                      + ("time.sleep(15)\n" if mode == "live-parent-writer" else ""))
                        elif mode == "closed-output":
                            source = "import os,time\nos.close(1);os.close(2);time.sleep(15)\n"
                        else:
                            source = "import os\nwhile True:os.write(1,b'x'*65536);os.write(2,b'y'*65536)\n"
                        answer = self.invoke(case, python, source)
                        if mode == "late-stderr":
                            self.assertEqual(answer["code"], 0)
                            self.assertIn("parent output", answer["out"])
                            self.assertIn("late stderr", answer["err"])
                            self.assertGreater(answer["seconds"], .2)
                        else:
                            self.assertIsNone(answer["code"])
                            self.assertEqual((answer["out"], answer["err"]), ("", "timed out after 1s"))
                            self.assertGreaterEqual(answer["seconds"], .9)

    @unittest.skipUnless(os.name == "nt", "actual Windows unavailable nonblocking capture")
    def test_unavailable_capture_refuses_without_reader_threads_or_fallback(self):
        with tempfile.TemporaryDirectory(prefix="ouro-capture-unavailable-") as directory:
            answer = self.invoke(Path(directory).resolve(), Path(sys.executable), "import time;time.sleep(15)\n", unavailable=True)
            self.assertIn("timed command capture unavailable", answer["error"])
