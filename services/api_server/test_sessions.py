import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from services.api_server.sessions import SessionLibrary
from services.edge_agent.session_store import SessionStore
from packages.bms_protocol.aggregator import SnapshotAggregator
from packages.bms_protocol.telemetry import ALARM_FIELDS


def snapshot(index=0, source="file"):
    def value(number, unit, quality="unknown"):
        return {"value": number, "unit": unit, "quality": quality}
    return {"id": f"快照-{index}", "timestamp": 100.0 + index, "dataSource": source,
            "packStatus": {"totalVoltageAdc": value(506.8, "V"),
                           "totalVoltageAccumulated": value(506.4, "V", "partial"),
                           "prechargeVoltage": value(501.2, "V"),
                           "current": value(-3.5, "A"), "soc": value(82.4, "%")}}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "sessions"
        self.current = SessionStore(self.root / "nested" / "当前会话")
        self.library = SessionLibrary(self.root, self.current.directory)

    def artifact(self, kind, session_id="current"):
        result = self.library.export_session(session_id, kind)
        self.addCleanup(lambda: result.path.unlink(missing_ok=True))
        return result

    def test_lists_direct_sessions_and_explicit_nested_current(self):
        self.current.append(snapshot(2))
        historic = SessionStore(self.root / "中文记录")
        historic.append(snapshot(0))
        historic.append(snapshot(1))
        nested = SessionStore(self.root / "nested" / "unselected")
        nested.append(snapshot(9))
        rows = self.library.list_sessions()
        self.assertEqual({row["name"] for row in rows}, {"中文记录", "当前会话"})
        self.assertEqual({row["first"] for row in rows}, {100.0, 102.0})
        self.assertEqual({row["last"] for row in rows}, {101.0, 102.0})
        row = next(row for row in rows if row["name"] == "中文记录")
        self.assertEqual(self.library.resolve(row["id"]), historic.directory.resolve())
        self.assertEqual(self.library.resolve("current"), self.current.directory.resolve())

    def test_rejects_traversal_absolute_paths_and_unknown_ids(self):
        self.current.append(snapshot())
        for session_id in ("..", "../current", "..\\current", str(self.current.directory), "unknown", ""):
            with self.subTest(session_id=session_id), self.assertRaises((ValueError, FileNotFoundError)):
                self.library.resolve(session_id)

    def test_symlink_directory_and_payload_cannot_escape_root(self):
        outside = SessionStore(Path(self.temp.name) / "outside")
        outside.append(snapshot())
        try:
            (self.root / "escape").symlink_to(outside.directory, target_is_directory=True)
            self.current.path.symlink_to(outside.path)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.assertEqual(self.library.list_sessions(), [])
        with self.assertRaises(ValueError):
            self.library.resolve("current")

    def test_page_preserves_order_and_does_not_parse_skipped_records(self):
        self.current.path.write_text('invalid skipped record\n\n' + '\n'.join(
            json.dumps(snapshot(index)) for index in range(6)) + '\n', encoding="utf-8")
        page = self.library.snapshot_page("current", offset=1, limit=2)
        self.assertEqual([item["id"] for item in page["items"]], ["快照-0", "快照-1"])
        self.assertEqual(page["nextOffset"], 3)
        last = self.library.snapshot_page("current", offset=5, limit=2)
        self.assertEqual([item["id"] for item in last["items"]], ["快照-4", "快照-5"])
        self.assertIsNone(last["nextOffset"])
        for offset, limit in ((-1, 10), (0, 201), (True, 2), (0, 0)):
            with self.assertRaises(ValueError):
                self.library.snapshot_page("current", offset, limit)

    def test_csv_preserves_negative_numeric_values_and_escapes_untrusted_text(self):
        record = snapshot(source="=HYPERLINK(\"bad\")")
        record["packStatus"]["current"]["quality"] = "@malicious"
        self.current.append(record)
        exported = self.artifact("summary")
        self.assertEqual(exported.content_type, "text/csv; charset=utf-8")
        rows = list(csv.DictReader(io.StringIO(exported.path.read_text(encoding="utf-8-sig"))))
        self.assertEqual(rows[0]["current_A"], "-3.5")
        self.assertEqual(rows[0]["source"], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[0]["current_quality"], "'@malicious")
        self.assertEqual(rows[0]["totalVoltageAdc_V"], "506.8")
        self.assertEqual(rows[0]["totalVoltageAccumulated_quality"], "partial")

    def test_csv_missing_status_does_not_invent_adc_from_legacy_sum(self):
        self.current.append({"id": "legacy", "timestamp": 1, "totalVoltage":
                             {"value": 506400, "quality": "partial"}, "current":
                             {"value": -2, "quality": "unknown"}})
        exported = self.artifact("summary")
        row = next(csv.DictReader(io.StringIO(exported.path.read_text(encoding="utf-8-sig"))))
        self.assertEqual(row["totalVoltageAdc_V"], "")
        self.assertEqual(row["totalVoltageAccumulated_V"], "506.4")
        self.assertEqual(row["current_A"], "-2")

    def test_known_frames_export_engineering_units_and_field_evidence(self):
        aggregate = SnapshotAggregator(data_source="file", firmware_version="BMS_中文_rev42")
        payload = list((0xA1234567).to_bytes(4, "little")) + list(range(1, 19)) + [255, 254]
        frames = [
            (0x100, list((-3500).to_bytes(4, "little", signed=True)) + [0xC8, 0x13, 0xCC, 0x13]),
            (0x101, [0x94, 0x13, 0x38, 3, 0, 0, 5, 1]),
            (0x139, [1, 0, 1, 0, 0, 0, 0, 0]),
            (0x140, [0xBA, 0x13, 0x7B, 0, 9, 0, 0, 0]),
            *[(0x141 + index, payload[index * 8:index * 8 + 8]) for index in range(3)],
        ]
        for sequence, (frame_id, data) in enumerate(frames, start=1):
            aggregate.ingest({"id": frame_id, "data": data, "dlc": 8, "sequence": sequence,
                              "timestamp": 100 + sequence / 10, "bus": "can1", "direction": "rx",
                              "ide": "standard", "rtr": False})
        record = aggregate.snapshot(timestamp=101)
        self.current.append(record)
        row = next(csv.DictReader(io.StringIO(self.artifact("summary").path.read_text(encoding="utf-8-sig"))))
        self.assertEqual(row["id"], record["id"])
        self.assertEqual(row["firmwareVersion"], "BMS_中文_rev42")
        self.assertEqual(row["totalVoltageAdc_V"], "506.8")
        self.assertEqual(row["current_A"], "-3.5")
        self.assertEqual(row["chargerVoltage_V"], "505.0")
        self.assertEqual(row["chargerCurrent_A"], "12.3")
        self.assertEqual(row["chargerStatus_raw"], "9")
        self.assertEqual(row["chargerCurrent_source"], "can1:0x140")
        self.assertEqual(row["chargerCurrent_timestamp"], "100.4")
        self.assertEqual(row["chargerCurrent_quality"], "unknown")
        self.assertEqual([row[f"{name}_raw"] for name in
                          ("relayPositive", "relayNegative", "relayPrecharge", "relayDcdc")], ["1", "0", "1", "0"])
        self.assertEqual(row["bmsState_raw"], "5")
        self.assertEqual(row["initFlag_raw"], "1")
        self.assertEqual(row["alarmMask_raw"], str(0xA1234567))
        self.assertEqual([row[f"alarm.{name}_raw"] for name, _ in ALARM_FIELDS], list(map(str, range(1, 19))))
        self.assertEqual(row["alarm.IMD_Error_source"], "can1:0x142")

    def test_missing_signal_values_stay_blank_and_text_evidence_is_escaped(self):
        record = snapshot()
        record.update(id="+bad", firmwareVersion="@bad", signals={"relayPositive":
                      {"value": 0, "quality": "timeout", "timestamp": 12, "source": "=bad"}})
        self.current.append(record)
        row = next(csv.DictReader(io.StringIO(self.artifact("summary").path.read_text(encoding="utf-8-sig"))))
        self.assertEqual(row["relayPositive_raw"], "0")
        self.assertEqual(row["relayPositive_timestamp"], "12")
        self.assertEqual(row["relayPositive_source"], "'=bad")
        self.assertEqual(row["relayPositive_quality"], "timeout")
        self.assertEqual(row["id"], "'+bad")
        self.assertEqual(row["firmwareVersion"], "'@bad")
        for name in ("relayNegative", "chargerStatus", "bmsState", "alarm.IMD_Error"):
            self.assertEqual(row[f"{name}_raw"], "")
            self.assertEqual(row[f"{name}_timestamp"], "")
            self.assertEqual(row[f"{name}_quality"], "unknown")

    def test_snapshot_exports_latest_utf8_without_changing_history(self):
        self.current.append(snapshot(0))
        self.current.append(snapshot(1))
        original = self.current.path.read_bytes()
        exported = self.artifact("snapshot")
        self.assertEqual(json.loads(exported.path.read_text(encoding="utf-8")), snapshot(1))
        self.assertIn("当前会话", exported.download_name)
        self.assertEqual(self.current.path.read_bytes(), original)

    def test_full_zip_hashes_exact_whitelisted_bytes_and_preserves_inputs(self):
        self.current.append(snapshot())
        self.current.append_frames([{"id": 0x104, "data": [1, 2]}])
        (self.current.directory / ".env").write_text("secret", encoding="utf-8")
        original = {path.name: path.read_bytes() for path in (self.current.path, self.current.frames_path)}
        exported = self.artifact("full")
        with ZipFile(exported.path) as archive:
            self.assertEqual(set(archive.namelist()), {"snapshots.jsonl", "frames.jsonl", "manifest.json"})
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["formatVersion"], "1.0")
            self.assertEqual(manifest["session"]["name"], "当前会话")
            for item in manifest["files"]:
                raw = archive.read(item["name"])
                self.assertEqual(raw, original[item["name"]])
                self.assertEqual(item["size"], len(raw))
                self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
        for path in (self.current.path, self.current.frames_path):
            self.assertEqual(path.read_bytes(), original[path.name])

    def test_full_export_omits_incomplete_tail_and_records_that_boundary(self):
        self.current.append(snapshot())
        original = self.current.path.read_bytes()
        with self.current.path.open("ab") as stream:
            stream.write(b'{"partial":')
        exported = self.artifact("full")
        with ZipFile(exported.path) as archive:
            self.assertEqual(archive.read("snapshots.jsonl"), original)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["files"][0]["omittedTailBytes"], len(b'{"partial":'))
        self.assertEqual(self.current.path.read_bytes(), original + b'{"partial":')

    def test_empty_session_lists_and_exports_header_or_full_but_not_snapshot(self):
        self.assertEqual(self.library.list_sessions(), [])
        self.current.path.touch()
        rows = self.library.list_sessions()
        self.assertIsNone(rows[0]["first"])
        self.assertEqual(self.library.snapshot_page("current")["items"], [])
        with self.assertRaises(FileNotFoundError):
            self.library.export_session("current", "snapshot")
        exported = self.artifact("summary")
        self.assertEqual(len(exported.path.read_text(encoding="utf-8-sig").splitlines()), 1)
        with ZipFile(self.artifact("full").path) as archive:
            self.assertEqual(set(archive.namelist()), {"snapshots.jsonl", "manifest.json"})

    def test_export_boundary_excludes_concurrent_appends_without_blocking_capture(self):
        self.current.append(snapshot(0))
        original = self.current.path.read_bytes()
        opened, resume = threading.Event(), threading.Event()
        real_open = ZipFile.open
        results, failures = [], []

        def gated_open(archive, name, mode="r", *args, **kwargs):
            if name == "snapshots.jsonl" and mode == "w":
                opened.set()
                if not resume.wait(2):
                    raise TimeoutError("test export boundary wait")
            return real_open(archive, name, mode, *args, **kwargs)

        def export():
            try:
                results.append(self.library.export_session("current", "full"))
            except Exception as error:
                failures.append(error)

        with patch.object(ZipFile, "open", gated_open):
            thread = threading.Thread(target=export)
            thread.start()
            try:
                self.assertTrue(opened.wait(2))
                SessionStore(self.current.directory).append(snapshot(1))
            finally:
                resume.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.addCleanup(lambda: results[0].path.unlink(missing_ok=True))
        with ZipFile(results[0].path) as archive:
            self.assertEqual(archive.read("snapshots.jsonl"), original)
        self.assertEqual(self.current.latest()["id"], "快照-1")

    def test_failed_export_removes_its_temporary_file(self):
        self.current.path.write_text('not json\n' + json.dumps(snapshot()) + '\n', encoding="utf-8")
        created = []
        real_mkstemp = tempfile.mkstemp

        def temporary_output(*args, **kwargs):
            result = real_mkstemp(*args, dir=self.temp.name, **kwargs)
            created.append(Path(result[1]))
            return result

        with patch("services.api_server.sessions.tempfile.mkstemp", temporary_output):
            with self.assertRaises(ValueError):
                self.library.export_session("current", "summary")
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())

    def test_payload_resolving_outside_session_is_rejected(self):
        self.current.path.touch()
        outside = Path(self.temp.name) / "outside.jsonl"
        real_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return outside if path == self.current.path else real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve), self.assertRaisesRegex(ValueError, "escapes"):
            self.library.resolve("current")


if __name__ == "__main__":
    unittest.main()
