from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import urlopen

from apps.desktop.sidecar.host import SidecarHost, serve


class SidecarHostTests(unittest.TestCase):
    def test_start_status_stop_manage_api_with_no_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SidecarHost(Path(directory))
            initial = host.handle({"id": 1, "command": "status"})
            self.assertFalse(initial["result"]["active"])
            start = host.handle({"id": 2, "command": "start", "settings": {"mode": "simulation"}})
            self.assertTrue(start["ok"], start)
            url = start["result"]["url"]
            with urlopen(url + "api/v1/health", timeout=2) as response:
                self.assertEqual(json.load(response)["dataSource"], "simulation")
            stop = host.handle({"id": 3, "command": "stop"})
            self.assertTrue(stop["ok"])
            self.assertFalse(stop["result"]["active"])

    def test_stdin_eof_stops_current_session_and_returns_one_response_per_request(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SidecarHost(Path(directory))
            source = io.StringIO(json.dumps({"id": 4, "command": "start", "settings": {"mode": "simulation"}}) + "\n")
            output = io.StringIO()
            serve(host, source, output)
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["id"], 4)
            self.assertTrue(records[0]["ok"])
            self.assertFalse(host.controller.active)

    def test_unknown_command_and_secret_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SidecarHost(Path(directory))
            self.assertFalse(host.handle({"id": 5, "command": "send_can"})["ok"])
            self.assertFalse(host.handle({"id": 6, "command": "start", "settings": {"api_key": "secret"}})["ok"])
            self.assertFalse(host.controller.active)

    def test_load_settings_never_selects_can_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SidecarHost(Path(directory))
            response = host.handle({"id": 1, "command": "settings"})
            self.assertEqual(response["result"]["mode"], "simulation")


if __name__ == "__main__":
    unittest.main()
