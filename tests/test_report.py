"""Phase 5 reports consume evidence only and never change acceptance semantics."""

import builtins
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from prooflab.cli import main
from prooflab.comparison import compare_runs
from prooflab.records import canonical_json
from prooflab.report import render_report, scalar
from prooflab.runner import run_experiment
from prooflab.verify import verify_package
from tests.support import make_package
from tests.test_comparison import POLICY, reseal, tree
from tests.test_phase4a import project, load, OUTPUT, MEASUREMENT


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(prefix='prooflab-report-')
        root = Path(cls.shared.name)
        cls.packages = {}
        for name, kwargs in {
            'success': {'trials': 2}, 'wrong': {'trials': 2, 'data': b'{"answer":9}'},
            'error': {'trials': 2, 'data': b'{bad'}, 'missing': {'script': 'pass\n'},
            'optional': {'script': 'pass\n', 'declarations': OUTPUT.replace(b'true', b'false') + MEASUREMENT},
            'failed': {'script': 'sys.exit(7)\n'},
            'timed_out': {'script': 'time.sleep(5)\n', 'timeout': 1},
            'capture_error': {'script': 'Path("out").mkdir()\nPath("out/result.json").mkdir()\n'},
        }.items():
            result = run_experiment(project(root / name, **kwargs))
            assert result.sealed
            cls.packages[name] = result.directory
        original_wait = subprocess.Popen.wait
        interrupted = False

        def interrupt(process, *args, **kwargs):
            nonlocal interrupted
            if isinstance(process.args, list) and 'program.py' in process.args and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(process, *args, **kwargs)

        with patch.object(subprocess.Popen, 'wait', interrupt):
            result = run_experiment(project(root / 'interrupted', trials=2, script='time.sleep(5)\n'))
        assert result.sealed
        cls.packages['interrupted'] = result.directory
        policy = root / 'policy.toml'
        policy.write_bytes(POLICY)
        for name, a, b in [('PASS', 'success', 'success'), ('FAIL', 'success', 'wrong'),
                           ('INCONCLUSIVE', 'success', 'error'), ('INCOMPATIBLE', 'success', 'missing'),
                           ('NA', 'optional', 'optional')]:
            result = compare_runs(cls.packages[a], cls.packages[b], policy, output_root=root)
            assert result.sealed
            cls.packages[name] = result.directory
        policy.write_bytes(POLICY.replace(b'"equal"', b'"candidate_lte_baseline"') +
                          b'\n[[rules]]\nid="optional-equal"\nmeasurement="answer"\noperator="equal"\nrequired=false\n')
        cls.packages['optional_fail'] = compare_runs(cls.packages['success'], cls.packages['wrong'], policy, output_root=root).directory
        cls.packages['legacy'] = root / 'legacy'
        make_package(cls.packages['legacy'])

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='prooflab-report-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def invoke(self, package, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['report', str(package), *map(str, args)])
        return code, out.getvalue(), err.getvalue()

    def report(self, name):
        code, out, err = self.invoke(self.packages[name])
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('# ProofLab'))
        return out

    def copy(self, name='success'):
        return Path(shutil.copytree(self.packages[name], self.root / name))

    def test_success_identity_accounting_measurements(self):
        out = self.report('success')
        run = load(self.packages['success'], 'run.json')
        self.assertIn(run['id'], out)
        self.assertIn(verify_package(self.packages['success']).manifest_sha256, out)
        for text in ('Planned cases: `1`', 'Planned trials: `2`', 'Retained measurements: `2`',
                     'No acceptance policy is recorded', '`10`', '`"integer"`', 'snapshot_sha256'):
            self.assertIn(text, out)

    def test_failed_run_still_report_exit_zero(self):
        self.assertIn('Operational state: `"failed"`', self.report('failed'))

    def test_timeout_run(self):
        self.assertIn('Operational state: `"timed_out"`', self.report('timed_out'))

    def test_interrupted_run_accounts_skipped(self):
        out = self.report('interrupted')
        self.assertIn('| `"interrupted"` | `1` |', out)
        self.assertIn('| `"skipped"` | `1` |', out)
        self.assertIn('execution_not_started', out)

    def test_exit_zero_missing_required_output_prominent(self):
        out = self.report('missing')
        self.assertIn('Operational state: `"completed"`', out)
        self.assertIn('Required-output absences: `1`', out.split('## Identity')[0])
        self.assertIn('Measurement errors: `1`', out)
        self.assertIn('required_output_missing', out)

    def test_measurement_error(self):
        out = self.report('error')
        self.assertIn('Measurement errors: `2`', out)
        self.assertIn('invalid_json', out)
        self.assertNotIn('{bad', out)

    def test_optional_absence(self):
        out = self.report('optional')
        self.assertIn('Optional-output absences: `1`', out)
        self.assertIn('optional_output_missing', out)
        self.assertIn('not_applicable', out)

    def test_capture_error(self):
        self.assertIn('Output capture errors: `1`', self.report('capture_error'))

    def test_legacy_absence_and_untrusted_acceptance(self):
        out = self.report('legacy')
        self.assertIn('Required-output absences: not recorded / unavailable', out)
        self.assertIn('Execution receipts: not recorded', out)
        self.assertIn('Legacy acceptance labels are not recomputed', out)
        self.assertNotIn('**PASS**', out)

    def test_pass_comparison(self):
        self.assertIn('Scientific result under retained policy: **PASS**', self.report('PASS'))

    def test_fail_comparison_exit_zero(self):
        self.assertIn('Scientific result under retained policy: **FAIL**', self.report('FAIL'))

    def test_inconclusive_comparison_exit_zero(self):
        self.assertIn('Scientific result under retained policy: **INCONCLUSIVE**', self.report('INCONCLUSIVE'))

    def test_incompatible_no_scientific_verdict(self):
        out = self.report('INCOMPATIBLE')
        self.assertIn('Paired trials: `0`', out)
        self.assertIn('trial_population', out)
        self.assertNotIn('Scientific result under retained policy:', out)
        self.assertIn('Rule result and operands: unavailable / inapplicable', out)

    def test_optional_fail_does_not_change_pass(self):
        out = self.report('optional_fail')
        self.assertIn('**PASS**', out)
        self.assertIn('Required: `false`', out)
        self.assertIn('Rule result: `"FAIL"`', out)
        self.assertIn('Pair denominator (all outcomes): `2`', out)

    def test_inapplicable_population(self):
        out = self.report('NA')
        self.assertIn('**INCONCLUSIVE**', out)
        self.assertIn('Rule result: `"NOT_APPLICABLE"`', out)
        self.assertIn('both_not_applicable', out)

    def test_empty_population_not_supported_by_v1(self):
        # v1 requires at least one case and positive trials. Never loosen this
        # verifier rule just to manufacture an empty compatible report.
        package = self.copy('legacy')
        protocol = load(package, 'protocol.json')
        protocol['cases'] = []
        (package / 'protocol.json').write_bytes(canonical_json(protocol))
        reseal(package)
        self.assertEqual(self.invoke(package)[:2], (1, ''))
        out = self.report('INCOMPATIBLE')
        self.assertIn('Retained rule-pair rows (all outcomes): `0`', out)

    def test_pre_measurement_execution_package(self):
        package = run_experiment(project(self.root / 'old', declarations=b'')).directory
        out = render_report(package)
        self.assertIn('Output declarations and coverage: not recorded', out)
        self.assertIn('Measurement declarations, types, units and extraction provenance: not recorded', out)
        self.assertIn('Execution receipts: recorded', out)

    def test_comparison_semantic_tamper_rejected_after_reseal(self):
        package = self.copy('PASS')
        record = load(package, 'comparison.json')
        record['verdict'] = 'FAIL'
        (package / 'comparison.json').write_bytes(canonical_json(record))
        reseal(package)
        self.assertEqual(self.invoke(package)[:2], (1, ''))

    def test_replacement_manifest_after_verification(self):
        package = self.copy('legacy')
        def replace(*args, **kwargs):
            result = verify_package(*args, **kwargs)
            record = load(package, 'run.json')
            record['id'] = 'changed-run'
            (package / 'run.json').write_bytes(canonical_json(record))
            reseal(package)
            return result
        with patch('prooflab.report.verify_package', side_effect=replace):
            self.assertEqual(self.invoke(package)[:2], (1, ''))

    def test_source_lineage_and_no_original_access(self):
        package = self.copy('PASS')
        out = render_report(package)
        self.assertIn('Original source packages are not rechecked by report', out)
        self.assertIn('do not substitute for complete original packages', out)

    def test_repeated_and_relocated_reports(self):
        for name in ('success', 'PASS'):
            with self.subTest(name=name):
                original = render_report(self.packages[name])
                self.assertEqual(original, render_report(self.packages[name]))
                self.assertEqual(original, render_report(self.copy(name)))

    def test_entire_input_trees_unchanged(self):
        for name in ('success', 'FAIL'):
            before = tree(self.packages[name])
            directories = sorted(str(p) for p in self.packages[name].rglob('*') if p.is_dir())
            self.report(name)
            self.assertEqual(before, tree(self.packages[name]))
            self.assertEqual(directories, sorted(str(p) for p in self.packages[name].rglob('*') if p.is_dir()))

    def test_no_execution_network_dynamic_retained_imports_or_writes(self):
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            self.assertNotIn('program', name)
            return original_import(name, *args, **kwargs)
        with contextlib.ExitStack() as stack:
            for target in ('subprocess.Popen', 'os.system', 'socket.socket', 'importlib.util.spec_from_file_location',
                           'pathlib.Path.write_bytes', 'pathlib.Path.write_text'):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            stack.enter_context(patch('builtins.__import__', side_effect=guarded_import))
            for name in ('success', 'PASS'):
                self.report(name)

    def test_invalid_tampered_rejected_no_stdout(self):
        package = self.copy()
        (package / 'run.json').write_bytes(b'{}')
        code, out, err = self.invoke(package)
        self.assertEqual((code, out), (1, ''))
        self.assertIn('INVALID', err)

    def test_incomplete_rejected(self):
        package = self.copy()
        (package / 'manifest.sha256').unlink()
        self.assertEqual(self.invoke(package)[:2], (3, ''))

    def test_unsupported_rejected(self):
        package = self.copy('legacy')
        record = load(package, 'run.json')
        record['schema'] = 'prooflab.run/v99'
        (package / 'run.json').write_bytes(canonical_json(record))
        reseal(package)
        self.assertEqual(self.invoke(package)[:2], (4, ''))

    def test_nonexistent_input(self):
        self.assertEqual(self.invoke(self.root / 'missing')[:2], (3, ''))

    def test_mutation_after_first_verification(self):
        package = self.copy()
        original = verify_package
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (package / 'run.json').write_bytes(b'{}')
            return result
        with patch('prooflab.report.verify_package', side_effect=mutate):
            self.assertEqual(self.invoke(package)[:2], (1, ''))

    def test_payload_mutation_during_render(self):
        package = self.copy()
        from prooflab.report import _run
        def mutate(*args):
            _run(*args)
            (package / 'artifacts/trial-000001.stdout').write_bytes(b'changed')
        with patch('prooflab.report._run', side_effect=mutate):
            self.assertEqual(self.invoke(package)[:2], (1, ''))

    def test_new_output_only(self):
        target = self.root / 'report.md'
        code, out, err = self.invoke(self.packages['success'], '--output', target)
        self.assertEqual((code, out, err), (0, '', ''))
        self.assertEqual(target.read_text(), render_report(self.packages['success']))

    def test_existing_output_untouched(self):
        target = self.root / 'report.md'
        target.write_bytes(b'keep')
        self.assertEqual(self.invoke(self.packages['success'], '--output', target)[:2], (2, ''))
        self.assertEqual(target.read_bytes(), b'keep')

    def test_output_inside_package_rejected(self):
        package = self.copy()
        before = tree(package)
        self.assertEqual(self.invoke(package, '--output', package / 'new.md')[:2], (2, ''))
        self.assertEqual(before, tree(package))

    def test_symlink_parent_alias_inside_rejected(self):
        package = self.copy()
        alias = self.root / 'alias'
        alias.symlink_to(package, target_is_directory=True)
        self.assertEqual(self.invoke(package, '--output', alias / 'new.md')[:2], (2, ''))
        self.assertFalse((package / 'new.md').exists())

    def test_existing_symlink_destination_untouched(self):
        target = self.root / 'existing'
        target.write_bytes(b'keep')
        alias = self.root / 'report.md'
        alias.symlink_to(target)
        self.assertEqual(self.invoke(self.packages['success'], '--output', alias)[:2], (2, ''))
        self.assertEqual(target.read_bytes(), b'keep')

    def test_missing_parent_not_created(self):
        target = self.root / 'absent' / 'report.md'
        self.assertEqual(self.invoke(self.packages['success'], '--output', target)[:2], (2, ''))
        self.assertFalse(target.parent.exists())

    def test_write_failure(self):
        with patch('prooflab.report.write_report', side_effect=OSError('private host path')):
            code, out, err = self.invoke(self.packages['success'], '--output', self.root / 'report.md')
        self.assertEqual((code, out), (2, ''))
        self.assertNotIn('private host path', err)

    def test_invalid_input_does_not_create_output(self):
        target = self.root / 'report.md'
        self.assertEqual(self.invoke(self.root / 'absent', '--output', target)[0], 3)
        self.assertFalse(target.exists())

    def test_invalid_invocation_and_five_commands(self):
        for args in (['report'], ['report', 'x', '--unknown']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(args)
            self.assertEqual(error.exception.code, 2)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(['--help'])
        self.assertIn('{verify,run,compare,report,init}', output.getvalue())

    def test_scalar_escaping_and_precision(self):
        value = '`<script>&|\n\r\t\x1b[31m\x7f\u202e https://example.test [x](y) **bold**'
        shown = scalar(value)
        self.assertEqual(shown.count('`'), 2)
        for char in ('<', '>', '|', '\n', '\r', '\t', '\x1b', '\x7f', '\u202e'):
            self.assertNotIn(char, shown)
        for number in (0.30000000000000004, -0.0, 10**100):
            self.assertEqual(scalar(number), '`' + json.dumps(number) + '`')
        self.assertEqual(scalar(True), '`true`')

    def test_untrusted_measurement_string_and_truncation(self):
        value = '`<script>|\n\x1b[31m https://example.test ' + 'x' * 300
        declarations = OUTPUT + MEASUREMENT.replace(b'type = "integer"', b'type = "string"')
        package = run_experiment(project(self.root / 'strings', data=json.dumps({'answer': value}).encode(),
                                         declarations=declarations)).directory
        out = render_report(package)
        self.assertIn('[truncated after 256 code points]', out)
        self.assertIn('\\u001b', out)
        self.assertIn('\\u003cscript\\u003e', out)
        self.assertNotIn('<script>', out)
        self.assertNotIn('\x1b', out)
        self.assertIn('Retained measurements: `1`', out)

    def test_policy_description_escaped(self):
        policy = self.root / 'policy.toml'
        policy.write_bytes(POLICY + b'description="<img>|\\n`[x](https://example.test)"\n')
        package = compare_runs(self.packages['success'], self.packages['success'], policy, output_root=self.root).directory
        out = render_report(package)
        self.assertIn('\\u003cimg\\u003e\\u007c\\n\\u0060', out)
        self.assertNotIn('<img>', out)

    def test_no_generated_host_environment_or_content_leakage(self):
        out = self.report('success')
        for text in (str(self.root), str(Path.cwd()), str(Path.home()), 'retained stdout',
                     'Path("out")', 'report-generation timestamp'):
            self.assertNotIn(text, out)
        self.assertIn('not an atomic snapshot', out)
        self.assertIn('Escaping is not secret redaction', out)

    def test_stdout_flush_failure_returns_two(self):
        sink = io.StringIO()
        with patch('sys.stdout', sink), patch.object(sink, 'flush', side_effect=OSError('write failed')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['report', str(self.packages['success'])]), 2)

    def test_legacy_unfinished_accounting_is_explicit(self):
        package = self.copy('legacy')
        case = load(package, 'cases/small-1.json')
        case['execution_state'] = 'planned'
        (package / 'cases/small-1.json').write_bytes(canonical_json(case))
        reseal(package)
        out = render_report(package)
        self.assertIn('Unfinished trial states (planned/running): `1`', out)
        self.assertIn('legacy states are retained assertions', out)
