"""Layer-name conventions and conservative content evidence, shared by DWG/DXF.

These are suggestions, never approvals. Generic geometry is not an object label.
"""
from __future__ import annotations

from collections import Counter
import math
import re
import unicodedata


# Explicit user conventions. Match whole names, not arbitrary substrings such as
# RC-lighting, SITE-FURN or DOOR-TEXT. Canonical spelling comes from the standard.
NAME_CONVENTIONS = {
    "rc": "a-wall", "rc牆": "a-wall", "rc墙": "a-wall",
    "site": "a-text", "挑空": "a-text", "text": "a-text",
}
CATEGORY_TERMS = {
    "wall": ("wall", "partition", "牆", "墙", "內牆", "内墙", "隔間", "隔断"),
    "door": ("door", "門", "门"),
    "window": ("window", "wind", "窗", "窗戶", "窗户"),
    "curtain": ("curtain", "窗簾", "窗帘"),
    "balcony": ("balcony", "balcon", "陽台", "阳台"),
    "beam": ("beam", "梁", "樑"),
    "column": ("column", "col", "柱"),
    "parking": ("parking", "park", "車位", "车位", "車道", "车道", "停車", "停车"),
    "stair": ("stair", "stairs", "strs", "樓梯", "楼梯"),
    "elevator": ("elevator", "lift", "電梯", "电梯"),
    "furniture": ("furn", "furniture", "sofa", "table", "chair", "bed", "家具", "沙發", "椅", "床"),
    "sanitary": ("sanitary", "toilet", "basin", "bathtub", "衛浴", "衛生設備", "馬桶", "臉盆", "面盆", "浴缸"),
    "annotation": ("annotation", "annot", "text", "文字", "註記", "注記"),
    "dimension": ("dim", "dimension", "尺寸", "標註", "标注"),
    "grid": ("grid", "軸線", "轴线"),
    "lighting": ("light", "lighting", "lamp", "燈", "灯"),
    "mep": ("mep", "機電", "机电", "配電盤", "分電盤", "空調", "出風口", "回風口"),
    "hatch": ("hatch", "填充", "剖線"),
}


def _parts(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9\u4e00-\u9fff]+", unicodedata.normalize("NFKC", value).casefold()))


def _norm(value: str) -> str:
    return "".join(_parts(value))


def _contains(parts: tuple[str, ...], term: tuple[str, ...]) -> bool:
    return bool(term) and any(parts[i:i + len(term)] == term for i in range(len(parts) - len(term) + 1))


def _categories(value: str, *, block: bool = False) -> set[str]:
    value = unicodedata.normalize("NFKC", value).casefold()
    if block and re.match(r"^(?:\*|_?u\d|a\$c|\$\d\$)", value):
        return set()
    matches = [(category, term) for category, terms in CATEGORY_TERMS.items() for term in terms
               if (term in value if re.search(r"[\u4e00-\u9fff]", term) else
                   re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", value))]
    if block and re.match(r"^door[a-z]?\d", value):
        matches.append(("door", "door"))
    # A curtain/wardrobe door is not a building window/door.
    if any(term in value for term in ("衣櫃", "衣柜", "cabinet", "wardrobe")):
        return set()
    return {category for category, term in matches
            if not any(term != longer and term in longer for _, longer in matches)}


def _scope(name: str, scopes: set[str]) -> str | None:
    parts = _parts(name)
    return parts[0] if len(parts) > 1 and parts[0] in scopes else None


def _prefer_scope(targets: list[dict], scope: str | None, scopes: set[str]) -> list[dict]:
    if scope:
        return [target for target in targets if _scope(target["name"], scopes) == scope]
    architectural = [target for target in targets if _scope(target["name"], scopes) == "a"]
    return architectural or targets


def _result(row: dict, targets: list[dict], confidence: float, reason: str, method: str, category="unknown") -> dict:
    unique = {target["name"]: target for target in targets}
    ambiguous = len(unique) > 1
    target = next(iter(unique.values())) if len(unique) == 1 else None
    if ambiguous:
        reason = "多個目標或用途證據相近，保留待判讀；" + reason
    if row.get("total", 0) == 0:
        reason += "；空白圖層，沒有內容可交叉核對"
    return {"source": row["name"], "target": target["name"] if target else None,
            "confidence": round(confidence, 2), "status": "ambiguous" if ambiguous else "suggested" if target else "unknown",
            "category": target.get("category", category) if target else category,
            "reason": reason, "match_method": method, "engine": "rules",
            "candidates": [{"target": name, "confidence": round(confidence, 2)} for name in sorted(unique)[:5]]}


def name_suggestion(row: dict, targets: list[dict]) -> dict | None:
    """Resolve exact names, approved conventions and whole suffix segments only."""
    targets = [target for target in targets if target["name"] != "0"]
    name = str(row["name"])
    scopes = {_parts(target["name"])[0] for target in targets if len(_parts(target["name"])) > 1}
    source_scope = _scope(name, scopes)
    exact = [target for target in targets if _norm(name) == _norm(target["name"])]
    if exact:
        return _result(row, exact, .99, "標準圖層名稱完全符合", "exact_name")
    conventional = NAME_CONVENTIONS.get(_norm(name))
    if conventional:
        matching = [target for target in targets if _norm(target["name"]) == _norm(conventional)]
        # A missing convention target must not silently fall back to another scope.
        return _result(row, matching, .97 if matching else 0,
                       "依使用者指定命名規則：" + name + " → " + conventional.upper() +
                       ("" if matching else "；目前標準沒有此目標"), "user_convention")
    aliases = [target for target in targets if any(_norm(name) == _norm(alias) for alias in target.get("aliases", []))]
    if aliases:
        return _result(row, aliases, .98, "標準圖層別名完全符合", "exact_alias")
    # Resolve a complete Chinese use name before testing for empty content.
    # Mixed names such as 衛浴廚房 still require evidence.
    source_parts = _parts(name)
    use_name = "".join(source_parts[1:] if source_scope else source_parts)
    chinese_uses = {category for category, terms in CATEGORY_TERMS.items()
                    if any(re.search(r"[\u4e00-\u9fff]", term) and _norm(term) == use_name for term in terms)}
    if chinese_uses:
        matching = [target for category in chinese_uses
                    for target in _content_targets(category, targets, source_scope, scopes)]
        return _result(row, matching, .94 if matching else 0,
                       "中文用途名稱完整對應標準類別" +
                       ("" if matching else "；目前標準沒有適用目標"), "semantic_name")
    parts = _parts(name)
    matches = []
    for target in targets:
        target_scope = _scope(target["name"], scopes)
        if source_scope and target_scope != source_scope:
            continue
        for alias in [target["name"], *target.get("aliases", [])]:
            token = _parts(alias)
            # Keep multiword suffixes intact: WALL-FINISH is more specific than WALL.
            tail = token[1:] if len(token) > 1 and token[0] in scopes else token
            if _contains(parts, tail):
                matches.append((len(tail), target))
    if matches:
        longest = max(length for length, _ in matches)
        matching = _prefer_scope([target for length, target in matches if length == longest], source_scope, scopes)
        return _result(row, matching, .94, "來源名稱的完整詞段對應標準後綴／別名；保留明確的專業前綴", "standard_suffix")
    return None


def _geometry_note(row: dict) -> str:
    shapes = [shape for shape in (row.get("preview") or [])[:250] if isinstance(shape, dict)]
    kinds = Counter(str(shape.get("kind", "unknown")) for shape in shapes)
    if not shapes:
        return "沒有可判讀的幾何樣本"
    slender = 0
    for shape in shapes:
        points = shape.get("points") or []
        if (shape.get("kind") != "polyline" or not shape.get("closed") or shape.get("truncated") or
                shape.get("has_bulges") or len(points) not in (4, 5)):
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in points]
            if len(pts) == 5 and pts[0] == pts[-1]:
                pts.pop()
            if len(pts) != 4 or not all(math.isfinite(v) for p in pts for v in p):
                continue
            xs, ys = zip(*pts)
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            area = abs(sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1] for i in range(4))) / 2
            if min(w, h) > 0 and max(w, h) / min(w, h) >= 8 and area / (w * h) >= .98:
                slender += 1
        except (TypeError, ValueError, IndexError):
            continue
    summary = "幾何抽樣 " + "、".join(f"{kind} {count}" for kind, count in kinds.items())
    if slender:
        summary += f"，含 {slender} 個狹長封閉直線輪廓"
    return summary + "；抽樣輪廓不能單獨證明構件用途"


def _content_targets(category: str, targets: list[dict], scope: str | None, scopes: set[str]) -> list[dict]:
    if category in ("annotation", "dimension"):
        tail = "text" if category == "annotation" else "dim"
        matching = [target for target in targets if _parts(target["name"])[1:] == (tail,)]
        preferred = _prefer_scope(matching, scope, scopes)
        if preferred:
            return preferred
        # Custom standards may use generic names such as STD-ANNOTATION.
        # Keep explicit text/dimension/title targets distinct, and preserve
        # ambiguity when more than one generic target has the same category.
        if category == "dimension":
            dimensions = _prefer_scope([target for target in targets
                                        if target.get("category", "").casefold() == "dimension"], scope, scopes)
            if dimensions:
                return dimensions
        matching = [target for target in targets
                    if target.get("category", "").casefold() == "annotation"
                    and _parts(target["name"])[-1:] not in (("text",), ("dim",), ("title",))]
    else:
        matching = [target for target in targets if target.get("category", "").casefold() == category]
    return _prefer_scope(matching, scope, scopes)


def content_suggestion(row: dict, targets: list[dict]) -> dict:
    scopes = {_parts(target["name"])[0] for target in targets if len(_parts(target["name"])) > 1}
    scope = _scope(row["name"], scopes)
    geometry = _geometry_note(row)
    if row.get("total", 0) == 0:
        return _result(row, [], 0, "名稱無直接對應", "insufficient_evidence")
    types = Counter({str(k).upper(): max(0, int(v)) for k, v in (row.get("types") or {}).items()})
    total = max(sum(types.values()), int(row.get("total", 0)))
    named = _categories(row["name"])
    block_counts = Counter()
    for name, count in (row.get("block_names") or {}).items():
        cats = _categories(str(name), block=True)
        for category in cats:
            block_counts[category] += max(0, int(count))
    inserts = types.get("INSERT", 0)
    # One named block among many anonymous/mixed inserts cannot label a layer.
    strong_blocks = {cat for cat, count in block_counts.items() if inserts and count / inserts >= .8 and
                     (inserts / total >= .5 or total <= 5)}
    significant_blocks = {cat for cat, count in block_counts.items() if count and inserts and inserts / total >= .5}
    if len(named) > 1 or len(significant_blocks) > 1 or (named and strong_blocks and named != strong_blocks):
        cats = named | significant_blocks | strong_blocks
        matching = [target for cat in cats for target in _content_targets(cat, targets, scope, scopes)]
        result = _result(row, matching, .5, "名稱／具名圖塊包含不同用途；" + geometry, "conflicting_evidence")
        result.update(target=None, status="ambiguous")
        return result
    if named:
        cat = next(iter(named))
        confidence, why = .68, "來源名稱含明確用途詞；物件類型與幾何只供交叉核對"
    elif strong_blocks:
        cat = next(iter(strong_blocks))
        confidence, why = .8, f"具名圖塊的 {cat} 用途涵蓋 {block_counts[cat]}/{inserts} 個插入，且圖塊占主要內容"
    elif total and sum(types[t] for t in ("DIMENSION", "LEADER", "MLEADER")) / total >= .8:
        cat, confidence, why = "dimension", .88, "80% 以上物件為尺寸／引線標註"
    elif total and sum(types[t] for t in ("TEXT", "MTEXT", "ATTRIB")) / total >= .8:
        cat, confidence, why = "annotation", .88, "80% 以上物件為文字／屬性文字"
    elif total and types["HATCH"] / total >= .85:
        cat, confidence, why = "hatch", .84, "85% 以上物件為原生填充"
    else:
        hints = "；具名圖塊線索未涵蓋主要內容" if block_counts else "；圖塊名稱無明確且一致的用途"
        return _result(row, [], 0, "物件組成不足以確定整層用途" + hints + "；" + geometry, "insufficient_evidence")
    matching = _content_targets(cat, targets, scope, scopes)
    return _result(row, matching, confidence if matching else 0,
                   why + "；" + geometry + ("" if matching else "；標準沒有唯一適用的目標"), "content_evidence", cat)


def suggest_layers(inventory: dict, standard: dict) -> list[dict]:
    result = []
    for row in inventory.get("layers", []):
        name = str(row["name"])
        if row.get("protected") or name.upper() in ("0", "DEFPOINTS") or "|" in name:
            result.append({"source": name, "target": None, "confidence": 1, "status": "protected",
                           "category": "protected", "reason": row.get("protected_reason") or "系統／XREF 保護圖層",
                           "match_method": "protected", "engine": "policy", "candidates": []})
            continue
        suggestion = name_suggestion(row, standard["layers"]) or content_suggestion(row, standard["layers"])
        if (row.get("total", 0) == 0 and suggestion["target"] is None and
                suggestion["match_method"] not in ("user_convention", "semantic_name")):
            zero = [target for target in standard["layers"] if target["name"] == "0"]
            if zero:
                suggestion = _result(row, zero, 1,
                                     "依使用者規則：空白且名稱無法確定用途，預設對應 0；仍須人工確認",
                                     "empty_fallback")
        result.append(suggestion)
    return result
