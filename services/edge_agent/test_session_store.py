import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from services.edge_agent.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SessionStore(self.directory.name)

    def test_latest_reads_only_tail_and_preserves_large_unicode_record(self):
        record = {"id": "last", "text": "电池" * 6000}
        self.store.path.write_text("invalid historical line\n" + json.dumps(record, ensure_ascii=False)
                                   + "\r\n \t\r\n", encoding="utf-8")
        self.assertEqual(self.store.latest(), record)

    def test_latest_sees_other_store_appends_without_stale_cache(self):
        other = SessionStore(self.directory.name)
        self.assertIsNone(self.store.latest())
        self.store.append({"id": "first"})
        self.assertEqual(self.store.latest()["id"], "first")
        other.append({"id": "second"})
        self.assertEqual(self.store.latest()["id"], "second")

    def test_latest_accepts_empty_and_single_record_without_newline(self):
        for content, expected in ((" \n\r\n", None), ('{"id":"only"}', {"id": "only"})):
            self.store.path.write_text(content, encoding="utf-8")
            self.assertEqual(self.store.latest(), expected)

    def test_latest_recovers_last_complete_record_from_crash_tail_without_rewrite(self):
        for incomplete in (b'{"id":"interrupted"', b'{"text":"\xe7\x94'):
            original = b'{"id":"first"}\n{"id":"last"}\n' + incomplete
            self.store.path.write_bytes(original)
            with self.subTest(incomplete=incomplete):
                self.assertEqual(self.store.latest(), {"id": "last"})
                self.assertEqual(self.store.path.read_bytes(), original)

    def test_latest_accepts_bom_and_complete_unterminated_last_line(self):
        for original, expected in (
            (b'\xef\xbb\xbf{"id":"only"}', {"id": "only"}),
            (b'\xef\xbb\xbf{"id":"first"}\n{"id":"last"}', {"id": "last"}),
            (b'\xef\xbb\xbf{"id":"first"}\n{"id":', {"id": "first"}),
            (b'', None),
            (b'{"id":', None),
        ):
            self.store.path.write_bytes(original)
            with self.subTest(original=original):
                self.assertEqual(self.store.latest(), expected)
                self.assertEqual(self.store.path.read_bytes(), original)

    def test_latest_complete_nonobject_is_explicit_error(self):
        for suffix in (b'[]', b'null\n', b'42\n{"incomplete":'):
            self.store.path.write_bytes(b'{"id":"prior"}\n' + suffix)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "snapshot record must be an object"):
                self.store.latest()

    def test_crash_tail_session_remains_listed_and_page_keeps_complete_records(self):
        from services.api_server.sessions import SessionLibrary
        original = b'{"id":"first","timestamp":100,"dataSource":"can"}\n{"id":'
        self.store.path.write_bytes(original)
        library = SessionLibrary(self.directory.name, self.directory.name)
        row = next(item for item in library.list_sessions() if item["id"] == "current")
        self.assertEqual((100, 100, "can"), (row["first"], row["last"], row["source"]))
        self.assertEqual(["first"], [item["id"] for item in library.snapshot_page("current")["items"]])
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_raw_frames_are_appended_separately_without_rewriting(self):
        first = {"id": 0x104, "data": [1, 2], "sequence": 1}
        second = {"id": 0x105, "data": [3, 4], "sequence": 2}
        self.assertEqual(self.store.append_frames([first]), 1)
        path = Path(self.directory.name) / "frames.jsonl"
        original = path.read_bytes()
        self.assertEqual(self.store.append_frames([second]), 1)
        self.assertTrue(path.read_bytes().startswith(original))
        self.assertEqual([json.loads(line) for line in path.read_text().splitlines()], [first, second])
        self.assertFalse(self.store.path.exists())

    def test_multiple_instances_can_append_and_read_concurrently(self):
        stores = [SessionStore(self.directory.name) for _ in range(4)]

        def write(index):
            for number in range(30):
                stores[index].append({"id": f"{index}-{number}", "text": "电池" * 1000})
                self.assertIsNotNone(stores[(index + 1) % 4].latest())

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(4)))
        records = self.store.list_snapshots()
        self.assertEqual(len(records), 120)
        self.assertEqual(len({record["id"] for record in records}), 120)

    def test_slow_page_does_not_block_append_or_include_new_records(self):
        self.store.append({"id": "first"})
        self.store.append({"id": "second"})
        parsing, release, appended = threading.Event(), threading.Event(), threading.Event()
        results = []
        real_loads = json.loads

        def slow_loads(value, *args, **kwargs):
            if threading.current_thread().name == "page-reader" and not parsing.is_set():
                parsing.set()
                release.wait(2)
            return real_loads(value, *args, **kwargs)

        def append():
            SessionStore(self.directory.name).append({"id": "new"})
            appended.set()

        with patch("services.edge_agent.session_store.json.loads", slow_loads):
            reader = threading.Thread(name="page-reader", target=lambda: results.append(self.store.page_snapshots()))
            writer = threading.Thread(target=append)
            reader.start()
            try:
                self.assertTrue(parsing.wait(1))
                writer.start()
                self.assertTrue(appended.wait(0.5), "page scan held the capture append lock")
            finally:
                release.set()
                reader.join(2)
                if writer.ident is not None:
                    writer.join(2)
        self.assertFalse(reader.is_alive())
        self.assertEqual([item["id"] for item in results[0]["items"]], ["first", "second"])
        self.assertIsNone(results[0]["nextOffset"])
        self.assertEqual(self.store.latest()["id"], "new")

    def test_page_excludes_partial_tail_but_accepts_complete_last_line(self):
        self.store.path.write_bytes(b'{"id":"first"}\n{"id":')
        self.assertEqual(self.store.page_snapshots(), {"items": [{"id": "first"}], "nextOffset": None})
        self.store.path.write_bytes(b'{"id":"first"}\n{"id":"last"}')
        self.assertEqual([item["id"] for item in self.store.page_snapshots()["items"]], ["first", "last"])


if __name__ == "__main__":
    unittest.main()
