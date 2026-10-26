"""Optional advisory board transport. Responses are data, never workflow authority."""
import hashlib
import http.client
import ipaddress
import json
import os
import queue
import re
import socket
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlsplit

from .sh import DriverError

BUDGET = 3.0
LIMIT = 256 * 1024
PROTOCOL = "2025-03-26"
VERSIONS = {PROTOCOL, "2024-11-05", "2025-06-18", "2025-11-25"}
TOOLS = {"board_search", "board_list_threads", "board_read_thread", "board_post", "board_reply", "board_update_thread"}


@dataclass(frozen=True)
class Coordination:
    endpoint: str
    person: str
    forum: str
    credential_env: str = "MOMENTUS_API_KEY"

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) - {"endpoint", "person", "forum", "credential_env"}:
            raise ValueError("unknown coordination key or invalid table")
        for key in ("endpoint", "person", "forum"):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or value != value.strip() or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value):
                raise ValueError(f"coordination.{key} must be non-empty text without control characters")
        name = data.get("credential_env", "MOMENTUS_API_KEY")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("coordination.credential_env must name an environment variable")
        try:
            url = urlsplit(data["endpoint"])
            loopback = url.hostname == "localhost"
            try:
                loopback = loopback or ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                pass
            if (not url.hostname or url.username is not None or url.password is not None
                    or url.query or url.fragment or url.port == 0
                    or (url.scheme != "https" and not (url.scheme == "http" and loopback))
                    or any(c.isspace() or ord(c) == 127 for c in data["endpoint"])):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("coordination.endpoint requires HTTPS (HTTP only on loopback), without URL credentials, query or fragment") from None
        return cls(data["endpoint"], data["person"], data["forum"], name)


def worker_env(settings):
    """None removes the inherited credential at the subprocess boundary."""
    config = settings.coordination
    return {config.credential_env: None} if config else {}


@dataclass(frozen=True)
class Reply:
    value: dict | None = None
    notice: str = ""

    @property
    def available(self):
        return self.value is not None


class Unavailable(Exception):
    pass


class Board:
    """One bounded MCP call, including lazy initialization and complete tool discovery.

    Tool arguments and results retain the board's existing payloads. Construction posts nothing.
    """
    def __init__(self, config, environ=None, budget=BUDGET):
        self.config = config
        self.environ = os.environ if environ is None else environ
        self.budget = min(BUDGET, max(0.01, budget))
        self.session = None
        self.protocol = PROTOCOL
        self.tools = None
        self.serial = 0
        self.lock = threading.Lock()

    def call(self, name, arguments, deadline=None):
        if self.config is None:
            return Reply()
        key = self.environ.get(self.config.credential_env)
        if not key:
            return self.unavailable("credential missing")
        if name not in TOOLS or not isinstance(arguments, dict):
            return self.unavailable("unsupported tool")
        if not self.lock.acquire(blocking=False):
            return self.unavailable("transport busy")
        results = queue.Queue(maxsize=1)
        deadline = min(deadline or float('inf'), time.monotonic() + self.budget)
        cancelled = threading.Event()

        def perform():
            try:
                if self.tools is None:
                    result = self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                      "clientInfo": {"name": "ouro", "version": "1"}}, key, deadline, cancelled)
                    server = result.get("serverInfo")
                    if (result.get("protocolVersion") not in VERSIONS or not isinstance(result.get("capabilities"), dict)
                            or not isinstance(server, dict) or any(not isinstance(server.get(k), str) for k in ("name", "version"))):
                        raise Unavailable("invalid initialization")
                    self.protocol = result["protocolVersion"]
                    self.rpc("notifications/initialized", {}, key, deadline, cancelled, notify=True)
                    found, cursors, cursor = set(), set(), None
                    for _ in range(8):
                        page = self.rpc("tools/list", {"cursor": cursor} if cursor else {}, key, deadline, cancelled)
                        entries = page.get("tools")
                        if not isinstance(entries, list) or any(
                                not isinstance(t, dict) or not isinstance(t.get("name"), str)
                                or not isinstance(t.get("inputSchema"), dict) or t["inputSchema"].get("type") != "object"
                                for t in entries):
                            raise Unavailable("incomplete discovery")
                        found.update(t["name"] for t in entries)
                        cursor = page.get("nextCursor")
                        if cursor is None:
                            self.tools = found
                            break
                        if not isinstance(cursor, str) or not cursor or cursor in cursors:
                            raise Unavailable("incomplete discovery")
                        cursors.add(cursor)
                    else:
                        raise Unavailable("incomplete discovery")
                if name not in self.tools:
                    raise Unavailable("tool unavailable")
                result = self.rpc("tools/call", {"name": name, "arguments": arguments}, key, deadline, cancelled)
                if result.get("isError") not in (None, False):
                    raise Unavailable("tool failure")
                content = result.get("content")
                if (not isinstance(content, list) or any(
                        not isinstance(c, dict) or not isinstance(c.get("type"), str)
                        or (c["type"] == "text" and not isinstance(c.get("text"), str)) for c in content)
                        or ("structuredContent" in result and not isinstance(result["structuredContent"], dict))):
                    raise Unavailable("malformed reply")
                payloads = [result.get("structuredContent")]
                for item in content:
                    if item["type"] == "text":
                        try:
                            payloads.append(json.loads(item["text"]))
                        except ValueError:
                            pass
                if any(isinstance(value, dict) and "error" in value for value in payloads):
                    raise Unavailable("tool failure")
                results.put(Reply(result))
            except Unavailable as ex:
                # Only our fixed reason strings cross this boundary.
                self.tools = None
                self.session = None
                results.put(self.unavailable(str(ex)))
            except Exception:
                self.tools = None
                self.session = None
                results.put(self.unavailable("transport failure"))
            finally:
                self.lock.release()

        # DNS, TLS and fragmented headers can otherwise outlive a socket's per-read
        # timeout. The caller's absolute deadline also bounds those stdlib operations.
        try:
            threading.Thread(target=perform, daemon=True).start()
        except Exception:
            self.lock.release()
            return self.unavailable("transport failure")
        try:
            return results.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            return self.unavailable("timeout")
        except BaseException:
            cancelled.set()
            raise

    @staticmethod
    def unavailable(reason):
        return Reply(notice=f"Coordination unavailable ({reason}); continuing without board evidence.")

    def rpc(self, method, params, key, deadline, cancelled, notify=False):
        def remaining():
            left = deadline - time.monotonic()
            if cancelled.is_set() or left <= 0:
                raise Unavailable("timeout")
            return left

        self.serial += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            request["id"] = self.serial
        body = json.dumps(request, allow_nan=False).encode("utf-8")
        if len(body) > LIMIT:
            raise Unavailable("request too large")
        url = urlsplit(self.config.endpoint)
        connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(url.hostname, url.port, timeout=remaining())
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + key}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if method != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol
        response = None
        try:
            connection.connect()
            connection.sock.settimeout(remaining())  # DNS/TLS may have consumed the whole budget
            connection.request("POST", url.path or "/", body, headers)
            remaining()
            response = connection.getresponse()
            remaining()
            if response.status in (401, 403):
                raise Unavailable("authentication failed")
            if response.status not in (200, 202, 204):
                raise Unavailable("HTTP failure")  # redirects are never followed
            if notify and response.status in (202, 204):
                return {}
            session = response.getheader("Mcp-Session-Id")
            if session is not None:
                if (not session or len(session) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in session)
                        or (method != "initialize" and session != self.session)):
                    raise Unavailable("invalid session")
                self.session = session
            kind = response.getheader("Content-Type", "").split(";")[0].strip().lower()
            if kind not in ("application/json", "text/event-stream"):
                raise Unavailable("malformed reply")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > LIMIT):
                raise Unavailable("response too large")
            raw, pending, data = bytearray(), bytearray(), []
            after_cr = False
            while True:
                if connection.sock:
                    connection.sock.settimeout(remaining())
                chunk = response.read1(min(8192, LIMIT + 1 - len(raw)))
                remaining()
                raw.extend(chunk)
                if len(raw) > LIMIT:
                    raise Unavailable("response too large")
                if kind == "text/event-stream":
                    for byte in chunk:
                        if after_cr and byte == 10:
                            after_cr = False
                            continue
                        after_cr = byte == 13
                        if byte not in (10, 13):
                            pending.append(byte)
                            continue
                        # CR, LF and CRLF delimit lines, even across read boundaries.
                        line = bytes(pending)
                        pending.clear()
                        if line.startswith(b"data:"):
                            data.append(line[5:].lstrip(b" "))
                        elif not line and data:
                            event = json.loads(b"\n".join(data))
                            data = []
                            if isinstance(event, dict) and event.get("id") == request.get("id"):
                                return self.envelope(event, request.get("id"))
                    if not chunk:
                        raise Unavailable("malformed reply")
                elif not chunk:
                    return self.envelope(json.loads(raw), request.get("id"))
        except (TimeoutError, socket.timeout):
            raise Unavailable("timeout") from None
        finally:
            if response is not None:
                response.close()
            connection.close()

    @staticmethod
    def envelope(value, expected):
        if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0"
                or type(value.get("id")) is not int or value["id"] != expected
                or "error" in value or not isinstance(value.get("result"), dict)):
            raise Unavailable("malformed reply")
        return value["result"]

# The cache carries correlation only. GitHub/git decide whether work stopped or landed.
MARK = re.compile(r"<!-- ouro-claim: (.*?) -->", re.DOTALL)


def payload(reply):
    if not reply.available:
        raise Unavailable("transport unavailable")
    values = []
    result = reply.value
    if isinstance(result.get("structuredContent"), dict):
        values.append(result["structuredContent"])
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                value = json.loads(item["text"])
                if isinstance(value, dict):
                    values.append(value)
            except ValueError:
                pass
    if not values or any(v != values[0] for v in values) or "error" in values[0]:
        raise Unavailable("malformed tool result")
    return values[0]


def thread_id(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise Unavailable("malformed thread")
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise Unavailable("malformed thread")
    return value


class Claim:
    """Optional issue claim; check-then-post is advisory, never a lease or shipping grant."""
    def __init__(self, run):
        self.run, self.env = run, run.env
        self.config = run.env.runtime.coordination
        self.board = Board(self.config)
        self.owned = None
        self.data = None
        self.path = None
        self.noticed = False
        self.terminal = False
        self.prepared = None

    def notice(self):
        if not self.noticed:
            from .sh import say
            say("Coordination unavailable; continuing without complete board evidence.")
            self.noticed = True

    def safe(self, value):
        key = os.environ.get(self.config.credential_env, "")
        text = re.sub(r"https?://\S+", "[url]", str(value))
        if key:
            text = text.replace(key, "[redacted]")
        return "".join(c if c.isprintable() else " " for c in text)[:160]

    def call(self, name, args, deadline):
        if time.monotonic() >= deadline:
            raise Unavailable("boundary timeout")
        return payload(self.board.call(name, args, deadline=deadline))

    def save(self):
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex)
        try:
            temporary.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def identity(self):
        e, r = self.env, self.run
        actor = e.viewer()
        root = Path(e.git("rev-parse", "--path-format=absolute", "--git-common-dir").out.strip()) / "ouro-claims"
        root.mkdir(exist_ok=True)
        session_file = root / "session"
        try:
            with session_file.open("x", encoding="utf-8") as stream:
                stream.write(uuid.uuid4().hex)
        except FileExistsError:
            pass
        session = session_file.read_text(encoding="utf-8")
        if not re.fullmatch(r"[a-f0-9]{32}", session):
            raise Unavailable("invalid cache")
        identity = dict(person=self.config.person, platform=e.runtime.backend, session=session,
                        actor=actor, repository=e.slug, issue=r.n, part=r.k)
        scope = [self.config.endpoint, self.config.forum, identity]
        self.path = root / (hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest() + ".json")
        if self.path.exists():
            if self.path.stat().st_size > 4096:
                raise Unavailable("invalid cache")
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(self.data, dict) or self.data.get("identity") != identity
                    or not re.fullmatch(r"[a-f0-9]{32}", str(self.data.get("correlation", "")))):
                raise Unavailable("invalid cache")
        else:
            self.data = dict(identity=identity, correlation=uuid.uuid4().hex)
            self.save()  # before create: a lost response can be reconciled on resume

    def metadata(self, detail):
        messages = detail.get("messages")
        if (detail.get("earlierNotShown", 0) != 0 or not isinstance(messages, list)
                or not messages or not isinstance(messages[0], dict)):
            return False
        body = messages[0].get("body")
        if not isinstance(body, str):
            return False
        match = MARK.search(body)
        if not match:
            return False
        try:
            data = json.loads(match[1])
            return data if isinstance(data, dict) else None
        except (ValueError, RecursionError):
            return None

    def owns(self, detail):
        data = self.metadata(detail)
        return bool(data and data.get("identity") == self.data["identity"]
                    and data.get("correlation") == self.data["correlation"])

    def retire_completed(self, detail, ident, deadline):
        """Recognize a completed part only through its original local correlation and GitHub checklist."""
        from .run import parse_parts
        data = self.metadata(detail)
        if not data or not isinstance(data.get("identity"), dict):
            return False
        identity = data["identity"]
        part = identity.get("part")
        if type(part) is not int or part == self.run.k:
            return False
        expected = dict(self.data["identity"], part=part)
        if identity != expected or not any(k == part and done for k, done, _ in parse_parts(self.run.body)):
            return False
        scope = [self.config.endpoint, self.config.forum, expected]
        path = self.path.parent / (hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest() + ".json")
        try:
            if path.stat().st_size > 4096:
                return False
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(cached, dict) or cached.get("identity") != expected
                    or not re.fullmatch(r"[a-f0-9]{32}", str(cached.get("correlation", "")))
                    or cached.get("correlation") != data.get("correlation")):
                return False
        except (OSError, ValueError, RecursionError):
            return False
        # A failed close remains ours on the next ordinary resume, never a foreign conflict.
        try:
            result = self.call("board_reply", dict(id=ident,
                               body=f"Checklist part {part} is complete on GitHub; closing its correlated advisory claim.",
                               **self.author()), deadline)
            if str(result.get("threadId")) != ident or not isinstance(result.get("added"), dict):
                raise Unavailable("malformed reply")
            result = self.call("board_update_thread", dict(id=ident, status="closed", waitingOn=[], **self.author()), deadline)
            updated = result.get("updated")
            if not isinstance(updated, dict) or thread_id(updated.get("id")) != ident or updated.get("status") != "closed":
                raise Unavailable("malformed close")
        except Unavailable:
            self.notice()
        return True

    def discover(self, deadline, scope=None):
        r, e = self.run, self.env
        title = f"Claimed: {e.slug}#{r.n}"
        listed = self.call("board_list_threads", dict(forum=self.config.forum, status="open", limit=100), deadline)
        rows = listed.get("threads")
        if not isinstance(rows, list) or len(rows) > 100:
            raise Unavailable("malformed listing")
        incomplete = type(listed.get("more", 0)) is not int or listed.get("more", 0) != 0
        conflicts, owned = [], []
        candidates = 0
        for row in rows:
            if not isinstance(row, dict):
                incomplete = True
                continue
            if not isinstance(row.get("title"), str):
                incomplete = True
                continue
            same_issue = row["title"] == title
            if not same_issue and not (scope is not None and re.fullmatch(r"Claimed: " + re.escape(e.slug) + r"#\d+", row["title"])):
                continue
            try:
                ident = thread_id(row.get("id"))
                if row.get("forum") != self.config.forum or row.get("status") != "open":
                    raise Unavailable("malformed summary")
                candidates += 1
                if candidates > 16:
                    raise Unavailable("discovery fan-out")
                detail = self.call("board_read_thread", dict(id=ident, limit=100, markRead=False), deadline)
                thread = detail.get("thread")
                if (not isinstance(thread, dict) or thread_id(thread.get("id")) != ident
                        or thread.get("title") != row["title"] or thread.get("forum") != self.config.forum
                        or thread.get("status") != "open" or not isinstance(detail.get("messages"), list)
                        or not detail["messages"] or not isinstance(detail["messages"][0], dict)
                        or not isinstance(detail["messages"][0].get("body"), str)
                        or type(detail.get("earlierNotShown", 0)) is not int or detail.get("earlierNotShown", 0) < 0):
                    raise Unavailable("malformed thread")
                if self.owns(detail):
                    owned.append(ident)
                elif (same_issue and not self.retire_completed(detail, ident, deadline)) or (not same_issue and self.overlaps(detail, scope)):
                    conflicts.append(dict(thread=ident, holder=self.safe(thread.get("createdBy") or "unknown holder")))
            except (Unavailable, ValueError, TypeError, AttributeError, RecursionError):
                incomplete = True
        return owned, conflicts, incomplete

    def overlaps(self, detail, scope):
        initial = self.metadata(detail)
        if not initial:
            raise Unavailable("scope unavailable")
        overlap, incomplete = None, False
        for message in detail["messages"]:
            try:
                match = MARK.search(message.get("body", ""))
                if not match:
                    continue
                data = json.loads(match[1])
                if (not isinstance(data, dict) or data.get("identity") != initial.get("identity")
                        or data.get("correlation") != initial.get("correlation")):
                    continue
                other = data.get("scope")
                if (not isinstance(other, dict) or any(
                        not isinstance(other.get(k), list) or any(not isinstance(v, str) for v in other[k])
                        for k in ("paths", "areas")) or not (other["paths"] or other["areas"])):
                    raise Unavailable("scope unavailable")
                overlap = bool(set(scope["paths"]) & set(other["paths"]) or set(scope["areas"]) & set(other["areas"]))
                incomplete = False
            except (Unavailable, ValueError, TypeError, AttributeError, RecursionError):
                incomplete = True
        if overlap is True:
            return True  # malformed later evidence cannot erase a known conflict
        if overlap is None or incomplete:
            raise Unavailable("scope unavailable")
        return False

    def body(self, outcome, conflicts=()):
        r, e = self.run, self.env
        cwd = r.worktree if (r.worktree / ".git").exists() else e.main
        head = e.git("rev-parse", "HEAD", cwd=cwd).out.strip()
        record = dict(self.data, kind="issue-claim", branch=r.branch, head=head, outcome=outcome,
                      scope=dict(paths=[], areas=list(getattr(r, "areas", []))))
        if self.prepared:
            record.update(self.prepared)
            record["branch"] = e.git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).out.strip()
        if conflicts:
            record["override"] = dict(actor=e.viewer(), conflicts=conflicts)
        encoded = json.dumps(record, ensure_ascii=True).replace(">", "\\u003e")
        return ("Advisory claim; self-announced identity, not verified board identity or shipping authority.\n"
                + "<!-- ouro-claim: " + encoded + " -->")

    def author(self):
        return dict(author=self.config.person, via=self.env.runtime.backend, session=self.data["identity"]["session"])

    def enter(self, create=True):
        if not self.config or self.env.dry:
            return
        if not os.environ.get(self.config.credential_env):
            self.notice()
            return
        deadline = time.monotonic() + BUDGET
        try:
            self.identity()
            owned, conflicts, incomplete = self.discover(deadline)
            if conflicts and not getattr(self.env, "override_claim", False):
                from .sh import Refused
                holders = ", ".join(f"{c['holder']} (thread {self.safe(c['thread'])})" for c in conflicts)
                raise Refused(f"known issue claim held by {holders}; the invoking person may use --override-claim")
            if incomplete:
                self.notice()
            if len(owned) > 1:
                raise Unavailable("duplicate owned claims")
            self.owned = owned[0] if owned else None
            # Incomplete discovery alone never justifies creating another claim.
            if self.owned:
                self.data["thread"] = self.owned
                self.save()
                if conflicts:
                    self.call("board_reply", dict(id=self.owned, body=self.body("override", conflicts), **self.author()), deadline)
            elif create and (not incomplete or conflicts):
                result = self.call("board_post", dict(forum=self.config.forum, title=f"Claimed: {self.env.slug}#{self.run.n}",
                                   body=self.body("claimed", conflicts), **self.author()), deadline)
                created = result.get("created")
                if (not isinstance(created, dict) or created.get("status") != "open"
                        or created.get("title") != f"Claimed: {self.env.slug}#{self.run.n}"
                        or created.get("forum") != self.config.forum):
                    raise Unavailable("malformed creation")
                self.owned = thread_id(created.get("id"))
                self.data["thread"] = self.owned
                self.save()
        except (Unavailable, OSError, ValueError, TypeError, RecursionError, DriverError):
            self.notice()

    def prepare(self, base, head, paths, areas, stage, *, check=True):
        """Reusable prepared-change boundary; the caller supplies Git scope and reviewed policy areas."""
        if not self.config or self.env.dry:
            return
        if not os.environ.get(self.config.credential_env):
            self.notice()
            return
        self.prepared = dict(base=base, head=head, stage=stage,
                             scope=dict(paths=sorted(set(paths)), areas=sorted(set(areas))))
        if not check:
            return  # reconstruct a waiting trace; this grants no publication permission
        deadline = time.monotonic() + BUDGET
        try:
            if self.data is None:
                self.identity()
            owned, conflicts, incomplete = self.discover(deadline, self.prepared["scope"])
            if conflicts and not getattr(self.env, "override_claim", False):
                from .sh import Refused
                holders = ", ".join(f"{c['holder']} (thread {self.safe(c['thread'])})" for c in conflicts)
                raise Refused(f"known prepared-change claim held by {holders}; the invoking person may use --override-claim")
            if incomplete:
                self.notice()
            if len(owned) != 1:
                self.owned = None
                raise Unavailable("ownership unavailable")
            self.owned = owned[0]
            self.update("direct prepared", deadline=deadline, conflicts=conflicts)
        except (Unavailable, OSError, ValueError, TypeError, RecursionError, DriverError):
            self.notice()

    def update(self, outcome, close=False, deadline=None, conflicts=()):
        if not self.owned or self.env.dry or (self.terminal and not close):
            return
        self.terminal = self.terminal or close
        deadline = deadline or time.monotonic() + BUDGET
        try:
            # Re-read before every write; never update another holder's thread.
            detail = self.call("board_read_thread", dict(id=self.owned, limit=100, markRead=False), deadline)
            thread = detail.get("thread", {})
            if (not isinstance(thread, dict) or thread_id(thread.get("id")) != self.owned
                    or thread.get("status") != "open" or not self.owns(detail)):
                raise Unavailable("ownership unavailable")
            result = self.call("board_reply", dict(id=self.owned, body=self.body(outcome, conflicts), **self.author()), deadline)
            if (str(result.get("threadId")) != self.owned or not isinstance(result.get("added"), dict)
                    or not isinstance(result["added"].get("body"), str)):
                raise Unavailable("malformed reply")
            if close:
                result = self.call("board_update_thread", dict(id=self.owned, status="closed", waitingOn=[], **self.author()), deadline)
                updated = result.get("updated")
                if not isinstance(updated, dict) or thread_id(updated.get("id")) != self.owned or updated.get("status") != "closed":
                    raise Unavailable("malformed close")
                self.owned = None
        except (Unavailable, OSError, ValueError, TypeError, RecursionError, DriverError):
            self.notice()
