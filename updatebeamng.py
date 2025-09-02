#!/usr/bin/env python3
# Windows (PowerShell or CMD)
# python "C:\Users\Administrator\projects\BeamNG\updatebeamng.py" -r "C:\_lib\_BeamNG__" -o "C:\_lib\_BeamNG__\____test-extract____\mods_index_on_C.csv"
# python "C:\Users\Administrator\projects\BeamNG\updatebeamng.py" -r "M:\_lib\__BeamNG" -o "C:\_lib\_BeamNG__\____test-extract____\mods_index_on_M.csv"
# python "C:\Users\Administrator\projects\BeamNG\updatebeamng.py" -r "D:\__BeamNG__" -o "C:\_lib\_BeamNG__\____test-extract____\mods_index_on_D.csv"
#
# WSL/Linux/macOS
# python3 beamng_zip_inventory.py -r "/mnt/d/BeamNG/mods" -o "/mnt/d/BeamNG/mods_index.csv" --workers 8
#
# BeamNG ZIP Inventory -> CSV
# Author: ChatGPT (for Clay)
# Description:
#   - Recursively scan a root folder for .zip files (BeamNG mods: maps, vehicles, others).
#   - For each .zip, extract file info (path, name, size, created, modified).
#   - Search inside the .zip for any "info.json" files (case-insensitive). Some zips have 0..N of these.
#   - Parse JSON (robust to common trailing comma issues). Merge keys across multiple info.json files.
#   - Derive "Directory NAME" for maps (levels/NAME/...) and vehicles (vehicles/NAME/...) from entries.
#   - Normalize author(s): "Author" and "authors" go into a single output column "authors".
#   - Convert timestamps like last_update/resource_date to human-readable where possible.
#   - Output a single CSV (one row per zip) with the requested fields. Extra keys are included if present.
#   - NEW: Truncate overly long fields (default 1000 chars) and remove newlines to avoid row splits.
#   - NEW: Optional --out-base-dir to save output into a fixed folder with filename based on input drive.
# Usage:
#   python beamng_zip_inventory.py -r "D:\BeamNG\mods" --out-base-dir "C:\_lib\_BeamNG__\output"
#   python beamng_zip_inventory.py -r "/mnt/d/BeamNG/mods" -o mods_index.csv --workers 8
# Notes:
#   * No external dependencies required (standard library only).
#   * Designed to handle ~5k+ ZIPs efficiently (uses a ThreadPool for I/O parallelism).

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile, BadZipFile

# ---------------------------
# Configuration (keys/order)
# ---------------------------

# File information columns
FILE_INFO_COLS = [
    "directory",        # parent directory on disk
    "file_name",        # zip filename
    "file_path",        # full path
    "file_size_bytes",  # size in bytes
    "date_created",     # filesystem ctime (best effort; on Linux it's change time)
    "date_modified",    # filesystem mtime
]

# Derived from ZIP content
DERIVED_COLS = [
    "map_name",         # from levels/NAME/...
    "vehicle_name",     # from vehicles/NAME/...
    "info_json_count",  # number of info.json files found
    "info_json_paths",  # semicolon-separated list of info.json internal paths
]

# Requested keys (Vehicles, Maps, Other). Keep case as requested.
VEHICLE_KEYS = [
    "Author", "Body Style", "Brand", "Country", "Derby Class", "Description",
    "Name", "Region", "Type"
]

MAP_KEYS = [
    "features", "suitablefor", "authors", "roads", "title", "description",
    "country", "biome", "size", "localUnits", "previews", "length"
]

OTHER_KEYS = [
    "version_string", "last_update", "resource_date", "tag_line", "filename",
    "user_id", "username", "tagid", "message", "category_title",
    "prefix_title", "via"
]

# Output special/normalized columns (we normalize author(s) and humanized timestamps)
NORMALIZED_COLS = [
    "authors",                  # merged from "Author" or "authors" (vehicles vs maps)
    "last_update_human",        # parsed from last_update if possible
    "resource_date_human",      # parsed from resource_date if possible
]

# Preferred column order for CSV
PREFERRED_ORDER = (
    FILE_INFO_COLS
    + DERIVED_COLS
    + NORMALIZED_COLS
    + VEHICLE_KEYS
    + MAP_KEYS
    + OTHER_KEYS
)


# ---------------------------
# Utility helpers
# ---------------------------

def to_iso(ts: float) -> str:
    """Convert a POSIX timestamp (seconds) to ISO8601 string in UTC."""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return ""

def parse_human_time(value: object) -> str:
    """
    Attempt to convert common timestamp formats to a human-readable UTC time.
    Accepts:
      - int/float seconds since epoch (1970)
      - int/float milliseconds since epoch (1970) (heuristic: > 10^12 considered ms)
      - ISO8601-ish strings (YYYY-MM-DD..., or RFC 3339 with Z)
    Returns ISO8601-like 'YYYY-MM-DD HH:MM:SS UTC' or empty string if unknown.
    """
    if value is None:
        return ""
    # numeric?
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip())):
            num = float(value)
            # Heuristic: timestamps beyond ~10^12 are likely ms
            if num > 1e12:
                num /= 1000.0
            # If it's way in the past or future, just give up
            if 0 < num < 32503680000:  # year 3000 cutoff
                return to_iso(num)
    except Exception:
        pass

    # ISO-like string?
    if isinstance(value, str):
        s = value.strip()
        # Replace 'Z' with '+00:00' for fromisoformat compatibility, and handle space ' ' T
        s2 = s.replace("Z", "+00:00").replace("z", "+00:00")
        try:
            # Try various layouts
            try:
                dt = datetime.fromisoformat(s2)
            except ValueError:
                # Fallback: try removing timezone if present and parse naive
                s3 = re.sub(r"([+-]\d{2}:\d{2})$", "", s2)
                dt = datetime.fromisoformat(s3)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            pass
    return ""

_JSON_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

def cleanup_json_blob(blob: str) -> str:
    """
    Remove common trailing commas that break strict JSON:
      - e.g. {"a":1,} or [1,2,]
    Also strip BOM and attempt to remove C++-style line comments if present.
    """
    # Strip BOM
    if blob and blob[0] == "\ufeff":
        blob = blob.lstrip("\ufeff")
    # Remove // line comments (very light; only full-line comments)
    blob = re.sub(r"^\s*//.*$", "", blob, flags=re.MULTILINE)
    # Remove /* ... */ comments (naive, but often enough)
    blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.DOTALL)
    # Fix trailing commas before } or ]
    prev = None
    while prev != blob:
        prev = blob
        blob = _JSON_TRAILING_COMMA_RE.sub(r"\1", blob)
    return blob

def safe_load_json(raw_bytes: bytes, encoding: str = "utf-8") -> Optional[Dict]:
    """Try to parse JSON, attempting cleanup if strict parse fails."""
    try:
        text = raw_bytes.decode(encoding, errors="replace")
    except Exception:
        try:
            text = raw_bytes.decode("latin-1", errors="replace")
        except Exception:
            return None
    # First try strict
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try cleanup
    try:
        cleaned = cleanup_json_blob(text)
        return json.loads(cleaned)
    except Exception:
        return None

def find_map_or_vehicle_name(zip_obj: ZipFile) -> Tuple[str, str]:
    """
    Inspect entries to find levels/NAME/... and vehicles/NAME/...
    Returns (map_name, vehicle_name) (one or both may be '').
    Picks the first NAME found in each category.
    """
    map_name = ""
    vehicle_name = ""
    try:
        for zi in zip_obj.infolist():
            p = zi.filename.replace("\\", "/")
            parts = p.split("/")
            # Look for levels/<NAME>/...
            for i in range(len(parts) - 1):
                if parts[i].lower() == "levels" and (i + 1) < len(parts):
                    if not map_name:
                        map_name = parts[i + 1]
                if parts[i].lower() == "vehicles" and (i + 1) < len(parts):
                    if not vehicle_name:
                        vehicle_name = parts[i + 1]
            if map_name and vehicle_name:
                break
    except Exception:
        pass
    return map_name, vehicle_name

def collect_info_jsons(zip_obj: ZipFile) -> List[str]:
    """Return internal paths of files named 'info.json' (case-insensitive)."""
    paths = []
    try:
        for zi in zip_obj.infolist():
            name = os.path.basename(zi.filename)
            if name.lower() == "info.json":
                paths.append(zi.filename)
    except Exception:
        pass
    return paths

def merge_dicts(left: Dict, right: Dict) -> Dict:
    """Shallow merge: right overrides left for same keys."""
    merged = dict(left)
    for k, v in right.items():
        merged[k] = v
    return merged

def read_info_json(zip_obj: ZipFile, internal_path: str) -> Optional[Dict]:
    """Read and parse a single info.json inside the zip."""
    try:
        with zip_obj.open(internal_path, "r") as f:
            data = f.read()
        return safe_load_json(data)
    except Exception:
        return None

def file_times(path: str) -> Tuple[str, str]:
    """Return (created_iso, modified_iso) from filesystem times (UTC)."""
    try:
        c = os.path.getctime(path)
    except Exception:
        c = None
    try:
        m = os.path.getmtime(path)
    except Exception:
        m = None
    created = to_iso(c) if c else ""
    modified = to_iso(m) if m else ""
    return created, modified

def get_file_info(zip_path: str) -> Dict[str, str]:
    """Gather file-level info for the zip on disk."""
    directory = os.path.dirname(zip_path)
    file_name = os.path.basename(zip_path)
    try:
        size = os.path.getsize(zip_path)
    except Exception:
        size = 0
    created, modified = file_times(zip_path)
    return {
        "directory": directory,
        "file_name": file_name,
        "file_path": zip_path,
        "file_size_bytes": str(size),
        "date_created": created,
        "date_modified": modified,
    }

def normalize_fields(merged_json: Dict) -> Dict[str, str]:
    """
    Build the output dict of requested/normalized fields from the merged JSON.
    - Merge authors from "Author" or "authors" into a single "authors" column.
    - Create human-readable time columns for last_update and resource_date.
    - Keep original keys (Vehicles, Maps, Other) as separate columns too.
    """
    out: Dict[str, str] = {}

    # Original keys
    for k in VEHICLE_KEYS + MAP_KEYS + OTHER_KEYS:
        if k in merged_json:
            v = merged_json.get(k)
            # Convert lists/objects to JSON strings for CSV readability
            if isinstance(v, (list, dict)):
                try:
                    out[k] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    out[k] = str(v)
            else:
                out[k] = str(v)

    # Normalized authors
    a = merged_json.get("authors")
    if a is None:
        a = merged_json.get("Author")
    if isinstance(a, (list, dict)):
        try:
            out["authors"] = json.dumps(a, ensure_ascii=False)
        except Exception:
            out["authors"] = str(a)
    elif a is not None:
        out["authors"] = str(a)

    # Humanized timestamps
    if "last_update" in merged_json:
        out["last_update_human"] = parse_human_time(merged_json.get("last_update"))
    if "resource_date" in merged_json:
        out["resource_date_human"] = parse_human_time(merged_json.get("resource_date"))

    return out

def process_zip(zip_path: str) -> Dict[str, str]:
    """
    Process a single zip:
      - file info
      - find map/vehicle names
      - read & merge any info.json files
    Returns a row dict for CSV.
    """
    row: Dict[str, str] = {}
    row.update(get_file_info(zip_path))

    info_paths: List[str] = []
    merged: Dict = {}

    try:
        with ZipFile(zip_path, "r") as zf:
            # Derived names
            map_name, vehicle_name = find_map_or_vehicle_name(zf)
            if map_name:
                row["map_name"] = map_name
            if vehicle_name:
                row["vehicle_name"] = vehicle_name

            # info.jsons
            info_paths = collect_info_jsons(zf)
            for p in info_paths:
                data = read_info_json(zf, p)
                if isinstance(data, dict):
                    merged = merge_dicts(merged, data)

            # Record info.json diagnostics
            row["info_json_count"] = str(len(info_paths))
            if info_paths:
                row["info_json_paths"] = ";".join(info_paths)
    except BadZipFile:
        row["info_json_count"] = "0"
        row["info_json_paths"] = ""
        row["zip_error"] = "BadZipFile"
    except Exception as e:
        row["info_json_count"] = "0"
        row["info_json_paths"] = ""
        row["zip_error"] = f"{type(e).__name__}: {e}"

    # Normalize JSON-derived fields
    if merged:
        row.update(normalize_fields(merged))

    return row

def walk_zip_paths(root: str) -> List[str]:
    """Find all .zip files under root (case-insensitive)."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".zip"):
                results.append(os.path.join(dirpath, fn))
    return results

def determine_headers(rows: List[Dict[str, str]]) -> List[str]:
    """Compute CSV headers: prefer PREFERRED_ORDER, then add any extra keys seen."""
    keys_seen = set()
    for r in rows:
        keys_seen.update(r.keys())
    # Start with preferred order if present
    headers = [k for k in PREFERRED_ORDER if k in keys_seen]
    # Append any remaining keys in sorted order for stability
    remaining = sorted(k for k in keys_seen if k not in headers)
    headers.extend(remaining)
    return headers

_ELLIPSIS = "…"

def sanitize_for_csv(val: object, max_len: int) -> str:
    """
    Ensure a safe single-line CSV cell without huge payloads:
      - Convert to str (JSON-dump lists/dicts).
      - Replace CR/LF/TAB with spaces; collapse repeated whitespace.
      - Truncate to max_len and add an ellipsis if truncated.
    """
    # Serialize
    if isinstance(val, (list, dict)):
        try:
            s = json.dumps(val, ensure_ascii=False)
        except Exception:
            s = str(val)
    else:
        s = "" if val is None else str(val)
    # Normalize whitespace
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Truncate
    if max_len is not None and max_len > 0 and len(s) > max_len:
        return s[: max_len - 1] + _ELLIPSIS
    return s

def derive_letter_from_path(path: str) -> str:
    """
    Try to derive a Windows-style drive letter from a path.
    - On Windows: uses os.path.splitdrive -> 'C:' -> 'C'
    - On WSL/Linux: detect '/mnt/<letter>/' -> '<letter>'
    - UNC paths -> 'UNC'
    - Otherwise -> 'X'
    """
    abspath = os.path.abspath(path)
    drive, _ = os.path.splitdrive(abspath)
    if drive:
        # Typical 'C:' form
        return (drive[0] if drive[0].isalpha() else "X").upper()
    # WSL/Linux '/mnt/d/...'
    parts = abspath.replace("\\", "/").split("/")
    if len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return parts[2].upper()
    # UNC like '\\server\share' on Windows may appear without drive
    if abspath.startswith("\\\\") or abspath.startswith("//"):
        return "UNC"
    return "X"

def compute_output_path(root: str, explicit_output: Optional[str], out_base_dir: Optional[str]) -> str:
    """
    Determine the CSV output path:
      - If explicit_output provided, use it.
      - Else if out_base_dir provided, save to that directory with filename 'mods_index_on_<LETTER>.csv'
        where <LETTER> is derived from the input root.
      - Else default to './mods_index.csv' in CWD.
    """
    if explicit_output:
        return explicit_output
    if out_base_dir:
        letter = derive_letter_from_path(root)
        filename = f"mods_index_on_{letter}.csv"
        return os.path.join(out_base_dir, filename)
    return os.path.join(os.getcwd(), "mods_index.csv")

def main():
    ap = argparse.ArgumentParser(description="Index BeamNG mod ZIPs to CSV.")
    ap.add_argument("-r", "--root", required=True, help="Root directory to scan for .zip files (recursively).")
    ap.add_argument("-o", "--output", help="Explicit output CSV path. If omitted, see --out-base-dir.")
    ap.add_argument("--out-base-dir", help="If provided and --output is omitted, save to this folder as mods_index_on_<DRIVE>.csv")
    ap.add_argument("--max-cell-chars", type=int, default=1000, help="Max characters per CSV cell (default: 1000).")
    ap.add_argument("--workers", type=int, default=min(8, (os.cpu_count() or 8)), help="Thread workers (I/O bound).")
    ap.add_argument("--quiet", action="store_true", help="Reduce console output.")
    args = ap.parse_args()

    start = time.time()
    zips = walk_zip_paths(args.root)
    if not args.quiet:
        print(f"Found {len(zips)} zip(s) under: {os.path.abspath(args.root)}")

    rows: List[Dict[str, str]] = []
    # Process in parallel (I/O heavy: reading many small files inside zip)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_map = {ex.submit(process_zip, zp): zp for zp in zips}
        for i, fut in enumerate(as_completed(fut_map), 1):
            try:
                row = fut.result()
                rows.append(row)
            except Exception as e:
                # Shouldn't happen since process_zip is defensive, but just in case
                rows.append({"file_path": fut_map[fut], "zip_error": f"{type(e).__name__}: {e}"})
            if not args.quiet and (i % 50 == 0 or i == len(fut_map)):
                print(f"Processed {i}/{len(fut_map)}")

    headers = determine_headers(rows)

    # Determine output destination
    out_path = compute_output_path(args.root, args.output, args.out_base_dir)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    # Write CSV with sanitization to prevent row-splitting and huge cells
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            sanitized = {k: sanitize_for_csv(v, args.max_cell_chars) for k, v in r.items()}
            w.writerow(sanitized)

    elapsed = time.time() - start
    if not args.quiet:
        print(f"Wrote {len(rows)} row(s) to {out_path}")
        print(f"Elapsed: {elapsed:.2f}s")

if __name__ == "__main__":
    main()