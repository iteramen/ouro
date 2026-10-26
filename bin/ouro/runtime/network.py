"""Bounded, generated-only proof of the requested worker network boundary."""
import base64
from pathlib import Path
import secrets
import socket
import sys

from .. import sh
from . import process
from .tools import sandbox_argv

WINDOWS = sys.platform == "win32" or sys.platform.startswith(("cygwin", "msys"))
START = "ouro-network-probe-started"


def launcher(phase, argv):
    return ("python" if phase == "unrestricted" else
            "cmd" if Path(argv[0]).suffix.lower() in (".cmd", ".bat") else
            "powershell-script" if "-File" in argv[:4] else "executable")


def evidence(phase, argv, result):
    """Describe only generated markers and sizes, never arbitrary wrapper output."""
    receipt = {"ouro-network-sent": "sent", "ouro-network-denied": "denied"}.get(result.out.strip(),
                "missing" if not result.out.strip() else "unexpected")
    return (f"phase={phase}; launcher={launcher(phase, argv)}; exit={result.code}; "
            f"probe-start={'observed' if START in result.err.splitlines() else 'missing'}; receipt={receipt}; "
            f"stdout-bytes={len(result.out.encode('utf-8'))}; stderr-bytes={len(result.err.encode('utf-8'))}")


def readiness(root, env=None):
    """Require a working host control and explicit sandbox denial, never a timeout."""
    remedy = ("; this host cannot establish the disabled-network worker contract. "
              "Diagnose the configured Codex sandbox implementation and supported policy; "
              "use a host that enforces it (for example native Linux/WSL), then rerun readiness. "
              "Ouro will not switch implementations or run sandbox setup")
    try:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(2)
            server.settimeout(0.5)
            port = server.getsockname()[1]
            for restricted in (False, True):
                nonce = secrets.token_hex(24)
                script = (
                    "import socket,sys\n"
                    f"print({START!r}, file=sys.stderr, flush=True)\n"
                    "try:\n"
                    f" s = socket.create_connection(('127.0.0.1', {port}), timeout=3)\n"
                    f" s.sendall({nonce.encode()!r})\n"
                    " s.close()\n"
                    "except OSError as ex:\n"
                    " if ex.errno not in (1, 13, 10013): raise\n"
                    " print('ouro-network-denied')\n"
                    "else: print('ouro-network-sent')\n"
                )
                # A Windows CMD launcher can silently truncate a literal multiline -c
                # argument. Encode this generated code, without changing CLI resolution.
                payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
                argv = [sys.executable, "-c", f"import base64;exec(base64.b64decode('{payload}'))"]
                argv = sandbox_argv(root, argv) if restricted else argv
                phase = "restricted" if restricted else "unrestricted"
                try:
                    result = process.run(argv, cwd=root, timeout=15, env=env)
                except sh.DriverError as ex:
                    failure = next((name for text, name in (("timed out", "timeout"), ("output limit", "output-limit"),
                                   ("cannot start", "startup"), ("exit", "exit-proof")) if text in str(ex)), "process")
                    raise sh.DriverError(f"Network capability readiness: {phase} probe failed "
                                         f"(launcher={launcher(phase, argv)}; failure={failure})") from ex
                detail = evidence(phase, argv, result)
                received, accepted = b"", False
                try:
                    connection, _ = server.accept()
                    accepted = True
                    with connection:
                        connection.settimeout(0.5)
                        while len(received) < len(nonce):
                            data = connection.recv(len(nonce) - len(received))
                            if not data:
                                break
                            received += data
                except socket.timeout:
                    pass
                if not restricted:
                    if result.code != 0 or received != nonce.encode() or result.out.strip() != "ouro-network-sent":
                        raise sh.DriverError("Network capability readiness: unrestricted nonce control failed (" + detail + ")")
                elif accepted:
                    admission = "host received generated nonce" if received == nonce.encode() else "host accepted a TCP connection"
                    raise sh.DriverError("Network capability readiness: sandbox admitted host loopback (" + admission + "; " + detail + ")")
                elif result.code != 0 or result.out.strip() != "ouro-network-denied":
                    raise sh.DriverError("Network capability readiness: sandbox denial not established "
                                         "(" + detail + ")")
            if WINDOWS:
                raise sh.DriverError("Network capability readiness: Windows sandbox implementation/policy "
                                     "is not established; loopback denial alone cannot certify disabled networking (" + detail + ")")
    except (OSError, sh.DriverError) as ex:
        detail = str(ex) if isinstance(ex, sh.DriverError) else "Network capability readiness: host control unavailable: " + str(ex)
        raise sh.DriverError(detail + remedy) from ex
