"""Generated network controls: command transport, safe diagnostics and fail-closed policy."""
import os
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ouro import sh
from ouro.runtime import network


class NetworkReadinessTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="ouro-network-test-")
        self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)
        self.calls = []

    def invoke(self, result, *, windows=False, admission=None, positive=None):
        nonce = "a" * 48
        server = MagicMock()
        server.__enter__.return_value = server
        server.getsockname.return_value = ("127.0.0.1", 12345)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = nonce.encode()
        denied = network.socket.timeout()
        if admission is None:
            server.accept.side_effect = [(connection, None), denied]
        else:
            admitted = MagicMock()
            admitted.__enter__.return_value = admitted
            admitted.recv.return_value = admission
            server.accept.side_effect = [(connection, None), (admitted, None)]
        results = iter([positive or sh.R(0, "ouro-network-sent\n", "", []), result])
        def launch(argv, **kwargs):
            self.calls.append((argv, kwargs))
            item = next(results)
            if isinstance(item, Exception):
                raise item
            return item
        with patch.object(network, "WINDOWS", windows), patch.object(network.secrets, "token_hex", return_value=nonce), \
                patch.object(network.socket, "socket", return_value=server), \
                patch.object(network, "sandbox_argv", side_effect=lambda root, argv: ["fixture.CMD", *argv]), \
                patch.object(network.process, "run", side_effect=launch):
            network.readiness(self.root, env={"GENERATED_CONTROL": "yes"})

    def test_explicit_denial_keeps_posix_semantics_and_immutable_arguments(self):
        self.invoke(sh.R(0, "ouro-network-denied\n", "ouro-network-probe-started\n", []))
        self.assertEqual(len(self.calls), 2)
        for argv, options in self.calls:
            self.assertNotIn("\n", argv[-1])
            self.assertNotIn("\r", argv[-1])
            self.assertEqual(options, {"cwd": self.root, "timeout": 15, "env": {"GENERATED_CONTROL": "yes"}})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_or_unexpected_receipt_never_proves_denial_or_leaks_output(self):
        secret = "synthetic-wrapper-credential-DO-NOT-PRINT"
        for output, error, started, receipt in (("", "", "missing", "missing"),
                ("", "ouro-network-probe-started\n", "observed", "missing"),
                (secret, secret, "missing", "unexpected"),
                ("ouro-network-denied\n" + secret, secret, "missing", "unexpected")):
            with self.subTest(receipt=receipt, started=started), self.assertRaises(sh.DriverError) as caught:
                self.invoke(sh.R(0, output, error, []))
            message = str(caught.exception)
            self.assertIn("sandbox denial not established", message)
            for part in ("phase=restricted", "launcher=cmd", "exit=0", "probe-start=" + started,
                         "receipt=" + receipt, "stdout-bytes=", "stderr-bytes="):
                self.assertIn(part, message)
            self.assertNotIn(secret, message)

    def test_actual_host_admission_overrules_denial_claim_and_other_output(self):
        for admitted in (b"a" * 48, b""):
            with self.subTest(admitted=bool(admitted)), self.assertRaises(sh.DriverError) as caught:
                self.invoke(sh.R(0, "ouro-network-denied", "synthetic-secret", []), admission=admitted)
            self.assertIn("sandbox admitted host loopback", str(caught.exception))
            self.assertIn("host received generated nonce" if admitted else "host accepted a TCP connection", str(caught.exception))
            self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_probe_failures_and_bad_positive_control_are_distinct_and_safe(self):
        for failure, expected in ((sh.DriverError("runtime timed out: synthetic-secret"), "failure=timeout"),
                (sh.DriverError("runtime output limit exceeded: synthetic-secret"), "failure=output-limit"),
                (sh.DriverError("cannot start runtime: synthetic-secret"), "failure=startup"),
                (sh.DriverError("runtime process exit not established: synthetic-secret"), "failure=exit-proof"),
                (sh.DriverError("synthetic-secret"), "failure=process")):
            with self.subTest(expected=expected), self.assertRaises(sh.DriverError) as caught:
                self.invoke(failure)
            self.assertIn("restricted probe failed", str(caught.exception))
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn("synthetic-secret", str(caught.exception))
        with self.assertRaises(sh.DriverError) as caught:
            self.invoke(sh.R(9, "ouro-network-denied", "synthetic-secret", []))
        self.assertIn("exit=9", str(caught.exception))
        self.assertIn("sandbox denial not established", str(caught.exception))
        self.assertNotIn("synthetic-secret", str(caught.exception))
        self.calls.clear()
        with self.assertRaises(sh.DriverError) as caught:
            self.invoke(sh.R(0, "ouro-network-denied", "", []), positive=sh.R(0, "synthetic-secret", "", []))
        self.assertIn("unrestricted nonce control failed", str(caught.exception))
        self.assertIn("phase=unrestricted", str(caught.exception))
        self.assertNotIn("synthetic-secret", str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    def test_windows_denial_keeps_unsupported_policy_refusal(self):
        with self.assertRaises(sh.DriverError) as caught:
            self.invoke(sh.R(0, "ouro-network-denied", "ouro-network-probe-started", []), windows=True)
        message = str(caught.exception)
        self.assertIn("Windows sandbox implementation/policy is not established", message)
        self.assertIn("loopback denial alone cannot certify disabled networking", message)
        self.assertIn("receipt=denied", message)

    @unittest.skipUnless(os.name == "nt", "actual Windows CMD argument transport")
    def test_real_cmd_wrapper_executes_encoded_probe_and_admission_still_refuses(self):
        wrapper = self.root / "generated-wrapper.cmd"
        wrapper.write_text("@echo off\n%*\n", encoding="utf-8")
        # This generated launcher has the npm wrapper's raw %* argument forwarding.
        # It enforces no boundary: receipt at the real listener must always refuse.
        with patch.object(network, "sandbox_argv", side_effect=lambda root, argv: [str(wrapper), *argv]):
            with self.assertRaisesRegex(sh.DriverError, "host received generated nonce"):
                network.readiness(self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
