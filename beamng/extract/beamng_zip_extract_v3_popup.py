#!/usr/bin/env python3
# python 'C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v3_popup.py' -r 'D:\__BeamNG__\___mods___' --out-base-dir 'C:\__BeamNG__\____directory-extract____\_output'
# python 'C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v3_popup.py' -r 'M:\__BeamNG__\___mods___' --out-base-dir 'C:\__BeamNG__\____directory-extract____\_output'
# python 'C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v3_popup.py' -r 'C:\__BeamNG__\___mods___' --out-base-dir 'C:\__BeamNG__\____directory-extract____\_output'

"""
BeamNG ZIP Inventory -> CSV (+ sidecar JSONL for truncated fields)

v3 (Clay rules update):
- Progress output: "Processed X/Y" every N zips.
- One row per zip.
- Directory exclusion "ground zero": excluded top-level dirs are not searched for JSON (info.json/app.json).
- JSON selection logic (priority):
  1) If levels/**/info.json exists (and not excluded): use ALL levels/**/info.json + ALL mod_info/**/info.json. Nothing else.
     If "levels" dir exists but has no levels info.json => fall through.
  2) Else if vehicles/**/info.json exists (and not excluded): use ALL vehicles/**/info.json + ALL mod_info/**/info.json. Nothing else.
     If vehicles dir exists but has no vehicles info.json => fall through.
  3) Else if ui/modules/apps/**/app.json exists (and not excluded): use ALL those app.json + ALL mod_info/**/info.json. Nothing else.
     - Adds ui_name column: collects the app directory name(s) under ui/modules/apps/<ui_name>/app.json
  4) Else: if ANY mod_info/**/info.json exists: use ALL mod_info/**/info.json
  5) Else: no json files => set top_level_dir to "no-json-file" and output file info only.
- Multiple JSON files in the chosen rule:
  - We DO NOT create extra rows. We aggregate per field.
  - If a field has multiple distinct values across files, we join them with " | " (order-preserving).
- Adds diagnostics columns:
  - json_rule_used, json_selected_count, json_selected_paths
"""

from __future__ import annotations
import argparse, csv, os, json, re, hashlib, time, sys
try:
    import tkinter as _tk
    from tkinter import messagebox as _messagebox
except Exception:
    _tk = None
    _messagebox = None
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile, BadZipFile

# ---------------------------
# Ground-zero exclusions
# ---------------------------
# Top-level directories here are NOT searched for info.json/app.json.
EXCLUDE_DEFAULT = [
    ".git", "art", "gameplay", "lua", "music", "resources", "scripts", "settings", "shaders"
]

# This is just a convenience list you can extend; it's not used for selection,
# but it documents common "support" dirs you mentioned.
COMMON_SUPPORT_DIRS = ["ui", "scripts", "settings", "lua"]

# ---------------------------
# Columns
# ---------------------------
FILE_INFO_COLS = [
    "row_id",
    "directory",
    "file_name",
    "file_size_bytes",
    "date_created",
    "date_modified",
]

DERIVED_COLS = [
    "top_level_dir",          # source category used: levels/vehicles/ui/mod_info/no-json-file
    "map_name",               # from levels/NAME/...
    "vehicle_name",           # from vehicles/NAME/...
    "ui_name",                # from ui/modules/apps/<ui_name>/app.json (can be multi)
    "info_json_count",        # count of info.json found (after exclusion)
    "info_json_paths",        # all info.json paths found (after exclusion)
    "app_json_count",         # count of app.json found (after exclusion)
    "app_json_paths",         # all app.json paths found (after exclusion)
    "json_rule_used",         # levels|vehicles|ui|mod_info|no-json-file
    "json_selected_count",    # how many json files were used for extraction
    "json_selected_paths",    # semicolon separated paths actually used
]

NORMALIZED_COLS = ["authors", "last_update_human", "resource_date_human"]

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

# Some app.json files use slightly different keys; map the common ones into our output keys when possible.
APP_KEY_ALIASES = {
    "name": ["Name", "title"],
    "author": ["Author", "authors"],
    "authors": ["authors", "Author"],
    "description": ["Description", "description"],
    "brand": ["Brand"],
    "type": ["Type"],
}

PREFERRED_ORDER = (
    FILE_INFO_COLS
    + DERIVED_COLS
    + NORMALIZED_COLS
    + VEHICLE_KEYS
    + MAP_KEYS
    + OTHER_KEYS
)

# ---------------------------
# Helpers
# ---------------------------

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
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        try:
            obj = json.loads(cleanup_json_blob(text))
            return obj if isinstance(obj, dict) else None
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
    base = out_base_dir or os.getcwd()
    os.makedirs(base, exist_ok=True)
    letter = derive_letter_from_path(root)
    return os.path.join(base, f"mods_index_on_{letter}.csv")

def _should_show_popup(auto: bool, popup_flag: Optional[bool]) -> bool:
    """Decide whether to show a completion popup.

    - If user explicitly sets --popup/--no-popup, respect it.
    - Otherwise (auto), only show when running in an interactive user session.
    """
    if popup_flag is not None:
        return bool(popup_flag)
    if not auto:
        return False

    # Heuristics: Task Scheduler commonly runs without a TTY and/or under Services session.
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False

    session = (os.environ.get("SESSIONNAME") or "").lower()
    if session in {"services", "service"}:
        return False

    # If tkinter isn't available, don't attempt.
    if _tk is None or _messagebox is None:
        return False

    return True


def _show_popup(title: str, message: str) -> None:
    """Show a simple Windows popup (best effort)."""
    if _tk is None or _messagebox is None:
        return
    try:
        root = _tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        _messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        return


def top_level_from_internal(path: str) -> str:
    p = path.replace("\\", "/").lstrip("/")
    return (p.split("/")[0] if p else "").lower()

def matches_ui_app_json(path: str) -> bool:
    p = path.replace("\\", "/").lstrip("/")
    parts = p.split("/")
    if len(parts) >= 5 and parts[0].lower() == "ui" and parts[1].lower() == "modules" and parts[2].lower() == "apps":
        return parts[-1].lower() == "app.json"
    return False

def ui_name_from_app_json(path: str) -> str:
    p = path.replace("\\", "/").lstrip("/")
    parts = p.split("/")
    if len(parts) >= 5:
        return parts[3]
    return ""

def collect_level_vehicle_names(zf: ZipFile) -> Tuple[str, str]:
    map_name = ""
    vehicle_name = ""
    for zi in zf.infolist():
        p = zi.filename.replace("\\", "/")
        parts = p.split("/")
        for i in range(len(parts) - 1):
            if parts[i].lower() == "levels" and not map_name and (i + 1) < len(parts):
                map_name = parts[i + 1]
            if parts[i].lower() == "vehicles" and not vehicle_name and (i + 1) < len(parts):
                vehicle_name = parts[i + 1]
        if map_name and vehicle_name:
            break
    return map_name, vehicle_name

def normalize_fields_from_obj(obj: Dict) -> Dict[str, str]:
    out: Dict[str, str] = {}

    for k in VEHICLE_KEYS + MAP_KEYS + OTHER_KEYS:
        if k in obj:
            v = obj.get(k)
            if isinstance(v, (list, dict)):
                try:
                    out[k] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    out[k] = str(v)
            else:
                out[k] = "" if v is None else str(v)

    a = obj.get("authors")
    if a is None:
        a = obj.get("Author")
    if a is not None:
        if isinstance(a, (list, dict)):
            try:
                out["authors"] = json.dumps(a, ensure_ascii=False)
            except Exception:
                out["authors"] = str(a)
        else:
            out["authors"] = str(a)

    if "last_update" in obj:
        out["last_update_human"] = parse_human_time(obj.get("last_update"))
    if "resource_date" in obj:
        out["resource_date_human"] = parse_human_time(obj.get("resource_date"))

    return out

def apply_app_aliases(app: Dict) -> Dict:
    if not isinstance(app, dict):
        return {}
    out = dict(app)
    for src_key, targets in APP_KEY_ALIASES.items():
        if src_key in app and app.get(src_key) is not None:
            for t in targets:
                if t not in out:
                    out[t] = app.get(src_key)
    return out

def aggregate_field_values(per_file_fields: List[Dict[str, str]]) -> Dict[str, str]:
    agg: Dict[str, List[str]] = {}
    for d in per_file_fields:
        for k, v in d.items():
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            if k not in agg:
                agg[k] = []
            if s not in agg[k]:
                agg[k].append(s)
    out: Dict[str, str] = {}
    for k, vals in agg.items():
        out[k] = " | ".join(vals) if vals else ""
    return out

def collect_json_candidates(zf: ZipFile, exclude: set) -> Tuple[List[str], List[str]]:
    info_paths: List[str] = []
    app_paths: List[str] = []
    for zi in zf.infolist():
        ip = zi.filename.replace("\\", "/").lstrip("/")
        if not ip:
            continue
        top = top_level_from_internal(ip)
        if top in exclude:
            continue
        base = os.path.basename(ip).lower()
        if base == "info.json":
            info_paths.append(ip)
        elif matches_ui_app_json(ip):
            app_paths.append(ip)
    return info_paths, app_paths

def categorize_info_paths(paths: List[str]) -> Tuple[List[str], List[str], List[str], List[str]]:
    levels, vehicles, mod_info, other = [], [], [], []
    for p in paths:
        top = top_level_from_internal(p)
        if top == "levels":
            levels.append(p)
        elif top == "vehicles":
            vehicles.append(p)
        elif top == "mod_info":
            mod_info.append(p)
        else:
            other.append(p)
    return levels, vehicles, mod_info, other

def select_jsons(info_paths: List[str], app_paths: List[str]) -> Tuple[str, List[str], List[str]]:
    levels, vehicles, mod_info, other = categorize_info_paths(info_paths)

    if levels:
        return "levels", levels + mod_info, []
    if vehicles:
        return "vehicles", vehicles + mod_info, []
    if app_paths:
        return "ui", mod_info, app_paths
    if mod_info:
        return "mod_info", mod_info, []
    return "no-json-file", [], []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r", "--root", required=True, help="Folder to scan recursively for zips")
    ap.add_argument("-o", "--output", help="Explicit output CSV path")
    ap.add_argument("--out-base-dir", help="If provided and --output omitted, save to this folder as mods_index_on_<DRIVE>.csv")
    ap.add_argument("--max-cell-chars", type=int, default=1000, help="Max characters per CSV cell (default 1000)")
    ap.add_argument("--exclude-dirs", default=",".join(EXCLUDE_DEFAULT),
                    help="Comma-separated top-level dirs to exclude from JSON search (ground zero).")
    ap.add_argument("--progress-every", type=int, default=50, help="Print progress every N zips (default 50). Use 0 to disable.")
    ap.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    ap.add_argument("--popup", dest="popup", action="store_true", help="Force a completion popup (manual runs).")
    ap.add_argument("--no-popup", dest="popup", action="store_false", help="Disable completion popup (scheduled runs).")
    ap.set_defaults(popup=None)
    args = ap.parse_args()

    exclude = {x.strip().lower() for x in (args.exclude_dirs or "").split(",") if x.strip()}

    out_path = compute_output_path(args.root, args.output, args.out_base_dir)
    sidecar_path = os.path.splitext(out_path)[0] + ".details.jsonl"

    start = time.time()
    zips = walk_zips(args.root)
    total = len(zips)
    if not args.quiet:
        print(f"Found {total} zip(s) under: {os.path.abspath(args.root)}", flush=True)

    rows: List[Dict[str, str]] = []

    for i, zp in enumerate(zips, 1):
        row = dict(get_file_info(zp))
        try:
            with ZipFile(zp, "r") as zf:
                map_name, vehicle_name = collect_level_vehicle_names(zf)
                if map_name:
                    row["map_name"] = map_name
                if vehicle_name:
                    row["vehicle_name"] = vehicle_name

                info_paths, app_paths = collect_json_candidates(zf, exclude)
                row["info_json_count"] = str(len(info_paths))
                row["info_json_paths"] = ";".join(info_paths) if info_paths else ""
                row["app_json_count"] = str(len(app_paths))
                row["app_json_paths"] = ";".join(app_paths) if app_paths else ""

                rule_used, selected_info, selected_apps = select_jsons(info_paths, app_paths)
                row["json_rule_used"] = rule_used
                row["json_selected_count"] = str(len(selected_info) + len(selected_apps))
                row["json_selected_paths"] = ";".join(selected_info + selected_apps) if (selected_info or selected_apps) else ""
                row["top_level_dir"] = rule_used

                if rule_used == "ui" and selected_apps:
                    ui_names = []
                    for p in selected_apps:
                        nm = ui_name_from_app_json(p)
                        if nm and nm not in ui_names:
                            ui_names.append(nm)
                    row["ui_name"] = " | ".join(ui_names) if ui_names else ""

                per_file_fields: List[Dict[str, str]] = []

                for p in selected_info:
                    try:
                        with zf.open(p, "r") as f:
                            data = f.read()
                        obj = safe_load_json(data) or {}
                        if isinstance(obj, dict):
                            per_file_fields.append(normalize_fields_from_obj(obj))
                    except Exception:
                        continue

                for p in selected_apps:
                    try:
                        with zf.open(p, "r") as f:
                            data = f.read()
                        app = safe_load_json(data) or {}
                        if isinstance(app, dict):
                            per_file_fields.append(normalize_fields_from_obj(apply_app_aliases(app)))
                    except Exception:
                        continue

                row.update(aggregate_field_values(per_file_fields))

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
    with open(out_path, "w", newline="", encoding="utf-8") as fcsv, open(sidecar_path, "w", encoding="utf-8") as fj:
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
                fj.write(json.dumps({
                    "row_id": r.get("row_id", ""),
                    "file_name": r.get("file_name", ""),
                    "truncated_fields": tfields,
                    "full": fulls,
                    "lengths": lens,
                }, ensure_ascii=False) + "\n")

    elapsed = time.time() - start
    if not args.quiet:
        print(f"Wrote {len(rows)} rows to {out_path}", flush=True)
        print(f"Sidecar: {sidecar_path} (rows with truncations: {trunc_rows}, total truncated cells: {trunc_cells})", flush=True)
        print(f"Elapsed: {elapsed:.2f}s", flush=True)


    # Completion popup: show when run manually; suppress in scheduler (auto-detected) or via --no-popup
    if _should_show_popup(auto=True, popup_flag=args.popup):
        drive = derive_letter_from_path(args.root)
        msg = (
            f"BeamNG ZIP extract finished.\n\n"
            f"Drive: {drive}\n"
            f"Root: {os.path.abspath(args.root)}\n"
            f"Rows: {len(rows)}\n"
            f"Output: {out_path}"
        )
        _show_popup("BeamNG ZIP Extract", msg)

if __name__ == "__main__":
    main()
