#!/usr/bin/env python3
"""
AlphaFold 3 prefilter helper for rosetta_filter.sh.

This helper collects top AF3 CIF results from summary confidence files before
Rosetta InterfaceAnalyzer runs. It is intentionally kept as a private helper
instead of a standalone user workflow.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SUMMARY_SUFFIX = "_summary_confidences.json"
CONFIDENCE_SUFFIX = "_confidences.json"
MODEL_SUFFIX = "_model.cif"
PAE_CAP_ANGSTROM = 30.0

PRESETS = {
    "none": {
        "max_clash": None,
        "min_ranking_score": None,
        "min_iptm": None,
        "min_pair_iptm": None,
        "max_pair_pae_min": None,
        "max_fraction_disordered": None,
    },
    "loose": {
        "max_clash": 0.0,
        "min_ranking_score": 0.30,
        "min_iptm": None,
        "min_pair_iptm": 0.30,
        "max_pair_pae_min": 25.0,
        "max_fraction_disordered": 0.50,
    },
    "balanced": {
        "max_clash": 0.0,
        "min_ranking_score": 0.40,
        "min_iptm": None,
        "min_pair_iptm": 0.50,
        "max_pair_pae_min": 15.0,
        "max_fraction_disordered": 0.30,
    },
    "strict": {
        "max_clash": 0.0,
        "min_ranking_score": 0.50,
        "min_iptm": None,
        "min_pair_iptm": 0.70,
        "max_pair_pae_min": 10.0,
        "max_fraction_disordered": 0.20,
    },
}

HIGHER_IS_BETTER = {
    "ranking_score": True,
    "iptm": True,
    "ptm": True,
    "pair_iptm": True,
    "pair_pae_min": False,
    "interface_score": True,
    "composite_score": True,
    "fraction_disordered": False,
    "has_clash": False,
}


@dataclass(frozen=True)
class PairMetric:
    label: str
    first_index: int
    second_index: int
    pair_iptm: float | None
    pair_pae_min: float | None
    interface_score: float | None


@dataclass
class ResultRecord:
    relative_id: str
    summary_path: Path
    model_path: Path | None
    confidence_path: Path | None
    chain_count: int
    selected_pair: str
    ranking_score: float | None
    iptm: float | None
    ptm: float | None
    pair_iptm: float | None
    pair_pae_min: float | None
    interface_score: float | None
    composite_score: float | None
    has_clash: float | None
    fraction_disordered: float | None
    status: str
    fail_reasons: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter and collect top AlphaFold 3 CIF results by summary confidence metrics.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-i", "--input-dir", required=True, help="AF3 output directory or a single AF3 job directory.")
    parser.add_argument("-o", "--output-dir", help="Directory where selected CIF and report files are written.")
    parser.add_argument("--top", type=int, default=10, help="Maximum number of passing results to collect. Use 0 for all.")
    parser.add_argument(
        "--include-samples",
        action="store_true",
        help="Include seed/sample subdirectories. By default only top-level job summaries are considered.",
    )
    parser.add_argument(
        "--samples-only",
        action="store_true",
        help="Evaluate only seed/sample subdirectories, skipping job-root summary files.",
    )
    parser.add_argument(
        "--pair",
        action="append",
        default=[],
        help=(
            "Restrict interface evaluation to a chain pair. Repeat as needed. "
            "Examples: A:B, A-C, 1:3. Numeric chain IDs are 1-based."
        ),
    )
    parser.add_argument(
        "--pair-reduce",
        choices=("best", "worst", "mean"),
        default=None,
        help=(
            "How to combine candidate chain pairs. The default is best for automatic pairs "
            "and worst when --pair is supplied."
        ),
    )
    parser.add_argument("--preset", choices=tuple(PRESETS), default="balanced", help="Default threshold set.")
    parser.add_argument("--min-ranking-score", type=float, help="Minimum ranking_score.")
    parser.add_argument("--min-iptm", type=float, help="Minimum global iptm.")
    parser.add_argument("--min-pair-iptm", type=float, help="Minimum selected interface chain_pair_iptm.")
    parser.add_argument("--max-pair-pae-min", type=float, help="Maximum selected interface chain_pair_pae_min.")
    parser.add_argument("--max-fraction-disordered", type=float, help="Maximum fraction_disordered.")
    parser.add_argument("--max-clash", type=float, help="Maximum has_clash value.")
    parser.add_argument(
        "--sort-by",
        choices=tuple(HIGHER_IS_BETTER),
        default="composite_score",
        help="Metric used to sort passing results.",
    )
    parser.add_argument(
        "--copy-mode",
        choices=("cif", "cif-summary", "bundle"),
        default="cif-summary",
        help="Files copied for each selected result. CIF files are written to cif/ and JSON files to json/.",
    )
    parser.add_argument(
        "--rename",
        action="store_true",
        help="Rename selected CIF files to OUTPUT_DIR_NAME_N.cif.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the output directory before writing new selected files and reports.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected results without copying files or writing reports.",
    )
    args = parser.parse_args()

    if args.top < 0:
        parser.error("--top must be 0 or a positive integer.")
    if args.samples_only:
        args.include_samples = True
    if not args.dry_run and not args.output_dir:
        parser.error("--output-dir is required unless --dry-run is used.")
    return args


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


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def chain_label(index: int) -> str:
    label = ""
    number = index
    while True:
        label = chr(ord("A") + (number % 26)) + label
        number = number // 26 - 1
        if number < 0:
            return label


def chain_index_from_label(label: str, chain_count: int) -> int:
    token = label.strip()
    if re.fullmatch(r"[0-9]+", token):
        index = int(token) - 1
    elif re.fullmatch(r"[A-Za-z]+", token):
        index = 0
        for char in token.upper():
            index = index * 26 + (ord(char) - ord("A") + 1)
        index -= 1
    else:
        die(f"Invalid chain reference: {label}")

    if index < 0 or index >= chain_count:
        die(f"Chain reference out of range for {chain_count} chain(s): {label}")
    return index


def parse_pair_spec(pair_spec: str, chain_count: int) -> tuple[int, int]:
    parts = re.split(r"\s*[:,-]\s*", pair_spec.strip())
    if len(parts) != 2 or not parts[0] or not parts[1]:
        die(f"Invalid --pair value: {pair_spec}")
    first = chain_index_from_label(parts[0], chain_count)
    second = chain_index_from_label(parts[1], chain_count)
    if first == second:
        die(f"Pair must contain two different chains: {pair_spec}")
    return tuple(sorted((first, second)))


def is_sample_dir(path: Path) -> bool:
    return re.fullmatch(r"seed-[^\\/]+_sample-[^\\/]+", path.name) is not None


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def discover_summary_files(
    input_dir: Path,
    output_dir: Path | None,
    include_samples: bool,
    samples_only: bool,
) -> list[Path]:
    summary_files = sorted(input_dir.rglob(f"*{SUMMARY_SUFFIX}"))
    discovered = []
    for summary_path in summary_files:
        if output_dir and output_dir.exists() and is_relative_to(summary_path, output_dir):
            continue
        summary_is_sample = is_sample_dir(summary_path.parent)
        if samples_only and not summary_is_sample:
            continue
        if not include_samples and summary_path.parent != input_dir and summary_is_sample:
            continue
        discovered.append(summary_path)
    return discovered


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        die(f"Invalid JSON in {path}: {exc}")
    except OSError as exc:
        die(f"Failed to read {path}: {exc}")
    if not isinstance(data, dict):
        die(f"Summary JSON must contain an object: {path}")
    return data


def matrix_value(matrix: Any, row: int, column: int) -> float | None:
    if not isinstance(matrix, list):
        return None
    if row >= len(matrix) or not isinstance(matrix[row], list):
        return None
    if column >= len(matrix[row]):
        return None
    return as_float(matrix[row][column])


def infer_chain_count(data: dict[str, Any]) -> int:
    for key in ("chain_ptm", "chain_iptm", "chain_pair_iptm", "chain_pair_pae_min"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return len(value)
    return 1


def interface_score(pair_iptm: float | None, pair_pae_min: float | None) -> float | None:
    if pair_iptm is None:
        return None
    if pair_pae_min is None:
        return pair_iptm
    pae_quality = clamp(1.0 - (pair_pae_min / PAE_CAP_ANGSTROM), 0.0, 1.0)
    return pair_iptm * pae_quality


def build_pair_metrics(
    data: dict[str, Any],
    chain_count: int,
    pair_specs: Iterable[str],
) -> list[PairMetric]:
    if chain_count < 2:
        return []

    if pair_specs:
        pairs = []
        seen = set()
        for pair_spec in pair_specs:
            pair = parse_pair_spec(pair_spec, chain_count)
            if pair not in seen:
                pairs.append(pair)
                seen.add(pair)
    else:
        pairs = [(first, second) for first in range(chain_count) for second in range(first + 1, chain_count)]

    pair_metrics = []
    iptm_matrix = data.get("chain_pair_iptm")
    pae_matrix = data.get("chain_pair_pae_min")
    for first, second in pairs:
        iptm_values = [
            value
            for value in (
                matrix_value(iptm_matrix, first, second),
                matrix_value(iptm_matrix, second, first),
            )
            if value is not None
        ]
        pae_values = [
            value
            for value in (
                matrix_value(pae_matrix, first, second),
                matrix_value(pae_matrix, second, first),
            )
            if value is not None
        ]
        pair_iptm = sum(iptm_values) / len(iptm_values) if iptm_values else None
        pair_pae_min = min(pae_values) if pae_values else None
        pair_metrics.append(
            PairMetric(
                label=f"{chain_label(first)}:{chain_label(second)}",
                first_index=first,
                second_index=second,
                pair_iptm=pair_iptm,
                pair_pae_min=pair_pae_min,
                interface_score=interface_score(pair_iptm, pair_pae_min),
            )
        )
    return pair_metrics


def numeric_sort_value(value: float | None, higher_is_better: bool) -> float:
    if value is None:
        return -math.inf if higher_is_better else math.inf
    return value


def select_pair_metric(pair_metrics: list[PairMetric], pair_reduce: str) -> tuple[str, float | None, float | None, float | None]:
    if not pair_metrics:
        return "", None, None, None

    if pair_reduce == "mean":
        pair_iptm_values = [metric.pair_iptm for metric in pair_metrics if metric.pair_iptm is not None]
        pair_pae_values = [metric.pair_pae_min for metric in pair_metrics if metric.pair_pae_min is not None]
        interface_values = [metric.interface_score for metric in pair_metrics if metric.interface_score is not None]
        pair_iptm = sum(pair_iptm_values) / len(pair_iptm_values) if pair_iptm_values else None
        pair_pae_min = sum(pair_pae_values) / len(pair_pae_values) if pair_pae_values else None
        score = sum(interface_values) / len(interface_values) if interface_values else None
        labels = ",".join(metric.label for metric in pair_metrics)
        return f"mean({labels})", pair_iptm, pair_pae_min, score

    higher_is_better = pair_reduce == "best"
    selected = sorted(
        pair_metrics,
        key=lambda metric: numeric_sort_value(metric.interface_score, higher_is_better),
        reverse=higher_is_better,
    )[0]
    return selected.label, selected.pair_iptm, selected.pair_pae_min, selected.interface_score


def composite_score(
    ranking_score: float | None,
    iptm: float | None,
    interface: float | None,
    has_clash: float | None,
    fraction_disordered: float | None,
) -> float | None:
    values = {
        "ranking_score": ranking_score,
        "iptm": iptm,
        "interface_score": interface,
    }
    if all(value is None for value in values.values()):
        return None

    score = 0.0
    total_weight = 0.0
    weights = {
        "ranking_score": 0.45,
        "interface_score": 0.35,
        "iptm": 0.20,
    }
    for key, weight in weights.items():
        value = values[key]
        if value is None:
            continue
        score += value * weight
        total_weight += weight

    if total_weight == 0.0:
        return None
    score /= total_weight

    if has_clash is not None and has_clash > 0:
        score -= min(has_clash, 1.0)
    if fraction_disordered is not None:
        score -= max(fraction_disordered - 0.30, 0.0) * 0.25
    return score


def find_model_path(summary_path: Path) -> Path | None:
    expected_name = None
    if summary_path.name.endswith(SUMMARY_SUFFIX):
        expected_name = summary_path.name[: -len(SUMMARY_SUFFIX)] + MODEL_SUFFIX
    if expected_name:
        expected_path = summary_path.with_name(expected_name)
        if expected_path.exists():
            return expected_path

    model_files = sorted(summary_path.parent.glob(f"*{MODEL_SUFFIX}"))
    if model_files:
        return model_files[0]
    cif_files = sorted(summary_path.parent.glob("*.cif"))
    if cif_files:
        return cif_files[0]
    return None


def find_confidence_path(summary_path: Path) -> Path | None:
    if not summary_path.name.endswith(SUMMARY_SUFFIX):
        return None
    confidence_path = summary_path.with_name(summary_path.name[: -len(SUMMARY_SUFFIX)] + CONFIDENCE_SUFFIX)
    return confidence_path if confidence_path.exists() else None


def make_relative_id(summary_path: Path, input_dir: Path) -> str:
    try:
        relative_parent = summary_path.parent.resolve().relative_to(input_dir.resolve())
    except ValueError:
        relative_parent = Path(summary_path.parent.name)
    if str(relative_parent) == ".":
        return summary_path.name[: -len(SUMMARY_SUFFIX)] if summary_path.name.endswith(SUMMARY_SUFFIX) else summary_path.stem
    return str(relative_parent).replace("\\", "/")


def make_record(summary_path: Path, input_dir: Path, pair_specs: list[str], pair_reduce: str) -> ResultRecord:
    data = load_json(summary_path)
    chain_count = infer_chain_count(data)
    pair_metrics = build_pair_metrics(data, chain_count, pair_specs)
    selected_pair, pair_iptm, pair_pae_min, selected_interface_score = select_pair_metric(pair_metrics, pair_reduce)
    ranking_score = as_float(data.get("ranking_score"))
    iptm = as_float(data.get("iptm"))
    ptm = as_float(data.get("ptm"))
    has_clash = as_float(data.get("has_clash"))
    fraction_disordered = as_float(data.get("fraction_disordered"))
    score = composite_score(ranking_score, iptm, selected_interface_score, has_clash, fraction_disordered)
    return ResultRecord(
        relative_id=make_relative_id(summary_path, input_dir),
        summary_path=summary_path,
        model_path=find_model_path(summary_path),
        confidence_path=find_confidence_path(summary_path),
        chain_count=chain_count,
        selected_pair=selected_pair,
        ranking_score=ranking_score,
        iptm=iptm,
        ptm=ptm,
        pair_iptm=pair_iptm,
        pair_pae_min=pair_pae_min,
        interface_score=selected_interface_score,
        composite_score=score,
        has_clash=has_clash,
        fraction_disordered=fraction_disordered,
        status="pass",
        fail_reasons=[],
    )


def threshold_value(args: argparse.Namespace, name: str) -> float | None:
    override = getattr(args, name)
    if override is not None:
        return override
    return PRESETS[args.preset][name]


def evaluate_record(record: ResultRecord, args: argparse.Namespace) -> None:
    checks = [
        ("ranking_score", record.ranking_score, threshold_value(args, "min_ranking_score"), "min"),
        ("iptm", record.iptm, threshold_value(args, "min_iptm"), "min"),
        ("pair_iptm", record.pair_iptm, threshold_value(args, "min_pair_iptm"), "min"),
        ("pair_pae_min", record.pair_pae_min, threshold_value(args, "max_pair_pae_min"), "max"),
        (
            "fraction_disordered",
            record.fraction_disordered,
            threshold_value(args, "max_fraction_disordered"),
            "max",
        ),
        ("has_clash", record.has_clash, threshold_value(args, "max_clash"), "max"),
    ]
    reasons = []
    for metric_name, value, threshold, direction in checks:
        if threshold is None:
            continue
        if value is None:
            reasons.append(f"missing {metric_name}")
            continue
        if direction == "min" and value < threshold:
            reasons.append(f"{metric_name} {value:.4g} < {threshold:.4g}")
        elif direction == "max" and value > threshold:
            reasons.append(f"{metric_name} {value:.4g} > {threshold:.4g}")

    if record.model_path is None:
        reasons.append("missing model CIF")

    record.fail_reasons = reasons
    record.status = "pass" if not reasons else "fail"


def metric_for_sort(record: ResultRecord, metric_name: str) -> float | None:
    return getattr(record, metric_name)


def sort_records(records: list[ResultRecord], sort_by: str) -> list[ResultRecord]:
    higher_is_better = HIGHER_IS_BETTER[sort_by]
    return sorted(
        records,
        key=lambda record: numeric_sort_value(metric_for_sort(record, sort_by), higher_is_better),
        reverse=higher_is_better,
    )


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("._")
    return slug or "result"


def copy_selected_files(records: list[ResultRecord], output_dir: Path, copy_mode: str, rename: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cif_dir = output_dir / "cif"
    json_dir = output_dir / "json"
    cif_dir.mkdir(parents=True, exist_ok=True)
    if copy_mode in ("cif-summary", "bundle"):
        json_dir.mkdir(parents=True, exist_ok=True)

    output_stem = safe_slug(output_dir.name or "results")
    for rank, record in enumerate(records, start=1):
        slug = safe_slug(record.relative_id)
        prefix = f"rank_{rank:03d}_{slug}"
        if record.model_path is not None:
            cif_name = f"{output_stem}_{rank}.cif" if rename else f"{prefix}.cif"
            shutil.copy2(record.model_path, cif_dir / cif_name)
        if copy_mode in ("cif-summary", "bundle"):
            shutil.copy2(record.summary_path, json_dir / f"{prefix}_summary_confidences.json")
        if copy_mode == "bundle" and record.confidence_path is not None:
            shutil.copy2(record.confidence_path, json_dir / f"{prefix}_confidences.json")


def csv_row(record: ResultRecord, rank: int | None) -> dict[str, Any]:
    return {
        "rank": "" if rank is None else rank,
        "status": record.status,
        "fail_reasons": "; ".join(record.fail_reasons),
        "relative_id": record.relative_id,
        "summary_path": str(record.summary_path),
        "model_path": "" if record.model_path is None else str(record.model_path),
        "chain_count": record.chain_count,
        "selected_pair": record.selected_pair,
        "ranking_score": record.ranking_score,
        "iptm": record.iptm,
        "ptm": record.ptm,
        "pair_iptm": record.pair_iptm,
        "pair_pae_min": record.pair_pae_min,
        "interface_score": record.interface_score,
        "composite_score": record.composite_score,
        "has_clash": record.has_clash,
        "fraction_disordered": record.fraction_disordered,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(csv_row_header())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def csv_row_header() -> tuple[str, ...]:
    return (
        "rank",
        "status",
        "fail_reasons",
        "relative_id",
        "summary_path",
        "model_path",
        "chain_count",
        "selected_pair",
        "ranking_score",
        "iptm",
        "ptm",
        "pair_iptm",
        "pair_pae_min",
        "interface_score",
        "composite_score",
        "has_clash",
        "fraction_disordered",
    )


def write_reports(
    output_dir: Path,
    selected_records: list[ResultRecord],
    sorted_records: list[ResultRecord],
    args: argparse.Namespace,
    pair_reduce: str,
) -> None:
    selected_rank = {id(record): rank for rank, record in enumerate(selected_records, start=1)}
    selected_rows = [csv_row(record, selected_rank[id(record)]) for record in selected_records]
    all_rows = [csv_row(record, selected_rank.get(id(record))) for record in sorted_records]
    write_csv(output_dir / "selected_results.csv", selected_rows)
    write_csv(output_dir / "all_results.csv", all_rows)

    settings = {
        "input_dir": str(Path(args.input_dir).resolve()),
        "top": args.top,
        "include_samples": args.include_samples,
        "samples_only": args.samples_only,
        "pairs": args.pair,
        "pair_reduce": pair_reduce,
        "preset": args.preset,
        "sort_by": args.sort_by,
        "thresholds": {name: threshold_value(args, name) for name in PRESETS["balanced"]},
        "copy_mode": args.copy_mode,
        "rename": args.rename,
    }
    with (output_dir / "filter_settings.json").open("w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")


def print_dry_run(selected_records: list[ResultRecord], sorted_records: list[ResultRecord]) -> None:
    print(f"Passing results: {len([record for record in sorted_records if record.status == 'pass'])}")
    print(f"Selected results: {len(selected_records)}")
    for rank, record in enumerate(selected_records, start=1):
        print(
            "\t".join(
                [
                    str(rank),
                    record.relative_id,
                    f"composite_score={format_number(record.composite_score)}",
                    f"ranking_score={format_number(record.ranking_score)}",
                    f"pair={record.selected_pair or 'NA'}",
                    f"pair_iptm={format_number(record.pair_iptm)}",
                    f"pair_pae_min={format_number(record.pair_pae_min)}",
                    str(record.model_path or ""),
                ]
            )
        )


def format_number(value: float | None) -> str:
    return "NA" if value is None else f"{value:.4g}"


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        die(f"Input directory not found: {input_dir}")

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else None
    if output_dir and args.clean and output_dir.exists():
        if output_dir == input_dir or is_relative_to(input_dir, output_dir):
            die("--clean output directory must not be the input directory or an input parent directory.")
        shutil.rmtree(output_dir)

    pair_reduce = args.pair_reduce
    if pair_reduce is None:
        pair_reduce = "worst" if args.pair else "best"

    summary_files = discover_summary_files(input_dir, output_dir, args.include_samples, args.samples_only)
    if not summary_files:
        die(f"No *{SUMMARY_SUFFIX} files found under: {input_dir}")

    records = [make_record(summary_path, input_dir, args.pair, pair_reduce) for summary_path in summary_files]
    for record in records:
        evaluate_record(record, args)

    sorted_records = sort_records(records, args.sort_by)
    passing_records = [record for record in sorted_records if record.status == "pass"]
    selected_records = passing_records if args.top == 0 else passing_records[: args.top]

    if args.dry_run:
        print_dry_run(selected_records, sorted_records)
        return 0 if selected_records else 1

    if not selected_records:
        assert output_dir is not None
        output_dir.mkdir(parents=True, exist_ok=True)
        write_reports(output_dir, selected_records, sorted_records, args, pair_reduce)
        die("No results passed the selected filters. See all_results.csv for details.")

    assert output_dir is not None
    copy_selected_files(selected_records, output_dir, args.copy_mode, args.rename)
    write_reports(output_dir, selected_records, sorted_records, args, pair_reduce)
    print(f"Scanned {len(records)} summary file(s).")
    print(f"Selected {len(selected_records)} result(s).")
    print(f"Output directory: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
