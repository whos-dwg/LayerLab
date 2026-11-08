"""Contracts for optional DWS generation and actual AutoCAD load validation."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "src/dwg-layer-standardizer/integrations/autocad-2026-native"
sys.path.insert(0, str(ROOT / "src/dwg-layer-standardizer"))

from cadlayer.native import completed_dws, convert_dwg


class DwsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commands = (NATIVE / "NativeDwgCommands.cs").read_text(encoding="utf-8")
        cls.script = (NATIVE / "run-native-dws.ps1").read_text(encoding="utf-8")
        cls.desktop_loader = (NATIVE / "run-full-autocad-dws-load.ps1").read_text(encoding="utf-8")
        cls.api = (ROOT / "src/dwg-layer-standardizer/cadlayer/app.py").read_text(encoding="utf-8")

    def test_download_rejects_unverified_dws(self):
        import uuid
        from fastapi import HTTPException
        from cadlayer import app as api
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = str(uuid.uuid4())
            output = root / job / 'output'
            output.mkdir(parents=True)
            (output / 'fixture_layer_mapping.dws').write_bytes(b'unverified')
            with patch.object(api, 'WORKSPACE', root):
                with self.assertRaises(HTTPException) as error:
                    api.download(job, 'fixture_layer_mapping.dws')
            self.assertEqual(error.exception.status_code, 404)

    def test_dws_uses_autocad_layer_translator_xdata(self):
        self.assertIn('const string appName = "ACLAYTRANS"', self.commands)
        self.assertIn("ExtendedDataAsciiString", self.commands)
        self.assertIn("record.XData = xdata", self.commands)

    def test_validation_actually_loads_dws_in_autocad(self):
        self.assertIn("acet-laytrans", self.script)
        self.assertIn("LAYERLAB_DWS_LOAD_PASS", self.script)
        self.assertIn("LAYERLABPREPAREDWSVALIDATION", self.script)
        self.assertIn("LAYERLABVALIDATEDWS", self.script)
        self.assertIn("prepare-dws-validation.generated.scr", self.script)
        self.assertIn("load-dws.generated.lsp", self.script)
        self.assertIn("run-full-autocad-dws-load.ps1", self.script)
        self.assertIn("AutoCAD 2026\\acad.exe", self.script)
        self.assertIn('command "_.QSAVE"', self.script)
        self.assertIn("GetActiveObject('AutoCAD.Application.25.1')", self.desktop_loader)
        self.assertIn("RPC_E_CALL_REJECTED", self.desktop_loader)
        self.assertIn("RPC_E_SERVERCALL_RETRYLATER", self.desktop_loader)
        self.assertIn("Invoke-AutoCadCom", self.desktop_loader)
        self.assertIn("AutoCAD remained busy while", self.desktop_loader)
        self.assertIn("if ($documentCount -gt 0)", self.desktop_loader)
        self.assertNotIn("has no active document", self.desktop_loader)
        self.assertIn("Write-Output -NoEnumerate $application.Documents", self.desktop_loader)
        self.assertIn("$validationDocument.Close($false)", self.desktop_loader)
        self.assertIn("$originalDocument.Activate()", self.desktop_loader)
        self.assertIn("LAYERLAB_DWS_LOAD_MARKER", self.commands)
        self.assertIn("Remove-Item -LiteralPath $transientPath", self.script)
        self.assertIn('actual_autocad_load = true', self.commands)
        self.assertIn("VerifyRemovedLayers(document.Database, expectation.RemovedSourceLayers)", self.commands)
        self.assertIn("removed_source_layers_verified", self.commands)

    def test_dws_failure_cannot_remove_or_downgrade_primary_dwg(self):
        dws_endpoint = self.api.split('@app.post("/api/jobs/{job_id}/dws")', 1)[1]
        self.assertIn('"primary_dwg_status": "PASS"', dws_endpoint)
        self.assertIn('"DWG 已完成、DWS 未產生。"', dws_endpoint)
        self.assertNotIn("unlink", dws_endpoint.split('@app.get("/api/jobs/{job_id}/files/{filename}")', 1)[0])

    def test_completed_dws_rejects_a_file_changed_after_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            dws = output / "fixture_layer_mapping.dws"
            dws.write_bytes(b"verified fixture")
            validation = {
                "status": "PASS",
                "dws": {
                    "path": dws.name,
                    "sha256": hashlib.sha256(dws.read_bytes()).hexdigest().upper(),
                    "actual_autocad_load": True,
                },
            }
            (output / "dws-validation.json").write_text(json.dumps(validation), encoding="utf-8")
            self.assertIsNotNone(completed_dws(output))
            dws.write_bytes(b"changed after validation")
            self.assertIsNone(completed_dws(output))

    def test_dws_rejects_object_routing_and_checks_primary_hash_and_cleanup(self):
        self.assertIn("cannot represent object-level mapping decisions", self.commands)
        self.assertIn("Approved DWS mapping must contain every source layer exactly once", self.commands)
        self.assertIn("$verifiedDwgBefore = Get-Sha256 $verifiedDwgPath", self.script)
        self.assertIn("Verified primary DWG changed during DWS creation", self.script)
        self.assertIn("$validationAuxiliary", self.script)

    def test_failed_native_output_is_archived_for_a_clean_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "input.dwg"
            source.write_bytes(b"fixture")
            output = folder / "output"
            output.mkdir()
            (output / "native-writer.trace.log").write_text("crashed", encoding="utf-8")
            inventory = {"filetype": "DWG", "filename": source.name, "sha256": "ABC"}
            standard = {"schema": "cad-layer-standard/v1", "name": "Fixture", "version": "1", "layers": []}
            with patch("cadlayer.native._run_script", side_effect=ValueError("native crash")):
                with self.assertRaisesRegex(ValueError, "native crash"):
                    convert_dwg(source, inventory, standard, [], output)
            archived = list(folder.glob("output-failed-*"))
            self.assertEqual(len(archived), 1)
            self.assertEqual((archived[0] / "native-writer.trace.log").read_text(encoding="utf-8"), "crashed")
            self.assertTrue((folder / "native-convert-input" / "approved-mapping.json").is_file())


if __name__ == "__main__":
    unittest.main()
