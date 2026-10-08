"""Portable logical paths and regular-file-only package access."""

import os
from pathlib import Path
import re
import stat


class PathError(ValueError):
    pass


RESERVED = {"manifest.json", "manifest.sha256"}
_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)
}


def logical_path(value: str) -> str:
    """Use a conservative ASCII subset that has one spelling across platforms."""
    if type(value) is not str or not value:
        raise PathError("package path must be a nonempty string")
    for part in value.split("/"):
        if (not part or part in (".", "..") or part.endswith(".")
                or re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None
                or part.split(".")[0].lower() in _DEVICES):
            raise PathError(f"unsafe package path: {value!r}")
    return value


def check_aliases(paths: list[str]) -> None:
    """Reject duplicate files, case aliases (including directories), file parents."""
    files: set[str] = set()
    spellings: dict[str, str] = {}
    directories: set[str] = set()
    for value in paths:
        logical_path(value)
        parts = value.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            folded = prefix.casefold()
            if folded in spellings and spellings[folded] != prefix:
                raise PathError(f"case-insensitive path collision: {prefix!r}")
            spellings[folded] = prefix
            if i < len(parts):
                directories.add(folded)
        folded = value.casefold()
        if folded in files:
            raise PathError(f"duplicate path: {value!r}")
        files.add(folded)
    if files & directories:
        raise PathError("a file path is also used as a directory")


def scan_package(root: Path) -> set[str]:
    """Inspect without following links; reject special files before opening any."""
    if root.is_symlink() or not stat.S_ISDIR(root.lstat().st_mode):
        raise PathError("run directory must be a real directory, not a symlink")
    files: set[str] = set()
    spellings: dict[str, str] = {}

    def visit(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                relative = logical_path(prefix + entry.name)
                folded = relative.casefold()
                if folded in spellings:
                    raise PathError(f"case-insensitive path collision: {relative!r}")
                spellings[folded] = relative
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise PathError(f"symlink forbidden: {relative}")
                if stat.S_ISDIR(mode):
                    visit(Path(entry.path), relative + "/")
                elif stat.S_ISREG(mode):
                    files.add(relative)
                else:
                    raise PathError(f"nonregular file forbidden: {relative}")

    visit(root, "")
    return files


def open_payload(root: Path, relative: str):
    """Open checked bytes only; no imports, deserialization hooks, or evaluation."""
    logical_path(relative)
    target = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise PathError(f"symlink forbidden: {relative}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise PathError(f"path escapes package: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(target, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PathError(f"nonregular file forbidden: {relative}")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
