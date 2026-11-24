"""Execution evidence across the real driver loop with modeled workers and GitHub."""
import hashlib
import json
import pathlib
import types

from ouro import developer


def exercise(h):
    try:
        h.runmod.validation.check_platform()
        caller_supported = True
    except h.runmod.DriverError:
        caller_supported = False

    def no_caller_receipt(c):
        worktree = pathlib.Path(c.node_call("BUILD")["cwd"])
        private = pathlib.Path(h.git(worktree, "rev-parse", "--absolute-git-dir"))
        return not list((private / "ouro-validation").glob("*/receipt.json"))

    command = "python3 gates/check.py"
    second = "python3 gates/other.py"
    binding = h.BINDING.format(policy="stop-at-pr", review="adversarial-review").replace(
        'ci = "echo SKIP: the checks cannot run this gate"',
        'ci = "echo another workflow runs this gate"\ncheap = true')
    body = h.ISSUE_BODY + "\n## Acceptance\n`" + second + "` passes.\n"
    def case(name, answer):
        c = h.happy_case("execution-" + name, binding=binding, files={"gates/other.py": "print('other passed')\n"},
                         table={"_issue_1": h.issue(body=body)})
        if name == "forged":
            c.write(".claude/ouro.toml", binding.replace('policy = "stop-at-pr"', 'policy = "merge-on-green"'))
            h.git(c.main, "add", ".claude/ouro.toml")
            h.git(c.main, "commit", "-q", "-m", "merge policy fixture")
            h.git(c.main, "push", "-q", "origin", "master")
        c.node("BUILD", {**h.BUILD, **answer})
        return c
    def restore_ready(c):
        # Model the owner's explicit re-admission after a stop; keep counters and evidence.
        path = c.state / "issue-1.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["labels"] = [{"name": name} for name in ("agent-ready", "gates", "bug")]
        path.write_text(json.dumps(state), encoding="utf-8")

    def ready(c):
        return any(x["body"].startswith("**Ready for merge**") for x in c.pr_comments())
    successes = {"_run": [command, second]}
    for name, answer in (
            ("missing", {}),
            ("forged", {"gates_run": [{"cmd": cmd, "exit": 0} for cmd in (command, second)]}),
            ("partial", {"_run": [command]}),
            ("drop-cheap", {"_run": [second], "writes": {**h.BUILD["writes"],
                ".claude/ouro.toml": binding.replace("cheap = true", "cheap = false")}}),
            ("failed", {"_run": [command, second], "writes": {
                **h.BUILD["writes"], "gates/other.py": "raise SystemExit(9)\n"}}),
            ("edit-after", {**successes, "_after_run": {"src/app.py": "changed after success\n"}}),
            ("new-file-after", {**successes, "_after_run": {"later.txt": "new input\n"}})):
        c = case(name, answer)
        code, out, err = c.ouro("run", "1", "--validate")
        if not caller_supported:
            h.check(name + ": unsupported proof never reaches Ready or spends another BUILD",
                    code == 4 and "waiting for validation" in out and not ready(c)
                    and not c.prompts("REVIEW") and len(c.prompts("BUILD")) == 1
                    and no_caller_receipt(c) and not any(s.startswith("pr merge") for s in c.sigs()))
            continue
        if name == "new-file-after":
            h.check("a new input after execution requires a fresh run on the enlarged input set",
                    code == 0 and ready(c) and len(c.prompts("BUILD")) == 2
                    and "Missing successful execution evidence" in out)
            continue
        h.check(name + " cannot reach review, Ready or merge; missing proof spends bounded fixes",
                code == 3 and "Missing successful execution evidence" in out and not ready(c)
                and not c.prompts("REVIEW") and not any(s.startswith("pr merge") for s in c.sigs())
                and any(h.stop_comment_ok(s, "review cap") for s in h.stops_of(c)))

    if not caller_supported:
        for name, answer in (("candidate", successes), ("pending", {"validation_pending": [
                {"cmd": cmd, "reason": "worker executable unavailable"} for cmd in (command, second)]})):
            c = case("unsupported-" + name, answer)
            c.ouro("run", "1")
            code, out, err = c.ouro("run", "1", "--validate")
            h.check(name + ": actual unsupported validation waits without proof, review or another BUILD",
                    code == 4 and "waiting for validation" in out and not ready(c)
                    and not c.prompts("REVIEW") and len(c.prompts("BUILD")) == 1
                    and no_caller_receipt(c) and not any(s.startswith("pr merge") for s in c.sigs()))
        c = h.happy_case("execution-unsupported-driver-mutation", files={
            "gates/check.py": "from pathlib import Path\nPath('src/app.py').write_text('changed by gate')\n",
            "gates/other.py": "print('other passed')\n"}, table={"_issue_1": h.issue(body=body)})
        c.node("BUILD", {**h.BUILD, "validation_pending": [{"cmd": second, "reason": "worker unavailable"}]})
        c.ouro("run", "1")
        code, out, err = c.ouro("run", "1", "--validate")
        worktree = pathlib.Path(c.node_call("BUILD")["cwd"])
        h.check("unsupported caller validation never reaches the mutating driver gate",
                code == 4 and "waiting for validation" in out and not ready(c)
                and not c.prompts("REVIEW") and len(c.prompts("BUILD")) == 1 and no_caller_receipt(c)
                and "changed by gate" not in (worktree / "src/app.py").read_text(encoding="utf-8"))
        print("SKIP: supported caller proof controls; actual unsupported refusal controls above remain required")
        return

    for fails in (False, True):
        c = case("resumed-unobserved-" + str(fails), {})
        if fails:
            c.write("gates/other.py", "raise SystemExit(9)\n")
            h.git(c.main, "add", "gates/other.py")
            h.git(c.main, "commit", "-qm", "failing acceptance fixture")
            h.git(c.main, "push", "-q", "origin", "master")
        code, out, err = c.ouro("run", "1")
        wt = pathlib.Path(c.node_call("BUILD")["cwd"])
        head = h.git(wt, "rev-parse", "HEAD")
        h.check("unobserved acceptance without consent never executes caller command",
                code == 3 and not ready(c) and no_caller_receipt(c)
                and "Missing successful execution evidence" in out)
        c.clear_prompts()
        code, out, err = c.ouro("run", "1", "--validate")
        h.check("explicit caller consent cannot resume an owner stop",
                code == 3 and "not agent-ready" in out and not c.prompts("BUILD")
                and h.git(wt, "rev-parse", "HEAD") == head)
        restore_ready(c)
        c.clear_prompts()
        code, out, err = c.ouro("run", "1", "--validate")
        h.check("resumed explicit caller proof preserves head and spent fixes",
                h.git(wt, "rev-parse", "HEAD") == head and not c.prompts("BUILD")
                and ((fails and code == 3 and "exit 9" in out and not ready(c))
                     or (not fails and code == 0 and ready(c))))
        if not fails:
            receipts = [json.loads(p.read_text(encoding="utf-8")) for p in h.validation_receipts(c)]
            h.check("missing candidate obtains authentic exact-head acceptance evidence",
                    any(r["sha"] == head and second in r["commands"] for r in receipts))
            h.git(wt, "commit", "--allow-empty", "-qm", "stale caller proof")
            newer = h.git(wt, "rev-parse", "HEAD")
            code, out, err = c.ouro("run", "1", "--validate")
            receipts = [json.loads(p.read_text(encoding="utf-8")) for p in h.validation_receipts(c)]
            h.check("resumed caller path executes again rather than accepting stale-head proof",
                    code == 0 and any(r["sha"] == newer and second in r["commands"] for r in receipts)
                    and not c.prompts("BUILD"))

    # A missing candidate is different from an explicit unsuccessful worker report.
    # The command itself passes, so a widened resume would incorrectly reach review.
    for name, report in (("failed", {"exit": 9}), ("noninteger", {"exit": "0"}),
                         ("boolean", {"exit": False}), ("missing-exit", {})):
        c = case("resumed-report-" + name, {"gates_run": [{"cmd": second, **report}]})
        code, out, err = c.ouro("run", "1")
        wt = pathlib.Path(c.node_call("BUILD")["cwd"])
        head = h.git(wt, "rev-parse", "HEAD")
        h.check(name + " worker report reaches the original bounded finding",
                code == 3 and "Missing successful execution evidence" in out)
        restore_ready(c)
        c.clear_prompts()
        code, out, err = c.ouro("run", "1", "--validate")
        h.check(name + " worker report cannot be bypassed by resumed caller consent",
                code == 3 and "Missing successful execution evidence" in out
                and not ready(c) and not c.prompts("BUILD") and not c.prompts("REVIEW")
                and h.git(wt, "rev-parse", "HEAD") == head and no_caller_receipt(c))

    unapproved = "python3 gates/unapproved.py"
    c = case("resumed-unapproved", {"writes": {
        **h.BUILD["writes"],
        ".claude/ouro.toml": binding.replace("[ship]", '[[gate]]\nrun = "' + unapproved
            + '"\ncheap = true\npaths = ["src/*"]\nci = "echo another workflow runs this gate"\n[ship]'),
        "gates/unapproved.py": "raise SystemExit('caller must not execute')\n"}})
    c.ouro("run", "1")
    restore_ready(c)
    c.clear_prompts()
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("resumed branch-only approval cannot authorize missing caller execution",
            code == 3 and "Missing successful execution evidence" in out
            and unapproved in out and "caller must not execute" not in out
            and not c.prompts("BUILD") and no_caller_receipt(c))

    c = case("success", successes)
    code, out, err = c.ouro("run", "1")
    h.check("worker-only candidates wait for explicit caller consent instead of auto-executing",
            code == 4 and "waiting for validation" in out and not ready(c)
            and not c.prompts("REVIEW") and no_caller_receipt(c) and len(c.prompts("BUILD")) == 1)
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("real successful commands satisfy proof despite an empty self-report and a CI notice",
            code == 0 and ready(c) and len(c.prompts("BUILD")) == 1)
    wt = pathlib.Path(c.node_call("BUILD")["cwd"])
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    h.check("resuming the same head reuses genuine settled proof without rebuilding or caller execution",
            code == 0 and not c.prompts("BUILD") and "Missing successful execution evidence" not in out)

    h.git(wt, "commit", "-q", "--allow-empty", "-m", "later head without execution")
    c.clear_prompts()
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("a newer resumed head obtains fresh caller proof without a BUILD fix",
            code == 0 and not c.prompts("BUILD") and "Missing successful execution evidence" not in out)
    proof = pathlib.Path(h.git(wt, "rev-parse", "--absolute-git-dir")) / "ouro-validation"
    head = h.git(wt, "rev-parse", "HEAD")
    receipts = [p for p in proof.glob("*/receipt.json") if json.loads(p.read_text(encoding="utf-8"))["sha"] == head]
    h.check("a genuine current-head receipt exists before corruption", bool(receipts))
    for receipt in receipts:
        receipt.write_text("{corrupt", encoding="utf-8")
    ready_before = sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments())
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    h.check("corrupt proof without current candidates uses the bounded fix path",
            code == 4 and len(c.prompts("BUILD")) == 1 and "waiting for validation" in out
            and "Missing successful execution evidence" in out and sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments()) == ready_before)
    c.clear_prompts()
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("explicit validation replaces corrupt proof with genuine command execution",
            code == 0 and not c.prompts("BUILD") and "Missing successful execution evidence" not in out)

    # Keep the original corruption control with current worker-candidate provenance;
    # the caller-only newer-head case above deliberately has different provenance.
    c = case("current-candidate-corrupt", successes)
    c.ouro("run", "1")
    code, out, err = c.ouro("run", "1", "--validate")
    wt = pathlib.Path(c.node_call("BUILD")["cwd"])
    head = h.git(wt, "rev-parse", "HEAD")
    proof = pathlib.Path(h.git(wt, "rev-parse", "--absolute-git-dir")) / "ouro-validation"
    receipts = [path for path in proof.glob("*/receipt.json")
                if json.loads(path.read_text(encoding="utf-8"))["sha"] == head]
    h.check("current-candidate corruption starts from genuine current-head proof",
            code == 0 and ready(c) and bool(receipts))
    for receipt in receipts:
        receipt.write_text("{corrupt", encoding="utf-8")
    ready_before = sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments())
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    h.check("current-candidate corrupt proof waits for consent without rebuilding",
            code == 4 and not c.prompts("BUILD") and not c.prompts("REVIEW")
            and "waiting for validation" in out and h.git(wt, "rev-parse", "HEAD") == head
            and sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments()) == ready_before)
    c.clear_prompts()
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("current-candidate corrupt proof is replaced by fresh explicit execution",
            code == 0 and not c.prompts("BUILD") and h.git(wt, "rev-parse", "HEAD") == head
            and "Missing successful execution evidence" not in out
            and "validation @ " in out and ready(c))

    c = case("head-reader", {"_run": [command, second]})
    c.write("gates/check.py",
            "import subprocess\n"
            "source = subprocess.check_output(['git', 'show', 'HEAD:src/app.py'])\n"
            "raise SystemExit(9 if b'sum(items) + 0' in source else 0)\n")
    h.git(c.main, "add", "gates/check.py")
    h.git(c.main, "commit", "-q", "-m", "head-sensitive gate")
    h.git(c.main, "push", "-q", "origin", "master")
    for k in range(2, 7):
        c.node("BUILD", {**h.BUILD, "_run": [command, second], "writes": {
            "src/app.py": h.BUILD["writes"]["src/app.py"] + f"# fix {k}\n"}}, k)
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("a gate passing on the parent HEAD must pass again on the changed HEAD",
            code == 3 and not ready(c) and not c.prompts("REVIEW")
            and "exit 9" in out and len(c.prompts("BUILD")) == 6
            and any(h.stop_comment_ok(stop, "review cap") and "5 fix rounds are spent" in stop
                    for stop in h.stops_of(c))
            and not any(s.startswith("pr merge") for s in c.sigs()))

    for recovery in ("intact", "corrupt", "missing"):
        c = case("branch-grants-" + recovery, successes)
        c.node("REVIEW", {"verdict": "FINDINGS", "findings": [h.finding("M")]}, 1)
        c.node("REVIEW", h.CLEAN, 2)
        added = "python3 gates/added.py"
        augmented = binding.replace("[ship]", '[[gate]]\nrun = "' + added
                                    + '"\ncheap = true\nci = "echo another workflow runs this gate"\n[ship]')
        # The invoking owner also approves this exact caller command; the branch
        # change alone must never widen caller-context execution authority.
        c.write(".claude/ouro.toml", augmented)
        c.node("BUILD", {**h.BUILD, **successes, "writes": {
            "src/app.py": "fixed again\n", ".claude/ouro.toml": augmented,
            "gates/added.py": "print('added passed')\n"}}, 2)
        c.node("BUILD", {**h.BUILD, "_run": [command, second, added],
                        "writes": {"src/app.py": "third input\n"}}, 3)
        code, out, err = c.ouro("run", "1", "--validate")
        wt = pathlib.Path(c.node_call("BUILD")["cwd"])
        head = h.git(wt, "rev-parse", "HEAD")
        added_proof = []
        for receipt in h.validation_receipts(c):
            record = json.loads(receipt.read_text(encoding="utf-8"))
            if record["sha"] != head:
                continue
            for result in record["results"]:
                if result["cmd"] == added:
                    log = pathlib.Path(result["log"])
                    added_proof.append(result["sha"] == head and result["exit"] == 0
                                       and result["digest"] == hashlib.sha256(log.read_bytes()).hexdigest())
        fix_brief = (c.nodes / "BUILD.3.prompt.txt").read_text(encoding="utf-8")
        h.check(recovery + ": a branch-added requirement forces BUILD3 before clean REVIEW2 with genuine proof",
                code == 0 and ready(c) and len(c.prompts("BUILD")) == 3 and len(c.prompts("REVIEW")) == 2
                and "Missing successful execution evidence" in fix_brief and added in fix_brief
                and added_proof and all(added_proof))
        proof = pathlib.Path(h.git(wt, "rev-parse", "--absolute-git-dir")) / "ouro-execution"
        if recovery != "intact":
            retained = removed = 0
            for receipt in proof.glob("*.json"):
                grants = json.loads(receipt.read_text(encoding="utf-8")).get("grants") or []
                if any(row[0] == added for row in grants):
                    removed += 1
                    if recovery == "corrupt":
                        receipt.write_text("{corrupt", encoding="utf-8")
                    else:
                        receipt.unlink()
                elif grants:
                    retained += 1
            h.check(recovery + " recovery leaves older grants but removes the added gate's receipts",
                    retained > 0 and removed > 0)
        # BUILD2 and BUILD3 spent two fixes; two manual commits plus the
        # changed-gate commit below exhaust the independently pinned five-fix budget.
        for index in range(2):
            h.git(wt, "commit", "--allow-empty", "-qm", f"spent manual fix {index}")
        (wt / "gates/added.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
        h.git(wt, "add", "gates/added.py")
        h.git(wt, "commit", "-q", "-m", "new head invalidates added gate proof")
        h.git(wt, "push", "-q", "origin", "HEAD")
        # Prove the original commands genuinely at this new head. Otherwise the
        # run could stop on their missing proof and hide a dropped added gate.
        validator = types.SimpleNamespace(path=wt,
            head=lambda: h.git(wt, "rev-parse", "HEAD"), admin=lambda: pathlib.Path(h.git(wt, "rev-parse", "--absolute-git-dir")),
            git=lambda *args, **kw: types.SimpleNamespace(out=h.git(wt, *args)),
            env=types.SimpleNamespace(default="master", runtime=developer.Settings()))
        known, failure = h.runmod.validation.validate(validator, [
            {"cmd": cmd, "reason": "known-command regression control"} for cmd in (command, second)], str)
        h.check(recovery + ": original commands have genuine current-head proof before added-gate regression",
                failure is None and [row["cmd"] for row in known] == [command, second])
        ready_before = sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments())
        c.clear_prompts()
        code, out, err = c.ouro("run", "1", "--validate")
        h.check(recovery + " receipts: a newer resumed head retains a branch-added command obligation",
                code == 3 and added in out and "exit 9" in out
                and int(h.git(wt, "rev-list", "--count", "--no-merges", "origin/master..HEAD")) == 6
                and any(h.stop_comment_ok(stop, "review cap") and "5 fix rounds are spent" in stop
                        for stop in h.stops_of(c))
                and not c.prompts("REVIEW") and not c.prompts("BUILD")
                and not any(s.startswith("pr merge") for s in c.sigs())
                and sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments()) == ready_before)

    for lane in ("worker", "driver"):
        setup = ("import subprocess\n"
                 "subprocess.check_call(['git', '-c', 'protocol.file.allow=always', "
                 "'submodule', 'update', '--init'])\n")
        sub_binding = (binding if lane == "worker" else h.BINDING.format(
            policy="stop-at-pr", review="adversarial-review")).replace(
                "[labels]", 'setup = "python3 gates/setup.py"\n[labels]')
        c = h.happy_case("execution-submodule-" + lane, binding=sub_binding,
                         files={"gates/setup.py": setup})
        h.git(c.main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(c.main), "dep")
        h.git(c.main, "commit", "-qam", "tracked dependency")
        h.git(c.main, "push", "-q", "origin", "master")
        c.node("BUILD", {**h.BUILD, "_run": [command] if lane == "worker" else []})
        code, out, err = c.ouro("run", "1", "--validate")
        h.check(lane + " execution with an initialized tracked submodule satisfies the barrier",
                code == 0 and ready(c) and len(c.prompts("BUILD")) == 1
                and "Missing successful execution evidence" not in out)

    c = case("fixed", {})
    c.node("BUILD", {**h.BUILD, **successes}, 2)
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("a fix round can supply missing execution with a fresh observed run",
            code == 0 and ready(c) and len(c.prompts("BUILD")) == 2
            and "Missing successful execution evidence" in out)
    c = case("fix-regression", {})
    c.node("BUILD", {**h.BUILD, **successes}, 1)
    c.node("REVIEW", {"verdict": "FINDINGS", "findings": [h.finding("M")]}, 1)
    c.node("REVIEW", h.CLEAN, 2)
    c.node("BUILD", {**h.BUILD, "writes": {"src/app.py": "changed by fix\n"}}, 2)
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("a later fix cannot reuse the first BUILD's successful execution",
            code == 3 and not ready(c) and "Missing successful execution evidence" in out)

    c = case("pending", {"validation_pending": [
        {"cmd": cmd, "reason": "worker executable unavailable"} for cmd in (command, second)]})
    code, out, err = c.ouro("run", "1")
    h.check("environment-blocked execution waits without a synthetic fix or review",
            code == 4 and "waiting for validation" in out and len(c.prompts("BUILD")) == 1
            and not c.prompts("REVIEW") and not ready(c))
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("genuine exact-head caller validation satisfies all required commands",
            code == 0 and ready(c) and "Missing successful execution evidence" not in out)

    c = h.happy_case("execution-driver-mutation", files={
        "gates/check.py": "from pathlib import Path\nPath('src/app.py').write_text('changed by gate')\n",
        "gates/other.py": "print('other passed')\n"}, table={"_issue_1": h.issue(body=body)})
    c.node("BUILD", {**h.BUILD, "validation_pending": [{"cmd": second, "reason": "worker unavailable"}]})
    c.ouro("run", "1")
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("a driver gate cannot mutate inputs after caller validation and reach Ready",
            code == 1 and "unchanged HEAD and clean tracked/untracked inputs" in err and not ready(c))
