"""SHA-256 byte inventory and deterministic manifest construction."""

import hashlib
from pathlib import Path

from .artifacts import RESERVED, PathError, check_aliases, open_payload, scan_package
from .records import ManifestEntry, ManifestRecord, as_record, canonical_json
from .schema import validate_record


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_identity(root: Path, relative: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open_payload(root, relative) as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def build_manifest(root: Path, roles: dict[str, str], *, package_type: str | None = None) -> tuple[bytes, bytes]:
    """Return manifest and sidecar bytes; caller chooses when/where to write."""
    check_aliases(list(roles) + sorted(RESERVED))
    actual = scan_package(root) - RESERVED
    if actual != set(roles):
        raise PathError(f"inventory mismatch: unlisted={sorted(actual - roles.keys())}, "
                        f"missing={sorted(roles.keys() - actual)}")
    entries = []
    for relative, role in sorted(roles.items()):
        size, digest = file_identity(root, relative)
        entries.append(ManifestEntry(relative, role, size, digest))
    record = as_record(ManifestRecord(entries))
    if package_type is not None:
        record["package_type"] = package_type
    validate_record(record, "manifest")
    data = canonical_json(record)
    return data, (sha256_bytes(data) + "\n").encode("ascii")
