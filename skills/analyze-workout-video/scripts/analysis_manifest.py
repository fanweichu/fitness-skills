#!/usr/bin/env python3
"""Register reviewed frame receipts and derive counts from structured event evidence."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from count_evidence import (digest, local, load_receipt, read, require,
                            validate_evidence, validate_manifest)


def new_manifest(max_visible=24, max_frames=3000, max_pixels=300000000):
    return {"schema_version": 4, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "candidate_sets": [], "evidence": {}, "final_counts": {}, "visible_pages": {},
            "generated_contact_sheets": [], "model_visible_contact_sheets": [],
            "dense_windows": [], "decode_attempts": [], "failed_extra_methods": [],
            "budget": {"max_frames": max_frames, "max_pixels": max_pixels},
            "max_model_visible_contact_sheets": max_visible}


def split(raw):
    require("=" in raw, "expected SET_ID=PATH")
    sid, value = raw.split("=", 1)
    require(sid and value, "empty set id/path")
    return sid, value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument("--max-visible", type=int, default=24, help="legacy/discovery sheet-name ceiling")
    p.add_argument("--max-frames", type=int, default=3000)
    p.add_argument("--max-pixels", type=int, default=300000000)
    for name in ["candidate-set", "evidence", "reviewed-pages", "discovery-page", "final-count", "generated", "visible",
                 "dense-window", "decode-attempt", "failed-method", "count-evidence", "source-verified-set", "dismiss-candidate"]:
        p.add_argument("--"+name, action="append", default=[])
    p.add_argument("--assert-final-ready", action="store_true"); p.add_argument("--summary", action="store_true")
    a = p.parse_args(); path = a.manifest.resolve()
    try:
        m = read(path) if path.exists() else new_manifest(a.max_visible,a.max_frames,a.max_pixels)
        require(m.get("schema_version") == 4, "legacy manifest is read-only history; start a new evidence run")
        require(not a.count_evidence, "structured evidence required: use --evidence SET_ID=JSON, not a type label")
        require(not a.source_verified_set, "source-local verification is derived from receipts; use --evidence")
        require(not a.dismiss_candidate, "dismissal needs structured evidence with disposition=non_set")
        for key, values in [("candidate_sets", a.candidate_set),
                            ("model_visible_contact_sheets",a.visible), ("dense_windows",a.dense_window),
                            ("failed_extra_methods",a.failed_method)]:
            m[key] = list(dict.fromkeys(m.get(key, []) + values))
        m["generated_contact_sheets"] = list(dict.fromkeys(
            [os.path.relpath(local(path.parent,x),path.parent) for x in m["generated_contact_sheets"]]
            + [os.path.relpath(Path(x).resolve(),path.parent) for x in a.generated]))
        require(len(m["model_visible_contact_sheets"]) <= m["max_model_visible_contact_sheets"], "visual budget exceeded")
        require(not a.visible, "unmeasured sheet names are not accepted; use --discovery-page PATH=FRAMES")
        for raw in a.discovery_page:
            name, n = raw.rsplit("=",1); asset = Path(name).resolve(); n = int(n)
            require(asset.is_file() and 0 < n <= 48, "discovery page needs 1-48 actual frame slots")
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json", str(asset)],capture_output=True,text=True,check=True,timeout=15)
            size = json.loads(probe.stdout)["streams"][0]; w,h = size["width"],size["height"]
            require(w <= 1920 and h <= 2000 and w*h/n >= 160*90, "unreadable discovery sheet geometry")
            m["visible_pages"][str(asset)] = {"path":str(asset),"sha256":digest(asset),"frames":n,"pixels":w*h}
        for raw in a.reviewed_pages:
            name, page_list = raw.rsplit("#",1)
            rp = Path(name).resolve(); r = load_receipt(rp)
            wanted = page_list.split(","); pages = {x["id"]: x for x in r["pages"]}
            require(set(wanted) <= set(pages), "unknown reviewed page")
            for pid in wanted:
                page = pages[pid]; asset = local(rp.parent,page["path"])
                m["visible_pages"][str(asset)] = {"path": str(asset), "sha256":page["sha256"],
                    "frames":len(page["frame_ids"]), "pixels":page["width"]*page["height"]}
        for raw in a.evidence:
            sid, name = split(raw); ep = Path(name).resolve()
            require(sid in m["candidate_sets"], "evidence references an unknown candidate")
            result = validate_evidence(ep)
            require(result["set_id"] == sid, "evidence set id mismatch")
            m["evidence"][sid] = {"path":os.path.relpath(ep,path.parent), "sha256":digest(ep)}
        derived = {}; results = {}; assets = {}
        generated = {str(local(path.parent,name)) for name in m["generated_contact_sheets"]}
        for asset in m["visible_pages"].values():
            ap = local(path.parent, asset["path"])
            require(ap.is_file() and digest(ap) == asset["sha256"], "registered page changed")
            assets[str(ap)] = asset
        for sid, entry in m["evidence"].items():
            ep = local(path.parent,entry["path"])
            require(digest(ep) == entry["sha256"], "registered evidence changed; re-register after review")
            result = validate_evidence(ep); results[sid] = result; assets.update(result["viewed_assets"])
            for rp in read(ep)["receipts"].values():
                receipt_path = local(ep.parent, rp)
                for page in read(receipt_path)["pages"]:
                    generated.add(str(local(receipt_path.parent,page["path"])))
            if result["disposition"] == "working_set": derived[sid] = result["completed"]
        for raw in a.final_count:
            sid, n = split(raw)
            require(sid in derived, "source-local structured evidence required before final count")
            require(int(n) == derived[sid], "final count differs from confirmed event records")
        m["final_counts"] = derived
        frame_count = sum(x["frames"] for x in assets.values()); pixels = sum(x["pixels"] for x in assets.values())
        require(frame_count <= m["budget"]["max_frames"] and pixels <= m["budget"]["max_pixels"], "visual frame/pixel budget exceeded")
        m["counts"] = {"generated_contact_sheets":len(generated),
                       "model_visible_contact_sheets":len(assets)+len(m["model_visible_contact_sheets"]),
                       "model_visible_frames":frame_count, "model_visible_pixels":pixels,
                       "candidate_sets":len(m["candidate_sets"]), "source_verified_sets":len(results),
                       "count_evidence":len(results), "final_counts":len(derived),
                       "dense_windows":len(m["dense_windows"]), "failed_extra_methods":len(m["failed_extra_methods"])}
        m["decode_attempts"].extend(a.decode_attempt)
        m["counts"]["decode_attempts"] = len(m["decode_attempts"])
        m["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        m["visible_pages"] = {os.path.relpath(local(path.parent,x["path"]), path.parent):
                              {**x, "path":os.path.relpath(local(path.parent,x["path"]), path.parent)}
                              for x in m["visible_pages"].values()}
        if a.assert_final_ready:
            require(set(m["candidate_sets"]) == set(results), "candidate sets missing structured evidence")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".pending")
        try:
            temp.write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            if a.assert_final_ready: validate_manifest(temp)
            os.replace(temp,path)
        finally:
            temp.unlink(missing_ok=True)
        print(json.dumps(m["counts"],ensure_ascii=False) if a.summary else path)
    except (ValueError, KeyError, OSError, TypeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"analysis gate failed: {exc}")


if __name__ == "__main__":
    main()
