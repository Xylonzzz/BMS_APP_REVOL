from __future__ import annotations

import time
import uuid
from typing import Any

from packages.bms_protocol.model_detection import detect_model
from packages.bms_protocol.pack_status import pack_status_from_frames

QUALITY_VALUES = {"valid", "pec_error", "timeout", "partial", "stale", "range_error", "disconnected", "unknown"}
SLOT_COUNT = 144
RULE_VERSION = "simulation-0.2"


def build_snapshot(sequence: int, timestamp: float, *, snapshot_id: str,
                   firmware_version: str, model: str = "af", total_voltage_adc: float = 506.8,
                   precharge_voltage: float = 501.2, current: float = 3.0, soc: float = 82.4) -> dict[str, Any]:
    """生成144软件槽和136物理槽的模拟证据；扩展温度仅用于型号测试。"""
    def value(data: float, source: str, raw_ids: list[int]) -> dict[str, Any]:
        return {"value": data, "timestamp": timestamp, "source": source,
                "sequence": sequence, "valid": True, "quality": "valid", "rawFrameIds": raw_ids}

    cells = []
    for index in range(SLOT_COUNT):
        physical = index % 18 != 17
        voltage = 3700 + (index % 18) * 3 if physical else (3800 if model == "ef" else 0)
        cell = value(voltage, f"simulated_slave_{index // 18 + 1:02d}", [0x104 + index // 4])
        cell.update({"cellIndex": index, "unit": "mV", "physicalValid": physical,
                     "moduleIndex": index // 18, "channelIndex": index % 18})
        cells.append(cell)
    temperatures = [value(25 if index < 48 or model == "ef" else 0,
                          "synthetic-model-evidence", []) for index in range(72)]
    snapshot = {"id": snapshot_id, "timestamp": timestamp, "firmwareVersion": firmware_version,
                "dataSource": "simulation", "connectionState": "connected",
                "totalVoltage": value(0, "simulated_cell_sum", []),
                "current": value(current, "simulation", [0x100]), "cells": cells,
                "packStatus": {"totalVoltageAdc": value(total_voltage_adc, "simulation", [0x100]),
                               "prechargeVoltage": value(precharge_voltage, "simulation", [0x101]),
                               "soc": value(soc, "simulation", [0x101])},
                "temperatures": temperatures, "frames": [], "diagnostics": []}
    refresh_snapshot(snapshot)
    return snapshot


def refresh_snapshot(snapshot: dict[str, Any], received_groups: set[int] | None = None,
                     previous: dict[str, Any] | None = None) -> None:
    """由槽值生成本次接收帧；旧槽的时间和序号由调用者保留。"""
    groups = set(range(SLOT_COUNT // 4)) if received_groups is None else received_groups
    frames = []
    for group in sorted(groups):
        cells = snapshot["cells"][group * 4:group * 4 + 4]
        data = [byte for cell in cells for byte in (cell["value"] & 0xFF, cell["value"] >> 8)]
        frames.append({"id": 0x104 + group, "bus": "can1", "data": data, "dlc": 8,
                       "timestamp": cells[0]["timestamp"], "direction": "rx",
                       "sequence": cells[0]["sequence"], "ide": "standard", "rtr": False,
                       "ruleVersion": RULE_VERSION, "qualityMetadataSource": "simulation",
                       "signalQualities": {f"cellVoltage[{cell['cellIndex']}]": cell["quality"] for cell in cells}})
    snapshot["frames"] = frames
    total = snapshot["totalVoltage"]
    total["value"] = sum(cell["value"] for cell in snapshot["cells"])
    total["rawFrameIds"] = sorted({frame_id for cell in snapshot["cells"] for frame_id in cell["rawFrameIds"]})
    if total["quality"] in {"valid", "partial"}:
        total["valid"] = all(cell["valid"] and cell["quality"] == "valid" for cell in snapshot["cells"])
        total["quality"] = "valid" if total["valid"] else "partial"
    if groups:
        # ADC 总压、预充电压和 SOC 是独立模拟输入；不按累压比例构造预充状态。
        status = snapshot["packStatus"]
        current = snapshot["current"]
        def u16_tenths(value: float) -> list[int]:
            return list(round(value * 10).to_bytes(2, "little", signed=False))

        state_frames = [
            (0x100, list(round(current["value"] * 1000).to_bytes(4, "little", signed=True))
             + list(int(total["value"] / 100).to_bytes(2, "little", signed=False))
             + u16_tenths(status["totalVoltageAdc"]["value"]),
             {"totalCurrent": current["quality"], "totalVoltageAccumulated": total["quality"],
              "totalVoltageAdc": status["totalVoltageAdc"]["quality"]}),
            (0x101, u16_tenths(status["prechargeVoltage"]["value"]) + u16_tenths(status["soc"]["value"]) + [0, 0, 0, 0],
             {"prechargeVoltage": status["prechargeVoltage"]["quality"], "soc": status["soc"]["quality"]}),
        ]
        for frame_id, data, qualities in state_frames:
            frames.append({"id": frame_id, "bus": "can1", "data": data, "dlc": 8,
                           "timestamp": snapshot["timestamp"], "direction": "rx", "sequence": current["sequence"],
                           "ide": "standard", "rtr": False, "ruleVersion": RULE_VERSION,
                           "qualityMetadataSource": "simulation", "signalQualities": qualities})
        snapshot["packStatus"] = pack_status_from_frames(frames)
        snapshot["current"] = dict(snapshot["packStatus"]["current"])
    snapshot["modelDetection"] = detect_model(snapshot, previous=previous)


def pec_diagnostic(snapshot: dict[str, Any], index: int) -> dict[str, Any]:
    return {"id": f"{snapshot['id']}-pec-{index}", "timestamp": snapshot["timestamp"],
            "severity": "warning", "chain": "H01/A01", "code": "CELL_PEC_ERROR",
            "message": f"模拟软件槽 {index} PEC 失败，保留显示值；该状态不是 CAN 帧内字段",
            "evidence": [f"cellIndex={index}", f"frameId=0x{0x104 + index // 4:X}", "quality=pec_error", "qualityMetadataSource=simulation"],
            "ruleVersion": RULE_VERSION}


class Simulator:
    """生成只读模拟会话，不连接真实 CAN。"""

    def __init__(self, firmware_version: str = "BMS_SIM_0.2.0") -> None:
        self.firmware_version = firmware_version
        self.sequence = 0
        self.pec_errors: set[int] = set()

    def inject_pec_error(self, cell_index: int) -> None:
        if not 0 <= cell_index < SLOT_COUNT or cell_index % 18 == 17:
            raise ValueError("cell_index must identify a physical slot in [0, 143]")
        self.pec_errors.add(cell_index)

    def next_snapshot(self) -> dict[str, Any]:
        self.sequence += 1
        snapshot = build_snapshot(self.sequence, time.time(), snapshot_id=str(uuid.uuid4()),
                                  firmware_version=self.firmware_version)
        for index in sorted(self.pec_errors):
            snapshot["cells"][index].update({"valid": False, "quality": "pec_error"})
            snapshot["diagnostics"].append(pec_diagnostic(snapshot, index))
        refresh_snapshot(snapshot)
        return snapshot
