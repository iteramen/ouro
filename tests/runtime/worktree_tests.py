"""Real Git creation/retention and native resource controls; no network or model."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ouro import developer, sh, worktrees
from ouro.run import Run

# Removal needs a process observer: Windows handles or Linux /proc. Without one (Darwin) cleanup keeps
# every worktree, which test_unobserved_platform_keeps_every_worktree pins.
OBSERVED = os.name == "nt" or Path("/proc").is_dir()
REMOVES = unittest.skipUnless(OBSERVED, "no process observer here: cleanup keeps every worktree")


class WorktreeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ouro-worktree-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.main = self.root / "main"
        self.main.mkdir()
        self.envvars = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                       "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.patcher = patch.dict(os.environ, self.envvars)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.git("init", "-q", "-b", "master")
        (self.main / ".gitignore").write_text(".claude/worktrees/\nignored\n")
        (self.main / "tracked").write_text("fixture\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("clone", "-q", "--bare", str(self.main), str(self.root / "origin.git"))
        self.git("remote", "add", "origin", str(self.root / "origin.git"))
        self.git("fetch", "-q", "origin")
        data = {"repo": {"slug": "fixture/project", "default_branch": "master"}}
        self.env = sh.Env(self.main, None, data, False, runtime=developer.Settings())
        self.addCleanup(self.env.close)
        self.path = self.main / ".claude" / "worktrees" / "one"
        self.branch = "fix/one"
        self.prs = []
        self.env.gh_json = lambda *args: self.prs

    def git(self, *args, cwd=None, check=True):
        p = subprocess.run(["git", "-c", "core.excludesFile=", *args], cwd=cwd or self.main,
                           capture_output=True, timeout=15, encoding="utf-8", errors="replace")
        if check and p.returncode:
            raise AssertionError(p.stderr)
        return p.stdout.strip()

    def create(self, settled=True):
        r = object.__new__(Run)
        r.env, r.tree, r.branch, r.n, r.k = self.env, worktrees.Tree(self.env, self.path, self.branch), self.branch, 1, None
        r.catch_up = lambda *a, **kw: False
        r.fetch_branch = lambda: False
        r.setup_tree = lambda *a, **kw: None
        with patch("ouro.run.node.worktree_readiness"):
            r.ensure_worktree()  # The actual driver add is the only creation source.
        self.git("push", "-q", "origin", self.branch)
        self.head = self.git("rev-parse", "HEAD", cwd=self.path)
        self.prs = [{"state": "CLOSED", "headRefOid": self.head, "headRefName": self.branch,
                     "headRepository": {"name": "project", "nameWithOwner": ""}, "headRepositoryOwner": {"login": "fixture"}}]
        self.record = next((self.main / ".git" / "ouro-worktrees").glob("*.json"))
        self.env._worktrees_settled = settled
        worktrees.close(self.env)
        return self.record

    def sweep(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            worktrees.sweep(self.env)
        return output.getvalue()

    @contextlib.contextmanager
    def unreadable_external(self):
        proc = self.root / "external-processes"
        descriptor = proc / "111111" / "fd"
        descriptor.mkdir(parents=True)
        original = Path.iterdir
        def entries(path):
            if path == descriptor:
                raise PermissionError("fixture external descriptor visibility unavailable")
            values = original(path)
            return iter(sorted(values)) if path == proc else values
        with patch.object(Path, "iterdir", entries):
            yield proc

    @REMOVES
    def test_unreadable_external_reports_limitation_without_overriding_own_proofs(self):
        self.create()
        original = worktrees.resources
        with self.unreadable_external() as proc:
            def limited(path):
                original(path)  # Actual platform conflict check remains mandatory.
                worktrees._process_resources(path, proc)
            with patch.object(worktrees, "resources", side_effect=limited):
                (self.path / "ignored").write_text("private data", encoding="utf-8")
                self.assertIn("ignored contents", self.sweep())
                self.assertTrue(self.path.exists())
                (self.path / "ignored").unlink()
                out = self.sweep()
        self.assertIn("external process visibility limited", out)
        self.assertIn("may still be active", out)
        self.assertIn("deleted local branch", out)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.git("rev-parse", "refs/heads/" + self.branch, cwd=self.root / "origin.git"), self.head)

    def test_unreadable_external_does_not_hide_later_visible_active_user(self):
        self.create()
        with self.unreadable_external() as proc:
            visible = proc / "222222"
            (visible / "fd").mkdir(parents=True)
            (visible / "maps").write_text("0-1 r--p 0 00:00 0 " + str(self.path / "tracked") + "\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaisesRegex(ValueError, "observed external mapped resource"):
                worktrees._process_resources(self.path, proc)
        self.assertIn("external process visibility limited", output.getvalue())
        self.assertTrue((self.path / "tracked").exists())

    @REMOVES
    def test_closed_and_merged_remove_only_local_refs(self):
        for state in ("CLOSED", "MERGED"):
            self.path = self.path.parent / state.lower()
            self.branch = "fix/" + state.lower()
            self.create()
            self.prs[0]["state"] = state
            out = self.sweep()
            self.assertFalse(self.path.exists(), out)
            self.assertEqual(self.git("rev-parse", "refs/heads/" + self.branch, cwd=self.root / "origin.git"), self.head)
            self.assertEqual(self.git("show-ref", "--verify", "refs/heads/" + self.branch, check=False), "")
            self.assertIn("deleted local branch", out)

    def test_creation_and_legacy_resume_never_retroactively_stamp(self):
        self.git("worktree", "add", "-q", "-b", self.branch, str(self.path), "master")
        r = object.__new__(Run)
        r.env, r.tree, r.branch = self.env, worktrees.Tree(self.env, self.path, self.branch), self.branch
        r.catch_up = lambda *a, **kw: False
        r.setup_tree = lambda *a, **kw: None
        with patch("ouro.run.node.worktree_readiness"):
            r.ensure_worktree()
        self.assertFalse(list((self.main / ".git" / "ouro-worktrees").glob("*.json")))
        self.assertIn("no driver creation record", self.sweep())
        self.assertTrue(self.path.exists())

    def test_failed_add_has_no_creation_marker(self):
        with patch.object(self.env, "git", side_effect=sh.DriverError("add failed")):
            r = object.__new__(Run)
            r.env, r.tree, r.branch = self.env, worktrees.Tree(self.env, self.path, self.branch), self.branch
            with self.assertRaises(sh.DriverError), patch("ouro.run.node.worktree_readiness"):
                r.ensure_worktree()
        self.assertFalse(list((self.main / ".git" / "ouro-worktrees").glob("*.json")))

    def test_optional_creation_write_failure_does_not_admit_or_abort_tree(self):
        original = worktrees.write_record
        def refuse_create(path, data, *, create=False):
            if create:
                raise OSError("fixture metadata unavailable")
            return original(path, data)
        with patch.object(worktrees, "write_record", side_effect=refuse_create):
            r = object.__new__(Run)
            r.env, r.tree, r.branch, r.n, r.k = self.env, worktrees.Tree(self.env, self.path, self.branch), self.branch, 1, None
            r.catch_up = lambda *a, **kw: False
            r.fetch_branch = lambda: False
            r.setup_tree = lambda *a, **kw: None
            with patch("ouro.run.node.worktree_readiness"):
                r.ensure_worktree()
        self.assertTrue((self.path / "tracked").exists())
        self.env._worktrees_settled = True
        worktrees.close(self.env)
        self.assertIn("no driver creation record", self.sweep())

    def test_dirty_private_hidden_unpushed_and_uncertain_evidence_keep(self):
        self.create()
        def probe(label, change, restore):
            change()
            self.assertIn("kept", self.sweep(), label)
            self.assertTrue((self.path / "tracked").exists(), label)
            restore()
        probe("tracked", lambda: (self.path / "tracked").write_text("dirty"),
              lambda: (self.path / "tracked").write_text("fixture\n"))
        for name in ("unknown", "ignored"):
            probe(name, lambda: (self.path / name).write_text("preserve"), lambda: (self.path / name).unlink())
        for flag in ("assume-unchanged", "skip-worktree"):
            probe(flag, lambda: self.git("update-index", "--" + flag, "tracked", cwd=self.path),
                  lambda: self.git("update-index", "--no-" + flag, "tracked", cwd=self.path))
        original = dict(self.prs[0])
        for altered in ({**original, "state": "OPEN"}, {**original, "headRefOid": "0" * 40},
                        {**original, "headRepositoryOwner": {"login": "fork"}}):
            probe("PR", lambda: self.prs.__setitem__(0, altered), lambda: self.prs.__setitem__(0, original))
        probe("ambiguous", lambda: self.prs.append(original), lambda: self.prs.pop())
        probe("unsettled", lambda: worktrees.write_record(self.record, {**worktrees.read_record(self.record), "settled": False}),
              lambda: worktrees.write_record(self.record, {**worktrees.read_record(self.record), "settled": True}))
        probe("locked", lambda: self.git("worktree", "lock", str(self.path)),
              lambda: self.git("worktree", "unlock", str(self.path)))
        admin = Path(worktrees.read_record(self.record)["admin"])
        (admin / "ouro-validation").mkdir()
        probe("validation", lambda: (admin / "ouro-validation" / "lock").write_text("unknown"),
              lambda: (admin / "ouro-validation" / "lock").unlink())
        self.git("branch", "-D", self.branch, cwd=self.root / "origin.git")
        self.assertIn("origin", self.sweep())
        self.assertTrue(self.path.exists())

    def test_active_lease_dry_run_crash_and_unknown_observer_keep(self):
        self.create()
        worktrees.use(self.env, self.path)
        self.assertIn("active driver", self.sweep())
        other = sh.Env(self.main, None, self.env.data, False, runtime=developer.Settings())
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                worktrees.sweep(other)
            self.assertIn("kept", output.getvalue())
            self.assertTrue(self.path.exists())
            with self.assertRaises(sh.Refused):
                worktrees.use(other, self.path)  # Actual native nonblocking OS exclusion.
        finally:
            other.close()
        self.env._worktrees_settled = False
        worktrees.close(self.env)
        self.assertIn("unsettled", self.sweep())
        worktrees.write_record(self.record, {**worktrees.read_record(self.record), "settled": True})
        before = self.record.read_bytes()
        self.env.dry = True
        self.assertEqual(self.sweep(), "")
        self.assertEqual(self.record.read_bytes(), before)
        self.env.dry = False
        with patch.object(worktrees, "resources", side_effect=ValueError("observer unavailable")):
            self.assertIn("observer unavailable", self.sweep())
        self.assertTrue(self.path.exists())

    def test_protected_identity_current_checkout_and_api_failures_keep(self):
        self.create()
        self.env.data["repo"]["protected_branch_prefixes"] = ["fix/"]
        self.assertIn("protected", self.sweep())
        self.env.data["repo"]["protected_branch_prefixes"] = []
        data = worktrees.read_record(self.record)
        worktrees.write_record(self.record, {**data, "directory": [0, 0]})
        self.assertIn("identity", self.sweep())
        worktrees.write_record(self.record, data)
        previous = Path.cwd()
        try:
            os.chdir(self.path)
            self.assertIn("current", self.sweep())
        finally:
            os.chdir(previous)
        with patch.object(self.env, "gh_json", side_effect=sh.DriverError("API unavailable")):
            self.assertIn("evidence unavailable", self.sweep())
        self.assertTrue((self.path / "tracked").exists())

    def test_successful_resume_cannot_settle_an_uncertain_prior_use(self):
        self.create(settled=False)
        worktrees.use(self.env, self.path)
        self.env._worktrees_settled = True
        worktrees.close(self.env)
        self.assertIs(worktrees.read_record(self.record)["settled"], False)
        self.assertIn("unsettled", self.sweep())
        self.assertTrue((self.path / "tracked").exists())

    def test_removed_tree_recreation_and_resume_keep_stale_cleanup_evidence(self):
        self.create()
        retained = self.record.read_bytes()
        with patch.object(worktrees, "provenance", side_effect=sh.DriverError("transient Git metadata failure")):
            with self.assertRaises(sh.Refused):
                worktrees.use(self.env, self.path)  # Same identities cannot discard unsettled-use accounting.
        self.assertEqual(self.record.read_bytes(), retained)
        (self.path / "tracked").write_text("remote branch advanced\n", encoding="utf-8")
        self.git("commit", "-qam", "remote branch advanced", cwd=self.path)
        tip = self.git("rev-parse", "HEAD", cwd=self.path)
        self.git("push", "-q", "origin", self.branch)
        self.git("worktree", "remove", "--force", str(self.path))
        self.git("branch", "-f", self.branch, "master")
        self.prs[0]["headRefOid"] = tip
        run = object.__new__(Run)
        run.env, run.tree, run.branch, run.n, run.k = self.env, worktrees.Tree(self.env, self.path, self.branch), self.branch, 1, None
        run.setup_tree = lambda *a, **kw: None
        for resumed in (False, True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                run.ensure_worktree()  # Actual Git readiness/catch-up/add; no model or mocked lease.
            self.assertIn("cleanup", output.getvalue())
            if not resumed:
                self.assertIn("fast-forward", output.getvalue())
            self.assertEqual(self.git("rev-parse", self.branch), tip)
            self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.path), tip)
            self.assertEqual(self.record.read_bytes(), retained)
            lease, _ = self.env._worktree_leases[str(self.path)]
            self.assertIsNone(lease.record_identity)
            self.assertFalse(lease.may_settle)
            other = sh.Env(self.main, None, self.env.data, False, runtime=developer.Settings())
            try:
                with self.assertRaises(sh.Refused):
                    worktrees.use(other, self.path)
            finally:
                other.close()
            self.env._worktrees_settled = True
            worktrees.close(self.env)
            self.assertEqual(self.record.read_bytes(), retained)
            self.assertIn("identity", self.sweep())
            self.assertTrue((self.path / "tracked").is_file())

    @unittest.skipUnless(os.name == "posix", "FIFO substitution requires POSIX")
    def test_record_open_is_nonblocking_and_rejects_fifo_substitution(self):
        self.create()
        original = os.open
        def replace(path, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_NONBLOCK, "record opens must not block before identity check")
            self.record.unlink()
            os.mkfifo(self.record)
            return original(path, flags, *args, **kwargs)
        with patch.object(worktrees.os, "open", side_effect=replace), self.assertRaises(ValueError):
            worktrees.read_record(self.record)

    @REMOVES
    def test_partial_removal_names_residual_and_retains_branch(self):
        self.create()
        original = worktrees.git
        def partial(env, *args, **kw):
            result = original(env, *args, **kw)
            if args[:2] == ("worktree", "remove"):
                self.path.mkdir()  # Real Git removed it; concurrent residual is uncertain.
            return result
        with patch.object(worktrees, "git", side_effect=partial):
            out = self.sweep()
        self.assertIn("partial worktree removal", out)
        self.assertNotIn("deleted local branch", out)
        self.assertEqual(self.git("rev-parse", "refs/heads/" + self.branch), self.head)
        self.assertTrue(self.record.exists())

    @REMOVES
    def test_failed_remove_after_mutation_never_reports_intact_retention(self):
        self.create()
        original = worktrees.git
        def partially_fail(env, *args, **kw):
            result = original(env, *args, **kw)
            if args[:2] == ("worktree", "remove"):
                raise ValueError("failed after removal began")
            return result
        with patch.object(worktrees, "git", side_effect=partially_fail):
            out = self.sweep()
        self.assertNotIn("cleanup: kept", out)
        self.assertIn("local cleanup incomplete", out)
        self.assertEqual(self.git("rev-parse", "refs/heads/" + self.branch), self.head)
        self.assertTrue(self.record.exists())

    @REMOVES
    def test_branch_race_and_failed_remove_preserve_new_work(self):
        self.create()
        original_git = worktrees.git
        def fail_remove(env, *args, **kw):
            if args[:2] == ("worktree", "remove"):
                raise ValueError("removal failed")
            return original_git(env, *args, **kw)
        with patch.object(worktrees, "git", side_effect=fail_remove):
            self.assertIn("removal failed", self.sweep())
        self.assertTrue(self.path.exists())
        def move_before_delete(env, *args, **kw):
            if args[:2] == ("update-ref", "-d"):
                new = self.git("commit-tree", self.git("rev-parse", "HEAD^{tree}"), "-p", self.head, "-m", "new local commit")
                self.git("update-ref", "refs/heads/" + self.branch, new)
                self.new = new
            return original_git(env, *args, **kw)
        with patch.object(worktrees, "git", side_effect=move_before_delete):
            out = self.sweep()
        self.assertFalse(self.path.exists(), out)
        self.assertEqual(self.git("rev-parse", "refs/heads/" + self.branch), self.new)
        self.assertTrue(self.record.exists())

    @unittest.skipIf(OBSERVED, "this platform has a process observer: the removal rows run")
    def test_unobserved_platform_keeps_every_worktree(self):
        self.create()
        out = self.sweep()
        self.assertIn("kept one: resource observer unavailable", out)
        self.assertTrue((self.path / "tracked").exists())
        self.assertEqual(self.git("rev-parse", "refs/heads/" + self.branch), self.head)

    @REMOVES
    def test_native_resource_users_refused_before_any_removal(self):
        self.create()
        for kind in ("cwd", "nested", "reader", *( ["shared-delete-reader"] if os.name == "nt" else [])):
            ready = self.root / (kind + ".ready")
            release = self.root / (kind + ".release")
            (self.path / "nested").mkdir(exist_ok=True)
            action = "os.chdir(p/'nested')" if kind == "nested" else "os.chdir(p)" if kind == "cwd" else "held=open(p/'tracked','rb')"
            if kind == "shared-delete-reader":
                action = ("import ctypes;from ctypes import wintypes;k=ctypes.WinDLL('kernel32',use_last_error=True);"
                          "k.CreateFileW.restype=wintypes.HANDLE;h=k.CreateFileW(str(p/'tracked'),0x80000000,7,None,3,0,None);"
                          "assert h != ctypes.c_void_p(-1).value")
            code = ("import os,time;from pathlib import Path;p=Path(" + repr(str(self.path)) + ");" + action
                    + ";Path(" + repr(str(ready)) + ").touch()\n"
                    + "while not Path(" + repr(str(release)) + ").exists(): time.sleep(.02)\n")
            child = subprocess.Popen([sys.executable, "-B", "-c", code])
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                with self.assertRaises(ValueError):
                    worktrees.resources(self.path)
                self.assertIn("kept", self.sweep())
                self.assertTrue((self.path / "tracked").exists())
            finally:
                release.touch()
                child.wait(timeout=5)
        worktrees.resources(self.path)
