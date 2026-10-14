from solver_utils_typed import intervalGuidedSampler


def main():
    # file_path = "benchmarks/ranked_linear_independence/rank_13/rank_13_instance_000.smt2"
    file_path = "benchmarks/slacks-80/10-12.slack.smt2"
    intervalGuidedSampler(
        file_path,
        theta=256,
        coverage=50,
        delta=0.0050,
        U=2**256-1,
        timeout=1800,
        threads=60,
    )


if __name__ == "__main__":
    main()
