"""A PROBE review node launches without git or GitHub credentials on every backend; READ and BUILD keep their
environment; the review copy has no remote; a push through a nested interpreter or a renamed git finds neither the
credential helper nor the askpass program the developer's git config names, and a plain push from a Claude or a Codex
PROBE launch, whose home is private, finds no developer ~/.netrc."""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ouro import developer, sh
from ouro.runtime import PROBE, READ, SANDBOXED_PROBE, builder, claude, codex, diagnostics, process, qwen, tools

INHERITED = {**{key: "private-credential" for key in process.PUBLISHING_CREDENTIALS},
             "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.inherited", "GIT_CONFIG_VALUE_0": "kept",
             "CODEX_HOME": "/codex-home", "CLAUDE_CONFIG_DIR": "/claude-config",
             "OPENAI_API_KEY": "model-key", "ANTHROPIC_API_KEY": "model-key"}
PROBE_KEYS = {*process.PUBLISHING_CREDENTIALS, "GH_CONFIG_DIR", "GIT_SSH_COMMAND", "GIT_TERMINAL_PROMPT",
              "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1", "GIT_CONFIG_KEY_2", "GIT_CONFIG_VALUE_2"}
REVIEW = {"verdict": "CLEAN", "findings": [], "residue": [], "false_record_sentences": 0, "files_read": [], "stop": None}
BUILT = dict(subject="feat(app): x", body="", claims=[], gates_run=[], validation_pending=[], residue=[], manual=[],
             door="", blast_radius="", stop=None, replies=[])
CHECKED = {"finding": "x", "stop": None}


def empty_dir(path):
    return bool(path) and os.path.isdir(path) and not os.listdir(path)


def launched(env):
    """(the override map a launch was given, whether its GH_CONFIG_DIR was an empty directory at launch)"""
    return dict(env or {}), empty_dir((env or {}).get("GH_CONFIG_DIR"))


def bridge_env(argv):
    """The environment a Codex launch names for its MCP bridge: the last mcp_servers value, after the empty default."""
    return tomllib.loads(next(a for a in reversed(argv) if a.startswith("mcp_servers=")))["mcp_servers"]["ouro"]["env"]


def codex_answered(argv, answer):
    output = Path(argv[argv.index("--output-last-message") + 1])
    output.write_text(json.dumps(answer), encoding="utf-8")
    output.with_name("tools.jsonl").write_text(json.dumps({"tool": "list", "error": None}) + "\n", encoding="utf-8")
    return sh.R(0, '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.completed"}', "", argv)


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
        for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            self.assertEqual(resolved[key], INHERITED[key], f"{label}: the harness's own login stays")

    def assertPrivateHome(self, env, empty, label):
        """HOME, USERPROFILE and XDG_CONFIG_HOME name one directory that was empty at launch: no developer file."""
        resolved = process.child_env(env)
        self.assertTrue(empty, f"{label}: HOME is an empty directory at launch")
        self.assertEqual(resolved["USERPROFILE"], resolved["HOME"], label)
        self.assertEqual(resolved["XDG_CONFIG_HOME"], str(Path(resolved["HOME"]) / ".config"), label)

    def assertUnchanged(self, env, label, expected=None):
        self.assertFalse(PROBE_KEYS & set(env), f"{label}: the launch environment gains no PROBE override")
        if expected is not None:
            self.assertEqual(env, expected, label)

    def test_claude_probe_launches_without_credentials_and_read_and_build_unchanged(self):
        seen, homes = [], []

        def run(argv, **kwargs):
            seen.append(launched(kwargs.get("env")))
            homes.append(empty_dir(kwargs["env"].get("HOME")))
            return sh.R(0, json.dumps({"session_id": "s", "structured_output": REVIEW}), "", argv)
        settings = developer.Settings()
        with patch.dict(os.environ, INHERITED), patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=run):
            for name, capabilities in (("REVIEW", PROBE), ("REVIEW", SANDBOXED_PROBE), ("REVIEW", READ),
                                       ("BUILD", builder(["python3 check.py"]))):
                claude.execute(name, "p", "opus", capabilities, str(self.root), 5, None, {}, settings)
            for label, (env, empty), home in zip(("PROBE", "sandboxed PROBE"), seen, homes):
                self.assertCredentialFree(env, empty, "claude " + label)
                self.assertPrivateHome(env, home, "claude " + label)
                self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/claude-config", f"claude {label}: named past the private home")
        claude_env = {**process.worker_env(settings), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        self.assertUnchanged(seen[2][0], "claude READ", claude_env)
        self.assertUnchanged(seen[3][0], "claude BUILD", claude_env)

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
                self.assertNotIn("CLAUDE_CODE_DISABLE_AUTO_MEMORY", env, f"qwen {label}: the Claude-only switch stays out")
                self.assertEqual(process.child_env(env)["GH_TOKEN"], "private-credential", f"qwen {label} keeps GH_TOKEN")

    def test_codex_probe_launch_and_bridge_commands_run_without_credentials(self):
        seen, current, homes, bridges = [], [], [], []

        def run(argv, **kwargs):
            seen.append(launched(kwargs.get("env")))
            homes.append(empty_dir(kwargs["env"].get("HOME")))
            bridges.append(bridge_env(argv))
            return codex_answered(argv, BUILT if current[-1] == "BUILD" else REVIEW)
        settings = developer.Settings("codex")
        with patch.dict(os.environ, INHERITED), patch.object(codex, "WINDOWS", False), \
                patch.object(codex, "git_readiness"), patch.object(tools, "probe_root"), \
                patch.object(sh, "which", side_effect=lambda name: name), \
                patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(process, "run", side_effect=run):
            for name, capabilities in (("REVIEW", PROBE), ("BUILD", builder([]))):
                current.append(name)
                codex.execute(name, "p", "default", capabilities, str(self.root), 5, None, {}, settings)
            self.assertCredentialFree(*seen[0], "codex PROBE launch")
            self.assertPrivateHome(seen[0][0], homes[0], "codex PROBE launch")
        launch = seen[0][0]
        self.assertEqual(launch["CODEX_HOME"], "/codex-home", "codex PROBE launch: named past the private home")
        self.assertEqual(bridges[0], {"CODEX_HOME": "/codex-home",
                                      **{k: launch[k] for k in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME")}},
                         "the bridge gets the launch's private home and the real CODEX_HOME")
        self.assertUnchanged(seen[1][0], "codex BUILD", process.worker_env(settings))
        self.assertEqual(bridges[1], {"CODEX_HOME": "/codex-home"}, "a BUILD bridge keeps the developer's home")

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
        from ouro.run import Run, copy_git_dir, new_copy, remove_copy
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
        new_copy(copy)
        Run.review_clone(SimpleNamespace(env=SimpleNamespace(git=env_git, main=main, default="master")), copy, head)
        self.assertEqual(git("remote", cwd=copy), "", "the review copy has no remote")
        self.assertEqual(subprocess.run(["git", "config", "--get-regexp", r"^remote\."], cwd=copy,
                                        capture_output=True).returncode, 1, "nor any remote.* key")
        self.assertEqual(git("rev-parse", "HEAD", cwd=copy).strip(), head)
        self.assertEqual(git("rev-parse", "origin/master", cwd=copy).strip(), base, "the reviewer's range still resolves")
        self.assertEqual(git("status", "--porcelain", cwd=copy), "")
        self.assertEqual((copy / "a.txt").read_text(encoding="utf-8"), "head\n")
        gitdir = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=copy).strip()).resolve()
        self.assertEqual(gitdir, copy_git_dir(copy).resolve(), "a repository of its own, not a linked worktree")
        self.assertEqual(gitdir.parent, copy.resolve().parent, "its git directory is the copy's sibling")
        self.assertFalse(gitdir.is_relative_to(copy.resolve()), "outside the copy, so a write grant on the copy misses it")
        self.assertTrue((copy / ".git").is_file(), "the copy holds only the gitfile")
        self.assertEqual(git("remote", cwd=main), "origin\n", "the main checkout keeps its origin")
        linked = [p for root in (copy, gitdir) for p in root.rglob("*") if p.is_file() and p.stat().st_nlink != 1]
        self.assertEqual(linked, [], "no hard link into the main checkout's objects, which Codex PROBE admission refuses")
        if sys.platform in ("linux", "darwin"):  # probe_root refuses every other platform before reading the copy
            self.assertEqual(tools.probe_root(copy), copy.resolve(), "PROBE admission admits the gitfile layout")
        remove_copy(copy, "review copy")
        self.assertFalse(copy.exists() or gitdir.exists(), "the round leaves neither the copy nor its git directory")
        copy_git_dir(copy).mkdir()
        with self.assertRaises(FileExistsError):
            new_copy(copy)
        self.assertFalse(copy.exists(), "an existing git directory refuses the copy, and leaves no copy behind")

    def developer_environment(self, home, **extra):
        """This process's environment with no credential, proxy or harness-home variable, and `home` as HOME."""
        drop = {*process.PUBLISHING_CREDENTIALS, "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "http_proxy",
                "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy",
                "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"}
        return {**{k: v for k, v in os.environ.items() if k not in drop},
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "HOME": str(home), **extra}

    def unauthorized_remote(self, env):
        """(an http URL that answers 401 and records each request's Authorization, those records, a clone with a
        commit to push); the clone's own remote is a local bare repository, the only one reachable."""
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
        remote, work = self.root / "remote.git", self.root / "work"
        git("init", "--quiet", "--bare", str(remote), cwd=self.root, env=env)
        git("clone", "--quiet", str(remote), str(work), cwd=self.root, env=env)
        git("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet",
            "--allow-empty", "-m", "x", cwd=work, env=env)
        return f"http://127.0.0.1:{server.server_address[1]}/remote.git", requests, work

    @unittest.skipIf(os.name == "nt", "the credential helper stub and renamed-git rows run on POSIX")
    def test_a_push_through_a_nested_interpreter_or_a_renamed_git_finds_no_configured_credential(self):
        marker = self.root / "helper-called"
        helper = self.root / "helper.py"
        helper.write_text("import sys\nsys.stdin.read()\nopen(%r, 'a').write(sys.argv[1] + '\\n')\n"
                          "if sys.argv[1] == 'get':\n    print('username=fixture')\n    print('password=fixture')\n"
                          % str(marker), encoding="utf-8")
        askpass = self.root / "askpass.sh"
        askpass.write_text(f"#!/bin/sh\necho ask >> '{marker}'\necho fixture\n", encoding="utf-8")
        askpass.chmod(0o755)
        global_config = self.root / "global.gitconfig"
        normal = self.developer_environment(self.root, GIT_CONFIG_GLOBAL=str(global_config))
        gh = self.root / "gh"
        gh.mkdir()
        probe = {k: v for k, v in {**normal, **process.probe_env(True, gh, normal)}.items() if v is not None}
        url, requests, work = self.unauthorized_remote(normal)
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

    @unittest.skipIf(os.name == "nt", "the ~/.netrc push rows run on POSIX, where curl reads HOME")
    def test_a_plain_push_from_a_claude_or_codex_probe_launch_sends_no_developer_netrc_entry(self):
        # The developer's ~/.netrc names this host; curl inside git reads it from HOME whatever the helpers say.
        developer_home = self.root / "developer"
        developer_home.mkdir()
        netrc = developer_home / ".netrc"
        netrc.write_text("machine 127.0.0.1 login fixture password fixture\n", encoding="utf-8")
        netrc.chmod(0o600)
        developer_env = self.developer_environment(developer_home)
        url, requests, work = self.unauthorized_remote(developer_env)
        pushed = {}

        def push(label, env):
            requests.clear()
            result = subprocess.run(["git", "push", url, "HEAD:refs/heads/probe"], cwd=work, env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", timeout=60)
            pushed[label] = result, list(requests), env

        def claude_run(argv, env=None, **kwargs):
            push("claude", process.child_env(env))
            return sh.R(0, json.dumps({"session_id": "s", "structured_output": REVIEW}), "", argv)

        def codex_run(argv, env=None, **kwargs):
            push("codex", process.child_env(env))
            pushed["bridge"] = bridge_env(argv)
            return codex_answered(argv, REVIEW)
        push("developer", developer_env)
        with patch.dict(os.environ, developer_env, clear=True), patch.object(codex, "WINDOWS", False), \
                patch.object(codex, "git_readiness"), patch.object(tools, "probe_root"), \
                patch.object(sh, "which", side_effect=lambda name: name), \
                patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=claude_run), patch.object(process, "run", side_effect=codex_run):
            claude.execute("REVIEW", "p", "opus", PROBE, str(self.root), 5, None, {}, developer.Settings())
            codex.execute("REVIEW", "p", "default", PROBE, str(self.root), 5, None, {}, developer.Settings("codex"))
        for label in ("developer", "claude", "codex"):
            result, sent, _ = pushed[label]
            with self.subTest(netrc=label):
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertTrue(sent, f"the push reached the credential-requiring URL: {result.stderr}")
                if label == "developer":
                    self.assertTrue(any(sent), "positive control: the developer's own environment sends the ~/.netrc entry")
                else:
                    self.assertFalse(any(sent), f"a plain push from a {label} PROBE launch sent the ~/.netrc entry")
        self.assertNotIn("CLAUDE_CONFIG_DIR", pushed["claude"][2], "an undeclared CLAUDE_CONFIG_DIR stays undeclared")
        self.assertEqual((pushed["codex"][2]["CODEX_HOME"], pushed["bridge"]["CODEX_HOME"]),
                         (str(developer_home / ".codex"),) * 2, "an undeclared CODEX_HOME is the developer's ~/.codex")

    def test_an_undeclared_claude_config_dir_probe_reads_the_developer_state_and_writes_none_into_it(self):
        # Claude Code keeps its global .claude.json under a set CLAUDE_CONFIG_DIR, else at ~/.claude.json.
        developer_home = self.root / "developer"
        (developer_home / ".claude").mkdir(parents=True)
        (developer_home / ".claude" / ".credentials.json").write_text("login", encoding="utf-8")
        (developer_home / ".claude.json").write_text('{"oauthAccount": "developer"}', encoding="utf-8")
        seen = {}

        def run(argv, env=None, **kwargs):
            resolved = process.child_env(env)
            home = Path(resolved["HOME"])
            seen.update(env=resolved, credentials=(home / ".claude" / ".credentials.json").read_text(encoding="utf-8"),
                        link=(home / ".claude").is_junction() if os.name == "nt" else (home / ".claude").is_symlink(),
                        state=(home / ".claude.json").read_text(encoding="utf-8"),
                        copied=(home / ".claude.json").is_file() and not (home / ".claude.json").is_symlink())
            (home / ".claude.json").write_text('{"oauthAccount": "probe"}', encoding="utf-8")
            return sh.R(0, json.dumps({"session_id": "s", "structured_output": REVIEW}), "", argv)
        # Path.home() reads USERPROFILE on Windows.
        with patch.dict(os.environ, self.developer_environment(
                developer_home, USERPROFILE=str(developer_home), CLAUDE_CONFIG_DIR=""), clear=True), \
                patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(sh, "run", side_effect=run):
            claude.execute("REVIEW", "p", "opus", PROBE, str(self.root), 5, None, {}, developer.Settings())
        self.assertNotIn("CLAUDE_CONFIG_DIR", seen["env"], "the variable would move .claude.json into ~/.claude")
        self.assertNotEqual(seen["env"]["HOME"], str(developer_home), "the home is still private")
        self.assertEqual((seen["link"], seen["credentials"]), (True, "login"), "~/.claude is the developer's, linked")
        self.assertEqual((seen["copied"], seen["state"]), (True, '{"oauthAccount": "developer"}'),
                         "~/.claude.json is a copy of the developer's")
        self.assertFalse((developer_home / ".claude" / ".claude.json").exists(), "nothing new in the developer's ~/.claude")
        self.assertEqual((developer_home / ".claude.json").read_text(encoding="utf-8"), '{"oauthAccount": "developer"}',
                         "the launch's writes to its global state stay in the scratch home")
        self.assertEqual((developer_home / ".claude" / ".credentials.json").read_text(encoding="utf-8"), "login",
                         "removing the scratch home leaves the linked ~/.claude in place")


if __name__ == "__main__":
    unittest.main(verbosity=2)
