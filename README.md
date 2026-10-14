# Beyond Solver Bias: Interval-Guided Uniform Sampling for SMT

## Setup

Create and activate a Python virtual environment, then install the required packages.

### Unix-like systems

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -3 -m venv venv
venv\Scripts\Activate.ps1
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt
```

## Replicate Results

## 1. InterSect Configuration

This section provides the commands used to analyze the CSV files for ranking the best interval width.

### A. Rank the best interval widths

This command computes the ranking of `interval_width_percentage` values across all available CSV files, keeping the top 30 entries and using `alpha = 0.05` as the uniformity threshold.

```bash
python3 results_rq/rq_1/best_interval_width.py --glob "results_rq/rq_1/csv/mask_uniformity_int*.csv" --top 30 --alpha 0.05 --plot
```

### B. Analyze a single file

This command analyzes one CSV file only. Replace the file name with the specific dataset you want to inspect.

```bash
python3 results_rq/rq_1/best_interval_width.py --csv results_rq/rq_1/csv/mask_uniformity_int32.csv --top 30 --alpha 0.05
```

## 2. InterSect behaviour

Run the SAT-rate table builder for the ranked-linear benchmark results.

```bash
python3 results_rq/rq_ranked_linear/build_ranked_linear_sat_percent_table.py
```

## 3. InterSect early stop condition

Run the runtime stopping analysis on the ranked-linear degradation frontier results.

```bash
python3 results_rq/rq_ranked_linear/analyze_frontier_runtime_stopping.py --min-viable-sat-rate 0.53 --bayesian-confidence 0.95
```

## 4. InterSect benchmark

### 4.A InterSect results

Generate the uniformity summary for the InterSect results.

```bash
python3 results_rq/rq_2/are_InterSect_results_uniform_int.py
```

### 4.B HighDiv results

Generate the uniformity summary for the HighDiv results.

```bash
python3 results_rq/rq_2/are_HighDiv_results_uniform_int.py
```

## 5. InterSect integration in the Mythril symbolic execution engine 

Generate the uniformity summary for the case study.

```bash
python3 results_rq/rq_3/is_case_study_uniform.py
```

Compare InterSect samples versus Z3 samples.

```bash
python3 results_rq/rq_3/compare_InterSect_vs_z3_values.py
```

```bash
python3 results_rq/rq_3/summarize_InterSect_vs_z3_differences.py
```

Plot InterSect and Z3 sample distributions.

```bash
python3 results_rq/rq_3/plot_interval_guided_cov30_scatter.py
```
```bash
python3 results_rq/rq_3/plot_z3_baseline_scatter.py
```

## Replicate Experiments

The commands below reproduce the main raw experiment outputs using the `run_*` scripts in `scripts/`.

### A. Case study

Run the case-study pipeline, which generates the interval-guided and Z3 baseline result directories under `mythril_test/SAT_execution_path_int_subset`.

```bash
python3 scripts/run_case_study.py
```

### B. Ranked-linear studies

Run the bounds-size study and the exact-rank dependence study. Both commands generate interval-guided result CSVs under the corresponding benchmark-specific `*_results_interval_guided_sampler_cov*` folders.

```bash
python3 scripts/run_dependences_study.py
```

### C. Benchmark batches

Run the integer and string benchmark batches used in the sampler experiments.

```bash
python3 scripts/run_integer_benchmark.py
python3 scripts/run_string_benchmark.py
```

### D. Sampler smoke test

Run a single interval-guided sampling example on one benchmark file. This is useful as a quick sanity check that the sampler stack is working.

```bash
python3 scripts/run_sampler.py
```

### E. Interval uniformity batch

Generate the combined interval-uniformity CSV used in the analysis from the integer benchmark results.

```bash
python3 scripts/run_interval_uniformity_batch.py --root SamplerResults/Integer --out-csv results_rq/rq_3/csv/interval_uniformity_integer.csv
```

## Run InterSect

Run the InterSect sampler on a single SMT-LIB file.

```bash
python3 scripts/InterSect.py path/to/input.smt2 --theta 256 --coverage 50 --U 2**256-1 --timeout 1800
```

Parameters:
- `path/to/input.smt2`: required SMT-LIB file to sample.
- `--theta`: required partition threshold for variables. Default 256.
- `--coverage`: required target coverage of the finite universe. Default 50.
- `--U`: required finite universe size. Default `2**256-1`.
- `--timeout`: required timeout in seconds. Default 1800.
- `--delta`: optional delta parameter used by the sampler. Default 0.0050 (strongly recommended).
- `--free-var x0 x1`: optional list of free variables to keep; if omitted, all free variables are sampled.
