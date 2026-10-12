from __future__ import annotations

import time
import uuid
from typing import Any

from packages.bms_protocol.model_detection import detect_model

QUALITY_VALUES = {"valid", "pec_error", "timeout", "partial", "stale", "range_error", "disconnected", "unknown"}


class Simulator:
    """生成可重复的 BMS 只读会话，不连接真实 CAN。"""

    def __init__(self, firmware_version: str = "BMS_SIM_0.1.0") -> None:
        self.firmware_version = firmware_version
        self.sequence = 0
        self.pec_errors: set[int] = set()

    def inject_pec_error(self, cell_index: int) -> None:
        if not 0 <= cell_index < 136:
            raise ValueError("cell_index must be in [0, 135]")
        self.pec_errors.add(cell_index)

    def next_snapshot(self) -> dict[str, Any]:
        self.sequence += 1
        now = time.time()
        frames = []
        cells = []
        temperatures = []
        diagnostics = []
        values = [3700 + (index % 17) * 3 for index in range(136)]
        for group in range(34):
            frame_id = 0x104 + group
            frame_data = []
            for slot in range(4):
                index = group * 4 + slot
                frame_data.extend([values[index] & 0xFF, values[index] >> 8])
            frames.append({"id": frame_id, "bus": "can1", "data": frame_data, "dlc": 8, "timestamp": now, "direction": "rx", "sequence": self.sequence, "ide": "standard", "rtr": False, "ruleVersion": "simulator-0.1"})
        for index, value in enumerate(values):
            frame_id = 0x104 + index // 4
            quality = "pec_error" if index in self.pec_errors else "valid"
            cell = {"value": value, "timestamp": now, "source": f"slave_{index // 17 + 1:02d}", "sequence": self.sequence, "valid": quality == "valid", "quality": quality, "rawFrameIds": [frame_id], "cellIndex": index, "unit": "mV", "physicalValid": True}
            cells.append(cell)
            if quality == "pec_error":
                diagnostics.append({"id": str(uuid.uuid4()), "timestamp": now, "severity": "warning", "chain": "H01/A01", "code": "CELL_PEC_ERROR", "message": f"第 {index + 1} 节电压 PEC 校验失败，保留原始显示值但标记为无效", "evidence": [f"cellIndex={index}", f"frameId=0x{frame_id:X}", "quality=pec_error"], "ruleVersion": "diagnostic-0.1"})
        temperatures = [{"value": 25, "timestamp": now, "source": "simulator", "sequence": self.sequence, "valid": True, "quality": "valid", "rawFrameIds": []} for _ in range(70)]
        snapshot = {"id": str(uuid.uuid4()), "timestamp": now, "firmwareVersion": self.firmware_version, "totalVoltage": {"value": sum(c["value"] for c in cells), "timestamp": now, "source": "simulator", "sequence": self.sequence, "valid": not diagnostics, "quality": "valid" if not diagnostics else "partial", "rawFrameIds": [f["id"] for f in frames]}, "current": {"value": 3.0, "timestamp": now, "source": "simulator", "sequence": self.sequence, "valid": True, "quality": "valid", "rawFrameIds": []}, "cells": cells, "temperatures": temperatures, "frames": frames, "diagnostics": diagnostics}
        snapshot["modelDetection"] = detect_model(snapshot)
        return snapshot
