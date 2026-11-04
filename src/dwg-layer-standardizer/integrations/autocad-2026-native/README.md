# AutoCAD 2026 native DWG adapter

This adapter scans and standardizes native DWG files through AutoCAD 2026 Core Console. It never converts DWG through DXF and never overwrites the source.

## Capabilities

- full layer-table and content inventory across model, paper, and block-definition contexts;
- content-based mapping suggestions with explicit evidence;
- protected handling for `0`, `DEFPOINTS`, and XREF-dependent layers;
- native layer creation/property conformance and entity layer reassignment;
- mapping inside editable block definitions while preserving XREF definitions;
- non-layer content verification of AutoCAD-regenerated `*D` dimension anonymous blocks as derived display caches;
- preservation of entity color, linetype, and lineweight overrides;
- two-process Save/Reopen verification and source SHA-256 protection.

## Scan

```powershell
.\run-native-scan.ps1 -Source input.dwg -Standard standard.json -OutFolder new-scan-folder
```

## Convert after human approval

```powershell
.\run-native-convert.ps1 `
  -Source input.dwg `
  -Mapping approved-mapping.json `
  -Standard standard.json `
  -OutFolder new-output-folder
```

The mapping must cover every source layer exactly once with `map` or `keep`, be bound to the source SHA-256, and keep protected layers unchanged. The output folder must not already exist.

The first AutoCAD process captures an immutable pre-write signature, applies the approved mapping to its throwaway active document, and writes a new DWG plus the expected entity/layer state without overwriting the source. Editable block definitions are mapped and XREF definitions are preserved. AutoCAD-generated `*D` dimension anonymous blocks are not edited directly because AutoCAD regenerates those derived caches (and therefore their handles and internal cache layers) from the owning Dimension and DimStyle. The writer deliberately does not perform another full traversal of the mutated in-memory database: AutoCAD 2026 can access-violate on large drawings with thousands of anonymous blocks during that redundant pass. After the writer exits, a second AutoCAD process opens the exact output and authoritatively verifies ordinary/editable entities—including the owning Dimension objects—by exact handle, type, layer, count, and non-layer signature. Regenerated `*D` cache entities are verified separately by exact count plus a handle-independent multiset of type, geometry, text, and entity-override signatures; their derived internal layer is not treated as authoritative. It also verifies target layer properties and source integrity. Failure removes the unverified DWG.
