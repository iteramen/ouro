"""Real driver subprocesses against a loopback MCP board, within the bound driver suite."""
import contextlib
import http.server
import json
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from ouro import coordination as co

KEY = "fixture-coordination-secret"
ENV = "OURO_DRIVER_BOARD_KEY"
TOOLS = ("board_search", "board_list_threads", "board_read_thread", "board_post", "board_reply", "board_update_thread")


@contextlib.contextmanager
def board():
    class State:
        threads = {}
        calls = []
        mode = ""
        reads = 0

        def add(self, body="Convention-only claim", author="other developer"):
            ident = str(len(self.threads) + 1)
            summary = dict(id=ident, forum="fixture", title="Claimed: o/n#1", status="open", createdBy=author)
            self.threads[ident] = dict(thread=summary, messages=[dict(id="m1", from_=author, body=body)])
            return ident

    state = State()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = request["method"]
                if state.mode == "auth":
                    self.send_error(401, KEY)
                    return
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = dict(protocolVersion=co.PROTOCOL, capabilities={}, serverInfo=dict(name="fixture", version="1"))
                elif method == "tools/list":
                    result = dict(tools=[dict(name=n, inputSchema=dict(type="object")) for n in TOOLS])
                else:
                    name, args = request["params"]["name"], request["params"]["arguments"]
                    state.calls.append((name, args))
                    if name == "board_list_threads":
                        value = dict(threads=[t["thread"] for t in state.threads.values() if t["thread"]["status"] == "open"])
                        if state.mode == "incomplete":
                            value["more"] = 1
                        elif state.mode == "malformed":
                            value["threads"] = "invalid"
                    elif name == "board_read_thread":
                        assert args["markRead"] is False
                        state.reads += 1
                        if state.mode == "slow":
                            time.sleep(0.9)
                        value = state.threads[args["id"]]
                        if state.mode == "truncated":
                            value = dict(value, earlierNotShown=1)
                    elif name == "board_post":
                        assert "about" not in args
                        ident = state.add(args["body"], args["author"])
                        value = dict(created=state.threads[ident]["thread"])
                        if state.mode == "drop":
                            state.mode = ""
                            time.sleep(3.2)  # create succeeds, coordinator's complete boundary expires
                            self.connection.shutdown(socket.SHUT_RDWR)
                            self.connection.close()
                            return
                    elif name == "board_reply":
                        if state.mode == "reply_error":
                            value = dict(error=KEY)
                        else:
                            message = dict(id="m" + str(len(state.threads[args["id"]]["messages"]) + 1), body=args["body"])
                            state.threads[args["id"]]["messages"].append(message)
                            value = dict(added=message, threadId=args["id"])
                    elif name == "board_update_thread":
                        if state.mode == "close_error":
                            value = dict(error=KEY)
                        else:
                            assert args["waitingOn"] == []
                            state.threads[args["id"]]["thread"]["status"] = args["status"]
                            value = dict(updated=state.threads[args["id"]]["thread"])
                    else:
                        raise AssertionError(name)
                    result = dict(content=[dict(type="text", text=json.dumps(value))])
                raw = json.dumps(dict(jsonrpc="2.0", id=request["id"], result=result)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except OSError:
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs=dict(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def exercise(h):
    offline(h)
    prepared_offline(h)
    check = h.check

    def settings(c, endpoint):
        config = c.root / "developer.toml"
        config.write_text('[runtime]\nbackend="claude"\n[coordination]\n'
                          + f'endpoint="{endpoint}"\nperson="fixture person"\nforum="fixture"\ncredential_env="{ENV}"\n',
                          encoding="utf-8")
        return {"OURO_DEVELOPER_CONFIG": str(config), ENV: KEY}

    def run(c, extra, *args):
        return c.ouro("run", "1", *args, extra=extra)

    def records(state, ident):
        return [json.loads(co.MARK.search(m["body"])[1]) for m in state.threads[ident]["messages"] if co.MARK.search(m["body"])]

    with board() as (b, endpoint):
        c = h.happy_case("claim-first")
        extra = settings(c, endpoint)
        code, out, err = run(c, dict(extra, **{ENV: ""}))
        check("configured no-key run makes no board calls or correlation cache", code == 0 and not b.calls and not (c.main / ".git/ouro-claims").exists())

        dry = h.happy_case("claim-dry")
        code, out, err = run(dry, settings(dry, endpoint), "--dry-run")
        check("dry run makes no board/cache writes", code == 0 and not b.calls and not (dry.main / ".git/ouro-claims").exists())

        code, out, err = run(c, extra)
        first = "1"
        initial = records(b, first)[0]
        check("waiting on owner merge keeps an advisory claim with identity, branch and checked head",
              code == 0 and b.threads[first]["thread"]["status"] == "open"
              and initial["identity"]["platform"] == "claude" and initial["identity"]["actor"]
              and initial["identity"]["session"] and initial["correlation"]
              and initial["branch"] == "gates/1-fix-the-total" and len(initial["head"]) == 40
              and records(b, first)[-1]["outcome"].startswith("waiting"))

        second = h.happy_case("claim-second")
        code, out, err = run(second, settings(second, endpoint))
        check("different repository session with same display name refuses before BUILD, branch or tracker writes",
              code == 2 and "fixture person" in err and "thread 1" in err
              and not second.prompts("BUILD") and not second.wrote()
              and h.git(second.main, "branch", "--list", "gates/*") == "")
        code, out, err = run(second, settings(second, endpoint), "--override-claim")
        override = records(b, "2")[0]["override"]
        check("explicit override records authenticated actor and conflicting thread without changing merge policy",
              code == 0 and override["actor"] == initial["identity"]["actor"]
              and override["conflicts"][0]["thread"] == first and not any(s.startswith("pr merge") for s in second.sigs())
              and b.threads[first]["thread"]["status"] == "open")
        b.threads["2"]["thread"]["status"] = "closed"
        c.clear_prompts()
        code, out, err = run(c, extra)
        check("same-holder resume reuses correlation without a duplicate create or BUILD",
              code == 0 and len(b.threads) == 2 and not c.prompts("BUILD")
              and records(b, first)[-1]["correlation"] == initial["correlation"])

        conventional = b.add()
        b.mode = "incomplete"
        c.clear_prompts()
        wrote = c.wrote()
        code, out, err = run(c, extra)
        check("incomplete listing preserves an established convention-only conflict on resume",
              code == 2 and "other developer" in err and not c.prompts("BUILD") and c.wrote() == wrote)
        code, out, err = run(c, extra, "--override-claim")
        check("resume override records conflict on the owned thread, retaining the other holder",
              code == 0 and any(r.get("override", {}).get("conflicts", [{}])[0].get("thread") == conventional for r in records(b, first))
              and b.threads[conventional]["thread"]["status"] == "open")
        b.threads[conventional]["thread"]["status"] = "closed"
        b.mode = ""
        h.sh(h.sys.executable, h.STUB, "pr", "merge", "7", "--squash", "--subject", "fixture merge", "--delete-branch", env=c.env())
        code, out, err = run(c, extra)
        check("external merge reconciles and closes only the owned claim", code == 0 and b.threads[first]["thread"]["status"] == "closed"
              and records(b, first)[-1]["outcome"].startswith("landed:"))

    try:
        h.runmod.validation.check_platform()
        inline_supported = True
    except h.shmod.DriverError:
        inline_supported = False
    scenarios = ("green", "fix", "revert", "push-failed", "overlap", "override") if inline_supported else (
        "green", "push-failed", "overlap", "override")
    if not inline_supported:
        print("INFO: actual inline fix/revert publication needs admitted caller execution; "
              "offline phase controls and real ordinary-PR fallback remain exercised")
    for scenario in scenarios:
        with board() as (b, endpoint):
            table = h.run_rows("failure", "success") if scenario == "fix" else (
                h.run_rows("failure", "failure") if scenario == "revert" else {})
            files = {"gates/doc_gate.py": "open('junk.txt', 'w').write('x')\n"} if scenario == "push-failed" else {}
            fallback_binding = None
            if not inline_supported and scenario in ("green", "override"):
                # Keep the actual publisher's platform refusal and use the
                # declared ordinary CI authority; no local receipt is invented.
                fallback_binding = (h.BINDING.format(policy="stop-at-pr", review="adversarial-review")
                                    + h.LANE_GATES.replace('run = "python3 gates/doc_gate.py"\n',
                                        'run = "python3 gates/doc_gate.py"\nci = "echo another workflow runs this gate"\n'))
                fallback_binding = fallback_binding.replace('[ship]\n', '[ship]\nvalidation="ci"\nvalidation_checks=["ci"]\n')
            c = h.lane_case("claim-direct-" + scenario, table=table, files=files, binding=fallback_binding)
            if scenario in ("fix", "revert"):
                c.node("BUILD", {**h.NOTE_BUILD, "writes": {"docs/note.md": "fixed note\n"}}, 2)
            if scenario in ("overlap", "override"):
                ident = b.add('<!-- ouro-claim: ' + json.dumps(dict(
                    identity={}, correlation="foreign", scope=dict(paths=["docs/note.md"], areas=[]))) + ' -->')
                b.threads[ident]["thread"]["title"] = "Claimed: o/n#2"
            code, out, err = run(c, settings(c, endpoint), *(["--override-claim"] if scenario == "override" else []))
            owned = "2" if scenario in ("overlap", "override") else "1"
            recs = records(b, owned)
            prepared = [r for r in recs if r["outcome"] == "direct prepared"]
            if scenario == "overlap":
                check("direct overlap refuses after build but before publication",
                      code == 2 and "other developer" in err and h.git(c.origin, "rev-list", "--count", "master") == "1")
            elif scenario == "override":
                check("direct override records actor/thread and never closes foreign claim",
                      code == 0 and prepared[-1]["override"]["conflicts"][0]["thread"] == "1"
                      and b.threads["1"]["thread"]["status"] == "open")
                if not inline_supported:
                    check("unsupported inline override retains the foreign claim and waits on its ordinary PR",
                          c.pr()["head"] == "gates/1-fix-the-total"
                          and any(row["body"].startswith("**Ready for merge**") for row in c.pr_comments())
                          and h.git(c.origin, "rev-list", "--count", "master") == "1"
                          and b.threads[owned]["thread"]["status"] == "open"
                          and recs[-1]["outcome"].startswith("waiting"))
            elif scenario == "push-failed":
                check("failed direct push records no publication before fallback",
                      any(r["outcome"] == "direct push failed; nothing pushed" for r in recs)
                      and not any(r["outcome"].startswith("landed:") for r in recs))
            else:
                if not inline_supported:
                    head = h.git(c.origin, "rev-parse", "refs/heads/gates/1-fix-the-total")
                    check("unsupported inline delivery retains prepared scope and exact-head CI authority on its ordinary PR",
                          code == 0 and len(prepared) == 1 and prepared[0]["stage"] == "parent"
                          and prepared[0]["scope"]["paths"] == ["docs/note.md"]
                          and len(prepared[0]["base"]) == len(prepared[0]["head"]) == 40
                          and c.pr()["head"] == "gates/1-fix-the-total"
                          and any(row["body"].startswith("**Ready for merge**") and head in row["body"] for row in c.pr_comments())
                          and not list(c.main.rglob("ouro-validation/*/receipt.json"))
                          and h.git(c.origin, "rev-list", "--count", "master") == "1"
                          and b.threads[owned]["thread"]["status"] == "open"
                          and recs[-1]["outcome"].startswith("waiting"))
                    continue
                stages = [r["stage"] for r in prepared]
                expected = ["parent", "fix", "revert"] if scenario == "revert" else (
                    ["parent", "fix"] if scenario == "fix" else ["parent"])
                check("direct " + scenario + " retains each checked head/base and outcome",
                      code == (3 if scenario == "revert" else 0) and stages == expected
                      and all(r["scope"]["paths"] == ["docs/note.md"] and len(r["head"]) == 40
                              and len(r["base"]) == 40 for r in prepared)
                      and b.threads[owned]["thread"]["status"] == "closed"
                      and recs[-1]["outcome"].startswith("stopped:" if scenario == "revert" else "landed:"))

    for mode in ("drop", "auth", "incomplete", "malformed"):
        with board() as (b, endpoint):
            b.mode = mode
            c = h.happy_case("claim-" + mode)
            extra = settings(c, endpoint)
            code, out, err = run(c, extra)
            check(f"{mode} board evidence fails open with a bounded sanitized notice",
                  code == 0 and "Coordination unavailable" in out and KEY not in out + err and endpoint not in out + err)
            if mode != "drop":
                check(f"{mode} evidence never creates a false clear claim", not b.threads)
            if mode == "drop":
                code, out, err = run(c, extra)
                check("timed-out create response reconciles by stored correlation on resume without duplication",
                      code == 0 and len(b.threads) == 1 and len([n for n, a in b.calls if n == "board_post"]) == 1)

    for closed in ("issue", "pr"):
        with board() as (b, endpoint):
            c = h.happy_case("claim-closed-" + closed)
            extra = settings(c, endpoint)
            run(c, extra)
            state_path = c.state / ("issue-1.json" if closed == "issue" else "pr.json")
            data = c.issue_state() if closed == "issue" else c.pr()
            if data is None:
                data = h.issue()
            data["state"] = "CLOSED"
            state_path.write_text(json.dumps(data), encoding="utf-8")
            c.clear_prompts()
            wrote = c.wrote()
            code, out, err = run(c, extra)
            check(f"externally closed {closed} reconciles without another build or tracker mutation",
                  code == 0 and b.threads["1"]["thread"]["status"] == "closed"
                  and not c.prompts("BUILD") and c.wrote() == wrote
                  and "without merge" in records(b, "1")[-1]["outcome"])

    with board() as (b, endpoint):
        c = h.validation_case("claim")
        code, out, err = run(c, dict(settings(c, endpoint), VALIDATION_STATE=str(c.root)))
        check("pending caller validation retains claim at exit zero", code == 0 and b.threads["1"]["thread"]["status"] == "open"
              and not c.prompts("REVIEW") and records(b, "1")[-1]["outcome"].startswith("waiting"))

    with board() as (b, endpoint):
        c = h.happy_case("claim-ci", table={"_issue_1": h.issue(), "_runs": [["in_progress"]]})
        code, out, err = run(c, dict(settings(c, endpoint), OURO_WATCH_LIMIT="1"))
        check("pending CI retains the claim rather than treating exit zero as landed",
              code == 0 and "still running" in out and b.threads["1"]["thread"]["status"] == "open")

    with board() as (b, endpoint):
        c = h.Case("claim-stop")
        c.node("BUILD", {"stop": {"reason": "open decision", "question": "Which format?"}})
        code, out, err = run(c, settings(c, endpoint))
        check("terminal stop posts outcome and closes claim", code == 3 and b.threads["1"]["thread"]["status"] == "closed"
              and records(b, "1")[-1]["outcome"].startswith("stopped"))

    with board() as (b, endpoint):
        c = h.happy_case("claim-land", policy="merge-on-green")
        code, out, err = run(c, settings(c, endpoint))
        check("successful unattended land closes claim with actual merged SHA",
              code == 0 and b.threads["1"]["thread"]["status"] == "closed"
              and records(b, "1")[-1]["outcome"] == "landed: " + c.pr()["mergeCommit"]["oid"])

    with board() as (b, endpoint):
        c = h.happy_case("claim-cleanup")
        extra = settings(c, endpoint)
        run(c, extra)
        h.sh(h.sys.executable, h.STUB, "pr", "merge", "7", "--squash", "--subject", "fixture merge", "--delete-branch", env=c.env())
        wt = c.main / ".claude/worktrees/1-fix-the-total"
        (wt / "untracked").write_text("preserve")
        code, out, err = run(c, extra)
        check("local cleanup failure still records an observed merge accurately", code == 1
              and b.threads["1"]["thread"]["status"] == "closed" and records(b, "1")[-1]["outcome"].startswith("landed:"))

    with board() as (b, endpoint):
        c = h.happy_case("claim-close-error")
        extra = settings(c, endpoint)
        run(c, extra)
        b.mode = "close_error"
        h.sh(h.sys.executable, h.STUB, "pr", "merge", "7", "--squash", "--subject", "fixture merge", "--delete-branch", env=c.env())
        code, out, err = run(c, extra)
        check("board cleanup failure cannot fail shipping or expose response body",
              code == 0 and "Coordination unavailable" in out and KEY not in out + err and b.threads["1"]["thread"]["status"] == "open")
        b.mode = ""
        code, out, err = run(c, extra)
        check("resume retries owned claim cleanup without a duplicate post", code == 0 and b.threads["1"]["thread"]["status"] == "closed" and len(b.threads) == 1)

    parts_body = ("## Deliverable\n- [ ] 1. **First (Flow: full).** a\n"
                  "- [ ] 2. **Second (Flow: full).** b\n\n"
                  "- `src/app.py` -- compute_total -- \"def compute_total(items):\"\n\n"
                  "Doc impact on close: none\n\nSize: M\nFlow: full\n")
    with board() as (b, endpoint):
        c = h.happy_case("claim-part-close-error", table={"_issue_1": h.issue(body=parts_body)})
        extra = settings(c, endpoint)
        run(c, extra)
        b.mode = "close_error"
        h.sh(h.sys.executable, h.STUB, "pr", "merge", "7", "--squash", "--subject", "fixture merge", "--delete-branch", env=c.env())
        code, out, err = run(c, extra)
        check("failed board closure still advances a genuinely completed checklist part",
              code == 0 and "- [x] 1." in c.issue_state()["body"] and b.threads["1"]["thread"]["status"] == "open")
        # Ordinary selection now chooses part 2. A BUILD stop keeps this probe focused on
        # claim cleanup rather than the stand-in GitHub service's single-PR model.
        c.node("BUILD", {"stop": {"reason": "open decision", "question": "Fixture stop"}})
        c.clear_prompts()
        b.mode = ""
        code, out, err = run(c, extra)
        check("ordinary next-part run retries old correlated closure before BUILD without a self-conflict",
              code == 3 and b.threads["1"]["thread"]["status"] == "closed"
              and len(b.threads) == 2 and c.prompts("BUILD"))

    with board() as (b, endpoint):
        # Default-branch run discovery can lag the merge. Let the real watcher wait
        # in PowerShell; the fake gh run watch returns immediately, even for pending runs.
        c = h.happy_case("claim-part-board-outage", table={"_issue_1": h.issue(body=parts_body),
                                                         "run list *--branch master*": {"Out": []}})
        extra = settings(c, endpoint)
        run(c, extra)
        h.sh(h.sys.executable, h.STUB, "pr", "merge", "7", "--squash", "--subject", "fixture merge", "--delete-branch", env=c.env())
        b.mode = "auth"
        code, out, err = run(c, dict(extra, OURO_WATCH_LIMIT="3"))
        check("board outage with pending default checks leaves part unticked and worktree resumable",
              code == 0 and "still running" in out and "Coordination unavailable" in out
              and "- [ ] 1." in (c.issue_state() or h.issue(body=parts_body))["body"]
              and (c.main / ".claude/worktrees/1-fix-the-total-p1").exists()
              and b.threads["1"]["thread"]["status"] == "open")

    c = h.happy_case("pending-default-no-board", table={"_issue_1": h.issue(body=parts_body), "run list *--branch master*": {"Out": []}})
    c.ouro("run", "1")
    h.sh(h.sys.executable, h.STUB, "pr", "merge", "7", "--squash", "--subject", "fixture merge", "--delete-branch", env=c.env())
    code, out, err = c.ouro("run", "1", extra={"OURO_WATCH_LIMIT": "3"})
    tree = c.main / ".claude/worktrees/1-fix-the-total-p1"
    check("pending default-branch checks with no board leave the part unticked and the worktree for the resume",
          code == 0 and "still running" in out and "- [ ] 1." in (c.issue_state() or h.issue(body=parts_body))["body"] and tree.exists())
    table = json.loads((c.root / "gh-table.json").read_text(encoding="utf-8"))
    table.pop("run list *--branch master*")
    c.set_table(table)
    code, out, err = c.ouro("run", "1")
    check("the resume, once the run is green, completes once: the part ticked, the worktree removed",
          code == 0 and "runs are green" in out and c.issue_state()["body"].count("- [x] 1.") == 1 and not tree.exists())

    with board() as (b, endpoint):
        b.add("<!-- ouro-claim: " + "[" * 2000 + "0" + "]" * 2000 + " -->", "nested holder")
        c = h.happy_case("claim-nested")
        code, out, err = run(c, settings(c, endpoint))
        check("nested claim metadata refuses as a convention conflict before mutation",
              code == 2 and "nested holder" in err and "RecursionError" not in err
              and not c.prompts("BUILD") and not c.wrote())

    with board() as (b, endpoint):
        c = h.Case("claim-crash")
        c.node("BUILD", {"subject": "malformed answer", "writes": {}})
        code, out, err = run(c, settings(c, endpoint))
        check("worker failure retains claim without reporting a landed change", code == 1
              and b.threads["1"]["thread"]["status"] == "open"
              and records(b, "1")[-1]["outcome"].startswith("interrupted"))

    with board() as (b, endpoint):
        b.add()
        b.mode = "truncated"
        c = h.happy_case("claim-truncated")
        code, out, err = run(c, settings(c, endpoint))
        check("hidden initial correlation never establishes ownership", code == 2 and not c.wrote())

    with board() as (b, endpoint):
        for i in range(8):
            b.add()
        b.mode = "slow"
        c = h.happy_case("claim-budget")
        extra = settings(c, endpoint)
        started = time.monotonic()
        code, out, err = run(c, extra)
        check("known conflict survives later candidate timeouts within one boundary budget",
              code == 2 and b.reads <= 4 and time.monotonic() - started < 8 and not c.wrote())


def resume_offline(h):
    """Exercise real Git resume inputs without HTTP or worker processes."""
    config = co.Coordination("https://board.invalid/mcp", "caller", "fixture", ENV)

    def git(root, *args):
        result = h.runmod.sh.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=root)
        if result.code != 0:
            raise AssertionError(result.text)
        return result.out.strip()

    def setup(name):
        root = h.TMP / name
        root.mkdir()
        c = SimpleNamespace(root=root, main=root / "main", origin=root / "origin.git")
        git(root, "init", "-q", "--bare", "-b", "master", str(c.origin))
        git(root, "init", "-q", "-b", "master", str(c.main))
        git(c.main, "config", "user.name", "Fixture")
        git(c.main, "config", "user.email", "fixture@example.invalid")
        def write(path, text):
            target = c.main / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        c.write = write
        write(".claude/ouro.toml", '[[gate]]\npaths=["docs/"]\n')
        write("docs/note.md", "note\n")
        git(c.main, "add", "-A")
        git(c.main, "commit", "-qm", "initial")
        git(c.main, "remote", "add", "origin", str(c.origin))
        git(c.main, "push", "-q", "origin", "master")
        outcomes, builds, reverts = [], [], []
        env = SimpleNamespace(runtime=SimpleNamespace(coordination=config, backend="codex"),
                              dry=False, main=c.main, default="master", slug="o/n",
                              ob=h.runmod.sh.load_binding_module(),
                              lookup=lambda *a: "fixture", viewer=lambda: "actor",
                              body_file=lambda *a: c.root / "body.md", pr_checks=lambda: True,
                              gh_json=lambda *a: [], trusted=lambda: {"actor", "alice", "bob"},
                              run=h.runmod.sh.run)
        def git_result(*args, cwd=None, check=True):
            result = h.runmod.sh.run(["git", *args], cwd=cwd or c.main)
            if check and result.code != 0:
                raise AssertionError(result.text)
            return result
        env.git = git_result
        r = h.runmod.Run(env, 1, None, h.runmod.RunOptions())
        r.issue, r.body, r.title = dict(state="CLOSED"), "", "Edit note"
        r.flow, r.size, r.branch, r.tree = "direct", "S", "gates/1-note", h.runmod.worktrees.Tree(env, c.main, "gates/1-note")
        r.labels, r.areas = ["agent-ready"], ["gates"]
        r.read_issue = r.plan_part = r.preflight = r.check_spec_trust = lambda: None
        r.branch_exists = lambda: False
        r.find_pr = lambda *a, **kw: None
        r.label_problem = lambda: None
        r.claim.data = dict(identity={}, correlation="ours")
        r.claim.owned = "ours"
        r.claim.enter = lambda **kw: None
        def update(outcome, close=False, **kw):
            outcomes.append((json.loads(co.MARK.search(r.claim.body(outcome, kw.get("conflicts", ())))[1]), close))
            r.claim.terminal = close
        r.claim.update = update
        r.lane_revert = lambda shas, urls: reverts.append((shas, urls))
        def commit(text, stage="parent", parent=None):
            c.write("docs/note.md", text + "\n")
            record = r.lane_record(stage, parent)
            git(c.main, "commit", "-qam", text + "\n\nFixes #1" + record)
            git(c.main, "push", "-q", "origin", "master")
            return git(c.main, "rev-parse", "HEAD")
        def build(**kw):
            builds.append(kw)
            # Another publication between parent and fix cannot reset the fix allowance.
            git(c.main, "commit", "--allow-empty", "-qm", "unrelated publication")
            git(c.main, "push", "-q", "origin", "master")
            commit("fixed note", "fix", r.lane_build[1])
        r.build = build
        def ship(base, **kw):
            head = git(c.main, "rev-parse", "HEAD")
            r.lane_prepare(base, head, kw.get("stage", "parent"), check=False)
            return head
        r.lane_ship = ship
        return c, r, commit, outcomes, builds, reverts

    with patch.dict(co.os.environ, {ENV: KEY}):
        for final in ("green", "red"):
            c, r, commit, outcomes, builds, reverts = setup("resume-closed-" + final)
            head = commit("published note")
            states = {head: "running"}
            r.lane_watch = lambda sha: (states.get(sha, "running"), "run/" + sha, "failure")
            code = r.go()
            h.check("closed direct publication resumes pending checks without closing its claim",
                    code == 0 and not outcomes[-1][1] and not builds
                    and outcomes[-1][0]["head"] == head
                    and outcomes[-1][0]["scope"] == dict(paths=["docs/note.md"], areas=["gates"]))
            states[head] = final
            r.go()
            if final == "green":
                h.check("resumed green checks close only after watching the published head",
                        outcomes[-1][1] and outcomes[-1][0]["outcome"] == "landed: " + head and not builds)
            else:
                fix = git(c.main, "rev-parse", "HEAD")
                h.check("resumed red parent gets one fix and retains claim while fix checks wait",
                        len(builds) == 1 and fix != head and not outcomes[-1][1]
                        and outcomes[-1][0]["stage"] == "fix")
                states[fix] = "red"
                r.go()
                h.check("resumed red fix reverts the original pair without granting another fix",
                        len(builds) == 1 and reverts == [([fix, head], ["run/" + head, "run/" + fix])])

        for source in ("owner-merge", "owner-squash", "owner-squash-advance", "owner-squash-same-file-advance", "origin-advance"):
            for final in ("green", "red"):
                c, r, commit, outcomes, builds, reverts = setup("resume-behind-" + source + "-" + final)
                same_file = "same-file" in source
                if same_file:
                    baseline = [f"line {i}" for i in range(100)]
                    c.write("docs/note.md", "\n".join(baseline) + "\n")
                    git(c.main, "commit", "-qam", "multiline baseline")
                    git(c.main, "push", "-q", "origin", "master")
                old = git(c.main, "rev-parse", "HEAD")
                if same_file:
                    reviewed = baseline.copy()
                    reviewed[6] = "reviewed source edit"
                    head = commit("\n".join(reviewed))
                else:
                    head = commit("published note")
                if source.startswith("owner-"):
                    # A direct PR waited on the owner while the checkout stayed at old.
                    r.issue["state"] = "OPEN"
                    pr = dict(number=7, title="Edit note",
                              body=git(c.main, "log", "-1", "--format=%B", head), headRefOid=head)
                    r.env.gh_json = lambda *a: dict(headRefOid=head)
                    r.env.pr_checks = lambda: False
                    r.find_pr = lambda branch=None, **kw: pr if branch == "direct/1" else None
                    git(c.origin, "update-ref", "refs/heads/master", old)
                    git(c.main, "reset", "--hard", old)
                    git(c.main, "fetch", "-q", "origin")
                    with patch.object(h.runmod, "Run", return_value=r):
                        h.runmod.main(r.env, 1, None, r.options)
                    h.check("owner-waiting direct PR retains claim without BUILD", not builds and not outcomes[-1][1])
                    source_head = head
                    if source.endswith("-advance"):
                        if same_file:
                            upstream = baseline.copy()
                            upstream[96] = "independent upstream edit"
                            c.write("docs/note.md", "\n".join(upstream) + "\n")
                            git(c.main, "add", "docs/note.md")
                        else:
                            c.write("other.txt", "another developer\n")
                            git(c.main, "add", "other.txt")
                        git(c.main, "commit", "-qm", "unrelated advancement before squash")
                    publication_base = git(c.main, "rev-parse", "HEAD")
                    if "squash" in source:
                        git(c.main, "merge", "--squash", source_head)
                        git(c.main, "commit", "-qm", "Publish the reviewed change")
                    else:
                        git(c.main, "merge", "--no-ff", source_head, "-m", "Merge the reviewed change")
                    head = git(c.main, "rev-parse", "HEAD")
                    if same_file:
                        published = git(c.main, "show", head + ":docs/note.md").splitlines()
                        h.check("same-file squash preserves both independent edits",
                                published[6] == "reviewed source edit" and published[96] == "independent upstream edit")
                    if source.endswith("-advance"):
                        git(c.main, "commit", "--allow-empty", "-qm", "default advances after squash")
                    git(c.main, "push", "-q", "origin", "master")
                    git(c.main, "reset", "--hard", old)
                    merged_pr = dict(state="MERGED", baseRefName="master", headRefOid=source_head,
                                     mergeCommit=dict(oid=head))
                    r.env.gh_json = lambda *a: [merged_pr] if a[1] == "list" else dict(headRefOid=source_head)
                    r.issue["state"] = "CLOSED"
                    r.find_pr = lambda *a, **kw: None
                    h.check(source + " has a distinct publication and original off first-parent",
                            head != source_head and source_head not in git(c.main, "rev-list", "--first-parent", head).splitlines()
                            and "Ouro-Direct:" not in git(c.main, "log", "-1", "--format=%B", head)
                            and "Fixes #1" not in git(c.main, "log", "-1", "--format=%B", head)
                            and "Fixes #1" in pr["body"]
                            and r.lane_provenance(head) == r.lane_provenance(source_head))
                else:
                    c.write("other.txt", "another developer\n")
                    git(c.main, "add", "other.txt")
                    git(c.main, "commit", "-qm", "unrelated advancement")
                    git(c.main, "push", "-q", "origin", "master")
                    git(c.main, "reset", "--hard", head)
                    old = head
                admitted, build_heads = [], []
                origin = git(c.origin, "rev-parse", "master")
                build = r.build
                def repair(**kw):
                    build_heads.append(git(c.main, "rev-parse", "HEAD"))
                    build(**kw)
                r.build = repair
                r.claim.enter = lambda **kw: admitted.append(git(c.main, "rev-parse", "HEAD"))
                watched = []
                states = {head: "running"}
                def watch(sha):
                    watched.append(sha)
                    return states.get(sha, "running"), "run/" + sha, "failure"
                r.lane_watch = watch
                r.go()
                h.check(source + " watches the actual publication and retains the pending claim",
                        watched == [head] and not builds and not outcomes[-1][1]
                        and outcomes[-1][0]["head"] == head
                        and outcomes[-1][0]["base"] == git(c.main, "rev-parse", head + "^")
                        and outcomes[-1][0]["scope"] == dict(paths=["docs/note.md"], areas=["gates"]))
                states[head] = final
                admitted.clear()
                r.go()
                h.check(source + " behind checkout reconciles " + final + " publication",
                        admitted == [old] and (len(builds) == 1 and build_heads == [origin] if final == "red" else
                        not builds and git(c.main, "rev-parse", "HEAD") == old and outcomes[-1][1]))

                if source.startswith("owner-"):
                    for invalid in (dict(merged_pr, state="CLOSED"), dict(merged_pr, baseRefName="other"),
                                    dict(merged_pr, headRefOid=old), dict(merged_pr, mergeCommit=dict(oid=source_head))):
                        r.env.gh_json = lambda *a: [invalid] if a[1] == "list" else dict(headRefOid=source_head)
                        h.check("PR metadata without the exact Git merge relation is not publication evidence",
                                not r.lane_merged_direct())
                    r.env.gh_json = lambda *a: [merged_pr] if a[1] == "list" else dict(headRefOid=source_head)
                    if final == "red":
                        fix = git(c.main, "rev-parse", "HEAD")
                        r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
                        r.go()
                        h.check("owner merge spends exactly one fix and reverts the actual merge publication",
                                len(builds) == 1 and reverts == [([fix, head], ["run/" + head, "run/" + fix])])
                        def body_file(name, body):
                            path = c.root / name
                            path.write_text(body, encoding="utf-8")
                            return path
                        r.env.body_file = body_file
                        r.lane_revert_stop = lambda pushed, *a, **kw: pushed
                        reverted = h.runmod.Run.lane_revert(r, [fix, head], ["run/parent", "run/fix"])
                        git(c.main, "push", "-q", "origin", "master")
                        h.check("real revert of ordinary fix plus merge restores the original tree and execution",
                                git(c.main, "rev-parse", "HEAD^{tree}") == git(c.main, "rev-parse", publication_base + "^{tree}")
                                and r.lane_provenance(reverted)["execution"] == r.lane_provenance(head)["execution"])
                        h.check("historical merged PR cannot supersede its later reverted execution",
                                r.go() == reverted and len(builds) == 1)

        # A historical/reused direct branch must not lend its execution to an
        # unrelated publication, even when the final source trees are identical.
        c, r, commit, outcomes, builds, reverts = setup("resume-squash-metadata")
        base = git(c.main, "rev-parse", "HEAD")
        source_head = commit("reviewed source")
        git(c.origin, "update-ref", "refs/heads/master", base)
        git(c.main, "reset", "--hard", base)
        git(c.main, "merge", "--squash", source_head)
        git(c.main, "commit", "-qm", "Owner custom publication")
        publication = git(c.main, "rev-parse", "HEAD")
        git(c.main, "push", "-q", "origin", "master")
        metadata = dict(state="MERGED", baseRefName="master", headRefOid=source_head,
                        mergeCommit=dict(oid=publication))
        r.env.gh_json = lambda *a: [metadata]
        h.check("squash metadata identifies the exact source delta", r.lane_merged_direct() == {publication: source_head})

        git(c.main, "checkout", "-qb", "mismatched-source", base)
        c.write("docs/note.md", "a different source change\n")
        git(c.main, "commit", "-qam", "other execution" + r.lane_record("parent", None))
        mismatched = git(c.main, "rev-parse", "HEAD")
        git(c.main, "checkout", "-q", "master")
        r.env.gh_json = lambda *a: [dict(metadata, headRefOid=mismatched)]
        h.check("a provenance-bearing but mismatched source cannot authorize the squash",
                not r.lane_merged_direct())

        # Same final tree and change, but another source execution is based on
        # the publication itself: it cannot be the head that produced it.
        git(c.main, "commit", "--allow-empty", "-qm", "reused direct branch" + r.lane_record("parent", None))
        reused = git(c.main, "rev-parse", "HEAD")
        r.env.gh_json = lambda *a: [dict(metadata, headRefOid=reused)]
        h.check("equal trees on a reused direct branch do not corroborate a squash",
                git(c.main, "rev-parse", reused + "^{tree}") == git(c.main, "rev-parse", source_head + "^{tree}")
                and not r.lane_merged_direct())
        git(c.main, "revert", "--no-edit", publication)
        reverted = git(c.main, "rev-parse", "HEAD")
        git(c.main, "merge", "--squash", source_head)
        git(c.main, "commit", "-qm", "Another custom publication")
        repeated = git(c.main, "rev-parse", "HEAD")
        git(c.main, "push", "-q", "origin", "master")
        r.env.gh_json = lambda *a: [metadata, dict(metadata, mergeCommit=dict(oid=repeated))]
        h.check("one source head cannot authorize two distinct squash publications", not r.lane_merged_direct())
        r.env.gh_json = lambda *a: [dict(metadata, mergeCommit=dict(oid=reverted))]
        h.check("stale metadata for a different applied change is rejected", not r.lane_merged_direct())
        c.write("other.txt", "unrelated default change\n")
        git(c.main, "add", "other.txt")
        git(c.main, "commit", "-qm", "advance after publication")
        advance = git(c.main, "rev-parse", "HEAD")
        git(c.main, "push", "-q", "origin", "master")
        r.env.gh_json = lambda *a: [dict(metadata, mergeCommit=dict(oid=advance))]
        h.check("default ancestry alone cannot assign a stale source to a later publication",
                not r.lane_merged_direct())

        for protection in ("dirty", "nondefault", "diverged", "conflict"):
            c, r, commit, outcomes, builds, reverts = setup("resume-behind-protect-" + protection)
            old = git(c.main, "rev-parse", "HEAD")
            head = commit("published note")
            git(c.main, "reset", "--hard", old)
            if protection == "dirty":
                c.write("docs/note.md", "unknown edit\n")
            elif protection == "nondefault":
                git(c.main, "checkout", "-qb", "other-work")
            elif protection == "diverged":
                git(c.main, "commit", "--allow-empty", "-qm", "unknown local work")
            else:
                r.claim.enter = lambda **kw: (_ for _ in ()).throw(h.runmod.Refused("holder"))
            before = git(c.main, "rev-parse", "HEAD")
            status = git(c.main, "status", "--porcelain")
            r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
            r.stop_issue = lambda *a: (_ for _ in ()).throw(h.runmod.DriverError("protected"))
            try:
                r.go()
                refused = False
            except (h.runmod.DriverError, h.runmod.Refused):
                refused = True
            h.check(protection + " checkout cannot be overwritten for red publication repair",
                    refused and not builds and git(c.main, "rev-parse", "HEAD") == before
                    and git(c.main, "status", "--porcelain") == status)

        c, r, commit, outcomes, builds, reverts = setup("legacy-unpushed-configured")
        c.write("docs/note.md", "legacy note\n")
        git(c.main, "commit", "-qam", "legacy\n\nFixes #1")
        legacy = git(c.main, "rev-parse", "HEAD")
        origin = git(c.origin, "rev-parse", "master")
        pushes = []
        r.lane_ship = lambda *a, **kw: pushes.append(a)
        try:
            r.direct()
            refused = False
        except h.runmod.DriverError as ex:
            refused = "provenance" in str(ex)
        h.check("configured legacy unpushed publication refuses before BUILD or publication",
                refused and not builds and not pushes and git(c.origin, "rev-parse", "master") == origin
                and not git(c.main, "status", "--porcelain"))
        # A previously published legacy failure must also refuse before repair BUILD.
        git(c.main, "push", "-q", "origin", "master")
        r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
        try:
            r.lane_after(legacy)
            refused = False
        except h.runmod.DriverError as ex:
            refused = "provenance" in str(ex)
        h.check("configured legacy red publication refuses before repair source edits",
                refused and not builds and not pushes and not git(c.main, "status", "--porcelain"))

        for failure in ("labels-before", "labels-after", "state", "reopen-before",
                        "reopen-after", "comment-before", "comment-after"):
            c, r, commit, outcomes, builds, reverts = setup("stop-retry-" + failure)
            parent = commit("published parent")
            fix = commit("published fix", "fix", parent)
            record = r.lane_record("revert", fix)
            git(c.main, "revert", "--no-commit", fix, parent)
            git(c.main, "commit", "-qm", "Revert the direct commits for #1\n" + record)
            git(c.main, "push", "-q", "origin", "master")
            head = git(c.main, "rev-parse", "HEAD")
            initial_count = git(c.origin, "rev-list", "--count", "master")
            r.issue["comments"] = [
                dict(author=dict(login="stranger"), body="**Stop:** open decision\n\n"
                     + f"<!-- ouro-direct-stop: {head} -->"),
                dict(author=dict(login="actor"), body="**Stop:** open decision\n\n"
                     + "<!-- ouro-direct-stop: " + "0" * 40 + " -->")]
            failed = []
            def trip(point):
                if failure == point and not failed:
                    failed.append(point)
                    raise h.runmod.DriverError("fixture interrupted " + point)
            def write(label, argv):
                point = "reopen" if "reopen" in argv else "labels"
                trip(point + "-before")
                if point == "reopen":
                    r.issue["state"] = "OPEN"
                else:
                    r.labels = ["needs-ruling"]
                trip(point + "-after")
            def gh_json(*args):
                if args[1] == "list":
                    return []
                trip("state")
                return dict(state=r.issue["state"])
            def comment_issue(n, body):
                trip("comment-before")
                r.issue["comments"].append(dict(author=dict(login="actor"), body=body))
                trip("comment-after")
            r.env.write, r.env.gh_json, r.env.comment_issue = write, gh_json, comment_issue
            r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
            owned = r.claim.owned
            with patch.object(h.runmod, "Run", return_value=r):
                try:
                    h.runmod.main(r.env, 1, None, r.options)
                    interrupted = False
                except h.runmod.DriverError:
                    interrupted = True
                h.check(f"{failure}: interrupted terminal write retains the same owned claim",
                        interrupted and failed and r.claim.owned == owned
                        and not any(closed for _, closed in outcomes))
                code = h.runmod.main(r.env, 1, None, r.options)
            comments = [c for c in r.issue["comments"] if c["author"]["login"] == "actor"
                        and f"<!-- ouro-direct-stop: {head} -->" in c["body"].splitlines()]
            h.check(f"{failure}: retry completes terminal stop without BUILD, commit or revert",
                    code == 3 and not builds and not reverts and len(comments) == 1
                    and r.issue["state"] == "OPEN" and r.labels == ["needs-ruling"]
                    and r.claim.owned == owned and outcomes[-1][1]
                    and outcomes[-1][0]["outcome"] == "stopped: open decision"
                    and git(c.origin, "rev-list", "--count", "master") == initial_count
                    and git(c.main, "rev-parse", "HEAD") == head)
            # An owner's explicit label restoration follows a fully completed stop.
            r.labels = ["agent-ready"]
            r.env.viewer = lambda: "bob"  # a different authorized developer resumes
            r.claim.config = None if failure in ("labels-before", "comment-after") else config
            r.claim.terminal = False
            r.verify = r.refuse_other_pr = lambda: None
            r.direct_eligible = lambda: True
            r.direct_blocked = lambda: None
            def next_execution():
                builds.append("reauthorized")
                return 0
            r.direct = next_execution
            r.go()
            h.check(f"{failure}: completed stop permits owner-reauthorized next execution",
                    builds == ["reauthorized"])

        for enabled in (True, False):
            for evidence in ("complete", "unfinished", "unrelated", "forged", "other-execution"):
                c, r, commit, outcomes, builds, reverts = setup(f"stop-viewer-{enabled}-{evidence}")
                parent = commit("Alice parent")
                fix = commit("Alice fix", "fix", parent)
                record = r.lane_record("revert", fix)
                git(c.main, "revert", "--no-commit", fix, parent)
                git(c.main, "commit", "-qm", "Revert the direct commits for #1\n" + record)
                git(c.main, "push", "-q", "origin", "master")
                head = git(c.main, "rev-parse", "HEAD")
                r.env.viewer = lambda: "alice"
                r.issue["comments"] = []
                def write_stop(label, argv):
                    if "reopen" in argv:
                        r.issue["state"] = "OPEN"
                    else:
                        r.labels = ["needs-ruling"]
                r.env.write = write_stop
                r.env.gh_json = lambda *a: [] if a[1] == "list" else dict(state=r.issue["state"])
                r.env.comment_issue = lambda n, body: r.issue["comments"].append(
                    dict(author=dict(login=r.env.viewer()), body=body))
                r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
                try:
                    r.lane_revert_stop(head, fix, ["run/parent", "run/fix"], parent)
                except h.runmod.Stopped:
                    pass
                h.check("Alice's real terminal transition has provenance-bound completion",
                        r.lane_stop_complete(head) and r.issue["state"] == "OPEN")
                comment = r.issue["comments"][0]
                if evidence == "unfinished":
                    r.issue["comments"] = []
                elif evidence == "unrelated":
                    comment["body"] = "**Stop:** open decision\n\n" + f"<!-- ouro-direct-stop: {head} -->"
                elif evidence == "forged":
                    comment["author"]["login"] = "stranger"
                elif evidence == "other-execution":
                    comment["body"] = comment["body"].replace(fix[:7], "0" * 7)
                r.env.viewer = lambda: "bob"
                r.claim.config = config if enabled else None
                r.labels = ["agent-ready"]
                r.claim.terminal = False
                r.verify = r.refuse_other_pr = lambda: None
                r.direct_eligible = lambda: True
                r.direct_blocked = lambda: None
                r.direct = lambda: builds.append("Bob execution") or 0
                try:
                    code = r.go()
                except h.runmod.Stopped as stop:
                    code = stop.args[0]
                h.check(f"{enabled}/{evidence}: only Alice's authentic completed stop permits Bob's new execution",
                        (code == 0 and builds == ["Bob execution"] and r.labels == ["agent-ready"])
                        if evidence == "complete" else
                        (code == 3 and not builds and r.labels == ["needs-ruling"] and outcomes[-1][1]))

        for earlier in (1, 3):
            c, r, commit, outcomes, builds, reverts = setup("resume-reopened-" + str(earlier))
            for i in range(earlier):
                commit("successful earlier execution " + str(i))
            head = commit("current reopened execution")
            states = {head: "running"}
            r.lane_watch = lambda sha: (states.get(sha, "green"), "run/" + sha, "failure")
            r.go()
            states[head] = "red"
            r.go()
            fix = git(c.main, "rev-parse", "HEAD")
            states[fix] = "red"
            r.go()
            h.check("reopened execution has its own one-fix allowance after " + str(earlier) + " successful runs",
                    len(builds) == 1 and reverts == [([fix, head], ["run/" + head, "run/" + fix])]
                    and r.lane_provenance(fix)["execution"] == r.lane_provenance(head)["execution"])

        c, r, commit, outcomes, builds, reverts = setup("resume-legacy-ambiguous")
        c.write("docs/note.md", "legacy publication\n")
        git(c.main, "commit", "-qam", "legacy\n\nFixes #1")
        git(c.main, "push", "-q", "origin", "master")
        try:
            r.go()
            refused = False
        except h.runmod.DriverError as ex:
            refused = "provenance" in str(ex)
        h.check("ambiguous legacy evidence refuses before claim or tracker writes",
                refused and not outcomes and not builds)

        c, r, commit, outcomes, builds, reverts = setup("resume-closed-external")
        code = r.go()
        h.check("a closed issue without a Git publication still reconciles as an external closure",
                code == 0 and outcomes[-1][1]
                and outcomes[-1][0]["outcome"] == "closed externally without merge" and not builds)

        c, r, commit, outcomes, builds, reverts = setup("resume-refused-fix")
        parent = commit("published parent")
        modeled_floors, pushes, legacy_gates = [], [], []
        r.options.override_claim = False
        def build_fix(**kw):
            builds.append(kw)
            c.write("docs/note.md", "unpublished fix\n")
            git(c.main, "commit", "-qam", "fix note\n\nFixes #1" + r.lane_record("fix", parent))
        r.build = build_fix
        r.build_facts = lambda **kw: dict(validation_pending=[])
        r.run_gates = lambda head: legacy_gates.append(head)
        r.lane_ship = h.runmod.Run.lane_ship.__get__(r)
        conflict = dict(thread="foreign", holder="other developer")
        r.claim.discover = lambda *a: (["ours"], [conflict], False)
        def run_command(argv, **kw):
            if any("Push-Direct.ps1" in str(a) for a in argv):
                head = git(c.main, "rev-parse", "HEAD")
                prepared = [data for data, _ in outcomes if data["outcome"] == "direct prepared"]
                h.check("prepared override and pinned head precede the modeled publisher floor",
                        argv[argv.index("-ExpectedHead") + 1] == head and prepared
                        and prepared[-1]["head"] == head and prepared[-1]["base"] == parent
                        and prepared[-1]["stage"] == "fix"
                        and prepared[-1]["override"] == dict(actor="actor", conflicts=[conflict]))
                # This offline boundary models a completed public floor; real
                # command execution is established by the public CLI fixtures.
                modeled_floors.append(head)
                pushes.append(head)
                git(c.main, "push", "-q", "origin", f"{head}:refs/heads/master")
                return SimpleNamespace(code=0, out="vehicle: push\n")
            raise AssertionError("unexpected modeled publisher command")
        r.env.run = run_command
        r.lane_watch = lambda sha: ("red" if sha == parent else "running", "run/" + sha, "failure")
        for attempt in range(2):
            try:
                r.go()
                refused = False
            except h.runmod.Refused:
                refused = True
            fix = git(c.main, "rev-parse", "HEAD")
            h.check("scope-refused retries retain one committed fix without publisher, floor or extra BUILD",
                    refused and len(builds) == 1 and not modeled_floors and not legacy_gates
                    and git(c.origin, "rev-parse", "master") == parent
                    and git(c.main, "rev-list", "--count", f"{parent}..{fix}") == "1"
                    and not pushes and not git(c.main, "status", "--porcelain"))
        r.options.override_claim = True  # explicit caller override on the next invocation
        r.claim.prepared = None
        code = r.go()
        prepared = [data for data, _ in outcomes if data["outcome"] == "direct prepared"]
        h.check("override resume prepares the existing fix before one modeled floor and actual Git push",
                code == 0 and len(builds) == 1 and modeled_floors == [fix] and not legacy_gates and pushes == [fix]
                and git(c.origin, "rev-parse", "master") == fix
                and prepared[-1]["head"] == fix and prepared[-1]["base"] == parent
                and prepared[-1]["stage"] == "fix"
                and prepared[-1]["override"] == dict(actor="actor", conflicts=[conflict])
                and not outcomes[-1][1])
        r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
        r.go()
        h.check("retried fix still spends the one-fix allowance when published checks turn red",
                len(builds) == 1 and modeled_floors == [fix] and pushes == [fix]
                and reverts == [([fix, parent], ["run/" + parent, "run/" + fix])])

        for foreign_path in ("docs/other.md", "docs/note.md"):
            c, r, commit, outcomes, builds, reverts = setup("repair-delta-" + foreign_path.split("/")[-1])
            c.write(".claude/ouro.toml", '[ship]\ndirect=["docs/*.md"]\n'
                    '[[gate]]\npaths=["docs/note.md"]\nrun="note gate"\n'
                    '[[gate]]\npaths=["docs/other.md"]\nrun="other gate"\n')
            parent = commit("published parent")
            c.write("docs/other.md", "unrelated upstream edit\n")
            git(c.main, "add", "docs/other.md")
            git(c.main, "commit", "-qm", "unrelated upstream publication")
            git(c.main, "push", "-q", "origin", "master")
            upstream = git(c.main, "rev-parse", "HEAD")
            git(c.main, "reset", "--hard", parent)
            modeled_floors, pushes, legacy_gates = [], [], []
            r.options.override_claim = False
            r.env.ob.plugin_root = lambda: None
            r.env.ob.OURO_TOKEN = "<ouro>"
            def build_fix(**kw):
                builds.append(git(c.main, "rev-parse", "HEAD"))
                c.write("docs/note.md", "repair only\n")
                git(c.main, "commit", "-qam", "fix note\n\nFixes #1" + r.lane_build_record)
            r.build = build_fix
            r.build_facts = lambda **kw: dict(validation_pending=[])
            r.run_gates = lambda head: legacy_gates.append(head)
            r.lane_ship = h.runmod.Run.lane_ship.__get__(r)
            def run_command(argv, **kw):
                if any("Push-Direct.ps1" in str(a) for a in argv):
                    head = git(c.main, "rev-parse", "HEAD")
                    prepared = [data for data, _ in outcomes if data["outcome"] == "direct prepared"]
                    h.check("repair scope is prepared before the modeled publisher sees its exact head",
                            argv[argv.index("-ExpectedHead") + 1] == head and prepared
                            and prepared[-1]["head"] == head and prepared[-1]["base"] == upstream
                            and prepared[-1]["stage"] == "fix")
                    chosen, skipped = r.select_gates("direct")
                    modeled_floors.append((head, [cmd for cmd, _ in chosen], skipped))
                    pushes.append(head)
                    git(c.main, "push", "-q", "origin", f"{head}:refs/heads/master")
                    return SimpleNamespace(code=0, out="vehicle: push\n")
                return h.runmod.sh.run(argv, **kw)  # real direct-path policy check
            r.env.run = run_command
            def call(name, args, deadline):
                if name == "board_list_threads":
                    return dict(threads=[dict(id=i, title="Claimed: o/n#" + n,
                                             forum="fixture", status="open")
                                         for i, n in (("ours", "1"), ("foreign", "2"))])
                if name == "board_read_thread":
                    ours = args["id"] == "ours"
                    data = r.claim.data if ours else dict(identity={}, correlation="foreign",
                                                         scope=dict(paths=[foreign_path], areas=[]))
                    return dict(thread=dict(id=args["id"], title="Claimed: o/n#" + ("1" if ours else "2"),
                                            forum="fixture", status="open", createdBy="other holder"),
                                messages=[dict(body="<!-- ouro-claim: " + json.dumps(data) + " -->")])
                raise AssertionError(name)
            r.claim.call = call
            r.lane_watch = lambda sha: ("red" if sha == parent else "running", "run/" + sha, "failure")
            for attempt in range(2):
                try:
                    r.lane_after(parent)
                    refused = False
                except h.runmod.Refused as ex:
                    refused = "other holder" in str(ex)
                fix = git(c.main, "rev-parse", "HEAD")
                h.check("P -> U -> F prepares only repair scope and reaches a modeled floor only when admitted on " + foreign_path,
                        refused == (foreign_path == "docs/note.md") and builds == [upstream]
                        and modeled_floors == ([] if refused else [(fix, ["note gate"], ["other gate"])])
                        and not legacy_gates
                        and r.claim.prepared == dict(base=upstream, head=fix, stage="fix",
                                                    scope=dict(paths=["docs/note.md"], areas=["gates"]))
                        and r.lane_provenance(fix)["parent"] == parent
                        and r.lane_provenance(fix)["execution"] == r.lane_provenance(parent)["execution"]
                        and r.lane_provenance(fix)["base"] == upstream
                        and git(c.main, "rev-parse", fix + "^") == upstream
                        and pushes == ([] if refused else [fix])
                        and git(c.origin, "rev-parse", "master") == (upstream if refused else fix))
                if not refused:
                    break
            h.check("refused repair retries spend neither another BUILD nor modeled publisher floor",
                    len(modeled_floors) == (0 if refused else 1) and builds == [upstream])

        c, r, commit, outcomes, builds, reverts = setup("resume-refused-revert")
        parent = commit("published parent")
        fix = commit("published fix", "fix", parent)
        r.lane_revert = h.runmod.Run.lane_revert.__get__(r)
        def body_file(name, body):
            path = c.root / name
            path.write_text(body, encoding="utf-8")
            return path
        r.env.body_file = body_file
        r.build_facts = lambda **kw: dict(validation_pending=[])
        r.lane_ship = h.runmod.Run.lane_ship.__get__(r)
        r.lane_watch = lambda sha: ("red", "run/" + sha, "failure")
        r.options.override_claim = False
        conflict = dict(thread="foreign", holder="other developer")
        checks, pushes, transitions = [], [], []
        r.claim.discover = lambda *a: ([r.claim.owned], [conflict], False)
        original_prepare = r.lane_prepare
        def prepare(*a, **kw):
            if kw.get("check", True):
                checks.append(a)
            return original_prepare(*a, **kw)
        r.lane_prepare = prepare
        def run_command(argv, **kw):
            if any("Push-Direct.ps1" in str(a) for a in argv):
                pushes.append(git(c.main, "rev-parse", "HEAD"))
                git(c.main, "push", "-q", "origin", "master")
                return SimpleNamespace(code=0, out="vehicle: push\n")
            return SimpleNamespace(code=0)
        r.env.run = run_command
        r.env.gh_json = lambda *a: [] if a[1] == "list" else dict(state="CLOSED")
        r.env.write = lambda label, argv: transitions.append((label, argv))
        r.env.comment_issue = lambda n, body: transitions.append(body)
        for attempt in range(2):
            try:
                r.go()
                refused = False
            except h.runmod.Refused:
                refused = True
            head = git(c.main, "rev-parse", "HEAD")
            h.check("prepared revert refusal/retry reruns actual scope without another revert or BUILD",
                    refused and not builds and not pushes and not transitions
                    and len(checks) == attempt + 1 and checks[-1] == (fix, head, "revert")
                    and git(c.main, "rev-list", "--count", fix + "..HEAD") == "1"
                    and not any(closed for _, closed in outcomes))
        git(c.main, "commit", "--allow-empty", "-qm", "foreign commit")
        h.check("foreign commit alongside prepared revert is refused", not r.lane_commits_ahead())
        git(c.main, "reset", "-q", "--hard", head)  # scratch fixture only
        r.options.override_claim = True
        with patch.object(h.runmod, "Run", return_value=r):
            code = h.runmod.main(r.env, 1, None, r.options)
        h.check("explicit override publishes only the prepared revert and reaches terminal stop",
                code == 3 and pushes == [head] and len(checks) == 3 and not builds
                and git(c.origin, "rev-parse", "master") == head
                and "needs-ruling" in transitions[0][1] and transitions[1][0] == "reopen #1" and outcomes[-1][1]
                and git(c.main, "rev-list", "--count", fix + "..HEAD") == "1")


def prepared_offline(h):
    """Scope decisions run without sockets; the full-driver rows exercise publication."""
    from ouro.sh import Refused
    config = co.Coordination("https://board.invalid/mcp", "caller", "fixture", ENV)
    env = SimpleNamespace(runtime=SimpleNamespace(coordination=config, backend="codex"),
                          dry=False, slug="o/n", viewer=lambda: "actor",
                          main=h.TMP, git=lambda *a, **kw: SimpleNamespace(out="a" * 40))
    run = SimpleNamespace(env=env, n=1, tree=SimpleNamespace(path=h.TMP), branch="master", areas=[], options=h.runmod.RunOptions())
    claim = co.Claim(run)
    claim.data = dict(identity=dict(session="local-session"), correlation="ours")
    records, deadlines = [], []
    more, foreign_scope = [0], [dict(paths=["docs/café.md"], areas=["docs"])]
    suffix = []
    def call(name, args, deadline):
        deadlines.append(deadline)
        if name == "board_list_threads":
            return dict(threads=[dict(id=i, title="Claimed: o/n#" + n, forum="fixture", status="open")
                                 for i, n in (("ours", "1"), ("foreign", "2"))], more=more[0])
        if name == "board_read_thread":
            ours = args["id"] == "ours"
            data = claim.data if ours else dict(identity={}, correlation="theirs", scope=foreign_scope[0])
            return dict(thread=dict(id=args["id"], title="Claimed: o/n#" + ("1" if ours else "2"),
                                    forum="fixture", status="open", createdBy="holder"),
                        messages=[dict(body="<!-- ouro-claim: " + json.dumps(data) + " -->")] + ([] if ours else suffix))
        if name == "board_reply":
            records.append(json.loads(co.MARK.search(args["body"])[1]))
            return dict(threadId=args["id"], added=dict(body=args["body"]))
        raise AssertionError(name)
    with patch.dict(co.os.environ, {ENV: KEY}), patch.object(claim, "call", side_effect=call):
        for scope, conflict in ((dict(paths=["docs/café.md"], areas=[]), True),
                                (dict(paths=["other"], areas=["docs"]), True),
                                (dict(paths=["other"], areas=["src"]), False)):
            try:
                claim.prepare("a"*40, "b"*40, **scope, stage="parent")
                refused = False
            except Refused as ex:
                refused = "holder" in str(ex)
            h.check("prepared exact path/area overlap or disjoint scope " + str(scope), refused == conflict)
        h.check("prepared boundary shares one deadline across discovery and writes",
                len(set(deadlines[-5:])) == 1 and records[-1]["head"] == "b"*40)
        more[0] = 1
        try:
            claim.prepare("a"*40, "b"*40, ["docs/café.md"], [], "fix")
            refused = False
        except Refused:
            refused = True
        h.check("prepared incomplete listing preserves known overlap", refused)
        suffix.append(dict(body="<!-- ouro-claim: invalid json -->"))
        try:
            claim.prepare("a"*40, "b"*40, ["docs/café.md"], [], "fix")
            refused = False
        except Refused:
            refused = True
        h.check("prepared malformed later metadata preserves known overlap", refused)
        suffix.clear()
        run.options.override_claim = True
        claim.prepare("a"*40, "c"*40, ["docs/café.md"], [], "revert")
        h.check("prepared explicit override records actor and foreign thread",
                records[-1]["override"] == dict(actor="actor", conflicts=[dict(thread="foreign", holder="holder")])
                and records[-1]["stage"] == "revert")
        run.options.override_claim = False
        foreign_scope[0] = None
        claim.prepare("a"*40, "d"*40, ["other"], [], "parent")
        h.check("prepared unknown scope fails open without claiming complete evidence", claim.noticed)
        before = len(deadlines)
        with patch.dict(co.os.environ, {ENV: ""}):
            claim.prepare("a"*40, "e"*40, ["other"], [], "parent")
        env.dry = True
        claim.prepare("a"*40, "e"*40, ["other"], [], "parent")
        h.check("prepared missing key and dry run make no board calls", len(deadlines) == before)


def offline(h):
    """Exercise ownership and fail-open decisions even on hosts that deny loopback sockets."""
    from ouro.sh import Refused

    # Pending default checks must not tick a checklist part and strand its open claim.
    pending = object.__new__(h.runmod.Run)
    pending.env = SimpleNamespace(run=lambda *a, **k: h.runmod.sh.run(*a, **k), dry=False, main=h.TMP, git=lambda *a, **kw: None)
    pending.flow, pending.n = "full", 1
    pending.claim = SimpleNamespace(config=SimpleNamespace(credential_env=ENV), owned="owned-thread",
                                    update=lambda *a, **kw: outcomes.append((a, kw)))
    pending.branch_exists = lambda: (_ for _ in ()).throw(AssertionError("premature cleanup"))
    pending.tick_part = lambda: (_ for _ in ()).throw(AssertionError("premature part tick"))
    outcomes = []
    with patch.dict(co.os.environ, {ENV: KEY}), patch.object(h.runmod.sh, "run", return_value=SimpleNamespace(code=None)):
        code = pending.after_merge(dict(number=7, mergeCommit=dict(oid="b" * 40)))
    h.check("offline: pending default checks retain the current part and its claim before cleanup",
            code == 0 and outcomes == [((f"merged: {'b' * 40}; waiting on default-branch checks",), {})])
    pending.claim.owned = None
    outcomes.clear()
    with patch.dict(co.os.environ, {ENV: KEY}), patch.object(h.runmod.sh, "run", return_value=SimpleNamespace(code=None)):
        code = pending.after_merge(dict(number=7, mergeCommit=dict(oid="b" * 40)))
    h.check("offline: unavailable board discovery cannot advance a part with pending default checks",
            code == 0 and outcomes == [((f"merged: {'b' * 40}; waiting on default-branch checks",), {})])

    root = h.TMP / "claim-offline"
    root.mkdir()
    config = co.Coordination("https://board.invalid/mcp", "same person", "fixture", ENV)
    runtime = SimpleNamespace(coordination=config, backend="codex")
    env = SimpleNamespace(runtime=runtime, dry=False, slug="o/n", main=root,
                          viewer=lambda: "github-actor",
                          git=lambda *args, **kwargs: SimpleNamespace(out=str(root) if "--git-common-dir" in args else "a" * 40))
    run = SimpleNamespace(env=env, n=1, k=None, branch="area/change", tree=SimpleNamespace(path=root / "absent"),
                          options=h.runmod.RunOptions())
    requests, threads, deadlines = [], {}, []

    def call(name, args, deadline=None):
        requests.append((name, args))
        deadlines.append(deadline)
        if name == "board_list_threads":
            value = dict(threads=[t["thread"] for t in threads.values() if t["thread"]["status"] == "open"], more=more[0])
        elif name == "board_read_thread":
            value = threads[args["id"]]
        elif name == "board_post":
            ident = str(len(threads) + 1)
            summary = dict(id=ident, title=args["title"], forum=args["forum"], status="open", createdBy=args["author"])
            threads[ident] = dict(thread=summary, messages=[dict(body=args["body"])])
            value = dict(created=summary)
        elif name == "board_reply":
            message = dict(body=args["body"])
            threads[args["id"]]["messages"].append(message)
            value = dict(added=message, threadId=args["id"])
        elif name == "board_update_thread":
            threads[args["id"]]["thread"]["status"] = args["status"]
            value = dict(updated=threads[args["id"]]["thread"])
        else:
            raise AssertionError(name)
        return co.Reply(dict(content=[dict(type="text", text=json.dumps(value))]))

    more = [0]
    with patch.dict(co.os.environ, {ENV: KEY}), patch.object(co.Board, "call", side_effect=call):
        first = co.Claim(run)
        first.enter()
        h.check("offline: one complete enter shares an absolute budget and records actual platform",
                first.owned == "1" and len(set(deadlines)) == 1
                and first.data["identity"]["platform"] == "codex")
        resumed = co.Claim(run)
        resumed.enter()
        h.check("offline: matching session/correlation resumes an owned thread",
                resumed.owned == "1" and len(threads) == 1 and first.data["correlation"] == resumed.data["correlation"])
        threads["other"] = dict(thread=dict(id="other", title="Claimed: o/n#1", forum="fixture", status="open", createdBy="same person"),
                                messages=[dict(body="Convention claim")])
        more[0] = 1
        try:
            co.Claim(run).enter()
            refused = False
        except Refused as ex:
            refused = "same person" in str(ex) and "other" in str(ex)
        h.check("offline: incomplete discovery preserves a known conflict; display name proves no ownership", refused)
        run.options.override_claim = True
        overriding = co.Claim(run)
        overriding.enter()
        overrides = [a for n, a in requests if n == "board_reply" and '"override"' in a["body"]]
        h.check("offline: explicit override records actor and conflicting thread on owned claim",
                overriding.owned == "1" and overrides and '"github-actor"' in overrides[-1]["body"] and '"other"' in overrides[-1]["body"])
        # A completed part may advance despite failed board closure. Its cached correlation
        # must still distinguish that claim from a different holder on an ordinary next-part run.
        threads["other"]["thread"]["status"] = "closed"
        run.options.override_claim = False
        run.k = 1
        run.body = "## Deliverable\n- [x] 1. **First (Flow: full).** a\n- [ ] 2. **Second (Flow: full).** b\n"
        more[0] = 0
        part1 = co.Claim(run)
        # Hide the non-part fixture claim while creating the first checklist claim.
        threads["1"]["thread"]["status"] = "closed"
        part1.enter()
        part_thread = part1.owned
        run.k = 2
        initial_body = threads[part_thread]["messages"][0]["body"]
        threads[part_thread]["messages"][0]["body"] = initial_body.replace(part1.data["correlation"], "f" * 32)
        before = len(requests)
        try:
            co.Claim(run).enter()
            refused = False
        except Refused:
            refused = True
        h.check("offline: a completed part with a different correlation remains a conflict without writes",
                refused and all(n in ("board_list_threads", "board_read_thread") for n, _ in requests[before:]))
        threads[part_thread]["messages"][0]["body"] = initial_body
        original_call = call

        def failing_close(name, args, deadline=None):
            if name == "board_update_thread":
                raise co.Unavailable("close failed")
            return original_call(name, args, deadline)

        with patch.object(co.Board, "call", side_effect=failing_close):
            next_part = co.Claim(run)
            next_part.enter()
        h.check("offline: failed prior-part closure fails open without treating the cached holder as foreign",
                next_part.owned and threads[part_thread]["thread"]["status"] == "open")
        retried = co.Claim(run)
        retried.enter()
        h.check("offline: ordinary next-part resume closes the prior correlated claim and reuses the new one",
                threads[part_thread]["thread"]["status"] == "closed" and retried.owned == next_part.owned)
        retried.update("landed: " + "b" * 40, close=True)
        run.k = None
        threads["1"]["thread"]["status"] = "open"
        threads["other"]["thread"]["status"] = "open"
        overriding.update("landed: " + "b" * 40, close=True)
        overriding.update("waiting")
        h.check("offline: a terminal close records actual outcome and never closes someone else's claim",
                threads["1"]["thread"]["status"] == "closed" and threads["other"]["thread"]["status"] == "open"
                and "landed:" in threads["1"]["messages"][-1]["body"])
        threads.clear()
        more[0] = 1
        co.Claim(run).enter()
        h.check("offline: incomplete discovery alone cannot create a claim", not threads)
        more[0] = 0
        run.options.override_claim = False
        threads["nested"] = dict(thread=dict(id="nested", title="Claimed: o/n#1", forum="fixture",
                                            status="open", createdBy="nested holder"),
                                 messages=[dict(body="<!-- ouro-claim: " + "[" * 2000 + "0" + "]" * 2000 + " -->")])
        try:
            co.Claim(run).enter()
            refused = False
        except Refused as ex:
            refused = "nested holder" in str(ex)
        h.check("offline: deeply nested metadata is a convention claim, never a driver crash", refused)
        env.dry = True
        before = len(requests)
        co.Claim(run).enter()
        h.check("offline: dry run makes no board calls", len(requests) == before)
        try:
            co.payload(co.Reply(dict(content=[dict(type="text", text='{"error":"untrusted"}')])))
            malformed = False
        except co.Unavailable:
            malformed = True
        h.check("offline: JSON tool errors never become empty discovery", malformed)
