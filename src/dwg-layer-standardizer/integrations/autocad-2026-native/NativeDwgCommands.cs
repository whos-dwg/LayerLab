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
            var reopened = CaptureDatabase(document.Database);
            Trace("validate:snapshot-captured");
            VerifyExpectedLayers(reopened, expectation.ExpectedLayers);
            Trace("validate:expected-layers-pass");
            VerifyTargetLayers(document.Database, standard, expectation.UsedTargets);
            Trace("validate:target-layers-pass");
            if (reopened.EntityCount != expectation.EntityCount)
                throw new InvalidOperationException($"Entity count changed after Save/Reopen ({expectation.EntityCount} -> {reopened.EntityCount}).");
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
            if (reopened.GeneratedDimensionBlockEntityCount != expectation.GeneratedDimensionBlockEntityCount)
                throw new InvalidOperationException("Generated dimension block entity count changed after Save/Reopen.");
            if (!reopened.GeneratedDimensionBlockSha256.Equals(expectation.GeneratedDimensionBlockSha256, StringComparison.Ordinal))
            {
                var beforeCounts = expectation.GeneratedDimensionBlockSignatures
                    .GroupBy(value => value, StringComparer.Ordinal)
                    .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
                var afterCounts = reopened.GeneratedDimensionBlockSignatures
                    .GroupBy(value => value, StringComparer.Ordinal)
                    .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
                foreach (var difference in beforeCounts
                    .Where(pair => !afterCounts.TryGetValue(pair.Key, out var count) || count != pair.Value)
                    .Take(20))
                {
                    afterCounts.TryGetValue(difference.Key, out var afterCount);
                    Trace($"generated-signature:missing-or-count-changed:before-count={difference.Value}:after-count={afterCount}:value={JsonSerializer.Serialize(difference.Key)}");
                }
                foreach (var difference in afterCounts
                    .Where(pair => !beforeCounts.TryGetValue(pair.Key, out var count) || count != pair.Value)
                    .Take(20))
                {
                    beforeCounts.TryGetValue(difference.Key, out var beforeCount);
                    Trace($"generated-signature:new-or-count-changed:before-count={beforeCount}:after-count={difference.Value}:value={JsonSerializer.Serialize(difference.Key)}");
                }
                throw new InvalidOperationException("Generated dimension block content signature changed after Save/Reopen.");
            }
            Trace("validate:generated-dimension-block-signature-pass");

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
                generated_block_policy = "verify_regenerated_dimension_anonymous_block_non_layer_content",
                entity_override_policy = "preserve",
                verification_policy = "independent_save_reopen_process",
                entity_count_before = expectation.EntityCount,
                entity_count_after = reopened.EntityCount,
                non_layer_signature_before = expectation.NonLayerSha256,
                non_layer_signature_after = reopened.NonLayerSha256,
                non_layer_signature_match = true,
                generated_dimension_block_entity_count_before = expectation.GeneratedDimensionBlockEntityCount,
                generated_dimension_block_entity_count_after = reopened.GeneratedDimensionBlockEntityCount,
                generated_dimension_block_non_layer_signature_before = expectation.GeneratedDimensionBlockSha256,
                generated_dimension_block_non_layer_signature_after = reopened.GeneratedDimensionBlockSha256,
                generated_dimension_block_non_layer_signature_match = true,
                mapped_entities = expectation.MappedEntities,
                mapped_by_context = expectation.MappedByContext,
                xref_entities_preserved = expectation.XrefEntitiesPreserved,
                generated_dimension_block_entities_verified = expectation.GeneratedDimensionBlockEntitiesExpected,
                target_layers_verified = expectation.UsedTargets.OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToArray(),
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
                block_definition_policy = "map_editable_preserve_xref_verify_regenerated_dimension_blocks",
                entity_property_override_policy = "preserve",
                actions = mapping.Actions,
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

    private static ExecutionResult ApplyMapping(Database database, MappingDocument mapping, StandardDocument standard,
        DatabaseSnapshot before)
    {
        Trace("apply:start");
        var sourceLayers = new Dictionary<string, LayerInfo>(StringComparer.OrdinalIgnoreCase);
        var standardLayers = standard.Layers.ToDictionary(x => x.Name, StringComparer.OrdinalIgnoreCase);
        var actions = mapping.Actions.ToDictionary(x => x.Source, StringComparer.OrdinalIgnoreCase);
        var usedTargets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
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
                if (!actions.TryGetValue(readEntity.Layer, out var action) ||
                    !action.Decision.Equals("map", StringComparison.OrdinalIgnoreCase) ||
                    string.IsNullOrWhiteSpace(action.Target) ||
                    action.Target.Equals(readEntity.Layer, StringComparison.OrdinalIgnoreCase)) continue;
                var entity = (Entity)transaction.GetObject(entityId, OpenMode.ForWrite, false, true);
                Trace($"apply:entity:{entity.Handle}:{readEntity.Layer}->{action.Target}");
                entity.Layer = action.Target;
                expectedLayers[entity.Handle.ToString()] = action.Target;
                mappedEntities++;
                mappedByContext[context] = mappedByContext.TryGetValue(context, out var count) ? count + 1 : 1;
            }
        }
        transaction.Commit();
        return new ExecutionResult(mappedEntities, mappedByContext, xrefEntitiesPreserved,
            generatedDimensionBlockEntitiesExpected, expectedGeneratedDimensionBlockSha256,
            expectedGeneratedDimensionBlockSignatures, usedTargets, expectedLayers);
    }

    private static void EnsureTargetLayers(Database database, Transaction transaction, IReadOnlyCollection<StandardLayer> targets)
    {
        var layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        var lineTypeTable = (LinetypeTable)transaction.GetObject(database.LinetypeTableId, OpenMode.ForRead);
        foreach (var target in targets)
        {
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

    private static DatabaseSnapshot CaptureDatabase(Database database)
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
        if (entity is not DBText && entity is not MText && entity is not BlockReference)
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
                builder.Append(text.TextString).Append('|');
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
                builder.Append(text.Contents).Append('|'); AppendPoint(builder, text.Location);
                builder.Append((int)text.Attachment).Append('|')
                    .Append(NormalizeCoordinate(text.TextHeight)).Append('|')
                    .Append(NormalizeCoordinate(text.Width)).Append('|')
                    .Append(NormalizeCoordinate(text.Rotation)).Append('|');
                break;
            case Dimension dimension:
                builder.Append(dimension.DimensionText).Append('|'); break;
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
        return new MappingDocument(hash, actions);
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
            root.GetProperty("dwg_version").GetString() ?? "unknown");
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
    private sealed record MappingDocument(string SourceSha256, List<MappingAction> Actions);
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
        HashSet<string> UsedTargets, Dictionary<string, string> ExpectedLayers);
    private sealed record WriteExpectation(string SourceSha256, int EntityCount, string NonLayerSha256,
        int GeneratedDimensionBlockEntityCount, string GeneratedDimensionBlockSha256,
        List<string> GeneratedDimensionBlockSignatures,
        Dictionary<string, string> ExpectedLayers, Dictionary<string, string> ExpectedNonLayerSignatures,
        int MappedEntities, Dictionary<string, int> MappedByContext,
        int XrefEntitiesPreserved, int GeneratedDimensionBlockEntitiesExpected,
        HashSet<string> UsedTargets, string DwgVersion);
}
