import json
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from services.api_server.server import create_server


class ApiTests(unittest.TestCase):
    def test_read_only_endpoints_expose_quality_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(base + "/api/v1/health") as response:
                    self.assertEqual(json.load(response)["status"], "ok")
                with urlopen(base + "/api/v1/snapshots/latest") as response:
                    snapshot = json.load(response)
                self.assertEqual(snapshot["cells"][17]["quality"], "pec_error")
                self.assertEqual(snapshot["frames"][0]["decoded"][0]["name"], "cellVoltage[0]")
                with urlopen(base + "/api/v1/diagnostics") as response:
                    events = json.load(response)["items"]
                self.assertTrue(any(item["code"] == "CELL_PEC_ERROR" for item in events))
                request = Request(base + "/api/v1/ai/analyze", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    analysis = json.load(response)
                self.assertEqual(analysis["provider"], "local-rules")
                self.assertEqual(analysis["suggestedChain"], "H01/A01")
            finally:
                server.shutdown()
                server.server_close()

    def test_replay_endpoints_expose_quality_and_model_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(base + "/api/v1/replay/scenarios") as response:
                    scenarios = json.load(response)["items"]
                self.assertIn("af", scenarios)
                self.assertIn("timeout_stale", scenarios)
                with urlopen(base + "/api/v1/replay/scenarios/af") as response:
                    af = json.load(response)
                self.assertEqual(af["items"][0]["snapshot"]["modelDetection"]["model"], "af")
                with urlopen(base + "/api/v1/replay/scenarios/timeout_stale") as response:
                    timeout = json.load(response)
                self.assertEqual(timeout["items"][-1]["snapshot"]["totalVoltage"]["quality"], "timeout")
                with self.assertRaises(Exception):
                    urlopen(base + "/api/v1/replay/scenarios/not-a-scenario")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
