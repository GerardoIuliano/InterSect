#!/usr/bin/env python3
"""
Check whether interval intersection counts are uniform over the domain [0, 2^bits).

The script expects a CSV where one cell contains a Python-literal interval
structure such as:

    [('X', [(start1, end1), (start2, end2), ...])]

It also accepts a plain list of intervals:

    [(start1, end1), (start2, end2), ...]

The default test works on *number of intervals intersecting each bin*:
  1. split the domain into equal-width bins
  2. count how many intervals intersect each bin
  3. compare observed vs expected bin counts under a uniform null

You can switch back to covered length with `--metric coverage`.

Examples:
    python scripts/check_interval_coverage_uniformity.py \
        --csv data.csv \
        --column intervals

    python scripts/check_interval_coverage_uniformity.py \
        --csv data.csv \
        --column intervals \
        --bins 64 \
        --out-csv bin_coverage.csv

    python scripts/check_interval_coverage_uniformity.py \
        --csv data.csv \
        --column intervals \
        --label X
"""

from __future__ import annotations

import argparse
import ast
import csv
import math
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Iterable, Iterator, Sequence


def increase_csv_field_limit() -> None:
    """
    Python's csv module defaults to a small field limit on some platforms.
    Interval cells can be very large, so raise the limit proactively.
    """
    try:
        csv.field_size_limit(sys.maxsize)
        return
    except OverflowError:
        pass

    limit = 131072
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit * 10)


increase_csv_field_limit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", required=True, help="Path to the CSV file.")
    parser.add_argument(
        "--column",
        default=None,
        help="Name of the CSV column containing the interval cell. If omitted, "
        "the script tries to infer it from the first row.",
    )
    parser.add_argument(
        "--row",
        type=int,
        default=None,
        help="Optional zero-based row index to analyze. If omitted, all rows are "
        "parsed.",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional variable-name filter. If omitted, all variables are tested.",
    )
    parser.add_argument(
        "--bins",
        type=int,
        default=64,
        help="Number of equal-width bins over the domain (default: 64).",
    )
    parser.add_argument(
        "--metric",
        choices=("intersections", "coverage"),
        default="intersections",
        help="What to count per bin: interval intersections (default) or covered length.",
    )
    parser.add_argument(
        "--domain-bits",
        type=int,
        default=256,
        help="Domain size is 2^bits (default: 256).",
    )
    parser.add_argument(
        "--out-csv",
        default=None,
        help="Optional path to write the per-(row, variable) summary CSV.",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=10,
        help="How many test cases to preview on stdout (default: 10).",
    )
    return parser.parse_args()


def is_int_like(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_interval_pair(value: object) -> bool:
    return (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and is_int_like(value[0])
        and is_int_like(value[1])
    )


def extract_intervals(obj: object) -> Iterator[tuple[int, int]]:
    """
    Recursively extract (start, end) pairs from nested lists/tuples.

    Supports both:
      - [(start, end), ...]
      - [('X', [(start, end), ...]), ...]
    """
    if is_interval_pair(obj):
        start, end = obj  # type: ignore[misc]
        yield int(start), int(end)
        return

    if isinstance(obj, (list, tuple)):
        for item in obj:
            yield from extract_intervals(item)


def parse_interval_cell(cell: str) -> list[tuple[int, int]]:
    parsed = ast.literal_eval(cell)
    intervals = list(extract_intervals(parsed))
    if not intervals:
        raise ValueError("No intervals found in the provided cell.")
    return intervals


def parse_variable_interval_cell(
    cell: str, label: str | None = None
) -> list[tuple[str, list[tuple[int, int]]]]:
    """
    Parse a mask_var_pairs-like cell into one test case per variable.

    Expected shape:
        [('x6_plus', [(start, end), ...]), ('x8_plus', [(start, end), ...]), ...]

    Also accepts a plain interval list and assigns it to '__all__'.
    """
    parsed = ast.literal_eval(cell)
    cases: list[tuple[str, list[tuple[int, int]]]] = []

    if isinstance(parsed, (list, tuple)):
        for item in parsed:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], (list, tuple))
            ):
                var_name = item[0]
                if label is not None and var_name != label:
                    continue
                intervals = list(extract_intervals(item[1]))
                if intervals:
                    cases.append((var_name, intervals))

    if cases:
        return cases

    intervals = parse_interval_cell(cell)
    if label is None or label == "__all__":
        return [("__all__", intervals)]
    return []


def infer_interval_column(fieldnames: Sequence[str]) -> str:
    candidates = list(fieldnames)
    if not candidates:
        raise ValueError("CSV has no header columns.")
    preferred = [
        "intervals",
        "interval_list",
        "mask_intervals",
        "mask",
        "coverage",
    ]
    for name in preferred:
        if name in candidates:
            return name
    return candidates[0]


def load_test_cases_from_csv(
    path: str,
    column: str | None,
    row_index: int | None,
    label: str | None,
) -> tuple[list[dict[str, object]], str]:
    cases: list[dict[str, object]] = []
    used_column = column
    matched_rows = 0
    matched_cases = 0

    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("CSV appears to be empty or missing a header.")

        if used_column is None:
            used_column = infer_interval_column(reader.fieldnames)

        for idx, row in enumerate(reader):
            if row_index is not None and idx != row_index:
                continue

            cell = row.get(used_column, "")
            if cell is None or not str(cell).strip():
                if column is not None:
                    raise ValueError(
                        f"Row {idx} has an empty value in column '{used_column}'."
                    )
                continue

            try:
                row_cases = parse_variable_interval_cell(str(cell), label=label)
                if row_cases:
                    matched_rows += 1
                    for var_name, intervals in row_cases:
                        matched_cases += 1
                        cases.append(
                            {
                                "row_index": idx,
                                "variable": var_name,
                                "intervals": intervals,
                            }
                        )
            except Exception as exc:
                if column is not None:
                    raise ValueError(
                        f"Failed to parse row {idx} column '{used_column}': {exc}"
                    ) from exc

        if not cases and column is None:
            # Fallback: try every column in the first matching row.
            fh.seek(0)
            reader = csv.DictReader(fh)
            for idx, row in enumerate(reader):
                if row_index is not None and idx != row_index:
                    continue
                for candidate in reader.fieldnames:
                    cell = row.get(candidate, "")
                    if cell is None or not str(cell).strip():
                        continue
                    try:
                        row_cases = parse_variable_interval_cell(str(cell), label=label)
                        used_column = candidate
                        if row_cases:
                            matched_rows = 1
                            for var_name, intervals in row_cases:
                                matched_cases += 1
                                cases.append(
                                    {
                                        "row_index": idx,
                                        "variable": var_name,
                                        "intervals": intervals,
                                    }
                                )
                        break
                    except Exception:
                        continue
                if cases:
                    break

    if not cases:
        raise ValueError(
            "No intervals could be parsed. Try passing --column explicitly."
        )

    assert used_column is not None
    return cases, used_column


def clamp_interval(
    start: int, end: int, domain_size: int
) -> tuple[int, int] | None:
    if end <= start:
        return None
    start = max(0, min(start, domain_size))
    end = max(0, min(end, domain_size))
    if end <= start:
        return None
    return start, end


def build_bin_boundaries(domain_size: int, bins: int) -> list[int]:
    return [(domain_size * i) // bins for i in range(bins + 1)]


def coverage_per_bin(
    intervals: Iterable[tuple[int, int]],
    boundaries: Sequence[int],
    domain_size: int,
) -> list[int]:
    bins = len(boundaries) - 1
    coverage = [0] * bins

    for start, end in intervals:
        clipped = clamp_interval(start, end, domain_size)
        if clipped is None:
            continue
        start, end = clipped

        left = max(0, bisect_right(boundaries, start) - 1)
        right = min(bins - 1, bisect_left(boundaries, end) - 1)
        if right < left:
            continue

        for i in range(left, right + 1):
            overlap_start = max(start, boundaries[i])
            overlap_end = min(end, boundaries[i + 1])
            if overlap_end > overlap_start:
                coverage[i] += overlap_end - overlap_start

    return coverage


def intersections_per_bin(
    intervals: Iterable[tuple[int, int]],
    boundaries: Sequence[int],
    domain_size: int,
) -> list[int]:
    bins = len(boundaries) - 1
    counts = [0] * bins

    for start, end in intervals:
        clipped = clamp_interval(start, end, domain_size)
        if clipped is None:
            continue
        start, end = clipped

        left = max(0, bisect_right(boundaries, start) - 1)
        right = min(bins - 1, bisect_left(boundaries, end) - 1)
        if right < left:
            continue

        for i in range(left, right + 1):
            counts[i] += 1

    return counts


def chi_square_statistic(observed: Sequence[float], expected: Sequence[float]) -> float:
    stat = 0.0
    for o, e in zip(observed, expected):
        if e <= 0:
            raise ValueError("Expected count must be positive in every bin.")
        diff = o - e
        stat += (diff * diff) / e
    return stat


def gammaincc(a: float, x: float) -> float:
    """
    Regularized upper incomplete gamma function Q(a, x).
    Implementation adapted from standard series / continued fraction methods.
    """
    if a <= 0.0 or x < 0.0:
        raise ValueError("Invalid arguments for gammaincc.")
    if x == 0.0:
        return 1.0

    eps = 3e-14
    itmax = 200
    gln = math.lgamma(a)

    # Use the series for P(a, x) when x < a + 1, then return 1 - P.
    if x < a + 1.0:
        ap = a
        summ = 1.0 / a
        delta = summ
        for _ in range(itmax):
            ap += 1.0
            delta *= x / ap
            summ += delta
            if abs(delta) < abs(summ) * eps:
                break
        p = summ * math.exp(-x + a * math.log(x) - gln)
        return max(0.0, min(1.0, 1.0 - p))

    # Continued fraction for Q(a, x)
    b = x + 1.0 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, itmax + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    q = math.exp(-x + a * math.log(x) - gln) * h
    return max(0.0, min(1.0, q))


def chi_square_p_value(stat: float, df: int) -> float:
    return gammaincc(df / 2.0, stat / 2.0)


def write_bin_csv(
    out_path: str,
    boundaries: Sequence[int],
    observed: Sequence[int],
    expected: Sequence[float],
) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "bin_index",
                "bin_start",
                "bin_end",
                "bin_width",
                "observed_coverage",
                "expected_coverage",
                "ratio_observed_expected",
            ]
        )
        for i, (obs, exp) in enumerate(zip(observed, expected)):
            width = boundaries[i + 1] - boundaries[i]
            ratio = (obs / exp) if exp else float("nan")
            writer.writerow(
                [
                    i,
                    boundaries[i],
                    boundaries[i + 1],
                    width,
                    obs,
                    f"{exp:.20f}",
                    f"{ratio:.20f}",
                ]
            )


def compute_result_for_case(
    row_index: int,
    variable: str,
    intervals: Sequence[tuple[int, int]],
    boundaries: Sequence[int],
    domain_size: int,
    metric: str,
) -> dict[str, object]:
    if metric == "intersections":
        observed = intersections_per_bin(intervals, boundaries, domain_size)
    else:
        observed = coverage_per_bin(intervals, boundaries, domain_size)

    bin_widths = [boundaries[i + 1] - boundaries[i] for i in range(len(boundaries) - 1)]
    total_observed = sum(observed)
    if metric == "intersections":
        expected = [total_observed / len(bin_widths) for _ in bin_widths]
    else:
        expected = [(total_observed * width) / domain_size for width in bin_widths]

    stat = chi_square_statistic(observed, expected)
    df = len(observed) - 1
    p_value = chi_square_p_value(stat, df)
    return {
        "row_index": row_index,
        "variable": variable,
        "n_intervals": len(intervals),
        "total_observed": total_observed,
        "chi_square": stat,
        "degrees_of_freedom": df,
        "p_value": p_value,
        "min_expected": min(expected),
        "max_expected": max(expected),
        "observed": observed,
        "expected": expected,
    }


def write_results_csv(out_path: str, results: Sequence[dict[str, object]]) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
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
        for result in results:
            writer.writerow(
                [
                    result["row_index"],
                    result["variable"],
                    result["n_intervals"],
                    result["total_observed"],
                    f"{result['chi_square']:.20f}",
                    result["degrees_of_freedom"],
                    f"{result['p_value']:.20g}",
                    f"{result['min_expected']:.20f}",
                    f"{result['max_expected']:.20f}",
                ]
            )


def main() -> None:
    args = parse_args()
    domain_size = 1 << args.domain_bits
    if args.bins <= 1:
        raise SystemExit("--bins must be greater than 1.")
    if args.bins > domain_size:
        raise SystemExit("--bins cannot exceed the domain size.")

    cases, used_column = load_test_cases_from_csv(
        args.csv, args.column, args.row, args.label
    )

    boundaries = build_bin_boundaries(domain_size, args.bins)
    results = [
        compute_result_for_case(
            int(case["row_index"]),
            str(case["variable"]),
            case["intervals"],  # type: ignore[arg-type]
            boundaries,
            domain_size,
            args.metric,
        )
        for case in cases
    ]

    print(f"[INFO] CSV file              : {args.csv}")
    print(f"[INFO] Interval column       : {used_column}")
    print(f"[INFO] Metric                : {args.metric}")
    if args.row is not None:
        print(f"[INFO] Selected row          : {args.row}")
    if args.label is not None:
        print(f"[INFO] Label filter           : {args.label}")
    print(f"[INFO] Rows with parsed data  : {len(set(int(c['row_index']) for c in cases))}")
    print(f"[INFO] Test cases             : {len(results)}")
    print(f"[INFO] Domain size            : 2^{args.domain_bits}")
    print(f"[INFO] Number of bins         : {args.bins}")
    if results:
        p_values = [float(r["p_value"]) for r in results]
        print(f"[INFO] p-value range          : min={min(p_values):.12g}, max={max(p_values):.12g}")
        significant = sum(1 for p in p_values if p < 0.05)
        print(f"[INFO] p < 0.05 cases         : {significant}/{len(p_values)}")

    preview = min(args.preview, len(results))
    print("\n[PREVIEW] First test cases:")
    for result in results[:preview]:
        print(
            f"  row={result['row_index']:>4} var={result['variable']:<16} "
            f"n={result['n_intervals']:>4} "
            f"chi2={float(result['chi_square']):.3f} "
            f"p={float(result['p_value']):.12g} "
            f"total={result['total_observed']}"
        )

    if args.out_csv:
        out_path = Path(args.out_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_results_csv(str(out_path), results)
        print(f"\n[INFO] Wrote per-bin table to {out_path}")


if __name__ == "__main__":
    main()
