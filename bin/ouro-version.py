#!/usr/bin/env python3
"""ouro-version.py - print the installed ouro plugin's version, for a Claude Code status line.

Usage:
    python3 ouro-version.py
        Print "ouro <version>", or "ouro <version> (stale)" when the marketplace clone on this
        machine carries a newer version than the one installed. Print nothing, and exit 0, when
        the version cannot be read.
    python3 ouro-version.py --selftest
        Run the self-check.

The version is the user-scope install's, from Claude Code's plugin registry
(<config>/plugins/installed_plugins.json, the "ouro@ouro" entry with scope "user"; <config> is
$CLAUDE_CONFIG_DIR, else ~/.claude), not a repository's vendored copy. A project-scope install is
tied to one project's path, which this does not read, so it prints nothing for one. The newer side
of the stale check is the plugin manifest in the marketplace clone that known_marketplaces.json
names, at an absolute path. Where that cannot be read, or either version is not dotted numbers,
the version prints alone: staleness is never guessed. A status line runs this on every refresh,
so it reads at most three local files, never the network or git, never reads stdin, and never
fails: any error, or an installed version that is not plain version characters, prints nothing
and exits 0.

Stdlib only.
"""
import json
import os
import re
import sys
from pathlib import Path


def _read_json(path):
    path = Path(path)
    if not path.is_file():  # a directory or a FIFO is no file to read, and a FIFO would block
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _numbers(version):
    """The version as a tuple of ints, zeros trimmed from the end so 1.0 equals 1.0.0, or None
    when it is not dotted numbers."""
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,9})*", version):
        return None
    parts = [int(part) for part in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def status_token():
    """The status-line text, or "" when the installed version cannot be read."""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    plugins = config / "plugins"
    try:
        entries = _read_json(plugins / "installed_plugins.json")["plugins"]["ouro@ouro"]
        # One entry per install: the user-scope one is loaded in every session; a project-scope
        # one only in its projectPath, which this cannot see, so it shows nothing for it.
        entry = next(e for e in entries if isinstance(e, dict) and e.get("scope") == "user")
        installed = entry["version"].strip()
    except Exception:
        return ""
    # Plain version characters only, so a status line never takes a control character, a line
    # break or a character the console cannot encode.
    if not isinstance(installed, str) or not re.fullmatch(r"[0-9A-Za-z.+-]{1,64}", installed):
        return ""
    try:
        clone = Path(_read_json(plugins / "known_marketplaces.json")["ouro"]["installLocation"])
        if not clone.is_absolute():  # relative to nothing a status line can name
            return f"ouro {installed}"
        channel = _read_json(clone / ".claude-plugin" / "plugin.json")["version"]
    except Exception:
        return f"ouro {installed}"
    mine, newest = _numbers(installed), _numbers(channel)
    if mine is not None and newest is not None and newest > mine:
        return f"ouro {installed} (stale)"
    return f"ouro {installed}"


def selftest():
    import subprocess
    import tempfile

    this = str(Path(__file__).resolve())

    def run(registry=None, marketplaces=None, manifest=None, clone_dir="marketplaces/ouro"):
        with tempfile.TemporaryDirectory() as tmp:
            plugins = Path(tmp) / "plugins"
            plugins.mkdir()
            clone = plugins / clone_dir
            (clone / ".claude-plugin").mkdir(parents=True)
            if registry is not None:
                (plugins / "installed_plugins.json").write_text(registry, encoding="utf-8")
            if marketplaces is not None:
                text = marketplaces.replace("<clone>", json.dumps(str(clone))[1:-1])
                (plugins / "known_marketplaces.json").write_text(text, encoding="utf-8")
            if manifest is not None:
                (clone / ".claude-plugin" / "plugin.json").write_text(manifest, encoding="utf-8")
            # Run from the config directory, so a relative installLocation would resolve to the
            # real clone there and the relative-path case shows its refusal, not a missing file.
            got = subprocess.run([sys.executable, this], capture_output=True, text=True, cwd=tmp,
                                 env={**os.environ, "CLAUDE_CONFIG_DIR": tmp}, stdin=subprocess.DEVNULL)
            return got.returncode, got.stdout, got.stderr

    def reg(*entries):
        return json.dumps({"version": 2, "plugins": {"ouro@ouro": list(entries), "other@x": []}})

    user = lambda v: {"scope": "user", "installPath": "p", "version": v}
    known = '{"ouro": {"installLocation": "<clone>"}}'
    man = lambda v: json.dumps({"name": "ouro", "version": v})

    cases = [
        ("no registry", dict(), ""),
        ("malformed registry", dict(registry="{not json"), ""),
        ("no ouro@ouro entry", dict(registry=json.dumps({"plugins": {"other@x": [user("1.0.0")]}})), ""),
        ("an empty entry list", dict(registry=reg()), ""),
        ("an entry list that is not a list", dict(registry=json.dumps({"plugins": {"ouro@ouro": {"version": "1"}}})), ""),
        ("current", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=man("0.1.82")), "ouro 0.1.82\n"),
        ("stale", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=man("0.1.90")), "ouro 0.1.82 (stale)\n"),
        ("numeric, not string, comparison", dict(registry=reg(user("0.1.99")), marketplaces=known, manifest=man("0.1.100")), "ouro 0.1.99 (stale)\n"),
        ("a clone older than the install is not stale", dict(registry=reg(user("0.1.90")), marketplaces=known, manifest=man("0.1.82")), "ouro 0.1.90\n"),
        ("no marketplace record: the version alone", dict(registry=reg(user("0.1.82"))), "ouro 0.1.82\n"),
        ("no clone manifest: the version alone", dict(registry=reg(user("0.1.82")), marketplaces=known), "ouro 0.1.82\n"),
        ("a malformed clone manifest: the version alone", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest="{"), "ouro 0.1.82\n"),
        ("a version that is not dotted numbers: alone", dict(registry=reg(user("0.1.82-rc1")), marketplaces=known, manifest=man("0.1.90")), "ouro 0.1.82-rc1\n"),
        ("the user-scope install over a project-scope one",
         dict(registry=reg({"scope": "project", "version": "0.1.10"}, user("0.1.82")), marketplaces=known, manifest=man("0.1.82")),
         "ouro 0.1.82\n"),
        ("a project-scope install alone shows nothing", dict(registry=reg({"scope": "project", "version": "0.1.10"})), ""),
        ("a local-scope install alone shows nothing", dict(registry=reg({"scope": "local", "version": "0.1.5"})), ""),
        ("a whitespace version shows nothing", dict(registry=reg(user("  "))), ""),
        ("a version holding a non-version character shows nothing", dict(registry=reg(user("0.1.82\u2713"))), ""),
        ("a version holding a line break shows nothing", dict(registry=reg(user("0.1\n82"))), ""),
        ("the clone where known_marketplaces.json puts it, not the default path",
         dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=man("0.1.90"), clone_dir="elsewhere/clone"),
         "ouro 0.1.82 (stale)\n"),
        ("a byte-order mark on known_marketplaces.json", dict(registry=reg(user("0.1.82")), marketplaces="\ufeff" + known, manifest=man("0.1.90")),
         "ouro 0.1.82 (stale)\n"),
        ("1.0 against 1.0.0 is not stale", dict(registry=reg(user("1.0")), marketplaces=known, manifest=man("1.0.0")), "ouro 1.0\n"),
        ("a clone version that is not dotted numbers: alone", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=man("0.1.8_3")),
         "ouro 0.1.82\n"),
        ("a clone version in non-ASCII digits: alone", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=man("0.1.\u0669\u0660")),
         "ouro 0.1.82\n"),
        ("a clone version part too long to be one: alone", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=man("9" * 5000)),
         "ouro 0.1.82\n"),
        ("a clone version given as a number: alone", dict(registry=reg(user("0.1.82")), marketplaces=known, manifest=json.dumps({"version": 1})),
         "ouro 0.1.82\n"),
        ("a relative installLocation: alone",
         dict(registry=reg(user("0.1.82")), marketplaces='{"ouro": {"installLocation": "plugins/marketplaces/ouro"}}', manifest=man("0.1.90")),
         "ouro 0.1.82\n"),
    ]
    for name, kwargs, want in cases:
        code, out, err = run(**kwargs)
        assert (code, out, err) == (0, want, ""), (name, code, out, err)
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    try:
        token = status_token()
        if token:
            sys.stdout.buffer.write((token + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
    except Exception:
        pass
    sys.exit(0)
