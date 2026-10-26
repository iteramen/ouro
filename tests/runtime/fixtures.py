"""Disposable public fixtures with ordinary workspace ACL inheritance on Windows."""
import contextlib
import pathlib
import os
import stat
import shutil
import uuid


@contextlib.contextmanager
def public_fixture(prefix):
    base = (pathlib.Path.cwd() / "TestResults").resolve()
    base.mkdir(exist_ok=True)
    root = base / (prefix + uuid.uuid4().hex)
    root.mkdir()  # atomic, new directory; inherits workspace ACLs rather than private-temp ACLs
    try:
        yield root
    finally:
        if root.is_symlink() or not root.resolve().is_relative_to(base):
            raise RuntimeError("fixture cleanup target leaves its owned workspace")
        def writable_retry(function, path, error):
            if os.name != "nt" or not isinstance(error, PermissionError):
                raise error
            os.chmod(path, stat.S_IWRITE)
            function(path)
        shutil.rmtree(root, onexc=writable_retry)
