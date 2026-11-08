using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcadApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

[assembly: CommandClass(typeof(LayerLab.NativeLayerExport.Commands))]

namespace LayerLab.NativeLayerExport;

public sealed class Commands
{
    private const int MaxGeometrySamplesPerLayer = 25;
    private const int MaxPolylinePoints = 200;
    private const int MaxAttributeValuesPerTag = 5;
    private const int MaxTextSamplesPerLayer = 20;

    private static readonly Dictionary<string, string[]> CategoryWords = new(StringComparer.OrdinalIgnoreCase)
    {
        ["wall"] = ["wall", "partition", "牆", "墙", "隔間", "隔断"],
        ["door"] = ["door", "門", "门"],
        ["window"] = ["window", "wind", "窗"],
        ["furniture"] = ["furn", "furniture", "sofa", "table", "chair", "家具"],
        ["annotation"] = ["annotation", "annot", "text", "dim", "title", "mark", "標註", "标注", "文字"],
        ["lighting"] = ["light", "lighting", "lamp", "燈", "灯"],
        ["column"] = ["column", "col", "柱"],
        ["hatch"] = ["hatch", "填充", "剖線", "剖线"],
        ["ceiling"] = ["ceiling", "rcp", "天花"],
        ["flooring"] = ["floor", "carpet", "地坪", "地板"],
        ["sanitary"] = ["sanitary", "toilet", "plumbing", "衛浴", "卫浴"],
        ["electrical"] = ["socket", "outlet", "electrical", "電氣", "电气", "插座"]
    };

    [CommandMethod("LAYERLABEXPORTSTANDARD", CommandFlags.Session)]
    public void ExportStandard()
    {
        var document = RequireDocument();
        var standardPath = RequireOutputPath("LAYERLAB_STANDARD_OUT");
        var auditPath = RequireOutputPath("LAYERLAB_AUDIT_OUT");
        var sourcePath = RequireSourcePath();
        var layers = new List<StandardLayer>();
        var excluded = new List<ExcludedLayer>();
        var warnings = new List<string>();

        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var layerTable = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
            foreach (ObjectId layerId in layerTable)
            {
                var layer = (LayerTableRecord)transaction.GetObject(layerId, OpenMode.ForRead);
                var protectedReason = GetProtectedReason(layer);
                if (protectedReason is not null)
                {
                    excluded.Add(new ExcludedLayer(layer.Name, protectedReason));
                    continue;
                }

                var colorIndex = (int)layer.Color.ColorIndex;
                if (colorIndex is < 1 or > 255)
                {
                    warnings.Add($"{layer.Name}: layer color is not ACI 1-255; candidate uses ACI 7.");
                    colorIndex = 7;
                }

                var lineType = (LinetypeTableRecord)transaction.GetObject(layer.LinetypeObjectId, OpenMode.ForRead);
                layers.Add(new StandardLayer(layer.Name, colorIndex, lineType.Name, (int)layer.LineWeight,
                    layer.IsPlottable, string.Empty, []));
            }
            transaction.Commit();
        }

        layers.Sort((left, right) => StringComparer.OrdinalIgnoreCase.Compare(left.name, right.name));
        excluded.Sort((left, right) => StringComparer.OrdinalIgnoreCase.Compare(left.name, right.name));
        var sourceHash = ComputeSha256(sourcePath);
        var standard = new
        {
            schema = "cad-layer-standard/v1",
            name = $"{Path.GetFileName(sourcePath)} derived candidate",
            version = DateTimeOffset.Now.ToString("yyyy-MM-dd-'draft'"),
            layers
        };
        var audit = new
        {
            schema = "cad-layer-standard-source-audit/v1",
            state = "AWAITING_USER_APPROVAL",
            source = new { path = Path.GetFullPath(sourcePath), filename = Path.GetFileName(sourcePath), sha256 = sourceHash,
                opened_by = "AutoCAD 2026 Core Console", read_only = true },
            standard_candidate = new { path = Path.GetFullPath(standardPath), layer_count = layers.Count,
                semantic_categories_inferred = false },
            excluded_layers = excluded,
            warnings,
            checked_at = DateTimeOffset.Now.ToString("O")
        };

        WriteJsonNew(standardPath, standard);
        WriteJsonNew(auditPath, audit);
        document.Editor.WriteMessage($"\nLAYERLAB_EXPORT_OK layers={layers.Count} sha256={sourceHash}");
    }

    [CommandMethod("LAYERLABSCANNATIVE", CommandFlags.Session)]
    public void ScanNative()
    {
        var document = RequireDocument();
        var sourcePath = RequireSourcePath();
        var inventoryPath = RequireOutputPath("LAYERLAB_INVENTORY_OUT");
        var standardPath = Environment.GetEnvironmentVariable("LAYERLAB_STANDARD_PATH");
        var suggestionsPath = string.IsNullOrWhiteSpace(standardPath)
            ? null
            : RequireOutputPath("LAYERLAB_SUGGESTIONS_OUT");

        if (!string.IsNullOrWhiteSpace(standardPath) && !File.Exists(standardPath))
            throw new InvalidOperationException("LAYERLAB_STANDARD_PATH does not identify an existing standard JSON.");

        var sourceHash = ComputeSha256(sourcePath);
        var rows = new Dictionary<string, LayerStats>(StringComparer.OrdinalIgnoreCase);
        var warnings = new List<string>();
        var totalModel = 0;
        var totalPaper = 0;
        var totalBlock = 0;
        var objects = new List<object>();

        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var layerTable = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
            foreach (ObjectId layerId in layerTable)
            {
                var layer = (LayerTableRecord)transaction.GetObject(layerId, OpenMode.ForRead);
                var lineType = (LinetypeTableRecord)transaction.GetObject(layer.LinetypeObjectId, OpenMode.ForRead);
                rows[layer.Name] = new LayerStats(layer.Name)
                {
                    Aci = NormalizeAci(layer.Color.ColorIndex),
                    TrueColor = GetTrueColor(layer.Color),
                    Linetype = lineType.Name,
                    Lineweight = (int)layer.LineWeight,
                    Plot = layer.IsPlottable,
                    Locked = layer.IsLocked,
                    Frozen = layer.IsFrozen,
                    Off = layer.IsOff,
                    ProtectedReason = GetProtectedReason(layer)
                };
            }

            var blockTable = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
            foreach (ObjectId blockId in blockTable)
            {
                var block = (BlockTableRecord)transaction.GetObject(blockId, OpenMode.ForRead);
                var context = GetContext(block, transaction);
                foreach (ObjectId entityId in block)
                {
                    if (!entityId.IsValid || entityId.IsErased) continue;
                    if (transaction.GetObject(entityId, OpenMode.ForRead, false) is not Entity entity) continue;
                    var layerName = string.IsNullOrWhiteSpace(entity.Layer) ? "0" : entity.Layer;
                    if (!rows.TryGetValue(layerName, out var row))
                    {
                        row = LayerStats.ForUndefined(layerName);
                        rows[layerName] = row;
                    }

                    row.AddEntity(entity, context, block.Name, transaction);
                    objects.Add(ObjectEvidence(entity, context, block, transaction, row.ProtectedReason));
                    switch (context)
                    {
                        case EntityContext.Model: totalModel++; break;
                        case EntityContext.Paper: totalPaper++; break;
                        default: totalBlock++; break;
                    }
                }
            }
            transaction.Commit();
        }

        var layerRows = rows.Values
            .OrderByDescending(row => row.Total > 0)
            .ThenBy(row => row.Name, StringComparer.OrdinalIgnoreCase)
            .Select(row => row.ToContract())
            .ToList();

        if (totalBlock > 0) warnings.Add("圖塊定義內物件已盤點；核准後原生 writer 可映射可編輯圖塊定義，XREF 內容維持保護。");
        if (rows.Values.Any(row => row.OverrideCount > 0)) warnings.Add("存在物件顏色、線型或線寬覆寫；未將其強制改成 ByLayer。");
        if (rows.Values.Any(row => row.AttributeTags.Count > 0)) warnings.Add("圖塊屬性值僅保留每個標籤最多 5 個、每值最多 120 字元的本機審核樣本。");
        if (rows.Values.Any(row => row.Undefined)) warnings.Add("部分物件引用圖層表中不存在的圖層。");

        var inventory = new
        {
            schema = "cad-layer-inventory/v1",
            job_id = Environment.GetEnvironmentVariable("LAYERLAB_JOB_ID") ?? Guid.NewGuid().ToString(),
            filename = Path.GetFileName(sourcePath),
            filetype = Path.GetExtension(sourcePath).TrimStart('.').ToUpperInvariant(),
            sha256 = sourceHash,
            can_convert = true,
            engine = new { application = "AutoCAD 2026", method = "native .NET content scanner",
                native_write_status = "VERIFIED_SAVE_REOPEN" },
            layers = layerRows,
            objects,
            total_entities = totalModel + totalPaper + totalBlock,
            totals = new { model = totalModel, paper = totalPaper, block = totalBlock },
            warnings,
            checked_at = DateTimeOffset.Now.ToString("O")
        };
        WriteJsonNew(inventoryPath, inventory);

        if (standardPath is not null && suggestionsPath is not null)
        {
            var standard = ReadStandard(standardPath);
            var suggestions = BuildSuggestions(rows.Values, standard);
            var suggestionDocument = new
            {
                schema = "cad-layer-suggestions/v1",
                state = "AWAITING_USER_APPROVAL",
                inventory_sha256 = sourceHash,
                standard = new { standard.Name, standard.Version, source_path = Path.GetFullPath(standardPath) },
                scoring_policy = new { content_evidence_preferred = true, exact_name_is_not_approval = true,
                    inferred_target_categories_are_low_confidence = true, candidate_limit_per_source = 5 },
                suggestions
            };
            WriteJsonNew(suggestionsPath, suggestionDocument);
        }

        document.Editor.WriteMessage(
            $"\nLAYERLAB_SCAN_OK layers={layerRows.Count} model={totalModel} paper={totalPaper} block={totalBlock} sha256={sourceHash}");
    }

    private static List<object> BuildSuggestions(IEnumerable<LayerStats> rows, StandardDocument standard)
    {
        var results = new List<object>();
        foreach (var row in rows.OrderByDescending(item => item.Total > 0).ThenBy(item => item.Name, StringComparer.OrdinalIgnoreCase))
        {
            if (row.ProtectedReason is not null)
            {
                results.Add(new { source = row.Name, status = "protected", category = "protected", confidence = 1.0,
                    suggested_target = (string?)null, reason = row.ProtectedReason, candidates = Array.Empty<object>() });
                continue;
            }

            var sourceEvidence = GuessSourceCategory(row);
            var candidates = standard.Layers.Select(target => ScoreCandidate(row, sourceEvidence, target))
                .OrderByDescending(candidate => candidate.Score)
                .ThenBy(candidate => candidate.Target, StringComparer.OrdinalIgnoreCase)
                .Take(5)
                .ToList();
            var best = candidates.FirstOrDefault();
            var tied = best is not null && best.Score >= 0.25 && !best.ExactOrAlias &&
                candidates.Skip(1).Any(candidate => best.Score - candidate.Score <= 0.02);
            var suggested = best is not null && best.Score >= 0.25 && !tied ? best.Target : null;
            var confidence = best is null ? 0 : Math.Round(best.Score, 2);
            var reason = row.Total == 0
                ? "空白圖層，沒有物件內容證據；候選僅供人工檢視。"
                : tied
                    ? $"多個候選分數並列（最高 {Math.Round(best!.Score, 2)}）；保留為人工判讀，不自動指定單一目標。{best.Reason}"
                    : best?.Reason ?? sourceEvidence.Reason;
            results.Add(new
            {
                source = row.Name,
                status = tied ? "ambiguous" : suggested is null ? "unknown" : "suggested",
                category = sourceEvidence.Category,
                confidence,
                suggested_target = suggested,
                reason,
                evidence = new { sourceEvidence.Reason, counts = new { model = row.ModelCount, paper = row.PaperCount, block = row.BlockCount },
                    types = row.Types, block_names = row.BlockNames, attribute_tags = row.AttributeTags, override_count = row.OverrideCount },
                candidates = candidates.Select(candidate => new { target = candidate.Target, confidence = Math.Round(candidate.Score, 2),
                    reason = candidate.Reason, target_category = candidate.TargetCategory,
                    target_category_source = candidate.TargetCategorySource, exact_name_or_alias = candidate.ExactOrAlias }).ToList()
            });
        }
        return results;
    }

    private static CandidateScore ScoreCandidate(LayerStats row, CategoryEvidence source, StandardTarget target)
    {
        var normalizedSource = Normalize(row.Name);
        var normalizedTarget = Normalize(target.Name);
        var exact = normalizedSource.Length > 0 && normalizedSource == normalizedTarget;
        var alias = target.Aliases.Any(value => Normalize(value) == normalizedSource);
        var similarity = Similarity(normalizedSource, normalizedTarget);
        var score = exact || alias ? 0.95 : Math.Min(0.42, similarity * 0.42);
        var targetEvidence = GuessTargetCategory(target);
        var categoryMatch = source.Category != "unknown" && source.Category == targetEvidence.Category;

        if (categoryMatch)
        {
            var contentScore = source.Confidence * 0.78;
            if (targetEvidence.Source == "name_inference") contentScore = Math.Min(contentScore, 0.58);
            score = Math.Max(score, contentScore);
        }
        else if (source.Category != "unknown" && targetEvidence.Source == "explicit") score *= 0.55;
        if (row.Total == 0) score = exact || alias ? 0.55 : Math.Min(score, 0.18);

        var reasonParts = new List<string>();
        if (exact || alias) reasonParts.Add("標準圖層名稱或別名完全符合（仍需人工核准）");
        else if (categoryMatch)
            reasonParts.Add(targetEvidence.Source == "explicit" ? "來源內容類別與標準明確類別相符"
                : "來源內容類別與由目標名稱推測的類別相符；目標端語意尚未核准");
        else reasonParts.Add("僅有圖層名稱相似度或弱證據");
        reasonParts.Add(source.Reason);
        return new CandidateScore(target.Name, Math.Clamp(score, 0, 1), string.Join("；", reasonParts),
            targetEvidence.Category, targetEvidence.Source, exact || alias);
    }

    private static CategoryEvidence GuessSourceCategory(LayerStats row)
    {
        var total = row.Types.Values.Sum();
        if (total > 0)
        {
            var annotation = SumTypes(row.Types, "TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER", "MULTILEADER");
            if ((double)annotation / total >= 0.8)
                return new CategoryEvidence("annotation", "80% 以上內容為文字或標註物件", 0.85, "content");
            var hatch = SumTypes(row.Types, "HATCH");
            if ((double)hatch / total >= 0.85)
                return new CategoryEvidence("hatch", "85% 以上內容為填充線物件", 0.78, "content");
        }

        var blockReferenceCount = SumTypes(row.Types, "INSERT");
        var blockShare = total == 0 ? 0 : (double)blockReferenceCount / total;
        var blockEvidence = InferCategory(string.Join(" ", row.BlockNames.Keys.Concat(row.AttributeTags.Keys)));
        if (blockEvidence is not null && blockReferenceCount > 0 && (blockShare >= 0.50 || total <= 5))
            return new CategoryEvidence(blockEvidence,
                $"圖塊參考占此層內容 {blockShare:P0}，圖塊名稱或屬性標籤提供用途線索，尚未驗證幾何",
                0.70, "block_evidence");
        var nameEvidence = InferCategory(row.Name);
        if (nameEvidence is not null)
            return new CategoryEvidence(nameEvidence, "僅由來源圖層名稱推測，不能視為物件辨識", 0.32, "name_inference");
        return new CategoryEvidence("unknown", "物件與幾何證據不足以判定用途", 0, "unknown");
    }

    private static CategoryEvidence GuessTargetCategory(StandardTarget target)
    {
        if (!string.IsNullOrWhiteSpace(target.Category))
            return new CategoryEvidence(target.Category.Trim().ToLowerInvariant(), "標準明確類別", 1, "explicit");
        var inferred = InferCategory(string.Join(" ", new[] { target.Name }.Concat(target.Aliases)));
        return inferred is null ? new CategoryEvidence("unknown", "目標未提供類別", 0, "unknown")
            : new CategoryEvidence(inferred, "由目標名稱推測", 0.32, "name_inference");
    }

    private static string? InferCategory(string text)
    {
        foreach (var pair in CategoryWords)
            if (pair.Value.Any(term => text.Contains(term, StringComparison.OrdinalIgnoreCase))) return pair.Key;
        return null;
    }

    private static int SumTypes(Dictionary<string, int> values, params string[] names) =>
        names.Sum(name => values.TryGetValue(name, out var count) ? count : 0);

    private static StandardDocument ReadStandard(string path)
    {
        using var stream = File.OpenRead(path);
        using var json = JsonDocument.Parse(stream);
        var root = json.RootElement;
        if (!root.TryGetProperty("layers", out var layerArray) || layerArray.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("Standard JSON must contain a layers array.");
        var layers = new List<StandardTarget>();
        foreach (var item in layerArray.EnumerateArray())
        {
            var name = item.GetProperty("name").GetString();
            if (string.IsNullOrWhiteSpace(name)) continue;
            var category = item.TryGetProperty("category", out var categoryValue) ? categoryValue.GetString() ?? "" : "";
            var aliases = item.TryGetProperty("aliases", out var aliasArray) && aliasArray.ValueKind == JsonValueKind.Array
                ? aliasArray.EnumerateArray().Select(value => value.GetString()).Where(value => !string.IsNullOrWhiteSpace(value)).Cast<string>().ToArray()
                : [];
            layers.Add(new StandardTarget(name, category, aliases));
        }
        if (layers.Count == 0) throw new InvalidDataException("Standard JSON has no usable layers.");
        var standardName = root.TryGetProperty("name", out var nameValue) ? nameValue.GetString() ?? "Unnamed Standard" : "Unnamed Standard";
        var version = root.TryGetProperty("version", out var versionValue) ? versionValue.GetString() ?? "unknown" : "unknown";
        return new StandardDocument(standardName, version, layers);
    }

    private static EntityContext GetContext(BlockTableRecord block, Transaction transaction)
    {
        if (!block.IsLayout) return EntityContext.Block;
        var layout = (Layout)transaction.GetObject(block.LayoutId, OpenMode.ForRead);
        return layout.ModelType ? EntityContext.Model : EntityContext.Paper;
    }

    private static string Normalize(string value) => Regex.Replace(value.ToLowerInvariant(), "[^a-z0-9\\u4e00-\\u9fff]", string.Empty);

    private static double Similarity(string left, string right)
    {
        if (left == right) return 1;
        if (left.Length == 0 || right.Length == 0) return 0;
        var previous = Enumerable.Range(0, right.Length + 1).ToArray();
        for (var i = 1; i <= left.Length; i++)
        {
            var current = new int[right.Length + 1];
            current[0] = i;
            for (var j = 1; j <= right.Length; j++)
            {
                var cost = left[i - 1] == right[j - 1] ? 0 : 1;
                current[j] = Math.Min(Math.Min(current[j - 1] + 1, previous[j] + 1), previous[j - 1] + cost);
            }
            previous = current;
        }
        return 1.0 - (double)previous[^1] / Math.Max(left.Length, right.Length);
    }

    private static int NormalizeAci(short colorIndex) => colorIndex is >= 1 and <= 255 ? colorIndex : 7;
    private static int? GetTrueColor(Color color) => color.ColorMethod == ColorMethod.ByColor ? (color.Red << 16) | (color.Green << 8) | color.Blue : null;
    private static Document RequireDocument() => AcadApplication.DocumentManager.MdiActiveDocument ?? throw new InvalidOperationException("No active AutoCAD document.");

    private static string RequireSourcePath()
    {
        var sourcePath = Environment.GetEnvironmentVariable("LAYERLAB_SOURCE_PATH");
        if (string.IsNullOrWhiteSpace(sourcePath) || !File.Exists(sourcePath))
            throw new InvalidOperationException("LAYERLAB_SOURCE_PATH does not identify an existing source file.");
        return Path.GetFullPath(sourcePath);
    }

    private static string? GetProtectedReason(LayerTableRecord layer)
    {
        if (layer.Name.Equals("0", StringComparison.OrdinalIgnoreCase)) return "system_layer_0";
        if (layer.Name.Equals("DEFPOINTS", StringComparison.OrdinalIgnoreCase)) return "system_layer_defpoints";
        if (layer.IsDependent || layer.Name.Contains('|')) return "xref_dependent";
        return null;
    }

    private static string RequireOutputPath(string variableName)
    {
        var value = Environment.GetEnvironmentVariable(variableName);
        if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException($"{variableName} is required.");
        var fullPath = Path.GetFullPath(value);
        if (File.Exists(fullPath)) throw new IOException($"Refusing to overwrite existing output: {fullPath}");
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath) ?? throw new InvalidOperationException($"Output has no parent directory: {fullPath}"));
        return fullPath;
    }

    private static void WriteJsonNew(string path, object value)
    {
        var options = new JsonSerializerOptions { WriteIndented = true };
        using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        JsonSerializer.Serialize(stream, value, options);
        stream.WriteByte((byte)'\n');
    }

    private static string ComputeSha256(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        return Convert.ToHexString(SHA256.HashData(stream));
    }

    private sealed class LayerStats(string name)
    {
        public string Name { get; } = name;
        public int Aci { get; init; } = 7;
        public int? TrueColor { get; init; }
        public string Linetype { get; init; } = "UNKNOWN";
        public int Lineweight { get; init; } = -3;
        public bool Plot { get; init; } = true;
        public bool Locked { get; init; }
        public bool Frozen { get; init; }
        public bool Off { get; init; }
        public string? ProtectedReason { get; init; }
        public bool Undefined { get; init; }
        public int ModelCount { get; private set; }
        public int PaperCount { get; private set; }
        public int BlockCount { get; private set; }
        public int OverrideCount { get; private set; }
        public int Total => ModelCount + PaperCount + BlockCount;
        public Dictionary<string, int> Types { get; } = new(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, int> BlockNames { get; } = new(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, int> AttributeTags { get; } = new(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, List<string>> AttributeValueSamples { get; } = new(StringComparer.OrdinalIgnoreCase);
        public List<string> TextValueSamples { get; } = [];
        public List<Dictionary<string, object?>> Preview { get; } = [];
        public Dictionary<string, int> BlockDefinitionNames { get; } = new(StringComparer.OrdinalIgnoreCase);
        public static LayerStats ForUndefined(string name) => new(name) { Undefined = true };

        public void AddEntity(Entity entity, EntityContext context, string containingBlock, Transaction transaction)
        {
            switch (context)
            {
                case EntityContext.Model: ModelCount++; break;
                case EntityContext.Paper: PaperCount++; break;
                default: BlockCount++; Increment(BlockDefinitionNames, containingBlock); break;
            }
            var typeName = entity.GetRXClass().DxfName.ToUpperInvariant();
            Increment(Types, typeName);
            if (HasOverride(entity)) OverrideCount++;
            if (entity is BlockReference blockReference) AddBlockReference(blockReference, transaction);
            AddTextSample(entity);
            if (context == EntityContext.Model && Preview.Count < MaxGeometrySamplesPerLayer)
            {
                var sample = SampleGeometry(entity);
                if (sample is not null) Preview.Add(sample);
            }
        }

        private void AddBlockReference(BlockReference blockReference, Transaction transaction)
        {
            var definitionId = blockReference.IsDynamicBlock ? blockReference.DynamicBlockTableRecord : blockReference.BlockTableRecord;
            var definition = (BlockTableRecord)transaction.GetObject(definitionId, OpenMode.ForRead);
            Increment(BlockNames, definition.Name);
            foreach (ObjectId attributeId in blockReference.AttributeCollection)
            {
                if (!attributeId.IsValid || attributeId.IsErased) continue;
                if (transaction.GetObject(attributeId, OpenMode.ForRead, false) is not AttributeReference attribute) continue;
                var tag = string.IsNullOrWhiteSpace(attribute.Tag) ? "(untagged)" : attribute.Tag;
                Increment(AttributeTags, tag);
                if (!AttributeValueSamples.TryGetValue(tag, out var values))
                {
                    values = [];
                    AttributeValueSamples[tag] = values;
                }
                var value = (attribute.TextString ?? string.Empty).Replace("\r", " ").Replace("\n", " ").Trim();
                if (value.Length > 120) value = value[..120];
                if (value.Length > 0 && values.Count < MaxAttributeValuesPerTag && !values.Contains(value, StringComparer.Ordinal)) values.Add(value);
            }
        }

        public object ToContract() => new
        {
            name = Name, color = new { aci = Aci, true_color = TrueColor }, linetype = Linetype, lineweight = Lineweight,
            plot = Plot, locked = Locked, frozen = Frozen, off = Off,
            counts = new { model = ModelCount, paper = PaperCount, block = BlockCount }, total = Total,
            types = Types, override_count = OverrideCount, block_names = BlockNames,
            block_definition_names = BlockDefinitionNames, attribute_tags = AttributeTags,
            attribute_value_samples = AttributeValueSamples, text_value_samples = TextValueSamples, preview = Preview,
            @protected = ProtectedReason is not null, protected_reason = ProtectedReason, undefined = Undefined
        };

        private static void Increment(Dictionary<string, int> values, string key) => values[key] = values.TryGetValue(key, out var count) ? count + 1 : 1;

        private void AddTextSample(Entity entity)
        {
            string? value = entity switch
            {
                DBText text => text.TextString,
                MText text => text.Contents,
                Dimension dimension => dimension.DimensionText,
                _ => null
            };
            if (string.IsNullOrWhiteSpace(value) || value == "<>") return;
            value = value.Replace("\\P", " ").Replace("\r", " ").Replace("\n", " ").Trim();
            if (value.Length > 160) value = value[..160];
            if (TextValueSamples.Count < MaxTextSamplesPerLayer && !TextValueSamples.Contains(value, StringComparer.Ordinal))
                TextValueSamples.Add(value);
        }
    }

    private static object ObjectEvidence(Entity entity, EntityContext context, BlockTableRecord owner,
        Transaction transaction, string? protectedReason)
    {
        string? blockName = null;
        var attributes = new Dictionary<string, string>();
        if (entity is BlockReference reference)
        {
            var definition = (BlockTableRecord)transaction.GetObject(reference.IsDynamicBlock
                ? reference.DynamicBlockTableRecord : reference.BlockTableRecord, OpenMode.ForRead);
            blockName = definition.Name;
            foreach (ObjectId id in reference.AttributeCollection)
                if (id.IsValid && !id.IsErased && transaction.GetObject(id, OpenMode.ForRead) is AttributeReference a)
                    attributes[a.Tag] = (a.TextString ?? "")[..Math.Min(160, (a.TextString ?? "").Length)];
        }
        string? text = entity switch { DBText t => t.TextString, MText t => t.Contents,
            Dimension d => d.DimensionText, _ => null };
        if (text?.Length > 200) text = text[..200];
        double[]? bounds = null;
        try { var b = entity.GeometricExtents; bounds = [b.MinPoint.X, b.MinPoint.Y, b.MaxPoint.X, b.MaxPoint.Y]; }
        catch (Autodesk.AutoCAD.Runtime.Exception) { }
        return new { handle = entity.Handle.ToString(), source = entity.Layer,
            type = entity.GetRXClass().DxfName, context = context.ToString().ToLowerInvariant(),
            owner = owner.Name, block_name = blockName, attributes, text, bounds,
            geometry = context == EntityContext.Block ? null : SampleGeometry(entity),
            protected_reason = protectedReason ?? (owner.IsFromExternalReference || owner.IsFromOverlayReference ? "xref_definition" : null),
            color = entity.Color.ColorIndex, linetype = entity.Linetype, lineweight = (int)entity.LineWeight };
    }

    private static bool HasOverride(Entity entity) => !entity.Color.IsByLayer ||
        !entity.Linetype.Equals("ByLayer", StringComparison.OrdinalIgnoreCase) || entity.LineWeight != LineWeight.ByLayer;

    private static Dictionary<string, object?>? SampleGeometry(Entity entity)
    {
        switch (entity)
        {
            case Line line:
                return new Dictionary<string, object?> { ["kind"] = "line", ["points"] = new[] { Point(line.StartPoint), Point(line.EndPoint) } };
            case Polyline polyline:
            {
                var count = Math.Min(polyline.NumberOfVertices, MaxPolylinePoints);
                var points = new List<double[]>(count);
                for (var i = 0; i < count; i++) points.Add(Point(polyline.GetPoint2dAt(i)));
                return new Dictionary<string, object?> { ["kind"] = "polyline", ["points"] = points,
                    ["closed"] = polyline.Closed, ["truncated"] = polyline.NumberOfVertices > count,
                    ["has_bulges"] = Enumerable.Range(0, polyline.NumberOfVertices).Any(i => Math.Abs(polyline.GetBulgeAt(i)) > 1e-10) };
            }
            case Circle circle:
                return new Dictionary<string, object?> { ["kind"] = "circle", ["center"] = Point(circle.Center), ["radius"] = circle.Radius };
            case Arc arc:
                return new Dictionary<string, object?> { ["kind"] = "arc", ["center"] = Point(arc.Center), ["radius"] = arc.Radius,
                    ["start"] = arc.StartAngle * 180 / Math.PI, ["end"] = arc.EndAngle * 180 / Math.PI };
            default: return null;
        }
    }

    private static double[] Point(Point3d point) => [point.X, point.Y];
    private static double[] Point(Point2d point) => [point.X, point.Y];

    private enum EntityContext { Model, Paper, Block }
    private sealed record StandardLayer(string name, int aci, string linetype, int lineweight, bool plot, string category, string[] aliases);
    private sealed record ExcludedLayer(string name, string reason);
    private sealed record StandardTarget(string Name, string Category, string[] Aliases);
    private sealed record StandardDocument(string Name, string Version, List<StandardTarget> Layers);
    private sealed record CategoryEvidence(string Category, string Reason, double Confidence, string Source);
    private sealed record CandidateScore(string Target, double Score, string Reason, string TargetCategory, string TargetCategorySource, bool ExactOrAlias);
}
