"""Typed v1 records and deliberately limited JSON encoding/decoding."""

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
import json
import math
from typing import Any

from .protocol import AcceptanceState, ExecutionState, MeasurementState


class RecordError(ValueError):
    """Malformed or nonportable JSON record."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecordError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise RecordError(f"nonfinite JSON number: {value}")


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RecordError(f"nonfinite JSON number: {value}")
    return result


def _check(value: Any) -> None:
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        value.encode("utf-8", errors="strict")
    elif type(value) is float:
        if not math.isfinite(value):
            raise RecordError("nonfinite JSON number")
    elif type(value) is list:
        for item in value:
            _check(item)
    elif type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise RecordError("JSON object keys must be strings")
            _check(key)
            _check(item)
    else:
        raise RecordError(f"unsupported JSON value type: {type(value).__name__}")


def strict_loads(data: bytes) -> Any:
    try:
        value = json.loads(data.decode("utf-8", errors="strict"),
                           object_pairs_hook=_pairs, parse_constant=_constant,
                           parse_float=_float)
        _check(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RecordError(str(exc)) from exc


def canonical_json(value: Any) -> bytes:
    """Sorted keys, two-space indentation, literal UTF-8, one trailing LF."""
    try:
        _check(value)
        return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, indent=2) + "\n").encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RecordError(str(exc)) from exc


@dataclass(frozen=True)
class ProtocolCase:
    id: str
    trials: int


@dataclass(frozen=True)
class ProtocolRecord:
    id: str
    variants: list[str] | tuple[str, ...]
    cases: list[ProtocolCase] | tuple[ProtocolCase, ...]
    schema: str = "prooflab.protocol/v1"
    execution: "ExecutionDefinition | None" = None
    outputs: "tuple[OutputDeclaration, ...] | None" = None
    measurements: "tuple[MeasurementDefinition, ...] | None" = None


@dataclass(frozen=True)
class OutputDeclaration:
    id: str
    path: str
    required: bool = True


@dataclass(frozen=True)
class ExtractionParameters:
    path: tuple[str | int, ...] = ()


@dataclass(frozen=True)
class MeasurementDefinition:
    id: str
    source: str
    extractor: str
    type: str
    version: int = 1
    unit: str = ""
    parameters: ExtractionParameters = field(default_factory=ExtractionParameters)


@dataclass(frozen=True)
class MaterialDeclaration:
    id: str
    path: str
    role: str
    capture: bool = True
    reason: str = ""


@dataclass(frozen=True)
class VariantCommand:
    variant: str
    argv: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaseBinding:
    case_id: str
    args: tuple[str, ...] = ()
    input_ids: tuple[str, ...] = ()
    seed: int | None = None


@dataclass(frozen=True)
class ExecutionDefinition:
    commands: tuple[VariantCommand, ...]
    materials: tuple[MaterialDeclaration, ...]
    bindings: tuple[CaseBinding, ...]
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    path: str


@dataclass(frozen=True)
class RunRecord:
    id: str
    protocol_id: str
    variant: str
    execution_state: ExecutionState
    acceptance_state: AcceptanceState
    case_records: list[str]
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    schema: str = "prooflab.run/v1"


@dataclass(frozen=True)
class MeasurementRecord:
    id: str
    state: MeasurementState
    value: int | float | None = None


@dataclass(frozen=True)
class CaseRecord:
    id: str
    run_id: str
    case_id: str
    trial: int
    execution_state: ExecutionState
    acceptance_state: AcceptanceState
    measurements: list[MeasurementRecord] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    schema: str = "prooflab.case/v1"


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    role: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ManifestRecord:
    files: list[ManifestEntry]
    schema: str = "prooflab.manifest/v1"


def as_record(record: Any) -> dict[str, Any]:
    """Explicit dataclass conversion; canonical_json itself accepts only JSON types."""
    if not is_dataclass(record) or isinstance(record, type):
        raise TypeError("expected a record dataclass instance")

    def convert(value: Any) -> Any:
        if isinstance(value, StrEnum):
            return value.value
        if type(value) is dict:
            return {key: convert(item) for key, item in value.items()}
        if type(value) in (list, tuple):
            return [convert(item) for item in value]
        return value

    result = convert(asdict(record))
    if isinstance(record, ProtocolRecord):
        for key in ("outputs", "measurements"):
            if result[key] is None:
                del result[key]
    if isinstance(record, ProtocolRecord) and result["execution"] is None:
        del result["execution"]
    elif isinstance(record, ProtocolRecord) and result["execution"]["timeout_seconds"] is None:
        del result["execution"]["timeout_seconds"]
    _check(result)
    return result
