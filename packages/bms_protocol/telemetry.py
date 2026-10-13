"""CAN1 遥测字段目录；依据活动 data-user.h 与发送入口，2026-10-13 核对。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    size: int
    raw_unit: str | None
    meaning: str


SIGNAL_CATALOG: dict[str, dict[str, Any]] = {}
_FIELDS: dict[int, list[Field]] = {}


def _catalog(name: str, label: str, path: str, unit: str | None = None,
             divisor: int = 1) -> None:
    SIGNAL_CATALOG[name] = {"label": label, "path": path, "unit": unit, "divisor": divisor}


def _field(frame_id: int, name: str, offset: int, size: int, label: str, path: str,
           raw_unit: str | None = None, unit: str | None = None, divisor: int = 1,
           meaning: str | None = None) -> None:
    _catalog(name, label, path, raw_unit if unit is None else unit, divisor)
    _FIELDS.setdefault(frame_id, []).append(Field(name, offset, size, raw_unit, meaning or label))


# 既有解码字段也进入目录；数值换算由消费者按 divisor 完成。
for _name, _label, _path, _unit, _divisor in (
    ("totalCurrent", "总电流", "A03.State.TotalCurrent", "A", 1000),
    ("totalVoltageAccumulated", "单体累压", "A03.State.TotalVoltage_cal", "V", 10),
    ("totalVoltageAdc", "电池总压", "A03.State.TotalVoltage", "V", 10),
    ("prechargeVoltage", "预充电压", "A03.State.PrechargeVoltage", "V", 10),
    ("soc", "荷电状态", "A03.State.SOC", "%", 10),
    ("relayPositive", "主正反馈", "A03.Relay.AIRP_State", None, 1),
    ("relayNegative", "主负反馈", "A03.Relay.AIRN_State", None, 1),
    ("relayPrecharge", "预充反馈", "A03.Relay.PrechargeAIR_State", None, 1),
    ("relayDcdc", "DCDC反馈", "A03.Relay.DCDC_State", None, 1),
    ("chargerVoltage", "充电机反馈电压", "A03.charge.ChargeVol_rx", "V", 10),
    ("chargerCurrent", "充电机反馈电流", "A03.charge.ChargeCur_rx", "A", 10),
    ("chargerStatus", "充电机状态字", "A03.charge.ChargeStatus_rx", "raw", 1),
):
    _catalog(_name, _label, _path, _unit, _divisor)

for _index in range(144):
    _catalog(f"cellVoltage[{_index}]", f"电压软件槽 {_index}", f"A03.Cell.Vol[{_index}]", "mV")
for _index in range(48):
    _catalog(f"cellTemperature[{_index}]", f"温度通道 {_index}", f"A03.Cell.Temp[{_index}]", "℃")

_field(0x101, "rtcTime", 4, 2, "RTC兼容字段", "A03.State.RTC_Time", "raw",
       meaning="RTC兼容字段；活动恢复链未实现，单位未确认，不能解释为实际下电时间")
_field(0x101, "bmsState", 6, 1, "BMS状态", "A03.State.BMS_State",
       meaning="BMS状态码，枚举见state-user.h")
_field(0x101, "initFlag", 7, 1, "阈值初始化标志", "A03.State.Init_flag",
       meaning="阈值初始化/修改标志；不是告警等级，也不是Flash完成应答")

for _offset, (_name, _label, _member, _unit) in enumerate((
    ("maxCellVoltage", "最高单体电压", "MaxVol", "mV"),
    ("minCellVoltage", "最低单体电压", "MinVol", "mV"),
    ("maxCellTemperature", "最高单体温度", "MaxTemp", "℃"),
    ("minCellTemperature", "最低单体温度", "MinTemp", "℃"),
)):
    _field(0x134, _name, _offset * 2, 2, _label, f"A03.Cell.{_member}", _unit)

for _offset, (_name, _label, _member) in enumerate((
    ("maxVoltageIndex", "最高电压软件槽序号", "MaxVol_No"),
    ("minVoltageIndex", "最低电压软件槽序号", "MinVol_No"),
    ("maxTemperatureIndex", "最高温度通道序号", "MaxTemp_No"),
    ("minTemperatureIndex", "最低温度通道序号", "MinTemp_No"),
)):
    _field(0x138, _name, _offset, 1, _label, f"A03.Cell.{_member}", "raw",
           meaning=f"{_label}；保留固件原始序号，不推断实物接线位置")

ALARM_FIELDS = (
    ("OV", "单体过压"), ("UV", "单体欠压"), ("OT", "单体高温"), ("UT", "单体低温"),
    ("Vol_Openwires", "电压采集断线"), ("Temp_Openwires", "温度采集断线"),
    ("ODV", "单体压差过大"), ("ODT", "单体温差过大"),
    ("StaticDischarge_OC", "放电过流"), ("StaticCharge_OC", "充电过流"),
    ("Chargemachine_Error", "充电机故障"), ("IMD_Error", "绝缘接地故障"),
    ("Hall_Openwires", "霍尔电流传感器离线"), ("Hall_Error", "霍尔电流传感器故障"),
    ("Daisy_Openwires", "菊花链断线"), ("Precharge_Fail", "预充失败"),
    ("Relay_Error", "继电器故障"), ("Vol_Error", "总压偏差异常"),
)
_field(0x141, "alarmMask", 0, 4, "故障屏蔽掩码", "A03.Alarm.error_mute", "raw",
       meaning="32位故障屏蔽掩码；置位表示屏蔽，不能据此认定无故障")
for _index, (_member, _label) in enumerate(ALARM_FIELDS):
    _offset = 4 + _index
    _field(0x141 + _offset // 8, f"alarm.{_member}", _offset % 8, 1,
           _label, f"A03.Alarm.{_member}", "raw",
           meaning=f"{_label}原始状态；0为无告警，分级/布尔语义以固件检测函数为准")

for _first_id, _prefix, _count, _path, _label in (
    (0x135, "dcc", 18, "A03.Cell.DCC", "均衡位图"),
    (0x144, "openwireVoltage", 19, "A03.Alarm.Openwire_Vol", "电压开路位图"),
    (0x147, "openwireTemperature", 6, "A03.Alarm.Openwire_Temp", "温度开路位图"),
):
    for _index in range(_count):
        if _prefix == "openwireVoltage" and _index == 18:
            _meaning = "8个模组C0开路位图；最低位为模组0"
        else:
            _meaning = f"{_label}字节{_index}，bit0..7对应软件索引{_index * 8}..{_index * 8 + 7}"
            if _prefix == "dcc":
                _meaning += "；配置位为1不证明实物均衡电流"
        _field(_first_id + _index // 8, f"{_prefix}[{_index}]", _index % 8, 1,
               f"{_label} [{_index}]", f"{_path}[{_index}]", "raw", meaning=_meaning)

# 阈值结构：36个u16、32个分级u8、4个尾部u8，共108有效字节。
# 无活动消费或明确单位证据的字段保留raw，不能用默认值大小猜单位。
_THRESHOLD_GROUPS = (
    ("OTV", "总压上限", 2, "raw", "raw", 1),
    ("UTV", "总压下限", 2, "raw", "raw", 1),
    ("StaticDischarge_OC", "放电过流", 2, "0.1A", "A", 10),
    ("StaticCharge_OC", "充电过流", 2, "0.1A", "A", 10),
    ("SOC_High", "SOC上限", 2, "0.1%", "%", 10),
    ("SOC_Low", "SOC下限", 2, "0.1%", "%", 10),
    ("Cell_OV", "单体过压", 2, "mV", "mV", 1),
    ("Cell_UV", "单体欠压", 2, "mV", "mV", 1),
    ("Cell_ODV", "单体压差", 2, "mV", "mV", 1),
    ("Cell_OT", "单体高温", 1, "℃", "℃", 1),
    ("Cell_UT", "单体低温", 1, "℃", "℃", 1),
    ("Cell_ODT", "单体温差", 1, "℃", "℃", 1),
    ("Daisy_Openwires", "菊花链断线", 1, "raw", "raw", 1),
    ("Cell_Vol_Openwires", "电压采集断线", 1, "raw", "raw", 1),
    ("Cell_Temp_Openwires", "温度采集断线", 1, "raw", "raw", 1),
    ("Hall_Openwires", "霍尔报文间隔计数", 1, "count", "count", 1),
    ("Vol_Error", "总压相对偏差", 1, "%", "%", 1),
)
_threshold_offset = 0
for _group, _label, _size, _raw_unit, _unit, _divisor in _THRESHOLD_GROUPS:
    for _level in range(1, 5):
        _member = f"{_group}_LV{_level}"
        _meaning = f"{_label} LV{_level}回读；参数回读不证明保护启用或Flash保存"
        if _raw_unit == "raw":
            _meaning += "；单位未确认"
        _field(0x150 + _threshold_offset // 8, f"threshold.{_member}", _threshold_offset % 8,
               _size, f"{_label} LV{_level}", f"A03.Threshold.{_member}", _raw_unit,
               _unit, _divisor, _meaning)
        _threshold_offset += _size
for _member, _label, _unit in (
    ("Balance_Start_DV", "均衡启动压差", "raw"),
    ("Balance_End_DV", "均衡结束压差", "raw"),
    ("Precharge_End_Vol_Percent", "预充完成电压比例", "%"),
    ("Precharge_Time", "预充时间参数", "s"),
):
    _meaning = f"{_label}回读；不证明当前控制流程使用该参数"
    if _unit == "raw":
        _meaning += "；单位未确认"
    _field(0x150 + _threshold_offset // 8, f"threshold.{_member}", _threshold_offset % 8,
           1, _label, f"A03.Threshold.{_member}", _unit, meaning=_meaning)
    _threshold_offset += 1

_field(0x15F, "chargeTargetVoltage", 0, 2, "充电目标电压", "A03.charge.ChargeVol_tx",
       "0.1V", "V", 10, "充电目标电压回读；不是充电机实际输出")
_field(0x15F, "chargeTargetCurrent", 2, 2, "充电目标电流", "A03.charge.ChargeCur_tx",
       "0.1A", "A", 10, "充电目标电流回读；不是充电机实际输出")


def decode_telemetry_fields(frame_id: int, data: list[int], quality: str) -> list[dict[str, Any]]:
    """供已完成方向/类型/长度检查的decoder调用；只解释声明范围，保留raw值。"""
    return [{"name": field.name,
             "value": int.from_bytes(bytes(data[field.offset:field.offset + field.size]), "little")
             if len(data) >= field.offset + field.size else None,
             "unit": field.raw_unit,
             "byte_range": str(field.offset) if field.size == 1 else f"{field.offset}..{field.offset + field.size - 1}",
             "quality": quality, "meaning": field.meaning}
            for field in _FIELDS.get(frame_id, ())]
