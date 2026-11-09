"""Deploy committed UI assets; never copy CAD engines or layer standards."""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

PACKAGE = "src/dwg-layer-standardizer"
UI_FILES = ("workbench-core.js", "preview-hover.js", "style.css", "app.js", "index.html")
BACKEND_FILES = ("cadlayer/app.py",)
CONFIG = ".layerlab-sync.json"
SERVE_MARKER = "    # LayerLab managed UI/standard preflight."
SERVE_PREFLIGHT = SERVE_MARKER + "\n    from sync_ui import prepare_workbench\n    prepare_workbench(ROOT)\n"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=1)
def git_program():
    """Use a working Git executable without changing the user's global PATH."""
    detected = shutil.which("git")
    candidates = [Path(detected)] if detected else []
    if detected:
        base = Path(detected).parent.parent
        candidates.extend(base / relative for relative in ("ucrt64/bin/git.exe", "mingw64/bin/git.exe"))
    if os.name == "nt":
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git"
        candidates.extend(bundled / relative for relative in ("ucrt64/bin/git.exe", "mingw64/bin/git.exe", "cmd/git.exe"))
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run([str(candidate), "--version"], capture_output=True, timeout=10)
            if probe.returncode == 0 and probe.stdout.startswith(b"git version "):
                return str(candidate)
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise ValueError("A working Git executable is required to synchronize the installed UI.")


def git(repo, *args):
    executable = git_program()
    env = {**os.environ, "PATH": str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    result = subprocess.run([executable, "-C", str(repo), *args], capture_output=True, check=False, env=env)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def committed_payload(repo):
    revision = git(repo, "rev-parse", "HEAD").decode().strip()
    payload = {"ui/" + name: git(repo, "show", revision + ":" + PACKAGE + "/ui/" + name) for name in UI_FILES}
    payload.update({name: git(repo, "show", revision + ":" + PACKAGE + "/" + name) for name in BACKEND_FILES})
    index = payload["ui/index.html"].decode("utf-8")
    index = re.sub(r'\?v=[^"\s]+', "?v=" + revision[:12], index)
    index = index.replace("<head>", '<head>\n<meta name="layerlab-ui-revision" content="' + revision + '">', 1)
    payload["ui/index.html"] = index.encode("utf-8")
    helper_path = PACKAGE + "/scripts/sync_ui.py"
    if subprocess.run([git_program(), "-C", str(repo), "cat-file", "-e", revision + ":" + helper_path], capture_output=True).returncode == 0:
        payload["scripts/sync_ui.py"] = git(repo, "show", revision + ":" + helper_path)
    return revision, payload


def validate_payload(repo, payload):
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js is required to validate an updated UI; installed files were not changed.")
    for name in ("app.js", "workbench-core.js", "preview-hover.js"):
        result = subprocess.run([node, "--check"], input=payload["ui/" + name], capture_output=True)
        if result.returncode:
            raise ValueError("UI syntax validation failed: " + name + "\n" + result.stderr.decode(errors="replace"))
    index = payload["ui/index.html"].decode("utf-8")
    for asset in re.findall(r'(?:src|href)="\./([^"?]+)(?:\?[^" ]*)?"', index):
        if "ui/" + asset not in payload:
            raise ValueError("UI references an unmanaged asset; update the deployment allowlist: " + asset)
    if "scripts/sync_ui.py" in payload:
        ast.parse(payload["scripts/sync_ui.py"].decode("utf-8"))
    for name in BACKEND_FILES:
        if name in payload:
            ast.parse(payload[name].decode("utf-8"))
    # Run the committed core tests against the committed core, never dirty working files.
    tests = git(repo, "show", "HEAD:tools/test-workbench-core.cjs")
    scratch = Path(repo) / "temp" / "layerlab-ui-validation"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as folder:
        root = Path(folder)
        (root / PACKAGE / "ui").mkdir(parents=True)
        (root / "tools").mkdir()
        (root / PACKAGE / "ui/workbench-core.js").write_bytes(payload["ui/workbench-core.js"])
        test_file = root / "tools/test-workbench-core.cjs"
        test_file.write_bytes(tests)
        result = subprocess.run([node, "--test", str(test_file)], capture_output=True)
        if result.returncode:
            raise ValueError("Committed UI tests failed; installed files were not changed.\n" + result.stdout.decode(errors="replace"))


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".sync-" + uuid.uuid4().hex)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def deployment_lock(target):
    lock = Path(target) / ".layerlab-sync.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise ValueError("Another LayerLab UI synchronization is running; retry after it finishes.") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def deploy(target, payload, revision, validator):
    """Validate first, back up changed files, publish index last, restore on failure."""
    target = Path(target).resolve()
    allowed = {"ui/" + name for name in UI_FILES} | set(BACKEND_FILES) | {"scripts/sync_ui.py", "scripts/run.py", "start-layerlab.bat"}
    if not set(payload) <= allowed:
        raise ValueError("Refusing to deploy files outside the UI/launcher allowlist.")
    if not (target / "SKILL.md").is_file():
        raise ValueError("The installed skill directory is missing SKILL.md.")
    with deployment_lock(target):
        previous = {}
        for relative in payload:
            path = target / relative
            if not path.resolve().is_relative_to(target):
                raise ValueError("Refusing a deployment path outside the installed skill.")
            previous[relative] = path.read_bytes() if path.exists() else None
        changed = {name: data for name, data in payload.items() if previous[name] != data}
        if not changed:
            return {"revision": revision, "changed": [], "backup": None}
        validator()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        backup = target / ".layerlab-ui-backups" / stamp
        backup.mkdir(parents=True)
        for relative in changed:
            if previous[relative] is not None:
                path = backup / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(previous[relative])
        manifest = {"revision": revision, "files": {name: {"existed": previous[name] is not None,
                    "before": sha(previous[name]) if previous[name] is not None else None, "after": sha(data)} for name, data in changed.items()}}
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        published = []
        try:
            for relative in sorted(changed, key=lambda name: name == "ui/index.html"):
                atomic_write(target / relative, changed[relative])
                published.append(relative)
            for relative, data in payload.items():
                if (target / relative).read_bytes() != data:
                    raise OSError("Deployed hash mismatch: " + relative)
        except Exception:
            for relative in reversed(published):
                if previous[relative] is None:
                    (target / relative).unlink(missing_ok=True)
                else:
                    atomic_write(target / relative, previous[relative])
            raise
        return {"revision": revision, "changed": sorted(changed), "backup": str(backup)}


def check_standard(path):
    path = Path(path).resolve(strict=True)
    data = read_json(path)
    if data.get("schema") != "cad-layer-standard/v1" or not data.get("layers"):
        raise ValueError("Configured standard is not a valid cad-layer-standard/v1 JSON.")
    if "DEMO" in str(data.get("name", "")).upper():
        raise ValueError("The installed production standard must be user-approved, not the demo standard.")
    return path


def sync(repo, target):
    repo, target = Path(repo).resolve(), Path(target).resolve()
    revision, payload = committed_payload(repo)
    return deploy(target, payload, revision, lambda: validate_payload(repo, payload))


def patch_launchers(target):
    run = (target / "scripts/run.py").read_text(encoding="utf-8")
    if SERVE_MARKER not in run:
        anchor = "def run_serve(args: argparse.Namespace) -> None:\n"
        if run.count(anchor) != 1:
            raise ValueError("Unrecognized serve launcher; refusing to overwrite a customized CLI.")
        run = run.replace(anchor, anchor + SERVE_PREFLIGHT, 1)
    bat = (target / "start-layerlab.bat").read_text(encoding="utf-8")
    lines = bat.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith('start "LayerLab local server"')]
    if len(matches) != 1:
        raise ValueError("Unrecognized batch launcher; refusing to overwrite it.")
    lines[matches[0]] = 'start "LayerLab local server" /min "%~dp0.venv\\Scripts\\python.exe" "%~dp0scripts\\run.py" serve --port 8765 --open-browser'
    lines = [line for line in lines if not line.startswith('timeout /t 3') and line != 'start "" "http://127.0.0.1:8765"']
    return {"scripts/run.py": run.encode("utf-8"), "start-layerlab.bat": ("\n".join(lines) + "\n").encode("utf-8")}


def install(repo, target, standard):
    repo, target = Path(repo).resolve(), Path(target).resolve()
    standard = check_standard(standard)
    current_hooks = subprocess.run([git_program(), "-C", str(repo), "config", "--get", "core.hooksPath"], capture_output=True, text=True).stdout.strip()
    hooks = str(repo / "tools/git-hooks")
    if current_hooks and Path(current_hooks).as_posix() not in ("tools/git-hooks", Path(hooks).as_posix()):
        raise ValueError("Existing core.hooksPath was preserved; integrate the LayerLab hook manually: " + current_hooks)
    if not current_hooks:
        hooks_path = Path(git(repo, "rev-parse", "--git-path", "hooks").decode().strip())
        if not hooks_path.is_absolute():
            hooks_path = repo / hooks_path
        active = [p.name for p in hooks_path.glob("*") if p.is_file() and not p.name.endswith(".sample")]
        if active:
            raise ValueError("Existing Git hooks were preserved; integrate them before enabling UI sync: " + ", ".join(active))
    revision, payload = committed_payload(repo)
    # Bootstrap the reviewed automation itself before its first commit.
    payload["scripts/sync_ui.py"] = Path(__file__).read_bytes()
    payload.update(patch_launchers(target))
    result = deploy(target, payload, revision, lambda: (ast.parse(payload["scripts/run.py"].decode()), validate_payload(repo, payload)))
    config = {"schema": "layerlab-installed-sync/v1", "source_repo": str(repo), "standard_path": str(standard)}
    atomic_write(target / CONFIG, json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"))
    git(repo, "config", "--local", "core.hooksPath", "tools/git-hooks")
    git(repo, "config", "--local", "layerlab.installedSkill", str(target))
    return result


def prepare_workbench(package):
    package = Path(package).resolve()
    config_path = package / CONFIG
    if not config_path.is_file():
        candidate = Path.home() / ".codex/skills/dwg-layer-standardizer" / CONFIG
        if not candidate.is_file():
            return
        config_path = candidate
    config = read_json(config_path)
    repo = Path(config["source_repo"])
    target = Path(config.get("sync_target", str(config_path.parent)))
    standard = check_standard(os.environ.get("CADLAYER_STANDARD") or config["standard_path"])
    result = sync(repo, target)
    os.environ["CADLAYER_STANDARD"] = str(standard)
    print("[LayerLab] UI " + result["revision"][:12] + " verified; standard: " + str(standard), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "sync"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--standard", type=Path)
    args = parser.parse_args()
    target = args.target
    if target is None:
        value = subprocess.run([git_program(), "-C", str(args.repo), "config", "--local", "--get", "layerlab.installedSkill"], capture_output=True, text=True).stdout.strip()
        if not value:
            print("[LayerLab] No installed skill is configured; automatic synchronization skipped.")
            return
        target = Path(value)
    if args.command == "install":
        if not args.standard:
            parser.error("--standard is required for installation")
        result = install(args.repo, target, args.standard)
    else:
        result = sync(args.repo, target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("[LayerLab] Synchronization failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
