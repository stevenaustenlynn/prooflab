"""Versioned scalar extraction from retained evidence only; no experiment execution."""

from .artifacts import PathError, check_aliases
from .integrity import sha256_bytes
from .records import RecordError, canonical_json, strict_loads
from .schema import SchemaError

VALUE_TYPES = {"integer": int, "float": float, "boolean": bool, "string": str}


def validate_declarations(protocol):
    outputs = protocol.get("outputs", [])
    measurements = protocol.get("measurements", [])
    for name, items in (("outputs", outputs), ("measurements", measurements)):
        if len({item["id"] for item in items}) != len(items):
            raise SchemaError(f"protocol.{name}: duplicate ID")
    try:
        # Inputs cannot accidentally be reported as produced outputs, or block their parents.
        check_aliases([o["path"] for o in outputs] +
                      [m["path"] for m in protocol.get("execution", {}).get("materials", [])])
    except PathError as exc:
        raise SchemaError(f"protocol.outputs/materials: {exc}") from None
    sources = {"stdout", "stderr", "execution"} | {"output:" + o["id"] for o in outputs}
    for m in measurements:
        if m["source"] not in sources:
            raise SchemaError("protocol.measurements: unresolved source")
        execution = m["extractor"] in ("exit_code", "duration_ns")
        if execution != (m["source"] == "execution"):
            raise SchemaError("protocol.measurements: extractor/source mismatch")
        expected = {"sha256": "string", "byte_count": "integer", "utf8": "string",
                    "exit_code": "integer", "duration_ns": "integer"}.get(m["extractor"])
        if expected and m["type"] != expected:
            raise SchemaError("protocol.measurements: extractor/type mismatch")
        if m["extractor"] != "json" and m["parameters"]["path"]:
            raise SchemaError("protocol.measurements: path is only supported for json")
        if m["extractor"] == "duration_ns" and m["unit"] != "ns":
            raise SchemaError("protocol.measurements: duration_ns unit must be ns")


def extract(definition, case, read_artifact):
    """Return the full deterministic record. read_artifact accepts retained IDs.

    Artifact I/O failures propagate: unreadable retained bytes are assembly/integrity
    failures, not a reproducible content extraction error.
    """
    result = {**definition, "state": "observed", "value": None,
              "source_artifact": None, "reason": None}

    def unavailable(state, reason):
        result.update(state=state, reason=reason)
        return result

    source = definition["source"]
    if source in ("stdout", "stderr"):
        result["source_artifact"] = case["execution"][source + "_artifact"]
    elif source.startswith("output:"):
        output = next(o for o in case["outputs"] if o["id"] == source[7:])
        result["source_artifact"] = output["artifact_id"]
    if (case["execution_state"] == "skipped" or case["execution"]["started_at"] is None
            or (source in ("stdout", "stderr") and case["execution"]["exit_code"] is None)):
        return unavailable("not_evaluated", "execution_not_started")
    if source.startswith("output:") and output["state"] != "observed":
        reason = output["reason"] if output["reason"] in (
            "required_output_missing", "optional_output_missing", "execution_not_started") else "output_capture_error"
        return unavailable(output["state"], reason)
    extractor = definition["extractor"]
    if source == "execution":
        value = case["execution"]["exit_code" if extractor == "exit_code" else "elapsed_ns"]
        if value is None:
            return unavailable("not_applicable", "source_unavailable")
    else:
        data = read_artifact(result["source_artifact"])
        if extractor == "sha256":
            value = sha256_bytes(data)
        elif extractor == "byte_count":
            value = len(data)
        elif extractor == "utf8":
            try:
                value = data.decode("utf-8", errors="strict")
            except UnicodeError:
                return unavailable("error", "invalid_utf8")
        else:
            try:
                value = strict_loads(data)
            except RecordError:
                return unavailable("error", "invalid_json")
            for component in definition["parameters"]["path"]:
                if type(component) is str and type(value) is dict and component in value:
                    value = value[component]
                elif type(component) is int and type(value) is list and component < len(value):
                    value = value[component]
                else:
                    return unavailable("error", "missing_json_path")
    if type(value) is not VALUE_TYPES[definition["type"]]:
        return unavailable("error", "type_mismatch")
    result["value"] = value
    return result


def check_trial_evidence(protocol, case, ordinal, artifacts, entries, read):
    """Bind output observations and independently re-extract every declared scalar."""
    def require(condition, reason):
        if not condition:
            raise SchemaError("trial evidence: " + reason)

    declarations = protocol.get("outputs", [])
    outputs = case.get("outputs", [])
    require(len(outputs) == len(declarations), "output accounting mismatch")
    if "outputs" in protocol:
        require("outputs" in case, "missing output observations")
    owned = {case["execution"][s + "_artifact"] for s in ("stdout", "stderr")}
    for declaration, output in zip(declarations, outputs):
        require(all(output[k] == v for k, v in declaration.items()), "output definition mismatch")
        state, reason = output["state"], output["reason"]
        if case["execution"]["exit_code"] is None:
            require(state == "not_evaluated" and reason == "execution_not_started",
                    "unstarted output observation")
        else:
            require(state != "not_evaluated", "executed output was not accounted for")
        if state == "observed":
            identifier = output["artifact_id"]
            relative = f"outputs/trial-{ordinal:06d}/" + declaration["path"]
            require(identifier in artifacts and artifacts[identifier] == relative,
                    "output artifact binding mismatch")
            require(identifier not in owned, "reused output artifact")
            owned.add(identifier)
            require(reason is None, "observed output has reason")
            require((output["size_bytes"], output["sha256"]) ==
                    (entries[relative]["size_bytes"], entries[relative]["sha256"]), "output identity mismatch")
        else:
            require(all(output[k] is None for k in ("artifact_id", "size_bytes", "sha256")),
                    "unobserved output has byte identity")
            if reason in ("required_output_missing", "optional_output_missing"):
                require((state, reason) == (("error", "required_output_missing") if declaration["required"]
                                           else ("not_applicable", "optional_output_missing")), "missing output policy mismatch")
            elif reason == "execution_not_started":
                require(state == "not_evaluated", "unstarted output state mismatch")
            else:
                require(state == "error" and reason is not None, "invalid output error")
    require(set(case["artifact_ids"]) == owned, "trial artifacts differ from streams and declared outputs")
    if "measurements" in protocol or declarations or any("extractor" in m for m in case["measurements"]):
        expected = [extract(m, case, lambda identifier: read(artifacts[identifier]))
                    for m in protocol.get("measurements", [])]
        # Byte comparison distinguishes boolean/integer, integer/float and signed zero.
        require(canonical_json(case["measurements"]) == canonical_json(expected), "measurement recomputation mismatch")
        require(case["acceptance_state"] == "not_applicable", "measurement is not acceptance")
