from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class FrameSource(Protocol):
    """只读输入边界；空接收不等于文件结束。"""

    def open(self) -> None: ...

    def receive(self, limit: int = 1000, timeout_ms: int = 100) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def validate_receive_options(limit: int, timeout_ms: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("limit must be an integer in [1, 10000]")
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 60000:
        raise ValueError("timeout_ms must be an integer in [0, 60000]")


def canonical_file_frame(value: Any) -> dict[str, Any]:
    """只保留原始证据字段；文件中的解析结果和模拟质量不能升级测点可信度。"""
    if not isinstance(value, dict):
        raise ValueError("frame must be an object")
    required = ("id", "bus", "data", "dlc", "timestamp", "direction", "sequence")
    for key in required:
        if key not in value:
            raise ValueError(f"missing frame field: {key}")
    if type(value["id"]) is not int or not 0 <= value["id"] <= 0x1FFFFFFF:
        raise ValueError("id must be an integer in [0, 0x1FFFFFFF]")
    if value["bus"] not in ("can1", "can2"):
        raise ValueError("bus must be can1 or can2")
    if value["direction"] not in ("rx", "tx"):
        raise ValueError("direction must be rx or tx")
    if type(value["dlc"]) is not int or not 0 <= value["dlc"] <= 8:
        raise ValueError("dlc must be an integer in [0, 8]")
    if not isinstance(value["data"], list) or len(value["data"]) > 8:
        raise ValueError("data must be an array of at most 8 bytes")
    if any(type(item) is not int or not 0 <= item <= 255 for item in value["data"]):
        raise ValueError("data contains an illegal byte")
    if len(value["data"]) != value["dlc"] and not (value.get("rtr") is True and not value["data"]):
        raise ValueError("data length must match dlc for a data frame")
    timestamp = value["timestamp"]
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("timestamp must be a finite nonnegative number")
    if type(value["sequence"]) is not int or value["sequence"] < 0:
        raise ValueError("sequence must be a nonnegative integer")
    result = {key: value[key] for key in required}
    result["data"] = list(value["data"])
    if "originalSequence" in value:
        if type(value["originalSequence"]) is not int or value["originalSequence"] < 0:
            raise ValueError("originalSequence must be a nonnegative integer")
        result["originalSequence"] = value["originalSequence"]
    if "ide" in value:
        if value["ide"] not in ("standard", "extended"):
            raise ValueError("ide must be standard or extended")
        if value["ide"] == "standard" and value["id"] > 0x7FF:
            raise ValueError("standard frame id exceeds 0x7FF")
        result["ide"] = value["ide"]
    if "rtr" in value:
        if type(value["rtr"]) is not bool:
            raise ValueError("rtr must be boolean")
        result["rtr"] = value["rtr"]
    for key, upper in (("hardwareTimestamp", 0xFFFFFFFF), ("TimeFlag", 1)):
        if key in value:
            if type(value[key]) is not int or not 0 <= value[key] <= upper:
                raise ValueError(f"{key} must be an integer in [0, {upper}]")
            result[key] = value[key]
    result["acquisitionSource"] = "file"
    return result


class FileFrameSource:
    """顺序读取帧或快照 JSONL，保留记录时间和序号，不改写输入文件。"""

    MAX_LINE_BYTES = 16 * 1024 * 1024
    MAX_FRAMES_PER_RECORD = 10000

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.exhausted = False
        self._stream: BinaryIO | None = None
        self._line_number = 0
        self._pending: deque[tuple[Any, int]] = deque()
        self._error: ValueError | None = None

    def open(self) -> None:
        if self._stream is not None:
            return
        try:
            self._stream = self.path.open("rb")
        except FileNotFoundError as error:
            raise FileNotFoundError(f"FileFrameSource: file not found: {self.path}") from error
        self._line_number = 0
        self._pending.clear()
        self._error = None
        self.exhausted = False

    def receive(self, limit: int = 1000, timeout_ms: int = 100) -> list[dict[str, Any]]:
        validate_receive_options(limit, timeout_ms)
        if self._stream is None:
            raise RuntimeError("FileFrameSource is not open")
        if self._error is not None:
            raise self._error
        result: list[dict[str, Any]] = []
        while len(result) < limit:
            try:
                if self._pending:
                    raw, line_number = self._pending.popleft()
                    try:
                        result.append(canonical_file_frame(raw))
                    except (ValueError, OverflowError) as error:
                        raise ValueError(f"{self.path}: line {line_number}: {error}") from error
                    continue
                if self.exhausted:
                    break
                self._line_number += 1
                line = self._stream.readline(self.MAX_LINE_BYTES + 1)
                if len(line) > self.MAX_LINE_BYTES:
                    raise ValueError(f"{self.path}: line {self._line_number}: exceeds {self.MAX_LINE_BYTES} byte limit")
                if not line:
                    self.exhausted = True
                    break
                if not line.strip():
                    continue
                try:
                    record = json.loads(line.decode("utf-8-sig"))
                except UnicodeError as error:
                    raise ValueError(f"{self.path}: line {self._line_number}: invalid UTF-8") from error
                except ValueError as error:
                    raise ValueError(f"{self.path}: line {self._line_number}: invalid JSON: {error}") from error
                if isinstance(record, dict) and "frames" in record:
                    frames = record["frames"]
                    if not isinstance(frames, list):
                        raise ValueError(f"{self.path}: line {self._line_number}: frames must be an array")
                    if len(frames) > self.MAX_FRAMES_PER_RECORD:
                        raise ValueError(f"{self.path}: line {self._line_number}: exceeds {self.MAX_FRAMES_PER_RECORD} frames per record")
                    self._pending.extend((item, self._line_number) for item in frames)
                else:
                    self._pending.append((record, self._line_number))
            except ValueError as error:
                # 先交付本批已读原始帧，下一次接收显式抛错，避免坏行吞掉前序证据。
                self._error = error
                if result:
                    break
                raise
        return result

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._pending.clear()
