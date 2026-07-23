#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"

OUTPUT_DIR="${AF3_INPUTGEN_OUTPUT_DIR:-${PWD}/input_jsons}"
JOB_PREFIX="${AF3_INPUTGEN_JOB_PREFIX:-AF3_input}"
MODEL_SEED="${AF3_MODEL_SEED:-1}"
PYTHON_BIN="${AF3_PYTHON_BIN:-}"
CLEAN=0

declare -A CHAIN_INPUTS=()
declare -a CHAIN_IDS=()

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} -A FASTA_INPUT -B MSA_JSON [options]
  ${SCRIPT_NAME} -A MSA_JSON -B FASTA_INPUT [-C MSA_JSON] [options]

Examples:
  ${SCRIPT_NAME} -A ./mpnn_fastas -B B_data.json
  ${SCRIPT_NAME} -A ./mpnn.fasta -B B_data.json
  ${SCRIPT_NAME} -A A_data.json -B ./b_fastas -C C_data.json
  ${SCRIPT_NAME} --chain A ./mpnn_fastas --chain B B_data.json

Chain inputs:
  -A VALUE                  Chain A AF3 MSA data JSON file, FASTA file, or FASTA directory.
  -B VALUE                  Chain B AF3 MSA data JSON file, FASTA file, or FASTA directory.
  -C VALUE                  Chain C AF3 MSA data JSON file, FASTA file, or FASTA directory.
  -D VALUE                  Chain D AF3 MSA data JSON file, FASTA file, or FASTA directory.
      --chain ID VALUE      Generic chain input for additional chain IDs.

Options:
  -o, --o, --output-dir DIR Directory for generated AF3 JSON files. Default: ${OUTPUT_DIR}
      --name-prefix NAME    Job name and filename prefix. Default: ${JOB_PREFIX}
      --seed N              Model seed. Default: ${MODEL_SEED}
      --clean               Remove existing JSON files in output directory before writing.
  -h, --help                Show this help message.

Notes:
  Exactly one chain input must be a FASTA file or FASTA directory.
  FASTA records whose header contains model_name are treated as original/reference records and skipped.
  ProteinMPNN multi-chain sequences separated by / are assigned using designed_chains from the header.
  If generated records omit designed_chains, the most recent skipped model_name header supplies that mapping.

Environment overrides:
  AF3_INPUTGEN_OUTPUT_DIR, AF3_INPUTGEN_JOB_PREFIX, AF3_MODEL_SEED, AF3_PYTHON_BIN
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

add_chain_input() {
  local chain_id="$1"
  local value="$2"

  [[ "${chain_id}" =~ ^[A-Za-z0-9_.-]+$ ]] || die "Invalid chain ID: ${chain_id}"
  [[ -n "${value}" ]] || die "Missing input for chain ${chain_id}"

  if [[ -n "${CHAIN_INPUTS[${chain_id}]+x}" ]]; then
    die "Duplicate input for chain ${chain_id}"
  fi

  CHAIN_IDS+=("${chain_id}")
  CHAIN_INPUTS["${chain_id}"]="${value}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -A|-B|-C|-D)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      add_chain_input "${1#-}" "$2"
      shift 2
      ;;
    --chain)
      [[ $# -ge 3 ]] || die "Usage: --chain ID VALUE"
      add_chain_input "$2" "$3"
      shift 3
      ;;
    -o|--o|--output-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --name-prefix)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      JOB_PREFIX="$2"
      shift 2
      ;;
    --seed)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      MODEL_SEED="$2"
      shift 2
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ "${#CHAIN_IDS[@]}" -gt 0 ]] || die "At least one chain input is required."
[[ "${MODEL_SEED}" =~ ^[0-9]+$ ]] || die "Model seed must be a positive integer."
[[ "${JOB_PREFIX}" =~ ^[A-Za-z0-9_.-]+$ ]] || die "Name prefix may only contain letters, numbers, dots, underscores, and hyphens."

if [[ -n "${PYTHON_BIN}" ]]; then
  "${PYTHON_BIN}" -c "import json" >/dev/null 2>&1 || die "Configured Python is not usable: ${PYTHON_BIN}"
else
  for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c "import json" >/dev/null 2>&1; then
      PYTHON_BIN="${candidate}"
      break
    fi
  done
  [[ -n "${PYTHON_BIN}" ]] || die "No usable python3 or python interpreter was found in PATH."
fi

mkdir -p -- "${OUTPUT_DIR}"
if [[ "${CLEAN}" == "1" ]]; then
  find "${OUTPUT_DIR}" -maxdepth 1 -type f -name '*.json' -delete
fi

chain_args=()
for chain_id in "${CHAIN_IDS[@]}"; do
  chain_args+=(--chain "${chain_id}" "${CHAIN_INPUTS[${chain_id}]}")
done

"${PYTHON_BIN}" - "${OUTPUT_DIR}" "${JOB_PREFIX}" "${MODEL_SEED}" "${chain_args[@]}" <<'PY'
import argparse
import copy
import json
import re
import sys
from pathlib import Path

FIELDS_TO_COPY = ("sequence", "unpairedMsa", "pairedMsa", "templates")
FASTA_EXTENSIONS = {
    ".fa",
    ".faa",
    ".fas",
    ".fasta",
    ".fna",
    ".ffn",
    ".frn",
    ".pep",
}


def fail(message):
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def sanitize_name(value):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    name = name.strip("._-")
    return name or "sequence"


def extract_designed_chains(header):
    match = re.search(r"designed_chains\s*=\s*\[([^\]]*)\]", header or "")
    if not match:
        return []

    content = match.group(1)
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", content)
    if quoted:
        return [item.strip() for item in quoted if item.strip()]

    return [item.strip() for item in content.split(",") if item.strip()]


def is_original_header(header):
    return "model_name" in (header or "").lower()


def is_metadata_line(value):
    lowered = value.strip().lower()
    if not lowered:
        return True
    if lowered.startswith(("#", ";")):
        return True
    return any(
        token in lowered
        for token in (
            "proteinmpnn",
            "fixed_chains",
            "designed_chains",
            "model_name",
            "global_score",
            "score=",
            "sample=",
            "temperature",
            "seq_recovery",
        )
    )


def normalize_sequence_part(value):
    sequence = re.sub(r"\s+", "", value).upper()
    if not sequence:
        fail("FASTA sequence is empty after whitespace removal.")
    if not re.fullmatch(r"[A-Z*]+", sequence):
        fail("FASTA sequences must contain letters or stop markers only.")
    return sequence


def split_sequence(value):
    return [normalize_sequence_part(part) for part in value.split("/")]


def is_sequence_line(value):
    compact = re.sub(r"\s+", "", value).upper()
    return bool(compact) and re.fullmatch(r"[A-Z*]+(?:/[A-Z*]+)*", compact) is not None


def protein_id_values(protein):
    value = protein.get("id")
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def iter_proteins(data):
    for entry in data.get("sequences", []):
        protein = entry.get("protein")
        if isinstance(protein, dict):
            yield protein


def read_msa_protein(path, chain_id):
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        fail(f"MSA input file is empty: {path}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        fail(
            f"MSA input must be an AF3 data JSON file: {path} "
            f"(line {exc.lineno}, column {exc.colno})"
        )

    proteins = list(iter_proteins(data))
    if not proteins:
        fail(f"No protein entries found in MSA input: {path}")

    matches = [protein for protein in proteins if chain_id in protein_id_values(protein)]
    if matches:
        protein = matches[0]
    elif len(proteins) == 1:
        protein = proteins[0]
    else:
        fail(f"Could not find chain {chain_id} in MSA input: {path}")

    cached = {
        key: copy.deepcopy(protein[key])
        for key in FIELDS_TO_COPY
        if key in protein
    }

    if "sequence" not in cached:
        fail(f"MSA input does not contain sequence for chain {chain_id}: {path}")

    if not any(key in cached for key in ("unpairedMsa", "pairedMsa", "templates")):
        fail(f"MSA input does not look like an AF3 data JSON: {path}")

    cached["id"] = chain_id
    return cached


def build_record(header, raw_sequence, fallback_chain, default_designed_chains, path):
    if is_original_header(header):
        chains = extract_designed_chains(header)
        return None, chains or default_designed_chains

    parts = split_sequence(raw_sequence)
    chains = extract_designed_chains(header)
    if chains:
        default_designed_chains = chains
    elif len(parts) > 1:
        chains = default_designed_chains
    else:
        chains = [fallback_chain]

    if len(parts) != len(chains):
        fail(
            f"FASTA record in {path} has {len(parts)} sequence part(s), "
            f"but {len(chains)} designed chain ID(s): {header}"
        )

    return {
        "header": header or path.stem,
        "sequences_by_chain": dict(zip(chains, parts)),
    }, default_designed_chains


def parse_fasta_file(path, fallback_chain):
    records = []
    header = None
    lines = []
    default_designed_chains = []

    def flush_record():
        nonlocal header, lines, default_designed_chains
        if header is None and not lines:
            return
        raw_sequence = "".join(lines)
        if not raw_sequence.strip():
            header = None
            lines = []
            return
        record, default_designed_chains = build_record(
            header or path.stem,
            raw_sequence,
            fallback_chain,
            default_designed_chains,
            path,
        )
        if record is not None:
            records.append(record)
        header = None
        lines = []

    with path.open(encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                flush_record()
                header = line[1:].strip() or path.stem
                lines = []
                continue

            if is_metadata_line(line):
                if lines:
                    flush_record()
                if header is None:
                    header = line
                continue

            if not is_sequence_line(line):
                if header is None and not lines:
                    continue
                fail(f"Unsupported FASTA line in {path}:{line_number}: {line[:120]}")

            if header is None:
                header = path.stem
            lines.append(line)

    flush_record()

    return records


def read_fasta_directory(path, fallback_chain):
    fasta_files = [
        candidate
        for candidate in sorted(path.iterdir())
        if candidate.is_file() and candidate.suffix.lower() in FASTA_EXTENSIONS
    ]
    if not fasta_files:
        fail(f"No FASTA files found in directory: {path}")

    records = []
    for fasta_file in fasta_files:
        parsed = parse_fasta_file(fasta_file, fallback_chain)
        if not parsed:
            fail(f"No generated FASTA records found in: {fasta_file}")
        for record_index, record in enumerate(parsed, start=1):
            header = record["header"]
            record_name = sanitize_name(header.split()[0] if header else fasta_file.stem)
            if len(parsed) > 1:
                label = f"{sanitize_name(fasta_file.stem)}_{record_index:04d}_{record_name}"
            else:
                label = sanitize_name(fasta_file.stem or record_name)
            records.append(
                {
                    "source": str(fasta_file),
                    "header": header,
                    "label": label,
                    "sequences_by_chain": record["sequences_by_chain"],
                }
            )

    return records


def read_fasta_input(path, fallback_chain):
    if path.is_dir():
        return read_fasta_directory(path, fallback_chain)

    if path.is_file() and path.suffix.lower() in FASTA_EXTENSIONS:
        parsed = parse_fasta_file(path, fallback_chain)
        if not parsed:
            fail(f"No generated FASTA records found in: {path}")

        records = []
        for record_index, record in enumerate(parsed, start=1):
            header = record["header"]
            record_name = sanitize_name(header.split()[0] if header else path.stem)
            if len(parsed) > 1:
                label = f"{sanitize_name(path.stem)}_{record_index:04d}_{record_name}"
            else:
                label = sanitize_name(path.stem or record_name)
            records.append(
                {
                    "source": str(path),
                    "header": header,
                    "label": label,
                    "sequences_by_chain": record["sequences_by_chain"],
                }
            )

        return records

    return None


def append_unique(values, value):
    if value not in values:
        values.append(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir")
    parser.add_argument("job_prefix")
    parser.add_argument("model_seed", type=int)
    parser.add_argument("--chain", nargs=2, action="append", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    static_proteins = {}
    static_chain_order = []
    fasta_chain = None
    fasta_records = None

    for chain_id, raw_value in args.chain:
        path = Path(raw_value).expanduser()
        fasta_input_records = read_fasta_input(path, chain_id)
        if fasta_input_records is not None:
            if fasta_chain is not None:
                fail("Only one FASTA file or FASTA directory chain input is supported.")
            fasta_chain = chain_id
            fasta_records = fasta_input_records
            continue

        if not path.is_file():
            fail(f"Input for chain {chain_id} is not a file or directory: {raw_value}")

        static_proteins[chain_id] = read_msa_protein(path, chain_id)
        append_unique(static_chain_order, chain_id)
        print(f"Chain {chain_id}: cached MSA from {path}", file=sys.stderr)

    if fasta_chain is None or fasta_records is None:
        fail("Exactly one chain input must be a FASTA file or FASTA directory.")

    written = 0
    seen_names = set()
    requested_chain_order = [chain_id for chain_id, _ in args.chain]

    for index, record in enumerate(fasta_records, start=1):
        name_base = sanitize_name(f"{args.job_prefix}_{record['label']}")
        if name_base in seen_names:
            name_base = f"{name_base}_{index:04d}"
        seen_names.add(name_base)

        proteins_by_chain = {
            chain_id: copy.deepcopy(protein)
            for chain_id, protein in static_proteins.items()
        }
        for chain_id, sequence in record["sequences_by_chain"].items():
            proteins_by_chain[chain_id] = {
                "id": chain_id,
                "sequence": sequence,
            }

        output_chain_order = []
        for chain_id in requested_chain_order:
            if chain_id in proteins_by_chain:
                append_unique(output_chain_order, chain_id)
        for chain_id in record["sequences_by_chain"]:
            append_unique(output_chain_order, chain_id)
        for chain_id in static_chain_order:
            append_unique(output_chain_order, chain_id)

        payload = {
            "name": name_base,
            "modelSeeds": [args.model_seed],
            "sequences": [
                {"protein": proteins_by_chain[chain_id]}
                for chain_id in output_chain_order
            ],
            "dialect": "alphafold3",
            "version": 4,
        }

        output_path = output_dir / f"{name_base}.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

        written += 1

    print(
        f"Wrote {written} AF3 JSON file(s) to {output_dir}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
PY
