#!/usr/bin/env python3
"""
Generate SMT-LIB formulas A*x = b from integer n*n matrices of exact rank r.

The construction is satisfiable by design:
  - build an integer matrix A with exact rank r,
  - sample a witness assignment x* inside the requested bounds,
  - compute b = A*x*,
  - emit QF_LIA constraints with bounds for every variable.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence


def parse_int_expr(value: str) -> int:
    """
    Parse an integer literal or a small arithmetic expression such as 2**256.

    This intentionally accepts only integer constants and arithmetic operators,
    so CLI values stay convenient without evaluating arbitrary Python code.
    """
    allowed_binary_ops = {
        ast.Add: lambda left, right: left + right,
        ast.Sub: lambda left, right: left - right,
        ast.Mult: lambda left, right: left * right,
        ast.FloorDiv: lambda left, right: left // right,
        ast.Pow: lambda left, right: left**right,
    }
    allowed_unary_ops = {
        ast.UAdd: lambda operand: operand,
        ast.USub: lambda operand: -operand,
    }

    def eval_node(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_binary_ops:
            return allowed_binary_ops[type(node.op)](eval_node(node.left), eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary_ops:
            return allowed_unary_ops[type(node.op)](eval_node(node.operand))
        raise argparse.ArgumentTypeError(f"invalid integer expression: {value!r}")

    try:
        return eval_node(ast.parse(value, mode="eval"))
    except (SyntaxError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError(f"invalid integer expression: {value!r}") from exc


def parse_int_expr_list(value: str) -> list[int]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected a comma-separated list of integer expressions")
    return [parse_int_expr(item) for item in values]


@dataclass(frozen=True)
class RankedLinearConfig:
    n: int
    rank: int
    var_lb: int
    var_ub: int
    coeff_min: int
    coeff_max: int
    combo_min: int
    combo_max: int
    seed: int | None = None

    def validate(self) -> None:
        if self.n <= 0:
            raise ValueError("n must be positive")
        if not (1 <= self.rank <= self.n):
            raise ValueError("rank must be in [1, n]")
        if self.var_lb > self.var_ub:
            raise ValueError("var-lb must be <= var-ub")
        if self.coeff_min > self.coeff_max:
            raise ValueError("coeff-min must be <= coeff-max")
        if self.combo_min > self.combo_max:
            raise ValueError("combo-min must be <= combo-max")
        if self.combo_min == 0 and self.combo_max == 0 and self.rank < self.n:
            raise ValueError("combo coefficient range cannot be only zero when dependent rows are needed")


def _nonzero_randint(rng: random.Random, lower: int, upper: int) -> int:
    choices = [value for value in range(lower, upper + 1) if value != 0]
    if not choices:
        raise ValueError("range does not contain a non-zero value")
    return rng.choice(choices)


def _generate_base_rows(config: RankedLinearConfig, rng: random.Random) -> list[list[int]]:
    rows: list[list[int]] = []
    for row_idx in range(config.rank):
        row = [0 for _ in range(config.n)]
        row[row_idx] = 1
        for col_idx in range(config.rank, config.n):
            row[col_idx] = rng.randint(config.coeff_min, config.coeff_max)
        rows.append(row)
    return rows


def generate_ranked_matrix(config: RankedLinearConfig) -> list[list[int]]:
    config.validate()
    rng = random.Random(config.seed)
    base_rows = _generate_base_rows(config, rng)
    matrix = [list(row) for row in base_rows]

    for _ in range(config.n - config.rank):
        coefficients = [rng.randint(config.combo_min, config.combo_max) for _ in range(config.rank)]
        if all(coeff == 0 for coeff in coefficients):
            coefficients[rng.randrange(config.rank)] = _nonzero_randint(
                rng,
                config.combo_min,
                config.combo_max,
            )

        dependent_row = [
            sum(coeff * base_rows[row_idx][col_idx] for row_idx, coeff in enumerate(coefficients))
            for col_idx in range(config.n)
        ]
        matrix.append(dependent_row)

    return matrix


def matrix_rank(matrix: Sequence[Sequence[int]]) -> int:
    if not matrix:
        return 0

    work = [[Fraction(value) for value in row] for row in matrix]
    row_count = len(work)
    col_count = len(work[0])
    rank = 0

    for col_idx in range(col_count):
        pivot = None
        for row_idx in range(rank, row_count):
            if work[row_idx][col_idx] != 0:
                pivot = row_idx
                break
        if pivot is None:
            continue

        work[rank], work[pivot] = work[pivot], work[rank]
        pivot_value = work[rank][col_idx]
        work[rank] = [value / pivot_value for value in work[rank]]

        for row_idx in range(row_count):
            if row_idx == rank:
                continue
            factor = work[row_idx][col_idx]
            if factor == 0:
                continue
            work[row_idx] = [
                current - factor * pivot_current
                for current, pivot_current in zip(work[row_idx], work[rank])
            ]

        rank += 1
        if rank == row_count:
            break

    return rank


def sample_witness(config: RankedLinearConfig, rng: random.Random) -> list[int]:
    return [rng.randint(config.var_lb, config.var_ub) for _ in range(config.n)]


def multiply_matrix_vector(matrix: Sequence[Sequence[int]], vector: Sequence[int]) -> list[int]:
    return [sum(coeff * value for coeff, value in zip(row, vector)) for row in matrix]


def _smt_term(coeff: int, variable: str) -> str | None:
    if coeff == 0:
        return None
    if coeff == 1:
        return variable
    if coeff == -1:
        return f"(- {variable})"
    return f"(* {coeff} {variable})"


def _smt_linear_expr(row: Sequence[int], variable_names: Sequence[str]) -> str:
    terms = [
        term
        for coeff, variable in zip(row, variable_names)
        if (term := _smt_term(coeff, variable)) is not None
    ]
    if not terms:
        return "0"
    if len(terms) == 1:
        return terms[0]
    return f"(+ {' '.join(terms)})"


def build_smt2(
    matrix: Sequence[Sequence[int]],
    rhs: Sequence[int],
    var_lb: int,
    var_ub: int,
    variable_names: Sequence[str],
) -> str:
    lines = ["(set-logic QF_LIA)", ""]
    for variable in variable_names:
        lines.append(f"(declare-const {variable} Int)")

    lines.append("")
    for variable in variable_names:
        lines.append(f"(assert (>= {variable} {var_lb}))")
        lines.append(f"(assert (<= {variable} {var_ub}))")

    lines.append("")
    for row_idx, (row, value) in enumerate(zip(matrix, rhs)):
        expr = _smt_linear_expr(row, variable_names)
        lines.append(f"; equation {row_idx}")
        lines.append(f"(assert (= {expr} {value}))")

    lines.extend(["", "(check-sat)", "(get-model)", ""])
    return "\n".join(lines)


def build_metadata(
    config: RankedLinearConfig,
    matrix: Sequence[Sequence[int]],
    rhs: Sequence[int],
    witness: Sequence[int],
    actual_rank: int,
) -> dict:
    return {
        "n": config.n,
        "rank": config.rank,
        "actual_rank": actual_rank,
        "nullity": config.n - config.rank,
        "var_lb": config.var_lb,
        "var_ub": config.var_ub,
        "coeff_min": config.coeff_min,
        "coeff_max": config.coeff_max,
        "combo_min": config.combo_min,
        "combo_max": config.combo_max,
        "seed": config.seed,
        "variable_names": [f"x{idx}" for idx in range(config.n)],
        "witness": list(witness),
        "matrix": [list(row) for row in matrix],
        "rhs": list(rhs),
    }


def write_ranked_instance(config: RankedLinearConfig, out_smt2: Path, out_json: Path | None) -> None:
    rng = random.Random(config.seed)
    matrix = generate_ranked_matrix(config)
    actual_rank = matrix_rank(matrix)
    if actual_rank != config.rank:
        raise RuntimeError(f"generated matrix has rank {actual_rank}, expected {config.rank}")

    witness = sample_witness(config, rng)
    rhs = multiply_matrix_vector(matrix, witness)
    variable_names = [f"x{idx}" for idx in range(config.n)]
    smt2 = build_smt2(matrix, rhs, config.var_lb, config.var_ub, variable_names)
    metadata = build_metadata(config, matrix, rhs, witness, actual_rank)

    out_smt2.parent.mkdir(parents=True, exist_ok=True)
    out_smt2.write_text(smt2, encoding="utf-8")

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def write_ranked_instance_from_parts(
    config: RankedLinearConfig,
    matrix: Sequence[Sequence[int]],
    rhs: Sequence[int],
    witness: Sequence[int],
    actual_rank: int,
    out_smt2: Path,
    out_json: Path | None,
) -> None:
    variable_names = [f"x{idx}" for idx in range(config.n)]
    smt2 = build_smt2(matrix, rhs, config.var_lb, config.var_ub, variable_names)
    metadata = build_metadata(config, matrix, rhs, witness, actual_rank)

    out_smt2.parent.mkdir(parents=True, exist_ok=True)
    out_smt2.write_text(smt2, encoding="utf-8")

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _instance_seed(base_seed: int | None, rank: int, instance_idx: int) -> int | None:
    if base_seed is None:
        return None
    return base_seed + rank * 100_000 + instance_idx


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate exact-rank integer linear SMT formulas A*x=b with variable bounds."
    )
    parser.add_argument("--n", type=parse_int_expr, default=20, help="matrix dimension and number of variables")

    rank_group = parser.add_mutually_exclusive_group(required=True)
    rank_group.add_argument("--rank", type=parse_int_expr, help="single exact rank to generate")
    rank_group.add_argument("--all-ranks", action="store_true", help="generate ranks 1..n")

    parser.add_argument("--instances", type=parse_int_expr, default=1, help="instances to generate per rank")
    parser.add_argument("--var-lb", type=parse_int_expr, default=0, help="inclusive lower bound for every variable")
    parser.add_argument("--var-ub", type=parse_int_expr, default=100, help="inclusive upper bound for every variable")
    parser.add_argument(
        "--sweep-var-ubs",
        type=parse_int_expr_list,
        default=None,
        help="comma-separated upper bounds to sweep while keeping rank, matrix, witness, and rhs fixed",
    )
    parser.add_argument("--coeff-min", type=parse_int_expr, default=-3, help="minimum coefficient for base-row free columns")
    parser.add_argument("--coeff-max", type=parse_int_expr, default=3, help="maximum coefficient for base-row free columns")
    parser.add_argument("--combo-min", type=parse_int_expr, default=-3, help="minimum combination coefficient for dependent rows")
    parser.add_argument("--combo-max", type=parse_int_expr, default=3, help="maximum combination coefficient for dependent rows")
    parser.add_argument("--seed", type=parse_int_expr, default=None, help="base random seed")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/ranked_linear_independence"),
        help="directory where SMT and metadata files are written",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="do not write metadata JSON files",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.instances <= 0:
        raise ValueError("instances must be positive")
    if args.sweep_var_ubs is not None and args.all_ranks:
        raise ValueError("--sweep-var-ubs requires a fixed --rank, not --all-ranks")

    ranks = range(1, args.n + 1) if args.all_ranks else [args.rank]
    written = 0

    if args.sweep_var_ubs is not None:
        sweep_var_ubs = sorted(set(args.sweep_var_ubs))
        min_sweep_ub = min(sweep_var_ubs)
        if args.var_lb > min_sweep_ub:
            raise ValueError("var-lb must be <= every value in --sweep-var-ubs")

        rank = args.rank
        for instance_idx in range(args.instances):
            seed = _instance_seed(args.seed, rank, instance_idx)
            matrix_config = RankedLinearConfig(
                n=args.n,
                rank=rank,
                var_lb=args.var_lb,
                var_ub=min_sweep_ub,
                coeff_min=args.coeff_min,
                coeff_max=args.coeff_max,
                combo_min=args.combo_min,
                combo_max=args.combo_max,
                seed=seed,
            )
            rng = random.Random(seed)
            matrix = generate_ranked_matrix(matrix_config)
            actual_rank = matrix_rank(matrix)
            if actual_rank != rank:
                raise RuntimeError(f"generated matrix has rank {actual_rank}, expected {rank}")

            witness = sample_witness(matrix_config, rng)
            rhs = multiply_matrix_vector(matrix, witness)

            for bound_idx, var_ub in enumerate(sweep_var_ubs):
                config = RankedLinearConfig(
                    n=args.n,
                    rank=rank,
                    var_lb=args.var_lb,
                    var_ub=var_ub,
                    coeff_min=args.coeff_min,
                    coeff_max=args.coeff_max,
                    combo_min=args.combo_min,
                    combo_max=args.combo_max,
                    seed=seed,
                )
                stem = f"rank_{rank:02d}_bound_{bound_idx:03d}_instance_{instance_idx:03d}"
                bound_dir = args.output_dir / f"rank_{rank:02d}" / f"bound_{bound_idx:03d}"
                out_smt2 = bound_dir / f"{stem}.smt2"
                out_json = None if args.no_json else bound_dir / f"{stem}.json"
                write_ranked_instance_from_parts(
                    config,
                    matrix,
                    rhs,
                    witness,
                    actual_rank,
                    out_smt2,
                    out_json,
                )
                written += 1
                print(f"Wrote {out_smt2}")

        print(f"Generated {written} SMT instance(s) in {args.output_dir}")
        return 0

    for rank in ranks:
        for instance_idx in range(args.instances):
            seed = _instance_seed(args.seed, rank, instance_idx)
            config = RankedLinearConfig(
                n=args.n,
                rank=rank,
                var_lb=args.var_lb,
                var_ub=args.var_ub,
                coeff_min=args.coeff_min,
                coeff_max=args.coeff_max,
                combo_min=args.combo_min,
                combo_max=args.combo_max,
                seed=seed,
            )

            stem = f"rank_{rank:02d}_instance_{instance_idx:03d}"
            rank_dir = args.output_dir / f"rank_{rank:02d}"
            out_smt2 = rank_dir / f"{stem}.smt2"
            out_json = None if args.no_json else rank_dir / f"{stem}.json"
            write_ranked_instance(config, out_smt2, out_json)
            written += 1
            print(f"Wrote {out_smt2}")

    print(f"Generated {written} SMT instance(s) in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
