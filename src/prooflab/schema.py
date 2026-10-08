"""The small closed v1 schema vocabulary, shared with the published JSON Schemas.

This is not a general JSON Schema implementation. Only keywords emitted here
are interpreted; package-provided schemas are never loaded.
"""

import re
import math
from typing import Any

from .protocol import AcceptanceState, ExecutionState, IntegrityState, MeasurementState


class SchemaError(ValueError):
    pass


class UnsupportedSchema(SchemaError):
    pass


def _object(properties: dict, optional: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties,
            "required": [key for key in properties if key not in optional],
            "additionalProperties": False}


def _array(items: dict, minimum: int = 0, unique: bool = False) -> dict:
    return {"type": "array", "items": items, "minItems": minimum,
            "uniqueItems": unique}


def _enum(enum: type) -> dict:
    return {"type": "string", "enum": [item.value for item in enum]}


ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", "minLength": 1}
PATH = {"type": "string", "minLength": 1}
INTEGER = {"type": "integer", "minimum": 1}
TEXT = {"type": "string"}
EXECUTION = _object({
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
    "commands": _array(_object({
        "variant": ID, "argv": _array(TEXT, 1), "source_ids": _array(ID, 1, True),
    }), 1),
    "materials": _array(_object({
        "id": ID, "path": PATH,
        "role": {"type": "string", "enum": ["input", "source", "evaluator"]},
        "capture": {"type": "boolean"}, "reason": TEXT,
    })),
    "bindings": _array(_object({
        "case_id": ID, "args": _array(TEXT), "input_ids": _array(ID, unique=True),
        "seed": {"type": ["integer", "null"], "minimum": 0},
    }), 1),
}, ("timeout_seconds",))

DIGEST = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
OUTPUT = _object({"id": ID, "path": PATH, "required": {"type": "boolean"}})
MEASUREMENT_DEFINITION = _object({
    "id": ID, "source": TEXT,
    "extractor": {"enum": ["sha256", "byte_count", "utf8", "json", "exit_code", "duration_ns"]},
    "version": {"type": "integer", "const": 1},
    "type": {"enum": ["integer", "float", "boolean", "string"]},
    "unit": TEXT,
    "parameters": _object({"path": _array({"type": ["string", "integer"], "minimum": 0})}),
})
OUTPUT_OBSERVATION = _object({
    **OUTPUT["properties"], "state": _enum(MeasurementState),
    "artifact_id": {"type": ["string", "null"], "pattern": ID["pattern"]},
    "size_bytes": {"type": ["integer", "null"], "minimum": 0},
    "sha256": {"type": ["string", "null"], "pattern": DIGEST["pattern"]},
    "reason": {"enum": [None, "required_output_missing", "optional_output_missing",
                          "execution_not_started", "ambiguous_path", "type_change_or_unsupported",
                          "changed_during_capture", "unreadable_or_unsafe"]},
})
MEASUREMENT = {"anyOf": [
    _object({"id": ID, "state": _enum(MeasurementState), "value": {"type": ["number", "null"]}}),
    _object({**MEASUREMENT_DEFINITION["properties"], "state": _enum(MeasurementState),
             "value": {"type": ["number", "boolean", "string", "null"]},
             "source_artifact": {"type": ["string", "null"], "pattern": ID["pattern"]},
             "reason": {"enum": [None, "execution_not_started", "source_unavailable",
                                   "required_output_missing", "optional_output_missing",
                                   "output_capture_error", "invalid_utf8", "invalid_json",
                                   "missing_json_path", "type_mismatch"]}}),
]}
IDENTITIES = {"protocol_sha256": DIGEST, "plan_sha256": DIGEST,
              "inventory_sha256": DIGEST, "snapshot_sha256": DIGEST}
RUN_EXECUTION = _object({
    **IDENTITIES,
    "plan_artifact": ID, "snapshot_artifact": ID,
    "definition_artifact": ID, "provenance_artifact": ID,
    "cwd": {"const": "trial_workspace_root"},
    "environment": {"const": "inherited_with_prooflab_seed"},
    "interrupted": {"type": "boolean"},
})
RECEIPT = _object({
    **IDENTITIES, "variant": ID, "argv": _array(TEXT, 1),
    "seed": {"type": ["integer", "null"], "minimum": 0},
    "cwd": {"const": "trial_workspace_root"},
    "exit_code": {"type": ["integer", "null"]},
    "started_at": {"type": ["string", "null"]},
    "ended_at": {"type": ["string", "null"]},
    "elapsed_ns": {"type": ["integer", "null"], "minimum": 0},
    "stdout_artifact": ID, "stderr_artifact": ID,
    "reason": {"enum": [None, "nonzero_exit", "launch_failed", "timeout",
                          "user_interrupt", "after_interrupt"]},
})


def _record(kind: str, properties: dict, optional: tuple[str, ...] = ()) -> dict:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:prooflab:{kind}:v1",
            **_object({"schema": {"const": f"prooflab.{kind}/v1"}, **properties}, optional)}


SCHEMAS = {
    "protocol": _record("protocol", {
        "id": ID,
        "variants": _array(ID, 1, True),
        "cases": _array(_object({"id": ID, "trials": INTEGER}), 1),
        "execution": EXECUTION,
        "outputs": _array(OUTPUT),
        "measurements": _array(MEASUREMENT_DEFINITION),
    }, ("execution", "outputs", "measurements")),
    "run": _record("run", {
        "id": ID, "protocol_id": ID, "variant": ID,
        "execution_state": _enum(ExecutionState),
        "acceptance_state": _enum(AcceptanceState),
        "case_records": _array(PATH, unique=True),
        "artifacts": _array(_object({"id": ID, "path": PATH})),
        "integrity_state": _enum(IntegrityState),
        "execution": RUN_EXECUTION,
    }, ("integrity_state", "execution")),
    "case": _record("case", {
        "id": ID, "run_id": ID, "case_id": ID, "trial": INTEGER,
        "execution_state": _enum(ExecutionState),
        "acceptance_state": _enum(AcceptanceState),
        "measurements": _array(MEASUREMENT),
        "outputs": _array(OUTPUT_OBSERVATION),
        "artifact_ids": _array(ID, unique=True),
        "execution": RECEIPT,
    }, ("execution", "outputs")),
    "manifest": _record("manifest", {
        "package_type": {"enum": ["run", "comparison"]},
        "files": _array(_object({
            "path": PATH,
            "role": {"enum": ["protocol", "run", "case", "artifact", "comparison"]},
            "size_bytes": {"type": "integer", "minimum": 0},
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        })),
    }, ("package_type",)),
}


COMPARISON_STATES = ["PASS", "FAIL", "INCONCLUSIVE", "NOT_APPLICABLE"]
RULE = _object({
    "id": ID, "measurement": ID,
    "operator": {"enum": ["equal", "candidate_gte_baseline", "candidate_lte_baseline",
                          "delta_gte", "delta_lte", "abs_delta_lte"]},
    "required": {"type": "boolean"}, "description": TEXT,
    "threshold": {"type": "number"},
}, ("threshold",))
OPERAND = _object({
    "state": {"enum": ["observed", "error", "not_evaluated", "not_applicable", "missing"]},
    "value": {"type": ["number", "string", "boolean", "null"]},
    "reason": {"type": ["string", "null"]},
})
SOURCE_IDENTITY = _object({"run_id": ID, "manifest_sha256": DIGEST})
SCHEMAS["comparison-policy"] = _record("comparison-policy", {
    "id": ID, "description": TEXT, "rules": _array(RULE, 1),
})
SCHEMAS["comparison"] = _record("comparison", {
    "id": ID, "created_at": TEXT,
    "baseline": SOURCE_IDENTITY, "candidate": SOURCE_IDENTITY,
    "policy": _object({"id": ID, "sha256": DIGEST, "normalized_sha256": DIGEST}),
    "compatibility": _object({
        "state": {"enum": ["COMPATIBLE", "INCOMPATIBLE"]},
        "checks": _array(_object({"id": ID, "passed": {"type": "boolean"}}), 1),
    }),
    "paired_trials": {"type": "integer", "minimum": 0},
    "pairs": _array(_object({
        "rule_id": ID, "case_id": ID, "trial": INTEGER,
        "seed": {"type": ["integer", "null"], "minimum": 0},
        "baseline": OPERAND, "candidate": OPERAND,
        "state": {"enum": COMPARISON_STATES},
        "reason": {"enum": ["evaluated", "operand_unavailable", "both_not_applicable", "nonfinite_delta"]},
    })),
    "rules": _array(_object({
        "rule_id": ID, "required": {"type": "boolean"}, "state": {"enum": COMPARISON_STATES},
        "counts": _object({s: {"type": "integer", "minimum": 0} for s in COMPARISON_STATES}),
    })),
    "verdict": {"enum": ["PASS", "FAIL", "INCONCLUSIVE", "INCOMPATIBLE"]},
})


def _validate(value: Any, spec: dict, location: str) -> None:
    if "anyOf" in spec:
        for alternative in spec["anyOf"]:
            try:
                _validate(value, alternative, location)
                return
            except SchemaError:
                pass
        raise SchemaError(f"{location}: no supported record shape matches")
    if type(value) is float and not math.isfinite(value):
        raise SchemaError(f"{location}: nonfinite number")
    kinds = {"object": lambda x: type(x) is dict,
             "array": lambda x: type(x) is list,
             "string": lambda x: type(x) is str,
             "integer": lambda x: type(x) is int,
             "boolean": lambda x: type(x) is bool,
             "number": lambda x: type(x) in (int, float),
             "null": lambda x: x is None}
    expected = spec.get("type")
    if expected is not None:
        expected = expected if isinstance(expected, list) else [expected]
        if not any(kinds[kind](value) for kind in expected):
            raise SchemaError(f"{location}: expected {' or '.join(expected)}")
    if "const" in spec and value != spec["const"]:
        raise SchemaError(f"{location}: expected {spec['const']!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise SchemaError(f"{location}: invalid enum value {value!r}")
    if type(value) is dict:
        missing = set(spec["required"]) - value.keys()
        extra = value.keys() - spec["properties"].keys()
        if missing or extra:
            raise SchemaError(f"{location}: missing fields {sorted(missing)}, unknown fields {sorted(extra)}")
        for key, item in value.items():
            _validate(item, spec["properties"][key], f"{location}.{key}")
    elif type(value) is list:
        if len(value) < spec.get("minItems", 0):
            raise SchemaError(f"{location}: too few items")
        if spec.get("uniqueItems") and any(item in value[:i] for i, item in enumerate(value)):
            raise SchemaError(f"{location}: duplicate item")
        for i, item in enumerate(value):
            _validate(item, spec["items"], f"{location}[{i}]")
    elif type(value) is str:
        if len(value) < spec.get("minLength", 0):
            raise SchemaError(f"{location}: empty string")
        if "pattern" in spec and re.fullmatch(spec["pattern"], value) is None:
            raise SchemaError(f"{location}: invalid string {value!r}")
    elif type(value) in (int, float) and value < spec.get("minimum", value):
        raise SchemaError(f"{location}: value below minimum")
    elif type(value) in (int, float) and value > spec.get("maximum", value):
        raise SchemaError(f"{location}: value above maximum")


def validate_record(value: Any, kind: str) -> None:
    if type(value) is not dict or type(value.get("schema")) is not str:
        raise SchemaError(f"{kind}: missing string schema identifier")
    expected = f"prooflab.{kind}/v1"
    if value["schema"] != expected:
        if value["schema"].startswith(f"prooflab.{kind}/"):
            raise UnsupportedSchema(f"unsupported schema {value['schema']!r}")
        raise SchemaError(f"{kind}: wrong schema {value['schema']!r}")
    _validate(value, SCHEMAS[kind], kind)
    if kind == "protocol":
        from .measurements import validate_declarations
        validate_declarations(value)
    if kind == "protocol" and "execution" in value:
        # Shared semantic checks also apply to retained execution definitions.
        from .ingestion import validate_execution
        validate_execution(value)
