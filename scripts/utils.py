# apri file mutations.json al path: Aletheia/aletheia/results/mutations.json
import json, os, subprocess, re, shutil, random
from collections import Counter
from pathlib import Path
from heuristics import HEURISTICS

DOCKER_IMAGE = "boogie-z3-image"

def move_assert_after_assumptions(file_path, procedure):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    impl_start_re = re.compile(rf"\s*implementation\s+{re.escape(procedure)}\b")
    out_lines = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if impl_start_re.match(line):
            out_lines.append(line)
            i += 1

            pre_block = []
            while i < n and '{' not in lines[i]:
                pre_block.append(lines[i])
                i += 1

            if i >= n:
                out_lines.extend(pre_block)
                break

            open_brace_line = lines[i]
            i += 1  # consume the line with '{'

            body_lines = []
            brace_depth = 1  # we have already seen the opening {
            while i < n and brace_depth > 0:
                l = lines[i]

                brace_depth += l.count('{')
                brace_depth -= l.count('}')
                body_lines.append(l)
                i += 1

            closing_idx = len(body_lines) - 1
            inner_lines = body_lines[:closing_idx]
            closing_lines = [body_lines[closing_idx]]

            assumes = []
            asserts = []
            others = []
            for inner in inner_lines:
                stripped = inner.lstrip()
                if stripped.startswith("assume"):
                    assumes.append(inner)
                elif stripped.startswith("assert"):
                    asserts.append(inner)
                else:
                    others.append(inner)

            out_lines.extend(pre_block)
            out_lines.append(open_brace_line)
            out_lines.extend(assumes)
            out_lines.extend(asserts)
            out_lines.extend(others)
            out_lines.extend(closing_lines)

            continue
        else:
            out_lines.append(line)
            i += 1

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(out_lines)

def remove_unused_axioms(file):
    """Migliora il file SMT2 rimuovendo righe inutili."""
    temp_file = str(file) + ".tmp"
    flag = False
    with open(file, "r", encoding="utf-8") as infile, open(temp_file, "w", encoding="utf-8") as outfile:
        lines = list(infile)
        flag = False
        i = 0
        while i < len(lines):
            line = lines[i]

            if flag:
                if "(get-info :rlimit)" in line or "(get-info :reason-unknown)" in line:
                    i += 1
                    continue

                if "(check-sat)" in line:
                    outfile.write(line)
                    # outfile.write("(get-model)\n")
                else:
                    outfile.write(line)

                if "(pop 1)" in line:
                    flag = False
                    # scrivi anche la riga successiva solo se esiste
                    if i + 1 < len(lines):
                        outfile.write(lines[i + 1])
                        i += 1  # evita di riscriverla

            else:
                if "(push 1)" in line:
                    flag = True
                    outfile.write(line)

            i += 1
    os.replace(temp_file, file)

def remove_useless_line(file):
    temp_file = str(file) + ".tmp"
    with open(file, "r", encoding="utf-8") as infile, open(temp_file, "w", encoding="utf-8") as outfile:
        for line in infile:
            # Controlla se la riga inizia con "call  {:cexpr" rimuovila
            if line.startswith("call  {:cexpr"):
                continue
            # Controlla se la riga inizia con "assert {:first}" rimuovila
            if line.startswith("assert {:first}"):
                continue
            # Altrimenti scrivi la riga nel file temporaneo se non è vuota
            outfile.write(line)
    os.replace(temp_file, file)

def rename_file_and_clean(CONTRACTS_DIR, new_file_name):
    print(f"\n▶️ Rinomino file di output di VeriSol in {new_file_name}.bpl e pulisco i file temporanei")
    """Rinomina i file di output di VeriSol e pulisce i file temporanei"""
    file_name = CONTRACTS_DIR / "__SolToBoogieTest_out.bpl"
    if file_name.exists():
        new_name = CONTRACTS_DIR / f"{file_name.stem.replace('__SolToBoogieTest_out', new_file_name)}.bpl"
        file_name.rename(new_name)
    #elimina i file temporanei
    temp_files = [
        CONTRACTS_DIR / "boogie.txt",
        CONTRACTS_DIR / "corral.txt",
    ]
    for temp_file in temp_files:
        if temp_file.exists():
            temp_file.unlink()

def run_command(command, cwd=None):
    """Esegue un comando di shell e gestisce eventuali errori."""
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
        # print(f"Comando eseguito con successo: {command}")
        # print(f"Output: {result.stdout.decode('utf-8')}")
        return True
    except subprocess.CalledProcessError as e:
        # print(f"Errore nell'esecuzione del comando '{command}': {e}")
        # print(f"Errore dettagliato: {e.stderr.decode('utf-8')}")
        # print(e)
        return False

def switch_solc(cmd, cwd=None):
    """Esegue un comando di sistema con log e gestione errori"""
    print(f"\n▶️ Eseguo: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Errore durante l'esecuzione di: {' '.join(cmd)}")
        print(e)
        return False
    return True

def run_docker(cmd):
    """Esegue un comando di sistema con log e gestione errori"""
    print(f"\n▶️ Eseguo: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=None, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Errore durante l'esecuzione di: {' '.join(cmd)}")
        print(e)
        return False
    return True

def run_FI_operator(contracts_dir="./Aletheia/"):
    """Genera mutazioni utilizzando Sumo."""
    # os.chdir(contracts_dir)
    run_command('npx aletheia disable && npx aletheia enable '+ "FI" +' && npx aletheia mutate', cwd=Path(__file__).parent.parent / "Aletheia")

def run_Aletheia_operators(contracts_dir="./Aletheia/", operators=[]):
    """Genera mutazioni utilizzando Sumo."""
    # os.chdir(contracts_dir) 
    #concatena gli operatori in una stringa separata da spazi
    operators_str = ' '.join(operators)
    run_command('npx aletheia disable && npx aletheia enable '+ operators_str +' && npx aletheia mutate', cwd=Path(__file__).parent.parent / "Aletheia")

def run_z3(filepath, contracts_dir="./output/"):
    """solv smt2 file.""" 
    try:   
        result = subprocess.run('z3 -smt2 -file:'+ str(filepath), shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=contracts_dir)
        return result.stdout.decode().strip()
        if not result:
            print("Error during execution of Z3")
        else:
            output = result.stdout.decode().strip()
            if output == "sat":
                return "SAT"
            elif output == "unsat" or "unsat" in output:
            # elif output == "unsat":
                return "UNSAT"
            else:
                print("Unknown:", output)
                return "UNKNOWN"
    except subprocess.CalledProcessError as e:
        print(f"Errore nell'esecuzione del comando 'z3 -smt2 -file{str(filepath)}': {e}")
        print(f"Errore dettagliato: {e.stderr.decode('utf-8')}")
        print(e)
        return "ERROR"

def extract_invariants(json_file=Path(__file__).parent.parent / "Aletheia/aletheia/results/mutations.json"):
    run_FI_operator()
    with open(json_file, 'r') as file:
        data = json.load(file)
    
    invariants = []
    for file_name, mutations in data.items():  # 🔹 iteri su chiave + lista
        for mutation in mutations:             # 🔹 iteri dentro la lista
            if mutation.get("operator") == "FI":
                struct = {
                    "file_name": file_name,
                    "invariant": mutation.get("original"),
                    "line": mutation.get("startLine"),
                    "function": mutation.get("functionName"),
                    "contract": mutation.get("replace")
                }
                invariants.append(struct)
    clean_mutants_folder()
    return invariants

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

def run_verisol(sol_file: Path, contract_name: str, CONTRACTS_DIR) -> bool:
    """Esegue VeriSol tramite dotnet"""
    cmd = ["dotnet", "./verisol/bin/Debug/VeriSol.dll", str(sol_file), contract_name, "/noChk", "/noInlineAttrs", "/noPrf", "/stubModel:skip"]
    return run_command(cmd, cwd=CONTRACTS_DIR)

def clean_mutants_folder():
    mutants_folder_path = Path(__file__).parent.parent / "Aletheia" / "aletheia" / "results" / "mutants"
   
    if not mutants_folder_path.exists():
        return  # se non esiste, niente da cancellare
    for item in mutants_folder_path.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()   # cancella file o symlink
        elif item.is_dir():
            shutil.rmtree(item)  # cancella cartella ricorsivamente
    
def clean_setup():
    aletheia_path = Path(__file__).parent.parent / "Aletheia" / "aletheia" 
    input_path = Path(__file__).parent.parent / "Aletheia" / "input" 
    output_path = Path(__file__).parent.parent / "output"

    if not aletheia_path.exists():
        return  # se non esiste, niente da cancellare
    for item in aletheia_path.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()   # cancella file o symlink
        elif item.is_dir():
            shutil.rmtree(item)  # cancella cartella ricorsivamente
    
    if not output_path.exists():
        return  # se non esiste, niente da cancellare
    for item in output_path.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()   # cancella file o symlink
        elif item.is_dir():
            shutil.rmtree(item)  # cancella cartella ricorsivamente

    if not input_path.exists():
        return  # se non esiste, niente da cancellare
    for item in input_path.iterdir():
        if item.is_file() and "-" in item.name:
            item.unlink()   # cancella file o symlink
        
def prepare_setup_for_mutations():
    # rimuovo le mutazione effettuate per il check delle invarianti originali
    mutants_path = Path(__file__).parent.parent / "Aletheia" / "aletheia" / "results" / "mutants"
    if not mutants_path.exists():
        return  # se non esiste, niente da cancellare
    for item in mutants_path.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()   # cancella file o symlink
        elif item.is_dir():
            shutil.rmtree(item)  # cancella cartella ricorsivamente

    input_path = Path(__file__).parent.parent / "Aletheia" / "input" 
    if not input_path.exists():
        return  # se non esiste, niente da cancellare
    for item in input_path.iterdir():
        if item.is_file() and "-" in item.name:
            item.unlink()   # cancella file o symlink

    # rinomino il file json per non perdere info sulle invarianti originali
    mutation_output_path = Path(__file__).parent.parent / "output" / "mutations.json"
    # Nuovo nome (esempio)
    new_path = mutation_output_path.with_name("original.json")
    # Copia del file
    shutil.copy2(mutation_output_path, new_path)
    # Elimina l'originale
    mutation_output_path.unlink()

def copy_mutants_to_input():
    mutants_path = Path(__file__).parent.parent / "Aletheia" / "aletheia" / "results" / "mutants"
    input_path = Path(__file__).parent.parent / "Aletheia" / "input"
    # rimuovi i file di input sui quali ho runnato il primo round di mutazioni 
    # i modo da non ripetere le mutazioni su di essi
    if not input_path.exists():
        return  # se non esiste, niente da cancellare
    for item in input_path.iterdir():
        if item.is_file():
            item.unlink()   

    for mutant_file in mutants_path.glob("*.sol"):
        destination_file = input_path / mutant_file.name
        with open(mutant_file, 'r') as src_file, open(destination_file, 'w') as dest_file:
            dest_file.write(src_file.read())

def copy_input_to_Aletheia():
    input_path = Path(__file__).parent.parent / "input"
    input_Aletheia_path = Path(__file__).parent.parent / "Aletheia" / "input"
    # pulisci la cartella Aletheia/input
    if input_Aletheia_path.exists():
        for item in input_Aletheia_path.iterdir():
            if item.is_file():
                item.unlink()   # cancella file o symlink
    
    for input_file in input_path.glob("*.sol"):
        destination_file = input_Aletheia_path / input_file.name
        with open(input_file, 'r') as src_file, open(destination_file, 'w') as dest_file:
            dest_file.write(src_file.read())

def copy_suitable_to_input(start, end):
    suitable_path = Path(__file__).parent.parent / "datasets" / "suitable_sc"
    input_path = Path(__file__).parent.parent / "input"

    if input_path.exists():
        for item in input_path.iterdir():
            if item.is_file():
                item.unlink()

    sorted_files = sorted(suitable_path.glob("*.sol"), key=lambda f: f.name)
    selected_files = sorted_files[start:end]

    for input_file in selected_files:
        destination_file = input_path / input_file.name
        with open(input_file, 'r') as src_file, open(destination_file, 'w') as dest_file:
            dest_file.write(src_file.read())


def run_mutations_by_heuristic(heuristic):
    # se euristic è una lista, per ogni elemento della lista trova le euristiche e le concatena
    if isinstance(heuristic, list):
        operators = []
        for e in heuristic:
            if e not in HEURISTICS:
                raise KeyError(f"Heuristic '{e}' not found")
            operators.extend(HEURISTICS.get(e, []))
    elif isinstance(heuristic, str):
        if heuristic not in HEURISTICS:
            raise KeyError(f"Heuristic '{e}' not found")
        operators = HEURISTICS.get(heuristic, [])
    
    run_Aletheia_operators(operators=operators)

    mutations_path = Path(__file__).parent.parent / "Aletheia" / "aletheia" / "results" / "mutations.json"
    output_path = Path(__file__).parent.parent / "output" / "mutations.json"

    # Leggi il JSON appena generato
    with open(mutations_path, 'r') as file:
        new_data = json.load(file)

    # Se il file output/mutations.json esiste, leggilo e fai il merge
    if output_path.exists():
        with open(output_path, 'r') as file:
            existing_data = json.load(file)

        # Merge dei due JSON (concatenando le liste per chiavi comuni)
        merged_data = existing_data.copy()
        for key, value in new_data.items():
            if key in merged_data:
                merged_data[key].extend(value)
            else:
                merged_data[key] = value
    else:
        merged_data = new_data

    # Salva sempre in output/mutations.json
    output_path.parent.mkdir(parents=True, exist_ok=True)  # assicurati che la cartella output esista
    with open(output_path, 'w') as file:
        json.dump(merged_data, file, indent=4)

    return merged_data

def extract_last_mutation_id(path):
    path = str(path)
    ids = re.findall(r"m[0-9a-fA-F]{8}", path)
    return ids[-1] if ids else None

def mutation_chain(id, inv):
    path_mutations = Path(__file__).parent.parent / "output" / "mutations.json"
    with open(path_mutations, 'r') as file:
        data = json.load(file)
    # passo base
    for file_name, mutations in data.items():  # 🔹 iteri su chiave + list
        for mutation in mutations:             # 🔹 iteri dentro la lista
            if mutation.get("id") == id and not("-" in Path(mutation.get("file")).stem):
                return mutation.get("operator")+"("+inv+")"
    # ricorsione
    for file_name, mutations in data.items():  # 🔹 iteri su chiave + list
        for mutation in mutations:             # 🔹 iteri dentro la lista
            if mutation.get("id") == id and ("-" in Path(mutation.get("file")).stem):
                parent_id = extract_last_mutation_id(Path(mutation.get("file")).stem)
                parent_chain = mutation_chain(parent_id, inv)
                return mutation.get("operator")+"("+parent_chain+")"


# # ---------------------------- Masking and Entropy Calculation ----------------------------
# def create_mask(interval_width, num_intervals, min_range, max_range):
#     domain_size = max_range - min_range + 1
#     masked = set()
#     for _ in range(num_intervals):
#         # choose a random start point in the domain
#         start = random.randint(min_range, max_range)
#         interval = set()
#         for i in range(interval_width):
#             # wrap modulo domain size
#             val = min_range + ((start - min_range + i) % domain_size)
#             interval.add(val)
#         masked |= interval  # union
#     return masked

# def compute_unmasked(mask, min_range, max_range):
#     domain = set(range(min_range, max_range + 1))
#     unmasked = domain - mask
#     return unmasked

# def unmasked_bag(unmasked_list):
#     bag = Counter()
#     for unmasked in unmasked_list:
#         bag.update(unmasked)
#     # print("Unmasked Bag:", sum(1 for v in bag.values() if v > 1))
#     return bag

# def relative_entropy_of_bag(bag: Counter, min_range: int, max_range: int):
#     domain_size = max_range - min_range + 1
#     if domain_size <= 0:
#         raise ValueError("Invalid domain range")

#     max_entropy = math.log2(domain_size)

#     total = sum(bag.values())
#     if total == 0:
#         bag_entropy = 0.0
#     else:
#         bag_entropy = 0.0
#         for val in range(min_range, max_range + 1):
#             count = bag.get(val, 0)
#             if count > 0:
#                 p = count / total
#                 bag_entropy -= p * math.log2(p)
#     return bag_entropy, max_entropy, bag_entropy / max_entropy

# def relative_entropy_of_bag_respect_masked(bag: Counter):
#     """
#     Compute the entropy of the bag, normalized with respect to the maximum entropy 
#     achievable using only the values actually present in the bag.
#     """
#     total = sum(bag.values())
#     if total == 0:
#         return 0.0, 0.0, 0.0

#     # values actually present in the bag
#     masked_values = list(bag.keys())
#     k = len(masked_values)  # ampiezza del dominio mascherato
#     if k == 1:
#         return 0.0, 0.0, 1.0  # define relative entropy as 1.0 when only one value is present

#     # max entropy possible with k masked values
#     max_entropy = math.log2(k)

#     # Entropy
#     bag_entropy = 0.0
#     for v in masked_values:
#         p = bag[v] / total
#         bag_entropy -= p * math.log2(p)

#     return bag_entropy, max_entropy, bag_entropy / max_entropy

# unmaskedList = []
# uint_min = 0
# uint_max = 2**8 - 1
# interval_width = 10
# num_intervals = 5
# num_masks = 2

# for _ in range(num_masks):
#     mask = create_mask(interval_width, num_intervals, uint_min, uint_max)
#     unmasked = compute_unmasked(mask, uint_min, uint_max)
#     unmaskedList.append(unmasked)
# bag = unmasked_bag(unmaskedList)
# # print("Relative Entropy (acceptable >= 0.80):", relative_entropy_of_bag(bag, 0, 255))
# print("EXAMPLE 1")
# entropy, max_entropy, rel_entropy = relative_entropy_of_bag(bag, uint_min, uint_max)
# print("Relative Entropy respect masked (acceptable >= 0.80). \nEntropy: {:.4f}, \nMax Entropy: {:.4f}, \nRelative Entropy: {:.2f}".format(entropy, max_entropy, rel_entropy))



# # ---------------------------- Optimized Masking and Entropy Calculation ----------------------------

# import random
# from collections import Counter
# import math

# # --------------------------
# # 1️⃣ Create mask
# # --------------------------
# def create_mask(interval_width, num_intervals, min_range, max_range):
#     """Crea una maschera rappresentata come lista di intervalli (start, width)."""
#     domain_size = max_range - min_range + 1
#     intervals = []
#     for _ in range(num_intervals):
#         start = random.randint(min_range, max_range)
#         intervals.append((start, interval_width))
#     return intervals

# # --------------------------
# # 2️⃣ Unmasking check
# # --------------------------
# def is_masked(x, intervals, min_range, max_range):
#     """Ritorna True se x è dentro uno degli intervalli della maschera."""
#     domain_size = max_range - min_range + 1
#     relative = x - min_range
#     for start, width in intervals:
#         offset = (relative - (start - min_range)) % domain_size
#         if offset < width:
#             return True
#     return False

# # --------------------------
# # 3️⃣ Unmasked Bag using sampling
# # --------------------------
# def unmasked_bag(intervals_list, min_range, max_range, samples):
#     """Genera un bag dei valori non mascherati campionati."""
#     bag = Counter()
#     domain_size = max_range - min_range + 1
    
#     for _ in range(samples):
#         x = random.randrange(domain_size) + min_range
        
#         # controlla se non è mascherato da tutte le maschere
#         is_unmasked = all(not is_masked(x, intervals, min_range, max_range) for intervals in intervals_list)
#         if is_unmasked:
#             bag[x] += 1
#     return bag

# # --------------------------
# # 4️⃣ Entropia relativa
# # --------------------------
# def relative_entropy_of_bag(bag: Counter):
#     """Calcola entropia della bag rispetto al massimo teorico per il campione."""
#     total = sum(bag.values())
#     if total == 0:
#         return 0.0, 0.0, 0.0
        
#     max_entropy = math.log2(total)

#     entropy = 0.0
#     for count in bag.values():
#         p = count / total
#         entropy -= p * math.log2(p)
        
#     return entropy, max_entropy, entropy / max_entropy

# # --------------------------
# # 5️⃣ Example
# # --------------------------
# unmaskedList = []
# uint_min = 0
# uint_max = 2**256 - 1  #uint256
# interval_width = 100000
# num_intervals = 50
# num_masks = 5
# samples = 100000

# # genera maschere
# for _ in range(num_masks):
#     mask = create_mask(interval_width, num_intervals, uint_min, uint_max)
#     unmaskedList.append(mask)

# # calcola bag dei non mascherati tramite sampling
# bag = unmasked_bag(unmaskedList, uint_min, uint_max, samples=samples)

# # calcola entropia
# entropy, max_entropy, rel_entropy = relative_entropy_of_bag(bag)
# print("\n-------\nEXAMPLE 2")
# print("Relative Entropy respect masked (acceptable >= 0.80). \nEntropy: {:.4f}, \nMax Entropy: {:.4f}, \nRelative Entropy: {:.2f}".format(entropy, max_entropy, rel_entropy))



