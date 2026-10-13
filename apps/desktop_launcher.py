"""REVOL 便携候选版启动器；所有硬件访问均由用户点击启动后触发。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
import argparse
import json
import os
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
import uuid
import webbrowser
from urllib.request import urlopen

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.api_server.server import create_server
from services.edge_agent.capture import CaptureWorker
from services.edge_agent.session_store import SessionStore


@dataclass(frozen=True)
class LauncherSettings:
    mode: str = "simulation"
    input_path: str = ""
    dll_path: str = ""
    device_type: int = 4
    device_index: int = 0
    channel: int = 0
    bitrate: int = 500000
    firmware_version: str = ""

    def validate(self) -> None:
        if self.mode not in {"simulation", "file", "can"}:
            raise ValueError("请选择模拟、文件或 CAN 输入")
        for name in ("input_path", "dll_path", "firmware_version"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} 必须是文本")
        if self.mode == "file" and not self.input_path.strip():
            raise ValueError("请先选择 JSONL 输入文件")
        if self.mode == "can" and not self.dll_path.strip():
            raise ValueError("请先选择与本程序位数一致的 ControlCAN DLL")
        for name in ("device_type", "device_index", "channel"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} 必须是非负整数")
        if type(self.bitrate) is not int or self.bitrate not in {250000, 500000}:
            raise ValueError("波特率仅支持 250000 或 500000")


def application_directory() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "REVOL"


def load_settings(path: str | Path) -> LauncherSettings:
    """只读取固定配置字段；每次启动仍回到模拟模式。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return LauncherSettings()
        allowed = {item.name for item in fields(LauncherSettings)} - {"mode"}
        settings = LauncherSettings(**{key: value for key, value in payload.items() if key in allowed})
        settings.validate()
        return settings
    except (OSError, ValueError, TypeError):
        return LauncherSettings()


def save_settings(path: str | Path, settings: LauncherSettings) -> None:
    settings.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    payload.pop("mode")
    payload["version"] = 1
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_source(settings: LauncherSettings):
    if settings.mode == "file":
        from services.edge_agent.adapters import FileFrameSource
        return FileFrameSource(settings.input_path)
    if settings.mode == "can":
        from services.edge_agent.controlcan import ControlCanSource
        return ControlCanSource(settings.dll_path, device_type=settings.device_type,
                                device_index=settings.device_index, channel=settings.channel,
                                bitrate=settings.bitrate, listen_only=True)
    raise ValueError("模拟模式不使用硬件输入")


class SessionController:
    """API 与采集线程由同一进程管理；UI 可在后台调用 start/stop。"""

    def __init__(self, directory: str | Path | None = None, *, source_factory=create_source,
                 worker_factory=CaptureWorker, server_factory=create_server):
        self.directory = Path(directory) if directory is not None else application_directory()
        self.source_factory, self.worker_factory, self.server_factory = source_factory, worker_factory, server_factory
        self.thread_name = f"revol-api-{uuid.uuid4().hex[:8]}"
        self._operation = threading.Lock()
        self._lock = threading.Lock()
        self._server = self._thread = self._worker = None
        self._state = "idle"
        self._error = None
        self._url = None
        self._session_directory = None
        self._mode = "simulation"
        self._last_capture = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._server is not None or self._worker is not None or self._state == "starting"

    def status(self) -> dict:
        with self._lock:
            worker = self._worker
            result = {"state": self._state, "error": self._error, "url": self._url,
                      "sessionDirectory": str(self._session_directory) if self._session_directory else None,
                      "mode": self._mode, "capture": self._last_capture}
        if worker is not None:
            result["capture"] = worker.status()
        if result["capture"] and result["capture"].get("error"):
            result["error"] = result["capture"]["error"]
        return result

    def _serve(self, server) -> None:
        try:
            server.serve_forever(poll_interval=0.1)
        except Exception as error:
            with self._lock:
                self._state = "error"
                self._error = f"API: {type(error).__name__}: {error}"

    def start(self, settings: LauncherSettings, *, port: int = 0) -> str:
        settings.validate()
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("端口必须在 0 到 65535 之间")
        with self._operation:
            if self.active:
                raise RuntimeError("请先停止当前会话")
            with self._lock:
                self._state, self._error, self._mode = "starting", None, settings.mode
                self._last_capture = None
                self._url = None
            source = worker = server = thread = None
            worker_started = thread_started = False
            try:
                session = self.directory / "sessions" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:12]}"
                session.mkdir(parents=True, exist_ok=False)
                with self._lock:
                    self._session_directory = session
                if settings.mode != "simulation":
                    source = self.source_factory(settings)
                    worker = self.worker_factory(source, SessionStore(session), data_source=settings.mode,
                                                 firmware_version=settings.firmware_version.strip() or "unknown")
                # 先绑定端口，成功后才启动采集，避免端口失败时触碰适配器。
                server = self.server_factory(session, port=port, mode=settings.mode, worker=worker)
                thread = threading.Thread(target=self._serve, args=(server,), name=self.thread_name, daemon=True)
                thread.start()
                thread_started = True
                if worker is not None:
                    worker.start()
                    worker_started = True
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                with self._lock:
                    self._server, self._thread, self._worker = server, thread, worker
                    self._url, self._state = url, "running"
                return url
            except Exception as error:
                cleanup_errors = []
                if thread_started:
                    server.shutdown()
                    thread.join(2)
                if server is not None:
                    server.server_close()
                if worker_started:
                    try:
                        worker.stop()
                    except Exception as cleanup_error:
                        cleanup_errors.append(str(cleanup_error))
                elif source is not None:
                    try:
                        source.close()
                    except Exception as cleanup_error:
                        cleanup_errors.append(str(cleanup_error))
                with self._lock:
                    self._state = "error"
                    self._error = f"{type(error).__name__}: {error}"
                    if cleanup_errors:
                        self._error += "; 清理失败: " + "; ".join(cleanup_errors)
                        self._worker = worker
                raise

    def stop(self) -> None:
        with self._operation:
            with self._lock:
                server, thread, worker = self._server, self._thread, self._worker
                if server is None and worker is None:
                    return
                self._state = "stopping"
            errors = []
            if server is not None:
                try:
                    server.shutdown()
                    server.server_close()
                    if thread is not None:
                        thread.join(2)
                        if thread.is_alive():
                            raise TimeoutError("API 线程未按时退出")
                except Exception as error:
                    errors.append(error)
                else:
                    with self._lock:
                        self._server = self._thread = None
                        self._url = None
            if worker is not None:
                try:
                    worker.stop(timeout=5)
                except Exception as error:
                    errors.append(error)
                else:
                    with self._lock:
                        self._last_capture = worker.status()
                        self._worker = None
            with self._lock:
                self._state = "error" if errors else "stopped"
                if errors:
                    self._error = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
            if errors:
                raise RuntimeError(self._error)


class LauncherWindow:
    MODE_LABELS = {"模拟数据": "simulation", "JSONL 文件": "file", "只读 CAN": "can"}
    STATE_LABELS = {"idle": "未启动", "starting": "正在启动", "running": "运行中",
                    "stopping": "正在停止", "stopped": "已停止", "completed": "文件已读完", "error": "发生错误"}

    def __init__(self, root, controller: SessionController | None = None, *, open_browser=True):
        import tkinter as tk
        from tkinter import ttk

        self.root, self.controller = root, controller or SessionController()
        self.open_browser_on_start = open_browser
        self.settings_path = self.controller.directory / "settings.json"
        settings = load_settings(self.settings_path)
        self.events = queue.Queue()
        self.busy = self.closing = False
        self.ui_message = ""
        self.variables = {name: tk.StringVar(value=str(value)) for name, value in asdict(settings).items()}
        self.variables["mode"].set("模拟数据")
        self.status_text = tk.StringVar(value="选择数据来源，点击启动后打开工作台。")
        self.detail_text = tk.StringVar(value="")
        self.url_text = tk.StringVar(value="")
        self.session_text = tk.StringVar(value="尚未创建会话")
        root.title("REVOL · 电池工作台")
        root.geometry("730x590")
        root.minsize(680, 540)
        root.configure(background="#f3f6fb")
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background="#f3f6fb")
        style.configure("TLabel", background="#f3f6fb", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 27, "bold"), foreground="#16324f")
        style.configure("Muted.TLabel", foreground="#687b90")
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        frame = ttk.Frame(root, padding=26)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="REVOL", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="电池工作台  /  Windows 便携候选版", style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 20))
        self.fields = []
        ttk.Label(frame, text="数据来源").grid(row=2, column=0, sticky="w", padx=(0, 20))
        self.mode_box = ttk.Combobox(frame, textvariable=self.variables["mode"], values=list(self.MODE_LABELS), state="readonly")
        self.mode_box.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _: self._update_fields())
        self.fields.append((self.mode_box, None, "readonly"))
        for row, name, label, group in ((3, "input_path", "记录文件", "file"), (4, "dll_path", "ControlCAN DLL", "can")):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(frame, textvariable=self.variables[name])
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            button = ttk.Button(frame, text="选择…", command=lambda key=name: self._choose_file(key))
            button.grid(row=row, column=2, padx=(8, 0))
            self.fields.extend(((entry, group, "normal"), (button, group, "normal")))
        can_options = ttk.Frame(frame)
        can_options.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        for column, (name, label, width) in enumerate((("device_type", "设备类型", 8), ("device_index", "设备索引", 8),
                                                     ("channel", "通道", 6), ("bitrate", "波特率", 12))):
            ttk.Label(can_options, text=label).grid(row=0, column=column * 2, padx=(0 if column == 0 else 14, 5))
            if name == "bitrate":
                widget = ttk.Combobox(can_options, textvariable=self.variables[name], values=(500000, 250000), width=width, state="readonly")
                normal = "readonly"
            else:
                widget = ttk.Entry(can_options, textvariable=self.variables[name], width=width)
                normal = "normal"
            widget.grid(row=0, column=column * 2 + 1)
            self.fields.append((widget, "can", normal))
        ttk.Label(frame, text="固件版本标签").grid(row=6, column=0, sticky="w")
        firmware = ttk.Entry(frame, textvariable=self.variables["firmware_version"])
        firmware.grid(row=6, column=1, columnspan=2, sticky="ew", pady=5)
        self.fields.append((firmware, "external", "normal"))
        ttk.Label(frame, text="版本可留空；手工标签不代表已与设备握手。CAN 使用只听模式。", style="Muted.TLabel").grid(row=7, column=0, columnspan=3, sticky="w", pady=(3, 16))
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew")
        self.start_button = ttk.Button(actions, text="启动工作台", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止会话", command=self._stop)
        self.stop_button.pack(side="left", padx=8)
        self.browser_button = ttk.Button(actions, text="打开浏览器", command=self._open_browser)
        self.browser_button.pack(side="left")
        ttk.Separator(frame).grid(row=9, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(frame, textvariable=self.status_text, style="Status.TLabel").grid(row=10, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, textvariable=self.url_text, style="Muted.TLabel").grid(row=11, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(frame, textvariable=self.detail_text, wraplength=650, foreground="#963d36").grid(row=12, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, textvariable=self.session_text, style="Muted.TLabel", wraplength=650).grid(row=13, column=0, columnspan=3, sticky="w", pady=(12, 0))
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._update_fields()
        root.after(200, self._heartbeat)

    def _choose_file(self, name):
        from tkinter import filedialog
        is_dll = name == "dll_path"
        selected = filedialog.askopenfilename(parent=self.root, title="选择 ControlCAN DLL" if is_dll else "选择 JSONL 记录",
                                             filetypes=[("ControlCAN DLL", "*.dll")] if is_dll else [("JSONL", "*.jsonl"), ("全部文件", "*.*")])
        if selected:
            self.variables[name].set(selected)

    def _update_fields(self):
        mode = self.MODE_LABELS[self.variables["mode"].get()]
        active = self.controller.active
        for widget, group, normal in self.fields:
            enabled = not (self.busy or active) and (group is None or group == mode or group == "external" and mode != "simulation")
            widget.configure(state=normal if enabled else "disabled")
        self.start_button.configure(state="disabled" if self.busy or active else "normal")
        self.stop_button.configure(state="normal" if active and not self.busy else "disabled")
        self.browser_button.configure(state="normal" if self.controller.status()["url"] and not self.busy else "disabled")

    def _background(self, action, kind):
        self.busy = True
        self._update_fields()
        def run():
            try:
                result = action()
            except Exception as error:
                self.events.put((kind, None, str(error)))
            else:
                self.events.put((kind, result, None))
        threading.Thread(target=run, name="revol-launcher-operation", daemon=True).start()

    def _start(self):
        try:
            payload = {key: variable.get() for key, variable in self.variables.items()}
            payload["mode"] = self.MODE_LABELS[payload["mode"]]
            for key in ("device_type", "device_index", "channel", "bitrate"):
                payload[key] = int(payload[key])
            settings = LauncherSettings(**payload)
            settings.validate()
            save_settings(self.settings_path, settings)
        except (ValueError, OSError) as error:
            self.ui_message = f"配置无效：{error}"
            return
        self.ui_message = ""
        self._background(lambda: self.controller.start(settings), "start")

    def _stop(self):
        self.ui_message = ""
        self._background(self.controller.stop, "stop")

    def _open_browser(self):
        url = self.controller.status()["url"]
        if url:
            try:
                if not webbrowser.open(url):
                    self.ui_message = f"无法自动打开浏览器，请手动访问 {url}"
            except Exception as error:
                self.ui_message = f"浏览器打开失败：{error}；请手动访问 {url}"

    def _close(self):
        self.closing = True
        if not self.busy:
            if self.controller.active:
                self._stop()
            else:
                self.root.destroy()

    def _heartbeat(self):
        while True:
            try:
                kind, _, error = self.events.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            if error:
                self.ui_message = error
                self.closing = False
            elif kind == "start" and self.open_browser_on_start and not self.closing:
                self._open_browser()
            if self.closing:
                if self.controller.active:
                    self._stop()
                else:
                    self.root.destroy()
                    return
        status = self.controller.status()
        capture = status["capture"]
        state = capture["state"] if capture and status["state"] == "running" else status["state"]
        title = self.STATE_LABELS.get(state, state)
        if capture:
            title += f" · 已接收 {capture['receivedFrames']} 帧"
        if self.busy and self.closing:
            title = "正在结束会话并关闭窗口…"
        self.status_text.set(title)
        self.url_text.set(status["url"] or "")
        self.detail_text.set(status["error"] or self.ui_message)
        self.session_text.set(f"会话保存：{status['sessionDirectory']}" if status["sessionDirectory"] else "记录保存在当前用户的 AppData\\Local\\REVOL\\sessions")
        self._update_fields()
        self.root.after(200, self._heartbeat)


def run_self_test(result_path: str | Path) -> dict:
    """验证冻结包的资源和服务生命周期；仅启动临时模拟会话。"""
    result_path = Path(result_path)
    if not result_path.is_absolute():
        raise ValueError("自检结果必须使用绝对路径")
    result = {"success": False, "errors": [], "checks": {}, "portClosed": False}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="revol-self-test-") as directory:
        controller = SessionController(directory)
        port = None
        try:
            url = controller.start(LauncherSettings(mode="simulation"))
            port = int(url.split(":")[-1].rstrip("/"))
            resources = {"health": "api/v1/health", "index": "", "schema": "api/v1/schema",
                         "batteryIcon": "assets/revol-battery.svg"}
            for name, path in resources.items():
                try:
                    with urlopen(url + path, timeout=3) as response:
                        payload = response.read()
                    if name == "health":
                        health = json.loads(payload)
                        if health.get("status") != "ok" or health.get("dataSource") != "simulation":
                            raise ValueError("unexpected health payload")
                    elif name == "index" and b"REVOL" not in payload:
                        raise ValueError("workbench page missing")
                    elif name == "schema" and not isinstance(json.loads(payload), dict):
                        raise ValueError("signal schema missing")
                    elif name == "batteryIcon" and b"<svg" not in payload:
                        raise ValueError("battery SVG missing")
                    result["checks"][name] = True
                except Exception as error:
                    result["checks"][name] = False
                    result["errors"].append(f"{name}: {type(error).__name__}: {error}")
        except Exception as error:
            result["errors"].append(f"startup: {type(error).__name__}: {error}")
        finally:
            try:
                controller.stop()
            except Exception as error:
                result["errors"].append(f"shutdown: {type(error).__name__}: {error}")
        if port is not None:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    result["errors"].append("API port remains open after shutdown")
            except OSError:
                result["portClosed"] = True
        result["success"] = not result["errors"] and result["portClosed"] and len(result["checks"]) == 4
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="REVOL Windows portable candidate")
    parser.add_argument("--self-test", metavar="ABSOLUTE_RESULT_JSON", help="run isolated simulation/resource checks without GUI, browser or CAN")
    args = parser.parse_args()
    if args.self_test:
        if not Path(args.self_test).is_absolute():
            parser.error("--self-test requires an absolute result path")
        result = run_self_test(args.self_test)
        raise SystemExit(0 if result["success"] else 1)
    import tkinter as tk
    root = tk.Tk()
    LauncherWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
