from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from services.edge_agent.adapters import FileFrameSource


def frame(sequence: int, *, timestamp: float = 100.0) -> dict:
    return {
        "id": 0x100,
        "bus": "can1",
        "data": [1, 2, 3, 4, 5, 6, 7, 8],
        "dlc": 8,
        "timestamp": timestamp,
        "direction": "rx",
        "sequence": sequence,
        "ide": "standard",
        "rtr": False,
    }


class FileFrameSourceTests(unittest.TestCase):
    def write_lines(self, lines: list[object]) -> Path:
        temp = Path(tempfile.mkdtemp())
        path = temp / "frames.jsonl"
        path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")
        self.addCleanup(lambda: shutil.rmtree(temp, ignore_errors=True))
        return path

    def test_reads_canonical_frames_in_bounded_batches_and_marks_eof(self) -> None:
        source = FileFrameSource(self.write_lines([frame(7), frame(8)]))
        source.open()
        self.addCleanup(source.close)
        self.assertFalse(source.exhausted)
        batch = source.receive(limit=1)
        self.assertEqual([7], [batch[0]["sequence"]])
        self.assertEqual("file", batch[0]["acquisitionSource"])
        self.assertEqual(100.0, batch[0]["timestamp"])
        self.assertEqual([8], [item["sequence"] for item in source.receive(limit=10)])
        self.assertTrue(source.exhausted)
        self.assertEqual([], source.receive())
        self.assertTrue(source.exhausted)

    def test_flattens_snapshot_frames_and_does_not_trust_simulation_quality(self) -> None:
        item = {"id": "snapshot", "frames": [dict(frame(3), qualityMetadataSource="simulation",
                                                     signalQualities={"cellVoltage[0]": "valid"})]}
        source = FileFrameSource(self.write_lines([item]))
        source.open()
        self.addCleanup(source.close)
        result = source.receive()
        self.assertEqual(1, len(result))
        self.assertEqual("file", result[0]["acquisitionSource"])
        self.assertNotIn("qualityMetadataSource", result[0])
        self.assertNotIn("signalQualities", result[0])

    def test_reports_bad_json_and_bad_frame_with_line_number(self) -> None:
        malformed = self.write_lines([frame(1)])
        with malformed.open("a", encoding="utf-8") as stream:
            stream.write("{bad json\n")
        source = FileFrameSource(malformed)
        source.open()
        self.addCleanup(source.close)
        self.assertEqual(1, len(source.receive(limit=1)))
        with self.assertRaisesRegex(ValueError, r"line 2"):
            source.receive()
        illegal = self.write_lines([dict(frame(1), timestamp=float("nan"))])
        source = FileFrameSource(illegal)
        source.open()
        self.addCleanup(source.close)
        with self.assertRaisesRegex(ValueError, r"line 1.*timestamp"):
            source.receive()

    def test_rejects_nonfinite_timestamp_and_illegal_fields(self) -> None:
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(timestamp=bad):
                source = FileFrameSource(self.write_lines([dict(frame(1), timestamp=bad)]))
                source.open()
                self.addCleanup(source.close)
                with self.assertRaises(ValueError):
                    source.receive()
        source = FileFrameSource(self.write_lines([dict(frame(1), dlc=7)]))
        source.open()
        self.addCleanup(source.close)
        with self.assertRaises(ValueError):
            source.receive()

    def test_missing_file_has_actionable_error(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, r"FileFrameSource.*not found"):
            FileFrameSource("missing-capture.jsonl").open()

    def test_large_snapshot_is_split_without_reordering(self) -> None:
        source = FileFrameSource(self.write_lines([{"frames": [frame(index) for index in range(7)]}]))
        source.open()
        self.addCleanup(source.close)
        results = [source.receive(limit=3), source.receive(limit=3), source.receive(limit=3)]
        self.assertEqual([3, 3, 1], [len(batch) for batch in results])
        self.assertEqual(list(range(7)), [item["sequence"] for batch in results for item in batch])
        self.assertTrue(source.exhausted)

    def test_bad_record_does_not_discard_already_read_frames(self) -> None:
        source = FileFrameSource(self.write_lines([frame(1), dict(frame(2), data=[999])]))
        source.open()
        self.addCleanup(source.close)
        self.assertEqual([1], [item["sequence"] for item in source.receive(limit=1000)])
        with self.assertRaisesRegex(ValueError, "line 2"):
            source.receive()
        self.assertFalse(source.exhausted)

    def test_empty_snapshots_and_blank_lines_lead_to_eof(self) -> None:
        path = self.write_lines([{"frames": []}])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n\n")
        source = FileFrameSource(path)
        source.open()
        self.addCleanup(source.close)
        self.assertEqual([], source.receive())
        self.assertTrue(source.exhausted)

    def test_illegal_canonical_fields_are_rejected(self) -> None:
        bad_fields = [("id", -1), ("id", True), ("id", 0x800), ("bus", "CAN1"),
                      ("direction", "up"), ("sequence", True), ("sequence", -1),
                      ("timestamp", True), ("timestamp", -0.1), ("dlc", 9), ("dlc", False),
                      ("data", [0] * 9), ("data", [True] * 8),
                      ("ide", "STD"), ("rtr", 1), ("hardwareTimestamp", -1)]
        for key, value in bad_fields:
            with self.subTest(field=key, value=value):
                source = FileFrameSource(self.write_lines([dict(frame(1), **{key: value})]))
                source.open()
                self.addCleanup(source.close)
                with self.assertRaisesRegex(ValueError, "line 1"):
                    source.receive()

    def test_missing_ide_rtr_are_not_invented_and_remote_dlc_is_preserved(self) -> None:
        legacy = frame(1)
        del legacy["ide"], legacy["rtr"]
        remote = dict(frame(2), rtr=True, data=[], dlc=8)
        source = FileFrameSource(self.write_lines([legacy, remote]))
        source.open()
        self.addCleanup(source.close)
        result = source.receive()
        self.assertNotIn("ide", result[0])
        self.assertNotIn("rtr", result[0])
        self.assertEqual([], result[1]["data"])
        self.assertEqual(8, result[1]["dlc"])

    def test_close_and_explicit_reopen_restarts_source(self) -> None:
        source = FileFrameSource(self.write_lines([frame(9)]))
        with self.assertRaisesRegex(RuntimeError, "not open"):
            source.receive()
        source.open()
        source.receive()
        source.close()
        source.close()
        with self.assertRaisesRegex(RuntimeError, "not open"):
            source.receive()
        source.open()
        self.addCleanup(source.close)
        self.assertEqual(9, source.receive()[0]["sequence"])

    def test_original_sequence_survives_reimport_and_must_be_nonnegative_integer(self) -> None:
        source = FileFrameSource(self.write_lines([dict(frame(20), originalSequence=7)]))
        source.open()
        self.addCleanup(source.close)
        self.assertEqual(7, source.receive()[0]["originalSequence"])
        for sequence in (-1, True, 3.5, "7"):
            source = FileFrameSource(self.write_lines([dict(frame(20), originalSequence=sequence)]))
            source.open()
            self.addCleanup(source.close)
            with self.assertRaisesRegex(ValueError, "originalSequence"):
                source.receive()

    def test_oversized_line_delivers_prior_evidence_then_reports_limit_and_line(self):
        path = self.write_lines([frame(1)])
        with path.open("ab") as stream:
            stream.write(b'{"frames":[],"padding":"' + b"x" * (16 * 1024 * 1024) + b'"}\n')
        original_size = path.stat().st_size
        source = FileFrameSource(path)
        source.open()
        self.addCleanup(source.close)
        self.assertEqual([1], [item["sequence"] for item in source.receive()])
        with self.assertRaisesRegex(ValueError, "line 2.*16777216"):
            source.receive()
        self.assertFalse(source.exhausted)
        self.assertEqual(path.stat().st_size, original_size)

    def test_oversized_frame_array_is_rejected_before_returning_any_of_that_line(self):
        source = FileFrameSource(self.write_lines([frame(1), {"frames": [frame(2)] * 10001}]))
        source.open()
        self.addCleanup(source.close)
        self.assertEqual([1], [item["sequence"] for item in source.receive(limit=100)])
        with self.assertRaisesRegex(ValueError, "line 2.*10000"):
            source.receive()

    def test_utf8_bom_and_multibyte_labels_remain_supported(self):
        path = self.write_lines([])
        path.write_text(json.dumps({"label": "电池记录", "frames": [frame(3)]}, ensure_ascii=False), encoding="utf-8-sig")
        source = FileFrameSource(path)
        source.open()
        self.addCleanup(source.close)
        self.assertEqual([3], [item["sequence"] for item in source.receive()])


if __name__ == "__main__":
    unittest.main()
