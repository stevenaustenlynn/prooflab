"""Stream exact declared bytes into ordinary copies; never copy host metadata."""

import shutil
from pathlib import Path

from .artifacts import open_payload
from .integrity import file_identity
from .materials import MaterialError, compare_materials
from .records import as_record


def copy_verified(source: Path, relative: str, destination: Path,
                  size: int, digest: str, *, executable: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open_payload(source, relative) as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    destination.chmod(0o700 if executable else 0o600)
    if file_identity(destination.parent, destination.name) != (size, digest):
        raise MaterialError(relative, "snapshot_identity_mismatch")


def capture_snapshot(root, package, plan):
    if compare_materials(root, (plan.definition, *plan.materials)):
        raise MaterialError("materials", "changed_before_snapshot")
    definition = plan.definition
    copy_verified(root, definition.path, package / "definition/experiment.toml",
                  definition.size_bytes, definition.sha256)
    executable = plan.trials[0].argv[0].removeprefix("./")
    entries = []
    for index, material in enumerate(plan.materials):
        is_executable = material.path == executable
        copy_verified(root, material.path, package / "snapshot" / material.path,
                      material.size_bytes, material.sha256, executable=is_executable)
        entries.append({**as_record(material), "artifact_id": f"material-{index:04d}",
                        "executable": is_executable})
    return {"inventory_sha256": plan.inventory_sha256, "materials": entries}


def populate_workspace(package, workspace, snapshot):
    for material in snapshot["materials"]:
        copy_verified(package, "snapshot/" + material["path"], workspace / material["path"],
                      material["size_bytes"], material["sha256"],
                      executable=material["executable"])
