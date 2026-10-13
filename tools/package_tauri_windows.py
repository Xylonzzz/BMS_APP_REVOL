"""收集已经构建的桌面包，保存精确哈希与源码边界，不触发安装。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.0-candidate")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.version):
        parser.error("invalid version")
    built = ROOT / "apps/desktop/src-tauri/target/release"
    executable = built / "revol-desktop.exe"
    installers = list((built / "bundle/nsis").glob("REVOL_*_x64-setup.exe"))
    if not executable.is_file() or not (built / "sidecar/revol-sidecar.exe").is_file() or len(installers) != 1:
        parser.error("expected one compiled desktop executable, sidecar and x64 installer")
    release = ROOT / "dist/windows-tauri" / f"{args.version}-{datetime.now():%Y%m%d-%H%M%S}"
    product = release / "REVOL"
    product.mkdir(parents=True, exist_ok=False)
    shutil.copy2(executable, product / executable.name)
    shutil.copytree(built / "sidecar", product / "sidecar")
    for name in ("Windows与浏览器使用说明.md", "真实CAN只读联调.md"):
        shutil.copy2(ROOT / "docs" / name, product / name)
        shutil.copy2(ROOT / "docs" / name, release / name)
    shutil.copy2(installers[0], release / installers[0].name)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, encoding="utf-8")
    sources = {}
    roots = ("apps/web", "apps/desktop", "services", "packages", "schemas")
    excluded = {"node_modules", "target", "gen", "__pycache__", "revol-sidecar"}
    for directory in roots:
        for source in (ROOT / directory).rglob("*"):
            if source.is_file() and not set(source.relative_to(ROOT).parts) & excluded:
                sources[source.relative_to(ROOT).as_posix()] = sha256(source)
    for name in ("apps/desktop_launcher.py", "requirements-build.txt", "tools/build_tauri_windows.ps1"):
        sources[name] = sha256(ROOT / name)
    manifest = {"product": "REVOL", "version": args.version, "architecture": "x64", "packaging": "Tauri 2 + Python sidecar",
                "builtAt": datetime.now().astimezone().isoformat(), "sourceRevision": revision,
                "sourceIncludesUncommittedChanges": bool(dirty.strip()), "hardwareValidated": False,
                "sourceFilesSha256": dict(sorted(sources.items())),
                "executablesSha256": {"revol-desktop.exe": sha256(executable), "sidecar/revol-sidecar.exe": sha256(built / "sidecar/revol-sidecar.exe"),
                                      installers[0].name: sha256(installers[0])}}
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2)
    (release / "version.json").write_text(encoded, encoding="utf-8")
    (product / "version.json").write_text(encoded, encoding="utf-8")
    archive = release / f"REVOL-Windows-x64-{args.version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for file in product.rglob("*"):
            if file.is_file():
                target.write(file, file.relative_to(release))
    hashes = []
    for file in (archive, release / installers[0].name, release / "version.json"):
        hashes.append(f"{sha256(file)}  {file.name}")
    (release / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
    print(str(release))


if __name__ == "__main__":
    main()
