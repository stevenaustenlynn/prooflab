# Deterministic retained-run comparison (Phase 4B)

```sh
PYTHONPATH=src python3 -m prooflab compare BASELINE_RUN CANDIDATE_RUN --policy POLICY.toml
PYTHONPATH=src python3 -m prooflab verify COMPARISON_DIRECTORY
```

The first positional run is always baseline/reference; the second is candidate.
Names, variant labels, values, directories and timestamps never assign roles.
Comparison reads retained data only. It never reruns experiments, executes evaluator
files, imports package code, or modifies either input tree or the policy file.
Phase 6 adds [safe initialization](initialization.md). Phase 5 adds [verified Markdown reporting](reporting.md); there is no statistical layer.

## Flow and supported inputs

The command verifies baseline and candidate using the authoritative offline run
verifier, reads manifest-bound source records, checks compatibility and pairs stable
identities, then evaluates the separately parsed and identity-bound policy. The
policy-referenced definition checks necessarily follow policy parsing. No scientific
rule is evaluated until every compatibility check passes. Results are written into
a new comparison directory, manifested, and verified offline before `SEALED`.

INVALID, INCOMPLETE or UNSUPPORTED sources stop comparison before scientific
evaluation and before creating a package. Stored integrity or acceptance fields
are never used as proof of current verification. v1 comparison requires execution
identities and declared typed measurements (Phase 4A); legacy accounting-only
packages remain verifiable but are unsupported comparison inputs.

The files must remain quiescent during verification and comparison. Reads of copied
records are checked against the verified source manifest, and its digest must match
the just-verified digest. These are ordinary change checks, not an atomic filesystem
snapshot or protection against adversarial concurrent replacement.

## Conservative compatibility

Every check has a stable ID and boolean outcome in `compatibility.checks`:

- `schema_versions`: supported run and protocol identities agree.
- `experiment_id`: protocol ID agrees.
- `protocol_identity`: SHA-256 of the complete normalized protocol agrees.
- `case_identities`: the complete planned case ID sets agree.
- `trial_population`: every `(case_id, trial)` key agrees, with no subset selection.
- `trial_seed_semantics`: complete case bindings agree, including arguments, input
  references, and base seed. Individual receipt seeds must be base + trial - 1,
  or null when no seed was declared.
- `input_identities` and `evaluator_identities`: all captured materials with those
  roles agree in ID, logical path, role, size and SHA-256.
- `measurement.<id>.definition`, `.type`, `.unit`: each referenced definition,
  exact value type, and literal unit label agree.

The full protocol check is intentionally stronger than the detailed checks. It
includes case declaration order, all variant commands, output declarations,
measurement declarations, material declarations/exclusions, timeout and bindings.
Selected variants, actual selected argv, source byte hashes, source sizes, run IDs,
timing, receipts, output bytes, and measurement values may differ. Captured source
bytes are outside normalized protocol identity, so implementation changes do not
require weakening that identity. Changed command declarations require new compatible
runs under the changed protocol. No unit conversion or type coercion is performed.

Both missing referenced definitions are an invalid policy (unknown measurement).
A definition present on only one side is incompatibility. Any failed compatibility
check yields `INCOMPATIBLE`, zero evaluated pairs, no scientific rule verdicts, and
exit 2. The command retains and verifies the incompatibility evidence package; it
never labels incompatible runs scientifically PASS or FAIL. Failed check IDs and the
retained source records explain the differences.

## Pairing and policy

Pairs use case-sensitive case ID plus the 1-based integer trial index. Evidence sorts
by rule ID, then case ID and trial number. Array position is never a pairing key.
Every expected pair/rule relationship is retained; duplicate or missing trial records
prevent source verification or comparison accounting. No surviving subset is used.

```toml
schema = "prooflab.comparison-policy/v1"
id = "answer-agreement"
description = "Compare each candidate answer with the selected reference."

[[rules]]
id = "answer-equal"
measurement = "answer"
operator = "equal"
required = true
```

The schema is closed and versioned. A policy has a logical ID, optional description,
and at least one rule. Rules have unique logical IDs, measurement ID, operator,
`required` (default true), optional description, and a threshold only for delta
operators. Descriptions default to empty strings; rules normalize in ID order.
`schemas/comparison-policy-v1.schema.json` describes the normalized JSON form with
these defaults present. Runtime semantic validation additionally enforces uniqueness,
operator/threshold relationships, and nonnegative absolute tolerance.

The package retains exact original TOML in `policy.toml` and canonical normalized
JSON in `policy.json`. `policy.sha256` hashes the original bytes;
`policy.normalized_sha256` hashes canonical normalized semantics. Comments and
whitespace can change the former without changing the latter. Changing a rule,
threshold, measurement, operator or required flag changes normalized identity.
No expressions, `eval`, plugins, environment policy overrides or arbitrary code exist.
Destination selection has no influence on scientific results.

## Operators and numbers

| Operator | Deterministic condition | Threshold |
| --- | --- | --- |
| `equal` | candidate == baseline, with identical declared scalar types | forbidden |
| `candidate_gte_baseline` | candidate >= baseline | forbidden |
| `candidate_lte_baseline` | candidate <= baseline | forbidden |
| `delta_gte` | candidate - baseline >= threshold | required, finite number |
| `delta_lte` | candidate - baseline <= threshold | required, finite number |
| `abs_delta_lte` | abs(candidate - baseline) <= threshold | required, finite nonnegative number |

Equality supports integer, finite float, boolean and exact string values. All other
operators require integer or finite float measurement definitions. Thresholds may be
integers or finite floats; booleans and strings are rejected. No operand coercion:
integer 1, float 1.0, boolean true and string "1" have different measurement types.
Comparisons use Python's standard integer and finite binary floating representation.
Float equality uses numeric equality: +0.0 equals -0.0; 0.1 + 0.2 need not equal 0.3.
Exact retained JSON still distinguishes their byte representations and types.

Integer subtraction remains integer arithmetic. Floating subtraction uses the retained
binary floating representation; if subtraction becomes nonfinite, the pair is
INCONCLUSIVE with `nonfinite_delta`. ProofLab claims deterministic comparison over
these representations, not arbitrary-precision scientific arithmetic. No Decimal,
third-party runtime library, ratio, percentage, formula or approximation is added.

## States and aggregation

An operand retains its state, value and reason. Unavailable values remain null.

| Operand population | Pair result |
| --- | --- |
| Both observed and compatible | PASS or FAIL from the declared operator |
| Error, not_evaluated, or missing observation on either side | INCONCLUSIVE |
| Both explicitly not_applicable | NOT_APPLICABLE |
| Only one not_applicable | INCONCLUSIVE |
| Nonfinite floating subtraction | INCONCLUSIVE |

`missing` is a comparison operand state with reason `expected_measurement_absent`.
This defensive accounting does not loosen source verification: an omitted Phase 4A
measurement currently makes a complete execution package invalid and blocks CLI
comparison. Explicit extraction errors and unstarted trials can verify and compare.
`not_applicable` denotes source availability under its declaration, not a scientific
claim of irrelevance. Only bilateral explicit inapplicability excludes a pair from
a rule's applicable population.

For each rule, any FAIL dominates; otherwise any INCONCLUSIVE dominates. Otherwise
at least one PASS and only PASS/NOT_APPLICABLE pairs yields PASS. No applicable pairs
yields NOT_APPLICABLE, never PASS. Counts for all four states are retained.

Overall uses only required rules. Any required FAIL yields FAIL. All required rules
must PASS, and at least one required rule must exist, for overall PASS. All other
compatible cases yield INCONCLUSIVE, including required NOT_APPLICABLE and policies
containing only optional rules. Optional failures remain visible but cannot fail the
overall result. There is no majority voting, statistical aggregation or implicit
winner selection.

## Package and verification

By default a unique directory is created under the invoking working directory:

```text
.prooflab/comparisons/comparison-<uuid>/
  comparison.json
  policy.toml
  policy.json
  sources/baseline/manifest.json
  sources/baseline/protocol.json
  sources/baseline/run.json
  sources/baseline/snapshot.json
  sources/baseline/cases/trial-000001.json
  ... all baseline trial receipts
  sources/candidate/... same source-record subset
  manifest.json
  manifest.sha256
```

Source snapshot and case paths preserve their original logical paths (the layout
above shows runner defaults). Only original manifests, protocol/run/snapshot records
and complete case receipts are copied. Material, output, stream, plan, original
experiment definition and provenance payloads are not copied. The subset is not
itself a complete run package.

`comparison.json` uses `prooflab.comparison/v1` and contains the comparison ID,
creation timestamp, explicit baseline/candidate IDs and exact source manifest digests,
policy ID and both digests, compatibility checks, paired trial count, operands and
pair outcomes/reasons, rule outcomes/counts, and overall verdict. No absolute source
location is stored. This single record holds compatibility, pair and summary evidence
without redundant separate ledgers.

The unreleased manifest v1 adds an optional closed `package_type` field (`run` or
`comparison`; omission preserves legacy run semantics) and `comparison` record role.
Comparison manifests explicitly declare their type; filenames are not dispatch
heuristics. Only `comparison.json` has role `comparison`; all other comparison
payloads are artifacts. The existing run verifier still requires its complete roles,
records, references and extraction checks. New schemas are published alongside it.

Offline comparison verification checks the ordinary complete package inventory,
portable unique paths, manifest/sidecar hashes, supported closed schemas, policy
normalization and identity, source digest shape and exact retained manifest bytes,
source record membership/hashes, supported source record relationships, complete
trial accounting, types, definitions, compatibility, every pair, every rule aggregate
and the overall verdict. It reconstructs the comparison record and compares canonical
bytes. It never trusts stored outcomes. Altering a copied source operand and rebuilding
only the comparison manifest also fails its binding to the retained source manifest.

A self-contained comparison package proves that its retained operands and policy
deterministically yield its retained verdict. It does not claim the complete source
packages are currently present, or independently re-extract measurements from absent
output payloads. Full end-to-end lineage additionally depends on retaining original
run packages whose manifest digests match the recorded baseline/candidate identities,
and verifying those packages. The command performs that full verification at creation;
standalone comparison verification cannot prove that historical action occurred.

FAIL and INCONCLUSIVE packages can both be mechanically VERIFIED (verify exit 0).
A scientific failure is not an integrity failure. Hashes do not authenticate evidence:
coordinated replacement of records and all corresponding digests is outside the trust
model unless a trusted external manifest digest is supplied to verification.

## Exits and determinism

| Compare exit | Meaning |
| --- | --- |
| 0 | Sealed, overall PASS |
| 1 | Sealed, overall FAIL |
| 2 | Invalid invocation/policy, invalid or unsupported sources, unavailable destination before creation, or retained INCOMPATIBLE comparison |
| 3 | Sealed, overall INCONCLUSIVE |
| 4 | Comparison evidence assembly, integrity or sealing failure after directory creation |

Repeated comparison of identical retained inputs and policy has identical policy
identities, compatibility, paired operands, results, aggregations and verdict.
Only comparison instance ID and UTC creation timestamp vary; consequently comparison
record bytes, directory name and outer manifest identity vary. Original source records
and policy bytes remain exact. Destination paths are printed locally, not serialized
as source identity.

The result establishes agreement/disagreement with a declared reference under declared
rules. It does not establish baseline correctness, candidate safety, meaningful
measurement, historical execution, good policy, causality, significance or commercial
readiness. Retained descriptions, original policy bytes and string measurements are
user-controlled and unredacted; metadata portability cannot guarantee content privacy.

No mean, median, variance, confidence interval, significance, outlier rejection,
optimization, automatic candidate selection, AI judgment or re-execution
is implemented. Future layers can use retained pair evidence without changing it.
