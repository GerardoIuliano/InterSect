from solver_utils import hybrid_state_space_sampler, extract_free_vars, run_z3_and_get_model, extract_free_vars_from_smt2_file, extract_free_vars_2, extract_free_vars_with_type_from_smt2_file
import os, shutil, z3
from z3.z3util import get_vars
from solver_utils_typed import hybrid_state_space_sampler_typed

def run_algorithm_on_benchmark(benchmark_path, theta, coverage, string_max_length=128):
    results_dir = "benchmarks/automatark-lu-100-SAT_results_cov"+str(coverage)+"_strlen"+str(string_max_length)
    # results_dir = "benchmarks/slacks_results_cov"+str(coverage)

    for filename in sorted(os.listdir(benchmark_path)):
        if filename.endswith(".smt2"):
            result_filename = filename.replace(".smt2", ".csv")
            result_filepath = os.path.join(results_dir, result_filename)

            if os.path.exists(result_filepath):
                print(f"Skipping {filename}: already processed.")
                continue

            smt_filepath = os.path.join(benchmark_path, filename)

            samples = hybrid_state_space_sampler_typed(smt_filepath, theta=theta, coverage=coverage, delta=0.0050, use_recursive_string_helpers=False, string_max_length=string_max_length)
            print(f"Generated samples for {filename}:", samples)


# Example usage:
benchmark_folder = "datasets/negated_smt"
run_algorithm_on_benchmark(benchmark_folder, theta=256, coverage=90, string_max_length=27)
