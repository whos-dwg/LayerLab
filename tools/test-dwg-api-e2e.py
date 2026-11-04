from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--standard", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    args.workspace.mkdir(parents=True, exist_ok=True)
    os.environ["CADLAYER_WORKSPACE"] = str(args.workspace.resolve())

    from cadlayer.app import ActionsPayload, create_job, convert, health

    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    standard = json.loads(args.standard.read_text(encoding="utf-8"))
    actions = mapping.get("actions") or mapping.get("mappings")
    if not isinstance(actions, list):
        raise AssertionError("Mapping has no actions/mappings array")

    assert health()["dwg_convert"] is True
    scan = create_job(args.source.name, args.source.read_bytes(), "dwg")
    inventory = scan["inventory"]
    assert inventory["filetype"] == "DWG"
    assert inventory["can_convert"] is True
    assert inventory["sha256"].upper() == mapping["source_sha256"].upper()

    result = convert(inventory["job_id"], ActionsPayload(standard=standard, actions=actions))
    assert result["report"]["status"] == "PASS"
    assert result["report"]["output"]["save_reopen"] is True
    print(json.dumps({
        "status": "PASS",
        "job_id": inventory["job_id"],
        "layers": len(inventory["layers"]),
        "entities": inventory["total_entities"],
        "mapped_entities": result["report"]["mapped_entities"],
        "output": result["output"],
        "validation": result["validation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
