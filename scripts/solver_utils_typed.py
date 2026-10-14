import csv
import itertools
import random
import re
import shutil
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

try:
    from tqdm.auto import tqdm
except ImportError:
    def tqdm(iterable=None, **kwargs):
        return iterable if iterable is not None else []

import pandas as pd
from scipy.stats import beta, ks_2samp, mannwhitneyu

from solver_utils import (
    create_mask_with_coverage,
    create_string_mask_with_coverage,
    expand_multiset,
    multiset_from_intervals_2,
    run_z3_and_get_model,
    uniform_multiset,
)


BITVEC_RE = re.compile(r"^\(_\s+BitVec\s+(\d+)\)$")
STRING_MASK_START_TEMPLATE = ";; STRING_INTERVAL_MASK_START {var_name}"
STRING_MASK_END_TEMPLATE = ";; STRING_INTERVAL_MASK_END {var_name}"
STRING_TO_INDEX_HELPERS_START = ";; STRING_TO_INDEX_HELPERS_START"
STRING_TO_INDEX_HELPERS_END = ";; STRING_TO_INDEX_HELPERS_END"
STRING_ORDER_SHORTLEX = "shortlex"
STRING_ORDER_LEX = "lex"
DEFAULT_STRING_ORDER = STRING_ORDER_SHORTLEX

RECURSIVE_STRING_TO_INDEX_HELPERS_SHORTLEX = """;; STRING_TO_INDEX_HELPERS_START
;; Helper: Calculate 26^n
(define-fun-rec pow26 ((n Int)) Int
  (if (<= n 0)
      1
      (* 26 (pow26 (- n 1)))))

;; Helper: sum(26^k for k in range(1, len))
;; This counts all possible strings shorter than the current string.
(define-fun-rec sumPrevLengths ((len Int)) Int
  (if (<= len 1)
      0
      (+ (pow26 (- len 1)) (sumPrevLengths (- len 1)))))

;; Helper: check that the string only uses lowercase a-z characters.
(define-fun-rec isLowerAlphaRec ((s String) (idx Int)) Bool
  (if (>= idx (str.len s))
      true
      (let ((code (str.to_code (str.at s idx))))
        (and (<= 97 code)
             (<= code 122)
             (isLowerAlphaRec s (+ idx 1))))))

(define-fun isLowerAlphaString ((s String)) Bool
  (isLowerAlphaRec s 0))

;; Helper: recursive offset calculation (base-26 conversion).
(define-fun-rec offsetRec ((s String) (idx Int)) Int
  (if (>= idx (str.len s))
      0
      (+ (* (- (str.to_code (str.at s idx)) 97)
            (pow26 (- (- (str.len s) 1) idx)))
         (offsetRec s (+ idx 1)))))

;; Main helper function: stringToIndex
(define-fun stringToIndex ((s String)) Int
  (+ (sumPrevLengths (str.len s))
     (offsetRec s 0)))
;; STRING_TO_INDEX_HELPERS_END
"""

RECURSIVE_STRING_TO_INDEX_HELPERS_LEX = """;; STRING_TO_INDEX_HELPERS_START
;; Helper: Calculate 26^n
(define-fun-rec pow26 ((n Int)) Int
  (if (<= n 0)
      1
      (* 26 (pow26 (- n 1)))))

;; Helper: size of a lexicographic subtree with `remaining` positions left.
(define-fun-rec lexSubtreeSize ((remaining Int)) Int
  (if (<= remaining 0)
      1
      (+ 1 (* 26 (lexSubtreeSize (- remaining 1))))))

;; Helper: check that the string only uses lowercase a-z characters.
(define-fun-rec isLowerAlphaRec ((s String) (idx Int)) Bool
  (if (>= idx (str.len s))
      true
      (let ((code (str.to_code (str.at s idx))))
        (and (<= 97 code)
             (<= code 122)
             (isLowerAlphaRec s (+ idx 1))))))

(define-fun isLowerAlphaString ((s String)) Bool
  (isLowerAlphaRec s 0))

;; Helper: recursive lexicographic rank calculation.
(define-fun-rec offsetRecLex ((s String) (idx Int)) Int
  (if (>= idx (str.len s))
      0
      (+ (* (- (str.to_code (str.at s idx)) 97)
            (lexSubtreeSize (- (- (str.len s) 1) idx)))
         (offsetRecLex s (+ idx 1)))))

;; Main helper function: stringToIndex
(define-fun stringToIndex ((s String)) Int
  (+ (- (str.len s) 1)
     (offsetRecLex s 0)))
;; STRING_TO_INDEX_HELPERS_END
"""


MIN_STRING_MAX_LENGTH = 8
MAX_STRING_MAX_LENGTH = 54
REMOVAL_MODE_ONE = "ONE"
REMOVAL_MODE_CHUNK = "CHUNK"


def _validate_timeout(timeout):
    if timeout is None:
        return None
    if not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a number of seconds or None.")
    if timeout < 0:
        raise ValueError("timeout must be >= 0.")
    return float(timeout)


def _timeout_expired(deadline):
    return deadline is not None and time.perf_counter() >= deadline


def _build_length_dispatch(cases, fallback, base_indent="      ", var_name="len"):
    expr = str(fallback)
    for length, value in reversed(cases):
        expr = f"(if (= {var_name} {length}) {value}\n{base_indent}{expr})"
    return expr


def _build_sum_prev_lengths_expr(max_len):
    cases = []
    cumulative = 0
    for length in range(1, max_len + 1):
        cases.append((length, cumulative))
        cumulative += 26 ** length
    return _build_length_dispatch(cases, cases[-1][1])


def _build_lex_subtree_size_expr(max_len):
    cases = []
    cumulative = 1
    for remaining in range(0, max_len + 1):
        cases.append((remaining, cumulative))
        cumulative = 1 + (26 * cumulative)
    return _build_length_dispatch(cases, cases[-1][1], var_name="remaining")


def _build_char_weight_expr(idx, max_len):
    cases = []
    for length in range(idx + 1, max_len + 1):
        cases.append((length, 26 ** (length - 1 - idx)))
    return _build_length_dispatch(cases, 0)


def _build_lex_char_weight_expr(idx, max_len):
    cases = []
    for length in range(idx + 1, max_len + 1):
        remaining = length - idx - 1
        subtree_size = sum(26 ** k for k in range(0, remaining + 1))
        cases.append((length, subtree_size))
    return _build_length_dispatch(cases, 1)


def _build_is_lower_alpha_clauses(max_len):
    clauses = [f"      (<= len {max_len})"]
    for idx in range(max_len):
        clauses.append(
            "      "
            f"(or (<= len {idx}) "
            f"(let ((code{idx} (str.to_code (str.at s {idx})))) "
            f"(and (<= 97 code{idx}) (<= code{idx} 122))))"
        )
    return "\n".join(clauses)


def _build_string_index_terms(max_len):
    terms = []
    for idx in range(max_len):
        weight_expr = _build_char_weight_expr(idx, max_len)
        terms.append(
            f"      (if (> len {idx})\n"
            f"          (* (- (str.to_code (str.at s {idx})) 97)\n"
            f"             {weight_expr})\n"
            f"          0)"
        )
    return "\n".join(terms)


def _build_lex_string_index_terms(max_len):
    terms = []
    for idx in range(max_len):
        weight_expr = _build_lex_char_weight_expr(idx, max_len)
        terms.append(
            f"      (if (> len {idx})\n"
            f"          (* (- (str.to_code (str.at s {idx})) 97)\n"
            f"             {weight_expr})\n"
            f"          0)"
        )
    return "\n".join(terms)


def _build_non_recursive_string_to_index_helpers(max_len):
    return f""";; STRING_TO_INDEX_HELPERS_START
;; Helper: count all strings shorter than the current string length
(define-fun sumPrevLengths ((len Int)) Int
  (if (<= len 1)
      0
      {_build_sum_prev_lengths_expr(max_len)}))

;; Helper: check that the string only uses lowercase a-z characters
(define-fun isLowerAlphaString ((s String)) Bool
  (let ((len (str.len s)))
    (and
{_build_is_lower_alpha_clauses(max_len)})))

;; Main helper function: stringToIndex
(define-fun stringToIndex ((s String)) Int
  (let ((len (str.len s)))
    (+ (sumPrevLengths len)
{_build_string_index_terms(max_len)})))
;; STRING_TO_INDEX_HELPERS_END
"""


def _build_non_recursive_lex_string_to_index_helpers(max_len):
    return f""";; STRING_TO_INDEX_HELPERS_START
;; Helper: count lexicographic subtree sizes
(define-fun lexSubtreeSize ((remaining Int)) Int
  (if (<= remaining 0)
      1
      {_build_lex_subtree_size_expr(max_len)}))

;; Helper: check that the string only uses lowercase a-z characters
(define-fun isLowerAlphaString ((s String)) Bool
  (let ((len (str.len s)))
    (and
{_build_is_lower_alpha_clauses(max_len)})))

;; Main helper function: stringToIndex
(define-fun stringToIndex ((s String)) Int
  (let ((len (str.len s)))
    (+ (- len 1)
{_build_lex_string_index_terms(max_len)})))
;; STRING_TO_INDEX_HELPERS_END
"""


def hybrid_state_space_sampler_typed(
    smt_filepath,
    free_vars = None,
    theta=256,
    coverage=50,
    delta=0.0050,
    use_recursive_string_helpers=True,
    string_max_length=54,
    timeout=None,
    output_filename=None,
):
    """
    Typed variant of hybrid_state_space_sampler.

    free_vars must be a list of (var_name, var_type) pairs, such as the output of
    extract_free_vars_with_type_from_smt2_file.

    """
    return _hybrid_state_space_sampler_typed_impl(
        smt_filepath,
        free_vars=free_vars,
        theta=theta,
        coverage=coverage,
        delta=delta,
        use_recursive_string_helpers=use_recursive_string_helpers,
        string_max_length=string_max_length,
        complement_on_unsat=False,
        results_suffix="",
        timeout=timeout,
        output_filename=output_filename,
    )


def hybrid_state_space_sampler_hsss_typed(
    smt_filepath,
    free_vars=None,
    theta=256,
    coverage=50,
    delta=0.0050,
    use_recursive_string_helpers=True,
    string_max_length=54,
    removal_mode=REMOVAL_MODE_ONE,
    chunk_size=None,
    timeout=None,
    output_filename=None,
    results_suffix="_hsss",
):
    """
    HSSS typed variant.

    When the solver returns UNSAT or UNKNOWN while masks are active, the algorithm
    complements the current masks, removes one interval from each complemented mask
    using a minimal-distortion heuristic, and continues the exploration.
    """
    return _hybrid_state_space_sampler_typed_impl(
        smt_filepath,
        free_vars=free_vars,
        theta=theta,
        coverage=coverage,
        delta=delta,
        use_recursive_string_helpers=use_recursive_string_helpers,
        string_max_length=string_max_length,
        complement_on_unsat=True,
        results_suffix=results_suffix,
        removal_mode=removal_mode,
        chunk_size=chunk_size,
        timeout=timeout,
        output_filename=output_filename,
    )


def sampler_typed_always_new_mask(
    smt_filepath,
    free_vars=None,
    theta=256,
    coverage=50,
    delta=0.0050,
    use_recursive_string_helpers=True,
    string_max_length=54,
    timeout=None,
    output_filename=None,
    results_suffix="_always_new_mask",
):
    """
    Typed sampler that always restarts from a fresh mask set after each save.

    Behaviour:
    - generate masks, run the solver
    - if SAT: save result, then restart from the original SMT and build new masks
    - if UNSAT/UNKNOWN: complement the current masks without removing intervals,
      rerun the solver, save that outcome, then restart from the original SMT
      and build new masks
    """
    timeout_seconds = _validate_timeout(timeout)
    string_domain = _get_string_domain_config(string_max_length)

    S = set()
    E = set()
    L = set()
    solver_result = {}
    blocked_models = []

    if free_vars is None:
        free_vars = extract_free_vars_with_type_from_smt2_file(smt_filepath)
    print(f"Extracted free variables: {free_vars}")

    tmp_dir = Path(__file__).parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_filepath = tmp_dir / f"tmp_{uuid.uuid4().hex}.smt2"
    shutil.copy2(smt_filepath, tmp_filepath)

    interrupted = False

    try:
        start_time = time.perf_counter()
        deadline = None if timeout_seconds is None else start_time + timeout_seconds

        for free_var_name, free_var_type in free_vars:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            partition_variable_typed(
                E,
                L,
                smt_filepath,
                free_var_name,
                free_var_type,
                theta=theta,
                string_domain=string_domain,
            )

        assignment_iter = [None]
        if E:
            assignment_iter = get_assignment_for_typed_E(E, limit=theta)

        for assignment in assignment_iter:
            while True:
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S

                _reset_typed_tmp_formula(smt_filepath, tmp_filepath, assignment)
                for blocked_model in blocked_models:
                    prevent_same_model_typed(blocked_model, L, tmp_filepath)
                mask_var_pairs = _generate_and_apply_typed_masks(
                    L,
                    tmp_filepath,
                    coverage,
                    delta,
                    use_recursive_string_helpers,
                    string_domain,
                )

                if not mask_var_pairs:
                    status, model = run_z3_and_get_model(tmp_filepath)
                    if _timeout_expired(deadline):
                        print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                        return S
                    partial_time = time.perf_counter() - start_time
                    start_time = time.perf_counter()
                    if status in ("UNSAT", "UNKNOWN"):
                        save_model_typed(
                            solver_result,
                            model,
                            free_vars,
                            tmp_filepath,
                            status,
                            assignment,
                            [],
                            partial_time,
                            L,
                        )
                    elif status == "SAT":
                        save_sat_model_typed(
                            solver_result,
                            model,
                            free_vars,
                            tmp_filepath,
                            status,
                            [],
                            partial_time,
                            L,
                        )
                        S.add(frozenset(model.items()))
                    break

                status, model = run_z3_and_get_model(tmp_filepath)
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S
                partial_time = time.perf_counter() - start_time
                start_time = time.perf_counter()

                if status == "SAT":
                    save_sat_model_typed(
                        solver_result,
                        model,
                        free_vars,
                        tmp_filepath,
                        status,
                        mask_var_pairs,
                        partial_time,
                        L,
                    )
                    blocked_models.append(dict(model.items()))
                    prevent_same_model_typed(model, L, tmp_filepath)
                    S.add(frozenset(model.items()))
                    continue

                if status in ("UNSAT", "UNKNOWN"):
                    complemented_masks = complement_mask_var_pairs_typed(mask_var_pairs, L)
                    _reset_typed_tmp_formula(smt_filepath, tmp_filepath, assignment)
                    for blocked_model in blocked_models:
                        prevent_same_model_typed(blocked_model, L, tmp_filepath)
                    apply_mask_var_pairs_typed(
                        complemented_masks,
                        L,
                        tmp_filepath,
                        use_recursive_string_helpers=use_recursive_string_helpers,
                        string_domain=string_domain,
                    )

                    status, model = run_z3_and_get_model(tmp_filepath)
                    if _timeout_expired(deadline):
                        print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                        return S
                    partial_time = time.perf_counter() - start_time
                    start_time = time.perf_counter()

                    if status in ("UNSAT", "UNKNOWN"):
                        save_model_typed(
                            solver_result,
                            model,
                            free_vars,
                            tmp_filepath,
                            status,
                            assignment,
                            complemented_masks,
                            partial_time,
                            L,
                        )
                    elif status == "SAT":
                        save_sat_model_typed(
                            solver_result,
                            model,
                            free_vars,
                            tmp_filepath,
                            status,
                            complemented_masks,
                            partial_time,
                            L,
                        )
                        blocked_models.append(dict(model.items()))
                        prevent_same_model_typed(model, L, tmp_filepath)
                        S.add(frozenset(model.items()))
                    continue

        if not E and not L:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            status, model = run_z3_and_get_model(tmp_filepath)
            partial_time = time.perf_counter() - start_time
            if status in ("UNSAT", "UNKNOWN"):
                save_model_typed(solver_result, model, free_vars, tmp_filepath, status, None, [], partial_time, L)
            elif status == "SAT":
                save_sat_model_typed(solver_result, model, free_vars, tmp_filepath, status, [], partial_time, L)
                S.add(frozenset(model.items()))
    except KeyboardInterrupt:
        interrupted = True
        print("\nKeyboardInterrupt ricevuto: salvataggio dei risultati parziali in corso...")
    finally:
        _write_solver_results_typed(
            solver_result,
            smt_filepath,
            coverage,
            string_domain["max_len"],
            results_suffix=results_suffix,
            output_filename=output_filename,
        )

    if interrupted:
        raise KeyboardInterrupt

    return S


def intervalGuidedSampler(
    smt_filepath,
    free_vars=None,
    theta=256,
    coverage=50,
    delta=0.0050,
    use_recursive_string_helpers=False,
    string_order=DEFAULT_STRING_ORDER,
    U=2**256 - 1,
    timeout=None,
    output_filename=None,
    results_suffix="_interval_guided_sampler",
    threads=None,
    num_samples=None,
    results_root=None,
):
    """
    Typed sampler that always restarts from a fresh mask set after each save.

    Behaviour:
    - generate masks, run the solver
    - if SAT: save result, then restart from the original SMT and build new masks
    - if UNSAT/UNKNOWN: save result, then restart from the original SMT and build new masks

    Unlike sampler_typed_always_new_mask, this variant never applies any
    complement strategy.
    """
    timeout_seconds = _validate_timeout(timeout)
    string_domain = _get_string_domain_config(MAX_STRING_MAX_LENGTH, string_order=string_order)
    bayesian_min_iterations = 20
    bayesian_min_viable_sat_rate = 0.53
    bayesian_confidence = 0.95
    if num_samples is not None:
        if not isinstance(num_samples, int):
            raise TypeError("num_samples must be an integer or None.")
        if num_samples <= 0:
            raise ValueError("num_samples must be positive when provided.")

    S = set()
    E = set()
    L = set()
    solver_result = {}
    blocked_models = []

    if free_vars is None:
        free_vars = extract_free_vars_with_type_from_smt2_file(smt_filepath)
    print(f"Extracted free variables: {free_vars}")

    tmp_dir = Path(__file__).parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_filepath = tmp_dir / f"tmp_{uuid.uuid4().hex}.smt2"
    shutil.copy2(smt_filepath, tmp_filepath)

    results_parent = Path(results_root) if results_root is not None else Path(smt_filepath).parent.parent
    results_dir = results_parent / (
        Path(smt_filepath).parent.name + "_results" + results_suffix + "_cov" + str(coverage)
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        csv_filename = Path(smt_filepath).stem + ".csv"
    else:
        csv_filename = Path(output_filename)
        if csv_filename.suffix.lower() != ".csv":
            csv_filename = csv_filename.with_suffix(".csv")
    csv_filepath = results_dir / csv_filename
    sat_count = _count_existing_sat_rows(csv_filepath)
    if sat_count:
        print(f"[INFO] Found {sat_count} existing SAT solutions in {csv_filepath}; appending new results.")
    if num_samples is not None and sat_count >= num_samples:
        print(f"[INFO] Existing SAT solutions already reach num_samples={num_samples}; stopping.")
        return S

    interrupted = False

    def _save_and_flush(status, model, assignment, mask_var_pairs, partial_time):
        nonlocal sat_count

        if status in ("UNSAT", "UNKNOWN"):
            save_model_typed(
                solver_result,
                model,
                free_vars,
                tmp_filepath,
                status,
                assignment,
                mask_var_pairs,
                partial_time,
                L,
            )
        elif status == "SAT":
            save_sat_model_typed(
                solver_result,
                model,
                free_vars,
                tmp_filepath,
                status,
                mask_var_pairs,
                partial_time,
                L,
            )
            S.add(frozenset(model.items()))
            sat_count += 1
        _append_solver_result_row_typed(solver_result, csv_filepath)
        if num_samples is not None and sat_count >= num_samples:
            print(f"[INFO] Reached num_samples={num_samples} SAT solutions, saving CSV and stopping.")
            return True
        return False

    def _update_bayesian_counts(status, successes, failures):
        if status == "SAT":
            successes += 1
        elif status in ("UNSAT", "UNKNOWN"):
            failures += 1
        return successes, failures

    def _should_stop_due_to_bayesian_test(successes, failures):
        total = successes + failures
        if total < bayesian_min_iterations:
            return False, 0.0

        prob_sat_below_min = float(
            beta.cdf(
                bayesian_min_viable_sat_rate,
                1 + successes,
                1 + failures,
            )
        )
        return prob_sat_below_min >= bayesian_confidence, prob_sat_below_min

    try:
        start_time = time.perf_counter()
        deadline = None if timeout_seconds is None else start_time + timeout_seconds

        for free_var_name, free_var_type in free_vars:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            partition_variable_typed(
                E,
                L,
                smt_filepath,
                free_var_name,
                free_var_type,
                theta=theta,
                string_domain=string_domain,
                U=U,
            )

        assignment_iter = [None]
        if E:
            assignment_iter = get_assignment_for_typed_E(E, limit=theta)

        for assignment in assignment_iter:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            if L:
                bayesian_successes = 0
                bayesian_failures = 0
                bayesian_should_stop = False
                while not _timeout_expired(deadline) and not bayesian_should_stop:

                    _reset_typed_tmp_formula(smt_filepath, tmp_filepath, assignment)
                    for blocked_model in blocked_models:
                        prevent_same_model_typed(blocked_model, L, tmp_filepath)
                    mask_var_pairs = _generate_and_apply_typed_masks_parallel(
                        L,
                        tmp_filepath,
                        coverage,
                        delta,
                        use_recursive_string_helpers,
                        string_domain,
                        max_workers=threads,
                    )

                    if not mask_var_pairs:
                        status, model = run_z3_and_get_model(tmp_filepath)
                        partial_time = time.perf_counter() - start_time
                        start_time = time.perf_counter()
                        reached_num_samples = _save_and_flush(status, model, assignment, [], partial_time)
                        bayesian_successes, bayesian_failures = _update_bayesian_counts(
                            status,
                            bayesian_successes,
                            bayesian_failures,
                        )
                        if reached_num_samples:
                            return S
                        break

                    status, model = run_z3_and_get_model(tmp_filepath)
                    partial_time = time.perf_counter() - start_time
                    start_time = time.perf_counter()

                    if status == "SAT":
                        reached_num_samples = _save_and_flush(
                            status,
                            model,
                            assignment,
                            mask_var_pairs,
                            partial_time,
                        )
                        bayesian_successes, bayesian_failures = _update_bayesian_counts(
                            status,
                            bayesian_successes,
                            bayesian_failures,
                        )
                        if reached_num_samples:
                            return S
                        blocked_models.append(dict(model.items()))
                        prevent_same_model_typed(model, L, tmp_filepath)
                        bayesian_stop, bayesian_prob = _should_stop_due_to_bayesian_test(
                            bayesian_successes,
                            bayesian_failures,
                        )
                        if bayesian_stop:
                            total = bayesian_successes + bayesian_failures
                            print(
                                f"[INFO] Bayesian early stop after {total} iterations for current assignment: "
                                f"P(SAT rate <= {bayesian_min_viable_sat_rate:.0%})={bayesian_prob:.1%} "
                                f">= {bayesian_confidence:.0%}."
                            )
                            bayesian_should_stop = True
                        continue

                    if status in ("UNSAT", "UNKNOWN"):
                        _save_and_flush(status, model, assignment, mask_var_pairs, partial_time)
                        bayesian_successes, bayesian_failures = _update_bayesian_counts(
                            status,
                            bayesian_successes,
                            bayesian_failures,
                        )
                        bayesian_stop, bayesian_prob = _should_stop_due_to_bayesian_test(
                            bayesian_successes,
                            bayesian_failures,
                        )
                        if bayesian_stop:
                            total = bayesian_successes + bayesian_failures
                            print(
                                f"[INFO] Bayesian early stop after {total} iterations for current assignment: "
                                f"P(SAT rate <= {bayesian_min_viable_sat_rate:.0%})={bayesian_prob:.1%} "
                                f">= {bayesian_confidence:.0%}."
                            )
                            bayesian_should_stop = True
                        continue
            else:

                _reset_typed_tmp_formula(smt_filepath, tmp_filepath, assignment)
                for blocked_model in blocked_models:
                    prevent_same_model_typed(blocked_model, L, tmp_filepath)

                status, model = run_z3_and_get_model(tmp_filepath)
                partial_time = time.perf_counter() - start_time
                start_time = time.perf_counter()

                if status in ("UNSAT", "UNKNOWN"):
                    _save_and_flush(status, model, assignment, [], partial_time)
                elif status == "SAT":
                    if _save_and_flush(status, model, assignment, [], partial_time):
                        return S

        if not E and not L:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            status, model = run_z3_and_get_model(tmp_filepath)
            partial_time = time.perf_counter() - start_time
            _save_and_flush(status, model, None, [], partial_time)
    except KeyboardInterrupt:
        interrupted = True
        print("\nKeyboardInterrupt ricevuto: salvataggio dei risultati parziali in corso...")
    finally:
        pass

    if interrupted:
        raise KeyboardInterrupt

    return S


def z3_baseline_sampler(
    smt_filepath,
    free_vars=None,
    max_samples=None,
    string_max_length=54,
    output_filename=None,
    timeout=None,
):
    """
    Baseline Z3 sampler.

    Repeatedly invokes Z3 on the same formula and adds a blocking clause after each
    SAT result so the next iteration cannot return the same model again.
    """
    timeout_seconds = _validate_timeout(timeout)
    string_domain = _get_string_domain_config(string_max_length)
    if max_samples is not None:
        if not isinstance(max_samples, int):
            raise TypeError("max_samples must be an integer or None.")
        if max_samples <= 0:
            raise ValueError("max_samples must be positive when provided.")

    S = set()
    solver_result = {}
    if free_vars is None:
        free_vars = extract_free_vars_with_type_from_smt2_file(smt_filepath)
    print(f"Extracted free variables: {free_vars}")

    tmp_dir = Path(__file__).parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_filepath = tmp_dir / f"tmp_{uuid.uuid4().hex}.smt2"
    shutil.copy2(smt_filepath, tmp_filepath)
    baseline_csv_path = _baseline_result_csv_path(smt_filepath, results_suffix="_z3_baseline_call_value")
    baseline_csv_path.parent.mkdir(parents=True, exist_ok=True)

    sat_count = 0
    interrupted = False

    try:
        start_time = time.perf_counter()
        deadline = None if timeout_seconds is None else start_time + timeout_seconds
        while max_samples is None or sat_count < max_samples:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                break
            remaining_ms = None
            if deadline is not None:
                remaining_seconds = max(0.0, deadline - time.perf_counter())
                if remaining_seconds <= 0:
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    break
                remaining_ms = max(1, int(remaining_seconds * 1000))

            status, model = run_z3_and_get_model(tmp_filepath, timeout_ms=remaining_ms)
            partial_time = time.perf_counter() - start_time
            start_time = time.perf_counter()

            if status in ("UNSAT", "UNKNOWN"):
                save_model_typed(
                    solver_result,
                    model,
                    free_vars,
                    tmp_filepath,
                    status,
                    None,
                    [],
                    partial_time,
                )
                break

            if status == "SAT":
                save_sat_model_typed(
                    solver_result,
                    model,
                    free_vars,
                    tmp_filepath,
                    status,
                    [],
                    partial_time,
                )
                _append_solver_result_row_typed(solver_result, baseline_csv_path)
                prevent_same_model_for_free_vars_typed(model, free_vars, tmp_filepath)
                S.add(frozenset(model.items()))
                sat_count += 1
    except KeyboardInterrupt:
        interrupted = True
        print("\nKeyboardInterrupt ricevuto: salvataggio dei risultati parziali in corso...")
    finally:
        _write_baseline_solver_results_typed(
            solver_result,
            smt_filepath,
            results_suffix="_z3_baseline_call_value",
            output_filename=output_filename,
        )

    if interrupted:
        raise KeyboardInterrupt

    return S


def _reset_typed_tmp_formula(smt_filepath, tmp_filepath, assignment=None):
    shutil.copy2(smt_filepath, tmp_filepath)
    if assignment is not None:
        add_e_assignment_typed(assignment, tmp_filepath)


def _generate_and_apply_typed_masks(
    L,
    tmp_filepath,
    coverage,
    delta,
    use_recursive_string_helpers,
    string_domain,
):
    mask_var_pairs = []
    for (var_name, var_type, lb, ub) in tqdm(L, desc="Generating masks..."):
        if not _is_mask_supported(var_type):
            continue

        mask = _create_mask_for_type(var_type, lb, ub, coverage, delta, string_domain=string_domain)
        mask_var_pairs.append((var_name, mask))
        add_mask_typed(
            mask,
            var_name,
            var_type,
            tmp_filepath,
            use_recursive_string_helpers=use_recursive_string_helpers,
            string_domain=string_domain,
        )

    return mask_var_pairs


def _build_typed_mask_entry(args):
    var_name, var_type, lb, ub, coverage, delta, string_domain = args
    if not _is_mask_supported(var_type):
        return None

    mask = _create_mask_for_type(var_type, lb, ub, coverage, delta, string_domain=string_domain)
    return var_name, var_type, mask


def _generate_and_apply_typed_masks_parallel(
    L,
    tmp_filepath,
    coverage,
    delta,
    use_recursive_string_helpers,
    string_domain,
    max_workers=None,
):
    """
    Parallel two-phase variant:
    1. compute all masks in parallel
    2. apply them to the SMT file sequentially in input order
    """
    supported_entries = [
        (var_name, var_type, lb, ub)
        for (var_name, var_type, lb, ub) in L
        if _is_mask_supported(var_type)
    ]
    if not supported_entries:
        return []

    task_args = [
        (var_name, var_type, lb, ub, coverage, delta, string_domain)
        for (var_name, var_type, lb, ub) in supported_entries
    ]

    if max_workers in (None, 1):
        computed_masks = [
            _build_typed_mask_entry(task_arg)
            for task_arg in tqdm(task_args, total=len(task_args), desc="Generating masks...")
        ]
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            computed_masks = list(
                tqdm(
                    executor.map(_build_typed_mask_entry, task_args),
                    total=len(task_args),
                    desc="Generating masks...",
                )
            )

    mask_var_pairs = []
    for entry, computed in zip(supported_entries, computed_masks):
        if computed is None:
            continue
        var_name, var_type, _, _ = entry
        _, _, mask = computed
        mask_var_pairs.append((var_name, mask))
        add_mask_typed(
            mask,
            var_name,
            var_type,
            tmp_filepath,
            use_recursive_string_helpers=use_recursive_string_helpers,
            string_domain=string_domain,
        )

    return mask_var_pairs


def _hybrid_state_space_sampler_typed_impl(
    smt_filepath,
    free_vars=None,
    theta=256,
    coverage=50,
    delta=0.0050,
    use_recursive_string_helpers=True,
    string_max_length=54,
    complement_on_unsat=False,
    results_suffix="",
    removal_mode=REMOVAL_MODE_ONE,
    chunk_size=None,
    timeout=None,
    output_filename=None,
):
    _validate_removal_mode(removal_mode)
    timeout_seconds = _validate_timeout(timeout)
    string_domain = _get_string_domain_config(string_max_length)
    S = set()
    E = set()
    L = set()
    solver_result = {}
    n = 2**256
    if(free_vars is None):
        free_vars = extract_free_vars_with_type_from_smt2_file(smt_filepath)
    print(f"Extracted free variables: {free_vars}")
    tmp_dir = Path(__file__).parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_filepath = tmp_dir / f"tmp_{uuid.uuid4().hex}.smt2"
    shutil.copy2(smt_filepath, tmp_filepath)

    interrupted = False

    try:
        start_time = time.perf_counter()
        deadline = None if timeout_seconds is None else start_time + timeout_seconds
        for free_var_name, free_var_type in free_vars:
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            partition_variable_typed(
                E,
                L,
                smt_filepath,
                free_var_name,
                free_var_type,
                theta=theta,
                string_domain=string_domain,
            )

        if E:
            assignments = get_assignment_for_typed_E(E, limit=theta)

            for assignment in assignments:
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S
                add_e_assignment_typed(assignment, tmp_filepath)
                mask_var_pairs = []

                if L:
                    for (var_name, var_type, lb, ub) in tqdm(L, desc="Generating masks..."):
                        if _timeout_expired(deadline):
                            print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                            return S
                        if not _is_mask_supported(var_type):
                            continue

                        mask = _create_mask_for_type(var_type, lb, ub, coverage, delta, string_domain=string_domain)
                        mask_var_pairs.append((var_name, mask))
                        n = min(n, _mask_iteration_count(mask, var_type))
                        add_mask_typed(
                            mask,
                            var_name,
                            var_type,
                            tmp_filepath,
                            use_recursive_string_helpers=use_recursive_string_helpers,
                            string_domain=string_domain,
                        )

                    if mask_var_pairs:
                        start_time = _run_masked_solver_loop_typed(
                            S,
                            solver_result,
                            free_vars,
                            L,
                            tmp_filepath,
                            mask_var_pairs,
                            n,
                            start_time,
                            assignment=assignment,
                            use_recursive_string_helpers=use_recursive_string_helpers,
                            string_domain=string_domain,
                            complement_on_unsat=complement_on_unsat,
                            removal_mode=removal_mode,
                            chunk_size=chunk_size,
                            deadline=deadline,
                        )
                        if _timeout_expired(deadline):
                            print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                            return S
                    else:
                        status, model = run_z3_and_get_model(tmp_filepath)
                        if _timeout_expired(deadline):
                            print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                            return S
                        partial_time = time.perf_counter() - start_time
                        start_time = time.perf_counter()
                        if status in ("UNSAT", "UNKNOWN"):
                            save_model_typed(
                                solver_result,
                                model,
                                free_vars,
                                tmp_filepath,
                                status,
                                assignment,
                                mask_var_pairs,
                                partial_time,
                                L,
                            )
                        elif status == "SAT":
                            save_sat_model_typed(
                                solver_result,
                                model,
                                free_vars,
                                tmp_filepath,
                                status,
                                mask_var_pairs,
                                partial_time,
                                L,
                            )
                            S.add(frozenset(model.items()))
                else:
                    if _timeout_expired(deadline):
                        print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                        return S
                    status, model = run_z3_and_get_model(tmp_filepath)
                    partial_time = time.perf_counter() - start_time
                    start_time = time.perf_counter()
                    if status in ("UNSAT", "UNKNOWN"):
                        save_model_typed(
                            solver_result,
                            model,
                            free_vars,
                            tmp_filepath,
                            status,
                            assignment,
                            mask_var_pairs,
                            partial_time,
                            L,
                        )
                    elif status == "SAT":
                        save_sat_model_typed(
                            solver_result,
                            model,
                            free_vars,
                            tmp_filepath,
                            status,
                            mask_var_pairs,
                            partial_time,
                            L,
                        )
                        S.add(frozenset(model.items()))
        elif L:
            mask_var_pairs = []
            for (var_name, var_type, lb, ub) in tqdm(L, desc="Generating masks..."):
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S
                if not _is_mask_supported(var_type):
                    continue
                mask = _create_mask_for_type(var_type, lb, ub, coverage, delta, string_domain=string_domain)
                mask_var_pairs.append((var_name, mask))
                n = min(n, _mask_iteration_count(mask, var_type))
                add_mask_typed(
                    mask,
                    var_name,
                    var_type,
                    tmp_filepath,
                    use_recursive_string_helpers=use_recursive_string_helpers,
                    string_domain=string_domain,
                )

            if mask_var_pairs:
                start_time = _run_masked_solver_loop_typed(
                    S,
                    solver_result,
                    free_vars,
                    L,
                    tmp_filepath,
                    mask_var_pairs,
                    n,
                    start_time,
                    assignment=None,
                    use_recursive_string_helpers=use_recursive_string_helpers,
                    string_domain=string_domain,
                    complement_on_unsat=complement_on_unsat,
                    removal_mode=removal_mode,
                    chunk_size=chunk_size,
                    deadline=deadline,
                )
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S
            else:
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S
                status, model = run_z3_and_get_model(tmp_filepath)
                if _timeout_expired(deadline):
                    print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                    return S
                partial_time = time.perf_counter() - start_time
                if status in ("UNSAT", "UNKNOWN"):
                    save_model_typed(solver_result, model, free_vars, tmp_filepath, status, None, [], partial_time, L)
                elif status == "SAT":
                    save_sat_model_typed(solver_result, model, free_vars, tmp_filepath, status, [], partial_time, L)
                    S.add(frozenset(model.items()))
        else:
            # No partitionable variables: still collect one solver answer.
            if _timeout_expired(deadline):
                print(f"[INFO] Timeout reached after {timeout_seconds} seconds, saving CSV and stopping.")
                return S
            status, model = run_z3_and_get_model(tmp_filepath)
            partial_time = time.perf_counter() - start_time
            if status in ("UNSAT", "UNKNOWN"):
                save_model_typed(solver_result, model, free_vars, tmp_filepath, status, None, [], partial_time, L)
            elif status == "SAT":
                save_sat_model_typed(solver_result, model, free_vars, tmp_filepath, status, [], partial_time, L)
                S.add(frozenset(model.items()))
    except KeyboardInterrupt:
        interrupted = True
        print("\nKeyboardInterrupt ricevuto: salvataggio dei risultati parziali in corso...")
    finally:
        _write_solver_results_typed(
            solver_result,
            smt_filepath,
            coverage,
            string_domain["max_len"],
            results_suffix=results_suffix,
            output_filename=output_filename,
        )

    if interrupted:
        raise KeyboardInterrupt

    return S


def _run_masked_solver_loop_typed(
    S,
    solver_result,
    free_vars,
    L,
    tmp_filepath,
    mask_var_pairs,
    iteration_budget,
    start_time,
    assignment,
    use_recursive_string_helpers,
    string_domain,
    complement_on_unsat=False,
    removal_mode=REMOVAL_MODE_ONE,
    chunk_size=None,
    deadline=None,
):
    complement_applied = False
    remaining_iterations = max(1, iteration_budget)
    complemented_variants = []

    while remaining_iterations > 0:
        if _timeout_expired(deadline):
            return start_time
        status, model = run_z3_and_get_model(tmp_filepath)
        if _timeout_expired(deadline):
            return start_time
        partial_time = time.perf_counter() - start_time
        start_time = time.perf_counter()
        remaining_iterations -= 1

        if status in ("UNSAT", "UNKNOWN"):
            current_masks_snapshot = [(var_name, list(mask)) for var_name, mask in mask_var_pairs]
            save_model_typed(
                solver_result,
                model,
                free_vars,
                tmp_filepath,
                status,
                assignment,
                current_masks_snapshot,
                partial_time,
                L,
            )

            if complement_on_unsat and not complement_applied:
                complemented_masks = complement_mask_var_pairs_typed(
                    mask_var_pairs,
                    L,
                )
                if complemented_masks:
                    complemented_variants = build_complement_mask_variants_typed(
                        complemented_masks,
                        L,
                        removal_mode=removal_mode,
                        chunk_size=chunk_size,
                    )
                    print(
                        "[INFO] Uniform complemented variants after "
                        f"{removal_mode} removal: {len(complemented_variants)}"
                    )
                    if complemented_variants:
                        first_variant = complemented_variants.pop(random.randrange(len(complemented_variants)))
                        apply_mask_var_pairs_typed(
                            first_variant,
                            L,
                            tmp_filepath,
                            use_recursive_string_helpers=use_recursive_string_helpers,
                            string_domain=string_domain,
                        )
                        mask_var_pairs[:] = first_variant
                        complement_applied = True
                        remaining_iterations = max(
                            remaining_iterations,
                            _mask_iteration_budget_from_pairs(first_variant, L) + len(complemented_variants),
                        )
                        continue

            if complement_applied and complemented_variants:
                next_variant = complemented_variants.pop(random.randrange(len(complemented_variants)))
                apply_mask_var_pairs_typed(
                    next_variant,
                    L,
                    tmp_filepath,
                    use_recursive_string_helpers=use_recursive_string_helpers,
                    string_domain=string_domain,
                )
                mask_var_pairs[:] = next_variant
                remaining_iterations = max(
                    remaining_iterations,
                    _mask_iteration_budget_from_pairs(next_variant, L) + len(complemented_variants),
                )
                continue

            return start_time

        if status == "SAT":
            save_sat_model_typed(
                solver_result,
                model,
                free_vars,
                tmp_filepath,
                status,
                mask_var_pairs,
                partial_time,
                L,
            )
            remove_used_mask_typed(model, L, tmp_filepath, mask_var_pairs, string_domain=string_domain)
            prevent_same_model_typed(model, L, tmp_filepath)
            S.add(frozenset(model.items()))

    return start_time


def _write_solver_results_typed(
    solver_result,
    smt_filepath,
    coverage,
    string_max_length,
    results_suffix="",
    output_filename=None,
    include_string_length_suffix=True,
):
    df = pd.DataFrame(solver_result)
    benchmark_dir = Path(smt_filepath).parent
    results_dir_name = benchmark_dir.name + "_results" + results_suffix + "_cov" + str(coverage)
    if include_string_length_suffix:
        results_dir_name += "_strlen" + str(string_max_length)
    results_dir = benchmark_dir.parent / results_dir_name
    results_dir.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        csv_filename = Path(smt_filepath).stem + ".csv"
    else:
        csv_filename = Path(output_filename)
        if csv_filename.suffix.lower() != ".csv":
            csv_filename = csv_filename.with_suffix(".csv")
    df.to_csv(results_dir / csv_filename, index=False)


def _write_baseline_solver_results_typed(
    solver_result,
    smt_filepath,
    results_suffix="",
    output_filename=None,
):
    df = pd.DataFrame(solver_result)
    benchmark_dir = Path(smt_filepath).parent
    results_dir = benchmark_dir.parent / (benchmark_dir.name + "_results" + results_suffix)
    results_dir.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        csv_filename = Path(smt_filepath).stem + ".csv"
    else:
        csv_filename = Path(output_filename)
        if csv_filename.suffix.lower() != ".csv":
            csv_filename = csv_filename.with_suffix(".csv")
    df.to_csv(results_dir / csv_filename, index=False)


def _baseline_result_csv_path(smt_filepath, results_suffix=""):
    benchmark_dir = Path(smt_filepath).parent
    results_dir = benchmark_dir.parent / (benchmark_dir.name + "_results" + results_suffix)
    return results_dir / Path(smt_filepath).with_suffix(".csv").name


def _append_solver_result_row_typed(
    solver_result,
    csv_filepath,
):
    row = {key: values[-1] for key, values in solver_result.items() if values}
    if not row:
        return

    df = pd.DataFrame([row])
    header = not csv_filepath.exists()
    df.to_csv(csv_filepath, mode="a", header=header, index=False)


def _count_existing_sat_rows(csv_filepath):
    if not csv_filepath.exists() or csv_filepath.stat().st_size == 0:
        return 0

    try:
        with csv_filepath.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if "status" not in (reader.fieldnames or []):
                return 0
            return sum(
                1
                for row in reader
                if str(row.get("status", "")).strip().upper() == "SAT"
            )
    except (OSError, csv.Error):
        return 0


def extract_free_vars_with_type_from_smt2_file(file_path):
    free_vars = []
    with open(file_path, "r") as f:
        for line in f:
            # Match zero-arg declare-fun: (declare-fun <name> () <type>)
            m_fun = re.match(r"\(declare-fun\s+(\w+)\s+\(\s*\)\s+(.+)\)", line)
            # Match declare-const: (declare-const <name> <type>)
            m_const = re.match(r"\(declare-const\s+(\w+)\s+(.+)\)", line)
            if m_fun:
                name = m_fun.group(1)
                var_type = m_fun.group(2).strip()
                free_vars.append((name, var_type))
            elif m_const:
                name = m_const.group(1)
                var_type = m_const.group(2).strip()
                free_vars.append((name, var_type))
    return free_vars

def _mask_bounds_from_pairs(mask_var_pairs, L):
    if not mask_var_pairs:
        return []

    bounds_by_var = {var_name: (var_type, lb, ub) for var_name, var_type, lb, ub in L}
    mask_bounds = []
    for var_name, _ in mask_var_pairs:
        bounds = bounds_by_var.get(var_name)
        if bounds is None:
            continue
        var_type, lb, ub = bounds
        mask_bounds.append((var_name, var_type, lb, ub))
    return mask_bounds


def save_model_typed(solver_result, model, free_vars, tmp_filepath, status, assignment, mask_var_pairs, partial_time, L=None):
    if status not in ("UNSAT", "UNKNOWN"):
        return

    if isinstance(assignment, frozenset):
        assignment_dict = dict(assignment)
    elif isinstance(assignment, dict):
        assignment_dict = assignment
    else:
        assignment_dict = {}

    free_var_names = set(_free_var_names(free_vars))

    for meta_key in ("status", "mask_var_pairs", "mask_bounds", "time"):
        if meta_key not in solver_result:
            solver_result[meta_key] = [None] * (len(next(iter(solver_result.values()))) if solver_result else 0)

    for var_name in free_var_names:
        if var_name not in solver_result:
            solver_result[var_name] = [None] * (len(next(iter(solver_result.values()))) if solver_result else 0)

    for var_name in free_var_names:
        assignment_entry = assignment_dict.get(var_name, None)
        if isinstance(assignment_entry, dict) and "value" in assignment_entry:
            assignment_entry = assignment_entry["value"]
        solver_result[var_name].append(assignment_entry)

    solver_result["status"].append(status)
    solver_result["mask_var_pairs"].append(list(mask_var_pairs) if mask_var_pairs is not None else None)
    solver_result["mask_bounds"].append(_mask_bounds_from_pairs(mask_var_pairs, L or []))
    solver_result["time"].append(partial_time)


def save_sat_model_typed(result, model_dict, free_vars, tmp_filepath, status, mask_var_pairs, partial_time, L=None):
    free_var_names = set(_free_var_names(free_vars))

    for meta_key in ("status", "mask_var_pairs", "mask_bounds", "time"):
        if meta_key not in result:
            result[meta_key] = [None] * (len(next(iter(result.values()))) if result else 0)

    if model_dict is None:
        for key in result:
            result[key].append(None)
        result["status"][-1] = status
        result["mask_var_pairs"][-1] = list(mask_var_pairs) if mask_var_pairs is not None else None
        result["mask_bounds"][-1] = _mask_bounds_from_pairs(mask_var_pairs, L or [])
        result["time"][-1] = partial_time
        return result

    filtered = {k: v for k, v in model_dict.items() if k in free_var_names}

    for var_name in free_var_names:
        if var_name not in result:
            result[var_name] = [None] * (len(next(iter(result.values()))) if result else 0)

    for var_name in result:
        if var_name in ("status", "mask_var_pairs", "mask_bounds", "time"):
            continue
        result[var_name].append(filtered.get(var_name, None))

    result["status"].append(status)
    result["mask_var_pairs"].append(list(mask_var_pairs) if mask_var_pairs is not None else None)
    result["mask_bounds"].append(_mask_bounds_from_pairs(mask_var_pairs, L or []))
    result["time"].append(partial_time)
    return result


def get_assignment_for_typed_E(E, limit=None):
    ranges = []
    for var_name, var_type, values in E:
        ranges.append((var_name, var_type, values))

    total = 1
    for _, _, values in ranges:
        total *= len(values)
    print(f"Total assignments for E: {total}")

    if limit is not None and limit < total:
        sampled_indices = _sample_distinct_indices(total, limit)
        value_lists = [values for _, _, values in ranges]
        for index in sampled_indices:
            combo = []
            remaining = index
            for values in reversed(value_lists):
                remaining, value_index = divmod(remaining, len(values))
                combo.append(values[value_index])
            combo.reverse()
            yield {
                var_name: {"type": var_type, "value": value}
                for (var_name, var_type, _), value in zip(ranges, combo)
            }
        return

    for combo in itertools.product(*(values for _, _, values in ranges)):
        yield {
            var_name: {"type": var_type, "value": value}
            for (var_name, var_type, _), value in zip(ranges, combo)
        }


def _sample_distinct_indices(total, limit):
    if limit <= 0:
        return []
    if limit >= total:
        return range(total)

    sampled = set()
    while len(sampled) < limit:
        sampled.add(random.randrange(total))
    return sampled


def add_e_assignment_typed(assignment, file_path):
    with open(file_path, "r") as f:
        content = f.read()

    content = re.sub(
        r'\(assert\s+\(and\s+(?:\(=\s+[\w@]+\s+.+?\)\s*)+\)\)\n?',
        '',
        content,
        flags=re.DOTALL,
    )

    clauses = [
        f"(= {var_name} {_format_literal(data['value'], data['type'])})"
        for var_name, data in assignment.items()
    ]
    assignment_assert = "(assert (and\n    " + "\n    ".join(clauses) + "\n))\n"

    lines = content.splitlines(keepends=True)
    first_checksat_index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("(check-sat")),
        None,
    )

    if first_checksat_index is None:
        content += assignment_assert
    else:
        lines.insert(first_checksat_index, assignment_assert)
        content = "".join(lines)

    with open(file_path, "w") as f:
        f.write(content)


def partition_variable_typed(E, L, file_path, free_var_name, free_var_type, theta=1000, string_domain=None, U=2**256 - 1):
    string_domain = string_domain or _get_string_domain_config(54)
    if free_var_type == "Bool":
        E.add((free_var_name, free_var_type, (False, True)))
        return

    if free_var_type == "String":
        string_ub = min(U, string_domain["domain_max"] - 1)
        L.add((free_var_name, free_var_type, 0, string_ub))
        return

    if free_var_type == "Int":
        lb, ub = _extract_int_bounds(file_path, free_var_name, U)
        if ub >= lb and (ub - lb) < theta:
            E.add((free_var_name, free_var_type, range(lb, ub + 1)))
        else:
            L.add((free_var_name, free_var_type, lb, ub))
        return

    bitvec_match = BITVEC_RE.match(free_var_type)
    if bitvec_match:
        bitwidth = int(bitvec_match.group(1))
        lb, ub = _extract_bitvec_bounds(file_path, free_var_name, bitwidth)
        if ub >= lb and (ub - lb) < theta:
            E.add((free_var_name, free_var_type, range(lb, ub + 1)))
        else:
            L.add((free_var_name, free_var_type, lb, ub))
        return
    
def _extract_int_bounds(file_path, free_var_name, U=2**256 - 1):
    with open(file_path, "r") as f:
        content = f.read()

    pattern = fr'\(\s*(>=|>|<=|<)\s+{re.escape(free_var_name)}\s+(-?[0-9]+)\s*\)'
    matches = re.findall(pattern, content)

    lb = 0
    ub = U
    for op, value in matches:
        value = int(value)
        if op in (">=", ">"):
            lb = max(lb, value + (1 if op == ">" else 0))
        elif op in ("<=", "<"):
            ub = min(ub, value - (1 if op == "<" else 0))
    return lb, ub


def _extract_bitvec_bounds(file_path, free_var_name, bitwidth):
    with open(file_path, "r") as f:
        content = f.read()

    lb = 0
    ub = 2**bitwidth - 1

    patterns = [
        (rf'\((?:bvule|bvsle)\s+\(_\s+bv(\d+)\s+{bitwidth}\)\s+{re.escape(free_var_name)}\)', "lb", 0),
        (rf'\((?:bvult|bvslt)\s+\(_\s+bv(\d+)\s+{bitwidth}\)\s+{re.escape(free_var_name)}\)', "lb", 1),
        (rf'\((?:bvule|bvsle)\s+{re.escape(free_var_name)}\s+\(_\s+bv(\d+)\s+{bitwidth}\)\)', "ub", 0),
        (rf'\((?:bvult|bvslt)\s+{re.escape(free_var_name)}\s+\(_\s+bv(\d+)\s+{bitwidth}\)\)', "ub", -1),
    ]

    for pattern, bound_kind, delta in patterns:
        for raw_value in re.findall(pattern, content):
            value = int(raw_value) + delta
            if bound_kind == "lb":
                lb = max(lb, value)
            else:
                ub = min(ub, value)

    return lb, ub


def _format_literal(value, var_type):
    if var_type == "Bool":
        return "true" if value else "false"

    bitvec_match = BITVEC_RE.match(var_type)
    if bitvec_match:
        return f"(_ bv{int(value)} {int(bitvec_match.group(1))})"

    if var_type == "String":
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return str(value)


def _is_mask_supported(var_type):
    return var_type in {"Int", "String"} or BITVEC_RE.match(var_type) is not None


def _create_mask_for_type(var_type, lb, ub, coverage, delta, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    if var_type == "String":
        return _create_string_interval_mask(lb, ub, coverage, delta, string_domain=string_domain)
    return create_mask_with_coverage(lb, ub, target_coverage=coverage, delta=delta)


def _mask_iteration_count(mask, var_type):
    # if var_type == "String":
    #     return sum(len(group) for group in mask)
    return len(mask)


def _mask_iteration_budget_from_pairs(mask_var_pairs, L):
    type_by_var = {var_name: var_type for var_name, var_type, _, _ in L}
    counts = []
    for var_name, mask in mask_var_pairs:
        var_type = type_by_var.get(var_name)
        if var_type is None or not mask:
            continue
        counts.append(_mask_iteration_count(mask, var_type))
    return min(counts) if counts else 0


def add_mask_typed(mask, free_var_name, free_var_type, file_path, use_recursive_string_helpers=True, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    if free_var_type == "String":
        _add_string_mask(
            mask,
            free_var_name,
            file_path,
            use_recursive_string_helpers=use_recursive_string_helpers,
            string_domain=string_domain,
        )
        return

    shuffled_mask = list(mask)
    random.shuffle(shuffled_mask)

    for band_lb, band_ub in shuffled_mask:
        _add_numeric_band(band_lb, band_ub, free_var_name, file_path)


def _add_numeric_band(lb_band, ub_band, free_var_name, file_path):
    new_clause = f"(and (>= {free_var_name} {lb_band}) (< {free_var_name} {ub_band}))"

    with open(file_path, "r") as f:
        content = f.read()

    pattern = re.compile(
        rf'\(assert\s+\(or\s+((?:\(and\s+\(>=\s+{re.escape(free_var_name)}\s+-?\d+\)\s+\(<\s+{re.escape(free_var_name)}\s+-?\d+\)\)\s*)+)\)\)',
        re.DOTALL,
    )

    match = pattern.search(content)
    if match:
        existing_clauses = match.group(1).strip()
        new_assert = f"(assert (or\n  {existing_clauses}\n  {new_clause}\n))"
        content = content[:match.start()] + new_assert + content[match.end():]
    else:
        new_assert = f"(assert (or\n  {new_clause}\n))"
        content = _insert_before_first_checksat(content, new_assert + "\n")

    with open(file_path, "w") as f:
        f.write(content)


def replace_mask_typed(mask, free_var_name, free_var_type, file_path, use_recursive_string_helpers=True, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    with open(file_path, "r") as f:
        content = f.read()

    content = _clear_mask_for_var_typed(content, free_var_name, free_var_type)

    with open(file_path, "w") as f:
        f.write(content)

    if mask:
        add_mask_typed(
            mask,
            free_var_name,
            free_var_type,
            file_path,
            use_recursive_string_helpers=use_recursive_string_helpers,
            string_domain=string_domain,
        )


def apply_mask_var_pairs_typed(mask_var_pairs, L, file_path, use_recursive_string_helpers=True, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    type_by_var = {var_name: var_type for var_name, var_type, _, _ in L}

    for var_name, var_type, _, _ in L:
        if _is_mask_supported(var_type):
            replace_mask_typed(
                [],
                var_name,
                var_type,
                file_path,
                use_recursive_string_helpers=use_recursive_string_helpers,
                string_domain=string_domain,
            )

    for var_name, mask in mask_var_pairs:
        var_type = type_by_var.get(var_name)
        if var_type is None:
            continue
        replace_mask_typed(
            mask,
            var_name,
            var_type,
            file_path,
            use_recursive_string_helpers=use_recursive_string_helpers,
            string_domain=string_domain,
        )


def _clear_mask_for_var_typed(content, free_var_name, free_var_type):
    if free_var_type == "String":
        string_block_pattern = re.compile(
            rf'{re.escape(STRING_MASK_START_TEMPLATE.format(var_name=free_var_name))}\n'
            rf'.*?'
            rf'{re.escape(STRING_MASK_END_TEMPLATE.format(var_name=free_var_name))}\n?',
            re.DOTALL,
        )
        return string_block_pattern.sub("", content)

    numeric_block_pattern = re.compile(
        rf'\(assert\s+\(or\s+(?:\s*\(and\s+\(>=\s+{re.escape(free_var_name)}\s+-?\d+\)\s+\(<\s+{re.escape(free_var_name)}\s+-?\d+\)\))+\s*\)\)\n?',
        re.DOTALL,
    )
    return numeric_block_pattern.sub("", content)


def _add_string_mask(mask, free_var_name, file_path, use_recursive_string_helpers=True, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    with open(file_path, "r") as f:
        content = f.read()

    content = _ensure_string_to_index_helpers(
        content,
        use_recursive_string_helpers=use_recursive_string_helpers,
        string_domain=string_domain,
    )

    interval_clauses = [
        f"(and (>= (stringToIndex {free_var_name}) {band_lb}) (< (stringToIndex {free_var_name}) {band_ub}))"
        for band_lb, band_ub in mask
    ]
    random.shuffle(interval_clauses)

    if not interval_clauses:
        return

    start_marker = STRING_MASK_START_TEMPLATE.format(var_name=free_var_name)
    end_marker = STRING_MASK_END_TEMPLATE.format(var_name=free_var_name)
    new_assert = (
        f"{start_marker}\n"
        "(assert (and\n"
        f"  (>= (str.len {free_var_name}) 1)\n"
        f"  (<= (str.len {free_var_name}) {string_domain['max_len']})\n"
        f"  (isLowerAlphaString {free_var_name})\n"
        "  (or\n"
        "    " + "\n    ".join(interval_clauses) + "\n"
        "  )\n"
        "))\n"
        f"{end_marker}\n"
    )
    content = _insert_before_first_checksat(content, new_assert)

    with open(file_path, "w") as f:
        f.write(content)


def _add_legacy_string_mask(mask, free_var_name, file_path):
    with open(file_path, "r") as f:
        content = f.read()

    interval_blocks = []
    for group in mask:
        if not group:
            continue
        new_clauses = "\n    ".join(
            f'(= {free_var_name} "{_escape_smt_string(value)}")'
            for value in group
        )
        interval_blocks.append(f"(or\n    {new_clauses}\n  )")

    if not interval_blocks:
        return

    new_assert = "(assert (or\n  " + "\n  ".join(interval_blocks) + "\n))\n"
    content = _insert_before_first_checksat(content, new_assert)

    with open(file_path, "w") as f:
        f.write(content)


def remove_used_mask_typed(model, L, file_path, mask_var_pairs, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    with open(file_path, "r") as f:
        content = f.read()

    l_info = {var_name: var_type for var_name, var_type, _, _ in L}
    mask_index = {var_name: i for i, (var_name, _) in enumerate(mask_var_pairs)}

    for var_name, z3_value in model.items():
        if var_name not in l_info:
            continue

        var_type = l_info[var_name]
        if var_type == "String":
            value = str(z3_value)
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if var_name in mask_index:
                idx = mask_index[var_name]
                current_var, current_mask = mask_var_pairs[idx]
                if current_mask and isinstance(current_mask[0], tuple):
                    string_index = _string_to_index(value, string_domain=string_domain)
                    content = _remove_string_band_from_mask(content, var_name, string_index, string_domain=string_domain)
                    mask_var_pairs[idx] = (
                        current_var,
                        [band for band in current_mask if not (band[0] <= string_index < band[1])],
                    )
                else:
                    content = _remove_legacy_string_interval_block_from_mask(content, var_name, value)
                    updated_groups = []
                    removed = False
                    for group in current_mask:
                        if not removed and value in group:
                            removed = True
                            continue
                        updated_groups.append(group)
                    mask_var_pairs[idx] = (
                        current_var,
                        updated_groups,
                    )
        elif _is_mask_supported(var_type):
            value = int(str(z3_value))
            content = _remove_numeric_value_from_mask(content, var_name, value, mask_index, mask_var_pairs)

    content = _cleanup_empty_mask_asserts(content)

    with open(file_path, "w") as f:
        f.write(content)


def _remove_numeric_value_from_mask(content, var_name, value, mask_index, mask_var_pairs):
    band_pattern = re.compile(
        rf'\s*\(and\s+\(>=\s+{re.escape(var_name)}\s+(-?\d+)\)\s+\(<\s+{re.escape(var_name)}\s+(-?\d+)\)\)'
    )
    or_block_pattern = re.compile(
        rf'\(assert\s+\(or\s+(?:\s*\(and\s+\(>=\s+{re.escape(var_name)}\s+-?\d+\)\s+\(<\s+{re.escape(var_name)}\s+-?\d+\)\))+\s*\)\)',
        re.MULTILINE,
    )

    def remove_band_from_block(or_block_match):
        block = or_block_match.group(0)

        def replace(match):
            lb = int(match.group(1))
            ub = int(match.group(2))
            if lb <= value < ub:
                if var_name in mask_index:
                    idx = mask_index[var_name]
                    current_var, current_mask = mask_var_pairs[idx]
                    mask_var_pairs[idx] = (current_var, [band for band in current_mask if band != (lb, ub)])
                return ""
            return match.group(0)

        return band_pattern.sub(replace, block)

    return or_block_pattern.sub(remove_band_from_block, content)


def _remove_string_band_from_mask(content, var_name, value, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    block_pattern = re.compile(
        rf'{re.escape(STRING_MASK_START_TEMPLATE.format(var_name=var_name))}\n'
        rf'(?P<body>.*?)'
        rf'{re.escape(STRING_MASK_END_TEMPLATE.format(var_name=var_name))}\n?',
        re.DOTALL,
    )
    band_pattern = re.compile(
        rf'\s*\(and\s+\(>=\s+\(stringToIndex\s+{re.escape(var_name)}\)\s+(\d+)\)\s+\(<\s+\(stringToIndex\s+{re.escape(var_name)}\)\s+(\d+)\)\)\n?'
    )

    def replace_block(match):
        block = match.group("body")
        remaining_bands = []
        for band_match in band_pattern.finditer(block):
            lb = int(band_match.group(1))
            ub = int(band_match.group(2))
            if lb <= value < ub:
                continue
            remaining_bands.append((lb, ub))

        if not remaining_bands:
            return ""

        rebuilt_assert = (
            "(assert (and\n"
            f"  (>= (str.len {var_name}) 1)\n"
            f"  (<= (str.len {var_name}) {string_domain['max_len']})\n"
            f"  (isLowerAlphaString {var_name})\n"
            "  (or\n"
            "    " + "\n    ".join(
                f"(and (>= (stringToIndex {var_name}) {lb}) (< (stringToIndex {var_name}) {ub}))"
                for lb, ub in remaining_bands
            ) + "\n"
            "  )\n"
            "))\n"
        )
        return (
            STRING_MASK_START_TEMPLATE.format(var_name=var_name) + "\n" +
            rebuilt_assert +
            STRING_MASK_END_TEMPLATE.format(var_name=var_name) + "\n"
        )

    return block_pattern.sub(replace_block, content)


def _remove_legacy_string_interval_block_from_mask(content, var_name, value):
    interval_pattern = re.compile(
        rf'\(or\s+(?P<body>(?:\s*\(=\s+{re.escape(var_name)}\s+\"(?:[^\"\\\\]|\\\\.)*\"\))+)\s*\)',
        re.DOTALL,
    )
    value_pattern = re.compile(
        rf'\(=\s+{re.escape(var_name)}\s+\"((?:[^\"\\\\]|\\\\.)*)\"\)'
    )

    def replace_interval(match):
        body = match.group("body")
        values = [
            _unescape_smt_string(value_match.group(1))
            for value_match in value_pattern.finditer(body)
        ]
        if value in values:
            return ""
        return match.group(0)

    updated = interval_pattern.sub(replace_interval, content)
    updated = re.sub(r'\(assert\s+\(or\s*\)\)\s*', '', updated, flags=re.DOTALL)
    return updated


def complement_mask_typed(mask, domain_lb, domain_ub):
    if domain_lb >= domain_ub:
        return []

    normalized = _normalize_mask_intervals(mask, domain_lb, domain_ub)
    complement = []
    cursor = domain_lb

    for start, end in normalized:
        if cursor < start:
            complement.append((cursor, start))
        cursor = max(cursor, end)

    if cursor < domain_ub:
        complement.append((cursor, domain_ub))

    return complement


def complement_mask_var_pairs_typed(
    mask_var_pairs,
    L,
):
    bounds_by_var = {var_name: (var_type, lb, ub) for var_name, var_type, lb, ub in L}
    complemented_pairs = []

    for var_name, mask in mask_var_pairs:
        bounds = bounds_by_var.get(var_name)
        if bounds is None:
            continue

        var_type, lb, ub = bounds
        complemented_mask = complement_mask_typed(mask, lb, ub)
        if complemented_mask:
            complemented_pairs.append((var_name, complemented_mask))

    return complemented_pairs


def build_complement_mask_variants_typed(mask_var_pairs, L, removal_mode=REMOVAL_MODE_ONE, chunk_size=None):
    if not mask_var_pairs:
        return []

    _validate_removal_mode(removal_mode)
    bounds_by_var = {var_name: (var_type, lb, ub) for var_name, var_type, lb, ub in L}
    per_variable_variants = []
    for var_name, mask in mask_var_pairs:
        bounds = bounds_by_var.get(var_name)
        if bounds is None:
            return []

        var_type, lb, ub = bounds
        removal_variants = [
            (var_name, reduced_mask)
            for reduced_mask in _build_mask_removal_variants(
                mask,
                var_type,
                lb,
                ub,
                removal_mode,
                chunk_size,
            )
        ]
        if not removal_variants:
            return []
        per_variable_variants.append(removal_variants)

    variants = []
    for variant_combo in itertools.product(*per_variable_variants):
        variants.append([(var_name, list(mask)) for var_name, mask in variant_combo])

    return variants


def build_add_mask_variants_typed(
    original_mask_var_pairs,
    complemented_mask_var_pairs,
    L,
    addition_mode=REMOVAL_MODE_ONE,
    chunk_size=None,
):
    if not original_mask_var_pairs or not complemented_mask_var_pairs:
        return []

    _validate_removal_mode(addition_mode)
    bounds_by_var = {var_name: (var_type, lb, ub) for var_name, var_type, lb, ub in L}
    complemented_by_var = {var_name: mask for var_name, mask in complemented_mask_var_pairs}
    per_variable_variants = []

    for var_name, original_mask in original_mask_var_pairs:
        bounds = bounds_by_var.get(var_name)
        if bounds is None:
            return []
        complemented_mask = complemented_by_var.get(var_name)
        if complemented_mask is None:
            return []

        var_type, lb, ub = bounds
        addition_variants = [
            (var_name, expanded_mask)
            for expanded_mask in _build_mask_addition_variants(
                complemented_mask,
                original_mask,
                var_type,
                lb,
                ub,
                addition_mode,
                chunk_size,
            )
        ]
        if not addition_variants:
            return []
        per_variable_variants.append(addition_variants)

    variants = []
    for variant_combo in itertools.product(*per_variable_variants):
        variants.append([(var_name, list(mask)) for var_name, mask in variant_combo])

    return variants


def _validate_removal_mode(removal_mode):
    if removal_mode not in {REMOVAL_MODE_ONE, REMOVAL_MODE_CHUNK}:
        raise ValueError(
            f"Unsupported removal_mode={removal_mode!r}. "
            f"Expected {REMOVAL_MODE_ONE!r} or {REMOVAL_MODE_CHUNK!r}."
        )


def _build_mask_removal_variants(mask, var_type, domain_lb, domain_ub, removal_mode, chunk_size=None):
    if len(mask) <= 1:
        return []

    if removal_mode == REMOVAL_MODE_ONE:
        candidates = [
            [interval for index, interval in enumerate(mask) if index != removed_index]
            for removed_index in range(len(mask))
        ]
        return [
            candidate
            for candidate in candidates
            if _mask_is_uniform_typed(candidate, var_type, domain_lb, domain_ub)
        ]

    remove_count = _resolve_chunk_remove_count(len(mask), chunk_size)
    variants = []
    seen = set()
    for _ in range(len(mask)):
        removal_indices = _uniform_chunk_removal_indices(len(mask), remove_count)
        reduced_mask = [
            interval for index, interval in enumerate(mask)
            if index not in removal_indices
        ]
        reduced_key = tuple(reduced_mask)
        if (
            reduced_mask
            and reduced_key not in seen
            and _mask_is_uniform_typed(reduced_mask, var_type, domain_lb, domain_ub)
        ):
            seen.add(reduced_key)
            variants.append(reduced_mask)
    return variants


def _build_mask_addition_variants(base_mask, addition_candidates, var_type, domain_lb, domain_ub, addition_mode, chunk_size=None):
    if not base_mask or not addition_candidates:
        return []

    if addition_mode == REMOVAL_MODE_ONE:
        candidates = [
            _normalize_mask_intervals(base_mask + [interval], domain_lb, domain_ub)
            for interval in addition_candidates
        ]
        return [
            candidate
            for candidate in candidates
            if candidate and _mask_is_uniform_typed(candidate, var_type, domain_lb, domain_ub)
        ]

    add_count = _resolve_chunk_remove_count(len(addition_candidates), chunk_size)
    variants = []
    seen = set()
    for _ in range(len(addition_candidates)):
        addition_indices = _uniform_chunk_addition_indices(len(addition_candidates), add_count)
        expanded_mask = _normalize_mask_intervals(
            base_mask + [interval for index, interval in enumerate(addition_candidates) if index in addition_indices],
            domain_lb,
            domain_ub,
        )
        expanded_key = tuple(expanded_mask)
        if (
            expanded_mask
            and expanded_key not in seen
            and _mask_is_uniform_typed(expanded_mask, var_type, domain_lb, domain_ub)
        ):
            seen.add(expanded_key)
            variants.append(expanded_mask)
    return variants


def _resolve_chunk_remove_count(mask_len, chunk_size):
    if mask_len <= 1:
        return 0
    if chunk_size is None:
        return min(mask_len - 1, max(2, mask_len // 2))
    if not isinstance(chunk_size, int):
        raise TypeError("chunk_size must be an integer or None.")
    if chunk_size < 2:
        raise ValueError("chunk_size must be at least 2 when removal_mode='CHUNK'.")
    return min(mask_len - 1, chunk_size)


def _uniform_chunk_removal_indices(mask_len, remove_count):
    if remove_count <= 0:
        return set()
    if remove_count >= mask_len:
        return set(range(mask_len))
    # Pick a random subset of distinct positions uniformly from the mask.
    return set(random.sample(range(mask_len), remove_count))


def _uniform_chunk_addition_indices(mask_len, add_count):
    if add_count <= 0:
        return set()
    if add_count >= mask_len:
        return set(range(mask_len))
    # Pick a random subset of distinct positions uniformly from the complement.
    return set(random.sample(range(mask_len), add_count))


def _mask_is_uniform_typed(mask, var_type, domain_lb, domain_ub):
    normalized_mask = _normalize_mask_intervals(mask, domain_lb, domain_ub)
    if not normalized_mask:
        return False

    domain_span = domain_ub - domain_lb
    if domain_span <= 0:
        return False

    real_multiset = multiset_from_intervals_2(normalized_mask, domain_lb, domain_ub)
    if not real_multiset:
        return False

    ideal_multiset = uniform_multiset(domain_lb, domain_ub, sum(real_multiset.values()))
    real_samples = expand_multiset(real_multiset)
    ideal_samples = expand_multiset(ideal_multiset)

    use_mwu = all((end - start) <= 1 for start, end in normalized_mask)
    if use_mwu:
        _, p_value = mannwhitneyu(real_samples, ideal_samples, alternative="two-sided")
        return p_value > 0.05

    ks_stat, p_value = ks_2samp(real_samples, ideal_samples)
    return ks_stat < 0.10 and p_value > 0.05


def _normalize_mask_intervals(mask, domain_lb, domain_ub):
    if not mask:
        return []

    clipped = []
    for start, end in sorted(mask, key=lambda interval: interval[0]):
        start = max(domain_lb, start)
        end = min(domain_ub, end)
        if start < end:
            clipped.append((start, end))

    if not clipped:
        return []

    merged = [clipped[0]]
    for start, end in clipped[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def prevent_same_model_typed(model, L, file_path):
    with open(file_path, "r") as f:
        content = f.read()

    l_info = {var_name: var_type for var_name, var_type, _, _ in L}
    equalities = []
    for var_name, z3_value in model.items():
        if var_name not in l_info:
            continue
        var_type = l_info[var_name]
        literal = _format_literal(_normalize_model_value(z3_value, var_type), var_type)
        equalities.append(f"(= {var_name} {literal})")

    if not equalities:
        return

    blocking_clause = f"(assert (not (and {' '.join(equalities)})))\n"
    content = _insert_before_first_checksat(content, blocking_clause)

    with open(file_path, "w") as f:
        f.write(content)


def prevent_same_model_for_free_vars_typed(model, free_vars, file_path):
    with open(file_path, "r") as f:
        content = f.read()

    type_by_var = {var_name: var_type for var_name, var_type in free_vars}
    equalities = []
    for var_name, z3_value in model.items():
        var_type = type_by_var.get(var_name)
        if var_type is None:
            continue
        literal = _format_literal(_normalize_model_value(z3_value, var_type), var_type)
        equalities.append(f"(= {var_name} {literal})")

    if not equalities:
        return

    blocking_clause = f"(assert (not (and {' '.join(equalities)})))\n"
    content = _insert_before_first_checksat(content, blocking_clause)

    with open(file_path, "w") as f:
        f.write(content)


def _normalize_model_value(z3_value, var_type):
    if var_type == "String":
        value = str(z3_value)
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        return value
    if var_type == "Bool":
        return str(z3_value).lower() == "true"
    return int(str(z3_value))


def _insert_before_first_checksat(content, insertion):
    lines = content.splitlines(keepends=True)
    first_checksat_index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("(check-sat")),
        None,
    )

    if first_checksat_index is None:
        return content + insertion

    lines.insert(first_checksat_index, insertion)
    return "".join(lines)


def _cleanup_empty_mask_asserts(content):
    return re.sub(r"\(assert\s+\(or\s*\)\)\s*", "", content, flags=re.DOTALL)


def _create_string_interval_mask(lb, ub, coverage, delta, string_domain=None):
    return create_mask_with_coverage(lb, ub, target_coverage=coverage, delta=delta)


def _validate_string_order(string_order):
    if string_order not in {STRING_ORDER_SHORTLEX, STRING_ORDER_LEX}:
        raise ValueError(
            f"Unsupported string_order={string_order!r}. "
            f"Expected {STRING_ORDER_SHORTLEX!r} or {STRING_ORDER_LEX!r}."
        )
    return string_order


def _get_string_to_index_helpers(use_recursive_string_helpers=False, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    string_order = _validate_string_order(string_domain.get("order", DEFAULT_STRING_ORDER))
    if string_order == STRING_ORDER_LEX:
        if use_recursive_string_helpers:
            return RECURSIVE_STRING_TO_INDEX_HELPERS_LEX
        return _build_non_recursive_lex_string_to_index_helpers(string_domain["max_len"])
    if use_recursive_string_helpers:
        return RECURSIVE_STRING_TO_INDEX_HELPERS_SHORTLEX
    return _build_non_recursive_string_to_index_helpers(string_domain["max_len"])


def _ensure_string_to_index_helpers(content, use_recursive_string_helpers=False, string_domain=None):
    if STRING_TO_INDEX_HELPERS_START in content and STRING_TO_INDEX_HELPERS_END in content:
        return content
    return _insert_before_first_checksat(content, _get_string_to_index_helpers(use_recursive_string_helpers, string_domain=string_domain))


def _string_to_index(value, string_domain=None):
    string_domain = string_domain or _get_string_domain_config(54)
    string_order = _validate_string_order(string_domain.get("order", DEFAULT_STRING_ORDER))
    if not isinstance(value, str):
        raise TypeError("Input must be a string.")
    if not value:
        raise ValueError("Input string must be non-empty.")
    if len(value) > string_domain["max_len"]:
        raise ValueError(f"String length exceeds the supported maximum ({string_domain['max_len']}).")
    if any(ch < "a" or ch > "z" for ch in value):
        raise ValueError("Input string must contain only lowercase letters a-z.")

    if string_order == STRING_ORDER_LEX:
        index = len(value) - 1
        for idx, ch in enumerate(value):
            remaining = len(value) - idx - 1
            subtree_size = sum(26**k for k in range(0, remaining + 1))
            index += (ord(ch) - ord("a")) * subtree_size
        return index

    index = sum(26**k for k in range(1, len(value)))
    offset = 0
    for ch in value:
        offset = offset * 26 + (ord(ch) - ord("a"))
    return index + offset


def _escape_smt_string(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_smt_string(value):
    return bytes(value, "utf-8").decode("unicode_escape")


def _free_var_names(free_vars):
    return [var_name for var_name, _ in free_vars]


def _total_strings_up_to_length(max_len):
    return sum(26**k for k in range(1, max_len + 1))


def _get_string_domain_config(string_max_length, string_order=DEFAULT_STRING_ORDER):
    if not isinstance(string_max_length, int):
        raise TypeError("string_max_length must be an integer.")
    if not (MIN_STRING_MAX_LENGTH <= string_max_length <= MAX_STRING_MAX_LENGTH):
        raise ValueError(
            f"Unsupported string_max_length={string_max_length}. "
            f"Expected an integer in [{MIN_STRING_MAX_LENGTH}, {MAX_STRING_MAX_LENGTH}]."
        )
    _validate_string_order(string_order)
    return {
        "max_len": string_max_length,
        "domain_max": _total_strings_up_to_length(string_max_length),
        "order": string_order,
    }
