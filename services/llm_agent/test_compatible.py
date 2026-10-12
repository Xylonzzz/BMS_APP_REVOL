import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.edge_agent.simulator import Simulator
from services.llm_agent.compatible import CompatibleProvider, ProviderError


class CompatibleTests(unittest.TestCase):
    def test_model_request_and_json_output_over_local_http(self):
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                result = {"summary": "待验证的采集质量异常", "severity": "warning", "evidence": ["PEC错误"],
                          "suggestedChain": "H01/A01", "nextChecks": ["核对原始数据"]}
                payload = json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = CompatibleProvider(f"http://127.0.0.1:{server.server_port}/chat/completions", "test-model")
            snapshot = Simulator().next_snapshot()
            result = provider.analyze(snapshot)
            self.assertEqual(result.model, "test-model")
            self.assertEqual(observed[0]["response_format"], {"type": "json_object"})
            evidence = json.loads(observed[0]["messages"][1]["content"])
            self.assertEqual(evidence["snapshotId"], snapshot["id"])
            self.assertNotIn("frames", evidence)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_unsafe_endpoint_rejected(self):
        for url in ("http://example.com/chat", "https://user:secret@example.com/chat", "https://example.com/chat?key=secret"):
            with self.assertRaises(ValueError):
                CompatibleProvider(url, "model")
