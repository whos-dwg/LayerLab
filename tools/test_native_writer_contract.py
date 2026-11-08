"""Static safety contracts for the staged native DWG writer."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/dwg-layer-standardizer/integrations/autocad-2026-native/NativeDwgCommands.cs"
SCRIPT = ROOT / "src/dwg-layer-standardizer/integrations/autocad-2026-native/run-native-convert.ps1"


class NativeWriterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.writer = cls.source.split('[CommandMethod("LAYERLABCONVERTNATIVE"', 1)[1].split(
            '[CommandMethod("LAYERLABVALIDATENATIVE"', 1
        )[0]
        after_writer = cls.source.split('[CommandMethod("LAYERLABVALIDATENATIVE"', 1)[1]
        cls.validator = after_writer.split('[CommandMethod("LAYERLABCLEANUPMAPPEDSOURCES"', 1)[0]
        cls.cleanup = cls.source.split('[CommandMethod("LAYERLABCLEANUPMAPPEDSOURCES"', 1)[1].split(
            '[CommandMethod("LAYERLABCREATEDWS"', 1
        )[0]
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_writer_does_not_rescan_mutated_database(self):
        self.assertEqual(self.writer.count("CaptureDatabase(database)"), 1)
        self.assertIn('Trace("verification:deferred-to-save-reopen")', self.writer)
        self.assertNotIn("snapshot:in-memory-after", self.writer)
        self.assertNotIn("database.Purge", self.writer)

    def test_reopen_process_remains_the_verification_authority(self):
        self.assertIn("var reopened = CaptureDatabase(document.Database);", self.validator)
        self.assertIn("VerifyExpectedLayers(reopened, expectation.ExpectedLayers);", self.validator)
        self.assertIn("VerifyTargetLayers(document.Database, standard, expectation.UsedTargets);", self.validator)
        self.assertIn("reopened.NonLayerSha256.Equals(expectation.NonLayerSha256", self.validator)

    def test_mapped_source_layers_are_purged_and_verified_absent(self):
        self.assertIn("RemoveMappedSourceLayers", self.source)
        self.assertNotIn("database.Purge", self.source)
        self.assertIn("layer.Erase()", self.source)
        self.assertIn('Trace($"cleanup:erase:start:{name}")', self.source)
        self.assertIn('Trace("cleanup:bounded-removal-verified")', self.source)
        self.assertIn('Trace("cleanup:pass")', self.cleanup)
        self.assertIn("VerifyRemovedLayers(document.Database, expectation.RemovedSourceLayers)", self.validator)
        self.assertIn("removed_source_layers_verified", self.validator)
        self.assertIn("retainedByObjectDecision", self.source)

    def test_cleanup_runs_in_a_fresh_process_before_final_validation(self):
        self.assertIn("cleanup-mapped-sources.generated.scr", self.script)
        self.assertIn("LAYERLABCLEANUPMAPPEDSOURCES", self.script)
        cleanup = self.script.index("-SuccessMarker 'cleanup:pass'")
        validation = self.script.index("-SuccessMarker 'validate:pass'")
        self.assertLess(cleanup, validation)
        self.assertIn("Wait-Process -Id $process.Id -Timeout 20", self.script)

    def test_expectation_and_validation_record_the_policy(self):
        self.assertGreaterEqual(
            self.source.count('verification_policy = "independent_save_reopen_process"'), 2
        )

    def test_generated_dimension_blocks_are_preserved_and_content_verified(self):
        self.assertIn("IsGeneratedDimensionBlock(block)", self.source)
        self.assertIn('generated_block_policy = "verify_regenerated_dimension_anonymous_block_non_layer_content"', self.validator)
        self.assertIn("GeneratedDimensionBlockSha256.Equals", self.validator)
        self.assertIn("generated_dimension_block_non_layer_sha256", self.source)

    def test_signature_coordinates_are_normalized_for_save_reopen_noise(self):
        self.assertIn("NormalizeCoordinate(point.X)", self.source)
        self.assertIn("Math.Round(value, 6", self.source)

    def test_text_signatures_use_authoritative_alignment_not_derived_extents(self):
        self.assertIn("entity is not DBText && entity is not MText && entity is not BlockReference", self.source)
        self.assertIn("text.AlignmentPoint", self.source)
        self.assertIn("text.HorizontalMode", self.source)


if __name__ == "__main__":
    unittest.main()
