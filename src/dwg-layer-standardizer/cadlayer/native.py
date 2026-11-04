"""AutoCAD 2026 native DWG scan and verified Save/Reopen adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import shutil
import subprocess

BASE = Path(__file__).resolve().parent.parent
NATIVE = BASE / "integrations" / "autocad-2026-native"
CORE_CONSOLE = Path(r"C:\Program Files\Autodesk\AutoCAD 2026\accoreconsole.exe")
SCAN_SCRIPT = NATIVE / "run-native-scan.ps1"
CONVERT_SCRIPT = NATIVE / "run-native-convert.ps1"


def available() -> bool:
    return CORE_CONSOLE.is_file() and SCAN_SCRIPT.is_file() and CONVERT_SCRIPT.is_file() and bool(_powershell())


def _powershell() -> str | None:
    system = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if system.is_file():
        return str(system)
    return shutil.which("pwsh.exe") or shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")


def _run_script(script: Path, arguments: list[str], timeout: int = 900) -> str:
    shell = _powershell()
    if not shell or not CORE_CONSOLE.is_file():
        raise ValueError("此電腦未偵測到 AutoCAD 2026 Core Console 與 PowerShell，無法處理原生 DWG。")
    command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown native adapter failure")[-6000:]
        raise ValueError(f"AutoCAD 2026 原生處理失敗（exit {result.returncode}）：\n{detail}")
    return result.stdout


def scan_dwg(source: Path, job_id: str, standard: dict[str, Any], job_folder: Path) -> dict[str, Any]:
    if source.suffix.lower() != ".dwg":
        raise ValueError("原生掃描器只接受 .dwg。")
    standard_path = job_folder / "scan-standard.json"
    standard_path.write_text(json.dumps(standard, ensure_ascii=False, indent=2), encoding="utf-8")
    scan_folder = job_folder / "native-scan"
    _run_script(SCAN_SCRIPT, ["-Source", str(source), "-Standard", str(standard_path), "-OutFolder", str(scan_folder)])
    inventories = list(scan_folder.glob("*.native.inventory.json"))
    if len(inventories) != 1:
        raise ValueError("原生掃描未產生唯一 inventory JSON。")
    inventory = json.loads(inventories[0].read_text(encoding="utf-8"))
    inventory["job_id"] = job_id
    inventory["filename"] = source.name
    inventory["filetype"] = "DWG"
    inventory["can_convert"] = True
    inventory.setdefault("engine", {}).update({
        "application": "AutoCAD 2026",
        "method": "native .NET content scanner",
        "native_write_status": "VERIFIED_SAVE_REOPEN",
    })
    return inventory


def convert_dwg(source: Path, inventory: dict[str, Any], standard: dict[str, Any],
                actions: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    if source.suffix.lower() != ".dwg" or inventory.get("filetype") != "DWG":
        raise ValueError("原生 DWG writer 收到非 DWG 工作。")
    input_dir = output_dir.parent / "native-convert-input"
    input_dir.mkdir(parents=False, exist_ok=False)
    mapping_path = input_dir / "approved-mapping.json"
    standard_path = input_dir / "approved-standard.json"
    mapping_path.write_text(json.dumps({
        "schema": "cad-layer-mapping/v1",
        "state": "APPROVED",
        "source_file": inventory["filename"],
        "source_sha256": inventory["sha256"],
        "scope": "model_paper_and_editable_block_definitions",
        "actions": actions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    standard_path.write_text(json.dumps(standard, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _run_script(CONVERT_SCRIPT, [
            "-Source", str(source), "-Mapping", str(mapping_path),
            "-Standard", str(standard_path), "-OutFolder", str(output_dir),
        ], timeout=1800)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    outputs = list(output_dir.glob("*_standardized.dwg"))
    if len(outputs) != 1:
        raise ValueError("原生 writer 未產生唯一的標準化 DWG。")
    validation_path = output_dir / "validation.json"
    receipt_path = output_dir / "mapping.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "PASS" or not validation.get("output", {}).get("save_reopen"):
        raise ValueError("DWG 輸出未通過 Save/Reopen 驗證。")
    return {
        "output": outputs[0].name,
        "mapping": receipt_path.name,
        "validation": validation_path.name,
        "report": validation,
    }
