# Evidence package v1

An evidence package is a quiescent directory tree. A minimal package contains:

```text
manifest.json
manifest.sha256
protocol.json
run.json
cases/small-1.json
artifacts/result.txt
```

`protocol.json` and `run.json` have fixed locations and manifest roles
`protocol` and `run`. Case paths are listed in `run.case_records`, with role
`case`. Artifacts are declared in `run.artifacts` as `{id, path}`, with role
`artifact`; case records reference their IDs via `artifact_ids`. Artifact
content is opaque, including files whose extension happens to be `.json` or
`.py`. Only files assigned record roles are parsed as records.

All regular files except the two manifest files must appear exactly once in
the inventory. Every manifest entry must be declared by the run or be one of
the two fixed records. Extra files, including hidden files, are invalid.
Empty directories are permitted, checked for path safety, and not inventoried.

## Manifest and byte identity

Each manifest entry has exactly:

```json
{
  "path": "artifacts/result.txt",
  "role": "artifact",
  "sha256": "<64 lowercase hexadecimal characters>",
  "size_bytes": 3
}
```

The manifest object has `schema: "prooflab.manifest/v1"` and a `files` array
sorted lexicographically by logical path, using case-sensitive ASCII order.
The manifest excludes both itself and `manifest.sha256` to avoid cyclic
identity. The sidecar is exactly 64 lowercase hexadecimal characters plus one
LF. Its digest is calculated over the exact bytes of `manifest.json`.

`canonical_json` emits UTF-8 without a BOM, sorted object keys, two-space
indentation, literal Unicode, and exactly one trailing LF. It rejects values
outside the simple JSON type system (including tuples, bytes, arbitrary
objects, and non-string keys). The canonical manifest contains no floats.
Other records use the same generation policy, but the verifier accepts any
strict JSON whitespace/key order for non-manifest records; their exact bytes
are still hashed. This is ProofLab's encoding convention, not a claim of
compliance with an external canonical JSON standard.

All parsed records reject invalid UTF-8, lone Unicode surrogates, BOMs,
duplicate keys at any nesting level, trailing content, `NaN`, infinities,
and floating-point overflow to infinity. Sizes and trial numbers must be
integers, never booleans. Fields are closed: unknown fields are invalid for
v1. An unknown version within the expected schema family is `UNSUPPORTED`;
a missing identifier or wrong family is `INVALID`.

## Portable paths

Package paths are relative POSIX logical paths with `/` separators. Components
use ASCII letters, digits, `_`, `-`, and `.` only. Empty components, `.` and
`..`, trailing dots, backslashes, colons, whitespace, non-ASCII components,
absolute paths, and Windows device names (including names with extensions)
are rejected. This deliberately conservative subset avoids platform aliases.

Duplicate paths and case-insensitive aliases are rejected, including aliases
of intermediate directories. A file cannot also be a directory prefix.
Symlinks and special files are rejected anywhere in the tree, including the
manifest files, directories, and unlisted content. The supplied run directory
itself must be a real directory. Reads check containment and regular-file
type. File permissions, timestamps, ownership, hard-link relationships, and
filesystem extended metadata are not part of the identity.

## Relationships and accounting

The published JSON Schemas describe closed record shapes; the verifier also
checks these relationships and constraints:

- Protocol case IDs are unique; variant names are unique.
- Run `protocol_id` equals the retained protocol ID and the selected variant exists.
- Case record IDs are unique within a run. Each references that run's ID.
- Artifact IDs and artifact paths are unique within a run. Each case artifact ID resolves.
- Measurement IDs are unique within each case record.
- IDs are case-sensitive logical names, scoped as above. Different namespaces
  may reuse an ID; IDs do not automatically become paths.
- Record/artifact paths resolve to manifest entries of the correct role.
- Every case belongs to a planned case and trial in the range `1..trials`.
- Duplicate trials or unplanned cases/trials are invalid. Missing planned
  trials are incomplete. Accounting is checked without allocating an array
  proportional to the declared trial count.
- Measurement values must agree with their availability states.
- Optional `run.integrity_state` is only a retained assertion. Verification
  always recomputes integrity, regardless of its value.

## Phase 2 protocol compatibility

The protocol schema now permits a closed optional `execution` block describing
commands, material declarations, and case bindings. Phase 2 normalized records
include it; the original Phase 1 accounting snapshots remain valid without it.
See [the protocol contract](protocol.md) for its required fields, defaults, and
additional semantic checks. Retained execution blocks are validated without
opening their declared project paths or executing any commands.

## Phase 3 execution packages

A runner package uses the existing four record identities and manifest roles.
Optional closed `run.execution` and `case.execution` fields add execution
relationships; old Phase 1 fixtures without these remain valid. No fifth public
schema identity is introduced. The exact runner layout is:

```text
.prooflab/runs/run-<uuid>/
  definition/experiment.toml        original definition bytes
  protocol.json                    normalized protocol (role protocol)
  plan.json                        deterministic Phase 2 plan (artifact)
  snapshot.json                    inventory plus copy metadata (artifact)
  snapshot/<material logical path> exact source/input/evaluator bytes (artifacts)
  provenance.json                  Phase 2 provenance (artifact)
  run.json                         run identity/progress (role run)
  cases/trial-000001.json           receipt in plan order (role case)
  artifacts/trial-000001.stdout     exact bytes (artifact)
  artifacts/trial-000001.stderr     exact bytes (artifact)
  ...                              one receipt and two streams per planned trial
  manifest.json
  manifest.sha256
```

Numeric filenames (at least six digits) avoid filesystem case collisions between
case-sensitive semantic case IDs. Each receipt retains the Phase 2 trial ID,
case ID and trial index. All auxiliary files and snapshot materials are explicitly
registered in `run.artifacts`. A material retains its `source`, `input`, or
`evaluator` identity role in snapshot metadata; the manifest role remains
`artifact`. No modified workspace is retained. Phase 3 collected stdout/stderr only; Phase
4A additionally retains explicitly declared output files as described below.

`run.execution` contains `protocol_sha256`, `plan_sha256`, `inventory_sha256`,
`snapshot_sha256`, artifact IDs for the definition/plan/snapshot/provenance,
`cwd = "trial_workspace_root"`, `environment = "inherited_with_prooflab_seed"`,
and a boolean `interrupted`. The snapshot digest covers canonical `snapshot.json`.
That object contains `inventory_sha256` and `materials`, an ordered array of
Phase 2 material identities extended with `artifact_id` and boolean `executable`.
Inventory identity still covers only Phase 2 identities, not permissions.

`case.execution` binds the same four digests, variant, literal argv, seed, cwd
semantics, exit code, `started_at`, `ended_at`, monotonic `elapsed_ns`, stdout and
stderr artifact IDs, and a reason. Run ID/case ID/trial/execution state remain in
the enclosing existing case record. Nullable observations distinguish unstarted
trials from executions. Reason codes are `nonzero_exit`, `launch_failed`,
`timeout`, `user_interrupt`, `after_interrupt`, or null. See the public schemas
and [execution contract](protocol.md#serial-execution-phase-3).

The offline verifier additionally checks:

- The exact retained TOML normalizes to the retained protocol.
- The retained plan is canonical and reconstructs from protocol, definition
  identity, captured inventory, selected variant, and deterministic ordered trials.
- All declared captured materials, and only those materials, appear in inventory.
- Snapshot paths, roles, sizes, hashes, executable policy, and artifact references
  agree with that inventory; snapshot bytes agree with material identities.
- Definition bytes agree with definition identity; provenance binds inventory.
- Every receipt is in plan order and binds the same identities, argv, seed and variant.
- Every trial is final, stream IDs are unique, timing is structurally consistent,
  skipped streams are empty, and final execution states agree with exit/reason data.
- Run state agrees with receipts and the run-level interruption flag.

These checks validate claims and bytes, not whether any process actually ran.
Auxiliary identity records are only interpreted when a run opts into the
execution extension; ordinary opaque artifacts retain their Phase 1 semantics.

Records are replaced atomically in their own directories as execution progresses.
Run directories are never overwritten, removed on failure, or automatically
resumed. Missing manifest/sidecar or trial evidence is incomplete. An unfinished
trial inside an execution package with a complete manifest contradicts the final
execution contract and is invalid. The runner does not store or trust a sealed
label: it prints `SEALED` only after its offline verifier returns `VERIFIED`.
A complete manifest alone is insufficient. Re-run `prooflab verify` to check
current bytes; a previous successful check does not prevent later tampering.

Generated records contain logical paths and fixed diagnostics, not live roots,
temporary paths, hostnames, usernames, or environment dumps. Original definition,
source and stream artifacts preserve user-supplied bytes exactly and cannot be
guaranteed free of sensitive content. ProofLab does not redact them because that
would change evidence identity. No undeclared workspace output is retained.

## Results and exit codes

| Result | Exit code | Meaning |
| --- | --- | --- |
| `VERIFIED` | 0 | All implemented byte, schema, path, reference, and accounting checks passed. |
| `INVALID` | 1 | A contradiction, malformed record, unsafe path, hash/size mismatch, or unreadable package was found. |
| `INCOMPLETE` | 3 | Required records, listed payload, sidecar, or planned case/trial records are absent. |
| `UNSUPPORTED` | 4 | A record uses an unimplemented schema version in the expected family. |

CLI argument errors return 2. All five commands are implemented; init exits are
documented in [initialization](initialization.md). Findings are
printed after the result and actual manifest digest. When multiple findings
exist, precedence is `INVALID`, then `UNSUPPORTED`, then `INCOMPLETE`. The
verifier stops where unknown or malformed structure prevents safe meaningful
checks, so diagnostics need not enumerate every possible defect. An omitted
manifest entry for a required reference is a structural contradiction
(`INVALID`), even if that payload is also missing.

`--expect-sha256` accepts a 64-digit hexadecimal digest (either case) and checks
it against exact manifest bytes. A malformed argument or mismatch is invalid.
The manifest sidecar is required even with an external digest.

### Run command exits

| Exit | Meaning |
| --- | --- |
| 0 | Every trial completed operationally and the package sealed. |
| 2 | Invalid definition, variant, materials during planning, invocation, or unavailable/colliding output directory before package creation. |
| 3 | Package sealed, but execution failed, timed out, was skipped, or the user interrupted the run. |
| 4 | Snapshot, evidence assembly, finalization, or mechanical verification failed after package creation; directory retained. |

Material changes caught during planning return 2; changes caught after package
creation during snapshotting return 4. Neither starts experiment execution.
`verify` uses its established table above, independently of execution outcomes.
No run exit code denotes scientific acceptance or scientific rejection.


## Phase 4A output and measurement extension

Only `protocol-v1` and `case-v1` shapes change. Protocol adds optional `outputs`
and `measurements` arrays. Case adds optional `outputs` observations and an
alternative full typed measurement shape alongside legacy numeric measurements.
`run-v1`, `manifest-v1`, their roles and schema identities remain unchanged. Old
Phase 1 synthetic measurements retain their original shape/verification semantics;
old Phase 2/3 definitions without the extension retain their normalized identities.
Built-in recomputation applies to execution packages with declared measurements.
Legacy accounting-only packages do not claim executable measurement provenance.
Typed observations require execution receipts and their run identity bindings.

```text
cases/trial-000001.json                  receipt, output observations, measurements
artifacts/trial-000001.stdout            retained stream
artifacts/trial-000001.stderr            retained stream
outputs/trial-000001/out/result.json     exact declared output bytes
```

Measurements stay in the existing case record rather than a redundant fifth record
or separate measurement file. Case bytes have manifest role `case`; observed output
files have role `artifact` and are registered in both `run.artifacts` and the owning
case's `artifact_ids`. Numeric trial directories avoid case-sensitive ID collisions.
Output artifact IDs are `output-<six-digit-trial>-<six-digit-output-index>`, with
indices assigned in normalized output-ID order. Missing outputs have no artifact.

An example output observation is:

```json
{
  "id": "result", "path": "out/result.json", "required": true,
  "state": "observed", "artifact_id": "output-000001-000001",
  "size_bytes": 15, "sha256": "<digest of exact bytes>", "reason": null
}
```

A derived measurement is:

```json
{
  "id": "answer", "source": "output:result", "extractor": "json",
  "version": 1, "type": "integer", "unit": "",
  "parameters": {"path": ["answer"]},
  "source_artifact": "output-000001-000001",
  "state": "observed", "value": 10, "reason": null
}
```

The [protocol contract](protocol.md#declared-outputs-and-measurements-phase-4a)
defines every extractor, default, scalar type and state/reason mapping. The verifier
checks complete output accounting against declarations, exact observation identities
against manifest entries, correct trial ownership and paths, and measurement
recomputation from retained bytes/receipts. Typed measurements cannot be silently
omitted, substituted, changed to an invented error, or redirected to a different
artifact by merely rebuilding manifest hashes. Record comparison distinguishes
integer/float/boolean and signed floating zero. No record receives a scientific
verdict from extraction; runner acceptance remains `not_applicable`.

Output absence/type-error observations remain historical assertions: offline
verification cannot inspect the discarded workspace to prove a file was missing
or a symlink. It can check their internal consistency and reproduce the resulting
measurement state. All retained output bytes are hashed, even when extraction
fails. Required absence and content extraction errors can seal with run exit 0
when every command completed operationally. Disk failures remain assembly failures.

Generated receipt metadata excludes live filesystem roots and environment dumps.
Output artifacts and extracted values are unredacted user-controlled data; a string
measurement can carry private values into a case record. Review before sharing.


## Phase 4B comparison packages

Comparison packages use an explicit `package_type = "comparison"` manifest extension
and a `comparison` role for `comparison.json`. Omission of package_type still means a
run package. Existing run verification is unchanged. The new closed schemas are
`prooflab.comparison/v1` and `prooflab.comparison-policy/v1`.

See [the comparison contract](comparison.md#package-and-verification) for the exact
layout, source manifest and policy bindings, compatibility checks, pair accounting,
recomputation, exit codes and full-lineage limitation. Scientific FAIL/INCONCLUSIVE
are independent of mechanical VERIFIED. Comparison does not execute retained code.


## Phase 5 reports

Reports are external Markdown views of verified v1 run or comparison packages.
No evidence schemas or retained bytes change. Report exits differ from run and
compare exits: 0 means rendering succeeded, even for a scientific FAIL or
INCONCLUSIVE, or for incompatible inputs. Invalid, incomplete and unsupported
evidence retain verify exits 1, 3 and 4. Invocation/destination/write failures use 2.
See [the reporting contract](reporting.md).
