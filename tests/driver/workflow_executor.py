"""TEST ONLY: executor availability for generic workflow decisions, never platform evidence.

Real execution/validation/lifecycle sections invoke bin/ouro directly. No production
module reads a flag for this dependency model, and it writes no production receipt.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from ouro import cli, run, sh, validation  # noqa: E402


class WorkflowExecutor:
    def __init__(self, fixture):
        self.fixture = fixture.resolve()

    def __getattr__(self, name):
        return getattr(validation, name)

    def check_platform(self):
        """Availability is the modeled dependency; the real platform check is unchanged."""

    def check_inputs(self, current, head):
        return None if validation.inputs_match(current, head) else 1

    def validate(self, current, pending, *, reuse=False, execute_missing=True):
        assert current.worktree.resolve().is_relative_to(self.fixture)
        head = current.head()
        validation.clean(current, head)
        commands = [row["cmd"] for row in pending]
        directory = self.fixture / "workflow-executor-model" / validation.key(head, commands)
        directory.mkdir(parents=True, exist_ok=True)
        cache = directory / "model-results.json"
        if reuse and cache.is_file():
            results = json.loads(cache.read_text(encoding="utf-8"))
            if all(row["sha"] == head and row["cmd"] == command and row["exit"] == 0
                   for row, command in zip(results, commands)) and len(results) == len(commands):
                return results, None
        if not execute_missing:
            return [], 75
        results = []
        for i, command in enumerate(commands):
            argv, environment = validation.command(command)
            result = sh.run(sh.argv_for(*argv), cwd=current.worktree, env=environment, timeout=30)
            log = directory / f"{i}.log"
            log.write_text(result.out + result.err, encoding="utf-8")
            results.append({"cmd": command, "sha": head, "exit": result.code,
                            "log": str(log), "digest": hashlib.sha256(log.read_bytes()).hexdigest()})
            validation.clean(current, head)
            if result.code:
                return results, result.code
        cache.write_text(json.dumps(results), encoding="utf-8")
        return results, None


if __name__ == "__main__":
    fixture = Path(sys.argv[1]).resolve()
    assert fixture.parent.name.startswith("driver-test-run-")
    assert all((fixture / name).exists() for name in ("nodes", "state", "gh-table.json"))
    try:
        validation.check_platform()
    except sh.DriverError:
        pass
    else:
        raise SystemExit("test executor model must not replace a supported real executor")
    print("INFO: generic workflow uses a test-only supported-executor model; this is not Windows/WSL admission.")
    run.validation = WorkflowExecutor(fixture)
    raise SystemExit(cli.main(sys.argv[2:]))
