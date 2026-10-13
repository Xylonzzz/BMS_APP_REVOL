from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol


@dataclass(frozen=True)
class AiAnalysis:
    provider: str
    model: str
    summary: str
    severity: str
    evidence: list[str]
    suggested_chain: str | None
    next_checks: list[str]


class AiProvider(Protocol):
    name: str
    model: str

    def analyze(self, snapshot: dict[str, Any]) -> AiAnalysis:
        ...


class MockAiProvider:
    """本地只读证据规则；不联网、不推断固件未上传的采样有效性。"""

    name = "local-rules"
    model = "evidence-rules-0.2"

    def analyze(self, snapshot: dict[str, Any]) -> AiAnalysis:
        source = snapshot.get("dataSource", "unknown")
        source_label = {"simulation": "模拟会话", "can": "真实 CAN 会话", "file": "文件会话"}.get(source, "来源未知的会话")
        cells = [cell for cell in snapshot.get("cells", []) if isinstance(cell, dict)]
        expected = {index for index in range(144) if index % 18 != 17}
        physical: dict[int, dict[str, Any]] = {}
        for cell in cells:
            index = cell.get("cellIndex")
            if type(index) is int and index in expected and cell.get("physicalValid") is not False:
                physical[index] = cell

        def has_value(item: dict[str, Any]) -> bool:
            value = item.get("value")
            return type(value) in (int, float) and math.isfinite(value)

        def quality(item: dict[str, Any]) -> str:
            declared = item.get("quality", "unknown")
            if declared == "valid" and item.get("valid") is not True:
                return "unknown"
            return declared if isinstance(declared, str) else "unknown"

        def detail(name: str, item: dict[str, Any]) -> str:
            raw_ids = item.get("rawFrameIds", [])
            ids = ",".join(f"0x{value:X}" for value in raw_ids if type(value) is int)
            return (f"{name}={item.get('value')} quality={quality(item)} "
                    f"timestamp={item.get('timestamp')} sequence={item.get('sequence')} "
                    f"source={item.get('source', 'unknown')} rawFrameIds=[{ids}]")

        measurements = [(f"cellIndex={index}", cell) for index, cell in physical.items()]
        measurements.extend((f"temperature[{index}]", item) for index, item in enumerate(snapshot.get("temperatures", []))
                            if isinstance(item, dict))
        measurements.extend((f"packStatus.{name}", item) for name, item in snapshot.get("packStatus", {}).items()
                            if isinstance(item, dict))
        for name in ("totalVoltage", "current"):
            if isinstance(snapshot.get(name), dict):
                measurements.append((name, snapshot[name]))
        signals = {name: item for name, item in snapshot.get("signals", {}).items() if isinstance(item, dict)}
        measurements.extend(signals.items())

        bad = [(name, item) for name, item in measurements if not has_value(item) or quality(item) != "valid"]
        qualities = {quality(item) for _, item in measurements}
        disconnected = snapshot.get("connectionState") == "disconnected" or "disconnected" in qualities
        expired = bool(qualities & {"timeout", "stale"})
        incomplete = len(physical) != len(expected) or any(not has_value(item) for _, item in measurements)
        uncertain = "unknown" in qualities or source != "simulation"
        errors = qualities & {"pec_error", "partial", "range_error"}
        active_alarms = [(name, item) for name, item in signals.items()
                         if name.startswith("alarm.") and has_value(item) and item["value"] != 0]
        mask = signals.get("alarmMask")
        masked = mask is not None and has_value(mask) and mask["value"] != 0
        bms_state = signals.get("bmsState")
        error_state = bms_state is not None and has_value(bms_state) and bms_state["value"] == 7
        diagnostics = [event for event in snapshot.get("diagnostics", []) if isinstance(event, dict)]
        warning_events = [event for event in diagnostics if event.get("severity") != "info"]
        warning = disconnected or expired or incomplete or uncertain or bool(bad or active_alarms or masked or error_state or warning_events)
        severity = "error" if any(event.get("severity") == "error" for event in diagnostics) else "warning" if warning else "info"

        facts = []
        if disconnected:
            facts.append("采集已停止或连接断开，保留的旧值不表示仍在更新" if source == "file"
                         else "连接已断开，保留的旧值不表示仍在采集")
        if expired:
            facts.append("存在超时或过期旧值，不能代表当前状态")
        if incomplete:
            facts.append("缺少物理电芯槽或测点数值")
        if errors:
            names = {"pec_error": "PEC错误", "partial": "部分更新", "range_error": "范围异常"}
            facts.append("存在" + "、".join(names[item] for item in sorted(errors)))
        if active_alarms:
            facts.append("记录中存在非零告警，需结合其质量和时间核对")
        if error_state:
            facts.append("记录中BMS状态码为7（故障状态），当前状态需结合该值时间核对")
        if masked:
            facts.append("故障屏蔽掩码非零，不能据无告警判断保护状态")
        if uncertain:
            facts.append("采样有效性未知，不能据页面数值或接收成功判定采样正常")
        if warning_events:
            facts.append("存在需要核对的诊断事件")
        if bad and not facts:
            facts.append("测点质量状态不能确认有效")
        summary = source_label + "：" + ("；".join(facts) if facts else "未发现已记录的质量异常；该结论仅适用于模拟输入") + "。"

        evidence = [f"dataSource={source}",
                    f"snapshotId={snapshot.get('id')} timestamp={snapshot.get('timestamp')} firmwareVersion={snapshot.get('firmwareVersion', 'unknown')}",
                    f"connectionState={snapshot.get('connectionState', 'unknown')} sequenceScope={snapshot.get('sequenceScope', 'unknown')}",
                    f"physicalSlots={len(physical)}/136 softwareSlots={len(cells)} expectedSoftwareSlots=144 reservedSlots=8",
                    f"newFrames={len(snapshot.get('frames', []))}; 接收帧数量不证明固件采样有效或本轮完整"]
        cell_bad = [(name, item) for name, item in bad if name.startswith("cellIndex=")]
        other_bad = [(name, item) for name, item in bad if not name.startswith("cellIndex=")]
        evidence.extend(detail(name, item) for name, item in cell_bad[:5] + other_bad[:6])
        evidence.extend(detail(name, item) for name, item in active_alarms)
        if mask is not None:
            evidence.append(detail("alarmMask", mask))
        if error_state:
            evidence.append(detail("bmsState", bms_state))
        evidence.extend(str(event.get("code", "unknown")) for event in diagnostics[:8])
        if source != "simulation":
            evidence.append("当前协议没有固件采样批次/逐槽PEC有效性字段；valid标签不补足该证据")

        checks = []
        if disconnected:
            checks.append("对照采集状态、接收日志和最后一帧时间；文件结束或停止采集不等于物理CAN总线断线"
                          if source == "file" else "核对连接状态、适配器接收错误与最后一帧时间，区分服务停止和物理链路中断")
        if disconnected or expired:
            checks.append("对照旧值原始 timestamp、sequence 与帧ID；旧值不能证明当前故障或恢复，查看后续是否收到新帧")
        if incomplete or cell_bad:
            checks.append("核对CAN1上行0x104–0x127的接收覆盖；按8×18软件槽定位136物理电芯，每模组第18槽为空位")
        if errors:
            checks.append("对照原始帧、DLC及质量来源；PEC错误标签来自采集证据或模拟注入，不能仅由电压值推断")
        if uncertain:
            checks.append("核对固件版本与采样有效性证据；主机接收时间/序号只说明到达顺序，不能当固件采样时间/批次")
        if active_alarms or masked or error_state:
            checks.append("对照0x141–0x143原始告警与屏蔽掩码，并核对值的时间和质量；屏蔽位不是故障消除证据")
        if "relayPrecharge" in signals:
            evidence.append(detail("relayPrecharge", signals["relayPrecharge"]) + "；预充状态镜像")
            checks.append("预充反馈是固件状态镜像，不能据此确认实物触点闭合；结合总压、预充电压和现场测量记录判断")
        if not checks:
            checks.append("继续观察数据时间、序号与质量变化；模拟通过不代表真实硬件已验收")
        chain = ("H07/A01" if active_alarms or masked or error_state else "H01/A01" if incomplete or cell_bad
                 else "A01" if warning else None)
        return AiAnalysis(self.name, self.model, summary, severity, evidence, chain, checks)


def analysis_to_dict(analysis: AiAnalysis) -> dict[str, Any]:
    return {"provider": analysis.provider, "model": analysis.model, "summary": analysis.summary, "severity": analysis.severity, "evidence": analysis.evidence, "suggestedChain": analysis.suggested_chain, "nextChecks": analysis.next_checks}
