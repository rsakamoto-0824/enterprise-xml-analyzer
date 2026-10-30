"""Build on Windows x64 with exactly CPython 3.14.6; only build tools use pip."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import venv
import zipfile

from xml_analyzer import VERSION

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build-python"
ARTIFACTS = ROOT / "artifacts"
NAME = "EnterpriseXmlAnalyzer"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, help="Directory containing the locked Windows wheels; avoids network downloads")
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 14, 6) or sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Build requires Windows x64 and the full CPython 3.14.6 distribution with Tcl/Tk.")
    import tkinter
    tkinter.Tcl()
    BUILD.mkdir(exist_ok=True)
    environment = BUILD / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / "Scripts/python.exe"
    command = [str(python), "-m", "pip", "install", "--require-hashes", "-r", str(ROOT / "requirements-build-windows.txt")]
    if args.wheelhouse:
        command += ["--no-index", "--find-links", str(args.wheelhouse.resolve())]
    subprocess.run(command, check=True, cwd=ROOT)
    subprocess.run([str(python), "-m", "unittest", "discover", "-s", "tests_python", "-v"], check=True, cwd=ROOT)
    subprocess.run([str(python), "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--windowed",
                    "--name", NAME, "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"),
                    "--specpath", str(BUILD), str(ROOT / "run.py")], check=True, cwd=ROOT)
    folder = BUILD / "dist" / NAME
    shutil.copy2(ROOT / "docs/python-windows.md", folder / "README-Windows.md")
    shutil.copytree(ROOT / "docs/python-user-guide", folder / "UserGuide", dirs_exist_ok=True)
    for name in ("python-design.md", "python-test-report.md"):
        shutil.copy2(ROOT / "docs" / name, folder / name)
    (folder / "validation").mkdir(exist_ok=True)
    for source in (ROOT / "docs/validation").glob("python-*.json"):
        shutil.copy2(source, folder / "validation" / source.name)
    shutil.copy2(ROOT / "requirements-build-windows.txt", folder / "BUILD-DEPENDENCIES.txt")
    license_file = Path(sys.base_prefix) / "LICENSE.txt"
    if not license_file.is_file():
        raise SystemExit("Python LICENSE.txt is missing; refusing to publish an incomplete package.")
    shutil.copy2(license_file, folder / "PYTHON-LICENSE.txt")
    licenses = folder / "licenses"
    licenses.mkdir(exist_ok=True)
    for source in (Path(sys.base_prefix) / "tcl").rglob("license.terms"):
        shutil.copy2(source, licenses / (source.parent.name + "-license.terms"))
    (licenses / "THIRD-PARTY.md").write_text(
        "This application contains CPython 3.14.6 and its standard library, SQLite, Expat and Tcl/Tk. "
        "See PYTHON-LICENSE.txt, the Tcl/Tk license files and the bundled data. "
        "PyInstaller 6.22.3 provides the bootloader under the GPL with its distribution exception: "
        "https://pyinstaller.org/en/stable/license.html\n", encoding="utf-8")
    ARTIFACTS.mkdir(exist_ok=True)
    # Test the bundled runtime from a path with spaces and Japanese, with host Python settings poisoned.
    check_root = BUILD / "portable-check" / "日本語 検証"
    if check_root.exists():
        shutil.rmtree(check_root)
    check_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(folder, check_root)
    report_path = ARTIFACTS / "python-windows-frozen-selftest.json"
    env = dict(os.environ, PYTHONHOME=str(BUILD / "nonexistent-python"), PYTHONPATH=str(BUILD / "nonexistent-modules"))
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    subprocess.run([str(check_root / (NAME + ".exe")), "--self-test", str(report_path)],
                   check=True, cwd=check_root.parent, env=env, timeout=120)
    result = json.loads(report_path.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result.get("python") != "3.14.6" or not result.get("frozen"):
        raise SystemExit("Frozen runtime verification failed.")
    if result.get('native_drop_files') != 2:
        raise SystemExit('Frozen Windows file drop verification failed.')
    manifest = {"app": "enterprise-xml-analyzer-python", "version": VERSION, "python": "3.14.6",
                "platform": platform.platform(), "pyinstaller": "6.22.3", "files": [], "source_sha256": {}}
    for source in sorted((ROOT / "xml_analyzer").glob("*.py")) + [ROOT / "run.py"]:
        manifest["source_sha256"][source.relative_to(ROOT).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    for file in sorted(folder.rglob("*")):
        if file.is_file():
            with file.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest["files"].append({"path": file.relative_to(folder).as_posix(), "sha256": digest})
    (folder / "BUILD-MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    target = ARTIFACTS / "enterprise-xml-analyzer-windows-x64-python3146.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                archive.write(file, NAME + "/" + file.relative_to(folder).as_posix())
    print("Windows package:", target)


if __name__ == "__main__":
    main()
