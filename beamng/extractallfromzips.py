#!/usr/bin/env python3
# BeamNG ZIP -> All info.json key/value pairs (long-form CSV + sidecar JSONL)
# Author: ChatGPT (for Clay)
#
# What it does:
#   - Recursively scans a root folder for .zip files.
#   - For each zip, finds every "info.json" (case-insensitive), parses robustly.
#   - Flattens ALL keys (including nested) into dot-paths (arrays indexed with [i]).
#   - Emits a long-form CSV with one row per (zip, info.json, key_path) pair:
#         row_id, file_path, info_json_path, key_path, value, value_type
#   - Sanitizes CSV cells and truncates to --max-cell-chars (default 1000).
#   - If a value is truncated, writes its full content to a sidecar NDJSON file
#     named "<csv_basename>.details.jsonl".
#   - Also writes a key frequency summary CSV listing how often each key_path appears.
#
# Example:
#   python "C:\Users\Administrator\projects\BeamNG\extractallfromzips.py" -r "C:\_lib\_BeamNG__" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____"
#   python "C:\Users\Administrator\projects\BeamNG\extractallfromzips.py" -r "M:\_lib\__BeamNG" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____"
#   python "C:\Users\Administrator\projects\BeamNG\extractallfromzips.py" -r "D:\__BeamNG__" --out-base-dir "C:\_lib\_BeamNG__\____test-extract____"
#
# -> allpairs_on_D.csv  (+ .details.jsonl) and keys_summary_on_D.csv

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
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Iterable
from zipfile import ZipFile, BadZipFile

_ELLIPSIS = "…"

def to_iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return ""

_JSON_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

def cleanup_json_blob(blob: str) -> str:
    if blob and blob[0] == "\ufeff":
        blob = blob.lstrip("\ufeff")
    blob = re.sub(r"^\s*//.*$", "", blob, flags=re.MULTILINE)
    blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.DOTALL)
    prev = None
    while prev != blob:
        prev = blob
        blob = _JSON_TRAILING_COMMA_RE.sub(r"\1", blob)
    return blob

def safe_load_json(raw_bytes: bytes, encoding: str = "utf-8") -> Optional[Dict]:
    try:
        text = raw_bytes.decode(encoding, errors="replace")
    except Exception:
        try:
            text = raw_bytes.decode("latin-1", errors="replace")
        except Exception:
            return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        cleaned = cleanup_json_blob(text)
        return json.loads(cleaned)
    except Exception:
        return None

def walk_zip_paths(root: str) -> List[str]:
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".zip"):
                results.append(os.path.join(dirpath, fn))
    return results

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

def compute_output_paths(root: str, explicit_output: Optional[str], out_base_dir: Optional[str]) -> Tuple[str, str]:
    """
    Returns (long_csv_path, summary_csv_path). sidecar JSONL path is derived from long_csv_path.
    """
    if explicit_output:
        base, _ = os.path.splitext(explicit_output)
        long_csv = explicit_output
        summary_csv = base + ".keys_summary.csv"
        return long_csv, summary_csv
    letter = derive_letter_from_path(root)
    base_dir = out_base_dir or os.getcwd()
    os.makedirs(base_dir, exist_ok=True)
    long_csv = os.path.join(base_dir, f"allpairs_on_{letter}.csv")
    summary_csv = os.path.join(base_dir, f"keys_summary_on_{letter}.csv")
    return long_csv, summary_csv

def make_row_id(path: str, size: int, mtime_ts: float) -> str:
    h = hashlib.sha1()
    h.update(path.encode('utf-8', 'replace'))
    h.update(b'|')
    h.update(str(size).encode())
    h.update(b'|')
    h.update(str(int(mtime_ts)).encode())
    return h.hexdigest()[:12]

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
    original = serialize_value(val)
    cleaned = clean_single_line(original)
    was_truncated = False
    if max_len and max_len > 0 and len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + _ELLIPSIS
        was_truncated = True
    return cleaned, was_truncated, original, len(original)

def flatten(prefix: str, obj) -> Iterable[Tuple[str, object]]:
    """
    Flatten JSON object to (key_path, scalar_value) pairs.
    - Dicts join with '.'
    - Lists use '[i]' for indices.
    - Only emit scalars (str, int, float, bool, None). Complex values are JSON-dumped as scalars as well.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield from flatten(p, v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            yield from flatten(p, v)
    else:
        # scalar
        yield prefix, obj

def main():
    ap = argparse.ArgumentParser(description="Extract ALL key/value pairs from BeamNG info.json files into a long-form CSV (+ sidecar JSONL).")
    ap.add_argument("-r", "--root", required=True, help="Root directory to scan for .zip files (recursively).")
    ap.add_argument("-o", "--output", help="Explicit output CSV path for long-form data. Summary path will be derived.")
    ap.add_argument("--out-base-dir", help="If provided and --output is omitted, save to this folder as allpairs_on_<DRIVE>.csv and keys_summary_on_<DRIVE>.csv")
    ap.add_argument("--max-cell-chars", type=int, default=1000, help="Max characters per CSV cell (default: 1000).")
    args = ap.parse_args()

    long_csv, summary_csv = compute_output_paths(args.root, args.output, args.out_base_dir)
    base, _ = os.path.splitext(long_csv)
    sidecar_path = base + ".details.jsonl"

    os.makedirs(os.path.dirname(os.path.abspath(long_csv)), exist_ok=True)

    # Prepare writers
    long_headers = ["row_id", "file_path", "info_json_path", "key_path", "value", "value_type"]
    key_counts = defaultdict(int)
    key_zip_counts = defaultdict(set)

    total_rows = 0
    truncated_cells = 0
    rows_with_truncations = 0

    with open(long_csv, "w", newline="", encoding="utf-8") as f_long, \
         open(sidecar_path, "w", encoding="utf-8") as f_jsonl:

        w = csv.DictWriter(f_long, fieldnames=long_headers)
        w.writeheader()

        for dirpath, dirnames, filenames in os.walk(args.root):
            for fn in filenames:
                if not fn.lower().endswith(".zip"):
                    continue
                zip_path = os.path.join(dirpath, fn)
                # Build row_id from path + size + mtime
                try:
                    size = os.path.getsize(zip_path)
                except Exception:
                    size = 0
                try:
                    m_ts = os.path.getmtime(zip_path)
                except Exception:
                    m_ts = 0.0
                row_id = make_row_id(os.path.abspath(zip_path), size, m_ts)

                try:
                    with ZipFile(zip_path, "r") as zf:
                        # Find info.json files
                        info_paths = []
                        for zi in zf.infolist():
                            if os.path.basename(zi.filename).lower() == "info.json":
                                info_paths.append(zi.filename)

                        for ip in info_paths:
                            try:
                                with zf.open(ip, "r") as f:
                                    data = f.read()
                                j = safe_load_json(data)
                                if not isinstance(j, dict):
                                    continue
                            except Exception:
                                continue

                            for key_path, value in flatten("", j):
                                total_rows += 1
                                val_sanitized, was_trunc, original, orig_len = sanitize_with_tracking(value, args.max_cell_chars)
                                value_type = type(value).__name__

                                w.writerow({
                                    "row_id": row_id,
                                    "file_path": zip_path,
                                    "info_json_path": ip,
                                    "key_path": key_path,
                                    "value": val_sanitized,
                                    "value_type": value_type,
                                })

                                # summaries
                                key_counts[key_path] += 1
                                key_zip_counts[key_path].add(row_id)

                                if was_trunc:
                                    truncated_cells += 1
                                    rows_with_truncations += 1
                                    f_jsonl.write(json.dumps({
                                        "row_id": row_id,
                                        "file_path": zip_path,
                                        "info_json_path": ip,
                                        "key_path": key_path,
                                        "full": original,
                                        "length": orig_len,
                                    }, ensure_ascii=False) + "\n")

                except BadZipFile:
                    # skip bad zips
                    continue
                except Exception:
                    # general skip
                    continue

    # Write summary CSV
    with open(summary_csv, "w", newline="", encoding="utf-8") as f_sum:
        wsum = csv.DictWriter(f_sum, fieldnames=["key_path", "count_pairs", "unique_zips"])
        wsum.writeheader()
        for k in sorted(key_counts.keys()):
            wsum.writerow({
                "key_path": k,
                "count_pairs": key_counts[k],
                "unique_zips": len(key_zip_counts[k]),
            })

    print(f"Wrote long-form key/value rows: {total_rows} -> {long_csv}")
    print(f"Sidecar JSONL (truncated values): {sidecar_path} (records: {truncated_cells})")
    print(f"Keys summary: {summary_csv} (unique keys: {len(key_counts)})")

if __name__ == "__main__":
    main()