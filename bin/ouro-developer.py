#!/usr/bin/env python3
"""Install developer-wide ouro skills, inspect configuration, or remove owned installation files.

    python3 ouro-developer.py install [--backend claude|codex] [--model NAME] [--effort LEVEL] [--home PATH]
    python3 ouro-developer.py inspect [--check] [--home PATH]
    python3 ouro-developer.py uninstall [--home PATH]

The repository binding and unrelated user settings are never written. Re-running install updates
only unmodified files this installer owns. Existing developer configuration is preserved.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from ouro import developer, node, sh


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("install", "inspect", "uninstall"))
    ap.add_argument("--home", type=Path, help="alternate developer home, useful for isolated setup")
    ap.add_argument("--backend", choices=("claude", "codex"), default="codex")
    ap.add_argument("--model", help="set all worker roles in a newly created developer binding")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--check", action="store_true", help="also run the selected runtime's preflight")
    args = ap.parse_args(argv)
    home = (args.home or Path.home()).resolve()
    config = home / ".config" / "ouro" / "developer.toml" if args.home else developer.default_path()
    manifest_path = config.with_name("installation.json")
    expected_files = {str(config.with_name("launch.py")),
                      *(str(home / ".agents" / "skills" / ("ouro-" + n) / "SKILL.md") for n in ("run", "triage", "weekly", "init"))}
    try:
        if (manifest_path.is_symlink() or manifest_path.resolve() != manifest_path.absolute()
                or (manifest_path.exists() and manifest_path.stat().st_nlink > 1)):
            raise sh.DriverError("installation manifest aliases another file")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": {}}
        if manifest_path.exists() and (set(manifest) != {"source", "files"} or not isinstance(manifest["source"], str)):
            raise sh.DriverError("invalid installation manifest")
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) - expected_files:
            raise sh.DriverError("invalid installation manifest")
        for name, expected in files.items():
            path = Path(name)
            if path.is_symlink() or path.resolve() != path.absolute():
                raise sh.DriverError("installation manifest path follows a symlink")
            if path.exists() and digest(path) != expected:
                raise sh.DriverError(f"owned file was edited; preserve it before updating: {path}")
        if args.command == "inspect":
            active = Path(os.environ.get("OURO_DEVELOPER_CONFIG") or config) if not args.home else config
            cfg = developer.load(active) if active.exists() or os.environ.get("OURO_DEVELOPER_CONFIG") else developer.Settings()
            print(json.dumps({"config": str(active), "backend": cfg.backend, "models": cfg.models,
                              "effort": cfg.effort, "source": manifest.get("source"), "owned_files": len(files)}, indent=2))
            if args.check:
                node.preflight(cfg)
                print("runtime preflight passed")
            return 0
        if args.command == "uninstall":
            for name in files:
                path = Path(name)
                if path.is_file():
                    path.unlink()
                    print(f"removed: {path}")
            manifest_path.unlink(missing_ok=True)
            print(f"preserved developer binding: {config}")
            return 0
        source = Path(__file__).resolve().parent.parent
        launcher = config.with_name("launch.py")
        plan = {launcher: "import os, sys\n" +
                f"os.environ.setdefault('OURO_DEVELOPER_CONFIG', {str(config)!r})\n" +
                f"sys.path.insert(0, {str(source / 'bin')!r})\n" +
                "from ouro.cli import main\nsys.exit(main(sys.argv[1:]))\n"}
        for name in ("run", "triage", "weekly", "init"):
            original = source / "skills" / name / "SKILL.md"
            text = original.read_text(encoding="utf-8")
            description = text.split("description: >", 1)[1].split("---", 1)[0].strip()
            description = description.split("disable-model-invocation:")[0].strip()
            description = re.sub(r"<([^<>]+)>", r"\1", description).replace("/ouro:", "$ouro-")
            plan[home / ".agents" / "skills" / ("ouro-" + name) / "SKILL.md"] = (
                f"---\nname: ouro-{name}\ndescription: >\n" +
                "\n".join("  " + line.strip() for line in description.splitlines()) +
                f"\n---\n\nRead the shared workflow at {original.as_posix()}.\n" +
                f"In that workflow, <ouro> is {source.as_posix()}.\n" +
                f"For driver commands use python3 \"{launcher.as_posix()}\" followed by the command and arguments.\n" +
                "Relay the driver's outcome and exit code. The driver owns tracker and git transitions.\n")
        config_text = f'[runtime]\nbackend = "{args.backend}"\n'
        if args.effort:
            config_text += f'effort = "{args.effort}"\n'
        if args.model:
            config_text += "\n[runtime.models]\n" + "".join(f"{role} = {json.dumps(args.model)}\n" for role in developer.ROLES)
        # Validate the complete plan before the first write.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="ouro-install-check-") as tmp:
            sample = Path(tmp) / "developer.toml"
            sample.write_text(config_text, encoding="utf-8")
            developer.load(config if config.exists() else sample)
        for path in plan:
            if path.resolve() != path.absolute():
                raise sh.DriverError(f"installation target follows a symlink: {path}")
            if path.exists() and str(path) not in files:
                raise sh.DriverError(f"refusing to overwrite an unowned file: {path}")
        if not config.exists():
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(config_text, encoding="utf-8")
            print(f"wrote: {config}")
        else:
            print(f"preserved: {config}")
        for path, text in plan.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            print(f"wrote: {path}")
        manifest_path.write_text(json.dumps({"source": str(source), "files": {str(p): digest(p) for p in plan}}, indent=2), encoding="utf-8")
        print("Skills installed for this developer. Restart the consuming Codex session if discovery has not refreshed.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, sh.DriverError) as ex:
        print(f"ouro developer setup: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
