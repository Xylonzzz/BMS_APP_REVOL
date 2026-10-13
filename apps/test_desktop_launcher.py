from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from apps.desktop_launcher import LauncherSettings, SessionController, load_settings, save_settings, run_self_test
from services.edge_agent.capture import CaptureWorker


class FakeSource:
    def __init__(self, fail_open=False):
        self.opened = False
        self.closed = False
        self.fail_open = fail_open

    def open(self):
        self.opened = True
        if self.fail_open:
            raise OSError("adapter unavailable")

    def receive(self, **kwargs):
        time.sleep(0.005)
        return []

    def close(self):
        self.closed = True


class DesktopLauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.stop()
        self.directory.cleanup()

    def controller(self, **kwargs):
        controller = SessionController(self.root, **kwargs)
        self.controllers.append(controller)
        return controller

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("background operation did not complete")

    def test_simulation_starts_without_creating_or_opening_hardware(self):
        def forbidden_source(_):
            self.fail("simulation must not construct an adapter")

        controller = self.controller(source_factory=forbidden_source)
        self.assertFalse(controller.active)
        url = controller.start(LauncherSettings())
        with urlopen(url + "api/v1/health", timeout=2) as response:
            health = json.load(response)
        self.assertEqual(health["dataSource"], "simulation")
        with urlopen(url, timeout=2) as response:
            self.assertIn(b"REVOL", response.read())
        self.assertTrue(controller.active)
        controller.stop()
        self.assertFalse(controller.active)

    def test_new_sessions_are_unique_and_previous_evidence_is_preserved(self):
        controller = self.controller()
        controller.start(LauncherSettings())
        first = Path(controller.status()["sessionDirectory"])
        evidence = (first / "snapshots.jsonl").read_bytes()
        controller.stop()
        controller.start(LauncherSettings())
        second = Path(controller.status()["sessionDirectory"])
        self.assertNotEqual(first, second)
        self.assertEqual((first / "snapshots.jsonl").read_bytes(), evidence)
        self.assertEqual(first.parent, self.root / "sessions")

    def test_occupied_port_fails_before_adapter_open_and_leaves_no_api(self):
        source = FakeSource()
        controller = self.controller(source_factory=lambda _: source)
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            with self.assertRaises(OSError):
                controller.start(LauncherSettings(mode="can", dll_path="test-only.dll"),
                                 port=blocker.getsockname()[1])
        self.assertFalse(source.opened)
        self.assertFalse(controller.active)
        self.assertTrue(controller.status()["error"])

    def test_file_mode_imports_without_modifying_input_and_exposes_failure_state(self):
        source_file = self.root / "input.jsonl"
        raw = {"id": 0x100, "bus": "can1", "direction": "rx", "ide": "standard",
               "rtr": False, "dlc": 8, "timestamp": 1000, "sequence": 7,
               "data": [184, 11, 0, 0, 196, 19, 198, 19]}
        content = json.dumps(raw).encode() + b"\n"
        source_file.write_bytes(content)
        controller = self.controller()
        url = controller.start(LauncherSettings(mode="file", input_path=str(source_file)))
        self.wait_until(lambda: controller.status()["capture"]["state"] == "completed")
        with urlopen(url + "api/v1/snapshots/latest", timeout=2) as response:
            snapshot = json.load(response)
        self.assertEqual(snapshot["dataSource"], "file")
        self.assertEqual(snapshot["packStatus"]["current"]["value"], 3)
        self.assertEqual(source_file.read_bytes(), content)
        self.assertTrue((Path(controller.status()["sessionDirectory"]) / "frames.jsonl").exists())

    def test_adapter_open_error_is_visible_and_source_is_closed(self):
        source = FakeSource(fail_open=True)
        controller = self.controller(source_factory=lambda _: source)
        controller.start(LauncherSettings(mode="can", dll_path="test-only.dll"))
        self.wait_until(lambda: controller.status()["capture"]["state"] == "error")
        self.assertIn("adapter unavailable", controller.status()["error"])
        self.assertTrue(source.closed)
        controller.stop()
        self.assertFalse(controller.active)

    def test_worker_start_failure_releases_bound_http_port(self):
        class BrokenWorker(CaptureWorker):
            def start(self):
                raise RuntimeError("thread creation failed")

        source = FakeSource()
        controller = self.controller(source_factory=lambda _: source, worker_factory=BrokenWorker)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with self.assertRaisesRegex(RuntimeError, "thread creation failed"):
            controller.start(LauncherSettings(mode="can", dll_path="test-only.dll"), port=port)
        with socket.socket() as check:
            check.bind(("127.0.0.1", port))
        self.assertFalse(controller.active)
        self.assertTrue(source.closed)

    def test_stop_releases_source_and_thread_then_is_idempotent(self):
        source = FakeSource()
        controller = self.controller(source_factory=lambda _: source)
        controller.start(LauncherSettings(mode="can", dll_path="test-only.dll"))
        self.wait_until(lambda: source.opened)
        controller.stop()
        controller.stop()
        self.assertTrue(source.closed)
        self.assertFalse(controller.active)
        self.assertFalse(any(thread.is_alive() and thread.name == controller.thread_name
                             for thread in threading.enumerate()))

    def test_missing_input_is_rejected_without_creating_a_session(self):
        controller = self.controller()
        with self.assertRaises(ValueError):
            controller.start(LauncherSettings(mode="file"))
        self.assertFalse((self.root / "sessions").exists())
        self.assertFalse(controller.active)

    def test_saved_preferences_keep_paths_but_always_restore_simulation_mode(self):
        path = self.root / "settings.json"
        settings = LauncherSettings(mode="can", input_path="capture.jsonl", dll_path="sdk.dll",
                                    bitrate=250000, firmware_version="user-label")
        save_settings(path, settings)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["apiKey"] = "must-not-load"
        path.write_text(json.dumps(payload), encoding="utf-8")
        restored = load_settings(path)
        self.assertEqual(restored.mode, "simulation")
        self.assertEqual(restored.dll_path, "sdk.dll")
        self.assertEqual(restored.bitrate, 250000)
        self.assertFalse(hasattr(restored, "apiKey"))

    def test_portable_self_test_checks_resources_then_releases_api_without_hardware(self):
        result_path = self.root / "self-test.json"
        with patch("services.edge_agent.controlcan.ControlCanSource.open", side_effect=AssertionError("hardware forbidden")) as hardware:
            with patch("webbrowser.open", side_effect=AssertionError("browser forbidden")) as browser:
                result = run_self_test(result_path)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["portClosed"])
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result)
        self.assertEqual(set(result["checks"]), {"health", "index", "schema", "batteryIcon"})
        hardware.assert_not_called()
        browser.assert_not_called()

    def test_self_test_requires_absolute_report_path(self):
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            run_self_test("relative-result.json")


if __name__ == "__main__":
    unittest.main()
