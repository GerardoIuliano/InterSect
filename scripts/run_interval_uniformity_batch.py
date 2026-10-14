#!/usr/bin/env python3
"""
Batch runner for interval uniformity tests over a directory of CSV files.

This script walks a directory recursively, finds all CSV files, and runs the
interval uniformity test for each row/variable pair in each file.

It writes one combined summary CSV with one row per test case.

Example:
    python scripts/run_interval_uniformity_batch.py \
        --root SamplerResults/Integer \
        --column mask_var_pairs \
        --metric intersections \
        --bins 64 \
        --out-csv interval_uniformity_summary.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from pathlib import Path
from typing import Iterable

MODULE_PATH = Path(__file__).resolve().with_name("check_interval_coverage_uniformity.py")


def load_helper_module():
    spec = importlib.util.spec_from_file_location(
        "check_interval_coverage_uniformity", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load helper module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load_helper_module()
build_bin_boundaries = helper.build_bin_boundaries
compute_result_for_case = helper.compute_result_for_case
load_test_cases_from_csv = helper.load_test_cases_from_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        required=True,
        help="Root directory to scan recursively for CSV files.",
    )
    parser.add_argument(
        "--column",
        default="mask_var_pairs",
        help="CSV column containing the interval data (default: mask_var_pairs).",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional variable-name filter. If omitted, all variables are tested.",
    )
    parser.add_argument(
        "--row",
        type=int,
        default=None,
        help="Optional zero-based row filter applied to every CSV file.",
    )
    parser.add_argument(
        "--metric",
        choices=("intersections", "coverage"),
        default="intersections",
        help="What to count per bin (default: intersections).",
    )
    parser.add_argument(
        "--bins",
        type=int,
        default=64,
        help="Number of bins to use for each test case.",
    )
    parser.add_argument(
        "--domain-bits",
        type=int,
        default=256,
        help="Domain size is 2^bits (default: 256).",
    )
    parser.add_argument(
        "--out-csv",
        required=True,
        help="Path to the combined summary CSV.",
    )
    parser.add_argument(
        "--per-file-outdir",
        default=None,
        help="Optional directory to store one summary CSV per input file.",
    )
    return parser.parse_args()


def find_csv_files(root: str) -> list[Path]:
    root_path = Path(root)
    if not root_path.exists():
        raise SystemExit(f"[ERRORE] Root directory not found: {root}")
    return sorted(p for p in root_path.rglob("*.csv") if p.is_file())


def write_summary_csv(out_path: str, rows: Iterable[dict[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        raise SystemExit("[ERRORE] Nessun test case trovato.")

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "source_csv",
                "row_index",
                "variable",
                "n_intervals",
                "total_observed",
                "chi_square",
                "degrees_of_freedom",
                "p_value",
                "min_expected",
                "max_expected",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["source_csv"],
                    row["row_index"],
                    row["variable"],
                    row["n_intervals"],
                    row["total_observed"],
                    f"{row['chi_square']:.20f}",
                    row["degrees_of_freedom"],
                    f"{row['p_value']:.20g}",
                    f"{row['min_expected']:.20f}",
                    f"{row['max_expected']:.20f}",
                ]
            )


def write_per_file_summary(
    outdir: str, source_csv: str, rows: list[dict[str, object]]
) -> None:
    out_path = Path(outdir)
    out_path.mkdir(parents=True, exist_ok=True)
    safe_name = Path(source_csv).name
    target = out_path / f"{safe_name}.summary.csv"
    write_summary_csv(str(target), rows)


def main() -> None:
    args = parse_args()
    csv_files = find_csv_files(args.root)
    if not csv_files:
        raise SystemExit(f"[ERRORE] Nessun CSV trovato sotto {args.root}")

    domain_size = 1 << args.domain_bits
    boundaries = build_bin_boundaries(domain_size, args.bins)
    all_rows: list[dict[str, object]] = []
    failures: list[tuple[str, str]] = []

    print(f"[INFO] CSV files found : {len(csv_files)}")
    print(f"[INFO] Metric          : {args.metric}")
    print(f"[INFO] Bins            : {args.bins}")
    print(f"[INFO] Domain bits     : {args.domain_bits}")

    for idx, csv_path in enumerate(csv_files, start=1):
        print(f"[INFO] Processing {idx}/{len(csv_files)}: {csv_path}")
        try:
            cases, used_column = load_test_cases_from_csv(
                str(csv_path), args.column, args.row, args.label
            )
            file_rows: list[dict[str, object]] = []
            for case in cases:
                result = compute_result_for_case(
                    int(case["row_index"]),
                    str(case["variable"]),
                    case["intervals"],  # type: ignore[arg-type]
                    boundaries,
                    domain_size,
                    args.metric,
                )
                result["source_csv"] = str(csv_path)
                file_rows.append(result)
                all_rows.append(result)

            print(
                f"[INFO]   column={used_column} "
                f"cases={len(file_rows)}"
            )

            if args.per_file_outdir and file_rows:
                write_per_file_summary(args.per_file_outdir, str(csv_path), file_rows)
        except Exception as exc:
            failures.append((str(csv_path), str(exc)))
            print(f"[WARN]   skipped: {exc}")

    if not all_rows:
        raise SystemExit("[ERRORE] Nessun test case valido trovato.")

    write_summary_csv(args.out_csv, all_rows)
    print(f"[INFO] Wrote combined summary to {args.out_csv}")
    if failures:
        print(f"[WARN] Failed files   : {len(failures)}")
        for path, msg in failures[:10]:
            print(f"[WARN]   {path} :: {msg}")


if __name__ == "__main__":
    main()
