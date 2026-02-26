# BeamNG ZIP Management and BeamMP Server Management

This app is for **BeamNG / BeamMP players and mod hoarders** who want to **inventory, normalize, edit, rename, and merge mod ZIP metadata (and export BeamMP server lists) into analysis-friendly CSVs**.

## Table of contents

- [Overview](#overview)
- [Problem statement](#problem-statement)
- [Brief MNP description](#brief-mnp-description)
- [Features](#features)
- [Project structure](#project-structure)
- [Setup and run instructions](#setup-and-run-instructions)
- [Configuration](#configuration)
- [Usage](#usage)
  - [BeamNG ZIP inventory to CSV](#beamng-zip-inventory-to-csv)
  - [Extract all info.json key/value pairs (long-form)](#extract-all-infojson-keyvalue-pairs-long-form)
  - [Combine CSV outputs into one CSV](#combine-csv-outputs-into-one-csv)
  - [Rename BeamNG ZIPs based on metadata](#rename-beamng-zips-based-on-metadata)
  - [Edit info.json key/values inside ZIPs (safe workflow)](#edit-infojson-keyvalues-inside-zips-safe-workflow)
  - [BeamMP server JSON to CSV](#beammp-server-json-to-csv)
  - [BeamMP: list all unique server keys](#beammp-list-all-unique-server-keys)
  - [Windows shortcut: silent parallel extract (VBS)](#windows-shortcut-silent-parallel-extract-vbs)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Notes and limitations](#notes-and-limitations)

## Overview

This repo is a small toolbox of scripts that operate on two main things:

1. **BeamNG mod ZIPs** (inventory/extract metadata from `info.json` and `ui/modules/apps/**/app.json`, write CSVs, optionally stash long fields into sidecar files, rename ZIPs, and edit metadata inside ZIPs safely).  
2. **BeamMP server list JSON** (turn a JSON dump into a CSV, and optionally dump the complete set of keys present across server objects).  

Each script is standalone and uses only standard Python libraries (plus Windows Script Host for the `.vbs` shortcut).

## Problem statement

If you’ve got lots of BeamNG mods spread across drives and folders, the basic problems become:

- You can’t easily answer “what mods do I have, where are they, and what metadata do they contain?”
- Metadata inside ZIPs is inconsistent (different keys, nested structures, long text fields), so analysis becomes messy.
- Renaming ZIPs consistently is tedious and risky.
- Editing `info.json` inside ZIPs is risk-prone if you write in-place without validation.
- BeamMP server data is JSON-heavy and awkward to filter/sort in spreadsheets.

These scripts solve that by producing **repeatable CSV exports**, keeping **full long fields in sidecars**, and providing **safe rename/edit workflows**.

## Brief MNP description

Right now, the app can:

- Scan mod ZIP folders and output a **single “inventory CSV”** per root/drive, with derived fields (map/vehicle/UI app names), normalized metadata fields, caching, and sidecar files for truncations and long “message” text.  
- Extract **every key/value from every `info.json`**, flatten it to dot paths, and produce a long-form dataset plus a key-frequency summary.  
- Combine multiple CSV outputs into one consistent combined CSV.  
- Rename ZIP files using metadata-derived naming conventions (dry-run by default).  
- Edit `info.json` key/values in a safe way (build `.edited.zip`, validate, then optionally swap in-place with backups).  
- Convert a BeamMP server JSON dump into a CSV for spreadsheet filtering.  

## Features

### BeamNG ZIP inventory extractor (`beamng_zip_extract_v4.5.py`)

- Recursively finds `.zip` files under a root folder.  
- Collects file info (`file_name`, `directory`, size, created/modified times, stable-ish `row_id`).  
- Searches inside zips for:
  - `info.json` (anywhere, but filters paths using an exclusion list)
  - UI apps: `ui/modules/apps/**/app.json`  
- Derives:
  - `map_name` from `levels/<name>/info.json`
  - `vehicle_name` from `vehicles/<name>/info.json`
  - `ui_name` from `ui/modules/apps/<name>/**/app.json`  
- Applies selection rules to decide which JSON files are “primary” for metadata extraction (`json_rule_used`), and aggregates metadata values across selected JSONs.  
- **Moves long `message` text out of the CSV** into per-row text files and keeps an optional `message_preview` in the CSV.  
- Writes a sidecar `*.details.jsonl` with full text for any CSV cells truncated by `--max-cell-chars`.  
- Uses a SQLite cache (`*.cache.sqlite`) keyed by file path + size + mtime to skip re-parsing unchanged zips.  

### Extract all key/value pairs (`extract_key_val_pairs_from_zips.py`)

- For every zip and every `info.json` inside it:
  - Parses JSON with a “cleanup” pass (comments, trailing commas, BOM).  
  - Flattens nested keys to dot-paths (`a.b.c`) and list indices (`arr[0]`).  
  - Writes a long-form CSV:
    `row_id, file_path, info_json_path, key_path, value, value_type`  
- Writes:
  - Sidecar `*.details.jsonl` when values are truncated.
  - Key summary CSV (count of pairs and unique zips per key).  

### Combine CSVs (`combine-csvs.py`)

- Recursively finds CSV files under an input folder using include/exclude glob patterns.  
- Unions all columns across files and writes a combined CSV with a stable header order:
  - Preferred fields first (if present), then the rest sorted.  
- Can optionally add a `source_file` column.  

### Rename ZIPs (`beamng_zip_renamer.py`)

- Determines whether a zip is a **vehicle** or **map** by looking for `vehicles/**/info.json` or `levels/**/info.json`.  
- Builds filenames:
  - Vehicles: `"[vehicle][car][{Brand}][{Body Style}] {Name}{ver}.zip"`
  - Maps: `"[map][{category}] {Title}{ver}.zip"`  
- Version suffix: prefer parsing it from the existing filename (` v1.2`), else `version_string` in JSON.  
- Dry-run by default; `--apply` actually renames. Name conflicts get ` (n)` appended. Optional CSV log.  

### Edit `info.json` inside ZIPs safely (`beamng_zip_edit_kv.py`)

- Never writes directly to the original zip:
  - Writes `<name>.edited.zip`, validates it, then optionally swaps in-place and keeps a `.bak`.  
- Supports key operations on JSON (dot-path keys supported):
  - `--set key=val` (val can be JSON or a bare string)
  - `--remove key`
  - `--rename old:new`  
- Scope and selection controls:
  - `--scope vehicles,levels,mod_info,all`
  - `--prefer-primary`
  - `--include-mod-info`  
- Dry-run by default; `--apply` writes output. Optional CSV log.  

### BeamMP helpers

- `beammp_servers_to_csv.py`: reads `beammp_servers.json` and writes `beammp_servers.csv`, selecting a fixed list of columns.  
- `beammp_combine_server_keys.py`: scans server objects and outputs every unique key to `beammp_keys.txt`.  

### Windows shortcut script (`silent_zip_extract.vbs`)

- Runs the BeamNG zip inventory extractor on multiple roots **in parallel** using `pythonw.exe` (hidden window).  
- Waits until the expected CSV outputs exist and stop changing (“stable file” check), then (optionally) runs the combine step (currently commented out in the file).  

## Project structure

Based on the paths shown in the provided code:

```
.
├── beammp
│   ├── beammp_servers_to_csv.py
│   └── get_keys
│       └── beammp_combine_server_keys.py
├── beamng
│   ├── combine
│   │   └── combine-csvs.py
│   ├── extract
│   │   ├── beamng_zip_extract_v4.5.py
│   │   └── extract_key_val_pairs_from_zips.py
│   ├── rename
│   │   └── beamng_zip_renamer.py
│   └── zip_edit
│       └── beamng_zip_edit_kv.py
└── shortcuts
    └── silent_zip_extract.vbs
```

## Setup and run instructions

### Requirements

- Python 3.x (these scripts use only standard library modules: `argparse`, `csv`, `json`, `zipfile`, `sqlite3`, etc.).  
- On Windows, the `.vbs` helper needs Windows Script Host (standard on most Windows installs).  

### Quick start

1. Put your scripts somewhere convenient (for example, a repo folder).
2. From a terminal:
   - Run the script you need using `python` (Windows) or `python3` (Linux/macOS).

## Configuration

These scripts primarily use **command-line flags**. There are **no environment variables or config files required by the code shown**.  

The only “configuration-like” values that exist are:
- Defaults baked into flags (example: `--max-cell-chars` default 1000).
- In the `.vbs` file, constants like `EXTRACT_PY`, `OUT_DIR`, and the list of drive roots.  

## Usage

Below are practical examples you can copy/paste. Adjust paths for your machine.

### BeamNG ZIP inventory to CSV

Script: `beamng/extract/beamng_zip_extract_v4.5.py`  

#### Run

```bash
python beamng/extract/beamng_zip_extract_v4.5.py -r "D:\__BeamNG__\___mods___" --out-base-dir "G:\My Drive\__BeamNG__\____directory-extract____\output"
```

#### Expected output

- A CSV named `mods_index_on_<DRIVE>.csv` in `--out-base-dir` (unless you used `-o/--output`).  
- A sidecar JSONL file next to the CSV: `mods_index_on_<DRIVE>.details.jsonl` containing full content for truncated cells.  
- A message folder next to the CSV (default `__messages/`), containing one file per row_id with the full `message` field.  
- A SQLite cache DB next to the CSV: `mods_index_on_<DRIVE>.cache.sqlite`.  

#### Flags (what they do)

- `-r, --root` (required): root folder to scan recursively for `.zip`.  
- `-o, --output`: explicit output CSV path (overrides the auto `mods_index_on_<DRIVE>.csv`).  
- `--out-base-dir`: if `--output` is omitted, write CSVs into this directory. Filename is derived from the root drive/letter.  
- `--max-cell-chars` (default `1000`): max characters per CSV cell; longer values are truncated and recorded in `*.details.jsonl`.  
- `--exclude-dirs` (default is a comma list like `.git,art,gameplay,...`): directory names to exclude when scanning internal zip paths for JSON candidates (“ground zero” exclusions).  
- `--progress-every` (default `50`): print progress every N zips; `0` disables.  
- `--quiet`: suppress progress output.  
- `--popup` / `--no-popup`: force or disable a completion popup (uses `tkinter` if available).  
- `--message-preview-chars` (default `500`): how many message characters to keep in CSV (`message_preview`); `0` disables preview.  
- `--messages-subdir` (default `__messages`): folder name (relative to CSV output folder) to store per-row message files.  
- `--refresh-existing`: overwrite existing message files even if already present.  
- `--prune-missing`: delete message files in the messages folder that don’t match any `row_id` in the current run.  

### Extract all info.json key/value pairs (long-form)

Script: `beamng/extract/extract_key_val_pairs_from_zips.py`  

```bash
python beamng/extract/extract_key_val_pairs_from_zips.py -r "D:\__BeamNG__" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____"
```

#### Expected output

- `allpairs_on_<DRIVE>.csv` (long-form flattened key/value rows)
- `allpairs_on_<DRIVE>.details.jsonl` (full values for truncated cells)
- `keys_summary_on_<DRIVE>.csv` (how often each key_path appears + unique zips count)  

#### Flags

- `-r, --root` (required): root folder containing zips.  
- `-o, --output`: explicit path for the long-form CSV; summary name is derived from it.  
- `--out-base-dir`: where to write `allpairs_on_<DRIVE>.csv` and `keys_summary_on_<DRIVE>.csv` if `--output` is omitted.  
- `--max-cell-chars` (default `1000`): truncation limit; full values go to sidecar JSONL.  

### Combine CSV outputs into one CSV

Script: `beamng/combine/combine-csvs.py`  

```bash
python beamng/combine/combine-csvs.py -i "G:\My Drive\__BeamNG__\____directory-extract____\output" -o "G:\My Drive\__BeamNG__\____directory-extract____\combined.csv" --add-source-col
```

#### Expected output

- A combined CSV at `--output`
- Console summary including header count and total rows written (unless `--quiet`).  

#### Flags

- `-i, --input-root` (required): root folder to search (recursively) for CSVs.  
- `-o, --output` (required): combined CSV output path.  
- `--include` (repeatable): include glob patterns. Default includes `mods_index_on_*.csv`, `allpairs_on_*.csv`, and `*.csv`.  
- `--exclude` (repeatable): exclude glob patterns. Default excludes `*keys_summary*.csv`.  
- `--add-source-col`: add a `source_file` column containing the basename of each input CSV.  
- `--quiet`: less console output.  

### Rename BeamNG ZIPs based on metadata

Script: `beamng/rename/beamng_zip_renamer.py`  

Dry-run first (recommended):

```bash
python beamng/rename/beamng_zip_renamer.py -r "D:\BeamNG\mods"
```

Apply renames and write a log:

```bash
python beamng/rename/beamng_zip_renamer.py -r "D:\BeamNG\mods" --apply --log "rename_log.csv"
```

#### Flags

- `-r, --root` (required): folder to scan (recursively) for `.zip`.  
- `--apply`: actually rename files (otherwise dry-run).  
- `--log`: write a CSV log of planned/applied changes.  

### Edit info.json key/values inside ZIPs (safe workflow)

Script: `beamng/zip_edit/beamng_zip_edit_kv.py`  

Dry run:

```bash
python beamng/zip_edit/beamng_zip_edit_kv.py -r "C:\__BeamNG__\___zip edit test" --scope levels,mod_info --set map_category=offroad --prefer-primary --include-mod-info --log "edit_log.csv"
```

Apply edits (creates `*.edited.zip`):

```bash
python beamng/zip_edit/beamng_zip_edit_kv.py -r "C:\__BeamNG__\___zip edit test" --scope levels,mod_info --set map_category=offroad --prefer-primary --include-mod-info --apply --log "edit_log.csv"
```

Apply and swap in-place (creates `.bak` backup):

```bash
python beamng/zip_edit/beamng_zip_edit_kv.py -r "C:\__BeamNG__\___zip edit test" --scope levels,mod_info --set map_category=offroad --prefer-primary --include-mod-info --apply --in-place --log "edit_log.csv"
```

#### Flags

- `-r, --root` (required): folder to scan recursively for zips.  
- `--scope` (default `all`): comma list of `vehicles`, `levels`, `mod_info`, or `all`. Controls which `info.json` paths get edited.  
- `--prefer-primary`: if `vehicles/` or `levels/` exists, edit those and ignore other locations (except mod_info when `--include-mod-info`).  
- `--include-mod-info`: always include `mod_info/info.json` when present (depending on scope).  
- `--set` (repeatable): `key=val` set operation; `val` may be JSON (`{"a":1}`), booleans, numbers, etc.  
- `--remove` (repeatable): remove a key (dot-path supported).  
- `--rename` (repeatable): `old:new` rename operation (dot-path supported).  
- `--apply`: actually write changes; without this the script logs dry-run actions.  
- `--in-place`: after validation, replace the original zip and keep a `.bak` backup.  
- `--log`: path to a CSV log file (or a directory, in which case it writes `edit_log.csv`).  

### BeamMP server JSON to CSV

Script: `beammp/beammp_servers_to_csv.py`  

This script expects a local `beammp_servers.json` in the current working directory and writes `beammp_servers.csv`.

```bash
python beammp/beammp_servers_to_csv.py
```

Notes:
- The CSV columns are hard-coded in the script (`cols = [...]`).  

### BeamMP: list all unique server keys

Script: `beammp/get_keys/beammp_combine_server_keys.py`  

This reads `../beammp_servers.json` (relative to the script folder) and writes `beammp_keys.txt`.

```bash
python beammp/get_keys/beammp_combine_server_keys.py
```

### Windows shortcut: silent parallel extract (VBS)

Script: `shortcuts/silent_zip_extract.vbs`  

What it does (based on the file):

- Uses `pythonw.exe` (hidden console) to run `beamng_zip_extract_v4.5.py` for each configured root (example roots include `D:`, `M:`, `C:`, `R:`).  
- Waits until the expected output CSVs stop changing for multiple checks (stable size + stable modified time).  
- A “combine” step exists but is commented out.  

To use it:
1. Open the `.vbs` and set:
   - `EXTRACT_PY` to your extractor script path
   - `OUT_DIR` to your output folder
   - `roots(...)` and `expected(...)` to match the drives/paths you want  
2. Double-click the `.vbs` (or run via `cscript`/`wscript`).

## Testing

There are no unit tests or test runner configuration in the provided code.  

Practical testing approach (what the scripts themselves imply):

- Run in **dry-run modes** when available:
  - ZIP renamer: omit `--apply`
  - ZIP editor: omit `--apply`  
- For the extractor scripts, test on a **small folder** first and confirm:
  - CSV is produced and opens correctly
  - Sidecar `*.details.jsonl` is valid JSONL
  - Messages folder contains expected `<row_id>.txt` files (when `message` exists)  

## Troubleshooting

### “No CSV files found matching your patterns” (combine-csvs)

- Your `--input-root` folder doesn’t contain files matching the default include patterns, or they’re excluded by the default exclude pattern.  
- Fix: add `--include "*.csv"` (or the exact pattern you need), or adjust `--exclude`.

### “No readable CSVs with headers” (combine-csvs)

- The tool skips CSVs that are empty or have no header row.  
- Fix: confirm the source CSVs are complete and have headers.

### Zips reported as `BadZipFile` (inventory extractor / zip editor)

- The zip is corrupt or not a real zip. The extractor sets `zip_error = "BadZipFile"`.  
- Fix: re-download or re-pack the mod, or exclude that folder.

### Message files not being overwritten

- By default, existing `<row_id>.txt` message files are not overwritten.
- Fix: use `--refresh-existing`.  

### Too many truncations in CSV

- Default `--max-cell-chars` is 1000 in both extractor scripts.  
- Fix: increase `--max-cell-chars`, and/or rely on the `.details.jsonl` sidecar for the full content.

### Popup doesn’t appear (or appears when you don’t want it)

- Popup is conditional on `tkinter` availability and whether it looks like a manual run; the script also offers `--popup` and `--no-popup`.  
- Fix: use `--no-popup` for scheduled runs, `--popup` to force it.

## Notes and limitations

- **BeamMP JSON isn’t fetched in these scripts**: `beammp_servers_to_csv.py` assumes `beammp_servers.json` already exists locally.  
- **Inventory “row_id” changes if the file changes**: it’s derived from absolute path + file size + modified time. Renames/moves or edits will produce a new `row_id`.  
- **SQLite cache keys are strict**: cache hits require the same `file_path`, `file_size`, and `mtime`. If you copy a zip to a different path, it will be re-parsed.  
- **ZIP editor edits only `info.json` files** (and only those within your selected scope rules). It doesn’t try to edit arbitrary JSON files beyond that.  
- **Renamer logic focuses on `vehicles/` and `levels/`**: if your mod uses unusual folder layouts, it may skip with “no info.json under vehicles/ or levels/”.  
- **Combine step is separate by design**: you can run extraction on multiple roots and combine later, or keep separate CSVs per drive.  
