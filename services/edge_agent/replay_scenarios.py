from __future__ import annotations

from copy import deepcopy
from typing import Any

from services.edge_agent.simulator import build_snapshot, pec_diagnostic, refresh_snapshot, RULE_VERSION

SCENARIO_NAMES = ("valid", "pec_error", "partial", "timeout_stale", "disconnected_reconnect", "af", "ef", "mixed_model")


def _base_snapshot(sequence: int = 1, timestamp: float = 1000.0, model: str = "af") -> dict[str, Any]:
    return build_snapshot(sequence, timestamp, snapshot_id=f"replay-{sequence}",
                          firmware_version="BMS_REPLAY_0.2.0", model=model)


def _record(name: str, snapshot: dict[str, Any], sequence: int) -> dict[str, Any]:
    snapshot["id"] = f"replay-{name}-{sequence}"
    snapshot["replayScenario"] = name
    return {"scenario": name, "sequence": sequence, "timestamp": snapshot["timestamp"],
            "frames": snapshot["frames"], "snapshot": snapshot, "diagnostics": snapshot["diagnostics"]}


def _event(snapshot: dict[str, Any], code: str, quality: str, message: str) -> dict[str, Any]:
    return {"id": f"{snapshot['id']}-{code}", "timestamp": snapshot["timestamp"],
            "severity": "info" if quality == "valid" else "warning", "chain": "H01/A01",
            "code": code, "message": message, "evidence": [f"quality={quality}", "dataSource=simulation"],
            "ruleVersion": RULE_VERSION}


def _unavailable(previous: dict[str, Any], timestamp: float, disconnected: bool = False) -> dict[str, Any]:
    snapshot = deepcopy(previous)
    snapshot["timestamp"] = timestamp
    quality = "disconnected" if disconnected else "stale"
    snapshot["connectionState"] = "disconnected" if disconnected else "connected"
    for item in snapshot["cells"] + snapshot["temperatures"] + [snapshot["current"], snapshot["totalVoltage"]] + list(snapshot.get("packStatus", {}).values()):
        item.update({"valid": False, "quality": quality})
    snapshot["totalVoltage"]["quality"] = "disconnected" if disconnected else "timeout"
    code = "CAN_DISCONNECTED" if disconnected else "SNAPSHOT_TIMEOUT"
    snapshot["diagnostics"] = [_event(snapshot, code, quality, "模拟断线，显示上次采样" if disconnected else "模拟超时，未收到本轮帧；显示旧值")]
    refresh_snapshot(snapshot, set(), previous["modelDetection"])
    return snapshot


def build_scenario(name: str) -> list[dict[str, Any]]:
    if name not in SCENARIO_NAMES:
        raise ValueError(f"unknown replay scenario: {name}")
    first = _base_snapshot(model="ef" if name in {"ef", "mixed_model"} else "af")
    if name in {"valid", "af", "ef"}:
        return [_record(name, first, 1)]
    if name == "mixed_model":
        first["cells"][17]["value"] = 0
        refresh_snapshot(first)
        return [_record(name, first, 1)]
    if name == "pec_error":
        first["cells"][18].update({"valid": False, "quality": "pec_error"})
        first["diagnostics"].append(pec_diagnostic(first, 18))
        refresh_snapshot(first)
        return [_record(name, first, 1)]
    if name == "partial":
        second = _base_snapshot(2, 1001.0)
        for index in range(8, 12):
            second["cells"][index] = deepcopy(first["cells"][index])
            second["cells"][index].update({"valid": False, "quality": "partial"})
        second["diagnostics"].append(_event(second, "CELL_PARTIAL_UPDATE", "partial", "模拟缺失 0x106，软件槽8至11保留上一批证据"))
        refresh_snapshot(second, set(range(36)) - {2}, first["modelDetection"])
        return [_record(name, first, 1), _record(name, second, 2)]
    if name == "timeout_stale":
        return [_record(name, first, 1), _record(name, _unavailable(first, 1002.0), 2)]
    offline = _unavailable(first, 1001.0, disconnected=True)
    online = _base_snapshot(3, 1002.0)
    online["diagnostics"].append(_event(online, "CAN_RECONNECTED", "valid", "模拟连接恢复，收到新批次"))
    return [_record(name, first, 1), _record(name, offline, 2), _record(name, online, 3)]
