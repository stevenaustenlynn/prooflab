"""Explicit, regular-file-only workspace output retention."""

from .materials import MaterialError, checked_root, read_identity


def unobserved_outputs(declarations):
    return [{**o, "state": "not_evaluated", "artifact_id": None,
             "size_bytes": None, "sha256": None, "reason": "execution_not_started"}
            for o in declarations]


def capture_outputs(workspace, package, declarations, ordinal, artifact, write_bytes):
    observations = unobserved_outputs(declarations)
    try:
        checked_root(workspace)
    except MaterialError:
        for output in observations:
            output.update(state="error", reason="unreadable_or_unsafe")
        return observations
    for index, output in enumerate(observations, 1):
        try:
            size, digest, data = read_identity(workspace, output["path"], retain=True)
        except MaterialError as exc:
            if exc.kind == "missing":
                output.update(state="error" if output["required"] else "not_applicable",
                              reason="required_output_missing" if output["required"] else "optional_output_missing")
            else:
                output.update(state="error", reason=exc.kind)
            continue
        identifier = f"output-{ordinal:06d}-{index:06d}"
        relative = f"outputs/trial-{ordinal:06d}/" + output["path"]
        write_bytes(package / relative, data)
        artifact(identifier, relative)
        output.update(state="observed", reason=None, artifact_id=identifier,
                      size_bytes=size, sha256=digest)
    return observations
