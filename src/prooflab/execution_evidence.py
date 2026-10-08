"""Offline Phase 3 relationships, using retained data only. No subprocess calls."""

from datetime import datetime

from .artifacts import check_aliases
from .ingestion import parse_protocol
from .integrity import sha256_bytes
from .materials import MaterialIdentity
from .measurements import check_trial_evidence
from .planning import ExecutionPlan, PlannedTrial
from .records import as_record, canonical_json, strict_loads
from .schema import DIGEST, ID, PATH, SchemaError, _array, _object, _validate

MATERIAL = _object({"id": ID, "path": PATH,
                    "role": {"enum": ["source", "input", "evaluator", "definition"]},
                    "size_bytes": {"type": "integer", "minimum": 0}, "sha256": DIGEST})
SNAPSHOT = _object({"inventory_sha256": DIGEST,
                    "materials": _array(_object({**MATERIAL["properties"],
                                                 "artifact_id": ID, "executable": {"type": "boolean"}}))})


def check_execution_evidence(protocol, run, cases, entries, read):
    """Raise on contradictions; missing payloads are handled by the caller."""
    def require(condition, message):
        if not condition:
            raise SchemaError("execution evidence: " + message)

    execution = run["execution"]
    artifacts = {a["id"]: a["path"] for a in run["artifacts"]}

    def path(identifier):
        require(identifier in artifacts, "unresolved artifact")
        return artifacts[identifier]

    def identity(identifier, expected):
        entry = entries[path(identifier)]
        require((entry["size_bytes"], entry["sha256"]) ==
                (expected["size_bytes"], expected["sha256"]), "snapshot/definition bytes differ from identity")

    raw_definition = read(path(execution["definition_artifact"]))
    normalized = as_record(parse_protocol(raw_definition))
    require(normalized == protocol, "original definition differs from normalized protocol")
    raw_plan = read(path(execution["plan_artifact"]))
    require(sha256_bytes(raw_plan) == execution["plan_sha256"], "plan digest mismatch")
    plan = strict_loads(raw_plan)
    require(type(plan) is dict and type(plan.get("materials")) is list and "definition" in plan,
            "invalid plan shape")
    _validate(plan["definition"], MATERIAL, "plan.definition")
    for material in plan["materials"]:
        _validate(material, MATERIAL, "plan.materials")
    definition = plan["definition"]
    require(definition["id"] == "definition" and definition["role"] == "definition", "invalid definition identity")
    require(definition["size_bytes"] == len(raw_definition) and
            definition["sha256"] == sha256_bytes(raw_definition), "definition identity mismatch")
    check_aliases([definition["path"], *[m["path"] for m in protocol["execution"]["materials"]]])
    declarations = [m for m in protocol["execution"]["materials"] if m["capture"]]
    require([{k: m[k] for k in ("id", "path", "role")} for m in plan["materials"]] ==
            [{k: m[k] for k in ("id", "path", "role")} for m in declarations], "inventory differs from declarations")
    command = next(c for c in protocol["execution"]["commands"] if c["variant"] == run["variant"])
    bindings = {b["case_id"]: b for b in protocol["execution"]["bindings"]}
    # Bound reconstruction by available receipts, not an untrusted trial count.
    population = sum(c["trials"] for c in protocol["cases"])
    require(population == len(run["case_records"]), "incomplete execution accounting")
    trials = []
    for case in protocol["cases"]:
        binding = bindings[case["id"]]
        for number in range(1, case["trials"] + 1):
            trials.append(PlannedTrial(f"{case['id']}-trial-{number:04d}", case["id"], number,
                                       tuple(command["argv"] + binding["args"]), tuple(binding["input_ids"]),
                                       None if binding["seed"] is None else binding["seed"] + number - 1))
    expected = ExecutionPlan(canonical_json(protocol), sha256_bytes(canonical_json(protocol)),
                             MaterialIdentity(**definition), run["variant"], tuple(command["source_ids"]),
                             tuple(MaterialIdentity(**m) for m in plan["materials"]), tuple(trials))
    require(canonical_json(expected.to_record()) == raw_plan, "plan is not the deterministic retained plan")
    require(execution["protocol_sha256"] == expected.protocol_sha256 and
            execution["inventory_sha256"] == expected.inventory_sha256, "protocol/inventory digest mismatch")
    raw_snapshot = read(path(execution["snapshot_artifact"]))
    require(sha256_bytes(raw_snapshot) == execution["snapshot_sha256"], "snapshot digest mismatch")
    snapshot = strict_loads(raw_snapshot)
    _validate(snapshot, SNAPSHOT, "snapshot")
    require(snapshot["inventory_sha256"] == expected.inventory_sha256, "snapshot inventory mismatch")
    require([{k: m[k] for k in MATERIAL["properties"]} for m in snapshot["materials"]] == plan["materials"],
            "snapshot materials mismatch")
    snapshot_ids = [m["artifact_id"] for m in snapshot["materials"]]
    require(len(set(snapshot_ids)) == len(snapshot_ids), "duplicate snapshot artifact")
    for material in snapshot["materials"]:
        require(path(material["artifact_id"]) == "snapshot/" + material["path"], "snapshot logical path mismatch")
        require(material["executable"] == (material["path"] == command["argv"][0].removeprefix("./")),
                "snapshot executable policy mismatch")
        identity(material["artifact_id"], material)
    identity(execution["definition_artifact"], definition)
    provenance = strict_loads(read(path(execution["provenance_artifact"])))
    require(type(provenance) is dict and provenance.get("inventory_sha256") == expected.inventory_sha256,
            "provenance inventory mismatch")
    states = []
    streams = set()
    interrupted = False
    for ordinal, (trial, relative) in enumerate(zip(trials, run["case_records"]), 1):
        case = cases[relative]
        require(case is not None and "execution" in case, "missing execution receipt")
        receipt = case["execution"]
        require(case["id"] == trial.id and case["case_id"] == trial.case_id and case["trial"] == trial.trial,
                "receipt order/identity mismatch")
        for key in ("protocol_sha256", "plan_sha256", "inventory_sha256", "snapshot_sha256"):
            require(receipt[key] == execution[key], "receipt identity mismatch")
        require(receipt["argv"] == list(trial.argv) and receipt["seed"] == trial.seed and
                receipt["variant"] == run["variant"], "receipt command/seed/variant mismatch")
        state = case["execution_state"]
        require(state in ("completed", "failed", "timed_out", "interrupted", "skipped"), "unfinished trial")
        require(not interrupted or state == "skipped", "execution continued after interruption")
        require(state != "skipped" or execution["interrupted"], "skipped without interruption")
        interrupted |= state in ("interrupted", "skipped")
        code, reason = receipt["exit_code"], receipt["reason"]
        if state == "completed":
            require(code == 0 and reason is None, "completed process must exit zero")
        elif state == "failed":
            require((code is None and reason == "launch_failed") or
                    (code is not None and code != 0 and reason == "nonzero_exit"), "failed process mismatch")
        else:
            require(reason == {"timed_out": "timeout", "interrupted": "user_interrupt", "skipped": "after_interrupt"}[state],
                    "execution reason mismatch")
        if state == "timed_out":
            require("timeout_seconds" in protocol["execution"] and code is not None, "timeout requires policy and exit code")
        times = [receipt[k] for k in ("started_at", "ended_at", "elapsed_ns")]
        require(all(t is None for t in times) or all(t is not None for t in times), "partial timing")
        if state in ("completed", "failed", "timed_out"):
            require(all(t is not None for t in times), "missing execution timing")
        if times[0] is not None:
            for value in times[:2]:
                require(datetime.fromisoformat(value).utcoffset() is not None, "timestamp needs timezone")
        if state == "skipped":
            require(code is None and all(t is None for t in times), "skipped process has execution observations")
        for key in ("stdout_artifact", "stderr_artifact"):
            identifier = receipt[key]
            require(identifier in case["artifact_ids"] and identifier not in streams, "invalid/reused stream artifact")
            streams.add(identifier)
            stream_path = path(identifier)
            if state == "skipped":
                require(entries[stream_path]["size_bytes"] == 0, "skipped stream is not empty")
        check_trial_evidence(protocol, case, ordinal, artifacts, entries, read)
        states.append(state)
    # Kept independent of runner imports: the offline verifier cannot launch code.
    aggregate = next((s for s in ("interrupted", "failed", "timed_out", "skipped") if s in states), "completed")
    require(not interrupted or execution["interrupted"], "missing run interruption flag")
    if execution["interrupted"]:
        aggregate = "interrupted"
    require(run["execution_state"] == aggregate, "run execution state differs from receipts")
    if protocol.get("outputs") or "measurements" in protocol:
        require(run["acceptance_state"] == "not_applicable", "measurements do not confer acceptance")
        declared_outputs = {artifacts[o["artifact_id"]] for case in cases.values()
                            for o in case.get("outputs", []) if o["artifact_id"] is not None}
        require({p for p in artifacts.values() if p.startswith("outputs/")} == declared_outputs,
                "undeclared retained output")
