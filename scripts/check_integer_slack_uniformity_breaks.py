#!/usr/bin/env python3
"""Analyze integer slack CSVs and report where mask uniformity breaks.

For every CSV in:
    OurResults/Integer/slacks_results_cov10/*.csv

the script inspects the `mask_var_pairs` column, parses each
`(variable, list_of_intervals)` pair, and checks whether each variable mask
is uniform using the same heuristic employed by `create_mask_with_coverage`
in `scripts/solver_utils.py`.

The output contains:
  - the first row where at least one variable mask is non-uniform
  - the first row where all variable masks are non-uniform
  - the complete list of row numbers satisfying each condition

Row numbers are 1-based with respect to the CSV data rows (header excluded).
"""

from __future__ import annotations

import ast
import csv
from collections import Counter
from math import erfc, exp, sqrt
from pathlib import Path
from typing import Iterable
import random
import sys

from scipy.stats import ks_2samp


BASE_DIR = Path("/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL/OurResults/Integer/slacks_results_cov90")
OUTPUT_FILE = BASE_DIR / "uniformity_break_rows_cov90.csv"

UINT256_DOMAIN_START = 0
UINT256_DOMAIN_END = 2**256 - 1

csv.field_size_limit(sys.maxsize)


def merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    normalized = sorted((int(a), int(b)) for a, b in intervals)
    if not normalized:
        return []

    merged: list[tuple[int, int]] = [normalized[0]]
    for start, end in normalized[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def multiset_from_intervals_2(
    intervals: Iterable[tuple[int, int]],
    min_value: int,
    max_value: int,
) -> Counter:
    """Replicates the heuristic used by `solver_utils.py`."""

    S = Counter()
    domain_span = max_value - min_value
    if domain_span <= 0:
        return S

    for a, b in intervals:
        num_samples = max(5, int(1000 * (b - a) / domain_span))
        norm_a = (a - min_value) / domain_span
        norm_b = (b - min_value) / domain_span
        if num_samples == 1:
            S[round(norm_a, 16)] += 1
            continue
        step = (norm_b - norm_a) / (num_samples - 1)
        for i in range(num_samples):
            x = norm_a + step * i
            S[round(x, 16)] += 1
    return S


def uniform_multiset(size: int) -> Counter:
    """Random ideal multiset, matching `solver_utils.py`."""

    return Counter(round(random.random(), 16) for _ in range(size))


def expand_multiset(M: Counter) -> list[float]:
    arr = []
    for x, count in M.items():
        arr.extend([x] * count)
    return arr


def empirical_mannwhitneyu(sample_a: list[float], sample_b: list[float]) -> tuple[float, float]:
    """Pure-Python Mann-Whitney U with normal approximation."""

    n1 = len(sample_a)
    n2 = len(sample_b)
    if n1 == 0 or n2 == 0:
        return 0.0, 0.0

    combined = sorted([(value, 0) for value in sample_a] + [(value, 1) for value in sample_b], key=lambda x: x[0])
    ranks = [0.0] * len(combined)

    idx = 0
    while idx < len(combined):
        j = idx
        while j < len(combined) and combined[j][0] == combined[idx][0]:
            j += 1
        avg_rank = (idx + 1 + j) / 2.0
        for k in range(idx, j):
            ranks[k] = avg_rank
        idx = j

    rank_sum_a = sum(rank for rank, (_, group) in zip(ranks, combined) if group == 0)
    u1 = rank_sum_a - n1 * (n1 + 1) / 2.0
    mean_u = n1 * n2 / 2.0
    sd_u = sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if sd_u == 0:
        return u1, 0.0
    z = (u1 - mean_u) / sd_u
    p_value = erfc(abs(z) / sqrt(2.0))
    return u1, p_value


def mask_is_uniform(mask: list[tuple[int, int]]) -> bool:
    merged = merge_intervals(mask)
    if not merged:
        return False

    real_multiset = multiset_from_intervals_2(merged, UINT256_DOMAIN_START, UINT256_DOMAIN_END)
    if not real_multiset:
        return False

    ideal_multiset = uniform_multiset(sum(real_multiset.values()))
    real_samples = expand_multiset(real_multiset)
    ideal_samples = expand_multiset(ideal_multiset)

    use_mwu = all((end - start) <= 1 for start, end in merged)
    if use_mwu:
        _, p_value = empirical_mannwhitneyu(real_samples, ideal_samples)
        return p_value > 0.05

    ks_stat, p_value = ks_2samp(real_samples, ideal_samples)
    return ks_stat < 0.10 and p_value > 0.05


def parse_mask_var_pairs(raw_value: str) -> list[tuple[str, list[tuple[int, int]]]]:
    parsed = ast.literal_eval(raw_value)
    result: list[tuple[str, list[tuple[int, int]]]] = []
    for item in parsed:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        var_name, intervals = item
        if not isinstance(intervals, list):
            continue
        cleaned: list[tuple[int, int]] = []
        for interval in intervals:
            if isinstance(interval, (list, tuple)) and len(interval) == 2:
                cleaned.append((int(interval[0]), int(interval[1])))
        result.append((str(var_name), cleaned))
    return result


def analyze_csv(csv_path: Path) -> dict:
    rows_any_non_uniform: list[int] = []
    rows_all_non_uniform: list[int] = []
    first_any_non_uniform = None
    first_all_non_uniform = None
    total_rows = 0

    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row_number, row in enumerate(reader, start=1):
            total_rows += 1
            raw_value = row.get("mask_var_pairs", "")
            if not raw_value or not str(raw_value).strip():
                continue

            try:
                mask_var_pairs = parse_mask_var_pairs(raw_value)
            except (ValueError, SyntaxError):
                continue

            if not mask_var_pairs:
                continue

            uniform_flags = [mask_is_uniform(intervals) for _, intervals in mask_var_pairs]
            any_non_uniform = not all(uniform_flags)
            all_non_uniform = all(not flag for flag in uniform_flags)

            if any_non_uniform:
                rows_any_non_uniform.append(row_number)
                if first_any_non_uniform is None:
                    first_any_non_uniform = row_number

            if all_non_uniform:
                rows_all_non_uniform.append(row_number)
                if first_all_non_uniform is None:
                    first_all_non_uniform = row_number

    return {
        "filename": csv_path.name,
        "total_rows": total_rows,
        "first_row_any_non_uniform": first_any_non_uniform,
        "first_row_all_non_uniform": first_all_non_uniform,
        "rows_any_non_uniform": ";".join(map(str, rows_any_non_uniform)),
        "rows_all_non_uniform": ";".join(map(str, rows_all_non_uniform)),
    }


def main() -> None:
    if not BASE_DIR.exists():
        raise SystemExit(f"[ERRORE] Cartella non trovata: {BASE_DIR}")

    random.seed(0)

    results = []
    for csv_file in sorted(BASE_DIR.glob("*.csv")):
        summary = analyze_csv(csv_file)
        results.append(summary)
        print(
            f"{csv_file.name}: "
            f"any_non_uniform={summary['first_row_any_non_uniform']}, "
            f"all_non_uniform={summary['first_row_all_non_uniform']}"
        )

    with OUTPUT_FILE.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "filename",
                "total_rows",
                "first_row_any_non_uniform",
                "first_row_all_non_uniform",
                "rows_any_non_uniform",
                "rows_all_non_uniform",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    print(f"\nSalvato in: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
