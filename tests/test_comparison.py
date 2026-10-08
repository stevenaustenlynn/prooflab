"""Retained comparison policy, accounting, source binding, and CLI regressions."""

from copy import deepcopy
import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from prooflab.cli import main
from prooflab.comparison import (ComparisonError, aggregate, compare_runs, compatibility,
                                derive, evaluate_pair, source_records, read_bytes)
from prooflab.comparison_policy import parse_policy
from prooflab.integrity import build_manifest, sha256_bytes
from prooflab.records import canonical_json
from prooflab.runner import run_experiment
from prooflab.schema import SchemaError, UnsupportedSchema
from prooflab.verify import VerificationResult, verify_package
from prooflab.protocol import IntegrityState
from tests.test_phase4a import project, load, cases, OUTPUT, MEASUREMENT
from tests.test_runner import definition

ROOT = Path(__file__).resolve().parents[1]
POLICY = b'''schema = "prooflab.comparison-policy/v1"
id = "equivalence"
[[rules]]
id = "answer-equal"
measurement = "answer"
operator = "equal"
'''


def reseal(root):
    old = load(root, 'manifest.json')
    manifest, sidecar = build_manifest(root, {e['path']: e['role'] for e in old['files']},
                                       package_type=old.get('package_type'))
    (root / 'manifest.json').write_bytes(manifest)
    (root / 'manifest.sha256').write_bytes(sidecar)


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def observed(value):
    return {'state': 'observed', 'value': value, 'reason': None}


def unavailable(state):
    return {'state': state, 'value': None, 'reason': 'test'}


class PolicyTests(unittest.TestCase):
    def test_defaults(self):
        p = parse_policy(POLICY)
        self.assertEqual(p['description'], '')
        self.assertTrue(p['rules'][0]['required'])
        self.assertEqual(p['rules'][0]['description'], '')

    def test_malformed_toml(self):
        for raw in (b'[[bad', b'\xff', b'\xef\xbb\xbf' + POLICY, POLICY + b'id="twice"'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_policy(raw)

    def test_unsupported_policy(self):
        with self.assertRaises(UnsupportedSchema):
            parse_policy(POLICY.replace(b'/v1', b'/v2'))

    def test_duplicate_rule(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            parse_policy(POLICY + POLICY[POLICY.index(b'[[rules]]'):])

    def test_unknown_operator(self):
        with self.assertRaises(ValueError):
            parse_policy(POLICY.replace(b'"equal"', b'"eval"'))

    def test_missing_threshold(self):
        for operator in (b'delta_gte', b'delta_lte', b'abs_delta_lte'):
            with self.subTest(operator=operator), self.assertRaises(ValueError):
                parse_policy(POLICY.replace(b'"equal"', b'"' + operator + b'"'))

    def test_forbidden_threshold(self):
        for operator in (b'equal', b'candidate_gte_baseline', b'candidate_lte_baseline'):
            with self.subTest(operator=operator), self.assertRaises(ValueError):
                parse_policy(POLICY.replace(b'"equal"', b'"' + operator + b'"') + b'threshold=1\n')

    def test_wrong_threshold_type(self):
        for value in (b'true', b'"1"', b'[]', b'inf', b'nan'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_policy(POLICY.replace(b'"equal"', b'"delta_gte"') + b'threshold=' + value)

    def test_negative_absolute_tolerance(self):
        with self.assertRaises(ValueError):
            parse_policy(POLICY.replace(b'"equal"', b'"abs_delta_lte"') + b'threshold=-1')

    def test_closed_shape_and_required_type(self):
        for extra in (b'formula="candidate/baseline"', b'required=1', b'description=2'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_policy(POLICY + extra)

    def test_empty_policy_rules(self):
        with self.assertRaises(ValueError):
            parse_policy(POLICY.split(b'[[rules]]')[0] + b'rules=[]')

    def test_normalized_identity_comments_defaults(self):
        self.assertEqual(canonical_json(parse_policy(POLICY)),
                         canonical_json(parse_policy(POLICY + b'# comment\nrequired=true\n')))
        self.assertNotEqual(sha256_bytes(POLICY), sha256_bytes(POLICY + b'# comment\n'))

    def test_rule_order_normalized(self):
        header, rule = POLICY.split(b'[[rules]]')
        other = rule.replace(b'answer-equal', b'other')
        self.assertEqual(parse_policy(header + b'[[rules]]' + rule + b'[[rules]]' + other),
                         parse_policy(header + b'[[rules]]' + other + b'[[rules]]' + rule))

    def test_semantic_changes_change_identity(self):
        original = sha256_bytes(canonical_json(parse_policy(POLICY)))
        for raw in (POLICY + b'required=false', POLICY.replace(b'measurement = "answer"', b'measurement="other"'),
                    POLICY.replace(b'"equal"', b'"candidate_gte_baseline"')):
            self.assertNotEqual(original, sha256_bytes(canonical_json(parse_policy(raw))))
        one = POLICY.replace(b'"equal"', b'"delta_gte"')
        self.assertNotEqual(parse_policy(one + b'threshold=1'), parse_policy(one + b'threshold=2'))


class OperatorTests(unittest.TestCase):
    def result(self, op, a, b, threshold=None):
        rule = {'operator': op}
        if threshold is not None:
            rule['threshold'] = threshold
        return evaluate_pair(rule, observed(a), observed(b))[0]

    def test_equal_integer(self):
        self.assertEqual(self.result('equal', 10, 10), 'PASS')
        self.assertEqual(self.result('equal', 10, 9), 'FAIL')

    def test_equal_string(self):
        self.assertEqual(self.result('equal', 'a', 'a'), 'PASS')
        self.assertEqual(self.result('equal', 'a', 'A'), 'FAIL')

    def test_equal_boolean(self):
        self.assertEqual(self.result('equal', True, True), 'PASS')
        self.assertEqual(self.result('equal', True, False), 'FAIL')

    def test_equal_float(self):
        self.assertEqual(self.result('equal', 0.0, -0.0), 'PASS')
        self.assertEqual(self.result('equal', 0.3, 0.1 + 0.2), 'FAIL')
        self.assertEqual(self.result('equal', -1.25, -1.25), 'PASS')

    def test_no_type_coercion(self):
        for a, b in ((1, True), ('1', 1), (1, 1.0)):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                self.result('equal', a, b)

    def test_candidate_gte_baseline(self):
        for a, b, expected in ((-3, -2, 'PASS'), (-2, -3, 'FAIL'), (1.25, 1.25, 'PASS'), (-1.5, -1.25, 'PASS')):
            self.assertEqual(self.result('candidate_gte_baseline', a, b), expected)

    def test_candidate_lte_baseline(self):
        for a, b, expected in ((-2, -3, 'PASS'), (-3, -2, 'FAIL'), (1.25, 1.25, 'PASS'), (-1.25, -1.5, 'PASS')):
            self.assertEqual(self.result('candidate_lte_baseline', a, b), expected)

    def test_delta_gte(self):
        for a, b, t, expected in ((-3, -1, 2, 'PASS'), (-3, -2, 2, 'FAIL'),
                                   (-1.5, -1.25, .25, 'PASS'), (3, 1, -2, 'PASS')):
            self.assertEqual(self.result('delta_gte', a, b, t), expected)

    def test_delta_lte(self):
        for a, b, t, expected in ((-3, -1, 2, 'PASS'), (-3, 0, 2, 'FAIL'),
                                   (-1.5, -1.25, .25, 'PASS'), (3, 1, -2, 'PASS')):
            self.assertEqual(self.result('delta_lte', a, b, t), expected)

    def test_abs_delta_lte(self):
        for a, b, t, expected in ((-3, -1, 2, 'PASS'), (-3, 0, 2, 'FAIL'),
                                   (-1.5, -1.25, .25, 'PASS'), (3, 1, 2, 'PASS')):
            self.assertEqual(self.result('abs_delta_lte', a, b, t), expected)

    def test_nonfinite_delta_inconclusive(self):
        self.assertEqual(self.result('delta_gte', -1e308, 1e308, 0), 'INCONCLUSIVE')

    def test_integer_delta_does_not_round_to_float(self):
        self.assertEqual(self.result('delta_gte', 10**100, 10**100 + 1, 1), 'PASS')

    def test_both_not_applicable(self):
        self.assertEqual(evaluate_pair({'operator': 'equal'}, unavailable('not_applicable'),
                                       unavailable('not_applicable'))[0], 'NOT_APPLICABLE')

    def test_one_sided_not_applicable(self):
        self.assertEqual(evaluate_pair({'operator': 'equal'}, observed(1), unavailable('not_applicable'))[0],
                         'INCONCLUSIVE')

    def test_missing_error_not_evaluated(self):
        for state in ('missing', 'error', 'not_evaluated'):
            for a, b in ((observed(1), unavailable(state)), (unavailable(state), observed(1))):
                self.assertEqual(evaluate_pair({'operator': 'equal'}, a, b)[0], 'INCONCLUSIVE')

    def test_rule_fail_precedence(self):
        self.assertEqual(aggregate(['PASS', 'FAIL', 'INCONCLUSIVE']), 'FAIL')

    def test_rule_inconclusive_precedence(self):
        self.assertEqual(aggregate(['PASS', 'NOT_APPLICABLE', 'INCONCLUSIVE']), 'INCONCLUSIVE')

    def test_rule_all_passing(self):
        self.assertEqual(aggregate(['PASS', 'PASS']), 'PASS')

    def test_empty_never_passes(self):
        self.assertEqual(aggregate([]), 'NOT_APPLICABLE')
        self.assertEqual(aggregate(['NOT_APPLICABLE']), 'NOT_APPLICABLE')


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(prefix='prooflab-comparison-runs-')
        root = Path(cls.shared.name)
        cls.a = run_experiment(project(root / 'a', trials=2)).directory
        cls.b = run_experiment(project(root / 'b', trials=2, data=b'{"answer":9}\n')).directory
        cls.error = run_experiment(project(root / 'error', trials=2, data=b'{bad')).directory
        cls.na = run_experiment(project(root / 'na', trials=2, script='pass\n',
                                        declarations=OUTPUT.replace(b'true', b'false') + MEASUREMENT)).directory
        cls.na_observed = run_experiment(project(root / 'na-observed', trials=2,
                                        declarations=OUTPUT.replace(b'true', b'false') + MEASUREMENT)).directory
        cls.unstarted = run_experiment(project(root / 'unstarted', trials=2,
                                               argv=['prooflab-absent-4b-executable'])).directory
        for package in (cls.a, cls.b, cls.error, cls.na, cls.na_observed, cls.unstarted):
            assert verify_package(package).state == 'verified'

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prooflab-compare-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / 'policy.toml'
        self.policy.write_bytes(POLICY)

    def compare(self, a=None, b=None):
        result = compare_runs(a or self.a, b or self.b, self.policy, output_root=self.root)
        self.assertTrue(result.sealed)
        self.assertEqual(verify_package(result.directory).state, 'verified')
        return result

    def copy(self, source=None):
        return Path(shutil.copytree(source or self.a, self.root / 'source-copy'))

    def source(self, package=None):
        return source_records(lambda p: read_bytes(package or self.a, p))

    def edit_comparison(self, result, change):
        record = load(result.directory, 'comparison.json')
        change(record)
        (result.directory / 'comparison.json').write_bytes(canonical_json(record))
        reseal(result.directory)

    def test_valid_sealed_pass_and_fail(self):
        self.assertEqual(self.compare(self.a, self.a).exit_code, 0)
        self.assertEqual(self.compare().exit_code, 1)

    def test_invalid_baseline_rejected(self):
        a = self.copy()
        (a / 'protocol.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ComparisonError, 'baseline.*INVALID'):
            self.compare(a, self.b)
        self.assertFalse((self.root / '.prooflab').exists())

    def test_invalid_candidate_rejected(self):
        a = self.copy()
        (a / 'manifest.sha256').write_bytes(b'wrong')
        with self.assertRaisesRegex(ComparisonError, 'candidate.*INVALID'):
            self.compare(self.b, a)

    def test_incomplete_input_rejected(self):
        a = self.copy()
        (a / 'cases/trial-000002.json').unlink()
        with self.assertRaisesRegex(ComparisonError, 'INCOMPLETE'):
            self.compare(a, self.b)

    def test_unsupported_input_rejected(self):
        a = self.copy()
        record = load(a, 'run.json'); record['schema'] = 'prooflab.run/v99'
        (a / 'run.json').write_bytes(canonical_json(record)); reseal(a)
        with self.assertRaisesRegex(ComparisonError, 'UNSUPPORTED'):
            self.compare(a, self.b)

    def test_legacy_accounting_input_not_supported(self):
        with self.assertRaises(ComparisonError):
            self.compare(ROOT / 'tests/fixtures/valid', self.a)

    def test_roles_positional_directional(self):
        self.policy.write_bytes(POLICY.replace(b'"equal"', b'"candidate_gte_baseline"'))
        self.assertEqual(self.compare(self.a, self.b).exit_code, 1)
        self.assertEqual(self.compare(self.b, self.a).exit_code, 0)

    def test_source_difference_allowed(self):
        result = self.compare()
        self.assertEqual(result.record['compatibility']['state'], 'COMPATIBLE')
        self.assertNotEqual(load(self.a, 'snapshot.json')['materials'], load(self.b, 'snapshot.json')['materials'])

    def test_protocol_identity_incompatible(self):
        path = project(self.root / 'other', trials=2)
        path.write_bytes(path.read_bytes().replace(b'execution-demo', b'different'))
        other = run_experiment(path).directory
        result = self.compare(self.a, other)
        self.assertEqual((result.exit_code, result.record['verdict'], result.record['pairs']), (2, 'INCOMPATIBLE', []))
        self.assertFalse(next(c for c in result.record['compatibility']['checks'] if c['id'] == 'experiment_id')['passed'])

    def test_trial_population_incompatible(self):
        other = run_experiment(project(self.root / 'other', trials=1)).directory
        result = self.compare(self.a, other)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.record['paired_trials'], 0)

    def test_case_identities_incompatible(self):
        path = project(self.root / 'other', trials=2)
        path.write_bytes(path.read_bytes().replace(b'"case"', b'"other"'))
        result = self.compare(self.a, run_experiment(path).directory)
        self.assertFalse(next(c for c in result.record['compatibility']['checks'] if c['id'] == 'case_identities')['passed'])

    def test_seed_semantics_incompatible(self):
        path = project(self.root / 'other', trials=2)
        path.write_bytes(path.read_bytes().replace(b'case_id = "case"', b'case_id = "case"\nseed = 41'))
        result = self.compare(self.a, run_experiment(path).directory)
        self.assertFalse(next(c for c in result.record['compatibility']['checks'] if c['id'] == 'trial_seed_semantics')['passed'])

    def test_input_identity_incompatible(self):
        path = project(self.root / 'other', trials=2)
        (path.parent / 'input.txt').write_bytes(b'different')
        result = self.compare(self.a, run_experiment(path).directory)
        self.assertFalse(next(c for c in result.record['compatibility']['checks'] if c['id'] == 'input_identities')['passed'])

    def test_evaluator_identity_incompatible(self):
        path = project(self.root / 'other', trials=2)
        path.write_bytes(path.read_bytes() + b'\n[[execution.materials]]\nid="eval"\npath="eval.py"\nrole="evaluator"\n')
        (path.parent / 'eval.py').write_bytes(b'raise RuntimeError("never execute")')
        a = run_experiment(path).directory
        (path.parent / 'eval.py').write_bytes(b'raise RuntimeError("changed; never execute")')
        b = run_experiment(path).directory
        result = self.compare(a, b)
        self.assertFalse(next(c for c in result.record['compatibility']['checks'] if c['id'] == 'evaluator_identities')['passed'])

    def test_measurement_definition_incompatible(self):
        other = run_experiment(project(self.root / 'other', trials=2,
                    declarations=OUTPUT + MEASUREMENT.replace(b'["answer"]', b'["other"]'))).directory
        self.assertEqual(self.compare(self.a, other).exit_code, 2)

    def test_value_type_incompatible(self):
        other = run_experiment(project(self.root / 'other', trials=2, data=b'{"answer":10.0}',
                    declarations=OUTPUT + MEASUREMENT.replace(b'integer', b'float'))).directory
        self.assertEqual(self.compare(self.a, other).exit_code, 2)

    def test_unit_incompatible(self):
        other = run_experiment(project(self.root / 'other', trials=2,
                    declarations=OUTPUT + MEASUREMENT + b'unit="items"')).directory
        self.assertEqual(self.compare(self.a, other).exit_code, 2)

    def test_unknown_measurement(self):
        self.policy.write_bytes(POLICY.replace(b'measurement = "answer"', b'measurement="absent"'))
        with self.assertRaisesRegex(ComparisonError, 'unknown measurement'):
            self.compare()

    def test_numeric_operator_on_string_invalid(self):
        other = run_experiment(project(self.root / 'other', data=b'{"answer":"x"}',
                    declarations=OUTPUT + MEASUREMENT.replace(b'integer', b'string'))).directory
        self.policy.write_bytes(POLICY.replace(b'"equal"', b'"delta_gte"') + b'threshold=1')
        with self.assertRaisesRegex(ComparisonError, 'numeric operator'):
            self.compare(other, other)

    def test_pair_by_key_not_array_position(self):
        a, b = self.source(), self.source(self.b)
        b['cases'][('case', 2)]['measurements'][0]['value'] = 8
        b['cases'] = dict(reversed(list(b['cases'].items())))
        result = derive(a, b, parse_policy(POLICY))
        self.assertEqual([(p['case_id'], p['trial']) for p in result['pairs']], [('case', 1), ('case', 2)])
        self.assertEqual([p['candidate']['value'] for p in result['pairs']], [9, 8])

    def test_missing_expected_trial_never_dropped(self):
        a, b = self.source(), self.source()
        b['cases'].pop(('case', 2))
        self.assertEqual(derive(a, b, parse_policy(POLICY))['verdict'], 'INCOMPATIBLE')

    def test_missing_measurement_inconclusive_defensively(self):
        a, b = self.source(), self.source()
        b['cases'][('case', 1)]['measurements'] = []
        result = derive(a, b, parse_policy(POLICY))
        self.assertEqual(result['verdict'], 'INCONCLUSIVE')
        self.assertEqual(result['pairs'][0]['candidate']['state'], 'missing')

    def test_measurement_error_inconclusive_seals(self):
        result = self.compare(self.a, self.error)
        self.assertEqual((result.record['verdict'], result.exit_code), ('INCONCLUSIVE', 3))

    def test_required_output_absent_inconclusive_seals(self):
        other = run_experiment(project(self.root / 'missing', trials=2, script='pass\n')).directory
        result = self.compare(self.a, other)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.record['pairs'][0]['candidate']['reason'], 'required_output_missing')

    def test_not_evaluated_inconclusive_seals(self):
        self.assertEqual(self.compare(self.unstarted, self.unstarted).exit_code, 3)

    def test_no_applicable_population_never_pass(self):
        result = self.compare(self.na, self.na)
        self.assertEqual(result.record['rules'][0]['state'], 'NOT_APPLICABLE')
        self.assertEqual(result.exit_code, 3)

    def test_one_sided_not_applicable_inconclusive_seals(self):
        self.assertEqual(self.compare(self.na, self.na_observed).exit_code, 3)

    def test_one_failure_overrides_inconclusive_pair(self):
        a, b = self.source(), self.source(self.b)
        b['cases'][('case', 1)]['measurements'][0].update(state='error', value=None, reason='invalid_json')
        self.assertEqual(derive(a, b, parse_policy(POLICY))['verdict'], 'FAIL')

    def test_one_inconclusive_prevents_pass(self):
        a, b = self.source(), self.source()
        b['cases'][('case', 1)]['measurements'][0].update(state='not_evaluated', value=None, reason='execution_not_started')
        self.assertEqual(derive(a, b, parse_policy(POLICY))['verdict'], 'INCONCLUSIVE')

    def test_optional_failure_does_not_fail_overall(self):
        self.policy.write_bytes(POLICY + b'required=false\n[[rules]]\nid="required"\nmeasurement="answer"\noperator="candidate_lte_baseline"')
        result = self.compare()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.record['rules'][0]['state'], 'FAIL')

    def test_only_optional_rules_never_pass_overall(self):
        self.policy.write_bytes(POLICY + b'required=false')
        self.assertEqual(self.compare(self.a, self.a).exit_code, 3)

    def test_identity_binding_and_privacy(self):
        result = self.compare()
        for role, source in (('baseline', self.a), ('candidate', self.b)):
            self.assertEqual(result.record[role]['manifest_sha256'], sha256_bytes((source / 'manifest.json').read_bytes()))
            self.assertEqual(result.record[role]['run_id'], load(source, 'run.json')['id'])
        self.assertEqual(result.record['policy']['sha256'], sha256_bytes(POLICY))
        for raw in tree(result.directory).values():
            self.assertNotIn(str(self.root).encode(), raw)
            self.assertNotIn(str(Path(self.shared.name)).encode(), raw)
            self.assertNotIn(b'/home/', raw)

    def test_inputs_immutable_and_compare_never_executes(self):
        before = tree(self.a), tree(self.b), self.policy.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            self.compare()
        self.assertEqual(before, (tree(self.a), tree(self.b), self.policy.read_bytes()))

    def test_output_inside_source_refused_before_write(self):
        before = tree(self.a)
        with self.assertRaisesRegex(ValueError, 'inside a source'):
            compare_runs(self.a, self.b, self.policy, output_root=self.a)
        self.assertEqual(before, tree(self.a))

    def test_output_symlink_refused(self):
        target = self.root / 'target'; target.mkdir()
        (self.root / '.prooflab').symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            compare_runs(self.a, self.b, self.policy, output_root=self.root)
        self.assertEqual(list(target.iterdir()), [])

    def test_repeated_determinism(self):
        a, b = self.compare().record, self.compare().record
        self.assertNotEqual(a.pop('id'), b.pop('id'))
        a.pop('created_at'); b.pop('created_at')
        self.assertEqual(canonical_json(a), canonical_json(b))

    def test_altered_operand_rejected_after_rehash(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r['pairs'][0]['candidate'].update(value=10))
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_altered_rule_rejected_after_rehash(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r['rules'][0].update(state='PASS'))
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_altered_overall_rejected_after_rehash(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r.update(verdict='PASS'))
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_altered_policy_rejected(self):
        result = self.compare()
        (result.directory / 'policy.toml').write_bytes(POLICY + b'required=false')
        self.assertEqual(verify_package(result.directory).state, 'invalid')
        reseal(result.directory)
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_altered_source_operand_rejected_after_outer_rehash(self):
        result = self.compare()
        relative = 'sources/candidate/cases/trial-000001.json'
        record = load(result.directory, relative)
        record['measurements'][0]['value'] = 10
        (result.directory / relative).write_bytes(canonical_json(record)); reseal(result.directory)
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_compatibility_forgery_rejected(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r['compatibility']['checks'][0].update(passed=False))
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_pair_omission_rejected(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r['pairs'].pop())
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_source_digest_format_rejected(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r['baseline'].update(manifest_sha256='bad'))
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_unknown_comparison_version(self):
        result = self.compare()
        self.edit_comparison(result, lambda r: r.update(schema='prooflab.comparison/v2'))
        self.assertEqual(verify_package(result.directory).state, 'unsupported')

    def test_manifest_dispatch_explicit(self):
        result = self.compare()
        self.assertEqual(load(result.directory, 'manifest.json')['package_type'], 'comparison')
        manifest = load(result.directory, 'manifest.json'); manifest.pop('package_type')
        raw = canonical_json(manifest)
        (result.directory / 'manifest.json').write_bytes(raw)
        (result.directory / 'manifest.sha256').write_text(sha256_bytes(raw) + '\n')
        self.assertNotEqual(verify_package(result.directory).state, 'verified')

    def test_sealing_failure_exit_four(self):
        import prooflab.comparison as comparison
        original = comparison.verify_package
        def verify(path, *args):
            if Path(path).name.startswith('comparison-'):
                return VerificationResult(IntegrityState.INVALID, ())
            return original(path, *args)
        with patch.object(comparison, 'verify_package', side_effect=verify):
            result = compare_runs(self.a, self.b, self.policy, output_root=self.root)
        self.assertFalse(result.sealed)
        self.assertEqual(result.exit_code, 4)

    def test_assembly_failure_exit_four(self):
        with patch('prooflab.comparison.build_manifest', side_effect=OSError):
            result = compare_runs(self.a, self.b, self.policy, output_root=self.root)
        self.assertEqual(result.exit_code, 4)
        self.assertTrue((result.directory / 'comparison.json').exists())

    def test_cli_compare_exit_codes(self):
        with patch('prooflab.comparison.Path.cwd', return_value=self.root):
            for a, b, expected in ((self.a, self.a, 0), (self.a, self.b, 1),
                                   (self.a, self.error, 3), (self.a, self.na, 2)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(['compare', str(a), str(b), '--policy', str(self.policy)])
                self.assertEqual(code, expected)
                self.assertIn('integrity=VERIFIED', out.getvalue())
                directory = next(line.split('=', 1)[1] for line in out.getvalue().splitlines() if line.startswith('evidence_directory='))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['verify', directory]), 0)

    def test_cli_missing_args_and_invalid_policy(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                main(['compare'])
            self.assertEqual(exc.exception.code, 2)
            self.policy.write_bytes(b'invalid')
            self.assertEqual(main(['compare', str(self.a), str(self.b), '--policy', str(self.policy)]), 2)

    def test_cli_help(self):
        for args in (['--help'], ['compare', '--help'], ['verify', '--help']):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
                main(args)
            self.assertEqual(exc.exception.code, 0)

    def test_typed_equal_packages(self):
        for kind, left, right in ((b'string', b'"yes"', b'"no"'),
                                  (b'boolean', b'true', b'false'),
                                  (b'float', b'-1.25', b'-1.5')):
            with self.subTest(kind=kind):
                declaration = OUTPUT + MEASUREMENT.replace(b'integer', kind)
                path = project(self.root / kind.decode(), declarations=declaration,
                               data=b'{"answer":' + left + b'}')
                a = run_experiment(path).directory
                project(path.parent, declarations=declaration, data=b'{"answer":' + right + b'}')
                b = run_experiment(path).directory
                self.assertEqual(self.compare(a, a).exit_code, 0)
                self.assertEqual(self.compare(a, b).exit_code, 1)

    def test_standalone_verifier_does_not_require_original_packages(self):
        result = self.compare()
        with patch('prooflab.comparison.verify_package', side_effect=AssertionError('no source verification')):
            self.assertEqual(verify_package(result.directory).state, 'verified')

    def test_source_change_after_verification_rejected(self):
        a = self.copy()
        import prooflab.comparison as comparison
        original = comparison.verify_package
        def verify(path):
            result = original(path)
            if Path(path) == a:
                (a / 'cases/trial-000001.json').write_bytes(b'{}')
            return result
        with patch.object(comparison, 'verify_package', side_effect=verify), self.assertRaises(ComparisonError):
            self.compare(a, self.b)
        self.assertFalse((self.root / '.prooflab').exists())

    def test_cli_seal_failure_exit_four(self):
        with patch('prooflab.comparison.Path.cwd', return_value=self.root), \
                patch('prooflab.comparison.build_manifest', side_effect=OSError), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['compare', str(self.a), str(self.b), '--policy', str(self.policy)]), 4)
        self.assertIn('UNSEALED', out.getvalue())

    def test_run_verifier_rejects_comparison_role_in_run(self):
        a = self.copy()
        manifest = load(a, 'manifest.json')
        manifest['files'][0]['role'] = 'comparison'
        raw = canonical_json(manifest)
        (a / 'manifest.json').write_bytes(raw)
        (a / 'manifest.sha256').write_text(sha256_bytes(raw) + '\n')
        self.assertEqual(verify_package(a).state, 'invalid')

    def test_sum_roles_variants_and_sealed_results(self):
        root = self.root / 'sum'; root.mkdir()
        for name in ('experiment.toml', 'implementations.py', 'small.txt'):
            (root / name).write_bytes((ROOT / 'examples/sum' / name).read_bytes())
        runs = {v: run_experiment(root / 'experiment.toml', v).directory for v in ('iterative', 'formula', 'incorrect')}
        for variant, answer, verdict, code in (('formula', 10, 'PASS', 0), ('incorrect', 9, 'FAIL', 1)):
            result = self.compare(runs['iterative'], runs[variant])
            self.assertEqual((result.record['verdict'], result.exit_code), (verdict, code))
            self.assertEqual([(p['baseline']['value'], p['candidate']['value']) for p in result.record['pairs']], [(10, answer)] * 2)
        self.policy.write_bytes(POLICY.replace(b'"equal"', b'"candidate_gte_baseline"'))
        result = self.compare(runs['incorrect'], runs['iterative'])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.record['baseline']['run_id'], load(runs['incorrect'], 'run.json')['id'])


if __name__ == '__main__':
    unittest.main()
