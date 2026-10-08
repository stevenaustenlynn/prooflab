"""Declared evidence, scalar extraction and rehashed-contradiction regressions."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from prooflab.ingestion import parse_protocol
from prooflab.integrity import build_manifest, sha256_bytes
from prooflab.measurements import extract
from prooflab.records import as_record, canonical_json, strict_loads
from prooflab.runner import run_experiment
from prooflab.schema import SchemaError, validate_record
from prooflab.verify import verify_package
from tests.test_runner import definition

OUTPUT = b'\n[[outputs]]\nid = "result"\npath = "out/result.json"\nrequired = true\n'
MEASUREMENT = b'\n[[measurements]]\nid = "answer"\nsource = "output:result"\nextractor = "json"\ntype = "integer"\nparameters.path = ["answer"]\n'


def project(root, data=b'{"answer":10}\n', *, script=None, declarations=OUTPUT + MEASUREMENT,
            trials=1, timeout=None, argv=None):
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'experiment.toml'
    path.write_bytes(definition(cases=(("case", trials, "ok"),), timeout=timeout, argv=argv) + declarations)
    source = 'from pathlib import Path\nimport os, sys, time\n'
    source += script if script is not None else (
        'assert not Path("out").exists()\nPath("out").mkdir()\n'
        f'Path("out/result.json").write_bytes({data!r})\n'
        'Path("undeclared.txt").write_bytes(b"never retained")\nprint("retained stdout")\n')
    (root / 'program.py').write_text(source, encoding='utf-8')
    (root / 'input.txt').write_bytes(b'input\n')
    return path


def load(package, relative):
    return strict_loads((package / relative).read_bytes())


def cases(package):
    return [load(package, relative) for relative in load(package, 'run.json')['case_records']]


def reseal(package):
    roles = {e['path']: e['role'] for e in load(package, 'manifest.json')['files']}
    manifest, sidecar = build_manifest(package, roles)
    (package / 'manifest.json').write_bytes(manifest)
    (package / 'manifest.sha256').write_bytes(sidecar)


class DeclarationTests(unittest.TestCase):
    def record(self, extra=OUTPUT + MEASUREMENT):
        return as_record(parse_protocol(definition() + extra))

    def test_required_and_optional_defaults(self):
        self.assertTrue(self.record()['outputs'][0]['required'])
        self.assertTrue(self.record(OUTPUT.replace(b'required = true\n', b''))['outputs'][0]['required'])
        self.assertFalse(self.record(OUTPUT.replace(b'true', b'false'))['outputs'][0]['required'])
        m = self.record()['measurements'][0]
        self.assertEqual((m['version'], m['unit'], m['parameters']), (1, '', {'path': ['answer']}))

    def test_duplicate_output_id(self):
        with self.assertRaises(SchemaError):
            self.record(OUTPUT + OUTPUT.replace(b'out/result.json', b'out/other.json'))

    def test_duplicate_output_path(self):
        with self.assertRaises(SchemaError):
            self.record(OUTPUT + OUTPUT.replace(b'id = "result"', b'id = "other"'))

    def test_case_and_prefix_path_collisions(self):
        for path in (b'OUT/other.json', b'out/RESULT.json', b'out', b'out/result.json/child'):
            with self.subTest(path=path), self.assertRaises(SchemaError):
                self.record(OUTPUT + OUTPUT.replace(b'id = "result"', b'id = "other"').replace(b'out/result.json', path))

    def test_nonportable_output_paths(self):
        for path in (b'/absolute', b'../escape', b'a/../b', b'C:/drive', b'out/*.json',
                     b'out/**', b'out/./a', b'out//a', b'NUL.txt', b'a.', b'a b'):
            with self.subTest(path=path), self.assertRaises(SchemaError):
                self.record(OUTPUT.replace(b'out/result.json', path))

    def test_material_and_output_collision(self):
        for path in (b'program.py', b'PROGRAM.py', b'program.py/result', b'input.txt'):
            with self.subTest(path=path), self.assertRaises(SchemaError):
                self.record(OUTPUT.replace(b'out/result.json', path))

    def test_duplicate_measurement_id(self):
        with self.assertRaises(SchemaError):
            self.record(OUTPUT + MEASUREMENT + MEASUREMENT)

    def test_unknown_source_extractor_type_version_and_parameters(self):
        for before, after in ((b'output:result', b'output:absent'), (b'"json"', b'"plugin"'),
                              (b'"integer"', b'"array"'), (b'parameters.path', b'parameters.expression')):
            with self.subTest(after=after), self.assertRaises(SchemaError):
                self.record(OUTPUT + MEASUREMENT.replace(before, after))
        with self.assertRaises(SchemaError):
            self.record(OUTPUT + MEASUREMENT + b'version = 2\n')

    def test_path_components_are_keys_or_nonnegative_indices(self):
        for path in (b'[-1]', b'[true]', b'[1.5]', b'[{}]', b'"answer"'):
            with self.subTest(path=path), self.assertRaises(SchemaError):
                self.record(OUTPUT + MEASUREMENT.replace(b'["answer"]', path))

    def test_extractor_source_type_and_unit_constraints(self):
        record = self.record()
        for changes in ({'source': 'execution'}, {'extractor': 'byte_count'},
                        {'extractor': 'exit_code'}, {'source': 'execution', 'extractor': 'duration_ns'}):
            with self.subTest(changes=changes), self.assertRaises(SchemaError):
                altered = deepcopy(record)
                altered['measurements'][0].update(changes)
                validate_record(altered, 'protocol')

    def test_each_definition_semantic_changes_identity(self):
        record = self.record()
        original = sha256_bytes(canonical_json(record))
        for changes in ({'id': 'other'}, {'source': 'stdout'}, {'type': 'float'}, {'unit': 'items'},
                        {'parameters': {'path': ['different']}},
                        {'extractor': 'byte_count', 'parameters': {'path': []}}):
            with self.subTest(changes=changes):
                altered = deepcopy(record)
                altered['measurements'][0].update(changes)
                validate_record(altered, 'protocol')
                self.assertNotEqual(original, sha256_bytes(canonical_json(altered)))

    def test_declaration_order_is_normalized(self):
        other_o = OUTPUT.replace(b'"result"', b'"other"').replace(b'result.json', b'other.json')
        other_m = MEASUREMENT.replace(b'"answer"\n', b'"second"\n')
        self.assertEqual(self.record(OUTPUT + other_o + MEASUREMENT + other_m),
                         self.record(other_o + OUTPUT + other_m + MEASUREMENT))

    def test_legacy_absence_is_preserved(self):
        record = self.record(b'')
        self.assertNotIn('outputs', record)
        self.assertNotIn('measurements', record)


class ExtractionTests(unittest.TestCase):
    def evaluate(self, data, *, extractor='json', kind='integer', path=('answer',)):
        definition = {'id': 'answer', 'source': 'stdout', 'extractor': extractor, 'version': 1,
                      'type': kind, 'unit': '', 'parameters': {'path': list(path)}}
        case = {'execution_state': 'completed', 'execution': {'started_at': 'retained', 'exit_code': 0, 'stdout_artifact': 'stdout'}}
        return extract(definition, case, lambda identifier: data)

    def test_integer(self):
        self.assertEqual(self.evaluate(b'{"answer":10}')['value'], 10)

    def test_finite_float(self):
        value = self.evaluate(b'{"answer":1.25}', kind='float')['value']
        self.assertIs(type(value), float)
        self.assertEqual(value, 1.25)

    def test_boolean(self):
        value = self.evaluate(b'{"answer":false}', kind='boolean')['value']
        self.assertIs(value, False)

    def test_string(self):
        self.assertEqual(self.evaluate(b'{"answer":"yes"}', kind='string')['value'], 'yes')

    def test_byte_count(self):
        self.assertEqual(self.evaluate(b'\x00\xff\r\n', extractor='byte_count', path=())['value'], 4)

    def test_sha256(self):
        data = b'\x00\xff\r\n'
        self.assertEqual(self.evaluate(data, extractor='sha256', kind='string', path=())['value'], sha256_bytes(data))

    def test_utf8_preserves_exact_text(self):
        text = '\ufeffé\r\n\x00'
        self.assertEqual(self.evaluate(text.encode(), extractor='utf8', kind='string', path=())['value'], text)

    def test_utf8_invalid(self):
        self.assertEqual(self.evaluate(b'\xff', extractor='utf8', kind='string')['reason'], 'invalid_utf8')

    def test_json_key_index_and_root_paths(self):
        self.assertEqual(self.evaluate(b'{"a":[{"0":7}]}', path=('a', 0, '0'))['value'], 7)
        self.assertEqual(self.evaluate(b'42', path=())['value'], 42)
        self.assertEqual(self.evaluate(b'{"":3}', path=('',))['value'], 3)

    def test_invalid_json(self):
        for data in (b'{bad', b'{} trailing', b'\xff', b'\xef\xbb\xbf{}', b'"\\ud800"'):
            with self.subTest(data=data):
                self.assertEqual(self.evaluate(data)['reason'], 'invalid_json')

    def test_duplicate_json_keys(self):
        for data in (b'{"answer":1,"answer":2}', b'{"answer":1,"a":{"x":1,"x":2}}'):
            self.assertEqual(self.evaluate(data)['reason'], 'invalid_json')

    def test_nonfinite_json_anywhere(self):
        for literal in (b'NaN', b'Infinity', b'-Infinity', b'1e999'):
            with self.subTest(literal=literal):
                self.assertEqual(self.evaluate(b'{"answer":1,"other":' + literal + b'}')['reason'], 'invalid_json')

    def test_missing_path(self):
        for data, path in ((b'{}', ('answer',)), (b'[1]', (1,)), (b'[1]', ('0',)),
                           (b'{"0":1}', (0,)), (b'1', ('a',))):
            self.assertEqual(self.evaluate(data, path=path)['reason'], 'missing_json_path')

    def test_type_mismatch_no_coercion(self):
        for literal, kind in ((b'true', 'integer'), (b'1', 'float'), (b'1.0', 'integer'),
                              (b'1', 'boolean'), (b'null', 'string'), (b'[]', 'string'), (b'{}', 'integer')):
            result = self.evaluate(b'{"answer":' + literal + b'}', kind=kind)
            self.assertEqual((result['state'], result['value'], result['reason']), ('error', None, 'type_mismatch'))

    def test_repeat_extraction_has_identical_bytes(self):
        for data in (b'{"answer":10}', b'{bad'):
            self.assertEqual(canonical_json(self.evaluate(data)), canonical_json(self.evaluate(data)))

    def test_retained_read_failure_is_not_a_content_error(self):
        with self.assertRaises(OSError):
            extract({'source': 'stdout', 'extractor': 'utf8'}, {'execution_state': 'completed',
                    'execution': {'started_at': 'retained', 'exit_code': 0, 'stdout_artifact': 'stream'}},
                    lambda _: (_ for _ in ()).throw(OSError()))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prooflab-phase4a-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_project(self, **kwargs):
        result = run_experiment(project(self.root, **kwargs))
        self.assertTrue(result.sealed, result)
        verified = verify_package(result.directory)
        self.assertEqual(verified.state, 'verified', verified.findings)
        return result

    def test_exact_capture_size_hash_and_no_undeclared_outputs(self):
        data = b'\x00\xff\r\n'
        result = self.run_project(data=data)
        output = cases(result.directory)[0]['outputs'][0]
        self.assertEqual((output['size_bytes'], output['sha256']), (len(data), sha256_bytes(data)))
        self.assertEqual((result.directory / 'outputs/trial-000001/out/result.json').read_bytes(), data)
        self.assertFalse(list(result.directory.rglob('undeclared.txt')))
        self.assertEqual(cases(result.directory)[0]['measurements'][0]['reason'], 'invalid_json')
        self.assertEqual(result.exit_code, 0)

    def test_required_missing_is_error_and_still_seals(self):
        result = self.run_project(script='print("execution evidence")\n')
        case = cases(result.directory)[0]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(case['execution_state'], 'completed')
        self.assertEqual(case['outputs'][0]['reason'], 'required_output_missing')
        self.assertEqual((case['measurements'][0]['state'], case['measurements'][0]['value']), ('error', None))
        self.assertIsNone(case['outputs'][0]['artifact_id'])
        self.assertFalse((result.directory / 'outputs').exists())

    def test_optional_missing_is_not_applicable(self):
        result = self.run_project(script='pass\n', declarations=OUTPUT.replace(b'true', b'false') + MEASUREMENT)
        case = cases(result.directory)[0]
        self.assertEqual(case['outputs'][0]['state'], 'not_applicable')
        self.assertEqual(case['measurements'][0]['reason'], 'optional_output_missing')
        self.assertIsNone(case['measurements'][0]['value'])

    def test_output_only_without_measurement(self):
        result = self.run_project(declarations=OUTPUT)
        self.assertEqual(cases(result.directory)[0]['measurements'], [])
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['state'], 'observed')

    def test_symlink_file_rejected(self):
        result = self.run_project(script='Path("out").mkdir()\nPath("out/result.json").symlink_to("../input.txt")\n')
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['reason'], 'type_change_or_unsupported')
        self.assertFalse((result.directory / 'outputs').exists())

    def test_symlink_directory_rejected(self):
        result = self.run_project(script='Path("out").symlink_to(".", target_is_directory=True)\n')
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['reason'], 'type_change_or_unsupported')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX FIFO')
    def test_fifo_rejected_without_opening(self):
        result = self.run_project(script='Path("out").mkdir()\nos.mkfifo("out/result.json")\n', timeout=2)
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['reason'], 'type_change_or_unsupported')

    def test_directory_output_rejected(self):
        result = self.run_project(script='Path("out/result.json").mkdir(parents=True)\n')
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['state'], 'error')

    def test_filesystem_case_alias_rejected(self):
        result = self.run_project(script='Path("out").mkdir()\nPath("out/result.json").touch()\nPath("out/RESULT.json").touch()\n')
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['reason'], 'ambiguous_path')

    def test_clean_trials_and_repeated_measurement_identity(self):
        result = self.run_project(trials=2)
        first, second = cases(result.directory)
        self.assertEqual(result.counts['completed'], 2)
        self.assertEqual(first['outputs'][0]['sha256'], second['outputs'][0]['sha256'])
        again = self.run_project(trials=2)
        self.assertEqual(canonical_json(first['measurements']), canonical_json(cases(again.directory)[0]['measurements']))

    def test_operational_sources_and_streams(self):
        declarations = b''
        for name, source, extractor, kind, unit in (
            ('exit', 'execution', 'exit_code', 'integer', ''),
            ('duration', 'execution', 'duration_ns', 'integer', 'ns'),
            ('stdout', 'stdout', 'utf8', 'string', ''),
            ('stderr', 'stderr', 'byte_count', 'integer', ''),
            ('digest', 'stdout', 'sha256', 'string', '')):
            declarations += (f'\n[[measurements]]\nid="{name}"\nsource="{source}"\nextractor="{extractor}"\n'
                             f'type="{kind}"\nunit="{unit}"\n').encode()
        result = self.run_project(script='print("hello")\nsys.exit(7)\n', declarations=declarations)
        case = cases(result.directory)[0]
        values = {m['id']: m['value'] for m in case['measurements']}
        self.assertEqual(values['exit'], 7)
        self.assertEqual(values['duration'], case['execution']['elapsed_ns'])
        self.assertEqual(values['stdout'], 'hello\n')
        self.assertEqual(values['stderr'], 0)
        self.assertEqual(values['digest'], sha256_bytes(b'hello\n'))
        self.assertEqual(result.exit_code, 3)

    def test_launch_failure_outputs_not_evaluated(self):
        result = self.run_project(argv=['prooflab-missing-executable-phase4a'])
        self.assertEqual(cases(result.directory)[0]['outputs'][0]['state'], 'not_evaluated')
        self.assertEqual(cases(result.directory)[0]['measurements'][0]['state'], 'not_evaluated')

    def test_launch_failure_streams_are_not_fabricated_zero_measurements(self):
        declarations = MEASUREMENT.replace(b'output:result', b'stdout').replace(b'"json"', b'"byte_count"').replace(b'["answer"]', b'[]')
        result = self.run_project(argv=['prooflab-missing-executable-phase4a'], declarations=declarations)
        m = cases(result.directory)[0]['measurements'][0]
        self.assertEqual((m['state'], m['value'], m['reason']),
                         ('not_evaluated', None, 'execution_not_started'))
        self.assertIsNotNone(m['source_artifact'])

    def test_timeout_retains_partial_declared_output(self):
        result = self.run_project(script='Path("out").mkdir()\nPath("out/result.json").write_bytes(b"{partial")\ntime.sleep(60)\n', timeout=1)
        case = cases(result.directory)[0]
        self.assertEqual(case['execution_state'], 'timed_out')
        self.assertEqual(case['outputs'][0]['state'], 'observed')
        self.assertEqual(case['measurements'][0]['reason'], 'invalid_json')

    def test_interruption_retains_output_and_accounts_for_skipped_measurements(self):
        original = subprocess.Popen.wait
        interrupted = False
        def wait(process, timeout=None):
            nonlocal interrupted
            if isinstance(process.args, list) and 'program.py' in process.args and not interrupted:
                original(process, timeout=timeout)
                interrupted = True
                raise KeyboardInterrupt
            return original(process, timeout=timeout)
        with patch.object(subprocess.Popen, 'wait', wait):
            result = self.run_project(trials=2)
        active, skipped = cases(result.directory)
        self.assertEqual(active['execution_state'], 'interrupted')
        self.assertEqual(active['measurements'][0]['value'], 10)
        self.assertEqual(skipped['outputs'][0]['state'], 'not_evaluated')
        self.assertEqual(skipped['measurements'][0]['state'], 'not_evaluated')

    def test_no_live_workspace_read_during_extraction(self):
        import prooflab.runner as runner
        original, populate = runner.extract, runner.populate_workspace
        workspaces = []
        def remember(package, workspace, snapshot):
            workspaces.append(workspace)
            return populate(package, workspace, snapshot)
        def retained_only(definition, case, read):
            self.assertTrue(workspaces)
            self.assertTrue(all(not workspace.exists() for workspace in workspaces))
            return original(definition, case, read)
        with patch.object(runner, 'extract', retained_only), patch.object(runner, 'populate_workspace', remember):
            result = self.run_project()
        self.assertEqual(cases(result.directory)[0]['measurements'][0]['value'], 10)

    def test_all_scalar_types_verify_from_retained_bytes(self):
        for literal, kind, value in ((b'1.25', b'float', 1.25), (b'false', b'boolean', False),
                                    (b'"text"', b'string', 'text')):
            with self.subTest(kind=kind):
                result = self.run_project(data=b'{"answer":' + literal + b'}',
                                          declarations=OUTPUT + MEASUREMENT.replace(b'integer', kind))
                measurement = cases(result.directory)[0]['measurements'][0]
                self.assertIs(type(measurement['value']), type(value))
                self.assertEqual(measurement['value'], value)

    def test_verification_never_executes_code(self):
        result = self.run_project()
        with patch('subprocess.Popen', side_effect=AssertionError('offline only')):
            self.assertEqual(verify_package(result.directory).state, 'verified')

    def test_output_tamper_detected(self):
        result = self.run_project()
        (result.directory / 'outputs/trial-000001/out/result.json').write_bytes(b'{"answer":99}\n')
        self.assertEqual(verify_package(result.directory).state, 'invalid')
        reseal(result.directory)
        self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_measurement_tamper_detected_even_after_rehash(self):
        result = self.run_project()
        path = result.directory / 'cases/trial-000001.json'
        case = load(result.directory, 'cases/trial-000001.json')
        case['measurements'][0]['value'] = 99
        path.write_bytes(canonical_json(case))
        self.assertEqual(verify_package(result.directory).state, 'invalid')
        reseal(result.directory)
        verification = verify_package(result.directory)
        self.assertEqual(verification.state, 'invalid')
        self.assertIn('recomputation', str(verification.findings))

    def test_rehashed_measurement_metadata_contradictions(self):
        result = self.run_project()
        path = result.directory / 'cases/trial-000001.json'
        original = load(result.directory, 'cases/trial-000001.json')
        for changes in ({'value': True}, {'value': 10.0}, {'unit': 'changed'}, {'source_artifact': None},
                        {'state': 'error', 'value': None, 'reason': 'invalid_json'},
                        {'parameters': {'path': []}}):
            with self.subTest(changes=changes):
                case = deepcopy(original)
                case['measurements'][0].update(changes)
                path.write_bytes(canonical_json(case)); reseal(result.directory)
                self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_rehashed_output_metadata_contradictions(self):
        result = self.run_project()
        path = result.directory / 'cases/trial-000001.json'
        original = load(result.directory, 'cases/trial-000001.json')
        for changes in ({'size_bytes': 99}, {'sha256': '0' * 64}, {'artifact_id': 'material-0000'},
                        {'required': False}, {'path': 'other.json'}, {'reason': 'required_output_missing'}):
            with self.subTest(changes=changes):
                case = deepcopy(original)
                case['outputs'][0].update(changes)
                path.write_bytes(canonical_json(case)); reseal(result.directory)
                self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_rehashed_missing_or_extra_measurements_rejected(self):
        result = self.run_project()
        path = result.directory / 'cases/trial-000001.json'
        original = load(result.directory, 'cases/trial-000001.json')
        for field in ('measurements', 'outputs'):
            case = deepcopy(original); case[field] = []
            path.write_bytes(canonical_json(case)); reseal(result.directory)
            self.assertEqual(verify_package(result.directory).state, 'invalid')

    def test_truthful_error_package_verifies(self):
        result = self.run_project(data=b'{"answer":true}')
        case = cases(result.directory)[0]
        self.assertEqual(case['measurements'][0]['reason'], 'type_mismatch')
        self.assertEqual((result.directory / 'artifacts/trial-000001.stdout').read_bytes(), b'retained stdout\n')
        self.assertEqual(result.exit_code, 0)

    def test_sum_variants_have_values_without_scientific_verdict(self):
        example = Path(__file__).resolve().parents[1] / 'examples/sum'
        for name in ('experiment.toml', 'implementations.py', 'small.txt'):
            (self.root / name).write_bytes((example / name).read_bytes())
        for variant, answer in (('iterative', 10), ('formula', 10), ('incorrect', 9)):
            result = run_experiment(self.root / 'experiment.toml', variant)
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(result.sealed)
            self.assertEqual(load(result.directory, 'run.json')['acceptance_state'], 'not_applicable')
            for case in cases(result.directory):
                self.assertEqual(case['measurements'][0]['value'], answer)
                self.assertEqual(case['acceptance_state'], 'not_applicable')

    def test_generated_metadata_is_portable(self):
        result = self.run_project()
        for relative in ('run.json', 'protocol.json', 'plan.json', 'cases/trial-000001.json'):
            data = (result.directory / relative).read_bytes()
            self.assertNotIn(str(self.root).encode(), data)
            self.assertNotIn(b'prooflab-trial-', data)
            self.assertNotIn(b'/home/', data)

    def test_capture_rejects_symlink_workspace_root(self):
        from prooflab.outputs import capture_outputs
        target = self.root / 'target'
        target.mkdir()
        (target / 'result').write_bytes(b'not captured')
        workspace = self.root / 'workspace'
        workspace.symlink_to(target, target_is_directory=True)
        def forbidden(*args):
            self.fail('unsafe output must not be retained')
        records = capture_outputs(workspace, self.root / 'package',
                                  [{'id': 'result', 'path': 'result', 'required': True}],
                                  1, forbidden, forbidden)
        self.assertEqual(records[0]['reason'], 'unreadable_or_unsafe')

    def test_retained_read_failure_preserves_completed_execution(self):
        with patch('prooflab.runner.extract', side_effect=OSError('private failure detail')):
            result = run_experiment(project(self.root))
        self.assertEqual(result.exit_code, 4)
        self.assertFalse(result.sealed)
        case = cases(result.directory)[0]
        self.assertEqual(case['execution_state'], 'completed')
        self.assertEqual(case['outputs'][0]['state'], 'observed')
        self.assertEqual(case['execution']['exit_code'], 0)
        self.assertEqual(result.reason, 'evidence_assembly_incomplete')
        self.assertTrue((result.directory / 'outputs/trial-000001/out/result.json').is_file())

    def test_empty_file_is_observed_zero_bytes(self):
        declarations = OUTPUT + MEASUREMENT.replace(b'"json"', b'"byte_count"').replace(b'["answer"]', b'[]')
        result = self.run_project(data=b'', declarations=declarations)
        case = cases(result.directory)[0]
        self.assertEqual(case['outputs'][0]['state'], 'observed')
        self.assertEqual(case['outputs'][0]['size_bytes'], 0)
        self.assertEqual(case['measurements'][0]['state'], 'observed')
        self.assertEqual(case['measurements'][0]['value'], 0)

    def test_every_builtin_rejects_rehashed_false_value(self):
        declarations = OUTPUT + MEASUREMENT
        for name, source, extractor, kind, unit in (
            ('exit', 'execution', 'exit_code', 'integer', ''),
            ('duration', 'execution', 'duration_ns', 'integer', 'ns'),
            ('text', 'stdout', 'utf8', 'string', ''),
            ('count', 'stderr', 'byte_count', 'integer', ''),
            ('digest', 'stdout', 'sha256', 'string', '')):
            declarations += (f'\n[[measurements]]\nid="{name}"\nsource="{source}"\nextractor="{extractor}"\n'
                             f'type="{kind}"\nunit="{unit}"\n').encode()
        result = self.run_project(declarations=declarations)
        original = cases(result.directory)[0]
        for index, measurement in enumerate(original['measurements']):
            with self.subTest(extractor=measurement['extractor']):
                case = deepcopy(original)
                case['measurements'][index]['value'] = (measurement['value'] + 1
                    if type(measurement['value']) is int else measurement['value'] + 'changed')
                (result.directory / 'cases/trial-000001.json').write_bytes(canonical_json(case))
                reseal(result.directory)
                verification = verify_package(result.directory)
                self.assertEqual(verification.state, 'invalid')
                self.assertIn('recomputation', str(verification.findings))

    def test_interruption_between_trials_finalizes_measurements(self):
        import prooflab.runner as runner
        original = runner.write_record
        interrupted = False
        def write(path, record, kind=None):
            nonlocal interrupted
            if kind == 'case' and record['execution_state'] == 'completed' and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(path, record, kind)
        with patch.object(runner, 'write_record', write):
            result = self.run_project(trials=2)
        first, second = cases(result.directory)
        self.assertEqual(first['measurements'][0]['value'], 10)
        self.assertEqual(second['measurements'][0]['state'], 'not_evaluated')
        self.assertEqual(result.exit_code, 3)

    def test_user_output_and_extracted_text_are_not_redacted(self):
        text = b'user-supplied private text\n'
        declarations = OUTPUT + MEASUREMENT.replace(b'"json"', b'"utf8"').replace(b'"integer"', b'"string"').replace(b'["answer"]', b'[]')
        result = self.run_project(data=text, declarations=declarations)
        self.assertEqual(cases(result.directory)[0]['measurements'][0]['value'], text.decode())
