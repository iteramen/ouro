"""Observed BUILD commands, bound to final file inputs and a committed head."""
import hashlib
import json
import os
import re
from urllib.parse import quote
from pathlib import Path

from . import sh


def ci_checks(env, pr, head):
    """Fresh GitHub results, not a local receipt or a successful notice in a gate command: the evidence, or None to wait."""
    view = env.gh_json("pr", "view", str(pr["number"]), "--json", "headRefOid,statusCheckRollup")
    names = env.lookup("ship.validation_checks") or []
    if not names:
        raise sh.DriverError("CI validation requires nonempty ship.validation_checks")
    if view.get("headRefOid") != head:
        sh.say(f"waiting for CI validation at {head}: the pull request head changed")
        return None
    rows = view.get("statusCheckRollup") or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise sh.DriverError("GitHub returned malformed CI check results; nothing is validated")
    evidence = []
    for name in names:
        matches = [row for row in rows if row.get("name", row.get("context")) == name]
        if not matches or any(not ((row.get("__typename") == "CheckRun" and row.get("name") == name
                                   and row.get("status") == "COMPLETED" and row.get("conclusion") == "SUCCESS")
                                  or (row.get("__typename") == "StatusContext" and row.get("context") == name
                                      and row.get("state") == "SUCCESS")) for row in matches):
            return actions_checks(env, pr, head, names, rows)
        evidence.extend({"name": name, "sha": head, "url": row.get("detailsUrl", row.get("targetUrl", ""))}
                        for row in matches)
    return evidence


def actions_checks(env, pr, head, names, rows):
    """Reconcile omitted Actions jobs, anchored by this PR's ordinary Actions invocation.

    Every API walk is bounded. No partial page, ambiguous identity or changed snapshot
    authorizes admission; the next supported resume starts a fresh observation.
    """
    class Unproven(Exception):
        pass

    def require(ok, why):
        if not ok:
            raise Unproven(why)

    api_calls = 0

    def api(path):
        nonlocal api_calls
        api_calls += 1
        require(api_calls <= 128, "Actions observation exceeds bounded API budget")
        result = env.gh("api", "repos/" + env.slug + "/" + path, check=False)
        require(result.code == 0, "Actions API unavailable")
        try:
            data = json.loads(result.out)
        except (ValueError, TypeError):
            raise Unproven("malformed Actions API JSON")
        require(isinstance(data, dict), "malformed Actions API object")
        return data

    def pages(path, key):
        result = []
        for page in range(1, 6):
            data = api(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            batch = data.get(key)
            total = data.get("total_count")
            require(isinstance(batch, list) and all(isinstance(x, dict) for x in batch)
                    and type(total) is int and total >= 0, "malformed Actions page")
            result.extend(batch)
            if len(result) == total:
                return result
            require(len(batch) == 100 and len(result) < total, "incomplete Actions pagination")
        raise Unproven("Actions pagination exceeds bounded observation")

    try:
        # Retain only unambiguous ordinary legacy authority; Actions cannot replace it.
        statuses = {}
        for name in names:
            matches = [row for row in rows if row.get("name", row.get("context")) == name]
            if any(row.get("__typename") == "StatusContext" for row in matches):
                require(len(matches) == 1 and matches[0].get("__typename") == "StatusContext"
                        and matches[0].get("context") == name and matches[0].get("state") == "SUCCESS",
                        "ambiguous or unsuccessful required status")
                statuses[name] = matches[0]
        meta = api(f"pulls/{pr['number']}")
        ref = meta["head"]["ref"]
        require(meta["head"]["sha"] == head and meta["head"]["repo"]["full_name"] == env.slug
                and meta["base"]["repo"]["full_name"] == env.slug and isinstance(ref, str) and ref,
                "PR repository/head/ref is not trusted")
        repository_id = meta["base"]["repo"]["id"]
        require(type(repository_id) is int and repository_id > 0
                and meta["head"]["repo"]["id"] == repository_id, "PR repository identity mismatch")
        base = meta["base"]["sha"]
        snapshots = {}
        jobs_cache = {}
        inventories = {}

        def run(run_id):
            value = api(f"actions/runs/{run_id}")
            require(type(run_id) is int and run_id > 0 and value.get("id") == run_id
                    and type(value.get("run_attempt")) is int and value["run_attempt"] > 0
                    and type(value.get("run_number")) is int and value["run_number"] > 0,
                    "missing current run identity/attempt")
            snapshots[run_id] = value
            return value

        def identity(value, workflow=None):
            require(value.get("repository", {}).get("full_name") == env.slug
                    and value.get("repository", {}).get("id") == repository_id
                    and value.get("head_repository", {}).get("full_name") == env.slug
                    and value.get("head_repository", {}).get("id") == repository_id
                    and value.get("head_sha") == head and value.get("head_branch") == ref,
                    "Actions repository/head/ref mismatch")
            require(value.get("event") in ("pull_request", "workflow_dispatch"),
                    "Actions event is not trusted")
            if value["event"] == "pull_request":
                require(any(p.get("number") == pr["number"] for p in value.get("pull_requests", [])),
                        "Actions run is not associated with this PR")
            if workflow:
                require((value.get("workflow_id"), value.get("path")) == workflow,
                        "Actions workflow identity mismatch")

        def jobs(value):
            key = (value["id"], value["run_attempt"])
            if key not in jobs_cache:
                jobs_cache[key] = pages(f"actions/runs/{key[0]}/attempts/{key[1]}/jobs", "jobs")
            return jobs_cache[key]

        def authenticate(job, value):
            require(job.get("run_id") == value["id"] and job.get("head_sha") == head
                    and job.get("run_attempt") == value["run_attempt"],
                    "job revision or attempt mismatch")
            url = job.get("check_run_url", "")
            prefix = f"https://api.github.com/repos/{env.slug}/check-runs/"
            require(isinstance(url, str) and url.startswith(prefix)
                    and url[len(prefix):].isdigit(), "foreign check provider endpoint")
            check = api("check-runs/" + url[len(prefix):])
            require(check.get("app", {}).get("id") == 15368
                    and check.get("app", {}).get("slug") == "github-actions"
                    and check.get("app", {}).get("owner", {}).get("login") == "github"
                    and check.get("head_sha") == head and check.get("name") == job.get("name")
                    and check.get("details_url") == job.get("html_url")
                    and check.get("status") == job.get("status")
                    and check.get("conclusion") == job.get("conclusion"),
                    "job/check provider or completion mismatch")

        # A rollup URL alone is not authority: authenticate the referenced PR job.
        anchors = {}
        for row in rows:
            if row.get("__typename") != "CheckRun":
                continue
            url = row.get("detailsUrl", "")
            match = re.fullmatch(r"https://github.com/" + re.escape(env.slug)
                                + r"/actions/runs/([0-9]+)/job/([0-9]+)", url)
            if not match:
                continue
            value = run(int(match[1]))
            # Other invocations can appear in the rollup, but cannot establish
            # ordinary PR workflow authority. Required rows are reconciled below.
            if value.get("event") != "pull_request":
                continue
            identity(value)
            matching = [j for j in jobs(value) if str(j.get("id")) == match[2]
                        and j.get("name") == row.get("name") and j.get("html_url") == url]
            require(len(matching) == 1, "rollup job is absent or ambiguous")
            job = matching[0]
            authenticate(job, value)
            require(job.get("status", "").upper() == row.get("status")
                    and (job.get("conclusion") or "").upper() == row.get("conclusion"),
                    "rollup and current attempt disagree")
            workflow = (value.get("workflow_id"), value.get("path"))
            require(type(workflow[0]) is int and isinstance(workflow[1], str)
                    and workflow[1].startswith(".github/workflows/") and "@" not in workflow[1],
                    "invalid workflow identity")
            anchors[workflow] = value
        require(anchors, "no authenticated ordinary PR workflow anchor")
        evidence = [{"name": name, "sha": head, "url": row.get("targetUrl", "")}
                    for name, row in statuses.items()]
        covered = set(statuses)
        owners = {}
        for workflow in sorted(anchors):
            wid, path = workflow
            declared = api(f"actions/workflows/{wid}")
            require(declared.get("id") == wid and declared.get("path") == path
                    and declared.get("state") == "active", "workflow identity is unavailable")
            # Dispatch executes branch code. Refuse changed workflow definitions rather
            # than letting a branch substitute a same-name validation job.
            content_path = "contents/" + quote(path, safe="/") + "?ref="
            trusted = api(content_path + quote(base, safe=""))
            current = api(content_path + quote(head, safe=""))
            require(trusted.get("type") == current.get("type") == "file"
                    and trusted.get("sha") and trusted["sha"] == current.get("sha"),
                    "workflow differs from the trusted PR base")
            inventory = f"actions/workflows/{wid}/runs?head_sha={head}&branch={quote(ref, safe='')}"
            listed = pages(inventory, "workflow_runs")
            inventories[inventory] = listed
            require(len({v.get("id") for v in listed}) == len(listed), "duplicate Actions run identity")
            relevant = []
            for summary in listed:
                require(type(summary.get("id")) is int, "malformed run identity")
                value = run(summary["id"])
                if value.get("event") not in ("pull_request", "workflow_dispatch"):
                    continue
                identity(value, workflow)
                require(summary.get("run_attempt") == value["run_attempt"], "run attempt changed during discovery")
                relevant.append(value)
            require(any(v["id"] == anchors[workflow]["id"]
                        and v["run_attempt"] == anchors[workflow]["run_attempt"] for v in relevant),
                    "ordinary PR anchor omitted or attempt changed")
            # Latest run per event is authoritative; never hunt an older success.
            selected = {}
            for value in relevant:
                event = value["event"]
                if event not in selected or value["run_number"] > selected[event]["run_number"]:
                    selected[event] = value
                elif value["run_number"] == selected[event]["run_number"]:
                    require(value["id"] == selected[event]["id"], "ambiguous run ordering")
            for value in selected.values():
                require(value.get("status") == "completed", "latest applicable run is pending")
                for name in names:
                    matches = [j for j in jobs(value) if j.get("name") == name]
                    require(len(matches) <= 1, "duplicate literal job names")
                    if not matches:
                        continue
                    require(name not in statuses, "required name has both status and Actions authority")
                    require(name not in owners or owners[name] == workflow,
                            "literal job name belongs to multiple workflows")
                    owners[name] = workflow
                    job = matches[0]
                    authenticate(job, value)
                    if (value["event"] == "pull_request" and job.get("status") == "completed"
                            and job.get("conclusion") == "skipped"):
                        continue
                    require(job.get("status") == "completed" and job.get("conclusion") == "success",
                            f"applicable job {name!r} has no successful completion")
                    covered.add(name)
                    evidence.append({"name": name, "sha": head, "url": job["html_url"],
                                     "run_id": value["id"], "attempt": value["run_attempt"],
                                     "workflow_id": wid, "workflow_path": path, "event": value["event"]})
        require(covered == set(names), "required checks have no authenticated success: "
                + ", ".join(repr(name) for name in names if name not in covered))
        # Foreign/failed rollup rows cannot be erased by an Actions success.
        authenticated_urls = {j.get("html_url") for batch in jobs_cache.values() for j in batch}
        for row in rows:
            name = row.get("name", row.get("context"))
            if name in statuses:
                require(row == statuses[name], "required status changed")
                continue
            if name in names:
                require(row.get("__typename") == "CheckRun"
                        and row.get("detailsUrl") in authenticated_urls
                        and row.get("status") == "COMPLETED"
                        and row.get("conclusion") in ("SUCCESS", "SKIPPED"),
                        "conflicting or unauthenticated required rollup row")
        for inventory, before in inventories.items():
            require(pages(inventory, "workflow_runs") == before, "Actions inventory changed during reconciliation")
        for (run_id, attempt), before in jobs_cache.items():
            require(pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs") == before,
                    "jobs changed during reconciliation")
        for run_id, before in snapshots.items():
            require(api(f"actions/runs/{run_id}") == before, "Actions run changed during reconciliation")
        require(api(f"pulls/{pr['number']}") == meta, "PR changed during reconciliation")
        if statuses:
            fresh = env.gh_json("pr", "view", str(pr["number"]), "--json", "headRefOid,statusCheckRollup")
            require(fresh.get("headRefOid") == head and fresh.get("statusCheckRollup") == rows,
                    "required rollup changed during reconciliation")
        return evidence
    except (Unproven, KeyError, TypeError, ValueError, AttributeError, sh.DriverError) as error:
        sh.say(f"waiting for CI validation at {head}: {error}")
        return None


def ci_inputs(tree, head):
    """Visible clean inputs for the portable local part of repository CI validation."""
    from . import validation
    validation.clean(tree, head)
    entries = tree.git("ls-files", "-v", "-z").out.split("\0")
    if any(row[:1].islower() or row.startswith("S ") for row in entries):
        raise sh.Refused("CI validation requires visible local inputs; preserve and resolve assume-unchanged/skip-worktree entries")


def fingerprint(root, git=None):
    """HEAD, the index, and the mode and bytes of every path Git reports changed, untracked or hidden
    (assume-unchanged, skip-worktree): equal before and after a command that changed none of its
    inputs. None when Git cannot say, or a changed path is a directory whose contents it does not list."""
    root = Path(root)
    def query(*args):
        r = sh.run(sh.argv_for("git", "-c", "core.fsmonitor=false", *args), cwd=root, timeout=60)
        if r.code:
            raise sh.DriverError("execution input inventory unavailable")
        return r.out
    git = git or query
    try:
        status = git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--ignore-submodules=none")
        hidden = [row[2:] for row in git("ls-files", "-v", "-z").split("\0") if row[:1].islower() or row.startswith("S ")]
        if "\ufffd" in status + "".join(hidden):
            return None  # an undecodable name cannot be read back to its file
        rows = [git("rev-parse", "--revs-only", "HEAD"), git("ls-files", "--stage", "-z"), status]
        for name in sorted({row[3:] for row in status.split("\0") if row[3:]} | set(hidden)):
            path = root / name
            if path.is_symlink():
                rows.append([name, "link", os.readlink(path)])
            elif path.is_file():
                rows.append([name, path.stat().st_mode & 0o111, hashlib.sha256(path.read_bytes()).hexdigest()])
            elif path.exists():
                return None  # a dirty submodule or a nested repository
    except (OSError, ValueError, sh.DriverError):
        return None
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode("utf-8")).hexdigest()


def directory(tree):
    return tree.admin() / "ouro-execution"


def retain(tree, requirements, *, recheck=None):
    """Only the driver writes these receipts: the head's inputs, its obligations and the commands BUILD's
    observations let it re-run. None `requirements`: the branch's own."""
    inputs = fingerprint(tree.path)
    root = directory(tree)
    root.mkdir(exist_ok=True)
    head = tree.head()
    if root.is_symlink() or any((root / (head + suffix)).is_symlink() for suffix in (".json", ".sha256")):
        raise sh.DriverError("aliased execution evidence directory or file")
    if requirements is None:
        requirements = grants(tree)
    data = {"sha": head, "inputs": inputs, "grants": requirements,
            "recheck": list(recheck) if recheck is not None else candidates(tree)}
    raw = json.dumps(data).encode("utf-8")
    (root / (head + ".json")).write_bytes(raw)
    (root / (head + ".sha256")).write_text(hashlib.sha256(raw).hexdigest(), encoding="utf-8")


def load(path):
    if path.is_symlink() or path.parent.is_symlink() or path.with_suffix(".sha256").is_symlink():
        raise ValueError("aliased execution receipt")
    raw = path.read_bytes()
    if path.with_suffix(".sha256").read_text(encoding="utf-8") != hashlib.sha256(raw).hexdigest():
        raise ValueError("corrupt execution receipt")
    return json.loads(raw)


def grants(tree):
    """Obligations survive descendant heads; results never do."""
    heads = tree.git("rev-list", "HEAD", "--not", f"origin/{tree.env.default}").out.split()
    values = {}
    for head in heads:
        try:
            data = load(directory(tree) / (head + ".json"))
            value = data.get("grants")
            if data["sha"] == head and isinstance(value, list):
                for row in value:
                    if (isinstance(row, list) and len(row) == 2
                            and isinstance(row[0], str) and isinstance(row[1], dict)):
                        values[json.dumps(row, sort_keys=True)] = row
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return list(values.values())


def candidates(tree):
    """Pre-commit observations authorize a re-run, never a pass at a new head."""
    try:
        data = load(directory(tree) / (tree.head() + ".json"))
        if data["sha"] == tree.head() and data["inputs"] == fingerprint(tree.path):
            return [cmd for cmd in data.get("recheck", []) if isinstance(cmd, str)]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return []
