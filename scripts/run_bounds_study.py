#!/usr/bin/env python3
"""Run intervalGuidedSampler on the generated bound-size benchmark files."""

from __future__ import annotations

import argparse
from pathlib import Path

from generate_ranked_linear_smt import parse_int_expr


DEFAULT_COVERAGES = tuple(range(10, 100, 10))


def _result_csv_path(smt_path: Path, coverage: int, results_suffix: str) -> Path:
    benchmark_dir = smt_path.parent
    results_dir = benchmark_dir.parent / (
        benchmark_dir.name + "_results" + results_suffix + "_cov" + str(coverage)
    )
    return results_dir / smt_path.with_suffix(".csv").name


def _iter_smt_files(benchmark_dir: Path) -> list[Path]:
    return sorted(benchmark_dir.rglob("*.smt2"))


def run_bounds_study(args: argparse.Namespace) -> int:
    results_suffix = "_interval_guided_sampler"
    executed = 0
    skipped = 0
    smt_files = _iter_smt_files(args.benchmark_dir)
    coverages = args.coverages

    if not smt_files:
        print(f"No SMT files found under {args.benchmark_dir}")
        return 0

    from solver_utils_typed import intervalGuidedSampler

    for smt_path in smt_files:
        for coverage in coverages:
            result_csv = _result_csv_path(smt_path, coverage, results_suffix)
            if result_csv.exists() and not args.force:
                print(f"Skipping {smt_path} at coverage {coverage}: already processed.")
                skipped += 1
                continue

            intervalGuidedSampler(
                smt_path,
                theta=args.theta,
                coverage=coverage,
                delta=args.delta,
                use_recursive_string_helpers=False,
                timeout=args.timeout,
                threads=args.threads,
            )
            executed += 1

    print(f"Found formulas: {len(smt_files)}")
    print(f"Coverage values: {list(coverages)}")
    print(f"Executed runs: {executed}")
    print(f"Skipped runs: {skipped}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fixed-rank bound-size study.")
    parser.add_argument("--theta", type=parse_int_expr, default=256)
    parser.add_argument("--coverages", type=parse_int_expr, nargs="+", default=DEFAULT_COVERAGES)
    parser.add_argument("--delta", type=float, default=0.0050)
    parser.add_argument("--threads", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=Path("benchmarks/ranked_linear_bounds"),
        help="folder containing generated SMT files for the bounds study",
    )
    parser.add_argument("--force", action="store_true", help="run even when the output CSV already exists")
    return parser


def main() -> int:
    return run_bounds_study(build_arg_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
