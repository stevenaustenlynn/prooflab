"""Newly authored public summation evidence; no experiment code is run."""

from pathlib import Path

from prooflab.integrity import build_manifest
from prooflab.protocol import AcceptanceState, ExecutionState, MeasurementState
from prooflab.records import (ArtifactRecord, CaseRecord, MeasurementRecord,
                              ProtocolCase, ProtocolRecord, RunRecord, as_record, canonical_json)

ROLES = {"protocol.json": "protocol", "run.json": "run",
         "cases/small-1.json": "case", "artifacts/result.txt": "artifact"}


def write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def seal(root: Path, roles: dict[str, str] | None = None) -> None:
    manifest, sidecar = build_manifest(root, ROLES if roles is None else roles)
    (root / "manifest.json").write_bytes(manifest)
    (root / "manifest.sha256").write_bytes(sidecar)


def make_package(root: Path, failed: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    acceptance = AcceptanceState.FAIL if failed else AcceptanceState.PASS
    protocol = ProtocolRecord("sum-demo", ["iterative", "formula", "incorrect"],
                              [ProtocolCase("small", 1)])
    run = RunRecord("run-demo", "sum-demo", "incorrect" if failed else "iterative",
                    ExecutionState.COMPLETED, acceptance, ["cases/small-1.json"],
                    [ArtifactRecord("answer", "artifacts/result.txt")])
    case = CaseRecord("small-trial-1", "run-demo", "small", 1,
                      ExecutionState.COMPLETED, acceptance,
                      [MeasurementRecord("sum", MeasurementState.OBSERVED, 9 if failed else 10)],
                      ["answer"])
    for relative, record in (("protocol.json", protocol), ("run.json", run),
                             ("cases/small-1.json", case)):
        write_json(root, relative, as_record(record))
    (root / "artifacts").mkdir(exist_ok=True)
    (root / "artifacts/result.txt").write_bytes(b"9\n" if failed else b"10\n")
    seal(root)
