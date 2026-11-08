"""Loopback-only DWG/DXF layer standardization workbench API."""
from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir
import json
import os
import shutil
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import scan_dxf, validate_standard, suggest, convert_dxf, verify_actions
from .native import (available as native_available, completed_dws, create_dws,
                     scan_dwg, convert_dwg)
from .semantic import configuration as semantic_configuration, semantic_suggest

BASE = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("CADLAYER_WORKSPACE", str(Path(gettempdir()) / "cadlayer-mvp-jobs"))).resolve()
STANDARD_PATH = Path(os.environ.get(
    "CADLAYER_STANDARD",
    str(BASE / "standards" / "DEFAULT_STANDARD.json"),
)).resolve()
DEMO_DXF = BASE / "examples" / "demo_input.dxf"
MAX_UPLOAD = 250 * 1024 * 1024

app = FastAPI(title="DWG Layer Standardizer", version="1.0.0", docs_url="/api/docs", redoc_url=None)


class ActionsPayload(BaseModel):
    standard: dict
    actions: list[dict]


class SemanticPayload(BaseModel):
    standard: dict


def get_job(job_id: str) -> Path:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown job")
    folder = WORKSPACE / job_id
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")
    return folder


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def completed_result(folder: Path) -> dict | None:
    """Return only a fully verified prior conversion; partial output stays unusable."""
    output_dir = folder / "output"
    validation_path = output_dir / "validation.json"
    receipt_path = output_dir / "mapping.json"
    outputs = list(output_dir.glob("*_standardized.dwg")) + list(output_dir.glob("*_standardized.dxf"))
    if len(outputs) != 1 or not validation_path.is_file() or not receipt_path.is_file():
        return None
    try:
        validation = load_json(validation_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(validation, dict) or validation.get("status") != "PASS":
        return None
    output = validation.get("output")
    if isinstance(output, dict):
        # Native DWG validation uses nested output metadata.
        verified_reopen = output.get("save_reopen") is True
    elif isinstance(output, str):
        # DXF validation records its output filename and reopen flag at the top level.
        verified_reopen = output == outputs[0].name and validation.get("save_reopen") is True
    else:
        verified_reopen = False
    if not verified_reopen:
        return None
    result = {
        "output": outputs[0].name,
        "mapping": receipt_path.name,
        "validation": validation_path.name,
        "report": validation,
    }
    dws = completed_dws(output_dir)
    if dws is not None:
        result["dws"] = dws
    return result


def create_job(original_filename: str, data: bytes, kind: str) -> dict:
    job_id = str(uuid.uuid4())
    folder = WORKSPACE / job_id
    folder.mkdir(parents=True, exist_ok=False)
    filename = Path(original_filename.replace('\\', '/')).name
    if not filename or len(filename) > 140:
        filename = "input." + kind
    inp = folder / filename
    try:
        inp.write_bytes(data)
        if kind == "dxf":
            inventory = scan_dxf(inp, job_id)
        elif kind == "dwg":
            if not native_available():
                raise ValueError("此電腦未偵測到 AutoCAD 2026 原生 DWG adapter。")
            inventory = scan_dwg(inp, job_id, load_json(STANDARD_PATH), folder)
        elif kind == "json":
            inventory = load_json(inp)
            if inventory.get("schema") != "cad-layer-inventory/v1" or not isinstance(inventory.get("layers"), list):
                raise ValueError("JSON 不是有效的 cad-layer-inventory/v1。")
            # Imported Autodesk manifests are read-only until a native adapter is verified.
            inventory["job_id"] = job_id
            inventory["filename"] = filename
            inventory["filetype"] = "MANIFEST"
            inventory["can_convert"] = False
            for layer in inventory["layers"]:
                if not isinstance(layer.get("name"), str):
                    raise ValueError("Manifest layer 缺少 name。")
                layer.setdefault("protected", layer["name"].upper() in ("0", "DEFPOINTS") or "|" in layer["name"])
                layer.setdefault("protected_reason", "受保護" if layer["protected"] else None)
                layer.setdefault("types", {})
                layer.setdefault("preview", [])
                layer.setdefault("total", 0)
                layer.setdefault("counts", {"model": 0, "paper": 0, "block": 0})
                layer.setdefault("override_count", 0)
                layer.setdefault("block_names", {})
                layer.setdefault("color", {"aci": 7, "true_color": None})
                layer.setdefault("linetype", "UNKNOWN")
                layer.setdefault("lineweight", -3)
            inventory.setdefault("total_entities", sum(x["total"] for x in inventory["layers"]))
            inventory.setdefault("warnings", [])
            inventory["warnings"].append("外部盤點清單僅能審核映射；尚未接入原生 DWG 寫入。")
        else:
            raise ValueError("不支援的檔案類型。")
        (folder / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        standard = load_json(STANDARD_PATH)
        return {"inventory": inventory, "standard": standard, "suggestions": suggest(inventory, standard)}
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


@app.exception_handler(ValueError)
async def value_error_handler(request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    native = native_available()
    configured_standard = load_json(STANDARD_PATH)
    semantic = semantic_configuration()
    return {"version": "1.0.0", "dxf_scan": True, "dxf_convert": True,
            "dwg_scan": native, "dwg_convert": native, "mode": "local_loopback",
            "dwg_engine": "AutoCAD 2026 Core Console" if native else None,
            "standard": {"name": configured_standard.get("name"),
                         "version": configured_standard.get("version"),
                         "path": str(STANDARD_PATH),
                         "demo": "DEMO" in str(configured_standard.get("name", "")).upper()},
            "semantic_suggest": semantic}


@app.get("/api/standard")
def standard():
    return load_json(STANDARD_PATH)


@app.post("/api/scan")
async def scan(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".dwg", ".dxf", ".json"):
        raise HTTPException(status_code=422, detail="支援原生 .dwg、.dxf 或 inventory.json。")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="檔案超過 250 MB 上限。")
    if not data:
        raise HTTPException(status_code=422, detail="空白檔案。")
    return create_job(file.filename or "input" + suffix, data, suffix[1:])


@app.post("/api/demo")
def demo():
    return create_job(DEMO_DXF.name, DEMO_DXF.read_bytes(), "dxf")


@app.post("/api/suggest")
def suggested(payload: dict = Body(...)):
    inventory = payload.get("inventory", {})
    return {"standard": validate_standard(payload.get("standard", {})),
            "suggestions": suggest(inventory, payload.get("standard", {}))}


@app.post("/api/jobs/{job_id}/semantic-suggest")
def semantic_suggested(job_id: str, payload: SemanticPayload):
    folder = get_job(job_id)
    inventory = load_json(folder / "inventory.json")
    standard = validate_standard(payload.standard)
    result = semantic_suggest(inventory, standard)
    receipt_name = f"semantic-suggestions-{uuid.uuid4().hex[:12]}.json"
    receipt_path = folder / receipt_name
    receipt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**result, "receipt": receipt_name,
            "download": f"/api/jobs/{job_id}/files/{receipt_name}"}


@app.post("/api/jobs/{job_id}/plan")
def plan(job_id: str, payload: ActionsPayload):
    folder = get_job(job_id)
    inventory = load_json(folder / "inventory.json")
    std = validate_standard(payload.standard)
    # Allow pending decisions in an exported review draft, but never permit those to execute.
    originals = {x["name"] for x in inventory["layers"]}
    if {x.get("source") for x in payload.actions} != originals or len(payload.actions) != len(originals):
        raise ValueError("Mapping Profile 應包含每一個來源圖層一次。")
    targets = {x["name"] for x in std["layers"]}
    for act in payload.actions:
        if act.get("decision") == "map" and act.get("target") not in targets:
            raise ValueError(f"{act['source']} 的目標圖層不在標準清單。")
    profile = {"schema": "cad-layer-mapping/v1", "source": inventory["filename"],
               "source_sha256": inventory.get("sha256"),
               "source_type": inventory["filetype"], "standard": std, "actions": payload.actions,
               "approved": all(a.get("decision") in ("map", "keep") for a in payload.actions),
               "write_performed": False,
               "scope": "model_paper_and_editable_block_definitions" if inventory["filetype"] == "DWG" else "model_and_paper_only",
               "block_definition_policy": "map_editable_preserve_xref" if inventory["filetype"] == "DWG" else "preserve",
               "entity_property_override_policy": "preserve"}
    path = folder / "mapping-draft.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"filename": path.name, "approved": profile["approved"],
            "download": f"/api/jobs/{job_id}/files/{path.name}"}


@app.post("/api/jobs/{job_id}/convert")
def convert(job_id: str, payload: ActionsPayload):
    folder = get_job(job_id)
    inventory = load_json(folder / "inventory.json")
    if not inventory.get("can_convert"):
        raise ValueError("此工作只有清單，沒有可安全寫入的來源檔。")
    standard = validate_standard(payload.standard)
    verify_actions(inventory, standard, payload.actions)
    source = folder / inventory["filename"]
    if not source.is_file() or source.parent != folder:
        raise ValueError("找不到可驗證的來源檔案。")
    result = completed_result(folder)
    if result is None:
        if inventory.get("filetype") == "DWG":
            result = convert_dwg(source, inventory, standard, payload.actions, folder / "output")
        elif inventory.get("filetype") == "DXF":
            result = convert_dxf(source, inventory, standard, payload.actions, folder / "output")
        else:
            raise ValueError("此來源類型只允許審核，不允許寫入。")
    result["download_prefix"] = f"/api/jobs/{job_id}/files/"
    if result["output"].lower().endswith(".dwg"):
        result["dws_endpoint"] = f"/api/jobs/{job_id}/dws"
    return result


@app.get("/api/jobs/{job_id}/result")
def conversion_result(job_id: str):
    result = completed_result(get_job(job_id))
    if result is None:
        raise HTTPException(status_code=404, detail="尚無通過回讀驗證的輸出。")
    result["download_prefix"] = f"/api/jobs/{job_id}/files/"
    if result["output"].lower().endswith(".dwg"):
        result["dws_endpoint"] = f"/api/jobs/{job_id}/dws"
    return result


@app.post("/api/jobs/{job_id}/dws")
def dws_result(job_id: str):
    folder = get_job(job_id)
    primary = completed_result(folder)
    if primary is None or not primary["output"].lower().endswith(".dwg"):
        raise ValueError("必須先完成並通過 Save/Reopen 驗證的原生 DWG。")
    inventory = load_json(folder / "inventory.json")
    source = folder / inventory["filename"]
    verified_dwg = folder / "output" / primary["output"]
    mapping_path = folder / "native-convert-input" / "approved-mapping.json"
    standard_path = folder / "native-convert-input" / "approved-standard.json"
    try:
        result = create_dws(source, verified_dwg, mapping_path, standard_path, folder / "output")
    except Exception as exc:
        return {"status": "FAILED", "primary_dwg_status": "PASS",
                "message": "DWG 已完成、DWS 未產生。", "detail": str(exc)}
    result["primary_dwg_status"] = "PASS"
    result["download_prefix"] = f"/api/jobs/{job_id}/files/"
    return result


@app.get("/api/jobs/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    folder = get_job(job_id)
    if not filename or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Invalid file")
    # Whitelist files to prevent serving uploaded arbitrary files or host paths.
    candidates = [folder / filename, folder / "output" / filename]
    allowed = {"inventory.json", "mapping-draft.json", "mapping.json", "validation.json",
               "dws-validation.json"}
    for candidate in candidates:
        is_standardized = candidate.parent.name == "output" and "_standardized" in filename and Path(filename).suffix.lower() in (".dwg", ".dxf")
        is_dws = candidate.parent.name == "output" and filename.endswith("_layer_mapping.dws")
        if is_dws and completed_dws(folder / "output") is None:
            raise HTTPException(status_code=404, detail="DWS is not verified")
        is_semantic_receipt = candidate.parent == folder and filename.startswith("semantic-suggestions-") and filename.endswith(".json")
        if candidate.exists() and candidate.is_file() and (filename in allowed or is_standardized or is_dws or is_semantic_receipt):
            return FileResponse(candidate, filename=filename, media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="File not found")


UI = BASE / "ui"
app.mount("/", StaticFiles(directory=UI, html=True), name="ui")
