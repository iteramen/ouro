"""Actual Windows Git sandbox conformance on generated configuration only."""
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "bin"))
from ouro import developer, sh
from ouro.runtime import git_capabilities, process
from ouro.runtime.tools import git_read_argv, git_readiness, sandbox_argv
from fixtures import public_fixture

with public_fixture("ouro-git-sandbox-fixture-") as folder:
    root = pathlib.Path(folder)
    version = sh.run([*sh.argv_for("codex"), "--version"], timeout=30)
    assert version.code == 0, version
    print(json.dumps({"cli": version.out.strip(), "platform": sys.platform}))
    control = "git"
    if control in ("all", "git"):
        if os.name == "nt":
            # The private temp directory retains the invoking account's ACLs. The public
            # workspace config points at it; no real developer configuration is edited.
            with tempfile.TemporaryDirectory(prefix="ouro-private-git-") as private:
                private = pathlib.Path(private)
                identity = private / "identity"
                identity.write_text("[user]\n name = Public Fixture\n email = fixture@example.invalid\n", encoding="utf-8")
                ignores = private / "ignore"
                ignores.write_text("intentionally-ignored.txt\n", encoding="utf-8")
                config = root / "public-gitconfig"
                readable_ignore = root / "public-ignore"
                readable_ignore.write_bytes(ignores.read_bytes())
                (root / "intentionally-ignored.txt").write_text("public ignored control")
                result = sh.run(sh.argv_for("git", "init", "-q", str(root)))
                assert result.code == 0, result.err
                env = {"GIT_CONFIG_GLOBAL": str(config)}
                for kind, text in (
                    ("identity include", '[include]\n path = "' + identity.as_posix() + '"\n'),
                    ("global ignore", '[core]\n excludesFile = "' + ignores.as_posix() + '"\n'),
                    ("global ignore with untracked disabled", '[status]\n showUntrackedFiles = no\n[core]\n excludesFile = "' + ignores.as_posix() + '"\n'),
                ):
                    config.write_text(text, encoding="utf-8")
                    try:
                        git_readiness(root, env=env)
                    except sh.DriverError as ex:
                        assert "inaccessible configuration or excludes" in str(ex), str(ex)
                        print("Windows protected " + kind + ": readiness refused before dispatch")
                    else:
                        raise AssertionError("private Windows fixture was readable; protected control not established")
                config.write_text('[core]\n excludesFile = "' + readable_ignore.as_posix() + '"\n', encoding="utf-8")
                git_readiness(root, env=env)
                status = process.run(sandbox_argv(root, sh.argv_for(*git_read_argv(["git", "status", "--short"]))),
                                     cwd=root, env=env, timeout=30)
                assert status.code == 0 and "intentionally-ignored.txt" not in status.out, status
                print("Windows readable ignore control: intended ignore semantics preserved")
                commit = sh.run(sh.argv_for("git", "-c", "user.name=Fixture", "-c",
                                "user.email=fixture@example.invalid", "-c", "core.hooksPath=" + str(private),
                                "commit", "--allow-empty", "-qm", "fixture"), cwd=root, env=env)
                assert commit.code == 0, commit.err
                # Real tracked dependencies are absent until the prospective tree is materialized.
                leaf = root / "nested-config"
                entry = root / "tracked-config"
                entry.write_text('[include]\n path = nested-config\n', encoding="utf-8")
                scratch = pathlib.Path(tempfile.mkdtemp(prefix="ouro-run-")).resolve()
                def git(*args):
                    result = sh.run(sh.argv_for("git", *map(str, args)), cwd=root, env=env)
                    assert result.code == 0, result.text
                    return result.out
                try:
                    for branch, name, condition in (
                            ("ouro/fixture", "prospective", "onbranch:ouro/**"),
                            ("ouro/fixture", "prospective", "gitdir:" + (root / ".git/worktrees/prospective").as_posix()),
                            (None, "review-1", "gitdir:" + (root / ".git/worktrees/review-1").as_posix()),
                            (None, "checkpoint", "gitdir:" + (root / ".git/worktrees/checkpoint").as_posix())):
                        target = (root if branch else scratch) / name
                        for kind, text in (
                            ("identity", '[include]\n path = "' + identity.as_posix() + '"\n'),
                            ("ignore", '[core]\n excludesFile = "' + ignores.as_posix() + '"\n'),
                            ("ignore-untracked-disabled", '[status]\n showUntrackedFiles = no\n[core]\n excludesFile = "' + ignores.as_posix() + '"\n'),
                            ("readable", '[core]\n excludesFile = "' + readable_ignore.as_posix() + '"\n'),
                        ):
                            leaf.write_text(text, encoding="utf-8")
                            git("add", "tracked-config", "nested-config")
                            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                "-c", "core.hooksPath=" + str(private), "commit", "-qm", "tracked include fixture")
                            for dependency in (leaf, target / "tracked-config"):
                                config.write_text('[includeIf "' + condition + '"]\n path = "' +
                                                  dependency.as_posix() + '"\n', encoding="utf-8")
                                refs, source = git("show-ref"), git("status", "--porcelain")
                                with patch.dict(os.environ, env):
                                    try:
                                        git_capabilities.worktree_readiness(developer.Settings("codex"), root, target,
                                                                 branch, "HEAD", scratch=scratch)
                                    except sh.DriverError as ex:
                                        assert kind != "readable", str(ex)
                                        assert "inaccessible configuration or excludes" in str(ex), str(ex)
                                    else:
                                        assert kind == "readable", "conditional protected configuration admitted"
                                assert not target.exists()
                                assert git("show-ref") == refs
                                assert git("status", "--porcelain") == source
                                assert str(target) not in git("worktree", "list", "--porcelain")
                                print("Windows tracked conditional " + kind + ": proof before branch/setup")
                finally:
                    scratch.rmdir()
        else:
            print("SKIP: actual Windows protected Git configuration controls require Windows")

