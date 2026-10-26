"""Claude native Bash observation: wrap only an exact, already approved BUILD command."""
import json
import shlex
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ouro import execution, sh, validation
from ouro.runtime import process


def invocation(mode, policy, *args):
    return shlex.join([sys.executable, str(Path(__file__).resolve()), mode, str(policy), *map(str, args)])


def main():
    mode, location = sys.argv[1:3]
    policy = json.loads(Path(location).read_text(encoding="utf-8"))
    if mode == "hook":
        event = json.load(sys.stdin)
        command = (event.get("tool_input") or {}).get("command")
        if event.get("tool_name") == "Bash" and command in policy["commands"]:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "allow",
                "updatedInput": {**event["tool_input"], "command": invocation(
                    "run", location, policy["commands"].index(command))}}}))
        return 0
    command = policy["commands"][int(sys.argv[3])]
    argv, env = validation.command(command)
    if env:
        raise sh.DriverError("BUILD observation does not admit environment prefixes")
    root = Path(policy["root"])
    before = execution.fingerprint(root)
    error = None
    try:
        result = process.run(sh.argv_for(*argv), cwd=root, timeout=600)
    except (sh.DriverError, OSError) as ex:
        result, error = None, str(ex)
    after = execution.fingerprint(root) if result else None
    row = {"tool": "run", "command": command, "exit": result.code if result else None,
           "inputs": before if before == after else None, "error": error}
    with open(policy["evidence"], "a", encoding="utf-8") as output:
        output.write(json.dumps(row) + "\n")
    if result is None:
        sys.stderr.write(error + "\n")
        return 1
    sys.stdout.write(result.out)
    sys.stderr.write(result.err)
    return result.code if type(result.code) is int else 1


if __name__ == "__main__":
    sys.exit(main())
