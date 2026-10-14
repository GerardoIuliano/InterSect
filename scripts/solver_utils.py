import random, re, subprocess, shutil, numpy as np, itertools, z3, csv, pandas as pd, os, uuid, time
from pathlib import Path
from collections import Counter
from scipy.stats import ks_2samp, kstest, chisquare, mannwhitneyu
from math import floor, isclose
from bisect import bisect_right
from z3.z3util import get_vars
from tqdm.auto import tqdm


def hybrid_state_space_sampler(free_vars, smt_filepath, theta, coverage):
##################  hybrid state-space sampler  ##################
    S = set()
    E = set()
    L = set()
    solver_result = {}
    n = 2**256
    
    #make a copy of the file and work on the copy at each iteration to avoid modifying the original SMT formula
    tmp_dir = Path(__file__).parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_filepath = tmp_dir / f"tmp_{uuid.uuid4().hex}.smt2"
    shutil.copy2(smt_filepath, tmp_filepath)
    
    start_time = time.perf_counter()
    for free_var in free_vars:
        partitionVariable(E, L, smt_filepath, free_var, theta=theta)
        
    if E :
        assignments = list(get_assignment_for_E(E))
        if len(assignments) > theta:
            print(f"Too many assignments for E ({len(assignments)}), sampling {theta} random assignments...")
            assignments = random.sample(assignments, theta)
        if assignments is not None:
            for assignment in assignments:
                
                add_e_assignment(assignment, tmp_filepath) # Add assignment of brute force variable to SMT formula 
                mask_var_pairs = []
                if L :
                    # print(f"Variables in E and L")
                    for (var, lb, ub) in tqdm(L, desc="Generating masks..."):
                        mask = create_mask_with_coverage(lb, ub, target_coverage=coverage) # generate mask
                        mask_var_pairs.append((var, mask))
                        n = min(n, len(mask)) 
                        # add bands to SMT formula
                        for (band_lb, band_ub) in mask:
                            add_band(band_lb, band_ub, var, tmp_filepath)
                    for i in range(n):
                        status, model = run_z3_and_get_model(tmp_filepath)
                        
                        if status == "UNSAT" or status == "UNKNOWN":
                            partial_time = time.perf_counter() - start_time
                            start_time = time.perf_counter()
                            save_model(solver_result, model, free_vars, tmp_filepath, status, assignment, mask_var_pairs, partial_time)
                            
                            break
                        if status == "SAT":
                            partial_time = time.perf_counter() - start_time
                            start_time = time.perf_counter()
                            save_sat_model(solver_result, model, free_vars, tmp_filepath, status, mask_var_pairs, partial_time)
                            # remove bands used by model
                            remove_used_band(model, L, tmp_filepath, mask_var_pairs) 
                            # add blocking clause to prevent same model
                            prevent_same_model(model, L, tmp_filepath)
                            # save model in set S
                            S.add(frozenset(model.items()))
                else:
                    # print(f"Variables in E")
                    status, model = run_z3_and_get_model(tmp_filepath)
                    
                    if status == "UNSAT" or status == "UNKNOWN":
                        partial_time = time.perf_counter() - start_time
                        start_time = time.perf_counter()
                        save_model(solver_result, model, free_vars, tmp_filepath, status, assignment, mask_var_pairs, partial_time)
                    if status == "SAT":
                        partial_time = time.perf_counter() - start_time
                        start_time = time.perf_counter()
                        save_sat_model(solver_result, model, free_vars, tmp_filepath, status, mask_var_pairs, partial_time)
                        # remove bands used by model
                        # remove_used_band(model, L, tmp_filepath) 
                        # add blocking clause to prevent same model
                        # prevent_same_model(model, L, tmp_filepath)
                        # save model in set S
                        S.add(frozenset(model.items()))
    if not E and L :
        # print(f"Variables in L")
        mask_var_pairs = []
        for (var, lb, ub) in tqdm(L, desc="Generating masks..."):
            mask = create_mask_with_coverage(lb, ub, target_coverage=coverage) # generate mask
            mask_var_pairs.append((var, mask))
            n = min(n, len(mask)) 
            # add bands to SMT formula
            for (band_lb, band_ub) in mask:
                add_band(band_lb, band_ub, var, tmp_filepath)
        print("Masks generated")
        for i in range(n):
            status, model = run_z3_and_get_model(tmp_filepath)
            
            if status == "UNSAT" or status == "UNKNOWN":
                partial_time = time.perf_counter() - start_time
                start_time = time.perf_counter()
                save_model(solver_result, model, free_vars, tmp_filepath, status, None, mask_var_pairs, partial_time)
                break
            if status == "SAT":
                partial_time = time.perf_counter() - start_time
                start_time = time.perf_counter()
                save_sat_model(solver_result, model, free_vars, tmp_filepath, status, mask_var_pairs, partial_time)
                # remove bands used by model
                remove_used_band(model, L, tmp_filepath, mask_var_pairs) 
                # add blocking clause to prevent same model
                prevent_same_model(model, L, tmp_filepath)
                # save model in set S
                S.add(frozenset(model.items()))  
    ##################  hybrid state-space sampler  ##################
    #save solver_result on csv file
    df = pd.DataFrame(solver_result)
    # Build output directory mirroring the benchmark folder structure
    benchmark_dir = Path(smt_filepath).parent
    results_dir = benchmark_dir.parent / (benchmark_dir.name + "_results" + "_cov" + str(coverage) )
    results_dir.mkdir(parents=True, exist_ok=True)
    # Save CSV
    csv_filename = Path(smt_filepath).stem + ".csv"
    df.to_csv(results_dir / csv_filename, index=False)
    return S

def save_model(solver_result, model, free_vars, tmp_filepath, status, assignment, mask_var_pairs, partial_time):
    """
    Saves a row for UNSAT or UNKNOWN results.
    Free vars from assignment carry their assigned value;
    all other free vars are saved as None.

    Args:
        solver_result:   dict accumulating results (column -> list of values)
        model:           Z3 model dict (ignored for UNSAT/UNKNOWN, kept for signature consistency)
        free_vars:       list of all free variable names in the formula
        tmp_filepath:    path to the tmp .smt2 file used in this iteration
        status:          "UNSAT" or "UNKNOWN"
        assignment:      dict {var_name: value} of variables fixed by E-assignment
        mask_var_pairs:  list of (var, lb, ub) tuples describing the active mask bands
    """
    if status not in ("UNSAT", "UNKNOWN"):
        return

    # Normalize assignment to a plain dict regardless of how it's passed in
    if isinstance(assignment, frozenset):
        assignment_dict = dict(assignment)
    elif isinstance(assignment, dict):
        assignment_dict = assignment
    else:
        assignment_dict = {}

    free_vars_set = set(free_vars)

    # Ensure metadata columns exist
    for meta_key in ("filename", "status", "mask_var_pairs", "time"):
        if meta_key not in solver_result:
            solver_result[meta_key] = [None] * (len(next(iter(solver_result.values()))) if solver_result else 0)

    # Ensure all free var columns exist
    for var in free_vars_set:
        if var not in solver_result:
            solver_result[var] = [None] * (len(next(iter(solver_result.values()))) if solver_result else 0)

    # Append values: assignment value if var was fixed, None otherwise
    for var in free_vars_set:
        solver_result[var].append(assignment_dict.get(var, None))

    solver_result["filename"].append(Path(tmp_filepath).name)
    solver_result["status"].append(status)
    solver_result["mask_var_pairs"].append(list(mask_var_pairs) if mask_var_pairs is not None else None)
    solver_result["time"].append(partial_time) 

def save_sat_model(result, model_dict, free_vars, tmp_filepath, status, mask_var_pairs, partial_time):
    free_vars_set = set(free_vars)

    # Ensure metadata columns exist
    for meta_key in ("filename", "status", "mask_var_pairs", "time"):
        if meta_key not in result:
            result[meta_key] = [None] * (len(next(iter(result.values()))) if result else 0)

    if model_dict is None:
        for key in result:
            result[key].append(None)
        # Overwrite the Nones just appended for metadata
        result["filename"][-1] = Path(tmp_filepath).name
        result["status"][-1] = status
        result["mask_var_pairs"][-1] = list(mask_var_pairs) if mask_var_pairs is not None else None
        return result

    # Filter model_dict to only free vars
    filtered = {k: v for k, v in model_dict.items() if k in free_vars_set}

    # Ensure all free vars exist as keys in result
    for var in free_vars_set:
        if var not in result:
            result[var] = [None] * (len(next(iter(result.values()))) if result else 0)

    # Append values for this iteration
    for var in result:
        if var in ("filename", "status", "mask_var_pairs", "time"):
            continue
        result[var].append(filtered.get(var, None))

    result["filename"].append(Path(tmp_filepath).name)
    result["status"].append(status)
    result["mask_var_pairs"].append(list(mask_var_pairs) if mask_var_pairs is not None else None)
    result["time"].append(partial_time)  # added


    return result

# def save_sat_model(result, model_dict, free_vars):
#     if model_dict is None:
#         # append None for all existing columns
#         for key in result:
#             result[key].append(None)
#         return result
#     # First, ensure all keys exist
#     for var in model_dict:
#         if var not in result:
#             result[var] = [None] * (len(next(iter(result.values()))) if result else 0)
#     # Append values for this iteration
#     for var in result:
#         if var in model_dict:
#             val = model_dict[var]
#             if isinstance(val, list):
#                 result[var].append(val)
#             else:
#                 result[var].append(val)
#         else:
#             result[var].append(None)
#     return result

def remove_used_band(model, L, file_path, mask_var_pairs):
    with open(file_path, 'r') as f:
        content = f.read()

    vars_in_L = {var for var, lb, ub in L}

    # Build a quick lookup: var_name -> index in mask_var_pairs
    mask_index = {var: i for i, (var, mask) in enumerate(mask_var_pairs)}

    for var_name, z3_value in model.items():
        if var_name not in vars_in_L:
            continue

        value = int(str(z3_value))

        band_pattern = re.compile(
            rf'\s*\(and\s+\(>=\s+{re.escape(var_name)}\s+(\d+)\)\s+\(<\s+{re.escape(var_name)}\s+(\d+)\)\)'
        )

        or_block_pattern = re.compile(
            rf'\(assert\s+\(or\s+(?:\s*\(and\s+\(>=\s+{re.escape(var_name)}\s+\d+\)\s+\(<\s+{re.escape(var_name)}\s+\d+\)\))+\s*\)\)',
            re.MULTILINE
        )

        def remove_band_from_block(or_block_match):
            block = or_block_match.group(0)

            def replace(m):
                lb = int(m.group(1))
                ub = int(m.group(2))
                if lb <= value < ub:
                    # Remove from mask_var_pairs too
                    if var_name in mask_index:
                        idx = mask_index[var_name]
                        var, mask = mask_var_pairs[idx]
                        mask_var_pairs[idx] = (var, [b for b in mask if b != (lb, ub)])
                    return ''
                return m.group(0)

            return band_pattern.sub(replace, block)

        content = or_block_pattern.sub(remove_band_from_block, content)

    with open(file_path, 'w') as f:
        f.write(content)
# def remove_used_band(model, L, file_path, mask_var_pairs):
#     """
#     Per ogni variabile nel model presente in L, rimuove la banda (and (>= var LB) (< var UB))
#     che contiene il valore assunto dalla variabile.
#     Rimuove solo dalle clausole (assert (or ...)) e non dalla formula principale.
#     """
#     with open(file_path, 'r') as f:
#         content = f.read()

#     vars_in_L = {var for var, lb, ub in L}

#     for var_name, z3_value in model.items():
#         if var_name not in vars_in_L:
#             continue

#         value = int(str(z3_value))

#         band_pattern = re.compile(
#             rf'\s*\(and\s+\(>=\s+{re.escape(var_name)}\s+(\d+)\)\s+\(<\s+{re.escape(var_name)}\s+(\d+)\)\)'
#         )

#         # Matches only (assert (or ... )) blocks
#         or_block_pattern = re.compile(
#             rf'\(assert\s+\(or\s+(?:\s*\(and\s+\(>=\s+{re.escape(var_name)}\s+\d+\)\s+\(<\s+{re.escape(var_name)}\s+\d+\)\))+\s*\)\)',
#             re.MULTILINE
#         )

#         def remove_band_from_block(or_block_match):
#             block = or_block_match.group(0)

#             def replace(m):
#                 lb = int(m.group(1))
#                 ub = int(m.group(2))
#                 return '' if lb <= value < ub else m.group(0)

#             return band_pattern.sub(replace, block)

#         content = or_block_pattern.sub(remove_band_from_block, content)

#     with open(file_path, 'w') as f:
#         f.write(content)


def prevent_same_model(model, L, file_path):
    """
    Aggiunge una blocking clause considerando solo le variabili presenti in L.
    (assert (not (and (= var1 val1) (= var2 val2) ...)))
    """
    with open(file_path, 'r') as f:
        content = f.read()

    # Estrai solo le variabili presenti in L
    vars_in_L = {var for var, lb, ub in L}

    equalities = ' '.join(
        f'(= {var_name} {int(str(z3_value))})'
        for var_name, z3_value in model.items()
        if var_name in vars_in_L
    )

    if not equalities:
        return  # Nessuna variabile in L, niente da aggiungere

    blocking_clause = f'(assert (not (and {equalities})))\n'

    # Inserisci prima del primo (check-sat)
    lines = content.splitlines(keepends=True)
    first_checksat_index = next(
        (i for i, l in enumerate(lines) if l.strip().startswith('(check-sat)')),
        None
    )

    if first_checksat_index is None:
        content += blocking_clause
    else:
        lines.insert(first_checksat_index, blocking_clause)
        content = ''.join(lines)

    with open(file_path, 'w') as f:
        f.write(content)

def run_z3_and_get_model(smt_filepath, timeout_ms=None):
    """Returns (status, model_dict) where status is 'SAT', 'UNSAT', or 'UNKNOWN'."""
    with open(smt_filepath, 'r') as f:
        smt2_content = f.read()

    smt2_clean = _strip_smt2_commands_2(smt2_content)
    # smt2_clean = _strip_smt2_commands_2(smt2_content)

    solver = z3.Solver()
    if timeout_ms is not None:
        solver.set(timeout=int(timeout_ms))
    assertions = z3.parse_smt2_string(smt2_clean)
    solver.add(assertions)

    result = solver.check()
    print(f"Z3 result: {result}")
    if result == z3.sat:
        model = solver.model()
        model_dict = {model[i].name(): model[model[i]] for i in range(len(model))}
        return "SAT", model_dict
    elif result == z3.unsat:
        return "UNSAT", None
    else:
        return "UNKNOWN", None

def run_z3_and_get_model_2(smt_filepath):
    """Returns (status, model_dict) where status is 'SAT', 'UNSAT', or 'UNKNOWN'"""
    solver = z3.Solver()
    
    try:
        # It's often safer to use the built-in file parser
        assertions = z3.parse_smt2_file(str(smt_filepath))
        solver.add(assertions)
    except Exception as e:
        print(f"Error parsing SMT2 file: {e}")
        return "ERROR", None

    result = solver.check()
    print(f"Z3 result: {result}")

    if result == z3.sat:
        model = solver.model()
        print(f"Z3 model: {model}")
        model_dict = {}
        
        # Iterating over .decls() is more reliable than range(len(model))
        for decl in model.decls():
            name = decl.name()
            value = model[decl]
            
            # Note: value is still a Z3 object (e.g., IntNumRef)
            # If you want a Python int/bool, you might need to convert it:
            if z3.is_int_value(value):
                model_dict[name] = value.as_long()
            elif z3.is_bool(value):
                model_dict[name] = bool(value)
            else:
                model_dict[name] = value  # Keep as Z3 object for complex types
                
        return "SAT", model_dict
    
    elif result == z3.unsat:
        return "UNSAT", None
    else:
        return "UNKNOWN", None

def _tokenize_smt2(content: str) -> list[str]:
    """
    Split SMT2 content into top-level s-expressions and atoms,
    correctly handling:
      - nested parentheses
      - quoted symbols |...|
      - string literals "..."
      - line comments ;...
    """
    tokens = []
    i = 0
    n = len(content)

    while i < n:
        ch = content[i]

        # Skip whitespace between top-level tokens
        if ch in ' \t\n\r':
            i += 1
            continue

        # Line comment — skip to end of line
        if ch == ';':
            while i < n and content[i] != '\n':
                i += 1
            continue

        # Quoted symbol |...|
        if ch == '|':
            start = i
            i += 1
            while i < n and content[i] != '|':
                i += 1
            i += 1  # consume closing |
            tokens.append(content[start:i])
            continue

        # String literal "..."
        if ch == '"':
            start = i
            i += 1
            while i < n:
                if content[i] == '\\':
                    i += 2  # escaped character
                elif content[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            tokens.append(content[start:i])
            continue

        # S-expression: read until matching closing paren
        if ch == '(':
            start = i
            depth = 0
            while i < n:
                c = content[i]
                if c == ';':
                    while i < n and content[i] != '\n':
                        i += 1
                    continue
                if c == '|':
                    i += 1
                    while i < n and content[i] != '|':
                        i += 1
                elif c == '"':
                    i += 1
                    while i < n:
                        if content[i] == '\\':
                            i += 2
                            continue
                        if content[i] == '"':
                            break
                        i += 1
                elif c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            tokens.append(content[start:i])
            continue

        # Bare atom (keyword, symbol, number)
        start = i
        while i < n and content[i] not in ' \t\n\r();"|\n':
            i += 1
        tokens.append(content[start:i])

    return tokens


def _get_command_name(token: str) -> str | None:
    """Extract the leading keyword from an s-expression token."""
    m = re.match(r'^\(\s*([\w-]+)', token)
    return m.group(1).lower() if m else None


# Commands that z3.parse_smt2_string understands
_ALLOWED_COMMANDS = {
    "declare-fun",
    "declare-const",
    "declare-sort",
    "declare-datatypes",
    "define-fun",
    "define-fun-rec",
    "define-funs-rec",
    "define-sort",
    "assert",
    "set-logic",
}


def _strip_smt2_commands_2(smt2_content: str) -> str:
    """
    Robustly strips all commands that z3.parse_smt2_string cannot handle,
    using a proper tokenizer instead of regex. Handles:
      - push/pop and all incremental commands
      - set-info / set-option with nested parens or multiline values
      - multiple check-sat blocks (collects ALL assertions, not just pre-first)
      - quoted symbols |...| and string literals "..."
      - line comments
    """
    tokens = _tokenize_smt2(smt2_content)
    kept = [t for t in tokens if _get_command_name(t) in _ALLOWED_COMMANDS]
    return "\n".join(kept)

def _strip_smt2_commands(smt2_content):
    blacklist = [
        r'\(push\s+\d+\)',
        r'\(pop\s+\d+\)',
        r'\(get-model\)',
        r'\(exit\)',
        r'\(set-option\s+[^)]*\)',
        r'\(set-info\s+[^)]*\)',
        r';[^\n]*',
    ]
    pattern = '|'.join(blacklist)
    cleaned = re.sub(pattern, '', smt2_content).strip()

    # Tieni solo la parte PRIMA del primo (check-sat)
    first_checksat = cleaned.find('(check-sat)')
    if first_checksat != -1:
        cleaned = cleaned[:first_checksat].strip()

    return cleaned


def _collect_symbols(expr, visited=None):
    """Recursively collect all free constants (leaf variables) in a Z3 expression."""
    if visited is None:
        visited = set()

    if expr.get_id() in visited:
        return set()
    visited.add(expr.get_id())

    symbols = set()
    if z3.is_const(expr) and expr.decl().kind() == z3.Z3_OP_UNINTERPRETED:
        symbols.add(expr)
    for child in expr.children():
        symbols |= _collect_symbols(child, visited)
    return symbols
    
def get_assignment_for_E(E):
    ranges = [(var, list(range(lb, ub + 1))) for (var, lb, ub) in E]
    var_names = [r[0] for r in ranges]
    val_lists = [r[1] for r in ranges]
    total = 1
    for vals in val_lists:
        total *= len(vals)
    print(f"Total assignments for E: {total}")
    for combo in itertools.product(*val_lists):
        yield dict(zip(var_names, combo))  # yield invece di return

def add_e_assignment(assignment, file_path):
    with open(file_path, "r") as f:
        content = f.read()

    # Rimuovi eventuali assignment precedenti
    content = re.sub(
        r'\(assert\s+\(and\s+(?:\(=\s+\w[\w@]*\s+\d+\)\s*)+\)\)\n?',
        '',
        content
    )

    # Build the SMT-LIB assert for the assignment
    clauses = [f"(= {var} {val})" for var, val in assignment.items()]
    assignment_assert = "(assert (and\n    " + "\n    ".join(clauses) + "\n))\n"

    # Inserisci prima del primo (check-sat)
    lines = content.splitlines(keepends=True)
    first_checksat_index = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("(check-sat")),
        None
    )

    if first_checksat_index is None:
        content += assignment_assert
    else:
        lines.insert(first_checksat_index, assignment_assert)
        content = "".join(lines)

    with open(file_path, "w") as f:
        f.write(content)

def add_band(lb_band, ub_band, free_var, file_path):
    new_clause = f"(and (>= {free_var} {lb_band}) (< {free_var} {ub_band}))"

    with open(file_path, "r") as f:
        content = f.read()

    # Cerca un assert OR esistente per questa variabile
    # pattern = re.compile(
    #     rf'\(assert\s+\(or\s+((?:\(and\s+\(>=\s+{re.escape(free_var)}\s+\d+\)\s+\(<\s+{re.escape(free_var)}\s+\d+\)\)\s*)+)\)\)',
    #     re.MULTILINE
    # )
    pattern = re.compile(
        rf'\(assert\s+\(or\s+((?:\(and\s+\(>=\s+{re.escape(free_var)}\s+-?\d+\)\s+\(<\s+{re.escape(free_var)}\s+-?\d+\)\)\s*)+)\)\)',
        re.DOTALL # DOTALL aiuta a gestire meglio i ritorni a capo se presenti
    )

    match = pattern.search(content)

    if match:
        # Aggiungi il nuovo clause all'OR esistente
        existing_clauses = match.group(1).strip()
        new_assert = f'(assert (or\n  {existing_clauses}\n  {new_clause}\n))'
        content = content[:match.start()] + new_assert + content[match.end():]
    else:
        # Prima banda per questa variabile: crea l'assert OR
        new_assert = f'(assert (or\n  {new_clause}\n))'

        # Inserisci prima del primo check-sat
        lines = content.splitlines(keepends=True)
        first_checksat_index = next(
            (i for i, l in enumerate(lines) if l.strip().startswith("(check-sat")),
            None
        )

        if first_checksat_index is None:
            content += new_assert + "\n"
        else:
            lines.insert(first_checksat_index, new_assert + "\n")
            content = "".join(lines)

    with open(file_path, "w") as f:
        f.write(content)

# def add_band(lb_band, ub_band, free_var, file_path):
#     band_assert = f"(assert (and (>= {free_var} {lb_band}) (< {free_var} {ub_band})))\n"

#     # Read original content
#     with open(file_path, "r") as f:
#         lines = f.readlines()

#     # Find the first (check-sat)
#     first_checksat_index = None
#     for i, line in enumerate(lines):
#         if line.strip().startswith("(check-sat"):
#             first_checksat_index = i
#             break

#     if first_checksat_index is None:
#         # If no check-sat found, append at the end
#         lines.append(band_assert)
#     else:
#         # Insert band_assert right before the first check-sat
#         lines = (
#             lines[:first_checksat_index]
#             + [band_assert]
#             + lines[first_checksat_index:]
#         )

#     # Write back to the file
#     with open(file_path, "w") as f:
#         f.writelines(lines)

def extract_free_vars_from_smt2_file(file_path):
    free_vars = []
    with open(file_path, "r") as f:
        for line in f:
            # Match zero-arg declare-fun
            m = re.match(r"\(declare-fun\s+(\w+)\s+\(\s*\)\s+.*\)", line)
            if m:
                free_vars.append(m.group(1))
    return free_vars

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

INCREMENTAL_CMDS = re.compile(
    r"^\s*\((push|pop|check-sat|check-sat-assuming|get-model|get-unsat-core|reset|exit)[^)]*\)",
    re.IGNORECASE
)

def strip_incremental_commands(smt2_content: str) -> str:
    """Remove push/pop and other incremental commands unsupported by z3.parse_smt2_string."""
    return "\n".join(
        line for line in smt2_content.splitlines()
        if not INCREMENTAL_CMDS.match(line)
    )

def get_free_consts(formula):
    """Walk a Z3 formula AST and collect all uninterpreted applications."""
    seen = set()
    result = []
    stack = [formula]
    while stack:
        expr = stack.pop()
        if not z3.is_expr(expr):
            continue
        if z3.is_app(expr):
            decl = expr.decl()
            if decl.kind() == z3.Z3_OP_UNINTERPRETED:
                name = decl.name()
                if name not in seen:
                    seen.add(name)
                    result.append(expr)
            stack.extend(expr.children())
    return result

def extract_free_vars_2(smt_file_path: str) -> list[str]:
    """
    Parses an SMT-LIB2 file (including those with push/pop) and returns
    all free variable/function names appearing in the formula.
    """
    with open(smt_file_path, "r") as f:
        content = f.read()

    cleaned = strip_incremental_commands(content)

    try:
        formulas = z3.parse_smt2_string(cleaned)
    except z3.Z3Exception as e:
        raise ValueError(f"Failed to parse SMT2 file after stripping incremental commands: {e}")

    all_vars = set()
    for formula in formulas:
        for v in get_free_consts(formula):
            all_vars.add(v.decl().name())

    return list(all_vars)

def extract_free_vars(smt_file_path: str) -> list[z3.ExprRef]:
    """
    Parses an SMT-LIB2 file and returns all free variables in the formula.

    Args:
        smt_file_path: Path to the .smt2 file.

    Returns:
        A list of unique Z3 expressions representing the free variables.
    """
    formulas = z3.parse_smt2_file(smt_file_path)

    all_vars = set()
    for formula in tqdm(formulas, desc="Extracting free variables..."):
        all_vars.update(get_vars(formula))

    return [v.decl().name() for v in all_vars]

def extract_domain_for_free_var(free_var, file_path):
    with open(file_path, "r") as f:
        content = f.read()
    # Regex unica che cattura sia il min che il max nel blocco (and (>= VAR MIN) (< VAR MAX))
    pattern = rf"\(and\s*\(\>=\s*{free_var}\s*(\d+)\)\s*\(<\s*{free_var}\s*(\d+)\)\)"
    match = re.search(pattern, content)
    if match:
        min_val, max_val = match.groups()
        return int(min_val), int(max_val)
    
def partitionVariable(E: set,L: set,file_path, free_var, theta=1000):

    with open(file_path, "r") as f:
        content = f.read()
    # Pattern per catturare >=, >, <=, < anche con valori negativi
    pattern = fr'\(\s*(>=|>|<=|<)\s+{free_var}\s+(-?[0-9]+)\s*\)'
    matches = re.findall(pattern, content)

    # Default bounds uint256
    # lb = -2**255
    # ub = 2**256 - 1
    # Default bounds int256
    lb = 0
    ub = 2**256 - 1
    for op, val in matches:
        val = int(val)
        if op in ('>=', '>'):
            lb = max(lb, val + (1 if op == '>' else 0))
        elif op in ('<=', '<'):
            ub = min(ub, val - (1 if op == '<' else 0))

    # Verifica se il range è piccolo
    if ub >= lb and (ub - lb) <= theta:
        E.add((free_var, lb, ub))
    else:
        L.add((free_var, lb, ub))



def wrap(x, range_size, min_value):
    """Wrap integer x into [min_value, max_value] using modular arithmetic."""
    return min_value + ((x - min_value) % range_size)

def split_into_regions(min_value, max_value, num_regions):
    step = (max_value - min_value) // num_regions
    regions = [
        (min_value + i * step, min_value + (i + 1) * step if i < num_regions - 1 else max_value)
        for i in range(num_regions)
    ]
    return regions

def region_generator(min_value, max_value, width, num_intervals, range_size):
    num_regions = 10
    regions = split_into_regions(min_value, max_value, num_regions)

    intervals = []
    for region_min, region_max in regions:
        for _ in range(num_intervals // num_regions):
            start = random.randint(region_min, region_max)
            end = wrap(start + width, range_size, min_value)

            if start <= end:
                intervals.append((start, end))
            else:
                intervals.append((start, max_value))
                intervals.append((min_value, end))
    return intervals

def std_generator(min_value, max_value, width, num_intervals, range_size):
    intervals = []
    for _ in range(num_intervals):
        # Randomly select start in [min_value, max_value]
        start = random.randint(min_value, max_value)
        # Compute end using wrapping addition
        end = wrap(start + width, range_size, min_value)
        # Handle wrapped intervals that cross the boundary; normalize as two cases:
        # 1. Simple non-wrapping interval: start <= end
        # 2. Wrapping interval: start > end → split into two intervals
        if start <= end:
            intervals.append((start, end))
        else:
            # Wrapped interval; becomes two intervals:
            # [start, max_value] and [min_value, end]
            intervals.append((start, max_value))
            intervals.append((min_value, end))
    return intervals

# ------------------------------------------------------------------------------------------------------

def multiset_from_intervals(intervals, min_value, max_value):
    S = Counter()

    for a, b in intervals:
        try:   
            num_samples = max(3, int(5000 * (b - a) / (max_value - min_value)))
            print(f"Generating {num_samples} samples for interval of size {b - a}")
            norm_a = (a - min_value) / (max_value - min_value)
            norm_b = (b - min_value) / (max_value - min_value)
            pts = np.linspace(norm_a, norm_b, num_samples)
        except Exception as e:
            print("\n--- ERROR IN INTERVAL ---")
            print("a =", a, type(a))
            print("b =", b, type(b))
            print("num_samples =", num_samples)
            raise e
        for x in pts:
            S[round(x,6)] += 1
    return S

def multiset_from_intervals_2(intervals, min_value, max_value):
    S = Counter()

    for a, b in intervals:
        try:   
            num_samples = max(5, int(1000 * (b - a) / (max_value - min_value)))
            # if not b-a > 0 and num_samples < b-a:
            #     num_samples = b-a
            # print("num_samples =", num_samples, "for interval of size", b - a)
            norm_a = (a - min_value) / (max_value - min_value)
            norm_b = (b - min_value) / (max_value - min_value)
            pts = np.linspace(norm_a, norm_b, num_samples)
        except Exception as e:
            print("\n--- ERROR IN INTERVAL ---")
            print("a =", a, type(a))
            print("b =", b, type(b))
            print("num_samples =", num_samples)
            raise e
        for x in pts:
            S[round(x,16)] += 1
    return S
# ------------------------------------------------------------------------------------------------------

def allocate_samples_per_interval(merged_intervals, total_samples, min_per_interval=1):
    """Distribuisce total_samples proporzionalmente alla lunghezza di ogni merged interval.
       Restituisce lista di sample counts corrispondenti a merged_intervals."""
    lengths = [b-a for a,b in merged_intervals]
    total_len = sum(lengths)
    if total_len == 0:
        return [min_per_interval]*len(merged_intervals)
    # prima assegna proporzionale (float), poi arrotonda e correggi per somma esatta
    raw = [total_samples * (L / total_len) for L in lengths]
    alloc = [max(min_per_interval, int(floor(x))) for x in raw]
    # correggi differenza dovuta all'arrotondamento
    diff = total_samples - sum(alloc)
    i = 0
    # distribuisci l'eventuale differenza (+/-) uno per volta
    while diff != 0:
        if diff > 0:
            alloc[i % len(alloc)] += 1
            diff -= 1
        else:  # diff < 0
            # evita togliere sotto il minimo
            j = i % len(alloc)
            if alloc[j] > min_per_interval:
                alloc[j] -= 1
                diff += 1
        i += 1
    return alloc

def integer_linspace(a: int, b: int, n: int):
    """Genera n campioni interi equidistanti tra a e b inclusi (usa aritmetica intera).
       Se n==1 restituisce il punto medio (floor)."""
    if n <= 0:
        return []
    if n == 1:
        return [ (a + b) // 2 ]
    # usiamo formula: x_j = a + round(j*(b-a)/(n-1))  con aritmetica intera
    width = b - a
    res = []
    for j in range(n):
        # moltiplichiamo prima per j per mantenere precisione integer
        # usare floor division per avere monotonia
        x = a + (width * j) // (n - 1)
        res.append(x)
    return res

def integer_uniform_sample(a, b, n):
    return [a + int((b-a) * np.random.random()) for _ in range(n)]

def build_mask_multiset(intervals, merged_intervals, total_samples=2500, min_per_interval=1):
    """
    intervals: lista di (start,end) originali (possono sovrapporsi)
    merged_intervals: lista già mergeata (disjoint, ordinata)
    total_samples: numero totale di campioni da generare sull'intera maschera (dinamico)
    min_per_interval: minimo campioni da garantire per ciascun merged interval
    Restituisce Counter dei valori normalizzati (float in [0,1] arrotondati a 1e-6).
    """
    # 1) precomputa lunghezze cumulative sui merged_intervals
    lengths = [b-a for a,b in merged_intervals]
    cum = []
    acc = 0
    for L in lengths:
        cum.append(acc)
        acc += L
    total_len = acc
    if total_len == 0:
        raise ValueError("Merged mask has zero total length")

    # 2) decide quanti samples assegnare a ciascun merged interval (dinamico)
    samples_per_merged = allocate_samples_per_interval(merged_intervals, total_samples, min_per_interval)

    # 3) per ogni interval originale, generiamo punti interi e li mappiamo alla merged mask
    normalized_points = []

    # Per semplicità: generiamo campioni per merged interval (non per original intervals)
    # Questo evita il problema di campionare punti che sono fuori dalla merged mask.
    # Se tu vuoi campionare *per original interval* specificamente, si può adattare.
    for idx, (ma, mb) in enumerate(merged_intervals):
        n = samples_per_merged[idx]
        # genera n punti interi equidistanti in [ma, mb]
        pts = integer_uniform_sample(ma, mb, n)
        # normalizza ogni punto rispetto alla maschera usando la lunghezza cumulativa
        base = cum[idx]
        for x in pts:
            # x è intero <= grandi dimensioni ma Python li gestisce
            # normalized float in [0,1] = (base + (x - ma)) / total_len
            # convertiamo in float qui (precisione double sufficiente per KS)
            x_norm = (base + (x - ma)) / total_len
            normalized_points.append(round(float(x_norm), 6))

    # 4) colleziona in multiset
    return Counter(normalized_points)

# ------------------------------------------------------------------------------------------------------

# merge intervals (assume intervals as (a,b) ints)
def merge_intervals(intervals):
    if not intervals:
        return []
    iv = sorted([(int(a), int(b)) for a,b in intervals], key=lambda x: x[0])
    merged = [iv[0]]
    for s,e in iv[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s,e))
    return merged

# 1) Midpoints KS test (mask uniformity w.r.t. domain)
def ks_test_midpoints(merged_intervals, domain_start, domain_end):
    mids = []
    for a,b in merged_intervals:
        mids.append((a + b) / 2.0)
    # normalize to [0,1]
    denom = domain_end - domain_start
    if denom == 0:
        raise ValueError("domain zero length")
    u = np.array([(m - domain_start) / denom for m in mids])
    # if very few intervals, KS loses power
    stat, p = kstest(u, 'uniform')
    return stat, p, u

# 2) Chi-square on covered length per bin
def chi2_on_bins(merged_intervals, domain_start, domain_end, k=30):
    # build bin edges (use integer arithmetic)
    domain_start = int(domain_start); domain_end = int(domain_end)
    domain_len = domain_end - domain_start
    if domain_len <= 0:
        raise ValueError("invalid domain")
    # equal-sized bins by domain coordinates
    edges = [ domain_start + (domain_len * i)//k for i in range(k) ] + [domain_end]
    obs = np.zeros(k, dtype=float)
    # for each merge interval, add covered length overlapping bins
    for a,b in merged_intervals:
        # clip
        a = max(a, domain_start); b = min(b, domain_end)
        if b <= a:
            continue
        # find first bin index
        i = bisect_right(edges, a) - 1
        while a < b and i < k:
            left = max(a, edges[i])
            right = min(b, edges[i+1])
            if right > left:
                obs[i] += (right - left)
            i += 1
    total_covered = obs.sum()
    # expected per bin (proportional to bin width; here equal widths -> total_covered/k)
    exp = np.ones(k) * (total_covered / k)
    # make sure expected >= some threshold; if not, combine bins or use permutation test
    # compute chi-square
    # if any exp is zero, chisquare will fail: handle trivial case
    if np.any(exp == 0):
        raise ValueError("some expected count is zero; choose fewer bins or different approach")
    chi2_stat, p = chisquare(f_obs=obs, f_exp=exp)
    return chi2_stat, p, obs, exp, edges

# 3) Cumulative coverage deviation (Kolmogorov-like)
def cumulative_deviation(merged_intervals, domain_start, domain_end, m=1000):
    # compute cumulative covered length at m grid points
    domain_start = int(domain_start); domain_end = int(domain_end)
    domain_len = domain_end - domain_start
    xs = [ domain_start + (domain_len * i)//m for i in range(m+1) ]  # m+1 points including end
    # build cumulative covered length at x
    cum = np.zeros(len(xs), dtype=float)
    idx = 0
    covered = 0
    # process merged intervals in sorted order
    mi = iter(merged_intervals)
    merged_list = merged_intervals[:]  # assume sorted
    j = 0
    for i,x in enumerate(xs):
        # compute covered length <= x
        covered_x = 0
        for a,b in merged_list:
            if b <= domain_start:
                continue
            if a >= x:
                break
            covered_x += max(0, min(b, x) - max(a, domain_start))
        cum[i] = covered_x
    total_cov = cum[-1]
    if total_cov == 0:
        raise ValueError("mask empty")
    # normalize cum by total_cov and compare to x' = (x - domain_start)/domain_len
    xnorm = np.array([ (xi - domain_start) / domain_len for xi in xs ])
    G = cum / total_cov
    D = np.max(np.abs(G - xnorm))
    # no exact p-value formula here; you can get empirical p by simulation:
    return D, xnorm, G

# ------------------------------------------------------------------------------------------------------

def uniform_multiset(domain_start, domain_end, size):
    X = np.random.uniform(0, 1, size)   # <-- normalized domain
    X = np.round(X, 6)
    return Counter(X)

def expand_multiset(M):
    arr = []
    for x, count in M.items():
        arr.extend([x] * count)
    return np.array(arr)


def chi_square_uniform_points(normalized_points, max_bins=10, min_expected=5, alpha=0.05):
    """Chi-square uniformity test over normalized points in [0, 1]."""
    n = len(normalized_points)
    if n < 2 * min_expected:
        return False, None, None, 0

    bins = max(2, min(max_bins, n // min_expected))
    observed, _ = np.histogram(normalized_points, bins=bins, range=(0.0, 1.0))
    expected = np.full(bins, n / bins)

    if np.any(expected < min_expected):
        return False, None, None, bins

    chi2_stat, p = chisquare(f_obs=observed, f_exp=expected)
    return p > alpha, chi2_stat, p, bins

BIN_x_COV ={
    10: 4,
    20: 4,
    30: 4,
    40: 4,
    50: 4,
    60: 4,
    70: 7,
    80: 7,
    90: 8,
}

def chi_square_interval_intersections(intervals, domain_start, domain_end, bins=4, min_expected=5, alpha=0.05):
    """Chi-square test over the number of intervals intersecting each domain bin."""
    bins = len(intervals) // 5  # Use predefined bin counts if available

    domain_start = int(domain_start)
    domain_end = int(domain_end)
    domain_size = domain_end - domain_start
    if domain_size <= 0 or bins <= 1:
        return False, None, None, bins
    
    boundaries = [domain_start + (domain_size * i) // bins for i in range(bins + 1)]
    observed = np.zeros(bins, dtype=float)

    for start, end in intervals:
        start = max(domain_start, min(int(start), domain_end))
        end = max(domain_start, min(int(end), domain_end))
        if end <= start:
            continue

        left = max(0, bisect_right(boundaries, start) - 1)
        right = min(bins - 1, np.searchsorted(boundaries, end, side="left") - 1)
        if right < left:
            continue

        observed[left:right + 1] += 1

    total_observed = observed.sum()
    if total_observed <= 0:
        return False, None, None, bins

    expected = np.full(bins, total_observed / bins)
    if np.any(expected < min_expected):
        return False, None, None, bins

    chi2_stat, p = chisquare(f_obs=observed, f_exp=expected)
    return p > alpha

def kolmogorov_smirnov_test(intervals, min_value, max_value, band_width, alpha=0.05):
    S_real = multiset_from_intervals_2(intervals, min_value, max_value)
    S_ideal = uniform_multiset(min_value, max_value, sum(S_real.values()))
    
    R = expand_multiset(S_real)
    I = expand_multiset(S_ideal)
    
    if band_width == 1:
        U, p = mannwhitneyu(R, I, alternative='two-sided')
        distribution_ok = p > 0.05
    else:
        D, p = ks_2samp(R, I)
        distribution_ok = D < 0.10 and p > 0.05
    return distribution_ok

# ----------------------------------------------STRING--------------------------------------------------------

TOTAL_STRINGS = sum(26**k for k in range(1, 55))

def index_to_string(i: int) -> str:
    """
    Returns the string at 0-based position i in the lexicographic enumeration
    of all strings over {a..z} with length 1 to 54.

    Order: a, b, ..., z, aa, ab, ..., az, ba, ..., zz, aaa, ...

    Indices >= TOTAL_STRINGS are clamped via modulo to stay in range.
    """
    if i < 0:
        raise ValueError("Index must be non-negative.")

    i = i % TOTAL_STRINGS  # clamp out-of-range indices

    ALPHABET = 'abcdefghijklmnopqrstuvwxyz'
    MAX_LEN  = 54

    # --- Step 1: determine the length of the target string ---
    length = 1
    count  = 26          # number of strings of current length (26^length)

    while i >= count:
        if length == MAX_LEN:
            raise ValueError(f"Index {i} exceeds the language (max length {MAX_LEN}).")
        i     -= count
        length += 1
        count  = 26 ** length

    # --- Step 2: decode the offset `i` in base-26 into `length` characters ---
    chars = []
    for _ in range(length):
        chars.append(ALPHABET[i % 26])
        i //= 26

    return ''.join(reversed(chars))




def sample_interval(start: int, end: int, n: int) -> list[str]:
    """
    Uniformly samples n integers from [start, end] (inclusive)
    and returns their corresponding strings.

    - If n >= number of integers in the interval, all integers are returned.
    - Samples are always inclusive of both endpoints.
    """
    if start > end:
        raise ValueError(f"start ({start}) must be <= end ({end}).")
    if n <= 0:
        raise ValueError("n must be positive.")

    interval_size = end - start + 1

    if n >= interval_size:
        # Return every integer in the interval
        indices = range(start, end + 1)
    else:
        # Spread n points uniformly across [start, end], always including both endpoints
        indices = [
            round(start + i * (interval_size - 1) / (n - 1))
            for i in range(n)
        ] if n > 1 else [start]

    return [index_to_string(idx) for idx in indices]


def sample_intervals(intervals: list[tuple[int, int]], n: int) -> list[list[str]]:
    """
    Applies sample_interval to a list of (start, end) intervals.
    Returns a list of string lists, one per interval.
    """
    return [sample_interval(start, end, n) for start, end in intervals]

def create_string_mask_with_coverage(min_value, max_value, target_coverage, numStringSamples, delta):
    if min_value > max_value:
        raise ValueError("min_value must be <= max_value")
    cardinality = max_value - min_value + 1
    band_width = max(1, int(cardinality * delta))  # 0.12% del dominio
    if band_width == 1:
        min_intervals = int(target_coverage / band_width) - 10
    else:
        min_intervals = int((target_coverage / 100) / delta)

    distribution_ok = False
    iteration = 0
    
    while (
        not distribution_ok or
        (mask_coverage > target_coverage + 2) or
        (mask_coverage < target_coverage - 2)):
        # ---- Generate intervals ----
        intervals = std_generator(min_value, max_value, band_width, min_intervals, cardinality)
        
        # ---- Merge intervals ----
        intervals.sort(key=lambda x: x[0])
        merged = []

        for interval in intervals:
            if not merged:
                merged.append(interval)
            else:
                last_start, last_end = merged[-1]
                curr_start, curr_end = interval

                if curr_start <= last_end:  # overlap
                    merged[-1] = (last_start, max(last_end, curr_end))
                else:
                    merged.append(interval)
        # ---- Calculate mask coverage ----
        mask_coverage = sum(e - s for s,e in merged)/(max_value - min_value)
        mask_coverage = mask_coverage*100

        S_real = multiset_from_intervals_2(intervals, min_value, max_value)
        S_ideal = uniform_multiset(min_value, max_value, sum(S_real.values()))
        
        R = expand_multiset(S_real)
        I = expand_multiset(S_ideal)
        
        # SMALL_SAMPLE_THRESHOLD = 50
        # if len(R) < SMALL_SAMPLE_THRESHOLD or len(I) < SMALL_SAMPLE_THRESHOLD:
        
        if band_width == 1:
            U, p = mannwhitneyu(R, I, alternative='two-sided')
            distribution_ok = p > 0.05
        else:
            D, p = ks_2samp(R, I)
            distribution_ok = D < 0.10 and p > 0.05
        
        iteration += 1
            
        if ((iteration % 5 == 0) and (mask_coverage < target_coverage + 2)):
            min_intervals += 1
        if ((mask_coverage > target_coverage + 2)):
            min_intervals -= 1
    #sample each merged interval and convert samples to strings using index_to_string
    #final_mask = []
    sample_intervals_list = sample_intervals(merged, n=numStringSamples)  # 10 samples per merged interval
    return sample_intervals_list

# ----------------------------------------------STRING--------------------------------------------------------


def create_mask_with_coverage(min_value, max_value, target_coverage, delta):
    if min_value > max_value:
        raise ValueError("min_value must be <= max_value")
    cardinality = max_value - min_value + 1
    band_width = max(1, int(cardinality * delta))  # delta del dominio
    if band_width == 1:
        min_intervals = int(target_coverage / band_width) - 10
    else:
        min_intervals = int((target_coverage / 100) / delta)

    distribution_ok = False
    iteration = 0
    while (
        not distribution_ok or
        (mask_coverage > target_coverage + 2) or
        (mask_coverage < target_coverage - 2)):
        # ---- Generate intervals ----
        intervals = std_generator(min_value, max_value, band_width, min_intervals, cardinality)
        
        # ---- Merge intervals ----
        intervals.sort(key=lambda x: x[0])
        merged = []

        for interval in intervals:
            if not merged:
                merged.append(interval)
            else:
                last_start, last_end = merged[-1]
                curr_start, curr_end = interval

                if curr_start <= last_end:  # overlap
                    merged[-1] = (last_start, max(last_end, curr_end))
                else:
                    merged.append(interval)
        # ---- Calculate mask coverage ----
        mask_coverage = sum(e - s for s,e in merged)/(max_value - min_value)
        mask_coverage = mask_coverage*100

        distribution_ok = kolmogorov_smirnov_test(
            intervals,
            min_value,
            max_value,
            band_width
        )
        
        iteration += 1
            
        if ((iteration % 5 == 0) and (mask_coverage < target_coverage + 2)):
            min_intervals += 1
        if ((mask_coverage > target_coverage + 2)):
            min_intervals -= 1
    # print("Coverage: ", mask_coverage)
    # print("Bands: ", merged)
    return merged
    


def create_mask(min_value, max_value, num_intervals, width):
    """Generate random intervals in [min_value, max_value] allowing
    overflow-wrapping semantics on interval bounds.    """

    if min_value > max_value:
        raise ValueError("min_value must be <= max_value")
    if width < 0:
        raise ValueError("width must be >= 0")

    cardinality = max_value - min_value + 1
    band_width = max(1, int(cardinality * 0.0050))  # 0.12% del dominio

    # ---- Generate intervals ----
    # Use standard generator
    intervals = std_generator(min_value, max_value, width, num_intervals, cardinality)
    # print("Generating ...")
    # ---- Merge intervals ----
    intervals.sort(key=lambda x: x[0])
    merged = []

    for interval in intervals:
        if not merged:
            merged.append(interval)
        else:
            last_start, last_end = merged[-1]
            curr_start, curr_end = interval

            if curr_start <= last_end:  # overlap
                merged[-1] = (last_start, max(last_end, curr_end))
            else:
                merged.append(interval)
    # ---- Calculate mask coverage ----
    mask_coverage = sum(e - s for s,e in merged)/(max_value - min_value)
    # print("Coverage: ", mask_coverage)
   
    # ---- Calculate multiset from intervals ----
    # S_real = build_mask_multiset(intervals, merged, total_samples=2500, min_per_interval=1)
    S_real = multiset_from_intervals_2(intervals, min_value, max_value)
    S_ideal = uniform_multiset(min_value, max_value, sum(S_real.values()))


    R = expand_multiset(S_real)
    I = expand_multiset(S_ideal)
    SMALL_SAMPLE_THRESHOLD = 100

    # if band_width == 1:
    #     U, p = mannwhitneyu(R, I, alternative='two-sided')
    # else:
    D_test, p_kstest = kstest(R, 'uniform')
    D_2sample, p_ks2sample = ks_2samp(R, I)

    # ks_stat, ks_pvalue = ks_2samp(R, I)
    # print(f"Uniform multiset: KS stat (D):  {ks_stat:.3f}, P-value: {ks_pvalue}")
    # ks_stat, ks_pvalue = kstest(R, 'uniform')
    # print(f"Uniform distribution: KS stat (D):  {ks_stat:.3f}, P-value: {ks_pvalue}")

    return p_kstest, D_test, p_ks2sample, D_2sample, merged, num_intervals, width, mask_coverage


def add_mask(mask, free_var, file_path):
    # Read original content
    with open(file_path, "r") as f:
        lines = f.readlines()

    # Build the SMT-LIB assert for excluding masked ranges
    clauses = []
    for low, high in mask:
        clauses.append(f"(not (and (<= {low} {free_var}) (<= {free_var} {high})))")

    mask_assert = "(assert (and\n    " + "\n    ".join(clauses) + "\n))\n"

    # Find the first (check-sat)
    first_checksat_index = None
    for i, line in enumerate(lines):
        if line.strip().startswith("(check-sat"):
            first_checksat_index = i
            break

    if first_checksat_index is None:
        # If no check-sat found, append at the end
        lines.append(mask_assert)
    else:
        # Insert mask_assert right before the first check-sat
        lines = (
            lines[:first_checksat_index]
            + [mask_assert]
            + lines[first_checksat_index:]
        )

    # Write back to the file
    with open(file_path, "w") as f:
        f.writelines(lines)



def run_z3_and_collect_models(file_path):
    """
    Runs Z3 CLI on the SMT-LIB file and collects all SAT models.
    Ignores ControlFlow blocks and removes quotes at start/end.
    """
    result = subprocess.run(["z3", file_path], capture_output=True, text=True)
    output = result.stdout.splitlines()
    
    models = []
    current_model = {}
    sat_block = False
    skip_block = False  # flag for skipping ControlFlow block

    for line in output:
        line = line.strip()

        # Remove starting quote
        if line.startswith('"'):
            line = line[1:].strip()
        # Remove ending quote
        if line.endswith('"'):
            line = line[:-1].strip()

        # Skip multi-line ControlFlow block
        if skip_block:
            if line == "}":
                skip_block = False
            continue

        # Detect start of ControlFlow block
        if line.startswith("ControlFlow -> {"):
            skip_block = True
            continue

        # SAT / UNSAT handling
        if line == "sat":
            sat_block = True
            current_model = {}
        elif line == "unsat":
            sat_block = False
        elif sat_block:
            # Capture variable assignments
            if "->" in line:
                var, val = map(str.strip, line.split("->", 1))
                current_model[var] = val

        # End of SAT block
        if sat_block and line == "":
            if current_model:
                models.append(current_model)
                current_model = {}
                sat_block = False

    # Append last model if any
    if current_model:
        models.append(current_model)

    return models

def create_blocking_assert(model, or_=True):
    # Create a blocking assert from a model.
    terms = []
    conjunctor = "or" if or_ else "and"
    
    for var, val in model.items():
        # Ignora variabili con @
        if "@" not in var:
            terms.append(f"(not (= {var} {val}))")
    
    return f"(assert ({conjunctor}\n    " + "\n    ".join(terms) + "\n))"

def add_blocking_assert(blocking_assert, file_path):
    # Read original content
    with open(file_path, "r") as f:
        lines = f.readlines()

    # Trova il primo (check-sat)
    first_checksat_index = None
    for i, line in enumerate(lines):
        if line.strip().startswith("(check-sat"):
            first_checksat_index = i
            break

    if first_checksat_index is None:
        # Se non c'è check-sat, append alla fine
        lines.append(blocking_assert + "\n")
    else:
        # Inserisci blocking_assert subito prima del primo check-sat
        lines = (
            lines[:first_checksat_index]
            + [blocking_assert + "\n"]
            + lines[first_checksat_index:]
        )

    # Scrivi di nuovo il file
    with open(file_path, "w") as f:
        f.writelines(lines)


def get_sat_statuses(file_path):
    result = subprocess.run(["z3", file_path], capture_output=True, text=True)
    output = result.stdout.strip().splitlines()

    statuses = []
    count = 1

    for line in output:
        line = line.strip()
        if line in ("sat", "unsat", "unknown"):
            statuses.append((f"assert_{count}", line))
            count += 1

    return statuses

def add_get_model_after_sat_checks(file_path):
    # Leggi il contenuto originale
    statuses = get_sat_statuses(file_path)
    with open(file_path, "r") as f:
        lines = f.readlines()
    # controlla che ci sia almeno un SAT status
    if not any(result == "sat" for _, result in statuses):
        return False
    new_lines = []
    check_index = 0  # Indica quale check-sat stiamo processando

    for line in lines:
        stripped = line.strip()

        # Copia sempre la linea
        new_lines.append(line)

        # Se è un (check-sat)
        if stripped.startswith("(check-sat"):
            # Guarda il risultato associato
            label, result = statuses[check_index]

            if result == "sat":
                # Aggiungi il get-model SUBITO dopo
                new_lines.append("(get-model)\n")

            check_index += 1

    # Scrivi il nuovo file
    with open(file_path, "w") as f:
        f.writelines(new_lines)
    return True



def copy_smt_for_processing(original_path: Path, run_id: str | None) -> Path:
    tmp_dir = Path(__file__).parent.parent / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    # Ricava nome base e estensione
    stem = original_path.stem          # "m01d2485e"
    suffix = original_path.suffix      # ".smt2"

    # Se ID è vuoto o None → usa nome originale
    if run_id is None or str(run_id).strip() == "":
        new_name = stem + suffix
    else:
        new_name = f"{stem}_{run_id}{suffix}"

    # Path completo nella cartella tmp/
    new_path = tmp_dir / new_name

    # Copia del file
    shutil.copy2(original_path, new_path)

    return new_path

def select_free_vars(
    free_vars: list[tuple[str, str]],
    keep: list[str] | None = None,
    select_type: str | None = None,
    interactive: bool = True,
) -> list[tuple[str, str]]:
    """
    Select which free variables to keep.
    
    Args:
        free_vars: list of (name, type) tuples
        keep: optional list of variable names to keep directly (skips interaction)
        select_type: if provided, pre-filters free_vars to only variables of this type
        
    Returns:
        list of (name, type) tuples for kept variables
    """
    if select_type is not None:
        free_vars = [(name, typ) for name, typ in free_vars if typ == select_type]

    name_to_var = {name: (name, typ) for name, typ in free_vars}

    # --- Non-interactive mode ---
    if keep is not None or not interactive:
        if keep is None:
            keep = [name for name, _ in free_vars]  # tieni tutto se non specificato
        invalid = [n for n in keep if n not in name_to_var]
        if invalid:
            raise ValueError(f"Unknown variable names: {invalid}")
        selected = [name_to_var[n] for n in keep]
        discarded = [v for v in free_vars if v not in selected]
        print(f"✓ Kept    ({len(selected)}): {[n for n, _ in selected]}")
        print(f"✗ Discarded ({len(discarded)}): {[n for n, _ in discarded]}")
        return selected

    # --- Interactive mode ---
    print("\n=== Free Variable Selection ===")
    type_info = f" of type '{select_type}'" if select_type is not None else ""
    print(f"Found {len(free_vars)} free variables{type_info}:\n")
    for i, (name, typ) in enumerate(free_vars):
        print(f"  [{i}] {name:20s} : {typ}")
    print("\nOptions:")
    print("  - Enter indices to KEEP (e.g. '0 2 3')")
    print("  - Enter 'all' to keep all")
    print("  - Enter 'none' to discard all")
    print("  - Enter variable names directly (e.g. 'call_value1 sender_2')")

    while True:
        choice = input("\nYour selection: ").strip().lower()

        if choice == "all":
            selected = free_vars
            break
        elif choice == "none":
            selected = []
            break
        else:
            tokens = choice.split()
            if all(t.isdigit() for t in tokens):
                indices = [int(t) for t in tokens]
                invalid = [i for i in indices if i >= len(free_vars)]
                if invalid:
                    print(f"  Invalid indices: {invalid}. Try again.")
                    continue
                selected = [free_vars[i] for i in indices]
                break
            invalid = [t for t in tokens if t not in name_to_var]
            if invalid:
                print(f"  Unknown variable names: {invalid}. Try again.")
                continue
            selected = [name_to_var[t] for t in tokens]
            break

    discarded = [v for v in free_vars if v not in selected]
    print(f"\n✓ Kept    ({len(selected)}): {[n for n, _ in selected]}")
    print(f"✗ Discarded ({len(discarded)}): {[n for n, _ in discarded]}")
    return selected
# USAGE
# file_path = Path(__file__).parent.parent / "output" / "me4a137ba.smt2"
# free_vars = extract_free_vars_from_smt2_file(file_path)
# for free_var in free_vars:
#     print(is_brute_force_applicable(file_path, free_var, max_size=1000))

# file_path = Path(__file__).parent.parent / "output" / "m01d2485e.smt2"
# num_intervals = 5
# # Extract free variables
# free_vars = extract_free_vars_from_smt2_file(file_path)
# free_var = free_vars[1]  # Just take the first one for this example
# # For each free variable, extract domain and generate mask
# tmp_file_path_main = copy_smt_for_processing(file_path, None)
# if add_get_model_after_sat_checks(tmp_file_path_main):
#     # Cicla sulle free variables
#     # for free_var in free_vars:
#     for _ in range(1):
#         z3_models_collected = []
#         min_value, max_value = extract_domain_for_free_var(free_var, tmp_file_path_main)
#         domain = max_value - min_value + 1
#         interval_width = int(domain * 0.001)   
#         # Maschera 100 volte il dominio della free variable
#         for i in range(800):
#             # Crea una copia temporanea del file SMT per questa iterazione
#             tmp_file_path_to_process = copy_smt_for_processing(tmp_file_path_main, str(i)+"_"+free_var)
#             # Crea la maschera e aggiungila al file temporaneo
#             mask = create_mask(min_value, max_value, num_intervals, interval_width)
#             add_mask(mask, free_var, tmp_file_path_to_process)
#             # Aggiungi le blocking asserts per tutti i modelli raccolti finora
#             for model in z3_models_collected:
#                 blocking_clause = create_blocking_assert(model, or_=False)
#                 add_blocking_assert(blocking_clause, tmp_file_path_to_process)
#             # Esegui Z3 e raccogli il nuove modello
#             model = run_z3_and_collect_models(tmp_file_path_to_process)
#             #aggiungi il modello raccolto alla lista
#             if not model:
#                 continue
#             z3_models_collected.append(model[0])
#         # salva i modelli raccolti in un file csv
#         models_output_path = Path(__file__).parent.parent / "output" / f"models_{free_var}.csv"
#         with open(models_output_path, "w") as f:
#             # Scrivi header
#             if z3_models_collected:
#                 header = ",".join(z3_models_collected[0].keys())
#                 f.write(header + "\n")
#             # Scrivi modelli
#             for model in z3_models_collected:
#                 row = ",".join(model.values())
#                 f.write(row + "\n")
        


# import csv
# domain = 2**8
# start = 0
# end   =  2**8 - 1


# width_fractions = [
#     0.0001,  # 0.01%
#     0.0002,  # 0.02%
#     0.0003,  # 0.03%
#     0.0004,  # 0.04%
#     0.0005,  # 0.05%
#     0.0006,  # 0.06%
#     0.0007,  # 0.07%
#     0.0008,  # 0.08%
#     0.0009,  # 0.09%
#     0.0010,  # 0.10%  WORKS WELL
#     0.0011,  # 0.11%
#     0.0012,  # 0.12%
#     0.0013,  # 0.13%
#     0.0014,  # 0.14%
#     0.0015,  # 0.15%
#     0.0016,  # 0.16%
#     0.0017,  # 0.17%
#     0.0018,  # 0.18%
#     0.0019,  # 0.19%
#     0.0020,  # 0.20%
# ]
# width_fractions = [
#     0.1
# ]
# results = []
# for interval_numbers in range(1, 200):  # 5 → 100
#     for frac in width_fractions:
#         interval_width = int(domain * frac)

#         # Evita width = 0 per numeri piccoli
#         if interval_width < 1:
#             interval_width = 1

#         for _ in range(10):
#             # --- CALL create_mask ---
#             p, cov = create_mask(
#                 start,
#                 end,
#                 interval_numbers,
#                 interval_width
#             )

#             # --- REGISTRA IL RECORD ---
#             results.append({
#                 "interval_numbers": interval_numbers,
#                 "interval_width_fraction": frac,
#                 "interval_width": interval_width,
#                 "mask_coverage": cov,
#                 "p": p,
#             })

# # -------------------------------
# # SALVA IN CSV
# # -------------------------------

# with open("mask_uniformity_int8_results.csv", "w", newline="") as f:
#     writer = csv.DictWriter(f, fieldnames=results[0].keys())
#     writer.writeheader()
#     writer.writerows(results)

# print("\nSalvato")

# smt_filepath = Path(__file__).parent.parent / "no_axioms.smt2"
# free_vars = extract_free_vars_from_smt2_file(smt_filepath)
# hybrid_state_space_sampler(free_vars, smt_filepath, theta=256, coverage=30)
