# What verification does and does not prove

`VERIFIED` means that, during this check, the supported records were strict
JSON, the manifest and sidecar agreed, inventoried file sizes and SHA-256
digests matched, and the implemented path, reference, uniqueness, and planned
trial accounting rules passed. It covers all regular payload files in the
directory, not only those mentioned by a case. It never trusts a stored
`verified` label. The verifier performs no network requests and does not
import, evaluate, or execute code from the retained package.

This proves consistency of retained bytes with the supplied manifest, subject
to SHA-256's cryptographic assumptions. A local `manifest.sha256` proves only
consistency with that local sidecar. Someone able to replace a package can
replace both manifest and sidecar and obtain `VERIFIED` for the replacement.

An externally supplied `--expect-sha256` binds the check to a particular
manifest identity. Its value must come through an independently trusted
channel to provide an independent anchor. This is not a signature system;
ProofLab does not authenticate the digest provider or establish authorship.

Verification does **not** prove:

- Scientific correctness, sound experimental design, fair baselines, appropriate
  acceptance rules, statistical significance, or validity of conclusions.
- That an execution status or acceptance decision is historically true. Phase
  4A built-in measurements are recomputed from retained evidence, but this proves
  extraction consistency, not scientific meaning. Legacy Phase 1 measurements
  and acceptance decisions are not recomputed.
- That code ran at all, ran on the claimed inputs or environment, or can be
  reproduced. The Phase 3 runner executes verified material copies and records limited
  provenance. Offline checks bind receipts to snapshot identities but cannot
  establish the historical truth that those bytes were actually used.
- That every scientifically relevant input/output was retained. Inventory is
  complete relative to the directory and declared records, not to reality.
- That absent metrics equal zero, or that execution failure implies scientific
  failure. A mechanically sound failed experiment can verify successfully.
- Authenticity, chain of custody, timestamps, authorship, or resistance to a
  coordinated replacement of payload, manifest, and sidecar.
- Safety of executing the retained code later. ProofLab is not a sandbox or
  malware scanner; opaque artifacts can contain arbitrary bytes.
- Filesystem metadata, extended attributes, hard-link independence, or a
  transactionally consistent snapshot while another process mutates files.

Verify a stable local directory. The reader rejects observed symlinks and
nonregular files and checks containment, but it is not a security boundary
against an adversary concurrently replacing directories or files. Reads are
not an atomic filesystem snapshot. Parsed JSON records are read into memory;
artifact hashes are streamed. There is no resource sandbox or adversarial
size/time budget for untrusted packages. Hard-linked files are allowed and
must also remain unchanged during verification.

Verification does not extract archives, repair packages, update stored
integrity, run experiments, or write into the evidence tree. Unknown versions
remain `UNSUPPORTED`, missing evidence remains `INCOMPLETE`, and detected
contradictions remain `INVALID`; none is silently upgraded to `VERIFIED`.


The runner is not a security sandbox. Experiment code can perform arbitrary
operations available to the invoking user. `shell=False` prevents ProofLab from
introducing shell interpretation, but the selected executable may itself be an
interpreter, shell, or unsafe program. Inherited environment values remain
accessible to that program. External executable binaries and installed libraries
are outside captured material identity; execution is not hermetic.

Fresh trial directories prevent ordinary workspace state carrying into the next
trial. They do not isolate global files, services, environment, or other host
state that experiment code deliberately accesses. Exact source, definition and
stdout/stderr and declared output bytes may include private values emitted by the program or supplied
by its author. Generated records avoid host paths and environment dumps; opaque
payloads are not sanitized. Review evidence before sharing it.

POSIX process-group cleanup does not contain descendants that detach into new
sessions/groups. Other platforms terminate only the direct child. Escaping
processes can outlive the trial and mutate files or retained output streams.
Keep evidence quiescent for verification. A successful check establishes
consistency during that check, not future immutability.


Phase 4A independently recomputes SHA-256, byte count, exact UTF-8 text, JSON scalar
extraction, receipt exit code and receipt duration. It rejects contradictory values,
types, parameters, references and reproducible content-error states even when manifest
hashes are rebuilt. It does not remeasure elapsed time or prove that a missing/unsafe
output observation is historically true. Recorded duration includes operational
runner overhead and can vary across executions; it is not a scientific benchmark.

A package with an accurately represented measurement error can verify. An observed
`answer = 9` can also verify. Run verification does not evaluate comparison rules. Phase 4B comparison
verification recomputes separately retained deterministic policy outcomes. External evaluator subprocesses and arbitrary Python plugins are not
implemented. Extraction uses standard-library code and no dynamic experiment imports.

Original output bytes are never normalized or redacted. Text/JSON string extraction
can copy sensitive user-produced content into measurement records; metadata privacy
does not guarantee that values are safe to share. Output capture and extraction read
file contents into memory and impose no adversarial resource budget. Work on a
quiescent tree; ordinary change checks do not protect against malicious filesystem
races or coordinated replacement of all evidence and its hashes.


Phase 4B comparison verification proves that retained operands and the retained policy
yield the retained compatibility, pair, rule and overall results. Source-record bytes
are checked against copied source manifests. Original outputs and material bytes are
not duplicated: full end-to-end lineage additionally requires retaining and verifying
the complete source packages with matching manifest digests. Standalone comparison
verification makes no claim that those packages are currently present. It does not
prove reference correctness, meaningful measurement, policy quality, candidate safety,
historical execution, causality, significance or commercial readiness. Source runs
are independently verified at comparison creation, never rerun or modified.
See [the comparison contract](comparison.md).


Phase 5 reporting first calls the authoritative verifier. It binds every reread
record to the exact accepted manifest, renders in memory, then verifies again
against that manifest digest before returning Markdown. A detected mutation aborts
reporting with INVALID and no trusted report. This is an ordinary change check,
not an atomic snapshot: keep input directories and output parents stable.
Reporting never repairs evidence, changes stored integrity, or reruns experiments.

Comparison reports verify the comparison package and its recomputed results only.
Original source packages are not rechecked. Copied source manifests and record
excerpts cannot replace complete original packages or independent verification of
their outputs and materials. Legacy measurements and acceptance labels are not
scientifically re-evaluated. A report's successful exit is not scientific acceptance.

Reports display user-authored measurement strings, policy descriptions and reasons.
Strings are escaped and bounded at 256 Unicode code points, with explicit truncation;
counts still cover complete records. Escaping prevents markup/terminal control
injection, but does not redact secrets. Reports omit opaque source, stdout, stderr
and output-file dumps. Provenance environment/Git assertions are inventory-bound,
not independently verified facts; reporting identifies that scope without reproducing
arbitrary provenance contents. No current host identity or generation time is added.
