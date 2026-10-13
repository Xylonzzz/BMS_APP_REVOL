from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(ROOT))
from services.edge_agent.session_store import SessionStore
from services.edge_agent.simulator import Simulator
from services.edge_agent.replay_scenarios import SCENARIO_NAMES, build_scenario
from packages.bms_protocol.decoder import decode_can1_frame
from packages.bms_protocol.pack_status import pack_status_from_frames
from packages.bms_protocol.aggregator import SnapshotAggregator
from packages.bms_protocol.telemetry import SIGNAL_CATALOG
from services.api_server.sessions import SessionLibrary
from services.llm_agent.provider import MockAiProvider, analysis_to_dict
from services.llm_agent.compatible import load_providers, ProviderError


def ensure_demo_data(store: SessionStore) -> None:
    latest = store.latest()
    if latest is None or "modelDetection" not in latest:
        simulator = Simulator()
        simulator.inject_pec_error(18)
        store.append(simulator.next_snapshot())


def decorate_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot record must be an object")
    for name in ("frames", "evidenceFrames", "cells", "temperatures", "diagnostics"):
        if name in snapshot and (not isinstance(snapshot[name], list)
                                 or any(not isinstance(item, dict) for item in snapshot[name])):
            raise ValueError(f"snapshot {name} must contain objects")
    for name in ("packStatus", "signals"):
        if name in snapshot and (not isinstance(snapshot[name], dict)
                                 or any(not isinstance(item, dict) for item in snapshot[name].values())):
            raise ValueError(f"snapshot {name} must map to objects")
    result = dict(snapshot)
    external = snapshot.get("dataSource") in {"can", "file"}
    def decorate_frame(frame):
        value = dict(frame)
        if external:
            value["acquisitionSource"] = snapshot["dataSource"]
        return dict(value, decoded=decode_can1_frame(value))
    result["frames"] = [decorate_frame(frame) for frame in snapshot.get("frames", [])]
    if "evidenceFrames" in snapshot:
        result["evidenceFrames"] = [decorate_frame(frame) for frame in snapshot["evidenceFrames"]]
    legacy_simulation = str(snapshot.get("firmwareVersion", "")).startswith(("BMS_SIM_0.1", "BMS_REPLAY_0.1"))
    if legacy_simulation:
        for frame in result["frames"]:
            if frame.get("qualityMetadataSource") == "simulation" and isinstance(frame.get("signalQualities"), dict):
                continue
            for signal in frame["decoded"]:
                if signal["quality"] == "valid":
                    signal["quality"] = "unknown"
                    signal["meaning"] += "；历史模拟帧仅字节可解析，采样质量未知"
    pack_status = dict(snapshot.get("packStatus", {}))
    # 实时聚合器已处理测点新鲜度；重新解码不可把超时/断线旧值升级。
    if not external:
        pack_status.update(pack_status_from_frames(result["frames"], legacy_simulation=legacy_simulation))
    if pack_status:
        result["packStatus"] = pack_status
    return result


def create_server(data_dir: str | Path, port: int = 0, providers=None, *, mode="simulation", worker=None) -> ThreadingHTTPServer:
    if mode not in {"simulation", "can", "file"}:
        raise ValueError("invalid source mode")
    store = SessionStore(data_dir)
    library = SessionLibrary(Path(data_dir).resolve().parent, data_dir)
    latest = store.latest()
    if latest is not None and latest.get("dataSource", "simulation") != mode:
        raise ValueError("data directory belongs to a different source; select a new session directory")
    if mode == "simulation":
        ensure_demo_data(store)
    providers = providers if providers is not None else {"local-rules": MockAiProvider()}

    def latest_snapshot():
        latest = store.latest()
        if latest is None and mode != "simulation":
            latest = SnapshotAggregator(data_source=mode).snapshot(connected=False)
        return decorate_snapshot(latest)

    class ApiHandler(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            self._headers_sent = True
            super().end_headers()

        def _send(self, status: int, payload: Any) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
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
            self._headers_sent = False
            try:
                self._get()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except (ValueError, TypeError, AttributeError, OSError) as error:
                self.log_error("recording request failed: %s", error)
                if self._headers_sent:
                    # 下载已开始时只能终止该流；禁止将第二份HTTP响应拼入证据文件。
                    self.close_connection = True
                else:
                    status = 404 if isinstance(error, FileNotFoundError) else 500 if isinstance(error, OSError) else 400
                    code = {404: "session_not_found", 500: "recording_io_error", 400: "invalid_recording"}[status]
                    self._send(status, {"error": code})

        def _get(self) -> None:
            request = urlparse(self.path)
            path = request.path
            if path == "/api/v1/catalog":
                self._send(200, {"items": SIGNAL_CATALOG})
                return
            if path == "/api/v1/sessions":
                self._send(200, {"items": library.list_sessions()})
                return
            if path.startswith("/api/v1/sessions/"):
                parts = path.split("/")
                if len(parts) != 6 or parts[5] not in {"snapshots", "export"}:
                    self._send(404, {"error": "not_found"})
                    return
                query = parse_qs(request.query, keep_blank_values=True)
                artifact = None
                try:
                    if any(len(value) != 1 for value in query.values()):
                        raise ValueError("duplicate query parameter")
                    if parts[5] == "snapshots":
                        if set(query) - {"offset", "limit"}:
                            raise ValueError("unexpected page parameter")
                        page = library.snapshot_page(parts[4], int(query.get("offset", [0])[0]),
                                                     int(query.get("limit", [100])[0]))
                        self._send(200, dict(page, items=[decorate_snapshot(item) for item in page["items"]]))
                    else:
                        if set(query) - {"kind"}:
                            raise ValueError("unexpected export parameter")
                        artifact = library.export_session(parts[4], query.get("kind", ["summary"])[0])
                        self.send_response(200)
                        self.send_header("Content-Type", artifact.content_type)
                        self.send_header("Content-Length", str(artifact.path.stat().st_size))
                        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(artifact.download_name))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        with artifact.path.open("rb") as stream:
                            while chunk := stream.read(1024 * 1024):
                                self.wfile.write(chunk)
                finally:
                    if artifact is not None:
                        artifact.path.unlink(missing_ok=True)
                return
            if path == "/api/v1/capture":
                self._send(200, worker.status() if worker else {"state": "idle", "dataSource": mode, "receivedFrames": 0, "error": None})
                return
            if not path.startswith("/api/"):
                # 仅发布工作台静态资源，不暴露会话文件、模型配置或任意路径。
                relative = "index.html" if path == "/" else path.lstrip("/")
                web_root = (ROOT / "apps" / "web").resolve()
                target = (web_root / relative).resolve()
                if (target.is_relative_to(web_root) and target.is_file()
                        and target.suffix in {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"}
                        and not target.name.startswith("test_")):
                    payload = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers(); self.wfile.write(payload)
                    return
                self._send(404, {"error": "not_found"})
                return
            if path == "/api/v1/ai/providers":
                self._send(200, {"items": [
                    {"id": name, "model": provider.model,
                     "external": getattr(provider, "external", False)}
                    for name, provider in providers.items()
                ]})
                return
            if path == "/api/v1/health":
                self._send(200, {"status": "ok", "service": "bms-api", "readOnly": True, "dataSource": mode})
                return
            if path == "/api/v1/snapshots/latest":
                latest = latest_snapshot()
                self._send(200, latest or {"snapshot": None})
                return
            if path == "/api/v1/snapshots":
                try:
                    query = parse_qs(request.query)
                    page = store.page_snapshots(int(query.get("offset", [0])[0]), int(query.get("limit", [100])[0]))
                    self._send(200, dict(page, items=[decorate_snapshot(item) for item in page["items"]]))
                except ValueError:
                    self._send(400, {"error": "invalid_page"})
                return
            if path == "/api/v1/diagnostics":
                latest = latest_snapshot() or {}
                self._send(200, {"items": latest.get("diagnostics", []), "snapshotId": latest.get("id")})
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
                records = []
                for record in build_scenario(name):
                    snapshot = decorate_snapshot(record["snapshot"])
                    records.append(dict(record, snapshot=snapshot, frames=snapshot["frames"]))
                self._send(200, {"name": name, "items": records})
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
                           "ruleVersion": "quality-review-0.2", "externalRequest": external,
                           "limitations": ["仅分析本次快照，未接入完整 H/A 文档检索或真实硬件验证"]})
            self._send(200, result)

        def log_message(self, format: str, *args: object) -> None:
            return

        def log_error(self, format: str, *args: object) -> None:
            if sys.stderr is not None:
                print(format % args, file=sys.stderr)

    return ThreadingHTTPServer(("127.0.0.1", port), ApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="BMS APP REVOL read-only acquisition and API")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=str(ROOT / "runtime" / "live-check" / "current"))
    parser.add_argument("--mode", choices=["simulation", "file", "can"], default="simulation")
    parser.add_argument("--input", help="canonical frame JSONL or snapshot JSONL")
    parser.add_argument("--dll", help="matching ControlCAN SDK DLL path")
    parser.add_argument("--device-type", type=int, default=4)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--bitrate", type=int, choices=[250000, 500000], default=500000)
    parser.add_argument("--firmware-version", default="unknown", help="user supplied version label; not a firmware handshake")
    args = parser.parse_args()
    store = SessionStore(args.data_dir)
    worker = None
    if args.mode != "simulation":
        if store.latest() is not None or store.frames_path.exists():
            parser.error("capture requires a new data directory; existing sessions remain immutable")
        from services.edge_agent.capture import CaptureWorker
        if args.mode == "file":
            if not args.input:
                parser.error("--mode file requires --input")
            input_path = Path(args.input).resolve()
            if input_path in {store.path.resolve(), store.frames_path.resolve()}:
                parser.error("input file cannot also be a capture output file")
            from services.edge_agent.adapters import FileFrameSource
            source = FileFrameSource(args.input)
        else:
            if not args.dll:
                parser.error("--mode can requires --dll")
            from services.edge_agent.controlcan import ControlCanSource
            source = ControlCanSource(args.dll, device_type=args.device_type, device_index=args.device_index,
                                      channel=args.channel, bitrate=args.bitrate, listen_only=True)
        worker = CaptureWorker(source, store, data_source=args.mode, firmware_version=args.firmware_version)
    server = create_server(args.data_dir, args.port, load_providers(), mode=args.mode, worker=worker)
    if worker:
        worker.start()
    print(f"REVOL ({args.mode}) http://127.0.0.1:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if worker:
            worker.stop()


if __name__ == "__main__":
    main()
