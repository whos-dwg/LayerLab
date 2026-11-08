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
- staged three-process Save/Reopen verification and source SHA-256 protection.

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

The first AutoCAD process captures an immutable pre-write signature, applies the approved mapping to its throwaway active document, and writes a new DWG plus the expected entity/layer state without overwriting the source. Editable block definitions are mapped and XREF definitions are preserved. AutoCAD-generated `*D` dimension anonymous blocks are not edited directly because AutoCAD regenerates those derived caches (and therefore their handles and internal cache layers) from the owning Dimension and DimStyle. The writer deliberately does not purge or perform another full traversal of the heavily mutated in-memory database: AutoCAD 2026 can access-violate on large drawings with thousands of anonymous blocks during that work.

A second AutoCAD process reopens the stable written copy and removes only the bounded set of mapped source layers that should no longer be referenced. It deliberately avoids `Database.Purge`: on large vendor drawings AutoCAD 2026 can access-violate while that API walks the complete hard-reference graph. Each candidate is erased individually; AutoCAD rejects any remaining hard reference and the conversion fails closed. A source name is retained when it is also a target, is protected, is an identity mapping, or is required by an explicit object-level keep decision. A third AutoCAD process then reopens the exact cleaned output and authoritatively verifies ordinary/editable entities—including the owning Dimension objects—by exact handle, type, layer, count, non-layer signature, and absence of the removable source layers. Regenerated `*D` cache entities are verified separately by exact count plus a handle-independent multiset of type, geometry, text, and entity-override signatures; their derived internal layer is not treated as authoritative. It also verifies target layer properties and source integrity. Failure removes the unverified DWG.

## Optional reusable DWS

After the primary DWG has passed Save/Reopen validation, the adapter can create a separate AutoCAD Layer Translator mapping:

```powershell
.\run-native-dws.ps1 `
  -Source input.dwg `
  -VerifiedDwg verified-output\input_standardized.dwg `
  -Mapping approved-mapping.json `
  -Standard approved-standard.json `
  -OutFolder verified-output
```

This optional stage requires a running AutoCAD 2026 desktop session with no active command because `Laytrans.arx` cannot be loaded by Core Console. The loader records the user's active document, opens only a disposable source copy beside the source so relative references retain their base path, invokes `acet-laytrans` with the generated DWS, saves the copy, closes it, and restores the original active document without saving or closing it. A fresh Core Console process then reopens the translated copy and verifies entity handles, expected layers, removed source layers, target properties, counts, and non-layer signatures. Only a report with `actual_autocad_load: true` makes the DWS downloadable. Failure removes the unverified DWS and never changes the already verified primary DWG.

The desktop loader tolerates a Start-page-only session with no open document. If a document is open, it must be idle and is restored afterward. AutoCAD may temporarily reject COM automation while its UI thread is busy; the loader retries only `RPC_E_CALL_REJECTED` and `RPC_E_SERVERCALL_RETRYLATER` for a bounded interval. Other COM failures still stop immediately.

Layer Translator DWS files represent whole-layer mappings only. If the approved conversion contains object-level exceptions, the adapter fails the optional DWS stage and retains the verified primary DWG as the authoritative result.


## Build dependencies

The .NET 10 adapter requires AutoCAD 2026 Update 1.2 or later; earlier 2026 updates host .NET 8 and are not validated by this build.

The `net10.0-windows` adapter explicitly references `Microsoft.WindowsDesktop.App`, matching the desktop assemblies referenced by AutoCAD. Build with `dotnet build -c Release --no-incremental -warnaserror` to detect unresolved assembly conflicts; warnings are not suppressed.

The DWS `-Mapping` argument must point to the original approved-mapping.json used for conversion, not the generated mapping.json execution receipt. The workbench supplies this original input automatically.
