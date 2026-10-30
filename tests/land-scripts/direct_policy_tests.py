"""Generated Git histories exercise the read-only direct policy, not publication."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "bin/ouro-direct.py"
if not POLICY.is_file():
    POLICY = ROOT / "ouro-direct.py"  # Same public helper beside the flat vendored scripts.
spec = importlib.util.spec_from_file_location("direct_policy", POLICY)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
RUN = shlex.quote(sys.executable) + " --version"
CODE_RUN = shlex.quote(sys.executable) + " checks/code.py"
CODE_PROGRAM = "import runpy\nassert runpy.run_path('src/a.py')['answer']() == 2\n"


def binding(*, direct=("docs/*",), gates=None, limits=""):
    text = ('schema = 1\n[repo]\nslug = "o/r"\ndefault_branch = "master"\n'
            '[ship]\npolicy = "stop-at-pr"\nreview = "none"\n'
            f'direct = {json.dumps(direct)}\n{limits}\n[owner]\nruling_approvers = ["owner"]\n')
    for gate in gates if gates is not None else [{"cheap": True}]:
        values = {"run": RUN, **gate}
        text += "\n[[gate]]\n" + "".join(key + " = " + json.dumps(value) + "\n" for key, value in values.items())
    return text


class DirectPolicyTests(unittest.TestCase):
    def test_unreadable_external_tool_metadata_does_not_erase_ci_obligations(self):
        alias = self.home / 'external-tool.exe'
        alias.write_bytes(b'fixture metadata only; never executable proof')
        alias.chmod(0o755)
        command = shlex.quote(str(alias)) + ' --version'
        base = self.start(gates=[{'cheap': True, 'run': command}])
        head = self.commit({'docs/change.md': 'small\n'})
        real_resolve = Path.resolve
        def unreadable(path, *args, **kwargs):
            if path == alias:
                raise OSError(22, 'unreadable external application alias')
            return real_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', unreadable):
            plan = policy.plan(self.repo, base, head)
            self.assertTrue(plan['eligible'], plan)
            self.assertEqual([row['run'] for row in plan['commands']], [command])
            with self.assertRaises(OSError):
                policy._execution_pin(self.repo, str(alias))
            checked = policy.check(self.repo, base, head)
        self.assertFalse(checked['eligible'])
        self.assertEqual((checked['route'], checked['checks'], checked['outputs']), ('pr', [], []))
        self.assertIs(checked['execution_proof'], False)

    def test_unreadable_checkout_tool_metadata_still_refuses_structural_eligibility(self):
        alias = self.repo / 'owned-tool.exe'
        alias.write_bytes(b'owned fixture metadata')
        alias.chmod(0o755)
        command = shlex.quote(str(alias)) + ' --version'
        base = self.start(gates=[{'cheap': True, 'run': command}])
        head = self.commit({'docs/change.md': 'small\n'})
        real_resolve = Path.resolve
        intercepted = []
        def unreadable(path, *args, **kwargs):
            if path == alias:
                intercepted.append(path)
                raise OSError(22, 'unreadable checkout application alias')
            return real_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', unreadable):
            plan = policy.plan(self.repo, base, head)
        self.assertTrue(intercepted, 'the checkout metadata refusal was actually exercised')
        self.assertFalse(plan['eligible'], plan)

    def test_unreadable_external_parent_cannot_prove_external_gate_ownership(self):
        alias = self.home / "external-tool.exe"
        alias.write_bytes(b"fixture metadata only")
        alias.chmod(0o755)
        base = self.start(gates=[{"cheap": True, "run": shlex.quote(str(alias)) + " --version"}])
        head = self.commit({"docs/change.md": "small\n"})
        real_resolve = Path.resolve
        def unreadable(path, *args, **kwargs):
            if path in (alias, alias.parent):
                raise OSError(22, "unprovable gate parent identity")
            return real_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", unreadable):
            result = policy.plan(self.repo, base, head)
        self.assertFalse(result["eligible"], result)
        self.assertIn("unprovable gate parent", " ".join(result["reasons"]))

    @unittest.skipUnless(os.name == "nt", "actual Windows short-path aliases")
    def test_unreadable_raw_short_checkout_tool_and_operand_remain_owned(self):
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        kernel.GetShortPathNameW.restype = ctypes.c_uint
        self.git("config", "core.longpaths", "true")  # The owned TEMP may itself be deeply nested.
        directory = self.home / "owned checkout long directory"
        self.repo.rename(directory)
        self.repo = directory
        marker = self.home / "owned-command-ran"
        tool = self.repo / "owned command launcher.cmd"
        operand = self.repo / "owned command script.py"
        tool.write_text(f'@echo ran>"{marker}"\n', encoding="utf-8")
        operand.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
        (self.repo / "__main__.py").write_bytes(operand.read_bytes())
        for kind, target in (("executable", tool), ("operand", operand), ("directory", directory)):
            with self.subTest(kind=kind):
                buffer = ctypes.create_unicode_buffer(32768)
                self.assertTrue(kernel.GetShortPathNameW(str(target), buffer, len(buffer)))
                raw = Path(buffer.value)
                if raw == target:
                    self.skipTest("owned filesystem has no distinct 8.3 spelling")
                self.assertTrue(raw.samefile(target))
                command = shlex.join([str(raw)] if kind == "executable" else [sys.executable, str(raw)])
                base = self.start(gates=[{"cheap": True, "run": command}])
                head = self.commit({"docs/change.md": kind + "\n"})
                real_resolve, intercepted = Path.resolve, []
                def unreadable(path, *args, **kwargs):
                    if path == raw:
                        intercepted.append(path)
                        raise OSError(22, "unreadable raw-short checkout metadata")
                    return real_resolve(path, *args, **kwargs)
                # The 8.3 identity is real; only the leaf metadata failure is modeled.
                with patch.object(Path, "resolve", unreadable):
                    result = policy.plan(self.repo, base, head)
                self.assertTrue(intercepted, result)
                self.assertFalse(result["eligible"], result)
                self.assertIn("requires a PR", " ".join(result["reasons"]))
                self.assertFalse(marker.exists())

    def test_missing_ci_toolchain_keeps_portable_obligations_but_no_local_success(self):
        missing = "ouro-missing-ci-toolchain-fixture"
        self.assertIsNone(shutil.which(missing))
        command = missing + " checks/code.py"
        self.start(direct=("src/*",), gates=[{"cheap": True},
            {"run": command, "ci": "repository-ci-build", "paths": ["src/*"], "direct_code_check": True}])
        base = self.commit({"checks/code.py": CODE_PROGRAM})
        head = self.commit({"src/a.py": "def answer():\n    return 2\n"})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["code_coverage"], {"src/a.py": [2]})
        self.assertEqual(result["commands"][1]["argv"], [missing, "checks/code.py"])
        self.assertEqual(result["commands"][1]["run"], command)
        self.assertEqual(result["commands"][1]["gates"], [2])
        actual = policy.check(self.repo, base, head)
        self.assertFalse(actual["eligible"], actual)
        self.assertEqual((actual["route"], actual["checks"], actual["outputs"]), ("pr", [], []))
        self.assertFalse(actual["execution_proof"])
        self.assertFalse((self.repo / ".git/ouro-validation").exists())

    def test_equivalent_argv_retains_distinct_declared_command_authority(self):
        first, second = RUN, RUN.replace(" --version", "  --version")
        self.assertEqual(shlex.split(first), shlex.split(second))
        base = self.start(gates=[{"cheap": True, "run": first}, {"cheap": True, "run": second}])
        self.commit({"docs/change.md": "small\n"})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual([(row["run"], row["gates"]) for row in result["commands"]], [(first, [1]), (second, [2])])

    def test_execution_pin_detects_real_external_replacement_and_path_redirection(self):
        name = "fixture-check.cmd" if os.name == "nt" else "fixture-check"
        tools = [self.home / "first", self.home / "second"]
        for directory in tools:
            directory.mkdir()
            tool = directory / name
            tool.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(tools[0]) + os.pathsep + os.environ["PATH"]}):
            before = policy._execution_pin(self.repo, name)
            target = tools[0] / name
            target.write_bytes(target.read_bytes() + b"\n# replacement bytes\n")
            self.assertNotEqual(policy._execution_pin(self.repo, name), before)
        with patch.dict(os.environ, {"PATH": str(tools[1]) + os.pathsep + os.environ["PATH"]}):
            self.assertNotEqual(policy._execution_pin(self.repo, name)[0], before[0])

    @unittest.skipUnless(os.name == "posix", "actual inline mutation control requires POSIX caller admission")
    def test_actual_check_rejects_executable_changed_by_successful_command(self):
        source = POLICY.parent / "ouro-process.py"
        if not source.is_file():
            source = POLICY.parent / "ouro/runtime/process.py"
        spec = importlib.util.spec_from_file_location("mutation_admission_control", source)
        executor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(executor)
        try:
            executor.check_platform()
        except executor.DriverError as error:
            self.skipTest(str(error))
        tool = self.home / "fixture-check"
        program = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n'
        tool.write_text(program, encoding="utf-8")
        tool.chmod(0o755)
        command = shlex.join([str(tool), "checks/change.py"])
        self.start(gates=[{"cheap": True, "run": command}])
        base = self.commit({"checks/change.py": "from pathlib import Path\nPath(" + repr(str(tool))
                            + ").write_text(" + repr(program + "# changed\n")
                            + ")\nPath('.git/command-ran').touch()\n"})
        head = self.commit({"docs/change.md": "small\n"})
        self.assertTrue(policy.plan(self.repo, base, head)["eligible"])
        result = policy.check(self.repo, base, head)
        self.assertTrue((self.repo / ".git/command-ran").exists(), result)
        self.assertEqual([row["exit"] for row in result["checks"]], [0])
        self.assertFalse(result["eligible"], result)
        self.assertIn("executable identity changed", " ".join(result["reasons"]))
        self.assertFalse(result["execution_proof"])
        self.assertFalse((self.repo / ".git/ouro-validation").exists())

    def test_real_inline_execution_obeys_caller_admission_but_plan_is_portable(self):
        command = shlex.quote(sys.executable) + " checks/inline.py"
        self.start(gates=[{"cheap": True, "run": command}])
        base = self.commit({"checks/inline.py": "from pathlib import Path\nPath('.git/inline-ran').touch()\n"})
        self.commit({"docs/change.md": "small\n"})
        head = self.git("rev-parse", "HEAD")
        self.assertTrue(policy.plan(self.repo, base, head)["eligible"])
        source = POLICY.parent / "ouro-process.py"
        if not source.is_file():
            source = POLICY.parent / "ouro/runtime/process.py"
        spec = importlib.util.spec_from_file_location("inline_admission_control", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            module.check_platform()
        except module.DriverError:
            admitted = False
        else:
            admitted = True
        result = policy.check(self.repo, base, head)
        self.assertIs(result["execution_proof"], False)
        self.assertEqual(result["eligible"], admitted, result)
        self.assertEqual((self.repo / ".git/inline-ran").exists(), admitted)
        self.assertEqual(len(result["checks"]), 1 if admitted else 0)
        self.assertFalse((self.repo / ".git/ouro-validation").exists())
        if not admitted:
            self.assertEqual((result["route"], result["outputs"]), ("pr", []))

    @unittest.skipUnless(os.name == "posix", "WSL kernel-fact simulation requires POSIX")
    def test_native_launcher_does_not_admit_a_wsl_execution_context(self):
        command = shlex.quote(sys.executable) + " checks/inline.py"
        self.start(gates=[{"cheap": True, "run": command}])
        base = self.commit({"checks/inline.py": "from pathlib import Path\nPath('.git/inline-ran').touch()\n"})
        self.commit({"docs/change.md": "small\n"})
        head = self.git("rev-parse", "HEAD")
        kernel = types.SimpleNamespace(sysname="Linux", release="fixture", version="Microsoft")
        with patch.object(os, "uname", return_value=kernel):
            self.assertTrue(policy.plan(self.repo, base, head)["eligible"])
            result = policy.check(self.repo, base, head)
        self.assertFalse(result["eligible"])
        # Without /proc (Darwin) the visibility check refuses first, before the kernel is read.
        self.assertIn("WSL" if os.path.isdir("/proc") else "platform visibility unavailable", " ".join(result["reasons"]))
        self.assertEqual(result["checks"], [])
        self.assertFalse((self.repo / ".git/inline-ran").exists())

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="ouro-direct-policy-")
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name).resolve()  # Service TEMP may use a real 8.3 alias.
        config = self.home / "gitconfig"
        config.write_text("", encoding="utf-8")
        environment = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
                                               "PYTHONDONTWRITEBYTECODE": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        # A synthetic Git world must not inherit a caller's repository pointers.
        # Production planning still refuses them; dedicated cases set them explicitly.
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_COUNT"):
            os.environ.pop(name, None)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "master")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@invalid")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, stdin=None):
        p = subprocess.run(["git", *map(str, args)], cwd=self.repo, input=stdin,
                           capture_output=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("utf-8", "strict").strip()

    def commit(self, files):
        for path, value in files.items():
            target = self.repo / path
            if value is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(value.encode("utf-8") if isinstance(value, str) else value)
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def start(self, **options):
        return self.commit({".claude/ouro.toml": binding(**options), "seed.txt": "baseline\n"})

    def evaluate(self, base):
        before = self.git("rev-parse", "HEAD"), self.git("status", "--porcelain")
        result = policy.plan(self.repo, base, before[0])
        self.assertEqual((self.git("rev-parse", "HEAD"), self.git("status", "--porcelain")), before)
        self.assertFalse(result["execution_proof"])
        self.assertEqual(result["kind"], "plan")
        return result

    def test_exact_twenty_lines_three_files_and_no_ci_substitution(self):
        base = self.start(gates=[{"cheap": True, "ci": "unavailable-ci-notice"}])
        self.commit({f"docs/{i}.md": "line\n" * n for i, n in enumerate((7, 7, 6))})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual((result["lines"], result["files"]), (20, 3))
        self.assertEqual(result["commands"][0]["argv"], shlex.split(RUN))
        self.assertTrue(result["commands"][0]["must_pass"])

    def test_twenty_one_lines_and_four_files_require_pr(self):
        base = self.start()
        self.commit({"docs/a.md": "line\n" * 21})
        self.assertIn("limit", " ".join(self.evaluate(base)["reasons"]))
        self.git("reset", "--hard", base)
        self.commit({f"docs/{i}.md": "line\n" for i in range(4)})
        self.assertEqual(self.evaluate(base)["route"], "pr")

    def test_trusted_size_overrides_and_changelog_is_counted(self):
        base = self.start(direct=("docs/*", "CHANGELOG.md"), limits="direct_max_lines = 22\ndirect_max_files = 4")
        self.commit({"CHANGELOG.md": "line\n" * 19, **{f"docs/{i}.md": "line\n" for i in range(3)}})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual((result["lines"], result["files"]), (22, 4))

    def test_transient_paths_and_cancelled_lines_do_not_disappear(self):
        base = self.start()
        self.commit({"docs/transient.md": "line\n" * 11})
        self.commit({"docs/transient.md": None, "docs/final.md": "line\n"})
        result = self.evaluate(base)
        self.assertEqual((result["lines"], result["files"]), (23, 2))
        self.assertIn("docs/transient.md", result["paths"])
        self.assertFalse(result["eligible"])

    def test_transient_protected_path_and_candidate_binding_cannot_authorize(self):
        base = self.start()
        self.commit({".github/workflows/x.yml": "name: x\n"})
        self.commit({".github/workflows/x.yml": None, "docs/final.md": "line\n"})
        self.assertIn("protected", " ".join(self.evaluate(base)["reasons"]))
        self.git("reset", "--hard", base)
        self.commit({".claude/ouro.toml": binding(direct=("src/*",)), "src/a.py": "pass\n"})
        result = self.evaluate(base)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["binding_blob"], self.git("rev-parse", base + ":.claude/ouro.toml"))

    def test_binary_invalid_utf8_and_transient_binary_refuse(self):
        base = self.start()
        for index, content in enumerate((b"\x00binary\n", b"\xff\n")):
            with self.subTest(content=index):
                self.git("reset", "--hard", base)
                self.commit({"docs/a.md": content})
                self.assertFalse(self.evaluate(base)["eligible"])
        self.commit({"docs/a.md": None, "docs/final.md": "line\n"})
        self.assertFalse(self.evaluate(base)["eligible"])

    def test_executable_mode_change_is_not_regular_text_change(self):
        base = self.start()
        self.commit({"docs/a.md": "text\n"})
        before = self.git("rev-parse", "HEAD")
        self.git("update-index", "--chmod=+x", "docs/a.md")
        self.git("commit", "-qm", "mode")
        self.assertFalse(self.evaluate(before)["eligible"])
    def test_symlink_is_not_regular_text_change(self):
        base = self.start()
        link = self.repo / "docs/link.md"
        link.parent.mkdir(exist_ok=True)
        try:
            link.symlink_to("../seed.txt")
        except OSError as error:
            self.skipTest("symlink creation unavailable: " + str(error.winerror if hasattr(error, "winerror") else error.errno))
        self.git("add", "-A")
        self.git("commit", "-qm", "link")
        self.assertFalse(self.evaluate(base)["eligible"])

    def test_missing_applicable_cheap_floor_refuses(self):
        for index, gates in enumerate(([], [{"cheap": True, "paths": ["other/"]}])):
            base = self.start(gates=gates)
            self.commit({f"docs/{index}.md": "text\n"})
            self.assertIn("cheap", " ".join(self.evaluate(base)["reasons"]))

    def test_code_needs_explicit_scoped_check_and_it_need_not_be_cheap(self):
        base = self.start(direct=("src/*",))
        self.commit({"src/a.py": "pass\n"})
        self.assertIn("code", " ".join(self.evaluate(base)["reasons"]))
        scoped = [{"cheap": True}, {"run": CODE_RUN, "paths": [":(glob)src/**/*.py"], "direct_code_check": True}]
        self.start(direct=("src/*",), gates=scoped)
        base = self.commit({"checks/code.py": CODE_PROGRAM})
        before = subprocess.run(shlex.split(CODE_RUN), cwd=self.repo, capture_output=True, timeout=10)
        self.assertNotEqual(before.returncode, 0)
        self.commit({"src/a.py": "def answer():\n    return 2\n"})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["code_coverage"], {"src/a.py": [2]})
        self.assertEqual(len(result["commands"]), 2)
        self.assertFalse(result["commands"][1]["cheap"])
        after = subprocess.run(result["commands"][1]["argv"], cwd=self.repo, capture_output=True, timeout=10)
        self.assertEqual(after.returncode, 0, after.stderr)

    def test_partial_code_coverage_and_transient_code_require_all_checks(self):
        self.start(direct=("src/*",), gates=[{"cheap": True},
            {"direct_code_check": True, "paths": ["src/a.py"], "run": CODE_RUN}])
        base = self.commit({"checks/code.py": CODE_PROGRAM})
        self.commit({"src/a.py": "def answer():\n    return 2\n", "src/b.py": "pass\n"})
        self.commit({"src/b.py": None})
        result = self.evaluate(base)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["code_coverage"]["src/b.py"], [])

    def test_invalid_pathspec_and_dirty_inputs_refuse(self):
        base = self.start(gates=[{"cheap": True, "paths": [":(notvalid)x"]}])
        self.commit({"docs/change.md": "text\n"})
        self.assertFalse(self.evaluate(base)["eligible"])
        (self.repo / "untracked.txt").write_text("dirty", encoding="utf-8")
        self.assertIn("clean", " ".join(self.evaluate(base)["reasons"]))

    def test_gate_scripts_policy_helpers_and_empty_direct_are_protected(self):
        for direct in (("bin/ouro-direct.py",), ("bin/Push-Direct.ps1",), ("gates/check.py",)):
            base = self.start(direct=direct, gates=[{"cheap": True, "run": RUN + " gates/check.py"}])
            self.commit({direct[0]: "text\n"})
            self.assertFalse(self.evaluate(base)["eligible"])
        base = self.start(direct=())
        self.commit({"docs/a.md": "text\n"})
        self.assertIn("disabled", " ".join(self.evaluate(base)["reasons"]))

    def test_exact_head_and_ancestor_are_required(self):
        base = self.start()
        head = self.commit({"docs/a.md": "text\n"})
        self.assertFalse(policy.plan(self.repo, base, base)["eligible"])
        self.assertFalse(policy.plan(self.repo, head, base)["eligible"])
        self.assertFalse(policy.plan(self.repo, "HEAD~1", head)["eligible"])

    def test_command_argv_is_data_and_duplicates_keep_all_gate_obligations(self):
        checker = self.home / "argument check.py"
        checker.write_text("import sys\nprint(sys.argv[-1])\n", encoding="utf-8")
        command = shlex.join([sys.executable, str(checker), "two words"])
        base = self.start(gates=[{"cheap": True, "run": command}, {"cheap": True, "run": command}])
        self.commit({"docs/a.md": "text\n"})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(len(result["commands"]), 1)
        self.assertEqual(result["commands"][0]["gates"], [1, 2])
        self.assertEqual(result["commands"][0]["argv"][-1], "two words")
        actual = subprocess.run(result["commands"][0]["argv"], capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual((actual.returncode, actual.stdout), (0, "two words\n"))

    def test_rename_counts_both_paths_and_preserves_unicode_identity(self):
        self.start()
        base = self.commit({"docs/old.md": "line\n"})
        self.git("mv", "docs/old.md", "docs/café.md")
        self.git("commit", "-qm", "rename")
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual((result["lines"], result["paths"]), (2, ["docs/café.md", "docs/old.md"]))

    def test_nonlinear_history_refuses_instead_of_guessing_merge_size(self):
        base = self.start()
        self.git("checkout", "-qb", "side")
        self.commit({"docs/side.md": "line\n"})
        self.git("checkout", "-q", "master")
        self.commit({"docs/main.md": "line\n"})
        self.git("merge", "--no-ff", "-qm", "merge", "side")
        self.assertIn("nonlinear", " ".join(self.evaluate(base)["reasons"]))

    def test_hidden_untracked_inputs_and_redirected_index_are_not_clean_proof(self):
        base = self.start()
        self.commit({"docs/a.md": "line\n"})
        self.git("config", "status.showUntrackedFiles", "no")
        (self.repo / "hidden.txt").write_text("dirty", encoding="utf-8")
        self.assertIn("clean", " ".join(self.evaluate(base)["reasons"]))
        (self.repo / "hidden.txt").unlink()
        with patch.dict(os.environ, {"GIT_INDEX_FILE": str(self.home / "other-index")}):
            result = policy.plan(self.repo, base, self.git("rev-parse", "HEAD"))
        self.assertIn("redirected", " ".join(result["reasons"]))

    def test_actual_vendored_location_is_protected_without_excluding_ordinary_docs(self):
        self.start(direct=("automation/*",))
        files = {"automation/" + name: (POLICY.parent / name).read_text(encoding="utf-8")
                 for name in ("ouro-direct.py", "ouro-binding.py")}
        base = self.commit(files)
        location = self.repo / "automation/ouro-direct.py"
        spec = importlib.util.spec_from_file_location("vendored_policy", location)
        vendored = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vendored)
        head = self.commit({"automation/ordinary.md": "line\n"})
        result = vendored.plan(self.repo, base, head)
        self.assertTrue(result["eligible"], result)
        head = self.commit({"automation/ouro-direct.py": files["automation/ouro-direct.py"] + "\n# changed\n"})
        result = vendored.plan(self.repo, base, head)
        self.assertFalse(result["eligible"])
        self.assertIn("protected", " ".join(result["reasons"]))

    def test_public_cli_outputs_only_a_plan_and_leaves_inputs_untouched(self):
        base = self.start()
        head = self.commit({"docs/a.md": "line\n"})
        for proposed, code, route in ((head, 0, "direct"), (base, 3, "pr")):
            result = subprocess.run([sys.executable, str(POLICY), "--repo", str(self.repo),
                                     "--base", base, "--head", proposed], capture_output=True,
                                    encoding="utf-8", errors="strict", timeout=30)
            self.assertEqual((result.returncode, result.stderr), (code, ""))
            value = json.loads(result.stdout)
            self.assertEqual((value["kind"], value["route"], value["execution_proof"]), ("plan", route, False))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse(list(self.repo.rglob("__pycache__")))

    def test_configured_filter_is_calibrated_then_refused_before_metadata_side_effect(self):
        self.start()
        base = self.commit({".gitattributes": "docs/*.md filter=owned\n", "docs/a.md": "line\n"})
        head = self.commit({"docs/a.md": "changed\n"})
        marker = self.home / "filter-ran"
        script = "import pathlib,sys; pathlib.Path(" + repr(str(marker)) + ").write_text('ran'); sys.stdout.buffer.write(sys.stdin.buffer.read())"
        self.git("config", "filter.owned.clean", shlex.join([sys.executable, "-c", script]))
        self.git("hash-object", "--path=docs/a.md", "docs/a.md")
        self.assertTrue(marker.is_file(), "positive configured-helper control")
        marker.unlink()
        os.utime(self.repo / "docs/a.md", (time.time() + 2, time.time() + 2))
        result = policy.plan(self.repo, base, head)
        self.assertFalse(result["eligible"])
        self.assertIn("filters", " ".join(result["reasons"]))
        self.assertFalse(marker.exists(), "planner must refuse before status can run the configured helper")

    def test_promisor_and_indexed_submodule_state_refuse_without_fetch(self):
        base = self.start()
        head = self.commit({"docs/a.md": "line\n"})
        self.git("config", "remote.unreachable.promisor", "true")
        result = policy.plan(self.repo, base, head)
        self.assertIn("promisor", " ".join(result["reasons"]))
        self.git("config", "--unset", "remote.unreachable.promisor")
        (self.repo / "nested").mkdir()
        self.git("update-index", "--add", "--cacheinfo", "160000," + base + ",nested")
        self.git("commit", "-qm", "gitlink")
        result = policy.plan(self.repo, base, self.git("rev-parse", "HEAD"))
        self.assertFalse(result["eligible"])
        self.assertIn("submodule", " ".join(result["reasons"]))

    def test_checkout_shadow_git_is_never_started(self):
        self.start()
        marker = self.home / "fake-git-ran"
        fake = self.repo / ("git.cmd" if os.name == "nt" else "git")
        fake.write_text(("@echo fake>\"" + str(marker) + "\"\n@exit /b 0\n") if os.name == "nt"
                        else "#!/bin/sh\nprintf fake > " + shlex.quote(str(marker)) + "\n", encoding="utf-8")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.repo) + os.pathsep + os.environ["PATH"]}):
            self.assertEqual(Path(shutil.which("git")).resolve().parent, self.repo)
            with self.assertRaisesRegex(policy.Unproved, "inside the candidate"):
                policy._git(self.repo, "--version")
        self.assertFalse(marker.exists())

    def test_graft_cannot_hide_a_transient_protected_commit(self):
        base = self.start()
        self.commit({".github/workflows/transient.yml": "name: x\n"})
        head = self.commit({".github/workflows/transient.yml": None, "docs/a.md": "line\n"})
        (self.repo / ".git/info/grafts").write_text(head + " " + base + "\n", encoding="ascii")
        result = policy.plan(self.repo, base, head)
        self.assertFalse(result["eligible"])
        self.assertIn("committed parent", " ".join(result["reasons"]))

    def test_compound_run_cannot_drop_a_mandatory_check(self):
        marker = self.home / "real-check-ran"
        checker = self.home / "real_check.py"
        checker.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('ran')\nraise SystemExit(7)\n", encoding="utf-8")
        compound = shlex.quote(sys.executable) + ' -c "pass" && ' + shlex.join([sys.executable, str(checker)])
        control = subprocess.run([sys.executable, str(checker)], capture_output=True, timeout=10)
        self.assertEqual(control.returncode, 7)
        self.assertTrue(marker.exists(), "positive genuine-check control")
        marker.unlink()
        weakened = subprocess.run(shlex.split(compound), capture_output=True, timeout=10)
        self.assertEqual(weakened.returncode, 0)
        self.assertFalse(marker.exists(), "plain argv silently drops the declared second check")
        base = self.start(gates=[{"cheap": True, "run": compound}])
        self.commit({"docs/a.md": "line\n"})
        result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("grammar", " ".join(result["reasons"]))

    def test_run_and_ci_protect_normalized_owned_gate_scripts(self):
        for field in ("run", "ci"):
            for index, spelling in enumerate(("quoted", "dot", "absolute")):
                with self.subTest(field=field, spelling=spelling):
                    name = f"checks/{field}-{index}" + (" check.py" if spelling == "quoted" else ".py")
                    script_arg = ("./" + name if spelling == "dot" else
                                  str(self.repo / name) if spelling == "absolute" else name)
                    command = shlex.join([sys.executable, script_arg])
                    gate = {"cheap": True, "direct_code_check": True, "paths": [name], field: command}
                    self.start(direct=(name,), gates=[gate])
                    base = self.commit({name: "raise SystemExit(7)\n"})
                    self.commit({name: "raise SystemExit(0)\n"})
                    actual = subprocess.run([sys.executable, str(self.repo / name)], capture_output=True, timeout=10)
                    self.assertEqual(actual.returncode, 0, "candidate weakened its own gate")
                    result = self.evaluate(base)
                    self.assertFalse(result["eligible"], result)
                    self.assertIn("protected", " ".join(result["reasons"]))

    def test_hidden_index_flags_cannot_authorize_different_worktree_inputs(self):
        base = self.start()
        head = self.commit({"docs/a.md": "line\n"})
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                self.git("update-index", "--" + flag, "seed.txt")
                (self.repo / "seed.txt").write_text("different unpublished input\n", encoding="utf-8")
                try:
                    self.assertEqual(self.git("status", "--porcelain"), "", "positive hidden-dirty control")
                    result = policy.plan(self.repo, base, head)
                    self.assertFalse(result["eligible"], result)
                    self.assertIn("index", " ".join(result["reasons"]))
                finally:
                    self.git("update-index", "--no-" + flag, "seed.txt")
                    self.git("checkout", "--", "seed.txt")

    def test_unsupported_run_forms_refuse_and_literal_ci_notice_is_not_an_obligation(self):
        forms = [RUN + " > result.txt", RUN + ' "$(never-expand)"',
                 shlex.quote(sys.executable) + ' -c "print(123)"']
        for index, command in enumerate(forms):
            base = self.start(gates=[{"cheap": True, "run": command}])
            self.commit({f"docs/{index}.md": "line\n"})
            result = self.evaluate(base)
            self.assertFalse(result["eligible"], result)
        base = self.start(gates=[{"cheap": True, "ci": 'pwsh -NoProfile -Command "Write-Host \'the No binding workflow runs this step in CI\'"'}])
        self.commit({"docs/notice.md": "line\n"})
        result = self.evaluate(base)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["commands"][0]["argv"], shlex.split(RUN))

    def test_ignored_checkout_runner_cannot_authorize_direct(self):
        name = "owned.cmd" if os.name == "nt" else "owned"
        self.start(gates=[{"cheap": True, "run": name}])
        base = self.commit({".gitignore": "tools/\n"})
        checker = self.repo / "tools" / name
        checker.parent.mkdir()
        checker.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
        checker.chmod(0o755)
        self.commit({"docs/a.md": "line\n"})
        self.assertEqual(self.git("status", "--porcelain"), "")
        with patch.dict(os.environ, {"PATH": str(checker.parent) + os.pathsep + os.environ["PATH"]}):
            result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("checkout-owned", " ".join(result["reasons"]))

    def test_ignored_check_operand_cannot_authorize_direct(self):
        self.start(gates=[{"cheap": True, "run": shlex.quote(sys.executable) + " build/check.py"}])
        base = self.commit({".gitignore": "build/\n"})
        checker = self.repo / "build/check.py"
        checker.parent.mkdir()
        checker.write_text("raise SystemExit(0)\n", encoding="utf-8")
        self.commit({"docs/a.md": "line\n"})
        self.assertEqual(self.git("status", "--porcelain"), "")
        result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("trusted base", " ".join(result["reasons"]))

    def test_external_wrapper_cannot_hide_inline_self_check(self):
        wrapper = self.home / ("env.cmd" if os.name == "nt" else "env")
        wrapper.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        command = shlex.quote(str(wrapper)) + ' python3 -c "exec(open(\'check\').read())"'
        base = self.start(direct=("check",), gates=[{"cheap": True, "direct_code_check": True,
                                                    "paths": ["check"], "run": command}])
        self.commit({"check": "raise SystemExit(0)\n"})
        result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("dynamic", " ".join(result["reasons"]))

    def test_attached_and_abbreviated_interpreter_modes_require_pr(self):
        commands = [shlex.quote(sys.executable) + " -c'exec(open(\"check\").read())'",
                    'pwsh -Com "Invoke-Expression (Get-Content check)"']
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                base = self.start(direct=("check",), gates=[{"cheap": True, "direct_code_check": True,
                                                           "paths": ["check"], "run": command}])
                self.commit({"check": f"raise SystemExit({index})\n"})
                result = self.evaluate(base)
                self.assertFalse(result["eligible"], result)
                self.assertIn("dynamic", " ".join(result["reasons"]))

    def test_short_ci_command_returns_reasoned_pr(self):
        base = self.start(gates=[{"cheap": True, "ci": "pwsh"}])
        self.commit({"docs/a.md": "line\n"})
        result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("dynamic", " ".join(result["reasons"]))

    def test_project_recipe_ownership_requires_pr(self):
        tool = self.home / ("make.cmd" if os.name == "nt" else "make")
        tool.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        self.start(direct=("Makefile",), gates=[{"cheap": True, "direct_code_check": True,
                                                "paths": ["Makefile"], "run": shlex.quote(str(tool)) + " test"}])
        base = self.commit({"Makefile": "test:\n\tfalse\n"})
        self.commit({"Makefile": "test:\n\ttrue\n"})
        result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("dynamic", " ".join(result["reasons"]))

    def test_an_app_execution_alias_pins_its_own_path_and_never_one_in_the_checkout(self):
        self.start(gates=[{"cheap": True, "run": "python3 --version"}])
        suffix = ".exe" if os.name == "nt" else ""
        outside = self.home / "WindowsApps" / ("alias" + suffix)
        inside = self.repo / "tools" / ("alias" + suffix)
        for path in (outside, inside):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            path.chmod(0o755)
        original = Path.resolve
        def resolve(path, strict=False):  # realpath cannot follow an app execution alias
            if strict and path.name == outside.name:
                raise OSError(1920, "The file cannot be accessed by the system")
            return original(path, strict=strict)
        with patch.object(Path, "resolve", resolve), patch.object(policy, "_app_alias", lambda path: True):
            self.assertEqual(policy._executable(self.repo, str(outside), required=True), str(outside))
            with self.assertRaises(policy.Unproved):
                policy._executable(self.repo, str(inside), required=True)
        with patch.object(Path, "resolve", resolve), patch.object(policy, "_app_alias", lambda path: False):
            with self.assertRaises(OSError):
                policy._executable(self.repo, str(outside), required=True)

    def test_execution_pin_resolves_portable_command_and_owned_executable_is_protected(self):
        with patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}):
            expected = str(Path(shutil.which("python3")).resolve())
            base = self.start(gates=[{"cheap": True, "run": "python3 --version"}])
            self.commit({"docs/a.md": "line\n"})
            result = self.evaluate(base)
            self.assertEqual(policy._execution_pin(self.repo, "python3")[0], expected)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["commands"][0]["argv"][0], "python3")
        name = "tools/owned.cmd" if os.name == "nt" else "tools/owned"
        gate = {"cheap": True, "direct_code_check": True, "paths": [name], "run": Path(name).name}
        self.start(direct=(name,), gates=[gate])
        checker = self.repo / name
        checker.parent.mkdir(exist_ok=True)
        checker.write_text("@exit /b 7\n" if os.name == "nt" else "#!/bin/sh\nexit 7\n", encoding="utf-8")
        checker.chmod(0o755)
        self.git("add", "-A")
        self.git("commit", "-qm", "owned check")
        base = self.git("rev-parse", "HEAD")
        self.commit({name: "@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n"})
        with patch.dict(os.environ, {"PATH": str(checker.parent) + os.pathsep + os.environ["PATH"]}):
            result = self.evaluate(base)
        self.assertFalse(result["eligible"], result)
        self.assertIn("protected", " ".join(result["reasons"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
