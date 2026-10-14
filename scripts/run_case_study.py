#!/usr/bin/env python3
"""Run intervalGuidedSampler and the Z3 baseline on the case study SMT files."""

from __future__ import annotations

import csv
from pathlib import Path
import multiprocessing
import logging
from solver_utils import extract_free_vars_with_type_from_smt2_file, select_free_vars
from solver_utils_typed import intervalGuidedSampler, z3_baseline_sampler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = PROJECT_ROOT / "mythril_test" / "SAT_execution_path_int_subset"
DEFAULT_COVERAGE = 50
DEFAULT_THETA = 256
DEFAULT_DELTA = 0.0050
DEFAULT_TIMEOUT = 1200
DEFAULT_THREADS = 60
DEFAULT_RESULTS_SUFFIX = "_interval_guided_sampler"
BASELINE_RESULTS_SUFFIX = "_z3_baseline_call_value"


def result_csv_path(smt_path: Path, coverage: int) -> Path:
    benchmark_dir = smt_path.parent
    results_dir = benchmark_dir.parent / (
        benchmark_dir.name + "_results" + DEFAULT_RESULTS_SUFFIX + "_cov" + str(coverage)
    )
    return results_dir / smt_path.with_suffix(".csv").name


def baseline_result_csv_path(smt_path: Path) -> Path:
    benchmark_dir = smt_path.parent
    results_dir = benchmark_dir.parent / (benchmark_dir.name + "_results" + BASELINE_RESULTS_SUFFIX)
    return results_dir / smt_path.with_suffix(".csv").name


def load_case_study_free_vars() -> dict[str, list[str]]:
    """Load the CSV map filename -> free variables to keep."""
    cs_free_var_path = BASE_DIR / "CS_free_var.csv"
    cs_free_var: dict[str, list[str]] = {}

    with cs_free_var_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = row["filename"]
            varying = [v.strip() for v in row["varying_variables"].split(",") if v.strip()]
            cs_free_var[filename] = varying

    return cs_free_var


def iter_case_study_files() -> list[Path]:
    return sorted(BASE_DIR.glob("*/*.smt2"))


def run_case_study(coverage: int) -> int:
    cs_free_var = load_case_study_free_vars()
    smt_files = iter_case_study_files()

    if not smt_files:
        print(f"No SMT files found under {BASE_DIR}")
        return 0

    for smt_filepath in smt_files:
        print(smt_filepath)

        csv_filename = smt_filepath.stem + ".csv"
        keep = cs_free_var.get(csv_filename, [])
        keep = [v for v in keep if v.startswith("call_value")]

        free_vars = extract_free_vars_with_type_from_smt2_file(smt_filepath)
        free_vars = select_free_vars(free_vars, select_type="Int", interactive=False, keep=keep)

        interval_result_csv = result_csv_path(smt_filepath, coverage)
        if interval_result_csv.exists():
            print(f"Skipping {smt_filepath.name} at coverage {coverage}: already processed.")
        else:
            intervalGuidedSampler(
                smt_filepath,
                free_vars=free_vars,
                theta=DEFAULT_THETA,
                coverage=coverage,
                delta=DEFAULT_DELTA,
                U=2**256 - 1,
                timeout=DEFAULT_TIMEOUT,
                threads=DEFAULT_THREADS,
                results_suffix=DEFAULT_RESULTS_SUFFIX,
            )

        baseline_csv = baseline_result_csv_path(smt_filepath)
        if baseline_csv.exists():
            print(f"Skipping {smt_filepath.name} baseline: already processed.")
        else:
            p = multiprocessing.Process(
                target=z3_baseline_sampler,
                args=(smt_filepath,),
                kwargs={
                    "free_vars": free_vars,
                    "timeout": DEFAULT_TIMEOUT,
                },
            )
            p.start()
            p.join(timeout=DEFAULT_TIMEOUT)

            if p.is_alive():
                logging.warning(
                    f"Timeout esterno scattato per {smt_filepath.name}: "
                    f"processo killato dopo {DEFAULT_TIMEOUT // 60} minuti."
                )
                p.terminate()  # SIGTERM
                p.join(timeout=5)
                if p.is_alive():
                    p.kill()  # SIGKILL se terminate non basta
                    p.join()

    return 0


def main() -> int:
    run_case_study(DEFAULT_COVERAGE)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
