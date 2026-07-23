#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

INPUT_DIR=""
OUTPUT_DIR=""
NP="${ROSETTA_FILTER_NP:-24}"
INTERFACE="${ROSETTA_FILTER_INTERFACE:-A_B}"
ROSETTA_EXE="${ROSETTA_FILTER_EXE:-/HL9/HApp/EMT/rosetta/source/build/src/release/linux/5.14/64/x86/gcc/11/mpi/InterfaceAnalyzer.mpi.linuxgccrelease}"
DATABASE_DIR="${ROSETTA_FILTER_DATABASE:-/HL9/HApp/EMT/rosetta/database/}"
WEIGHTS="${ROSETTA_FILTER_WEIGHTS:-ref2015}"
MODULE_NAME="${ROSETTA_FILTER_MODULE:-rosetta}"
PACK_SEPARATED="${ROSETTA_FILTER_PACK_SEPARATED:-true}"
SCOREFILE_NAME="${ROSETTA_FILTER_SCOREFILE:-interface.sc}"
LIST_NAME="${ROSETTA_FILTER_LIST:-pdb_list.txt}"
LOAD_MODULE=1
RUN_ROSETTA=1
CLEAN=0
COPY_SELECTED=0
SERVE=1
SERVE_HOST="${ROSETTA_FILTER_HOST:-172.27.25.26}"
SERVE_PORT="${ROSETTA_FILTER_PORT:-8787}"
VERBOSE="${ROSETTA_FILTER_VERBOSE:-0}"
LOG_DIR=""
X_COLUMN=""
Y_COLUMN=""
X_MIN=""
X_MAX=""
Y_MIN=""
Y_MAX=""
X_LABEL=""
Y_LABEL=""
RENAME_SELECTED=0
AF3_TOP=""
AF3_PRESET="${ROSETTA_FILTER_AF3_PRESET:-none}"
AF3_SORT_BY="${ROSETTA_FILTER_AF3_SORT_BY:-composite_score}"
AF3_PAIR_REDUCE=""
AF3_INCLUDE_SAMPLES=0
AF3_SAMPLES_ONLY=0
AF3_METRICS_CSV=""

declare -a ROSETTA_EXTRA_ARGS=()
declare -a AF3_PAIRS=()

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} -i STRUCTURE_DIR -o OUTPUT_DIR [options]

Required:
  -i, --input-dir DIR       Directory containing PDB or CIF files.
  -o, --output-dir DIR      Directory for Rosetta scores, CSV reports, and HTML plot.

Rosetta options:
      --np N                MPI process count. Default: ${NP}
      --interface CHAINS    InterfaceAnalyzer interface string. Default: ${INTERFACE}
      --executable FILE     InterfaceAnalyzer MPI executable.
      --database DIR        Rosetta database directory. Default: ${DATABASE_DIR}
      --weights NAME        Rosetta score weights. Default: ${WEIGHTS}
      --module NAME         Environment module to load. Default: ${MODULE_NAME}
      --no-module-load      Skip module loading.
      --extra-arg VALUE     Extra InterfaceAnalyzer argument. Repeat as needed.

AF3 prefilter options:
      --af3-top N           Select the top N AF3 results first, then run Rosetta only on those CIF files.
      --af3-preset NAME     AF3 filter preset: none, loose, balanced, strict. Default: ${AF3_PRESET}
      --preset NAME         Alias for --af3-preset.
      --af3-sort-by NAME    AF3 ranking metric. Default: ${AF3_SORT_BY}
      --af3-pair VALUE      AF3 chain pair for interface metrics. Overrides automatic pairing
                            from a simple Rosetta interface such as A_B. Repeat as needed.
      --af3-pair-reduce NAME
                            AF3 pair reducer: best, worst, mean.
      --af3-include-samples Include AF3 seed/sample subdirectories.
      --af3-samples-only    Evaluate only AF3 seed/sample subdirectories.
      --af3-metrics-csv CSV Merge an existing AF3 selected_results.csv into the plot.

Report options:
      --scorefile NAME      Scorefile name or path. Default: ${SCOREFILE_NAME}
      --x-column NAME       Initial x-axis metric in the HTML plot.
      --y-column NAME       Initial y-axis metric in the HTML plot.
      --x-label TEXT        Display label for the x-axis metric.
      --y-label TEXT        Display label for the y-axis metric.
      --x-min VALUE         Initial and command-line x lower cutoff.
      --x-max VALUE         Initial and command-line x upper cutoff.
      --y-min VALUE         Initial and command-line y lower cutoff.
      --y-max VALUE         Initial and command-line y upper cutoff.
      --copy-selected       Optional command-line copy. Default is no structure copy.
      --rename              Rename exported structures by final selected order: 1.cif, 2.cif, ...
      --serve               Start the export-enabled local report server after report generation. Default.
      --no-serve            Do not start the report server after report generation.
      --host HOST           Report server bind host. Default: ${SERVE_HOST}
      --port PORT           Report server bind port. Default: ${SERVE_PORT}
      --verbose             Print Rosetta and report-generation output to the terminal.
      --plot-only           Skip Rosetta and generate reports from an existing scorefile.
      --clean               Remove OUTPUT_DIR before running.
  -h, --help                Show this help message.

Examples:
  ${SCRIPT_NAME} -i ./af3_pdbs -o ./rosetta_filter --interface A_B --np 8
  ${SCRIPT_NAME} -i ./af3_results -o ./rosetta_filter --af3-top 50 --interface A_B
  ${SCRIPT_NAME} -i ./af3_results -o ./rosetta_filter --af3-top 1000 --af3-preset loose --interface A_B
  ${SCRIPT_NAME} -i ./af3_pdbs -o ./rosetta_filter --y-column dG_separated --y-max -10
  ${SCRIPT_NAME} -i ./af3_pdbs -o ./rosetta_filter --plot-only --scorefile interface.sc
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

log_status() {
  printf '%s [%s]\n' "$*" "$(date '+%Y-%m-%d %H:%M:%S')"
}

run_step() {
  local label="$1"
  local log_file="$2"
  local status
  shift 2

  if [[ "${VERBOSE}" == "1" ]]; then
    log_status "${label} [Running...]"
    "$@"
    log_status "${label} [Done]"
    return
  fi

  printf '%s [Running...]\n' "${label}"
  set +e
  "$@" > "${log_file}" 2>&1
  status=$?
  set -e
  if [[ "${status}" -ne 0 ]]; then
    printf '%s [Failed]\n' "${label}" >&2
    printf 'Log: %s\n' "${log_file}" >&2
    return "${status}"
  fi
  printf '%s [Done]\n' "${label}"
}

run_quiet_step() {
  local label="$1"
  local log_file="$2"
  local status
  shift 2

  if [[ "${VERBOSE}" == "1" ]]; then
    "$@"
    return
  fi

  set +e
  "$@" > "${log_file}" 2>&1
  status=$?
  set -e
  if [[ "${status}" -ne 0 ]]; then
    printf '%s [Failed]\n' "${label}" >&2
    printf 'Log: %s\n' "${log_file}" >&2
    return "${status}"
  fi
}

resolve_existing_dir() {
  local path="$1"
  local label="$2"

  [[ -d "${path}" ]] || die "${label} not found: ${path}"
  (cd "${path}" && pwd -P)
}

resolve_output_dir() {
  local path="$1"
  local parent
  local name

  [[ -n "${path}" ]] || die "Output directory must not be empty."
  parent="$(dirname -- "${path}")"
  name="$(basename -- "${path}")"
  [[ -n "${name}" && "${name}" != "." && "${name}" != ".." ]] || die "Invalid output directory: ${path}"
  mkdir -p -- "${parent}"
  parent="$(cd "${parent}" && pwd -P)"
  printf '%s/%s\n' "${parent}" "${name}"
}

find_python() {
  local candidate

  for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

load_rosetta_module() {
  local init_script

  if type module >/dev/null 2>&1; then
    module load "${MODULE_NAME}"
    return
  fi

  for init_script in \
    /etc/profile.d/modules.sh \
    /usr/share/Modules/init/bash \
    /usr/share/lmod/lmod/init/bash
  do
    if [[ -r "${init_script}" ]]; then
      # shellcheck source=/dev/null
      . "${init_script}"
      if type module >/dev/null 2>&1; then
        module load "${MODULE_NAME}"
        return
      fi
    fi
  done

  die "Environment modules are not available. Run in a login shell or use --no-module-load."
}

safe_clean_output_dir() {
  local input_dir="$1"
  local output_dir="$2"

  [[ -n "${output_dir}" && "${output_dir}" != "/" ]] || die "Refusing to clean unsafe output directory: ${output_dir}"
  case "${output_dir}" in
    "${input_dir}"|"${input_dir}"/*)
      die "--clean output directory must not be the input directory or a child of it."
      ;;
  esac
  rm -rf -- "${output_dir}"
}

write_structure_list() {
  local input_dir="$1"
  local output_dir="$2"
  local list_file="$3"

  if [[ "${output_dir}" == "${input_dir}" || "${output_dir}" == "${input_dir}"/* ]]; then
    find "${input_dir}" \
      -path "${output_dir}" -prune -o \
      -type f \( -iname '*.pdb' -o -iname '*.cif' \) -print | sort > "${list_file}"
  else
    find "${input_dir}" \
      -type f \( -iname '*.pdb' -o -iname '*.cif' \) -print | sort > "${list_file}"
  fi

  [[ -s "${list_file}" ]] || die "No PDB or CIF files were found under: ${input_dir}"
  if grep -Eq '[[:space:]]' "${list_file}"; then
    die "Structure paths containing whitespace are not supported by Rosetta -l."
  fi
}

count_csv_rows() {
  local csv_file="$1"
  local line_count

  if [[ ! -f "${csv_file}" ]]; then
    printf '0\n'
    return
  fi

  line_count="$(wc -l < "${csv_file}" | tr -d '[:space:]')"
  if [[ -z "${line_count}" || ! "${line_count}" =~ ^[0-9]+$ ]]; then
    printf '0\n'
    return
  fi
  if (( line_count <= 1 )); then
    printf '0\n'
    return
  fi
  printf '%s\n' "$((line_count - 1))"
}

report_af3_prefilter_summary() {
  local output_dir="$1"
  local selected_csv="${output_dir}/selected_results.csv"
  local all_csv="${output_dir}/all_results.csv"
  local selected_count
  local scanned_count

  selected_count="$(count_csv_rows "${selected_csv}")"
  scanned_count="$(count_csv_rows "${all_csv}")"
  log_status "AF3 prefilter selected ${selected_count} result(s) from ${scanned_count} scanned result(s) with preset ${AF3_PRESET}."
  if [[ "${AF3_TOP}" -gt 0 && "${selected_count}" -lt "${AF3_TOP}" && "${AF3_PRESET}" != "none" ]]; then
    log_status "AF3 preset ${AF3_PRESET} passed fewer than --af3-top ${AF3_TOP}; use --af3-preset none to keep top N without preset thresholds."
  fi
}

run_interface_analyzer() {
  local output_dir="$1"
  local list_file="$2"
  local scorefile_name="$3"

  [[ -x "${ROSETTA_EXE}" ]] || die "InterfaceAnalyzer executable is not executable: ${ROSETTA_EXE}"
  [[ -d "${DATABASE_DIR}" ]] || die "Rosetta database directory not found: ${DATABASE_DIR}"
  command -v mpirun >/dev/null 2>&1 || die "mpirun was not found in PATH."

  (
    cd "${output_dir}"
    mpirun -np "${NP}" "${ROSETTA_EXE}" \
      -l "${list_file}" \
      -interface "${INTERFACE}" \
      -out:file:score_only "${scorefile_name}" \
      -score:weights "${WEIGHTS}" \
      -pack_separated "${PACK_SEPARATED}" \
      -database "${DATABASE_DIR}" \
      "${ROSETTA_EXTRA_ARGS[@]}"
  )
}

configure_af3_pairs() {
  if [[ "${#AF3_PAIRS[@]}" -gt 0 ]]; then
    return
  fi
  if [[ "${INTERFACE}" =~ ^([A-Za-z])_([A-Za-z])$ ]]; then
    AF3_PAIRS=("${BASH_REMATCH[1]}:${BASH_REMATCH[2]}")
  fi
}

run_af3_prefilter() {
  local input_dir="$1"
  local output_dir="$2"
  local af3_filter="$3"
  local python_bin="$4"
  local pair
  local -a af3_args=(
    --input-dir "${input_dir}"
    --output-dir "${output_dir}"
    --top "${AF3_TOP}"
    --preset "${AF3_PRESET}"
    --sort-by "${AF3_SORT_BY}"
    --copy-mode cif-summary
    --clean
  )

  for pair in "${AF3_PAIRS[@]}"; do
    af3_args+=(--pair "${pair}")
  done
  [[ -n "${AF3_PAIR_REDUCE}" ]] && af3_args+=(--pair-reduce "${AF3_PAIR_REDUCE}")
  [[ "${AF3_INCLUDE_SAMPLES}" == "1" ]] && af3_args+=(--include-samples)
  [[ "${AF3_SAMPLES_ONLY}" == "1" ]] && af3_args+=(--samples-only)

  "${python_bin}" "${af3_filter}" "${af3_args[@]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -i|--input-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      INPUT_DIR="$2"
      shift 2
      ;;
    -o|--output-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --np)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      NP="$2"
      shift 2
      ;;
    --interface)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      INTERFACE="$2"
      shift 2
      ;;
    --executable)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      ROSETTA_EXE="$2"
      shift 2
      ;;
    --database|--database-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      DATABASE_DIR="$2"
      shift 2
      ;;
    --weights)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      WEIGHTS="$2"
      shift 2
      ;;
    --module)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      MODULE_NAME="$2"
      shift 2
      ;;
    --no-module-load)
      LOAD_MODULE=0
      shift
      ;;
    --extra-arg)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      ROSETTA_EXTRA_ARGS+=("$2")
      shift 2
      ;;
    --af3-top)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      AF3_TOP="$2"
      shift 2
      ;;
    --af3-preset|--preset)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      AF3_PRESET="$2"
      shift 2
      ;;
    --af3-sort-by)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      AF3_SORT_BY="$2"
      shift 2
      ;;
    --af3-pair)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      AF3_PAIRS+=("$2")
      shift 2
      ;;
    --af3-pair-reduce)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      AF3_PAIR_REDUCE="$2"
      shift 2
      ;;
    --af3-include-samples)
      AF3_INCLUDE_SAMPLES=1
      shift
      ;;
    --af3-samples-only)
      AF3_SAMPLES_ONLY=1
      shift
      ;;
    --af3-metrics-csv)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      AF3_METRICS_CSV="$2"
      shift 2
      ;;
    --scorefile)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      SCOREFILE_NAME="$2"
      shift 2
      ;;
    --x-column)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      X_COLUMN="$2"
      shift 2
      ;;
    --y-column)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      Y_COLUMN="$2"
      shift 2
      ;;
    --x-label)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      X_LABEL="$2"
      shift 2
      ;;
    --y-label)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      Y_LABEL="$2"
      shift 2
      ;;
    --x-min)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      X_MIN="$2"
      shift 2
      ;;
    --x-max)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      X_MAX="$2"
      shift 2
      ;;
    --y-min)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      Y_MIN="$2"
      shift 2
      ;;
    --y-max)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      Y_MAX="$2"
      shift 2
      ;;
    --copy-selected)
      COPY_SELECTED=1
      shift
      ;;
    --rename)
      RENAME_SELECTED=1
      shift
      ;;
    --serve)
      SERVE=1
      shift
      ;;
    --no-serve)
      SERVE=0
      shift
      ;;
    --host)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      SERVE_HOST="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      SERVE_PORT="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    --plot-only|--skip-rosetta)
      RUN_ROSETTA=0
      shift
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

[[ -n "${INPUT_DIR}" ]] || die "Missing required option: --input-dir"
[[ -n "${OUTPUT_DIR}" ]] || die "Missing required option: --output-dir"
[[ "${NP}" =~ ^[0-9]+$ && "${NP}" -gt 0 ]] || die "--np must be a positive integer."
[[ "${SERVE_PORT}" =~ ^[0-9]+$ && "${SERVE_PORT}" -gt 0 ]] || die "--port must be a positive integer."
if [[ -n "${AF3_TOP}" ]]; then
  [[ "${AF3_TOP}" =~ ^[0-9]+$ ]] || die "--af3-top must be a non-negative integer."
fi
case "${AF3_PRESET}" in
  none|loose|balanced|strict) ;;
  *) die "--af3-preset must be one of: none, loose, balanced, strict." ;;
esac
case "${AF3_SORT_BY}" in
  ranking_score|iptm|ptm|pair_iptm|pair_pae_min|interface_score|composite_score|fraction_disordered|has_clash) ;;
  *) die "--af3-sort-by is not supported: ${AF3_SORT_BY}" ;;
esac
if [[ -n "${AF3_PAIR_REDUCE}" ]]; then
  case "${AF3_PAIR_REDUCE}" in
    best|worst|mean) ;;
    *) die "--af3-pair-reduce must be one of: best, worst, mean." ;;
  esac
fi

INPUT_DIR="$(resolve_existing_dir "${INPUT_DIR}" "Input directory")"
OUTPUT_DIR="$(resolve_output_dir "${OUTPUT_DIR}")"

if [[ "${CLEAN}" == "1" && -e "${OUTPUT_DIR}" ]]; then
  safe_clean_output_dir "${INPUT_DIR}" "${OUTPUT_DIR}"
fi
mkdir -p -- "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd -P)"
LOG_DIR="${OUTPUT_DIR}/logs"
mkdir -p -- "${LOG_DIR}"

PYTHON_BIN="$(find_python)" || die "python3 or python was not found in PATH."
ROSETTA_INPUT_DIR="${INPUT_DIR}"
if [[ -n "${AF3_TOP}" ]]; then
  configure_af3_pairs
  AF3_FILTER="${SCRIPT_DIR}/rosetta_filter_af3.py"
  [[ -f "${AF3_FILTER}" ]] || die "AF3 filter helper not found: ${AF3_FILTER}"
  AF3_WORK_DIR="${OUTPUT_DIR}/af3_top_for_rosetta"
  run_step "AF3 top-${AF3_TOP} filtering" "${LOG_DIR}/af3_top_filter.log" \
    run_af3_prefilter "${INPUT_DIR}" "${AF3_WORK_DIR}" "${AF3_FILTER}" "${PYTHON_BIN}"
  report_af3_prefilter_summary "${AF3_WORK_DIR}"
  ROSETTA_INPUT_DIR="${AF3_WORK_DIR}/cif"
  [[ -d "${ROSETTA_INPUT_DIR}" ]] || die "AF3 filtered CIF directory not found: ${ROSETTA_INPUT_DIR}"
  if [[ -z "${AF3_METRICS_CSV}" ]]; then
    AF3_METRICS_CSV="${AF3_WORK_DIR}/selected_results.csv"
  fi
fi

if [[ -n "${AF3_METRICS_CSV}" && "${AF3_METRICS_CSV}" != /* ]]; then
  AF3_METRICS_PARENT="$(dirname -- "${AF3_METRICS_CSV}")"
  [[ -d "${AF3_METRICS_PARENT}" ]] || die "AF3 metrics CSV directory not found: ${AF3_METRICS_PARENT}"
  AF3_METRICS_CSV="$(cd "${AF3_METRICS_PARENT}" && pwd -P)/$(basename -- "${AF3_METRICS_CSV}")"
fi
if [[ -n "${AF3_METRICS_CSV}" ]]; then
  [[ -f "${AF3_METRICS_CSV}" ]] || die "AF3 metrics CSV not found: ${AF3_METRICS_CSV}"
fi

LIST_FILE="${OUTPUT_DIR}/${LIST_NAME}"
write_structure_list "${ROSETTA_INPUT_DIR}" "${OUTPUT_DIR}" "${LIST_FILE}"
STRUCTURE_COUNT="$(wc -l < "${LIST_FILE}" | tr -d '[:space:]')"
log_status "Discovered ${STRUCTURE_COUNT} structure file(s)."

SCOREFILE_ARG="${SCOREFILE_NAME}"
if [[ "${SCOREFILE_ARG}" == /* ]]; then
  SCOREFILE_PATH="${SCOREFILE_ARG}"
else
  SCOREFILE_PATH="${OUTPUT_DIR}/${SCOREFILE_ARG}"
fi

if [[ "${RUN_ROSETTA}" == "1" ]]; then
  mkdir -p -- "$(dirname -- "${SCOREFILE_PATH}")"
  if [[ "${LOAD_MODULE}" == "1" ]]; then
    run_quiet_step "Loading module: ${MODULE_NAME}" "${LOG_DIR}/module_load.log" load_rosetta_module
  fi
  run_step "InterfaceAnalyzer (${NP} MPI process(es))" "${LOG_DIR}/interface_analyzer.log" \
    run_interface_analyzer "${OUTPUT_DIR}" "${LIST_FILE}" "${SCOREFILE_ARG}"
else
  [[ -f "${SCOREFILE_PATH}" ]] || die "Scorefile not found for --plot-only: ${SCOREFILE_PATH}"
fi

PLOTTER="${SCRIPT_DIR}/rosetta_filter_plot.py"
[[ -f "${PLOTTER}" ]] || die "Plot helper not found: ${PLOTTER}"

declare -a REPORT_ARGS=(
  --scorefile "${SCOREFILE_PATH}"
  --output-dir "${OUTPUT_DIR}"
  --structure-list "${LIST_FILE}"
)

[[ -n "${AF3_METRICS_CSV}" ]] && REPORT_ARGS+=(--af3-metrics-csv "${AF3_METRICS_CSV}")
[[ -n "${X_COLUMN}" ]] && REPORT_ARGS+=(--x-column "${X_COLUMN}")
[[ -n "${Y_COLUMN}" ]] && REPORT_ARGS+=(--y-column "${Y_COLUMN}")
[[ -n "${X_LABEL}" ]] && REPORT_ARGS+=(--x-label "${X_LABEL}")
[[ -n "${Y_LABEL}" ]] && REPORT_ARGS+=(--y-label "${Y_LABEL}")
[[ -n "${X_MIN}" ]] && REPORT_ARGS+=(--x-min "${X_MIN}")
[[ -n "${X_MAX}" ]] && REPORT_ARGS+=(--x-max "${X_MAX}")
[[ -n "${Y_MIN}" ]] && REPORT_ARGS+=(--y-min "${Y_MIN}")
[[ -n "${Y_MAX}" ]] && REPORT_ARGS+=(--y-max "${Y_MAX}")
[[ "${COPY_SELECTED}" == "1" ]] && REPORT_ARGS+=(--copy-selected)
[[ "${RENAME_SELECTED}" == "1" ]] && REPORT_ARGS+=(--rename)

run_quiet_step "Generating CSV reports and interactive HTML plot" "${LOG_DIR}/report_generation.log" \
  "${PYTHON_BIN}" "${PLOTTER}" "${REPORT_ARGS[@]}"

if [[ "${SERVE}" == "1" ]]; then
  SERVER="${SCRIPT_DIR}/rosetta_filter_server.py"
  [[ -f "${SERVER}" ]] || die "Export server not found: ${SERVER}"
  log_status "Starting report server. Press Ctrl-C to stop it."
  exec "${PYTHON_BIN}" "${SERVER}" \
    --output-dir "${OUTPUT_DIR}" \
    --host "${SERVE_HOST}" \
    --port "${SERVE_PORT}" \
    --csv-name "interface_scores.csv"
fi

log_status "Done. Open ${OUTPUT_DIR}/rosetta_filter_plot.html"
