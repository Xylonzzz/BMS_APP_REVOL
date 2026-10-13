"""有界接收、先保存原帧再聚合，并将后台错误显式交给 API。"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import threading
import time
from typing import Any

from packages.bms_protocol.aggregator import SnapshotAggregator
from services.edge_agent.session_store import SessionStore


class CaptureWorker:
    """每个实例对应一次采集；文件按记录时间处理，不冒充实时会话。"""

    BATCH_SIZE = 100
    MAX_PENDING_FRAMES = 1000

    def __init__(self, source, store: SessionStore, data_source="can", firmware_version="unknown",
                 interval=0.5, stale_after=2.0):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("capture interval must be finite and positive")
        self.source, self.store = source, store
        self._validate_destination(require_empty=False)
        self.data_source, self.interval = data_source, interval
        self._aggregate = SnapshotAggregator(data_source, firmware_version, stale_after)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._error: str | None = None
        self._received = 0
        self._sequence = 0
        self._pending = 0
        self._last_timestamp = 0.0
        self._published = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self._state, "receivedFrames": self._received,
                    "error": self._error, "dataSource": self.data_source}

    def _validate_destination(self, *, require_empty: bool) -> None:
        """构造和启动均核对路径，输入不能通过硬链接指向输出。"""
        source_path = getattr(self.source, "path", None)
        source_path = Path(source_path) if isinstance(source_path, (str, Path)) else None
        for name in ("path", "frames_path"):
            output = getattr(self.store, name, None)
            if not isinstance(output, (str, Path)):
                continue
            output = Path(output)
            if source_path is not None and (
                    source_path.resolve() == output.resolve()
                    or (source_path.exists() and output.exists() and source_path.samefile(output))):
                raise ValueError("capture input must not alias a session output file")
            if require_empty and output.exists() and (not output.is_file() or output.stat().st_size > 0):
                raise ValueError("capture requires an empty session; existing recordings remain immutable")

    def start(self) -> None:
        with self._lock:
            if self._state != "idle":
                raise RuntimeError("capture worker has already been started")
            self._validate_destination(require_empty=True)
            self._state = "running"
            self._thread = threading.Thread(target=self._run, name="revol-capture", daemon=True)
            try:
                self._thread.start()
            except Exception as error:
                self._fail(error)
                raise

    def stop(self, timeout=5.0) -> None:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("stop timeout must be finite and nonnegative")
        self._stop.set()
        with self._lock:
            thread = self._thread
            if thread is None:
                self._state = "stopped"
                return
        thread.join(timeout)
        if thread.is_alive():
            error = TimeoutError(f"capture worker did not stop within {timeout:g}s")
            self._fail(error)
            raise error

    def _fail(self, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        with self._lock:
            self._error = f"{self._error}; {message}" if self._error else message
            self._state = "error"

    def _publish(self, *, connected=True) -> None:
        timestamp = self._last_timestamp if self.data_source == "file" else time.time()
        self.store.append(self._aggregate.snapshot(timestamp=timestamp, connected=connected))
        self._pending = 0
        self._published = True

    def _run(self) -> None:
        completed = False
        next_publish = time.monotonic() + self.interval
        try:
            self.source.open()
            while not self._stop.is_set():
                frames = self.source.receive(limit=self.BATCH_SIZE,
                                             timeout_ms=min(100, max(1, int(self.interval * 1000))))
                if len(frames) > self.BATCH_SIZE:
                    raise ValueError("source exceeded requested receive batch limit")
                if frames:
                    normalized = []
                    for raw in frames:
                        item = deepcopy(raw)
                        self._sequence += 1
                        if "sequence" in item:
                            item.setdefault("originalSequence", item["sequence"])
                        item["sequence"] = self._sequence
                        normalized.append(item)
                    with self._lock:
                        self._received += len(normalized)
                    # 解析失败时仍有原始证据；写盘失败则立即停止，不继续丢弃帧。
                    self.store.append_frames(normalized)
                    for item in normalized:
                        self._aggregate.ingest(item)
                        self._pending += 1
                        self._last_timestamp = max(self._last_timestamp, item["timestamp"])
                now = time.monotonic()
                if ((self.data_source == "file" and frames)
                        or (self.data_source == "can" and now >= next_publish)
                        or self._pending >= self.MAX_PENDING_FRAMES):
                    self._publish()
                    next_publish = now + self.interval
                if self.data_source == "file" and getattr(self.source, "exhausted", False):
                    completed = True
                    if not self._published:
                        self._publish()
                    break
                if not frames:
                    # 部分驱动空接收会立即返回；用可中断等待限制空转频率。
                    self._stop.wait(min(0.01, self.interval))
        except Exception as error:
            self._fail(error)
        finally:
            try:
                self.source.close()
            except Exception as error:
                self._fail(error)
            if not completed or self.status()["state"] == "error":
                try:
                    if self._pending:
                        self._publish()
                    self._publish(connected=False)
                except Exception as error:
                    self._fail(error)
            with self._lock:
                if self._state != "error":
                    self._state = "completed" if completed else "stopped"
