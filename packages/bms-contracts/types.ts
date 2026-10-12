export type QualityStatus = "valid" | "pec_error" | "timeout" | "partial" | "stale" | "range_error" | "disconnected" | "unknown"
export type BmsValue<T> = { value: T; timestamp: number; source: string; sequence?: number; valid: boolean; quality: QualityStatus; rawFrameIds?: number[] }
export type CanFrame = { id: number; bus: "can1" | "can2"; data: number[]; dlc: number; timestamp: number; direction: "rx" | "tx"; sequence: number; ide?: "standard" | "extended"; rtr?: boolean; ruleVersion?: string }
export type CellVoltage = BmsValue<number> & { cellIndex: number; unit: "mV"; physicalValid?: boolean }
export type ModelDetection = { model: "af" | "ef" | "unknown"; mode: "automatic" | "manual"; confidence: "high" | "medium" | "low"; reason: string; observed: string[]; ruleVersion: string; timestamp: number }
export type DiagnosticEvent = { id: string; timestamp: number; severity: "info" | "warning" | "error"; chain: string; code: string; message: string; evidence: string[]; ruleVersion: string }
export type BmsSnapshot = { id: string; timestamp: number; firmwareVersion: string; totalVoltage: BmsValue<number>; current: BmsValue<number>; cells: CellVoltage[]; frames: CanFrame[]; diagnostics: DiagnosticEvent[]; modelDetection?: ModelDetection; replayScenario?: string }
