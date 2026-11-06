"""Backend interface conformance: every registered backend implements each member or declares a marker."""
import dataclasses
import json
import pathlib
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from ouro import developer, node, sh
from ouro.runtime import PROBE, READ, Capabilities, builder, codex, diagnostics, process
from ouro.runtime.backends import BACKENDS, CONFINED, NOT_REQUIRED, UNCONFINED, UNSUPPORTED, Backend, Interface

MARKERS = {"worktree_readiness": {NOT_REQUIRED}, "validate_answer": {UNSUPPORTED},
           "probe_confinement": {CONFINED, UNCONFINED}, "sandbox_route": {NOT_REQUIRED, UNSUPPORTED}}
# Today's matrix; "implemented" means a callable. A new backend or member must be added here.
MATRIX = {
    "claude": {"preflight": "implemented", "worktree_readiness": NOT_REQUIRED, "execute": "implemented",
               "probe_confinement": UNCONFINED, "sandbox_route": "implemented", "validate_answer": UNSUPPORTED,
               "admits": "implemented"},
    "codex": {"preflight": "implemented", "worktree_readiness": "implemented", "execute": "implemented",
              "probe_confinement": CONFINED, "sandbox_route": NOT_REQUIRED, "validate_answer": "implemented",
              "admits": "implemented"},
    "qwen": {"preflight": "implemented", "worktree_readiness": NOT_REQUIRED, "execute": "implemented",
             "probe_confinement": UNCONFINED, "sandbox_route": "implemented", "validate_answer": "implemented",
             "admits": "implemented"},
}
# The contracts each backend admits on this host; None is full readiness.
CONTRACTS = {"READ": READ, "PROBE": PROBE, "commands": Capabilities(commands=("python3 check.py",)), "edit": builder([]),
             "full": None}
ADMITS = {"claude": set(CONTRACTS), "codex": set(CONTRACTS), "codex on native Windows": {"READ"}, "qwen": set(CONTRACTS)}
# A recorded Qwen Code FOLLOWUP run: one granted read, then the structured result.
QWEN_FOLLOWUP = [
    {"type": "system", "subtype": "init", "session_id": "q-1", "qwen_code_version": "0.26.6"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c0", "name": "read_file", "input": {"file_path": "a.txt"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c0", "is_error": False, "content": "hello"}]}},
    {"type": "result", "subtype": "success", "session_id": "q-1", "is_error": False,
     "usage": {"input_tokens": 3, "output_tokens": 4}, "structured_result": {"issues": []}},
]
CLAUDE_REVIEW = {"session_id": "c-1", "structured_output": {"verdict": "CLEAN", "findings": []},
                 "usage": {"input_tokens": 5, "output_tokens": 6}, "total_cost_usd": 0.01}


class BackendInterfaceTests(unittest.TestCase):
    def test_every_backend_implements_or_declares_every_member(self):
        seen = {}
        for name, backend in BACKENDS.items():
            interface = backend.interface
            self.assertIsInstance(interface, Interface)
            row = seen[name] = {}
            for member in (f.name for f in dataclasses.fields(Interface)):
                value = getattr(interface, member)
                if member == "probe_confinement":
                    self.assertIn(value, MARKERS[member], (name, member))
                    row[member] = value
                elif callable(value):
                    row[member] = "implemented"
                else:
                    self.assertIn(value, MARKERS.get(member, set()), (name, member))
                    row[member] = value
        self.assertEqual(seen, MATRIX)

    def test_binding_errors_keep_their_text(self):
        cases = {'[runtime]\nbackend="unknown"': "runtime.backend must be claude, codex or qwen",
                 '[runtime]\nbackend="claude"\n[runtime.models]\nbuilder="gpt-example"':
                     "runtime.models.builder: Claude requires a supported alias",
                 '[runtime]\nbackend="codex"\n[runtime.models]\nbuilder="opus"':
                     "runtime.models.builder: Claude aliases cannot select Codex models",
                 '[runtime]\nbackend="claude"\neffort="low"':
                     "runtime.effort requires Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="codex"\neffort="none"':
                     "runtime.effort requires Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="codex"\n[runtime.models]\nbuilder=" x"':
                     "runtime.models.builder must be a non-empty model",
                 '[runtime]\nbackend="claude"\n[runtime.models]\ntriager="gpt-example"':
                     "runtime.models.triager: Claude requires a supported alias",
                 '[runtime]\nroles=[]': "unknown runtime.roles role",
                 '[runtime]\nbackend="claude"\n[runtime.roles.verifier]\nmodel="opus"': "unknown runtime.roles role",
                 '[runtime]\nbackend="claude"\n[runtime.roles.triager]\nmodels="opus"': "unknown runtime.roles.triager key",
                 '[runtime]\nbackend="claude"\n[runtime.roles]\ntriager="opus"': "unknown runtime.roles.triager key",
                 '[runtime]\nbackend="claude"\n[runtime.roles.triager]\nbackend="other"':
                     "runtime.roles.triager.backend must be claude, codex or qwen",
                 '[runtime]\nbackend="claude"\n[runtime.roles.reviewer]\nmodel="gpt-example"':
                     "runtime.roles.reviewer.model: Claude requires a supported alias",
                 '[runtime]\nbackend="codex"\n[runtime.roles.reviewer]\nmodel="opus"':
                     "runtime.roles.reviewer.model: Claude aliases cannot select Codex models",
                 '[runtime]\nbackend="claude"\n[runtime.roles.triager]\nbackend="qwen"\nmodel="opus"':
                     "runtime.roles.triager.model: Claude aliases cannot select Qwen Code models",
                 '[runtime]\nbackend="claude"\n[runtime.roles.builder]\neffort="high"':
                     "runtime.roles.builder.effort requires Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="claude"\n[runtime.models]\ntriager="sonnet"\n[runtime.roles.triager]\nbackend="qwen"':
                     "runtime.models.triager: runtime.roles.triager.backend selects Qwen Code; "
                     "name its model as runtime.roles.triager.model"}
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "developer.toml"
            for text, message in cases.items():
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(sh.DriverError) as caught:
                    developer.load(path)
                self.assertEqual(str(caught.exception), f"developer binding {path}: {message}")

    def test_a_claude_denial_reaches_the_shared_shape_and_the_codex_manual_step(self):
        from types import SimpleNamespace
        from ouro.run import Run
        from ouro.runtime import claude
        envelope = {"session_id": "s", "structured_output": {"subject": "x"}, "permission_denials": [
            {"tool_name": "Write", "tool_use_id": "t1", "tool_input": {"file_path": ".claude/rules/x.md", "content": "a rule"}},
            {"tool_name": "Edit", "tool_use_id": "t2", "tool_input": {"file_path": ".codex/config.toml", "old_string": "a",
                                                                      "new_string": "b", "replace_all": False}},
            {"tool_name": "Bash", "tool_use_id": "t3", "tool_input": {"command": "gh pr merge 1", "description": "merge"}}]}
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", return_value=sh.R(0, json.dumps(envelope), "", [])):
            answer = claude._execute("BUILD", "p", "opus", ["Read"], ".", 5, None, {}, developer.Settings())[0]
        codex_shape = [{"tool_name": "write", "tool_input": {"file_path": ".claude/rules/x.md", "content": "a rule"}},
                       {"tool_name": "edit", "tool_input": {"file_path": ".codex/config.toml", "old": "a", "new": "b"}},
                       {"tool_name": "run", "tool_input": {"command": "gh pr merge 1"}}]
        self.assertEqual(answer["_denied"], codex_shape)
        with tempfile.TemporaryDirectory() as d:
            runner = object.__new__(Run)
            runner.tree, runner.manual_paths = SimpleNamespace(path=pathlib.Path(d)), lambda: []
            steps = runner.manual_steps(answer)
            self.assertEqual(steps, runner.manual_steps({"_denied": codex_shape}))
        self.assertEqual(steps, [{"path": ".claude/rules/x.md", "edit": "a rule"},
                                 {"path": ".codex/config.toml", "edit": '{"old": "a", "new": "b"}'}])

    def test_node_opens_one_attempt_row_per_execution_for_every_backend(self):
        for name, backend in BACKENDS.items():
            calls = []

            def execute(*args):
                calls.append(args)
                args[-1]["session"] = "filled-by-backend"
                return {"finding": "x"}, "s", {}, None
            fake = dataclasses.replace(backend.interface, execute=execute)
            before, usage = len(diagnostics.ATTEMPTS), len(node.USAGE)
            with self.subTest(backend=name), patch.object(type(backend), "interface", new_callable=PropertyMock, return_value=fake):
                node._real("CHECKPOINT", "p", "m", [], ".", 1, None, developer.Settings(name))
            del node.USAGE[usage:]
            self.assertEqual(len(diagnostics.ATTEMPTS), before + 1)
            row = diagnostics.ATTEMPTS[-1]
            self.assertEqual((row["backend"], row["outcome"], row["session"]), (name, "adapter_returned", "filled-by-backend"))
            self.assertEqual(calls[0][7], node.SCHEMAS["CHECKPOINT"])

    def test_the_admits_matrix(self):
        def admitted(name):
            return {key for key, contract in CONTRACTS.items() if BACKENDS[name].interface.admits(contract)}
        seen = {"claude": admitted("claude"), "qwen": admitted("qwen")}
        with patch.object(codex, "WINDOWS", False):
            seen["codex"] = admitted("codex")
        with patch.object(codex, "WINDOWS", True):
            seen["codex on native Windows"] = admitted("codex")
        self.assertEqual(seen, ADMITS)

    def test_a_role_table_selects_backend_model_and_effort_per_role(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "developer.toml"
            path.write_text('[runtime]\nbackend="claude"\n[runtime.models]\nreviewer="sonnet"\n'
                            '[runtime.roles.triager]\nbackend="qwen"\nmodel="deepseek-v4-pro"\n'
                            '[runtime.roles.builder]\nbackend="codex"\neffort="high"\n', encoding="utf-8")
            cfg = developer.load(path)
            path.write_text('[runtime]\nbackend="codex"\neffort="low"\n[runtime.roles.builder]\neffort="high"\n'
                            '[runtime.roles.triager]\nbackend="claude"\n', encoding="utf-8")
            efforts = developer.load(path)
        self.assertEqual({role: cfg.backend_of(role) for role in developer.ROLES},
                         {"triager": "qwen", "checkpointer": "claude", "builder": "codex", "builder_trivial": "claude", "reviewer": "claude"})
        # The checkpointer inherits no model from a builder on another backend: it keeps the repository's alias.
        self.assertEqual({role: cfg.model(role, "opus") for role in developer.ROLES},
                         {"triager": "deepseek-v4-pro", "checkpointer": "opus", "builder": "default", "builder_trivial": "opus",
                          "reviewer": "sonnet"})
        self.assertEqual((cfg.of("builder").backend, cfg.of("builder").effort, cfg.of("triager").backend), ("codex", "high", "qwen"))
        self.assertIs(cfg.of("reviewer"), cfg)
        self.assertTrue(cfg.mixed())
        self.assertFalse(cfg.mixed(("reviewer", "checkpointer")))
        self.assertEqual({role: efforts.effort_of(role) for role in developer.ROLES},
                         {"triager": None, "checkpointer": "low", "builder": "high", "builder_trivial": "low", "reviewer": "low"})
        self.assertFalse(developer.Settings("codex").mixed())

    def test_the_planner_contract_per_command_and_its_refusal(self):
        from ouro.run import Run
        runner = object.__new__(Run)
        runner.labels, runner.flow = [], "express"
        self.assertEqual(runner.contracts(), {"triager": READ, "builder": None, "reviewer": READ})
        runner.labels, runner.flow = ["trivial"], "full"
        self.assertEqual(runner.contracts(), {"triager": READ, "builder_trivial": None, "reviewer": PROBE})
        runner.labels, runner.build_commands = ["checkpoint"], lambda: ("(none)", [])
        self.assertEqual(runner.contracts(), {"checkpointer": READ})
        runner.build_commands = lambda: ("- `python3 check.py`", ["python3 check.py"])
        self.assertEqual(runner.contracts(), {"checkpointer": Capabilities(commands=("python3 check.py",))})

        cfg = developer.Settings("claude", roles={"triager": {"backend": "qwen"}, "reviewer": {"backend": "qwen"}})
        groups = node.plan(cfg, {"triager": READ, "builder": None, "reviewer": READ})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in groups.items()},
                         {"qwen": (("triager", "reviewer"), READ), "claude": (("builder",), None)})
        self.assertEqual(groups["qwen"][0].serving("qwen", ("triager",)), ["triager", "reviewer"])
        self.assertEqual(groups["claude"][0].serving("claude"), ["builder"])
        groups = node.plan(cfg, {"triager": READ, "builder": None, "reviewer": PROBE})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in groups.items()},
                         {"qwen": (("triager", "reviewer"), None), "claude": (("builder",), None)})
        self.assertEqual(set(node.plan(cfg, {"checkpointer": Capabilities(commands=("python3 check.py",))})), {"claude"})
        moved = developer.Settings("claude", roles={"checkpointer": {"backend": "qwen"}})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in
                          node.plan(moved, {"checkpointer": Capabilities(commands=("python3 check.py",))}).items()},
                         {"qwen": (("checkpointer",), None)})
        building = developer.Settings("claude", roles={"builder": {"backend": "qwen"}})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in
                          node.plan(building, {"triager": READ, "builder": None, "reviewer": PROBE}).items()},
                         {"claude": (("triager", "reviewer"), None), "qwen": (("builder",), None)})
        windows = developer.Settings("claude", roles={"builder": {"backend": "codex"}})
        with patch.object(codex, "WINDOWS", True), self.assertRaises(sh.DriverError) as caught:
            node.plan(windows, {"triager": READ, "builder": None, "reviewer": PROBE})
        self.assertEqual(str(caught.exception), "preflight: role builder selects Codex, which does not admit its full "
                                                "contract; select another backend in [runtime.roles.builder]")
        self.assertEqual(node.widest(cfg), {"triager": READ, "checkpointer": None, "builder": None, "builder_trivial": None,
                                            "reviewer": PROBE})

    def test_each_backend_a_role_selects_preflights_once(self):
        calls = []

        def fake(name):
            def preflight(settings, capabilities):
                calls.append((name, settings.serves, capabilities))
                return [(f"{name}-proof", {}, None)]
            return dataclasses.replace(BACKENDS[name].interface, preflight=preflight)
        fakes = {name: fake(name) for name in BACKENDS}
        cfg = developer.Settings("claude", roles={"triager": {"backend": "qwen"}})
        usage = len(node.USAGE)
        with patch.dict("os.environ", {"OURO_NODE_STUB": ""}), patch.object(Backend, "interface", new=property(lambda b: fakes[b.name])):
            node.preflight(cfg, needs={"triager": READ, "builder": None, "reviewer": READ})
            self.assertEqual(calls, [("qwen", ("triager",), READ), ("claude", ("builder", "reviewer"), None)])
            self.assertEqual([(u["node"], u["backend"]) for u in node.USAGE[usage:]], [("PREFLIGHT", "qwen"), ("PREFLIGHT", "claude")])
            del calls[:], node.USAGE[usage:]
            same = developer.Settings("claude", roles={"reviewer": {"model": "sonnet"}})
            node.preflight(same, capabilities=READ, needs={"triager": READ})
            self.assertEqual(calls, [("claude", (), READ)])  # one backend: the call a binding without a role table makes
            del node.USAGE[usage:]
        windows = developer.Settings("claude", roles={"triager": {"backend": "codex"}})
        with patch.dict("os.environ", {"OURO_NODE_STUB": "stub"}), patch.object(codex, "WINDOWS", True), \
                self.assertRaisesRegex(sh.DriverError, "does not admit its full"):
            node.preflight(windows, needs={"triager": None})  # the refusal precedes the stub's skip, before any write

    def test_a_mixed_dispatch_runs_each_node_on_its_roles_backend(self):
        cfg = developer.Settings("claude", roles={"triager": {"backend": "qwen", "model": "deepseek-v4-pro"}})
        seen = {}

        def qwen_run(argv, cwd=None, prompt=None, timeout=None, env=None):
            seen["qwen"] = argv
            hook = pathlib.Path(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"]).with_name("hook.jsonl")
            hook.write_text(json.dumps({"tool": "read_file", "granted": True}) + "\n", encoding="utf-8")
            return sh.R(0, json.dumps(QWEN_FOLLOWUP), "", argv)

        def claude_run(argv, **kwargs):
            seen["claude"] = argv
            return sh.R(0, json.dumps(CLAUDE_REVIEW), "", argv)
        usage = len(node.USAGE)
        with tempfile.TemporaryDirectory() as d, patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.dict("os.environ", {"OURO_NODE_STUB": ""}), \
                patch.object(process, "run", side_effect=qwen_run), patch.object(sh, "run", side_effect=claude_run):
            follow = node.run_node("FOLLOWUP", "p", cfg.model("triager", "opus"), READ, d, 30, runtime=cfg)
            review = node.run_node("REVIEW", "p", cfg.model("reviewer", "opus"), READ, d, 30, runtime=cfg)
        self.assertEqual((follow, review["verdict"]), ({"issues": []}, "CLEAN"))
        self.assertEqual(seen["qwen"][-2:], ["--model", "deepseek-v4-pro"])
        self.assertEqual(seen["claude"][:6], ["claude", "-p", "--output-format", "json", "--model", "opus"])
        self.assertEqual([(r["node"], r["backend"], r["model"]) for r in list(diagnostics.ATTEMPTS)[-2:]],
                         [("FOLLOWUP", "qwen", "deepseek-v4-pro"), ("REVIEW", "claude", "opus")])
        self.assertEqual([(u["node"], u["backend"]) for u in node.USAGE[usage:]], [("FOLLOWUP", "qwen"), ("REVIEW", "claude")])
        self.assertIn("| FOLLOWUP | q-1 | 3 | 4 | 0 | 0 | unknown | qwen |", node.usage_table())
        del node.USAGE[usage:]
