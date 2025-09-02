#!/usr/bin/env python3
# Windows (PowerShell or CMD)
# python "C:\Users\Administrator\projects\BeamNG\updatebeamng.py" -r "C:\_lib\_BeamNG__" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____\_output"
# python "C:\Users\Administrator\projects\BeamNG\updatebeamng.py" -r "M:\_lib\__BeamNG" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____\_output"
# python "C:\Users\Administrator\projects\BeamNG\updatebeamng.py" -r "D:\__BeamNG__" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____\_output"
#
#!/usr/bin/env python3

# BeamNG ZIP Inventory -> CSV (+ sidecar JSONL for truncated fields)
# Author: ChatGPT (for Clay)
#
# What it does:
#   - Recursively scan a root folder for .zip files (BeamNG mods: maps, vehicles, others).
#   - For each .zip, extract file info (path, name, size, created, modified).
#   - Search inside the .zip for any "info.json" files (case-insensitive). Some zips have 0..N.
#   - Parse JSON robustly (handles BOM, trailing commas, basic // and /* */ comments).
#   - Merge keys across multiple info.json files (shallow merge; later wins).
#   - Derive NAME from levels/NAME/... and vehicles/NAME/...
#   - Normalize author(s): "Author" and "authors" -> output "authors" column.
#   - Convert last_update/resource_date to human-readable UTC (if parseable).
#   - Writes ONE consolidated CSV (one row per zip).
#
# New in this version:
#   - Sanitizes CSV cells: strips CR/LF/TAB, collapses whitespace, and truncates to --max-cell-chars (default 1000).
#   - Adds a stable row_id for each zip (based on file_path + size + mtime).
#   - If any fields are truncated in a row, writes the full (untruncated) values to a sidecar NDJSON file
#     next to the CSV named "<csv_basename>.details.jsonl".
#   - Supports --out-base-dir to derive output filename as "mods_index_on_<DRIVE>.csv".


from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile, BadZipFile

# ---------------------------
# Configuration (keys/order)
# ---------------------------

# File information columns
FILE_INFO_COLS = [
    "row_id",           # stable id for the row
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

def file_times(path: str) -> Tuple[str, str, float, float]:
    """Return (created_iso, modified_iso, created_ts, modified_ts)."""
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
    return created, modified, (c or 0.0), (m or 0.0)

def make_row_id(path: str, size: int, mtime_ts: float) -> str:
    h = hashlib.sha1()
    h.update(path.encode('utf-8', 'replace'))
    h.update(b'|')
    h.update(str(size).encode())
    h.update(b'|')
    h.update(str(int(mtime_ts)).encode())
    return h.hexdigest()[:12]

def get_file_info(zip_path: str) -> Dict[str, str]:
    """Gather file-level info for the zip on disk + row_id."""
    directory = os.path.dirname(zip_path)
    file_name = os.path.basename(zip_path)
    try:
        size = os.path.getsize(zip_path)
    except Exception:
        size = 0
    created, modified, c_ts, m_ts = file_times(zip_path)
    row_id = make_row_id(os.path.abspath(zip_path), size, m_ts)
    return {
        "row_id": row_id,
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

_ELLIPSIS = "…"

def serialize_value(val: object) -> str:
    if isinstance(val, (list, dict)):
        try:
            return json.dumps(val, ensure_ascii=False)
        except Exception:
            return str(val)
    return "" if val is None else str(val)

def clean_single_line(s: str) -> str:
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def sanitize_with_tracking(val: object, max_len: int) -> Tuple[str, bool, str, int]:
    """
    Returns (sanitized, was_truncated, original_serialized, original_len).
    - original_serialized: before cleaning/truncation
    - truncation is based on cleaned length > max_len
    """
    original = serialize_value(val)
    cleaned = clean_single_line(original)
    was_truncated = False
    if max_len and max_len > 0 and len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + _ELLIPSIS
        was_truncated = True
    return cleaned, was_truncated, original, len(original)

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
    headers = [k for k in PREFERRED_ORDER if k in keys_seen]
    remaining = sorted(k for k in keys_seen if k not in headers)
    headers.extend(remaining)
    return headers

def derive_letter_from_path(path: str) -> str:
    abspath = os.path.abspath(path)
    drive, _ = os.path.splitdrive(abspath)
    if drive:
        return (drive[0] if drive[0].isalpha() else "X").upper()
    parts = abspath.replace("\\", "/").split("/")
    if len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return parts[2].upper()
    if abspath.startswith("\\\\") or abspath.startswith("//"):
        return "UNC"
    return "X"

def compute_output_path(root: str, explicit_output: Optional[str], out_base_dir: Optional[str]) -> str:
    if explicit_output:
        return explicit_output
    if out_base_dir:
        letter = derive_letter_from_path(root)
        filename = f"mods_index_on_{letter}.csv"
        return os.path.join(out_base_dir, filename)
    return os.path.join(os.getcwd(), "mods_index.csv")

def main():
    ap = argparse.ArgumentParser(description="Index BeamNG mod ZIPs to CSV (+ sidecar for truncated fields).")
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
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_map = {ex.submit(process_zip, zp): zp for zp in zips}
        for i, fut in enumerate(as_completed(fut_map), 1):
            try:
                row = fut.result()
                rows.append(row)
            except Exception as e:
                rows.append({"row_id": "", "file_path": fut_map[fut], "zip_error": f"{type(e).__name__}: {e}"})
            if not args.quiet and (i % 50 == 0 or i == len(fut_map)):
                print(f"Processed {i}/{len(fut_map)}")

    headers = determine_headers(rows)

    out_path = compute_output_path(args.root, args.output, args.out_base_dir)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    # Sidecar NDJSON path
    base, _ = os.path.splitext(out_path)
    sidecar_path = base + ".details.jsonl"
    truncated_count = 0
    rows_with_truncations = 0

    with open(out_path, "w", newline="", encoding="utf-8") as f_csv, \
         open(sidecar_path, "w", encoding="utf-8") as f_jsonl:
        w = csv.DictWriter(f_csv, fieldnames=headers)
        w.writeheader()
        for r in rows:
            # Build sanitized row; collect truncations
            sanitized_row = {}
            trunc_fulls = {}
            trunc_lengths = {}
            truncated_fields = []

            for k, v in r.items():
                sanitized, was_trunc, original, orig_len = sanitize_with_tracking(v, args.max_cell_chars)
                sanitized_row[k] = sanitized
                if was_trunc:
                    truncated_fields.append(k)
                    trunc_fulls[k] = original
                    trunc_lengths[k] = orig_len
                    truncated_count += 1

            w.writerow(sanitized_row)

            # Emit sidecar only if something truncated in this row
            if truncated_fields:
                rows_with_truncations += 1
                sidecar_obj = {
                    "row_id": r.get("row_id", ""),
                    "file_path": r.get("file_path", ""),
                    "truncated_fields": truncated_fields,
                    "full": trunc_fulls,
                    "lengths": trunc_lengths,
                }
                f_jsonl.write(json.dumps(sidecar_obj, ensure_ascii=False) + "\n")

    elapsed = time.time() - start
    if not args.quiet:
        print(f"Wrote {len(rows)} row(s) to {out_path}")
        if rows_with_truncations:
            print(f"Sidecar: {sidecar_path} (rows with truncations: {rows_with_truncations}, total truncated cells: {truncated_count})")
        else:
            print("No truncated cells; sidecar JSONL created but empty.")
        print(f"Elapsed: {elapsed:.2f}s")

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

if __name__ == "__main__":
    main()