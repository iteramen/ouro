"""A PROBE review node launches without git or GitHub credentials on every backend; READ and BUILD keep their
environment; the review copy has no remote; a push through a nested interpreter or a renamed git finds neither the
credential helper nor the askpass program the developer's git config names."""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ouro import developer, sh
from ouro.runtime import PROBE, READ, SANDBOXED_PROBE, builder, claude, codex, diagnostics, process, qwen, tools

INHERITED = {**{key: "private-credential" for key in process.PUBLISHING_CREDENTIALS},
             "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.inherited", "GIT_CONFIG_VALUE_0": "kept",
             "CODEX_HOME": "/codex-home", "OPENAI_API_KEY": "model-key", "ANTHROPIC_API_KEY": "model-key"}
PROBE_KEYS = {*process.PUBLISHING_CREDENTIALS, "GH_CONFIG_DIR", "GIT_SSH_COMMAND", "GIT_TERMINAL_PROMPT",
              "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1", "GIT_CONFIG_KEY_2", "GIT_CONFIG_VALUE_2"}
REVIEW = {"verdict": "CLEAN", "findings": [], "false_record_sentences": 0, "files_read": [], "stop": None}
BUILT = dict(subject="feat(app): x", body="", claims=[], gates_run=[], validation_pending=[], residue=[], manual=[],
             door="", blast_radius="", stop=None, replies=[])
CHECKED = {"finding": "x", "stop": None}


def launched(env):
    """(the override map a launch was given, whether its GH_CONFIG_DIR was an empty directory at launch)"""
    gh = (env or {}).get("GH_CONFIG_DIR")
    return dict(env or {}), bool(gh) and os.path.isdir(gh) and not os.listdir(gh)


def git(*args, cwd, env=None):
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout


class ProbeCredentialTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="ouro-probe-credentials-")
        self.addCleanup(folder.cleanup)
        self.addCleanup(diagnostics.ATTEMPTS.clear)
        self.root = Path(folder.name)

    def assertCredentialFree(self, env, empty_gh, label):
        resolved = process.child_env(env)
        for key in process.PUBLISHING_CREDENTIALS:
            self.assertNotIn(key, resolved, f"{label}: {key} reaches the node")
        self.assertTrue(empty_gh, f"{label}: GH_CONFIG_DIR is an empty directory at launch")
        self.assertEqual(resolved["GIT_TERMINAL_PROMPT"], "0", label)
        self.assertEqual(resolved["GIT_SSH_COMMAND"], process.NO_IDENTITY_SSH, label)
        for option in ("-F /dev/null", "IdentityAgent=none", "IdentitiesOnly=yes", "IdentityFile=none"):
            self.assertIn(option, resolved["GIT_SSH_COMMAND"], label)
        self.assertEqual((resolved["GIT_CONFIG_COUNT"], resolved["GIT_CONFIG_KEY_0"], resolved["GIT_CONFIG_VALUE_0"]),
                         ("3", "core.inherited", "kept"), f"{label}: the inherited git config entry is kept")
        self.assertEqual((resolved["GIT_CONFIG_KEY_1"], resolved["GIT_CONFIG_VALUE_1"]), ("credential.helper", ""), label)
        self.assertEqual((resolved["GIT_CONFIG_KEY_2"], resolved["GIT_CONFIG_VALUE_2"]), ("core.askPass", ""), label)
        for key in ("CODEX_HOME", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            self.assertEqual(resolved[key], INHERITED[key], f"{label}: the harness's own login stays")

    def assertUnchanged(self, env, label, expected=None):
        self.assertFalse(PROBE_KEYS & set(env), f"{label}: the launch environment gains no PROBE override")
        if expected is not None:
            self.assertEqual(env, expected, label)

    def test_claude_probe_launches_without_credentials_and_read_and_build_unchanged(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(launched(kwargs.get("env")))
            return sh.R(0, json.dumps({"session_id": "s", "structured_output": REVIEW}), "", argv)
        settings = developer.Settings()
        with patch.dict(os.environ, INHERITED), patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=run):
            for name, capabilities in (("REVIEW", PROBE), ("REVIEW", SANDBOXED_PROBE), ("REVIEW", READ),
                                       ("BUILD", builder(["python3 check.py"]))):
                claude.execute(name, "p", "opus", capabilities, str(self.root), 5, None, {}, settings)
            for label, (env, empty) in zip(("PROBE", "sandboxed PROBE"), seen):
                self.assertCredentialFree(env, empty, "claude " + label)
        self.assertUnchanged(seen[2][0], "claude READ", process.worker_env(settings))
        self.assertUnchanged(seen[3][0], "claude BUILD", process.worker_env(settings))

    def test_qwen_probe_launches_without_credentials_and_read_and_build_unchanged(self):
        seen = []

        answers = {"REVIEW": REVIEW, "CHECKPOINT": CHECKED, "BUILD": BUILT}
        current = []

        def run(argv, cwd=None, prompt=None, timeout=None, env=None):
            seen.append(launched(env))
            return sh.R(0, json.dumps([{"type": "result", "subtype": "success", "session_id": "q", "is_error": False,
                                        "structured_result": answers[current[-1]]}]), "", argv)
        settings = developer.Settings("qwen")
        with patch.dict(os.environ, INHERITED), patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(process, "run", side_effect=run):
            for name, capabilities in (("REVIEW", PROBE), ("REVIEW", SANDBOXED_PROBE), ("CHECKPOINT", READ),
                                       ("BUILD", builder([]))):
                current.append(name)
                qwen._execute(name, "p", "m", capabilities, str(self.root), 5, None, {}, settings, {})
            # Uncovered here: whether Qwen Code's bwrap sandbox hands this environment on to its commands.
            for label, (env, empty) in zip(("PROBE", "sandboxed PROBE"), seen):
                self.assertCredentialFree(env, empty, "qwen " + label)
            for label, (env, _) in zip(("READ", "BUILD"), seen[2:]):
                self.assertUnchanged(env, "qwen " + label)
                self.assertEqual(process.child_env(env)["GH_TOKEN"], "private-credential", f"qwen {label} keeps GH_TOKEN")

    def test_codex_probe_launch_and_bridge_commands_run_without_credentials(self):
        seen, current = [], []

        def run(argv, **kwargs):
            seen.append(launched(kwargs.get("env")))
            output = Path(argv[argv.index("--output-last-message") + 1])
            output.write_text(json.dumps(BUILT if current[-1] == "BUILD" else REVIEW), encoding="utf-8")
            output.with_name("tools.jsonl").write_text(json.dumps({"tool": "list", "error": None}) + "\n", encoding="utf-8")
            return sh.R(0, '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed"}', "", argv)
        settings = developer.Settings("codex")
        with patch.dict(os.environ, INHERITED), patch.object(codex, "WINDOWS", False), \
                patch.object(codex, "git_readiness"), patch.object(tools, "probe_root"), \
                patch.object(sh, "which", side_effect=lambda name: name), \
                patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(process, "run", side_effect=run):
            for name, capabilities in (("REVIEW", PROBE), ("BUILD", builder([]))):
                current.append(name)
                codex.execute(name, "p", "default", capabilities, str(self.root), 5, None, {}, settings)
            self.assertCredentialFree(*seen[0], "codex PROBE launch")
        self.assertUnchanged(seen[1][0], "codex BUILD", process.worker_env(settings))

        commands = []

        def command(argv, **kwargs):
            commands.append(launched(kwargs.get("env")))
            return sh.R(0, "ran", "", argv)
        copy = (self.root / "copy").resolve()
        copy.mkdir()
        probe = tools.Bridge(dict(root=str(copy), edit=False, commands=[], probe=True))
        listed = tools.Bridge(dict(root=str(copy), edit=False, commands=["python check.py"], probe=False))
        # Codex hands the bridge a filtered environment: the bridge applies the removals to each command itself.
        with patch.dict(os.environ, INHERITED), patch.object(tools.sys, "platform", "darwin"), \
                patch.object(sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]), \
                patch.object(tools.process, "run", side_effect=command), patch.object(tools, "probe_configuration"):
            self.assertEqual(probe.run("python check.py")["exit"], 0)
            self.assertEqual(listed.run("python check.py")["exit"], 0)
            self.assertCredentialFree(*commands[0], "codex bridge PROBE command")
        self.assertUnchanged(commands[1][0], "codex bridge listed command")
        self.assertFalse(os.path.exists(commands[0][0]["GH_CONFIG_DIR"]), "the command's gh directory is deleted after it")

    def test_an_unreadable_inherited_count_refuses_and_a_non_probe_node_gets_nothing(self):
        self.assertEqual(process.probe_env(False, self.root), {})
        with self.assertRaisesRegex(sh.DriverError, "GIT_CONFIG_COUNT is 'two', not a count"):
            process.probe_env(True, self.root, {"GIT_CONFIG_COUNT": "two"})
        self.assertEqual(process.probe_env(True, self.root, {})["GIT_CONFIG_KEY_0"], "credential.helper")

    def test_the_review_copy_is_a_standalone_repository_with_no_remote(self):
        from ouro.run import Run
        main, remote = self.root / "main", self.root / "remote.git"
        git("init", "--quiet", "--bare", str(remote), cwd=self.root)
        git("init", "--quiet", "-b", "master", str(main), cwd=self.root)
        identity = ("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid")
        (main / "a.txt").write_text("base\n", encoding="utf-8")
        git("add", "a.txt", cwd=main)
        git(*identity, "commit", "--quiet", "-m", "base", cwd=main)
        git("remote", "add", "origin", str(remote), cwd=main)
        git("push", "--quiet", "origin", "master", cwd=main)
        git("fetch", "--quiet", "origin", cwd=main)
        base = git("rev-parse", "origin/master", cwd=main).strip()
        git("checkout", "--quiet", "-b", "feature", cwd=main)
        (main / "a.txt").write_text("head\n", encoding="utf-8")
        git(*identity, "commit", "--quiet", "-am", "head", cwd=main)
        head = git("rev-parse", "HEAD", cwd=main).strip()

        def env_git(*args, cwd=None, check=True):
            r = subprocess.run(["git", *args], cwd=cwd or main, capture_output=True, encoding="utf-8", timeout=60)
            if check and r.returncode:
                raise sh.DriverError(r.stderr)
            return sh.R(r.returncode, r.stdout, r.stderr, args)
        copy = self.root / "review-1"
        copy.mkdir()
        Run.review_clone(SimpleNamespace(env=SimpleNamespace(git=env_git, main=main, default="master")), copy, head)
        self.assertEqual(git("remote", cwd=copy), "", "the review copy has no remote")
        self.assertEqual(subprocess.run(["git", "config", "--get-regexp", r"^remote\."], cwd=copy,
                                        capture_output=True).returncode, 1, "nor any remote.* key")
        self.assertEqual(git("rev-parse", "HEAD", cwd=copy).strip(), head)
        self.assertEqual(git("rev-parse", "origin/master", cwd=copy).strip(), base, "the reviewer's range still resolves")
        self.assertEqual(git("status", "--porcelain", cwd=copy), "")
        self.assertEqual((copy / "a.txt").read_text(encoding="utf-8"), "head\n")
        self.assertTrue((copy / ".git").is_dir(), "a repository of its own, not a linked worktree")
        self.assertEqual(git("remote", cwd=main), "origin\n", "the main checkout keeps its origin")
        linked = [p for p in copy.rglob("*") if p.is_file() and p.stat().st_nlink != 1]
        self.assertEqual(linked, [], "no hard link into the main checkout's objects, which Codex PROBE admission refuses")

    @unittest.skipIf(os.name == "nt", "the credential helper stub and renamed-git rows run on POSIX")
    def test_a_push_through_a_nested_interpreter_or_a_renamed_git_finds_no_configured_credential(self):
        requests = []

        class Unauthorized(http.server.BaseHTTPRequestHandler):
            def answer(self):
                requests.append(self.headers.get("Authorization"))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_GET = do_POST = answer

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/remote.git"

        marker = self.root / "helper-called"
        helper = self.root / "helper.py"
        helper.write_text("import sys\nsys.stdin.read()\nopen(%r, 'a').write(sys.argv[1] + '\\n')\n"
                          "if sys.argv[1] == 'get':\n    print('username=fixture')\n    print('password=fixture')\n"
                          % str(marker), encoding="utf-8")
        askpass = self.root / "askpass.sh"
        askpass.write_text(f"#!/bin/sh\necho ask >> '{marker}'\necho fixture\n", encoding="utf-8")
        askpass.chmod(0o755)
        global_config = self.root / "global.gitconfig"
        drop = {*process.PUBLISHING_CREDENTIALS, "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "http_proxy",
                "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"}
        normal = {**{k: v for k, v in os.environ.items() if k not in drop}, "GIT_CONFIG_GLOBAL": str(global_config),
                  "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "HOME": str(self.root)}
        gh = self.root / "gh"
        gh.mkdir()
        probe = {k: v for k, v in {**normal, **process.probe_env(True, gh, normal)}.items() if v is not None}

        remote, work = self.root / "remote.git", self.root / "work"
        git("init", "--quiet", "--bare", str(remote), cwd=self.root, env=normal)  # the only reachable remote
        git("clone", "--quiet", str(remote), str(work), cwd=self.root, env=normal)
        git("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet",
            "--allow-empty", "-m", "x", cwd=work, env=normal)
        nested = [sys.executable, "-c", "import subprocess, sys; sys.exit(subprocess.run("
                  "['git', 'push', sys.argv[1], 'HEAD:refs/heads/probe']).returncode)", url]
        shapes = {"python3 -c running git push": nested}
        if sys.platform == "linux":
            renamed = self.root / "publish"
            shutil.copy(shutil.which("git"), renamed)
            shapes["a renamed copy of git running push"] = [str(renamed), "push", url, "HEAD:refs/heads/probe"]
        else:
            print(f"SKIP: the renamed-git push row runs on Linux; this is {sys.platform}")
        # The developer's global config names one credential source per row: a helper, or an askpass program.
        sources = {"credential.helper": (f'!"{sys.executable}" "{helper}"', "get"), "core.askPass": (str(askpass), "ask")}
        for (shape, argv), (key, (value, called)) in ((s, c) for s in shapes.items() for c in sources.items()):
            global_config.write_text("", encoding="utf-8")
            git("config", "--file", str(global_config), key, value, cwd=self.root)
            for env, credentials in ((normal, True), (probe, False)):
                with self.subTest(shape=shape, source=key, credentials=credentials):
                    marker.unlink(missing_ok=True)
                    requests.clear()
                    result = subprocess.run(argv, cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                            encoding="utf-8", timeout=60)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(requests, f"the push reached the credential-requiring URL: {result.stderr}")
                    if credentials:
                        # Positive control: the same push, with the developer's environment, does find a credential.
                        self.assertIn(called, marker.read_text(encoding="utf-8").split(), result.stderr)
                        self.assertTrue(any(requests), f"the {key} credential was sent")
                    else:
                        self.assertFalse(marker.exists(), f"the PROBE environment ran {key}: {result.stderr}")
                        self.assertFalse(any(requests), "no credential was sent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
