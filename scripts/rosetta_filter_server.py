#!/usr/bin/env python3
"""
Serve Rosetta filter reports and export currently selected structures on request.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_HOST = "172.27.25.26"
DEFAULT_PORT = 8787
DEFAULT_CSV = "interface_scores.csv"
DEFAULT_TARGET_DIR = "selected_structures"
AF3_WORKSPACE_DIR = "af3_top_for_rosetta"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve a Rosetta filter HTML report with an export-selected API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir", default=".", help="Rosetta filter output directory.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument("--csv-name", default=DEFAULT_CSV, help="All-score CSV filename.")
    return parser.parse_args()


def die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_target_dir(output_dir: Path, requested: object) -> Path:
    name = str(requested or DEFAULT_TARGET_DIR).strip()
    if not name:
        name = DEFAULT_TARGET_DIR
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Target directory must be relative to the report directory.")
    resolved = (output_dir / path).resolve()
    resolved.relative_to(output_dir)
    return resolved


def safe_file_name(index: int, source: Path, rename: bool) -> str:
    if rename:
        return f"{index}{source.suffix or '.pdb'}"
    basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name).lstrip(".")
    return f"{index:04d}_{basename or 'structure'}"


def fieldnames_with_export(fieldnames: list[str]) -> list[str]:
    if "exported_filename" in fieldnames:
        return fieldnames
    output = list(fieldnames)
    try:
        insert_at = output.index("structure_path") + 1
    except ValueError:
        insert_at = len(output)
    output.insert(insert_at, "exported_filename")
    return output


def load_score_rows(csv_path: Path) -> tuple[dict[int, Path], dict[int, dict[str, str]], list[str]]:
    if not csv_path.is_file():
        die(f"CSV report not found: {csv_path}")
    allowed: dict[int, Path] = {}
    rows: dict[int, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        for fallback_index, row in enumerate(reader, start=1):
            raw_index = row.get("_index") or row.get("index") or fallback_index
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = fallback_index
            rows[index] = row
            raw_path = (row.get("structure_path") or "").strip()
            if not raw_path:
                continue
            allowed[index] = Path(raw_path)
    return allowed, rows, fieldnames


def load_allowed_structures(csv_path: Path) -> dict[int, Path]:
    allowed, _rows, _fieldnames = load_score_rows(csv_path)
    return allowed


def make_server(host: str, port: int, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    last_error: OSError | None = None
    for candidate in range(port, port + 50):
        try:
            return ThreadingHTTPServer((host, candidate), handler)
        except OSError as exc:
            last_error = exc
            if exc.errno not in (98, 10048):
                break
    if last_error is not None:
        raise last_error
    raise OSError("Failed to create HTTP server.")


class ExportServer(BaseHTTPRequestHandler):
    output_dir: Path
    allowed_structures: dict[int, Path]
    score_rows: dict[int, dict[str, str]]
    score_fieldnames: list[str]

    server_version = "RosettaFilterExport/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        request_path = unquote(parsed.path)
        if request_path in ("", "/"):
            request_path = "/rosetta_filter_plot.html"
        relative = Path(request_path.lstrip("/"))
        if relative.is_absolute() or ".." in relative.parts:
            self.send_error(403)
            return
        target = (self.output_dir / relative).resolve()
        try:
            target.relative_to(self.output_dir)
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/export-selected":
            self.send_error(404)
            return
        try:
            if not self.allowed_structures:
                raise ValueError("No structure paths are available. Regenerate the report from rosetta_filter.sh.")
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            indices = payload.get("indices", [])
            if not isinstance(indices, list):
                raise ValueError("indices must be a list.")
            target_dir = safe_target_dir(self.output_dir, payload.get("target_dir"))
            rename = bool(payload.get("rename"))
            export_records = self.build_export_records(indices, rename)
            if not export_records:
                raise ValueError("No selected structure files are available for export.")
            copied = self.copy_records(export_records, target_dir)
            csv_rows = self.write_selected_csv(export_records, target_dir)
            if copied != len(export_records) or csv_rows != len(export_records):
                raise RuntimeError("Export did not write every selected structure and CSV row.")
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return
        workspace_removed, cleanup_error = self.remove_af3_workspace(target_dir)
        self.send_json(
            200,
            {
                "ok": True,
                "copied": copied,
                "csv_rows": csv_rows,
                "csv_path": str(target_dir / "selected_results.csv"),
                "target_dir": str(target_dir),
                "af3_workspace_removed": workspace_removed,
                "af3_cleanup_error": cleanup_error,
            },
        )

    def build_export_records(self, indices: list[object], rename: bool) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        seen: set[Path] = set()
        for value in indices:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            source = self.allowed_structures.get(index)
            row = self.score_rows.get(index)
            if source is None or row is None:
                continue
            if not source.is_file():
                continue
            source_key = source.resolve()
            if source_key in seen:
                continue
            seen.add(source_key)
            exported_filename = safe_file_name(len(records) + 1, source, rename)
            records.append(
                {
                    "index": index,
                    "source": source,
                    "row": row,
                    "exported_filename": exported_filename,
                }
            )
        return records

    def copy_records(self, export_records: list[dict[str, object]], target_dir: Path) -> int:
        target_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for record in export_records:
            source = record["source"]
            exported_filename = str(record["exported_filename"])
            if not isinstance(source, Path):
                continue
            shutil.copy2(source, target_dir / exported_filename)
            copied += 1
        return copied

    def write_selected_csv(self, export_records: list[dict[str, object]], target_dir: Path) -> int:
        target_dir.mkdir(parents=True, exist_ok=True)
        csv_path = target_dir / "selected_results.csv"
        fieldnames = fieldnames_with_export(self.score_fieldnames)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for record in export_records:
                row = record["row"]
                if not isinstance(row, dict):
                    continue
                output = dict(row)
                output["exported_filename"] = record["exported_filename"]
                writer.writerow(output)
        return len(export_records)

    def remove_af3_workspace(self, target_dir: Path) -> tuple[bool, str]:
        workspace = self.output_dir / AF3_WORKSPACE_DIR
        if not workspace.exists() and not workspace.is_symlink():
            return False, ""
        try:
            target_dir.resolve().relative_to(workspace.resolve())
        except ValueError:
            pass
        else:
            return False, "Export target is inside the AF3 workspace; cleanup was skipped."
        try:
            if workspace.is_symlink():
                workspace.unlink()
            else:
                resolved = workspace.resolve()
                if resolved.parent != self.output_dir:
                    raise ValueError("AF3 workspace is not a direct child of the report directory.")
                if not resolved.is_dir():
                    raise ValueError("AF3 workspace path is not a directory.")
                shutil.rmtree(resolved)
        except Exception as exc:
            return False, str(exc)
        return True, ""


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not output_dir.is_dir():
        die(f"Output directory not found: {output_dir}")
    allowed, rows, fieldnames = load_score_rows(output_dir / args.csv_name)
    if not allowed:
        print("Warning: no structure paths were found in the CSV report. Plot serving will still start.", file=sys.stderr)

    ExportServer.output_dir = output_dir
    ExportServer.allowed_structures = allowed
    ExportServer.score_rows = rows
    ExportServer.score_fieldnames = fieldnames
    server = make_server(args.host, args.port, ExportServer)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/rosetta_filter_plot.html"
    print(f"Serving Rosetta filter report: {url}", flush=True)
    print("Press Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
