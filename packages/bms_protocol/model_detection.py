from __future__ import annotations

from typing import Any
from math import isfinite


BOUNDARY_VOLTAGE_INDICES = (17, 35, 53, 71, 89, 107, 125)
TEMPERATURE_INDICES = tuple(range(48, 70))
RULE_VERSION = "af-ef-detection-0.2"


def _result(model: str, mode: str, confidence: str, reason: str, observed: list[str], timestamp: float) -> dict[str, Any]:
    return {
        "model": model,
        "mode": mode,
        "confidence": confidence,
        "reason": reason,
        "observed": observed,
        "ruleVersion": RULE_VERSION,
        "timestamp": timestamp,
    }


def _previous_model(previous: dict[str, Any] | None) -> str:
    model = (previous or {}).get("model")
    return model if model in {"af", "ef"} else "unknown"


def detect_model(snapshot: dict[str, Any], override: str | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    timestamp = float(snapshot.get("timestamp", 0.0))
    if override in {"af", "ef"}:
        return _result(override, "manual", "high", "manual_override", ["override"], timestamp)
    if override is not None:
        return _result("unknown", "automatic", "low", "invalid_override", [], timestamp)

    if snapshot.get("connectionState") == "disconnected":
        return _result(_previous_model(previous), "automatic", "low", "disconnected", [], timestamp)
    total = snapshot.get("totalVoltage")
    if total is not None and (total.get("valid") is not True or total.get("quality") != "valid"):
        return _result(_previous_model(previous), "automatic", "low", "quality_not_valid", [], timestamp)

    cells = snapshot.get("cells") or []
    temperatures = snapshot.get("temperatures") or []
    required_cells = [cells[index] if index < len(cells) else None for index in BOUNDARY_VOLTAGE_INDICES]
    required_temperatures = [temperatures[index] if index < len(temperatures) else None for index in TEMPERATURE_INDICES]
    if any(item is None for item in required_cells + required_temperatures):
        model = _previous_model(previous)
        return _result(model, "automatic", "low", "incomplete_evidence", [], timestamp)

    required = required_cells + required_temperatures
    if any(not isinstance(item, dict) for item in required):
        return _result(_previous_model(previous), "automatic", "low", "incomplete_evidence", [], timestamp)
    if any(item.get("valid") is not True or item.get("quality") != "valid" for item in required):
        model = _previous_model(previous) if previous else "unknown"
        return _result(model, "automatic", "low", "quality_not_valid", [], timestamp)

    if any(type(item.get("value")) not in {int, float} or not isfinite(item["value"]) for item in required):
        return _result(_previous_model(previous), "automatic", "low", "invalid_value", [], timestamp)
    if any(type(item.get("sequence")) is not int or item["sequence"] < 0 for item in required):
        return _result(_previous_model(previous), "automatic", "low", "missing_sequence", [], timestamp)
    if len({item["sequence"] for item in required}) != 1:
        return _result(_previous_model(previous), "automatic", "low", "mixed_sequence", [], timestamp)
    if not isfinite(timestamp) or any(item.get("timestamp") != timestamp for item in required):
        return _result(_previous_model(previous), "automatic", "low", "stale_or_mixed_timestamp", [], timestamp)

    voltage_af = all(item.get("value", 0) < 10 for item in required_cells)
    voltage_ef = all(item.get("value", 0) >= 10 for item in required_cells)
    temperature_af = all(item.get("value", 0) == 0 for item in required_temperatures)
    temperature_ef = all(item.get("value", 0) != 0 for item in required_temperatures)
    observed = [
        f"voltageBoundaries={list(BOUNDARY_VOLTAGE_INDICES)}",
        f"temperatureRange=48..69",
        f"sequence={required[0]['sequence']}",
        f"timestamp={timestamp}",
    ]
    if voltage_af and temperature_af:
        return _result("af", "automatic", "high", "af_zero_boundary_features", observed, timestamp)
    if voltage_ef and temperature_ef:
        return _result("ef", "automatic", "high", "ef_nonzero_boundary_features", observed, timestamp)
    model = _previous_model(previous) if previous else "unknown"
    return _result(model, "automatic", "low", "mixed_evidence", observed, timestamp)
