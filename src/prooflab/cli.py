"""Safe initialization, serial execution, comparison, reporting and verification."""

import argparse
from collections.abc import Sequence
import sys

from . import __version__
from .protocol import IntegrityState
from .verify import verify_package
from .runner import run_experiment

EXIT_CODES = {IntegrityState.VERIFIED: 0, IntegrityState.INVALID: 1,
              IntegrityState.INCOMPLETE: 3, IntegrityState.UNSUPPORTED: 4}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prooflab", description="Local experiment evidence and offline verification")
    parser.add_argument("--version", action="version", version=f"prooflab {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="verify a retained package offline")
    verify.add_argument("run_directory")
    verify.add_argument("--expect-sha256", help="externally supplied manifest SHA-256")
    run = commands.add_parser("run", help="execute serially from snapshots and seal evidence")
    run.add_argument("experiment")
    run.add_argument("--variant")
    run.add_argument("--project-root", help="explicit local root for declared material paths")
    run.add_argument("--animation", choices=("off", "truecolor", "monochrome"), default="off",
                     help="optional terminal presentation (default: off; requires at least 33x37 cells)")
    compare = commands.add_parser("compare", help="compare verified retained runs under an explicit policy")
    compare.add_argument("baseline_run")
    compare.add_argument("candidate_run")
    compare.add_argument("--policy", required=True)
    report = commands.add_parser("report", help="render verified evidence as Markdown")
    report.add_argument("package_directory")
    report.add_argument("--output", help="create a new external Markdown file (never overwrite)")
    init = commands.add_parser("init", help="create a starter project without overwriting files")
    init.add_argument("directory", nargs="?", default=".", help="destination (default: current directory)")
    args = parser.parse_args(argv)
    if args.command == "init":
        from .initialization import InitializationError, initialize
        try:
            directory = initialize(args.directory)
        except InitializationError as exc:
            print("INIT_ERROR: " + str(exc), file=sys.stderr)
            return 2
        print("INITIALIZED")
        print(f"project_directory={directory}")
        return 0
    if args.command == "report":
        from .report import ReportError, render_report, write_report
        try:
            markdown = render_report(args.package_directory)
        except ReportError as exc:
            print(exc.state.value.upper() + ": " + str(exc), file=sys.stderr)
            return EXIT_CODES[exc.state]
        try:
            if args.output is None:
                sys.stdout.write(markdown)
                sys.stdout.flush()
            else:
                write_report(args.package_directory, args.output, markdown)
        except (OSError, ValueError, RuntimeError):
            print("REPORT_WRITE_ERROR: destination must be a new external file with an existing parent; unable to write report", file=sys.stderr)
            return 2
        return 0
    if args.command == "compare":
        from .comparison import ComparisonError, compare_runs
        try:
            result = compare_runs(args.baseline_run, args.candidate_run, args.policy)
        except ComparisonError as exc:
            print("INVALID_COMPARISON: " + str(exc), file=sys.stderr)
            return 2
        except (ValueError, OSError):
            print("INVALID_COMPARISON: invalid policy, input, or output directory", file=sys.stderr)
            return 2
        record = result.record
        print("SEALED" if result.sealed else "UNSEALED")
        print(f"comparison_id={record['id']}")
        for role in ("baseline", "candidate"):
            print(f"{role}_run_id={record[role]['run_id']}")
        print(f"policy_id={record['policy']['id']}\npaired_trials={record['paired_trials']}")
        for state in ("PASS", "FAIL", "INCONCLUSIVE", "NOT_APPLICABLE"):
            print(f"required_rules_{state.lower()}=" + str(sum(
                r['required'] and r['state'] == state for r in record['rules'])))
        print(f"verdict={record['verdict']}")
        for check in record['compatibility']['checks']:
            if not check['passed']:
                print("incompatible=" + check['id'])
        print(f"evidence_directory={result.directory}")
        print("integrity=" + ("VERIFIED" if result.sealed else "NOT_VERIFIED"))
        return result.exit_code
    if args.command == "run":
        try:
            from .animation import TerminalAnimation
            with TerminalAnimation(args.animation):
                result = run_experiment(args.experiment, args.variant, project_root=args.project_root)
        except (ValueError, OSError):
            print("INVALID_CONFIGURATION: unable to validate definition, materials, variant, or output directory", file=sys.stderr)
            return 2
        print("SEALED" if result.sealed else "UNSEALED")
        print(f"run_id={result.run_id}\nvariant={result.variant}\nplanned_trials={result.planned}")
        print(" ".join(f"{state}={count}" for state, count in result.counts.items()))
        print(f"evidence_directory={result.directory}")
        print("integrity=" + ("VERIFIED" if result.sealed else "NOT_VERIFIED"))
        if result.reason:
            print(f"reason={result.reason}")
        return result.exit_code
    result = verify_package(args.run_directory, args.expect_sha256)
    print(result.state.value.upper())
    if result.manifest_sha256:
        print(f"manifest_sha256={result.manifest_sha256}")
    for finding in result.findings:
        print(f"{finding.state.value.upper()}: {finding.message}")
    return EXIT_CODES[result.state]
