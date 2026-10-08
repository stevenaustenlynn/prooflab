"""Immutable future-run data. This module cannot execute an experiment."""

from dataclasses import dataclass

from .artifacts import PathError, check_aliases
from .ingestion import LoadedProtocol, ProtocolError
from .integrity import sha256_bytes
from .materials import (MaterialIdentity, capture_materials, compare_materials,
                        inventory_sha256)
from .records import as_record, canonical_json, strict_loads
from .schema import validate_record


@dataclass(frozen=True)
class PlannedTrial:
    id: str
    case_id: str
    trial: int
    argv: tuple[str, ...]
    input_ids: tuple[str, ...]
    seed: int | None


@dataclass(frozen=True)
class ExecutionPlan:
    protocol_bytes: bytes
    protocol_sha256: str
    definition: MaterialIdentity
    variant: str
    source_ids: tuple[str, ...]
    materials: tuple[MaterialIdentity, ...]
    trials: tuple[PlannedTrial, ...]

    @property
    def population_count(self) -> int:
        return len(self.trials)

    @property
    def inventory_sha256(self) -> str:
        return inventory_sha256(self.materials)

    def to_record(self) -> dict:
        """Portable library record; not a run package or a fifth public schema."""
        return {"protocol": strict_loads(self.protocol_bytes),
                "protocol_sha256": self.protocol_sha256,
                "definition": as_record(self.definition), "variant": self.variant,
                "source_ids": list(self.source_ids),
                "materials": [as_record(m) for m in self.materials],
                "inventory_sha256": self.inventory_sha256,
                "trials": [as_record(t) for t in self.trials],
                "population_count": self.population_count}

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json(self.to_record()))


def build_plan(loaded: LoadedProtocol, variant: str | None = None, *,
               max_population: int = 100_000) -> ExecutionPlan:
    protocol = loaded.protocol
    validate_record(as_record(protocol), "protocol")
    if type(max_population) is not int or max_population < 1:
        raise ProtocolError("max_population: expected a positive integer")
    if sum(case.trials for case in protocol.cases) > max_population:
        raise ProtocolError("cases.trials: population exceeds planning limit; explicitly increase max_population")
    if variant is None:
        if len(protocol.variants) != 1:
            raise ProtocolError("variant: explicit selection required when multiple variants exist")
        variant = protocol.variants[0]
    if type(variant) is not str or variant not in protocol.variants:
        raise ProtocolError(f"variant: unknown variant {variant!r}")
    execution = protocol.execution
    if execution is None:
        raise ProtocolError("protocol.execution: required for planning")
    # The loaded definition must still match, even if only its comments changed.
    if compare_materials(loaded.project_root, (loaded.definition,)):
        raise ProtocolError("protocol definition: changed since loading; reload explicitly")
    try:
        check_aliases([loaded.definition.path] + [m.path for m in execution.materials])
    except PathError as exc:
        raise ProtocolError(f"protocol definition/material paths: {exc}") from None
    materials = capture_materials(loaded.project_root, execution.materials)
    command = next(c for c in execution.commands if c.variant == variant)
    bindings = {b.case_id: b for b in execution.bindings}
    trials = []
    for case in protocol.cases:
        binding = bindings[case.id]
        for trial in range(1, case.trials + 1):
            trials.append(PlannedTrial(
                f"{case.id}-trial-{trial:04d}", case.id, trial,
                command.argv + binding.args, binding.input_ids,
                None if binding.seed is None else binding.seed + trial - 1,
            ))
    # Catch ordinary changes while constructing the inventory/plan. No atomic snapshot claim.
    if compare_materials(loaded.project_root, (loaded.definition, *materials)):
        raise ProtocolError("materials: changed during planning; recapture explicitly")
    return ExecutionPlan(loaded.normalized_bytes, loaded.protocol_sha256, loaded.definition,
                         variant, command.source_ids, materials, tuple(trials))
