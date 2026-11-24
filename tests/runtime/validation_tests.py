"""Caller-validation refusal tests; WSL and other unsupported platforms are simulated rows."""
import ast
import inspect
import itertools
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

from ouro import developer, sh, validation
from ouro.worktrees import Tree
from ouro.runtime import process as owned_process

HINT = str  # stands in for Run.wait_hint: the wait's text names its commands

GRANTED = "python3 granted.py"
ALLOWED = [GRANTED]
U = {"cmd": "python3 other.py", "reason": "sandbox"}  # well-formed and ungranted: the grant alone would clear it
STOP = "stop: open decision, exit 3"
ARRAY, MALFORMED, INVALID = "validation_pending must be an array", "malformed validation_pending record", "invalid validation command"
UNPARSEABLE, DUPLICATE_ENV = "invalid validation command: ", "duplicate validation environment assignment"
NO_EXECUTABLE, DUPLICATE = "validation command has no executable", "duplicate validation command: `"
CONFLICT, NOT_GRANTED = "validation command conflicts with gates_run", "validation command not granted: `"


def req(cmd, reason="sandbox"):
    return {"cmd": cmd, "reason": reason}


def pend(*items, gates=()):
    """One commit's Ouro-Build facts: these requests, and a gates_run naming `gates`."""
    return {"validation_pending": list(items), **({"gates_run": [{"cmd": g, "exit": 0} for g in gates]} if gates else {})}


G = req(GRANTED)
# One request no grant would clear, and the error `validation.record` or `validation.command` raises for it.
BAD_ITEMS = (
    ("not-a-dict", GRANTED, MALFORMED),
    ("missing-reason", {"cmd": GRANTED}, MALFORMED),
    ("missing-cmd", {"reason": "sandbox"}, MALFORMED),
    ("extra-field", {**G, "note": 1}, MALFORMED),
    ("empty-reason", req(GRANTED, ""), MALFORMED),
    ("blank-reason", req(GRANTED, "  "), MALFORMED),
    ("non-string-reason", req(GRANTED, 1), MALFORMED),
    ("non-string-cmd", req(["python3"]), MALFORMED),
    ("empty-cmd", req(""), INVALID),
    ("blank-cmd", req("   "), INVALID),
    *((f"metacharacter-{ord(c):02x}", req(f"python3 granted.py {c} x"), INVALID) for c in "\n\r\x00;&|<>`$"),
    ("unparseable", req('python3 "unterminated'), UNPARSEABLE),
    ("duplicate-env", req("A=1 A=2 python3 granted.py"), DUPLICATE_ENV),
    ("no-executable", req("A=1 B=2"), NO_EXECUTABLE),
)
# One commit's facts no grant would clear, and the error `validation.requests` raises for them.
BAD_RECORDS = (
    *((name, pend(item), error) for name, item, error in BAD_ITEMS),
    ("not-an-array", {"validation_pending": GRANTED}, ARRAY),
    ("object-not-an-array", {"validation_pending": {}}, ARRAY),
    ("null-not-an-array", {"validation_pending": None}, ARRAY),
    ("duplicate", pend(G, G), DUPLICATE),
    ("duplicate-argv", pend(G, req("python3  granted.py")), DUPLICATE),
    ("gates-run-conflict", pend(G, gates=[GRANTED]), CONFLICT),
)
# (name, via, records oldest first, expected outcome). `branch` runs Run.build_facts over one commit per record (None:
# a commit with no Ouro-Build line); `fresh` runs validation.requests over one BUILD answer with the grant, as Run.build
# does. The outcome is the source message prefix of the DriverError raised, STOP, or None for accepted.
REQUEST_CASES = (
    ("no-record", "branch", [None], None),
    ("empty-list", "branch", [pend()], None),
    ("granted", "branch", [pend(G)], None),
    ("ungranted", "branch", [pend(U)], STOP),
    *((name, "branch", [facts], error) for name, facts, error in BAD_RECORDS),
    # An error beside an ungranted request in one list is raised, in either order.
    *((f"{name}-after-ungranted", "branch", [pend(U, item)], error) for name, item, error in BAD_ITEMS),
    *((f"{name}-before-ungranted", "branch", [pend(item, U)], error) for name, item, error in BAD_ITEMS),
    ("duplicate-ungranted", "branch", [pend(U, U)], DUPLICATE),
    ("ungranted-conflicts-with-gates-run", "branch", [pend(U, gates=[U["cmd"]])], CONFLICT),
    # An error in any commit is raised before an ungranted request in another, older or newer.
    *((f"ungranted-commit-then-{name}", "branch", [pend(U), facts], error) for name, facts, error in BAD_RECORDS),
    *((f"{name}-then-ungranted-commit", "branch", [facts, pend(U)], error) for name, facts, error in BAD_RECORDS),
    # A gates_run conflicts only with its own commit's fresh requests: an earlier valid request is inherited.
    ("gates-run-inherited", "branch", [pend(G), pend(G, gates=[GRANTED])], None),
    ("gates-run-in-earlier-commit", "branch", [pend(gates=[GRANTED]), pend(G)], None),
    ("ungranted-inherited-by-gates-run", "branch", [pend(U), pend(U, gates=[U["cmd"]])], STOP),
    # Well-formed across commits: an ungranted request in any commit stops, naming the oldest such commit.
    ("ungranted-then-granted", "branch", [pend(U), pend(G)], STOP),
    ("granted-then-ungranted", "branch", [pend(G), pend(U)], STOP),
    ("ungranted-repeated", "branch", [pend(U), pend(U)], STOP),
    ("fresh-granted", "fresh", [pend(G)], None),
    ("fresh-ungranted", "fresh", [pend(U)], NOT_GRANTED),
)


def source_outcomes():
    """Each DriverError message prefix and each stop the request checks hold, read from their source. Two raises that share
    a prefix are refused: one row would stand for both, and the second could be left without a case."""
    from ouro.run import Run
    found = []
    for fn in (validation.command, validation.record, validation.requests, Run.require_granted):
        for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and getattr(n.exc.func, "attr", getattr(n.exc.func, "id", None)) == "DriverError":
                message = n.exc.args[0]
                parts = message.values if isinstance(message, ast.JoinedStr) else [message]
                found.append("".join(p.value for p in itertools.takewhile(lambda p: isinstance(p, ast.Constant), parts)))
            elif isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "stop_issue":
                found.append(f"stop: {n.args[0].value}, exit {n.args[2].value}")
    shared = sorted({x for x in found if found.count(x) > 1})
    assert not shared, f"request checks that share an outcome prefix: {shared}"
    return set(found)


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
                        patch.object(validation, "lock") as lease:
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

    def test_every_request_error_is_raised_before_an_ungranted_stop(self):
        from ouro.run import Run
        sources = source_outcomes()
        self.assertTrue(all(sources) and STOP in sources, sources)
        names = [case[0] for case in REQUEST_CASES]
        self.assertEqual(len(names), len(set(names)), "row names are unique")
        tree = self.fixture({})
        comments = []
        run = Run.__new__(Run)
        run.tree, run.n, run.labels = tree, 1, ["agent-ready"]
        run.env = types.SimpleNamespace(default="main", slug="owner/repo", write=lambda *a: None,
                                        comment_issue=lambda n, body: comments.append(body))
        run.claim = types.SimpleNamespace(update=lambda *a, **k: None)
        run.validation_commands, run.ci_commands = lambda: ALLOWED, lambda **kw: set()
        for name, via, records, expected in REQUEST_CASES:
            with self.subTest(name):
                comments.clear()
                tree.git("update-ref", "refs/remotes/origin/main", "HEAD")  # the row's branch: only its own commits
                shas = []
                for r in records if via == "branch" else ():
                    tree.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty",
                             "-qm", name + ("" if r is None else "\n\nOuro-Build: " + json.dumps(r)))
                    shas.append(tree.head())
                try:
                    with patch("ouro.run.say"), patch("ouro.run.argv_for", lambda *a: list(a)):
                        if via == "fresh":
                            validation.requests(records[0]["validation_pending"], ALLOWED, records[0].get("gates_run") or [])
                        else:
                            Run.build_facts(run, evidence=False)
                    got = None
                except sh.DriverError as ex:
                    hits = [s for s in sources if str(ex).startswith(s)]
                    got = max(hits, key=len) if hits else "unattributed: " + str(ex)
                except sh.Stopped as ex:
                    got = f"stop: {comments[0].splitlines()[0].removeprefix('**Stop:** ')}, exit {ex.args[0]}"
                self.assertEqual(got, expected)
                if expected == STOP:
                    sha, cmd = next((sha, p["cmd"]) for sha, r in zip(shas, records) if r
                                    for p in r["validation_pending"] if p["cmd"] not in ALLOWED)
                    self.assertIn(f"The BUILD commit {sha[:7]} requested caller validation of `{cmd}`", comments[0])
        # A DriverError or stop added to the request checks without a row of its own fails here.
        self.assertEqual(sources - {case[3] for case in REQUEST_CASES}, set())

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

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_a_mocked_child_never_reaches_killpg(self):
        # A mocked pid reads as 1, and killpg(1) is kill(-1): every process the user owns.
        with patch.object(validation, "check_platform"), patch.object(validation.subprocess, "Popen") as launch, \
                patch.object(validation.os, "killpg") as killpg:
            launch.return_value.poll.return_value = 0
            launch.return_value.wait.return_value = 0
            self.assertEqual(validation.execute(sys.executable + " -V", self.root, self.root / "attempt.log"), 0)
        launch.assert_called_once()
        killpg.assert_not_called()

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
                patch.object(validation, "lock") as lease:
            # Real BUILD metadata parsing must not trigger receipt/state preflight.
            self.assertEqual(Run.drive(run, None), 4)
            clean.assert_not_called()
            receipt.assert_not_called()
            lease.assert_not_called()


    def test_driver_nonvalidating_wait_keeps_clean_input_barrier(self):
        from ouro.run import Run
        run, pending = self.driver_fixture(validating=False)
        with patch.object(validation, "validate", return_value=([], 75)) as validate:
            self.assertEqual(Run.drive(run, None), 4)
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
            self.assertIsNotNone(kwargs.get("inputs"), "receipt preflight escaped the lock")
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

    @unittest.skipUnless(os.name == "posix", "POSIX PID identities")
    def test_pid_reuse_never_authorizes_cleanup(self):
        # A reused parent PID must neither acquire the new parent's children nor
        # authorize signalling it. The original still-live child remains owned.
        owned = {10: "old-parent", 20: "owned-child"}
        table = {10: (1, "reused-parent"), 20: (1, "owned-child"), 30: (10, "unrelated-child")}
        process = types.SimpleNamespace(wait=lambda: None)
        with patch.object(owned_process, "process_table", return_value=table), \
                patch.object(owned_process.os, "kill") as kill:
            owned_process.terminate_owned(process, owned)
        self.assertEqual(set(owned), {10, 20})
        self.assertEqual([args[0][0] for args in kill.call_args_list], [20, 20])
