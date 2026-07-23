# Standardize Data Plotter

Windows GUI for loading one or more CSV, TSV, TXT, XLSX, XLSM, or XLS files, selecting numeric columns, calculating Excel-compatible `STANDARDIZE(value, AVERAGE(range), STDEV.S(range))` values, and plotting box-and-whisker charts with original min, max, and average labels.

Files can be added with the `Add Files` button or by dragging and dropping supported files onto the window. Adding another file after one file is already loaded switches the app into multifile mode while keeping the existing file. Use `Clear Files` to reset all loaded files and results. With multiple input files, choose one file as the reference; all selected columns are normalized using that reference file's average and sample standard deviation.

Numeric columns can be selected or removed with a single click. Each selected column is plotted as a group, with each input file's boxplot shown adjacent inside that group. After normalizing, use `Export Plot` to save the chart as a high-resolution PNG file by default.

## Setup

On this Codex workstation, the bundled Python can run the GUI directly:

```powershell
.\run_standardize_gui.ps1
```

If Windows blocks PowerShell scripts, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_standardize_gui.ps1
```

For a separately installed Python, install dependencies with:

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python .\standardize_gui.py
```

You can also double-click `run_standardize_gui.bat` on Windows.

If `python` only prints `Python`, Windows is using the Microsoft Store app alias instead of a real Python install. Use `run_standardize_gui.ps1` or install Python from python.org and disable the Python app execution aliases in Windows settings.

## Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The distributable file is created at `dist\StandardizeDataPlotter.exe`.

Running the EXE opens the GUI and the process remains active until the GUI window is closed. This is expected behavior for a Windows desktop application.

## Notes

- XLSX and XLSM files require `openpyxl`.
- Legacy XLS files require `xlrd`.
- Drag and drop requires `tkinterdnd2`.
- PNG plot export requires `pillow`.
- Plot whiskers use the 1.5 IQR rule, and outliers are shown as points.
- Plot labels use enlarged fonts for screen and export readability.
- Plot export saves PNG by default at 3x scale with 300 DPI metadata; use a `.svg` extension to save SVG.
- CSV delimiter detection is automatic for comma, tab, semicolon, and similar text files.
- Columns with fewer than two numeric values or zero sample standard deviation are skipped.
