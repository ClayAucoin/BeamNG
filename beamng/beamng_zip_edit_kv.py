#!/usr/bin/env python3
"""
beamng_zip_edit_kv_safe.py
Hardened version:
- Never writes to the original zip directly.
- Always writes to <name>.edited.zip, validates it, then (if --in-place) swaps with a .bak backup.
- If validation fails, the original remains untouched.

Other features match the previous tool:
- Scope selection (vehicles, levels, mod_info, all)
- prefer-primary + include-mod-info
- --set key=val, --remove key, --rename old:new (dot-path keys supported)
- Dry-run by default; use --apply
- CSV log via --log

Dry run:
python .\beamng_zip_edit_kv_safe.py `
  -r "C:\__BeamNG__\___zip edit test" `
  --scope levels,mod_info `
  --set map_category=offroad `
  --prefer-primary `
  --include-mod-info `
  --log "C:\__BeamNG__\___zip edit test\edit_log.csv"

Apply without replacing originals (creates *.edited.zip):
python .\beamng_zip_edit_kv_safe.py `
  -r "C:\__BeamNG__\___zip edit test" `
  --scope levels,mod_info `
  --set map_category=offroad `
  --prefer-primary `
  --include-mod-info `
  --apply `
  --log "C:\__BeamNG__\___zip edit test\edit_log.csv"

Swap in-place after validation (keeps .bak):
python .\beamng_zip_edit_kv_safe.py `
  -r "C:\__BeamNG__\___zip edit test" `
  --scope levels,mod_info `
  --set map_category=offroad `
  --prefer-primary `
  --include-mod-info `
  --apply --in-place `
  --log "C:\__BeamNG__\___zip edit test\edit_log.csv"

"""

from __future__ import annotations
import argparse, csv, os, json, re, shutil, tempfile, sys
from typing import Dict, Any, List, Tuple
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

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

def safe_load_json(raw: bytes) -> Dict:
    for enc in ("utf-8","latin-1"):
        try:
            t = raw.decode(enc, errors="replace")
        except Exception:
            continue
        try:
            j = json.loads(t); return j if isinstance(j, dict) else {}
        except Exception:
            pass
        try:
            j = json.loads(cleanup_json_blob(t)); return j if isinstance(j, dict) else {}
        except Exception:
            pass
    return {}

def parse_value(s: str):
    s = s.strip()
    if s and (s[0] in "{[" or s.lower() in ("true","false","null") or re.match(r"^-?\d+(\.\d+)?$", s)):
        try:
            return json.loads(s)
        except Exception:
            return s
    return s

def set_path(root: Dict, path: str, value: Any):
    parts = path.split("."); cur = root
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value

def remove_path(root: Dict, path: str):
    parts = path.split("."); cur = root
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict): return
        cur = cur[p]
    cur.pop(parts[-1], None)

def rename_key(root: Dict, old: str, new: str):
    parts = old.split("."); cur = root
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict): return
        cur = cur[p]
    if parts[-1] in cur:
        val = cur.pop(parts[-1]); set_path(root, new, val)

def list_infos(zf: ZipFile) -> List[str]:
    paths = []
    for zi in zf.infolist():
        if os.path.basename(zi.filename).lower()=="info.json":
            paths.append(zi.filename.replace("\\","/"))
    return paths

def filter_scope(paths: List[str], scope_set: set, prefer_primary: bool, include_mod_info: bool) -> List[str]:
    vehicles = [p for p in paths if p.split("/")[0].lower()=="vehicles"]
    levels   = [p for p in paths if p.split("/")[0].lower()=="levels"]
    modinfo  = [p for p in paths if p.split("/")[0].lower()=="mod_info"]
    chosen: List[str] = []
    def add_if(paths, allowed):
        for p in paths:
            top = p.split("/")[0].lower()
            if allowed and top in scope_set:
                chosen.append(p)
    if prefer_primary and (vehicles or levels):
        prim = vehicles or levels
        add_if(prim, True)
        if include_mod_info and "mod_info" in scope_set:
            chosen += modinfo
    else:
        add_if(vehicles, "vehicles" in scope_set)
        add_if(levels, "levels" in scope_set)
        if include_mod_info and "mod_info" in scope_set:
            chosen += modinfo
        elif "mod_info" in scope_set:
            chosen += modinfo
    seen=set(); out=[]
    for p in chosen:
        if p not in seen:
            seen.add(p); out.append(p)
    return out

def build_edited_zip(in_path: str, edits: Dict[str, Dict], out_path: str):
    # Read once, write once
    with ZipFile(in_path, "r") as zin, ZipFile(out_path, "w", compression=ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            ip = item.filename.replace("\\","/")
            if ip in edits:
                data = json.dumps(edits[ip], ensure_ascii=False, indent=2).encode("utf-8")
                zout.writestr(item, data)
            else:
                zout.writestr(item, zin.read(item))

def validate_zip(path: str) -> Tuple[bool, str]:
    try:
        with ZipFile(path, "r") as z:
            names = z.namelist()
            if not names:
                return False, "empty zip"
            # try reading a few small files (or up to first 5 entries)
            for i, n in enumerate(names[:5]):
                try:
                    _ = z.read(n)
                except Exception as e:
                    return False, f"failed to read entry '{n}': {e}"
        return True, ""
    except Exception as e:
        return False, f"open failed: {e}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r","--root", required=True, help="Folder to scan recursively for zips")
    ap.add_argument("--scope", default="all", help="Comma list: vehicles,levels,mod_info,all")
    ap.add_argument("--prefer-primary", action="store_true", help="If vehicles/ or levels/ exists, edit those and ignore others (except mod_info with --include-mod-info)")
    ap.add_argument("--include-mod-info", action="store_true", help="Always include mod_info/info.json when present")
    ap.add_argument("--set", dest="sets", action="append", default=[], help='key=val (repeat). val may be JSON or bare string')
    ap.add_argument("--remove", dest="removes", action="append", default=[], help="key (repeat)")
    ap.add_argument("--rename", dest="renames", action="append", default=[], help="old:new (repeat)")
    ap.add_argument("--apply", action="store_true", help="Actually write changes")
    ap.add_argument("--in-place", action="store_true", help="After building .edited.zip and validating it, replace original and keep .bak")
    ap.add_argument("--log", help="CSV log path (file or directory)")
    args = ap.parse_args()

    if args.scope.lower() == "all": scope_set = {"vehicles","levels","mod_info"}
    else: scope_set = {s.strip() for s in args.scope.lower().split(",") if s.strip() in {"vehicles","levels","mod_info"}}
    if not scope_set:
        print("Nothing to edit: empty scope.", file=sys.stderr); return

    def parse_sets(items):
        out=[]
        for s in items:
            if "=" not in s:
                print(f"--set needs key=val, got: {s}", file=sys.stderr); continue
            k,v = s.split("=",1); out.append((k.strip(), parse_value(v)))
        return out

    def parse_renames(items):
        out=[]
        for s in items:
            if ":" not in s:
                print(f"--rename needs old:new, got: {s}", file=sys.stderr); continue
            old,new = s.split(":",1); out.append((old.strip(), new.strip()))
        return out

    sets = parse_sets(args.sets); renames = parse_renames(args.renames)

    logs = []
    for dirpath,_,files in os.walk(args.root):
        for fn in files:
            if not fn.lower().endswith(".zip"): continue
            zp = os.path.join(dirpath, fn)
            try:
                with ZipFile(zp,"r") as zf:
                    infos = list_infos(zf)
                targets = filter_scope(infos, scope_set, args.prefer_primary, args.include_mod_info)
                if not targets:
                    logs.append({"path": zp, "status":"skip", "reason":"no targets in scope"}); continue

                if not args.apply:
                    logs.append({"path": zp, "status":"dry-run", "targets": ";".join(targets)})
                    continue

                # Build edited content in a sibling .edited.zip
                edited_zip = os.path.splitext(zp)[0] + ".edited.zip"
                edited = {}
                with ZipFile(zp,"r") as zf2:
                    for ip in targets:
                        with zf2.open(ip,"r") as f: data = f.read()
                        j = safe_load_json(data)
                        if not isinstance(j, dict): j = {}
                        for old,new in renames: rename_key(j, old, new)
                        for k,v in sets: set_path(j, k, v)
                        for k in args.removes: remove_path(j, k)
                        edited[ip] = j
                build_edited_zip(zp, edited, edited_zip)

                ok, why = validate_zip(edited_zip)
                if not ok:
                    logs.append({"path": zp, "status":"error", "reason": f"validation failed: {why}"})
                    continue

                if args.in_place:
                    bak = zp + ".bak"
                    if os.path.exists(bak):
                        os.remove(bak)
                    shutil.move(zp, bak)
                    shutil.move(edited_zip, zp)
                    logs.append({"path": zp, "status":"edited", "out": zp, "targets": ";".join(edited.keys())})
                else:
                    logs.append({"path": zp, "status":"edited", "out": edited_zip, "targets": ";".join(edited.keys())})
            except BadZipFile as e:
                logs.append({"path": zp, "status":"error", "reason": f"BadZipFile: {e}"})
            except Exception as e:
                logs.append({"path": zp, "status":"error", "reason": f"{type(e).__name__}: {e}"})

    if args.log:
        log_path = args.log
        if os.path.isdir(log_path):
            log_path = os.path.join(log_path, "edit_log.csv")
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path,"w",newline="",encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["path","status","out","targets","reason"])
            w.writeheader()
            for r in logs: w.writerow(r)

    total=len(logs)
    edited=sum(1 for r in logs if r.get("status")=="edited")
    dry=sum(1 for r in logs if r.get("status")=="dry-run")
    skip=sum(1 for r in logs if r.get("status")=="skip")
    err=sum(1 for r in logs if r.get("status")=="error")
    print(f"Total: {total} | edited: {edited} | dry-run: {dry} | skipped: {skip} | errors: {err}")

if __name__ == "__main__":
    main()
