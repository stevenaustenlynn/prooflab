"""Real serial subprocess tests, with bounded fault injection at lifecycle seams."""

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from prooflab.cli import main
from prooflab.ingestion import parse_protocol
from prooflab.integrity import build_manifest, file_identity, sha256_bytes
from prooflab.records import canonical_json, strict_loads
from prooflab.runner import run_experiment
from prooflab.schema import SchemaError
from prooflab.verify import VerificationResult, verify_package
from prooflab.protocol import IntegrityState
import prooflab.runner as runner
from tests.support import write_json

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = Path(__file__).with_name("execution_program.py").read_bytes()


def definition(cases=(("first", 1, "ok"),), *, timeout=None, args=(), seed=None, argv=None):
    text = 'schema = "prooflab.protocol/v1"\nid = "execution-demo"\nvariants = ["only"]\n'
    for name, trials, mode in cases:
        text += f'[[cases]]\nid = "{name}"\ntrials = {trials}\n'
    text += '[execution]\n'
    if timeout is not None:
        text += f'timeout_seconds = {timeout}\n'
    text += '[[execution.commands]]\nvariant = "only"\n'
    text += 'argv = ' + json.dumps(argv or ['python3', 'program.py']) + '\nsource_ids = ["program"]\n'
    text += '[[execution.materials]]\nid = "program"\npath = "program.py"\nrole = "source"\n'
    text += '[[execution.materials]]\nid = "input"\npath = "input.txt"\nrole = "input"\n'
    for name, trials, mode in cases:
        text += f'[[execution.bindings]]\ncase_id = "{name}"\ninput_ids = ["input"]\n'
        text += 'args = ' + json.dumps([mode, *args]) + '\n'
        if seed is not None:
            text += f'seed = {seed}\n'
    return text.encode()


def make_project(root, **kwargs):
    (root / "experiment.toml").write_bytes(definition(**kwargs))
    (root / "program.py").write_bytes(PROGRAM)
    (root / "input.txt").write_bytes(b"captured input\n")
    return root / "experiment.toml"


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="prooflab-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = make_project(self.root)

    def run_case(self, **kwargs):
        make_project(self.root, **kwargs)
        result = run_experiment(self.path)
        self.assertTrue(result.sealed, result)
        verified = verify_package(result.directory)
        self.assertEqual(verified.state, IntegrityState.VERIFIED, verified.findings)
        return result

    def load(self, result, relative):
        return strict_loads((result.directory / relative).read_bytes())

    def receipts(self, result):
        return [self.load(result, p) for p in self.load(result, "run.json")["case_records"]]

    def output(self, result, number=1, stream="stdout"):
        return (result.directory / f"artifacts/trial-{number:06d}.{stream}").read_bytes()

    def reseal(self, result):
        manifest = self.load(result, "manifest.json")
        data, sidecar = build_manifest(result.directory, {f["path"]: f["role"] for f in manifest["files"]})
        (result.directory / "manifest.json").write_bytes(data)
        (result.directory / "manifest.sha256").write_bytes(sidecar)

    def test_success_single_and_offline_verify_never_executes(self):
        result = self.run_case()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.counts["completed"], 1)
        with patch("subprocess.Popen", side_effect=AssertionError("offline only")):
            self.assertEqual(verify_package(result.directory).state, IntegrityState.VERIFIED)

    def test_multiple_cases_repeats_order_and_case_sensitive_ids(self):
        result = self.run_case(cases=(("Z", 2, "ok"), ("z", 1, "ok"), ("a", 2, "ok")))
        self.assertEqual([(r["case_id"], r["trial"]) for r in self.receipts(result)],
                         [("Z", 1), ("Z", 2), ("z", 1), ("a", 1), ("a", 2)])
        receipts = self.receipts(result)
        for before, after in zip(receipts, receipts[1:]):
            self.assertLessEqual(before["execution"]["ended_at"], after["execution"]["started_at"])

    def test_snapshot_hashes_roles_and_definition(self):
        result = self.run_case()
        snapshot = self.load(result, "snapshot.json")
        self.assertEqual({m["role"] for m in snapshot["materials"]}, {"input", "source"})
        for material in snapshot["materials"]:
            self.assertEqual(file_identity(result.directory, "snapshot/" + material["path"]),
                             (material["size_bytes"], material["sha256"]))
        self.assertEqual((result.directory / "definition/experiment.toml").read_bytes(), self.path.read_bytes())

    def test_change_before_snapshot_prevents_launch(self):
        original = runner.capture_snapshot
        def capture(root, package, plan):
            (root / "program.py").write_bytes(b'print("changed")\n')
            return original(root, package, plan)
        with patch.object(runner, "capture_snapshot", side_effect=capture), patch.object(runner, "execute_trial") as launch:
            result = run_experiment(self.path)
        launch.assert_not_called()
        self.assertFalse(result.sealed)
        self.assertEqual(result.exit_code, 4)
        self.assertTrue((result.directory / "plan.json").exists())
        self.assertEqual(verify_package(result.directory).state, IntegrityState.INCOMPLETE)

    def test_definition_change_before_snapshot_prevents_launch(self):
        original = runner.capture_snapshot
        def capture(root, package, plan):
            self.path.write_bytes(self.path.read_bytes() + b"# changed\n")
            return original(root, package, plan)
        with patch.object(runner, "capture_snapshot", side_effect=capture), patch.object(runner, "execute_trial") as launch:
            result = run_experiment(self.path)
        launch.assert_not_called()
        self.assertFalse(result.sealed)

    def test_live_mutation_after_snapshot_does_not_change_trials(self):
        original = runner.capture_snapshot
        def capture(root, package, plan):
            snapshot = original(root, package, plan)
            (root / "program.py").write_bytes(b'raise RuntimeError("live mutated")\n')
            (root / "input.txt").write_bytes(b"live mutation")
            return snapshot
        with patch.object(runner, "capture_snapshot", side_effect=capture):
            result = self.run_case(cases=(("clean", 2, "clean"),))
        self.assertEqual(result.counts["completed"], 2)
        self.assertEqual(self.output(result, 2), b"clean\n")

    def test_corrupt_snapshot_copy_prevents_execution(self):
        import prooflab.snapshot as snapshot
        def corrupt(source, target, length):
            target.write(b"wrong bytes")
        with patch.object(snapshot.shutil, "copyfileobj", side_effect=corrupt), patch.object(runner, "execute_trial") as launch:
            result = run_experiment(self.path)
        launch.assert_not_called()
        self.assertFalse(result.sealed)

    def test_snapshot_changed_before_workspace_copy_prevents_launch(self):
        original = runner.populate_workspace
        def populate(package, workspace, snapshot):
            (package / "snapshot/program.py").write_bytes(b"bad")
            return original(package, workspace, snapshot)
        with patch.object(runner, "populate_workspace", side_effect=populate):
            result = run_experiment(self.path)
        self.assertFalse(result.sealed)
        self.assertEqual(result.counts["completed"], 0)

    def test_workspace_starts_clean_and_undeclared_outputs_not_retained(self):
        result = self.run_case(cases=(("clean", 3, "clean"),))
        self.assertEqual(result.counts["completed"], 3)
        self.assertEqual((self.root / "input.txt").read_bytes(), b"captured input\n")
        self.assertFalse(list(result.directory.rglob("created.txt")))

    def test_literal_metacharacters_and_no_shell_injection(self):
        args = ['two words', '"quotes"', "'quote'", '; touch INJECTED', '& touch INJECTED',
                '| touch INJECTED', '>INJECTED', '<input.txt', '*', '$HOME', '$(touch INJECTED)']
        original = runner.populate_workspace
        workspaces = []
        def populate(package, workspace, snapshot):
            workspaces.append(workspace)
            return original(package, workspace, snapshot)
        real = subprocess.Popen
        calls = []
        def popen(argv, **kwargs):
            if "program.py" in argv:
                calls.append((argv, kwargs))
                self.assertFalse(kwargs["shell"])
                self.assertNotEqual(kwargs["cwd"], self.root)
            return real(argv, **kwargs)
        with patch.object(runner, "populate_workspace", side_effect=populate), patch("subprocess.Popen", side_effect=popen):
            result = self.run_case(cases=(("literal", 1, "args"),), args=args)
        self.assertEqual(json.loads(self.output(result)), args)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.root / "INJECTED").exists())
        self.assertFalse(any(p.exists() for p in workspaces))

    def test_stdout_stderr_exact_non_utf8_and_manifest_hashes(self):
        result = self.run_case(cases=(("bytes", 1, "bytes"),))
        self.assertEqual(self.output(result), b"stdout\x00\xff\n")
        self.assertEqual(self.output(result, stream="stderr"), b"stderr\xfe\x00\n")
        entries = {f["path"]: f for f in self.load(result, "manifest.json")["files"]}
        for stream in ("stdout", "stderr"):
            data = self.output(result, stream=stream)
            item = entries[f"artifacts/trial-000001.{stream}"]
            self.assertEqual((item["size_bytes"], item["sha256"]), (len(data), sha256_bytes(data)))

    def test_nonzero_exit_seals_and_later_trial_runs(self):
        result = self.run_case(cases=(("fail", 1, "fail"), ("later", 1, "ok")))
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.counts["failed"], 1)
        self.assertEqual(self.receipts(result)[0]["execution"]["exit_code"], 7)
        self.assertEqual(result.counts["completed"], 1)

    def test_missing_executable_is_failed_and_sealable(self):
        result = self.run_case(argv=["prooflab-nonexistent-test-tool-abcdef"])
        receipt = self.receipts(result)[0]
        self.assertEqual(receipt["execution"]["reason"], "launch_failed")
        self.assertIsNone(receipt["execution"]["exit_code"])
        self.assertEqual(result.exit_code, 3)

    def test_timeout_preserves_partial_streams_and_continues(self):
        result = self.run_case(cases=(("slow", 1, "timeout"), ("later", 1, "ok")), timeout=1)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.counts["timed_out"], 1)
        self.assertEqual(result.counts["completed"], 1)
        self.assertEqual(self.output(result), b"before timeout\xff\n")
        self.assertEqual(self.output(result, stream="stderr"), b"partial stderr\xfe\n")

    @unittest.skipUnless(os.name == "posix", "POSIX signal escalation")
    def test_timeout_escalates_when_sigterm_ignored(self):
        result = self.run_case(cases=(("slow", 1, "ignore-term"),), timeout=1)
        self.assertEqual(self.receipts(result)[0]["execution"]["exit_code"], -signal.SIGKILL)

    def test_timeout_validation_and_absent_identity_compatibility(self):
        for invalid in (0, -1, 86401, 10 ** 400, "true", '"1"', "0.1", "inf"):
            with self.subTest(invalid=invalid), self.assertRaises(SchemaError):
                parse_protocol(definition(timeout=invalid))
        self.assertNotIn("timeout_seconds", self.load(self.run_case(), "protocol.json")["execution"])

    def test_interrupt_active_trial_preserves_prior_receipt_and_skips_rest(self):
        original = subprocess.Popen.wait
        interrupted = False
        def wait(process, timeout=None):
            nonlocal interrupted
            if isinstance(process.args, list) and process.args[-1] == "timeout" and not interrupted:
                # Wait for file-backed output before injecting a normal interrupt.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    packages = list(self.root.glob(".prooflab/runs/*/artifacts/trial-000002.stdout"))
                    if packages and packages[0].stat().st_size:
                        break
                    time.sleep(0.01)
                interrupted = True
                raise KeyboardInterrupt
            return original(process, timeout=timeout)
        with patch.object(subprocess.Popen, "wait", wait):
            result = self.run_case(cases=(("prior", 1, "ok"), ("active", 1, "timeout"), ("rest", 2, "ok")))
        self.assertEqual(result.exit_code, 3)
        self.assertEqual([r["execution_state"] for r in self.receipts(result)],
                         ["completed", "interrupted", "skipped", "skipped"])
        self.assertEqual(self.output(result), b"ok\n")
        self.assertEqual(self.output(result, 2), b"before timeout\xff\n")

    def test_interrupt_between_trials_keeps_completed_evidence(self):
        original = runner.write_record
        interrupted = False
        def write(path, record, kind=None):
            nonlocal interrupted
            if kind == "case" and record["execution_state"] == "completed" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(path, record, kind)
        with patch.object(runner, "write_record", side_effect=write):
            result = self.run_case(cases=(("work", 3, "ok"),))
        self.assertEqual([r["execution_state"] for r in self.receipts(result)], ["completed", "skipped", "skipped"])
        self.assertEqual(result.exit_code, 3)

    def test_portable_generated_records_no_host_path_or_environment_values(self):
        with patch.dict(os.environ, {"PROOFLAB_TEST_SECRET": "private-test-value"}):
            result = self.run_case(cases=(("env", 1, "environment"),))
        self.assertEqual(self.output(result), b"present\n")
        for path in result.directory.rglob("*.json"):
            data = path.read_bytes()
            for forbidden in (str(self.root).encode(), b"/home/", b"prooflab-trial-", b"private-test-value"):
                self.assertNotIn(forbidden, data)

    def test_seed_supplied_and_stale_inherited_seed_removed(self):
        result = self.run_case(cases=(("seed", 2, "seed"),), seed=41)
        self.assertEqual((self.output(result), self.output(result, 2)), (b"41\n", b"42\n"))
        with patch.dict(os.environ, {"PROOFLAB_SEED": "999"}):
            absent = self.run_case(cases=(("seed", 1, "seed"),))
        self.assertEqual(self.output(absent), b"absent\n")

    def test_manifest_covers_all_files_and_tamper_rejected(self):
        result = self.run_case()
        expected = {p.relative_to(result.directory).as_posix() for p in result.directory.rglob("*") if p.is_file()}
        entries = {e["path"] for e in self.load(result, "manifest.json")["files"]}
        self.assertEqual(expected, entries | {"manifest.json", "manifest.sha256"})
        (result.directory / "artifacts/trial-000001.stdout").write_bytes(b"tampered")
        self.assertEqual(verify_package(result.directory).state, IntegrityState.INVALID)

    def test_resealed_receipt_contradictions_rejected(self):
        result = self.run_case()
        relative = self.load(result, "run.json")["case_records"][0]
        original = self.load(result, relative)
        for field, value in (("argv", ["other"]), ("plan_sha256", "0" * 64), ("seed", 99),
                             ("exit_code", 7), ("elapsed_ns", None), ("reason", "timeout"),
                             ("started_at", "not-a-time"), ("stdout_artifact", "missing")):
            with self.subTest(field=field):
                record = strict_loads(canonical_json(original))
                record["execution"][field] = value
                write_json(result.directory, relative, record)
                self.reseal(result)
                self.assertEqual(verify_package(result.directory).state, IntegrityState.INVALID)

    def test_resealed_snapshot_bytes_must_match_inventory(self):
        result = self.run_case()
        (result.directory / "snapshot/input.txt").write_bytes(b"wrong")
        self.reseal(result)
        verified = verify_package(result.directory)
        self.assertEqual(verified.state, IntegrityState.INVALID)
        self.assertIn("identity", str(verified.findings))

    def test_resealed_plan_and_definition_contradictions_rejected(self):
        result = self.run_case()
        (result.directory / "definition/experiment.toml").write_bytes(definition(cases=(("different", 1, "ok"),)))
        self.reseal(result)
        self.assertEqual(verify_package(result.directory).state, IntegrityState.INVALID)

    def test_verification_failure_does_not_report_sealed(self):
        with patch.object(runner, "verify_package", return_value=VerificationResult(IntegrityState.INVALID, ())):
            result = run_experiment(self.path)
        self.assertFalse(result.sealed)
        self.assertEqual(result.exit_code, 4)
        self.assertTrue((result.directory / "manifest.json").exists())

    def test_manifest_write_failure_preserves_completed_progress(self):
        with patch.object(runner, "build_manifest", side_effect=OSError("private path")):
            result = run_experiment(self.path)
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(self.receipts(result)[0]["execution_state"], "completed")
        self.assertEqual(verify_package(result.directory).state, IntegrityState.INCOMPLETE)
        self.assertNotIn("private path", result.reason)

    def test_collision_rejected_without_overwriting(self):
        with patch.object(runner.uuid, "uuid4") as random:
            random.return_value.hex = "fixed"
            first = self.run_case()
            before = (first.directory / "manifest.json").read_bytes()
            with self.assertRaises(FileExistsError):
                run_experiment(self.path)
        self.assertEqual((first.directory / "manifest.json").read_bytes(), before)

    def test_unique_run_ids_stable_deterministic_identities(self):
        first, second = self.run_case(), self.run_case()
        self.assertNotEqual(first.run_id, second.run_id)
        for name in ("protocol.json", "plan.json", "snapshot.json"):
            self.assertEqual((first.directory / name).read_bytes(), (second.directory / name).read_bytes())

    @unittest.skipUnless(os.name == "posix", "POSIX direct executable")
    def test_direct_captured_executable_minimal_permissions(self):
        self.path.write_bytes(definition(argv=["./program.py"]))
        (self.root / "program.py").write_bytes(b'#!/usr/bin/env python3\nprint("direct")\n')
        (self.root / "program.py").chmod(0o755)
        result = run_experiment(self.path)
        self.assertTrue(result.sealed, result)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.output(result), b"direct\n")
        self.assertEqual((result.directory / "snapshot/program.py").stat().st_mode & 0o777, 0o700)

    def test_symlinked_run_parent_rejected(self):
        target = self.root / "target"
        target.mkdir()
        (self.root / ".prooflab").symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            run_experiment(self.path)
        self.assertFalse(list(target.iterdir()))

    def test_cli_run_exit_zero_and_failed_three_verify_zero(self):
        for mode, expected in (("ok", 0), ("fail", 3)):
            make_project(self.root, cases=(("cli", 1, mode),))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["run", str(self.path)]), expected)
            self.assertIn("SEALED\n", out.getvalue())
            directory = next(line.split("=", 1)[1] for line in out.getvalue().splitlines() if line.startswith("evidence_directory="))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["verify", directory]), 0)

    def test_cli_invalid_selection_missing_argument_and_init_conflict(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", str(self.path), "--variant", "unknown"]), 2)
            with self.assertRaises(SystemExit) as error:
                main(["run"])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(main(["init", str(self.root)]), 2)

    def test_cli_help_and_nested_project_root(self):
        for args in (["--help"], ["run", "--help"]):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                main(args)
            self.assertEqual(result.exception.code, 0)
        nested = self.root / "definitions"
        nested.mkdir()
        self.path.rename(nested / "experiment.toml")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", str(nested / "experiment.toml"), "--project-root", str(self.root)]), 0)

    @unittest.skipUnless(os.name == "posix", "real POSIX user interruption")
    def test_real_cli_sigint_finalizes_and_reaps_child(self):
        make_project(self.root, cases=(("prior", 1, "ok"), ("active", 1, "timeout"), ("remaining", 1, "ok")))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        process = subprocess.Popen([sys.executable, "-m", "prooflab", "run", str(self.path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                outputs = list(self.root.glob(".prooflab/runs/*/artifacts/trial-000002.stdout"))
                if outputs and outputs[0].stat().st_size:
                    break
                if process.poll() is not None:
                    self.fail("runner exited before interrupt")
                time.sleep(0.01)
            else:
                self.fail("child did not produce output before test deadline")
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 3, stderr)
            self.assertIn(b"SEALED\n", stdout)
            package = outputs[0].parents[1]
            self.assertEqual(verify_package(package).state, IntegrityState.VERIFIED)
            run = strict_loads((package / "run.json").read_bytes())
            states = [strict_loads((package / p).read_bytes())["execution_state"] for p in run["case_records"]]
            self.assertEqual(states, ["completed", "interrupted", "skipped"])
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()

    def test_progress_survives_later_workspace_failure(self):
        original = runner.populate_workspace
        count = 0
        def populate(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("simulated storage failure")
            return original(*args)
        make_project(self.root, cases=(("work", 3, "ok"),))
        with patch.object(runner, "populate_workspace", side_effect=populate):
            result = run_experiment(self.path)
        self.assertFalse(result.sealed)
        self.assertEqual(self.receipts(result)[0]["execution_state"], "completed")
        self.assertEqual(self.output(result), b"ok\n")

    def test_cli_integrity_failure_returns_four_and_unsealed(self):
        with patch.object(runner, "build_manifest", side_effect=OSError), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["run", str(self.path)]), 4)
        self.assertEqual(out.getvalue().splitlines()[0], "UNSEALED")
        self.assertIn("integrity=NOT_VERIFIED", out.getvalue())

    def test_evaluator_retained_and_excluded_material_not_opened(self):
        self.path.write_bytes(self.path.read_bytes() + b'''
[[execution.materials]]
id = "evaluator"
path = "eval.py"
role = "evaluator"
[[execution.materials]]
id = "excluded"
path = "missing.txt"
role = "input"
capture = false
reason = "unused"
''')
        (self.root / "eval.py").write_bytes(b'raise RuntimeError("must never execute evaluator")\n')
        result = run_experiment(self.path)
        self.assertTrue(result.sealed)
        self.assertEqual({m["role"] for m in self.load(result, "snapshot.json")["materials"]},
                         {"source", "input", "evaluator"})
        self.assertFalse((result.directory / "snapshot/missing.txt").exists())

    def test_offline_verifier_rejects_rehashed_false_plan(self):
        result = self.run_case()
        plan = self.load(result, "plan.json")
        plan["trials"][0]["argv"] = ["other"]
        write_json(result.directory, "plan.json", plan)
        digest = sha256_bytes((result.directory / "plan.json").read_bytes())
        run = self.load(result, "run.json")
        run["execution"]["plan_sha256"] = digest
        write_json(result.directory, "run.json", run)
        for relative in run["case_records"]:
            receipt = self.load(result, relative)
            receipt["execution"]["plan_sha256"] = digest
            write_json(result.directory, relative, receipt)
        self.reseal(result)
        verification = verify_package(result.directory)
        self.assertEqual(verification.state, IntegrityState.INVALID)
        self.assertIn("deterministic retained plan", str(verification.findings))

    def test_offline_verifier_rejects_rehashed_false_snapshot_metadata(self):
        result = self.run_case()
        snapshot = self.load(result, "snapshot.json")
        snapshot["materials"][0]["executable"] = True
        write_json(result.directory, "snapshot.json", snapshot)
        digest = sha256_bytes((result.directory / "snapshot.json").read_bytes())
        run = self.load(result, "run.json")
        run["execution"]["snapshot_sha256"] = digest
        write_json(result.directory, "run.json", run)
        for relative in run["case_records"]:
            receipt = self.load(result, relative)
            receipt["execution"]["snapshot_sha256"] = digest
            write_json(result.directory, relative, receipt)
        self.reseal(result)
        self.assertEqual(verify_package(result.directory).state, IntegrityState.INVALID)

    def test_unfinished_trial_cannot_seal_with_execution_contract(self):
        result = self.run_case()
        relative = self.load(result, "run.json")["case_records"][0]
        receipt = self.load(result, relative)
        receipt["execution_state"] = "running"
        write_json(result.directory, relative, receipt)
        self.reseal(result)
        self.assertEqual(verify_package(result.directory).state, IntegrityState.INVALID)

    def test_dangling_symlink_material_prevents_package_execution(self):
        (self.root / "input.txt").unlink()
        (self.root / "input.txt").symlink_to("absent")
        with patch.object(runner, "execute_trial") as launch, self.assertRaises(ValueError):
            run_experiment(self.path)
        launch.assert_not_called()

    def test_timeout_changes_protocol_and_plan_identity(self):
        first, second = self.run_case(), self.run_case(timeout=1)
        for field in ("protocol_sha256", "plan_sha256"):
            self.assertNotEqual(self.load(first, "run.json")["execution"][field],
                                self.load(second, "run.json")["execution"][field])

    def test_interruption_after_final_trial_is_non_success(self):
        original = runner.write_record
        interrupted = False
        def write(path, record, kind=None):
            nonlocal interrupted
            if kind == "case" and record["execution_state"] == "completed" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(path, record, kind)
        with patch.object(runner, "write_record", side_effect=write):
            result = self.run_case()
        self.assertEqual(result.counts["completed"], 1)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(self.load(result, "run.json")["execution_state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
