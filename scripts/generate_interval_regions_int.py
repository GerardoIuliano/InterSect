#!/usr/bin/env python3
"""
Generate QF_LIA SMT-LIB formulas whose solution space is a union of integer intervals.

The intended universe is the finite integer range [0, 2^256 - 1]. Each generated
formula declares one Int variable and constrains it to belong to a configurable
number of non-overlapping regions:

    x in [a_1, b_1] OR ... OR x in [a_k, b_k]

The generator is parametric over interval width, number of regions, and region
distribution, so experiment grids can be extended by adding CLI values.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

from generate_ranked_linear_smt import parse_int_expr, parse_int_expr_list


UNIVERSE_SIZE_256 = 2**256
NON_UNIFORM_RE = re.compile(r"^non_uniform_skew_(0(?:\.\d+)?|1(?:\.0+)?)$")


@dataclass(frozen=True)
class DistributionSpec:
    name: str
    kind: str
    skew: Fraction | None = None


@dataclass(frozen=True)
class FormulaConfig:
    width: int
    num_regions: int
    distribution: DistributionSpec
    universe_size: int = UNIVERSE_SIZE_256
    seed: int = 1
    cluster_span_ratio: Fraction = Fraction(1, 100)
    variable_name: str = "x"

    def validate(self) -> None:
        if self.width <= 0:
            raise ValueError("width must be positive")
        if self.num_regions <= 0:
            raise ValueError("num_regions must be positive")
        if self.universe_size <= 0:
            raise ValueError("universe_size must be positive")
        if self.width * self.num_regions > self.universe_size:
            raise ValueError(
                "requested regions do not fit in the universe: "
                f"width * num_regions = {self.width * self.num_regions}, "
                f"universe_size = {self.universe_size}"
            )
        if not (0 < self.cluster_span_ratio <= 1):
            raise ValueError("cluster_span_ratio must be in (0, 1]")


def parse_fraction(value: str) -> Fraction:
    try:
        result = Fraction(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid fraction: {value!r}") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("fraction must be positive")
    return result


def parse_distribution(value: str) -> DistributionSpec:
    if value == "uniform":
        return DistributionSpec(name=value, kind="uniform")

    match = NON_UNIFORM_RE.match(value)
    if match:
        skew = Fraction(match.group(1))
        if not (0 <= skew <= 1):
            raise argparse.ArgumentTypeError("skew must be in [0, 1]")
        return DistributionSpec(name=value, kind="non_uniform", skew=skew)

    raise argparse.ArgumentTypeError(
        "distribution must be 'uniform' or 'non_uniform_skew_0.x'"
    )


def parse_distribution_list(value: str) -> list[DistributionSpec]:
    distributions = [part.strip() for part in value.split(",") if part.strip()]
    if not distributions:
        raise argparse.ArgumentTypeError("expected a comma-separated distribution list")
    return [parse_distribution(distribution) for distribution in distributions]


def _round_fraction(value: Fraction) -> int:
    return (value.numerator + value.denominator // 2) // value.denominator


def _uniform_starts(count: int, width: int, universe_size: int) -> list[int]:
    remaining = universe_size - (count * width)
    if remaining < 0:
        raise ValueError("intervals do not fit in the requested span")
    gap = remaining // (count + 1)
    return [gap + idx * (width + gap) for idx in range(count)]


def _uniform_starts_in_span(count: int, width: int, start: int, span: int) -> list[int]:
    return [start + offset for offset in _uniform_starts(count, width, span)]


def _subtract_span(
    spans: Sequence[tuple[int, int]],
    blocked: tuple[int, int],
) -> list[tuple[int, int]]:
    blocked_start, blocked_end = blocked
    result: list[tuple[int, int]] = []
    for start, end in spans:
        if end <= blocked_start or start >= blocked_end:
            result.append((start, end))
            continue
        if start < blocked_start:
            result.append((start, blocked_start))
        if blocked_end < end:
            result.append((blocked_end, end))
    return result


def _allocate_uniformly_across_spans(
    count: int,
    width: int,
    spans: Sequence[tuple[int, int]],
) -> list[int]:
    if count == 0:
        return []

    capacities = [max(0, (end - start) // width) for start, end in spans]
    total_capacity = sum(capacities)
    if total_capacity < count:
        raise ValueError("not enough room outside the non-uniform cluster")

    # Pick target positions in capacity-space, then map them back to concrete spans.
    targets = _uniform_starts(count, 1, total_capacity)
    starts = []
    cumulative_capacity = 0
    span_idx = 0
    for target in targets:
        slot = min(total_capacity - 1, target)
        while span_idx < len(spans) and slot >= cumulative_capacity + capacities[span_idx]:
            cumulative_capacity += capacities[span_idx]
            span_idx += 1
        if span_idx >= len(spans):
            span_idx = len(spans) - 1
        start, end = spans[span_idx]
        local_slot = slot - cumulative_capacity
        starts.append(start + local_slot * width)

    return starts


def _non_uniform_starts(config: FormulaConfig) -> list[int]:
    distribution = config.distribution
    assert distribution.skew is not None

    cluster_count = _round_fraction(distribution.skew * config.num_regions)
    cluster_count = max(1, min(config.num_regions, cluster_count))
    sparse_count = config.num_regions - cluster_count

    cluster_span = (config.universe_size * config.cluster_span_ratio.numerator) // (
        config.cluster_span_ratio.denominator
    )
    cluster_span = max(cluster_span, cluster_count * config.width)
    if cluster_span > config.universe_size:
        raise ValueError("cluster_span_ratio leaves no valid universe span")

    rng = random.Random(config.seed)
    max_cluster_start = config.universe_size - cluster_span
    cluster_start = rng.randint(0, max_cluster_start) if max_cluster_start else 0
    cluster_end = cluster_start + cluster_span

    clustered = _uniform_starts_in_span(
        cluster_count,
        config.width,
        cluster_start,
        cluster_span,
    )

    outside_spans = _subtract_span([(0, config.universe_size)], (cluster_start, cluster_end))
    sparse = _allocate_uniformly_across_spans(sparse_count, config.width, outside_spans)

    return sorted(clustered + sparse)


def generate_regions(config: FormulaConfig) -> list[tuple[int, int]]:
    config.validate()
    if config.distribution.kind == "uniform":
        starts = _uniform_starts(config.num_regions, config.width, config.universe_size)
    else:
        starts = _non_uniform_starts(config)

    regions = [(start, start + config.width - 1) for start in starts]
    for idx in range(1, len(regions)):
        previous_end = regions[idx - 1][1]
        current_start = regions[idx][0]
        if current_start <= previous_end:
            raise RuntimeError("generated overlapping regions")
    return regions


def build_smt2(config: FormulaConfig, regions: Sequence[tuple[int, int]]) -> str:
    variable = config.variable_name
    universe_upper = config.universe_size - 1
    lines = [
        "(set-logic QF_LIA)",
        "",
        f"(declare-const {variable} Int)",
        "",
        f"(assert (>= {variable} 0))",
        f"(assert (<= {variable} {universe_upper}))",
        "",
        "(assert",
        "  (or",
    ]

    for start, end in regions:
        # Keep interval bounds equivalent but with constants first, so InterSect's
        # global Int-bound extractor only sees the explicit universe bounds above.
        lines.append(f"    (and (<= {start} {variable}) (>= {end} {variable}))")

    lines.extend(
        [
            "  )",
            ")",
            "",
            "(check-sat)",
            "(get-model)",
            "",
        ]
    )
    return "\n".join(lines)


def build_metadata(config: FormulaConfig, regions: Sequence[tuple[int, int]]) -> dict:
    total_solutions = config.width * config.num_regions
    return {
        "logic": "QF_LIA",
        "variable_name": config.variable_name,
        "universe": {
            "lower": 0,
            "upper": config.universe_size - 1,
            "size": config.universe_size,
        },
        "parameters": {
            "width": config.width,
            "num_regions": config.num_regions,
            "distribution": config.distribution.name,
            "seed": config.seed,
            "cluster_span_ratio": str(config.cluster_span_ratio),
        },
        "solution_space": {
            "total_solutions": total_solutions,
            "coverage_fraction": f"{total_solutions}/{config.universe_size}",
            "regions": [
                {"index": idx, "lower": start, "upper": end, "width": end - start + 1}
                for idx, (start, end) in enumerate(regions)
            ],
        },
    }


def _safe_width_label(width: int) -> str:
    if width > 0 and width.bit_count() == 1:
        return f"2pow{width.bit_length() - 1}"
    return str(width)


def _instance_stem(width: int, num_regions: int, distribution: DistributionSpec) -> str:
    return f"w_{_safe_width_label(width)}__r_{num_regions:02d}__d_{distribution.name}"


def write_formula(config: FormulaConfig, output_dir: Path) -> tuple[Path, Path]:
    regions = generate_regions(config)
    stem = _instance_stem(config.width, config.num_regions, config.distribution)
    smt2_path = output_dir / f"{stem}.smt2"
    json_path = output_dir / f"{stem}.json"

    output_dir.mkdir(parents=True, exist_ok=True)
    smt2_path.write_text(build_smt2(config, regions), encoding="utf-8")
    json_path.write_text(json.dumps(build_metadata(config, regions), indent=2), encoding="utf-8")
    return smt2_path, json_path


def _iter_configs(
    widths: Iterable[int],
    num_regions_list: Iterable[int],
    distributions: Iterable[DistributionSpec],
    universe_size: int,
    seed: int,
    cluster_span_ratio: Fraction,
    variable_name: str,
) -> Iterable[FormulaConfig]:
    for width in widths:
        for num_regions in num_regions_list:
            for distribution in distributions:
                yield FormulaConfig(
                    width=width,
                    num_regions=num_regions,
                    distribution=distribution,
                    universe_size=universe_size,
                    seed=seed,
                    cluster_span_ratio=cluster_span_ratio,
                    variable_name=variable_name,
                )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Int interval-region SMT formulas over a finite universe."
    )
    parser.add_argument(
        "--widths",
        type=parse_int_expr_list,
        required=True,
        help="comma-separated interval widths, e.g. '256,2**128'",
    )
    parser.add_argument(
        "--num-regions",
        type=parse_int_expr_list,
        required=True,
        help="comma-separated region counts, e.g. '10,20,30'",
    )
    parser.add_argument(
        "--distributions",
        type=parse_distribution_list,
        required=True,
        help="comma-separated values: uniform,non_uniform_skew_0.8,...",
    )
    parser.add_argument(
        "--universe-size",
        type=parse_int_expr,
        default=UNIVERSE_SIZE_256,
        help="exclusive universe size; default is 2**256",
    )
    parser.add_argument(
        "--seed",
        type=parse_int_expr,
        default=1,
        help="seed used for non-uniform cluster placement",
    )
    parser.add_argument(
        "--cluster-span-ratio",
        type=parse_fraction,
        default=Fraction(1, 100),
        help="fraction of the universe used as the dense non-uniform cluster span",
    )
    parser.add_argument(
        "--variable-name",
        default="x",
        help="SMT variable name",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/interval_regions"),
        help="directory where .smt2 and .json files are written",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    written = []
    for config in _iter_configs(
        widths=args.widths,
        num_regions_list=args.num_regions,
        distributions=args.distributions,
        universe_size=args.universe_size,
        seed=args.seed,
        cluster_span_ratio=args.cluster_span_ratio,
        variable_name=args.variable_name,
    ):
        smt2_path, json_path = write_formula(config, args.output_dir)
        written.append((smt2_path, json_path))
        print(f"Wrote {smt2_path}")
        print(f"Wrote {json_path}")

    print(f"Generated {len(written)} formula(s) in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
