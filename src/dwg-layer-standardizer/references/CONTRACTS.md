# Data contracts v1.0

## Layer inventory: `cad-layer-inventory/v1`

```json
{
  "schema": "cad-layer-inventory/v1",
  "job_id": "UUID",
  "filename": "input.dxf",
  "filetype": "DXF",
  "sha256": "SHA256",
  "can_convert": true,
  "layers": [{
    "name": "A-WALL",
    "color": {"aci": 8, "true_color": null},
    "linetype": "CONTINUOUS", "lineweight": 35, "plot": true,
    "locked": false, "frozen": false, "off": false,
    "counts": {"model": 20, "paper": 0, "block": 0},
    "total": 20, "types": {"LINE": 20},
    "override_count": 0, "block_names": {},
    "preview": [{"kind": "line", "points": [[0,0],[200,0]]}],
    "protected": false, "protected_reason": null
  }],
  "total_entities": 20,
  "warnings": []
}
```

`counts.block` is informational for DXF because the DXF converter does not write block definitions. Preview samples do not prove semantic categories. On a host with the verified AutoCAD 2026 adapter, a native DWG inventory has `can_convert: true`; imported manifests remain review-only because they have no bound source file.

The tested AutoCAD 2026 native scanner also supplies `attribute_tags`, bounded `attribute_value_samples`, `block_definition_names`, an `engine` object, and top-level `totals` for model/paper/block contexts. These are additive fields under the same inventory schema.

## Standard: `cad-layer-standard/v1`

```json
{
  "schema": "cad-layer-standard/v1",
  "name": "ClientApprovedStandard",
  "version": "1.0.0",
  "layers": [
    {"name": "A-WALL", "aci": 8, "linetype": "CONTINUOUS", "lineweight": 35, "category": "wall", "aliases": ["WALL-NEW"], "plot": true}
  ]
}
```

ACI 1–255, lineweight in 1/100 mm; negative values are DXF default codes. Non-continuous target linetypes must already exist in the source DXF. The included sample standard is not the user's drawing standard.

## Mapping draft: `cad-layer-mapping/v1`

Action per source layer:

```json
{
  "source": "A-WALL",
  "decision": "map",
  "target": "STD-WALL",
  "reason": "human_review"
}
```

`decision` can be `pending`, `map`, `keep` in review drafts. Only `map` and `keep` allowed in conversion. Protected layers must be `keep`. Many-to-one mappings are allowed. A `source_sha256` binds mapping to a specific source file version.

## State machine

Standards may include the system destination `0`; `DEFPOINTS` and XREF-dependent names remain invalid targets. Source layer `0` remains keep-only. Unlike ordinary targets, destination `0` preserves its existing drawing properties rather than applying nominal standard properties. `semantic_name` resolves complete, unambiguous Chinese names before checking for empty contents. `empty_fallback` suggests `0` only for empty, unrecognized layers when that target exists; it never approves a mapping. Nonempty unknown layers continue through content/model classification and cannot receive a model-selected `0` fallback.

`SCANNED → AWAITING_REVIEW → APPROVED → WRITTEN_COPY → SAVE_REOPEN_VERIFIED`

- Missing Autodesk engine for `.dwg` → `BLOCKED_NATIVE_DWG_ADAPTER`.
- Invalid mapping, changed source, unsupported linetype, failed save/reopen → `FAIL_CLOSED`.

## Native suggestions: `cad-layer-suggestions/v1`

Each source layer includes `status`, `category`, `confidence`, `suggested_target`, `reason`, `evidence`, and up to five `candidates`. Candidate rows include a target, confidence, reason, target-category provenance, and whether the match is an exact standard name or alias.

- `suggested`: one candidate clears the threshold without a near tie.
- `ambiguous`: multiple non-exact candidates are within 0.02 of the top score; no target is selected.
- `unknown`: the evidence remains below the selection threshold.
- `protected`: system or XREF-dependent layer; keep only.

Suggestions are review evidence, never approval. Native conversion still requires an approved mapping covering every source layer.

The workbench and unified Python CLI use the same name/content rules for both DWG and DXF inventories. These suggestion rows use `target` and add `match_method` (`exact_name`, `exact_alias`, `user_convention`, `standard_suffix`, `content_evidence`, `conflicting_evidence`, `insufficient_evidence`, or `protected`) plus a per-row `engine`. Whole suffix segments preserve an explicit discipline prefix; unscoped matches prefer the architectural A target when present. Multiple matching uses remain ambiguous. User conventions are target-constrained and are not approvals. Raw scanner evidence files retain their native format.

Semantic requests omit protected layers and already resolved direct name matches. Mixed receipts distinguish local rules from model rows. An uncertain model status, score below 0.65, or competing candidate within 0.05 leaves `target` null. Scores express matching strength, not calibrated probabilities. Geometry evidence uses one uniform scale and retains centers, arc angles, closure, bulge and truncation flags.

Semantic-model receipts use the same schema and add `engine: semantic-model`, provider/model metadata, bounded-transmission disclosure, response IDs, and up to three standard-constrained candidates per source layer. The model must not receive the original drawing or return targets outside the approved standard.

## Execution contracts

Primary native workflow:

Input: `SourceDwg` + `InventoryJson` + `StandardJson` + `ApprovedMapping`.
Output: `StandardizedDwg` + `MappingJson` + `ValidationJson` + `WriteExpectationJson`.

Compatibility workflow:

Input: `SourceDxf` + `InventoryJson` + `StandardJson` + `ApprovedMapping`.
Output: `StandardizedDxf` + `MappingJson` + `ValidationJson`.

## Native DWG validation: `dwg-layer-validation/v1`

A PASS report records source/output SHA-256 values from the three-process write → cleanup → reopen-validation workflow, authoritative entity counts, non-layer signatures, mapped-entity counts by context, preserved XREF counts, verified target layers, verified removal of unreferenced mapped source layers, and the policies used for block definitions and entity overrides. Authoritative entities include owning `Dimension` objects. AutoCAD-regenerated `*D` dimension display caches are excluded from reopen traversal because they are derived state and opening thousands of regenerated cache objects can terminate AutoCAD 2026; the write expectation retains their source-snapshot diagnostics without treating them as PASS authority.

Native scope is `model_paper_and_editable_block_definitions`. XREF definitions and dependent layers are preserved. Entity color, linetype, and lineweight overrides remain unchanged. A failed validation must not leave a standardized DWG available as a successful output.

## Optional DWS validation: `dws-layer-translation-validation/v1`

The reusable DWS is an additional output only after the primary native DWG has passed `dwg-layer-validation/v1`. Its report records the source and DWS SHA-256 values, `actual_autocad_load: true`, Layer Translator settings, mapped source and target counts, entity counts before/after, verified mapped entities, target-layer property verification, verified removal of mapped source layers, and the validation policy.

Acceptance requires a real AutoCAD 2026 desktop `acet-laytrans` load and apply on a disposable source copy, followed by bounded mapped-source-layer cleanup on the disposable copy and Save/Reopen verification in a separate fresh Core Console process. The cleanup set must match the approved mapping, skips source layers already removed by Layer Translator, and never modifies the source or verified primary DWG. File existence or managed-code inspection of the DWS alone is insufficient. Any DWS failure leaves the primary DWG status as PASS and removes the unverified DWS.

A reusable DWS is available only for complete whole-layer decisions. Object-level exceptions cannot be represented by AutoCAD Layer Translator and therefore fail this optional stage without downgrading the verified DWG.
