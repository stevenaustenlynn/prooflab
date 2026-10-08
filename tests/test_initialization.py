"""Initialization is exercised only in newly allocated temporary destinations."""

import contextlib
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from prooflab.cli import main
from prooflab.ingestion import load_protocol
from prooflab.initialization import InitializationError, TEMPLATE_FILES, initialize, template_bytes
from prooflab.planning import build_plan
from tests.phase6_demo import exercise

ROOT = Path(__file__).resolve().parents[1]


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="prooflab-init-test-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.root = self.parent / "project"

    def test_new_destination_exact_files(self):
        self.assertEqual(initialize(self.root), self.root)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, template_bytes())

    def test_omitted_directory_cli(self):
        with contextlib.chdir(self.parent), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["init"]), 0)
        self.assertIn("INITIALIZED", output.getvalue())
        self.assertIn("project_directory=" + str(self.parent), output.getvalue())
        self.assertEqual(set(p.name for p in self.parent.iterdir()), set(TEMPLATE_FILES))

    def test_existing_unrelated_content_preserved(self):
        self.root.mkdir()
        (self.root / "notes").mkdir()
        (self.root / "notes/private.txt").write_bytes(b"keep")
        (self.root / "unrelated").symlink_to("missing")
        initialize(self.root)
        self.assertEqual((self.root / "notes/private.txt").read_bytes(), b"keep")
        self.assertTrue((self.root / "unrelated").is_symlink())

    def test_late_collision_no_earlier_writes(self):
        self.root.mkdir()
        (self.root / TEMPLATE_FILES[-1]).write_bytes(b"keep")
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertEqual(list(self.root.iterdir()), [self.root / TEMPLATE_FILES[-1]])
        self.assertEqual((self.root / TEMPLATE_FILES[-1]).read_bytes(), b"keep")

    def test_repeat_refuses_without_mutation(self):
        initialize(self.root)
        before = {p.name: (p.read_bytes(), p.stat()) for p in self.root.iterdir()}
        with self.assertRaises(InitializationError):
            initialize(self.root)
        for p in self.root.iterdir():
            data, info = before[p.name]
            self.assertEqual(p.read_bytes(), data)
            self.assertEqual(p.stat().st_mtime_ns, info.st_mtime_ns)
            self.assertEqual(p.stat().st_ino, info.st_ino)

    def test_directory_at_planned_file(self):
        self.root.mkdir()
        (self.root / "small.txt").mkdir()
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertEqual([p.name for p in self.root.iterdir()], ["small.txt"])

    def test_file_at_destination(self):
        self.root.write_bytes(b"keep")
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertEqual(self.root.read_bytes(), b"keep")

    def test_file_where_parent_directory_required(self):
        self.root.write_bytes(b"keep")
        with self.assertRaises(InitializationError):
            initialize(self.root / "nested")
        self.assertEqual(self.root.read_bytes(), b"keep")

    def test_missing_parent(self):
        with self.assertRaises(InitializationError):
            initialize(self.root / "nested")
        self.assertFalse(self.root.exists())

    def test_missing_current_directory_uses_cli_error_convention(self):
        self.root.mkdir()
        with contextlib.chdir(self.root):
            self.root.rmdir()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(["init"]), 2)
        self.assertIn("INIT_ERROR", err.getvalue())

    def test_unsupported_platform_fails_before_mutation(self):
        with patch("prooflab.initialization.os.name", "unsupported"):
            with self.assertRaisesRegex(InitializationError, "unavailable on this platform"):
                initialize(self.root)
        self.assertFalse(self.root.exists())

    def test_case_alias_file(self):
        self.root.mkdir()
        (self.root / "SMALL.TXT").write_bytes(b"keep")
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertEqual([p.name for p in self.root.iterdir()], ["SMALL.TXT"])

    def test_case_alias_destination(self):
        (self.parent / "PROJECT").mkdir()
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertFalse(self.root.exists())

    def test_case_alias_parent(self):
        self.root.mkdir()
        (self.parent / "PROJECT").mkdir()
        with self.assertRaises(InitializationError):
            initialize(self.root / "child")
        self.assertFalse((self.root / "child").exists())

    def test_symlink_destination(self):
        target = self.parent / "target"
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertEqual(list(target.iterdir()), [])

    def test_symlink_ancestor(self):
        self.root.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(InitializationError):
            initialize(self.root / "child")
        self.assertFalse((self.parent / "child").exists())

    def test_dangling_symlink_template_file(self):
        self.root.mkdir()
        (self.root / "small.txt").symlink_to(self.parent / "absent")
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertFalse((self.parent / "absent").exists())
        self.assertEqual([p.name for p in self.root.iterdir()], ["small.txt"])

    def test_symlink_followed_by_parent_traversal(self):
        self.root.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(InitializationError):
            initialize(self.root / ".." / "child")
        self.assertFalse((self.parent / "child").exists())

    def test_fifo_template_file(self):
        self.root.mkdir()
        os.mkfifo(self.root / "small.txt")
        with self.assertRaises(InitializationError):
            initialize(self.root)
        self.assertEqual([p.name for p in self.root.iterdir()], ["small.txt"])

    def test_fifo_destination(self):
        os.mkfifo(self.root)
        with self.assertRaises(InitializationError):
            initialize(self.root)

    def test_template_prefix_and_case_conflicts_preflight(self):
        for paths in (("a", "a/b"), ("A", "a"), ("A/b", "a/c")):
            with self.subTest(paths=paths), patch("prooflab.initialization.TEMPLATE_FILES", paths):
                with self.assertRaises(InitializationError):
                    initialize(self.root)
                self.assertFalse(self.root.exists())

    def test_exclusive_creation_race_preserves_competitor(self):
        original = os.open

        def racing_open(path, flags, *args, **kwargs):
            if path == "README.md" and flags & os.O_CREAT:
                other = original(path, flags, *args, **kwargs)
                os.write(other, b"concurrent content")
                os.close(other)
            return original(path, flags, *args, **kwargs)

        with patch("prooflab.initialization.os.open", side_effect=racing_open):
            with self.assertRaisesRegex(InitializationError, "PARTIAL"):
                initialize(self.root)
        self.assertEqual((self.root / "README.md").read_bytes(), b"concurrent content")
        self.assertEqual([p.name for p in self.root.iterdir()], ["README.md"])

    def test_directory_substitution_does_not_redirect_writes(self):
        from prooflab.initialization import _write_all
        target = self.parent / "target"
        target.mkdir()
        moved = self.parent / "moved"

        def substitute(fd, data):
            _write_all(fd, data)
            self.root.rename(moved)
            self.root.symlink_to(target, target_is_directory=True)

        with patch("prooflab.initialization._write_all", side_effect=substitute):
            with self.assertRaisesRegex(InitializationError, "PARTIAL"):
                initialize(self.root)
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual([p.name for p in moved.iterdir()], ["README.md"])

    def test_partial_write_retained_and_reported(self):
        from prooflab.initialization import _write_all
        calls = 0

        def fail(fd, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                os.write(fd, b"partial")
                raise OSError("injected disk error")
            _write_all(fd, data)

        with patch("prooflab.initialization._write_all", side_effect=fail):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(["init", str(self.root)]), 2)
        self.assertIn("PARTIAL", err.getvalue())
        self.assertIn("'README.md', 'comparison-policy.toml'", err.getvalue())
        self.assertEqual(set(p.name for p in self.root.iterdir()), set(TEMPLATE_FILES[:2]))
        self.assertEqual((self.root / "README.md").read_bytes(), template_bytes()["README.md"])
        self.assertEqual((self.root / "comparison-policy.toml").read_bytes(), b"partial")

    def test_failed_existing_directory_preserves_unrelated_content(self):
        self.root.mkdir()
        (self.root / "notes.txt").write_bytes(b"keep")
        with patch("prooflab.initialization._write_all", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(InitializationError, "new_directory=False"):
                initialize(self.root)
        self.assertEqual((self.root / "notes.txt").read_bytes(), b"keep")
        self.assertEqual(set(p.name for p in self.root.iterdir()), {"notes.txt", "README.md"})

    def test_interrupted_write_reports_partial_creation(self):
        with patch("prooflab.initialization._write_all", side_effect=KeyboardInterrupt):
            with self.assertRaisesRegex(InitializationError, "PARTIAL.*README.md"):
                initialize(self.root)

    def test_short_writes_complete_all_bytes(self):
        original = os.write
        with patch("prooflab.initialization.os.write", side_effect=lambda fd, data: original(fd, data[:7])):
            initialize(self.root)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, template_bytes())

    def test_no_progress_write_reports_partial(self):
        with patch("prooflab.initialization.os.write", return_value=0):
            with self.assertRaisesRegex(InitializationError, "short write.*PARTIAL"):
                initialize(self.root)

    def test_final_readback_detects_concurrent_edit(self):
        from prooflab.initialization import _write_all

        def change(fd, data):
            _write_all(fd, data)
            if data == template_bytes()["small.txt"]:
                (self.root / "README.md").write_bytes(b"concurrent edit")

        with patch("prooflab.initialization._write_all", side_effect=change):
            with self.assertRaisesRegex(InitializationError, "template bytes changed"):
                initialize(self.root)
        self.assertEqual((self.root / "README.md").read_bytes(), b"concurrent edit")

    def test_concurrent_change_not_deleted_on_failure(self):
        from prooflab.initialization import _write_all
        calls = 0

        def fail(fd, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "README.md").write_bytes(b"concurrent edit")
                raise OSError("injected")
            _write_all(fd, data)

        with patch("prooflab.initialization._write_all", side_effect=fail):
            with self.assertRaises(InitializationError):
                initialize(self.root)
        self.assertEqual((self.root / "README.md").read_bytes(), b"concurrent edit")

    def test_missing_resource_no_destination_created(self):
        with patch("prooflab.initialization.template_bytes", side_effect=FileNotFoundError("resource")):
            with self.assertRaisesRegex(InitializationError, "no template files created"):
                initialize(self.root)
        self.assertFalse(self.root.exists())

    def test_identical_bytes_different_destinations_and_cwd(self):
        initialize(self.root)
        with contextlib.chdir(self.parent):
            other = initialize("relocated")
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()},
                         {p.name: p.read_bytes() for p in other.iterdir()})
        for data in template_bytes().values():
            for path in (self.root, self.parent, ROOT, Path.home()):
                self.assertNotIn(str(path).encode(), data)

    def test_ingestion_materials_and_planning(self):
        initialize(self.root)
        loaded = load_protocol(self.root / "experiment.toml")
        for variant in ("iterative", "formula", "incorrect"):
            plan = build_plan(loaded, variant)
            self.assertEqual(len(plan.trials), 2)
            self.assertEqual({m.path for m in plan.materials}, {"implementations.py", "small.txt"})
            self.assertEqual(plan.trials[0].argv[0], "python3")

    def test_public_example_mirrors_authoritative_resources(self):
        for name, data in template_bytes().items():
            self.assertEqual((ROOT / "examples/sum" / name).read_bytes(), data, name)

    def test_resource_directory_contains_only_allowlist(self):
        self.assertEqual({p.name for p in (ROOT / "src/prooflab/starter").iterdir()}, set(TEMPLATE_FILES))

    def test_package_data_allowlist_matches_resources(self):
        import tomllib
        settings = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
        self.assertEqual(set(settings["package-data"]["prooflab"]), {"starter/" + n for n in TEMPLATE_FILES} |
                         {"animation_frames/truecolor/*.ans", "animation_frames/monochrome/*.ans"})
        self.assertFalse(settings["include-package-data"])
        self.assertEqual(settings["packages"]["find"]["include"], ["prooflab"])

    def test_resources_from_zip_without_checkout_imports(self):
        archive = self.parent / "application.zip"
        with ZipFile(archive, "w") as bundle:
            for module in (ROOT / "src/prooflab").glob("*.py"):
                bundle.write(module, "prooflab/" + module.name)
            for name, data in template_bytes().items():
                bundle.writestr("prooflab/starter/" + name, data)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import prooflab; "
                "assert prooflab.__file__.startswith(sys.argv[1]); "
                "from prooflab.initialization import initialize; initialize(sys.argv[2])")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(archive), str(self.root)],
                                cwd=self.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, template_bytes())

    def test_no_process_network_install_or_execution(self):
        with patch("subprocess.Popen", side_effect=AssertionError("process launch")), \
                patch("os.system", side_effect=AssertionError("shell")), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("prooflab.cli.run_experiment", side_effect=AssertionError("experiment")):
            initialize(self.root)
        self.assertEqual(set(p.name for p in self.root.iterdir()), set(TEMPLATE_FILES))

    def test_unknown_option_and_extra_arguments(self):
        for args in (["init", "--force"], ["init", str(self.root), "extra"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                main(args)
            self.assertEqual(exc.exception.code, 2)
        self.assertFalse(self.root.exists())

    def test_full_generated_cli_workflow(self):
        result = exercise(self.parent)
        self.assertEqual(result["answers"], {"iterative": [10, 10], "formula": [10, 10], "incorrect": [9, 9]})
        self.assertTrue(result["source_packages_unchanged"])
        self.assertTrue(result["templates_unchanged"])


if __name__ == "__main__":
    unittest.main()
