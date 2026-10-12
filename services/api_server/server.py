from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.edge_agent.session_store import SessionStore
from services.edge_agent.simulator import Simulator
from services.edge_agent.replay_scenarios import SCENARIO_NAMES, build_scenario
from packages.bms_protocol.decoder import decode_can1_frame
from services.llm_agent.provider import MockAiProvider, analysis_to_dict
from services.llm_agent.compatible import load_providers, ProviderError


def ensure_demo_data(store: SessionStore) -> None:
    latest = store.latest()
    if latest is None or "modelDetection" not in latest:
        simulator = Simulator()
        simulator.inject_pec_error(17)
        store.append(simulator.next_snapshot())


def decorate_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    result = dict(snapshot)
    result["frames"] = [dict(frame, decoded=decode_can1_frame(frame, direction="up")) for frame in snapshot.get("frames", [])]
    return result


def create_server(data_dir: str | Path, port: int = 0, providers=None) -> ThreadingHTTPServer:
    store = SessionStore(data_dir)
    ensure_demo_data(store)
    providers = providers if providers is not None else {"local-rules": MockAiProvider()}

    class ApiHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/v1/ai/providers":
                self._send(200, {"items": [
                    {"id": name, "model": provider.model,
                     "external": getattr(provider, "external", False)}
                    for name, provider in providers.items()
                ]})
                return
            if path == "/api/v1/health":
                self._send(200, {"status": "ok", "service": "bms-api", "readOnly": True})
                return
            if path == "/api/v1/snapshots/latest":
                latest = decorate_snapshot(store.latest())
                self._send(200, latest or {"snapshot": None})
                return
            if path == "/api/v1/snapshots":
                self._send(200, {"items": [decorate_snapshot(item) for item in store.list_snapshots()]})
                return
            if path == "/api/v1/diagnostics":
                events = [event for item in store.list_snapshots() for event in item.get("diagnostics", [])]
                self._send(200, {"items": events})
                return
            if path == "/api/v1/schema":
                schema_path = ROOT / "schemas" / "signal-tree.json"
                self._send(200, json.loads(schema_path.read_text(encoding="utf-8-sig")))
                return
            if path == "/api/v1/replay/scenarios":
                self._send(200, {"items": list(SCENARIO_NAMES)})
                return
            replay_prefix = "/api/v1/replay/scenarios/"
            if path.startswith(replay_prefix):
                name = path[len(replay_prefix):]
                if name not in SCENARIO_NAMES:
                    self._send(404, {"error": "unknown_replay_scenario", "scenario": name})
                    return
                self._send(200, {"name": name, "items": build_scenario(name)})
                return
            self._send(404, {"error": "not_found", "path": path})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/v1/ai/analyze":
                self._send(404, {"error": "not_found", "path": path})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 8192:
                    self._send(413, {"error": "request_too_large"})
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict) or set(body) - {"provider", "snapshotId", "allowRemote"}:
                    raise ValueError("invalid fields")
                if not isinstance(body.get("provider", "local-rules"), str):
                    raise ValueError("invalid provider")
                if "snapshotId" in body and not isinstance(body["snapshotId"], str):
                    raise ValueError("invalid snapshotId")
                if not isinstance(body.get("allowRemote", False), bool):
                    raise ValueError("invalid allowRemote")
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"error": "invalid_request"})
                return
            provider = providers.get(body.get("provider", "local-rules"))
            if provider is None:
                self._send(400, {"error": "provider_not_configured"})
                return
            external = getattr(provider, "external", False)
            if external and body.get("allowRemote") is not True:
                self._send(403, {"error": "remote_analysis_not_allowed"})
                return
            snapshot = decorate_snapshot(store.latest())
            if snapshot is None:
                self._send(404, {"error": "snapshot_not_found"})
                return
            if "snapshotId" in body and body["snapshotId"] != snapshot["id"]:
                self._send(409, {"error": "snapshot_changed"})
                return
            try:
                result = analysis_to_dict(provider.analyze(snapshot))
            except ProviderError:
                self._send(502, {"error": "provider_failed", "message": "模型服务未返回有效结果，请检查配置或稍后重试"})
                return
            result.update({"snapshotId": snapshot["id"], "generatedAt": time.time(),
                           "ruleVersion": "quality-review-0.1", "externalRequest": external,
                           "limitations": ["仅分析本次快照，未接入完整 H/A 文档检索或真实硬件验证"]})
            self._send(200, result)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer(("127.0.0.1", port), ApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="BMS APP REVOL read-only demo API")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=str(ROOT / "runtime" / "demo"))
    args = parser.parse_args()
    store = SessionStore(args.data_dir)
    ensure_demo_data(store)
    server = create_server(args.data_dir, args.port, load_providers())
    print(f"BMS API listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
