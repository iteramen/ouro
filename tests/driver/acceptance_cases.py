"""Real driver grants, scratch mutation probes and no-fix acceptance refusals."""
import json
import os
import pathlib
import re
from unittest.mock import patch


def acceptance_build(h, command):
    # These generic cases target later review decisions. Windows cannot observe
    # a worker fingerprint, so declare its blocked acceptance command explicitly;
    # each review-reaching invocation still supplies its own --validate consent.
    answer = {**h.BUILD, "_run": [command]}
    if os.name == "nt":
        answer["validation_pending"] = [{"cmd": command, "reason": "worker input fingerprint unavailable"}]
    return answer


def exercise(h):
    subset = "python3 probes/subset.py --case selected"
    files = {
        "probes/subset.py": "import runpy\nfn=runpy.run_path('src/app.py')['compute_total']\nassert fn([1,2,3]) == 6, 'selected total expected six'\nprint('selected subset passed')\n",
        "probes/mutation.py": '''import pathlib,subprocess,sys
p=pathlib.Path('src/app.py');original=p.read_text()
cmd=[sys.executable,'probes/subset.py','--case','selected']
green=subprocess.run(cmd,capture_output=True,text=True)
assert green.returncode == 0 and 'selected subset passed' in green.stdout
assert original.count('sum(items) + 0') == 1
try:
    p.write_text(original.replace('sum(items) + 0','0'))
    red=subprocess.run(cmd,capture_output=True,text=True)
    assert red.returncode != 0 and 'selected total expected six' in red.stderr
finally:p.write_text(original)
print('mutation counterfactual red; source restored')
''',
    }
    comments = [
        {"author": {"login": "o"}, "body": "Acceptance command: `python3 obsolete.py`", "createdAt": "2025-12-31T00:00:00Z"},
        {"author": {"login": "o"}, "body": "**Triage**\n\nAcceptance command: `" + subset + "`", "createdAt": h.TRIAGED},
        {"author": {"login": "o"}, "body": "Historically `python3 history.py` ran.\nDo not run `python3 forbidden.py`.\n## Acceptance\n`python3 quoted-history.py` is an obsolete example.\n```text\nAcceptance command: `python3 fenced-history.py`\n```\n    Acceptance command: `python3 indented-history.py`", "createdAt": "2026-01-02T00:00:00Z"},
        {"author": {"login": "outsider"}, "body": "Acceptance command: `python3 untrusted.py`", "createdAt": "2026-01-03T00:00:00Z"},
    ]
    c = h.happy_case("acceptance-trusted-grant", files=files, table={"_issue_1": h.issue(comments=comments)})
    c.node("BUILD", acceptance_build(h, subset))
    code, out, err = c.ouro("run", "1", "--validate")
    tools = c.node_call("BUILD")["tools"]
    h.check("a trusted explicit subset command is granted exactly, without blanket BUILD Bash",
            code == 0 and "Bash(" + subset + ")" in tools and "Bash" not in tools)
    h.check("historical, negative, superseded and untrusted quotations do not grant commands",
            not any(name in str(tools) for name in ("obsolete.py", "history.py", "forbidden.py", "quoted-history.py", "fenced-history.py", "indented-history.py", "untrusted.py")))
    review = c.node_call("REVIEW")
    h.check("sized review has a separate scratch probe with publishing denied and removes it",
            review["tools"] == h.PROBE_TOOLS and review["denied"] == h.PROBE_DENIED
            and "review-1" in review["cwd"] and not pathlib.Path(review["cwd"]).exists())

    # Exercise the real driver review method and capability bridge against real
    # Git worktrees and Python subprocesses. The fixture replaces only OS sandbox
    # launch, not command admission, probe construction or mutation assertions.
    from ouro import developer, sh
    from ouro.runtime import tools as bridge
    ob = sh.load_binding_module()
    env = sh.Env(c.main, ob, ob.load(c.main / ".claude/ouro.toml"), False, runtime=developer.Settings("claude"))
    observed = []
    try:
        with patch.dict(os.environ, c.env()):
            run = h.runmod.Run(env, 1, None, h.runmod.RunOptions())
            run.read_issue(); run.plan_part()
            build_source = (run.tree.path / "src/app.py").read_bytes()
            head = run.tree.head()
            def worker(name, prompt, model, capabilities, cwd, timeout, denied=None, runtime=None):
                assert name == "REVIEW" and "Mode: sized" in prompt and capabilities.probe
                assert pathlib.Path(cwd).resolve() != run.tree.path.resolve()
                policy = {"root": cwd, "edit": capabilities.edit, "commands": list(capabilities.commands), "probe": capabilities.probe}
                # This fixture tests disposable-copy mutation, not native sandbox admission.
                with patch.object(bridge, "sandbox_argv", side_effect=lambda root, argv, **kwargs: argv), \
                        patch.object(bridge, "probe_root"), patch.object(bridge, "probe_configuration"):
                    result = bridge.Bridge(policy).call("run", {"command": "python3 probes/mutation.py"})
                assert result["exit"] == 0 and "mutation counterfactual red; source restored" in result["stdout"], result
                observed.append(result)
                return dict(h.CLEAN)
            with patch.object(h.runmod.node, "run_node", side_effect=worker):
                answer = run.node_review(None, head, [], 1)
            h.check("a sized review actually runs a green subset and a red mutation in its scratch copy",
                    answer["verdict"] == "CLEAN" and len(observed) == 1)
            h.check("temporary review mutations never change the original BUILD input or head",
                    (run.tree.path / "src/app.py").read_bytes() == build_source and run.tree.head() == head
                    and not run.tree.git("status", "--porcelain").out
                    and "review-1" not in env.git("worktree", "list", "--porcelain").out)
    finally:
        env.close()

    body = h.ISSUE_BODY + "\n## Acceptance\n`" + subset + "` passes.\n"
    for stage in ("BUILD", "REVIEW"):
        case = h.happy_case("acceptance-stop-" + stage.lower(), files=files, table={"_issue_1": h.issue(body=body)})
        answer = dict(h.BUILD if stage == "BUILD" else h.CLEAN)
        answer["stop"] = {"reason": "acceptance unrunnable", "question": "Supply the approved mutation runner; this required proof cannot run here."}
        case.node(stage, answer)
        if stage == "REVIEW":
            case.node("BUILD", acceptance_build(h, subset))
        code, out, err = case.ouro("run", "1", *(["--validate"] if stage == "REVIEW" else []))
        wt = case.main / ".claude/worktrees/1-fix-the-total"
        commits = h.git(wt, "rev-list", "--count", "origin/master..HEAD")
        h.check(stage + " unrunnable acceptance stops without a fix round or Ready state",
                code == 3 and any(h.stop_comment_ok(stop, "acceptance unrunnable") for stop in h.stops_of(case))
                and len(case.prompts("BUILD")) == 1 and commits == ("0" if stage == "BUILD" else "1")
                and "Ready for merge" not in out and "review cap" not in out)

    for stage in ("BUILD", "REVIEW"):
        case = h.happy_case("acceptance-denied-" + stage.lower(), files=files, table={"_issue_1": h.issue(body=body)})
        answer = dict(h.BUILD if stage == "BUILD" else h.CLEAN)
        answer["_denied"] = [{"tool_name": "Bash", "tool_input": {"command": subset}}]
        case.node(stage, answer)
        if stage == "REVIEW":
            case.node("BUILD", acceptance_build(h, subset))
        code, out, err = case.ouro("run", "1", *(["--validate"] if stage == "REVIEW" else []))
        h.check("an observed required " + stage + " denial cannot enter empty fix rounds",
                code == 3 and len(case.prompts("BUILD")) == 1
                and any(h.stop_comment_ok(stop, "acceptance unrunnable") for stop in h.stops_of(case)))

    case = h.happy_case("acceptance-denied-retried", files=files, table={"_issue_1": h.issue(body=body)})
    case.node("BUILD", {**acceptance_build(h, subset), "_denied": [{"tool_name": "PowerShell", "tool_input": {"command": subset}}]})
    code, out, err = case.ouro("run", "1")
    wt = case.main / ".claude/worktrees/1-fix-the-total"
    h.check("a required BUILD denial that a later call of the same command ran does not stop the run",
            not any("acceptance unrunnable" in stop for stop in h.stops_of(case))
            and h.git(wt, "rev-list", "--count", "origin/master..HEAD") == "1")

    case = h.happy_case("acceptance-valid-handoff", files=files, table={"_issue_1": h.issue(comments=comments)})
    case.node("BUILD", {**h.BUILD, "validation_pending": [{"cmd": subset, "reason": "approved command blocked in worker environment"}]})
    code, out, err = case.ouro("run", "1")
    h.check("approved environment-blocked acceptance keeps validation_pending without a ruling or review",
            code == 4 and "waiting for validation" in out and not h.stops_of(case)
            and len(case.prompts("BUILD")) == 1 and not case.prompts("REVIEW"))

    caller_only = "PATH='' python3 probes/subset.py --case selected"
    caller_body = h.ISSUE_BODY + "\n## Acceptance\n`" + caller_only + "` passes.\n"
    case = h.happy_case("acceptance-unapproved-prefix", files=files, table={"_issue_1": h.issue(body=caller_body)})
    code, out, err = case.ouro("run", "1", "--validate")
    h.check("an unapproved environment prefix stops before BUILD, execution and PR creation",
            code == 3 and not case.prompts("BUILD") and not case.prompts("REVIEW")
            and not case.pr() and case.branches() == ["master"]
            and any(h.stop_comment_ok(stop, "acceptance unrunnable") for stop in h.stops_of(case)))
    h.check("only an exact already-approved single caller command bypasses worker-prefix refusal",
            h.runmod.acceptance_problems(caller_body)
            and not h.runmod.acceptance_problems(caller_body, [caller_only])
            and caller_only not in h.runmod.acceptance_commands(caller_body))
    injected = caller_only + " | echo injected"
    h.check("caller approval cannot bypass single-command parsing",
            h.runmod.acceptance_problems("## Acceptance\n`" + injected + "`\n", [injected]))
    with patch.object(bridge.process, "run") as launched:
        try:
            bridge.Bridge({"root": str(case.main), "edit": False, "commands": [caller_only], "probe": False}).call(
                "run", {"command": caller_only})
        except ValueError as ex:
            denied = "environment assignments are not granted" in str(ex)
        else:
            denied = False
        h.check("caller-only approval preserves the real worker bridge environment-prefix denial",
                denied and not launched.called)

    for command in ("gh issue close 1", "git push origin HEAD"):
        comment = {"author": {"login": "o"}, "body": "**Triage**\n\nAcceptance command: `" + command + "`", "createdAt": h.TRIAGED}
        case = h.happy_case("acceptance-no-publish-" + command.split()[0], table={"_issue_1": h.issue(comments=[comment])})
        code, out, err = case.ouro("run", "1")
        h.check("explicit " + command.split()[0] + " publication declarations cannot expand worker grants",
                code == 3 and not case.prompts("BUILD") and case.branches() == ["master"]
                and any(h.stop_comment_ok(stop, "acceptance unrunnable") for stop in h.stops_of(case)))

    body = h.ISSUE_BODY + "\n## Acceptance\n`python3 probes/subset.py | tail` passes.\n"
    case = h.happy_case("acceptance-syntax-block", files=files, table={"_issue_1": h.issue(body=body)})
    code, out, err = case.ouro("run", "1")
    h.check("syntactically ungrantable required acceptance stops before BUILD despite another valid gate",
            code == 3 and not case.prompts("BUILD") and case.branches() == ["master"]
            and any(h.stop_comment_ok(stop, "acceptance unrunnable") for stop in h.stops_of(case)))


def review_corrections(h):
    for fence in ("```", "~~~"):
        for tail in ("python", "\u00a0", "\u2028"):
            text = (fence + "markdown\n" + fence + tail + "\n"
                    "Acceptance command: `python3 historical.py`\n" + fence + " \t\n"
                    "Acceptance command: `python3 approved.py`\n")
            h.check("a " + fence[0] + " fence only closes before a space/tab tail: " + repr(tail),
                    h.runmod.acceptance_declarations(text) == ["python3 approved.py"])
    command = "python3 probes/subset.py --case selected"
    comments = [
        {"author": {"login": "o"}, "createdAt": h.TRIAGED,
         "body": "**Triage**\n\n```text\nan old pasted log has no closing fence"},
        {"author": {"login": "o"}, "createdAt": "2026-01-02T00:00:00Z",
         "body": "Acceptance command: `" + command + "`"},
        {"author": {"login": "outsider"}, "createdAt": "2026-01-03T00:00:00Z",
         "body": "Acceptance command: `python3 untrusted-later.py`"},
    ]
    case = h.happy_case("acceptance-independent-comments", files={"probes/subset.py": "print('selected')\n"},
                        table={"_issue_1": h.issue(comments=comments)})
    case.node("BUILD", acceptance_build(h, command))
    code, out, err = case.ouro("run", "1")
    tools = case.node_call("BUILD")["tools"]
    h.check("an unclosed earlier comment fence cannot hide a later trusted amendment or admit an outsider",
            code == 4 and "Bash(" + command + ")" in tools and "untrusted-later" not in str(tools))

    body = h.ISSUE_BODY + "\n## Acceptance\nRun `python3 probes/mutation.py` and require red with each changed hunk reverted.\n"
    question = "The configured read-only reader cannot execute the required mutation and no genuine execution proof covers it; supply the approved proof."
    for verdict, files in (("APPROVE", []), ("REVISE", ["src/app.py"])):
        case = h.happy_case("acceptance-external-stop-" + verdict.lower(), binding=h.EXTERNAL.format(policy="stop-at-pr", review="external-audit"),
                            files={"probes/mutation.py": "print('fixture')\n"}, table={"_issue_1": h.issue(body=body)})
        case.node("BUILD", acceptance_build(h, "python3 probes/mutation.py"))
        stop = {"reason": "acceptance unrunnable", "question": question}
        reply = {"verdict": verdict, "files_read": files, "critiques": [], "stop": stop}
        code, out, err = case.ouro("run", "1", "--validate", extra=case.grok({"stdout": json.dumps(reply), "exit": 0}))
        wt = case.main / ".claude/worktrees/1-fix-the-total"
        h.check("external " + verdict + " plus an acceptance stop cannot approve, retry or spend a fix",
                code == 3 and len(case.grok_calls()) == 1 and len(case.prompts("BUILD")) == 1
                and not case.prompts("REVIEW") and h.git(wt, "rev-list", "--count", "origin/master..HEAD") == "1"
                and any(h.stop_comment_ok(item, "acceptance unrunnable") and question in item for item in h.stops_of(case))
                and not any(item["body"].startswith("**Ready for merge**") for item in case.pr_comments()))
        prompts = list((case.root / "tmp").glob("external-audit-*/*/round-1/prompt.txt"))
        prompt = prompts[0].read_text(encoding="utf-8") if prompts else ""
        h.check("the configured external reader receives the acceptance and truthful stop protocol",
                "python3 probes/mutation.py" in prompt and "Required acceptance" in prompt
                and "acceptance unrunnable" in prompt and '"stop": null' in prompt)

    case = h.happy_case("acceptance-external-malformed-stop", binding=h.EXTERNAL.format(policy="stop-at-pr", review="external-audit"),
                        table={"_issue_1": h.issue(body=body)})
    case.write("probes/mutation.py", "print(\'fixture\')\n")
    h.git(case.main, "add", "probes/mutation.py")
    h.git(case.main, "commit", "-q", "-m", "acceptance fixture")
    h.git(case.main, "push", "-q", "origin", "master")
    case.node("BUILD", acceptance_build(h, "python3 probes/mutation.py"))
    reply = {"verdict": "APPROVE", "files_read": ["src/app.py"], "critiques": [],
             "stop": {"reason": "acceptance unrunnable", "question": ""}}
    code, out, err = case.ouro("run", "1", "--validate", extra=case.grok({"stdout": json.dumps(reply), "exit": 0}))
    named = re.search(r"verdict\.json and stdout\.txt are in ([^\r\n]+)\)", out + err)
    h.check("a malformed external acceptance stop is a driver error without replacement or empty fixes",
            code == 1 and "malformed acceptance stop" in out + err and not h.stops_of(case)
            and len(case.grok_calls()) == 1 and len(case.prompts("BUILD")) == 1 and not case.prompts("REVIEW")
            and named is not None and (pathlib.Path(named.group(1)) / "verdict.json").is_file())

    unread_fields(h)
    part_scope(h)


def part_scope(h):
    """A checklist part run reads the shared acceptance and its own part's, never another part's; a run with no part reads all."""
    from types import SimpleNamespace
    from ouro.acceptance import acceptance_commands, acceptance_text, part_body
    body = (h.ISSUE_BODY + "\n## Acceptance\n`python3 shared.py` passes.\nAcceptance command (part 2): `python3 field2.py`\n"
            "## Acceptance (part 2)\n`python3 part2.py` passes.\n### Tests\n`python3 nested2.py` passes.\n## Notes\n`python3 notes.py` ran once.\n")
    comments = [{"body": "**Triage**\n\nAcceptance command (part 2): `python3 declared2.py`\nAcceptance command: `python3 declared.py`"}]
    shared = {"python3 shared.py", "python3 declared.py"}
    second = {"python3 field2.py", "python3 part2.py", "python3 nested2.py", "python3 declared2.py"}

    def granted(k):
        run = SimpleNamespace(body=body, k=k, env=SimpleNamespace(lookup=lambda key: None), spec_amendments=lambda: comments)
        return h.runmod.granted_acceptance(run), h.runmod.acceptance_problems(h.runmod.acceptance_source(run))
    for k, want in ((1, shared), (2, shared | second), (None, shared | second)):
        commands, problems = granted(k)
        h.check(f"a part-scoped acceptance grants part {k} the shared commands and its own alone, with no refusal",
                set(commands) == want and len(commands) == len(want) and problems == [])
    text = acceptance_text(part_body(body, 1))
    h.check("part 1's required acceptance text holds no other part's section, and the section after it stays unheld",
            "shared.py" in text and not any(s in text for s in ("field2.py", "part2.py", "nested2.py", "notes.py")))
    nested = (h.ISSUE_BODY + "\n## Acceptance\n`python3 shared.py` passes.\n### Acceptance (part 2)\n`python3 p2.py` passes.\n"
              "### Notes\n`python3 after.py` passes.\n## Notes\n")
    h.check("a part-tagged sub-heading under the shared Acceptance heading is that part's alone; the untagged sub-heading after it is shared again",
            [acceptance_commands(part_body(nested, k)) for k in (1, 2)]
            == [["python3 shared.py", "python3 after.py"], ["python3 shared.py", "python3 p2.py", "python3 after.py"]])
    for k in (1, 2):
        run = SimpleNamespace(body=body, k=k, title="t", env=SimpleNamespace(lookup=lambda key: None),
                              spec_amendments=lambda: comments, spec_comments=lambda: "")
        goal = h.runmod.Run.reader_goal(run)
        stops = []
        run.stop_issue = lambda reason, text, code: stops.append(text)
        h.runmod.Run.stop_uncovered(run)
        h.check(f"part {k}'s review brief and `gate uncovered` text name another part's acceptance only when it is part {k}'s own",
                all((s in goal) == (k == 2) for s in ("part2.py", "declared2.py")) and ("part2.py" in stops[0]) == (k == 2) and "shared.py" in stops[0])
    h.check("a refused part-tagged field is still named as granting nothing in its own part's run",
            h.runmod.acceptance_problems(part_body(h.ISSUE_BODY + "\n## Notes\nAcceptance command (part 1): `python3 x.py`\n", 1)) != [])
    from ouro.acceptance import ACCEPTANCE
    from ouro.triage import ungranted_declarations
    text, turned = ungranted_declarations("Acceptance command (part 2): `gh issue close 1`\n", h.ISSUE_BODY, [], ACCEPTANCE)
    h.check("triage turns a refused part-tagged field into prose, as it does an untagged one",
            turned == ["gh issue close 1"] and "Acceptance command" not in text and "Acceptance check, not a grant" in text)


def unread_fields(h):
    problems = h.runmod.acceptance_problems
    dotnet = "dotnet test X.csproj -c Debug --filter FullyQualifiedName~Y"
    notes = h.ISSUE_BODY + "\n## Notes\nThe owner's grant:\nAcceptance command: " + dotnet + "\n"
    found = problems(notes)
    h.check("a bare Acceptance command line under another heading names both fixes",
            len(found) == 1 and found[0].startswith("`" + dotnet + "`: ")
            and "put the command in backticks" in found[0] and "move the line under an `## Acceptance` heading" in found[0])
    found = problems(h.ISSUE_BODY + "\n## Acceptance\n- **Acceptance command:** " + dotnet + "\n")
    h.check("a bare Acceptance command line under an Acceptance heading names the backticks alone",
            len(found) == 1 and "put the command in backticks" in found[0] and "## Acceptance" not in found[0])
    for name, body in (("whole-line field", "\nAcceptance command: `" + dotnet + "`\n"),
                       ("field with trailing prose", "\nAcceptance command: `" + dotnet + "` passes\n")):
        found = problems(h.ISSUE_BODY + body)
        h.check("a backticked " + name + " outside an Acceptance heading names the move, not the runner forms",
                len(found) == 1 and found[0].startswith("`" + dotnet + "`: ")
                and "move the line under an `## Acceptance` heading" in found[0]
                and "backticks" not in found[0] and "runner forms" not in found[0])
    label = "this `Acceptance command:` line grants nothing as written; "
    for name, heading, below, fix in (
            ("outside an Acceptance heading before a sh fence names the move alone", "Notes", "```sh\n" + dotnet + "\n```\n",
             "move the line and its command under an `## Acceptance` heading"),
            ("outside an Acceptance heading before a bare next line names both fixes", "Notes", dotnet + "\n",
             "put the command in backticks and move the line and its command under an `## Acceptance` heading"),
            ("under an Acceptance heading before a sh fence under another heading names the move alone", "Acceptance",
             "## Notes\n```sh\n" + dotnet + "\n```\n", "move the line and its command under an `## Acceptance` heading"),
            ("under an Acceptance heading before a bare next line names the backticks alone", "Acceptance", dotnet + "\n",
             "put the command in backticks"),
            ("under an Acceptance heading before a text fence names the backticks alone", "Acceptance", "```text\n" + dotnet + "\n```\n",
             "put the command in backticks"),
            ("under an Acceptance heading, its command granted nowhere, before prose and a sh fence under another heading names the backticks alone",
             "Acceptance",
             "Run from the repo root:\n## Notes\n```sh\n" + dotnet + "\n```\n", "put the command in backticks")):
        body = h.ISSUE_BODY + "\n## " + heading + "\nAcceptance command:\n" + below
        h.check("a label line " + name, problems(body) == [label + fix] and dotnet not in h.runmod.acceptance_commands(body))
    for name, text, below in (
            ("an unbackticked runner above an unrelated granted sh fence", dotnet, "```sh\npython3 lint.py\n```\n"),
            ("an unbackticked runner above a non-runner span", dotnet, "Needs `DOTNET_ROOT` set.\n"),
            ("prose above a non-runner span", "run the suite", "Edit `foo.py` first.\n"),
            ("prose above prose and a text fence", "run the suite", "Run from the repo root:\n```text\n" + dotnet + "\n```\n")):
        body = h.ISSUE_BODY + "\n## Acceptance\nAcceptance command: " + text + "\n" + below
        h.check("a label line carrying " + name + " names the backticks alone",
                problems(body) == ["`" + text + "`: " + label + "put the command in backticks"])
    for name, tail, fix in (
            ("under Acceptance, its command granted above, before prose and a sh fence under another heading",
             "## Acceptance\n`" + dotnet + "`\nAcceptance command: run the suite\nRun from the repo root:\n## Notes\n```sh\n" + dotnet + "\n```\n",
             "put the command in backticks"),
            ("under Acceptance before the next field, which grants the command",
             "## Acceptance\nAcceptance command: run the suite\nAcceptance command: `" + dotnet + "`\n", "put the command in backticks"),
            ("under another heading before a sh fence opening Acceptance",
             "## Notes\nAcceptance command: run the suite\n## Acceptance\n```sh\n" + dotnet + "\n```\n",
             "put the command in backticks and move the line under an `## Acceptance` heading")):
        h.check("a label line carrying prose " + name + " is refused", problems(h.ISSUE_BODY + "\n" + tail) == ["`run the suite`: " + label + fix])
    for name, body in (("a whole-line field under Acceptance", h.ISSUE_BODY + "\n## Acceptance\nAcceptance command: `" + dotnet + "`\n"),
                       ("a bold bullet field under Verification", h.ISSUE_BODY + "\n### Verification\n* **Acceptance command:** `" + dotnet + "`\n"),
                       ("a span under Tests", h.ISSUE_BODY + "\n## Tests\nAcceptance command: `" + dotnet + "` passes\n"),
                       ("a trusted comment's field", h.runmod.declared_source(h.ISSUE_BODY, [dotnet])),
                       ("a bare line whose command a trusted comment grants", h.runmod.declared_source(notes, [dotnet])),
                       ("fenced and indented examples", h.ISSUE_BODY + "\n## Anchors\n```text\nAcceptance command: " + dotnet
                        + "\n```\n    Acceptance command: " + dotnet + "\n\tAcceptance command: `" + dotnet + "`\n"),
                       ("a quoted field inside a list item", h.ISSUE_BODY + "\n## What happened\n2. Granted:\n   `Acceptance command: " + dotnet + "`\n"),
                       ("a label line before a sh fence", h.ISSUE_BODY + "\n## Acceptance\nAcceptance command:\n```sh\n" + dotnet + "\n```\n"),
                       ("a bold label line before a bash fence", h.ISSUE_BODY + "\n## Acceptance\n**Acceptance command:**\n```bash\n" + dotnet + "\n```\n"),
                       ("a label line before a span on the next line", h.ISSUE_BODY + "\n## Acceptance\nAcceptance command:\n`" + dotnet + "`\n"),
                       ("a label line ending another section before a sh fence opening Acceptance",
                        h.ISSUE_BODY + "\n## Notes\nAcceptance command:\n## Acceptance\n```sh\n" + dotnet + "\n```\n"),
                       ("a label line and a prose line before a sh fence",
                        h.ISSUE_BODY + "\n## Acceptance\nAcceptance command:\nRun from the repo root:\n```sh\n" + dotnet + "\n```\n"),
                       ("a label line and a sub-heading before a sh fence",
                        h.ISSUE_BODY + "\n## Acceptance\nAcceptance command:\n#### Linux\n```sh\n" + dotnet + "\n```\n"),
                       ("a label line carrying prose before a sh fence",
                        h.ISSUE_BODY + "\n## Acceptance\nAcceptance command: run from the repo root\n```sh\n" + dotnet + "\n```\n")):
        h.check("granted forms and examples yield no unread-field problem: " + name, problems(body) == [])
    quoted = ("## What happens\n```text\n## Acceptance\nRun it.\n```\nThe quoted body then ran `dotnet test X.csproj -c Debug`.\n"
              "## Acceptance\nAcceptance command: `python3 tests/driver/driver.Tests.py`\n")
    h.check("a heading inside a fence opens no section: only the real section's command is granted",
            h.runmod.acceptance_commands(quoted) == ["python3 tests/driver/driver.Tests.py"])
    h.check("a line holding an inline code span after its opening backticks opens no fence: the Acceptance section stays granted",
            h.runmod.acceptance_commands("## What\n```dotnet build```\n## Acceptance\n`python3 a.py`\n") == ["python3 a.py"])
    h.check("a truly unclosed fence hides the Acceptance heading below it: nothing is granted",
            h.runmod.acceptance_commands("## What\n```text\ndotnet build\n## Acceptance\n`python3 a.py`\n") == [])
    h.check("a non-runner declaration keeps the runner-forms message",
            problems(h.runmod.declared_source(h.ISSUE_BODY, ["gh issue close 1"]))
            == ["`gh issue close 1`: this explicit command is outside the approved acceptance runner forms"])

    declared_runners(h)

    case = h.happy_case("acceptance-unread-field", table={"_issue_1": h.issue(body=notes)})
    code, out, err = case.ouro("run", "1")
    h.check("an unread Acceptance command line stops the run before BUILD, naming its fix",
            code == 3 and not case.prompts("BUILD") and case.branches() == ["master"]
            and any(h.stop_comment_ok(stop, "acceptance unrunnable") and "put the command in backticks" in stop for stop in h.stops_of(case)))


def declared_runners(h):
    """A `ship.acceptance_runners` prefix is a runner the driver grants exactly as it grants a built-in one."""
    from ouro.acceptance import ACCEPTANCE
    cargo = "cargo test -p core"
    extended = ACCEPTANCE + ("cargo ",)
    body = h.ISSUE_BODY + "\n## Acceptance\n`" + cargo + "` passes.\n"
    field = h.runmod.declared_source(h.ISSUE_BODY, [cargo])
    refusal = ["`" + cargo + "`: this explicit command is outside the approved acceptance runner forms"]
    h.check("a declared runner prefix grants its command under an Acceptance heading",
            h.runmod.acceptance_commands(body, extended) == [cargo] and h.runmod.acceptance_problems(body, (), extended) == [])
    h.check("a declared runner prefix lets its explicit Acceptance command field raise no problem",
            h.runmod.acceptance_problems(field, (), extended) == [])
    h.check("with no prefix declared the same command is not granted and its field keeps the runner-forms refusal",
            h.runmod.acceptance_commands(body) == [] and h.runmod.acceptance_problems(field) == refusal)
    h.check("a declared prefix adds a runner, not shell grammar",
            h.runmod.acceptance_problems(h.runmod.declared_source(h.ISSUE_BODY, [cargo + " | tail"]), (), extended) != [])

    binding = h.BINDING.format(policy="stop-at-pr", review="adversarial-review").replace("[ship]\n", '[ship]\nacceptance_runners = ["cargo "]\n')
    pending = {**h.BUILD, "validation_pending": [{"cmd": cargo, "reason": "no cargo toolchain in the fixture"}]}
    case = h.happy_case("acceptance-declared-runner", binding=binding, table={"_issue_1": h.issue(body=body)})
    case.node("BUILD", pending)
    code, out, err = case.ouro("run", "1")
    h.check("a run whose binding declares the prefix grants the command to BUILD exactly",
            code == 4 and len(case.prompts("BUILD")) == 1 and "Bash(" + cargo + ")" in case.node_call("BUILD")["tools"]
            and not h.stops_of(case))
    triage = [{"author": {"login": "o"}, "body": "**Triage**\n\nAcceptance command: `" + cargo + "`", "createdAt": h.TRIAGED}]
    case = h.happy_case("acceptance-undeclared-runner", table={"_issue_1": h.issue(comments=triage)})
    code, out, err = case.ouro("run", "1")
    h.check("a run whose binding declares no prefix refuses the same field before BUILD",
            code == 3 and not case.prompts("BUILD")
            and any(h.stop_comment_ok(stop, "acceptance unrunnable") and refusal[0] in stop for stop in h.stops_of(case)))
    case = h.happy_case("acceptance-declared-runner-field", binding=binding, table={"_issue_1": h.issue(comments=triage)})
    case.node("BUILD", pending)
    code, out, err = case.ouro("run", "1")
    h.check("a run whose binding declares the prefix grants the same field to BUILD, with no stop",
            code == 4 and len(case.prompts("BUILD")) == 1 and "Bash(" + cargo + ")" in case.node_call("BUILD")["tools"]
            and not h.stops_of(case))
    declared_runners_triage(h, cargo)


def declared_runners_triage(h, cargo):
    """Triage reads a declared prefix as the run does: its grant list, the PROMOTE check, the comment's fields and the empty-list note."""
    notice = h.BINDING.format(policy="stop-at-pr", review="adversarial-review").replace(
        'ci = "echo SKIP: the checks cannot run this gate"', 'ci = "echo another workflow runs this gate"')  # no gate BUILD is granted
    declared = notice.replace("[ship]\n", '[ship]\nacceptance_runners = ["cargo "]\n')
    labels = ("needs-triage", "gates", "bug")
    answer = {"verdict": "PROMOTE", "size": "S", "flow": "express", "doc_impact": "none",
              "comment": "One deliverable.\n\nAcceptance command: `" + cargo + "`\n"}

    def triage(name, binding, acceptance):
        body = h.ISSUE_BODY.replace("Doc impact on close: none", "## Acceptance\n" + acceptance + "\n\nDoc impact on close: none")
        case = h.Case(name, binding=binding, table={"_issue_1": h.issue(labels=labels, body=body)})
        case.node("TRIAGE", answer)
        code, out, err = case.ouro("triage", "1", "--dry-run")
        return code, out, open(case.nodes / case.prompts("TRIAGE")[0], encoding="utf-8").read()

    field = "Acceptance command: `" + cargo + "`"
    code, out, brief = triage("triage-declared-runner", declared, field)
    h.check("triage with a declared prefix grants its command, promotes, keeps the comment's field and adds no empty-list note",
            code == 0 and "as the driver reads them: `" + cargo + "`." in brief and "labels +['agent-ready'] -['needs-triage']" in out
            and field in out and "not a grant" not in out and "empty command list" not in out)
    code, out, brief = triage("triage-undeclared-runner", notice, field)
    h.check("triage with no prefix declared asks on the same body and turns the comment's field into prose",
            code == 3 and "none, the body grants no command" in brief and "labels +['needs-ruling'] -['needs-triage']" in out
            and "Acceptance check, not a grant (this explicit command is outside the approved acceptance runner forms): `" + cargo + "`" in out
            and "would get an empty command list" in out)
    code, out, brief = triage("triage-declared-runner-env", declared, "`OURO_X=1 " + cargo + "` passes.")
    h.check("triage's empty-list note names an environment-prefixed command that starts with a declared prefix",
            "The acceptance names `OURO_X=1 " + cargo + "`, and the BUILD node would get an empty command list" in out)
