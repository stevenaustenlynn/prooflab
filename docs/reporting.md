# Verified Markdown reporting (Phase 5)

```sh
PYTHONPATH=src python3 -m prooflab report PACKAGE_DIRECTORY
PYTHONPATH=src python3 -m prooflab report PACKAGE_DIRECTORY --output new-report.md
```

The command supports verified run and comparison packages. It reads retained bytes
only: no experiments, evaluator subprocesses, dynamic retained-code imports,
network, prompts, animation or ANSI styling. The five public commands remain
`init`, `run`, `compare`, `report`, `verify` (all IMPLEMENTED).

Markdown goes to stdout; diagnostics go to stderr. With `--output`, stdout is
empty on success. The output must be a new external file with an existing parent.
Existing files, directories and symlinks are refused without replacement or append.
Destinations inside the input package, including aliases through symlinked parents,
are rejected. No missing directories are created. Input evidence is never modified,
repaired, resealed or supplemented. A write failure can leave an incomplete newly
created external report; it returns 2 and never overwrites an existing destination.

| Report exit | Meaning |
| --- | --- |
| 0 | Verified evidence rendered successfully, including operational failure, scientific FAIL/INCONCLUSIVE, or incompatible comparison inputs. |
| 1 | INVALID evidence, including a detected change during reporting. No trusted report. |
| 2 | Invalid invocation, output destination or write failure. |
| 3 | INCOMPLETE evidence. No trusted report. |
| 4 | UNSUPPORTED evidence. No trusted report. |

Init exits are documented in [initialization](initialization.md). Report success is not scientific
acceptance. For example, compare's scientific FAIL returns 1, but reporting that
intact comparison returns 0. Initial input verification takes precedence over
output-destination checks.

## Verification and identity

The authoritative verifier runs before records are presented. Every reread record
must match its accepted manifest size and SHA-256. Existing strict JSON/schema
loaders and comparison source-record interfaces are reused. The renderer consumes
verified outcomes; it does not implement policy evaluation or aggregation.
A final verifier call pins the original manifest digest and checks the complete
tree before returning the in-memory Markdown. Detected changes abort with INVALID.
Neither stored SEALED/VERIFIED strings nor run acceptance labels establish trust.

Keep the directory stable. The checks do not provide an atomic snapshot or protect
against adversarial filesystem races; output parents must also remain stable.
A local sidecar is not an independent trust anchor. No acceptance rules or schemas
were relaxed for rendering. An empty compatible trial population is not supported
by v1 (at least one case and positive trials are required). Incompatible comparisons
can retain zero pairs; entirely inapplicable pairs retain their verified outcomes.

For comparisons, original source packages are **not rechecked**. Retained manifests
and source record excerpts bind the operands but do not include all original
materials/outputs. Complete source lineage requires complete original packages and
separate verification against their retained manifest digests. No baseline truth,
historical execution, causality, significance or universal superiority is inferred.

## Contents

Run reports include identity/digests, variant, distinct case/trial counts, every
supported execution state, receipt observations, declared output coverage and
individual observations, typed measurements with units/reasons, artifact IDs,
package-relative paths and digests. Required-output absences, optional absences,
capture errors and measurement errors appear in the summary. Missing values remain
unavailable. Legacy execution, output and measurement fields are explicitly marked
not recorded. Legacy acceptance labels are not promoted into scientific findings.
Timing is a retained operational observation, not an established performance result.
Provenance scope and declared omissions are identified without promoting arbitrary
provenance environment/Git assertions to verified facts.

Comparison reports include explicit baseline/candidate identities, source digests,
original/normalized policy digests, compatibility checks, expected cases/trials and
paired populations, the verified overall result, required/optional rule outcomes,
thresholds where applicable, and every pair's operands, availability and result.
Denominators/counts include all outcomes. Optional failure remains visible and does
not become a required failure. Incompatible inputs have no scientific verdict.

## Deterministic display

Identical package bytes produce identical UTF-8 Markdown across repeated rendering
and relocation. No generation timestamp, current host identity, absolute input path,
average, percentage improvement, inferred score or new analysis is added. Retained
comparison creation time is labeled as a retained fact.

Every displayed string is limited to **256 Unicode code points before escaping**;
longer strings receive `[truncated after 256 code points]`. No rows are filtered or
truncated, so counts represent complete records. Numeric values use Python's
round-trip JSON spelling without display rounding; booleans stay distinct from
integers. Strings retain JSON quotes, empty units display as `""`, and null fields
say `not recorded / unavailable` alongside their state/reason/context.

User content appears only in code spans, disabling Markdown formatting and automatic
links. Backticks, table delimiters, HTML delimiters, Unicode control/format characters
and line separators use visible escapes; JSON escaping handles newlines and ASCII
controls. Logical paths are references, not generated clickable file URLs.
Source, stdout, stderr and output-file bytes are not dumped. String measurements and
descriptions can still contain sensitive content: **escaping is not secret redaction**.

## Actual retained examples

The Phase 5 demonstrations report existing Phase 4B evidence without rerunning it.
These exact excerpts are from reports recorded in the checkout-only
`docs/phase5-validation.md` ledger. Historical evidence is excluded from distributions:

```markdown
Operational state: `"completed"`.

**Output and measurement attention (all retained trials):**

- Required-output absences: `0`
- Optional-output absences: `0`
- Output capture errors: `0`
- Measurement errors: `1`
```

That operationally completed run retains invalid JSON, so its measurement reason is
`invalid_json`. Its report still exits 0. The scientific comparison excerpts are:

```markdown
Scientific result under retained policy: **PASS**.
Scientific result under retained policy: **FAIL**.
Scientific result under retained policy: **INCONCLUSIVE**.
```

These are three separate reports. The sum comparisons retain 10 versus 10 (PASS)
and 10 versus 9 (FAIL), with two trials each; the error comparison is INCONCLUSIVE.
These are retained observations and rule results, not benchmarks or efficiency claims.
