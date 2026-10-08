"""Create only the explicit packaged starter, without overwriting user content."""

from contextlib import ExitStack
from importlib.resources import files
import os
from pathlib import Path
import stat

from .artifacts import check_aliases


# This flat list is both the public write set and the resource allowlist.
TEMPLATE_FILES = (
    "README.md", "comparison-policy.toml", "experiment.toml",
    "implementations.py", "small.txt",
)


class InitializationError(ValueError):
    """All init failures use exit 2; partial creations are explicitly identified."""


def template_bytes() -> dict[str, bytes]:
    check_aliases(list(TEMPLATE_FILES))
    if any("/" in name for name in TEMPLATE_FILES):
        raise InitializationError("starter resources must have flat file names")
    resource = files("prooflab").joinpath("starter")
    return {name: resource.joinpath(name).read_bytes() for name in TEMPLATE_FILES}


def _aliases(fd: int, name: str) -> list[str]:
    return sorted(entry for entry in os.listdir(fd) if entry.casefold() == name.casefold())


def _open_directory(path: Path, stack: ExitStack) -> int:
    """Walk from the filesystem root using pinned, no-follow directory handles."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    stack.callback(os.close, fd)
    for component in path.parts[1:]:
        if _aliases(fd, component) != [component]:
            raise InitializationError(f"missing or case-ambiguous directory: {str(path)!r}")
        fd = os.open(component, flags, dir_fd=fd)
        stack.callback(os.close, fd)
    return fd


def _identity(info):
    return info.st_dev, info.st_ino


def _check_binding(root: Path, fd: int) -> None:
    with ExitStack() as stack:
        current = _open_directory(root, stack)
        if _identity(os.fstat(current)) != _identity(os.fstat(fd)):
            raise InitializationError("destination directory changed during initialization")


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            raise OSError("short write")
        remaining = remaining[count:]


def initialize(directory: str | Path = ".") -> Path:
    """Preflight every name, then exclusively create regular files.

    No rollback: exclusive creation proves original ownership, but cannot prove
    continued ownership after another writer intervenes. Retain and enumerate
    partial creations instead of attempting a racy check-then-unlink cleanup.
    Directory handles prevent a symlink substitution from redirecting writes.
    This is not a filesystem transaction; callers must keep the tree quiescent.
    """
    created: list[str] = []
    made_root = False
    try:
        root = Path(directory).absolute()
        if ".." in root.parts:
            raise InitializationError("destination must not contain parent traversal")
        if os.name != "posix" or not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise InitializationError("safe no-follow directory handles are unavailable on this platform")
        payloads = template_bytes()  # Load the complete resource set before mutation.
        with ExitStack() as stack:
            parent = _open_directory(root.parent, stack)
            matches = _aliases(parent, root.name) if root.name else []
            if root == root.parent:
                fd = _open_directory(root, stack)
            elif matches:
                if matches != [root.name]:
                    raise InitializationError("case-ambiguous destination directory")
                fd = _open_directory(root, stack)
            else:
                fd = None
            # Full preflight before even creating a new root.
            if fd is not None:
                for name in payloads:
                    if _aliases(fd, name):
                        raise InitializationError(f"template destination conflicts: {name!r}")
            if fd is None:
                os.mkdir(root.name, dir_fd=parent)
                made_root = True
                fd = _open_directory(root, stack)
            for name, data in payloads.items():
                _check_binding(root, fd)
                if _aliases(fd, name):
                    raise InitializationError(f"template destination appeared after preflight: {name!r}")
                handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o666, dir_fd=fd)
                created.append(name)
                try:
                    _write_all(handle, data)
                finally:
                    os.close(handle)
            _check_binding(root, fd)
            # Refuse success if ordinary concurrent mutation/aliasing was detected.
            for name, data in payloads.items():
                if _aliases(fd, name) != [name]:
                    raise InitializationError(f"template destination changed: {name!r}")
                handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(handle, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(len(data) + 1) != data:
                        raise InitializationError(f"template bytes changed: {name!r}")
        return root
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
        detail = str(exc) if str(exc) else type(exc).__name__
        if made_root or created:
            # repr protects terminal output from control characters in local paths.
            partial = ", ".join(repr(name) for name in created) or "(no files)"
            detail += (f"; PARTIAL initialization at {str(root)!r}; "
                       f"new_directory={made_root}; created file paths retained: {partial}; "
                       "last file may be incomplete; concurrent changes are preserved; "
                       "if the directory was moved, inspect its moved location")
        else:
            detail += "; no template files created"
        raise InitializationError(detail) from None
