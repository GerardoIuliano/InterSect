import argparse
import csv
from collections import defaultdict
from pathlib import Path


DEFAULT_INPUT = Path(
    "/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL/results_rq/rq_2/csv/integer_uniformity_summary.csv"
)
DEFAULT_OUTPUT = Path(
    "/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL/results_rq/rq_2/csv/integer_uniformity_overall_by_coverage.csv"
)


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def aggregate_by_coverage(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[int(float(row["Coverage"]))].append(row)

    aggregated_rows = []
    for coverage in sorted(grouped):
        coverage_rows = grouped[coverage]

        number_of_files = sum(float(row["Number of File"]) for row in coverage_rows)
        total_variable = sum(float(row["Total Variable"]) for row in coverage_rows)
        total_models = sum(float(row["Total models"]) for row in coverage_rows)
        reject_h0 = sum(float(row["Reject H0"]) for row in coverage_rows)
        fail_to_reject_h0 = sum(float(row["Fail to Reject H0"]) for row in coverage_rows)
        total_execution_time = sum(float(row["Total execution time"]) for row in coverage_rows)

        average_models_per_file = total_models / number_of_files if number_of_files else 0.0
        mean_time_all = total_execution_time / total_models if total_models else 0.0
        average_intervals_per_mask = (
            sum(float(row["Average intervals per mask"]) * float(row["Number of File"]) for row in coverage_rows)
            / number_of_files
            if number_of_files
            else 0.0
        )
        mask_generation_mean_time = (
            sum(float(row["Mask Generation mean time"]) * float(row["Number of File"]) for row in coverage_rows)
            / number_of_files
            if number_of_files
            else 0.0
        )

        # The summary CSV does not store steady response counts, so this is only an approximation.
        mean_time_steady_weighted_by_models = (
            sum(float(row["mean time/steady"]) * float(row["Total models"]) for row in coverage_rows)
            / total_models
            if total_models
            else 0.0
        )

        aggregated_rows.append(
            {
                "Coverage": coverage,
                "Number of File": int(round(number_of_files)),
                "Variable for File": "overall",
                "Total Variable": int(round(total_variable)),
                "Average models per file": average_models_per_file,
                "Total models": int(round(total_models)),
                "Average intervals per mask": average_intervals_per_mask,
                "Reject H0": int(round(reject_h0)),
                "Fail to Reject H0": int(round(fail_to_reject_h0)),
                "Mask Generation mean time": mask_generation_mean_time,
                "Total execution time": total_execution_time,
                "mean time/all": mean_time_all,
                "mean time/steady (approx)": mean_time_steady_weighted_by_models,
            }
        )

    return aggregated_rows


def write_rows(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate integer_uniformity_summary.csv into one overall row per coverage."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = load_rows(args.input)
    aggregated_rows = aggregate_by_coverage(rows)
    if not aggregated_rows:
        raise ValueError("No rows found in the input CSV.")

    write_rows(aggregated_rows, args.output)
    print(f"Wrote coverage summary to: {args.output}")


if __name__ == "__main__":
    main()
