from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from xml_analyzer.models import AppError, Cancelled, Task
from xml_analyzer.parser import parse_file
from xml_analyzer.storage import Store
from xml_analyzer.table_export import LONG, WIDE, items, prepare_table, preview_table, save_table, wide_rows


class TableExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xml-table-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = Store(self.base / "work.sqlite")
        blocker = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def parse(self, xml, name="a.xml"):
        path = self.base / name
        path.write_text(xml, encoding="utf-8")
        return parse_file(self.store, path)

    def test_all_levels_attributes_empty_parent_and_repeated_values(self):
        self.parse('<r version="001">before<n><x>20</x><x>30</x><empty/></n>after<other flag="false">=1+1</other></r>')
        result = list(items(self.store, Task()))
        self.assertEqual(sum(item.kind == "要素" for item in result), 6)
        by_path = {item.path: item.value for item in result}
        self.assertEqual(by_path, {'/r[1]': 'beforeafter', '/r[1]/@version': '001', '/r[1]/n[1]': '',
                                  '/r[1]/n[1]/x[1]': '20', '/r[1]/n[1]/x[2]': '30', '/r[1]/n[1]/empty[1]': '',
                                  '/r[1]/other[1]': '=1+1', '/r[1]/other[1]/@flag': 'false'})

    def test_wide_unions_every_xml_without_a_common_record_path(self):
        self.parse('<r><n id="001"><x>20</x></n><outside> A </outside></r>')
        self.parse('<different><z>30</z></different>', 'b.xml')
        preview = preview_table(self.store, WIDE)
        self.assertEqual(preview['elements'], 6)
        self.assertEqual(len(preview['rows']), 2)
        rows = [dict(zip(preview['headers'], row)) for row in preview['rows']]
        self.assertEqual(rows[0]['/r[1]/outside[1]'], ' A ')
        self.assertEqual(rows[0]['/r[1]/n[1]/@id'], '001')
        self.assertIsNone(rows[1]['/r[1]/outside[1]'])
        self.assertEqual(rows[1]['/different[1]/z[1]'], '30')

    def test_prefix_changes_align_and_namespace_uris_remain_distinct(self):
        self.parse('<r xmlns:a="urn:a" xmlns:b="urn:b"><a:x a:id="01">A</a:x><b:x>B</b:x></r>')
        self.parse('<r xmlns:q="urn:a"><q:x q:id="02">C</q:x></r>', 'b.xml')
        preview = preview_table(self.store)
        rows = [dict(zip(preview['headers'], row)) for row in preview['rows']]
        self.assertEqual(rows[0]['/r[1]/{urn%3Aa}x[1]'], 'A')
        self.assertEqual(rows[1]['/r[1]/{urn%3Aa}x[1]'], 'C')
        self.assertIsNone(rows[1]['/r[1]/{urn%3Ab}x[1]'])
        self.assertEqual(rows[1]['/r[1]/{urn%3Aa}x[1]/@{urn%3Aa}id'], '02')

    def test_both_orientations_write_literal_values_and_no_formulas(self):
        self.parse('<r id="001"><v>=1+1</v><empty/></r>')
        for orientation in (LONG, WIDE):
            target = self.base / (orientation + '.xlsx')
            result = save_table(self.store, target, orientation)
            self.assertEqual(result['elements'], 3)
            self.assertEqual(result['values'], 4)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                text = archive.read('xl/worksheets/sheet1.xml').decode()
                self.assertIn('001', text)
                self.assertIn('=1+1', text)
                self.assertEqual(ET.fromstring(text).findall('.//{*}f'), [])
                names = [s.attrib['name'] for s in ET.fromstring(archive.read('xl/workbook.xml')).findall('.//{*}sheet')]
                self.assertIn('出典', names)
                self.assertIn('出力情報', names)
                self.assertEqual('項目一覧' in names, orientation == WIDE)

    def test_long_values_and_metadata_are_split_without_losing_characters(self):
        value = 'a' * 32766 + '😀' + 'b' * 36000
        uri = 'u' * 40000
        self.parse(f'<r xmlns:p="{uri}"><p:x a="{value}">{value}</p:x></r>')
        with prepare_table(self.store, Task()) as plan:
            element = plan.db.execute("SELECT id,part FROM fields WHERE kind='要素' AND path!=? ORDER BY part", ('/r[1]',)).fetchall()
            chunks = [plan.db.execute('SELECT value FROM vals WHERE field=?', (f['id'],)).fetchone()[0] for f in element]
            self.assertEqual(''.join(chunks), value)
            self.assertEqual([f['part'] for f in element], list(range(1, len(chunks) + 1)))
            for row in wide_rows(plan, Task()):
                if row.sheet.startswith('全要素'):
                    self.assertTrue(all(len(h) <= 128 for h in row.headers))
        for orientation in (LONG, WIDE):
            result = save_table(self.store, self.base / (orientation + '.xlsx'), orientation)
            self.assertEqual(result['elements'], 2)

    def test_columns_over_excel_limit_split_into_more_sheets_and_keep_last_value(self):
        self.parse('<r>' + ''.join(f'<v>{i}</v>' for i in range(16400)) + '</r>')
        target = self.base / 'wide.xlsx'
        result = save_table(self.store, target)
        self.assertEqual(result['columns'], 16401)
        self.assertEqual(result['sheets'], 5)
        with zipfile.ZipFile(target) as archive:
            first = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(len(first.findall('.//{*}row')[0].findall('{*}c')), 16384)
            second = ET.fromstring(archive.read('xl/worksheets/sheet2.xml'))
            values = [''.join(c.itertext()) for c in second.findall('.//{*}row')[1].findall('{*}c')]
            self.assertEqual(values[-1], '16399')

    def test_empty_bad_format_cancel_and_limit_preserve_existing_file(self):
        target = self.base / 'existing.xlsx'
        target.write_bytes(b'original')
        for orientation in (LONG, WIDE):
            with self.assertRaisesRegex(AppError, 'FILES_EMPTY'):
                save_table(self.store, target, orientation)
        self.parse('<r><n>value</n></r>')
        with self.assertRaisesRegex(AppError, 'TABLE_LAYOUT'):
            save_table(self.store, target, 'bad')
        for orientation in (LONG, WIDE):
            task = Task()
            task.cancel.set()
            with self.assertRaises(Cancelled):
                save_table(self.store, target, orientation, task)
            with patch('xml_analyzer.writers.MAX_EXPORT_ROWS', 1):
                with self.assertRaisesRegex(AppError, 'EXPORT_LIMIT'):
                    save_table(self.store, target, orientation)
            self.assertEqual(target.read_bytes(), b'original')
        self.assertEqual(list(self.base.glob('xml-all-table-*')), [])


if __name__ == '__main__':
    unittest.main()
