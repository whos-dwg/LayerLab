"""Run with: python -m unittest discover -s tools -p test_ui_sync.py -v"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/dwg-layer-standardizer/scripts/sync_ui.py"
spec = importlib.util.spec_from_file_location("layerlab_sync", MODULE)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


class SyncTests(unittest.TestCase):
    def test_git_discovery_recovers_from_a_broken_windows_launcher(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'temp') as folder:
            base = Path(folder)
            launcher = base / 'cmd/git.exe'
            actual = base / 'ucrt64/bin/git.exe'
            launcher.parent.mkdir(parents=True)
            actual.parent.mkdir(parents=True)
            launcher.write_bytes(b'fake launcher')
            actual.write_bytes(b'fake real executable')
            def probe(command, **kwargs):
                if command[0] == str(launcher):
                    raise OSError('corrupt launcher')
                self.assertEqual(command[0], str(actual))
                return subprocess.CompletedProcess(command, 0, b'git version test', b'')
            S.git_program.cache_clear()
            try:
                with patch.object(S.shutil, 'which', return_value=str(launcher)), patch.object(S.subprocess, 'run', side_effect=probe):
                    self.assertEqual(S.git_program(), str(actual))
            finally:
                S.git_program.cache_clear()

    def setUp(self):
        scratch = ROOT / "temp/ui-sync-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "installed skill"
        (self.target / "ui").mkdir(parents=True)
        (self.target / "scripts").mkdir()
        (self.target / "cadlayer").mkdir()
        (self.target / "standards").mkdir()
        (self.target / "SKILL.md").write_text("installed skill", encoding="utf-8")
        (self.target / "ui/index.html").write_bytes(b"old index")
        (self.target / "ui/app.js").write_bytes(b"old app")
        (self.target / "cadlayer/engine.py").write_bytes(b"custom CAD engine")
        self.standard = self.target / "standards/approved.json"
        self.standard.write_text(json.dumps({"schema":"cad-layer-standard/v1", "name":"Synthetic approved fixture", "version":"1.1.0", "layers":[{"name":"A-wall", "aci":8}]}), encoding="utf-8")
        (self.target / "scripts/run.py").write_text("# custom CLI retained\ndef run_serve(args: argparse.Namespace) -> None:\n    import uvicorn\n", encoding="utf-8")
        (self.target / "start-layerlab.bat").write_text('@echo off\nrem custom launcher retained\nstart "LayerLab local server" /min cmd /c "old command"\ntimeout /t 3 >nul\nstart "" "http://127.0.0.1:8765"\n', encoding="utf-8")

    def repository(self):
        repo = self.root / "repo with spaces"
        repo.mkdir()
        self.command(repo, "init", "-q")
        for name in S.UI_FILES:
            dest = repo / S.PACKAGE / "ui" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / S.PACKAGE / "ui" / name, dest)
        for name in S.BACKEND_FILES:
            dest = repo / S.PACKAGE / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / S.PACKAGE / name, dest)
        for relative in ["tools/test-workbench-core.cjs", S.PACKAGE + "/scripts/sync_ui.py", "tools/git-hooks/post-commit", "tools/git-hooks/post-merge", "tools/git-hooks/post-checkout"]:
            dest = repo / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, dest)
            if "git-hooks" in relative:
                dest.chmod(0o755)
        # Git for Windows commonly has core.filemode=false, so chmod alone does
        # not make copied hook fixtures executable in the index.
        self.command(repo, "add", ".")
        for hook in ("post-commit", "post-merge", "post-checkout"):
            self.command(repo, "update-index", "--chmod=+x", "tools/git-hooks/" + hook)
        self.commit(repo, "initial")
        hook_mode = self.command(repo, "ls-files", "-s", "tools/git-hooks/post-commit").split()[0]
        self.assertEqual(hook_mode, b"100755")
        return repo

    def command(self, repo, *args):
        env = {**os.environ, "GIT_AUTHOR_NAME":"Sync Test", "GIT_AUTHOR_EMAIL":"sync@test.local", "GIT_COMMITTER_NAME":"Sync Test", "GIT_COMMITTER_EMAIL":"sync@test.local"}
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
        self.last_git_stderr = result.stderr.decode(errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def commit(self, repo, message):
        self.command(repo, "add", ".")
        return self.command(repo, "commit", "-qm", message)

    def test_real_commit_hook_deploys_and_startup_repairs_committed_ui(self):
        repo = self.repository()
        engine = (self.target / "cadlayer/engine.py").read_bytes()
        standard = self.standard.read_bytes()
        S.install(repo, self.target, self.standard)
        self.assertIn(b"custom CLI retained", (self.target / "scripts/run.py").read_bytes())
        self.assertIn(b"--open-browser", (self.target / "start-layerlab.bat").read_bytes())
        style = repo / S.PACKAGE / "ui/style.css"
        style.write_bytes(style.read_bytes() + b"\n/* committed update */\n")
        hook_output = self.commit(repo, "new UI triggers hook").decode(errors="replace")
        hook_error = self.last_git_stderr
        revision = self.command(repo, "rev-parse", "HEAD").decode().strip()
        committed_style = self.command(repo, "show", "HEAD:" + S.PACKAGE + "/ui/style.css")
        hooks_path = self.command(repo,"config","--get","core.hooksPath").decode()
        self.assertTrue(revision.encode() in (self.target / "ui/index.html").read_bytes(), hook_error + hook_output + "\nHooks: " + hooks_path)
        self.assertEqual(committed_style, (self.target / "ui/style.css").read_bytes())
        # Uncommitted source edits must never leak into the installed copy.
        (repo / S.PACKAGE / "ui/app.js").write_bytes(b"invalid dirty code {")
        S.sync(repo, self.target)
        self.assertNotIn(b"invalid dirty", (self.target / "ui/app.js").read_bytes())
        (self.target / "ui/style.css").write_bytes(b"stale installed file")
        with patch.dict(os.environ):
            os.environ.pop("CADLAYER_STANDARD", None)
            S.prepare_workbench(self.target)
            self.assertEqual(os.environ["CADLAYER_STANDARD"], str(self.standard.resolve()))
        self.assertEqual(committed_style, (self.target / "ui/style.css").read_bytes())
        self.assertEqual(engine, (self.target / "cadlayer/engine.py").read_bytes())
        self.assertEqual(standard, self.standard.read_bytes())
        self.assertTrue(list((self.target / ".layerlab-ui-backups").glob("*/manifest.json")))

    def test_failed_validation_preserves_every_installed_file(self):
        def invalid():
            raise ValueError("failed tests")
        with self.assertRaisesRegex(ValueError, "failed tests"):
            S.deploy(self.target, {"ui/app.js":b"new app", "ui/index.html":b"new index"}, "revision", invalid)
        self.assertEqual((self.target / "ui/app.js").read_bytes(), b"old app")
        self.assertEqual((self.target / "ui/index.html").read_bytes(), b"old index")
        self.assertFalse((self.target / ".layerlab-ui-backups").exists())

    def test_partial_copy_rolls_back_and_preserves_backup(self):
        original = S.atomic_write
        failed = False
        def fail_once(path, data):
            nonlocal failed
            if Path(path).name == "index.html" and not failed:
                failed = True
                raise OSError("simulated write failure")
            return original(path, data)
        with patch.object(S, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
            S.deploy(self.target, {"ui/app.js":b"new", "ui/preview-hover.js":b"new helper", "ui/index.html":b"new index"}, "rev", lambda:None)
        self.assertEqual((self.target / "ui/app.js").read_bytes(), b"old app")
        self.assertEqual((self.target / "ui/index.html").read_bytes(), b"old index")
        self.assertFalse((self.target / "ui/preview-hover.js").exists())
        self.assertFalse((self.target / ".layerlab-sync.lock").exists())
        self.assertTrue(list((self.target / ".layerlab-ui-backups").glob("*/manifest.json")))

    def test_allowlist_never_permits_standard_or_engine_overwrite(self):
        for path in ["standards/approved.json", "cadlayer/engine.py", "../outside"]:
            with self.assertRaisesRegex(ValueError, "allowlist"):
                S.deploy(self.target, {path:b"overwrite"}, "rev", lambda:None)

    def test_verified_result_endpoint_is_in_the_controlled_deployment(self):
        self.assertIn("cadlayer/app.py", S.BACKEND_FILES)
        payload = {"cadlayer/app.py": b"value = 1\n"}
        result = S.deploy(self.target, payload, "rev", lambda: compile(payload["cadlayer/app.py"], "app.py", "exec"))
        self.assertEqual((self.target / "cadlayer/app.py").read_bytes(), payload["cadlayer/app.py"])
        self.assertIn("cadlayer/app.py", result["changed"])

    def test_lock_rejects_concurrent_deployment(self):
        (self.target / ".layerlab-sync.lock").write_text("other sync")
        with self.assertRaisesRegex(ValueError, "Another"):
            S.deploy(self.target, {"ui/index.html":b"new"}, "rev", lambda:None)
        self.assertEqual((self.target / "ui/index.html").read_bytes(), b"old index")

    def test_missing_configured_standard_stops_instead_of_using_demo(self):
        (self.target / S.CONFIG).write_text(json.dumps({"source_repo":str(self.root), "standard_path":str(self.root / "missing.json")}))
        with patch.dict(os.environ):
            os.environ.pop("CADLAYER_STANDARD", None)
            with self.assertRaises(FileNotFoundError):
                S.prepare_workbench(self.target)
        self.assertEqual((self.target / "ui/index.html").read_bytes(), b"old index")

    def test_existing_git_hooks_are_preserved(self):
        repo = self.repository()
        custom = repo / ".git/hooks/pre-commit"
        custom.write_text("#!/bin/sh\nexit 0\n")
        with self.assertRaisesRegex(ValueError, "Existing Git hooks"):
            S.install(repo, self.target, self.standard)
        self.assertEqual(custom.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual((self.target / "ui/index.html").read_bytes(), b"old index")

    def test_demo_standard_cannot_become_the_production_default(self):
        self.standard.write_text(json.dumps({"schema":"cad-layer-standard/v1", "name":"DEMO ONLY", "layers":[{"name":"STD-WALL"}]}))
        with self.assertRaisesRegex(ValueError, "user-approved"):
            S.check_standard(self.standard)


if __name__ == "__main__":
    unittest.main()
