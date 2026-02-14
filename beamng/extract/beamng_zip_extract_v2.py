#!/usr/bin/env python3

# Write to a drive-based path like mods_index_on_C.csv
# python "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v2.py" -r "M:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output"
# python "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v2.py" -r "D:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output"
# python "C:\Users\Administrator\projects\BeamNG\beamng\extract\beamng_zip_extract_v2.py" -r "C:\__BeamNG__\___mods___" --out-base-dir "C:\__BeamNG__\____directory-extract____\_output"

# Custom exclude list (comma separated), keep sidecar behavior
# python beamng_zip_extract_v2.py -r "D:\BeamNG" --exclude-when-primary "gameplay,ui,scripts"

# See chat for full description.

from __future__ import annotations
import argparse, csv, os, json, re, hashlib, time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile, BadZipFile

EXCLUDE_DEFAULT = {
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
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
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
        # "file_path": zip_path,
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


def choose_primary_and_merge(paths: List[str]):
    primary = None
    mod_info = None
    extras = []
    for p in paths:
        top = p.split("/")[0].lower()
        if top == "vehicles" and not primary:
            primary = p
        elif top == "levels" and not primary:
            primary = p
        elif top == "mod_info" and not mod_info:
            mod_info = p
        else:
            extras.append(p)
    return primary, mod_info, extras


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r", "--root", required=True)
    ap.add_argument("-o", "--output")
    ap.add_argument("--out-base-dir")
    ap.add_argument("--max-cell-chars", type=int, default=1000)
    ap.add_argument("--exclude-when-primary", default=",".join(sorted(EXCLUDE_DEFAULT)))
    args = ap.parse_args()

    def compute_output_path(root, explicit, base_dir):
        def derive_letter(p):
            abspath = os.path.abspath(p)
            drive, _ = os.path.splitdrive(abspath)
            if drive:
                return (drive[0] if drive[0].isalpha() else "X").upper()
            parts = abspath.replace("\\", "/").split("/")
            if (
                len(parts) > 2
                and parts[1] == "mnt"
                and len(parts[2]) == 1
                and parts[2].isalpha()
            ):
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
    excl = {
        x.strip().lower()
        for x in (args.exclude_when_primary or "").split(",")
        if x.strip()
    }

    rows = []
    for dirpath, _, files in os.walk(args.root):
        for fn in files:
            if not fn.lower().endswith(".zip"):
                continue
            zp = os.path.join(dirpath, fn)
            file_info = get_file_info(zp)
            row = dict(file_info)
            try:
                with ZipFile(zp, "r") as zf:
                    row["top_level_dir"] = collect_top_level_dir(zf) or ""
                    info_paths = find_info_paths(zf)
                    primary, mod_info, extras = choose_primary_and_merge(info_paths)
                    selected = []
                    if primary:
                        selected.append(primary)
                        for p in extras:
                            top = p.split("/")[0].lower() if p else ""
                            if top == "mod_info":
                                continue
                            if top in excl:
                                continue
                            selected.append(p)
                    else:
                        selected = info_paths[:1]

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
                    if mod_info:
                        try:
                            with zf.open(mod_info, "r") as f:
                                data = f.read()
                            mj = safe_load_json(data) or {}
                            if isinstance(mj, dict):
                                merged.update(mj)
                        except Exception:
                            pass

                    row["info_json_count"] = str(len(info_paths))
                    if info_paths:
                        row["info_json_paths"] = ";".join(info_paths)
                    for zi in zf.infolist():
                        p = zi.filename.replace("\\", "/")
                        parts = p.split("/")
                        for i in range(len(parts) - 1):
                            if (
                                parts[i].lower() == "levels"
                                and i + 1 < len(parts)
                                and "map_name" not in row
                            ):
                                row["map_name"] = parts[i + 1]
                            if (
                                parts[i].lower() == "vehicles"
                                and i + 1 < len(parts)
                                and "vehicle_name" not in row
                            ):
                                row["vehicle_name"] = parts[i + 1]
                    row.update(normalize_fields(merged))
            except BadZipFile:
                row["zip_error"] = "BadZipFile"
            except Exception as e:
                row["zip_error"] = f"{type(e).__name__}: {e}"
            rows.append(row)

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
                s, t, orig, ln = sanitize_cell(
                    str(v) if v is not None else "", max_len=args.max_cell_chars
                )
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
                            # "file_path": r.get("file_path", ""),
                            "truncated_fields": tfields,
                            "full": fulls,
                            "lengths": lens,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    print(f"Wrote {len(rows)} rows to {out_path}")
    print(
        f"Sidecar: {sidecar_path} (rows with truncations: {trunc_rows}, total truncated cells: {trunc_cells})"
    )


if __name__ == "__main__":
    main()
