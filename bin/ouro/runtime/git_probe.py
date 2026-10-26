"""Identity-checked teardown of an owned Git-readiness probe; no workflow state."""
import os
from pathlib import Path

from .. import sh


def remove_probe(target, identity, parent_identity, common, common_identity, previous, created, marker_identity):
    """Remove only a newly owned checkout and its uniquely mapped new registration."""
    import stat

    def plain(path, directory=False):
        info = path.lstat()
        if (getattr(info, "st_file_attributes", 0) & 0x400
                or getattr(info, "st_reparse_tag", 0)
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
            raise sh.DriverError("unproved probe file identity")
        return info

    def same(path, expected, directory=True):
        info = plain(path, directory)
        if not os.path.samestat(info, expected):
            raise sh.DriverError("probe identity changed")

    same(target.parent, parent_identity)
    same(common, common_identity)
    registrations = common / "worktrees"
    if registrations.exists():
        plain(registrations, True)
    current = set(registrations.iterdir()) if registrations.exists() else set()
    new = current - previous
    if new != set(created):
        raise sh.DriverError("probe registration set changed")
    if not target.exists() and not target.is_symlink() and not new:
        return  # Git add can roll back its own checkout and registration on failure.
    same(target, identity)
    marker = target / ".git"
    registration = None
    if marker.exists() or marker.is_symlink():
        if marker_identity is None:
            raise sh.DriverError("probe marker was not captured")
        same(marker, marker_identity, False)
        text = marker.read_text(encoding="utf-8")
        if not text.startswith("gitdir: ") or len(text.splitlines()) != 1:
            raise sh.DriverError("unproved probe marker")
        registration = Path(text[8:].strip())
        if not registration.is_absolute():
            raise sh.DriverError("nonabsolute probe registration")
        plain(registration, True)
        registration = registration.resolve(strict=True)
        if (registration.parent != registrations or new != {registration}):
            raise sh.DriverError("unproved new probe registration")
        registration_identity = created[registration]
        same(registration, registration_identity)
        for name in ("commondir", "gitdir"):
            plain(registration / name)
        if ((registration / (registration / "commondir").read_text(encoding="utf-8").strip()).resolve(strict=True) != common
                or not os.path.samefile(Path((registration / "gitdir").read_text(encoding="utf-8").strip()), marker)):
            raise sh.DriverError("probe registration mapping changed")
        # Git owns this fixed metadata shape. Unknown state is preserved for inspection.
        allowed = {"HEAD", "ORIG_HEAD", "index", "commondir", "gitdir", "logs", "refs", "config.worktree"}
        if any(p.name not in allowed for p in registration.iterdir()):
            raise sh.DriverError("unknown probe registration state")
        if (registration / "config.worktree").exists():
            plain(registration / "config.worktree")
        if (registration / "logs").exists():
            plain(registration / "logs", True)
            if any(p.name != "HEAD" for p in (registration / "logs").iterdir()):
                raise sh.DriverError("unknown probe registration log")
        if (registration / "refs").exists():
            plain(registration / "refs", True)
            if any((registration / "refs").iterdir()):
                raise sh.DriverError("unknown probe registration refs")
        same(marker, marker_identity, False)
    else:
        if new:
            raise sh.DriverError("new registration without owned probe marker")
        target.rmdir()  # An unregistered tree must still be empty; never recurse into it.
        return

    # Snapshot every identity before deleting anything. Never traverse a link,
    # junction or other reparse point; generated tracked symlinks are unlinked.
    def inventory(root):
        rows = []
        def visit(path):
            info = path.lstat()
            tag = getattr(info, "st_reparse_tag", 0)
            symlink = stat.S_ISLNK(info.st_mode) and tag in (0, getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C))
            if (getattr(info, "st_file_attributes", 0) & 0x400 or tag) and not symlink:
                raise sh.DriverError("reparsed probe contents")
            if stat.S_ISDIR(info.st_mode):
                for child in path.iterdir():
                    visit(child)
            elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise sh.DriverError("unknown probe contents")
            rows.append((path, info))
        visit(root)
        return rows

    rows = inventory(target)
    if registration is not None:
        same(registration, registration_identity)
        rows += inventory(registration)
    for path, info in rows:
        if not os.path.samestat(path.lstat(), info):
            raise sh.DriverError("probe contents changed during teardown")
    for path, info in rows:
        if not os.path.samestat(path.lstat(), info):
            raise sh.DriverError("probe contents changed during teardown")
        path.rmdir() if stat.S_ISDIR(info.st_mode) else path.unlink()
