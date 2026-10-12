from __future__ import annotations

from copy import deepcopy
from typing import Any

from packages.bms_protocol.model_detection import BOUNDARY_VOLTAGE_INDICES, detect_model


SCENARIO_NAMES = (
    "valid",
    "pec_error",
    "partial",
    "timeout_stale",
    "disconnected_reconnect",
    "af",
    "ef",
    "mixed_model",
)


def _base_snapshot(sequence: int = 1, timestamp: float = 1000.0) -> dict[str, Any]:
    values = [3700 + (index % 17) * 3 for index in range(136)]
    frames = []
    for group in range(34):
        data: list[int] = []
        for slot in range(4):
            value = values[group * 4 + slot]
            data.extend([value & 0xFF, value >> 8])
        frames.append({"id": 0x104 + group, "bus": "can1", "data": data, "dlc": 8, "timestamp": timestamp, "direction": "rx", "sequence": sequence, "ide": "standard", "rtr": False, "ruleVersion": "replay-0.1"})
    cells = [{"value": value, "timestamp": timestamp, "source": f"slave_{index // 17 + 1:02d}", "sequence": sequence, "valid": True, "quality": "valid", "rawFrameIds": [0x104 + index // 4], "cellIndex": index, "unit": "mV", "physicalValid": True} for index, value in enumerate(values)]
    temperatures = [{"value": 25, "timestamp": timestamp, "source": "replay", "sequence": sequence, "valid": True, "quality": "valid", "rawFrameIds": []} for _ in range(70)]
    snapshot = {"id": f"replay-{sequence}", "timestamp": timestamp, "firmwareVersion": "BMS_REPLAY_0.1.0", "totalVoltage": {"value": sum(values), "timestamp": timestamp, "source": "replay", "sequence": sequence, "valid": True, "quality": "valid", "rawFrameIds": [frame["id"] for frame in frames]}, "current": {"value": 3.0, "timestamp": timestamp, "source": "replay", "sequence": sequence, "valid": True, "quality": "valid", "rawFrameIds": []}, "cells": cells, "temperatures": temperatures, "frames": frames, "diagnostics": []}
    snapshot["modelDetection"] = detect_model(snapshot)
    return snapshot


def _record(name: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot["replayScenario"] = name
    return {"scenario": name, "sequence": snapshot["current"]["sequence"], "timestamp": snapshot["timestamp"], "frames": snapshot["frames"], "snapshot": snapshot, "diagnostics": snapshot["diagnostics"]}


def _model_snapshot(name: str, boundary_value: int, temperature_value: int, mixed: bool = False) -> dict[str, Any]:
    snapshot = _base_snapshot()
    for index in BOUNDARY_VOLTAGE_INDICES:
        snapshot["cells"][index]["value"] = boundary_value
    if mixed:
        snapshot["cells"][BOUNDARY_VOLTAGE_INDICES[0]]["value"] = 0
    for item in snapshot["temperatures"]:
        item["value"] = temperature_value
    snapshot["modelDetection"] = detect_model(snapshot)
    return snapshot


def build_scenario(name: str) -> list[dict[str, Any]]:
    if name not in SCENARIO_NAMES:
        raise ValueError(f"unknown replay scenario: {name}")
    if name == "valid":
        return [_record(name, _base_snapshot())]
    if name == "pec_error":
        snapshot = _base_snapshot()
        cell = snapshot["cells"][17]
        cell.update({"valid": False, "quality": "pec_error"})
        snapshot["diagnostics"].append({"id": "replay-pec-17", "timestamp": snapshot["timestamp"], "severity": "warning", "chain": "H01/A01", "code": "CELL_PEC_ERROR", "message": "第 18 节电压 PEC 校验失败，保留原始显示值但标记为无效", "evidence": ["cellIndex=17", "quality=pec_error"], "ruleVersion": "replay-0.1"})
        snapshot["totalVoltage"].update({"valid": False, "quality": "partial"})
        return [_record(name, snapshot)]
    if name == "partial":
        snapshot = _base_snapshot()
        snapshot["cells"][10].update({"valid": False, "quality": "partial"})
        snapshot["diagnostics"].append({"id": "replay-partial-10", "timestamp": snapshot["timestamp"], "severity": "warning", "chain": "H01/A01", "code": "CELL_PARTIAL_UPDATE", "message": "第 11 节未在本批次更新", "evidence": ["cellIndex=10", "quality=partial"], "ruleVersion": "replay-0.1"})
        return [_record(name, snapshot)]
    if name == "timeout_stale":
        first = _base_snapshot(1, 1000.0)
        second = deepcopy(_base_snapshot(2, 1002.0))
        second["totalVoltage"].update({"valid": False, "quality": "timeout"})
        second["cells"][0].update({"valid": False, "quality": "stale", "timestamp": 1000.0, "sequence": 1})
        second["diagnostics"].append({"id": "replay-timeout-1", "timestamp": 1002.0, "severity": "error", "chain": "H01/A01", "code": "SNAPSHOT_TIMEOUT", "message": "本批次未在阈值内收到完整快照", "evidence": ["sequence=2", "quality=timeout"], "ruleVersion": "replay-0.1"})
        return [_record(name, first), _record(name, second)]
    if name == "disconnected_reconnect":
        offline = _base_snapshot(1, 1000.0)
        offline["totalVoltage"].update({"valid": False, "quality": "disconnected"})
        offline["diagnostics"].append({"id": "replay-disconnect-1", "timestamp": 1001.0, "severity": "error", "chain": "A01", "code": "CAN_DISCONNECTED", "message": "CAN 会话断开", "evidence": ["quality=disconnected"], "ruleVersion": "replay-0.1"})
        online = _base_snapshot(2, 1002.0)
        online["diagnostics"].append({"id": "replay-reconnect-2", "timestamp": 1002.0, "severity": "info", "chain": "A01", "code": "CAN_RECONNECTED", "message": "CAN 会话恢复", "evidence": ["sequence=2", "quality=valid"], "ruleVersion": "replay-0.1"})
        return [_record(name, offline), _record(name, online)]
    if name == "af":
        return [_record(name, _model_snapshot(name, 0, 0))]
    if name == "ef":
        return [_record(name, _model_snapshot(name, 3800, 25))]
    return [_record(name, _model_snapshot(name, 3800, 25, mixed=True))]
