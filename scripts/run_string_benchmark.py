import os
from solver_utils_typed import intervalGuidedSampler

def run_algorithm_on_benchmark(benchmark_path, theta, coverage):
    results_dir = "benchmarks/automatark-lu-80_results_interval_guided_sampler_cov"+str(coverage)

    for filename in sorted(os.listdir(benchmark_path)):
        if filename.endswith(".smt2"):
            result_filename = filename.replace(".smt2", ".csv")
            result_filepath = os.path.join(results_dir, result_filename)

            if os.path.exists(result_filepath):
                print(f"Skipping {filename}: already processed.")
                continue

            smt_filepath = os.path.join(benchmark_path, filename)

            intervalGuidedSampler(smt_filepath, theta=theta, coverage=coverage, delta=0.0050, U=2**256 - 1, timeout=1800, threads=60)
            

if __name__ == "__main__":
    benchmark_folder = "benchmarks/automatark-lu-80"
    run_algorithm_on_benchmark(benchmark_folder, theta=256, coverage=10)
    run_algorithm_on_benchmark(benchmark_folder, theta=256, coverage=30)
    run_algorithm_on_benchmark(benchmark_folder, theta=256, coverage=50)
    run_algorithm_on_benchmark(benchmark_folder, theta=256, coverage=70)
    run_algorithm_on_benchmark(benchmark_folder, theta=256, coverage=90)
