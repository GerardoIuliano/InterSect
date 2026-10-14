
from solver_utils import create_mask
import csv
from tqdm import tqdm

def generate_mask_for_domain(int_type):
    
    domain = 2**int_type
    start = 0
    end   =  2**int_type - 1

    width_fractions = [round(i * 0.0005, 4) for i in range(1, 51)]
    # 0.0005, 0.0010, 0.0015, ..., 0.0500

    results = []
    for interval_numbers in tqdm(range(1, 1000)):  # 1 → 1000
        for frac in width_fractions:
            interval_width = int(domain * frac)

            # Evita width = 0 per numeri piccoli
            if interval_width < 1:
                interval_width = 1

            for _ in range(5):
                # --- CALL create_mask ---
                p_kstest, D_test, p_ks2sample, D_2sample, merged, num_intervals, width, mask_coverage = create_mask(
                    start,
                    end,
                    interval_numbers,
                    interval_width
                )
                # --- REGISTRA IL RECORD ---
                results.append({
                    "band_number": interval_numbers,
                    "band_width_percentage": frac,
                    "band_width": interval_width,
                    "mask_coverage": mask_coverage,
                    "p_kstest": p_kstest,
                    "p_ks2sample": p_ks2sample,
                    "d_kstest": D_test,
                    "d_ks2sample": D_2sample,
                })

    # -------------------------------
    # SALVA IN CSV
    # -------------------------------

    with open(f"results_rq/rq_1/csv/mask_uniformity_int{int_type}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print("\nSalvato")
    
# generate_mask_for_domain(256)
# generate_mask_for_domain(128)
# generate_mask_for_domain(64)
# generate_mask_for_domain(32)
# generate_mask_for_domain(16)
# generate_mask_for_domain(8)
generate_mask_for_domain(7)