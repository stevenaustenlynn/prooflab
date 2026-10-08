"""Explicit regular-file identities; no copying, imports, or execution."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from .artifacts import PathError, check_aliases, logical_path, open_payload
from .integrity import sha256_bytes
from .records import MaterialDeclaration, as_record, canonical_json


class MaterialError(ValueError):
    def __init__(self, path: str, kind: str):
        self.path = path
        self.kind = kind
        super().__init__(f"{path}: {kind}")


@dataclass(frozen=True)
class MaterialIdentity:
    id: str
    path: str
    role: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class MaterialChange:
    path: str
    kind: str


def checked_root(root: str | Path) -> Path:
    """Keep the host binding private and reject symlink ancestors before resolution."""
    root = Path(os.path.abspath(root))
    try:
        for parent in (*reversed(root.parents), root):
            mode = parent.lstat().st_mode
            if not stat.S_ISDIR(mode):
                raise MaterialError("project_root", "must be a real directory without symlink ancestors")
    except OSError:
        raise MaterialError("project_root", "unavailable") from None
    return root


def _check_file(root: Path, relative: str) -> Path:
    logical_path(relative)
    current = root
    parts = relative.split("/")
    for i, part in enumerate(parts):
        # Check only siblings of declared components, never traverse unrelated trees.
        with os.scandir(current) as entries:
            matches = sorted(e.name for e in entries if e.name.casefold() == part.casefold())
        if matches != [part]:
            if matches:
                raise MaterialError(relative, "ambiguous_path")
            raise MaterialError(relative, "missing")
        current = current / part
        mode = current.lstat().st_mode
        expected = stat.S_ISREG if i == len(parts) - 1 else stat.S_ISDIR
        if not expected(mode):
            raise MaterialError(relative, "type_change_or_unsupported")
    return current


def read_identity(root: Path, relative: str, *, retain: bool = False) -> tuple[int, str, bytes]:
    """Hash an observed stream; detect ordinary concurrent replacement/modification.

    A quiescent tree is required. This is not an adversarial filesystem sandbox.
    """
    try:
        _check_file(root, relative)
        digest = hashlib.sha256()
        chunks = []
        size = 0
        with open_payload(root, relative) as stream:
            before = os.fstat(stream.fileno())
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                if retain:
                    chunks.append(chunk)
            after = os.fstat(stream.fileno())
        final = _check_file(root, relative).lstat()
        def stamp(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if stamp(before) != stamp(after) or stamp(after) != stamp(final) or size != final.st_size:
            raise MaterialError(relative, "changed_during_capture")
        return size, digest.hexdigest(), b"".join(chunks)
    except FileNotFoundError:
        raise MaterialError(relative, "missing") from None
    except (OSError, PathError):
        raise MaterialError(relative, "unreadable_or_unsafe") from None


def capture_materials(root: str | Path, declarations: tuple[MaterialDeclaration, ...]) -> tuple[MaterialIdentity, ...]:
    root = checked_root(root)
    check_aliases([item.path for item in declarations])
    result = []
    for item in sorted(declarations, key=lambda item: item.path):
        if item.capture:
            size, digest, _ = read_identity(root, item.path)
            result.append(MaterialIdentity(item.id, item.path, item.role, size, digest))
    return tuple(result)


def inventory_sha256(identities: tuple[MaterialIdentity, ...]) -> str:
    return sha256_bytes(canonical_json([as_record(item) for item in identities]))


def compare_materials(root: str | Path, identities: tuple[MaterialIdentity, ...]) -> tuple[MaterialChange, ...]:
    """Compare against the baseline without modifying it or any source file."""
    root = checked_root(root)
    try:
        check_aliases([item.path for item in identities])
    except PathError:
        return (MaterialChange("inventory", "ambiguous_path"),)
    changes = []
    for item in sorted(identities, key=lambda item: item.path):
        try:
            size, digest, _ = read_identity(root, item.path)
        except MaterialError as exc:
            changes.append(MaterialChange(item.path, exc.kind))
            continue
        if size != item.size_bytes:
            changes.append(MaterialChange(item.path, "size_change"))
        if digest != item.sha256:
            changes.append(MaterialChange(item.path, "digest_change"))
    return tuple(changes)
