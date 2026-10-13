from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Any
from weakref import WeakValueDictionary


_DIRECTORY_LOCKS: WeakValueDictionary = WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


def complete_jsonl_size(stream, size: int | None = None) -> tuple[int, int]:
    """在固定大小内确认完整末行，不让后续追加进入当前读取边界。"""
    size = os.fstat(stream.fileno()).st_size if size is None else size
    position, tail = size, b""
    while position:
        length = min(8192, position)
        position -= length
        stream.seek(position)
        chunk = stream.read(length)
        if len(chunk) != length:
            raise OSError("recording was truncated while reading")
        tail = chunk + tail
        newline = tail.rfind(b"\n")
        if newline >= 0:
            last_line = tail[newline + 1:]
            if not last_line.strip():
                return size, size
            try:
                json.loads(last_line)
                return size, size
            except (ValueError, UnicodeDecodeError):
                return position + newline + 1, size
    if not tail.strip():
        return size, size
    try:
        json.loads(tail)
        return size, size
    except (ValueError, UnicodeDecodeError):
        return 0, size


class SessionStore:
    """使用 JSONL 保存不可变快照，并提供最新记录查询。"""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "snapshots.jsonl"
        self.frames_path = self.directory / "frames.jsonl"
        # 同进程多个 API/采集实例必须共用锁，避免 Windows 追加覆盖和读到半行。
        key = str(self.directory.resolve()).casefold()
        with _LOCKS_GUARD:
            lock = _DIRECTORY_LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _DIRECTORY_LOCKS[key] = lock
            self._lock = lock

    def append(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = str(snapshot["id"])
        record = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(record)
        return snapshot_id

    def append_frames(self, frames: list[dict[str, Any]]) -> int:
        """将本批原始接收证据追加到独立文件，不改写历史行。"""
        records = [json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
                   for frame in frames]
        if records:
            with self._lock, self.frames_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.writelines(records)
        return len(records)

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            with self.path.open(encoding="utf-8") as stream:
                return [json.loads(line) for line in stream if line.strip()]

    @contextmanager
    def locked(self):
        """用于获取一致的文件边界；锁与采集追加、其他实例共用。"""
        with self._lock:
            yield

    def page_snapshots(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("offset must be nonnegative and limit must be in [1, 200]")
        with self._lock:
            if not self.path.exists():
                return {"items": [], "nextOffset": None}
            stream = self.path.open("rb")
            try:
                size = os.fstat(stream.fileno()).st_size
            except BaseException:
                stream.close()
                raise
        # 追加只改文件尾；固定长度后解锁，慢历史扫描不阻塞设备接收写盘。
        with stream:
            size, _ = complete_jsonl_size(stream, size)
            stream.seek(0)
            items = []
            index = 0
            remaining = size
            while remaining:
                line = stream.readline(remaining)
                if not line:
                    raise OSError("recording was truncated while reading")
                remaining -= len(line)
                if not line.strip():
                    continue
                if index >= offset:
                    if len(items) == limit:
                        return {"items": items, "nextOffset": offset + len(items)}
                    items.append(json.loads(line.decode("utf-8-sig")))
                index += 1
            return {"items": items, "nextOffset": None}

    def latest(self) -> dict[str, Any] | None:
        """从完整文件边界倒查最后一条记录，跳过崩溃半行但不改写证据。"""
        def snapshot(line: bytes) -> dict[str, Any]:
            value = json.loads(line.decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("snapshot record must be an object")
            return value

        with self._lock:
            if not self.path.exists():
                return None
            with self.path.open("rb") as stream:
                boundary = os.fstat(stream.fileno()).st_size
                position, _ = complete_jsonl_size(stream, boundary)
                tail = b""
                while position:
                    size = min(position, 8192)
                    position -= size
                    stream.seek(position)
                    chunk = stream.read(size)
                    if len(chunk) != size:
                        raise OSError("recording was truncated while reading")
                    tail = (chunk + tail).rstrip(b"\r\n \t")
                    newline = tail.rfind(b"\n")
                    if newline >= 0:
                        return snapshot(tail[newline + 1:])
                return snapshot(tail) if tail else None
