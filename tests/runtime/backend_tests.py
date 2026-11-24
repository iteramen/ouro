"""Backend interface conformance: every registered backend implements each member or declares a marker."""
import dataclasses
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from ouro import developer, node, sh
from ouro.runtime import PROBE, READ, SANDBOXED_PROBE, Capabilities, builder, codex, diagnostics, process
from ouro.runtime.backends import BACKENDS, CONFINED, NOT_REQUIRED, UNCONFINED, UNSUPPORTED, Backend, Interface

MARKERS = {"worktree_readiness": {NOT_REQUIRED}, "validate_answer": {UNSUPPORTED},
           "probe_confinement": {CONFINED, UNCONFINED}, "sandbox_route": {NOT_REQUIRED, UNSUPPORTED}}
# Today's matrix; "implemented" means a callable. A new backend or member must be added here.
MATRIX = {
    "claude": {"preflight": "implemented", "worktree_readiness": NOT_REQUIRED, "execute": "implemented",
               "probe_confinement": UNCONFINED, "sandbox_route": "implemented", "validate_answer": UNSUPPORTED,
               "admits": "implemented"},
    "codex": {"preflight": "implemented", "worktree_readiness": "implemented", "execute": "implemented",
              "probe_confinement": CONFINED, "sandbox_route": "implemented", "validate_answer": "implemented",
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
                 '[runtime]\nbackend="qwen"\neffort="low"':
                     "runtime.effort requires Claude or Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="claude"\neffort="none"':
                     "runtime.effort requires Claude or Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="codex"\neffort="none"':
                     "runtime.effort requires Claude or Codex and low, medium, high, xhigh or max",
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
                 '[runtime]\nbackend="claude"\n[runtime.roles.triager]\nbackend="qwen"\nmodel="m"\neffort="high"':
                     "runtime.roles.triager.effort requires Claude or Codex and low, medium, high, xhigh or max",
                 '[runtime]\nbackend="claude"\nadvisor="gpt-example"':
                     "runtime.advisor must be up, opus, sonnet, haiku or fable",
                 '[runtime]\nbackend="claude"\n[runtime.roles.builder]\nadvisor="next"':
                     "runtime.roles.builder.advisor must be up, opus, sonnet, haiku or fable",
                 '[runtime]\nbackend="codex"\nadvisor="up"': "runtime.advisor: Codex takes no advisor",
                 '[runtime]\nbackend="claude"\n[runtime.roles.triager]\nbackend="qwen"\nmodel="m"\nadvisor="up"':
                     "runtime.roles.triager.advisor: Qwen Code takes no advisor",
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

    def test_a_claude_effort_reaches_the_argv_and_the_attempt_row_and_none_passes_no_flag(self):
        from ouro.runtime import claude
        seen = []

        def run(argv, **kwargs):
            seen.append(argv)
            return sh.R(0, json.dumps(CLAUDE_REVIEW), "", argv)
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "developer.toml"
            path.write_text('[runtime]\nbackend="claude"\neffort="high"\n[runtime.roles.reviewer]\neffort="max"\n', encoding="utf-8")
            pinned = developer.load(path)
        usage = len(node.USAGE)
        with patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(sh, "run", side_effect=run):
            for settings in (pinned.of("builder"), developer.Settings()):
                claude._execute("REVIEW", "p", "opus", ["Read"], ".", 5, None, {}, settings)
            node._real("REVIEW", "p", "opus", ["Read"], ".", 5, None, pinned)
        self.assertEqual([u["effort"] for u in node.USAGE[usage:]], ["max"], "the usage row carries the effort the call was given")
        del node.USAGE[usage:]
        self.assertEqual(seen[0][:8], ["claude", "-p", "--output-format", "json", "--model", "opus", "--effort", "high"])
        self.assertEqual(seen[1], ["claude", "-p", "--output-format", "json", "--model", "opus", "--allowedTools", "Read",
                                   "--permission-mode", "dontAsk", "--json-schema", "{}"])
        self.assertEqual(seen[2][6:8], ["--effort", "max"], "a role's effort wins over runtime.effort")
        row = diagnostics.ATTEMPTS[-1]
        self.assertEqual((row["node"], row["model"], row["effort"]), ("REVIEW", "opus", "max"))
        self.assertIn("| REVIEW | claude | opus | max |", diagnostics.table())

    def test_claude_preflight_requires_effort_only_when_a_role_it_serves_sets_one(self):
        from ouro.runtime import claude
        help_text = " ".join(claude.FLAGS)

        def preflight(settings, listed=help_text):
            results = [sh.R(0, '{"loggedIn": true}', "", []), sh.R(0, listed, "", [])]
            with patch.object(sh, "argv_for", return_value=["claude"]), patch.object(sh, "run", side_effect=results):
                claude.preflight(settings)
        preflight(developer.Settings())
        preflight(developer.Settings(roles={"triager": {"backend": "codex", "effort": "high"}}))  # no Claude role sets one
        for settings in (developer.Settings(effort="high"), developer.Settings(roles={"reviewer": {"effort": "max"}})):
            with self.assertRaisesRegex(sh.DriverError, "lists no --effort; update Claude Code"):
                preflight(settings)
            preflight(settings, help_text + " --effort")

    def test_a_claude_advisor_reaches_one_settings_argument_and_the_attempt_row(self):
        from ouro.runtime import claude
        seen, said = [], []

        def run(argv, **kwargs):
            seen.append(argv)
            used = {"claude-fable": {}} if argv[argv.index("--model") + 1] == "fable" else {"claude-opus": {}, "claude-fable": {}}
            return sh.R(0, json.dumps({**CLAUDE_REVIEW, "modelUsage": used}), "", argv)

        def launched(argv):
            settings = [i for i, a in enumerate(argv) if a == "--settings"]
            return [json.loads(argv[i + 1]) for i in settings]
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "developer.toml"
            path.write_text('[runtime]\nbackend="claude"\nadvisor="up"\n[runtime.roles.reviewer]\nadvisor="sonnet"\n',
                            encoding="utf-8")
            pinned = developer.load(path)
        usage = len(node.USAGE)
        with tempfile.TemporaryDirectory() as d, patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                patch.object(sh, "run", side_effect=run), patch.object(sh, "say", side_effect=said.append):
            claude.execute("CHECKPOINT", "p", "opus", ["Read"], d, 5, None, {}, pinned.of("builder"))
            claude.execute("CHECKPOINT", "p", "fable", ["Read"], d, 5, None, {}, pinned.of("builder"))
            node._real("REVIEW", "p", "opus", ["Read"], d, 5, None, pinned)
            claude.execute("BUILD", "p", "sonnet", builder(["python3 check.py"]), d, 5, None, {}, pinned.of("builder"))
            claude.execute("CHECKPOINT", "p", "opus", ["Read"], d, 5, None, {}, developer.Settings())
            node._real("CHECKPOINT", "p", "fable", ["Read"], d, 5, None, pinned)
        del node.USAGE[usage:]
        self.assertEqual(launched(seen[0]), [{"advisorModel": "fable"}], "up on opus is fable")
        self.assertEqual(launched(seen[1]), [], "up on fable passes no advisor")
        self.assertEqual(said[0], "node CHECKPOINT: runtime advisor up: fable is the top tier, so this node has no advisor")
        self.assertEqual(launched(seen[2]), [{"advisorModel": "sonnet"}], "a role's alias is passed as written")
        build = launched(seen[3])
        self.assertEqual(len(build), 1, "hooks and the advisor share one --settings argument")
        self.assertEqual((build[0]["advisorModel"], list(build[0]["hooks"])), ("opus", ["PreToolUse"]))
        self.assertEqual(launched(seen[4]), [], "no advisor configured: the host's settings decide")
        review, top = diagnostics.ATTEMPTS[-2], diagnostics.ATTEMPTS[-1]
        self.assertEqual((review["node"], review["effort"], review["advisor"], review["second_model"]),
                         ("REVIEW", None, "sonnet", True))
        self.assertEqual((top["advisor"], top["second_model"]), (None, False))
        self.assertIn("| REVIEW | claude | opus | unknown | sonnet | True |", diagnostics.table())
        self.assertIn("| CHECKPOINT | claude | fable | unknown | unknown | False |", diagnostics.table())

    def test_a_claude_effort_removes_the_hosts_effort_variable_and_none_leaves_it(self):
        from ouro.runtime import claude
        seen = []

        def run(argv, **kwargs):
            seen.append(kwargs["env"])
            return sh.R(0, json.dumps(CLAUDE_REVIEW), "", argv)
        with patch.dict("os.environ", {"CLAUDE_CODE_EFFORT_LEVEL": "low"}), \
                patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(sh, "run", side_effect=run):
            for settings in (developer.Settings(effort="max"), developer.Settings()):
                claude._execute("REVIEW", "p", "opus", ["Read"], ".", 5, None, {}, settings)
            self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", process.child_env(seen[0]), "--effort decides, not the host")
            self.assertEqual(process.child_env(seen[1])["CLAUDE_CODE_EFFORT_LEVEL"], "low", "no effort: the host's stays")

    def test_a_codex_effort_reaches_the_argv_and_the_attempt_row_and_none_passes_no_override(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(argv)
            raise sh.DriverError("captured")
        with tempfile.TemporaryDirectory() as d, patch.object(sh, "which", side_effect=lambda n: n), \
                patch.object(process, "run", side_effect=run):
            for settings in (developer.Settings("codex", effort="high"), developer.Settings("codex")):
                with self.assertRaises(sh.DriverError):
                    codex.execute("CHECKPOINT", "p", "default", READ, d, 1, None, {}, settings)
                self.assertEqual(diagnostics.ATTEMPTS[-1]["effort"], settings.effort)
        self.assertIn('model_reasoning_effort="high"', seen[0])
        self.assertEqual(seen[0][seen[0].index('model_reasoning_effort="high"') - 1], "-c")
        self.assertFalse([a for a in seen[1] if a.startswith("model_reasoning_effort")])

    def test_a_claude_envelope_turn_count_reaches_the_attempt_row_and_a_missing_one_is_unknown(self):
        for envelope, expected in (({**CLAUDE_REVIEW, "num_turns": 7}, 7), (CLAUDE_REVIEW, None),
                                   ({**CLAUDE_REVIEW, "num_turns": "7"}, None)):
            usage = len(node.USAGE)
            with self.subTest(turns=envelope.get("num_turns")), patch.object(sh, "argv_for", side_effect=lambda *a: list(a)), \
                    patch.object(sh, "run", return_value=sh.R(0, json.dumps(envelope), "", [])):
                node._real("REVIEW", "p", "opus", ["Read"], ".", 5, None, developer.Settings())
                del node.USAGE[usage:]
                self.assertEqual(diagnostics.ATTEMPTS[-1]["turns"], expected)
        self.assertIn("| turns |", diagnostics.table())

    def test_claude_probe_grants_both_shells_and_denies_publishing_in_each(self):
        from ouro.runtime import SANDBOXED_PROBE, claude
        self.assertEqual(claude.tools_for(PROBE), (
            ["Read", "Grep", "Glob", "Bash", "PowerShell"],
            ["Bash(git push *)", "Bash(git commit *)", "Bash(gh *)",
             "PowerShell(git push *)", "PowerShell(git commit *)", "PowerShell(gh *)"]))
        tools, denied = claude.tools_for(SANDBOXED_PROBE)
        self.assertEqual(tools, ["Read", "Grep", "Glob", "Bash"], "a sandboxed probe grants Bash alone")
        self.assertEqual(denied, claude.tools_for(PROBE)[1])
        self.assertEqual(claude.tools_for(builder(["python3 check.py"])), (
            ["Edit", "Write", "Read", "Grep", "Glob", "Bash(git rm *)", "Bash(git mv *)", "Bash(git status *)",
             "Bash(git diff *)", "Bash(git log *)", "Bash(git ls-files *)", "Bash(python3 check.py)"], None))
        self.assertEqual(claude.tools_for(READ), (["Read", "Grep", "Glob"], None))

    def test_node_opens_one_attempt_row_per_execution_for_every_backend(self):
        for name, backend in BACKENDS.items():
            calls = []

            def execute(*args):
                calls.append(args)
                args[-1]["session"] = "filled-by-backend"
                return {"finding": "x"}, "s", {}, 0.25
            fake = dataclasses.replace(backend.interface, execute=execute)
            before, usage = len(diagnostics.ATTEMPTS), len(node.USAGE)
            with self.subTest(backend=name), patch.object(type(backend), "interface", new_callable=PropertyMock, return_value=fake):
                node._real("CHECKPOINT", "p", "m", [], ".", 1, None, developer.Settings(name))
            recorded = node.USAGE[-1]["cost"]
            del node.USAGE[usage:]
            self.assertEqual(len(diagnostics.ATTEMPTS), before + 1)
            row = diagnostics.ATTEMPTS[-1]
            self.assertEqual((row["backend"], row["outcome"], row["session"]), (name, "adapter_returned", "filled-by-backend"))
            self.assertEqual((row["cost"], recorded), (0.25, 0.25))
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
        binding = {}
        runner.env = SimpleNamespace(lookup=lambda key, default=None: binding.get(key, default))
        runner.labels, runner.flow = [], "express"
        self.assertEqual(runner.contracts(), {"triager": READ, "builder": None, "reviewer": READ})
        runner.labels, runner.flow = ["trivial"], "full"
        self.assertEqual(runner.contracts(), {"triager": READ, "builder_trivial": None, "reviewer": PROBE})
        # ship.review_sandbox = true plans the sandboxed PROBE the review node launches with, so preflight proves that route.
        binding["ship.review_sandbox"] = True
        self.assertEqual(runner.contracts(), {"triager": READ, "builder_trivial": None, "reviewer": SANDBOXED_PROBE})
        runner.flow = "express"
        self.assertEqual(runner.contracts()["reviewer"], READ, "an express review reads, sandbox or not")
        del binding["ship.review_sandbox"]
        runner.labels, runner.build_commands = ["checkpoint"], lambda: ("(none)", [])
        self.assertEqual(runner.contracts(), {"checkpointer": READ})
        runner.build_commands = lambda: ("- `python3 check.py`", ["python3 check.py"])
        self.assertEqual(runner.contracts(), {"checkpointer": Capabilities(commands=("python3 check.py",))})

        cfg = developer.Settings("claude", roles={"triager": {"backend": "qwen"}, "reviewer": {"backend": "qwen"}})
        groups = node.plan(cfg, {"triager": READ, "builder": None, "reviewer": READ})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in groups.items()},
                         {"qwen": ({"triager": READ, "reviewer": READ}, READ), "claude": ({"builder": None}, None)})
        self.assertEqual(groups["qwen"][0].serving("qwen", ("triager",)), ["triager", "reviewer"])
        self.assertEqual(groups["claude"][0].serving("claude"), ["builder"])
        groups = node.plan(cfg, {"triager": READ, "builder": None, "reviewer": PROBE})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in groups.items()},
                         {"qwen": ({"triager": READ, "reviewer": PROBE}, None), "claude": ({"builder": None}, None)})
        self.assertEqual(set(node.plan(cfg, {"checkpointer": Capabilities(commands=("python3 check.py",))})), {"claude"})
        moved = developer.Settings("claude", roles={"checkpointer": {"backend": "qwen"}})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in
                          node.plan(moved, {"checkpointer": Capabilities(commands=("python3 check.py",))}).items()},
                         {"qwen": ({"checkpointer": Capabilities(commands=("python3 check.py",))}, None)})
        building = developer.Settings("claude", roles={"builder": {"backend": "qwen"}})
        self.assertEqual({name: (scoped.serves, contract) for name, (scoped, contract) in
                          node.plan(building, {"triager": READ, "builder": None, "reviewer": PROBE}).items()},
                         {"claude": ({"triager": READ, "reviewer": PROBE}, None), "qwen": ({"builder": None}, None)})
        windows = developer.Settings("claude", roles={"builder": {"backend": "codex"}})
        with patch.object(codex, "WINDOWS", True), self.assertRaises(sh.DriverError) as caught:
            node.plan(windows, {"triager": READ, "builder": None, "reviewer": PROBE})
        self.assertEqual(str(caught.exception), "preflight: role builder selects Codex, which does not admit its full "
                                                "contract; select another backend in [runtime.roles.builder]")
        reviewing = developer.Settings("claude", roles={"reviewer": {"backend": "codex"}})
        with patch.object(codex, "WINDOWS", True), self.assertRaisesRegex(sh.DriverError, "does not admit its PROBE contract"):
            node.plan(reviewing, {"reviewer": SANDBOXED_PROBE})
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
            self.assertEqual(calls, [("qwen", {"triager": READ}, READ), ("claude", {"builder": None, "reviewer": READ}, None)])
            self.assertEqual([(u["node"], u["backend"]) for u in node.USAGE[usage:]], [("PREFLIGHT", "qwen"), ("PREFLIGHT", "claude")])
            del calls[:], node.USAGE[usage:]
            commands = Capabilities(commands=("python3 check.py",))
            reviewing = developer.Settings("claude", roles={"reviewer": {"backend": "qwen"}})
            node.preflight(reviewing, needs={"reviewer": PROBE, "checkpointer": commands})
            self.assertEqual(calls, [("qwen", {"reviewer": PROBE}, None), ("claude", {"checkpointer": commands}, None)])
            del calls[:], node.USAGE[usage:]
            same = developer.Settings("claude", roles={"reviewer": {"model": "sonnet"}})
            node.preflight(same, capabilities=READ, needs={"triager": READ})
            node.preflight(same, needs={"triager": READ, "builder": None, "reviewer": PROBE})
            node.preflight(same, capabilities=READ)
            self.assertEqual(calls, [("claude", {"triager": READ}, READ),  # one backend: one call, scoped to the command's roles
                                     ("claude", {"triager": READ, "builder": None, "reviewer": PROBE}, None), ("claude", {}, READ)])
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
