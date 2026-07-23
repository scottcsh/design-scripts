# Design Scripts

Utilities for protein design workflows and a Windows application for standardizing and plotting tabular data.

## Repository Layout

| Path | Description |
| --- | --- |
| `scripts/` | AlphaFold 3, Foundry ProteinMPNN, RFdiffusion3, and Rosetta workflow scripts. |
| `wintools/` | Windows GUI source, launchers, tests, and build configuration for Standardize Data Plotter. |

## Protein Design Scripts

The `scripts/` directory contains Linux workflow wrappers for preparing AlphaFold 3 inputs, running MSA and inference jobs, generating sequences with Foundry ProteinMPNN, running RFdiffusion3, and filtering structures with Rosetta InterfaceAnalyzer.

### Requirements

- Bash 4 or later.
- Python 3 for JSON generation and report helpers.
- Docker with the AlphaFold 3 image, model parameters, and databases for the AF3 scripts.
- Foundry ProteinMPNN and RFdiffusion3 environments for the Foundry wrappers.
- Rosetta InterfaceAnalyzer, MPI, and the Rosetta database for interface filtering.

Several scripts contain workstation-specific defaults for module names, model directories, database directories, checkpoint paths, GPU IDs, and network binding. Review each script with `--help` and override those defaults before running it on another system.

### Script Reference

| File | Purpose |
| --- | --- |
| `af3_msa_only.sh` | Run only the AlphaFold 3 data pipeline for a protein sequence and save reusable MSA data as JSON. |
| `af3_inputgen.sh` | Combine one FASTA source with reusable AF3 MSA JSON data for other chains and generate AF3 input JSON files. |
| `af3_batch_run.sh` | Split AF3 data-pipeline work into batches and run inference across one or more GPUs. |
| `foundry-proteinmpnn.sh` | Run Foundry ProteinMPNN over PDB or CIF structures in parallel and extract designed-chain FASTA files. |
| `foundry-rfdiffusion3.sh` | Generate RFdiffusion3 input YAML from a PDB, contig, and hotspot list, then run the configured RFdiffusion3 executable. |
| `rosetta_filter.sh` | Optionally prefilter AF3 results, run Rosetta InterfaceAnalyzer, build CSV and HTML reports, and export selected structures. |
| `rosetta_filter_af3.py` | AF3 metric collection, ranking, and preset-based prefilter helper. |
| `rosetta_filter_plot.py` | Interactive HTML report generator for Rosetta and AF3 metrics. |
| `rosetta_filter_server.py` | Local export-enabled server used by the interactive report. |

### Examples

Generate reusable MSA data for one sequence:

```bash
./scripts/af3_msa_only.sh \
  --name test_MSA \
  -i MQSIKGNHLVKVYDYQEDGSVLLTCDAEAKNITWFKDGKMIGFLTEDK
```

Generate AlphaFold 3 input JSON files from designed FASTA records and saved chain-B MSA data:

```bash
./scripts/af3_inputgen.sh \
  -A ./mpnn_fastas \
  -B B_data.json \
  -o ./input_jsons
```

Run an AlphaFold 3 batch across two inference GPUs:

```bash
./scripts/af3_batch_run.sh \
  -i ./input_jsons \
  -o ./af3_output \
  --inference-gpus 0,1
```

Run Foundry ProteinMPNN:

```bash
./scripts/foundry-proteinmpnn.sh \
  -i ./structures \
  -o ./mpnn_out \
  -gpu 0 \
  -chain A
```

Run RFdiffusion3 with hotspot residues:

```bash
./scripts/foundry-rfdiffusion3.sh \
  -i 1SY6.pdb \
  -c '60-120,/0,A118-203' \
  -h 'A144,A150,A151,A161G,A162,A190,A191' \
  -o ./rfd3_output
```

Prefilter AlphaFold 3 results and run Rosetta interface analysis:

```bash
./scripts/rosetta_filter.sh \
  -i ./af3_results \
  -o ./rosetta_filter \
  --af3-top 1000 \
  --af3-preset loose \
  --interface A_B \
  --np 24
```

Run any wrapper with `--help` to view all supported flags and environment overrides.

### Configuration Notes

- AF3 defaults can be changed with variables such as `AF3_DOCKER_IMAGE`, `AF3_MODEL_DIR`, `AF3_DB_DIR`, `AF3_OUTPUT_DIR`, and `AF3_INFERENCE_GPUS`.
- `foundry-proteinmpnn.sh` expects the configured Miniforge module, `rfd3` conda environment, and ProteinMPNN checkpoint.
- `foundry-rfdiffusion3.sh` supports `--module`, `--conda-env`, `--rfd3-bin`, and `--no-env` for environment control.
- Rosetta executable and database paths can be changed with `--executable`, `--database`, `ROSETTA_FILTER_EXE`, and `ROSETTA_FILTER_DATABASE`.
- `sample_scripts.txt` contains short command-name examples for environments where aliases or wrapper commands are installed.

## Standardize Data Plotter

The `wintools/` project is a Windows GUI for loading one or more CSV, TSV, TXT, XLSX, XLSM, or XLS files, selecting numeric columns, calculating Excel-compatible `STANDARDIZE(value, AVERAGE(range), STDEV.S(range))` values, and plotting box-and-whisker charts with original minimum, maximum, and average labels.

Files can be added with the `Add Files` button or by dragging and dropping supported files onto the window. Adding another file after one file is already loaded switches the app into multifile mode while keeping the existing file. Use `Clear Files` to reset all loaded files and results. With multiple input files, choose one file as the reference; all selected columns are normalized using that reference file's average and sample standard deviation.

Numeric columns can be selected or removed with a single click. Each selected column is plotted as a group, with each input file's boxplot shown adjacent inside that group. After normalizing, use `Export Plot` to save the chart as a high-resolution PNG file by default.

### Setup

From the repository root, enter the application directory:

```powershell
cd .\wintools
```

On a Codex workstation with the bundled Python runtime, run:

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

### Run

```powershell
python .\standardize_gui.py
```

You can also double-click `run_standardize_gui.bat` on Windows.

If `python` only prints `Python`, Windows is using the Microsoft Store app alias instead of a real Python installation. Use `run_standardize_gui.ps1`, or install Python from python.org and disable the Python app execution aliases in Windows settings.

### Build the Executable

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The distributable file is created at `dist\StandardizeDataPlotter.exe`.

Running the executable opens the GUI, and the process remains active until the GUI window is closed. This is expected behavior for a Windows desktop application.

### Notes

- XLSX and XLSM files require `openpyxl`.
- Legacy XLS files require `xlrd`.
- Drag and drop requires `tkinterdnd2`.
- PNG plot export requires `pillow`.
- Plot whiskers use the 1.5 IQR rule, and outliers are shown as points.
- Plot labels use enlarged fonts for screen and export readability.
- Plot export saves PNG by default at 3x scale with 300 DPI metadata; use an `.svg` extension to save SVG.
- CSV delimiter detection is automatic for comma, tab, semicolon, and similar text files.
- Columns with fewer than two numeric values or zero sample standard deviation are skipped.
