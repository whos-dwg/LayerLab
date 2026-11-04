"""Dependency-free validation shared by native DWG and DXF entry points."""
from __future__ import annotations

import re

PROTECTED = {"0", "DEFPOINTS"}


def validate_standard(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list) or not data["layers"]:
        raise ValueError("標準檔須為 JSON，且包含非空的 layers 清單。")
    seen, clean = set(), []
    for raw in data["layers"]:
        if not isinstance(raw, dict):
            raise ValueError("layers 的每一列必須是物件。")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 255 or re.search(r'[<>/\\":;?*|=,]', name):
            raise ValueError(f"不合法標準圖層名稱：{name!r}")
        if name.upper() in PROTECTED or name.upper() in seen:
            raise ValueError(f"重複或保護的目標圖層：{name}")
        seen.add(name.upper())
        aci, lineweight = raw.get("aci", 7), raw.get("lineweight", -3)
        if not isinstance(aci, int) or not 1 <= aci <= 255:
            raise ValueError(f"{name}: ACI 必須是 1–255")
        if not isinstance(lineweight, int) or lineweight < -3 or lineweight > 211:
            raise ValueError(f"{name}: 無效線寬")
        linetype = raw.get("linetype", "CONTINUOUS")
        if not isinstance(linetype, str) or not linetype.strip():
            raise ValueError(f"{name}: 無效線型")
        clean.append({"name": name, "aci": aci, "linetype": linetype, "lineweight": lineweight,
                      "plot": bool(raw.get("plot", True)), "category": str(raw.get("category", "")),
                      "aliases": [str(a) for a in raw.get("aliases", []) if isinstance(a, str)][:50]})
    return {"schema": "cad-layer-standard/v1", "name": str(data.get("name", "Unnamed Standard")),
            "version": str(data.get("version", "1.0.0")), "layers": clean}


def verify_actions(inventory: dict, standard: dict, actions: list[dict]):
    allowed = {row["name"] for row in validate_standard(standard)["layers"]}
    originals = {row["name"]: row for row in inventory["layers"]}
    indexed = {}
    for action in actions:
        if not isinstance(action, dict) or action.get("source") not in originals:
            raise ValueError("映射中含有未知來源圖層。")
        source = action["source"]
        if source in indexed:
            raise ValueError(f"來源圖層重複：{source}")
        indexed[source] = action
    if indexed.keys() != originals.keys():
        raise ValueError(f"尚未審核全部來源圖層：{', '.join(sorted(originals.keys() - indexed.keys()))}")
    mapping, normalized = {}, []
    for source, action in indexed.items():
        decision, target = action.get("decision"), action.get("target")
        if originals[source].get("protected"):
            if decision != "keep" or target not in (None, source):
                raise ValueError(f"受保護圖層不可轉換：{source}")
        elif decision == "map":
            if target not in allowed:
                raise ValueError(f"{source} 的目標不在標準圖層：{target}")
            mapping[source] = target
        elif decision != "keep":
            raise ValueError(f"{source} 尚未獲得人工核准（map/keep）。")
        normalized.append({"source": source, "decision": decision,
                           "target": target if decision == "map" else None,
                           "approved": True, "reason": action.get("reason", "human_review")})
    return mapping, normalized
