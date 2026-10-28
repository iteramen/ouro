"""Codex Git capability proof for prospective and occupied worktrees."""
import os
import stat
import tempfile
from pathlib import Path

from .. import sh
from ..coordination import worker_env
from .git_probe import ProbeParent, remove_probe
from .tools import git_environment, git_readiness


def occupied_inputs(target, base, env):
    """Prove a file-changing FF cannot change Git's configuration dependencies."""
    def refuse():
        raise sh.DriverError(
            "Git capability readiness: cannot prove occupied worktree configuration independent; "
            "branch and files preserved. Preserve your edits and manually sync this tree, then retry, "
            "or recreate it from the selected branch.")

    env = git_environment(env)
    def query(*args, missing=False):
        result = sh.run(sh.argv_for("git", *args), cwd=target, env=env, timeout=30)
        # Never include configuration output or diagnostics: they may contain credentials.
        if result.err or result.code not in ((0, 1) if missing else (0,)):
            refuse()
        return result

    # Git evaluates worktreeConfig/core.worktree itself. Even an unchanged tip
    # must prove the occupied root before readiness admits setup or a fast-forward.
    roots = query("rev-parse", "--path-format=absolute", "--show-toplevel").out.splitlines()
    try:
        if (len(roots) != 1 or not Path(roots[0]).is_absolute()
                or not Path(roots[0]).resolve(strict=True).is_dir()
                or not os.path.samefile(Path(roots[0]).resolve(strict=True), target.resolve(strict=True))):
            refuse()
    except (OSError, RuntimeError, ValueError):
        refuse()

    if base == "HEAD":
        return
    trees = query("rev-parse", "HEAD^{tree}", base + "^{tree}").out.splitlines()
    if len(trees) != 2:
        refuse()
    if trees[0] == trees[1]:
        return  # Tree identities include gitlinks regardless of diff configuration.

    def outside(value):
        # Resolve one prefix at a time: an external alias can traverse a tracked
        # link before ending outside again. Never collapse ".." before link lookup.
        import stat
        try:
            path = Path(value)
            tree = target.resolve(strict=True)
            tree_info = tree.stat()
            if not value or not path.is_absolute():
                refuse()
            def anchor(path):
                root = Path(path.anchor)
                info = root.lstat()
                if (not stat.S_ISDIR(info.st_mode) or os.path.samestat(info, tree_info)
                        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        or getattr(info, "st_reparse_tag", 0)):
                    refuse()  # A reparsed filesystem root has no proved parent semantics.
                return root
            current = anchor(path)
            pending = [(part, False) for part in path.parts[1:]]
            hops = steps = 0
            while pending:
                steps += 1
                if steps > 4096:
                    refuse()
                part, required = pending.pop(0)
                current = current.parent if part == ".." else current / part
                if current.is_relative_to(tree):
                    refuse()
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    if required:
                        refuse()  # Broken link resolution is not evidence.
                    continue  # Absent implicit files are safe under proven prefixes.
                if stat.S_ISDIR(info.st_mode) and os.path.samestat(info, tree_info):
                    refuse()  # Also recognize Windows short-name/case aliases of the tree.
                tag = getattr(info, "st_reparse_tag", 0)
                reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                if (reparse or tag) and tag not in (0xA000000C, 0xA0000003):
                    refuse()  # Only symbolic links and directory junctions are understood.
                if stat.S_ISLNK(info.st_mode) or reparse or tag:
                    hops += 1
                    if hops > 40:
                        refuse()
                    destination = os.readlink(current)
                    if os.name == "nt" and destination.startswith("\\\\?\\"):
                        destination = destination[4:]
                        if destination.startswith("UNC\\"):
                            destination = "\\\\" + destination[4:]
                        elif not (len(destination) >= 3 and destination[1:3] == ":\\"):
                            refuse()  # Device/volume namespace semantics are unsupported.
                    link = Path(destination)
                    if (link.drive or link.root) and not link.is_absolute():
                        refuse()
                    if link.is_absolute():
                        current = anchor(link)
                        pending = [(part, True) for part in link.parts[1:]] + pending
                    else:
                        current = current.parent
                        pending = [(part, True) for part in link.parts] + pending
                elif pending and not stat.S_ISDIR(info.st_mode):
                    refuse()
        except (OSError, RuntimeError, ValueError):
            refuse()

    # Implicit (possibly absent) global config and excludes must also stay outside.
    # Check the source directories themselves, covering Git's platform defaults.
    environment = {**os.environ, **env}
    if environment.get("GIT_CONFIG") is not None:
        refuse()  # This redirects config queries, unlike the Git reads being proved.
    for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "GIT_CONFIG",
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        value = environment.get(key)
        if value is not None:
            outside(value)
    if environment.get("HOMEDRIVE") or environment.get("HOMEPATH"):
        outside((environment.get("HOMEDRIVE") or "") + (environment.get("HOMEPATH") or ""))
    outside(str(Path.home()))
    # Ask Git for its platform config locations, including absent implicit files.
    # Explicit scope reads report the missing filename even on Git before "var
    # GIT_CONFIG_SYSTEM". Accept only that exact C-locale missing-file diagnostic.
    import re
    for scope in ("--system", "--global"):
        result = sh.run(sh.argv_for("git", "config", scope, "--no-includes", "--null",
                                   "--show-origin", "--name-only", "--list"),
                        cwd=target, env=env, timeout=30)
        if result.code:
            absent = re.fullmatch(r"fatal: unable to read config file '([^'\n]+)': No such file or directory\n?",
                                  result.err)
            if not absent:
                refuse()
            outside(absent[1])
        else:
            if result.err:
                refuse()
            entries = result.out.split("\0")
            if entries.pop() != "" or len(entries) % 2:
                refuse()
            if not entries and scope == "--system" and not environment.get("GIT_CONFIG_SYSTEM"):
                refuse()  # An empty implicit system file has no origin to establish its location.
            for origin in entries[::2]:
                if not origin.startswith("file:"):
                    refuse()
                outside(origin[5:])
    home = environment.get("HOME") or str(Path.home())
    xdg = environment.get("XDG_CONFIG_HOME") or str(Path(home) / ".config")
    for value in (Path(home) / ".gitconfig", Path(xdg) / "git/config", Path(xdg) / "git/ignore"):
        outside(str(value))

    # Absent/empty per-repository inputs need proof too, including symlink targets.
    gitdir = query("rev-parse", "--absolute-git-dir").out.rstrip("\n")
    outside(gitdir)
    outside(str(Path(gitdir) / "config.worktree"))
    for name in ("config", "config.worktree", "info/exclude"):
        outside(query("rev-parse", "--path-format=absolute", "--git-path", name).out.rstrip("\n"))

    # Names only: enumerate origins without reading unrelated configuration values.
    origins = query("config", "--includes", "--null", "--show-origin", "--name-only", "--list").out.split("\0")
    if origins.pop() != "" or len(origins) % 2:
        refuse()
    for origin in origins[::2]:
        if origin.startswith("file:"):
            outside(origin[5:])
        elif origin != "command line:":
            refuse()

    # Git expands typed paths and evaluates includeIf, including nested includes.
    # Include directives remain visible even for absent targets/inactive conditions;
    # conservatively require those paths to be absolute and outside as well.
    paths = query("config", "--includes", "--null", "--show-origin", "--type=path",
                  "--get-regexp", r"^(include(if\..*)?\.path|core\.excludesfile)$", missing=True).out
    if not paths:
        return
    fields = paths.split("\0")
    if fields.pop() != "" or len(fields) % 2:
        refuse()
    for entry in fields[1::2]:
        key, separator, value = entry.partition("\n")
        if not separator or entry.count("\n") != 1:
            refuse()
        outside(value)


def worktree_readiness(settings, main, target, branch, base, *, scratch=None):
    """Let Git evaluate onbranch/gitdir/nested includes at the actual target path."""
    main, target = Path(main).resolve(), Path(target).absolute()
    owned_detached = (branch is None and scratch is not None and Path(scratch).is_dir()
                      and target.parent.resolve() == Path(scratch).resolve()
                      and target.resolve().parent == Path(scratch).resolve())
    if not target.resolve().is_relative_to(main) and not owned_detached:
        raise sh.DriverError(f"Git capability readiness: target leaves repository: {target}")
    env = worker_env(settings)
    if (target / ".git").exists():
        occupied_inputs(target, base, env)
        git_readiness(target, env=env)
        return
    if target.exists() or target.is_symlink():
        raise sh.DriverError(f"Git capability readiness: preserving existing target: {target}")

    parent_identity = None
    def git(*args, cwd=main):
        if parent_identity is not None:
            parent_identity.check()
        with tempfile.TemporaryDirectory(prefix="ouro-no-hooks-") as hooks:
            result = sh.run(sh.argv_for("git", "-c", "core.hooksPath=" + hooks, *map(str, args)),
                            cwd=cwd, env=env, timeout=30)
        if parent_identity is not None:
            parent_identity.check()
        if result.code != 0:
            raise sh.DriverError(f"Git capability readiness: disposable probe failed: {result.text}")
        return result

    if branch is not None:
        git("check-ref-format", "refs/heads/" + branch)
    commit = git("rev-parse", "--verify", str(base) + "^{commit}").out.strip()
    # Own the disposable checkout exclusively; no branch ref, setup or hook is admitted.
    parents = []
    parent = target.parent
    while not parent.exists():
        parents.append(parent)
        parent = parent.parent
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").out.strip()).resolve(strict=True)
    common_identity = common.stat()
    registrations = common / "worktrees"
    if registrations.is_symlink() or (registrations.exists() and
            (getattr(registrations.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
             or getattr(registrations.lstat(), "st_reparse_tag", 0))):
        raise sh.DriverError("Git capability readiness: aliased worktree registration directory")
    previous = set(registrations.iterdir()) if registrations.exists() else set()
    target.parent.mkdir(parents=True, exist_ok=True)
    parent_identity = ProbeParent(target.parent)
    owned_target = parent_identity.resolved / target.name
    if not owned_target.is_relative_to(main) and not (owned_detached and owned_target.parent == Path(scratch).resolve()):
        raise sh.DriverError(f"Git capability readiness: target leaves repository: {target}")
    owned_target.mkdir()
    target_identity = owned_target.stat()
    created, marker_identity = {}, None
    try:
        try:
            git("worktree", "add", "--detach", "--no-checkout", target, base)
        finally:
            # Capture identities even if add fails after writing its marker.
            created = {p: p.lstat() for p in registrations.iterdir() if p not in previous} if registrations.exists() else {}
            if (owned_target / ".git").exists() or (owned_target / ".git").is_symlink():
                marker_identity = (owned_target / ".git").lstat()
        if branch is not None:
            git("symbolic-ref", "HEAD", "refs/heads/" + branch, cwd=target)
        # Git materializes tracked include dependencies with normal checkout/filter semantics.
        # Resolve base before symbolic HEAD selection, which may name a nonexistent branch.
        git("read-tree", "--reset", "-u", commit, cwd=target)
        with tempfile.NamedTemporaryFile(prefix="ouro-readiness-sentinel-", dir=target):
            git_readiness(target, env=env)
    finally:
        # The checkout can poison includes even for Git invoked from main. Teardown
        # therefore uses filesystem identities, never a config-dependent Git command.
        import sys
        primary = sys.exception()
        try:
            remove_probe(target, target_identity, parent_identity, common, common_identity, previous, created, marker_identity)
            for parent in parents:
                parent.rmdir()
        except (OSError, RuntimeError, ValueError, sh.DriverError) as cleanup:
            if primary is not None:
                raise sh.DriverError(f"{primary}; disposable probe cleanup failed: {cleanup}") from primary
            raise sh.DriverError(f"Git capability readiness: disposable probe cleanup failed: {cleanup}") from cleanup

