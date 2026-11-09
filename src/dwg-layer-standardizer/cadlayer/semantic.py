"""Model-backed semantic layer classification for extracted DWG evidence.

The original drawing is never transmitted. Only bounded inventory evidence and
the approved target-layer standard are sent to the configured Responses API.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .engine import suggest

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5-mini"
MAX_BATCH = 30


def configuration() -> dict[str, Any]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return {
        "available": bool(key),
        "provider": "openai-responses",
        "model": os.environ.get("CADLAYER_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "base_url": os.environ.get("CADLAYER_AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "sends_original_drawing": False,
        "evidence_only": True,
    }


def _bounded_dict(value: Any, limit: int = 20) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    pairs = sorted(value.items(), key=lambda item: (-int(item[1]) if isinstance(item[1], (int, float)) else 0, str(item[0])))
    return {str(key)[:120]: val for key, val in pairs[:limit]}


def _bounded_samples(value: Any, limit: int = 8) -> Any:
    if isinstance(value, dict):
        return {str(key)[:80]: [str(item)[:160] for item in (items if isinstance(items, list) else [items])[:limit]]
                for key, items in list(value.items())[:limit]}
    if isinstance(value, list):
        return [str(item)[:160] for item in value[:limit]]
    return []


def _geometry_summary(preview: Any) -> dict[str, Any]:
    if not isinstance(preview, list):
        return {"sample_count": 0, "kinds": {}, "normalized_samples": []}
    shapes = [shape for shape in preview[:30] if isinstance(shape, dict)]
    kind_counts = Counter(str(shape.get("kind", "unknown")) for shape in shapes)
    points: list[tuple[float, float]] = []

    def read_point(value):
        try:
            pair = (float(value[0]), float(value[1]))
            return pair if all(math.isfinite(v) for v in pair) else None
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    for shape in shapes:
        raw_points = shape.get("points", [])
        if isinstance(raw_points, list):
            points.extend(pair for raw in raw_points[:40] if (pair := read_point(raw)) is not None)
        center = read_point(shape.get("center"))
        if center:
            try:
                radius = abs(float(shape.get("radius", 0)))
                if math.isfinite(radius):
                    points.extend([(center[0] - radius, center[1] - radius), (center[0] + radius, center[1] + radius)])
            except (TypeError, ValueError):
                pass
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        width, height = max(max_x - min_x, 1e-9), max(max_y - min_y, 1e-9)
    else:
        min_x = min_y = 0.0
        width = height = 1.0
    normalized = []
    scale = max(width, height)
    for shape in shapes[:12]:
        item: dict[str, Any] = {"kind": str(shape.get("kind", "unknown")), "closed": bool(shape.get("closed", False)),
                                "truncated": bool(shape.get("truncated", False)), "has_bulges": bool(shape.get("has_bulges", False))}
        raw_points = shape.get("points", [])
        if isinstance(raw_points, list):
            item["points"] = [
                [round((pair[0] - min_x) / scale, 3), round((pair[1] - min_y) / scale, 3)]
                for raw in raw_points[:24] if (pair := read_point(raw)) is not None
            ]
        center = read_point(shape.get("center"))
        if center:
            item["center"] = [round((center[0] - min_x) / scale, 3), round((center[1] - min_y) / scale, 3)]
        if "radius" in shape:
            try:
                radius = float(shape["radius"])
                if math.isfinite(radius):
                    item["relative_radius"] = round(radius / scale, 3)
            except (TypeError, ValueError):
                pass
        for key in ("start", "end"):
            try:
                angle = float(shape[key])
                if math.isfinite(angle):
                    item[key] = angle
            except (KeyError, TypeError, ValueError):
                pass
        normalized.append(item)
    return {"sample_count": len(shapes), "kinds": dict(kind_counts),
            "aspect_ratio": round(width / height, 3), "uniform_scale": True, "normalized_samples": normalized}


def _layer_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row.get("name"),
        "properties": {"aci": (row.get("color") or {}).get("aci"), "linetype": row.get("linetype"),
                       "lineweight": row.get("lineweight"), "plot": row.get("plot")},
        "counts": row.get("counts", {}),
        "total": row.get("total", 0),
        "types": _bounded_dict(row.get("types")),
        "override_count": row.get("override_count", 0),
        "block_names": _bounded_dict(row.get("block_names")),
        "block_definition_names": _bounded_dict(row.get("block_definition_names")),
        "attribute_tags": _bounded_dict(row.get("attribute_tags")),
        "attribute_value_samples": _bounded_samples(row.get("attribute_value_samples")),
        "text_value_samples": _bounded_samples(row.get("text_value_samples")),
        "geometry": _geometry_summary(row.get("preview")),
    }


def _response_schema() -> dict[str, Any]:
    candidate = {
        "type": "object", "additionalProperties": False,
        "properties": {"target": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                       "reason": {"type": "string"}},
        "required": ["target", "confidence", "reason"],
    }
    suggestion = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "source": {"type": "string"},
            "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "status": {"type": "string", "enum": ["suggested", "ambiguous", "unknown"]},
            "category": {"type": "string"},
            "reason": {"type": "string"},
            "candidates": {"type": "array", "items": candidate, "maxItems": 3},
        },
        "required": ["source", "target", "confidence", "status", "category", "reason", "candidates"],
    }
    return {"type": "object", "additionalProperties": False,
            "properties": {"suggestions": {"type": "array", "items": suggestion}},
            "required": ["suggestions"]}


def _extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("模型回應缺少 structured output text。")


def _request_batch(layers: list[dict[str, Any]], standard: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    config = configuration()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("尚未設定 OPENAI_API_KEY，模型判定目前不可用。")
    target_rows = [{"name": row.get("name"), "category": row.get("category", ""),
                    "aliases": row.get("aliases", [])} for row in standard.get("layers", []) if row.get("name") != "0"]
    input_data = {"approved_standard": {"name": standard.get("name"), "version": standard.get("version"),
                                         "allowed_targets": target_rows},
                  "source_layers": [_layer_evidence(row) for row in layers]}
    instructions = (
        "You are an architectural CAD layer classifier. Infer semantics from all supplied evidence, not name similarity alone. "
        "Choose targets only from approved_standard.allowed_targets. Treat block names, text samples, object composition, context counts, "
        "and normalized geometry as evidence. Exact/whole-segment standard suffix matches and explicit user conventions "
        "are resolved locally before this request: rc and RC牆 -> A-WALL; SITE, 挑空 and TEXT -> A-TEXT; "
        "unscoped DOOR -> A-DOOR and HATCH -> A-HATCH when those targets exist. Preserve explicit discipline prefixes. "
        "For remaining layers inspect actual shape composition, closed/open outlines, curves, block names and their counts; "
        "do not equate a generic line or rectangle with a wall, column or door. Anonymous/generated block names are not semantic evidence. "
        "A single meaningful block among many unknown blocks cannot label a whole layer. Samples may be incomplete. "
        "If evidence conflicts or is weak, return target null and status ambiguous or unknown; do not force coverage. "
        "Never classify protected system/XREF layers; they are excluded from this request. Provide concise Traditional Chinese reasons."
    )
    body = {
        "model": config["model"], "store": False, "instructions": instructions,
        "input": json.dumps(input_data, ensure_ascii=False, separators=(",", ":")),
        "text": {"format": {"type": "json_schema", "name": "cad_layer_semantic_mapping",
                            "strict": True, "schema": _response_schema()}},
    }
    request = Request(f"{config['base_url']}/responses", data=json.dumps(body).encode("utf-8"), method="POST",
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=180) as result:
            response = json.loads(result.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-2000:]
        raise ValueError(f"模型服務回應 HTTP {exc.code}：{detail}") from exc
    except URLError as exc:
        raise ValueError(f"無法連線模型服務：{exc.reason}") from exc
    parsed = json.loads(_extract_output_text(response))
    suggestions = parsed.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("模型 structured output 缺少 suggestions。")
    return suggestions, str(response.get("id", ""))


def semantic_suggest(inventory: dict[str, Any], standard: dict[str, Any]) -> dict[str, Any]:
    allowed = {str(row.get("name")) for row in standard.get("layers", []) if row.get("name") != "0"}
    local = suggest(inventory, standard)
    resolved = {row["source"]: row for row in local if row["status"] == "protected" or
                row["match_method"] == "user_convention" or
                (row["status"] == "suggested" and row["match_method"] in {"exact_name", "exact_alias", "standard_suffix", "semantic_name", "empty_fallback"})}
    active = [row for row in inventory.get("layers", []) if row["name"] not in resolved]
    received: list[dict[str, Any]] = []
    response_ids: list[str] = []
    for offset in range(0, len(active), MAX_BATCH):
        batch = active[offset:offset + MAX_BATCH]
        rows, response_id = _request_batch(batch, standard)
        received.extend(rows)
        if response_id:
            response_ids.append(response_id)
    by_source = {str(row.get("source")): row for row in received if isinstance(row, dict)}
    normalized = list(resolved.values())

    def confidence(value):
        try:
            number = float(value)
            return min(1, max(0, number)) if math.isfinite(number) else 0
        except (TypeError, ValueError):
            return 0
    for row in active:
        source = str(row.get("name"))
        result = by_source.get(source)
        if result is None:
            normalized.append({"source": source, "target": None, "confidence": 0, "status": "unknown",
                               "category": "unknown", "reason": "模型未回傳此來源圖層。", "candidates": []})
            continue
        target = result.get("target")
        candidates = [{**candidate, "confidence": confidence(candidate.get("confidence"))}
                      for candidate in result.get("candidates", [])
                      if isinstance(candidate, dict) and candidate.get("target") in allowed][:3]
        if target not in allowed:
            target = None
        status = result.get("status") if result.get("status") in {"suggested", "ambiguous", "unknown"} else "unknown"
        if target is None and status == "suggested":
            status = "ambiguous"
        score = confidence(result.get("confidence"))
        if status != "suggested" or score < .65 or any(
                candidate["target"] != target and candidate["confidence"] >= score - .05 for candidate in candidates):
            target = None
            if status == "suggested":
                status = "ambiguous"
        normalized.append({"source": source, "target": target, "confidence": score,
                           "status": status, "category": str(result.get("category", "unknown"))[:80],
                           "reason": str(result.get("reason", ""))[:600], "candidates": candidates,
                           "engine": "semantic-model"})
    order = {str(row.get("name")): idx for idx, row in enumerate(inventory.get("layers", []))}
    normalized.sort(key=lambda row: order.get(row["source"], 10**9))
    config = configuration()
    return {"schema": "cad-layer-suggestions/v1", "engine": "semantic-model" if active else "rules",
            "model": config["model"], "provider": config["provider"], "source_sha256": inventory.get("sha256"),
            "standard": {"name": standard.get("name"), "version": standard.get("version")},
            "transmission": {"original_drawing": False, "bounded_inventory_evidence": True},
            "response_ids": response_ids, "created_at": datetime.now(timezone.utc).isoformat(),
            "suggestions": normalized}
