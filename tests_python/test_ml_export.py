from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from xml_analyzer.ml_export import matrix_rows, numeric_value, plan_export, preview_export, save_export, workbook_rows
from xml_analyzer.models import AppError, Cancelled, Column, Task, Template
from xml_analyzer.parser import parse_file
from xml_analyzer.storage import Store
from xml_analyzer.writers import write_xlsx


class MLExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xml-ml-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = Store(self.base / "work.sqlite")
        blocker = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def parse(self, text, name="a.xml"):
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return parse_file(self.store, path)

    def test_auto_candidates_override_and_file_unit(self):
        for name in ("a.xml", "b.xml"):
            self.parse('<r><group><n>1</n><n>2</n></group><group><n>3</n></group></r>', name)
        plan = plan_export(self.store)
        self.assertEqual(plan.record_path, "/r/group")
        self.assertEqual(plan.rows, 4)
        self.assertEqual(plan.headers, ["n[1]", "n[2]"])
        self.assertEqual([r.cells for r in matrix_rows(self.store, plan)], [[1, 2], [3, None]] * 2)
        self.assertEqual(plan_export(self.store, "/r/group/n").rows, 6)
        root = plan_export(self.store, "")
        self.assertEqual(root.rows, 2)
        self.assertIn("group[2]/n[1]", root.headers)

    def test_global_types_missing_ids_and_repeated_children(self):
        self.assertIsNone(numeric_value("1.000000000000000000000000000000001"))
        self.assertIsNone(numeric_value("100000000000000000000000000000001"))
        self.assertEqual(numeric_value("100000000000000000000000000000000"), 1e32)
        self.parse('<r><n id="123"><v>1.5</v><ok>true</ok><code>001</code><large>1234567890123456</large><tag>A</tag><tag>B</tag><empty/></n>'
                   '<n id="124"><v>2</v><ok>FALSE</ok><code>002</code><large>1234567890123457</large><tag>C</tag></n>'
                   '<n id="125"><v/><ok> </ok><code>003</code></n></r>')
        plan = plan_export(self.store)
        rows = [dict(zip(r.headers, r.cells)) for r in matrix_rows(self.store, plan)]
        self.assertEqual(rows[0], {"@id": "123", "v": 1.5, "ok": True, "code": "001", "large": "1234567890123456", "tag[1]": "A", "tag[2]": "B", "empty": None})
        self.assertEqual(rows[1]["ok"], False)
        self.assertIsNone(rows[1]["tag[2]"])
        self.assertIsNone(rows[2]["v"])
        self.assertFalse(set(plan.headers).intersection({"元ファイル", "要素パス", "ファイルID"}))
        dictionary = [r for r in workbook_rows(self.store, plan, "v", Task()) if r.sheet == "項目定義"]
        by_name = {r.cells[0]: r.cells for r in dictionary}
        self.assertEqual(by_name["v"][2:5], ["目的変数", 1, 2])
        self.assertIn("ID候補", by_name["@id"][2])
        self.assertEqual(by_name["empty"][-1], "全件欠損")

    def test_types_scan_beyond_preview_and_across_all_files(self):
        self.parse('<r>' + '<n><v>2</v></n>' * 25 + '</r>')
        self.parse('<r><n><v>category</v></n></r>', "b.xml")
        plan, preview = preview_export(self.store)
        self.assertEqual(plan.rows, 26)
        self.assertEqual(len(preview), 20)
        self.assertEqual(preview[0].cells, ["2"])
        self.assertEqual(plan.features[0].type, "string")

    def test_namespace_identity_mixed_text_and_nested_alignment(self):
        self.parse('<r xmlns:a="urn:a" xmlns:b="urn:b"><n>before<a:v a:id="001">2</a:v>after<b:v>3</b:v><x><t>4</t><t>5</t></x><x><t>6</t></x></n><n><a:v>7</a:v></n></r>')
        plan = plan_export(self.store)
        rows = [dict(zip(r.headers, r.cells)) for r in matrix_rows(self.store, plan)]
        self.assertEqual(rows[0]["#text"], "beforeafter")
        self.assertEqual(rows[0]["{urn%3Aa}v/@{urn%3Aa}id"], "001")
        self.assertEqual(rows[0]["{urn%3Ab}v"], 3)
        self.assertEqual(rows[0]["x[1]/t[2]"], 5)
        self.assertEqual(rows[0]["x[2]/t[1]"], 6)
        self.assertIsNone(rows[1]["x[1]/t[2]"])

    def test_four_sheets_types_literal_formula_and_provenance(self):
        self.parse('<r><n id="001"><x>1.25</x><y>=1+1</y></n><n id="002"><x>2</x><y>class</y></n></r>')
        self.parse('<r><n id="003"><x>3</x><y>class</y></n></r>', "b.xml")
        target = self.base / "学習.xlsx"
        result = save_export(self.store, target, target="y")
        self.assertEqual((result["records"], result["features"]), (3, 3))
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall(".//{*}sheet")
            self.assertEqual([s.attrib["name"] for s in sheets], ["学習データ", "出典", "項目定義", "出力情報"])
            for name in archive.namelist():
                if name.endswith(".xml"):
                    ET.fromstring(archive.read(name))
            data = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(len(data.findall(".//{*}row")), 4)
            self.assertEqual(data.findall(".//{*}f"), [])
            cells = data.findall(".//{*}row")[1].findall("{*}c")
            self.assertEqual(cells[0].attrib["t"], "inlineStr")
            self.assertEqual(cells[1].find("{*}v").text, "1.25")
            self.assertEqual(cells[2].find(".//{*}t").text, "=1+1")
        source = [r.cells for r in workbook_rows(self.store, plan_export(self.store), "", Task()) if r.sheet == "出典"]
        self.assertEqual(source[0][2:5], [1, 2, 2])
        self.assertEqual(source[1][2:5], [3, 3, 1])
        split = self.base / "split.xlsx"
        template = Template(sheet_rows=2, source_file=False, columns=[Column(".", "dummy")])
        write_xlsx(split, workbook_rows(self.store, plan_export(self.store), "y", Task()), template)
        with zipfile.ZipFile(split) as archive:
            names = [s.attrib["name"] for s in ET.fromstring(archive.read("xl/workbook.xml")).findall(".//{*}sheet")]
            self.assertEqual(names[:3], ["学習データ", "学習データ-2", "出典"])
            self.assertEqual(len(ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(".//{*}row")), 3)
            self.assertEqual(len(ET.fromstring(archive.read("xl/worksheets/sheet2.xml")).findall(".//{*}row")), 2)

    def test_bad_path_target_overflow_cancel_preserve_existing_file(self):
        self.parse('<r><n>1</n><n>2</n></r>')
        target = self.base / "existing.xlsx"
        target.write_bytes(b"original")
        for kwargs in ({"record_path": "relative"}, {"record_path": "/r/missing"}, {"target": "missing"}):
            with self.assertRaises(AppError):
                save_export(self.store, target, **kwargs)
            self.assertEqual(target.read_bytes(), b"original")
        task = Task()
        task.cancel.set()
        with self.assertRaises(Cancelled):
            save_export(self.store, target, task=task)
        self.assertEqual(target.read_bytes(), b"original")
        self.store.clear()
        self.parse('<r><n>' + '😀' * 17000 + '</n></r>')
        with self.assertRaisesRegex(AppError, "ML_CELL_LIMIT"):
            save_export(self.store, target, "/r/n")
        self.assertEqual(target.read_bytes(), b"original")

    def test_heterogeneous_file_missing_record_is_not_silently_skipped(self):
        self.parse('<r><n>1</n><n>2</n></r>')
        self.parse('<other><v>3</v></other>', "b.xml")
        self.assertEqual(plan_export(self.store).rows, 2)
        with self.assertRaisesRegex(AppError, "ML_RECORD_MISSING"):
            plan_export(self.store, "/r/n")

    def test_column_limit_and_empty_input(self):
        with self.assertRaisesRegex(AppError, "FILES_EMPTY"):
            plan_export(self.store)
        self.parse('<r><n>' + ''.join(f'<v{i}>1</v{i}>' for i in range(1025)) + '</n></r>')
        with self.assertRaisesRegex(AppError, "ML_RECORD_LIMIT"):
            plan_export(self.store, "/r/n")
        self.store.clear()
        self.parse('<r><n>' + ''.join(f'<v{i}>1</v{i}>' for i in range(600)) + '</n><n>'
                   + ''.join(f'<v{i}>1</v{i}>' for i in range(600, 1200)) + '</n></r>')
        with self.assertRaisesRegex(AppError, "ML_COLUMN_LIMIT"):
            plan_export(self.store)


if __name__ == "__main__":
    unittest.main()
