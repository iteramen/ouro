"""Native CLI conformance on generated data; no model, auth or tracker call.

Run python3 tests/runtime/sandbox.py with each supported native CLI on PATH, without
compatibility wrappers. A reachable loopback control is required, never silently skipped.
"""
import json
import pathlib
import argparse
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "bin"))
from ouro import sh
from ouro.runtime import process, network
from ouro.runtime.tools import Bridge, sandbox_argv
from fixtures import public_fixture

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--control", choices=("all", "filesystem", "network"), default="all")
control = parser.parse_args().control

with public_fixture("ouro-sandbox-fixture-") as folder, public_fixture("ouro-outside-fixture-") as outside:
    root = pathlib.Path(folder)
    protected = [root / name / "protected.txt" for name in (".git", ".claude", ".codex", ".agents", ".aws")]
    protected.append(pathlib.Path(outside) / "protected.txt")
    for path in protected:
        path.parent.mkdir(exist_ok=True)
        path.write_text("preserve")
    version = sh.run([*sh.argv_for("codex"), "--version"], timeout=30)
    assert version.code == 0, version
    if control in ("all", "filesystem"):
        script = (
            "from pathlib import Path\n"
            "Path('allowed.txt').write_text('allowed')\n"
            f"for name in {list(map(str, protected))!r}:\n"
            "    try:\n        Path(name).write_text('forbidden')\n"
            "    except OSError as ex:\n"
            "        assert ex.errno in (1, 13, 30), repr(ex)\n"
            "    else:\n        raise AssertionError('sandbox allowed protected write: ' + name)\n"
            "print('ouro-sandbox-ready')\n"
        )
        (root / "check.py").write_text(script, encoding="utf-8")
        command = '"' + sys.executable + '" check.py'
        bridge = Bridge({"root": folder, "edit": True, "commands": [command], "probe": False})
        result = bridge.call("run", {"command": command})
        assert result["exit"] == 0 and "ouro-sandbox-ready" in result["stdout"], result
        assert (root / "allowed.txt").read_text() == "allowed"
        assert all(path.read_text() == "preserve" for path in protected)
        print(json.dumps({"cli": version.out.strip(), "platform": sys.platform,
                          "bridge": result, "controls": "workspace writable; protected/outside writes denied"}))

    if control in ("all", "network"):
        # Production readiness itself runs the unrestricted nonce/host receipt control
        # and the exact configured sandbox boundary. A refusal is a failed conformance,
        # never a successful skip; the other controls can be run independently.
        try:
            network.readiness(root)
        except sh.DriverError as ex:
            print("Native network readiness REFUSED: " + str(ex), flush=True)
            raise
        print("Native network readiness: unrestricted nonce received; sandbox explicitly denied")
