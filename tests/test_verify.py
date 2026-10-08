import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from prooflab.cli import main
from prooflab.integrity import build_manifest, sha256_bytes
from prooflab.protocol import IntegrityState
from prooflab.records import canonical_json, strict_loads
from prooflab.verify import verify_package
from tests.support import ROLES, make_package, seal, write_json

ROOT = Path(__file__).resolve().parents[1]


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        make_package(self.root)

    def load(self, path):
        return strict_loads((self.root / path).read_bytes())

    def edit(self, path, mutate, reseal=True):
        data = self.load(path)
        mutate(data)
        write_json(self.root, path, data)
        if reseal:
            seal(self.root)

    def manifest(self, mutate):
        self.edit("manifest.json", mutate, reseal=False)
        self.sidecar()

    def sidecar(self):
        digest = sha256_bytes((self.root / "manifest.json").read_bytes())
        (self.root / "manifest.sha256").write_text(digest + "\n", encoding="ascii")

    def state(self, expected, message=None):
        result = verify_package(self.root)
        self.assertEqual(result.state, expected, result.findings)
        if message:
            self.assertTrue(any(message in finding.message for finding in result.findings), result.findings)
        return result

    def test_valid_minimal_package(self):
        self.state(IntegrityState.VERIFIED)

    def test_manifest_reproducibility(self):
        manifest, sidecar = build_manifest(self.root, dict(reversed(list(ROLES.items()))))
        self.assertEqual(manifest, (self.root / "manifest.json").read_bytes())
        self.assertEqual(sidecar, (self.root / "manifest.sha256").read_bytes())

    def test_tampered_payload(self):
        (self.root / "artifacts/result.txt").write_bytes(b"11\n")
        self.state(IntegrityState.INVALID, "SHA-256 mismatch")

    def test_size_mismatch(self):
        (self.root / "artifacts/result.txt").write_bytes(b"100\n")
        self.state(IntegrityState.INVALID, "size mismatch")

    def test_tampered_manifest(self):
        self.edit("manifest.json", lambda data: data["files"][0].update(size_bytes=99), False)
        self.state(IntegrityState.INVALID, "manifest.sha256")

    def test_incorrect_sidecar(self):
        (self.root / "manifest.sha256").write_bytes(b"0" * 64 + b"\n")
        self.state(IntegrityState.INVALID, "manifest.sha256")

    def test_missing_payload(self):
        (self.root / "artifacts/result.txt").unlink()
        self.state(IntegrityState.INCOMPLETE, "missing payload")

    def test_directory_in_place_of_payload(self):
        path = self.root / "artifacts/result.txt"
        path.unlink()
        path.mkdir()
        self.state(IntegrityState.INVALID, "listed payload is a directory")

    def test_directory_in_place_of_manifest(self):
        path = self.root / "manifest.json"
        path.unlink()
        path.mkdir()
        self.state(IntegrityState.INVALID, "required file is a directory")

    def test_missing_case_record(self):
        (self.root / "cases/small-1.json").unlink()
        self.state(IntegrityState.INCOMPLETE, "0 of 1")

    def test_missing_required_files(self):
        for name in ("manifest.json", "manifest.sha256", "protocol.json", "run.json"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                self.state(IntegrityState.INCOMPLETE, "missing required file")
                path.write_bytes(original)

    def test_unlisted_payload(self):
        (self.root / "extra.txt").write_bytes(b"extra")
        self.state(IntegrityState.INVALID, "unlisted payload")

    def test_duplicate_path(self):
        self.manifest(lambda data: data["files"].append(data["files"][0]))
        self.state(IntegrityState.INVALID, "duplicate path")

    def test_case_alias(self):
        self.manifest(lambda data: data["files"].append({**data["files"][0], "path": "Artifacts/result.txt"}))
        self.state(IntegrityState.INVALID, "collision")

    def test_duplicate_artifact_id(self):
        self.edit("run.json", lambda data: data["artifacts"].append(data["artifacts"][0]))
        self.state(IntegrityState.INVALID, "duplicate artifact ID")

    def test_duplicate_case_id(self):
        self.edit("protocol.json", lambda data: data["cases"].append(data["cases"][0]))
        self.state(IntegrityState.INVALID, "duplicate protocol case ID")

    def test_duplicate_record_id_and_trial(self):
        write_json(self.root, "cases/copy.json", self.load("cases/small-1.json"))
        self.edit("run.json", lambda data: data["case_records"].append("cases/copy.json"), False)
        seal(self.root, {**ROLES, "cases/copy.json": "case"})
        self.state(IntegrityState.INVALID, "duplicate case record ID")
        self.state(IntegrityState.INVALID, "duplicate case/trial")

    def test_absolute_path(self):
        self.manifest(lambda data: data["files"][0].update(path="/outside.txt"))
        self.state(IntegrityState.INVALID, "unsafe package path")

    def test_traversal(self):
        self.manifest(lambda data: data["files"][0].update(path="../outside.txt"))
        self.state(IntegrityState.INVALID, "unsafe package path")

    def test_unsafe_record_reference(self):
        self.edit("run.json", lambda data: data["artifacts"][0].update(path="../outside.txt"))
        self.state(IntegrityState.INVALID, "unsafe package path")

    def test_symlink_payload(self):
        path = self.root / "artifacts/result.txt"
        path.unlink()
        try:
            path.symlink_to("../run.json")
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.state(IntegrityState.INVALID, "symlink forbidden")

    def test_symlink_directory(self):
        try:
            (self.root / "linked").symlink_to("artifacts", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.state(IntegrityState.INVALID, "symlink forbidden")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_nonregular_payload(self):
        os.mkfifo(self.root / "pipe")
        self.state(IntegrityState.INVALID, "nonregular file")

    def test_malformed_json(self):
        (self.root / "run.json").write_bytes(b"{bad")
        seal(self.root)
        self.state(IntegrityState.INVALID, "run.json")

    def test_duplicate_json_key(self):
        raw = (self.root / "run.json").read_bytes()
        (self.root / "run.json").write_bytes(raw.replace(b'"id": "run-demo"', b'"id": "run-demo", "id": "run-demo"'))
        seal(self.root)
        self.state(IntegrityState.INVALID, "duplicate JSON key")

    def test_nonfinite_json(self):
        for literal in (b"NaN", b"Infinity", b"-Infinity", b"1e999"):
            with self.subTest(literal=literal):
                make_package(self.root)
                path = self.root / "cases/small-1.json"
                path.write_bytes(path.read_bytes().replace(b'"value": 10', b'"value": ' + literal))
                seal(self.root)
                self.state(IntegrityState.INVALID, "nonfinite")

    def test_unsupported_schema_versions(self):
        for path, kind in (("protocol.json", "protocol"), ("run.json", "run"),
                           ("cases/small-1.json", "case"), ("manifest.json", "manifest")):
            with self.subTest(kind=kind):
                make_package(self.root)
                if kind == "manifest":
                    self.manifest(lambda data: data.update(schema="prooflab.manifest/v2"))
                else:
                    self.edit(path, lambda data: data.update(schema=f"prooflab.{kind}/v2"))
                self.state(IntegrityState.UNSUPPORTED, "unsupported schema")

    def test_incomplete_case_accounting(self):
        self.edit("protocol.json", lambda data: data["cases"][0].update(trials=2))
        self.state(IntegrityState.INCOMPLETE, "1 of 2")

    def test_unplanned_trial(self):
        self.edit("cases/small-1.json", lambda data: data.update(trial=2))
        self.state(IntegrityState.INVALID, "unplanned case/trial")

    def test_valid_failed_experiment(self):
        make_package(self.root, failed=True)
        self.state(IntegrityState.VERIFIED)

    def test_execution_failure_independent_of_acceptance(self):
        self.edit("run.json", lambda data: data.update(execution_state="failed", acceptance_state="inconclusive"))
        self.edit("cases/small-1.json", lambda data: data.update(
            execution_state="failed", acceptance_state="inconclusive",
            measurements=[{"id": "sum", "state": "not_evaluated", "value": None}]))
        self.state(IntegrityState.VERIFIED)

    def test_missing_metric_is_not_zero(self):
        self.edit("cases/small-1.json", lambda data: data["measurements"][0].update(value=None))
        self.state(IntegrityState.INVALID, "numeric value exactly when observed")

    def test_duplicate_measurement_id(self):
        self.edit("cases/small-1.json", lambda data: data["measurements"].append(data["measurements"][0]))
        self.state(IntegrityState.INVALID, "duplicate measurement ID")

    def test_nonobserved_metric_cannot_have_value(self):
        self.edit("cases/small-1.json", lambda data: data["measurements"][0].update(state="error"))
        self.state(IntegrityState.INVALID, "numeric value exactly when observed")

    def test_unresolved_artifact_id(self):
        self.edit("cases/small-1.json", lambda data: data.update(artifact_ids=["missing"]))
        self.state(IntegrityState.INVALID, "unresolved artifact ID")

    def test_unresolved_path(self):
        self.edit("run.json", lambda data: data["artifacts"][0].update(path="artifacts/missing.txt"))
        self.state(IntegrityState.INVALID, "reference lacks manifest coverage")

    def test_wrong_role(self):
        self.manifest(lambda data: data["files"][-1].update(role="artifact"))
        self.state(IntegrityState.INVALID, "wrong manifest role")

    def test_manifest_cannot_list_itself_or_sidecar(self):
        for reserved in ("manifest.json", "manifest.sha256"):
            with self.subTest(reserved=reserved):
                make_package(self.root)
                self.manifest(lambda data: data["files"][0].update(path=reserved))
                self.state(IntegrityState.INVALID, "duplicate path")

    def test_nondeterministic_manifest(self):
        raw = (self.root / "manifest.json").read_bytes()
        (self.root / "manifest.json").write_bytes(raw + b"\n")
        self.sidecar()
        self.state(IntegrityState.INVALID, "deterministically encoded")

    def test_unsorted_manifest(self):
        self.manifest(lambda data: data["files"].reverse())
        self.state(IntegrityState.INVALID, "not sorted")

    def test_stored_integrity_is_not_trusted(self):
        self.edit("run.json", lambda data: data.update(integrity_state="verified"))
        (self.root / "artifacts/result.txt").write_bytes(b"99\n")
        self.state(IntegrityState.INVALID)

    def test_external_digest(self):
        digest = sha256_bytes((self.root / "manifest.json").read_bytes())
        self.assertEqual(verify_package(self.root, digest).state, IntegrityState.VERIFIED)
        self.assertEqual(verify_package(self.root, digest.upper()).state, IntegrityState.VERIFIED)

    def test_external_digest_mismatch(self):
        self.assertEqual(verify_package(self.root, "0" * 64).state, IntegrityState.INVALID)

    def test_external_anchor_detects_consistent_replacement(self):
        expected = sha256_bytes((self.root / "manifest.json").read_bytes())
        make_package(self.root, failed=True)
        self.state(IntegrityState.VERIFIED)
        self.assertEqual(verify_package(self.root, expected).state, IntegrityState.INVALID)

    def test_invalid_takes_precedence_over_unsupported(self):
        self.edit("run.json", lambda data: data.update(schema="prooflab.run/v2"))
        (self.root / "artifacts/result.txt").write_bytes(b"99\n")
        self.state(IntegrityState.INVALID, "SHA-256 mismatch")

    def test_bad_expected_digest(self):
        self.assertEqual(verify_package(self.root, "bad").state, IntegrityState.INVALID)

    def test_never_executes_retained_code_and_does_not_write(self):
        sentinel = self.root / "EXECUTED"
        script = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\nraise RuntimeError('executed')\n"
        (self.root / "artifacts/untrusted.py").write_text(script, encoding="utf-8")
        self.edit("run.json", lambda data: data["artifacts"].append(
            {"id": "code", "path": "artifacts/untrusted.py"}), False)
        seal(self.root, {**ROLES, "artifacts/untrusted.py": "artifact"})
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.state(IntegrityState.VERIFIED)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(sentinel.exists())

    def test_public_fixtures(self):
        for name, state in (("valid", IntegrityState.VERIFIED), ("tampered", IntegrityState.INVALID),
                            ("failed-experiment", IntegrityState.VERIFIED)):
            with self.subTest(name=name):
                self.assertEqual(verify_package(ROOT / "tests/fixtures" / name).state, state)

    def test_cli_exit_states(self):
        for name, code, state in (("valid", 0, "VERIFIED"), ("tampered", 1, "INVALID")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = main(["verify", str(ROOT / "tests/fixtures" / name)])
            self.assertEqual(result, code)
            self.assertEqual(out.getvalue().splitlines()[0], state)
        self.edit("protocol.json", lambda data: data["cases"][0].update(trials=2))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["verify", str(self.root)]), 3)
        self.edit("run.json", lambda data: data.update(schema="prooflab.run/v2"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["verify", str(self.root)]), 4)

    def test_init_conflict(self):
        (self.root / "README.md").write_bytes(b"unrelated content")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(main(["init", str(self.root)]), 2)
        self.assertIn("INIT_ERROR", output.getvalue())
        self.assertEqual((self.root / "README.md").read_bytes(), b"unrelated content")
