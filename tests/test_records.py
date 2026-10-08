import json
from pathlib import Path
import unittest

from prooflab.artifacts import PathError, check_aliases, logical_path
from prooflab.records import RecordError, canonical_json, strict_loads
from prooflab.schema import SCHEMAS, SchemaError, validate_record

ROOT = Path(__file__).resolve().parents[1]


class JsonTests(unittest.TestCase):
    def test_deterministic_bytes(self):
        self.assertEqual(canonical_json({"z": 2, "a": "é"}),
                         '{\n  "a": "é",\n  "z": 2\n}\n'.encode())
        self.assertEqual(canonical_json({"a": 1, "b": 2}), canonical_json({"b": 2, "a": 1}))

    def test_strict_parser(self):
        for data in (b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}', b'NaN',
                     b'Infinity', b'-Infinity', b'1e999', b'"\xff"', b'"\\ud800"',
                     b'{} trailing', b'\xef\xbb\xbf{}'):
            with self.subTest(data=data), self.assertRaises(RecordError):
                strict_loads(data)

    def test_encoder_rejects_arbitrary_objects(self):
        for value in (float("nan"), float("inf"), {1: "bad"}, (1, 2), object(), b"bytes"):
            with self.subTest(value=value), self.assertRaises(RecordError):
                canonical_json(value)

    def test_published_schemas_match_runtime(self):
        for kind, schema in SCHEMAS.items():
            path = ROOT / "schemas" / f"{kind}-v1.schema.json"
            self.assertEqual(path.read_bytes(), canonical_json(schema))
            self.assertEqual(json.loads(path.read_bytes()), schema)

    def test_bool_is_not_integer(self):
        value = {"schema": "prooflab.protocol/v1", "id": "p", "variants": ["v"],
                 "cases": [{"id": "c", "trials": True}]}
        with self.assertRaises(SchemaError):
            validate_record(value, "protocol")


class PathTests(unittest.TestCase):
    def test_reject_unsafe_paths(self):
        for path in ("", "/abs", "../escape", "a/../b", "./a", "a//b", "a/", "a\\b",
                     "C:/drive", "a.", "a ", "NUL.txt", "dir/COM1", "é.txt", "a:b"):
            with self.subTest(path=path), self.assertRaises(PathError):
                logical_path(path)

    def test_aliases(self):
        for paths in (["a", "a"], ["a", "A"], ["Dir/a", "dir/b"], ["a", "a/b"]):
            with self.subTest(paths=paths), self.assertRaises(PathError):
                check_aliases(paths)

    def test_portable_path(self):
        self.assertEqual(logical_path("artifacts/sum-result_1.txt"), "artifacts/sum-result_1.txt")
