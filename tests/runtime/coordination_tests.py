"""Loopback-only MCP fixtures and real subprocess credential-boundary controls."""
import contextlib
import importlib.util
import http.server
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from ouro import coordination as co, developer, sh
from ouro.worktrees import Tree
from ouro.runtime import READ, claude, codex, process

KEY = "fixture-board-secret-sentinel"
VARIABLE = "ouro_test_board_key"
# Server contract, deliberately independent of the client allowlist.
BOARD_TOOLS = ("board_search", "board_list_threads", "board_read_thread",
               "board_post", "board_reply", "board_update_thread")


@contextlib.contextmanager
def board(mode="json"):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((request, dict(self.headers)))
                method = request["method"]
                if mode == "slow_steps":
                    time.sleep(0.06)
                if mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", self.server.endpoint + "/credential-destination")
                    self.end_headers()
                    return
                if mode == "auth" or (mode == "auth_call" and method == "tools/call"):
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(KEY.encode())
                    return
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {"protocolVersion": co.PROTOCOL, "capabilities": {"tools": {}},
                              "serverInfo": {"name": "fixture-board", "version": "1"}}
                    if mode == "malformed_init":
                        del result["serverInfo"]
                    if mode == "bad_init":
                        result["protocolVersion"] = "unsupported"
                elif method == "tools/list":
                    result = {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in BOARD_TOOLS]}
                    if mode == "incomplete":
                        result["nextCursor"] = "same-page"
                    elif mode == "unending":
                        result["nextCursor"] = str(len(requests))
                    elif mode == "malformed_tools":
                        result = {"tools": [{"name": "board_search"}]}
                    elif mode == "missing_tool":
                        result = {"tools": []}
                    elif mode == "paginated" and not request["params"].get("cursor"):
                        result = {"tools": [], "nextCursor": "last"}
                else:
                    result = {"content": [{"type": "text", "text": "board text is data"}],
                              "structuredContent": {"received": request["params"]}}
                    if mode == "tool_error":
                        result["isError"] = True
                    elif mode in ("json_tool_error", "structured_tool_error", "mixed_tool_error"):
                        failure = {"error": KEY + self.server.endpoint}
                        if mode == "structured_tool_error":
                            result["structuredContent"] = failure
                        else:
                            result["content"] = [{"type": "text", "text": json.dumps(failure)}]
                            if mode == "mixed_tool_error":
                                result["content"].insert(0, {"type": "text", "text": "untrusted text"})
                    elif mode == "malformed_content":
                        result["content"] = [{"type": "text", "text": 42}]
                envelope = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                if mode == "wrong_id":
                    envelope["id"] = 999
                if mode == "rpc_error":
                    envelope = {"jsonrpc": "2.0", "id": request["id"], "error": {"message": KEY}}
                payload = json.dumps(envelope).encode()
                kind = "application/json"
                if mode in ("sse", "fragmented", "sse_cr", "sse_crlf", "fragmented_cr", "fragmented_crlf"):
                    kind = "text/event-stream"
                    first, rest = payload.split(b",", 1)
                    payload = (b': keepalive\n\ndata: {"jsonrpc":"2.0","method":"notice"}\n\n'
                               + b"data: " + first + b",\ndata: " + rest + b"\n\n")
                    if mode.endswith("_cr"):
                        payload = payload.replace(b"\n", b"\r")
                    elif mode.endswith("_crlf"):
                        payload = payload.replace(b"\n", b"\r\n")
                if mode == "malformed":
                    payload = KEY.encode()
                if mode in ("oversized", "unframed_oversized", "oversized_sse"):
                    payload = b"x" * (co.LIMIT + 1)
                    if mode == "oversized_sse":
                        kind = "text/event-stream"
                if mode == "slow_headers":
                    self.wfile.write(b"HTTP/1.0 200 OK\r\n")
                    self.wfile.flush()
                    for _ in range(100):
                        self.wfile.write(b"X-Drip: x\r\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                    self.wfile.write(b"\r\n")
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                if mode not in ("unframed_oversized", "oversized_sse"):
                    self.send_header("Content-Length", str(len(payload)))
                self.send_header("Mcp-Session-Id", "fixture-session")
                self.end_headers()
                slow = mode == "slow_body" or (mode == "slow_call" and method == "tools/call")
                step = 1 if mode.startswith("fragmented") else (3 if slow else len(payload))
                for offset in range(0, len(payload), step):
                    self.wfile.write(payload[offset:offset + step])
                    self.wfile.flush()
                    if mode.startswith("fragmented") or slow:
                        time.sleep(0.0002 if mode.startswith("fragmented") else 0.1)
            except (OSError, ValueError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.endpoint = f"http://127.0.0.1:{server.server_port}/mcp"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.endpoint, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class CoordinationTests(unittest.TestCase):
    def test_prepared_scope_controls_without_sockets(self):
        # Reuse the driver suite's offline adapter controls so sandboxed workers
        # can verify them even when the complete driver exceeds its run bound.
        path = pathlib.Path(__file__).resolve().parents[1] / "driver" / "coordination_cases.py"
        spec = importlib.util.spec_from_file_location("prepared_cases", path)
        cases = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cases)
        with tempfile.TemporaryDirectory(prefix="ouro-prepared-") as root:
            from ouro import run as runmod
            host = SimpleNamespace(TMP=pathlib.Path(root), runmod=runmod,
                                   check=lambda name, result: self.assertTrue(result, name))
            with patch.object(runmod, "argv_for", side_effect=lambda *args: [str(a) for a in args]):
                cases.prepared_offline(host)
                cases.resume_offline(host)


    def config(self, endpoint="https://board.invalid/mcp", **kw):
        return co.Coordination.parse({"endpoint": endpoint, "person": "fixture person", "forum": "fixture forum", **kw})

    def test_strict_developer_coordination_and_legacy_defaults(self):
        with tempfile.TemporaryDirectory(prefix="ouro-coordination-config-") as directory:
            path = pathlib.Path(directory) / "developer.toml"

            def load(text):
                path.write_text(text, encoding="utf-8")
                return developer.load(path)

            legacy = load('[runtime]\nbackend="codex"\n')
            self.assertIsNone(legacy.coordination)
            valid = '[coordination]\nendpoint="https://board.invalid/mcp"\nperson="fixture person"\nforum="fixture forum"\n'
            settings = load(valid)
            self.assertEqual(settings.backend, "claude")
            self.assertEqual(settings.coordination.credential_env, "MOMENTUS_API_KEY")
            self.assertEqual(load(valid + 'credential_env="CUSTOM_BOARD_KEY"\n').coordination.credential_env, "CUSTOM_BOARD_KEY")
            for invalid in ("", "[ship]\npolicy='merge-on-green'", "[coordination]\n",
                            valid + 'key="not-allowed"', valid + 'budget=30',
                            valid + '[repo]\nslug="fixture/project"', '[runtime]\nbackend="codex"\n[coordination]\nforum="x"'):
                with self.subTest(invalid=invalid), self.assertRaises(sh.DriverError):
                    load(invalid)
            # The installer validates and preserves a coordination-bearing developer file.
            spec = importlib.util.spec_from_file_location(
                "coordination_setup", pathlib.Path(__file__).resolve().parents[2] / "bin" / "ouro-developer.py")
            setup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(setup)
            home = pathlib.Path(directory) / "home"
            installed = home / ".config/ouro/developer.toml"
            installed.parent.mkdir(parents=True)
            installed.write_text(valid, encoding="utf-8")
            self.assertEqual(setup.main(["install", "--home", str(home)]), 0)
            self.assertEqual(installed.read_text(encoding="utf-8"), valid)

        for endpoint in ("http://board.invalid", "https://user:secret@board.invalid", "https://board.invalid?key=secret",
                         "https://board.invalid/#secret", "https://board.invalid:0", "https://board.invalid:bad",
                         "https://board.invalid/\nsecret", "file:///tmp/board", "https://"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError) as error:
                self.config(endpoint)
            self.assertNotIn("secret", str(error.exception))
        for endpoint in ("http://localhost:123/mcp", "http://127.0.0.1:123/mcp", "http://[::1]:123/mcp"):
            self.config(endpoint)
        for kw in ({"credential_env": ""}, {"credential_env": "key=value"}, {"person": ""}, {"forum": []}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.config(**kw)

    def test_disabled_and_missing_key_make_no_network_calls_or_writes(self):
        with patch.object(co.http.client.HTTPConnection, "request") as http, patch.object(co.http.client.HTTPSConnection, "request") as https:
            self.assertFalse(co.Board(None, {}).call("board_search", {}).available)
            reply = co.Board(self.config(), {}).call("board_post", {"title": "not sent"})
            self.assertFalse(reply.available)
            self.assertIn("credential missing", reply.notice)
            http.assert_not_called()
            https.assert_not_called()

    def test_json_sse_fragments_pagination_and_session_headers(self):
        for mode in ("json", "sse", "fragmented", "sse_cr", "sse_crlf", "fragmented_cr", "fragmented_crlf", "paginated"):
            with self.subTest(mode=mode), board(mode) as (endpoint, requests):
                client = co.Board(self.config(endpoint), {"MOMENTUS_API_KEY": KEY})
                arguments = {"forum": "fixture forum", "query": "Claimed: fixture/project#1"}
                reply = client.call("board_search", arguments)
                self.assertTrue(reply.available, reply.notice)
                self.assertEqual(reply.value["structuredContent"]["received"], {"name": "board_search", "arguments": arguments})
                self.assertEqual([r["method"] for r, _ in requests][:3], ["initialize", "notifications/initialized", "tools/list"])
                headers = {k.lower(): v for k, v in requests[-1][1].items()}
                self.assertEqual(headers["mcp-session-id"], "fixture-session")
                self.assertEqual(headers["mcp-protocol-version"], co.PROTOCOL)
                self.assertEqual(headers["authorization"], "Bearer " + KEY)
                self.assertIn("text/event-stream", headers["accept"])
                for name in BOARD_TOOLS:
                    arguments = {"forum": "fixture forum", "status": "open", "limit": 100} if name == "board_list_threads" else {"fixture": name}
                    reply = client.call(name, arguments)
                    self.assertTrue(reply.available, reply.notice)
                    self.assertEqual(requests[-1][0]["params"], {"name": name, "arguments": arguments})
                self.assertIn("unsupported tool", client.call("list_threads", {}).notice)
                self.assertEqual(sum(r["method"] == "initialize" for r, _ in requests), 1)

    def test_claim_body_roundtrip_and_redacted_conflict_ids(self):
        with tempfile.TemporaryDirectory(prefix="ouro-claim-body-") as directory:
            config = self.config(person="person --> suffix", credential_env=VARIABLE)
            root = pathlib.Path(directory)
            env = SimpleNamespace(runtime=developer.Settings(backend="codex", coordination=config),
                                  dry=False, main=root, slug="fixture/project",
                                  viewer=lambda: "github-actor",
                                  git=lambda *a, **kw: SimpleNamespace(out="a" * 40))
            run = SimpleNamespace(env=env, n=1, k=None, branch="area/change", tree=SimpleNamespace(path=root / "absent"),
                                  options=SimpleNamespace(override_claim=False))
            claim = co.Claim(run)
            claim.data = dict(identity=dict(person=config.person, session="repository-session"),
                              correlation="run-correlation")
            body = claim.body("waiting")
            detail = dict(messages=[dict(body=body)])
            self.assertTrue(claim.owns(detail))
            self.assertEqual(json.loads(co.MARK.search(body)[1])["identity"]["person"], config.person)
            with patch.dict(os.environ, {VARIABLE: KEY}):
                diagnostic = claim.safe("holder " + KEY + " https://user:password@board.invalid")
                self.assertNotIn(KEY, diagnostic)
                self.assertNotIn("password", diagnostic)
                with patch.object(claim, "identity"), patch.object(claim, "discover",
                        return_value=([], [dict(thread=KEY, holder="holder")], False)):
                    with self.assertRaises(sh.Refused) as error:
                        claim.enter()
                self.assertNotIn(KEY, str(error.exception))
                self.assertIn("holder", str(error.exception))

    def test_transport_controls_without_network(self):
        # Exercise the real RPC parser with deterministic single-byte reads, including
        # CRLF split across reads; the loopback fixtures separately cover actual HTTP.
        for ending in (None, b"\n", b"\r", b"\r\n"):
            for failure in (None, "text", "structured"):
                with self.subTest(ending=ending, failure=failure):
                    requests = []
                    class Connection:
                        def __init__(self, *args, **kwargs):
                            self.sock = SimpleNamespace(settimeout=lambda timeout: None)
                        def connect(self):
                            pass
                        def request(self, method, path, body, headers):
                            self.request_body = json.loads(body)
                            requests.append(self.request_body)
                        def getresponse(self):
                            request = self.request_body
                            method = request["method"]
                            if method == "initialize":
                                result = {"protocolVersion": co.PROTOCOL, "capabilities": {},
                                          "serverInfo": {"name": "fixture", "version": "1"}}
                            elif method == "tools/list":
                                result = {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in BOARD_TOOLS]}
                            else:
                                value = {"error": KEY} if failure else {"threads": []}
                                result = {"content": [{"type": "text", "text": json.dumps(value)}]}
                                if failure == "structured":
                                    result = {"content": [], "structuredContent": value}
                            payload = json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}).encode()
                            if ending:
                                payload = b"data: " + payload + ending + ending
                            pieces = iter(bytes([byte]) for byte in payload)
                            return SimpleNamespace(
                                status=202 if method == "notifications/initialized" else 200,
                                getheader=lambda name, default=None: {
                                    "Content-Type": "text/event-stream" if ending else "application/json",
                                    "Mcp-Session-Id": "fixture-session"}.get(name, default),
                                read1=lambda size: next(pieces, b""), close=lambda: None)
                        def close(self):
                            pass
                    with patch.object(co.http.client, "HTTPConnection", Connection):
                        arguments = {"forum": "fixture", "status": "open", "limit": 100}
                        reply = co.Board(self.config("http://127.0.0.1/mcp"), {"MOMENTUS_API_KEY": KEY}).call(
                            "board_list_threads", arguments)
                    self.assertEqual(reply.available, failure is None, reply.notice)
                    self.assertEqual(len(requests), 4)
                    self.assertEqual(requests[-1]["params"], {"name": "board_list_threads", "arguments": arguments})
                    if failure:
                        self.assertIsNone(reply.value)
                        self.assertIn("tool failure", reply.notice)
                        self.assertNotIn(KEY, reply.notice)

    def test_unavailable_replies_are_bounded_sanitized_and_never_empty_evidence(self):
        for mode in ("redirect", "auth", "auth_call", "malformed", "oversized", "wrong_id", "rpc_error", "bad_init",
                     "malformed_init", "malformed_tools", "malformed_content", "missing_tool", "tool_error", "incomplete", "unending",
                     "unframed_oversized", "oversized_sse", "json_tool_error", "structured_tool_error", "mixed_tool_error"):
            with self.subTest(mode=mode), board(mode) as (endpoint, requests):
                reply = co.Board(self.config(endpoint), {"MOMENTUS_API_KEY": KEY}).call("board_search", {})
                self.assertFalse(reply.available)
                self.assertIsNone(reply.value)
                self.assertTrue(reply.notice)
                self.assertLess(len(reply.notice), 150)
                self.assertNotIn(KEY, reply.notice)
                self.assertNotIn(endpoint, reply.notice)
                if mode == "redirect":
                    self.assertEqual(len(requests), 1, "redirect must never forward credentials")
                if mode in ("incomplete", "unending"):
                    self.assertNotIn("tools/call", [r["method"] for r, _ in requests])
                    self.assertIn("incomplete discovery", reply.notice)

    def test_absolute_budget_includes_slow_headers_body_and_initialization(self):
        self.assertEqual(co.Board(None, budget=99).budget, 3)
        for mode in ("slow_headers", "slow_body", "slow_call"):
            with self.subTest(mode=mode), board(mode) as (endpoint, requests):
                start = time.monotonic()
                reply = co.Board(self.config(endpoint), {"MOMENTUS_API_KEY": KEY}, budget=0.15).call("board_post", {})
                self.assertLess(time.monotonic() - start, 2)  # each stall runs for seconds
                self.assertFalse(reply.available)
                self.assertIn("timeout", reply.notice)
                time.sleep(0.1)
                self.assertEqual(any(r["method"] == "tools/call" for r, _ in requests), mode == "slow_call")
        with board("slow_steps") as (endpoint, requests):
            reply = co.Board(self.config(endpoint), {"MOMENTUS_API_KEY": KEY}, budget=0.15).call("board_search", {})
            self.assertIn("timeout", reply.notice)
            time.sleep(0.1)
            self.assertNotIn("tools/call", [r["method"] for r, _ in requests])
        # A late connection must not send headers or a mutation after cancellation.
        original = co.http.client.HTTPConnection.connect
        def delayed(connection):
            time.sleep(0.2)
            original(connection)
        with board() as (endpoint, requests), patch.object(co.http.client.HTTPConnection, "connect", delayed):
            client = co.Board(self.config(endpoint), {"MOMENTUS_API_KEY": KEY}, budget=0.03)
            self.assertIn("timeout", client.call("board_post", {}).notice)
            time.sleep(0.3)
            self.assertEqual(requests, [])
            self.assertFalse(client.lock.locked())
        # A blocked resolver is bounded too; repeated calls cannot create more workers.
        release = threading.Event()
        with patch.object(co.Board, "rpc", side_effect=lambda *a, **k: release.wait(1)):
            client = co.Board(self.config(), {"MOMENTUS_API_KEY": KEY}, budget=0.02)
            self.assertIn("timeout", client.call("board_search", {}).notice)
            self.assertIn("busy", client.call("board_search", {}).notice)
            release.set()

    def test_configured_credential_absent_from_real_workers_and_bridge_children(self):
        with tempfile.TemporaryDirectory(prefix="ouro-credential-workers-") as directory:
            root = pathlib.Path(directory).resolve()
            wrapper = root / "bridge_wrapper.py"
            assertion = (
                "import os, pathlib, sys\n"
                f"assert not any(k.lower() == {VARIABLE!r} or v == {KEY!r} for k,v in os.environ.items()), 'credential inherited'\n"
                "assert os.environ['OURO_TEST_OTHER'] == 'preserved'\n")
            wrapper.write_text(assertion +
                "import runpy\npathlib.Path('bridge-seen').write_text('safe')\n"
                "sys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name='__main__')\n", encoding="utf-8")
            worker = root / "worker.py"
            worker.write_text(assertion + """
import json, subprocess, tomllib
backend = sys.argv[1]
pathlib.Path('worker-' + backend).write_text('safe')
if backend == 'claude':
    subprocess.run([sys.executable, '-c', "import os; assert not any(k.lower() == 'ouro_test_board_key' for k in os.environ)"], check=True)
    print(json.dumps({"session_id":"fixture-claude", "structured_output":{"finding":"safe"}}))
else:
    config = [a for a in sys.argv if a.startswith('mcp_servers=')][-1]
    bridge = tomllib.loads(config)['mcp_servers']['ouro']
    request = {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list","arguments":{}}}
    result = subprocess.run([bridge['command'], *bridge['args']], input=json.dumps(request)+'\\n',
                            encoding='utf-8', capture_output=True, check=True)
    assert not json.loads(result.stdout).get('result', {}).get('isError'), 'bridge failed'
    target = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
    target.write_text(json.dumps({"finding":"safe","stop":None}))
    print(json.dumps({"type":"thread.started","thread_id":"fixture-codex"}))
    print(json.dumps({"type":"turn.completed","usage":{}}))
""", encoding="utf-8")

            def argv_for(name, *args):
                return [sys.executable, str(wrapper), *args] if name == "python3" else [sys.executable, str(worker), name, *args]

            cfg = developer.Settings(coordination=self.config(credential_env=VARIABLE))
            with patch.dict(os.environ, {VARIABLE: KEY, "OURO_TEST_OTHER": "preserved"}), patch.object(sh, "argv_for", side_effect=argv_for), patch.object(sh, "which", return_value=sys.executable):
                for backend in (claude, codex):
                    answer, _, _, _ = backend.execute("CHECKPOINT", "public fixture", "default", READ, root, 10, None, {}, cfg)
                    self.assertEqual(answer["finding"], "safe")
                self.assertEqual(os.environ[VARIABLE], KEY, "invoking coordinator retains its key")
            for marker in ("worker-claude", "worker-codex", "bridge-seen"):
                self.assertEqual((root / marker).read_text(), "safe")

    @unittest.skipIf(os.name == "nt", "POSIX executable wrappers exercise actual discovery and ps children")
    def test_discovery_uses_one_loaded_profile_and_filtered_git_children(self):
        from ouro import cli
        with tempfile.TemporaryDirectory(prefix="ouro-discovery-key-") as directory:
            root = pathlib.Path(directory).resolve()
            real_git = sh.which("git")
            for args in (("init", "-q"), ("config", "user.name", "Fixture"),
                         ("config", "user.email", "fixture@example.invalid")):
                subprocess.run([real_git, *args], cwd=root, check=True, capture_output=True)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "test.yml").write_text("on: [pull_request]\njobs: {}\n", encoding="utf-8")
            subprocess.run([real_git, "add", "."], cwd=root, check=True)
            subprocess.run([real_git, "commit", "-qm", "fixture"], cwd=root, check=True)
            subprocess.run([real_git, "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=root, check=True)
            profile = root / "developer.toml"
            profile.write_text('[coordination]\nendpoint="https://board.invalid/mcp"\nperson="fixture"\nforum="fixture"\ncredential_env="' + VARIABLE + '"\n', encoding="utf-8")
            tools = root / "tools"
            tools.mkdir()
            wrapper = tools / "git"
            wrapper.write_text("#!" + sys.executable + "\nimport os,pathlib,sys\n"
                               + f"assert {VARIABLE!r} not in os.environ\n"
                               + "assert os.environ['OURO_TEST_OTHER'] == 'preserved'\n"
                               + f"with pathlib.Path({str(root / 'git-seen')!r}).open('a') as stream: stream.write('safe\\n')\n"
                               + f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n", encoding="utf-8")
            wrapper.chmod(0o755)
            ob = sh.load_binding_module()
            data = {"repo": {"slug": "fixture/repo", "default_branch": "main"}}
            previous = pathlib.Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {VARIABLE: KEY, "OURO_TEST_OTHER": "preserved",
                                             "PATH": str(tools) + os.pathsep + os.environ["PATH"]}), \
                        patch.dict(sh._RESOLVED, {"git": str(wrapper)}), \
                        patch.object(cli.sh, "load_binding_module", return_value=ob), \
                        patch.object(ob, "load", return_value=data), \
                        patch.object(ob, "validate", return_value=[]), \
                        patch.object(developer, "load", wraps=developer.load) as load:
                    env = cli.bind(False, profile)
                    try:
                        self.assertEqual(env.main, root)
                        self.assertTrue(env.pr_checks())
                        self.assertTrue(env.pr_checks())
                        self.assertEqual(load.call_count, 1)
                        self.assertEqual(os.environ[VARIABLE], KEY)
                    finally:
                        env.close()
                self.assertGreaterEqual(len((root / "git-seen").read_text().splitlines()), 3)
            finally:
                os.chdir(previous)

    @unittest.skipIf(os.name == "nt", "POSIX ps helper and runtime lifecycle")
    def test_lifecycle_helpers_remove_key_in_initial_poll_and_cleanup(self):
        from ouro import validation
        with tempfile.TemporaryDirectory(prefix="ouro-lifecycle key-") as directory:
            root = pathlib.Path(directory).resolve()
            real_ps = sh.which("ps")
            tools = root / "tools"
            tools.mkdir()
            wrapper = tools / "ps"
            wrapper.write_text("#!" + sys.executable + "\nimport os,pathlib,sys\n"
                               + f"assert {VARIABLE!r} not in os.environ\n"
                               + "assert os.environ['OURO_TEST_OTHER'] == 'preserved'\n"
                               + f"with pathlib.Path({str(root / 'ps-seen')!r}).open('a') as stream: stream.write('safe\\n')\n"
                               + f"os.execv({real_ps!r}, [{real_ps!r}, *sys.argv[1:]])\n", encoding="utf-8")
            wrapper.chmod(0o755)
            cfg = developer.Settings(coordination=self.config(credential_env=VARIABLE))
            with patch.dict(os.environ, {VARIABLE: KEY, "OURO_TEST_OTHER": "preserved",
                                         "PATH": str(tools) + os.pathsep + os.environ["PATH"]}):
                exclusions = process.worker_env(cfg)
                result = process.run([sys.executable, "-c", "import time; time.sleep(.12)"], cwd=root, env=exclusions)
                self.assertEqual(result.code, 0, result.text)
                # Component test only: a harmless child, no caller receipt or claim of WSL support.
                with patch.object(validation, "check_platform", return_value=None):
                    probe = root / "wait.py"
                    probe.write_text("import time\ntime.sleep(.12)\n", encoding="utf-8")
                    code = validation.execute('"' + sys.executable + '" "' + str(probe) + '"',
                                              root, root / "validation.log", cfg)
                self.assertEqual(code, 0, (root / "validation.log").read_text())
                command_tools = root / "command-tools"
                command_tools.mkdir()
                with patch.object(validation, "check_platform", return_value=None):
                    command = shlex.join(["PATH=" + str(command_tools), sys.executable, str(probe)])
                    self.assertEqual(validation.execute(command, root, root / "restricted-path.log", cfg), 0)
                    fake_ps = command_tools / "ps"
                    fake_ps.write_text("#!" + sys.executable + "\nimport pathlib\n"
                                       + f"pathlib.Path({str(root / 'command-selected-observer')!r}).write_text('unsafe')\n", encoding="utf-8")
                    fake_ps.chmod(0o755)
                    self.assertEqual(validation.execute(command, root, root / "spoof-path.log", cfg), 0)
                self.assertFalse((root / "command-selected-observer").exists())
                self.assertEqual(os.environ[VARIABLE], KEY)
            self.assertGreaterEqual(len((root / "ps-seen").read_text().splitlines()), 6)

    def test_coordinator_overrides_cannot_restore_removed_case_alias(self):
        variable = "OURO_OVERRIDE_KEY"
        with tempfile.TemporaryDirectory(prefix="ouro-credential-alias-") as directory:
            root = pathlib.Path(directory).resolve()
            profile = root / "developer.toml"
            profile.write_text('[coordination]\nendpoint="https://board.invalid/mcp"\nperson="fixture"\nforum="fixture"\ncredential_env="' + variable + '"\n', encoding="utf-8")
            env = sh.Env(root, None, {"repo": {"slug": "fixture/repo", "default_branch": "main"}}, False, profile)
            try:
                script = ("import json,os; print(json.dumps({"
                          + "'configured': " + repr(variable) + " in os.environ, "
                          + "'alias': " + repr(variable.lower()) + " in os.environ, "
                          + "'other': os.environ.get('OURO_TEST_OTHER') == 'preserved'}))")
                with patch.dict(os.environ, {variable: KEY}):
                    for order, overrides in (
                            ("configured-first", {variable: "unused", variable.lower(): KEY}),
                            ("configured-last", {variable.lower(): KEY, variable: "unused"})):
                        with self.subTest(order=order):
                            result = env.run([sys.executable, "-c", script],
                                             env={**overrides, "OURO_TEST_OTHER": "preserved"})
                            self.assertEqual(result.code, 0, result.text)
                            self.assertEqual(json.loads(result.out),
                                             {"configured": False, "alias": os.name != "nt", "other": True})
                    self.assertEqual(os.environ[variable], KEY, "coordinator retains its key")
            finally:
                env.close()

    def test_repository_children_and_validation_receipts(self):
        from ouro import execution, run as driver, validation
        try:
            validation.check_platform()
            lifecycle_available = True
        except sh.DriverError:
            lifecycle_available = False
        for variable in (VARIABLE, "OURO_CUSTOM_COORDINATION_KEY"):
            for enabled in (False, True):
                with self.subTest(variable=variable, enabled=enabled), tempfile.TemporaryDirectory(prefix="ouro-repo-credential-") as directory:
                    root = pathlib.Path(directory).resolve()
                    repo = root / "repo"
                    repo.mkdir()
                    profile = root / "developer.toml"
                    profile.write_text(('[coordination]\nendpoint="https://board.invalid/mcp"\nperson="fixture"\nforum="fixture"\ncredential_env="' + variable + '"\n') if enabled else '[runtime]\nbackend="claude"\n', encoding="utf-8")
                    probe = repo / "probe.py"
                    probe.write_text(
                        "import os, pathlib, sys\n"
                        + f"present = any(k.casefold() == {variable.casefold()!r} or v == {KEY!r} for k,v in os.environ.items())\n"
                        + f"assert present == {not enabled!r}, 'credential boundary'\n"
                        + "assert os.environ['OURO_TEST_OTHER'] == 'preserved'\n"
                        + "pathlib.Path('seen-' + sys.argv[1]).write_text('safe')\n"
                        + "sys.exit(1 if sys.argv[1] == 'failure' else 0)\n", encoding="utf-8")
                    (repo / ".gitignore").write_text("seen-*\n", encoding="utf-8")
                    def raw_git(*args):
                        return subprocess.run([sh.which("git"), *args], cwd=repo, check=True, capture_output=True)
                    raw_git("init", "-q")
                    raw_git("config", "user.name", "Fixture")
                    raw_git("config", "user.email", "fixture@example.invalid")
                    raw_git("add", ".")
                    raw_git("commit", "-qm", "fixture")
                    ob = SimpleNamespace(OURO_TOKEN="{ouro}", plugin_root=lambda: None,
                                         lookup=lambda data, key: data[key])
                    cmd = lambda mode: shlex.join([sys.executable, str(probe), mode])
                    data = {"repo": {"slug": "fixture/repo", "default_branch": "main"}, "repo.setup": cmd("setup")}
                    env = sh.Env(repo, ob, data, False, profile)
                    self.addCleanup(env.close)
                    run = driver.Run.__new__(driver.Run)
                    run.env, run.tree, run.flow, run.in_lane = env, Tree(env, repo, None), "full", False
                    run.caller_execution_results, run.n, run.k = [], 1, None
                    run.select_gates = lambda flow: ([(cmd("gate"), None)], [])
                    run.required_execution = lambda: [cmd("gate")]
                    run.validation_commands = lambda: [cmd("gate")]
                    run.execution_grants = [(cmd("gate"), {})]
                    run.build_facts = lambda: {"validation_results": []}
                    inherited = variable.upper() if os.name == "nt" else variable
                    with patch.dict(os.environ, {inherited: KEY, "OURO_TEST_OTHER": "preserved"}):
                        run.setup_tree(repo)
                        result = run.run_gates(run.tree.head())
                        gate_pending = [{"cmd": cmd("gate"), "reason": "fixture"}]
                        if lifecycle_available:
                            self.assertIsNone(result)
                            self.assertEqual([r["cmd"] for r in run.execution_results], [cmd("gate")])
                            self.assertIsNotNone(validation.receipt(run.tree, gate_pending))
                        else:
                            self.assertEqual(result, 75)
                            self.assertIsNone(validation.receipt(run.tree, gate_pending))
                            self.assertFalse((repo / "seen-gate").exists())
                        hooks = repo / ".git" / "hooks"
                        hook = hooks / "pre-commit"
                        hook.write_text("#!/bin/sh\nexec " + cmd("hook") + "\n", encoding="utf-8", newline="\n")
                        hook.chmod(0o755)
                        env.git("commit", "-q", "--allow-empty", "-m", "hook fixture")
                        for mode in ("success", "failure"):
                            pending = [{"cmd": cmd(mode), "reason": "fixture"}]
                            results, failure = validation.validate(run.tree, pending, run.wait_hint)
                            if not lifecycle_available:
                                self.assertEqual(failure, 75)
                                self.assertIsNone(validation.receipt(run.tree, pending))
                                self.assertFalse((repo / ("seen-" + mode)).exists())
                            else:
                                self.assertEqual(failure, None if mode == "success" else 1)
                                self.assertEqual(bool(validation.receipt(run.tree, pending)), mode == "success")
                                self.assertEqual(results[0]["exit"], 0 if mode == "success" else 1)
                        self.assertEqual(os.environ[inherited], KEY)
                    for mode in ("setup", "hook", *(("gate",) if lifecycle_available else ())):
                        self.assertEqual((repo / ("seen-" + mode)).read_text(), "safe")

    def test_external_reader_and_its_cli_child_remove_the_credential(self):
        from ouro import run as driver
        with tempfile.TemporaryDirectory(prefix="ouro-credential-readers-") as directory:
            root = pathlib.Path(directory).resolve()
            worker = root / "reader_worker.py"
            worker.write_text(
                "import os, pathlib, sys\n"
                f"assert {VARIABLE!r} not in os.environ, 'credential inherited'\n"
                f"assert {KEY!r} not in os.environ.values(), 'credential inherited'\n"
                "assert os.environ['OURO_TEST_OTHER'] == 'preserved'\n"
                "pathlib.Path('reader-' + sys.argv[1]).write_text('safe')\n", encoding="utf-8")
            reader = root / "reader.py"
            reader.write_text(
                "import json, pathlib, subprocess, sys\n"
                "subprocess.run([sys.executable, 'reader_worker.py', sys.argv[1]], check=True)\n"
                "pathlib.Path('verdict.json').write_text(json.dumps({'findings': [], 'files_read': []}))\n"
                "print('external-audit: session fixture round 1 -> ' + str(pathlib.Path.cwd()))\n"
                "print('--resume fixture --digest next')\n", encoding="utf-8")
            cfg = developer.Settings(coordination=self.config(credential_env=VARIABLE))
            for cli in ("claude", "codex"):
                with self.subTest(cli=cli):
                    env = SimpleNamespace(runtime=cfg, run=lambda *a, **k: sh.run(*a, **k), default="main",
                                          lookup=lambda key: cli if key == "ship.external_cli" else None)
                    run = driver.Run.__new__(driver.Run)
                    run.env, run.tree, run.ci_execution_checks = env, Tree(env, root, None), []
                    run.reader_goal = lambda: "public fixture"
                    run.build_facts = lambda: {"validation_results": [], "validation_attempts": []}
                    run.add_note = self.fail
                    with patch.dict(os.environ, {VARIABLE: KEY, "OURO_TEST_OTHER": "preserved"}), patch.object(
                            driver, "script", return_value=[sys.executable, str(reader), cli]):
                        answer = run.reader_round(1, "a" * 40, [])
                        self.assertEqual(answer["verdict"], "CLEAN")
                        self.assertEqual(os.environ[VARIABLE], KEY)
                    self.assertEqual((root / ("reader-" + cli)).read_text(), "safe")

    @patch.object(codex, "WINDOWS", False)
    def test_preflight_subprocesses_also_remove_the_credential(self):
        cfg = developer.Settings(coordination=self.config(credential_env=VARIABLE))
        def result(out="", code=0):
            return sh.R(code, out, "", [])
        claude_results = [result('{"loggedIn":true}'), result(" ".join(claude.FLAGS))]
        codex_results = [result("codex-cli 0.163.0"), result("Logged in using fixture"),
                         result("--ignore-user-config --ignore-rules --output-schema --output-last-message --ephemeral"),
                         result('{"models":[]}'), result()]  # generated Git init
        with patch.object(sh, "argv_for", return_value=["fixture-cli"]), patch.object(sh, "run", side_effect=claude_results) as launch:
            claude.preflight(cfg)
            self.assertTrue(all(c.kwargs["env"] == {VARIABLE: None} for c in launch.call_args_list))
        proof = {"tool_calls": [{"tool": "read", "path": "./readiness.txt", "error": None}]}
        with patch.object(sh, "argv_for", return_value=["fixture-cli"]), patch.object(sh, "run", side_effect=codex_results) as launch, patch.object(process, "run", return_value=result("ouro-sandbox-ready")) as sandbox, patch.object(codex, "network_readiness") as network, patch.object(codex, "execute", return_value=({"finding": "ouro-ready"}, "fixture", proof, None)):
            codex.preflight(cfg)
            self.assertTrue(all(c.kwargs["env"] == {VARIABLE: None} for c in launch.call_args_list))
            self.assertEqual(len(sandbox.call_args_list), 4)  # sandbox print plus three Git reads
            self.assertTrue(all(call.kwargs["env"].get(VARIABLE, "missing") is None
                                for call in sandbox.call_args_list))
            network.assert_called_once()
            self.assertEqual(network.call_args.kwargs["env"], {VARIABLE: None})
