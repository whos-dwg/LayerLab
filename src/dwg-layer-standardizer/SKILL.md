---
name: dwg-layer-standardizer
description: Inspect, review, and standardize native AutoCAD DWG layers with content evidence, human-approved mappings, staged Save/Reopen validation, and auditable outputs. DXF remains a compatibility path.
metadata:
  short-description: Review and standardize DWG/DXF layers
---

# DWG Layer Standardizer

Use this skill for evidence-first standardization of vendor DWG drawings. Native DWG is the primary workflow; DXF is retained only for compatibility. Layer names are hints, not proof of object meaning.

## Required behavior

1. Preserve the original drawing. Every conversion writes a new file in a job/output folder and verifies the source SHA-256 is unchanged.
2. Load a user-approved `cad-layer-standard/v1` JSON. `standards/DEMO_STANDARD.json` is demonstration data only.
3. Scan before proposing mappings. Show per-layer properties, model/paper/block-definition counts, object types, overrides, block names, bounded text/attribute samples, and available geometry previews.
4. Treat suggestions as review evidence only. Every non-protected source layer requires an explicit `map` or `keep` decision.
5. Keep layer `0`, `DEFPOINTS`, and XREF-dependent layers protected. Do not explode blocks or force entity overrides to ByLayer.
6. Fail closed on source-hash drift, incomplete mappings, unavailable target linetypes, native engine errors, or validation mismatches. Remove an unverified output.

## Semantic model suggestions

The local workbench can use an OpenAI Responses API model to classify extracted layer evidence. Configure `OPENAI_API_KEY`; optionally set `CADLAYER_AI_MODEL` and `CADLAYER_AI_BASE_URL`. The model receives the approved target-layer list plus bounded layer names, properties, context counts, object types, block/attribute/text samples, and normalized geometry samples. It never receives the original DWG. Model output is constrained to the approved standard, saved as an auditable `cad-layer-suggestions/v1` receipt, and remains unapproved until the user explicitly accepts it.

Do not describe rule-only suggestions as AI. If the semantic provider is unavailable, label the result as a rule suggestion and keep the model action disabled.

## Primary DWG workflow

DWG processing requires AutoCAD 2026 Core Console. Never route native DWG through `ezdxf`.

- Use the unified CLI for health, scan, rescore, and conversion. The CLI routes DWG to the native adapter and never through `ezdxf`.
- The native writer maps model space, paper space, and editable block definitions. It preserves XREF content and entity-level color/linetype/lineweight overrides.
- Conversion uses three separate AutoCAD processes: one writes the mapped DWG, a second reopens the stable copy and removes mapped source layers that are no longer referenced, and a third reopens that exact output to verify entity handles, types, non-layer signatures, mapped layers, removed source layers, target-layer properties, counts, and source integrity.

```powershell
python "<skill-directory>\scripts\run.py" health
python "<skill-directory>\scripts\run.py" scan --source input.dwg --standard standard.json --out scan-job
python "<skill-directory>\scripts\run.py" convert --source input.dwg --inventory scan-job\inventory.json --standard standard.json --mapping approved-mapping.json --out verified-output
```

Successful DWG conversion produces a standardized `.dwg`, `mapping.json`, `validation.json`, the write-stage expectation, and both generated AutoCAD scripts.

## DXF compatibility workflow

Use `scripts/run.py scan`, `suggest`, and the local Web UI. DXF conversion remains non-destructive and performs geometry-neutral Save/Reopen checks.

## Local review workbench

Run `python "<skill-directory>/scripts/run.py" serve --port 8765 --open-browser`. The UI accepts `.dwg`, `.dxf`, or an inventory manifest. On this Windows host, DWG upload/convert is available only when the AutoCAD 2026 adapter health check passes.

Read [references/CONTRACTS.md](references/CONTRACTS.md) when creating, validating, or integrating standards, inventories, mappings, or validation reports. Read [integrations/autocad-2026-native/README.md](integrations/autocad-2026-native/README.md) when changing the native adapter.
