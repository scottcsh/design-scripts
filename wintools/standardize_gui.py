from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

try:
    import pandas as pd
except Exception as exc:  # pragma: no cover - handled in the GUI startup path
    pd = None
    PANDAS_IMPORT_ERROR = exc
else:
    PANDAS_IMPORT_ERROR = None


SUPPORTED_FILE_TYPES = (
    ("Spreadsheet files", "*.csv *.tsv *.txt *.xlsx *.xlsm *.xls"),
    ("CSV files", "*.csv"),
    ("Excel files", "*.xlsx *.xlsm *.xls"),
    ("All files", "*.*"),
)

SUPPORTED_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}
APP_BASE = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk

PLOT_COLORS = (
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#8c564b",
    "#e377c2",
)

PLOT_EXPORT_SCALE = 3
PLOT_EXPORT_DPI = 300
PLOT_FONT_SCALE = 1.6
BOX_FILL_OPACITY = 0.35
VALUE_LABEL_FONT_SIZE = 8
GROUP_LABEL_FONT_SIZE = 10
FILE_LABEL_FONT_SIZE = 8
GROUP_LABEL_OFFSET = 64
GROUP_SEPARATOR_BOTTOM_OFFSET = 82
FILE_LABEL_OFFSET = 38
QUICK_SELECT_COLUMNS = (
    "dG_separated/dSASAx100",
    "delta_unsatHbonds",
    "sc_value",
    "ranking_score",
    "iptm",
    "ptm",
    "pair_iptm",
    "pair_pae_min",
    "interface_score",
)


def plot_font_size(size: int) -> int:
    return max(1, math.ceil(size * PLOT_FONT_SCALE))


def plot_font(size: int, weight: str | None = None) -> tuple[str, int] | tuple[str, int, str]:
    if weight:
        return ("Segoe UI", plot_font_size(size), weight)
    return ("Segoe UI", plot_font_size(size))


def blend_hex_color(color: str, opacity: float = BOX_FILL_OPACITY) -> str:
    value = color.strip().lstrip("#")
    if len(value) != 6:
        return color
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except ValueError:
        return color
    blended_red = int(255 * (1 - opacity) + red * opacity)
    blended_green = int(255 * (1 - opacity) + green * opacity)
    blended_blue = int(255 * (1 - opacity) + blue * opacity)
    return f"#{blended_red:02x}{blended_green:02x}{blended_blue:02x}"


def quick_select_column_indices(columns: Iterable[object]) -> list[int]:
    targets = set(QUICK_SELECT_COLUMNS)
    return [index for index, column in enumerate(columns) if str(column) in targets]


@dataclass(frozen=True)
class StandardizeStats:
    count: int
    mean: float
    sample_stdev: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class PlotSeries:
    name: str
    values: list[float | None]
    stats: StandardizeStats
    color: str
    file_name: str = ""
    column_name: str = ""


@dataclass(frozen=True)
class LoadedDataset:
    path: Path
    display_name: str
    sheet_name: str
    dataframe: object


@dataclass(frozen=True)
class BoxPlotSummary:
    minimum: float
    q1: float
    median: float
    q3: float
    maximum: float
    lower_whisker: float
    upper_whisker: float
    outliers: list[float]


@dataclass(frozen=True)
class PlotGroup:
    column_name: str
    series: list[tuple[PlotSeries, BoxPlotSummary]]


@dataclass(frozen=True)
class PlotLayout:
    width: int
    height: int
    content_width: int
    plot_left: int
    plot_top: int
    plot_right: int
    plot_bottom: int
    y_min: float
    y_max: float
    groups: list[PlotGroup]


def is_number(value: object) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric)


def coerce_finite_numbers(values: Iterable[object]) -> list[float | None]:
    result: list[float | None] = []
    for value in values:
        if is_number(value):
            result.append(float(value))
        else:
            result.append(None)
    return result


def calculate_original_stats(
    values: Iterable[object],
    require_sample_stdev: bool = False,
) -> StandardizeStats:
    numeric_values = coerce_finite_numbers(values)
    valid_values = [value for value in numeric_values if value is not None]

    if not valid_values:
        raise ValueError("At least one numeric value is required.")
    if require_sample_stdev and len(valid_values) < 2:
        raise ValueError("At least two numeric values are required for STDEV.S.")

    mean_value = sum(valid_values) / len(valid_values)
    if len(valid_values) < 2:
        sample_stdev = 0.0
    else:
        variance = sum((value - mean_value) ** 2 for value in valid_values) / (len(valid_values) - 1)
        sample_stdev = math.sqrt(variance)

    return StandardizeStats(
        count=len(valid_values),
        mean=mean_value,
        sample_stdev=sample_stdev,
        minimum=min(valid_values),
        maximum=max(valid_values),
    )


def standardize_values(values: Iterable[object], mean_value: float, sample_stdev: float) -> list[float | None]:
    if sample_stdev == 0:
        raise ValueError("STDEV.S is zero, so STANDARDIZE cannot be calculated.")

    numeric_values = coerce_finite_numbers(values)
    standardized = [
        None if value is None else (value - mean_value) / sample_stdev
        for value in numeric_values
    ]
    return standardized


def calculate_standardize(values: Iterable[object]) -> tuple[list[float | None], StandardizeStats]:
    stats = calculate_original_stats(values, require_sample_stdev=True)
    standardized = standardize_values(values, stats.mean, stats.sample_stdev)
    return standardized, stats


def calculate_standardize_against(
    values: Iterable[object],
    reference_stats: StandardizeStats,
) -> tuple[list[float | None], StandardizeStats]:
    stats = calculate_original_stats(values, require_sample_stdev=False)
    standardized = standardize_values(values, reference_stats.mean, reference_stats.sample_stdev)
    return standardized, stats


def percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        raise ValueError("At least one value is required.")
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (len(sorted_values) - 1) * ratio
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]

    weight = position - lower_index
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    return lower_value + (upper_value - lower_value) * weight


def calculate_box_plot(values: Iterable[float | None]) -> BoxPlotSummary:
    finite_values = sorted(
        value for value in values
        if value is not None and math.isfinite(value)
    )
    if not finite_values:
        raise ValueError("At least one finite value is required.")

    q1 = percentile(finite_values, 0.25)
    median = percentile(finite_values, 0.5)
    q3 = percentile(finite_values, 0.75)
    iqr = q3 - q1

    if iqr == 0:
        lower_whisker = finite_values[0]
        upper_whisker = finite_values[-1]
        outliers: list[float] = []
    else:
        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr
        inliers = [value for value in finite_values if lower_fence <= value <= upper_fence]
        lower_whisker = inliers[0]
        upper_whisker = inliers[-1]
        outliers = [value for value in finite_values if value < lower_fence or value > upper_fence]

    return BoxPlotSummary(
        minimum=finite_values[0],
        q1=q1,
        median=median,
        q3=q3,
        maximum=finite_values[-1],
        lower_whisker=lower_whisker,
        upper_whisker=upper_whisker,
        outliers=outliers,
    )


def format_number(value: float | None, digits: int = 4) -> str:
    if value is None:
        return ""
    if abs(value) >= 100000 or (value != 0 and abs(value) < 0.001):
        return f"{value:.{digits}e}"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def shortened_text(text: object, max_length: int = 28) -> str:
    value = str(text)
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def cropped_single_line_text(text: object, max_width: float, size: int, min_length: int = 6) -> str:
    value = " ".join(str(text).splitlines())
    approx_character_width = max(1.0, plot_font_size(size) * 0.58)
    max_length = max(min_length, int(max_width / approx_character_width))
    return shortened_text(value, max_length)


class StandardizeApp(APP_BASE):
    def __init__(self) -> None:
        super().__init__()
        self.title("Standardize Data Plotter")
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.file_path: Path | None = None
        self.sheet_names: list[str] = []
        self.dataframe = None
        self.datasets: list[LoadedDataset] = []
        self.reference_dataset_index = 0
        self.numeric_columns: list[object] = []
        self.normalized_dataframe = None
        self.plot_series: list[PlotSeries] = []
        self.current_plot_layout: PlotLayout | None = None

        self._build_ui()
        self._register_drag_drop()
        self._set_actions_enabled(False)

        if pd is None:
            messagebox.showerror(
                "Missing dependency",
                "pandas is required. Install dependencies with: pip install -r requirements.txt",
            )
            self.status_var.set(f"Missing dependency: {PANDAS_IMPORT_ERROR}")

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(12, 10, 12, 6))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(2, weight=1)

        load_button = ttk.Button(header, text="Add Files", command=self.open_file)
        load_button.grid(row=0, column=0, sticky="w")

        clear_files_button = ttk.Button(header, text="Clear Files", command=self.clear_files)
        clear_files_button.grid(row=0, column=1, padx=(8, 0), sticky="w")

        self.file_var = tk.StringVar(value="No file loaded. Drag and drop CSV or Excel files here.")
        file_label = ttk.Label(header, textvariable=self.file_var)
        file_label.grid(row=0, column=2, padx=(12, 8), sticky="ew")
        self.drop_widgets = [self, header, file_label]

        ttk.Label(header, text="Reference").grid(row=0, column=3, padx=(8, 4))
        self.reference_var = tk.StringVar()
        self.reference_combo = ttk.Combobox(
            header,
            textvariable=self.reference_var,
            state="disabled",
            width=22,
            values=[],
        )
        self.reference_combo.grid(row=0, column=4, sticky="e")
        self.reference_combo.bind("<<ComboboxSelected>>", lambda _event: self.change_reference_dataset())

        ttk.Label(header, text="Sheet").grid(row=0, column=5, padx=(8, 4))
        self.sheet_var = tk.StringVar()
        self.sheet_combo = ttk.Combobox(
            header,
            textvariable=self.sheet_var,
            state="disabled",
            width=24,
            values=[],
        )
        self.sheet_combo.grid(row=0, column=6, sticky="e")
        self.sheet_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_sheet())

        side = ttk.Frame(self, padding=(12, 6, 8, 12))
        side.grid(row=1, column=0, sticky="nsew")
        side.rowconfigure(3, weight=1)

        ttk.Label(side, text="Numeric Columns").grid(row=0, column=0, columnspan=2, sticky="w")

        self.summary_var = tk.StringVar(value="Open a file to detect columns.")
        summary_label = ttk.Label(side, textvariable=self.summary_var, wraplength=280)
        summary_label.grid(row=1, column=0, columnspan=2, pady=(4, 8), sticky="ew")

        list_frame = ttk.Frame(side)
        list_frame.grid(row=3, column=0, columnspan=2, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.column_list = tk.Listbox(
            list_frame,
            selectmode=tk.MULTIPLE,
            activestyle="dotbox",
            height=18,
            exportselection=False,
        )
        self.column_list.grid(row=0, column=0, sticky="nsew")
        column_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.column_list.yview)
        column_scroll.grid(row=0, column=1, sticky="ns")
        self.column_list.configure(yscrollcommand=column_scroll.set)

        select_all = ttk.Button(side, text="Select All", command=self.select_all_columns)
        select_all.grid(row=4, column=0, pady=(8, 0), sticky="ew")
        clear_selection = ttk.Button(side, text="Clear", command=self.clear_column_selection)
        clear_selection.grid(row=4, column=1, padx=(8, 0), pady=(8, 0), sticky="ew")

        self.quick_select_button = ttk.Button(side, text="Select Score Metrics", command=self.select_score_metric_columns)
        self.quick_select_button.grid(row=5, column=0, columnspan=2, pady=(8, 0), sticky="ew")

        self.normalize_button = ttk.Button(side, text="Normalize and Plot", command=self.normalize_selected_columns)
        self.normalize_button.grid(row=6, column=0, columnspan=2, pady=(12, 0), sticky="ew")

        self.export_csv_button = ttk.Button(side, text="Export CSV", command=self.export_csv)
        self.export_csv_button.grid(row=7, column=0, pady=(8, 0), sticky="ew")
        self.export_excel_button = ttk.Button(side, text="Export Excel", command=self.export_excel)
        self.export_excel_button.grid(row=7, column=1, padx=(8, 0), pady=(8, 0), sticky="ew")

        self.export_plot_button = ttk.Button(side, text="Export Plot", command=self.export_plot)
        self.export_plot_button.grid(row=8, column=0, columnspan=2, pady=(8, 0), sticky="ew")

        self.status_var = tk.StringVar(value="Ready.")
        status_frame = ttk.Frame(side, width=280, height=58)
        status_frame.grid(row=9, column=0, columnspan=2, pady=(12, 0), sticky="w")
        status_frame.grid_propagate(False)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)
        status_label = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            wraplength=268,
            anchor="nw",
            justify="left",
        )
        status_label.grid(row=0, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=1, sticky="nsew", padx=(0, 12), pady=(6, 12))
        self.drop_widgets.append(self.notebook)

        self.preview_tree = self._create_tree_tab("Data Preview")
        self.plot_canvas = self._create_plot_tab()
        self.normalized_tree = self._create_tree_tab("Normalized Data")
        self.drop_widgets.extend([self.preview_tree, self.plot_canvas, self.normalized_tree])

    def _create_tree_tab(self, title: str) -> ttk.Treeview:
        frame = ttk.Frame(self.notebook, padding=8)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        tree = ttk.Treeview(frame, show="headings")
        tree.grid(row=0, column=0, sticky="nsew")

        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        self.notebook.add(frame, text=title)
        return tree

    def _create_plot_tab(self) -> tk.Canvas:
        frame = ttk.Frame(self.notebook, padding=8)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        canvas = tk.Canvas(frame, background="white", highlightthickness=1, highlightbackground="#c8c8c8")
        canvas.grid(row=0, column=0, sticky="nsew")
        canvas.bind("<Configure>", lambda _event: self.draw_plot())
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        canvas.configure(xscrollcommand=horizontal.set)

        self.notebook.add(frame, text="Plot")
        return canvas

    def _set_actions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.quick_select_button.configure(state=state)
        self.normalize_button.configure(state=state)
        self.export_csv_button.configure(state="disabled")
        self.export_excel_button.configure(state="disabled")
        self.export_plot_button.configure(state="disabled")

    def _register_drag_drop(self) -> None:
        if DND_FILES is None:
            self.status_var.set("Drag and drop is unavailable. Install tkinterdnd2 to enable it.")
            return

        for widget in self.drop_widgets:
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.handle_file_drop)
            except tk.TclError:
                continue

        self.status_var.set("Ready. Drag and drop a CSV or Excel file, or use Open File.")

    def handle_file_drop(self, event) -> str:
        paths = self._parse_drop_paths(event.data)
        if not paths:
            self.status_var.set("No readable file was dropped.")
            return "break"

        self.load_files(paths)
        return "break"

    def _parse_drop_paths(self, data: str) -> list[Path]:
        paths: list[Path] = []
        for raw_value in self.tk.splitlist(data):
            value = str(raw_value).strip()
            if value.startswith("file:///"):
                value = value.replace("file:///", "", 1).replace("/", "\\")
            path = Path(value)
            if path.is_file():
                paths.append(path)
        return paths

    def open_file(self) -> None:
        if pd is None:
            messagebox.showerror(
                "Missing dependency",
                "pandas is required. Install dependencies with: pip install -r requirements.txt",
            )
            return

        paths = filedialog.askopenfilenames(title="Open data files", filetypes=SUPPORTED_FILE_TYPES)
        if not paths:
            return

        self.load_files([Path(path) for path in paths])

    def load_file(self, path: Path) -> None:
        self.load_files([path])

    def load_files(self, paths: list[Path]) -> None:
        if pd is None:
            messagebox.showerror(
                "Missing dependency",
                "pandas is required. Install dependencies with: pip install -r requirements.txt",
            )
            return

        clean_paths: list[Path] = []
        for path in paths:
            if not path.is_file():
                messagebox.showerror("File not found", str(path))
                self.status_var.set(f"File not found: {path}")
                return
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                messagebox.showerror("Unsupported file type", f"Unsupported file type: {path.suffix}")
                self.status_var.set(f"Unsupported file type: {path.suffix}")
                return
            clean_paths.append(path)

        if not clean_paths:
            return

        self.normalized_dataframe = None
        self.plot_series = []
        self.draw_plot()

        new_paths = self._filter_new_paths(clean_paths)
        if not new_paths:
            self.status_var.set("Selected files are already loaded.")
            return

        if self.datasets:
            new_datasets, errors = self._read_datasets(new_paths)
            if not new_datasets:
                messagebox.showerror("File load failed", "\n".join(errors) if errors else "No readable files.")
                self.status_var.set("File load failed.")
                return
            reference_name = self.datasets[self.reference_dataset_index].display_name
            self._set_loaded_datasets(self.datasets + new_datasets, reference_name=reference_name)
            if errors:
                messagebox.showwarning("Some files skipped", "\n".join(errors))
            self.status_var.set(
                f"Loaded {len(self.datasets):,} files. Reference: {self.datasets[self.reference_dataset_index].display_name}."
            )
            return

        self.file_path = new_paths[0]
        if len(new_paths) == 1 and self.file_path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            self._load_excel_metadata()
        else:
            self.sheet_names = []
            self.sheet_combo.configure(state="disabled", values=[])
            self.sheet_var.set("")
            self._load_datasets(new_paths)

    def clear_files(self) -> None:
        self.file_path = None
        self.sheet_names = []
        self.dataframe = None
        self.datasets = []
        self.reference_dataset_index = 0
        self.numeric_columns = []
        self.normalized_dataframe = None
        self.plot_series = []

        self.file_var.set("No file loaded. Drag and drop CSV or Excel files here.")
        self.reference_var.set("")
        self.reference_combo.configure(state="disabled", values=[])
        self.sheet_var.set("")
        self.sheet_combo.configure(state="disabled", values=[])
        self.summary_var.set("Open a file to detect columns.")
        self.column_list.delete(0, tk.END)
        self.populate_tree(self.preview_tree, None)
        self.populate_tree(self.normalized_tree, None)
        self.draw_plot()
        self._set_actions_enabled(False)
        self.status_var.set("Files cleared.")

    def _normalized_path(self, path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path.absolute()).lower()

    def _filter_new_paths(self, paths: list[Path]) -> list[Path]:
        existing_paths = {self._normalized_path(dataset.path) for dataset in self.datasets}
        seen_paths: set[str] = set()
        new_paths: list[Path] = []
        for path in paths:
            normalized_path = self._normalized_path(path)
            if normalized_path in existing_paths or normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            new_paths.append(path)
        return new_paths

    def _load_excel_metadata(self) -> None:
        assert self.file_path is not None
        try:
            excel_file = pd.ExcelFile(self.file_path)
        except Exception as exc:
            messagebox.showerror("Excel load failed", str(exc))
            self.status_var.set(f"Excel load failed: {exc}")
            return

        self.sheet_names = list(excel_file.sheet_names)
        if not self.sheet_names:
            messagebox.showwarning("No sheets", "The workbook has no readable sheets.")
            return

        self.sheet_combo.configure(state="readonly", values=self.sheet_names)
        self.sheet_var.set(self.sheet_names[0])
        self.load_selected_sheet()

    def load_selected_sheet(self) -> None:
        if self.file_path is None or pd is None:
            return

        try:
            dataframe = self._read_dataframe(self.file_path, self.sheet_var.get())
        except Exception as exc:
            messagebox.showerror("File load failed", str(exc))
            self.status_var.set(f"File load failed: {exc}")
            return

        dataset = LoadedDataset(
            path=self.file_path,
            display_name=self.file_path.name,
            sheet_name=self.sheet_var.get(),
            dataframe=dataframe,
        )
        self._set_loaded_datasets([dataset])

    def _load_datasets(self, paths: list[Path]) -> None:
        datasets, errors = self._read_datasets(paths)

        if not datasets:
            messagebox.showerror("File load failed", "\n".join(errors) if errors else "No readable files.")
            self.status_var.set("File load failed.")
            return

        self._set_loaded_datasets(datasets)
        if errors:
            messagebox.showwarning("Some files skipped", "\n".join(errors))

    def _read_datasets(self, paths: list[Path]) -> tuple[list[LoadedDataset], list[str]]:
        datasets: list[LoadedDataset] = []
        errors: list[str] = []
        display_names = {dataset.display_name for dataset in self.datasets}

        for path in paths:
            try:
                sheet_name = ""
                if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
                    excel_file = pd.ExcelFile(path)
                    if excel_file.sheet_names:
                        sheet_name = str(excel_file.sheet_names[0])
                dataframe = self._read_dataframe(path, sheet_name)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                continue

            display_name = self._unique_display_name(path.name, display_names)
            display_names.add(display_name)
            datasets.append(
                LoadedDataset(
                    path=path,
                    display_name=display_name,
                    sheet_name=sheet_name,
                    dataframe=dataframe,
                )
            )

        return datasets, errors

    def _unique_display_name(self, name: str, existing_names: set[str]) -> str:
        if name not in existing_names:
            return name
        stem = Path(name).stem
        suffix = Path(name).suffix
        counter = 2
        while True:
            candidate = f"{stem} ({counter}){suffix}"
            if candidate not in existing_names:
                return candidate
            counter += 1

    def _set_loaded_datasets(self, datasets: list[LoadedDataset], reference_name: str | None = None) -> None:
        self.datasets = datasets
        self.reference_dataset_index = 0
        if reference_name is not None:
            for index, dataset in enumerate(datasets):
                if dataset.display_name == reference_name:
                    self.reference_dataset_index = index
                    break
        self.dataframe = datasets[0].dataframe

        reference_values = [dataset.display_name for dataset in datasets]
        self.reference_combo.configure(
            state="readonly" if len(datasets) > 1 else "disabled",
            values=reference_values,
        )
        self.reference_var.set(reference_values[self.reference_dataset_index])

        if len(datasets) > 1:
            self.sheet_combo.configure(state="disabled", values=[])
            self.sheet_var.set("")
        elif datasets[0].path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            self.sheet_combo.configure(state="disabled", values=[])
            self.sheet_var.set("")

        self.dataframe = datasets[self.reference_dataset_index].dataframe
        self.file_var.set(self._format_loaded_files_label())
        self.detect_columns()
        self.populate_tree(self.preview_tree, self.dataframe)
        self.populate_tree(self.normalized_tree, None)
        self._set_actions_enabled(bool(self.numeric_columns))
        if len(datasets) == 1:
            self.status_var.set(f"Loaded {len(self.dataframe):,} rows and {len(self.dataframe.columns):,} columns.")
        else:
            self.status_var.set(
                f"Loaded {len(datasets):,} files. Reference: {datasets[0].display_name}."
            )

    def _format_loaded_files_label(self) -> str:
        if not self.datasets:
            return "No file loaded. Drag and drop CSV or Excel files here."
        if len(self.datasets) == 1:
            return str(self.datasets[0].path)
        names = ", ".join(dataset.display_name for dataset in self.datasets[:3])
        if len(self.datasets) > 3:
            names += f", +{len(self.datasets) - 3} more"
        return f"{len(self.datasets)} files loaded: {names}"

    def change_reference_dataset(self) -> None:
        if not self.datasets:
            return
        selected_name = self.reference_var.get()
        for index, dataset in enumerate(self.datasets):
            if dataset.display_name == selected_name:
                self.reference_dataset_index = index
                self.dataframe = dataset.dataframe
                break
        self.normalized_dataframe = None
        self.plot_series = []
        self.detect_columns()
        self.populate_tree(self.preview_tree, self.dataframe)
        self.populate_tree(self.normalized_tree, None)
        self.draw_plot()
        self._set_actions_enabled(bool(self.numeric_columns))
        self.status_var.set(f"Reference file set to {self.datasets[self.reference_dataset_index].display_name}.")

    def _read_dataframe(self, path: Path, sheet_name: str):
        suffix = path.suffix.lower()
        if suffix in {".csv", ".txt"}:
            return pd.read_csv(path, sep=None, engine="python")
        if suffix == ".tsv":
            return pd.read_csv(path, sep="\t")
        if suffix in {".xlsx", ".xlsm", ".xls"}:
            selected_sheet = sheet_name if sheet_name else 0
            return pd.read_excel(path, sheet_name=selected_sheet)
        raise ValueError(f"Unsupported file type: {suffix}")

    def detect_columns(self) -> None:
        self.column_list.delete(0, tk.END)
        self.numeric_columns = []

        if self.dataframe is None or not self.datasets:
            self.summary_var.set("Open a file to detect columns.")
            return

        reference_dataset = self.datasets[self.reference_dataset_index]
        reference_columns = [
            str(column)
            for column in reference_dataset.dataframe.columns
            if self._numeric_count(reference_dataset.dataframe, str(column)) > 0
        ]

        if len(self.datasets) == 1:
            row_count = len(reference_dataset.dataframe)
            for column_name in reference_columns:
                numeric_count = self._numeric_count(reference_dataset.dataframe, column_name)
                self.numeric_columns.append(column_name)
                self.column_list.insert(tk.END, f"{column_name} ({numeric_count}/{row_count} numeric)")
        else:
            common_columns = []
            for column_name in reference_columns:
                if all(self._numeric_count(dataset.dataframe, column_name) > 0 for dataset in self.datasets):
                    common_columns.append(column_name)

            for column_name in common_columns:
                counts = [
                    self._numeric_count(dataset.dataframe, column_name)
                    for dataset in self.datasets
                ]
                self.numeric_columns.append(column_name)
                self.column_list.insert(
                    tk.END,
                    f"{column_name} ({min(counts)}-{max(counts)} numeric/file)",
                )

        if self.numeric_columns:
            if len(self.datasets) == 1:
                self.summary_var.set(f"Detected {len(self.numeric_columns)} numeric columns.")
            else:
                self.summary_var.set(
                    f"Detected {len(self.numeric_columns)} common numeric columns across {len(self.datasets)} files."
                )
        else:
            self.summary_var.set("No numeric columns were detected.")

    def _get_column_label(self, dataframe, column_name: str):
        for column in dataframe.columns:
            if str(column) == column_name:
                return column
        raise KeyError(column_name)

    def _numeric_count(self, dataframe, column_name: str) -> int:
        try:
            column_label = self._get_column_label(dataframe, column_name)
        except KeyError:
            return 0
        numeric = pd.to_numeric(dataframe[column_label], errors="coerce")
        numeric = numeric.replace([math.inf, -math.inf], pd.NA).dropna()
        return len(numeric)

    def select_all_columns(self) -> None:
        self.column_list.select_set(0, tk.END)

    def clear_column_selection(self) -> None:
        self.column_list.select_clear(0, tk.END)

    def select_score_metric_columns(self) -> None:
        self.column_list.select_clear(0, tk.END)
        indices = quick_select_column_indices(self.numeric_columns)
        for index in indices:
            self.column_list.select_set(index)
            self.column_list.see(index)

        selected_count = len(indices)
        missing_count = len(QUICK_SELECT_COLUMNS) - selected_count
        if selected_count:
            self.status_var.set(f"Selected {selected_count} score metric columns. Missing {missing_count}.")
        else:
            self.status_var.set("No score metric columns were found in the numeric column list.")

    def normalize_selected_columns(self) -> None:
        if self.dataframe is None or not self.datasets:
            messagebox.showwarning("No data", "Open a file first.")
            return

        selected_indices = list(self.column_list.curselection())
        if not selected_indices:
            messagebox.showwarning("No columns selected", "Select one or more numeric columns.")
            return

        reference_dataset = self.datasets[self.reference_dataset_index]
        result_frames = []
        plot_series: list[PlotSeries] = []
        errors: list[str] = []

        selected_columns = [str(self.numeric_columns[list_index]) for list_index in selected_indices]
        reference_stats_by_column: dict[str, StandardizeStats] = {}

        for column_name in selected_columns:
            try:
                reference_label = self._get_column_label(reference_dataset.dataframe, column_name)
                reference_stats_by_column[column_name] = calculate_original_stats(
                    reference_dataset.dataframe[reference_label].to_list(),
                    require_sample_stdev=True,
                )
            except ValueError as exc:
                errors.append(f"{column_name} reference: {exc}")
            except KeyError:
                errors.append(f"{column_name} reference: column not found")

        for dataset_index, dataset in enumerate(self.datasets):
            result = dataset.dataframe.copy()
            if len(self.datasets) > 1:
                result.insert(0, self._unique_column_name(result, "Source File"), dataset.display_name)

            for column_name in selected_columns:
                reference_stats = reference_stats_by_column.get(column_name)
                if reference_stats is None:
                    continue

                try:
                    column_label = self._get_column_label(dataset.dataframe, column_name)
                    standardized, stats = calculate_standardize_against(
                        dataset.dataframe[column_label].to_list(),
                        reference_stats,
                    )
                except (ValueError, KeyError) as exc:
                    errors.append(f"{dataset.display_name} / {column_name}: {exc}")
                    continue

                standardized_name = self._unique_column_name(result, f"{column_name}_standardized")
                result[standardized_name] = standardized
                color = PLOT_COLORS[dataset_index % len(PLOT_COLORS)]
                plot_series.append(
                    PlotSeries(
                        name=dataset.display_name,
                        values=standardized,
                        stats=stats,
                        color=color,
                        file_name=dataset.display_name,
                        column_name=column_name,
                    )
                )

            result_frames.append(result)

        if not plot_series:
            messagebox.showerror("Normalization failed", "\n".join(errors))
            self.status_var.set("Normalization failed.")
            return

        if len(result_frames) == 1:
            self.normalized_dataframe = result_frames[0]
        else:
            self.normalized_dataframe = pd.concat(result_frames, ignore_index=True, sort=False)
        self.plot_series = plot_series
        self.populate_tree(self.normalized_tree, self.normalized_dataframe)
        self.draw_plot()
        self.notebook.select(1)
        self.export_csv_button.configure(state="normal")
        self.export_excel_button.configure(state="normal")
        self.export_plot_button.configure(state="normal")

        status = (
            f"Normalized {len(selected_columns)} columns using reference "
            f"{reference_dataset.display_name}."
        )
        if errors:
            status += f" Skipped {len(errors)} columns."
            messagebox.showwarning("Some columns skipped", "\n".join(errors))
        self.status_var.set(status)

    def _unique_column_name(self, dataframe, name: str) -> str:
        candidate = name
        counter = 2
        while candidate in dataframe.columns:
            candidate = f"{name}_{counter}"
            counter += 1
        return candidate

    def populate_tree(self, tree: ttk.Treeview, dataframe) -> None:
        tree.delete(*tree.get_children())
        tree["columns"] = ()

        if dataframe is None:
            return

        preview = dataframe.head(250)
        columns = [str(column) for column in preview.columns]
        tree["columns"] = columns

        for column in columns:
            tree.heading(column, text=shortened_text(column, 34))
            tree.column(column, width=120, minwidth=70, stretch=False, anchor="w")

        for _index, row in preview.iterrows():
            values = [self._display_cell(row[column]) for column in preview.columns]
            tree.insert("", tk.END, values=values)

    def _display_cell(self, value: object) -> str:
        if pd is not None and pd.isna(value):
            return ""
        if isinstance(value, float):
            return format_number(value, 6)
        return str(value)

    def _get_plot_groups(self) -> list[PlotGroup]:
        grouped: dict[str, list[tuple[PlotSeries, BoxPlotSummary]]] = {}
        order: list[str] = []
        for series in self.plot_series:
            column_name = series.column_name or series.name
            try:
                summary = calculate_box_plot(series.values)
            except ValueError:
                continue
            if column_name not in grouped:
                grouped[column_name] = []
                order.append(column_name)
            grouped[column_name].append((series, summary))
        return [PlotGroup(column_name=column_name, series=grouped[column_name]) for column_name in order]

    def _build_plot_layout(self, width: int, height: int) -> PlotLayout | None:
        groups = self._get_plot_groups()
        if not groups:
            return None

        margin_left = 86
        margin_top = 58
        margin_bottom = 154
        margin_right = 44
        max_series_per_group = max(len(group.series) for group in groups)
        slot_width = max(280, max_series_per_group * 116 + 118)
        content_width = max(width, margin_left + margin_right + len(groups) * slot_width)
        plot_left = margin_left
        plot_top = margin_top
        plot_right = max(plot_left + 160, content_width - margin_right)
        plot_bottom = max(plot_top + 120, height - margin_bottom)

        all_values = [
            value
            for group in groups
            for series, _summary in group.series
            for value in series.values
            if value is not None and math.isfinite(value)
        ]
        if not all_values:
            return None

        y_min = min(all_values + [0.0])
        y_max = max(all_values + [0.0])
        if y_min == y_max:
            y_min -= 1
            y_max += 1
        padding = (y_max - y_min) * 0.12
        y_min -= padding
        y_max += padding

        return PlotLayout(
            width=width,
            height=height,
            content_width=content_width,
            plot_left=plot_left,
            plot_top=plot_top,
            plot_right=plot_right,
            plot_bottom=plot_bottom,
            y_min=y_min,
            y_max=y_max,
            groups=groups,
        )

    def _plot_y(self, layout: PlotLayout, value: float) -> float:
        return layout.plot_bottom - (
            (value - layout.y_min) / (layout.y_max - layout.y_min)
        ) * (layout.plot_bottom - layout.plot_top)

    def _avg_label_y_below_min(self, layout: PlotLayout, min_label_y: float, size: int) -> float:
        return min(layout.plot_bottom + 22, min_label_y + plot_font_size(size) + 14)

    def _plot_group_slot(self, layout: PlotLayout, index: int) -> tuple[float, float, float]:
        slot = (layout.plot_right - layout.plot_left) / len(layout.groups)
        left = layout.plot_left + slot * index
        center = left + slot * 0.5
        right = left + slot
        return left, center, right

    def _plot_series_slot(
        self,
        layout: PlotLayout,
        group_index: int,
        series_index: int,
    ) -> tuple[float, float, float, float]:
        group_left, _group_center, group_right = self._plot_group_slot(layout, group_index)
        group = layout.groups[group_index]
        inner_left = group_left + 46
        inner_right = group_right - 46
        if inner_right <= inner_left:
            inner_left = group_left + 16
            inner_right = group_right - 16
        slot = (inner_right - inner_left) / len(group.series)
        left = inner_left + slot * series_index
        center = left + slot * 0.5
        right = left + slot
        box_width = min(48, max(18, slot * 0.45))
        return left, center, right, box_width

    def _draw_text_label(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        text: str,
        anchor: str,
        fill: str = "#222222",
        font: tuple[str, int] | tuple[str, int, str] | None = None,
    ) -> None:
        if font is None:
            font = plot_font(8)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)

    def _draw_internal_value_labels(
        self,
        canvas: tk.Canvas,
        layout: PlotLayout,
        series: PlotSeries,
        summary: BoxPlotSummary,
        center_x: float,
        box_width: float,
        slot_left: float,
        slot_right: float,
    ) -> None:
        marker_left = center_x - box_width * 0.62
        marker_right = center_x + box_width * 0.62
        true_min_y = self._plot_y(layout, summary.minimum)
        true_max_y = self._plot_y(layout, summary.maximum)

        canvas.create_line(marker_left, true_max_y, marker_right, true_max_y, fill="#555555", dash=(2, 2))
        canvas.create_line(marker_left, true_min_y, marker_right, true_min_y, fill="#555555", dash=(2, 2))

        max_y = max(layout.plot_top + 12, true_max_y - 5)
        min_y = min(layout.plot_bottom - 12, true_min_y + 5)
        avg_label_y = self._avg_label_y_below_min(layout, min_y, VALUE_LABEL_FONT_SIZE)
        self._draw_text_label(
            canvas,
            center_x,
            max_y,
            f"max {format_number(series.stats.maximum, 3)}",
            "s",
            font=("Segoe UI", VALUE_LABEL_FONT_SIZE),
        )
        self._draw_text_label(
            canvas,
            center_x,
            min_y,
            f"min {format_number(series.stats.minimum, 3)}",
            "n",
            font=("Segoe UI", VALUE_LABEL_FONT_SIZE),
        )
        self._draw_text_label(
            canvas,
            center_x,
            avg_label_y,
            f"avg {format_number(series.stats.mean, 3)}",
            "center",
        )

    def draw_plot(self) -> None:
        canvas = self.plot_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)

        if not self.plot_series:
            self.current_plot_layout = None
            canvas.configure(scrollregion=(0, 0, width, height))
            canvas.create_rectangle(0, 0, width, height, fill="white", outline="")
            canvas.create_text(
                width // 2,
                height // 2,
                text="Normalize selected columns to show the plot.",
                fill="#666666",
                font=plot_font(12),
            )
            return

        layout = self._build_plot_layout(width, height)
        if layout is None:
            self.current_plot_layout = None
            canvas.configure(scrollregion=(0, 0, width, height))
            canvas.create_rectangle(0, 0, width, height, fill="white", outline="")
            return

        self.current_plot_layout = layout
        canvas.configure(scrollregion=(0, 0, layout.content_width, layout.height))
        canvas.create_rectangle(0, 0, layout.content_width, layout.height, fill="white", outline="")
        canvas.create_text(
            layout.plot_left,
            18,
            text="STANDARDIZE Box Plot",
            fill="#222222",
            font=plot_font(14, "bold"),
            anchor="w",
        )
        canvas.create_line(layout.plot_left, layout.plot_bottom, layout.plot_right, layout.plot_bottom, fill="#333333", width=1)
        canvas.create_line(layout.plot_left, layout.plot_top, layout.plot_left, layout.plot_bottom, fill="#333333", width=1)

        for tick in range(5):
            ratio = tick / 4
            value = layout.y_min + (layout.y_max - layout.y_min) * ratio
            y = self._plot_y(layout, value)
            canvas.create_line(layout.plot_left - 5, y, layout.plot_right, y, fill="#e6e6e6")
            canvas.create_text(
                layout.plot_left - 10,
                y,
                text=format_number(value, 2),
                fill="#555555",
                font=plot_font(9),
                anchor="e",
            )

        zero_y = self._plot_y(layout, 0)
        if layout.plot_top <= zero_y <= layout.plot_bottom:
            canvas.create_line(layout.plot_left, zero_y, layout.plot_right, zero_y, fill="#9a9a9a", dash=(3, 3))

        canvas.create_text(
            (layout.plot_left + layout.plot_right) / 2,
            layout.height - 18,
            text="Columns",
            fill="#444444",
            font=plot_font(10),
        )
        canvas.create_text(
            18,
            (layout.plot_top + layout.plot_bottom) / 2,
            text="Standardized value",
            fill="#444444",
            font=plot_font(10),
            angle=90,
        )

        for group_index, group in enumerate(layout.groups):
            group_left, group_center, group_right = self._plot_group_slot(layout, group_index)
            canvas.create_text(
                group_center,
                layout.plot_bottom + GROUP_LABEL_OFFSET,
                text=shortened_text(group.column_name, 34),
                fill="#222222",
                font=plot_font(GROUP_LABEL_FONT_SIZE, "bold"),
                anchor="n",
                width=max(120, int(group_right - group_left - 12)),
            )
            if group_index > 0:
                canvas.create_line(
                    group_left,
                    layout.plot_top,
                    group_left,
                    layout.plot_bottom + GROUP_SEPARATOR_BOTTOM_OFFSET,
                    fill="#efefef",
                )

            for series_index, (series, summary) in enumerate(group.series):
                slot_left, center_x, slot_right, box_width = self._plot_series_slot(
                    layout,
                    group_index,
                    series_index,
                )
                box_left = center_x - box_width / 2
                box_right = center_x + box_width / 2
                cap_left = center_x - box_width * 0.35
                cap_right = center_x + box_width * 0.35

                q1_y = self._plot_y(layout, summary.q1)
                median_y = self._plot_y(layout, summary.median)
                q3_y = self._plot_y(layout, summary.q3)
                lower_whisker_y = self._plot_y(layout, summary.lower_whisker)
                upper_whisker_y = self._plot_y(layout, summary.upper_whisker)

                canvas.create_line(center_x, upper_whisker_y, center_x, q3_y, fill=series.color, width=2)
                canvas.create_line(center_x, q1_y, center_x, lower_whisker_y, fill=series.color, width=2)
                canvas.create_line(cap_left, upper_whisker_y, cap_right, upper_whisker_y, fill=series.color, width=2)
                canvas.create_line(cap_left, lower_whisker_y, cap_right, lower_whisker_y, fill=series.color, width=2)
                canvas.create_rectangle(
                    box_left,
                    q3_y,
                    box_right,
                    q1_y,
                    fill=blend_hex_color(series.color),
                    outline=series.color,
                )
                canvas.create_line(box_left, median_y, box_right, median_y, fill="#222222", width=2)

                for outlier_index, outlier in enumerate(summary.outliers[:80]):
                    jitter = ((outlier_index % 5) - 2) * 4
                    outlier_y = self._plot_y(layout, outlier)
                    radius = 2
                    canvas.create_oval(
                        center_x + jitter - radius,
                        outlier_y - radius,
                        center_x + jitter + radius,
                        outlier_y + radius,
                        fill=series.color,
                        outline=series.color,
                    )
                if len(summary.outliers) > 80:
                    canvas.create_text(
                        center_x,
                        layout.plot_top + 6,
                        text=f"+{len(summary.outliers) - 80} outliers",
                        fill=series.color,
                        font=plot_font(8),
                        anchor="n",
                    )

                self._draw_internal_value_labels(
                    canvas,
                    layout,
                    series,
                    summary,
                    center_x,
                    box_width,
                    slot_left,
                    slot_right,
                )

                canvas.create_text(
                    center_x,
                    layout.plot_bottom + FILE_LABEL_OFFSET,
                    text=cropped_single_line_text(
                        series.file_name or series.name,
                        slot_right - slot_left - 4,
                        FILE_LABEL_FONT_SIZE,
                    ),
                    fill=series.color,
                    font=plot_font(FILE_LABEL_FONT_SIZE, "bold"),
                    anchor="n",
                )

    def export_plot(self) -> None:
        if not self.plot_series:
            messagebox.showwarning("No plot", "Normalize selected columns before exporting the plot.")
            return

        path = filedialog.asksaveasfilename(
            title="Export plot",
            defaultextension=".png",
            filetypes=(("PNG files", "*.png"), ("SVG files", "*.svg"), ("All files", "*.*")),
        )
        if not path:
            return

        layout = self.current_plot_layout
        if layout is None:
            width = max(self.plot_canvas.winfo_width(), 1)
            height = max(self.plot_canvas.winfo_height(), 1)
            layout = self._build_plot_layout(width, height)
        if layout is None:
            messagebox.showwarning("No plot", "There is no plot content to export.")
            return

        try:
            output_path = Path(path)
            if output_path.suffix.lower() == ".svg":
                output_path.write_text(self._plot_to_svg(layout), encoding="utf-8")
            else:
                self._capture_full_plot_canvas_to_png(layout, output_path)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self.status_var.set(f"Plot export failed: {exc}")
            return

        self.status_var.set(f"Exported plot: {path}")

    def _capture_full_plot_canvas_to_png(self, layout: PlotLayout, path: Path) -> None:
        from PIL import Image, ImageGrab

        canvas = self.plot_canvas
        original_tab = self.notebook.select()
        original_xview = canvas.xview()
        original_yview = canvas.yview()
        original_topmost = self.attributes("-topmost")

        def option_pixels(option_name: str) -> int:
            try:
                return int(float(canvas.cget(option_name)))
            except (tk.TclError, TypeError, ValueError):
                return 0

        def tile_offsets(total: int, viewport: int) -> list[int]:
            viewport = max(1, viewport)
            if total <= viewport:
                return [0]
            offsets = list(range(0, max(1, total - viewport + 1), viewport))
            last_offset = total - viewport
            if offsets[-1] != last_offset:
                offsets.append(last_offset)
            return offsets

        try:
            self.notebook.select(self.plot_canvas.master)
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.update_idletasks()
            self.update()

            content_width = max(1, int(math.ceil(layout.content_width)))
            content_height = max(1, int(math.ceil(layout.height)))
            inset = option_pixels("highlightthickness") + option_pixels("borderwidth")
            widget_width = max(1, canvas.winfo_width())
            widget_height = max(1, canvas.winfo_height())
            viewport_width = max(1, min(content_width, widget_width - inset * 2))
            viewport_height = max(1, min(content_height, widget_height - inset * 2))
            output = Image.new("RGB", (content_width, content_height), "white")

            for y_offset in tile_offsets(content_height, viewport_height):
                canvas.yview_moveto(y_offset / content_height)
                for x_offset in tile_offsets(content_width, viewport_width):
                    canvas.xview_moveto(x_offset / content_width)
                    self.update_idletasks()
                    self.update()

                    capture_x = canvas.winfo_rootx() + inset
                    capture_y = canvas.winfo_rooty() + inset
                    bbox = (
                        capture_x,
                        capture_y,
                        capture_x + viewport_width,
                        capture_y + viewport_height,
                    )
                    try:
                        tile = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
                    except TypeError:
                        tile = ImageGrab.grab(bbox=bbox).convert("RGB")

                    paste_x = max(0, min(content_width - 1, int(round(canvas.canvasx(0)))))
                    paste_y = max(0, min(content_height - 1, int(round(canvas.canvasy(0)))))
                    paste_width = min(tile.width, content_width - paste_x)
                    paste_height = min(tile.height, content_height - paste_y)
                    output.paste(tile.crop((0, 0, paste_width, paste_height)), (paste_x, paste_y))

            output.save(path, "PNG", dpi=(PLOT_EXPORT_DPI, PLOT_EXPORT_DPI))
        finally:
            if original_xview:
                canvas.xview_moveto(original_xview[0])
            if original_yview:
                canvas.yview_moveto(original_yview[0])
            if original_tab:
                self.notebook.select(original_tab)
            self.attributes("-topmost", original_topmost)
            self.update_idletasks()

    def _plot_to_svg(self, layout: PlotLayout) -> str:
        elements: list[str] = []

        def line(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            color: str,
            width: float = 1,
            dash: str | None = None,
        ) -> None:
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
            elements.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="{color}" stroke-width="{width}"{dash_attr} />'
            )

        def rect(
            x: float,
            y: float,
            width: float,
            height: float,
            fill: str,
            stroke: str = "none",
            opacity: float = 1.0,
        ) -> None:
            elements.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" '
                f'fill="{fill}" stroke="{stroke}" fill-opacity="{opacity:.2f}" />'
            )

        def circle(x: float, y: float, radius: float, fill: str) -> None:
            elements.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" stroke="{fill}" />'
            )

        def text(
            x: float,
            y: float,
            value: str,
            anchor: str = "middle",
            color: str = "#222222",
            size: int = 10,
            weight: str = "normal",
        ) -> None:
            scaled_size = plot_font_size(size)
            elements.append(
                f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
                f'font-family="Segoe UI, Arial, sans-serif" font-size="{scaled_size}" '
                f'font-weight="{weight}" fill="{color}">{escape(value)}</text>'
            )

        def label(
            x: float,
            y: float,
            value: str,
            anchor: str = "middle",
            color: str = "#222222",
            size: int = 9,
            scaled: bool = True,
        ) -> None:
            rendered_size = plot_font_size(size) if scaled else size
            elements.append(
                f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
                'dominant-baseline="middle" '
                f'font-family="Segoe UI, Arial, sans-serif" font-size="{rendered_size}" '
                f'font-weight="normal" fill="{color}">{escape(value)}</text>'
            )

        rect(0, 0, layout.content_width, layout.height, "white")
        text(layout.plot_left, 24, "STANDARDIZE Box Plot", "start", "#222222", 16, "700")
        line(layout.plot_left, layout.plot_bottom, layout.plot_right, layout.plot_bottom, "#333333")
        line(layout.plot_left, layout.plot_top, layout.plot_left, layout.plot_bottom, "#333333")

        for tick in range(5):
            ratio = tick / 4
            value = layout.y_min + (layout.y_max - layout.y_min) * ratio
            y = self._plot_y(layout, value)
            line(layout.plot_left - 5, y, layout.plot_right, y, "#e6e6e6")
            text(layout.plot_left - 10, y + 3, format_number(value, 2), "end", "#555555", 9)

        zero_y = self._plot_y(layout, 0)
        if layout.plot_top <= zero_y <= layout.plot_bottom:
            line(layout.plot_left, zero_y, layout.plot_right, zero_y, "#9a9a9a", 1, "3 3")

        text((layout.plot_left + layout.plot_right) / 2, layout.height - 18, "Columns", "middle", "#444444", 10)
        elements.append(
            f'<text x="18" y="{((layout.plot_top + layout.plot_bottom) / 2):.2f}" '
            'text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="{plot_font_size(10)}" '
            'fill="#444444" transform="rotate(-90 18 '
            f'{((layout.plot_top + layout.plot_bottom) / 2):.2f})">Standardized value</text>'
        )

        for group_index, group in enumerate(layout.groups):
            group_left, group_center, group_right = self._plot_group_slot(layout, group_index)
            if group_index > 0:
                line(
                    group_left,
                    layout.plot_top,
                    group_left,
                    layout.plot_bottom + GROUP_SEPARATOR_BOTTOM_OFFSET,
                    "#efefef",
                )
            text(
                group_center,
                layout.plot_bottom + GROUP_LABEL_OFFSET,
                shortened_text(group.column_name, 34),
                "middle",
                "#222222",
                GROUP_LABEL_FONT_SIZE,
                "700",
            )

            for series_index, (series, summary) in enumerate(group.series):
                slot_left, center_x, slot_right, box_width = self._plot_series_slot(layout, group_index, series_index)
                box_left = center_x - box_width / 2
                box_right = center_x + box_width / 2
                cap_left = center_x - box_width * 0.35
                cap_right = center_x + box_width * 0.35
                marker_left = center_x - box_width * 0.62
                marker_right = center_x + box_width * 0.62

                q1_y = self._plot_y(layout, summary.q1)
                median_y = self._plot_y(layout, summary.median)
                q3_y = self._plot_y(layout, summary.q3)
                lower_whisker_y = self._plot_y(layout, summary.lower_whisker)
                upper_whisker_y = self._plot_y(layout, summary.upper_whisker)
                true_min_y = self._plot_y(layout, summary.minimum)
                true_max_y = self._plot_y(layout, summary.maximum)

                line(center_x, upper_whisker_y, center_x, q3_y, series.color, 2)
                line(center_x, q1_y, center_x, lower_whisker_y, series.color, 2)
                line(cap_left, upper_whisker_y, cap_right, upper_whisker_y, series.color, 2)
                line(cap_left, lower_whisker_y, cap_right, lower_whisker_y, series.color, 2)
                rect(
                    box_left,
                    q3_y,
                    box_right - box_left,
                    q1_y - q3_y,
                    blend_hex_color(series.color),
                    series.color,
                    1.0,
                )
                line(box_left, median_y, box_right, median_y, "#222222", 2)

                line(marker_left, true_max_y, marker_right, true_max_y, "#555555", 1, "2 2")
                line(marker_left, true_min_y, marker_right, true_min_y, "#555555", 1, "2 2")

                for outlier_index, outlier in enumerate(summary.outliers[:80]):
                    jitter = ((outlier_index % 5) - 2) * 4
                    circle(center_x + jitter, self._plot_y(layout, outlier), 2, series.color)
                if len(summary.outliers) > 80:
                    text(center_x, layout.plot_top + 16, f"+{len(summary.outliers) - 80} outliers", "middle", series.color, 8)

                max_y = max(layout.plot_top + 12, true_max_y - 5)
                min_y = min(layout.plot_bottom - 12, true_min_y + 5)
                avg_label_y = self._avg_label_y_below_min(layout, min_y, VALUE_LABEL_FONT_SIZE)
                label(
                    center_x,
                    max_y,
                    f"max {format_number(series.stats.maximum, 3)}",
                    "middle",
                    size=VALUE_LABEL_FONT_SIZE,
                    scaled=False,
                )
                label(
                    center_x,
                    min_y,
                    f"min {format_number(series.stats.minimum, 3)}",
                    "middle",
                    size=VALUE_LABEL_FONT_SIZE,
                    scaled=False,
                )
                label(
                    center_x,
                    avg_label_y,
                    f"avg {format_number(series.stats.mean, 3)}",
                    "middle",
                    size=VALUE_LABEL_FONT_SIZE,
                )
                text(
                    center_x,
                    layout.plot_bottom + FILE_LABEL_OFFSET,
                    cropped_single_line_text(
                        series.file_name or series.name,
                        slot_right - slot_left - 4,
                        FILE_LABEL_FONT_SIZE,
                    ),
                    "middle",
                    series.color,
                    FILE_LABEL_FONT_SIZE,
                    "700",
                )

        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{layout.content_width}" '
            f'height="{layout.height}" viewBox="0 0 {layout.content_width} {layout.height}">'
            + "".join(elements)
            + "</svg>\n"
        )
        return svg

    def _plot_to_png(self, layout: PlotLayout, path: Path) -> None:
        from PIL import Image, ImageColor, ImageDraw, ImageFont

        scale = PLOT_EXPORT_SCALE
        image = Image.new(
            "RGBA",
            (layout.content_width * scale, layout.height * scale),
            "white",
        )
        draw = ImageDraw.Draw(image)
        fonts: dict[tuple[int, str, bool], ImageFont.ImageFont] = {}

        def xy(value: float) -> int:
            return int(round(value * scale))

        def scaled_width(value: int) -> int:
            return max(1, int(round(value * scale)))

        def scaled_font(size: int, weight: str = "normal", scaled: bool = True) -> ImageFont.ImageFont:
            key = (size, weight, scaled)
            if key in fonts:
                return fonts[key]
            font_names = (
                ("segoeuib.ttf", "arialbd.ttf")
                if weight in {"bold", "700"}
                else ("segoeui.ttf", "arial.ttf")
            )
            rendered_size = plot_font_size(size) if scaled else size
            font = None
            for font_name in font_names:
                try:
                    font = ImageFont.truetype(font_name, rendered_size * scale)
                    break
                except OSError:
                    continue
            if font is None:
                font = ImageFont.load_default()
            fonts[key] = font
            return font

        def draw_line(
            points: tuple[float, float, float, float],
            fill: str,
            width: int = 1,
            dash: bool = False,
        ) -> None:
            x1, y1, x2, y2 = points
            if not dash:
                draw.line(
                    (xy(x1), xy(y1), xy(x2), xy(y2)),
                    fill=fill,
                    width=scaled_width(width),
                )
                return
            segments = 12
            for segment in range(segments):
                if segment % 2:
                    continue
                start = segment / segments
                end = (segment + 1) / segments
                draw.line(
                    (
                        xy(x1 + (x2 - x1) * start),
                        xy(y1 + (y2 - y1) * start),
                        xy(x1 + (x2 - x1) * end),
                        xy(y1 + (y2 - y1) * end),
                    ),
                    fill=fill,
                    width=scaled_width(width),
                )

        def draw_text(
            x: float,
            y: float,
            value: str,
            fill: str = "#222222",
            anchor: str = "mm",
            size: int = 10,
            weight: str = "normal",
        ) -> None:
            draw.text(
                (xy(x), xy(y)),
                value,
                fill=fill,
                anchor=anchor,
                font=scaled_font(size, weight),
            )

        def draw_label(x: float, y: float, value: str, anchor: str = "mm", size: int = 9, scaled: bool = True) -> None:
            font = scaled_font(size, scaled=scaled)
            draw.text((xy(x), xy(y)), value, fill="#222222", anchor=anchor, font=font)

        draw_text(layout.plot_left, 20, "STANDARDIZE Box Plot", "#222222", "lm", 16, "700")
        draw_line((layout.plot_left, layout.plot_bottom, layout.plot_right, layout.plot_bottom), "#333333")
        draw_line((layout.plot_left, layout.plot_top, layout.plot_left, layout.plot_bottom), "#333333")

        for tick in range(5):
            ratio = tick / 4
            value = layout.y_min + (layout.y_max - layout.y_min) * ratio
            y = self._plot_y(layout, value)
            draw_line((layout.plot_left - 5, y, layout.plot_right, y), "#e6e6e6")
            draw_text(layout.plot_left - 10, y, format_number(value, 2), "#555555", "rm", 9)

        zero_y = self._plot_y(layout, 0)
        if layout.plot_top <= zero_y <= layout.plot_bottom:
            draw_line((layout.plot_left, zero_y, layout.plot_right, zero_y), "#9a9a9a", dash=True)

        draw_text((layout.plot_left + layout.plot_right) / 2, layout.height - 18, "Columns", "#444444", "mm", 10)
        draw_text(18, (layout.plot_top + layout.plot_bottom) / 2, "Standardized value", "#444444", "mm", 10)

        for group_index, group in enumerate(layout.groups):
            group_left, group_center, _group_right = self._plot_group_slot(layout, group_index)
            if group_index > 0:
                draw_line(
                    (
                        group_left,
                        layout.plot_top,
                        group_left,
                        layout.plot_bottom + GROUP_SEPARATOR_BOTTOM_OFFSET,
                    ),
                    "#efefef",
                )
            draw_text(
                group_center,
                layout.plot_bottom + GROUP_LABEL_OFFSET,
                shortened_text(group.column_name, 34),
                "#222222",
                "mm",
                GROUP_LABEL_FONT_SIZE,
                "700",
            )

            for series_index, (series, summary) in enumerate(group.series):
                slot_left, center_x, slot_right, box_width = self._plot_series_slot(layout, group_index, series_index)
                box_left = center_x - box_width / 2
                box_right = center_x + box_width / 2
                cap_left = center_x - box_width * 0.35
                cap_right = center_x + box_width * 0.35
                marker_left = center_x - box_width * 0.62
                marker_right = center_x + box_width * 0.62

                q1_y = self._plot_y(layout, summary.q1)
                median_y = self._plot_y(layout, summary.median)
                q3_y = self._plot_y(layout, summary.q3)
                lower_whisker_y = self._plot_y(layout, summary.lower_whisker)
                upper_whisker_y = self._plot_y(layout, summary.upper_whisker)
                true_min_y = self._plot_y(layout, summary.minimum)
                true_max_y = self._plot_y(layout, summary.maximum)

                draw_line((center_x, upper_whisker_y, center_x, q3_y), series.color, 2)
                draw_line((center_x, q1_y, center_x, lower_whisker_y), series.color, 2)
                draw_line((cap_left, upper_whisker_y, cap_right, upper_whisker_y), series.color, 2)
                draw_line((cap_left, lower_whisker_y, cap_right, lower_whisker_y), series.color, 2)
                draw.rectangle(
                    (xy(box_left), xy(q3_y), xy(box_right), xy(q1_y)),
                    fill=ImageColor.getrgb(blend_hex_color(series.color)),
                    outline=series.color,
                )
                draw_line((box_left, median_y, box_right, median_y), "#222222", 2)
                draw_line((marker_left, true_max_y, marker_right, true_max_y), "#555555", dash=True)
                draw_line((marker_left, true_min_y, marker_right, true_min_y), "#555555", dash=True)

                for outlier_index, outlier in enumerate(summary.outliers[:80]):
                    jitter = ((outlier_index % 5) - 2) * 4
                    outlier_y = self._plot_y(layout, outlier)
                    radius = 2
                    draw.ellipse(
                        (
                            xy(center_x + jitter - radius),
                            xy(outlier_y - radius),
                            xy(center_x + jitter + radius),
                            xy(outlier_y + radius),
                        ),
                        fill=series.color,
                        outline=series.color,
                    )

                max_y = max(layout.plot_top + 12, true_max_y - 5)
                min_y = min(layout.plot_bottom - 12, true_min_y + 5)
                avg_label_y = self._avg_label_y_below_min(layout, min_y, VALUE_LABEL_FONT_SIZE)
                draw_label(
                    center_x,
                    max_y,
                    f"max {format_number(series.stats.maximum, 3)}",
                    size=VALUE_LABEL_FONT_SIZE,
                    scaled=False,
                )
                draw_label(
                    center_x,
                    min_y,
                    f"min {format_number(series.stats.minimum, 3)}",
                    size=VALUE_LABEL_FONT_SIZE,
                    scaled=False,
                )
                draw_label(
                    center_x,
                    avg_label_y,
                    f"avg {format_number(series.stats.mean, 3)}",
                    "mm",
                    size=VALUE_LABEL_FONT_SIZE,
                )
                draw_text(
                    center_x,
                    layout.plot_bottom + FILE_LABEL_OFFSET,
                    cropped_single_line_text(
                        series.file_name or series.name,
                        slot_right - slot_left - 4,
                        FILE_LABEL_FONT_SIZE,
                    ),
                    series.color,
                    "mm",
                    FILE_LABEL_FONT_SIZE,
                    "700",
                )

        image.convert("RGB").save(path, "PNG", dpi=(PLOT_EXPORT_DPI, PLOT_EXPORT_DPI))

    def export_csv(self) -> None:
        if self.normalized_dataframe is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export normalized CSV",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            self.normalized_dataframe.to_csv(path, index=False)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self.status_var.set(f"CSV export failed: {exc}")
            return
        self.status_var.set(f"Exported CSV: {path}")

    def export_excel(self) -> None:
        if self.normalized_dataframe is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export normalized Excel",
            defaultextension=".xlsx",
            filetypes=(("Excel files", "*.xlsx"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            self.normalized_dataframe.to_excel(path, index=False)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self.status_var.set(f"Excel export failed: {exc}")
            return
        self.status_var.set(f"Exported Excel: {path}")


def run_self_test() -> int:
    standardized, stats = calculate_standardize([10, 20, 30])
    checks = (
        stats.count == 3,
        math.isclose(stats.mean, 20.0),
        math.isclose(stats.sample_stdev, 10.0),
        math.isclose(standardized[0] or 0.0, -1.0),
        math.isclose(standardized[1] or 0.0, 0.0),
        math.isclose(standardized[2] or 0.0, 1.0),
    )
    if not all(checks):
        print("Self-test failed.")
        return 1
    print("Self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()

    app = StandardizeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
