"""Public toy implementations for independent serial runs."""


def iterative(n: int) -> int:
    total = 0
    for value in range(1, n + 1):
        total += value
    return total


def formula(n: int) -> int:
    return n * (n + 1) // 2


def incorrect(n: int) -> int:
    return formula(n) - 1


if __name__ == "__main__":
    # The runner invokes this interface with literal argv in a fresh workspace.
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("variant", choices=("iterative", "formula", "incorrect"))
    parser.add_argument("input_file", type=Path)
    args = parser.parse_args()
    n = int(args.input_file.read_text(encoding="utf-8"))
    answer = {"iterative": iterative, "formula": formula, "incorrect": incorrect}[args.variant](n)
    Path("out").mkdir()
    Path("out/result.json").write_bytes((json.dumps({"answer": answer}, sort_keys=True) + "\n").encode("utf-8"))
    print(answer)
