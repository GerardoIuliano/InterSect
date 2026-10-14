#!/usr/bin/env python3
"""Run InterSect on the generated Int solution-space formulas."""

from __future__ import annotations

import argparse
from pathlib import Path

from generate_ranked_linear_smt import parse_int_expr


DEFAULT_COVERAGES = (10, 20, 30, 40, 50, 60, 70, 80, 90)
DEFAULT_BENCHMARK_DIR = Path("benchmarks/solution_space")
DEFAULT_RESULTS_ROOT = Path("benchmarks/solution_space_results")
DEFAULT_UNIVERSE_UPPER_BOUND = 2**256 - 1


def _iter_smt_files(benchmark_dir: Path) -> list[Path]:
    return sorted(benchmark_dir.glob("*.smt2"))


def _result_csv_path(
    smt_path: Path,
    coverage: int,
    results_suffix: str,
    results_root: Path | None,
) -> Path:
    parent_dir = results_root if results_root is not None else smt_path.parent.parent
    results_dir = parent_dir / (
        smt_path.parent.name + "_results" + results_suffix + "_cov" + str(coverage)
    )
    return results_dir / smt_path.with_suffix(".csv").name


def run_solution_space_study(args: argparse.Namespace) -> int:
    if args.results_root is not None and str(args.results_root).lower() == "none":
        args.results_root = None

    smt_files = _iter_smt_files(args.benchmark_dir)
    if not smt_files:
        print(f"No SMT files found in {args.benchmark_dir}")
        return 0

    planned = 0
    executed = 0
    skipped = 0

    if args.dry_run:
        sampler = None
    else:
        from solver_utils_typed import intervalGuidedSampler

        sampler = intervalGuidedSampler

    for coverage in args.coverages:
        for smt_path in smt_files:
            planned += 1
            result_csv = _result_csv_path(
                smt_path,
                coverage,
                args.results_suffix,
                args.results_root,
            )
            if result_csv.exists() and not args.force:
                print(f"Skipping {smt_path} at coverage {coverage}: already processed.")
                skipped += 1
                continue

            if args.dry_run:
                print(f"Would run {smt_path} at coverage {coverage} -> {result_csv}")
                continue

            sampler(
                smt_path,
                theta=args.theta,
                coverage=coverage,
                delta=args.delta,
                U=args.universe_upper_bound,
                timeout=600,
                num_samples=100,
                results_suffix=args.results_suffix,
                results_root=args.results_root,
                threads=args.threads,
            )
            executed += 1

    print(f"Found formulas: {len(smt_files)}")
    print(f"Coverage values: {list(args.coverages)}")
    print(f"Planned runs: {planned}")
    print(f"Executed runs: {executed}")
    print(f"Skipped runs: {skipped}")
    if args.dry_run:
        print("Dry run only: InterSect was not executed.")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run InterSect on generated interval-region Int formulas."
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=DEFAULT_BENCHMARK_DIR,
        help="directory containing generated .smt2 formulas",
    )
    parser.add_argument(
        "--coverages",
        type=parse_int_expr,
        nargs="+",
        default=DEFAULT_COVERAGES,
        help="coverage values to run",
    )
    parser.add_argument("--theta", type=parse_int_expr, default=256)
    parser.add_argument("--delta", type=float, default=0.0050)
    parser.add_argument(
        "--universe-upper-bound",
        type=parse_int_expr,
        default=DEFAULT_UNIVERSE_UPPER_BOUND,
        help="inclusive finite universe upper bound passed to InterSect",
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--threads", type=int, default=60)
    parser.add_argument(
        "--results-suffix",
        default="_interval_guided_sampler",
        help="suffix used by InterSect when creating result directories",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=(
            "root directory for result coverage folders; use 'none' to preserve "
            "the legacy location next to the benchmark directory"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even when the expected output CSV already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the runs that would be executed without launching InterSect",
    )
    return parser


def main() -> int:
    return run_solution_space_study(build_arg_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
