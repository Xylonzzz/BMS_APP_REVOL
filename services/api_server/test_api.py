import json
import io
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zipfile import ZipFile
from unittest.mock import patch

from services.api_server.server import create_server, decorate_snapshot
from services.edge_agent.replay_scenarios import build_scenario


class ApiTests(unittest.TestCase):
    def test_corrupt_history_and_export_io_failure_return_json_without_killing_service(self):
        from services.api_server.sessions import SessionLibrary
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = create_server(root / "current")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                for index, record in enumerate([[], {"frames": None}, {"frames": [None]}, {"packStatus": []}]):
                    history = root / f"corrupt-{index}"
                    history.mkdir()
                    path = history / "snapshots.jsonl"
                    contents = json.dumps(record).encode() + b"\n"
                    path.write_bytes(contents)
                    session_id = SessionLibrary._id(history.name)
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + f"/api/v1/sessions/{session_id}/snapshots")
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual(json.load(error.exception)["error"], "invalid_recording")
                    self.assertEqual(path.read_bytes(), contents)
                with patch.object(SessionLibrary, "export_session", side_effect=OSError("disk full secret-path")):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + "/api/v1/sessions/current/export?kind=full")
                    self.assertEqual(error.exception.code, 500)
                    self.assertEqual(json.load(error.exception), {"error": "recording_io_error"})
                with urlopen(base + "/api/v1/health") as response:
                    self.assertEqual(json.load(response)["status"], "ok")
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_session_pages_downloads_and_catalog_preserve_original_records(self):
        from services.edge_agent.session_store import SessionStore
        from services.api_server.sessions import SessionLibrary
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "当前采集"
            history = SessionStore(Path(directory) / "历史记录")
            records = build_scenario("timeout_stale")
            for record in records:
                history.append(record["snapshot"])
            before = history.path.read_bytes()
            server = create_server(current)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            def get(path):
                with urlopen(base + path) as response:
                    return json.load(response)
            artifacts = []
            original = SessionLibrary.export_session
            def track(library, *args):
                artifact = original(library, *args)
                artifacts.append(artifact.path)
                return artifact
            try:
                catalog = get("/api/v1/catalog")["items"]
                self.assertEqual(catalog["initFlag"]["path"], "A03.State.Init_flag")
                sessions = get("/api/v1/sessions")["items"]
                session = next(item["id"] for item in sessions if item["name"] == "历史记录")
                prefix = "/api/v1/sessions/" + session
                first = get(prefix + "/snapshots?offset=0&limit=1")
                self.assertEqual(first["nextOffset"], 1)
                last = get(prefix + "/snapshots?offset=1&limit=200")
                self.assertIsNone(last["nextOffset"])
                self.assertEqual(last["items"][-1]["totalVoltage"]["quality"], "timeout")
                for bad in [prefix + "/snapshots?limit=201", prefix + "/snapshots?offset=-1",
                            prefix + "/snapshots?limit=1&limit=2", prefix + "/export?kind=bad",
                            "/api/v1/sessions/..%2foutside/snapshots"]:
                    with self.assertRaises(HTTPError) as error:
                        get(bad)
                    self.assertEqual(error.exception.code, 400)
                with patch.object(SessionLibrary, "export_session", track):
                    with urlopen(base + prefix + "/export?kind=full") as response:
                        self.assertIn("filename*=UTF-8", response.headers["Content-Disposition"])
                        with ZipFile(io.BytesIO(response.read())) as archive:
                            self.assertEqual(archive.read("snapshots.jsonl"), before)
                            self.assertIn("manifest.json", archive.namelist())
                    with urlopen(base + prefix + "/export?kind=summary") as response:
                        self.assertIn("totalVoltageAdc_V", response.read().decode("utf-8-sig"))
                self.assertEqual(history.path.read_bytes(), before)
            finally:
                server.shutdown(); server.server_close(); thread.join()
            self.assertTrue(artifacts)
            self.assertTrue(all(not path.exists() for path in artifacts))

    def test_live_snapshot_preserves_aged_quality_instead_of_redecoding_as_fresh(self):
        from packages.bms_protocol.aggregator import SnapshotAggregator
        from packages.bms_protocol.test_aggregator import frame
        aggregate = SnapshotAggregator()
        aggregate.ingest(frame(0x100))
        source = aggregate.snapshot(timestamp=104, connected=False)
        result = decorate_snapshot(source)
        self.assertEqual(result["packStatus"]["current"]["quality"], "disconnected")
        self.assertEqual(result["frames"][0]["decoded"][0]["quality"], "unknown")

    def test_live_server_never_seeds_simulated_data_and_serves_web(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(directory, mode="file")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(base + "/api/v1/snapshots/latest") as response:
                    snapshot = json.load(response)
                self.assertEqual(snapshot["dataSource"], "file")
                self.assertIsNone(snapshot["cells"][0]["value"])
                self.assertEqual(snapshot["connectionState"], "disconnected")
                with urlopen(base + "/api/v1/capture") as response:
                    self.assertEqual(json.load(response)["state"], "idle")
                with urlopen(base + "/") as response:
                    self.assertIn(b"REVOL", response.read())
            finally:
                server.shutdown(); server.server_close()

    def test_pack_status_is_decoded_when_frames_exist_without_rewriting_input(self):
        snapshot = build_scenario("valid")[0]["snapshot"]
        expected = snapshot.pop("packStatus")
        original = deepcopy(snapshot)
        self.assertEqual(decorate_snapshot(snapshot)["packStatus"], expected)
        self.assertEqual(snapshot, original)

    def test_historical_cell_sum_does_not_become_adc_or_precharge_voltage(self):
        snapshot = build_scenario("valid")[0]["snapshot"]
        snapshot.pop("packStatus")
        snapshot["frames"] = [frame for frame in snapshot["frames"] if frame["id"] not in {0x100, 0x101}]
        self.assertNotIn("packStatus", decorate_snapshot(snapshot))

    def test_offline_pack_status_retains_original_sampling_time_and_quality(self):
        snapshot = build_scenario("disconnected_reconnect")[1]["snapshot"]
        result = decorate_snapshot(snapshot)
        self.assertEqual(result["packStatus"], snapshot["packStatus"])
        self.assertEqual(result["packStatus"]["totalVoltageAdc"]["quality"], "disconnected")
        self.assertLess(result["packStatus"]["totalVoltageAdc"]["timestamp"], result["timestamp"])

    def test_legacy_snapshot_byte_decode_does_not_claim_sampling_validity(self):
        snapshot = build_scenario("pec_error")[0]["snapshot"]
        snapshot["firmwareVersion"] = "BMS_SIM_0.1.0"
        for frame in snapshot["frames"]:
            frame.pop("qualityMetadataSource")
            frame.pop("signalQualities")
        original = deepcopy(snapshot)
        result = decorate_snapshot(snapshot)
        pec_frame = next(frame for frame in result["frames"] if frame["id"] == snapshot["cells"][18]["rawFrameIds"][0])
        signal = next(signal for signal in pec_frame["decoded"] if signal["name"] == "cellVoltage[18]")
        self.assertEqual(signal["quality"], "unknown")
        self.assertIn("采样质量未知", signal["meaning"])
        self.assertEqual(snapshot, original)

    def test_legacy_quality_fallback_preserves_partial_and_range_errors(self):
        snapshot = build_scenario("valid")[0]["snapshot"]
        snapshot["firmwareVersion"] = "BMS_REPLAY_0.1.0"
        for frame in snapshot["frames"]:
            frame.pop("qualityMetadataSource")
            frame.pop("signalQualities")
        snapshot["frames"][0]["dlc"] = 2
        snapshot["frames"][1]["data"][0] = 256
        result = decorate_snapshot(snapshot)
        self.assertEqual(result["frames"][0]["decoded"][0]["quality"], "partial")
        self.assertEqual(result["frames"][1]["decoded"][0]["quality"], "range_error")

    def test_current_snapshot_keeps_explicit_simulated_pec_quality(self):
        result = decorate_snapshot(build_scenario("pec_error")[0]["snapshot"])
        signals = [signal for frame in result["frames"] for signal in frame["decoded"]]
        self.assertEqual(next(signal for signal in signals if signal["name"] == "cellVoltage[18]")["quality"], "pec_error")

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
                self.assertEqual(snapshot["cells"][18]["quality"], "pec_error")
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
                record = af["items"][0]
                self.assertEqual(record["frames"], record["snapshot"]["frames"])
                self.assertIn("decoded", record["frames"][0])
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
