import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from services.api_server.server import create_server
from services.llm_agent.provider import MockAiProvider
from services.llm_agent.compatible import ProviderError


class AiApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server(self.directory.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, method="GET", data=None):
        request = Request(self.base + path, method=method, data=data,
                          headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:4173"})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            return response.status, dict(response.headers), json.loads(content) if content else None

    def test_browser_preflight_allows_json_post(self):
        status, headers, _ = self.request("/api/v1/ai/analyze", "OPTIONS")
        self.assertEqual(status, 204)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", headers["Access-Control-Allow-Headers"])

    def test_malformed_input_returns_json_error_and_service_survives(self):
        for raw in (b"{", b"[]", b'{"snapshot":42}', b'{"provider":42}'):
            status, _, body = self.request("/api/v1/ai/analyze", "POST", raw)
            self.assertEqual(status, 400)
            self.assertEqual(body["error"], "invalid_request")
        self.assertEqual(self.request("/api/v1/health")[0], 200)

    def test_unknown_provider_does_not_silently_fall_back(self):
        status, _, body = self.request("/api/v1/ai/analyze", "POST", b'{"provider":"missing"}')
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "provider_not_configured")

    def test_report_identifies_input_snapshot_and_analysis_time(self):
        status, _, body = self.request("/api/v1/ai/analyze", "POST", b"{}")
        self.assertEqual(status, 200)
        self.assertTrue(body["snapshotId"])
        self.assertGreater(body["generatedAt"], 0)
        self.assertTrue(body["ruleVersion"])
        self.assertFalse(body["externalRequest"])

    def test_provider_catalog_contains_no_credentials(self):
        status, _, body = self.request("/api/v1/ai/providers")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["id"], "local-rules")
        self.assertNotIn("apiKey", json.dumps(body))

    def test_old_snapshot_id_rejected_instead_of_analyzing_newer_data(self):
        status, _, body = self.request("/api/v1/ai/analyze", "POST", b'{"snapshotId":"missing"}')
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "snapshot_changed")

    def test_external_provider_requires_explicit_permission(self):
        class External(MockAiProvider):
            external = True

        self.server.shutdown()
        self.server.server_close()
        self.server = create_server(self.directory.name, providers={"custom": External()})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        status, _, body = self.request("/api/v1/ai/analyze", "POST", b'{"provider":"custom"}')
        self.assertEqual(status, 403)
        status, _, body = self.request("/api/v1/ai/analyze", "POST", b'{"provider":"custom","allowRemote":true}')
        self.assertEqual(status, 200)
        self.assertTrue(body["externalRequest"])

    def test_provider_failure_returns_error_without_secrets(self):
        class Failed(MockAiProvider):
            def analyze(self, snapshot):
                raise ProviderError("test-secret-must-not-leak")

        self.server.shutdown()
        self.server.server_close()
        self.server = create_server(self.directory.name, providers={"local-rules": Failed()})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        status, _, body = self.request("/api/v1/ai/analyze", "POST", b"{}")
        self.assertEqual(status, 502)
        self.assertNotIn("test-secret", json.dumps(body))
