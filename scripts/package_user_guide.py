"""Build an offline HTML and a portable folder ZIP from the checked-in guide."""
import base64
from html.parser import HTMLParser
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs/python-user-guide"
ARTIFACTS = ROOT / "artifacts"


class LocalLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        path = values.get("src") or values.get("href", "")
        if path and not path.startswith(("#", "https:", "data:")):
            self.paths.append(path)


def main():
    source = (GUIDE / "procedure-guide.html").read_text(encoding="utf-8")
    parser = LocalLinks()
    parser.feed(source)
    for name in parser.paths:
        if not (GUIDE / name).is_file():
            raise SystemExit("Guide asset missing: " + name)
    if "［" in source:
        raise SystemExit("Unreplaced template placeholder")
    inline_css = (GUIDE / "procedure-guide.css").read_text(encoding="utf-8").replace(
        '@import url("base.css");', (GUIDE / "base.css").read_text(encoding="utf-8"))
    source = source.replace('<link rel="stylesheet" href="procedure-guide.css">', "<style>" + inline_css + "</style>")
    source = source.replace('<link rel="stylesheet" href="guide.css">', "<style>" + (GUIDE / "guide.css").read_text(encoding="utf-8") + "</style>")
    for name in ("procedure-guide.js", "guide.js"):
        script = (GUIDE / name).read_text(encoding="utf-8")
        source = source.replace(f'<script src="{name}" defer></script>', "")
        source = source.replace("</body>", "<script>" + script + "</script>\n</body>")
    for name in parser.paths:
        path = GUIDE / name
        if path.suffix not in (".png", ".xml"):
            continue
        mime = "image/png" if path.suffix == ".png" else "application/xml"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        source = source.replace(f'"{name}"', f'"data:{mime};base64,{encoded}"')
    ARTIFACTS.mkdir(exist_ok=True)
    single = ARTIFACTS / "enterprise-xml-analyzer-user-guide.html"
    single.write_text(source, encoding="utf-8")
    target = ARTIFACTS / "enterprise-xml-analyzer-user-guide.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(GUIDE.rglob("*")):
            if path.is_file():
                archive.write(path, "EnterpriseXmlAnalyzer-UserGuide/" + path.relative_to(GUIDE).as_posix())
    print(single)
    print(target)


if __name__ == "__main__":
    main()
