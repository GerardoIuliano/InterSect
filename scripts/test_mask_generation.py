from solver_utils import create_mask_with_coverage, create_string_mask_with_coverage
import scipy.stats

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

mask_for_int = create_mask_with_coverage(0, 2**32, target_coverage=30, delta=0.0050)
print(f"Mask for int: {mask_for_int}")

mask_for_str = create_string_mask_with_coverage(0, 2**256, target_coverage=30, numStringSamples=10, delta=0.0050)
print(f"Mask for str: {mask_for_str}")
flat_mask = [item for sublist in mask_for_str for item in sublist]
normalized_distribution = [string_to_index(x) / TOTAL_STRINGS for x in flat_mask]
# test uniformity of the mask for string

    
kstest_result = scipy.stats.kstest(normalized_distribution, 'uniform', args=(0, 1))

print(f"KS Statistic: {kstest_result.statistic}")
print(f"P-value: {kstest_result.pvalue}")