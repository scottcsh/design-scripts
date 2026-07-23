#!/usr/bin/env bash
set -euo pipefail

module load miniforge3/25.3.0-3
conda activate rfd3

SCRIPT_NAME="$(basename "$0")"

MPNN_CKPT=/home/hpclab/.foundry/checkpoints/proteinmpnn_v_48_020.pt

IN_DIR=""
OUT_DIR="mpnn_out"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
MODEL_TYPE="${MODEL_TYPE:-protein_mpnn}"
IS_LEGACY_WEIGHTS="${IS_LEGACY_WEIGHTS:-True}"
DESIGNED_CHAINS="${DESIGNED_CHAINS:-A}"
BATCH_SIZE="${BATCH_SIZE:-4}"
N_BATCHES="${N_BATCHES:-1}"
TEMPERATURE="${TEMPERATURE:-0.2}"
WRITE_FASTA="${WRITE_FASTA:-True}"
WRITE_STRUCTURES="${WRITE_STRUCTURES:-False}"
JOBS="${JOBS:-16}"

usage() {
    cat <<EOF
Usage:
  ${SCRIPT_NAME} -i INPUT_DIR [-o OUTPUT_DIR]

Required:
  -i, --input-dir DIR      Directory containing input CIF, CIF.GZ, PDB, or PDB.GZ files.

Options:
  -o, --output-dir DIR     Output directory. Default: ${OUT_DIR}
  -gpu VALUE               CUDA_VISIBLE_DEVICES value. Default: ${CUDA_VISIBLE_DEVICES}
  -chain VALUE             Designed chain ID. Default: ${DESIGNED_CHAINS}
  -bs N                    Batch size. Default: ${BATCH_SIZE}
  -nb N                    Number of batches. Default: ${N_BATCHES}
  -t VALUE                 Sampling temperature. Default: ${TEMPERATURE}
  -jobs N                  Number of concurrent MPNN processes. Default: ${JOBS}
  -wf VALUE                Write FASTA output. Default: ${WRITE_FASTA}
  -ws VALUE                Write structure output. Default: ${WRITE_STRUCTURES}
  -h, --help               Show this help message.

Environment overrides:
  CUDA_VISIBLE_DEVICES, MODEL_TYPE, IS_LEGACY_WEIGHTS, DESIGNED_CHAINS,
  BATCH_SIZE, N_BATCHES, TEMPERATURE, WRITE_FASTA, WRITE_STRUCTURES, JOBS
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        -i|--input-dir|--i)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            IN_DIR="$2"
            shift 2
            ;;
        -o|--output-dir|--o)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            OUT_DIR="$2"
            shift 2
            ;;
        -gpu|--gpu|--cuda-visible-devices)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            CUDA_VISIBLE_DEVICES="$2"
            shift 2
            ;;
        -chain|--chain|--designed-chains)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            DESIGNED_CHAINS="$2"
            shift 2
            ;;
        -bs|--batch-size)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            BATCH_SIZE="$2"
            shift 2
            ;;
        -nb|--number-of-batches|--n-batches)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            N_BATCHES="$2"
            shift 2
            ;;
        -t|--temperature)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            TEMPERATURE="$2"
            shift 2
            ;;
        -jobs|-j|--jobs)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            JOBS="$2"
            shift 2
            ;;
        -wf|--write-fasta)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            WRITE_FASTA="$2"
            shift 2
            ;;
        -ws|--write-structures)
            [[ $# -ge 2 ]] || die "Missing value for $1"
            WRITE_STRUCTURES="$2"
            shift 2
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

[[ -n "$IN_DIR" ]] || die "-i INPUT_DIR is required."
[[ -d "$IN_DIR" ]] || die "Input directory not found: $IN_DIR"

: "${MPNN_CKPT:?Set MPNN_CKPT=/path/to/proteinmpnn_v_48_020.pt first}"
export CUDA_VISIBLE_DEVICES

mkdir -p "$OUT_DIR/logs"

run_one() {
    cif="$1"

    rel="${cif#$IN_DIR/}"
    stem="${rel%.cif.gz}"
    stem="${stem%.cif}"
    stem="${stem%.pdb.gz}"
    stem="${stem%.pdb}"
    safe_name="$(echo "$stem" | sed 's#[^A-Za-z0-9_.-]#_#g')"

    job_out="$OUT_DIR/$safe_name"
    log_file="$OUT_DIR/logs/${safe_name}.log"
    chain_fasta="$job_out/${safe_name}_chain${DESIGNED_CHAINS}_only.fasta"

    mkdir -p "$job_out"

    if [[ -s "$chain_fasta" ]]; then
        echo "[SKIP] $rel"
        return 0
    fi

    echo "[RUN] $rel"

    mpnn \
      --model_type "$MODEL_TYPE" \
      --checkpoint_path "$MPNN_CKPT" \
      --is_legacy_weights "$IS_LEGACY_WEIGHTS" \
      --structure_path "$cif" \
      --out_directory "$job_out" \
      --name "$safe_name" \
      --write_fasta "$WRITE_FASTA" \
      --write_structures "$WRITE_STRUCTURES" \
      --designed_chains "$DESIGNED_CHAINS" \
      --batch_size "$BATCH_SIZE" \
      --number_of_batches "$N_BATCHES" \
      --temperature "$TEMPERATURE" \
      > "$log_file" 2>&1 || {
          echo "[FAIL] MPNN failed: $rel"
          echo "  log: $log_file"
          tail -30 "$log_file"
          return 1
      }

    # Extract only the designed chain sequence from Foundry MPNN concat FASTA.
    python - "$cif" "$job_out" "$DESIGNED_CHAINS" "$chain_fasta" "$safe_name" >> "$log_file" 2>&1 <<'PY'
import sys
from pathlib import Path

try:
    import gemmi
except ImportError:
    raise SystemExit(
        "Python package 'gemmi' is required. Install once with:\n"
        "  python -m pip install gemmi"
    )

structure_path = Path(sys.argv[1])
fasta_dir = Path(sys.argv[2])
chain_id = sys.argv[3]
out_fasta = Path(sys.argv[4])
safe_name = sys.argv[5]

if "," in chain_id:
    raise SystemExit(
        f"Only one DESIGNED_CHAINS value is supported for extraction, got: {chain_id}"
    )

AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M",
}

def get_chain_lengths(path):
    st = gemmi.read_structure(str(path))
    if len(st) == 0:
        raise ValueError(f"No model found in {path}")

    model = st[0]
    chain_info = []

    for chain in model:
        seq = []
        for res in chain:
            aa = AA3_TO_1.get(res.name.upper())
            if aa is not None:
                seq.append(aa)
        if seq:
            chain_info.append((chain.name, len(seq), "".join(seq)))

    if not chain_info:
        raise ValueError(f"No protein chains found in {path}")

    return chain_info

def read_fasta_records(path):
    records = []
    header = None
    seq_lines = []

    with open(path, "r") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    records.append((header, "".join(seq_lines)))
                header = line[1:]
                seq_lines = []
            else:
                seq_lines.append(line)

    if header is not None:
        records.append((header, "".join(seq_lines)))

    return records

def write_record(handle, header, seq, width=80):
    handle.write(f">{header}\n")
    for i in range(0, len(seq), width):
        handle.write(seq[i:i + width] + "\n")

chain_info = get_chain_lengths(structure_path)
chain_order = [cid for cid, _, _ in chain_info]

if chain_id not in chain_order:
    raise SystemExit(
        f"Chain {chain_id} not found in {structure_path}. "
        f"Available chains: {chain_order}"
    )

offset = 0
selected_len = None
for cid, length, _seq in chain_info:
    if cid == chain_id:
        selected_len = length
        break
    offset += length

total_len = sum(length for _, length, _seq in chain_info)

# Avoid re-reading already extracted chain-only FASTA if rerun happened inside same folder.
fasta_files = sorted(
    fp for fp in list(fasta_dir.rglob("*.fa")) + list(fasta_dir.rglob("*.fasta"))
    if fp.resolve() != out_fasta.resolve()
)

if not fasta_files:
    raise SystemExit(f"No FASTA files found under {fasta_dir}")

n_written = 0
out_fasta.parent.mkdir(parents=True, exist_ok=True)

with open(out_fasta, "w") as out:
    for fp in fasta_files:
        for header, seq in read_fasta_records(fp):
            clean_seq = (
                seq.replace("/", "")
                   .replace(":", "")
                   .replace(" ", "")
                   .replace("\t", "")
            )

            if len(clean_seq) == total_len:
                chain_seq = clean_seq[offset:offset + selected_len]
            elif len(clean_seq) == selected_len:
                chain_seq = clean_seq
            else:
                print(
                    f"[WARN] Skip length-mismatched FASTA record: {fp} | {header} | "
                    f"len={len(clean_seq)}, expected total={total_len} or chain={selected_len}",
                    file=sys.stderr,
                )
                continue

            write_record(
                out,
                f"{safe_name}|chain_{chain_id}|sample_{n_written}|{header}",
                chain_seq,
            )
            n_written += 1

if n_written == 0:
    raise SystemExit(
        f"No chain-{chain_id} sequences extracted. "
        f"Check FASTA format and chain lengths."
    )

print(f"[OK] wrote {n_written} chain-{chain_id} sequences -> {out_fasta}")
print("[INFO] chain lengths:", ", ".join(f"{cid}:{length}" for cid, length, _ in chain_info))
PY

    if [[ ! -s "$chain_fasta" ]]; then
        echo "[FAIL] Chain-only FASTA was not created: $chain_fasta"
        return 1
    fi

    echo "[OK] $rel -> $chain_fasta"
}

export -f run_one
export IN_DIR OUT_DIR MPNN_CKPT MODEL_TYPE IS_LEGACY_WEIGHTS DESIGNED_CHAINS
export BATCH_SIZE N_BATCHES TEMPERATURE WRITE_FASTA WRITE_STRUCTURES

if ! find "$IN_DIR" -type f \( -name "*.cif.gz" -o -name "*.cif" -o -name "*.pdb.gz" -o -name "*.pdb" \) -print -quit | grep -q .; then
    die "No input CIF, CIF.GZ, PDB, or PDB.GZ files found under: $IN_DIR"
fi

find "$IN_DIR" -type f \( -name "*.cif.gz" -o -name "*.cif" -o -name "*.pdb.gz" -o -name "*.pdb" \) -print0 \
  | sort -z \
  | xargs -0 -r -n 1 -P "$JOBS" bash -c 'run_one "$0"'

# Collect all extracted designed-chain FASTA records into one file.
COMBINED_FASTA="$OUT_DIR/all_chain${DESIGNED_CHAINS}_only.fasta"
find "$OUT_DIR" -type f -name "*_chain${DESIGNED_CHAINS}_only.fasta" -print0 \
  | sort -z \
  | xargs -0 -r cat > "$COMBINED_FASTA"

echo "[DONE] outputs in $OUT_DIR"
echo "[DONE] combined FASTA: $COMBINED_FASTA"
