"""Controls for the test-only dependency model, distinct from real platform coverage."""
from types import SimpleNamespace

from ouro import validation
from workflow_executor import WorkflowExecutor


def exercise(h):
    h.check("generic executor model leaves the real validator module unchanged", h.runmod.validation is validation)
    try:
        validation.check_platform()
    except h.shmod.DriverError:
        pass
    else:
        print("INFO: native supported host keeps the real executor in every driver section")
        return
    c = h.Case("workflow-executor-model-controls")
    previous = h.WORKFLOW_EXECUTOR_MODEL
    try:
        h.WORKFLOW_EXECUTOR_MODEL = True
        h.check("a modeled section's entry is the executor model on unsupported hosts",
                c.ouro_entry() == [h.STUB.parent / "workflow_executor.py", c.root])
        real = h.validation_case("mixed-coordination-model-control")
        h.check("mixed caller-validation fixtures retain the real production entry",
                real.real_executor and real.ouro_entry() == [h.BIN / "ouro"])
    finally:
        h.WORKFLOW_EXECUTOR_MODEL = previous
    mark = c.root / "executed.txt"
    c.write("gates/model.py", "from pathlib import Path\nwith Path(" + repr(str(mark)) + ").open('a') as f: f.write('ran\\n')\n")
    c.write("gates/fail.py", "raise SystemExit(9)\n")
    h.git(c.main, "add", "gates")
    h.git(c.main, "commit", "-qm", "model command fixtures")
    current = SimpleNamespace(path=c.main, head=lambda: h.git(c.main, "rev-parse", "HEAD"),
                              git=lambda *args, **kw: SimpleNamespace(out=h.git(c.main, *args)))
    model = WorkflowExecutor(c.root)
    h.check("modeled input preflight checks the real fixture head and cleanliness", model.check_inputs(current, current.head(), None) is None)
    h.check("modeled input preflight returns the real refusal code for changed HEAD without execution",
            model.check_inputs(current, "0" * 40, None) == 1 and not mark.exists())
    pending = [{"cmd": "python3 gates/model.py", "reason": "generic workflow model control"}]
    rows, failure = model.validate(current, pending, None, reuse=True)
    h.check("model actually runs the fixture command and binds its result to the current head",
            failure is None and rows[0]["exit"] == 0 and rows[0]["sha"] == current.head() and mark.read_text() == "ran\n")
    h.git(c.main, "commit", "-q", "--allow-empty", "-m", "new modeled head")
    rows, failure = model.validate(current, pending, None, reuse=True, execute_missing=False)
    h.check("a newer head cannot reuse prior modeled proof", rows == [] and failure == 75 and mark.read_text() == "ran\n")
    rows, failure = model.validate(current, [{"cmd": "python3 gates/fail.py"}], None, execute_missing=False)
    h.check("missing modeled proof respects execute_missing consent", rows == [] and failure == 75)
    rows, failure = model.validate(current, [{"cmd": "python3 gates/fail.py"}], None)
    h.check("a real nonzero fixture exit cannot become modeled success", failure == 9 and rows[0]["exit"] == 9)
    c.write("src/app.py", "changed without a commit\n")
    refused = False
    try:
        model.validate(current, pending, None, reuse=True)
    except h.shmod.DriverError:
        refused = True
    h.check("dirty inputs cannot reuse modeled current-head success", refused and mark.read_text() == "ran\n")
    h.check("modeled input preflight returns the real refusal code for dirty inputs without execution",
            model.check_inputs(current, current.head(), None) == 1 and mark.read_text() == "ran\n")
    private = h.pathlib.Path(h.git(c.main, 'rev-parse', '--absolute-git-dir')) / 'ouro-validation'
    h.check("the fixture model creates no production validation receipts", not list(private.glob("*/receipt.json")))
