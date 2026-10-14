#!/usr/bin/env python3
"""Generate the D_s x D_w solution-space benchmark grid."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from solution_space_generator import (
    FORMULA_SCALE_FACTOR,
    LOC_SAMPLERS,
    PLOT_STYLES,
    WIDTH_SAMPLERS,
    parse_fraction,
)


DEFAULT_LOC_SAMPLERS = ("uniform", "single_cluster", "multi_cluster")
DEFAULT_WIDTH_SAMPLERS = ("uniform", "log_uniform", "geometric")
DEFAULT_NUM_FORMULAS = 10
DEFAULT_FORMULAS_PER_TARGET = 1
COMBINATION_SEED_STRIDE = 10_000_019


def _stem(loc_sampler: str, width_sampler: str) -> str:
    return f"solution_space__Ds_{loc_sampler}__Dw_{width_sampler}"


def _build_generator_command(
    *,
    generator_script: Path,
    target_percentage: str,
    loc_sampler: str,
    width_sampler: str,
    num_formulas: int,
    formulas_per_target: int,
    output_dir: Path,
    info_dir: Path,
    seed: int | None,
    combination_index: int,
    num_vars: int,
    verify: bool,
    plot: bool,
    plot_style: str,
    extra_args: list[str],
) -> list[str]:
    stem = _stem(loc_sampler, width_sampler)
    command = [
        sys.executable,
        str(generator_script),
        "--target-percentage",
        target_percentage,
        "--loc-sampler",
        loc_sampler,
        "--width-sampler",
        width_sampler,
        "--num-formulas",
        str(num_formulas),
        "--formulas-per-target",
        str(formulas_per_target),
        "--num-vars",
        str(num_vars),
        "--out-smt2",
        str(output_dir / f"{stem}.smt2"),
        "--out-json",
        str(info_dir / f"{stem}.json"),
    ]

    if seed is not None:
        command.extend(["--seed", str(seed + combination_index * COMBINATION_SEED_STRIDE)])
    if verify:
        command.append("--verify")
    if plot:
        command.extend(
            [
                "--plot",
                "--plot-style",
                plot_style,
                "--plot-out",
                str(info_dir / f"{stem}.png"),
            ]
        )
    command.extend(extra_args)
    return command


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate solution-space formulas for every combination of D_s "
            "and D_w samplers."
        )
    )
    parser.add_argument(
        "--target-percentage",
        type=parse_fraction,
        required=True,
        help=(
            "initial target solution-space percentage; each generated formula uses "
            f"target / {FORMULA_SCALE_FACTOR}^index"
        ),
    )
    parser.add_argument(
        "--loc-samplers",
        nargs="+",
        choices=LOC_SAMPLERS,
        default=DEFAULT_LOC_SAMPLERS,
        help="D_s location samplers",
    )
    parser.add_argument(
        "--width-samplers",
        nargs="+",
        choices=WIDTH_SAMPLERS,
        default=DEFAULT_WIDTH_SAMPLERS,
        help="D_w width samplers",
    )
    parser.add_argument(
        "--num-formulas",
        dest="num_formulas",
        type=int,
        default=DEFAULT_NUM_FORMULAS,
        help="number of target-coverage levels to generate for each D_s x D_w combination",
    )
    parser.add_argument(
        "--formulas-per-target",
        "--formulas-per-coverage",
        "--formulas-per-config",
        "--formulas-per-configuration",
        type=int,
        default=DEFAULT_FORMULAS_PER_TARGET,
        help="number of independent formulas to generate for each target-coverage level",
    )
    parser.add_argument(
        "--num-vars",
        type=int,
        default=1,
        help="number of Int variables per generated formula",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="base seed; each sampler combination receives a deterministic offset",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/solution_space"),
        help="directory where generated .smt2 files are written",
    )
    parser.add_argument(
        "--info-dir",
        type=Path,
        default=Path("benchmarks/solution_space_info"),
        help="directory where generated metadata JSON and optional plots are written",
    )
    parser.add_argument(
        "--generator-script",
        type=Path,
        default=Path(__file__).with_name("solution_space_generator.py"),
        help="path to solution_space_generator.py",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="ask the generator to verify each generated formula with z3",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="ask the generator to create one plot per generated formula",
    )
    parser.add_argument(
        "--plot-style",
        choices=PLOT_STYLES,
        default="bars",
        help=(
            "plot representation: bars preserve interval width at scale; points "
            "show every interval as a visible marker colored by width"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print generator commands without executing them",
    )
    parser.add_argument(
        "generator_args",
        nargs=argparse.REMAINDER,
        help="extra arguments passed to solution_space_generator.py after '--'",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.num_formulas <= 0:
        raise ValueError("num_formulas must be positive")
    if args.formulas_per_target <= 0:
        raise ValueError("formulas_per_target must be positive")
    if args.num_vars <= 0:
        raise ValueError("num_vars must be positive")

    extra_args = args.generator_args
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    total_combinations = len(args.loc_samplers) * len(args.width_samplers)
    total_formulas = total_combinations * args.num_formulas * args.formulas_per_target

    print(f"D_s samplers: {list(args.loc_samplers)}")
    print(f"D_w samplers: {list(args.width_samplers)}")
    print(f"Combinations: {total_combinations}")
    print(f"Target coverage levels per combination: {args.num_formulas}")
    print(f"Formulas per target coverage: {args.formulas_per_target}")
    print(f"Total formulas: {total_formulas}")

    combination_index = 0
    for loc_sampler in args.loc_samplers:
        for width_sampler in args.width_samplers:
            command = _build_generator_command(
                generator_script=args.generator_script,
                target_percentage=str(args.target_percentage),
                loc_sampler=loc_sampler,
                width_sampler=width_sampler,
                num_formulas=args.num_formulas,
                formulas_per_target=args.formulas_per_target,
                output_dir=args.output_dir,
                info_dir=args.info_dir,
                seed=args.seed,
                combination_index=combination_index,
                num_vars=args.num_vars,
                verify=args.verify,
                plot=args.plot,
                plot_style=args.plot_style,
                extra_args=extra_args,
            )

            label = f"D_s={loc_sampler}, D_w={width_sampler}"
            if args.dry_run:
                print(f"[dry-run] {label}: {' '.join(command)}")
            else:
                print(f"Generating {label}")
                subprocess.run(command, check=True)

            combination_index += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
