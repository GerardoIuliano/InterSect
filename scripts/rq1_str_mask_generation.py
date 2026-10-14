
from solver_utils import create_mask, sample_intervals
import csv, scipy.stats
from tqdm import tqdm

TOTAL_STRINGS = sum(26**k for k in range(1, 55))
def string_to_index(s: str) -> int:
    """
    Returns the 0-based index of a string in the lexicographic enumeration
    of all strings over {a..z} with length 1 to 54.

    Order: a, b, ..., z, aa, ab, ..., az, ba, ..., zz, aaa, ...
    """
    if not isinstance(s, str):
        raise TypeError("Input must be a string.")
    if not s:
        raise ValueError("Input string must be non-empty.")

    alphabet = 'abcdefghijklmnopqrstuvwxyz'
    max_len = 54

    if len(s) > max_len:
        raise ValueError(f"String length exceeds the supported maximum ({max_len}).")
    if any(ch not in alphabet for ch in s):
        raise ValueError("Input string must contain only lowercase letters a-z.")

    index = sum(26**k for k in range(1, len(s)))

    offset = 0
    for ch in s:
        offset = offset * 26 + alphabet.index(ch)

    return index + offset

def generate_mask_for_domain(samples):
    
    domain = 2**256
    start = 0
    end   =  2**256 - 1

    width_fractions = [0.0050]

    results = []
    for interval_numbers in tqdm(range(1, 1000)):  # 1 → 1000

        for _ in range(5):
            # --- CALL create_mask ---
            p_kstest, p_ks2sample, merged, num_intervals, width, mask_coverage = create_mask(
                start,
                end,
                interval_numbers,
                int(domain * 0.0050)
            )
            sampled_intervals = sample_intervals(merged, samples)
            flat_sample = [item for sublist in sampled_intervals for item in sublist]
            normalized_distribution = [string_to_index(x) / TOTAL_STRINGS for x in flat_sample]
            # test uniformity of the mask for string               
            kstest_result = scipy.stats.kstest(normalized_distribution, 'uniform', args=(0, 1))
            # --- REGISTRA IL RECORD ---
            results.append({
                "band_number": interval_numbers,
                "band_width_percentage": 0.0050,
                "samples": samples,
                "mask_coverage": mask_coverage,
                "p_kstest": p_kstest,
                "p_ks2sample": p_ks2sample,
                "sample_statistic": kstest_result.statistic,
                "sample_p_value": kstest_result.pvalue,
            })

    # -------------------------------
    # SALVA IN CSV
    # -------------------------------

    with open(f"results_rq/rq_1/csv/mask_uniformity_sampled_str{samples}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print("\nSalvato")
    
generate_mask_for_domain(1)
# generate_mask_for_domain(10)
# generate_mask_for_domain(50)
# generate_mask_for_domain(100)
