"""Serial snapshot execution and evidence sealing. This is not a security sandbox."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid

from .ingestion import load_protocol
from .integrity import build_manifest, sha256_bytes
from .materials import checked_root
from .artifacts import open_payload
from .measurements import extract
from .outputs import capture_outputs, unobserved_outputs
from .planning import build_plan
from .protocol import IntegrityState
from .provenance import capture_provenance
from .records import as_record, canonical_json
from .schema import validate_record
from .snapshot import capture_snapshot, populate_workspace
from .verify import verify_package

FINAL_STATES = ("completed", "failed", "timed_out", "interrupted", "skipped")
TERMINATION_GRACE_SECONDS = 0.5


@dataclass(frozen=True)
class RunResult:
    run_id: str
    variant: str
    planned: int
    directory: Path
    counts: dict
    sealed: bool
    exit_code: int
    reason: str | None = None


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory replacement avoids exposing a half-written record.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".write-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_record(path, record, kind=None):
    if kind:
        validate_record(record, kind)
    atomic_bytes(path, canonical_json(record))


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def stop_process(process):
    """Terminate the POSIX session's group, or only the direct child elsewhere."""
    def send(force=False):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif process.poll() is None:
                process.kill() if force else process.terminate()
        except ProcessLookupError:
            pass
    send()
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        send(True)
        process.wait()
    # Descendants may survive the leader's exit; do not leave its group writing evidence.
    if os.name == "posix":
        send(True)


def execute_trial(package, trial, snapshot, receipt, timeout, ordinal, retain_outputs=None):
    process = None
    observation = receipt["execution"]
    started = None
    out = package / f"artifacts/trial-{ordinal:06d}.stdout"
    err = package / f"artifacts/trial-{ordinal:06d}.stderr"
    try:
        with tempfile.TemporaryDirectory(prefix="prooflab-trial-") as temporary:
            workspace = Path(temporary)
            populate_workspace(package, workspace, snapshot)
            env = os.environ.copy()
            env.pop("PROOFLAB_SEED", None)
            if trial.seed is not None:
                env["PROOFLAB_SEED"] = str(trial.seed)
            executable = next(("./" + m["path"] for m in snapshot["materials"]
                               if m["executable"]), None)
            try:
                with out.open("wb") as stdout, err.open("wb") as stderr:
                    observation["started_at"] = timestamp()
                    started = time.monotonic_ns()
                    try:
                        process = subprocess.Popen(list(trial.argv), executable=executable,
                                                   shell=False, cwd=workspace, env=env,
                                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                                   start_new_session=(os.name == "posix"))
                    except OSError:
                        receipt["execution_state"] = "failed"
                        observation["reason"] = "launch_failed"
                    else:
                        try:
                            code = process.wait(timeout=timeout)
                            receipt["execution_state"] = "completed" if code == 0 else "failed"
                            observation["reason"] = None if code == 0 else "nonzero_exit"
                        except subprocess.TimeoutExpired:
                            receipt["execution_state"] = "timed_out"
                            observation["reason"] = "timeout"
                        finally:
                            stop_process(process)
            except KeyboardInterrupt:
                if process is not None:
                    stop_process(process)
                receipt["execution_state"] = "interrupted"
                observation["reason"] = "user_interrupt"
            finally:
                if process is not None:
                    observation["exit_code"] = process.returncode
                    if retain_outputs is not None:
                        retain_outputs(workspace)
    except KeyboardInterrupt:
        if process is not None:
            stop_process(process)
        receipt["execution_state"] = "interrupted"
        observation["reason"] = "user_interrupt"
    finally:
        if process is not None:
            observation["exit_code"] = process.returncode
        if started is not None:
            observation["elapsed_ns"] = time.monotonic_ns() - started
            observation["ended_at"] = timestamp()


def aggregate(states):
    for state in ("interrupted", "failed", "timed_out", "skipped", "running", "planned"):
        if state in states:
            return state
    return "completed"


def run_experiment(path, variant=None, *, project_root=None):
    loaded = load_protocol(path, project_root=project_root)
    plan = build_plan(loaded, variant)
    protocol = as_record(loaded.protocol)
    run_id = "run-" + uuid.uuid4().hex
    # Refuse symlinked output parents, and never overwrite a collision.
    parent = loaded.project_root
    for component in (".prooflab", "runs"):
        parent = parent / component
        parent.mkdir(exist_ok=True)
        checked_root(parent)
    package = parent / run_id
    package.mkdir()
    receipts = []
    run = {"schema": "prooflab.run/v1", "id": run_id, "protocol_id": loaded.protocol.id,
           "variant": plan.variant, "execution_state": "planned", "acceptance_state": "not_applicable",
           "case_records": [], "artifacts": []}

    def artifact(identifier, relative):
        run["artifacts"].append({"id": identifier, "path": relative})

    def measure(receipt):
        paths = {a["id"]: a["path"] for a in run["artifacts"]}
        def read(identifier):
            with open_payload(package, paths[identifier]) as stream:
                return stream.read()
        receipt["measurements"] = [extract(m, receipt, read) for m in protocol.get("measurements", [])]

    def persist():
        run["execution_state"] = aggregate([r["execution_state"] for r in receipts]) if receipts else "planned"
        if run.get("execution", {}).get("interrupted"):
            run["execution_state"] = "interrupted"
        write_record(package / "run.json", run, "run")

    def result(sealed, reason=None):
        counts = Counter(r["execution_state"] for r in receipts)
        return RunResult(run_id, plan.variant, plan.population_count, package,
                         {s: counts[s] for s in FINAL_STATES}, sealed,
                         (0 if counts["completed"] == plan.population_count and not run.get("execution", {}).get("interrupted") else 3) if sealed else 4, reason)

    try:
        atomic_bytes(package / "protocol.json", plan.protocol_bytes)
        persist()
        write_record(package / "plan.json", plan.to_record())
        artifact("plan", "plan.json")
        snapshot = capture_snapshot(loaded.project_root, package, plan)
        artifact("definition", "definition/experiment.toml")
        for material in snapshot["materials"]:
            artifact(material["artifact_id"], "snapshot/" + material["path"])
        write_record(package / "snapshot.json", snapshot)
        artifact("snapshot", "snapshot.json")
        write_record(package / "provenance.json", as_record(
            capture_provenance(loaded.project_root, plan.inventory_sha256)))
        artifact("provenance", "provenance.json")
        identities = {"protocol_sha256": plan.protocol_sha256, "plan_sha256": plan.sha256,
                      "inventory_sha256": plan.inventory_sha256,
                      "snapshot_sha256": sha256_bytes(canonical_json(snapshot))}
        run["execution"] = {**identities, "plan_artifact": "plan", "snapshot_artifact": "snapshot",
                            "definition_artifact": "definition", "provenance_artifact": "provenance",
                            "cwd": "trial_workspace_root", "environment": "inherited_with_prooflab_seed", "interrupted": False}
        (package / "artifacts").mkdir()
        for ordinal, trial in enumerate(plan.trials, 1):
            for stream in ("stdout", "stderr"):
                relative = f"artifacts/trial-{ordinal:06d}.{stream}"
                (package / relative).touch(exist_ok=False)
                artifact(f"{trial.id}-{stream}", relative)
            receipt = {"schema": "prooflab.case/v1", "id": trial.id, "run_id": run_id,
                       "case_id": trial.case_id, "trial": trial.trial, "execution_state": "planned",
                       "acceptance_state": "not_applicable", "measurements": [],
                       "artifact_ids": [f"{trial.id}-stdout", f"{trial.id}-stderr"],
                       "execution": {**identities, "variant": plan.variant, "argv": list(trial.argv),
                                     "seed": trial.seed, "cwd": "trial_workspace_root", "exit_code": None,
                                     "started_at": None, "ended_at": None, "elapsed_ns": None,
                                     "stdout_artifact": f"{trial.id}-stdout",
                                     "stderr_artifact": f"{trial.id}-stderr", "reason": None}}
            if "outputs" in protocol:
                receipt["outputs"] = unobserved_outputs(protocol["outputs"])
            receipts.append(receipt)
            run["case_records"].append(f"cases/trial-{ordinal:06d}.json")
            write_record(package / run["case_records"][-1], receipt, "case")
        persist()
        interrupted = False
        try:
            for ordinal, (trial, receipt, relative) in enumerate(zip(plan.trials, receipts, run["case_records"]), 1):
                if interrupted:
                    receipt["execution_state"] = "skipped"
                    receipt["execution"]["reason"] = "after_interrupt"
                else:
                    receipt["execution_state"] = "running"
                    write_record(package / relative, receipt, "case")
                    persist()

                    def retain(workspace):
                        if "outputs" in protocol:
                            receipt["outputs"] = capture_outputs(workspace, package, protocol["outputs"],
                                                                 ordinal, artifact, atomic_bytes)
                            receipt["artifact_ids"].extend(o["artifact_id"] for o in receipt["outputs"]
                                                           if o["artifact_id"] is not None)
                    execute_trial(package, trial, snapshot, receipt, loaded.protocol.execution.timeout_seconds,
                                  ordinal, retain)
                    interrupted = receipt["execution_state"] == "interrupted"
                # Preserve completed execution/output accounting even if retained reads fail.
                write_record(package / relative, receipt, "case")
                persist()
                measure(receipt)
                write_record(package / relative, receipt, "case")
                persist()
        except KeyboardInterrupt:
            for receipt in receipts:
                if receipt["execution_state"] in ("planned", "running"):
                    active = receipt["execution_state"] == "running"
                    receipt["execution_state"] = "interrupted" if active else "skipped"
                    receipt["execution"]["reason"] = "user_interrupt" if active else "after_interrupt"
            # A between-trial interruption has no active child. Record it at run level.
            run["execution"]["interrupted"] = True
        if interrupted:
            run["execution"]["interrupted"] = True
        for receipt, relative in zip(receipts, run["case_records"]):
            measure(receipt)
            write_record(package / relative, receipt, "case")
        persist()
        roles = {"protocol.json": "protocol", "run.json": "run",
                 **{p: "case" for p in run["case_records"]},
                 **{a["path"]: "artifact" for a in run["artifacts"]}}
        manifest, sidecar = build_manifest(package, roles)
        atomic_bytes(package / "manifest.json", manifest)
        atomic_bytes(package / "manifest.sha256", sidecar)
        verification = verify_package(package)
        return result(verification.state == IntegrityState.VERIFIED,
                      None if verification.state == IntegrityState.VERIFIED else "offline_verification_failed")
    except (OSError, ValueError, KeyboardInterrupt):
        # No raw exception strings: they can contain host paths or environment values.
        # Retain all written evidence, including when storage or finalization fails.
        return result(False, "evidence_assembly_incomplete")
