"""从状态帧提取整包工程量；缺失帧不补值，也不借用单体累压代替 ADC。"""

from __future__ import annotations

from typing import Any

from packages.bms_protocol.decoder import decode_can1_frame

PACK_SIGNALS = {
    "totalVoltageAdc": ("totalVoltageAdc", 10, "V"),
    "totalVoltageAccumulated": ("totalVoltageAccumulated", 10, "V"),
    "prechargeVoltage": ("prechargeVoltage", 10, "V"),
    "totalCurrent": ("current", 1000, "A"),
    "soc": ("soc", 10, "%"),
}


def pack_status_from_frames(frames: list[dict[str, Any]], *, legacy_simulation: bool = False) -> dict[str, Any]:
    """按记录顺序聚合 0x100/0x101；重复状态字段采用最后收到的证据。"""
    result: dict[str, Any] = {}
    for frame in frames:
        if frame.get("id") not in {0x100, 0x101}:
            continue
        for signal in decode_can1_frame(frame):
            conversion = PACK_SIGNALS.get(signal["name"])
            if conversion is None:
                continue
            name, divisor, unit = conversion
            quality = signal["quality"]
            explicit_quality = (frame.get("qualityMetadataSource") == "simulation"
                                and isinstance(frame.get("signalQualities"), dict))
            if legacy_simulation and not explicit_quality and quality == "valid":
                quality = "unknown"
            value = None if signal["value"] is None else signal["value"] / divisor
            result[name] = {"value": value, "unit": unit, "timestamp": frame["timestamp"],
                            "source": f"can1:0x{frame['id']:03X}", "sequence": frame["sequence"],
                            "valid": value is not None and quality == "valid", "quality": quality,
                            "rawFrameIds": [frame["id"]]}
    return result
