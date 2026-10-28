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
                    code == 0 and "waiting for validation" in out and not ready(c)
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
                    code == 0 and "waiting for validation" in out and not ready(c)
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
                code == 0 and "waiting for validation" in out and not ready(c)
                and not c.prompts("REVIEW") and len(c.prompts("BUILD")) == 1 and no_caller_receipt(c)
                and "changed by gate" not in (worktree / "src/app.py").read_text(encoding="utf-8"))
        print("SKIP: supported caller proof controls; actual unsupported refusal controls above remain required")
        return

    c = case("success", successes)
    code, out, err = c.ouro("run", "1")
    h.check("worker-only candidates wait for explicit caller consent instead of auto-executing",
            code == 0 and "waiting for validation" in out and not ready(c)
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
    h.check("a newer resumed head cannot inherit an older worker receipt",
            code == 0 and c.prompts("BUILD") and "Missing successful execution evidence" in out)
    proof = pathlib.Path(h.git(wt, "rev-parse", "--absolute-git-dir")) / "ouro-validation"
    head = h.git(wt, "rev-parse", "HEAD")
    receipts = [p for p in proof.glob("*/receipt.json") if json.loads(p.read_text(encoding="utf-8"))["sha"] == head]
    h.check("a genuine current-head receipt exists before corruption", bool(receipts))
    for receipt in receipts:
        receipt.write_text("{corrupt", encoding="utf-8")
    ready_before = sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments())
    c.clear_prompts()
    code, out, err = c.ouro("run", "1")
    h.check("corrupt current-head proof waits for consent instead of reusing observations",
            code == 0 and not c.prompts("BUILD") and "waiting for validation" in out
            and sum(x["body"].startswith("**Ready for merge**") for x in c.pr_comments()) == ready_before)
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("explicit validation replaces corrupt proof with genuine command execution",
            code == 0 and not c.prompts("BUILD") and "Missing successful execution evidence" not in out)

    c = case("head-reader", {"_run": [command, second]})
    c.write("gates/check.py",
            "import subprocess\n"
            "source = subprocess.check_output(['git', 'show', 'HEAD:src/app.py'])\n"
            "raise SystemExit(9 if b'sum(items) + 0' in source else 0)\n")
    h.git(c.main, "add", "gates/check.py")
    h.git(c.main, "commit", "-q", "-m", "head-sensitive gate")
    h.git(c.main, "push", "-q", "origin", "master")
    for k in (2, 3, 4):
        c.node("BUILD", {**h.BUILD, "_run": [command, second], "writes": {
            "src/app.py": h.BUILD["writes"]["src/app.py"] + f"# fix {k}\n"}}, k)
    code, out, err = c.ouro("run", "1", "--validate")
    h.check("a gate passing on the parent HEAD must pass again on the changed HEAD",
            code == 3 and not ready(c) and not c.prompts("REVIEW")
            and "exit 9" in out and not any(s.startswith("pr merge") for s in c.sigs()))

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
        (wt / "gates/added.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
        h.git(wt, "add", "gates/added.py")
        h.git(wt, "commit", "-q", "-m", "new head invalidates added gate proof")
        h.git(wt, "push", "-q", "origin", "HEAD")
        # Prove the original commands genuinely at this new head. Otherwise the
        # run could stop on their missing proof and hide a dropped added gate.
        validator = types.SimpleNamespace(path=wt,
            head=lambda: h.git(wt, "rev-parse", "HEAD"),
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
                code == 3 and added in out and "Missing successful execution evidence" in out
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
            code == 0 and "waiting for validation" in out and len(c.prompts("BUILD")) == 1
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
