# Public semantic contract (v1)

An experiment names variants and ordered cases, each with a positive trial
count. A run selects one variant and retains one case record per
`(case_id, trial)` pair. Trials start at 1. Skipped or interrupted trials still
need explicit records for complete accounting.

Phase 2 ingestion, deterministic planning, and material identity APIs remain
read-only. Phase 3 adds `prooflab run`: serial execution from verified copies,
receipt retention, and mechanical sealing. Phase 4A adds declared outputs and
built-in scalar extraction. Phase 4B adds separate deterministic retained-run
comparison and policy-based acceptance; see [the comparison contract](comparison.md).
Phase 5 adds [verified Markdown reporting](reporting.md). Phase 6 adds [safe starter initialization](initialization.md) without changing the protocol.

## Load and plan

Python 3.11+ standard-library `tomllib` loads UTF-8 TOML. Use from a checkout:

```sh
PYTHONPATH=src python3 - <<'PY'
from prooflab.ingestion import load_protocol
from prooflab.planning import build_plan

loaded = load_protocol("examples/sum/experiment.toml")
plan = build_plan(loaded, "iterative")
print(loaded.protocol_sha256)
print(plan.inventory_sha256)
print(plan.sha256, plan.population_count)
for trial in plan.trials:
    print(trial.id, trial.argv)
PY
```

`parse_protocol(bytes)` validates and normalizes without accessing files.
`load_protocol(path, project_root=...)` additionally binds the definition's
exact bytes and a local root. `build_plan(loaded, variant)` checks that the
loaded definition is unchanged, captures every non-excluded material (also
those belonging to other variants), and returns an immutable `ExecutionPlan`.
It checks identities again before returning. No run directory is created.

## Definition shape

The unreleased `prooflab.protocol/v1` keeps its existing `id`, `variants`
(array of names), and `cases` (array of `{id, trials}`) accounting fields.
Phase 2 adds an optional `execution` block to the retained schema. It is
**required for TOML ingestion and planning**. Legacy Phase 1 JSON accounting
records without it remain valid and verifiable. There is no parallel protocol
model and no new public schema identity.

A minimal definition is:

```toml
schema = "prooflab.protocol/v1"
id = "demo"
variants = ["baseline"]

[[cases]]
id = "small"
trials = 2

[[execution.commands]]
variant = "baseline"
argv = ["python3", "src/implementation.py"]
source_ids = ["implementation"]

[[execution.materials]]
id = "implementation"
path = "src/implementation.py"
role = "source"

[[execution.materials]]
id = "small-input"
path = "inputs/small.txt"
role = "input"

[[execution.bindings]]
case_id = "small"
args = ["inputs/small.txt"]
input_ids = ["small-input"]
seed = 100
```

All fields are closed. Required top-level fields are `schema`, `id`, `variants`,
`cases`, and `execution`. An optional `execution.timeout_seconds` is an integer from 1 through 86400 seconds;
omission means no timeout. The one-day cap bounds the wait parameter and rejects
integer overflow inputs before launching a child. It is omitted from normalized JSON when absent,
preserving Phase 2 identities. Explicit null, booleans, fractions, and zero fail.
Each of the three execution arrays (`commands`,
`materials`, `bindings`) is required. Commands require `variant`, `argv`, and
nonempty `source_ids`. Materials require `id`, `path`, and `role`; `capture`
defaults to `true`, `reason` to `""`. Bindings require `case_id`; `args` and
`input_ids` default to empty arrays, `seed` to JSON `null`. Missing case
`trials` defaults to 1. Retained normalized JSON contains every default.

There must be exactly one command per variant and one binding per case.
Names/IDs use ASCII letters, digits, underscores, hyphens and dots, starting
with a letter or digit. They are case-sensitive semantic identifiers, not
package filenames. Duplicate IDs within a namespace fail. Duplicate names,
commands, bindings, material IDs, and reference IDs fail. References must
resolve with the right role and to captured materials. Each command declares
at least one source material. Evaluator files can be identified with role
`evaluator`; external evaluator execution and plugin contracts are not defined.

Trials must be positive integers, seeds nonnegative integers. Booleans,
strings, and floats are not coerced to integers. TOML syntax errors, invalid
UTF-8/BOMs, unsupported schema versions, unknown/missing fields, malformed
paths, and invalid references fail with field/path diagnostics. v1 defines `measurements` as described below; it does not define
acceptance gates: `metrics`, `gates`, and similar
unknown fields are rejected rather than silently ignored.

## Commands and selection

Exactly one variant permits implicit selection. Multiple variants require an
explicit name; unknown names fail. The selected name and source IDs are part
of the plan identity. Selecting a different variant changes that identity.

`argv` is a nonempty array of literal strings, with a nonblank executable.
The final trial command is `command.argv + binding.args`, preserving order.
The working directory is a fresh isolated trial workspace recreating captured
logical paths at its root. It is never the original project root. `python3` in the example is an
external executable name; its installed bytes are **not** captured.

There is no shell joining, shell expansion, placeholder substitution, glob
expansion, or automatic interpretation of argument strings as material paths.
Control characters, backslashes, parent path components, and absolute/home
path tokens (including `--option=/path`) are rejected in commands and args.
These checks are portability constraints, not a shell or security sandbox:
an explicitly declared interpreter may itself execute arbitrary code later.
Command strings are user-authored data; ProofLab cannot infer dependencies
from them or certify that they contain no sensitive information.

## Normalization and planning

Normalization uses `canonical_json`: sorted object keys, two-space indentation,
UTF-8, and one final LF. Variants and commands sort by variant name; material
declarations sort by logical path; bindings sort by case ID; source/input
reference sets sort by ID. Case order and argument order are semantic and
remain exactly as declared. Defaults are explicit. Comments, TOML whitespace,
key order, and declaration order of these sets do not affect normalized identity.

`protocol_sha256` hashes normalized protocol JSON. The separately recorded
`definition` identity hashes the original TOML bytes and records its logical
path and size. Thus a comment-only edit preserves normalized identity but
changes definition and plan identity. A loaded definition cannot silently be
reused after even a comment edit: reload explicitly.

Planning visits cases in declared order, then trials `1..trials` ascending.
IDs are `<case-id>-trial-<number>` with at least four digits, for example
`small-trial-0001`. Every trial includes its case ID, integer trial, final argv,
input IDs, and seed. A declared base seed yields `base + trial - 1`; an omitted
seed stays `null`. Planning neither alters arguments nor seeds Python. The runner supplies a
declared seed as the decimal `PROOFLAB_SEED` environment variable for each trial,
and removes any inherited `PROOFLAB_SEED` when the plan has no seed. Programs
must opt in to consuming it; no RNG is automatically seeded.

Population is `sum(case.trials)` and can be calculated before allocating a
plan. `build_plan` defaults to a 100,000-trial allocation guard; callers may
explicitly raise `max_population`. It rejects an oversized plan without
omitting trials or redefining the public trial-count contract.

Ingested records use frozen dataclasses with tuple collections. Plans contain
immutable bytes/tuples/frozen records. `plan.to_record()` returns a detached
portable JSON-compatible value. `plan.sha256` binds normalized protocol,
original definition identity, selection, full captured inventory, and ordered
trials. It contains no timestamps, random IDs, provenance, or local root.
Plan records are a library structure, not a run package or fifth public schema.

## Paths and capture boundary

The default project root is the TOML file's parent, independent of the process
working directory. For `project/experiments/demo.toml` referring to `project/src`,
pass `project_root="project"`. **All** declarations and command-relative paths
then use that root, not the TOML directory. No Git-root discovery is implicit.
The definition itself must be inside the chosen root.

Material logical paths follow the existing package-path rules: relative POSIX
paths, ASCII letters/digits/`_`/`-`/`.` components, no empty components, `.` or
`..`, trailing dots, Windows device names, colons, backslashes, whitespace,
non-ASCII names, or absolute paths. Parent traversal in source declarations
is never supported; choose the common project root instead. There is no
alternate logical-path alias, directory declaration, recursive enumeration,
or glob syntax. An explicit project root is a local binding and is not
retained. `LoadedProtocol.project_root` is host-local and must not be serialized;
use `normalized_bytes` or `plan.to_record()` for portable output.

Only explicit regular files are captured. Symlinks are rejected both at the
file and in directory components; the root and its ancestors must also be real
directories. Directories, devices, FIFOs and sockets cannot be materials.
All declared logical paths (including excluded paths) must be unique without
case aliases, intermediate-directory case collisions, or file/directory
prefix conflicts. The definition path cannot alias a material path.

For each captured path, its parent directories are inspected for sibling names
that collide by case, including undeclared aliases. Unrelated trees are not
traversed. A later sibling alias invalidates a capture. Excluded files are not
opened or checked for existence/type; only their declaration paths are checked.

## Identities, exclusions, and changes

Captured material records contain `id`, `path`, `role`, `size_bytes`, and
lowercase SHA-256. Roles are `input`, `source`, or `evaluator`; the separately
bound definition uses role `definition`. Inventory ordering is by logical path.
`inventory_sha256` hashes the canonical array of captured material records.
The plan additionally binds exclusions through its normalized protocol.

To explicitly document an unused material, declare `capture = false` and a
nonblank `reason`. Captured materials must have an empty reason. Commands and
case bindings cannot reference excluded files. Exclusions are declarations,
not an inventory of bytes. Unlisted repository files, executable binaries,
installed libraries, environment variables, permissions, timestamps, ownership,
and hard-link relationships are outside the capture scope. Declaring a source
file does not discover its imports or prove the dependency list is complete.

SHA-256 identifies exact observed bytes and detects changes relative to a
baseline. It does not establish safety, authorship, authenticity, licensing,
or scientific validity. ProofLab is not a sandbox.

`compare_materials(root, plan.materials)` is a read-only comparison against the
original baseline. To include TOML changes, compare
`(plan.definition, *plan.materials)`. It returns deterministic findings for
missing files, size and digest changes (both if applicable), unsupported/type
changes, new case ambiguity, unreadable/unsafe files, or modification during
capture. It never updates the baseline. Changes to excluded or undeclared bytes
are intentionally outside this comparison. A declaration edit changes the
TOML identity, even if it only adds an exclusion.

Reads hash byte streams and check file identity/size/mtime/ctime before and
after reading, plus the current path binding, to catch ordinary concurrent
changes. Work on a quiescent tree. These checks do not provide an atomic view
of multiple files or protection against malicious filesystem races. Files
may change after a plan is returned. The Phase 3 runner rechecks identities, copies and verifies exact bytes, then
initializes each trial from that snapshot. Ordinary live-file changes after
capture do not affect the copies. Snapshot files are ordinary local files, not
an OS-enforced immutable store or an adversarial security boundary.

## Optional provenance

`capture_provenance(root, plan.inventory_sha256)` separately records ProofLab
and Python versions, OS name, architecture, inventory digest, optional Git
commit, attached/detached/unborn state, and a dirty flag/status-entry count.
Branch names, filenames, diffs, remotes, usernames, hostnames, home paths, and
environment dumps are not retained. Git errors are not retained either.

Git is optional; unavailable, non-repository and timed-out queries produce an
explicit unavailable state. Only bounded read-only Git commands are invoked.
Optional locks, filesystem-monitor hooks and the untracked cache are disabled;
global/system Git config and inherited Git overrides are suppressed. Submodule
worktree inspection is disabled. The dirty count uses porcelain status entries
and groups untracked directories; it is not an exact changed-file count or
proof of source identity. This observation may become stale immediately and is
excluded from protocol and plan digests.

## Independent evidence states

| Dimension | Values |
| --- | --- |
| Execution | `planned`, `running`, `completed`, `failed`, `timed_out`, `interrupted`, `skipped` |
| Measurement | `observed`, `not_applicable`, `not_evaluated`, `error` |
| Acceptance | `pass`, `fail`, `inconclusive`, `not_applicable` |
| Integrity | `verified`, `invalid`, `incomplete`, `unsupported` |

A legacy measurement has a finite numeric value exactly when `observed`. Phase
4A typed measurements also support booleans and strings; all unobserved
states require JSON `null`. Missing measurements are never zero. Execution,
acceptance and integrity are independent. Verification checks supported
structure and consistency. Phase 4A additionally recomputes built-in extraction
from retained evidence, without establishing scientific validity.
Even planned trial records can be mechanically complete.

The original four public identities are `prooflab.protocol/v1`, `prooflab.run/v1`,
`prooflab.case/v1`, and `prooflab.manifest/v1`. Dataclass construction alone is
not validation; ingestion validates shape and relationships. Published JSON
Schemas describe closed shapes; runtime semantics add reference, path and
command checks. No runtime JSON Schema dependency, migration engine, or plugin loader is involved
in validation. Phase 4B adds `prooflab.comparison/v1` and
`prooflab.comparison-policy/v1`. Offline verification never executes experiment code.

## Serial execution (Phase 3)

```sh
prooflab run experiment.toml
prooflab run experiment.toml --variant baseline
prooflab run experiments/demo.toml --project-root . --variant baseline
```

The CLI uses `load_protocol` and `build_plan` unchanged. It creates a unique
`run-<UUID4 hex>` directory under the selected root's `.prooflab/runs/`, rejecting
collisions and symlinked output parents. Run instance IDs do not change the
protocol, plan, or material hashes. No database or final-directory rename is used.
The same directory holds progress, incomplete evidence, and the completed package.

Before execution it revalidates the definition and every captured material,
streams ordinary copies of their exact bytes, and verifies copied sizes and
SHA-256 against the plan. A mismatch prevents execution. All captured materials
are included, even sources for unselected variants and evaluator-role files.
Evaluator files are only copied; no evaluator subsystem is implemented.
Excluded and undeclared files are not copied. The original TOML is retained
separately from the normalized protocol. No imports or recursive dependency
searches are performed.

Every trial gets a Python-managed temporary directory populated with freshly
copied and verified snapshot materials at their declared logical paths. Copies
are independent ordinary files, not links to the source or to another trial.
The definition is retained as evidence but is not an undeclared workspace input.
The workspace is deleted after child termination and declared-output capture.
Arbitrary undeclared outputs and modified input/source files are not collected.

Only a material matching the selected command's executable (after removing a
leading `./`) receives owner execute permission. Copies use mode 0700 for that
file and 0600 otherwise on POSIX; execution declaration supplies this minimum
permission, irrespective of live source mode. No source permissions, ownership,
ACLs, timestamps, or other metadata are copied. A matching bare executable name
is resolved explicitly to `./<captured path>` via `Popen(executable=...)`, keeping
the declared argv unchanged. Other executable names use inherited PATH; external
interpreters, installed libraries, and their dependencies are not fingerprinted.
Permission enforcement on Windows follows Python/OS limitations.

The subprocess receives the literal plan argv array with `shell=False`, stdin
from the null device, and separate file-backed binary stdout/stderr. There is no
joining, interpolation, wildcard expansion, or ProofLab-introduced shell. A user
can explicitly declare an interpreter or shell; that selected program still
controls its own argument semantics. Arguments never become safe just because
ProofLab passes them literally.

The parent environment is inherited, with only the documented `PROOFLAB_SEED`
addition/removal. No environment dump or arbitrary environment values are put in
generated records. Inherited PATH, Python configuration, locale, and other values
can affect execution and can expose credentials to the child. This is generic
local execution, not hermetic execution or host security isolation. Code can read
or modify anything available to the invoking user, including absolute paths it
constructs itself. Snapshotting controls declared input bytes, not code behavior.

Cases execute in protocol order and trials in ascending order. Receipts use the
existing final states: `completed`, `failed`, `timed_out`, `interrupted`, `skipped`.
Zero exit means operational completion; nonzero exit or launch failure means
`failed`. These runs use `acceptance_state = "not_applicable"`. Declared
measurements observe retained evidence without judging scientific correctness.

`timeout_seconds`, when present, applies to each child wait after launch. On
POSIX, the child starts a new session; cleanup sends SIGTERM to its process group,
waits up to 0.5 seconds for the direct child, then uses SIGKILL if needed and reaps
it. Any surviving members of that group are killed after the leader exits,
including after normal completion. On other platforms only the direct child is
terminated/killed and reaped. This is not complete process-tree containment:
descendants can escape POSIX groups, and other-platform descendants are not
managed. Such processes can keep writing inherited output handles, so a stable
package cannot be promised for programs that leave escaping descendants.
Timeouts retain partial streams, record `timed_out`, and continue the plan.

A normal Ctrl-C while trials are running terminates/reaps the active child,
records `interrupted`, and marks unstarted trials `skipped`. Interruption between
trials sets the run's interruption flag without inventing an active execution.
The runner attempts sealing and returns 3 if successful, even if interruption
arrived just after the final trial completed. An additional interruption during
cleanup/finalization, storage failure, or abrupt kill may leave an incomplete
package. Completed receipts and streams already written remain inspectable.
There is no resume or crash-proof durability guarantee.

Receipt start/end timestamps are UTC wall-clock observations. `elapsed_ns` is a
monotonic duration covering launch/wait/termination, declared-output capture, and workspace cleanup after
timing starts; it is an execution observation, not a statistical benchmark.
Preparation-only interrupted trials can have null timing and exit code. Failed
launches have timing but no exit code. Skipped trials have empty streams and null
timing/exit code. Reasons are fixed portable codes, never raw OS exception text.

Run aggregation prioritizes interruption, failure, timeout, then skipped; all
completed gives `completed`. Every trial receipt is initialized and persisted
before execution and atomically replaced as it advances. Run progress is also
replaced after each trial. Once final accounting is complete the runner builds
the manifest/sidecar and invokes the offline verifier. Only `VERIFIED` permits
`SEALED`. Sealing failures return 4 and leave the directory intact.

Phase 2 provenance is captured automatically for a run and binds the captured
inventory. Its Git observation refers to the original tree near snapshot time;
execution uses the retained snapshot, not that live Git tree. Provenance is
outside deterministic identities and does not fingerprint external interpreters.


## Declared outputs and measurements (Phase 4A)

Two optional top-level arrays extend unreleased protocol v1: `outputs` and
`measurements`. Omitting either leaves it absent in normalized JSON, preserving
old protocol identities. Explicit empty arrays remain explicit. Definitions sort
by case-sensitive ID; declaration order is not semantic. Changing any extractor,
source, type, unit, version or parameter changes normalized protocol identity and
therefore plan identity. No hidden measurement configuration is used.

```toml
[[outputs]]
id = "result"
path = "out/result.json"
required = true

[[measurements]]
id = "answer"
source = "output:result"
extractor = "json"
type = "integer"
version = 1
unit = ""
parameters.path = ["answer"]
```

Output fields are exactly `id`, `path`, and `required` (default true). IDs follow
existing case-sensitive semantic ID rules and are unique in the output namespace.
Paths follow the portable material-path rules above and are unique without case
aliases, including directory components and file/directory prefix conflicts.
Outputs cannot alias any declared material, including exclusions. There are no
globs, absolute paths, traversal, directory outputs or implicit recursive capture.
A role field is unnecessary in this phase: all declarations identify output files.

After termination and process cleanup, each declared path is checked within the
workspace, including parent components and sibling case aliases. Only regular
files are read; links (including dangling links and directory links), directories,
FIFOs, devices and sockets are rejected without following/opening them as data.
The same before/after identity checks used for materials detect ordinary changes
during capture. Retention preserves exact bytes with no normalization. Each output
observation binds the declaration, state, artifact ID, size and SHA-256. For an
unobserved output all three byte-identity fields are null; no empty artifact is
fabricated. A genuinely produced empty file is observed with size zero.

| Output condition | State | Reason |
| --- | --- | --- |
| Retained regular file | `observed` | null |
| Required file absent | `error` | `required_output_missing` |
| Optional file absent | `not_applicable` | `optional_output_missing` |
| Process never started, including skipped/launch failure | `not_evaluated` | `execution_not_started` |
| Unsafe, ambiguous, unreadable or changing file | `error` | fixed capture reason |

Capture reasons are `ambiguous_path`, `type_change_or_unsupported`,
`unreadable_or_unsafe`, and `changed_during_capture`. These observations make no
claim that an absent optional output is scientifically irrelevant. The
`not_applicable` state means its source is unavailable under the optional policy.
There is no new missing state. Required absence and extraction errors do not alter
execution state or run exit codes. Complete, truthful error accounting can seal.
Package storage/read failures still prevent sealing; they are not content errors.

Each measurement requires `id`, `source`, `extractor`, and `type`. Defaults are
`version = 1`, `unit = ""`, and `parameters.path = []`. Parameters are a closed
object containing only `path`; it must be empty except for `json`. All defaults
are retained explicitly. Measurement IDs are unique. `unit` is a literal label;
no conversions occur. `duration_ns` requires `unit = "ns"`. No direction, threshold,
comparison role, acceptance rule, expression, or external plugin is supported.

Sources are `output:<declared-id>`, `stdout`, `stderr`, or `execution`. Artifact
extractors require output/stream sources; receipt extractors require `execution`.
A derived record repeats its complete normalized definition, including version
and parameters, plus `state`, `value`, `source_artifact`, and `reason`. A source
artifact ID resolves through `run.artifacts`; it is null for receipt fields or
unretained outputs. Receipt measurements refer to the enclosing case's bound
`execution` fields. The full definition is part of protocol identity.

| Extractor (version 1) | Value type | Semantics |
| --- | --- | --- |
| `sha256` | `string` | Lowercase SHA-256 of exact retained bytes |
| `byte_count` | `integer` | Exact retained byte length |
| `utf8` | `string` | Strict UTF-8 decoding; preserves BOM, whitespace, CR/LF and NUL |
| `json` | declared scalar type | Strict JSON, then key/index traversal |
| `exit_code` | `integer` | Enclosing receipt's exit code, including nonzero/negative codes |
| `duration_ns` | `integer` | Enclosing receipt's `elapsed_ns`; operational duration in ns |

JSON paths are arrays of literal string object keys and nonnegative integer array
indices. For example, `["samples", 0, "answer"]` selects the first sample's answer;
`[]` selects the document root. Empty keys and numeric-looking string keys work
literally. There is no dotted syntax, escaping mini-language, wildcard, expression
or JSONPath dependency. Wrong container types, absent keys and out-of-range indices
produce `error` / `missing_json_path`. Parsing rejects duplicate keys anywhere,
invalid UTF-8/BOM/surrogates, trailing content, NaN, infinities and numeric overflow
to infinity, even outside the selected path (`invalid_json`). Invalid text decoding
produces `invalid_utf8`.

Scalar types are exactly `integer`, `float`, `boolean`, and `string`. JSON integers
are arbitrary-precision Python integers; floats use finite Python binary64 values
and the existing canonical JSON encoder. Types are never coerced: `1` does not
satisfy `float`, `1.0` does not satisfy `integer`, and booleans do not satisfy either.
JSON null, arrays and objects are not scalar measurements (`type_mismatch`). Float
rounding/underflow follows Python JSON parsing; original bytes always remain retained.

A value is present exactly when `observed`; otherwise value is null. Missing output
measurements inherit the output state and missing reason. Other capture failures
become `error` / `output_capture_error`. Unstarted/skipped trials yield
`not_evaluated` / `execution_not_started`; launch failures have no output observation
or exit code, but can have an observed duration. Absent receipt fields yield
`not_applicable` / `source_unavailable`. Nonzero, timed-out and interrupted processes
can still provide observed outputs and measurements. Unstarted stream measurements
retain their stream reference but are not treated as measured empty strings/zeroes.

Extraction reads only retained artifact bytes after workspace cleanup, or bound
receipt fields. The verifier re-extracts every declaration, compares complete
canonical records (including types, states, reasons and references), and never
executes experiment code. Rebuilt manifests do not make contradictory observations
valid. Duration extraction reproduces the recorded number, not the historical
elapsed time. Repeated extraction of the same evidence is byte-identical; separate
executions can have different durations, timestamps and run IDs.

No scientific acceptance is inferred. An observed `answer = 9` is mechanically
valid. Output contents, exact text and JSON string measurements are user-controlled,
unredacted evidence and may contain private data. ProofLab-generated metadata and
fixed diagnostics do not inventory host paths, usernames, hostnames or environment
values. These privacy properties do not sanitize what an experiment emits.
