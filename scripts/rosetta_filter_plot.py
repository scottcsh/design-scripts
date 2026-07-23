#!/usr/bin/env python3
"""
Convert a Rosetta InterfaceAnalyzer scorefile into CSV reports and an
interactive cutoff plot.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any


DEFAULT_X_CANDIDATES = (
    "interface_rms",
    "interface_rmsd",
    "InterfaceRMS",
    "Interface_RMS",
    "Interface_RMSD",
    "Irms",
    "Irmsd",
    "rms",
    "rmsd",
    "sc_value",
    "dSASA_int",
    "packstat",
)
DEFAULT_Y_CANDIDATES = (
    "dG_separated/dSASAx100",
    "dG_separated",
    "dG_cross/dSASAx100",
    "dG_cross",
    "interface_delta",
    "total_score",
)

AF3_METRIC_COLUMNS = (
    "ranking_score",
    "iptm",
    "ptm",
    "pair_iptm",
    "pair_pae_min",
    "interface_score",
    "composite_score",
)

AF3_METADATA_COLUMNS = {
    "rank": "af3_rank",
    "status": "af3_status",
    "fail_reasons": "af3_fail_reasons",
    "relative_id": "af3_relative_id",
    "summary_path": "af3_summary_path",
    "model_path": "af3_model_path",
    "chain_count": "af3_chain_count",
    "selected_pair": "af3_selected_pair",
}

IGNORED_NUMERIC_COLUMNS = {
    "af3_rank",
    "af3_chain_count",
}

DISPLAY_LABELS = {
    "interface_rms": "Interface RMS (A)",
    "interface_rmsd": "Interface RMS (A)",
    "InterfaceRMS": "Interface RMS (A)",
    "Interface_RMS": "Interface RMS (A)",
    "Interface_RMSD": "Interface RMS (A)",
    "Irms": "Interface RMS (A)",
    "Irmsd": "Interface RMS (A)",
    "rms": "Interface RMS (A)",
    "rmsd": "Interface RMS (A)",
    "dG_separated/dSASAx100": "Interface Energy (kcal/mol per 100 A^2)",
    "dG_separated": "Interface Energy (kcal/mol)",
    "dG_cross/dSASAx100": "Interface Energy (kcal/mol per 100 A^2)",
    "dG_cross": "Interface Energy (kcal/mol)",
    "interface_delta": "Interface Energy (kcal/mol)",
    "total_score": "Total Score",
    "sc_value": "Shape Complementarity",
    "dSASA_int": "Buried Interface SASA (A^2)",
    "packstat": "Packstat",
    "ranking_score": "AF3 Ranking Score",
    "iptm": "AF3 ipTM",
    "ptm": "AF3 pTM",
    "pair_iptm": "AF3 Pair ipTM",
    "pair_pae_min": "AF3 Pair PAE Min (A)",
    "interface_score": "AF3 Interface Score",
    "composite_score": "AF3 Composite Score",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build CSV reports and an interactive cutoff plot from a Rosetta scorefile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scorefile", required=True, help="Rosetta scorefile written by InterfaceAnalyzer.")
    parser.add_argument("--output-dir", required=True, help="Directory where reports are written.")
    parser.add_argument("--structure-list", help="Optional pdb_list.txt used for resolving structure paths.")
    parser.add_argument("--af3-metrics-csv", help="Optional AF3 selected_results.csv to merge into Rosetta rows.")
    parser.add_argument("--x-column", help="Initial x-axis metric.")
    parser.add_argument("--y-column", help="Initial y-axis metric.")
    parser.add_argument("--x-label", help="Display label for the x-axis metric.")
    parser.add_argument("--y-label", help="Display label for the y-axis metric.")
    parser.add_argument("--x-min", type=float, help="Command-line and initial x lower cutoff.")
    parser.add_argument("--x-max", type=float, help="Command-line and initial x upper cutoff.")
    parser.add_argument("--y-min", type=float, help="Command-line and initial y lower cutoff.")
    parser.add_argument("--y-max", type=float, help="Command-line and initial y upper cutoff.")
    parser.add_argument("--html-name", default="rosetta_filter_plot.html", help="HTML report filename.")
    parser.add_argument("--csv-name", default="interface_scores.csv", help="All-score CSV filename.")
    parser.add_argument("--selected-csv-name", default="selected_results.csv", help="Selected-score CSV filename.")
    parser.add_argument("--settings-name", default="rosetta_filter_settings.json", help="Settings JSON filename.")
    parser.add_argument(
        "--copy-selected",
        action="store_true",
        help="Optional command-line copy. By default, files are not copied; use the HTML export button.",
    )
    parser.add_argument(
        "--rename",
        action="store_true",
        help="Rename exported structures by final selected order: 1.cif, 2.cif, ...",
    )
    parser.add_argument("--selected-dir", default="selected_structures", help="Directory name for copied structures.")
    return parser.parse_args()


def die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def is_number_token(value: str) -> bool:
    return as_float(value) is not None


def normalize_score_tokens(header: list[str], tokens: list[str]) -> list[str]:
    if len(tokens) == len(header):
        return tokens
    if len(tokens) < len(header):
        return tokens + [""] * (len(header) - len(tokens))

    if "description" in header:
        desc_index = header.index("description")
        trailing_slots = len(header) - desc_index - 1
        desc_width = len(tokens) - desc_index - trailing_slots
        desc_width = max(desc_width, 1)
        return (
            tokens[:desc_index]
            + [" ".join(tokens[desc_index : desc_index + desc_width])]
            + tokens[desc_index + desc_width :]
        )

    return tokens[: len(header) - 1] + [" ".join(tokens[len(header) - 1 :])]


def parse_scorefile(scorefile: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if not scorefile.is_file():
        die(f"Scorefile not found: {scorefile}")

    header: list[str] | None = None
    rows: list[dict[str, Any]] = []
    with scorefile.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line.startswith("SCORE:"):
                continue
            tokens = line.split()[1:]
            if not tokens:
                continue
            if header is None:
                header = tokens
                continue
            if tokens == header:
                continue
            if len(tokens) >= 2 and not is_number_token(tokens[0]) and "description" in tokens:
                header = tokens
                continue
            values = normalize_score_tokens(header, tokens)
            row: dict[str, Any] = {"_index": len(rows) + 1}
            for key, value in zip(header, values):
                if key == "description":
                    row[key] = value
                    continue
                number = as_float(value)
                row[key] = number if number is not None else value
            if "description" not in row:
                row["description"] = f"row_{row['_index']}"
            rows.append(row)

    if header is None:
        die(f"No SCORE header was found in: {scorefile}")
    if not rows:
        die(f"No SCORE data rows were found in: {scorefile}")
    if "description" not in header:
        header = header + ["description"]
    return header, rows


def numeric_columns(header: list[str], rows: list[dict[str, Any]]) -> list[str]:
    columns = []
    for column in header:
        if column == "description" or column in IGNORED_NUMERIC_COLUMNS:
            continue
        if any(isinstance(row.get(column), (int, float)) for row in rows):
            columns.append(column)
    return columns


def choose_column(requested: str | None, candidates: tuple[str, ...], columns: list[str], label: str) -> str:
    if not columns:
        die("No numeric score columns were found.")
    if requested:
        if requested in columns:
            return requested
        lower_map = {column.lower(): column for column in columns}
        if requested.lower() in lower_map:
            return lower_map[requested.lower()]
        die(f"{label} column is not numeric or was not found: {requested}")

    lower_map = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    return columns[0]


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def path_keys(path: Path) -> list[str]:
    raw = str(path)
    normalized = raw.replace("\\", "/")
    return unique(
        [
            raw,
            normalized,
            path.name,
            path.stem,
            raw.lower(),
            normalized.lower(),
            path.name.lower(),
            path.stem.lower(),
        ]
    )


def with_rosetta_description_suffix_stripped(value: str) -> list[str]:
    values = [value]
    match = re.fullmatch(r"(.+)_\d{4}", value)
    if match:
        values.append(match.group(1))
    return values


def description_keys(description: str) -> list[str]:
    normalized = description.replace("\\", "/").strip()
    path = Path(normalized)
    stem = path.stem if path.suffix else normalized
    values = []
    for value in (description, normalized, path.name, stem):
        if not value:
            continue
        values.extend(with_rosetta_description_suffix_stripped(value))
    values.extend(value.lower() for value in list(values))
    return unique(values)


def attach_structure_paths(rows: list[dict[str, Any]], structure_list: Path | None) -> None:
    if structure_list is None or not structure_list.is_file():
        for row in rows:
            row["structure_path"] = ""
        return

    index: dict[str, str] = {}
    source_paths: list[tuple[str, str, str]] = []
    with structure_list.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            path = Path(text)
            source_paths.append((path.stem.lower(), path.name.lower(), text))
            for key in path_keys(path):
                index.setdefault(key, text)

    for row_index, row in enumerate(rows):
        description = str(row.get("description", ""))
        structure_path = ""
        for key in description_keys(description):
            if key in index:
                structure_path = index[key]
                break
        if not structure_path and description:
            candidates = []
            desc_keys = {key.lower() for key in description_keys(description) if key}
            for stem, name, text in source_paths:
                if any(stem.endswith(key) or key.endswith(stem) or name.endswith(key) for key in desc_keys):
                    candidates.append(text)
            if len(set(candidates)) == 1:
                structure_path = candidates[0]
        if not structure_path and len(source_paths) == len(rows) == 1:
            structure_path = source_paths[row_index][2]
        row["structure_path"] = structure_path


def rank_from_text(value: Any) -> int | None:
    text = str(value or "").replace("\\", "/")
    match = re.search(r"(?:^|[/_.-])rank[_-]?0*([0-9]+)(?:$|[/_.-])", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def csv_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(number)


def load_af3_metrics(metrics_csv: Path) -> list[dict[str, Any]]:
    if not metrics_csv.is_file():
        die(f"AF3 metrics CSV not found: {metrics_csv}")

    metrics = []
    with metrics_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            row: dict[str, Any] = {}
            for source_column, target_column in AF3_METADATA_COLUMNS.items():
                if source_column not in raw_row:
                    continue
                if source_column in ("rank", "chain_count"):
                    row[target_column] = csv_int(raw_row.get(source_column))
                else:
                    row[target_column] = raw_row.get(source_column, "")
            for column in AF3_METRIC_COLUMNS:
                if column in raw_row:
                    row[column] = as_float(raw_row.get(column))
            metrics.append(row)
    return metrics


def merge_af3_metrics(rows: list[dict[str, Any]], metrics_csv: Path | None) -> tuple[list[str], int]:
    if metrics_csv is None:
        return [], 0

    metrics = load_af3_metrics(metrics_csv)
    if not metrics:
        return list(AF3_METADATA_COLUMNS.values()) + list(AF3_METRIC_COLUMNS), 0

    by_rank = {
        row["af3_rank"]: row
        for row in metrics
        if isinstance(row.get("af3_rank"), int)
    }
    merged_count = 0
    for row_index, row in enumerate(rows):
        rank = rank_from_text(row.get("structure_path")) or rank_from_text(row.get("description"))
        metric_row = by_rank.get(rank) if rank is not None else None
        if metric_row is None and len(metrics) == len(rows):
            metric_row = metrics[row_index]
        if metric_row is None:
            continue
        for column, value in metric_row.items():
            row[column] = value
        merged_count += 1

    return list(AF3_METADATA_COLUMNS.values()) + list(AF3_METRIC_COLUMNS), merged_count


def row_number(row: dict[str, Any], column: str) -> float | None:
    value = row.get(column)
    return value if isinstance(value, (int, float)) else None


def cutoff_tolerance(*values: float | None) -> float:
    finite_values = [abs(value) for value in values if isinstance(value, (int, float))]
    scale = max(finite_values, default=1.0)
    return max(1.0e-9, scale * 1.0e-12)


def passes_cutoffs(row: dict[str, Any], x_column: str, y_column: str, args: argparse.Namespace) -> bool:
    x_value = row_number(row, x_column)
    y_value = row_number(row, y_column)
    x_tolerance = cutoff_tolerance(x_value, args.x_min, args.x_max)
    y_tolerance = cutoff_tolerance(y_value, args.y_min, args.y_max)
    checks = (
        (x_value, args.x_min, lambda value, cutoff: value >= cutoff - x_tolerance),
        (x_value, args.x_max, lambda value, cutoff: value <= cutoff + x_tolerance),
        (y_value, args.y_min, lambda value, cutoff: value >= cutoff - y_tolerance),
        (y_value, args.y_max, lambda value, cutoff: value <= cutoff + y_tolerance),
    )
    for value, cutoff, predicate in checks:
        if cutoff is None:
            continue
        if value is None or not predicate(value, cutoff):
            return False
    return True


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str], x_column: str, y_column: str, args: argparse.Namespace) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output = {field: csv_value(row.get(field, "")) for field in fieldnames}
            output["selected"] = "1" if passes_cutoffs(row, x_column, y_column, args) else "0"
            writer.writerow(output)


def copy_selected(rows: list[dict[str, Any]], output_dir: Path, x_column: str, y_column: str, args: argparse.Namespace) -> int:
    selected_dir = output_dir / args.selected_dir
    selected_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    used_names: set[str] = set()
    for row in rows:
        if not passes_cutoffs(row, x_column, y_column, args):
            continue
        source_text = str(row.get("structure_path", ""))
        if not source_text:
            continue
        source = Path(source_text)
        if not source.is_file():
            continue
        copied += 1
        target_name = export_file_name(copied, source, args.rename)
        while target_name in used_names:
            copied += 1
            target_name = export_file_name(copied, source, args.rename)
        used_names.add(target_name)
        shutil.copy2(source, selected_dir / target_name)
    return copied


def export_file_name(index: int, source: Path, rename: bool) -> str:
    if rename:
        return f"{index}{source.suffix or '.pdb'}"
    basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name).lstrip(".")
    return f"{index:04d}_{basename or 'structure'}"


def public_row(row: dict[str, Any], header: list[str]) -> dict[str, Any]:
    output = {"_index": row.get("_index"), "structure_path": row.get("structure_path", "")}
    for column in header:
        value = row.get(column, "")
        if isinstance(value, float):
            output[column] = round(value, 8)
        else:
            output[column] = value
    return output


def display_labels(columns: list[str], x_column: str, y_column: str, args: argparse.Namespace) -> dict[str, str]:
    labels = {column: DISPLAY_LABELS[column] for column in columns if column in DISPLAY_LABELS}
    if args.x_label:
        labels[x_column] = args.x_label
    if args.y_label:
        labels[y_column] = args.y_label
    return labels


def build_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rosetta InterfaceAnalyzer Filter</title>
<style>
:root {
  color-scheme: light;
  --bg: #f7f7f5;
  --panel: #ffffff;
  --line: #d4d4d0;
  --text: #202124;
  --muted: #646660;
  --point: #737373;
  --selected: #e11d48;
  --axis: #2f302d;
}
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Arial, Helvetica, sans-serif;
}
main {
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px;
}
h1 {
  margin: 0 0 16px;
  font-size: 24px;
  font-weight: 700;
}
.controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.metric-controls,
.range-controls {
  display: grid;
  gap: 12px;
}
.metric-controls {
  grid-template-columns: repeat(2, minmax(150px, 1fr));
  align-content: start;
}
.action-controls {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: repeat(3, minmax(120px, 1fr));
  gap: 12px;
  margin-top: 8px;
}
label {
  display: grid;
  gap: 4px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
.range-control {
  grid-column: auto;
  gap: 8px;
}
.range-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.range-value {
  color: var(--text);
  font-weight: 700;
  text-transform: none;
}
.dual-slider {
  position: relative;
  height: 24px;
  --thumb-size: 16px;
  --thumb-radius: 8px;
  --track-height: 6px;
}
.range-track {
  position: absolute;
  left: var(--thumb-radius);
  right: var(--thumb-radius);
  top: 50%;
  height: var(--track-height);
  transform: translateY(-50%);
  border-radius: 999px;
  background: #e5e5e1;
}
.range-fill {
  position: absolute;
  top: 0;
  bottom: 0;
  border-radius: 999px;
  background: color-mix(in srgb, var(--selected) 70%, #ffffff);
}
.range-thumb {
  position: absolute;
  top: 50%;
  width: var(--thumb-size);
  height: var(--thumb-size);
  border: 2px solid var(--selected);
  border-radius: 50%;
  background: #ffffff;
  transform: translate(-50%, -50%);
  pointer-events: none;
  box-sizing: border-box;
}
select,
input,
button {
  min-height: 36px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  color: var(--text);
  font: inherit;
  padding: 6px 8px;
}
input[type="range"] {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: transparent;
  cursor: default;
  opacity: 0;
  pointer-events: none;
  appearance: none;
  -webkit-appearance: none;
}
input[type="range"]::-webkit-slider-thumb {
  width: var(--thumb-size);
  height: var(--thumb-size);
  cursor: default;
  appearance: none;
  -webkit-appearance: none;
}
input[type="range"]::-moz-range-thumb {
  width: var(--thumb-size);
  height: var(--thumb-size);
  border: 0;
  cursor: default;
}
input[type="range"]::-webkit-slider-runnable-track {
  height: var(--track-height);
  background: transparent;
}
input[type="range"]::-moz-range-track {
  height: var(--track-height);
  background: transparent;
}
button {
  cursor: pointer;
  font-weight: 700;
}
.plot-wrap {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}
#plot {
  display: block;
  width: 100%;
  height: 560px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin: 14px 0;
  color: var(--muted);
}
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
th,
td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  font-size: 12px;
  white-space: nowrap;
}
th {
  color: var(--muted);
  font-weight: 700;
}
@media (max-width: 820px) {
  main {
    padding: 16px;
  }
  .controls {
    grid-template-columns: 1fr;
  }
  .metric-controls,
  .range-controls {
    grid-column: 1 / -1;
  }
  .action-controls {
    grid-template-columns: 1fr;
  }
  #plot {
    height: 440px;
  }
}
</style>
</head>
<body>
<main>
  <h1>Rosetta InterfaceAnalyzer Filter</h1>
  <section class="controls">
    <div class="metric-controls">
      <label>X Metric<select id="xColumn"></select></label>
      <label>Y Metric<select id="yColumn"></select></label>
      <div class="action-controls">
        <button id="reset" type="button">Reset</button>
        <button id="applyFilter" type="button">Apply</button>
        <button id="exportFiles" type="button">Export Selected Files</button>
      </div>
    </div>
    <div class="range-controls">
      <label class="range-control">
        <span class="range-head"><span>X Range</span><span class="range-value"><output id="xMinValue"></output> - <output id="xMaxValue"></output></span></span>
        <span id="xSlider" class="dual-slider" data-axis="x">
          <span class="range-track"><span id="xRangeFill" class="range-fill"></span></span>
          <span id="xMinThumb" class="range-thumb"></span>
          <span id="xMaxThumb" class="range-thumb"></span>
          <input id="xMin" type="range" aria-label="X minimum cutoff">
          <input id="xMax" type="range" aria-label="X maximum cutoff">
        </span>
      </label>
      <label class="range-control">
        <span class="range-head"><span>Y Range</span><span class="range-value"><output id="yMinValue"></output> - <output id="yMaxValue"></output></span></span>
        <span id="ySlider" class="dual-slider" data-axis="y">
          <span class="range-track"><span id="yRangeFill" class="range-fill"></span></span>
          <span id="yMinThumb" class="range-thumb"></span>
          <span id="yMaxThumb" class="range-thumb"></span>
          <input id="yMin" type="range" aria-label="Y minimum cutoff">
          <input id="yMax" type="range" aria-label="Y maximum cutoff">
        </span>
      </label>
    </div>
  </section>
  <section class="plot-wrap">
    <svg id="plot" role="img" aria-label="Interactive Rosetta score scatter plot"></svg>
  </section>
  <div class="status">
    <span id="count"></span>
    <span id="scorefile"></span>
  </div>
  <div style="overflow:auto">
    <table>
      <thead id="tableHead"></thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>
</main>
<script>
const payload = __PAYLOAD__;
const rows = Array.isArray(payload.rows) ? payload.rows : [];
const columns = resolveNumericColumns(payload.numericColumns, rows);
const tableColumns = resolveTableColumns(payload.tableColumns, rows);
const defaults = payload.defaults || {};
const displayLabels = payload.displayLabels || {};
const svg = document.getElementById("plot");
const controls = {
  xColumn: document.getElementById("xColumn"),
  yColumn: document.getElementById("yColumn"),
  xMin: document.getElementById("xMin"),
  xMax: document.getElementById("xMax"),
  yMin: document.getElementById("yMin"),
  yMax: document.getElementById("yMax"),
};
const cutoffOutputs = {
  xMin: document.getElementById("xMinValue"),
  xMax: document.getElementById("xMaxValue"),
  yMin: document.getElementById("yMinValue"),
  yMax: document.getElementById("yMaxValue"),
};
const rangeFills = {
  x: document.getElementById("xRangeFill"),
  y: document.getElementById("yRangeFill"),
};
const rangeThumbs = {
  xMin: document.getElementById("xMinThumb"),
  xMax: document.getElementById("xMaxThumb"),
  yMin: document.getElementById("yMinThumb"),
  yMax: document.getElementById("yMaxThumb"),
};
const rangeSliders = {
  x: document.getElementById("xSlider"),
  y: document.getElementById("ySlider"),
};
const plotGeometry = {
  width: 980,
  height: 560,
  margin: {top: 24, right: 28, bottom: 62, left: 76},
};
const boundaryTolerancePixels = 5;
let activeRangeHandle = null;
let activeRowKeys = new Set(rows.map(rowKey));
let appliedFilterCount = 0;
const ns = "http://www.w3.org/2000/svg";

function makeSvg(name, attrs = {}) {
  const node = document.createElementNS(ns, name);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, value);
  }
  return node;
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function rowKey(row) {
  return String(row._index);
}

function isActiveRow(row) {
  return activeRowKeys.has(rowKey(row));
}

function resolveNumericColumns(providedColumns, dataRows) {
  if (Array.isArray(providedColumns) && providedColumns.length > 0) {
    return providedColumns.filter(column => typeof column === "string" && column !== "");
  }
  const ignored = new Set(["_index", "description", "structure_path", "selected"]);
  const discovered = [];
  const seen = new Set();
  for (const row of dataRows) {
    for (const [key, value] of Object.entries(row)) {
      if (ignored.has(key) || seen.has(key)) {
        continue;
      }
      if (finite(value) !== null) {
        seen.add(key);
        discovered.push(key);
      }
    }
  }
  return discovered;
}

function resolveTableColumns(providedColumns, dataRows) {
  if (Array.isArray(providedColumns) && providedColumns.length > 0) {
    return providedColumns.filter(column => typeof column === "string" && column !== "");
  }
  const firstRow = dataRows[0] || {};
  return Object.keys(firstRow).filter(column => column !== "_index");
}

function inputNumber(id) {
  const value = controls[id].value.trim();
  if (value === "") {
    return null;
  }
  return finite(value);
}

function formatNumber(value) {
  const number = finite(value);
  if (number === null) {
    return "";
  }
  return Math.abs(number) >= 1000 || Math.abs(number) < 0.01
    ? number.toExponential(3)
    : number.toFixed(3).replace(/\\.?0+$/, "");
}

function metricLabel(column) {
  return column;
}

function axisLabel(column) {
  const description = displayLabels[column];
  return description && description !== column ? `${column} - ${description}` : column;
}

function axisLabelLines(column) {
  const description = displayLabels[column];
  return description && description !== column ? [column, description] : [column];
}

function appendMultilineText(parent, lines, attributes, lineGap = 17) {
  const label = makeSvg("text", attributes);
  const centerOffset = -((lines.length - 1) * lineGap) / 2;
  lines.forEach((line, index) => {
    const span = makeSvg("tspan", {
      x: attributes.x,
      dy: index === 0 ? centerOffset : lineGap,
    });
    span.textContent = line;
    label.appendChild(span);
  });
  parent.appendChild(label);
  return label;
}

function cutoffTolerance(axis) {
  const minId = axis === "x" ? "xMin" : "yMin";
  const maxId = axis === "x" ? "xMax" : "yMax";
  const minValue = finite(controls[minId].min);
  const maxValue = finite(controls[minId].max);
  const step = finite(controls[minId].step);
  const span = minValue !== null && maxValue !== null ? Math.abs(maxValue - minValue) : 1;
  const plotPixels = axis === "x"
    ? plotGeometry.width - plotGeometry.margin.left - plotGeometry.margin.right
    : plotGeometry.height - plotGeometry.margin.top - plotGeometry.margin.bottom;
  const stepTolerance = step !== null && step > 0 ? step * 0.51 : 0;
  const visualTolerance = plotPixels > 0 ? (span * boundaryTolerancePixels) / plotPixels : 0;
  return Math.max(1e-9, span * 1e-12, stepTolerance, visualTolerance);
}

function passesCurrentCutoffs(row) {
  const x = finite(row[controls.xColumn.value]);
  const y = finite(row[controls.yColumn.value]);
  const xMin = inputNumber("xMin");
  const xMax = inputNumber("xMax");
  const yMin = inputNumber("yMin");
  const yMax = inputNumber("yMax");
  const xTolerance = cutoffTolerance("x");
  const yTolerance = cutoffTolerance("y");
  if (x === null || y === null) return false;
  if (xMin !== null && x < xMin - xTolerance) return false;
  if (xMax !== null && x > xMax + xTolerance) return false;
  if (yMin !== null && y < yMin - yTolerance) return false;
  if (yMax !== null && y > yMax + yTolerance) return false;
  return true;
}

function passes(row) {
  return isActiveRow(row) && passesCurrentCutoffs(row);
}

function scale(value, min, max, start, end) {
  return start + ((value - min) / (max - min)) * (end - start);
}

function extent(values) {
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    const pad = Math.abs(min || 1) * 0.05;
    min -= pad;
    max += pad;
  }
  const pad = (max - min) * 0.06;
  return [min - pad, max + pad];
}

function dataExtent(column) {
  const values = rows.map(row => finite(row[column])).filter(value => value !== null);
  if (values.length === 0) {
    return [0, 1];
  }
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    const pad = Math.abs(min || 1) * 0.05;
    min -= pad;
    max += pad;
  }
  return [min, max];
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function sliderStep(min, max) {
  const span = Math.abs(max - min);
  if (!Number.isFinite(span) || span === 0) {
    return 1;
  }
  return span / 1000;
}

function setSlider(id, min, max, value) {
  const slider = controls[id];
  slider.min = String(min);
  slider.max = String(max);
  slider.step = String(sliderStep(min, max));
  slider.value = String(clamp(value, min, max));
}

function updateCutoffOutputs() {
  for (const id of ["xMin", "xMax", "yMin", "yMax"]) {
    cutoffOutputs[id].textContent = formatNumber(controls[id].value);
  }
  updateRangeFill("x");
  updateRangeFill("y");
}

function sliderPercent(id) {
  const slider = controls[id];
  const min = finite(slider.min);
  const max = finite(slider.max);
  const value = finite(slider.value);
  if (min === null || max === null || value === null || min === max) {
    return 0;
  }
  return clamp(((value - min) / (max - min)) * 100, 0, 100);
}

function updateRangeFill(axis) {
  const minId = axis === "x" ? "xMin" : "yMin";
  const maxId = axis === "x" ? "xMax" : "yMax";
  const low = Math.min(sliderPercent(minId), sliderPercent(maxId));
  const high = Math.max(sliderPercent(minId), sliderPercent(maxId));
  rangeFills[axis].style.left = `${low}%`;
  rangeFills[axis].style.right = `${100 - high}%`;
  rangeThumbs[minId].style.left = `${sliderPercent(minId)}%`;
  rangeThumbs[maxId].style.left = `${sliderPercent(maxId)}%`;
}

function valueFromPointer(event, axis) {
  const slider = rangeSliders[axis];
  const minId = axis === "x" ? "xMin" : "yMin";
  const rect = slider.getBoundingClientRect();
  const min = finite(controls[minId].min);
  const max = finite(controls[minId].max);
  if (min === null || max === null || rect.width <= 0) {
    return null;
  }
  const percent = clamp((event.clientX - rect.left) / rect.width, 0, 1);
  return min + (max - min) * percent;
}

function setHandleValue(id, value) {
  const slider = controls[id];
  const min = finite(slider.min);
  const max = finite(slider.max);
  const step = finite(slider.step);
  if (min === null || max === null || value === null) {
    return;
  }
  let next = clamp(value, min, max);
  if (step !== null && step > 0) {
    next = min + Math.round((next - min) / step) * step;
  }
  slider.value = String(clamp(next, min, max));
}

function nearestHandle(axis, value) {
  const minId = axis === "x" ? "xMin" : "yMin";
  const maxId = axis === "x" ? "xMax" : "yMax";
  const minDistance = Math.abs((inputNumber(minId) ?? 0) - value);
  const maxDistance = Math.abs((inputNumber(maxId) ?? 0) - value);
  return minDistance <= maxDistance ? minId : maxId;
}

function beginRangeDrag(event, axis) {
  if (controls.xMin.disabled) {
    return;
  }
  event.preventDefault();
  const value = valueFromPointer(event, axis);
  if (value === null) {
    return;
  }
  activeRangeHandle = nearestHandle(axis, value);
  rangeSliders[axis].setPointerCapture(event.pointerId);
  setHandleValue(activeRangeHandle, value);
  enforceCutoffOrder(activeRangeHandle);
  render();
}

function moveRangeDrag(event, axis) {
  if (!activeRangeHandle) {
    return;
  }
  event.preventDefault();
  const value = valueFromPointer(event, axis);
  setHandleValue(activeRangeHandle, value);
  enforceCutoffOrder(activeRangeHandle);
  render();
}

function endRangeDrag(event, axis) {
  if (activeRangeHandle && rangeSliders[axis].hasPointerCapture && rangeSliders[axis].hasPointerCapture(event.pointerId)) {
    rangeSliders[axis].releasePointerCapture(event.pointerId);
  }
  activeRangeHandle = null;
}

function enforceCutoffOrder(changedId) {
  const xMin = inputNumber("xMin");
  const xMax = inputNumber("xMax");
  const yMin = inputNumber("yMin");
  const yMax = inputNumber("yMax");
  if (xMin !== null && xMax !== null && xMin > xMax) {
    if (changedId === "xMin") {
      controls.xMax.value = controls.xMin.value;
    } else {
      controls.xMin.value = controls.xMax.value;
    }
  }
  if (yMin !== null && yMax !== null && yMin > yMax) {
    if (changedId === "yMin") {
      controls.yMax.value = controls.yMin.value;
    } else {
      controls.yMin.value = controls.yMax.value;
    }
  }
  updateCutoffOutputs();
}

function configureCutoffSliders(useDefaults) {
  const xColumn = controls.xColumn.value;
  const yColumn = controls.yColumn.value;
  const [xDataMin, xDataMax] = dataExtent(xColumn);
  const [yDataMin, yDataMax] = dataExtent(yColumn);
  const xMinValue = useDefaults && defaults.xMin !== null && defaults.xMin !== undefined ? defaults.xMin : xDataMin;
  const xMaxValue = useDefaults && defaults.xMax !== null && defaults.xMax !== undefined ? defaults.xMax : xDataMax;
  const yMinValue = useDefaults && defaults.yMin !== null && defaults.yMin !== undefined ? defaults.yMin : yDataMin;
  const yMaxValue = useDefaults && defaults.yMax !== null && defaults.yMax !== undefined ? defaults.yMax : yDataMax;
  setSlider("xMin", xDataMin, xDataMax, xMinValue);
  setSlider("xMax", xDataMin, xDataMax, xMaxValue);
  setSlider("yMin", yDataMin, yDataMax, yMinValue);
  setSlider("yMax", yDataMin, yDataMax, yMaxValue);
  enforceCutoffOrder("");
}

function drawAxis(width, height, margin, xMin, xMax, yMin, yMax) {
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  svg.appendChild(makeSvg("line", {x1: margin.left, y1: height - margin.bottom, x2: width - margin.right, y2: height - margin.bottom, stroke: "var(--axis)", "stroke-width": 1.5}));
  svg.appendChild(makeSvg("line", {x1: margin.left, y1: margin.top, x2: margin.left, y2: height - margin.bottom, stroke: "var(--axis)", "stroke-width": 1.5}));
  for (let i = 0; i <= 5; i += 1) {
    const xValue = xMin + ((xMax - xMin) * i) / 5;
    const x = margin.left + (plotWidth * i) / 5;
    svg.appendChild(makeSvg("line", {x1: x, y1: margin.top, x2: x, y2: height - margin.bottom, stroke: "#ececea"}));
    svg.appendChild(makeSvg("text", {x, y: height - margin.bottom + 22, "text-anchor": "middle", fill: "var(--muted)", "font-size": 12})).textContent = formatNumber(xValue);
    const yValue = yMin + ((yMax - yMin) * i) / 5;
    const y = height - margin.bottom - (plotHeight * i) / 5;
    svg.appendChild(makeSvg("line", {x1: margin.left, y1: y, x2: width - margin.right, y2: y, stroke: "#ececea"}));
    svg.appendChild(makeSvg("text", {x: margin.left - 10, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 12})).textContent = formatNumber(yValue);
  }
  appendMultilineText(
    svg,
    axisLabelLines(controls.xColumn.value),
    {x: margin.left + plotWidth / 2, y: height - 20, "text-anchor": "middle", fill: "var(--axis)", "font-size": 15, "font-weight": 700},
  );
  const yLabelX = 20;
  const yLabelY = margin.top + plotHeight / 2;
  appendMultilineText(
    svg,
    axisLabelLines(controls.yColumn.value),
    {x: yLabelX, y: yLabelY, "text-anchor": "middle", fill: "var(--axis)", "font-size": 15, "font-weight": 700, transform: `rotate(-90 ${yLabelX} ${yLabelY})`},
  );
}

function drawCutoffRegion(width, height, margin, xMin, xMax, yMin, yMax) {
  const xTolerance = cutoffTolerance("x");
  const yTolerance = cutoffTolerance("y");
  const xLow = clamp((inputNumber("xMin") ?? xMin) - xTolerance, xMin, xMax);
  const xHigh = clamp((inputNumber("xMax") ?? xMax) + xTolerance, xMin, xMax);
  const yLow = clamp((inputNumber("yMin") ?? yMin) - yTolerance, yMin, yMax);
  const yHigh = clamp((inputNumber("yMax") ?? yMax) + yTolerance, yMin, yMax);
  const left = scale(Math.min(xLow, xHigh), xMin, xMax, margin.left, width - margin.right);
  const right = scale(Math.max(xLow, xHigh), xMin, xMax, margin.left, width - margin.right);
  const top = scale(Math.max(yLow, yHigh), yMin, yMax, height - margin.bottom, margin.top);
  const bottom = scale(Math.min(yLow, yHigh), yMin, yMax, height - margin.bottom, margin.top);
  svg.appendChild(makeSvg("rect", {
    x: left,
    y: top,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
    fill: "var(--selected)",
    opacity: 0.09,
    stroke: "var(--selected)",
    "stroke-width": 1.5,
    "stroke-dasharray": "6 4",
  }));
}

function renderTable(selectedRows) {
  const head = document.getElementById("tableHead");
  const body = document.getElementById("tableBody");
  head.innerHTML = "";
  body.innerHTML = "";
  const headerRow = document.createElement("tr");
  for (const column of tableColumns) {
    const th = document.createElement("th");
    th.textContent = column;
    headerRow.appendChild(th);
  }
  head.appendChild(headerRow);
  for (const row of selectedRows.slice(0, 100)) {
    const tr = document.createElement("tr");
    for (const column of tableColumns) {
      const td = document.createElement("td");
      const value = row[column] ?? "";
      td.textContent = typeof value === "number" ? formatNumber(value) : value;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function render() {
  svg.innerHTML = "";
  const width = plotGeometry.width;
  const height = plotGeometry.height;
  const margin = plotGeometry.margin;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const xColumn = controls.xColumn.value;
  const yColumn = controls.yColumn.value;
  if (!xColumn || !yColumn) {
    svg.appendChild(makeSvg("text", {x: width / 2, y: height / 2, "text-anchor": "middle", fill: "var(--muted)", "font-size": 16})).textContent = "No numeric score columns were found in the scorefile.";
    document.getElementById("count").textContent = `${rows.length} total row(s)`;
    document.getElementById("scorefile").textContent = payload.scorefile;
    renderTable([]);
    return;
  }
  const activeTotal = rows.filter(row => isActiveRow(row)).length;
  const validRows = rows.filter(row => finite(row[xColumn]) !== null && finite(row[yColumn]) !== null);
  if (validRows.length === 0) {
    svg.appendChild(makeSvg("text", {x: width / 2, y: height / 2, "text-anchor": "middle", fill: "var(--muted)", "font-size": 16})).textContent = "No numeric rows for the selected axes.";
    document.getElementById("count").textContent = `0 selected / 0 plotted / ${activeTotal} retained / ${rows.length} total`;
    document.getElementById("scorefile").textContent = payload.scorefile;
    renderTable([]);
    return;
  }
  const [xMin, xMax] = extent(validRows.map(row => finite(row[xColumn])));
  const [yMin, yMax] = extent(validRows.map(row => finite(row[yColumn])));
  drawAxis(width, height, margin, xMin, xMax, yMin, yMax);
  drawCutoffRegion(width, height, margin, xMin, xMax, yMin, yMax);
  const selectedRows = [];
  for (const row of validRows) {
    const retained = isActiveRow(row);
    const isSelected = retained && passesCurrentCutoffs(row);
    if (isSelected) selectedRows.push(row);
    const x = scale(finite(row[xColumn]), xMin, xMax, margin.left, width - margin.right);
    const y = scale(finite(row[yColumn]), yMin, yMax, height - margin.bottom, margin.top);
    const circle = makeSvg("circle", {
      cx: x,
      cy: y,
      r: isSelected ? 4 : 3,
      fill: isSelected ? "var(--selected)" : "var(--point)",
      opacity: isSelected ? 0.92 : (retained ? 0.62 : 0.28),
    });
    const title = makeSvg("title");
    const state = retained ? "retained" : "filtered out";
    title.textContent = `${row.description || row._index}\n${axisLabel(xColumn)}: ${formatNumber(row[xColumn])}\n${axisLabel(yColumn)}: ${formatNumber(row[yColumn])}\n${state}`;
    circle.appendChild(title);
    svg.appendChild(circle);
  }
  const removedCount = rows.length - activeTotal;
  const appliedText = appliedFilterCount > 0 ? ` / ${removedCount} filtered out / ${appliedFilterCount} applied` : "";
  document.getElementById("count").textContent = `${selectedRows.length} selected / ${validRows.length} plotted / ${activeTotal} retained / ${rows.length} total${appliedText}`;
  document.getElementById("scorefile").textContent = payload.scorefile;
  renderTable(selectedRows);
}

function selectedRowsForCurrentAxes() {
  return rows.filter(row => finite(row[controls.xColumn.value]) !== null && finite(row[controls.yColumn.value]) !== null && passes(row));
}

function applyCurrentFilter() {
  const selectedRows = selectedRowsForCurrentAxes();
  if (selectedRows.length === 0) {
    window.alert("No selected rows are available to apply.");
    return;
  }
  activeRowKeys = new Set(selectedRows.map(rowKey));
  appliedFilterCount += 1;
  render();
}

async function exportSelectedFiles() {
  const selectedRows = selectedRowsForCurrentAxes();
  const indices = selectedRows.map(row => row._index).filter(index => Number.isInteger(Number(index)));
  if (indices.length === 0) {
    window.alert("No selected rows are available for export.");
    return;
  }
  const button = document.getElementById("exportFiles");
  button.disabled = true;
  button.textContent = "Exporting...";
  try {
    const response = await fetch("/api/export-selected", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({indices, target_dir: "selected_structures", rename: Boolean(payload.exportRename)}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || `Export failed with status ${response.status}`);
    }
    let message = `Exported ${result.copied} structure file(s) and ${result.csv_rows} CSV row(s) to ${result.target_dir}`;
    if (result.af3_workspace_removed) {
      message += "\\nRemoved af3_top_for_rosetta workspace.";
    } else if (result.af3_cleanup_error) {
      message += `\\nAF3 workspace cleanup was skipped: ${result.af3_cleanup_error}`;
    }
    window.alert(message);
  } catch (error) {
    window.alert("Direct export requires rosetta_filter_server.py. Open this report through the server URL instead of file/SFTP preview.");
  } finally {
    button.disabled = false;
    button.textContent = "Export Selected Files";
  }
}

function resetControls() {
  activeRowKeys = new Set(rows.map(rowKey));
  appliedFilterCount = 0;
  const fallbackX = columns[0] || "";
  const fallbackY = columns.includes("dG_separated/dSASAx100")
    ? "dG_separated/dSASAx100"
    : columns.includes("dG_separated")
    ? "dG_separated"
    : columns[Math.min(1, Math.max(columns.length - 1, 0))] || fallbackX;
  controls.xColumn.value = columns.includes(defaults.xColumn) ? defaults.xColumn : fallbackX;
  controls.yColumn.value = columns.includes(defaults.yColumn) ? defaults.yColumn : fallbackY;
  configureCutoffSliders(true);
  render();
}

for (const column of columns) {
  const xOption = document.createElement("option");
  xOption.value = column;
  xOption.textContent = metricLabel(column);
  controls.xColumn.appendChild(xOption);
  const yOption = document.createElement("option");
  yOption.value = column;
  yOption.textContent = metricLabel(column);
  controls.yColumn.appendChild(yOption);
}
const hasMetricColumns = columns.length > 0;
controls.xColumn.disabled = !hasMetricColumns;
controls.yColumn.disabled = !hasMetricColumns;
document.getElementById("applyFilter").disabled = !hasMetricColumns;
for (const id of ["xMin", "xMax", "yMin", "yMax"]) {
  controls[id].disabled = !hasMetricColumns;
}
for (const axis of ["x", "y"]) {
  rangeSliders[axis].addEventListener("pointerdown", event => beginRangeDrag(event, axis));
  rangeSliders[axis].addEventListener("pointermove", event => moveRangeDrag(event, axis));
  rangeSliders[axis].addEventListener("pointerup", event => endRangeDrag(event, axis));
  rangeSliders[axis].addEventListener("pointercancel", event => endRangeDrag(event, axis));
}
controls.xColumn.addEventListener("change", () => {
  configureCutoffSliders(false);
  render();
});
controls.yColumn.addEventListener("change", () => {
  configureCutoffSliders(false);
  render();
});
document.getElementById("reset").addEventListener("click", resetControls);
document.getElementById("applyFilter").addEventListener("click", applyCurrentFilter);
document.getElementById("exportFiles").addEventListener("click", exportSelectedFiles);
resetControls();
</script>
</body>
</html>
"""
    return template.replace("__PAYLOAD__", payload_json)


def write_settings(
    path: Path,
    args: argparse.Namespace,
    scorefile: Path,
    x_column: str,
    y_column: str,
    rows: list[dict[str, Any]],
    labels: dict[str, str],
    af3_metrics_csv: Path | None,
    af3_merged_count: int,
) -> None:
    settings = {
        "scorefile": str(scorefile),
        "af3_metrics_csv": "" if af3_metrics_csv is None else str(af3_metrics_csv),
        "af3_merged_rows": af3_merged_count,
        "row_count": len(rows),
        "x_column": x_column,
        "y_column": y_column,
        "display_labels": {
            "x": labels.get(x_column, x_column),
            "y": labels.get(y_column, y_column),
        },
        "cutoffs": {
            "x_min": args.x_min,
            "x_max": args.x_max,
            "y_min": args.y_min,
            "y_max": args.y_max,
        },
        "html_report": args.html_name,
        "csv_report": args.csv_name,
        "selected_csv_report": args.selected_csv_name,
        "rename_exported_structures": args.rename,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")


def main() -> int:
    args = parse_args()
    scorefile = Path(args.scorefile).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    structure_list = Path(args.structure_list).expanduser().resolve() if args.structure_list else None
    af3_metrics_csv = Path(args.af3_metrics_csv).expanduser().resolve() if args.af3_metrics_csv else None

    header, rows = parse_scorefile(scorefile)
    attach_structure_paths(rows, structure_list)
    af3_columns, af3_merged_count = merge_af3_metrics(rows, af3_metrics_csv)
    header = unique(header + af3_columns)
    columns = numeric_columns(header, rows)
    x_column = choose_column(args.x_column, DEFAULT_X_CANDIDATES, columns, "x-axis")
    y_column = choose_column(args.y_column, DEFAULT_Y_CANDIDATES, columns, "y-axis")

    fieldnames = unique(["selected", "_index", "structure_path"] + header)
    selected_rows = [row for row in rows if passes_cutoffs(row, x_column, y_column, args)]
    write_csv(output_dir / args.csv_name, rows, fieldnames, x_column, y_column, args)
    write_csv(output_dir / args.selected_csv_name, selected_rows, fieldnames, x_column, y_column, args)
    if args.copy_selected:
        copied = copy_selected(rows, output_dir, x_column, y_column, args)
        print(f"Copied {copied} selected structure file(s).")

    table_columns = unique([
        "description",
        "structure_path",
        x_column,
        y_column,
        "dG_separated/dSASAx100",
        "dG_separated",
        "sc_value",
        "dSASA_int",
        "packstat",
        "total_score",
        "ranking_score",
        "iptm",
        "ptm",
        "pair_iptm",
        "pair_pae_min",
        "interface_score",
        "composite_score",
    ])
    table_columns = [column for column in table_columns if column in fieldnames or column == "structure_path"]
    labels = display_labels(columns, x_column, y_column, args)
    payload = {
        "scorefile": str(scorefile),
        "rows": [public_row(row, header) for row in rows],
        "numericColumns": columns,
        "tableColumns": table_columns,
        "displayLabels": labels,
        "exportRename": args.rename,
        "defaults": {
            "xColumn": x_column,
            "yColumn": y_column,
            "xMin": args.x_min,
            "xMax": args.x_max,
            "yMin": args.y_min,
            "yMax": args.y_max,
        },
    }
    (output_dir / args.html_name).write_text(build_html(payload), encoding="utf-8")
    write_settings(
        output_dir / args.settings_name,
        args,
        scorefile,
        x_column,
        y_column,
        rows,
        labels,
        af3_metrics_csv,
        af3_merged_count,
    )

    print(f"Parsed {len(rows)} Rosetta score row(s).")
    if af3_metrics_csv is not None:
        print(f"Merged AF3 metrics into {af3_merged_count} row(s).")
    print(f"Selected {len(selected_rows)} row(s) using command-line cutoffs.")
    print(f"CSV report: {output_dir / args.csv_name}")
    print(f"HTML report: {output_dir / args.html_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
