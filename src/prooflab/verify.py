"""Offline structural verification. Retained content is data, never code."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .artifacts import RESERVED, PathError, check_aliases, logical_path, open_payload, scan_package
from .integrity import file_identity, sha256_bytes
from .protocol import IntegrityState
from .records import RecordError, canonical_json, strict_loads
from .schema import SchemaError, UnsupportedSchema, validate_record


@dataclass(frozen=True)
class Finding:
    state: IntegrityState
    message: str


@dataclass(frozen=True)
class VerificationResult:
    state: IntegrityState
    findings: tuple[Finding, ...]
    manifest_sha256: str | None = None


def verify_package(directory: str | Path, expected_sha256: str | None = None) -> VerificationResult:
    root = Path(directory)
    findings: list[Finding] = []
    digest = None

    def add(state: IntegrityState, message: str) -> None:
        findings.append(Finding(state, message))

    def finish() -> VerificationResult:
        # A known contradiction takes precedence over unknown versions or absence.
        priority = (IntegrityState.INVALID, IntegrityState.UNSUPPORTED, IntegrityState.INCOMPLETE)
        state = next((state for state in priority if any(f.state == state for f in findings)),
                     IntegrityState.VERIFIED)
        return VerificationResult(state, tuple(findings), digest)

    def read(relative: str) -> bytes:
        with open_payload(root, relative) as stream:
            return stream.read()

    def record(relative: str, kind: str) -> dict[str, Any] | None:
        try:
            data = strict_loads(read(relative))
            validate_record(data, kind)
            return data
        except UnsupportedSchema as exc:
            add(IntegrityState.UNSUPPORTED, f"{relative}: {exc}")
        except FileNotFoundError:
            add(IntegrityState.INCOMPLETE, f"missing record: {relative}")
        except (OSError, PathError, RecordError, SchemaError) as exc:
            add(IntegrityState.INVALID, f"{relative}: {exc}")
        return None

    if expected_sha256 is not None and re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
        add(IntegrityState.INVALID, "expected SHA-256 must contain exactly 64 hexadecimal characters")
        return finish()
    try:
        actual = scan_package(root)
    except FileNotFoundError:
        add(IntegrityState.INCOMPLETE, "run directory does not exist")
        return finish()
    except (OSError, PathError, RecursionError) as exc:
        add(IntegrityState.INVALID, f"package scan: {exc}")
        return finish()

    for relative in sorted(RESERVED):
        if relative not in actual:
            if (root / relative).is_dir():
                add(IntegrityState.INVALID, f"required file is a directory: {relative}")
            else:
                add(IntegrityState.INCOMPLETE, f"missing required file: {relative}")
    if "manifest.json" not in actual:
        return finish()
    try:
        raw = read("manifest.json")
        digest = sha256_bytes(raw)
        if expected_sha256 is not None and digest != expected_sha256.lower():
            add(IntegrityState.INVALID, "manifest SHA-256 differs from external expected digest")
        if "manifest.sha256" in actual:
            sidecar = read("manifest.sha256")
            if sidecar != (digest + "\n").encode("ascii"):
                add(IntegrityState.INVALID, "manifest.sha256 does not match exact manifest bytes or sidecar format")
        manifest = strict_loads(raw)
        validate_record(manifest, "manifest")
        if canonical_json(manifest) != raw:
            add(IntegrityState.INVALID, "manifest.json is not deterministically encoded")
        paths = [entry["path"] for entry in manifest["files"]]
        check_aliases(paths + sorted(RESERVED))
        if paths != sorted(paths):
            add(IntegrityState.INVALID, "manifest file entries are not sorted by path")
    except UnsupportedSchema as exc:
        add(IntegrityState.UNSUPPORTED, str(exc))
        return finish()
    except (OSError, PathError, RecordError, SchemaError) as exc:
        add(IntegrityState.INVALID, f"manifest: {exc}")
        return finish()

    entries = {entry["path"]: entry for entry in manifest["files"]}
    for relative in sorted(actual - RESERVED - entries.keys()):
        add(IntegrityState.INVALID, f"unlisted payload: {relative}")
    for relative, entry in entries.items():
        if relative not in actual:
            if (root / relative).is_dir():
                add(IntegrityState.INVALID, f"listed payload is a directory: {relative}")
            else:
                add(IntegrityState.INCOMPLETE, f"missing payload: {relative}")
            continue
        try:
            size, checksum = file_identity(root, relative)
            if size != entry["size_bytes"]:
                add(IntegrityState.INVALID, f"size mismatch: {relative}")
            if checksum != entry["sha256"]:
                add(IntegrityState.INVALID, f"SHA-256 mismatch: {relative}")
        except (OSError, PathError) as exc:
            add(IntegrityState.INVALID, f"payload {relative}: {exc}")

    def reference(relative: str, role: str) -> bool:
        try:
            logical_path(relative)
        except PathError as exc:
            add(IntegrityState.INVALID, str(exc))
            return False
        if relative not in entries:
            add(IntegrityState.INVALID, f"reference lacks manifest coverage: {relative}")
            return False
        if entries[relative]["role"] != role:
            add(IntegrityState.INVALID, f"wrong manifest role for {relative}: expected {role}")
            return False
        return True

    if manifest.get("package_type", "run") == "comparison":
        if not findings:
            from .comparison import check_comparison
            try:
                check_comparison(read, entries)
            except UnsupportedSchema as exc:
                add(IntegrityState.UNSUPPORTED, str(exc))
            except FileNotFoundError:
                add(IntegrityState.INCOMPLETE, "missing comparison record")
            except (OSError, ValueError, KeyError, TypeError, StopIteration, RecursionError):
                add(IntegrityState.INVALID, "comparison evidence: schema, identity, accounting, or recomputation mismatch")
        return finish()

    for relative in ("protocol.json", "run.json"):
        if relative not in actual:
            add(IntegrityState.INVALID if (root / relative).is_dir() else IntegrityState.INCOMPLETE,
                f"missing required file: {relative}")

    for relative, role in (("protocol.json", "protocol"), ("run.json", "run")):
        reference(relative, role)
    protocol = record("protocol.json", "protocol") if "protocol.json" in actual else None
    run = record("run.json", "run") if "run.json" in actual else None
    # Check every case record, even if the run has an unsupported schema.
    cases = {relative: record(relative, "case") for relative, entry in entries.items()
             if entry["role"] == "case" and relative in actual}
    for relative, entry in entries.items():
        if entry["role"] in ("protocol", "run") and relative != entry["role"] + ".json":
            add(IntegrityState.INVALID, f"unexpected {entry['role']} record: {relative}")
    if protocol is None or run is None:
        return finish()

    def unique(values: list, label: str) -> None:
        seen = set()
        for value in values:
            if value in seen:
                add(IntegrityState.INVALID, f"duplicate {label}: {value}")
            seen.add(value)

    unique([case["id"] for case in protocol["cases"]], "protocol case ID")
    if run["protocol_id"] != protocol["id"]:
        add(IntegrityState.INVALID, "run protocol_id does not resolve to protocol record")
    if run["variant"] not in protocol["variants"]:
        add(IntegrityState.INVALID, "run variant is not declared in protocol")
    unique([item["id"] for item in run["artifacts"]], "artifact ID")
    unique([item["path"] for item in run["artifacts"]], "artifact path")
    artifact_ids = {item["id"] for item in run["artifacts"]}
    expected_roles = {"protocol.json": "protocol", "run.json": "run"}
    for relative in run["case_records"]:
        reference(relative, "case")
        expected_roles[relative] = "case"
    for item in run["artifacts"]:
        reference(item["path"], "artifact")
        expected_roles[item["path"]] = "artifact"
    for relative in sorted(entries.keys() - expected_roles.keys()):
        add(IntegrityState.INVALID, f"manifest entry not declared by run: {relative}")

    planned = {case["id"]: case["trials"] for case in protocol["cases"]}
    seen_trials: set[tuple[str, int]] = set()
    case_ids = []
    unknown_cases = False
    for relative in run["case_records"]:
        case = cases.get(relative)
        if case is None:
            unknown_cases = True
            continue
        case_ids.append(case["id"])
        if case["run_id"] != run["id"]:
            add(IntegrityState.INVALID, f"{relative}: run_id does not resolve")
        trial = (case["case_id"], case["trial"])
        if trial[0] not in planned or trial[1] > planned[trial[0]]:
            add(IntegrityState.INVALID, f"{relative}: unplanned case/trial {trial}")
        elif trial in seen_trials:
            add(IntegrityState.INVALID, f"duplicate case/trial: {trial}")
        else:
            seen_trials.add(trial)
        for artifact_id in case["artifact_ids"]:
            if artifact_id not in artifact_ids:
                add(IntegrityState.INVALID, f"{relative}: unresolved artifact ID {artifact_id}")
        unique([item["id"] for item in case["measurements"]], f"measurement ID in {relative}")
        if "execution" not in case and ("outputs" in case or any("extractor" in m for m in case["measurements"])):
            add(IntegrityState.INVALID, f"{relative}: derived observations require execution receipt")
        for measurement in case["measurements"]:
            observed = measurement["state"] == "observed"
            if observed == (measurement["value"] is None):
                add(IntegrityState.INVALID, f"{relative}: measurement {measurement['id']} "
                    "must have a numeric value exactly when observed")
    unique(case_ids, "case record ID")
    expected_count = sum(planned.values())
    if len(seen_trials) < expected_count:
        add(IntegrityState.INCOMPLETE, f"case/trial accounting: {len(seen_trials)} of "
            f"{expected_count} planned trials available" +
            (" (some records could not be interpreted)" if unknown_cases else ""))
    if "execution" in run and not findings:
        from .execution_evidence import check_execution_evidence
        try:
            check_execution_evidence(protocol, run, cases, entries, read)
        except FileNotFoundError:
            add(IntegrityState.INCOMPLETE, "missing execution evidence")
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            add(IntegrityState.INVALID, f"execution evidence: {exc}")
    elif "execution" not in run and any(case and "execution" in case for case in cases.values()):
        add(IntegrityState.INVALID, "execution receipts require run execution identity")
    return finish()
