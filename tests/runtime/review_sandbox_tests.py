"""ship.review_sandbox: which reviewer backend and platform the key admits, how this host's platform is read, and the
settings a sandboxed Claude or Qwen Code PROBE review launches with."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ouro import developer
from ouro.runtime import review_sandbox


def uname(sysname, release, version="#1 SMP"):
    return SimpleNamespace(sysname=sysname, release=release, version=version)


def which(*present):
    return patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in present else None)


class ReviewSandboxTests(unittest.TestCase):
    def test_the_route_and_platform_matrix(self):
        with which("bwrap", "socat"):
            for where in ("linux", "wsl2", "macos"):
                for backend in ("codex", "claude"):
                    self.assertIsNone(review_sandbox.refusal(developer.Settings(backend), where), (backend, where))
            for where in ("wsl2", "macos"):
                why = review_sandbox.refusal(developer.Settings("qwen"), where)
                self.assertIn(f"selects Qwen Code on {where}: missing a Qwen Code sandbox route", why)
            for where in ("windows", "wsl1", "freebsd"):
                for backend, label in (("codex", "Codex"), ("claude", "Claude"), ("qwen", "Qwen Code")):
                    why = review_sandbox.refusal(developer.Settings(backend), where)
                    self.assertIn(f"selects {label} on {where}", why)
                    self.assertIn("missing a platform with a harness sandbox", why)

    def test_claude_on_linux_and_wsl2_needs_bwrap_and_socat_on_path_and_macos_nothing(self):
        for where in ("linux", "wsl2"):
            for present, missing in (((), "bwrap on PATH and socat on PATH"), (("socat",), "bwrap on PATH"),
                                     (("bwrap",), "socat on PATH")):
                with which(*present):
                    why = review_sandbox.refusal(developer.Settings("claude"), where)
                self.assertIn(f"selects Claude on {where}: missing {missing}, which the Claude sandbox needs", why)
        with which():
            self.assertIsNone(review_sandbox.refusal(developer.Settings("claude"), "macos"))
            self.assertIsNone(review_sandbox.refusal(developer.Settings("codex"), "linux"), "Codex keeps its own boundary")

    def test_the_reviewer_role_decides_not_runtime_backend(self):
        qwen_reviewer = developer.Settings("codex", roles={"reviewer": {"backend": "qwen"}})
        self.assertIn("selects Qwen Code", review_sandbox.refusal(qwen_reviewer, "macos"))
        codex_reviewer = developer.Settings("qwen", roles={"reviewer": {"backend": "codex"}})
        self.assertIsNone(review_sandbox.refusal(codex_reviewer, "linux"))
        with which():
            claude_reviewer = developer.Settings("codex", roles={"reviewer": {"backend": "claude"}})
            self.assertIn("selects Claude", review_sandbox.refusal(claude_reviewer, "linux"))

    def test_a_sandboxed_claude_probe_launches_with_the_sandbox_settings(self):
        from ouro import sh
        from ouro.runtime import PROBE, SANDBOXED_PROBE, claude
        envelope = {"session_id": "s", "structured_output": {"verdict": "CLEAN", "findings": []}}
        seen = []

        def run(argv, **kwargs):
            env = kwargs.get("env") or {}
            temp = env.get("CLAUDE_CODE_TMPDIR")
            seen.append((argv, kwargs.get("cwd"), env, temp, temp is not None and os.path.isdir(temp)))
            return sh.R(0, json.dumps(envelope), "", argv)
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(sh, "run", side_effect=run):
            for tools in (SANDBOXED_PROBE, PROBE):
                claude.execute("REVIEW", "p", "opus", tools, "/review-copy", 5, None, {}, developer.Settings())
        (sandboxed, cwd, env, temp, existed), (plain, _, plain_env, _, _) = seen
        # Claude Code's sandbox admits writes under <CLAUDE_CODE_TMPDIR or TMPDIR>/claude-<uid>: never the uid's shared one
        self.assertTrue(existed, "the sandbox's temp root exists while the node runs")
        self.assertEqual(env.get("TMPDIR"), temp, "TMPDIR, the fallback root, is the same private directory")
        self.assertFalse(os.path.exists(temp), "the temp root is deleted when the launch returns")
        self.assertNotIn("CLAUDE_CODE_TMPDIR", plain_env)
        self.assertNotIn("TMPDIR", plain_env)
        settings = json.loads(sandboxed[sandboxed.index("--settings") + 1])
        self.assertEqual(settings, {"sandbox": {"enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
                                                "network": {"allowedDomains": []}}})
        self.assertEqual(cwd, "/review-copy", "the sandbox's write scope is the cwd, the review copy")
        self.assertIn("Bash", sandboxed[sandboxed.index("--allowedTools") + 1].split(","))
        self.assertEqual(sandboxed[sandboxed.index("--disallowedTools") + 1], "Bash(git push *),Bash(git commit *),Bash(gh *)")
        self.assertNotIn("--settings", plain, "without the key a PROBE review launches as before")

    def test_qwen_has_a_route_on_linux_alone_and_needs_usr_bin_bwrap(self):
        from ouro.runtime import qwen
        for present in (True, False):
            with patch.object(qwen.os.path, "exists", side_effect=lambda path: present and path == qwen.BWRAP):
                why = review_sandbox.refusal(developer.Settings("qwen"), "linux")
            if present:
                self.assertIsNone(why)
            else:
                self.assertIn("selects Qwen Code on linux: missing /usr/bin/bwrap, which the Qwen Code sandbox needs", why)

    def test_a_sandboxed_qwen_probe_review_launches_under_the_tool_sandbox_and_answers_without_the_hook(self):
        from ouro import sh
        from ouro.runtime import PROBE, SANDBOXED_PROBE, Capabilities, process, qwen
        answer = {"verdict": "CLEAN", "findings": [], "false_record_sentences": 0, "files_read": [], "stop": None}
        # A shell call no hook granted: under the sandbox no executable hook runs.
        events = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c0", "name": "run_shell_command",
                                                                 "input": {"command": "git status"}}]}},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c0", "is_error": False,
                                                            "content": "clean"}]}},
                  {"type": "result", "subtype": "success", "session_id": "q-1", "is_error": False, "structured_result": answer}]
        seen = []

        def run(argv, cwd=None, prompt=None, timeout=None, env=None):
            system = Path(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"]).read_text(encoding="utf-8")
            seen.append((argv, cwd, json.loads(system)))
            return sh.R(0, json.dumps(events), "", argv)
        settings = developer.Settings("qwen")
        with tempfile.TemporaryDirectory() as copy, patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(process, "run", side_effect=run):
            reviewed = qwen._execute("REVIEW", "p", "m", SANDBOXED_PROBE, copy, 5, None, {}, settings, {})
            with self.assertRaisesRegex(sh.DriverError, "run_shell_command ran without the allow-list hook's grant"):
                qwen._execute("REVIEW", "p", "m", PROBE, copy, 5, None, {}, settings, {})
            for name, tools in (("BUILD", SANDBOXED_PROBE), ("REVIEW", Capabilities(sandbox=True))):
                with self.assertRaisesRegex(sh.DriverError, "the tool execution sandbox serves a PROBE review node alone"):
                    qwen._execute(name, "p", "m", tools, copy, 5, None, {}, settings, {})
        self.assertEqual(len(seen), 2, "a sandbox on any other node is refused before launch")
        (argv, cwd, system), (_, _, plain) = seen
        self.assertEqual(reviewed[0]["verdict"], "CLEAN", "the hook-grant check does not discard the sandboxed answer")
        self.assertEqual(system["tools"], {"executionSandbox": {"backend": "bwrap", "filesystem": "workspace-write",
                                                                "network": "closed"}})
        self.assertNotIn("hooks", system)
        self.assertEqual(cwd, copy, "workspace-write admits the cwd, the review copy")
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "yolo")
        deny = system["permissions"]["deny"]
        self.assertLessEqual({"Bash(git push *)", "Bash(git commit *)", "Bash(gh *)", "write_file", "edit", "web_fetch"}, set(deny))
        self.assertNotIn("run_shell_command", deny)
        self.assertIn("hooks", plain, "without the key a PROBE review keeps the allow-list hook")
        self.assertNotIn("tools", plain)
        self.assertFalse([rule for rule in plain["permissions"]["deny"] if rule.startswith("Bash(")])

    @unittest.skipIf(os.name == "nt", "os.uname is POSIX-only; native Windows is read from os.name")
    def test_platform_reads_kernel_facts(self):
        cases = {("Linux", "6.8.0-146-generic"): "linux",
                 ("Linux", "5.15.167.4-microsoft-standard-WSL2"): "wsl2",
                 ("Linux", "4.19.104-microsoft-standard"): "wsl2",
                 ("Linux", "4.4.0-19041-Microsoft"): "wsl1",
                 ("Darwin", "24.1.0"): "macos",
                 ("FreeBSD", "14.1-RELEASE"): "freebsd"}
        for (sysname, release), want in cases.items():
            with patch.object(review_sandbox.os, "uname", return_value=uname(sysname, release)), \
                    patch.object(review_sandbox.Path, "exists", return_value=False):
                self.assertEqual(review_sandbox.platform(), want, release)
        with patch.object(review_sandbox.os, "uname", return_value=uname("Linux", "4.4.0-19041-generic")), \
                patch.object(review_sandbox.Path, "exists", return_value=True):
            self.assertEqual(review_sandbox.platform(), "wsl1", "a WSL marker on a kernel that names no WSL2 build")
        with patch.object(review_sandbox.os, "name", "nt"):
            self.assertEqual(review_sandbox.platform(), "windows")
