from __future__ import annotations

from dataclasses import dataclass
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
    """本地规则型占位提供商，不联网、不调用外部模型。"""

    name = "local-rules"
    model = "mock-0.1"

    def analyze(self, snapshot: dict[str, Any]) -> AiAnalysis:
        diagnostics = snapshot.get("diagnostics", [])
        if not snapshot.get("cells"):
            return AiAnalysis(self.name, self.model, "缺少单体采集数据，无法判断采集质量。", "warning", ["cells为空"], "H01/A01", ["检查采集和接收链路是否提供快照"])
        invalid = [cell for cell in snapshot.get("cells", []) if not cell.get("valid", False)]
        if diagnostics or invalid:
            evidence = [f"quality={cell.get('quality')} cellIndex={cell.get('cellIndex')}" for cell in invalid[:8]]
            evidence.extend(event.get("code", "unknown") for event in diagnostics[:8])
            return AiAnalysis(self.name, self.model, "快照含无效单体或诊断事件，需要沿对应链路核对。", "warning", evidence, "H01/A01" if invalid else None, ["对照原始 CAN 帧与质量状态", "区分部分帧接收、旧值保留与采集异常；原因尚待验证"])
        return AiAnalysis(self.name, self.model, "当前模拟快照未发现质量异常。", "info", ["所有模拟电芯 valid=true"], None, ["继续观察新鲜度和序号"])


def analysis_to_dict(analysis: AiAnalysis) -> dict[str, Any]:
    return {"provider": analysis.provider, "model": analysis.model, "summary": analysis.summary, "severity": analysis.severity, "evidence": analysis.evidence, "suggestedChain": analysis.suggested_chain, "nextChecks": analysis.next_checks}
