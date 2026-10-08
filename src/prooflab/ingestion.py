"""Strict TOML ingestion into the existing public protocol record."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib

from .artifacts import PathError, check_aliases
from .integrity import sha256_bytes
from .materials import MaterialIdentity, checked_root, read_identity
from .records import (CaseBinding, ExecutionDefinition, MaterialDeclaration,
                      ProtocolCase, ProtocolRecord, VariantCommand, as_record, canonical_json,
                      OutputDeclaration, MeasurementDefinition, ExtractionParameters)
from .schema import SchemaError, validate_record


class ProtocolError(SchemaError):
    pass


def _unique(values: list[str], where: str) -> None:
    seen = set()
    for value in values:
        if value in seen:
            raise ProtocolError(f"{where}: duplicate semantic ID {value!r}")
        seen.add(value)


def _arguments(argv: list[str], where: str, *, command: bool = False) -> None:
    for i, arg in enumerate(argv):
        # Arguments are literal, never shell syntax or interpolated templates.
        if any(ord(c) < 32 or ord(c) == 127 for c in arg):
            raise ProtocolError(f"{where}[{i}]: control characters are forbidden")
        # Portable arguments must not embed host absolute paths, including --x=/path.
        if re.search(r"(^|[=\s])(?:[/~]|[A-Za-z]:|\\)", arg):
            raise ProtocolError(f"{where}[{i}]: host absolute/home paths are forbidden")
        if "\\" in arg or ".." in arg.split("/"):
            raise ProtocolError(f"{where}[{i}]: backslashes/parent traversal are forbidden")
    if command and not argv[0].strip():
        raise ProtocolError(f"{where}[0]: executable must be nonempty")


def validate_execution(value: dict) -> None:
    """Semantic checks after closed structural validation; no filesystem access."""
    execution = value["execution"]
    _unique([c["id"] for c in value["cases"]], "protocol.cases")
    materials = execution["materials"]
    commands = execution["commands"]
    bindings = execution["bindings"]
    _unique([m["id"] for m in materials], "protocol.execution.materials")
    _unique([c["variant"] for c in commands], "protocol.execution.commands")
    _unique([b["case_id"] for b in bindings], "protocol.execution.bindings")
    try:
        check_aliases([m["path"] for m in materials])
    except PathError as exc:
        raise ProtocolError(f"protocol.execution.materials: {exc}") from None
    by_id = {m["id"]: m for m in materials}
    for i, material in enumerate(materials):
        if not material["capture"] and not material["reason"].strip():
            raise ProtocolError(f"protocol.execution.materials[{i}].reason: excluded material needs a reason")
        if material["capture"] and material["reason"]:
            raise ProtocolError(f"protocol.execution.materials[{i}].reason: only excluded materials have a reason")
        _arguments([material["reason"]], f"protocol.execution.materials[{i}].reason")
    if set(c["variant"] for c in commands) != set(value["variants"]):
        raise ProtocolError("protocol.execution.commands: exactly one command per declared variant is required")
    if set(b["case_id"] for b in bindings) != set(c["id"] for c in value["cases"]):
        raise ProtocolError("protocol.execution.bindings: exactly one binding per declared case is required")
    for key, entries, ref, role in (("commands", commands, "source_ids", "source"),
                                    ("bindings", bindings, "input_ids", "input")):
        for i, entry in enumerate(entries):
            where = f"protocol.execution.{key}[{i}]"
            _arguments(entry["argv" if key == "commands" else "args"], where, command=key == "commands")
            for material_id in entry[ref]:
                material = by_id.get(material_id)
                if material is None or material["role"] != role or not material["capture"]:
                    raise ProtocolError(f"{where}.{ref}: {material_id!r} must reference a captured {role} material")


def parse_protocol(data: bytes) -> ProtocolRecord:
    try:
        value = tomllib.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ProtocolError(f"protocol TOML: {exc}") from None
    # Defaults apply only to absent keys, never wrong types or explicit questionable values.
    if type(value.get("cases")) is list:
        for case in value["cases"]:
            if type(case) is dict:
                case.setdefault("trials", 1)
    execution = value.get("execution")
    for key, defaults in (("outputs", {"required": True}),
                          ("measurements", {"version": 1, "unit": "", "parameters": {"path": []}})):
        if type(value.get(key)) is list:
            for entry in value[key]:
                if type(entry) is dict:
                    for name, default in defaults.items():
                        entry.setdefault(name, default)
                    if key == "measurements" and type(entry.get("parameters")) is dict:
                        entry["parameters"].setdefault("path", [])
    if type(execution) is dict:
        for key, defaults in (("materials", {"capture": True, "reason": ""}),
                              ("bindings", {"args": [], "input_ids": [], "seed": None})):
            if type(execution.get(key)) is list:
                for entry in execution[key]:
                    if type(entry) is dict:
                        for name, default in defaults.items():
                            entry.setdefault(name, default)
    validate_record(value, "protocol")
    if "execution" not in value:
        raise ProtocolError("protocol.execution: required for TOML ingestion (legacy accounting records are not executable definitions)")
    return ProtocolRecord(
        value["id"], tuple(sorted(value["variants"])),
        tuple(ProtocolCase(**case) for case in value["cases"]),
        execution=ExecutionDefinition(
            tuple(VariantCommand(c["variant"], tuple(c["argv"]), tuple(sorted(c["source_ids"])))
                  for c in sorted(execution["commands"], key=lambda c: c["variant"])),
            tuple(MaterialDeclaration(**m) for m in sorted(execution["materials"], key=lambda m: m["path"])),
            tuple(CaseBinding(b["case_id"], tuple(b["args"]), tuple(sorted(b["input_ids"])), b["seed"])
                  for b in sorted(execution["bindings"], key=lambda b: b["case_id"])),
            execution.get("timeout_seconds"),
        ),
        outputs=None if "outputs" not in value else tuple(
            OutputDeclaration(**o) for o in sorted(value["outputs"], key=lambda o: o["id"])),
        measurements=None if "measurements" not in value else tuple(
            MeasurementDefinition(**{k: v for k, v in m.items() if k != "parameters"},
                                  parameters=ExtractionParameters(tuple(m["parameters"]["path"])))
            for m in sorted(value["measurements"], key=lambda m: m["id"])),
    )


@dataclass(frozen=True)
class LoadedProtocol:
    protocol: ProtocolRecord
    definition: MaterialIdentity
    # Local binding only. Never serialize this wrapper as a portable record.
    project_root: Path = field(repr=False, compare=False)

    @property
    def normalized_bytes(self) -> bytes:
        return canonical_json(as_record(self.protocol))

    @property
    def protocol_sha256(self) -> str:
        return sha256_bytes(self.normalized_bytes)


def load_protocol(path: str | Path, *, project_root: str | Path | None = None) -> LoadedProtocol:
    path = Path(path).absolute()
    root = checked_root(path.parent if project_root is None else project_root)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise ProtocolError("protocol path: definition must be within project_root") from None
    size, digest, data = read_identity(root, relative, retain=True)
    return LoadedProtocol(parse_protocol(data), MaterialIdentity("definition", relative, "definition", size, digest), root)
