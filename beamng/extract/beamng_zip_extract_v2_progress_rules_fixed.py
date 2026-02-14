#!/usr/bin/env python3

# BeamNG ZIP Inventory -> CSV (+ sidecar JSONL for truncated fields)
# Updated: adds progress output similar to prior iteration.
#
# Example:
# python "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v2_progress_rules_fixed.py" -r "M:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output"
# python "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v2_progress_rules_fixed.py" -r "D:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output"
# python "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v2_progress_rules_fixed.py" -r "C:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output"

#   python beamng_zip_extract_v2_progress_rules_fixed.py -r "M:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output" --progress-every 50
#
# Notes:
# - Progress prints "Processed X/Y" every N zips (default 50), unless --quiet is set.
# - Gathers zip list first so Y is known (this can take a bit on slow disks, but avoids "looks like hanging").


from __future__ import annotations
import argparse, csv, os, json, re, hashlib, time  # noqa: E401
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zipfile import ZipFile, BadZipFile

EXCLUDE_DEFAULT = {
    "art",
    "campaigns",
    "gameplay",
    "ui",
    "lua",
    "settings",
    "scripts",
    "flowEditor",
    "timeTrials",
}

FILE_INFO_COLS = [
    "row_id",
    "directory",
    "file_name",
    # "file_path",
    "file_size_bytes",
    "date_created",
    "date_modified",
]
DERIVED_COLS = [
    "top_level_dir",
    "map_name",
    "vehicle_name",
    "info_json_count",
    "info_json_paths",
]
NORMALIZED_COLS = ["authors", "last_update_human", "resource_date_human"]

VEHICLE_KEYS = [
    "Author",
    "Body Style",
    "Brand",
    "Country",
    "Derby Class",
    "Description",
    "Name",
    "Region",
    "Type",
]
MAP_KEYS = [
    "features",
    "suitablefor",
    "authors",
    "roads",
    "title",
    "description",
    "country",
    "biome",
    "size",
    "localUnits",
    "previews",
    "length",
]
OTHER_KEYS = [
    "version_string",
    "last_update",
    "resource_date",
    "tag_line",
    "filename",
    "user_id",
    "username",
    "tagid",
    "message",
    "category_title",
    "prefix_title",
    "via",
]

PREFERRED_ORDER = (
    FILE_INFO_COLS
    + DERIVED_COLS
    + NORMALIZED_COLS
    + VEHICLE_KEYS
    + MAP_KEYS
    + OTHER_KEYS
)


def to_iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return ""


def parse_human_time(value: object) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip())
        ):
            num = float(value)
            if num > 1e12:
                num /= 1000.0
            if 0 < num < 32503680000:
                return to_iso(num)
    except Exception:
        pass
    if isinstance(value, str):
        s2 = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
        try:
            try:
                dt = datetime.fromisoformat(s2)
            except ValueError:
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


def cleanup_json_blob(blob: str) -> str:
    if blob and blob[0] == "\ufeff":
        blob = blob.lstrip("\ufeff")
    blob = re.sub(r"^\s*//.*$", "", blob, flags=re.MULTILINE)
    blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.DOTALL)
    prev = None
    while prev != blob:
        prev = blob
        blob = re.sub(r",\s*([}\]])", r"\1", blob)
    return blob


def safe_load_json(raw_bytes: bytes) -> Optional[Dict]:
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw_bytes.decode(enc, errors="replace")
        except Exception:
            continue
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            return json.loads(cleanup_json_blob(text))
        except Exception:
            pass
    return None


def make_row_id(path: str, size: int, mtime_ts: float) -> str:
    h = hashlib.sha1()
    h.update(os.path.abspath(path).encode("utf-8", "replace"))
    h.update(b"|")
    h.update(str(size).encode())
    h.update(b"|")
    h.update(str(int(mtime_ts)).encode())
    return h.hexdigest()[:12]


def file_times(path: str):
    try:
        c = os.path.getctime(path)
    except Exception:
        c = None
    try:
        m = os.path.getmtime(path)
    except Exception:
        m = None
    return to_iso(c) if c else "", to_iso(m) if m else "", (m or 0.0)


def get_file_info(zip_path: str) -> Dict[str, str]:
    directory = os.path.dirname(zip_path)
    file_name = os.path.basename(zip_path)
    try:
        size = os.path.getsize(zip_path)
    except Exception:
        size = 0
    created, modified, m_ts = file_times(zip_path)
    row_id = make_row_id(zip_path, size, m_ts)
    return {
        "row_id": row_id,
        "directory": directory,
        "file_name": file_name,
        "file_size_bytes": str(size),
        "date_created": created,
        "date_modified": modified,
    }


def collect_top_level_dir(zf: ZipFile) -> str:
    ordered = []
    seen = set()
    for zi in zf.infolist():
        p = zi.filename.replace("\\", "/").lstrip("/")
        parts = p.split("/")
        if len(parts) > 1:
            t = parts[0]
            if t.lower() not in seen:
                seen.add(t.lower())
                ordered.append(t)
    return ordered[0] if ordered else ""


def find_info_paths(zf: ZipFile) -> List[str]:
    paths = []
    for zi in zf.infolist():
        if os.path.basename(zi.filename).lower() == "info.json":
            paths.append(zi.filename.replace("\\", "/"))
    return paths


def categorize_info_paths(paths: List[str]):
    """Categorize info.json paths by their top-level directory."""
    levels = []
    vehicles = []
    mod_info = []
    other = []
    for p in paths:
        top = (p.split("/")[0] if p else "").lower()
        if top == "levels":
            levels.append(p)
        elif top == "vehicles":
            vehicles.append(p)
        elif top == "mod_info":
            mod_info.append(p)
        else:
            other.append(p)
    return levels, vehicles, mod_info, other


def select_info_jsons(paths: List[str]) -> List[str]:
    """
    Selection rules (one row per zip; we just decide which info.json files to read/merge):
      - Always include mod_info/*/info.json if present.
      - If ANY levels/ info.json exists: include ONLY all levels/*/info.json + all mod_info/*/info.json.
        Ignore vehicles/ and all other directories.
      - Else if ANY vehicles/ info.json exists: include ONLY all vehicles/*/info.json + all mod_info/*/info.json.
        Ignore other directories.
      - Else (no levels and no vehicles): include ALL info.json files found (including mod_info).
    """
    levels, vehicles, mod_info, other = categorize_info_paths(paths)
    if levels:
        return levels + mod_info
    if vehicles:
        return vehicles + mod_info
    return paths


def normalize_fields(j: Dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k in VEHICLE_KEYS + MAP_KEYS + OTHER_KEYS:
        if k in j:
            v = j.get(k)
            if isinstance(v, (list, dict)):
                try:
                    out[k] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    out[k] = str(v)
            else:
                out[k] = str(v)
    a = j.get("authors", j.get("Author", None))
    if a is not None:
        if isinstance(a, (list, dict)):
            try:
                out["authors"] = json.dumps(a, ensure_ascii=False)
            except Exception:
                out["authors"] = str(a)
        else:
            out["authors"] = str(a)
    if "last_update" in j:
        out["last_update_human"] = parse_human_time(j.get("last_update"))
    if "resource_date" in j:
        out["resource_date_human"] = parse_human_time(j.get("resource_date"))
    return out


def sanitize_cell(s: str, max_len: int):
    if s is None:
        s = ""
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    orig = s
    trunc = False
    if max_len and len(s) > max_len:
        s = s[: max_len - 1] + "…"
        trunc = True
    return s, trunc, orig, len(orig)


def determine_headers(rows: List[Dict[str, str]]) -> List[str]:
    keys = set()
    for r in rows:
        keys.update(r.keys())
    ordered = [k for k in PREFERRED_ORDER if k in keys]
    ordered += sorted(k for k in keys if k not in ordered)
    return ordered


def walk_zips(root: str) -> List[str]:
    zips = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(".zip"):
                zips.append(os.path.join(dirpath, fn))
    return zips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r", "--root", required=True)
    ap.add_argument("-o", "--output")
    ap.add_argument("--out-base-dir")
    ap.add_argument("--max-cell-chars", type=int, default=1000)
    ap.add_argument("--exclude-when-primary", default=",".join(sorted(EXCLUDE_DEFAULT)))
    ap.add_argument("--progress-every", type=int, default=50, help="Print progress every N zips (default 50). Use 0 to disable.")
    ap.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    args = ap.parse_args()

    def compute_output_path(root, explicit, base_dir):
        def derive_letter(p):
            abspath = os.path.abspath(p)
            drive, _ = os.path.splitdrive(abspath)
            if drive:
                return (drive[0] if drive[0].isalpha() else "X").upper()
            parts = abspath.replace("\\", "/").split("/")
            if len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
                return parts[2].upper()
            if abspath.startswith("\\\\") or abspath.startswith("//"):
                return "UNC"
            return "X"

        if explicit:
            return explicit
        base_dir = base_dir or os.getcwd()
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, f"mods_index_on_{derive_letter(root)}.csv")

    out_path = compute_output_path(args.root, args.output, args.out_base_dir)
    sidecar_path = os.path.splitext(out_path)[0] + ".details.jsonl"
    excl = {x.strip().lower() for x in (args.exclude_when_primary or "").split(",") if x.strip()}  # noqa: F841

    start = time.time()
    zips = walk_zips(args.root)
    total = len(zips)
    if not args.quiet:
        print(f"Found {total} zip(s) under: {os.path.abspath(args.root)}", flush=True)

    rows = []
    for i, zp in enumerate(zips, 1):
        file_info = get_file_info(zp)
        row = dict(file_info)
        try:
            with ZipFile(zp, "r") as zf:
                row["top_level_dir"] = collect_top_level_dir(zf) or ""
                info_paths = find_info_paths(zf)
                selected = select_info_jsons(info_paths)

                merged = {}
                for p in selected:
                    try:
                        with zf.open(p, "r") as f:
                            data = f.read()
                        j = safe_load_json(data) or {}
                        if isinstance(j, dict):
                            merged.update(j)
                    except Exception:
                        pass

                row["info_json_count"] = str(len(info_paths))
                if info_paths:
                    row["info_json_paths"] = ";".join(info_paths)

                for zi in zf.infolist():
                    p = zi.filename.replace("\\", "/")
                    parts = p.split("/")
                    for j in range(len(parts) - 1):
                        if parts[j].lower() == "levels" and j + 1 < len(parts) and "map_name" not in row:
                            row["map_name"] = parts[j + 1]
                        if parts[j].lower() == "vehicles" and j + 1 < len(parts) and "vehicle_name" not in row:
                            row["vehicle_name"] = parts[j + 1]

                row.update(normalize_fields(merged))
        except BadZipFile:
            row["zip_error"] = "BadZipFile"
        except Exception as e:
            row["zip_error"] = f"{type(e).__name__}: {e}"
        rows.append(row)

        if (not args.quiet) and args.progress_every and (i % args.progress_every == 0 or i == total):
            elapsed = time.time() - start
            rate = (i / elapsed) if elapsed > 0 else 0.0
            print(f"Processed {i}/{total} ({rate:.1f} zips/s)", flush=True)

    headers = determine_headers(rows)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    trunc_rows = 0
    trunc_cells = 0
    with (
        open(out_path, "w", newline="", encoding="utf-8") as fcsv,
        open(sidecar_path, "w", encoding="utf-8") as fj,
    ):
        w = csv.DictWriter(fcsv, fieldnames=headers)
        w.writeheader()
        for r in rows:
            sanitized = {}
            fulls = {}
            lens = {}
            tfields = []
            for k, v in r.items():
                s, t, orig, ln = sanitize_cell(str(v) if v is not None else "", max_len=args.max_cell_chars)
                sanitized[k] = s
                if t:
                    tfields.append(k)
                    fulls[k] = orig
                    lens[k] = ln
                    trunc_cells += 1
            w.writerow(sanitized)
            if tfields:
                trunc_rows += 1
                fj.write(
                    json.dumps(
                        {
                            "row_id": r.get("row_id", ""),
                            "truncated_fields": tfields,
                            "full": fulls,
                            "lengths": lens,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    elapsed = time.time() - start
    print(f"Wrote {len(rows)} rows to {out_path}", flush=True)
    print(f"Sidecar: {sidecar_path} (rows with truncations: {trunc_rows}, total truncated cells: {trunc_cells})", flush=True)
    print(f"Elapsed: {elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()
