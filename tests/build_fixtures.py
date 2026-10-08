"""Regenerate only the three explicitly named, public synthetic fixtures."""

from pathlib import Path

from .support import make_package


def main() -> None:
    destination = Path(__file__).parent / "fixtures"
    for name, failed in (("valid", False), ("tampered", False), ("failed-experiment", True)):
        root = destination / name
        make_package(root, failed=failed)
        if name == "tampered":
            (root / "artifacts/result.txt").write_bytes(b"11\n")


if __name__ == "__main__":
    main()
