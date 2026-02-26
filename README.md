BeamNG ZIP Inventory -> CSV (+ sidecar JSONL for truncated fields)

v2 notes:
- Initial version: recursively scans for ZIP files under a root folder, collects file info, and outputs to CSV.

v2.1 notes:
- Added JSON parsing: looks for info.json files and extracts metadata fields into CSV.

v3 notes:
- Recursively scans for ZIP files under a root folder.
- For each ZIP, collects file info and searches for JSON files to extract metadata.

v4 (Clay rules update):
- Progress output: "Processed X/Y" every N zips.
- One row per zip.
- Directory exclusion "ground zero": excluded top-level dirs are not searched for JSON (info.json/app.json).
- JSON selection logic (priority):
  1) If levels/\**/info.json exists (and not excluded): use ALL levels/\**/info.json + ALL mod_info/**/info.json. Nothing else.
     If "levels" dir exists but has no levels info.json => fall through.
  2) Else if vehicles/\**/info.json exists (and not excluded): use ALL vehicles/\**/info.json + ALL mod_info/**/info.json. Nothing else.
     If vehicles dir exists but has no vehicles info.json => fall through.
  3) Else if ui/modules/apps/\**/app.json exists (and not excluded): use ALL those app.json + ALL mod_info/**/info.json. Nothing else.
     - Adds ui_name column: collects the app directory name(s) under ui/modules/apps/<ui_name>/app.json
  4) Else: if ANY mod_info/\**/info.json exists: use ALL mod_info/**/info.json
  5) Else: no json files => set top_level_dir to "no-json-file" and output file info only.
- Multiple JSON files in the chosen rule:
  - We DO NOT create extra rows. We aggregate per field.
  - If a field has multiple distinct values across files, we join them with " | " (order-preserving).
- Adds diagnostics columns:
  - json_rule_used, json_selected_count, json_selected_paths

v4.1 changes:
- Better handling of multiple JSON files: if multiple files have the same field with different values, we join them with " | " in the CSV.

v4.2 changes:
- Added SQLite caching of extracted JSON metadata keyed by zip file path, size, and mtime. This speeds up subsequent runs by skipping ZIP extraction and JSON parsing for unchanged files.

v4.3 changes:
- Added sidecar JSONL output for fields that may be truncated in the CSV (like "message"). Each line is a JSON object with row_id and the full message. This preserves the full content without bloating the CSV.

v4.4 changes:
- Adds has_message column.
- Moves full "message" out of CSV into per-row files: <row_id>.txt in a messages folder.
- Optional message_preview column (default 500 chars) stored in CSV.
- Allows disabling preview entirely (0 chars).

v4.5 changes:
--root
- Changed from optional (default current dir) to required, to avoid accidental runs on wrong folders. Please specify the folder to scan with -r or --root.

--output
- Explicit output CSV path

--out-base-dir
- If provided and --output omitted, save to this folder as mods_index_on_<DRIVE>.csv

--max-cell-chars
- Max characters per CSV cell (default 1000)

--exclude-dirs
- Comma-separated dirs to exclude from JSON search anywhere in the zip path (ground zero).

--progress-every
- Print progress every N zips (default 50). Use 0 to disable.

--quiet
- Suppress progress output.--popup

--popup
- Force a completion popup (manual runs).

--no-popup
- Disable completion popup (scheduled runs).

--message-preview-chars
- How many message chars to keep in CSV as message_preview. Use 0 to disable preview entirely. (default 500)

--messages-subdir
- Folder name (relative to CSV output folder) to store per-row message files. (default __messages)

--refresh-existing (default OFF)
- When enabled, will overwrite message files even if they already exist. This is useful if you want to update message files with new content or fixes, but be aware it will cause more file writes on each run.

--prune-missing (default OFF)
- When enabled, after processing all zips, will delete any message files in the messages folder that don't match any row_id found in this run. This helps clean up message files from mods that have been removed since the last run.
