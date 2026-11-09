using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcadApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

[assembly: CommandClass(typeof(LayerLab.NativeLayerExport.NativeDwgCommands))]

namespace LayerLab.NativeLayerExport;

/// <summary>
/// Native DWG layer writer. It changes layer assignments and target layer-table
/// properties only, writes a new DWG, then reopens and verifies that exact file.
/// </summary>
public sealed class NativeDwgCommands
{
    [CommandMethod("LAYERLABCONVERTNATIVE", CommandFlags.Session)]
    public void ConvertNative()
    {
        Trace("command:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var sourcePath = RequireExistingFile("LAYERLAB_SOURCE_PATH");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var standardPath = RequireExistingFile("LAYERLAB_STANDARD_PATH");
        var outputPath = RequireNewPath("LAYERLAB_OUTPUT_PATH", ".dwg");
        var expectationPath = RequireNewPath("LAYERLAB_EXPECTATION_OUT", ".json");
        var validationPath = RequireNewPath("LAYERLAB_VALIDATION_OUT", ".json");
        var receiptPath = RequireNewPath("LAYERLAB_RECEIPT_OUT", ".json");

        if (Path.GetFullPath(sourcePath).Equals(Path.GetFullPath(outputPath), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Output DWG must not be the source DWG.");

        try
        {
            Trace("inputs:resolved");
            var sourceHash = ComputeSha256(sourcePath);
            var mapping = ReadMapping(mappingPath);
            var standard = ReadStandard(standardPath);
            Trace("contracts:loaded");
            if (!mapping.SourceSha256.Equals(sourceHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("The source DWG hash no longer matches the approved mapping.");

            using var documentLock = document.LockDocument();
            Trace("document:locked");
            var database = document.Database;
            var before = CaptureDatabase(database);
            Trace($"snapshot:before:{before.EntityCount}");
            var execution = ApplyMapping(database, mapping, standard, before);
            Trace($"mapping:applied:{execution.MappedEntities}");

            // Do not perform a second full traversal of the heavily mutated database.
            // AutoCAD 2026 can terminate with AccessViolationException after that traversal
            // on drawings with thousands of anonymous block definitions. The independent
            // reopen process below is the authority for entity handles, mapped layers,
            // target properties, counts, and the non-layer signature. Its expectation is
            // derived from the immutable pre-write snapshot plus the approved mapping.
            Trace("verification:deferred-to-save-reopen");

            Trace("save:start");
            database.SaveAs(outputPath, true, database.OriginalFileVersion, database.SecurityParameters);
            Trace("save:output");
            if (!ComputeSha256(sourcePath).Equals(sourceHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Source DWG changed during conversion.");

            WriteJsonNew(expectationPath, new
            {
                schema = "dwg-layer-expectation/v1",
                source_sha256 = sourceHash,
                entity_count = before.EntityCount,
                non_layer_sha256 = before.NonLayerSha256,
                    generated_dimension_block_entity_count = before.GeneratedDimensionBlockEntityCount,
                    generated_dimension_block_non_layer_sha256 = execution.ExpectedGeneratedDimensionBlockSha256,
                    generated_dimension_block_non_layer_signatures = execution.ExpectedGeneratedDimensionBlockSignatures,
                expected_layers = execution.ExpectedLayers,
                    expected_non_layer_signatures = before.Entities.ToDictionary(
                        pair => pair.Key, pair => pair.Value.NonLayerSignature, StringComparer.OrdinalIgnoreCase),
                mapped_entities = execution.MappedEntities,
                mapped_by_context = execution.MappedByContext,
                xref_entities_preserved = execution.XrefEntitiesPreserved,
                    generated_dimension_block_entities_expected = execution.GeneratedDimensionBlockEntitiesExpected,
                used_targets = execution.UsedTargets.OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToArray(),
                removed_source_layers = execution.RemovedSourceLayers.OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToArray(),
                verification_policy = "independent_save_reopen_process",
                dwg_version = database.OriginalFileVersion.ToString()
            });
            document.Editor.WriteMessage($"\nLAYERLAB_DWG_WRITE_OK mapped={execution.MappedEntities} output={outputPath}");
            Trace("command:stage1-pass");
        }
        catch
        {
            DeleteIfPresent(outputPath);
            DeleteIfPresent(expectationPath);
            DeleteIfPresent(validationPath);
            DeleteIfPresent(receiptPath);
            throw;
        }
    }

    [CommandMethod("LAYERLABVALIDATENATIVE", CommandFlags.Session)]
    public void ValidateNative()
    {
        Trace("validate:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var sourcePath = RequireExistingFile("LAYERLAB_SOURCE_PATH");
        var outputPath = RequireExistingFile("LAYERLAB_OUTPUT_PATH");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var standardPath = RequireExistingFile("LAYERLAB_STANDARD_PATH");
        var expectationPath = RequireExistingFile("LAYERLAB_EXPECTATION_OUT");
        var validationPath = RequireNewPath("LAYERLAB_VALIDATION_OUT", ".json");
        var receiptPath = RequireNewPath("LAYERLAB_RECEIPT_OUT", ".json");
        try
        {
            var mapping = ReadMapping(mappingPath);
            var standard = ReadStandard(standardPath);
            var expectation = ReadExpectation(expectationPath);
            var sourceHash = ComputeSha256(sourcePath);
            if (!sourceHash.Equals(mapping.SourceSha256, StringComparison.OrdinalIgnoreCase) ||
                !sourceHash.Equals(expectation.SourceSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Source hash does not match the mapping and write-stage expectation.");

            using var documentLock = document.LockDocument();
            var expectedAuthoritativeCount = expectation.ExpectedLayers.Count;
            if (expectation.ExpectedNonLayerSignatures.Count != expectedAuthoritativeCount ||
                expectation.EntityCount - expectation.GeneratedDimensionBlockEntityCount != expectedAuthoritativeCount)
                throw new InvalidDataException("Write expectation authoritative entity counts disagree.");
            var reopened = CaptureAuthoritativeDatabase(document.Database);
            Trace("validate:snapshot-captured");
            VerifyExpectedLayers(reopened, expectation.ExpectedLayers);
            Trace("validate:expected-layers-pass");
            VerifyTargetLayers(document.Database, standard, expectation.UsedTargets);
            Trace("validate:target-layers-pass");
            VerifyRemovedLayers(document.Database, expectation.RemovedSourceLayers);
            Trace("validate:removed-source-layers-pass");
            if (reopened.EntityCount != expectedAuthoritativeCount)
                throw new InvalidOperationException(
                    $"Authoritative entity count changed after Save/Reopen ({expectedAuthoritativeCount} -> {reopened.EntityCount}).");
            Trace("validate:entity-count-pass");
            if (!reopened.NonLayerSha256.Equals(expectation.NonLayerSha256, StringComparison.Ordinal))
            {
                foreach (var difference in expectation.ExpectedNonLayerSignatures
                    .Where(pair => !reopened.Entities.TryGetValue(pair.Key, out var actual) ||
                        !actual.NonLayerSignature.Equals(pair.Value, StringComparison.Ordinal))
                    .Take(20))
                {
                    var actual = reopened.Entities.TryGetValue(difference.Key, out var entity)
                        ? entity.NonLayerSignature : "MISSING";
                    Trace($"signature:diff:{difference.Key}:before={JsonSerializer.Serialize(difference.Value)}:after={JsonSerializer.Serialize(actual)}");
                }
                throw new InvalidOperationException("Non-layer entity signature changed after Save/Reopen.");
            }
            Trace("validate:non-layer-signature-pass");
            var owningDimensionsVerified = expectation.ExpectedNonLayerSignatures.Values.Count(IsDimensionSignature);
            Trace($"validate:derived-dimension-caches-skipped:owning-dimensions-verified:{owningDimensionsVerified}");

            var outputHash = ComputeSha256(outputPath);
            WriteJsonNew(validationPath, new
            {
                schema = "dwg-layer-validation/v1",
                status = "PASS",
                engine = new { application = "AutoCAD 2026", adapter = "dwg-layer-standardizer/native-writer-v1" },
                source = new { path = sourcePath, sha256 = sourceHash, unchanged = true },
                output = new { path = outputPath, sha256 = outputHash, save_reopen = true,
                    dwg_version = expectation.DwgVersion },
                scope = "model_paper_and_editable_block_definitions",
                xref_policy = "preserve",
                generated_block_policy = "exclude_regenerated_dimension_caches_verify_owning_dimensions",
                entity_override_policy = "preserve",
                verification_policy = "independent_save_reopen_authoritative_entities",
                entity_count_scope = "model_paper_and_editable_block_definitions_excluding_regenerated_dimension_caches",
                entity_count_before = expectedAuthoritativeCount,
                entity_count_after = reopened.EntityCount,
                source_snapshot_entity_count = expectation.EntityCount,
                non_layer_signature_before = expectation.NonLayerSha256,
                non_layer_signature_after = reopened.NonLayerSha256,
                non_layer_signature_match = true,
                source_snapshot_generated_dimension_cache_entity_count = expectation.GeneratedDimensionBlockEntityCount,
                regenerated_dimension_cache_reopen_traversal = false,
                owning_dimension_entities_verified = owningDimensionsVerified,
                mapped_entities = expectation.MappedEntities,
                mapped_by_context = expectation.MappedByContext,
                xref_entities_preserved = expectation.XrefEntitiesPreserved,
                target_layers_verified = expectation.UsedTargets.OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToArray(),
                system_zero_property_policy = "preserve_existing",
                removed_source_layers_verified = expectation.RemovedSourceLayers.OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToArray(),
                checked_at = DateTimeOffset.Now.ToString("O")
            });
            WriteJsonNew(receiptPath, new
            {
                schema = "cad-layer-mapping/v1",
                state = "EXECUTED_AND_VERIFIED",
                source_file = Path.GetFileName(sourcePath),
                source_sha256 = sourceHash,
                standard = new { standard.Name, standard.Version, source_path = standardPath },
                scope = "model_paper_and_editable_block_definitions",
                block_definition_policy = "map_editable_preserve_xref_verify_owning_dimensions_exclude_regenerated_caches",
                entity_property_override_policy = "preserve",
                actions = mapping.Actions,
                entity_actions = mapping.EntityActions,
                output_file = Path.GetFileName(outputPath),
                output_sha256 = outputHash,
                validation_file = Path.GetFileName(validationPath)
            });
            Trace("validate:pass");
            document.Editor.WriteMessage($"\nLAYERLAB_DWG_VALIDATE_OK output={outputPath}");
        }
        catch (System.Exception exception)
        {
            Trace($"validate:fail:{exception.GetType().FullName}:{exception.Message}");
            DeleteIfPresent(validationPath);
            DeleteIfPresent(receiptPath);
            throw;
        }
    }

    [CommandMethod("LAYERLABCLEANUPMAPPEDSOURCES", CommandFlags.Session)]
    public void CleanupMappedSources()
    {
        Trace("cleanup:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var sourcePath = RequireExistingFile("LAYERLAB_SOURCE_PATH");
        var outputPath = RequireExistingFile("LAYERLAB_OUTPUT_PATH");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var expectationPath = RequireExistingFile("LAYERLAB_EXPECTATION_OUT");
        try
        {
            if (!Path.GetFullPath(document.Name).Equals(outputPath, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Cleanup must run against the exact written output DWG.");
            var sourceHash = ComputeSha256(sourcePath);
            var mapping = ReadMapping(mappingPath);
            var expectation = ReadExpectation(expectationPath);
            if (!sourceHash.Equals(mapping.SourceSha256, StringComparison.OrdinalIgnoreCase) ||
                !sourceHash.Equals(expectation.SourceSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Source hash does not match the cleanup expectation.");
            var recomputed = RemovableMappedSources(mapping, expectation.UsedTargets)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (!recomputed.SetEquals(expectation.RemovedSourceLayers))
                throw new InvalidDataException("Mapped source cleanup set does not match the write expectation.");

            using var documentLock = document.LockDocument();
            RemoveMappedSourceLayers(document.Database, mapping, expectation.RemovedSourceLayers);
            Trace($"cleanup:source-layers-removed:{expectation.RemovedSourceLayers.Count}");
            document.Database.SaveAs(outputPath, true, document.Database.OriginalFileVersion,
                document.Database.SecurityParameters);
            if (!ComputeSha256(sourcePath).Equals(sourceHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Source DWG changed during mapped-source cleanup.");
            Trace("cleanup:pass");
            document.Editor.WriteMessage(
                $"\nLAYERLAB_DWG_CLEANUP_OK removed={expectation.RemovedSourceLayers.Count} output={outputPath}");
        }
        catch
        {
            Trace("cleanup:fail");
            throw;
        }
    }

    [CommandMethod("LAYERLABCREATEDWS", CommandFlags.Session)]
    public void CreateDws()
    {
        Trace("dws:create:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var standardPath = RequireExistingFile("LAYERLAB_STANDARD_PATH");
        var outputPath = RequireNewPath("LAYERLAB_DWS_OUTPUT", ".dws");
        try
        {
            var mapping = ReadMapping(mappingPath);
            var standard = ReadStandard(standardPath);
            var mapped = DwsLayerMappings(mapping, standard);
            using var documentLock = document.LockDocument();
            using var dws = new Database(true, true);
            CloneRequiredLinetypes(document.Database, dws,
                mapped.Keys.Select(name => standard.Layers.Single(layer =>
                    layer.Name.Equals(name, StringComparison.OrdinalIgnoreCase))).ToList());
            using (var transaction = dws.TransactionManager.StartTransaction())
            {
                EnsureDwsTargets(dws, transaction, standard, mapped);
                transaction.Commit();
            }
            if (mapped.ContainsKey("0")) CopyZeroLayerProperties(document.Database, dws);
            dws.SaveAs(outputPath, DwgVersion.Current);
            VerifyDwsFile(outputPath, mapping, standard);
            Trace("dws:create:pass");
            document.Editor.WriteMessage($"\nLAYERLAB_DWS_CREATE_OK output={outputPath}");
        }
        catch
        {
            DeleteIfPresent(outputPath);
            throw;
        }
    }

    [CommandMethod("LAYERLABPREPAREDWSVALIDATION", CommandFlags.Session)]
    public void PrepareDwsValidation()
    {
        Trace("dws:prepare:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var sourcePath = RequireExistingFile("LAYERLAB_SOURCE_PATH");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var standardPath = RequireExistingFile("LAYERLAB_STANDARD_PATH");
        var dwsPath = RequireExistingFile("LAYERLAB_DWS_OUTPUT");
        var expectationPath = RequireNewPath("LAYERLAB_DWS_EXPECTATION_OUT", ".json");
        try
        {
            var mapping = ReadMapping(mappingPath);
            var standard = ReadStandard(standardPath);
            var sourceHash = ComputeSha256(sourcePath);
            if (!mapping.SourceSha256.Equals(sourceHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("The source DWG hash no longer matches the approved mapping.");
            var mapped = DwsLayerMappings(mapping, standard);
            VerifyDwsFile(dwsPath, mapping, standard);
            using var documentLock = document.LockDocument();
            ValidateDwsSourceMapping(document.Database, mapping, standard);
            var before = CaptureAuthoritativeDatabase(document.Database);
            var actions = mapping.Actions.ToDictionary(action => action.Source, StringComparer.OrdinalIgnoreCase);
            var expectedLayers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var mappedEntities = 0;
            foreach (var pair in before.Entities)
            {
                var expected = pair.Value.Layer;
                if (actions.TryGetValue(pair.Value.Layer, out var action) &&
                    action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) &&
                    !string.IsNullOrWhiteSpace(action.Target))
                {
                    expected = action.Target;
                    if (!expected.Equals(pair.Value.Layer, StringComparison.OrdinalIgnoreCase)) mappedEntities++;
                }
                expectedLayers[pair.Key] = expected;
            }
            WriteJsonNew(expectationPath, new
            {
                schema = "dws-layer-translation-expectation/v1",
                source_sha256 = sourceHash,
                dws_sha256 = ComputeSha256(dwsPath),
                entity_count = before.EntityCount,
                non_layer_sha256 = before.NonLayerSha256,
                expected_layers = expectedLayers,
                expected_non_layer_signatures = before.Entities.ToDictionary(
                    pair => pair.Key, pair => pair.Value.NonLayerSignature, StringComparer.OrdinalIgnoreCase),
                mapped_entities = mappedEntities,
                mapped_sources = mapping.Actions.Count(action => action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase)),
                used_targets = mapped.Keys.OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToArray(),
                removed_source_layers = RemovableMappedSources(mapping, mapped.Keys),
                validation_policy = "actual_autocad_acet_laytrans_load_save_reopen_on_disposable_source_copy"
            });
            Trace("dws:prepare:pass");
        }
        catch
        {
            DeleteIfPresent(expectationPath);
            throw;
        }
    }

    [CommandMethod("LAYERLABCLEANUPDWSVALIDATION", CommandFlags.Session)]
    public void CleanupDwsValidation()
    {
        Trace("dws:cleanup:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var sourcePath = RequireExistingFile("LAYERLAB_SOURCE_PATH");
        var copyPath = RequireExistingFile("LAYERLAB_DWS_VALIDATION_COPY");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var expectationPath = RequireExistingFile("LAYERLAB_DWS_EXPECTATION_OUT");
        var markerPath = RequireExistingFile("LAYERLAB_DWS_LOAD_MARKER");
        try
        {
            if (!Path.GetFullPath(document.Name).Equals(copyPath, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("DWS cleanup must use the disposable validation copy.");
            if (File.ReadAllText(markerPath).Trim() != "LAYERLAB_DWS_LOAD_PASS")
                throw new InvalidOperationException("DWS cleanup requires an actual successful AutoCAD load.");
            var mapping = ReadMapping(mappingPath);
            var expectation = ReadDwsExpectation(expectationPath);
            var sourceHash = ComputeSha256(sourcePath);
            if (!sourceHash.Equals(mapping.SourceSha256, StringComparison.OrdinalIgnoreCase) ||
                !sourceHash.Equals(expectation.SourceSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Source hash does not match the DWS cleanup expectation.");
            var names = RemovableMappedSources(mapping, expectation.UsedTargets)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (!names.SetEquals(expectation.RemovedSourceLayers))
                throw new InvalidDataException("DWS cleanup set does not match the approved mapping.");
            using var documentLock = document.LockDocument();
            var present = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
            {
                var table = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
                foreach (ObjectId id in table)
                {
                    if (!id.IsValid || id.IsErased) continue;
                    present.Add(((LayerTableRecord)transaction.GetObject(id, OpenMode.ForRead)).Name);
                }
                transaction.Commit();
            }
            // Layer Translator has already removed most candidates. The primary
            // DWG cleanup routine expects existing records, so pass only those
            // still present; final validation still checks the complete set.
            names.IntersectWith(present);
            Trace($"dws:cleanup:remaining:{names.Count}");
            RemoveMappedSourceLayers(document.Database, mapping, names);
            document.Database.SaveAs(copyPath, true, document.Database.OriginalFileVersion,
                document.Database.SecurityParameters);
            if (!ComputeSha256(sourcePath).Equals(sourceHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Source DWG changed during DWS cleanup.");
            Trace("dws:cleanup:pass");
        }
        catch (System.Exception exception)
        {
            Trace($"dws:cleanup:fail:{exception.Message}");
            document.Editor.WriteMessage($"\nLAYERLAB_DWS_CLEANUP_FAILED {exception.Message}");
        }
    }

    [CommandMethod("LAYERLABVALIDATEDWS", CommandFlags.Session)]
    public void ValidateDws()
    {
        Trace("dws:validate:start");
        var document = AcadApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document.");
        var sourcePath = RequireExistingFile("LAYERLAB_SOURCE_PATH");
        var mappingPath = RequireExistingFile("LAYERLAB_MAPPING_PATH");
        var standardPath = RequireExistingFile("LAYERLAB_STANDARD_PATH");
        var dwsPath = RequireExistingFile("LAYERLAB_DWS_OUTPUT");
        var expectationPath = RequireExistingFile("LAYERLAB_DWS_EXPECTATION_OUT");
        var loadMarkerPath = RequireExistingFile("LAYERLAB_DWS_LOAD_MARKER");
        var validationPath = RequireNewPath("LAYERLAB_DWS_VALIDATION_OUT", ".json");
        try
        {
            var loadResult = File.ReadAllText(loadMarkerPath).Trim();
            if (!"LAYERLAB_DWS_LOAD_PASS".Equals(loadResult, StringComparison.Ordinal))
                throw new InvalidOperationException("AutoCAD acet-laytrans did not report a successful DWS load.");
            Trace("dws:validate:load-marker-pass");
            var mapping = ReadMapping(mappingPath);
            var standard = ReadStandard(standardPath);
            var expectation = ReadDwsExpectation(expectationPath);
            if (!ComputeSha256(sourcePath).Equals(expectation.SourceSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Source DWG changed during DWS validation.");
            if (!ComputeSha256(dwsPath).Equals(expectation.DwsSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("DWS changed between creation and AutoCAD load validation.");
            VerifyDwsFile(dwsPath, mapping, standard);
            Trace("dws:validate:dws-file-pass");
            using var documentLock = document.LockDocument();
            // Fail early on layer-state mismatches before the large entity
            // traversal. The document stays locked, and all checks describe
            // the same independently reopened validation copy.
            VerifyTargetLayers(document.Database, standard, expectation.UsedTargets);
            Trace("dws:validate:target-layers-pass");
            VerifyRemovedLayers(document.Database, expectation.RemovedSourceLayers);
            Trace("dws:validate:removed-source-layers-pass");
            var after = CaptureAuthoritativeDatabase(document.Database);
            Trace("dws:validate:snapshot-pass");
            VerifyExpectedLayers(after, expectation.ExpectedLayers);
            Trace("dws:validate:expected-layers-pass");
            if (after.EntityCount != expectation.EntityCount)
                throw new InvalidOperationException("Entity count changed while validating the DWS translation.");
            if (!after.NonLayerSha256.Equals(expectation.NonLayerSha256, StringComparison.Ordinal))
                throw new InvalidOperationException("Non-layer entity content changed while validating the DWS translation.");
            WriteJsonNew(validationPath, new
            {
                schema = "dws-layer-translation-validation/v1",
                status = "PASS",
                engine = new { application = "AutoCAD 2026", method = "acet-laytrans" },
                source = new { path = Path.GetFileName(sourcePath), sha256 = expectation.SourceSha256, unchanged = true },
                dws = new { path = Path.GetFileName(dwsPath), sha256 = expectation.DwsSha256,
                    actual_autocad_load = true, laytrans_settings = 4 },
                mapping = new { source_layers = expectation.MappedSources,
                    target_layers = expectation.UsedTargets.OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToArray() },
                entity_count_before = expectation.EntityCount,
                entity_count_after = after.EntityCount,
                mapped_entities_verified = expectation.MappedEntities,
                target_layer_properties_verified = true,
                removed_source_layers_verified = expectation.RemovedSourceLayers.OrderBy(
                    name => name, StringComparer.OrdinalIgnoreCase).ToArray(),
                entity_overrides_policy = "preserve",
                block_policy = "translate_editable_blocks",
                cleanup_policy = "bounded_mapped_source_layer_cleanup_on_disposable_copy",
                validation_policy = "actual_autocad_acet_laytrans_load_save_reopen_on_disposable_source_copy",
                checked_at = DateTimeOffset.Now.ToString("O")
            });
            Trace("dws:validate:pass");
            document.Editor.WriteMessage($"\nLAYERLAB_DWS_VALIDATE_OK output={dwsPath}");
        }
        catch (System.Exception exception)
        {
            DeleteIfPresent(validationPath);
            Trace($"dws:validate:fail:{exception.Message}");
            document.Editor.WriteMessage($"\nLAYERLAB_DWS_VALIDATE_FAILED {exception.Message}");
        }
    }

    private static Dictionary<string, List<string>> DwsLayerMappings(MappingDocument mapping,
        StandardDocument standard)
    {
        if (mapping.EntityActions.Count != 0)
            throw new InvalidDataException(
                "A reusable DWS cannot represent object-level mapping decisions; use the verified DWG instead.");
        var definitions = standard.Layers.ToDictionary(layer => layer.Name, StringComparer.OrdinalIgnoreCase);
        var result = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        foreach (var action in mapping.Actions)
        {
            if (action.Decision.Equals("keep", StringComparison.OrdinalIgnoreCase))
            {
                if (action.Target is not null)
                    throw new InvalidDataException($"A kept DWS source must not name a target: {action.Source}");
                continue;
            }
            if (!action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"DWS mapping has an unapproved decision: {action.Source}");
            if (string.IsNullOrWhiteSpace(action.Target) || !definitions.ContainsKey(action.Target))
                throw new InvalidDataException($"DWS target is outside the approved standard: {action.Target}");
            if (!result.TryGetValue(action.Target, out var sources)) result[action.Target] = sources = new List<string>();
            sources.Add(action.Source);
        }
        if (result.Count == 0) throw new InvalidDataException("The approved mapping has no reusable layer translations.");
        return result;
    }

    private static void ValidateDwsSourceMapping(Database database, MappingDocument mapping,
        StandardDocument standard)
    {
        var actions = mapping.Actions.ToDictionary(action => action.Source, StringComparer.OrdinalIgnoreCase);
        var definitions = standard.Layers.ToDictionary(layer => layer.Name, StringComparer.OrdinalIgnoreCase);
        using var transaction = database.TransactionManager.StartOpenCloseTransaction();
        var table = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        var sourceLayers = new Dictionary<string, LayerTableRecord>(StringComparer.OrdinalIgnoreCase);
        foreach (ObjectId id in table)
        {
            var layer = (LayerTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            sourceLayers[layer.Name] = layer;
        }
        if (actions.Count != sourceLayers.Count || sourceLayers.Keys.Any(name => !actions.ContainsKey(name)))
            throw new InvalidDataException("Approved DWS mapping must contain every source layer exactly once.");
        foreach (var pair in sourceLayers)
        {
            var action = actions[pair.Key];
            if (IsProtected(pair.Value) &&
                (!action.Decision.Equals("keep", StringComparison.OrdinalIgnoreCase) || action.Target is not null))
                throw new InvalidDataException($"Protected DWS source must be kept: {pair.Key}");
            if (action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) &&
                (string.IsNullOrWhiteSpace(action.Target) || !definitions.ContainsKey(action.Target)))
                throw new InvalidDataException($"DWS target is outside the approved standard: {action.Target}");
        }
    }

    private static string[] RemovableMappedSources(MappingDocument mapping, IEnumerable<string> usedTargets)
    {
        var targets = usedTargets.ToHashSet(StringComparer.OrdinalIgnoreCase);
        var retainedByObjectDecision = mapping.EntityActions.Where(action =>
                action.Decision.Equals("keep", StringComparison.OrdinalIgnoreCase) ||
                (action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) &&
                 action.Target?.Equals(action.Source, StringComparison.OrdinalIgnoreCase) == true))
            .Select(action => action.Source)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        return mapping.Actions.Where(action =>
                action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) &&
                !string.IsNullOrWhiteSpace(action.Target) &&
                !action.Source.Equals(action.Target, StringComparison.OrdinalIgnoreCase) &&
                !targets.Contains(action.Source) &&
                !retainedByObjectDecision.Contains(action.Source))
            .Select(action => action.Source)
            .OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static void CloneRequiredLinetypes(Database source, Database destination,
        IReadOnlyCollection<StandardLayer> targets)
    {
        var ids = new ObjectIdCollection();
        using (var sourceTransaction = source.TransactionManager.StartOpenCloseTransaction())
        using (var destinationTransaction = destination.TransactionManager.StartOpenCloseTransaction())
        {
            var sourceTable = (LinetypeTable)sourceTransaction.GetObject(source.LinetypeTableId, OpenMode.ForRead);
            var destinationTable = (LinetypeTable)destinationTransaction.GetObject(destination.LinetypeTableId, OpenMode.ForRead);
            var names = targets.Select(target => target.Linetype).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (targets.Any(target => target.Name == "0"))
            {
                var sourceLayers = (LayerTable)sourceTransaction.GetObject(source.LayerTableId, OpenMode.ForRead);
                var zero = (LayerTableRecord)sourceTransaction.GetObject(sourceLayers["0"], OpenMode.ForRead);
                var zeroType = (LinetypeTableRecord)sourceTransaction.GetObject(zero.LinetypeObjectId, OpenMode.ForRead);
                names.Add(zeroType.Name);
            }
            foreach (var name in names)
            {
                if (destinationTable.Has(name)) continue;
                if (!sourceTable.Has(name)) throw new InvalidDataException($"Verified DWG is missing target linetype: {name}");
                ids.Add(sourceTable[name]);
            }
        }
        if (ids.Count > 0)
            source.WblockCloneObjects(ids, destination.LinetypeTableId, new IdMapping(), DuplicateRecordCloning.Ignore, false);
    }

    private static void CopyZeroLayerProperties(Database source, Database destination)
    {
        using var sourceTransaction = source.TransactionManager.StartOpenCloseTransaction();
        using var destinationTransaction = destination.TransactionManager.StartTransaction();
        var sourceLayers = (LayerTable)sourceTransaction.GetObject(source.LayerTableId, OpenMode.ForRead);
        var destinationLayers = (LayerTable)destinationTransaction.GetObject(destination.LayerTableId, OpenMode.ForRead);
        var sourceZero = (LayerTableRecord)sourceTransaction.GetObject(sourceLayers["0"], OpenMode.ForRead);
        var destinationZero = (LayerTableRecord)destinationTransaction.GetObject(destinationLayers["0"], OpenMode.ForWrite);
        var sourceType = (LinetypeTableRecord)sourceTransaction.GetObject(sourceZero.LinetypeObjectId, OpenMode.ForRead);
        var destinationTypes = (LinetypeTable)destinationTransaction.GetObject(destination.LinetypeTableId, OpenMode.ForRead);
        destinationZero.Color = sourceZero.Color;
        destinationZero.LinetypeObjectId = destinationTypes[sourceType.Name];
        destinationZero.LineWeight = sourceZero.LineWeight;
        destinationZero.IsPlottable = sourceZero.IsPlottable;
        destinationTransaction.Commit();
    }

    private static void EnsureDwsTargets(Database database, Transaction transaction, StandardDocument standard,
        IReadOnlyDictionary<string, List<string>> mappings)
    {
        const string appName = "ACLAYTRANS";
        var regApps = (RegAppTable)transaction.GetObject(database.RegAppTableId, OpenMode.ForRead);
        if (!regApps.Has(appName))
        {
            regApps.UpgradeOpen();
            var app = new RegAppTableRecord { Name = appName };
            regApps.Add(app);
            transaction.AddNewlyCreatedDBObject(app, true);
        }
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        var lineTypeTable = (LinetypeTable)transaction.GetObject(database.LinetypeTableId, OpenMode.ForRead);
        var definitions = standard.Layers.ToDictionary(layer => layer.Name, StringComparer.OrdinalIgnoreCase);
        foreach (var pair in mappings)
        {
            var target = definitions[pair.Key];
            if (!lineTypeTable.Has(target.Linetype))
                throw new InvalidDataException($"DWS is missing target linetype: {target.Linetype}");
            LayerTableRecord record;
            if (target.Name == "0")
            {
                record = (LayerTableRecord)transaction.GetObject(layerTable["0"], OpenMode.ForWrite);
            }
            else
            {
                if (layerTable.Has(target.Name))
                    throw new InvalidDataException($"DWS target collides with a built-in layer: {target.Name}");
                layerTable.UpgradeOpen();
                record = new LayerTableRecord
                {
                    Name = target.Name,
                    Color = Color.FromColorIndex(ColorMethod.ByAci, checked((short)target.Aci)),
                    LinetypeObjectId = lineTypeTable[target.Linetype],
                    LineWeight = (LineWeight)target.Lineweight,
                    IsPlottable = target.Plot
                };
                layerTable.Add(record);
                transaction.AddNewlyCreatedDBObject(record, true);
            }
            var values = new List<TypedValue> { new((int)DxfCode.ExtendedDataRegAppName, appName) };
            values.AddRange(pair.Value.OrderBy(source => source, StringComparer.OrdinalIgnoreCase)
                .Select(source => new TypedValue((int)DxfCode.ExtendedDataAsciiString, source)));
            using var xdata = new ResultBuffer(values.ToArray());
            record.XData = xdata;
        }
    }

    private static void VerifyDwsFile(string path, MappingDocument mapping, StandardDocument standard)
    {
        var expected = DwsLayerMappings(mapping, standard);
        using var database = new Database(false, true);
        database.ReadDwgFile(path, FileOpenMode.OpenForReadAndAllShare, true, "");
        database.CloseInput(true);
        using (var transaction = database.TransactionManager.StartOpenCloseTransaction())
        {
            var layers = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
            foreach (var pair in expected)
            {
                if (!layers.Has(pair.Key)) throw new InvalidDataException($"DWS is missing target layer: {pair.Key}");
                var layer = (LayerTableRecord)transaction.GetObject(layers[pair.Key], OpenMode.ForRead);
                using var xdata = layer.GetXDataForApplication("ACLAYTRANS");
                if (xdata is null) throw new InvalidDataException($"DWS target has no ACLAYTRANS mapping: {pair.Key}");
                var actual = xdata.AsArray().Where(value => value.TypeCode == (int)DxfCode.ExtendedDataAsciiString)
                    .Select(value => Convert.ToString(value.Value, CultureInfo.InvariantCulture) ?? "")
                    .ToHashSet(StringComparer.OrdinalIgnoreCase);
                if (!actual.SetEquals(pair.Value))
                    throw new InvalidDataException($"DWS source mappings do not match the approved plan for {pair.Key}.");
            }
        }
        VerifyTargetLayers(database, standard, expected.Keys.ToHashSet(StringComparer.OrdinalIgnoreCase));
        if (CaptureDatabase(database).EntityCount != 0)
            throw new InvalidDataException("DWS must not contain source drawing entities.");
    }

    private static ExecutionResult ApplyMapping(Database database, MappingDocument mapping, StandardDocument standard,
        DatabaseSnapshot before)
    {
        Trace("apply:start");
        var sourceLayers = new Dictionary<string, LayerInfo>(StringComparer.OrdinalIgnoreCase);
        var standardLayers = standard.Layers.ToDictionary(x => x.Name, StringComparer.OrdinalIgnoreCase);
        var actions = mapping.Actions.ToDictionary(x => x.Source, StringComparer.OrdinalIgnoreCase);
        var usedTargets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var entityActions = mapping.EntityActions.ToDictionary(x => x.Handle, StringComparer.OrdinalIgnoreCase);
        var consumed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var expectedLayers = before.Entities.ToDictionary(pair => pair.Key, pair => pair.Value.Layer,
            StringComparer.OrdinalIgnoreCase);
        var mappedByContext = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var mappedEntities = 0;
        var xrefEntitiesPreserved = 0;
        var generatedDimensionBlockEntitiesExpected = before.GeneratedDimensionBlockEntityCount;
        var expectedGeneratedDimensionBlockSignatures = before.GeneratedDimensionBlockSignatures
            .OrderBy(signature => signature, StringComparer.Ordinal).ToList();
        var expectedGeneratedDimensionBlockSha256 = ComputeTextSha256(
            string.Join("\n", expectedGeneratedDimensionBlockSignatures));

        using var transaction = database.TransactionManager.StartTransaction();
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        foreach (ObjectId id in layerTable)
        {
            var layer = (LayerTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            sourceLayers[layer.Name] = new LayerInfo(layer.Name, IsProtected(layer));
        }
        Trace($"apply:layers:{sourceLayers.Count}");
        if (actions.Count != sourceLayers.Count || sourceLayers.Keys.Any(name => !actions.ContainsKey(name)))
            throw new InvalidDataException("Approved mapping must contain every DWG layer exactly once.");

        foreach (var action in actions.Values)
        {
            if (!sourceLayers.TryGetValue(action.Source, out var source))
                throw new InvalidDataException($"Mapping contains unknown source layer: {action.Source}");
            if (source.Protected && (!action.Decision.Equals("keep", StringComparison.OrdinalIgnoreCase) || action.Target is not null))
                throw new InvalidDataException($"Protected layer must be kept: {action.Source}");
            if (action.Decision.Equals("keep", StringComparison.OrdinalIgnoreCase)) continue;
            if (!action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(action.Target))
                throw new InvalidDataException($"Layer has no approved map/keep decision: {action.Source}");
            if (!standardLayers.ContainsKey(action.Target))
                throw new InvalidDataException($"Target is not in the approved standard: {action.Target}");
            usedTargets.Add(action.Target);
        }
        Trace($"apply:actions-verified:targets={usedTargets.Count}");

        foreach (var action in entityActions.Values)
        {
            if (!before.Entities.TryGetValue(action.Handle, out var snapshot) ||
                !snapshot.Layer.Equals(action.Source, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"Unknown or stale object: {action.Handle}");
            if (!sourceLayers.TryGetValue(action.Source, out var source) || source.Protected)
                throw new InvalidDataException($"Protected object cannot be overridden: {action.Handle}");
            if (action.Decision != "map" && action.Decision != "keep")
                throw new InvalidDataException("Object decision must be map or keep.");
            if (action.Decision == "map")
            {
                if (action.Target is null || !standardLayers.ContainsKey(action.Target))
                    throw new InvalidDataException($"Object target outside standard: {action.Target}");
                usedTargets.Add(action.Target);
            }
        }

        EnsureTargetLayers(database, transaction, usedTargets.Select(name => standardLayers[name]).ToList());
        Trace("apply:targets-ready");
        var blockTable = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
        foreach (ObjectId blockId in blockTable)
        {
            var block = (BlockTableRecord)transaction.GetObject(blockId, OpenMode.ForRead);
            Trace($"apply:block:{block.Handle}:{block.Name}");
            if (block.IsFromExternalReference || block.IsFromOverlayReference)
            {
                xrefEntitiesPreserved += block.Cast<ObjectId>().Count(id => id.IsValid && !id.IsErased);
                continue;
            }
            if (IsGeneratedDimensionBlock(block))
            {
                Trace($"apply:defer-generated-dimension-block:{block.Name}");
                continue;
            }
            var context = GetContext(block, transaction);
            foreach (ObjectId entityId in block)
            {
                if (!entityId.IsValid || entityId.IsErased) continue;
                if (transaction.GetObject(entityId, OpenMode.ForRead, false) is not Entity readEntity) continue;
                actions.TryGetValue(readEntity.Layer, out var action);
                var target = action?.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) == true ? action.Target : null;
                if (entityActions.TryGetValue(readEntity.Handle.ToString(), out var objectAction))
                {
                    if (!block.IsLayout || objectAction.Type != readEntity.GetRXClass().DxfName || objectAction.Context != context)
                        throw new InvalidDataException($"Object type/context mismatch or shared definition edit: {objectAction.Handle}");
                    consumed.Add(objectAction.Handle);
                    target = objectAction.Decision == "map" ? objectAction.Target : null;
                }
                if (string.IsNullOrWhiteSpace(target) || target.Equals(readEntity.Layer, StringComparison.OrdinalIgnoreCase)) continue;
                var entity = (Entity)transaction.GetObject(entityId, OpenMode.ForWrite, false, true);
                Trace($"apply:entity:{entity.Handle}:{readEntity.Layer}->{target}");
                entity.Layer = target;
                expectedLayers[entity.Handle.ToString()] = target;
                mappedEntities++;
                mappedByContext[context] = mappedByContext.TryGetValue(context, out var count) ? count + 1 : 1;
            }
        }
        if (consumed.Count != entityActions.Count)
            throw new InvalidDataException("Some object decisions were not applied (xref or shared definition).");
        var removedSourceLayers = RemovableMappedSources(mapping, usedTargets)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        Trace($"apply:source-layers-scheduled-for-cleanup:{removedSourceLayers.Count}");
        transaction.Commit();
        return new ExecutionResult(mappedEntities, mappedByContext, xrefEntitiesPreserved,
            generatedDimensionBlockEntitiesExpected, expectedGeneratedDimensionBlockSha256,
            expectedGeneratedDimensionBlockSignatures, usedTargets, removedSourceLayers, expectedLayers);
    }

    private static void RemoveMappedSourceLayers(Database database, MappingDocument mapping,
        HashSet<string> names)
    {
        if (names.Count == 0) return;

        using var transaction = database.TransactionManager.StartTransaction();
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        var current = (LayerTableRecord)transaction.GetObject(database.Clayer, OpenMode.ForRead);
        if (names.Contains(current.Name))
        {
            var action = mapping.Actions.Single(row =>
                row.Source.Equals(current.Name, StringComparison.OrdinalIgnoreCase));
            if (string.IsNullOrWhiteSpace(action.Target) || !layerTable.Has(action.Target))
                throw new InvalidDataException($"Mapped current layer has no valid replacement: {current.Name}");
            database.Clayer = layerTable[action.Target];
        }

        // Do not call Database.Purge here. On vendor drawings with thousands of
        // anonymous block definitions AutoCAD 2026 can access-violate while Purge
        // walks the entire hard-reference graph, even in a freshly reopened process.
        // ApplyMapping already reassigned every editable entity and the candidates
        // exclude protected/identity/object-exception layers. Erase each bounded
        // candidate directly; AutoCAD still rejects a layer that has any remaining
        // hard reference, which is a fail-closed outcome rather than a native crash.
        foreach (var name in names.OrderBy(value => value, StringComparer.OrdinalIgnoreCase))
        {
            Trace($"cleanup:erase:start:{name}");
            var id = layerTable[name];
            var layer = (LayerTableRecord)transaction.GetObject(id, OpenMode.ForWrite);
            try
            {
                layer.Erase();
            }
            catch (Autodesk.AutoCAD.Runtime.Exception exception)
            {
                throw new InvalidOperationException(
                    $"Mapped source layer remains referenced and cannot be removed: {name} ({exception.ErrorStatus})",
                    exception);
            }
            Trace($"cleanup:erase:pass:{name}");
        }
        transaction.Commit();
        VerifyRemovedLayers(database, names);
        Trace("cleanup:bounded-removal-verified");
    }

    private static void EnsureTargetLayers(Database database, Transaction transaction, IReadOnlyCollection<StandardLayer> targets)
    {
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        var lineTypeTable = (LinetypeTable)transaction.GetObject(database.LinetypeTableId, OpenMode.ForRead);
        foreach (var target in targets)
        {
            if (target.Name == "0")
            {
                if (!layerTable.Has("0")) throw new InvalidDataException("The built-in zero layer is missing.");
                Trace("target:preserve-system-zero");
                continue;
            }
            Trace($"target:start:{target.Name}:{target.Aci}:{target.Linetype}:{target.Lineweight}:{target.Plot}");
            if (!lineTypeTable.Has(target.Linetype))
                throw new InvalidDataException($"Target linetype does not exist in this DWG: {target.Linetype}");
            LayerTableRecord record;
            if (layerTable.Has(target.Name))
            {
                Trace($"target:existing:{target.Name}");
                record = (LayerTableRecord)transaction.GetObject(layerTable[target.Name], OpenMode.ForWrite);
                if (IsProtected(record)) throw new InvalidDataException($"Target resolves to a protected layer: {target.Name}");
            }
            else
            {
                Trace($"target:create:{target.Name}");
                layerTable.UpgradeOpen();
                record = new LayerTableRecord { Name = target.Name };
                layerTable.Add(record);
                transaction.AddNewlyCreatedDBObject(record, true);
            }
            Trace($"target:set-color:{target.Name}");
            record.Color = Color.FromColorIndex(ColorMethod.ByAci, checked((short)target.Aci));
            Trace($"target:set-linetype:{target.Name}");
            record.LinetypeObjectId = lineTypeTable[target.Linetype];
            Trace($"target:set-lineweight:{target.Name}");
            record.LineWeight = (LineWeight)target.Lineweight;
            Trace($"target:set-plot:{target.Name}");
            record.IsPlottable = target.Plot;
            Trace($"target:done:{target.Name}");
        }
    }

    private static void VerifyTargetLayers(Database database, StandardDocument standard, HashSet<string> usedTargets)
    {
        var definitions = standard.Layers.ToDictionary(x => x.Name, StringComparer.OrdinalIgnoreCase);
        using var transaction = database.TransactionManager.StartOpenCloseTransaction();
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        foreach (var name in usedTargets)
        {
            if (!layerTable.Has(name)) throw new InvalidDataException($"Saved DWG is missing target layer: {name}");
            if (name == "0") continue; // System zero keeps the source drawing's existing properties.
            var expected = definitions[name];
            var actual = (LayerTableRecord)transaction.GetObject(layerTable[name], OpenMode.ForRead);
            var lineType = (LinetypeTableRecord)transaction.GetObject(actual.LinetypeObjectId, OpenMode.ForRead);
            if (actual.Color.ColorIndex != expected.Aci ||
                !lineType.Name.Equals(expected.Linetype, StringComparison.OrdinalIgnoreCase) ||
                (int)actual.LineWeight != expected.Lineweight || actual.IsPlottable != expected.Plot)
                throw new InvalidDataException($"Saved target layer properties do not match the standard: {name}");
        }
        transaction.Commit();
    }

    private static void VerifyRemovedLayers(Database database, HashSet<string> removedLayers)
    {
        Trace($"removed-layers:verify:start:{removedLayers.Count}");
        if (removedLayers.Count == 0)
        {
            Trace("removed-layers:verify:pass");
            return;
        }
        var present = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using var transaction = database.TransactionManager.StartOpenCloseTransaction();
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        // Compare the surviving record names in managed memory; every mapped
        // source that should have been removed must still be absent.
        foreach (ObjectId id in layerTable)
        {
            if (!id.IsValid || id.IsErased) continue;
            var layer = (LayerTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            present.Add(layer.Name);
        }
        transaction.Commit();
        Trace($"removed-layers:verify:surviving:{present.Count}");
        var retained = removedLayers.Where(present.Contains).ToArray();
        Trace($"removed-layers:verify:retained:{retained.Length}");
        if (retained.Length != 0)
            throw new InvalidOperationException(
                $"Mapped source layers remain after Save/Reopen: {string.Join(", ", retained)}");
        Trace("removed-layers:verify:pass");
    }

    private static DatabaseSnapshot CaptureDatabase(Database database)
    {
        return CaptureDatabase(database, includeGeneratedDimensionCaches: true);
    }

    private static DatabaseSnapshot CaptureAuthoritativeDatabase(Database database)
    {
        return CaptureDatabase(database, includeGeneratedDimensionCaches: false);
    }

    private static DatabaseSnapshot CaptureDatabase(Database database, bool includeGeneratedDimensionCaches)
    {
        var entities = new Dictionary<string, EntitySnapshot>(StringComparer.OrdinalIgnoreCase);
        var generatedDimensionBlockSignatures = new List<string>();
        var entityCount = 0;
        var blockIds = new List<ObjectId>();
        using (var tableTransaction = database.TransactionManager.StartOpenCloseTransaction())
        {
            var blockTable = (BlockTable)tableTransaction.GetObject(database.BlockTableId, OpenMode.ForRead);
            foreach (ObjectId blockId in blockTable) blockIds.Add(blockId);
        }
        foreach (var blockId in blockIds)
        {
            // Bound native object lifetime to one block. A single OpenCloseTransaction
            // retaining 60k+ entities can access-violate when AutoCAD releases it.
            using var transaction = database.TransactionManager.StartOpenCloseTransaction();
            var block = (BlockTableRecord)transaction.GetObject(blockId, OpenMode.ForRead);
            Trace($"snapshot:block:{block.Handle}:{block.Name}");
            if (!block.IsFromExternalReference && !block.IsFromOverlayReference)
            {
                var generatedDimensionBlock = IsGeneratedDimensionBlock(block);
                if (generatedDimensionBlock && !includeGeneratedDimensionCaches)
                {
                    // *D records are AutoCAD-regenerated display caches. Opening every
                    // cache entity after dimensions have regenerated can access-violate
                    // AutoCAD 2026 on large vendor drawings. Their owning Dimension
                    // entities remain in the authoritative snapshot and are verified by
                    // handle, layer, signature and count.
                    Trace($"snapshot:block:skip-derived-dimension-cache:{block.Handle}:{block.Name}");
                    continue;
                }
                var context = GetContext(block, transaction);
                foreach (ObjectId entityId in block)
                {
                    if (!entityId.IsValid || entityId.IsErased) continue;
                    if (transaction.GetObject(entityId, OpenMode.ForRead, false) is not Entity entity) continue;
                    entityCount++;
                    var handle = entity.Handle.ToString();
                    var signature = BuildEntitySignature(entity, context, transaction);
                    if (generatedDimensionBlock)
                        generatedDimensionBlockSignatures.Add(signature);
                    else
                        entities[handle] = new EntitySnapshot(entity.Layer, signature);
                }
            }
        }
        Trace($"snapshot:entities-read:{entityCount}");
        var canonical = string.Join("\n", entities.OrderBy(x => x.Key, StringComparer.OrdinalIgnoreCase)
            .Select(x => $"{x.Key}|{x.Value.NonLayerSignature}"));
        var generatedCanonical = string.Join("\n", generatedDimensionBlockSignatures
            .OrderBy(x => x, StringComparer.Ordinal));
        var snapshot = new DatabaseSnapshot(entityCount, ComputeTextSha256(canonical), entities,
            generatedDimensionBlockSignatures.Count, ComputeTextSha256(generatedCanonical),
            generatedDimensionBlockSignatures.OrderBy(value => value, StringComparer.Ordinal).ToList());
        Trace($"snapshot:complete:{snapshot.EntityCount}");
        return snapshot;
    }

    private static string BuildEntitySignature(Entity entity, string context, Transaction transaction)
    {
        var builder = new StringBuilder();
        builder.Append(context).Append('|').Append(entity.GetRXClass().DxfName).Append('|');
        if (entity is not DBText && entity is not MText && entity is not BlockReference && entity is not Viewport)
        {
            try
            {
                var extents = entity.GeometricExtents;
                AppendPoint(builder, extents.MinPoint);
                AppendPoint(builder, extents.MaxPoint);
            }
            catch (Autodesk.AutoCAD.Runtime.Exception)
            {
                builder.Append("NO_EXTENTS|");
            }
        }
        switch (entity)
        {
            case DBText text:
                builder.Append(AuthoritativeText(text, text.TextString, transaction)).Append('|');
                var textAnchor = text.HorizontalMode == TextHorizontalMode.TextLeft &&
                    text.VerticalMode == TextVerticalMode.TextBase
                    ? text.Position : text.AlignmentPoint;
                AppendPoint(builder, textAnchor);
                builder.Append((int)text.HorizontalMode).Append(':').Append((int)text.VerticalMode).Append('|')
                    .Append(NormalizeCoordinate(text.Height)).Append('|')
                    .Append(NormalizeCoordinate(text.WidthFactor)).Append('|')
                    .Append(NormalizeCoordinate(text.Rotation)).Append('|')
                    .Append(NormalizeCoordinate(text.Oblique)).Append('|');
                break;
            case MText text:
                builder.Append(AuthoritativeText(text, text.Contents, transaction)).Append('|'); AppendPoint(builder, text.Location);
                builder.Append((int)text.Attachment).Append('|')
                    .Append(NormalizeCoordinate(text.TextHeight)).Append('|')
                    .Append(NormalizeCoordinate(text.Width)).Append('|')
                    .Append(NormalizeCoordinate(text.Rotation)).Append('|');
                break;
            case Dimension dimension:
                builder.Append(dimension.DimensionText).Append('|'); break;
            case Viewport viewport:
                builder.Append("VIEWPORT:").Append(viewport.Number).Append('|');
                // Number 1 is AutoCAD's derived overall paper-space display viewport.
                // Its extents are regenerated on SaveAs; actual floating viewports retain
                // authoritative position, size and model camera checks below.
                if (viewport.Number != 1)
                {
                    AppendPoint(builder, viewport.CenterPoint);
                    AppendPoint(builder, viewport.ViewTarget);
                    AppendPoint(builder, new Point3d(viewport.ViewDirection.X, viewport.ViewDirection.Y, viewport.ViewDirection.Z));
                    builder.Append(NormalizeCoordinate(viewport.Width)).Append('|')
                        .Append(NormalizeCoordinate(viewport.Height)).Append('|')
                        .Append(NormalizeCoordinate(viewport.ViewHeight)).Append('|')
                        .Append(NormalizeCoordinate(viewport.ViewCenter.X)).Append('|')
                        .Append(NormalizeCoordinate(viewport.ViewCenter.Y)).Append('|')
                        .Append(NormalizeCoordinate(viewport.TwistAngle)).Append('|')
                        .Append(viewport.Locked).Append('|');
                }
                break;
            case BlockReference blockReference:
                var definition = (BlockTableRecord)transaction.GetObject(
                    blockReference.IsDynamicBlock ? blockReference.DynamicBlockTableRecord : blockReference.BlockTableRecord,
                    OpenMode.ForRead);
                builder.Append(definition.Name).Append('|'); AppendPoint(builder, blockReference.Position);
                builder.Append(blockReference.Rotation.ToString("R", CultureInfo.InvariantCulture)).Append('|')
                    .Append(blockReference.ScaleFactors.X.ToString("R", CultureInfo.InvariantCulture)).Append(',')
                    .Append(blockReference.ScaleFactors.Y.ToString("R", CultureInfo.InvariantCulture)).Append(',')
                    .Append(blockReference.ScaleFactors.Z.ToString("R", CultureInfo.InvariantCulture)).Append('|');
                break;
        }
        builder.Append(entity.Color.ColorMethod).Append(':').Append(entity.Color.ColorIndex).Append('|')
            .Append(entity.Linetype).Append('|').Append((int)entity.LineWeight).Append('|')
            .Append(entity.Visible).Append('|');
        return builder.ToString();
    }

    private static string AuthoritativeText(Entity entity, string display, Transaction transaction)
    {
        if (!entity.HasFields) return display;
        var id = entity.GetField();
        if (!id.IsValid || id.IsErased) throw new InvalidDataException("Text has an unreadable field.");
        var field = (Field)transaction.GetObject(id, OpenMode.ForRead);
        // Compare the unevaluated expression, not the transient date display cache.
        return "FIELD:" + field.GetFieldCode(FieldCodeFlags.AddMarkers);
    }

    private static void VerifyExpectedLayers(DatabaseSnapshot snapshot, Dictionary<string, string> expected)
    {
        if (snapshot.Entities.Count != expected.Count)
            throw new InvalidDataException("Saved DWG entity handles do not match the source set.");
        foreach (var pair in expected)
        {
            if (!snapshot.Entities.TryGetValue(pair.Key, out var entity))
                throw new InvalidDataException($"Saved DWG is missing entity handle {pair.Key}.");
            if (!entity.Layer.Equals(pair.Value, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"Entity {pair.Key} saved on {entity.Layer}; expected {pair.Value}.");
        }
    }

    private static string GetContext(BlockTableRecord block, Transaction transaction)
    {
        if (!block.IsLayout) return "block_definition";
        var layout = (Layout)transaction.GetObject(block.LayoutId, OpenMode.ForRead);
        return layout.ModelType ? "model" : "paper";
    }

    private static bool IsProtected(LayerTableRecord layer) =>
        layer.Name.Equals("0", StringComparison.OrdinalIgnoreCase) ||
        layer.Name.Equals("DEFPOINTS", StringComparison.OrdinalIgnoreCase) ||
        layer.IsDependent || layer.Name.Contains('|');

    private static bool IsGeneratedDimensionBlock(BlockTableRecord block) =>
        block.IsAnonymous && block.Name.StartsWith("*D", StringComparison.OrdinalIgnoreCase);

    private static bool IsDimensionSignature(string signature)
    {
        var firstSeparator = signature.IndexOf('|');
        if (firstSeparator < 0) return false;
        var secondSeparator = signature.IndexOf('|', firstSeparator + 1);
        return secondSeparator > firstSeparator && signature.Substring(firstSeparator + 1,
            secondSeparator - firstSeparator - 1).Equals("DIMENSION", StringComparison.OrdinalIgnoreCase);
    }

    private static MappingDocument ReadMapping(string path)
    {
        using var json = JsonDocument.Parse(File.ReadAllBytes(path));
        var root = json.RootElement;
        var hash = root.TryGetProperty("source_sha256", out var hashNode) ? hashNode.GetString() : null;
        if (string.IsNullOrWhiteSpace(hash)) throw new InvalidDataException("Mapping is missing source_sha256.");
        JsonElement rows;
        if (!root.TryGetProperty("actions", out rows) && !root.TryGetProperty("mappings", out rows))
            throw new InvalidDataException("Mapping must contain actions or mappings.");
        if (rows.ValueKind != JsonValueKind.Array) throw new InvalidDataException("Mapping rows must be an array.");
        var actions = new List<MappingAction>();
        foreach (var row in rows.EnumerateArray())
        {
            var source = row.TryGetProperty("source", out var sourceNode) ? sourceNode.GetString() : null;
            var decision = row.TryGetProperty("decision", out var decisionNode) ? decisionNode.GetString() : null;
            var target = row.TryGetProperty("target", out var targetNode) && targetNode.ValueKind != JsonValueKind.Null
                ? targetNode.GetString() : null;
            var reason = row.TryGetProperty("reason", out var reasonNode) ? reasonNode.GetString() ?? "human_review" : "human_review";
            if (string.IsNullOrWhiteSpace(source) || string.IsNullOrWhiteSpace(decision))
                throw new InvalidDataException("Every mapping row requires source and decision.");
            actions.Add(new MappingAction(source, decision, target, reason));
        }
        if (actions.GroupBy(x => x.Source, StringComparer.OrdinalIgnoreCase).Any(group => group.Count() != 1))
            throw new InvalidDataException("Mapping contains duplicate source layers.");
        var entityActions = new List<EntityAction>();
        if (root.TryGetProperty("entity_actions", out var objectRows))
            foreach (var row in objectRows.EnumerateArray())
                entityActions.Add(new EntityAction(row.GetProperty("handle").GetString()!,
                    row.GetProperty("source").GetString()!, row.GetProperty("type").GetString()!,
                    row.GetProperty("context").GetString()!, row.GetProperty("decision").GetString()!,
                    row.TryGetProperty("target", out var t) && t.ValueKind != JsonValueKind.Null ? t.GetString() : null));
        if (entityActions.Any(x => string.IsNullOrWhiteSpace(x.Handle)) ||
            entityActions.GroupBy(x => x.Handle, StringComparer.OrdinalIgnoreCase).Any(g => g.Count() != 1))
            throw new InvalidDataException("Duplicate or empty object handle.");
        return new MappingDocument(hash, actions, entityActions);
    }

    private static StandardDocument ReadStandard(string path)
    {
        using var json = JsonDocument.Parse(File.ReadAllBytes(path));
        var root = json.RootElement;
        if (!root.TryGetProperty("layers", out var rows) || rows.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("Standard must contain a layers array.");
        var layers = new List<StandardLayer>();
        foreach (var row in rows.EnumerateArray())
        {
            var name = row.GetProperty("name").GetString();
            if (string.IsNullOrWhiteSpace(name)) throw new InvalidDataException("Standard layer name is empty.");
            var aci = row.TryGetProperty("aci", out var aciNode) ? aciNode.GetInt32() : 7;
            var linetype = row.TryGetProperty("linetype", out var typeNode) ? typeNode.GetString() ?? "Continuous" : "Continuous";
            var lineweight = row.TryGetProperty("lineweight", out var weightNode) ? weightNode.GetInt32() : -3;
            var plot = !row.TryGetProperty("plot", out var plotNode) || plotNode.GetBoolean();
            if (aci is < 1 or > 255) throw new InvalidDataException($"Invalid ACI for {name}: {aci}");
            layers.Add(new StandardLayer(name, aci, linetype, lineweight, plot));
        }
        if (layers.Count == 0) throw new InvalidDataException("Standard has no layers.");
        if (layers.GroupBy(x => x.Name, StringComparer.OrdinalIgnoreCase).Any(group => group.Count() != 1))
            throw new InvalidDataException("Standard contains duplicate layer names.");
        var nameValue = root.TryGetProperty("name", out var standardName) ? standardName.GetString() ?? "Unnamed Standard" : "Unnamed Standard";
        var version = root.TryGetProperty("version", out var versionNode) ? versionNode.GetString() ?? "unknown" : "unknown";
        return new StandardDocument(nameValue, version, layers);
    }

    private static WriteExpectation ReadExpectation(string path)
    {
        using var json = JsonDocument.Parse(File.ReadAllBytes(path));
        var root = json.RootElement;
        var expectedLayers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var property in root.GetProperty("expected_layers").EnumerateObject())
            expectedLayers[property.Name] = property.Value.GetString()
                ?? throw new InvalidDataException($"Expected layer is null for handle {property.Name}.");
        var expectedNonLayerSignatures = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (root.TryGetProperty("expected_non_layer_signatures", out var signatureRows))
            foreach (var property in signatureRows.EnumerateObject())
                expectedNonLayerSignatures[property.Name] = property.Value.GetString()
                    ?? throw new InvalidDataException($"Expected signature is null for handle {property.Name}.");
        var generatedDimensionBlockSignatures = root.TryGetProperty("generated_dimension_block_non_layer_signatures", out var generatedRows)
            ? generatedRows.EnumerateArray().Select(item => item.GetString()
                ?? throw new InvalidDataException("Generated dimension block signature is null.")).ToList()
            : new List<string>();
        var mappedByContext = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        if (root.TryGetProperty("mapped_by_context", out var contexts))
            foreach (var property in contexts.EnumerateObject()) mappedByContext[property.Name] = property.Value.GetInt32();
        var targets = root.GetProperty("used_targets").EnumerateArray()
            .Select(item => item.GetString())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Cast<string>()
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var removedSources = root.TryGetProperty("removed_source_layers", out var removedRows)
            ? removedRows.EnumerateArray().Select(item => item.GetString())
                .Where(item => !string.IsNullOrWhiteSpace(item)).Cast<string>()
                .ToHashSet(StringComparer.OrdinalIgnoreCase)
            : new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        return new WriteExpectation(
            root.GetProperty("source_sha256").GetString() ?? throw new InvalidDataException("Expectation has no source hash."),
            root.GetProperty("entity_count").GetInt32(),
            root.GetProperty("non_layer_sha256").GetString() ?? throw new InvalidDataException("Expectation has no signature."),
            root.TryGetProperty("generated_dimension_block_entity_count", out var generatedEntityCount)
                ? generatedEntityCount.GetInt32() : 0,
            root.TryGetProperty("generated_dimension_block_non_layer_sha256", out var generatedSha)
                ? generatedSha.GetString() ?? ComputeTextSha256(string.Empty) : ComputeTextSha256(string.Empty),
            generatedDimensionBlockSignatures,
            expectedLayers,
            expectedNonLayerSignatures,
            root.GetProperty("mapped_entities").GetInt32(),
            mappedByContext,
            root.GetProperty("xref_entities_preserved").GetInt32(),
            root.TryGetProperty("generated_dimension_block_entities_expected", out var generatedCount)
                ? generatedCount.GetInt32() : 0,
            targets,
            removedSources,
            root.GetProperty("dwg_version").GetString() ?? "unknown");
    }

    private static DwsExpectation ReadDwsExpectation(string path)
    {
        using var json = JsonDocument.Parse(File.ReadAllBytes(path));
        var root = json.RootElement;
        var expectedLayers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var property in root.GetProperty("expected_layers").EnumerateObject())
            expectedLayers[property.Name] = property.Value.GetString()
                ?? throw new InvalidDataException($"DWS expected layer is null for handle {property.Name}.");
        var expectedSignatures = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var property in root.GetProperty("expected_non_layer_signatures").EnumerateObject())
            expectedSignatures[property.Name] = property.Value.GetString()
                ?? throw new InvalidDataException($"DWS expected signature is null for handle {property.Name}.");
        var targets = root.GetProperty("used_targets").EnumerateArray()
            .Select(value => value.GetString())
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Cast<string>()
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var removedSources = root.TryGetProperty("removed_source_layers", out var removedRows)
            ? removedRows.EnumerateArray().Select(value => value.GetString())
                .Where(value => !string.IsNullOrWhiteSpace(value)).Cast<string>()
                .ToHashSet(StringComparer.OrdinalIgnoreCase)
            : new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        return new DwsExpectation(
            root.GetProperty("source_sha256").GetString()
                ?? throw new InvalidDataException("DWS expectation has no source hash."),
            root.GetProperty("dws_sha256").GetString()
                ?? throw new InvalidDataException("DWS expectation has no DWS hash."),
            root.GetProperty("entity_count").GetInt32(),
            root.GetProperty("non_layer_sha256").GetString()
                ?? throw new InvalidDataException("DWS expectation has no content signature."),
            expectedLayers,
            expectedSignatures,
            root.GetProperty("mapped_entities").GetInt32(),
            root.GetProperty("mapped_sources").GetInt32(),
            targets,
            removedSources);
    }

    private static string RequireExistingFile(string variable)
    {
        var value = Environment.GetEnvironmentVariable(variable);
        if (string.IsNullOrWhiteSpace(value) || !File.Exists(value))
            throw new InvalidOperationException($"{variable} must identify an existing file.");
        return Path.GetFullPath(value);
    }

    private static string RequireNewPath(string variable, string extension)
    {
        var value = Environment.GetEnvironmentVariable(variable);
        if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException($"{variable} is required.");
        var full = Path.GetFullPath(value);
        if (!Path.GetExtension(full).Equals(extension, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"{variable} must end with {extension}.");
        if (File.Exists(full)) throw new IOException($"Refusing to overwrite output: {full}");
        Directory.CreateDirectory(Path.GetDirectoryName(full) ?? throw new InvalidOperationException("Output has no parent."));
        return full;
    }

    private static string ComputeSha256(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        return Convert.ToHexString(SHA256.HashData(stream));
    }

    private static string ComputeTextSha256(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));

    private static void AppendPoint(StringBuilder builder, Point3d point) => builder
        .Append(NormalizeCoordinate(point.X)).Append(',')
        .Append(NormalizeCoordinate(point.Y)).Append(',')
        .Append(NormalizeCoordinate(point.Z)).Append('|');

    private static string NormalizeCoordinate(double value)
    {
        // AutoCAD can rewrite calculated Dimension extents by a few IEEE-754 ULPs
        // during Save/Reopen. A 1e-6 drawing-unit grid rejects material geometry
        // changes while remaining stable across that native serialization noise.
        var rounded = Math.Round(value, 6, MidpointRounding.AwayFromZero);
        if (rounded == 0d) rounded = 0d; // canonicalize negative zero
        return rounded.ToString("0.######", CultureInfo.InvariantCulture);
    }

    private static void WriteJsonNew(string path, object value)
    {
        using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        JsonSerializer.Serialize(stream, value, new JsonSerializerOptions { WriteIndented = true });
        stream.WriteByte((byte)'\n');
    }

    private static void DeleteIfPresent(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); }
        catch { /* Retain the original exception. */ }
    }

    private static void Trace(string message)
    {
        var path = Environment.GetEnvironmentVariable("LAYERLAB_TRACE_OUT");
        if (string.IsNullOrWhiteSpace(path)) return;
        try { File.AppendAllText(path, $"{DateTimeOffset.Now:O} {message}{Environment.NewLine}"); }
        catch { /* Diagnostics must not change conversion behavior. */ }
    }

    private sealed record MappingAction(string Source, string Decision, string? Target, string Reason);
    private sealed record EntityAction(string Handle, string Source, string Type, string Context, string Decision, string? Target);
    private sealed record MappingDocument(string SourceSha256, List<MappingAction> Actions, List<EntityAction> EntityActions);
    private sealed record StandardLayer(string Name, int Aci, string Linetype, int Lineweight, bool Plot);
    private sealed record StandardDocument(string Name, string Version, List<StandardLayer> Layers);
    private sealed record LayerInfo(string Name, bool Protected);
    private sealed record EntitySnapshot(string Layer, string NonLayerSignature);
    private sealed record DatabaseSnapshot(int EntityCount, string NonLayerSha256,
        Dictionary<string, EntitySnapshot> Entities, int GeneratedDimensionBlockEntityCount,
        string GeneratedDimensionBlockSha256, List<string> GeneratedDimensionBlockSignatures);
    private sealed record ExecutionResult(int MappedEntities, Dictionary<string, int> MappedByContext,
        int XrefEntitiesPreserved, int GeneratedDimensionBlockEntitiesExpected,
        string ExpectedGeneratedDimensionBlockSha256, List<string> ExpectedGeneratedDimensionBlockSignatures,
        HashSet<string> UsedTargets, HashSet<string> RemovedSourceLayers,
        Dictionary<string, string> ExpectedLayers);
    private sealed record WriteExpectation(string SourceSha256, int EntityCount, string NonLayerSha256,
        int GeneratedDimensionBlockEntityCount, string GeneratedDimensionBlockSha256,
        List<string> GeneratedDimensionBlockSignatures,
        Dictionary<string, string> ExpectedLayers, Dictionary<string, string> ExpectedNonLayerSignatures,
        int MappedEntities, Dictionary<string, int> MappedByContext,
        int XrefEntitiesPreserved, int GeneratedDimensionBlockEntitiesExpected,
        HashSet<string> UsedTargets, HashSet<string> RemovedSourceLayers, string DwgVersion);
    private sealed record DwsExpectation(string SourceSha256, string DwsSha256, int EntityCount,
        string NonLayerSha256, Dictionary<string, string> ExpectedLayers,
        Dictionary<string, string> ExpectedNonLayerSignatures, int MappedEntities,
        int MappedSources, HashSet<string> UsedTargets, HashSet<string> RemovedSourceLayers);
}
