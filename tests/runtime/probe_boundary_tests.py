"""PROBE command grants and fail-closed topology regressions; native enforcement is live."""
import copy
import errno
import json
import os
import pathlib
import sys
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ouro import sh
from ouro.runtime import tools
from fixtures import public_fixture
from probe_boundary import HARDLINK_CHECK, PUBLICATION_CHECK


class ProbeBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform in ("linux", "darwin"), "POSIX read-only RPC transport")
    def test_owned_rpc_transport_handshake_and_cleanup(self):
        with public_fixture("ouro-rpc-transport-") as folder:
            state = []
            def exchange(data):
                if not state:
                    state.append("sent")
                    return b"public request\n"
                if data:
                    self.assertEqual(data, b"public response\n")
                    state.append("complete")
                    return None
                return b""
            script = ("import sys,time; assert sys.stdin.readline() == 'public request\\n'; "
                      "print('public response', flush=True); time.sleep(120)")
            result = tools.process.run([sys.executable, "-c", script], cwd=folder, timeout=5, exchange=exchange)
            self.assertEqual((result.out, result.err), ("", ""))
            self.assertEqual(state, ["sent", "complete"])

    @unittest.skipUnless(sys.platform in ("linux", "darwin"), "POSIX read-only RPC transport")
    def test_rpc_parser_failure_closes_owned_server(self):
        with public_fixture("ouro-rpc-refusal-") as folder:
            owned = []
            def exchange(data):
                if data:
                    owned.append(int(data))
                    raise ValueError("malformed RPC")
                return b""
            script = "import os,time; print(os.getpid(), flush=True); time.sleep(120)"
            with self.assertRaisesRegex(ValueError, "malformed RPC"):
                tools.process.run([sys.executable, "-c", script], cwd=folder, timeout=5, exchange=exchange)
            self.assertEqual(len(owned), 1)
            with self.assertRaises(ProcessLookupError):
                os.kill(owned[0], 0)

    def configuration_fixture(self, root):
        files = {str(root): "write", **{str(root / p): "read" for p in tools.PROTECTED_NAMES}}
        selected = dict(description=None, extends=":read-only", workspace_roots=None,
                        filesystem={**files, "glob_scan_max_depth": None},
                        network={key: None for key in (
                            "proxy_url", "enable_socks5", "socks_url", "enable_socks5_udp",
                            "allow_upstream_proxy", "dangerously_allow_non_loopback_proxy",
                            "dangerously_allow_all_unix_sockets", "mode", "domains", "unix_sockets",
                            "allow_local_binding", "mitm")})
        selected["network"]["enabled"] = False
        identity = dict(name=dict(type="sessionFlags"), version="opaque-session-version")
        required = ["default_permissions", "permissions.ouro.extends", "permissions.ouro.network.enabled",
                    *("permissions.ouro.filesystem." + path for path in files)]
        return dict(config=dict(default_permissions="ouro", permissions=dict(ouro=selected)),
                    origins={key: copy.deepcopy(identity) for key in required},
                    layers=[dict(**identity, disabledReason=None, config={})])

    def rpc(self, response, requirements=None, notification=None):
        def run(argv, **kwargs):
            exchange = kwargs["exchange"]
            self.assertEqual(argv[1], "app-server")
            self.assertIn('default_permissions="ouro"', argv)
            self.assertNotIn("--ignore-user-config", argv)
            first = json.loads(exchange(b""))
            self.assertEqual(first["method"], "initialize")
            self.assertTrue(first["params"]["capabilities"]["experimentalApi"])
            packet = lambda identifier, result: (json.dumps(dict(id=identifier, result=result)) + "\n").encode()
            sent = exchange(packet(1, {}))
            self.assertEqual([json.loads(line)["method"] for line in sent.splitlines()],
                             ["initialized", "config/read"])
            self.assertTrue(json.loads(sent.splitlines()[1])["params"]["includeLayers"])
            if notification is not None:
                notice = (json.dumps(notification) + "\n").encode()
                # Fragmented frames and interleaved notifications must not lose the matching response.
                self.assertEqual(exchange(notice[:7]), b"")
                self.assertEqual(exchange(notice[7:]), b"")
            self.assertEqual(json.loads(exchange(packet(2, response)))["method"], "configRequirements/read")
            self.assertIsNone(exchange(packet(3, requirements or dict(
                requirements=None, supportsIndependentSpeedModes=True))))
            return sh.R(0, "never log raw config or credentials", "", argv)
        return run

    def test_rpc_notifications_and_requests_fail_closed_without_private_diagnostics(self):
        with public_fixture("ouro-config-notification-") as folder:
            root = pathlib.Path(folder).resolve()
            response = self.configuration_fixture(root)
            environment = {"CODEX_HOME": str(root / ".codex")}
            with patch.object(sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]):
                launch = tools.sandbox_argv(root, ["python", "check.py"], probe=True)
            notice = dict(emittedAtMs=1, method="remoteControl/status/changed",
                          params=dict(installationId="private-id", serverName="private-name",
                                      status="disabled", environmentId=None))
            with patch.object(tools.process, "run", side_effect=self.rpc(response, notification=notice)):
                self.assertEqual(tools.probe_configuration(root, environment, launch)[0], response)
            for bad in (dict(method="unknown", params={"secret": "private-value"}),
                        {**notice, "params": {"status": "disabled"}},
                        {**notice, "params": {**notice["params"], "status": "unknown"}},
                        dict(id=91, method="approval/request", params={"secret": "private-value"}),
                        dict(id=2, error={"message": "private-value"})):
                with self.subTest(packet=bad), patch.object(
                        tools.process, "run", side_effect=self.rpc(response, notification=bad)):
                    with self.assertRaises(sh.DriverError) as refusal:
                        tools.probe_configuration(root, environment, launch)
                    self.assertNotIn("private", str(refusal.exception))
                    self.assertEqual(str(refusal.exception),
                        "PROBE effective configuration admission refused: read-only-rpc "
                        "(unavailable, conflicting, aliased or unprovable configuration)")

    def test_effective_configuration_rejects_merged_and_unknown_grants(self):
        with public_fixture("ouro-effective-config-") as folder:
            root = pathlib.Path(folder).resolve()
            response = self.configuration_fixture(root)
            with patch.object(sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]):
                launch = tools.sandbox_argv(root, ["python", "check.py"], probe=True)
            with patch.object(tools.process, "run", side_effect=self.rpc(response)):
                accepted = tools.probe_configuration(root, {"CODEX_HOME": str(root / ".codex")}, launch)
            self.assertEqual(accepted[0], response)
            # ConfigLayer requires name/version/config; disabledReason is optional.
            del response["layers"][0]["disabledReason"]
            with patch.object(tools.process, "run", side_effect=self.rpc(response)):
                self.assertEqual(tools.probe_configuration(
                    root, {"CODEX_HOME": str(root / ".codex")}, launch)[0], response)
            response["layers"][0]["disabledReason"] = "generated disabled layer"
            with patch.object(tools.process, "run", side_effect=self.rpc(response)):
                with self.assertRaisesRegex(sh.DriverError, "admission refused: layer-provenance"):
                    tools.probe_configuration(root, {"CODEX_HOME": str(root / ".codex")}, launch)
            response["layers"][0]["disabledReason"] = None
            for field in ("name", "version", "config", "unknown"):
                malformed = copy.deepcopy(response)
                if field == "unknown":
                    malformed["layers"][0][field] = None
                else:
                    del malformed["layers"][0][field]
                with self.subTest(layer_field=field), patch.object(
                        tools.process, "run", side_effect=self.rpc(malformed)):
                    with self.assertRaisesRegex(sh.DriverError, "admission refused: layer-provenance"):
                        tools.probe_configuration(root, {"CODEX_HOME": str(root / ".codex")}, launch)
            for mutation in ("workspace", "extra", "alias", "glob", "network", "socket",
                             "unknown", "origin", "version", "managed"):
                bad = copy.deepcopy(response)
                selected = bad["config"]["permissions"]["ouro"]
                if mutation == "workspace":
                    selected["extends"] = ":workspace"
                elif mutation in ("extra", "alias", "glob"):
                    selected["filesystem"][{"extra": "/tmp", "alias": str(root / ".." / root.name),
                                            "glob": "/tmp/**"}[mutation]] = "write"
                elif mutation == "network":
                    selected["network"]["enabled"] = True
                elif mutation == "socket":
                    selected["network"]["dangerously_allow_all_unix_sockets"] = True
                elif mutation == "unknown":
                    selected["new_field"] = None
                elif mutation == "origin":
                    bad["origins"]["default_permissions"]["name"] = dict(type="user")
                elif mutation == "version":
                    bad["layers"][0]["version"] = ""
                else:
                    bad["layers"].append(dict(name=dict(type="mdm", domain="generated", key="generated"),
                                              version="opaque", disabledReason=None, config={}))
                with self.subTest(mutation=mutation), patch.object(
                        tools.process, "run", side_effect=self.rpc(bad)):
                    stage = {"workspace": "selected-profile", "unknown": "selected-profile",
                             "extra": "filesystem", "alias": "filesystem", "glob": "filesystem",
                             "network": "network", "socket": "network", "origin": "permission-origins",
                             "version": "layer-provenance", "managed": "layer-provenance"}[mutation]
                    with self.assertRaisesRegex(sh.DriverError, "admission refused: " + stage):
                        tools.probe_configuration(root, {"CODEX_HOME": str(root / ".codex")}, launch)
            with patch.object(tools.process, "run", side_effect=self.rpc(
                    response, dict(requirements={"unknown": True}, supportsIndependentSpeedModes=True))):
                with self.assertRaises(sh.DriverError):
                    tools.probe_configuration(root, {"CODEX_HOME": str(root / ".codex")}, launch)

    def test_file_layer_versions_aliases_and_metadata_are_checked(self):
        with public_fixture("ouro-config-provenance-") as folder:
            root = pathlib.Path(folder).resolve()
            home = root / ".codex"
            home.mkdir()
            source = home / "config.toml"
            source.write_text("# generated public configuration\n", encoding="utf-8")
            response = self.configuration_fixture(root)
            response["layers"].append(dict(name=dict(type="user", file=str(source), profile=None),
                                           version="opaque-user-version", disabledReason=None, config={}))
            environment = {"CODEX_HOME": str(home)}
            with patch.object(sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]):
                launch = tools.sandbox_argv(root, ["python", "check.py"], probe=True)
            with patch.object(tools.process, "run", side_effect=self.rpc(response)):
                first = tools.probe_configuration(root, environment, launch)
                info = source.stat()
                os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns - 10_000_000_000))
                second = tools.probe_configuration(root, environment, launch)
                self.assertNotEqual(first, second)
                alias = home / "hardlink.toml"
                os.link(source, alias)
                with self.assertRaises(sh.DriverError):
                    tools.probe_configuration(root, environment, launch)
                alias.unlink()
            response["layers"][-1]["name"]["file"] = str(root / "unproved.toml")
            with patch.object(tools.process, "run", side_effect=self.rpc(response)):
                with self.assertRaises(sh.DriverError):
                    tools.probe_configuration(root, environment, launch)

    def test_configuration_drift_refuses_before_command_or_temp_creation(self):
        with public_fixture("ouro-config-drift-") as folder:
            root = pathlib.Path(folder).resolve()
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            with patch.object(tools, "probe_root"), patch.object(
                    sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]), patch.object(
                    tools, "probe_configuration", side_effect=[("version-one",), ("version-two",)]), patch.object(
                    tools.process, "run") as launch:
                with self.assertRaisesRegex(sh.DriverError, "configuration changed"):
                    bridge.run("python check.py")
                launch.assert_not_called()
                self.assertFalse((root / ".ouro-probe-tmp").exists())

    def test_publication_requires_os_denial_not_unpacker_text(self):
        diagnostic = "error: remote unpack failed: unable to create temporary object directory"
        cases = [
            (1, diagnostic, errno.EPERM, True),
            (1, diagnostic, errno.EACCES, True),
            (1, diagnostic, errno.EROFS, True),
            (1, diagnostic, errno.EIO, False),
            (1, diagnostic, errno.EXDEV, False),
            (1, diagnostic, None, False),
            (0, diagnostic, errno.EPERM, False),
            (1, "unrelated transport failure", errno.EPERM, False),
        ]
        for code, message, denied, accepted in cases:
            with self.subTest(code=code, message=message, denied=denied):
                rows = []
                responses = [SimpleNamespace(returncode=code, stderr=message),
                             SimpleNamespace(returncode=0, stdout="/public/remote.git", stderr="")]
                values = dict(subprocess=SimpleNamespace(run=lambda *a, **k: responses.pop(0)),
                              pathlib=pathlib, os=SimpleNamespace(path=SimpleNamespace(lexists=lambda p: False)),
                              errno=errno, rows=rows)
                failure = OSError(denied, "generated denial") if denied else None
                with patch.object(pathlib.Path, "mkdir", side_effect=failure):
                    if accepted:
                        exec(PUBLICATION_CHECK, values)
                        self.assertEqual(rows, [dict(path="git push", stderr=message, errno=denied)])
                    else:
                        with self.assertRaises(AssertionError):
                            exec(PUBLICATION_CHECK, values)
                        self.assertEqual(rows, [])

    def test_new_hardlink_refusal_requires_errno_and_unchanged_source(self):
        cases = [
            ("permission", errno.EPERM, None, True),
            ("access", errno.EACCES, None, True),
            ("readonly", errno.EROFS, None, True),
            ("cross-device creation", errno.EXDEV, None, True),
            ("unrelated error", errno.EIO, None, False),
            ("missing input", errno.ENOENT, None, False),
            ("changed bytes", errno.EXDEV, "bytes", False),
            ("changed metadata", errno.EXDEV, "mtime", False),
            ("destination left", errno.EXDEV, "destination", False),
            ("successful creation", None, "destination", False),
        ]
        for label, code, mutation, accepted in cases:
            with self.subTest(label=label), public_fixture("ouro-link-refusal-") as folder:
                root = pathlib.Path(folder)
                source = root / "source"
                source.write_bytes(b"public original")
                def attempt(src, dst):
                    if mutation == "bytes":
                        source.write_bytes(b"changed")
                    elif mutation == "mtime":
                        info = source.stat()
                        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns - 10_000_000_000))
                        self.assertNotEqual(source.stat().st_mtime_ns, info.st_mtime_ns)
                    elif mutation == "destination":
                        os.link(src, dst)
                    if code is not None:
                        raise OSError(code, label)
                rows = []
                values = dict(pathlib=pathlib, errno=errno, root=root,
                              targets=[str(source)], rows=rows,
                              os=SimpleNamespace(path=os.path, link=attempt))
                if accepted:
                    exec(HARDLINK_CHECK, values)
                    self.assertEqual(rows, [dict(path="new-hardlink", errno=code)])
                else:
                    with self.assertRaises(AssertionError):
                        exec(HARDLINK_CHECK, values)
                    self.assertEqual(rows, [])

    def test_probe_profile_has_only_explicit_canonical_writes(self):
        with public_fixture("ouro-probe-profile-") as root:
            root = pathlib.Path(root).resolve()
            for platform, name in (("linux", "posix"), ("darwin", "posix"), ("win32", "nt")):
                # Model the command interface without changing the host pathlib flavour.
                with patch.object(tools.sys, "platform", platform), patch.object(tools, "os", SimpleNamespace(name=name)), patch.object(sh, "argv_for", side_effect=lambda *args: list(args)):
                    argv = tools.sandbox_argv(root, ["python", "check.py"], probe=True)
                settings = {}
                for i, value in enumerate(argv):
                    if value == "-c":
                        settings.update(tomllib.loads(argv[i + 1])["permissions"]["ouro"])
                self.assertEqual(settings["extends"], ":read-only")
                self.assertFalse(settings["network"]["enabled"])
                self.assertEqual(settings["filesystem"], {
                    str(root): "write", **{str(root / p): "read" for p in tools.PROTECTED_NAMES}})
                self.assertIn("--include-managed-config", argv)
                self.assertEqual("-C" in argv, name == "nt")
                self.assertEqual(argv[-2:], ["python", "check.py"])

    def test_git_metadata_in_write_grant_refuses_before_command(self):
        with public_fixture("ouro-probe-git-overlap-") as folder:
            root = pathlib.Path(folder).resolve()
            admin = root / "shared-admin"
            admin.mkdir()
            (root / ".git").write_text("gitdir: shared-admin\n", encoding="utf-8")
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            with patch.object(tools.sys, "platform", "darwin"), patch.object(tools.process, "run") as launch:
                with self.assertRaisesRegex(sh.DriverError, "linked Git admin"):
                    bridge.run("python check.py")
                launch.assert_not_called()
            (root / ".git").unlink()
            (root / ".git").mkdir()
            (root / ".git/commondir").write_text("../shared-admin\n", encoding="utf-8")
            with patch.object(tools.sys, "platform", "darwin"), patch.object(tools.process, "run") as launch:
                with self.assertRaisesRegex(sh.DriverError, "common Git overlaps"):
                    bridge.run("python check.py")
                launch.assert_not_called()

    def test_alias_topologies_refuse_before_process(self):
        with public_fixture("ouro-probe-links-") as root:
            root = pathlib.Path(root)
            original = root / "input"
            original.write_bytes(b"preserve")
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            for alias in ("hard", "symbolic"):
                target = root / "alias"
                if alias == "hard":
                    os.link(original, target)
                else:
                    try:
                        target.symlink_to(original)
                    except OSError:
                        if os.name != "nt":
                            raise
                        continue  # Windows command PROBE is refused independently.
                with patch.object(tools.process, "run") as launch, patch.object(tools.sys, "platform", "darwin"):
                    with self.assertRaisesRegex(sh.DriverError, "symlink or hard link"):
                        bridge.run("python check.py")
                    launch.assert_not_called()
                target.unlink()
                self.assertEqual(original.read_bytes(), b"preserve")

    def test_policy_carries_probe_boundary_and_owned_temp(self):
        with public_fixture("ouro-probe-dispatch-") as root:
            root = pathlib.Path(root).resolve()
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            with patch.object(tools.sys, "platform", "darwin"), patch.object(sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]), patch.object(tools.process, "run", return_value=sh.R(0, "ready", "", [])) as launch, patch.object(tools, "probe_configuration"):
                self.assertEqual(bridge.run("python check.py")["exit"], 0)
            argv = launch.call_args.args[0]
            self.assertIn('permissions.ouro.extends=":read-only"', argv)
            for key in ("TMPDIR", "TEMP", "TMP"):
                self.assertEqual(launch.call_args.kwargs["env"][key], str(root))

    def test_linux_bind_mount_refuses_before_process(self):
        with public_fixture("ouro-probe-mount-") as root:
            root = pathlib.Path(root).resolve()
            (root / "nested").mkdir()
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            mountinfo = "1 0 0:1 / " + str(root / "nested") + " rw - tmpfs tmpfs rw"
            with patch.object(tools.sys, "platform", "linux"), patch.object(tools.process, "check_platform"), patch.object(pathlib.Path, "read_text", return_value=mountinfo), patch.object(tools.process, "run") as launch:
                with self.assertRaisesRegex(sh.DriverError, "contains a mount"):
                    bridge.run("python check.py")
                launch.assert_not_called()

    def test_probe_readiness_keeps_credential_removals_in_rpc_and_launch(self):
        with public_fixture("ouro-probe-environment-") as root:
            with patch.dict(os.environ, {"PUBLIC_REMOVED_CREDENTIAL": "private-value"}), patch.object(
                    sh, "argv_for", side_effect=lambda *a: list(a)), patch.object(
                    tools.process, "run", return_value=sh.R(0, "", "", [])) as launch, patch.object(
                    tools, "probe_configuration") as inspect:
                tools.git_readiness(root, env={"PUBLIC_REMOVED_CREDENTIAL": None}, probe=True)
            for call in inspect.call_args_list:
                self.assertIsNone(call.args[1]["PUBLIC_REMOVED_CREDENTIAL"])
                self.assertNotIn("PUBLIC_REMOVED_CREDENTIAL", tools.process.child_env(call.args[1]))
            for call in launch.call_args_list:
                self.assertIsNone(call.kwargs["env"]["PUBLIC_REMOVED_CREDENTIAL"])

    def test_git_readiness_uses_probe_boundary_without_changing_build(self):
        with public_fixture("ouro-probe-git-") as root:
            for probe in (False, True):
                with patch.object(sh, "argv_for", side_effect=lambda *a: [sys.executable if a[0] == "codex" else a[0], *a[1:]]), patch.object(tools.process, "run", return_value=sh.R(0, "", "", [])) as launch, patch.object(tools, "probe_configuration"):
                    tools.git_readiness(root, probe=probe)
                self.assertEqual(launch.call_count, 3)
                parent = ":read-only" if probe else ":workspace"
                for call in launch.call_args_list:
                    self.assertIn('permissions.ouro.extends="' + parent + '"', call.args[0])
                    self.assertIn("core.fsmonitor=false", call.args[0])

    def test_unproved_linux_boundary_refuses_without_launch(self):
        with public_fixture("ouro-probe-native-") as root:
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            with patch.object(tools.sys, "platform", "linux"), patch.object(tools.process, "check_platform", side_effect=sh.DriverError("WSL refused")), patch.object(tools.process, "run") as launch:
                with self.assertRaisesRegex(sh.DriverError, "WSL refused"):
                    bridge.run("python check.py")
                launch.assert_not_called()

    def test_unsupported_platform_refuses_without_launch(self):
        with public_fixture("ouro-probe-platform-") as root:
            bridge = tools.Bridge(dict(root=str(root), edit=False, commands=[], probe=True))
            with patch.object(tools.sys, "platform", "win32"), patch.object(tools.process, "run") as launch:
                with self.assertRaisesRegex(sh.DriverError, "native Linux or macOS"):
                    bridge.run("python check.py")
                launch.assert_not_called()
