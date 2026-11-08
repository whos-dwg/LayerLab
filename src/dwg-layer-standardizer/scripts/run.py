#!/usr/bin/env python3
"""CLI for the DWG/DXF Layer Standardizer skill."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cadlayer.contracts import validate_standard, verify_actions  # noqa: E402
from cadlayer.native import (  # noqa: E402
    available as native_available, convert_dwg, scan_dwg,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_new(path: Path, value: dict) -> None:
    if path.exists():
        raise ValueError(f"Refusing to overwrite existing file: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def actions_from(mapping: dict) -> list[dict]:
    actions = mapping.get("actions", mapping.get("mappings"))
    if not isinstance(actions, list):
        raise ValueError("Mapping JSON must contain an actions array.")
    return actions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Native DWG and DXF layer standardization")
    sub = parser.add_subparsers(dest="command", required=True)
    health = sub.add_parser("health", help="report native DWG adapter availability")
    health.set_defaults(handler=run_health)
    scan = sub.add_parser("scan", help="scan a DWG or DXF and create review evidence")
    scan.add_argument("--source", type=Path, required=True)
    scan.add_argument("--standard", type=Path, required=True)
    scan.add_argument("--out", type=Path, required=True)
    scan.set_defaults(handler=run_scan)
    proposals = sub.add_parser("suggest", help="rescore an inventory against an approved standard")
    proposals.add_argument("--inventory", type=Path, required=True)
    proposals.add_argument("--standard", type=Path, required=True)
    proposals.add_argument("--out", type=Path, required=True)
    proposals.set_defaults(handler=run_suggest)
    convert = sub.add_parser("convert", help="write and Save/Reopen-verify an approved DWG or DXF copy")
    convert.add_argument("--source", type=Path, required=True)
    convert.add_argument("--inventory", type=Path, required=True)
    convert.add_argument("--standard", type=Path, required=True)
    convert.add_argument("--mapping", type=Path, required=True)
    convert.add_argument("--out", type=Path, required=True)
    convert.set_defaults(handler=run_convert)
    objects = sub.add_parser("objects", help="classify individual native DWG objects; optionally convert a copy")
    objects.add_argument("--source", type=Path, required=True)
    objects.add_argument("--standard", type=Path, required=True)
    objects.add_argument("--out", type=Path, required=True)
    objects.add_argument("--convert", action="store_true", help="authorize conservative object decisions and verified copy")
    objects.set_defaults(handler=run_objects)
    serve = sub.add_parser("serve", help="start the local review workbench")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open-browser", action="store_true")
    serve.set_defaults(handler=run_serve)
    return parser


def run_health(_args: argparse.Namespace) -> None:
    native = native_available()
    print(json.dumps({"dwg_scan": native, "dwg_convert": native,
                      "dwg_engine": "AutoCAD 2026 Core Console" if native else None,
                      "dxf_scan": True, "dxf_convert": True}, ensure_ascii=False, indent=2))


def run_scan(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    standard = validate_standard(load_json(args.standard))
    suffix = source.suffix.lower()
    if suffix not in (".dwg", ".dxf"):
        raise ValueError("Source must be a .dwg or .dxf file.")
    if args.out.exists():
        raise ValueError("Output folder already exists; use a new job folder.")
    args.out.mkdir(parents=True, exist_ok=False)
    if suffix == ".dwg":
        if not native_available():
            raise ValueError("BLOCKED_NATIVE_DWG_ADAPTER: AutoCAD 2026 Core Console is unavailable.")
        inventory = scan_dwg(source, str(uuid.uuid4()), standard, args.out)
        native_suggestions = list((args.out / "native-scan").glob("*.native.suggestions.json"))
        if len(native_suggestions) != 1:
            raise ValueError("Native DWG scan did not produce a unique suggestions file.")
        proposals = load_json(native_suggestions[0])
    else:
        from cadlayer.engine import scan_dxf, suggest
        inventory = scan_dxf(source, str(uuid.uuid4()))
        proposals = {"schema": "cad-layer-suggestions/v1",
                     "inventory_sha256": inventory.get("sha256"), "standard": standard,
                     "suggestions": suggest(inventory, standard)}
    write_new(args.out / "inventory.json", inventory)
    write_new(args.out / "suggestions.json", proposals)
    print((args.out / "inventory.json").resolve())
    print((args.out / "suggestions.json").resolve())


def run_suggest(args: argparse.Namespace) -> None:
    from cadlayer.engine import suggest
    inventory = load_json(args.inventory)
    standard = validate_standard(load_json(args.standard))
    if args.out.exists():
        raise ValueError("Output folder already exists; use a new review folder.")
    args.out.mkdir(parents=True, exist_ok=False)
    write_new(args.out / "suggestions.json", {
        "schema": "cad-layer-suggestions/v1", "inventory_sha256": inventory.get("sha256"),
        "standard": standard, "suggestions": suggest(inventory, standard),
    })
    print((args.out / "suggestions.json").resolve())


def run_convert(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    inventory = load_json(args.inventory)
    standard = validate_standard(load_json(args.standard))
    mapping = load_json(args.mapping)
    actions = actions_from(mapping)
    if digest_file(source).upper() != str(inventory.get("sha256", "")).upper():
        raise ValueError("Source SHA-256 no longer matches the scanned inventory.")
    mapping_hash = str(mapping.get("source_sha256", ""))
    if mapping_hash and mapping_hash.upper() != str(inventory.get("sha256", "")).upper():
        raise ValueError("Mapping is bound to a different source SHA-256.")
    verify_actions(inventory, standard, actions)
    entity_actions = mapping.get("entity_actions", [])
    if entity_actions:
        if source.suffix.lower() != ".dwg":
            raise ValueError("Individual object actions require native DWG.")
        from cadlayer.objects import validate_object_actions
        validate_object_actions(inventory, standard, entity_actions)
    if args.out.exists():
        raise ValueError("Output folder already exists; use a new output folder.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    filetype = str(inventory.get("filetype", "")).upper()
    if filetype == "DWG" and source.suffix.lower() == ".dwg":
        result = convert_dwg(source, inventory, standard, actions, args.out, entity_actions)
    elif filetype == "DXF" and source.suffix.lower() == ".dxf":
        from cadlayer.engine import convert_dxf
        result = convert_dxf(source, inventory, standard, actions, args.out)
    else:
        raise ValueError("Source extension does not match the inventory filetype.")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def run_serve(args: argparse.Namespace) -> None:
    # LayerLab managed UI/standard preflight.
    from sync_ui import prepare_workbench
    prepare_workbench(ROOT)
    import uvicorn
    if args.open_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run("cadlayer.app:app", host="127.0.0.1", port=args.port, log_level="info")


def run_objects(args: argparse.Namespace) -> None:
    from cadlayer.objects import run
    print(run(args.source.resolve(), args.standard.resolve(), args.out.resolve(), args.convert))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
