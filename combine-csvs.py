#!/usr/bin/env python3

# Combine BeamNG CSV outputs (inconsistent fields) into one CSV.
#
# - Recursively finds CSV files under an input folder (by default, only those whose
#   basename starts with "mods_index_on_" or "allpairs_on_" or ends with ".csv").
# - Unions all columns across files; missing fields are left blank.
# - Two-pass approach: first pass collects headers; second pass writes rows.
# - Optional: add a source filename column; filter which CSVs to include; exclude summaries.
#
# Usage:
#   python "C:\Users\Administrator\projects\BeamNG\combine-csvs.py" -i "C:\_lib\_BeamNG__\____directory-extract____\_output" -o "C:\_lib\_BeamNG__\____directory-extract____\combined.csv"
#   python combine-csvs.py -i "/mnt/c/_lib/_BeamNG__/output" -o combined.csv --include "mods_index_on_*.csv" --include "allpairs_on_*.csv" --exclude "*keys_summary*.csv" --add-source-col
#
# Notes:
#   - Handles UTF-8 and UTF-8 with BOM; falls back to latin-1 if needed.
#   - Skips empty files and those with no header row.
#   - Preserves a useful header order: preferred fields first (if present), then the rest sorted.


from __future__ import annotations

import argparse
import csv
import fnmatch
import os
import sys
from typing import Iterable, List, Set, Dict, Tuple

# Common preferred columns seen in the main inventory script
PREFERRED_FIRST = [
    "row_id",
    "file_path",
    "directory",
    "file_name",
    "file_size_bytes",
    "date_created",
    "date_modified",
    "top_level_dir",
    "map_name",
    "vehicle_name",
    "info_json_count",
    "info_json_paths",
    "authors",
    "last_update_human",
    "resource_date_human",
    "message",              # often present and long
    "zip_error",
]

DEFAULT_INCLUDE_PATTERNS = ["mods_index_on_*.csv", "allpairs_on_*.csv", "*.csv"]
DEFAULT_EXCLUDE_PATTERNS = ["*keys_summary*.csv"]  # usually not desired in combined row data

def iter_csv_files(root: str, includes: List[str], excludes: List[str]) -> Iterable[str]:
    """Yield CSV paths under root matching includes and not matching excludes (case-insensitive)."""
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            low = fn.lower()
            path = os.path.join(dirpath, fn)
            # include check
            inc = any(fnmatch.fnmatch(fn, pat) or fnmatch.fnmatch(low, pat.lower()) for pat in includes)
            if not inc:
                continue
            # exclude check
            exc = any(fnmatch.fnmatch(fn, pat) or fnmatch.fnmatch(low, pat.lower()) for pat in excludes)
            if exc:
                continue
            yield path

def open_csv_reader(path: str):
    """Return (file_handle, DictReader) trying encodings in order."""
    # try utf-8-sig, then utf-8, then latin-1
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            f = open(path, "r", newline="", encoding=enc, errors="replace")
            r = csv.DictReader(f)
            # Validate header presence
            if r.fieldnames and any((h or "").strip() for h in r.fieldnames):
                return f, r
            f.close()
        except Exception:
            try:
                f.close()
            except Exception:
                pass
    return None, None

def compute_header_order(all_headers: Set[str], preferred_first: List[str]) -> List[str]:
    """Order headers with preferred_first first (if present), then remaining sorted."""
    ordered = [h for h in preferred_first if h in all_headers]
    remaining = sorted(h for h in all_headers if h not in ordered)
    ordered.extend(remaining)
    return ordered

def main():
    ap = argparse.ArgumentParser(description="Combine BeamNG CSV outputs into one CSV (unions columns).")
    ap.add_argument("-i", "--input-root", required=True, help="Root folder to search for CSVs (recursively).")
    ap.add_argument("-o", "--output", required=True, help="Combined CSV output path.")
    ap.add_argument("--include", action="append", default=None, help="Glob pattern to include (can repeat). Default: mods_index_on_*.csv, allpairs_on_*.csv, *.csv")
    ap.add_argument("--exclude", action="append", default=None, help="Glob pattern to exclude (can repeat). Default: *keys_summary*.csv")
    ap.add_argument("--add-source-col", action="store_true", help="Add a 'source_file' column with the basename of the input CSV.")
    ap.add_argument("--quiet", action="store_true", help="Less console output.")
    args = ap.parse_args()

    includes = args.include or DEFAULT_INCLUDE_PATTERNS
    excludes = args.exclude or DEFAULT_EXCLUDE_PATTERNS

    # First pass: discover files and union headers
    files = list(iter_csv_files(args.input_root, includes, excludes))
    if not files:
        print("No CSV files found matching your patterns.", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Found {len(files)} CSV(s) to combine.")

    all_headers: Set[str] = set()
    readable_files: List[str] = []

    for path in files:
        fh, rdr = open_csv_reader(path)
        if rdr is None:
            if not args.quiet:
                print(f"Skipping unreadable or headerless CSV: {path}")
            continue
        # Collect field names; keep reference for second pass use?
        for h in rdr.fieldnames or []:
            if h is not None:
                all_headers.add(h)
        readable_files.append(path)
        try:
            fh.close()
        except Exception:
            pass

    if not readable_files:
        print("No readable CSVs with headers.", file=sys.stderr)
        sys.exit(1)

    # Optionally add source_file column
    if args.add_source_col:
        all_headers.add("source_file")

    # Order headers
    header_order = compute_header_order(all_headers, PREFERRED_FIRST)

    # Second pass: write combined CSV
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    written_rows = 0
    with open(args.output, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=header_order)
        writer.writeheader()

        for path in readable_files:
            fh, rdr = open_csv_reader(path)
            if rdr is None:
                continue
            try:
                for row in rdr:
                    combined = {k: "" for k in header_order}
                    for k, v in row.items():
                        if k in combined:
                            combined[k] = v
                    if args.add_source_col:
                        combined["source_file"] = os.path.basename(path)
                    writer.writerow(combined)
                    written_rows += 1
            finally:
                try:
                    fh.close()
                except Exception:
                    pass

    if not args.quiet:
        print(f"Wrote {written_rows} combined row(s) to {args.output}")
        print(f"Headers ({len(header_order)}): {', '.join(header_order)}")

if __name__ == "__main__":
    main()
