#!/usr/bin/env python3
"""ouro-binding.py - read and validate a repo's ouro binding (.claude/ouro.toml).

Usage:
    python3 ouro-binding.py check [path]
        Validate against docs/binding.md. Exit 0 and print "ok", or exit 1 listing every
        violation. A `warning:` line about repo.checkout may print before either.
        Default path: .claude/ouro.toml under the working directory's git root.
    python3 ouro-binding.py get <dotted.key> [path]
        Print one value: strings raw, everything else as JSON. List entries by index
        (gate.0.run). Exit 1 if the key is absent, except that an absent ship.sync or
        ship.merge is answered from the [ship].landing preset, when one is declared.
    python3 ouro-binding.py gates [--changed <base> [--head <rev>]] [--json] [path]
        Print the `run` command of every [[gate]], one per line, in binding order, each with
        every <ouro> replaced by this script's own plugin root. With --changed, only the gates
        that run for the diff from the merge base of <base> and <rev> (default HEAD): one with
        no paths always runs, one with paths when the diff touches one of them. Exit 1 with an
        error when that diff cannot be read. --json prints the selected [[gate]] tables as
        `get gate` does instead of their runs.
    python3 ouro-binding.py direct <path>... [--binding <path>]
        Read ship.direct and print, one per line, each given path that matches no glob
        (fnmatch.fnmatchcase on the path as given). Exit 1 when any is printed, 0 when all
        match, 2 with an error on stderr when ship.direct is empty or absent, when the
        binding cannot be loaded, or when --binding has no path after it.
    python3 ouro-binding.py pr-workflows [path]
        Print, one per line, each workflow file under .github/workflows/ at origin/<default
        branch> whose top-level `on:` names the pull_request event: what runs a pull request's
        checks. Nothing printed: a pull request gets no check.
    python3 ouro-binding.py --selftest
        Run the in-memory self-check.

A [[gate]].run may name the plugin root with the token <ouro>: `check` accepts it and `gates`
expands it. A vendored copy of this script sits flat in the consumer's scripts directory and has
no plugin root, so there both commands refuse such a run, naming that directory.

A gitignored .claude/ouro.local.toml beside the binding is merged over it; it may set
repo.checkout and nothing else.

The binding and its overlay are read as UTF-8, with a byte-order mark accepted; a file that
cannot be read is one `error: <path>: <reason>` line.

Stdlib only, Python 3.12+ (tomllib).
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile

# Check the shared runtime floor before imports or binding reads, with an installable requirement. Read by index, not .major/.minor, so the selftest can stand
# a plain tuple in for sys.version_info.
if tuple(sys.version_info[:2]) < (3, 12):
    sys.exit(f"ouro-binding.py needs Python 3.12+ (tomllib); this python is {sys.version_info[0]}.{sys.version_info[1]}")

# Both streams: the error lines below name paths, and a caller reads them back the same way it
# reads the answers. The floor above is printed before this runs, and it is ASCII.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"): _stream.reconfigure(encoding="utf-8", errors="replace")

import tomllib
from pathlib import Path

POLICIES = ("stop-at-pr", "trivial-merge", "merge-on-green")
# ship.unattended_classes, under merge-on-green: the Flow classes the driver may merge unattended.
UNATTENDED_CLASSES = ("express", "standard", "full")
REVIEWS = ("copilot", "adversarial-review", "external-audit", "none")
# ship.external_cli, required under REVIEWS' "external-audit" and allowed under any value: the
# adapters bin/external-audit.py ships. One list, pinned equal to the installer's -ExternalCli
# set and the driver's own ADAPTERS registry by a test, so the three cannot drift apart.
EXTERNAL_CLIS = ("grok", "claude", "codex", "copilot")
# Two separate choices: a repo may rebase to sync and still squash to land. First is the default.
SYNCS = ("merge", "rebase")
MERGES = ("squash", "merge")
# ship.landing is a named preset over that pair, spelled "<sync>-<merge>". Built as the product of
# the two sets, so no preset can name a pair the keys themselves refuse. merge-squash is the loop's
# original shape -- merge to sync, squash to land, a linear default branch -- and it is what both
# keys already default to, so a binding that declares none of the three lands that way.
LANDINGS = {f"{sync}-{merge}": (sync, merge) for sync in SYNCS for merge in MERGES}
# ship.direct: globs of the files the direct lane may push without a pull request or a review. A
# glob is matched with fnmatchcase against the repository-relative path in forward slashes, so `*`
# crosses `/`. `check` refuses one that matches any of these paths, which full's list covers: the
# CI definitions, the hook registry and a hook, release machinery, the binding itself, and (added
# per binding) every path a [[gate]] run or ci line names; and one whose literal prefix, the text
# before its first `*`, `?` or `[`, starts with a directory below, since a wildcard there reaches
# files the probes do not name.
DIRECT_PROBES = (".github/workflows/x.yml", ".github/workflows/x.yaml", ".github/actions/x/action.yml",
                 ".github/actions/x/action.yaml", "actions/x/action.yml", "hooks/hooks.json", "hooks/x.py",
                 "hooks/x.sh", "release/x.py", "release/x.ps1", ".claude/ouro.toml",
                 ".claude/ouro.local.toml", ".claude/settings.json",
                 *(prefix + name for prefix in ("", "bin/") for name in
                   ("ouro-direct.py", "ouro-process.py", "ouro-binding.py", "Push-Direct.ps1", "Push-Branch.ps1", "Get-LandBinding.ps1")))
DIRECT_PREFIXES = (".github/", "actions/", "hooks/", "release/", ".claude/")


def gate_paths(data):
    """The repository paths the [[gate]] run lines name: tokens with a slash or an extension."""
    out = []
    for g in data.get("gate", []):
        lines = [g.get(k) for k in ("run", "ci")] if isinstance(g, dict) else []
        for tok in [t for line in lines if isinstance(line, str) for t in line.split()]:
            tok = tok.strip("\"'")
            tok = tok[len(OURO_TOKEN) + 1:] if tok.startswith(OURO_TOKEN + "/") else tok
            if not tok.startswith("-") and (("/" in tok and not tok.endswith("/")) or re.search(r"\.\w+$", tok)):
                out.append(tok)
    return out


# [models] roles and the aliases each may hold -- never a pinned version, so a role follows the
# current model. Absent is the skill's own default, named beside each role in docs/binding.md.
MODEL_ROLES = ("builder", "builder_trivial", "reviewer")
MODEL_VALUES = ("opus", "sonnet", "haiku", "fable")
# The contract's eight states and two modifiers: fixed by it, never declared under [labels].
CONTRACT_LABELS = ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage",
                   "idea", "umbrella", "architecture", "trivial", "checkpoint")

LOCAL_NAME = "ouro.local.toml"

# The token a [[gate]].run may use for the plugin root.
OURO_TOKEN = "<ouro>"

# table -> {key: expected type}. Lists are checked for element type str; a dict-typed key is a
# free-key sub-table whose keys are data (not schema) and whose values must be strings.
SCHEMA = {
    "repo": {"slug": str, "default_branch": str, "protected_branch_prefixes": list, "branch_prefix": str, "checkout": str, "setup": str, "release_notes": list},
    "labels": {"scope": list, "area": list, "type": list},
    "gate": {"run": str, "ci": str, "paths": list, "cheap": bool, "direct_code_check": bool},
    "ship": {"policy": str, "review": str, "landing": str, "sync": str, "merge": str,
             "copilot_bot_id": str, "external_cli": str, "external_model": str,
             "external_effort": str, "test_first_for_bugs": bool, "direct": list,
             "unattended_classes": list, "validation": str, "validation_checks": list, "direct_max_lines": int, "direct_max_files": int},
    # Every key optional; each holds one of MODEL_VALUES, checked below. Absent means the
    # reading skill's own default.
    "models": {role: str for role in MODEL_ROLES},
    "rolling_issues": {"loop_runs": str, "docs_freshness": str, "comments_freshness": str},
    # Every key is optional: an absent [docs] table means the docs gate runs on its own
    # defaults. One key per configurable parameter the gate exposes.
    "docs": {
        "extensions": list,        # what makes a code-span token path-shaped
        "exclude": list,           # scope exclusions, substring-matched
        "suppress_prefixes": list, # references never reported (another repo, build output)
        "generated_pattern": str,  # regex for a generated doc tree to skip
        "html_globs": list,        # HTML pages whose relative hrefs and <code> paths resolve
        "index_path": str,         # the doc index; with indexed_trees, arms both index checks
        "indexed_trees": list,     # trees whose docs must be indexed; with index_path, arms both
        "index_exempt": list,      # REGEXES exempting a path from the coverage check
        "planning_paths": list,    # REGEXES for docs where work-remaining content is allowed
        "report_only": list,       # signals that never block
        "banned": dict,            # known-dead substring -> reason. Free keys: they are data.
    },
    "authority": {"local": list},
    "overlays": {"implement": list, "review": list, "land": list},
    "owner": {"role": str, "ruling_approvers": list},
}
REQUIRED = ("schema", "repo.slug", "repo.default_branch", "ship.policy", "ship.review", "owner.ruling_approvers")

# Lists whose entries are regexes, not literals. An invalid pattern is a configuration error the
# consumer can fix here; `check` catches only what Python's re refuses, and the docs gate matches
# with .NET (the driver reads repo.release_notes with Python's re).
REGEX_KEYS = {"docs": ("index_exempt", "planning_paths", "generated_pattern"), "repo": ("release_notes",)}


def lookup(data, dotted):
    """Walk a dotted key through tables and lists; KeyError if any segment is absent."""
    cur = data
    for seg in dotted.split("."):
        if isinstance(cur, list) and seg.isdigit():
            cur = cur[int(seg)]
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            raise KeyError(dotted)
    return cur


def _check_table(name, table, spec, errors):
    for key, value in table.items():
        if key not in spec:
            errors.append(f"unknown key: {name}.{key}")
        elif not isinstance(value, spec[key]) or (spec[key] is bool) != isinstance(value, bool):
            errors.append(f"{name}.{key}: expected {spec[key].__name__}")
        elif spec[key] is list and not all(isinstance(x, str) for x in value):
            errors.append(f"{name}.{key}: every entry must be a string")
        elif spec[key] is dict and not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            # A free-key sub-table: the KEYS are data (substrings to match), so a typo in one is
            # undetectable by construction and is not an error. What is checkable is that each
            # carries a string reason -- the signal that consumes this blocks, and a blocked
            # build has to be able to say why.
            errors.append(f"{name}.{key}: every entry must map a string to a string reason")
        elif spec[key] is dict and any(k == "" for k in value):
            # An empty substring is inside every line of every doc, so it would turn the whole
            # corpus into findings for a signal that blocks.
            errors.append(f"{name}.{key}: an empty key matches every line; give the substring")
        elif key in REGEX_KEYS.get(name, ()):
            # A scalar regex key is checked too: generated_pattern is a single pattern, and an
            # invalid one is refused here like a list entry.
            for pat in (value if isinstance(value, list) else [value]):
                try:
                    re.compile(pat)
                except re.error as exc:
                    errors.append(f"{name}.{key}: {pat!r} is not a valid regex ({exc})")


def validate(data):
    """Return the list of violations; empty means the binding is valid."""
    errors = []
    for key in data:
        if key != "schema" and key not in SCHEMA:
            errors.append(f"unknown table: {key}")
    if data.get("schema") != 1:
        errors.append("schema: must be 1")
    for name, spec in SCHEMA.items():
        if name not in data:
            continue
        if name == "gate":
            gates = data["gate"]
            if not isinstance(gates, list) or not all(isinstance(g, dict) for g in gates):
                errors.append("gate: must be an array of tables ([[gate]])")
                continue
            for i, g in enumerate(gates):
                _check_table(f"gate.{i}", {k: v for k, v in g.items() if k != "areas"}, spec, errors)
                if "areas" in g:
                    errors.append(f"gate.{i}.areas: paths is the only gate selector; delete areas, and give the gate paths to scope it (one without paths runs on every change)")
                if not g.get("run"):
                    errors.append(f"gate.{i}.run: required and non-empty")
                elif isinstance(g["run"], str) and OURO_TOKEN in g["run"] and plugin_root() is None:
                    errors.append(f"gate.{i}.run: {_no_plugin_root()}")
                # `gates` prints one run per line, so a line break -- any str.splitlines splits on,
                # a trailing one too, hence the padding -- would print one gate as two.
                if isinstance(g.get("run"), str) and len(f".{g['run']}.".splitlines()) > 1:
                    errors.append(f"gate.{i}.run: holds a line break ({g['run']!r}); a run is one command line, printed one per line by gates")
                # ci is optional -- CI runs it in place of run when declared -- and checked the
                # same way as run, but only when present: an absent ci is not required.
                if "ci" in g:
                    if not g["ci"]:
                        errors.append(f"gate.{i}.ci: must be non-empty")
                    elif isinstance(g["ci"], str) and OURO_TOKEN in g["ci"] and plugin_root() is None:
                        errors.append(f"gate.{i}.ci: {_no_plugin_root()}")
                    if isinstance(g["ci"], str) and len(f".{g['ci']}.".splitlines()) > 1:
                        errors.append(f"gate.{i}.ci: holds a line break ({g['ci']!r}); a run is one command line, printed one per line by gates")
                # paths is optional -- the gates Action's selection reads it -- and an empty list
                # is refused: read as no paths it would mean "always runs", the opposite of what
                # an empty selector says. Type and string entries are _check_table's findings.
                if isinstance(g.get("paths"), list):
                    if not g["paths"]:
                        errors.append(f"gate.{i}.paths: must be a non-empty list")
                    elif "" in g["paths"]:
                        errors.append(f"gate.{i}.paths: every entry must be a non-empty pathspec")
                if g.get("direct_code_check") is True and not g.get("paths"):
                    errors.append(f"gate.{i}.direct_code_check: requires non-empty paths")
        elif not isinstance(data[name], dict):
            errors.append(f"{name}: must be a table")
        else:
            _check_table(name, data[name], spec, errors)
    for dotted in REQUIRED:
        try:
            if not lookup(data, dotted) and dotted != "schema":
                errors.append(f"{dotted}: required and non-empty")
        except (KeyError, IndexError):
            errors.append(f"{dotted}: required")
    repo = data.get("repo") if isinstance(data.get("repo"), dict) else {}
    bp = repo.get("branch_prefix")
    if isinstance(bp, str):
        protected = [p for p in repo.get("protected_branch_prefixes", []) if isinstance(p, str) and p]
        if not bp or re.search(r"\s|/|\.\.", bp):
            errors.append(f"repo.branch_prefix: {bp!r} must be non-empty and hold no slash, whitespace or '..'")
        elif any(bp.startswith(p) or f"{bp}/".startswith(p) for p in protected):
            errors.append(f"repo.branch_prefix: {bp!r} equals or starts with a repo.protected_branch_prefixes entry, so the driver would cut branches an agent never pushes to")
    # setup is one command line the driver runs once in each worktree it creates: checked as a gate's run is
    st = repo.get("setup")
    if isinstance(st, str):
        if not st:
            errors.append("repo.setup: must be non-empty")
        elif OURO_TOKEN in st and plugin_root() is None:
            errors.append(f"repo.setup: {_no_plugin_root()}")
        if len(f".{st}.".splitlines()) > 1:
            errors.append(f"repo.setup: holds a line break ({st!r}); a setup is one command line")
    labels = data.get("labels") if isinstance(data.get("labels"), dict) else {}
    # GitHub label names are case-insensitive, and the shape gate matches them with -contains,
    # which ignores case too: both rules compare lowered names.
    sets = {key: [x for x in labels[key] if isinstance(x, str)] for key in ("scope", "area", "type") if isinstance(labels.get(key), list)}
    for key, entries in sets.items():
        for entry in entries:
            if entry.lower() in CONTRACT_LABELS:
                errors.append(f"labels.{key}: {entry!r} is a state or modifier the contract fixes, so it is not declared under [labels]")
    if "area" in sets and "type" in sets:
        overlap = {x.lower() for x in sets["area"]} & {x.lower() for x in sets["type"]}
        if overlap:
            errors.append(f"labels.area and labels.type must be disjoint (both contain: {', '.join(sorted(overlap))}) - one label would satisfy both of the shape gate's counts")
    ship = data.get("ship") if isinstance(data.get("ship"), dict) else {}
    if "validation" in ship and ship["validation"] not in ("local", "ci"):
        errors.append("ship.validation: must be one of local, ci")
    if ship.get("validation") == "ci" and "validation_checks" not in ship:
        errors.append("ship.validation_checks: required when ship.validation = \"ci\"")
    checks = ship.get("validation_checks")
    if isinstance(checks, list) and all(isinstance(name, str) for name in checks):
        if not checks:
            errors.append("ship.validation_checks: must be a non-empty list")
        if any(not name.strip() or len(f".{name}.".splitlines()) > 1
               or any(ord(char) < 32 or ord(char) == 127 for char in name) for name in checks):
            errors.append("ship.validation_checks: names must be non-blank single-line strings")
        if len(set(checks)) != len(checks):
            errors.append("ship.validation_checks: names must be unique")
    if "policy" in ship and ship["policy"] not in POLICIES:
        errors.append(f"ship.policy: must be one of {', '.join(POLICIES)}")
    if "review" in ship and ship["review"] not in REVIEWS:
        errors.append(f"ship.review: must be one of {', '.join(REVIEWS)}")
    if "sync" in ship and ship["sync"] not in SYNCS:
        errors.append(f"ship.sync: must be one of {', '.join(SYNCS)}")
    if "merge" in ship and ship["merge"] not in MERGES:
        errors.append(f"ship.merge: must be one of {', '.join(MERGES)}")
    # A non-string landing is a type error _check_table already named, and it is kept out of the
    # dict lookups below: `in` on a dict hashes its operand, where the tuples above only compare.
    landing = ship["landing"] if isinstance(ship.get("landing"), str) else None
    if "landing" in ship and landing not in LANDINGS:
        errors.append(f"ship.landing: must be one of {', '.join(LANDINGS)}")
    # A preset and an explicit key beside it are one declaration made twice: agreeing is redundant,
    # disagreeing has no reading, so it is refused with both halves named rather than one silently
    # winning over the other. A non-string key is left to the type error _check_table gave it.
    for key, want in zip(("sync", "merge"), LANDINGS.get(landing, ())):
        if isinstance(ship.get(key), str) and ship[key] != want:
            errors.append(f'ship.landing = "{landing}" and ship.{key} = "{ship[key]}" disagree: '
                          f'that landing means {key} = "{want}"')
    if ship.get("review") == "none" and ship.get("policy") in ("trivial-merge", "merge-on-green"):
        errors.append(f"ship.review = \"none\" forbids ship.policy = \"{ship['policy']}\" (no independent review, no unattended merge)")
    if isinstance(ship.get("unattended_classes"), list):
        if ship.get("policy") != "merge-on-green":
            errors.append("ship.unattended_classes: only allowed with ship.policy = \"merge-on-green\"")
        for cls in ship["unattended_classes"]:
            if cls not in UNATTENDED_CLASSES:
                errors.append(f"ship.unattended_classes: {cls!r} must be one of {', '.join(UNATTENDED_CLASSES)}")
    if ship.get("review") == "copilot" and not ship.get("copilot_bot_id"):
        errors.append("ship.copilot_bot_id: required when ship.review = \"copilot\"")
    if ship.get("review") == "external-audit" and not ship.get("external_cli"):
        errors.append("ship.external_cli: required when ship.review = \"external-audit\"")
    if "external_cli" in ship and ship["external_cli"] not in EXTERNAL_CLIS:
        errors.append(f"ship.external_cli: must be one of {', '.join(EXTERNAL_CLIS)}")
    if isinstance(ship.get("external_model"), str) and not ship["external_model"]:
        errors.append("ship.external_model: must be a non-empty string")
    if isinstance(ship.get("external_effort"), str) and not ship["external_effort"]:
        errors.append("ship.external_effort: must be a non-empty string")
    for key in ("direct_max_lines", "direct_max_files"):
        if type(ship.get(key)) is int and ship[key] <= 0:
            errors.append(f"ship.{key}: must be a positive integer")
    if isinstance(ship.get("direct"), list):
        probes = tuple(gate_paths(data)) + DIRECT_PROBES
        for glob in ship["direct"]:
            if not isinstance(glob, str):
                continue
            prefix = re.split(r"[*?\[]", glob, maxsplit=1)[0]
            rule = next((d for d in DIRECT_PREFIXES if prefix.startswith(d)), None)
            if rule:
                errors.append(f"ship.direct: {glob!r} has the literal prefix {prefix!r}, under {rule!r}, which the direct lane never pushes (full's list: CI definitions, hooks, release machinery, the binding)")
                continue
            hit = next((p for p in probes if fnmatch.fnmatchcase(p, glob)), None)
            if hit:
                errors.append(f"ship.direct: {glob!r} matches {hit!r}, which the direct lane never pushes (full's list: CI definitions, hooks, release machinery, the binding, the gates' own scripts)")
                continue
            # A wildcard or class in the first segment reaches every top-level directory, the
            # protected ones included (`*.js` reaches a JavaScript action; `[.]github` spells one),
            # and can miss every probe: such a glob is refused, named for what it reaches.
            first = glob.split("/", 1)[0]
            if re.search(r"[*?\[]", first):
                errors.append(f"ship.direct: {glob!r} has a wildcard in its first path segment, so it reaches every top-level directory, the ones the direct lane never pushes included (full's list: CI definitions, hooks, release machinery, the binding); name the directory")
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    for role in MODEL_ROLES:
        if role in models and models[role] not in MODEL_VALUES:
            errors.append(f"models.{role}: must be one of {', '.join(MODEL_VALUES)}")
    return errors


def from_landing(data, dotted):
    """The value [ship].landing implies for an absent ship.sync or ship.merge, else None.

    A binding may declare the preset alone, and a reader asks for the key it needs. With no
    landing declared there is nothing to imply: the key stays absent and the reader's own
    default -- today's behaviour, which is what merge-squash names -- applies.
    """
    if dotted not in ("ship.sync", "ship.merge"):
        return None
    ship = data.get("ship") if isinstance(data.get("ship"), dict) else {}
    landing = ship["landing"] if isinstance(ship.get("landing"), str) else None
    pair = LANDINGS.get(landing)
    return None if pair is None else pair[0 if dotted == "ship.sync" else 1]


def plugin_root():
    """This script's own plugin root, or None when the script is not inside an ouro plugin.

    Vendor-Ouro.ps1 copies this file flat into the consumer's scripts directory, and a consumer
    tree is not the plugin. The marker is .claude-plugin/plugin.json two levels up naming ouro:
    a consumer that is itself a plugin has that file and another name, and the same two-levels-up
    read in Test-AgentReadyShape.ps1 tells them apart the same way. The manifest is read as
    read_toml reads the binding: utf-8-sig for the byte-order mark Windows
    PowerShell 5.1 writes when asked for UTF-8, and the RecursionError a pathologically nested
    array throws mid-parse caught beside the rest, so an unreadable or malformed manifest is no
    plugin root rather than a traceback.
    """
    root = Path(__file__).resolve().parent.parent
    try:
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None
    return root if isinstance(manifest, dict) and manifest.get("name") == "ouro" else None


def _no_plugin_root():
    """The one line a run naming <ouro> gets where this copy has no plugin root."""
    return f"{OURO_TOKEN} is the plugin root, and this copy sits in {Path(__file__).resolve().parent}, outside any ouro plugin, where the token names nothing"


# The one diff every gate selector reads: from the merge base of base and head to head, with no
# rename pairing and no configured diff helper or text conversion, which the repository's own
# configuration could point at a command. --name-only lists the touched paths; git exits nonzero
# only when it cannot read the diff.
def changed_diff(base, head, paths):
    return ["diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
            "--ignore-submodules=none", f"{base}...{head}", "--", *paths]


def selected(data, touches=None):
    """The [[gate]] entries that run, in binding order: one with no paths always, one with paths when
    `touches(paths)` says the diff touches one of them. No `touches` selects every gate."""
    return [g for g in data.get("gate", []) if touches is None or not g.get("paths") or touches(g["paths"])]


def git_touches(base, head):
    """`touches` for `selected`, reading changed_diff in the working directory's repository."""
    def touches(paths):
        r = subprocess.run(["git", *changed_diff(base, head, paths)], capture_output=True)
        if r.returncode:
            sys.exit(f"error: the diff {base}...{head} cannot be read: {r.stderr.decode('utf-8', 'replace').strip()}")
        return bool(r.stdout.strip(b"\0"))
    return touches


def gate_runs(gates):
    """Each gate's run, in order, with <ouro> replaced by the plugin root."""
    runs = [g["run"] for g in gates]
    if not any(isinstance(run, str) and OURO_TOKEN in run for run in runs):
        return runs
    root = plugin_root()
    if root is None:
        sys.exit(f"error: {_no_plugin_root()}")
    return [run.replace(OURO_TOKEN, str(root)) if isinstance(run, str) else run for run in runs]


ON_BLOCK = re.compile(r"(?m)^[\"']?on[\"']?[ \t]*:(.*(?:\n(?![^ \t\r\n]).*)*)")  # the `on:` line and every indented or blank line after it
PULL_REQUEST = re.compile(r"(?<![\w.-])pull_request(?![\w-])")  # not pull_request_target, nor github.event.pull_request in an expression


def names_pull_request(text):
    """Whether a workflow's top-level `on:` names the pull_request event, comments aside.
    shortcut: a `branches` filter is not read, so a workflow filtered off the default branch counts; read it when a repository needs it."""
    m = ON_BLOCK.search(re.sub(r"(?m)(^|[ \t])#.*", r"\1", text.lstrip("\ufeff")))
    return bool(m and PULL_REQUEST.search(m.group(1)))


def pr_workflows(default, cwd=None, env=None):
    """The workflow files at origin/<default> whose `on:` names pull_request: what runs a pull request's checks."""
    def git(*args):
        r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True)
        if r.returncode:
            sys.exit(f"error: git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
        return r.stdout.decode("utf-8", "replace")
    ref = f"origin/{default}"
    names = git("ls-tree", "-z", "--full-tree", "--name-only", ref, "--", ".github/workflows/").split("\0")
    return [n for n in names if n.endswith((".yml", ".yaml")) and names_pull_request(git("show", f"{ref}:{n}"))]


def default_path():
    try:
        # git prints the root as raw path bytes (UTF-8 on Windows). Decoded with the locale encoding
        # (cp1252 on a Windows python outside UTF-8 mode), a non-ASCII root names no directory or
        # raises; os.fsdecode decodes the bytes the way the filesystem names them, on every OS.
        root = os.fsdecode(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        sys.exit("error: not inside a git work tree and no path given")
    return Path(root) / ".claude" / "ouro.toml"


def read_toml(p):
    # utf-8-sig reads the UTF-8 byte-order mark Windows PowerShell 5.1 writes when asked for
    # UTF-8. The committed binding's call site tests is_file() and the overlay's tests exists(), so
    # a directory at the overlay's name reaches here and is refused; neither test guarantees the file
    # opens, so OSError is caught here too, alongside the RecursionError a pathologically nested array throws mid-parse; an
    # OSError's own message repeats the path, so it is named by its strerror.
    try:
        return tomllib.loads(p.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
        sys.exit(f"error: {p}: {e.strerror if isinstance(e, OSError) else e}")


def merge_local(data, local, path):
    """Merge the machine-local overlay over the committed binding, in place. The whitelist is
    structural -- a [repo] table holding only checkout -- so a machine may move where the repo
    lives, never policy: an unreviewed file must not be able to change ship, owner or gates.
    Anything else (a quoted "repo.checkout" scalar included) is a hard error, empty tables too."""
    for table, value in local.items():
        if table == "repo" and isinstance(value, dict):
            for key in value:
                if key != "checkout":
                    sys.exit(f"error: {path}: repo.{key}: only the [repo] table's checkout key may be set in a local overlay")
        else:
            offender = f"{table}.{next(iter(value))}" if isinstance(value, dict) and value else table
            sys.exit(f"error: {path}: {offender}: only the [repo] table's checkout key may be set in a local overlay")
    if isinstance(local.get("repo"), dict) and "checkout" in local["repo"]:
        target = data.get("repo")
        if isinstance(target, dict):
            target["checkout"] = local["repo"]["checkout"]
        elif target is None:
            data["repo"] = {"checkout": local["repo"]["checkout"]}
        # else: the committed [repo] is not a table -- validate names that; the overlay waits.
    return data


def load(path, overlay=True):
    """Read a binding, merged with its machine-local overlay. overlay=False reads the committed
    file alone - `check` needs it to warn about a checkout that sits in shared config."""
    p = Path(path) if path else default_path()
    if not p.is_file():
        sys.exit(f"error: no binding at {p}")
    data = read_toml(p)
    local = p.with_name(LOCAL_NAME)
    if overlay and local.exists():
        merge_local(data, read_toml(local), local)
    return data


def check(path):
    """Return (warning or None, violations) for one binding. The warning keys off the committed
    file, so a checkout arriving only through the overlay - the point of the overlay - is silent."""
    p = Path(path) if path else default_path()
    warning = None
    if "checkout" in load(p, overlay=False).get("repo", {}):
        warning = f"warning: repo.checkout is a per-machine path in shared config - move it to .claude/{LOCAL_NAME} (gitignored) or omit it"
    return warning, validate(load(p))


def main(argv):
    if not argv or argv[0] not in ("check", "get", "gates", "direct", "pr-workflows"):
        sys.exit(__doc__.strip())
    cmd, rest = argv[0], argv[1:]
    if cmd == "pr-workflows":
        for name in pr_workflows(load(rest[0] if rest else None)["repo"]["default_branch"]):
            print(name)
        return 0
    if cmd == "direct":
        where = None
        if "--binding" in rest:
            i = rest.index("--binding")
            if i + 1 >= len(rest):  # 1 means paths were printed: a usage error is 2, as every other error here
                print("usage: --binding needs a path", file=sys.stderr)
                return 2
            where, rest = rest[i + 1], rest[:i] + rest[i + 2:]
        try:
            globs = load(where).get("ship", {}).get("direct") or []
        except SystemExit as e:  # load() exits with its message (already prefixed); here that is exit 2, never 1
            print(e.code, file=sys.stderr)
            return 2
        if not globs:
            print("error: ship.direct is empty or absent: the direct lane is off", file=sys.stderr)
            return 2
        missed = [p for p in rest if not any(fnmatch.fnmatchcase(p, g) for g in globs)]
        if missed:
            print("\n".join(missed))
        return 1 if missed else 0
    if cmd == "check":
        warning, errors = check(rest[0] if rest else None)
        if warning:
            print(warning)
        if errors:
            print("\n".join(errors))
            return 1
        print("ok")
        return 0
    if cmd == "get":
        if len(rest) < 1:
            sys.exit("usage: ouro-binding.py get <dotted.key> [path]")
        data = load(rest[1] if len(rest) > 1 else None)
        try:
            value = lookup(data, rest[0])
        except (KeyError, IndexError, ValueError):
            value = from_landing(data, rest[0])
            if value is None:
                sys.exit(f"error: no such key: {rest[0]}")
        print(value if isinstance(value, str) else json.dumps(value))
        return 0
    base, head, as_json, where = None, "HEAD", False, None
    while rest:
        arg, rest = rest[0], rest[1:]
        if arg in ("--changed", "--head") and rest:
            base, head = (rest[0], head) if arg == "--changed" else (base, rest[0])
            rest = rest[1:]
        elif arg == "--json" or (where is None and not arg.startswith("-")):
            as_json, where = (True, where) if arg == "--json" else (as_json, arg)
        else:
            sys.exit("usage: ouro-binding.py gates [--changed <base> [--head <rev>]] [--json] [path]")
    gates = selected(load(where), None if base is None else git_touches(base, head))
    for line in [json.dumps(gates)] if as_json else gate_runs(gates):
        print(line)
    return 0


def selftest():
    base = """
schema = 1
[repo]
slug = "owner/name"
default_branch = "main"
[[gate]]
run = "make test"
[[gate]]
run = "make core-test"
paths = ["core/"]
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["someone"]
"""
    # An unsupported python must stop before a binding read, with one line
    # naming the floor, not a traceback. Faked in a subprocess: the version is replaced before
    # this file's module body runs, the way an older interpreter would present it.
    this = str(Path(__file__).resolve())
    fake = (
        "import runpy, sys; sys.version_info = (3, 11, 2, 'final', 0); "
        f"sys.argv = [{this!r}, 'check', 'no-such-binding.toml']; "
        f"runpy.run_path({this!r}, run_name='__main__')"
    )
    old = subprocess.run([sys.executable, "-c", fake], capture_output=True, text=True)
    assert old.returncode != 0, (old.returncode, old.stdout, old.stderr)
    assert old.stderr.strip().splitlines() == ["ouro-binding.py needs Python 3.12+ (tomllib); this python is 3.11"], old.stderr

    # git prints the work-tree root as UTF-8. Decoded with the locale encoding -- cp1252 on a
    # Windows python outside UTF-8 mode -- a non-ASCII root names no directory, and a byte cp1252
    # leaves undefined (0x81, inside U+0141) is a reader traceback instead of a named error.
    # Faked the same way: UTF-8 mode off, locale.getencoding replaced before this file runs.
    for name in (f"r{chr(0xE9)}po", f"r{chr(0x141)}po"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Path(tmp) / name
            try:
                (repo / ".claude").mkdir(parents=True)
            except (OSError, UnicodeError) as e:
                print(f"skip: cannot create a directory named {name!a} ({e})")
                continue
            (repo / ".claude" / "ouro.toml").write_text(base, encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, check=True)
            cp1252 = (
                "import locale, runpy, sys; locale.getencoding = lambda: 'cp1252'; "
                f"sys.argv = [{this!r}, 'get', 'repo.slug']; "
                f"runpy.run_path({this!r}, run_name='__main__')"
            )
            got = subprocess.run([sys.executable, "-X", "utf8=0", "-c", cp1252], cwd=repo, capture_output=True, encoding="utf-8", errors="replace")
            assert (got.returncode, got.stdout.strip()) == (0, "owner/name"), (name, got.returncode, got.stdout, got.stderr)

    # On Windows with output piped, print() uses the ANSI code page, so without the reconfigure a
    # message holding a character that code page cannot encode is a traceback, not the message.
    # PYTHONIOENCODING forces that encoding on every OS, so the case fails on ubuntu too.
    ch = chr(0x6587)
    with tempfile.TemporaryDirectory() as tmp:
        binding = Path(tmp) / "ouro.toml"
        binding.write_text(base + f'[[gate]]\nrun = "echo {ch}"\n[labels]\narea = ["{ch}"]\ntype = ["{ch}"]\n', encoding="utf-8")
        for args, code, want in ((["check"], 1, f"both contain: {ch}"), (["get", "labels.area.0"], 0, ch), (["gates"], 0, f"echo {ch}")):
            got = subprocess.run([sys.executable, this, *args, str(binding)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "cp1252"})
            out, err = got.stdout.decode("utf-8", errors="replace"), got.stderr.decode("utf-8", errors="replace")
            assert got.returncode == code and want in out and "Traceback" not in err, (args, got.returncode, out, err)

    # paths is the only selector: areas is refused with its remedy, and --changed selects by the
    # diff from the merge base, a gate with no paths on every change; an unreadable diff is an error.
    refused = validate(tomllib.loads(base.replace('run = "make test"', 'areas = ["*"]\nrun = "make test"')))
    assert [e for e in refused if e.startswith("gate.0.areas: paths is the only gate selector")], refused
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        def commit(name):
            (Path(tmp) / name).parent.mkdir(exist_ok=True)
            (Path(tmp) / name).write_text(name, encoding="utf-8")
            for args in (["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qm", name]):
                subprocess.run(["git", *args], cwd=tmp, capture_output=True, check=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True, check=True)
        commit("a.txt")
        subprocess.run(["git", "branch", "start"], cwd=tmp, capture_output=True, check=True)
        commit("core/x.txt")
        binding = Path(tmp).parent / (Path(tmp).name + "-ouro.toml")
        binding.write_text(base, encoding="utf-8")
        try:
            for args, code, want in ((["--changed", "start"], 0, "make test\nmake core-test\n"), (["--changed", "HEAD"], 0, "make test\n"),
                                     (["--changed", "start", "--head", "start"], 0, "make test\n"), (["--changed", "no-such-rev"], 1, "")):
                got = subprocess.run([sys.executable, this, "gates", *args, str(binding)], cwd=tmp, capture_output=True, encoding="utf-8")
                assert (got.returncode, got.stdout) == (code, want) and (code == 0 or "cannot be read" in got.stderr), (args, got)
            got = subprocess.run([sys.executable, this, "gates", "--changed", "HEAD", "--json", str(binding)], cwd=tmp, capture_output=True, encoding="utf-8")
            assert json.loads(got.stdout) == [{"run": "make test"}], got
        finally:
            binding.unlink()

    valid = tomllib.loads(base + "\n[overlays]\nimplement = []\nland = []\n")
    assert validate(valid) == [], validate(valid)
    with_prefix = lambda line: tomllib.loads(base.replace('default_branch = "main"\n', f'default_branch = "main"\n{line}\n'))
    assert validate(with_prefix('branch_prefix = "feature"')) == []
    for line in ('branch_prefix = ""', 'branch_prefix = "a/b"', 'branch_prefix = "a b"', 'branch_prefix = "a..b"',
                 'branch_prefix = "release"\nprotected_branch_prefixes = ["release/"]',
                 'branch_prefix = "release-x"\nprotected_branch_prefixes = ["release"]'):
        assert any(e.startswith("repo.branch_prefix: ") for e in validate(with_prefix(line))), line
    # setup is checked as a gate's run is: one non-empty line; an empty one or a line break is refused and named
    assert validate(with_prefix('setup = "npm ci --no-audit --no-fund"')) == []
    for value in ("", "npm ci" + chr(10) + "npm run build", "npm ci" + chr(13)):
        assert any(e.startswith("repo.setup: ") for e in validate(with_prefix("setup = " + json.dumps(value)))), value
    for table, key in (("models", "verifier"), ("rolling_issues", "drift_audit"), ("overlays", "drift")):
        errs = validate(tomllib.loads(base + f'\n[{table}]\n{key} = "x"\n'))
        assert any(f"unknown key: {table}.{key}" in e for e in errs), (table, key, errs)
    assert gate_runs(selected(valid)) == ["make test", "make core-test"]
    assert gate_runs(selected(valid, lambda paths: False)) == ["make test"]
    assert lookup(valid, "gate.1.run") == "make core-test"

    # Validation authority belongs to the repository, not the developer backend.
    validation_binding = lambda lines: tomllib.loads(base.replace("[ship]\n", "[ship]\n" + lines + "\n"))
    assert validate(validation_binding('validation = "local"')) == []
    ci_validation = validation_binding('validation = "ci"\nvalidation_checks = ["gates (windows)", "gates (linux)"]')
    assert validate(ci_validation) == [], validate(ci_validation)
    assert lookup(ci_validation, "ship.validation_checks") == ["gates (windows)", "gates (linux)"]
    assert validate(validation_binding('validation_checks = ["Build", "build"]')) == []  # Exact names, not case-folded.
    for lines, fragment in (
        ('validation = "unknown"', "ship.validation:"),
        ('validation = true', "ship.validation: expected str"),
        ('validation = "ci"', "ship.validation_checks: required"),
        ('validation_checks = []', "ship.validation_checks: must be a non-empty list"),
        ('validation_checks = "Build"', "ship.validation_checks: expected list"),
        ('validation_checks = [1]', "ship.validation_checks: every entry must be a string"),
        ('validation_checks = ["Build", "Build"]', "ship.validation_checks: names must be unique"),
        ('validation_checks = [" "]', "ship.validation_checks: names must be non-blank single-line strings"),
        ('validation_checks = ["Build\\nother"]', "ship.validation_checks: names must be non-blank single-line strings"),
    ):
        errors = validate(validation_binding(lines))
        assert any(fragment in error for error in errors), (lines, errors)

    # review is the reviewer's standards list: a list of strings, like implement. A string entry
    # is accepted and readable through get; a non-string entry and a bare string are refused.
    with_review = tomllib.loads(base + '\n[overlays]\nreview = ["docs/standards.md"]\n')
    assert validate(with_review) == [], validate(with_review)
    assert lookup(with_review, "overlays.review.0") == "docs/standards.md"
    errs = validate(tomllib.loads(base + '\n[overlays]\nreview = ["docs/standards.md", 3]\n'))
    assert errs == ["overlays.review: every entry must be a string"], errs
    errs = validate(tomllib.loads(base + '\n[overlays]\nreview = "docs/standards.md"\n'))
    assert errs == ["overlays.review: expected list"], errs

    # A run is one command line, and `gates` prints one per line: a run holding a line break would
    # print as two gates, so check refuses it and names the gate. Every break a line-reading
    # caller splits on, a trailing one included; a run without one is still accepted.
    for brk in ("\n", "\r", "\u2028"):
        for run in (f"make a{brk}make b", f"make a{brk}"):
            broken = tomllib.loads(base + f'[[gate]]\nrun = {json.dumps(run)}\n')
            assert any(e.startswith("gate.2.run: ") and "line break" in e for e in validate(broken)), (run, validate(broken))
    one_line = tomllib.loads(base + '[[gate]]\nrun = "make a && make b"\n')
    assert validate(one_line) == [], validate(one_line)

    # ci is optional and checked the same way as run: a gate without it is unchanged, one with a
    # non-empty single-line ci is accepted and readable through get, an empty ci is refused, and a
    # ci holding a line break is refused the same way a run's would be.
    without_ci = tomllib.loads(base + '\n[[gate]]\nrun = "make syntax"\n')
    assert validate(without_ci) == [], validate(without_ci)
    with_ci = tomllib.loads(base + '\n[[gate]]\nrun = "make syntax"\nci = "make syntax-ci"\n')
    assert validate(with_ci) == [], validate(with_ci)
    assert lookup(with_ci, "gate.2.ci") == "make syntax-ci"
    empty_ci = tomllib.loads(base + '\n[[gate]]\nrun = "make syntax"\nci = ""\n')
    errs = validate(empty_ci)
    assert errs == ["gate.2.ci: must be non-empty"], errs
    for brk in ("\n", "\r", "\u2028"):
        broken_ci = tomllib.loads(base + f'[[gate]]\nrun = "make syntax"\nci = {json.dumps(f"make a{brk}make b")}\n')
        assert any(e.startswith("gate.2.ci: ") and "line break" in e for e in validate(broken_ci)), (brk, validate(broken_ci))

    # paths is optional: a gate without it is the without_ci row above, one with a non-empty list
    # of non-empty pathspecs is accepted and readable through get. An empty list is refused rather
    # than read as no paths, so it cannot mean both "always runs" and "never selected".
    with_paths = tomllib.loads(base + '\n[[gate]]\nrun = "make syntax"\npaths = ["bin/", "*.toml"]\n')
    assert validate(with_paths) == [], validate(with_paths)
    assert lookup(with_paths, "gate.2.paths.0") == "bin/"
    for paths, want in (('"bin/"', "gate.2.paths: expected list"),
                        ("[]", "gate.2.paths: must be a non-empty list"),
                        ('[""]', "gate.2.paths: every entry must be a non-empty pathspec"),
                        ('["bin/", ""]', "gate.2.paths: every entry must be a non-empty pathspec"),
                        ("[1]", "gate.2.paths: every entry must be a string")):
        errs = validate(tomllib.loads(base + f'\n[[gate]]\nrun = "make syntax"\npaths = {paths}\n'))
        assert errs == [want], (paths, errs)

    # cheap is optional and a boolean: true and false pass, a string or a number is refused, naming the gate.
    for value, want in (("true", []), ("false", []), ('"yes"', ["gate.2.cheap: expected bool"]), ("1", ["gate.2.cheap: expected bool"])):
        errs = validate(tomllib.loads(base + f'\n[[gate]]\nrun = "make syntax"\ncheap = {value}\n'))
        assert errs == want, (value, errs)

    for value in ("true", "false"):
        scoped = base + f'\n[[gate]]\nrun = "make syntax"\npaths = ["src/"]\ndirect_code_check = {value}\n'
        assert validate(tomllib.loads(scoped)) == []
    assert any("direct_code_check: requires" in e for e in validate(tomllib.loads(
        base + '\n[[gate]]\nrun = "make syntax"\ndirect_code_check = true\n')))
    for value in ('"true"', "1"):
        assert any("direct_code_check: expected bool" in e for e in validate(tomllib.loads(
            base + f'\n[[gate]]\nrun = "make syntax"\npaths = ["src/"]\ndirect_code_check = {value}\n')))
    for key in ("direct_max_lines", "direct_max_files"):
        for value in ("1", "20"):
            assert validate(tomllib.loads(base.replace("[ship]\n", f"[ship]\n{key} = {value}\n"))) == []
        for value in ("true", "0", "-1", '"20"'):
            assert any(f"ship.{key}:" in e for e in validate(tomllib.loads(
                base.replace("[ship]\n", f"[ship]\n{key} = {value}\n"))))

    # A run may name the plugin root with <ouro>: check accepts it, gates hands out the expansion,
    # and a run without the token is untouched.
    token = tomllib.loads(base + '\n[[gate]]\nrun = "pwsh -File <ouro>/bin/x.ps1"\n')
    # Vendor-Ouro.ps1 copies the suites into a consumer tree, where this file has no plugin root
    # and these two rows do not apply; the rows below them hold everywhere.
    root = plugin_root()
    if root is not None:
        assert validate(token) == [], validate(token)
        assert gate_runs(selected(token)) == ["make test", "make core-test", f"pwsh -File {root}/bin/x.ps1"], gate_runs(selected(token))
        # A non-string run is check's finding to name; gates hands it on untouched rather than
        # raise on its way past the token-bearing one beside it.
        mixed = tomllib.loads(base + '\n[[gate]]\nrun = "pwsh -File <ouro>/bin/x.ps1"\n\n[[gate]]\nrun = 7\n')
        assert gate_runs(selected(mixed)) == ["make test", "make core-test", f"pwsh -File {root}/bin/x.ps1", 7], gate_runs(selected(mixed))

        # ci is refused the same way run is when it names <ouro> and the copy has no plugin root;
        # this copy has one, so a ci naming the token is accepted here.
        ci_token = tomllib.loads(base + '\n[[gate]]\nrun = "make syntax"\nci = "pwsh -File <ouro>/bin/y.ps1"\n')
        assert validate(ci_token) == [], validate(ci_token)

    # What decides a plugin root is the manifest two levels up, so the copy is put there and the
    # manifest walked through its answers: absent (Vendor-Ouro.ps1's flat copy in a consumer's
    # scripts directory), a consumer plugin's own name, one nested past any parser, ouro's behind
    # a byte-order mark, and ouro's plain. The first three refuse -- check on one line, gates on
    # stderr rather than print a run that resolves nowhere -- and the last two hand out the root,
    # which is the only row that holds the expansion wherever this file sits.
    #
    # The tree is reached by a name that is not its own resolved name, and spelled with a
    # non-ASCII character, where the OS allows either: resolve() folds a link as it folds the
    # junction and 8.3 forms a TEMP can carry, and the child's two streams are UTF-8 by the
    # reconfigure at the top of this file, so the reader says so.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / f"v{chr(0xE9)}ndor"
        try:
            home.mkdir()
        except (OSError, UnicodeError):
            home = Path(tmp) / "vendor"
            home.mkdir()
            print(f"skip: cannot name a directory with U+00E9 under {tmp}")
        reached = home
        link = Path(tmp) / "link"
        try:
            os.symlink(home, link, target_is_directory=True)
            reached = link
        except (OSError, NotImplementedError):
            print(f"skip: cannot make a directory symbolic link under {tmp}")
        vendored = reached / "scripts" / "ouro-binding.py"
        vendored.parent.mkdir()
        vendored.write_bytes(Path(this).read_bytes())
        binding = home / "ouro.toml"
        binding.write_text(base + '\n[[gate]]\nrun = "pwsh -File <ouro>/bin/x.ps1"\n', encoding="utf-8")
        where = str(vendored.parent.resolve())
        manifest = home / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir()
        ouro = json.dumps({"name": "ouro", "version": "1.0.0"}).encode("utf-8")
        nested = ("[" * 20000 + "]" * 20000).encode("utf-8")
        cases = ((None, False), (json.dumps({"name": "consumer-plugin"}).encode("utf-8"), False),
                 (nested, False), (b"\xef\xbb\xbf" + ouro, True), (ouro, True))
        for content, rooted in cases:
            why = "absent" if content is None else f"{content[:24]!r}"
            if content is not None:
                manifest.write_bytes(content)
            got = subprocess.run([sys.executable, str(vendored), "check", str(binding)], capture_output=True, encoding="utf-8", errors="replace")
            lines = got.stdout.strip().splitlines()
            assert got.returncode == (0 if rooted else 1), (why, got.returncode, got.stdout, got.stderr)
            if rooted:
                assert lines == ["ok"], (why, lines)
            else:
                assert len(lines) == 1, (why, lines)
                assert lines[0].startswith("gate.2.run: ") and where in lines[0] and "names nothing" in lines[0], (why, lines)
            got = subprocess.run([sys.executable, str(vendored), "gates", str(binding)], capture_output=True, encoding="utf-8", errors="replace")
            assert got.returncode == (0 if rooted else 1), (why, got.returncode, got.stdout, got.stderr)
            if rooted:
                assert got.stdout.strip().splitlines() == ["make test", "make core-test", f"pwsh -File {Path(where).parent}/bin/x.ps1"], (why, got.stdout)
            else:
                assert got.stdout == "", (why, got.stdout)
                assert where in got.stderr and "names nothing" in got.stderr, (why, got.stderr)

    # All four rolling-issue titles are optional and independent: a repo may name any subset.
    rolling = tomllib.loads(base + '\n[rolling_issues]\ndocs_freshness = "Docs freshness"\n')
    assert validate(rolling) == [], validate(rolling)
    assert lookup(rolling, "rolling_issues.docs_freshness") == "Docs freshness"
    comments = tomllib.loads(base + '\n[rolling_issues]\ncomments_freshness = "Comments freshness"\n')
    assert validate(comments) == [], validate(comments)
    assert lookup(comments, "rolling_issues.comments_freshness") == "Comments freshness"
    rolling_typo = tomllib.loads(base + '\n[rolling_issues]\ndocs_freshnes = "Docs freshness"\n')
    errs = validate(rolling_typo)
    assert any("unknown key: rolling_issues.docs_freshnes" in e for e in errs), errs

    # [docs] is optional, and so is every key in it.
    docs = tomllib.loads(base + r"""
[docs]
extensions = [".md", ".rs"]
exclude = ["/vendor/"]
suppress_prefixes = ["sibling-repo/"]
generated_pattern = "api/generated/"
html_globs = ["site/*.html"]
index_path = "docs/index.md"
indexed_trees = ["src/"]
index_exempt = ["(^|/)CHANGELOG[^/]*\\.md$"]
planning_paths = ["^docs/policy/"]
report_only = ["S9"]

[docs.banned]
"dead.example.com/old" = "renamed; Pages URLs do not follow renames"
"also-dead.example.org" = "host retired"
""")
    assert validate(docs) == [], validate(docs)
    assert lookup(docs, "docs.banned")["dead.example.com/old"].startswith("renamed")
    assert lookup(docs, "docs.report_only") == ["S9"]

    # A free-key sub-table takes arbitrary KEYS -- they are data, not schema, so a typo in one
    # is undetectable by construction. Every value must still be a string reason: the signal
    # that reads this blocks, and a blocked build has to be able to say why.
    bad_reason = tomllib.loads(base + '\n[docs.banned]\n"x.example.com" = 1\n')
    assert validate(bad_reason) == ["docs.banned: every entry must map a string to a string reason"], validate(bad_reason)

    # A typo in a docs key, and a typo in the sub-table's NAME, are both caught.
    docs_typo = tomllib.loads(base + '\n[docs]\nindex_pth = "docs/index.md"\n')
    assert validate(docs_typo) == ["unknown key: docs.index_pth"], validate(docs_typo)
    subtable_typo = tomllib.loads(base + '\n[docs.bannned]\n"x" = "y"\n')
    assert validate(subtable_typo) == ["unknown key: docs.bannned"], validate(subtable_typo)

    # Absent [docs] is the normal case: the gate runs on its own defaults.
    assert validate(tomllib.loads(base)) == []

    # Domain checks the type system cannot express. All three pass a type check and then break
    # the gate: an invalid regex throws mid-run, an empty banned key is inside every line of
    # every doc, and both feed signals that block.
    bad_regex = tomllib.loads(base + '\n[docs]\nindex_exempt = ["("]\n')
    assert validate(bad_regex) and "not a valid regex" in validate(bad_regex)[0], validate(bad_regex)
    bad_planning = tomllib.loads(base + '\n[docs]\nplanning_paths = ["["]\n')
    assert validate(bad_planning) and "not a valid regex" in validate(bad_planning)[0], validate(bad_planning)
    notes = lambda pats: validate(tomllib.loads(base.replace('default_branch = "main"', f'default_branch = "main"\nrelease_notes = {pats}')))
    assert notes('["^changes/"]') == [], notes('["^changes/"]')
    assert notes('["^changes/", "("]') and notes('["^changes/", "("]')[0].startswith("repo.release_notes: '(' is not a valid regex"), notes('["("]')
    empty_key = tomllib.loads(base + '\n[docs.banned]\n"" = "reason"\n')
    assert validate(empty_key) == ["docs.banned: an empty key matches every line; give the substring"], validate(empty_key)

    # An empty banned table is the unarmed signal, not an error.
    assert validate(tomllib.loads(base + "\n[docs.banned]\n")) == []

    # The example a consumer is told to copy must itself validate, and must carry every table.
    example = Path(__file__).resolve().parent.parent / "templates" / "ouro.toml.example"
    if example.exists():
        parsed = read_toml(example)
        assert validate(parsed) == [], validate(parsed)
        assert set(SCHEMA) <= set(parsed), f"tables missing from the example: {sorted(set(SCHEMA) - set(parsed))}"

    unknown = tomllib.loads(base + "\n[repo.extra]\nx = 1\n")
    unknown["repo"]["slgu"] = "typo"
    errs = validate(unknown)
    assert any("unknown key: repo.slgu" in e for e in errs), errs
    assert any("unknown key: repo.extra" in e for e in errs), errs

    unattended = tomllib.loads(base.replace('policy = "stop-at-pr"', 'policy = "trivial-merge"').replace('review = "adversarial-review"', 'review = "none"'))
    errs = validate(unattended)
    assert len(errs) == 1 and "forbids" in errs[0], errs

    # merge-on-green is refused without a reviewer; unattended_classes needs it, takes only the
    # three classes, and is absent by default (the driver reads that as express and standard).
    green = base.replace('policy = "stop-at-pr"', 'policy = "merge-on-green"')
    assert validate(tomllib.loads(green)) == [] and "unattended_classes" not in tomllib.loads(green)["ship"]
    errs = validate(tomllib.loads(green.replace('review = "adversarial-review"', 'review = "none"')))
    assert len(errs) == 1 and "forbids" in errs[0] and "merge-on-green" in errs[0], errs
    assert validate(tomllib.loads(green.replace("[ship]\n", '[ship]\nunattended_classes = ["express", "full"]\n'))) == []
    errs = validate(tomllib.loads(green.replace("[ship]\n", '[ship]\nunattended_classes = ["trivial"]\n')))
    assert errs == ["ship.unattended_classes: 'trivial' must be one of express, standard, full"], errs
    errs = validate(tomllib.loads(base.replace("[ship]\n", '[ship]\nunattended_classes = ["express"]\n')))
    assert errs == ['ship.unattended_classes: only allowed with ship.policy = "merge-on-green"'], errs

    # sync and merge are independent: a repo may rebase to sync and still squash to land. Each is
    # optional, and absent means today's behaviour, so a binding written before them keeps working.
    for key, good, bad in (("sync", "rebase", "rebse"), ("merge", "merge", "rebase-merge")):
        assert validate(tomllib.loads(base.replace("[ship]\n", f'[ship]\n{key} = "{good}"\n'))) == []
        errs = validate(tomllib.loads(base.replace("[ship]\n", f'[ship]\n{key} = "{bad}"\n')))
        assert len(errs) == 1 and errs[0].startswith(f"ship.{key}: must be one of"), (key, errs)
    absent = tomllib.loads(base)
    assert "sync" not in absent["ship"] and "merge" not in absent["ship"] and validate(absent) == []

    # landing is the preset over that pair. The four names and their pairs are written out here,
    # not read from LANDINGS, so a name the product stops generating fails this loop. Declaring the
    # preset alone is the normal case; the explicit keys may stand beside it where they agree, and
    # `get` answers either key from the preset, since a reader asks for the key it needs.
    assert set(LANDINGS) == {"merge-squash", "rebase-squash", "rebase-merge", "merge-merge"}, sorted(LANDINGS)
    for name, pair in (("merge-squash", ("merge", "squash")), ("rebase-squash", ("rebase", "squash")),
                       ("rebase-merge", ("rebase", "merge")), ("merge-merge", ("merge", "merge"))):
        alone = tomllib.loads(base.replace("[ship]\n", f'[ship]\nlanding = "{name}"\n'))
        assert validate(alone) == [], (name, validate(alone))
        assert (from_landing(alone, "ship.sync"), from_landing(alone, "ship.merge")) == pair, name
        agreeing = tomllib.loads(base.replace("[ship]\n", f'[ship]\nlanding = "{name}"\nsync = "{pair[0]}"\nmerge = "{pair[1]}"\n'))
        assert validate(agreeing) == [], (name, validate(agreeing))
    # An unknown preset is refused by name, the spelling reversed included: the pair is ordered
    # sync-merge, and "squash-merge" reads as a landing while naming no pair.
    for bad in ("squash-merge", "rebase"):
        errs = validate(tomllib.loads(base.replace("[ship]\n", f'[ship]\nlanding = "{bad}"\n')))
        assert len(errs) == 1 and errs[0].startswith("ship.landing: must be one of"), (bad, errs)
    # A preset disagreeing with an explicit key beside it is refused with both halves named.
    for key, name, bad in (("sync", "rebase-squash", "merge"), ("merge", "merge-merge", "squash")):
        errs = validate(tomllib.loads(base.replace("[ship]\n", f'[ship]\nlanding = "{name}"\n{key} = "{bad}"\n')))
        assert len(errs) == 1 and f'ship.landing = "{name}"' in errs[0] and f'ship.{key} = "{bad}"' in errs[0], (key, errs)
    # A landing that is not a string is the type error, and the lookups that follow it must not
    # throw on one: `in` on a dict hashes its operand, where a tuple's only compares, so an
    # unguarded list here is a TypeError instead of two named lines.
    for value in ("1", '["merge-squash"]'):
        errs = validate(tomllib.loads(base.replace("[ship]\n", f'[ship]\nlanding = {value}\n')))
        assert errs == ["ship.landing: expected str", f"ship.landing: must be one of {', '.join(LANDINGS)}"], (value, errs)
    # A sync or merge that is not a string is its own type error, and the preset does not call it
    # a disagreement on top of that.
    errs = validate(tomllib.loads(base.replace("[ship]\n", '[ship]\nlanding = "merge-squash"\nsync = 1\n')))
    assert errs == ["ship.sync: expected str", f"ship.sync: must be one of {', '.join(SYNCS)}"], errs
    # Absent is the normal case for every binding written before the preset existed, and nothing is
    # implied for a key it does not name: `get` still exits 1, and the reader's default stands.
    assert "landing" not in absent["ship"] and validate(absent) == []
    assert from_landing(absent, "ship.sync") is None and from_landing(absent, "ship.merge") is None
    assert from_landing(tomllib.loads(base.replace("[ship]\n", '[ship]\nlanding = "rebase-merge"\n')), "ship.policy") is None

    # End to end through `get`, the one way a skill reads a binding: a preset declared alone
    # answers both keys, and a binding declaring neither answers neither.
    with tempfile.TemporaryDirectory() as tmp:
        preset = Path(tmp) / "ouro.toml"
        preset.write_text(base.replace("[ship]\n", '[ship]\nlanding = "rebase-merge"\n'), encoding="utf-8")
        for key, want in (("ship.sync", "rebase"), ("ship.merge", "merge")):
            got = subprocess.run([sys.executable, this, "get", key, str(preset)], capture_output=True, text=True)
            assert (got.returncode, got.stdout.strip()) == (0, want), (key, got.returncode, got.stdout, got.stderr)
        preset.write_text(base, encoding="utf-8")
        got = subprocess.run([sys.executable, this, "get", "ship.sync", str(preset)], capture_output=True, text=True)
        assert got.returncode == 1 and "no such key" in got.stderr, (got.returncode, got.stdout, got.stderr)

    copilot = tomllib.loads(base.replace('review = "adversarial-review"', 'review = "copilot"'))
    assert validate(copilot) == ["ship.copilot_bot_id: required when ship.review = \"copilot\""]

    # The hard rename's retired value is refused as outside the set, with no alias: the set
    # itself never carries it again. The retired spelling is not written here -- a sweep for it
    # finds nothing under bin/ -- so a bogus value stands in; tests/install-ouro's own rows cover
    # the retired spelling by name, where that sweep allows it.
    bogus_review = tomllib.loads(base.replace('review = "adversarial-review"', 'review = "bogus"'))
    errs = validate(bogus_review)
    assert len(errs) == 1 and errs[0] == f"ship.review: must be one of {', '.join(REVIEWS)}", errs

    # external-audit requires ship.external_cli, named the way copilot_bot_id is; external_cli is
    # allowed under any review value (a manual run may still want it declared), and must name a
    # shipped adapter. external_model is optional and, when present, must be non-empty -- an
    # empty string is not "default", it is a typo.
    external = tomllib.loads(base.replace('review = "adversarial-review"', 'review = "external-audit"'))
    assert validate(external) == ["ship.external_cli: required when ship.review = \"external-audit\""], validate(external)
    assert set(EXTERNAL_CLIS) == {"grok", "claude", "codex", "copilot"}, EXTERNAL_CLIS  # the shipped set; a further adapter widens this tuple, this assertion, and nothing else here
    for cli in EXTERNAL_CLIS:
        good = tomllib.loads(base.replace('review = "adversarial-review"', f'review = "external-audit"\nexternal_cli = "{cli}"'))
        assert validate(good) == [], (cli, validate(good))
    # nosuchcli, not a real adapter name -- a name outside the set, one that never ships.
    unknown_cli = tomllib.loads(base.replace('review = "adversarial-review"', 'review = "external-audit"\nexternal_cli = "nosuchcli"'))
    errs = validate(unknown_cli)
    assert len(errs) == 1 and errs[0].startswith("ship.external_cli: must be one of ")
    # A set, not the tuple's declared order: a further adapter widens EXTERNAL_CLIS in whatever
    # order it is declared there, which need not match this list's own order.
    assert {c.strip() for c in errs[0].removeprefix("ship.external_cli: must be one of ").split(",")} == set(EXTERNAL_CLIS), errs
    # external_cli is not tied to external-audit: declared under another review value it is
    # merely unused, never an error.
    stray_cli = tomllib.loads(base.replace("review = \"adversarial-review\"", 'review = "adversarial-review"\nexternal_cli = "grok"'))
    assert validate(stray_cli) == [], validate(stray_cli)
    with_model = tomllib.loads(base.replace('review = "adversarial-review"',
        'review = "external-audit"\nexternal_cli = "grok"\nexternal_model = "grok-4-fast"'))
    assert validate(with_model) == [], validate(with_model)
    empty_model = tomllib.loads(base.replace('review = "adversarial-review"',
        'review = "external-audit"\nexternal_cli = "grok"\nexternal_model = ""'))
    assert validate(empty_model) == ["ship.external_model: must be a non-empty string"], validate(empty_model)
    with_effort = tomllib.loads(base.replace('review = "adversarial-review"',
        'review = "external-audit"\nexternal_cli = "grok"\nexternal_effort = "max"'))
    assert validate(with_effort) == [], validate(with_effort)
    empty_effort = tomllib.loads(base.replace('review = "adversarial-review"',
        'review = "external-audit"\nexternal_cli = "grok"\nexternal_effort = ""'))
    assert validate(empty_effort) == ["ship.external_effort: must be a non-empty string"], validate(empty_effort)

    # ship.direct is optional and empty when absent; a docs glob passes, `**` and a glob naming a
    # gate's own script are refused with the glob and the probe it matched.
    def with_ship(lines):
        return tomllib.loads(base.replace("[ship]\n", "[ship]\n" + lines + "\n"))
    absent = tomllib.loads(base)
    assert "direct" not in absent["ship"] and validate(absent) == []
    assert validate(with_ship("direct = []")) == []
    assert validate(with_ship('direct = ["docs/rationale.md", "README.md"]')) == []
    errs = validate(with_ship('direct = ["**"]'))
    assert len(errs) == 1 and errs[0].startswith("ship.direct: '**' matches '.github/workflows/x.yml'"), errs
    scripted = base.replace('run = "make core-test"', 'run = "python3 tools/check.py --all"').replace("[ship]\n", '[ship]\ndirect = ["tools/*.py", "tools/other.py"]\n')
    errs = validate(tomllib.loads(scripted))
    assert len(errs) == 1 and "'tools/*.py' matches 'tools/check.py'" in errs[0], errs
    # A wildcard under a directory full's list owns is refused by its literal prefix, so the holes
    # the probes alone left (another extension, another name) are shut; a literal one is too.
    for glob in ("hooks/*", "hooks/*.py", "hooks/guard-*.py", "hooks/*.sh", "hooks/guard-commit.py", "release/*",
                 "release/export*.py", "release/**/*.py", ".claude/*", ".claude/*.json", ".claude/hooks/*",
                 ".github/workflows/*.yaml", ".github/*", "actions/**"):
        errs = validate(with_ship(f'direct = ["{glob}"]'))
        assert len(errs) == 1 and "has the literal prefix" in errs[0] and f"{glob!r}" in errs[0], (glob, errs)
    errs = validate(with_ship('direct = ["*.yaml"]'))
    assert len(errs) == 1 and "'*.yaml' matches '.github/workflows/x.yaml'" in errs[0], errs
    # A wildcard or class in the first segment reaches every top-level directory (`*.js` reaches a
    # JavaScript action, `[.]github` spells the directory): refused by that segment, named.
    for glob in ("[.]github/workflows/c*.yml", ".git[h]ub/workflows/c*.yml", "?ooks/guard-*.py", "[h]ooks/guard-*.py",
                 "[r]elease/export*.py", "*/guard-x.py", "*.js", "*.md", "?.txt"):
        errs = validate(with_ship(f'direct = ["{glob}"]'))
        assert len(errs) == 1 and "has a wildcard in its first path segment" in errs[0], (glob, errs)
    for glob in ("docs/**", "docs/notes/*.md", "README.md", "docs/docs-governance.md"):
        assert validate(with_ship(f'direct = ["{glob}"]')) == [], glob
    # A script named only on a gate's ci line is a probe, and so is one named through <ouro>/.
    ci_only = base.replace('run = "make core-test"', 'run = "make core-test"\nci = "python3 tools/ci.py"').replace("[ship]\n", '[ship]\ndirect = ["tools/ci.py"]\n')
    errs = validate(tomllib.loads(ci_only))
    assert len(errs) == 1 and "'tools/ci.py' matches 'tools/ci.py'" in errs[0], errs
    through = base.replace('run = "make core-test"', 'run = "python3 <ouro>/bin/x.py"').replace("[ship]\n", '[ship]\ndirect = ["bin/*"]\n')
    errs = validate(tomllib.loads(through))
    assert len(errs) == 1 and "'bin/*' matches 'bin/x.py'" in errs[0], errs
    assert validate(with_ship("direct = [1]")) == ["ship.direct: every entry must be a string"]

    # `direct` prints the paths no glob matches and exits 1; fnmatch's own class syntax, where
    # [^d] is the set of ^ and d, not a negation; an empty key is exit 2 with the reason.
    import contextlib
    import io
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        def direct(globs, *paths):
            f = Path(tmp) / "b.toml"
            f.write_text(base.replace("[ship]\n", "[ship]\n" + globs + "\n"), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["direct", *paths, "--binding", str(f)])
            return code, out.getvalue().splitlines(), err.getvalue()
        assert direct('direct = ["docs/*", "README.md"]', "docs/a/b.md", "README.md") == (0, [], "")
        assert direct('direct = ["docs/*"]', "docs/a.md", "src/b.txt", "Docs/c.md") == (1, ["src/b.txt", "Docs/c.md"], "")
        code, out, _ = direct('direct = ["[^d]*"]', "dx", "x")
        assert (code, out) == (1, ["x"]), (code, out)
        code, out, err = direct("direct = []", "docs/a.md")
        assert (code, out) == (2, []) and "ship.direct is empty or absent" in err, (code, out, err)
        code, out, err = direct("", "docs/a.md")
        assert code == 2 and "ship.direct is empty or absent" in err, (code, err)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["direct", "docs/a.md", "--binding", str(Path(tmp) / "missing.toml")])
        assert code == 2 and out.getvalue() == "" and err.getvalue().startswith("error:"), (code, err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["direct", "docs/a.md", "--binding"])
        assert code == 2 and out.getvalue() == "" and "--binding needs a path" in err.getvalue(), (code, err.getvalue())

    # The pull_request event in the top-level `on:` only, in each form a workflow writes it.
    for text, want in (("on: pull_request\n", True), ("on: [push, pull_request]\njobs: {}\n", True),
                       ("name: x\non:\n  push:\n\n  pull_request:\n    branches: [main]\njobs: {}\n", True),
                       ("on:\r\n  push:\r\n\r\n  pull_request:\r\n", True), ("\ufeffon: pull_request\n", True),
                       ("on:\n  pull_request_target:\njobs:\n  a:\n    if: github.event.pull_request.draft == false\n", False),
                       ("on:\n  push:\n  # pull_request:\njobs:\n  pull_request:\n", False), ("jobs: {}\n", False)):
        assert names_pull_request(text) is want, text

    # [models] is optional, and so is every key in it; each key holds one of MODEL_VALUES.
    # Absent means the reading skill's own default (docs/binding.md), never a pinned version.
    assert set(MODEL_ROLES) == {"builder", "builder_trivial", "reviewer"}, MODEL_ROLES
    models = tomllib.loads(base + "\n[models]\n" + "\n".join(f'{role} = "sonnet"' for role in MODEL_ROLES) + "\n")
    assert validate(models) == [], validate(models)
    assert lookup(models, "models.reviewer") == "sonnet"
    for role in MODEL_ROLES:
        for value in MODEL_VALUES:
            one = tomllib.loads(base + f'\n[models]\n{role} = "{value}"\n')
            assert validate(one) == [], (role, value, validate(one))
        bad = tomllib.loads(base + f'\n[models]\n{role} = "gpt5"\n')
        errs = validate(bad)
        assert errs == [f"models.{role}: must be one of {', '.join(MODEL_VALUES)}"], (role, errs)
    models_typo = tomllib.loads(base + '\n[models]\nbuidler = "sonnet"\n')
    errs = validate(models_typo)
    assert errs == ["unknown key: models.buidler"], errs
    assert "models" not in tomllib.loads(base) and validate(tomllib.loads(base)) == []

    overlapping = tomllib.loads(base + '\n[labels]\nscope = []\narea = ["docs", "app"]\ntype = ["docs", "bug"]\n')
    errs = validate(overlapping)
    assert len(errs) == 1 and "disjoint" in errs[0] and "docs" in errs[0], errs

    # The eight states and two modifiers are the contract's: a set naming one passes the type check
    # and the shape gate then counts it as the set's own. GitHub label names ignore case, and so
    # does the gate's -contains, so Agent-Ready is agent-ready and Docs overlaps docs.
    def labelled(scope, area, typ):
        return tomllib.loads(base + f"""
[labels]
scope = {json.dumps(scope)}
area = {json.dumps(area)}
type = {json.dumps(typ)}
""")
    for where, label, sets in (
        ("labels.type", "agent-ready", ([], ["app"], ["bug", "agent-ready"])),
        ("labels.area", "trivial", ([], ["app", "trivial"], ["bug"])),
        ("labels.scope", "needs-ruling", (["needs-ruling"], ["app"], ["bug"])),
        ("labels.type", "Agent-Ready", ([], ["app"], ["Agent-Ready"])),
        ("labels.scope", "architecture", (["architecture"], ["app"], ["bug"])),
        ("labels.area", "architecture", ([], ["app", "architecture"], ["bug"])),
    ):
        errs = validate(labelled(*sets))
        assert len(errs) == 1 and where in errs[0] and label in errs[0] and "contract" in errs[0], (where, label, errs)
    errs = validate(labelled([], ["Docs", "app"], ["docs", "bug"]))
    assert len(errs) == 1 and "disjoint" in errs[0], errs
    # Every one of the contract's ten labels is refused. The list is written out here, not read
    # from CONTRACT_LABELS, so dropping a name from that tuple fails this loop.
    for name in ("agent-ready", "human-ready", "needs-ruling", "blocked", "needs-triage",
                 "idea", "umbrella", "architecture", "trivial", "checkpoint"):
        errs = validate(labelled([], ["app"], ["bug", name]))
        assert len(errs) == 1 and name in errs[0] and "contract" in errs[0], (name, errs)

    with tempfile.TemporaryDirectory() as tmp:
        committed, local = Path(tmp) / "ouro.toml", Path(tmp) / LOCAL_NAME
        committed.write_text(base, encoding="utf-8")
        local.write_text('[repo]\ncheckout = "D:/machine/name"\n', encoding="utf-8")
        assert lookup(load(committed), "repo.checkout") == "D:/machine/name"
        assert check(committed) == (None, []), check(committed)

        local.write_text('[ship]\npolicy = "trivial-merge"\n', encoding="utf-8")
        try:
            load(committed)
            raise AssertionError("a key outside the whitelist must fail loudly")
        except SystemExit as e:
            assert "ship.policy" in str(e), e

        local.unlink()
        committed.write_text(base.replace("[repo]", '[repo]\ncheckout = "D:/machine/name"'), encoding="utf-8")
        warning, errs = check(committed)
        assert warning and LOCAL_NAME in warning and errs == [], (warning, errs)

        local.write_text('"repo.checkout" = "D:/quoted"\n', encoding="utf-8")  # a scalar, not the [repo] table
        try:
            load(committed)
            raise AssertionError("a quoted repo.checkout scalar must fail loudly, not merge silently")
        except SystemExit as e:
            assert "repo.checkout" in str(e), e

        # The check is repo policy, so a machine cannot turn it off.
        local.write_text('[ship]\nreview = "none"\n', encoding="utf-8")
        try:
            load(committed)
            raise AssertionError("an overlay setting ship.review must fail loudly")
        except SystemExit as e:
            assert "ship.review" in str(e), e

        local.write_text("[ship]\n", encoding="utf-8")  # empty forbidden table is still forbidden
        try:
            load(committed)
            raise AssertionError("an empty non-repo table must fail loudly")
        except SystemExit as e:
            assert "ship" in str(e), e

        local.write_bytes('[repo]\ncheckout = "D:/x"\n'.encode("utf-16"))  # PowerShell 5's default encoding
        try:
            load(committed)
            raise AssertionError("a non-UTF-8 overlay must fail with a named error, not a traceback")
        except SystemExit as e:
            assert LOCAL_NAME in str(e), e

        # A byte-identical copy of a valid binding with a UTF-8 BOM prepended is what Windows
        # PowerShell 5.1 writes when asked for UTF-8; utf-8-sig reads it whether the BOM sits on
        # the committed binding or the local overlay, and the UTF-16 overlay above stays refused.
        committed.write_bytes(b"\xef\xbb\xbf" + base.encode("utf-8"))
        local.unlink()
        assert lookup(load(committed), "repo.slug") == "owner/name"

        committed.write_text(base, encoding="utf-8")
        local.write_bytes(b"\xef\xbb\xbf" + '[repo]\ncheckout = "D:/x"\n'.encode("utf-8"))
        assert lookup(load(committed), "repo.checkout") == "D:/x"

        # A directory handed to read_toml is an OSError on both platforms -- it bypasses is_file(),
        # which is the hole the comment above describes -- so it must exit 1 on one named line, not
        # a traceback. The strerror text itself differs by platform (Permission denied on Windows,
        # Is a directory on Linux) and is not asserted, only its shape and that it names the path once.
        with tempfile.TemporaryDirectory() as tmp2:
            d = Path(tmp2)
            try:
                read_toml(d)
                raise AssertionError("a directory handed to read_toml must fail loudly, not a traceback")
            except SystemExit as e:
                msg = str(e)
                assert msg.startswith(f"error: {d}: ") and "[Errno" not in msg and msg.count(str(d)) == 1, msg

        # A binding nested deep enough exhausts Python's recursion limit mid-parse; that
        # RecursionError is caught the same way, through get and not just through a direct call.
        # The message names the binding's path, which the child writes as UTF-8 whatever the
        # locale, so it is read as UTF-8 and asked again under a directory with a non-ASCII name,
        # as TEMP is under a Windows profile named with one.
        for sub in ("", f"r{chr(0x141)}po"):
            deep = Path(tmp) / sub / "ouro.toml"
            try:
                deep.parent.mkdir(exist_ok=True)
            except (OSError, UnicodeError) as e:
                print(f"skip: cannot create a directory named {sub!a} ({e})")
                continue
            deep.write_text(base + "x = " + "[" * 2000, encoding="utf-8")
            got = subprocess.run([sys.executable, this, "get", "repo.slug", str(deep)],
                                  capture_output=True, encoding="utf-8", errors="replace")
            assert got.returncode == 1 and got.stdout == "" and "Traceback" not in got.stderr, got
            assert got.stderr.strip().startswith(f"error: {deep}: ") and "maximum recursion depth exceeded" in got.stderr, got.stderr
        committed.write_text(base, encoding="utf-8")

        local.write_text('[repo]\ncheckout = "D:/machine/name"\n', encoding="utf-8")
        committed.write_text(base.replace("[repo]\n", "[[repo]]\n"), encoding="utf-8")
        warning, errs = check(committed)  # malformed committed [repo]: validate names it, no traceback
        assert any("repo: must be a table" in e for e in errs), errs

        # A dotted key whose segment is digits int() cannot read -- a superscript that passes
        # isdigit(), or one long enough to exceed Python's integer-string limit -- answers like
        # any absent key, not a traceback.
        committed.write_text(base, encoding="utf-8")
        for key in (f"gate.{chr(0xB2)}", "gate." + "9" * 5000):
            got = subprocess.run([sys.executable, this, "get", key, str(committed)],
                                  capture_output=True, encoding="utf-8", errors="replace")
            assert got.returncode == 1 and got.stdout == "" and "Traceback" not in got.stderr, (key, got.returncode, got.stdout, got.stderr)
            assert got.stderr.strip() == f"error: no such key: {key}", (key, got.stderr)

        # A directory at the overlay's name is refused by name, not silently ignored.
        local.unlink()
        local.mkdir()
        try:
            load(committed)
            raise AssertionError("a directory at the overlay's name must fail loudly, not merge silently")
        except SystemExit as e:
            assert str(local) in str(e), e
        local.rmdir()
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
