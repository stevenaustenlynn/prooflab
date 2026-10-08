# Sum starter

This public toy experiment sums integers 1 through 4. `small.txt` contains 4.
Iterative accumulation and the arithmetic formula produce 10; the intentionally
incorrect formula produces 9. Each variant runs two trials and exits successfully.
The runner records operational completion; the separate equality policy decides
agreement with the selected reference.

With a locally installed ProofLab command and Python 3.11+ (`python3` on PATH):

```sh
prooflab init sum-project
cd sum-project
prooflab run experiment.toml --variant iterative
prooflab run experiment.toml --variant formula
prooflab run experiment.toml --variant incorrect
```

If reading this inside an already initialized project, start with the three
`run` commands. No installation, execution, Git initialization or evidence
creation happens during `init`.

Each run prints `run_id` and `evidence_directory`. Copy the returned directory
paths into the commands below: `ITERATIVE_RUN`, `FORMULA_RUN`, and `INCORRECT_RUN`
are placeholders, not literal filenames. All three runs should seal (exit 0).

```sh
prooflab verify ITERATIVE_RUN
prooflab verify FORMULA_RUN
prooflab verify INCORRECT_RUN
prooflab compare ITERATIVE_RUN FORMULA_RUN --policy comparison-policy.toml
prooflab compare ITERATIVE_RUN INCORRECT_RUN --policy comparison-policy.toml
```

All three verifications return VERIFIED (exit 0). The first comparison gives
PASS (exit 0); the second gives FAIL (exit 1). **Compare exit 1 is the expected
scientific FAIL for the incorrect variant, not a broken installation.** If using
a shell with exit-on-error, handle that expected exit explicitly before continuing.
Each comparison prints `comparison_id` and `evidence_directory`. Replace
`PASS_COMPARISON` and `FAIL_COMPARISON` below with those returned paths.

```sh
prooflab verify PASS_COMPARISON
prooflab verify FAIL_COMPARISON
prooflab report PASS_COMPARISON --output pass-report.md
prooflab report FAIL_COMPARISON --output fail-report.md
```

Both comparisons mechanically verify (exit 0). Both reports return 0: **report
exit 0 means rendering succeeded**, including when the scientific verdict is FAIL.
Omit `--output` for Markdown on stdout. Report files must be new and outside the
evidence packages. Compare and report leave their source packages unchanged.

The exact initialized tree is:

```text
sum-project/
  README.md
  comparison-policy.toml
  experiment.toml
  implementations.py
  small.txt
```

`init` with no directory uses the current directory. A new destination requires
an existing parent. Unrelated files may coexist, but any collision with these
five names (including case aliases, directories, symlinks or special files)
refuses the whole preflight without writing. Existing files are never overwritten;
there is no force option. Success returns 0; invalid arguments, conflicts or
creation failures return 2. Mid-write failures list partial creations for inspection;
init does not delete them because concurrent edits cannot safely be rolled back.
Keep destination directories stable. This is not a filesystem transaction or
power-loss-safe operation. Safe directory-handle support is required; unsupported
platforms refuse initialization. WSL is the validated environment.

The experiment and all declared source/input paths live in this project.
Later runs store evidence under `.prooflab/runs/` beside `experiment.toml`.
Comparisons store evidence under the invoking directory's `.prooflab/comparisons/`.
Declared `out/result.json` files are created in isolated trial workspaces and
retained under each run's `outputs/`. The JSON extractor records the typed integer
`answer` in `cases/trial-NNNNNN.json`. Exact source, inputs and output artifacts
are **unredacted**. Review evidence before sharing.

Verification establishes supported byte and record consistency. It does not
establish historical execution, authenticity, or mathematical truth. Agreement
with this reference under this policy is not a general correctness proof.
ProofLab is not a security sandbox; experiment programs have the invoking user's
permissions and inherited environment. This starter needs no network or third-party
runtime dependencies.

For development-checkout use, follow the repository README's separate instructions.
Installed usage does not require the development checkout. These instructions do
not claim a publicly released package or direct installation from a public registry.
