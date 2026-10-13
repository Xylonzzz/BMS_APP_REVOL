"""按主机接收证据聚合 CAN1；不推断固件采样批次或 PEC。"""
from __future__ import annotations

from copy import deepcopy
import math
import time
import uuid
from typing import Any

from packages.bms_protocol.decoder import decode_can1_frame
from packages.bms_protocol.pack_status import PACK_SIGNALS


def _empty(source: str, **fields) -> dict[str, Any]:
    return dict(value=None, timestamp=0.0, source=source, valid=False, quality="unknown", rawFrameIds=[], **fields)


class SnapshotAggregator:
    def __init__(self, data_source="can", firmware_version="unknown", stale_after=2.0):
        if data_source not in {"can", "file"} or not math.isfinite(stale_after) or stale_after <= 0:
            raise ValueError("invalid source or freshness interval")
        self.data_source, self.firmware_version, self.stale_after = data_source, firmware_version, stale_after
        self.cells = [_empty(f"slave_{i // 18 + 1:02d}", cellIndex=i, moduleIndex=i // 18,
                             channelIndex=i % 18, unit="mV", physicalValid=i % 18 != 17) for i in range(144)]
        self.temperatures = [_empty("can1:temperature", unit="℃") for _ in range(48)]
        self.pack: dict[str, Any] = {}
        self.signals: dict[str, Any] = {}
        self.pending: list[dict[str, Any]] = []
        self.evidence: dict[str, Any] = {}
        self.last_sequence = -1

    def ingest(self, frame: dict[str, Any]) -> None:
        timestamp, sequence = frame.get("timestamp"), frame.get("sequence")
        if (type(timestamp) not in {int, float} or not math.isfinite(timestamp) or timestamp < 0
                or type(sequence) is not int or sequence < 0 or sequence <= self.last_sequence):
            raise ValueError("frame requires finite receive time and increasing host sequence")
        self.last_sequence = sequence
        source = deepcopy(frame)
        for key in ("qualityMetadataSource", "signalQualities", "decoded"):
            source.pop(key, None)
        source["acquisitionSource"] = self.data_source
        self.pending.append(source)
        for signal in decode_can1_frame(source):
            name, raw = signal["name"], signal["value"]
            if name == "unknownFrame" or raw is None:
                continue
            quality = "unknown" if signal["quality"] == "valid" else signal["quality"]
            value = dict(value=raw, timestamp=timestamp, sequence=sequence, valid=False, quality=quality,
                         source=f"can1:0x{source['id']:03X}", unit=signal["unit"], rawFrameIds=[source["id"]])
            if name.startswith("cellVoltage["):
                index = int(name[12:-1])
                value["source"] = self.cells[index]["source"]
                self.cells[index].update(value)
            elif name.startswith("cellTemperature["):
                index = int(name[16:-1])
                self.temperatures[index].update(value)
            elif name in PACK_SIGNALS:
                field, divisor, unit = PACK_SIGNALS[name]
                self.pack[field] = dict(value, value=raw / divisor, unit=unit)
            else:
                self.signals[name] = value
            # 每个字段保留自己的原帧，短帧不能替换未更新字段的证据。
            self.evidence[name] = source

    def snapshot(self, timestamp=None, connected=True) -> dict[str, Any]:
        timestamp = time.time() if timestamp is None else timestamp
        if type(timestamp) not in {int, float} or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("invalid snapshot timestamp")
        def age(item):
            result = deepcopy(item)
            if result["value"] is not None:
                if not connected:
                    result.update(valid=False, quality="disconnected")
                elif timestamp - result["timestamp"] >= self.stale_after:
                    result.update(valid=False, quality="timeout")
            return result

        pack = {key: age(value) for key, value in self.pack.items()}
        total = deepcopy(pack.get("totalVoltageAccumulated", _empty("can1:0x100", unit="V")))
        if total["value"] is not None:
            total["value"] = round(total["value"] * 1000)
        total["unit"] = "mV"
        frames, self.pending = deepcopy(self.pending), []
        evidence = {value["sequence"]: value for value in self.evidence.values()}
        return {
            "id": str(uuid.uuid4()), "timestamp": timestamp, "firmwareVersion": self.firmware_version,
            "dataSource": self.data_source, "sequenceScope": "host_rx",
            "connectionState": "connected" if connected else "disconnected",
            "totalVoltage": total, "current": deepcopy(pack.get("current", _empty("can1:0x100", unit="A"))),
            "packStatus": pack, "cells": [age(c) for c in self.cells],
            "temperatures": [age(t) for t in self.temperatures],
            "signals": {key: age(value) for key, value in self.signals.items()},
            "frames": frames, "evidenceFrames": deepcopy(list(evidence.values())), "diagnostics": [],
            "modelDetection": {"model": "unknown", "mode": "automatic", "confidence": "low",
                               "reason": "固件未提供型号握手或完整采样有效性证据", "observed": [],
                               "ruleVersion": "can-host-0.1", "timestamp": timestamp},
        }
