"""Qwen Code adapter on recorded envelopes: argv, isolation settings, the edit refusal and the hook."""
import dataclasses
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ouro import developer, sh
from ouro.runtime import PROBE, READ, Capabilities, builder, process, qwen, qwen_isolation, schema

ANSWER = {"finding": "the interface matrix", "stop": None}
# The event array Qwen Code 0.26.6 prints for `-o json`, trimmed to the fields the adapter reads.
EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "s-1", "qwen_code_version": "0.26.6"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c0", "name": "read_file", "input": {"file_path": "a.txt"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c0", "is_error": False, "content": "hello"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c1", "name": "run_shell_command", "input": {"command": "touch x"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c1", "is_error": True,
                                              "content": "Qwen Code requires permission to use \"run_shell_command\", but that permission was declined."}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c2", "name": "structured_output", "input": ANSWER}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c2", "is_error": False, "content": "Structured output accepted."}]}},
    {"type": "result", "subtype": "success", "session_id": "s-1", "is_error": False, "num_turns": 3,
     "usage": {"input_tokens": 66, "output_tokens": 42, "cache_read_input_tokens": 0},
     "permission_denials": [{"tool_name": "run_shell_command", "tool_input": {"command": "touch x"}}],
     "structured_result": ANSWER},
]
# What the hook logs for EVENTS: the shell call never reaches it (permissions.deny removes the tool first).
HOOKED = [{"tool": "read_file", "granted": True}, {"tool": "structured_output", "granted": True}]


def result(**fields):
    return [*EVENTS[:-1], {**EVENTS[-1], **fields}]


def text_run(text, message="Model produced plain text instead of calling the structured_output tool"):
    return [*EVENTS[:5], {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
            {**EVENTS[-1], "subtype": "error_during_execution", "is_error": True, "structured_result": None,
             "error": {"message": message}}]


class QwenTests(unittest.TestCase):
    def execute(self, events, *, tools=READ, model="qwen3-coder", code=0, hooked=HOOKED, raises=None, name="CHECKPOINT"):
        seen = {}

        def run(argv, cwd=None, prompt=None, timeout=None, env=None):
            system = pathlib.Path(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"])
            policy = system.with_name("policy.json")
            seen.update(argv=argv, input=prompt, env=env, evidence=system.with_name("hook.jsonl"), cwd=cwd,
                        settings=json.loads(system.read_text(encoding="utf-8")),
                        trust=json.loads(pathlib.Path(env["QWEN_CODE_TRUSTED_FOLDERS_PATH"]).read_text(encoding="utf-8")),
                        policy=json.loads(policy.read_text(encoding="utf-8")) if policy.exists() else None)
            if raises:
                raise raises
            system.with_name("hook.jsonl").write_text("".join(json.dumps(h) + "\n" for h in hooked), encoding="utf-8")
            return SimpleNamespace(code=code, out=events if isinstance(events, str) else json.dumps(events), err="stderr text")
        with tempfile.TemporaryDirectory() as d, patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(process, "run", side_effect=run):
            pathlib.Path(d, "AGENTS.md").write_text("repo rule", encoding="utf-8")
            out = qwen._execute(name, "the brief", model, tools, d, 30, None, {}, developer.Settings("qwen"), {})
        return out, seen

    def test_the_command_line_is_headless_file_only_and_takes_the_prompt_on_stdin(self):
        (answer, session, usage, cost), seen = self.execute(EVENTS)
        argv = seen["argv"]
        self.assertEqual(argv[:9], ["qwen", "--auth-type", "openai", "--output-format", "json", "--approval-mode", "default",
                                    "--json-schema", json.dumps(schema.SCHEMAS["CHECKPOINT"])])
        self.assertEqual(argv[9:], ["--chat-recording", "false", "--model", "qwen3-coder"])
        self.assertNotIn("the brief", " ".join(argv))
        self.assertIn("the brief", seen["input"])
        self.assertIn("### AGENTS.md\nrepo rule", seen["input"])
        self.assertEqual((answer, session, cost), ({"finding": "the interface matrix"}, "s-1", None))
        self.assertEqual(usage["tool_calls"], [
            {"tool": "read", "path": "a.txt", "error": None},
            {"tool": "run", "path": None, "command": "touch x",
             "error": "Qwen Code requires permission to use \"run_shell_command\", but that permission was declined."}])
        self.assertEqual((usage["input_tokens"], usage["provider"], usage["model"], usage["hook"]), (66, "qwen", "qwen3-coder", HOOKED))

    def test_the_default_model_passes_no_model_flag(self):
        self.assertNotIn("--model", self.execute(EVENTS, model="default")[1]["argv"])

    def test_isolation_settings_and_environment(self):
        inherited = {"QWEN_CODE_TRUSTED_FOLDERS_PATH": "/trusts/everything.json", "QWEN_SYSTEM_MD": "/x.md",
                     "QWEN_RUNTIME_DIR": "/shared", "QWEN_SANDBOX": "false"}
        with patch.dict(os.environ, inherited):
            seen = self.execute(EVENTS)[1]
        settings, env, evidence = seen["settings"], seen["env"], seen["evidence"]
        self.assertEqual(qwen_isolation.ALLOWED, ("read_file", "glob", "grep_search", "structured_output"))
        (hook,) = settings["hooks"]["PreToolUse"]
        self.assertEqual(hook["matcher"], "*")
        self.assertEqual(hook["hooks"][0]["command"], shlex.join(
            [sys.executable, str(qwen_isolation.HOOK), "read_file,glob,grep_search,structured_output", str(evidence)]))
        self.assertEqual(settings["permissions"]["deny"], ["run_shell_command", "exec", "monitor", "write_file", "edit",
                                                           "notebook_edit", "web_fetch", "agent"])
        self.assertNotIn("tool_search", settings["permissions"]["deny"])  # denying the bridge registers every deferred tool
        self.assertEqual(settings["security"], {"folderTrust": {"enabled": True}})
        self.assertRegex(settings["context"]["fileName"], r"^ouro-no-context-[0-9a-f]{32}\.md$")
        self.assertNotEqual(settings["context"]["fileName"], self.execute(EVENTS)[1]["settings"]["context"]["fileName"])
        self.assertEqual(settings["memory"], {"enableManagedAutoMemory": False, "enableManagedAutoDream": False})
        scratch = evidence.parent
        home = scratch / "home"
        self.assertEqual({k: env[k] for k in ("HOME", "USERPROFILE", "QWEN_HOME", "QWEN_RUNTIME_DIR", "QWEN_CODE_TRUSTED_FOLDERS_PATH",
                                              "QWEN_CODE_MCP_APPROVALS_PATH", "QWEN_CODE_SYSTEM_DEFAULTS_PATH")},
                         {"HOME": str(home), "USERPROFILE": str(home), "QWEN_HOME": str(home / ".qwen"),
                          "QWEN_RUNTIME_DIR": str(scratch / "runtime"),
                          "QWEN_CODE_TRUSTED_FOLDERS_PATH": str(scratch / "trusted-folders.json"),
                          "QWEN_CODE_MCP_APPROVALS_PATH": str(scratch / "mcp-approvals.json"),
                          "QWEN_CODE_SYSTEM_DEFAULTS_PATH": str(scratch / "defaults.json")})
        self.assertEqual((env["QWEN_SYSTEM_MD"], env["QWEN_SANDBOX"]), (None, None))  # removed in the child
        self.assertEqual((env["QWEN_USAGE_STATISTICS_ENABLED"], env["QWEN_TELEMETRY_ENABLED"]), ("0", "0"))

    def test_a_contract_that_is_not_a_capability_set_is_refused_and_a_builder_preflight_proceeds(self):
        with patch.object(process, "run", side_effect=AssertionError("dispatched")), \
                self.assertRaisesRegex(sh.DriverError, "a capability contract is required"):
            qwen._execute("BUILD", "p", "m", ["read_file"], ".", 30, None, {}, developer.Settings("qwen"), {})
        builds = developer.Settings("claude", roles={"builder": {"backend": "qwen"}, "reviewer": {"backend": "qwen"}})
        for settings in (developer.Settings("qwen"), builds, dataclasses.replace(builds, serves=("builder",))):
            with self.subTest(settings=settings), patch.object(sh, "argv_for", side_effect=sh.DriverError("reached the CLI check")), \
                    self.assertRaisesRegex(sh.DriverError, "reached the CLI check"):
                qwen.preflight(settings, None)

    def test_a_build_contract_runs_under_yolo_with_writes_decided_by_the_hook_and_denials_as_manual_steps(self):
        from ouro.run import Run
        answer = dict(subject="feat(app): x", body="", claims=[], gates_run=[], validation_pending=[], residue=[], manual=[],
                      door="", blast_radius="", stop=None, replies=[])
        hooked = [*HOOKED, {"tool": "write_file", "path": "src/a.py", "granted": True},
                  {"tool": "write_file", "path": ".claude/rules/x.md", "granted": False,
                   "input": {"file_path": ".claude/rules/x.md", "content": "a rule"}},
                  {"tool": "edit", "path": ".qwen/settings.json", "granted": False,
                   "input": {"file_path": ".qwen/settings.json", "old_string": "a", "new_string": "b", "replace_all": False}},
                  {"tool": "notebook_edit", "path": ".codex/n.ipynb", "granted": False,
                   "input": {"notebook_path": ".codex/n.ipynb", "new_source": "x"}},
                  {"tool": "write_file", "path": "../outside.txt", "granted": False,
                   "input": {"file_path": "../outside.txt", "content": "far"}}]
        tools = builder(["python3 check.py"])
        (built, *_), seen = self.execute(result(structured_result=answer), tools=tools, hooked=hooked, name="BUILD")
        argv, settings, evidence = seen["argv"], seen["settings"], seen["evidence"]
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "yolo")
        self.assertEqual(settings["permissions"]["deny"], ["exec", "monitor", "web_fetch", "agent"])
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"], shlex.join(
            [sys.executable, str(qwen_isolation.HOOK),
             "read_file,glob,grep_search,structured_output,run_shell_command,write_file,edit,notebook_edit",
             str(evidence), str(evidence.with_name("policy.json"))]))
        self.assertEqual(seen["policy"]["edit"], True)
        self.assertEqual(seen["policy"]["commands"], list(tools.commands))
        prompt = seen["input"]
        self.assertTrue(prompt.startswith("Use only read_file, glob, grep_search, write_file, edit, notebook_edit and "
                                          "run_shell_command. Edits take paths inside the working directory; "
                                          ".git, .claude, .codex, .agents, .aws and .qwen are refused"))
        self.assertIn("Do not invoke the driver again", prompt)
        self.assertIn((sh.PLUGIN / "skills/run/SKILL.md").read_bytes().decode("utf-8-sig"), prompt)
        self.assertLess(prompt.index("outer caller"), prompt.index("### AGENTS.md"))
        denied = [{"tool_name": "write", "tool_input": {"file_path": ".claude/rules/x.md", "content": "a rule"}},
                  {"tool_name": "edit", "tool_input": {"file_path": ".qwen/settings.json", "old": "a", "new": "b"}},
                  {"tool_name": "notebook_edit", "tool_input": {"file_path": ".codex/n.ipynb"}},
                  {"tool_name": "write", "tool_input": {"file_path": "../outside.txt", "content": "far"}}]
        self.assertEqual(built["_denied"], denied)
        with tempfile.TemporaryDirectory() as d:
            runner = object.__new__(Run)
            runner.tree, runner.manual_paths = SimpleNamespace(path=pathlib.Path(d)), lambda: []
            runner.env, runner.labels = SimpleNamespace(runtime=developer.Settings("qwen")), []
            self.assertEqual(runner.manual_steps(built), [{"path": ".claude/rules/x.md", "edit": "a rule"},
                                                          {"path": ".qwen/settings.json", "edit": '{"old": "a", "new": "b"}'},
                                                          {"path": ".codex/n.ipynb", "edit": ""}])
        self.assertNotIn("Do not invoke the driver again", self.execute(EVENTS, tools=PROBE)[1]["input"])
        self.assertNotIn("_denied", self.execute(EVENTS, tools=tools)[0][0])  # only BUILD returns denials

    def test_an_invalid_run_skill_refuses_build_before_dispatch(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sh, "PLUGIN", pathlib.Path(d, "no-installed-skill")), \
                patch.object(process, "run", side_effect=AssertionError("dispatched")), \
                self.assertRaisesRegex(sh.DriverError, "qwen BUILD: trusted run skill is missing"):
            qwen._execute("BUILD", "p", "m", builder([]), d, 30, None, {}, developer.Settings("qwen"), {})

    def test_a_command_or_edit_node_refuses_a_working_directory_holding_qwen_configuration_and_a_read_node_does_not(self):
        for tools in (Capabilities(commands=("python3 check.py",)), PROBE, builder([])):
            for entry, directory in ((".qwen", True), (".QWEN", True), (".Qwen", False), (".env", False), (".ENV", False)):
                with self.subTest(tools=tools, entry=entry), tempfile.TemporaryDirectory() as d, \
                        patch.object(process, "run", side_effect=AssertionError("dispatched")):
                    path = pathlib.Path(d, entry)
                    if directory:
                        path.mkdir()
                    else:
                        path.write_text("OPENAI_BASE_URL=x", encoding="utf-8")
                    with self.assertRaisesRegex(sh.DriverError, f"qwen REVIEW: the working directory holds {re.escape(str(path))},"):
                        qwen._execute("REVIEW", "p", "m", tools, d, 30, None, {}, developer.Settings("qwen"), {})
        seen = {}

        def run(argv, cwd=None, prompt=None, timeout=None, env=None):
            seen["cwd"] = cwd
            raise sh.DriverError("reached the CLI")
        with tempfile.TemporaryDirectory() as d, patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(process, "run", side_effect=run):
            pathlib.Path(d, ".qwen").mkdir()
            pathlib.Path(d, ".env").write_text("X=1", encoding="utf-8")
            with self.assertRaisesRegex(sh.DriverError, "reached the CLI"):
                qwen._execute("CHECKPOINT", "p", "m", READ, d, 30, None, {}, developer.Settings("qwen"), {})
            self.assertEqual(seen["cwd"], d)

    def test_a_command_or_edit_node_trusts_its_working_directory_and_untrusts_each_ancestor_and_a_read_node_trusts_nothing(self):
        for tools in (Capabilities(commands=("python3 check.py",)), PROBE, builder([])):
            with self.subTest(tools=tools):
                seen = self.execute(EVENTS, tools=tools)[1]
                cwd = pathlib.Path(seen["cwd"]).resolve()
                self.assertEqual(seen["trust"], {str(cwd): "TRUST_FOLDER", **{str(p): "DO_NOT_TRUST" for p in cwd.parents}})
                self.assertEqual(list(seen["trust"].values()).count("TRUST_FOLDER"), 1)
                self.assertIn(str(pathlib.Path(cwd.anchor)), seen["trust"])
        self.assertEqual(self.execute(EVENTS, tools=READ)[1]["trust"], {})
        with tempfile.TemporaryDirectory() as d:  # the external reader's call: no trust named
            qwen_isolation.isolated_env(developer.Settings("qwen"), pathlib.Path(d))
            self.assertEqual(json.loads(pathlib.Path(d, "trusted-folders.json").read_text(encoding="utf-8")), {})

    def test_a_command_contract_runs_under_yolo_with_the_shell_decided_by_the_hook(self):
        for tools, scope in ((Capabilities(commands=("python3 check.py",)), "only these exact commands: `python3 check.py`"),
                             (PROBE, "any single command but git push, git commit and gh, with no `$`, glob, brace or git `-c`")):
            with self.subTest(tools=tools):
                (answer, *_), seen = self.execute(EVENTS, tools=tools)
                argv, settings, evidence = seen["argv"], seen["settings"], seen["evidence"]
                self.assertEqual(argv[argv.index("--approval-mode") + 1], "yolo")
                self.assertNotIn("run_shell_command", settings["permissions"]["deny"])
                self.assertEqual(settings["permissions"]["deny"], ["exec", "monitor", "write_file", "edit", "notebook_edit",
                                                                   "web_fetch", "agent"])
                policy = evidence.with_name("policy.json")
                self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"], shlex.join(
                    [sys.executable, str(qwen_isolation.HOOK), "read_file,glob,grep_search,structured_output,run_shell_command",
                     str(evidence), str(policy)]))
                self.assertTrue(seen["input"].startswith("Use only read_file, glob, grep_search and run_shell_command. "
                                                         "run_shell_command takes " + scope + ", one per call"))
                self.assertEqual(answer, {"finding": "the interface matrix"})
        self.assertEqual(qwen.INTERFACE.probe_confinement, "unconfined")

    def test_a_granted_command_on_a_recorded_envelope_reaches_the_shared_run_record(self):
        events = [*EVENTS[:3],
                  {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c1", "name": "run_shell_command",
                                                                 "input": {"command": "python3 check.py"}}]}},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c1", "is_error": False,
                                                            "content": "ok"}]}}, *EVENTS[5:]]
        tools = Capabilities(commands=("python3 check.py",))
        granted = [*HOOKED, {"tool": "run_shell_command", "command": "python3 check.py", "granted": True}]
        usage = self.execute(events, tools=tools, hooked=granted)[0][2]
        self.assertEqual(usage["tool_calls"][1], {"tool": "run", "path": None, "command": "python3 check.py", "error": None})
        refused = [*HOOKED, {"tool": "run_shell_command", "command": "python3 check.py", "granted": False}]
        with self.assertRaisesRegex(sh.DriverError, "run_shell_command ran without the allow-list hook"):
            self.execute(events, tools=tools, hooked=refused)

    def test_a_tool_that_ran_without_the_hooks_grant_discards_the_answer(self):
        for hooked in ([], [{"tool": "read_file", "granted": False}], [{"tool": "glob", "granted": True}]):
            with self.subTest(hooked=hooked), self.assertRaisesRegex(sh.DriverError, "read_file ran without the allow-list hook"):
                self.execute(EVENTS, hooked=hooked)
        self.assertEqual(self.execute(EVENTS, hooked=HOOKED[:1])[0][0], {"finding": "the interface matrix"})

    def test_a_failed_or_malformed_envelope_is_a_driver_error(self):
        cases = {"no result event": "not json", "error_max_turns": result(subtype="error_max_turns", is_error=True),
                 "plain text instead of calling": result(subtype="error_during_execution", is_error=True, error={
                     "message": "Model produced plain text instead of calling the structured_output tool"}),
                 "missing session identity": result(session_id=""),
                 "malformed structured result": result(structured_result={"finding": "x"}),
                 "exited 1": (EVENTS, 1)}
        for message, case in cases.items():
            events, code = case if isinstance(case, tuple) else (case, 0)
            with self.subTest(message=message), self.assertRaisesRegex(sh.DriverError, message):
                self.execute(events, code=code)
        with self.assertRaisesRegex(sh.DriverError, "qwen CHECKPOINT: runtime timed out after 30s"):
            self.execute(EVENTS, raises=sh.DriverError("runtime timed out after 30s"))
        self.assertEqual(self.execute(result(usage=["not", "a", "dict"]))[0][2]["provider"], "qwen")
        earlier = {**EVENTS[-1], "subtype": "error_max_turns", "is_error": True, "structured_result": None}
        self.assertEqual(self.execute([earlier, *EVENTS])[0][0], {"finding": "the interface matrix"})  # the last result decides

    def test_an_answer_written_as_text_passes_only_the_same_strict_check(self):
        self.assertEqual(self.execute(text_run('```json\n{"finding": "ouro-ready", "stop": null}\n```'), code=1)[0][0],
                         {"finding": "ouro-ready"})
        for text in ('{"finding": "ouro-ready"}', "ouro-ready", '{"finding": "x", "stop": null, "extra": 1}'):
            with self.subTest(text=text), self.assertRaisesRegex(sh.DriverError, "plain text instead|malformed structured result"):
                self.execute(text_run(text), code=1)
        valid = '{"finding": "ouro-ready", "stop": null}'
        mixed = text_run(valid)
        mixed[5] = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": valid}, {"type": "tool_use", "id": "c9", "name": "read_file", "input": {}}]}}
        for events, code in ((text_run(valid, message="provider connection reset"), 1), (mixed, 1),
                             ([*text_run(valid)[:-1], {**text_run(valid)[-1], "subtype": "error_max_turns"}], 53),
                             (text_run(valid), 53)):
            with self.subTest(code=code), self.assertRaises(sh.DriverError):
                self.execute(events, code=code)

    def test_the_file_proof_needs_the_content_the_read_and_the_hooks_grant(self):
        with tempfile.TemporaryDirectory() as root:
            read = {"tool": "read", "path": "readiness.txt", "error": None}
            good = {"model": "m", "tool_calls": [read], "hook": [{"tool": "read_file", "granted": True}]}
            qwen.file_proof({"finding": "ouro-ready"}, good, root)
            for answer, usage in (({"finding": "ouro-ready "}, good), ({"finding": "ouro-ready"}, {**good, "hook": []}),
                                  ({"finding": "ouro-ready"}, {**good, "hook": [{"tool": "read_file", "granted": False}]}),
                                  ({"finding": "ouro-ready"}, {**good, "tool_calls": [{**read, "error": "denied"}]}),
                                  ({"finding": "ouro-ready"}, {**good, "tool_calls": [{**read, "path": "other.txt"}]})):
                with self.subTest(answer=answer, usage=usage), self.assertRaisesRegex(sh.DriverError, "file read proof failed"):
                    qwen.file_proof(answer, usage, root)

    def test_preflight_names_the_unset_endpoint_variables(self):
        replies = {"--version": sh.R(0, "0.26.6\n", "", []), "--help": sh.R(0, " ".join(qwen.FLAGS), "", [])}
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=lambda argv, **k: replies[argv[-1]]), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_BASE_URL": "", "OPENAI_MODEL": ""}), \
                self.assertRaisesRegex(sh.DriverError, "set OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL in the launch environment"):
            qwen.preflight(developer.Settings("qwen"), READ)
        replies["--version"] = sh.R(0, "0.26.5\n", "", [])
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=lambda argv, **k: replies[argv[-1]]), \
                self.assertRaisesRegex(sh.DriverError, r"0\.26\.6\+ with .* required \(version 0\.26\.5, missing none\)"):
            qwen.preflight(developer.Settings("qwen"), READ)

    def test_preflight_runs_the_hook_proof_then_the_file_proof(self):
        replies = {"--version": sh.R(0, "0.26.6\n", "", []), "--help": sh.R(0, " ".join(qwen.FLAGS), "", [])}
        calls = []

        def execute(name, prompt, model, tools, root, *rest):
            calls.append((name, model, tools))
            return {"finding": "ouro-ready"}, "s", {"model": model, "tool_calls": [
                {"tool": "read", "path": "readiness.txt", "error": None}], "hook": []}, None
        common = (patch.object(sh, "argv_for", side_effect=lambda *a: list(a)),
                  patch.object(sh, "run", side_effect=lambda argv, **k: replies[argv[-1]]),
                  patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://x/v1", "OPENAI_MODEL": "m"}))
        with common[0], common[1], common[2], patch.object(qwen, "hook_proof", side_effect=sh.DriverError("hook proof ran")), \
                patch.object(qwen, "_execute", side_effect=AssertionError("model called before the hook proof")), \
                self.assertRaisesRegex(sh.DriverError, "hook proof ran"):
            qwen.preflight(developer.Settings("qwen"), READ)
        with common[0], common[1], common[2], patch.object(qwen, "hook_proof"), patch.object(qwen, "_execute", side_effect=execute), \
                self.assertRaisesRegex(sh.DriverError, "file read proof failed .* hook observed False"):
            qwen.preflight(developer.Settings("qwen"), READ)
        self.assertEqual(calls, [("CHECKPOINT", "default", READ)])

    def test_preflight_proves_the_model_triage_dispatches(self):
        replies = {"--version": sh.R(0, "0.26.6\n", "", []), "--help": sh.R(0, " ".join(qwen.FLAGS), "", [])}
        calls = []

        def execute(name, prompt, model, tools, root, *rest):
            calls.append(model)
            return {"finding": "ouro-ready"}, "s", {"model": model, "tool_calls": [
                {"tool": "read", "path": "readiness.txt", "error": None}], "hook": [{"tool": "read_file", "granted": True}]}, None
        builder_only = developer.Settings("qwen", {"builder": "deepseek-v4-pro"})
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=lambda argv, **k: replies[argv[-1]]), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://x/v1", "OPENAI_MODEL": ""}), \
                patch.object(qwen, "hook_proof"), patch.object(qwen, "_execute", side_effect=execute):
            qwen.preflight(builder_only, READ)  # the triager inherits the builder's model: no OPENAI_MODEL needed
            qwen.preflight(developer.Settings("qwen", {"builder": "deepseek-v4-pro"}, roles={"triager": {"model": "qwen3-coder-small"}}), READ)
            with self.assertRaisesRegex(sh.DriverError, "set OPENAI_MODEL in the launch environment"):
                qwen.preflight(developer.Settings("qwen", {"builder": "deepseek-v4-pro"}, roles={"triager": {"model": "default"}}), READ)
        self.assertEqual(calls, ["deepseek-v4-pro", "qwen3-coder-small"])

    def test_a_run_on_qwen_alone_proves_every_role_model_and_triage_the_triagers(self):
        from ouro import node, run, triage
        replies = {"--version": sh.R(0, "0.26.6\n", "", []), "--help": sh.R(0, " ".join(qwen.FLAGS), "", [])}
        calls = []

        def execute(name, prompt, model, tools, root, *rest):
            calls.append(model)
            return {"finding": "ouro-ready"}, "s", {"model": model, "tool_calls": [
                {"tool": "read", "path": "readiness.txt", "error": None},
                {"tool": "run", "path": None, "command": qwen.LISTED, "error": None},
                {"tool": "write", "path": qwen.WRITTEN, "error": None}],
                "hook": [{"tool": t, "granted": True} for t in ("read_file", "run_shell_command", "write_file")]}, None
        cfg = developer.Settings("qwen", {role: "model-b" for role in ("builder", "builder_trivial", "reviewer")},
                                 roles={"triager": {"model": "model-a"}})
        usage = len(node.USAGE)
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=lambda argv, **k: replies[argv[-1]]), \
                patch.dict(os.environ, {"OURO_NODE_STUB": "", "OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://x/v1", "OPENAI_MODEL": ""}), \
                patch.object(qwen, "hook_proof"), patch.object(qwen, "_execute", side_effect=execute):
            runner = object.__new__(run.Run)
            runner.env = SimpleNamespace(runtime=cfg, dry=False)
            runner.preflight()
            self.assertEqual(calls, ["model-a", "model-b", "model-b", "model-b", "model-b"])  # then commands, PROBE, BUILD
            del calls[:]
            env = SimpleNamespace(runtime=cfg, gh_json=Mock(side_effect=sh.DriverError("past the preflight")))
            with self.assertRaisesRegex(sh.DriverError, "past the preflight"):
                triage.triage(env, 1, False)
            self.assertEqual(calls, ["model-a"])
        del node.USAGE[usage:]

    def test_preflight_proves_each_served_contract_live_and_refuses_a_missing_or_ungranted_tool(self):
        replies = {"--version": sh.R(0, "0.26.6\n", "", []), "--help": sh.R(0, " ".join(qwen.FLAGS), "", [])}
        seen = []

        def turn(i, name, args, error=None):
            return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"p{i}", "name": name, "input": args}]}},
                    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"p{i}",
                                                              "is_error": bool(error), "content": error or "ok"}]}}]

        def recorded(mode):
            """A transcript per call: the read proof always passes; a live proof's shell call is granted, declined by
            Qwen Code (the untrusted folder's headless default: no hook record) or refused by the hook."""
            def run(argv, cwd=None, prompt=None, timeout=None, env=None):
                system = pathlib.Path(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"])
                trust = json.loads(pathlib.Path(env["QWEN_CODE_TRUSTED_FOLDERS_PATH"]).read_text(encoding="utf-8"))
                seen.append((argv[argv.index("--approval-mode") + 1], trust.get(str(pathlib.Path(cwd).resolve()))))
                if "readiness.txt" in prompt:
                    events, hooked, finding = turn(0, "read_file", {"file_path": "readiness.txt"}), [{"tool": "read_file", "granted": True}], "ouro-ready"
                else:
                    shell = {"granted": (None, True), "missing": ("permission was declined.", None),
                             "ungranted": ("ouro: command is not on the node's approved list", False)}[mode]
                    events = turn(1, "run_shell_command", {"command": qwen.LISTED}, shell[0])
                    hooked = [] if shell[1] is None else [{"tool": "run_shell_command", "command": qwen.LISTED, "granted": shell[1]}]
                    if qwen.ASKS["write"] in prompt:
                        events += turn(2, "write_file", {"file_path": str(pathlib.Path(cwd, qwen.WRITTEN)), "content": "ouro-ready"})
                        hooked.append({"tool": "write_file", "path": str(pathlib.Path(cwd, qwen.WRITTEN)), "granted": True})
                    finding = "done"
                answer = {"finding": finding, "stop": None}
                system.with_name("hook.jsonl").write_text("".join(json.dumps(h) + "\n" for h in hooked), encoding="utf-8")
                return SimpleNamespace(code=0, err="", out=json.dumps([EVENTS[0], *events, {**EVENTS[-1], "structured_result": answer}]))
            return run

        def preflight(mode, settings, capabilities):
            with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                    patch.object(sh, "run", side_effect=lambda argv, **k: replies[argv[-1]]), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://x/v1", "OPENAI_MODEL": "m"}), \
                    patch.object(qwen, "hook_proof"), patch.object(process, "run", side_effect=recorded(mode)):
                return qwen.preflight(settings, capabilities)
        self.assertEqual(len(preflight("granted", developer.Settings("qwen"), None)), 4)  # the read, then commands, PROBE and BUILD
        self.assertEqual(seen, [("default", None), *[("yolo", "TRUST_FOLDER")] * 3])
        self.assertEqual(len(preflight("granted", dataclasses.replace(developer.Settings("qwen"), serves=("reviewer",)), None)), 2)
        self.assertEqual(len(preflight("granted", developer.Settings("qwen"), READ)), 1)  # a READ contract makes no live call
        for mode in ("missing", "ungranted"):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    sh.DriverError, r"the commands contract's run proof failed \(call observed False, hook observed False\)"):
                preflight(mode, developer.Settings("qwen"), None)
        with tempfile.TemporaryDirectory() as root:
            run = {"tool": "run", "path": None, "command": qwen.LISTED, "error": None}
            write = {"tool": "write", "path": qwen.WRITTEN, "error": None}
            hooked = [{"tool": "run_shell_command", "granted": True}, {"tool": "write_file", "granted": True}]
            qwen.contract_proof("BUILD", ("run", "write"), {"tool_calls": [run, write], "hook": hooked}, root)
            for usage, message in (({"tool_calls": [write], "hook": hooked}, "run proof failed .call observed False, hook observed True"),
                                   ({"tool_calls": [run, write], "hook": hooked[1:]}, "run proof failed .call observed True, hook observed False"),
                                   ({"tool_calls": [run, {**write, "path": "../x.txt"}], "hook": hooked}, "write proof failed .call observed False"),
                                   ({"tool_calls": [run, write], "hook": [hooked[0], {"tool": "write_file", "granted": False}]},
                                    "write proof failed .call observed True, hook observed False")):
                with self.subTest(message=message), self.assertRaisesRegex(sh.DriverError, message):
                    qwen.contract_proof("BUILD", ("run", "write"), usage, root)

    def test_the_hook_blocks_ungranted_tools_admits_granted_ones_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            evidence = pathlib.Path(d, "hook.jsonl")

            def hook(stdin, log=evidence):
                return subprocess.run([sys.executable, str(qwen_isolation.HOOK), ",".join(qwen_isolation.ALLOWED), str(log)],
                                      input=stdin, capture_output=True, text=True, timeout=60)
            for tool in ("run_shell_command", "exec", "write_file", "tool_call", "monitor"):
                with self.subTest(tool=tool):
                    self.assertEqual(hook(json.dumps({"tool_name": tool, "tool_input": {}})).returncode, 2)
            admitted = hook(json.dumps({"tool_name": "read_file", "tool_input": {"file_path": "a"}}))
            self.assertEqual((admitted.returncode, admitted.stdout), (0, ""))
            self.assertEqual([json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()][-2:],
                             [{"tool": "monitor", "granted": False}, {"tool": "read_file", "granted": True}])
            self.assertEqual(hook("not json").returncode, 2)
            for event in ({"tool_name": 5}, {"tool_input": {}}):
                with self.subTest(event=event):
                    self.assertEqual(hook(json.dumps(event)).returncode, 2)
            self.assertEqual(hook(json.dumps({"tool_name": "read_file"}), log=pathlib.Path(d, "missing", "x")).returncode, 2)
        good = [code for *_, code in qwen.HOOK_CASES]
        for codes, message in (((0, 0), "did not block run_shell_command"), ((2, 2), "did not admit read_file"),
                               ((2, 0, 2), "did not admit the listed command `python3 --version`"),
                               ((*good[:3], 0), "did not block the listed command `python3 -V`"),
                               ((*good[:4], 0), "did not block the listed command `python3 --version; python3 --version`"),
                               ((*good[:6], 0), "did not block the probe command `git push origin HEAD`"),
                               ((*good[:7], 2), "did not admit the build write_file of `notes.txt`"),
                               ((*good[:8], 0), "did not block the build write_file of `.claude/settings.json`"),
                               ((*good[:9], 0), "did not block the build edit of `../outside.txt`")):
            replies = iter(sh.R(c, "", "", []) for c in codes)
            with self.subTest(codes=codes), patch.object(sh, "run", side_effect=lambda *a, **k: next(replies)), \
                    self.assertRaisesRegex(sh.DriverError, message):
                qwen.hook_proof(developer.Settings("qwen"))
        qwen.hook_proof(developer.Settings("qwen"))  # the real hook passes every proof

    def test_the_hook_decides_each_shell_command_from_the_policy(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d, "tree")
            root.mkdir()
            evidence = pathlib.Path(d, "hook.jsonl")
            policies = {}
            for name, commands, probe in (("listed", ["python3 check.py", "git diff *"], False), ("probe", [], True)):
                policies[name] = pathlib.Path(d, f"{name}.json")
                policies[name].write_text(json.dumps({"root": str(root), "commands": commands, "probe": probe}), encoding="utf-8")

            def hook(policy, command, **extra):
                argv = shlex.split(qwen_isolation.hook_command(evidence, policies[policy]))
                event = {"tool_name": "run_shell_command", "tool_input": {"command": command, **extra}}
                return subprocess.run(argv, input=json.dumps(event), capture_output=True, text=True, timeout=60).returncode
            admitted = [("listed", "python3 check.py"), ("listed", " python3 check.py "), ("listed", "git diff --stat HEAD"),
                        ("probe", "git status"), ("probe", "python3 -c 'print(1)'"), ("probe", 'grep -rn "a|b" src'),
                        ("probe", "ls"), ("probe", "git -C . status"), ("probe", "grep -n a# src"), ("probe", "/usr/lib/git-core/git-log --oneline"),
                        ("listed","python3 check.py", {"directory": str(root)}),
                        ("listed", "python3 check.py", {"is_background": False, "description": "the check"})]
            refused = [("listed", "python3 other.py"), ("listed", "python3 check.py --flag"), ("listed", "git log"),
                       ("listed", "python3 check.py; python3 check.py"), ("listed", "python3 check.py && git push"),
                       ("listed", "python3 check.py | tee x"), ("listed", "python3 check.py > out.txt"),
                       ("listed", "python3 check.py\ngit push"), ("listed", "python3 check.py $(git push)"),
                       ("listed", "python3 check.py `git push`"), ("listed", "X=1 python3 check.py"),
                       ("listed", "python3 check.py", {"directory": d}), ("listed", "python3 check.py", {"is_background": True}),
                       ("probe", "git push origin HEAD"), ("probe", "git -C . commit -m x"), ("probe", "gh pr merge 1"),
                       ("probe", "/usr/bin/gh issue list"), ("probe", "env git push"), ("probe", "bash -c 'git push'"),
                       ("probe", "(git push)"), ("probe", "git status & git push"), ("probe", "echo 'unbalanced"),
                       ("probe", ""), ("probe", "ls 2>&1"), ("probe", "ls a#; git push"),
                       ("probe", "ls a#&& gh pr merge 1"), ("listed", "git diff a#; git push origin HEAD"),
                       ("probe", "git${IFS}push origin HEAD"), ("probe", "$'\\x67'it push"), ("probe", "/usr/bin/g[i]t push"),
                       ("probe", "/usr/bin/g[h] pr merge 1"), ("probe", "/usr/bin/g?t push"), ("probe", "git {push,}"),
                       ("probe", "git -c alias.p=push p origin HEAD"), ("probe", "git --config-env=alias.p=X p"),
                       ("probe", "git config ALIAS.p '!true'"), ("probe", "/usr/lib/git-core/git-push origin HEAD"),
                       ("probe", "/usr/lib/git-core/git-commit --allow-empty -m x"),
                       ("probe", "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p origin HEAD"),
                       ("probe", "env git_config_parameters=x git p")]
            for policy, command, *extra in admitted:
                with self.subTest(policy=policy, command=command, extra=extra):
                    self.assertEqual(hook(policy, command, **(extra[0] if extra else {})), 0)
            for policy, command, *extra in refused:
                with self.subTest(policy=policy, command=command, extra=extra):
                    self.assertEqual(hook(policy, command, **(extra[0] if extra else {})), 2)
            logged = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(logged[0], {"tool": "run_shell_command", "command": "python3 check.py", "granted": True})
            self.assertEqual(logged[len(admitted)], {"tool": "run_shell_command", "command": "python3 other.py", "granted": False})
            no_policy = shlex.split(qwen_isolation.hook_command(evidence))
            event = json.dumps({"tool_name": "run_shell_command", "tool_input": {"command": "ls"}})
            self.assertEqual(subprocess.run(no_policy, input=event, capture_output=True, text=True, timeout=60).returncode, 2)
            missing = [*no_policy[:2], f"{no_policy[2]},run_shell_command", no_policy[3]]  # the shell granted with no policy file
            self.assertEqual(subprocess.run(missing, input=event, capture_output=True, text=True, timeout=60).returncode, 2)

    def test_the_hook_decides_each_write_against_the_worktree_and_its_protected_paths(self):
        with tempfile.TemporaryDirectory() as d:
            d = str(pathlib.Path(d).resolve())  # the Windows leg's TEMP is an 8.3 path; the hook compares resolved roots
            root = pathlib.Path(d, "tree")
            (root / ".claude").mkdir(parents=True)
            (root / "docs").mkdir()
            try:
                (root / "docs" / "link").symlink_to(root / ".claude")
                (root / "out").symlink_to(d)
                links = ["docs/link/x", "out/x"]
            except OSError as ex:
                if os.name != "nt":
                    raise
                print(f"  SKIP: symlink rows of the write hook: {ex}")
                links = []
            evidence = pathlib.Path(d, "hook.jsonl")
            policies = {}
            for name, edit in (("build", True), ("listed", False)):
                policies[name] = pathlib.Path(d, f"{name}.json")
                policies[name].write_text(json.dumps({"root": str(root), "commands": ["python3 check.py"], "probe": False,
                                                      "edit": edit}), encoding="utf-8")

            def hook(tool, args, policy="build", edit=True):
                argv = shlex.split(qwen_isolation.hook_command(evidence, policies[policy], edit))
                event = {"tool_name": tool, "tool_input": args}
                return subprocess.run(argv, input=json.dumps(event), capture_output=True, text=True, timeout=60).returncode
            admitted = [("write_file", {"file_path": "src/a.py", "content": "x"}),
                        ("write_file", {"file_path": str(root / "a.txt"), "content": "x"}),
                        ("edit", {"file_path": "a.txt", "old_string": "a", "new_string": "b"}),
                        ("notebook_edit", {"notebook_path": "n.ipynb", "new_source": "x"}),
                        ("write_file", {"file_path": "src/.claude/x.md", "content": "x"}),
                        ("write_file", {"file_path": ".claudex/x.md", "content": "x"})]
            refused = [(tool, {key: path, "content": "x"}) for tool, key in (("write_file", "file_path"), ("edit", "file_path"),
                                                                            ("notebook_edit", "notebook_path"))
                       for path in (".claude/rules/x.md", ".CLAUDE/x", ".git/config", ".qwen/settings.json", ".aws/config",
                                    ".agents/skills/x", ".codex/config.toml", "./.claude/x", "src/../.claude/x",
                                    str(root / ".claude" / "x"), "../outside.txt", str(pathlib.Path(d, "x")),
                                    str(root), "", " ", *links)]
            refused += [("write_file", {"content": "x"}), ("write_file", {"file_path": 5})]
            for tool, args in admitted:
                with self.subTest(tool=tool, args=args):
                    self.assertEqual(hook(tool, args), 0)
            for tool, args in refused:
                with self.subTest(tool=tool, args=args):
                    self.assertEqual(hook(tool, args), 2)
            self.assertEqual(hook("write_file", {"file_path": "a.txt", "content": "x"}, policy="listed"), 2)  # no edit grant
            self.assertEqual(hook("write_file", {"file_path": "a.txt", "content": "x"}, edit=False), 2)  # not on the allow list
            logged = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(logged[0], {"tool": "write_file", "path": "src/a.py", "granted": True})
            self.assertEqual(logged[len(admitted)], {"tool": "write_file", "path": ".claude/rules/x.md", "granted": False,
                                                     "input": {"file_path": ".claude/rules/x.md", "content": "x"}})
            self.assertEqual(logged[-1], {"tool": "write_file", "granted": False})
