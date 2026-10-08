"""Optional, bounded environment/Git facts, separate from deterministic identity."""

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import subprocess

from . import __version__


@dataclass(frozen=True)
class Provenance:
    prooflab_version: str
    python_version: str
    system: str
    architecture: str
    inventory_sha256: str
    git_state: str
    git_commit: str | None
    git_dirty: bool | None
    git_status_entries: int | None


def capture_provenance(root: str | Path, inventory_sha256: str) -> Provenance:
    """Read-only Git commands only; never retain filenames, diffs, remotes, or errors.

    Absence/failure is explicit. No subprocess is used by ingestion or planning.
    """
    if type(inventory_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", inventory_sha256) is None:
        raise ValueError("inventory_sha256: expected 64 lowercase hexadecimal digits")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args):
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
             "-C", str(root), *args], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
        return result.stdout if result.returncode == 0 else None

    state, commit, dirty, count = "unavailable", None, None, None
    try:
        if git("rev-parse", "--is-inside-work-tree") == b"true\n":
            raw_commit = git("rev-parse", "--verify", "HEAD")
            if raw_commit is not None:
                candidate = raw_commit.decode("ascii").strip()
                if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", candidate):
                    commit = candidate
            attached = git("symbolic-ref", "-q", "HEAD") is not None
            state = "unborn" if commit is None else ("attached" if attached else "detached")
            status = git("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=all")
            if status is not None:
                # Renames/copies have a second NUL-delimited path: count records, not paths.
                entries = iter(status.split(b"\0")[:-1])
                count = 0
                for entry in entries:
                    count += 1
                    if b"R" in entry[:2] or b"C" in entry[:2]:
                        next(entries, None)
                dirty = count != 0
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        state, commit, dirty, count = "unavailable", None, None, None
    return Provenance(__version__, platform.python_version(), platform.system(),
                      platform.machine(), inventory_sha256, state, commit, dirty, count)
