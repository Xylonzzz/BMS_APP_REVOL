"""Tauri 专用 JSON Lines 控制通道；EOF 与 shutdown 均清理本地采集。"""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import sys

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.desktop_launcher import LauncherSettings, SessionController, application_directory, load_settings, save_settings, run_self_test


class SidecarHost:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or application_directory()
        self.controller = SessionController(self.directory)
        self.shutdown_requested = False

    def status(self):
        return dict(self.controller.status(), active=self.controller.active)

    def handle(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or set(request) - {"id", "command", "settings"}:
                raise ValueError("invalid request fields")
            if type(request_id) is not int or request_id < 0:
                raise ValueError("invalid request id")
            command = request.get("command")
            if command == "status":
                result = self.status()
            elif command == "settings":
                result = asdict(load_settings(self.directory / "settings.json"))
            elif command == "start":
                payload = request.get("settings", {})
                if not isinstance(payload, dict) or set(payload) - {item.name for item in fields(LauncherSettings)}:
                    raise ValueError("invalid settings fields")
                settings = LauncherSettings(**payload)
                settings.validate()
                save_settings(self.directory / "settings.json", settings)
                self.controller.start(settings)
                result = self.status()
            elif command == "stop":
                self.controller.stop()
                result = self.status()
            elif command == "shutdown":
                self.controller.stop()
                self.shutdown_requested = True
                result = self.status()
            else:
                raise ValueError("unsupported command")
            return {"id": request_id, "ok": True, "result": result}
        except Exception as error:
            return {"id": request_id, "ok": False, "error": f"{type(error).__name__}: {error}"}


def serve(host: SidecarHost, source, output) -> None:
    try:
        while not host.shutdown_requested:
            line = source.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                response = {"id": None, "ok": False, "error": "request exceeds 64 KiB"}
            else:
                try:
                    response = host.handle(json.loads(line))
                except ValueError as error:
                    response = {"id": None, "ok": False, "error": str(error)}
            output.write(json.dumps(response, ensure_ascii=False) + "\n")
            output.flush()
    finally:
        host.controller.stop()


def main():
    parser = argparse.ArgumentParser(description="REVOL Tauri sidecar")
    parser.add_argument("--data-root")
    parser.add_argument("--self-test")
    args = parser.parse_args()
    if args.self_test:
        result = run_self_test(args.self_test)
        raise SystemExit(0 if result["success"] else 1)
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    serve(SidecarHost(Path(args.data_root) if args.data_root else None), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
