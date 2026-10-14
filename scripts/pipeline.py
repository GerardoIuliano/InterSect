import os, argparse, csv
import re, signal
import subprocess
from rich import progress
from pathlib import Path
from utils import extract_invariants,clean_setup, run_mutations_by_heuristic, run_z3, mutation_chain
from utils import remove_useless_line, rename_file_and_clean, remove_unused_axioms, copy_mutants_to_input 
from utils import copy_input_to_Aletheia, move_assert_after_assumptions, prepare_setup_for_mutations, copy_suitable_to_input
from solver_utils import extract_free_vars_from_smt2_file, partitionVariable, get_assignment_for_E, add_e_assignment, create_mask_with_coverage, add_band, run_z3_and_get_model, remove_used_band, prevent_same_model, hybrid_state_space_sampler
from z3 import *
from tqdm import tqdm

# Percorsi principali
BASE_DIR = Path("/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL")
INPUT_DIR = BASE_DIR / "Aletheia" / "input"
MUTATION_DIR = BASE_DIR / "Aletheia" / "aletheia" / "results" / "mutants"
OUTPUT_DIR = BASE_DIR / "output"
# SCRIPT_SWITCH = BASE_DIR / "scripts" / "switch-solc-verisol.sh"
SCRIPT_SWITCH = BASE_DIR / "scripts" / "switch-solc-verisol-github.sh"
DOCKER_IMAGE = "boogie-z3-image"
VERISOL_PATH = "/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL/verisol/bin/Debug/VeriSol.dll"

def extract_pragma(filepath: Path) -> str:
    """Estrae la versione pragma dal contratto Solidity (es. 0.8.0)"""
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            match = re.search(r"pragma\s+solidity\s+([^;]+);", line)
            if match:
                version = match.group(1).strip()
                # Rimuovi caratteri come ^, >=, <=, ~ ecc.
                version = re.sub(r"[^\d\.]", "", version)
                return version
    raise ValueError(f"Nessun pragma trovato in {filepath}")

def run_verisol(sol_file: Path, contract_name: str, cwd=None) -> bool:
    """Esegue VeriSol tramite dotnet"""
    # cmd = ["dotnet", VERISOL_PATH, str(sol_file), contract_name, "/noChk", "/noInlineAttrs", "/useModularArithmetic", "/noPrf", "/stubModel:skip"]
    cmd = ["dotnet", VERISOL_PATH, str(sol_file), contract_name, "/noChk", "/noInlineAttrs", "/useModularArithmetic", "/stubModel:skip"]
    # cmd = ["dotnet", VERISOL_PATH, str(sol_file), contract_name, "/useModularArithmetic", "/stubModel:skip"]
    return run_command(cmd, cwd=cwd)

# def run_command(cmd, cwd=None):
#     """Esegue un comando di sistema con log e gestione errori"""
#     # print(f"\n▶️ Run: {' '.join(cmd)}")
#     try:
#         result = subprocess.run(cmd, cwd=cwd, check=True, timeout=30)  # Timeout di 5 minuti
#         if "Assertion failed" in result.stdout or "Assertion failed" in result.stderr:
#             print(f"⚠️ VeriSol assertion crash")
#             return False
#     except subprocess.TimeoutExpired:
#         print(f"⚠️ Command timed out: {cmd[0]}")
#         return False
#     except subprocess.CalledProcessError as e:
#         # print(f"❌ Error during the execution of: {' '.join(cmd)}")
#         # print(e)
#         return False
#     return True

def run_command(cmd, cwd=None, timeout=30):
    try:
        process = subprocess.Popen(
            cmd, cwd=cwd,
            # stdout=subprocess.PIPE,
            # stderr=subprocess.PIPE,
            start_new_session=True  # ✅ puts process in its own group
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"⚠️ Command timed out: {cmd[0]}")
            # os.killpg(os.getpgid(process.pid), signal.SIGKILL)  # ✅ kills entire process group
            # process.wait()
            return False

        # if "Assertion failed" in (stdout.decode() + stderr.decode()):
        #     print(f"⚠️ VeriSol assertion crash")
        #     return False

        return process.returncode == 0

    except Exception as e:
        print(f"⚠️ Command error: {e}")
        return False
    
def run_pipeline(heuristics: None, rounds: int = 1, identity: bool = False):
    # copy_suitable_to_input(355,400)
    copy_input_to_Aletheia()
    rows = []
    mutations_by_heuristic = {}
    clean_setup()
    #check original invariants of the contract
    #identify invariants in the contract
    # Aletheia path links to input folder

    invariants = extract_invariants()

    mutations_by_heuristic = run_mutations_by_heuristic("identity")
    # -----------------------IDENTITY-----------------------
    output_file = "output.csv"
    # Open file once before the loop
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        
        for invariant in tqdm(invariants):
            file_name = invariant["file_name"]
            invariant_text = invariant["invariant"]
            line_number = invariant["line"]
            function_name = invariant["function"]
            inv_contract_name = invariant["contract"]

            mutations_of_contract = []
            for key, value in mutations_by_heuristic.items():
                if key.startswith(file_name):
                    mutations_of_contract.extend(value)
            
            mutations_to_check = []
            for mutation in mutations_of_contract:
                if(int(mutation["startLine"])==line_number):
                    mutations_to_check.append(mutation["id"])
            
            for mutation in mutations_to_check:
                row = [None] * 7
                row[0] = (file_name)
                row[1] = (inv_contract_name)
                row[2] = (function_name)
                row[3] = (invariant_text)
                row[4] = f"I({row[3]})"
                row[5] = (mutation)
                # row.append(mutation)

                target_suffix = f"-{mutation}.sol"
                sol_file = next(MUTATION_DIR.glob(f"*{target_suffix}"))
                sol_file_name = sol_file.stem

                # # 1️⃣ Extract pragma
                # try:
                #     pragma = extract_pragma(sol_file)
                #     print(f"🔹 Pragma found: {pragma}")
                # except ValueError as e:
                #     print(f"⚠️ {e}")
                #     continue

                # # 2️⃣  Switch solc version for VeriSol 
                # if not run_command(["bash", str(SCRIPT_SWITCH), pragma]):
                #     continue

                # 3️⃣ Run VeriSol to generate .bpl
                contract_name = sol_file.stem
                if(not run_verisol(sol_file, contract_name, cwd=OUTPUT_DIR)):
                    print(f"⚠️ Verisol failed on {sol_file_name}, skip.")
                    row[6] = ("Verisol Failed")
                    writer.writerow(row)
                    f.flush()
                    continue
                
                rename_file_and_clean(OUTPUT_DIR, sol_file_name)
                
                # 4️⃣ Run Boogie on .bpl and Generate VC SMT files for each procedure
                bpl_file = OUTPUT_DIR / f"{sol_file_name}.bpl"
                if not bpl_file.exists():
                    print(f"⚠️ File .bpl does not exist for {contract_name}, skip.")
                    continue

                docker_cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{OUTPUT_DIR}:/workspace",
                    DOCKER_IMAGE,
                    f"{sol_file_name}.bpl",
                    f"/proc:{function_name}_{inv_contract_name}",
                    "/inferModifies",
                    f"/proverLog:{mutation}.smt2"
                ]
                if not run_command(docker_cmd, cwd=OUTPUT_DIR):
                    print(f"⚠️ Boogie failed on {sol_file_name}, skip.")
                    row[6] = ("Boogie Failed")
                    writer.writerow(row)
                    f.flush()
                    continue

                smt_filepath = OUTPUT_DIR / f"{mutation}.smt2"
                if not (smt_filepath).exists():
                    print(f"⚠️ SMT file not found for {mutation}, skip.")
                    row[6] = ("SMT Generation Failed")
                    writer.writerow(row)
                    f.flush()
                    continue
                # remove_unused_axioms(smt_filepath)
                # free_vars = extract_free_vars_from_smt2_file(smt_filepath)

                
                # hybrid_state_space_sampler_manager(free_vars, smt_filepath, theta = 256, coverage = 30)
                # hybrid_state_space_sampler(free_vars, smt_filepath, theta = 256, coverage = 70)

                
                # ################## hybrid state-space sampler  ##################
                # S = set()
                # E = set()
                # L = set()
                # n = 2**256
                # for free_var in free_vars:
                #     partitionVariable(E, L, smt_filepath, free_var, theta=256)
                    
                # if E is not None:
                #     assignments = get_assignment_for_E(E)
                #     if assignments is not None:
                #         for assignment in assignments:
                #             add_e_assignment(assignment, smt_filepath) # Add assignment of brute force variable to SMT formula 
                #             if L is not None:
                #                 for (var, lb, ub) in L:
                #                     bands = create_mask_with_coverage(lb, ub, target_coverage=30) # generate mask
                #                     n = min(n, len(bands)) 
                #                     # add bands to SMT formula
                #                     for (band_lb, band_ub) in bands:
                #                         add_band(band_lb, band_ub, var, smt_filepath)
                #                 for i in range(n):
                #                     status, model = run_z3_and_get_model(smt_filepath)
                #                     print(f"Z3: {model}")
                #                     if status == "UNSAT" or status == "UNKNOWN":
                #                         break
                #                     if status == "SAT":
                #                         # remove bands used by model
                #                         remove_used_band(model, L, smt_filepath) 
                #                         # add blocking clause to prevent same model
                #                         prevent_same_model(model, L, smt_filepath)
                #                         # save model in set S
                #                         S.add(frozenset(model.items()))
                
                # if E is None and L is not None:
                #     for (var, lb, ub) in L:
                #         bands = create_mask_with_coverage(lb, ub, target_coverage=30) # generate mask
                #         n = min(n, len(bands)) 
                #         # add bands to SMT formula
                #         for (band_lb, band_ub) in bands:
                #             add_band(band_lb, band_ub, var, smt_filepath)
                #     for i in range(n):
                #         status, model = run_z3_and_get_model(smt_filepath)
                #         print(f"Z3: {model}")
                #         if status == "UNSAT" or status == "UNKNOWN":
                #             break
                #         if status == "SAT":
                #             # remove bands used by model
                #             remove_used_band(model, L, smt_filepath) 
                #             # add blocking clause to prevent same model
                #             prevent_same_model(model, L, smt_filepath)
                #             # save model in set S
                #             S.add(frozenset(model.items()))  
                # ################## hybrid state-space sampler  ##################


                status, model = run_z3_and_get_model(smt_filepath)
                row[6] = status
                # result_z3 = run_z3(smt_filepath, contracts_dir=OUTPUT_DIR)
                # row[6] = (result_z3)  
                              
                writer.writerow(row)
                f.flush()  # Ensure it's written to disk right away

    mutations_by_heuristic = {}
    if identity:
        return
    
    prepare_setup_for_mutations()
    # -----------------------OTHER HEURISTICS-----------------------
    if rounds == 1:
        mutations_by_heuristic = run_mutations_by_heuristic(heuristics)
    else:
        run_mutations_by_heuristic(heuristics)
        for _ in range(rounds-1):
            # copia le mutazioni generate nell'ultima iterazione nella cartella di input
            copy_mutants_to_input()
            mutations_by_heuristic = run_mutations_by_heuristic(heuristics)

    with open(output_file, "a", newline="") as f:
        writer = csv.writer(f)

        for invariant in invariants:
            file_name = invariant["file_name"]
            invariant_text = invariant["invariant"]
            line_number = invariant["line"]
            function_name = invariant["function"]
            inv_contract_name = invariant["contract"]

            mutations_of_contract = []
            for key, value in mutations_by_heuristic.items():
                if key.startswith(file_name):
                    mutations_of_contract.extend(value)
            
            mutations_to_check = []
            for mutation in mutations_of_contract:
                if(int(mutation["startLine"])==line_number):
                    mutations_to_check.append(mutation["id"])
            
            for mutation in mutations_to_check:
                row = [None] * 7
                row[0] = (file_name)
                row[1] = (inv_contract_name)
                row[2] = (function_name)
                row[3] = (invariant_text)
                row[4] = mutation_chain(mutation, row[3])
                row[5] = (mutation)

                target_suffix = f"-{mutation}.sol"
                sol_file = next(MUTATION_DIR.glob(f"*{target_suffix}"))
                sol_file_name = sol_file.stem

                # # 1️⃣ Extract pragma
                # try:
                #     pragma = extract_pragma(sol_file)
                #     print(f"🔹 Pragma found: {pragma}")
                # except ValueError as e:
                #     print(f"⚠️ {e}")
                #     continue

                # # 2️⃣  Switch solc version for VeriSol 
                # if not run_command(["bash", str(SCRIPT_SWITCH), pragma]):
                #     continue

                # 3️⃣ Run VeriSol to generate .bpl
                contract_name = sol_file.stem
                if(not run_verisol(sol_file, contract_name, cwd=OUTPUT_DIR)):
                    print(f"⚠️ Verisol failed on {sol_file_name}, skip.")
                    row[6] = "Verisol Failed"
                    writer.writerow(row)
                    f.flush()
                    continue

                rename_file_and_clean(OUTPUT_DIR, sol_file_name)
                remove_useless_line(OUTPUT_DIR / f"{sol_file_name}.bpl")

                # 4️⃣ Run Boogie on .bpl and Generate VC SMT files for each procedure
                bpl_file = OUTPUT_DIR / f"{sol_file_name}.bpl"
                if not bpl_file.exists():
                    print(f"⚠️ File .bpl does not exist for {contract_name}, skip.")
                    continue

                docker_cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{OUTPUT_DIR}:/workspace",
                    DOCKER_IMAGE,
                    f"{sol_file_name}.bpl",
                    f"/proc:{function_name}_{inv_contract_name}",
                    "/inferModifies",
                    f"/proverLog:{mutation}.smt2"
                ]
                if not run_command(docker_cmd, cwd=OUTPUT_DIR):
                    row[6] = "Boogie Failed"
                    writer.writerow(row)
                    f.flush()
                    continue

                if not (OUTPUT_DIR / f"{mutation}.smt2").exists():
                    print(f"⚠️ SMT file not found for {mutation}, skip.")
                    row[6] = ("SMT Generation Failed")
                    writer.writerow(row)
                    f.flush()
                    continue

                result_z3 = run_z3(OUTPUT_DIR / f"{mutation}.smt2", contracts_dir=OUTPUT_DIR)
                row[6] = result_z3
                writer.writerow(row)
                f.flush()



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run pipeline with a specific heuristic.")
    parser.add_argument(
        "--heuristics",
        type=str,
        nargs="+",
        help="Heuristics to use: fenceposts, relax, tighten"
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Number of mutation rounds to perform"
    )
    parser.add_argument(
        "--identity",
        type=bool,
        default=False,
        help="Check current invariants without applying mutations"
    )

    args = parser.parse_args()
    run_pipeline(args.heuristics, args.rounds, args.identity)