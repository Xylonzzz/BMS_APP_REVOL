export type QualityStatus = "valid" | "pec_error" | "timeout" | "partial" | "stale" | "range_error" | "disconnected" | "unknown"
export type BmsValue<T> = { value: T; timestamp: number; source: string; sequence?: number; valid: boolean; quality: QualityStatus; rawFrameIds?: number[] }
export type DecodedSignal = { name: string; value: number | null; unit: string | null; byte_range: string; quality: QualityStatus; meaning: string }
// direction 相对 PC；rx 是固件上行。模拟质量是帧外元数据，不是 CAN 载荷。
export type CanFrame = { id: number; bus: "can1" | "can2"; data: number[]; dlc: number; timestamp: number; direction: "rx" | "tx"; sequence: number; acquisitionSource?: "can" | "file"; originalSequence?: number; hardwareTimestamp?: number; TimeFlag?: 0 | 1; ide?: "standard" | "extended"; rtr?: boolean; ruleVersion?: string; qualityMetadataSource?: "simulation"; signalQualities?: Record<string, QualityStatus>; decoded?: DecodedSignal[] }
// 模组与通道索引均从 0 开始；历史快照可缺失，客户端不得凭缺失字段认定模组拓扑。
export type CellVoltage = BmsValue<number | null> & { cellIndex: number; unit: "mV"; physicalValid?: boolean; moduleIndex?: number; channelIndex?: number }
export type ModelDetection = { model: "af" | "ef" | "unknown"; mode: "automatic" | "manual"; confidence: "high" | "medium" | "low"; reason: string; observed: string[]; ruleVersion: string; timestamp: number }
export type DiagnosticEvent = { id: string; timestamp: number; severity: "info" | "warning" | "error"; chain: string; code: string; message: string; evidence: string[]; ruleVersion: string }
// 本地模拟 totalVoltage 使用 mV，current 使用 A；不等同于固件状态帧原始单位。
export type PackStatus = { totalVoltageAdc?: BmsValue<number | null> & { unit: "V" }; totalVoltageAccumulated?: BmsValue<number | null> & { unit: "V" }; prechargeVoltage?: BmsValue<number | null> & { unit: "V" }; current?: BmsValue<number | null> & { unit: "A" }; soc?: BmsValue<number | null> & { unit: "%" } }
export type BmsSnapshot = { id: string; timestamp: number; firmwareVersion: string; totalVoltage: BmsValue<number | null>; current: BmsValue<number | null>; packStatus?: PackStatus; cells: CellVoltage[]; temperatures?: BmsValue<number | null>[]; frames: CanFrame[]; evidenceFrames?: CanFrame[]; signals?: Record<string, BmsValue<number | null>>; sequenceScope?: "host_rx" | "simulation"; diagnostics: DiagnosticEvent[]; modelDetection?: ModelDetection; replayScenario?: string; dataSource?: "simulation" | "can" | "file"; connectionState?: "connected" | "disconnected" }
export type ReplayScenario = "valid" | "pec_error" | "partial" | "timeout_stale" | "disconnected_reconnect" | "af" | "ef" | "mixed_model"
export type ReplayRecord = { scenario: ReplayScenario; sequence: number; timestamp: number; frames: CanFrame[]; snapshot: BmsSnapshot; diagnostics: DiagnosticEvent[] }
export type ReplayResponse = { name: ReplayScenario; items: ReplayRecord[] }
export type CaptureStatus = { state: "idle" | "running" | "completed" | "stopped" | "error"; dataSource: "simulation" | "can" | "file"; receivedFrames: number; error: string | null }
export type SignalCatalogEntry = { label: string; path: string; unit: string | null; divisor: number }
export type SessionSummary = { id: string; name: string; source: string; first: number | null; last: number | null }
export type SnapshotPage = { items: BmsSnapshot[]; nextOffset: number | null }
export type SessionExportKind = "summary" | "full" | "snapshot"
export type UserViewProfile = { version: 1; name: string; theme: "minimal" | "engineering" | "operations"; density: "compact" | "cozy"; animation: "full" | "off"; autoRefresh: boolean }
