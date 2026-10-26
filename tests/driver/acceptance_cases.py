"""Real driver grants, scratch mutation probes and no-fix acceptance refusals."""
import json
import os
import pathlib
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
            review["tools"] == ["Read", "Grep", "Glob", "Bash"]
            and review["denied"] == ["Bash(git push *)", "Bash(git commit *)", "Bash(gh *)"]
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
            run = h.runmod.Run(env, 1, None)
            run.read_issue(); run.plan_part()
            build_source = (run.worktree / "src/app.py").read_bytes()
            head = run.head()
            def worker(name, prompt, model, capabilities, cwd, timeout, denied=None, runtime=None):
                assert name == "REVIEW" and "Mode: sized" in prompt and capabilities.probe
                assert pathlib.Path(cwd).resolve() != run.worktree.resolve()
                policy = {"root": cwd, "edit": capabilities.edit, "commands": list(capabilities.commands), "probe": capabilities.probe}
                with patch.object(bridge, "sandbox_argv", side_effect=lambda root, argv: argv):
                    result = bridge.Bridge(policy).call("run", {"command": "python3 probes/mutation.py"})
                assert result["exit"] == 0 and "mutation counterfactual red; source restored" in result["stdout"], result
                observed.append(result)
                return dict(h.CLEAN)
            with patch.object(h.runmod.node, "run_node", side_effect=worker):
                answer = run.node_review(None, head, [], 1)
            h.check("a sized review actually runs a green subset and a red mutation in its scratch copy",
                    answer["verdict"] == "CLEAN" and len(observed) == 1)
            h.check("temporary review mutations never change the original BUILD input or head",
                    (run.worktree / "src/app.py").read_bytes() == build_source and run.head() == head
                    and not run.wt_git("status", "--porcelain").out
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

    case = h.happy_case("acceptance-valid-handoff", files=files, table={"_issue_1": h.issue(comments=comments)})
    case.node("BUILD", {**h.BUILD, "validation_pending": [{"cmd": subset, "reason": "approved command blocked in worker environment"}]})
    code, out, err = case.ouro("run", "1")
    h.check("approved environment-blocked acceptance keeps validation_pending without a ruling or review",
            code == 0 and "waiting for validation" in out and not h.stops_of(case)
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
            code == 0 and "Bash(" + command + ")" in tools and "untrusted-later" not in str(tools))

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
    h.check("a malformed external acceptance stop is a driver error without replacement or empty fixes",
            code == 1 and "malformed acceptance stop" in out + err and not h.stops_of(case)
            and len(case.grok_calls()) == 1 and len(case.prompts("BUILD")) == 1 and not case.prompts("REVIEW"))
