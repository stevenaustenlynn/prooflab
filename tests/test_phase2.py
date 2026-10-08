"""Protocol/planning tests never import or invoke experiment implementations."""

from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from prooflab.ingestion import ProtocolError, load_protocol, parse_protocol
from prooflab.materials import (MaterialError, capture_materials, compare_materials,
                               inventory_sha256, read_identity)
from prooflab.planning import build_plan
from prooflab.provenance import capture_provenance
from prooflab.records import as_record, canonical_json
from prooflab.schema import SchemaError, UnsupportedSchema, validate_record
from prooflab.verify import verify_package
from tests.support import make_package, seal, write_json

ROOT = Path(__file__).resolve().parents[1]
TOML = b'''schema = "prooflab.protocol/v1"
id = "demo"
variants = ["only"]
[[cases]]
id = "b"
trials = 2
[[cases]]
id = "a"
[[execution.commands]]
variant = "only"
argv = ["python3", "source.py"]
source_ids = ["src"]
[[execution.materials]]
id = "src"
path = "source.py"
role = "source"
[[execution.materials]]
id = "data"
path = "input.txt"
role = "input"
[[execution.bindings]]
case_id = "b"
args = ["input.txt"]
input_ids = ["data"]
seed = 10
[[execution.bindings]]
case_id = "a"
'''


class ProtocolTests(unittest.TestCase):
    def test_valid_toml_and_explicit_defaults(self):
        protocol = parse_protocol(TOML)
        record = as_record(protocol)
        validate_record(record, "protocol")
        self.assertEqual(protocol.cases[1].trials, 1)
        self.assertEqual(record["execution"]["bindings"][0],
                         {"case_id": "a", "args": [], "input_ids": [], "seed": None})
        self.assertTrue(all(m.capture for m in protocol.execution.materials))

    def test_malformed_toml_utf8_bom_and_duplicate_keys(self):
        for value in (b'[[broken', b'\xff', b'\xef\xbb\xbf' + TOML, TOML + b'case_id = "a"\n'):
            with self.subTest(value=value[:15]), self.assertRaises(ProtocolError):
                parse_protocol(value)

    def test_unsupported_version(self):
        with self.assertRaises(UnsupportedSchema):
            parse_protocol(TOML.replace(b'protocol/v1', b'protocol/v2'))

    def test_required_fields(self):
        for field in (b'id = "demo"\n', b'schema = "prooflab.protocol/v1"\n',
                      b'variants = ["only"]\n', b'argv = ["python3", "source.py"]\n'):
            with self.subTest(field=field), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(field, b''))
        with self.assertRaisesRegex(ProtocolError, 'execution'):
            parse_protocol(TOML.split(b'[[execution.commands]]')[0])

    def test_wrong_types(self):
        for old, new in ((b'variants = ["only"]', b'variants = "only"'),
                         (b'trials = 2', b'trials = true'),
                         (b'seed = 10', b'seed = false'),
                         (b'seed = 10', b'seed = 1.5'),
                         (b'argv = ["python3", "source.py"]', b'argv = "python3 source.py"'),
                         (b'input_ids = ["data"]', b'input_ids = [1]')):
            with self.subTest(new=new), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(old, new))

    def test_unknown_fields_metrics_gates(self):
        for field in (b'metrics = []\n', b'gates = []\n', b'varaints = []\n'):
            with self.subTest(field=field), self.assertRaisesRegex(SchemaError, 'unknown fields'):
                parse_protocol(field + TOML)
        with self.assertRaises(SchemaError):
            parse_protocol(TOML + b'argz = []\n')

    def test_duplicate_semantic_ids(self):
        for old, new in ((b'id = "a"', b'id = "b"'),
                         (b'id = "data"', b'id = "src"'),
                         (b'case_id = "a"', b'case_id = "b"'),
                         (b'variants = ["only"]', b'variants = ["only", "only"]')):
            with self.subTest(new=new), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(old, new))
        command = b'[[execution.commands]]\nvariant = "only"\nargv = ["x"]\nsource_ids = ["src"]\n'
        with self.assertRaisesRegex(SchemaError, 'duplicate'):
            parse_protocol(TOML + command)

    def test_invalid_counts_and_seed(self):
        for value in (b'0', b'-1', b'1.5', b'"2"'):
            with self.subTest(value=value), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(b'trials = 2', b'trials = ' + value))
        with self.assertRaises(SchemaError):
            parse_protocol(TOML.replace(b'seed = 10', b'seed = -1'))

    def test_invalid_commands(self):
        for value in (b'[]', b'[""]', b'[1]', b'["/bin/python"]', b'["../tool"]',
                      b'["tool", "--data=/home/private/data"]', b'["tool", "\\u0000"]'):
            with self.subTest(value=value), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(b'["python3", "source.py"]', value))

    def test_references_and_roles(self):
        for old, new in ((b'source_ids = ["src"]', b'source_ids = ["missing"]'),
                         (b'source_ids = ["src"]', b'source_ids = ["data"]'),
                         (b'input_ids = ["data"]', b'input_ids = ["src"]'),
                         (b'variant = "only"', b'variant = "absent"'),
                         (b'case_id = "a"', b'case_id = "absent"')):
            with self.subTest(new=new), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(old, new))

    def test_malformed_materials(self):
        for value in (b'../source.py', b'/source.py', b'a//b', b'NUL.txt', b'./source.py'):
            with self.subTest(value=value), self.assertRaises(SchemaError):
                parse_protocol(TOML.replace(b'source.py"\nrole', value + b'"\nrole'))
        with self.assertRaises(SchemaError):
            parse_protocol(TOML.replace(b'role = "source"', b'role = "unknown"'))

    def test_material_aliases_including_directory_prefixes(self):
        for source, data in ((b'A.py', b'a.py'), (b'Dir/a', b'dir/b'), (b'a', b'a/b')):
            value = TOML.replace(b'path = "source.py"', b'path = "' + source + b'"')
            value = value.replace(b'path = "input.txt"', b'path = "' + data + b'"')
            with self.subTest(source=source), self.assertRaises(SchemaError):
                parse_protocol(value)

    def test_exclusions_require_reason_and_cannot_be_referenced(self):
        excluded = b'[[execution.materials]]\nid = "excluded"\npath = "excluded.txt"\nrole = "source"\ncapture = false\n'
        with self.assertRaisesRegex(SchemaError, 'reason'):
            parse_protocol(TOML + excluded)
        protocol = parse_protocol(TOML + excluded + b'reason = "unused alternative"\n')
        self.assertFalse(next(m for m in protocol.execution.materials if m.id == "excluded").capture)
        with self.assertRaisesRegex(SchemaError, 'captured source'):
            parse_protocol(TOML.replace(b'role = "source"', b'role = "source"\ncapture = false\nreason = "omitted"'))

    def test_normalization_ignores_comments_key_and_set_order(self):
        one = TOML.replace(b'variants = ["only"]', b'variants = ["only", "other"]')
        one += b'[[execution.commands]]\nvariant = "other"\nargv = ["other"]\nsource_ids = ["src"]\n'
        two = one.replace(b'variants = ["only", "other"]', b'variants = ["other", "only"]')
        two = two.replace(b'id = "demo"\nvariants', b'# comment\nid = "demo"\nvariants')
        two = two.replace(b'[[cases]]\nid = "a"\n', b'[[cases]]\nid = "a"\ntrials = 1\n')
        self.assertEqual(canonical_json(as_record(parse_protocol(one))), canonical_json(as_record(parse_protocol(two))))


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.phase2-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'experiment.toml'
        self.path.write_bytes(TOML)
        (self.root / 'source.py').write_bytes(b'raise RuntimeError("must never execute")\n')
        (self.root / 'input.txt').write_bytes(b'4\n')

    def loaded(self):
        return load_protocol(self.path)

    def plan(self):
        return build_plan(self.loaded())

    def test_single_variant_and_order_population_seeds(self):
        plan = self.plan()
        self.assertEqual(plan.variant, 'only')
        self.assertEqual([(t.case_id, t.trial, t.seed) for t in plan.trials],
                         [('b', 1, 10), ('b', 2, 11), ('a', 1, None)])
        self.assertEqual(plan.population_count, 3)
        self.assertEqual(plan.trials[0].id, 'b-trial-0001')
        self.assertEqual(plan.trials[0].argv, ('python3', 'source.py', 'input.txt'))

    def test_unknown_variant(self):
        with self.assertRaisesRegex(ProtocolError, 'unknown variant'):
            build_plan(self.loaded(), 'missing')

    def test_multi_variant_selection(self):
        loaded = load_protocol(ROOT / 'examples/sum/experiment.toml')
        with self.assertRaisesRegex(ProtocolError, 'explicit selection'):
            build_plan(loaded)
        a, b = build_plan(loaded, 'iterative'), build_plan(loaded, 'formula')
        self.assertNotEqual(a.sha256, b.sha256)
        self.assertEqual(a.protocol_sha256, b.protocol_sha256)

    def test_repeated_protocol_material_and_plan_identity(self):
        a, b = self.loaded(), self.loaded()
        pa, pb = build_plan(a), build_plan(b)
        self.assertEqual(a.normalized_bytes, b.normalized_bytes)
        self.assertEqual(a.protocol_sha256, b.protocol_sha256)
        self.assertEqual(pa.materials, pb.materials)
        self.assertEqual(pa.inventory_sha256, pb.inventory_sha256)
        self.assertEqual(pa.sha256, pb.sha256)
        self.assertEqual(capture_materials(a.project_root, a.protocol.execution.materials),
                         capture_materials(b.project_root, tuple(reversed(b.protocol.execution.materials))))

    def test_population_limit_is_explicit_and_does_not_truncate(self):
        with self.assertRaisesRegex(ProtocolError, 'planning limit'):
            build_plan(self.loaded(), max_population=2)
        self.assertEqual(build_plan(self.loaded(), max_population=3).population_count, 3)
        with self.assertRaises(ProtocolError):
            build_plan(self.loaded(), max_population=True)

    def test_immutable_objects_and_detached_serialization(self):
        loaded = self.loaded()
        with self.assertRaises(FrozenInstanceError):
            loaded.protocol.id = 'changed'
        self.assertIsInstance(loaded.protocol.cases, tuple)
        self.assertIsInstance(loaded.protocol.execution.commands[0].argv, tuple)
        plan = build_plan(loaded)
        before = plan.sha256
        value = plan.to_record()
        value['trials'][0]['argv'].append('changed')
        self.assertEqual(plan.sha256, before)

    def test_exact_definition_distinct_from_normalized_identity(self):
        before = self.loaded()
        self.path.write_bytes(TOML + b'\n# comment\n')
        after = self.loaded()
        self.assertEqual(before.protocol_sha256, after.protocol_sha256)
        self.assertNotEqual(before.definition.sha256, after.definition.sha256)
        with self.assertRaisesRegex(ProtocolError, 'changed since loading'):
            build_plan(before)

    def test_file_inventory_and_roles(self):
        plan = self.plan()
        self.assertEqual([m.path for m in plan.materials], ['input.txt', 'source.py'])
        data, source = plan.materials
        self.assertEqual((data.role, source.role), ('input', 'source'))
        self.assertEqual(data.size_bytes, 2)
        self.assertEqual(data.sha256, hashlib.sha256(b'4\n').hexdigest())
        self.assertEqual(plan.inventory_sha256, inventory_sha256(plan.materials))

    def test_portable_records_and_no_execution(self):
        with patch('subprocess.run', side_effect=AssertionError('no subprocess allowed')):
            plan = self.plan()
        raw = canonical_json(plan.to_record())
        self.assertNotIn(str(self.root).encode(), raw)
        self.assertNotIn(b'/home/', raw)
        self.assertNotIn(b'project_root', raw)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['experiment.toml', 'input.txt', 'source.py'])

    def test_relocation_preserves_plan_identity(self):
        (self.root / 'copy').mkdir()
        for name in ('experiment.toml', 'source.py', 'input.txt'):
            (self.root / 'copy' / name).write_bytes((self.root / name).read_bytes())
        self.assertEqual(self.plan().sha256, build_plan(load_protocol(self.root / 'copy/experiment.toml')).sha256)

    def test_project_root_supports_nested_definition(self):
        (self.root / 'experiments').mkdir()
        path = self.root / 'experiments/experiment.toml'
        path.write_bytes(TOML)
        plan = build_plan(load_protocol(path, project_root=self.root))
        self.assertEqual(plan.definition.path, 'experiments/experiment.toml')
        with self.assertRaises(MaterialError):
            build_plan(load_protocol(path))
        with self.assertRaisesRegex(ProtocolError, 'within project_root'):
            load_protocol(self.path, project_root=self.root / 'experiments')

    def test_missing_material(self):
        (self.root / 'input.txt').unlink()
        with self.assertRaisesRegex(MaterialError, 'input.txt: missing'):
            self.plan()

    def test_digest_size_and_missing_detection_without_refresh(self):
        plan = self.plan()
        baseline = plan.sha256
        path = self.root / 'input.txt'
        path.write_bytes(b'5\n')
        self.assertEqual([c.kind for c in compare_materials(self.root, plan.materials)], ['digest_change'])
        path.write_bytes(b'100\n')
        self.assertEqual([c.kind for c in compare_materials(self.root, plan.materials)], ['size_change', 'digest_change'])
        path.unlink()
        self.assertEqual([c.kind for c in compare_materials(self.root, plan.materials)], ['missing'])
        self.assertEqual(plan.sha256, baseline)

    def test_directory_type_change(self):
        plan = self.plan()
        path = self.root / 'input.txt'
        path.unlink()
        path.mkdir()
        self.assertEqual(compare_materials(self.root, plan.materials)[0].kind, 'type_change_or_unsupported')
        with self.assertRaises(MaterialError):
            self.plan()

    def test_symlinks_file_directory_root_and_definition(self):
        path = self.root / 'input.txt'
        path.unlink()
        path.symlink_to('source.py')
        with self.assertRaises(MaterialError):
            self.plan()
        path.unlink()
        path.write_bytes(b'4\n')
        (self.root / 'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(MaterialError):
            load_protocol(self.root / 'link/experiment.toml')
        self.path.unlink()
        self.path.symlink_to('input.txt')
        with self.assertRaises(MaterialError):
            self.loaded()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires FIFO')
    def test_special_file_rejected_without_blocking(self):
        (self.root / 'input.txt').unlink()
        os.mkfifo(self.root / 'input.txt')
        with self.assertRaises(MaterialError):
            self.plan()

    def test_new_filesystem_case_alias_detected(self):
        plan = self.plan()
        (self.root / 'INPUT.txt').write_bytes(b'4\n')
        if not {'input.txt', 'INPUT.txt'} <= {p.name for p in self.root.iterdir()}:
            self.skipTest('case-insensitive filesystem')
        self.assertEqual(compare_materials(self.root, plan.materials)[0].kind, 'ambiguous_path')
        with self.assertRaises(MaterialError):
            self.plan()

    def test_intermediate_symlink_and_new_directory_alias(self):
        (self.root / 'Data').mkdir()
        (self.root / 'Data/input.txt').write_bytes(b'4\n')
        self.path.write_bytes(TOML.replace(b'path = "input.txt"', b'path = "Data/input.txt"'))
        plan = self.plan()
        (self.root / 'data').mkdir(exist_ok=True)
        if {'data', 'Data'} <= {p.name for p in self.root.iterdir()}:
            self.assertEqual(compare_materials(self.root, plan.materials)[0].kind, 'ambiguous_path')
            (self.root / 'data').rmdir()
        (self.root / 'Link').symlink_to('Data', target_is_directory=True)
        self.path.write_bytes(TOML.replace(b'path = "input.txt"', b'path = "Link/input.txt"'))
        with self.assertRaises(MaterialError):
            self.plan()

    def test_change_during_read_is_rejected(self):
        import prooflab.materials as materials
        original = materials._check_file
        calls = 0
        def check(root, relative):
            nonlocal calls
            calls += 1
            if calls == 2:
                (root / relative).write_bytes(b'changed during read')
            return original(root, relative)
        with patch('prooflab.materials._check_file', side_effect=check):
            with self.assertRaisesRegex(MaterialError, 'changed_during_capture'):
                read_identity(self.root, 'input.txt')

    def test_definition_cannot_alias_material(self):
        self.path.write_bytes(TOML.replace(b'path = "input.txt"', b'path = "experiment.toml"'))
        with self.assertRaisesRegex(ProtocolError, 'duplicate path'):
            self.plan()

    def test_excluded_material_never_read(self):
        self.path.write_bytes(TOML + b'[[execution.materials]]\nid = "excluded"\npath = "missing.py"\nrole = "source"\ncapture = false\nreason = "unused alternative"\n')
        self.assertEqual(len(self.plan().materials), 2)

    def test_evaluator_role_is_captured_without_execution(self):
        (self.root / 'eval.py').write_bytes(b'raise RuntimeError("no")')
        self.path.write_bytes(TOML + b'[[execution.materials]]\nid = "eval"\npath = "eval.py"\nrole = "evaluator"\n')
        self.assertEqual(self.plan().materials[0].role, 'evaluator')

    def test_extended_record_verifies_with_phase1_accounting(self):
        package = self.root / 'package'
        make_package(package)
        loaded = load_protocol(ROOT / 'examples/sum/experiment.toml')
        record = as_record(loaded.protocol)
        record['cases'][0]['trials'] = 1
        write_json(package, 'protocol.json', record)
        seal(package)
        self.assertEqual(verify_package(package).state.value, 'verified')


class ProvenanceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git') and (ROOT / '.git').exists(),
                         'Git checkout metadata is optional and absent from distributions')
    def test_actual_repository_available(self):
        result = capture_provenance(ROOT, '0' * 64)
        self.assertIn(result.git_state, ('unborn', 'attached', 'detached'))
        self.assertIsInstance(result.git_dirty, bool)
        self.assertGreaterEqual(result.git_status_entries, 0)
        raw = canonical_json(as_record(result))
        self.assertNotIn(str(ROOT).encode(), raw)
        self.assertNotIn(b'/home/', raw)
        for key in ('username', 'hostname', 'diff', 'remote', 'environment', 'branch_name'):
            self.assertNotIn(key, as_record(result))

    def test_git_unavailable(self):
        with patch('prooflab.provenance.subprocess.run', side_effect=FileNotFoundError):
            self.assertEqual(capture_provenance(ROOT, '0' * 64).git_state, 'unavailable')

    def test_nonrepository(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture_provenance(directory, '0' * 64)
        self.assertEqual(result.git_state, 'unavailable')
        self.assertIsNone(result.git_dirty)

    def test_attached_detached_commit_and_rename_count(self):
        for attached in (True, False):
            replies = [(0, b'true\n'), (0, b'a' * 40 + b'\n'),
                       (0 if attached else 1, b'refs/heads/private-name\n'),
                       (0, b'R  private-new\0private-old\0?? private-file\0')]
            def run(argv, **kwargs):
                self.assertIn('core.fsmonitor=false', argv)
                self.assertEqual(kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
                code, out = replies.pop(0)
                return subprocess.CompletedProcess(argv, code, out)
            with patch('prooflab.provenance.subprocess.run', side_effect=run):
                result = capture_provenance(ROOT, '0' * 64)
            self.assertEqual(result.git_state, 'attached' if attached else 'detached')
            self.assertEqual(result.git_commit, 'a' * 40)
            self.assertEqual(result.git_status_entries, 2)
            self.assertTrue(result.git_dirty)
            self.assertNotIn(b'private', canonical_json(as_record(result)))

    def test_invalid_inventory_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'inventory_sha256'):
            capture_provenance(ROOT, 'not-a-digest')

    def test_git_timeout_is_optional(self):
        with patch('prooflab.provenance.subprocess.run', side_effect=subprocess.TimeoutExpired('git', 5)):
            self.assertEqual(capture_provenance(ROOT, '0' * 64).git_state, 'unavailable')
