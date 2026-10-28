"""Git capability admission, configuration and owned-worktree regressions."""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = pathlib.Path(__file__).resolve().parents[2]

from ouro import developer, node, sh
from ouro.runtime import READ, builder, codex, diagnostics, git_capabilities, process, schema
from ouro.runtime.tools import Bridge, sandbox_argv


class GitCapabilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ouro-git-capability-")
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name).resolve()
        self.policy = {"root": str(self.root), "edit": False, "commands": [], "probe": False}

    def config(self, text):
        path = self.root / "developer.toml"
        path.write_text(text, encoding="utf-8")
        return developer.load(path)

    def test_git_readiness_and_configuration_failure_classification(self):
        from ouro.runtime import tools
        failures = [
            sh.R(128, "", "fatal: unable to access 'C:/private/identity': Permission denied", []),
            sh.R(0, "", "warning: unable to access 'C:/private/ignore': Permission denied", []),
        ]
        for failure in failures:
            with self.subTest(stderr=failure.err), patch.object(sh, "which", side_effect=lambda n: n), patch.object(process, "run", return_value=failure):
                with self.assertRaisesRegex(sh.DriverError, "Git capability readiness: git status.*inaccessible configuration"):
                    tools.git_readiness(self.root)
                with patch.object(sh, "run", return_value=sh.R(0, "", "", [])):
                    bridge = Bridge({**self.policy, "commands": ["git status *"]})
                    with self.assertRaisesRegex(sh.DriverError, "Git capability unavailable"):
                        bridge.run("git status --short")
        genuine = sh.R(128, "", "fatal: bad revision 'missing'", [])
        with patch.object(sh, "which", side_effect=lambda n: n), patch.object(sh, "run", return_value=sh.R(0, "", "", [])), patch.object(process, "run", return_value=genuine):
            self.assertEqual(Bridge({**self.policy, "commands": ["git log *"]}).run("git log missing")["exit"], 128)
            with self.assertRaisesRegex(sh.DriverError, "query failed.*exit 128"):
                tools.git_readiness(self.root)
        # A path resembling a diagnostic in stdout must remain ordinary Git data.
        success = sh.R(0, failures[1].err, "", [])
        with patch.object(sh, "which", side_effect=lambda n: n), patch.object(process, "run", return_value=success) as launch:
            tools.git_readiness(self.root, env={"PUBLIC_CONTROL": "yes"})
        self.assertEqual(launch.call_count, 3)
        for call in launch.call_args_list:
            argv = call.args[0]
            self.assertIn("--permission-profile", argv)
            self.assertIn("--include-managed-config", argv)
            self.assertIn("core.fsmonitor=false", argv)
            self.assertNotIn("core.excludesFile=NUL", argv)
            self.assertEqual(call.kwargs["env"], {"LC_ALL": "C", "LANGUAGE": "C", "PUBLIC_CONTROL": "yes"})


    def test_git_locale_and_directory_warning_controls(self):
        from ouro.runtime import tools
        english = "warning: unable to access '/private/ignore': Permission denied"
        translated = "Warnung: Zugriff auf '/private/ignore' nicht möglich: Keine Berechtigung"
        directory = "warning: could not open directory 'private/': Permission denied"
        env = tools.git_environment({"LC_ALL": "de", "LANGUAGE": "de", "PUBLIC_KEY": None})
        self.assertEqual((env["LC_ALL"], env["LANGUAGE"], env["PUBLIC_KEY"]), ("C", "C", None))
        observed = []
        def localized(argv, **kwargs):
            observed.append(kwargs.get("env") or {})
            message = english if observed[-1].get("LC_ALL") == "C" else translated
            return sh.R(0, "", message, argv)
        with patch.dict(os.environ, {"LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de"}), \
                patch.object(sh, "which", side_effect=lambda n: n), \
                patch.object(sh, "run", return_value=sh.R(0, "", "", [])), \
                patch.object(process, "run", side_effect=localized):
            with self.assertRaisesRegex(sh.DriverError, "inaccessible configuration"):
                tools.git_readiness(self.root, env={"GIT_CONFIG_GLOBAL": "public config"})
            with self.assertRaisesRegex(sh.DriverError, "Git capability unavailable"):
                Bridge({**self.policy, "commands": ["git status *"]}).run("git status --short")
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[0]["GIT_CONFIG_GLOBAL"], "public config")
        self.assertTrue(all(env["LC_ALL"] == env["LANGUAGE"] == "C" for env in observed))
        self.assertFalse(tools.git_configuration_failure(sh.R(0, "", directory, [])))
        warning = sh.R(0, "", directory, [])
        with patch.object(sh, "which", side_effect=lambda n: n), \
                patch.object(sh, "run", return_value=sh.R(0, "", "", [])), \
                patch.object(process, "run", return_value=warning):
            tools.git_readiness(self.root)
            result = Bridge({**self.policy, "commands": ["git status *"]}).run("git status --short")
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["stderr"], directory)


    def test_git_error_argument_is_not_a_configuration_diagnostic(self):
        from ouro.runtime import tools
        error = sh.R(128, "", "fatal: ambiguous argument 'unable to access Permission denied': unknown revision or path not in the working tree.", [])
        self.assertFalse(tools.git_configuration_failure(error))
        with patch.object(sh, "which", side_effect=lambda n: n), \
                patch.object(sh, "run", return_value=sh.R(0, "", "", [])), \
                patch.object(process, "run", return_value=error):
            result = Bridge({**self.policy, "commands": ["git log *"]}).run('git log "unable to access Permission denied"')
        self.assertEqual(result["exit"], 128)
        self.assertEqual(result["stderr"], error.err)


    def test_git_poisoned_probe_cleanup_and_direct_fallback(self):
        from ouro import run as driver
        from ouro.runtime import tools
        from types import SimpleNamespace
        from unittest.mock import Mock
        repo = self.root / "repo"
        repo.mkdir()
        def git(*args, cwd=repo, check=True):
            result = sh.run(sh.argv_for("git", *map(str, args)), cwd=cwd or repo)
            if check:
                self.assertEqual(result.code, 0, result.text)
            return result
        git("init", "-q", "--template=")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        (repo / "source").write_bytes(b"base\n")
        git("add", "source")
        git("commit", "-qm", "base")
        git("update-ref", "refs/remotes/origin/default", "HEAD")
        (repo / "source").write_bytes(b"direct commit\n")
        (repo / "tracked-config").write_bytes(b"[invalid syntax\n")
        git("add", "source", "tracked-config")
        git("commit", "-qm", "direct")
        tip = git("rev-parse", "HEAD").out
        refs = git("show-ref").out
        source = (repo / "source").read_bytes()
        local = (repo / ".git/config").read_bytes()
        target = repo / ".claude/worktrees/fallback"
        config = self.root / "global"
        poison = self.root / "poison"
        poison.write_bytes(b"[invalid syntax\n")
        run = object.__new__(driver.Run)
        run.env = SimpleNamespace(main=repo, runtime=developer.Settings("codex"),
                                  default="default", git=git)
        run.branch, run.dirname, run.in_lane = "ouro/fallback", "fallback", True
        run.setup_tree, run.drive = Mock(), Mock()
        for conditional in (False, True):
            for dependency in (target / "tracked-config", target / ".git", poison):
                if not conditional and dependency == poison:
                    continue  # Main must be usable before the proof.
                heading = 'includeIf "onbranch:ouro/**"' if conditional else "include"
                config.write_text('[' + heading + ']\n path = "' + dependency.as_posix() + '"\n', encoding="utf-8")
                configuration = config.read_bytes()
                with self.subTest(conditional=conditional, dependency=dependency), \
                        patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}), \
                        patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                        patch.object(process, "run", side_effect=sh.run):
                    # These are actual Git syntax failures, including a marker poisoned
                    # immediately by add and tracked poison materialized by read-tree.
                    with self.assertRaises(sh.DriverError) as failure:
                        run.lane_fallback()
                    self.assertIn("bad config", str(failure.exception))
                    self.assertNotIn("cleanup failed", str(failure.exception))
                    self.assertTrue(run.in_lane)
                    self.assertEqual(git("show-ref").out, refs)
                    self.assertEqual(git("rev-parse", "HEAD").out, tip)
                    self.assertEqual((repo / "source").read_bytes(), source)
                    self.assertEqual((repo / ".git/config").read_bytes(), local)
                    self.assertEqual(config.read_bytes(), configuration)
                    self.assertFalse(target.exists())
                    self.assertFalse(target.parent.exists())
                    self.assertNotIn(str(target), git("worktree", "list", "--porcelain").out)
                    registrations = repo / ".git/worktrees"
                    self.assertFalse(registrations.exists() and any(registrations.iterdir()))
                    git("status", "--porcelain")
                    run.setup_tree.assert_not_called()
                    run.drive.assert_not_called()


    def test_owned_probe_accepts_git_generated_worktree_configuration(self):
        repo = self.root / "worktree-config-repo"
        repo.mkdir()
        def git(*args):
            result = sh.run(sh.argv_for("git", *map(str, args)), cwd=repo)
            self.assertEqual(result.code, 0, result.text)
            return result.out.strip()
        git("init", "-q", "--template=")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "base")
        git("config", "extensions.worktreeConfig", "true")
        git("config", "--worktree", "ouro.fixture", "active")
        before = (repo / ".git/config.worktree").read_bytes()
        refs = git("show-ref")
        target = repo / "probes/disposable"
        def inspect(root, **kwargs):
            registrations = list((repo / ".git/worktrees").iterdir())
            self.assertEqual(len(registrations), 1)
            copied = registrations[0] / "config.worktree"
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.read_bytes(), before)
        with patch.object(git_capabilities, "git_readiness", side_effect=inspect) as readiness:
            git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, "ouro/probe", "HEAD")
        readiness.assert_called_once()
        self.assertFalse(target.exists())
        self.assertEqual((repo / ".git/config.worktree").read_bytes(), before)
        self.assertEqual(git("show-ref"), refs)
        self.assertNotIn(str(target), git("worktree", "list", "--porcelain"))


    def parent_alias_fixture(self, name="alias-repo"):
        repo = self.root / name
        repo.mkdir()
        def git(*args, cwd=repo):
            result = sh.run(sh.argv_for("git", *map(str, args)), cwd=cwd)
            self.assertEqual(result.code, 0, result.text)
            return result.out.strip()
        git("init", "-q", "--template=")
        (repo / "tracked-config").write_text("[ouro]\n parentAlias = active\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".worktrees.noindex/\n.claude/\n", encoding="utf-8")
        git("add", "tracked-config", ".gitignore")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
        (repo / ".claude").mkdir()
        physical = repo / ".worktrees.noindex"
        physical.mkdir()
        alias = repo / ".claude/worktrees"
        try:
            alias.symlink_to(pathlib.Path("..") / ".worktrees.noindex", target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                self.skipTest("Windows symlink privilege unavailable")
            raise
        self.assertTrue(alias.is_dir(), "configured parent alias fixture must resolve to a directory")
        self.assertTrue(alias.samefile(physical))
        return repo, git, alias, physical

    def test_parent_alias_support_does_not_admit_chains_or_intermediate_aliases(self):
        for mode in ("chain", "intermediate"):
            with self.subTest(mode=mode):
                repo, git, alias, physical = self.parent_alias_fixture(mode)
                if mode == "chain":
                    chain = repo / "chain"
                    chain.symlink_to(physical, target_is_directory=True)
                    alias.unlink()
                    alias.symlink_to(chain, target_is_directory=True)
                else:
                    (repo / ".claude").rename(repo / ".claude-saved")
                    (repo / ".claude").symlink_to(".claude-saved", target_is_directory=True)
                refs = git("show-ref")
                with self.assertRaisesRegex(sh.DriverError, "unproved probe parent directory"):
                    git_capabilities.worktree_readiness(developer.Settings("codex"), repo,
                        alias / "prospective", "ouro/probe", "HEAD")
                self.assertFalse((physical / "prospective").exists())
                self.assertEqual(git("show-ref"), refs)
                registrations = repo / ".git/worktrees"
                self.assertFalse(registrations.exists() and any(registrations.iterdir()))

    def test_stable_configured_parent_alias_preserves_git_semantics_and_cleans_owned_probe(self):
        from ouro.runtime import tools
        repo, git, alias, physical = self.parent_alias_fixture()
        target = alias / "prospective"
        config = self.root / "global"
        config.write_text('[includeIf "onbranch:ouro/**"]\n path = "' +
                          (target / "tracked-config").as_posix() + '"\n', encoding="utf-8")
        before = (git("show-ref"), (repo / ".git/config").read_bytes(), config.read_bytes(), alias.lstat())
        observed = []
        def query(argv, **kwargs):
            self.assertEqual(kwargs["cwd"], target)
            self.assertEqual(git("config", "--get", "ouro.parentAlias", cwd=target), "active")
            observed.append(argv)
            return sh.run(argv, **kwargs)
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}), \
                patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                patch.object(process, "run", side_effect=query):
            git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, "ouro/probe", "HEAD")
        self.assertEqual(len(observed), 3)
        self.assertTrue(os.path.samestat(alias.lstat(), before[3]))
        self.assertEqual(os.readlink(alias), str(pathlib.Path("..") / ".worktrees.noindex"))
        self.assertFalse((physical / target.name).exists())
        self.assertEqual(git("show-ref"), before[0])
        self.assertEqual((repo / ".git/config").read_bytes(), before[1])
        self.assertEqual(config.read_bytes(), before[2])
        self.assertFalse(any((repo / ".git/worktrees").iterdir()))
        self.assertNotIn(target.name, git("worktree", "list", "--porcelain"))

    def test_retargeted_configured_parent_alias_preserves_checkout_registration_and_other_tree(self):
        repo, git, alias, physical = self.parent_alias_fixture()
        target = alias / "prospective"
        alternative = repo / "alternative"
        (alternative / target.name).mkdir(parents=True)
        (alternative / target.name / "keep").write_bytes(b"unrelated retained tree")
        cleanup = git_capabilities.remove_probe
        refused = []
        def interfere(*args):
            registration = next(iter(args[-2]))
            owned = physical / target.name
            before = {path: path.read_bytes() for root in (owned, registration)
                      for path in root.rglob("*") if path.is_file()}
            saved = alias.with_name("original-alias")
            alias.rename(saved)
            alias.symlink_to(alternative, target_is_directory=True)
            try:
                with self.assertRaisesRegex(sh.DriverError, "parent identity changed"):
                    cleanup(*args)
                self.assertTrue(owned.is_dir())
                self.assertTrue(registration.is_dir())
                self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))
                self.assertEqual((alternative / target.name / "keep").read_bytes(), b"unrelated retained tree")
                refused.append(True)
            finally:
                alias.unlink()
                saved.rename(alias)  # Restore the original owned alias inode for fixture-only cleanup.
                cleanup(*args)
            raise sh.DriverError("retarget refusal control")
        with patch.object(git_capabilities, "git_readiness"), \
                patch.object(git_capabilities, "remove_probe", side_effect=interfere):
            with self.assertRaisesRegex(sh.DriverError, "cleanup failed: retarget refusal control"):
                git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, "ouro/probe", "HEAD")
        self.assertEqual(refused, [True])
        self.assertEqual((alternative / target.name / "keep").read_bytes(), b"unrelated retained tree")


    def test_git_probe_unlinks_tracked_symlink_without_following_its_target(self):
        repo = self.root / "symlink-repo"
        repo.mkdir()
        outside = self.root / "outside-preserved"
        outside.mkdir()
        (outside / "keep").write_bytes(b"preserve outside")
        link = repo / "tracked-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as ex:
            if os.name == "nt" and ex.winerror == 1314:
                self.skipTest("Windows symlink privilege unavailable")
            raise
        def git(*args):
            result = sh.run(sh.argv_for("git", *map(str, args)), cwd=repo)
            self.assertEqual(result.code, 0, result.text)
            return result.out.strip()
        git("init", "-q", "--template=")
        git("config", "core.symlinks", "true")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("add", "tracked-link")
        git("commit", "-qm", "tracked link")
        refs = git("show-ref")
        target = repo / "probes/disposable"
        with patch.object(git_capabilities, "git_readiness") as readiness:
            git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, "ouro/probe", "HEAD")
        readiness.assert_called_once()
        self.assertFalse(target.exists())
        self.assertEqual((outside / "keep").read_bytes(), b"preserve outside")
        self.assertEqual(git("show-ref"), refs)
        self.assertNotIn(str(target), git("worktree", "list", "--porcelain"))


    def test_git_probe_teardown_preserves_unknown_state_and_primary_failure(self):
        repo = self.root / "repo"
        repo.mkdir()
        def git(*args):
            result = sh.run(sh.argv_for("git", *map(str, args)), cwd=repo)
            self.assertEqual(result.code, 0, result.text)
            return result.out
        git("init", "-q", "--template=")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "base")
        before = git("show-ref")
        cleanup = git_capabilities.remove_probe
        for mutation in ("marker", "common", "unknown", "refs", "replacement", "unregistered"):
            target = repo / "probes" / mutation
            observed = []
            def interfere(*args):
                marker = target / ".git"
                registration = next(iter(args[-2]))
                (registration / "refs").mkdir(exist_ok=True)  # Git for Windows creates this empty directory.
                if mutation == "replacement":
                    saved = target.with_name(target.name + "-saved")
                    target.rename(saved)
                    target.mkdir()
                    (target / "user-source").write_bytes(b"preserve")
                    def restore():
                        self.assertEqual((target / "user-source").read_bytes(), b"preserve")
                        (target / "user-source").unlink()
                        target.rmdir()
                        saved.rename(target)
                else:
                    path = (marker if mutation in ("marker", "unregistered") else
                            registration / ("commondir" if mutation == "common" else "refs/user" if mutation == "refs" else "unknown"))
                    data = path.read_bytes() if path.exists() else None
                    if mutation == "unregistered":
                        path.unlink()
                    else:
                        # Preserve the hidden Windows marker's identity and attributes.
                        with path.open("r+b" if data is not None else "wb") as output:
                            output.write(b"unproved\n")
                            output.truncate()
                    def restore():
                        if data is None:
                            path.unlink()
                        else:
                            # Recreate marker identity only for this fixture's own cleanup.
                            with path.open("r+b" if path.exists() else "wb") as output:
                                output.write(data)
                                output.truncate()
                try:
                    with self.assertRaises((sh.DriverError, OSError)):
                        cleanup(*args)
                    self.assertTrue(target.exists())
                    self.assertTrue(registration.exists())
                    observed.append(True)
                finally:
                    restore()
                    if mutation == "unregistered":
                        args = (*args[:-1], marker.lstat())
                    cleanup(*args)
                raise OSError("cleanup refusal control")
            with self.subTest(mutation=mutation), patch.object(git_capabilities, "remove_probe", side_effect=interfere), \
                    patch.object(git_capabilities, "git_readiness", side_effect=sh.DriverError("primary readiness failure")):
                with self.assertRaisesRegex(sh.DriverError, "primary readiness failure; disposable probe cleanup failed: cleanup refusal control"):
                    git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, "ouro/probe", "HEAD")
            self.assertEqual(observed, [True])
            self.assertFalse(target.exists())
            target.parent.rmdir()  # Failure deliberately leaves caller-created parents.
            self.assertEqual(git("show-ref"), before)
            self.assertNotIn(str(target), git("worktree", "list", "--porcelain"))


    def test_prospective_git_configuration_and_cleanup(self):
        from ouro.runtime import tools
        repo = self.root / "repo"
        repo.mkdir()
        def git(*args, cwd=repo):
            result = sh.run(sh.argv_for("git", *map(str, args)), cwd=cwd)
            self.assertEqual(result.code, 0, result.text)
            return result.out.strip()
        git("init", "-q")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "base")
        # These dependencies must exist in the prospective checkout, including nested includes.
        (repo / "tracked-config").write_text('[include]\n path = nested-config\n', encoding="utf-8")
        (repo / "nested-config").write_text('[ouro]\n tracked = active\n[include]\n path = "' +
                                          (self.root / "leaf").as_posix() + '"\n', encoding="utf-8")
        git("add", "tracked-config", "nested-config")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "tracked configuration")
        git("update-ref", "refs/remotes/origin/fixture", "HEAD")
        hooks = self.root / "hooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text("#!/bin/sh\ntouch hook-ran\n", encoding="utf-8")
        hook.chmod(0o755)
        transaction = hooks / "reference-transaction"
        transaction.write_bytes(hook.read_bytes())
        transaction.chmod(0o755)
        git("config", "core.hooksPath", str(hooks))
        target = repo / "probes" / "prospective"
        config = self.root / "global"
        ignore = self.root / "ignore"
        ignore.write_text("ouro-readiness-sentinel-*\n", encoding="utf-8")
        leaf = self.root / "leaf"
        leaf.write_text('[core]\n excludesFile = "' + ignore.as_posix() + '"\n'
                        '[ouro]\n conditional = active\n', encoding="utf-8")
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="ouro-run-")).resolve()
        self.addCleanup(scratch.rmdir)
        before = git("show-ref")
        source = git("status", "--porcelain")
        for branch, name, condition in (
                ("ouro/prospective", "prospective", "onbranch:ouro/**"),
                ("ouro/prospective", "prospective", "gitdir:" + (repo / ".git/worktrees/prospective").as_posix()),
                (None, "review-1", "gitdir:" + (repo / ".git/worktrees/review-1").as_posix()),
                (None, "checkpoint", "gitdir:" + (repo / ".git/worktrees/checkpoint").as_posix())):
            target = (repo / "probes" if branch else scratch) / name
            config.write_text('[includeIf "' + condition + '"]\n path = "' +
                              (target / "tracked-config").as_posix() + '"\n', encoding="utf-8")
            for denied in (False, True):
                observed = []
                def query(argv, **kwargs):
                    cwd = kwargs["cwd"]
                    marker = git("config", "--get", "ouro.conditional", cwd=cwd)
                    self.assertEqual(marker, "active")
                    self.assertEqual(git("config", "--get", "ouro.tracked", cwd=cwd), "active")
                    self.assertFalse((repo / "hook-ran").exists())
                    self.assertFalse((target / "hook-ran").exists())
                    self.assertEqual(git("show-ref"), before)
                    observed.append(argv)
                    if denied:
                        return sh.R(0, "", "warning: unable to access '/private/ignore': Permission denied", argv)
                    result = sh.run(argv, **kwargs)
                    if "status" in argv:
                        self.assertNotIn("ouro-readiness-sentinel", result.out)
                    return result
                with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}), \
                        patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                        patch.object(process, "run", side_effect=query):
                    if denied:
                        with self.assertRaisesRegex(sh.DriverError, "inaccessible configuration"):
                            git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, branch, "HEAD", scratch=scratch)
                    else:
                        git_capabilities.worktree_readiness(developer.Settings("codex"), repo, target, branch, "HEAD", scratch=scratch)
                self.assertEqual(len(observed), 1 if denied else 3)
                self.assertFalse(target.exists())
                self.assertEqual(target.parent.exists(), branch is None)
                self.assertEqual(git("status", "--porcelain"), source)
                self.assertEqual(git("show-ref"), before)
                self.assertNotIn(str(target), git("worktree", "list", "--porcelain"))
        self.assertFalse((repo / "hook-ran").exists())


    def test_resumed_git_worktree_proves_selected_branch_before_mutation(self):
        from ouro import run as driver
        from ouro.runtime import tools
        from types import SimpleNamespace
        from unittest.mock import Mock
        for mode in ("local", "remote", "fast-forward"):
            with self.subTest(mode=mode):
                repo = self.root / mode
                repo.mkdir()
                def git(*args, cwd=repo, check=True):
                    result = sh.run(sh.argv_for("git", *map(str, args)), cwd=cwd or repo)
                    if check:
                        self.assertEqual(result.code, 0, result.text)
                    return result
                git("init", "-q")
                git("config", "user.name", "Fixture")
                git("config", "user.email", "fixture@example.invalid")
                # Directory-only rule must match before the missing directory is created.
                (repo / ".gitignore").write_text("probes/resume/\n", encoding="utf-8")
                git("add", ".gitignore")
                git("commit", "-qm", "base")
                base = git("rev-parse", "HEAD").out.strip()
                origin = self.root / (mode + "-origin")
                git("init", "--bare", "-q", origin)
                git("remote", "add", "origin", origin)
                git("push", "-q", "origin", "HEAD:refs/heads/default")
                (repo / "tracked-config").write_text("[ouro]\n selected = active\n", encoding="utf-8")
                git("add", "tracked-config")
                git("commit", "-qm", "issue configuration")
                tip = git("rev-parse", "HEAD").out.strip()
                branch = "ouro/resume"
                if mode != "local":
                    git("push", "-q", "origin", "HEAD:refs/heads/" + branch)
                if mode != "remote":
                    git("branch", branch, base if mode == "fast-forward" else tip)
                target = repo / "probes" / "resume"
                config = self.root / (mode + "-global")
                config.write_text('[includeIf "onbranch:ouro/**"]\n path = "' +
                                  (target / "tracked-config").as_posix() + '"\n', encoding="utf-8")
                env = SimpleNamespace(runtime=developer.Settings("codex"), main=repo,
                                      default="default", git=git, dry=False)
                run = object.__new__(driver.Run)
                run.env, run.worktree, run.branch = env, target, branch
                run.n, run.k = 1, None
                run.setup_tree = Mock()
                observed = []
                denied = True
                def query(argv, **kwargs):
                    self.assertEqual(git("config", "--get", "ouro.selected", cwd=kwargs["cwd"]).out.strip(), "active")
                    self.assertTrue((target / "tracked-config").is_file())
                    # The proof must not create or advance the real local branch.
                    actual = git("rev-parse", "--verify", "refs/heads/" + branch, check=False)
                    self.assertEqual(actual.out.strip(), "" if mode == "remote" else base if mode == "fast-forward" else tip)
                    observed.append(argv)
                    if denied:
                        return sh.R(0, "", "warning: unable to access '/private/ignore': Permission denied", argv)
                    return sh.run(argv, **kwargs)
                refs = git("show-ref").out
                source = git("status", "--porcelain", "-z").out
                with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}), \
                        patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                        patch.object(process, "run", side_effect=query):
                    with self.assertRaisesRegex(sh.DriverError, "inaccessible configuration"):
                        run.ensure_worktree()
                    self.assertEqual(len(observed), 1)
                    self.assertFalse(target.exists())
                    run.setup_tree.assert_not_called()
                    self.assertEqual(git("show-ref").out, refs)
                    self.assertEqual(git("status", "--porcelain", "-z").out, source)
                    # An unknown target is retained before any probe or branch change.
                    target.mkdir(parents=True)
                    (target / "keep").write_bytes(b"preserve")
                    with self.assertRaisesRegex(sh.DriverError, "already exists"):
                        run.ensure_worktree()
                    self.assertEqual((target / "keep").read_bytes(), b"preserve")
                    self.assertEqual(git("show-ref").out, refs)
                    (target / "keep").unlink()
                    target.rmdir()
                    denied = False
                    run.ensure_worktree()
                run.setup_tree.assert_called_once_with(target)
                self.assertEqual(git("rev-parse", "HEAD", cwd=target).out.strip(), tip)
                self.assertEqual(git("status", "--porcelain", "-z").out, source)
                if mode == "fast-forward":
                    # Occupied trees refuse tracked configuration changes before the FF.
                    (repo / "tracked-config").write_text("[ouro]\n selected = final\n", encoding="utf-8")
                    git("commit", "-qam", "final configuration")
                    final = git("rev-parse", "HEAD").out.strip()
                    git("push", "-q", "origin", "HEAD:refs/heads/" + branch)
                    keep = target / "user-edit"
                    keep.write_bytes(b"preserve")
                    run.setup_tree.reset_mock()
                    stages = []
                    def occupied_query(argv, **kwargs):
                        selected = git("config", "--get", "ouro.selected", cwd=target).out.strip()
                        stages.append(selected)
                        if selected == "final":
                            return sh.R(0, "", "warning: unable to access '/private/ignore': Permission denied", argv)
                        return sh.run(argv, **kwargs)
                    with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}), \
                            patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                            patch.object(process, "run", side_effect=occupied_query):
                        with self.assertRaisesRegex(sh.DriverError, "configuration independent"):
                            run.ensure_worktree()
                    self.assertEqual(stages, ["active"] * 3)
                    self.assertEqual(git("rev-parse", "HEAD", cwd=target).out.strip(), tip)
                    self.assertEqual(keep.read_bytes(), b"preserve")
                    run.setup_tree.assert_not_called()
                    # Keep the profile independent: an outside absolute include still catches up.
                    config.write_text('[include]\n path = "' + (repo / "tracked-config").as_posix() + '"\n', encoding="utf-8")
                    with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}), \
                            patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                            patch.object(process, "run", side_effect=sh.run):
                        run.ensure_worktree()
                    self.assertEqual(git("rev-parse", "HEAD", cwd=target).out.strip(), final)
                    run.setup_tree.assert_called_once_with(target, fresh=False)
                    self.assertEqual(keep.read_bytes(), b"preserve")


    def test_occupied_git_fast_forward_input_independence(self):
        from ouro import run as driver
        from ouro.runtime import tools
        from types import SimpleNamespace
        from unittest.mock import Mock
        modes = ("worktree-root", "missing-include", "changed-include", "relative", "nested",
                 "excludes", "missing-excludes", "home", "xdg", "global",
                 "implicit-global", "implicit-ignore", "repo-excludes", "system", "redirect", "external", "missing-external",
                 "dirty-external", "same-tree", "unreadable-config", "unreadable-excludes",
                 "intermediate-alias", "prefix-alias", "external-alias", "relative-alias",
                 "loop-alias", "broken-alias", "missing-prefix-alias", "external-junction", "intermediate-junction",
                 "unknown-reparse")
        for mode in modes:
            with self.subTest(mode=mode):
                repo = self.root / mode
                repo.mkdir()
                def git(*args, cwd=repo, check=True):
                    result = sh.run(sh.argv_for("git", *map(str, args)), cwd=cwd or repo)
                    if check:
                        self.assertEqual(result.code, 0, result.text)
                    return result
                git("init", "-q")
                git("config", "user.name", "Fixture")
                git("config", "user.email", "fixture@example.invalid")
                git("config", "core.autocrlf", "false")  # Stable bytes across global-config controls.
                (repo / ".gitignore").write_text("probes/\n", encoding="utf-8")
                (repo / "keep").write_bytes(b"original")
                if mode in ("changed-include", "worktree-root"):
                    (repo / "incoming").write_text("[ouro]\n selected = old\n", encoding="utf-8", newline="\n")
                if mode == "intermediate-alias":
                    try:
                        (repo / "tracked-link").symlink_to(self.root / (mode + "-leaf"))
                    except OSError as ex:
                        if os.name != "nt" or ex.winerror != 1314:
                            raise
                        print("SKIP: Windows file symlink privilege unavailable")
                        continue
                    git("config", "core.symlinks", "true")
                git("add", ".")
                git("commit", "-qm", "base")
                base = git("rev-parse", "HEAD").out.strip()
                branch = "ouro/occupied"
                target = repo / "probes/occupied"
                git("worktree", "add", "-qb", branch, target)
                if mode == "intermediate-alias":
                    (repo / "tracked-link").unlink()
                    (repo / "tracked-link").symlink_to(self.root / (mode + "-invalid"))
                    (self.root / (mode + "-invalid")).write_text("malformed configuration", encoding="utf-8")
                    git("add", "tracked-link")
                if mode != "same-tree":
                    (repo / "incoming").write_text('[include]\n path = "/unavailable/config"\n', encoding="utf-8")
                    git("add", "incoming")
                git("commit", "--allow-empty", "-qm", "incoming")
                tip = git("rev-parse", "HEAD").out.strip()
                git("update-ref", "refs/remotes/origin/" + branch, tip)
                config = self.root / (mode + "-global")
                leaf = self.root / (mode + "-leaf")
                leaf.write_text("[ouro]\n selected = stable\n", encoding="utf-8")
                home = self.root / (mode + "-home")
                home.mkdir()
                environment = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                               "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}
                value = target / "incoming"
                key = 'includeIf "onbranch:ouro/**"'
                text = '[include]\n path = "' + leaf.as_posix() + '"\n'
                if mode in ("missing-include", "changed-include", "same-tree"):
                    text = '[' + key + ']\n path = "' + value.as_posix() + '"\n'
                elif mode == "relative":
                    text = "[include]\n path = relative-missing\n"
                elif mode == "nested":
                    leaf.write_text('[include]\n path = "' + value.as_posix() + '"\n', encoding="utf-8")
                elif mode in ("excludes", "missing-excludes"):
                    value = target / ("keep" if mode == "excludes" else "incoming")
                    text = '[core]\n excludesFile = "' + value.as_posix() + '"\n'
                elif mode == "home":
                    environment["HOME"] = str(target)
                elif mode == "xdg":
                    environment["XDG_CONFIG_HOME"] = str(target / "config")
                elif mode == "global":
                    environment["GIT_CONFIG_GLOBAL"] = str(target / "missing-global")
                elif mode in ("implicit-global", "implicit-ignore"):
                    if os.name == "nt":
                        continue  # Windows symlinks require host privileges; native controls remain separate.
                    path = home / (".gitconfig" if mode == "implicit-global" else ".config/git/ignore")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.symlink_to(target / "incoming")
                    if mode == "implicit-global":
                        environment.pop("GIT_CONFIG_GLOBAL")
                elif mode == "redirect":
                    environment["GIT_CONFIG"] = str(config)
                elif mode == "system":
                    environment["GIT_CONFIG_SYSTEM"] = str(target / "incoming")
                elif mode == "repo-excludes":
                    if os.name == "nt":
                        continue
                    (repo / ".git/info/exclude").unlink()
                    (repo / ".git/info/exclude").symlink_to(target / "incoming")
                elif mode == "missing-external":
                    text = '[include]\n path = "' + (self.root / "absent-external").as_posix() + '"\n'
                elif mode.endswith("-alias"):
                    alias = self.root / (mode + "-alias")
                    destination = (target / "tracked-link" if mode == "intermediate-alias" else
                                   target if mode == "prefix-alias" else
                                   alias if mode == "loop-alias" else
                                   self.root / "absent" if mode == "broken-alias" else
                                   leaf.parent if mode == "missing-prefix-alias" else leaf)
                    try:
                        alias.symlink_to(destination.name if mode == "relative-alias" else destination,
                                         target_is_directory=mode in ("prefix-alias", "missing-prefix-alias"))
                    except OSError as ex:
                        if os.name != "nt" or ex.winerror != 1314:
                            raise
                        print("SKIP: Windows symlink privilege unavailable")
                        continue
                    if mode == "prefix-alias":
                        (target / "outward").symlink_to(leaf.parent, target_is_directory=True)
                        value = alias / "outward" / leaf.name
                    else:
                        value = alias / "absent-config" if mode == "missing-prefix-alias" else alias
                    if mode in ("intermediate-alias", "prefix-alias"):
                        self.assertEqual(value.resolve(), leaf)
                        self.assertEqual(git("config", "--file", value, "--get", "ouro.selected").out.strip(), "stable")
                    # Broken/loop paths are inactive: Git must not fail before the path proof.
                    text = '[' + (key if mode in ("broken-alias", "loop-alias") else "include") + ']\n path = "' + value.as_posix() + '"\n'
                    if mode in ("broken-alias", "loop-alias"):
                        text = text.replace("onbranch:ouro/**", "onbranch:inactive")
                elif mode.endswith("-junction"):
                    if os.name != "nt":
                        continue  # Native junction controls execute on Windows.
                    alias = self.root / (mode + "-alias")
                    destination = target if mode == "intermediate-junction" else leaf.parent
                    result = sh.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(destination)])
                    self.assertEqual(result.code, 0, result.text)
                    self.addCleanup(alias.rmdir)
                    if mode == "intermediate-junction":
                        outward = target / "outward"
                        result = sh.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(outward), str(leaf.parent)])
                        self.assertEqual(result.code, 0, result.text)
                        self.addCleanup(outward.rmdir)
                        value = alias / "outward" / leaf.name
                    else:
                        value = alias / leaf.name
                    self.assertEqual(value.resolve(), leaf)
                    text = '[includeIf "onbranch:inactive"]\n path = "' + value.as_posix() + '"\n'
                effective = None
                if mode == "worktree-root":
                    effective = self.root / (mode + "-effective")
                    effective.mkdir()
                    for path in target.iterdir():
                        if path.is_file() and path.name != ".git":
                            (effective / path.name).write_bytes(path.read_bytes())
                    git("config", "extensions.worktreeConfig", "true")
                    git("config", "--worktree", "core.worktree", str(effective), cwd=target)
                    text = '[include]\n path = "' + (effective / "incoming").as_posix() + '"\n'
                config.write_text(text, encoding="utf-8")
                keep = target / "keep"
                if mode == "dirty-external":
                    keep.write_bytes(b"developer edit")
                (target / "untracked").write_bytes(b"preserve")
                env = SimpleNamespace(runtime=developer.Settings("codex"), main=repo,
                                      default="default", git=git, dry=False)
                run = object.__new__(driver.Run)
                run.env, run.worktree, run.branch = env, target, branch
                run.n, run.k = 1, None
                run.setup_tree = Mock()
                run.fetch_branch = lambda: True
                before_refs = git("show-ref").out
                before_source = git("status", "--porcelain", "-z").out
                before_files = {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}
                if effective is not None:
                    before_effective = {p.name: p.read_bytes() for p in effective.iterdir()}
                    worktree_config = pathlib.Path(git("rev-parse", "--absolute-git-dir", cwd=target).out.strip()) / "config.worktree"
                    configs = (config, repo / ".git/config", worktree_config)
                    before_configs = {p: p.read_bytes() for p in configs}
                allowed = mode in ("external", "missing-external", "dirty-external", "same-tree",
                                   "external-alias", "relative-alias", "missing-prefix-alias", "external-junction")
                def sandbox_query(argv, **kwargs):
                    if mode in ("unreadable-config", "unreadable-excludes"):
                        return sh.R(0, "", "warning: unable to access '/fixture/input': Permission denied", argv)
                    return sh.run(argv, **kwargs)
                real_lstat = pathlib.Path.lstat
                def lstat(path, *args, **kwargs):
                    info = real_lstat(path, *args, **kwargs)
                    if mode == "unknown-reparse" and path == leaf:
                        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400,
                                               st_reparse_tag=0x8000001B)
                    return info
                with patch.dict(os.environ, environment), \
                        patch.object(pathlib.Path, "lstat", lstat), \
                        patch.object(tools, "sandbox_argv", side_effect=lambda root, argv: argv), \
                        patch.object(process, "run", side_effect=sandbox_query):
                    if effective is not None:
                        self.assertTrue(pathlib.Path(git("rev-parse", "--show-toplevel", cwd=target).out.strip()).samefile(effective))
                        self.assertFalse((effective / "incoming").is_relative_to(target))
                        self.assertEqual(git("config", "--get", "ouro.selected", cwd=target).out.strip(), "old")
                        self.assertEqual(git("show", base + ":incoming").out, "[ouro]\n selected = old\n")
                        self.assertNotEqual(git("show", tip + ":incoming").out, git("show", base + ":incoming").out)
                    if allowed:
                        run.ensure_worktree()
                        self.assertEqual(git("rev-parse", "HEAD", cwd=target).out.strip(), tip)
                        run.setup_tree.assert_called_once_with(target, fresh=False)
                    else:
                        with self.assertRaisesRegex(sh.DriverError, "Git capability readiness"):
                            run.ensure_worktree()
                        self.assertEqual(git("rev-parse", "HEAD", cwd=target).out.strip(), base)
                        self.assertEqual(git("show-ref").out, before_refs)
                        self.assertEqual(git("status", "--porcelain", "-z").out, before_source)
                        self.assertEqual({p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}, before_files)
                        run.setup_tree.assert_not_called()
                        if effective is not None:
                            self.assertEqual({p.name: p.read_bytes() for p in effective.iterdir()}, before_effective)
                            self.assertEqual({p: p.read_bytes() for p in configs}, before_configs)
                            # Setup must also refuse an unchanged occupied tip/root.
                            for selected in ("HEAD", base):
                                with self.assertRaisesRegex(sh.DriverError, "configuration independent"):
                                    git_capabilities.occupied_inputs(target, selected, {})
                self.assertEqual((target / "untracked").read_bytes(), b"preserve")
                self.assertEqual(keep.read_bytes(), before_files["keep"])


    def test_detached_git_driver_readiness_precedes_creation_setup_and_dispatch(self):
        from ouro import run as driver
        from types import SimpleNamespace
        from unittest.mock import Mock
        cfg = developer.Settings("codex")
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="ouro-run-")).resolve()
        self.addCleanup(scratch.rmdir)
        env = SimpleNamespace(runtime=cfg, main=self.root, scratch=scratch,
                              default="fixture", dry=False, trusted=lambda: [], lookup=lambda key, default=None: default)
        fake = SimpleNamespace(
            env=env, n=1, title="fixture", body="", issue={}, flow="full",
            worktree=self.root, verify=lambda **kw: None, changed_paths=lambda: [],
            build_commands=lambda: ("python3 check.py", ["python3 check.py"]),
            wt_git=lambda *a: sh.R(0, "", "", []), build_facts=lambda: {
                "validation_results": [], "validation_attempts": []}, evidence_lines=lambda: "",
            verification=lambda *a: "", part_scope=lambda *a: "", spec_comments=lambda: "",
            setup_tree=Mock(side_effect=AssertionError("setup before readiness")))
        for operation, name in ((lambda: driver.Run.checkpoint(fake), "checkpoint"),
                                (lambda: driver.Run.node_review(fake, None, "HEAD", [], 1), "review-1")):
            with patch.object(node, "worktree_readiness", side_effect=sh.DriverError("readiness control")) as ready, \
                    patch.object(node, "run_node") as dispatch, patch.object(node, "model", return_value="default"), \
                    patch.object(driver.briefs, "contract_sections", return_value=""), \
                    patch.object(driver.briefs, "render", return_value="fixture"):
                with self.assertRaisesRegex(sh.DriverError, "readiness control"):
                    operation()
                ready.assert_called_once_with(cfg, self.root, env.scratch / name, None,
                                              "origin/fixture" if name == "checkpoint" else "HEAD", scratch=scratch)
                dispatch.assert_not_called()
                fake.setup_tree.assert_not_called()


    def test_existing_git_target_and_unknown_paths_are_preserved(self):
        from ouro import run as driver
        from types import SimpleNamespace
        target = self.root / "existing"
        target.mkdir()
        keep = target / "keep"
        keep.write_bytes(b"preserve")
        cfg = developer.Settings("codex")
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="ouro-run-")).resolve()
        self.addCleanup(scratch.rmdir)
        for branch, owned in ((None, None), ("ouro/test", scratch)):
            with self.assertRaisesRegex(sh.DriverError, "target leaves repository"):
                git_capabilities.worktree_readiness(cfg, self.root, scratch / "unknown", branch, "HEAD", scratch=owned)
        unknown = scratch / "unknown"
        unknown.mkdir()
        (unknown / "keep").write_bytes(b"preserve")
        with self.assertRaisesRegex(sh.DriverError, "preserving existing target"):
            git_capabilities.worktree_readiness(cfg, self.root, unknown, None, "HEAD", scratch=scratch)
        self.assertEqual((unknown / "keep").read_bytes(), b"preserve")
        (unknown / "keep").unlink()
        unknown.rmdir()
        with self.assertRaisesRegex(sh.DriverError, "preserving existing target"):
            git_capabilities.worktree_readiness(cfg, self.root, target, "ouro/test", "HEAD")
        (target / ".git").write_text("fixture", encoding="utf-8")
        failure = sh.DriverError("Git capability readiness: control")
        # Exercise the real shared-driver seam: refusal must precede catch-up and setup.
        fake = SimpleNamespace(env=SimpleNamespace(runtime=cfg, main=self.root, default="main", dry=False),
                               worktree=target, branch="ouro/test")
        with patch.object(git_capabilities, "git_readiness", side_effect=failure) as readiness:
            with self.assertRaisesRegex(sh.DriverError, "configuration independent"):
                driver.Run.ensure_worktree(fake)
            readiness.assert_not_called()
        self.assertEqual(keep.read_bytes(), b"preserve")


    def test_git_readiness_refuses_before_worker_dispatch(self):
        failure = sh.DriverError("Git capability readiness: inaccessible configuration")
        with patch.object(codex, "git_readiness", side_effect=failure), patch.object(process, "run") as launch, patch.object(codex, "WINDOWS", False):
            with self.assertRaisesRegex(sh.DriverError, "Git capability readiness"):
                codex.execute("BUILD", "p", "default", builder([]), self.root, 1, None, {}, developer.Settings("codex"))
            launch.assert_not_called()
        # File-only dispatch has no Git dependency.
        with patch.object(codex, "git_readiness") as readiness, patch.object(process, "run", side_effect=sh.DriverError("transport control")), patch.object(sh, "which", side_effect=lambda n: n):
            with self.assertRaisesRegex(sh.DriverError, "transport control"):
                codex.execute("CHECKPOINT", "p", "default", READ, self.root, 1, None, {}, developer.Settings("codex"))
            readiness.assert_not_called()


    @patch.object(codex, 'WINDOWS', False)  # supported-host policy; early Windows refusal has separate controls
    def test_cli_inspect_git_preflight_outside_repository(self):
        from ouro.runtime import tools
        spec = importlib.util.spec_from_file_location('ouro_inspection', BASE / 'bin/ouro-developer.py')
        inspector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inspector)
        home = self.root / 'home'
        config = home / '.config/ouro/developer.toml'
        config.parent.mkdir(parents=True)
        config.write_text('[runtime]\nbackend="codex"\n', encoding='utf-8')
        templates = self.root / 'private-templates'
        templates.mkdir()
        (templates / 'private-material').write_bytes(b'generated-private-template-control')
        global_config = self.root / 'global'
        global_config.write_text('[init]\n templateDir = "' + templates.as_posix() + '"\n', encoding='utf-8')
        before = global_config.read_bytes()
        real_run = sh.run

        def launch(argv, **kwargs):
            if pathlib.Path(argv[0]).stem.lower() == 'git':
                return real_run(argv, **kwargs)
            if '--version' in argv:
                return sh.R(0, 'codex 0.164.0', '', argv)
            if 'login' in argv:
                return sh.R(0, 'Logged in', '', argv)
            if '--help' in argv:
                return sh.R(0, '--ignore-user-config --ignore-rules --output-schema --output-last-message --ephemeral', '', argv)
            return sh.R(0, '{"models":[]}', '', argv)
        queried = []

        def restricted(argv, **kwargs):
            if pathlib.Path(argv[0]).stem.lower() == 'git':
                queried.append(kwargs['cwd'])
                self.assertTrue((pathlib.Path(kwargs['cwd']) / '.git').is_dir())
                return real_run(argv, **kwargs)
            return sh.R(0, 'ouro-sandbox-ready', '', argv)

        def model(name, prompt, selected, capability, cwd, *args):
            self.assertEqual(queried, [cwd] * 3)
            self.assertFalse((pathlib.Path(cwd) / '.git/private-material').exists())
            self.assertFalse(any(('private-material' in p.name for p in pathlib.Path(cwd).rglob('*'))))
            return ({'finding': 'ouro-ready'}, 'fixture', {'tool_calls': [{'tool': 'read', 'path': 'readiness.txt'}]}, None)
        original = pathlib.Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(codex, 'network_readiness'), patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(global_config), 'GIT_TEMPLATE_DIR': str(templates)}), patch.object(sh, 'which', side_effect=lambda name: name), patch.object(sh, 'run', side_effect=launch), patch.object(process, 'run', side_effect=restricted), patch.object(tools, 'sandbox_argv', side_effect=lambda root, argv: argv), patch.object(codex, 'execute', side_effect=model) as execute:
                self.assertEqual(inspector.main(['inspect', '--check', '--home', str(home)]), 0)
                execute.assert_called_once()
                self.assertFalse((self.root / '.git').exists())
                self.assertEqual(global_config.read_bytes(), before)
                execute.reset_mock()
                with patch.dict(os.environ, {'GIT_DIR': str(self.root / 'preserve-git-dir')}):
                    self.assertEqual(inspector.main(['inspect', '--check', '--home', str(home)]), 1)
                self.assertFalse((self.root / 'preserve-git-dir').exists())
                execute.assert_not_called()
                global_config.write_bytes(b'[invalid syntax\n')
                queried.clear()
                execute.reset_mock()
                self.assertEqual(inspector.main(['inspect', '--check', '--home', str(home)]), 1)
                execute.assert_not_called()
                self.assertEqual(global_config.read_bytes(), b'[invalid syntax\n')
        finally:
            os.chdir(original)
        self.assertEqual((templates / 'private-material').read_bytes(), b'generated-private-template-control')

