from dataclasses import replace
from datetime import date
import csv
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from xml_analyzer.comparison import compare, search
from xml_analyzer.extraction import (OutputRow, candidate_columns, convert, difference_rows,
                                     extract, preview, values_for)
from xml_analyzer.models import AppError, Cancelled, Column, DiffOptions, Task, Template
from xml_analyzer.parser import parse_file
from xml_analyzer.simple_export import SIMPLE_HEADERS, all_element_rows, simple_template
from xml_analyzer.storage import Store
from xml_analyzer.visualization import xml_fragment
from xml_analyzer.writers import write_csv, write_xlsx


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xml-python-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / "work.sqlite")
        self.no_network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def parse(self, text, name="sample.xml", chunk=65536):
        path = self.base / name
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return parse_file(self.store, path, chunk_bytes=chunk)

    def template(self, path="/r/n", *columns):
        return Template(record_path=path, columns=list(columns) or [Column("@id", "id"), Column("v", "値")])

    def test_simple_export_includes_all_elements_and_files_in_document_order(self):
        a = self.parse('<r>前<n id="001">中<![CDATA[😀]]></n>後<empty/><n id="002"/><!--注釈--><?test value?></r>')
        b = self.parse('<r xmlns:p="urn:test"><p:n p:id="003">=1+1</p:n></r>')
        rows = [dict(zip(row.headers, row.cells)) for row in all_element_rows(self.store)]
        self.assertEqual(len(rows), a["info"]["elements"] + b["info"]["elements"])
        self.assertEqual([r["要素名"] for r in rows], ["r", "n", "empty", "n", "r", "p:n"])
        self.assertEqual(rows[0]["テキスト"], "前後")
        self.assertEqual(rows[1]["テキスト"], "中😀")
        self.assertEqual(rows[2]["テキスト"], "")
        self.assertEqual(rows[3]["要素パス"], "/r[1]/n[2]")
        self.assertEqual(rows[1]["親要素番号"], rows[0]["要素番号"])
        self.assertEqual(rows[0]["階層"], 0)
        self.assertNotEqual(rows[0]["ファイルID"], rows[-1]["ファイルID"])
        self.assertEqual(rows[0]["元ファイル"], rows[-1]["元ファイル"])
        self.assertEqual(rows[-1]["名前空間"], "urn:test")
        self.assertEqual(json.loads(rows[-1]["属性(JSON)"]), {"{urn%3Atest}id": "003"})
        self.assertEqual(rows[-1]["テキスト"], "=1+1")

    def test_simple_export_splits_long_values_losslessly_and_writes_safe_xlsx(self):
        text = "a" * 32766 + "😀" + "b" * 40000
        attribute = "😀" * 20000
        namespace = "u" * 40000
        source = self.parse(f'<r xmlns:p="{namespace}"><p:n id="001" a="{attribute}">{text[:32766]}<e/>{text[32766:]}</p:n><v>=1+1</v></r>')
        rows = list(all_element_rows(self.store))
        child = [dict(zip(r.headers, r.cells)) for r in rows if r.cells[5] == "p:n" or r.cells[2] == 2]
        self.assertGreater(len(child), 1)
        self.assertEqual([r["分割番号"] for r in child], list(range(1, len(child) + 1)))
        for key, expected in (("テキスト", text), ("名前空間", namespace), ("要素名", "p:n"), ("要素パス", "/r[1]/p:n[1]")):
            self.assertEqual("".join(r[key] for r in child), expected)
        self.assertEqual(json.loads("".join(r["属性(JSON)"] for r in child)), {"id": "001", "a": attribute})
        self.assertEqual(sum(r.cells[-1] == 1 for r in rows), source["info"]["elements"])
        for r in rows:
            for cell in r.cells:
                if isinstance(cell, str):
                    self.assertLessEqual(len(cell.encode("utf-16-le")) // 2, 32767)
        target = self.base / "all.xlsx"
        result = write_xlsx(target, iter(rows), replace(simple_template(), sheet_rows=2))
        self.assertEqual(result["rows"], len(rows))
        self.assertGreater(result["sheets"], 1)
        with zipfile.ZipFile(target) as z:
            self.assertIsNone(z.testzip())
            for name in z.namelist():
                if name.endswith(".xml"):
                    tree = ET.fromstring(z.read(name))
                    self.assertEqual(tree.findall('.//{*}f'), [])

    def test_simple_export_is_not_limited_by_tree_or_preview(self):
        count = 10050
        self.parse('<r>' + '<n/>' * count + '</r>')
        self.assertEqual(sum(1 for _ in all_element_rows(self.store)), count + 1)

    def test_simple_export_empty_cancel_and_limit_preserve_existing_file(self):
        with self.assertRaisesRegex(AppError, "FILES_EMPTY"):
            list(all_element_rows(self.store))
        self.parse('<r>' + '<n/>' * 300 + '</r>')
        target = self.base / "existing.xlsx"
        target.write_bytes(b"original")
        task = Task(progress=lambda *args: task.cancel.set())
        with self.assertRaises(Cancelled):
            write_xlsx(target, all_element_rows(self.store, task), simple_template(), task)
        self.assertEqual(target.read_bytes(), b"original")
        with patch("xml_analyzer.writers.MAX_EXPORT_ROWS", 2):
            with self.assertRaisesRegex(AppError, "EXPORT_LIMIT"):
                write_xlsx(target, all_element_rows(self.store), simple_template())
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.base.glob(".xml-analyzer-*.part")), [])
        self.store.clear()

    def test_namespaces_unicode_and_chunk_boundaries(self):
        text = '<r xmlns="urn:root" xmlns:p="urn:p"><p:n p:id="001">日本語😀<![CDATA[<&]]><!--c--><?test p?></p:n></r>'
        left, right = self.parse(text, "a.xml", 1), self.parse(text, "b.xml", 7)
        self.assertEqual(left["info"]["elements"], 2)
        self.assertEqual(left["info"]["attributes"], 1)
        a, b = self.store.node(left["id"], 1), self.store.node(right["id"], 1)
        self.assertEqual(a["full_hash"], b["full_hash"])
        self.assertEqual(a["shape_hash"], b["shape_hash"])
        n = self.store.node(left["id"], 2)
        self.assertEqual(n["path"], "/{urn%3Aroot}r/{urn%3Ap}n")
        self.assertEqual(json.loads(n["attrs"]), {"{urn%3Ap}id": "001"})
        self.assertEqual(self.store.direct_text(n), "日本語😀<&")

    def test_utf16_both_orders_and_declared_encoding(self):
        text = '<?xml version="1.0" encoding="UTF-16"?><r>日本語😀</r>'
        for codec, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            with self.subTest(codec=codec):
                f = self.parse(bom + text.encode(codec), codec + ".xml", 1)
                self.assertEqual(self.store.direct_text(self.store.node(f["id"], 1)), "日本語😀")

    def test_rejects_malformed_without_disclosing_input(self):
        for xml in ("<r>CONFIDENTIAL</wrong>", "<r>", b"<r>\xff</r>"):
            with self.subTest(xml_type=type(xml).__name__):
                with self.assertRaises(AppError) as error:
                    self.parse(xml)
                self.assertNotIn("CONFIDENTIAL", str(error.exception))
                self.assertEqual(self.store.files(), [])
                with self.store.connect() as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM nodes").fetchone()[0], 0)

    def test_dtd_external_and_expansion_are_forbidden(self):
        for xml in ('<!DOCTYPE r SYSTEM "https://invalid.test/dtd"><r/>',
                    '<!DOCTYPE r [<!ENTITY e SYSTEM "file:///secret">]><r>&e;</r>',
                    '<!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><r>&b;</r>'):
            with self.subTest(xml=xml[:20]):
                with self.assertRaisesRegex(AppError, "DTD_FORBIDDEN"):
                    self.parse(xml)
                self.assertFalse(self.store.files())

    def test_unsupported_encodings(self):
        for xml in (b'<?xml version="1.0" encoding="shift_jis"?><r/>',
                    b'<?xml version="1.0" encoding="UTF-16"?><r/>', '<r/>'.encode("utf-32")):
            with self.assertRaisesRegex(AppError, "ENCODING"):
                self.parse(xml)

    def test_depth_token_and_aggregate_attribute_limits(self):
        samples = ["<r>" + "x" * 1048577 + "</r>", "<r>" * 258 + "</r>" * 258,
                   '<r ' + " ".join(f'a{i}="' + "v" * 60000 + '"' for i in range(20)) + '/>',
                   '<r a="' + "v" * 4500000]
        for text in samples:
            with self.subTest(size=len(text)):
                with self.assertRaisesRegex(AppError, "TOKEN_LIMIT|DEPTH_LIMIT"):
                    self.parse(text)
                self.assertEqual(self.store.files(), [])

    def test_cancel_after_first_chunk_cleans_partial_records(self):
        path = self.base / "cancel.xml"
        path.write_text("<r>" + "<n/>" * 10000 + "</r>")
        task = Task()
        task.progress = lambda *args: task.cancel.set()
        with self.assertRaises(Cancelled):
            parse_file(self.store, path, task, chunk_bytes=1000)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM files").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM nodes").fetchone()[0], 0)

    def test_attribute_order_and_namespace_prefix_do_not_change_hash(self):
        a = self.parse('<r xmlns:a="urn:a"><a:n x="1" y="2"/></r>', "a.xml")
        b = self.parse('<r xmlns:b="urn:a"><b:n y="2" x="1"/></r>', "b.xml")
        self.assertEqual(self.store.node(a["id"], 1)["full_hash"], self.store.node(b["id"], 1)["full_hash"])

    def test_file_deletion_cascades_comparison_and_differences(self):
        a, b = self.parse("<r/>", "a.xml"), self.parse("<r/>", "b.xml")
        compare(self.store, a["id"], b["id"])
        self.store.delete_file(a["id"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM comparisons").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM differences").fetchone()[0], 0)
        self.assertEqual(len(self.store.files()), 1)

    def test_workspace_backup_load_and_invalid_source_preserves_current(self):
        self.parse('<r><n id="001"/></r>')
        saved = self.base / "saved.xmlwork"
        self.store.save_workspace(saved)
        self.store.clear()
        self.store.load_workspace(saved)
        self.assertEqual(len(self.store.files()), 1)
        invalid = self.base / "not.db"
        invalid.write_text("not a database")
        with self.assertRaisesRegex(AppError, "WORKSPACE_INVALID"):
            self.store.load_workspace(invalid)
        self.assertEqual(len(self.store.files()), 1)

    def test_three_comparison_modes_and_strict_hashes(self):
        a = self.parse('<r><n id="001">old</n></r>', "a.xml")
        b = self.parse('<r><n id="001">new</n></r>', "b.xml")
        for mode in ("structure", "value", "full"):
            with self.subTest(mode=mode):
                info = compare(self.store, a["id"], b["id"], DiffOptions(mode=mode, verify_hashes=True))
                self.assertEqual(info["counts"]["MODIFIED"], 0 if mode == "structure" else 1)
                self.assertEqual(info["counts"]["AMBIGUOUS"], 0)

    def test_added_removed_attribute_changes_and_differences_export(self):
        a = self.parse('<r><n id="A" v="old"/><n id="B"/></r>', "a.xml")
        b = self.parse('<r><n id="A" v="new"/><n id="C"/></r>', "b.xml")
        info = compare(self.store, a["id"], b["id"])
        self.assertEqual(info["counts"]["ADDED"], 1)
        self.assertEqual(info["counts"]["REMOVED"], 1)
        self.assertEqual(info["counts"]["MODIFIED"], 1)
        rows = list(difference_rows(self.store, info["id"], "MODIFIED"))
        self.assertEqual(rows[0].cells[3], "v")
        self.assertEqual(rows[0].cells[6:8], ["a.xml", "b.xml"])

    def test_key_reorder_move_and_ignore_order(self):
        a = self.parse('<r><n id="A"/><n id="B"/></r>', "a.xml")
        b = self.parse('<r><n id="B"/><n id="A"/></r>', "b.xml")
        self.assertEqual(compare(self.store, a["id"], b["id"])["counts"]["MOVED"], 2)
        info = compare(self.store, a["id"], b["id"], DiffOptions(ignore_order=True))
        self.assertEqual(info["counts"]["MOVED"], 0)
        self.assertEqual(info["counts"]["MODIFIED"], 0)

    def test_duplicate_empty_keys_are_ambiguous_and_empty_unique_key_matches(self):
        a = self.parse('<r><n id=""/><n id=""/></r>', "a.xml")
        b = self.parse('<r><n id=""/><n id=""/></r>', "b.xml")
        self.assertEqual(compare(self.store, a["id"], b["id"])["counts"]["AMBIGUOUS"], 2)
        c = self.parse('<r><n id="" v="a"/></r>', "c.xml")
        d = self.parse('<r><n id="" v="b"/></r>', "d.xml")
        self.assertEqual(compare(self.store, c["id"], d["id"])["counts"]["MODIFIED"], 1)

    def test_large_sibling_sets_are_explicitly_ambiguous(self):
        xml = '<r>' + '<n/>' * 4100 + '</r>'
        a, b = self.parse(xml, "a.xml"), self.parse(xml, "b.xml")
        info = compare(self.store, a["id"], b["id"], DiffOptions(verify_hashes=True))
        self.assertEqual(info["counts"]["AMBIGUOUS"], 4100)

    def test_comments_pi_whitespace_and_cdata(self):
        a = self.parse('<r><!--A--><?p A?><n> a </n></r>', "a.xml")
        b = self.parse('<r><!--B--><?p B?><n>a</n></r>', "b.xml")
        self.assertEqual(compare(self.store, a["id"], b["id"])["counts"]["MODIFIED"], 0)
        result = compare(self.store, a["id"], b["id"], DiffOptions(trim=False, comments=True, processing_instructions=True))
        self.assertEqual(result["counts"]["MODIFIED"], 3)

    def test_cancel_comparison_leaves_no_partial_result(self):
        a, b = self.parse("<r/>", "a.xml"), self.parse("<r/>", "b.xml")
        task = Task()
        task.cancel.set()
        with self.assertRaises(Cancelled):
            compare(self.store, a["id"], b["id"], task=task)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM comparisons").fetchone()[0], 0)

    def test_search_all_targets_exact_case_and_cancel(self):
        f = self.parse('<r><Name CODE="A1">りんご</Name><name/></r>')
        for target, query, expected in (("name", "Name", 1), ("attribute-name", "CODE", 1),
                                         ("attribute-value", "A1", 1), ("text", "りんご", 1), ("path", "/r/Name", 1)):
            with self.subTest(target=target):
                self.assertEqual(search(self.store, f["id"], target, query, True, True)["count"], expected)
        self.assertEqual(search(self.store, f["id"], "name", "name", True)["count"], 2)
        task = Task()
        task.cancel.set()
        with self.assertRaises(Cancelled):
            search(self.store, f["id"], "name", "r", task=task)

    def test_namespace_extraction_and_parent_metadata(self):
        f = self.parse('<r xmlns:p="urn:p"><p:n p:id="001"><p:v>日本語</p:v></p:n></r>')
        n = self.store.node(f["id"], 2)
        for path, expected in (("@{urn%3Ap}id", ["001"]), ("{urn%3Ap}v", ["日本語"]),
                               ("../$name", ["r"]), ("$level:0", ["r"]), ("$namespace", ["urn:p"])):
            self.assertEqual(values_for(self.store, n, path, "a.xml", Task()), expected)

    def test_legacy_namespace_paths_preserve_encode_uri_component_characters(self):
        f = self.parse('<r xmlns:p="urn:a(b)!*\'c"><p:n p:id="001"/></r>')
        path = "/r/{urn%3Aa(b)!*'c}n"
        template = self.template(path, Column("@{urn%3Aa(b)!*'c}id", "id"))
        legacy = json.loads(template.to_json())
        legacy["version"] = 1
        restored = Template.from_json(json.dumps(legacy))
        self.assertEqual(list(extract(self.store, [f["id"]], restored))[0].cells[-1], "001")

    def test_repeat_join_first_rows_and_child_sheets(self):
        f = self.parse('<r><n id="001"><v>A</v><v>B</v><w>1</w><w>2</w></n></r>')
        for repeat, count, expected in (("join", 1, "A | B"), ("first", 1, "A"), ("rows", 2, "A")):
            template = self.template("/r/n", Column("v", "value", repeat=repeat))
            rows = list(extract(self.store, [f["id"]], template))
            self.assertEqual(len(rows), count)
            self.assertEqual(rows[0].cells[-1], expected)
        t = self.template("/r/n", Column("v", "v", repeat="rows"), Column("w", "w", repeat="rows"))
        self.assertEqual(len(list(extract(self.store, [f["id"]], t))), 4)
        t.columns = [Column("v", "v", repeat="sheet")]
        rows = list(extract(self.store, [f["id"]], t))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].headers[0], "RecordPath")
        self.assertEqual(rows[1].cells[0], rows[0].cells[0])
        self.assertEqual(rows[-1].cells[-1], "B")

    def test_filters_and_missing_values(self):
        f = self.parse('<r><n id="001"><v>10</v></n><n id="002"><v>20</v></n><n/></r>')
        t = self.template()
        for op, path, value, maximum, expected in (("equals", "@id", "001", "", 1), ("contains", "@id", "00", "", 2),
                                                   ("range", "v", "15", "25", 1), ("empty", "@id", "", "", 1), ("exists", "@id", "", "", 2)):
            t.filters = [{"path": path, "op": op, "value": value, "max": maximum}]
            self.assertEqual(len(list(extract(self.store, [f["id"]], t))), expected)
        t.filters.append({"path": "@id", "op": "equals", "value": "001"})
        t.logic = "OR"
        self.assertEqual(len(list(extract(self.store, [f["id"]], t))), 2)
        t.filters = []
        t.columns[0].missing = "error"
        with self.assertRaisesRegex(AppError, "MISSING_VALUE"):
            list(extract(self.store, [f["id"]], t))

    def test_type_precision_dates_boolean_and_emoji_cell_limit(self):
        self.assertEqual(convert("001", Column("", "")), "001")
        self.assertEqual(convert("123.5", Column("", "", type="number")), 123.5)
        self.assertEqual(convert("2026-10-25", Column("", "", type="date")), date(2026, 10, 25))
        self.assertIs(convert("true", Column("", "", type="boolean")), True)
        for text, kind in (("123456789012345678", "number"), ("NaN", "number"), ("2026-02-30", "date"), ("yes", "boolean"), ("😀" * 16384, "string")):
            with self.assertRaises(AppError):
                convert(text, Column("", "value", type=kind))

    def test_multi_file_grouping_preview_and_candidates(self):
        a = self.parse('<r>' + '<n id="001">text</n>' * 150 + '</r>', "a.xml")
        b = self.parse('<r><n id="002">text</n></r>', "b.xml")
        t = self.template("/r/n", Column("@id", "id"), Column(".", "text"))
        rows, more = preview(self.store, [a["id"], b["id"]], t)
        self.assertEqual(len(rows), 100)
        self.assertTrue(more)
        t.grouping = "sheets"
        self.assertEqual({r.sheet for r in extract(self.store, [a["id"], b["id"]], t)}, {"a.xml", "b.xml"})
        paths = [c.path for c in candidate_columns(self.store, self.store.node(a["id"], 2))]
        self.assertIn(".", paths)
        self.assertIn("@id", paths)

    def test_template_round_trip_legacy_migration_and_validation(self):
        t = self.template()
        self.assertEqual(Template.from_json(t.to_json()), t)
        legacy = json.loads(t.to_json())
        legacy["version"] = 1
        legacy["recordPath"] = legacy.pop("record_path")
        legacy["format"] = "xlsx"
        legacy["outputName"] = "old"
        self.assertEqual(Template.from_json(json.dumps(legacy)).record_path, "/r/n")
        for raw in ("[]", "{}", '{"version":999}', t.to_json().replace('"sheet_rows": 1048575', '"sheet_rows": 0'),
                    t.to_json().replace('"enabled": true', '"enabled": "yes"')):
            with self.assertRaises(AppError):
                Template.from_json(raw)

    def test_fragment_is_standalone_and_namespace_correct(self):
        f = self.parse('<r xmlns:p="urn:p"><p:n p:id="001"><v/></p:n></r>')
        fragment = xml_fragment(self.store, self.store.node(f["id"], 2))
        parsed = ET.fromstring(fragment)
        self.assertEqual(parsed.tag, "{urn:p}n")
        self.assertEqual(parsed.attrib["{urn:p}id"], "001")
        self.assertEqual(parsed[0].tag, "v")

    def test_xlsx_structure_types_formula_safety_and_sheet_splitting(self):
        target = self.base / "test.xlsx"
        t = self.template("/r/n", Column("@id", "ID"), Column("v", "値"))
        t.source_file, t.sheet_rows = False, 2
        rows = [OutputRow("結果", ["ID", "値"], ["001", "=1+1"]), OutputRow("結果", ["ID", "値"], ["002", date(2026, 10, 25)]),
                OutputRow("結果", ["ID", "値"], ["003", True])]
        result = write_xlsx(target, iter(rows), t)
        self.assertEqual(result["sheets"], 2)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('topLeftCell="A2"', sheet)
            self.assertIn('ref="A1:B3"', sheet)
            self.assertIn('>001<', sheet)
            self.assertIn('>=1+1<', sheet)
            self.assertNotIn('<f>', sheet)
            self.assertFalse(any("externalLink" in p or "vba" in p.lower() for p in archive.namelist()))

    def test_csv_bom_quotes_crlf_and_formula_injection(self):
        target = self.base / "test.csv"
        t = self.template("/r/n", Column(".", "value"))
        values = ["001", "日本語", '=SUM(1,2)', '\t-1+2', 'a,"b"\nnext', "_x0000_"]
        write_csv(target, (OutputRow("x", ["値"], [v]) for v in values), t)
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[1][0], "001")
        self.assertEqual(rows[3][0], "'=SUM(1,2)")
        self.assertEqual(rows[4][0], "'\t-1+2")
        self.assertIn(b"\r\n", data)

    def test_export_cancellation_preserves_existing_file(self):
        t = self.template("/r/n", Column(".", "value"))
        for extension, writer in (("xlsx", write_xlsx), ("csv", write_csv)):
            target = self.base / ("existing." + extension)
            target.write_bytes(b"ORIGINAL")
            task = Task()

            def rows():
                for i in range(10):
                    if i == 3:
                        task.cancel.set()
                    yield OutputRow("x", ["value"], [str(i)])

            with self.assertRaises(Cancelled):
                writer(target, rows(), t, task)
            self.assertEqual(target.read_bytes(), b"ORIGINAL")
        self.assertFalse(list(self.base.glob("*.part")))
        self.assertFalse(list(self.base.glob("xml-xlsx-*")))


if __name__ == "__main__":
    unittest.main()
