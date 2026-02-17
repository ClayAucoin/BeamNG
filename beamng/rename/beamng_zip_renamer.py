#!/usr/bin/env python3
"""
beamng_zip_renamer.py
Safely rename BeamNG mod ZIPs based on info.json metadata.

Vehicles -> "[vehicle][car][{Brand}][{Body Style}] {Name}{ver}.zip"
Maps     -> "[map][{category}] {Title}{ver}.zip"

Notes
- Reads info.json files in the archive; selection rules:
  - If a "vehicles/NAME/..." exists, treat as Vehicle.
  - Else if a "levels/NAME/..." exists, treat as Map.
  - Always prefer info.json that lives under vehicles/ or levels/ for type-specific naming.
  - If none found, no rename.
- Version suffix ({ver}) is taken from the current filename (e.g., " v1.2" or " v2") if present,
  otherwise from "version_string" in info.json. If neither, omitted.
- Dry run by default; pass --apply to actually rename. Conflicts resolved by appending " (n)".
- Output a CSV log of changes if --log is given.

Map category (second bracket)
- Pulled from (in order): "category_title" -> "tag_line" -> keyword detection in
  title/description/message. Normalized to one of {"fictional","race track","offroad"} if matched,
  otherwise left as-is if any token found; fallback "fictional".

Usage
  python beamng_zip_renamer.py -r "D:\\BeamNG\\mods" --apply --log "rename_log.csv"
"""

from __future__ import annotations
import argparse, csv, os, re, json
from zipfile import ZipFile
from typing import Optional, Tuple, Dict, List

VEHICLE = "vehicle"
MAP = "map"

def cleanup_json_blob(blob: str) -> str:
    import re
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

def find_kind_and_info(zf: ZipFile) -> Tuple[Optional[str], Optional[str]]:
    vehicles_info = None
    levels_info = None
    for zi in zf.infolist():
        name = os.path.basename(zi.filename)
        if name.lower() != "info.json":
            continue
        path = zi.filename.replace("\\", "/")
        parts = path.split("/")
        if len(parts) > 1 and parts[0].lower() == "vehicles":
            vehicles_info = path
        if len(parts) > 1 and parts[0].lower() == "levels":
            levels_info = path
    if vehicles_info:
        return VEHICLE, vehicles_info
    if levels_info:
        return MAP, levels_info
    return None, None

def read_json(zf: ZipFile, path: str) -> Dict:
    try:
        with zf.open(path, "r") as f:
            data = f.read()
        j = safe_load_json(data)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def guess_version(basename: str, j: Dict) -> str:
    m = re.search(r"\s(v\d+(?:\.\d+)*)", basename, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    ver = j.get("version_string")
    if isinstance(ver, str) and ver.strip():
        if not ver.lower().startswith("v"):
            ver = "v" + ver.strip()
        return " " + ver.strip()
    return ""

def sanitize_component(s: str) -> str:
    bad = r'<>:"/\|?*'
    s2 = "".join(("_" if c in bad else c) for c in s)
    s2 = re.sub(r"\s+", " ", s2).strip()
    return s2

def norm_category(j: Dict) -> str:
    for key in ("category_title", "tag_line"):
        v = j.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    blob = " ".join(str(j.get(k, "")) for k in ("title","description","message")).lower()
    if any(k in blob for k in ("track","raceway","speedway","circuit","motorspeedway")):
        return "race track"
    if any(k in blob for k in ("trail","overland","rock","mud","offroad","off-road")):
        return "offroad"
    return "fictional"

def vehicle_name(j: Dict) -> Tuple[str,str,str]:
    brand = j.get("Brand") or j.get("brand") or ""
    body = j.get("Body Style") or j.get("body_style") or j.get("BodyStyle") or ""
    name  = j.get("Name") or j.get("title") or ""
    return str(brand).strip(), str(body).strip(), str(name).strip()

def map_title(j: Dict) -> str:
    t = j.get("title") or j.get("Name") or ""
    return str(t).strip()

def build_vehicle_filename(brand: str, body: str, name: str, ver: str) -> Optional[str]:
    if not name:
        return None
    brand = sanitize_component(brand) if brand else "Unknown"
    body  = sanitize_component(body) if body else "Unknown"
    name  = sanitize_component(name)
    return f"[vehicle][car][{brand}][{body}] {name}{ver}.zip"

def build_map_filename(category: str, title: str, ver: str) -> Optional[str]:
    title = sanitize_component(title) if title else ""
    if not title:
        return None
    cat = sanitize_component(category or "fictional")
    return f"[map][{cat}] {title}{ver}.zip"

def next_nonconflicting(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while True:
        cand = f"{base} ({n}){ext}"
        if not os.path.exists(cand):
            return cand
        n += 1

def plan_new_name(zip_path: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        with ZipFile(zip_path, "r") as zf:
            kind, info_path = find_kind_and_info(zf)
            if not kind or not info_path:
                return None, "no info.json under vehicles/ or levels/"
            j = read_json(zf, info_path)
            ver = guess_version(os.path.basename(zip_path), j)
            if kind == VEHICLE:
                brand, body, name = vehicle_name(j)
                new = build_vehicle_filename(brand, body, name, ver)
            else:
                cat = norm_category(j)
                title = map_title(j)
                new = build_map_filename(cat, title, ver)
            return new, None if new else "insufficient metadata"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r","--root", required=True, help="Folder to scan (recursively) for .zip files")
    ap.add_argument("--apply", action="store_true", help="Perform rename (otherwise dry-run)")
    ap.add_argument("--log", help="Write a CSV log of planned/applied changes")
    args = ap.parse_args()

    records = []
    for dirpath, _, files in os.walk(args.root):
        for fn in files:
            if not fn.lower().endswith(".zip"):
                continue
            full = os.path.join(dirpath, fn)
            new_name, reason = plan_new_name(full)
            if not new_name:
                records.append({"status":"skip","path":full,"reason":reason or ""})
                continue
            target = os.path.join(dirpath, new_name)
            target = next_nonconflicting(target)
            if args.apply:
                try:
                    os.rename(full, target)
                    records.append({"status":"renamed","path":full,"to":target,"reason":""})
                except Exception as e:
                    records.append({"status":"error","path":full,"to":target,"reason":f"{type(e).__name__}: {e}"})
            else:
                records.append({"status":"dry-run","path":full,"to":target,"reason":""})

    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        with open(args.log, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["status","path","to","reason"])
            w.writeheader()
            for r in records:
                w.writerow(r)

    total = len(records)
    renamed = sum(1 for r in records if r["status"]=="renamed")
    dry = sum(1 for r in records if r["status"]=="dry-run")
    skipped = sum(1 for r in records if r["status"]=="skip")
    errors = sum(1 for r in records if r["status"]=="error")
    print(f"Total: {total} | renamed: {renamed} | dry-run: {dry} | skipped: {skipped} | errors: {errors}")

if __name__ == "__main__":
    main()
