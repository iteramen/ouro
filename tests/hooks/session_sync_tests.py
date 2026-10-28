"""hooks/session-sync.py over real repositories: a bare origin, a clone as the main checkout, and a
second clone that moves origin."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "session-sync.py"
ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.test",
       "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.test"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True,
                          encoding="utf-8").stdout.strip()


class SessionSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ouro-session-sync-")
        root = Path(self.tmp.name)
        self.origin, self.main, self.other = root / "origin.git", root / "main", root / "other"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "clone", "-q", str(self.origin), str(self.other))
        self.commit(self.other, ".claude/ouro.toml", "[repo]\n")
        git(self.other, "push", "-q", "origin", "HEAD:main")
        git(root, "clone", "-q", str(self.origin), str(self.main))

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, repo, rel, text):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"edit {rel}")

    def advance_origin(self):
        self.commit(self.other, "CLAUDE.md", "new rules\n")
        git(self.other, "push", "-q", "origin", "HEAD:main")
        return git(self.other, "rev-parse", "HEAD")

    def hook(self, cwd):
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(cwd)}),
                           env=ENV, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_behind_fast_forwards_and_asks_for_a_restart(self):
        tip = self.advance_origin()
        out = self.hook(self.main)
        self.assertEqual(git(self.main, "rev-parse", "HEAD"), tip)
        self.assertEqual((self.main / "CLAUDE.md").read_text(encoding="utf-8"), "new rules\n")
        self.assertIn("restart", out)

    def test_current_checkout_says_nothing(self):
        self.assertEqual(self.hook(self.main), "")

    def test_untouchable_checkouts_keep_their_head_and_say_why(self):
        cases = {
            "uncommitted": lambda: (self.main / ".claude/ouro.toml").write_text("[repo]\n# edit\n", encoding="utf-8"),
            "not main": lambda: git(self.main, "checkout", "-qb", "work"),
            "diverged": lambda: self.commit(self.main, "local.txt", "x\n"),
        }
        cases["ignored files in the way"] = lambda: self.ignored_in_the_way()
        for why, arrange in cases.items():
            with self.subTest(why):
                self.tearDown(); self.setUp()
                arrange()
                self.advance_origin()
                before = git(self.main, "rev-parse", "HEAD")
                out = self.hook(self.main)
                self.assertEqual(git(self.main, "rev-parse", "HEAD"), before)
                self.assertIn("may be stale", out)
                self.assertIn(why, out)
                if why.startswith("ignored"):
                    self.assertEqual((self.main / "secret.env").read_text(encoding="utf-8"), "local\n")

    def ignored_in_the_way(self):
        """An ignored local file that origin's next commit tracks: the merge must not replace it."""
        self.commit(self.other, ".gitignore", "secret.env\n")
        git(self.other, "push", "-q", "origin", "HEAD:main")
        git(self.main, "pull", "-q", "--ff-only")
        (self.main / "secret.env").write_text("local\n", encoding="utf-8")
        git(self.other, "rm", "-q", ".gitignore")
        self.commit(self.other, "secret.env", "template\n")

    def test_failed_fetch_is_a_notice(self):
        git(self.main, "remote", "set-url", "origin", str(Path(self.tmp.name) / "gone.git"))
        self.assertIn("fetch origin main failed", self.hook(self.main))

    def test_worktree_and_unbound_repository_are_untouched(self):
        worktree = Path(self.tmp.name) / "wt"
        git(self.main, "worktree", "add", "-q", "--detach", str(worktree))
        git(self.other, "rm", "-q", ".claude/ouro.toml")
        git(self.other, "commit", "-qm", "unbind")
        git(self.other, "push", "-q", "origin", "HEAD:main")
        unbound = Path(self.tmp.name) / "unbound"
        git(self.tmp.name, "clone", "-q", str(self.origin), str(unbound))  # origin/HEAD set, no binding
        self.advance_origin()
        before = git(worktree, "rev-parse", "HEAD"), git(unbound, "rev-parse", "HEAD")
        self.assertEqual(self.hook(worktree), "")
        self.assertEqual(self.hook(unbound), "")
        self.assertEqual((git(worktree, "rev-parse", "HEAD"), git(unbound, "rev-parse", "HEAD")), before)


if __name__ == "__main__":
    unittest.main()
