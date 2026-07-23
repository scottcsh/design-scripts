#!/usr/bin/env bash
set -Eeuo pipefail

MODULE_NAME="miniforge3/25.3.0-3"
CONDA_ENV="rfd3"
GPUS="0,1"
DIFFUSION_BATCH_SIZE="16"
N_BATCHES="10"
STEP_SCALE="3"
GAMMA_0="0.2"
SKIP_EXISTING="True"
YAML_NAME="input.yaml"

INPUT_PDB=""
CONTIG=""
HOTSPOTS_RAW=()
OUTPUT_DIR=""

usage() {
  cat <<'USAGE'
Usage:
  rfd3 -i /path/to/input.pdb -c '<contig>' -h 'A144,A150,A161G' -o /path/to/output_dir

Required:
  -i, --input PDB          Input PDB path.
  -c, --contig CONTIG     Contig string passed to target_binder.contig.
  -h, --hotspots LIST     Hotspots separated by commas or spaces.
                          A144 becomes CA,CB. A144G becomes CA.
  -o, --out-dir DIR       Output directory.

Optional:
      --gpus LIST                 GPU IDs, comma-separated. Default: 0,1
      --diffusion-batch-size N    Default: 16
      --n-batches N               Default: 10
      --step-scale VALUE          Default: 3
      --gamma-0 VALUE             Default: 0.2
      --skip-existing VALUE       Default: True
      --module NAME               Default: miniforge3/25.3.0-3
      --conda-env NAME            Default: rfd3
      --yaml-name NAME            Default: input.yaml
      --rfd3-bin PATH             Explicit path to the real rfd3 executable.
      --no-env                    Do not load module or activate conda.
      --help                      Show this help.

Examples:
  rfd3 -i /data/input.pdb -c 'A1-200/0 B1-100' -h 'A144,A150,A161G' -o /data/rfd3_out
  rfd3 -i input.pdb -c 'A1-120' -h A144 -h A150 -h A161G -o ./out --gpus 0,1,2,3
USAGE
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '[rfd3-wrapper] %s\n' "$*" >&2
}

yaml_quote() {
  local raw="${1}"
  local escaped="${raw//\'/\'\'}"
  printf "'%s'" "${escaped}"
}

canonical_path() {
  local path="${1}"
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "${path}"
  elif command -v readlink >/dev/null 2>&1; then
    readlink -f "${path}" 2>/dev/null || printf '%s\n' "${path}"
  else
    printf '%s\n' "${path}"
  fi
}

load_environment() {
  if [[ "${NO_ENV:-0}" == "1" ]]; then
    info "Skipping module load and conda activation because --no-env was set."
    return
  fi

  if ! command -v module >/dev/null 2>&1; then
    if [[ -r /etc/profile.d/modules.sh ]]; then
      # shellcheck source=/dev/null
      source /etc/profile.d/modules.sh
    elif [[ -r /usr/share/Modules/init/bash ]]; then
      # shellcheck source=/dev/null
      source /usr/share/Modules/init/bash
    fi
  fi

  if command -v module >/dev/null 2>&1; then
    info "Loading module ${MODULE_NAME}."
    module load "${MODULE_NAME}"
  else
    die "The module command is unavailable. Start a login shell or use --no-env after activating conda manually."
  fi

  if ! command -v conda >/dev/null 2>&1; then
    die "conda is unavailable after loading module ${MODULE_NAME}."
  fi

  local conda_base
  conda_base="$(conda info --base)"
  if [[ -r "${conda_base}/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${conda_base}/etc/profile.d/conda.sh"
  else
    eval "$(conda shell.bash hook)"
  fi

  info "Activating conda environment ${CONDA_ENV}."
  conda activate "${CONDA_ENV}"
}

find_rfd3_binary() {
  if [[ -n "${RFD3_BIN:-}" ]]; then
    [[ -x "${RFD3_BIN}" ]] || die "RFD3 binary is not executable: ${RFD3_BIN}"
    printf '%s\n' "${RFD3_BIN}"
    return
  fi

  local self_path
  self_path="$(canonical_path "$0")"

  local candidate
  while IFS= read -r candidate; do
    [[ -n "${candidate}" ]] || continue
    local candidate_path
    candidate_path="$(canonical_path "${candidate}")"
    if [[ "${candidate_path}" != "${self_path}" && -x "${candidate_path}" ]]; then
      printf '%s\n' "${candidate_path}"
      return
    fi
  done < <(type -P -a rfd3 2>/dev/null || true)

  die "Could not find the real rfd3 executable. Set --rfd3-bin /path/to/rfd3."
}

add_hotspot_token() {
  local token="${1}"
  token="${token//[[:space:]]/}"
  token="${token%,}"
  token="${token#,}"
  [[ -n "${token}" ]] || return

  if [[ "${token}" =~ ^([A-Za-z][0-9]+)(G)?$ ]]; then
    local base="${BASH_REMATCH[1]}"
    local glycine_marker="${BASH_REMATCH[2]}"
    local atoms="CA,CB"
    if [[ "${glycine_marker}" == "G" ]]; then
      atoms="CA"
    fi

    if [[ -z "${HOTSPOT_SEEN[${base}]+x}" ]]; then
      HOTSPOT_KEYS+=("${base}")
      HOTSPOT_SEEN["${base}"]="1"
    fi
    HOTSPOT_ATOMS["${base}"]="${atoms}"
  else
    die "Invalid hotspot '${token}'. Use values like A144 or A144G."
  fi
}

parse_hotspots() {
  declare -gA HOTSPOT_ATOMS=()
  declare -gA HOTSPOT_SEEN=()
  declare -ga HOTSPOT_KEYS=()

  local raw
  for raw in "${HOTSPOTS_RAW[@]}"; do
    raw="${raw//,/ }"
    local token
    for token in ${raw}; do
      add_hotspot_token "${token}"
    done
  done

  [[ "${#HOTSPOT_KEYS[@]}" -gt 0 ]] || die "At least one hotspot is required."
}

write_yaml() {
  local yaml_path="${1}"
  mkdir -p "$(dirname "${yaml_path}")"

  {
    printf 'target_binder:\n'
    printf '  input: %s\n' "$(yaml_quote "${INPUT_PDB}")"
    printf '  contig: %s\n' "$(yaml_quote "${CONTIG}")"
    printf '  infer_ori_strategy: hotspots\n'
    printf '  select_hotspots:\n'
    local key
    for key in "${HOTSPOT_KEYS[@]}"; do
      printf '    %s: %s\n' "${key}" "${HOTSPOT_ATOMS[${key}]}"
    done
    printf '  is_non_loopy: true\n'
  } > "${yaml_path}"
}

parse_args() {
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      -i|--input)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        INPUT_PDB="$2"
        shift 2
        ;;
      -c|--contig)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        CONTIG="$2"
        shift 2
        ;;
      -h|--hotspots)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        HOTSPOTS_RAW+=("$2")
        shift 2
        ;;
      -o|--out-dir)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        OUTPUT_DIR="$2"
        shift 2
        ;;
      --gpus)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        GPUS="$2"
        shift 2
        ;;
      --diffusion-batch-size)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        DIFFUSION_BATCH_SIZE="$2"
        shift 2
        ;;
      --n-batches)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        N_BATCHES="$2"
        shift 2
        ;;
      --step-scale)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        STEP_SCALE="$2"
        shift 2
        ;;
      --gamma-0)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        GAMMA_0="$2"
        shift 2
        ;;
      --skip-existing)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        SKIP_EXISTING="$2"
        shift 2
        ;;
      --module)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        MODULE_NAME="$2"
        shift 2
        ;;
      --conda-env)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        CONDA_ENV="$2"
        shift 2
        ;;
      --yaml-name)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        YAML_NAME="$2"
        shift 2
        ;;
      --rfd3-bin)
        [[ "$#" -ge 2 ]] || die "$1 requires a value."
        RFD3_BIN="$2"
        shift 2
        ;;
      --no-env)
        NO_ENV="1"
        shift
        ;;
      --help)
        usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      -*)
        die "Unknown option: $1"
        ;;
      *)
        HOTSPOTS_RAW+=("$1")
        shift
        ;;
    esac
  done
}

run_designs() {
  local rfd3_binary="${1}"
  local yaml_path="${2}"

  IFS=',' read -r -a gpu_ids <<< "${GPUS}"
  [[ "${#gpu_ids[@]}" -gt 0 ]] || die "At least one GPU ID is required."

  local pids=()
  local gpu
  for gpu in "${gpu_ids[@]}"; do
    gpu="${gpu//[[:space:]]/}"
    [[ -n "${gpu}" ]] || continue

    local gpu_out_dir="${OUTPUT_DIR}/gpu${gpu}"
    mkdir -p "${gpu_out_dir}"

    info "Starting GPU ${gpu}: ${gpu_out_dir}"
    (
      CUDA_VISIBLE_DEVICES="${gpu}" "${rfd3_binary}" design \
        "out_dir=${gpu_out_dir}" \
        "inputs=${yaml_path}" \
        "diffusion_batch_size=${DIFFUSION_BATCH_SIZE}" \
        "n_batches=${N_BATCHES}" \
        "inference_sampler.step_scale=${STEP_SCALE}" \
        "inference_sampler.gamma_0=${GAMMA_0}" \
        "skip_existing=${SKIP_EXISTING}"
    ) &
    pids+=("$!")
  done

  [[ "${#pids[@]}" -gt 0 ]] || die "No valid GPU IDs were provided."

  local status=0
  local pid
  for pid in "${pids[@]}"; do
    if ! wait "${pid}"; then
      status=1
    fi
  done

  return "${status}"
}

main() {
  parse_args "$@"

  [[ -n "${INPUT_PDB}" ]] || die "Missing required -i/--input."
  [[ -n "${CONTIG}" ]] || die "Missing required -c/--contig."
  [[ -n "${OUTPUT_DIR}" ]] || die "Missing required -o/--out-dir."
  [[ -f "${INPUT_PDB}" ]] || die "Input PDB does not exist: ${INPUT_PDB}"

  INPUT_PDB="$(canonical_path "${INPUT_PDB}")"
  OUTPUT_DIR="$(canonical_path "${OUTPUT_DIR}")"

  parse_hotspots
  mkdir -p "${OUTPUT_DIR}"

  local yaml_path="${OUTPUT_DIR}/${YAML_NAME}"
  write_yaml "${yaml_path}"
  info "Wrote YAML: ${yaml_path}"

  load_environment
  local rfd3_binary
  rfd3_binary="$(find_rfd3_binary)"
  info "Using rfd3 binary: ${rfd3_binary}"

  if run_designs "${rfd3_binary}" "${yaml_path}"; then
    info "All rfd3 jobs completed successfully."
  else
    die "One or more rfd3 jobs failed."
  fi
}

main "$@"
