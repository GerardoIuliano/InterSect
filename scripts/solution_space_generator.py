#!/usr/bin/env python3
"""
Generate Int SMT-LIB formulas from stochastic subinterval coverage.

The generator builds a stochastic solution space over the fixed finite universe
[0, 2^256). It samples candidate widths before start locations, interprets raw
intervals over a circular universe, inserts only the net new covered measure,
and stops when the union measure reaches the target percentage exactly.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence


UNIVERSE_LOWER = 0
UNIVERSE_UPPER_EXCLUSIVE = 2**256
DEFAULT_VARIABLE_NAME = "x"
DEFAULT_LOC_SAMPLER = "uniform"
DEFAULT_WIDTH_SAMPLER = "uniform"
LOC_SAMPLERS = (
    "uniform",
    "single_cluster",
    "multi_cluster",
    "clustered",
    "edge_biased",
    "center_biased",
    "non_uniform",
)
WIDTH_SAMPLERS = (
    "uniform",
    "geometric",
    "reverse_geometric",
    "log_uniform",
    "beta_left_skewed",
    "exponential",
)
CLUSTERED_LOC_CLUSTER_COUNT = 4
CLUSTERED_LOC_RADIUS_DENOMINATOR = 100
NON_UNIFORM_CLUSTER_WEIGHT_NUMERATOR = 4
NON_UNIFORM_CLUSTER_WEIGHT_DENOMINATOR = 5
DEFAULT_CLUSTER_MU = 0.5
DEFAULT_CLUSTER_SIGMA = 0.05
DEFAULT_NUM_CLUSTERS = 3
DEFAULT_GEOMETRIC_MEAN_FRACTION = Fraction(1, 1000)
DEFAULT_BETA_WIDTH_ALPHA = 5.0
DEFAULT_BETA_WIDTH_BETA = 2.0
FORMULA_SEED_STRIDE = 1_000_003
FORMULA_SCALE_FACTOR = 1000000


@dataclass(frozen=True)
class GeneratorConfig:
    target_percentage: Fraction
    num_vars: int = 1
    seed: int | None = None
    variable_name: str = DEFAULT_VARIABLE_NAME
    loc_sampler: str = DEFAULT_LOC_SAMPLER
    width_sampler: str = DEFAULT_WIDTH_SAMPLER
    cluster_mu: float = DEFAULT_CLUSTER_MU
    cluster_sigma: float = DEFAULT_CLUSTER_SIGMA
    num_clusters: int = DEFAULT_NUM_CLUSTERS
    cluster_mus: tuple[float, ...] | None = None
    cluster_sigmas: tuple[float, ...] | None = None
    cluster_weights: tuple[float, ...] | None = None
    geometric_mean_fraction: Fraction = DEFAULT_GEOMETRIC_MEAN_FRACTION
    beta_width_alpha: float = DEFAULT_BETA_WIDTH_ALPHA
    beta_width_beta: float = DEFAULT_BETA_WIDTH_BETA
    max_iterations: int = 1_000_000
    stale_iteration_limit: int = 10_000

    @property
    def universe_size(self) -> int:
        return UNIVERSE_UPPER_EXCLUSIVE - UNIVERSE_LOWER

    @property
    def target_measure(self) -> int:
        raw = self.universe_size * self.target_percentage / 100
        return round_fraction(raw)

    def validate(self) -> None:
        if not (0 < self.target_percentage <= 100):
            raise ValueError("target_percentage must be in (0, 100]")
        if self.target_measure <= 0:
            raise ValueError("target percentage rounds to zero covered values")
        if self.num_vars <= 0:
            raise ValueError("num_vars must be positive")
        if not self.variable_name:
            raise ValueError("variable_name must be non-empty")
        if self.loc_sampler not in LOC_SAMPLERS:
            raise ValueError(f"unsupported loc sampler: {self.loc_sampler}")
        if self.width_sampler not in WIDTH_SAMPLERS:
            raise ValueError(f"unsupported width sampler: {self.width_sampler}")
        if not (0 <= self.cluster_mu < 1):
            raise ValueError("cluster_mu must be in [0, 1)")
        if self.cluster_sigma <= 0:
            raise ValueError("cluster_sigma must be positive")
        if self.num_clusters <= 0:
            raise ValueError("num_clusters must be positive")
        if self.geometric_mean_fraction <= 0:
            raise ValueError("geometric_mean_fraction must be positive")
        if self.beta_width_alpha <= 0 or self.beta_width_beta <= 0:
            raise ValueError("beta width parameters must be positive")
        if self.width_sampler == "beta_left_skewed" and self.beta_width_alpha <= self.beta_width_beta:
            raise ValueError("beta_left_skewed requires beta_width_alpha > beta_width_beta")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.stale_iteration_limit <= 0:
            raise ValueError("stale_iteration_limit must be positive")


@dataclass(frozen=True)
class GeneratedSolutionSpace:
    config: GeneratorConfig
    intervals: list[tuple[int, int]]
    merged_intervals: list[tuple[int, int]]
    covered_measure: int
    iterations: int
    smt2: str
    metadata: dict


@dataclass(frozen=True)
class VariableSolutionSpace:
    variable_name: str
    intervals: list[tuple[int, int]]
    merged_intervals: list[tuple[int, int]]
    covered_measure: int
    iterations: int
    seed: int | None
    sampler_metadata: dict


class MergedIntervalTree:
    """Small interval tree abstraction backed by sorted disjoint intervals."""

    def __init__(self) -> None:
        self._intervals: list[tuple[int, int]] = []

    @property
    def intervals(self) -> list[tuple[int, int]]:
        return list(self._intervals)

    def find_overlaps(self, start: int, end: int) -> list[tuple[int, int]]:
        overlaps = []
        for current_start, current_end in self._intervals:
            if current_end <= start:
                continue
            if current_start >= end:
                break
            overlaps.append((current_start, current_end))
        return overlaps

    def insert_and_merge(self, start: int, end: int) -> None:
        if start >= end:
            return

        merged: list[tuple[int, int]] = []
        inserted = False
        new_start = start
        new_end = end

        for current_start, current_end in self._intervals:
            if current_end < new_start:
                merged.append((current_start, current_end))
                continue
            if new_end < current_start:
                if not inserted:
                    merged.append((new_start, new_end))
                    inserted = True
                merged.append((current_start, current_end))
                continue

            new_start = min(new_start, current_start)
            new_end = max(new_end, current_end)

        if not inserted:
            merged.append((new_start, new_end))

        self._intervals = merged

    def first_uncovered_point(self, lower: int, upper_exclusive: int) -> int | None:
        cursor = lower
        for start, end in self._intervals:
            if cursor < start:
                return cursor
            cursor = max(cursor, end)
            if cursor >= upper_exclusive:
                return None
        return cursor if cursor < upper_exclusive else None


def parse_fraction(value: str) -> Fraction:
    try:
        return Fraction(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid numeric value: {value!r}") from exc


def parse_float_list(value: str) -> tuple[float, ...]:
    values = tuple(float(part.strip()) for part in value.split(",") if part.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected a comma-separated list of floats")
    return values


def round_fraction(value: Fraction) -> int:
    if value < 0:
        raise ValueError("round_fraction expects a non-negative value")
    return (value.numerator + value.denominator // 2) // value.denominator


def calculate_net_new(start: int, end: int, overlaps: Sequence[tuple[int, int]]) -> int:
    covered = 0
    cursor = start
    for overlap_start, overlap_end in overlaps:
        clipped_start = max(start, overlap_start)
        clipped_end = min(end, overlap_end)
        if clipped_start >= clipped_end:
            continue
        if cursor < clipped_start:
            covered += clipped_start - cursor
        cursor = max(cursor, clipped_end)
    if cursor < end:
        covered += end - cursor
    return covered


def trim_to_target(
    start: int,
    end: int,
    overlaps: Sequence[tuple[int, int]],
    target_delta: int,
) -> int:
    remaining = target_delta
    cursor = start

    for overlap_start, overlap_end in overlaps:
        clipped_start = max(start, overlap_start)
        clipped_end = min(end, overlap_end)
        if clipped_start >= clipped_end:
            continue

        if cursor < clipped_start:
            gap_len = clipped_start - cursor
            if remaining <= gap_len:
                return cursor + remaining
            remaining -= gap_len

        cursor = max(cursor, clipped_end)

    if cursor < end:
        gap_len = end - cursor
        if remaining <= gap_len:
            return cursor + remaining

    raise RuntimeError("could not trim interval to requested target")


def split_circular_interval(start: int, width: int) -> list[tuple[int, int]]:
    if width <= 0:
        raise ValueError("width must be positive")
    universe_size = UNIVERSE_UPPER_EXCLUSIVE - UNIVERSE_LOWER
    if width >= universe_size:
        return [(UNIVERSE_LOWER, UNIVERSE_UPPER_EXCLUSIVE)]

    end = start + width
    if end <= UNIVERSE_UPPER_EXCLUSIVE:
        return [(start, end)]
    return [
        (start, UNIVERSE_UPPER_EXCLUSIVE),
        (UNIVERSE_LOWER, UNIVERSE_LOWER + (end - UNIVERSE_UPPER_EXCLUSIVE)),
    ]


def calculate_net_new_segments(
    segments: Sequence[tuple[int, int]],
    tree: MergedIntervalTree,
) -> int:
    total = 0
    for start, end in segments:
        overlaps = tree.find_overlaps(start, end)
        total += calculate_net_new(start, end, overlaps)
    return total


def trim_width_to_target(
    start: int,
    width: int,
    tree: MergedIntervalTree,
    target_delta: int,
) -> int:
    low = 1
    high = width
    while low < high:
        mid = (low + high) // 2
        net_new = calculate_net_new_segments(split_circular_interval(start, mid), tree)
        if net_new >= target_delta:
            high = mid
        else:
            low = mid + 1

    net_new = calculate_net_new_segments(split_circular_interval(start, low), tree)
    if net_new != target_delta:
        raise RuntimeError("could not trim circular interval to requested target")
    return low


def _normalize_weights(weights: Sequence[float], expected_len: int) -> tuple[float, ...]:
    if len(weights) != expected_len:
        raise ValueError(f"expected {expected_len} cluster weights, got {len(weights)}")
    if any(weight < 0 for weight in weights):
        raise ValueError("cluster weights must be non-negative")
    total = sum(weights)
    if total <= 0:
        raise ValueError("at least one cluster weight must be positive")
    return tuple(weight / total for weight in weights)


def _expand_cluster_values(
    values: tuple[float, ...] | None,
    default: float,
    expected_len: int,
    name: str,
) -> tuple[float, ...]:
    if values is None:
        return tuple(default for _ in range(expected_len))
    if len(values) == 1:
        return tuple(values[0] for _ in range(expected_len))
    if len(values) != expected_len:
        raise ValueError(f"expected 1 or {expected_len} values for {name}, got {len(values)}")
    return values


def _normalized_to_location(value: float, lower: int, upper_inclusive: int) -> int:
    span = upper_inclusive - lower + 1
    normalized = value % 1.0
    offset = min(span - 1, int(normalized * span))
    return lower + offset


def _sample_wrapped_normal_location(
    rng: random.Random,
    mu: float,
    sigma: float,
    lower: int,
    upper_inclusive: int,
) -> int:
    return _normalized_to_location(mu + sigma * rng.gauss(0.0, 1.0), lower, upper_inclusive)


def _build_location_sampler_state(
    rng: random.Random,
    config: GeneratorConfig,
    sampler: str,
    lower: int,
    upper_inclusive: int,
) -> dict:
    if sampler in {"single_cluster", "clustered"}:
        return {
            "cluster_family": "single_cluster",
            "mu": config.cluster_mu,
            "sigma": config.cluster_sigma,
        }
    if sampler in {"multi_cluster", "non_uniform"}:
        if config.cluster_mus is None:
            rotation = rng.random()
            mus = tuple(
                (rotation + cluster_idx / config.num_clusters) % 1.0
                for cluster_idx in range(config.num_clusters)
            )
        else:
            mus = _expand_cluster_values(
                config.cluster_mus,
                config.cluster_mu,
                config.num_clusters,
                "cluster_mus",
            )
        sigmas = _expand_cluster_values(
            config.cluster_sigmas,
            config.cluster_sigma,
            config.num_clusters,
            "cluster_sigmas",
        )
        weights = _normalize_weights(
            config.cluster_weights
            if config.cluster_weights is not None
            else tuple(1.0 for _ in range(config.num_clusters)),
            config.num_clusters,
        )
        if any(not (0 <= mu < 1) for mu in mus):
            raise ValueError("all cluster mus must be in [0, 1)")
        if any(sigma <= 0 for sigma in sigmas):
            raise ValueError("all cluster sigmas must be positive")
        return {
            "cluster_family": "multi_cluster",
            "cluster_count": config.num_clusters,
            "mus": mus,
            "sigmas": sigmas,
            "weights": weights,
        }
    return {}


def _sample_location(
    rng: random.Random,
    sampler: str,
    lower: int,
    upper_inclusive: int,
    state: dict,
) -> int:
    if sampler == "uniform":
        return rng.randint(lower, upper_inclusive)
    if sampler in {"single_cluster", "clustered"}:
        return _sample_wrapped_normal_location(
            rng,
            state["mu"],
            state["sigma"],
            lower,
            upper_inclusive,
        )
    if sampler in {"multi_cluster", "non_uniform"}:
        cluster_idx = rng.choices(
            range(state["cluster_count"]),
            weights=state["weights"],
            k=1,
        )[0]
        return _sample_wrapped_normal_location(
            rng,
            state["mus"][cluster_idx],
            state["sigmas"][cluster_idx],
            lower,
            upper_inclusive,
        )
    if sampler == "edge_biased":
        span = upper_inclusive - lower + 1
        distance = min(rng.randrange(span), rng.randrange(span))
        if rng.randrange(2) == 0:
            return lower + distance
        return upper_inclusive - distance
    if sampler == "center_biased":
        return (rng.randint(lower, upper_inclusive) + rng.randint(lower, upper_inclusive)) // 2
    raise ValueError(f"unsupported location sampler: {sampler}")


def _sample_log_uniform_int(rng: random.Random, lower: int, upper: int) -> int:
    if lower != 1:
        raise ValueError("log_uniform width sampler currently expects lower=1")
    exponent = rng.randint(0, upper.bit_length() - 1)
    bucket_lower = 1 << exponent
    bucket_upper = min((1 << (exponent + 1)) - 1, upper)
    return rng.randint(bucket_lower, bucket_upper)


def _sample_truncated_geometric(
    rng: random.Random,
    upper: int,
    mean_width: int,
) -> int:
    if upper <= 1:
        return 1
    q = min(1.0, max(1.0 / max(1, mean_width), float.fromhex("0x1p-1022")))
    if q >= 1.0:
        return 1

    log_r = math.log1p(-q)
    log_tail = upper * log_r
    one_minus_tail = 1.0 if log_tail < -745 else -math.expm1(log_tail)
    log_value = math.log1p(-rng.random() * one_minus_tail)
    sample = math.ceil(log_value / log_r)
    return min(upper, max(1, sample))


def _sample_reverse_geometric(
    rng: random.Random,
    upper: int,
    mean_width: int,
) -> int:
    geometric_sample = _sample_truncated_geometric(rng, upper, mean_width)
    return upper + 1 - geometric_sample


def _sample_beta_left_skewed_int(
    rng: random.Random,
    lower: int,
    upper: int,
    alpha: float,
    beta_param: float,
) -> int:
    if upper == lower:
        return lower
    normalized = rng.betavariate(alpha, beta_param)
    sample = lower + round(normalized * (upper - lower))
    return min(upper, max(lower, sample))


def _sample_width(
    rng: random.Random,
    config: GeneratorConfig,
    lower: int,
    max_width: int,
    remaining_target: int,
) -> int:
    if max_width < lower:
        raise ValueError("max_width must be >= lower")

    sampler = config.width_sampler
    if sampler == "uniform":
        upper = min(max_width, remaining_target)
        return rng.randint(lower, upper)
    if sampler == "geometric":
        upper = min(max_width, remaining_target)
        mean_width = max(1, round_fraction(config.universe_size * config.geometric_mean_fraction))
        return _sample_truncated_geometric(rng, upper, mean_width)
    if sampler == "reverse_geometric":
        upper = min(max_width, remaining_target)
        mean_width = max(1, round_fraction(config.universe_size * config.geometric_mean_fraction))
        return _sample_reverse_geometric(rng, upper, mean_width)
    if sampler == "log_uniform":
        upper = min(max_width, remaining_target)
        return _sample_log_uniform_int(rng, lower, upper)
    if sampler == "beta_left_skewed":
        upper = min(max_width, remaining_target)
        return _sample_beta_left_skewed_int(
            rng,
            lower,
            upper,
            config.beta_width_alpha,
            config.beta_width_beta,
        )
    if sampler == "exponential":
        upper = min(max_width, remaining_target)
        if upper == lower:
            return lower
        mean = max(1.0, upper / 8.0)
        sample = int(rng.expovariate(1.0 / mean)) + lower
        return min(upper, max(lower, sample))

    raise ValueError(f"unsupported width sampler: {sampler}")


def stochastic_subinterval_coverage(config: GeneratorConfig) -> tuple[list[tuple[int, int]], MergedIntervalTree, int, dict]:
    config.validate()
    rng = random.Random(config.seed)
    location_state = _build_location_sampler_state(
        rng,
        config,
        config.loc_sampler,
        UNIVERSE_LOWER,
        UNIVERSE_UPPER_EXCLUSIVE - 1,
    )
    tree = MergedIntervalTree()
    intervals: list[tuple[int, int]] = []
    covered = 0
    iterations = 0
    stale_iterations = 0
    target = config.target_measure

    while covered < target:
        iterations += 1
        if iterations > config.max_iterations:
            raise RuntimeError(
                f"failed to reach target coverage after {config.max_iterations} iterations"
            )

        remaining_target = target - covered
        max_width = min(config.universe_size, remaining_target)
        width = _sample_width(
            rng,
            config,
            lower=1,
            max_width=max_width,
            remaining_target=remaining_target,
        )
        if stale_iterations >= config.stale_iteration_limit:
            start = tree.first_uncovered_point(UNIVERSE_LOWER, UNIVERSE_UPPER_EXCLUSIVE)
            if start is None:
                raise RuntimeError("no uncovered point remains before target was reached")
            stale_iterations = 0
        else:
            start = _sample_location(
                rng,
                config.loc_sampler,
                UNIVERSE_LOWER,
                UNIVERSE_UPPER_EXCLUSIVE - 1,
                location_state,
            )

        segments = split_circular_interval(start, width)
        net_new = calculate_net_new_segments(segments, tree)
        if net_new <= 0:
            stale_iterations += 1
            continue

        if net_new > remaining_target:
            width = trim_width_to_target(start, width, tree, remaining_target)
            segments = split_circular_interval(start, width)
            net_new = remaining_target

        intervals.append((start, width))
        for segment_start, segment_end in segments:
            tree.insert_and_merge(segment_start, segment_end)
        covered += net_new
        stale_iterations = 0

    sampler_metadata = {
        "loc_sampler": config.loc_sampler,
        "loc_sampler_state": location_state,
        "width_sampler": config.width_sampler,
        "width_sampler_state": {
            "geometric_mean_fraction": str(config.geometric_mean_fraction),
            "beta_width_alpha": config.beta_width_alpha,
            "beta_width_beta": config.beta_width_beta,
        },
    }
    return intervals, tree, iterations, sampler_metadata


def _build_solution_space_assertion(
    variable_name: str,
    intervals: Sequence[tuple[int, int]],
) -> list[str]:
    if len(intervals) == 1:
        start, end = intervals[0]
        return [f"(assert (and (<= {start} {variable_name}) (> {end} {variable_name})))"]

    lines = ["(assert", "  (or"]
    for start, end in intervals:
        # Constants-first comparisons keep InterSect's global Int-bound
        # extractor focused on the explicit universe bounds above.
        lines.append(f"    (and (<= {start} {variable_name}) (> {end} {variable_name}))")
    lines.extend(["  )", ")"])
    return lines


def build_smt2(variable_spaces: Sequence[VariableSolutionSpace]) -> str:
    lines = ["(set-logic QF_LIA)", ""]
    for space in variable_spaces:
        lines.append(f"(declare-const {space.variable_name} Int)")

    lines.append("")
    for space in variable_spaces:
        lines.append(f"(assert (>= {space.variable_name} {UNIVERSE_LOWER}))")
        lines.append(f"(assert (< {space.variable_name} {UNIVERSE_UPPER_EXCLUSIVE}))")

    for space in variable_spaces:
        lines.append("")
        lines.extend(_build_solution_space_assertion(space.variable_name, space.merged_intervals))

    lines.extend(["", "(check-sat)", "(get-model)", ""])
    return "\n".join(lines)


def _linear_interval_dicts(intervals: Sequence[tuple[int, int]]) -> list[dict[str, int]]:
    return [
        {
            "index": index,
            "lower": start,
            "upper_exclusive": end,
            "width": end - start,
        }
        for index, (start, end) in enumerate(intervals)
    ]


def _raw_circular_interval_dicts(intervals: Sequence[tuple[int, int]]) -> list[dict]:
    universe_size = UNIVERSE_UPPER_EXCLUSIVE - UNIVERSE_LOWER
    result = []
    for index, (start, width) in enumerate(intervals):
        segments = split_circular_interval(start, width)
        end_mod = UNIVERSE_LOWER + ((start - UNIVERSE_LOWER + width) % universe_size)
        result.append(
            {
                "index": index,
                "start": start,
                "width": width,
                "end_mod": end_mod,
                "wraps": len(segments) > 1 or width >= universe_size,
                "segments": _linear_interval_dicts(segments),
            }
        )
    return result


def _variable_metadata(config: GeneratorConfig, space: VariableSolutionSpace) -> dict:
    return {
        "variable_name": space.variable_name,
        "seed": space.seed,
        "sampler_metadata": space.sampler_metadata,
        "result": {
            "iterations": space.iterations,
            "raw_interval_count": len(space.intervals),
            "merged_interval_count": len(space.merged_intervals),
            "covered_measure": space.covered_measure,
            "coverage_fraction": f"{space.covered_measure}/{config.universe_size}",
        },
        "intervals": _raw_circular_interval_dicts(space.intervals),
        "merged_intervals": _linear_interval_dicts(space.merged_intervals),
    }


def build_metadata(config: GeneratorConfig, variable_spaces: Sequence[VariableSolutionSpace]) -> dict:
    return {
        "algorithm": "stochastic_subinterval_coverage",
        "logic": "QF_LIA",
        "variable_names": [space.variable_name for space in variable_spaces],
        "universe": {
            "lower": UNIVERSE_LOWER,
            "upper_exclusive": UNIVERSE_UPPER_EXCLUSIVE,
            "upper_inclusive": UNIVERSE_UPPER_EXCLUSIVE - 1,
            "size": config.universe_size,
        },
        "parameters": {
            "target_percentage": str(config.target_percentage),
            "target_measure_per_variable": config.target_measure,
            "num_vars": config.num_vars,
            "seed": config.seed,
            "loc_sampler": config.loc_sampler,
            "width_sampler": config.width_sampler,
            "cluster_mu": config.cluster_mu,
            "cluster_sigma": config.cluster_sigma,
            "num_clusters": config.num_clusters,
            "cluster_mus": config.cluster_mus,
            "cluster_sigmas": config.cluster_sigmas,
            "cluster_weights": config.cluster_weights,
            "geometric_mean_fraction": str(config.geometric_mean_fraction),
            "beta_width_alpha": config.beta_width_alpha,
            "beta_width_beta": config.beta_width_beta,
            "max_iterations": config.max_iterations,
            "stale_iteration_limit": config.stale_iteration_limit,
        },
        "variables": [_variable_metadata(config, space) for space in variable_spaces],
    }


def _variable_name(base_name: str, num_vars: int, index: int) -> str:
    if num_vars == 1:
        return base_name
    return f"{base_name}{index}"


def _variable_seed(seed: int | None, index: int) -> int | None:
    return None if seed is None else seed + index


def _formula_seed(seed: int | None, index: int) -> int | None:
    return None if seed is None else seed + index * FORMULA_SEED_STRIDE


def _formula_target_percentage(target_percentage: Fraction, target_index: int) -> Fraction:
    return target_percentage / (FORMULA_SCALE_FACTOR**target_index)


def _formula_geometric_mean_fraction(
    geometric_mean_fraction: Fraction,
    target_index: int,
) -> Fraction:
    return geometric_mean_fraction / (FORMULA_SCALE_FACTOR**target_index)


def _indexed_output_path(path: Path | None, index: int, count: int) -> Path | None:
    if path is None or count == 1:
        return path
    suffix_width = max(3, len(str(count)))
    return path.with_name(f"{path.stem}_{index + 1:0{suffix_width}d}{path.suffix}")


def generate_solution_space(config: GeneratorConfig) -> GeneratedSolutionSpace:
    config.validate()
    variable_spaces: list[VariableSolutionSpace] = []

    for var_idx in range(config.num_vars):
        var_seed = _variable_seed(config.seed, var_idx)
        var_config = GeneratorConfig(
            target_percentage=config.target_percentage,
            num_vars=1,
            seed=var_seed,
            variable_name=_variable_name(config.variable_name, config.num_vars, var_idx),
            loc_sampler=config.loc_sampler,
            width_sampler=config.width_sampler,
            cluster_mu=config.cluster_mu,
            cluster_sigma=config.cluster_sigma,
            num_clusters=config.num_clusters,
            cluster_mus=config.cluster_mus,
            cluster_sigmas=config.cluster_sigmas,
            cluster_weights=config.cluster_weights,
            geometric_mean_fraction=config.geometric_mean_fraction,
            beta_width_alpha=config.beta_width_alpha,
            beta_width_beta=config.beta_width_beta,
            max_iterations=config.max_iterations,
            stale_iteration_limit=config.stale_iteration_limit,
        )
        intervals, tree, iterations, sampler_metadata = stochastic_subinterval_coverage(var_config)
        merged_intervals = tree.intervals
        covered_measure = sum(end - start for start, end in merged_intervals)
        if covered_measure != config.target_measure:
            raise RuntimeError(
                f"covered measure {covered_measure} differs from target {config.target_measure} "
                f"for variable {var_config.variable_name}"
            )
        variable_spaces.append(
            VariableSolutionSpace(
                variable_name=var_config.variable_name,
                intervals=intervals,
                merged_intervals=merged_intervals,
                covered_measure=covered_measure,
                iterations=iterations,
                seed=var_seed,
                sampler_metadata=sampler_metadata,
            )
        )

    smt2 = build_smt2(variable_spaces)
    metadata = build_metadata(config, variable_spaces)
    total_covered_measure = sum(space.covered_measure for space in variable_spaces)
    return GeneratedSolutionSpace(
        config=config,
        intervals=[interval for space in variable_spaces for interval in space.intervals],
        merged_intervals=[
            interval for space in variable_spaces for interval in space.merged_intervals
        ],
        covered_measure=total_covered_measure,
        iterations=sum(space.iterations for space in variable_spaces),
        smt2=smt2,
        metadata=metadata,
    )


def verify_with_z3(smt2_path: Path) -> None:
    if shutil.which("z3") is None:
        raise RuntimeError("z3 executable not found; cannot verify generated formula")
    result = subprocess.run(
        ["z3", str(smt2_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    first_line = (result.stdout.strip().splitlines() or [""])[0]
    if result.returncode != 0 or first_line != "sat":
        raise RuntimeError(
            "z3 did not report sat for generated formula: "
            f"returncode={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


PLOT_STYLES = ("bars", "points")


def _plot_solution_space_bars(ax, variables: Sequence[dict], universe_size: int) -> None:
    for row_idx, variable in enumerate(variables):
        y = len(variables) - row_idx - 1
        bars = []
        for interval in variable["merged_intervals"]:
            start = interval["lower"] / universe_size
            width = interval["width"] / universe_size
            bars.append((start, width))
        if bars:
            ax.broken_barh(bars, (y - 0.18, 0.36), facecolors="#1f77b4", linewidth=0)


def _plot_solution_space_points(fig, ax, variables: Sequence[dict], universe_size: int) -> None:
    xs = []
    ys = []
    widths = []
    for row_idx, variable in enumerate(variables):
        y = len(variables) - row_idx - 1
        for interval in variable["merged_intervals"]:
            lower = interval["lower"]
            width = interval["width"]
            midpoint = lower + width / 2
            xs.append(midpoint / universe_size)
            ys.append(y)
            widths.append(width / universe_size)

    if not xs:
        return

    scatter = ax.scatter(
        xs,
        ys,
        c=widths,
        cmap="coolwarm",
        s=18,
        marker="|",
        linewidths=1.4,
    )
    colorbar = fig.colorbar(scatter, ax=ax, pad=0.012)
    colorbar.set_label("Interval width / universe")


def plot_solution_space(metadata: dict, plot_path: Path, plot_style: str = "bars") -> None:
    if plot_style not in PLOT_STYLES:
        raise ValueError(f"unsupported plot style: {plot_style}")

    matplotlib_cache_dir = Path("tmp/matplotlib").resolve()
    matplotlib_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache_dir))
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib and numpy are required for --plot. "
            "Install them in the active environment or run with the project .venv."
        ) from exc

    universe_size = metadata["universe"]["size"]
    variables = metadata["variables"]
    height = max(2.8, 0.55 * len(variables) + 1.6)
    fig, ax = plt.subplots(figsize=(12, height))

    if plot_style == "bars":
        _plot_solution_space_bars(ax, variables, universe_size)
    else:
        _plot_solution_space_points(fig, ax, variables, universe_size)

    ax.set_xlim(0, 1)
    ax.set_ylim(-0.6, len(variables) - 0.4)
    ax.set_xlabel("Normalized universe [0, 1]")
    ax.set_yticks(range(len(variables)))
    ax.set_yticklabels([variable["variable_name"] for variable in reversed(variables)])
    ax.set_title(f"Generated solution space ({plot_style})")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()

    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, dpi=200)
    plt.close(fig)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an Int SMT formula using stochastic subinterval coverage."
    )
    parser.add_argument(
        "--target-percentage",
        type=parse_fraction,
        required=True,
        help="target solution-space percentage in (0, 100], e.g. 10 or 0.001",
    )
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    parser.add_argument(
        "--num-formulas",
        "--num-formula",
        dest="num_formulas",
        type=int,
        default=1,
        help=(
            "number of target-coverage levels to generate; each level scales "
            f"target coverage by {FORMULA_SCALE_FACTOR}^index"
        ),
    )
    parser.add_argument(
        "--formulas-per-target",
        "--formulas-per-coverage",
        type=int,
        default=1,
        help="number of independent formulas to generate for each target-coverage level",
    )
    parser.add_argument(
        "--num-vars",
        type=int,
        default=1,
        help="number of Int variables, each with its own sampled solution space",
    )
    parser.add_argument("--variable-name", default=DEFAULT_VARIABLE_NAME)
    parser.add_argument(
        "--loc-sampler",
        choices=LOC_SAMPLERS,
        default=DEFAULT_LOC_SAMPLER,
        help="candidate location sampler",
    )
    parser.add_argument(
        "--width-sampler",
        choices=WIDTH_SAMPLERS,
        default=DEFAULT_WIDTH_SAMPLER,
        help=(
            "candidate width sampler; 'uniform' samples all admissible widths equally, "
            "'geometric' favors short widths, 'reverse_geometric' favors long widths "
            "as the reflected geometric case, 'log_uniform' samples widths by scale, "
            "'beta_left_skewed' samples many large widths with a bounded beta law, "
            "and 'exponential' is kept for compatibility"
        ),
    )
    parser.add_argument(
        "--cluster-mu",
        type=float,
        default=DEFAULT_CLUSTER_MU,
        help="single-cluster normalized center in [0, 1)",
    )
    parser.add_argument(
        "--cluster-sigma",
        type=float,
        default=DEFAULT_CLUSTER_SIGMA,
        help="wrapped-normal cluster compactness",
    )
    parser.add_argument(
        "--num-clusters",
        type=int,
        default=DEFAULT_NUM_CLUSTERS,
        help="number of components for --loc-sampler multi_cluster",
    )
    parser.add_argument(
        "--cluster-mus",
        type=parse_float_list,
        default=None,
        help="comma-separated normalized centers for multi_cluster",
    )
    parser.add_argument(
        "--cluster-sigmas",
        type=parse_float_list,
        default=None,
        help="comma-separated sigmas for multi_cluster; one value is broadcast",
    )
    parser.add_argument(
        "--cluster-weights",
        type=parse_float_list,
        default=None,
        help="comma-separated mixture weights for multi_cluster",
    )
    parser.add_argument(
        "--geometric-mean-fraction",
        type=parse_fraction,
        default=DEFAULT_GEOMETRIC_MEAN_FRACTION,
        help=(
            "initial desired geometric mean width as a fraction of the universe; "
            "when generating multiple formulas it is scaled by the same "
            f"{FORMULA_SCALE_FACTOR}^index factor as --target-percentage; used by "
            "geometric and reverse_geometric"
        ),
    )
    parser.add_argument(
        "--beta-width-alpha",
        type=float,
        default=DEFAULT_BETA_WIDTH_ALPHA,
        help=(
            "alpha parameter for --width-sampler beta_left_skewed; must be greater "
            f"than beta for left skew (default: {DEFAULT_BETA_WIDTH_ALPHA})"
        ),
    )
    parser.add_argument(
        "--beta-width-beta",
        type=float,
        default=DEFAULT_BETA_WIDTH_BETA,
        help=(
            "beta parameter for --width-sampler beta_left_skewed; must be smaller "
            f"than alpha for left skew (default: {DEFAULT_BETA_WIDTH_BETA})"
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=1_000_000)
    parser.add_argument("--stale-iteration-limit", type=int, default=10_000)
    parser.add_argument(
        "--out-smt2",
        type=Path,
        default=Path("benchmarks/solution_space/solution_space.smt2"),
        help="path for the generated SMT-LIB file",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path("benchmarks/solution_space_info/solution_space.json"),
        help="optional path for metadata JSON",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="run z3 on the generated SMT-LIB file and require sat",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="save a normalized [0, 1] plot of the generated solution spaces",
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
        "--plot-out",
        type=Path,
        default=None,
        help="optional path for the plot PNG; defaults to --out-smt2 with .png suffix",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.num_formulas <= 0:
        raise ValueError("num_formulas must be positive")
    if args.formulas_per_target <= 0:
        raise ValueError("formulas_per_target must be positive")

    total_formulas = args.num_formulas * args.formulas_per_target

    generated_spaces = []
    for target_idx in range(args.num_formulas):
        formula_target_percentage = _formula_target_percentage(
            args.target_percentage,
            target_idx,
        )
        formula_geometric_mean_fraction = _formula_geometric_mean_fraction(
            args.geometric_mean_fraction,
            target_idx,
        )
        for repeat_idx in range(args.formulas_per_target):
            formula_idx = target_idx * args.formulas_per_target + repeat_idx
            formula_seed = _formula_seed(args.seed, formula_idx)
            config = GeneratorConfig(
                target_percentage=formula_target_percentage,
                num_vars=args.num_vars,
                seed=formula_seed,
                variable_name=args.variable_name,
                loc_sampler=args.loc_sampler,
                width_sampler=args.width_sampler,
                cluster_mu=args.cluster_mu,
                cluster_sigma=args.cluster_sigma,
                num_clusters=args.num_clusters,
                cluster_mus=args.cluster_mus,
                cluster_sigmas=args.cluster_sigmas,
                cluster_weights=args.cluster_weights,
                geometric_mean_fraction=formula_geometric_mean_fraction,
                beta_width_alpha=args.beta_width_alpha,
                beta_width_beta=args.beta_width_beta,
                max_iterations=args.max_iterations,
                stale_iteration_limit=args.stale_iteration_limit,
            )
            generated = generate_solution_space(config)
            generated.metadata["batch"] = {
                "formula_index": formula_idx,
                "formula_number": formula_idx + 1,
                "target_index": target_idx,
                "target_number": target_idx + 1,
                "repeat_index": repeat_idx,
                "repeat_number": repeat_idx + 1,
                "num_target_coverages": args.num_formulas,
                "formulas_per_target": args.formulas_per_target,
                "total_formulas": total_formulas,
                "num_formulas": total_formulas,
                "num_formula": total_formulas,
                "base_target_percentage": str(args.target_percentage),
                "target_percentage": str(formula_target_percentage),
                "base_geometric_mean_fraction": str(args.geometric_mean_fraction),
                "geometric_mean_fraction": str(formula_geometric_mean_fraction),
                "beta_width_alpha": args.beta_width_alpha,
                "beta_width_beta": args.beta_width_beta,
                "base_seed": args.seed,
                "formula_seed": formula_seed,
            }

            smt2_path = _indexed_output_path(args.out_smt2, formula_idx, total_formulas)
            json_path = _indexed_output_path(args.out_json, formula_idx, total_formulas)
            if args.plot_out is None:
                plot_path = smt2_path.with_suffix(".png") if smt2_path is not None else None
            else:
                plot_path = _indexed_output_path(args.plot_out, formula_idx, total_formulas)

            _write_text(smt2_path, generated.smt2)
            if json_path is not None:
                _write_json(json_path, generated.metadata)
            if args.verify:
                verify_with_z3(smt2_path)
            if args.plot:
                if plot_path is None:
                    raise RuntimeError("plot path could not be derived")
                plot_solution_space(generated.metadata, plot_path, args.plot_style)

            generated_spaces.append((generated, smt2_path, json_path, plot_path))
            print(f"Wrote SMT-LIB formula {formula_idx + 1}/{total_formulas} to {smt2_path}")
            if json_path is not None:
                print(f"Wrote metadata {formula_idx + 1}/{total_formulas} to {json_path}")
            if args.plot:
                print(f"Wrote solution-space plot {formula_idx + 1}/{total_formulas} to {plot_path}")

    total_raw_intervals = sum(len(generated.intervals) for generated, _, _, _ in generated_spaces)
    total_merged_intervals = sum(
        len(generated.merged_intervals) for generated, _, _, _ in generated_spaces
    )
    total_iterations = sum(generated.iterations for generated, _, _, _ in generated_spaces)

    print(f"Base target percentage: {args.target_percentage}")
    print("Target coverage levels:")
    for target_idx in range(args.num_formulas):
        target_percentage = _formula_target_percentage(args.target_percentage, target_idx)
        geometric_mean_fraction = _formula_geometric_mean_fraction(
            args.geometric_mean_fraction,
            target_idx,
        )
        print(
            f"  {target_idx + 1}: target={target_percentage}, "
            f"geometric_mean_fraction={geometric_mean_fraction}, "
            f"formulas={args.formulas_per_target}"
        )
    print(f"Target coverage levels: {args.num_formulas}")
    print(f"Formulas per target coverage: {args.formulas_per_target}")
    print(f"Total formulas: {total_formulas}")
    print(f"Variables per formula: {args.num_vars}")
    print(f"Final covered measure per variable: {generated_spaces[-1][0].config.target_measure}")
    print(f"Raw intervals total: {total_raw_intervals}")
    print(f"Merged intervals total: {total_merged_intervals}")
    print(f"Iterations total: {total_iterations}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
