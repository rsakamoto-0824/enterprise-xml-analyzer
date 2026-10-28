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
from xml_analyzer.table_export import LONG, WIDE, header_ranges, items, prepare_table, preview_table, save_table, wide_rows, item_rows, link_rows


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
        self.assertEqual(len(preview['rows']), 3)
        rows = [dict(zip(preview['headers'], row)) for row in preview['rows']]
        self.assertEqual(rows[1]['/outside'], ' A ')
        self.assertEqual(rows[0]['/n/@id'], '001')
        self.assertIsNone(rows[2]['/outside'])
        self.assertEqual(rows[2]['/z'], '30')
        self.assertEqual(len({row['データID'] for row in rows}), 3)

    def test_prefix_changes_align_and_namespace_uris_remain_distinct(self):
        self.parse('<r xmlns:a="urn:a" xmlns:b="urn:b"><a:x a:id="01">A</a:x><b:x>B</b:x></r>')
        self.parse('<r xmlns:q="urn:a"><q:x q:id="02">C</q:x></r>', 'b.xml')
        preview = preview_table(self.store)
        rows = [dict(zip(preview['headers'], row)) for row in preview['rows']]
        self.assertEqual(rows[0]['/{urn%3Aa}x'], 'A')
        self.assertEqual(rows[2]['/{urn%3Aa}x'], 'C')
        self.assertIsNone(rows[2]['/{urn%3Ab}x'])
        self.assertEqual(rows[2]['/{urn%3Aa}x/@{urn%3Aa}id'], '02')

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
                all_text = ''.join(archive.read(name).decode() for name in archive.namelist() if name.startswith('xl/worksheets/'))
                self.assertIn('001', all_text)
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
        self.parse('<r><record>' + ''.join(f'<v>{i}</v>' for i in range(16400)) + '</record></r>')
        target = self.base / 'wide.xlsx'
        result = save_table(self.store, target)
        self.assertEqual(result['columns'], 16401)
        self.assertEqual(result['sheets'], 7)
        with zipfile.ZipFile(target) as archive:
            first = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(len(first.findall('.//{*}row')[0].findall('{*}c')), 16384)
            second = ET.fromstring(archive.read('xl/worksheets/sheet2.xml'))
            values = [''.join(c.itertext()) for c in second.findall('.//{*}row')[1].findall('{*}c')]
            self.assertEqual(values[-1], '16399')

    def test_headers_are_separated_and_link_common_and_record_specific_scopes(self):
        self.parse('<root version="001"><header><run>007</run></header><record id="A"><header><kind>K1</kind></header><v>20</v><v>21</v></record><record id="B"><header><kind>K2</kind></header><v>30</v></record></root>')
        paths = ['/root/header', '/root/record/header']
        with prepare_table(self.store, Task(), paths) as plan:
            self.assertEqual((plan.records, plan.header_groups, plan.values), (2, 3, 15))
            self.assertEqual((plan.columns, plan.header_values, plan.common_values, plan.data_values), (4, 6, 2, 7))
            headers = [dict(zip(row.headers, row.cells)) for row in item_rows(plan, Task(), 'header')]
            links = [dict(zip(row.headers, row.cells)) for row in link_rows(plan, Task())]
            self.assertEqual(len(links), 4)
            record_ids = [r['id'] for r in plan.db.execute('SELECT * FROM records ORDER BY seq')]
            for index, expected in enumerate(('K1', 'K2')):
                groups = {row['ヘッダーID'] for row in links if row['データID'] == record_ids[index]}
                values = {row['値'] for row in headers if row['ヘッダーID'] in groups}
                self.assertIn('007', values)
                self.assertIn(expected, values)
                self.assertNotIn('K2' if expected == 'K1' else 'K1', values)
            self.assertEqual({r['適用範囲'] for r in links}, {'共通', 'ノード内'})
        for orientation in (LONG, WIDE):
            result = save_table(self.store, self.base / (orientation + '.xlsx'), orientation, header_paths=paths)
            self.assertEqual(result['data_records'], 2)
            self.assertEqual(result['data_rows'], 2 if orientation == WIDE else 7)
            with zipfile.ZipFile(self.base / (orientation + '.xlsx')) as archive:
                names = [s.attrib['name'] for s in ET.fromstring(archive.read('xl/workbook.xml')).findall('.//{*}sheet')]
                self.assertTrue({'ヘッダー', '紐づけ', 'ヘッダー定義', '共通データ', 'データ一覧'}.issubset(names))
                data = [''.join(cell.itertext()) for cell in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//{*}c')]
                self.assertNotIn('007', data)
                self.assertNotIn('K1', data)
                self.assertIn('21', data)

    def test_overlapping_ranges_coalesce_and_same_filenames_never_cross_link(self):
        for number in ('001', '002'):
            self.parse(f'<r><h><id>{number}</id></h><n><v>{number}</v></n></r>')
        with prepare_table(self.store, Task(), ['/r/h', '/r/h/id', '/r/h']) as plan:
            self.assertEqual(plan.ranges, ('/r/h', '/r/h/id'))
            self.assertEqual((plan.records, plan.header_groups, plan.header_values), (2, 2, 4))
            links = list(link_rows(plan, Task()))
            self.assertEqual(len(links), 2)
            for row in links:
                data, header, _, file, _ = row.cells
                self.assertTrue(data.startswith(file + ':D:'))
                self.assertTrue(header.startswith(file + ':H:'))

    def test_header_validation_preserves_existing_output_and_settings_accept_multiple_ranges(self):
        self.parse('<r><h>A</h><n>B</n></r>')
        target = self.base / 'existing.xlsx'
        target.write_bytes(b'original')
        for path in ('r/h', '/', '/r/h[1]', '/r/@id', '/r/*', '/r/../h', '/r//h'):
            with self.assertRaisesRegex(AppError, 'HEADER_PATH'):
                save_table(self.store, target, header_paths=[path])
        with self.assertRaisesRegex(AppError, 'HEADER_NOT_FOUND'):
            save_table(self.store, target, header_paths=['/r/missing'])
        self.assertEqual(target.read_bytes(), b'original')
        self.assertEqual(header_ranges(' /r/h\n\n/r/h\n/r/n '), ('/r/h', '/r/n'))
        self.assertEqual(list(self.base.glob('xml-all-table-*')), [])

    def test_header_only_and_leaf_root_do_not_lose_values(self):
        self.parse('<r id="001">A</r>')
        preview = preview_table(self.store, WIDE)
        self.assertEqual(preview['data_records'], 1)
        self.assertEqual(dict(zip(preview['headers'], preview['rows'][0]))['/r/@id'], '001')
        preview = preview_table(self.store, WIDE, header_paths=['/r'])
        self.assertEqual((preview['data_records'], preview['header_groups'], preview['header_values']), (0, 1, 2))
        self.assertEqual(len(preview['previews']['ヘッダー情報']['rows']), 2)
        result = save_table(self.store, self.base / 'headers.xlsx', header_paths=['/r'])
        self.assertEqual(result['values'], 2)
        self.assertEqual(result['data_rows'], 0)

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
