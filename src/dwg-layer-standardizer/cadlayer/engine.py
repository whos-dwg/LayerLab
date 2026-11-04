"""Local DXF inspection, evidence-based mapping and non-destructive translation.

DWG must be handled by a separately tested Autodesk adapter. This module NEVER
claims it can edit native DWG files.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re
import uuid

import ezdxf

PROTECTED = {"0", "DEFPOINTS"}
SCAN_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "INSERT", "TEXT", "MTEXT", "DIMENSION", "HATCH", "LEADER", "MLEADER", "ELLIPSE", "SPLINE")
GEOM_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
CATEGORY_WORDS = {
    "wall": ("wall", "partition", "牆", "隔間"),
    "door": ("door", "門"),
    "window": ("window", "窗"),
    "balcony": ("balcony", "balcon", "陽台", "阳台"),
    "beam": ("beam", "梁"),
    "parking": ("parking", "park", "車位", "车位", "車道", "车道", "停車", "停车"),
    "stair": ("stair", "strs", "樓梯", "楼梯"),
    "elevator": ("elevator", "lift", "電梯", "电梯"),
    "mep": ("mep", "e&m", "機電", "机电", "機電設備", "机电设备"),
    "site": ("site", "地籍", "基地", "街廓", "街廓"),
    "furniture": ("furn", "furniture", "sofa", "table", "chair", "家具"),
    "annotation": ("annotation", "annot", "text", "dim", "標註", "文字"),
    "lighting": ("light", "lamp", "燈"),
    "column": ("column", "柱"),
    "hatch": ("hatch", "填充", "剖線"),
}


def digest_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def protected_reason(name: str) -> str | None:
    if name.upper() in PROTECTED:
        return "AutoCAD 系統圖層"
    if "|" in name:
        return "XREF 依附圖層"
    return None


def _layer_color(layer: Any) -> dict:
    return {"aci": int(abs(layer.dxf.get("color", 7))), "true_color": layer.dxf.get("true_color")}


def _entity_override(e: Any) -> bool:
    # ACI=256 means ByLayer, lineweight=-1 and linetype=BYLAYER.
    return (
        (e.dxf.hasattr("color") and e.dxf.color != 256)
        or e.dxf.hasattr("true_color")
        or (e.dxf.hasattr("linetype") and str(e.dxf.linetype).upper() != "BYLAYER")
        or (e.dxf.hasattr("lineweight") and e.dxf.lineweight != -1)
    )


def _sample_entity(e: Any) -> dict | None:
    """Preview only. Does not assert semantic meaning from geometric outlines."""
    try:
        kind = e.dxftype()
        if kind == "LINE":
            return {"kind": "line", "points": [[float(e.dxf.start.x), float(e.dxf.start.y)], [float(e.dxf.end.x), float(e.dxf.end.y)]]}
        if kind == "LWPOLYLINE":
            coords = [[float(p[0]), float(p[1])] for p in e.get_points("xy")]
            return {"kind": "polyline", "points": coords[:500], "closed": bool(e.closed), "truncated": len(coords) > 500}
        if kind == "CIRCLE":
            c = e.dxf.center
            return {"kind": "circle", "center": [float(c.x), float(c.y)], "radius": float(e.dxf.radius)}
        if kind == "ARC":
            c = e.dxf.center
            return {"kind": "arc", "center": [float(c.x), float(c.y)], "radius": float(e.dxf.radius), "start": float(e.dxf.start_angle), "end": float(e.dxf.end_angle)}
    except (AttributeError, ValueError, TypeError):
        pass
    return None


def scan_dxf(path: Path, job_id: str | None = None) -> dict:
    if path.suffix.lower() != ".dxf":
        raise ValueError("此讀取引擎僅支援 DXF。DWG 必須透過 AutoCAD 2026 接入層讀取。")
    doc = ezdxf.readfile(path)
    stats = defaultdict(lambda: {"model": 0, "paper": 0, "block": 0, "types": Counter(), "override": 0, "samples": [], "blocks": Counter()})
    total = 0

    def add(e: Any, context: str):
        nonlocal total
        name = str(e.dxf.get("layer", "0"))
        row = stats[name]
        row[context] += 1
        row["types"][e.dxftype()] += 1
        row["override"] += int(_entity_override(e))
        if e.dxftype() == "INSERT":
            row["blocks"][str(e.dxf.get("name", ""))] += 1
        if context == "model" and len(row["samples"]) < 250:
            sample = _sample_entity(e)
            if sample:
                row["samples"].append(sample)
        total += 1

    for layout in doc.layouts:
        context = "model" if layout.name.lower() == "model" else "paper"
        for ent in layout:
            add(ent, context)

    # Block definitions are not automatically edited by the translator.
    # This inventory is informational so mixed layer hazards are visible.
    for block in doc.blocks:
        if block.name.startswith("*") and not block.name.upper().startswith("*U"):
            continue
        for ent in block:
            add(ent, "block")

    layers = []
    for layer in doc.layers:
        name = str(layer.dxf.name)
        st = stats[name]
        flags = int(layer.dxf.get("flags", 0))
        reason = protected_reason(name)
        layers.append({
            "name": name, "color": _layer_color(layer), "linetype": layer.dxf.get("linetype", "CONTINUOUS"),
            "lineweight": int(layer.dxf.get("lineweight", -3)), "plot": bool(layer.dxf.get("plot", 1)),
            "locked": bool(flags & 4), "frozen": bool(flags & 1), "off": layer.dxf.get("color", 7) < 0,
            "counts": {k: st[k] for k in ("model", "paper", "block")},
            "total": st["model"] + st["paper"] + st["block"],
            "types": dict(st["types"]), "override_count": st["override"],
            "block_names": dict(st["blocks"]), "preview": st["samples"],
            "protected": bool(reason), "protected_reason": reason,
        })
    listed = {r["name"] for r in layers}
    for name in stats.keys() - listed:
        st = stats[name]
        reason = protected_reason(name)
        layers.append({"name": name, "color": {"aci": 7, "true_color": None},
                       "linetype": "UNKNOWN", "lineweight": -3, "plot": True, "locked": False, "frozen": False, "off": False,
                       "counts": {k: st[k] for k in ("model", "paper", "block")},
                       "total": st["model"] + st["paper"] + st["block"],
                       "types": dict(st["types"]), "override_count": st["override"],
                       "block_names": dict(st["blocks"]), "preview": st["samples"], "protected": bool(reason), "protected_reason": reason})
    layers.sort(key=lambda x: (not x["total"], x["name"].upper()))
    warnings = []
    if any(x["counts"]["block"] for x in layers):
        warnings.append("圖塊定義內物件已盤點，但 MVP 不會修改圖塊定義內的圖層；仍需獨立審核。")
    if any(x["override_count"] for x in layers):
        warnings.append("存在物件顏色／線型／線寬覆寫。轉換不會強制改成 ByLayer。")
    if any(x["name"] not in doc.layers for x in layers):
        warnings.append("部分物件引用未定義的圖層。")
    return {"schema": "cad-layer-inventory/v1", "job_id": job_id or str(uuid.uuid4()), "filename": path.name,
            "filetype": "DXF", "sha256": digest_file(path), "can_convert": True,
            "layers": layers, "total_entities": total, "warnings": warnings}


def validate_standard(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list) or not data["layers"]:
        raise ValueError("標準檔須為 JSON，且包含非空的 layers 清單。")
    seen = set()
    clean = []
    for raw in data["layers"]:
        if not isinstance(raw, dict):
            raise ValueError("layers 的每一列必須是物件。")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 255 or re.search(r'[<>/\\":;?*|=,]', name):
            raise ValueError(f"不合法標準圖層名稱：{name!r}")
        if name.upper() in PROTECTED or name.upper() in seen:
            raise ValueError(f"重複或保護的目標圖層：{name}")
        seen.add(name.upper())
        aci = raw.get("aci", 7)
        lineweight = raw.get("lineweight", -3)
        if not isinstance(aci, int) or not 1 <= aci <= 255:
            raise ValueError(f"{name}: ACI 必須是 1–255")
        if not isinstance(lineweight, int) or lineweight < -3 or lineweight > 211:
            raise ValueError(f"{name}: 無效線寬")
        linetype = raw.get("linetype", "CONTINUOUS")
        if not isinstance(linetype, str) or not linetype.strip():
            raise ValueError(f"{name}: 無效線型")
        clean.append({"name": name, "aci": aci, "linetype": linetype,
                      "lineweight": lineweight, "plot": bool(raw.get("plot", True)),
                      "category": str(raw.get("category", "")),
                      "aliases": [str(a) for a in raw.get("aliases", []) if isinstance(a, str)][:50]})
    return {"schema": "cad-layer-standard/v1", "name": str(data.get("name", "Unnamed Standard")),
            "version": str(data.get("version", "1.0.0")), "layers": clean}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s.casefold())


def _guess_category(row: dict) -> tuple[str, str, float]:
    types = row.get("types") or {}
    blocks = row.get("block_names") or {}
    total = sum(types.values())
    # An explicit architectural term in a layer name expresses intended usage.
    # It outranks incidental content such as a parking layer containing only TEXT.
    lower = str(row.get("name", "")).lower()
    for cat, terms in CATEGORY_WORDS.items():
        if any(term in lower for term in terms):
            return cat, "來源圖層名稱含明確建築用途詞；仍需人工核准", 0.62
    if total:
        annotation = sum(types.get(t, 0) for t in ("TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER"))
        if annotation / total >= 0.8:
            return "annotation", "80% 以上內容為文字或標註物件", 0.85
        hatch = types.get("HATCH", 0)
        if hatch / total >= 0.85:
            return "hatch", "主要由填充線物件構成", 0.78
    if blocks:
        strong = []
        for block in blocks:
            for cat, terms in CATEGORY_WORDS.items():
                if cat not in ("annotation", "hatch") and any(term in block.lower() for term in terms):
                    strong.append(cat)
        if strong and len(set(strong)) == 1:
            return strong[0], "圖塊名稱提供用途線索，尚未驗證幾何", 0.7
    return "unknown", "僅有幾何／物件性質，無足夠用途證據", 0


def suggest(inventory: dict, standard: dict) -> list[dict]:
    standard = validate_standard(standard)
    suggestions = []
    for row in inventory["layers"]:
        if row["protected"]:
            suggestions.append({"source": row["name"], "target": None, "confidence": 1, "reason": row["protected_reason"],
                                "category": "protected", "status": "protected"})
            continue
        cat, reason, cat_conf = _guess_category(row)
        empty_layer = row["total"] == 0
        if empty_layer:
            cat_conf = 0
            reason = "空白圖層，沒有物件證據"
        candidates = []
        for target in standard["layers"]:
            exact = _norm(row["name"]) == _norm(target["name"])
            alias = _norm(row["name"]) in {_norm(x) for x in target["aliases"]}
            string_sim = SequenceMatcher(None, _norm(row["name"]), _norm(target["name"])).ratio()
            matching_cat = bool(cat != "unknown" and target["category"].lower() == cat)
            # Never report a strong match solely from layer-name resemblance.
            # Fuzzy names alone are too weak for CAD semantics. Require very high
            # resemblance, and never let an empty layer fuzzy-match a target.
            confidence = 0.9 if exact or alias else (min(0.42, string_sim * 0.42) if string_sim >= 0.85 else 0)
            if matching_cat:
                confidence = max(confidence, cat_conf * 0.78)
            elif cat != "unknown" and target["category"]:
                confidence *= 0.55
            if empty_layer and not (exact or alias):
                confidence = 0
            candidates.append((confidence, target["name"], matching_cat, exact or alias))
        candidates.sort(reverse=True)
        best = candidates[0] if candidates else (0, None, False, False)
        score, target_name, catmatch, aliasmatch = best
        tied = bool(score >= 0.25 and not aliasmatch and any(score - item[0] <= 0.02 for item in candidates[1:]))
        if score < 0.25 or tied:
            target_name = None
        if aliasmatch:
            reason = f"標準圖層名稱／別名符合；{reason}"
        elif catmatch:
            reason = f"目標類別符合；{reason}"
        if tied:
            reason = f"多個標準目標證據相近，禁止自動指定；{reason}"
        suggestions.append({"source": row["name"], "target": target_name,
                            "confidence": round(score, 2), "category": cat,
                            "reason": reason, "status": "ambiguous" if tied else "suggested" if target_name else "unknown",
                            "candidates": [{"target": item[1], "confidence": round(item[0], 2)} for item in candidates[:5] if item[0] > 0]})
    return suggestions


def _entity_signature(doc: Any) -> dict:
    """Geometry neutral check, ignoring layer only. Preserve entity counts and attribute signatures."""
    groups = defaultdict(list)

    def capture(e: Any, group: str):
        attrs = e.dxf.all_existing_dxf_attribs()
        attrs.pop("layer", None)
        attrs.pop("owner", None)
        attrs.pop("handle", None)
        # DXF serialization can convert Vec3/TagStorage; string form remains stable.
        parts = [(key, str(value)) for key, value in sorted(attrs.items())]
        if e.dxftype() == "LWPOLYLINE":
            parts.append(("vertices", str(list(e.get_points("xyseb")))))
        groups[group].append((e.dxftype(), tuple(parts)))

    for layout in doc.layouts:
        for e in layout:
            capture(e, f"layout:{layout.name}")
    for block in doc.blocks:
        if not block.name.startswith("*") or block.name.upper().startswith("*U"):
            for e in block:
                capture(e, f"block:{block.name}")
    return {k: sha256(repr(sorted(v)).encode()).hexdigest() for k, v in groups.items()}


def verify_actions(inventory: dict, standard: dict, actions: list[dict]) -> tuple[dict[str, str], list[dict]]:
    clean = validate_standard(standard)
    allowed = {l["name"] for l in clean["layers"]}
    originals = {l["name"]: l for l in inventory["layers"]}
    indexed: dict[str, dict] = {}
    for action in actions:
        if not isinstance(action, dict) or action.get("source") not in originals:
            raise ValueError("映射中含有未知來源圖層。")
        src = action["source"]
        if src in indexed:
            raise ValueError(f"來源圖層重複：{src}")
        indexed[src] = action
    if indexed.keys() != originals.keys():
        missing = originals.keys() - indexed.keys()
        raise ValueError(f"尚未審核全部來源圖層：{', '.join(sorted(missing))}")
    mapping = {}
    normalized = []
    for src, item in indexed.items():
        decision = item.get("decision")
        target = item.get("target")
        protected = originals[src]["protected"]
        if protected:
            if decision != "keep" or target not in (None, src):
                raise ValueError(f"受保護圖層不可轉換：{src}")
        elif decision == "map":
            if target not in allowed:
                raise ValueError(f"{src} 的目標不在標準圖層：{target}")
            mapping[src] = target
        elif decision != "keep":
            raise ValueError(f"{src} 尚未獲得人工核准（map/keep）。")
        normalized.append({"source": src, "decision": decision, "target": target if decision == "map" else None,
                           "approved": True, "reason": item.get("reason", "human_review")})
    return mapping, normalized


def convert_dxf(source: Path, inventory: dict, standard: dict, actions: list[dict], output_dir: Path) -> dict:
    if inventory.get("filetype") != "DXF" or not inventory.get("can_convert"):
        raise ValueError("此轉換器只能寫入 DXF。DWG 需先使用已驗證的 AutoCAD 執行接入層。")
    if digest_file(source) != inventory.get("sha256"):
        raise ValueError("來源檔案在盤點後已被修改，請重新盤點。")
    mappings, review = verify_actions(inventory, standard, actions)
    std = validate_standard(standard)
    target_defs = {l["name"]: l for l in std["layers"]}
    doc = ezdxf.readfile(source)
    before = _entity_signature(doc)
    counters = Counter()
    for src, dst in mappings.items():
        target = target_defs[dst]
        if dst not in doc.layers:
            doc.layers.new(dst, dxfattribs={"color": target["aci"], "linetype": "CONTINUOUS", "lineweight": target["lineweight"], "plot": int(target["plot"])})
        rec = doc.layers.get(dst)
        rec.dxf.color = target["aci"]
        rec.dxf.lineweight = target["lineweight"]
        rec.dxf.plot = int(target["plot"])
        if target["linetype"] in doc.linetypes:
            rec.dxf.linetype = target["linetype"]
        else:
            raise ValueError(f"目標線型 {target['linetype']} 不在檔案中。為避免錯誤替代，未進行寫入。")
    for layout in doc.layouts:
        for entity in layout:
            old_layer = entity.dxf.get("layer", "0")
            if old_layer in mappings and mappings[old_layer] != old_layer:
                entity.dxf.layer = mappings[old_layer]
                counters[old_layer] += 1
    if _entity_signature(doc) != before:
        raise RuntimeError("轉換過程中偵測到非圖層的幾何或屬性異動，已中止。")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{source.stem}_standardized.dxf"
    suffix = 2
    while final_path.exists() or final_path.resolve() == source.resolve():
        final_path = output_dir / f"{source.stem}_standardized_{suffix}.dxf"
        suffix += 1
    try:
        doc.saveas(final_path)
        reopened = ezdxf.readfile(final_path)
        after = _entity_signature(reopened)
        if after != before:
            final_path.unlink(missing_ok=True)
            raise RuntimeError("另存後回讀未通過幾何／屬性一致性驗證，輸出已刪除。")
        output_scan = scan_dxf(final_path)
        if sum(x["total"] for x in output_scan["layers"]) != inventory["total_entities"]:
            final_path.unlink(missing_ok=True)
            raise RuntimeError("另存後回讀物件數量不符，輸出已刪除。")
    except Exception:
        # Do not leave an output that has not passed post-save validation.
        final_path.unlink(missing_ok=True)
        raise
    plan = {"schema": "cad-layer-mapping/v1", "source": inventory["filename"], "source_sha256": inventory["sha256"],
            "standard": {"name": std["name"], "version": std["version"]}, "actions": review,
            "block_definition_policy": "preserve", "entity_property_override_policy": "preserve", "scope": "model_and_paper"}
    report = {"status": "PASS", "source_unchanged": digest_file(source) == inventory["sha256"],
              "geometry_and_non_layer_properties_match": after == before,
              "save_reopen": True, "entity_count_before": inventory["total_entities"],
              "entity_count_after": output_scan["total_entities"],
              "mapped_entity_counts": dict(counters), "block_definitions_unchanged": True,
              "output": final_path.name, "warnings": inventory.get("warnings", [])}
    (output_dir / "mapping.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": final_path.name, "mapping": "mapping.json", "validation": "validation.json", "report": report}
