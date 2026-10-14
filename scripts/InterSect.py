#!/usr/bin/env python3
"""Command-line entry point for the InterSect interval-guided sampler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_ranked_linear_smt import parse_int_expr


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the InterSect interval-guided sampler on a single SMT-LIB file. "
            "The required parameters are the input file, theta, coverage, U, and timeout."
        )
    )
    parser.add_argument(
        "file_path",
        type=Path,
        help="Path to the SMT-LIB file to sample (required).",
    )
    parser.add_argument(
        "--theta",
        type=parse_int_expr,
        required=True,
        help="Partition threshold for variables (required).",
    )
    parser.add_argument(
        "--coverage",
        type=parse_int_expr,
        required=True,
        help="Target coverage of the finite universe (required).",
    )
    parser.add_argument(
        "--delta",
        type=float,
        default=0.0050,
        help="Optional delta parameter (default: 0.0050).",
    )
    parser.add_argument(
        "--U",
        type=parse_int_expr,
        required=True,
        help="Finite universe size, for example 2**256-1 (required).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        required=True,
        help="Timeout in seconds (required).",
    )
    parser.add_argument(
        "--num-samples",
        type=parse_int_expr,
        default=None,
        help=(
            "Optional stop condition: terminate after collecting this many SAT "
            "solutions. If omitted, the sampler uses its existing stopping logic."
        ),
    )
    parser.add_argument(
        "--free-var",
        dest="free_vars",
        nargs="+",
        default=None,
        help=(
            "Optional free variable name(s) to keep. "
            "If omitted, all free variables are sampled."
        ),
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    from solver_utils_typed import intervalGuidedSampler
    from solver_utils import extract_free_vars_with_type_from_smt2_file, select_free_vars

    if not args.file_path.exists():
        raise SystemExit(f"[ERROR] SMT file not found: {args.file_path}")
    if not args.file_path.is_file():
        raise SystemExit(f"[ERROR] Path is not a file: {args.file_path}")

    print("[INFO] Running InterSect")
    print(f"[INFO] file_path = {args.file_path}")
    print(f"[INFO] theta     = {args.theta}")
    print(f"[INFO] coverage  = {args.coverage}")
    print(f"[INFO] delta     = {args.delta}")
    print(f"[INFO] U         = {args.U}")
    print(f"[INFO] timeout   = {args.timeout}")
    if args.num_samples is not None:
        print(f"[INFO] num_samples = {args.num_samples}")
    if args.free_vars is not None:
        print(f"[INFO] free_var  = {args.free_vars}")

    free_vars = None
    if args.free_vars is not None:
        available_free_vars = extract_free_vars_with_type_from_smt2_file(args.file_path)
        free_vars = select_free_vars(
            available_free_vars,
            keep=args.free_vars,
            interactive=False,
        )

    intervalGuidedSampler(
        args.file_path,
        free_vars=free_vars,
        theta=args.theta,
        coverage=args.coverage,
        delta=args.delta,
        U=args.U,
        timeout=args.timeout,
        threads=60,
        num_samples=args.num_samples,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
