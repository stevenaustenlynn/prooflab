# Contributing

Use Python 3.11 or later. Runtime code should remain standard-library-only
unless a concrete requirement justifies a dependency. Keep retained records
explicit, paths portable, and failures actionable. Never introduce package
code execution into verification, reporting, or comparison.

Run from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -c 'import ast; from pathlib import Path; [ast.parse(p.read_bytes(), filename=str(p)) for p in [*Path("src/prooflab").glob("*.py"), *Path("tests").glob("test_*.py")]]'
PYTHONPATH=src python3 -m prooflab --help
```

Tests use isolated temporary destinations and remove them on completion.
Never recursively compile retained evidence or import its source artifacts. The public fixtures are synthetic and intentionally small. To
regenerate their exact bytes after an intentional format change:

```sh
PYTHONPATH=src python3 -m tests.build_fixtures
```

`src/prooflab/schema.py` defines the closed v1 structural vocabulary. The
published `schemas/*-v1.schema.json` files must match it byte for byte; a test
enforces this. Generate snapshots with `canonical_json(SCHEMAS[kind])` when
deliberately changing an unpublished format. Once released, incompatible
changes require a new public schema version. Relationship and portable-path
checks are additional verifier rules documented in `docs/evidence-format.md`.

New tests and examples must be independently authored public material. Do not
copy private source, corpora, measurements, or evidence. Keep integrity claims
separate from acceptance decisions. Execute public examples only through the runner
in isolated tests/demos; never import or execute retained evidence during verification.

The authoritative starter lives in `src/prooflab/starter/`. Keep its explicit package
resource allowlist and public `examples/sum` mirror synchronized. Tests enforce exact
bytes for all five files. Do not add generated evidence or bytecode to resources.
