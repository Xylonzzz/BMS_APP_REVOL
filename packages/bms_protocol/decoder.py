from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class DecodedSignal:
    name: str
    value: Any
    unit: str | None
    byte_range: str
    quality: str
    meaning: str


def _quality(data: list[int], dlc: int, required: int) -> str:
    return "valid" if dlc >= required and len(data) >= required else "partial"


def _u16(data: list[int], offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _i32le(data: list[int], offset: int) -> int:
    raw = int.from_bytes(bytes(data[offset:offset + 4]), "little", signed=False)
    return raw - (1 << 32) if raw & 0x80000000 else raw


def decode_can1_frame(frame: dict[str, Any], direction: str = "up") -> list[dict[str, Any]]:
    """解析已审阅的 CAN1 帧；只读，不发送命令。方向相对固件 CAN1：up/down。"""
    frame_id = int(frame["id"])
    data = [int(value) & 0xFF for value in frame.get("data", [])]
    dlc = int(frame.get("dlc", len(data)))
    quality = _quality(data, dlc, 8)
    signals: list[DecodedSignal] = []
    if direction == "up" and frame_id == 0x100:
        signals.extend([
            DecodedSignal("totalCurrent", _i32le(data, 0) if len(data) >= 4 else None, "mA", "0..3", quality, "总电流，放电为正、充电为负"),
            DecodedSignal("totalVoltageAccumulated", _u16(data, 4) if len(data) >= 6 else None, "0.1V", "4..5", quality, "单体电压累加总压"),
            DecodedSignal("totalVoltageAdc", _u16(data, 6) if len(data) >= 8 else None, "0.1V", "6..7", quality, "主控 ADC 总压"),
        ])
    elif direction == "up" and frame_id == 0x101:
        signals.extend([
            DecodedSignal("prechargeVoltage", _u16(data, 0) if len(data) >= 2 else None, "0.1V", "0..1", quality, "主控 ADC 预充/负载侧电压"),
            DecodedSignal("soc", _u16(data, 2) if len(data) >= 4 else None, "0.1%", "2..3", quality, "SOC"),
        ])
    elif direction == "up" and 0x104 <= frame_id <= 0x127:
        start = (frame_id - 0x104) * 4
        for slot in range(4):
            offset = slot * 2
            signals.append(DecodedSignal(f"cellVoltage[{start + slot}]", _u16(data, offset) if len(data) >= offset + 2 else None, "mV", f"{offset}..{offset + 1}", quality, "兼容144槽单体电压；物理有效槽为8×17"))
    elif direction == "up" and 0x128 <= frame_id <= 0x133:
        start = (frame_id - 0x128) * 4
        for channel in range(4):
            offset = channel * 2
            signals.append(DecodedSignal(f"cellTemperature[{start + channel}]", _u16(data, offset) if len(data) >= offset + 2 else None, "℃", f"{offset}..{offset + 1}", quality, "温度通道"))
    elif direction == "up" and frame_id == 0x139:
        names = [("relayPositive", "主正辅助/替代反馈"), ("relayNegative", "主负辅助反馈"), ("relayPrecharge", "预充状态"), ("relayDcdc", "DCDC状态")]
        for index, (name, meaning) in enumerate(names):
            signals.append(DecodedSignal(name, data[index] if len(data) > index else None, None, str(index), quality, meaning))
    elif direction == "up" and frame_id == 0x140:
        signals.extend([
            DecodedSignal("chargerVoltage", _u16(data, 0) if len(data) >= 2 else None, "0.1V", "0..1", quality, "充电机电压反馈"),
            DecodedSignal("chargerCurrent", _u16(data, 2) if len(data) >= 4 else None, "0.1A", "2..3", quality, "充电机电流反馈"),
            DecodedSignal("chargerStatus", data[4] if len(data) >= 5 else None, None, "4", quality, "充电机状态"),
        ])
    else:
        signals.append(DecodedSignal("unknownFrame", None, None, "-", "unknown", "当前协议目录未定义该方向/ID"))
    return [asdict(signal) for signal in signals]
