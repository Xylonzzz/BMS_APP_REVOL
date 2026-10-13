from collections import deque
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from services.edge_agent.capture import CaptureWorker
from services.edge_agent.adapters import FileFrameSource
from services.edge_agent.session_store import SessionStore


def frame(sequence=1, timestamp=100.0):
    return dict(id=0x104, timestamp=timestamp, sequence=sequence, bus="can1", direction="rx",
                ide="standard", rtr=False, dlc=8, data=[0x70, 0x0E] * 4)


class FakeSource:
    def __init__(self, frames=(), *, finite=False, error=None):
        self.frames = deque(frames)
        self.finite = finite
        self.error = error
        self.exhausted = False
        self.opened = False
        self.closed = False
        self.calls = 0
        self.limits = []

    def open(self):
        self.opened = True

    def receive(self, limit=1000, timeout_ms=100):
        self.calls += 1
        self.limits.append(limit)
        if not self.frames and self.error:
            raise self.error
        result = [self.frames.popleft() for _ in range(min(limit, len(self.frames)))]
        self.exhausted = self.finite and not self.frames
        return result

    def close(self):
        self.closed = True


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SessionStore(self.directory.name)

    def wait_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() >= deadline:
                self.fail("capture condition timed out")
            time.sleep(0.005)

    def raw_frames(self):
        return [json.loads(line) for line in (Path(self.directory.name) / "frames.jsonl")
                .read_text(encoding="utf-8").splitlines()]

    def test_file_uses_original_time_bounded_batches_and_host_sequences(self):
        inputs = [frame(sequence=index % 2, timestamp=100 + index / 1000) for index in range(205)]
        original = deepcopy(inputs)
        source = FakeSource(inputs, finite=True)
        worker = CaptureWorker(source, self.store, data_source="file", interval=0.02)
        self.assertEqual(worker.status()["state"], "idle")
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "completed")
        records = self.store.list_snapshots()
        self.assertEqual([len(record["frames"]) for record in records], [100, 100, 5])
        self.assertEqual(records[-1]["timestamp"], 100.204)
        self.assertEqual(records[-1]["cells"][0]["quality"], "unknown")
        self.assertEqual(records[-1]["connectionState"], "connected")
        raw = self.raw_frames()
        self.assertEqual([item["sequence"] for item in raw], list(range(1, 206)))
        self.assertEqual([item["originalSequence"] for item in raw], [index % 2 for index in range(205)])
        self.assertEqual(inputs, original)
        self.assertTrue(source.opened and source.closed)
        self.assertEqual(worker.status()["receivedFrames"], 205)
        self.assertEqual(worker.status()["dataSource"], "file")
        self.assertIsNone(worker.status()["error"])
        worker.stop()
        self.assertEqual(worker.status()["state"], "completed")

    def test_idle_receive_ages_data_and_stop_keeps_old_evidence(self):
        source = FakeSource([frame(timestamp=time.time())])
        worker = CaptureWorker(source, self.store, interval=0.02, stale_after=0.05)
        worker.start()
        self.addCleanup(worker.stop)
        self.wait_until(lambda: self.store.latest() is not None
                        and self.store.latest()["cells"][0]["quality"] == "timeout")
        worker.stop()
        latest = self.store.latest()
        self.assertEqual(worker.status()["state"], "stopped")
        self.assertEqual(latest["connectionState"], "disconnected")
        self.assertEqual(latest["cells"][0]["quality"], "disconnected")
        self.assertEqual(latest["cells"][0]["value"], 3696)
        self.assertEqual(latest["frames"], [])
        self.assertEqual(len(latest["evidenceFrames"]), 1)
        self.assertEqual(len(self.raw_frames()), 1)
        self.assertLess(source.calls, 100)
        self.assertTrue(source.closed)

    def test_receive_error_flushes_received_frames_then_disconnects(self):
        source = FakeSource([frame(timestamp=time.time())], error=OSError("USB unplugged"))
        worker = CaptureWorker(source, self.store, interval=10)
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "error")
        worker.stop()
        self.assertIn("USB unplugged", worker.status()["error"])
        records = self.store.list_snapshots()
        self.assertEqual(len(records[0]["frames"]), 1)
        self.assertEqual(records[-1]["frames"], [])
        self.assertEqual(records[-1]["connectionState"], "disconnected")
        self.assertTrue(source.closed)

    def test_raw_evidence_is_persisted_before_aggregation_validation(self):
        source = FakeSource([frame(timestamp=-1)], finite=True)
        worker = CaptureWorker(source, self.store, data_source="file")
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "error")
        worker.stop()
        self.assertEqual(self.raw_frames()[0]["timestamp"], -1)
        self.assertIn("receive time", worker.status()["error"])
        self.assertTrue(source.closed)

    def test_open_failure_is_explicit_and_closes_partial_resources(self):
        class BrokenSource(FakeSource):
            def open(self):
                raise OSError("open failed")

        source = BrokenSource()
        worker = CaptureWorker(source, self.store)
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "error")
        worker.stop()
        self.assertIn("open failed", worker.status()["error"])
        self.assertTrue(source.closed)
        self.assertEqual(self.store.latest()["connectionState"], "disconnected")

    def test_write_failure_stops_capture_instead_of_silently_losing_frames(self):
        class BrokenStore(SessionStore):
            def append_frames(self, frames):
                raise OSError("disk full")

        source = FakeSource([frame(timestamp=time.time())])
        worker = CaptureWorker(source, BrokenStore(self.directory.name))
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "error")
        worker.stop()
        self.assertIn("disk full", worker.status()["error"])
        self.assertEqual(worker.status()["receivedFrames"], 1)
        self.assertEqual(self.store.latest()["frames"], [])
        self.assertTrue(source.closed)

    def test_file_error_preserves_prior_evidence_and_original_input(self):
        path = Path(self.directory.name) / "input.jsonl"
        content = (json.dumps({"frames": [frame(), frame(sequence=1)]}) + "\ninvalid\n").encode()
        path.write_bytes(content)
        worker = CaptureWorker(FileFrameSource(path), self.store, data_source="file")
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "error")
        worker.stop()
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(len(self.raw_frames()), 2)
        self.assertIn("line 2", worker.status()["error"])
        self.assertEqual(self.store.latest()["cells"][0]["value"], 3696)
        self.assertEqual(self.store.latest()["frames"], [])

    def test_empty_file_completes_once_without_spinning(self):
        source = FakeSource(finite=True)
        worker = CaptureWorker(source, self.store, data_source="file")
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "completed")
        self.assertEqual(source.calls, 1)
        self.assertEqual(len(self.store.list_snapshots()), 1)
        self.assertIsNone(self.store.latest()["cells"][0]["value"])

    def test_close_failure_is_reported_even_after_file_eof(self):
        class BrokenCloseSource(FakeSource):
            def close(self):
                raise OSError("close failed")

        worker = CaptureWorker(BrokenCloseSource([frame()], finite=True), self.store, data_source="file")
        worker.start()
        self.wait_until(lambda: worker.status()["state"] == "error")
        worker.stop()
        self.assertIn("close failed", worker.status()["error"])
        self.assertEqual(self.store.latest()["connectionState"], "disconnected")

    def test_stop_timeout_is_reported_and_not_erased_by_late_thread_exit(self):
        entered, release = threading.Event(), threading.Event()

        class BlockingSource(FakeSource):
            def receive(self, **kwargs):
                entered.set()
                release.wait(2)
                return []

        source = BlockingSource()
        worker = CaptureWorker(source, self.store, interval=0.02)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(TimeoutError):
                worker.stop(timeout=0.01)
            self.assertEqual(worker.status()["state"], "error")
        finally:
            release.set()
            worker.stop()
        self.assertIn("stop", worker.status()["error"])
        self.assertEqual(worker.status()["state"], "error")
        self.assertTrue(source.closed)

    def test_file_input_cannot_alias_either_output_even_when_empty(self):
        for output in (self.store.path, self.store.frames_path):
            output.touch()
            with self.subTest(output=output.name), self.assertRaisesRegex(ValueError, "input.*output"):
                CaptureWorker(FileFrameSource(output), self.store, data_source="file")
            self.assertEqual(output.read_bytes(), b"")

    def test_hardlinked_input_output_is_rejected_without_changing_recording(self):
        path = Path(self.directory.name) / "recording.jsonl"
        original = (json.dumps(frame()) + "\n").encode()
        path.write_bytes(original)
        try:
            os.link(path, self.store.frames_path)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "input.*output"):
            CaptureWorker(FileFrameSource(path), self.store, data_source="file")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.store.frames_path.read_bytes(), original)

    def test_start_rejects_existing_recordings_and_keeps_source_closed(self):
        for output in (self.store.path, self.store.frames_path):
            source = FakeSource([frame()], finite=True)
            worker = CaptureWorker(source, self.store, data_source="file")
            content = (json.dumps({"id": "prior"}) + "\n").encode()
            output.write_bytes(content)
            with self.subTest(output=output.name), self.assertRaisesRegex(ValueError, "empty session"):
                worker.start()
            self.assertFalse(source.opened)
            self.assertEqual(output.read_bytes(), content)
            self.assertEqual(worker.status()["state"], "idle")
            output.unlink()

    def test_start_rechecks_input_alias_created_after_construction(self):
        path = Path(self.directory.name) / "recording.jsonl"
        path.touch()
        worker = CaptureWorker(FileFrameSource(path), self.store, data_source="file")
        try:
            os.link(path, self.store.frames_path)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "input.*output"):
            worker.start()
        self.assertEqual(path.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
