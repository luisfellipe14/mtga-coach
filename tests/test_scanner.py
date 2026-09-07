import json
import unittest

from mtga_coach.scanner import RecordScanner


class RecordScannerTests(unittest.TestCase):
    def test_record_split_across_chunks_is_held_until_complete(self):
        record = json.dumps({"type": "GREMessageType_ConnectResp", "systemSeatIds": [2]})
        scanner = RecordScanner()
        first = scanner.feed(record[:12].encode())
        self.assertEqual(first, [])
        self.assertEqual(scanner.warnings, [])
        second = scanner.feed(record[12:].encode() + b"\n")
        self.assertEqual([item["payload"]["type"] for item in second], ["GREMessageType_ConnectResp"])

    def test_multibyte_character_split_across_chunks_survives(self):
        payload = json.dumps({"name": "Túmulo Aquático"}, ensure_ascii=False).encode("utf-8")
        cut = payload.index("ú".encode("utf-8")) + 1
        scanner = RecordScanner()
        scanner.feed(payload[:cut])
        records = scanner.feed(payload[cut:] + b"\n", final=True)
        self.assertEqual(records[0]["payload"]["name"], "Túmulo Aquático")

    def test_line_numbers_survive_buffer_trimming(self):
        blocks = b"".join(f'ruído\n{{"n": {index}}}\n'.encode() for index in range(5))
        scanner = RecordScanner()
        records = []
        for start in range(0, len(blocks), 7):
            records.extend(scanner.feed(blocks[start:start + 7]))
        records.extend(scanner.feed(b"", final=True))
        self.assertEqual([item["payload"]["n"] for item in records], [0, 1, 2, 3, 4])
        self.assertEqual([item["line"] for item in records], [2, 4, 6, 8, 10])

    def test_unparsable_dictionary_is_skipped_without_stopping_the_scan(self):
        data = b'[D3D12] {"Vendor": "Intel", "Driver": any}\n{"kept": true}\n'
        scanner = RecordScanner()
        records = scanner.feed(data, final=True)
        self.assertEqual([item["payload"] for item in records], [{"kept": True}])
        self.assertEqual(scanner.warnings, [])

    def test_truncated_tail_only_warns_once_the_file_is_declared_finished(self):
        scanner = RecordScanner()
        self.assertEqual(scanner.feed(b'{"a": 1}\n{"unfinished":'), [{"line": 1, "payload": {"a": 1}}])
        self.assertEqual(scanner.warnings, [])
        scanner.feed(b"", final=True)
        self.assertEqual(len(scanner.warnings), 1)
        self.assertIn("linha 2", scanner.warnings[0])


if __name__ == "__main__":
    unittest.main()
