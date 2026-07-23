#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"

IMAGE="${AF3_DOCKER_IMAGE:-alphafold3:v3.0.2}"
MODEL_DIR="${AF3_MODEL_DIR:-/HL9/HApp/EMT/alphafold3}"
DB_DIR="${AF3_DB_DIR:-/scratch/AF3_database}"
OUTPUT_DIR="${AF3_OUTPUT_DIR:-${PWD}}"
WORK_ROOT="${AF3_WORK_ROOT:-${PWD}/temp}"
JACKHMMER_N_CPU="${AF3_JACKHMMER_N_CPU:-24}"
DOCKER_GPUS="${AF3_DOCKER_GPUS:-all}"
NAME="MSA"
CHAIN_ID="A"
SEQUENCE=""
KEEP_TEMP=0
WORK_DIR=""

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} -i SEQUENCE [options]

Required:
  -i, --input SEQUENCE      Protein sequence.

Options:
      --name NAME           Job name and final JSON filename stem. Default: ${NAME}
      --chain-id ID         Protein chain ID. Default: ${CHAIN_ID}
      --output-dir DIR      Final output directory. Default: ${OUTPUT_DIR}
      --work-root DIR       Temporary work root. Default: ${WORK_ROOT}
      --model-dir DIR       AlphaFold model directory. Default: ${MODEL_DIR}
      --db-dir DIR          AlphaFold database directory. Default: ${DB_DIR}
      --image IMAGE         Docker image. Default: ${IMAGE}
      --jackhmmer-n-cpu N   Jackhmmer CPU count. Default: ${JACKHMMER_N_CPU}
      --gpus VALUE          Docker --gpus value. Use "none" to omit. Default: ${DOCKER_GPUS}
      --keep-temp           Keep temporary files and logs.
  -h, --help                Show this help message.

Environment overrides:
  AF3_DOCKER_IMAGE, AF3_MODEL_DIR, AF3_DB_DIR, AF3_OUTPUT_DIR, AF3_WORK_ROOT,
  AF3_JACKHMMER_N_CPU, AF3_DOCKER_GPUS
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

resolve_existing_dir() {
  local path="$1"
  local label="$2"

  [[ -d "${path}" ]] || die "${label} not found: ${path}"
  (cd "${path}" && pwd -P)
}

ensure_dir() {
  local path="$1"

  mkdir -p -- "${path}"
  (cd "${path}" && pwd -P)
}

cleanup() {
  if [[ "${KEEP_TEMP}" == "1" ]]; then
    if [[ -n "${WORK_DIR}" ]]; then
      echo "Temporary files kept at: ${WORK_DIR}" >&2
    fi
    return
  fi

  if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
    case "${WORK_DIR}" in
      "${WORK_ROOT}"/af3_msa_only.*)
        rm -rf -- "${WORK_DIR}"
        ;;
      *)
        echo "Skipped cleanup for unexpected path: ${WORK_DIR}" >&2
        ;;
    esac
  fi

  if [[ -n "${WORK_ROOT}" && -d "${WORK_ROOT}" ]]; then
    rmdir -- "${WORK_ROOT}" 2>/dev/null || true
  fi
}

trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -i|--input)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      SEQUENCE="$2"
      shift 2
      ;;
    --name)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      NAME="$2"
      shift 2
      ;;
    --chain-id)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      CHAIN_ID="$2"
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --work-root)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      WORK_ROOT="$2"
      shift 2
      ;;
    --model-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      MODEL_DIR="$2"
      shift 2
      ;;
    --db-dir|--database-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      DB_DIR="$2"
      shift 2
      ;;
    --image)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      IMAGE="$2"
      shift 2
      ;;
    --jackhmmer-n-cpu|--cpus)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      JACKHMMER_N_CPU="$2"
      shift 2
      ;;
    --gpus)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      DOCKER_GPUS="$2"
      shift 2
      ;;
    --keep-temp)
      KEEP_TEMP=1
      shift
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "${SEQUENCE}" ]] || die "-i SEQUENCE is required."
[[ -n "${NAME}" ]] || die "--name must not be empty."
[[ "${NAME}" != *"/"* && "${NAME}" != *"\\"* ]] || die "--name must not contain path separators."
[[ "${CHAIN_ID}" =~ ^[A-Za-z0-9]+$ ]] || die "--chain-id must contain letters and numbers only."
[[ "${JACKHMMER_N_CPU}" =~ ^[0-9]+$ ]] || die "Jackhmmer CPU count must be a positive integer."
[[ "${JACKHMMER_N_CPU}" -ge 1 ]] || die "Jackhmmer CPU count must be at least 1."

command -v docker >/dev/null 2>&1 || die "docker was not found in PATH."
PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c 'import json' >/dev/null 2>&1; then
    PYTHON_BIN="${candidate}"
    break
  fi
done
if [[ -z "${PYTHON_BIN}" ]]; then
  die "python3 or python was not found in PATH."
fi

MODEL_DIR="$(resolve_existing_dir "${MODEL_DIR}" "Model directory")"
DB_DIR="$(resolve_existing_dir "${DB_DIR}" "Database directory")"
OUTPUT_DIR="$(ensure_dir "${OUTPUT_DIR}")"
WORK_ROOT="$(ensure_dir "${WORK_ROOT}")"

JOB_NAME="${NAME%.json}"
FINAL_JSON="${OUTPUT_DIR}/${JOB_NAME}.json"

WORK_DIR="$(mktemp -d "${WORK_ROOT}/af3_msa_only.XXXXXXXX")"
INPUT_DIR="${WORK_DIR}/input"
AF_OUTPUT_DIR="${WORK_DIR}/af_output"
LOG_DIR="${WORK_DIR}/logs"
mkdir -p -- "${INPUT_DIR}" "${AF_OUTPUT_DIR}" "${LOG_DIR}"

INPUT_JSON="${INPUT_DIR}/${JOB_NAME}_for_msa.json"

export AF3_MSA_NAME="${JOB_NAME}"
export AF3_MSA_CHAIN_ID="${CHAIN_ID}"
export AF3_MSA_SEQUENCE="${SEQUENCE}"
export AF3_MSA_INPUT_JSON="${INPUT_JSON}"

"${PYTHON_BIN}" - <<'PY'
import json
import os
import re

name = os.environ["AF3_MSA_NAME"]
chain_id = os.environ["AF3_MSA_CHAIN_ID"]
sequence = re.sub(r"\s+", "", os.environ["AF3_MSA_SEQUENCE"]).upper()
path = os.environ["AF3_MSA_INPUT_JSON"]

if not re.fullmatch(r"[A-Z*]+", sequence):
    raise SystemExit("Error: sequence must contain letters or stop markers only.")

payload = {
    "name": name,
    "modelSeeds": [1],
    "sequences": [
        {
            "protein": {
                "id": chain_id,
                "sequence": sequence,
            }
        }
    ],
    "dialect": "alphafold3",
    "version": 4,
}

with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY

docker_args=(
  run
  --rm
)

if [[ -t 1 ]]; then
  docker_args+=(-t)
fi

if [[ -n "${DOCKER_GPUS}" && "${DOCKER_GPUS}" != "none" ]]; then
  docker_args+=(--gpus "${DOCKER_GPUS}")
fi

docker_args+=(
  --volume "${INPUT_DIR}:/root/af_input:ro"
  --volume "${AF_OUTPUT_DIR}:/root/af_output"
  --volume "${MODEL_DIR}:/root/models:ro"
  --volume "${DB_DIR}:/root/public_databases:ro"
  "${IMAGE}"
  python run_alphafold.py
  --json_path="/root/af_input/$(basename "${INPUT_JSON}")"
  --model_dir=/root/models
  --db_dir=/root/public_databases
  --output_dir=/root/af_output
  --jackhmmer_n_cpu="${JACKHMMER_N_CPU}"
  --run_inference=false
)

printf 'Running AlphaFold 3 MSA generation...\n\n'
if ! docker "${docker_args[@]}" 2>&1 | tee "${LOG_DIR}/msa.log"; then
  KEEP_TEMP=1
  die "MSA data pipeline failed. Log: ${LOG_DIR}/msa.log"
fi

mapfile -t DATA_JSONS < <(find "${AF_OUTPUT_DIR}" -type f -name '*_data.json' | sort)
if [[ "${#DATA_JSONS[@]}" -eq 0 ]]; then
  KEEP_TEMP=1
  die "No *_data.json file found under: ${AF_OUTPUT_DIR}"
fi
if [[ "${#DATA_JSONS[@]}" -gt 1 ]]; then
  KEEP_TEMP=1
  die "Multiple *_data.json files found under: ${AF_OUTPUT_DIR}"
fi

cp -p -- "${DATA_JSONS[0]}" "${FINAL_JSON}"

echo "MSA JSON: ${FINAL_JSON}"
