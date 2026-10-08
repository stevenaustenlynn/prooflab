"""Fresh CLI onboarding validation; standard library, also usable against a wheel."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def tree(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else None) for p in sorted(root.rglob("*"))}


def exercise(destination, installed_command=None):
    destination = Path(destination).absolute()
    project = destination / "project"
    assert not project.exists()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    if installed_command:
        prefix = [str(installed_command)]
    else:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        prefix = [sys.executable, "-m", "prooflab"]
    history = []

    def invoke(args, expected=0, cwd=project):
        command = [*prefix, *map(str, args)]
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
        entry = {"command": shlex.join(command), "cwd": str(cwd), "exit": result.returncode,
                 "stdout": result.stdout, "stderr": result.stderr}
        history.append(entry)
        (destination / "commands.json").write_text(json.dumps(history, indent=2) + "\n")
        assert result.returncode == expected, entry
        assert not result.stderr, entry
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    invoke(["init", project], cwd=destination)
    templates = tree(project)
    invoke(["init", destination / "second-project"], cwd=destination)
    assert templates == tree(destination / "second-project")
    runs, answers = {}, {}
    for variant, answer in (("iterative", 10), ("formula", 10), ("incorrect", 9)):
        fields = invoke(["run", "experiment.toml", "--variant", variant])
        package = Path(fields["evidence_directory"])
        assert fields["integrity"] == "VERIFIED"
        assert package.name == fields["run_id"]
        runs[variant] = package
        record = json.loads((package / "run.json").read_bytes())
        assert record["execution_state"] == "completed"
        assert record["acceptance_state"] == "not_applicable"
        values = []
        for relative in record["case_records"]:
            case = json.loads((package / relative).read_bytes())
            assert case["execution_state"] == "completed" and case["execution"]["exit_code"] == 0
            measurement, = case["measurements"]
            assert measurement["state"] == "observed" and measurement["type"] == "integer"
            values.append(measurement["value"])
        assert values == [answer, answer]
        answers[variant] = values
        invoke(["verify", package])
    before = {str(p): tree(p) for p in runs.values()}
    comparisons, reports = {}, {}
    for variant, verdict, code in (("formula", "PASS", 0), ("incorrect", "FAIL", 1)):
        fields = invoke(["compare", runs["iterative"], runs[variant], "--policy", "comparison-policy.toml"], code)
        assert fields["verdict"] == verdict and fields["integrity"] == "VERIFIED"
        package = Path(fields["evidence_directory"])
        assert package.name == fields["comparison_id"]
        comparisons[verdict] = str(package)
        before[str(package)] = tree(package)
        invoke(["verify", package])
        report = destination / (verdict.lower() + "-report.md")
        invoke(["report", package, "--output", report])
        assert verdict in report.read_text()
        reports[verdict] = str(report)
    assert all(tree(Path(p)) == data for p, data in before.items())
    assert all(hashlib.sha256((project / name).read_bytes()).hexdigest() == digest
               for name, digest in templates.items())
    result = {"project": str(project), "runs": {k: str(v) for k, v in runs.items()},
              "answers": answers, "comparisons": comparisons, "reports": reports,
              "source_packages_unchanged": True, "templates_unchanged": True,
              "different_destinations_identical": True, "template_sha256": templates,
              "environment": {k: env.get(k) for k in ("PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE")}}
    (destination / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--installed-command", type=Path)
    args = parser.parse_args()
    if args.destination is None:
        parent = Path(__file__).resolve().parent / ".prooflab/phase6"
        parent.mkdir(parents=True, exist_ok=True)
        args.destination = Path(tempfile.mkdtemp(prefix="demo-", dir=parent))
    else:
        args.destination.mkdir()  # Refuse reuse of a retained demonstration.
    result = exercise(args.destination, args.installed_command)
    print(json.dumps(result, indent=2))
    print("commands=" + str(args.destination / "commands.json"))
