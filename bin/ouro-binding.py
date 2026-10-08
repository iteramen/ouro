#!/usr/bin/env python3
"""ouro-binding.py - read and validate a repo's ouro binding (.claude/ouro.toml).

Usage:
    python3 ouro-binding.py check [path]
        Validate against docs/binding.md. Exit 0 and print "ok", or exit 1 listing every
        violation. Default path: .claude/ouro.toml under the working directory's git root.
    python3 ouro-binding.py get <dotted.key> [path]
        Print one value: strings raw, everything else as JSON. List entries by index
        (gate.0.run). Exit 1 if the key is absent, except that an absent ship.sync or
        ship.merge is answered from the [ship].landing preset, when one is declared.
    python3 ouro-binding.py gates <area-label> [path]
        Print the `run` command of every [[gate]] whose areas contain the label or "*",
        one per line, in binding order, each with every <ouro> replaced by this script's
        own plugin root.
    python3 ouro-binding.py --selftest
        Run the in-memory self-check.

A [[gate]].run may name the plugin root with the token <ouro>: `check` accepts it and `gates`
expands it. A vendored copy of this script sits flat in the consumer's scripts directory and has
no plugin root, so there both commands refuse such a run, naming that directory.

A gitignored .claude/ouro.local.toml beside the binding is merged over it; it may set
repo.checkout and nothing else.

The binding and its overlay are read as UTF-8, with a byte-order mark accepted; a file that
cannot be read is one `error: <path>: <reason>` line.

Stdlib only, Python 3.11+ (tomllib).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

# tomllib is why the floor is 3.11, and importing it on an older python dies with a traceback
# that does not say what to install. Read by index, not .major/.minor, so the selftest can stand
# a plain tuple in for sys.version_info.
if tuple(sys.version_info[:2]) < (3, 11):
    sys.exit(f"ouro-binding.py needs Python 3.11+ (tomllib); this python is {sys.version_info[0]}.{sys.version_info[1]}")

# Both streams: the error lines below name paths, and a caller reads them back the same way it
# reads the answers. The floor above is printed before this runs, and it is ASCII.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"): _stream.reconfigure(encoding="utf-8", errors="replace")

import tomllib
from pathlib import Path

POLICIES = ("stop-at-pr", "trivial-merge")
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
# ship.forbidden_check: whether bin/apply-manifest.py matches what it posts against the private
# token list. The first is the default, and absent means it.
FORBIDDEN_CHECKS = ("required", "off")
# [models] roles and the aliases each may hold -- never a pinned version, so a role follows the
# current model. Absent is the skill's own default, named beside each role in docs/binding.md.
MODEL_ROLES = ("builder", "builder_trivial", "reviewer", "verifier", "weekly")
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
    "repo": {"slug": str, "default_branch": str, "protected_branch_prefixes": list, "checkout": str},
    "labels": {"scope": list, "area": list, "type": list},
    "gate": {"areas": list, "run": str, "ci": str, "paths": list},
    "ship": {"policy": str, "review": str, "landing": str, "sync": str, "merge": str,
             "copilot_bot_id": str, "external_cli": str, "external_model": str,
             "test_first_for_bugs": bool, "forbidden_check": str},
    # Every key optional; each holds one of MODEL_VALUES, checked below. Absent means the
    # reading skill's own default.
    "models": {role: str for role in MODEL_ROLES},
    "rolling_issues": {"drift_audit": str, "loop_runs": str, "docs_freshness": str, "comments_freshness": str},
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
    "overlays": {"implement": list, "review": list, "land": list, "drift": list},
    "owner": {"role": str, "ruling_approvers": list},
}
REQUIRED = ("schema", "repo.slug", "repo.default_branch", "ship.policy", "ship.review", "owner.ruling_approvers")

# Lists whose entries are regexes, not literals. An invalid pattern is a configuration error the
# consumer can fix here; left to the gate it is an unhandled throw mid-run.
REGEX_KEYS = {"docs": ("index_exempt", "planning_paths", "generated_pattern")}


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
            # invalid one used to pass `check` and then throw from inside the gate.
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
                _check_table(f"gate.{i}", g, spec, errors)
                if not g.get("areas"):
                    errors.append(f"gate.{i}.areas: required and non-empty")
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
    if ship.get("review") == "none" and ship.get("policy") == "trivial-merge":
        errors.append("ship.review = \"none\" forbids ship.policy = \"trivial-merge\" (no independent review, no unattended merge)")
    if ship.get("review") == "copilot" and not ship.get("copilot_bot_id"):
        errors.append("ship.copilot_bot_id: required when ship.review = \"copilot\"")
    if ship.get("review") == "external-audit" and not ship.get("external_cli"):
        errors.append("ship.external_cli: required when ship.review = \"external-audit\"")
    if "external_cli" in ship and ship["external_cli"] not in EXTERNAL_CLIS:
        errors.append(f"ship.external_cli: must be one of {', '.join(EXTERNAL_CLIS)}")
    if isinstance(ship.get("external_model"), str) and not ship["external_model"]:
        errors.append("ship.external_model: must be a non-empty string")
    if "forbidden_check" in ship and ship["forbidden_check"] not in FORBIDDEN_CHECKS:
        errors.append(f"ship.forbidden_check: must be one of {', '.join(FORBIDDEN_CHECKS)}")
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


def gate_runs(data, area):
    """Every matching [[gate]]'s run, in binding order, with <ouro> replaced by the plugin root."""
    runs = [g["run"] for g in data.get("gate", []) if area in g.get("areas", []) or "*" in g.get("areas", [])]
    if not any(isinstance(run, str) and OURO_TOKEN in run for run in runs):
        return runs
    root = plugin_root()
    if root is None:
        sys.exit(f"error: {_no_plugin_root()}")
    return [run.replace(OURO_TOKEN, str(root)) if isinstance(run, str) else run for run in runs]


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
    if not argv or argv[0] not in ("check", "get", "gates"):
        sys.exit(__doc__.strip())
    cmd, rest = argv[0], argv[1:]
    if cmd == "check":
        warning, errors = check(rest[0] if rest else None)
        if warning:
            print(warning)
        if errors:
            print("\n".join(errors))
            return 1
        print("ok")
        return 0
    if len(rest) < 1:
        sys.exit(f"usage: ouro-binding.py {cmd} <{'dotted.key' if cmd == 'get' else 'area-label'}> [path]")
    data = load(rest[1] if len(rest) > 1 else None)
    if cmd == "get":
        try:
            value = lookup(data, rest[0])
        except (KeyError, IndexError, ValueError):
            value = from_landing(data, rest[0])
            if value is None:
                sys.exit(f"error: no such key: {rest[0]}")
        print(value if isinstance(value, str) else json.dumps(value))
        return 0
    for run in gate_runs(data, rest[0]):
        print(run)
    return 0


def selftest():
    base = """
schema = 1
[repo]
slug = "owner/name"
default_branch = "main"
[[gate]]
areas = ["*"]
run = "make test"
[[gate]]
areas = ["core"]
run = "make core-test"
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["someone"]
"""
    # Under a python older than 3.11 nothing here can run (tomllib), and that must be one line
    # naming the floor, not a traceback. Faked in a subprocess: the version is replaced before
    # this file's module body runs, the way an older interpreter would present it.
    this = str(Path(__file__).resolve())
    fake = (
        "import runpy, sys; sys.version_info = (3, 10, 0, 'final', 0); "
        f"sys.argv = [{this!r}, 'check', 'no-such-binding.toml']; "
        f"runpy.run_path({this!r}, run_name='__main__')"
    )
    old = subprocess.run([sys.executable, "-c", fake], capture_output=True, text=True)
    assert old.returncode != 0, (old.returncode, old.stdout, old.stderr)
    assert old.stderr.strip().splitlines() == ["ouro-binding.py needs Python 3.11+ (tomllib); this python is 3.10"], old.stderr

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
        binding.write_text(base + f'[[gate]]\nareas = ["*"]\nrun = "echo {ch}"\n[labels]\narea = ["{ch}"]\ntype = ["{ch}"]\n', encoding="utf-8")
        for args, code, want in ((["check"], 1, f"both contain: {ch}"), (["get", "labels.area.0"], 0, ch), (["gates", "core"], 0, f"echo {ch}")):
            got = subprocess.run([sys.executable, this, *args, str(binding)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "cp1252"})
            out, err = got.stdout.decode("utf-8", errors="replace"), got.stderr.decode("utf-8", errors="replace")
            assert got.returncode == code and want in out and "Traceback" not in err, (args, got.returncode, out, err)

    valid = tomllib.loads(base + "\n[overlays]\nimplement = []\nland = []\ndrift = [\"docs/forensics\"]\n")
    assert validate(valid) == [], validate(valid)
    assert gate_runs(valid, "core") == ["make test", "make core-test"]
    assert gate_runs(valid, "ui") == ["make test"]
    assert lookup(valid, "gate.1.run") == "make core-test"

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
            broken = tomllib.loads(base + f'[[gate]]\nareas = ["*"]\nrun = {json.dumps(run)}\n')
            assert any(e.startswith("gate.2.run: ") and "line break" in e for e in validate(broken)), (run, validate(broken))
    one_line = tomllib.loads(base + '[[gate]]\nareas = ["*"]\nrun = "make a && make b"\n')
    assert validate(one_line) == [], validate(one_line)

    # ci is optional and checked the same way as run: a gate without it is unchanged, one with a
    # non-empty single-line ci is accepted and readable through get, an empty ci is refused, and a
    # ci holding a line break is refused the same way a run's would be.
    without_ci = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "make syntax"\n')
    assert validate(without_ci) == [], validate(without_ci)
    with_ci = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "make syntax"\nci = "make syntax-ci"\n')
    assert validate(with_ci) == [], validate(with_ci)
    assert lookup(with_ci, "gate.2.ci") == "make syntax-ci"
    empty_ci = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "make syntax"\nci = ""\n')
    errs = validate(empty_ci)
    assert errs == ["gate.2.ci: must be non-empty"], errs
    for brk in ("\n", "\r", "\u2028"):
        broken_ci = tomllib.loads(base + f'[[gate]]\nareas = ["*"]\nrun = "make syntax"\nci = {json.dumps(f"make a{brk}make b")}\n')
        assert any(e.startswith("gate.2.ci: ") and "line break" in e for e in validate(broken_ci)), (brk, validate(broken_ci))

    # paths is optional: a gate without it is the without_ci row above, one with a non-empty list
    # of non-empty pathspecs is accepted and readable through get. An empty list is refused rather
    # than read as no paths, so it cannot mean both "always runs" and "never selected".
    with_paths = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "make syntax"\npaths = ["bin/", "*.toml"]\n')
    assert validate(with_paths) == [], validate(with_paths)
    assert lookup(with_paths, "gate.2.paths.0") == "bin/"
    for paths, want in (('"bin/"', "gate.2.paths: expected list"),
                        ("[]", "gate.2.paths: must be a non-empty list"),
                        ('[""]', "gate.2.paths: every entry must be a non-empty pathspec"),
                        ('["bin/", ""]', "gate.2.paths: every entry must be a non-empty pathspec"),
                        ("[1]", "gate.2.paths: every entry must be a string")):
        errs = validate(tomllib.loads(base + f'\n[[gate]]\nareas = ["*"]\nrun = "make syntax"\npaths = {paths}\n'))
        assert errs == [want], (paths, errs)

    # A run may name the plugin root with <ouro>: check accepts it, gates hands out the expansion,
    # and a run without the token is untouched.
    token = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "pwsh -File <ouro>/bin/x.ps1"\n')
    # Vendor-Ouro.ps1 copies the suites into a consumer tree, where this file has no plugin root
    # and these two rows do not apply; the rows below them hold everywhere.
    root = plugin_root()
    if root is not None:
        assert validate(token) == [], validate(token)
        assert gate_runs(token, "ui") == ["make test", f"pwsh -File {root}/bin/x.ps1"], gate_runs(token, "ui")
        # A non-string run is check's finding to name; gates hands it on untouched rather than
        # raise on its way past the token-bearing one beside it.
        mixed = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "pwsh -File <ouro>/bin/x.ps1"\n\n[[gate]]\nareas = ["*"]\nrun = 7\n')
        assert gate_runs(mixed, "ui") == ["make test", f"pwsh -File {root}/bin/x.ps1", 7], gate_runs(mixed, "ui")

        # ci is refused the same way run is when it names <ouro> and the copy has no plugin root;
        # this copy has one, so a ci naming the token is accepted here.
        ci_token = tomllib.loads(base + '\n[[gate]]\nareas = ["*"]\nrun = "make syntax"\nci = "pwsh -File <ouro>/bin/y.ps1"\n')
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
        binding.write_text(base + '\n[[gate]]\nareas = ["*"]\nrun = "pwsh -File <ouro>/bin/x.ps1"\n', encoding="utf-8")
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
            got = subprocess.run([sys.executable, str(vendored), "gates", "*", str(binding)], capture_output=True, encoding="utf-8", errors="replace")
            assert got.returncode == (0 if rooted else 1), (why, got.returncode, got.stdout, got.stderr)
            if rooted:
                assert got.stdout.strip().splitlines() == ["make test", f"pwsh -File {Path(where).parent}/bin/x.ps1"], (why, got.stdout)
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

    # forbidden_check is optional; the two values are written out here, not read from the tuple.
    for value in ("required", "off"):
        assert validate(tomllib.loads(base.replace("[ship]\n", f'[ship]\nforbidden_check = "{value}"\n'))) == [], value
    for value, want in (('"Off"', ["ship.forbidden_check: must be one of required, off"]),
                        ("false", ["ship.forbidden_check: expected str", "ship.forbidden_check: must be one of required, off"])):
        errs = validate(tomllib.loads(base.replace("[ship]\n", f"[ship]\nforbidden_check = {value}\n")))
        assert errs == want, (value, errs)

    # [models] is optional, and so is every key in it; each key holds one of MODEL_VALUES.
    # Absent means the reading skill's own default (docs/binding.md), never a pinned version.
    assert set(MODEL_ROLES) == {"builder", "builder_trivial", "reviewer", "verifier", "weekly"}, MODEL_ROLES
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
        local.write_text('[ship]\nforbidden_check = "off"\n', encoding="utf-8")
        try:
            load(committed)
            raise AssertionError("an overlay setting ship.forbidden_check must fail loudly")
        except SystemExit as e:
            assert "ship.forbidden_check" in str(e), e

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
