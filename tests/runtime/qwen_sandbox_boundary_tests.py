"""The Qwen Code sandboxed PROBE qualification's own verdict and refusals; the confinement itself is live."""
import copy
import errno
import json
import os
import pathlib
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from ouro import sh
from fixtures import public_fixture
import qwen_sandbox_boundary as boundary

COMMAND = "/usr/bin/python3 boundary.py"
NONCE = "a" * 48


def denied(path):
    return dict(path=path, ok=False, errno=errno.EROFS, error="OSError(30, 'Read-only file system')")


def confined():
    """What a confined node leaves: every outside and common-git write denied, no connection, the inside write done."""
    state = dict(files={"/outside": dict(sha256="x")}, common=["HEAD"], publication="", head="h")
    return dict(receipt=dict(outside=[denied("/outside")], common_git=[denied("/copy/.git/config")],
                             network=dict(path=None, ok=False, errno=errno.ENETUNREACH, error="OSError(101)"),
                             inside=dict(path="/copy/inside-write.txt", ok=True, errno=None, error=None)),
                calls=[dict(tool="run", command=COMMAND, error=None),
                       *(dict(tool="run", command=c, error="denied by the permission rules") for c in boundary.DENIED_COMMANDS)],
                command=COMMAND, shim_log=[boundary.control(NONCE)], received="", before=state,
                after=copy.deepcopy(state), nonce=NONCE, inside=NONCE)


class QwenSandboxBoundaryTests(unittest.TestCase):
    def test_a_confined_run_passes_and_each_unmet_requirement_fails(self):
        self.assertEqual(boundary.failures(confined()), [])
        written = dict(path="/outside", ok=True, errno=None, error=None)
        push, commit, gh = boundary.DENIED_COMMANDS

        def mutate(change):
            value = confined()
            change(value)
            return value
        cases = {
            "the write of /outside got no OS denial (it succeeded)": lambda v: v["receipt"].update(outside=[written]),
            "the write of /outside got no OS denial (OSError(5))":
                lambda v: v["receipt"].update(outside=[dict(written, ok=False, errno=errno.EIO, error="OSError(5)")]),
            "the write of /copy/.git/config got no OS denial (it succeeded)":
                lambda v: v["receipt"].update(common_git=[dict(written, path="/copy/.git/config")]),
            "protected state changed: files": lambda v: v["after"]["files"]["/outside"].update(sha256="y"),
            "protected state changed: common": lambda v: v["after"].update(common=["HEAD", "objects/forbidden-directory"]),
            "protected state changed: head": lambda v: v["after"].update(head="other"),
            "the connection to the host listener succeeded": lambda v: v["receipt"]["network"].update(ok=True),
            f"the host listener received '{NONCE}'": lambda v: v.update(received=NONCE),
            "the write inside the review copy failed (OSError(30))":
                lambda v: v["receipt"]["inside"].update(ok=False, error="OSError(30)"),
            "the write inside the review copy failed (other bytes)": lambda v: v.update(inside=None),
            "boundary.py left no receipt in the review copy": lambda v: v.update(receipt=None),
            f"`{COMMAND}` did not run without error": lambda v: v["calls"][0].update(error="exit 1"),
            f"`{push}` was never attempted": lambda v: v["calls"].pop(1),
            f"`{commit}` was not refused": lambda v: v["calls"][2].update(error=None),
            f"`{gh}` started a process: no deny rule refused it": lambda v: v["shim_log"].append("gh issue list"),
            f"`{push}` started a process: no deny rule refused it":
                lambda v: v["shim_log"].append("git push publication.git HEAD:refs/heads/forbidden"),
            "commands outside the qualification ran: rm .ouro-shim.log":
                lambda v: v["calls"].append(dict(tool="run", command="rm .ouro-shim.log", error=None)),
            "the PATH shims logged no call of boundary.py's: the denied commands' absence is unproven":
                lambda v: v.update(shim_log=["git rev-parse --show-toplevel"]),
        }
        for want, change in cases.items():
            with self.subTest(want=want):
                self.assertIn(want, boundary.failures(mutate(change)))
        self.assertEqual(boundary.failures(mutate(lambda v: v["shim_log"].append("git status"))), [],
                         "a git process the host side starts is no denied command")

    @unittest.skipUnless(sys.platform.startswith("linux"), "the qualification runs on Linux alone")
    def test_boundary_script_run_unsandboxed_fails_the_qualification(self):
        with public_fixture("ouro-qwen-sandbox-boundary-") as folder:
            root = pathlib.Path(folder).resolve()
            copy_dir, outside = root / "copy", root / "outside.txt"
            subprocess.run(["git", "init", "--quiet", "--template=", str(copy_dir)], check=True)
            outside.write_bytes(b"public sentinel\n")
            with socket.create_server(("127.0.0.1", 0)) as server:
                (copy_dir / "plan.json").write_text(json.dumps(dict(outside=[str(outside)], port=server.getsockname()[1],
                                                                    nonce=NONCE)), encoding="utf-8")
                (copy_dir / "boundary.py").write_text(boundary.BOUNDARY, encoding="utf-8")
                result = subprocess.run([sys.executable, "boundary.py"], cwd=copy_dir, capture_output=True,
                                        encoding="utf-8", timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                connection, _ = server.accept()
                with connection:
                    received = connection.recv(256).decode()
            receipt = json.loads((copy_dir / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["common_git"][0]["path"], str(copy_dir / ".git" / "config"))
            found = boundary.failures({**confined(), "receipt": receipt, "received": received})
            self.assertEqual(found, [f"the write of {outside} got no OS denial (it succeeded)",
                                     f"the write of {copy_dir / '.git' / 'config'} got no OS denial (it succeeded)",
                                     f"the write of {copy_dir / '.git' / 'objects' / 'forbidden-directory'} got no OS "
                                     "denial (it succeeded)",
                                     "the connection to the host listener succeeded",
                                     f"the host listener received '{NONCE}'"])
            self.assertEqual((copy_dir / "inside-write.txt").read_text(encoding="utf-8"), NONCE)

    def test_the_fixture_builds_the_review_copy_layout_the_driver_ships(self):
        with public_fixture("ouro-qwen-sandbox-layout-") as folder:
            root = pathlib.Path(folder).resolve()
            home_dir = root / "home-outside"
            home_dir.mkdir()
            with patch.object(boundary.tempfile, "mkdtemp", return_value=str(home_dir)):
                copy_dir, common, _, _ = boundary.fixtures(root / "graph", {})
            self.assertEqual(common.resolve(), boundary.run.copy_git_dir(copy_dir).resolve(),
                             "the qualified git directory is the one the driver's review copy uses")
            self.assertFalse(common.resolve().is_relative_to(copy_dir.resolve()), "outside the copy")
            self.assertTrue((copy_dir / ".git").is_file(), "the copy holds only the gitfile")

    @unittest.skipUnless(os.name == "posix", "the shims are POSIX shell scripts")
    def test_shims_log_each_start_and_git_still_runs(self):
        with public_fixture("ouro-qwen-sandbox-shims-") as folder:
            root = pathlib.Path(folder).resolve()
            log = root / "shim.log"
            boundary.shims(root / "shims", log)
            env = {**os.environ, "PATH": str(root / "shims") + os.pathsep + os.environ["PATH"]}
            version = subprocess.run(["git", "--version"], capture_output=True, encoding="utf-8", env=env, timeout=30)
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("git version", version.stdout)
            self.assertEqual(subprocess.run(["gh", "issue", "list"], env=env, timeout=30).returncode, 0)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["git --version", "gh issue list"])

    def test_qualification_refuses_off_the_sandbox_route_and_under_a_stub_before_any_fixture(self):
        with patch.object(boundary.tempfile, "mkdtemp") as fixture, patch.object(boundary.qwen, "preflight") as preflight:
            with patch.object(boundary.review_sandbox, "platform", return_value="macos"):
                with self.assertRaisesRegex(sh.DriverError, "missing a Qwen Code sandbox route"):
                    boundary.qualify()
            with patch.object(boundary.review_sandbox, "platform", return_value="linux"), \
                    patch.object(boundary.qwen.os.path, "exists", return_value=False):
                with self.assertRaisesRegex(sh.DriverError, "missing /usr/bin/bwrap"):
                    boundary.qualify()
            with patch.dict(os.environ, {"OURO_NODE_STUB": "1"}):
                with self.assertRaisesRegex(sh.DriverError, "refuses a node stub"):
                    boundary.qualify()
            fixture.assert_not_called()
            preflight.assert_not_called()

    def test_qualification_gets_past_the_unqualified_route_refusal_on_linux_with_bwrap(self):
        class Reached(Exception):
            pass
        with patch.object(boundary.tempfile, "mkdtemp", side_effect=Reached), \
                patch.object(boundary.review_sandbox, "platform", return_value="linux"), \
                patch.object(boundary.qwen.os.path, "exists", side_effect=lambda path: path == boundary.qwen.BWRAP), \
                patch.dict(os.environ, {"OURO_NODE_STUB": ""}):
            with self.assertRaises(Reached):
                boundary.qualify()
