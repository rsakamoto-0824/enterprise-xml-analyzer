"""Package only the Python application, with no user data or legacy web dependencies."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    target = ROOT / "artifacts/enterprise-xml-analyzer-python-source.zip"
    target.parent.mkdir(exist_ok=True)
    files = [ROOT / name for name in ("run.py", "start_windows.cmd", "build_windows.cmd", "build_windows.py",
                                     ".python-version", "pyproject.toml", "requirements-build-windows.txt", "README.md")]
    for folder, pattern in (("xml_analyzer", "*.py"), ("tests_python", "*.py"), ("docs", "python-*.md")):
        files.extend(sorted((ROOT / folder).glob(pattern)))
    files.extend(ROOT / name for name in ("scripts/package_python.py", "scripts/performance_python.py", "docs/legacy-web-readme.md", ".github/workflows/python-windows.yml"))
    files.extend(sorted((ROOT / "docs/validation").glob("python-*.json")))
    manifest = {"app": "enterprise-xml-analyzer-python-source", "python": "3.14.6", "files": []}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr("enterprise-xml-analyzer-python/" + relative, data)
            manifest["files"].append({"path": relative, "sha256": hashlib.sha256(data).hexdigest()})
        archive.writestr("enterprise-xml-analyzer-python/SOURCE-MANIFEST.json", json.dumps(manifest, indent=2))
    print(target)


if __name__ == "__main__":
    main()
