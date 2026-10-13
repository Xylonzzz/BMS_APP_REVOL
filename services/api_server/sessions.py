"""只读会话目录、分页和流式导出；只收集明确允许的原始记录文件。"""
from __future__ import annotations

from contextlib import ExitStack
import csv
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, BinaryIO
from zipfile import ZIP_DEFLATED, ZipFile

from packages.bms_protocol.telemetry import ALARM_FIELDS, SIGNAL_CATALOG
from services.edge_agent.session_store import SessionStore, complete_jsonl_size


@dataclass(frozen=True)
class ExportArtifact:
    path: Path
    download_name: str
    content_type: str


def _numeric(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _csv_text(value):
    text = value if isinstance(value, str) else "unknown"
    # 文本来源不可信；负电流等数值不经过这个分支。
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _records(stream: BinaryIO, size: int):
    stream.seek(0)
    remaining = size
    while remaining:
        line = stream.readline(remaining)
        if not line:
            raise OSError("recording was truncated while exporting")
        remaining -= len(line)
        if line.strip():
            value = json.loads(line.decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("snapshot record must be an object")
            yield value


class SessionLibrary:
    def __init__(self, root: str | Path, current_dir: str | Path):
        self.root = Path(root).resolve()
        self.current_dir = Path(current_dir).resolve()

    @staticmethod
    def _id(name: str) -> str:
        return "s-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _safe_file(directory: Path, name: str, *, required=True) -> Path | None:
        path = directory / name
        if not path.exists():
            if required:
                raise FileNotFoundError("session has no snapshots")
            return None
        resolved = path.resolve()
        if resolved.parent != directory or not resolved.is_file():
            raise ValueError("session payload escapes its directory or is not a file")
        return path

    def _candidates(self):
        yield "current", self.current_dir
        if self.root.is_dir():
            for entry in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
                if entry.is_dir():
                    resolved = entry.resolve()
                    if resolved.parent == self.root and resolved != self.current_dir:
                        yield self._id(entry.name), resolved

    def resolve(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not re.fullmatch(r"current|s-[0-9a-f]{24}", session_id):
            raise ValueError("invalid session id")
        for candidate_id, directory in self._candidates():
            if candidate_id == session_id:
                self._safe_file(directory, "snapshots.jsonl")
                return directory
        raise FileNotFoundError("session not found")

    def list_sessions(self) -> list[dict[str, Any]]:
        items = []
        for session_id, directory in self._candidates():
            try:
                self._safe_file(directory, "snapshots.jsonl")
                store = SessionStore(directory)
                first = store.page_snapshots(limit=1)["items"]
                last = store.latest()
                items.append({"id": session_id, "name": directory.name,
                              "source": last.get("dataSource", "unknown") if last else "unknown",
                              "first": _numeric(first[0].get("timestamp")) if first else None,
                              "last": _numeric(last.get("timestamp")) if last else None})
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return items

    def snapshot_page(self, session_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return SessionStore(self.resolve(session_id)).page_snapshots(offset, limit)

    def export_session(self, session_id: str, kind: str = "summary") -> ExportArtifact:
        formats = {"summary": ("csv", "text/csv; charset=utf-8"),
                   "snapshot": ("json", "application/json; charset=utf-8"),
                   "full": ("zip", "application/zip")}
        if kind not in formats:
            raise ValueError("export kind must be summary, full or snapshot")
        directory = self.resolve(session_id)
        store = SessionStore(directory)
        extension, content_type = formats[kind]
        fd, raw_path = tempfile.mkstemp(prefix="revol-export-", suffix="." + extension)
        os.close(fd)
        path = Path(raw_path)
        try:
            with ExitStack() as stack:
                inputs = []
                # 只在确定打开文件与大小边界时持锁；压缩期间允许采集追加。
                with store.locked():
                    for name in (("snapshots.jsonl", "frames.jsonl") if kind == "full" else ("snapshots.jsonl",)):
                        source = self._safe_file(directory, name, required=name == "snapshots.jsonl")
                        if source is not None:
                            stream = stack.enter_context(source.open("rb"))
                            size, original_size = complete_jsonl_size(stream)
                            inputs.append((name, stream, size, original_size))
                if kind == "full":
                    self._write_zip(path, session_id, directory.name, inputs)
                elif kind == "summary":
                    self._write_csv(path, _records(inputs[0][1], inputs[0][2]))
                else:
                    latest = None
                    for latest in _records(inputs[0][1], inputs[0][2]):
                        pass
                    if latest is None:
                        raise FileNotFoundError("session has no snapshot")
                    with path.open("w", encoding="utf-8") as output:
                        json.dump(latest, output, ensure_ascii=False, indent=2, allow_nan=False)
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", directory.name)[:80] or "session"
            return ExportArtifact(path, f"REVOL-{name}-{kind}.{extension}", content_type)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_csv(path, records):
        pack_fields = [("totalVoltageAdc", "V"), ("totalVoltageAccumulated", "V"),
                       ("prechargeVoltage", "V"), ("current", "A"), ("soc", "percent")]
        signal_names = ("relayPositive", "relayNegative", "relayPrecharge", "relayDcdc",
                        "chargerVoltage", "chargerCurrent", "chargerStatus", "bmsState", "initFlag",
                        "alarmMask", *(f"alarm.{name}" for name, _ in ALARM_FIELDS))
        fields = [(name, unit, False) for name, unit in pack_fields] + [
            (name, SIGNAL_CATALOG[name]["unit"] or "raw", True) for name in signal_names]
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["id", "firmwareVersion", "timestamp", "source"] + [
                column for field, unit, _ in fields for column in (
                    f"{field}_{unit}", f"{field}_quality", f"{field}_timestamp", f"{field}_source")])
            for record in records:
                pack = record.get("packStatus") or {}
                signals = record.get("signals") or {}
                if not isinstance(pack, dict) or not isinstance(signals, dict):
                    raise ValueError("packStatus and signals must be objects")
                row = [_csv_text(record.get("id", "")), _csv_text(record.get("firmwareVersion", "unknown")),
                       _numeric(record.get("timestamp")), _csv_text(record.get("dataSource", "unknown"))]
                for field, _, raw_signal in fields:
                    measurement = signals.get(field) if raw_signal else pack.get(field)
                    # packStatus 已经是工程量，只有原始 signals 按协议目录换算。
                    divisor = SIGNAL_CATALOG[field]["divisor"] if raw_signal else 1
                    if not raw_signal and measurement is None and field == "totalVoltageAccumulated":
                        measurement, divisor = record.get("totalVoltage"), 1000
                    if not raw_signal and measurement is None and field == "current":
                        measurement = record.get("current")
                    measurement = measurement if isinstance(measurement, dict) else {}
                    number = _numeric(measurement.get("value"))
                    row += [number / divisor if number is not None and divisor != 1 else number,
                            _csv_text(measurement.get("quality", "unknown")),
                            _numeric(measurement.get("timestamp")), _csv_text(measurement.get("source", ""))]
                writer.writerow(row)

    @staticmethod
    def _write_zip(path, session_id, name, inputs):
        manifest = {"formatVersion": "1.0", "exporter": "REVOL", "exportedAt": time.time(),
                    "session": {"id": session_id, "name": name}, "files": [],
                    "notes": ["Files contain only bytes present at the export boundary; later appends are excluded.",
                              "Raw frames may precede their next snapshot. Incomplete trailing records are excluded.",
                              "Host timestamps and sequences do not prove firmware sampling validity."]}
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for name, source, size, original_size in inputs:
                digest = hashlib.sha256()
                source.seek(0)
                remaining = size
                with archive.open(name, "w", force_zip64=True) as destination:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("recording was truncated while exporting")
                        destination.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                manifest["files"].append({"name": name, "size": size, "sha256": digest.hexdigest(),
                                          "omittedTailBytes": original_size - size})
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
