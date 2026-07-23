#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"

IMAGE="${AF3_DOCKER_IMAGE:-alphafold3:v3.0.2}"
MODEL_DIR="${AF3_MODEL_DIR:-/HL9/HApp/EMT/alphafold3}"
DB_DIR="${AF3_DB_DIR:-/scratch/AF3_database}"
OUTPUT_DIR="${AF3_OUTPUT_DIR:-${PWD}/af3_output}"
WORK_ROOT="${AF3_WORK_ROOT:-${PWD}/temp}"
NUM_BATCHES="${AF3_NUM_BATCHES:-10}"
JACKHMMER_N_CPU="${AF3_JACKHMMER_N_CPU:-6}"
NHMMER_N_CPU="${AF3_NHMMER_N_CPU:-24}"
DOCKER_GPUS="${AF3_DOCKER_GPUS:-all}"
INFERENCE_GPUS="${AF3_INFERENCE_GPUS:-0,1}"
JAX_PLATFORMS="${AF3_JAX_PLATFORMS:-}"
KEEP_TEMP=0
CLEAN=0
USE_SU_ROOT=0
INPUT_DIR=""
WORK_DIR=""

if [[ "${AF3_USE_SU_ROOT:-0}" == "1" ]]; then
  USE_SU_ROOT=1
fi

filtered_args=()
for arg in "$@"; do
  if [[ "${arg}" == "--su-root" ]]; then
    USE_SU_ROOT=1
  else
    filtered_args+=("${arg}")
  fi
done

if [[ "${USE_SU_ROOT}" == "1" && "$(id -u)" != "0" ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0" "${filtered_args[@]}"
  fi

  if command -v su >/dev/null 2>&1; then
    quoted_args=""
    for arg in "${filtered_args[@]}"; do
      printf -v quoted_arg "%q" "${arg}"
      quoted_args+=" ${quoted_arg}"
    done
    printf -v quoted_pwd "%q" "${PWD}"
    printf -v quoted_script "%q" "$0"
    exec su root -c "cd ${quoted_pwd} && exec bash ${quoted_script}${quoted_args}"
  fi

  echo "Error: --su-root was requested, but neither sudo nor su is available." >&2
  exit 1
fi

declare -a DATA_PIPELINE_EXTRA_ARGS=()
declare -a INFERENCE_EXTRA_ARGS=()
declare -a DOCKER_EXTRA_ARGS=()

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} --input-dir DIR [options]

Required:
      --input-dir DIR, --i DIR
                            Directory containing AlphaFold 3 input JSON files.

Options:
      --num-batches N       Number of data-pipeline batches. Default: ${NUM_BATCHES}
      --output-dir DIR, --o DIR
                            Final AF3 inference output directory. Default: ${OUTPUT_DIR}
      --work-root DIR       Temporary work root. Default: ${WORK_ROOT}
      --model-dir DIR       AlphaFold model directory. Default: ${MODEL_DIR}
      --db-dir DIR          AlphaFold database directory. Default: ${DB_DIR}
      --image IMAGE         Docker image. Default: ${IMAGE}
      --jackhmmer-n-cpu N   Jackhmmer CPU count per data-pipeline container. Default: ${JACKHMMER_N_CPU}
      --nhmmer-n-cpu N      Nhmmer CPU count per data-pipeline container. Default: ${NHMMER_N_CPU}
      --inference-gpus LIST Comma-separated GPU IDs for parallel inference. Default: ${INFERENCE_GPUS}
      --gpus VALUE          Fallback Docker --gpus value for single inference. Use "none" to omit. Default: ${DOCKER_GPUS}
      --jax-platforms VALUE JAX_PLATFORMS for inference. Use "" or "none" to omit. Default: ${JAX_PLATFORMS:-\"\"}
      --docker-extra-arg X  Extra argument passed to every docker run. Repeat as needed.
      --data-extra-arg X    Extra argument passed to data-pipeline run_alphafold.py. Repeat as needed.
      --infer-extra-arg X   Extra argument passed to inference run_alphafold.py. Repeat as needed.
      --clean               Remove existing output directory before inference.
      --keep-temp           Keep temporary files and logs.
      --su-root             Re-execute this script as root before doing any work.
  -h, --help                Show this help message.

Environment overrides:
  AF3_DOCKER_IMAGE, AF3_MODEL_DIR, AF3_DB_DIR, AF3_OUTPUT_DIR, AF3_WORK_ROOT,
  AF3_NUM_BATCHES, AF3_JACKHMMER_N_CPU, AF3_NHMMER_N_CPU, AF3_DOCKER_GPUS,
  AF3_INFERENCE_GPUS, AF3_JAX_PLATFORMS, AF3_USE_SU_ROOT
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

log_status() {
  printf '%s [%s]\n' "$*" "$(date '+%Y-%m-%d %H:%M:%S')"
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

has_json_file() {
  local path="$1"

  [[ -n "$(find "${path}" -maxdepth 1 -type f -name '*.json' -print -quit)" ]]
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
      "${WORK_ROOT}"/af3_batch_run.*)
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
    --input-dir|--i|-i)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      INPUT_DIR="$2"
      shift 2
      ;;
    --num-batches)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      NUM_BATCHES="$2"
      shift 2
      ;;
    --output-dir|--o|-o)
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
    --nhmmer-n-cpu)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      NHMMER_N_CPU="$2"
      shift 2
      ;;
    --gpus)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      DOCKER_GPUS="$2"
      shift 2
      ;;
    --inference-gpus)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      INFERENCE_GPUS="$2"
      shift 2
      ;;
    --jax-platforms)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      JAX_PLATFORMS="$2"
      shift 2
      ;;
    --docker-extra-arg)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      DOCKER_EXTRA_ARGS+=("$2")
      shift 2
      ;;
    --data-extra-arg|--data-pipeline-extra-arg)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      DATA_PIPELINE_EXTRA_ARGS+=("$2")
      shift 2
      ;;
    --infer-extra-arg|--inference-extra-arg)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      INFERENCE_EXTRA_ARGS+=("$2")
      shift 2
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --keep-temp)
      KEEP_TEMP=1
      shift
      ;;
    --su-root)
      shift
      ;;

    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "${INPUT_DIR}" ]] || die "--input-dir DIR is required."
[[ "${NUM_BATCHES}" =~ ^[0-9]+$ ]] || die "Batch count must be a positive integer."
[[ "${NUM_BATCHES}" -ge 1 ]] || die "Batch count must be at least 1."
[[ "${JACKHMMER_N_CPU}" =~ ^[0-9]+$ ]] || die "Jackhmmer CPU count must be a positive integer."
[[ "${NHMMER_N_CPU}" =~ ^[0-9]+$ ]] || die "Nhmmer CPU count must be a positive integer."
if [[ "${INFERENCE_GPUS}" != "none" && "${INFERENCE_GPUS}" != "all" && ! "${INFERENCE_GPUS}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  die "Inference GPUs must be a comma-separated list like 0,1, or one of: all, none."
fi

command -v docker >/dev/null 2>&1 || die "docker was not found in PATH."

INPUT_DIR="$(resolve_existing_dir "${INPUT_DIR}" "Input directory")"
MODEL_DIR="$(resolve_existing_dir "${MODEL_DIR}" "Model directory")"
DB_DIR="$(resolve_existing_dir "${DB_DIR}" "Database directory")"
WORK_ROOT="$(ensure_dir "${WORK_ROOT}")"

if [[ "${CLEAN}" == "1" && -d "${OUTPUT_DIR}" ]]; then
  rm -rf -- "${OUTPUT_DIR}"
fi
OUTPUT_DIR="$(ensure_dir "${OUTPUT_DIR}")"

WORK_DIR="$(mktemp -d "${WORK_ROOT}/af3_batch_run.XXXXXXXX")"
BATCH_ROOT="${WORK_DIR}/batches"
DATA_OUTPUT_ROOT="${WORK_DIR}/data_pipeline_outputs"
READY_DIR="${WORK_DIR}/ready_for_inference"
INFERENCE_INPUT_ROOT="${WORK_DIR}/inference_inputs"
LOG_DIR="${WORK_DIR}/logs"
mkdir -p -- "${BATCH_ROOT}" "${DATA_OUTPUT_ROOT}" "${READY_DIR}" "${INFERENCE_INPUT_ROOT}" "${LOG_DIR}"

echo "Input directory: ${INPUT_DIR}"
echo "Output directory: ${OUTPUT_DIR}"
echo "Working directory: ${WORK_DIR}"
echo "Inference GPUs: ${INFERENCE_GPUS}"

mapfile -t SOURCE_JSONS < <(find "${INPUT_DIR}" -maxdepth 1 -type f -name '*.json' | sort)
[[ "${#SOURCE_JSONS[@]}" -gt 0 ]] || die "No JSON files found in: ${INPUT_DIR}"

for ((idx = 0; idx < NUM_BATCHES; idx++)); do
  mkdir -p -- "${BATCH_ROOT}/b$(printf '%02d' "${idx}")"
done

for idx in "${!SOURCE_JSONS[@]}"; do
  batch_idx=$((idx % NUM_BATCHES))
  batch_dir="${BATCH_ROOT}/b$(printf '%02d' "${batch_idx}")"
  cp -p -- "${SOURCE_JSONS[${idx}]}" "${batch_dir}/"
done

echo "Split ${#SOURCE_JSONS[@]} JSON file(s) into ${NUM_BATCHES} batch(es)."

run_docker_data_pipeline() {
  local batch_dir="$1"
  local output_dir="$2"
  local log_path="$3"

  local docker_args=(
    run
    --rm
    --volume "${batch_dir}:/root/af_input:ro"
    --volume "${output_dir}:/root/af_output"
    --volume "${MODEL_DIR}:/root/models:ro"
    --volume "${DB_DIR}:/root/public_databases:ro"
  )

  docker_args+=("${DOCKER_EXTRA_ARGS[@]}")
  docker_args+=(
    "${IMAGE}"
    python run_alphafold.py
    --input_dir=/root/af_input
    --model_dir=/root/models
    --db_dir=/root/public_databases
    --output_dir=/root/af_output
    --run_inference=false
    --jackhmmer_n_cpu="${JACKHMMER_N_CPU}"
    --nhmmer_n_cpu="${NHMMER_N_CPU}"
  )
  docker_args+=("${DATA_PIPELINE_EXTRA_ARGS[@]}")

  docker "${docker_args[@]}" >"${log_path}" 2>&1
}

pids=()
pid_names=()
pid_logs=()

for batch_dir in "${BATCH_ROOT}"/b*; do
  [[ -d "${batch_dir}" ]] || continue
  if ! has_json_file "${batch_dir}"; then
    echo "Skipping empty batch: $(basename "${batch_dir}")"
    continue
  fi

  name="$(basename "${batch_dir}")"
  out_dir="${DATA_OUTPUT_ROOT}/${name}"
  log_path="${LOG_DIR}/${name}.log"
  mkdir -p -- "${out_dir}"
  log_status "Starting data pipeline batch: ${name}"
  run_docker_data_pipeline "${batch_dir}" "${out_dir}" "${log_path}" &
  pids+=("$!")
  pid_names+=("${name}")
  pid_logs+=("${log_path}")
done

[[ "${#pids[@]}" -gt 0 ]] || die "No data pipeline batch was started."

failures=0
for idx in "${!pids[@]}"; do
  if wait "${pids[${idx}]}"; then
    log_status "Finished data pipeline batch: ${pid_names[${idx}]}"
  else
    echo "FAILED data pipeline batch: ${pid_names[${idx}]} log=${pid_logs[${idx}]}" >&2
    failures=$((failures + 1))
  fi
done

if [[ "${failures}" -ne 0 ]]; then
  KEEP_TEMP=1
  die "${failures} data pipeline batch(es) failed."
fi

mapfile -t DATA_JSONS < <(find "${DATA_OUTPUT_ROOT}" -type f -name '*_data.json' | sort)
if [[ "${#DATA_JSONS[@]}" -eq 0 ]]; then
  KEEP_TEMP=1
  die "No *_data.json files found under: ${DATA_OUTPUT_ROOT}"
fi

declare -A seen_data_json_names=()
for src in "${DATA_JSONS[@]}"; do
  name="$(basename "${src}")"
  if [[ -n "${seen_data_json_names[${name}]+x}" ]]; then
    KEEP_TEMP=1
    die "Duplicate data JSON filename found: ${name}"
  fi
  seen_data_json_names["${name}"]=1
  cp -p -- "${src}" "${READY_DIR}/${name}"
done

echo "Collected ${#DATA_JSONS[@]} data JSON file(s) for inference."

declare -a INFERENCE_GPU_IDS=()
if [[ "${INFERENCE_GPUS}" == "none" || "${INFERENCE_GPUS}" == "all" ]]; then
  INFERENCE_GPU_IDS=("${INFERENCE_GPUS}")
else
  IFS=',' read -r -a INFERENCE_GPU_IDS <<< "${INFERENCE_GPUS}"
fi

declare -a INFERENCE_INPUT_DIRS=()
for gpu_id in "${INFERENCE_GPU_IDS[@]}"; do
  gpu_dir="${INFERENCE_INPUT_ROOT}/gpu_${gpu_id}"
  mkdir -p -- "${gpu_dir}"
  INFERENCE_INPUT_DIRS+=("${gpu_dir}")
done

mapfile -t READY_JSONS < <(find "${READY_DIR}" -maxdepth 1 -type f -name '*.json' | sort)
for idx in "${!READY_JSONS[@]}"; do
  gpu_idx=$((idx % ${#INFERENCE_INPUT_DIRS[@]}))
  cp -p -- "${READY_JSONS[${idx}]}" "${INFERENCE_INPUT_DIRS[${gpu_idx}]}/"
done

run_docker_inference() {
  local gpu_id="$1"
  local input_dir="$2"
  local log_path="$3"

  local docker_args=(
    run
    --rm
  )

  case "${gpu_id}" in
    none)
      ;;
    all)
      if [[ -n "${DOCKER_GPUS}" && "${DOCKER_GPUS}" != "none" ]]; then
        docker_args+=(--gpus "${DOCKER_GPUS}")
      fi
      ;;
    *)
      docker_args+=(--gpus "device=${gpu_id}")
      ;;
  esac

  if [[ -n "${JAX_PLATFORMS}" && "${JAX_PLATFORMS}" != "none" ]]; then
    docker_args+=(-e "JAX_PLATFORMS=${JAX_PLATFORMS}")
  fi

  docker_args+=("${DOCKER_EXTRA_ARGS[@]}")
  docker_args+=(
    --volume "${input_dir}:/root/af_input:ro"
    --volume "${OUTPUT_DIR}:/root/af_output"
    --volume "${MODEL_DIR}:/root/models:ro"
    "${IMAGE}"
    python run_alphafold.py
    --input_dir=/root/af_input
    --model_dir=/root/models
    --output_dir=/root/af_output
    --run_data_pipeline=false
  )
  docker_args+=("${INFERENCE_EXTRA_ARGS[@]}")

  docker "${docker_args[@]}" >"${log_path}" 2>&1
}

inference_pids=()
inference_names=()
inference_logs=()

for idx in "${!INFERENCE_GPU_IDS[@]}"; do
  gpu_id="${INFERENCE_GPU_IDS[${idx}]}"
  input_dir="${INFERENCE_INPUT_DIRS[${idx}]}"
  if ! has_json_file "${input_dir}"; then
    echo "Skipping empty inference GPU: ${gpu_id}"
    continue
  fi

  log_path="${LOG_DIR}/inference_gpu_${gpu_id}.log"
  log_status "Starting inference on GPU ${gpu_id}."
  run_docker_inference "${gpu_id}" "${input_dir}" "${log_path}" &
  inference_pids+=("$!")
  inference_names+=("${gpu_id}")
  inference_logs+=("${log_path}")
done

[[ "${#inference_pids[@]}" -gt 0 ]] || die "No inference container was started."

inference_failures=0
for idx in "${!inference_pids[@]}"; do
  if wait "${inference_pids[${idx}]}"; then
    log_status "Finished inference on GPU ${inference_names[${idx}]}."
  else
    echo "FAILED inference on GPU ${inference_names[${idx}]} log=${inference_logs[${idx}]}" >&2
    inference_failures=$((inference_failures + 1))
  fi
done

if [[ "${inference_failures}" -ne 0 ]]; then
  KEEP_TEMP=1
  die "${inference_failures} inference container(s) failed."
fi

mapfile -t OUTPUT_FILES < <(find "${OUTPUT_DIR}" -type f | sort)
if [[ "${#OUTPUT_FILES[@]}" -eq 0 ]]; then
  KEEP_TEMP=1
  die "Inference finished, but no output files were found under: ${OUTPUT_DIR}"
fi

log_status "Inference finished."
echo "Output directory: ${OUTPUT_DIR}"
